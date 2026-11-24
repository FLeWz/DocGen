import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/core";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiBlob, BaseDoc, Doc, Draft, Me, Style } from "../api";
import { ErrorBanner, Icon, Modal, Notice, NoticeBar, Spinner, useUI } from "../ui";
import { blockRange, hasSelection, selectModeOn, setSelectMode } from "./blockrange";
import { extractStyles, extractVariables, PMNode } from "./doc";
import "./editor.css";
import { headingNumbering, pagedExtensions } from "./extensions";
import { TableTools } from "./TableTools";
import { HeaderFooterEditor, HfPart, HfTarget } from "./HeaderFooter";
import { Look, LookSrc, PageFrame, useLook, withHf } from "./look";
import { Ribbon } from "./Ribbon";
import { Structure } from "./Structure";
import { EditorLayout, pageState } from "./pageplugin";
import { normalize, pageKind, pageParts } from "./pagelayout";
import { PagedView } from "./pages";
import { contentBlocks } from "./render";

/** Style set for the dropdown/numbering: org styles, overridden by a base document's (?base_doc=). */
export const useStyles = (me: Me, baseDoc?: string | null) =>
  useQuery({ queryKey: ["styles", me.org?.id, baseDoc ?? null], queryFn: () => api<Style[]>(`/styles${baseDoc ? `?base_doc=${baseDoc}` : ""}`), staleTime: 60_000 });

/** Live HTML preview on the Word page of `look` (default: the org styles). */
export function Preview({ doc, styles, small, look, zoom, guides, onPages }: { doc: PMNode | null | undefined; styles: Style[]; small?: boolean; look?: Look; zoom?: number; guides?: boolean; onPages?: (n: number) => void }) {
  const own = useLook({}).data;
  const l = look ?? own;
  const blocks = useMemo(() => contentBlocks(doc, styles, l?.media, l?.tabs), [doc, styles, l]);
  return <div className="docx-stage"><PagedView blocks={blocks} look={l} zoom={zoom ?? (small ? 0.35 : 0.55)} guides={guides} testid="preview" onPages={onPages} /></div>;
}

type Status = "idle" | "saving" | "saved" | "error";

export type EditorData = { title: string; content: Doc };
export type SaveBody = { title?: string; content: PMNode; styles: string[]; variables: string[] };

/** TipTap editor + ribbon + preview over any document source (snippet draft, imported DOCX). Saves are debounced 800 ms.
 *  For an import, `content.hf` carries the edited header/footer parts next to the body blocks. */
