// Editor pagination: ONE contenteditable stays the single editing surface; page gaps are non-editable widget decorations
// inserted at the computed break positions. Each gap draws the rest of the page (white), its footer, a grey gap and the
// next page's header, so the flow looks like separate paper cards. Typing, selection and undo are untouched (decorations only,
// no document change). Layout is recomputed (debounced) after edits, look/zoom changes and font loading.
import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Look, pageVars } from "./look";
import { measureBlock } from "./measure";
import { Layout, layout, normalize, pageParts } from "./pagelayout";
import { Block } from "./paginate";
import { Stop, validStops } from "./tabs";

export type EditorLayout = Layout & { total: number };
export const pageState = { look: undefined as Look | undefined, zoom: 1, tick: 0, onLayout: undefined as ((l: EditorLayout) => void) | undefined };
type PS = { deco: DecorationSet; sig: string; tick: number };
export const pagesKey = new PluginKey<PS>("pages");

const el = (cls: string, html = "", style = "", tag = "div") => { const e = document.createElement(tag); e.className = cls; if (html) e.innerHTML = html; if (style) e.style.cssText = style; return e; };
const varsCss = (p: Parameters<typeof pageVars>[0]) => Object.entries(pageVars(p)).map(([k, v]) => `${k}:${v}`).join(";");

/** Gap between two pages: rest of page (fill), footer of the finished page, grey gap, header of the next page. */
function gap(look: Look | undefined, lay: Layout, k: number, total: number): HTMLElement {
  const a = lay.pages[k - 1], b = lay.pages[k], pa = pageParts(look, a, total), pb = pageParts(look, b, total);
  const root = el("pgsep", "", "", "div");
  root.contentEditable = "false";
  root.dataset.page = String(k + 1);
  root.append(el("pgsep-fill", "", `height:${Math.max(a.body - lay.spans[k - 1].used, 0)}pt`),
    el("docx-ftr pgsep-ftr", pa.ftr, varsCss(a.page)), el("pgsep-gap"), el("docx-hdr pgsep-hdr", pb.hdr, varsCss(b.page)));
  return root;
}

/** Same gap as table rows (a widget inside a table body must be a <tr>); header rows are repeated by the clones that follow. */
function gapRow(look: Look | undefined, lay: Layout, k: number, total: number, cols: number): HTMLElement {
  const tr = el("pgsep-row", "", "", "tr"), td = el("", "", "padding:0;border:0;background:none", "td");
  td.setAttribute("colspan", String(cols)); td.contentEditable = "false"; td.append(gap(look, lay, k, total)); tr.append(td);
  return tr;
}

function compute(view: import("@tiptap/pm/view").EditorView): { deco: DecorationSet; lay: EditorLayout; sig: string } | null {
  const look = pageState.look, doc = view.state.doc, blocks: Block[] = [], offs: { pos: number; node: PMNode }[] = [];
  const norm = normalize(look);
  let sec = 0, prevSec = 0;
  doc.forEach((node, pos) => {
    const dom = view.nodeDOM(pos);
    if (!(dom instanceof Element)) return;
    const b = measureBlock(dom, pageState.zoom, look?.flags);
    b.section = sec;
    if (sec !== prevSec && norm.sections[Math.min(sec, norm.sections.length - 1)]?.brk === "continuous") b.breakBefore = b.breakBefore ? true : false;
    prevSec = sec;
    blocks.push(b); offs.push({ pos, node });
    if (node.type.name === "sectBreak" || (node.type.name === "opaque" && node.attrs.kind === "section break")) sec++;
  });
  if (!blocks.length) return null;
  const lay = layout(look, blocks), total = lay.spans.length, out: Decoration[] = [];
  for (let k = 1; k < total; k++) {
    const from = lay.spans[k].from, { pos, node } = offs[Math.min(from.block, offs.length - 1)];
    if (from.row === 0) { out.push(Decoration.node(pos, pos + node.nodeSize, { class: "pgtop" })); out.push(Decoration.widget(pos, () => gap(look, lay, k, total), { side: -1, key: `pg${k}-${lay.pages[k - 1].label}-${lay.pages[k].label}-${total}-${Math.round(lay.pages[k - 1].body - lay.spans[k - 1].used)}` })); continue; }
    // inside a table: before row `from.row`
    let rp = pos + 1;
    for (let r = 0; r < from.row; r++) rp += node.child(r).nodeSize;
    const cols = Array.from({ length: node.child(0).childCount }, (_, i) => Number(node.child(0).child(i).attrs.colspan) || 1).reduce((x, y) => x + y, 0);
    const dom = view.nodeDOM(pos) as Element, rows = Array.from(dom.querySelectorAll("tr")).filter((r) => !r.classList.contains("pgsep-row") && !r.classList.contains("rep"));
    out.push(Decoration.widget(rp, () => gapRow(look, lay, k, total, cols), { side: -1, key: `pg${k}-${lay.pages[k].label}-${total}-${Math.round(lay.pages[k - 1].body - lay.spans[k - 1].used)}` }));
    for (let h = 0; h < lay.spans[k].repeat; h++) {
      const src = rows[h];
      if (src) out.push(Decoration.widget(rp, () => { const c = src.cloneNode(true) as HTMLElement; c.classList.add("rep"); c.contentEditable = "false"; c.removeAttribute("data-fmt"); return c; }, { side: -1 }));
    }
  }
  const last = lay.pages[total - 1], used = lay.spans[total - 1].used;
  out.push(Decoration.widget(doc.content.size, () => el("pgsep-end", "", `height:${Math.max(last.body - used, 0)}pt`), { side: 1, key: `end${Math.round(last.body - used)}` }));
  const sig = JSON.stringify([lay.spans.map((s) => [s.from.block, s.from.row, Math.round(s.used)]), lay.pages.map((p) => [p.label, p.hdr, p.ftr, Math.round(p.body)])]);
  return { deco: DecorationSet.create(doc, out), lay: { ...lay, total }, sig };
}

