// Preview follows the editor (round 23). The anchor is the top-level block index: the preview renders the same blocks
// (`data-block="<i>"` on each block's root element). Pure helpers first (Vitest), DOM glue (useFollow) below.
import { MutableRefObject, useCallback, useEffect, useRef } from "react";

export const FOLLOW_KEY = "docgen.editor.v2.follow";
export const loadFollow = (s: Pick<Storage, "getItem">) => s.getItem(FOLLOW_KEY) !== "0"; // default ON
export const PAUSE_MS = 2000, TOP_FRAC = 0.3;

/** Top-level child index of a resolved selection position (clamped to the block count). */
export const topBlock = (r: { index: (d: number) => number }, count: number) => Math.max(0, Math.min(r.index(0), count - 1));

/** Put `data-block` on the root element of a block's HTML (empty html, e.g. bookmarks, has none). */
export const tagBlock = (html: string, i: number) => html.replace(/^<([a-zA-Z][\w-]*)/, `<$1 data-block="${i}"`);

/** Block -> page of the PDF: estimated page (1-based, from the pagination spans) scaled by pdfPages / estimated pages. */
export function blockPage(spans: { to: { block: number; row: number } }[], block: number, pdfPages: number): number {
  if (!spans.length) return 1;
  const i = spans.findIndex((sp) => block < sp.to.block || (block === sp.to.block && sp.to.row > 0)), est = i < 0 ? spans.length : i + 1;
  return pdfPages > 0 ? Math.min(Math.max(Math.round(est * pdfPages / spans.length), 1), pdfPages) : est;
}

/** Pages of a PDF without parsing it: page objects; falls back to the largest /Count when pages sit in object streams. */
export function pdfPageCount(raw: string): number {
  const n = (raw.match(/\/Type\s*\/Page(?![a-zA-Z])/g) ?? []).length;
  return n || Math.max(0, ...Array.from(raw.matchAll(/\/Count\s+(\d+)/g), (m) => +m[1]));
}

/** `rel` = block top relative to the viewport top, `h` its height, `view` the viewport height.
 *  null = already visible (do nothing); else the new scrollTop. Tall blocks count as visible while they cover the 30% line. */
export function scrollPlan(rel: number, h: number, view: number, scrollTop: number, reduced = false): { top: number; smooth: boolean } | null {
  const line = view * TOP_FRAC;
  if (h <= view ? rel >= 0 && rel + h <= view : rel <= line && rel + h >= line) return null;
  const top = Math.max(0, scrollTop + rel - line);
  return { top, smooth: !reduced && Math.abs(top - scrollTop) < 2 * view };
}

export const paused = (now: number, until: number) => now < until;

