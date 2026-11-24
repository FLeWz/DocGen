"""Direct formatting <-> small JSON dicts, shared by the DOCX importer (XML -> attrs), the exporter (attrs -> XML) and
the style CSS resolver. Values stay close to OOXML units (twips, half-points, eighths of a point) so a round trip is
lossless; everything read back from client JSON is validated here (hex colours, enums, int ranges), junk is dropped.

run   {b,i,u,strike,color,font,size,hl,va,caps,smallCaps,rStyle}      (b/i/u are marks, not part of the `fmt` mark)
para  {jc,left,right,first,before,after,line,lineRule,shd,bdr,keepNext,keepLines,pageBreakBefore,tabs,ctx (contextualSpacing),mark (paragraph-mark run props)}
      tabs = [{pos (twips), val: left|center|right|decimal|bar, leader: dot|hyphen|underscore|...}]
cell  {shd,vAlign,bdr,mar,dir}   tbl {jc,bdr,width,ind,mar,layout}   row {h: {val,rule}, cant, jc}
bdr = {top|left|bottom|right|insideH|insideV: {val,sz,color}}   mar = {top|left|bottom|right: twips}
"""
import re
import zipfile

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
HEX = re.compile(r"[0-9A-Fa-f]{6}")
FONT = re.compile(r"[A-Za-z0-9 ._()-]{1,60}")
STYLE_ID = re.compile(r"[A-Za-z0-9_-]{1,60}")
JC = {"left": "left", "start": "left", "right": "right", "end": "right", "center": "center", "both": "both",
      "distribute": "both", "justify": "both"}
BORDER = {"single", "double", "dotted", "dashed", "thick", "none", "nil"}
SIDES = ("top", "left", "bottom", "right")
HL = {"yellow", "green", "cyan", "magenta", "blue", "red", "darkBlue", "darkCyan", "darkGreen", "darkMagenta", "darkRed",
      "darkYellow", "darkGray", "lightGray", "black", "white"}
HL_HEX = {"yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF", "magenta": "FF00FF", "blue": "0000FF", "red": "FF0000",
          "darkBlue": "000080", "darkCyan": "008080", "darkGreen": "008000", "darkMagenta": "800080", "darkRed": "800000",
          "darkYellow": "808000", "darkGray": "808080", "lightGray": "C0C0C0", "black": "000000", "white": "FFFFFF"}
VALIGN = {"top", "center", "bottom"}

RPR_ORDER = ("rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish "
             "webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang "
             "eastAsianLayout specVanish oMath").split()
PPR_ORDER = ("pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs "
             "suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi adjustRightInd "
             "snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection textAlignment "
             "textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange").split()
TRPR_ORDER = "cnfStyle divId gridBefore gridAfter wBefore wAfter cantSplit trHeight tblHeader tblCellSpacing jc hidden".split()
TCPR_ORDER = ("cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar textDirection tcFitText vAlign hideMark").split()

loc = lambda e: e.tag.rpartition("}")[2] if isinstance(e.tag, str) else ""
val = lambda e, k="val": e.get(W + k) if e is not None else None


def _int(s, lo=-31680, hi=31680):
    try:
        n = int(s)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _on(e) -> bool:
    return e is not None and val(e) not in ("0", "false", "off")


# ---- theme -------------------------------------------------------------------------------------