export function RichEditor({ qkey, load, save, keyOf, me, idleSaved, notices = [], noticeBar = "editor", noticeTestId, actions, onSaved, lookSrc }: {
  lookSrc?: LookSrc; // fixed look (imported DOCX); else the user picks a base document
  qkey: unknown[]; load: () => Promise<EditorData>; save: (b: SaveBody) => Promise<EditorData>; keyOf: (d: EditorData) => string; me: Me;
  idleSaved?: boolean; notices?: Notice[]; noticeBar?: string; noticeTestId?: string; actions?: (h: { editor: Editor; flush: () => Promise<unknown> }) => ReactNode; onSaved?: () => void;
}) {
  const qc = useQueryClient();
  const ro = me.org?.role === "viewer";
  const d = useQuery({ queryKey: qkey, queryFn: load, gcTime: 0 }); // no stale cache on reopen: its refetch would reload (wipe) the editor after typing started
  const [pick, setPick] = useState(() => localStorage.getItem(`docgen.look.${me.org?.id}`) ?? "");
  const [pv, setPv] = useState<"live" | "word" | "off">("live");
  const [zoom, setZoom] = useState(innerWidth >= 1600 ? 0.6 : 0.45), [guides, setGuides] = useState(false), [struct, setStruct] = useState(false);
  const [pdf, setPdf] = useState<{ url: string; busy: boolean; err: string }>({ url: "", busy: false, err: "" });
  const bases = useQuery({ queryKey: ["base-docs", me.org?.id], queryFn: () => api<BaseDoc[]>("/base-documents"), enabled: !lookSrc });
  const src: LookSrc = lookSrc ?? (pick && bases.data?.some((b) => b.id === pick) ? { base_doc: pick } : {});
  const lk = useLook(src);
  const [hfDocs, setHfDocs] = useState<Record<string, PMNode>>({}), hfRef = useRef(hfDocs);
  hfRef.current = hfDocs;
  const look = useMemo(() => withHf(lk.data, hfDocs), [lk.data, hfDocs]); // header/footer edits laid over the package's parts: live in every page
  const [hfT, setHfT] = useState<HfTarget | null>(null);
  const hfq = useQuery({ queryKey: ["hf", src.import ?? null], enabled: !!src.import && !!hfT, gcTime: 0, queryFn: () => api<{ parts: HfPart[] }>(`/imports/${src.import}/headers-footers`) });
  const orgStyles = useStyles(me, src.base_doc).data;
  const styles = (src.import ? look?.styles : orgStyles) ?? orgStyles ?? [];
  headingNumbering.styles = new Map(styles.filter((s) => s.numbering === "decimal" && s.kind === "heading").map((s) => [s.style_id, { level: s.level }]));
  const [doc, setDoc] = useState<PMNode>({ type: "doc" });
  const [lay, setLay] = useState<EditorLayout | null>(null);
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [err, setErr] = useState("");
  const titleRef = useRef(""), timer = useRef<number>(), pending = useRef(false), chain = useRef<Promise<unknown>>(Promise.resolve());
  const editorRef = useRef<Editor | null>(null), paneRef = useRef<HTMLElement>(null), downAt = useRef<[number, number] | null>(null);

  // Save everything pending now. Saves are chained so a slow earlier PUT can't overwrite a later one.
  const flush = useCallback((): Promise<unknown> => {
    window.clearTimeout(timer.current);
    const ed = editorRef.current;
    if (!pending.current || !ed || !loaded.current) return chain.current; // before the document arrived the editor is empty: saving it would wipe the stored one
    pending.current = false;
    const body = ed.getJSON() as PMNode, t = titleRef.current;
    const content = (Object.keys(hfRef.current).length ? { ...body, hf: hfRef.current } : body) as PMNode;
    setStatus("saving");
    chain.current = chain.current.then(() => save({ title: t || undefined, content, styles: extractStyles(body), variables: extractVariables(body) }))
      .then((r) => { setErr(""); qc.setQueryData(qkey, r); onSaved?.(); setStatus(pending.current ? "saving" : "saved"); })
      .catch((e: Error) => { pending.current = true; setErr(e.message); setStatus("error"); });
    return chain.current;
  }, [qkey.join(), qc]); // eslint-disable-line react-hooks/exhaustive-deps
  const touch = useCallback(() => { pending.current = true; setStatus("saving"); window.clearTimeout(timer.current); timer.current = window.setTimeout(flush, 800); }, [flush]);
  const onHf = useCallback((path: string, doc: PMNode) => { hfRef.current = { ...hfRef.current, [path]: doc }; setHfDocs(hfRef.current); touch(); }, [touch]);

  const editor = useEditor({
    extensions: pagedExtensions, content: { type: "doc", content: [] }, editable: false,
    onUpdate: ({ editor }) => { setDoc(editor.getJSON() as PMNode); touch(); },
  });
  editorRef.current = editor;
  useEffect(() => () => { flush(); }, [flush]); // leaving the editor never drops edits
  const [ready, setReady] = useState(false); // read-only until the document arrived: early typing would be wiped by the load
  // emitUpdate=false: the update event would autosave an unchanged draft just for opening it
  useEffect(() => { editor?.setEditable(!ro && ready, false); }, [editor, ro, ready]);
  useEffect(() => { editor?.view.dispatch(editor.state.tr); }, [editor, orgStyles, lk.data]); // redraw heading numbers when the style set arrives
  // page gaps: the plugin measures the editor DOM and reports the layout back (header of page 1 and footer of the last page live in the frame)
  pageState.look = look; pageState.zoom = zoom; pageState.onLayout = setLay;
  useEffect(() => { pageState.tick++; editor?.view.dispatch(editor.state.tr); }, [editor, look, zoom]);

  // Reload the editor only when the server draft changes identity (restore/discard/resolve), not per autosave.
  const loaded = useRef("");
  useEffect(() => {
    if (!d.data || !editor || lk.isPending) return; // opaque blocks need the look's media when they are first drawn
    const k = keyOf(d.data);
    if (loaded.current === k) return;
    loaded.current = k;
    editor.commands.setContent(d.data.content as any, { emitUpdate: false });
    hfRef.current = ((d.data.content as { hf?: Record<string, PMNode> }).hf) ?? {}; setHfDocs(hfRef.current);
    titleRef.current = d.data.title; setTitle(d.data.title);
    setDoc(editor.getJSON() as PMNode); pending.current = false; setStatus("idle"); setReady(true);
  }, [d.data, editor, keyOf, lk.isPending]);

  const st = useEditorState({
    editor, selector: ({ editor: e }) => (e ? { nsel: hasSelection(e) ? blockRange(e)[1] - blockRange(e)[0] : 0, selMode: selectModeOn(e) } : null),
  });

  if (!editor) return null;
  const renderWord = async () => {  // the real exporter + LibreOffice: ground truth for the HTML view
    await flush();
    setPdf((p) => ({ ...p, busy: true, err: "" }));
    try {
      const blob = await apiBlob("/render/pdf", { content: editor.getJSON(), import_id: src.import ?? null, base_doc: src.base_doc ?? null });
      setPdf((p) => { if (p.url) URL.revokeObjectURL(p.url); return { url: URL.createObjectURL(blob), busy: false, err: "" }; });
    } catch (e) { setPdf((p) => ({ ...p, busy: false, err: (e as Error).message })); }
  };
  useEffect(() => { if (pv === "word" && !pdf.url && !pdf.busy) renderWord(); }, [pv]); // eslint-disable-line react-hooks/exhaustive-deps

  // A plain click on a page header/footer (they are not hit-testable: pointer-events off, so geometry decides) opens its editor.
  const openHfAt = (x: number, y: number) => {
    if (!src.import || ro || !lay || !look) return;
    const norm = normalize(look);
    for (const el of Array.from(paneRef.current?.querySelectorAll<HTMLElement>(".docx-hdr, .docx-ftr") ?? [])) {
      const r = el.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) continue;
      const hdr = el.classList.contains("docx-hdr"), gap = el.closest<HTMLElement>(".pgsep");
      const info = lay.pages[Math.min(Math.max(gap ? Number(gap.dataset.page) - (hdr ? 1 : 2) : hdr ? 0 : lay.total - 1, 0), lay.total - 1)];
      const si = Math.min(info.section, norm.sections.length - 1);
      setHfT({ si, kind: hdr ? "header" : "footer", type: pageKind(norm.sections[si], info.inSection, info.number, norm.evenOdd) });
      return;
    }
  };
  const label = { idle: "No changes", saving: "Saving…", saved: "Draft saved", error: "Save failed" }[status];

  return (
    <div className="editorpage" onKeyDown={(e) => {  // Ctrl+A with focus on the toolbar/page would select the web page, not the document
      const t = e.target as HTMLElement;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && !t.closest("input,textarea,select,[contenteditable=true]")) { e.preventDefault(); editor.chain().focus().selectAll().run(); }
    }}>
      <div className="edhead">
        <input className="titleinput" value={title} disabled={ro} aria-label="Title" placeholder="Untitled" onChange={(e) => { setTitle(e.target.value); titleRef.current = e.target.value; touch(); }} />
        <span className={`savestatus ${status}`} data-testid="save-status" data-status={status} role="status">
          {status === "saving" ? <Spinner /> : <Icon n={status === "error" ? "alert" : "check"} size={14} />}{status === "idle" && idleSaved ? "Draft saved" : label}</span>
        {status === "error" && <button className="btn sm" onClick={() => flush()}>Retry</button>}
        <div className="actions">{actions?.({ editor, flush })}</div>
      </div>
      <Ribbon editor={editor} ro={ro} styles={styles} onErr={setErr} structure={struct} onStructure={() => setStruct(!struct)} />
      <NoticeBar bar={noticeBar} testid={noticeTestId} notices={[...notices, ...(err ? [{ id: "err", kind: "err" as const, text: err }] : [])]} />
      <div className="viewbar" role="group" aria-label="View options">
        {!lookSrc && <label>Look <select aria-label="Look" value={src.base_doc ?? ""} onChange={(e) => { setPick(e.target.value); localStorage.setItem(`docgen.look.${me.org?.id}`, e.target.value); }}>
          <option value="">Organization styles</option>{bases.data?.map((b) => <option key={b.id} value={b.id}>Base: {b.name}</option>)}</select></label>}
        <label>Preview <select aria-label="Preview mode" value={pv} onChange={(e) => setPv(e.target.value as typeof pv)}>
          <option value="live">Live (HTML)</option><option value="word">Render as Word does (PDF)</option><option value="off">Off</option></select></label>
        <label>Zoom <select aria-label="Zoom" value={zoom} onChange={(e) => setZoom(+e.target.value)}>
          {[0.4, 0.45, 0.5, 0.6, 0.75, 1, 1.5].map((z) => <option key={z} value={z}>{z * 100}%</option>)}</select></label>
        <button type="button" className={`btn sm ${guides ? "on" : ""}`} aria-pressed={guides} onClick={() => setGuides(!guides)}>Page guides</button>
        <button type="button" className={`btn sm ${struct ? "on" : ""}`} aria-pressed={struct} title="Outline of headings, marked sections, lists and captions" onClick={() => setStruct(!struct)}>Structure</button>
        {pv === "word" && <button type="button" className="btn sm" disabled={pdf.busy} onClick={renderWord}>{pdf.busy ? "Rendering…" : "Re-render PDF"}</button>}
        <span className="muted small" data-testid="page-count" title="Estimated page count (browser layout; Word/LibreOffice may differ)">{lay ? `${lay.total} page${lay.total === 1 ? "" : "s"} (est.)` : ""}</span>
        <button type="button" className={`btn sm ${st?.selMode ? "on" : ""}`} aria-pressed={!!st?.selMode} title="Show a handle on every block: click selects one block, Shift+click a range (works across pages)" onMouseDown={(e) => e.preventDefault()} onClick={() => setSelectMode(editor, !st?.selMode)}>Select blocks</button>
        <span className="small" data-testid="sel-count" aria-live="polite">{st?.nsel ? <b>{st.nsel} block{st.nsel === 1 ? "" : "s"} selected</b> : ""}</span>
        <span className="spacer" />
        <span className="muted small">Styles: {extractStyles(doc).join(", ") || "—"} · Variables: {extractVariables(doc).join(", ") || "—"}</span>
      </div>
      <div className="panes">
        {struct && <Structure editor={editor} doc={doc} ro={ro} />}
        <section className="pane" aria-label="Editor pane" ref={paneRef} onMouseDown={(e) => { downAt.current = [e.clientX, e.clientY]; }}
          onClick={(e) => { const d0 = downAt.current; if (d0 && Math.hypot(e.clientX - d0[0], e.clientY - d0[1]) > 4) return; if (!(window.getSelection()?.isCollapsed ?? true)) return; openHfAt(e.clientX, e.clientY); }}>
          <div className="pane-h">Editor{src.import && <span className="muted small" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}> · click a page header or footer to edit it</span>}</div>
          <TableTools editor={editor} ro={ro} /><div className="docx-stage"><PageFrame look={look} zoom={zoom} guides={guides} hdrHtml={lay ? pageParts(look, lay.pages[0], lay.total).hdr : undefined} ftrHtml={lay ? pageParts(look, lay.pages[lay.total - 1], lay.total).ftr : undefined}><EditorContent editor={editor} aria-label="Editor" /></PageFrame></div></section>
        {pv === "live" && <section className="pane" aria-label="Preview pane"><div className="pane-h">Preview</div><Preview doc={doc} styles={styles} look={look} zoom={zoom} guides={guides} /></section>}
        {pv === "word" && <section className="pane" aria-label="Word rendering pane"><div className="pane-h">Rendered as Word does</div>
          {pdf.err && <ErrorBanner e={new Error(pdf.err)} />}
          {pdf.url ? <iframe className="word-pdf" title="Word rendering (PDF)" src={pdf.url} data-testid="word-pdf" /> : <div className="pane-msg">{pdf.busy ? <><Spinner /> Rendering with LibreOffice…</> : ""}</div>}</section>}
      </div>
      {hfT && (hfq.data ? <HeaderFooterEditor look={look} parts={hfq.data.parts} docs={hfDocs} target={hfT} setTarget={setHfT} onChange={onHf} onClose={() => setHfT(null)} styles={styles} ro={ro} />
        : <Modal title="Edit header/footer" onClose={() => setHfT(null)}>{hfq.error ? <ErrorBanner e={hfq.error} /> : <><Spinner /> Loading…</>}</Modal>)}
    </div>
  );
}

