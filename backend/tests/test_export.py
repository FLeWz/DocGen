"""DOCX export: job flow, OOXML output (python-docx), determinism, records, base-doc styles, roles."""
import io
import time
import zipfile

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn

from app import export
from app.git import repo as git

from .conftest import add_member, new_client, signup
from .test_base_docs import make_docx, upload
from .test_documents import put, ref
from .test_editor import PNG
from .test_git import commit, mk

T = lambda text, *marks: {"type": "text", "text": text, **({"marks": [{"type": m} if isinstance(m, str) else m for m in marks]} if marks else {})}
P = lambda *c, style=None: {"type": "paragraph", "attrs": {"styleId": style}, "content": list(c)}
CELL = lambda text, **a: {"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1, **a}, "content": [P(T(text))]}


def snippet_content(image_id):
    return {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 1, "styleId": None}, "content": [T("Overview")]},
        P(T("Hello "), T("bold", "bold"), T(" "), T("it", "italic", "underline"), T(" "),
          T("site", {"type": "link", "attrs": {"href": "https://example.com/x?a=1&b=2"}}), T(" for "),
          {"type": "variable", "attrs": {"name": "client", "default": "Acme", "filter": "upper"}}, T(" & <b>raw</b>")),
        P(T("Callout text"), style="Callout"),
        {"type": "bulletList", "content": [{"type": "listItem", "content": [P(T("one")), {"type": "orderedList", "content": [
            {"type": "listItem", "content": [P(T("nested"))]}]}]}, {"type": "listItem", "content": [P(T("two"))]}]},
        {"type": "orderedList", "content": [{"type": "listItem", "content": [P(T("first"))]},
                                            {"type": "listItem", "content": [P(T("second"))]}]},
        {"type": "orderedList", "content": [{"type": "listItem", "content": [P(T("again"))]}]},
        {"type": "table", "attrs": {"dynamicList": None}, "content": [
            {"type": "tableRow", "content": [CELL("A", colspan=2), CELL("B")]},
            {"type": "tableRow", "content": [CELL("C", rowspan=2), CELL("D"), CELL("E")]},
            {"type": "tableRow", "content": [CELL("F"), CELL("G")]}]},
        {"type": "image", "attrs": {"imageId": image_id, "alt": "a red pixel", "width": None}},
    ]}


