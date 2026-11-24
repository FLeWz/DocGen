"""ProseMirror JSON -> OOXML, inserted into a base DOCX. Pure: no DB or git, content/assets come in
through two callbacks, so the same spec always yields the same bytes (timestamps and zip metadata fixed).

spec = {title, built_ins:{doc.title, doc.version, doc.date, org.name, author}, variables:{declarations,values},
        links, revision_history:[{version,date,author,message}], blocks:[{type, slot, ..., pin:{folder_id, snippet_id, sha}}]}
"""
import copy
import io
import json
import re
from datetime import datetime

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, RGBColor, Twips
from jinja2 import Undefined
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment
from PIL import Image as PILImage

from . import fmt
from .base_docs import CONTENT_RE, SLOT_RE
from .ooxml import add_field, opaque_element, stable_zip

MAX_W = 5_486_400  # 6 in; images wider than the text area are scaled down
SAFE_HREF = re.compile(r"(https?://|mailto:)", re.I)
SETTINGS_AFTER = {"hdrShapeDefaults", "footnotePr", "endnotePr", "compat", "docVars", "rsids", "mathPr",
                  "attachedSchema", "themeFontLang", "clrSchemeMapping", "shapeDefaults", "decimalSymbol",
                  "listSeparator", "doNotIncludeSubdocsInStats", "doNotAutoCompressPictures", "forceUpgrade"}


# ---- sandboxed jinja ---------------------------------------------------------------------------

_DATE = {"YYYY": "%Y", "MM": "%m", "DD": "%d", "HH": "%H", "mm": "%M", "ss": "%S"}


def make_env(missing: set) -> SandboxedEnvironment:
    class Missing(Undefined):  # renders as its own placeholder so nothing silently vanishes
        def __str__(s):
            missing.add(s._undefined_name)
            return "{{ %s }}" % s._undefined_name

        def __getattr__(s, k):
            if k.startswith("__"):
                raise AttributeError(k)
            return Missing(name=f"{s._undefined_name}.{k}")

    class Env(SandboxedEnvironment):
        intercepted_binops = frozenset({"*", "**", "<<"})  # 'a'*10**9 style memory bombs

        def call_binop(self, context, operator, left, right):
            raise SecurityError(f"operator {operator} not allowed")

    env = Env(undefined=Missing, autoescape=False)  # values land in XML text nodes via lxml, which escapes
    env.filters["upper"] = lambda v: v if isinstance(v, Undefined) else str(v).upper()

    def datefmt(v, fmt):
        if isinstance(v, Undefined):
            return v
        try:
            d = datetime.fromisoformat(str(v))
        except ValueError:
            return v
        return d.strftime(re.sub("|".join(_DATE), lambda m: _DATE[m[0]], fmt))
    env.filters["datefmt"] = datefmt
    return env


def chip(a: dict) -> str:
    """variable node -> jinja expression text; resolved by the render step."""
    e = a["name"]
    if a.get("default") not in (None, ""):
        e += f"|default({json.dumps(a['default'])}, true)"
    f = a.get("filter") or ""
    if f == "upper":
        e += "|upper"
    elif f.startswith("date:"):
        e += f"|datefmt({json.dumps(f[5:])})"
    return "{{ %s }}" % e


def _balanced(t: str) -> bool:
    return t.count("{{") == len(re.findall(r"\{\{.*?\}\}", t))


def render_texts(roots, ctx: dict, missing: set, warnings: list, done: set) -> None:
    """Renders every `{{ }}` text once; `done` holds nodes rendered earlier (repeated rows), never re-evaluated."""
    env = make_env(missing)
    for root in roots:
        for p in root.iter(qn("w:p")):
            ts = list(p.iter(qn("w:t")))
            full = "".join(t.text or "" for t in ts)
            if "{{" not in full:
                continue
            if any(t in done for t in ts):
                continue
            if not all(_balanced(t.text or "") for t in ts):  # placeholder split over runs: merge (formatting of first run wins)
                ts[0].text = full
                for t in ts[1:]:
                    t.text = ""
                ts = ts[:1]
            for t in ts:
                s = t.text or ""
                if t in done or "{{" not in s or "{%" in s or "{#" in s:
                    continue
                done.add(t)
                try:
                    t.text = env.from_string(s).render(ctx)
                    t.set(qn("xml:space"), "preserve")
                except Exception as e:  # syntax error / sandbox violation: keep the text as typed
                    warnings.append(f"template text left as is: {s[:40]!r} ({type(e).__name__})")


