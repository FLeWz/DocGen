"""DOCX -> TipTap JSON (import) and back (export into the ORIGINAL package, body replaced).

Whatever the editor cannot represent stays an `opaque` node {kind, xml}: the raw OOXML of the whole block, written back
verbatim on export (after re-validation, see ooxml.opaque_element). Granularity is the block: a paragraph with one
unsupported inline thing (text box, field, tracked change...) becomes one opaque block. Direct formatting is kept as
attrs (see fmt.py): a `fmt` mark on runs, `pfmt` on paragraphs/headings, `cfmt` on cells, `tfmt` on tables, size/align on
images; theme colours/fonts are resolved to literal values. The original package (styles, numbering, headers, theme,
media) is reused on export.
"""
import copy
import hashlib
import io
import json
import posixpath
import re
import zipfile
from collections import Counter

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from lxml import etree

from . import fmt
from .docx_style import Look
from .tblstyle import TableEff
from .base_docs import CONTENT_RE, BadDocx, W, parse_styles
from .docx_export import Builder, seq_numbers
from .docx_rich import FIELD_CODES, bm_name, sec_from_bookmark
from .ooxml import opaque_element, rel_ids, stable_zip

NS = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}
R = "{%s}" % NS["r"]
MAX_DOC_XML = 30 * 1024 * 1024
MAX_IMAGES = 300
IMG_FORMATS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
REVISION = {"ins", "del", "moveFrom", "moveTo", "pPrChange", "rPrChange", "sectPrChange", "tblPrChange", "trPrChange",
            "tcPrChange", "tblGridChange", "moveFromRangeStart", "moveToRangeStart", "cellIns", "cellDel", "cellMerge"}
ROW_REVISION = REVISION - {"pPrChange", "rPrChange", "sectPrChange"}
# first match wins: label of an unsupported element, from the tags found inside it
KINDS = [("txbxContent", "text box"), ("oMath", "equation"), ("oMathPara", "equation"), ("sdt", "content control"),
         ("commentRangeStart", "comment"), ("commentRangeEnd", "comment"), ("commentReference", "comment"),
         ("relIds", "SmartArt"), ("chart", "chart"), ("footnoteReference", "footnote"), ("endnoteReference", "footnote"),
         ("fldSimple", "field"), ("fldChar", "field"), ("instrText", "field"), ("pict", "legacy drawing"),
         ("object", "embedded object"), ("drawing", "drawing"), ("sectPr", "section break"), ("tbl", "table")]
RUN_IGNORED = {"rPr", "t", "tab", "br", "drawing", "lastRenderedPageBreak"}
LIST_STYLE = {"decimal": "decimal", "lowerLetter": "lower-alpha", "upperLetter": "upper-alpha", "lowerRoman": "lower-roman",
              "upperRoman": "upper-roman", "decimalZero": "decimal-leading-zero"}
STYLE_ID = re.compile(r"[A-Za-z0-9_-]{1,60}")
loc = lambda e: e.tag.rpartition("}")[2] if isinstance(e.tag, str) else ""
val = lambda e, k="val": e.get(W + k) if e is not None else None


def fingerprint(node: dict) -> str:
    """Hash of a block's TipTap JSON modulo what the editor may add or reorder (defaults, derived `eff`, mark order)."""
    def norm(v):
        if isinstance(v, dict):
            d = {k: norm(x) for k, x in v.items() if k not in ("eff", "orig", "sec")}
            if v.get("type") == "link" and isinstance(d.get("attrs"), dict):  # TipTap adds target/rel
                d["attrs"] = {"href": d["attrs"].get("href")}
            if v.get("type") == "orderedList" and isinstance(d.get("attrs"), dict) and d["attrs"].get("start") == 1:
                del d["attrs"]["start"]
            return {k: x for k, x in d.items() if x not in (None, False, {}, [])}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v
    n = norm(node)
    for x in walk(n):
        if isinstance(x.get("marks"), list):
            x["marks"] = sorted(x["marks"], key=lambda m: json.dumps(m, sort_keys=True))
    return hashlib.sha1(json.dumps(n, sort_keys=True).encode()).hexdigest()[:20]


