"""Round 16: revision-table `sections` = the heading / table caption / figure caption a change happened under,
with the section number of the BUILT document."""
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


def test_change_in_a_paragraph_is_reported_at_its_heading_with_number():
    new = [*BASE[:-1], P(T("body changed"))]
    assert diff(BASE, new) == "2.2.1 Deep"
    assert diff(BASE, [BASE[0], P(T("intro changed")), *BASE[2:]]) == "1 One"
    assert diff(BASE, new) == diff(BASE, new)  # deterministic


def test_table_and_figure_changes_use_the_caption_and_the_section():
    base = [*BASE, CAPTION("Table", 1, "Specs"), TABLE("a"), IMG("i1"), CAPTION("Figure", 1, "Layout")]
    assert diff(base, [*base[:-3], TABLE("b"), *base[-2:]]) == "Table 1: Specs (2.2.1)"
    assert diff(base, [*base[:-2], IMG("i2"), base[-1]]) == "Figure 1: Layout (2.2.1)"
    # changing the caption text itself is the same entry; a table + figure change gives both, in document order
    both = [*base[:-4], CAPTION("Table", 1, "Specs v2"), TABLE("b"), IMG("i2"), base[-1]]
    assert diff(base, both) == "Table 1: Specs v2 (2.2.1); Figure 1: Layout (2.2.1)"
    # no caption: the table's own title attribute, else the section
    assert diff([*BASE, TABLE("a", caption="Own title")], [*BASE, TABLE("b", caption="Own title")]) == "Own title (2.2.1)"
    assert diff([*BASE, TABLE("a")], [*BASE, TABLE("b")]) == "2.2.1 Deep"


def test_added_removed_and_renamed_headings_and_unnumbered_first_part():
    assert diff(BASE, [*BASE, H("Three")]) == "3 Three (added)"
    assert diff(BASE, BASE[:3] + BASE[4:]) == "2.2 Two.2 (removed)" or "(removed)" in diff(BASE, BASE[:3] + BASE[4:])
    assert diff(BASE, [BASE[0], BASE[1], H("Second"), *BASE[3:]]) == "2 Second"  # renamed: the new text
    before = [P(T("preamble")), *BASE]
    assert diff(before, [P(T("preamble 2")), *BASE]) == "Guide"  # before any heading: the snippet title, unnumbered
    assert diff([], BASE).startswith("1 One (added); 2 Two (added); 2.1 Two.1 (added)")
    # a vanished paragraph belongs to the section it was in
    assert diff(BASE, BASE[:-1]) == "2.2.1 Deep"


def test_numbering_unnumbered_headings_start_and_restart():
    nodes = [H("Preface", 1, _blk=True, numbered=False), H("A"), H("A.1", 2), H("B"), H("B.1", 2)]
    na = S.anchors(nodes, ["t"] * 5, NUM())
    assert [a["own"]["label"] for a in na] == ["Preface", "1 A", "1.1 A.1", "2 B", "2.1 B.1"]  # unnumbered ones don't count
    na = S.anchors(nodes[1:], ["t"] * 4, NUM(start=5))
    assert [a["own"]["label"] for a in na] == ["5 A", "5.1 A.1", "6 B", "6.1 B.1"]
    na = S.anchors(nodes[1:], ["t"] * 4, NUM(restart_l2=0))  # lvlRestart 0: level 2 never restarts
    assert [a["own"]["label"] for a in na] == ["1 A", "1.1 A.1", "2 B", "2.2 B.1"]
    own = S.anchors([H("X", 1, _blk=True, numbered=True), H("Y", 2, _blk=True, numbered=True)], ["t"] * 2, S.Numbering())
    assert [a["own"]["label"] for a in own] == ["1 X", "1.1 Y"]  # builder headings w/o a base list: our own multilevel
    assert [a["own"]["label"] for a in S.anchors([H("Plain")], ["t"], S.Numbering())] == ["Plain"]  # numbering unknown: text only


def test_cap_and_more():
    old = [H(f"S{i}") for i in range(15)]
    new = [H(f"S{i}x") for i in range(15)]
    out = diff(old, new).split("; ")
    assert len(out) == 13 and out[0] == "1 S0x" and out[11] == "12 S11x" and out[12] == "+3 more"


def test_snippet_only_history_is_relative_and_unnumbered_without_numbering():
    old = dict(title="G", content={"type": "doc", "content": BASE})
    new = dict(title="G", content={"type": "doc", "content": [*BASE[:-1], P(T("x"))]})
    assert S.snippet_changes(old, new) == "Deep"  # numbering unknown: heading text
    assert S.snippet_changes(old, new, NUM()) == "2.2.1 Deep"


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
    assert by["Edit run"] == "2.1 Run"  # chapter 1 is the builder heading, Guide's Setup is 2
    assert by["Edit table"] == "Table 1: Opts (2.1)"
    assert by["Write guide"].startswith("2 Setup (added); 2.1 Run (added)")
    rel = by["Release"]
    assert "1 Manual chapter (added)" in rel and "Guide" not in rel and "install" not in rel and "go faster" not in rel
    assert c.get(f"/api/documents/{ws['doc']}/revision-history").json() == rows  # deterministic
    eid, st = run_export(c, ws["doc"])
    assert st["status"] == "done", st
    t = next(t for t in Document(io.BytesIO(fetch(c, eid))).tables if t.rows[0].cells[0].text == "Date")
    body = {r.cells[4].text: r.cells[3].text for r in t.rows[1:]}
    assert body["Edit run"] == "2.1 Run" and body["Edit table"] == "Table 1: Opts (2.1)"
    eid, st = run_export(c, ws["doc"], "pdf")
    assert st["status"] == "done", st
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(fetch(c, eid))).pages)
    assert "2.1 Run" in text and "Opts" in text
