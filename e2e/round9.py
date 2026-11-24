"""Round 9 e2e (E2E_BASE_URL): builder column widths at 1440/1100 px, themed scrollbars (pixels of the scrollbar track in dark
theme, not white), ... Logs in as the throwaway user `round9` of scripts/e2e-users.sh."""
import io
import os
import re

from PIL import Image
from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, login, new_folder, new_item, user

email, password, _ = user("round9")
os.makedirs(ART, exist_ok=True)


def rect(page, sel):
    return page.evaluate("s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom}; }", sel)


def builder_layout(page):
    for w in (1440, 1100):
        page.set_viewport_size({"width": w, "height": 900})
        lib, blk, rp = rect(page, ".library"), rect(page, ".blocks"), rect(page, ".rpanel")
        no_hscroll = page.evaluate("() => document.documentElement.scrollWidth <= innerWidth && [...document.querySelectorAll('.main, .builder')].every(e => e.scrollWidth <= e.clientWidth + 1)")
        print(f"{w}px: library {lib['w']:.0f} blocks {blk['w']:.0f} rpanel {rp['w']:.0f} (x={rp['x']:.0f}) hscroll-free={no_hscroll}")
        assert round(lib["w"]) == 320 and 200 <= blk["w"] <= 560.5 and no_hscroll
        if w == 1440:
            assert rp["w"] >= 500 and rp["x"] >= blk["r"] and rp["r"] <= w  # side by side, nothing overlaps
        else:
            assert rp["w"] >= 400  # overlays (position: fixed) at <= 1280 px; see docs/UI.md


def scrollbars(page, scheme):
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": 1440, "height": 240})  # short: the right panel and the main column scroll
    page.wait_for_timeout(100)
    cs = page.evaluate("() => [getComputedStyle(document.documentElement).colorScheme, getComputedStyle(document.documentElement).scrollbarColor, getComputedStyle(document.documentElement).scrollbarWidth]")
    assert cs[0] == scheme and cs[2] == "thin", cs
    r = page.evaluate("""() => { const e = document.querySelector('.rpanel'); return {x: e.getBoundingClientRect().x, y: e.getBoundingClientRect().y, w: e.getBoundingClientRect().width, h: e.clientHeight,
      bar: e.offsetWidth - e.clientWidth, scrolls: e.scrollHeight > e.clientHeight}; }""")
    assert r["scrolls"], r
    img = Image.open(io.BytesIO(page.screenshot(path=os.path.join(ART, f"scrollbar-{scheme}.png")))).convert("RGB")
    xs = [int(r["x"] + r["w"] - 1 - i) for i in range(0, max(r["bar"], 6), 1)]
    px = {img.getpixel((x, int(r["y"] + r["h"] / 2))) for x in xs}
    print(scheme, "scrollbar width", r["bar"], "colours at the track:", sorted(px)[:4], cs[1])
    assert 4 <= r["bar"] - 1 <= 12, r  # thin (the 1 is the panel border); classic bars are 15 px
    if scheme == "dark":
        assert (255, 255, 255) not in px and all(max(c) < 140 for c in px), px  # no white track
    else:
        assert all(min(c) > 150 for c in px), px


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])  # Playwright hides scrollbars by default
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        login(page, email, password)
        new_folder(page, f"R9 {os.getpid()}")
        new_item(page, "document", "Layout doc")
        expect(page.locator(".library")).to_be_visible()
        builder_layout(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        for scheme in ("dark", "light"):
            scrollbars(page, scheme)
        print("round9 (layout, scrollbars): ok")
    except Exception:
        page.screenshot(path=os.path.join(ART, "round9-fail.png"))
        raise
    finally:
        browser.close()
