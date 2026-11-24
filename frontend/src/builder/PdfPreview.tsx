import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, ExportStatus } from "../api";
import { Banner, ErrorBanner, Icon, Spinner } from "../ui";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const ZOOMS = [["page-width", "Fit width"], ["page-fit", "Fit page"], ["50", "50%"], ["75", "75%"], ["100", "100%"], ["125", "125%"], ["150", "150%"], ["200", "200%"]];

/** The real render of the document: the normal PDF export (Gotenberg/LibreOffice), polled, shown from a blob URL. An identical spec is served from the
 *  previous render (`reuse`); "Refresh preview" renders again. */
export function PdfPreview({ docId, flush }: { docId: string; flush: () => Promise<unknown> }) {
  const [url, setUrl] = useState(""), [busy, setBusy] = useState(""), [err, setErr] = useState<Error | null>(null);
  const [missing, setMissing] = useState<{ name: string; type: string }[]>([]), [info, setInfo] = useState<{ reused: boolean; warnings: string[] } | null>(null);
  const [zoom, setZoom] = useState("page-width"), [page, setPage] = useState(1);
  const cur = useRef(""), alive = useRef(true), seq = useRef(0);
  useEffect(() => () => { alive.current = false; if (cur.current) URL.revokeObjectURL(cur.current); }, []);

  const render = useCallback(async (force: boolean) => {
    const me = ++seq.current;
    setErr(null); setMissing([]); setBusy("Saving…");
    try {
      await flush();
      setBusy("Preparing PDF…");
      const { export_id, reused } = await api<{ export_id: string; reused?: boolean }>(`/documents/${docId}/export?format=pdf${force ? "" : "&reuse=true"}`);
      let s: ExportStatus;
      for (;;) {
        s = await api<ExportStatus>(`/exports/${export_id}`);
        if (s.status === "error") throw new Error(s.error ?? "export failed");
        if (s.status === "done") break;
        if (!alive.current || me !== seq.current) return;
        setBusy(s.status === "running" ? "Rendering with LibreOffice…" : "Queued…");
        await sleep(500);
      }
      const r = await fetch(`/api/exports/${export_id}/download`);
      if (!r.ok) throw new Error(`download failed (${r.status})`);
      const blob = await r.blob();
      if (!alive.current || me !== seq.current) return;
      if (cur.current) URL.revokeObjectURL(cur.current);
      cur.current = URL.createObjectURL(blob);
      setUrl(cur.current); setPage(1); setInfo({ reused: !!reused, warnings: s.warnings });
    } catch (e) {
      if (!alive.current || me !== seq.current) return;
      const d = e instanceof ApiError ? (e.detail as { error?: string; variables?: { name: string; type: string }[] } | undefined) : undefined;
      if (d?.error === "missing_variables") setMissing(d.variables ?? []); else setErr(e as Error);
    } finally { if (me === seq.current && alive.current) setBusy(""); }
  }, [docId, flush]);
  useEffect(() => { render(false); }, [render]);

  return (
    <div className="pdfprev" data-testid="pdf-preview">
      <div className="pdfbar" role="toolbar" aria-label="PDF preview">
        <button type="button" className="btn sm" disabled={!!busy} onClick={() => render(true)}>{busy ? <Spinner /> : <Icon n="refresh" size={14} />}Refresh preview</button>
        {busy && <span className="muted small" role="status">{busy}</span>}
        {url && <>
          <label className="small">Zoom <select aria-label="Zoom" value={zoom} onChange={(e) => setZoom(e.target.value)}>{ZOOMS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <span className="small">Page <button type="button" className="btn sm icon" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</button>
            <input type="number" min={1} aria-label="Page number" value={page} onChange={(e) => setPage(Math.max(1, Number(e.target.value) || 1))} style={{ width: 56 }} />
            <button type="button" className="btn sm icon" aria-label="Next page" onClick={() => setPage(page + 1)}>›</button></span>
          <a className="btn sm" href={url} download="document.pdf"><Icon n="download" size={14} />Download PDF</a>
          {info?.reused && <span className="muted small" data-testid="pdf-cached" title="The document did not change since this render">cached render</span>}
          {!!info?.warnings.length && <span className="muted small" title={info.warnings.join("\n")}>{info.warnings.length} warning{info.warnings.length > 1 ? "s" : ""}</span>}
        </>}
      </div>
      {missing.length > 0 && <Banner kind="err" data-testid="preview-missing"><b>The preview needs values for {missing.length} variable{missing.length > 1 ? "s" : ""}:</b>
        <ul className="checklist">{missing.map((m) => <li key={m.name}><Icon n="x" size={14} /><code>{m.name}</code> <span className="muted">({m.type})</span></li>)}</ul>
        <span className="muted small">Close the preview and give them a value or default in the Variables panel, then refresh.</span></Banner>}
      <ErrorBanner e={err} />
      {url ? <iframe key={`${zoom}-${page}`} className="word-pdf" title="Document preview (PDF)" data-testid="word-pdf" src={`${url}#page=${page}&zoom=${zoom}`} />
        : <div className="pane-msg">{busy ? <><Spinner /> {busy}</> : null}</div>}
    </div>
  );
}
