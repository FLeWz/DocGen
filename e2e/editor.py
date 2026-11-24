"""Editor round 5B e2e against a running stack (E2E_BASE_URL): selections for snippets across page gaps and of hundreds of blocks
(long synthetic DOCX: 600+ paragraphs, 50+ pages, a section break, tables, an image, text boxes), 'Select blocks' mode, ribbon tabs,
header/footer editing visible on every page and in the re-exported DOCX, mark/unmark a table-of-contents section.
Logs in as the throwaway user of scripts/e2e-users.sh (`editor`)."""
import os
import re
import shutil
import subprocess
import zipfile

from playwright.sync_api import expect, sync_playwright

from common import ART, editor_ready, login, new_folder, saved, user

email, password, _ = user("editor")
os.makedirs(ART, exist_ok=True)
long_docx = os.path.join(ART, "long.docx")
if os.environ.get("E2E_LONG_DOCX"):
    shutil.copy(os.environ["E2E_LONG_DOCX"], long_docx)
else:
    with open(long_docx, "wb") as f:
        f.write(subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "tests.fixtures.long", "-", "600"],
                               capture_output=True, check=True).stdout)


def part(path, name):
    return zipfile.ZipFile(path).read(name).decode()


def count(page):
    return int(re.match(r"(\d+)", page.get_by_test_id("sel-count").inner_text() or "0").group(1) or 0) if page.get_by_test_id("sel-count").inner_text() else 0