/** Block index of every line of a source text: blank-line separated chunks, fences/verbatim kept whole, comment-only chunks folded into the previous block. */
export function lineChunks(text: string, latex: boolean): number[] {
  const chunks: { real: boolean }[] = [], of: number[] = [];
  let fence = "", cur = -1, open = false;
  for (const l of text.split("\n")) {
    const t = l.trim();
    if (!fence && !t) { open = false; of.push(Math.max(cur, 0)); continue; }
    if (!open) { chunks.push({ real: false }); cur = chunks.length - 1; open = true; }
    if (fence) { if (latex ? t.startsWith("\\end{verbatim}") : t.startsWith(fence)) fence = ""; }
    else { const f = latex ? (t.startsWith("\\begin{verbatim}") ? "v" : "") : /^(`{3,}|~{3,})/.exec(t)?.[1] ?? ""; if (f) fence = f; }
    if (!(latex ? t.startsWith("%") : /^<!--.*-->$/.test(t))) chunks[cur].real = true;
    of.push(cur);
  }
  let k = -1;
  const idx = chunks.map((c) => (c.real ? ++k : Math.max(k, 0)));
  return of.map((c) => idx[c] ?? 0);
}

/** Line (1-based) -> top-level block index. Chunk count == block count: exact; else proportional (a loose list / table with blank lines, a stale parse). */
export function lineBlock(text: string, line: number, blocks: number, latex: boolean): number {
  const ch = lineChunks(text, latex), c = ch[Math.min(Math.max(line, 1), ch.length) - 1] ?? 0, n = (ch[ch.length - 1] ?? 0) + 1;
  return Math.max(0, Math.min(n === blocks ? c : Math.floor(c * blocks / n), blocks - 1));
}

type Opts = {
  root: MutableRefObject<HTMLElement | null>; on: boolean; pv: "live" | "word" | "off"; mode: string; ready: boolean; // ready: the panes are mounted (the editor renders nothing before)
 
  spans: () => { to: { block: number; row: number } }[]; pdfPages: () => number;
  scrollBlock: () => number | null; // top-most visible block of the editor
};

/** Wires the preview to the editor. `go(block)` = the caret moved to that block (clears a manual-scroll pause);
 *  `again()` = re-apply after the preview re-rendered. Nothing is attached while the preview is closed or Follow is off. */
export function useFollow(o: Opts) {
  const ref = useRef(o); ref.current = o;
  const s = useRef({ block: -1, until: 0, t: 0, page: 0, pdfAt: 0, hl: null as HTMLElement | null, user: 0 });
  const apply = useCallback(() => {
    const { root, on, pv, spans, pdfPages } = ref.current, st = s.current, now = performance.now();
    if (!on || pv === "off" || st.block < 0 || !root.current) return;
    if (pv === "word") {
      const f = root.current.querySelector<HTMLIFrameElement>("iframe.word-pdf");
      if (!f) return;
      const base = f.src.split("#")[0], page = blockPage(spans(), st.block, pdfPages());
      if (page === st.page && f.dataset.fbase === base) return;
      if (now - st.pdfAt < 400) { window.clearTimeout(st.t); st.t = window.setTimeout(apply, 400 - (now - st.pdfAt)); return; }
      st.pdfAt = now; st.page = page; f.dataset.fbase = base;
      f.src = `${base}#page=${page}`; // fragment-only change: same document, no reload
      return;
    }
    const stage = root.current.querySelector<HTMLElement>('[aria-label="Preview pane"] .docx-stage');
    if (!stage || paused(now, st.until)) return;
    let el: HTMLElement | null = null;
    for (let i = st.block; i >= Math.max(0, st.block - 20) && !el; i--) el = stage.querySelector(`.docx-pages [data-block="${i}"]`);
    if (!el) return;
    const sr = stage.getBoundingClientRect(), er = el.getBoundingClientRect(); // reads first, writes after
    const plan = scrollPlan(er.top - sr.top, er.height, stage.clientHeight, stage.scrollTop, matchMedia("(prefers-reduced-motion: reduce)").matches);
    if (el !== st.hl) {
      st.hl?.classList.remove("pv-current", "pv-follow");
      el.classList.add("pv-current", "pv-follow");
      st.hl = el;
      const e = el;
      window.setTimeout(() => e.classList.remove("pv-follow"), 1000);
    }
    if (plan) stage.scrollTo({ top: plan.top, behavior: plan.smooth ? "smooth" : "instant" });
  }, []);
  const go = useCallback((block: number, fromScroll = false) => {
    const st = s.current;
    if (!ref.current.on || ref.current.pv === "off") return;
    if (!fromScroll) st.until = 0; // a selection change / typing ends the pause
    st.block = block;
    window.clearTimeout(st.t);
    st.t = window.setTimeout(apply, 80);
  }, [apply]);
  const again = useCallback(() => { if (s.current.block >= 0) { window.clearTimeout(s.current.t); s.current.t = window.setTimeout(apply, 80); } }, [apply]);

  const { on, pv, mode, ready } = o;
  useEffect(() => { // manual scroll in the preview pauses following; wheel/touch/pointer on the stage also covers its scrollbar
    if (!on || pv !== "live") return;
    const stage = o.root.current?.querySelector<HTMLElement>('[aria-label="Preview pane"] .docx-stage');
    if (!stage) return;
    const h = () => { s.current.until = performance.now() + PAUSE_MS; };
    const ev = ["wheel", "touchstart", "pointerdown"] as const;
    ev.forEach((e) => stage.addEventListener(e, h, { passive: true }));
    return () => ev.forEach((e) => stage.removeEventListener(e, h));
  }, [on, pv, ready]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { // scrolling the EDITOR with wheel/touch/scrollbar drives the preview (caret-driven scrolls are ignored)
    if (!on || pv === "off") return;
    const sc = o.root.current?.querySelector<HTMLElement>(mode === "word" ? '[aria-label="Editor pane"] .docx-stage' : ".mdsrc .cm-scroller");
    if (!sc) return;
    let raf = 0;
    const mark = (e: Event) => { if (e.type !== "pointerdown" || e.target === sc) s.current.user = performance.now(); }; // pointerdown on the scroller itself = its scrollbar
    const sync = () => { raf = 0; const b = ref.current.scrollBlock(); if (b != null) go(b, true); };
    const onScroll = () => { // smooth/inertial wheel scrolling keeps the "user is scrolling" window open
      const now = performance.now();
      if (now - s.current.user < 500) { s.current.user = now; if (!raf) raf = requestAnimationFrame(sync); }
    };
    const ev = ["wheel", "touchmove", "pointerdown"] as const;
    ev.forEach((e) => sc.addEventListener(e, mark, { passive: true }));
    sc.addEventListener("scroll", onScroll, { passive: true });
    return () => { ev.forEach((e) => sc.removeEventListener(e, mark)); sc.removeEventListener("scroll", onScroll); cancelAnimationFrame(raf); };
  }, [on, pv, mode, ready, go]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => window.clearTimeout(s.current.t), []);
  return { go, again };
}
