"""Small OOXML helpers shared by the base-doc generator and the exporter."""
import io
import zipfile

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def add_field(p, instr: str, shown: str = "", dirty: bool = True) -> None:
    """Complex field (TOC, PAGE...) in paragraph `p`; dirty = Word recalculates it on open."""
    def run(*kids):
        r = OxmlElement("w:r")
        for k in kids:
            r.append(k)
        p._p.append(r)

    def ch(kind):
        e = OxmlElement("w:fldChar")
        e.set(qn("w:fldCharType"), kind)
        if kind == "begin" and dirty:
            e.set(qn("w:dirty"), "true")
        return e

    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    t = OxmlElement("w:t")
    t.text = shown
    t.set(qn("xml:space"), "preserve")
    run(ch("begin")), run(it), run(ch("separate")), run(t), run(ch("end"))


def stable_zip(data: bytes) -> bytes:
    """Re-pack with fixed timestamps/attrs: same parts => same bytes (python-docx stamps 'now')."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            zi = zipfile.ZipInfo(i.filename, (1980, 1, 1, 0, 0, 0))
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o600 << 16
            zout.writestr(zi, zin.read(i))
    return out.getvalue()
