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


def test_folder_snippet_roles(client, outbox):
    from .conftest import add_member, signup
    signup(client, "o@x.co", "Org")
    ed, vw = add_member(client, outbox, "e@x.co", "editor"), add_member(client, outbox, "v@x.co", "viewer")
    ad = add_member(client, outbox, "a@x.co", "admin")
    assert vw.post("/api/folders", json={"name": "F"}).status_code == 403
    fo = ed.post("/api/folders", json={"name": "F"}).json()["id"]
    sid = ed.post(f"/api/folders/{fo}/snippets", json={"title": "S"}).json()["id"]
    doc = {"content": {"type": "doc", "content": []}}
    assert vw.get("/api/folders").status_code == 200 and vw.get(f"/api/folders/{fo}/snippets").status_code == 200
    assert vw.get(f"/api/snippets/{sid}").status_code == 200
    assert vw.get(f"/api/folders/{fo}/changes").status_code == 200
    for method, url, body in [
        ("post", f"/api/folders/{fo}/snippets", {"title": "T"}), ("put", f"/api/snippets/{sid}/draft", doc),
        ("delete", f"/api/snippets/{sid}/draft", None), ("post", f"/api/snippets/{sid}/resolve", doc),
        ("post", f"/api/snippets/{sid}/restore", {"version": "abc1234"}),
        ("post", f"/api/folders/{fo}/commit", {"message": "m", "snippet_ids": [sid]}),
        ("post", f"/api/folders/{fo}/tags", {"name": "v1"}), ("patch", f"/api/folders/{fo}", {"name": "Z"}),
        ("delete", f"/api/folders/{fo}", None)]:
        assert getattr(vw, method)(url, **({"json": body} if body is not None else {})).status_code == 403, url
    assert ed.delete(f"/api/folders/{fo}").status_code == 403  # admin only
    assert ed.post(f"/api/folders/{fo}/commit", json={"message": "m", "snippet_ids": [sid]}).status_code == 201
    assert ad.delete(f"/api/folders/{fo}").status_code == 204