class Row(dict):
    """A list-variable row: a column the user left out renders empty instead of as a missing variable."""

    def __missing__(self, k):
        if k.startswith("__"):
            raise KeyError(k)
        return ""


FOR_RE = re.compile(r"\{%tr\s+for\s+([A-Za-z_]\w*)\s+in\s+([A-Za-z_]\w*(?:\.\w+)?)\s*%\}")


def expand_rows(root, ctx: dict, missing: set, warnings: list, done: set) -> None:
    """`{%tr for x in list %}` .. `{%tr endfor %}` table rows (base doc and dynamic snippet tables): the rows between
    the markers repeat once per item and are rendered right here with `x` bound; markers are removed."""
    for tbl in list(root.iter(qn("w:tbl"))):
        rows, i = tbl.findall(qn("w:tr")), 0
        txt = lambda tr: "".join(t.text or "" for t in tr.iter(qn("w:t")))
        while i < len(rows):
            m = FOR_RE.search(txt(rows[i]))
            if not m:
                i += 1
                continue
            j = next((k for k in range(i + 1, len(rows)) if "{%tr" in txt(rows[k]) and "endfor" in txt(rows[k])), None)
            if j is None:
                warnings.append(f"table loop '{m[2]}' has no endfor: rows dropped")
                j = len(rows) - 1
            var, name = m[1], m[2]
            items: object = ctx
            for part in name.split("."):
                items = items.get(part) if isinstance(items, dict) else None
            if not isinstance(items, list):
                if items is not None:
                    warnings.append(f"'{name}' is not a list: table rows dropped")
                items = []
            tpl = rows[i + 1:j]
            for item in items:
                for tr in tpl:
                    c = copy.deepcopy(tr)
                    rows[j].addprevious(c)
                    render_texts([c], {**ctx, var: Row(item) if isinstance(item, dict) else item}, missing, warnings, done)
            for tr in rows[i:j + 1]:
                tbl.remove(tr)
            i = j + 1


def _clean(row: dict, **extra) -> dict:  # None would render as the text "None"
    return Row({k: "" if v is None else v for k, v in {**row, **extra}.items()})


def context(spec: dict) -> dict:
    v = spec["variables"]
    ctx: dict = {}
    for k, val in {**{d["name"]: d.get("default") for d in v.get("declarations", []) if d.get("default") is not None},
                   **v.get("values", {})}.items():
        a, _, b = k.partition(".")
        if b:
            if not isinstance(ctx.get(a), dict):
                ctx[a] = {}
            ctx[a][b] = val
        else:
            ctx[a] = val
    bi = spec["built_ins"]
    ctx.update({"doc": {"title": bi["doc.title"], "version": bi["doc.version"], "date": bi["doc.date"]},
                "org": {"name": bi["org.name"]}, "author": bi["author"],
                "linked_documents": [_clean(l, id=l.get("doc_id"), link=l.get("url")) for l in spec.get("links", [])],
                "revision_history": [_clean(r) for r in spec.get("revision_history") or []]})
    return ctx


# ---- numbering ---------------------------------------------------------------------------------

