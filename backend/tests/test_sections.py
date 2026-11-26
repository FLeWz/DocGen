"""Rounds 16, 22: revision-table `sections` = heading numbers and `Table N` / `Figure N` of the BUILT document, nothing else."""
import io

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from pypdf import PdfReader

from app import sections as S

from .test_base_docs import make_docx, upload
from .test_documents import put, ref
from .test_dynamic import CHIP, ROW
from .test_export import CELL, P, T, fetch, run_export, ws  # noqa: F401  (ws is a fixture)
from .test_git import commit, mk
from .test_revisions import COLS, CC, REV_TABLE, save_commit

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def numbered_base(start: int = 1, restart_l2=None) -> bytes:
    """Base document whose Heading 1-4 styles carry a multilevel decimal list (Word's 'linked to heading styles')."""
    def mod(d):
        nb = d.part.numbering_part.element
        lv = "".join(f'<w:lvl w:ilvl="{i}"><w:start w:val="{start if i == 0 else 1}"/><w:numFmt w:val="decimal"/>'
                     f'{f"<w:lvlRestart w:val=\"{restart_l2}\"/>" if i == 1 and restart_l2 is not None else ""}'
                     f'<w:pStyle w:val="Heading{i + 1}"/><w:lvlText w:val="%{i + 1}"/></w:lvl>' for i in range(4))
        a = parse_xml(f'<w:abstractNum xmlns:w="{W}" w:abstractNumId="90"><w:multiLevelType w:val="multilevel"/>{lv}</w:abstractNum>')
        first = nb.find(qn("w:num"))
        first.addprevious(a) if first is not None else nb.append(a)
        nb.append(parse_xml(f'<w:num xmlns:w="{W}" w:numId="90"><w:abstractNumId w:val="90"/></w:num>'))
        for i in range(1, 5):
            ppr = d.styles[f"Heading {i}"].element.get_or_add_pPr()
            ppr.append(parse_xml(f'<w:numPr xmlns:w="{W}"><w:ilvl w:val="{i - 1}"/><w:numId w:val="90"/></w:numPr>'))
    return make_docx(mod)


NUM = lambda **kw: S.Numbering(numbered_base(**kw))
H = lambda text, level=1, **a: {"type": "heading", "attrs": {"level": level, "styleId": None, **a}, "content": [T(text)]}
SEQ = lambda label, n: {"type": "field", "attrs": {"code": "SEQ", "arg": label, "text": str(n)}}
CAPTION = lambda label, n, text: P(T(f"{label} "), SEQ(label, n), T(f": {text}"))
IMG = lambda iid: {"type": "image", "attrs": {"imageId": iid, "alt": ""}}
TABLE = lambda cell, **a: {"type": "table", "attrs": a, "content": [ROW(CELL(cell))]}
BASE = [H("One"), P(T("intro")), H("Two"), H("Two.1", 2), H("Two.2", 2), H("Deep", 3), P(T("body"))]


def diff(old, new, num=None, title="Guide"):
    return S.diff_nodes(old, new, [title] * len(old), [title] * len(new), num or NUM())


def test_change_in_a_paragraph_is_reported_as_its_heading_number():
    new = [*BASE[:-1], P(T("body changed"))]
    assert diff(BASE, new) == "2.2.1"
    assert diff(BASE, [BASE[0], P(T("intro changed")), *BASE[2:]]) == "1"
    assert diff(BASE, new) == diff(BASE, new)  # deterministic


def test_table_and_figure_changes_are_label_and_number_only():
    base = [*BASE, CAPTION("Table", 1, "Specs"), TABLE("a"), IMG("i1"), CAPTION("Figure", 1, "Layout")]
    assert diff(base, [*base[:-3], TABLE("b"), *base[-2:]]) == "Table 1"
    assert diff(base, [*base[:-2], IMG("i2"), base[-1]]) == "Figure 1"
    both = [*base[:-4], CAPTION("Table", 1, "Specs v2"), TABLE("b"), IMG("i2"), base[-1]]
    assert diff(base, both) == "Table 1, Figure 1"  # caption text is never printed
    assert diff([*BASE, TABLE("a", caption="Own")], [*BASE, TABLE("b", caption="Own")]) == ""  # no caption paragraph: no number
    assert diff([*BASE, TABLE("a")], [*BASE, TABLE("b")]) == ""


