// Faithful-ish HTML for raw OOXML blocks the editor cannot edit (text boxes, fields, TOC, content controls, header/footer).
// Read-only approximation: text, direct run/paragraph formatting, tables, images (from a server-provided rId -> data: URI map),
// complex/simple fields shown as their cached result. Everything is built from escaped text and validated style values.
import { Attrs, cellCss, esc, pfmtCss, runCss, safeId, tableCss } from "./fmt";

const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing";
const A = "http://schemas.openxmlformats.org/drawingml/2006/main";
const R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const MC = "http://schemas.openxmlformats.org/markup-compatibility/2006";
const TAB = "\u0001";
let MEDIA: Record<string, string> = {};
/** rId -> data: URI of the images of the document body (set by the editor from the look; opaque blocks reference them). */
export const setMedia = (m: Record<string, string> | undefined) => { MEDIA = m ?? {}; };
export const getMedia = () => MEDIA;
const kids = (e: Element) => Array.from(e.children);
const ln = (e: Element) => e.localName;
const w = (e: Element | null | undefined, name: string) => (e ? kids(e).find((c) => c.namespaceURI === W && ln(c) === name) ?? null : null);
const wa = (e: Element | null | undefined, k: string) => e?.getAttributeNS(W, k) ?? null;
const on = (e: Element | null) => !!e && !["0", "false", "off"].includes(wa(e, "val") ?? "true");
const int = (s: string | null) => (s !== null && /^-?\d{1,6}$/.test(s) ? parseInt(s, 10) : undefined);
const hx = (s: string | null) => (s && /^[0-9A-Fa-f]{6}$/.test(s) ? s.toUpperCase() : undefined);
const EMU_PX = 9525;

function border(e: Element | null): Attrs | undefined {
  if (!e) return undefined;
  return { val: ["single", "double", "dotted", "dashed", "none", "nil"].includes(wa(e, "val") ?? "") ? wa(e, "val") : "single", sz: int(wa(e, "sz")) ?? 4, color: hx(wa(e, "color")) };
}
const borders = (e: Element | null, names: string[]) => {
  const o: Attrs = {};
  for (const n of names) { const b = border(w(e, n) ?? (n === "left" ? w(e, "start") : n === "right" ? w(e, "end") : null)); if (b) o[n] = b; }
  return o;
};
const SIDES = ["top", "left", "bottom", "right"];

export function runAttrs(rpr: Element | null): { f: Attrs; b: boolean; i: boolean; u: boolean } {
  const f: Attrs = {};
  if (!rpr) return { f, b: false, i: false, u: false };
  const c = hx(wa(w(rpr, "color"), "val")); if (c) f.color = c;
  const rf = w(rpr, "rFonts"); const font = wa(rf, "ascii") ?? wa(rf, "hAnsi"); if (font) f.font = font;
  const sz = int(wa(w(rpr, "sz"), "val")); if (sz) f.size = sz;
  const hl = wa(w(rpr, "highlight"), "val");
  const HL: Record<string, string> = { yellow: "FFFF00", green: "00FF00", cyan: "00FFFF", magenta: "FF00FF", blue: "0000FF", red: "FF0000", lightGray: "C0C0C0", darkGray: "808080", black: "000000", white: "FFFFFF" };
  const shd = hx(wa(w(rpr, "shd"), "fill"));
  if (hl && HL[hl]) f.hl = HL[hl]; else if (shd) f.hl = shd;
  if (on(w(rpr, "strike")) || on(w(rpr, "dstrike"))) f.strike = true;
  if (on(w(rpr, "caps"))) f.caps = true;
  if (on(w(rpr, "smallCaps"))) f.smallCaps = true;
  const va = wa(w(rpr, "vertAlign"), "val"); if (va === "superscript" || va === "subscript") f.va = va;
  const u = w(rpr, "u");
  return { f, b: on(w(rpr, "b")), i: on(w(rpr, "i")), u: !!u && !["none", "0"].includes(wa(u, "val") ?? "") };
}

