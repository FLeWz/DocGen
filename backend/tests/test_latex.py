"""Round 21: LaTeX as a third editing mode (same bridge as markdown) and the markdown gaps fixed in the shared code."""
import glob
import json
import os
import time

import pytest

from app import docx_import as D, latex_text as LT, pandoc_bridge as B

from .fixtures.tables import build as sample_docx
from .test_convert import IID, OPAQUE, SAMPLE, P, T, cell, li, manual, row, strip, var

BASE0 = {"type": "doc", "content": [P(T("x"))]}


def tex(src, base=BASE0):
    r = B.from_text(src, base, "latex")
    assert not r["errors"], r["errors"]
    return r


def blocks(src, base=BASE0):
    return strip(tex(src, base)["content"]["content"])


def flat(n):
    """all text of a node, fields as {SEQ:Table}, math as $..$"""
    if isinstance(n, list):
        return "".join(flat(x) for x in n)
    if n["type"] == "text":
        return n["text"]
    if n["type"] == "math":
        return "$%s$" % n["attrs"]["latex"]
    if n["type"] == "field":
        return "{%s:%s}" % (n["attrs"]["code"], n["attrs"].get("arg", ""))
    if n["type"] == "footnote":
        return "[^%s]" % n["attrs"]["text"]
    if n["type"] == "hardBreak":
        return "\n"
    return flat(n.get("content") or [])


def kinds(src):
    return [b["type"] for b in blocks(src)]


# ---- both directions ------------------------------------------------------------------------------------------------------
def test_every_node_and_mark_round_trips_from_latex_text_alone():
    r = B.to_text(SAMPLE, "latex")
    back = B.from_text(r["text"], {"type": "doc", "content": [OPAQUE]}, "latex")  # the base only supplies the opaque store
    assert not back["errors"] and not back["warnings"], (back["errors"], back["warnings"])
    got, want = strip(back["content"]["content"]), strip(SAMPLE["content"])
    # not expressible in plain LaTeX: font size (a mark attribute), the cached number of a SEQ field
    want[2]["content"][7]["marks"][0]["attrs"].pop("size")
    want[3]["content"][1]["attrs"]["text"] = ""
    got[3]["content"][1]["attrs"].pop("text", None), want[3]["content"][1]["attrs"].pop("text")
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == w, (i, g, w)
    assert len(got) == len(want)


def test_the_text_we_write_is_body_only_valid_looking_latex_without_warnings():
    t = B.to_text(SAMPLE, "latex")["text"]
    for frag in ["\\section{Title}", "\\paragraph{Deep}", "\\textbf{bold}", "\\emph{it}", "\\underline{ul}", "\\textcolor[HTML]{FF0000}{\\sout{red}}", "\\href{https://example.com/a?b=1}{link}",
                 "\\{\\{ doc.title \\textbar{} default(\"Untitled\") \\textbar{} upper \\}\\}", "\\(x^2+y^2\\)", "\\[\\frac{a}{b}\\]", "\\footnote{the note}", "\\ref{SEQ:Table}",
                 "\\begin{tabular}{|l|l|}", "\\multicolumn{2}{|l|}{merged}", "\\includegraphics[width=300px]{docgen-image:", "\\tableofcontents", "\\newpage{}", "\\setcounter{enumi}{2}",
                 '% docgen:{"custom-style":"Heading1","jc":"center"}', '% docgen:{"dynamic":"items"}', '% docgen:{"opaque":', "\\fbox{[opaque: equation]}"]:
        assert frag in t, frag
    assert "\\documentclass" not in t and "\\begin{document}" not in t and "\\usepackage" not in t and "tightlist" not in t and "longtable" not in t and "\\pandocbounded" not in t
    errs, warns = LT.scan(t, {B.oid(OPAQUE): OPAQUE})
    assert errs == [] and warns == []


def test_untouched_latex_gives_identical_json():
    assert B.from_text(B.to_text(SAMPLE, "latex")["text"], SAMPLE, "latex")["content"] == SAMPLE


def test_import_round_trip_is_identical_json_and_identical_docx_bytes():
    c = manual()
    back = B.from_text(B.to_text(c, "latex")["text"], c, "latex")
    assert back["content"] == c and not back["warnings"]
    a, _ = D.export(sample_docx(), c, lambda *_: None)
    b, _ = D.export(sample_docx(), back["content"], lambda *_: None)
    assert a == b


