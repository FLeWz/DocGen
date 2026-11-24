// Table tools: the shared actions, the contextual "Table" ribbon tab, and the overlay on the editor page (floating toolbar,
// column/row selection strips, border/row/corner drag grips with live guides and tooltips, right-click / Shift+F10 menu).
// The overlay reads the DOM in visual px (CSS zoom already applied by getBoundingClientRect) and converts drag distances to
// model twips with the table's own zoom factor, so it works at any zoom level and across page-gap rows.
import type { Editor } from "@tiptap/core";
import { TextSelection } from "@tiptap/pm/state";
import { CellSelection, TableMap } from "@tiptap/pm/tables";
import { useEditorState } from "@tiptap/react";
import { useEffect, useRef, useState } from "react";
import { Style } from "../api";
import { useUI } from "../ui";
import { Btn } from "./Ribbon";
import * as T from "./tables";

type Ui = ReturnType<typeof useUI>;
const BORDER_STYLES = ["single", "double", "dotted", "dashed", "thick"];
const BORDER_SZ: [string, number][] = [["¼ pt", 2], ["½ pt", 4], ["¾ pt", 6], ["1 pt", 8], ["1½ pt", 12], ["2¼ pt", 18], ["3 pt", 24]];
const BORDER_MODES: [T.BorderMode, string][] = [["all", "All borders"], ["outside", "Outside"], ["inside", "Inside"], ["none", "No border"], ["top", "Top"], ["bottom", "Bottom"], ["left", "Left"], ["right", "Right"], ["insideH", "Inside horizontal"], ["insideV", "Inside vertical"]];

/** One entry point for every table command, so ribbon, floating toolbar and context menu behave the same (confirmations included). */
export function tableActions(editor: Editor, ui: Ui) {
  const r = (op: T.Op) => T.run(editor, op);
  const back = () => { setTimeout(() => editor.view.focus(), 60); }; // a closing dialog hands the focus back to the button that opened it
  const warn = (t: string) => ui.toast(t, "err");
  const need = (ok: boolean, t: string) => { if (!ok) warn(t); };
  const delTable = async () => {
    if (await ui.confirm({ title: "Delete the table?", danger: true, ok: "Delete table", body: "The whole table and its contents are removed (Undo brings it back)." })) { r(T.removeTable); back(); }
  };
  const delRows = async () => {
    const c = T.ctxOf(editor.state);
    if (!c) return;
    if (c.rect.top === 0 && c.rect.bottom === c.map.height) return delTable();
    if (c.table.attrs.dynamicList && c.rect.bottom === c.map.height && !(await ui.confirm({ title: "Delete the template row?", danger: true, ok: "Delete row",
      body: `The last row of a dynamic table is the row template repeated for each item of ${c.table.attrs.dynamicList}. Deleting it makes the row above the new template.` }))) return;
    r(T.removeRows); back();
  };
  const delCols = async () => {
    const c = T.ctxOf(editor.state);
    if (!c) return;
    if (c.rect.left === 0 && c.rect.right === c.map.width) return delTable();
    r(T.removeColumns);
  };
  return {
    delTable, delRows, delCols,
    rowAbove: () => r(T.insertRow(false)), rowBelow: () => r(T.insertRow(true)), colLeft: () => r(T.insertColumn(false)), colRight: () => r(T.insertColumn(true)),
    merge: () => need(r(T.merge), "Select two or more cells to merge"), split: () => need(r(T.unmerge), "Select a merged cell to split"),
    header: () => r(T.toggleHeader), clear: () => r(T.clearCells), dupRow: () => need(r(T.duplicateRow), "A row inside a vertical merge cannot be duplicated"),
    dupCol: () => need(r(T.duplicateColumn), "A column with merged cells cannot be duplicated"),
    moveRow: (d: -1 | 1) => need(r(T.moveRow(d)), "This row cannot move there (header boundary, merged cells or the dynamic template row)"),
    moveCol: (d: -1 | 1) => need(r(T.moveColumn(d)), "A column with merged cells cannot be moved"),
    sort: (asc: boolean) => need(r(T.sortRows(asc)), "Nothing to sort (or the table has vertical merges)"),
    align: (jc: string) => r(T.cellAlign(jc)), fmt: (p: Record<string, unknown>) => r(T.cellFmt(p)), borders: (m: T.BorderMode, s: T.Border | null) => r(T.setBorders(m, s)),
    toText: () => r(T.tableToText), toTable: () => need(r(T.textToTable), "Select some paragraphs outside a table first"),
    selectTable: () => r(T.selectTable), r,
    dynamic: () => window.dispatchEvent(new CustomEvent("docgen:cmd", { detail: "dynamic" })),
  };
}
type Actions = ReturnType<typeof tableActions>;

