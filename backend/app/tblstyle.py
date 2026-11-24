"""Effective formatting of table cells: table style (basedOn chain, conditional formats chosen by tblLook), direct table
properties and direct cell properties folded into one explicit dict per cell, so the editor/preview need no style engine.

eff = {bdr: {top,left,bottom,right}, shd, vAlign, mar, dir, r (run props: b,i,color,size,font...), p (jc only)}

Order of precedence, lowest first: style wholeTable -> band1/2Vert -> band1/2Horz -> lastCol -> firstCol -> lastRow ->
firstRow -> corner cells; then direct tblBorders/tblCellMar, then direct tcPr. The exporter never writes `eff` back (it
is derived); direct properties travel in `cfmt` and the verbatim `tblPr`.
"""
from . import fmt

SIDES = fmt.SIDES


class TableEff:
    def __init__(self, look, tblpr, style_id=None):
        self.look = look
        sid = style_id if style_id in look.styles and look.styles[style_id]["type"] == "table" else look.default_t
        self.chain = look.chain(sid) if sid else []
        self.flags = fmt.tbl_look(tblpr)
        self.direct = fmt.tbl_fmt(tblpr, look.theme)
        self.nrows = self.ncols = 0

    def table_fmt(self) -> dict:
        """jc/ind/layout/width/borders of the table: style first, direct on top (used for the table element itself)."""
        t: dict = {}
        for st in self.chain:
            t = _merge(t, st["tbl"])
        return _merge(t, self.direct)

    def _conds(self, ri, ci, rs, cs) -> list:
        f, nr, nc = self.flags, self.nrows, self.ncols
        last_row, last_col = ri + rs >= nr, ci + cs >= nc
        first_row, first_col = ri == 0, ci == 0
        out = []
        h = ri - (1 if f["firstRow"] else 0)  # banding starts after the header row / first column
        v = ci - (1 if f["firstCol"] else 0)
        if f["vBand"] and not (f["firstCol"] and first_col) and not (f["lastCol"] and last_col):
            out.append("band1Vert" if v % 2 == 0 else "band2Vert")
        if f["hBand"] and not (f["firstRow"] and first_row) and not (f["lastRow"] and last_row):
            out.append("band1Horz" if h % 2 == 0 else "band2Horz")
        if f["lastCol"] and last_col:
            out.append("lastCol")
        if f["firstCol"] and first_col:
            out.append("firstCol")
        if f["lastRow"] and last_row:
            out.append("lastRow")
        if f["firstRow"] and first_row:
            out.append("firstRow")
        for on, name in ((f["firstRow"] and f["lastCol"] and first_row and last_col, "neCell"),
                         (f["firstRow"] and f["firstCol"] and first_row and first_col, "nwCell"),
                         (f["lastRow"] and f["lastCol"] and last_row and last_col, "seCell"),
                         (f["lastRow"] and f["firstCol"] and last_row and first_col, "swCell")):
            if on:
                out.append(name)
        return out

    def cell(self, ri, ci, rs, cs, tcpr) -> dict:
        """Effective props of the cell at row `ri`, grid column `ci`, spanning `rs` rows and `cs` columns."""
        props, r, p, tbd, tmar = {}, {}, {}, {}, {}
        conds = self._conds(ri, ci, rs, cs)
        for st in self.chain:
            tbd, tmar = {**tbd, **st["tbl"].get("bdr", {})}, {**tmar, **st["tbl"].get("mar", {})}
            props, r, p = _merge(props, st["tcp"]), _merge(r, st["r"]), _merge(p, st["p"])
            for c in conds:
                cd = st["cond"].get(c)
                if not cd:
                    continue
                props = _merge(props, _merge(cd["tcp"], {k: v for k, v in ((k, cd["tbl"].get(k)) for k in ("bdr", "mar")) if v}))
                r, p = _merge(r, cd["r"]), _merge(p, cd["p"])
        dbd = self.direct.get("bdr", {})
        tmar = {**tmar, **self.direct.get("mar", {})}
        direct = fmt.cell_fmt(tcpr, self.look.theme)
        edge = {"top": ri == 0, "bottom": ri + rs >= self.nrows, "left": ci == 0, "right": ci + cs >= self.ncols}
        bdr = {}
        for s in SIDES:
            inside = "insideH" if s in ("top", "bottom") else "insideV"
            key = s if edge[s] else inside
            b = tbd.get(key)
            b = (props.get("bdr") or {}).get(s, b)  # style cell borders (also conditional ones)
            b = dbd.get(key, b)
            b = (direct.get("bdr") or {}).get(s, b)
            if b:
                bdr[s] = b
        out: dict = {"bdr": bdr} if bdr else {}
        mar = {**tmar, **(props.get("mar") or {}), **(direct.get("mar") or {})}
        if mar:
            out["mar"] = mar
        for k in ("shd", "vAlign", "dir"):
            v = direct.get(k) or props.get(k)
            if v:
                out[k] = v
        rr = {k: v for k, v in r.items() if k in ("b", "i", "u", "strike", "color", "size", "font", "caps", "smallCaps") and v not in (None, False)}
        if rr:
            out["r"] = rr
        if p.get("jc"):
            out["p"] = {"jc": p["jc"]}
        return out


def _merge(base: dict, over: dict) -> dict:
    out = {**base, **over}
    for k in ("bdr", "mar"):
        if isinstance(base.get(k), dict) and isinstance(over.get(k), dict):
            out[k] = {**base[k], **over[k]}
    return out
