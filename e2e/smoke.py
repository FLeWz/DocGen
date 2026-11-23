"""Editor smoke test against a running stack.
  PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright .venv/bin/python e2e/smoke.py   (E2E_BASE_URL, default http://localhost:8080)
Signs up a throwaway user with a random password; leaves its org behind in the dev DB."""
import os
import secrets
import sys

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
tag = secrets.token_hex(4)
email, password = f"e2e-{tag}@example.com", secrets.token_urlsafe(16)
ART = os.environ.get("E2E_ARTIFACTS", "e2e-artifacts")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1500, "height": 1000})
    try:
        page.goto(BASE)
        page.get_by_role("button", name="Need an account?").click()
        page.get_by_placeholder("Your name").fill("E2E User")
        page.get_by_placeholder("Organization name").fill(f"E2E {tag}")
        page.get_by_placeholder("Email").fill(email)
        page.get_by_placeholder("Password (min 10)").fill(password)
        page.get_by_role("button", name="Create account").click()

        page.once("dialog", lambda d: d.accept("Docs"))
        page.get_by_role("button", name="+ Folder").click()
        page.get_by_text("📁 Docs").click()
        page.get_by_placeholder("New snippet title").fill("Intro")
        page.get_by_role("button", name="Add snippet").click()

        ed, prev = page.locator(".ProseMirror"), page.get_by_test_id("preview")
        expect(ed).to_be_visible()
        for _ in range(5):  # the first keystrokes are lost if the draft loads right after the click: retry
            ed.click()
            page.keyboard.type("Welcome Acme")
            try:
                expect(prev).to_contain_text("Welcome Acme", timeout=1500)
                break
            except AssertionError:
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
        expect(prev).to_contain_text("Welcome Acme")

        page.get_by_label("Paragraph style").select_option("Heading1")
        expect(prev.locator("h1")).to_contain_text("Welcome Acme")
        expect(prev.locator("h1 .num")).to_have_text("1")
        page.keyboard.press("End")
        page.keyboard.press("Enter")  # new line after a heading is a Normal paragraph again
        page.keyboard.type("Client: Acme")

        for _ in range(4):  # select the word "Acme"
            page.keyboard.press("Shift+ArrowLeft")
        page.get_by_label("Make variable").click()
        page.get_by_label("Variable name").fill("client")
        page.get_by_role("button", name="Create variable").click()
        expect(prev.locator(".var-chip")).to_have_text("Acme")  # default = the selected text
        expect(ed.locator(".var-chip")).to_have_count(1)
        expect(page.get_by_text("Variables: client")).to_be_visible()

        expect(page.get_by_test_id("save-status")).to_have_attribute("data-status", "saved")
        page.get_by_role("button", name="Commit…").click()
        page.get_by_role("checkbox").check()
        page.get_by_placeholder("Commit message").fill("Add intro")
        page.get_by_role("button", name="Commit 1").click()
        expect(page.get_by_text("No changes.")).to_be_visible()
        page.get_by_role("button", name="history").click()
        expect(page.get_by_text("Add intro")).to_be_visible()

        # edit -> commit again -> history + diff -> restore the old version into the draft
        page.get_by_role("button", name="snippets", exact=True).click()
        expect(ed).to_be_visible()  # the editor stays open behind the commit tab
        for _ in range(5):
            ed.click()
            page.keyboard.press("Control+End")
            page.keyboard.type(" EDIT2")
            try:
                expect(prev).to_contain_text("EDIT2", timeout=1500)
                break
            except AssertionError:
                page.keyboard.press("Control+Z")
        expect(page.get_by_test_id("save-status")).to_have_attribute("data-status", "saved")
        page.get_by_role("button", name="Commit…").click()
        page.get_by_role("checkbox").check()
        page.get_by_placeholder("Commit message").fill("Second edit")
        page.get_by_role("button", name="Commit 1").click()
        expect(page.get_by_text("No changes.")).to_be_visible()
        page.get_by_role("button", name="snippets", exact=True).click()
        expect(page.get_by_text("Second edit")).to_be_visible()
        page.get_by_label("from", exact=True).nth(1).check()  # history is newest first: row 1 = "Add intro"
        page.get_by_label("to", exact=True).nth(0).check()
        expect(page.locator("pre")).to_contain_text("EDIT2")  # the diff
        page.get_by_role("button", name="Restore to draft").nth(1).click()
        expect(prev).not_to_contain_text("EDIT2")
        expect(prev).to_contain_text("Welcome Acme")
        print("e2e smoke OK")
    except Exception:
        os.makedirs(ART, exist_ok=True)
        page.screenshot(path=f"{ART}/failure.png", full_page=True)
        raise
    finally:
        browser.close()
