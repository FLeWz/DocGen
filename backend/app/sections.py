"""Revision-table `sections` (rounds 16, 22): the numbers of what changed, nothing else.

A flat list of top-level nodes (the BUILT document in order, or one snippet) gets one anchor per node: the number Word shows
for the heading it is (or sits under), or label + SEQ number of the table/figure caption it is part of ('Table 4').
Two versions are diffed block by block (JSON hash); every changed block reports its anchor once, in document order.
Unnumbered headings, parts before the first heading and uncaptioned tables have no number and are omitted."""
import difflib
import io
import json
import re
import zipfile

from lxml import etree

from .docx_rich import seq_label, seq_numbers, _walk

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CAP = 12  # entries per row, then '+N more'


def _x(z: zipfile.ZipFile, name: str):
    try:
        return etree.fromstring(z.read(name), etree.XMLParser(resolve_entities=False, no_network=True))
    except KeyError:
        return None


class Numbering:
    """Heading numbering of a base document: decimal multi-level lists from numbering.xml linked to the heading styles.
    shortcut: every level is rendered as decimal (A./i. formats and custom lvlText are not reproduced)."""

    def __init__(self, docx: bytes | None = None):
        self.styles, self.abstract, self.nums = {}, {}, {}
        self.c: dict = {}
        if not docx:
            return
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            st, nm = _x(z, "word/styles.xml"), _x(z, "word/numbering.xml")
        for s in (st.iter(W + "style") if st is not None else []):
            b = s.find(W + "basedOn")
            n = s.find(f"{W}pPr/{W}numPr")
            self.styles[s.get(W + "styleId")] = dict(
                based=b is not None and b.get(W + "val"),
                num=n is not None and (n.find(W + "numId").get(W + "val") if n.find(W + "numId") is not None else None),
                ilvl=n is not None and n.find(W + "ilvl") is not None and int(n.find(W + "ilvl").get(W + "val")))
        for a in (nm.iter(W + "abstractNum") if nm is not None else []):
            lv = {}
            for l in a.findall(W + "lvl"):
                g = lambda t: (l.find(W + t).get(W + "val") if l.find(W + t) is not None else None)
                lv[int(l.get(W + "ilvl"))] = dict(start=int(g("start") or 1), restart=g("lvlRestart"), style=g("pStyle"), fmt=g("numFmt"))
            self.abstract[a.get(W + "abstractNumId")] = lv
        for n in (nm.iter(W + "num") if nm is not None else []):
            over = {int(o.get(W + "ilvl")): int(o.find(W + "startOverride").get(W + "val"))
                    for o in n.findall(W + "lvlOverride") if o.find(W + "startOverride") is not None}
            self.nums[n.get(W + "numId")] = (n.find(W + "abstractNumId").get(W + "val"), over)

    def _style_list(self, sid: str, level: int):
        """(numId, ilvl) of the list a style belongs to (own numPr or an ancestor's), None = not numbered."""
        seen, cur = set(), sid
        while cur and cur in self.styles and cur not in seen:
            seen.add(cur)
            s = self.styles[cur]
            if s["num"] is not None and s["num"] is not False:
                if s["num"] == "0":
                    return None
                aid = self.nums.get(s["num"], (None, {}))[0]
                lv = self.abstract.get(aid, {})
                if lv and all(v["fmt"] in ("none", "bullet") for v in lv.values()):
                    return None
                linked = next((i for i, v in lv.items() if v["style"] == cur), None)  # the level that names this style
                return s["num"], linked if linked is not None else s["ilvl"] if s["ilvl"] is not False else level - 1
            cur = s["based"]
        return None

    def heading(self, n: dict):
        """(list key, ilvl) when this heading shows a number, else None."""
        a = n.get("attrs") or {}
        level = min(max(int(a.get("level") or 1), 1), 9)
        if a.get("_blk"):  # builder heading block: its `numbered` flag decides; the base's own style numbering wins over ours
            if not a.get("numbered"):
                return None
            return self._style_list(a.get("styleId") or f"Heading{level}", level) or ("own", level - 1)
        if a.get("_numpr") is not None:  # base document paragraph with direct numPr
            return (a["_numpr"], level - 1) if a["_numpr"] != "0" else None
        return self._style_list(a.get("styleId") or f"Heading{min(level, 4)}", min(level, 4))

    def _lvl(self, key, ilvl: int) -> dict:
        if key == "own":
            return dict(start=1, restart=None)
        aid, over = self.nums.get(key, (None, {}))
        lv = dict(self.abstract.get(aid, {}).get(ilvl) or dict(start=1, restart=None))
        lv["start"] = over.get(ilvl, lv["start"])
        return lv

    def next(self, key, ilvl: int) -> str:
        c = self.c.setdefault(key, {})
        c[ilvl] = c[ilvl] + 1 if ilvl in c else self._lvl(key, ilvl)["start"]
        for j in [j for j in c if j > ilvl]:  # deeper levels restart (unless their lvlRestart says otherwise)
            r = self._lvl(key, j)["restart"]
            if r is None or (r != "0" and ilvl + 1 <= int(r)):
                del c[j]
        return ".".join(str(c.get(i, self._lvl(key, i)["start"])) for i in range(ilvl + 1))


