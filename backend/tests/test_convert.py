"""Round 20: pandoc bridge (markdown <-> TipTap JSON), the convert routes, and LaTeX -> OMML in the DOCX exporter."""
import io
import json
import subprocess
import threading
import zipfile

import pytest
from lxml import etree

from app import docx_import as D, omml, pandoc_bridge as B

from .conftest import add_member, signup
from .fixtures.tables import build as sample_docx

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
IID = "00000000-0000-0000-0000-000000000001"


def T(s, *marks):
    return {"type": "text", "text": s, **({"marks": [{"type": m} if isinstance(m, str) else m for m in marks]} if marks else {})}


def P(*c, s=None, p=None):
    return {"type": "paragraph", "attrs": {"styleId": s, "pfmt": p}, **({"content": list(c)} if c else {})}


def cell(*c, kind="tableCell", cs=1, rs=1):
    return {"type": kind, "attrs": {"colspan": cs, "rowspan": rs, "colwidth": None}, "content": list(c) or [P()]}


def row(*c):
    return {"type": "tableRow", "content": list(c)}


def li(*c):
    return {"type": "listItem", "content": list(c)}


def var(name, default="", filt=None):
    return {"type": "variable", "attrs": {"name": name, "default": default, "filter": filt}}


FMT = {"type": "fmt", "attrs": {"color": "ff0000", "font": None, "size": 28, "hl": None, "strike": True, "va": None, "caps": None, "smallCaps": None, "rStyle": None}}
OPAQUE = {"type": "opaque", "attrs": {"kind": "equation", "xml": "<w:p xmlns:w=\"%s\"><w:r><w:t>x</w:t></w:r></w:p>" % WNS}}
SAMPLE = {"type": "doc", "content": [
    {"type": "heading", "attrs": {"level": 1, "styleId": "Heading1", "pfmt": {"jc": "center"}}, "content": [T("Title")]},
    {"type": "heading", "attrs": {"level": 4, "styleId": None, "pfmt": None}, "content": [T("Deep")]},
    P(T("plain "), T("bold", "bold"), T(" "), T("it", "italic"), T(" "), T("ul", "underline"), T(" "), T("red", FMT), T(" "),
      T("link", {"type": "link", "attrs": {"href": "https://example.com/a?b=1"}}), T(" "), var("doc.title", "Untitled", "upper"), T(" and "),
      {"type": "math", "attrs": {"latex": "x^2+y^2", "display": False}}, {"type": "hardBreak", "attrs": {"page": False}}, T("next"), s="Quote", p={"jc": "center", "left": 720}),
    P(T("Table "), {"type": "field", "attrs": {"code": "SEQ", "arg": "Table", "text": "1"}}, T(": caption"), s="Caption"),
    P(T("note here"), {"type": "footnote", "attrs": {"kind": "footnote", "text": "the note", "n": 1}}, T(" more")),
    P(),
    P({"type": "math", "attrs": {"latex": "\\frac{a}{b}", "display": True}}),
    {"type": "bulletList", "content": [li(P(T("one")), {"type": "orderedList", "attrs": {"start": 1, "listStyle": "lower-alpha"}, "content": [li(P(T("n3"))), li(P(T("n4")))]}), li(P(T("two")))]},
    {"type": "bulletList", "content": [li(P(T("second list")))]},
    {"type": "orderedList", "attrs": {"start": 3}, "content": [li(P(T("three"))), li(P(T("four")))]},
    {"type": "table", "attrs": {"dynamicList": None}, "content": [row(cell(P(T("H1")), kind="tableHeader"), cell(P(T("H2 | pipe")), kind="tableHeader")),
                                                                  row(cell(P(T("a "), var("row.f", "", "upper"))), cell(P(T("b"))))]},
    {"type": "table", "attrs": {"dynamicList": "items"}, "content": [row(cell(P(T("Name")), kind="tableHeader"), cell(P(T("Qty")), kind="tableHeader")),
                                                                     row(cell(P(var("row.name"))), cell(P(var("row.qty"))))]},
    {"type": "table", "attrs": {"dynamicList": None}, "content": [row(cell(P(T("merged")), cs=2)), row(cell(P(T("x"))), cell(P(T("y")), P(T("second paragraph"))))]},
    {"type": "image", "attrs": {"imageId": IID, "alt": "an image", "width": 300, "height": None, "align": "center"}},
    {"type": "toc", "attrs": {"kind": "toc", "title": "", "levels": "1-3", "label": None}},
    {"type": "sectBreak", "attrs": {"brk": "nextPage", "orient": "landscape"}},
    OPAQUE,
    P({"type": "hardBreak", "attrs": {"page": True}}),
    P(T("last")),
]}


