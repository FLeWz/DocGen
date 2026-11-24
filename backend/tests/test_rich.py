"""Editor round 5B serializations, pure (no DB): fields/captions, footnotes, cross references, bookmarks, marked sections,
TOC/TOF/TOT, section breaks, list formats. Content goes through docx_import.export into a real package and is read back."""
import io
import zipfile

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from app import docx_import as D
from .fixtures.sample import build

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SAMPLE = build()
P = lambda *c, **a: {"type": "paragraph", "attrs": a, "content": list(c)}
T = lambda s, marks=None: {"type": "text", "text": s, **({"marks": marks} if marks else {})}
H = lambda s, lvl=1: {"type": "heading", "attrs": {"level": lvl, "styleId": f"Heading{lvl}"}, "content": [T(s)]}
F = lambda code, arg="", text="": {"type": "field", "attrs": {"code": code, "arg": arg, "text": text}}


def export(blocks, orig=SAMPLE):
    out, warn = D.export(orig, {"type": "doc", "content": blocks}, lambda c, i: None)
    return out, warn


def part(data, name):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(name))


def body(data):
    return part(data, "word/document.xml").find(qn("w:body"))


def instrs(root):
    return ["".join(i.text or "" for i in [x]).strip() for x in root.iter(qn("w:instrText"))]


def test_caption_seq_fields_and_figure_list():
    blocks = [H("Intro"), P(T("Figure "), F("SEQ", "Figure"), T(": A cat"), styleId="Caption"),
              P(T("Table "), F("SEQ", "Table"), T(": Prices"), styleId="Caption"),
              P(T("Figure "), F("SEQ", "Figure"), T(": A dog"), styleId="Caption"),
              {"type": "toc", "attrs": {"kind": "tof", "title": "Figures", "label": "Figure"}},
              {"type": "toc", "attrs": {"kind": "tot", "title": "", "label": "Table"}}]
    out, warn = export(blocks)
    b = body(out)
    ins = instrs(b)
    assert ins.count("SEQ Figure \\* ARABIC") == 2 and ins.count("SEQ Table \\* ARABIC") == 1
    assert 'TOC \\h \\z \\c "Figure"' in ins and 'TOC \\h \\z \\c "Table"' in ins
    text = "".join(t.text or "" for t in b.iter(qn("w:t")))
    assert "Figure 1: A cat" in text and "Figure 2: A dog" in text and "Table 1: Prices" in text  # cached results = Word's numbering
    # the figure list's cached entries are the two figure captions, the table list's the one table caption
    paras = list(b.iter(qn("w:p")))
    tof = [p for p in paras if p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}") is not None and p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}").get(qn("w:val")) == "TableofFigures"]
    assert len(tof) == 3
    assert any(f.get(qn("w:dirty")) == "true" for f in b.iter(qn("w:fldChar")))  # Word refreshes the list on open
    st = part(out, "word/styles.xml")
    assert {s.get(qn("w:styleId")) for s in st.iter(qn("w:style"))} >= {"Caption", "TableofFigures"}


def test_toc_is_an_sdt_docpart_with_field_and_heading_entries():
    out, _ = export([H("Alpha"), H("Beta", 2), H("Gamma", 4), {"type": "toc", "attrs": {"kind": "toc", "title": "Contents", "levels": "1-2"}}])
    b = body(out)
    sdt = b.find(qn("w:sdt"))
    assert sdt.find(f"{qn('w:sdtPr')}/{qn('w:docPartObj')}/{qn('w:docPartGallery')}").get(qn("w:val")) == "Table of Contents"
    assert instrs(sdt) == ['TOC \\o "1-2" \\h \\z \\u']
    cached = ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in sdt.find(qn("w:sdtContent")).findall(qn("w:p"))]
    assert cached == ["Contents", "Alpha", "Beta"]  # level 4 is outside 1-2
    fc = [f.get(qn("w:fldCharType")) for f in sdt.iter(qn("w:fldChar"))]
    assert fc == ["begin", "separate", "end"]  # one field across the entry paragraphs


def test_toc_roundtrip_detects_sdt_and_field_structures():
    out, _ = export([H("Alpha"), {"type": "toc", "attrs": {"kind": "toc", "title": "", "levels": "1-3"}},
                     {"type": "toc", "attrs": {"kind": "tof", "title": "", "label": "Figure"}}, P(T("tail"))])
    content, warn = D.parse(out, lambda b: None)
    kinds = [(n["type"], (n.get("attrs") or {}).get("kind")) for n in content["content"]]
    assert ("toc", "toc") in kinds and ("toc", "tof") in kinds, kinds
    assert not any("table of contents" in w for w in warn)  # recognised, not an opaque leftover
    again, _ = D.export(out, content, lambda c, i: None)  # unedited: written back as it was
    assert instrs(body(again)) == instrs(body(out))
    toc = next(n for n in content["content"] if n["type"] == "toc" and n["attrs"]["kind"] == "toc")
    assert toc["attrs"]["levels"] == "1-3" and toc["attrs"]["xml"]


