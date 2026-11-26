"""TipTap JSON <-> text (markdown now, LaTeX in round 21) through pandoc's JSON AST. See docs/DECISIONS.md, round 20.

The JSON stays the source of truth; the text is a view. Going back, blocks whose parsed AST is unchanged against the AST of
the text the user started from are restored verbatim from the pre-switch JSON (attrs.orig, grids, shading...); only changed
or new blocks come from the text. Pandoc is run only through `run()`: fixed argv, no shell, `--sandbox`, timeout, size limits.
Rule that keeps alignment simple: every top-level TipTap block is exactly one top-level AST block (and back).
"""
import contextvars
import difflib
import hashlib
import json
import re
import subprocess
import threading

from . import latex_text as LT

API = [1, 23, 1, 2]  # pandoc-api-version of pandoc 3.14
MAX_TEXT = 2 * 1024 * 1024
MAX_OUT = 16 * 1024 * 1024
TIMEOUT_S = 20
_slots = threading.BoundedSemaphore(4)  # concurrent pandoc processes in this api worker

# markdown dialect: everything off that can reach outside the text (raw html/tex/attributes, citations, metadata blocks, includes)
FORMATS = {"markdown": "markdown-raw_html-raw_tex-raw_attribute-smart-auto_identifiers-implicit_figures-simple_tables-multiline_tables"
                       "-yaml_metadata_block-pandoc_title_block-citations-example_lists-markdown_in_html_blocks-native_divs-native_spans"
                       "+link_attributes+pipe_tables+grid_tables+fenced_divs+bracketed_spans+header_attributes+footnotes+inline_notes"
                       "+tex_math_dollars+strikeout+superscript+subscript+fancy_lists+startnum+escaped_line_breaks+lists_without_preceding_blankline",
           # LaTeX: no raw TeX through; --sandbox keeps \\input/\\includegraphics from reading anything; \\cite stays on (Cite nodes are mapped by hand)
           "latex": "latex-raw_tex"}
_FMT: contextvars.ContextVar[str] = contextvars.ContextVar("fmt", default="markdown")


class BridgeError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


def run(args: list[str], data: bytes, warn: list | None = None) -> bytes:
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
    err = p.stderr.decode("utf8", "replace")
    if p.returncode:
        raise BridgeError(422, "conversion failed: " + err[:300])
    if warn is not None:
        warn += LT.stderr_warnings(err)
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
def LX() -> bool:
    return _FMT.get() == "latex"


def raw(s: str) -> dict:
    return E("RawInline", ["latex", s])


def wrap(el: list, a: str, b: str) -> list:
    return [raw(a), *el, raw(b)]


def ann(d: dict) -> dict:
    """`% docgen:{json}` comment line carrying the Word-only attributes of the next block (inert in LaTeX, read back by `latex_text.pre`)"""
    return E("RawBlock", ["latex", "% docgen:" + json.dumps(d, sort_keys=True, separators=(",", ":"))])


def ann_of(kvs, classes=()) -> dict:
    return ann({**dict(kvs), **({"class": " ".join(classes)} if classes else {})})


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
            el = wrap(el, "\\underline{", "}") if LX() else [E("Underline", el)]
        elif t == "link":
            el = [E("Link", [attr(), el, [str(a.get("href") or ""), ""]])]
        elif t == "fmt":
            if a.get("va") in ("superscript", "subscript"):
                el = [E("Superscript" if a["va"] == "superscript" else "Subscript", el)]
            if a.get("strike") is True:
                el = wrap(el, "\\sout{", "}") if LX() else [E("Strikeout", el)]
            if LX():  # size, font, highlight, character styles have no plain-LaTeX spelling: they live in untouched blocks only
                if a.get("smallCaps"):
                    el = [E("SmallCaps", el)]
                if a.get("font") in ("Courier New", "Courier", "Consolas"):
                    el = wrap(el, "\\texttt{", "}")
                if re.fullmatch(r"[0-9A-Fa-f]{6}", str(a.get("color") or "")):
                    el = wrap(el, "\\textcolor[HTML]{%s}{" % a["color"].upper(), "}")
                continue
            kv = [["custom-style" if k == "rStyle" else k, enc(v)] for k, v in sorted(a.items()) if v not in (None, "") and k not in ("va", "strike")]
            if kv:
                el = [E("Span", [attr(kv), el])]
    return words(lead) + el + words(trail)


