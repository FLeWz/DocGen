"""Synthetic DOCX with the direct-formatting features of a real-world manual (the proprietary example is not committed):
landscape page + margins, docDefaults, basedOn style chain, theme colour, run colour/font/size/highlight/strike/sup,
paragraph align/indent/spacing/shading/border, page break, lowerLetter list, table with shading/borders/vAlign/vMerge/
widths, sized image, text box + field (opaque), header/footer."""
import io

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt, RGBColor
from PIL import Image

NS = nsdecls("w")
R = lambda rpr, t: f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{t}</w:t></w:r>'
P = lambda ppr, *runs: f'<w:p {NS}><w:pPr>{ppr}</w:pPr>{"".join(runs)}</w:p>'
PPR = ('<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="1" w:color="FF0000"/></w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="EEEEEE"/>'
       '<w:spacing w:before="240" w:after="120" w:line="360" w:lineRule="auto"/><w:ind w:left="720" w:hanging="360"/><w:jc w:val="center"/>')
FIELD = (f'<w:p {NS}><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> DOCPROPERTY X </w:instrText></w:r>'
         '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>FIELD-RESULT</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
TEXTBOX = (f'<w:p {NS} xmlns:v="urn:schemas-microsoft-com:vml"><w:r><w:pict><v:shape id="tb1" style="width:120pt;height:40pt"><v:textbox>'
           '<w:txbxContent><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>TEXTBOX-TEXT</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>')


def png(color, size=(40, 20)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    b.seek(0)
    return b


def build() -> bytes:
    d = Document()
    st = d.styles
    a = st.add_style("Callout", WD_STYLE_TYPE.PARAGRAPH)
    a.base_style = st["Normal"]
    a.font.bold, a.font.color.rgb = True, RGBColor(0xC0, 0x39, 0x2B)
    b = st.add_style("CalloutBig", WD_STYLE_TYPE.PARAGRAPH)
    b.base_style = a
    b.font.size, b.font.name = Pt(20), "Georgia"
    sec = d.sections[0]
    sec.page_width, sec.page_height = 15840 * 635, 12240 * 635  # US Letter landscape, EMU
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = 1000 * 635
    sec.header.paragraphs[0].text = "HEADER-MARKER"
    sec.footer.paragraphs[0].text = "FOOTER-MARKER"
    num = d.part.numbering_part.element
    num.insert(0, parse_xml(f'<w:abstractNum {NS} w:abstractNumId="30"><w:multiLevelType w:val="hybridMultilevel"/>'
                            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%1)"/></w:lvl></w:abstractNum>'))
    num.append(parse_xml(f'<w:num {NS} w:numId="30"><w:abstractNumId w:val="30"/></w:num>'))

    body = d.element.body
    end = body.find(qn("w:sectPr"))
    add = lambda xml: end.addprevious(parse_xml(xml))
    add(P('<w:pStyle w:val="CalloutBig"/>', R("", "Styled")))
    add(P(PPR, R('<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/><w:strike/><w:color w:val="00AA00"/><w:sz w:val="28"/>'
                 '<w:highlight w:val="yellow"/>', "direct "),
          R('<w:color w:val="auto" w:themeColor="accent1"/><w:vertAlign w:val="superscript"/>', "theme-sup")))
    add(P("", '<w:r><w:br w:type="page"/></w:r>', R("", "after break")))
    add(P('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="30"/></w:numPr>', R("", "item")))
    d.add_paragraph("Logo").add_run().add_picture(png("red"), width=Pt(90), height=Pt(30))
    t = d.add_table(rows=2, cols=2)
    t._tbl.tblPr.find(qn("w:tblLook")).addprevious(parse_xml(
        f'<w:tblBorders {NS}><w:top w:val="single" w:sz="12" w:color="00FF00"/><w:insideH w:val="single" w:sz="4" w:color="0000FF"/></w:tblBorders>'))
    for i, (tc, txt) in enumerate(zip(t._tbl.iter(qn("w:tc")), "ABCD")):
        tc.find(qn("w:p")).append(parse_xml(f'<w:r {NS}><w:t>{txt}</w:t></w:r>'))
        if i == 0:
            for x in parse_xml(f'<w:tcPr {NS}><w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/><w:vAlign w:val="center"/></w:tcPr>'):
                tc.find(qn("w:tcPr")).append(x)
    t.cell(0, 1).merge(t.cell(1, 1))  # vertical merge
    add(FIELD)
    add(TEXTBOX)
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()
