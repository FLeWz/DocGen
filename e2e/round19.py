"""Round 19 e2e: caption option when inserting tables/figures (dialog checkbox, numbering, no 'ARABIC' anywhere, checkbox absent from the
editor area), Insert caption for an existing table. Logs in as the throwaway user `round19`."""
import base64
import os
import re
import secrets

from playwright.sync_api import expect, sync_playwright

from common import ART, EDITOR, login, new_folder, new_item, saved, user

email, password, _ = user("round19")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
os.makedirs(ART, exist_ok=True)
png = os.path.join(ART, "r19.png")
open(png, "wb").write(PNG)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        login(page, email, password, prefs=False)
        new_folder(page, "R19" + secrets.token_hex(2))
        new_item(page, "snippet", "Captions")
        ed = page.locator(EDITOR)
        ed.wait_for()
        page.get_by_role("tab", name="Insert").click()
        # table: dialog, caption on by default (above), numbered 1
        page.get_by_label("Insert table with size").select_option("3x2")
        dlg = page.get_by_role("dialog")
        expect(dlg.get_by_label("Add caption")).to_be_checked()
        expect(dlg.get_by_label("Position")).to_have_value("above")
        dlg.get_by_label("Caption text").fill("Sizes")
        dlg.get_by_role("button", name="Insert", exact=True).click()
        expect(ed.locator("table")).to_have_count(1)
        expect(page.get_by_role("checkbox")).to_have_count(0)  # never in the editor area (dialog closed)
        cap = ed.locator("p", has_text="Sizes")
        expect(cap).to_have_text("Table 1: Sizes")
        expect(cap.locator(".fld-SEQ")).to_have_attribute("title", re.compile("Automatic number"))
        # the caption sits above the table
        assert ed.evaluate("e => { const c=[...e.querySelectorAll('p')].find(p=>p.textContent.includes('Sizes')); return c.nextElementSibling.tagName }") in ("TABLE", "DIV")
        # second table: choice remembered, numbered 2, caption off for the third
        page.get_by_role("tab", name="Insert").click()
        page.get_by_label("Insert table with size").select_option("2x2")
        dlg = page.get_by_role("dialog")
        dlg.get_by_label("Caption text").fill("More")
        dlg.get_by_role("button", name="Insert", exact=True).click()
        expect(ed.locator("p", has_text="More")).to_have_text("Table 2: More")
        page.get_by_role("tab", name="Insert").click()
        page.get_by_label("Insert table with size").select_option("2x2")
        page.get_by_role("dialog").get_by_label("Add caption").uncheck()
        page.get_by_role("dialog").get_by_role("button", name="Insert", exact=True).click()
        expect(ed.locator("table")).to_have_count(3)
        # figure: upload -> dialog; caption off was remembered, switch it on, below, custom label
        page.get_by_role("tab", name="Insert").click()
        page.get_by_label("Image file").set_input_files(png)
        dlg = page.get_by_role("dialog")
        expect(dlg.get_by_label("Add caption")).not_to_be_checked()
        dlg.get_by_label("Add caption").check()
        expect(dlg.get_by_label("Position")).to_have_value("below")
        dlg.get_by_label("Label").fill("Figure")
        dlg.get_by_label("Caption text").fill("Dot")
        dlg.get_by_role("button", name="Insert", exact=True).click()
        expect(ed.locator("p", has_text="Dot")).to_have_text("Figure 1: Dot")
        # a lone image gets a caption through References > Insert caption (existing block)
        saved(page)
        text = page.locator(".docx").first.inner_text()
        assert "ARABIC" not in text and "SEQ" not in text, text
        page.reload()
        ed = page.locator(EDITOR)
        expect(ed.locator("p", has_text="Dot")).to_have_text("Figure 1: Dot")
        expect(ed.locator("p", has_text="Sizes")).to_have_text("Table 1: Sizes")
        print("round19 ok")
    finally:
        browser.close()
