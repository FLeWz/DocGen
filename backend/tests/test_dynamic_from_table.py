"""'Make dynamic table' on a table that already has content: header cells -> column variables, body rows -> default values of the
list variable (attrs.dynamicRows), the first body row stays as the formatted row template. Import -> make dynamic (the shape the
editor produces, see frontend/src/editor/dynamic.ts) -> use as base -> build a document without override: the table reads as the
original; with an override the template row (formatting kept) repeats."""
import copy
import io
import zipfile

from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor

from app import docx_vars

from .conftest import signup
from .test_base_docs import make_docx
from .test_documents import put
from .test_export import fetch, run_export

ROWS = [["Ann", "ann@x.org", "Lead\nsecond line"], ["Bob", "bob@x.org", "Dev"], ["Cy", "", "QA"]]


def source(d):
    d.add_paragraph("Cover")
    t = d.add_table(rows=1 + len(ROWS), cols=3)
    t.style = "Table Grid"
    for ci, h in enumerate(("Full name", "E-mail", "Role")):
        t.cell(0, ci).text = h
        t.cell(0, ci).paragraphs[0].runs[0].font.bold = True
    t.rows[0]._tr.get_or_add_trPr().append(t.rows[0]._tr.makeelement(qn("w:tblHeader"), {}))
    for ri, row in enumerate(ROWS, 1):
        for ci, v in enumerate(row):
            c = t.cell(ri, ci)
            c.text = v
            if ci == 0:
                c.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0, 0)  # template formatting that must survive
    d.add_heading("Chapter", 1)


def chip(name, marks=None):
    return {"type": "variable", "attrs": {"name": f"row.{name}", "default": "", "filter": None}, **({"marks": marks} if marks else {})}


def make_dynamic(tbl, name, cols, rows, header_rows=1):
    """What the editor's conversion does, on the imported JSON: header stays, the first body row becomes the template, the rest go."""
    body = tbl["content"][header_rows:]
    tpl = copy.deepcopy(body[0]) if body else None
    for cell, col in zip(tpl["content"], cols):
        first = cell["content"][0]
        marks = next((n.get("marks") for n in first.get("content", []) if n.get("type") == "text"), None)
        cell["content"] = [{"type": "paragraph", "attrs": first.get("attrs"), "content": [chip(col, marks)]}]
    tbl["content"] = tbl["content"][:header_rows] + [tpl]
    tbl["attrs"].update(dynamicList=name, dynamicCols=cols, dynamicRows=rows)


def to_rows(t):
    return [[c.text for c in r.cells] for r in t.rows]


def test_table_decls_validates_and_collects():
    node = {"type": "doc", "content": [{"type": "table", "attrs": {"dynamicList": "people", "dynamicCols": ["a", "b"], "dynamicRows": [{"a": "1", "b": None, "c": 2}]}},
                                       {"type": "table", "attrs": {"dynamicList": "bad", "dynamicRows": [{"x y": "1"}]}},
                                       {"type": "table", "attrs": {"dynamicList": "none"}}]}
    d = docx_vars.table_decls(node)
    assert d == [{"name": "people", "type": "list", "columns": ["a", "b", "c"], "default": [{"a": "1", "b": "", "c": 2}]}]
    assert docx_vars.clean_rows([{"a": "x"}] * 1001) is None and docx_vars.clean_rows("x") is None and docx_vars.clean_rows([]) == []