export function paraAttrs(ppr: Element | null): Attrs {
  const p: Attrs = {};
  if (!ppr) return p;
  const jc = wa(w(ppr, "jc"), "val");
  const JC: Record<string, string> = { left: "left", start: "left", right: "right", end: "right", center: "center", both: "both" };
  if (jc && JC[jc]) p.jc = JC[jc];
  const ind = w(ppr, "ind");
  if (ind) {
    const l = int(wa(ind, "left") ?? wa(ind, "start")), r = int(wa(ind, "right") ?? wa(ind, "end")), h = int(wa(ind, "hanging")), fl = int(wa(ind, "firstLine"));
    if (l !== undefined) p.left = l; if (r !== undefined) p.right = r;
    if (h) p.first = -h; else if (fl) p.first = fl;
  }
  const sp = w(ppr, "spacing");
  if (sp) for (const k of ["before", "after", "line"]) { const n = int(wa(sp, k)); if (n !== undefined) p[k] = n; }
  if (sp && ["auto", "exact", "atLeast"].includes(wa(sp, "lineRule") ?? "")) p.lineRule = wa(sp, "lineRule");
  const sh = hx(wa(w(ppr, "shd"), "fill")); if (sh) p.shd = sh;
  const bd = borders(w(ppr, "pBdr"), SIDES); if (Object.keys(bd).length) p.bdr = bd;
  return p;
}

type Ctx = { media: Record<string, string>; stack: ("instr" | "result")[]; rowsOf?: unknown };
const visible = (c: Ctx) => c.stack.every((s) => s === "result");

function image(d: Element, c: Ctx): string {
  const blip = Array.from(d.getElementsByTagNameNS(A, "blip"))[0];
  const rid = blip?.getAttributeNS(R, "embed") ?? "";
  const src = /^[A-Za-z0-9_]{1,40}$/.test(rid) ? c.media[rid] : undefined;
  if (!src || !src.startsWith("data:image/")) return "";
  const ext = Array.from(d.getElementsByTagNameNS(WP, "extent"))[0];
  const cx = int(ext?.getAttribute("cx") ?? null), cy = int(ext?.getAttribute("cy") ?? null);
  const st = cx && cy ? ` style="width:${Math.round(cx / EMU_PX)}px;height:${Math.round(cy / EMU_PX)}px"` : "";
  return `<img src="${esc(src)}" alt=""${st}>`;
}

function textbox(d: Element, c: Ctx): string {
  const tb = Array.from(d.getElementsByTagNameNS(W, "txbxContent"))[0];
  if (!tb) return image(d, c);
  const ext = Array.from(d.getElementsByTagNameNS(WP, "extent"))[0];
  const cx = int(ext?.getAttribute("cx") ?? null);
  const sp = Array.from(d.getElementsByTagNameNS("http://schemas.microsoft.com/office/word/2010/wordprocessingShape", "spPr"))[0];
  const fill = hx(sp?.getElementsByTagNameNS(A, "srgbClr")[0]?.getAttribute("val") ?? null);
  const st = [cx ? `width:${Math.round(cx / EMU_PX)}px` : "", fill && sp && !w(sp, "noFill") && !sp.getElementsByTagNameNS(A, "noFill").length ? `background:#${fill}` : ""].filter(Boolean).join(";");
  return `<div class="txbx" style="${esc(st)}">${blocks(tb, c)}</div>`;
}

