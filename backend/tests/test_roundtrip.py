"""Unedited blocks of an import are written back as their original XML; edited tables keep style, look, borders, shading, widths
and the paragraph/run properties of their cells."""
import copy
import io
import zipfile

from lxml import etree

from app import docx_import as D

from .fixtures.tables import build

SRC = build()
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NSM = {"w": W}
IID = "00000000-0000-0000-0000-000000000001"


def body(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return etree.fromstring(z.read("word/document.xml")).find(f"{{{W}}}body")


def canon(el):
    return etree.tostring(el, method="c14n")


def export(content):
    data, _ = D.export(SRC, content, lambda *_: None)
    return data


def parsed():
    return D.parse(SRC, lambda b: IID)[0]


def test_unedited_import_round_trips_every_block_canonically():
    a, b = body(SRC), body(export(parsed()))
    assert [canon(x) for x in a] == [canon(x) for x in b]
    assert len(a.findall("w:tbl", NSM)) == 2


def test_survives_json_normalisation_like_the_editor_does():
    c = parsed()
    for n in D.walk(c):  # TipTap adds default attrs (null/false), reorders marks and drops nothing else
        if n.get("type") in ("paragraph", "heading", "tableCell"):
            n["attrs"] = {**n.get("attrs", {}), "extra_default": None}
        if n.get("type") == "orderedList":
            n["attrs"]["start"] = 1  # TipTap's own default
        for k in ("marks",):
            if n.get(k):
                n[k].reverse()
    assert [canon(x) for x in body(SRC)] == [canon(x) for x in body(export(c))]


def test_edited_table_is_reserialised_others_stay_verbatim():
    c = parsed()
    t1 = next(n for n in c["content"] if n["type"] == "table")
    cell = next(n for n in D.walk(t1) if n.get("type") == "text")
    cell["text"] = "EDITED"
    out = body(export(c))
    src = body(SRC)
    assert canon(out.findall("w:tbl", NSM)[1]) == canon(src.findall("w:tbl", NSM)[1])  # untouched table identical
    t = out.findall("w:tbl", NSM)[0]
    pr, sp = t.find("w:tblPr", NSM), src.findall("w:tbl", NSM)[0].find("w:tblPr", NSM)
    assert canon(pr) == canon(sp)  # tblStyle, tblW, tblInd, layout, cell margins, tblLook all kept
    assert [g.get(f"{{{W}}}w") for g in t.findall("w:tblGrid/w:gridCol", NSM)] == ["2000", "3000"]
    tc = t.find(".//w:tc", NSM)
    assert tc.find("w:tcPr/w:shd", NSM).get(f"{{{W}}}fill") == "909FA4"
    assert tc.find("w:tcPr/w:tcBorders/w:bottom", NSM).get(f"{{{W}}}color") == "909FA4"
    assert tc.find("w:tcPr/w:tcW", NSM).get(f"{{{W}}}w") == "2000"
    sp_ = tc.find("w:p/w:pPr/w:spacing", NSM)
    assert sp_.get(f"{{{W}}}after") == "0" and sp_.get(f"{{{W}}}line") == "240"
    mark = tc.find("w:p/w:pPr/w:rPr", NSM)  # paragraph-mark properties survive too
    assert mark.find("w:b", NSM) is not None and mark.find("w:color", NSM).get(f"{{{W}}}val") == "FFFFFF"
    assert t.find("w:tr/w:trPr/w:jc", NSM).get(f"{{{W}}}val") == "right"
    assert "EDITED" in "".join(t.itertext())


def test_edited_list_item_keeps_original_style_not_list_paragraph():
    c = parsed()
    lst = next(n for n in c["content"] if n["type"] in ("bulletList", "orderedList"))
    next(n for n in D.walk(lst) if n.get("type") == "text")["text"] = "changed"
    p = next(p for p in body(export(c)).findall("w:p", NSM) if "changed" in "".join(p.itertext()))
    assert p.find("w:pPr/w:pStyle", NSM) is None and p.find("w:pPr/w:numPr", NSM) is not None


def test_forged_origin_is_ignored():
    c = parsed()
    c["content"][0]["attrs"]["orig"] = {"s": 99, "e": 120, "h": "x"}
    n = copy.deepcopy(c["content"][2])
    n["attrs"]["orig"]["s"] = n["attrs"]["orig"]["e"] = 0  # right range type, but the hash no longer matches
    c["content"][2] = n
    export(c)  # no IndexError, falls back to the serializer
