"""Round 7: lists of tables/figures in built documents (imported SEQ args), paragraph blocks, header tables wider than the page."""
import io
import re
import zipfile

import pytest
from docx import Document
from lxml import etree
from pypdf import PdfReader

from app import docx_vars
from app.docx_rich import instr_of, seq_label, seq_numbers, toc_entries

from .conftest import signup
from .test_base_docs import make_docx, upload
from .test_documents import put, ref
from .test_export import P, T, fetch, run_export, ws  # noqa: F401  (ws is a fixture)
from .test_git import commit, mk
from .test_pdf import gotenberg, live  # noqa: F401  (gotenberg is a fixture)

CAPTION = lambda n, label, text: P(T(f"{label} "), {"type": "field", "attrs": {"code": "SEQ", "arg": f"{label} \\* ARABIC", "text": str(n)}}, T(f": {text}"), style="Caption")


def base_with_lists(d):
    d.add_paragraph("Tables")
    p = d.add_paragraph()
    docx_vars_add = __import__("app.ooxml", fromlist=["add_field"]).add_field
    docx_vars_add(p, 'TOC \\h \\z \\c "Table"', "old stale entry", dirty=False)


def doc_xml(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()


def test_imported_seq_arg_with_switches_is_a_caption_not_a_skipped_field():
    a = {"code": "SEQ", "arg": "Table \\* ARABIC", "text": "2"}
    assert seq_label(a["arg"]) == "Table" and seq_label("AD \\* ARABIC") == "AD" and seq_label("") == "Figure"
    assert instr_of(a) == ("SEQ Table \\* ARABIC", "2")
    blocks = [CAPTION(7, "Table", "One"), CAPTION(9, "Table", "Two"), CAPTION(3, "Figure", "Fig")]
    assert [t for _, t in toc_entries(blocks, {"kind": "tot", "label": "Table"}, seq_numbers(blocks))] == ["Table 1: One", "Table 2: Two"]


@pytest.fixture
def built(ws):
    c = ws["c"]
    bid = upload(c, make_docx(base_with_lists), name="Lists").json()["id"]
    sid = mk(c, ws["fa"], "Tables")
    c.put(f"/api/snippets/{sid}/draft", json={"content": {"type": "doc", "content": [P(T("before")), CAPTION(5, "Table", "Alpha"), P(T("x")), CAPTION(5, "Table", "Beta")]}})
    sha = commit(c, ws["fa"], [sid]).json()["sha"]
    return dict(ws, sid2=sid, sha2=sha, base=bid)


def test_built_document_has_list_of_tables_with_dirty_flag_and_global_numbers(built):
    c = built["c"]
    put(c, built["doc"], [
        {"type": "toc", "kind": "tot", "title": "List of tables"},
        {"type": "toc", "kind": "tof", "title": "List of figures"},
        ref(built["fa"], built["sid2"], "commit", built["sha2"]), ref(built["fa"], built["sid2"], "commit", built["sha2"])],
        base_document={"id": built["base"]})
    eid, st = run_export(c, built["doc"], "docx")
    assert st["status"] == "done" and not [w for w in st["warnings"] if "skipped" in w], st
    xml = doc_xml(fetch(c, eid))
    assert 'TOC \\h \\z \\c "Table"' in xml and 'TOC \\h \\z \\c "Figure"' in xml
    assert xml.count('w:dirty="true"') >= 3  # our two lists and the base's own list of tables
    text = re.sub(r"<[^>]+>", "", xml)
    assert "Table 1: Alpha" in text and "Table 4: Beta" in text  # numbered over the whole document, not per snippet


@pytest.mark.skipif(not live(), reason="Gotenberg container not reachable")
def test_live_pdf_list_of_tables_is_populated(built):
    c = built["c"]
    put(c, built["doc"], [{"type": "toc", "kind": "tot", "title": "List of tables"}, ref(built["fa"], built["sid2"], "commit", built["sha2"])],
        base_document={"id": built["base"]})
    eid, st = run_export(c, built["doc"], "pdf")
    assert st["status"] == "done", st
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(fetch(c, eid))).pages)
    assert re.search(r"Table 1: Alpha\s*\.{3,}\s*\d", text) and re.search(r"Table 2: Beta\s*\.{3,}\s*\d", text), text
    assert "old stale entry" not in text  # the base's own list was refreshed too


def test_paragraph_block_exports_with_styles_variables_and_is_validated(built):
    c = built["c"]
    body = {"type": "doc", "content": [P(T("Typed directly "), {"type": "variable", "attrs": {"name": "who", "default": "", "filter": None}}, style=None),
                                       {"type": "bulletList", "content": [{"type": "listItem", "content": [P(T("item"))]}]}]}
    blk = {"type": "paragraph", "content": body}
    r = c.put(f"/api/documents/{built['doc']}/draft", json={"blocks": [blk], "variables": {"declarations": [{"name": "who"}], "values": {}}})
    assert r.status_code == 200 and r.json()["blocks"][0]["id"]
    ex = c.get(f"/api/documents/{built['doc']}/export", params={"format": "docx"})
    assert ex.status_code == 422 and ex.json()["detail"]["variables"][0]["name"] == "who"  # variable detection covers the block
    put(c, built["doc"], [blk], variables={"declarations": [{"name": "who"}], "values": {"who": "Ann"}})
    eid, st = run_export(c, built["doc"], "docx")
    text = re.sub(r"<[^>]+>", "", doc_xml(fetch(c, eid)))
    assert "Typed directly Ann" in text and "item" in text
    for bad in ({"type": "doc", "content": [{"type": "table", "content": []}]}, {"type": "doc", "content": [{"type": "opaque"}]},
                {"type": "paragraph"}, {"type": "doc", "content": [P(T("x" * 120_000))]}):
        assert c.put(f"/api/documents/{built['doc']}/draft", json={"blocks": [{"type": "paragraph", "content": bad}]}).status_code == 422