def _kind(el) -> str:
    tags = {loc(x) for x in el.iter()}
    k = next((label for t, label in KINDS if t in tags), None) or (
        "tracked changes" if tags & REVISION else "page break" if "br" in tags else loc(el))
    if k == "content control" and b"Table of Contents" in etree.tostring(el):
        return "table of contents"
    return k


FOLD_CODES = FIELD_CODES - {"REF", "PAGEREF"}  # fields the editor can hold as `field` nodes
HF_VARS = {"TITLE": "doc.title", "AUTHOR": "author", "DATE": "doc.date", "SAVEDATE": "doc.date", "CREATEDATE": "doc.date"}  # header/footer only
FLD_RE = re.compile(r"\s*([A-Z]+)\s*(.*)")


def _instr(s: str):
    m = FLD_RE.match(s or "")
    return (m[1], m[2].strip()) if m else (None, "")


def fold_fields(p, hf: bool = False):
    """Copy of paragraph `p` with each supported field (fldSimple or begin/separate/end runs) replaced by one run holding a
    <docgenField code arg text/> marker; None when the paragraph has nothing to fold or holds a field the editor can't hold."""
    ok = FOLD_CODES | (set(HF_VARS) if hf else set())
    res = etree.Element(p.tag, nsmap=p.nsmap)
    res.attrib.update(p.attrib)
    depth, cur, found = 0, None, False
    for el in p:
        t = loc(el)
        fc = el.find(W + "fldChar") if t == "r" else None
        kind = fc.get(W + "fldCharType") if fc is not None else None
        if depth:
            if t != "r" or kind == "begin":
                return None  # nested field or foreign element inside one
            if kind == "separate":
                cur["sep"] = True
            elif kind == "end":
                code, arg = _instr(cur["instr"])
                if code not in ok:
                    return None
                res.append(_marker(code, arg, cur["text"], cur["rpr"]))
                depth, found = 0, True
            elif cur["sep"]:
                cur["text"] += "".join(x.text or "" for x in el.iter(W + "t"))
                cur["rpr"] = cur["rpr"] if cur["rpr"] is not None else el.find(W + "rPr")
            elif (it := el.find(W + "instrText")) is not None:
                cur["instr"] += it.text or ""
            continue
        if t == "fldSimple":
            code, arg = _instr(el.get(W + "instr"))
            if code not in ok:
                return None
            r = el.find(W + "r")
            res.append(_marker(code, arg, "".join(x.text or "" for x in el.iter(W + "t")), r.find(W + "rPr") if r is not None else None))
            found = True
        elif kind == "begin":
            depth, cur = 1, {"instr": "", "text": "", "rpr": None, "sep": False}
        else:
            res.append(copy.deepcopy(el))
    return res if found and not depth else None


