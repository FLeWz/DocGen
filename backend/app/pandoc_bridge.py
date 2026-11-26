"""TipTap JSON <-> text (markdown now, LaTeX in round 21) through pandoc's JSON AST. See docs/DECISIONS.md, round 20.

The JSON stays the source of truth; the text is a view. Going back, blocks whose parsed AST is unchanged against the AST of
the text the user started from are restored verbatim from the pre-switch JSON (attrs.orig, grids, shading...); only changed
or new blocks come from the text. Pandoc is run only through `run()`: fixed argv, no shell, `--sandbox`, timeout, size limits.
Rule that keeps alignment simple: every top-level TipTap block is exactly one top-level AST block (and back).
"""
import difflib
import hashlib
import json
import re
import subprocess
import threading

API = [1, 23, 1, 2]  # pandoc-api-version of pandoc 3.14
MAX_TEXT = 2 * 1024 * 1024
MAX_OUT = 16 * 1024 * 1024
TIMEOUT_S = 20
_slots = threading.BoundedSemaphore(4)  # concurrent pandoc processes in this api worker

# markdown dialect: everything off that can reach outside the text (raw html/tex/attributes, citations, metadata blocks, includes)
FORMATS = {"markdown": "markdown-raw_html-raw_tex-raw_attribute-smart-auto_identifiers-implicit_figures-simple_tables-multiline_tables"
                       "-yaml_metadata_block-pandoc_title_block-citations-example_lists-markdown_in_html_blocks-native_divs-native_spans"
                       "+link_attributes+pipe_tables+grid_tables+fenced_divs+bracketed_spans+header_attributes+footnotes+inline_notes"
                       "+tex_math_dollars+strikeout+superscript+subscript+fancy_lists+startnum+escaped_line_breaks+lists_without_preceding_blankline"}


class BridgeError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


def run(args: list[str], data: bytes) -> bytes:
    if len(data) > MAX_TEXT * 4:
        raise BridgeError(413, "input too large")
    if not _slots.acquire(timeout=5):
        raise BridgeError(503, "conversion service busy, try again")
    try:
        p = subprocess.run(["pandoc", "--sandbox", *args], input=data, capture_output=True, timeout=TIMEOUT_S, env={"PATH": "/usr/local/bin:/usr/bin:/bin"})
    except subprocess.TimeoutExpired:
        raise BridgeError(422, f"conversion timed out after {TIMEOUT_S} s")
    finally:
        _slots.release()
    if p.returncode:
        raise BridgeError(422, "conversion failed: " + p.stderr.decode("utf8", "replace")[:300])
    if len(p.stdout) > MAX_OUT:  # shortcut: checked after the fact (the process is time-boxed), cap rlimits if this ever matters
        raise BridgeError(413, "output too large")
    return p.stdout


def _ast(blocks: list) -> bytes:
    return json.dumps({"pandoc-api-version": API, "meta": {}, "blocks": blocks}).encode()


def _fmt(fmt: str) -> str:
    if fmt not in FORMATS:
        raise BridgeError(422, f"unsupported format {fmt!r}")
    return FORMATS[fmt]


# ---- small AST constructors ---------------------------------------------------------------------------------------------
def E(t, c=None):
    return {"t": t} if c is None else {"t": t, "c": c}


def attr(kv=(), classes=(), ident=""):
    return [ident, list(classes), [[k, v] for k, v in kv]]


def words(s: str) -> list:
    out = []
    for m in re.finditer(r"(\s+)|(\S+)", s):
        out.append(E("Space") if m.group(1) else E("Str", m.group(2)))
    return out


def enc(v) -> str:
    return "true" if v is True else "false" if v is False else str(v)


def dec(s: str):
    if s in ("true", "false"):
        return s == "true"
    return int(s) if re.fullmatch(r"-?\d{1,9}", s) else s


def kv_of(d: dict | None, skip=()) -> list:
    """flat scalar entries as attribute pairs; nested values as JSON under `<key>_json`"""
    out = []
    for k, v in sorted((d or {}).items()):
        if v is None or v == "" or k in skip:
            continue
        out.append([f"{k}_json", json.dumps(v, sort_keys=True, separators=(",", ":"))] if isinstance(v, (dict, list)) else [k, enc(v)])
    return out