def strip(n):
    """structure without TipTap's null/default attrs"""
    if isinstance(n, dict):
        return {k: strip(v) for k, v in n.items() if v not in (None, False, "", [], {}) or k == "text"}
    if isinstance(n, list):
        return [strip(x) for x in n]
    return n


def test_every_node_and_mark_round_trips_from_text_alone():
    r = B.to_text(SAMPLE)
    back = B.from_text(r["text"], {"type": "doc", "content": [OPAQUE]})  # the base only supplies the opaque store
    assert not back["errors"], back["errors"]
    got, want = strip(back["content"]["content"]), strip(SAMPLE["content"])
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == w, (i, g, w)
    assert len(got) == len(want)


def test_syntax_conventions():
    t = B.to_text(SAMPLE)["text"]
    for frag in ['# Title {custom-style="Heading1" jc="center"}', '::: {custom-style="Quote"', '{{ doc.title | default("Untitled") | upper }}',
                 "$x^2+y^2$", "$$\\frac{a}{b}$$", '{field="SEQ" arg="Table"}', "^[the note]", "![an image](docgen-image:", "[]{.pagebreak}",
                 '{toc="toc" levels="1-3"}', '{dynamic="items"}', "{opaque=", "opaque: equation", "+--"]:
        assert frag in t, frag


def test_untouched_text_gives_the_identical_json():
    r = B.to_text(SAMPLE)
    assert B.from_text(r["text"], SAMPLE)["content"] == SAMPLE


def manual():
    return D.parse(sample_docx(), lambda b: IID)[0]


def test_import_round_trip_is_identical_json_and_identical_docx_bytes():
    c = manual()
    r = B.to_text(c)
    back = B.from_text(r["text"], c)
    assert back["content"] == c
    a, _ = D.export(sample_docx(), c, lambda *_: None)
    b, _ = D.export(sample_docx(), back["content"], lambda *_: None)
    assert a == b


def test_one_edit_changes_one_block_and_keeps_neighbours():
    blocks = [{**P(T("alpha"), s="X"), "attrs": {"styleId": "X", "pfmt": None, "orig": {"s": 0, "e": 0, "h": "h0"}}},
              {**P(T("beta"), s="Y"), "attrs": {"styleId": "Y", "pfmt": {"jc": "right", "spacing": {"a": 1}}, "orig": {"s": 1, "e": 1, "h": "h1"}}},
              {**P(T("gamma")), "attrs": {"styleId": None, "pfmt": None, "orig": {"s": 2, "e": 2, "h": "h2"}}}]
    c = {"type": "doc", "content": blocks}
    r = B.to_text(c)
    back = B.from_text(r["text"].replace("beta", "beta edited", 1), c)
    out = back["content"]["content"]
    assert out[0] == blocks[0] and out[2] == blocks[2]
    assert out[1]["content"][0]["text"] == "beta edited" and out[1]["attrs"]["styleId"] == "Y" and out[1]["attrs"]["pfmt"]["jc"] == "right"
    assert "orig" not in out[1]["attrs"]  # edited: re-serialised on export


def test_one_edit_in_the_import_changes_one_block_only():
    c = manual()
    r = B.to_text(c)
    first = next(b for b in c["content"] if b["type"] == "paragraph" and b.get("content"))
    word = first["content"][0]["text"].split()[0]
    back = B.from_text(r["text"].replace(word, word + "X", 1), c)["content"]["content"]
    assert len(back) == len(c["content"])
    assert [i for i, (x, y) in enumerate(zip(c["content"], back)) if x != y] == [c["content"].index(first)]


def test_moved_deleted_and_duplicated_blocks_keep_their_original():
    blocks = [{**P(T(w)), "attrs": {"styleId": None, "pfmt": {"jc": "left"}, "orig": {"s": i, "e": i, "h": w}}} for i, w in enumerate("abcd")]
    c = {"type": "doc", "content": blocks}
    paras = [x for x in B.to_text(c)["text"].split("\n\n") if x.strip()]
    assert len(paras) == 4

    def merged(order):
        return B.from_text("\n\n".join(paras[i] for i in order) + "\n", c)["content"]["content"]
    moved = merged([2, 0, 1, 3])
    assert [b["content"][0]["text"] for b in moved] == ["c", "a", "b", "d"] and all("orig" in b["attrs"] for b in moved)
    gone = merged([0, 3])
    assert [b["content"][0]["text"] for b in gone] == ["a", "d"] and all("orig" in b["attrs"] for b in gone)
    dup = merged([0, 1, 1, 2, 3])
    assert len(dup) == 5 and dup[1] == dup[2] == blocks[1]