class Numbering:
    """Reuses a base-doc abstractNum (bullet / decimal, not tied to a paragraph style) or adds one."""

    def __init__(self, doc):
        self.root = doc.part.numbering_part.element

    def _ids(self, tag, attr):
        return [int(e.get(qn(attr))) for e in self.root.findall(qn(tag))]

    def _abstract(self, fmt: str, multilevel: bool = False) -> int:
        if not multilevel:
            for a in self.root.findall(qn("w:abstractNum")):
                l0 = a.find(qn("w:lvl"))
                if l0 is not None and l0.find(qn("w:pStyle")) is None and \
                        (l0.find(qn("w:numFmt")) is not None and l0.find(qn("w:numFmt")).get(qn("w:val")) == fmt):
                    return int(a.get(qn("w:abstractNumId")))
        aid = max(self._ids("w:abstractNum", "w:abstractNumId"), default=-1) + 1
        lv = []
        for i in range(9):
            text = "•" if fmt == "bullet" else (".".join(f"%{k + 1}" for k in range(i + 1)) + ("." if i == 0 else "")
                                              if multilevel else f"%{i + 1}.")
            lv.append(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/>'
                      f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * (i + 1)}" w:hanging="360"/></w:pPr></w:lvl>')
        from docx.oxml import parse_xml
        a = parse_xml(f'<w:abstractNum xmlns:w="{W_NS}" w:abstractNumId="{aid}"><w:multiLevelType w:val="'
                      f'{"multilevel" if multilevel else "hybridMultilevel"}"/>{"".join(lv)}</w:abstractNum>')
        first_num = self.root.find(qn("w:num"))
        first_num.addprevious(a) if first_num is not None else self.root.append(a)  # abstractNums precede nums
        return aid

    def _num(self, aid: int, restart: bool) -> int:
        nid = max(self._ids("w:num", "w:numId"), default=0) + 1
        from docx.oxml import parse_xml
        ov = "".join(f'<w:lvlOverride w:ilvl="{i}"><w:startOverride w:val="1"/></w:lvlOverride>'
                     for i in range(9)) if restart else ""
        self.root.append(parse_xml(f'<w:num xmlns:w="{W_NS}" w:numId="{nid}"><w:abstractNumId w:val="{aid}"/>{ov}</w:num>'))
        return nid

    def bullets(self) -> int:
        if getattr(self, "_b", None) is None:
            self._b = self._num(self._abstract("bullet"), False)
        return self._b

    def ordered(self, numfmt: str = "decimal") -> int:  # fresh num per list so every ordered list restarts at 1
        return self._num(self._abstract(numfmt), True)

    def headings(self) -> int:
        if getattr(self, "_h", None) is None:
            self._h = self._num(self._abstract("decimal", multilevel=True), False)
        return self._h


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _numpr(p, num_id: int, level: int) -> None:
    ppr = p._p.get_or_add_pPr()
    n = OxmlElement("w:numPr")
    for tag, v in (("w:ilvl", level), ("w:numId", num_id)):
        e = OxmlElement(tag)
        e.set(qn("w:val"), str(v))
        n.append(e)
    fmt.insert_ordered(ppr, n, fmt.PPR_ORDER)


# ---- ProseMirror -> OOXML ----------------------------------------------------------------------