/** Number input in the configured unit: commits on Enter / blur, so typing does not rewrite the table per keystroke. */
function Num({ label, tw, unit, onCommit, off, w = 64 }: { label: string; tw: number | null; unit: T.Unit; onCommit: (tw: number | null) => void; off?: boolean; w?: number }) {
  const shown = tw ? String(+T.fromTwips(tw, unit).toFixed(2)) : "";
  const [v, setV] = useState(shown);
  useEffect(() => setV(shown), [shown]);
  const go = () => { if (v === shown) return; const n = parseFloat(v); onCommit(v.trim() === "" ? null : n > 0 ? T.toTwips(n, unit) : tw); };
  return <input type="number" step={unit === "pt" ? 1 : 0.1} min={0} aria-label={label} title={label} placeholder={label} style={{ width: w }} disabled={off} value={v}
    onChange={(e) => setV(e.target.value)} onBlur={go} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); go(); } }} />;
}

/** The contextual ribbon tab. */
export function TableTab({ editor, ui, off, styles, onCaption }: { editor: Editor; ui: Ui; off: boolean; styles: Style[]; onCaption: () => void }) {
  const a = tableActions(editor, ui), info = useEditorState({ editor, selector: ({ editor: e }) => T.tableInfo(e.state) });
  const [unit, setUnitState] = useState<T.Unit>(T.getUnit()), [bs, setBs] = useState({ val: "single", sz: 4, color: "#000000" }), [wUnit, setWUnit] = useState<string>("unit");
  if (!info) return null;
  const setUnit = (u: T.Unit) => { localStorage.setItem("docgen.unit", u); setUnitState(u); };
  const tw = wUnit === "pct" ? (info.width?.type === "pct" ? info.width.w / 50 * 20 : null) : info.width?.type === "dxa" ? info.width.w : info.gridW ? T.sum(info.gridW) : null;
  const I = (p: Parameters<typeof Btn>[0]) => <Btn {...p} off={p.off ?? off} />;
  const border = (m: T.BorderMode) => a.borders(m, { val: bs.val, sz: bs.sz, color: bs.color.slice(1).toUpperCase() });
  const tstyles = styles.filter((s) => s.kind === "table");
  const G = ({ label, children }: { label: string; children: React.ReactNode }) => <div className="rgroup" role="group" aria-label={label}><div className="rrow">{children}</div><div className="rlabel">{label}</div></div>;
  return <>
    <G label="Rows and columns">
      <I i="rowAdd" t="Insert row above" flip fn={a.rowAbove} /><I i="rowAdd" t="Insert row below" fn={a.rowBelow} />
      <I i="colAdd" t="Insert column left" flip fn={a.colLeft} /><I i="colAdd" t="Insert column right" fn={a.colRight} />
      <I i="rowDel" t="Delete row" fn={a.delRows} /><I i="colDel" t="Delete column" fn={a.delCols} /><I i="trash" t="Delete table" fn={a.delTable} />
      <I i="rowAdd" t="Duplicate row" fn={a.dupRow} /><I i="colAdd" t="Duplicate column" fn={a.dupCol} />
      <I i="chevU" t="Move row up (Alt+Shift+Up)" fn={() => a.moveRow(-1)} /><I i="chevD" t="Move row down (Alt+Shift+Down)" fn={() => a.moveRow(1)} />
      <I i="chevL" t="Move column left (Alt+Shift+Left)" fn={() => a.moveCol(-1)} /><I i="chevR" t="Move column right (Alt+Shift+Right)" fn={() => a.moveCol(1)} />
    </G>
    <G label="Merge">
      <I i="merge" t="Merge cells" off={off || !info.multi} fn={a.merge} /><I i="split" t="Split cell" off={off || !info.merged} fn={a.split} />
      <I i="rowAdd" t="Header row: repeated on every page" label="Header row" on={info.header} fn={a.header} />
      <I i="trash" t="Clear cell contents" label="Clear" fn={a.clear} />
    </G>
    <G label="Cell">
      <select aria-label="Cell horizontal alignment" title="Horizontal alignment" disabled={off} value={info.cellJc} onChange={(e) => a.align(e.target.value)}><option value="left">Left</option><option value="center">Centre</option><option value="right">Right</option><option value="both">Justified</option></select>
      <select aria-label="Cell vertical alignment" title="Vertical alignment" disabled={off} value={info.vAlign} onChange={(e) => a.fmt({ vAlign: e.target.value })}><option value="top">Top</option><option value="center">Middle</option><option value="bottom">Bottom</option></select>
      <input type="color" className="tb-color" aria-label="Cell shading" title="Cell shading" disabled={off} value={info.shd ? `#${info.shd}` : "#ffffff"} onChange={(e) => a.fmt({ shd: e.target.value.slice(1).toUpperCase() })} />
      <I i="close" t="No shading" label="No fill" fn={() => a.fmt({ shd: null })} />
      <Num label={`Cell padding (${unit})`} unit={unit} tw={null} w={78} off={off} onCommit={(v) => v !== null && a.r(T.cellMargin(["top", "left", "bottom", "right"], v))} />
    </G>
    <G label="Borders">
      <select aria-label="Border line style" title="Line style" disabled={off} value={bs.val} onChange={(e) => setBs({ ...bs, val: e.target.value })}>{BORDER_STYLES.map((s) => <option key={s}>{s}</option>)}</select>
      <select aria-label="Border line width" title="Line width" disabled={off} value={bs.sz} onChange={(e) => setBs({ ...bs, sz: +e.target.value })}>{BORDER_SZ.map(([l, v]) => <option key={v} value={v}>{l}</option>)}</select>
      <input type="color" className="tb-color" aria-label="Border colour" title="Line colour" disabled={off} value={bs.color} onChange={(e) => setBs({ ...bs, color: e.target.value })} />
      <select aria-label="Apply borders" title="Apply the line to…" disabled={off} value="" onChange={(e) => { if (e.target.value) border(e.target.value as T.BorderMode); }}><option value="">Apply to…</option>{BORDER_MODES.map(([m, l]) => <option key={m} value={m}>{l}</option>)}</select>
    </G>
    <G label="Table">
      <select aria-label="Table alignment" title="Table alignment" disabled={off} value={info.jc} onChange={(e) => a.r(T.setTfmt({ jc: e.target.value }))}><option value="left">Left</option><option value="center">Centre</option><option value="right">Right</option></select>
      <select aria-label="Autofit" title="Autofit" disabled={off} value="" onChange={(e) => { const m = e.target.value; a.r(T.setWidth(m === "auto" ? { mode: "auto" } : m === "window" ? { mode: "window" } : { mode: "cols" })); }}>
        <option value="">Autofit…</option><option value="auto">To contents</option><option value="window">To window</option><option value="cols">Fixed column width</option></select>
      <Num label={`Table width (${wUnit === "pct" ? "%" : unit})`} unit={wUnit === "pct" ? "pt" : unit} tw={tw} off={off} onCommit={(v) => v && a.r(T.setWidth(wUnit === "pct" ? { mode: "pct", pct: v / 20 } : { mode: "fixed", tw: v }))} w={72} />
      <select aria-label="Table width unit" title="Width as" disabled={off} value={wUnit} onChange={(e) => setWUnit(e.target.value)}><option value="unit">{unit}</option><option value="pct">% of page</option></select>
      <I i="colAdd" t="Distribute columns evenly" label="Even cols" fn={() => a.r(T.distributeColumns)} /><I i="rowAdd" t="Distribute rows evenly" label="Even rows" fn={() => a.r(T.distributeRows)} />
      <select aria-label="Table style" title="Table style" disabled={off} value={info.styleId} onChange={(e) => a.r(T.setTableStyle(e.target.value || null))}>
        <option value="">Style…</option>{info.styleId && !tstyles.some((s) => s.style_id === info.styleId) && <option value={info.styleId}>{info.styleId}</option>}{tstyles.map((s) => <option key={s.id} value={s.style_id}>{s.name}</option>)}</select>
    </G>
    <G label="Row">
      <Num label={`Row height (${unit})`} unit={unit} tw={info.rowH?.val ?? null} off={off} onCommit={(v) => a.r(T.rowFmt({ h: v ? { val: v, rule: info.rowH?.rule ?? "atLeast" } : null }))} w={72} />
      <select aria-label="Row height rule" title="Row height rule" disabled={off || !info.rowH} value={info.rowH?.rule ?? "atLeast"} onChange={(e) => a.r(T.rowFmt({ h: { ...info.rowH, rule: e.target.value } }))}><option value="atLeast">At least</option><option value="exact">Exactly</option></select>
      <I i="keep" t="Allow row to break across pages" label="Can break" on={!info.cant} fn={() => a.r(T.rowFmt({ cant: info.cant ? null : true }))} />
    </G>
    <G label="Data">
      <I i="chevU" t="Sort rows by this column, ascending" label="A→Z" fn={() => a.sort(true)} /><I i="chevD" t="Sort rows by this column, descending" label="Z→A" fn={() => a.sort(false)} />
      <I i="toc" t="Convert the table to text (cells separated by tabs)" label="To text" fn={a.toText} />
      <I i="caption" t="Insert a table caption (SEQ Table)" label="Caption" fn={onCaption} />
      <select aria-label="Units" title="Units for sizes and drag tooltips" value={unit} onChange={(e) => setUnit(e.target.value as T.Unit)}><option value="cm">cm</option><option value="in">in</option><option value="pt">pt</option></select>
    </G>
  </>;
}

