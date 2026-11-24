"""DOCX import: parse -> edit -> export round trip, opaque blocks, snippet from selection, split, base document,
malicious uploads, roles."""
import io
import time
import zipfile

import pytest
from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from app import docx_import as D
from app.base_docs import validate

from .conftest import add_member, new_client, signup
from .fixtures.sample import build
from .test_base_docs import DOCX, rezip
from .test_git import commit

SAMPLE = build()


def imp(c, data=SAMPLE, fn="sample.docx", **kw):
    return c.post("/api/import/docx", files={"file": (fn, data, DOCX)}, **kw)


def zread(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read(name)


def c14n(data, name):
    return etree.tostring(etree.fromstring(zread(data, name)), method="c14n")


def parts(data, prefix):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return sorted(n for n in z.namelist() if n.startswith(prefix))


@pytest.fixture
def team(client, outbox):
    signup(client, "owner@x.co", "Acme")
    t = {r: add_member(client, outbox, f"{r}@x.co", r) for r in ("admin", "editor", "viewer")}
    t["owner"] = client
    return t


@pytest.fixture
def imported(team):
    r = imp(team["editor"])
    assert r.status_code == 201, r.text
    return team, r.json()["id"]


def export(c, iid):
    r = c.get(f"/api/imports/{iid}/export")
    assert r.status_code == 200, r.text
    return r.content


def test_parse_shapes(imported):
    team, iid = imported
    r = team["editor"].get(f"/api/imports/{iid}").json()
    blocks = r["content"]["content"]
    kinds = [b["type"] for b in blocks]
    assert kinds.count("table") == 2 and kinds.count("image") == 2 and kinds.count("opaque") == 3
    assert {b["attrs"]["kind"] for b in blocks if b["type"] == "opaque"} == {"content control", "text box", "tracked changes"}
    assert any(b["type"] == "paragraph" and b["attrs"]["styleId"] == "Callout" for b in blocks)  # custom style id kept
    h = [b for b in blocks if b["type"] == "heading"]
    assert [(x["attrs"]["level"], x["attrs"]["styleId"]) for x in h] == [(1, "Heading1"), (2, "Heading2"), (1, "Heading1"), (1, "Heading1"), (1, "Heading1")]
    ol = next(b for b in blocks if b["type"] == "orderedList")
    assert ol["attrs"]["numId"] == 20 and len(ol["content"]) == 2  # nested level-1 item lives inside item 1
    assert ol["content"][0]["content"][1]["type"] == "orderedList"
    assert next(b for b in blocks if b["type"] == "bulletList")["attrs"]["numId"] == 21
    t = [b for b in blocks if b["type"] == "table"][1]
    cells = [[(c["attrs"]["colspan"], c["attrs"]["rowspan"]) for c in row["content"]] for row in t["content"]]
    assert cells == [[(2, 1), (1, 1)], [(1, 1), (1, 1), (1, 2)], [(1, 1), (1, 1)]]
    hdr = [b for b in blocks if b["type"] == "table"][0]["content"][0]["content"]
    assert all(c["type"] == "tableHeader" for c in hdr)
    assert any("text box" in w and "not editable" in w for w in r["warnings"])


def test_round_trip_keeps_package_parts(imported):
    team, iid = imported
    out = export(team["viewer"], iid)
    assert validate(out)  # still a sane DOCX
    for part in ("word/styles.xml", "word/numbering.xml", "word/theme/theme1.xml", "word/settings.xml"):
        assert c14n(out, part) == c14n(SAMPLE, part), part
    hf = parts(SAMPLE, "word/header") + parts(SAMPLE, "word/footer")
    assert hf and hf == parts(out, "word/header") + parts(out, "word/footer")
    for n in hf:
        if n.endswith(".xml"):
            assert c14n(out, n) == c14n(SAMPLE, n)
    assert b"HEADER-MARKER" in zread(out, "word/header1.xml") and b"FOOTER-MARKER" in zread(out, "word/footer1.xml")
    # custom style survives with its formatting
    sty = zread(out, "word/styles.xml")
    assert b'w:styleId="Callout"' in sty


def tables(data):
    d = Document(io.BytesIO(data))
    res = []
    for t in d.tables:
        rows = []
        for tr in t._tbl.findall(qn("w:tr")):
            rows.append([(tc.find(qn("w:tcPr") + "/" + qn("w:gridSpan")) is not None and
                          tc.find(qn("w:tcPr")).find(qn("w:gridSpan")).get(qn("w:val")),
                          getattr(tc.find(qn("w:tcPr")).find(qn("w:vMerge")), "attrib", None) is not None
                          if tc.find(qn("w:tcPr")).find(qn("w:vMerge")) is not None else None,
                          "".join(tc.itertext())) for tc in tr.findall(qn("w:tc"))])
        res.append((t._tbl.tblPr.find(qn("w:tblStyle")).get(qn("w:val")), rows))
    return res


def test_round_trip_tables_images_lists_opaque(imported):
    team, iid = imported
    out = export(team["editor"], iid)
    assert tables(out) == tables(SAMPLE)  # style, gridSpan, vMerge and text identical
    assert len(parts(out, "word/media/")) == len(parts(SAMPLE, "word/media/")) == 2
    do, ds = Document(io.BytesIO(out)), Document(io.BytesIO(SAMPLE))
    assert len(do.inline_shapes) == len(ds.inline_shapes) == 2
    num = lambda d: [(p.style.style_id, p._p.pPr.numPr.numId.val, p._p.pPr.numPr.ilvl.val) for p in d.paragraphs if p._p.pPr is not None and p._p.pPr.numPr is not None]
    assert num(do) == num(ds) == [("ListParagraph", 20, 0), ("ListParagraph", 20, 1), ("ListParagraph", 20, 0),
                                  ("ListParagraph", 21, 0), ("ListParagraph", 21, 0)]
    assert [p.style.style_id for p in do.paragraphs if p.text] == [p.style.style_id for p in ds.paragraphs if p.text]
    # opaque XML written back verbatim (same canonical form as in the source)
    for marker in ("SDT-CONTENT-MARKER", "TEXTBOX-MARKER", "INSERTED-MARKER"):
        def frag(d):
            e = next(e for e in d.element.body if marker in "".join(e.itertext()))
            return etree.tostring(e, method="c14n")
        assert frag(do) == frag(ds), marker
    sect = lambda d: etree.tostring(d.element.body.find(qn("w:sectPr")), method="c14n")
    assert sect(do) == sect(ds)


def test_edit_and_export(imported):
    team, iid = imported
    ed = team["editor"]
    doc = ed.get(f"/api/imports/{iid}").json()["content"]
    doc["content"][0]["content"] = [{"type": "text", "text": "Edited heading"}]
    doc["content"].append({"type": "paragraph", "attrs": {"styleId": "Callout"},
                           "content": [{"type": "text", "text": "added", "marks": [{"type": "bold"}]}]})
    r = ed.put(f"/api/imports/{iid}", json={"content": doc, "title": "Renamed"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed"
    assert ed.get(f"/api/imports/{iid}").json()["content"] == doc  # draft persisted in DB
    d = Document(io.BytesIO(export(ed, iid)))
    assert d.paragraphs[0].text == "Edited heading" and d.paragraphs[0].style.style_id == "Heading1"
    assert d.paragraphs[-1].text == "added" and d.paragraphs[-1].style.style_id == "Callout" and d.paragraphs[-1].runs[0].bold
    assert "INSERTED-MARKER" in "".join(d.element.body.itertext())


def test_opaque_revalidated_on_save_and_export(imported):
    team, iid = imported
    ed = team["editor"]
    base = ed.get(f"/api/imports/{iid}").json()["content"]
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    bad = {
        "doctype": f'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><w:p {ns}><w:r><w:t>&e;</w:t></w:r></w:p>',
        "not word": '<html xmlns="http://www.w3.org/1999/xhtml"/>',
        "broken": f"<w:p {ns}>",
        "sectPr": f"<w:sectPr {ns}/>",
        "dde": f'<w:p {ns}><w:r><w:instrText> DDEAUTO c:\\\\windows\\\\system32\\\\cmd.exe </w:instrText></w:r></w:p>',
        "altChunk": f'<w:altChunk {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId1"/>',
        "dangling rel": f'<w:p {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:hyperlink r:id="rId999"/></w:p>',
    }
    for name, xml in bad.items():
        doc = {"type": "doc", "content": base["content"] + [{"type": "opaque", "attrs": {"kind": "x", "xml": xml}}]}
        assert ed.put(f"/api/imports/{iid}", json={"content": doc}).status_code == 422, name
    # a valid fragment goes through and comes back in the export
    ok = {"type": "opaque", "attrs": {"kind": "custom", "xml": f'<w:p {ns}><w:r><w:t>CUSTOM-OPAQUE</w:t></w:r></w:p>'}}
    assert ed.put(f"/api/imports/{iid}", json={"content": {"type": "doc", "content": [ok]}}).status_code == 200
    assert b"CUSTOM-OPAQUE" in zread(export(ed, iid), "word/document.xml")
    assert ed.get(f"/api/imports/{iid}").json()["content"]["content"] == [ok]


def folder(c, name="F"):
    return c.post("/api/folders", json={"name": name}).json()["id"]


def test_snippet_from_selection_keeps_styles_and_exports(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    # blocks 2..4 = Callout paragraph, "Logo:" paragraph, image
    r = ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "Callout bit", "start": 2, "end": 5})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    d = ed.get(f"/api/snippets/{sid}/draft").json()
    assert [b["type"] for b in d["content"]["content"]] == ["paragraph", "paragraph", "image"]
    assert d["content"]["content"][0]["attrs"]["styleId"] == "Callout" and "Callout" in d["styles"]
    assert d["content"]["content"][2]["attrs"]["imageId"]
    # commit (copies the image into the repo), use in a document on a base doc that has the style, export
    assert commit(ed, fo, [sid]).status_code == 201
    ex = ed.post(f"/api/imports/{iid}/base-document", json={"name": "From file"})
    assert ex.status_code == 403  # admin only
    ex = team["admin"].post(f"/api/imports/{iid}/base-document", json={"name": "From file"})
    assert ex.status_code == 201, ex.text
    did = ed.post("/api/documents", json={"folder_id": fo, "title": "Doc"}).json()["id"]
    sha = ed.get(f"/api/snippets/{sid}").json()["sha"]
    from .test_documents import put, ref
    put(ed, did, [ref(fo, sid, "commit", sha)], base_document={"id": ex.json()["base_document_id"]})
    eid = ed.get(f"/api/documents/{did}/export").json()["export_id"]
    for _ in range(100):
        if ed.get(f"/api/exports/{eid}").json()["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert ed.get(f"/api/exports/{eid}").json()["status"] == "done", ed.get(f"/api/exports/{eid}").text
    out = ed.get(f"/api/exports/{eid}/download").content
    dd = Document(io.BytesIO(out))
    p = next(p for p in dd.paragraphs if p.text.startswith("Callout paragraph"))
    assert p.style.style_id == "Callout"
    assert b'w:styleId="Callout"' in zread(out, "word/styles.xml")
    assert len(dd.inline_shapes) == 1
    assert b"HEADER-MARKER" in zread(out, "word/header1.xml")  # the imported file's header travelled with the base doc


def test_opaque_block_in_snippet_exports_when_self_contained(imported):
    team, iid = imported
    ed = team["editor"]
    fo = folder(ed)
    # blocks 18..20 = SDT, text box, tracked change (all without relationships)
    blocks = ed.get(f"/api/imports/{iid}").json()["content"]["content"]
    start = next(k for k, b in enumerate(blocks) if b["type"] == "opaque")
    sid = ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "Opaque", "start": start, "end": start + 3}).json()
    assert sid["warnings"]
    sid = sid["id"]
    commit(ed, fo, [sid])
    sha = ed.get(f"/api/snippets/{sid}").json()["sha"]
    from .test_documents import put, ref
    did = ed.post("/api/documents", json={"folder_id": fo, "title": "Doc"}).json()["id"]
    put(ed, did, [ref(fo, sid, "commit", sha)])
    eid = ed.get(f"/api/documents/{did}/export").json()["export_id"]
    for _ in range(100):
        if ed.get(f"/api/exports/{eid}").json()["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    out = ed.get(f"/api/exports/{eid}/download").content
    text = zread(out, "word/document.xml").decode()
    assert all(m in text for m in ("SDT-CONTENT-MARKER", "TEXTBOX-MARKER", "INSERTED-MARKER"))
    Document(io.BytesIO(out))  # python-docx can still open it


def test_split_by_headings(imported):
    team, iid = imported
    ed = team["editor"]
    p1 = ed.get(f"/api/imports/{iid}/split?level=1").json()
    assert [s["title"] for s in p1] == ["Introduction", "Revision history", "Merged cells", "Opaque things"]
    p2 = ed.get(f"/api/imports/{iid}/split?level=2").json()
    assert [s["title"] for s in p2] == ["Introduction", "Details", "Revision history", "Merged cells", "Opaque things"]
    assert sum(s["blocks"] for s in p2) == len(ed.get(f"/api/imports/{iid}").json()["content"]["content"])
    fo = folder(ed)
    r = ed.post(f"/api/imports/{iid}/split", json={"folder_id": fo, "level": 2})
    assert r.status_code == 201 and len(r.json()) == 5
    snips = ed.get(f"/api/folders/{fo}/snippets").json()
    assert len(snips) == 5 and all(s["has_draft"] and s["head_sha"] is None for s in snips)
    d = ed.get(f"/api/snippets/{r.json()[0]['id']}/draft").json()
    assert d["content"]["content"][0]["type"] == "heading" and d["styles"] and d["title"] == "Introduction"
    assert ed.post(f"/api/imports/{iid}/split", json={"folder_id": fo, "level": 3}).status_code == 422
    assert ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "x", "start": 5, "end": 999}).status_code == 422


