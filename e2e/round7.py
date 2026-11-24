"""Round 7 e2e against a running stack (E2E_BASE_URL): modal widths, header/footer editor without horizontal scroll (1100 and 1440 px),
'Make dynamic table' (Insert tab, snippet and import editors, row.field chips), paragraph/list blocks in the document builder and the
document preview shown as the real PDF (blob, application/pdf, cached on the second open, missing-variable checklist).
Logs in as the throwaway user of scripts/e2e-users.sh (`round7`)."""
import os
import re
import shutil
import subprocess

from playwright.sync_api import expect, sync_playwright

from common import ART, commit_from_editor, editor_ready, login, new_folder, new_item, open_folder, saved, type_into, user

email, password, _ = user("round7")
os.makedirs(ART, exist_ok=True)
cover = os.path.join(ART, "cover.docx")
if os.environ.get("E2E_COVER_DOCX"):
    shutil.copy(os.environ["E2E_COVER_DOCX"], cover)
else:
    with open(cover, "wb") as f:
        f.write(subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "tests.fixtures.cover", "-"],
                               capture_output=True, check=True).stdout)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    try:
        login(page, email, password)
        new_folder(page, "R7")
        page.get_by_role("link", name="Imports").click()
        page.get_by_label("DOCX file").set_input_files(cover)
        expect(page).to_have_url(re.compile("/imports/"))
        ed = editor_ready(page)

        # --- Make dynamic table in the import editor: Insert tab, enabled only inside a table
        page.get_by_role("tab", name="Insert").click()
        dyn = page.get_by_role("button", name=re.compile("Make dynamic table"))
        ed.get_by_text("plain table text").first.click()
        expect(page.get_by_role("tab", name="Table")).to_have_attribute("aria-selected", "true")  # round 8: the Table tab follows the cursor
        page.get_by_role("tab", name="Insert").click()
        expect(dyn).to_be_enabled()
        ed.get_by_text("This body text stays editable content.").click()
        expect(dyn).to_be_disabled()

        # --- header/footer editor: no horizontal scroll at 1100 and 1440 px (stage, dialog, hf box)
        for w in (1440, 1100):
            page.set_viewport_size({"width": w, "height": 1000})
            hdr = page.locator(".pane .docx-hdr").first
            hdr.scroll_into_view_if_needed()
            bb = hdr.bounding_box()
            page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            expect(page.get_by_test_id("hf-editor")).to_be_visible()
            m = page.evaluate("""() => { const f = (s) => { const e = document.querySelector(s); return e ? [e.scrollWidth, e.clientWidth] : null; };
                return { stage: f('.pane .docx-stage'), dialog: f('dialog.modal[open]'), box: f('.hfbox'), pm: f('.hfbox .ProseMirror'), page: f('html') }; }""")
            for k, v in m.items():
                assert v and v[0] <= v[1] + 1, (w, k, m)
            page.get_by_role("button", name="Done").click()
        page.set_viewport_size({"width": 1440, "height": 1000})

        # --- Use as base document: a properly sized modal with the kept/replaced preview
        ed.get_by_text("More body.").click()
        page.keyboard.press("End")
        page.keyboard.press("Enter")
        page.keyboard.press("Control+Alt+c")
        saved(page)
        page.get_by_role("button", name="Use as base document").click()
        dlg = page.locator("dialog[open]")
        assert dlg.bounding_box()["width"] >= 700, dlg.bounding_box()
        expect(page.get_by_test_id("base-keep")).to_contain_text("Replaced by")
        page.screenshot(path=f"{ART}/round7-usebase.png")
        page.get_by_label("Base document name").fill("R7 base")
        page.get_by_role("button", name="Save as base document").click()
        expect(page.get_by_text('Saved as base document "R7 base"')).to_be_visible()

        # --- Make dynamic table in a snippet: dialog, row.field chips, outline badge, insert-variable chips, make static again
        open_folder(page, "R7")
        new_item(page, "snippet", "Team")
        editor_ready(page)
        page.wait_for_load_state("networkidle")
        type_into(page, "Team table", end=False)
        page.get_by_role("tab", name="Insert").click()
        page.get_by_label("Insert table with size").select_option("3x2")
        tbl = page.locator(".ProseMirror table")
        expect(tbl).to_have_count(1)
        page.get_by_role("tab", name="Insert").click()  # (the cursor is in the new table: the Table tab was selected)
        page.get_by_role("button", name=re.compile("Make dynamic table")).click()
        page.get_by_role("textbox", name="List variable").fill("team")
        page.get_by_role("textbox", name="Row fields (optional)").fill("name, email")
        page.get_by_role("button", name="Make dynamic", exact=True).click()
        expect(page.locator(".ProseMirror table[data-dynamic-list=team]")).to_have_count(1)
        last = tbl.locator("tr").last
        expect(last.locator(".var-chip")).to_have_text(["{{ row.name }}", "{{ row.email }}"])
        assert "dynamic: team" in page.evaluate("() => getComputedStyle(document.querySelector('.ProseMirror table[data-dynamic-list]'), '::before').content")
        # the insert-variable dialog offers the row fields while the cursor is in the dynamic table
        last.locator("td").first.click()
        page.get_by_role("tab", name="Home").click()
        page.get_by_role("button", name="Make variable").click()
        expect(page.get_by_test_id("row-chips")).to_contain_text("row.email")
        page.get_by_role("button", name="Cancel").click()
        page.get_by_role("tab", name="Insert").click()
        page.get_by_role("button", name=re.compile("Make dynamic table")).click()  # empty name = static again
        page.get_by_role("textbox", name="List variable").fill("")
        page.get_by_role("button", name="Make static").click()
        expect(page.locator(".ProseMirror table[data-dynamic-list]")).to_have_count(0)
        saved(page)
        commit_from_editor(page, "team table")

        # --- builder: paragraph and list blocks, PDF preview
        open_folder(page, "R7")
        new_item(page, "document", "Report")
        page.get_by_label("Base document", exact=True).select_option(label="R7 base")
        page.get_by_role("button", name="Paragraph", exact=True).click()
        para = page.get_by_test_id("para-editor").locator(".ProseMirror")
        para.click()
        page.keyboard.type("Typed straight into the document")
        page.get_by_label("Paragraph style").first.select_option("Heading1") if page.get_by_label("Paragraph style").first.locator("option[value=Heading1]").count() else None
        page.get_by_role("button", name="List of tables", exact=True).click()
        page.get_by_role("button", name="Add Team").click()
        expect(page.get_by_test_id("block")).to_have_count(3)
        saved(page, "doc-save-status")
        page.screenshot(path=f"{ART}/round7-builder.png")
        page.get_by_role("button", name="Preview").click()
        pdfv = page.get_by_role("dialog").get_by_test_id("word-pdf")
        expect(pdfv).to_be_visible(timeout=120_000)
        src = pdfv.get_attribute("src")
        assert src.startswith("blob:"), src
        page.screenshot(path=f"{ART}/round7-preview.png")
        assert page.evaluate("s => fetch(s.split('#')[0]).then(r => r.blob()).then(b => b.type)", src) == "application/pdf"
        page.get_by_role("tab", name=re.compile("Draft view")).click()  # the fast HTML view stays available
        expect(page.get_by_role("dialog").get_by_test_id("preview")).to_contain_text("Typed straight into the document")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Preview").click()  # unchanged document: the render is reused
        expect(page.get_by_test_id("pdf-cached")).to_be_visible(timeout=120_000)
        page.keyboard.press("Escape")

        # a declared variable without value: the 422 checklist is shown in the preview
        page.get_by_placeholder("New variable name").fill("owner")
        page.get_by_role("button", name="Add", exact=True).click()
        page.get_by_role("button", name="Preview").click()
        expect(page.get_by_test_id("preview-missing")).to_contain_text("owner", timeout=60_000)
        page.keyboard.press("Escape")
        page.get_by_label("Value of owner").fill("Zed")
        saved(page, "doc-save-status")

        with page.expect_download(timeout=120_000) as dl:
            page.get_by_role("button", name="Download PDF").click()
        pdf = os.path.join(ART, "round7-report.pdf")
        dl.value.save_as(pdf)
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() for p in PdfReader(pdf).pages)
        assert "Typed straight into the document" in text and "List of tables" in text, text[:600]
        print("e2e round7 OK")
    except Exception:
        page.screenshot(path=f"{ART}/round7-failure.png", full_page=True)
        raise
    finally:
        browser.close()
