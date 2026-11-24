import { Editor } from "@tiptap/core";
import { CellSelection } from "@tiptap/pm/tables";
import { expect, test } from "vitest";
import { extensions } from "./extensions";
import * as T from "./tables";

const p = (t: string) => ({ type: "paragraph", attrs: {}, content: t ? [{ type: "text", text: t }] : [] });
const c = (t: string, a: Record<string, unknown> = {}, type = "tableCell") => ({ type, attrs: { colspan: 1, rowspan: 1, colwidth: null, ...a }, content: [p(t)] });
const row = (cells: unknown[], rfmt?: unknown) => ({ type: "tableRow", ...(rfmt ? { attrs: { rfmt } } : {}), content: cells });
const table = (rows: unknown[], attrs: Record<string, unknown> = {}) => ({ type: "table", attrs, content: rows });
const mk = (...blocks: unknown[]) => new Editor({ extensions, content: { type: "doc", content: blocks as any } });
/** Cursor into the first text node that reads `t`. */
const at = (e: Editor, t: string) => { let hit = -1; e.state.doc.descendants((n, pos) => { if (hit < 0 && n.isText && n.text === t) hit = pos + 1; }); e.commands.setTextSelection(hit); };
const tbl = (e: Editor): any => e.getJSON().content!.find((n) => n.type === "table")!;
const grid = (e: Editor) => tbl(e).attrs!.grid as number[] | null;
const texts = (e: Editor) => tbl(e).content!.map((r: any) => r.content!.map((x: any) => x.content![0].content?.[0]?.text ?? ""));
const cell = (e: Editor, r: number, k: number) => tbl(e).content![r].content![k];
const blue = { shd: "0000FF", vAlign: "center" };

test("units convert both ways and the drag tooltip lists all three", () => {
  expect(T.toTwips(1, "cm")).toBe(567); expect(T.toTwips(1, "in")).toBe(1440); expect(T.toTwips(10, "pt")).toBe(200);
  expect(T.fromTwips(1440, "in")).toBe(1); expect(T.fmtLen(567, "cm")).toBe("1.00 cm");
  expect(T.fmtAll(1440, "cm")).toBe("2.54 cm (1.00 in, 72.0 pt)");
});

test("resizeColumn: the neighbour absorbs, Shift rescales the rest, min width holds, the last border grows the table", () => {
  const g = [2000, 3000, 4000];
  expect(T.resizeColumn(g, 0, 500)).toEqual([2500, 2500, 4000]);
  expect(T.resizeColumn(g, 0, -5000)).toEqual([T.MIN_COL, 2000 + 3000 - T.MIN_COL, 4000]);
  expect(T.resizeColumn(g, 0, 99999)).toEqual([2000 + 3000 - T.MIN_COL, T.MIN_COL, 4000]);
  const k = T.resizeColumn(g, 0, 700, "keep");
  expect(k[0]).toBe(2700); expect(T.sum(k)).toBe(9000); expect(k[1] / k[2]).toBeCloseTo(3000 / 4000, 1);
  expect(T.resizeColumn(g, 2, 1000)).toEqual([2000, 3000, 5000]);
  expect(T.resizeColumn(g, 2, -99999)[2]).toBe(T.MIN_COL);
});

test("distribute, scaleGrid, insertCol and deleteCols keep sums", () => {
  expect(T.distribute([1000, 2000, 3001], 0, 3)).toEqual([2001, 2000, 2000]);
  expect(T.distribute([1000, 2000, 3000], 1, 3)).toEqual([1000, 2500, 2500]);
  expect(T.sum(T.scaleGrid([1, 1, 1], 100))).toBe(100);
  expect(T.scaleGrid([3000, 6000], 4500)).toEqual([1500, 3000]);
  expect(T.insertCol([2000, 4000], 1, true)).toEqual({ grid: [2000, 2000, 2000], at: 2 });
  expect(T.insertCol([2000, 4000], 0, false)).toEqual({ grid: [1000, 1000, 4000], at: 0 });
  expect(T.deleteCols([1, 2, 3, 4], 1, 3)).toEqual([1, 4]);
  expect(T.snap(100, false)).toBe(114); expect(T.snap(100, true)).toBe(100);
});

const plain = () => mk(table([row([c("a", { cfmt: blue }), c("b")], { h: { val: 500, rule: "atLeast" }, cant: true }), row([c("c"), c("d")])], { grid: [3000, 6000], tfmt: { width: { type: "dxa", w: 9000 } } }), p("after"));

