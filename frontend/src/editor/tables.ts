// Table editing: pure grid math (twips) + ProseMirror operations on top of prosemirror-tables. Every operation is ONE transaction
// (closeHistory starts a new undo event), keeps `attrs.grid` (twips, one entry per grid column) and the cells' `colwidth` (px,
// 15 twips each) consistent, and clones the neighbouring cell's formatting (cfmt/eff/paragraph attrs) into inserted cells.
import { closeHistory } from "@tiptap/pm/history";
import type { Node as PMNode } from "@tiptap/pm/model";
import { EditorState, Plugin, TextSelection, Transaction } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";
import { addColumn, addRow, cellAround, CellSelection, deleteColumn, deleteRow, deleteTable, isInTable, mergeCells, selectedRect, splitCell, TableMap, toggleHeaderRow, type TableRect } from "@tiptap/pm/tables";

// ---- units ----------------------------------------------------------------------------------------
export type Unit = "cm" | "in" | "pt";
const PER: Record<Unit, number> = { cm: 566.929, in: 1440, pt: 20 };
export const getUnit = (): Unit => { try { const u = localStorage.getItem("docgen.unit"); return u === "in" || u === "pt" ? u : "cm"; } catch { return "cm"; } };
export const toTwips = (v: number, u: Unit = getUnit()) => Math.round(v * PER[u]);
export const fromTwips = (tw: number, u: Unit = getUnit()) => tw / PER[u];
const fix = (v: number, u: Unit) => (u === "pt" ? v.toFixed(1) : v.toFixed(2));
export const fmtLen = (tw: number, u: Unit = getUnit()) => `${fix(fromTwips(tw, u), u)} ${u}`;
/** Drag tooltip: the configured unit first, the other two in brackets. */
export const fmtAll = (tw: number, u: Unit = getUnit()) => `${fmtLen(tw, u)} (${(["cm", "in", "pt"] as Unit[]).filter((x) => x !== u).map((x) => fmtLen(tw, x)).join(", ")})`;

// ---- grid math ------------------------------------------------------------------------------------
export const MIN_COL = 360; // twips (0.63 cm)
export const SNAP = 57;     // twips (0.1 cm); Alt = free
export const sum = (g: number[]) => g.reduce((a, b) => a + b, 0);
/** Even split of `total` over n columns, remainder spread from the left. */
const split = (total: number, n: number) => Array.from({ length: n }, (_, k) => Math.floor(total / n) + (k < total - Math.floor(total / n) * n ? 1 : 0));
/** Scale columns proportionally to `total` (the rounding remainder goes to the last column). */
export function scaleGrid(g: number[], total: number): number[] {
  const s = sum(g), out = g.map((x) => Math.max(Math.round(x * total / s), 1));
  out[out.length - 1] += total - sum(out);
  return out;
}
/** Move the right border of column i by d twips. "pair": the next column gives/takes it (table width stays); "keep" (Shift): the
 *  columns right of i are rescaled to keep the table width; the last border always changes the table width. Min width respected. */
export function resizeColumn(g: number[], i: number, d: number, mode: "pair" | "keep" = "pair", min = MIN_COL): number[] {
  const out = g.slice(), last = i === g.length - 1;
  if (last) { out[i] = Math.max(g[i] + d, min); return out; }
  const rest = sum(g.slice(i + 1)), room = mode === "pair" ? g[i + 1] - min : rest - min * (g.length - i - 1);
  d = Math.min(Math.max(d, min - g[i]), room);
  out[i] = g[i] + d;
  if (mode === "pair") out[i + 1] = g[i + 1] - d;
  else { const sc = scaleGrid(g.slice(i + 1), rest - d); sc.forEach((x, k) => (out[i + 1 + k] = x)); }
  return out;
}
export const distribute = (g: number[], from: number, to: number) => { const out = g.slice(); split(sum(g.slice(from, to)), to - from).forEach((x, k) => (out[from + k] = x)); return out; };
/** Insert a column next to column c: it takes half of c's width (table width unchanged). Returns the new grid and the new column's index. */
export function insertCol(g: number[], c: number, after: boolean): { grid: number[]; at: number } {
  const out = g.slice(), half = Math.max(Math.floor(g[c] / 2), 1), at = after ? c + 1 : c;
  out[c] = g[c] - half;
  out.splice(at, 0, half);
  return { grid: out, at };
}
export const deleteCols = (g: number[], from: number, to: number) => g.filter((_, i) => i < from || i >= to);
/** Border position helper: snap an offset to the 0.1 cm raster. */
export const snap = (tw: number, free: boolean) => (free ? tw : Math.round(tw / SNAP) * SNAP);