def test_one_edit_in_the_import_changes_one_block_only():
    c = manual()
    t = B.to_text(c, "latex")["text"]
    first = next(b for b in c["content"] if b["type"] == "paragraph" and b.get("content"))
    word = first["content"][0]["text"].split()[0]
    back = B.from_text(t.replace(word, word + "X", 1), c, "latex")["content"]["content"]
    assert len(back) == len(c["content"])
    assert [i for i, (x, y) in enumerate(zip(c["content"], back)) if x != y] == [c["content"].index(first)]


def test_one_edit_keeps_the_style_of_that_block_and_its_neighbours():
    blocks_ = [{**P(T(w), s="X" if i == 1 else None), "attrs": {"styleId": "X" if i == 1 else None, "pfmt": {"jc": "justify", "after": 3} if i == 1 else None, "orig": {"s": i, "e": i, "h": w}}} for i, w in enumerate(["alpha", "beta", "gamma"])]
    c = {"type": "doc", "content": blocks_}
    t = B.to_text(c, "latex")["text"]
    assert '% docgen:{"after":"3","custom-style":"X","jc":"justify"}' in t
    out = B.from_text(t.replace("beta", "beta edited"), c, "latex")["content"]["content"]
    assert out[0] == blocks_[0] and out[2] == blocks_[2]
    assert out[1]["content"][0]["text"] == "beta edited" and out[1]["attrs"]["styleId"] == "X" and out[1]["attrs"]["pfmt"] == {"jc": "justify", "after": 3} and "orig" not in out[1]["attrs"]


def test_moved_deleted_and_duplicated_blocks_keep_their_original():
    bl = [{**P(T(w)), "attrs": {"styleId": None, "pfmt": {"jc": "left"}, "orig": {"s": i, "e": i, "h": w}}} for i, w in enumerate("abcd")]
    c = {"type": "doc", "content": bl}
    parts = [x for x in B.to_text(c, "latex")["text"].split("\n\n") if x.strip()]
    assert len(parts) == 4 and parts[0].startswith("\\begin{flushleft}")

    def merged(order):
        return B.from_text("\n\n".join(parts[i] for i in order) + "\n", c, "latex")["content"]["content"]
    moved = merged([2, 0, 1, 3])
    assert [b["content"][0]["text"] for b in moved] == ["c", "a", "b", "d"] and all("orig" in b["attrs"] for b in moved)
    assert [b["content"][0]["text"] for b in merged([0, 3])] == ["a", "d"]
    dup = merged([0, 1, 1, 2, 3])
    assert len(dup) == 5 and dup[1] == dup[2] == bl[1]


# ---- native constructs, from LaTeX ------------------------------------------------------------------------------------------
def test_sectioning_levels_and_starred_variants():
    b = blocks("\\section{A}\n\n\\subsection{B}\n\n\\subsubsection{C}\n\n\\paragraph{D}\n\n\\subparagraph{E}\n\n\\section*{F}\n")
    assert [(x["type"], x["attrs"]["level"], flat(x)) for x in b] == [("heading", 1, "A"), ("heading", 2, "B"), ("heading", 3, "C"), ("heading", 4, "D"), ("heading", 4, "E"), ("heading", 1, "F")]
    ch = blocks("\\chapter{Ch}\n\n\\section{S}\n\n\\subsection{SS}\n")  # with chapters: chapter = Heading 1, section = 2 ...
    assert [x["attrs"]["level"] for x in ch] == [1, 2, 3]


def test_inline_formatting_and_escapes():
    b = blocks("a \\textbf{b} \\emph{c} \\textit{d} \\underline{e} \\texttt{f} \\textsc{g} \\textsuperscript{h} \\textsubscript{i} \\sout{j} \\textcolor{red}{k} \\textcolor[HTML]{00FF7F}{l}\n")[0]["content"]
    marks = {n["text"]: [m["type"] if m["type"] != "fmt" else {k: v for k, v in m["attrs"].items() if v} for m in n.get("marks", [])] for n in b if n["type"] == "text"}
    assert marks["b"] == ["bold"] and marks["c"] == ["italic"] and marks["d"] == ["italic"] and marks["e"] == ["underline"]
    assert marks["f"] == [{"font": "Courier New"}] and marks["g"] == [{"smallCaps": True}] and marks["h"] == [{"va": "superscript"}] and marks["i"] == [{"va": "subscript"}]
    assert marks["j"] == [{"strike": True}] and marks["k"] == [{"color": "ff0000"}] and marks["l"] == [{"color": "00ff7f"}]
    t = flat(blocks("50\\% \\& \\_ \\# \\{ \\} \\textbackslash{} \\ldots{} a~b -- --- ``q'' text\\\\next\\newline third \\verb|x_y| end\n")[0])
    assert t == "50% & _ # { } \\ … a\u00a0b – — “q” text\nnext\nthird x_y end"


