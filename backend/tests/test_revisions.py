"""Round 9: revision history rows with `sections` and `commit` (from real git commits), the revision table of the editor
(dynamic over revision_history) filled in DOCX and PDF, backward compatible keys."""
import io

from docx import Document
from pypdf import PdfReader

from app import export

from .test_base_docs import make_docx, upload
from .test_documents import put, ref
from .test_dynamic import CHIP, ROW
from .test_export import CELL, P, T, fetch, run_export, ws  # noqa: F401  (ws is a fixture)
from .test_git import commit, mk

COLS = ["date", "version", "author", "sections", "commit"]
CC = lambda *inl: {"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1}, "content": [P(*inl)]}
H = lambda text: {"type": "heading", "attrs": {"level": 1, "styleId": None}, "content": [T(text)]}
REV_TABLE = {"type": "table", "attrs": {"dynamicList": "revision_history", "dynamicCols": COLS}, "content": [
    ROW(*(CELL(h) for h in ("Date", "Revision", "Edited by", "Sections changed", "Commit"))),
    ROW(*(CC(CHIP(f"row.{k}")) for k in COLS))]}


def save_commit(c, folder, sid, content, msg):
    assert c.put(f"/api/snippets/{sid}/draft", json={"content": {"type": "doc", "content": content}}).status_code == 200
    return commit(c, folder, [sid], msg).json()["sha"]


def test_sections_and_commit_per_revision(ws):
    c, fa, fb = ws["c"], ws["fa"], ws["fb"]
    sid = mk(c, fa, "Guide")
    v1 = save_commit(c, fa, sid, [H("Setup"), P(T("install")), H("Usage"), P(T("run"))], "Write guide")
    v2 = save_commit(c, fa, sid, [H("Setup"), P(T("install")), H("Usage"), P(T("run twice")), H("Tips"), P(T("x"))], "Reword usage, add tips")
    base = upload(c, make_docx(lambda d: (d.add_heading("Intro", 1), d.add_paragraph("a"))), "Base R9").json()["id"]
    r = c.post(f"/api/base-documents/{base}/versions", files={"file": ("b.docx", make_docx(lambda d: (d.add_heading("Intro", 1), d.add_paragraph("a2"), d.add_heading("Added part", 1), d.add_paragraph("b"))),
                                                                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"message": "Edit base: add part"})
    assert r.status_code == 201, r.text
    put(c, ws["doc"], [{"type": "heading", "text": "Manual chapter", "level": 1}, ref(fa, sid, "commit", v2)], base_document={"id": base})
    assert c.post(f"/api/folders/{fb}/commit", json={"message": "Release R9", "document_ids": [ws["doc"]]}).status_code == 201
    rows = c.get(f"/api/documents/{ws['doc']}/revision-history").json()
    by = {x["commit"]: x for x in rows}
    assert by["Write guide"]["sections"] == "Guide (Setup, Usage)"
    assert by["Reword usage, add tips"]["sections"] == "Guide (Usage, Tips)"  # only what changed
    assert by["Edit base: add part"]["sections"] == "Intro, Added part" and by["Edit base: add part"]["author"] == "Alice A"
    assert "Upload version 1" not in by  # the first upload is not a revision
    assert "Manual chapter" in by["Release R9"]["sections"] and "Guide" in by["Release R9"]["sections"]
    assert all(x["message"] == x["commit"] and set(x) == {"version", "date", "author", "message", "sections", "commit", "status"} for x in rows)  # old keys stay
    assert all(__import__("re").fullmatch(r"\d\d\.\d\d\.\d{4}", x["date"]) for x in rows)  # round 11: DD.MM.YYYY
    assert rows == c.get(f"/api/documents/{ws['doc']}/revision-history").json()  # deterministic


def test_revision_table_filled_in_docx_and_pdf(ws):
    c, fa = ws["c"], ws["fa"]
    sid = mk(c, fa, "History")
    v = save_commit(c, fa, sid, [H("Document history"), REV_TABLE], "Add revision table")
    sid2 = mk(c, fa, "Body")
    v2 = save_commit(c, fa, sid2, [H("Body part"), P(T("text"))], "Write body")
    put(c, ws["doc"], [ref(fa, sid2, "commit", v2), ref(fa, sid, "commit", v)], base_document=None)
    eid, st = run_export(c, ws["doc"])
    assert st["status"] == "done" and not any("revision_history" in w for w in st["warnings"]), st
    doc = Document(io.BytesIO(fetch(c, eid)))
    t = next(t for t in doc.tables if t.rows[0].cells[0].text == "Date")
    body = [[x.text for x in r.cells] for r in t.rows[1:]]
    assert len(body) >= 2 and not any("{%" in x for r in body for x in r)
    mine = next(r for r in body if r[4] == "Write body")
    assert mine[2] == "Alice A" and mine[3] == "Body (Body part)" and len(mine[0]) == 10
    eid, st = run_export(c, ws["doc"], "pdf")
    assert st["status"] == "done", st
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(fetch(c, eid))).pages)
    for needle in ("Sections changed", "Alice A", "Write body", "Add revision table"):
        assert needle in text, needle
    assert "{{" not in text and "{%" not in text


