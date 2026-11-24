"""Base documents that work: variables found in cover/header/footer, import -> base keeps the front matter, build/export
applies the base (parts, sections, heading numbering), field repair, base-doc variable declarations."""
import io
import re
import time
import zipfile

from lxml import etree

from app import base_docs, docx_export, docx_vars
from app.docx_vars import W

from .conftest import new_client, signup
from .test_base_docs import DOCX, rezip
from .test_documents import put, ref
from .test_git import commit, mk, save
from .test_import import imp, zread
from .test_tables_sections import NS, pkg
from .fixtures.cover import HEADERS, PNG, build as source, p, tx


def root(data, name):
    return etree.fromstring(zread(data, name))


def plain(data, name):
    return "".join(t.text or "" for t in root(data, name).iter(W + "t"))


def team(client):
    signup(client, "owner@x.co", "Acme", name="Olga")
    return client


def imported(c):
    r = imp(c, source(), "cover.docx")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def wait(c, eid):
    for _ in range(200):
        s = c.get(f"/api/exports/{eid}").json()
        if s["status"] not in ("queued", "running"):
            return s
        time.sleep(0.1)


def build_doc(c, base_id, blocks=None, **kw):
    fo = c.post("/api/folders", json={"name": "F"}).json()["id"]
    sid = mk(c, fo, "Chap")
    save(c, sid, "chapter text")
    commit(c, fo, [sid], "v1")
    did = c.post("/api/documents", json={"folder_id": fo, "title": "My Doc"}).json()["id"]
    put(c, did, blocks or [{"type": "heading", "text": "Top", "level": 1, "styleId": "Heading1", "numbered": True},
                           ref(fo, sid, "latest")], base_document={"id": base_id}, **kw)
    return did


def export_docx(c, did, fmt="docx"):
    r = c.get(f"/api/documents/{did}/export?format={fmt}")
    assert r.status_code == 202, r.text
    s = wait(c, r.json()["export_id"])
    assert s["status"] == "done", s
    return c.get(f"/api/exports/{r.json()['export_id']}/download").content, s


# ---- detection -------------------------------------------------------------------------------------

def test_candidates_are_values_not_structure(client):
    c = team(client)
    iid = imported(c)
    got = c.get(f"/api/imports/{iid}/variables").json()
    by = {x["value"]: x for x in got["candidates"]}
    assert by["Acme Spec"]["name"] == "doc.title" and by["Acme Spec"]["locs"] == {"cover": 1}
    assert by["1.2"]["name"] == "doc.version" and by["Project Falcon"]["name"] == "subtitle" and not by["Project Falcon"]["include"]  # plain text: only Title style is on by default
    assert by["Old Title"]["name"] == "gone_bookmark" and by["Old Title"]["locs"] == {"header": 1}  # REF to a missing bookmark
    assert by["Stale Title"]["name"] == "doc.title"
    # never: bookmarked REF (source exists), PAGE/NUMPAGES, TOC, table text, headings/body
    assert not {"Introduction 1", "plain table text", "Notes", "Intro", "This body text stays editable content.", "1", "9"} & set(by)


def test_name_and_kind_heuristics():
    assert docx_vars.suggest("formtext", "Document status:", "Draft", False) == "document_status"  # no 'doc' prefix stripping
    assert docx_vars.VERSION_RE.fullmatch("Revision 3") and docx_vars.VERSION_RE.fullmatch("1.2.0") and not docx_vars.VERSION_RE.fullmatch("Revision history")
    assert docx_vars.suggest("field", "Sl_DocumentTitle_Copy_1", "x", False) == "doc.title"
    assert docx_vars.suggest("field", "SL_DocRevision", "x", False) == "doc.version" and docx_vars.suggest("field", "Company", "x", False) == "org.name"


