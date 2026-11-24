"""Document builder e2e against a running stack:
  PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright .venv/bin/python e2e/docbuilder.py   (E2E_BASE_URL, default http://localhost:8080)
Snippet in folder A -> document in folder B pinned to v1 -> snippet gets v2 -> 'update available' -> bump -> download DOCX/PDF.
Logs in as a throwaway user created by scripts/e2e-users.sh (needs E2E_TAG, E2E_PASSWORD); leaves its org in the dev DB."""
import os
import re
import shutil
import subprocess
import zipfile

from playwright.sync_api import expect, sync_playwright

from common import ART, commit, commit_from_editor, editor_ready, login, new_folder, new_item, open_folder, open_item, saved, type_into, user

email, password, _ = user("docbuilder")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000}, accept_downloads=True)
    try:
        login(page, email, password)
        for name in ("Security", "Product"):
            new_folder(page, name)

        # org settings: a base document (bundled default)
        page.get_by_role("link", name="Base documents").click()
        page.get_by_role("button", name="New base document").click()
        page.get_by_label("Base document name").fill("Corporate")
        page.get_by_label("Source").select_option("default")
        page.get_by_role("button", name="Create", exact=True).click()
        expect(page.get_by_role("cell", name="Corporate", exact=True)).to_be_visible()

        # snippet in folder A, committed as v1
        open_folder(page, "Security")
        new_item(page, "snippet", "Intro")
        type_into(page, "Version one text", end=False)
        saved(page)
        commit_from_editor(page, "v1")

        # document in folder B using the snippet from A
        open_folder(page, "Product")
        new_item(page, "document", "Manual")
        expect(page.get_by_label("Document title")).to_have_value("Manual")
        expect(page.get_by_test_id("lib-item")).to_have_count(1)
        page.get_by_role("button", name="Add Intro").click()
        blk = page.get_by_test_id("block")
        expect(blk).to_have_count(1)
        expect(blk).to_contain_text("Security")
        expect(blk).to_contain_text("Intro")
        expect(page.get_by_test_id("badge-update")).to_have_count(0)
        page.get_by_role("button", name="Heading", exact=True).click()
        expect(blk).to_have_count(2)
        # drag & drop: move the heading above the snippet
        page.get_by_label("Drag handle").nth(1).drag_to(page.get_by_test_id("drop-zone").first)
        expect(page.get_by_test_id("block").first.get_by_label("Heading text")).to_be_visible()
        # drag from the library into the blocks list
        page.get_by_test_id("lib-item").first.drag_to(page.get_by_test_id("drop-zone").last)
        expect(page.get_by_test_id("block")).to_have_count(3)
        page.get_by_label("Remove block").last.click()
        expect(page.get_by_test_id("block")).to_have_count(2)
        saved(page, "doc-save-status")
        page.get_by_role("button", name="Preview").click()  # default: the real PDF render, inline from a blob URL
        pdfv = page.get_by_role("dialog").get_by_test_id("word-pdf")
        expect(pdfv).to_be_visible(timeout=120_000)
        assert pdfv.get_attribute("src").startswith("blob:")
        page.get_by_role("tab", name=re.compile("Draft view")).click()
        expect(page.get_by_role("dialog").get_by_test_id("preview")).to_contain_text("Version one text")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Commit…").click()
        commit(page, "doc v1", "Manual")

        # snippet gets v2 in folder A
        open_folder(page, "Security")
        open_item(page, "Intro")
        type_into(page, " and version two")
        saved(page)
        commit_from_editor(page, "v2")
        open_folder(page, "Security")
        open_item(page, "Intro")
        editor_ready(page)
        page.get_by_role("tab", name="Used by").click()  # right panel: the document pins it
        expect(page.get_by_role("link", name="Manual")).to_be_visible()

        # back in the document: update available -> bump with diff
        open_folder(page, "Product")
        open_item(page, "Manual")
        expect(page.get_by_test_id("badge-update")).to_be_visible()
        page.get_by_role("button", name="Bump…").click()
        expect(page.get_by_test_id("bump-diff")).to_contain_text("version two")
        page.get_by_role("button", name="Apply bump").click()
        expect(page.get_by_test_id("badge-update")).to_have_count(0)

        # variables: a declared variable without value/default blocks the export and is listed as a checklist
        page.get_by_placeholder("New variable name").fill("owner")
        page.get_by_role("button", name="Add", exact=True).click()
        page.get_by_role("button", name="Download DOCX").click()
        expect(page.get_by_test_id("missing-vars")).to_contain_text("owner")
        page.get_by_label("Value of owner").fill("Zed")
        # list variable with columns and rows (the editor of list-of-objects values)
        page.get_by_placeholder("New variable name").fill("team")
        page.get_by_label("New variable type").select_option("list")
        page.get_by_role("button", name="Add", exact=True).click()
        page.get_by_label("Columns of team").fill("name, qty")
        page.get_by_label("Columns of team").blur()
        page.get_by_role("button", name="Row", exact=True).first.click()
        page.get_by_label("team 1 name").fill("Ann")
        expect(page.get_by_label("team 1 qty")).to_be_visible()
        page.get_by_text("Revision history", exact=True).click()  # accordion
        expect(page.get_by_test_id("revision-history").get_by_label("Revision 2 message")).to_have_value("doc v1")  # oldest first: v1, doc v1, v2
        saved(page, "doc-save-status")

        # download DOCX (background job, UI polls)
        with page.expect_download(timeout=60_000) as dl:
            page.get_by_role("button", name="Download DOCX").click()
        path = os.path.join(ART, "manual.docx")
        os.makedirs(ART, exist_ok=True)
        dl.value.save_as(path)
        xml = zipfile.ZipFile(path).read("word/document.xml").decode()
        assert "Version one text and version two" in xml, "bumped content missing in DOCX"
        assert "{{ CONTENT }}" not in xml
        assert "doc v1" in xml, "revision history row missing in DOCX"  # default base, filled from the git log

        # download PDF through the same job flow (Gotenberg)
        with page.expect_download(timeout=120_000) as dl:
            page.get_by_role("button", name="Download PDF").click()
        pdf = os.path.join(ART, "manual.pdf")
        dl.value.save_as(pdf)
        assert open(pdf, "rb").read(5) == b"%PDF-", "not a PDF"
        if shutil.which("pdftotext"):
            txt = subprocess.run(["pdftotext", pdf, "-"], capture_output=True, text=True, check=True).stdout
            assert "Version one text and version two" in txt and "doc v1" in txt, txt[:500]
        print("e2e docbuilder OK")
    except Exception:
        os.makedirs(ART, exist_ok=True)
        page.screenshot(path=f"{ART}/docbuilder-failure.png", full_page=True)
        raise
    finally:
        browser.close()