class Theme:
    """Colour scheme + font scheme of word/theme/theme1.xml (all lookups return None when unknown)."""
    MAP = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2", "text1": "dk1", "background1": "lt1", "text2": "dk2",
           "background2": "lt2"}

    def __init__(self, root=None):
        self.colors, self.fonts = {}, {}
        if root is None:
            return
        A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        for c in root.iter(A + "clrScheme"):
            for ch in c:
                k = ch.tag.rpartition("}")[2]
                for v in ch:
                    hx = v.get("lastClr") or v.get("val")
                    if hx and HEX.fullmatch(hx):
                        self.colors[k] = hx.upper()
        for kind in ("major", "minor"):
            f = next(root.iter(A + kind + "Font"), None)
            lat = f.find(A + "latin") if f is not None else None
            if lat is not None and lat.get("typeface"):
                self.fonts[kind] = lat.get("typeface")

    def color(self, name, tint=None, shade=None):
        c = self.colors.get(self.MAP.get(name, name))
        if not c:
            return None
        rgb = [int(c[i:i + 2], 16) for i in (0, 2, 4)]
        if tint and re.fullmatch(r"[0-9A-Fa-f]{2}", tint):
            t = int(tint, 16) / 255
            rgb = [round(x * t + 255 * (1 - t)) for x in rgb]
        if shade and re.fullmatch(r"[0-9A-Fa-f]{2}", shade):
            rgb = [round(x * int(shade, 16) / 255) for x in rgb]
        return "%02X%02X%02X" % tuple(rgb)

    def font(self, name):
        return self.fonts.get("major" if name.startswith("major") else "minor") if name else None


def load_theme(zf: zipfile.ZipFile, parse) -> Theme:
    n = next((x for x in zf.namelist() if x.startswith("word/theme/") and x.endswith(".xml")), None)
    return Theme(parse(n)) if n else Theme()


def _color(e, theme):
    """w:color / w:shd attribute set -> 'RRGGBB' | None (auto)."""
    if e is None:
        return None
    shd = loc(e) == "shd"  # shading uses themeFill/themeFillTint/themeFillShade for its fill
    tc = e.get(W + ("themeFill" if shd else "themeColor"))
    if tc and theme:
        c = theme.color(tc, e.get(W + ("themeFillTint" if shd else "themeTint")), e.get(W + ("themeFillShade" if shd else "themeShade")))
        if c:
            return c
    v = e.get(W + "val") if loc(e) == "color" else e.get(W + "fill")
    return v.upper() if v and HEX.fullmatch(v) else None


# ---- XML -> dict ---------------------------------------------------------------------------------

RUN_HANDLED = {"b", "bCs", "i", "iCs", "u", "lang", "noProof", "szCs", "webHidden", "rFonts", "color", "sz", "highlight",
               "strike", "dstrike", "vertAlign", "caps", "smallCaps", "rStyle", "shd"}


def run_fmt(rpr, theme=None) -> tuple[dict, set]:
    """-> (props, unhandled tag names). b/i/u are included for style resolution; the importer maps them to marks."""
    if rpr is None:
        return {}, set()
    d: dict = {}
    for k in ("b", "i", "strike", "caps", "smallCaps"):
        e = rpr.find(W + k)
        if e is not None:
            d[k] = _on(e)
    e = rpr.find(W + "dstrike")
    if e is not None:
        d["strike"] = _on(e)
    u = rpr.find(W + "u")
    if u is not None:
        d["u"] = val(u) not in ("none", "0", None)
    c = _color(rpr.find(W + "color"), theme)
    if c:
        d["color"] = c
    rf = rpr.find(W + "rFonts")
    if rf is not None:
        f = rf.get(W + "ascii") or rf.get(W + "hAnsi") or (theme.font(rf.get(W + "asciiTheme") or rf.get(W + "hAnsiTheme") or "")
                                                          if theme else None)
        if f and FONT.fullmatch(f):
            d["font"] = f
    sz = _int(val(rpr.find(W + "sz")), 2, 3276)
    if sz:
        d["size"] = sz
    h = val(rpr.find(W + "highlight"))
    if h in HL:
        d["hl"] = HL_HEX[h]
    else:
        s = rpr.find(W + "shd")
        sc = _color(s, theme) if s is not None and val(s) not in ("nil", "clear") or (s is not None and s.get(W + "fill")) else None
        if sc:
            d["hl"] = sc
    va = val(rpr.find(W + "vertAlign"))
    if va in ("superscript", "subscript"):
        d["va"] = va
    rs = val(rpr.find(W + "rStyle"))
    if rs and STYLE_ID.fullmatch(rs):
        d["rStyle"] = rs
    return d, {loc(c) for c in rpr if loc(c) not in RUN_HANDLED}


