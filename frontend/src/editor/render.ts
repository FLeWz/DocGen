// Pure content -> HTML for the live preview. Same classes and direct-format styles as the editor (extensions.ts), so one
// stylesheet (the base document's effective styles, see look.tsx) makes both look like the Word page.
// Everything user-controlled is escaped; style values are validated in fmt.ts.
import type { Style } from "../api";
import { effectiveStyle, PMNode } from "./doc";
import { cellStyle, esc, imgCss, listCss, pflags, pfmtCss, rowCss, runCss, safeId, tableCss } from "./fmt";
import { applyDateFilter, todayIso } from "../dates";
import { ooxmlToHtml } from "./ooxml";
import { opaqueWhy } from "./opaqueText";
import { tabbed, validStops } from "./tabs";
import { listEntries } from "./outline";

export { esc };
export const safeHref = (h: unknown) => (/^(https?:|mailto:)/i.test(String(h ?? "").trim()) ? String(h).trim() : "#");

/** Variable chip text: the default (filter applied) when set, else {{ name }}. */
export function variableText(a: Record<string, any>): string {
  const d = String(a.default ?? ""), f = String(a.filter ?? "");
  if (f.startsWith("date:")) return applyDateFilter(d || (a.name === "doc.date" ? todayIso() : ""), f.slice(5)) || `{{ ${a.name} }}`; // sample value
  if (!d) return `{{ ${a.name} }}`;
  return f === "upper" ? d.toUpperCase() : d;
}

const MARKS: Record<string, [string, string]> = { bold: ["<strong>", "</strong>"], italic: ["<em>", "</em>"], underline: ["<u>", "</u>"] };
const style = (s: string) => (s ? ` style="${esc(s)}"` : "");
const attrs = (o: Record<string, string>) => Object.entries(o).map(([k, v]) => ` ${k}="${esc(v)}"`).join("");
const TAB = "\u0001";

const notes = { items: [] as { kind: string; text: string }[] };  // footnotes met while rendering one document (see contentBlocks)
const roman = (n: number) => ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"][n - 1] ?? String(n);
const TOC_NAME: Record<string, string> = { toc: "Table of contents", tof: "List of figures", tot: "List of tables" };
const FLD = ["PAGE", "NUMPAGES", "SECTIONPAGES"];

function inline(n: PMNode): string {
  if (n.type === "field") {
    const a = n.attrs ?? {}, c = String(a.code ?? "");
    return `<span class="fld" data-fld="${esc(c)}">${FLD.includes(c) ? `‹${esc(c)}›` : esc(a.text ?? "")}</span>`;
  }
  if (n.type === "footnote") {
    notes.items.push({ kind: n.attrs?.kind === "endnote" ? "endnote" : "footnote", text: String(n.attrs?.text ?? "") });
    const k = n.attrs?.kind === "endnote" ? "endnote" : "footnote", num = notes.items.filter((x) => x.kind === k).length;
    return `<sup class="fn" title="${esc(n.attrs?.text)}">${k === "endnote" ? roman(num) : num}</sup>`;
  }
  if (n.type === "hardBreak") return n.attrs?.page ? '<span class="pgbrk"></span>' : "<br>";
  if (n.type === "variable") {
    const a = n.attrs ?? {};
    return `<span class="var-chip" title="${esc(`{{ ${a.name}${a.filter ? " | " + a.filter : ""} }}`)}">${esc(variableText(a))}</span>`;
  }
  const wrap = (txt: string) => {
    let h = esc(txt);
    for (const m of n.marks ?? []) {
      if (m.type === "link") h = `<a href="${esc(safeHref(m.attrs?.href))}" target="_blank" rel="noopener noreferrer">${h}</a>`;
      else if (m.type === "fmt") h = `<span${safeId(m.attrs?.rStyle) ? ` class="cs-${safeId(m.attrs?.rStyle)}"` : ""}${style(runCss(m.attrs))}>${h}</span>`;
      else if (MARKS[m.type]) h = MARKS[m.type][0] + h + MARKS[m.type][1];
    }
    return h;
  };
  return String(n.text ?? "").split("\t").map((t) => (t ? wrap(t) : "")).join(TAB); // tab markers stay outside the marks so a paragraph can split there
}

export type HtmlBlock = { html: string; sec: number };
/** Inline content of a paragraph; tab characters become a tab-stop layout when the paragraph has stops (direct or from its style). */
function inlineWithTabs(n: PMNode, stops: unknown): string {
  const h = (n.content ?? []).map(inline).join("");
  if (!h.includes(TAB)) return h;
  const st = validStops(stops);
  return st.length ? tabbed(h.split(TAB), st, Number(n.attrs?.pfmt?.left) || 0) : h.split(TAB).join("\t");
}

