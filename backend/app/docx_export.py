"""ProseMirror JSON -> OOXML, inserted into a base DOCX. Pure: no DB or git, content/assets come in
through two callbacks, so the same spec always yields the same bytes (timestamps and zip metadata fixed).

spec = {title, built_ins:{doc.title, doc.version, doc.date, org.name, author}, variables:{declarations,values},
        links, revision_history:[{version,date,author,message}], blocks:[{type, slot, ..., pin:{folder_id, snippet_id, sha}}]}
"""
import base64
import copy
import io
import json
import re
from datetime import datetime

from .dates import FMT, dmy, parse_date

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, RGBColor, Twips
from jinja2 import Undefined
from lxml import etree
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment
from PIL import Image as PILImage

from . import fmt, omml
from .base_docs import CONTENT_RE, SLOT_RE
from .docx_vars import clean_rows, clip_tables, fix_fields, text_width
from .docx_rich import Rich, bm_name, seq_numbers
from docx.opc.part import Part

from .ooxml import PART_KINDS, R_NS, add_field, blob_problem, opaque_element, stable_zip

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
        d = parse_date(v)
        if d is None:
            return v
        fmt = (fmt or "").strip() or FMT  # no format: DD.MM.YYYY
        return datetime(d.year, d.month, d.day).strftime(re.sub("|".join(_DATE), lambda m: _DATE[m[0]], fmt))
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
                    if "\n" in t.text:  # a value with line breaks (default rows keep them): real <w:br/> between the parts
                        first, *rest = t.text.split("\n")
                        t.text, at = first, t
                        for part in rest:
                            br = OxmlElement("w:br")
                            nt = OxmlElement("w:t")
                            nt.text = part
                            nt.set(qn("xml:space"), "preserve")
                            at.addnext(br)
                            br.addnext(nt)
                            at = nt
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


# Column keys of revision-history tables found in the wild (hand-made base documents, the inserted table, the default base):
# every group is one value, so a template using any of the names is filled. Only the first non-empty member is the source.
REV_ALIASES = (("date", "last_edited_date", "edited_date"), ("version", "revision", "revision_number", "rev"),
               ("author", "edited_by"), ("sections", "pages_paragraphs_affected", "pages_affected"),
               ("commit", "message", "description", "change_ref", "change"))


def revision_rows(spec: dict) -> list:
    """Rows for `revision_history`: blank rows dropped, aliases filled, a single explicit row when there are none."""
    bi = spec["built_ins"]
    rows = [_clean(r, date=dmy(r.get("date"))) for r in spec.get("revision_history") or []]
    rows = [r for r in rows if any(str(v).strip() for v in r.values())]
    if not rows:
        rows = [_clean({}, version=bi["doc.version"], date=dmy(bi["doc.date"]), author=bi["author"], message="No revisions yet")]
    for r in rows:
        for g in REV_ALIASES:
            v = next((r[k] for k in g if r.get(k)), "")
            r.update({k: v for k in g if not r.get(k)})
        r.setdefault("status", "Committed")
    return rows


def context(spec: dict) -> dict:
    v = spec["variables"]
    ctx: dict = {}
    for k, val in {**{d["name"]: d.get("default") for d in [*spec.get("base_variables", []), *v.get("declarations", [])]
                      if d.get("default") is not None},
                   **v.get("values", {})}.items():
        a, _, b = k.partition(".")
        if b:
            if not isinstance(ctx.get(a), dict):
                ctx[a] = {}
            ctx[a][b] = val
        else:
            ctx[a] = val
    for d in [*spec.get("base_variables", []), *v.get("declarations", [])]:  # date variables always render DD.MM.YYYY
        if d.get("type") == "date" and "." not in d["name"] and d["name"] in ctx:
            ctx[d["name"]] = dmy(ctx[d["name"]])
    bi = spec["built_ins"]
    doc = {"title": bi["doc.title"], "version": bi["doc.version"], "date": dmy(bi["doc.date"])}
    for alias, val in (("document_title", doc["title"]), ("document_version", doc["version"]), ("document_date", doc["date"]),
                       ("document_author", bi["author"])):  # round 22: imported covers say {{ document_title }}; a declared variable wins
        ctx.setdefault(alias, val)
    ctx.update({"doc": doc,
                "org": {"name": bi["org.name"]}, "author": bi["author"],
                "linked_documents": [_clean(l, id=l.get("doc_id"), link=l.get("url")) for l in spec.get("links", [])],
                "revision_history": revision_rows(spec)})
    return ctx


