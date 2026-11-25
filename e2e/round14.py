"""Round 14 e2e: rename a commit in the document history UI -> history shows the new name + 'renamed' badge (tooltip has the
original), the downloaded revision table uses it, Reset to original restores it."""
import io
import re
import zipfile

from playwright.sync_api import expect, sync_playwright

from common import BASE, login, user

email, password, _ = user("round14")


def docx_text(data: bytes) -> str:
    return re.sub(r"<[^>]+>", " ", zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode())


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1500, "height": 1000})
    try:
        login(page, email, password)
        page.request.get(BASE + "/api/auth/csrf")
        csrf = next(c["value"] for c in page.context.cookies() if c["name"] == "docgen_csrf")

        def api(method, path, body=None):
            r = page.request.fetch(BASE + "/api" + path, method=method, data=body, headers={"X-CSRF-Token": csrf})
            assert r.ok, (path, r.status, r.text())
            return r.json() if r.body() else None

        fo = api("POST", "/folders", {"name": "R14"})["id"]
        sid = api("POST", f"/folders/{fo}/snippets", {"title": "Body"})["id"]
        para = lambda t: {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}
        shas = []
        for text in ("first wording", "second wording"):
            api("PUT", f"/snippets/{sid}/draft", {"content": para(text)})
            shas.append(api("POST", f"/folders/{fo}/commit", {"message": f"snippet commit {len(shas) + 1}", "snippet_ids": [sid]})["sha"])
        doc = api("POST", "/documents", {"folder_id": fo, "title": "Manual"})["id"]
        docs = []
        for i, sha in enumerate(shas):
            api("PUT", f"/documents/{doc}/draft", {"blocks": [{"type": "snippet_ref", "folder_id": fo, "snippet_id": sid, "version": {"type": "commit", "value": sha}}]})
            docs.append(api("POST", f"/folders/{fo}/commit", {"message": f"doc rev {i + 1}", "document_ids": [doc]})["sha"])

        page.goto(f"{BASE}/history/document/{doc}")
        row = page.get_by_test_id("commit-row").filter(has_text="doc rev 1")
        row.get_by_test_id("rename-commit").click()
        page.get_by_label("Name").fill("Released to customer")
        page.get_by_label("Name").press("Enter")
        expect(row).to_contain_text("Released to customer")
        expect(row.get_by_test_id("renamed-badge")).to_have_attribute("title", re.compile(r"Originally: doc rev 1 — renamed by .* on \d\d\.\d\d\.\d{4} \d\d:\d\d"))
        row = page.get_by_test_id("commit-row").filter(has_text="Released to customer")
        with page.expect_download() as d:
            row.get_by_test_id("dl-docx").click()
        t = docx_text(open(d.value.path(), "rb").read())
        assert "Released to customer" in t and "doc rev 1" not in t, t[-400:]
        row.get_by_test_id("rename-commit").click()  # Esc cancels
        page.keyboard.press("Escape")
        expect(row).to_contain_text("Released to customer")
        row.get_by_test_id("rename-commit").click()
        page.get_by_role("button", name="Reset to original").click()
        expect(page.get_by_test_id("commit-row").filter(has_text="doc rev 1")).to_have_count(1)
        expect(page.get_by_test_id("renamed-badge")).to_have_count(0)
        print("round14 e2e ok")
    finally:
        browser.close()
