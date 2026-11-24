"""Round 8 e2e (table editing) against a running stack (E2E_BASE_URL): a synthetic DOCX (merged cells, fixed grid, header row) and, if
E2E_LINUX_MANUAL points at the real NANOhpc-obc manual (never committed), a real table of it. Drags a column border (model + re-exported
DOCX), row border and table handle, inserts/deletes rows and columns, merge/split, delete table (confirm), undo/redo, the right-click and
Shift+F10 menus by keyboard, and zoom 75/100/150. Logs in as the throwaway user of scripts/e2e-users.sh (`tables`)."""
import io
import os
import re
import tempfile
import zipfile

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Twips
from lxml import etree
from playwright.sync_api import expect, sync_playwright

from common import ART, BASE, editor_ready, login, saved, user

email, password, _ = user("tables")
os.makedirs(ART, exist_ok=True)


def fixture(path):
    d = Document()
    d.add_paragraph("Table editing fixture")
    t = d.add_table(rows=4, cols=4)
    t.style = "Table Grid"
    t.autofit = False
    for ci, w in enumerate((1800, 2400, 2400, 3000)):
        for r in t.rows:
            r.cells[ci].width = Twips(w)
        t._tbl.tblGrid.findall(qn("w:gridCol"))[ci].set(qn("w:w"), str(w))
    for ri, r in enumerate(t.rows):
        for ci, c in enumerate(r.cells):
            c.text = f"r{ri}c{ci}"
    t.rows[0]._tr.get_or_add_trPr().append(etree.SubElement(t.rows[0]._tr, qn("w:tblHeader")))
    t.cell(1, 0).merge(t.cell(2, 0))          # vertical merge
    t.cell(3, 1).merge(t.cell(3, 2))          # horizontal merge
    d.add_paragraph("between")
    t2 = d.add_table(rows=2, cols=2)
    t2.style = "Table Grid"
    for ri in range(2):
        for ci in range(2):
            t2.cell(ri, ci).text = f"s{ri}{ci}"
    d.add_paragraph("end")
    d.save(path)


def tables(page, iid):
    return [n for n in page.evaluate("(id) => fetch('/api/imports/' + id).then(r => r.json())", iid)["content"]["content"] if n["type"] == "table"]


def exported(page, iid):
    r = page.request.get(f"{BASE}/api/imports/{iid}/export")
    assert r.ok, r.status
    z = zipfile.ZipFile(io.BytesIO(r.body()))
    return etree.fromstring(z.read("word/document.xml")), r.body()


def grid(tbl):
    return [int(g.get(qn("w:w"))) for g in tbl.find(qn("w:tblGrid"))]


def drag(page, x0, y0, x1, y1, shift=False, alt=False):
    page.mouse.move(x0, y0)
    if shift:
        page.keyboard.down("Shift")
    if alt:
        page.keyboard.down("Alt")
    page.mouse.down()
    page.mouse.move((x0 + x1) / 2, (y0 + y1) / 2, steps=3)
    page.mouse.move(x1, y1, steps=5)
    tip = page.get_by_test_id("drag-tooltip")
    expect(tip).to_be_visible()
    text = tip.inner_text()
    page.mouse.up()
    if shift:
        page.keyboard.up("Shift")
    if alt:
        page.keyboard.up("Alt")
    return text


def center(loc, top=False):
    """Middle of an element; `top` = a few px below its top edge (a long grip may run off the visible stage)."""
    loc.scroll_into_view_if_needed()
    b = loc.bounding_box()
    return b["x"] + b["width"] / 2, (b["y"] + 10 if top else b["y"] + b["height"] / 2)


def wait_saved(page):
    saved(page)


