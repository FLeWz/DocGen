"""Editor smoke test against a running stack.
  PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright .venv/bin/python e2e/smoke.py   (E2E_BASE_URL, default http://localhost:8080)
Logs in as a throwaway user from scripts/e2e-users.sh (E2E_TAG, E2E_PASSWORD); leaves its org in the dev DB.
Covers: shell + routing (deep link, back, last folder), editor, variable dialog, commit screen, history, diff, restore."""
import os
import re

from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, commit_from_editor, editor_ready, login, new_folder, new_item, open_folder, open_item, saved, type_into, user

email, password, _ = user("smoke")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1500, "height": 1000})
    try:
        page.goto(BASE + "/")  # logged out: every route redirects to /login
        expect(page).to_have_url(re.compile("/login"))
        login(page, email, password)
        new_folder(page, "Docs")
        new_item(page, "snippet", "Intro")
        snippet_url = page.url
        ed, prev = editor_ready(page), page.get_by_test_id("preview")
        expect(page.get_by_role("checkbox")).to_have_count(0)  # nothing in the editor screen is a checkbox
        type_into(page, "Welcome Acme", end=False)

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
        saved(page)

        # deep link + browser back
        page.reload()
        expect(page).to_have_url(snippet_url)
        expect(editor_ready(page)).to_contain_text("Welcome")
        page.get_by_role("link", name="Dashboard").first.click()
        page.go_back()
        expect(editor_ready(page)).to_contain_text("Welcome")

        commit_from_editor(page, "Add intro")  # the snippet is preselected on the commit screen
        page.get_by_role("tab", name="History").click()
        expect(page.get_by_test_id("commit-row").filter(has_text="Add intro")).to_be_visible()

        # edit -> commit again -> history + diff -> restore the old version into the draft
        open_folder(page, "Docs")
        open_item(page, "Intro")
        type_into(page, " EDIT2")
        saved(page)
        commit_from_editor(page, "Second edit")
        page.get_by_role("tab", name="History").click()
        expect(page.get_by_test_id("commit-row")).to_have_count(2)
        open_folder(page, "Docs")
        open_item(page, "Intro")
        page.get_by_role("link", name="History").first.click()
        expect(page.get_by_test_id("diff-text")).to_contain_text("EDIT2")  # compare defaults to newest vs previous
        page.get_by_test_id("commit-row").nth(1).get_by_test_id("restore").click()  # newest first: row 2 = "Add intro"
        page.get_by_role("dialog").get_by_role("button", name="Restore to draft").click()
        expect(page).to_have_url(snippet_url)
        expect(page.get_by_test_id("preview")).to_contain_text("Welcome Acme")
        expect(page.get_by_test_id("preview")).not_to_contain_text("EDIT2")

        # dashboard remembers the last folder
        page.get_by_role("link", name="DocGen").click()
        expect(page.get_by_role("link", name="Continue in Docs")).to_be_visible()
        print("e2e smoke OK")
    except Exception:
        os.makedirs(ART, exist_ok=True)
        page.screenshot(path=f"{ART}/failure.png", full_page=True)
        raise
    finally:
        browser.close()
