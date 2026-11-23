from .conftest import PW, login, new_client, signup


def test_signup_login_logout_me(client):
    me = signup(client, "A@Example.com", "Acme")
    assert me["org"]["role"] == "owner" and me["user"]["email"] == "a@example.com"
    assert client.get("/api/auth/me").json()["org"]["name"] == "Acme"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    c2 = new_client()
    assert login(c2, "a@example.com")["org"]["name"] == "Acme"


def test_cookie_is_httponly(client):
    r = client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW, "name": "n", "org_name": "o"})
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
    assert c.post("/api/auth/signup", json={"email": "a@b.co", "password": PW, "name": "n", "org_name": "o"}).status_code == 409
    assert c.post("/api/auth/signup", json={"email": "bad", "password": "short", "name": "", "org_name": "o"}).status_code == 422


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
