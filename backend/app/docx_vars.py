"""Per-document values inside a DOCX (cover text, header/footer fields, content controls): find them, turn them into
`{{ name }}` placeholders, and repair fields whose source is gone at export time.

Everything works on lxml trees, so the same code serves header/footer parts, base-document bodies and the raw XML of
opaque blocks of an import. Tables, tables of contents/figures and the main body are never candidates for plain text;
fields and content controls are found anywhere in the scanned units (a cover table holds the document title field).
"""
import copy
import hashlib
import io
import re
import zipfile

from lxml import etree

from .dates import dmy, parse_date

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
KEEP = {"PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION", "PAGEREF", "TOC", "TOA", "TC", "XE", "SEQ", "HYPERLINK", "NOTEREF",
        "INDEX", "SYMBOL", "EQ", "IF", "DATE", "TIME", "PRINTDATE", "NUMWORDS", "NUMCHARS", "FILENAME"}
BUILTIN_FIELD = {"TITLE": "doc.title", "AUTHOR": "author", "LASTSAVEDBY": "author", "SAVEDATE": "doc.date",
                 "CREATEDATE": "doc.date"}
DOCPROP = {"title": "doc.title", "author": "author", "company": "org.name", "lastmodifiedby": "author",
           "createtime": "doc.date", "savetime": "doc.date"}
HINTS = (("title", "doc.title"), ("version", "doc.version"), ("revision", "doc.version"), ("date", "doc.date"),
         ("author", "author"), ("prepared", "author"), ("company", "org.name"), ("organi", "org.name"))
VERSION_RE = re.compile(r"(?i)(v(ersion)?\.?\s*)?\d+(\.\d+)+|(v(ersion)?|rev(ision)?|issue)\.?\s*\d[\w.]*")
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")
BUILTIN_ROOTS = {"doc", "org", "author", "revision_history", "linked_documents"}
STRONG_STYLES = {"Title"}  # Subtitle is often used as a heading of cover sections ("Table of contents")


def text(el) -> str:
    return "".join(t.text or "" for t in el.iter(W + "t"))


def style_of(p) -> str:
    s = p.find(f"{W}pPr/{W}pStyle")
    return s.get(W + "val") if s is not None else ""


def fields(p) -> list[dict]:
    """Outermost fields of paragraph `p`: {instr, kw, arg, runs (begin..end), result (runs after separate)}."""
    out, depth, cur = [], 0, None
    for r in p.iter(W + "r"):
        ch = r.find(W + "fldChar")
        typ = ch.get(W + "fldCharType") if ch is not None else None
        if typ == "begin":
            if depth == 0:
                cur = dict(instr="", runs=[], result=[], sep=False)
            depth += 1
        if depth:
            cur["runs"].append(r)
            if typ == "separate" and depth == 1:
                cur["sep"] = True
            elif typ is None:
                if cur["sep"]:
                    cur["result"].append(r)
                elif depth == 1:
                    cur["instr"] += "".join(x.text or "" for x in r.iter(W + "instrText"))
        if typ == "end" and depth:
            depth -= 1
            if depth == 0:
                out.append(cur)
    for e in p.iter(W + "fldSimple"):
        rs = list(e.iter(W + "r"))
        out.append(dict(instr=e.get(W + "instr") or "", runs=rs, result=rs, el=e))
    for f in out:
        parts = f["instr"].replace("\\", " \\").split()
        f["kw"], f["arg"] = (parts[0].upper() if parts else ""), (parts[1].strip('"') if len(parts) > 1 else "")
        f["text"] = "".join(t.text or "" for r in f["result"] for t in r.iter(W + "t"))
    return out


def set_text(runs, value: str) -> None:
    """Value into the first run's first w:t, the rest of the run texts emptied (formatting of the first run wins)."""
    ts = [t for r in runs for t in r.iter(W + "t")]
    if not ts:
        return
    ts[0].text = value
    ts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for t in ts[1:]:
        t.text = ""


