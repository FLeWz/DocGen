"""Effective look of a DOCX package as CSS, so the editor and the live preview can look like the Word page:
docDefaults + default paragraph style + basedOn chains + theme fonts/colours, page size/margins, header/footer parts.

CSS classes (all under `.docx`): `.st-<styleId>` paragraph/heading styles, `.cs-<styleId>` character styles,
`.ts-<styleId>` table styles (borders as custom properties, see editor.css). Every value that reaches the CSS text is
validated in fmt.py (hex, ints, font-name regex) and style ids must match STYLE_ID, so the string is safe to inject.
"""
import base64
import io
import posixpath
import zipfile

import defusedxml.ElementTree as DET
from lxml import etree

from . import fmt
from .base_docs import parse_styles

W = fmt.W
MAX_MEDIA = 400_000
MAX_MEDIA_TOTAL = 3_000_000
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
# Carlito/Liberation/Caladea are metric-compatible with the usual Word fonts and exist on most Linux/Windows setups
FALLBACK = {"calibri": "Carlito", "arial": "Liberation Sans, Arimo, Helvetica", "times new roman": "Liberation Serif, Tinos",
            "courier new": "Liberation Mono, Cousine", "cambria": "Caladea", "consolas": "monospace",
            "verdana": "DejaVu Sans", "georgia": "serif", "tahoma": "DejaVu Sans", "segoe ui": "Open Sans, sans-serif"}


def font_stack(name: str) -> str:
    low = name.lower()
    fb = FALLBACK.get(low) or ("serif" if "serif" in low or "times" in low or "garamond" in low else "sans-serif")
    alts = [f.strip() for f in fb.split(",")]
    return ", ".join([f"'{name}'"] + [f if f in ("serif", "sans-serif", "monospace") else f"'{f}'" for f in alts]
                     + ([] if alts[-1] in ("serif", "sans-serif", "monospace") else ["sans-serif"]))


def rule(r: dict, p: dict) -> str:
    """run dict + para dict -> CSS declarations."""
    out = []
    if r.get("font"):
        out.append(f"font-family:{font_stack(r['font'])}")
    if r.get("size"):
        out.append(f"font-size:{r['size'] / 2:g}pt")
    if r.get("color"):
        out.append(f"color:#{r['color']}")
    if "b" in r:
        out.append(f"font-weight:{'bold' if r['b'] else 'normal'}")
    if "i" in r:
        out.append(f"font-style:{'italic' if r['i'] else 'normal'}")
    deco = [d for d, k in (("underline", "u"), ("line-through", "strike")) if r.get(k)]
    if deco or "u" in r or "strike" in r:
        out.append(f"text-decoration:{' '.join(deco) or 'none'}")
    if "caps" in r:
        out.append(f"text-transform:{'uppercase' if r['caps'] else 'none'}")
    if "smallCaps" in r:
        out.append(f"font-variant:{'small-caps' if r['smallCaps'] else 'normal'}")
    if r.get("hl"):
        out.append(f"background:#{r['hl']}")
    if r.get("va"):
        out.append("vertical-align:" + ("super" if r["va"] == "superscript" else "sub") + ";font-size:smaller")
    if p.get("jc"):
        out.append("text-align:" + ("justify" if p["jc"] == "both" else p["jc"]))
    for k, css in (("left", "margin-left"), ("right", "margin-right"), ("before", "margin-top"), ("after", "margin-bottom")):
        if k in p:
            out.append(f"{css}:{p[k] / 20:g}pt")
    if "first" in p:
        out.append(f"text-indent:{p['first'] / 20:g}pt")
    if "line" in p:
        rule_ = p.get("lineRule", "auto")
        out.append(f"line-height:{p['line'] / 20:g}pt" if rule_ != "auto" else "line-height:normal" if p["line"] == 240 else f"line-height:{round(p['line'] / 240 * 1.15, 3):g}")  # single = the font's own line height
    if p.get("shd"):
        out.append(f"background:#{p['shd']}")
    out += border_css(p.get("bdr") or {}, "border")
    return ";".join(out)


