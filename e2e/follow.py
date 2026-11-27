"""Round 23 e2e: the open preview follows the editor. 70-block snippet; HTML preview: click in block 50 -> block 50 visible, typing at the
top scrolls back up, wheel on the editor drives the preview, a manual wheel on the preview pauses it, Follow off stops it; Markdown mode: caret
moves drive the preview; PDF mode: the iframe src page fragment changes; preview closed: no errors, no Follow button. Logs in as `follow`."""
import os
import secrets
import time

from playwright.sync_api import expect, sync_playwright

from common import BASE, EDITOR, login, new_folder, new_item, user

email, password, _ = user("follow")
STAGE = '[aria-label="Preview pane"] .docx-stage'
N = 70
TEXT = "\n\n".join(f"Block {i} " + "lorem ipsum dolor sit amet " * 8 for i in range(N))


VIS = """([s, i]) => { const st = document.querySelector(s), el = st && st.querySelector(`.docx-pages [data-block="${i}"]`);
        if (!el) return false; const a = st.getBoundingClientRect(), b = el.getBoundingClientRect(); return b.top >= a.top && b.bottom <= a.bottom; }"""


def wait_vis(page, i):
    """Wait until preview block i is fully inside the preview stage's viewport."""
    page.wait_for_function(VIS, arg=[STAGE, i])


def top(page):
    return page.evaluate("s => document.querySelector(s).scrollTop", STAGE)


def ms_until_moved(page, before, limit=3000):
    t = time.time()
    page.wait_for_function("([s, b]) => document.querySelector(s).scrollTop !== b", arg=[STAGE, before], timeout=limit)
    return round((time.time() - t) * 1000)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1700, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        login(page, email, password)
        new_folder(page, "Follow" + secrets.token_hex(2))
        new_item(page, "snippet", "Follow test")
        page.locator(EDITOR).wait_for()
        page.get_by_test_id("mode-markdown").click()
        page.locator(".cm-content").fill(TEXT)
        page.get_by_test_id("mode-word").click()
        expect(page.locator(EDITOR + " p")).to_have_count(N)
        expect(page.get_by_label("Preview mode")).to_have_value("live")
        expect(page.get_by_test_id("follow-toggle")).to_have_attribute("aria-pressed", "true")
        expect(page.locator(STAGE + ' .docx-pages [data-block="69"]')).to_have_count(1)

        # caret in block 50 -> preview block 50 becomes visible, highlighted
        b0 = top(page)
        page.locator(EDITOR + " p").nth(50).click()
        ms = ms_until_moved(page, b0)
        page.wait_for_function("s => document.querySelector(s).querySelector('.docx-pages [data-block=\"50\"].pv-current')", arg=STAGE)
        wait_vis(page, 50)
        print(f"caret -> preview scroll started after {ms} ms (80 ms debounce incl.)")

        # typing at the top scrolls the preview up
        b1 = top(page)
        page.locator(EDITOR + " p").first.click()
        page.keyboard.press("End")
        page.keyboard.type("x")
        page.wait_for_function("([s, b]) => document.querySelector(s).scrollTop < b", arg=[STAGE, b1])
        wait_vis(page, 0)

        # wheel on the editor (no caret move) drives the preview
        b2 = top(page)
        page.locator(".docx-stage").first.hover()
        page.mouse.wheel(0, 4000)
        page.wait_for_function("([s, b]) => document.querySelector(s).scrollTop > b", arg=[STAGE, b2])

        # a manual wheel on the preview pauses following (editor wheel does not move it for ~2 s)
        page.locator(STAGE).hover()
        page.mouse.wheel(0, -100000)
        page.wait_for_function("s => document.querySelector(s).scrollTop === 0", arg=STAGE)
        page.locator(".docx-stage").first.hover()
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(500)
        assert top(page) == 0, "preview moved during the manual-scroll pause"

        # Follow off: a caret move does not scroll the preview
        page.get_by_test_id("follow-toggle").click()
        expect(page.get_by_test_id("follow-toggle")).to_have_attribute("aria-pressed", "false")
        assert page.evaluate("localStorage.getItem('docgen.editor.v2.follow')") == "0"
        page.locator(EDITOR + " p").nth(40).click()
        page.wait_for_timeout(500)
        assert top(page) == 0
        page.get_by_test_id("follow-toggle").click()

        # Markdown mode: caret movement drives the preview
        page.get_by_test_id("mode-markdown").click()
        page.locator(".cm-content").click()
        page.keyboard.press("Control+End")
        wait_vis(page, 69)
        page.keyboard.press("Control+Home")
        wait_vis(page, 0)
        page.get_by_test_id("mode-word").click()
        expect(page.locator(EDITOR + " p")).to_have_count(N)

        # PDF mode: the page fragment follows the caret
        page.get_by_label("Preview mode").select_option("word")
        pdf = page.get_by_test_id("word-pdf")
        expect(pdf).to_be_visible(timeout=60000)
        pdf.evaluate("e => { window.__loads = 0; e.addEventListener('load', () => window.__loads++); }")
        page.locator(EDITOR + " p").nth(65).click()
        expect(pdf).to_have_attribute("src", __import__("re").compile(r"#page=\d+"))
        p_end = int((pdf.get_attribute("src") or "").split("#page=")[1])
        page.locator(EDITOR + " p").nth(3).click()
        page.wait_for_function("([e, p]) => !e.src.endsWith('#page=' + p)", arg=[pdf.element_handle(), p_end])
        p_start = int((pdf.get_attribute("src") or "").split("#page=")[1])
        assert p_start < p_end, (p_start, p_end)
        assert page.evaluate("window.__loads") == 0, "the PDF frame reloaded on a fragment change"
        print(f"pdf pages followed: block 65 -> page {p_end}, block 3 -> page {p_start}")

        # preview closed: no Follow button, no errors while moving the caret and typing
        page.get_by_label("Preview mode").select_option("off")
        expect(page.get_by_test_id("follow-toggle")).to_have_count(0)
        page.locator(EDITOR + " p").nth(20).click()
        page.keyboard.type("y")
        assert not errors, errors
        print("e2e follow ok")
    except BaseException:
        os.makedirs("e2e-artifacts", exist_ok=True)
        page.screenshot(path="e2e-artifacts/follow-fail.png")
        raise
    finally:
        browser.close()
