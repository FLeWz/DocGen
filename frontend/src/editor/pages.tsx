// Paged HTML view: the same content as the editor, but cloned into separate page cards (header, body, footer each).
// Blocks are measured in a hidden copy (zoom 1), paginate() decides the cut points, tables are cut between rows.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { applyLook, Look, pageVars } from "./look";
import { measureBlock, sliceTable } from "./measure";
import { layout, normalize, PageInfo, pageParts, startsPage } from "./pagelayout";
import { tagBlock } from "./follow";
import { Block } from "./paginate";
import type { HtmlBlock } from "./render";

type Planned = { info: PageInfo; body: string; hdr: string; ftr: string };
export type PagedStats = { pages: number };

export function PagedView({ blocks, look, zoom, guides, testid, onPages }: { blocks: HtmlBlock[]; look?: Look; zoom: number; guides?: boolean; testid?: string; onPages?: (n: number) => void }) {
  applyLook(look);
  const html = useMemo(() => blocks.map((b, i) => tagBlock(b.html, i)).join(""), [blocks]);
  const meas = useRef<HTMLDivElement>(null);
  const [plan, setPlan] = useState<Planned[] | null>(null);
  const [fonts, setFonts] = useState(0);
  useEffect(() => { document.fonts?.ready.then(() => setFonts((n) => n + 1)); }, []);
  const norm = useMemo(() => normalize(look), [look]);
  useLayoutEffect(() => {
    const pv = meas.current?.querySelector(".pv");
    if (!pv) return;
    const els = Array.from(pv.children), same = els.length === blocks.length;
    let prev = 0;
    const bl: Block[] = els.map((e, i) => {
      const b = measureBlock(e, 1, look?.flags), sec = same ? blocks[i].sec : 0;
      b.section = sec;
      if (sec !== prev && !startsPage(norm.sections[Math.min(sec, norm.sections.length - 1)])) b.breakBefore = b.breakBefore ? true : false;
      prev = sec;
      return b;
    });
    const lay = layout(look, bl), total = lay.spans.length;
    const planned = lay.spans.map((sp, pi): Planned => {
      let body = "";
      for (let b = sp.from.block; b <= sp.to.block && b < els.length; b++) {
        if (b === sp.to.block && sp.to.row === 0) break;
        const from = b === sp.from.block ? sp.from.row : 0, to = b === sp.to.block ? sp.to.row : 0;
        body += from === 0 && to === 0 ? els[b].outerHTML : sliceTable(els[b], from, to, sp.repeat);
      }
      return { info: lay.pages[pi], body, ...pageParts(look, lay.pages[pi], total) };
    });
    setPlan(planned);
    onPages?.(total);
  }, [html, look, fonts]); // eslint-disable-line react-hooks/exhaustive-deps

  const first = norm.sections[0].page;
  return (
    <div className={`docx ${guides ? "guides" : ""}`} style={{ "--zoom": zoom } as React.CSSProperties}>
      {look && <style>{look.css}</style>}
      <div className="docx-pages" data-testid={testid}>
        {(plan ?? [{ info: { page: first, label: "1", number: 1 } as PageInfo, body: html, hdr: "", ftr: "" }]).map((p) => (
          <div key={p.info.index ?? 0} className="docx-page paged" data-page={p.info.number} style={pageVars(p.info.page)}>
            <div className="docx-hdr" aria-label="Page header" dangerouslySetInnerHTML={{ __html: p.hdr }} />
            <div className="docx-body"><div className="pv" dangerouslySetInnerHTML={{ __html: p.body }} /></div>
            <div className="docx-ftr" aria-label="Page footer" dangerouslySetInnerHTML={{ __html: p.ftr }} />
          </div>))}
      </div>
      <div ref={meas} className="docx-measure" aria-hidden="true" style={{ ...pageVars(first), "--zoom": 1 } as React.CSSProperties}>
        <div className="docx-page"><div className="docx-body"><div className="pv" dangerouslySetInnerHTML={{ __html: html }} /></div></div>
      </div>
    </div>
  );
}
