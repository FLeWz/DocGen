import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/core";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import { useSearchParams } from "react-router-dom";
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, apiBlob, BaseDoc, convert, Doc, Draft, Me, Style } from "../api";
import { EditorHandle, LockBar, useEditLock } from "../locks";
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
import { autosaveAction, DEFAULT_MODE, leaveText, MODES, modeOf, ModeId, offsetOf, textKey, TextError } from "./modes";
import { TextEditor, TextHandle } from "./TextEditor";
import { contentBlocks } from "./render";
import { loadPrefs, PV_KEY, PvMode, ZOOM_KEY, ZOOMS } from "./prefs";

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
export function RichEditor({ qkey, load, save, keyOf, me, idleSaved, notices = [], noticeBar = "editor", noticeTestId, actions, onSaved, lookSrc, locked, handle }: {
  locked?: boolean; handle?: EditorHandle; // locked: somebody else holds the edit lock -> read-only
  lookSrc?: LookSrc; // fixed look (imported DOCX); else the user picks a base document
  qkey: unknown[]; load: () => Promise<EditorData>; save: (b: SaveBody) => Promise<EditorData>; keyOf: (d: EditorData) => string; me: Me;
  idleSaved?: boolean; notices?: Notice[]; noticeBar?: string; noticeTestId?: string; actions?: (h: { editor: Editor; flush: () => Promise<unknown> }) => ReactNode; onSaved?: () => void;
}) {
  const qc = useQueryClient();
  const ro = me.org?.role === "viewer" || !!locked;
  const d = useQuery({ queryKey: qkey, queryFn: load, gcTime: 0 }); // no stale cache on reopen: its refetch would reload (wipe) the editor after typing started
  const [pick, setPick] = useState(() => localStorage.getItem(`docgen.look.${me.org?.id}`) ?? "");
  const [prefs] = useState(() => loadPrefs(localStorage));
  const [pv, setPv] = useState<PvMode>(prefs.pv), [zoom, setZoom] = useState(prefs.zoom);
  const [sp, setSp] = useSearchParams(), focus = sp.get("focus") === "1"; // focus mode: shell hidden by a class on <html> (app.css)
  const setFocus = useCallback((on: boolean) => setSp((p) => { const n = new URLSearchParams(p); if (on) n.set("focus", "1"); else n.delete("focus"); return n; }, { replace: true }), [setSp]);
  useEffect(() => {
    document.documentElement.classList.toggle("ed-focus", focus);
    const h = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.code === "KeyF") { e.preventDefault(); setFocus(!focus); }
      else if (focus && e.key === "Escape" && !document.querySelector("dialog[open], [role=menu]")) setFocus(false);
    };
    window.addEventListener("keydown", h, true); // capture: the ribbon and ProseMirror swallow keys in the bubble phase
    return () => { window.removeEventListener("keydown", h, true); document.documentElement.classList.remove("ed-focus"); };
  }, [focus, setFocus]);
  const [guides, setGuides] = useState(false), [struct, setStruct] = useState(false);
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
  // Editing modes: Word is the TipTap page editor; the others are text views of the same JSON (backend pandoc bridge). Never persisted: every open starts in Word.
  const [mode, setMode] = useState<ModeId>(DEFAULT_MODE), modeRef = useRef<ModeId>(DEFAULT_MODE);
  modeRef.current = mode;
  type Conv = { errors: TextError[]; warnings: string[]; content: PMNode | null; ask?: boolean };
  const [conv, setConv] = useState<Conv | null>(null), [busy, setBusy] = useState(false);
  const txtRef = useRef(""), base = useRef<PMNode | null>(null), textDoc = useRef<PMNode | null>(null), parseT = useRef<number>(), parseSeq = useRef(0), tx = useRef<TextHandle>(null);
  const tkey = textKey(qkey, mode);
  const editorRef = useRef<Editor | null>(null), paneRef = useRef<HTMLElement>(null), downAt = useRef<[number, number] | null>(null);

  // Save everything pending now. Saves are chained so a slow earlier PUT can't overwrite a later one.
  const flush = useCallback((): Promise<unknown> => {
    window.clearTimeout(timer.current);
    const ed = editorRef.current;
    if (!pending.current || !ed || !loaded.current) return chain.current; // before the document arrived the editor is empty: saving it would wipe the stored one
    pending.current = false;
    const body = (modeRef.current !== "word" && textDoc.current ? textDoc.current : ed.getJSON()) as PMNode, t = titleRef.current;
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
  if (handle) handle.current = { flush, json: () => editorRef.current?.getJSON() };
  useEffect(() => () => { flush(); }, [flush]); // leaving the editor never drops edits
  const parse = (text: string) => convert.fromText(modeOf(modeRef.current).format!, text, base.current);
  // Text mode autosave: parse after a pause; a clean parse becomes the draft (JSON), a failed one never does (the text waits in localStorage)
  const onText = (v: string) => {
    txtRef.current = v;
    setStatus("saving");
    window.clearTimeout(parseT.current);
    parseT.current = window.setTimeout(async () => {
      const seq = ++parseSeq.current, key = textKey(qkey, modeRef.current);
      try {
        const r = await parse(v);
        if (seq !== parseSeq.current) return;
        const a = autosaveAction(r);
        setConv({ errors: r.errors, warnings: r.warnings, content: r.content });
        if (a.save) { textDoc.current = a.save; setDoc(a.save); localStorage.removeItem(key); setErr(""); touch(); }
        else { localStorage.setItem(key, v); setErr("The text has errors: the draft keeps the last good version"); setStatus("error"); }
      } catch (e) { if (seq === parseSeq.current) { localStorage.setItem(key, v); setErr((e as Error).message); setStatus("error"); } }
    }, 500);
  };
  const toText = async (to: ModeId) => {
    if (!editor || busy) return;
    setBusy(true);
    try {
      await flush();
      const content = editor.getJSON() as PMNode, r = await convert.toText(modeOf(to).format!, content), key = textKey(qkey, to), kept = localStorage.getItem(key);
      base.current = content; textDoc.current = null; txtRef.current = kept ?? r.text; setConv(null); setMode(to);
      if (kept && kept !== r.text) { setErr("Restored the text you had not saved last time (it had errors)"); onText(kept); }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const toWord = async (lossy = false): Promise<boolean> => {
    if (!editor || busy) return false;
    setBusy(true);
    window.clearTimeout(parseT.current); parseSeq.current++;
    try {
      const r = await parse(txtRef.current), g = leaveText(r, lossy);
      if (g.kind !== "go") { setConv({ errors: r.errors, warnings: r.warnings, content: r.content, ask: g.kind === "ask" }); return false; }
      editor.commands.setContent(g.content as any, { emitUpdate: false });
      const json = editor.getJSON() as PMNode;
      setDoc(json); textDoc.current = null; localStorage.removeItem(textKey(qkey, modeRef.current));
      if (JSON.stringify(json) !== JSON.stringify(base.current)) { pending.current = true; touch(); }
      setConv(null); setErr(""); setMode("word");
      return true;
    } catch (e) { setErr((e as Error).message); return false; } finally { setBusy(false); }
  };
  // text -> text goes through Word (parse with the same error/lossy rules, then write the other syntax)
  const switchTo = async (to: ModeId) => { if (to === mode) return; if (to === "word") toWord(); else if (mode === "word") toText(to); else if (await toWord()) toText(to); };
  const [ready, setReady] = useState(false); // read-only until the document arrived: early typing would be wiped by the load
  // emitUpdate=false: the update event would autosave an unchanged draft just for opening it
  useEffect(() => { editor?.setEditable(!ro && ready, false); }, [editor, ro, ready]);
  useEffect(() => { editor?.view.dispatch(editor.state.tr); }, [editor, orgStyles, lk.data]); // redraw heading numbers when the style set arrives
  // page gaps: the plugin measures the editor DOM and reports the layout back (header of page 1 and footer of the last page live in the frame)
  pageState.look = look; pageState.zoom = zoom; pageState.onLayout = setLay;
  useEffect(() => { pageState.tick++; editor?.view.dispatch(editor.state.tr); }, [editor, look, zoom, focus]);

  // Reload the editor only when the server draft changes identity (restore/discard/resolve), not per autosave.
  const loaded = useRef("");
  useEffect(() => {
    if (!d.data || !editor || lk.isPending) return; // opaque blocks need the look's media when they are first drawn
    const k = keyOf(d.data);
    if (loaded.current === k) return;
    loaded.current = k;
    editor.commands.setContent(d.data.content as any, { emitUpdate: false });
    hfRef.current = ((d.data.content as { hf?: Record<string, PMNode> }).hf) ?? {}; setHfDocs(hfRef.current);
    textDoc.current = null; setConv(null); setMode("word"); // a reload from the server (restore/discard/resolve) ends a text session
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
      const blob = await apiBlob("/render/pdf", { content: mode !== "word" && textDoc.current ? textDoc.current : editor.getJSON(), import_id: src.import ?? null, base_doc: src.base_doc ?? null });
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
        <div className="actions">
          <button type="button" className={`btn icon ${focus ? "on" : ""}`} data-testid="focus-toggle" aria-pressed={focus} aria-label={focus ? "Exit fullscreen editor" : "Fullscreen editor"}
            title={focus ? "Exit fullscreen editor (Esc or Ctrl+Shift+F)" : "Fullscreen editor (F11-like, Esc to exit) · Ctrl+Shift+F"} onClick={() => setFocus(!focus)}><Icon n={focus ? "shrink" : "expand"} /></button>
          {document.documentElement.requestFullscreen && <button type="button" className="btn sm" data-testid="browser-fullscreen" aria-label="Browser fullscreen" title="Browser fullscreen (hides the browser chrome too)"
            onClick={() => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen())}>F11</button>}
          {actions?.({ editor, flush })}</div>
      </div>
      {mode === "word" && <Ribbon editor={editor} ro={ro} styles={styles} onErr={setErr} structure={struct} onStructure={() => setStruct(!struct)} />}
      <NoticeBar bar={noticeBar} testid={noticeTestId} notices={[...notices, ...(err ? [{ id: "err", kind: "err" as const, text: err }] : [])]} />
      <div className="viewbar" role="group" aria-label="View options">
        <div className="seg" role="group" aria-label="Editing mode">
          {MODES.map((m) => {
            const why = m.id === mode ? "" : me.org?.role === "viewer" ? "Viewers cannot switch modes" : mode === "word" && struct ? "Close the Structure sidebar first (it is Word-only)" : mode === "word" && hfT ? "Close the header/footer editor first (it is Word-only)" : !ready || busy ? "Loading…" : "";
            return <button key={m.id} type="button" className={mode === m.id ? "on" : ""} aria-pressed={mode === m.id} data-testid={`mode-${m.id}`} title={why || m.hint} disabled={!!why} onClick={() => switchTo(m.id)}>{m.label}</button>;
          })}
        </div>
        {mode === "word" && !lookSrc && <label>Look <select aria-label="Look" value={src.base_doc ?? ""} onChange={(e) => { setPick(e.target.value); localStorage.setItem(`docgen.look.${me.org?.id}`, e.target.value); }}>
          <option value="">Organization styles</option>{bases.data?.map((b) => <option key={b.id} value={b.id}>Base: {b.name}</option>)}</select></label>}
        <label>Preview <select aria-label="Preview mode" value={pv} onChange={(e) => { setPv(e.target.value as PvMode); localStorage.setItem(PV_KEY, e.target.value); }}>
          <option value="off">No preview</option><option value="live">Live (HTML)</option><option value="word">Word (PDF)</option></select></label>
        <label>Zoom <select aria-label="Zoom" value={zoom} onChange={(e) => { setZoom(+e.target.value); localStorage.setItem(ZOOM_KEY, e.target.value); }}>
          {ZOOMS.map((z) => <option key={z} value={z}>{z * 100}%</option>)}</select></label>
        {mode === "word" && <><button type="button" className={`btn sm ${guides ? "on" : ""}`} aria-pressed={guides} onClick={() => setGuides(!guides)}>Page guides</button>
        <button type="button" className={`btn sm ${struct ? "on" : ""}`} aria-pressed={struct} title="Outline of headings, marked sections, lists and captions" onClick={() => setStruct(!struct)}>Structure</button></>}
        {pv === "word" && <button type="button" className="btn sm" disabled={pdf.busy} onClick={renderWord}>{pdf.busy ? "Rendering…" : "Re-render PDF"}</button>}
        {mode === "word" && <button type="button" className={`btn sm ${st?.selMode ? "on" : ""}`} aria-pressed={!!st?.selMode} title="Show a handle on every block: click selects one block, Shift+click a range (works across pages)" onMouseDown={(e) => e.preventDefault()} onClick={() => setSelectMode(editor, !st?.selMode)}>Select blocks</button>}
      </div>
      <div className={`panes ${pv === "off" && !src.import ? "single" : ""}`}>
        {mode !== "word" && <section className="pane" aria-label="Text editor pane">
          <div className="pane-h">{modeOf(mode).label} <span className="muted small" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>· the same content as text; saved as the usual draft when it parses</span></div>
          <TextEditor key={`${mode}:${loaded.current}`} ref={tx} mode={mode} value={txtRef.current} readOnly={ro} onChange={onText} label={`${modeOf(mode).label} source`} />
          {conv && (conv.errors.length > 0 || conv.warnings.length > 0) && <div className={`mderrs ${conv.errors.length ? "bad" : "warn"}`} data-testid="text-errors" role="alert">
            <ul>
              {conv.errors.map((e, i) => <li key={`e${i}`}><button type="button" className="linklike" onClick={() => tx.current?.jump(offsetOf(txtRef.current, e.line, e.column))}>Line {e.line}, column {e.column}</button> {e.message}</li>)}
              {conv.warnings.map((w, i) => <li key={`w${i}`}>{w}</li>)}
            </ul>
            {!conv.errors.length && conv.ask && <button type="button" className="btn sm" data-testid="switch-lossy" onClick={() => toWord(true)}>Switch anyway (lossy)</button>}
          </div>}
        </section>}
        {mode === "word" && struct && <Structure editor={editor} doc={doc} ro={ro} />}
        {mode === "word" && <section className="pane" aria-label="Editor pane" ref={paneRef} onMouseDown={(e) => { downAt.current = [e.clientX, e.clientY]; }}
          onClick={(e) => { const d0 = downAt.current; if (d0 && Math.hypot(e.clientX - d0[0], e.clientY - d0[1]) > 4) return; if (!(window.getSelection()?.isCollapsed ?? true)) return; openHfAt(e.clientX, e.clientY); }}>
          <div className="pane-h">Editor{src.import && <span className="muted small" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}> · click a page header or footer to edit it</span>}</div>
          <TableTools editor={editor} ro={ro} /><div className="docx-stage"><PageFrame look={look} zoom={zoom} guides={guides} hdrHtml={lay ? pageParts(look, lay.pages[0], lay.total).hdr : undefined} ftrHtml={lay ? pageParts(look, lay.pages[lay.total - 1], lay.total).ftr : undefined}><EditorContent editor={editor} aria-label="Editor" /></PageFrame></div></section>}
        {pv === "live" && <section className="pane" aria-label="Preview pane"><div className="pane-h">Preview</div><Preview doc={doc} styles={styles} look={look} zoom={zoom} guides={guides} /></section>}
        {pv === "word" && <section className="pane" aria-label="Word rendering pane"><div className="pane-h">Rendered as Word does</div>
          {pdf.err && <ErrorBanner e={new Error(pdf.err)} />}
          {pdf.url ? <iframe className="word-pdf" title="Word rendering (PDF)" src={pdf.url} data-testid="word-pdf" /> : <div className="pane-msg">{pdf.busy ? <><Spinner /> Rendering with LibreOffice…</> : ""}</div>}</section>}
      </div>
      <div className="statusbar muted small">
        <span data-testid="sel-count" aria-live="polite">{st?.nsel ? <b>{st.nsel} block{st.nsel === 1 ? "" : "s"} selected</b> : ""}</span>
        <span data-testid="page-count" title="Estimated page count (browser layout; Word/LibreOffice may differ)">{lay ? `${lay.total} page${lay.total === 1 ? "" : "s"} (est.)` : ""}</span>
        <span className="vb-info">Styles: {extractStyles(doc).join(", ") || "—"} · Variables: {extractVariables(doc).join(", ") || "—"}</span>
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
  const viewer = me.org?.role === "viewer";
  const lock = useEditLock("snippets", id, !viewer), handle: EditorHandle = useRef(null);
  useEffect(() => { if (lock.epoch) qc.refetchQueries({ queryKey: qkey }).then(() => setBump((b) => b + 1)); }, [lock.epoch]); // eslint-disable-line react-hooks/exhaustive-deps  rights arrived: show the draft the previous holder left
  const discard = useMutation({
    mutationFn: () => api(`/snippets/${id}/draft`, "DELETE"),
    onSuccess: async () => { await qc.invalidateQueries(); setBump((b) => b + 1); ui.toast("Draft discarded"); onDiscarded?.(); },
    onError: (e: Error) => ui.toast(e.message, "err"),
  });
  const by = d?.last_editor && d.contributors.length > 1 ? ` · edited by ${d.contributors.map((c) => c.name).join(", ")}` : "";
  return (<>
    <LockBar lock={lock} me={me} handle={handle} />
    <RichEditor qkey={qkey} me={me} idleSaved={d?.dirty} locked={!lock.held} handle={handle}
      load={() => api<Draft>(`/snippets/${id}/draft`)}
      save={(b) => api<Draft>(`/snippets/${id}/draft`, "PUT", b).catch((e) => { if (e instanceof ApiError && (e.detail as { error?: string })?.error === "locked") lock.markLost(); throw e; })}
      keyOf={(x) => { const y = x as Draft; return [id, epoch, bump, y.base_sha, y.conflict, y.head_sha].join(); }}
      onSaved={() => qc.invalidateQueries({ queryKey: ["changes"] })}
      noticeBar={`snippet:${id}`} notices={[...(d?.conflict ? [{ id: "conflict", kind: "warn" as const, text: "HEAD moved since your draft started — resolve it on the commit screen." }] : []),
        ...(by ? [{ id: "by", kind: "info" as const, text: `Shared draft${by}` }] : [])]}
      actions={({ flush }) => <>
        {me.org?.role !== "viewer" && <button className="btn" disabled={!lock.held} onClick={async () => { if (await ui.confirm({ title: "Discard the draft?", danger: true, ok: "Discard draft", body: "The uncommitted changes to this snippet (everyone's) are lost; the last committed version is restored." })) discard.mutate(); }}><Icon n="trash" />Discard draft</button>}
        <button className="btn primary" disabled={!lock.held} onClick={async () => { await flush(); onCommit(); }}><Icon n="commit" />Commit…</button>
      </>} />
  </>);
}