# ---- numbering ---------------------------------------------------------------------------------

class Numbering:
    """Reuses a base-doc abstractNum (bullet / decimal, not tied to a paragraph style) or adds one."""

    def __init__(self, doc):
        self.root = doc.part.numbering_part.element
        self.last = {}  # (format, "last") -> numId of the last ordered list written, for "continue numbering"
        self.bul = {}

    def _ids(self, tag, attr):
        return [int(e.get(qn(attr))) for e in self.root.findall(qn(tag))]

    def _abstract(self, fmt: str, multilevel: bool = False, glyph: str = "•") -> int:
        if not multilevel and glyph == "•":
            for a in self.root.findall(qn("w:abstractNum")):
                l0 = a.find(qn("w:lvl"))
                if l0 is not None and l0.find(qn("w:pStyle")) is None and \
                        (l0.find(qn("w:numFmt")) is not None and l0.find(qn("w:numFmt")).get(qn("w:val")) == fmt):
                    return int(a.get(qn("w:abstractNumId")))
        aid = max(self._ids("w:abstractNum", "w:abstractNumId"), default=-1) + 1
        lv = []
        for i in range(9):
            text = glyph if fmt == "bullet" else (".".join(f"%{k + 1}" for k in range(i + 1)) + ("." if i == 0 else "")
                                              if multilevel else f"%{i + 1}.")
            lv.append(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/>'
                      f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * (i + 1)}" w:hanging="360"/></w:pPr></w:lvl>')
        from docx.oxml import parse_xml
        a = parse_xml(f'<w:abstractNum xmlns:w="{W_NS}" w:abstractNumId="{aid}"><w:multiLevelType w:val="'
                      f'{"multilevel" if multilevel else "hybridMultilevel"}"/>{"".join(lv)}</w:abstractNum>')
        first_num = self.root.find(qn("w:num"))
        first_num.addprevious(a) if first_num is not None else self.root.append(a)  # abstractNums precede nums
        return aid

    def _num(self, aid: int, restart: bool, start: int = 1) -> int:
        nid = max(self._ids("w:num", "w:numId"), default=0) + 1
        from docx.oxml import parse_xml
        ov = "".join(f'<w:lvlOverride w:ilvl="{i}"><w:startOverride w:val="{start if i == 0 else 1}"/></w:lvlOverride>'
                     for i in range(9)) if restart else ""
        self.root.append(parse_xml(f'<w:num xmlns:w="{W_NS}" w:numId="{nid}"><w:abstractNumId w:val="{aid}"/>{ov}</w:num>'))
        return nid

    def bullets(self, style: str | None = None) -> int:
        glyph = {"circle": "o", "square": "▪"}.get(style or "", "•")
        if glyph not in self.bul:
            self.bul[glyph] = self._num(self._abstract("bullet", glyph=glyph), False)
        return self.bul[glyph]

    def ordered(self, numfmt: str = "decimal", start: int = 1) -> int:  # fresh num per list so every ordered list restarts (at `start`)
        return self._num(self._abstract(numfmt), True, start)

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

