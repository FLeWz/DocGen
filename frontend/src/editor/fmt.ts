// Direct-formatting attrs (see backend/app/fmt.py) -> CSS. Pure and defensive: the JSON may come from any client, so every
// value is validated before it reaches a style attribute (hex colours, ints, font-name regex, enums).
export type Attrs = Record<string, any>;
export const esc = (s: unknown) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const HEX = /^[0-9A-Fa-f]{6}$/;
const FONT = /^[A-Za-z0-9 ._()-]{1,60}$/;
const num = (v: unknown, lo: number, hi: number) => { const n = Number(v); return Number.isFinite(n) && n >= lo && n <= hi ? n : null; };
const hex = (v: unknown) => (typeof v === "string" && HEX.test(v) ? `#${v}` : null);
const pt = (twips: number) => `${+(twips / 20).toFixed(2)}pt`;

// metric-compatible fallbacks for the usual Word fonts (same table as backend docx_style.FALLBACK)
const FALLBACK: Record<string, string> = { calibri: "Carlito", arial: "Liberation Sans, Arimo, Helvetica", "times new roman": "Liberation Serif, Tinos",
  "courier new": "Liberation Mono, Cousine", cambria: "Caladea", consolas: "monospace", verdana: "DejaVu Sans", tahoma: "DejaVu Sans", "segoe ui": "Open Sans" };
const GENERIC = ["serif", "sans-serif", "monospace"];
export function fontStack(name: string): string {
  const low = name.toLowerCase(), fb = (FALLBACK[low] ?? (/serif|times|garamond/.test(low) ? "serif" : "sans-serif")).split(",").map((s) => s.trim());
  const q = (f: string) => (GENERIC.includes(f) ? f : `'${f}'`);
  return [`'${name}'`, ...fb.map(q), ...(GENERIC.includes(fb[fb.length - 1]) ? [] : ["sans-serif"])].join(", ");
}

/** Word "multiple" line spacing: single = the font's own line height (`normal`); other multiples scale a typical 1.15 (browsers cannot scale `normal`). */
export const lineHeight = (line: number) => (line === 240 ? "line-height:normal" : `line-height:${+(line / 240 * 1.15).toFixed(3)}`);

export function borderCss(bd: Attrs | undefined, prop = "border"): string[] {
  const out: string[] = [];
  for (const side of ["top", "left", "bottom", "right"]) {
    const b = bd?.[side];
    if (!b || typeof b !== "object") continue;
    const sz = num(b.sz, 0, 96);
    if (b.val === "none" || b.val === "nil" || !sz) out.push(`${prop}-${side}:none`);
    else out.push(`${prop}-${side}:${Math.max(sz / 8, 0.5)}pt ${["double", "dotted", "dashed"].includes(b.val) ? b.val : "solid"} ${hex(b.color) ?? "#000"}`);
  }
  return out;
}

/** `fmt` mark attrs {color,font,size,hl,strike,va,caps,smallCaps} -> declarations. */
export function runCss(f?: Attrs | null): string {
  if (!f) return "";
  const o: string[] = [];
  if (typeof f.font === "string" && FONT.test(f.font)) o.push(`font-family:${fontStack(f.font)}`);
  const sz = num(f.size, 2, 3276);
  if (sz) o.push(`font-size:${sz / 2}pt`);
  if (hex(f.color)) o.push(`color:${hex(f.color)}`);
  if (hex(f.hl)) o.push(`background:${hex(f.hl)}`);
  if (f.strike === true) o.push("text-decoration:line-through");
  if (f.caps === true) o.push("text-transform:uppercase");
  if (f.smallCaps === true) o.push("font-variant:small-caps");
  if (f.va === "superscript" || f.va === "subscript") o.push(`vertical-align:${f.va === "superscript" ? "super" : "sub"};font-size:smaller`);
  return o.join(";");
}

/** `pfmt` -> declarations. In list items the list itself indents, so the paragraph's own indent is skipped. */
export function pfmtCss(p?: Attrs | null, inList = false): string {
  if (!p) return "";
  const o: string[] = [];
  if (["left", "right", "center", "both"].includes(p.jc)) o.push(`text-align:${p.jc === "both" ? "justify" : p.jc}`);
  const ind: [string, string][] = [["left", "margin-left"], ["right", "margin-right"]];
  for (const [k, c] of ind) { const n = num(p[k], -31680, 31680); if (n !== null && !(inList && k === "left")) o.push(`${c}:${pt(n)}`); }
  for (const [k, c] of [["before", "margin-top"], ["after", "margin-bottom"]]) { const n = num(p[k], 0, 31680); if (n !== null) o.push(`${c}:${pt(n)}`); }
  const first = num(p.first, -31680, 31680);
  if (first !== null && !inList) o.push(`text-indent:${pt(first)}`);
  const line = num(p.line, 0, 31680);
  if (line) o.push(p.lineRule === "exact" || p.lineRule === "atLeast" ? `line-height:${pt(line)}` : lineHeight(line));
  if (hex(p.shd)) o.push(`background:${hex(p.shd)}`);
  o.push(...borderCss(p.bdr));
  return o.join(";");
}