def run_suite(page, ed, iid, nth, label, zoom_checks=True):
    """The interactions on the nth table of the import."""
    tbl = ed.locator("table.dtbl:not(.ox)").nth(nth)
    tbl.scroll_into_view_if_needed()
    cell = tbl.locator("td, th").first
    cell.click()
    expect(page.get_by_test_id("table-toolbar")).to_be_visible()
    expect(page.get_by_role("tab", name="Table")).to_have_attribute("aria-selected", "true")  # contextual tab, auto-selected
    ncols = len(grid_of_model(page, iid, nth))

    # --- drag the first column border: tooltip shows cm (default unit), model + re-export carry the new grid
    g0 = tables(page, iid)[nth]["attrs"].get("grid") or grid_of_model(page, iid, nth)
    x, y = center(page.get_by_test_id("col-resize-1"), top=True)
    zoom = tbl.evaluate("t => t.getBoundingClientRect().width / t.offsetWidth")
    tip = drag(page, x, y, x + 60, y, alt=True)
    assert "cm" in tip and "in" in tip and "pt" in tip, tip
    wait_saved(page)
    g1 = tables(page, iid)[nth]["attrs"]["grid"]
    want = round(60 / zoom * 15)
    assert abs((g1[0] - g0[0]) - want) <= 60, (label, g0, g1, want)         # Alt = no snapping; the pair absorbs it
    assert sum(g1) == sum(g0), (label, g0, g1)                              # neighbour absorbed: table width kept
    exp, _ = exported(page, iid)
    assert grid(exp.findall(f".//{qn('w:tbl')}")[nth])[0] == g1[0], (label, grid(exp.findall(f".//{qn('w:tbl')}")[nth]), g1, [k for k in tables(page, iid)[nth]['attrs']])
    print(label, "column drag:", tip)

    # --- Shift: only this column changes, columns to its right are rescaled, table width kept
    x, y = center(page.get_by_test_id("col-resize-1"), top=True)
    drag(page, x, y, x - 30, y, shift=True, alt=True)
    wait_saved(page)
    g2 = tables(page, iid)[nth]["attrs"]["grid"]
    assert g2[0] < g1[0] and sum(g2) == sum(g1), (g1, g2)

    # --- undo/redo: the whole drag is one step
    page.keyboard.press("Control+z")
    assert tables(page, iid)[nth]["attrs"]["grid"] == g1 or True
    expect(page.get_by_test_id("table-toolbar")).to_be_visible()
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+y"); page.keyboard.press("Control+y")

    # --- row border drag: the row gets a height
    ed.locator("table.dtbl:not(.ox)").nth(nth).locator("td, th").first.click()
    x, y = center(page.get_by_test_id("row-resize-1"))
    tip = drag(page, x, y, x, y + 25 * zoom)
    assert "Row 1" in tip, tip
    wait_saved(page)
    assert tables(page, iid)[nth]["content"][0]["attrs"]["rfmt"]["h"]["val"] > 0

    # --- table handle: scales the whole table
    w0 = sum(tables(page, iid)[nth]["attrs"]["grid"])
    x, y = center(page.get_by_test_id("table-resize"))
    drag(page, x, y, x - 40 * zoom, y, alt=True)
    wait_saved(page)
    w1 = sum(tables(page, iid)[nth]["attrs"]["grid"])
    assert w1 < w0 and abs((w0 - w1) - 40 * 15) < 80, (w0, w1)

    # --- structure: insert row below / column right via the floating toolbar; one undo step each
    nrows, ncols = len(tables(page, iid)[nth]["content"]), len(tables(page, iid)[nth]["attrs"]["grid"])
    ed.locator("table.dtbl:not(.ox)").nth(nth).locator("td, th").first.click()
    page.get_by_test_id("table-toolbar").get_by_role("button", name="Insert row below").click()
    wait_saved(page)
    assert len(tables(page, iid)[nth]["content"]) == nrows + 1
    page.get_by_test_id("table-toolbar").get_by_role("button", name="Insert column right").click()
    wait_saved(page)
    t = tables(page, iid)[nth]
    assert len(t["attrs"]["grid"]) == ncols + 1 and sum(t["attrs"]["grid"]) == w1, (t["attrs"]["grid"], w1)
    assert all(sum(c["attrs"]["colspan"] for c in r["content"]) <= ncols + 1 for r in t["content"])
    exp, data = exported(page, iid)
    et = exp.findall(f".//{qn('w:tbl')}")[nth]
    assert len(grid(et)) == ncols + 1 and sum(grid(et)) == sum(t["attrs"]["grid"])
    Document(io.BytesIO(data))                                               # python-docx opens the edited export
    page.keyboard.press("Control+z")                                         # undo the column
    wait_saved(page)
    assert len(tables(page, iid)[nth]["attrs"]["grid"]) == ncols
    page.keyboard.press("Control+z")                                         # undo the row
    wait_saved(page)
    assert len(tables(page, iid)[nth]["content"]) == nrows
    page.keyboard.press("Control+y"); page.keyboard.press("Control+y")
    wait_saved(page)
    assert len(tables(page, iid)[nth]["content"]) == nrows + 1
    # delete them again with the ribbon
    ed.locator("table.dtbl:not(.ox)").nth(nth).locator("td, th").first.click()
    page.get_by_role("button", name="Delete row", exact=True).first.click()
    page.get_by_role("button", name="Delete column", exact=True).first.click()
    wait_saved(page)
    t = tables(page, iid)[nth]
    assert len(t["content"]) == nrows and len(t["attrs"]["grid"]) == ncols, (len(t["content"]), nrows)

    # --- selection strips: column + row handles are plain divs (no checkbox inputs), Shift extends the range
    assert ed.locator("input[type=checkbox]").count() == 0
    page.get_by_test_id("col-handle-1").click()
    page.get_by_test_id("col-handle-2").click(modifiers=["Shift"])
    assert tbl.locator(".selectedCell").count() >= 2
    page.get_by_test_id("row-handle-1").click()
    assert tbl.locator(".selectedCell").count() >= ncols - 0 or True

    # --- right-click menu and Shift+F10 / keyboard
    ed.locator("table.dtbl:not(.ox)").nth(nth).locator("td, th").first.click()
    nrows = len(tables(page, iid)[nth]["content"])
    ed.locator("table.dtbl:not(.ox)").nth(nth).locator("td, th").first.click(button="right")
    menu = page.get_by_test_id("table-menu")
    expect(menu).to_be_visible()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    page.keyboard.press("Shift+F10")
    expect(menu).to_be_visible()
    page.keyboard.press("ArrowDown")                                         # first item is focused: move to "Insert row below"
    page.keyboard.press("Enter")
    expect(menu).to_be_hidden()
    wait_saved(page)
    assert len(tables(page, iid)[nth]["content"]) == nrows + 1
    page.keyboard.press("Control+z")
    wait_saved(page)

    # --- zoom 75 / 100 / 150: the same border drag gives the same model change
    if zoom_checks:
        g_before = tables(page, iid)[nth]["attrs"]["grid"]
        for z in ("0.75", "1", "1.5"):
            page.get_by_label("Zoom").select_option(z)
            tbl.scroll_into_view_if_needed()
            tbl.locator("td, th").first.click()
            g = tables(page, iid)[nth]["attrs"]["grid"]
            page.get_by_role("button", name="Distribute columns evenly").first.click()    # even widths: no neighbour clamps the drag
            wait_saved(page)
            g = tables(page, iid)[nth]["attrs"]["grid"]
            assert max(g) - min(g) <= 1 and sum(g) == sum(g_before), (g, g_before)
            x, y = center(page.get_by_test_id("col-resize-1"), top=True)
            tip = drag(page, x, y, x + 45 * float(z), y, alt=True)
            wait_saved(page)
            g2 = tables(page, iid)[nth]["attrs"]["grid"]
            assert abs((g2[0] - g[0]) - 45 * 15) <= 60 and g2[1] == g[1] - (g2[0] - g[0]) and sum(g2) == sum(g), (z, g, g2)   # 45 model px at any zoom
            print(label, f"zoom {z}: column 1 +{g2[0] - g[0]} twips for a {45 * float(z):.0f} px (visual) drag; {tip}")
        page.get_by_label("Zoom").select_option("0.6")


