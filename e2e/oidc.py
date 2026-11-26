"""AUTH_MODE=oidc e2e against the mock IdP (compose profile oidc-test, `make test-e2e-oidc`): the login screen has only the
SSO button, the first user owns the default org, logout goes through the IdP's end-session endpoint, a second user gets the role
from the client roles in the access token (account auto-created), a user without a role is refused, a token without email is refused with a readable message, group mapping applies at every login."""
import os
import re

from playwright.sync_api import expect, sync_playwright

from common import BASE

IDP = os.environ.get("IDP_URL", "http://idp:9000")


def set_idp_user(page, roles=None, **claims):
    """roles: client roles of `docgen` put into the ACCESS token only (as Keycloak does)."""
    claims["_access"] = {"resource_access": {"docgen": {"roles": roles}}} if roles is not None else {}
    assert page.request.post(IDP + "/_admin/user", data=claims).ok


def sign_in(page):
    page.goto(BASE + "/login")
    page.get_by_role("link", name="Sign in with Mock IdP").click()
    page.get_by_role("button", name="Sign in at the IdP").click()  # mock IdP asks unless its SSO cookie is set


def log_out(page):
    page.get_by_role("button", name="User menu").click()
    page.get_by_role("menuitem", name="Log out").click()  # -> IdP end_session -> back to /login
    expect(page.get_by_role("link", name="Sign in with Mock IdP")).to_be_visible()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_context().new_page()
    try:
        page.goto(BASE + "/login")
        expect(page.get_by_role("link", name="Sign in with Mock IdP")).to_be_visible()
        expect(page.get_by_label("Password")).to_have_count(0)  # built-in form is gone
        expect(page.get_by_label("Email")).to_have_count(0)
        page.request.get(BASE + "/api/auth/csrf")
        csrf = next(c["value"] for c in page.context.cookies() if c["name"] == "docgen_csrf")
        r = page.request.post(BASE + "/api/auth/login", data={"email": "a@b.co", "password": "x" * 12}, headers={"X-CSRF-Token": csrf})
        assert r.status == 404 and "OpenID Connect" in r.text(), (r.status, r.text())

        set_idp_user(page, sub="e2e-1", email="Ann@Example.com", name="Ann Owner", roles=["viewer"])  # first user owns the org whatever the role says
        sign_in(page)
        expect(page.get_by_role("heading", name=re.compile("Welcome back, Ann"))).to_be_visible()
        me = page.request.get(BASE + "/api/auth/me").json()
        assert me["user"]["email"] == "ann@example.com" and me["org"]["role"] == "owner", me  # first user owns the org
        log_out(page)
        assert page.request.get(BASE + "/api/auth/me").status == 401
        expect(page.get_by_text("You have been signed out.")).to_be_visible()
        assert not any(c["name"] == "idp_sso" for c in page.context.cookies()), "IdP SSO session must end with the logout"
        sign_in(page)  # after a logout the IdP asks for credentials again (the helper clicks its sign-in button)
        expect(page.get_by_role("heading", name=re.compile("Welcome back, Ann"))).to_be_visible()
        log_out(page)

        set_idp_user(page, sub="e2e-2", email="bob@example.com", name="Bob Viewer", roles=["viewer"])
        sign_in(page)
        expect(page.get_by_role("heading", name=re.compile("Welcome back, Bob"))).to_be_visible()
        assert page.request.get(BASE + "/api/auth/me").json()["org"]["role"] == "viewer"
        log_out(page)

        set_idp_user(page, sub="e2e-3", name="No Mail", roles=["viewer"])  # IdP releases no email
        sign_in(page)
        expect(page.get_by_role("alert")).to_contain_text("did not send an email address")
        expect(page).to_have_url(re.compile(r"/login\?error="))

        set_idp_user(page, sub="e2e-4", email="eve@example.com", name="Eve NoRole", roles=[])
        sign_in(page)  # no client role: refused with a readable message, nothing created
        expect(page.get_by_role("alert")).to_contain_text("Signed in as eve@example.com, but no DocGen role is assigned")
        assert page.request.get(BASE + "/api/auth/me").status == 401

        set_idp_user(page, sub="e2e-2", email="bob@example.com", name="Bob Viewer", roles=["Admin"], groups=["docgen-admins"])
        sign_in(page)  # role is applied at every login: viewer -> admin
        expect(page.get_by_role("heading", name=re.compile("Welcome back, Bob"))).to_be_visible()
        assert page.request.get(BASE + "/api/auth/me").json()["org"]["role"] == "admin"
        print("oidc e2e ok")
    finally:
        browser.close()