def make_snippet(page, title):
    """Toolbar button -> dialog -> create; returns the number of blocks the dialog announced."""
    page.get_by_role("button", name="Create snippet from selection").click()
    n = int(re.match(r"(\d+) block", page.locator("dialog p.muted").first.inner_text()).group(1))
    page.get_by_label("Snippet title").fill(title)
    page.get_by_role("button", name="Create snippet", exact=True).click()
    expect(page.get_by_text(f'Created snippet draft "{title}"')).to_be_visible()
    return n


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000}, accept_downloads=True)
    try:
        login(page, email, password)
        new_folder(page, "Long")
        page.get_by_role("link", name="Imports").click()
        page.get_by_label("DOCX file").set_input_files(long_docx)
        expect(page).to_have_url(re.compile("/imports/"))
        ed = editor_ready(page)
        expect(ed.locator(".pgsep").first).to_be_attached(timeout=20_000)
        gaps = page.locator(".pgsep").count()
        assert gaps >= 20, f"expected a long document, got {gaps} page gaps"

        # 1. drag from the last line of a page to the first line of the next one
        info = page.evaluate("""() => { const g = document.querySelectorAll('.pgsep')[2], r = (e) => { const x = e.getBoundingClientRect(); return [x.left, x.top, x.bottom]; };
          g.scrollIntoView({block: 'center'}); return [r(g.previousElementSibling), r(g.nextElementSibling)]; }""")
        (a, b) = page.evaluate("""() => { const g = document.querySelectorAll('.pgsep')[2], r = (e) => { const x = e.getBoundingClientRect(); return [x.left, x.top, x.bottom]; };
          return [r(g.previousElementSibling), r(g.nextElementSibling)]; }""")
        page.mouse.move(a[0] + 60, a[2] - 8); page.mouse.down(); page.mouse.move(b[0] + 120, b[1] + 10, steps=15); page.mouse.up()
        expect(page.get_by_test_id("sel-count")).to_contain_text("2 blocks selected")
        assert make_snippet(page, "Across a page gap") == 2

        # 2. select all (Ctrl+A in the editor) = every block; also with the focus outside the editor
        ed.click(); page.keyboard.press("Control+a")
        total = count(page)
        assert total > 600, total
        assert make_snippet(page, "Everything") == total
        page.get_by_label("Zoom").focus(); page.keyboard.press("Control+a")  # focus on the page chrome, not in the editor
        assert count(page) == total
        page.keyboard.press("Escape")

        # 3. click, scroll 400 paragraphs further, Shift+click: hundreds of blocks over many page gaps
        ed.get_by_text("Paragraph 5 ", exact=False).first.click()
        far = ed.get_by_text("Paragraph 400 ", exact=False).first
        far.scroll_into_view_if_needed(); far.click(modifiers=["Shift"])
        n = count(page)
        assert 400 < n < 450, n
        assert make_snippet(page, "Four hundred") == n

        # 4. a selection that starts inside a table (BIG table has 90 rows over page gaps) and ends in a paragraph
        cell = ed.get_by_text("BIG-3", exact=True).first if ed.get_by_text("BIG-3", exact=True).count() else ed.locator("td").first
        cell.scroll_into_view_if_needed(); cell.click()
        end = ed.get_by_text("Paragraph 350 ", exact=False).first
        end.scroll_into_view_if_needed(); end.click(modifiers=["Shift"])
        assert count(page) >= 2
        make_snippet(page, "From a table")

        # 5. Select blocks mode: gutter handles, no checkbox in the editor area, Shift+click range
        assert ed.locator("input[type=checkbox]").count() == 0
        page.get_by_role("button", name="Select blocks").click()
        assert ed.locator("input[type=checkbox]").count() == 0
        h = ed.locator(".blk-handle")
        expect(h.first).to_be_attached()
        h.nth(10).scroll_into_view_if_needed(); h.nth(10).click()
        expect(page.get_by_test_id("sel-count")).to_contain_text("1 block selected")
        h.nth(500).scroll_into_view_if_needed(); h.nth(500).click(modifiers=["Shift"])
        expect(page.get_by_test_id("sel-count")).to_contain_text("491 blocks selected")
        assert make_snippet(page, "By handles") == 491
        page.get_by_role("button", name="Select blocks").click()

        # 6. the autosave failing (offline server) must not break "create snippet": the blocks travel with the request
        page.route(re.compile(r"/api/imports/[^/]+$"), lambda r: r.abort() if r.request.method == "PUT" else r.continue_())
        ed.get_by_text("Paragraph 8 ", exact=False).first.scroll_into_view_if_needed()
        ed.get_by_text("Paragraph 8 ", exact=False).first.click(); page.keyboard.press("End"); page.keyboard.type(" TYPED-OFFLINE")
        page.keyboard.press("Shift+ArrowDown")
        make_snippet(page, "While offline")
        page.unroute(re.compile(r"/api/imports/[^/]+$"))
        # ---- ribbon tabs at 1100 px: every tab usable, nothing overflows, collapsible ----
        page.set_viewport_size({"width": 1100, "height": 900})
        rib = page.get_by_test_id("ribbon")
        for tab, probe in (("Home", "Font family"), ("Insert", "Insert table with size"), ("Layout", "Cell shading"), ("References", "Insert caption (Figure/Table, SEQ numbering, style Caption)"), ("Review", "Find and replace (Ctrl+F)")):
            rib.get_by_role("tab", name=tab).click()
            expect(rib.get_by_role("tab", name=tab)).to_have_attribute("aria-selected", "true")
            expect(rib.get_by_label(probe, exact=True).first).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"page overflows horizontally on the {tab} tab"
            assert page.evaluate("(() => { const r = document.querySelector('[data-testid=ribbon]'); return r.scrollWidth <= r.clientWidth + 1; })()"), f"ribbon overflows on {tab}"
        rib.get_by_role("button", name="Collapse ribbon").click()
        expect(rib.get_by_role("tabpanel")).to_be_hidden()
        rib.get_by_role("tab", name="Home").click()  # picking a tab expands it again
        expect(rib.get_by_role("tabpanel")).to_be_visible()

        # direct formatting from the Home tab -> preview + exported XML; undo/redo buttons; find & replace with regex
        ed.get_by_text("Paragraph 3 ", exact=False).first.scroll_into_view_if_needed()
        ed.get_by_text("Paragraph 3 ", exact=False).first.click(click_count=3)
        rib.get_by_label("Font size", exact=True).select_option("18")
        rib.get_by_role("button", name="Bold (Ctrl+B)").click()
        rib.get_by_role("button", name="Strikethrough (Ctrl+Shift+X)").click()
        rib.get_by_role("button", name="Justify").click()
        rib.get_by_label("Line spacing").select_option("360")
        expect(ed.locator("p", has_text="Paragraph 3 ").first).to_have_css("text-align", "justify")
        assert ed.locator("p", has_text="Paragraph 3 ").first.locator("span[data-fmt]").count() >= 1
        rib.get_by_role("button", name="Undo (Ctrl+Z)").click()
        rib.get_by_role("button", name="Redo (Ctrl+Y)").click()
        rib.get_by_role("tab", name="Review").click()
        rib.get_by_role("button", name="Find and replace (Ctrl+F)").click()
        page.get_by_role("textbox", name="Find", exact=True).fill(r"Paragraph (\d)5 ")
        page.get_by_role("button", name="Regex").click()
        expect(page.get_by_test_id("find-count")).to_contain_text("matches")
        page.get_by_role("textbox", name="Replace with").fill("Para $15 ")
        page.get_by_role("button", name="Replace all").click()
        page.get_by_role("button", name="Close", exact=True).click()
        expect(ed.get_by_text("Para 15 ", exact=False).first).to_be_attached()
        assert re.search(r"\d+ words", rib.get_by_test_id("word-count").inner_text())
        saved(page)

        # ---- caption + list of figures (References tab) ----
        rib.get_by_role("tab", name="References").click()
        img = ed.locator("img.img").first
        img.scroll_into_view_if_needed(); img.click()
        rib.get_by_role("button", name="Insert caption (Figure/Table, SEQ numbering, style Caption)").click()
        page.get_by_label("Caption text").fill("A red box")
        page.get_by_role("button", name="Insert caption", exact=True).click()
        expect(ed.locator("p.st-Caption").first).to_contain_text("Figure 1: A red box")
        ed.locator("p.st-Caption").first.click()
        rib.get_by_role("button", name="Insert list of figures").first.click()
        page.get_by_role("button", name="Insert", exact=True).click()
        expect(ed.locator(".toc-tof")).to_contain_text("Figure 1: A red box")

        # ---- mark / unmark a table of contents and a section; Structure outline ----
        h1 = ed.locator("h1").first
        h1.scroll_into_view_if_needed(); h1.click()
        page.get_by_role("button", name="Select blocks").click()
        ed.locator(".blk-handle").nth(1).click()
        ed.locator(".blk-handle").nth(3).click(modifiers=["Shift"])
        expect(page.get_by_test_id("sel-count")).to_contain_text("3 blocks selected")
        rib.get_by_role("button", name="Mark the selected blocks as a section, appendix or generated list").click()
        page.get_by_label("Kind").select_option("toc")
        page.get_by_label("Title (empty: none)").fill("Contents")
        page.get_by_role("button", name=re.compile(r"Mark 3 blocks")).click()
        expect(ed.locator(".toc-toc")).to_have_count(1)
        expect(ed.locator(".toc-toc")).to_contain_text("Chapter 1")
        page.get_by_role("button", name="Select blocks").click()
        page.get_by_role("button", name="Structure", exact=True).first.click()
        st = page.get_by_test_id("structure")
        expect(st.get_by_test_id("outline-list")).to_have_count(2)  # the TOC and the list of figures
        st.get_by_test_id("outline-list").first.get_by_role("button", name="Unmark").click()
        expect(ed.locator(".toc-toc")).to_have_count(0)
        # a named section: mark 2 blocks, rename in the outline, jump, unmark
        p10 = ed.get_by_text("Paragraph 10 ", exact=False).first
        p10.scroll_into_view_if_needed(); p10.click()
        page.keyboard.press("Shift+ArrowDown"); page.keyboard.press("Shift+ArrowDown")
        rib.get_by_role("button", name="Mark the selected blocks as a section, appendix or generated list").click()
        page.get_by_label("Kind").select_option("appendix")
        page.get_by_label("Name").fill("Annex A")
        page.get_by_role("button", name=re.compile(r"Mark \d blocks")).click()
        row = st.get_by_test_id("outline-section")
        expect(row).to_have_count(1)
        row.get_by_role("textbox").fill("Annex B"); row.get_by_role("textbox").blur()
        expect(row.get_by_role("textbox")).to_have_value("Annex B")
        assert ed.locator(".in-sec.sec-appendix").count() >= 2
        saved(page)
        with page.expect_download(timeout=60_000) as dl:
            page.get_by_role("button", name="Export DOCX").click()
        exp = os.path.join(ART, "editor-sections.docx")
        dl.value.save_as(exp)
        x = part(exp, "word/document.xml")
        assert "dgsec_appendix_1_Annex_B" in x and 'TOC \\h \\z \\c "Figure"' in x
        assert "SEQ Figure" in x and "A red box" in x
        assert '<w:jc w:val="both"/>' in x and "<w:strike" in x
        row.get_by_role("button", name="Unmark").click()
        expect(ed.locator(".in-sec")).to_have_count(0)

        # ---- header/footer: click the header of a page, edit it, see it on every page and in the re-exported DOCX ----
        pane = page.get_by_label("Editor pane")
        hdr = pane.locator(".docx-hdr").first
        hdr.scroll_into_view_if_needed()
        box = hdr.bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        hf = page.get_by_test_id("hf-editor")
        expect(hf).to_be_visible()
        expect(hf.get_by_test_id("hf-box")).to_contain_text("LONG-HEADER")
        expect(page.get_by_test_id("hf-shared")).to_contain_text("section")
        box_ed = hf.get_by_test_id("hf-box").locator(".ProseMirror")
        box_ed.click(); page.keyboard.press("Control+a"); page.keyboard.type("ACME Corp — ")
        hrib = hf.get_by_test_id("ribbon")
        hrib.get_by_role("tab", name="Insert").click()
        hrib.get_by_label("Insert field").select_option(label="Page number")
        page.keyboard.type(" / ")
        hrib.get_by_label("Insert field").select_option(label="Total pages")
        hrib.get_by_label("Insert field").select_option(label="Document title")
        expect(box_ed).to_contain_text("ACME Corp")
        page.get_by_role("button", name="Done").click()
        expect(pane.locator(".docx-hdr").first).to_contain_text("ACME Corp")
        n_gaps = page.locator(".pgsep .docx-hdr").count()
        assert n_gaps >= 20
        expect(page.locator(".pgsep .docx-hdr", has_text="ACME Corp")).to_have_count(n_gaps)  # the edit is in every page header (layout re-measures after ~350 ms)
        assert "LONG-HEADER" not in page.locator(".pgsep .docx-hdr").first.inner_text()
        txt = page.locator(".pgsep .docx-hdr").nth(3).inner_text()
        assert re.search(r"ACME Corp — 5 ?/ ?\d+", txt), txt  # PAGE and NUMPAGES evaluated per page
        assert page.get_by_test_id("preview").locator(".docx-hdr", has_text="ACME Corp").count() >= 2
        # footer click edits the footer, not the header
        ftr = pane.locator(".docx-ftr").last
        ftr.scroll_into_view_if_needed()
        fb = ftr.bounding_box()
        page.mouse.click(fb["x"] + fb["width"] / 2, fb["y"] + fb["height"] / 2)
        expect(page.get_by_test_id("hf-box")).to_contain_text("LONG-FOOTER")
        page.get_by_role("button", name="Done").click()
        saved(page)
        with page.expect_download(timeout=60_000) as dl:
            page.get_by_role("button", name="Export DOCX").click()
        exp = os.path.join(ART, "editor-header.docx")
        dl.value.save_as(exp)
        z = zipfile.ZipFile(exp)
        h = part(exp, "word/header1.xml")
        assert "ACME Corp" in h and "LONG-HEADER" not in h
        assert re.search(r"<w:instrText[^>]*>\s*PAGE\s*</w:instrText>", h) and re.search(r"<w:instrText[^>]*>\s*NUMPAGES\s*</w:instrText>", h)
        assert 'w:fldCharType="begin"' in h
        assert "LONG-FOOTER" in part(exp, "word/footer1.xml")  # untouched part: original content
        assert zipfile.ZipFile(long_docx).read("word/footer1.xml") == z.read("word/footer1.xml")  # byte-identical
        print("e2e editor OK")
    except Exception:
        page.screenshot(path=f"{ART}/editor-failure.png", full_page=True)
        raise
    finally:
        browser.close()
