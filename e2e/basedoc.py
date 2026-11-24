"""Base document e2e against a running stack (E2E_BASE_URL, default http://localhost:8080):
import a DOCX with a cover page, header/footer fields and a section break -> quick action {{ CONTENT }} (Ctrl+Alt+C) ->
Find variables modal (names edited) -> use as base document -> variables of the base under Settings -> document built on the base
(base variable overridden) -> export DOCX and PDF: base applied (cover, sections, header/footer images), values replaced, no
'Error! Reference source not found'.  Logs in as a throwaway user from scripts/e2e-users.sh."""
import io
import os
import re
import shutil
import subprocess
import zipfile

from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, commit_from_editor, editor_ready, login, new_folder, new_item, open_folder, saved, type_into, user

email, password, _ = user("basedoc")
os.makedirs(ART, exist_ok=True)
cover = os.path.join(ART, "cover.docx")
if os.environ.get("E2E_COVER_DOCX"):
    shutil.copy(os.environ["E2E_COVER_DOCX"], cover)
else:
    with open(cover, "wb") as f:
        f.write(subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "tests.fixtures.cover", "-"],
                               capture_output=True, check=True).stdout)


def part(path, name):
    return zipfile.ZipFile(path).read(name).decode()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000}, accept_downloads=True)
    try:
        login(page, email, password)
        new_folder(page, "Specs")
        page.get_by_role("link", name="Imports").click()
        page.get_by_label("DOCX file").set_input_files(cover)
        expect(page).to_have_url(re.compile("/imports/"))
        ed = editor_ready(page)
        expect(ed.get_by_text("This body text stays editable content.")).to_be_visible()

        # quick action: Ctrl+Alt+C inserts a {{ CONTENT }} paragraph at the cursor; the badge follows; deleting the text takes it away again
        badge = page.get_by_test_id("slot-badge")
        expect(badge).to_contain_text("no {{ CONTENT }}")
        ed.get_by_text("More body.").click()
        page.keyboard.press("End")
        page.keyboard.press("Enter")
        page.keyboard.press("Control+Alt+c")
        expect(badge).to_contain_text("{{ CONTENT }} set")
        expect(ed.locator("p[data-slot=CONTENT]")).to_have_count(1)
        page.keyboard.press("Control+Alt+c")  # a second one is refused
        expect(page.get_by_text("already has a {{ CONTENT }} paragraph")).to_be_visible()
        page.keyboard.press("Home")  # remove it again (undo would also undo the load)
        page.keyboard.press("Shift+End")
        page.keyboard.press("Backspace")
        expect(badge).to_contain_text("no {{ CONTENT }}")
        saved(page)

        # Find variables: candidates are values, not structure; names are editable
        page.get_by_role("button", name="Find variables").click()
        tbl = page.get_by_test_id("var-candidates")
        expect(tbl).to_be_visible()
        expect(tbl.locator("tbody tr").first).to_be_visible()
        rows = [r.replace("\n", " | ") for r in tbl.locator("tbody tr").all_inner_texts()]
        assert len(rows) == 5, rows  # title, version, subtitle, dead header REF, footer TITLE field
        expect(page.get_by_label("Name for Acme Spec")).to_have_value("doc.title")
        expect(page.get_by_label("Name for 1.2")).to_have_value("doc.version")
        page.get_by_label("Use Project Falcon").check()  # plain text is off by default
        page.get_by_label("Name for Project Falcon").fill("subtitle")
        expect(tbl).not_to_contain_text("plain table text")
        page.get_by_role("button", name="Replace with variables").click()
        expect(page.get_by_text("Replaced")).to_be_visible()
        page.wait_for_load_state("load")
        ed = editor_ready(page)

        # use as base document (admin): cover/TOC/sections stay, the body from Heading 1 on becomes {{ CONTENT }}
        page.get_by_role("button", name="Use as base document").click()
        page.get_by_label("Base document name").fill("Cover base")
        page.get_by_role("button", name="Save as base document").click()
        expect(page.get_by_text('Saved as base document "Cover base"')).to_be_visible()

        # settings: the base document's variables (subtitle, default = detected value) are editable under the base document
        page.get_by_role("link", name="Base documents").click()
        expect(page.get_by_text("CONTENT").first).to_be_visible()
        page.get_by_role("button", name="Details of Cover base").click()
        bv = page.get_by_test_id("base-doc-vars")
        expect(bv).to_contain_text("subtitle")
        expect(bv.get_by_label("Value of subtitle")).to_be_visible()
        expect(bv.get_by_label("Value of subtitle")).to_have_value("Project Falcon")

        # a snippet and a document on that base
        open_folder(page, "Specs")
        new_item(page, "snippet", "Chapter")
        page.wait_for_load_state("networkidle")  # the editor reloads the draft once the base document's look arrived: typing earlier is wiped
        type_into(page, "Chapter text here", end=False)
        saved(page)
        commit_from_editor(page, "v1")
        open_folder(page, "Specs")
        new_item(page, "document", "Report")
        page.get_by_label("Base document", exact=True).select_option(label="Cover base")
        bvars = page.get_by_test_id("base-vars")
        expect(bvars.get_by_label("Value of subtitle")).to_have_attribute("placeholder", re.compile("Project Falcon"))
        bvars.get_by_label("Value of subtitle").fill("Eagle")
        page.get_by_role("button", name="Heading", exact=True).click()
        page.get_by_label("Heading text").fill("First chapter")
        page.get_by_role("button", name="Add Chapter").click()
        saved(page, "doc-save-status")
        with page.expect_download(timeout=60_000) as dl:
            page.get_by_role("button", name="Download DOCX").click()
        out = os.path.join(ART, "basedoc-report.docx")
        dl.value.save_as(out)
        doc = part(out, "word/document.xml")
        z = zipfile.ZipFile(out)
        assert doc.count("<w:sectPr") == 2 and "titlePg" in doc, "sections of the base lost"
        assert {"word/header1.xml", "word/header2.xml", "word/footer1.xml", "word/media/logo.png"} <= set(z.namelist())
        text = re.sub(r"<[^>]+>", "", doc)
        assert "{{" not in text and "Report" in text and "Eagle" in text and "Chapter text here" in text and "First chapter" in text, text[:400]
        assert "Acme Spec" not in text and "Project Falcon" not in text
        assert "Report" in re.sub(r"<[^>]+>", "", part(out, "word/footer1.xml")), "TITLE field in the footer not filled"
        assert "REF Gone_Bookmark" not in part(out, "word/header2.xml")
        assert "DocTitle" in doc  # bookmark of the cover title kept, so the header REF resolves

        with page.expect_download(timeout=120_000) as dl:
            page.get_by_role("button", name="Download PDF").click()
        pdf = os.path.join(ART, "basedoc-report.pdf")
        dl.value.save_as(pdf)
        from pypdf import PdfReader
        pt = "\n".join(p.extract_text() for p in PdfReader(pdf).pages)
        assert "Error!" not in pt and "Report" in pt and "Eagle" in pt and "Chapter text here" in pt, pt[:500]
        # management: unique names (case-insensitive) -> "create new version?" dialog, rename, delete refused while a document uses it
        page.get_by_role("link", name="Base documents").click()
        page.get_by_role("button", name="New base document").click()
        page.get_by_label("Base document name").fill("cover BASE")
        page.get_by_label("Source").select_option("default")
        page.get_by_role("button", name="Create", exact=True).click()
        expect(page.get_by_test_id("name-conflict")).to_contain_text("A base document named Cover base already exists")
        expect(page.get_by_role("button", name="Create new version")).to_have_count(0)  # default template has no file to version with
        page.get_by_role("button", name="Choose another name").click()
        page.get_by_label("Base document name").fill("Spare")
        page.get_by_role("button", name="Create", exact=True).click()
        expect(page.get_by_test_id("base-row").filter(has_text="Spare")).to_be_visible()
        page.get_by_role("button", name="Rename Spare").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Cover Base")  # clash again: error toast, nothing renamed
        page.get_by_role("button", name="Rename", exact=True).click()
        expect(page.get_by_text("already exists")).to_be_visible()
        page.get_by_role("button", name="Rename Spare").click()
        page.get_by_role("textbox", name="Name", exact=True).fill("Spare 2")
        page.get_by_role("button", name="Rename", exact=True).click()
        expect(page.get_by_test_id("base-row").filter(has_text="Spare 2")).to_be_visible()
        row = page.get_by_test_id("base-row").filter(has_text="Cover base")
        expect(row.locator("td").nth(4)).to_have_text("1")  # the Report document is pinned to it
        page.get_by_role("button", name="Delete Cover base").click()
        expect(page.get_by_test_id("in-use")).to_contain_text("Report")
        expect(page.get_by_role("button", name="Delete base document")).to_be_disabled()
        page.get_by_role("button", name="Cancel").click()
        page.get_by_role("button", name="Delete Spare 2").click()
        page.get_by_role("button", name="Delete base document").click()
        expect(page.get_by_test_id("base-row").filter(has_text="Spare 2")).to_have_count(0)
        # side menu: Base documents and Styles sit under Imports; old settings URLs redirect
        nav = page.get_by_label("Navigation")
        assert [t.strip() for t in nav.locator(".navlinks a").all_inner_texts()][:5] == ["Dashboard", "Imports", "Base documents", "Styles", "Settings"]
        page.goto(BASE + "/settings/styles")
        expect(page).to_have_url(re.compile("/styles$"))
        expect(page.get_by_role("heading", name="Styles", level=1)).to_be_visible()
        page.set_viewport_size({"width": 1100, "height": 800})
        page.get_by_role("link", name="Base documents").click()
        expect(page.get_by_test_id("base-docs")).to_be_visible()
        print("e2e basedoc OK")
    except Exception:
        page.screenshot(path=f"{ART}/basedoc-failure.png", full_page=True)
        raise
    finally:
        browser.close()
