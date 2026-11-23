from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from .conftest import PW, login, new_client, signup, token_from


def invite(c, email, role="editor"):
    return c.post("/api/invites", json={"email": email, "role": role})


def test_new_user_flow(client, outbox, owner_engine):
    signup(client, "o@x.co", "Acme", "Olga")
    r = invite(client, "New@X.co")
    assert r.status_code == 201 and r.json()["email_sent"] and r.json()["status"] == "pending"
    to, subject, body = outbox[-1]
    assert to == "new@x.co" and "Acme" in subject
    tok = token_from(outbox)
    with owner_engine.connect() as c:  # the raw token is never stored
        assert c.scalar(text("select count(*) from invites where token_hash = :t"), {"t": tok}) == 0
    guest = new_client()  # public lookup, no login
    lk = guest.post("/api/invites/lookup", json={"token": tok}).json()
    assert lk == {"org_name": "Acme", "email": "new@x.co", "role": "editor", "status": "pending", "user_exists": False}
    assert guest.post("/api/invites/accept", json={"token": tok}).status_code == 422  # needs name+password
    me = guest.post("/api/invites/accept", json={"token": tok, "name": "Newbie", "password": PW}).json()
    assert me["org"]["name"] == "Acme" and me["org"]["role"] == "editor"
    assert guest.get("/api/auth/me").json()["user"]["email"] == "new@x.co"
    assert {m["email"] for m in client.get("/api/members").json()} == {"o@x.co", "new@x.co"}
    assert client.get("/api/invites").json()[0]["status"] == "accepted"
    # single use
    assert new_client().post("/api/invites/accept", json={"token": tok, "name": "x", "password": PW}).status_code == 410


def test_existing_user_flow(client, outbox):
    signup(client, "o@x.co", "Acme")
    other = new_client()
    signup(other, "e@x.co", "Mine")
    invite(client, "e@x.co", "viewer")
    tok = token_from(outbox)
    guest = new_client()
    assert guest.post("/api/invites/lookup", json={"token": tok}).json()["user_exists"] is True
    assert guest.post("/api/invites/accept", json={"token": tok, "name": "x", "password": PW}).status_code == 401
    login(guest, "e@x.co")
    me = guest.post("/api/invites/accept", json={"token": tok}).json()
    assert me["org"]["name"] == "Acme" and me["org"]["role"] == "viewer"
    assert {o["name"] for o in guest.get("/api/orgs").json()} == {"Acme", "Mine"}


def test_wrong_account_cannot_accept(client, outbox):
    signup(client, "o@x.co")
    invite(client, "e@x.co")
    other = new_client()
    signup(other, "z@x.co")
    assert other.post("/api/invites/accept", json={"token": token_from(outbox)}).status_code == 403


def test_unknown_token_404(client):
    assert client.post("/api/invites/lookup", json={"token": "x" * 30}).status_code == 404
    assert client.post("/api/invites/accept", json={"token": "x" * 30}).status_code == 404


def test_expired_and_revoked(client, outbox, owner_engine):
    signup(client, "o@x.co")
    iid = invite(client, "a@x.co").json()["id"]
    tok = token_from(outbox)
    with owner_engine.begin() as c:
        c.execute(text("update invites set expires_at=:e"), {"e": datetime.now(timezone.utc) - timedelta(seconds=1)})
    g = new_client()
    assert g.post("/api/invites/lookup", json={"token": tok}).json()["status"] == "expired"
    assert g.post("/api/invites/accept", json={"token": tok, "name": "a", "password": PW}).status_code == 410
    # resend revives with a fresh token; old link is dead
    assert client.post(f"/api/invites/{iid}/resend").json()["status"] == "pending"
    assert g.post("/api/invites/lookup", json={"token": tok}).status_code == 404
    tok2 = token_from(outbox)
    assert client.delete(f"/api/invites/{iid}").status_code == 204
    assert g.post("/api/invites/accept", json={"token": tok2, "name": "a", "password": PW}).status_code == 410
    assert client.delete(f"/api/invites/{iid}").status_code == 409


def test_expiry_is_7_days(client, outbox):
    signup(client, "o@x.co")
    exp = datetime.fromisoformat(invite(client, "a@x.co").json()["expires_at"])
    assert timedelta(days=6, hours=23) < exp - datetime.now(timezone.utc) <= timedelta(days=7)


def test_duplicates(client, outbox):
    signup(client, "o@x.co")
    assert invite(client, "a@x.co").status_code == 201
    assert invite(client, "A@x.co").status_code == 409  # pending exists
    assert invite(client, "o@x.co").status_code == 409  # already a member
    assert invite(client, "bad-email").status_code == 422
    assert client.post("/api/invites", json={"email": "q@x.co", "role": "god"}).status_code == 422


def test_mail_failure_still_creates(client, monkeypatch):
    monkeypatch.setattr("app.mail.send_mail", lambda *a: False)
    signup(client, "o@x.co")
    r = invite(client, "a@x.co")
    assert r.status_code == 201 and r.json()["email_sent"] is False