def dict_of(kvs, skip=()) -> dict:
    out = {}
    for k, v in kvs:
        if k in skip:
            continue
        if k.endswith("_json"):
            try:
                out[k[:-5]] = json.loads(v)
            except ValueError:
                pass
        else:
            out[k] = dec(v)
    return out


def oid(node: dict) -> str:
    return hashlib.sha1(json.dumps(node, sort_keys=True).encode()).hexdigest()[:8]


def esc_inline(s: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#+!|~^$])", r"\\\1", s)


# ---- TipTap -> AST --------------------------------------------------------------------------------------------------------
def var_text(a: dict, pipe: str = "|") -> str:
    parts = [str(a.get("name", ""))]
    if a.get("default"):
        parts.append('default("%s")' % str(a["default"]).replace('"', "'"))
    if a.get("filter"):
        parts.append(str(a["filter"]))
    return "{{ " + f" {pipe} ".join(parts) + " }}"


def w_text(n: dict, pipe: str) -> list:
    s = n.get("text") or ""
    core = s.strip()
    if not core:
        return words(s)
    lead, trail = s[:len(s) - len(s.lstrip())], s[len(s.rstrip()):]
    el = [E("Str", w) if i % 2 == 0 else E("Space") for i, w in enumerate(re.split(r"(\s+)", core))]
    for m in n.get("marks") or []:  # whitespace stays outside the emphasis, else markdown cannot express it
        t, a = m.get("type"), m.get("attrs") or {}
        if t == "bold":
            el = [E("Strong", el)]
        elif t == "italic":
            el = [E("Emph", el)]
        elif t == "underline":
            el = [E("Underline", el)]
        elif t == "link":
            el = [E("Link", [attr(), el, [str(a.get("href") or ""), ""]])]
        elif t == "fmt":
            if a.get("va") in ("superscript", "subscript"):
                el = [E("Superscript" if a["va"] == "superscript" else "Subscript", el)]
            if a.get("strike") is True:
                el = [E("Strikeout", el)]
            kv = [["custom-style" if k == "rStyle" else k, enc(v)] for k, v in sorted(a.items()) if v not in (None, "") and k not in ("va", "strike")]
            if kv:
                el = [E("Span", [attr(kv), el])]
    return words(lead) + el + words(trail)


def w_inlines(nodes: list, pipe: str = "|") -> list:
    out = []
    for n in nodes or []:
        t, a = n.get("type"), n.get("attrs") or {}
        if t == "text":
            out += w_text(n, pipe)
        elif t == "variable":
            out.append(E("RawInline", ["markdown", var_text(a, pipe)]))
        elif t == "hardBreak":
            out.append(E("Span", [attr(classes=["pagebreak"]), []]) if a.get("page") else E("LineBreak"))
        elif t == "math":
            out.append(E("Math", [E("DisplayMath" if a.get("display") else "InlineMath"), a.get("latex") or ""]))
        elif t == "field":
            out.append(E("Span", [attr([["field", str(a.get("code") or "PAGE")], ["arg", str(a.get("arg") or "")]]), words(str(a.get("text") or "?"))]))
        elif t == "footnote":
            out.append(E("RawInline", ["markdown", "^[" + esc_inline(str(a.get("text") or "")) + "]"]))
    return out


class Out:
    def __init__(self):
        self.opaque: dict[str, dict] = {}


def opaque_div(n: dict, o: Out, kind: str) -> dict:
    i = oid(n)
    o.opaque[i] = n
    return E("Div", [attr([["opaque", i]]), [E("Para", words(f"[opaque: {kind}]"))]])


def w_cell_blocks(blocks: list, o: Out) -> list | None:
    out = []
    for b in blocks:
        t = b.get("type")
        if t in ("paragraph", "heading"):
            out.append(E("Plain", w_inlines(b.get("content"), pipe="\\|")))
        elif t in ("bulletList", "orderedList"):
            r = w_list(b, o, "\\|", True)
            if r is None:
                return None
            out.append(r)
        elif t == "image":
            out.append(E("Plain", [w_image(b)]))
        else:
            return None  # nested table, opaque... -> the whole table is a placeholder
    if len(out) > 1:
        out = [E("Para", x["c"]) if x["t"] == "Plain" else x for x in out]
    return out


def w_table(n: dict, o: Out) -> dict | None:
    rows = n.get("content") or []
    if not rows:
        return None
    head, body = [], []
    for r in rows:
        cells = []
        for c in r.get("content") or []:
            a = c.get("attrs") or {}
            blocks = w_cell_blocks(c.get("content") or [], o)
            if blocks is None:
                return None
            cells.append([attr(), E("AlignDefault"), a.get("rowspan") or 1, a.get("colspan") or 1, blocks])
        is_head = not body and all(c.get("type") == "tableHeader" for c in r.get("content") or [])
        (head if is_head else body).append([attr(), cells])
    ncol = max((sum(c[3] for c in r[1]) for r in head + body), default=0)
    if not ncol:
        return None
    t = E("Table", [attr(), [None, []], [[E("AlignDefault"), E("ColWidthDefault")] for _ in range(ncol)],
                    [attr(), head], [[attr(), 0, [], body]], [attr(), []]])
    dl = (n.get("attrs") or {}).get("dynamicList")
    return E("Div", [attr([["dynamic", str(dl)]]), [t]]) if dl else t


def w_image(b: dict):
    a = b.get("attrs") or {}
    return E("Image", [attr(kv_of(a, skip=("imageId", "alt", "anchored"))), words(str(a.get("alt") or "")), [f"docgen-image:{a.get('imageId')}", ""]])


def w_list(n: dict, o: Out, pipe: str = "|", nested: bool = False) -> dict | None:
    items = []
    for li in n.get("content") or []:
        blocks = []
        for b in li.get("content") or []:
            t = b.get("type")
            if t in ("paragraph", "heading"):
                blocks.append(E("Plain", w_inlines(b.get("content"), pipe)))
            elif t in ("bulletList", "orderedList"):
                r = w_list(b, o, pipe, True)
                if r is None:
                    return None
                blocks.append(r)
            elif t == "image":
                blocks.append(E("Plain", [w_image(b)]))
            else:
                return None
        if sum(b["t"] == "Plain" for b in blocks) > 1:
            blocks = [E("Para", b["c"]) if b["t"] == "Plain" else b for b in blocks]
        items.append(blocks)
    a = n.get("attrs") or {}
    if n.get("type") == "bulletList":
        return E("BulletList", items)
    style = {"lower-alpha": "LowerAlpha", "upper-alpha": "UpperAlpha", "lower-roman": "LowerRoman", "upper-roman": "UpperRoman"}.get(a.get("listStyle"), "Decimal")
    # shortcut: pandoc only honours a start number on top-level lists, so nested ones are written from 1
    return E("OrderedList", [[1 if nested else (a.get("start") or 1), E(style), E("Period")], items])


def w_block(n: dict, o: Out) -> dict:
    """one TipTap top-level block -> exactly one AST block"""
    t, a = n.get("type"), n.get("attrs") or {}
    deco = [["custom-style", a["styleId"]]] if a.get("styleId") else []
    deco += kv_of(a.get("pfmt"))
    if t == "paragraph":
        inl = w_inlines(n.get("content"))
        if not inl:
            return E("Div", [attr(deco, ["empty"]), []])
        return E("Div", [attr(deco), [E("Para", inl)]]) if deco else E("Para", inl)
    if t == "heading":
        inl = w_inlines(n.get("content"))
        if not inl:
            return E("Div", [attr([*deco, ["heading", str(a.get("level") or 1)]], ["empty"]), []])
        return E("Header", [a.get("level") or 1, attr(deco), inl])
    if t in ("bulletList", "orderedList"):
        r = w_list(n, o)
        if r is not None:
            return r
    elif t == "table":
        r = w_table(n, o)
        if r is not None:
            return r
        return opaque_div(n, o, "table")
    elif t == "image":
        return E("Para", [w_image(n)])
    elif t == "toc":
        return E("Div", [attr([["toc", str(a.get("kind") or "toc")], *kv_of({k: a.get(k) for k in ("levels", "title", "label")})]), []])
    elif t == "sectBreak":
        return E("Div", [attr([["sectbreak", str(a.get("brk") or "nextPage")], *kv_of({k: a.get(k) for k in ("orient", "top", "right", "bottom", "left")})]), []])
    return opaque_div(n, o, str(a.get("kind") or t) if t == "opaque" else str(t))


SEP = E("Div", [attr(classes=["sep"]), []])  # keeps two adjacent lists from merging


def blocks_to_ast(blocks: list, o: Out) -> list:
    out = []
    for b in blocks:
        x = w_block(b, o)
        if out and x["t"] in ("BulletList", "OrderedList") and out[-1]["t"] == x["t"]:
            out.append(SEP)
        out.append(x)
    return out


def to_text(content: dict, fmt: str = "markdown") -> dict:
    o = Out()
    ast = blocks_to_ast((content or {}).get("content") or [], o)
    text = run(["-f", "json", "-t", _fmt(fmt), "--wrap=none"], _ast(ast)).decode("utf8")
    return {"text": text, "map": {"opaque": {k: (v.get("attrs") or {}).get("kind") or v.get("type") for k, v in o.opaque.items()}}}


# ---- AST -> TipTap ---------------------------------------------------------------------------------------------------------
VAR_RE = re.compile(r"\{\{\s*([A-Za-z_]\w*(?:\.\w+)?)((?:\s*\\?\|\s*[^|}]+?)*)\s*\}\}")
STYLE_KINDS = {"LowerAlpha": "lower-alpha", "UpperAlpha": "upper-alpha", "LowerRoman": "lower-roman", "UpperRoman": "upper-roman"}


class Rd:
    def __init__(self, store: dict):
        self.store, self.warn = store, []

    def w(self, msg: str):
        if msg not in self.warn:
            self.warn.append(msg)


def plain(inl: list) -> str:
    s = ""
    for x in inl or []:
        t, c = x["t"], x.get("c")
        if t == "Str":
            s += c
        elif t in ("Space", "SoftBreak", "LineBreak"):
            s += " "
        elif t in ("Emph", "Strong", "Underline", "Strikeout", "Superscript", "Subscript", "SmallCaps"):
            s += plain(c)
        elif t in ("Span", "Link"):
            s += plain(c[1])
        elif t == "Image":
            s += plain(c[1])
        elif t == "Code":
            s += c[1]
    return s


def r_inlines(inl: list, rd: Rd, marks: tuple = (), nodes: list | None = None) -> list:
    nodes = [] if nodes is None else nodes

    def text(s, mk=marks):
        if s:
            nodes.append({"type": "text", "text": s, **({"marks": list(mk)} if mk else {})})
    for x in inl or []:
        t, c = x["t"], x.get("c")
        if t == "Str":
            text(c)
        elif t in ("Space", "SoftBreak"):
            text(" ")
        elif t == "LineBreak":
            nodes.append({"type": "hardBreak", "attrs": {"page": False}})
        elif t == "Strong":
            r_inlines(c, rd, marks + ({"type": "bold"},), nodes)
        elif t == "Emph":
            r_inlines(c, rd, marks + ({"type": "italic"},), nodes)
        elif t == "Underline":
            r_inlines(c, rd, marks + ({"type": "underline"},), nodes)
        elif t == "Strikeout":
            r_inlines(c, rd, add_fmt(marks, strike=True), nodes)
        elif t in ("Superscript", "Subscript"):
            r_inlines(c, rd, add_fmt(marks, va=t.lower()), nodes)
        elif t == "SmallCaps":
            r_inlines(c, rd, add_fmt(marks, smallCaps=True), nodes)
        elif t == "Link":
            r_inlines(c[1], rd, marks + ({"type": "link", "attrs": {"href": c[2][0]}},), nodes)
        elif t == "Span":
            (_, cls, kvs), inner = c
            d = dict(kvs)
            if "pagebreak" in cls:
                nodes.append({"type": "hardBreak", "attrs": {"page": True}})
            elif "underline" in cls:
                r_inlines(inner, rd, marks + ({"type": "underline"},), nodes)
            elif "field" in d:
                nodes.append({"type": "field", "attrs": {"code": d["field"], "arg": d.get("arg", ""), "text": plain(inner)}})
            else:
                f = {("rStyle" if k == "custom-style" else k): v for k, v in dict_of(kvs).items()}
                r_inlines(inner, rd, add_fmt(marks, **f) if f else marks, nodes)
        elif t == "Math":
            nodes.append({"type": "math", "attrs": {"latex": c[1], "display": c[0]["t"] == "DisplayMath"}})
        elif t == "Note":
            nodes.append({"type": "footnote", "attrs": {"kind": "footnote", "text": " ".join(plain(b.get("c")) for b in c if isinstance(b.get("c"), list)), "n": 1}})
        elif t == "Code":
            text(c[1], add_fmt(marks, font="Courier New"))
        elif t == "Quoted":
            q = "\u201c\u201d" if c[0]["t"] == "DoubleQuote" else "\u2018\u2019"
            text(q[0]); r_inlines(c[1], rd, marks, nodes); text(q[1])
        elif t == "Image":
            rd.w("inline image inside text: put images in their own paragraph")
        else:
            rd.w(f"unsupported inline {t} dropped")
    return nodes


def add_fmt(marks: tuple, **k) -> tuple:
    """one fmt mark per run: strike/superscript/colour... from nested elements are merged"""
    for i, m in enumerate(marks):
        if m["type"] == "fmt":
            return marks[:i] + ({"type": "fmt", "attrs": {**m["attrs"], **{x: y for x, y in k.items() if y is not None}}},) + marks[i + 1:]
    return marks + ({"type": "fmt", "attrs": _fmt_attrs(**k)},)


def _fmt_attrs(**k) -> dict:
    base = {x: None for x in ("color", "font", "size", "hl", "strike", "va", "caps", "smallCaps", "rStyle")}
    base.update(k)
    return base


def split_vars(nodes: list) -> list:
    """`{{ name | filter }}` text -> variable chips (text nodes were merged first so the pattern is not split by marks)"""
    merged = []
    for n in nodes:
        if merged and n["type"] == "text" and merged[-1]["type"] == "text" and merged[-1].get("marks") == n.get("marks"):
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + n["text"]}
        else:
            merged.append(n)
    out = []
    for n in merged:
        if n["type"] != "text" or "{{" not in n["text"]:
            out.append(n)
            continue
        pos = 0
        for m in VAR_RE.finditer(n["text"]):
            if m.start() > pos:
                out.append({**n, "text": n["text"][pos:m.start()]})
            default, filt = "", None
            for f in re.split(r"\s*\\?\|\s*", m.group(2)):
                f = f.strip()
                if d := re.fullmatch(r"default\((?:\"(.*)\"|'(.*)')\)", f):
                    default = d.group(1) if d.group(1) is not None else d.group(2)
                elif f:
                    filt = f
            out.append({"type": "variable", "attrs": {"name": m.group(1), "default": default, "filter": filt}})
            pos = m.end()
        if pos < len(n["text"]):
            out.append({**n, "text": n["text"][pos:]})
    return out