def w_inlines(nodes: list, pipe: str = "|") -> list:
    out, nodes = [], nodes or []
    for i, n in enumerate(nodes):
        t, a = n.get("type"), n.get("attrs") or {}
        if t == "text":
            out += w_text(n, pipe)
        elif t == "variable":
            out += words(var_text(a)) if LX() else [E("RawInline", ["markdown", var_text(a, pipe)])]
        elif t == "hardBreak":
            out.append(raw("\\newpage{}") if a.get("page") and LX() else E("Span", [attr(classes=["pagebreak"]), []]) if a.get("page") else E("LineBreak"))
        elif t == "math":
            out.append(E("Math", [E("DisplayMath" if a.get("display") else "InlineMath"), a.get("latex") or ""]))
        elif t == "field" and LX():  # a reference nobody can resolve in plain LaTeX, spelled so that it reads back as the same field
            code, arg = str(a.get("code") or "PAGE"), re.sub(r"[{}%\\]", "", str(a.get("arg") or ""))
            out.append(raw(f"\\ref{{{code}:{arg}}}" if code in ("SEQ", "REF") else f"\\pageref{{{code}:{arg}}}"))
        elif t == "field":
            out.append(E("Span", [attr([["field", str(a.get("code") or "PAGE")], ["arg", str(a.get("arg") or "")]]), words(str(a.get("text") or "?"))]))
        elif t == "footnote":
            txt, end = str(a.get("text") or ""), a.get("kind") == "endnote"
            nxt = nodes[i + 1] if i + 1 < len(nodes) else {}
            if LX():
                out += [raw("\\endnote{"), *words(txt), raw("}")] if end else [E("Note", [E("Para", words(txt))])]
                continue
            r = E("RawInline", ["markdown", "^[" + esc_inline(txt) + "]"])
            # `^[x]` right before `[1]{..}` reads as a reference link: a span around the note ends in `}` and keeps them apart
            risky = nxt.get("type") in ("field", "variable") or (nxt.get("type") == "text" and nxt["text"][:1] in "[{")
            out.append(E("Span", [attr([["kind", "endnote"]] if end else [], ["fn"]), [r]]) if end or risky else r)
    return out


class Out:
    def __init__(self):
        self.opaque: dict[str, dict] = {}
        self.tspecs: list[tuple[str, bool]] = []  # LaTeX tabulars written (column spec, hlines), resolved by latex_text.fix_tables


def opaque_div(n: dict, o: Out, kind: str):
    i = oid(n)
    o.opaque[i] = n
    if LX():
        return [ann({"opaque": i}), E("Para", [raw("\\fbox{[opaque: %s]}" % re.sub(r"[^\w .-]", "", kind))])]
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


def w_table_tex(n: dict, o: Out) -> list | None:
    """plain `tabular` (no package): \\hline grid, \\multicolumn, \\multirow; cells come from the writer between sentinel blocks"""
    rows = n.get("content") or []
    bdr = ((n.get("attrs") or {}).get("tfmt") or {}).get("bdr")
    grid = bdr is None or bool(bdr.get("insideH") or bdr.get("top"))
    vl = "|" if bdr is None or bool(bdr.get("insideV") or bdr.get("left")) else ""
    cover: list[int] = []  # rows still covered by a rowspan above, per column
    out, ncol = [], 0
    for ri, r in enumerate(rows):
        cells, col, start = list(r.get("content") or []), 0, len(out)
        while True:
            while col < len(cover) and cover[col] > 0:
                cover[col] -= 1
                out.append(E("RawBlock", ["latex", LT.SC]))
                col += 1
            if not cells:
                break
            c = cells.pop(0)
            a = c.get("attrs") or {}
            cs, rs = a.get("colspan") or 1, a.get("rowspan") or 1
            blocks = w_cell_blocks(c.get("content") or [], o)
            if blocks is None:
                return None
            pre, post = ("", "")
            if rs > 1:
                pre, post = f"\\multirow{{{rs}}}{{*}}{{" + pre, "}" + post
            if cs > 1:
                pre, post = f"\\multicolumn{{{cs}}}{{{vl if col == 0 else ''}l{vl}}}{{" + pre, "}" + post
            if pre:
                if blocks and blocks[0]["t"] in ("Plain", "Para"):
                    blocks[0]["c"] = [raw(pre), *blocks[0]["c"]]
                else:
                    blocks.insert(0, E("Plain", [raw(pre)]))
                if blocks[-1]["t"] in ("Plain", "Para"):
                    blocks[-1]["c"] = [*blocks[-1]["c"], raw(post)]
                else:
                    blocks.append(E("Plain", [raw(post)]))
            out += blocks
            out.append(E("RawBlock", ["latex", LT.SC]))
            for k in range(cs):
                while len(cover) <= col + k:
                    cover.append(0)
                cover[col + k] = rs - 1 if rs > 1 else 0
            col += cs
        ncol = max(ncol, col)
        if len(out) > start:  # the row end; header row and last row are followed by \\hline (a header is what the reader sees before the first \\hline after row 1)
            head = ri == 0 and all(c.get("type") == "tableHeader" for c in r.get("content") or [])
            out[-1] = E("RawBlock", ["latex", LT.SH if head or r is rows[-1] else LT.SR])
    if not ncol or not out:
        return None
    o.tspecs.append((vl + ("l" + vl) * ncol, grid))
    return [E("RawBlock", ["latex", LT.TB]), *out, E("RawBlock", ["latex", LT.TE])]