def _border(e, theme=None):
    if e is None:
        return None
    v = val(e)
    out = {"val": v if v in BORDER else "single"}
    sz = _int(e.get(W + "sz"), 0, 96)
    out["sz"] = sz if sz is not None else 4
    c = e.get(W + "color")
    tc = e.get(W + "themeColor")
    c = (theme.color(tc, e.get(W + "themeTint"), e.get(W + "themeShade")) if tc and theme else None) or c
    if c and HEX.fullmatch(c):
        out["color"] = c.upper()
    return out


def _borders(parent, names, theme=None) -> dict:
    out = {}
    for n in names:
        b = _border(parent.find(W + n), theme) if parent is not None else None
        if not b and parent is not None and n in ("left", "right"):  # start/end are the bidi-aware spellings
            b = _border(parent.find(W + {"left": "start", "right": "end"}[n]), theme)
        if b:
            out[n] = b
    return out


def para_fmt(ppr, theme=None) -> tuple[dict, set]:
    if ppr is None:
        return {}, set()
    d: dict = {}
    jc = JC.get(val(ppr.find(W + "jc")) or "")
    if jc:
        d["jc"] = jc
    ind = ppr.find(W + "ind")
    if ind is not None:
        for k, a in (("left", "left"), ("left", "start"), ("right", "right"), ("right", "end")):
            n = _int(ind.get(W + a))
            if n is not None and k not in d:
                d[k] = n
        fl, hg = _int(ind.get(W + "firstLine")), _int(ind.get(W + "hanging"))
        if hg:
            d["first"] = -hg
        elif fl:
            d["first"] = fl
    sp = ppr.find(W + "spacing")
    if sp is not None:
        for k in ("before", "after", "line"):
            n = _int(sp.get(W + k), 0, 31680)
            if n is not None:
                d[k] = n
        if sp.get(W + "lineRule") in ("auto", "exact", "atLeast"):
            d["lineRule"] = sp.get(W + "lineRule")
    s = ppr.find(W + "shd")
    if s is not None and _color(s, theme):
        d["shd"] = _color(s, theme)
    bd = _borders(ppr.find(W + "pBdr"), SIDES, theme)
    if bd:
        d["bdr"] = bd
    for k in ("keepNext", "keepLines", "pageBreakBefore"):
        if _on(ppr.find(W + k)) and ppr.find(W + k) is not None:
            d[k] = True
    cs = ppr.find(W + "contextualSpacing")
    if cs is not None and _on(cs):
        d["ctx"] = True
    mark, _ = run_fmt(ppr.find(W + "rPr"), theme)  # paragraph-mark properties: size of empty lines, number/bullet look
    mark = {k: v for k, v in mark.items() if v not in (False, None) and k not in ("u", "rStyle")}
    if mark:
        d["mark"] = mark
    tabs = tab_stops(ppr.find(W + "tabs"))
    if tabs:
        d["tabs"] = tabs
    handled = {"pStyle", "numPr", "rPr", "tabs", "keepNext", "keepLines", "widowControl", "contextualSpacing", "pageBreakBefore",
               "jc", "ind", "spacing", "shd", "pBdr", "outlineLvl", "suppressAutoHyphens"}
    return d, {loc(c) for c in ppr if loc(c) not in handled}


TAB_VAL = {"left": "left", "start": "left", "center": "center", "right": "right", "end": "right", "decimal": "decimal", "bar": "bar"}
LEADER = {"dot", "hyphen", "underscore", "heavy", "middleDot"}


def tab_stops(tabs) -> list:
    out = []
    for t in (tabs if tabs is not None else []):
        pos, v = _int(t.get(W + "pos"), 0, 31680), t.get(W + "val")
        if pos is None or v == "clear" or v not in TAB_VAL:
            continue
        d = {"pos": pos, "val": TAB_VAL[v]}
        if t.get(W + "leader") in LEADER:
            d["leader"] = t.get(W + "leader")
        out.append(d)
    return sorted(out, key=lambda x: x["pos"])[:20]