def test_marked_sections_are_bookmark_ranges_and_come_back():
    sec = {"id": "7", "kind": "appendix", "title": "Appendix A"}
    blocks = [P(T("before")), P(T("one"), sec=sec), H("Two", 2) | {"attrs": {**H("x")["attrs"], "level": 2, "sec": sec}}, P(T("after"))]
    blocks[2] = {**blocks[2], "attrs": {"level": 2, "styleId": "Heading2", "sec": sec}}
    out, _ = export(blocks)
    b = body(out)
    marks = [(e.tag.split("}")[1], e.get(qn("w:name")), e.get(qn("w:id"))) for e in b if e.tag in (qn("w:bookmarkStart"), qn("w:bookmarkEnd"))]
    assert [m[0] for m in marks] == ["bookmarkStart", "bookmarkEnd"] and marks[0][1] == "dgsec_appendix_7_Appendix_A"
    kids = [e.tag.split("}")[1] for e in b]
    assert kids.index("bookmarkStart") < kids.index("bookmarkEnd") and kids[kids.index("bookmarkStart") + 1:kids.index("bookmarkEnd")] == ["p", "p"]
    content, warn = D.parse(out, lambda x: None)
    got = [(D.block_text(n), (n.get("attrs") or {}).get("sec")) for n in content["content"] if n["type"] in ("paragraph", "heading")]
    inside = [g for g in got if g[1]]
    assert [g[0] for g in inside] == ["one", "Two"] and inside[0][1] == {"id": "7", "kind": "appendix", "title": "Appendix A"}
    assert [g[1] for g in got if g[0] in ("before", "after")] == [None, None]
    assert not any("bookmark" in w for w in warn)
    # an edit does not need the sec attr to be re-serialised: unedited blocks keep their original XML and the range is rewritten
    again, _ = D.export(out, content, lambda c, i: None)
    assert [e.get(qn("w:name")) for e in body(again).iter(qn("w:bookmarkStart"))][:1] == ["dgsec_appendix_7_Appendix_A"]
    content["content"] = [{**n, "attrs": {k: v for k, v in n["attrs"].items() if k != "sec"}} if (n.get("attrs") or {}).get("sec") else n for n in content["content"]]
    gone, _ = D.export(out, content, lambda c, i: None)
    assert not list(body(gone).iter(qn("w:bookmarkStart")))  # unmark = no bookmark


def test_footnotes_and_endnotes_are_real_parts():
    out, _ = export([P(T("Claim"), {"type": "footnote", "attrs": {"kind": "footnote", "text": "Source: <A&B>"}}, T(" and more"),
                       {"type": "footnote", "attrs": {"kind": "endnote", "text": "See end"}})])
    z = zipfile.ZipFile(io.BytesIO(out))
    assert "word/footnotes.xml" in z.namelist() and "word/endnotes.xml" in z.namelist()
    fn = etree.fromstring(z.read("word/footnotes.xml"))
    notes = fn.findall(qn("w:footnote"))
    assert [n.get(qn("w:type")) for n in notes] == ["separator", "continuationSeparator", None]
    assert "".join(t.text for t in notes[2].iter(qn("w:t"))).strip() == "Source: <A&B>"
    assert notes[2].find(f".//{qn('w:footnoteRef')}") is not None
    refs = body(out).findall(f".//{qn('w:footnoteReference')}")
    assert [r.get(qn("w:id")) for r in refs] == [notes[2].get(qn("w:id"))]
    assert body(out).find(f".//{qn('w:endnoteReference')}") is not None
    assert b'/word/footnotes.xml' in z.read("[Content_Types].xml")
    assert b"footnotes.xml" in z.read("word/_rels/document.xml.rels")
    assert etree.fromstring(z.read("word/settings.xml")).find(qn("w:footnotePr")) is not None
    Document(io.BytesIO(out))  # still loads


def test_footnote_ids_continue_in_a_package_that_has_notes():
    first, _ = export([P(T("a"), {"type": "footnote", "attrs": {"text": "one"}})])
    second, _ = D.export(first, {"type": "doc", "content": [P(T("b"), {"type": "footnote", "attrs": {"text": "two"}})]}, lambda c, i: None)
    ids = [n.get(qn("w:id")) for n in etree.fromstring(zipfile.ZipFile(io.BytesIO(second)).read("word/footnotes.xml")).findall(qn("w:footnote"))]
    assert ids == ["-1", "0", "1", "2"]


def test_cross_references_ref_fields_and_bookmarks():
    blocks = [{"type": "heading", "attrs": {"level": 1, "styleId": "Heading1", "bm": "_Ref100"}, "content": [T("Target")]},
              P(T("see "), F("REF", "_Ref100", "Target")), P(T("bad "), F("REF", "no good", "x"), F("EVIL", "", "y"))]
    out, warn = export(blocks)
    b = body(out)
    assert instrs(b) == ["REF _Ref100 \\h"]
    bs = b.find(f".//{qn('w:bookmarkStart')}")
    assert bs.get(qn("w:name")) == "_Ref100"
    assert b.find(f".//{qn('w:bookmarkEnd')}").get(qn("w:id")) == bs.get(qn("w:id"))
    assert sum("skipped" in w for w in warn) == 2  # invalid bookmark name and a field code off the whitelist
    ids = [e.get(qn("w:id")) for e in b.iter(qn("w:bookmarkStart"))]
    assert len(ids) == len(set(ids))