// ---------------------------------------------------------------------------------------------------------------
type Geo = { pos: number; x: number[]; top: number[]; bot: number[]; z: number; W: number; H: number; box: DOMRect };
function geometry(editor: Editor, pos: number): Geo | null {
  const view = editor.view, node = view.state.doc.nodeAt(pos), tdom = node?.type.name === "table" ? (view.nodeDOM(pos) as HTMLElement | null) : null;
  if (!node || !tdom?.getBoundingClientRect) return null;
  const map = TableMap.get(node), W = map.width, H = map.height, x = Array(W + 1).fill(NaN), top = Array(H).fill(NaN), bot = Array(H).fill(NaN);
  for (const k of T.cellsOf(node, map)) {
    const el = view.nodeDOM(pos + 1 + k.pos) as HTMLElement | null;
    if (!el?.getBoundingClientRect) return null;
    const r = el.getBoundingClientRect();
    x[k.col] = isNaN(x[k.col]) ? r.left : Math.min(x[k.col], r.left); x[k.col + k.cs] = isNaN(x[k.col + k.cs]) ? r.right : Math.max(x[k.col + k.cs], r.right);
    top[k.row] = isNaN(top[k.row]) ? r.top : Math.min(top[k.row], r.top); bot[k.row + k.rs - 1] = isNaN(bot[k.row + k.rs - 1]) ? r.bottom : Math.max(bot[k.row + k.rs - 1], r.bottom);
  }
  if ([...x, ...top, ...bot].some(isNaN)) return null;
  return { pos, x, top, bot, z: T.zoomOf(tdom), W, H, box: tdom.getBoundingClientRect() };
}
/** Position of the table node that contains a DOM element (null for nested/opaque or non-table). */
function tablePosOf(editor: Editor, el: Element | null): number | null {
  const t = el?.closest("table");
  if (!t || !editor.view.dom.contains(t) || t.closest(".pgsep") || t.classList.contains("ox")) return null; // page header/footer tables are drawn, not part of the model
  try {
    const $p = editor.state.doc.resolve(editor.view.posAtDOM(t, 0));
    for (let d = $p.depth; d >= 0; d--) if ($p.node(d).type.name === "table") return $p.before(d);
  } catch { /* not in the editor model */ }
  return null;
}
const putCursor = (editor: Editor, pos: number, row: number, col: number) => {
  const node = editor.state.doc.nodeAt(pos)!, m = TableMap.get(node);
  editor.view.dispatch(editor.state.tr.setSelection(TextSelection.near(editor.state.doc.resolve(pos + 1 + m.map[row * m.width + col] + 1))));
};
const ctxAt = (editor: Editor, pos: number): T.Ctx => {
  const table = editor.state.doc.nodeAt(pos)!, map = TableMap.get(table);
  return { rect: { left: 0, top: 0, right: map.width, bottom: map.height, tableStart: pos + 1, map, table }, table, start: pos + 1, map, pos };
};
type Drag = { kind: "col" | "row" | "table"; at: number; text: string; apply: () => void };