def margins(e) -> dict:
    """tblCellMar / tcMar -> {top,left,bottom,right} twips (start/end are the bidi spellings of left/right)."""
    out = {}
    for k, alt in (("top", None), ("left", "start"), ("bottom", None), ("right", "end")):
        m = e.find(W + k) if e is not None else None
        if m is None and alt and e is not None:
            m = e.find(W + alt)
        n = _int(m.get(W + "w"), 0, 31680) if m is not None and m.get(W + "type", "dxa") == "dxa" else None
        if n is not None:
            out[k] = n
    return out


def cell_fmt(tcpr, theme=None) -> dict:
    if tcpr is None:
        return {}
    d: dict = {}
    s = tcpr.find(W + "shd")
    if s is not None and _color(s, theme):
        d["shd"] = _color(s, theme)
    va = val(tcpr.find(W + "vAlign"))
    if va in VALIGN:
        d["vAlign"] = va
    bd = _borders(tcpr.find(W + "tcBorders"), SIDES, theme)
    if bd:
        d["bdr"] = bd
    mar = margins(tcpr.find(W + "tcMar"))
    if mar:
        d["mar"] = mar
    td = val(tcpr.find(W + "textDirection"))
    if td in TEXT_DIR:
        d["dir"] = TEXT_DIR[td]
    return d


TEXT_DIR = {"btLr": "btLr", "tbRl": "tbRl", "lrTb": None, "tbRlV": "tbRl", "lrTbV": None, "tbLrV": "btLr"}


def tbl_fmt(tblpr, theme=None) -> dict:
    if tblpr is None:
        return {}
    d: dict = {}
    jc = JC.get(val(tblpr.find(W + "jc")) or "")
    if jc in ("left", "right", "center"):
        d["jc"] = jc
    bd = _borders(tblpr.find(W + "tblBorders"), SIDES + ("insideH", "insideV"), theme)
    if bd:
        d["bdr"] = bd
    w = tblpr.find(W + "tblW")
    if w is not None and _int(w.get(W + "w"), 0, 200000) and w.get(W + "type") in ("dxa", "pct"):
        d["width"] = {"w": int(w.get(W + "w")), "type": w.get(W + "type")}
    ind = tblpr.find(W + "tblInd")
    if ind is not None and _int(ind.get(W + "w"), -31680, 31680) is not None and ind.get(W + "type", "dxa") == "dxa":
        d["ind"] = int(ind.get(W + "w"))
    mar = margins(tblpr.find(W + "tblCellMar"))
    if mar:
        d["mar"] = mar
    if val(tblpr.find(W + "tblLayout"), "type") == "fixed":
        d["layout"] = "fixed"
    return d


def tbl_look(tblpr) -> dict:
    """tblLook -> which conditional formats apply (explicit attributes win over the hex bitmask `val`)."""
    e = tblpr.find(W + "tblLook") if tblpr is not None else None
    if e is None:
        return dict(firstRow=False, lastRow=False, firstCol=False, lastCol=False, hBand=True, vBand=True)
    bits = int(e.get(W + "val"), 16) if re.fullmatch(r"[0-9A-Fa-f]{1,4}", e.get(W + "val") or "") else None
    flag = lambda attr, bit, default: (e.get(W + attr) in ("1", "true", "on")) if e.get(W + attr) is not None else (bool(bits & bit) if bits is not None else default)
    return dict(firstRow=flag("firstRow", 0x20, False), lastRow=flag("lastRow", 0x40, False), firstCol=flag("firstColumn", 0x80, False),
                lastCol=flag("lastColumn", 0x100, False), hBand=not flag("noHBand", 0x200, False), vBand=not flag("noVBand", 0x400, False))


