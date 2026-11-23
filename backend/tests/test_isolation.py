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
    assert {r[0] for r in rows} >= {"memberships", "invites"}
    assert all(r[1] and r[2] == "NO" for r in rows), rows


COVERED = {  # every API route must be listed: a new endpoint forces a new isolation test
    "POST /api/auth/signup", "POST /api/auth/login", "POST /api/auth/logout", "GET /api/auth/me",
    "GET /api/auth/csrf",  # user/session scoped, no tenant data
    "GET /api/orgs", "POST /api/orgs", "GET /api/orgs/current", "POST /api/orgs/switch", "DELETE /api/orgs/current",
    "GET /api/members", "PATCH /api/members/{user_id}", "DELETE /api/members/{user_id}",
    "GET /api/invites", "POST /api/invites", "POST /api/invites/{id}/resend", "DELETE /api/invites/{id}",
    "POST /api/invites/lookup", "POST /api/invites/accept",  # token-scoped, see test_invites
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