export function SnippetEditor({ id, me, epoch, onCommit, onDiscarded }: { id: string; me: Me; epoch: number; onCommit: () => void; onDiscarded?: () => void }) {
  const qc = useQueryClient(), ui = useUI();
  const [bump, setBump] = useState(0); // forces a reload after discarding the draft
  const qkey = useMemo(() => ["draft", id], [id]);
  const d = useQuery({ queryKey: qkey, queryFn: () => api<Draft>(`/snippets/${id}/draft`), gcTime: 0 }).data;
  const discard = useMutation({
    mutationFn: () => api(`/snippets/${id}/draft`, "DELETE"),
    onSuccess: async () => { await qc.invalidateQueries(); setBump((b) => b + 1); ui.toast("Draft discarded"); onDiscarded?.(); },
    onError: (e: Error) => ui.toast(e.message, "err"),
  });
  return (
    <RichEditor qkey={qkey} me={me} idleSaved={d?.dirty}
      load={() => api<Draft>(`/snippets/${id}/draft`)}
      save={(b) => api<Draft>(`/snippets/${id}/draft`, "PUT", b)}
      keyOf={(x) => { const y = x as Draft; return [id, epoch, bump, y.base_sha, y.conflict, y.head_sha].join(); }}
      onSaved={() => qc.invalidateQueries({ queryKey: ["changes"] })}
      noticeBar={`snippet:${id}`} notices={d?.conflict ? [{ id: "conflict", kind: "warn", text: "HEAD moved since your draft started — resolve it on the commit screen." }] : []}
      actions={({ flush }) => <>
        {me.org?.role !== "viewer" && <button className="btn" onClick={async () => { if (await ui.confirm({ title: "Discard your draft?", danger: true, ok: "Discard draft", body: "Your uncommitted changes to this snippet are lost; the last committed version is restored." })) discard.mutate(); }}><Icon n="trash" />Discard draft</button>}
        <button className="btn primary" onClick={async () => { await flush(); onCommit(); }}><Icon n="commit" />Commit…</button>
      </>} />
  );
}