def test_added_removed_renamed_headings_and_unnumbered_parts():
    assert diff(BASE, [*BASE, H("Three")]) == "3"
    assert diff(BASE, BASE[:3] + BASE[4:]) == "2.1"  # removed: the number it had before
    assert diff(BASE, [BASE[0], BASE[1], H("Second"), *BASE[3:]]) == "2"  # renamed: number, not text
    assert diff([P(T("preamble")), *BASE], [P(T("preamble 2")), *BASE]) == ""  # before any heading: nothing to print
    assert diff([], BASE) == "1, 2, 2.1, 2.2, 2.2.1"
    assert diff(BASE, BASE[:-1]) == "2.2.1"  # a vanished paragraph belongs to its section
    assert diff([H("A", 1, _blk=True, numbered=False)], [H("A2", 1, _blk=True, numbered=False)]) == ""  # unnumbered heading
    shifted = [H("New"), *BASE]
    assert diff(BASE, shifted) == "1"  # only the changed block, under its new number


def test_numbering_unnumbered_headings_start_and_restart():
    nodes = [H("Preface", 1, _blk=True, numbered=False), H("A"), H("A.1", 2), H("B"), H("B.1", 2)]
    lab = lambda n, num: [a["own"]["label"] for a in S.anchors(n, ["t"] * len(n), num)]
    assert lab(nodes, NUM()) == [None, "1", "1.1", "2", "2.1"]  # unnumbered ones don't count
    assert lab(nodes[1:], NUM(start=5)) == ["5", "5.1", "6", "6.1"]
    assert lab(nodes[1:], NUM(restart_l2=0)) == ["1", "1.1", "2", "2.2"]  # lvlRestart 0: level 2 never restarts
    assert lab([H("X", 1, _blk=True, numbered=True), H("Y", 2, _blk=True, numbered=True)], S.Numbering()) == ["1", "1.1"]
    assert lab([H("Plain")], S.Numbering()) == [None]  # numbering unknown


def test_cap_and_more():
    old = [H(f"S{i}") for i in range(15)]
    new = [H(f"S{i}x") for i in range(15)]
    out = diff(old, new).split(", ")
    assert len(out) == 13 and out[0] == "1" and out[11] == "12" and out[12] == "+3 more"


def test_snippet_only_history_is_numbers_when_known():
    old = dict(title="G", content={"type": "doc", "content": BASE})
    new = dict(title="G", content={"type": "doc", "content": [*BASE[:-1], P(T("x"))]})
    assert S.snippet_changes(old, new) == ""  # numbering unknown
    assert S.snippet_changes(old, new, NUM()) == "2.2.1"


H2 = lambda text: H(text, 2)


def test_revision_rows_integration_numbers_over_the_built_document(ws):
    c, fa, fb = ws["c"], ws["fa"], ws["fb"]
    base = upload(c, numbered_base(), "Numbered").json()["id"]
    sid = mk(c, fa, "Guide")
    v1 = save_commit(c, fa, sid, [H("Setup"), P(T("install")), H2("Run"), P(T("go")), CAPTION("Table", 1, "Opts"), TABLE("a")], "Write guide")
    v2 = save_commit(c, fa, sid, [H("Setup"), P(T("install")), H2("Run"), P(T("go faster")), CAPTION("Table", 1, "Opts"), TABLE("a")], "Edit run")
    v3 = save_commit(c, fa, sid, [H("Setup"), P(T("install")), H2("Run"), P(T("go faster")), CAPTION("Table", 1, "Opts"), TABLE("b")], "Edit table")
    hist = mk(c, fa, "History")
    hv = save_commit(c, fa, hist, [H("Document history"), REV_TABLE], "Add revision table")
    put(c, ws["doc"], [{"type": "heading", "text": "Manual chapter", "level": 1, "numbered": True}, ref(fa, sid, "commit", v3),
                       ref(fa, hist, "commit", hv)], base_document={"id": base})
    assert c.post(f"/api/folders/{fb}/commit", json={"message": "Release", "document_ids": [ws["doc"]]}).status_code == 201
    rows = c.get(f"/api/documents/{ws['doc']}/revision-history").json()
    by = {r["commit"]: r["sections"] for r in rows}
    assert by["Edit run"] == "2.1"  # chapter 1 is the builder heading, Guide's Setup is 2
    assert by["Edit table"] == "Table 1"
    assert by["Write guide"].startswith("2, 2.1")
    rel = by["Release"]
    assert rel.split(", ")[0] == "1" and "Guide" not in rel and "install" not in rel
    assert c.get(f"/api/documents/{ws['doc']}/revision-history").json() == rows  # deterministic
    eid, st = run_export(c, ws["doc"])
    assert st["status"] == "done", st
    t = next(t for t in Document(io.BytesIO(fetch(c, eid))).tables if t.rows[0].cells[0].text == "Date")
    body = {r.cells[4].text: r.cells[3].text for r in t.rows[1:]}
    assert body["Edit run"] == "2.1" and body["Edit table"] == "Table 1"
    eid, st = run_export(c, ws["doc"], "pdf")
    assert st["status"] == "done", st
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(fetch(c, eid))).pages)
    assert "Table 1" in text