def _marker(code, arg, text, rpr):
    r = etree.Element(W + "r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    m = etree.SubElement(r, "docgenField")
    m.set("code", code)
    m.set("arg", arg)
    m.set("text", text)
    return r


def _flag(e) -> bool:
    return e is not None and val(e) not in ("0", "false", "off")


class _Parser:
    def __init__(self, zf, store_image, data=None):
        self.zf, self.store_image = zf, store_image
        parse = lambda n: etree.fromstring(zf.read(n), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
        names = set(zf.namelist())
        self.rels = {r.get("Id"): r for r in parse("word/_rels/document.xml.rels")} if "word/_rels/document.xml.rels" in names else {}
        self.heading = {s["style_id"]: s["level"] for s in parse_styles(parse("word/styles.xml")) if s["kind"] == "heading"} \
            if "word/styles.xml" in names else {}
        self.numfmt = {}  # numId -> {ilvl: numFmt}
        if "word/numbering.xml" in names:
            n = parse("word/numbering.xml")
            abstract = {a.get(W + "abstractNumId"): {l.get(W + "ilvl"): (val(l.find(W + "numFmt")), val(l.find(W + "lvlText")))
                                                      for l in a.findall(W + "lvl")} for a in n.findall(W + "abstractNum")}
            self.numfmt = {x.get(W + "numId"): abstract.get(val(x.find(W + "abstractNumId")), {}) for x in n.findall(W + "num")}
        self.theme = fmt.load_theme(zf, parse)
        self.look = Look(data) if data else None  # table styles (conditional formats) are resolved per cell, see tblstyle.py
        self.rel_ids = set(self.rels)
        self.opaque, self.dropped, self.blocked, self.images = Counter(), Counter(), [], {}
        self.hf = False  # header/footer parts: TITLE/AUTHOR/DATE fields become variable chips
        self.root = parse("word/document.xml")

    # ---- opaque ----
    def keep(self, el, kind: str | None = None) -> dict:
        kind = (kind or _kind(el))[:60]
        xml = etree.tostring(el, encoding="unicode", with_tail=False)
        try:
            opaque_element(xml, self.rel_ids)
        except ValueError as e:  # would never be written back: say so instead of carrying a dead block
            self.blocked.append(f"{kind}: {e}")
            return {"type": "paragraph", "content": [{"type": "text", "text": f"[{kind} removed: {e}]"}]}
        self.opaque[kind] += 1
        return {"type": "opaque", "attrs": {"kind": kind, "xml": xml}}

    # ---- inline ----
    def image(self, drawing):
        gd = drawing.find(".//a:graphicData", NS)
        blip = drawing.find(".//a:blip", NS)
        if gd is None or not (gd.get("uri") or "").endswith("/picture") or blip is None or not blip.get(R + "embed"):
            return None
        rel = self.rels.get(blip.get(R + "embed"))
        if rel is None or rel.get("TargetMode") == "External":
            return None
        path = posixpath.normpath(posixpath.join("word", rel.get("Target")))
        if path.startswith("..") or not path.lower().endswith(IMG_FORMATS) or path not in self.zf.namelist():
            return None
        if path not in self.images:
            self.images[path] = self.store_image(self.zf.read(path)) if len(self.images) < MAX_IMAGES else None
        if not self.images[path]:
            return None
        ext, dp = drawing.find(".//wp:extent", NS), drawing.find(".//wp:docPr", NS)
        px = lambda k: int(ext.get(k)) // 9525 if ext is not None and (ext.get(k) or "").isdigit() else None
        return {"type": "image", "attrs": {"imageId": self.images[path], "alt": (dp.get("descr") or "")[:200] if dp is not None else "",
                                           "width": px("cx") or None, "height": px("cy") or None,
                                           "anchored": drawing.find("wp:anchor", NS) is not None or None}}

    def run(self, r, marks: list, out: list, images: list):
        """Appends inline nodes of run `r`; returns the offending element when the run can't be represented."""
        rpr = r.find(W + "rPr")
        mk = list(marks)
        if rpr is not None:
            if _flag(rpr.find(W + "b")):
                mk.insert(0, {"type": "bold"})
            if _flag(rpr.find(W + "i")):
                mk.append({"type": "italic"})
            u = rpr.find(W + "u")
            if u is not None and val(u) not in ("none", "0", None):
                mk.append({"type": "underline"})
            d, left = fmt.run_fmt(rpr, self.theme)
            f = {k: v for k, v in d.items() if k not in ("b", "i", "u") and v not in (False, None)}
            if f:
                mk.append({"type": "fmt", "attrs": f})
            if left:
                self.dropped["run properties (" + ", ".join(sorted(left)) + ")"] += 1
        for c in r:
            t = loc(c)
            if t == "t" or t == "tab":
                txt = (c.text or "") if t == "t" else "\t"
                if out and out[-1].get("marks", []) == mk and out[-1]["type"] == "text":
                    out[-1]["text"] += txt
                elif txt:
                    out.append({"type": "text", "text": txt, **({"marks": mk} if mk else {})})
            elif t == "docgenField":
                code, arg = c.get("code"), c.get("arg") or ""
                if code in HF_VARS:
                    out.append({"type": "variable", "attrs": {"name": HF_VARS[code], "default": "", "filter": "date:YYYY-MM-DD" if "date" in HF_VARS[code] else None}, **({"marks": mk} if mk else {})})
                else:
                    out.append({"type": "field", "attrs": {"code": code, "arg": arg, "text": c.get("text") or ""}, **({"marks": mk} if mk else {})})
            elif t == "br":
                if c.get(W + "type") == "page":
                    out.append({"type": "hardBreak", "attrs": {"page": True}})
                elif c.get(W + "type") not in (None, "textWrapping"):
                    return c
                else:
                    out.append({"type": "hardBreak"})
            elif t == "drawing":
                img = self.image(c)
                if not img:
                    return c
                images.append(img)
            elif t not in RUN_IGNORED:
                return c

    def para(self, p):
        """-> (nodes, list_info). nodes = [paragraph|heading|opaque, image...]; list_info = (numId, ilvl) for list items."""
        ppr = p.find(W + "pPr")
        if any(loc(x) in REVISION for x in p.iter()):
            return [self.keep(p, "tracked changes")], None
        if ppr is not None and ppr.find(W + "sectPr") is not None:
            return [self.keep(p, "section break")], None
        out, images = [], []
        orig_p = p
        folded = fold_fields(p, self.hf)
        p = p if folded is None else folded  # PAGE/SEQ/... become `field` nodes; `keep` below still stores the original XML
        for c in p:
            t = loc(c)
            if t == "pPr" or t == "proofErr" or t == "bookmarkEnd":
                continue
            if t == "bookmarkStart":
                self.dropped["bookmarks"] += 1
            elif t == "r":
                bad = self.run(c, [], out, images)
                if bad is not None:
                    return [self.keep(orig_p, _kind(bad))], None
            elif t == "hyperlink":
                rel = self.rels.get(c.get(R + "id"))
                href = rel.get("Target") if rel is not None and rel.get("TargetMode") == "External" else None
                if href is None:
                    return [self.keep(orig_p, "internal link")], None
                if not re.match(r"(https?://|mailto:)", href, re.I):
                    self.dropped["links to non-web targets"] += 1
                    href = None
                for r in c.findall(W + "r"):
                    bad = self.run(r, [{"type": "link", "attrs": {"href": href}}] if href else [], out, images)
                    if bad is not None:
                        return [self.keep(p, _kind(bad))], None
            else:
                return [self.keep(orig_p, _kind(c))], None
        pf, left = fmt.para_fmt(ppr, self.theme)
        if left:
            self.dropped["paragraph properties (" + ", ".join(sorted(left)) + ")"] += 1
        sid = val(ppr.find(W + "pStyle")) if ppr is not None and ppr.find(W + "pStyle") is not None else None
        sid = sid if sid and STYLE_ID.fullmatch(sid) else None
        node = {"type": "paragraph", "attrs": {"styleId": sid, **({"pfmt": pf} if pf else {})}}
        lvl = self.heading.get(sid)
        if lvl and lvl <= 4:
            node = {"type": "heading", "attrs": {"level": lvl, "styleId": sid, **({"pfmt": pf} if pf else {})}}
        if out:
            node["content"] = out
        li = None
        num = ppr.find(f"{W}numPr") if ppr is not None else None
        if num is not None and node["type"] == "paragraph" and val(num.find(W + "numId")) not in (None, "0"):
            li = (val(num.find(W + "numId")), int(val(num.find(W + "ilvl")) or 0))
        return [node, *images], li

    # ---- blocks ----
    def toc_node(self, els) -> dict | None:
        """A table of contents / figures / tables: an SDT docPartObj "Table of Contents" or a TOC field spanning paragraphs
        (`els`, body children) -> `toc` node holding the original XML (written back as is)."""
        if len(els) == 1 and loc(els[0]) == "sdt":
            instr = "".join(x.text or "" for x in els[0].iter(W + "instrText"))
            xml = etree.tostring(els[0], encoding="unicode", with_tail=False)
        else:
            instr = "".join(x.text or "" for e in els for x in e.iter(W + "instrText"))
            wrap = etree.Element(W + "sdtContent", nsmap=els[0].getroottree().getroot().nsmap)
            for e in els:
                wrap.append(copy.deepcopy(e))
            xml = etree.tostring(wrap, encoding="unicode")
        try:
            opaque_element(xml, self.rel_ids)
        except ValueError:
            return None
        m = re.search(r'\\c\s+"([^"]{1,60})"', instr)
        kind = "toc" if not m else "tot" if m[1].lower() == "table" else "tof"
        lv = re.search(r'\\o\s+"(\d)-(\d)"', instr)
        return {"type": "toc", "attrs": {"kind": kind, "title": "", "levels": f"{lv[1]}-{lv[2]}" if lv else "1-3", "label": m[1] if m else None, "xml": xml, "refresh": False}}

    def toc_end(self, children, i) -> int | None:
        """Index of the last body child of the TOC field that begins in paragraph i (None: not a TOC field start)."""
        depth, started = 0, False
        for j in range(i, min(len(children), i + 2000)):
            ch = children[j]
            if loc(ch) != "p":
                return None if not started else None
            for r in ch.iter(W + "fldChar"):
                k = r.get(W + "fldCharType")
                depth += 1 if k == "begin" else -1 if k == "end" else 0
            if j == i:
                first = "".join(x.text or "" for x in ch.iter(W + "instrText")).strip()
                if not first.startswith("TOC") or ch.find(f".//{W}fldChar") is None:
                    return None
                started = True
            if depth <= 0:
                return j
        return None

    # ---- blocks ----
    def blocks(self, parent, depth=0) -> list:
        out, stack = [], []  # stack: (ilvl, list node, numId) of the open lists
        spans: dict[int, list] = {}  # top level only: id(node) -> [node, first, last body child] it was parsed from
        children, skip, sec = list(parent), -1, None

        def span(node, i):
            if depth == 0 and node["type"] not in ("opaque", "toc"):
                spans.setdefault(id(node), [node, i, i])[2] = i
        for i, ch in enumerate(children):
            if i <= skip:
                continue
            t = loc(ch)
            n0 = len(out)
            if depth == 0 and t in ("bookmarkStart", "bookmarkEnd"):
                if t == "bookmarkStart" and (m := sec_from_bookmark(ch.get(W + "name"))):
                    sec = {**m, "bid": ch.get(W + "id")}
                    continue
                if t == "bookmarkEnd" and sec and ch.get(W + "id") == sec["bid"]:
                    sec = None
                    continue
            if depth == 0 and (t == "sdt" and ch.find(f"{W}sdtPr/{W}docPartObj/{W}docPartGallery") is not None
                               and val(ch.find(f"{W}sdtPr/{W}docPartObj/{W}docPartGallery")) == "Table of Contents" or t == "p" and self.toc_end(children, i) is not None):
                j = i if t == "sdt" else self.toc_end(children, i)
                node = self.toc_node(children[i:j + 1])
                if node is not None:
                    stack.clear()
                    out.append(node)
                    skip = j
                    self.tag_sec(out, n0, sec)
                    continue
            if t == "p":
                nodes, li = self.para(ch)
                if li and nodes[0]["type"] == "paragraph":
                    self.item(out, stack, li, nodes[0])
                    span(stack[0][1], i)
                    nodes = nodes[1:]
                else:
                    stack.clear()
                    if len(nodes) == 1:
                        span(nodes[0], i)
                if nodes:  # images after a list item end the list
                    stack.clear()
                    out.extend(nodes)
            elif t == "tbl":
                stack.clear()
                out.append(self.keep(ch, "nested table") if depth else self.table(ch))
                span(out[-1], i)
            elif t in ("sectPr", "tcPr"):
                continue
            else:
                stack.clear()
                out.append(self.keep(ch))
            self.tag_sec(out, n0, sec if depth == 0 else None)
        for node, a, b in spans.values():  # unchanged blocks are written back as the original XML (see export)
            node.setdefault("attrs", {})["orig"] = {"s": a, "e": b, "h": fingerprint(node)}
        return out

    @staticmethod
    def tag_sec(out, n0, sec):
        """Blocks produced since index n0 (or the list that grew) belong to the marked section `sec` (bookmark range)."""
        if sec:
            for n in out[n0:] or out[-1:]:
                n.setdefault("attrs", {})["sec"] = {k: v for k, v in sec.items() if k != "bid"}

    def item(self, out, stack, li, para):
        num, lvl = li
        nf, text = self.numfmt.get(num, {}).get(str(lvl), (None, None))
        style = "disc" if nf == "bullet" and text not in ("o", "▪", "§", "■") else "circle" if text == "o" else "square" if nf == "bullet" else LIST_STYLE.get(nf, "decimal")
        node = lambda: {"type": "bulletList" if nf == "bullet" else "orderedList", "attrs": {"numId": int(num), "listStyle": style}, "content": []}
        while stack and stack[-1][0] > lvl:
            stack.pop()
        if stack and stack[-1][0] == lvl and stack[-1][2] == num:
            lst = stack[-1][1]
        else:
            if stack and stack[-1][0] == lvl:
                stack.pop()
            lst = node()
            (stack[-1][1]["content"][-1]["content"] if stack else out).append(lst)
            stack.append((lvl, lst, num))
        lst["content"].append({"type": "listItem", "content": [para]})

    def table(self, tbl):
        trs = tbl.findall(W + "tr")
        if any(loc(x) in ROW_REVISION and not any(loc(a) == "p" for a in x.iterancestors())
               for x in tbl.iter()) or any(tr.find(f"{W}trPr/{W}gridBefore") is not None
                                            or tr.find(f"{W}trPr/{W}gridAfter") is not None for tr in trs):
            return self.keep(tbl, "table (tracked or irregular)")
        grid = [int(g.get(W + "w")) for g in tbl.findall(f"{W}tblGrid/{W}gridCol") if (g.get(W + "w") or "").isdigit()]
        rows, origin, placed = [], {}, []
        for ri, tr in enumerate(trs):
            hdr = tr.find(f"{W}trPr/{W}tblHeader") is not None
            cells, col, new_origin = [], 0, {}
            for tc in tr.findall(W + "tc"):
                pr = tc.find(W + "tcPr")
                span = max(int(val(pr.find(W + "gridSpan")) or 1), 1) if pr is not None and pr.find(W + "gridSpan") is not None else 1
                vm = pr.find(W + "vMerge") if pr is not None else None
                if vm is not None and val(vm) != "restart" and col in origin:
                    origin[col][0]["attrs"]["rowspan"] += 1
                    new_origin[col] = origin[col]
                    col += span
                    continue
                widths = grid[col:col + span]
                cf = fmt.cell_fmt(pr, self.theme)
                cell = {"type": "tableHeader" if hdr else "tableCell",
                        "attrs": {"colspan": span, "rowspan": 1,
                                  "colwidth": [round(w / 15) for w in widths] if len(widths) == span else None,
                                  **({"cfmt": cf} if cf else {})},
                        "content": self.blocks(tc, 1) or [{"type": "paragraph", "attrs": {"styleId": None}}]}
                placed.append((cell, ri, col, span, pr))
                if vm is not None:
                    new_origin[col] = (cell,)
                cells.append(cell)
                col += span
            origin = new_origin
            rf = fmt.row_fmt(tr.find(W + "trPr"))
            rows.append({"type": "tableRow", **({"attrs": {"rfmt": rf}} if rf else {}), "content": cells})
        sid = val(tbl.find(f"{W}tblPr/{W}tblStyle"))
        sid = sid if sid and STYLE_ID.fullmatch(sid) else None
        pr = tbl.find(W + "tblPr")
        tf = {}
        if self.look is not None:  # resolve the table style + conditional formats into explicit per-cell attrs
            te = TableEff(self.look, pr, sid)
            te.nrows, te.ncols = len(trs), max(len(grid), max((c + s for _, _, c, s, _ in placed), default=0))
            for cell, ri, col, span, cpr in placed:
                eff = te.cell(ri, col, cell["attrs"]["rowspan"], span, cpr)
                if eff:
                    cell["attrs"]["eff"] = eff
            tf = te.table_fmt()
        else:
            tf = fmt.tbl_fmt(pr, self.theme)
        if pr is not None:
            pr = copy.deepcopy(pr)
            etree.cleanup_namespaces(pr)  # tblPr has no prefixed attribute values, so unused declarations can go
        tf = {k: v for k, v in tf.items() if k != "mar"}
        return {"type": "table", "attrs": {"dynamicList": None, "styleId": sid,
                                           **({"tfmt": tf} if tf else {}),
                                           "grid": grid or None,
                                           "tblPr": etree.tostring(pr, encoding="unicode", with_tail=False) if pr is not None else None},
                "content": rows}


def parse(data: bytes, store_image) -> tuple[dict, list[str]]:
    """`data` must have passed base_docs.validate. store_image(bytes) -> image id | None. -> (doc, warnings)"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if zf.getinfo("word/document.xml").file_size > MAX_DOC_XML:
            raise BadDocx("word/document.xml too large")
        try:
            ps = _Parser(zf, store_image, data)
            content = ps.blocks(ps.root.find(W + "body"))
        except (etree.XMLSyntaxError, KeyError, ValueError, RecursionError) as e:
            raise BadDocx(f"cannot parse document: {type(e).__name__}")
    warn = [f"{n}× {k}: not editable, kept as an opaque block and written back unchanged" for k, n in ps.opaque.items()]
    warn += [f"blocked {b}" for b in ps.blocked]
    warn += [f"{n} {k}: not kept (editor has no support); styles are kept" for k, n in ps.dropped.items()]
    return {"type": "doc", "content": content or [{"type": "paragraph", "attrs": {"styleId": None}}]}, warn


# ---- export / base document ----

def _strip_body(orig: bytes, kept: list | None = None):
    """The package with an empty body; the removed original children go to `kept` (same indexes as the parser's spans)."""
    doc = Document(io.BytesIO(orig))
    body = doc.element.body
    for ch in list(body):
        if ch.tag != qn("w:sectPr"):  # the final section (page size, header/footer refs) stays
            body.remove(ch)
            if kept is not None:
                kept.append(ch)
    return doc


def _original(n: dict, kept: list):
    """Original body XML of an unedited top-level block (its fingerprint still equals the parsed one), else None."""
    o = (n.get("attrs") or {}).get("orig")
    if not isinstance(o, dict) or not all(isinstance(o.get(k), int) and not isinstance(o.get(k), bool) for k in "se") \
            or not 0 <= o["s"] <= o["e"] < len(kept) or o.get("h") != fingerprint(n):
        return None
    return [copy.deepcopy(c) for c in kept[o["s"]:o["e"] + 1]]


def export(orig: bytes, content: dict, load_asset, render: dict | None = None) -> tuple[bytes, list]:
    """Original package, body replaced by `content`. styles/numbering/headers/footers/theme/media stay as they were.
    `render` = jinja context: `{{ name }}` texts (made by the variables modal) are filled in, else they stay literal."""
    kept: list = []
    doc, warnings = _strip_body(orig, kept), []
    b, sect = Builder(doc, load_asset, warnings, rels=rel_ids(orig)), doc.element.body.find(qn("w:sectPr"))
    b.bm_id = max([b.bm_id] + [int(x.get(qn("w:id"))) for k in kept for x in k.iter(qn("w:bookmarkStart")) if (x.get(qn("w:id")) or "").isdigit()])
    blocks = (content or {}).get("content") or []
    b.top_blocks, b.seq = blocks, seq_numbers(blocks)
    for n in blocks:
        b.sec_track(n)
        if (same := _original(n, kept)) is None:
            b.node(n, doc, {})
        else:  # unedited: the original paragraphs/table byte for byte, so nothing the editor model can't hold is lost
            for el in same:
                sect.addprevious(el) if sect is not None else doc.element.body.append(el)
    b.sec_close()
    b.write_notes()
    if not doc.paragraphs and not doc.tables:
        doc.add_paragraph()
    if render is not None:
        from .docx_export import render_texts
        roots = [doc.element.body] + [r.target_part.element for r in doc.part.rels.values() if r.reltype in (RT.HEADER, RT.FOOTER)]
        missing: set = set()
        render_texts(roots, render, missing, warnings, set())
        if missing:
            warnings.append("undefined variables: " + ", ".join(sorted(missing)))
    out = io.BytesIO()
    doc.save(out)
    return stable_zip(out.getvalue()), warnings


def is_slot(b: dict) -> bool:
    return b.get("type") == "paragraph" and bool(CONTENT_RE.fullmatch(block_text(b).strip()))


def front_end(blocks: list) -> int:
    """Index of the first Heading 1 = where the main body starts (cover, approval, TOC come before it)."""
    return next((k for k, b in enumerate(blocks) if b.get("type") == "heading" and (b.get("attrs") or {}).get("level", 1) <= 1),
                len(blocks))


def slot_paragraph() -> dict:
    return {"type": "paragraph", "attrs": {"slot": "CONTENT"}, "content": [{"type": "text", "text": "{{ CONTENT }}"}]}


def as_base_doc(orig: bytes, content: dict, load_asset) -> bytes:
    """The import as it stands (cover, TOC, section breaks, header/footer, styles, numbering), with the main body
    replaced by a {{ CONTENT }} paragraph. Where the author put one (the quick action) that is kept as is; else the body
    from the first Heading 1 on is cut."""
    blocks = list((content or {}).get("content") or [])
    if not any(is_slot(b) for b in blocks):
        blocks = blocks[:front_end(blocks)] + [slot_paragraph()]
    return export(orig, {"type": "doc", "content": blocks}, load_asset)[0]


# ---- content helpers (server side twins of frontend/src/editor/doc.ts) ----

def walk(n):
    yield n
    for c in (n.get("content") or []) if isinstance(n, dict) else []:
        yield from walk(c)


def style_ids(content: dict) -> list[str]:
    out = set()

    def rec(n, in_list=False):
        t, a = n.get("type"), n.get("attrs") or {}
        if t == "heading":
            out.add(a.get("styleId") or f"Heading{a.get('level') or 1}")
        elif t == "paragraph":
            out.add(a.get("styleId") or ("ListParagraph" if in_list else "Normal"))
        for c in n.get("content") or []:
            rec(c, in_list or t in ("bulletList", "orderedList"))
    rec(content)
    return sorted(out)


def check_content(content: dict, rels: set) -> None:
    """Every opaque node and table property of client-supplied content must pass the same validation as on write-back."""
    for n in walk(content):
        a = n.get("attrs") or {}
        if n.get("type") == "opaque":
            opaque_element(a.get("xml"), rels)
        elif n.get("type") == "table" and a.get("tblPr"):
            opaque_element(a["tblPr"], rels, {"tblPr"})


def block_text(n) -> str:
    return "".join(x.get("text", "") for x in walk(n))
