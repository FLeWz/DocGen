// The Word look: effective styles (CSS), page size/margins and header/footer of an imported DOCX / base document / the org
// style set, served by GET /api/styles/css. <PageFrame> draws any children on that "paper"; the editor and every preview use it.
import { useQuery } from "@tanstack/react-query";
import { ReactNode, useMemo } from "react";
import { api, Me, Style } from "../api";
import "./editor.css";
import { ooxmlToHtml, setMedia, setStyleTabs } from "./ooxml";

export type Page = { w: number; h: number; top: number; right: number; bottom: number; left: number; header: number; footer: number; first_page_differs?: boolean };
export type Part = { xml: string; media: Record<string, string> };
/** A section: page setup, how it starts, titlePg, the header/footer part (index into Look.parts) per type, page numbering. */
export type Sect = { page: Page; brk: string; title_pg: boolean; header: Partial<Record<"default" | "first" | "even", number>>; footer: Partial<Record<"default" | "first" | "even", number>>; num: { fmt: string; start: number | null } };
export type Look = {
  css: string; page: Page; header: Part | null; footer: Part | null; media: Record<string, string>; styles: Style[] | null;
  tabs?: Record<string, unknown>; flags?: Record<string, { keepNext?: boolean; keepLines?: boolean; pageBreakBefore?: boolean }>;
  sections?: Sect[]; parts?: Part[]; even_odd?: boolean;
};
export type LookSrc = { base_doc?: string | null; import?: string | null };

export function useLook(src: LookSrc) {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") }).data;
  const qs = src.import ? `?import=${src.import}` : src.base_doc ? `?base_doc=${src.base_doc}` : "";
  const q = useQuery({ queryKey: ["look", me?.org?.id, src.import ?? null, src.base_doc ?? null], queryFn: () => api<Look>(`/styles/css${qs}`), staleTime: 60_000 });
  applyLook(q.data);
  return q;
}

const FALLBACK: Page = { w: 595.3, h: 841.9, top: 70.9, right: 70.9, bottom: 70.9, left: 70.9, header: 35, footer: 35 };

/** Paper: page width, margins, header/footer (read-only), optional page-break guides, zoom. `bodyTestId` marks the content box. */
export const pageVars = (p: Page, zoom?: number) => ({ "--pw": `${p.w}pt`, "--ph": `${p.h}pt`, "--pt": `${p.top}pt`, "--pr": `${p.right}pt`, "--pb": `${p.bottom}pt`, "--pl": `${p.left}pt`,
  "--hd": `${p.header}pt`, "--ft": `${p.footer}pt`, "--bh": `${p.h - p.top - p.bottom}pt`, ...(zoom === undefined ? {} : { "--zoom": zoom }) }) as React.CSSProperties;

/** Make the look's media and style tab stops known to the OOXML renderer (headers/footers, opaque blocks). */
export function applyLook(look?: Look) { setMedia(look?.media); setStyleTabs(look?.tabs); } // during render: header/footer HTML is built in the same pass

/** `hdrHtml`/`ftrHtml` override the header/footer (the editor draws page 1's header and the last page's footer there). */
export function PageFrame({ look, children, zoom = 1, guides, bodyTestId, className = "", hdrHtml, ftrHtml }: {
  look?: Look; children: ReactNode; zoom?: number; guides?: boolean; bodyTestId?: string; className?: string; hdrHtml?: string; ftrHtml?: string;
}) {
  const p = look?.page ?? FALLBACK;
  const hdr0 = useMemo(() => (look?.header ? ooxmlToHtml(look.header.xml, look.header.media) : ""), [look]);
  const ftr0 = useMemo(() => (look?.footer ? ooxmlToHtml(look.footer.xml, look.footer.media) : ""), [look]);
  const hdr = hdrHtml ?? hdr0, ftr = ftrHtml ?? ftr0;
  applyLook(look);
  const vars = pageVars(p, zoom);
  return (
    <div className={`docx ${guides ? "guides" : ""} ${className}`} style={vars}>
      {look && <style>{look.css}</style>}
      <div className="docx-page">
        <div className="docx-hdr" aria-label="Page header" dangerouslySetInnerHTML={{ __html: hdr }} />
        <div className="docx-body" data-testid={bodyTestId}>{children}</div>
        <div className="docx-ftr" aria-label="Page footer" dangerouslySetInnerHTML={{ __html: ftr }} />
      </div>
    </div>
  );
}
