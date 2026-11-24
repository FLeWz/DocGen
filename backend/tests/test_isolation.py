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
    assert {r[0] for r in rows} >= {"memberships", "invites", "folders", "snippets", "drafts", "styles", "images",
                              "base_documents", "base_doc_versions", "documents", "document_drafts", "document_refs", "exports", "imports"}
    assert all(r[1] and r[2] == "NO" for r in rows), rows


COVERED = {  # every API route must be listed: a new endpoint forces a new isolation test
    "POST /api/auth/signup", "POST /api/auth/login", "POST /api/auth/logout", "GET /api/auth/me",
    "GET /api/auth/csrf", "GET /api/auth/me/config",  # user/session scoped, no tenant data
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
    "GET /api/styles", "GET /api/styles/css", "POST /api/render/pdf",  # look-ups are org-scoped, see test_fidelity
    "POST /api/styles", "PATCH /api/styles/{style_pk}", "DELETE /api/styles/{style_pk}",
    "POST /api/images", "GET /api/images/{image_id}",  # covered by test_b_cannot_touch_a_styles_and_images
    # milestone 4: all covered by test_b_cannot_touch_a_documents / test_b_cannot_touch_a_base_documents
    "GET /api/base-documents", "POST /api/base-documents", "GET /api/base-documents/{id}",
    "POST /api/base-documents/{id}/versions", "GET /api/base-documents/{id}/versions",
    "GET /api/base-documents/{id}/download",
    "GET /api/documents", "POST /api/documents", "GET /api/documents/{document_id}",
    "DELETE /api/documents/{document_id}", "PUT /api/documents/{document_id}/draft",
    "DELETE /api/documents/{document_id}/draft", "GET /api/documents/{document_id}/history",
    "GET /api/documents/{document_id}/updates", "GET /api/documents/{document_id}/blocks/{block_id}/bump",
    "POST /api/documents/{document_id}/blocks/{block_id}/bump", "GET /api/documents/{document_id}/export",
    "GET /api/documents/{document_id}/exports", "GET /api/exports/{export_id}",
    "GET /api/documents/{document_id}/revision-history",  # milestone 5, see test_b_cannot_touch_a_documents
    "GET /api/exports/{export_id}/download", "GET /api/library/snippets", "GET /api/snippets/{snippet_id}/used-by",
    # milestone 7: all covered by test_b_cannot_touch_a_imports
    "POST /api/import/docx", "GET /api/imports", "GET /api/imports/{import_id}", "PUT /api/imports/{import_id}",
    "GET /api/imports/{import_id}/export", "GET /api/imports/{import_id}/split", "POST /api/imports/{import_id}/split",
    "POST /api/imports/{import_id}/selection-snippet", "POST /api/imports/{import_id}/base-document",
    # round 5A: test_vars.test_roles_and_isolation_of_variable_routes
    "GET /api/imports/{import_id}/variables", "POST /api/imports/{import_id}/variables",
    "PUT /api/base-documents/{id}/variables", "GET /api/base-documents/{id}/find-variables",
    "POST /api/base-documents/{id}/find-variables",
    # milestone 8: test_b_cannot_delete_a_snippets_and_imports / test_cleanup_is_org_scoped
    "DELETE /api/imports/{import_id}", "DELETE /api/snippets/{snippet_id}", "POST /api/maintenance/cleanup",
    # UI round: test_overview.py::test_overview_counts_and_isolation
    "GET /api/overview",
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


def test_b_cannot_touch_a_documents(world):
    import time
    from .test_base_docs import make_docx, upload
    from .test_documents import put, ref
    from .test_git import commit, mk, save
    a, b = world["a"], world["b"]
    fa = a.post("/api/folders", json={"name": "Secret"}).json()["id"]
    sid = mk(a, fa, "S")
    save(a, sid, "classified")
    sha = commit(a, fa, [sid]).json()["sha"]
    bd = upload(a, make_docx(), "Base").json()
    did = a.post("/api/documents", json={"folder_id": fa, "title": "Doc"}).json()["id"]
    blk = put(a, did, [ref(fa, sid, "commit", sha)], base_document={"id": bd["id"]})["blocks"][0]["id"]
    commit_r = a.post(f"/api/folders/{fa}/commit", json={"message": "d", "document_ids": [did]})
    assert commit_r.status_code == 201
    eid = a.get(f"/api/documents/{did}/export").json()["export_id"]
    for _ in range(100):
        if a.get(f"/api/exports/{eid}").json()["status"] == "done":
            break
        time.sleep(0.1)
    bfo = b.post("/api/folders", json={"name": "Mine"}).json()["id"]
    bdoc = b.post("/api/documents", json={"folder_id": bfo, "title": "B doc"}).json()["id"]
    bsid = mk(b, bfo, "B")
    save(b, bsid, "mine")
    commit(b, bfo, [bsid])
    body = {"blocks": [ref(fa, sid, "commit", sha)]}
    calls = [
        ("get", f"/api/base-documents/{bd['id']}", None), ("get", f"/api/base-documents/{bd['id']}/versions", None),
        ("get", f"/api/base-documents/{bd['id']}/download", None),
        ("get", f"/api/documents/{did}", None), ("get", f"/api/documents/{did}?version={sha}", None),
        ("delete", f"/api/documents/{did}", None), ("put", f"/api/documents/{did}/draft", {}),
        ("delete", f"/api/documents/{did}/draft", None), ("get", f"/api/documents/{did}/history", None),
        ("get", f"/api/documents/{did}/updates", None), ("get", f"/api/documents/{did}/blocks/{blk}/bump", None),
        ("post", f"/api/documents/{did}/blocks/{blk}/bump", None), ("get", f"/api/documents/{did}/export", None),
        ("get", f"/api/documents/{did}/exports", None), ("get", f"/api/documents/{did}/revision-history", None),
        ("get", f"/api/documents/{did}/export?format=pdf", None), ("get", f"/api/exports/{eid}", None),
        ("get", f"/api/exports/{eid}/download", None), ("get", f"/api/snippets/{sid}/used-by", None),
        ("post", f"/api/folders/{fa}/commit", {"message": "x", "document_ids": [did]}),
        ("post", f"/api/folders/{bfo}/commit", {"message": "x", "document_ids": [did]}),  # A's doc into B's folder
        ("post", "/api/documents", {"folder_id": fa, "title": "x"}),
        ("put", f"/api/documents/{bdoc}/draft", body),  # B referencing A's snippet
        ("put", f"/api/documents/{bdoc}/draft", {"base_document": {"id": bd["id"]}}),  # ... or A's base doc
        ("get", f"/api/styles?base_doc={bd['id']}", None),
    ]
    for method, url, jb in calls:
        r = getattr(b, method)(url, **({"json": jb} if jb is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    assert b.get("/api/base-documents").json() == [] and b.get("/api/documents").json()[0]["id"] == bdoc
    assert [g["folder_name"] for g in b.get("/api/library/snippets").json()] == ["Mine"]  # A's snippets are not in B's library
    assert b.get(f"/api/documents?folder_id={fa}").json() == []
    assert a.get(f"/api/documents/{did}/exports").json()[0]["status"] == "done"
    assert a.get(f"/api/documents/{did}").json()["head_sha"] == commit_r.json()["sha"]
    # B cannot create a base document that overwrites A's repo/path, and B's _base repo is separate
    assert b.post("/api/base-documents", data={"name": "B base"}).status_code == 201
    from app.git import repo as git
    assert git.base_repo_path(world["a_org"]) != git.base_repo_path(b.get("/api/orgs/current").json()["id"])
    assert len(a.get("/api/base-documents").json()) == 1


def test_rls_blocks_cross_org_document_rows(world):
    from .test_base_docs import make_docx, upload
    a = world["a"]
    fo = a.post("/api/folders", json={"name": "F"}).json()["id"]
    a.post("/api/documents", json={"folder_id": fo, "title": "D"})
    upload(a, make_docx())
    b_org = world["b"].get("/api/orgs/current").json()["id"]
    with _app_conn() as c:
        c.execute(text("select set_config('app.org_id', :o, false)"), {"o": b_org})
        for t in ("documents", "document_drafts", "base_documents", "base_doc_versions", "document_refs", "exports"):
            assert c.scalar(text(f"select count(*) from {t}")) == 0
        with pytest.raises(Exception):
            c.execute(text("insert into base_documents (id, org_id, name) values (gen_random_uuid(), :o, 'x')"),
                      {"o": world["a_org"]})


def test_b_cannot_touch_a_imports(world):
    from .test_import import SAMPLE, imp
    a, b = world["a"], world["b"]
    iid = imp(a).json()["id"]
    fa = a.post("/api/folders", json={"name": "A"}).json()["id"]
    bfo = b.post("/api/folders", json={"name": "B"}).json()["id"]
    b_imp = imp(b).json()["id"]
    sel = {"title": "x", "start": 0, "end": 1}
    calls = [
        ("get", f"/api/imports/{iid}", None), ("put", f"/api/imports/{iid}", {"content": {"type": "doc", "content": []}}),
        ("get", f"/api/imports/{iid}/export", None), ("get", f"/api/imports/{iid}/split?level=1", None),
        ("post", f"/api/imports/{iid}/split", {"folder_id": bfo, "level": 1}),  # A's import into B's folder
        ("post", f"/api/imports/{iid}/selection-snippet", {**sel, "folder_id": bfo}),
        ("post", f"/api/imports/{iid}/base-document", {"name": "stolen"}),
        ("post", f"/api/imports/{b_imp}/split", {"folder_id": fa, "level": 1}),  # B's import into A's folder
        ("post", f"/api/imports/{b_imp}/selection-snippet", {**sel, "folder_id": fa}),
    ]
    for method, url, body in calls:
        r = getattr(b, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    assert [i["id"] for i in b.get("/api/imports").json()] == [b_imp]
    assert b.get("/api/base-documents").json() == [] and a.get(f"/api/folders/{fa}/snippets").json() == []
    assert a.get(f"/api/imports/{iid}").status_code == 200
    # B can't reference A's imported image in its own import content either: images are org-scoped
    img = next(x for x in a.get(f"/api/imports/{iid}").json()["content"]["content"] if x["type"] == "image")["attrs"]["imageId"]
    assert b.get(f"/api/images/{img}").status_code == 404
    doc = {"type": "doc", "content": [{"type": "image", "attrs": {"imageId": img}}]}
    b.put(f"/api/imports/{b_imp}", json={"content": doc})
    r = b.get(f"/api/imports/{b_imp}/export")
    assert r.status_code == 200 and r.headers["x-export-warnings"] == "1"  # "image not found"


def test_rls_blocks_cross_org_import_rows(world):
    from .test_import import imp
    imp(world["a"])
    b_org = world["b"].get("/api/orgs/current").json()["id"]
    with _app_conn() as c:
        assert c.scalar(text("select count(*) from imports")) == 0
        c.execute(text("select set_config('app.org_id', :o, false)"), {"o": b_org})
        assert c.scalar(text("select count(*) from imports")) == 0
        with pytest.raises(Exception):
            c.execute(text("insert into imports (id, org_id, title, filename, content) values "
                           "(gen_random_uuid(), :o, 't', 'f', '{}')"), {"o": world["a_org"]})


def test_b_cannot_delete_a_snippets_and_imports(world):
    from .test_import import imp
    from .test_git import mk, save
    a, b = world["a"], world["b"]
    iid = imp(a).json()["id"]
    fo = a.post("/api/folders", json={"name": "F"}).json()["id"]
    sid = mk(a, fo, "S")
    save(a, sid, "x")
    assert b.delete(f"/api/imports/{iid}").status_code == 404
    assert b.delete(f"/api/snippets/{sid}").status_code == 404
    assert a.get(f"/api/imports/{iid}").status_code == 200 and a.get(f"/api/snippets/{sid}").status_code == 200


def test_cleanup_is_org_scoped(world, owner_engine):
    from .test_editor import PNG
    a, b = world["a"], world["b"]
    img = a.post("/api/images", files={"file": ("x.png", PNG, "image/png")}).json()["id"]
    with owner_engine.begin() as c:
        c.execute(text("update images set created_at = now() - interval '3 days'"))
    assert b.post("/api/maintenance/cleanup").json()["images_deleted"] == 0  # B is owner of its own org only
    assert a.get(f"/api/images/{img}").status_code == 200