// ---- table context --------------------------------------------------------------------------------
export type Ctx = { rect: TableRect; table: PMNode; start: number; map: TableMap; pos: number };
export function ctxOf(state: EditorState): Ctx | null {
  if (!isInTable(state)) return null;
  const rect = selectedRect(state);
  return { rect, table: rect.table, start: rect.tableStart, map: rect.map, pos: rect.tableStart - 1 };
}
export type Cell = { pos: number; node: PMNode; row: number; col: number; rs: number; cs: number }; // pos relative to the table start
export function cellsOf(table: PMNode, map: TableMap): Cell[] {
  const seen = new Set<number>(), out: Cell[] = [];
  map.map.forEach((pos, i) => {
    if (seen.has(pos)) return;
    seen.add(pos);
    const node = table.nodeAt(pos)!;
    out.push({ pos, node, row: Math.floor(i / map.width), col: i % map.width, rs: node.attrs.rowspan ?? 1, cs: node.attrs.colspan ?? 1 });
  });
  return out;
}
const rowPos = (table: PMNode, r: number) => { let p = 0; for (let i = 0; i < r; i++) p += table.child(i).nodeSize; return p; };
const isHeaderRow = (row: PMNode) => row.childCount > 0 && (() => { let ok = true; row.forEach((c) => { if (c.type.name !== "tableHeader") ok = false; }); return ok; })();
export function headerCount(table: PMNode): number { let n = 0; while (n < table.childCount && isHeaderRow(table.child(n))) n++; return n; }
export const isTemplateRow = (table: PMNode, r: number) => !!table.attrs.dynamicList && r === table.childCount - 1;

/** attrs.grid when it fits the table, else the widths the cells carry (px), else null. */
export function gridOf(table: PMNode, map: TableMap): number[] | null {
  const g = table.attrs.grid;
  if (Array.isArray(g) && g.length === map.width && g.every((x) => x > 0)) return g;
  const w: (number | null)[] = Array(map.width).fill(null);
  for (const c of cellsOf(table, map)) { const cw = c.node.attrs.colwidth; if (Array.isArray(cw) && cw.length === c.cs && cw.every((x: number) => x > 0)) cw.forEach((x: number, k: number) => (w[c.col + k] = x * 15)); }
  return w.every(Boolean) ? (w as number[]) : null;
}
/** Visual px of CSS `zoom`: rect size / layout size of the same element. */
export const zoomOf = (el: HTMLElement) => (el.offsetWidth ? el.getBoundingClientRect().width / el.offsetWidth : 1) || 1;
/** Text width of the page in twips (editor surface width). */
export const textWidth = (view: EditorView) => (view.dom.clientWidth ? view.dom.clientWidth * 15 : 9638);
/** Column widths (twips) from the drawn table; widest single-column cell per column, spanning cells share what is left. */
export function domGrid(view: EditorView, c: Ctx): number[] {
  const dom = view.nodeDOM(c.pos) as HTMLElement | null, z = dom ? zoomOf(dom) : 1, w: number[] = Array(c.map.width).fill(0);
  const cells = cellsOf(c.table, c.map);
  for (const k of cells) if (k.cs === 1) { const el = view.nodeDOM(c.start + k.pos) as HTMLElement | null; if (el) w[k.col] = Math.max(w[k.col], el.getBoundingClientRect().width / z * 15); }
  for (const k of cells) if (k.cs > 1) {
    const el = view.nodeDOM(c.start + k.pos) as HTMLElement | null, have = sum(w.slice(k.col, k.col + k.cs)), tot = el ? el.getBoundingClientRect().width / z * 15 : 0;
    if (tot > have) for (let i = 0; i < k.cs; i++) w[k.col + i] += (tot - have) / k.cs;
  }
  const fill = textWidth(view) / c.map.width;
  return w.map((x) => Math.max(Math.round(x) || Math.round(fill), MIN_COL));
}
export const ensureGrid = (view: EditorView | null, c: Ctx) => gridOf(c.table, c.map) ?? (view ? domGrid(view, c) : split(9638, c.map.width));
/** Natural (autofit) column widths in twips: a clone of the table without colgroup/fixed layout is measured in place. */
export function autofitGrid(view: EditorView, c: Ctx): number[] {
  const dom = view.nodeDOM(c.pos) as HTMLElement | null;
  if (!dom?.parentElement) return ensureGrid(view, c);
  const clone = dom.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("colgroup, .pgsep-row, tr.rep").forEach((e) => e.remove());
  clone.style.cssText += ";table-layout:auto;width:auto;min-width:0;position:absolute;visibility:hidden;left:0;top:0";
  clone.querySelectorAll<HTMLElement>("td,th").forEach((e) => { e.style.width = "auto"; });
  dom.parentElement.appendChild(clone);
  const z = zoomOf(dom), w: number[] = Array(c.map.width).fill(0), rows = Array.from(clone.querySelectorAll("tr"));
  cellsOf(c.table, c.map).forEach((k) => { const el = rows[k.row]?.children[cellIndex(c, k)] as HTMLElement | undefined; if (el && k.cs === 1) w[k.col] = Math.max(w[k.col], el.getBoundingClientRect().width / z * 15); });
  clone.remove();
  const base = ensureGrid(view, c);
  return w.map((x, i) => Math.max(Math.round(x) || base[i], MIN_COL));
}
const cellIndex = (c: Ctx, k: Cell) => cellsOf(c.table, c.map).filter((o) => o.row === k.row && o.col < k.col).length;

