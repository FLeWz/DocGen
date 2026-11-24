"""Synthetic DOCX with an embedded document (OLE object): an embedded .docx holding a code-block paragraph, a PNG
preview as VML imagedata. Variants with dangerous objects for the sanitiser tests. No customer content."""
import io

from docx import Document
from docx.opc.part import Part
from docx.opc.packuri import PackURI
from docx.oxml import parse_xml
from PIL import Image

from .sample import png

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml" '
      'xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
OBJ = ('<w:p %s><w:r><w:object w:dxaOrig="3000" w:dyaOrig="1500"><v:shape id="_x0000_i1025" style="width:150pt;height:75pt" o:ole="">'
       '<v:imagedata r:id="%s" o:title=""/></v:shape><o:OLEObject Type="%s" ProgID="%s" ShapeID="_x0000_i1025" DrawAspect="Content" '
       'ObjectID="_1" r:id="%s"/></w:object></w:r></w:p>')
RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def embedded_docx() -> bytes:
    d = Document()
    d.add_paragraph("CODE-BLOCK-MARKER print('hello')")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def build(blob: bytes | None = None, prog="Word.Document.12", typ="Embed", external=False, between=("Before the object", "After the object")) -> bytes:
    """Paragraph, the object (preview picture + embedded part), paragraph. `blob` replaces the embedded part."""
    d = Document()
    d.add_paragraph(between[0])
    pkg = d.part.package
    img = io.BytesIO()
    Image.new("RGB", (200, 100), (200, 30, 30)).save(img, "PNG")
    ip = Part(PackURI("/word/media/preview1.png"), "image/png", img.getvalue(), pkg)
    irid = d.part.relate_to(ip, RT + "image")
    if external:
        orid = d.part.relate_to("file:///C:/evil.exe", RT + "oleObject", is_external=True)
    else:
        op = Part(PackURI("/word/embeddings/oleObject1.bin"), "application/vnd.openxmlformats-officedocument.oleObject",
                  blob if blob is not None else embedded_docx(), pkg)
        orid = d.part.relate_to(op, RT + "oleObject")
    body = d.element.body
    body.insert(len(body) - 1, parse_xml(OBJ % (NS, irid, typ, prog, orid)))
    d.add_paragraph(between[1])
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()
