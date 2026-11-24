"""Editor features beyond paragraphs/tables: fields (PAGE, NUMPAGES, SEQ captions, REF cross references), footnotes/endnotes,
bookmarks, marked sections (`sec` block attr -> bookmark range), table of contents / figures / tables (`toc` node -> SDT or field
structure), section breaks. Used by docx_export.Builder (a mixin) and docx_import (reading them back).

Content shapes (TipTap JSON):
  field      inline atom {code: PAGE|NUMPAGES|SECTIONPAGES|SEQ|REF|PAGEREF, arg, text}   (text = cached result)
  footnote   inline atom {kind: footnote|endnote, text}
  toc        block atom {kind: toc|tof|tot, title, levels: "1-3", label: "Figure"|"Table", xml|null (imported original), refresh}
  sectBreak  block atom {brk: nextPage|continuous|evenPage|oddPage, orient: portrait|landscape, top,right,bottom,left (twips)}
  attrs on blocks: `sec` {id, kind: section|appendix, title}  (consecutive blocks with one id = one marked section, written
  as a bookmark range `dgsec_<kind>_<id>_<Title_words>` around them), `bm` (a bookmark around the paragraph text, REF target)
"""
import copy
import re

from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from .ooxml import opaque_element

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FIELD_CODES = {"PAGE", "NUMPAGES", "SECTIONPAGES", "SEQ", "REF", "PAGEREF"}
ARG_RE = re.compile(r"[A-Za-z0-9_ .-]{0,60}")
BM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
SEC_KINDS = {"section", "appendix"}
SEC_NAME = re.compile(r"dgsec_(section|appendix)_(\d{1,9})_([A-Za-z0-9_]{0,24})$")
TOC_KINDS = {"toc": "Table of Contents", "tof": "Figures", "tot": "Tables"}
BREAKS = {"nextPage", "continuous", "evenPage", "oddPage"}


def bm_name(s) -> str | None:
    return s if isinstance(s, str) and BM_RE.fullmatch(s) else None


def sec_bookmark(sec: dict) -> str | None:
    """`sec` attr -> bookmark name, None when invalid."""
    if not isinstance(sec, dict) or sec.get("kind") not in SEC_KINDS:
        return None
    sid = str(sec.get("id") or "")
    if not re.fullmatch(r"\d{1,9}", sid):
        return None
    title = re.sub(r"[^A-Za-z0-9]+", "_", str(sec.get("title") or "")).strip("_")[:24]
    return f"dgsec_{sec['kind']}_{sid}_{title}"


def sec_from_bookmark(name: str) -> dict | None:
    m = SEC_NAME.match(name or "")
    return {"id": m[2], "kind": m[1], "title": m[3].replace("_", " ").strip()} if m else None


def instr_of(a: dict) -> tuple[str, str] | None:
    """field attrs -> (instruction text, cached result); None for anything not on the whitelist."""
    code, arg = a.get("code"), str(a.get("arg") or "")
    if code not in FIELD_CODES or not ARG_RE.fullmatch(arg):
        return None
    shown = str(a.get("text") or "")[:200] or {"PAGE": "1", "NUMPAGES": "1", "SECTIONPAGES": "1", "SEQ": "1"}.get(code, "")
    if code == "SEQ":
        return f"SEQ {arg.strip() or 'Figure'} \\* ARABIC", shown
    if code in ("REF", "PAGEREF"):
        return (f"{code} {arg} \\h", shown) if bm_name(arg) else None
    return code, shown


def ptext(n: dict, seq: dict | None = None) -> str:
    """Text of a block as a table-of-contents/figures entry shows it (field results included)."""
    out = []
    for x in _walk(n):
        if x.get("type") == "text":
            out.append(x.get("text", ""))
        elif x.get("type") == "field":
            out.append(str((seq or {}).get(id(x)) or (x.get("attrs") or {}).get("text") or ""))
    return "".join(out)


def _walk(n):
    yield n
    for c in n.get("content") or []:
        yield from _walk(c)


def seq_numbers(blocks: list) -> dict:
    """id(field node) -> number: SEQ fields count per label in document order (the cached results Word would compute)."""
    count, out = {}, {}
    for b in blocks:
        for x in _walk(b):
            if x.get("type") == "field" and (x.get("attrs") or {}).get("code") == "SEQ":
                label = str(x["attrs"].get("arg") or "Figure").strip() or "Figure"
                count[label] = count.get(label, 0) + 1
                out[id(x)] = count[label]
    return out