def test_errors_have_line_numbers_and_no_content():
    c = {"type": "doc", "content": [P(T("x"))]}
    r = B.from_text("para\n\n::: {custom-style=\"Quote\"}\ntext\n", c)
    assert r["content"] is None and r["errors"][0]["line"] == 3
    r = B.from_text("ok\n\nhello {{ name\n", c)
    assert r["errors"][0]["line"] == 3 and "{{" in r["errors"][0]["message"]
    r = B.from_text('::: {opaque="deadbeef"}\n[opaque: x]\n:::\n', c)
    assert r["errors"][0]["line"] == 1 and "opaque" in r["errors"][0]["message"]


def test_lossy_constructs_warn_instead_of_failing():
    r = B.from_text("para\n\n---\n\n```\ncode\n```\n\n> quoted\n", {"type": "doc", "content": [P(T("x"))]})
    assert not r["errors"] and any("horizontal" in w for w in r["warnings"]) and any("code block" in w for w in r["warnings"])
    assert ("paragraph", "Quote") in [(b["type"], b["attrs"].get("styleId")) for b in r["content"]["content"]]


def test_markdown_never_passes_raw_html_tex_or_includes():
    nasty = "<script>alert(1)</script>\n\n\\input{/etc/passwd}\n\n```{=html}\n<b>x</b>\n```\n\n<!-- c -->\n\n$\\input{/etc/passwd}$\n\n[a](file:///etc/passwd)\n"
    r = B.from_text(nasty, None)
    assert not r["errors"]
    blob = json.dumps(r["content"])
    assert "root:" not in blob and "RawBlock" not in blob and "RawInline" not in blob
    assert "root:" not in B.to_text(r["content"])["text"]
    assert "root:" not in json.dumps(B.from_text("---\ntitle: x\n---\n\n!include /etc/passwd\n", None))


def test_shell_metacharacters_are_just_text():
    s = "$(touch /tmp/pwned) `id` ; rm -rf / | cat && echo \"x\" 'y' <b>"
    c = {"type": "doc", "content": [P(T(s))]}
    back = B.from_text(B.to_text(c)["text"] + "\n\nmore\n", c)
    first = "".join(x.get("text", "") for x in back["content"]["content"][0]["content"])
    assert first.startswith("$(touch /tmp/pwned) `id` ; rm -rf /")


def test_limits(monkeypatch):
    with pytest.raises(B.BridgeError) as e:
        B.from_text("x" * (B.MAX_TEXT + 1), None)
    assert e.value.status == 413
    monkeypatch.setattr(B, "TIMEOUT_S", 0.001)
    with pytest.raises(B.BridgeError) as e:
        B.to_text(SAMPLE)
    assert e.value.status == 422 and "timed out" in e.value.msg
    monkeypatch.setattr(B, "TIMEOUT_S", 20)
    with pytest.raises(B.BridgeError) as e:
        B.to_text(SAMPLE, "docx")
    assert e.value.status == 422
    monkeypatch.setattr(B, "MAX_OUT", 10)
    with pytest.raises(B.BridgeError) as e:
        B.to_text(SAMPLE)
    assert e.value.status == 413


def test_concurrency_limit_answers_busy(monkeypatch):
    class Full:
        def acquire(self, timeout=None):
            return False
    monkeypatch.setattr(B, "_slots", Full())
    with pytest.raises(B.BridgeError) as e:
        B.to_text(SAMPLE)
    assert e.value.status == 503


def test_only_four_pandoc_processes_at_once():
    assert isinstance(B._slots, threading.BoundedSemaphore) and B._slots._initial_value == 4


def test_pandoc_sandbox_refuses_file_reads_from_inside_a_document(tmp_path):
    secret = tmp_path / "secret.tex"
    secret.write_text("TOPSECRET")
    base = ["pandoc", "--sandbox", "-t", "plain"]
    doc = ("\\input{%s}" % secret).encode()
    p = subprocess.run([*base, "-f", "latex"], input=doc, capture_output=True)
    assert b"TOPSECRET" not in p.stdout  # the same call without --sandbox reads it
    q = subprocess.run(["pandoc", "-t", "plain", "-f", "latex"], input=doc, capture_output=True)
    assert b"TOPSECRET" in q.stdout