def run_export(c, doc_id, fmt="docx"):
    r = c.get(f"/api/documents/{doc_id}/export", params={"format": fmt})
    assert r.status_code == 202, r.text
    eid = r.json()["export_id"]
    for _ in range(100):
        s = c.get(f"/api/exports/{eid}").json()
        if s["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    return eid, s


def fetch(c, eid) -> bytes:
    r = c.get(f"/api/exports/{eid}/download")
    assert r.status_code == 200, r.text
    return r.content


def style_ids(doc):
    return [p._p.pPr.style if p._p.pPr is not None else None for p in doc.paragraphs]


@pytest.fixture
def ws(client, outbox):
    signup(client, "alice@a.co", "Org A", name="Alice <A>")
    org = client.get("/api/orgs/current").json()["id"]
    fa = client.post("/api/folders", json={"name": "Security"}).json()["id"]
    fb = client.post("/api/folders", json={"name": "Product"}).json()["id"]
    img = client.post("/api/images", files={"file": ("x.png", PNG, "image/png")}).json()["id"]
    sid = mk(client, fa, "Intro")
    client.put(f"/api/snippets/{sid}/draft", json={"content": snippet_content(img)})
    v1 = commit(client, fa, [sid]).json()["sha"]
    def mod(d):
        d.styles.add_style("Callout", WD_STYLE_TYPE.PARAGRAPH)
        d.add_paragraph("{{ SLOT:appendix }}")
        d.add_paragraph("Footer line {{ doc.title }} / {{ missing_one }} / {{ doc.version }}")
    base = upload(client, make_docx(mod), "Corp").json()["id"]
    d = client.post("/api/documents", json={"folder_id": fb, "title": "User <Manual> & more"}).json()["id"]
    put(client, d, [{"type": "heading", "text": "Intro chapter", "level": 1, "numbered": True},
                    {"type": "toc"}, ref(fa, sid, "commit", v1), {"type": "page_break"},
                    {"type": "heading", "text": "Extra", "level": 2, "slot": "appendix"}],
        base_document={"id": base}, variables={"declarations": [{"name": "client", "default": "Acme"}], "values": {"client": "Globex"}})
    return dict(c=client, fa=fa, fb=fb, sid=sid, v1=v1, doc=d, base=base, org=org, outbox=outbox)


def test_export_docx_content(ws):
    c = ws["c"]
    eid, st = run_export(c, ws["doc"])
    assert st["status"] == "done", st
    assert st["warnings"] == ["undefined variables: missing_one"]
    data = fetch(c, eid)
    doc = Document(io.BytesIO(data))
    texts = [p.text for p in doc.paragraphs]
    # placeholders replaced, content in order
    assert not any("CONTENT" in t or "SLOT" in t for t in texts)
    assert texts.index("Intro chapter") < texts.index("Overview") < texts.index("Callout text") < texts.index("Extra")
    # built-ins and variables, filters, escaping (text nodes: no markup interpretation)
    assert any(t.startswith("Hello bold it site for GLOBEX & <b>raw</b>") for t in texts)
    assert any(t == "Footer line User <Manual> & more / {{ missing_one }} / draft" for t in texts)
    # style ids from the base doc (not names, not inline fonts)
    ids = dict(zip(texts, style_ids(doc)))
    assert ids["Overview"] == "Heading1" and ids["Callout text"] == "Callout" and ids["Extra"] == "Heading2"
    assert ids["Hello bold it site for GLOBEX & <b>raw</b>"] is None  # Normal = no pStyle
    assert doc.element.body.xpath('.//w:rPr/w:rFonts') == []
    # runs: bold / italic+underline / hyperlink
    p = next(p for p in doc.paragraphs if p.text.startswith("Hello"))
    runs = {r.text: r for r in p.runs}
    assert runs["bold"].bold and runs["it"].italic and runs["it"].underline
    link = p._p.xpath("./w:hyperlink")[0]
    assert doc.part.rels[link.get(qn("r:id"))]._target == "https://example.com/x?a=1&b=2"
    # numbered heading block gets numbering, TOC is a dirty field, page break, updateFields
    h = next(p for p in doc.paragraphs if p.text == "Intro chapter")
    assert h._p.xpath("./w:pPr/w:numPr/w:numId")
    xml = doc.element.xml
    assert 'TOC \\o "1-4"' in xml and 'w:dirty="true"' in xml and 'w:type="page"' in xml
    assert doc.settings.element.find(qn("w:updateFields")).get(qn("w:val")) == "true"
    # lists: bullet vs decimal, distinct restarting num per ordered list, nested level
    def num(t):
        q = next(p for p in doc.paragraphs if p.text == t)._p.xpath("./w:pPr/w:numPr")[0]
        return int(q.find(qn("w:numId")).get(qn("w:val"))), int(q.find(qn("w:ilvl")).get(qn("w:val")))
    (b1, l1), (b2, _), (n1, ln), (o1, _), (o2, _), (o3, _) = (num(t) for t in ("one", "two", "nested", "first", "second", "again"))
    assert b1 == b2 and (l1, ln) == (0, 1) and o1 == o2 and o3 != o1 and len({b1, n1, o1, o3}) == 4
    nr = doc.part.numbering_part.element
    absid = lambda nid: nr.xpath(f'./w:num[@w:numId="{nid}"]/w:abstractNumId/@w:val')[0]
    fmt = lambda nid: nr.xpath(f'./w:abstractNum[@w:abstractNumId="{absid(nid)}"]/w:lvl[@w:ilvl="0"]/w:numFmt/@w:val')[0]
    assert fmt(b1) == "bullet" and fmt(o1) == "decimal"
    assert nr.xpath("./w:abstractNum[last()]/following-sibling::w:abstractNum") == []  # abstractNums precede nums
    # merged cells
    t = doc.tables[0]
    rows = [r._tr.xpath("./w:tc") for r in t.rows]
    assert rows[0][0].xpath("./w:tcPr/w:gridSpan/@w:val") == ["2"]
    assert rows[1][0].xpath("./w:tcPr/w:vMerge/@w:val") == ["restart"] and rows[2][0].xpath("./w:tcPr/w:vMerge")
    assert [c.text for c in t.rows[1].cells][0] == "C" and t.cell(2, 1).text == "F"
    # image embedded with alt text
    assert len(doc.inline_shapes) == 1
    assert doc.element.xpath("//wp:docPr/@descr") == ["a red pixel"]
    assert any(n.startswith("word/media/") for n in zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert doc.core_properties.title == "User <Manual> & more" and doc.core_properties.author == "Alice <A>"


def test_default_base_when_none_pinned_header_footer_and_tables(ws):
    c = ws["c"]
    put(c, ws["doc"], [ref(ws["fa"], ws["sid"], "latest")], base_document=None)
    eid, st = run_export(c, ws["doc"])
    assert st["status"] == "done", st
    doc = Document(io.BytesIO(fetch(c, eid)))
    assert doc.sections[0].header.paragraphs[0].text == "Org A — User <Manual> & more"
    assert "NUMPAGES" in doc.sections[0].footer._element.xml
    assert [p.text for p in doc.paragraphs][0] == "User <Manual> & more"
    assert not any("{%" in t.text for t in [c for tb in doc.tables for r in tb.rows for c in r.cells])  # jinja rows removed
    assert "Heading1" in style_ids(doc) and len(doc.tables) == 3


def test_determinism_and_export_record(ws):
    c = ws["c"]
    e1, s1 = run_export(c, ws["doc"])
    e2, s2 = run_export(c, ws["doc"])
    assert s1["status"] == s2["status"] == "done" and e1 != e2
    assert fetch(c, e1) == fetch(c, e2)  # same pins + variables => same bytes
    recs = c.get(f"/api/documents/{ws['doc']}/exports").json()
    assert len(recs) == 2 and recs[0]["user_id"] and recs[0]["format"] == "docx"
    snip = recs[0]["pins"]["snippets"][0]
    assert snip["sha"] == ws["v1"] and snip["folder_id"] == ws["fa"] and snip["pin"]["type"] == "commit"
    assert recs[0]["pins"]["base"]["id"] == ws["base"] and recs[0]["variables"]["values"] == {"client": "Globex"}
    # re-render from the stored record, even after the snippet moved on
    from app.db import SessionLocal, set_ctx
    from app.models import Export
    from app.scoping import OrgRepo
    with SessionLocal() as db:
        set_ctx(db, org_id=ws["org"])
        spec = OrgRepo(db, Export, ws["org"]).get(__import__("uuid").UUID(e1)).spec
    assert export.render_docx(ws["org"], spec)[0] == fetch(c, e1)


def test_latest_and_tag_are_frozen_at_export_time(ws):
    c = ws["c"]
    c.put(f"/api/snippets/{ws['sid']}/draft", json={"content": {"type": "doc", "content": [P(T("changed later"))]}})
    v2 = commit(c, ws["fa"], [ws["sid"]]).json()["sha"]
    put(c, ws["doc"], [ref(ws["fa"], ws["sid"], "latest")])
    e, st = run_export(c, ws["doc"])
    assert st["pins"]["snippets"][0]["sha"] == v2
    assert "changed later" in [p.text for p in Document(io.BytesIO(fetch(c, e))).paragraphs]


def test_export_errors_and_formats(ws):
    c = ws["c"]
    assert c.get(f"/api/documents/{ws['doc']}/export?format=pdf").status_code == 400  # plugs in with milestone 6
    nob = upload(c, make_docx(content=False), "NoSlot").json()["id"]
    put(c, ws["doc"], [], base_document={"id": nob})
    e, st = run_export(c, ws["doc"])
    assert st["status"] == "error" and "CONTENT" in st["error"]
    assert c.get(f"/api/exports/{e}/download").status_code == 409
    assert c.get("/api/exports/00000000-0000-0000-0000-000000000000").status_code == 404


def test_viewer_can_export_but_not_edit(ws):
    c = ws["c"]
    v = add_member(c, ws["outbox"], "v@a.co", "viewer")
    put(c, ws["doc"], [ref(ws["fa"], ws["sid"], "latest")])
    eid, st = run_export(v, ws["doc"])  # exports the committed head (empty: the draft is the owner's)
    assert st["status"] == "done"
    assert Document(io.BytesIO(fetch(v, eid)))