def flatten(f: dict, value: str) -> None:
    """Replace a whole field (instruction + result) by one plain run with `value`, keeping the result's formatting."""
    src = (f["result"] or f["runs"])[0]
    new = copy.deepcopy(src)
    for c in list(new):
        if c.tag != W + "rPr":
            new.remove(c)
    t = new.makeelement(W + "t", {})
    t.text = value
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    new.append(t)
    first = f.get("el") if f.get("el") is not None else f["runs"][0]
    first.addprevious(new)
    for r in ([f["el"]] if f.get("el") is not None else f["runs"]):
        r.getparent().remove(r)


def bookmarks(*roots) -> set:
    return {b.get(W + "name") for r in roots for b in r.iter(W + "bookmarkStart")}


def bookmark_texts(*roots) -> dict:
    """{bookmark name: the text it encloses} (what a REF field to it shows)."""
    out: dict = {}
    for root in roots:
        opened: dict = {}
        for e in root.iter():
            if e.tag == W + "bookmarkStart":
                opened[e.get(W + "id")] = e.get(W + "name")
                out.setdefault(e.get(W + "name"), "")
            elif e.tag == W + "bookmarkEnd":
                opened.pop(e.get(W + "id"), None)
            elif e.tag == W + "t":
                for n in opened.values():
                    out[n] += e.text or ""
    return out


def _is_toc(p, fs) -> bool:
    s = style_of(p)
    if s.startswith(("TOC", "TableofFigures", "TableofAuthorities")) or any(f["kw"] in ("TOC", "TOA") for f in fs):
        return True
    return any("Table of Contents" in (g.get(W + "val") or "") for a in p.iterancestors(W + "sdt")
               for g in a.iter(W + "docPartGallery"))


def _label(p) -> str:
    """Text of the cell before the one holding `p` (the form label of a cover table row)."""
    tc = next(p.iterancestors(W + "tc"), None)
    prev = tc.getprevious() if tc is not None else None
    return text(prev).strip() if prev is not None and prev.tag == W + "tc" else ""


def walk(root, where: str, plain: bool, marks: set):
    """Yield (kind, value, hint, strong, where, apply(name)) for each replaceable value below `root`
    (text that already holds `{{ }}` / `{% %}` is a variable, not a value)."""
    for item in _walk(root, where, plain, marks):
        if "{{" not in item[1] and "{%" not in item[1]:
            yield item


def _walk(root, where: str, plain: bool, marks: set):
    done: set = set()
    for sdt in list(root.iter(W + "sdt")):
        pr, c = sdt.find(W + "sdtPr"), sdt.find(W + "sdtContent")
        if pr is None or c is None or pr.find(W + "docPartObj") is not None or pr.find(W + "showingPlcHdr") is not None \
                or not any(pr.find(W + k) is not None for k in ("dataBinding", "text", "date", "dropDownList", "comboBox")) \
                or "TOC" in "".join(x.text or "" for x in c.iter(W + "instrText")):
            continue
        v = text(c).strip()
        if not v:
            continue
        done.update(c.iter(W + "p"))
        hint = next((e.get(W + "val") for k in ("alias", "tag") if (e := pr.find(W + k)) is not None), "")

        def apply(name, c=c, pr=pr):
            set_text([c], "{{ %s }}" % name)
            if (b := pr.find(W + "dataBinding")) is not None:  # else Word refills it from custom XML
                pr.remove(b)
        yield "control", v, hint, False, where, apply
    for p in list(root.iter(W + "p")):
        if p in done:
            continue
        fs = fields(p)
        if _is_toc(p, fs):
            continue
        cands = False
        for f in fs:
            kw = f["kw"]
            if kw in KEEP or (kw in ("REF", "STYLEREF") and f["arg"] in marks):
                continue
            v = f["text"].strip()
            if kw == "FORMTEXT":
                if not re.search(r"\w", v):
                    continue
                kind, hint = "formtext", _label(p) or style_of(p)
            else:
                kind, hint = "field", f["arg"] if kw in ("REF", "STYLEREF", "DOCPROPERTY") else kw
            cands = True
            yield kind, v, hint, False, where, (lambda name, f=f: _put_field(f, name))
        if fs or cands or not plain or next(p.iterancestors(W + "tc"), None) is not None:
            continue
        runs = [r for r in p.iter(W + "r")]
        v = text(p).strip()
        if not re.search(r"\w", v) or len(v) > 200:
            continue
        kind = "date" if parse_date(v) else "version" if VERSION_RE.fullmatch(v) else "text"
        yield kind, v, style_of(p), style_of(p) in STRONG_STYLES, where, (lambda name, runs=runs: set_text(runs, "{{ %s }}" % name))


