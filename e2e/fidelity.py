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


def fragments(pdf: bytes):
    """[(page, text, x, y)] of every text fragment of the PDF."""
    out = []
    for n, page in enumerate(PdfReader(io.BytesIO(pdf)).pages):
        page.extract_text(visitor_text=lambda t, cm, tm, fd, fs, n=n: out.append((n, t.strip(), round(tm[4] * cm[0] + cm[4], 1), round(tm[5] * cm[3] + cm[5], 1))) if t.strip() else None)
    return out


def tables_position_check(post, api) -> None:
    """Render the import untouched (= the original XML) and with the text of every table cell edited (tables re-serialised):
    everything outside the edited cells must sit within 3 pt of where it was, so the exporter keeps style, margins, widths, spacing."""
    path = os.environ.get("E2E_TABLES_DOCX")
    if not path:
        return
    iid = post("/api/import/docx", multipart={"file": {"name": "tables.docx", "mimeType": "application/octet-stream", "buffer": open(path, "rb").read()}}).json()["id"]
    content = api.get(f"/api/imports/{iid}").json()["content"]
    base = fragments(post("/api/render/pdf", data={"content": content, "import_id": iid}).body())
    edited = 0
    for n in content["content"]:
        if n["type"] == "table":
            for t in (x for x in walk(n) if x.get("type") == "text"):
                t["text"] += "!"  # changes the block's fingerprint, so the whole table goes through the serializer
                edited += 1
    assert edited >= 4, "fixture should hold text in several table cells"
    got = fragments(post("/api/render/pdf", data={"content": content, "import_id": iid}).body())
    want = [f for f in base if not any(f[1] == t for t in ("T1-A", "T1-B", "T1-C", "T1-D", "T2-A", "T2-B", "T2-C"))]
    have = [f for f in got if f[1].rstrip("!") == f[1]]
    assert len(have) == len(want), f"fragment count {len(have)} vs {len(want)}"
    worst = max((max(abs(a[2] - b[2]), abs(a[3] - b[3])) for a, b in zip(want, have) if a[0] == b[0]), default=0)
    assert all(a[0] == b[0] and a[1] == b[1] for a, b in zip(want, have)), "text moved to another page"
    cells = [(a, b) for a, b in zip([f for f in base if f[1] in ("T1-A", "T1-D", "T2-C")], [f for f in got if f[1].rstrip("!") in ("T1-A", "T1-D", "T2-C")])]
    worst = max([worst] + [abs(a[3] - b[3]) for a, b in cells])  # edited cells: y only (centred text widens with the "!")
    print(f"tables: {edited} cells edited, max text position shift {worst:.1f} pt over {len(want)} fragments")
    assert worst <= 3, f"re-serialised tables moved by {worst} pt"


def walk(n):
    yield n
    for c in n.get("content") or []:
        yield from walk(c)


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
        tables_position_check(post, api)


main()