class Builder(Rich):
    def __init__(self, doc, load_asset, warnings: list, rels: set | None = None):
        """`rels` (ids of the document's relationships) switches on import mode: the package IS the original, so
        opaque blocks keep their r:id references and lists keep their original numIds."""
        self.doc, self.load_asset, self.warn, self.rels = doc, load_asset, warnings, rels
        self.styles = {s.style_id for s in doc.styles}
        self.num = Numbering(doc)
        self.list_defaults: dict = {}  # dynamic tables' default rows (attrs.dynamicRows): used when no value is given
        self.top = [doc]  # containers whose tables may be built (the body, header/footer parts)
        self._init_rich(max([int(x.get(qn("w:id"))) for x in doc.element.body.iter(qn("w:bookmarkStart")) if (x.get(qn("w:id")) or "").isdigit()] + [9000]))
        self.note_base = {k: max([int(x.get(qn("w:id"))) for part in doc.part.package.iter_parts() if str(part.partname) == f"/word/{k}s.xml"
                                  for x in etree.fromstring(part.blob).iter(qn(f"w:{k}"))] + [0]) for k in ("footnote", "endnote")}

    def style(self, *wanted) -> str | None:
        return next((w for w in wanted if w and w in self.styles), None)

    def apply_style(self, p, *wanted) -> None:
        if s := self.style(*wanted):
            p._p.get_or_add_pPr().style = s  # id, not name: base-doc styles are matched by id

    def nodes(self, nodes, cont, ctx) -> None:
        top = cont is self.doc
        omml.prepare(omml.collect(nodes))  # one pandoc run for all equations that are not cached yet
        if top and not self.whole:  # `whole` = the builder pre-computed lists/SEQ numbers over the whole document
            self.top_blocks = list(nodes or [])
            self.seq = seq_numbers(self.top_blocks)
        for n in nodes or []:
            if top:
                self.sec_track(n)
            self.node(n, cont, ctx)
        if top:
            self.sec_close()

    def node(self, n, cont, ctx) -> None:
        t, a = n.get("type"), n.get("attrs") or {}
        if t == "paragraph":
            p = cont.add_paragraph()
            self.apply_style(p, a.get("styleId"))
            self.pfmt(p, a)
            self.inline(n.get("content"), p)
            self.bookmark(p, a)
        elif t == "heading":
            lvl = min(max(int(a.get("level") or 1), 1), 4)
            p = cont.add_paragraph()
            self.apply_style(p, a.get("styleId"), f"Heading{lvl}")
            self.pfmt(p, a)
            self.inline(n.get("content"), p)
            self.bookmark(p, a)
        elif t in ("bulletList", "orderedList"):
            self.list(n, cont, ctx, 0)
        elif t == "table":
            if any(cont is c for c in self.top):
                self.table(n, ctx, cont)
            else:
                self.warn.append("nested table skipped")
        elif t == "image":
            self.image(a, cont, ctx)
        elif t == "opaque":
            self.opaque(a, cont)
        elif t == "toc" and cont is self.doc:
            self.toc(a)
        elif t == "sectBreak" and cont is self.doc:
            self.sect_break(a)
        elif t == "hardBreak":
            pass
        else:
            self.warn.append(f"unsupported node '{t}' skipped")

    def bookmark(self, p, a: dict) -> None:
        if a.get("bm") and (name := bm_name(a["bm"])):
            self.wrap_bookmark(p, name)

    def pfmt(self, p, a: dict) -> None:
        if a.get("pfmt"):
            fmt.write_para(p._p.get_or_add_pPr(), a["pfmt"])

    def opaque(self, a: dict, cont) -> None:
        """Raw OOXML block, re-validated here (it came from the client's JSON) and written back as is."""
        try:
            el = self.with_parts(a) if a.get("parts") and self.rels is None else opaque_element(a.get("xml"), self.rels)
        except ValueError as e:
            self.warn.append(f"opaque block '{str(a.get('kind'))[:40]}' skipped: {e}")
            return
        if cont is self.doc:
            s = self.doc.element.body.find(qn("w:sectPr"))
            s.addprevious(el) if s is not None else self.doc.element.body.append(el)
        else:
            cont._tc.append(el)

    def with_parts(self, a: dict):
        """An opaque block that travels with its pictures/embedded objects (`attrs.parts`, from a snippet): every part
        is re-checked, added under a fresh part name + relationship id of THIS package and the r:* ids are remapped."""
        parts = a["parts"]
        if not isinstance(parts, list) or len(parts) > 16:
            raise ValueError("bad parts")
        el = opaque_element(a.get("xml"), {str(p.get("rid")) for p in parts if isinstance(p, dict)})
        new, pkg = {}, self.doc.part.package
        for p in parts:
            kind = p.get("kind") if isinstance(p, dict) else None
            if kind not in PART_KINDS or p.get("ext") not in PART_KINDS[kind][2] or not isinstance(p.get("b64"), str):
                raise ValueError("bad part")
            try:
                blob = base64.b64decode(p["b64"], validate=True)
            except ValueError:
                raise ValueError("bad part data")
            if kind == "oleObject" and (why := blob_problem(blob)):
                raise ValueError(why)
            suffix, stem, types = PART_KINDS[kind]
            part = Part(pkg.next_partname(f"/word/{stem}%d.{p['ext']}"), types[p["ext"]], blob, pkg)
            new[p["rid"]] = self.doc.part.relate_to(part, "http://schemas.openxmlformats.org/officeDocument/2006/relationships" + suffix)
        for e in el.iter():
            for k, v in list(e.attrib.items()):
                if k.startswith("{%s}" % R_NS) and v in new:
                    e.set(k, new[v])
        return el

    def list(self, n, cont, ctx, level, parent=None) -> None:
        a = n.get("attrs") or {}
        nid = a.get("numId")
        key = (n["type"], a.get("listStyle"))
        if self.rels is not None and nid in set(self.num._ids("w:num", "w:numId")):
            num = nid  # imported list keeps its Word numbering (continues across lists)
        elif parent is not None and parent[0] == key:
            num = parent[1]  # a nested list of the same kind is the next level of the parent's numbering (real multilevel list)
        else:
            nf = {"lower-alpha": "lowerLetter", "upper-alpha": "upperLetter", "lower-roman": "lowerRoman", "upper-roman": "upperRoman",
                  "decimal-leading-zero": "decimalZero"}.get(a.get("listStyle"), "decimal")
            if n["type"] != "orderedList":
                num = self.num.bullets(a.get("listStyle"))
            elif a.get("listStyle") == "multilevel":
                num = self.num._num(self.num._abstract("decimal", multilevel=True), True)
            elif a.get("continue") and (nf, "last") in self.num.last:
                num = self.num.last[(nf, "last")]  # continue the previous list of this format
            else:
                start = a.get("start")
                num = self.num.ordered(nf, start if isinstance(start, int) and not isinstance(start, bool) and 1 <= start <= 9999 else 1)
                self.num.last[(nf, "last")] = num
        cur = (key, num)
        for li in n.get("content") or []:
            first = True
            for child in li.get("content") or []:
                if child.get("type") in ("bulletList", "orderedList"):
                    self.list(child, cont, ctx, min(level + 1, 8), cur)
                else:
                    p = cont.add_paragraph()
                    # imported paragraphs keep their own style (none = Normal): ListParagraph's contextual spacing would squeeze them
                    self.apply_style(p, (child.get("attrs") or {}).get("styleId"), *(() if self.rels is not None else ("ListParagraph",)))
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
            elif t == "field":
                self.field(p, c.get("attrs") or {}, c.get("marks") or [])
            elif t == "footnote":
                self.note(p, c.get("attrs") or {}, c.get("marks") or [])
            elif t == "math":
                self.math(p, c.get("attrs") or {})

    def math(self, p, a: dict) -> None:
        """LaTeX -> a native Word equation (inline m:oMath, or m:oMathPara for display math); the source text when it cannot be typeset"""
        latex, display = str(a.get("latex") or ""), bool(a.get("display"))
        el = omml.get(latex, display)
        if el is None:
            self.warn.append(f"equation could not be converted: {latex[:40]!r}")
            self.run(p, f"${latex}$", [])
        else:
            p._p.append(el)

    def apply_marks(self, r, marks: list) -> str | None:
        """bold/italic/underline/fmt marks onto run `r`; returns the link target when there is one."""
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
        return link

    def run(self, p, text: str, marks: list) -> None:
        r = p.add_run(text)
        link = self.apply_marks(r, marks)
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

    def table(self, n, ctx, cont=None) -> None:
        cont = cont if cont is not None else self.doc
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
        t = self.doc.add_table(len(rows), ncols) if cont is self.doc else cont.add_table(len(rows), ncols, Twips(9000))  # header/footer part
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
        want = self.style(ta.get("styleId"))
        if pr is not None and want and (cur := t._tbl.tblPr.find(qn("w:tblStyle"))) is not None and cur.get(qn("w:val")) != want:
            cur.set(qn("w:val"), want)  # style picked in the table tools
        elif pr is not None and want and t._tbl.tblPr.find(qn("w:tblStyle")) is None:
            t._tbl.tblPr.style = want
        grid = ta.get("grid")
        if not (isinstance(grid, list) and len(grid) == ncols and all(isinstance(g, int) and not isinstance(g, bool) and 0 < g < 200000 for g in grid)):
            grid = self.grid_from_cells(placed, ncols)  # edited tables: cell widths (px) when the grid is stale
        if grid:
            for gc, g in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), grid):
                gc.set(qn("w:w"), str(g))
        fmt.write_tbl(t._tbl.tblPr, {"jc": (ta.get("tfmt") or {}).get("jc"), **(ta.get("tedit") or {})}, grid)  # tfmt.jc: the old ribbon's alignment
        for ri, row in enumerate(rows):  # leading all-header rows repeat on each page (Word needs them contiguous from the top)
            if not (row.get("content") and all(c.get("type") == "tableHeader" for c in row["content"])):
                break
            t.rows[ri]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for ri, row in enumerate(rows):
            if (row.get("attrs") or {}).get("rfmt"):
                fmt.write_row(t.rows[ri]._tr.get_or_add_trPr(), row["attrs"]["rfmt"])
        for ri, ci, cs, rs, cell in placed:
            a = t.cell(ri, ci)
            m = a.merge(t.cell(ri + rs - 1, ci + cs - 1)) if cs > 1 or rs > 1 else a
            cw = (cell.get("attrs") or {}).get("colwidth")
            if grid:
                m.width = Twips(sum(grid[ci:ci + cs]))
            elif isinstance(cw, list) and cw and all(isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w < 5000 for w in cw):
                m.width = Twips(int(sum(cw) * 15))  # px -> twips (column width set in the ribbon)
            for p in m._tc.findall(qn("w:p")):
                m._tc.remove(p)
            if cell.get("attrs", {}).get("cfmt"):
                fmt.write_cell(m._tc.get_or_add_tcPr(), cell["attrs"]["cfmt"])
            self.nodes(cell.get("content"), m, ctx)
            if m._tc.find(qn("w:p")) is None:
                m._tc.append(OxmlElement("w:p"))
        if grid:  # vMerge continuation cells too: every tc is as wide as the grid columns it covers
            for tr in t._tbl.findall(qn("w:tr")):
                ci = 0
                for tc in tr.findall(qn("w:tc")):
                    pr_ = tc.get_or_add_tcPr()
                    gs = pr_.find(qn("w:gridSpan"))
                    cs = int(gs.get(qn("w:val"))) if gs is not None else 1
                    pr_.width = Twips(sum(grid[ci:ci + cs]))
                    ci += cs
        name = (n.get("attrs") or {}).get("dynamicList")
        if name and not re.fullmatch(r"[A-Za-z_]\w*", str(name)):
            self.warn.append(f"dynamic table: invalid list name {name!r}")
            name = None
        if name and (rows_ := clean_rows((n.get("attrs") or {}).get("dynamicRows"))) is not None:
            self.list_defaults.setdefault(name, rows_)
        if name:  # the last row is the row template; expand_rows repeats it between these markers
            last = t.rows[-1]._tr
            for tag, txt in (("addprevious", f"{{%tr for row in {name} %}}"), ("addnext", "{%tr endfor %}")):
                getattr(last, tag)(_marker_row(txt))

    @staticmethod
    def grid_from_cells(placed, ncols):
        """Column widths (twips) from the cells' colwidth (px, 15 twips each); None unless every column is known."""
        w: list = [None] * ncols
        for _, ci, cs, _, cell in placed:
            cw = (cell.get("attrs") or {}).get("colwidth")
            if isinstance(cw, list) and len(cw) == cs and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 < x < 5000 for x in cw):
                for k, x in enumerate(cw):
                    w[ci + k] = int(round(x * 15))
        return w if all(w) else None

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
    def style_numbered(self, sid: str | None) -> bool:
        """The style (or one it is based on) carries a numbering of its own, e.g. Word's multilevel list linked to Heading 1-9."""
        by_id = {s.get(qn("w:styleId")): s for s in self.doc.styles.element.findall(qn("w:style"))}
        for _ in range(10):
            st = by_id.get(sid)
            if st is None:
                return False
            n = st.find(qn("w:pPr") + "/" + qn("w:numPr") + "/" + qn("w:numId"))
            if n is not None:
                return n.get(qn("w:val")) != "0"
            b = st.find(qn("w:basedOn"))
            sid = b.get(qn("w:val")) if b is not None else None
        return False

    def heading_block(self, b) -> None:
        p = self.doc.add_paragraph(b["text"])
        self.apply_style(p, b.get("styleId"), f"Heading{b['level']}")
        sid = self.style(b.get("styleId"), f"Heading{b['level']}")
        if b.get("numbered"):
            if not self.style_numbered(sid):  # the base's own heading numbering must not be overridden by ours
                _numpr(p, self.num.headings(), b["level"] - 1)
        elif sid:
            _numpr(p, 0, 0)  # numId 0 switches off numbering the style may carry

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


