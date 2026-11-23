// Pure content -> HTML for the live preview. Mimics the DOCX look from the resolved style set.
// Everything user-controlled is escaped; style values are validated server-side and escaped again here.
import type { Style } from "../api";
import { effectiveStyle, PMNode } from "./doc";

export const esc = (s: unknown) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

export const safeHref = (h: unknown) => (/^(https?:|mailto:)/i.test(String(h ?? "").trim()) ? String(h).trim() : "#");

const css = (s?: Style) => !s ? "" : [
  s.color && `color:${s.color}`, s.font_size && `font-size:${s.font_size}pt`, s.font_family && `font-family:'${s.font_family}',sans-serif`,
  s.bold && "font-weight:bold", s.italic && "font-style:italic", `margin:${s.space_before}pt 0 ${s.space_after}pt`,
].filter(Boolean).join(";");

/** Variable chip text: the default (filter applied) when set, else {{ name }}. */
export function variableText(a: Record<string, any>): string {
  const d = String(a.default ?? "");
  if (!d) return `{{ ${a.name} }}`;
  return a.filter === "upper" ? d.toUpperCase() : d; // date filters need a runtime value: shown as default
}

const MARKS: Record<string, [string, string]> = { bold: ["<strong>", "</strong>"], italic: ["<em>", "</em>"], underline: ["<u>", "</u>"] };

function inline(n: PMNode): string {
  if (n.type === "hardBreak") return "<br>";
  if (n.type === "variable") {
    const a = n.attrs ?? {};
    return `<span class="var-chip" title="${esc(`{{ ${a.name}${a.filter ? " | " + a.filter : ""} }}`)}">${esc(variableText(a))}</span>`;
  }
  let h = esc(n.text);
  for (const m of n.marks ?? []) {
    if (m.type === "link") h = `<a href="${esc(safeHref(m.attrs?.href))}" target="_blank" rel="noopener noreferrer">${h}</a>`;
    else if (MARKS[m.type]) h = MARKS[m.type][0] + h + MARKS[m.type][1];
  }
  return h;
}

export function contentToHtml(doc: PMNode | null | undefined, styles: Style[]): string {
  const by = new Map(styles.map((s) => [s.style_id, s]));
  const count: number[] = [0, 0, 0, 0, 0, 0, 0, 0, 0];

  const kids = (n: PMNode, inList: boolean) => (n.content ?? []).map((c) => block(c, inList)).join("");
  function block(n: PMNode, inList = false): string {
    switch (n.type) {
      case "paragraph": {
        const id = effectiveStyle(n, inList)!;
        return `<p class="st-${esc(id)}" style="${esc(css(by.get(id) ?? by.get("Normal")))}">${(n.content ?? []).map(inline).join("") || "<br>"}</p>`;
      }
      case "heading": {
        const id = effectiveStyle(n)!, st = by.get(id), lvl = Math.min(Math.max(Number(n.attrs?.level) || 1, 1), 6);
        let num = "";
        if (st?.numbering === "decimal") {
          const d = Math.min(Math.max(st.level ?? lvl, 1), 9) - 1;
          count[d]++; count.fill(0, d + 1);
          num = `<span class="num">${count.slice(0, d + 1).map((c) => c || 1).join(".")}</span> `;
        }
        return `<h${lvl} class="st-${esc(id)}" style="${esc(css(st))}">${num}${(n.content ?? []).map(inline).join("")}</h${lvl}>`;
      }
      case "bulletList": return `<ul>${kids(n, true)}</ul>`;
      case "orderedList": return `<ol>${kids(n, true)}</ol>`;
      case "listItem": return `<li>${kids(n, true)}</li>`;
      case "table": {
        const dyn = n.attrs?.dynamicList;
        const rows = (n.content ?? []).map((r, i, a) =>
          `<tr${dyn && i === a.length - 1 ? ' class="tpl"' : ""}>${(r.content ?? []).map((c) => {
            const t = c.type === "tableHeader" ? "th" : "td", at = c.attrs ?? {};
            return `<${t}${at.colspan > 1 ? ` colspan="${+at.colspan}"` : ""}${at.rowspan > 1 ? ` rowspan="${+at.rowspan}"` : ""}>${kids(c, false)}</${t}>`;
          }).join("")}</tr>`).join("");
        return `${dyn ? `<div class="dyn-badge">↻ rows repeat for each item of {{ ${esc(dyn)} }}</div>` : ""}<table class="tbl">${rows}</table>`;
      }
      case "image": {
        const a = n.attrs ?? {}, w = Number(a.width);
        return `<img class="img" src="/api/images/${esc(encodeURIComponent(a.imageId ?? ""))}" alt="${esc(a.alt)}"${w > 0 ? ` style="width:${w}px"` : ""}>`;
      }
      default: return kids(n, inList);
    }
  }
  return kids(doc ?? { type: "doc" }, false);
}
