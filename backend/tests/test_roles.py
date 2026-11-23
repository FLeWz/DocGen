import pytest

from .conftest import add_member, signup


@pytest.fixture
def team(client, outbox):
    signup(client, "owner@x.co", "Acme")
    t = {r: add_member(client, outbox, f"{r}@x.co", r) for r in ("admin", "editor", "viewer")}
    t["owner"] = client
    uid = {m["email"].split("@")[0]: m["user_id"] for m in client.get("/api/members").json()}
    return t, uid


def test_everyone_reads_members_and_org(team):
    t, _ = team
    for c in t.values():
        assert len(c.get("/api/members").json()) == 4
        assert c.get("/api/orgs/current").status_code == 200


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_low_roles_cannot_manage(team, role):
    t, uid = team
    c = t[role]
    assert c.get("/api/invites").status_code == 403
    assert c.post("/api/invites", json={"email": "n@x.co", "role": "viewer"}).status_code == 403
    assert c.patch(f"/api/members/{uid['viewer']}", json={"role": "editor"}).status_code == 403
    assert c.delete(f"/api/members/{uid['admin']}").status_code == 403
    assert c.delete("/api/orgs/current").status_code == 403


def test_admin_scope(team):
    t, uid = team
    a = t["admin"]
    assert a.get("/api/invites").status_code == 200
    assert a.post("/api/invites", json={"email": "n@x.co", "role": "editor"}).status_code == 201
    assert a.post("/api/invites", json={"email": "o2@x.co", "role": "owner"}).status_code == 403
    assert a.patch(f"/api/members/{uid['viewer']}", json={"role": "editor"}).status_code == 200
    assert a.patch(f"/api/members/{uid['viewer']}", json={"role": "owner"}).status_code == 403
    assert a.patch(f"/api/members/{uid['owner']}", json={"role": "viewer"}).status_code == 403
    assert a.delete(f"/api/members/{uid['owner']}").status_code == 403
    assert a.delete("/api/orgs/current").status_code == 403
    assert a.delete(f"/api/members/{uid['editor']}").status_code == 204


def test_removed_member_loses_access(team):
    t, uid = team
    assert t["owner"].delete(f"/api/members/{uid['editor']}").status_code == 204
    assert t["editor"].get("/api/members").status_code == 400  # no active org any more
    assert t["editor"].get("/api/orgs").json() == []


def test_owner_rules(team):
    t, uid = team
    o = t["owner"]
    assert o.patch(f"/api/members/{uid['owner']}", json={"role": "admin"}).status_code == 409  # last owner
    assert o.delete(f"/api/members/{uid['owner']}").status_code == 409
    assert o.patch(f"/api/members/{uid['admin']}", json={"role": "owner"}).status_code == 200
    assert o.patch(f"/api/members/{uid['owner']}", json={"role": "admin"}).status_code == 200  # no longer last


def test_self_leave_and_owner_delete(team):
    t, uid = team
    assert t["viewer"].delete(f"/api/members/{uid['viewer']}").status_code == 204
    assert t["owner"].delete("/api/orgs/current").status_code == 204
    assert t["admin"].get("/api/orgs").json() == []
    assert t["owner"].get("/api/orgs").json() == []
