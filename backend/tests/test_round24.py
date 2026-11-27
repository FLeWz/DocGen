"""Round 24: tables inside table cells are editable table nodes (depth limit 8), unedited blocks stay byte for byte, edited ones
re-serialise into valid OOXML that python-docx opens and LibreOffice renders."""
import copy
import io
import zipfile

import pytest
from docx import Document
from lxml import etree

from app import docx_import as D
from app import export as X

from .test_base_docs import make_docx, rezip

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = f'xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
P = lambda t: f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>"


def tbl(cells, widths, merged=False, shade=True):
    """rows of `cells` (list of str or raw xml); the 2nd row merges two columns when `merged`"""
    shd = '<w:shd w:val="clear" w:color="auto" w:fill="FFFF00"/>' if shade else ""
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    rows = []
    for ri, row in enumerate(cells):
        tcs = []
        for ci, c in enumerate(row[:1] if merged and ri == 1 else row):
            body = c if c.startswith("<w:") else P(c)
            span = '<w:gridSpan w:val="2"/>' if merged and ri == 1 and ci == 0 else ""
            tcs.append(f'<w:tc><w:tcPr><w:tcW w:w="{sum(widths) if span else widths[ci]}" w:type="dxa"/>{span}{shd if ci == 0 else ""}</w:tcPr>{body}</w:tc>')
        rows.append(f"<w:tr>{''.join(tcs)}</w:tr>")
    return (f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders><w:top w:val="single" w:sz="4"/>'
            f'<w:bottom w:val="single" w:sz="4"/></w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>')


