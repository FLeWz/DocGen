"""Synthetic DOCX for table/paragraph round-trip tests: two styled tables (tblLook, cell margins, borders, shading, tcW,
trPr jc, paragraph + paragraph-mark properties in cells), a list without pStyle, spacing before/after paragraphs."""
import io

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

NS = nsdecls("w")
CELL = lambda w, fill, txt, extra="": (
    f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/><w:tcBorders><w:bottom w:val="single" w:sz="4" w:space="0" w:color="909FA4"/></w:tcBorders>'
    f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/><w:vAlign w:val="center"/></w:tcPr>'
    f'<w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:jc w:val="center"/><w:rPr><w:b/><w:color w:val="FFFFFF"/></w:rPr></w:pPr>'
    f'<w:r><w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/><w:szCs w:val="20"/><w:color w:val="FFFFFF" w:themeColor="light1"/></w:rPr>'
    f'<w:t>{txt}</w:t></w:r>{extra}</w:p></w:tc>')
TBL = lambda style, grid, rows: (
    f'<w:tbl {NS}><w:tblPr><w:tblStyle w:val="{style}"/><w:tblW w:w="{sum(grid)}" w:type="dxa"/><w:tblInd w:w="-109" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
    '<w:tblCellMar><w:top w:w="113" w:type="dxa"/><w:bottom w:w="113" w:type="dxa"/></w:tblCellMar>'
    '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
    f'<w:tblGrid>{"".join(f"<w:gridCol w:w=\"{g}\"/>" for g in grid)}</w:tblGrid>{rows}</w:tbl>')


def build() -> bytes:
    d = Document()
    body = d.element.body
    end = body.find(qn("w:sectPr"))
    add = lambda xml: end.addprevious(parse_xml(xml))
    add(f'<w:p {NS}><w:pPr><w:spacing w:before="3200"/></w:pPr></w:p>')
    add(TBL("TableGrid", [2000, 3000], f'<w:tr><w:trPr><w:jc w:val="right"/></w:trPr>{CELL(2000, "909FA4", "T1-A")}{CELL(3000, "909FA4", "T1-B")}</w:tr>'
            f'<w:tr>{CELL(2000, "FFFFFF", "T1-C")}{CELL(3000, "FFFFFF", "T1-D")}</w:tr>'))
    add(f'<w:p {NS}><w:pPr><w:spacing w:after="4000"/></w:pPr><w:r><w:t>Between</w:t></w:r></w:p>')
    add(f'<w:p {NS}><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>plain list item</w:t></w:r></w:p>')
    add(TBL("TableGrid", [1500, 1500, 4000], f'<w:tr>{CELL(1500, "D9E2F3", "T2-A")}{CELL(1500, "D9E2F3", "T2-B")}{CELL(4000, "D9E2F3", "T2-C")}</w:tr>'))
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


if __name__ == "__main__":
    import sys
    open(sys.argv[1], "wb").write(build())