def w_image(b: dict):
    a = b.get("attrs") or {}
    if LX():
        opts = ",".join(f"{k}={a[k]}px" for k in ("width", "height") if a.get(k))
        return raw("\\includegraphics%s{docgen-image:%s}" % (f"[{opts}]" if opts else "", a.get("imageId")))
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
                if r["t"] == "OrderedList" and r["c"][0][0] != 1 and not LX():  # pandoc markdown reads nested lists only from 1: the start goes on a wrapper
                    r = E("Div", [attr([["start", str(r["c"][0][0])]]), [E("OrderedList", [[1, *r["c"][0][1:]], r["c"][1]])]])
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
    return E("OrderedList", [[a.get("start") or 1, E(style), E("Period")], items])


ALIGN_ENV = {"center": "center", "left": "flushleft", "right": "flushright"}
HR_P = {"bdr": {"bottom": {"val": "single", "sz": 6, "color": "auto"}}}  # the paragraph a horizontal rule becomes
NBSP = [E("Str", "\u00a0")]


def code_text(inl: list) -> str | None:
    """text of a paragraph that is only unformatted text and line breaks (a code block), else None"""
    if not inl or not all((x.get("type") == "text" and not x.get("marks")) or (x.get("type") == "hardBreak" and not (x.get("attrs") or {}).get("page")) for x in inl):
        return None
    return "".join(x["text"] if x["type"] == "text" else "\n" for x in inl)


def w_block(n: dict, o: Out):
    """one TipTap top-level block -> one AST block (LaTeX: possibly a `% docgen:` annotation block in front)"""
    t, a = n.get("type"), n.get("attrs") or {}
    deco = [["custom-style", a["styleId"]]] if a.get("styleId") else []
    deco += kv_of(a.get("pfmt"))
    lx = LX()
    if t == "paragraph":
        inl = w_inlines(n.get("content"))
        if not inl:
            if a.get("pfmt") == HR_P and not a.get("styleId"):
                return E("RawBlock", ["latex", "\\hrule"]) if lx else E("HorizontalRule")
            return [ann_of(deco, ["empty"]), E("Para", NBSP)] if lx else E("Div", [attr(deco, ["empty"]), []])
        if not a.get("pfmt"):
            if a.get("styleId") == "Quote":
                return E("BlockQuote", [E("Para", inl)])
            if a.get("styleId") == "Code" and (code := code_text(n.get("content"))) is not None:
                return E("CodeBlock", [attr(), code])
        if lx:
            if len(deco) == 1 and deco[0][0] == "jc" and deco[0][1] in ALIGN_ENV:
                env = ALIGN_ENV[deco[0][1]]  # pandoc's LaTeX writer drops the class of a Div, so the environment is written by hand
                return E("Para", [raw("\\begin{%s}\n" % env), *inl, raw("\n\\end{%s}" % env)])
            return [ann_of(deco), E("Para", inl)] if deco else E("Para", inl)
        return E("Div", [attr(deco), [E("Para", inl)]]) if deco else E("Para", inl)
    if t == "heading":
        inl = w_inlines(n.get("content"))
        if not inl:
            d = [*deco, ["heading", str(a.get("level") or 1)]]
            return [ann_of(d, ["empty"]), E("Para", NBSP)] if lx else E("Div", [attr(d, ["empty"]), []])
        h = E("Header", [a.get("level") or 1, attr([] if lx else deco), inl])
        return [ann_of(deco), h] if lx and deco else h
    if t in ("bulletList", "orderedList"):
        r = w_list(n, o)
        if r is not None:
            return r
    elif t == "table":
        r = w_table_tex(n, o) if lx else w_table(n, o)
        if r is not None:
            dl = a.get("dynamicList")
            return [ann({"dynamic": str(dl)}), *r] if lx and dl else r
        return opaque_div(n, o, "table")
    elif t == "image":
        extra = {k: a[k] for k in ("alt", "align") if a.get(k)}
        return [ann(extra), E("Para", [w_image(n)])] if lx and extra else E("Para", [w_image(n)])
    elif t == "toc":
        kv = [["toc", str(a.get("kind") or "toc")], *kv_of({k: a.get(k) for k in ("levels", "title", "label")})]
        if lx:
            bare = not a.get("title") and not a.get("label") and a.get("levels") in (None, "", "1-3")
            cmd = {"tof": "\\listoffigures", "tot": "\\listoftables"}.get(a.get("kind"), "\\tableofcontents")
            return E("RawBlock", ["latex", cmd]) if bare else [ann_of(kv), E("RawBlock", ["latex", cmd])]
        return E("Div", [attr(kv), []])
    elif t == "sectBreak":
        kv = [["sectbreak", str(a.get("brk") or "nextPage")], *kv_of({k: a.get(k) for k in ("orient", "top", "right", "bottom", "left")})]
        return [ann_of(kv), E("RawBlock", ["latex", "\\clearpage"])] if lx else E("Div", [attr(kv), []])
    return opaque_div(n, o, str(a.get("kind") or t) if t == "opaque" else str(t))