def test_overflowing_header_column_is_dropped_but_fitting_tables_are_kept():
    W = docx_vars.W_NS
    cell = lambda w, t: f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:tc>'
    tbl = lambda cols: (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(cols)}" w:type="dxa"/><w:tblInd w:w="-109" w:type="dxa"/></w:tblPr><w:tblGrid>'
                        + "".join(f'<w:gridCol w:w="{c}"/>' for c in cols) + "</w:tblGrid><w:tr>" + "".join(cell(c, f"c{i}") for i, c in enumerate(cols))
                        + "</w:tr></w:tbl>")
    root = etree.fromstring(f'<w:hdr xmlns:w="{W}">{tbl([4815, 4813, 4813])}{tbl([4815, 4813])}</w:hdr>')
    warns: list = []
    docx_vars.clip_tables(root, 9638, warns)
    cols = [[c.get(f"{{{W}}}w") for c in t.iter(f"{{{W}}}gridCol")] for t in root.iter(f"{{{W}}}tbl")]
    assert cols == [["4815", "4813"], ["4815", "4813"]] and len(warns) == 1
    assert "c2" not in etree.tostring(root).decode() and root.find(f".//{{{W}}}tblW").get(f"{{{W}}}w") == "9628"


def test_pdf_preview_reuses_a_render_with_an_identical_spec(ws, gotenberg):
    c, d = ws["c"], ws["doc"]
    eid, st = run_export(c, d, "pdf")
    assert st["status"] == "done" and len(gotenberg.seen) == 1
    hit = c.get(f"/api/documents/{d}/export", params={"format": "pdf", "reuse": "true"})
    assert hit.status_code == 202 and hit.json()["export_id"] == eid and hit.json()["reused"] and len(gotenberg.seen) == 1
    put(c, d, [{"type": "heading", "text": "Changed", "level": 1, "numbered": False}])  # spec differs -> rendered again
    miss = c.get(f"/api/documents/{d}/export", params={"format": "pdf", "reuse": "true"}).json()
    assert miss["export_id"] != eid and "reused" not in miss
    forced = c.get(f"/api/documents/{d}/export", params={"format": "pdf"}).json()  # no reuse flag: always a new job
    assert forced["export_id"] not in (eid, miss["export_id"])
    assert c.get(f"/api/documents/{d}/export", params={"format": "pdf", "reuse": "true"}).status_code == 202


def test_dynamic_table_marked_in_an_import_becomes_a_base_list_variable_with_repeated_rows(client, outbox):
    signup(client, "dyn@a.co", "Dyn Org")
    from .test_import import imp
    from .test_dynamic import CCELL, CHIP

    def mod(d):
        d.add_paragraph("Cover")
        t = d.add_table(rows=2, cols=2)
        for i, x in enumerate(("Name", "Email", "-", "-")):
            t.cell(i // 2, i % 2).text = x
        d.add_heading("Chapter", 1)
    a = client
    iid = imp(a, make_docx(mod, content=False)).json()["id"]
    got = a.get(f"/api/imports/{iid}").json()
    content = got["content"]
    tbl = next(b for b in content["content"] if b["type"] == "table")
    tbl["attrs"]["dynamicList"] = "team"  # what the ribbon's "Dynamic table" does ...
    tbl["content"][-1]["content"] = [CCELL(CHIP("row.name")), CCELL(CHIP("row.email"))]  # ... with the row template chips
    r = a.put(f"/api/imports/{iid}", json={"content": content})
    assert r.status_code == 200, r.text
    base = a.post(f"/api/imports/{iid}/base-document", json={"name": "Dyn base"})
    assert base.status_code == 201, base.text
    bid = base.json()["base_document_id"]
    v = next(d for d in a.get(f"/api/base-documents/{bid}/versions").json()[0]["variables"] if d["name"] == "team")
    assert v["type"] == "list" and v["columns"] == ["name", "email"]  # appears under the base document's variables
    fo = a.post("/api/folders", json={"name": "F"}).json()["id"]
    did = a.post("/api/documents", json={"folder_id": fo, "title": "D"}).json()["id"]
    put(a, did, [{"type": "heading", "text": "Hi", "level": 1, "numbered": False}], base_document={"id": bid},
        variables={"declarations": [], "values": {"team": [{"name": "ann", "email": "a@x"}, {"name": "bob", "email": "b@x"}, {"name": "cy", "email": "c@x"}]}})
    eid, st = run_export(a, did)
    assert st["status"] == "done", st
    rows = [[c.text for c in r.cells] for t in Document(io.BytesIO(fetch(a, eid))).tables for r in t.rows]
    assert rows == [["Name", "Email"], ["ann", "a@x"], ["bob", "b@x"], ["cy", "c@x"]], rows  # template row repeated, markers gone
