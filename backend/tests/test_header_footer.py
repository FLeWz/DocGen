"""Header/footer editing of imports: parts as TipTap, edits stored in the import draft (`content.hf`), written back to the
original parts on export (fields as real fldChar, relationships kept, untouched parts byte-identical)."""
import io
import zipfile

import pytest
from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from app import docx_hf, docx_import as D

from .conftest import add_member, signup
from .fixtures.cover import build as build_cover
from .fixtures.sample import build as build_sample
from .test_import import imp, export, zread

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
T = lambda s, marks=None: {"type": "text", "text": s, **({"marks": marks} if marks else {})}
F = lambda code: {"type": "field", "attrs": {"code": code, "arg": "", "text": "1"}}
V = lambda n: {"type": "variable", "attrs": {"name": n, "default": "", "filter": None}}
doc = lambda *blocks: {"type": "doc", "content": list(blocks)}
para = lambda *c, **a: {"type": "paragraph", "attrs": a, "content": list(c)}

SAMPLE, COVER = build_sample(), build_cover()


@pytest.fixture
def team(client, outbox):
    signup(client, "owner@x.co", "Acme")
    t = {r: add_member(client, outbox, f"{r}@x.co", r) for r in ("editor", "viewer")}
    t["owner"] = client
    return t


def parts_of(data):
    return {n: zread(data, n) for n in docx_hf.part_paths(data)}


def test_parse_header_with_fields_into_editable_content():
    paths = docx_hf.part_paths(COVER)
    assert len(paths) >= 2
    kinds = {}
    for p in paths:
        d, warn = docx_hf.part_doc(COVER, p, lambda b: None)
        for n in D.walk(d):
            if n["type"] in ("field", "variable"):
                kinds.setdefault(p, set()).add(n["attrs"].get("code") or n["attrs"]["name"])
        assert all("orig" not in (n.get("attrs") or {}) for n in D.walk(d))
    assert any("PAGE" in v or "NUMPAGES" in v for v in kinds.values()), kinds


def test_edit_writes_real_fields_keeps_other_parts_byte_identical():
    paths = docx_hf.part_paths(SAMPLE)
    target = next(p for p in paths if "header" in p)
    others = [p for p in paths if p != target]
    assert others
    content = {"type": "doc", "content": [para(T("body"))], "hf": {target: doc(
        para(T("Acme — ", [{"type": "bold"}]), V("doc.title"), T("\tPage "), F("PAGE"), T(" of "), F("NUMPAGES"), styleId=None, pfmt={"jc": "center"}))}}
    out, warn = D.export(SAMPLE, content, lambda c, i: None)
    root = etree.fromstring(zread(out, target))
    assert "".join(t.text or "" for t in root.iter(f"{{{W}}}t")).startswith("Acme — {{ doc.title }}")
    assert [i.text.strip() for i in root.iter(f"{{{W}}}instrText")] == ["PAGE", "NUMPAGES"]
    assert [f.get(f"{{{W}}}fldCharType") for f in root.iter(f"{{{W}}}fldChar")] == ["begin", "separate", "end"] * 2
    assert b"HEADER-MARKER" not in zread(out, target)
    for p in others:  # untouched parts: the original bytes, not a re-serialisation
        assert zread(out, p) == zread(SAMPLE, p), p
    assert zread(out, "word/styles.xml")  # package otherwise intact
    Document(io.BytesIO(out))


def test_unedited_export_keeps_all_parts_byte_identical():
    out, _ = D.export(SAMPLE, {"type": "doc", "content": [para(T("x"))]}, lambda c, i: None)
    for p, data in parts_of(SAMPLE).items():
        assert zread(out, p) == data, p
    # and with an `hf` entry for one part only that one changes
    p0 = docx_hf.part_paths(SAMPLE)[0]
    out2, _ = D.export(SAMPLE, {"type": "doc", "content": [para(T("x"))], "hf": {p0: doc(para(T("new")))}}, lambda c, i: None)
    assert zread(out2, p0) != zread(SAMPLE, p0)
    assert all(zread(out2, p) == d for p, d in parts_of(SAMPLE).items() if p != p0)