def _put_field(f: dict, name: str) -> None:
    if f["kw"] == "FORMTEXT":
        set_text(f["result"], "{{ %s }}" % name)
    else:
        flatten(f, "{{ %s }}" % name)


def snake(s: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s.strip())
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()[:30]


def suggest(kind: str, hint: str, value: str, strong: bool) -> str:
    h = (hint or "").lower()
    words = re.findall(r"[a-z]+", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", hint or "").lower())  # "Sl_DocumentTitle" -> sl document title
    if kind == "field" and hint.upper() in BUILTIN_FIELD:
        return BUILTIN_FIELD[hint.upper()]
    if kind == "field" and h in DOCPROP:
        return DOCPROP[h]
    for key, name in HINTS:
        if any(w.startswith(key) for w in words):
            return name
    if hint == "Title" or kind == "date":
        return "doc.title" if hint == "Title" else "doc.date"
    if kind == "version":
        return "doc.version"
    n = snake(re.sub(r"^(sl|doc)_", "", hint, flags=re.I)) if hint and not hint.startswith(("Normal", "Heading")) else ""
    n = n or snake(" ".join(value.split()[:3])) or "value"
    return n if NAME_RE.fullmatch(n) else "v_" + n


def scan(units: list) -> list[dict]:
    """units = [(where, root, plain)]. Candidates merged by value: {id, kind, value, locs{where:n}, name, include}."""
    marks = bookmarks(*(u[1] for u in units))
    seen: dict = {}
    for where, root, plain in units:
        for kind, v, hint, strong, wh, _ in walk(root, where, plain, marks):
            c = seen.setdefault(v, dict(id=hashlib.sha1(v.encode()).hexdigest()[:10], kind=kind, value=v, locs={},
                                        name=suggest(kind, hint, v, strong),
                                        include=kind != "text" or strong))
            c["locs"][wh] = c["locs"].get(wh, 0) + 1
            if kind == "date":
                c["display"] = dmy(v)
    out, used = list(seen.values()), set()
    for c in out:  # distinct values must not share a user variable name unless it is a built-in
        base, n = c["name"], 1
        while c["name"] in used and c["name"].split(".")[0] not in BUILTIN_ROOTS | {"author"}:
            n += 1
            c["name"] = f"{base}_{n}"
        used.add(c["name"])
    return out


def apply(units: list, picks: dict) -> int:
    """picks = {value: name}: replace every occurrence by `{{ name }}`; returns the number of replacements."""
    marks, n = bookmarks(*(u[1] for u in units)), 0
    for where, root, plain in units:
        for kind, v, hint, strong, wh, ap in list(walk(root, where, plain, marks)):
            if v in picks:
                ap(picks[v])
                n += 1
    return n


# ---- export: repair fields whose source is gone --------------------------------------------------

def fix_fields(roots: list, built_ins: dict, warnings: list) -> None:
    """REF/STYLEREF to a bookmark that is not in the package would show 'Error! Reference source not found':
    keep the cached text as plain text instead. TITLE/AUTHOR/SAVEDATE/DOCPROPERTY fields get the built-in value
    (and stay live). PAGE/NUMPAGES and everything else are untouched."""
    marks = bookmark_texts(*roots)
    for root in roots:
        for p in list(root.iter(W + "p")):
            for f in fields(p):
                kw, arg = f["kw"], f["arg"]
                if kw == "REF" and marks.get(arg):  # the source text may have changed (cover title): show it now
                    set_text(f["result"], marks[arg])
                elif kw in ("REF", "STYLEREF") and arg not in marks and not arg.startswith("_"):
                    flatten(f, f["text"])
                    warnings.append(f"field {kw} {arg}: reference source not found, kept the text {f['text'][:40]!r}")
                elif kw in ("REF", "STYLEREF") and arg not in marks:
                    flatten(f, f["text"])
                elif kw == "TOC":  # contents / lists of figures and tables of the base: Word refreshes them on open
                    f["runs"][0].find(W + "fldChar").set(W + "dirty", "true")
                else:
                    key = BUILTIN_FIELD.get(kw) or (DOCPROP.get(arg.lower()) if kw == "DOCPROPERTY" else None)
                    if key and built_ins.get(key) is not None:
                        set_text(f["result"], str(built_ins[key]))


def text_width(sect_prs) -> int:
    """Widest text area (twips) of the given sectPr elements: a table must overflow even that to be clipped."""
    def tw(sp):
        sz, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
        g = lambda e, k, d: int(e.get(W + k) or d) if e is not None else d
        return g(sz, "w", 11906) - g(mar, "left", 1440) - g(mar, "right", 1440)
    return max((tw(sp) for sp in sect_prs), default=9026)


def clip_tables(root, width: int, warnings: list | None = None) -> None:
    """Header/footer tables of a hand-edited base (a duplicated column pasted in Word) can be far wider than the page: the
    extra cells are drawn over the right page edge. Trailing columns that lie mostly outside the text area are dropped;
    tables with merged cells are left alone."""
    for tbl in root.findall(W + "tbl"):
        grid = tbl.find(W + "tblGrid")
        cols = [int(g.get(W + "w") or 0) for g in grid] if grid is not None else []
        ind = tbl.find(f"{W}tblPr/{W}tblInd")
        start = int(ind.get(W + "w") or 0) if ind is not None else 0
        rows = tbl.findall(W + "tr")
        if len(cols) < 2 or any(len(r.findall(W + "tc")) != len(cols) or r.find(f".//{W}gridSpan") is not None
                                or r.find(f".//{W}vMerge") is not None for r in rows):
            continue
        dropped = 0
        while len(cols) > 1 and start + sum(cols) - width > cols[-1] / 2:
            cols.pop()
            grid.remove(grid[-1])
            for r in rows:
                r.remove(r.findall(W + "tc")[-1])
            dropped += 1
        if dropped:
            w = tbl.find(f"{W}tblPr/{W}tblW")
            if w is not None and w.get(W + "type") == "dxa":
                w.set(W + "w", str(sum(cols)))
            if warnings is not None:
                warnings.append(f"header/footer table wider than the page: {dropped} column(s) beyond the right margin dropped")


# ---- declarations of a base document -------------------------------------------------------------

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)([^}]*)\}\}")
DEFAULT_RE = re.compile(r"\|\s*default\(\s*(['\"])(.*?)\1")
FOR_RE = re.compile(r"\{%tr\s+for\s+([A-Za-z_]\w*)\s+in\s+([A-Za-z_]\w*)\s*%\}")


