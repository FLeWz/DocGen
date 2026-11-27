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