/** Top-level blocks as HTML plus the section each belongs to (a section-break block ends its section). */
export function contentBlocks(doc: PMNode | null | undefined, styles: Style[], media: Record<string, string> = {}, tabs: Record<string, unknown> = {}): HtmlBlock[] {
  const by = new Map(styles.map((s) => [s.style_id, s]));
  notes.items = [];
  const top = doc?.content ?? [];
  const count: number[] = [0, 0, 0, 0, 0, 0, 0, 0, 0];

  const kids = (n: PMNode, inList: boolean) => (n.content ?? []).map((c) => block(c, inList)).join("");
  function block(n: PMNode, inList = false): string {
    switch (n.type) {
      case "paragraph": {
        const id = safeId(effectiveStyle(n, inList)) ?? "Normal";
        return `<p class="st-${id}"${style(pfmtCss(n.attrs?.pfmt, inList))}${attrs(pflags(n.attrs?.pfmt))}>${inlineWithTabs(n, n.attrs?.pfmt?.tabs ?? tabs[id]) || "<br>"}</p>`;
      }
      case "heading": {
        const id = safeId(effectiveStyle(n)) ?? "Heading1", st = by.get(id), lvl = Math.min(Math.max(Number(n.attrs?.level) || 1, 1), 6);
        let num = "";
        if (st?.numbering === "decimal") {
          const d = Math.min(Math.max(st.level ?? lvl, 1), 9) - 1;
          count[d]++; count.fill(0, d + 1);
          num = `<span class="num">${count.slice(0, d + 1).map((c) => c || 1).join(".")}</span> `;
        }
        return `<h${lvl} class="st-${id}"${style(pfmtCss(n.attrs?.pfmt))}${attrs(pflags(n.attrs?.pfmt))}>${num}${inlineWithTabs(n, n.attrs?.pfmt?.tabs ?? tabs[id])}</h${lvl}>`;
      }
      case "bulletList": return `<ul${style(listCss(n.attrs?.listStyle))}>${kids(n, true)}</ul>`;
      case "orderedList": return `<ol${style(listCss(n.attrs?.listStyle))}>${kids(n, true)}</ol>`;
      case "listItem": return `<li>${kids(n, true)}</li>`;
      case "table": {
        const dyn = n.attrs?.dynamicList, sid = safeId(n.attrs?.styleId);
        const rows = (n.content ?? []).map((r, i, a) =>
          `<tr${dyn && i === a.length - 1 ? ' class="tpl"' : ""}${style(rowCss(r.attrs?.rfmt))}>${(r.content ?? []).map((c) => {
            const t = c.type === "tableHeader" ? "th" : "td", at = c.attrs ?? {};
            return `<${t}${at.colspan > 1 ? ` colspan="${+at.colspan}"` : ""}${at.rowspan > 1 ? ` rowspan="${+at.rowspan}"` : ""}${style(cellStyle(at))}>${kids(c, false)}</${t}>`;
          }).join("")}</tr>`).join("");
        const grid: number[] = Array.isArray(n.attrs?.grid) ? n.attrs!.grid.map(Number).filter((g: number) => g > 0 && g < 200000) : [];
        const gsum = grid.reduce((x, y) => x + y, 0);
        const cols = gsum ? `<colgroup>${grid.map((g) => `<col style="width:${+(g / gsum * 100).toFixed(2)}%">`).join("")}</colgroup>` : "";
        const tbl = `<table class="dtbl${sid ? ` ts-${sid}` : ""}"${style(tableCss(n.attrs?.tfmt, n.attrs?.grid))}>${cols}<tbody>${rows}</tbody></table>`;
        return dyn ? `<div><div class="dyn-badge">↻ rows repeat for each item of {{ ${esc(dyn)} }}</div>${tbl}</div>` : tbl; // one root element per block (pagination measures them)
      }
      case "image": {
        const a = n.attrs ?? {};
        return `<img class="img" src="/api/images/${esc(encodeURIComponent(a.imageId ?? ""))}" alt="${esc(a.alt)}"${style(imgCss(a))}>`;
      }
      case "toc": {
        const a = n.attrs ?? {};
        const name = esc(TOC_NAME[a.kind as string] ?? "Table of contents");
        const body = a.xml ? ooxmlToHtml(String(a.xml), media) : listEntries(top, a).map((e) => `<div class="toc-line toc-l${Math.min(Math.max(e.level, 1), 9)}"><span>${esc(e.text)}</span><i></i></div>`).join("") || `<div class="toc-empty">no entries yet</div>`;
        return `<div class="toc-block toc-${safeId(a.kind) ?? "toc"}"><div class="toc-h"><span class="toc-kind">${name}</span>${a.title ? ` <b>${esc(a.title)}</b>` : ""}</div>${body}</div>`;
      }
      case "sectBreak": return `<div class="sectbrk"><br></div>`;
      case "opaque": {
        const html = ooxmlToHtml(String(n.attrs?.xml ?? ""), media);
        if (!html && n.attrs?.kind === "section break") return '<div class="sectbrk"><br></div>'; // an empty paragraph in Word, no chip
        if (n.attrs?.kind === "bookmark") return "";
        const why = esc(opaqueWhy(String(n.attrs?.kind ?? "unknown")));
        return html ? `<div class="opaque-view" title="${why}">${html}</div>` : `<div class="opaque-chip" title="${why}">⬚ ${esc(n.attrs?.kind ?? "unknown")}</div>`;
      }
      default: return kids(n, inList);
    }
  }
  let sec = 0;
  const out = top.map((c) => {
    const o = { html: block(c), sec };
    if (c.type === "sectBreak" || (c.type === "opaque" && c.attrs?.kind === "section break")) sec++;
    return o;
  });
  if (notes.items.length) {  // footnotes/endnotes listed at the end (Word puts them at the foot of the page / end of the document)
    const cnt = { footnote: 0, endnote: 0 };
    out.push({ sec, html: `<div class="fnlist">${notes.items.map((x) => { const k = x.kind as "footnote" | "endnote", n = ++cnt[k]; return `<p class="fnitem"><sup>${k === "endnote" ? roman(n) : n}</sup> ${esc(x.text)}</p>`; }).join("")}</div>` });
  }
  return out;
}

/** HTML of an edited header/footer part (TipTap doc); PAGE/NUMPAGES placeholders are filled per page by pageParts. */
export const partDocHtml = (doc: PMNode, tabs: Record<string, unknown> = {}) => contentBlocks(doc, [], {}, tabs).map((b) => b.html).join("");

export const contentToHtml = (doc: PMNode | null | undefined, styles: Style[], media: Record<string, string> = {}, tabs: Record<string, unknown> = {}) =>
  contentBlocks(doc, styles, media, tabs).map((b) => b.html).join("");