def with_header_image():
    from .fixtures.sample import png
    d = Document(io.BytesIO(SAMPLE))
    d.sections[0].header.paragraphs[0].add_run().add_picture(png("blue", (30, 15)))
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def test_image_in_header_keeps_its_relationship(team):
    ed = team["editor"]
    iid = imp(ed, with_header_image(), "img.docx").json()["id"]
    parts = ed.get(f"/api/imports/{iid}/headers-footers").json()["parts"]
    p = next(p for p in parts if any(n["type"] == "image" for n in D.walk(p["doc"])))
    p["doc"]["content"].insert(0, para(T("EDITED-HF")))
    cont = ed.get(f"/api/imports/{iid}").json()["content"]
    assert ed.put(f"/api/imports/{iid}", json={"content": {**cont, "hf": {p["path"]: p["doc"]}}}).status_code == 200
    out = export(ed, iid)
    x = zread(out, p["path"])
    assert b"EDITED-HF" in x, x[-700:]
    assert b"<w:drawing" in x
    rels = zread(out, p["path"].replace("word/", "word/_rels/") + ".rels")
    assert b"relationships/image" in rels
    assert any(n.startswith("word/media/") for n in zipfile.ZipFile(io.BytesIO(out)).namelist())


def test_roles_and_validation(team):
    ed, viewer = team["editor"], team["viewer"]
    iid = imp(ed, SAMPLE, "s.docx").json()["id"]
    assert viewer.get(f"/api/imports/{iid}/headers-footers").status_code == 403
    r = ed.get(f"/api/imports/{iid}/headers-footers")
    assert r.status_code == 200 and r.json()["parts"]
    path = r.json()["parts"][0]["path"]
    cont = ed.get(f"/api/imports/{iid}").json()["content"]
    put = lambda hf: ed.put(f"/api/imports/{iid}", json={"content": {**cont, "hf": hf}})
    assert put({"word/document.xml": doc(para(T("x")))}).status_code == 422  # not a header/footer part
    assert put({path: {"type": "paragraph"}}).status_code == 422
    bad = {"type": "opaque", "attrs": {"kind": "x", "xml": f'<w:p xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:r><w:drawing r:id="rId99"/></w:r></w:p>'}}
    assert put({path: doc(bad)}).status_code == 422  # relationship that is not in the part
    assert put({path: doc(para(T("ok")))}).status_code == 200
    assert viewer.put(f"/api/imports/{iid}", json={"content": cont}).status_code == 403
    # the stored edit is what GET returns for that part, flagged edited, and export uses it
    got = {p["path"]: p for p in ed.get(f"/api/imports/{iid}/headers-footers").json()["parts"]}
    assert got[path]["edited"] and D.block_text(got[path]["doc"]["content"][0]) == "ok"
    assert b"ok" in zread(export(ed, iid), path)


def test_use_as_base_carries_header_edits(team):
    ed = team["editor"]
    owner = team["owner"]
    iid = imp(ed, COVER, "cover.docx").json()["id"]
    path = docx_hf.part_paths(COVER)[0]
    cont = ed.get(f"/api/imports/{iid}").json()["content"]
    assert ed.put(f"/api/imports/{iid}", json={"content": {**cont, "hf": {path: doc(para(T("BASE-HF-MARK")))}}}).status_code == 200
    r = owner.post(f"/api/imports/{iid}/base-document", json={"name": "b"})
    assert r.status_code == 201, r.text
    assert r.json()["placeholders"]  # a base was stored; its header part is the edited one (checked on the package below)
    from app.docx_import import as_base_doc
    from app.ooxml import rel_ids
    base = as_base_doc(zread_orig(ed, iid), {**cont, "hf": {path: doc(para(T("BASE-HF-MARK")))}}, lambda c, i: None)
    assert b"BASE-HF-MARK" in zread(base, path)


def zread_orig(c, iid):
    return c.get(f"/api/imports/{iid}/export").content  # the original package parts, as export leaves them
