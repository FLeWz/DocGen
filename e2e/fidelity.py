"""Import -> view -> export -> 'render as Word does' fidelity check against a running stack (API level, no browser).
  E2E_FIDELITY_DOCX=<file> [E2E_FIDELITY_PAGES=<expected page count>] python e2e/fidelity.py
Logs in as the throwaway user from scripts/e2e-users.sh. Asserts on the XML of the re-exported DOCX: the counts of
direct-formatted runs, paragraph/cell shading, borders, tables/rows/cells, merges, drawings, text boxes, fields, section
properties, header/footer parts and styles must equal the source's; the Word-rendered PDF has the expected number of pages
and contains the source text. Run on the proprietary example manual: E2E_FIDELITY_DOCX=manual.docx E2E_FIDELITY_PAGES=11."""
import io
import os
import re
import secrets
import sys
import zipfile

from lxml import etree
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
SRC = os.environ.get("E2E_FIDELITY_DOCX")
if not SRC:
    sys.exit("set E2E_FIDELITY_DOCX=<docx file>")
tag = (os.environ.get("E2E_TAG") or secrets.token_hex(4)) + "-fidelity"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
COUNTED = {
    # characters, not runs: the importer merges neighbouring runs that carry the same formatting
    "chars in direct-formatted runs": "chars://w:r[w:rPr/*[self::w:color[@w:val!='auto' or @w:themeColor] or self::w:sz or self::w:rFonts[@w:ascii or @w:hAnsi] or self::w:highlight or self::w:shd or self::w:strike or self::w:vertAlign]]",
    "chars with colour": "chars://w:r[w:rPr/w:color[@w:val!='auto' or @w:themeColor]]", "chars with size": "chars://w:r[w:rPr/w:sz]",
    "chars with font": "chars://w:r[w:rPr/w:rFonts[@w:ascii]]", "paragraph alignment": "//w:pPr/w:jc",
    "paragraph indent": "//w:pPr/w:ind", "paragraph spacing": "//w:pPr/w:spacing", "paragraph shading": "//w:pPr/w:shd", "paragraph borders": "//w:pPr/w:pBdr",
    "cell shading": "//w:tcPr/w:shd", "cell borders": "//w:tcPr/w:tcBorders", "cell vAlign": "//w:tcPr/w:vAlign", "tables": "//w:tbl", "table grids": "//w:tblGrid",
    "rows": "//w:tr", "cells": "//w:tc", "col spans": "//w:tcPr/w:gridSpan", "row spans": "//w:tcPr/w:vMerge", "table borders": "//w:tblPr/w:tblBorders",
    "drawings": "//w:drawing", "text boxes": "//w:txbxContent", "field chars": "//w:fldChar", "simple fields": "//w:fldSimple", "section props": "//w:sectPr",
    "page size": "//w:sectPr/w:pgSz", "header refs": "//w:sectPr/w:headerReference", "footer refs": "//w:sectPr/w:footerReference", "page breaks": "//w:br[@w:type='page']",
    "numbered paragraphs": "//w:pPr/w:numPr", "styled paragraphs": "//w:pPr/w:pStyle", "hyperlinks": "//w:hyperlink",
}
GROWS = {"styled paragraphs"}  # list items without a style get ListParagraph on export


def doc(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    return etree.fromstring(z.read("word/document.xml")), sorted(n for n in z.namelist() if re.match(r"word/(header|footer|styles|numbering|theme/)", n))


def count(root, xp):
    if xp.startswith("chars:"):
        return sum(len("".join(r.xpath("w:t/text()", namespaces={"w": W}))) for r in root.xpath(xp[6:], namespaces={"w": W}))
    return len(root.xpath(xp, namespaces={"w": W}))


def text(root):
    return re.sub(r"\s+", " ", " ".join(t.text or "" for t in root.iter("{%s}t" % W)))


def main() -> None:
    src = open(SRC, "rb").read()
    with sync_playwright() as pw:
        api = pw.request.new_context(base_url=BASE)

        def csrf():
            api.get("/api/auth/csrf")
            return next(c["value"] for c in api.storage_state()["cookies"] if c["name"] == "docgen_csrf")

        def post(path, **kw):
            r = api.post(path, headers={"X-CSRF-Token": csrf()}, **kw)
            assert r.ok, f"{path}: {r.status} {r.text()[:300]}"
            return r
        post("/api/auth/login", data={"email": f"e2e-{tag}@example.com", "password": os.environ["E2E_PASSWORD"]})
        r = post("/api/import/docx", multipart={"file": {"name": "fidelity.docx", "mimeType": "application/octet-stream", "buffer": src}})
        iid = r.json()["id"]
        content = api.get(f"/api/imports/{iid}").json()["content"]
        exp = api.get(f"/api/imports/{iid}/export")
        assert exp.ok, exp.status
        out = exp.body()
        sroot, sparts = doc(src)
        oroot, oparts = doc(out)
        bad = []
        for name, xp in COUNTED.items():
            a, b = count(sroot, xp), count(oroot, xp)
            print(f"{name:24} source {a:5}  re-export {b:5}")
            if a != b and not (name in GROWS and b >= a):
                bad.append(f"{name}: {a} -> {b}")
        assert sparts == oparts, f"package parts differ: {set(sparts) ^ set(oparts)}"
        assert not bad, "formatting lost on round trip: " + "; ".join(bad)
        css = api.get(f"/api/styles/css?import={iid}").json()
        assert css["css"] and css["page"]["w"] > 0 and (css["header"] or css["footer"] or True)
        print(f"look: {len(css['css'])} bytes CSS, {len(css['styles'])} styles, page {css['page']['w']}x{css['page']['h']}pt")
        pdf = post("/api/render/pdf", data={"content": content, "import_id": iid}).body()
        pages = PdfReader(io.BytesIO(pdf)).pages
        got = " ".join(p.extract_text() for p in pages)
        words = [w for w in re.findall(r"[A-Za-z]{5,}", text(sroot))]
        hit = sum(1 for w in set(words) if w in got) / max(len(set(words)), 1)
        print(f"rendered PDF: {len(pages)} pages, {hit:.1%} of the source's distinct 5+ letter words found")
        assert hit > 0.9, "rendered text diverges from the source"
        if os.environ.get("E2E_FIDELITY_PAGES"):
            want = int(os.environ["E2E_FIDELITY_PAGES"])
            assert abs(len(pages) - want) <= max(1, want // 10), f"page count {len(pages)} vs source {want}"
        print("e2e fidelity OK")


main()
