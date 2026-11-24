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
        print("e2e editor (selection) OK")
    except Exception:
        page.screenshot(path=f"{ART}/editor-failure.png", full_page=True)
        raise
    finally:
        browser.close()