def test_lists_nested_enumerate_start_and_description():
    b = blocks("\\begin{itemize}\n\\item a\n\\begin{enumerate}\n\\setcounter{enumi}{3}\n\\item b\n\\item c\n\\end{enumerate}\n\\item d\n\\end{itemize}\n\n"
               "\\begin{enumerate}[a.]\n\\setcounter{enumi}{1}\n\\item x\n\\end{enumerate}\n\n\\begin{description}\n\\item[Term] the text\n\\end{description}\n")
    assert b[0]["type"] == "bulletList" and b[0]["content"][0]["content"][1]["attrs"]["start"] == 4 and len(b[0]["content"][0]["content"][1]["content"]) == 2
    assert b[1]["attrs"] == {"start": 2, "listStyle": "lower-alpha"}
    assert [flat(x) for x in b[2:]] == ["Term", "the text"] and b[2]["content"][0]["marks"] == [{"type": "bold"}]
    assert "description list" in " ".join(tex("\\begin{description}\\item[a] b\\end{description}")["warnings"])


def test_quote_alignment_verbatim_and_rule():
    b = blocks("\\begin{quote}\nQ\n\\end{quote}\n\n\\begin{quotation}\nQQ\n\\end{quotation}\n\n\\begin{center}\nC\n\\end{center}\n\n\\begin{flushleft}\nL\n\\end{flushleft}\n\n"
               "\\begin{flushright}\nR\n\\end{flushright}\n\n\\begin{verbatim}\nline 1\n  line 2\n\\end{verbatim}\n\n\\hrule\n")
    assert [x["attrs"].get("styleId") for x in b[:2]] == ["Quote", "Quote"]
    assert [x["attrs"]["pfmt"]["jc"] for x in b[2:5]] == ["center", "left", "right"]
    assert b[5]["attrs"]["styleId"] == "Code" and flat(b[5]) == "line 1\n  line 2"
    assert b[6]["attrs"]["pfmt"] == B.HR_P


def test_tabular_hline_columns_multicolumn_multirow():
    b = blocks("\\begin{tabular}{|l|c|r|}\n\\hline\nA & B & C \\\\\n\\hline\n\\multicolumn{2}{|c|}{wide} & \\multirow{2}{*}{tall} \\\\\n1 & 2 & \\\\\n\\hline\n\\end{tabular}\n\n"
               "\\begin{tabular*}{\\textwidth}{ll}\nx & y \\\\\n\\end{tabular*}\n")
    t = b[0]
    assert t["type"] == "table" and [c["type"] for c in t["content"][0]["content"]] == ["tableHeader"] * 3
    r1 = t["content"][1]["content"]
    assert r1[0]["attrs"]["colspan"] == 2 and r1[1]["attrs"]["rowspan"] == 2 and flat(r1[1]) == "tall"
    assert len(t["content"][2]["content"]) == 2  # the covered cell is not written twice
    assert b[1]["type"] == "table"
    assert "\\multirow{2}{*}{" in B.to_text(tex(
        "\\begin{tabular}{ll}\n\\multirow{2}{*}{x} & y \\\\\n & z \\\\\n\\end{tabular}\n")["content"], "latex")["text"]


def test_float_table_and_figure_become_seq_caption_paragraphs():
    b = blocks("\\begin{table}\n\\caption{Prices}\\label{t:p}\n\\begin{tabular}{ll}\na & b\n\\end{tabular}\n\\end{table}\n\n"
               "\\begin{figure}\n\\includegraphics[width=3cm]{docgen-image:%s}\n\\caption{A cat}\n\\end{figure}\n" % IID)
    assert [x["type"] for x in b] == ["paragraph", "table", "image", "paragraph"]
    assert b[0]["attrs"]["styleId"] == "Caption" and flat(b[0]) == "Table {SEQ:Table}: Prices"
    assert b[2]["attrs"]["imageId"] == IID and b[2]["attrs"]["width"] == 113  # 3 cm at 96 dpi
    assert b[3]["attrs"]["styleId"] == "Caption" and flat(b[3]) == "Figure {SEQ:Figure}: A cat"