def test_apply_import_keeps_formatting_and_declares_defaults(client):
    c = team(client)
    iid = imported(c)
    items = [{"value": "Acme Spec", "name": "doc.title"}, {"value": "Project Falcon", "name": "subtitle"},
             {"value": "Old Title", "name": "gone"}]
    r = c.post(f"/api/imports/{iid}/variables", json={"items": items})
    assert r.status_code == 200 and r.json()["replaced"] == 3, r.text
    assert r.json()["declared"] == [{"name": "subtitle", "type": "text", "default": "Project Falcon"},
                                    {"name": "gone", "type": "text", "default": "Old Title"}]  # built-ins are not declared
    assert c.post(f"/api/imports/{iid}/variables", json={"items": [{"value": "nope", "name": "x"}]}).status_code == 422
    assert c.post(f"/api/imports/{iid}/variables", json={"items": [{"value": "a", "name": "bad name"}]}).status_code == 422
    again = {x["value"] for x in c.get(f"/api/imports/{iid}/variables").json()["candidates"]}
    assert not {"Acme Spec", "Project Falcon", "Old Title"} & again and not any("{{" in v for v in again)  # placeholders are not values
    out = c.get(f"/api/imports/{iid}/export").content  # the import's own export fills the placeholders
    body = plain(out, "word/document.xml")
    assert body.startswith("cover") and "{{" not in body and "Project Falcon" in body and "Acme Spec" not in body  # doc.title = import title
    assert "REF Gone_Bookmark" not in zread(out, "word/header2.xml").decode()  # the broken REF became text
    assert "Old Title" in plain(out, "word/header2.xml") and "{{" not in plain(out, "word/header2.xml")
    assert b"DocTitle" in zread(out, "word/document.xml")  # bookmark untouched


# ---- import -> base keeps the front matter ---------------------------------------------------------