def border_css(bd: dict, prop: str) -> list:
    out = []
    for side in fmt.SIDES:
        b = bd.get(side)
        if b:
            out.append(f"{prop}-{side}:none" if b.get("val") in ("none", "nil") or not b.get("sz") else
                       f"{prop}-{side}:{max(b['sz'] / 8, .5):g}pt {b.get('val', 'single') if b.get('val') in ('solid', 'double', 'dotted', 'dashed') else 'solid'} #{b.get('color') or '000000'}")
    return out


def _merge(base: dict, over: dict) -> dict:
    out = {**base, **over}
    for k in ("bdr", "mar"):  # per side: a derived style that sets only `bottom` keeps the base's other sides
        if k in base and k in over:
            out[k] = {**base[k], **over[k]}
    return out


class Look:
    def __init__(self, data: bytes):
        self.zf = zipfile.ZipFile(io.BytesIO(data))
        self.names = set(self.zf.namelist())
        self.theme = fmt.load_theme(self.zf, self.parse)
        self.styles = {}  # id -> dict(type, based, r, p, tbl, name, default)
        self.dr, self.dp = {}, {}
        if "word/styles.xml" in self.names:
            root = self.parse("word/styles.xml")
            self.root = root
            d = root.find(W + "docDefaults")
            if d is not None:
                self.dr = fmt.run_fmt(d.find(f"{W}rPrDefault/{W}rPr"), self.theme)[0]
                self.dp = fmt.para_fmt(d.find(f"{W}pPrDefault/{W}pPr"), self.theme)[0]
            for s in root.findall(W + "style"):
                sid = s.get(W + "styleId")
                if not sid or not fmt.STYLE_ID.fullmatch(sid):
                    continue
                tp = s.find(W + "tblPr")
                self.styles[sid] = dict(
                    type=s.get(W + "type"), based=fmt.val(s.find(W + "basedOn")), default=s.get(W + "default") in ("1", "true"),
                    r=fmt.run_fmt(s.find(W + "rPr"), self.theme)[0], p=fmt.para_fmt(s.find(W + "pPr"), self.theme)[0],
                    tbl=fmt.tbl_fmt(tp, self.theme), tcp=fmt.cell_fmt(s.find(W + "tcPr"), self.theme),
                    # conditional formats (firstRow, band1Horz...): the same property groups as the style itself
                    cond={c.get(W + "type"): dict(r=fmt.run_fmt(c.find(W + "rPr"), self.theme)[0], p=fmt.para_fmt(c.find(W + "pPr"), self.theme)[0],
                                                  tbl=fmt.tbl_fmt(c.find(W + "tblPr"), self.theme), tcp=fmt.cell_fmt(c.find(W + "tcPr"), self.theme))
                          for c in s.findall(W + "tblStylePr")})
                # a table style's paragraph/run props are on the style itself (pPr/rPr), cells on tcPr
        self.default_t = next((k for k, v in self.styles.items() if v["type"] == "table" and v["default"]), None)
        self.default_p = next((k for k, v in self.styles.items() if v["type"] == "paragraph" and v["default"]), "Normal")

    def parse(self, name):
        return etree.fromstring(self.zf.read(name), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))

    def chain(self, sid):
        seen, out = set(), []
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            out.append(self.styles[sid])
            sid = self.styles[sid]["based"]
        return out[::-1]  # root first

    def effective(self, sid):
        """(run, para) with docDefaults beneath the basedOn chain (character styles: the chain only, so they override
        the paragraph's font instead of resetting it)."""
        char = self.styles.get(sid, {}).get("type") == "character"
        r, p = ({}, {}) if char else (dict(self.dr), dict(self.dp))
        for s in self.chain(sid):
            r, p = _merge(r, s["r"]), _merge(p, s["p"])
        return r, p

    def css(self) -> str:
        out = []
        r, p = self.effective(self.default_p)
        out.append(f":where(.docx){{{rule(r, {})}}}")
        # `:where` keeps these at zero specificity so every style class and inline direct format wins
        out.append(f":where(.docx p,.docx li,.docx td,.docx th){{{rule({}, p)}}}")
        for sid, s in self.styles.items():
            if s["type"] == "paragraph":
                r, p = self.effective(sid)
                out.append(f".docx .st-{sid}{{{rule(r, p)}}}")
            elif s["type"] == "character":
                out.append(f".docx .cs-{sid}{{{rule(self.effective(sid)[0], {})}}}")
            elif s["type"] == "table":
                tb, rr, pp = {}, {}, {}
                for x in self.chain(sid):
                    tb, rr, pp = _merge(tb, x["tbl"]), _merge(rr, x["r"]), _merge(pp, x["p"])
                v = "".join(f"--b-{k}:{border_css({'top': b}, 'x')[0].split(':', 1)[1]};" for k, b in (tb.get("bdr") or {}).items())
                out.append(f".docx .ts-{sid}{{{v}}}")
                if rr or pp:
                    out.append(f".docx .ts-{sid} p.st-{self.default_p}{{{rule(rr, pp)}}}")
        return "\n".join(o for o in out if not o.endswith("{}"))

    def _sect_pr(self, root) -> list:
        """Every section's sectPr in document order: those in a top-level paragraph's pPr (section breaks) + the body's own (last)."""
        out = []
        for sp in root.iter(W + "sectPr"):
            par = sp.getparent()
            if par.tag == W + "body" or (par.tag == W + "pPr" and par.getparent().getparent().tag == W + "body"):
                out.append(sp)
        return out

    @staticmethod
    def _page(sp) -> dict:
        sz, mar = (sp.find(W + "pgSz"), sp.find(W + "pgMar")) if sp is not None else (None, None)
        g = lambda e, k, d: (fmt._int(e.get(W + k), 0, 200000) if e is not None else None) or d
        tw = lambda v: round(v / 20, 1)
        return dict(w=tw(g(sz, "w", 11906)), h=tw(g(sz, "h", 16838)), top=tw(g(mar, "top", 1440)), right=tw(g(mar, "right", 1440)),
                    bottom=tw(g(mar, "bottom", 1440)), left=tw(g(mar, "left", 1440)), header=tw(g(mar, "header", 720)),
                    footer=tw(g(mar, "footer", 720)), first_page_differs=sp is not None and sp.find(W + "titlePg") is not None)

    def page(self) -> dict:
        """pgSz/pgMar of the final (body-level) section."""
        sps = self._sect_pr(self.parse("word/document.xml"))
        return self._page(sps[-1] if sps else None)

    def sections(self) -> dict:
        """Per section: page setup, break type, titlePg, header/footer part indexes per type (inherited from the previous
        section when absent, as in Word), page number format/start; `parts` = the distinct header/footer parts (xml + media);
        `even_odd` = settings evenAndOddHeaders; `media` = images of the body."""
        root, budget = self.parse("word/document.xml"), [0]
        rels, parts, seen, secs = self._rels("word/document.xml"), [], {}, []
        prev = {"header": {}, "footer": {}}

        def part(rid):
            rel = rels.get(rid)
            if rel is None:
                return None
            path = posixpath.normpath(posixpath.join("word", rel.get("Target")))
            if path not in self.names or path.startswith(".."):
                return None
            if path not in seen:
                seen[path] = len(parts)
                parts.append(dict(xml=self.zf.read(path).decode("utf-8", "replace"), media=self.media(path, budget)))
            return seen[path]
        for sp in self._sect_pr(root):
            refs = {}
            for kind in ("header", "footer"):
                refs[kind] = dict(prev[kind])
                for e in sp.findall(W + kind + "Reference"):
                    t = e.get(W + "type")
                    if t in ("default", "first", "even"):
                        i = part(e.get("{%s}id" % fmt_r()))
                        if i is not None:
                            refs[kind][t] = i
            prev = refs
            n = sp.find(W + "pgNumType")
            fm = fmt.val(n, "fmt") if n is not None else None
            secs.append(dict(page=self._page(sp), brk=fmt.val(sp.find(W + "type")) or "nextPage", title_pg=sp.find(W + "titlePg") is not None,
                             header=refs["header"], footer=refs["footer"],
                             num=dict(fmt=fm if fm in ("decimal", "upperRoman", "lowerRoman", "upperLetter", "lowerLetter") else "decimal",
                                      start=fmt._int(n.get(W + "start"), 0, 100000) if n is not None else None)))
        st = self.parse("word/settings.xml") if "word/settings.xml" in self.names else None
        return dict(sections=secs, parts=parts, even_odd=st is not None and fmt._on(st.find(W + "evenAndOddHeaders")),
                    media=self.media("word/document.xml", budget))

    def _rels(self, part: str) -> dict:
        rp = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
        if rp not in self.names:
            return {}
        return {r.get("Id"): r for r in DET.fromstring(self.zf.read(rp), forbid_dtd=True)}

    def media(self, part: str, budget: list) -> dict:
        """rId -> data: URI of the raster images a part references (size-capped)."""
        out = {}
        for rid, r in self._rels(part).items():
            if r.get("TargetMode") == "External" or not (r.get("Type") or "").endswith("/image"):
                continue
            path = posixpath.normpath(posixpath.join(posixpath.dirname(part), r.get("Target")))
            ext = posixpath.splitext(path)[1].lower()
            if ext not in MIME or path not in self.names or path.startswith(".."):
                continue
            info = self.zf.getinfo(path)
            if info.file_size > MAX_MEDIA or budget[0] + info.file_size > MAX_MEDIA_TOTAL:
                continue
            budget[0] += info.file_size
            out[rid] = f"data:{MIME[ext]};base64," + base64.b64encode(self.zf.read(path)).decode()
        return out

    def parts(self) -> dict:
        """Compat: header/footer (default type of the final section) + body media, plus the per-section data of sections()."""
        sec = self.sections()
        last = sec["sections"][-1] if sec["sections"] else dict(header={}, footer={})
        get = lambda kind: sec["parts"][last[kind]["default"]] if "default" in last[kind] else None
        return dict(header=get("header"), footer=get("footer"), **sec)