def inl(i, rd):
    return split_vars(r_inlines(i, rd))


def para(i, rd, attrs: dict | None = None) -> list:
    """inlines -> [paragraph + image blocks]: images are block nodes, so a paragraph is cut around them"""
    attrs = attrs or {}
    out, cur = [], []
    for x in i:
        if x["t"] == "Image":
            if any(y["t"] != "Space" for y in cur):
                out.append(_p(cur, rd, attrs))
            cur = []
            a = dict_of(x["c"][0][2])
            out.append({"type": "image", "attrs": {"imageId": x["c"][2][0].removeprefix("docgen-image:"), "alt": plain(x["c"][1]),
                                                   "width": a.get("width"), "height": a.get("height"), "align": a.get("align"), "anchored": None}})
        else:
            cur.append(x)
    if cur or not out:
        out.append(_p(cur, rd, attrs))
    return out


def _p(i, rd, attrs):
    c = inl(i, rd)
    return {"type": "paragraph", "attrs": {"styleId": attrs.get("styleId"), "pfmt": attrs.get("pfmt")}, **({"content": c} if c else {})}


def deco(kvs) -> dict:
    d = dict_of(kvs, skip=("opaque", "toc", "sectbreak", "dynamic", "heading"))
    return {"styleId": d.pop("custom-style", None), "pfmt": d or None}