def test_footnotes_endnotes_references_and_links():
    b = blocks("a\\footnote{first} b\\endnote{second}\\ref{SEQ:Table} \\pageref{PAGE} \\href{http://x}{lnk} \\url{http://y}\n")[0]["content"]
    fn = [n for n in b if n["type"] == "footnote"]
    assert [(n["attrs"]["kind"], n["attrs"]["text"]) for n in fn] == [("footnote", "first"), ("endnote", "second")]
    assert [(n["attrs"]["code"], n["attrs"].get("arg", "")) for n in b if n["type"] == "field"] == [("SEQ", "Table"), ("PAGE", "")]
    assert [m["attrs"]["href"] for n in b for m in n.get("marks", []) if m["type"] == "link"] == ["http://x", "http://y"]
    # `\label` targets a heading of this text: no Word bookmark would exist for it, so the reference stays text and warns
    r = tex("\\section{S}\\label{sec:s}\n\nsee \\ref{sec:s}\n")
    assert flat(r["content"]["content"][1]) == "see [sec:s]" and any("sec:s" in w for w in r["warnings"])
    base = {"type": "doc", "content": [{"type": "paragraph", "attrs": {"bm": "_Ref1", "styleId": None, "pfmt": None}}]}
    f = [n for n in tex("see \\ref{_Ref1}\n", base)["content"]["content"][0]["content"] if n["type"] == "field"]
    assert f and f[0]["attrs"]["code"] == "REF" and f[0]["attrs"]["arg"] == "_Ref1"  # the target exists in the document


def test_bibliography_and_cite():
    r = tex("As in \\cite{b} and \\cite{a,zz}.\n\n\\begin{thebibliography}{9}\n\\bibitem{a} First, 2001.\n\\bibitem{b} Second, 2002.\n\\end{thebibliography}\n")
    b = strip(r["content"]["content"])
    assert flat(b[0]) == "As in [2] and [1, zz]."
    assert [(x["attrs"]["styleId"], flat(x)) for x in b[1:]] == [("Bibliography", "[1] First, 2001."), ("Bibliography", "[2] Second, 2002.")]
    assert any("'zz'" in w for w in r["warnings"])


def test_page_breaks_lists_of_contents_and_title():
    b = blocks("\\newpage\n\n\\clearpage\n\n\\pagebreak\n\n\\tableofcontents\n\n\\listoffigures\n\n\\listoftables\n")
    assert [x["type"] for x in b] == ["paragraph"] * 3 + ["toc"] * 3 and all(x["content"][0]["attrs"]["page"] for x in b[:3])
    assert [x["attrs"]["kind"] for x in b[3:]] == ["toc", "tof", "tot"]
    full = blocks("\\documentclass{article}\n\\usepackage{amsmath}\n\\title{My Title}\n\\author{Ann \\and Bob}\n\\date{1.1.2026}\n\\begin{document}\n\\maketitle\n\\section{S}\nbody\n\\end{document}\n")
    assert [(x["type"], x["attrs"].get("styleId"), flat(x)) for x in full] == [("paragraph", "Title", "My Title"), ("paragraph", "Subtitle", "Ann Bob, 1.1.2026"), ("heading", None, "S"), ("paragraph", None, "body")] \
        or [x["attrs"].get("styleId") for x in full[:2]] == ["Title", "Subtitle"]
    assert len(full) == 4


def test_comments_and_macros():
    r = tex("% a comment\nvisible % trailing\n\n\\newcommand{\\foo}{bar}\nuse \\foo{} here\n")
    assert [flat(x) for x in r["content"]["content"]] == ["visible", "use bar here"]
    assert any("macro definitions" in w for w in r["warnings"])


@pytest.mark.parametrize("src,latex,display", [
    ("$a+b$", "a+b", False), ("\\(a+b\\)", "a+b", False), ("\\[a+b\\]", "a+b", True), ("$$a+b$$", "a+b", True),
    ("\\begin{equation}\na=b\\label{e}\n\\end{equation}", "\\begin{equation}\na=b\\label{e}\n\\end{equation}", True),
    ("\\begin{align}\na&=b\\\\c&=d\n\\end{align}", "\\begin{align}\na&=b\\\\c&=d\n\\end{align}", True),
    ("\\begin{align*}\na&=b\n\\end{align*}", "\\begin{align*}\na&=b\n\\end{align*}", True),
    ("\\begin{gather}\na\\\\b\n\\end{gather}", "\\begin{gather}\na\\\\b\n\\end{gather}", True),
    ("\\begin{multline}\na\\\\b\n\\end{multline}", "\\begin{multline}\na\\\\b\n\\end{multline}", True),
    ("\\begin{eqnarray}\na&=&b\n\\end{eqnarray}", "\\begin{eqnarray}\na&=&b\n\\end{eqnarray}", True),
    ("\\begin{equation}\n\\begin{split}\na&=b\n\\end{split}\n\\end{equation}", "\\begin{equation}\n\\begin{split}\na&=b\n\\end{split}\n\\end{equation}", True),
    ("\\[ \\begin{cases} a & b \\\\ c & d \\end{cases} \\]", "\\begin{cases} a & b \\\\ c & d \\end{cases}", True),
    ("$\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}$", "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}", False),
])
def test_math_forms_become_the_math_node_with_the_latex_kept(src, latex, display):
    m = [n for n in blocks(src + "\n")[0]["content"] if n["type"] == "math"]
    assert len(m) == 1 and m[0]["attrs"] == {"latex": latex, **({"display": True} if display else {})}