// ---- transaction helpers --------------------------------------------------------------------------
export type Op = (state: EditorState, view?: EditorView | null) => Transaction | null;
/** Run an operation: one dispatch = one undo step. Returns whether it did something. */
export function run(editor: { state: EditorState; view: EditorView }, op: Op): boolean {
  const tr = op(editor.state, editor.view);
  if (!tr) return false;
  editor.view.dispatch(closeHistory(tr).scrollIntoView());
  editor.view.focus();
  return true;
}
const cap = (fn: (state: EditorState, d: (tr: Transaction) => void) => boolean, state: EditorState): Transaction | null => { let out: Transaction | null = null; fn(state, (tr) => (out = tr)); return out; };
const noNull = (o: Record<string, unknown>) => { const r = Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "")); return Object.keys(r).length ? r : null; };

/** Write `grid` into the table and every cell's colwidth (attr-only: positions do not move). `fixed` = the table gets a fixed layout and a dxa width. */
export function applyGrid(tr: Transaction, tablePos: number, grid0: number[], opts: { fixed?: boolean; width?: Record<string, unknown> | null; layout?: string } = {}): Transaction {
  const grid = grid0.map(Math.round), table = tr.doc.nodeAt(tablePos)!, map = TableMap.get(table);
  for (const k of cellsOf(table, map)) {
    const cw = grid.slice(k.col, k.col + k.cs).map((g) => Math.round(g / 15));
    if (JSON.stringify(cw) !== JSON.stringify(k.node.attrs.colwidth)) tr.setNodeMarkup(tablePos + 1 + k.pos, undefined, { ...k.node.attrs, colwidth: cw });
  }
  // tfmt = what is drawn (incl. the style's values); tedit = only what the user set, the part the exporter writes over the imported tblPr
  let tfmt = { ...(table.attrs.tfmt ?? {}) }, tedit = { ...(table.attrs.tedit ?? {}) };
  const both = (patch: Record<string, unknown>) => { tfmt = { ...tfmt, ...patch }; tedit = { ...tedit, ...patch }; };
  if (opts.fixed) both({ layout: "fixed", width: { type: "dxa", w: sum(grid) } });
  else if (tfmt.width?.type === "dxa") both({ width: { type: "dxa", w: sum(grid) } });
  if (opts.width !== undefined) both({ width: opts.width ?? undefined });
  if (opts.layout) both({ layout: opts.layout });
  tr.setNodeMarkup(tablePos, undefined, { ...table.attrs, grid, tfmt, tedit: noNull(tedit) });
  return tr;
}

/** Copy the formatting of `ref` (cell attrs without the geometry + the first paragraph's attrs) onto the new cell at tablePos+1+pos. */
function inherit(tr: Transaction, tablePos: number, pos: number, ref: PMNode, type?: string) {
  const cur = tr.doc.nodeAt(tablePos + 1 + pos)!, { colspan: _a, rowspan: _b, colwidth: _c, ...keep } = ref.attrs;
  tr.setNodeMarkup(tablePos + 1 + pos, type ? cur.type.schema.nodes[type] : undefined, { ...cur.attrs, ...keep, colspan: cur.attrs.colspan, rowspan: cur.attrs.rowspan, colwidth: cur.attrs.colwidth });
  const p = ref.firstChild, mine = cur.firstChild;
  if (p && mine && p.type === mine.type) tr.setNodeMarkup(tablePos + 1 + pos + 1, undefined, { ...mine.attrs, styleId: p.attrs.styleId, pfmt: p.attrs.pfmt });
}