def r_item_blocks(blocks: list, rd: Rd) -> list:
    out = []
    for b in blocks:
        out += r_block(b, rd, nested=True)
    if not out or out[0]["type"] != "paragraph":
        out.insert(0, {"type": "paragraph", "attrs": {"styleId": None, "pfmt": None}})
    return out


def r_list(b, rd) -> dict:
    items = [{"type": "listItem", "content": r_item_blocks(it, rd)} for it in (b["c"] if b["t"] == "BulletList" else b["c"][1])]
    if b["t"] == "BulletList":
        return {"type": "bulletList", "content": items}
    (start, style, _), _ = b["c"]
    return {"type": "orderedList", "attrs": {"start": start, "listStyle": STYLE_KINDS.get(style["t"])}, "content": items}


def r_table(b, rd, dyn=None) -> dict:
    _, _, _, head, bodies, foot = b["c"]
    rows = []
    for is_head, rs in [(True, head[1])] + [(False, bd[2] + bd[3]) for bd in bodies] + [(False, foot[1])]:
        for r in rs:
            if is_head and all(not c[4] for c in r[1]):  # headerless tables are written with an empty header row
                continue
            cs = []
            for _, _, rowspan, colspan, blocks in r[1]:
                content = r_item_blocks(blocks, rd) if blocks else [{"type": "paragraph", "attrs": {"styleId": None, "pfmt": None}}]
                cs.append({"type": "tableHeader" if is_head else "tableCell", "attrs": {"colspan": colspan, "rowspan": rowspan, "colwidth": None}, "content": content})
            rows.append({"type": "tableRow", "content": cs})
    return {"type": "table", "attrs": {"dynamicList": dyn}, "content": rows}


