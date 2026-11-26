"""Round 19 e2e: the editor never shows a preview on load (fresh profile, stale pre-round-18 keys, client-side navigation,
direct load): no preview pane, zoom 100%, no /render/pdf request."""
import secrets

from playwright.sync_api import expect, sync_playwright

from common import BASE, EDITOR, login, new_folder, new_item, user

email, password, _ = user("prefs")


def check(page, reqs, why):
    page.locator(EDITOR).wait_for()
    expect(page.get_by_label("Preview mode")).to_have_value("off")
    expect(page.get_by_label("Zoom")).to_have_value("1")
    for lab in ("Preview pane", "Word rendering pane"):
        expect(page.get_by_label(lab)).to_have_count(0)
    expect(page.get_by_test_id("preview")).to_have_count(0)
    expect(page.get_by_test_id("word-pdf")).to_have_count(0)
    assert not [r for r in reqs if "/render/" in r], (why, reqs)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    for name, init in (("fresh", ""), ("stale", "localStorage.setItem('docgen.editor.preview','live');localStorage.setItem('docgen.editor.zoom','0.45');")):
        page = browser.new_context().new_page()
        reqs: list[str] = []
        page.on("request", lambda r: reqs.append(r.url))
        if init:
            page.add_init_script(init)
        login(page, email, password, prefs=False)
        new_folder(page, "Prefs" + secrets.token_hex(2))
        title = f"{name}-{secrets.token_hex(2)}"
        new_item(page, "snippet", title)  # client-side navigation into the editor
        check(page, reqs, f"{name}: client nav")
        url = page.url
        page.goto(BASE + "/")
        page.goto(url)  # direct load
        check(page, reqs, f"{name}: direct load")
        page.go_back()
        page.go_forward()
        check(page, reqs, f"{name}: history nav")
        print(name, "ok")
    browser.close()