/** Cell attrs -> declarations. Used for both the derived `eff` (table style resolved by the backend, see tblstyle.py) and the
 *  direct `cfmt`; extra keys of eff: mar (padding), dir (text direction), r (run props of the style), p.jc. */
export function cellCss(c?: Attrs | null): string {
  if (!c) return "";
  const o: string[] = [];
  if (hex(c.shd)) o.push(`background:${hex(c.shd)}`);
  if (["top", "center", "bottom"].includes(c.vAlign)) o.push(`vertical-align:${c.vAlign === "center" ? "middle" : c.vAlign}`);
  o.push(...borderCss(c.bdr));
  for (const side of ["top", "left", "bottom", "right"]) { const n = num(c.mar?.[side], 0, 31680); if (n !== null) o.push(`padding-${side}:${pt(n)}`); }
  if (c.dir === "btLr") o.push("writing-mode:sideways-lr"); else if (c.dir === "tbRl") o.push("writing-mode:vertical-rl");
  if (c.r) o.push(runCss(c.r), c.r.b ? "font-weight:bold" : "", c.r.i ? "font-style:italic" : "", c.r.u ? "text-decoration:underline" : "");
  if (["left", "right", "center", "both"].includes(c.p?.jc)) o.push(`text-align:${c.p.jc === "both" ? "justify" : c.p.jc}`);
  return o.filter(Boolean).join(";");
}

/** Derived (style) formatting first, direct formatting on top. */
export const cellStyle = (a?: Attrs | null) => [cellCss(a?.eff), cellCss(a?.cfmt)].filter(Boolean).join(";");

/** Row height (trHeight): a table row grows with its content, so `height` acts as Word's atLeast; `exact` also clips. */
export function rowCss(r?: Attrs | null): string {
  const h = num(r?.h?.val, 1, 31680);
  return h ? `height:${pt(h)}${r?.h?.rule === "exact" ? ";overflow:hidden" : ""}` : "";
}

/** Paragraph flags the pagination reads from the DOM (data attributes; the styles' own flags come from the look). */
export const pflags = (p?: Attrs | null): Record<string, string> => ({ ...(p?.keepNext ? { "data-kn": "1" } : {}), ...(p?.pageBreakBefore ? { "data-pbb": "1" } : {}), ...(p?.keepLines ? { "data-kl": "1" } : {}) });

/** Table-level: border custom properties (consumed by editor.css, so cells in the first/last row/col get the outer border),
 *  width and alignment. `grid` = column widths in twips (fixed layout when known). */
export function tableCss(tfmt?: Attrs | null, grid?: unknown): string {
  const o: string[] = [];
  for (const k of ["top", "left", "bottom", "right", "insideH", "insideV"]) {
    const b = tfmt?.bdr?.[k];
    if (b && typeof b === "object") o.push(`--b-${k}:${borderCss({ top: b })[0].split(":").slice(1).join(":")}`);
  }
  const w = tfmt?.width, g = Array.isArray(grid) && grid.every((x) => num(x, 1, 200000)) ? (grid as number[]).reduce((a, b) => a + b, 0) : 0;
  if (w && num(w.w, 1, 200000)) o.push(w.type === "pct" ? `width:${+(w.w / 50)}%` : `width:${pt(w.w)}`);
  else if (g) o.push(`width:${pt(g)}`);
  if (g || w) o.push("table-layout:fixed");
  const ind = num(tfmt?.ind, -31680, 31680);
  if (ind !== null && !tfmt?.jc) o.push(`margin-left:${pt(ind)}`);
  if (tfmt?.jc === "right") o.push("margin-left:auto");
  if (tfmt?.jc === "center") o.push("margin-left:auto;margin-right:auto");
  return o.join(";");
}

export const LIST_STYLES = ["disc", "circle", "square", "decimal", "lower-alpha", "upper-alpha", "lower-roman", "upper-roman", "decimal-leading-zero"];
export const listCss = (s: unknown) => (LIST_STYLES.includes(s as string) ? `list-style-type:${s}` : "");
const SID = /^[A-Za-z0-9_-]{1,60}$/;
export const safeId = (s: unknown) => (typeof s === "string" && SID.test(s) ? s : null);
export const imgCss = (a: Attrs) => {
  const w = num(a.width, 1, 5000), h = num(a.height, 1, 5000);
  return [w ? `width:${w}px` : "", w && h ? `height:${h}px` : "", a.align === "center" ? "margin-left:auto;margin-right:auto" : a.align === "right" ? "margin-left:auto" : ""].filter(Boolean).join(";");
};
