"""WYSIWYG fidelity: direct formatting kept on import, written back on export (import and snippet paths), effective-style
CSS, page setup, header/footer, 'render as Word does' PDF."""
import io
import re
import zipfile

import pytest
from lxml import etree
from pypdf import PdfReader

from app import docx_export, docx_import as D, docx_style, fmt
from app.base_docs import default_docx

from .conftest import add_member, new_client, signup
from .fixtures.fidelity import build
from .test_import import DOCX, imp

SRC = build()
WNS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def doc_xml(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return etree.fromstring(z.read("word/document.xml"))


def count(data, xpath):
    return len(doc_xml(data).xpath(xpath, namespaces={"w": WNS[1:-1]}))


@pytest.fixture
def parsed():
    return D.parse(SRC, lambda b: "00000000-0000-0000-0000-000000000001")[0]


def find(content, pred):
    return [n for n in D.walk(content) if pred(n)]


def test_parse_keeps_run_formatting(parsed):
    direct = find(parsed, lambda n: n.get("text") == "direct ")[0]
    f = next(m["attrs"] for m in direct["marks"] if m["type"] == "fmt")
    assert f == {"strike": True, "color": "00AA00", "font": "Courier New", "size": 28, "hl": "FFFF00"}
    sup = find(parsed, lambda n: n.get("text") == "theme-sup")[0]
    fs = next(m["attrs"] for m in sup["marks"] if m["type"] == "fmt")
    assert fs["va"] == "superscript" and re.fullmatch(r"[0-9A-F]{6}", fs["color"])  # themeColor resolved to a literal
    with zipfile.ZipFile(io.BytesIO(SRC)) as z:
        theme = fmt.load_theme(z, lambda n: etree.fromstring(z.read(n)))
    assert fs["color"] == theme.colors["accent1"]


def test_parse_keeps_paragraph_cell_table_image_attrs(parsed):
    p = find(parsed, lambda n: n["type"] == "paragraph" and n["attrs"].get("pfmt", {}).get("jc") == "center")[0]
    assert p["attrs"]["pfmt"] == {"jc": "center", "left": 720, "first": -360, "before": 240, "after": 120, "line": 360, "lineRule": "auto",
                                  "shd": "EEEEEE", "bdr": {"bottom": {"val": "single", "sz": 8, "color": "FF0000"}}}
    assert find(parsed, lambda n: n["type"] == "paragraph" and n["attrs"].get("styleId") == "CalloutBig")
    assert find(parsed, lambda n: n["type"] == "hardBreak" and n.get("attrs", {}).get("page"))  # page break is native, not opaque
    ol = find(parsed, lambda n: n["type"] == "orderedList")[0]
    assert ol["attrs"]["listStyle"] == "lower-alpha"
    img = find(parsed, lambda n: n["type"] == "image")[0]["attrs"]
    assert (img["width"], img["height"]) == (120, 40)  # 90pt x 30pt in px
    tbl = find(parsed, lambda n: n["type"] == "table")[0]
    assert tbl["attrs"]["tfmt"]["bdr"]["top"] == {"val": "single", "sz": 12, "color": "00FF00"}
    cells = find(tbl, lambda n: n["type"] == "tableCell")
    assert cells[0]["attrs"]["cfmt"] == {"shd": "D9E2F3", "vAlign": "center"}
    assert cells[1]["attrs"]["rowspan"] == 2
    kinds = {n["attrs"]["kind"] for n in find(parsed, lambda n: n["type"] == "opaque")}
    assert kinds == {"field", "text box"}


def png_bytes():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (4, 4)).save(b, "PNG")
    return b.getvalue()


def round_trip(content, base=SRC):
    for n in D.walk(content):  # `orig` would short-circuit to the original XML (test_roundtrip.py); here the serializer is under test
        (n.get("attrs") or {}).pop("orig", None)
    out, warn = D.export(base, content, lambda c, i: png_bytes())
    assert warn == []
    return out