class Builder:
    def __init__(self, doc, load_asset, warnings: list, rels: set | None = None):
        """`rels` (ids of the document's relationships) switches on import mode: the package IS the original, so
        opaque blocks keep their r:id references and lists keep their original numIds."""
        self.doc, self.load_asset, self.warn, self.rels = doc, load_asset, warnings, rels
        self.styles = {s.style_id for s in doc.styles}
        self.num = Numbering(doc)

    def style(self, *wanted) -> str | None:
        return next((w for w in wanted if w and w in self.styles), None)

    def apply_style(self, p, *wanted) -> None:
        if s := self.style(*wanted):
            p._p.get_or_add_pPr().style = s  # id, not name: base-doc styles are matched by id

    def nodes(self, nodes, cont, ctx) -> None:
        for n in nodes or []:
            self.node(n, cont, ctx)

    def node(self, n, cont, ctx) -> None:
        t, a = n.get("type"), n.get("attrs") or {}
        if t == "paragraph":
            p = cont.add_paragraph()
            self.apply_style(p, a.get("styleId"))
            self.pfmt(p, a)
            self.inline(n.get("content"), p)
        elif t == "heading":
            lvl = min(max(int(a.get("level") or 1), 1), 4)
            p = cont.add_paragraph()
            self.apply_style(p, a.get("styleId"), f"Heading{lvl}")
            self.pfmt(p, a)
            self.inline(n.get("content"), p)
        elif t in ("bulletList", "orderedList"):
            self.list(n, cont, ctx, 0)
        elif t == "table":
            if cont is self.doc:
                self.table(n, ctx)
            else:
                self.warn.append("nested table skipped")
        elif t == "image":
            self.image(a, cont, ctx)
        elif t == "opaque":
            self.opaque(a, cont)
        elif t == "hardBreak":
            pass
        else:
            self.warn.append(f"unsupported node '{t}' skipped")

    def pfmt(self, p, a: dict) -> None:
        if a.get("pfmt"):
            fmt.write_para(p._p.get_or_add_pPr(), a["pfmt"])

    def opaque(self, a: dict, cont) -> None:
        """Raw OOXML block, re-validated here (it came from the client's JSON) and written back as is."""
        try:
            el = opaque_element(a.get("xml"), self.rels)
        except ValueError as e:
            self.warn.append(f"opaque block '{str(a.get('kind'))[:40]}' skipped: {e}")
            return
        if cont is self.doc:
            s = self.doc.element.body.find(qn("w:sectPr"))
            s.addprevious(el) if s is not None else self.doc.element.body.append(el)
        else:
            cont._tc.append(el)

    def list(self, n, cont, ctx, level) -> None:
        nid = (n.get("attrs") or {}).get("numId")
        if self.rels is not None and nid in set(self.num._ids("w:num", "w:numId")):
            num = nid  # imported list keeps its Word numbering (continues across lists)
        else:
            nf = {"lower-alpha": "lowerLetter", "upper-alpha": "upperLetter", "lower-roman": "lowerRoman",
                  "upper-roman": "upperRoman"}.get((n.get("attrs") or {}).get("listStyle"), "decimal")
            num = self.num.ordered(nf) if n["type"] == "orderedList" else self.num.bullets()
        for li in n.get("content") or []:
            first = True
            for child in li.get("content") or []:
                if child.get("type") in ("bulletList", "orderedList"):
                    self.list(child, cont, ctx, min(level + 1, 8))
                else:
                    p = cont.add_paragraph()
                    self.apply_style(p, (child.get("attrs") or {}).get("styleId"), "ListParagraph")
                    if first:
                        _numpr(p, num, level)
                        first = False
                    self.pfmt(p, child.get("attrs") or {})
                    self.inline(child.get("content"), p)

    def inline(self, content, p) -> None:
        for c in content or []:
            t = c.get("type")
            if t == "text":
                self.run(p, c.get("text", ""), c.get("marks") or [])
            elif t == "variable":
                self.run(p, chip(c.get("attrs") or {}), c.get("marks") or [])
            elif t == "hardBreak":
                from docx.enum.text import WD_BREAK
                p.add_run().add_break(WD_BREAK.PAGE if (c.get("attrs") or {}).get("page") else WD_BREAK.LINE)

    def run(self, p, text: str, marks: list) -> None:
        r = p.add_run(text)
        link = None
        for m in marks:
            k = m.get("type")
            if k == "bold":
                r.bold = True
            elif k == "italic":
                r.italic = True
            elif k == "underline":
                r.underline = True
            elif k == "fmt":
                fmt.write_run(r._r.get_or_add_rPr(), m.get("attrs") or {}, lambda s: s in self.styles)
            elif k == "link" and SAFE_HREF.match((m.get("attrs") or {}).get("href") or ""):
                link = m["attrs"]["href"]
        if link:
            h = OxmlElement("w:hyperlink")
            h.set(qn("r:id"), p.part.relate_to(link, RT.HYPERLINK, is_external=True))
            rpr = r._r.get_or_add_rPr()
            if rpr.find(qn("w:rStyle")) is not None:
                pass
            elif self.style("Hyperlink"):
                rs = OxmlElement("w:rStyle")
                rs.set(qn("w:val"), "Hyperlink")
                rpr.insert(0, rs)
            else:
                r.underline = True
                r.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
            h.append(r._r)
            p._p.append(h)

    def table(self, n, ctx) -> None:
        rows = [r for r in n.get("content") or [] if r.get("type") == "tableRow"]
        if not rows:
            return
        occ, placed, ncols = set(), [], 1
        for ri, row in enumerate(rows):
            ci = 0
            for cell in row.get("content") or []:
                while (ri, ci) in occ:
                    ci += 1
                a = cell.get("attrs") or {}
                cs, rs = max(int(a.get("colspan") or 1), 1), min(max(int(a.get("rowspan") or 1), 1), len(rows) - ri)
                occ |= {(ri + dr, ci + dc) for dr in range(rs) for dc in range(cs)}
                placed.append((ri, ci, cs, rs, cell))
                ci += cs
                ncols = max(ncols, ci)
        t = self.doc.add_table(len(rows), ncols)
        ta = n.get("attrs") or {}
        try:  # tables from an import keep their own properties (style, borders, width) and column grid, in snippets too
            pr = opaque_element(ta["tblPr"], self.rels, {"tblPr"}) if ta.get("tblPr") else None
        except ValueError:
            pr = None
        if pr is not None and (st := pr.find(qn("w:tblStyle"))) is not None and st.get(qn("w:val")) not in self.styles:
            pr.remove(st)  # style id of another document
        if pr is not None:
            t._tbl.remove(t._tbl.tblPr)
            t._tbl.insert(0, pr)
        elif s := self.style(ta.get("styleId"), "TableGrid"):
            t._tbl.tblPr.style = s
        grid = ta.get("grid")
        if isinstance(grid, list) and len(grid) == ncols and all(isinstance(g, int) and g > 0 for g in grid):
            for gc, g in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), grid):
                gc.set(qn("w:w"), str(g))
        else:
            grid = None
        if all(c.get("type") == "tableHeader" for c in rows[0].get("content") or [{}]):
            rpr = t.rows[0]._tr.get_or_add_trPr()
            rpr.append(OxmlElement("w:tblHeader"))
        for ri, row in enumerate(rows):
            if (row.get("attrs") or {}).get("rfmt"):
                fmt.write_row(t.rows[ri]._tr.get_or_add_trPr(), row["attrs"]["rfmt"])
        for ri, ci, cs, rs, cell in placed:
            a = t.cell(ri, ci)
            m = a.merge(t.cell(ri + rs - 1, ci + cs - 1)) if cs > 1 or rs > 1 else a
            if grid:
                m.width = Twips(sum(grid[ci:ci + cs]))
            for p in m._tc.findall(qn("w:p")):
                m._tc.remove(p)
            if cell.get("attrs", {}).get("cfmt"):
                fmt.write_cell(m._tc.get_or_add_tcPr(), cell["attrs"]["cfmt"])
            self.nodes(cell.get("content"), m, ctx)
            if m._tc.find(qn("w:p")) is None:
                m._tc.append(OxmlElement("w:p"))
        name = (n.get("attrs") or {}).get("dynamicList")
        if name and not re.fullmatch(r"[A-Za-z_]\w*", str(name)):
            self.warn.append(f"dynamic table: invalid list name {name!r}")
            name = None
        if name:  # the last row is the row template; expand_rows repeats it between these markers
            last = t.rows[-1]._tr
            for tag, txt in (("addprevious", f"{{%tr for row in {name} %}}"), ("addnext", "{%tr endfor %}")):
                getattr(last, tag)(_marker_row(txt))

    def image(self, a: dict, cont, ctx) -> None:
        data = self.load_asset(ctx, a.get("imageId"))
        p = cont.add_paragraph()
        if not data:
            self.warn.append(f"image {a.get('imageId')} not found")
            return
        with PILImage.open(io.BytesIO(data)) as im:
            if im.format == "WEBP":  # python-docx can't embed webp
                b = io.BytesIO()
                im.convert("RGBA").save(b, "PNG")
                data = b.getvalue()
        w, h = a.get("width"), a.get("height")
        ok = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        r = p.add_run()
        shape = r.add_picture(io.BytesIO(data), width=Emu(min(int(w) * 9525, MAX_W)) if ok(w) else None,
                              height=Emu(int(h) * 9525) if ok(w) and ok(h) else None)
        if a.get("align") in fmt.JC:
            fmt.write_para(p._p.get_or_add_pPr(), {"jc": a["align"]})
        if shape.width > MAX_W:
            shape.height, shape.width = int(shape.height * MAX_W / shape.width), Emu(MAX_W)
        dp = r._r.xpath(".//wp:docPr")[0]
        dp.set("descr", str(a.get("alt") or "")[:200])

    # ---- block-level items of a document ----
    def heading_block(self, b) -> None:
        p = self.doc.add_paragraph(b["text"])
        self.apply_style(p, b.get("styleId"), f"Heading{b['level']}")
        if b.get("numbered"):
            _numpr(p, self.num.headings(), b["level"] - 1)
        elif self.style(f"Heading{b['level']}"):
            _numpr(p, 0, 0)  # numId 0 switches off numbering the style may carry

    def toc_block(self) -> None:
        p = self.doc.add_paragraph()
        add_field(p, 'TOC \\o "1-4" \\h \\z \\u', "Right-click and choose Update Field to build the table of contents.")

    def page_break(self) -> None:
        from docx.enum.text import WD_BREAK
        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _marker_row(text: str):
    tr = OxmlElement("w:tr")
    tc = OxmlElement("w:tc")
    p = OxmlElement("w:p")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    for a, b in ((r, t), (p, r), (tc, p), (tr, tc)):
        a.append(b)
    return tr