def test_math_in_latex_text_reaches_omml_on_export():
    from app import omml
    r = tex("inline $x^2+y^2$ and\n\n\\begin{align}\na&=b\\\\c&=d\n\\end{align}\n")
    eqs = omml.collect(r["content"]["content"])
    assert len(eqs) == 2 and all(omml.get(l, d) is not None for l, d in eqs)  # `$..$` typed in LaTeX re-exports as OMML, environments included


def test_unknown_things_warn_with_line_numbers_and_never_vanish_silently():
    r = tex("ok\n\n\\foo{bar}\n\n\\begin{weirdenv}\ntext inside\n\\end{weirdenv}\n\n\\usepackage{tikz}\n")
    w = " | ".join(r["warnings"])
    assert "line 3: unsupported command \\foo" in w and "line 5: environment 'weirdenv'" in w and "line 9: \\usepackage is ignored" in w
    assert "text inside" in json.dumps(r["content"])  # the content of an unknown environment stays as text


def test_images_only_by_docgen_id():
    r = tex("\\includegraphics{/etc/passwd}\n\n\\includegraphics{docgen-image:%s}\n\n\\includegraphics{%s}\n\n\\includegraphics{http://evil/x.png}\n" % (IID, IID))
    imgs = [x["attrs"]["imageId"] for x in r["content"]["content"] if x["type"] == "image"]
    assert imgs == [IID, IID] and sum("dropped" in w for w in r["warnings"]) == 2


# ---- errors with line numbers -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("src,line,frag", [
    ("a\n\n\\begin{itemize}\n\\item x\n", 3, "never closed"),
    ("a\n\\end{itemize}\n", 2, "without \\begin"),
    ("\\begin{itemize}\n\\item x\n\\end{enumerate}\n", 3, "does not match"),
    ("a { b\n", 1, "unmatched {"),
    ("a\n\nb }\n", 3, "unmatched }"),
])
def test_syntax_errors_have_line_column_and_no_content(src, line, frag):
    r = B.from_text(src, BASE0, "latex")
    assert r["content"] is None and r["errors"][0]["line"] == line and frag in r["errors"][0]["message"] and r["errors"][0]["column"] >= 1


def test_unknown_opaque_id_in_an_annotation_is_an_error():
    r = B.from_text('x\n\n% docgen:{"opaque":"deadbeef"}\n\n\\fbox{[opaque: x]}\n', BASE0, "latex")
    assert r["content"] is None and r["errors"][0]["line"] == 3


def test_a_broken_annotation_is_just_a_comment():
    assert [flat(x) for x in blocks("% docgen:{not json\n\ntext\n")] == ["text"]


def test_opaque_blocks_are_restored_by_id():
    c = {"type": "doc", "content": [P(T("a")), OPAQUE]}
    t = B.to_text(c, "latex")["text"]
    assert B.from_text(t.replace("a\n", "a b\n", 1), c, "latex")["content"]["content"][1] == OPAQUE


# ---- security: nothing reads files, the network, or runs ------------------------------------------------------------------
NASTY = [
    "\\input{/etc/passwd}", "\\include{/etc/passwd}", "\\openin5=/etc/passwd \\read5 to \\x \\x", "\\write18{id > /tmp/pwned_docgen}", "\\immediate\\write18{touch /tmp/pwned_docgen}",
    "\\immediate\\write16{x}", "\\usepackage{/etc/passwd}", "\\usepackage{../../etc/passwd}", "\\verbatiminput{/etc/passwd}", "\\includegraphics{/etc/passwd}",
    "\\lstinputlisting{/etc/passwd}", "\\csname input\\endcsname{/etc/passwd}", "\\expandafter\\input\\csname /etc/passwd\\endcsname", "\\catcode`\\\\=0 \\input /etc/passwd",
    "\\href{file:///etc/passwd}{x}", "\\url{file:///etc/passwd}", "\\begin{verbatim}\n\\input{/etc/passwd}\n\\end{verbatim}", "$\\input{/etc/passwd}$",
    "\\documentclass{/etc/passwd}", "\\bibliography{/etc/passwd}", "\\InputIfFileExists{/etc/passwd}{}{}", "\\def\\x{\\input{/etc/passwd}}\\x",
]