COL_RE = re.compile(r"[A-Za-z_]\w{0,39}")


def clean_rows(rows, cols=None) -> list | None:
    """Default rows of a dynamic table (attrs.dynamicRows from the editor): <= 1000 rows of <= 20 scalar columns with valid names, else None."""
    if not isinstance(rows, list) or len(rows) > 1000:
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict) or len(r) > 20 or not all(isinstance(k, str) and COL_RE.fullmatch(k) for k in r):
            return None
        out.append({k: "" if v is None else v if isinstance(v, (str, int, float)) and not isinstance(v, bool) else str(v) for k, v in r.items()})
    return out


def table_decls(content) -> list[dict]:
    """List-variable declarations (name, columns, default rows) for the dynamic tables of editor content that carry default rows."""
    out: dict = {}

    def rec(n, nested=False):
        if not isinstance(n, dict):
            return
        a = n.get("attrs") or {}
        if n.get("type") == "table" and not nested and isinstance(a.get("dynamicList"), str) and re.fullmatch(r"[A-Za-z_]\w*", a["dynamicList"]) and (rows := clean_rows(a.get("dynamicRows"))) is not None:
            cols = [c for c in (a.get("dynamicCols") or []) if isinstance(c, str) and COL_RE.fullmatch(c)][:20]
            cols += [k for r in rows for k in r if k not in cols][:max(20 - len(cols), 0)]
            out.setdefault(a["dynamicList"], dict(name=a["dynamicList"], type="list", columns=cols, default=rows))
        for c in n.get("content") or []:
            rec(c, nested or n.get("type") == "table")  # dynamic tables are top-level only
    rec(content)
    return list(out.values())


