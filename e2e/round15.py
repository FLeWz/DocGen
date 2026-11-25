"""Round 15 (AUTH_MODE=local): a user created by the CLI with a CRLF-terminated password (Windows shell) logs in with
that password through the UI; wrong passwords and the lockout give different messages."""
import re

from playwright.sync_api import expect, sync_playwright

from common import BASE, login, user

email, pwd, org = user("round15")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_context().new_page()
    try:
        login(page, email.upper(), pwd)  # CLI stripped the CR; email case does not matter
        page.get_by_role("button", name="User menu").click()
        page.get_by_role("menuitem", name="Log out").click()
        expect(page.get_by_role("button", name="Log in")).to_be_visible()
        for i in range(5):  # wrong password: plain error each time
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("definitely-wrong-pw")
            page.get_by_role("button", name="Log in").click()
            expect(page.get_by_role("alert")).to_contain_text("invalid email or password")
        page.get_by_label("Password").fill(pwd)  # right password, but locked out: says so, not 'wrong password'
        page.get_by_role("button", name="Log in").click()
        expect(page.get_by_role("alert")).to_contain_text(re.compile(r"too many failed login attempts.*try again in \d+ min"))
        assert page.request.get(BASE + "/api/auth/config").json()["mode"] == "local"
        print("round15 ok")
    finally:
        browser.close()