@pytest.mark.parametrize("src", NASTY)
def test_nothing_in_a_latex_document_reads_files_or_runs_commands(src):
    r = B.from_text("before\n\n" + src + "\n\nafter\n", BASE0, "latex")
    blob = json.dumps(r) + json.dumps(B.to_text(r["content"], "latex") if r["content"] else "")
    assert "root:" not in blob and "/bin/" not in blob and "daemon" not in blob
    assert not os.path.exists("/tmp/pwned_docgen")
    assert "RawBlock" not in blob and "RawInline" not in blob


def test_shell_metacharacters_are_just_text_in_latex():
    s = "$(touch /tmp/pwned_docgen) `id` ; rm -rf / | cat && echo \"x\" 'y' <b>"
    c = {"type": "doc", "content": [P(T(s))]}
    back = B.from_text(B.to_text(c, "latex")["text"] + "\nmore\n", c, "latex")
    assert flat(back["content"]["content"][0]).startswith("$(touch /tmp/pwned_docgen) `id` ; rm -rf /") or "touch" in flat(back["content"]["content"][0])
    assert not os.path.exists("/tmp/pwned_docgen")


def pandoc_procs():
    out = []
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            if b"pandoc" in open(p, "rb").read():
                out.append(p)
        except OSError:
            pass
    return out


def test_runaway_macro_recursion_is_stopped_quickly():
    t0 = time.monotonic()
    r = B.from_text("\\def\\a{\\a\\a}\\a\n", BASE0, "latex")
    assert time.monotonic() - t0 < 5 and r["content"] is None and "Loop" in r["errors"][0]["message"] and r["errors"][0]["line"] >= 1


def test_macro_bombs_are_refused_and_a_slow_conversion_is_killed_at_the_timeout(monkeypatch):
    bomb = "\\def\\a#1{#1#1}" + "\\a{" * 40 + "x" + "}" * 40 + "\n"  # pandoc's own loop detection answers at once
    t0 = time.monotonic()
    r = B.from_text(bomb, BASE0, "latex")
    assert r["content"] is None and r["errors"] and time.monotonic() - t0 < 5
    big = "\n\n".join("para %d with \\textbf{bold} and $x_%d$" % (i, i) for i in range(20000))
    monkeypatch.setattr(B, "TIMEOUT_S", 0.05)
    t0 = time.monotonic()
    with pytest.raises(B.BridgeError) as e:
        B.from_text(big, BASE0, "latex")
    assert e.value.status == 422 and "timed out" in e.value.msg and time.monotonic() - t0 < 5
    time.sleep(0.2)
    assert pandoc_procs() == []  # no hung process left behind


def test_huge_and_deeply_nested_input():
    with pytest.raises(B.BridgeError) as e:
        B.from_text("x" * (B.MAX_TEXT + 1), BASE0, "latex")
    assert e.value.status == 413
    deep = "\\begin{quote}" * 150 + "x" + "\\end{quote}" * 150
    r = B.from_text(deep, BASE0, "latex")
    assert r["content"] is None and "deeper than" in r["errors"][0]["message"]
    ok = B.from_text("\\begin{quote}" * 40 + "x" + "\\end{quote}" * 40, BASE0, "latex")
    assert not ok["errors"]
    big = "\n\n".join("para %d with \\textbf{bold} and $x_%d$" % (i, i) for i in range(4000))
    t0 = time.monotonic()
    assert len(tex(big)["content"]["content"]) == 4000 and time.monotonic() - t0 < 15


def test_latex_routes(client, outbox):
    from .conftest import add_member, signup
    signup(client, "owner@x.co", "Acme")
    viewer = add_member(client, outbox, "viewer@x.co", "viewer")
    editor = add_member(client, outbox, "editor@x.co", "editor")
    assert viewer.post("/api/convert/to-text", json={"format": "latex", "content": SAMPLE}).status_code == 403
    r = editor.post("/api/convert/to-text", json={"format": "latex", "content": SAMPLE})
    assert r.status_code == 200 and "\\section{Title}" in r.json()["text"]
    r2 = editor.post("/api/convert/from-text", json={"format": "latex", "text": r.json()["text"], "base_content": SAMPLE}).json()
    assert r2["content"] == SAMPLE and r2["errors"] == []
    bad = editor.post("/api/convert/from-text", json={"format": "latex", "text": "\\begin{x}\n"}).json()
    assert bad["content"] is None and bad["errors"][0]["line"] == 1
    assert editor.post("/api/convert/from-text", json={"format": "latex", "text": "\\input{/etc/passwd}"}).status_code == 200
    assert editor.post("/api/convert/from-text", json={"format": "tex", "text": "x"}).status_code == 422