def r_block(b: dict, rd: Rd, nested: bool = False, style: dict | None = None) -> list:
    t, c = b["t"], b.get("c")
    if t in ("Para", "Plain"):
        return para(c, rd, style if not nested else None)
    if t == "Header":
        d = deco(c[1][2])
        content = inl(c[2], rd)
        return [{"type": "heading", "attrs": {"level": min(c[0], 4), **d}, **({"content": content} if content else {})}] + ([] if c[0] <= 4 else [])
    if t in ("BulletList", "OrderedList"):
        return [r_list(b, rd)]
    if t == "Table":
        return [r_table(b, rd)]
    if t == "BlockQuote":
        return [x for y in c for x in r_block(y, rd, style={"styleId": "Quote", "pfmt": None})]
    if t == "CodeBlock":
        rd.w("code block converted to plain paragraphs")
        return [x for line in c[1].split("\n") for x in [{"type": "paragraph", "attrs": {"styleId": None, "pfmt": None}, "content": [{"type": "text", "text": line, "marks": [{"type": "fmt", "attrs": _fmt_attrs(font="Courier New")}]}]}] if line]
    if t == "HorizontalRule":
        rd.w("horizontal rule dropped (no Word equivalent here)")
        return []
    if t == "Div":
        (_, cls, kvs), kids = c
        d = dict(kvs)
        if "sep" in cls:
            return []
        if "opaque" in d:
            n = rd.store.get(d["opaque"])
            if n is None:
                rd.w(f"unknown opaque block {d['opaque']} dropped")
                return []
            return [json.loads(json.dumps(n))]
        if "toc" in d:
            return [{"type": "toc", "attrs": {"kind": d["toc"], "title": d.get("title", ""), "levels": d.get("levels", "1-3"), "label": d.get("label"), "xml": None, "refresh": False, "entries": None}}]
        if "sectbreak" in d:
            return [{"type": "sectBreak", "attrs": {"brk": d["sectbreak"], "orient": d.get("orient", "portrait"), **{k: dec(d[k]) for k in ("top", "right", "bottom", "left") if k in d}}}]
        if "empty" in cls:
            dd = deco(kvs)
            return [{"type": "heading", "attrs": {"level": int(d["heading"]), **dd}} if "heading" in d else {"type": "paragraph", "attrs": dd}]
        if "dynamic" in d:
            tb = next((k for k in kids if k["t"] == "Table"), None)
            return [r_table(tb, rd, d["dynamic"])] if tb else []
        dd = deco(kvs)
        return [x for k in kids for x in r_block(k, rd, nested=False, style=dd if dd["styleId"] or dd["pfmt"] else style)]
    rd.w(f"unsupported block {t} dropped")
    return []


