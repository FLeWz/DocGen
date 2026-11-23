"""Org B must not read/list/edit/delete anything of org A, for every endpoint that exists."""
import uuid

import pytest
from sqlalchemy import create_engine, text

from .conftest import APP_URL, add_member, new_client, signup, token_from


@pytest.fixture
def world(client, outbox):
    a = client
    signup(a, "alice@a.co", "Org A")
    add_member(a, outbox, "bob@a.co", "editor")
    a.post("/api/invites", json={"email": "pending@a.co", "role": "viewer"})
    a_tok = token_from(outbox)
    b = new_client()
    signup(b, "carol@b.co", "Org B")
    ids = {m["email"]: m["user_id"] for m in a.get("/api/members").json()}
    return dict(a=a, b=b, a_org=a.get("/api/orgs/current").json()["id"],
                a_invite=a.get("/api/invites").json()[0]["id"], a_users=ids, a_tok=a_tok)


def test_b_list_read(world):
    b = world["b"]
    assert {m["email"] for m in b.get("/api/members").json()} == {"carol@b.co"}
    assert b.get("/api/invites").json() == []
    assert [o["name"] for o in b.get("/api/orgs").json()] == ["Org B"]
    assert b.get("/api/orgs/current").json()["name"] == "Org B"


def test_b_cannot_switch_to_a(world):
    b = world["b"]
    r_a = b.post("/api/orgs/switch", json={"org_id": world["a_org"]})
    r_none = b.post("/api/orgs/switch", json={"org_id": str(uuid.uuid4())})
    assert r_a.status_code == r_none.status_code == 404 and r_a.json() == r_none.json()
    assert b.get("/api/orgs/current").json()["name"] == "Org B"


def test_b_cannot_edit_or_remove_a_members(world):
    b = world["b"]
    for uid in world["a_users"].values():
        assert b.patch(f"/api/members/{uid}", json={"role": "viewer"}).status_code == 404
        assert b.delete(f"/api/members/{uid}").status_code == 404
    assert len(world["a"].get("/api/members").json()) == 2


def test_b_cannot_touch_a_invites(world):
    b, i = world["b"], world["a_invite"]
    assert b.post(f"/api/invites/{i}/resend").status_code == 404
    assert b.delete(f"/api/invites/{i}").status_code == 404
    assert world["a"].get("/api/invites").json()[0]["status"] == "pending"


def test_b_delete_org_only_deletes_b(world):
    assert world["b"].delete("/api/orgs/current").status_code == 204
    assert world["a"].get("/api/orgs/current").json()["name"] == "Org A"
    assert len(world["a"].get("/api/members").json()) == 2


def test_a_cannot_touch_b_user(world):
    b_uid = world["b"].get("/api/auth/me").json()["user"]["id"]
    a = world["a"]
    assert a.patch(f"/api/members/{b_uid}", json={"role": "viewer"}).status_code == 404
    assert a.delete(f"/api/members/{b_uid}").status_code == 404


def test_b_cannot_accept_invite_meant_for_other_email(world):
    assert world["b"].post("/api/invites/accept", json={"token": world["a_tok"]}).status_code == 403


# --- second wall: Postgres RLS, hit directly as the runtime role ------------------------------

def _app_conn():
    return create_engine(APP_URL).connect()


def test_runtime_role_is_not_privileged():
    with _app_conn() as c:
        assert c.scalar(text("select rolsuper or rolbypassrls from pg_roles where rolname=current_user")) is False


def test_rls_filters_even_without_app_filter(world):
    with _app_conn() as c:
        assert c.scalar(text("select count(*) from memberships")) == 0  # no context -> nothing
        assert c.scalar(text("select count(*) from invites")) == 0
        c.execute(text("select set_config('app.org_id', :o, false)"), {"o": world["a_org"]})
        assert c.scalar(text("select count(*) from memberships")) == 2
        assert c.scalar(text("select count(*) from invites where org_id <> :o"), {"o": world["a_org"]}) == 0
        with pytest.raises(Exception):  # cannot write into another org
            c.execute(text("insert into invites (id, org_id, email, role, token_hash, expires_at) "
                           "values (gen_random_uuid(), :o, 'e@x.co', 'viewer', 'h', now())"),
                      {"o": str(uuid.uuid4())})


def test_rls_blocks_other_org_writes(world):
    b_org = world["b"].get("/api/orgs/current").json()["id"]
    with _app_conn() as c:
        c.execute(text("select set_config('app.org_id', :o, false)"), {"o": b_org})
        assert c.execute(text("update memberships set role='viewer'")).rowcount == 1  # only B's own row
        assert c.execute(text("update invites set email='z@z.co'")).rowcount == 0
        assert c.execute(text("delete from organizations where id <> :o"), {"o": b_org}).rowcount == 0
        c.rollback()
    assert {m["role"] for m in world["a"].get("/api/members").json()} == {"owner", "editor"}