# ---- part B: markdown gaps of round 20, fixed in the shared bridge ------------------------------------------------------------
def md(text, base=BASE0):
    r = B.from_text(text, base)
    assert not r["errors"], r["errors"]
    return r


def tbl(rows, **attrs):
    return {"type": "table", "attrs": {"dynamicList": None, **attrs}, "content": [
        {"type": "tableRow", "attrs": {"rfmt": {"jc": "right"}} if i == 0 else {}, "content": [
            {"type": "tableHeader" if i == 0 else "tableCell", "attrs": {"colspan": 1, "rowspan": 1, "colwidth": [100 + j], "cfmt": {"shd": "909FA4"} if i == 0 else {"vAlign": "center"}},
             "content": [{**P(T(x)), "attrs": {"styleId": None, "pfmt": {"jc": "center"}}}]} for j, x in enumerate(r)]} for i, r in enumerate(rows)]}


ORIG_TBL = tbl([["Name", "Qty"], ["apple", "1"], ["pear", "2"]], styleId="TableGrid", grid=[2000, 3000], tfmt={"bdr": {"top": {"val": "single", "sz": 4}}}, tblPr="<w:tblPr/>", orig={"s": 1, "e": 1, "h": "abc"})


@pytest.mark.parametrize("fmt", ["markdown", "latex"])
def test_edited_table_keeps_its_attrs_and_new_rows_and_columns_clone_neighbours(fmt):
    c = {"type": "doc", "content": [P(T("before")), ORIG_TBL, P(T("after"))]}
    t = B.to_text(c, fmt)["text"]
    edited = {"markdown": lambda s: s.replace("| pear", "| plum").replace("| 2 ", "| 20"), "latex": lambda s: s.replace("pear", "plum").replace("& 2 ", "& 20 ")}[fmt](t)
    assert edited != t
    out = B.from_text(edited, c, fmt)["content"]["content"]
    nt = out[1]
    assert nt["type"] == "table" and nt["attrs"]["styleId"] == "TableGrid" and nt["attrs"]["grid"] == [2000, 3000] and nt["attrs"]["tfmt"] == ORIG_TBL["attrs"]["tfmt"] and "orig" not in nt["attrs"]
    assert flat(nt["content"][2]) == "plum20" or "plum" in flat(nt)
    h, last = nt["content"][0]["content"][0], nt["content"][2]["content"][1]
    assert h["type"] == "tableHeader" and h["attrs"]["cfmt"] == {"shd": "909FA4"} and h["attrs"]["colwidth"] == [100] and nt["content"][0]["attrs"] == {"rfmt": {"jc": "right"}}
    assert last["attrs"]["cfmt"] == {"vAlign": "center"} and last["content"][0]["attrs"]["pfmt"] == {"jc": "center"}
    # a new row and a new column
    more = {"markdown": "before\n\n| Name | Qty | X |\n|---|---|---|\n| apple | 1 | a |\n| fig | 9 | z |\n| pear | 2 | b |\n\nafter\n",
            "latex": t.replace("pear", "fig & 9 & z \\\\\npear").replace("Name", "Name & X").replace("{ll}", "{lll}")}[fmt]
    nt2 = B.from_text(more, c, fmt)["content"]["content"][1]
    assert nt2["type"] == "table" and nt2["attrs"]["styleId"] == "TableGrid" and len(nt2["attrs"]["grid"]) == 3 and len(nt2["content"]) == 4
    assert all(len(r["content"]) == 3 for r in nt2["content"]) and nt2["content"][2]["content"][0]["attrs"]["cfmt"] == {"vAlign": "center"}
    assert nt2["content"][2]["content"][2]["attrs"]["cfmt"] == {"vAlign": "center"} and nt2["content"][0]["content"][2]["type"] == "tableHeader"


def test_more_cells_than_columns_is_an_error_not_silent_paragraphs():
    r = B.from_text("a\n\n\\begin{tabular}{ll}\nx & y \\\\\n1 & 2 & 3 \\\\\n\\end{tabular}\n", BASE0, "latex")
    assert r["content"] is None and r["errors"][0]["line"] == 5 and "3 cells" in r["errors"][0]["message"]
    assert not B.from_text("\\begin{tabular}{|l|*{2}{c}|}\n\\multicolumn{3}{c}{w} \\\\\n1 & 2 & 3 \\\\\n\\end{tabular}\n", BASE0, "latex")["errors"]


