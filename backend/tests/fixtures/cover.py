"""Synthetic DOCX with a cover page (title field in a bookmark, form-field table, subtitle), a section break, a TOC,
numbered headings linked to Heading styles, first-page header with an image, REF/TITLE/PAGE fields in header/footer.
Used by tests/test_vars.py and e2e/basedoc.py:  python -m tests.fixtures.cover out.docx"""
import io
import sys
import zipfile

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?"
       b"\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")
HEAD_STYLES = f'''<w:styles {NS}><w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:numPr><w:numId w:val="3"/></w:numPr><w:spacing w:before="360"/><w:ind w:left="567" w:hanging="567"/><w:outlineLvl w:val="0"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:numPr><w:ilvl w:val="1"/><w:numId w:val="3"/></w:numPr><w:ind w:left="578" w:hanging="578"/><w:outlineLvl w:val="1"/></w:pPr></w:style>
</w:styles>'''
NUMBERING = (f'<w:numbering {NS}><w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="multilevel"/>'
             '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:pStyle w:val="Heading1"/><w:lvlText w:val="%1"/></w:lvl>'
             '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:pStyle w:val="Heading2"/><w:lvlText w:val="%1.%2"/></w:lvl>'
             '</w:abstractNum><w:num w:numId="3"><w:abstractNumId w:val="1"/></w:num></w:numbering>')


def fld(instr, shown, rpr=""):
    r = lambda inner: f"<w:r>{rpr}{inner}</w:r>"
    return (r('<w:fldChar w:fldCharType="begin"/>') + r(f"<w:instrText> {instr} </w:instrText>") + r('<w:fldChar w:fldCharType="separate"/>')
            + r(f"<w:t>{shown}</w:t>") + r('<w:fldChar w:fldCharType="end"/>'))


def p(inner, style=None, ppr=""):
    st = f'<w:pStyle w:val="{style}"/>' if style else ""
    return f"<w:p><w:pPr>{st}{ppr}</w:pPr>{inner}</w:p>"


def tx(s):
    return f"<w:r><w:t>{s}</w:t></w:r>"


SECT = ('<w:sectPr><w:headerReference w:type="default" r:id="rId_header1"/><w:headerReference w:type="first" r:id="rId_header2"/>'
        '<w:footerReference w:type="default" r:id="rId_footer1"/><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="425" w:footer="329"/><w:titlePg/></w:sectPr>')
BODY = (
    p('<w:bookmarkStart w:id="1" w:name="DocTitle"/>' + fld("FORMTEXT", "Acme Spec") + '<w:bookmarkEnd w:id="1"/>', "Title")
    + p(tx("Project Falcon"), "Subtitle")
    + '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid><w:tr>'
      f'<w:tc><w:p>{tx("Document revision:")}</w:p></w:tc><w:tc><w:p>{fld("FORMTEXT", "1.2")}</w:p></w:tc></w:tr>'
      f'<w:tr><w:tc><w:p>{tx("Notes")}</w:p></w:tc><w:tc><w:p>{tx("plain table text")}</w:p></w:tc></w:tr></w:tbl>'
    + p("", ppr=SECT)  # section break (cover section)
    + '<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/></w:docPartObj></w:sdtPr><w:sdtContent>'
    + p(fld('TOC \\o "1-3"', "Introduction 1"), "Normal") + "</w:sdtContent></w:sdt>"
    + p(tx("Intro"), "Heading1") + p(tx("This body text stays editable content."))
    + p(tx("Scope"), "Heading2") + p(tx("More body.")))
HEADERS = {
    "header1": p(fld("REF DocTitle \\h", "Acme Spec") + tx(" | ") + fld("PAGE", "1")),
    "header2": p(fld("REF Gone_Bookmark \\h", "Old Title") + '<w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
                 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><wp:extent cx="9525" cy="9525"/>'
                 '<wp:docPr id="1" name="logo"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="l"/><pic:cNvPicPr/></pic:nvPicPr>'
                 '<pic:blipFill><a:blip r:embed="rIdImg"/></pic:blipFill><pic:spPr/></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'),
    "footer1": p(tx("Confidential ") + fld("TITLE", "Stale Title") + tx(" page ") + fld("PAGE", "1") + tx(" of ") + fld("NUMPAGES", "9"), "Normal"),
}


REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
      + "".join(f'<Override PartName="/word/{n}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{k}+xml"/>'
                for n, k in (("document", "document.main"), ("styles", "styles"), ("numbering", "numbering"), ("header1", "header"),
                             ("header2", "header"), ("footer1", "footer"))) + "</Types>")


def build() -> bytes:
    rels = "".join(f'<Relationship Id="rId_{n}" Type="{REL}{k}" Target="{n}.xml"/>' for n, k in
                   (("styles", "styles"), ("numbering", "numbering"), ("header1", "header"), ("header2", "header"), ("footer1", "footer")))
    parts = {"[Content_Types].xml": CT,
             "_rels/.rels": f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{REL}officeDocument" Target="word/document.xml"/></Relationships>',
             "word/document.xml": f"<w:document {NS}><w:body>{BODY}{SECT}</w:body></w:document>",
             "word/styles.xml": HEAD_STYLES, "word/numbering.xml": NUMBERING,
             "word/_rels/document.xml.rels": f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
             "word/_rels/header2.xml.rels": f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdImg" Type="{REL}image" Target="media/logo.png"/></Relationships>',
             **{f"word/{n}.xml": f'<w:{t} {NS} xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">{x}</w:{t}>'
                for n, t, x in (("header1", "hdr", HEADERS["header1"]), ("header2", "hdr", HEADERS["header2"]), ("footer1", "ftr", HEADERS["footer1"]))}}
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in parts.items():
            z.writestr(k, v)
        z.writestr("word/media/logo.png", PNG)
    return b.getvalue()




if __name__ == "__main__":
    (sys.stdout.buffer if sys.argv[1] == "-" else open(sys.argv[1], "wb")).write(build())