# --- guards so later milestones can't forget ---------------------------------------------------

def test_every_org_id_table_has_rls_and_not_null(owner_engine):
    with owner_engine.connect() as c:
        rows = c.execute(text(
            "select c.relname, c.relrowsecurity, col.is_nullable from pg_class c "
            "join information_schema.columns col on col.table_name=c.relname and col.column_name='org_id' "
            "where c.relkind='r' and col.table_schema='public'")).all()
    assert {r[0] for r in rows} >= {"memberships", "invites", "folders", "snippets", "drafts", "styles", "images"}
    assert all(r[1] and r[2] == "NO" for r in rows), rows


COVERED = {  # every API route must be listed: a new endpoint forces a new isolation test
    "POST /api/auth/signup", "POST /api/auth/login", "POST /api/auth/logout", "GET /api/auth/me",
    "GET /api/auth/csrf",  # user/session scoped, no tenant data
    "GET /api/orgs", "POST /api/orgs", "GET /api/orgs/current", "POST /api/orgs/switch", "DELETE /api/orgs/current",
    "GET /api/members", "PATCH /api/members/{user_id}", "DELETE /api/members/{user_id}",
    "GET /api/invites", "POST /api/invites", "POST /api/invites/{id}/resend", "DELETE /api/invites/{id}",
    "POST /api/invites/lookup", "POST /api/invites/accept",  # token-scoped, see test_invites
    "GET /api/folders", "POST /api/folders", "PATCH /api/folders/{folder_id}", "DELETE /api/folders/{folder_id}",
    "GET /api/folders/{folder_id}/snippets", "POST /api/folders/{folder_id}/snippets",
    "GET /api/folders/{folder_id}/changes", "POST /api/folders/{folder_id}/commit",
    "GET /api/folders/{folder_id}/history", "GET /api/folders/{folder_id}/tags",
    "POST /api/folders/{folder_id}/tags", "DELETE /api/folders/{folder_id}/tags/{name}",
    "GET /api/snippets/{snippet_id}", "GET /api/snippets/{snippet_id}/draft", "PUT /api/snippets/{snippet_id}/draft",
    "DELETE /api/snippets/{snippet_id}/draft", "POST /api/snippets/{snippet_id}/resolve",
    "POST /api/snippets/{snippet_id}/restore", "GET /api/snippets/{snippet_id}/history",
    "GET /api/snippets/{snippet_id}/diff",  # all covered by test_b_cannot_touch_a_git_objects
    "GET /api/styles", "POST /api/styles", "PATCH /api/styles/{style_pk}", "DELETE /api/styles/{style_pk}",
    "POST /api/images", "GET /api/images/{image_id}",  # covered by test_b_cannot_touch_a_styles_and_images
}


def test_all_routes_covered_and_no_client_org_id(client):
    spec = client.get("/api/openapi.json").json()
    found = {f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m in ops}
    assert found == COVERED
    for p, ops in spec["paths"].items():
        for m, op in ops.items():
            if f"{m.upper()} {p}" == "POST /api/orgs/switch":
                continue
            assert "org_id" not in [x["name"] for x in op.get("parameters", [])]
            schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            ref = schema.get("$ref", "").split("/")[-1]
            assert "org_id" not in spec["components"]["schemas"].get(ref, {}).get("properties", {})


# --- folders / git ----------------------------------------------------------------------------