def test_use_as_base_document(imported):
    team, iid = imported
    a = team["admin"]
    r = a.post(f"/api/imports/{iid}/base-document", json={"name": "Corporate"})
    assert r.status_code == 201 and r.json()["version"] == 1 and r.json()["placeholders"]["content"] is True
    bid = r.json()["base_document_id"]
    styles = {s["style_id"] for s in a.get(f"/api/base-documents/{bid}").json()["styles"]}
    assert {"Callout", "Heading1", "TableGrid"} <= styles
    dl = a.get(f"/api/base-documents/{bid}/download").content
    assert b"HEADER-MARKER" in zread(dl, "word/header1.xml") and b"FOOTER-MARKER" in zread(dl, "word/footer1.xml")
    assert c14n(dl, "word/styles.xml") == c14n(SAMPLE, "word/styles.xml")
    assert "MARKER" not in zread(dl, "word/document.xml").decode().replace("HEADER-MARKER", "")
    # new version of the same base doc; both or neither argument -> 422
    assert a.post(f"/api/imports/{iid}/base-document", json={"base_document_id": bid}).json()["version"] == 2
    assert a.post(f"/api/imports/{iid}/base-document", json={}).status_code == 422
    assert a.post(f"/api/imports/{iid}/base-document", json={"name": "x", "base_document_id": bid}).status_code == 422
    # usable: a document built on it exports
    fo = folder(a)
    from .test_documents import put
    did = a.post("/api/documents", json={"folder_id": fo, "title": "D"}).json()["id"]
    put(a, did, [{"type": "heading", "text": "Hi", "level": 1, "styleId": "Heading1", "numbered": False}], base_document={"id": bid})
    eid = a.get(f"/api/documents/{did}/export").json()["export_id"]
    for _ in range(100):
        if a.get(f"/api/exports/{eid}").json()["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert a.get(f"/api/exports/{eid}").json()["status"] == "done"


# ---- hostile uploads ----

def test_malicious_uploads_rejected(team):
    ed = team["editor"]
    assert imp(ed, b"not a zip").status_code == 422
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        for n in ("[Content_Types].xml", "word/document.xml"):
            z.writestr(n, b"<a/>")
        z.writestr("word/big.xml", b"<a>" + b"A" * (150 * 1024 * 1024) + b"</a>")
    assert imp(ed, bomb.getvalue()).status_code == 422
    xxe = rezip(SAMPLE, replace={"word/document.xml": b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                                 b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'})
    assert imp(ed, xxe).status_code == 422
    lol = rezip(SAMPLE, extra={"word/evil.xml": b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><l>&b;</l>'})
    assert imp(ed, lol).status_code == 422
    assert imp(ed, rezip(SAMPLE, extra={"word/vbaProject.bin": b"x"})).status_code == 422
    assert imp(ed, b"x" * (26 * 1024 * 1024)).status_code == 413
    assert ed.get("/api/imports").json() == []


def test_parser_blocks_dangerous_fragments_and_survives_odd_docs():
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    doc = f'<w:document {ns}><w:body><w:p><w:r><w:instrText> DDEAUTO calc </w:instrText></w:r></w:p><w:p><w:r><w:t>ok</w:t></w:r></w:p></w:body></w:document>'
    data = rezip(SAMPLE, replace={"word/document.xml": doc.encode()})
    content, warn = D.parse(data, lambda b: None)
    assert "content" not in content["content"][0]  # no visible placeholder
    assert any("blocked" in w for w in warn) and content["content"][1]["type"] == "paragraph"


# ---- roles ----

def test_roles(imported):
    team, iid = imported
    v, e = team["viewer"], team["editor"]
    assert imp(v).status_code == 403
    assert v.put(f"/api/imports/{iid}", json={"content": {"type": "doc", "content": []}}).status_code == 403
    fo = folder(e)
    assert v.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "x", "start": 0, "end": 1}).status_code == 403
    assert v.post(f"/api/imports/{iid}/split", json={"folder_id": fo, "level": 1}).status_code == 403
    assert e.post(f"/api/imports/{iid}/base-document", json={"name": "n"}).status_code == 403
    for url in (f"/api/imports/{iid}", f"/api/imports/{iid}/export", f"/api/imports/{iid}/split?level=1", "/api/imports"):
        assert v.get(url).status_code == 200, url  # viewers read and export
    assert new_client().get("/api/imports").status_code == 401


# ---- selection snippets: large ranges, ranges across section/page boundaries, blocks payload ----

@pytest.fixture(scope="module")
def long_doc():
    from .fixtures.long import build as build_long
    return build_long(700)


def test_selection_large_range_across_section_break(team, long_doc):
    ed = team["editor"]
    iid = imp(ed, long_doc, "long.docx").json()["id"]
    blocks = ed.get(f"/api/imports/{iid}").json()["content"]["content"]
    assert len(blocks) > 700
    brk = next(k for k, b in enumerate(blocks) if b["type"] == "opaque" and b["attrs"]["kind"] == "section break")
    fo = folder(ed)
    for start, end in ((0, len(blocks)), (brk - 3, brk + 3), (5, 650)):  # everything, over the break, hundreds of blocks
        r = ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": f"r{start}", "start": start, "end": end})
        assert r.status_code == 201, r.text
        d = ed.get(f"/api/snippets/{r.json()['id']}/draft").json()
        got = d["content"]["content"]
        assert len(got) == end - start
        assert all("orig" not in (b.get("attrs") or {}) for b in got)
        assert [b["type"] for b in got] == [b["type"] for b in blocks[start:end]]
    assert ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "x", "start": 0, "end": len(blocks) + 1}).status_code == 422


def test_selection_blocks_payload_does_not_need_the_stored_copy(team, long_doc):
    ed = team["editor"]
    iid = imp(ed, long_doc, "long.docx").json()["id"]
    blocks = ed.get(f"/api/imports/{iid}").json()["content"]["content"]
    fo = folder(ed)
    new = [{"type": "paragraph", "attrs": {"styleId": "Callout"}, "content": [{"type": "text", "text": "typed after the last save"}]}] + blocks[:600]
    r = ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "live", "blocks": new})
    assert r.status_code == 201, r.text
    d = ed.get(f"/api/snippets/{r.json()['id']}/draft").json()
    assert len(d["content"]["content"]) == 601 and "Callout" in d["styles"]
    bad = [{"type": "opaque", "attrs": {"kind": "x", "xml": "<w:p xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:r><w:object><w:OLEObject/></w:object></w:r></w:p>"}}]
    assert ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "evil", "blocks": bad}).status_code == 422
    assert ed.post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "none"}).status_code == 422
    assert team["viewer"].post(f"/api/imports/{iid}/selection-snippet", json={"folder_id": fo, "title": "v", "blocks": new[:1]}).status_code == 403