function run(r: Element, c: Ctx): string {
  const { f, b, i, u } = runAttrs(w(r, "rPr"));
  const rs = safeId(wa(w(w(r, "rPr"), "rStyle"), "val"));
  let out = "";
  for (const ch of kids(r)) {
    const n = ln(ch);
    if (ch.namespaceURI === W) {
      if (n === "fldChar") {
        const t = wa(ch, "fldCharType");
        if (t === "begin") c.stack.push("instr"); else if (t === "separate") { if (c.stack.length) c.stack[c.stack.length - 1] = "result"; } else if (t === "end") c.stack.pop();
      } else if (!visible(c)) continue;
      else if (n === "t") out += esc(ch.textContent);
      else if (n === "tab") out += TAB;
      else if (n === "noBreakHyphen") out += "-";
      else if (n === "br") out += wa(ch, "type") === "page" ? '<span class="pgbrk"></span>' : "<br>";
      else if (n === "footnoteReference" || n === "endnoteReference") out += `<sup class="fnref">${esc(wa(ch, "id"))}</sup>`;
      else if (n === "drawing") out += drawing(ch, c);
      else if (n === "pict") out += textbox(ch, c);
    } else if (ch.namespaceURI === MC && n === "AlternateContent") {
      const choice = kids(ch).find((x) => ln(x) === "Choice") ?? kids(ch)[0];
      if (choice && visible(c)) for (const x of kids(choice)) out += x.namespaceURI === W && ln(x) === "drawing" ? drawing(x, c) : "";
    }
  }
  if (!out) return "";
  const style = [runCss(f), u ? "text-decoration:underline" : ""].filter(Boolean).join(";");
  const wrap = (x: string) => {
    let h = `<span${rs ? ` class="cs-${rs}"` : ""}${style ? ` style="${esc(style)}"` : ""}>${x}</span>`;
    if (b) h = `<strong>${h}</strong>`;
    if (i) h = `<em>${h}</em>`;
    return h;
  };
  return out.split(TAB).map((x) => (x ? wrap(x) : "")).join(TAB); // tab markers stay outside the spans so a paragraph can split there
}
const drawing = (d: Element, c: Ctx) => (d.getElementsByTagNameNS(W, "txbxContent").length ? textbox(d, c) : image(d, c));

function inline(parent: Element, c: Ctx): string {
  let out = "";
  for (const ch of kids(parent)) {
    if (ch.namespaceURI !== W) continue;
    const n = ln(ch);
    if (n === "r") out += run(ch, c);
    else if (["hyperlink", "fldSimple", "smartTag", "ins", "customXml"].includes(n)) out += inline(ch, c);
    else if (n === "sdt") { const sc = w(ch, "sdtContent"); if (sc) out += inline(sc, c); }
  }
  return out;
}

function para(p: Element, c: Ctx): string {
  const ppr = w(p, "pPr"), sid = safeId(wa(w(ppr, "pStyle"), "val"));
  const attrs = paraAttrs(ppr);
  let html = inline(p, c);
  const tabs = kids(w(ppr, "tabs") ?? p).filter((t) => ln(t) === "tab" && wa(t, "val") === "right");
  const toc = !!sid && /^(TOC\d?|TableofFigures|TOCHeading)$/i.test(sid);
  const cls = `${sid ? `st-${sid}` : ""}`;
  const style = pfmtCss(attrs);
  if (html.includes(TAB) && (tabs.length || toc)) {  // right tab with leader: text | dots | page number
    const at = html.lastIndexOf(TAB), left = html.slice(0, at).split(TAB).join("&emsp;"), right = html.slice(at + 1);
    return `<p class="${cls} tabbed" style="${esc(style)}"><span>${left}</span><span class="lead"></span><span>${right}</span></p>`;
  }
  html = html.split(TAB).join("&emsp;");
  return `<p class="${cls}" style="${esc(style)}">${html || "<br>"}</p>`;
}

