// Page layout of a document in the page view: sections (page size/margins, titlePg, even/odd, numbering), the header/footer each
// page gets with its fields evaluated, and the usable body height (page minus margins, margins grow when a header/footer is taller).
import { FieldCtx, pageLabel } from "./fields";
import { Look, Page, Part, partHtml, Sect } from "./look";
import { Block, PageSpan, paginate } from "./paginate";

export const PT_PX = 96 / 72;
type HF = "default" | "first" | "even";
export type Norm = { sections: Sect[]; parts: Part[]; evenOdd: boolean };
export type PageInfo = {
  index: number; section: number; inSection: number; number: number; label: string; page: Page;
  hdr: number | null; ftr: number | null;       // part indexes
  top: number; bottom: number; body: number;     // effective margins and body height (pt)
};

/** The look's sections, or one section built from the final page setup + default header/footer (base documents, org styles). */
export function normalize(look?: Look): Norm {
  if (look?.sections?.length) return { sections: look.sections, parts: look.parts ?? [], evenOdd: !!look.even_odd };
  const parts = [look?.header, look?.footer].filter(Boolean) as Part[];
  const page = look?.page ?? { w: 595.3, h: 841.9, top: 70.9, right: 70.9, bottom: 70.9, left: 70.9, header: 35, footer: 35 };
  return { sections: [{ page, brk: "nextPage", title_pg: false, header: look?.header ? { default: 0 } : {}, footer: look?.footer ? { default: look.header ? 1 : 0 } : {}, num: { fmt: "decimal", start: null } }], parts, evenOdd: false };
}

const hfKey = (s: number, kind: "h" | "f", t: HF) => `${s}:${kind}:${t}`;
const cache = new WeakMap<Look, Map<string, number>>();

/** Content height (pt) of every header/footer variant of every section, measured in a hidden box of the section's text width. */
export function measureHF(look: Look | undefined, norm: Norm): Map<string, number> {
  const hit = look && cache.get(look);
  if (hit) return hit;
  const out = new Map<string, number>();
  if (norm.parts.length) {
    const root = document.createElement("div");
    root.className = "docx";
    root.style.cssText = "position:absolute;left:-99999px;top:0;visibility:hidden;--zoom:1";
    if (look?.css) { const st = document.createElement("style"); st.textContent = look.css; root.appendChild(st); }
    document.body.appendChild(root);
    const fields: FieldCtx = { page: "88", pages: "88", now: new Date() };
    const jobs: [string, HTMLElement][] = [];
    norm.sections.forEach((s, si) => {
      for (const [kind, refs] of [["h", s.header], ["f", s.footer]] as const) {
        for (const t of ["default", "first", "even"] as const) {
          const idx = refs[t];
          if (idx === undefined || !norm.parts[idx]) continue;
          const box = document.createElement("div");
          box.className = "docx-page";
          box.style.cssText = `width:${s.page.w - s.page.left - s.page.right}pt;min-height:0;box-shadow:none;zoom:1;display:block`;
          box.innerHTML = partHtml(norm.parts[idx], fields, look?.tabs);
          root.appendChild(box);
          jobs.push([hfKey(si, kind, t), box]);
        }
      }
    });
    for (const [k, box] of jobs) out.set(k, box.getBoundingClientRect().height / PT_PX);
    root.remove();
  }
  if (look) cache.set(look, out);
  return out;
}

export function pageKind(sect: Sect, inSection: number, number: number, evenOdd: boolean): HF {
  if (sect.title_pg && inSection === 0) return "first";
  return evenOdd && number % 2 === 0 ? "even" : "default";
}

/** Info for the page that would come after pages of the given `before` sections. */
export function infoFor(norm: Norm, hf: Map<string, number>, before: readonly number[], section: number): PageInfo {
  const si = Math.min(section, norm.sections.length - 1), sect = norm.sections[si];
  let n = 0, inSection = 0;
  before.forEach((s, i) => {
    const prev = Math.min(s, norm.sections.length - 1), ps = norm.sections[prev];
    const first = i === 0 || Math.min(before[i - 1], norm.sections.length - 1) !== prev;
    n = first && ps.num.start != null ? ps.num.start : n + 1;
  });
  for (let i = before.length - 1; i >= 0 && Math.min(before[i], norm.sections.length - 1) === si; i--) inSection++;
  n = inSection === 0 && sect.num.start != null ? sect.num.start : n + 1;
  const kind = pageKind(sect, inSection, n, norm.evenOdd), p = sect.page;
  const hdr = sect.header[kind] ?? null, ftr = sect.footer[kind] ?? null;
  const top = Math.max(p.top, hdr === null ? 0 : p.header + (hf.get(hfKey(si, "h", kind)) ?? 0));
  const bottom = Math.max(p.bottom, ftr === null ? 0 : p.footer + (hf.get(hfKey(si, "f", kind)) ?? 0));
  return { index: before.length, section: si, inSection, number: n, label: pageLabel(n, sect.num.fmt), page: p, hdr, ftr, top, bottom, body: Math.max(p.h - top - bottom, 20) };
}

export type Layout = { spans: PageSpan[]; pages: PageInfo[] };
/** Blocks in pt -> pages (spans) and what each page looks like. */
export function layout(look: Look | undefined, blocks: Block[]): Layout {
  const norm = normalize(look), hf = measureHF(look, norm);
  const spans = paginate(blocks.length ? blocks : [{ h: 0 }], (page, section, before) => infoFor(norm, hf, before.slice(0, page).map((s) => s.section), section).body);
  const pages: PageInfo[] = [];
  spans.forEach((s) => pages.push(infoFor(norm, hf, pages.map((p) => p.section), s.section)));
  return { spans, pages };
}

/** Header and footer HTML of a page with PAGE / NUMPAGES evaluated. */
export function pageParts(look: Look | undefined, info: PageInfo, total: number): { hdr: string; ftr: string } {
  const norm = normalize(look), f: FieldCtx = { page: info.label, pages: String(total), now: new Date() };
  const html = (i: number | null) => (i === null || !norm.parts[i] ? "" : partHtml(norm.parts[i], f, look?.tabs));
  return { hdr: html(info.hdr), ftr: html(info.ftr) };
}

/** Section a top-level block belongs to + its start rule, for paginate(). */
export const startsPage = (sect: Sect | undefined) => sect?.brk !== "continuous";