test("insert row below clones the neighbour's cell and row formatting, in one undo step", () => {
  const e = plain(), before = JSON.stringify(e.getJSON());
  at(e, "a");
  expect(T.run(e, T.insertRow(true))).toBe(true);
  expect(texts(e)).toEqual([["a", "b"], ["", ""], ["c", "d"]]);
  expect(cell(e, 1, 0).attrs!.cfmt).toEqual(blue);
  expect(tbl(e).content![1].attrs!.rfmt).toEqual({ h: { val: 500, rule: "atLeast" }, cant: true });
  expect(e.state.selection.$from.parent.type.name).toBe("paragraph"); // selection stays in the table
  e.commands.undo();
  expect(JSON.stringify(e.getJSON())).toBe(before);
  e.commands.redo();
  expect(texts(e).length).toBe(3);
});

test("two quick operations are two undo steps", () => {
  const e = plain();
  at(e, "a"); T.run(e, T.insertRow(true)); T.run(e, T.insertRow(true));
  expect(texts(e).length).toBe(4);
  e.commands.undo();
  expect(texts(e).length).toBe(3);
});

test("insert column splits the neighbour's width, clones formatting and keeps the table width", () => {
  const e = plain();
  at(e, "a"); T.run(e, T.insertColumn(true));
  expect(grid(e)).toEqual([1500, 1500, 6000]);
  expect(texts(e)).toEqual([["a", "", "b"], ["c", "", "d"]]);
  expect(cell(e, 0, 1).attrs!.cfmt).toEqual(blue);
  expect(cell(e, 0, 1).attrs!.colwidth).toEqual([100]); expect(cell(e, 0, 2).attrs!.colwidth).toEqual([400]);
  expect((tbl(e).attrs!.tfmt as any).width).toEqual({ type: "dxa", w: 9000 });
  at(e, "d"); T.run(e, T.insertColumn(false));
  expect(grid(e)).toEqual([1500, 1500, 3000, 3000]);
  e.commands.undo(); expect(grid(e)).toEqual([1500, 1500, 6000]);
});

test("delete column through a merged cell shrinks the colspan; the grid loses that column", () => {
  const e = mk(table([row([c("h", { colspan: 3 })]), row([c("a"), c("b"), c("c")])], { grid: [1000, 2000, 3000] }));
  at(e, "b"); T.run(e, T.removeColumns);
  expect(grid(e)).toEqual([1000, 3000]);
  expect(cell(e, 0, 0).attrs!.colspan).toBe(2);
  expect(texts(e)).toEqual([["h"], ["a", "c"]]);
  expect(cell(e, 1, 1).attrs!.colwidth).toEqual([200]);
});

test("delete row that starts a vertical merge moves the merge start down", () => {
  const e = mk(table([row([c("m", { rowspan: 3 }), c("a")]), row([c("b")]), row([c("d")])], { grid: [1000, 2000] }));
  at(e, "a"); T.run(e, T.removeRows);
  expect(texts(e)).toEqual([["m", "b"], ["d"]]);
  expect(cell(e, 0, 0).attrs!.rowspan).toBe(2);
});

test("the last remaining row/column is never deleted (use delete table)", () => {
  const e = mk(table([row([c("a")])], { grid: [1000] }));
  at(e, "a");
  expect(T.run(e, T.removeRows)).toBe(false); expect(T.run(e, T.removeColumns)).toBe(false);
  expect(T.run(e, T.removeTable)).toBe(true);
  expect(e.getJSON().content!.some((n) => n.type === "table")).toBe(false);
});

test("merge then split keeps the grid and gives the new cells the formatting", () => {
  const e = mk(table([row([c("a", { cfmt: blue }), c("b")]), row([c("c"), c("d")])], { grid: [3000, 6000] }));
  const pos = (t: string) => { let hit = 0; e.state.doc.descendants((n, p0) => { if (n.isText && n.text === t) { const $p = e.state.doc.resolve(p0); hit = $p.before($p.depth - 1); } }); return hit; };
  e.view.dispatch(e.state.tr.setSelection(new CellSelection(e.state.doc.resolve(pos("a")), e.state.doc.resolve(pos("b")))));
  expect(T.run(e, T.merge)).toBe(true);
  expect(cell(e, 0, 0).attrs!.colspan).toBe(2); expect(cell(e, 0, 0).attrs!.colwidth).toEqual([200, 400]); expect(grid(e)).toEqual([3000, 6000]);
  at(e, "a");
  expect(T.run(e, T.unmerge)).toBe(true);
  expect(texts(e)[0].length).toBe(2); expect(cell(e, 0, 1).attrs!.cfmt).toEqual(blue); expect(grid(e)).toEqual([3000, 6000]);
});