// ---- structure ------------------------------------------------------------------------------------
/** Insert a row above/below the selection. In a dynamic table the last row is the template: "below" it inserts above instead. */
export const insertRow = (after: boolean): Op => (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  if (after && c.table.attrs.dynamicList && c.rect.bottom === c.map.height) after = false;
  const at = after ? c.rect.bottom : c.rect.top, tr = state.tr;
  const refRowNode = c.table.child(after ? at - 1 : at);
  addRow(tr, c.rect, at);
  const t = tr.doc.nodeAt(c.pos)!, m = TableMap.get(t), W = m.width, r = at, rr = after ? r - 1 : r + 1;
  for (let col = 0; col < W; col++) {
    const i = r * W + col, np = m.map[i];
    if ((r > 0 && m.map[i - W] === np) || (r < m.height - 1 && m.map[i + W] === np)) continue; // inside a vertical merge
    inherit(tr, c.pos, np, t.nodeAt(m.map[rr * W + col])!, "tableCell");
  }
  tr.setNodeMarkup(c.pos + 1 + rowPos(t, r), undefined, { ...t.child(r).attrs, rfmt: refRowNode.attrs.rfmt ?? null });
  const g = gridOf(t, m);
  return g ? applyGrid(tr, c.pos, g) : tr;
};
/** Insert a column left/right of the selection; it takes half of its neighbour's width, so the table width stays. */
export const insertColumn = (after: boolean): Op => (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  const at = after ? c.rect.right : c.rect.left, col0 = after ? at - 1 : at, old = gridOf(c.table, c.map), tr = state.tr;
  addColumn(tr, c.rect, at);
  const t = tr.doc.nodeAt(c.pos)!, m = TableMap.get(t), W = m.width, rc = after ? at - 1 : at + 1;
  for (let r = 0; r < m.height; r++) {
    const i = r * W + at, np = m.map[i];
    if ((at > 0 && m.map[i - 1] === np) || (at < W - 1 && m.map[i + 1] === np)) continue; // inside a horizontal merge
    inherit(tr, c.pos, np, t.nodeAt(m.map[r * W + rc])!);
  }
  return old ? applyGrid(tr, c.pos, insertCol(old, col0, after).grid) : tr;
};
export const removeRows: Op = (state) => {
  const c = ctxOf(state);
  if (!c || (c.rect.top === 0 && c.rect.bottom === c.map.height)) return null;
  return cap(deleteRow, state);
};
export const removeColumns: Op = (state) => {
  const c = ctxOf(state);
  if (!c || (c.rect.left === 0 && c.rect.right === c.map.width)) return null;
  const g = gridOf(c.table, c.map), tr = cap(deleteColumn, state);
  return tr && g ? applyGrid(tr, c.pos, deleteCols(g, c.rect.left, c.rect.right)) : tr;
};
export const removeTable: Op = (state) => cap(deleteTable, state);
export const merge: Op = (state) => {
  const c = ctxOf(state), tr = cap(mergeCells, state);
  const g = c && gridOf(c.table, c.map);
  return tr && c && g ? applyGrid(tr, c.pos, g) : tr;
};
/** Split a merged cell; the new cells inherit its formatting. */
export const unmerge: Op = (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  const cell = cellAround(state.selection.$from), tr = cap(splitCell, state);
  if (!tr || !cell) return tr;
  const src = cell.nodeAfter!, rr = c.map.findCell(cell.pos - c.start), t = tr.doc.nodeAt(c.pos)!, m = TableMap.get(t);
  for (const k of cellsOf(t, m)) if (k.row >= rr.top && k.row < rr.bottom && k.col >= rr.left && k.col < rr.right && !k.node.attrs.cfmt && !k.node.attrs.eff) inherit(tr, c.pos, k.pos, src);
  const g = gridOf(t, m);
  return g ? applyGrid(tr, c.pos, g) : tr;
};
/** The first n rows become header rows (repeated on every page by the exporter), the rest plain cells. */
export const setHeaderRows = (n: number): Op => (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  const tr = state.tr;
  cellsOf(c.table, c.map).forEach((k) => { const want = k.row < n ? "tableHeader" : "tableCell"; if (k.node.type.name !== want) tr.setNodeMarkup(c.start + k.pos, k.node.type.schema.nodes[want], k.node.attrs); });
  return tr.docChanged ? tr : null;
};
export const toggleHeader: Op = (state) => { const c = ctxOf(state); return c ? (headerCount(c.table) ? setHeaderRows(0)(state) : setHeaderRows(1)(state)) : cap(toggleHeaderRow, state); };

