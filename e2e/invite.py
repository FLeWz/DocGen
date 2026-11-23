"""Invite flow e2e against a running stack (E2E_BASE_URL, MAIL_URL = Mailpit UI, default http://localhost:8025):
sign up -> invite a second user -> invite mail visible in Mailpit -> accept (new account) -> create + switch orgs."""
import json
import os
import re
import secrets
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
MAIL = os.environ.get("MAIL_URL", "http://localhost:8025")
ART = os.environ.get("E2E_ARTIFACTS", "e2e-artifacts")
tag = secrets.token_hex(4)
owner, guest, pwd = f"e2e-{tag}@example.com", f"guest-{tag}@example.com", secrets.token_urlsafe(16)


def mail_to(addr):
    for _ in range(40):
        msgs = json.load(urllib.request.urlopen(f"{MAIL}/api/v1/messages"))["messages"]
        hit = next((m for m in msgs if addr in [t["Address"] for t in m["To"]]), None)
        if hit:
            return json.load(urllib.request.urlopen(f"{MAIL}/api/v1/message/{hit['ID']}"))["Text"]
        time.sleep(0.5)
    raise AssertionError(f"no mail for {addr} in Mailpit")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    a, b = browser.new_context().new_page(), browser.new_context().new_page()  # two browsers = two sessions
    try:
        a.goto(BASE)
        a.get_by_role("button", name="Need an account?").click()
        a.get_by_placeholder("Your name").fill("Owner")
        a.get_by_placeholder("Organization name").fill(f"E2E {tag}")
        a.get_by_placeholder("Email").fill(owner)
        a.get_by_placeholder("Password (min 10)").fill(pwd)
        a.get_by_role("button", name="Create account").click()
        expect(a.get_by_role("heading", name=f"E2E {tag}")).to_be_visible()

        a.get_by_placeholder("Email").last.fill(guest)
        a.get_by_role("button", name="Send invite").click()
        expect(a.get_by_text(f"{guest} (editor) – pending")).to_be_visible()

        body = mail_to(guest)
        token = re.search(r"token=(\S+)", body).group(1)
        assert "invited you to join" in body

        b.goto(f"{BASE}/accept-invite?token={token}")
        expect(b.get_by_role("heading", name=f"Join E2E {tag}")).to_be_visible()
        b.get_by_placeholder("Your name").fill("Guest")
        b.get_by_placeholder("Password (min 10)").fill(pwd)
        b.get_by_role("button", name="Create account & join").click()
        expect(b.get_by_role("heading", name=f"E2E {tag}")).to_be_visible()
        org = b.get_by_label("Organization")
        expect(org).to_have_value(re.compile(r".+"))
        expect(org.locator("option:checked")).to_have_text(f"E2E {tag} (editor)")

        b.get_by_placeholder("New organization").fill(f"Second {tag}")
        b.get_by_role("button", name="Create org").click()
        expect(b.get_by_role("heading", name=f"Second {tag}")).to_be_visible()
        org.select_option(label=f"E2E {tag} (editor)")
        expect(b.get_by_role("heading", name=f"E2E {tag}")).to_be_visible()
        a.reload()
        expect(a.get_by_text(f"{guest} (editor) – accepted")).to_be_visible()
        print("e2e invite OK")
    except Exception:
        os.makedirs(ART, exist_ok=True)
        a.screenshot(path=f"{ART}/invite-failure-a.png", full_page=True)
        b.screenshot(path=f"{ART}/invite-failure-b.png", full_page=True)
        raise
    finally:
        browser.close()