def parse_vars(roots: list, existing: list | None = None) -> list[dict]:
    """Variables a base document uses: `{{ name }}` text and `{%tr for x in list %}` table loops (columns from
    `{{ x.col }}`). Built-ins, CONTENT, SLOT and loops over built-in lists are skipped. Entries of `existing` (edited
    type/default) are kept for names that are still used."""
    names: dict = {}
    loops: dict = {}
    texts = [text(p) for r in roots for p in r.iter(W + "p")]
    for t in texts:
        for m in FOR_RE.finditer(t):
            loops[m[1]] = m[2]
    for t in texts:
        for m in VAR_RE.finditer(t):
            n = m[1]
            root, _, col = n.partition(".")
            if n in ("CONTENT", "SLOT") or root in BUILTIN_ROOTS or (root in loops and loops[root] in BUILTIN_ROOTS):
                continue
            if root in loops:
                lst = names.setdefault(loops[root], dict(name=loops[root], type="list", default=[], columns=[]))
                if col and col not in lst["columns"]:
                    lst["columns"].append(col)
            elif n not in names:
                d = DEFAULT_RE.search(m[2])  # `{{ x|default('y') }}` needs no value
                names[n] = dict(name=n, type="text", default=d and d[2])
    for lst in loops.values():
        if lst not in BUILTIN_ROOTS and lst not in names:
            names[lst] = dict(name=lst, type="list", default=[], columns=[])
    old = {d["name"]: d for d in existing or []}
    return [{**d, **{k: v for k, v in old.get(d["name"], {}).items() if k in ("type", "default", "columns")}}
            for d in names.values()]


# ---- packages -------------------------------------------------------------------------------------

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
HF = re.compile(r"word/(header|footer)\d*\.xml")


def parse(b: bytes):
    return etree.fromstring(b, _PARSER)


def hf_roots(data: bytes) -> dict:
    """{part name: root} of the header/footer parts of a (validated) DOCX."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: parse(z.read(n)) for n in z.namelist() if HF.fullmatch(n)}


def write_parts(data: bytes, roots: dict) -> bytes:
    """The package with the given parts replaced by the serialised roots (all other bytes untouched)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            raw = etree.tostring(roots[i.filename], xml_declaration=True, encoding="UTF-8", standalone=True) \
                if i.filename in roots else zin.read(i)
            zout.writestr(i, raw)
    return out.getvalue()


def package_vars(data: bytes, existing: list | None = None) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        body = parse(z.read("word/document.xml"))
    return parse_vars([body, *hf_roots(data).values()], existing)


def picks_decls(items) -> list[dict]:
    """Text variables for the picked names (built-ins are filled by the exporter and never declared); default = detected value."""
    return [dict(name=i.name, **(dict(type="date", default=d.isoformat()) if (d := parse_date(i.value)) else dict(
        type="text", default=None if i.value.startswith("Error!") else i.value)))  # Word's own error text is no default; dates: typed, stored ISO
            for i in items if i.name.split(".")[0] not in BUILTIN_ROOTS]