SEP = E("Div", [attr(classes=["sep"]), []])  # keeps two adjacent lists / block quotes from merging (markdown only: LaTeX environments never merge)


def blocks_to_ast(blocks: list, o: Out) -> list:
    out = []
    for b in blocks:
        xs = w_block(b, o)
        for x in xs if isinstance(xs, list) else [xs]:
            if not LX() and out and x["t"] in ("BulletList", "OrderedList", "BlockQuote") and out[-1]["t"] == x["t"]:
                out.append(SEP)
            out.append(x)
    return out


def to_text(content: dict, fmt: str = "markdown") -> dict:
    o, tok = Out(), _FMT.set(fmt)
    try:
        ast = blocks_to_ast((content or {}).get("content") or [], o)
    finally:
        _FMT.reset(tok)
    text = run(["-f", "json", "-t", _fmt(fmt), "--wrap=none"], _ast(ast)).decode("utf8")
    if fmt == "latex":  # body-only, no package macros: drop what pandoc adds for its own preamble
        text = LT.fix_tables(text, o.tspecs)
        text = re.sub(r"\\begin\{enumerate\}\n\s*\\def\\labelenum[iv]+\{\\(alph|Alph|roman|Roman|arabic)\{enum[iv]+\}\.\}", lambda m: "\\begin{enumerate}" + {"alph": "[a.]", "Alph": "[A.]", "roman": "[i.]", "Roman": "[I.]"}.get(m.group(1), ""), text)
        text = re.sub(r"^[ \t]*\\tightlist\n|^\\addcontentsline\{[^}]*\}\{[^}]*\}\{.*\}\n?", "", text, flags=re.M)
    return {"text": text, "map": {"opaque": {k: (v.get("attrs") or {}).get("kind") or v.get("type") for k, v in o.opaque.items()}}}


# ---- AST -> TipTap ---------------------------------------------------------------------------------------------------------
VAR_RE = re.compile(r"\{\{\s*([A-Za-z_]\w*(?:\.\w+)?)((?:\s*\\?\|\s*[^|}]+?)*)\s*\}\}")
STYLE_KINDS = {"LowerAlpha": "lower-alpha", "UpperAlpha": "upper-alpha", "LowerRoman": "lower-roman", "UpperRoman": "upper-roman"}