def test_import_round_trip_keeps_formatting(parsed):
    out = round_trip(parsed)
    for xp in ("//w:r/w:rPr/w:color", "//w:r/w:rPr/w:sz", "//w:r/w:rPr/w:rFonts", "//w:r/w:rPr/w:strike", "//w:r/w:rPr/w:vertAlign",
               "//w:pPr/w:jc", "//w:pPr/w:ind", "//w:pPr/w:spacing", "//w:pPr/w:pBdr", "//w:pPr/w:shd", "//w:tcPr/w:shd", "//w:tcPr/w:vAlign",
               "//w:tblPr/w:tblBorders", "//w:br[@w:type='page']", "//w:tc", "//w:drawing", "//w:txbxContent", "//w:fldChar",
               "//w:tcPr/w:vMerge"):
        assert count(out, xp) == count(SRC, xp) > 0, xp
    ext = doc_xml(out).xpath("//wp:extent", namespaces={"wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"})
    assert (ext[0].get("cx"), ext[0].get("cy")) == (str(90 * 12700), str(30 * 12700))
    assert count(out, "//w:sectPr/w:pgSz[@w:w='15840']") == 1  # page setup untouched
    assert count(out, "//w:rPr/w:shd[@w:fill='FFFF00']") == 1  # highlight written back as shading fill
    assert count(out, "//w:tbl//w:tr") == count(SRC, "//w:tbl//w:tr")


def test_schema_order_of_written_properties(parsed):
    out = round_trip(parsed)
    ppr = next(p for p in doc_xml(out).iter(WNS + "pPr") if p.find(WNS + "jc") is not None)
    tags = [fmt.loc(c) for c in ppr]
    assert tags == sorted(tags, key=fmt.PPR_ORDER.index)
    for rpr in doc_xml(out).iter(WNS + "rPr"):
        tags = [fmt.loc(c) for c in rpr if fmt.loc(c) in fmt.RPR_ORDER]
        assert tags == sorted(tags, key=fmt.RPR_ORDER.index), tags


def test_snippet_export_keeps_formatting(parsed):
    """The same content as a snippet on a (different) base document: direct formatting still lands in the DOCX."""
    content = {"type": "doc", "content": [n for n in parsed["content"] if n["type"] != "opaque"]}
    spec = dict(title="t", blocks=[dict(type="snippet_ref", slot="CONTENT", pin=dict(snippet_id="x", sha="0" * 40))],
                variables=dict(declarations=[], values={}), links=[], revision_history=[],
                built_ins={"doc.title": "t", "doc.version": "1", "doc.date": "2024-01-01", "org.name": "O", "author": "A"})
    out, warn = docx_export.build(spec, default_docx(), lambda pin: (content, "s"), lambda pin, slug, iid: png_bytes())
    for xp in ("//w:r/w:rPr/w:color[@w:val='00AA00']", "//w:pPr/w:jc[@w:val='center']", "//w:pPr/w:shd[@w:fill='EEEEEE']",
               "//w:pPr/w:pBdr/w:bottom", "//w:tcPr/w:shd[@w:fill='D9E2F3']", "//w:tcPr/w:vAlign", "//w:tblPr/w:tblBorders/w:top",
               "//w:br[@w:type='page']", "//w:rPr/w:vertAlign[@w:val='superscript']"):
        assert count(out, xp) == 1, xp
    ext = doc_xml(out).xpath("//wp:extent[@cx='%d']" % (90 * 12700), namespaces={"wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"})
    assert len(ext) == 1
    assert count(out, "//w:numPr") >= 1


def test_writer_rejects_junk():
    ppr = etree.Element(WNS + "pPr")
    fmt.write_para(ppr, {"jc": "evil", "shd": "zzzzzz", "left": "x", "bdr": {"top": {"val": "<x>", "sz": 99999, "color": "red"}}, "before": 10 ** 9})
    assert [fmt.loc(c) for c in ppr] == ["pBdr"] and ppr[0][0].get(WNS + "val") == "single" and ppr[0][0].get(WNS + "color") == "auto"
    rpr = etree.Element(WNS + "rPr")
    fmt.write_run(rpr, {"font": "a;b{", "color": "javascript", "size": "x", "va": "up"}, lambda s: True)
    assert len(rpr) == 0


