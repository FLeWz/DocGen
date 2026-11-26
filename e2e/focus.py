"""Round 18 e2e: editor defaults (no preview, 100%), stage fills the viewport, focus mode (button, Ctrl+Shift+F, Esc, ?focus=1).
Logs in as the throwaway user `focus` WITHOUT the preview prefs the other scripts preset."""
import re

from playwright.sync_api import expect, sync_playwright

from common import BASE, EDITOR, ART, login, new_folder, new_item, saved, user

email, password, _ = user("focus")
STAGE = ".pane .docx-stage"


def box(page, sel):
    return page.locator(sel).first.bounding_box()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        login(page, email, password, prefs=False)
        import secrets; new_folder(page, "Focus" + secrets.token_hex(2))
        new_item(page, "snippet", "Wide")
        url = page.url
        page.locator(EDITOR).wait_for()
        # defaults: no preview, 100%
        expect(page.get_by_label("Preview mode")).to_have_value("off")
        expect(page.get_by_label("Zoom")).to_have_value("1")
        expect(page.get_by_test_id("preview")).to_have_count(0)
        expect(page.get_by_role("checkbox")).to_have_count(0)

        def check_stage(w, h):
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(300)  # layout settle after resize (no UI condition to wait on)
            b = box(page, STAGE)
            assert b["y"] + b["height"] >= h - 50, f"stage ends at {b['y'] + b['height']} of {h}"
            pass
            assert page.evaluate("document.documentElement.scrollHeight") <= h, "page itself scrolls"
            assert page.evaluate("(()=>{const m=document.querySelector('.main');return m.scrollHeight<=m.clientHeight+1})()"), "double scroll: .main scrolls"
            print(f"stage {w}x{h}: {b['height']:.0f}px", page.evaluate("[...document.querySelectorAll('.page.fill > *, .editorpage > *')].map(e=>e.className+':'+Math.round(e.getBoundingClientRect().height))"))
            return b["height"]

        normal = {s: check_stage(*s) for s in [(1440, 900), (1100, 800)]}
        # focus mode via button
        btn = page.get_by_test_id("focus-toggle")
        btn.click()
        expect(page).to_have_url(re.compile(r"\?focus=1$"))
        for sel in (".topbar", ".sidebar", ".pagehead"):
            expect(page.locator(sel)).to_be_hidden()
        expect(page.get_by_test_id("ribbon")).to_be_visible()
        expect(page.get_by_test_id("save-status")).to_be_visible()
        expect(btn).to_be_visible()
        for s in [(1440, 900), (1100, 800)]:
            h = check_stage(*s)
            assert h > normal[s] + 60, f"focus stage {h} not taller than {normal[s]}"
            assert box(page, ".ribbon")["y"] < 100, "ribbon not at the top"
        # ribbon tabs usable at 1100
        page.get_by_role("tab", name="Insert").click()
        # typing still works
        ed = page.locator(EDITOR)
        ed.click()
        page.keyboard.type("Focus typing works")
        expect(ed).to_contain_text("Focus typing works")
        saved(page)
        # modal above focus mode; Esc closes the modal only
        page.get_by_role("button", name="Discard draft").click()
        dlg = page.get_by_role("dialog")
        expect(dlg).to_be_visible()
        page.keyboard.press("Escape")
        expect(dlg).to_have_count(0)
        expect(page).to_have_url(re.compile(r"\?focus=1$"))
        # Esc exits
        ed.click()
        page.keyboard.press("Escape")
        expect(page).to_have_url(url)
        expect(page.locator(".topbar")).to_be_visible()
        # Ctrl+Shift+F toggles
        page.keyboard.press("Control+Shift+F")
        expect(page.locator(".topbar")).to_be_hidden()
        page.keyboard.press("Control+Shift+F")
        expect(page.locator(".topbar")).to_be_visible()
        # exit button
        btn.click(); btn.click()
        expect(page.locator(".sidebar")).to_be_visible()
        # preview in focus mode: 50/50 split of the full width
        btn.click()
        page.get_by_label("Preview mode").select_option("live")
        expect(page.get_by_test_id("preview")).to_be_visible()
        a, b = box(page, "[aria-label='Editor pane']"), box(page, "[aria-label='Preview pane']")
        assert abs(a["width"] - b["width"]) < 4 and a["width"] + b["width"] > 1100 * 0.9, (a, b)
        page.get_by_label("Zoom").select_option("0.6")
        # remembered per browser; deep link restores focus mode with the saved prefs
        page.goto(url + "?focus=1")
        page.locator(EDITOR).wait_for()
        expect(page.get_by_label("Preview mode")).to_have_value("live")
        expect(page.get_by_label("Zoom")).to_have_value("0.6")
        expect(page.locator(".topbar")).to_be_hidden()
        # leaving the editor clears focus mode
        page.evaluate("history.back()")
        page.goto(BASE + "/")
        expect(page.locator(".topbar")).to_be_visible()
        # a fresh browser starts again at none + 100%
        p2 = browser.new_page(viewport={"width": 1440, "height": 900})
        login(p2, email, password, prefs=False)
        p2.goto(url)
        p2.locator(EDITOR).wait_for()
        expect(p2.get_by_label("Preview mode")).to_have_value("off")
        expect(p2.get_by_label("Zoom")).to_have_value("1")
        page.screenshot(path=f"{ART}/focus.png")
        print("e2e focus OK")
    except Exception:
        page.screenshot(path=f"{ART}/focus-fail.png")
        raise
    finally:
        browser.close()
