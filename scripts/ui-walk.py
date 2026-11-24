"""Visual walk-through of every screen against a SEEDED stack (SEED_DEMO=1, e.g. a separate compose project):
  E2E_BASE_URL=http://localhost:8094 PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright .venv/bin/python scripts/ui-walk.py [WxH ...]
Screenshots go to e2e-artifacts/ui/<WxH>/ (gitignored; inspect them yourself). Prints console errors/failed requests,
horizontal overflow per screen and basic a11y findings (unlabelled controls, duplicate ids, images without alt)."""
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8094")
SIZES = sys.argv[1:] or ["1440x900", "1100x800"]
A11Y = """() => {
  const bad = [], name = (e) => (e.getAttribute('aria-label') || e.getAttribute('title') || e.textContent || '').trim();
  document.querySelectorAll('button, a[href], [role=button]').forEach(e => { if (!name(e)) bad.push('no name: ' + e.outerHTML.slice(0, 90)); });
  document.querySelectorAll('input:not([type=hidden]), select, textarea').forEach(e => {
    if (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.closest('label') || (e.id && document.querySelector('label[for="' + e.id + '"]'))) return;
    if (e.getAttribute('placeholder') || e.type === 'file') return bad.push('placeholder only: ' + e.outerHTML.slice(0, 90));
    bad.push('unlabelled: ' + e.outerHTML.slice(0, 90)); });
  const ids = {}; document.querySelectorAll('[id]').forEach(e => { ids[e.id] = (ids[e.id] || 0) + 1; }); Object.entries(ids).forEach(([k, v]) => v > 1 && bad.push('duplicate id ' + k));
  document.querySelectorAll('img:not([alt])').forEach(e => bad.push('img without alt'));
  const m = document.querySelector('main'); const ov = document.documentElement.scrollWidth > innerWidth + 1 ? 'PAGE overflows x: ' + document.documentElement.scrollWidth : '';
  return { bad, ov, h1: [...document.querySelectorAll('h1')].length };
}"""


def main():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for size in SIZES:
            w, h = map(int, size.split("x"))
            out = f"e2e-artifacts/ui/{size}"
            os.makedirs(out, exist_ok=True)
            ctx = b.new_context(viewport={"width": w, "height": h})
            p = ctx.new_page()
            errs = []
            p.on("console", lambda m: m.type in ("error", "warning") and "401" not in m.text and errs.append(f"console {m.type}: {m.text[:200]}"))  # 401 = the login page asking /auth/me
            p.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
            p.on("response", lambda r: r.status >= 400 and r.status != 401 and errs.append(f"HTTP {r.status} {r.url}"))
            p.goto(BASE + "/login"); p.screenshot(path=f"{out}/00-login.png")
            p.get_by_label("Email").fill("owner@example.com"); p.get_by_label("Password").fill("demo-password-123")
            p.get_by_role("button", name="Log in").click(); p.get_by_role("heading", name=re.compile("Welcome")).wait_for()

            def shot(name, wait=None):
                if wait: wait()
                p.wait_for_load_state("networkidle"); p.screenshot(path=f"{out}/{name}.png")
                r = p.evaluate(A11Y)
                print(f"[{size}] {name}: {r['ov'] or 'no x-overflow'}; a11y: {r['bad'][:4] or 'ok'}")

            shot("01-dashboard")
            folders = p.locator("[data-testid=tree-folder]")
            folders.first.click(); shot("02-folder", lambda: p.get_by_test_id("item-row").first.wait_for())
            p.get_by_test_id("item-row").locator("a").first.click()
            shot("03-snippet", lambda: p.locator(".ProseMirror[contenteditable=true]").wait_for())
            p.get_by_label("Insert table").click(); shot("03b-snippet-table")
            p.get_by_label("Make variable").click(); shot("03c-variable-dialog"); p.keyboard.press("Escape")
            p.get_by_label("Preview mode").select_option("off"); shot("03d-preview-off"); p.get_by_label("Preview mode").select_option("live")
            p.get_by_role("link", name="History").first.click(); shot("04-snippet-history", lambda: p.get_by_test_id("commit-row").first.wait_for())
            p.get_by_test_id("tree-folder").first.click(); p.get_by_role("tab", name=re.compile("Changes")).click(); shot("05-commit", lambda: p.wait_for_selector("[data-testid=change-row], [data-testid=no-changes]"))
            p.get_by_role("tab", name="History").click(); shot("06-folder-history", lambda: p.get_by_test_id("commit-row").first.wait_for())
            p.goto(BASE + "/"); p.get_by_role("link", name=re.compile("Recent documents|Runbook|Manual|Handbook", re.I)).first.wait_for(timeout=3000) if False else None
            doc = p.locator("section.card:has-text('Recent documents') a").first
            doc.click(); shot("07-document", lambda: p.get_by_test_id("block").first.wait_for())
            p.get_by_role("button", name="Preview").click(); shot("07b-document-preview", lambda: p.get_by_test_id("preview").wait_for()); p.keyboard.press("Escape")
            p.goto(BASE + "/imports"); shot("08-imports", lambda: p.get_by_role("table").wait_for())
            p.get_by_role("link", name=re.compile("Imported")).first.click(); shot("09-import", lambda: p.locator(".ProseMirror[contenteditable=true]").wait_for())
            p.get_by_role("button", name="Split by headings").click(); shot("09b-import-split", lambda: p.get_by_test_id("split-preview").wait_for()); p.keyboard.press("Escape")
            for tab in ("members", "invites", "base-documents", "styles", "general"):
                p.goto(f"{BASE}/settings/{tab}"); shot(f"10-settings-{tab}", lambda: p.get_by_role("heading", level=1).wait_for())
            p.get_by_label("Organization:").first.click(); shot("11-org-menu"); p.keyboard.press("Escape")
            p.get_by_label("User menu").click(); shot("12-user-menu"); p.keyboard.press("Escape")
            print(f"[{size}] problems:", sorted(set(errs)) or "none")
            ctx.close()
        b.close()


main()