export function TableTools({ editor, ro }: { editor: Editor; ro: boolean }) {
  const ui = useUI(), a = tableActions(editor, ui), root = useRef<HTMLDivElement>(null), anchor = useRef<[number, number]>([0, 0]);
  const [, setTick] = useState(0), [hover, setHover] = useState<number | null>(null), [menu, setMenu] = useState<{ x: number; y: number } | null>(null), [drag, setDrag] = useState<Drag | null>(null);
  const bump = () => setTick((n) => n + 1);
  const dragging = useRef(false);

  useEffect(() => {
    const pane = root.current?.parentElement;
    if (!pane) return;
    const onMove = (e: MouseEvent) => {
      if (dragging.current || root.current?.contains(e.target as Node)) return;
      const p = tablePosOf(editor, e.target as Element);
      setHover((h) => (h === p ? h : p));
    };
    const onCtx = (e: MouseEvent) => {
      const p = tablePosOf(editor, e.target as Element);
      if (p === null || ro) return;
      e.preventDefault();
      if (!(editor.state.selection instanceof CellSelection)) { const at = editor.view.posAtCoords({ left: e.clientX, top: e.clientY }); if (at) editor.view.dispatch(editor.state.tr.setSelection(TextSelection.near(editor.state.doc.resolve(at.pos)))); }
      setMenu({ x: e.clientX, y: e.clientY });
    };
    const onKey = () => { if (ro || !T.ctxOf(editor.state)) return; const c = editor.view.coordsAtPos(editor.state.selection.head); setMenu({ x: c.left, y: c.bottom + 2 }); };
    const redraw = () => bump();
    pane.addEventListener("mousemove", onMove); pane.addEventListener("contextmenu", onCtx); pane.addEventListener("mouseleave", () => setHover(null));
    window.addEventListener("docgen:tablemenu", onKey); window.addEventListener("resize", redraw); pane.addEventListener("scroll", redraw, true);
    editor.on("transaction", redraw);
    const ro2 = new ResizeObserver(redraw); ro2.observe(editor.view.dom);
    return () => { pane.removeEventListener("mousemove", onMove); pane.removeEventListener("contextmenu", onCtx); window.removeEventListener("docgen:tablemenu", onKey); window.removeEventListener("resize", redraw); pane.removeEventListener("scroll", redraw, true); editor.off("transaction", redraw); ro2.disconnect(); };
  }, [editor, ro]);

  const sel = T.ctxOf(editor.state), pos = hover ?? sel?.pos ?? null;
  const stage = root.current?.parentElement?.querySelector<HTMLElement>(".docx-stage"), clip = stage?.getBoundingClientRect();
  const geo = !ro && pos !== null && clip && editor.isEditable ? geometry(editor, pos) : null;
  const ox = clip?.left ?? 0, oy = clip?.top ?? 0;
  const U = T.getUnit();
  const toFirstCell = (p: number) => { if (sel?.pos !== p) putCursor(editor, p, 0, 0); };

  // ---- drags: everything is computed from the pointer start; the model changes once, on release (one undo step) ----
  const begin = (ev: React.PointerEvent, kind: Drag["kind"], idx: number) => {
    if (!geo) return;
    ev.preventDefault(); ev.stopPropagation();
    const target = ev.currentTarget as HTMLElement; target.setPointerCapture?.(ev.pointerId);
    toFirstCell(geo.pos);
    const c = ctxAt(editor, geo.pos), g0 = T.ensureGrid(editor.view, c), h0 = T.domRowHeights(editor.view, c), z = geo.z, sx = ev.clientX, sy = ev.clientY;
    const left0 = geo.x[0], w0 = T.sum(g0);
    dragging.current = true;
    let last: Drag | null = null;
    const move = (e: PointerEvent) => {
      const free = e.altKey;
      if (kind === "col") {
        const base = T.sum(g0.slice(0, idx)), d = T.snap(base + (e.clientX - sx) / z * 15, free) - base, g = T.resizeColumn(g0, idx - 1, d, e.shiftKey ? "keep" : "pair");
        last = { kind, at: left0 + T.sum(g.slice(0, idx)) / 15 * z, text: `Column ${idx}: ${T.fmtAll(g[idx - 1])} · table ${T.fmtLen(T.sum(g))}`, apply: () => T.run(editor, T.setGrid(g)) };
      } else if (kind === "row") {
        const h = Math.max(T.snap(h0[idx - 1] + (e.clientY - sy) / z * 15, free), 240), rule = (ctxAt(editor, geo.pos).table.child(idx - 1).attrs.rfmt?.h?.rule as string) ?? "atLeast";
        last = { kind, at: geo.bot[idx - 1] + (h - h0[idx - 1]) / 15 * z, text: `Row ${idx}: ${T.fmtAll(h)}${rule === "exact" ? " (exactly)" : " (at least)"}`,
          apply: () => { putCursor(editor, geo.pos, idx - 1, 0); T.run(editor, T.rowFmt({ h: { val: h, rule } })); } };
      } else {
        const tw = Math.max(T.snap(w0 + (e.clientX - sx) / z * 15, free), c.map.width * T.MIN_COL), g = T.scaleGrid(g0, tw), k = tw / w0;
        last = { kind, at: left0 + tw / 15 * z, text: `Table: ${T.fmtAll(tw)} wide`, apply: () => T.run(editor, T.setGrid(g)) };
      }
      setDrag(last);
    };
    const up = () => {
      target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); target.removeEventListener("pointercancel", up);
      dragging.current = false; setDrag(null);
      const l = last as Drag | null; if (l) l.apply();
    };
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up); target.addEventListener("pointercancel", up);
  };
  const autofitCol = (i: number) => {
    if (!geo) return;
    toFirstCell(geo.pos);
    const c = ctxAt(editor, geo.pos), g = T.ensureGrid(editor.view, c), auto = T.autofitGrid(editor.view, c), n = g.slice();
    n[i - 1] = auto[i - 1];
    T.run(editor, T.setGrid(n));
  };
  const pickCols = (i: number, e: React.MouseEvent) => { if (!geo) return; toFirstCell(geo.pos); const from = e.shiftKey ? anchor.current[0] : i; if (!e.shiftKey) anchor.current[0] = i; T.run(editor, T.selectColumns(Math.min(from, i), Math.max(from, i))); };
  const pickRows = (i: number, e: React.MouseEvent) => { if (!geo) return; toFirstCell(geo.pos); const from = e.shiftKey ? anchor.current[1] : i; if (!e.shiftKey) anchor.current[1] = i; T.run(editor, T.selectRows(Math.min(from, i), Math.max(from, i))); };
  const key = (fn: () => void) => (e: React.KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); } };
  const rel = (v: number, o: number) => v - o;

  return (
    <div ref={root} className="tt-root" data-testid="table-tools" onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.stopPropagation()} style={clip ? { left: clip.left, top: clip.top, width: clip.width, height: clip.height } : { display: "none" }}>
      {geo && (() => {
        const X0 = rel(geo.x[0], ox), X1 = rel(geo.x[geo.W], ox), Y0 = rel(geo.top[0], oy), Y1 = rel(geo.bot[geo.H - 1], oy), inClip = (y: number) => y > -2 && y < (clip?.height ?? 0) + 2;
        const tbTop = Math.max(Y0 - 62, 2);
        return <>
          <div className="tt-bar" data-testid="table-toolbar" role="toolbar" aria-label="Table tools" style={{ left: Math.max(X0, 2), top: tbTop }} onMouseDown={(e) => e.preventDefault()}>
            {sel && sel.pos === geo.pos ? <>
              <Btn i="rowAdd" t="Insert row above" flip fn={a.rowAbove} /><Btn i="rowAdd" t="Insert row below" fn={a.rowBelow} /><Btn i="colAdd" t="Insert column left" flip fn={a.colLeft} /><Btn i="colAdd" t="Insert column right" fn={a.colRight} />
              <Btn i="rowDel" t="Delete row" fn={a.delRows} /><Btn i="colDel" t="Delete column" fn={a.delCols} /><Btn i="merge" t="Merge cells" fn={a.merge} /><Btn i="split" t="Split cell" fn={a.split} />
              <Btn i="trash" t="Delete table" fn={a.delTable} /><Btn i="refresh" t="Dynamic rows…: header row → variables, body rows → default values" fn={a.dynamic} />
            </> : <span className="muted small">Click in the table to edit it</span>}
          </div>
          <div className="tt-corner" data-testid="table-select" role="button" tabIndex={0} aria-label="Select the whole table" title="Select the whole table" style={{ left: X0 - 17, top: Y0 - 17 }}
            onClick={() => { toFirstCell(geo.pos); a.selectTable(); }} onKeyDown={key(() => { toFirstCell(geo.pos); a.selectTable(); })} />
          {Array.from({ length: geo.W }, (_, i) => <div key={`c${i}`} className="tt-col" data-testid={`col-handle-${i + 1}`} role="button" tabIndex={0} aria-label={`Select column ${i + 1}`} title={`Select column ${i + 1} (Shift: range)`}
            style={{ left: rel(geo.x[i], ox), width: geo.x[i + 1] - geo.x[i], top: Y0 - 11 }} onClick={(e) => pickCols(i, e)} onKeyDown={key(() => pickCols(i, { shiftKey: false } as React.MouseEvent))} />)}
          {Array.from({ length: geo.H }, (_, i) => inClip(rel(geo.top[i], oy)) && <div key={`r${i}`} className="tt-row" data-testid={`row-handle-${i + 1}`} role="button" tabIndex={0} aria-label={`Select row ${i + 1}`} title={`Select row ${i + 1} (Shift: range)`}
            style={{ top: rel(geo.top[i], oy), height: geo.bot[i] - geo.top[i], left: X0 - 11 }} onClick={(e) => pickRows(i, e)} onKeyDown={key(() => pickRows(i, { shiftKey: false } as React.MouseEvent))} />)}
          {Array.from({ length: geo.W }, (_, i) => <div key={`g${i}`} className="tt-grip tt-vgrip" data-testid={`col-resize-${i + 1}`} title="Drag to resize (Shift: only this column, Alt: no snapping; double-click: autofit)"
            style={{ left: rel(geo.x[i + 1], ox) - 4, top: Y0, height: Y1 - Y0 }} onPointerDown={(e) => begin(e, "col", i + 1)} onDoubleClick={() => autofitCol(i + 1)} />)}
          {Array.from({ length: geo.H }, (_, i) => inClip(rel(geo.bot[i], oy)) && <div key={`h${i}`} className="tt-grip tt-hgrip" data-testid={`row-resize-${i + 1}`} title="Drag to change the row height (Alt: no snapping)"
            style={{ top: rel(geo.bot[i], oy) - 4, left: X0, width: X1 - X0 }} onPointerDown={(e) => begin(e, "row", i + 1)} />)}
          <div className="tt-size" data-testid="table-resize" title="Drag to resize the whole table" style={{ left: X1 - 5, top: Y1 - 5 }} onPointerDown={(e) => begin(e, "table", 0)} />
          {drag && <>
            <div className={`tt-guide ${drag.kind === "row" ? "h" : "v"}`} data-testid="drag-guide" style={drag.kind === "row" ? { top: rel(drag.at, oy), left: X0, width: X1 - X0 } : { left: rel(drag.at, ox), top: Y0, height: Y1 - Y0 }} />
            <div className="tt-tip" data-testid="drag-tooltip" role="status" style={{ left: (drag.kind === "row" ? X0 : rel(drag.at, ox)) + 8, top: (drag.kind === "row" ? rel(drag.at, oy) : Y0) - 28 }}>{drag.text}</div>
          </>}
        </>;
      })()}
      {menu && <TableMenu editor={editor} a={a} at={menu} unit={U} onClose={() => { setMenu(null); editor.view.focus(); }} />}
    </div>
  );
}