def base_of(c, iid, name="B"):
    r = c.post(f"/api/imports/{iid}/base-document", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["base_document_id"], c.get(f"/api/base-documents/{r.json()['base_document_id']}/download").content


def test_use_as_base_keeps_cover_toc_sections_and_cuts_only_the_body(client):
    c = team(client)
    iid = imported(c)
    c.post(f"/api/imports/{iid}/variables", json={"items": [{"value": "Acme Spec", "name": "doc.title"}, {"value": "Project Falcon", "name": "subtitle"}]})
    bid, base = base_of(c, iid)
    doc = zread(base, "word/document.xml").decode()
    assert doc.count("{{ CONTENT }}") == 1 and "<w:t>Intro</w:t>" not in doc and "This body text" not in doc
    assert "{{ doc.title }}" in doc and "{{ subtitle }}" in doc and "Table of Contents" in doc  # cover + TOC verbatim
    assert doc.count("<w:sectPr") == 2 and "w:titlePg" in doc and "DocTitle" in doc and "Document revision:" in doc
    for part in ("header1", "header2", "footer1"):
        assert zread(base, f"word/{part}.xml")
    assert zread(base, "word/media/logo.png") == PNG
    v = c.get(f"/api/base-documents/{bid}/versions").json()[0]
    assert v["placeholders"]["content"] and [d["name"] for d in v["variables"]] == ["subtitle"]
    assert v["variables"][0]["default"] == "Project Falcon"


def test_author_placed_slot_wins_over_auto_cut(client):
    c = team(client)
    iid = imported(c)
    content = c.get(f"/api/imports/{iid}").json()["content"]
    blocks = content["content"]
    k = next(i for i, b in enumerate(blocks) if b["type"] == "heading" and b["attrs"]["level"] == 2)
    blocks.insert(k, {"type": "paragraph", "attrs": {"slot": "CONTENT"}, "content": [{"type": "text", "text": "{{ CONTENT }}"}]})
    assert c.put(f"/api/imports/{iid}", json={"content": content}).status_code == 200
    _, base = base_of(c, iid)
    doc = plain(base, "word/document.xml")
    assert doc.count("{{ CONTENT }}") == 1 and "Intro" in doc and "Scope" in doc and doc.index("Intro") < doc.index("{{ CONTENT }}") < doc.index("Scope")


# ---- build/export applies the base ---------------------------------------------------------------

def test_built_document_carries_the_base_and_heading_numbering_of_the_base(client):
    c = team(client)
    iid = imported(c)
    c.post(f"/api/imports/{iid}/variables", json={"items": [{"value": "Acme Spec", "name": "doc.title"}, {"value": "Old Title", "name": "doc.title"}]})
    bid, base = base_of(c, iid)
    did = build_doc(c, bid)
    out, st = export_docx(c, did)
    for part in ("word/styles.xml", "word/header2.xml", "word/footer1.xml"):  # untouched or only filled in
        assert part in zipfile.ZipFile(io.BytesIO(out)).namelist()
    assert etree.tostring(root(out, "word/styles.xml"), method="c14n") == etree.tostring(root(base, "word/styles.xml"), method="c14n")
    names = zipfile.ZipFile(io.BytesIO(out)).namelist()
    assert {"word/header1.xml", "word/header2.xml", "word/footer1.xml", "word/media/logo.png"} <= set(names)
    d = root(out, "word/document.xml")
    assert len(d.findall(f".//{W}sectPr")) == 2 and d.find(f".//{W}titlePg") is not None
    text = plain(out, "word/document.xml")
    assert text.startswith("My Doc") and "{{" not in text and "chapter text" in text  # cover filled with doc.title
    assert "My Doc" in plain(out, "word/header2.xml") and "{{" not in plain(out, "word/header2.xml")
    assert "My Doc" in plain(out, "word/header1.xml") and "REF DocTitle" in zread(out, "word/header1.xml").decode()  # live REF, bookmark kept
    # headings: the block heading uses the base's Heading1 numbering (no own numPr/indent), same pPr as base styles
    heads = [x for x in d.iter(W + "p") if (x.find(f"{W}pPr/{W}pStyle") is not None and x.find(f"{W}pPr/{W}pStyle").get(W + "val") == "Heading1")
             and "Top" in "".join(t.text or "" for t in x.iter(W + "t"))]
    assert len(heads) == 1
    ppr = [etree.QName(e).localname for e in heads[0].find(W + "pPr")]
    assert ppr == ["pStyle"], ppr
    assert len(list(root(out, "word/numbering.xml").iter(W + "abstractNum"))) == 1  # no list of our own next to the base's


def test_numbered_heading_without_style_numbering_gets_own_list_and_unnumbered_is_switched_off(client):
    from docx import Document
    d = Document(io.BytesIO(base_docs.default_docx()))
    b = docx_export.Builder(d, lambda *a: None, [])
    assert not b.style_numbered("Heading1")
    d = Document(io.BytesIO(source()))
    b = docx_export.Builder(d, lambda *a: None, [])
    assert b.style_numbered("Heading1") and b.style_numbered("Heading2") and not b.style_numbered("Title")


# ---- fields at export -------------------------------------------------------------------------------

def test_fix_fields_flattens_dead_refs_keeps_page_fields_updates_title():
    h = docx_vars.parse(f'<w:hdr {NS}>{HEADERS["header2"]}{HEADERS["footer1"]}</w:hdr>'.encode())
    warns: list = []
    docx_vars.fix_fields([h], {"doc.title": "Real Title", "author": "A"}, warns)
    kws = [f["kw"] for f in docx_vars.fields(h.findall(W + "p")[1])]
    assert kws == ["TITLE", "PAGE", "NUMPAGES"] and "REF" not in [f["kw"] for p_ in h.iter(W + "p") for f in docx_vars.fields(p_)]
    assert "Real Title" in docx_vars.text(h) and "Stale Title" not in docx_vars.text(h) and "Old Title" in docx_vars.text(h)
    assert warns == ["field REF Gone_Bookmark: reference source not found, kept the text 'Old Title'"]
    assert "Error" not in docx_vars.text(h)


def test_unresolved_template_variable_is_an_export_error_and_defaults_apply(client):
    c = team(client)
    iid = imported(c)
    items = [{"value": "Project Falcon", "name": "subtitle"}]
    c.post(f"/api/imports/{iid}/variables", json={"items": items})
    bid, base = base_of(c, iid)
    # a hand-written new version with a variable without default
    from docx import Document
    d = Document(io.BytesIO(base))
    d.paragraphs[0].insert_paragraph_before("Customer: {{ customer }}")
    b = io.BytesIO()
    d.save(b)
    assert c.post(f"/api/base-documents/{bid}/versions", files={"file": ("n.docx", b.getvalue(), DOCX)}).status_code == 201
    vs = c.get(f"/api/base-documents/{bid}/versions").json()
    assert [(x["name"], x["default"]) for x in vs[0]["variables"]] == [("customer", None), ("subtitle", "Project Falcon")]  # default of the earlier version kept
    did = build_doc(c, bid)
    r = c.get(f"/api/documents/{did}/export")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "missing_variables" and [m["name"] for m in r.json()["detail"]["variables"]] == ["customer"]
    cur = c.get(f"/api/documents/{did}").json()
    put(c, did, cur["blocks"], base_document=cur["base_document"], variables={"declarations": [], "values": {"customer": "Globex"}})
    out, _ = export_docx(c, did)
    assert "Customer: Globex" in plain(out, "word/document.xml") and "Project Falcon" in plain(out, "word/document.xml")


def test_dynamic_table_in_base_is_declared_editable_and_expanded(client):
    c = team(client)
    body = ('<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>'
            + "".join(f'<w:tr><w:tc><w:p>{tx(a)}</w:p></w:tc><w:tc><w:p>{tx(b)}</w:p></w:tc></w:tr>' for a, b in
                      (("Item", "Qty"), ("{%tr for it in parts %}", ""), ("{{ it.name }}", "{{ it.qty }}"), ("{%tr endfor %}", "")))
            + "</w:tbl>" + p(tx("{{ CONTENT }}")))
    base = pkg(body)
    r = c.post("/api/base-documents", data={"name": "T"}, files={"file": ("t.docx", base, DOCX)})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    assert r.json()["head"]["variables"] == [{"name": "parts", "type": "list", "default": [], "columns": ["name", "qty"]}]
    rows = [{"name": "bolt", "qty": "4"}, {"name": "nut", "qty": "8"}]
    put_r = c.put(f"/api/base-documents/{bid}/variables", json={"declarations": [
        {"name": "parts", "type": "list", "columns": ["name", "qty"], "default": rows}, {"name": "not_in_file", "type": "text"}]})
    assert put_r.status_code == 200 and [d["name"] for d in put_r.json()] == ["parts"]
    assert c.put(f"/api/base-documents/{bid}/variables", json={"declarations": [{"name": "parts", "type": "list", "default": "x"}]}).status_code == 422
    did = build_doc(c, bid)
    out, _ = export_docx(c, did)
    t = plain(out, "word/document.xml")
    assert "bolt" in t and "nut" in t and "{%tr" not in t and "{{" not in t
    # the document overrides the base default
    cur = c.get(f"/api/documents/{did}").json()
    put(c, did, cur["blocks"], base_document=cur["base_document"], variables={"declarations": [], "values": {"parts": [{"name": "washer", "qty": "1"}]}})
    t = plain(export_docx(c, did)[0], "word/document.xml")
    assert "washer" in t and "bolt" not in t


def test_find_variables_on_base_document_makes_new_version(client):
    c = team(client)
    iid = imported(c)
    bid, _ = base_of(c, iid)
    cands = c.get(f"/api/base-documents/{bid}/find-variables").json()
    assert {"Acme Spec", "Project Falcon", "1.2"} <= {x["value"] for x in cands}
    r = c.post(f"/api/base-documents/{bid}/find-variables", json={"items": [{"value": "Project Falcon", "name": "subtitle"}]})
    assert r.status_code == 201 and r.json()["number"] == 2 and r.json()["variables"][0]["default"] == "Project Falcon"
    assert "{{ subtitle }}" in plain(c.get(f"/api/base-documents/{bid}/download").content, "word/document.xml")
    assert c.post(f"/api/base-documents/{bid}/find-variables", json={"items": [{"value": "zzz", "name": "q"}]}).status_code == 422


def test_roles_and_isolation_of_variable_routes(client, outbox):
    from .conftest import add_member
    c = team(client)
    iid = imported(c)
    bid, _ = base_of(c, iid)
    ed = add_member(c, outbox, "ed@x.co", "editor")
    assert ed.get(f"/api/base-documents/{bid}/find-variables").status_code == 403
    assert ed.put(f"/api/base-documents/{bid}/variables", json={"declarations": []}).status_code == 403
    assert ed.get(f"/api/imports/{iid}/variables").status_code == 200
    other = new_client()
    signup(other, "o@y.co", "Other")
    for m, u, b in (("get", f"/api/imports/{iid}/variables", None), ("post", f"/api/imports/{iid}/variables", {"items": []}),
                    ("get", f"/api/base-documents/{bid}/find-variables", None), ("post", f"/api/base-documents/{bid}/find-variables", {"items": []}),
                    ("put", f"/api/base-documents/{bid}/variables", {"declarations": []})):
        assert getattr(other, m)(u, **({"json": b} if b is not None else {})).status_code == 404, u


def test_marked_sections_and_generated_lists_never_become_variables(client):
    c = team(client)
    iid = imported(c)
    cont = c.get(f"/api/imports/{iid}").json()["content"]
    title = next(b for b in cont["content"] if b["type"] == "paragraph" and "Project Falcon" in "".join(t.get("text", "") for t in b.get("content", [])))
    assert "Project Falcon" in {x["value"] for x in c.get(f"/api/imports/{iid}/variables").json()["candidates"]}  # a value before it is marked
    title["attrs"]["sec"] = {"id": "1", "kind": "section", "title": "Cover"}
    cont["content"].insert(0, {"type": "toc", "attrs": {"kind": "toc", "title": "Acme Spec contents", "levels": "1-3", "xml": None}})
    assert c.put(f"/api/imports/{iid}", json={"content": cont}).status_code == 200
    got = {x["value"] for x in c.get(f"/api/imports/{iid}/variables").json()["candidates"]}
    assert "Project Falcon" not in got and "Acme Spec contents" not in got  # structural: marked section / generated list text