class Rd:
    def __init__(self, store: dict, bib: dict | None = None, bms: set | None = None):
        self.store, self.warn, self.bib, self.bms, self.endnote, self.strict_images = store, [], bib or {}, bms or set(), False, _FMT.get() == "latex"

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
            for k, part in enumerate(c.split("DOCGENPB") if "DOCGENPB" in c else [c]):  # LaTeX \\newpage / \\clearpage token
                if k:
                    nodes.append({"type": "hardBreak", "attrs": {"page": True}})
                text(part)
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
        elif t == "Link" and any(k == "reference-type" for k, _ in c[0][2]):  # LaTeX \\ref \\eqref \\autoref \\pageref
            ref = dict(c[0][2]).get("reference", "")
            if m := re.fullmatch(r"(?:PG:)?(SEQ|REF|PAGE|NUMPAGES|SECTIONPAGES|PAGEREF)(?::(.*))?", ref):
                nodes.append({"type": "field", "attrs": {"code": m.group(1), "arg": m.group(2) or "", "text": ""}})
            elif ref in rd.bms:
                nodes.append({"type": "field", "attrs": {"code": "REF", "arg": ref, "text": ""}})
            else:
                rd.w(f"cross-reference {ref!r} has no target in the document, kept as text")
                text(f"[{ref}]")
        elif t == "Cite":
            keys = [x["citationId"] for x in c[0]]
            for k in keys:
                if k not in rd.bib:
                    rd.w(f"citation {k!r} has no \\bibitem, kept as the key")
            text("[" + ", ".join(str(rd.bib.get(k, k)) for k in keys) + "]")
        elif t == "Link":
            r_inlines(c[1], rd, marks + ({"type": "link", "attrs": {"href": c[2][0]}},), nodes)
        elif t == "Span":
            (_, cls, kvs), inner = c
            d = dict(kvs)
            if "pagebreak" in cls:
                nodes.append({"type": "hardBreak", "attrs": {"page": True}})
            elif "fn" in cls:  # footnote wrapper written by the markdown writer (kind, and a separator from a following `[..]`)
                rd.endnote = d.get("kind") == "endnote"
                r_inlines(inner, rd, marks, nodes)
                rd.endnote = False
            elif "underline" in cls:
                r_inlines(inner, rd, marks + ({"type": "underline"},), nodes)
            elif "field" in d:
                nodes.append({"type": "field", "attrs": {"code": d["field"], "arg": d.get("arg", ""), "text": plain(inner)}})
            else:
                f = {("rStyle" if k == "custom-style" else k): v for k, v in dict_of(kvs).items()}
                if m := re.fullmatch(r"color:\s*#?([0-9A-Fa-f]{6}|[a-z]+)", str(f.pop("style", ""))):  # LaTeX \\textcolor
                    f["color"] = m.group(1).lower() if len(m.group(1)) == 6 else NAMED.get(m.group(1))
                r_inlines(inner, rd, add_fmt(marks, **f) if f else marks, nodes)
        elif t == "Math":
            nodes.append({"type": "math", "attrs": {"latex": c[1], "display": c[0]["t"] == "DisplayMath"}})
        elif t == "Note":
            txt = " ".join(plain(b.get("c")) for b in c if isinstance(b.get("c"), list))
            end = txt.startswith("DOCGENEND")  # LaTeX \\endnote{}, see latex_text.pre
            nodes.append({"type": "footnote", "attrs": {"kind": "endnote" if end or rd.endnote else "footnote", "text": txt.removeprefix("DOCGENEND").strip(), "n": 1}})
        elif t == "Code":
            text(c[1], add_fmt(marks, font="Courier New"))
        elif t == "Quoted":
            q = "\u201c\u201d" if c[0]["t"] == "DoubleQuote" else "\u2018\u2019"
            text(q[0]); r_inlines(c[1], rd, marks, nodes); text(q[1])
        elif t == "Image":
            if (n := image_node(c, rd)) is not None:
                nodes.append(n)
        else:
            rd.w(f"unsupported inline {t} dropped")
    return nodes


NAMED = {"red": "ff0000", "green": "00ff00", "blue": "0000ff", "black": "000000", "white": "ffffff", "gray": "808080", "yellow": "ffff00", "cyan": "00ffff",
         "magenta": "ff00ff", "orange": "ff8000", "purple": "800080", "brown": "804000"}


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


UNITS = {"px": 1, "pt": 4 / 3, "cm": 96 / 2.54, "mm": 96 / 25.4, "in": 96, "bp": 96 / 72}


def px(v) -> int | None:
    m = re.fullmatch(r"([\d.]+)\s*(px|pt|cm|mm|in|bp)?", str(v or ""))
    return round(float(m.group(1)) * UNITS[m.group(2) or "px"]) or None if m else None


def image_node(c, rd) -> dict | None:
    """pandoc Image -> image block node; LaTeX may only name images of this app (`docgen-image:<id>` or the bare id), never a path or URL"""
    ident = c[2][0].removeprefix("docgen-image:")
    if rd.strict_images and not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{7,63}", ident):
        rd.w(f"image {ident[:40]!r} dropped: only docgen-image:<id> images can be used")
        return None
    a = dict_of(c[0][2])
    return {"type": "image", "attrs": {"imageId": ident, "alt": plain(c[1]), "width": px(a.get("width")), "height": px(a.get("height")), "align": a.get("align"), "anchored": None}}