# ---- routes ----------------------------------------------------------------------------------------------------------------
def test_routes_roles_and_shapes(client, outbox):
    signup(client, "owner@x.co", "Acme")
    viewer = add_member(client, outbox, "viewer@x.co", "viewer")
    editor = add_member(client, outbox, "editor@x.co", "editor")
    body = {"format": "markdown", "content": SAMPLE}
    assert viewer.post("/api/convert/to-text", json=body).status_code == 403
    assert viewer.post("/api/convert/from-text", json={"format": "markdown", "text": "x"}).status_code == 403
    r = editor.post("/api/convert/to-text", json=body)
    assert r.status_code == 200 and set(r.json()) == {"text", "map"} and r.json()["map"]["opaque"]
    r2 = editor.post("/api/convert/from-text", json={"format": "markdown", "text": r.json()["text"], "base_content": SAMPLE}).json()
    assert set(r2) == {"content", "warnings", "errors"} and r2["content"] == SAMPLE
    bad = editor.post("/api/convert/from-text", json={"format": "markdown", "text": ":::\n"}).json()
    assert bad["errors"] and bad["content"] is None
    assert editor.post("/api/convert/to-text", json={"format": "latex", "content": SAMPLE}).status_code == 422
    assert editor.post("/api/convert/from-text", json={"format": "markdown", "text": "x" * (B.MAX_TEXT + 1)}).status_code == 413
    anon = type(client)(client.app)
    assert anon.post("/api/convert/to-text", json=body).status_code in (401, 403)


def test_per_user_rate_limit(client, monkeypatch):
    from app.routers import convert
    monkeypatch.setattr(convert, "LIMIT", 3)
    convert._hits.clear()
    signup(client, "owner@x.co", "Acme")
    assert [client.post("/api/convert/from-text", json={"text": "x"}).status_code for _ in range(5)] == [200, 200, 200, 429, 429]


# ---- equations -> OMML ------------------------------------------------------------------------------------------------------
EQS = [("\\frac{a+b}{c^2}", True), ("\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}", True), ("\\alpha+\\beta\\to\\gamma", False),
       ("\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}", True), ("\\begin{aligned} x &= 1 \\\\ y &= 2 \\end{aligned}", True),
       ("\\text{speed} = \\sqrt{v_x^2+v_y^2}", False)]


def math_doc():
    return {"type": "doc", "content": [P(T("E: "), {"type": "math", "attrs": {"latex": l, "display": d}}) for l, d in EQS]}


def test_omml_for_a_set_of_equations_batched_cached_deterministic(monkeypatch):
    runs, orig = [], B.run
    monkeypatch.setattr(B, "run", lambda a, d: runs.append(a) or orig(a, d))
    omml._cache.clear()
    c = math_doc()
    a, w = D.export(sample_docx(), c, lambda *_: None)
    assert len(runs) == 1 and not [x for x in w if "equation" in x]  # all six in one pandoc run
    assert D.export(sample_docx(), c, lambda *_: None)[0] == a and len(runs) == 1  # cached by sha256 of the latex
    omml._cache.clear()
    assert D.export(sample_docx(), c, lambda *_: None)[0] == a  # same input, same bytes
    with zipfile.ZipFile(io.BytesIO(a)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    ns = {"m": M, "w": WNS}
    assert len(root.findall(".//m:oMathPara", ns)) == 4 and len(root.findall(".//m:oMath", ns)) == 6
    for tag in ("f", "nary", "m", "rad", "eqArr"):
        assert root.findall(f".//m:{tag}", ns), tag


def test_unconvertible_latex_keeps_the_source_and_warns():
    omml._cache.clear()
    c = {"type": "doc", "content": [P({"type": "math", "attrs": {"latex": "\\frac{", "display": False}})]}
    _, w = D.export(sample_docx(), c, lambda *_: None)
    assert any("equation could not be converted" in x for x in w)


def test_pdf_shows_the_equations():
    from app import export as X
    try:
        pdf = X.docx_to_pdf(D.export(sample_docx(), math_doc(), lambda *_: None)[0])
    except Exception as e:  # Gotenberg is only reachable inside compose
        pytest.skip(str(e))
    from pypdf import PdfReader
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "speed" in text
