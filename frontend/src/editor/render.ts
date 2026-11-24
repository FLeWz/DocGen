// Pure content -> HTML for the live preview. Same classes and direct-format styles as the editor (extensions.ts), so one
// stylesheet (the base document's effective styles, see look.tsx) makes both look like the Word page.
// Everything user-controlled is escaped; style values are validated in fmt.ts.
import type { Style } from "../api";
import { effectiveStyle, PMNode } from "./doc";
import { cellCss, esc, imgCss, listCss, pfmtCss, runCss, safeId, tableCss } from "./fmt";
import { ooxmlToHtml } from "./ooxml";

export { esc };
export const safeHref = (h: unknown) => (/^(https?:|mailto:)/i.test(String(h ?? "").trim()) ? String(h).trim() : "#");

/** Variable chip text: the default (filter applied) when set, else {{ name }}. */
export function variableText(a: Record<string, any>): string {
  const d = String(a.default ?? "");
  if (!d) return `{{ ${a.name} }}`;
  return a.filter === "upper" ? d.toUpperCase() : d; // date filters need a runtime value: shown as default
}

const MARKS: Record<string, [string, string]> = { bold: ["<strong>", "</strong>"], italic: ["<em>", "</em>"], underline: ["<u>", "</u>"] };
const style = (s: string) => (s ? ` style="${esc(s)}"` : "");

function inline(n: PMNode): string {
  if (n.type === "hardBreak") return n.attrs?.page ? '<span class="pgbrk"></span>' : "<br>";
  if (n.type === "variable") {
    const a = n.attrs ?? {};
    return `<span class="var-chip" title="${esc(`{{ ${a.name}${a.filter ? " | " + a.filter : ""} }}`)}">${esc(variableText(a))}</span>`;
  }
  let h = esc(n.text);
  for (const m of n.marks ?? []) {
    if (m.type === "link") h = `<a href="${esc(safeHref(m.attrs?.href))}" target="_blank" rel="noopener noreferrer">${h}</a>`;
    else if (m.type === "fmt") h = `<span${safeId(m.attrs?.rStyle) ? ` class="cs-${safeId(m.attrs?.rStyle)}"` : ""}${style(runCss(m.attrs))}>${h}</span>`;
    else if (MARKS[m.type]) h = MARKS[m.type][0] + h + MARKS[m.type][1];
  }
  return h;
}

export function contentToHtml(doc: PMNode | null | undefined, styles: Style[], media: Record<string, string> = {}): string {
  const by = new Map(styles.map((s) => [s.style_id, s]));
  const count: number[] = [0, 0, 0, 0, 0, 0, 0, 0, 0];

  const kids = (n: PMNode, inList: boolean) => (n.content ?? []).map((c) => block(c, inList)).join("");
  function block(n: PMNode, inList = false): string {
    switch (n.type) {
      case "paragraph": {
        const id = safeId(effectiveStyle(n, inList)) ?? "Normal";
        return `<p class="st-${id}"${style(pfmtCss(n.attrs?.pfmt, inList))}>${(n.content ?? []).map(inline).join("") || "<br>"}</p>`;
      }
      case "heading": {
        const id = safeId(effectiveStyle(n)) ?? "Heading1", st = by.get(id), lvl = Math.min(Math.max(Number(n.attrs?.level) || 1, 1), 6);
        let num = "";
        if (st?.numbering === "decimal") {
          const d = Math.min(Math.max(st.level ?? lvl, 1), 9) - 1;
          count[d]++; count.fill(0, d + 1);
          num = `<span class="num">${count.slice(0, d + 1).map((c) => c || 1).join(".")}</span> `;
        }
        return `<h${lvl} class="st-${id}"${style(pfmtCss(n.attrs?.pfmt))}>${num}${(n.content ?? []).map(inline).join("")}</h${lvl}>`;
      }
      case "bulletList": return `<ul${style(listCss(n.attrs?.listStyle))}>${kids(n, true)}</ul>`;
      case "orderedList": return `<ol${style(listCss(n.attrs?.listStyle))}>${kids(n, true)}</ol>`;
      case "listItem": return `<li>${kids(n, true)}</li>`;
      case "table": {
        const dyn = n.attrs?.dynamicList, sid = safeId(n.attrs?.styleId);
        const rows = (n.content ?? []).map((r, i, a) =>
          `<tr${dyn && i === a.length - 1 ? ' class="tpl"' : ""}>${(r.content ?? []).map((c) => {
            const t = c.type === "tableHeader" ? "th" : "td", at = c.attrs ?? {};
            return `<${t}${at.colspan > 1 ? ` colspan="${+at.colspan}"` : ""}${at.rowspan > 1 ? ` rowspan="${+at.rowspan}"` : ""}${style(cellCss(at.cfmt))}>${kids(c, false)}</${t}>`;
          }).join("")}</tr>`).join("");
        const grid: number[] = Array.isArray(n.attrs?.grid) ? n.attrs!.grid.map(Number).filter((g: number) => g > 0 && g < 200000) : [];
        const gsum = grid.reduce((x, y) => x + y, 0);
        const cols = gsum ? `<colgroup>${grid.map((g) => `<col style="width:${+(g / gsum * 100).toFixed(2)}%">`).join("")}</colgroup>` : "";
        return `${dyn ? `<div class="dyn-badge">↻ rows repeat for each item of {{ ${esc(dyn)} }}</div>` : ""}<table class="tbl${sid ? ` ts-${sid}` : ""}"${style(tableCss(n.attrs?.tfmt, n.attrs?.grid))}>${cols}<tbody>${rows}</tbody></table>`;
      }
      case "image": {
        const a = n.attrs ?? {};
        return `<img class="img" src="/api/images/${esc(encodeURIComponent(a.imageId ?? ""))}" alt="${esc(a.alt)}"${style(imgCss(a))}>`;
      }
      case "opaque": {
        const html = ooxmlToHtml(String(n.attrs?.xml ?? ""), media);
        return html ? `<div class="opaque-view" title="Not editable: kept as-is in the exported DOCX">${html}</div>`
          : `<div class="opaque-chip" title="Not editable: kept as-is in the exported DOCX">⬚ ${esc(n.attrs?.kind ?? "unknown")}</div>`;
      }
      default: return kids(n, inList);
    }
  }
  return kids(doc ?? { type: "doc" }, false);
}