def test_page_fields_roundtrip_in_paragraph():
    out, _ = export([P(T("Page "), F("PAGE"), T(" of "), F("NUMPAGES"))])
    assert instrs(body(out)) == ["PAGE", "NUMPAGES"]
    content, _ = D.parse(out, lambda b: None)
    p = next(n for n in content["content"] if D.block_text(n).startswith("Page "))
    assert [c["type"] for c in p["content"]] == ["text", "field", "text", "field"] and p["content"][1]["attrs"]["code"] == "PAGE"


def test_section_break_orientation_margins_type():
    out, _ = export([P(T("a")), {"type": "sectBreak", "attrs": {"brk": "continuous", "orient": "landscape", "top": 720, "left": 1000}}, P(T("b"))])
    sps = list(body(out).iter(qn("w:sectPr")))
    brk = sps[-2]  # the paragraph-level one precedes the final section's
    sz = brk.find(qn("w:pgSz"))
    assert sz.get(qn("w:orient")) == "landscape" and int(sz.get(qn("w:w"))) > int(sz.get(qn("w:h")))
    assert brk.find(qn("w:type")).get(qn("w:val")) == "continuous"
    mar = brk.find(qn("w:pgMar"))
    assert mar.get(qn("w:top")) == "720" and mar.get(qn("w:left")) == "1000"
    kids = [c.tag.split("}")[1] for c in brk]
    assert kids.index("type") < kids.index("pgSz") < kids.index("pgMar")  # schema order
    assert sps[-1].find(qn("w:pgSz")).get(qn("w:orient")) is None  # the final section is untouched


def _num(out):
    n = part(out, "word/numbering.xml")
    abstract = {a.get(qn("w:abstractNumId")): a for a in n.findall(qn("w:abstractNum"))}
    nums = {x.get(qn("w:numId")): x for x in n.findall(qn("w:num"))}
    return abstract, nums


def test_list_formats_bullet_styles_start_continue_multilevel():
    li = lambda t, *kids: {"type": "listItem", "content": [P(T(t)), *kids]}
    lst = lambda kind, items, **a: {"type": kind, "attrs": a, "content": items}
    blocks = [lst("orderedList", [li("a"), li("b")], listStyle="upper-roman"),
              lst("orderedList", [li("c")], listStyle="lower-alpha", start=5),
              lst("orderedList", [li("d")], listStyle="lower-alpha", **{"continue": True}),
              lst("bulletList", [li("e")], listStyle="square"),
              lst("orderedList", [li("f", lst("orderedList", [li("g")], listStyle="multilevel"))], listStyle="multilevel")]
    out, _ = export(blocks, orig=build())
    b = body(out)
    ps = [p for p in b.iter(qn("w:p")) if p.find(f".//{qn('w:numPr')}") is not None]
    num_of = lambda p: p.find(f".//{qn('w:numId')}").get(qn("w:val"))
    by_text = {"".join(t.text for t in p.iter(qn("w:t"))): p for p in ps}
    abstract, nums = _num(out)
    fmt_of = lambda nid: abstract[nums[nid].find(qn("w:abstractNumId")).get(qn("w:val"))].find(f"{qn('w:lvl')}/{qn('w:numFmt')}").get(qn("w:val"))
    assert fmt_of(num_of(by_text["a"])) == "upperRoman" and num_of(by_text["a"]) == num_of(by_text["b"])
    assert fmt_of(num_of(by_text["c"])) == "lowerLetter"
    so = nums[num_of(by_text["c"])].find(f"{qn('w:lvlOverride')}/{qn('w:startOverride')}")
    assert so.get(qn("w:val")) == "5"  # restart at 5
    assert num_of(by_text["d"]) == num_of(by_text["c"])  # continue numbering = same numId
    glyph = abstract[nums[num_of(by_text["e"])].find(qn("w:abstractNumId")).get(qn("w:val"))].find(f"{qn('w:lvl')}/{qn('w:lvlText')}").get(qn("w:val"))
    assert glyph == "▪"
    f_, g_ = by_text["f"], by_text["g"]
    assert num_of(f_) == num_of(g_) and g_.find(f".//{qn('w:ilvl')}").get(qn("w:val")) == "1"  # nested = level 1 of the same list
    ml = abstract[nums[num_of(f_)].find(qn("w:abstractNumId")).get(qn("w:val"))]
    assert ml.find(qn("w:multiLevelType")).get(qn("w:val")) == "multilevel"
    assert [l.find(qn("w:lvlText")).get(qn("w:val")) for l in ml.findall(qn("w:lvl"))][:3] == ["%1.", "%1.%2", "%1.%2.%3"]