// ---- rows and columns: move, duplicate, clear, sort --------------------------------------------------
const rowCells = (c: Ctx, rows: number[]) => cellsOf(c.table, c.map).filter((k) => rows.some((r) => r >= k.row && r < k.row + k.rs));
const hasVMerge = (c: Ctx, rows: number[]) => rowCells(c, rows).some((k) => k.rs > 1);
/** Alt+Shift+Up/Down. Refused (null) across the header boundary, through vertical merges and for the template row of a dynamic table. */
export const moveRow = (dir: -1 | 1): Op => (state) => {
  const c = ctxOf(state);
  if (!c || c.rect.bottom - c.rect.top !== 1) return null;
  const r = c.rect.top, t = r + dir, n = c.map.height;
  if (t < 0 || t >= n || (r < headerCount(c.table)) !== (t < headerCount(c.table)) || (c.table.attrs.dynamicList && (r === n - 1 || t === n - 1)) || hasVMerge(c, [r, t])) return null;
  const lo = Math.min(r, t), from = c.start + rowPos(c.table, lo), tr = state.tr;
  tr.replaceWith(from, from + c.table.child(lo).nodeSize + c.table.child(lo + 1).nodeSize, [c.table.child(lo + 1), c.table.child(lo)]);
  const m = TableMap.get(tr.doc.nodeAt(c.pos)!);
  tr.setSelection(state.selection instanceof CellSelection
    ? CellSelection.rowSelection(tr.doc.resolve(c.start + m.map[t * m.width]), tr.doc.resolve(c.start + m.map[t * m.width + m.width - 1]))
    : TextSelection.near(tr.doc.resolve(c.start + m.map[t * m.width + c.rect.left] + 1)));
  return tr;
};
export const duplicateRow: Op = (state) => {
  const c = ctxOf(state);
  if (!c || c.rect.bottom - c.rect.top !== 1 || hasVMerge(c, [c.rect.top])) return null;
  return state.tr.insert(c.start + rowPos(c.table, c.rect.bottom), c.table.child(c.rect.top));
};
/** Swap/duplicate need plain columns: refused when a merged cell touches the table's cells in that column. */
const plainColumn = (c: Ctx, cols: number[]) => !cellsOf(c.table, c.map).some((k) => (k.cs > 1 || k.rs > 1) && cols.some((x) => x >= k.col && x < k.col + k.cs));
export const duplicateColumn: Op = (state) => {
  const c = ctxOf(state), col = c?.rect.left;
  if (!c || col === undefined || c.rect.right - col !== 1 || !plainColumn(c, [col])) return null;
  const tr = state.tr, cells = cellsOf(c.table, c.map).filter((k) => k.col === col).sort((a, b) => b.pos - a.pos);
  for (const k of cells) tr.insert(c.start + k.pos + k.node.nodeSize, k.node);
  const g = gridOf(c.table, c.map);
  return g ? applyGrid(tr, c.pos, [...g.slice(0, col + 1), g[col], ...g.slice(col + 1)]) : tr;
};
export const moveColumn = (dir: -1 | 1): Op => (state) => {
  const c = ctxOf(state);
  if (!c || c.rect.right - c.rect.left !== 1) return null;
  const col = c.rect.left, t = col + dir;
  if (t < 0 || t >= c.map.width || !plainColumn(c, [col, t]) || !plainColumn(c, [t])) return null;
  const tr = state.tr, cells = cellsOf(c.table, c.map), pairs = Array.from({ length: c.map.height }, (_, r) => [cells.find((k) => k.row === r && k.col === Math.min(col, t))!, cells.find((k) => k.row === r && k.col === Math.max(col, t))!]).reverse();
  for (const [a, b] of pairs) tr.replaceWith(c.start + a.pos, c.start + b.pos + b.node.nodeSize, [b.node, a.node]);
  const g = gridOf(c.table, c.map);
  if (g) { const o = g.slice(); [o[col], o[t]] = [o[t], o[col]]; applyGrid(tr, c.pos, o); }
  const m = TableMap.get(tr.doc.nodeAt(c.pos)!);
  tr.setSelection(TextSelection.near(tr.doc.resolve(c.start + m.map[c.rect.top * m.width + t] + 1)));
  return tr;
};
export const clearCells: Op = (state) => {
  const cells = selectedCells(state);
  if (!cells.length) return null;
  const tr = state.tr;
  for (const k of [...cells].sort((a, b) => b.pos - a.pos)) {
    const p = k.node.firstChild;
    tr.replaceWith(k.pos + 1, k.pos + k.node.nodeSize - 1, p && (p.type.name === "paragraph" || p.type.name === "heading") ? p.type.create(p.attrs) : k.node.type.schema.nodes.paragraph.create());
  }
  return tr;
};
/** Sort the body rows by the column of the cursor (header rows and a dynamic template row stay). Refused with vertical merges. */
export const sortRows = (asc: boolean): Op => (state) => {
  const c = ctxOf(state);
  if (!c || cellsOf(c.table, c.map).some((k) => k.rs > 1)) return null;
  const hdr = headerCount(c.table), end = c.table.attrs.dynamicList ? c.map.height - 1 : c.map.height;
  if (end - hdr < 2) return null;
  const key = (r: number) => c.table.nodeAt(c.map.map[r * c.map.width + c.rect.left])?.textContent.trim() ?? "";
  const idx = Array.from({ length: end - hdr }, (_, i) => hdr + i);
  const cmp = (a: string, b: string) => { const x = parseFloat(a), y = parseFloat(b); return !isNaN(x) && !isNaN(y) ? x - y : a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }); };
  const sorted = idx.map((r, i) => ({ r, i })).sort((a, b) => (asc ? 1 : -1) * cmp(key(a.r), key(b.r)) || a.i - b.i).map((x) => c.table.child(x.r));
  if (sorted.every((n, i) => n === c.table.child(idx[i]))) return null;
  const from = c.start + rowPos(c.table, hdr), to = c.start + rowPos(c.table, end);
  const tr = state.tr.replaceWith(from, to, sorted), m = TableMap.get(tr.doc.nodeAt(c.pos)!);
  return tr.setSelection(TextSelection.near(tr.doc.resolve(c.start + m.map[hdr * m.width + c.rect.left] + 1)));
};