def test_a_table_with_nothing_in_common_is_not_matched():
    c = {"type": "doc", "content": [ORIG_TBL] + [P(T("p%d" % i)) for i in range(8)]}
    out = md("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n| 7 | 8 |\n| 9 | 0 |\n", c)["content"]["content"]
    assert out[0]["type"] == "table" and "styleId" not in out[0]["attrs"]  # dropped the original table, wrote an unrelated one


def test_nested_ordered_lists_keep_start_numbers_and_nesting_in_markdown():
    c = {"type": "doc", "content": [{"type": "orderedList", "attrs": {"start": 3, "listStyle": None}, "content": [
        li(P(T("one")), {"type": "orderedList", "attrs": {"start": 4, "listStyle": None}, "content": [li(P(T("n4"))), li(P(T("n5")))]}), li(P(T("two")))]}]}
    t = B.to_text(c)["text"]
    assert "3." in t and "4." in t
    back = B.from_text(t, None)["content"]["content"][0]
    assert back["attrs"]["start"] == 3 and len(back["content"]) == 2
    inner = back["content"][0]["content"][1]
    assert inner["type"] == "orderedList" and inner["attrs"]["start"] == 4 and len(inner["content"]) == 2


def test_horizontal_rule_node_round_trips_in_both_formats():
    hr = {"type": "paragraph", "attrs": {"styleId": None, "pfmt": B.HR_P}}
    c = {"type": "doc", "content": [P(T("a")), hr, P(T("b"))]}
    for fmt, mark in (("markdown", "---"), ("latex", "\\hrule")):
        t = B.to_text(c, fmt)["text"]
        assert mark in t
        assert strip(B.from_text(t, None, fmt)["content"]["content"]) == strip(c["content"])


def test_code_and_quote_styles_round_trip_in_both_formats():
    code = {**P({"type": "text", "text": "x = 1"}, {"type": "hardBreak", "attrs": {"page": False}}, {"type": "text", "text": "  y"}), "attrs": {"styleId": "Code", "pfmt": None}}
    quote = {**P(T("wise words")), "attrs": {"styleId": "Quote", "pfmt": None}}
    c = {"type": "doc", "content": [code, quote]}
    for fmt, marks in (("markdown", ("    x = 1", "> wise words")), ("latex", ("\\begin{verbatim}", "\\begin{quote}"))):
        t = B.to_text(c, fmt)["text"]
        assert all(m in t for m in marks) and "custom-style" not in t and "docgen" not in t
        assert strip(B.from_text(t, None, fmt)["content"]["content"]) == strip(c["content"])
    assert B.from_text(B.to_text(c)["text"], c)["content"] == c


def test_footnote_kind_survives_editing_and_a_following_field_is_not_a_reference_link():
    fn = {"type": "footnote", "attrs": {"kind": "footnote", "text": "the note", "n": 1}}
    en = {"type": "footnote", "attrs": {"kind": "endnote", "text": "an end", "n": 2}}
    fld = {"type": "field", "attrs": {"code": "SEQ", "arg": "Table", "text": "1"}}
    c = {"type": "doc", "content": [P(T("x "), fn, fld, T(" y"), en, T(" z"), s="Caption")]}
    t = B.to_text(c)["text"]
    assert "[1]{field" in t
    back = B.from_text(t.replace("x ", "w ", 1), c)["content"]["content"][0]["content"]
    kinds_ = [(n["type"], n["attrs"].get("kind") or n["attrs"].get("code")) for n in back if n["type"] in ("footnote", "field")]
    assert kinds_ == [("footnote", "footnote"), ("field", "SEQ"), ("footnote", "endnote")] and flat(back).startswith("w ")
    assert "[^" in flat(back) and "^[" not in flat(back)  # not swallowed into text
    # the same in LaTeX
    lt = B.to_text(c, "latex")["text"]
    lb = B.from_text(lt.replace("x ", "w ", 1), c, "latex")["content"]["content"][0]["content"]
    assert [(n["type"], n["attrs"].get("kind") or n["attrs"].get("code")) for n in lb if n["type"] in ("footnote", "field")] == kinds_


def test_images_inside_formatted_text_are_kept_as_image_blocks():
    out = md("before **bold ![alt](docgen-image:%s){width=\"120\"} after** end\n\n![solo](docgen-image:%s)\n" % (IID, IID))["content"]["content"]
    assert [b["type"] for b in out] == ["paragraph", "image", "paragraph", "image"]
    assert flat(out[0]).strip() == "before bold" and out[1]["attrs"]["imageId"] == IID and out[1]["attrs"]["width"] == 120 and flat(out[2]).strip() == "after end"
    mid = md("a ![x](docgen-image:%s) b\n" % IID)["content"]["content"]
    assert [b["type"] for b in mid] == ["paragraph", "image", "paragraph"]