# ---- text -> TipTap, with block alignment -------------------------------------------------------------------------------
def parse(text: str, fmt: str) -> list:
    if len(text.encode()) > MAX_TEXT:
        raise BridgeError(413, "text too large")
    return json.loads(run(["-f", _fmt(fmt), "-t", "json", "--wrap=none"], text.encode()))["blocks"]


def fp(b: dict) -> str:
    return hashlib.sha1(json.dumps(b, sort_keys=True).encode()).hexdigest()


def walk(n):
    yield n
    for c in n.get("content") or []:
        yield from walk(c)


def scan(text: str, store: dict) -> list[dict]:
    """problems pandoc does not report (it parses anything): unbalanced fences, unclosed `{{`/`$$`, unknown opaque ids"""
    errs, depth, opens, fence = [], 0, [], False
    dollars = 0
    for i, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
        if fence:
            continue
        if re.match(r":::+\s*\S", s):
            depth += 1; opens.append(i)
        elif re.fullmatch(r":::+", s):
            if depth == 0:
                errs.append({"line": i, "column": 1, "message": "closing ::: without an opening fence"})
            else:
                depth -= 1; opens.pop()
        if line.count("{{") != line.count("}}"):
            errs.append({"line": i, "column": (line.find("{{") if line.count("{{") > line.count("}}") else line.find("}}")) + 1, "message": "unbalanced {{ }} (variable)"})
        for m in re.finditer(r'opaque="([^"]*)"', line):
            if m.group(1) not in store:
                errs.append({"line": i, "column": m.start() + 1, "message": f"unknown opaque block {m.group(1)!r}: placeholders cannot be invented, copy an existing one"})
        dollars += line.count("$$")
    for i in opens:
        errs.append({"line": i, "column": 1, "message": "::: fence is never closed"})
    if dollars % 2:
        errs.append({"line": len(text.split("\n")), "column": 1, "message": "unbalanced $$ (display math)"})
    return errs


