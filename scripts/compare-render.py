"""Compare DocGen's page view of a DOCX with the LibreOffice render (ground truth). Not part of `make test`.
  E2E_BASE_URL=http://localhost:8093 DEV_EMAIL=.. E2E_PASSWORD=.. .venv/bin/python scripts/compare-render.py file.docx [outdir]
Imports the file, opens it in the editor, counts pages in the editor (page gaps + 1) and the preview (`.docx-page` cards), renders the PDF through
/api/render/pdf (needs `pdftoppm` and Pillow) and writes side-by-side PNGs (our preview card | LibreOffice) per page to outdir (default e2e-artifacts/compare).
Inspect the PNGs yourself; the page-count line is the only number it prints besides the paths."""
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
src = Path(sys.argv[1]).read_bytes()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "e2e-artifacts/compare")
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1800, "height": 1000})
    page.goto(BASE + "/login")
    page.get_by_label("Email").fill(os.environ["DEV_EMAIL"])
    page.get_by_label("Password").fill(os.environ["E2E_PASSWORD"])
    page.get_by_role("button", name="Log in").click()
    page.wait_for_url(BASE + "/")
    api = page.request
    api.get(BASE + "/api/auth/csrf")
    csrf = next(c["value"] for c in page.context.cookies() if c["name"] == "docgen_csrf")
    r = api.post(BASE + "/api/import/docx", headers={"X-CSRF-Token": csrf}, multipart={"file": {"name": "x.docx", "mimeType": "application/octet-stream", "buffer": src}})
    iid = r.json()["id"]
    content = api.get(BASE + f"/api/imports/{iid}").json()["content"]
    pdf = api.post(BASE + "/api/render/pdf", headers={"X-CSRF-Token": csrf}, data={"content": content, "import_id": iid}).body()
    (out / "ref.pdf").write_bytes(pdf)
    subprocess.run(["pdftoppm", "-r", "60", "-png", str(out / "ref.pdf"), str(out / "ref")], check=True)
    page.goto(f"{BASE}/imports/{iid}")
    page.locator(".ProseMirror[contenteditable=true]").wait_for()
    page.wait_for_timeout(3000)  # pagination settles (debounced)
    ed = page.locator(".pane[aria-label='Editor pane'] .pgsep")
    pv = page.locator("[data-testid=preview] .docx-page")
    n_ed, n_pv = ed.count() + 1, pv.count()  # the editor is one flow; each .pgsep is a page gap
    pdf_pages = len(list(out.glob("ref-*.png")))
    print(f"pages: editor {n_ed}, preview {n_pv}, LibreOffice {pdf_pages}, toolbar status: {page.get_by_test_id('page-count').inner_text()!r}")
    page.screenshot(path=str(out / "full.png"))
    page.locator(".docx-stage").first.screenshot(path=str(out / "editor-stage.png"))
    for i in range(min(n_pv, 12)):
        pv.nth(i).screenshot(path=str(out / f"ours-{i + 1:02}.png"))
    from PIL import Image
    for i in range(min(n_pv, pdf_pages, 12)):
        a, c = Image.open(out / f"ours-{i + 1:02}.png"), Image.open(sorted(out.glob("ref-*.png"))[i])
        a = a.resize((c.width, round(a.height * c.width / a.width)))
        s = Image.new("RGB", (c.width * 2 + 10, max(a.height, c.height)), "grey")
        s.paste(a, (0, 0)); s.paste(c, (c.width + 10, 0)); s.save(out / f"pair-{i + 1:02}.png")
    print("wrote", out)
    b.close()
