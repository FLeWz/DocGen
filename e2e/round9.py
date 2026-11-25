"""Round 9 e2e (E2E_BASE_URL): builder column widths at 1440/1100 px, themed scrollbars (pixels of the scrollbar track in dark
theme, not white), editing a base document in the import editor (add a paragraph, commit -> version 2, newer-version badge and bump in
the builder, builds use the new version), the revision table (Insert > Revision table in the base, document with two commits: author,
sections and commit name in the DOCX). Logs in as the throwaway user `round9` of scripts/e2e-users.sh.
Needs E2E_COVER_DOCX (or docker compose) like basedoc.py."""
import io
import os
import re
import shutil
import subprocess
import zipfile

from PIL import Image
from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, editor_ready, login, new_folder, new_item, saved, user

email, password, _ = user("round9")
os.makedirs(ART, exist_ok=True)
cover = os.path.join(ART, "cover.docx")
if os.environ.get("E2E_COVER_DOCX"):
    shutil.copy(os.environ["E2E_COVER_DOCX"], cover)
else:
    with open(cover, "wb") as f:
        f.write(subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "api", "python", "-m", "tests.fixtures.cover", "-"],
                               capture_output=True, check=True).stdout)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def rect(page, sel):
    return page.evaluate("s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom}; }", sel)


def widths(page):
    lib, blk, rp = rect(page, ".library"), rect(page, ".blocks"), rect(page, ".rpanel")
    return lib, blk, rp


def drag(page, sel, dx):
    h = rect(page, sel)
    x, y = h["x"] + h["w"] / 2, h["y"] + 200
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + dx / 2, y, steps=4); page.mouse.move(x + dx, y, steps=4); page.mouse.up()


def builder_layout(page):
    """3 panes [library | blocks (>= 600, fills the rest) | right panel], resizable, persisted per user."""
    nohs = "() => document.documentElement.scrollWidth <= innerWidth && [...document.querySelectorAll('.main, .builder')].every(e => e.scrollWidth <= e.clientWidth + 1)"
    for w in (1920, 1440, 1100):
        page.set_viewport_size({"width": w, "height": 900})
        page.wait_for_timeout(150)
        lib, blk, rp = widths(page)
        print(f"{w}px: library {lib['w']:.0f} blocks {blk['w']:.0f} rpanel {rp['w']:.0f} (x={rp['x']:.0f}) blocks.r={blk['r']:.0f}")
        assert blk["w"] >= 599.5, blk
        if w > 1280:
            assert page.evaluate(nohs)
            handle = rect(page, "[data-testid=resize-right]")
            assert abs(rp["x"] - blk["r"] - handle["w"]) <= 1 and rp["w"] >= 360 and rp["r"] <= w + 1  # no gap: only the handle between
            assert abs(handle["w"] - 6) <= 1
        else:
            assert rp["w"] >= 400  # overlays (position: fixed); see docs/UI.md
            assert page.evaluate(nohs) or blk["r"] > 0
    page.set_viewport_size({"width": 1920, "height": 900})
    page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('docgen.builder.')).forEach(k => localStorage.removeItem(k))")
    page.reload(); expect(page.locator(".library")).to_be_visible(); page.wait_for_timeout(200)
    lib0, blk0, rp0 = widths(page)
    assert round(lib0["w"]) == 320 and round(rp0["w"]) == 500, (lib0, rp0)
    drag(page, "[data-testid=resize-lib]", 80)
    lib1, blk1, rp1 = widths(page)
    assert abs(lib1["w"] - 400) <= 2 and abs(blk0["w"] - blk1["w"] - 80) <= 2 and abs(rp1["w"] - 500) <= 1, (lib1, blk1)
    drag(page, "[data-testid=resize-right]", -60)
    lib2, blk2, rp2 = widths(page)
    assert abs(rp2["w"] - 560) <= 2 and abs(blk1["w"] - blk2["w"] - 60) <= 2 and abs(rp2["x"] - blk2["r"] - 6) <= 1, (rp2, blk2)
    print(f"1920 dragged: library {lib1['w']:.0f} blocks {blk2['w']:.0f} rpanel {rp2['w']:.0f}")
    page.reload(); expect(page.locator(".library")).to_be_visible(); page.wait_for_timeout(200)
    lib3, _, rp3 = widths(page)
    assert abs(lib3["w"] - lib1["w"]) <= 1 and abs(rp3["w"] - rp2["w"]) <= 1  # persisted
    sep = page.locator("[data-testid=resize-lib]")
    assert sep.get_attribute("role") == "separator" and sep.get_attribute("aria-orientation") == "vertical"
    assert int(sep.get_attribute("aria-valuenow")) == 400 and sep.get_attribute("aria-valuemin") == "220"
    sep.focus(); page.keyboard.press("ArrowRight")
    assert abs(rect(page, ".library")["w"] - 416) <= 1
    page.keyboard.press("Home")
    assert abs(rect(page, ".library")["w"] - 320) <= 1
    page.locator("[data-testid=resize-right]").dblclick()
    assert abs(rect(page, ".rpanel")["w"] - 500) <= 1
    # too narrow for the wanted widths: blocks keep 600, the panels shrink (right first), no page scroll
    page.set_viewport_size({"width": 1440, "height": 900}); page.wait_for_timeout(200)
    lib, blk, rp = widths(page)
    print(f"1440 after reset: library {lib['w']:.0f} blocks {blk['w']:.0f} rpanel {rp['w']:.0f}")
    assert blk["w"] >= 599.5 and rp["w"] >= 359.5 and page.evaluate(nohs)


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


