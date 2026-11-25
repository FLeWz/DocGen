"""Round 13 e2e: download a committed document version from its history + the render cache.
Commit a document twice (different pinned snippet text) -> history -> download the OLD version as DOCX (text differs from the
latest, 'cached' badge absent) -> download again (cached badge) -> add a tag on that commit -> download again (not cached, the
revision table contains the tag)."""
import io
import re
import zipfile

from playwright.sync_api import expect, sync_playwright

from common import BASE, login, user

email, password, _ = user("round13")


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

        fo = api("POST", "/folders", {"name": "R13"})["id"]
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
        rows = page.get_by_test_id("commit-row")
        expect(rows).to_have_count(2)
        old = rows.filter(has_text="doc rev 1")

        def download(row):
            with page.expect_download() as d:
                row.get_by_test_id("dl-docx").click()
            return docx_text(open(d.value.path(), "rb").read())

        t_old = download(old)
        assert "first wording" in t_old and "second wording" not in t_old, t_old[-400:]
        assert "doc rev 2" not in t_old and re.search(r"\d\d\.\d\d\.\d{4}", t_old)
        t_new = download(rows.filter(has_text="doc rev 2"))
        assert "second wording" in t_new and "first wording" not in t_new
        expect(old.get_by_text("cached")).to_have_count(0)
        download(old)
        expect(old.get_by_text("cached")).to_be_visible()  # second download of the same version: from the cache

        api("POST", f"/folders/{fo}/tags", {"name": "rel-1", "sha": docs[0]})
        t_tag = download(old)  # same page: the badge from the previous download is still shown until this one answers
        expect(old.get_by_text("cached")).to_have_count(0)  # a tag changes the revision table: not served from the cache
        assert "rel-1" in t_tag and "rel-1" not in t_old
        print("round13 e2e ok")
    finally:
        browser.close()
