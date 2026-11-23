"""Milestone 5: variables, dynamic tables, revision history, linked documents, missing-variable errors, sandbox."""
import io
import subprocess

from docx import Document

from app import docx_export
from app.git import repo as git

from .test_base_docs import make_docx, upload
from .test_documents import put
from .test_export import CELL, P, T, fetch, run_export, ws  # noqa: F401  (ws is a fixture)
from .test_git import commit, mk

ROW = lambda *cells: {"type": "tableRow", "content": list(cells)}
CHIP = lambda name, default="", filter=None: {"type": "variable", "attrs": {"name": name, "default": default, "filter": filter}}
CCELL = lambda *inl: {"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1}, "content": [P(*inl)]}


def ref(folder, sid, sha):
    return {"type": "snippet_ref", "folder_id": folder, "snippet_id": sid, "version": {"type": "commit", "value": sha}}


def dyn_snippet(c, folder, title="Team"):
    sid = mk(c, folder, title)
    content = {"type": "doc", "content": [{"type": "table", "attrs": {"dynamicList": "team"}, "content": [
        ROW(CELL("Name"), CELL("Joined")),
        ROW(CCELL(CHIP("row.name", "?", "upper")), CCELL(CHIP("row.joined", "", "date:DD/MM/YYYY")))]}]}
    assert c.put(f"/api/snippets/{sid}/draft", json={"content": content}).status_code == 200
    return sid, commit(c, folder, [sid]).json()["sha"]


def texts(doc):
    return [[c.text for c in r.cells] for t in doc.tables for r in t.rows]


def export_doc(c, did):
    eid, st = run_export(c, did)
    assert st["status"] == "done", st
    return Document(io.BytesIO(fetch(c, eid))), st


TEAM = {"declarations": [{"name": "team", "type": "list", "columns": ["name", "joined"]}],
        "values": {"team": [{"name": "ann", "joined": "2026-03-04"}, {"name": "bob <b>", "joined": "2025-12-24"}, {"name": "cy"}]}}


def test_dynamic_snippet_table_repeats_and_filters(ws):
    c = ws["c"]
    sid, sha = dyn_snippet(c, ws["fa"])
    put(c, ws["doc"], [ref(ws["fa"], sid, sha)], base_document=None, variables=TEAM)
    doc, st = export_doc(c, ws["doc"])
    rows = texts(doc)
    assert [["Name", "Joined"], ["ANN", "04/03/2026"], ["BOB <B>", "24/12/2025"], ["CY", ""]] == rows[-4:], rows
    assert not any("{%" in x or "{{" in x for r in rows for x in r)
    assert st["warnings"] == []


def test_base_doc_table_loop_and_text_variables(ws):
    c = ws["c"]

    def mod(d):
        d.add_paragraph("Project {{ project|upper }} due {{ due|datefmt('YYYY') }} by {{ owner|default('nobody') }}")
        t = d.add_table(rows=3, cols=2)
        for r, (a, b) in zip(t.rows, (("Who", "When"), ("{%tr for p in team %}", ""), ("{{ p.name }}", "{{ p.joined }}"))):
            r.cells[0].text, r.cells[1].text = a, b
        t.add_row().cells[0].text = "{%tr endfor %}"
    base = upload(c, make_docx(mod), "Loop").json()["id"]
    v = {"declarations": TEAM["declarations"] + [{"name": "project"}, {"name": "due", "type": "date"}],
         "values": {**TEAM["values"], "project": "apollo", "due": "2027-01-02"}}
    put(c, ws["doc"], [], base_document={"id": base}, variables=v)
    doc, _ = export_doc(c, ws["doc"])
    assert "Project APOLLO due 2027 by nobody" in [p.text for p in doc.paragraphs]
    assert texts(doc)[0:4] == [["Who", "When"], ["ann", "2026-03-04"], ["bob <b>", "2025-12-24"], ["cy", ""]]


def test_sandbox_neutralizes_python_internals(ws):
    env = docx_export.make_env(set())
    for payload in ("{{ ''.__class__ }}", "{{ ().__class__.__mro__[1].__subclasses__() }}", "{{ x.__init__.__globals__ }}",
                    "{{ cycler.__init__ }}", "{{ 'a' * 100000000 }}", "{{ 9 ** 9 ** 9 }}"):
        try:
            out = env.from_string(payload).render(x=1)
        except Exception as e:  # sandbox errors are fine too: render_texts keeps the text as typed
            out = type(e).__name__
        assert "class 'str'" not in out and "subclasses" not in out and "globals" not in out and len(out) < 200, (payload, out)
    # end to end: in the base doc, in a snippet's text, and inside a variable value (never evaluated a second time)
    c = ws["c"]
    base = upload(c, make_docx(lambda d: d.add_paragraph("A:{{ ''.__class__.__mro__ }}:B")), "Evil").json()["id"]
    sid = mk(c, ws["fa"], "Evil")
    c.put(f"/api/snippets/{sid}/draft", json={"content": {"type": "doc", "content": [P(T("S:{{ ''.__class__ }}:"), CHIP("v"))]}})
    sha = commit(c, ws["fa"], [sid]).json()["sha"]
    put(c, ws["doc"], [ref(ws["fa"], sid, sha)], base_document={"id": base},
        variables={"declarations": [{"name": "v"}], "values": {"v": "{{ 7*7 }}{{ ''.__class__ }}"}})
    doc, _ = export_doc(c, ws["doc"])
    out = "\n".join(p.text for p in doc.paragraphs)
    assert "class 'str'" not in out and "49" not in out and "{{ 7*7 }}" in out


def test_missing_variables_422_before_export(ws):
    c = ws["c"]
    sid, sha = dyn_snippet(c, ws["fa"])
    put(c, ws["doc"], [ref(ws["fa"], sid, sha)], base_document=None,
        variables={"declarations": [{"name": "who"}, {"name": "ok", "default": "d"}], "values": {}})
    n = len(c.get(f"/api/documents/{ws['doc']}/exports").json())
    r = c.get(f"/api/documents/{ws['doc']}/export")
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["error"] == "missing_variables" and d["variables"] == [{"name": "team", "type": "list"}, {"name": "who", "type": "text"}]
    assert len(c.get(f"/api/documents/{ws['doc']}/exports").json()) == n  # no job was created
    put(c, ws["doc"], [ref(ws["fa"], sid, sha)], base_document=None,
        variables={"declarations": [{"name": "who", "default": "me"}], "values": {"team": []}})
    assert run_export(c, ws["doc"])[1]["status"] == "done"


def test_variable_values_are_typed(ws):
    c = ws["c"]
    for decl, val in (({"name": "n", "type": "number"}, "x"), ({"name": "d", "type": "date"}, "tomorrow"),
                      ({"name": "l", "type": "list"}, [{"a": {"nested": 1}}]), ({"name": "l", "type": "list"}, ["x"])):
        r = c.put(f"/api/documents/{ws['doc']}/draft", json={"variables": {"declarations": [decl], "values": {decl["name"]: val}}})
        assert r.status_code == 422, (decl, r.text)


def subjects(repo):
    out = subprocess.run(["git", "-C", str(repo), "log", "--format=%s"], capture_output=True, text=True, check=True).stdout
    return {s for s in out.split("\n") if s}


def test_revision_history_from_git_and_linked_documents(ws):
    c = ws["c"]
    sid2 = mk(c, ws["fa"], "Later")
    c.put(f"/api/snippets/{sid2}/draft", json={"content": {"type": "doc", "content": [P(T("x"))]}})
    v2 = commit(c, ws["fa"], [sid2], "Add later snippet").json()["sha"]
    c.put(f"/api/snippets/{ws['sid']}/draft", json={"content": {"type": "doc", "content": [P(T("changed"))]}})
    commit(c, ws["fa"], [ws["sid"]], "Intro after pin")  # newer than the pin: must not show up
    refs = [ref(ws["fa"], ws["sid"], ws["v1"]), ref(ws["fa"], sid2, v2)]
    put(c, ws["doc"], refs, base_document=None,
        links=[{"title": "Security policy", "doc_id": "SEC-7", "version": "2.1", "url": "https://example.com/sec"}, {"title": "No link"}])
    r = c.post(f"/api/folders/{ws['fb']}/commit", json={"message": "Release one", "document_ids": [ws["doc"]]})
    assert r.status_code == 201, r.text
    c.post(f"/api/folders/{ws['fb']}/tags", json={"name": "v1.0"})
    auto = c.get(f"/api/documents/{ws['doc']}/revision-history").json()
    msgs = [x["message"] for x in auto]
    assert "Release one" in msgs and "Add later snippet" in msgs and "m" in msgs and "Intro after pin" not in msgs
    rel = next(x for x in auto if x["message"] == "Release one")
    assert rel["author"] == "Alice A" and rel["version"] == "v1.0" and len(rel["date"]) == 10
    real = subjects(git.repo_path(ws["org"], ws["fa"])) | subjects(git.repo_path(ws["org"], ws["fb"]))
    assert set(msgs) <= real  # every row is a real commit subject
    doc, _ = export_doc(c, ws["doc"])
    rows = texts(doc)
    h = rows.index(["Version", "Date", "Author", "Description"])
    assert [r[3] for r in rows[h + 1:h + 1 + len(auto)]] == msgs
    i = rows.index(["Title", "ID", "Version", "Link"])
    assert rows[i + 1:i + 3] == [["Security policy", "SEC-7", "2.1", "https://example.com/sec"], ["No link", "", "", ""]]
    # edited rows (stored in the document) replace the auto ones
    put(c, ws["doc"], refs, base_document=None,
        revision_history=[{"version": "9.9", "date": "2020-01-01", "author": "Zed", "message": "Hand written"}])
    rows = texts(export_doc(c, ws["doc"])[0])
    h = rows.index(["Version", "Date", "Author", "Description"])
    assert rows[h + 1] == ["9.9", "2020-01-01", "Zed", "Hand written"]
    assert not any("Release one" in x for r in rows for x in r)
    assert c.put(f"/api/documents/{ws['doc']}/draft", json={"revision_history": [{"message": "x" * 501}]}).status_code == 422


def test_dynamic_export_is_deterministic(ws):
    c = ws["c"]
    sid, sha = dyn_snippet(c, ws["fa"])
    put(c, ws["doc"], [ref(ws["fa"], sid, sha)], base_document=None, variables=TEAM)
    (e1, _), (e2, _) = run_export(c, ws["doc"]), run_export(c, ws["doc"])
    assert fetch(c, e1) == fetch(c, e2)
    assert c.get(f"/api/exports/{e1}").json()["variables"]["values"] == TEAM["values"]
