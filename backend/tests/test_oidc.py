"""OpenID Connect against tests/mock_idp.py (a real HTTP server on loopback, RSA-signed tokens)."""
import socket
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
import uvicorn

from app import config, oidc
from sqlalchemy import text

from app.db import SessionLocal

from .conftest import PW, new_client, signup, token_from
from .mock_idp import Idp, make_app

CB = "http://testserver/api/auth/oidc/callback"


@pytest.fixture(scope="module")
def idp_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    idp = Idp(f"http://127.0.0.1:{port}")
    srv = uvicorn.Server(uvicorn.Config(make_app(idp), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield idp
    srv.should_exit = True


@pytest.fixture
def idp(idp_server, monkeypatch):
    idp_server.user = {"sub": "u-1", "email": "ann@example.com", "email_verified": True, "name": "Ann Example"}
    idp_server.tamper, idp_server.userinfo_only = {}, set()
    idp_server.extra = {"access": ra("viewer")}  # every login needs a client role; tests override
    for k, v in dict(OIDC_ROLES_CLIENT_ID="docgen", OIDC_REALM_ROLES=False, OIDC_ROLE_PREFIX="", OIDC_DEFAULT_ROLE="",
                     OIDC_GROUPS_CLAIM="", OIDC_ROLE_MAP={}, AUTH_MODE="oidc", OIDC_ISSUER=idp_server.issuer, OIDC_CLIENT_ID="docgen", OIDC_CLIENT_SECRET="shh",
                     OIDC_REDIRECT_URI=CB).items():
        monkeypatch.setattr(config, k, v)
    oidc._disc.clear()
    oidc._jwks.clear()
    return idp_server


def ra(*roles, client="docgen"):
    return {"resource_access": {client: {"roles": list(roles)}}}


def role_of(c):
    return c.get("/api/auth/me").json()["org"]["role"]


def go(client, return_to=None):
    """One full browser round trip: /login -> IdP (auto-approve) -> callback. Returns the callback response."""
    r = client.get("/api/auth/oidc/login", params={"return_to": return_to} if return_to else {}, follow_redirects=False)
    assert r.status_code == 302, r.text
    a = httpx.get(r.headers["location"], follow_redirects=False)
    assert a.status_code == 302, a.text
    return client.get(a.headers["location"].split("testserver", 1)[1], follow_redirects=False)


def denied(r, *words):
    assert r.status_code == 302 and r.headers["location"].startswith("/login?error="), r.headers
    msg = unquote(r.headers["location"].split("error=", 1)[1])
    for w in words:
        assert w in msg, msg
    return msg


def test_authorize_request_has_pkce_state_nonce(idp, client):
    r = client.get("/api/auth/oidc/login", params={"return_to": "/x"}, follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["response_type"] == ["code"] and q["redirect_uri"] == [CB]
    assert q["scope"] == ["openid profile email"] and q["state"][0] and q["nonce"][0] and q["code_challenge"][0]
    c = r.headers["set-cookie"].lower()
    assert "httponly" in c and "max-age=600" in c and "path=/api/auth/oidc" in c


def test_login_provisions_first_user_as_owner_then_role_from_token(idp, client):
    r = go(client)
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert "httponly" in r.headers["set-cookie"].lower()
    me = client.get("/api/auth/me").json()
    assert me["user"] == {**me["user"], "email": "ann@example.com", "name": "Ann Example"}
    assert me["org"]["name"] == "DocGen" and me["org"]["role"] == "owner"
    assert client.get("/api/auth/me").status_code == 200
    idp.user = {"sub": "u-2", "email": "Bob@Example.com", "name": "Bob"}
    c2 = new_client()
    assert go(c2).headers["location"] == "/"
    me2 = c2.get("/api/auth/me").json()
    assert me2["user"]["email"] == "bob@example.com" and me2["org"]["role"] == "viewer"
    assert me2["org"]["id"] == me["org"]["id"]  # same default org
    assert go(new_client()).status_code == 302  # u-2 again: no duplicate user
    assert len(new_client().get("/api/auth/config").json()) == 3


def test_public_client_without_secret(idp, client, monkeypatch):
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "")
    idp.client_secret = ""
    try:
        assert go(client).headers["location"] == "/"
    finally:
        idp.client_secret = "shh"


def test_return_to_only_relative(idp, client):
    assert go(client, "/accept-invite?token=abc").headers["location"] == "/accept-invite?token=abc"
    for evil in ("https://evil.example/", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/a\r\nb"):
        assert go(new_client(), evil).headers["location"] == "/"


def test_state_mismatch_and_missing_cookie(idp, client):
    idp.tamper = {"state": "forged"}
    denied(go(client), "state mismatch")
    assert client.get("/api/auth/me").status_code == 401
    idp.tamper = {}
    r = client.get("/api/auth/oidc/login", follow_redirects=False)
    a = httpx.get(r.headers["location"], follow_redirects=False)
    victim = new_client()  # the attacker's code+state replayed in another browser: no state cookie there
    denied(victim.get(a.headers["location"].split("testserver", 1)[1], follow_redirects=False), "expired or the browser")


def test_state_cookie_cannot_be_forged_or_replayed_after_expiry(idp, client, monkeypatch):
    c = oidc.seal({"s": "a", "n": "b", "v": "c", "r": "/"})
    assert oidc.unseal(c)["s"] == "a"
    with pytest.raises(oidc.OidcError):
        oidc.unseal(c[:-2] + "00")
    later = time.time() + 700
    monkeypatch.setattr(oidc.time, "time", lambda: later)
    with pytest.raises(oidc.OidcError):
        oidc.unseal(c)


@pytest.mark.parametrize("tamper,words", [
    ({"nonce": "other"}, "nonce mismatch"),
    ({"exp_delta": -3600}, "expired"),
    ({"aud": "someone-else"}, "Audience"),
    ({"iss": "https://evil.example"}, "issuer"),
    ({"bad_sig": True}, "Signature verification failed"),
    ({"azp": "another-client"}, "azp"),
    ({"drop": ["sub"]}, "sub"),
])
def test_bad_id_tokens_rejected(idp, client, tamper, words):
    idp.tamper = tamper
    denied(go(client), "rejected", words)
    assert client.get("/api/auth/me").status_code == 401


def test_key_rotation_refetches_jwks(idp, client, monkeypatch):
    monkeypatch.setattr(oidc, "JWKS_REFRESH_MIN_S", 0)
    assert go(client).status_code == 302
    from .mock_idp import _key
    idp.key, idp.kid = _key(), "k2"
    assert go(new_client()).headers["location"] == "/"


def test_missing_email_disallowed_domain_and_fallbacks(idp, client, monkeypatch):
    idp.tamper = {"drop": ["email"]}
    denied(go(client), "did not send an email")
    idp.user["preferred_username"] = "ann@corp.example"  # fallback claim containing '@'
    assert go(new_client()).status_code == 302 and new_client().get("/api/auth/config").status_code == 200
    idp.tamper, idp.userinfo_only = {}, {"email"}  # only /userinfo has the email
    idp.user["sub"] = "u-9"
    idp.user["email"] = "viaui@example.com"
    c = new_client()
    assert go(c).headers["location"] == "/" and c.get("/api/auth/me").json()["user"]["email"] == "viaui@example.com"
    monkeypatch.setattr(config, "OIDC_ALLOWED_EMAIL_DOMAINS", {"corp.example"})
    idp.userinfo_only = set()
    denied(go(new_client()), "not allowed", "domain")
    idp.user["email"] = "ok@CORP.example"
    idp.user["sub"] = "u-10"
    assert go(new_client()).headers["location"] == "/"


def test_groups_map_to_roles_and_refresh_each_login(idp, client, monkeypatch):
    monkeypatch.setattr(config, "OIDC_GROUPS_CLAIM", "realm.groups")  # dotted path into a nested claim
    monkeypatch.setattr(config, "OIDC_ROLE_MAP", {"/docgen/admin": "admin", "docgen-editors": "editor"})
    go(client)  # first user = owner
    idp.extra = {}
    idp.user = {"sub": "u-2", "email": "bob@example.com", "realm": {"groups": ["x", "docgen-editors", "/docgen/admin"]}}
    c = new_client()
    go(c)
    assert role_of(c) == "admin"  # highest mapped role
    idp.user["realm"]["groups"] = ["docgen-editors"]
    go(c)
    assert role_of(c) == "editor"  # demoted
    idp.user["realm"]["groups"] = ["x"]
    denied(go(c), "no DocGen role")
    assert role_of(c) == "editor"  # refused login leaves the membership untouched


@pytest.mark.parametrize("role", ["owner", "admin", "editor", "viewer"])
def test_first_login_creates_account_with_access_token_client_role(idp, client, role):
    go(client)  # ann: owner of the fresh org
    idp.user = {"sub": "u-2", "email": "bob@example.com", "name": "Bob"}
    idp.extra = {"access": ra(role)}
    c = new_client()
    assert go(c).headers["location"] == "/" and role_of(c) == role


@pytest.mark.parametrize("where", ["userinfo", "id"])
def test_role_only_in_userinfo_or_id_token(idp, client, where):
    go(client)
    idp.user = {"sub": "u-2", "email": "bob@example.com"}
    idp.extra = {where: ra("editor")}
    c = new_client()
    go(c)
    assert role_of(c) == "editor"


def counts():
    with SessionLocal() as db:
        return db.execute(text("select (select count(*) from users), (select count(*) from identities), (select count(*) from memberships)")).one()


def test_no_role_is_refused_and_nothing_is_created(idp, client):
    idp.extra = {}
    before = counts()
    msg = denied(go(client), "Signed in as ann@example.com, but no DocGen role is assigned",
                 "owner, admin, editor or viewer client role of docgen")
    assert client.get("/api/auth/me").status_code == 401 and counts() == before, msg
    idp.extra = {"access": ra("viewer", client="other"), "userinfo": ra("admin", client="doc")}  # roles of other clients
    denied(go(client), "no DocGen role")
    idp.extra = {"access": {"resource_access": {"docgen": {"roles": ["superuser", "uma_authorization"]}}}}
    denied(go(client), "no DocGen role")
    assert counts() == before


def test_client_id_with_dots_and_override(idp, client, monkeypatch):
    monkeypatch.setattr(config, "OIDC_ROLES_CLIENT_ID", "docgen.prod")
    idp.extra = {"access": ra("editor", client="docgen.prod")}
    go(client)
    assert role_of(client) == "owner"  # first user
    idp.extra = {"access": ra("viewer", client="docgen")}
    denied(go(client), "client role of docgen.prod")


def test_role_case_prefix_and_highest_wins(idp, client, monkeypatch):
    go(client)
    idp.user = {"sub": "u-2", "email": "bob@example.com"}
    c = new_client()
    idp.extra = {"access": ra(" Editor ", "VIEWER")}
    go(c)
    assert role_of(c) == "editor"
    monkeypatch.setattr(config, "OIDC_ROLE_PREFIX", "docgen-")
    idp.extra = {"access": ra("docgen-Admin", "viewer", "offline_access")}
    go(c)
    assert role_of(c) == "admin"
    idp.extra = {"access": ra("editor")}  # unprefixed names are still accepted
    go(c)
    assert role_of(c) == "editor"


def test_roles_from_all_sources_highest_wins(idp, client):
    go(client)
    idp.user = {"sub": "u-2", "email": "bob@example.com"}
    idp.extra = {"id": ra("viewer"), "access": ra("editor"), "userinfo": ra("admin")}
    c = new_client()
    go(c)
    assert role_of(c) == "admin"


def test_promotion_and_demotion_at_next_login(idp, client):
    go(client)
    idp.user = {"sub": "u-2", "email": "bob@example.com"}
    c = new_client()
    for r in ("viewer", "admin", "editor", "owner", "viewer"):
        idp.extra = {"access": ra(r)}
        go(c)
        assert role_of(c) == r, r


def test_last_owner_is_never_demoted(idp, client, caplog):
    go(client)  # ann = only owner
    idp.extra = {"access": ra("viewer")}
    with caplog.at_level("WARNING"):
        go(client)
    assert role_of(client) == "owner" and "only owner" in caplog.text
    idp.user = {"sub": "u-2", "email": "bob@example.com"}  # second owner: now ann can be demoted
    idp.extra = {"access": ra("owner")}
    c = new_client()
    go(c)
    idp.user = {"sub": "u-1", "email": "ann@example.com"}
    idp.extra = {"access": ra("viewer")}
    go(client)
    assert role_of(client) == "viewer"


@pytest.mark.parametrize("tamper", [{"at_bad_sig": True}, {"at_unsigned": True}, {"at_azp": "another-client"}, {"at_opaque": True}])
def test_bad_access_token_is_ignored(idp, client, tamper, caplog):
    go(client)
    idp.user = {"sub": "u-2", "email": "bob@example.com"}
    idp.extra = {"access": ra("admin")}
    idp.tamper = tamper
    with caplog.at_level("WARNING"):
        denied(go(new_client()), "no DocGen role")  # claims of an unverified token never count
    assert "access token ignored" in caplog.text
    idp.extra["userinfo"] = ra("editor")  # userinfo (server-to-server, sub-checked) still works
    c = new_client()
    go(c)
    assert role_of(c) == "editor"


def test_realm_roles_option(idp, client, monkeypatch):
    go(client)
    idp.user = {"sub": "u-2", "email": "bob@example.com"}
    idp.extra = {"access": {"realm_access": {"roles": ["admin"]}}}
    denied(go(new_client()), "no DocGen role")  # off by default
    monkeypatch.setattr(config, "OIDC_REALM_ROLES", True)
    c = new_client()
    go(c)
    assert role_of(c) == "admin"


def test_default_role_fallback(idp, client, monkeypatch):
    go(client)
    idp.user = {"sub": "u-2", "email": "bob@example.com"}
    idp.extra = {}
    monkeypatch.setattr(config, "OIDC_DEFAULT_ROLE", "editor")
    c = new_client()
    go(c)
    assert role_of(c) == "editor"


def test_missing_resource_access_warns_with_hint(idp, client, caplog):
    idp.extra = {}
    with caplog.at_level("WARNING"):
        denied(go(client), "no DocGen role")
    assert "client roles mapper" in caplog.text and "Full scope allowed" in caplog.text


def test_auto_provision_zero_is_ignored_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("OIDC_AUTO_PROVISION", "0")
    with caplog.at_level("WARNING"):
        oidc.warn_legacy()
    assert "OIDC_AUTO_PROVISION is ignored" in caplog.text


def test_invite_is_still_accepted_but_not_required(idp, client, monkeypatch, outbox):
    owner = new_client()
    monkeypatch.setattr(config, "AUTH_MODE", "local")  # the test harness logs the inviting admin in with a password
    signup(owner, "owner@x.co", org="Acme")
    assert owner.post("/api/invites", json={"email": "Ann@Example.com", "role": "editor"}).status_code == 201
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    r = go(client)
    assert r.headers["location"] == "/"
    me = client.get("/api/auth/me").json()
    assert me["org"]["name"] == "Acme" and me["org"]["role"] == "editor"  # invite accepted at sign-in
    inv = [i for i in owner.get("/api/invites").json() if i["email"] == "ann@example.com"][0]
    assert inv["status"] == "accepted"
    assert go(new_client()).status_code == 302  # known identity logs in again
    idp.user = {"sub": "u-3", "email": "eve@example.com"}  # no invite needed
    c = new_client()
    assert go(c).headers["location"] == "/" and c.get("/api/auth/me").json()["user"]["email"] == "eve@example.com"


def test_existing_password_user_is_not_linked_unless_enabled(idp, client, monkeypatch):
    from app.routers.auth import register
    with SessionLocal() as db:
        register(db, "ann@example.com", "Local Ann", PW, "Old Org")
    denied(go(client), "already exists", "OIDC_LINK_BY_EMAIL")
    monkeypatch.setattr(config, "OIDC_LINK_BY_EMAIL", True)
    idp.user["email_verified"] = False
    denied(go(client), "already exists")  # unverified email never links
    idp.user["email_verified"] = True
    go(client)
    me = client.get("/api/auth/me").json()
    assert me["user"]["name"] == "Local Ann"  # linked to the existing user, who keeps the old org and joins the default one
    assert {o["name"] for o in client.get("/api/orgs").json()} == {"Old Org", "DocGen"}


def test_password_routes_are_gone_in_oidc_mode(idp, client, outbox, monkeypatch):
    cfg = client.get("/api/auth/config").json()
    assert cfg == {"mode": "oidc", "provider_name": config.OIDC_PROVIDER_NAME, "allow_signup": False}
    monkeypatch.setattr(config, "ALLOW_SIGNUP", True)
    for path, body in (("/api/auth/login", {"email": "a@b.co", "password": PW}),
                       ("/api/auth/signup", {"email": "a@b.co", "password": PW, "name": "n", "org_name": "o"})):
        r = client.post(path, json=body)
        assert r.status_code == 404 and "OpenID Connect" in r.json()["detail"], path
    # password invite acceptance: also closed
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    owner = new_client()
    signup(owner, "owner@x.co")
    owner.post("/api/invites", json={"email": "new@x.co", "role": "viewer"})
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    r = client.post("/api/invites/accept", json={"token": token_from(outbox), "name": "N", "password": PW})
    assert r.status_code == 404 and "disabled" in r.json()["detail"]
    from app import manage
    with pytest.raises(SystemExit, match="no password"):
        manage.main(["reset-password", "--email", "a@b.co"])


def test_oidc_routes_404_in_local_mode(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    assert client.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404
    assert client.get("/api/auth/oidc/callback", follow_redirects=False).status_code == 404
    assert client.get("/api/auth/config").json()["mode"] == "local"


def test_rp_initiated_logout(idp, client):
    go(client)
    r = client.post("/api/auth/logout")
    url = r.json()["redirect"]
    q = parse_qs(urlsplit(url).query)
    assert url.startswith(idp.issuer + "/logout") and q["post_logout_redirect_uri"] == [config.PUBLIC_URL + "/login"]
    assert q["id_token_hint"][0].count(".") == 2 and q["client_id"] == ["docgen"]
    assert client.get("/api/auth/me").status_code == 401  # local session is gone
    assert httpx.get(url, follow_redirects=False).status_code == 302 and idp.logouts[-1]["id_token_hint"] == q["id_token_hint"][0]


def test_logout_without_end_session(idp, client, monkeypatch):
    monkeypatch.setattr(config, "OIDC_END_SESSION", False)
    go(client)
    assert client.post("/api/auth/logout").json() == {"redirect": None}


def test_discovery_problems_are_readable(idp, client, monkeypatch):
    monkeypatch.setattr(config, "OIDC_ISSUER", idp.issuer + "/wrong")
    denied(client.get("/api/auth/oidc/login", follow_redirects=False), "discovery")
    monkeypatch.setattr(config, "OIDC_ISSUER", "http://127.0.0.1:1")
    oidc._disc.clear()
    denied(client.get("/api/auth/oidc/login", follow_redirects=False), "cannot reach")
    monkeypatch.setattr(config, "OIDC_ISSUER", idp.issuer)
    oidc._disc.clear()
    idp.issuer, real = "http://other", idp.issuer
    try:
        denied(client.get("/api/auth/oidc/login", follow_redirects=False), "issuer mismatch")
    finally:
        idp.issuer = real


def test_wrong_client_secret_is_reported(idp, client, monkeypatch):
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "nope")
    denied(go(client), "rejected the login", "invalid_grant")


def test_role_map_parsing():
    assert config._role_map("a=admin, b = Editor") == {"a": "admin", "b": "editor"}
    assert config._role_map('{"x": "viewer"}') == {"x": "viewer"}
    assert config._role_map("") == {}
    with pytest.raises(RuntimeError):
        config._role_map("a=root")


def test_cli_creates_sso_only_admin_who_is_linked_at_first_login(idp, client, monkeypatch, capsys):
    from app import manage
    monkeypatch.setattr(config, "OIDC_LINK_BY_EMAIL", True)
    monkeypatch.setattr(config, "OIDC_PROVIDER_NAME", "Corp SSO")
    manage.main(["create-user", "--email", "Ann@Example.com", "--name", "Ann", "--org", "Acme"])  # no password asked
    assert "SSO only" in capsys.readouterr().out
    manage.main(["list-users"])
    assert "SSO only" in capsys.readouterr().out
    assert new_client().post("/api/auth/login", json={"email": "ann@example.com", "password": PW}).status_code == 404
    go(client)
    me = client.get("/api/auth/me").json()
    assert me["user"]["name"] == "Ann" and "Acme" in {o["name"] for o in client.get("/api/orgs").json()}


def test_logout_ends_sso_and_next_login_asks_for_credentials(idp, client):
    idp.interactive = True
    try:
        sso = httpx.Client(follow_redirects=False)  # the browser's cookie jar at the IdP
        r = client.get("/api/auth/oidc/login", follow_redirects=False)
        assert "prompt=login" not in r.headers["location"]
        page = sso.get(r.headers["location"])
        assert page.status_code == 200 and "Sign in at the IdP" in page.text  # no SSO cookie: interaction required
        a = sso.post(r.headers["location"])
        assert a.status_code == 302 and "idp_sso" in sso.cookies
        client.get(a.headers["location"].split("testserver", 1)[1], follow_redirects=False)
        assert client.get("/api/auth/me").status_code == 200
        lo = client.post("/api/auth/logout")
        url = lo.json()["redirect"]
        assert "id_token_hint=" in url and "docgen_signed_out=1" in lo.headers["set-cookie"]
        assert client.get("/api/auth/me").status_code == 401  # DocGen session row is gone
        assert sso.get(url).status_code == 302 and "idp_sso" not in sso.cookies  # IdP SSO session ended
        nxt = client.get("/api/auth/oidc/login", follow_redirects=False)
        assert "prompt=login" in nxt.headers["location"]  # right after a logout
        assert "Sign in at the IdP" in sso.get(nxt.headers["location"]).text
        assert "prompt=login" not in client.get("/api/auth/oidc/login", follow_redirects=False).headers["location"]  # one-shot
    finally:
        idp.interactive = False


def test_logout_survives_unreachable_idp_and_no_session(idp, client):
    go(client)
    oidc._disc.clear()
    idp_url = config.OIDC_ISSUER
    config.OIDC_ISSUER = "http://127.0.0.1:1"
    try:
        r = client.post("/api/auth/logout")
    finally:
        config.OIDC_ISSUER = idp_url
    assert r.status_code == 200 and r.json() == {"redirect": None}
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 200  # second tab: already gone