def _cap(n: dict) -> str | None:
    """SEQ label of a caption paragraph ('Table', 'Figure', ...)."""
    if n.get("type") != "paragraph":
        return None
    f = next((x for x in _walk(n) if x.get("type") == "field" and (x.get("attrs") or {}).get("code") == "SEQ"), None)
    return f and seq_label((f["attrs"] or {}).get("arg"))


def anchors(nodes: list[dict], titles: list[str], num: Numbering) -> list[dict]:
    """One dict per node: `own` = what a change of this very node is reported as, `sec` = its section (the heading
    or the snippet title) for blocks that are not headings/tables/figures. Entry = dict(key, label, pos)."""
    num.c = {}
    seq = seq_numbers(nodes)
    cap = {}  # index of a table/image -> index of its caption paragraph
    for i, n in enumerate(nodes):
        lab = _cap(n)
        if not lab:
            continue
        want = "table" if lab == "Table" else "image"
        for j in ((i + 1, i - 1) if want == "table" else (i - 1, i + 1)):  # tables: caption above; figures: below
            if 0 <= j < len(nodes) and nodes[j].get("type") == want and j not in cap:
                cap[j] = i
                break
    grp, capi = {}, {}  # node index -> group start / caption index, for the table or figure and for its caption paragraph
    for j, c in cap.items():
        grp[j] = grp[c] = min(j, c)
        capi[j] = capi[c] = c
    for c, n in enumerate(nodes):
        if _cap(n) and c not in grp:  # a caption with nothing to caption stands for itself
            grp[c] = capi[c] = c
    out, sec = [], None  # sec: the entry of the heading in force (label None = unnumbered or before the first heading)
    for i, n in enumerate(nodes):
        if n.get("type") == "heading":
            h = num.heading(n)
            e = dict(key=("h", i), label=num.next(*h) if h else None, pos=i)
            sec = e
            out.append(dict(own=e, sec=e, kind="h"))
            continue
        s = sec or dict(key=("t", None), label=None, pos=i)
        if i in capi:
            f = next(x for x in _walk(nodes[capi[i]]) if x.get("type") == "field" and (x.get("attrs") or {}).get("code") == "SEQ")
            own = dict(key=("c", grp[i]), label=f"{_cap(nodes[capi[i]])} {seq[id(f)]}", pos=grp[i])
        elif n.get("type") == "table":  # a table without caption has no number to report
            own = dict(key=("c", i), label=None, pos=i)
        else:
            own = None
        out.append(dict(own=own or s, sec=s, kind="c" if own else "s"))
    return out


def _hash(nodes):
    return [json.dumps(n, sort_keys=True) for n in nodes]


def changes(old: list[dict], new: list[dict], new_a: list[dict], old_a: list[dict], offset: int = 0) -> str:
    """', '-joined numbers (`3.2.1`, `Table 4`) for the blocks of `new` that differ from `old` (new_a/old_a = anchors of the two lists;
    `offset` shifts positions when `new` is a slice of a bigger built document)."""
    hits: dict = {}

    def hit(e: dict, pos=None):
        if e["label"] and e["key"] not in hits:
            hits[e["key"]] = (offset + (e["pos"] if pos is None else pos), e["label"])

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, _hash(old), _hash(new), autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        k = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for x in range(j1, j2):
            a = new_a[x]
            hit(a["own"])
        for y in range(i1 + k, i2):
            a = old_a[y]
            if a["kind"] in ("h", "c"):  # removed: the number it had in the old revision
                hit(dict(a["own"], key=("rm", y)), j1 - 0.5)
            else:  # a paragraph that vanished: it belonged to the section of its new neighbour
                ref = new_a[j1 - 1] if j1 > 0 else new_a[j1] if j1 < len(new_a) else a
                hit(ref["sec"])
    rows = sorted(hits.values(), key=lambda h: (h[0], h[1]))
    out = list(dict.fromkeys(label for _, label in rows))
    return ", ".join(out[:CAP] + ([f"+{len(out) - CAP} more"] if len(out) > CAP else []))


def diff_nodes(old: list[dict] | None, new: list[dict], titles_old, titles_new, num: Numbering) -> str:
    """Changes between two node lists, numbered by `num` (each list on its own)."""
    old = old or []
    return changes(old, new, anchors(new, titles_new, num), anchors(old, titles_old, num))


def snippet_changes(old: dict | None, new: dict, num: Numbering | None = None) -> str:
    """Snippet versions on their own: numbers relative to the snippet, and only when `num` knows a numbering."""
    on = (old or {}).get("content", {}).get("content") or []
    nn = new["content"].get("content") or []
    return diff_nodes(on, nn, [(old or {}).get("title", "")] * len(on), [new["title"]] * len(nn), num or Numbering())
