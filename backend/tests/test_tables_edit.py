"""Round 8: tables the editor changed (re-serialised) must stay valid OOXML: tblGrid sums, tcW, gridSpan, vMerge, header rows,
table properties; python-docx opens them and LibreOffice (Gotenberg) lays the cells out where the grid says."""
import io

import httpx
import pytest
from docx import Document
from docx.oxml.ns import qn
from pypdf import PdfReader

from app import config, fmt
from .test_rich import P, T, body, export

TW = lambda *a: qn("w:" + a[0])


def cell(text, span=1, rows=1, cw=None, hdr=False, **cf):
    a = {"colspan": span, "rowspan": rows, "colwidth": cw}
    if cf:
        a["cfmt"] = cf
    return {"type": "tableHeader" if hdr else "tableCell", "attrs": a, "content": [P(T(text))]}


def table(rows, grid=None, **attrs):
    return {"type": "table", "attrs": {"grid": grid, **attrs},
            "content": [{"type": "tableRow", **({"attrs": {"rfmt": rf}} if rf else {}), "content": cs} for cs, rf in rows]}


def tbl_of(data):
    return body(export_bytes(data)).find(qn("w:tbl")) if isinstance(data, list) else body(data).find(qn("w:tbl"))


def export_bytes(blocks):
    return export(blocks)[0]


def grid_of(t):
    return [int(g.get(qn("w:w"))) for g in t.find(qn("w:tblGrid"))]


def test_grid_sums_tcw_and_spans_after_edit():
    # 3 columns, header row, a row with a 2-wide cell and a vertical merge over rows 1-2 of column 0
    t = table([([cell("A", hdr=True), cell("B", hdr=True), cell("C", hdr=True)], None),
               ([cell("m", rows=2), cell("x", span=2)], {"h": {"val": 600, "rule": "exact"}, "cant": True}),
               ([cell("y"), cell("z")], None)], grid=[2000, 3000, 4000],
              tfmt={"width": {"type": "dxa", "w": 9000}, "layout": "fixed", "jc": "center", "ind": 120})
    data = export_bytes([t])
    tb = tbl_of(data)
    assert grid_of(tb) == [2000, 3000, 4000]
    tcw = [[int(c.find(qn("w:tcPr")).find(qn("w:tcW")).get(qn("w:w"))) for c in tr.findall(qn("w:tc"))] for tr in tb.findall(qn("w:tr"))]
    assert tcw[0] == [2000, 3000, 4000] and tcw[1][0] == 2000 and tcw[1][1] == 7000 and tcw[2] == [2000, 3000, 4000]
    rows = tb.findall(qn("w:tr"))
    spans = [c.find(qn("w:tcPr")).find(qn("w:gridSpan")) for c in rows[1].findall(qn("w:tc"))]
    assert spans[0] is None and spans[1].get(qn("w:val")) == "2"
    vm = [c.find(qn("w:tcPr")).find(qn("w:vMerge")) for r in rows[1:] for c in r.findall(qn("w:tc"))[:1]]
    assert vm[0].get(qn("w:val")) == "restart" and vm[1] is not None and vm[1].get(qn("w:val")) is None
    # a continuation cell after a vMerge still has a gridCol slot: row 2 has the merged cell + y (col 1) + z (col 2)
    assert len(rows[2].findall(qn("w:tc"))) == 3
    pr = tb.find(qn("w:tblPr"))
    assert pr.find(qn("w:tblW")).get(qn("w:w")) == "9000" and pr.find(qn("w:tblW")).get(qn("w:type")) == "dxa"
    assert pr.find(qn("w:tblLayout")).get(qn("w:type")) == "fixed" and pr.find(qn("w:jc")).get(qn("w:val")) == "center"
    assert pr.find(qn("w:tblInd")).get(qn("w:w")) == "120"
    assert rows[0].find(qn("w:trPr")).find(qn("w:tblHeader")) is not None and rows[1].find(qn("w:trPr")).find(qn("w:tblHeader")) is None
    trp = rows[1].find(qn("w:trPr"))
    assert trp.find(qn("w:cantSplit")) is not None and trp.find(qn("w:trHeight")).get(qn("w:hRule")) == "exact"
    # schema order inside tblPr
    names = [c.tag.split("}")[1] for c in pr]
    assert names == sorted(names, key=fmt.TBLPR_ORDER.index)
    d = Document(io.BytesIO(data))
    assert [c.text for c in d.tables[0].rows[0].cells] == ["A", "B", "C"]


