// DOM -> pagination input. Heights are measured in CSS px, divided by the element's zoom (CSS `zoom` scales getBoundingClientRect)
// and converted to pt, the unit of the page setup.
import { Look } from "./look";
import { Block } from "./paginate";
import { PT_PX } from "./pagelayout";

const styleOf = (el: Element) => /(?:^|\s)st-([A-Za-z0-9_-]+)/.exec(el.getAttribute("class") ?? "")?.[1];
const tableOf = (el: Element): HTMLTableElement | null => (el.tagName === "TABLE" ? (el as HTMLTableElement) : el.querySelector(":scope > table, :scope > div > table, :scope > .tableWrapper > table"));

/** One top-level block element -> Block. `zoom` = the CSS zoom the element is displayed at. */
export function measureBlock(el: Element, zoom: number, flags: Look["flags"] = {}): Block {
  const k = 1 / zoom / PT_PX, cs = getComputedStyle(el);
  let h = el.getBoundingClientRect().height * k;
  const own = Array.from(el.querySelectorAll(":scope tr.pgsep-row, :scope tr.rep")).reduce((a, r) => a + r.getBoundingClientRect().height * k, 0);
  h -= own; // page-gap rows the editor inserted into this table are not content
  const sf = flags?.[styleOf(el) ?? ""] ?? {};
  const b: Block = { h, mt: parseFloat(cs.marginTop) / PT_PX, mb: parseFloat(cs.marginBottom) / PT_PX, keepNext: !!(sf.keepNext || el.hasAttribute("data-kn")), breakBefore: !!(sf.pageBreakBefore || el.hasAttribute("data-pbb")) || undefined,
    breakAfter: !!el.querySelector(".pgbrk") || undefined };
  const t = tableOf(el);
  if (t && t.rows.length) {
    const real = Array.from(t.rows).filter((r) => !r.classList.contains("pgsep-row") && !r.classList.contains("rep")); // not our own page-gap rows
    const rows = real.map((r) => r.getBoundingClientRect().height * k);
    let hdr = 0;
    while (hdr < real.length - 1 && Array.from(real[hdr].cells).every((c) => c.tagName === "TH")) hdr++;
    const sum = rows.reduce((a, c) => a + c, 0);
    h = b.h = Math.max(h, sum);
    Object.assign(b, { rows, headerRows: hdr });
  }
  return b;
}

/** Block cut to rows [from, to) of its table, header rows repeated in front when `repeat` > 0 (HTML of the clone). */
export function sliceTable(el: Element, from: number, to: number, repeat: number): string {
  const c = el.cloneNode(true) as HTMLElement, t = tableOf(c);
  if (!t) return c.outerHTML;
  Array.from(t.rows).forEach((r, i) => { if (!((i >= from && (to <= 0 || i < to)) || (from > 0 && i < repeat))) r.remove(); });
  return c.outerHTML;
}