def toc_entries(blocks: list, a: dict, seq: dict | None = None) -> list[tuple[int, str]]:
    """(level, text) lines of a toc/tof/tot node computed from the headings / caption paragraphs of `blocks`."""
    kind = a.get("kind")
    if kind == "toc":
        m = re.fullmatch(r"(\d)-(\d)", str(a.get("levels") or "1-3"))
        lo, hi = (int(m[1]), int(m[2])) if m else (1, 3)
        hs = [(int((b.get("attrs") or {}).get("level") or 1), ptext(b)) for b in blocks if b.get("type") == "heading"]
        return [(lv, t) for lv, t in hs if lo <= lv <= hi and t.strip()]
    label = str(a.get("label") or ("Table" if kind == "tot" else "Figure"))
    return [(1, ptext(b, seq)) for b in blocks if b.get("type") in ("paragraph", "heading")
            and any(x.get("type") == "field" and (x.get("attrs") or {}).get("code") == "SEQ" and str((x.get("attrs") or {}).get("arg") or "").strip() == label
                    for x in _walk(b))]


class Rich:
    """Mixin of docx_export.Builder."""

    # ---- ids ----
    def _init_rich(self, next_bm: int = 9000):
        self.bm_id, self.notes, self.sec_open = next_bm, {"footnote": [], "endnote": []}, None
        self.seq, self.top_blocks = {}, []

    def new_bm_id(self) -> int:
        self.bm_id += 1
        return self.bm_id

    # ---- fields ----
    def field(self, p, a: dict, marks: list) -> None:
        got = instr_of(a)
        if got is None:
            self.warn.append(f"field {str(a.get('code'))[:20]!r} skipped")
            return
        instr, shown = got
        if a.get("code") == "SEQ" and id(a) in self.seq:
            shown = str(self.seq[id(a)])
        dirty = a.get("code") in ("REF", "PAGEREF")

        def run(*kids):
            r = p.add_run()
            self.apply_marks(r, marks)
            for k in kids:
                r._r.append(k)

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

    # ---- footnotes / endnotes ----
    def note(self, p, a: dict, marks: list) -> None:
        kind = "endnote" if a.get("kind") == "endnote" else "footnote"
        nid = len(self.notes[kind]) + 1 + self.note_base.get(kind, 0)
        self.notes[kind].append((nid, str(a.get("text") or "")[:5000]))
        self.ensure_style("FootnoteReference" if kind == "footnote" else "EndnoteReference", "character")
        r = p.add_run()
        rpr = r._r.get_or_add_rPr()
        rs = OxmlElement("w:rStyle")
        rs.set(qn("w:val"), "FootnoteReference" if kind == "footnote" else "EndnoteReference")
        rpr.insert(0, rs)
        ref = OxmlElement(f"w:{kind}Reference")
        ref.set(qn("w:id"), str(nid))
        r._r.append(ref)

    def write_notes(self) -> None:
        """footnotes.xml / endnotes.xml: created (with the separator entries Word expects) or appended to."""
        for kind, items in self.notes.items():
            if not items:
                continue
            tag, ct, rt = ("footnotes", "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml", RT.FOOTNOTES) if kind == "footnote" \
                else ("endnotes", "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml", RT.ENDNOTES)
            part = next((x for x in self.doc.part.package.iter_parts() if str(x.partname) == f"/word/{tag}.xml"), None)
            if part is None:
                from docx.opc.packuri import PackURI
                from docx.opc.part import Part
                root = parse_xml(f'<w:{tag} xmlns:w="{W_NS}"><w:{kind} w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
                                 f'<w:r><w:separator/></w:r></w:p></w:{kind}><w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/>'
                                 f'</w:pPr><w:r><w:continuationSeparator/></w:r></w:p></w:{kind}></w:{tag}>')
                part = Part(PackURI(f"/word/{tag}.xml"), ct, b"", self.doc.part.package)
                self.doc.part.relate_to(part, rt)
                self.footnote_settings(kind)
            else:
                root = parse_xml(part.blob)
            sid = "FootnoteText" if kind == "footnote" else "EndnoteText"
            rid = "FootnoteReference" if kind == "footnote" else "EndnoteReference"
            self.ensure_style(sid, "paragraph")
            for nid, text in items:
                esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                root.append(parse_xml(f'<w:{kind} xmlns:w="{W_NS}" w:id="{nid}"><w:p><w:pPr><w:pStyle w:val="{sid}"/></w:pPr><w:r><w:rPr><w:rStyle w:val="{rid}"/></w:rPr>'
                                      f'<w:{kind}Ref/></w:r><w:r><w:t xml:space="preserve"> {esc}</w:t></w:r></w:p></w:{kind}>'))
            from lxml import etree
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def footnote_settings(self, kind: str) -> None:
        st = self.doc.settings.element
        tag = "w:footnotePr" if kind == "footnote" else "w:endnotePr"
        if st.find(qn(tag)) is not None:
            return
        after = {"endnotePr", "compat", "docVars", "rsids", "mathPr", "attachedSchema", "themeFontLang", "clrSchemeMapping", "shapeDefaults",
                 "decimalSymbol", "listSeparator", "doNotIncludeSubdocsInStats", "doNotAutoCompressPictures", "forceUpgrade"}
        if kind == "endnote":
            after -= {"endnotePr"}
        e = parse_xml(f'<{tag} xmlns:w="{W_NS}"><w:{kind} w:id="-1"/><w:{kind} w:id="0"/></{tag}>')
        nxt = next((c for c in st if c.tag.split("}")[1] in after), None)
        nxt.addprevious(e) if nxt is not None else st.append(e)

    def ensure_style(self, sid: str, kind: str) -> None:
        """Styles the features rely on (Caption, TOC1.., footnote styles) when the package has none."""
        if sid in self.styles:
            return
        names = {"Caption": "caption", "FootnoteText": "footnote text", "FootnoteReference": "footnote reference", "EndnoteText": "endnote text",
                 "EndnoteReference": "endnote reference", "TableofFigures": "table of figures", "TOCHeading": "TOC Heading",
                 **{f"TOC{i}": f"toc {i}" for i in range(1, 10)}}
        body = {"Caption": '<w:pPr><w:spacing w:before="120" w:after="200" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:i/><w:iCs/><w:sz w:val="18"/></w:rPr>',
                "FootnoteReference": '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>', "EndnoteReference": '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>',
                "FootnoteText": '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr>',
                "EndnoteText": '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr>',
                "TOCHeading": '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr>'}.get(sid, "")
        if sid.startswith("TOC") and sid[3:].isdigit():
            body = f'<w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs><w:spacing w:after="100"/><w:ind w:left="{(int(sid[3:]) - 1) * 220}"/></w:pPr>'
        elif sid == "TableofFigures":
            body = '<w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs><w:spacing w:after="0"/></w:pPr>'
        if sid not in names:
            return
        self.doc.styles.element.append(parse_xml(
            f'<w:style xmlns:w="{W_NS}" w:type="{kind}" w:styleId="{sid}"><w:name w:val="{names[sid]}"/><w:basedOn w:val="{"DefaultParagraphFont" if kind == "character" else "Normal"}"/>'
            f'<w:uiPriority w:val="35"/><w:qFormat/>{body}</w:style>'))
        self.styles.add(sid)

    # ---- bookmarks ----
    def wrap_bookmark(self, p, name: str) -> None:
        """Bookmark around the runs of paragraph `p` (a REF target)."""
        i = self.new_bm_id()
        s = OxmlElement("w:bookmarkStart")
        s.set(qn("w:id"), str(i))
        s.set(qn("w:name"), name)
        e = OxmlElement("w:bookmarkEnd")
        e.set(qn("w:id"), str(i))
        ppr = p._p.pPr
        (ppr.addnext(s) if ppr is not None else p._p.insert(0, s))
        p._p.append(e)

    def body_add(self, el) -> None:
        s = self.doc.element.body.find(qn("w:sectPr"))
        s.addprevious(el) if s is not None else self.doc.element.body.append(el)

    def sec_track(self, n: dict) -> None:
        """Called before each top-level block: opens/closes the bookmark range of a marked section."""
        name = sec_bookmark((n.get("attrs") or {}).get("sec")) if n is not None else None
        cur = (n.get("attrs") or {}).get("sec", {}).get("id") if n is not None and name else None
        if self.sec_open and self.sec_open[0] == cur:
            return
        self.sec_close()
        if name:
            i = self.new_bm_id()
            s = OxmlElement("w:bookmarkStart")
            s.set(qn("w:id"), str(i))
            s.set(qn("w:name"), name)
            self.body_add(s)
            self.sec_open = (cur, i)

    def sec_close(self) -> None:
        if self.sec_open:
            e = OxmlElement("w:bookmarkEnd")
            e.set(qn("w:id"), str(self.sec_open[1]))
            self.body_add(e)
            self.sec_open = None

    # ---- table of contents / figures / tables ----
    def toc(self, a: dict) -> None:
        kind = a.get("kind") if a.get("kind") in TOC_KINDS else "toc"
        if a.get("xml"):  # imported: written back as it was (dirty flag only when asked)
            try:
                el = opaque_element(a["xml"], self.rels)
            except ValueError as e:
                self.warn.append(f"table of contents skipped: {e}")
                return
            kids = list(el) if el.tag == qn("w:sdtContent") else [el]
            if a.get("refresh"):
                for f in el.iter(qn("w:fldChar")):
                    if f.get(qn("w:fldCharType")) == "begin":
                        f.set(qn("w:dirty"), "true")
                        break
            for k in kids:
                self.body_add(k)
            return
        label = str(a.get("label") or ("Table" if kind == "tot" else "Figure"))
        if not ARG_RE.fullmatch(label):
            label = "Figure"
        m = re.fullmatch(r"(\d)-(\d)", str(a.get("levels") or "1-3"))
        levels = f"{m[1]}-{m[2]}" if m else "1-3"
        instr = f'TOC \\o "{levels}" \\h \\z \\u' if kind == "toc" else f'TOC \\h \\z \\c "{label}"'
        entries = toc_entries(self.top_blocks, {**a, "kind": kind, "label": label}, self.seq)
        paras = []
        title = str(a.get("title") or "")[:200]
        if title:
            self.ensure_style("TOCHeading", "paragraph")
            tp = self.doc.add_paragraph(title)
            self.apply_style(tp, "TOCHeading")
            paras.append(tp._p)
        lines = entries or [(1, "Right-click and choose Update Field to build this list.")]
        first = True
        for k, (lvl, text) in enumerate(lines):
            sid = f"TOC{min(max(lvl, 1), 9)}" if kind == "toc" else "TableofFigures"
            self.ensure_style(sid, "paragraph")
            p = self.doc.add_paragraph()
            self.apply_style(p, sid)
            if first:
                self._fld(p, "begin", True)
                self._fld(p, "instr", instr)
                self._fld(p, "separate")
                first = False
            r = p.add_run(text)
            if k == len(lines) - 1:
                self._fld(p, "end")
            paras.append(p._p)
        if kind == "toc":  # Word's own gallery wrapper: shows the "Update table" frame
            sdt = parse_xml(f'<w:sdt xmlns:w="{W_NS}"><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/><w:docPartUnique/></w:docPartObj></w:sdtPr><w:sdtContent/></w:sdt>')
            for x in paras:
                x.getparent().remove(x)
                sdt[1].append(x)
            self.body_add(sdt)

    def _fld(self, p, what: str, arg=None) -> None:
        r = OxmlElement("w:r")
        if what == "instr":
            e = OxmlElement("w:instrText")
            e.set(qn("xml:space"), "preserve")
            e.text = f" {arg} "
        else:
            e = OxmlElement("w:fldChar")
            e.set(qn("w:fldCharType"), what)
            if what == "begin" and arg:
                e.set(qn("w:dirty"), "true")
        r.append(e)
        p._p.append(r)

    # ---- section break ----
    def sect_break(self, a: dict) -> None:
        """A paragraph carrying a sectPr = the end of a section. Its page setup is the final section's, with the orientation,
        margins and start type given here (these describe the section that ENDS at the break, as in OOXML)."""
        final = self.doc.element.body.find(qn("w:sectPr"))
        sp = copy.deepcopy(final) if final is not None else parse_xml(f'<w:sectPr xmlns:w="{W_NS}"><w:pgSz w:w="11906" w:h="16838"/>'
                                                                       '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
        for t in sp.findall(qn("w:type")):
            sp.remove(t)
        brk = a.get("brk") if a.get("brk") in BREAKS else "nextPage"
        sz = sp.find(qn("w:pgSz"))
        t = OxmlElement("w:type")
        t.set(qn("w:val"), brk)
        (sz.addprevious(t) if sz is not None else sp.insert(0, t))
        if sz is not None and a.get("orient") in ("portrait", "landscape"):
            w, h = int(sz.get(qn("w:w"), 11906)), int(sz.get(qn("w:h"), 16838))
            if (a["orient"] == "landscape") != (w > h):
                w, h = h, w
            sz.set(qn("w:w"), str(w))
            sz.set(qn("w:h"), str(h))
            if a["orient"] == "landscape":
                sz.set(qn("w:orient"), "landscape")
            elif qn("w:orient") in sz.attrib:
                del sz.attrib[qn("w:orient")]
        mar = sp.find(qn("w:pgMar"))
        if mar is not None:
            for k in ("top", "right", "bottom", "left"):
                v = a.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10000:
                    mar.set(qn(f"w:{k}"), str(v))
        p = self.doc.add_paragraph()
        p._p.get_or_add_pPr().append(sp)
