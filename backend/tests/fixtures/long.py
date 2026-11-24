"""Long synthetic DOCX: `n` numbered paragraphs, a table every 50, an image every 100, one text box (opaque) per 120, headings per 100,
two sections (a section break in the middle). `python -m tests.fixtures.long out.docx [n]`."""
import io
import sys

from docx import Document
from docx.enum.section import WD_SECTION
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from .sample import png

TEXTBOX = ('<w:p %s xmlns:v="urn:schemas-microsoft-com:vml"><w:r><w:pict><v:shape id="tb%d" type="#_x0000_t202" style="width:120pt;height:40pt">'
           '<v:textbox><w:txbxContent><w:p><w:r><w:t>BOX-%d</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>')


def build(n: int = 600) -> bytes:
    d = Document()
    d.sections[0].header.paragraphs[0].text = "LONG-HEADER"
    d.sections[0].footer.paragraphs[0].text = "LONG-FOOTER"
    for i in range(n):
        if i % 100 == 0:
            d.add_heading(f"Chapter {i // 100 + 1}", 1)
        if i == n // 2:
            d.add_section(WD_SECTION.NEW_PAGE)
        d.add_paragraph(f"Paragraph {i} " + "lorem ipsum dolor sit amet " * 6)
        if i % 50 == 25:
            t = d.add_table(rows=2, cols=2)
            for r, row in enumerate(t.rows):
                for c, cell in enumerate(row.cells):
                    cell.text = f"T{i}-{r}{c}"
        if i == 300:  # a table taller than a page: page gaps fall between its rows
            t = d.add_table(rows=90, cols=2)
            for r, row in enumerate(t.rows):
                row.cells[0].text, row.cells[1].text = f"BIG-{r}", "x " * 10
        if i % 100 == 50:
            d.add_picture(png("red", (60, 30)))
        if i % 120 == 60:
            d.element.body.insert(len(d.element.body) - 1, parse_xml(TEXTBOX % (nsdecls("w"), i, i)))
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


if __name__ == "__main__":
    data = build(int(sys.argv[2]) if len(sys.argv) > 2 else 600)
    (sys.stdout.buffer if sys.argv[1] == "-" else open(sys.argv[1], "wb")).write(data)