// ---- convert ----------------------------------------------------------------------------------------
/** Table -> one paragraph per row, cells separated by tabs (text only: variable chips and formatting inside cells are dropped). */
export const tableToText: Op = (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  const S = state.schema, paras: PMNode[] = [];
  c.table.forEach((row) => { const cells: string[] = []; row.forEach((cell) => cells.push(cell.textContent.replace(/\s+/g, " ").trim())); const line = cells.join("\t").replace(/\t+$/, ""); paras.push(S.nodes.paragraph.create(null, line ? S.text(line) : null)); });
  return state.tr.replaceWith(c.pos, c.pos + c.table.nodeSize, paras);
};
/** Selected top-level paragraphs -> table, one row per paragraph, columns split at tabs. */
export const textToTable: Op = (state) => {
  const { $from, $to } = state.selection, S = state.schema;
  if ($from.depth < 1 || isInTable(state)) return null;
  const from = $from.before(1), to = $to.after(1), paras: PMNode[] = [];
  state.doc.nodesBetween(from, to, (n) => { if (n.type.name === "paragraph") paras.push(n); return false; });
  if (!paras.length) return null;
  const rows = paras.map((p) => p.textContent.split("\t")), n = Math.max(...rows.map((r) => r.length));
  const cell = (t: string, p: PMNode) => S.nodes.tableCell.create({ colspan: 1, rowspan: 1 }, S.nodes.paragraph.create({ styleId: p.attrs.styleId, pfmt: p.attrs.pfmt }, t ? S.text(t) : null));
  const tbl = S.nodes.table.create(null, rows.map((r, i) => S.nodes.tableRow.create(null, Array.from({ length: n }, (_, j) => cell(r[j] ?? "", paras[i])))));
  return state.tr.replaceWith(from, to, tbl);
};

// ---- formatting of cells, rows, table -------------------------------------------------------------------
/** Cells of the selection (absolute positions): a cell selection's cells, else the cell around the cursor. */
export function selectedCells(state: EditorState): { pos: number; node: PMNode }[] {
  const s = state.selection, out: { pos: number; node: PMNode }[] = [];
  if (s instanceof CellSelection) { s.forEachCell((node, pos) => out.push({ node, pos })); return out; }
  const $c = cellAround(s.$from);
  return $c ? [{ pos: $c.pos, node: $c.nodeAfter! }] : [];
}
/** Merge a patch into cfmt of the selected cells (null removes a key). */
export const cellFmt = (patch: Record<string, unknown>): Op => (state) => {
  const tr = state.tr, cells = selectedCells(state);
  cells.forEach((k) => tr.setNodeMarkup(k.pos, undefined, { ...k.node.attrs, cfmt: noNull({ ...(k.node.attrs.cfmt ?? {}), ...patch }) }));
  return cells.length ? tr : null;
};
/** Cell padding (twips) of the given sides of the selected cells; the other sides keep what they had. */
export const cellMargin = (sides: ("top" | "left" | "bottom" | "right")[], tw: number): Op => (state) => {
  const tr = state.tr, cells = selectedCells(state);
  cells.forEach((k) => tr.setNodeMarkup(k.pos, undefined, { ...k.node.attrs, cfmt: noNull({ ...(k.node.attrs.cfmt ?? {}), mar: { ...(k.node.attrs.cfmt?.mar ?? k.node.attrs.eff?.mar ?? {}), ...Object.fromEntries(sides.map((x) => [x, tw])) } }) }));
  return cells.length ? tr : null;
};
/** Horizontal alignment: pfmt.jc of every paragraph in the selected cells. */
export const cellAlign = (jc: string): Op => (state) => {
  const tr = state.tr, cells = selectedCells(state);
  for (const k of cells) k.node.descendants((n, p) => { if (n.type.name === "paragraph" || n.type.name === "heading") { tr.setNodeMarkup(k.pos + 1 + p, undefined, { ...n.attrs, pfmt: noNull({ ...(n.attrs.pfmt ?? {}), jc }) }); return false; } });
  return cells.length ? tr : null;
};
export type Border = { val: string; sz: number; color: string };
export type BorderMode = "all" | "outside" | "inside" | "none" | "top" | "bottom" | "left" | "right" | "insideH" | "insideV";
/** Grid edges a mode touches: h = [row boundary y, column x], v = [column boundary x, row y]. */
export function borderEdges(mode: BorderMode, r: { l: number; t: number; r: number; b: number }) {
  const h: [number, number][] = [], v: [number, number][] = [];
  for (let y = r.t; y <= r.b; y++) for (let x = r.l; x < r.r; x++) h.push([y, x]);
  for (let x = r.l; x <= r.r; x++) for (let y = r.t; y < r.b; y++) v.push([x, y]);
  const outH = ([y]: number[]) => y === r.t || y === r.b, outV = ([x]: number[]) => x === r.l || x === r.r;
  switch (mode) {
    case "all": case "none": return { h, v };
    case "outside": return { h: h.filter(outH), v: v.filter(outV) };
    case "inside": case "insideH": case "insideV": return { h: mode === "insideV" ? [] : h.filter((e) => !outH(e)), v: mode === "insideH" ? [] : v.filter((e) => !outV(e)) };
    case "top": return { h: h.filter(([y]) => y === r.t), v: [] };
    case "bottom": return { h: h.filter(([y]) => y === r.b), v: [] };
    case "left": return { h: [], v: v.filter(([x]) => x === r.l) };
    case "right": return { h: [], v: v.filter(([x]) => x === r.r) };
  }
}
/** Borders of the selection edges (null spec = none). A shared edge is written on both neighbouring cells, so the browser and
 *  Word/LibreOffice resolve the same line. */