def fmt_r() -> str:
    return "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def look(data: bytes) -> dict:
    lk = Look(data)
    tabs = {sid: t for sid in lk.styles if lk.styles[sid]["type"] == "paragraph" and (t := lk.effective(sid)[1].get("tabs"))}
    flags = {}  # paragraph styles that keep with the next paragraph / start a page: the pagination reads them
    for sid, v in lk.styles.items():
        if v["type"] == "paragraph" and (f := {k: True for k in ("keepNext", "keepLines", "pageBreakBefore") if lk.effective(sid)[1].get(k)}):
            flags[sid] = f
    return dict(css=lk.css(), page=lk.page(), tabs=tabs, flags=flags, **lk.parts(),
                styles=[dict(s, id=s["style_id"], builtin=False) for s in parse_styles(lk.root)] if hasattr(lk, "root") else [])


def org_look(styles, skip=()) -> dict:
    """The org style table (no base document) in the same shape; page = A4 with 2.5 cm margins like the bundled base."""
    css = [":where(.docx){font-family:'Calibri', 'Carlito', sans-serif;font-size:11pt}"] if not skip else []
    for s in styles:
        if s.kind not in ("paragraph", "heading") or not fmt.STYLE_ID.fullmatch(s.style_id) or s.style_id in skip:
            continue
        r = {k: v for k, v in dict(
            font=s.font_family if s.font_family and fmt.FONT.fullmatch(s.font_family) else None,
            size=int(s.font_size * 2) if s.font_size else None, color=s.color[1:].upper() if s.color else None,
            b=True if s.bold else None, i=True if s.italic else None).items() if v is not None}
        css.append(f".docx .st-{s.style_id}{{{rule(r, dict(before=round(s.space_before * 20), after=round(s.space_after * 20)))}}}")
    return dict(css="\n".join(css), page=dict(w=595.3, h=841.9, top=70.9, right=70.9, bottom=70.9, left=70.9, header=35, footer=35),
                header=None, footer=None, media={}, styles=None, sections=[], parts=[], even_odd=False, tabs={}, flags={})