def nest(depth, w=9000):
    """table whose first cell holds a table ... `depth` levels (1 = a plain table); deepest cell text L<depth>"""
    if depth == 1:
        return tbl([["L1", "x1"], ["m1", "y1"]], [w // 2, w // 2], merged=True)
    inner = nest(depth - 1, w - 700)
    return tbl([[inner, f"R{depth}"], [f"m{depth}", f"s{depth}"]], [w - 1200, 1200])


def package(body):
    doc = f'<w:document {NS}><w:body>{body}<w:sectPr/></w:body></w:document>'
    return rezip(make_docx(), replace={"word/document.xml": doc.encode()})


def children(data):
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return [etree.tostring(c, method="c14n") for c in root.find(f"{{{W}}}body")]


def tables(n, depth=0):
    for c in n.get("content") or []:
        if c.get("type") == "table":
            yield depth, c
        yield from tables(c, depth + (c.get("type") == "table"))


def export(data, content):
    return D.export(data, content, lambda *_: None)[0]


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_nested_tables_are_table_nodes_and_unedited_export_is_identical(depth):
    data = package(P("before") + nest(depth) + P("after"))
    content, warn = D.parse(data, lambda b: None)
    assert not [w for w in warn if "nested table" in w]
    found = [(d, t) for b in content["content"] for d, t in ([(0, b)] if b["type"] == "table" else []) + list(tables(b, 0 if b["type"] != "table" else 1))]
    assert len(found) == depth
    assert not any(b["type"] == "opaque" for b in content["content"])
    assert children(export(data, content)) == children(data)


def test_depth_limit_keeps_the_ninth_level_opaque_with_a_warning():
    data = package(nest(10))
    content, warn = D.parse(data, lambda b: None)
    assert sum(1 for b in content["content"] for _ in D.walk(b) if _.get("type") == "table") == 9  # levels 1..9
    assert any(w.startswith("1× nested table") for w in warn)


def test_edit_nested_cell_reserialises_valid_ooxml_and_keeps_everything_else():
    data = package(P("before") + nest(3) + P("after"))
    content, _ = D.parse(data, lambda b: None)
    inner = [t for b in content["content"] for d, t in tables(b, 1 if b["type"] == "table" else 0)][-1]
    cell = inner["content"][0]["content"][0]
    cell["content"][0]["content"][0]["text"] = "EDITED"
    out = export(data, content)
    a, b = children(data), children(out)
    assert a[0] == b[0] and a[2] == b[2] and a[1] != b[1]  # only the edited outer table changed
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml"))
    text = "".join(root.itertext())
    assert "EDITED" in text and "L1" not in text and all(x in text for x in ("x1", "m1", "R2", "R3", "m3"))
    assert len(list(root.iter(f"{{{W}}}tbl"))) == 3
    for tc in root.iter(f"{{{W}}}tc"):  # Word needs every tc to end with a paragraph
        assert tc[-1].tag == f"{{{W}}}p"
    # merge, shading and widths survive inside the innermost table
    assert root.xpath("//w:gridSpan", namespaces={"w": W}) and root.xpath("//w:shd[@w:fill='FFFF00']", namespaces={"w": W})
    Document(io.BytesIO(out))
    assert X.docx_to_pdf(out).startswith(b"%PDF")


def test_forced_reserialisation_keeps_text_and_does_not_grow_trailing_paragraphs():
    data = package(nest(2) + P("end"))
    content, _ = D.parse(data, lambda b: None)
    for n in D.walk(content):
        (n.get("attrs") or {}).pop("orig", None)
    once = export(data, content)
    again_content, _ = D.parse(once, lambda b: None)
    for n in D.walk(again_content):
        (n.get("attrs") or {}).pop("orig", None)
    twice = export(once, again_content)
    text = lambda d: "".join(etree.fromstring(zipfile.ZipFile(io.BytesIO(d)).read("word/document.xml")).itertext())
    assert text(data) == text(once) == text(twice)
    assert once == twice or children(once) == children(twice)  # the empty trailing paragraph is dropped on import and re-added once


@pytest.mark.parametrize("fmt", ["markdown", "latex"])
def test_markdown_and_latex_keep_nested_tables_as_placeholders_and_restore_them(fmt):
    from app import pandoc_bridge as B
    data = package(P("before") + nest(3) + P("after"))
    content, _ = D.parse(data, lambda b: None)
    r = B.to_text(content, fmt)
    assert "opaque" in r["text"].lower() and "R3" not in r["text"]  # the outer table is one placeholder: nothing editable is lost
    back = B.from_text(r["text"], content, fmt)
    assert not back["errors"] and back["content"] == content
    assert children(export(data, back["content"])) == children(data)
    edited = B.from_text(r["text"].replace("before", "BEFORE"), content, fmt)["content"]
    assert edited["content"][1] == content["content"][1]  # the table comes back verbatim


# ---- part 2: blocks that were opaque and are text now ----
FORM = ('<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Sl_Title"/><w:enabled/><w:calcOnExit w:val="0"/>'
        '<w:textInput><w:default w:val="DEF"/></w:textInput></w:ffData></w:fldChar></w:r><w:bookmarkStart w:id="1" w:name="Sl_Title"/>'
        '<w:r><w:instrText xml:space="preserve"> FORMTEXT </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>FORM-TEXT</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r><w:bookmarkEnd w:id="1"/></w:p>')
SECT = ('<w:p><w:pPr><w:pStyle w:val="Subtitle"/><w:sectPr><w:type w:val="continuous"/><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="425" w:footer="329" w:gutter="0"/><w:titlePg/></w:sectPr></w:pPr></w:p>')


def kinds(content):
    return sorted(n["attrs"]["kind"] for b in content["content"] for n in D.walk(b) if n.get("type") == "opaque")


def test_formtext_and_section_break_import_as_text_nodes_and_round_trip():
    data = package(FORM + P("mid") + SECT + P("end"))
    content, warn = D.parse(data, lambda b: None)
    assert kinds(content) == [] and not any("form field" in w or "section break" in w for w in warn)
    fld = next(n for n in D.walk(content["content"][0]) if n["type"] == "field")
    assert fld["attrs"]["code"] == "FORMTEXT" and fld["attrs"]["text"] == "FORM-TEXT" and fld["attrs"]["bm"] == "Sl_Title" and "Sl_Title" in fld["attrs"]["ff"]
    assert fld["marks"] and fld["marks"][0]["type"] == "bold"  # run formatting of the result kept
    sb = content["content"][2]
    assert sb["type"] == "sectBreak" and sb["attrs"]["brk"] == "continuous" and sb["attrs"]["top"] == 1134 and "titlePg" in sb["attrs"]["xml"]
    assert children(export(data, content)) == children(data)


def test_edited_form_field_and_section_break_keep_ffdata_bookmark_and_sectpr():
    data = package(FORM + P("mid") + SECT + P("end"))
    content, _ = D.parse(data, lambda b: None)
    next(n for n in D.walk(content["content"][0]) if n["type"] == "field")["attrs"]["text"] = "NEW-TEXT"
    sb = content["content"][2]
    sb["attrs"]["orient"], sb["attrs"]["brk"] = "landscape", "nextPage"
    out = export(data, content)
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml"))
    ns = {"w": W}
    assert root.xpath("//w:fldChar[@w:fldCharType='begin']/w:ffData/w:name/@w:val", namespaces=ns) == ["Sl_Title"]
    assert root.xpath("//w:bookmarkStart/@w:name", namespaces=ns) == ["Sl_Title"] and len(root.xpath("//w:bookmarkEnd", namespaces=ns)) == 1
    assert root.xpath("//w:instrText/text()", namespaces=ns) == [" FORMTEXT "] and "NEW-TEXT" in "".join(root.itertext()) and "FORM-TEXT" not in "".join(root.itertext())
    sp = root.xpath("//w:p/w:pPr/w:sectPr", namespaces=ns)[0]
    assert sp.xpath("w:titlePg", namespaces=ns) and sp.xpath("w:type/@w:val", namespaces=ns) == ["nextPage"] and sp.xpath("w:pgSz/@w:orient", namespaces=ns) == ["landscape"]
    assert root.xpath("//w:p[w:pPr/w:sectPr]/w:pPr/w:pStyle/@w:val", namespaces=ns) == ["Subtitle"]
    Document(io.BytesIO(out))
    assert X.docx_to_pdf(out).startswith(b"%PDF")


def box(inner, extra=""):
    return ('<w:p><w:r><mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><mc:Choice Requires="wps"><w:drawing>'
            '<wp:anchor xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="1" '
            'behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="column"><wp:posOffset>100</wp:posOffset></wp:positionH>'
            '<wp:positionV relativeFrom="paragraph"><wp:posOffset>200</wp:posOffset></wp:positionV><wp:extent cx="2000000" cy="500000"/><wp:wrapSquare wrapText="bothSides"/>'
            '<wp:docPr id="1" name="Box"/><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            f'<wps:wsp xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:spPr/><wps:txbx><w:txbxContent>{inner}</w:txbxContent></wps:txbx></wps:wsp>'
            '</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice><mc:Fallback><w:pict xmlns:v="urn:schemas-microsoft-com:vml"><v:shape><v:textbox>'
            f'<w:txbxContent>{inner}</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r>{extra}</w:p>')


def test_textbox_is_editable_content_inline_and_the_shape_stays():
    inner = P("Box one") + P("Box two")
    data = package(P("before") + box(inner) + box(P("beside"), '<w:r><w:t>text next to the box</w:t></w:r>') + P("after"))
    content, warn = D.parse(data, lambda b: None)
    tb = content["content"][1]
    assert tb["type"] == "textbox" and [D.block_text(c) for c in tb["content"]] == ["Box one", "Box two"]
    assert "wp:anchor" in tb["attrs"]["xml"] and "Box one" not in tb["attrs"]["xml"] and "posOffset>100<" in tb["attrs"]["xml"]
    assert content["content"][2]["type"] == "opaque" and content["content"][2]["attrs"]["kind"] == "text box"  # text beside the box: stays opaque
    assert children(export(data, content)) == children(data)
    tb["content"][0]["content"][0]["text"] = "EDITED"
    tb["content"].append({"type": "paragraph", "attrs": {"styleId": None}, "content": [{"type": "text", "text": "added"}]})
    out = export(data, content)
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml"))
    ns = {"w": W}
    boxes = root.xpath("//w:txbxContent", namespaces=ns)
    assert len(boxes) == 4  # both copies (DrawingML + VML fallback) of both boxes
    for b in boxes[:2]:
        assert ["".join(p.itertext()) for p in b.xpath("w:p", namespaces=ns)] == ["EDITED", "Box two", "added"]
    assert root.xpath("//wp:posOffset/text()", namespaces={"wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"})[:1] == ["100"]
    assert children(data)[0] == children(out)[0] and children(data)[2:] == children(out)[2:]
    Document(io.BytesIO(out))
    assert X.docx_to_pdf(out).startswith(b"%PDF")


SDT = ('<w:sdt><w:sdtPr><w:alias w:val="Block"/><w:tag w:val="T1"/></w:sdtPr><w:sdtContent>' + P("SDT-ONE") + tbl([["c1", "c2"]], [3000, 3000], shade=False) + '</w:sdtContent></w:sdt>')


@pytest.mark.parametrize("fmt", ["markdown", "latex"])
def test_new_nodes_survive_the_text_bridges_verbatim(fmt):
    from app import pandoc_bridge as B
    data = package(FORM + SECT + box(P("in box")) + SDT + P("end"))
    content, _ = D.parse(data, lambda b: None)
    assert [b["type"] for b in content["content"]] == ["paragraph", "sectBreak", "textbox", "sdtBlock", "paragraph"]
    back = B.from_text(B.to_text(content, fmt)["text"], content, fmt)
    assert not back["errors"] and back["content"] == content
    assert children(export(data, back["content"])) == children(data)


def test_block_sdt_is_an_editable_container_that_keeps_its_properties():
    data = package(P("a") + SDT + P("b"))
    content, warn = D.parse(data, lambda b: None)
    s = content["content"][1]
    assert s["type"] == "sdtBlock" and [c["type"] for c in s["content"]] == ["paragraph", "table"] and "Block" in s["attrs"]["xml"] and not any("content control" in w for w in warn)
    assert children(export(data, content)) == children(data)
    s["content"][0]["content"][0]["text"] = "SDT-EDIT"
    out = export(data, content)
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml"))
    ns = {"w": W}
    assert root.xpath("//w:sdt/w:sdtPr/w:alias/@w:val", namespaces=ns) == ["Block"] and root.xpath("//w:sdt/w:sdtPr/w:tag/@w:val", namespaces=ns) == ["T1"]
    assert "SDT-EDIT" in "".join(root.xpath("//w:sdt/w:sdtContent", namespaces=ns)[0].itertext()) and root.xpath("//w:sdtContent/w:tbl", namespaces=ns)
    assert X.docx_to_pdf(out).startswith(b"%PDF")