def test_old_base_revision_table_keeps_working(ws):
    c = ws["c"]  # default base docx: Version/Date/Author/Description rows (message key)
    put(c, ws["doc"], [ref(ws["fa"], ws["sid"], "commit", ws["v1"])], base_document=None)
    eid, st = run_export(c, ws["doc"])
    doc = Document(io.BytesIO(fetch(c, eid)))
    t = next(t for t in doc.tables if t.rows[0].cells[0].text == "Version")
    assert t.rows[1].cells[3].text == "m"


def test_changed_headings_is_deterministic():
    old = [{"type": "heading", "content": [{"type": "text", "text": "A"}]}, {"type": "paragraph", "content": [{"type": "text", "text": "x"}]}]
    new = [{"type": "paragraph"}, *old, {"type": "heading", "content": [{"type": "text", "text": "B"}]}]
    assert export.changed_headings(old, new) == ["", "B"]
    assert export.changed_headings(old, old) == [] and export.changed_headings(None, old) == ["A"]


# ---- round 13: empty rows ----

def _spec(rows, version="abc1234"):
    return {"revision_history": rows, "built_ins": {"doc.version": version, "doc.date": "2026-11-25", "author": "Bob"}}


def test_revision_aliases_and_blank_rows():
    from app.docx_export import revision_rows
    rows = revision_rows(_spec([dict(version="v1", date="2026-11-24", author="Al", message="m", sections="S", commit="m"),
                                dict(version="", date="", author=None, message="  "),  # builder's blank row
                                dict(revision="r2", edited_by="Bo", description="d")]))  # override with the other key set
    assert len(rows) == 2
    a, b = rows
    assert (a["last_edited_date"], a["revision_number"], a["revision"], a["edited_by"], a["pages_paragraphs_affected"], a["change_ref"], a["description"]) \
        == ("24.11.2026", "v1", "v1", "Al", "S", "m", "m")
    assert (b["version"], b["author"], b["message"], b["change_ref"]) == ("r2", "Bo", "d", "d")


def test_revision_rows_empty_history_one_explicit_row():
    from app.docx_export import revision_rows
    rows = revision_rows(_spec([]))
    assert len(rows) == 1 and rows[0]["version"] == "abc1234" and rows[0]["description"] == "No revisions yet" and rows[0]["date"] == "25.11.2026"
    assert len(revision_rows(_spec([dict(version="", date="")]))) == 1  # all-blank rows count as none


def test_hand_made_base_table_with_other_keys_and_two_commits_with_tag(ws):
    c, fb = ws["c"], ws["fb"]
    KEYS = ("last_edited_date", "revision_number", "status", "edited_by", "pages_paragraphs_affected", "change_ref")

    def mod(d):
        t = d.add_table(rows=4, cols=6)
        for cell, h in zip(t.rows[0].cells, ("Last Edited Date", "Revision Number", "Status", "Edited By", "Affected", "Change Ref")):
            cell.text = h
        for cell in t.rows[1].cells[:1]:
            cell.text = "{%tr for row in revision_history %}"
        for cell, k in zip(t.rows[2].cells, KEYS):
            cell.text = "{{ row.%s }}" % k
        t.rows[3].cells[0].text = "{%tr endfor %}"
    base = upload(c, make_docx(mod), "Skylike").json()["id"]
    put(c, ws["doc"], [{"type": "heading", "text": "Chapter", "level": 1}], base_document={"id": base})
    assert c.post(f"/api/folders/{fb}/commit", json={"message": "First", "document_ids": [ws["doc"]]}).status_code == 201
    put(c, ws["doc"], [{"type": "heading", "text": "Chapter", "level": 1}, {"type": "heading", "text": "More", "level": 1}], base_document={"id": base})
    assert c.post(f"/api/folders/{fb}/commit", json={"message": "Second", "document_ids": [ws["doc"]]}).status_code == 201
    assert c.post(f"/api/folders/{fb}/tags", json={"name": "v1.1"}).status_code == 201
    expect = [["First", "Committed"], ["Second", "Tagged"]]
    for fmt in ("docx", "pdf"):
        eid, st = run_export(c, ws["doc"], fmt)
        assert st["status"] == "done", st
        data = fetch(c, eid)
        if fmt == "docx":
            t = next(t for t in Document(io.BytesIO(data)).tables if t.rows[0].cells[0].text == "Last Edited Date")
            body = [[x.text for x in r.cells] for r in t.rows[1:]]
            assert len(body) == 2 and all(len(r[0]) == 10 and r[0][2] == "." for r in body)  # DD.MM.YYYY
            assert [[r[5], r[2]] for r in body] == expect and [r[1] for r in body][1] == "v1.1" and body[0][1] == body[0][1].strip() != ""
            assert all(r[3] == "Alice A" for r in body) and "More" in body[1][4]
        else:
            text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
            for needle in ("v1.1", "Alice A", "First", "Second", "Tagged", "Committed", "More"):
                assert needle in text, needle