def api(page, method, path, **kw):
    """Calls the app's API with the page's session (setup only: the things under test are driven through the UI)."""
    page.request.get(BASE + "/api/auth/csrf")
    tok = next(c["value"] for c in page.context.cookies() if c["name"] == "docgen_csrf")
    r = page.request.fetch(BASE + "/api" + path, method=method, headers={"X-CSRF-Token": tok}, **kw)
    assert r.ok, (method, path, r.status, r.text())
    return r


def edit_base(page, name):
    page.get_by_role("link", name="Base documents").click()
    if not page.get_by_test_id("edit-base").count():
        page.get_by_role("button", name=f"Details of {name}").click()
    page.get_by_test_id("edit-base").click()
    expect(page).to_have_url(re.compile("/imports/"))
    expect(page.get_by_test_id("base-edit-bar")).to_contain_text(f"Editing base document “{name}”")
    return editor_ready(page)


def commit_base(page, message, version):
    page.get_by_role("button", name="Commit…").click()
    page.get_by_label("Commit message").fill(message)
    page.get_by_role("button", name=f"Commit as version {version}").click()
    expect(page.get_by_test_id("base-committed")).to_contain_text(f"Version {version}")
    page.get_by_role("button", name="Close", exact=True).click()
    expect(page).to_have_url(re.compile("/base-documents"))


def export_docx(page, did) -> str:
    eid = api(page, "GET", f"/documents/{did}/export?format=docx").json()["export_id"]
    for _ in range(80):
        st = api(page, "GET", f"/exports/{eid}").json()
        if st["status"] not in ("running", "queued"):
            break
        page.wait_for_timeout(500)
    assert st["status"] == "done", st
    z = zipfile.ZipFile(io.BytesIO(api(page, "GET", f"/exports/{eid}/download").body()))
    return z.read("word/document.xml").decode()


def base_and_revisions(page):
    name = f"Rev base {os.getpid()}"
    api(page, "POST", "/base-documents", multipart={"name": name, "file": {"name": "cover.docx", "mimeType": DOCX, "buffer": open(cover, "rb").read()}})
    bid = next(b["id"] for b in api(page, "GET", "/base-documents").json() if b["name"] == name)
    fo = api(page, "POST", "/folders", data={"name": f"Revs {os.getpid()}"}).json()["id"]
    page.goto(BASE + "/base-documents")

    # A: edit the base in the import editor: add a paragraph, commit -> version 2 with the new text
    ed = edit_base(page, name)
    expect(page.get_by_test_id("base-diff")).to_contain_text("no changes yet")
    ed.get_by_text("More body.").click()
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.keyboard.type("Paragraph added in round 9")
    page.keyboard.press("Enter")
    page.get_by_role("button", name="{{ CONTENT }}", exact=True).click()  # the cover has no body slot yet
    saved(page)
    expect(page.get_by_test_id("base-diff")).to_contain_text("added", timeout=15000)
    commit_base(page, "Add a paragraph", 2)
    page.get_by_role("button", name=f"Details of {name}").click()
    expect(page.get_by_text("Add a paragraph").first).to_be_visible()

    # B: revision table into the base (Insert > Revision table), version 3
    ed = edit_base(page, name)
    ed.get_by_text("More body.").click()
    page.get_by_role("tab", name="Insert").click()
    page.get_by_role("button", name="Revision table").click()
    expect(ed.locator("table").filter(has_text="Sections changed")).to_have_count(1)
    saved(page)
    commit_base(page, "Add revision table", 3)

    # a document pinned to version 2 shows the badge; the bump uses v3; two commits of a snippet = two revisions
    v = api(page, "GET", f"/base-documents/{bid}/versions").json()
    did = api(page, "POST", "/documents", data={"folder_id": fo, "title": "Rev doc"}).json()["id"]
    sid = api(page, "POST", f"/folders/{fo}/snippets", data={"title": "Chapter"}).json()["id"]
    heading = {"type": "heading", "attrs": {"level": 1, "styleId": None}, "content": [{"type": "text", "text": "Chapter heading"}]}
    for text, msg in (("first text", "Write chapter"), ("second text", "Reword chapter")):
        api(page, "PUT", f"/snippets/{sid}/draft", data={"content": {"type": "doc", "content": [heading, {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}})
        sha = api(page, "POST", f"/folders/{fo}/commit", data={"message": msg, "snippet_ids": [sid]}).json()["sha"]
    blocks = [{"type": "snippet_ref", "folder_id": fo, "snippet_id": sid, "version": {"type": "commit", "value": sha}}]
    old = next(x["sha"] for x in v if x["number"] == 2)
    api(page, "PUT", f"/documents/{did}/draft", data={"blocks": blocks, "base_document": {"id": bid, "version": old}})
    page.goto(BASE + f"/documents/{did}")
    badge = page.get_by_test_id("base-newer")
    expect(badge).to_contain_text("v3")
    pinned = export_docx(page, did)
    assert "Paragraph added in round 9" in pinned and "Sections changed" not in pinned, "pinned to v2: the paragraph, no revision table yet"
    badge.get_by_role("button", name="Use v3").click()
    expect(badge).to_have_count(0)
    page.wait_for_timeout(1500)  # autosave of the draft
    xml = export_docx(page, did)
    assert "Sections changed" in xml, "bumped to v3: the revision table is in the build"
    for needle in ("Write chapter", "Reword chapter", "E2E User", "Chapter heading (added)", "Add a paragraph", "Add revision table"):
        assert needle in xml, needle
    assert "{%" not in xml and "{{" not in xml
    print("round9 (edit base, revision table, bump): ok")


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
        page.set_viewport_size({"width": 1700, "height": 1000})
        base_and_revisions(page)
    except Exception:
        page.screenshot(path=os.path.join(ART, "round9-fail.png"))
        raise
    finally:
        browser.close()