def para(i, rd, attrs: dict | None = None) -> list:
    """inlines -> [paragraph + image blocks]: images are block nodes, so a paragraph is cut around them"""
    attrs, out, cur = attrs or {}, [], []
    for x in inl(i, rd):
        if x["type"] == "image":
            if any(y["type"] != "text" or y["text"].strip() for y in cur):
                out.append(_p(cur, attrs))
            cur = []
            out.append(x)
        else:
            cur.append(x)
    if cur and (not out or any(y["type"] != "text" or y["text"].strip() for y in cur)) or not out:
        out.append(_p(cur, attrs))
    return out


def _p(c, attrs):
    return {"type": "paragraph", "attrs": {"styleId": attrs.get("styleId"), "pfmt": attrs.get("pfmt")}, **({"content": c} if c else {})}


def caption_para(label: str, blocks: list, rd) -> dict:
    """`\\caption{..}` of a table/figure float -> the SEQ caption paragraph used everywhere else (\"Table 1: text\", lists of tables read it)"""
    ils = []
    for b in blocks:
        ils += ([E("Space")] if ils else []) + (b.get("c") or [])
    return {"type": "paragraph", "attrs": {"styleId": "Caption", "pfmt": None}, "content": [
        {"type": "text", "text": label + " "}, {"type": "field", "attrs": {"code": "SEQ", "arg": label, "text": ""}}, {"type": "text", "text": ": "}, *inl(ils, rd)]}


def deco(kvs) -> dict:
    d = dict_of(kvs, skip=("opaque", "toc", "sectbreak", "dynamic", "heading", "start"))
    return {"styleId": d.pop("custom-style", None), "pfmt": d or None}


ALIGN_ENV_R = {v: k for k, v in ALIGN_ENV.items()}


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
        if any(x["type"] == "image" for x in content):
            rd.w("image inside a heading dropped")
            content = [x for x in content if x["type"] != "image"]
        return [{"type": "heading", "attrs": {"level": min(c[0], 4), **d}, **({"content": content} if content else {})}]
    if t in ("BulletList", "OrderedList"):
        return [r_list(b, rd)]
    if t == "Table":
        cap = c[1][1]
        return ([caption_para("Table", cap, rd)] if cap else []) + [r_table(b, rd)]
    if t == "Figure":
        return [x for k in c[2] for x in r_block(k, rd)] + ([caption_para("Figure", c[1][1], rd)] if c[1][1] else [])
    if t == "DefinitionList":
        rd.w("description list converted to paragraphs (term in bold)")
        out = []
        for term, defs in c:
            out.append(_p(split_vars(r_inlines(term, rd, ({"type": "bold"},))), {}))
            out += [x for d in defs for k in d for x in r_block(k, rd)]
        return out
    if t == "BlockQuote":
        return [x for y in c for x in r_block(y, rd, style={"styleId": "Quote", "pfmt": None})]
    if t == "CodeBlock":  # one paragraph in the Code style, lines separated by line breaks
        content = []
        for k, line in enumerate(c[1].split("\n")):
            content += ([{"type": "hardBreak", "attrs": {"page": False}}] if k else []) + ([{"type": "text", "text": line}] if line else [])
        return [{"type": "paragraph", "attrs": {"styleId": "Code", "pfmt": None}, **({"content": content} if content else {})}]
    if t == "HorizontalRule":
        return [{"type": "paragraph", "attrs": {"styleId": None, "pfmt": json.loads(json.dumps(HR_P))}}]
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
        if "start" in d and len(kids) == 1 and kids[0]["t"] == "OrderedList":
            lst = r_list(kids[0], rd)
            lst["attrs"]["start"] = dec(d["start"])
            return [lst]
        dd = deco(kvs)
        if not (dd["styleId"] or dd["pfmt"]) and (al := next((ALIGN_ENV_R[x] for x in cls if x in ALIGN_ENV_R), None)):
            dd = {"styleId": None, "pfmt": {"jc": al}}
        return [x for k in kids for x in r_block(k, rd, nested=False, style=dd if dd["styleId"] or dd["pfmt"] else style)]
    rd.w(f"unsupported block {t} dropped")
    return []


# ---- text -> TipTap, with block alignment -------------------------------------------------------------------------------
def tok_of(b: dict) -> str | None:
    """the one word of a paragraph that holds nothing else (LaTeX tokens made by latex_text.pre)"""
    c = b.get("c") if b.get("t") == "Para" else None
    return c[0]["c"] if c and len(c) == 1 and c[0]["t"] == "Str" else None


