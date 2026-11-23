"""Builds the round-trip fixture DOCX (python-docx + raw OOXML): custom styles, header/footer, a revision table, a
merged-cell table, two images, numbered + bullet lists with direct numbering, a content control (SDT), a text box and a
tracked change. `python -m tests.fixtures.sample out.docx` writes it to disk."""
import io
import sys

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt, RGBColor
from PIL import Image

SDT = ('<w:sdt %s><w:sdtPr><w:alias w:val="Client"/><w:tag w:val="client"/></w:sdtPr>'
       '<w:sdtContent><w:p><w:r><w:t>SDT-CONTENT-MARKER</w:t></w:r></w:p></w:sdtContent></w:sdt>')
TEXTBOX = ('<w:p %s xmlns:v="urn:schemas-microsoft-com:vml"><w:r><w:pict><v:shape id="tb1" type="#_x0000_t202" '
           'style="width:120pt;height:40pt"><v:textbox><w:txbxContent><w:p><w:r><w:t>TEXTBOX-MARKER</w:t></w:r></w:p>'
           '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>')
TRACKED = ('<w:p %s><w:r><w:t xml:space="preserve">Before </w:t></w:r><w:ins w:id="90" w:author="Eve" w:date="2024-01-01T00:00:00Z">'
           '<w:r><w:t>INSERTED-MARKER</w:t></w:r></w:ins></w:p>')
NUMBERING = ('<w:abstractNum %s w:abstractNumId="20"><w:multiLevelType w:val="hybridMultilevel"/>'
             '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%%1."/></w:lvl>'
             '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%%2)"/></w:lvl></w:abstractNum>',
             '<w:abstractNum %s w:abstractNumId="21"><w:multiLevelType w:val="hybridMultilevel"/>'
             '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/></w:lvl></w:abstractNum>')


def png(color, size=(40, 20)) -> io.BytesIO:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    b.seek(0)
    return b


def build() -> bytes:
    d = Document()
    st = d.styles
    callout = st.add_style("Callout", WD_STYLE_TYPE.PARAGRAPH)
    callout.base_style = st["Normal"]
    callout.font.bold, callout.font.color.rgb, callout.font.size = True, RGBColor(0xC0, 0x39, 0x2B), Pt(13)
    st["Heading 1"].font.color.rgb = RGBColor(0x12, 0x34, 0x56)
    sec = d.sections[0]
    sec.header.paragraphs[0].text = "HEADER-MARKER Acme Corp"
    sec.footer.paragraphs[0].text = "FOOTER-MARKER confidential"

    # numbering: custom abstractNums + nums 20 (decimal, 2 levels) and 21 (bullet)
    num = d.part.numbering_part.element
    first_num = num.find(qn("w:num"))
    for a in NUMBERING:
        el = parse_xml(a % nsdecls("w"))
        first_num.addprevious(el) if first_num is not None else num.append(el)
    for nid, aid in ((20, 20), (21, 21)):
        num.append(parse_xml(f'<w:num {nsdecls("w")} w:numId="{nid}"><w:abstractNumId w:val="{aid}"/></w:num>'))

    def li(text, num_id, lvl=0):
        p = d.add_paragraph(text, style="List Paragraph")
        p._p.get_or_add_pPr().append(parse_xml(
            f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{lvl}"/><w:numId w:val="{num_id}"/></w:numPr>'))
        return p

    d.add_paragraph("Introduction", style="Heading 1")
    p = d.add_paragraph("Plain ", style="Normal")
    p.add_run("bold").bold = True
    p.add_run(" and ")
    p.add_run("italic").italic = True
    p.add_run(" text.")
    d.add_paragraph("Callout paragraph with a custom style", style="Callout")
    d.add_paragraph("Logo: ").add_run().add_picture(png("red"))
    li("Step one", 20)
    li("Step one a", 20, 1)
    li("Step two", 20)
    li("Bullet A", 21)
    li("Bullet B", 21)
    d.add_paragraph("Details", style="Heading 2")
    d.add_paragraph("Second image:").add_run().add_picture(png("blue", (30, 30)))
    d.add_paragraph("Revision history", style="Heading 1")
    t = d.add_table(rows=1, cols=4)
    t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, ("Version", "Date", "Author", "Change")):
        c.text = h
    t.rows[0]._tr.get_or_add_trPr().append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    for row in (("1.0", "2024-01-01", "Ann", "First issue"), ("1.1", "2024-02-01", "Bob", "Fixes")):
        for c, v in zip(t.add_row().cells, row):
            c.text = v
    d.add_paragraph("Merged cells", style="Heading 1")
    m = d.add_table(rows=3, cols=3)
    m.style = "Table Grid"
    for r in range(3):
        for c in range(3):
            m.cell(r, c).text = f"r{r}c{c}"
    m.cell(0, 0).merge(m.cell(0, 1))  # colspan 2
    m.cell(1, 2).merge(m.cell(2, 2))  # rowspan 2
    d.add_paragraph("Opaque things", style="Heading 1")
    body = d.element.body
    sect = body.find(qn("w:sectPr"))
    for xml in (SDT, TEXTBOX, TRACKED):
        sect.addprevious(parse_xml(xml % nsdecls("w")))
    d.add_paragraph("The end.")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


if __name__ == "__main__":  # "-" = stdout (the e2e script pipes it out of the api container)
    (sys.stdout.buffer if sys.argv[1] == "-" else open(sys.argv[1], "wb")).write(build())