# ---- CSS ----------------------------------------------------------------------------------------

def test_style_css_inheritance_page_header_footer():
    lk = docx_style.look(SRC)
    css = lk["css"]
    big = re.search(r"\.docx \.st-CalloutBig\{([^}]*)\}", css)[1]
    assert "font-weight:bold" in big and "color:#C0392B" in big  # inherited via basedOn Callout
    assert "font-size:20pt" in big and "'Georgia'" in big
    assert "}" not in lk["css"].split("{")[0]
    assert lk["page"]["w"] == 792 and lk["page"]["h"] == 612 and lk["page"]["left"] == 50  # twips -> pt
    assert "HEADER-MARKER" in lk["header"]["xml"] and "FOOTER-MARKER" in lk["footer"]["xml"]
    assert re.fullmatch(r"[^<>]*", css.replace("\n", "")) or "<" not in css  # nothing that could break out of <style>


def test_docdefaults_and_theme_fonts_resolve():
    data = build()
    lk = docx_style.Look(data)
    assert lk.dr.get("font") or lk.dp is not None
    th = lk.theme
    assert th.fonts.get("minor") and th.colors.get("accent1")
    assert docx_style.font_stack("Calibri").startswith("'Calibri', 'Carlito'")


# ---- API ----------------------------------------------------------------------------------------

@pytest.fixture
def ws(client):
    signup(client, "o@x.co", "Acme")
    r = imp(client, SRC, "fid.docx")
    assert r.status_code == 201, r.text
    return client, r.json()["id"]


def test_css_endpoint_import_base_org_and_isolation(ws):
    c, iid = ws
    r = c.get(f"/api/styles/css?import={iid}")
    assert r.status_code == 200 and ".docx .st-CalloutBig" in r.json()["css"]
    assert any(s["style_id"] == "CalloutBig" and s["name"] == "CalloutBig" for s in r.json()["styles"])
    org = c.get("/api/styles/css").json()
    assert ".docx .st-Heading1" in org["css"] and org["page"]["w"] > 500 and org["styles"] is None
    bd = c.post("/api/base-documents", data={"name": "Default"}).json()
    b = c.get(f"/api/styles/css?base_doc={bd['id']}").json()
    assert ".docx .st-Heading1" in b["css"] and b["header"]["xml"]
    other = new_client()
    signup(other, "z@x.co", "Other")
    assert other.get(f"/api/styles/css?import={iid}").status_code == 404
    assert other.get(f"/api/styles/css?base_doc={bd['id']}").status_code == 404
    assert other.post("/api/render/pdf", json={"content": {"type": "doc"}, "import_id": iid}).status_code == 404


def test_render_pdf_import_and_snippet_mode(ws):
    c, iid = ws
    content = c.get(f"/api/imports/{iid}").json()["content"]
    r = c.post("/api/render/pdf", json={"content": content, "import_id": iid})
    assert r.status_code == 200 and r.content.startswith(b"%PDF"), r.text
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    for marker in ("HEADER-MARKER", "FOOTER-MARKER", "Styled", "TEXTBOX-TEXT", "FIELD-RESULT"):
        assert marker in text, marker
    # page size comes from the imported sectPr: US Letter landscape
    box = PdfReader(io.BytesIO(r.content)).pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (792, 612)
    r2 = c.post("/api/render/pdf", json={"content": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello snippet"}]}]}})
    assert r2.status_code == 200 and "Hello snippet" in PdfReader(io.BytesIO(r2.content)).pages[0].extract_text()
    bad = c.post("/api/render/pdf", json={"content": {"type": "doc", "content": [{"type": "opaque", "attrs": {"kind": "x", "xml": "<w:p xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:r><w:drawing xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships' r:id='rId99'/></w:r></w:p>"}}]}})
    assert bad.status_code in (200, 422)