def meta_text(m: dict | None) -> list:
    if not m:
        return []
    if m["t"] == "MetaInlines":
        return m["c"]
    if m["t"] == "MetaString":
        return words(m["c"])
    if m["t"] == "MetaList":
        out = []
        for x in m["c"]:
            out += ([E("Str", ","), E("Space")] if out else []) + meta_text(x)
        return out
    if m["t"] == "MetaBlocks":
        return [x for b in m["c"] for x in (b.get("c") or []) if isinstance(b.get("c"), list)]
    return []


def lx_wrap(b: dict, a: dict) -> dict:
    """`% docgen:{...}` attributes -> the same Div (or Header attributes) a markdown attribute block gives"""
    kv, cls = [[k, str(v)] for k, v in a.items() if k != "class"], str(a.get("class", "")).split()
    if b["t"] == "Header":
        b["c"][1] = attr(kv, cls)
        return b
    c = b.get("c")
    if b["t"] == "Para" and isinstance(c, list) and any(x["t"] == "Image" for x in c) and set(a) <= {"alt", "align"}:  # image: alt/align live on the image
        for x in c:
            if x["t"] == "Image":
                x["c"][0][2] += [["align", str(a["align"])]] if a.get("align") else []
                x["c"][1] = words(str(a["alt"])) if a.get("alt") else x["c"][1]
        return b
    return E("Div", [attr(kv, cls), [b]])


def lx_norm(blocks: list, anns: list, meta: dict, info: dict) -> list:
    """top-level normalisation of what the LaTeX reader returns, so that one TipTap block is one AST block as in markdown"""
    bib = [t[10:] for b in blocks if (t := first_str(b)) and t.startswith("DOCGENBIB:")]
    info["bib"] = {k: n for n, k in reversed(list(enumerate(bib, 1)))}
    out, pending, sty = [], None, lambda name: [["custom-style", name]]
    for b in blocks:
        tok = tok_of(b) or ""
        if tok.startswith("DOCGENMARK") and tok[10:].isdigit() and int(tok[10:]) < len(anns):
            pending = anns[int(tok[10:])]
            continue
        if tok == "DOCGENTITLE":
            if meta.get("title"):
                out.append(E("Div", [attr(sty("Title")), [E("Para", meta_text(meta["title"]))]]))
            sub = [x for k in ("author", "date") if meta.get(k) for x in ([E("Str", ","), E("Space")] if k == "date" and meta.get("author") else []) + meta_text(meta[k])]
            if sub:
                out.append(E("Div", [attr(sty("Subtitle")), [E("Para", sub)]]))
            continue
        if tok in ("DOCGENTOC", "DOCGENTOF", "DOCGENTOT"):
            b = E("Div", [attr([["toc", {"C": "toc", "F": "tof", "T": "tot"}[tok[-1]]]]), []])
        elif (f := first_str(b)) and f.startswith("DOCGENBIB:"):
            b = E("Div", [attr(sty("Bibliography")), [E("Para", [E("Str", f"[{info['bib'][f[10:]]}]"), *b["c"][1:]])]])
        out.append(lx_wrap(b, pending) if pending is not None else b)
        pending = None
    return out


def first_str(b: dict) -> str | None:
    c = b.get("c") if b.get("t") == "Para" else None
    return c[0]["c"] if c and c[0]["t"] == "Str" else None


def parse(text: str, fmt: str, info: dict | None = None) -> list:
    if len(text.encode()) > MAX_TEXT:
        raise BridgeError(413, "text too large")
    if fmt != "latex":
        return json.loads(run(["-f", _fmt(fmt), "-t", "json", "--wrap=none"], text.encode()))["blocks"]
    info = {} if info is None else info
    clean, anns = LT.pre(text)
    info["warn"] = []
    ast = json.loads(run(["-f", _fmt(fmt), "-t", "json", "--wrap=none"], clean.encode(), info["warn"]))
    return lx_norm(ast["blocks"], anns, ast.get("meta") or {}, info)


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


def cell_text(t: dict) -> str:
    return " ".join(x["text"] for x in walk(t) if x.get("type") == "text")