function table(t: Element, c: Ctx): string {
  const pr = w(t, "tblPr");
  const tf: Attrs = {}, bd = borders(w(pr, "tblBorders"), [...SIDES, "insideH", "insideV"]);
  if (Object.keys(bd).length) tf.bdr = bd;
  const jc = wa(w(pr, "jc"), "val"); if (jc === "right" || jc === "center") tf.jc = jc;
  const tw = w(pr, "tblW"), tww = int(wa(tw, "w")); if (tww && (wa(tw, "type") === "dxa" || wa(tw, "type") === "pct")) tf.width = { w: tww, type: wa(tw, "type") };
  const grid = kids(w(t, "tblGrid") ?? t).filter((g) => ln(g) === "gridCol").map((g) => int(wa(g, "w")) ?? 0);
  const rows = kids(t).filter((r) => ln(r) === "tr").map((tr) => {
    let col = 0;
    return kids(tr).filter((x) => ln(x) === "tc").map((tc) => {
      const cp = w(tc, "tcPr"), span = Math.max(int(wa(w(cp, "gridSpan"), "val")) ?? 1, 1), vm = w(cp, "vMerge");
      const o = { tc, col, span, vm: vm ? (wa(vm, "val") === "restart" ? "restart" : "cont") : null };
      col += span;
      return o;
    });
  });
  const body = rows.map((cells, ri) => `<tr>${cells.map((cell) => {
    if (cell.vm === "cont") return "";
    let rs = 1;
    if (cell.vm === "restart") for (let k = ri + 1; k < rows.length && rows[k].some((x) => x.col === cell.col && x.vm === "cont"); k++) rs++;
    const cp = w(cell.tc, "tcPr"), cf: Attrs = {};
    const sh = hx(wa(w(cp, "shd"), "fill")); if (sh) cf.shd = sh;
    const va = wa(w(cp, "vAlign"), "val"); if (va === "top" || va === "center" || va === "bottom") cf.vAlign = va;
    const cb = borders(w(cp, "tcBorders"), SIDES); if (Object.keys(cb).length) cf.bdr = cb;
    return `<td${cell.span > 1 ? ` colspan="${cell.span}"` : ""}${rs > 1 ? ` rowspan="${rs}"` : ""} style="${esc(cellCss(cf))}">${blocks(cell.tc, c)}</td>`;
  }).join("")}</tr>`).join("");
  const gsum = grid.reduce((x, y) => x + y, 0);  // percentages: a grid wider than the page shrinks proportionally, like LibreOffice does
  const cols = gsum ? `<colgroup>${grid.map((g) => `<col style="width:${+(g / gsum * 100).toFixed(2)}%">`).join("")}</colgroup>` : "";
  const sid = safeId(wa(w(pr, "tblStyle"), "val"));
  return `<table class="tbl${sid ? ` ts-${sid}` : ""}" style="${esc(tableCss(tf, grid.length ? grid : null))}">${cols}${body}</table>`;
}

function blocks(parent: Element, c: Ctx): string {
  let out = "";
  for (const ch of kids(parent)) {
    if (ch.namespaceURI !== W) {
      if (ch.namespaceURI === MC && ln(ch) === "AlternateContent") out += blocks(kids(ch).find((x) => ln(x) === "Choice") ?? ch, c);
      continue;
    }
    const n = ln(ch);
    if (n === "p") out += para(ch, c);
    else if (n === "tbl") out += table(ch, c);
    else if (n === "sdt") { const sc = w(ch, "sdtContent"); if (sc) out += blocks(sc, c); }
    else if (n === "txbxContent" || n === "body" || n === "hdr" || n === "ftr") out += blocks(ch, c);
  }
  return out;
}

/** Root may be a single block (w:p, w:tbl, w:sdt), a header/footer part (w:hdr, w:ftr) or a fragment. "" when nothing visible. */
export function ooxmlToHtml(xml: string, media: Record<string, string> = {}): string {
  let doc: Document;
  try { doc = new DOMParser().parseFromString(xml, "application/xml"); } catch { return ""; }
  const root = doc.documentElement;
  if (!root || doc.getElementsByTagName("parsererror").length) return "";
  const c: Ctx = { media, stack: [] };
  const wrap = { children: [root] } as unknown as Element;
  const html = ["hdr", "ftr", "body"].includes(ln(root)) ? blocks(root, c) : blocks(wrap, c);
  return html.replace(/<p class="[^"]*" style="">(<br>)?<\/p>/g, "").trim() ? html : "";
}