def test_stale_grid_is_rebuilt_from_cell_widths():
    # a column was inserted: the table kept a 2-entry grid, cells carry px widths (15 twips each)
    t = table([([cell("a", cw=[100]), cell("b", cw=[200]), cell("c", cw=[300])], None)], grid=[1500, 3000])
    tb = tbl_of(export_bytes([t]))
    assert grid_of(tb) == [1500, 3000, 4500]
    assert tb.find(qn("w:tr")).find(qn("w:tc")).find(qn("w:tcPr")).find(qn("w:tcW")).get(qn("w:w")) == "1500"


def test_tblw_dxa_follows_the_grid_but_a_user_width_wins():
    pr = '<w:tblPr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:tblW w:w="5000" w:type="dxa"/></w:tblPr>'
    t = table([([cell("a"), cell("b")], None)], grid=[3000, 3000], tblPr=pr)
    assert tbl_of(export_bytes([t])).find(qn("w:tblPr")).find(qn("w:tblW")).get(qn("w:w")) == "6000"
    t["attrs"]["tfmt"] = {"width": {"type": "pct", "w": 2500}}
    w = tbl_of(export_bytes([t])).find(qn("w:tblPr")).find(qn("w:tblW"))
    assert (w.get(qn("w:w")), w.get(qn("w:type"))) == ("2500", "pct")
    t["attrs"]["tfmt"] = {"width": {"type": "auto", "w": 0}, "layout": "autofit"}
    pr2 = tbl_of(export_bytes([t])).find(qn("w:tblPr"))
    assert pr2.find(qn("w:tblW")).get(qn("w:type")) == "auto" and pr2.find(qn("w:tblLayout")) is None


def test_cell_margins_and_borders_written():
    t = table([([cell("a"), cell("b")], None)], grid=[3000, 3000],
              tfmt={"mar": {"top": 50, "left": 100, "bottom": 50, "right": 100}, "bdr": {"top": {"val": "single", "sz": 8, "color": "FF0000"}, "insideV": {"val": "none"}}})
    pr = tbl_of(export_bytes([t])).find(qn("w:tblPr"))
    assert pr.find(qn("w:tblCellMar")).find(qn("w:left")).get(qn("w:w")) == "100"
    assert pr.find(qn("w:tblBorders")).find(qn("w:top")).get(qn("w:color")) == "FF0000"
    assert pr.find(qn("w:tblBorders")).find(qn("w:insideV")).get(qn("w:val")) == "nil"


def _gotenberg():
    try:
        httpx.get(config.PDF_URL, timeout=3)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _gotenberg(), reason="Gotenberg not reachable")
def test_libreoffice_places_cells_where_the_grid_says():
    t = table([([cell("AAA", cw=None), cell("BBB"), cell("CCC")], None), ([cell("merged", span=2), cell("ddd")], None)], grid=[2000, 4000, 3000],
              tfmt={"layout": "fixed", "width": {"type": "dxa", "w": 9000}})
    data = export_bytes([P(T("before")), t])
    r = httpx.post(f"{config.PDF_URL}/forms/libreoffice/convert", files={"files": ("t.docx", data)}, timeout=60)
    assert r.status_code == 200
    xs = {}
    PdfReader(io.BytesIO(r.content)).pages[0].extract_text(visitor_text=lambda text, cm, tm, fd, fs: xs.setdefault(text.strip(), tm[4] + cm[4]) if text.strip() else None)
    # column 2 starts 2000 twips (100pt) right of column 1, column 3 another 4000 twips (200pt)
    assert xs["BBB"] - xs["AAA"] == pytest.approx(100, abs=2) and xs["CCC"] - xs["BBB"] == pytest.approx(200, abs=2)
    assert xs["ddd"] == pytest.approx(xs["CCC"], abs=2) and xs["merged"] == pytest.approx(xs["AAA"], abs=2)