test("header rows: toggle, leading rows only", () => {
  const e = mk(table([row([c("h", {}, "tableHeader")]), row([c("a")]), row([c("b")])], { grid: [1000] }));
  at(e, "a"); T.run(e, T.toggleHeader);
  expect(cell(e, 0, 0).type).toBe("tableCell");
  T.run(e, T.setHeaderRows(2));
  expect([0, 1, 2].map((r) => cell(e, r, 0).type)).toEqual(["tableHeader", "tableHeader", "tableCell"]);
});

test("move row up/down, refused across the header, through merges, and for a dynamic template", () => {
  const e = mk(table([row([c("h", {}, "tableHeader")]), row([c("a")]), row([c("b")]), row([c("c")])], { grid: [1000] }));
  at(e, "b"); expect(T.run(e, T.moveRow(-1))).toBe(true);
  expect(texts(e)).toEqual([["h"], ["b"], ["a"], ["c"]]);
  expect(e.state.selection.$from.parent.textContent).toBe("b");
  expect(T.run(e, T.moveRow(-1))).toBe(false); // into the header
  e.commands.undo(); expect(texts(e)).toEqual([["h"], ["a"], ["b"], ["c"]]);
  const d = mk(table([row([c("a")]), row([c("tpl")])], { grid: [1000], dynamicList: "xs" }));
  at(d, "a"); expect(T.run(d, T.moveRow(1))).toBe(false);
  const m = mk(table([row([c("a"), c("m", { rowspan: 2 })]), row([c("b")]), row([c("z"), c("y")])], { grid: [1000, 1000] }));
  at(m, "b"); expect(T.run(m, T.moveRow(1))).toBe(false); // row b sits in a vertical merge
  at(m, "z"); expect(T.run(m, T.moveRow(-1))).toBe(false);
});

test("insert below the dynamic template inserts above it, so the template stays last", () => {
  const e = mk(table([row([c("h")]), row([c("tpl")])], { grid: [1000], dynamicList: "xs" }));
  at(e, "tpl"); T.run(e, T.insertRow(true));
  expect(texts(e)).toEqual([["h"], [""], ["tpl"]]);
});

test("duplicate row / column and clear cells", () => {
  const e = mk(table([row([c("a", { cfmt: blue }), c("b")]), row([c("c"), c("d")])], { grid: [3000, 6000] }));
  at(e, "c"); T.run(e, T.duplicateRow);
  expect(texts(e)).toEqual([["a", "b"], ["c", "d"], ["c", "d"]]);
  at(e, "b"); T.run(e, T.duplicateColumn);
  expect(texts(e)[0]).toEqual(["a", "b", "b"]); expect(grid(e)).toEqual([3000, 6000, 6000]);
  at(e, "a"); T.run(e, T.clearCells);
  expect(texts(e)[0][0]).toBe(""); expect(cell(e, 0, 0).attrs!.cfmt).toEqual(blue);
});

test("sort rows by the cursor's column keeps the header and is numeric aware", () => {
  const e = mk(table([row([c("n", {}, "tableHeader")]), row([c("10")]), row([c("9")]), row([c("100")])], { grid: [1000] }));
  at(e, "9"); T.run(e, T.sortRows(true));
  expect(texts(e)).toEqual([["n"], ["9"], ["10"], ["100"]]);
  T.run(e, T.sortRows(false));
  expect(texts(e)).toEqual([["n"], ["100"], ["10"], ["9"]]);
  const m = mk(table([row([c("a", { rowspan: 2 })]), row([])], { grid: [1000] }));
  at(m, "a"); expect(T.run(m, T.sortRows(true))).toBe(false);
});