def _fix_settings(doc) -> None:
    st = doc.settings.element
    if st.find(qn("w:updateFields")) is not None:
        return
    e = OxmlElement("w:updateFields")
    e.set(qn("w:val"), "true")
    nxt = next((c for c in st if c.tag.split("}")[1] in SETTINGS_AFTER), None)
    nxt.addprevious(e) if nxt is not None else st.append(e)


def build(spec: dict, base: bytes, load_content, load_asset) -> tuple[bytes, list]:
    """load_content(pin) -> (pm_doc, slug)|None; load_asset(pin, slug, image_id) -> bytes|None."""
    warnings: list = []
    doc = Document(io.BytesIO(base))
    body = doc.element.body
    cur: dict = {}
    b = Builder(doc, lambda c, iid: load_asset(c["pin"], c["slug"], iid), warnings)

    holders = {}
    for p in body.iter(qn("w:p")):
        t = "".join(x.text or "" for x in p.iter(qn("w:t"))).strip()
        if CONTENT_RE.fullmatch(t):
            holders.setdefault("CONTENT", []).append(p)
        elif m := SLOT_RE.fullmatch(t):
            holders.setdefault(m[1], []).append(p)
    if "CONTENT" not in holders:
        raise ValueError("base document has no {{ CONTENT }} placeholder")
    for slot in {x.get("slot", "CONTENT") for x in spec["blocks"]} - set(holders):
        warnings.append(f"slot '{slot}' not in base document: blocks skipped")

    for slot, ps in holders.items():
        before = list(body)
        n0 = len(before) - (body.find(qn("w:sectPr")) is not None)
        for blk in spec["blocks"]:
            if blk.get("slot", "CONTENT") != slot:
                continue
            if blk["type"] == "snippet_ref":
                got = load_content(blk["pin"])
                if not got:
                    raise ValueError(f"snippet {blk['pin']['snippet_id']} @ {blk['pin']['sha'][:7]} not found")
                content, slug = got
                cur.update(pin=blk["pin"], slug=slug)
                b.nodes((content or {}).get("content"), doc, dict(cur))
            elif blk["type"] == "heading":
                b.heading_block(blk)
            elif blk["type"] == "toc":
                b.toc_block()
            elif blk["type"] == "page_break":
                b.page_break()
        kids = list(body)
        new = kids[n0:len(kids) - (body.find(qn("w:sectPr")) is not None)]
        for el in new:
            ps[0].addprevious(el)
        for p in ps:
            p.getparent().remove(p)

    roots = [body] + [r.target_part.element for r in doc.part.rels.values() if r.reltype in (RT.HEADER, RT.FOOTER)]
    missing: set = set()
    ctx, done = context(spec), set()
    for r in roots:
        expand_rows(r, ctx, missing, warnings, done)
    render_texts(roots, ctx, missing, warnings, done)
    if missing:
        warnings.append("undefined variables: " + ", ".join(sorted(missing)))
    _fix_settings(doc)
    cp, bi = doc.core_properties, spec["built_ins"]
    cp.title, cp.author, cp.last_modified_by, cp.revision = bi["doc.title"], bi["author"], bi["author"], 1
    cp.created = cp.modified = datetime.fromisoformat(bi["doc.date"])
    out = io.BytesIO()
    doc.save(out)
    return stable_zip(out.getvalue()), warnings