def row_fmt(trpr) -> dict:
    d: dict = {}
    h = trpr.find(W + "trHeight") if trpr is not None else None
    if h is not None and _int(h.get(W + "val"), 0, 31680):
        d["h"] = {"val": int(h.get(W + "val")), "rule": "exact" if h.get(W + "hRule") == "exact" else "atLeast"}
    if trpr is not None and trpr.find(W + "cantSplit") is not None and _on(trpr.find(W + "cantSplit")):
        d["cant"] = True
    j = trpr.find(W + "jc") if trpr is not None else None
    if j is not None and j.get(W + "val") in ("left", "center", "right"):
        d["jc"] = j.get(W + "val")
    return d


# ---- dict -> XML ---------------------------------------------------------------------------------

def insert_ordered(parent, child, order) -> None:
    """Insert `child` where the schema sequence `order` wants it (python-docx helpers insert correctly; raw appends don't)."""
    idx = {n: i for i, n in enumerate(order)}
    mine = idx.get(loc(child), len(order))
    for sib in parent:
        if idx.get(loc(sib), len(order)) > mine:
            sib.addprevious(child)
            return
    parent.append(child)


def _el(tag, **attrs):
    e = etree.Element(W + tag)
    for k, v in attrs.items():
        e.set(W + k, str(v))
    return e


def _set(parent, order, tag, **attrs):
    old = parent.find(W + tag)
    if old is not None:
        parent.remove(old)
    e = _el(tag, **attrs)
    insert_ordered(parent, e, order)
    return e


def _hex(v):
    return v.upper() if isinstance(v, str) and HEX.fullmatch(v) else None


def _write_borders(parent, order, tag, bd, names) -> None:
    if not isinstance(bd, dict):
        return
    box = _el(tag)
    for n in names:
        b = bd.get(n)
        if isinstance(b, dict):
            v = b.get("val") if b.get("val") in BORDER else "single"
            at = {"val": "nil" if v == "none" else v, "sz": _int(b.get("sz"), 0, 96) or 0, "space": 0,
                  "color": _hex(b.get("color")) or "auto"}
            box.append(_el(n, **at))
    if len(box):
        old = parent.find(W + tag)
        if old is not None:
            parent.remove(old)
        insert_ordered(parent, box, order)


def write_run(rpr, d: dict, has_style) -> None:
    """Apply {color,font,size,hl,strike,va,caps,smallCaps,rStyle} to a w:rPr element."""
    if not isinstance(d, dict):
        return
    if d.get("rStyle") and STYLE_ID.fullmatch(str(d["rStyle"])) and has_style(d["rStyle"]) and rpr.find(W + "rStyle") is None:
        _set(rpr, RPR_ORDER, "rStyle", val=d["rStyle"])
    if isinstance(d.get("font"), str) and FONT.fullmatch(d["font"]):
        _set(rpr, RPR_ORDER, "rFonts", ascii=d["font"], hAnsi=d["font"], cs=d["font"])
    for k in ("strike", "caps", "smallCaps"):
        if d.get(k) is True:
            _set(rpr, RPR_ORDER, k)
    if _hex(d.get("color")):
        _set(rpr, RPR_ORDER, "color", val=_hex(d["color"]))
    if _int(d.get("size"), 2, 3276):
        _set(rpr, RPR_ORDER, "sz", val=int(d["size"]))
        _set(rpr, RPR_ORDER, "szCs", val=int(d["size"]))
    if _hex(d.get("hl")):
        _set(rpr, RPR_ORDER, "shd", val="clear", color="auto", fill=_hex(d["hl"]))
    if d.get("va") in ("superscript", "subscript"):
        _set(rpr, RPR_ORDER, "vertAlign", val=d["va"])