/** Tab stops in the editable text: each tab character becomes an inline-block as wide as the distance to its stop. The widths are
 *  measured from the rendered text (segments between tabs), so typing re-flows them (debounced, decorations only). */
type TS = { deco: DecorationSet; sig: string };
export const tabsKey = new PluginKey<TS>("tabstops");
function tabWidths(view: import("@tiptap/pm/view").EditorView): { deco: DecorationSet; sig: string } {
  const look = pageState.look, out: Decoration[] = [], sizes: number[] = [], k = 0.75 / pageState.zoom; // px (zoomed) -> pt
  view.state.doc.descendants((node, pos) => {
    if (node.type.name !== "paragraph" && node.type.name !== "heading") return true;
    const sid = node.attrs.styleId ?? (node.type.name === "heading" ? `Heading${node.attrs.level}` : "Normal");
    const stops: Stop[] = validStops(node.attrs.pfmt?.tabs ?? look?.tabs?.[sid]);
    if (!stops.length || !node.textContent.includes("\t")) return false;
    const base = pos + 1, tabs: number[] = [];
    node.descendants((c, p) => { if (c.isText) for (let i = c.text!.indexOf("\t"); i >= 0; i = c.text!.indexOf("\t", i + 1)) tabs.push(base + p + i); });
    const ends = [...tabs, pos + node.nodeSize - 1], starts = [base, ...tabs.map((t) => t + 1)];
    const w = starts.map((s, i) => Math.max((view.coordsAtPos(ends[i]).left - view.coordsAtPos(s).left) * k, 0));
    const indent = (Number(node.attrs.pfmt?.left) || 0) / 20;
    let x = w[0];
    tabs.forEach((t, i) => {
      const st = stops.find((s) => s.pos / 20 - indent > x + 0.5) ?? { pos: (Math.floor((x + indent) / 36) + 1) * 720, val: "left" as const };
      const target = st.pos / 20 - indent, next = w[i + 1];
      const tw = Math.max(st.val === "right" ? target - x - next : st.val === "center" ? target - x - next / 2 : target - x, 0);
      x = st.val === "right" ? target : st.val === "center" ? target + next / 2 : target + next;
      sizes.push(Math.round(tw * 10));
      out.push(Decoration.inline(t, t + 1, { class: "tabch", style: `width:${+tw.toFixed(1)}pt${"leader" in st && st.leader ? ";border-bottom:1px dotted currentColor" : ""}` }));
    });
    return false;
  });
  return { deco: DecorationSet.create(view.state.doc, out), sig: sizes.join(",") };
}
const tabsPlugin = () => new Plugin<TS>({
  key: tabsKey,
  state: { init: () => ({ deco: DecorationSet.empty, sig: "" }), apply(tr, v) { return tr.getMeta(tabsKey) ?? { ...v, deco: v.deco.map(tr.mapping, tr.doc) }; } },
  props: { decorations: (s) => tabsKey.getState(s)?.deco },
  view(view) {
    let timer = 0, seen = pageState.tick, doc = view.state.doc;
    const run = () => {
      if (!view.dom.isConnected) return;
      const r = tabWidths(view);
      if (r.sig !== tabsKey.getState(view.state)?.sig) view.dispatch(view.state.tr.setMeta(tabsKey, r).setMeta("addToHistory", false));
    };
    const schedule = (ms = 200) => { window.clearTimeout(timer); timer = window.setTimeout(run, ms); };
    schedule(30);
    document.fonts?.ready.then(() => schedule(0));
    return { update(v) { if (v.state.doc !== doc || seen !== pageState.tick) { doc = v.state.doc; seen = pageState.tick; schedule(); } }, destroy() { window.clearTimeout(timer); } };
  },
});

export const Pages = Extension.create({
  name: "pages",
  addProseMirrorPlugins() {
    return [tabsPlugin(), new Plugin<PS>({
      key: pagesKey,
      state: {
        init: () => ({ deco: DecorationSet.empty, sig: "", tick: 0 }),
        apply(tr, v) { const m = tr.getMeta(pagesKey); return m ?? { ...v, deco: v.deco.map(tr.mapping, tr.doc) }; },
      },
      props: { decorations: (s) => pagesKey.getState(s)?.deco },
      view(view) {
        let timer = 0, seen = pageState.tick, doc = view.state.doc, tabs = tabsKey.getState(view.state);
        const run = () => {
          if (!view.dom.isConnected) return;
          const r = compute(view);
          if (!r) return;
          pageState.onLayout?.(r.lay);
          (window as unknown as { __docgenLayout?: unknown }).__docgenLayout = r.lay; // inspection aid for e2e/compare scripts
          if (r.sig !== pagesKey.getState(view.state)?.sig) view.dispatch(view.state.tr.setMeta(pagesKey, { deco: r.deco, sig: r.sig, tick: pageState.tick }).setMeta("addToHistory", false));
        };
        const schedule = (ms = 350) => { window.clearTimeout(timer); timer = window.setTimeout(run, ms); }; // after the tab-stop pass (200 ms)
        schedule(150);
        document.fonts?.ready.then(() => schedule(60));
        return {
          update(v) {
            const t = tabsKey.getState(v.state);
            const tabsChanged = t !== tabs && t?.sig !== tabs?.sig;
            if (v.state.doc !== doc || seen !== pageState.tick || tabsChanged) { doc = v.state.doc; seen = pageState.tick; tabs = t; schedule(tabsChanged ? 30 : 350); }
          },
          destroy() { window.clearTimeout(timer); },
        };
      },
    })];
  },
});