def _whole_blocks(spec: dict, load_content) -> list:
    """Top-level nodes of the whole built document in order (headings and paragraph blocks included): the source of
    list-of-contents/figures/tables entries and of SEQ numbers, which count over the document, not per snippet."""
    out: list = []
    per: dict = {}  # id(block) -> its own copy of the nodes (a snippet may be used twice: each use numbers its own fields)
    for blk in spec["blocks"]:
        if blk["type"] == "snippet_ref":
            got = load_content(blk["pin"])
            per[id(blk)] = copy.deepcopy(((got and got[0]) or {}).get("content") or [])
        elif blk["type"] == "paragraph":
            per[id(blk)] = copy.deepcopy((blk["content"] or {}).get("content") or [])
        elif blk["type"] == "heading":
            per[id(blk)] = [{"type": "heading", "attrs": {"level": blk["level"]}, "content": [{"type": "text", "text": blk["text"]}]}]
        out += per.get(id(blk), [])
    return out, per


def build(spec: dict, base: bytes, load_content, load_asset) -> tuple[bytes, list]:
    """load_content(pin) -> (pm_doc, slug)|None; load_asset(pin, slug, image_id) -> bytes|None."""
    warnings: list = []
    doc = Document(io.BytesIO(base))
    body = doc.element.body
    cur: dict = {}
    b = Builder(doc, lambda c, iid: load_asset(c["pin"], c["slug"], iid), warnings)

    b.whole = True
    b.top_blocks, per = _whole_blocks(spec, load_content)
    b.seq = seq_numbers(b.top_blocks)
    omml.prepare(omml.collect(b.top_blocks))
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
                b.nodes(per[id(blk)], doc, dict(cur))
            elif blk["type"] == "paragraph":
                b.nodes(per[id(blk)], doc, {})
            elif blk["type"] == "heading":
                b.heading_block(blk)
            elif blk["type"] == "toc":
                b.nodes([{"type": "toc", "attrs": {k: blk.get(k) for k in ("kind", "title", "levels", "label")}}], doc, {})
            elif blk["type"] == "page_break":
                b.page_break()
        b.sec_close()
        kids = list(body)
        new = kids[n0:len(kids) - (body.find(qn("w:sectPr")) is not None)]
        for el in new:
            ps[0].addprevious(el)
        for p in ps:
            p.getparent().remove(p)

    b.write_notes()
    roots = [body] + [r.target_part.element for r in doc.part.rels.values() if r.reltype in (RT.HEADER, RT.FOOTER)]
    missing: set = set()
    fix_fields(roots, {**spec["built_ins"], "doc.date": dmy(spec["built_ins"]["doc.date"])}, warnings)
    tw = text_width(body.iter(qn("w:sectPr")))
    for r in roots[1:]:
        clip_tables(r, tw, warnings)
    ctx, done = context(spec), set()
    for n_, rows_ in b.list_defaults.items():  # a dynamic table's own rows: the default when neither the document nor the base declares the list
        ctx.setdefault(n_, rows_)
    for r in roots:
        expand_rows(r, ctx, missing, warnings, done)
    render_texts(roots, ctx, missing, warnings, done)
    if missing:
        warnings.append("undefined variables: " + ", ".join(sorted(missing)))
    parts = [("document", body)] + [(r.target_part.partname.lstrip("/"), r.target_part.element) for r in doc.part.rels.values()
                                    if r.reltype in (RT.HEADER, RT.FOOTER)]
    for part, root in parts:  # whatever is still a placeholder in the final XML, and where
        for p in root.iter(qn("w:p")):
            t = "".join(x.text or "" for x in p.iter(qn("w:t")))
            if "{{" in t or "{%" in t:
                warnings.append(f"unresolved placeholder in {part}: {t.strip()[:60]!r}")
    _fix_settings(doc)
    cp, bi = doc.core_properties, spec["built_ins"]
    cp.title, cp.author, cp.last_modified_by, cp.revision = bi["doc.title"], bi["author"], bi["author"], 1
    cp.created = cp.modified = datetime.fromisoformat(parse_date(bi["doc.date"]).isoformat())
    out = io.BytesIO()
    doc.save(out)
    return stable_zip(out.getvalue()), warnings