# ---- explicit heading split (round 6) ----

def heading_doc(spec):
    """spec: list of (level, title) or str paragraphs -> docx bytes"""
    d = Document()
    for x in spec:
        d.add_heading(x[1], x[0]) if isinstance(x, tuple) else d.add_paragraph(x)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def split_titles(c, fo, iid, **body):
    r = c.post(f"/api/imports/{iid}/split", json={"folder_id": fo, **body})
    assert r.status_code == 201, r.text
    return [s["title"] for s in r.json()]


def drafts(c, fo):
    return {s["title"]: len(c.get(f"/api/snippets/{s['id']}/draft").json()["content"]["content"]) for s in c.get(f"/api/folders/{fo}/snippets").json()}


def test_split_explicit_headings_merge_preface_and_titles(team):
    ed = team["editor"]
    iid = imp(ed, heading_doc(["intro text", (1, "A"), "a1", (2, "A.1"), "a11", (3, "A.1.1"), "a111", (1, "B"), "b1", (2, "B.1"), "b11"]), "h.docx").json()["id"]
    hs = ed.get(f"/api/imports/{iid}/headings").json()
    assert [(h["level"], h["title"]) for h in hs] == [(1, "A"), (2, "A.1"), (3, "A.1.1"), (1, "B"), (2, "B.1")]
    assert [h["index"] for h in hs] == [1, 3, 5, 7, 9] and [h["blocks"] for h in hs] == [2, 2, 2, 2, 2] and hs[0]["chars"] == len("A") + len("a1")
    idx = {h["title"]: h["index"] for h in hs}
    fo = folder(ed)
    # two non-adjacent headings: unselected A.1/A.1.1 merge into A, B.1 into B; no preface by default
    assert split_titles(ed, fo, iid, headings=[idx["B"], idx["A"]]) == ["A", "B"]
    assert drafts(ed, fo) == {"A": 6, "B": 4}
    # nested subset + preface + custom title (the others keep the heading text)
    fo2 = folder(ed, "F2")
    assert split_titles(ed, fo2, iid, headings=[idx["A.1"], idx["B.1"]], preface=True, titles={idx["A.1"]: "  Renamed ", -1: "Intro"}) == ["Intro", "Renamed", "B.1"]
    assert drafts(ed, fo2) == {"Intro": 3, "Renamed": 6, "B.1": 2}  # preface+A+a1 | A.1..B+b1 | B.1+b11
    # same titles again in a folder: slugs get a suffix, no 409
    assert split_titles(ed, fo2, iid, headings=[idx["A.1"]]) == ["A.1"]
    assert len({s["slug"] for s in ed.get(f"/api/folders/{fo2}/snippets").json()}) == 4
    # old level parameter still works
    assert split_titles(ed, folder(ed, "F3"), iid, level=1) == ["Front matter", "A", "B"]


