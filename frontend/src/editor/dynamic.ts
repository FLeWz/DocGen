// "Make dynamic table" for a table that already has content: the header row names the columns (variables), the body rows become the
// DEFAULT VALUES of the list variable (attrs.dynamicRows, picked up by the backend as a declaration with defaults), and the first
// body row stays as the formatted row template with {{ row.column }} chips. Undo dynamic rebuilds the static rows from the defaults.
import { Fragment, Node as PMNode } from "@tiptap/pm/model";
import { TextSelection } from "@tiptap/pm/state";
import { ctxOf, headerCount, type Op } from "./tables";

export const LIST_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
/** Header text -> variable name: ascii letters/digits/_ , lower case, never starting with a digit, max 40. */
export const slug = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 36).replace(/^(\d)/, "_$1");
/** One name per header cell: slugified, empty ones col_N (their position), duplicates get _2, _3 ... */
export function columnNames(headers: string[]): string[] {
  const used = new Set<string>();
  return headers.map((h, i) => {
    const base = slug(h) || `col_${i + 1}`;
    let n = base, k = 1;
    while (used.has(n)) n = `${base}_${++k}`;
    used.add(n);
    return n;
  });
}

const textOf = (n: PMNode) => n.textBetween(0, n.content.size, "\n", (l) => (l.type.name === "hardBreak" ? "\n" : l.type.name === "variable" ? `{{ ${l.attrs.name} }}` : ""));
/** Cell text; paragraphs and line breaks both become "\n". */
export const cellText = (cell: PMNode) => textOf(cell).replace(/^\n+|\n+$/g, "");

export type Plan = { ok: true; body: number; headers: string[]; rows: string[][] } | { ok: false; reason: string };
/** Header row = the leading tblHeader row, else the first row. Merged cells in the header or body rows are refused (not flattened). */
export function analyzeTable(table: PMNode): Plan {
  const body = Math.max(headerCount(table), 1), width = table.child(0).childCount;
  let merged = false, ragged = false;
  table.forEach((row, _o, r) => {
    if (r !== 0 && r < body) return; // further header rows stay as they are
    if (row.childCount !== width) ragged = true;
    row.forEach((c) => { if ((c.attrs.colspan ?? 1) > 1 || (c.attrs.rowspan ?? 1) > 1) merged = true; });
  });
  if (merged) return { ok: false, reason: "The header or body rows have merged cells: converting them to list rows is refused. Split the cells first (the template row can still be made by hand)." };
  if (ragged) return { ok: false, reason: "The rows have different numbers of cells." };
  const rows: string[][] = [];
  for (let r = body; r < table.childCount; r++) { const out: string[] = []; table.child(r).forEach((c) => out.push(cellText(c))); rows.push(out); }
  const headers: string[] = [];
  table.child(0).forEach((c) => headers.push(cellText(c).split("\n")[0].trim()));
  return { ok: true, body, headers, rows };
}

const marksOf = (cell: PMNode) => { let m: readonly import("@tiptap/pm/model").Mark[] = []; cell.descendants((n) => { if (!m.length && n.isText && n.marks.length) m = n.marks; return !m.length; }); return m; };
const firstPara = (cell: PMNode) => { let p: PMNode | null = null; cell.forEach((n) => { if (!p && (n.type.name === "paragraph" || n.type.name === "heading")) p = n; }); return p as PMNode | null; };
const paraAttrs = (cell: PMNode) => { const p = firstPara(cell); return p ? { styleId: p.attrs.styleId, pfmt: p.attrs.pfmt } : null; };

/** Convert the table under the cursor: header stays, first body row (or a plain copy of the header) = template, others -> dynamicRows. */
export const makeDynamicFromRows = (name: string, cols: string[]): Op => (state) => {
  const c = ctxOf(state), plan = c && analyzeTable(c.table);
  if (!c || !plan || !plan.ok || c.table.attrs.dynamicList || cols.length !== c.map.width) return null;
  const S = state.schema, kept: PMNode[] = [];
  for (let r = 0; r < plan.body; r++) kept.push(c.table.child(r));
  const tplSrc = plan.body < c.table.childCount ? c.table.child(plan.body) : null, hdr = c.table.child(plan.body - 1);
  const cells: PMNode[] = [];
  (tplSrc ?? hdr).forEach((cell, _o, i) => {
    const chip = S.nodes.variable.create({ name: `row.${cols[i]}`, default: "", filter: null }, null, marksOf(cell));
    const attrs = { ...cell.attrs, ...(tplSrc ? {} : { cfmt: { ...(cell.attrs.cfmt ?? {}), shd: undefined } }) };
    cells.push(S.nodes.tableCell.create(attrs, S.nodes.paragraph.create(paraAttrs(cell), chip)));
  });
  const row = S.nodes.tableRow.create((tplSrc ?? hdr).attrs, cells);
  const rows = plan.rows.map((r) => Object.fromEntries(cols.map((k, i) => [k, r[i] ?? ""])));
  const tbl = c.table.type.create({ ...c.table.attrs, dynamicList: name, dynamicCols: cols, dynamicRows: rows }, Fragment.from([...kept, row]));
  const tr = state.tr.replaceWith(c.pos, c.pos + c.table.nodeSize, tbl);
  return tr.setSelection(TextSelection.near(tr.doc.resolve(c.pos + 3)));
};

/** Undo dynamic: the template row is repeated once per default row with the text filled in (chip marks kept); no defaults = just static. */
export const undoDynamic: Op = (state) => {
  const c = ctxOf(state);
  if (!c || !c.table.attrs.dynamicList) return null;
  const S = state.schema, rowsAttr = c.table.attrs.dynamicRows as Record<string, unknown>[] | null, last = c.table.lastChild!, kept: PMNode[] = [];
  for (let r = 0; r < c.table.childCount - 1; r++) kept.push(c.table.child(r));
  const out: PMNode[] = [];
  if (Array.isArray(rowsAttr)) {
    for (const data of rowsAttr) {
      const cells: PMNode[] = [];
      last.forEach((cell) => {
        let chip: PMNode | null = null;
        cell.descendants((n) => { if (!chip && n.type.name === "variable") chip = n; });
        const col = ((chip as PMNode | null)?.attrs.name as string | undefined)?.replace(/^row\./, ""), val = col ? String(data[col] ?? "") : "", marks = (chip as PMNode | null)?.marks ?? [];
        const inl: PMNode[] = [];
        val.split("\n").forEach((line, i) => { if (i) inl.push(S.nodes.hardBreak.create()); if (line) inl.push(S.text(line, marks)); });
        cells.push(cell.type.create(cell.attrs, S.nodes.paragraph.create(paraAttrs(cell), inl)));
      });
      out.push(S.nodes.tableRow.create(last.attrs, cells));
    }
  } else out.push(last);
  const tbl = c.table.type.create({ ...c.table.attrs, dynamicList: null, dynamicCols: null, dynamicRows: null }, Fragment.from([...kept, ...out]));
  const tr = state.tr.replaceWith(c.pos, c.pos + c.table.nodeSize, tbl);
  return tr.setSelection(TextSelection.near(tr.doc.resolve(c.pos + 3)));
};
