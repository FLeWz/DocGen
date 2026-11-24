"""Small OOXML helpers shared by the base-doc generator and the exporter."""
import io
import posixpath
import re
import zipfile

from lxml import etree

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
FORBIDDEN = {"altChunk", "subDoc", "externalData", "control", "binData", "oleObj", "movie"}
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
        if loc(e.tag) == "OLEObject" and (e.get("Type") != "Embed" or rel_ids is None or not e.get("{%s}id" % R_NS)):
            raise ValueError("only embedded objects of this package are allowed")  # `Link`, no relationship: refused
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


OLE_PROGID_BAD = re.compile(r"^(Package|OLE2Link|Forms\.|ShellObject|Packager)", re.I)
OLE_MAGIC = (b"\xd0\xcf\x11\xe0", b"PK\x03\x04", b"%PDF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"{\\rtf")  # CFB (Word/Excel/ODT servers), zip (docx/xlsx/odt), pdf, images, rtf
MAX_OLE = 10 * 1024 * 1024
O_NS = "urn:schemas-microsoft-com:office:office"


def ole_problem(ole, rels: dict, zf) -> str | None:
    """Why an <o:OLEObject> must not stay (None = a normal embedded document, kept byte for byte): never executed or
    parsed by us, but its binary travels in every export. `rels` = Id -> (Target, external) of the part."""
    tgt, ext = rels.get(ole.get("{%s}id" % R_NS), (None, True))[:2]
    prog = ole.get("ProgID") or ""
    if ole.get("Type") != "Embed" or ext or tgt is None:
        return "linked or external object"
    path = posixpath.normpath(posixpath.join("word", tgt))
    if not path.startswith("word/embeddings/") or path not in zf.namelist():
        return "object outside word/embeddings"
    if zf.getinfo(path).file_size > MAX_OLE:
        return "object larger than 10 MB"
    return blob_problem(zf.read(path), prog)


def blob_problem(blob: bytes, prog: str = "") -> str | None:
    if len(blob) > MAX_OLE:
        return "object larger than 10 MB"
    if OLE_PROGID_BAD.match(prog):
        return "packaged file object"
    if b"vbaProject" in blob or "VBA".encode("utf-16-le") in blob:
        return "macros"
    if not blob.startswith(OLE_MAGIC) and (b"\0" in blob[:4096] or blob[:2] in (b"MZ", b"#!") or blob[:4] == b"\x7fELF"):
        return "executable or unknown binary"
    return None


def neutralize_ole(data: bytes) -> tuple[bytes, list[str]]:
    """Drop only the dangerous OLE binaries (see `ole_problem`) from an (already validated) DOCX; normal embedded documents are untouched: the <o:OLEObject> element, its
    relationship and its part go; the preview picture (v:imagedata) stays as a plain picture. Returns (package, warnings);
    the same bytes when nothing had to go."""
    out, warns, drop_parts, new = [], [], set(), {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if not re.fullmatch(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", name) or b"OLEObject" not in zf.read(name):
                continue
            rname = posixpath.join(posixpath.dirname(name), "_rels", posixpath.basename(name) + ".rels")
            if rname not in zf.namelist():
                continue
            rroot = etree.fromstring(zf.read(rname), etree.XMLParser(resolve_entities=False, no_network=True))
            rels = {r.get("Id"): (r.get("Target"), r.get("TargetMode") == "External", r.get("Type")) for r in rroot}
            root = etree.fromstring(zf.read(name), etree.XMLParser(resolve_entities=False, no_network=True))
            hit = False
            for ole in list(root.iter("{%s}OLEObject" % O_NS)):
                why = ole_problem(ole, rels, zf)
                if not why:
                    continue
                hit = True
                warns.append(f"embedded object ({ole.get('ProgID') or 'unknown'}) dropped from {name}: {why}; its preview picture is kept")
                rid = ole.get("{%s}id" % R_NS)
                for r in list(rroot):
                    if r.get("Id") == rid:
                        if r.get("TargetMode") != "External":
                            drop_parts.add(posixpath.normpath(posixpath.join("word", r.get("Target"))))
                        rroot.remove(r)
                par = ole.getparent()
                par.remove(ole)
                for sh in par.iter("{urn:schemas-microsoft-com:vml}shape"):
                    sh.attrib.pop("{%s}ole" % O_NS, None)
            if hit:
                new[name], new[rname] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), etree.tostring(rroot, xml_declaration=True, encoding="UTF-8", standalone=True)
        if not new:
            return data, []
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zo:
            for i in zf.infolist():
                if i.filename not in drop_parts:
                    zo.writestr(i, new.get(i.filename) or zf.read(i))
    return buf.getvalue(), warns


PART_KINDS = {"image": ("/image", "media/image", {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
                                                  "emf": "image/x-emf", "wmf": "image/x-wmf"}),
              "oleObject": ("/oleObject", "embeddings/oleObject", {"bin": "application/vnd.openxmlformats-officedocument.oleObject"})}
MAX_PARTS_B64 = 3_000_000


def rel_map(zf, part: str = "word/document.xml") -> dict:
    """Id -> (Target, external, Type) of a part's relationships."""
    rn = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if rn not in zf.namelist():
        return {}
    return {r.get("Id"): (r.get("Target"), r.get("TargetMode") == "External", r.get("Type")) for r in DET.fromstring(zf.read(rn), forbid_dtd=True)}


def collect_parts(el, zf, rels: dict) -> list[dict]:
    """Parts an opaque element's r:* attributes point to (pictures, embedded objects of word/media|embeddings) as
    [{rid, kind, ext, b64}] so the block can leave its package (snippets); [] when something is missing or too big."""
    import base64
    out, total = [], 0
    for rid in sorted({v for e in el.iter() for k, v in e.attrib.items() if k.startswith("{%s}" % R_NS)}):
        tgt, ext, typ = rels.get(rid, (None, True, ""))
        kind = next((k for k, v in PART_KINDS.items() if (typ or "").endswith(v[0])), None)
        path = tgt and posixpath.normpath(posixpath.join("word", tgt))
        if ext or not kind or not path.startswith(("word/media/", "word/embeddings/")) or path not in zf.namelist():
            return []
        e = path.rpartition(".")[2].lower()
        blob = zf.read(path)
        if e not in PART_KINDS[kind][2] or (kind == "oleObject" and blob_problem(blob)):
            return []
        total += len(blob) * 4 // 3
        out.append(dict(rid=rid, kind=kind, ext=e, b64=base64.b64encode(blob).decode()))
    return [] if total > MAX_PARTS_B64 else out