def test_b_cannot_touch_a_git_objects(world):
    from app.git import repo as git
    a, b = world["a"], world["b"]
    fo = a.post("/api/folders", json={"name": "Secret"}).json()["id"]
    sid = a.post(f"/api/folders/{fo}/snippets", json={"title": "S"}).json()["id"]
    doc = {"content": {"type": "doc", "content": [{"type": "paragraph"}]}}
    a.put(f"/api/snippets/{sid}/draft", json=doc)
    sha = a.post(f"/api/folders/{fo}/commit", json={"message": "m", "snippet_ids": [sid]}).json()["sha"]
    a.post(f"/api/folders/{fo}/tags", json={"name": "v1"})
    head_before = git.head(git.repo_path(world["a_org"], fo))
    bfo = b.post("/api/folders", json={"name": "Mine"}).json()["id"]
    bsid = b.post(f"/api/folders/{bfo}/snippets", json={"title": "B"}).json()["id"]
    b.put(f"/api/snippets/{bsid}/draft", json=doc)

    calls = [
        ("patch", f"/api/folders/{fo}", {"name": "x"}), ("delete", f"/api/folders/{fo}", None),
        ("get", f"/api/folders/{fo}/snippets", None), ("post", f"/api/folders/{fo}/snippets", {"title": "x"}),
        ("get", f"/api/folders/{fo}/changes", None), ("get", f"/api/folders/{fo}/history", None),
        ("post", f"/api/folders/{fo}/commit", {"message": "x", "snippet_ids": [sid]}),
        ("post", f"/api/folders/{bfo}/commit", {"message": "x", "snippet_ids": [sid]}),  # A snippet into B folder
        ("get", f"/api/folders/{fo}/tags", None), ("post", f"/api/folders/{fo}/tags", {"name": "v9", "sha": sha}),
        ("delete", f"/api/folders/{fo}/tags/v1", None),
        ("get", f"/api/snippets/{sid}", None), ("get", f"/api/snippets/{sid}?version={sha}", None),
        ("get", f"/api/snippets/{sid}?version=v1", None),
        ("get", f"/api/snippets/{sid}/draft", None), ("put", f"/api/snippets/{sid}/draft", doc),
        ("delete", f"/api/snippets/{sid}/draft", None), ("post", f"/api/snippets/{sid}/resolve", doc),
        ("post", f"/api/snippets/{sid}/restore", {"version": sha}),
        ("get", f"/api/snippets/{sid}/history", None),
        ("get", f"/api/snippets/{sid}/diff?from={sha}&to={sha}", None),
    ]
    for method, url, body in calls:
        r = getattr(b, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    # B's own snippet can't be asked for A's commit sha, nor A's folder moved under B (and vice versa)
    assert b.get(f"/api/snippets/{bsid}?version={sha}").status_code == 404
    assert b.patch(f"/api/folders/{bfo}", json={"parent_id": fo}).status_code == 404
    assert a.post("/api/folders", json={"name": "x", "parent_id": bfo}).status_code == 404
    assert b.get("/api/folders").json() == [{"id": bfo, "parent_id": None, "name": "Mine"}]
    # nothing of A changed; B's repo is separate and contains none of A's objects
    assert git.head(git.repo_path(world["a_org"], fo)) == head_before
    assert a.get(f"/api/folders/{fo}/tags").json() == [{"name": "v1", "sha": sha}]
    assert a.get(f"/api/snippets/{sid}/draft").json()["exists"] is False
    b_org = b.get("/api/orgs/current").json()["id"]
    assert git.repo_path(b_org, bfo) != git.repo_path(world["a_org"], fo)
    assert git.head(git.repo_path(b_org, bfo)) is None


def test_rls_blocks_cross_org_git_index_rows(world):
    a = world["a"]
    fo = a.post("/api/folders", json={"name": "F"}).json()["id"]
    a.post(f"/api/folders/{fo}/snippets", json={"title": "S"})
    b_org = world["b"].get("/api/orgs/current").json()["id"]
    with _app_conn() as c:
        assert c.scalar(text("select count(*) from folders")) == 0  # no context -> nothing
        c.execute(text("select set_config('app.org_id', :o, false)"), {"o": b_org})
        for t in ("folders", "snippets", "drafts"):
            assert c.scalar(text(f"select count(*) from {t}")) == 0
        with pytest.raises(Exception):
            c.execute(text("insert into folders (id, org_id, name) values (gen_random_uuid(), :o, 'x')"),
                      {"o": world["a_org"]})


def test_b_cannot_touch_a_styles_and_images(world):
    from .test_editor import PNG
    a, b = world["a"], world["b"]
    a_style = next(s for s in a.get("/api/styles").json() if s["style_id"] == "Heading1")["id"]
    img = a.post("/api/images", files={"file": ("x.png", PNG, "image/png")}).json()["id"]
    assert b.get(f"/api/images/{img}").status_code == 404
    assert b.patch(f"/api/styles/{a_style}", json={"name": "pwned"}).status_code == 404
    assert b.delete(f"/api/styles/{a_style}").status_code == 404
    assert {s["id"] for s in a.get("/api/styles").json()} .isdisjoint({s["id"] for s in b.get("/api/styles").json()})
    assert len(b.get("/api/styles").json()) == len(a.get("/api/styles").json())  # each org has its own seeded set
    assert next(s for s in a.get("/api/styles").json() if s["id"] == a_style)["name"] == "Heading 1"
    # B cannot embed A's image into its own commit (404-like: unknown image)
    fo = b.post("/api/folders", json={"name": "F"}).json()["id"]
    sid = b.post(f"/api/folders/{fo}/snippets", json={"title": "S"}).json()["id"]
    b.put(f"/api/snippets/{sid}/draft", json={"content": {"type": "doc", "content": [
        {"type": "image", "attrs": {"imageId": img}}]}})
    r = b.post(f"/api/folders/{fo}/commit", json={"message": "m", "snippet_ids": [sid]})
    assert r.status_code == 422
