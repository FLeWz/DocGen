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
        out.append(f"line-height:{p['line'] / 20:g}pt" if rule_ != "auto" else f"line-height:{round(p['line'] / 240 * 1.2, 3):g}")
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
    if "bdr" in base and "bdr" in over:
        out["bdr"] = {**base["bdr"], **over["bdr"]}
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
                    tbl=fmt.tbl_fmt(tp, self.theme), tcp=fmt.cell_fmt(s.find(W + "tcPr"), self.theme))
                # a table style's paragraph/run props are on the style itself (pPr/rPr), cells on tcPr
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

    def page(self) -> dict:
        root = self.parse("word/document.xml")
        sp = None
        for sp in root.iter(W + "sectPr"):
            pass  # the last one is the final section = body-level
        if sp is None:
            return dict(w=595.3, h=841.9, top=72, right=72, bottom=72, left=72, header=36, footer=36)
        sz, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
        g = lambda e, k, d: (fmt._int(e.get(W + k), 0, 200000) if e is not None else None) or d
        tw = lambda v: round(v / 20, 1)
        return dict(w=tw(g(sz, "w", 11906)), h=tw(g(sz, "h", 16838)), top=tw(g(mar, "top", 1440)), right=tw(g(mar, "right", 1440)),
                    bottom=tw(g(mar, "bottom", 1440)), left=tw(g(mar, "left", 1440)), header=tw(g(mar, "header", 720)),
                    footer=tw(g(mar, "footer", 720)), first_page_differs=sp.find(W + "titlePg") is not None)

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
        """Header/footer (default type of the final section): raw XML + its image media; plus the body's media."""
        budget, out = [0], dict(header=None, footer=None)
        root = self.parse("word/document.xml")
        sp = None
        for sp in root.iter(W + "sectPr"):
            pass
        rels = self._rels("word/document.xml")
        for kind in ("header", "footer"):
            ref = next((e for e in (sp if sp is not None else []) if e.tag == W + kind + "Reference" and e.get(W + "type") == "default"), None)
            rel = rels.get(ref.get("{%s}id" % fmt_r()) if ref is not None else None)
            if rel is None:
                continue
            path = posixpath.normpath(posixpath.join("word", rel.get("Target")))
            if path in self.names and not path.startswith(".."):
                out[kind] = dict(xml=self.zf.read(path).decode("utf-8", "replace"), media=self.media(path, budget))
        out["media"] = self.media("word/document.xml", budget)
        return out


def fmt_r() -> str:
    return "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def look(data: bytes) -> dict:
    lk = Look(data)
    return dict(css=lk.css(), page=lk.page(), **lk.parts(),
                styles=[dict(s, id=s["style_id"], builtin=False) for s in parse_styles(lk.root)] if hasattr(lk, "root") else [])


def org_look(styles) -> dict:
    """The org style table (no base document) in the same shape; page = A4 with 2.5 cm margins like the bundled base."""
    css = [":where(.docx){font-family:'Calibri', 'Carlito', sans-serif;font-size:11pt}"]
    for s in styles:
        if s.kind not in ("paragraph", "heading") or not fmt.STYLE_ID.fullmatch(s.style_id):
            continue
        r = {k: v for k, v in dict(
            font=s.font_family if s.font_family and fmt.FONT.fullmatch(s.font_family) else None,
            size=int(s.font_size * 2) if s.font_size else None, color=s.color[1:].upper() if s.color else None,
            b=True if s.bold else None, i=True if s.italic else None).items() if v is not None}
        css.append(f".docx .st-{s.style_id}{{{rule(r, dict(before=round(s.space_before * 20), after=round(s.space_after * 20)))}}}")
    return dict(css="\n".join(css), page=dict(w=595.3, h=841.9, top=70.9, right=70.9, bottom=70.9, left=70.9, header=35, footer=35),
                header=None, footer=None, media={}, styles=None)