export const setBorders = (mode: BorderMode, spec: Border | null): Op => (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  const val = mode === "none" || !spec ? { val: "nil", sz: 0 } : spec, W = c.map.width, H = c.map.height;
  const e = borderEdges(mode, { l: c.rect.left, t: c.rect.top, r: c.rect.right, b: c.rect.bottom }), patch = new Map<number, Record<string, Border | { val: string; sz: number }>>();
  const put = (pos: number, side: string) => { if (pos >= 0) patch.set(pos, { ...(patch.get(pos) ?? {}), [side]: val }); };
  for (const [y, x] of e.h) { const a = y > 0 ? c.map.map[(y - 1) * W + x] : -1, b = y < H ? c.map.map[y * W + x] : -1; if (a !== b) { put(a, "bottom"); put(b, "top"); } }
  for (const [x, y] of e.v) { const a = x > 0 ? c.map.map[y * W + x - 1] : -1, b = x < W ? c.map.map[y * W + x] : -1; if (a !== b) { put(a, "right"); put(b, "left"); } }
  const tr = state.tr;
  patch.forEach((p, pos) => { const n = c.table.nodeAt(pos)!; tr.setNodeMarkup(c.start + pos, undefined, { ...n.attrs, cfmt: { ...(n.attrs.cfmt ?? {}), bdr: { ...(n.attrs.cfmt?.bdr ?? {}), ...p } } }); });
  return patch.size ? tr : null;
};
const tblAttr = (fn: (a: Record<string, any>) => Record<string, unknown>): Op => (state) => { const c = ctxOf(state); return c ? state.tr.setNodeMarkup(c.pos, undefined, { ...c.table.attrs, ...fn(c.table.attrs) }) : null; };
export const setTfmt = (patch: Record<string, unknown>) => tblAttr((a) => ({ tfmt: noNull({ ...(a.tfmt ?? {}), ...patch }), tedit: noNull({ ...(a.tedit ?? {}), ...patch }) }));
/** Table style: derived cell formatting (eff) belongs to the old style, so it is dropped; direct formatting (cfmt) stays. */
export const setTableStyle = (styleId: string | null): Op => (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  const tr = state.tr;
  cellsOf(c.table, c.map).forEach((k) => { if (k.node.attrs.eff) tr.setNodeMarkup(c.start + k.pos, undefined, { ...k.node.attrs, eff: null }); });
  return tr.setNodeMarkup(c.pos, undefined, { ...c.table.attrs, styleId, tblPr: c.table.attrs.tblPr });
};
/** Row properties on the selected rows: height {val, rule} (null = auto), cant = the row never breaks across pages. */
export const rowFmt = (patch: Record<string, unknown>): Op => (state) => {
  const c = ctxOf(state);
  if (!c) return null;
  const tr = state.tr;
  for (let r = c.rect.top; r < c.rect.bottom; r++) { const row = c.table.child(r); tr.setNodeMarkup(c.start + rowPos(c.table, r), undefined, { ...row.attrs, rfmt: noNull({ ...(row.attrs.rfmt ?? {}), ...patch }) }); }
  return tr;
};
/** Row heights (twips) as drawn. */
export function domRowHeights(view: EditorView, c: Ctx): number[] {
  const dom = view.nodeDOM(c.pos) as HTMLElement | null, z = dom ? zoomOf(dom) : 1;
  return Array.from({ length: c.map.height }, (_, r) => { const el = view.nodeDOM(c.start + rowPos(c.table, r)) as HTMLElement | null; return el ? Math.round(el.getBoundingClientRect().height / z * 15) : 0; });
}
export const distributeRows: Op = (state, view) => {
  const c = ctxOf(state);
  if (!c || !view) return null;
  const all = c.rect.bottom - c.rect.top < 2, lo = all ? 0 : c.rect.top, hi = all ? c.map.height : c.rect.bottom, h = domRowHeights(view, c).slice(lo, hi), avg = Math.round(sum(h) / h.length), tr = state.tr;
  for (let r = lo; r < hi; r++) { const row = c.table.child(r); tr.setNodeMarkup(c.start + rowPos(c.table, r), undefined, { ...row.attrs, rfmt: { ...(row.attrs.rfmt ?? {}), h: { val: avg, rule: row.attrs.rfmt?.h?.rule ?? "atLeast" } } }); }
  return tr;
};
export const distributeColumns: Op = (state, view) => {
  const c = ctxOf(state);
  if (!c) return null;
  const g = ensureGrid(view ?? null, c), all = c.rect.right - c.rect.left < 2;
  return applyGrid(state.tr, c.pos, distribute(g, all ? 0 : c.rect.left, all ? g.length : c.rect.right), { fixed: true });
};
export type WidthMode = { mode: "auto" } | { mode: "window" } | { mode: "fixed"; tw: number } | { mode: "pct"; pct: number } | { mode: "cols" };
/** Table width / layout: auto = autofit to contents, window = 100 % of the text area, fixed = a given width, pct = % of the text area. */
export const setWidth = (w: WidthMode): Op => (state, view) => {
  const c = ctxOf(state);
  if (!c) return null;
  const tw = view ? textWidth(view) : 9638, tr = state.tr;
  if (w.mode === "auto") return applyGrid(tr, c.pos, view ? autofitGrid(view, c) : ensureGrid(null, c), { width: { type: "auto", w: 0 }, layout: "autofit" });
  const g = ensureGrid(view ?? null, c);
  if (w.mode === "window") return applyGrid(tr, c.pos, scaleGrid(g, tw), { width: { type: "pct", w: 5000 }, layout: "autofit" });
  if (w.mode === "pct") return applyGrid(tr, c.pos, scaleGrid(g, Math.max(Math.round(tw * w.pct / 100), g.length * MIN_COL)), { width: { type: "pct", w: Math.round(w.pct * 50) }, layout: "fixed" });
  if (w.mode === "fixed") return applyGrid(tr, c.pos, scaleGrid(g, Math.max(w.tw, g.length * MIN_COL)), { fixed: true });
  return applyGrid(tr, c.pos, g, { fixed: true }); // "cols": fixed column widths
};
/** Set the grid (a finished drag): fixed layout, dxa width = the sum. */
export const setGrid = (grid: number[]): Op => (state) => { const c = ctxOf(state); return c ? applyGrid(state.tr, c.pos, grid, { fixed: true }) : null; };
export const selectTable: Op = (state) => {
  const c = ctxOf(state);
  return c ? state.tr.setSelection(new CellSelection(state.doc.resolve(c.start + c.map.map[0]), state.doc.resolve(c.start + c.map.map[c.map.map.length - 1]))) : null;
};
export const selectColumns = (from: number, to: number): Op => (state) => {
  const c = ctxOf(state);
  return c ? state.tr.setSelection(CellSelection.colSelection(state.doc.resolve(c.start + c.map.map[from]), state.doc.resolve(c.start + c.map.map[to]))) : null;
};
export const selectRows = (from: number, to: number): Op => (state) => {
  const c = ctxOf(state), W = c?.map.width ?? 0;
  return c ? state.tr.setSelection(CellSelection.rowSelection(state.doc.resolve(c.start + c.map.map[from * W]), state.doc.resolve(c.start + c.map.map[to * W + W - 1]))) : null;
};

