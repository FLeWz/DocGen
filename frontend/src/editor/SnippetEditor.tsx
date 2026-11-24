import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/core";
import { NodeSelection } from "@tiptap/pm/state";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiBlob, BaseDoc, Doc, Draft, Me, Style } from "../api";
import { ErrorBanner, Field, Icon, Modal, Notice, NoticeBar, Spinner, useUI } from "../ui";
import { blockRange, hasSelection, selectModeOn, setSelectMode } from "./blockrange";
import { effectiveStyle, extractStyles, extractVariables, NAME_RE, PMNode } from "./doc";
import "./editor.css";
import { headingNumbering, pagedExtensions } from "./extensions";
import { Look, LookSrc, PageFrame, useLook } from "./look";
import { EditorLayout, pageState } from "./pageplugin";
import { pageParts } from "./pagelayout";
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

type VForm = { name: string; default: string; kind: "" | "upper" | "date"; format: string; existing: boolean };
type Status = "idle" | "saving" | "saved" | "error";

export type EditorData = { title: string; content: Doc };
export type SaveBody = { title?: string; content: PMNode; styles: string[]; variables: string[] };

/** TipTap editor + toolbar + preview over any document source (snippet draft, imported DOCX). Saves are debounced 800 ms. */
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
  const [zoom, setZoom] = useState(innerWidth >= 1600 ? 0.6 : 0.45), [guides, setGuides] = useState(false);
  const [pdf, setPdf] = useState<{ url: string; busy: boolean; err: string }>({ url: "", busy: false, err: "" });
  const bases = useQuery({ queryKey: ["base-docs", me.org?.id], queryFn: () => api<BaseDoc[]>("/base-documents"), enabled: !lookSrc });
  const src: LookSrc = lookSrc ?? (pick && bases.data?.some((b) => b.id === pick) ? { base_doc: pick } : {});
  const lk = useLook(src);
  const look = lk.data;
  const orgStyles = useStyles(me, src.base_doc).data;
  const styles = (src.import ? look?.styles : orgStyles) ?? orgStyles ?? [];
  headingNumbering.styles = new Map(styles.filter((s) => s.numbering === "decimal" && s.kind === "heading").map((s) => [s.style_id, { level: s.level }]));
  const [doc, setDoc] = useState<PMNode>({ type: "doc" });
  const [lay, setLay] = useState<EditorLayout | null>(null);
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [err, setErr] = useState("");
  const [vf, setVf] = useState<VForm | null>(null);
  const [dyn, setDyn] = useState<string | null>(null), [lnk, setLnk] = useState<string | null>(null);
  const titleRef = useRef(""), timer = useRef<number>(), pending = useRef(false), chain = useRef<Promise<unknown>>(Promise.resolve());
  const editorRef = useRef<Editor | null>(null);

  // Save everything pending now. Saves are chained so a slow earlier PUT can't overwrite a later one.
  const flush = useCallback((): Promise<unknown> => {
    window.clearTimeout(timer.current);
    const ed = editorRef.current;
    if (!pending.current || !ed) return chain.current;
    pending.current = false;
    const content = ed.getJSON() as PMNode, t = titleRef.current;
    setStatus("saving");
    chain.current = chain.current.then(() => save({ title: t || undefined, content, styles: extractStyles(content), variables: extractVariables(content) }))
      .then((r) => { setErr(""); qc.setQueryData(qkey, r); onSaved?.(); setStatus(pending.current ? "saving" : "saved"); })
      .catch((e: Error) => { pending.current = true; setErr(e.message); setStatus("error"); });
    return chain.current;
  }, [qkey.join(), qc]); // eslint-disable-line react-hooks/exhaustive-deps
  const touch = useCallback(() => { pending.current = true; setStatus("saving"); window.clearTimeout(timer.current); timer.current = window.setTimeout(flush, 800); }, [flush]);

  const editor = useEditor({
    extensions: pagedExtensions, content: { type: "doc", content: [] }, editable: false,
    onUpdate: ({ editor }) => { setDoc(editor.getJSON() as PMNode); touch(); },
  });
  editorRef.current = editor;
  useEffect(() => () => { flush(); }, [flush]); // leaving the editor never drops edits
  const [ready, setReady] = useState(false); // read-only until the document arrived: early typing would be wiped by the load
  // emitUpdate=false: the update event would autosave an unchanged draft just for opening it
  useEffect(() => { editor?.setEditable(!ro && ready, false); }, [editor, ro, ready]);
  useEffect(() => { editor?.view.dispatch(editor.state.tr); }, [editor, orgStyles, look]); // redraw heading numbers when the style set arrives
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
    titleRef.current = d.data.title; setTitle(d.data.title);
    setDoc(editor.getJSON() as PMNode); pending.current = false; setStatus("idle"); setReady(true);
  }, [d.data, editor, keyOf, lk.isPending]);

  const st = useEditorState({
    editor, selector: ({ editor: e }) => {
      if (!e) return null;
      const $f = e.state.selection.$from, p = $f.parent;
      const inList = [...Array(Math.max($f.depth, 0) + 1).keys()].some((i) => ["bulletList", "orderedList"].includes($f.node(i).type.name));
      const sel = e.state.selection;
      return { style: effectiveStyle({ type: p.type.name, attrs: p.attrs }, inList) ?? "Normal",
        bold: e.isActive("bold"), italic: e.isActive("italic"), underline: e.isActive("underline"), link: e.isActive("link"),
        jc: (p.attrs.pfmt?.jc as string | undefined) ?? "left", bullet: e.isActive("bulletList"), ordered: e.isActive("orderedList"), table: e.isActive("table"), headerRow: e.isActive("tableHeader"),
        dynamic: (e.getAttributes("table").dynamicList as string | null) ?? null,
        variable: sel instanceof NodeSelection && sel.node.type.name === "variable",
        nsel: hasSelection(e) ? blockRange(e)[1] - blockRange(e)[0] : 0, selMode: selectModeOn(e) };
    },
  });

  if (!editor) return null;
  const run = () => editor.chain().focus();
  /** Direct cell formatting (cfmt) on the selected cells, merged into what they have: shading and/or borders. */
  const cellFmt = (patch: Record<string, unknown>) => {
    const { selection } = editor.state, cells: { pos: number; attrs: Record<string, unknown> }[] = [];
    editor.state.doc.nodesBetween(selection.from, selection.to, (n, pos) => { if (n.type.name === "tableCell" || n.type.name === "tableHeader") { cells.push({ pos, attrs: n.attrs }); return false; } });
    if (!cells.length) { const $f = selection.$from; for (let d = $f.depth; d > 0; d--) if (["tableCell", "tableHeader"].includes($f.node(d).type.name)) { cells.push({ pos: $f.before(d), attrs: $f.node(d).attrs }); break; } }
    const tr = editor.state.tr;
    for (const c of cells) tr.setNodeMarkup(c.pos, undefined, { ...c.attrs, cfmt: { ...((c.attrs.cfmt as object) ?? {}), ...patch } });
    editor.view.dispatch(tr); editor.view.focus();
  };
  const applyStyle = (sid: string) => {
    const s = styles.find((x) => x.style_id === sid);
    if (!s) return;
    if (s.kind === "heading" && (s.level ?? 9) <= 4) run().setNode("heading", { level: s.level, styleId: s.style_id }).run();
    else run().setNode("paragraph", { styleId: s.style_id }).run();
  };
  const setAlign = (jc: string) => {  // paragraph/heading direct format; other pfmt keys of an import stay
    const t = editor.state.selection.$from.parent;
    run().updateAttributes(t.type.name, { pfmt: { ...(t.attrs.pfmt ?? {}), jc } }).run();
  };
  const renderWord = async () => {  // the real exporter + LibreOffice: ground truth for the HTML view
    await flush();
    setPdf((p) => ({ ...p, busy: true, err: "" }));
    try {
      const blob = await apiBlob("/render/pdf", { content: editor.getJSON(), import_id: src.import ?? null, base_doc: src.base_doc ?? null });
      setPdf((p) => { if (p.url) URL.revokeObjectURL(p.url); return { url: URL.createObjectURL(blob), busy: false, err: "" }; });
    } catch (e) { setPdf((p) => ({ ...p, busy: false, err: (e as Error).message })); }
  };
  useEffect(() => { if (pv === "word" && !pdf.url && !pdf.busy) renderWord(); }, [pv]); // eslint-disable-line react-hooks/exhaustive-deps
  const openVariable = () => {
    const sel = editor.state.selection;
    if (sel instanceof NodeSelection && sel.node.type.name === "variable") {
      const a = sel.node.attrs, [k, ...f] = String(a.filter ?? "").split(":");
      setVf({ name: a.name, default: a.default ?? "", kind: (k as VForm["kind"]) || "", format: f.join(":") || "YYYY-MM-DD", existing: true });
    } else {
      const txt = editor.state.doc.textBetween(sel.from, sel.to, " ").trim();
      setVf({ name: txt.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "_$1"), default: txt, kind: "", format: "YYYY-MM-DD", existing: false });
    }
  };
  const saveVariable = () => {
    if (!vf) return;
    if (!NAME_RE.test(vf.name)) { setErr("Variable name: letters, digits, _ (optionally row.field)"); return; }
    setErr("");
    const attrs = { name: vf.name, default: vf.default, filter: vf.kind === "date" ? `date:${vf.format}` : vf.kind || null };
    if (vf.existing) run().updateAttributes("variable", attrs).run();
    else run().insertContent({ type: "variable", attrs }).run();
    setVf(null);
  };
  const saveDynamic = () => {
    const v = (dyn ?? "").trim();
    if (v && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(v)) { setErr("List variable name: letters, digits, _"); return; }
    setErr(""); run().updateAttributes("table", { dynamicList: v || null }).run(); setDyn(null);
  };
  const saveLink = () => {
    const href = (lnk ?? "").trim();
    if (!href) run().extendMarkRange("link").unsetLink().run(); else run().extendMarkRange("link").setLink({ href }).run();
    setLnk(null);
  };
  const upload = async (f?: File) => {
    if (!f) return;
    try {
      const fd = new FormData(); fd.append("file", f);
      const img = await api<{ id: string }>("/images", "POST", fd);
      setErr(""); run().insertContent({ type: "image", attrs: { imageId: img.id, alt: f.name, width: null } }).run();
    } catch (e) { setErr((e as Error).message); }
  };
  const label = { idle: "No changes", saving: "Saving…", saved: "Draft saved", error: "Save failed" }[status];
  const B = (p: Omit<React.ComponentProps<typeof Btn>, "ro">) => <Btn ro={ro} {...p} />;

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
      <div className="tbar" role="toolbar" aria-label="Formatting">
        <div className="tgroup">
          <select aria-label="Paragraph style" title="Paragraph style" disabled={ro} value={st?.style ?? "Normal"} onChange={(e) => applyStyle(e.target.value)}>
            {styles.filter((s) => s.kind === "paragraph" || s.kind === "heading").map((s) => <option key={s.style_id} value={s.style_id}>{s.name}</option>)}
          </select>
        </div>
        <div className="tgroup">
          <B i="bold" t="Bold" on={st?.bold} fn={() => run().toggleBold().run()} /><B i="italic" t="Italic" on={st?.italic} fn={() => run().toggleItalic().run()} /><B i="underline" t="Underline" on={st?.underline} fn={() => run().toggleUnderline().run()} />
        </div>
        <div className="tgroup">
          <B i="ul" t="Bullet list" on={st?.bullet} fn={() => run().toggleBulletList().run()} /><B i="ol" t="Ordered list" on={st?.ordered} fn={() => run().toggleOrderedList().run()} />
        </div>
        <div className="tgroup">
          {([["left", "left", "Align left"], ["center", "center", "Align center"], ["right", "right", "Align right"], ["both", "justify", "Justify"]] as const).map(([jc, i, t]) =>
            <B key={jc} i={i} t={t} on={st?.jc === jc} fn={() => setAlign(jc)} />)}
        </div>
        <div className="tgroup">
          <B i="link" t="Link" on={st?.link} fn={() => setLnk(editor.getAttributes("link").href ?? "https://")} />
          <B i="image" t="Insert image" fn={() => document.getElementById("img-in")?.click()} />
          <input id="img-in" type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden aria-label="Image file" onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          <B i="table" t="Insert table" fn={() => run().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()} />
          <select className="tb-sel" aria-label="Insert table with size" value="" onChange={(e) => { const [r, c] = e.target.value.split("x").map(Number); if (r) run().insertTable({ rows: r, cols: c, withHeaderRow: true }).run(); }}>
            <option value="">Rows×cols</option>{["2x2", "3x2", "3x3", "4x3", "5x4", "6x5", "8x6", "10x4"].map((v) => <option key={v} value={v}>{v.replace("x", " × ")}</option>)}</select>
        </div>
        {st?.table && <div className="tgroup" role="group" aria-label="Table">
          <B i="rowAdd" t="Add row below" fn={() => run().addRowAfter().run()} /><B i="colAdd" t="Add column right" fn={() => run().addColumnAfter().run()} />
          <B i="rowDel" t="Delete row" fn={() => run().deleteRow().run()} /><B i="colDel" t="Delete column" fn={() => run().deleteColumn().run()} />
          <B i="rowAdd" t="Toggle header row" label="Header row" on={st?.headerRow} fn={() => run().toggleHeaderRow().run()} />
          <input type="color" className="tb-color" aria-label="Cell shading" title="Cell shading" defaultValue="#d9e2f3" onChange={(e) => cellFmt({ shd: e.target.value.slice(1).toUpperCase() })} />
          <B i="table" t="Borders on selected cells" label="Borders" fn={() => cellFmt({ bdr: BORDERS_ON })} /><B i="table" t="Remove borders of selected cells" label="No borders" fn={() => cellFmt({ bdr: BORDERS_OFF })} />
          <B i="merge" t="Merge cells" fn={() => run().mergeCells().run()} /><B i="split" t="Split cell" fn={() => run().splitCell().run()} />
          <B i="trash" t="Delete table" fn={() => run().deleteTable().run()} />
          <B i="refresh" t="Mark table dynamic" label={st.dynamic ? `Dynamic: ${st.dynamic}` : "Dynamic table"} on={!!st.dynamic} fn={() => setDyn(st.dynamic ?? "")} />
        </div>}
        <div className="tgroup"><B i="var" t="Make variable" label={st?.variable ? "Edit variable" : "Variable"} on={st?.variable} fn={openVariable} /></div>
      </div>
      <NoticeBar bar={noticeBar} testid={noticeTestId} notices={[...notices, ...(err ? [{ id: "err", kind: "err" as const, text: err }] : [])]} />
      <div className="viewbar" role="group" aria-label="View options">
        {!lookSrc && <label>Look <select aria-label="Look" value={src.base_doc ?? ""} onChange={(e) => { setPick(e.target.value); localStorage.setItem(`docgen.look.${me.org?.id}`, e.target.value); }}>
          <option value="">Organization styles</option>{bases.data?.map((b) => <option key={b.id} value={b.id}>Base: {b.name}</option>)}</select></label>}
        <label>Preview <select aria-label="Preview mode" value={pv} onChange={(e) => setPv(e.target.value as typeof pv)}>
          <option value="live">Live (HTML)</option><option value="word">Render as Word does (PDF)</option><option value="off">Off</option></select></label>
        <label>Zoom <select aria-label="Zoom" value={zoom} onChange={(e) => setZoom(+e.target.value)}>
          {[0.4, 0.45, 0.5, 0.6, 0.75, 1].map((z) => <option key={z} value={z}>{z * 100}%</option>)}</select></label>
        <button type="button" className={`btn sm ${guides ? "on" : ""}`} aria-pressed={guides} onClick={() => setGuides(!guides)}>Page guides</button>
        {pv === "word" && <button type="button" className="btn sm" disabled={pdf.busy} onClick={renderWord}>{pdf.busy ? "Rendering…" : "Re-render PDF"}</button>}
        <span className="muted small" data-testid="page-count" title="Estimated page count (browser layout; Word/LibreOffice may differ)">{lay ? `${lay.total} page${lay.total === 1 ? "" : "s"} (est.)` : ""}</span>
        <button type="button" className={`btn sm ${st?.selMode ? "on" : ""}`} aria-pressed={!!st?.selMode} title="Show a handle on every block: click selects one block, Shift+click a range (works across pages)" onMouseDown={(e) => e.preventDefault()} onClick={() => setSelectMode(editor, !st?.selMode)}>Select blocks</button>
        <span className="small" data-testid="sel-count" aria-live="polite">{st?.nsel ? <b>{st.nsel} block{st.nsel === 1 ? "" : "s"} selected</b> : ""}</span>
        <span className="spacer" />
        <span className="muted small">Styles: {extractStyles(doc).join(", ") || "—"} · Variables: {extractVariables(doc).join(", ") || "—"}</span>
      </div>
      <div className="panes">
        <section className="pane" aria-label="Editor pane"><div className="pane-h">Editor</div>
          <div className="docx-stage"><PageFrame look={look} zoom={zoom} guides={guides} hdrHtml={lay ? pageParts(look, lay.pages[0], lay.total).hdr : undefined} ftrHtml={lay ? pageParts(look, lay.pages[lay.total - 1], lay.total).ftr : undefined}><EditorContent editor={editor} aria-label="Editor" /></PageFrame></div></section>
        {pv === "live" && <section className="pane" aria-label="Preview pane"><div className="pane-h">Preview</div><Preview doc={doc} styles={styles} look={look} zoom={zoom} guides={guides} /></section>}
        {pv === "word" && <section className="pane" aria-label="Word rendering pane"><div className="pane-h">Rendered as Word does</div>
          {pdf.err && <ErrorBanner e={new Error(pdf.err)} />}
          {pdf.url ? <iframe className="word-pdf" title="Word rendering (PDF)" src={pdf.url} data-testid="word-pdf" /> : <div className="pane-msg">{pdf.busy ? <><Spinner /> Rendering with LibreOffice…</> : ""}</div>}</section>}
      </div>
      {vf && (
        <Modal title={vf.existing ? "Edit variable" : "Make variable"} onClose={() => setVf(null)} actions={<><button className="btn" type="button" onClick={() => setVf(null)}>Cancel</button><button className="btn primary" form="var-form">{vf.existing ? "Update" : "Create"} variable</button></>}>
          <form id="var-form" className="stack" onSubmit={(e) => { e.preventDefault(); saveVariable(); }}>
            <Field label="Variable name" hint="Letters, digits and _ (e.g. client_name)">{(id) => <input id={id} value={vf.name} onChange={(e) => setVf({ ...vf, name: e.target.value })} autoFocus />}</Field>
            <Field label="Variable default" hint="Shown in the preview when the document gives no value">{(id) => <input id={id} value={vf.default} onChange={(e) => setVf({ ...vf, default: e.target.value })} />}</Field>
            <Field label="Variable filter">{(id) => <select id={id} value={vf.kind} onChange={(e) => setVf({ ...vf, kind: e.target.value as VForm["kind"] })}><option value="">No filter</option><option value="upper">UPPERCASE</option><option value="date">Date format</option></select>}</Field>
            {vf.kind === "date" && <Field label="Date format" hint="e.g. YYYY-MM-DD">{(id) => <input id={id} value={vf.format} onChange={(e) => setVf({ ...vf, format: e.target.value })} />}</Field>}
            <ErrorBanner e={err ? new Error(err) : null} />
          </form>
        </Modal>)}
      {lnk !== null && (
        <Modal title="Link" onClose={() => setLnk(null)} actions={<><button className="btn" type="button" onClick={() => setLnk(null)}>Cancel</button><button className="btn primary" form="link-form">{lnk.trim() ? "Apply link" : "Remove link"}</button></>}>
          <form id="link-form" onSubmit={(e) => { e.preventDefault(); saveLink(); }}><Field label="Link URL" hint="http, https or mailto. Empty removes the link.">{(id) => <input id={id} value={lnk} onChange={(e) => setLnk(e.target.value)} autoFocus />}</Field></form>
        </Modal>)}
      {dyn !== null && (
        <Modal title="Dynamic table" onClose={() => setDyn(null)} actions={<><button className="btn" type="button" onClick={() => setDyn(null)}>Cancel</button><button className="btn primary" form="dyn-form">{dyn.trim() ? "Make dynamic" : "Make static"}</button></>}>
          <form id="dyn-form" className="stack" onSubmit={(e) => { e.preventDefault(); saveDynamic(); }}>
            <p className="muted">The rows of a dynamic table come from a list variable. The last row is the row template; use <code>{"{{ row.field }}"}</code> variable chips in it.</p>
            <Field label="List variable" hint="Empty makes the table static again.">{(id) => <input id={id} value={dyn} onChange={(e) => setDyn(e.target.value)} autoFocus />}</Field>
            <ErrorBanner e={err ? new Error(err) : null} />
          </form>
        </Modal>)}
    </div>
  );
}

const side = (val: string) => ({ val, sz: 4, color: "000000" });
const BORDERS_ON = Object.fromEntries(["top", "left", "bottom", "right"].map((k) => [k, side("single")]));
const BORDERS_OFF = Object.fromEntries(["top", "left", "bottom", "right"].map((k) => [k, { val: "nil", sz: 0 }]));

function Btn({ i, t, label, on, fn, ro }: { i: string; t: string; label?: string; on?: boolean; fn: () => void; ro?: boolean }) {
  return (
    <button type="button" className={`tb ${on ? "on" : ""} ${label ? "wide" : ""}`} disabled={ro} title={t} aria-label={t} aria-pressed={on}
      onMouseDown={(e) => e.preventDefault()} onClick={fn}><Icon n={i} />{label && <span>{label}</span>}</button>
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