test("borders: an outside border is written on both sides of shared edges and nowhere inside", () => {
  const e = mk(table([row([c("a"), c("b")]), row([c("c"), c("d")])], { grid: [3000, 3000] }));
  at(e, "a"); T.run(e, T.setBorders("right", { val: "single", sz: 8, color: "FF0000" }));
  expect((cell(e, 0, 0).attrs!.cfmt as any).bdr.right.color).toBe("FF0000");
  expect((cell(e, 0, 1).attrs!.cfmt as any).bdr.left.color).toBe("FF0000");
  expect(cell(e, 1, 0).attrs!.cfmt).toBeFalsy();
  T.run(e, T.setBorders("none", null));
  expect((cell(e, 0, 0).attrs!.cfmt as any).bdr.top).toEqual({ val: "nil", sz: 0 });
  expect(T.borderEdges("inside", { l: 0, t: 0, r: 3, b: 2 })).toEqual({ h: [[1, 0], [1, 1], [1, 2]], v: [[1, 0], [1, 1], [2, 0], [2, 1]] });
});

test("alignment, shading, row height, header flags and table width land in the attrs", () => {
  const e = mk(table([row([c("a"), c("b")]), row([c("c"), c("d")])], { grid: [3000, 6000] }));
  at(e, "a");
  T.run(e, T.cellAlign("center")); expect(cell(e, 0, 0).content![0].attrs!.pfmt).toEqual({ jc: "center" });
  T.run(e, T.cellFmt({ shd: "FFFF00", vAlign: "bottom" })); expect(cell(e, 0, 0).attrs!.cfmt).toEqual({ shd: "FFFF00", vAlign: "bottom" });
  T.run(e, T.rowFmt({ h: { val: 600, rule: "exact" }, cant: true })); expect(tbl(e).content![0].attrs!.rfmt).toEqual({ h: { val: 600, rule: "exact" }, cant: true });
  T.run(e, T.setWidth({ mode: "fixed", tw: 4500 }));
  expect(grid(e)).toEqual([1500, 3000]); expect((tbl(e).attrs!.tfmt as any)).toMatchObject({ layout: "fixed", width: { type: "dxa", w: 4500 } });
  T.run(e, T.setWidth({ mode: "pct", pct: 50 })); expect((tbl(e).attrs!.tfmt as any).width).toEqual({ type: "pct", w: 2500 });
  T.run(e, T.setWidth({ mode: "auto" })); expect((tbl(e).attrs!.tfmt as any)).toMatchObject({ layout: "autofit", width: { type: "auto", w: 0 } });
  e.commands.undo(); expect((tbl(e).attrs!.tfmt as any).layout).toBe("fixed");
});

test("table style change drops derived cell formatting but keeps direct formatting", () => {
  const e = mk(table([row([c("a", { eff: { shd: "AAAAAA" }, cfmt: blue })])], { grid: [1000], styleId: "Old" }));
  at(e, "a"); T.run(e, T.setTableStyle("TableGrid"));
  expect(tbl(e).attrs!.styleId).toBe("TableGrid"); expect(cell(e, 0, 0).attrs!.eff).toBeNull(); expect(cell(e, 0, 0).attrs!.cfmt).toEqual(blue);
});

test("table <-> text: tab separated", () => {
  const e = mk(p("x\ty"), p("z"), p("after"));
  e.commands.setTextSelection({ from: 1, to: 6 });
  expect(T.run(e, T.textToTable)).toBe(true);
  expect(texts(e)).toEqual([["x", "y"], ["z", ""]]);
  at(e, "x"); T.run(e, T.tableToText);
  expect(e.getJSON().content!.slice(0, 2).map((n: any) => n.content?.[0]?.text)).toEqual(["x\ty", "z"]);
});

test("gridGuard repairs a stale grid after a structure change we did not make", () => {
  const e = mk(table([row([c("a", { colwidth: [100] }), c("b", { colwidth: [200] })])], { grid: [1500, 3000] }));
  at(e, "b");
  e.view.dispatch(e.state.tr.setNodeMarkup(1 + 0, undefined, { ...tbl(e).attrs, grid: [1500] })); // a stale 1-entry grid
  expect(grid(e)).toEqual([1500, 3000]);
});

test("tableInfo reports the selection and an unedited table keeps its attrs", () => {
  const e = plain();
  at(e, "a");
  expect(T.tableInfo(e.state)).toMatchObject({ rows: 2, cols: 2, header: false, dynamic: null, cellJc: "left", shd: "0000FF", rowH: { val: 500 }, cant: true });
  at(e, "after"); expect(T.tableInfo(e.state)).toBeNull();
  expect(JSON.stringify(e.getJSON())).toContain('"grid":[3000,6000]');
});
