"""Small OOXML helpers shared by the base-doc generator and the exporter."""
import io
import re
import zipfile

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException
from xml.etree.ElementTree import ParseError

from docx.oxml import OxmlElement, parse_xml
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


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAX_FRAG = 2_000_000
# parts that pull in other content, embedded objects or external data: never written back
FORBIDDEN = {"altChunk", "subDoc", "externalData", "OLEObject", "control", "binData", "oleObj", "movie"}
BAD_FIELD = re.compile(r"\b(DDE|DDEAUTO|INCLUDETEXT|INCLUDEPICTURE|MACROBUTTON|LINK|IMPORT|AUTOTEXT|DATABASE|ADDIN)\b", re.I)
WORD_2010 = ("http://schemas.microsoft.com/office/word/2010/wordml", "http://schemas.microsoft.com/office/word/2012/wordml",
             "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing")


def opaque_element(xml, rel_ids: set | None, roots=None):
    """Validate and parse a raw OOXML fragment (opaque block, table properties) before it is written into a package.
    DTDs/entities are rejected (defusedxml, then lxml with resolve_entities off); the root must be a Word element
    (`roots`: allowed local names, default any but sectPr); denylisted parts/fields are refused. `rel_ids` = relationship
    ids that exist in the target package; None = the target is another document, so r:* references are refused and
    Word 2010+ attributes (paraId...) are stripped. Raises ValueError."""
    if not isinstance(xml, str) or not xml.strip() or len(xml) > MAX_FRAG:
        raise ValueError("empty or too large")
    if re.search(r"<!(DOCTYPE|ENTITY)", xml, re.I):
        raise ValueError("DTD/entities not allowed")
    try:
        DET.fromstring(xml, forbid_dtd=True)
        el = parse_xml(xml)
    except (DefusedXmlException, ParseError, Exception):
        raise ValueError("invalid XML")
    loc = lambda t: t.rpartition("}")[2]
    if not el.tag.startswith("{%s}" % W_NS) or loc(el.tag) == "sectPr" or (roots and loc(el.tag) not in roots):
        raise ValueError(f"unexpected root element {loc(el.tag)}")
    for e in el.iter():
        if not isinstance(e.tag, str):
            raise ValueError("comments/processing instructions not allowed")
        if loc(e.tag) in FORBIDDEN:
            raise ValueError(f"{loc(e.tag)} not allowed")
        if loc(e.tag) == "instrText" and BAD_FIELD.search(e.text or "") or BAD_FIELD.search(e.get("{%s}instr" % W_NS) or ""):
            raise ValueError("field type not allowed")
        for k in list(e.attrib):
            if k.startswith("{%s}" % R_NS):
                if rel_ids is None or e.attrib[k] not in rel_ids:
                    raise ValueError("references a relationship that does not exist in the target document")
            elif rel_ids is None and k.startswith(tuple("{%s}" % n for n in WORD_2010)):
                del e.attrib[k]
    return el


def rel_ids(zip_bytes: bytes) -> set:
    """Relationship ids of word/document.xml in an (already validated) DOCX."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        if "word/_rels/document.xml.rels" not in z.namelist():
            return set()
        root = DET.fromstring(z.read("word/_rels/document.xml.rels"), forbid_dtd=True)
    return {r.get("Id") for r in root}