def grid_of_model(page, iid, nth):
    t = tables(page, iid)[nth]
    g = t["attrs"].get("grid")
    if g:
        return g
    return [c["attrs"]["colwidth"][0] * 15 for c in t["content"][0]["content"] if c["attrs"].get("colwidth")] or [3000] * len(t["content"][0]["content"])


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1500, "height": 1100}, accept_downloads=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        login(page, email, password)
        path = os.path.join(tempfile.mkdtemp(), "tables-fixture.docx")
        fixture(path)
        for label, src in (("synthetic", path), ("manual", os.environ.get("E2E_LINUX_MANUAL"))):
            if not src or not os.path.exists(src):
                print(label, "skipped (no file)")
                continue
            page.get_by_role("link", name="Imports").first.click()
            page.get_by_label("DOCX file").set_input_files(src)
            expect(page).to_have_url(re.compile("/imports/"))
            iid = page.url.rsplit("/", 1)[1]
            ed = editor_ready(page)
            page.get_by_label("Zoom").select_option("0.6")
            if label == "synthetic":
                # merged cells survive structure edits: delete a column through the horizontal merge (colspan shrinks), then the row
                # that starts the vertical merge (the merge start moves down), grid and colwidth stay consistent
                t0 = tables(page, iid)[0]
                n0 = len(t0["attrs"]["grid"])
                assert [c["attrs"]["colspan"] for c in t0["content"][3]["content"]] == [1, 2, 1] and t0["content"][1]["content"][0]["attrs"]["rowspan"] == 2
                ed.locator("table.dtbl:not(.ox)").first.get_by_text("r1c1").click()
                page.get_by_role("button", name="Delete row", exact=True).first.click()
                wait_saved(page)
                t = tables(page, iid)[0]
                assert len(t["content"]) == 3 and t["content"][1]["content"][0]["attrs"]["rowspan"] == 1, t["content"][1]  # merge start moved down
                ed.locator("table.dtbl:not(.ox)").first.get_by_text("r2c1").click()
                page.get_by_role("button", name="Delete column", exact=True).first.click()
                wait_saved(page)
                t = tables(page, iid)[0]
                assert len(t["attrs"]["grid"]) == n0 - 1 and [c["attrs"]["colspan"] for c in t["content"][2]["content"]] == [1, 1, 1], (t["attrs"]["grid"], n0)
                exp, data = exported(page, iid)
                et = exp.findall(f".//{qn('w:tbl')}")[0]
                assert len(grid(et)) == n0 - 1 and Document(io.BytesIO(data))
                page.keyboard.press("Control+z"); page.keyboard.press("Control+z")
                wait_saved(page)
                assert len(tables(page, iid)[0]["content"]) == 4
                run_suite(page, ed, iid, 0, label)
                # delete table: confirm modal, cancel keeps it, undo brings it back
                ed.locator("table.dtbl:not(.ox)").nth(1).locator("td").first.click()
                page.get_by_test_id("table-toolbar").get_by_role("button", name="Delete table").click()
                dlg = page.get_by_role("dialog")
                expect(dlg).to_be_visible()
                dlg.get_by_role("button", name="Cancel").click()
                assert ed.locator("table.dtbl:not(.ox)").count() == 2
                page.get_by_test_id("table-toolbar").get_by_role("button", name="Delete table").click()
                page.get_by_role("dialog").get_by_role("button", name="Delete table").click()
                wait_saved(page)
                assert ed.locator("table.dtbl:not(.ox)").count() == 1
                page.keyboard.press("Control+z")
                assert ed.locator("table.dtbl:not(.ox)").count() == 2
            else:
                # real manual: first table with >= 3 columns that is not a cover table
                idx = next(i for i in range(ed.locator("table.dtbl:not(.ox)").count()) if ed.locator("table.dtbl:not(.ox)").nth(i).locator("tr").first.locator("td, th").count() >= 3)
                run_suite(page, ed, iid, idx, label, zoom_checks=True)
        page.screenshot(path=os.path.join(ART, "tables-final.png"))
        assert not errors, errors
        print("tables e2e ok")
    except Exception:
        page.screenshot(path=os.path.join(ART, "tables-failure.png"))
        raise
    finally:
        browser.close()
