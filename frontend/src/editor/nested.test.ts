import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { extensions } from "./extensions";
import { CellSelection } from "@tiptap/pm/tables";
import * as T from "./tables";
import { analyzeTable } from "./dynamic";

const p = (t: string) => ({ type: "paragraph", attrs: {}, content: t ? [{ type: "text", text: t }] : [] });
const c = (t: string | unknown, a: Record<string, unknown> = {}) => ({ type: "tableCell", attrs: { colspan: 1, rowspan: 1, colwidth: null, ...a }, content: [typeof t === "string" ? p(t) : t] });
const row = (cells: unknown[]) => ({ type: "tableRow", content: cells });
const table = (rows: unknown[], grid: number[] | null = null) => ({ type: "table", attrs: { grid }, content: rows });
const mk = (...blocks: unknown[]) => new Editor({ extensions, content: { type: "doc", content: blocks as any } });
const at = (e: Editor, t: string) => { let hit = -1; e.state.doc.descendants((n, pos) => { if (hit < 0 && n.isText && n.text === t) hit = pos + 1; }); e.commands.setTextSelection(hit); };
const inner = () => table([row([c("i1"), c("i2")]), row([c("i3"), c("i4")])], [1000, 1000]);
const outer = () => table([row([c(inner()), c("o2")]), row([c("o3"), c("o4")])], [2400, 1200]);
const rowsOf = (n: any) => n.content.length;

test("the schema accepts a table inside a cell and the selection resolves to the innermost table", () => {
  const e = mk(outer());
  at(e, "i1");
  const ctx = T.ctxOf(e.state)!;
  expect(ctx.table.childCount).toBe(2); expect(ctx.map.width).toBe(2);
  expect(ctx.table.textContent).toBe("i1i2i3i4");
  expect(T.nesting(e.state)).toEqual({ depth: 1, inner: false });
  at(e, "o2"); expect(T.nesting(e.state)).toEqual({ depth: 0, inner: false });
});

test("insert/delete/merge/split act on the inner table and leave the outer one alone", () => {
  const e = mk(outer());
  at(e, "i1"); expect(T.run(e, T.insertRow(true))).toBe(true);
  let o: any = e.getJSON().content![0];
  expect(rowsOf(o)).toBe(2); expect(rowsOf(o.content[0].content[0].content[0])).toBe(3);
  at(e, "i3"); expect(T.run(e, T.removeRows)).toBe(true);
  o = e.getJSON().content![0]; expect(rowsOf(o.content[0].content[0].content[0])).toBe(2);
  const cellPos = (t: string) => { let hit = 0; e.state.doc.descendants((n, p0) => { if (n.isText && n.text === t) { const $p = e.state.doc.resolve(p0); hit = $p.before($p.depth - 1); } }); return hit; };
  e.view.dispatch(e.state.tr.setSelection(new CellSelection(e.state.doc.resolve(cellPos("i1")), e.state.doc.resolve(cellPos("i2")))));
  expect(T.run(e, T.merge)).toBe(true); // merge on the inner table
  expect((e.getJSON().content![0] as any).content[0].content[0].content[0].content[0].content[0].attrs.colspan).toBe(2);
  expect(T.run(e, T.unmerge)).toBe(true);
  at(e, "i1"); T.run(e, T.insertColumn(true));
  o = e.getJSON().content![0];
  const it = o.content[0].content[0].content[0];
  expect(it.attrs.grid.length).toBe(3); expect(o.attrs.grid).toEqual([2400, 1200]); // inner grid grew, outer grid untouched
  at(e, "o3"); T.run(e, T.insertRow(false));
  expect(rowsOf(e.getJSON().content![0])).toBe(3);
});

test("Alt+Up selects the enclosing cell (commands then act on the outer table), Alt+Down goes back in", () => {
  const e = mk(outer());
  at(e, "i2");
  expect(T.run(e, T.selectOuterTable)).toBe(true);
  expect(T.nesting(e.state)).toEqual({ depth: 0, inner: true });
  expect(T.ctxOf(e.state)!.table.childCount).toBe(2); expect(T.ctxOf(e.state)!.map.width).toBe(2);
  T.run(e, T.insertColumn(true));
  const o: any = e.getJSON().content![0];
  expect(o.attrs.grid.length).toBe(3); expect(o.content[0].content[0].content[0].attrs.grid).toEqual([1000, 1000]);
  expect(T.run(e, T.selectInnerTable)).toBe(true);
  expect(T.nesting(e.state)!.depth).toBe(1);
  expect(T.run(e, T.selectOuterTable)).toBe(true); expect(T.run(e, T.selectOuterTable)).toBe(false); // top level has no parent
});

test("deleting a nested table that fills its cell leaves a valid cell; table depth is counted", () => {
  const e = mk(outer());
  at(e, "i4");
  expect(T.tableDepth(e.state, T.ctxOf(e.state)!.pos)).toBe(1);
  expect(T.run(e, T.removeTable)).toBe(true);
  const cell0: any = (e.getJSON().content![0] as any).content[0].content[0];
  expect(cell0.content.length).toBeGreaterThan(0); expect(cell0.content[0].type).toBe("paragraph");
});

test("the grid guard repairs a stale grid of a nested table too", () => {
  const e = mk(table([row([c(table([row([c("a"), c("b")])], [1000]))])], [2000]));
  at(e, "a"); e.commands.insertContent("x"); // any edit inside the inner table triggers the guard
  const it: any = (e.getJSON().content![0] as any).content[0].content[0].content[0];
  expect(it.attrs.grid === null || it.attrs.grid.length === 2).toBe(true);
});

test("dynamic tables are refused when a cell holds a nested table; table-to-text keeps the words apart", () => {
  const e = mk(outer());
  const plan = analyzeTable(e.state.doc.child(0));
  expect(plan.ok).toBe(false);
  at(e, "o3"); expect(T.run(e, T.tableToText)).toBe(true);
  const text = e.state.doc.textContent;
  expect(text).toContain("i1 i2 i3 i4");
});