def write_para(ppr, d: dict) -> None:
    if not isinstance(d, dict):
        return
    for k in ("keepNext", "keepLines", "pageBreakBefore"):
        if d.get(k) is True:
            _set(ppr, PPR_ORDER, k)
    if isinstance(d.get("tabs"), list):
        box = _el("tabs")
        for t in d["tabs"][:20]:
            if isinstance(t, dict) and _int(t.get("pos"), 0, 31680) is not None and t.get("val") in TAB_VAL.values():
                at = {"val": t["val"], "pos": int(t["pos"])}
                if t.get("leader") in LEADER:
                    at["leader"] = t["leader"]
                box.append(_el("tab", **at))
        if len(box):
            old = ppr.find(W + "tabs")
            if old is not None:
                ppr.remove(old)
            insert_ordered(ppr, box, PPR_ORDER)
    if d.get("ctx") is True:
        _set(ppr, PPR_ORDER, "contextualSpacing")
    if isinstance(d.get("mark"), dict) and d["mark"]:
        rpr = _set(ppr, PPR_ORDER, "rPr")
        for k in ("b", "i"):
            if d["mark"].get(k) is True:
                _set(rpr, RPR_ORDER, k)
        write_run(rpr, d["mark"], lambda s: False)
    _write_borders(ppr, PPR_ORDER, "pBdr", d.get("bdr"), SIDES)
    if _hex(d.get("shd")):
        _set(ppr, PPR_ORDER, "shd", val="clear", color="auto", fill=_hex(d["shd"]))
    sp = {k: n for k in ("before", "after", "line") if (n := _int(d.get(k), 0, 31680)) is not None}
    if sp:
        if d.get("lineRule") in ("auto", "exact", "atLeast") and "line" in sp:
            sp["lineRule"] = d["lineRule"]
        _set(ppr, PPR_ORDER, "spacing", **sp)
    ind = {}
    for k, a in (("left", "left"), ("right", "right")):
        if (n := _int(d.get(k))) is not None:
            ind[a] = n
    if (n := _int(d.get("first"))):
        ind["hanging" if n < 0 else "firstLine"] = abs(n)
    if ind:
        _set(ppr, PPR_ORDER, "ind", **ind)
    if d.get("jc") in JC:
        _set(ppr, PPR_ORDER, "jc", val=JC[d["jc"]])


def write_cell(tcpr, d: dict) -> None:
    if not isinstance(d, dict):
        return
    _write_borders(tcpr, TCPR_ORDER, "tcBorders", d.get("bdr"), SIDES)
    if _hex(d.get("shd")):
        _set(tcpr, TCPR_ORDER, "shd", val="clear", color="auto", fill=_hex(d["shd"]))
    if d.get("vAlign") in VALIGN:
        _set(tcpr, TCPR_ORDER, "vAlign", val=d["vAlign"])
    if d.get("dir") in ("btLr", "tbRl"):
        _set(tcpr, TCPR_ORDER, "textDirection", val=d["dir"])
    if isinstance(d.get("mar"), dict):
        box = _el("tcMar")
        for k in ("top", "left", "bottom", "right"):
            if (n := _int(d["mar"].get(k), 0, 31680)) is not None:
                box.append(_el(k, w=n, type="dxa"))
        if len(box):
            old = tcpr.find(W + "tcMar")
            if old is not None:
                tcpr.remove(old)
            insert_ordered(tcpr, box, TCPR_ORDER)


def write_row(trpr, d: dict) -> None:
    if isinstance(d, dict) and isinstance(d.get("h"), dict) and (n := _int(d["h"].get("val"), 1, 31680)):
        old = trpr.find(W + "trHeight")
        if old is not None:
            trpr.remove(old)
        insert_ordered(trpr, _el("trHeight", val=n, hRule="exact" if d["h"].get("rule") == "exact" else "atLeast"), TRPR_ORDER)
    if isinstance(d, dict) and d.get("jc") in ("left", "center", "right") and trpr.find(W + "jc") is None:
        insert_ordered(trpr, _el("jc", val=d["jc"]), TRPR_ORDER)
    if isinstance(d, dict) and d.get("cant") is True and trpr.find(W + "cantSplit") is None:
        insert_ordered(trpr, _el("cantSplit"), TRPR_ORDER)
