"""Document builder e2e against a running stack:
  PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright .venv/bin/python e2e/docbuilder.py   (E2E_BASE_URL, default http://localhost:8080)
Snippet in folder A -> document in folder B pinned to v1 -> snippet gets v2 -> 'update available' -> bump -> download DOCX.
Logs in as a throwaway user created by scripts/e2e-users.sh (needs E2E_TAG, E2E_PASSWORD); leaves its org in the dev DB."""
import os
import secrets
import shutil
import subprocess
import zipfile

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
tag = (os.environ.get("E2E_TAG") or secrets.token_hex(4)) + "-docbuilder"  # user+org come from `make test-e2e-docker` (sign-up is closed)
email, password = f"e2e-{tag}@example.com", os.environ["E2E_PASSWORD"]
ART = os.environ.get("E2E_ARTIFACTS", "e2e-artifacts")


def type_into(page, text):
    """Type into the (only) editor; the first keystrokes are lost if the draft loads right after the click, so retry."""
    ed, prev = page.locator(".ProseMirror"), page.get_by_test_id("preview")
    for _ in range(5):
        ed.click()
        page.keyboard.type(text)
        try:
            expect(prev).to_contain_text(text, timeout=1500)
            return
        except AssertionError:
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
    raise AssertionError(f"could not type {text!r} into the editor")


def commit_folder(page, message):
    page.get_by_role("button", name="Commit…").click()
    page.get_by_role("checkbox").first.check()
    page.get_by_placeholder("Commit message").fill(message)
    page.get_by_role("button", name="Commit 1").click()
    expect(page.get_by_text("No changes.")).to_be_visible()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000}, accept_downloads=True)
    try:
        page.goto(BASE)
        page.get_by_placeholder("Email").fill(email)
        page.get_by_placeholder("Password (min 10)").fill(password)
        page.get_by_role("button", name="Log in").click()

        for name in ("Security", "Product"):  # siblings: nothing selected while creating
            page.once("dialog", lambda d, n=name: d.accept(n))
            page.get_by_role("button", name="+ Folder").click()
            expect(page.get_by_text(f"📁 {name}")).to_be_visible()

        # org settings: bundled default base document
        page.get_by_placeholder("Base document name").fill("Corporate")
        page.get_by_role("button", name="Upload (no file = bundled default)").click()
        expect(page.get_by_text("Corporate")).to_be_visible()

        # snippet in folder A, committed as v1
        page.get_by_text("📁 Security").click()
        page.get_by_placeholder("New snippet title").fill("Intro")
        page.get_by_role("button", name="Add snippet").click()
        ed = page.locator(".ProseMirror")
        expect(ed).to_be_visible()
        type_into(page, "Version one text")
        expect(page.get_by_test_id("save-status")).to_have_attribute("data-status", "saved")
        commit_folder(page, "v1")

        # document in folder B using the snippet from A
        page.get_by_text("📁 Product").click()
        page.get_by_role("button", name="documents").click()
        page.get_by_placeholder("New document title").fill("Manual")
        page.get_by_role("button", name="Add document").click()
        expect(page.get_by_label("Document title")).to_have_value("Manual")
        expect(page.get_by_test_id("lib-item")).to_have_count(1)
        page.get_by_role("button", name="Add Intro").click()
        blk = page.get_by_test_id("block")
        expect(blk).to_have_count(1)
        expect(blk).to_contain_text("Security")
        expect(blk).to_contain_text("Intro")
        expect(page.get_by_test_id("badge-update")).to_have_count(0)
        page.get_by_role("button", name="+ Heading").click()
        # drag & drop: move the heading above the snippet
        page.get_by_label("Drag handle").nth(1).drag_to(page.get_by_test_id("drop-zone").first)
        expect(page.get_by_test_id("block").first.get_by_label("Heading text")).to_be_visible()
        # drag from the library into the blocks list
        page.get_by_test_id("lib-item").first.drag_to(page.get_by_test_id("drop-zone").last)
        expect(page.get_by_test_id("block")).to_have_count(3)
        page.get_by_label("Remove block").last.click()
        expect(page.get_by_test_id("block")).to_have_count(2)
        expect(page.get_by_test_id("doc-save-status")).to_have_attribute("data-status", "saved")
        page.get_by_role("button", name="Preview").click()
        expect(page.get_by_test_id("preview")).to_contain_text("Version one text")
        commit_btn = page.get_by_role("button", name="Commit…")
        commit_btn.click()
        page.get_by_role("checkbox").first.check()
        page.get_by_placeholder("Commit message").fill("doc v1")
        page.get_by_role("button", name="Commit 1").click()
        expect(page.get_by_text("No changes.")).to_be_visible()

        # snippet gets v2 in folder A
        page.get_by_text("📁 Security").click()
        page.get_by_role("link", name="Intro").click()
        expect(page.locator(".ProseMirror")).to_have_attribute("contenteditable", "true")  # read-only until the draft is loaded
        page.locator(".ProseMirror").click()
        page.keyboard.press("Control+End")
        page.keyboard.type(" and version two")
        expect(page.get_by_test_id("save-status")).to_have_attribute("data-status", "saved")
        commit_folder(page, "v2")
        page.get_by_role("button", name="snippets").click()
        page.get_by_role("button", name="Close").click()
        page.get_by_role("link", name="Intro").click()
        expect(page.get_by_text("No committed document references")).to_have_count(0)  # used-by shows the doc
        expect(page.get_by_text("Manual", exact=False).first).to_be_visible()

        # back in the document: update available -> bump with diff
        page.get_by_text("📁 Product").click()
        page.get_by_role("button", name="documents").click()
        page.get_by_role("link", name="Manual").click()
        expect(page.get_by_test_id("badge-update")).to_be_visible()
        page.get_by_role("button", name="Bump…").click()
        expect(page.get_by_test_id("bump-diff")).to_contain_text("version two")
        page.get_by_role("button", name="Apply bump").click()
        expect(page.get_by_test_id("badge-update")).to_have_count(0)

        # variables: a declared variable without value/default blocks the export and is listed
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
        page.get_by_role("button", name="+ Row").first.click()
        page.get_by_label("team 1 name").fill("Ann")
        expect(page.get_by_label("team 1 qty")).to_be_visible()
        expect(page.get_by_test_id("revision-history").get_by_label("Revision 2 message")).to_have_value("doc v1")  # oldest first: v1, doc v1, v2
        expect(page.get_by_test_id("doc-save-status")).to_have_attribute("data-status", "saved")

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