def carry_table(nt: dict, old: dict) -> None:
    """an edited table keeps what the text cannot say (widths, style, borders, shading, row/cell/paragraph formatting) of the original it
    was matched with; rows and columns that are new clone their nearest neighbour"""
    oa = {k: v for k, v in (old.get("attrs") or {}).items() if k not in ("orig", "dynamicList")}
    ncol = max((sum(c["attrs"]["colspan"] for c in r["content"]) for r in nt["content"]), default=0)
    if isinstance(oa.get("grid"), list) and oa["grid"] and len(oa["grid"]) != ncol:
        oa["grid"] = (oa["grid"] + oa["grid"][-1:] * ncol)[:ncol]
    nt["attrs"] = {**nt["attrs"], **oa}
    orows = old.get("content") or []
    for ri, row in enumerate(nt["content"]):
        if not orows:
            break
        src = orows[min(ri, len(orows) - 1)]
        if src.get("attrs"):
            row["attrs"] = json.loads(json.dumps(src["attrs"]))
        for ci, cell in enumerate(row["content"]):
            if not src.get("content"):
                break
            sc = src["content"][min(ci, len(src["content"]) - 1)]
            keep = {k: v for k, v in (sc.get("attrs") or {}).items() if k not in ("colspan", "rowspan", "orig")}
            if sc["attrs"].get("colspan") != cell["attrs"]["colspan"]:
                keep.pop("colwidth", None)
            cell["attrs"] = {**cell["attrs"], **json.loads(json.dumps(keep))}
            cell["type"] = sc["type"]
            sps = [p for p in sc.get("content") or [] if p.get("type") == "paragraph"]
            for pi, p in enumerate(c for c in cell["content"] if c.get("type") == "paragraph"):
                if sps:
                    p["attrs"] = {**p.get("attrs", {}), **json.loads(json.dumps({k: v for k, v in (sps[min(pi, len(sps) - 1)].get("attrs") or {}).items() if k != "orig"}))}


def best_table(nt: dict, j: int, n: int, cands: dict) -> int | None:
    """index of the unmatched original table most like the edited one (content, shape, position), None if none is close enough"""
    txt, shape, best = cell_text(nt), (len(nt["content"]), len(nt["content"][0]["content"]) if nt["content"] else 0), (0.45, None)
    for i, t in cands.items():
        rows = t.get("content") or []
        sc = 0.6 * difflib.SequenceMatcher(None, cell_text(t), txt, autojunk=False).ratio() + 0.2 * (shape == (len(rows), len(rows[0]["content"]) if rows else 0)) \
            + 0.2 * (1 - abs(i - j) / max(n, 1))
        if sc > best[0]:
            best = (sc, i)
    return best[1]


def from_text(text: str, base_content: dict | None = None, fmt: str = "markdown") -> dict:
    tok = _FMT.set(fmt)
    try:
        return _from_text(text, base_content, fmt)
    finally:
        _FMT.reset(tok)


def _from_text(text: str, base_content: dict | None, fmt: str) -> dict:
    base = (base_content or {}).get("content") or []
    store = {oid(x): x for b in base for x in walk(b) if x.get("type") in ("opaque", "table")}
    errors, lw = LT.scan(text, store) if fmt == "latex" else (scan(text, store), [])
    if errors:
        return {"content": None, "warnings": [], "errors": errors}
    info: dict = {}
    try:
        ast1 = parse(text, fmt, info)
    except BridgeError as e:  # pandoc's own syntax errors (LaTeX): report them with their line instead of failing the request
        if fmt == "latex" and e.status == 422 and e.msg.startswith("conversion failed"):
            return {"content": None, "warnings": [], "errors": [LT.parse_error(e.msg)]}
        raise
    rd, aligned = Rd(store, info.get("bib"), {x["attrs"]["bm"] for b in base for x in walk(b) if (x.get("attrs") or {}).get("bm")}), None
    for w in lw + info.get("warn", []):
        rd.w(w)
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
        free = {i: b for i, b in enumerate(base) if b.get("type") == "table" and i not in match.values()}  # originals an edited table may inherit from
        for j, b in enumerate(new):  # unmatched but equal to some original block: moved or duplicated
            src = match.get(j, first.get(f1[j]))
            if src is not None:
                out.append(json.loads(json.dumps(base[src])))
                continue
            for x in r_block(b, rd):
                if x["type"] == "table" and x["content"] and (k := best_table(x, j, len(new), free)) is not None:
                    carry_table(x, free.pop(k))
                out.append(x)
    else:
        out = [x for b in new for x in r_block(b, rd)]
    if not out:
        out = [{"type": "paragraph", "attrs": {"styleId": None, "pfmt": None}}]
    renumber(out)
    return {"content": {**(base_content or {"type": "doc"}), "type": "doc", "content": out}, "warnings": rd.warn, "errors": []}