def renumber(blocks: list) -> None:
    n = 0
    for b in blocks:
        for x in walk(b):
            if x.get("type") == "footnote":
                n += 1
                x["attrs"] = {**x["attrs"], "n": n}


def from_text(text: str, base_content: dict | None = None, fmt: str = "markdown") -> dict:
    base = (base_content or {}).get("content") or []
    store = {oid(x): x for b in base for x in walk(b) if x.get("type") in ("opaque", "table")}
    errors = scan(text, store)
    if errors:
        return {"content": None, "warnings": [], "errors": errors}
    rd, aligned, ast1 = Rd(store), None, parse(text, fmt)
    new = [b for b in ast1 if b != SEP and not (b["t"] == "Div" and "sep" in b["c"][0][1])]
    if base:
        o = to_text({"content": base}, fmt)
        if o["text"] == text:  # untouched: byte-identical, nothing to merge
            return {"content": base_content, "warnings": [], "errors": []}
        old = [b for b in parse(o["text"], fmt) if not (b["t"] == "Div" and "sep" in b["c"][0][1])]
        if len(old) == len(base):
            aligned = old
        else:
            rd.w("could not align blocks with the original: the whole document was rebuilt from the text")
    out = []
    if aligned is not None:
        f0, f1 = [fp(b) for b in aligned], [fp(b) for b in new]
        match = {}
        for i, j, k in difflib.SequenceMatcher(None, f0, f1, autojunk=False).get_matching_blocks():
            for d in range(k):
                match[j + d] = i + d
        first = {}
        for i, f in enumerate(f0):
            first.setdefault(f, i)
        for j, b in enumerate(new):  # unmatched but equal to some original block: moved or duplicated
            src = match.get(j, first.get(f1[j]))
            out += [json.loads(json.dumps(base[src]))] if src is not None else r_block(b, rd)
    else:
        out = [x for b in new for x in r_block(b, rd)]
    if not out:
        out = [{"type": "paragraph", "attrs": {"styleId": None, "pfmt": None}}]
    renumber(out)
    return {"content": {**(base_content or {"type": "doc"}), "type": "doc", "content": out}, "warnings": rd.warn, "errors": []}
