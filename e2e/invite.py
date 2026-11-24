"""Invite flow e2e against a running stack (E2E_BASE_URL, MAIL_URL = Mailpit UI, default http://localhost:8025):
log in -> invite a second user -> invite mail visible in Mailpit -> accept (new account) -> create + switch orgs."""
import json
import os
import re
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, login, user

MAIL = os.environ.get("MAIL_URL", "http://localhost:8025")
owner, pwd, org_name = user("invite")
tag = owner.split("e2e-")[1].split("@")[0]
guest = f"guest-{tag}@example.com"


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
        login(a, owner, pwd)
        a.get_by_role("link", name="Settings").click()
        a.get_by_role("tab", name="Invites").click()
        a.get_by_label("Email").fill(guest)
        a.get_by_role("button", name="Send invite").click()
        row = a.get_by_test_id("invites").get_by_role("row").filter(has_text=guest)
        expect(row).to_contain_text("pending")
        expect(row).to_contain_text(re.compile(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}"))  # round 11: DD.MM.YYYY HH:mm

        body = mail_to(guest)
        token = re.search(r"token=([\w\-.]+)", body).group(1)
        assert "invited you to join" in body

        b.goto(f"{BASE}/accept-invite?token={token}")
        expect(b.get_by_role("heading", name=f"Join {org_name}")).to_be_visible()
        b.get_by_label("Your name").fill("Guest")
        b.get_by_label("Password").fill(pwd)
        b.get_by_role("button", name="Create account & join").click()
        expect(b.get_by_role("heading", name=re.compile("Welcome back"))).to_be_visible()
        org = b.get_by_role("button", name=f"Organization: {org_name}")
        expect(org).to_contain_text("editor")

        b.get_by_role("button", name="Organization:").click()
        b.get_by_role("menuitem", name="New organization…").click()
        b.get_by_label("Organization name").fill(f"Second {tag}")
        b.get_by_role("button", name="Create", exact=True).click()
        expect(b.get_by_role("button", name=f"Organization: Second {tag}")).to_be_visible()
        b.get_by_role("button", name="Organization:").click()
        b.get_by_role("menuitem", name=f"{org_name} (editor)").click()
        expect(b.get_by_role("button", name=f"Organization: {org_name}")).to_be_visible()
        a.reload()
        expect(a.get_by_test_id("invites").get_by_role("row").filter(has_text=guest)).to_contain_text("accepted")
        print("e2e invite OK")
    except Exception:
        os.makedirs(ART, exist_ok=True)
        a.screenshot(path=f"{ART}/invite-failure-a.png", full_page=True)
        b.screenshot(path=f"{ART}/invite-failure-b.png", full_page=True)
        raise
    finally:
        browser.close()