function TableMenu({ editor, a, at, onClose }: { editor: Editor; a: Actions; at: { x: number; y: number }; unit: T.Unit; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null), info = T.tableInfo(editor.state);
  useEffect(() => { ref.current?.querySelector<HTMLElement>("button:not(:disabled)")?.focus(); }, []);
  useEffect(() => {
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    window.addEventListener("mousedown", away, true);
    return () => window.removeEventListener("mousedown", away, true);
  }, [onClose]);
  if (!info) return null;
  const items: ([string, () => void, boolean?] | "-")[] = [
    ["Insert row above", a.rowAbove], ["Insert row below", a.rowBelow], ["Insert column left", a.colLeft], ["Insert column right", a.colRight], "-",
    ["Delete row", a.delRows], ["Delete column", a.delCols], ["Delete table…", a.delTable], "-",
    ["Merge cells", a.merge, !info.multi], ["Split cell", a.split, !info.merged], ["Clear contents", a.clear], "-",
    [info.dynamic ? "Dynamic table…" : "Make dynamic table…", a.dynamic], "-",
    ["Align left", () => a.align("left")], ["Align centre", () => a.align("center")], ["Align right", () => a.align("right")],
    [info.header ? "Header row off" : "Header row on", a.header], ["Duplicate row", a.dupRow], ["Distribute columns evenly", () => a.r(T.distributeColumns)],
  ];
  const nav = (e: React.KeyboardEvent) => {
    const btns = Array.from(ref.current!.querySelectorAll<HTMLElement>("button:not(:disabled)")), i = btns.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); btns[(i + 1) % btns.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length]?.focus(); }
    else if (e.key === "Tab") { e.preventDefault(); onClose(); }
  };
  return <div ref={ref} className="tt-menu" role="menu" aria-label="Table menu" data-testid="table-menu" style={{ left: Math.min(at.x, innerWidth - 220), top: Math.min(at.y, innerHeight - 440) }} onKeyDown={nav}>
    {items.map((it, i) => it === "-" ? <hr key={i} /> : <button key={i} type="button" role="menuitem" disabled={it[2]} onMouseDown={(e) => e.preventDefault()} onClick={() => { onClose(); it[1](); }}>{it[0]}</button>)}
  </div>;
}
