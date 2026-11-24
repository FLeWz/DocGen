import pytest

from .conftest import PW, login, new_client, signup, token_from


def test_signup_login_logout_me(client):
    me = signup(client, "A@Example.com", "Acme")
    assert me["org"]["role"] == "owner" and me["user"]["email"] == "a@example.com"
    assert client.get("/api/auth/me").json()["org"]["name"] == "Acme"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    c2 = new_client()
    assert login(c2, "a@example.com")["org"]["name"] == "Acme"


def test_cookie_is_httponly(client):
    signup(client, "a@b.co")
    r = new_client().post("/api/auth/login", json={"email": "a@b.co", "password": PW})
    assert "httponly" in r.headers["set-cookie"].lower()


def test_csrf_required(client):
    del client.headers["X-CSRF-Token"]
    r = client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW, "name": "n", "org_name": "o"})
    assert r.status_code == 403
    client.headers["X-CSRF-Token"] = "forged.value"
    assert client.post("/api/auth/logout").status_code == 403


def test_bad_logins_and_validation(client):
    signup(client, "a@b.co")
    c = new_client()
    assert c.post("/api/auth/login", json={"email": "a@b.co", "password": "nope-nope-nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "x@b.co", "password": PW}).status_code == 401


def test_second_org_and_switch(client):
    signup(client, "a@b.co", "One")
    org2 = client.post("/api/orgs", json={"name": "Two"}).json()
    assert client.get("/api/orgs/current").json()["name"] == "Two"
    one = [o for o in client.get("/api/orgs").json() if o["name"] == "One"][0]
    assert client.post("/api/orgs/switch", json={"org_id": one["id"]}).status_code == 200
    assert client.get("/api/orgs/current").json()["id"] == one["id"]
    assert org2["id"] != one["id"]


def test_unauthenticated(client):
    for p in ("/api/orgs", "/api/orgs/current", "/api/members", "/api/invites"):
        assert client.get(p).status_code == 401


SIGNUP = {"email": "n@b.co", "password": PW, "name": "n", "org_name": "o"}


def test_signup_closed_by_default(client):
    assert client.get("/api/auth/me/config").json() == {"allow_signup": False}
    assert client.post("/api/auth/signup", json=SIGNUP).status_code == 404
    assert client.get("/api/auth/me").status_code == 401  # nothing was created / logged in
    assert client.post("/api/auth/login", json={"email": "n@b.co", "password": PW}).status_code == 401


def test_signup_when_enabled(client, monkeypatch):
    monkeypatch.setattr("app.config.ALLOW_SIGNUP", True)
    assert client.get("/api/auth/me/config").json() == {"allow_signup": True}
    assert client.post("/api/auth/signup", json=SIGNUP).status_code == 201
    assert new_client().post("/api/auth/signup", json=SIGNUP).status_code == 409
    assert new_client().post("/api/auth/signup", json={**SIGNUP, "email": "bad", "password": "short"}).status_code == 422


def test_invite_accept_still_creates_user(client, outbox):
    signup(client, "o@x.co", "Acme")
    assert client.post("/api/invites", json={"email": "new@x.co", "role": "editor"}).status_code == 201
    c = new_client()
    r = c.post("/api/invites/accept", json={"token": token_from(outbox), "name": "New", "password": PW})
    assert r.status_code == 200 and r.json()["org"]["role"] == "editor"
    assert login(new_client(), "new@x.co")["user"]["name"] == "New"


def test_manage_create_user(client, monkeypatch, capsys):
    import io
    from app import manage
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    manage.main(["create-user", "--email", "Boot@X.co", "--name", "Boot", "--org", "First", "--password-stdin"])
    me = login(client, "boot@x.co")
    assert me["org"] == {**me["org"], "name": "First", "role": "owner"}
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    with pytest.raises(SystemExit):  # duplicate
        manage.main(["create-user", "--email", "boot@x.co", "--name", "B", "--org", "F", "--password-stdin"])
