"""Base documents: DOCX validation (zip structure, bombs, XXE), styles.xml parsing, placeholder
detection and the bundled default template."""
import io
import re
import zipfile
from functools import lru_cache

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from xml.etree.ElementTree import ParseError

from .ooxml import add_field, stable_zip

MAX_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_RATIO = 100  # total uncompressed / compressed
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAIN_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
CONTENT_RE = re.compile(r"\{\{\s*CONTENT\s*\}\}")
SLOT_RE = re.compile(r"\{\{\s*SLOT:([A-Za-z_]\w{0,39})\s*\}\}")
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,39}")
FONT_RE = re.compile(r"[A-Za-z0-9 _-]{1,60}")
COLOR_RE = re.compile(r"[0-9a-fA-F]{6}")


class BadDocx(ValueError):
    pass


def _val(el, tag):
    e = el.find(W + tag) if el is not None else None
    return None if e is None else e.get(W + "val")


def _flag(rpr, tag) -> bool:
    e = rpr.find(W + tag) if rpr is not None else None
    return e is not None and e.get(W + "val", "true") not in ("0", "false", "off")


def parse_styles(root) -> list[dict]:
    """styles.xml -> rows shaped like the org `Style` table (only basic props, no basedOn resolution).
    Values are sanitized so they always satisfy StyleOut."""
    out = []
    for s in root.iter(W + "style"):
        typ, sid = s.get(W + "type"), s.get(W + "styleId")
        if typ not in ("paragraph", "character", "table") or not sid or not ID_RE.fullmatch(sid):
            continue
        ppr, rpr = s.find(W + "pPr"), s.find(W + "rPr")
        level = None
        ol = _val(ppr, "outlineLvl")
        if typ == "paragraph":
            m = re.fullmatch(r"Heading([1-9])", sid)
            level = int(ol) + 1 if ol and ol.isdigit() and int(ol) < 9 else int(m[1]) if m else None
        sz, col = _val(rpr, "sz"), _val(rpr, "color")
        fonts = rpr.find(W + "rFonts") if rpr is not None else None
        fam = fonts.get(W + "ascii") if fonts is not None else None
        sp = ppr.find(W + "spacing") if ppr is not None else None
        tw = lambda k: float(sp.get(W + k)) / 20 if sp is not None and (sp.get(W + k) or "").isdigit() else 0.0
        out.append(dict(
            style_id=sid, name=(_val(s, "name") or sid)[:80],
            kind="heading" if level else typ, level=level,
            color="#" + col.lower() if col and COLOR_RE.fullmatch(col) else None,
            font_size=int(sz) / 2 if sz and sz.isdigit() and 0 < int(sz) <= 400 else None,
            font_family=fam if fam and FONT_RE.fullmatch(fam) else None,
            numbering="decimal" if ppr is not None and ppr.find(W + "numPr") is not None else "none",
            space_before=min(tw("before"), 200), space_after=min(tw("after"), 200),
            bold=_flag(rpr, "b"), italic=_flag(rpr, "i")))
        if len(out) >= 500:
            break
    return out


def find_placeholders(root) -> dict:
    """Whole-paragraph `{{ CONTENT }}` and `{{ SLOT:name }}` markers in document.xml."""
    content, slots = False, []
    for p in root.iter(W + "p"):
        t = "".join(x.text or "" for x in p.iter(W + "t")).strip()
        if CONTENT_RE.fullmatch(t):
            content = True
        elif (m := SLOT_RE.fullmatch(t)) and m[1] not in slots:
            slots.append(m[1])
    return dict(content=content, slots=slots)