/** What the ribbon shows about the table under the cursor. */
export function tableInfo(state: EditorState) {
  const c = ctxOf(state);
  if (!c) return null;
  const cells = selectedCells(state), a = c.table.attrs, tf = a.tfmt ?? {}, row = c.table.child(c.rect.top).attrs.rfmt ?? {}, cf = cells[0]?.node.attrs.cfmt ?? {};
  const p = cells[0]?.node.firstChild;
  return { rows: c.map.height, cols: c.map.width, top: c.rect.top, left: c.rect.left, nrows: c.rect.bottom - c.rect.top, ncols: c.rect.right - c.rect.left, dynamic: (a.dynamicList as string) ?? null,
    header: headerCount(c.table) > 0, merged: cells.length === 1 && ((cells[0].node.attrs.colspan ?? 1) > 1 || (cells[0].node.attrs.rowspan ?? 1) > 1), multi: cells.length > 1,
    jc: tf.jc ?? "left", width: tf.width ?? null, layout: tf.layout ?? "", styleId: (a.styleId as string) ?? "", rowH: row.h ?? null, cant: !!row.cant,
    cellJc: p?.attrs.pfmt?.jc ?? "left", vAlign: cf.vAlign ?? "top", shd: cf.shd ?? "", gridW: gridOf(c.table, c.map) };
}

// ---- keeping the grid honest after edits we do not control (paste, undo of partial steps) -----------------------
/** A table whose attrs.grid no longer matches its columns gets the grid its cells carry, else none (exporter and renderer then
 *  fall back to cell widths / auto layout). Only tables touched by the transaction are looked at. */
export const gridGuard = () => new Plugin({
  appendTransaction(trs, _old, state) {
    if (!trs.some((t) => t.docChanged)) return null;
    const fixes = new Map<number, number[] | null>();
    for (const t of trs) t.mapping.maps.forEach((m, i) => m.forEach((_a, _b, s, e) => {
      const to = trs.length ? t.mapping.slice(i + 1).map(e) : e, from = t.mapping.slice(i + 1).map(s);
      state.doc.nodesBetween(Math.min(from, state.doc.content.size), Math.min(Math.max(to, from), state.doc.content.size), (n, pos) => {
        if (n.type.name !== "table") return;
        const g = n.attrs.grid;
        if (Array.isArray(g)) { const map = TableMap.get(n); if (g.length !== map.width) fixes.set(pos, gridOf(n, map)); }
        return false;
      });
    }));
    if (!fixes.size) return null;
    const tr = state.tr;
    fixes.forEach((g, pos) => { const n = state.doc.nodeAt(pos)!; tr.setNodeMarkup(pos, undefined, { ...n.attrs, grid: g }); });
    return tr.setMeta("addToHistory", false);
  },
});