def test_split_explicit_validation(team):
    ed = team["editor"]
    iid = imp(ed, heading_doc(["p", (1, "A"), "x"]), "h.docx").json()["id"]
    fo = folder(ed)
    for body in ({"headings": [0]}, {"headings": [99]}, {"headings": [-1]}, {"headings": []}, {}, {"level": 1, "headings": [1]},
                 {"headings": [1], "titles": {"x": "y"}}):
        assert ed.post(f"/api/imports/{iid}/split", json={"folder_id": fo, **body}).status_code == 422, body
    assert team["viewer"].post(f"/api/imports/{iid}/split", json={"folder_id": fo, "headings": [1]}).status_code == 403
    assert team["viewer"].get(f"/api/imports/{iid}/headings").status_code == 200
    assert ed.get(f"/api/folders/{fo}/snippets").json() == []  # nothing half-created


def test_split_500_headings(team):
    ed = team["editor"]
    iid = imp(ed, heading_doc([x for i in range(520) for x in ((1 if i % 7 == 0 else 2, f"H{i}"), f"t{i}")]), "big.docx").json()["id"]
    hs = ed.get(f"/api/imports/{iid}/headings").json()
    assert len(hs) == 520
    pick = [h["index"] for h in hs if h["level"] == 1]
    assert len(split_titles(ed, folder(ed), iid, headings=pick)) == len(pick) == 75
    assert split_titles(ed, folder(ed, "all"), iid, headings=[h["index"] for h in hs]) == [h["title"] for h in hs]
    assert ed.post(f"/api/imports/{iid}/split", json={"folder_id": folder(ed, "x"), "headings": list(range(1001))}).status_code == 422