def validate(data: bytes) -> dict:
    """Raise BadDocx unless `data` is a sane DOCX; return {styles, placeholders}."""
    if len(data) > MAX_BYTES:
        raise BadDocx("file too large (max 25 MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BadDocx("not a DOCX file (not a zip archive)")
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise BadDocx("too many entries in archive")
        names = {i.filename for i in infos}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise BadDocx("not a DOCX file (missing [Content_Types].xml or word/document.xml)")
        if any(n.startswith("/") or ".." in n.split("/") or n.lower().endswith("vbaproject.bin") for n in names):
            raise BadDocx("unsafe or macro-enabled archive")
        total = 0
        for i in infos:  # count real decompressed bytes: headers can lie
            if i.flag_bits & 1:
                raise BadDocx("encrypted archive")
            with zf.open(i) as f:
                while chunk := f.read(65536):
                    total += len(chunk)
                    if total > MAX_UNCOMPRESSED or total > MAX_RATIO * max(len(data), 1):
                        raise BadDocx("archive expands too much (zip bomb guard)")
        try:
            trees = {n: DET.fromstring(zf.read(n), forbid_dtd=True)
                     for n in names if n.endswith((".xml", ".rels"))}
        except (DefusedXmlException, ParseError) as e:
            raise BadDocx(f"invalid or unsafe XML: {type(e).__name__}")
        if MAIN_CT not in zf.read("[Content_Types].xml").decode("utf-8", "replace"):
            raise BadDocx("not a Word document")
    try:
        Document(io.BytesIO(data))
    except Exception:
        raise BadDocx("not a readable DOCX file")
    st = trees.get("word/styles.xml")
    return dict(styles=parse_styles(st) if st is not None else [],
                placeholders=find_placeholders(trees["word/document.xml"]))


def _row(t, *cells):
    for c, txt in zip(t.add_row().cells, cells):
        c.text = txt


@lru_cache
def default_docx() -> bytes:
    """Bundled base document: Normal/Title/Heading1-4 (python-docx template), header/footer with page
    fields, revision-history and linked-documents tables with docxtpl-style `{%tr %}` rows, CONTENT slot."""
    d = Document()
    for s in d.sections:
        s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Cm(2.5)
    d.styles["Normal"].font.name, d.styles["Normal"].font.size = "Calibri", Pt(11)
    for n, size in ((1, 18), (2, 14), (3, 12), (4, 11)):
        f = d.styles[f"Heading {n}"].font
        f.name, f.size, f.color.rgb = "Calibri", Pt(size), RGBColor(0x1F, 0x38, 0x64)
    sec = d.sections[0]
    sec.header.paragraphs[0].text = "{{ org.name }} — {{ doc.title }}"
    fp = sec.footer.paragraphs[0]
    fp.add_run("Page ")
    add_field(fp, "PAGE", "1")
    fp.add_run(" of ")
    add_field(fp, "NUMPAGES", "1")
    d.add_paragraph("{{ doc.title }}", style="Title")
    d.add_paragraph("Version {{ doc.version }} · {{ doc.date }} · {{ author }}")
    for title, head, var, row in (
            ("Revision history", ("Version", "Date", "Author", "Description"), "revision_history",
             ("{{ r.version }}", "{{ r.date }}", "{{ r.author }}", "{{ r.message }}")),
            ("Linked documents", ("Title", "ID", "Version", "Link"), "linked_documents",
             ("{{ l.title }}", "{{ l.doc_id }}", "{{ l.version }}", "{{ l.url }}"))):
        d.add_paragraph(title, style="Heading 1")
        t = d.add_table(rows=1, cols=4)
        t.style = "Table Grid"
        for c, h in zip(t.rows[0].cells, head):
            c.text = h
        v = var[0]
        _row(t, f"{{%tr for {v} in {var} %}}", "", "", "")
        _row(t, *row)
        _row(t, "{%tr endfor %}", "", "", "")
    d.add_paragraph("{{ CONTENT }}")
    d.core_properties.author = d.core_properties.last_modified_by = "DocGen"
    d.core_properties.title = "Default base document"
    import datetime as dt
    d.core_properties.created = d.core_properties.modified = dt.datetime(2000, 1, 1)
    b = io.BytesIO()
    d.save(b)
    return stable_zip(b.getvalue())