def test_import_make_dynamic_use_as_base_build_with_defaults_and_override(client, outbox):
    signup(client, "dfm@a.co", "Dfm Org")
    from .test_import import imp
    a = client
    iid = imp(a, make_docx(source, content=False)).json()["id"]
    content = a.get(f"/api/imports/{iid}").json()["content"]
    tbl = next(b for b in content["content"] if b["type"] == "table")
    assert [c["type"] for c in tbl["content"][0]["content"]] == ["tableHeader"] * 3
    cols = ["full_name", "e_mail", "role"]  # what the dialog proposes for the header cells
    rows = [dict(zip(cols, r)) for r in ROWS]
    make_dynamic(tbl, "people", cols, rows)
    assert a.put(f"/api/imports/{iid}", json={"content": content}).status_code == 200

    # the import's own variable declarations carry the list with its defaults
    decl = next(d for d in a.get(f"/api/imports/{iid}/variables").json()["declared"] if d["name"] == "people")
    assert decl["type"] == "list" and decl["columns"] == cols and decl["default"] == rows

    base = a.post(f"/api/imports/{iid}/base-document", json={"name": "People base"})
    assert base.status_code == 201, base.text
    bid = base.json()["base_document_id"]
    v = next(d for d in a.get(f"/api/base-documents/{bid}/versions").json()[0]["variables"] if d["name"] == "people")
    assert v["type"] == "list" and v["columns"] == cols and v["default"] == rows  # editable under Base document > variables

    fo = a.post("/api/folders", json={"name": "F"}).json()["id"]
    did = a.post("/api/documents", json={"folder_id": fo, "title": "D"}).json()["id"]
    put(a, did, [{"type": "heading", "text": "Hi", "level": 1, "numbered": False}], base_document={"id": bid})
    eid, st = run_export(a, did)  # no override: the defaults are the rows
    assert st["status"] == "done", st
    data = fetch(a, eid)
    t = next(t for t in Document(io.BytesIO(data)).tables)
    assert to_rows(t) == [["Full name", "E-mail", "Role"], *ROWS], to_rows(t)
    assert "{%" not in zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
    # formatting: header bold + repeated header row, template (red first column) on every default row
    assert t.cell(0, 0).paragraphs[0].runs[0].font.bold and t.rows[0]._tr.find(qn("w:trPr")) is not None
    for r in t.rows[1:]:
        assert r.cells[0].paragraphs[0].runs[0].font.color.rgb == RGBColor(0xFF, 0, 0)
    assert t.cell(1, 2).paragraphs[0]._p.findall(".//" + qn("w:br"))  # the line break of a multi-line default stays a line break

    put(a, did, [{"type": "heading", "text": "Hi", "level": 1, "numbered": False}], base_document={"id": bid},
        variables={"declarations": [], "values": {"people": [{"full_name": "Zed", "e_mail": "z@x", "role": "Boss"}]}})
    eid, st = run_export(a, did)
    t = next(t for t in Document(io.BytesIO(fetch(a, eid))).tables)
    assert to_rows(t) == [["Full name", "E-mail", "Role"], ["Zed", "z@x", "Boss"]]
    assert t.cell(1, 0).paragraphs[0].runs[0].font.color.rgb == RGBColor(0xFF, 0, 0)


def test_empty_default_and_snippet_tables_use_their_own_rows(client, outbox):
    signup(client, "dfm2@a.co", "Dfm2 Org")
    a = client
    fo = a.post("/api/folders", json={"name": "F"}).json()["id"]
    from .test_git import commit, mk
    from .test_documents import ref
    tpl = {"type": "tableRow", "content": [{"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None},
                                           "content": [{"type": "paragraph", "attrs": {}, "content": [chip("n")]}]}]}
    hdr = {"type": "tableRow", "content": [{"type": "tableHeader", "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None},
                                           "content": [{"type": "paragraph", "attrs": {}, "content": [{"type": "text", "text": "Name"}]}]}]}
    sid = mk(a, fo, "T")
    a.put(f"/api/snippets/{sid}/draft", json={"content": {"type": "doc", "content": [
        {"type": "table", "attrs": {"dynamicList": "names", "dynamicCols": ["n"], "dynamicRows": [{"n": "one"}, {"n": "two"}]}, "content": [hdr, tpl]},
        {"type": "table", "attrs": {"dynamicList": "nobody", "dynamicCols": ["n"], "dynamicRows": []}, "content": [hdr, tpl]}]}})
    sha = commit(a, fo, [sid]).json()["sha"]
    did = a.post("/api/documents", json={"folder_id": fo, "title": "D"}).json()["id"]
    put(a, did, [ref(fo, sid, "commit", sha)])
    eid, st = run_export(a, did)  # neither list is declared nor given: the tables' own defaults count (not a missing variable)
    assert st["status"] == "done", st
    ts = [t for t in Document(io.BytesIO(fetch(a, eid))).tables if t.cell(0, 0).text == "Name"]  # (the default base has its own tables)
    assert to_rows(ts[0]) == [["Name"], ["one"], ["two"]] and to_rows(ts[1]) == [["Name"]]
