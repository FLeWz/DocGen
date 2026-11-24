import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Doc, isAdmin, Me } from "../api";
import { Candidates } from "../builder/FindVariables";
import { hasContentSlot, Pick } from "../builder/picks";
import { blockRange } from "../editor/blockrange";
import { RichEditor } from "../editor/SnippetEditor";
import { canEdit, folderLabel, ImportRow, useFolders, useImports } from "../data";
import { PageHead } from "../Shell";
import { Badge, Banner, Empty, ErrorBanner, Field, Icon, Modal, Skeleton, Spinner, ago, useUI } from "../ui";

type ImportDoc = ImportRow & { content: Doc; warnings: string[] };
type Section = { title: string; start: number; end: number; blocks: number; level: number | null };
type Created = { id: string; title: string; slug: string; warnings: string[] };

export function ImportsPage({ me }: { me: Me }) {
  const qc = useQueryClient(), nav = useNavigate(), ui = useUI();
  const list = useImports(me), edit = canEdit(me);
  const [over, setOver] = useState(false);
  const upload = useMutation({ mutationFn: (fd: FormData) => api<ImportRow>("/import/docx", "POST", fd), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["imports"] }); nav(`/imports/${r.id}`); } });
  const send = (f?: File) => { if (!f) return; const fd = new FormData(); fd.append("file", f); upload.mutate(fd); };
  const del = async (i: ImportRow) => {
    if (!(await ui.confirm({ title: `Delete import “${i.title}”?`, danger: true, ok: "Delete import", body: "The uploaded file and your edits are removed. Snippets already created from it stay." }))) return;
    api(`/imports/${i.id}`, "DELETE").then(() => { qc.invalidateQueries({ queryKey: ["imports"] }); ui.toast("Import deleted"); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
  return (
    <div className="page">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, { label: "Imports" }]} title="Imports" />
      {edit && (
        <label className={`dropzone ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={(e) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files[0]); }}>
          {upload.isPending ? <><Spinner /><b>Importing…</b></> : <><Icon n="upload" size={28} /><b>Drop a .docx here or click to choose</b><span className="muted">Styles, tables, images and numbering are kept. What cannot be edited is preserved and listed as a warning.</span></>}
          <input type="file" accept=".docx" aria-label="DOCX file" onChange={(e) => { send(e.target.files?.[0]); e.target.value = ""; }} />
        </label>)}
      <ErrorBanner e={upload.error ?? list.error} />
      {list.isLoading ? <Skeleton rows={3} h={34} /> : !list.data?.length ? <Empty icon="upload" title="No imports yet">Imported documents appear here so you can edit them, cut snippets out of them or turn them into base documents.</Empty> : (
        <table className="tbl"><thead><tr><th>Title</th><th>File</th><th>Last edited</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>{list.data.map((i) => (
            <tr key={i.id}><td><Link className="titlelink" to={`/imports/${i.id}`}><Icon n="doc" />{i.title}</Link></td><td>{i.filename}</td><td>{ago(i.updated_at)}</td>
              <td className="end">{edit && <button className="btn sm ghost" aria-label={`Delete ${i.title}`} onClick={() => del(i)}><Icon n="trash" />Delete</button>}</td></tr>))}</tbody></table>)}
    </div>
  );
}

export function ImportPage({ me }: { me: Me }) {
  const { id = "" } = useParams(), qc = useQueryClient(), ui = useUI();
  const qkey = useMemo(() => ["import", id], [id]);
  const q = useQuery({ queryKey: qkey, queryFn: () => api<ImportDoc>(`/imports/${id}`) });
  const folders = useFolders(me);
  const [panel, setPanel] = useState<"sel" | "split" | "base" | "vars" | null>(null);
  const [rev, setRev] = useState(0), [sel2, setSel2] = useState<{ picks: Pick[]; error: string }>({ picks: [], error: "" }), edRef = useRef<Editor | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 1]);
  const [folder, setFolder] = useState(""), [title, setTitle] = useState(""), [level, setLevel] = useState(1), [name, setName] = useState("");
  const f = folder || folders.data?.[0]?.id || "";
  const edit = canEdit(me), admin = isAdmin(me.org?.role);
  const preview = useQuery({ queryKey: ["split", id, level], enabled: panel === "split", queryFn: () => api<Section[]>(`/imports/${id}/split?level=${level}`) });
  const done = (t: string) => { ui.toast(t); setPanel(null); qc.invalidateQueries(); };
  const sel = useMutation({ mutationFn: () => api<Created>(`/imports/${id}/selection-snippet`, "POST", { folder_id: f, title, start: range[0], end: range[1] }), onSuccess: (r) => done(`Created snippet draft "${r.title}"${r.warnings.length ? ` (${r.warnings[0]})` : ""}`) });
  const split = useMutation({ mutationFn: () => api<Created[]>(`/imports/${id}/split`, "POST", { folder_id: f, level }), onSuccess: (r) => done(`Created ${r.length} snippet drafts`) });
  // the editor holds the old text after the server replaced values: a new key makes it load the stored content again
  const applyVars = () => api(`/imports/${id}/variables`, "POST", { items: sel2.picks }).then(() => { if (sel2.picks.length) { setRev((r) => r + 1); return qc.invalidateQueries({ queryKey: qkey }); } });
  const base = useMutation({ mutationFn: async () => { if (sel2.picks.length) await applyVars(); return api<{ name: string }>(`/imports/${id}/base-document`, "POST", { name }); }, onSuccess: (r) => done(`Saved as base document "${r.name}"`) });
  const vars = useMutation({ mutationFn: applyVars, onSuccess: () => done(`Replaced ${sel2.picks.length} value${sel2.picks.length === 1 ? "" : "s"} by variables`) });
  const insertSlot = async (named: boolean) => {
    const ed = edRef.current;
    if (!ed) return;
    const slot = named ? (await ui.prompt({ title: "Insert slot", label: "Slot name", hint: "Letters, digits and _; blocks of a document can be placed in it by name.", ok: "Insert" }))?.trim() : "CONTENT";
    if (!slot) return;
    if (!/^[A-Za-z_]\w{0,39}$/.test(slot) || (slot === "CONTENT" && hasContentSlot(ed.state.doc.textContent))) { ui.toast(slot === "CONTENT" ? "This document already has a {{ CONTENT }} paragraph" : "Slot names: letters, digits, _", "err"); return; }
    ed.chain().focus().insertContent({ type: "paragraph", attrs: { slot }, content: [{ type: "text", text: slot === "CONTENT" ? "{{ CONTENT }}" : `{{ SLOT:${slot} }}` }] }).run();
  };
  const insertRef = useRef(insertSlot);
  insertRef.current = insertSlot;
  useEffect(() => { // Ctrl+Alt+C: insert {{ CONTENT }} at the cursor
    const h = (e: KeyboardEvent) => { if (e.ctrlKey && e.altKey && e.code === "KeyC" && edit && edRef.current?.isFocused) { e.preventDefault(); insertRef.current(false); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [edit]);
  const warnings = q.data?.warnings ?? [];
  const folderPick = <Field label="Target folder">{(i) => <select id={i} value={f} onChange={(e) => setFolder(e.target.value)}>{folders.data?.map((x) => <option key={x.id} value={x.id}>{folderLabel(folders.data, x.id)}</option>)}</select>}</Field>;
  const cancel = <button type="button" className="btn" onClick={() => setPanel(null)}>Cancel</button>;
  if (q.error) return <div className="page"><Empty icon="doc" title="Import not found"><Link to="/imports">Back to imports</Link></Empty></div>;
  return (
    <div className="page fill">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, { label: "Imports", to: "/imports" }, { label: q.data?.title ?? "Import" }]} />
      <RichEditor qkey={qkey} me={me} notices={warnings.length ? [{ id: "warn", kind: "warn", text: `${warnings.length} element${warnings.length === 1 ? "" : "s"} not editable`, items: warnings }] : []} noticeBar={`import:${id}`} noticeTestId="import-warnings" keyOf={() => `${id}:${rev}`} lookSrc={{ import: id }}
        load={() => api<ImportDoc>(`/imports/${id}`)} save={(b) => api<ImportDoc>(`/imports/${id}`, "PUT", { title: b.title, content: b.content })}
        actions={({ editor: ed, flush }) => { edRef.current = ed; const slot = hasContentSlot(ed.state.doc.textContent); return <>
          <span data-testid="slot-badge" title={slot ? "The main body is replaced where this paragraph is" : "Without a {{ CONTENT }} paragraph, the body from the first Heading 1 on is replaced"}>{slot ? <Badge kind="green">{"{{ CONTENT }}"} set</Badge> : <Badge kind="amber">no {"{{ CONTENT }}"}</Badge>}</span>
          {edit && <button className="btn" title="Insert a {{ CONTENT }} paragraph at the cursor (Ctrl+Alt+C)" onClick={() => insertSlot(false)}><Icon n="plus" />{"{{ CONTENT }}"}</button>}
          {edit && <button className="btn" onClick={() => insertSlot(true)}><Icon n="plus" />Insert slot…</button>}
          {edit && <button className="btn" onClick={async () => { await flush(); setPanel("vars"); }}><Icon n="layers" />Find variables</button>}
          {edit && <button className="btn" onClick={async () => { await flush(); setRange(blockRange(ed)); setTitle(""); setPanel("sel"); }}><Icon n="file" />Create snippet from selection</button>}
          {edit && <button className="btn" onClick={async () => { await flush(); setPanel("split"); }}><Icon n="layers" />Split by headings</button>}
          {admin && <button className="btn" onClick={async () => { await flush(); setPanel("base"); }}><Icon n="doc" />Use as base document</button>}
          <button className="btn primary" onClick={async () => { await flush(); location.href = `/api/imports/${id}/export`; }}><Icon n="download" />Export DOCX</button>
        </>; }} />
      {panel === "sel" && (
        <Modal title="Create snippet from selection" onClose={() => setPanel(null)} actions={<>{cancel}<button className="btn primary" form="sel-form" disabled={!f || sel.isPending}>Create snippet</button></>}>
          <form id="sel-form" className="stack" onSubmit={(e) => { e.preventDefault(); sel.mutate(); }}>
            <p className="muted">Blocks {range[0] + 1}–{range[1]} of the selection become a snippet draft (styles and images kept).</p>
            {folderPick}<Field label="Snippet title">{(i) => <input id={i} value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus />}</Field><ErrorBanner e={sel.error} />
          </form>
        </Modal>)}
      {panel === "split" && (
        <Modal title="Split by headings" wide onClose={() => setPanel(null)} actions={<>{cancel}<button className="btn primary" disabled={!f || !preview.data?.length || split.isPending} onClick={() => split.mutate()}>Create {preview.data?.length ?? 0} snippets</button></>}>
          <div className="stack">
            <Field label="Split at">{(i) => <select id={i} value={level} onChange={(e) => setLevel(+e.target.value)}><option value={1}>Heading 1</option><option value={2}>Heading 1 and 2</option></select>}</Field>
            {preview.isLoading ? <Skeleton rows={3} /> : <ol data-testid="split-preview" className="splitlist">{preview.data?.map((s) => <li key={s.start}>{s.title} <small>({s.blocks} blocks)</small></li>)}</ol>}
            {preview.data?.length === 0 && <Banner kind="info">Nothing to split at this level.</Banner>}
            {folderPick}<ErrorBanner e={split.error} />
          </div>
        </Modal>)}
      {panel === "vars" && (
        <Modal title="Find variables" wide onClose={() => setPanel(null)} actions={<>{cancel}<button className="btn primary" disabled={!sel2.picks.length || !!sel2.error || vars.isPending} onClick={() => vars.mutate()}>Replace with variables</button></>}>
          <div className="stack"><p className="muted">Values that look like per-document data: cover text, header/footer text and fields, dates, version numbers. Tables of contents/figures, tables and the body are left alone. Chosen values become <code>{"{{ name }}"}</code> (formatting kept); the detected value is the default. Built-in names (<code>doc.title</code>, <code>doc.version</code>, <code>doc.date</code>, <code>author</code>, <code>org.name</code>) are filled by the exporter.</p>
            <Candidates url={`/imports/${id}/variables`} onPicks={setSel2} />{sel2.error && <ErrorBanner e={new Error(sel2.error)} />}<ErrorBanner e={vars.error} /></div>
        </Modal>)}
      {panel === "base" && (
        <Modal title="Use as base document" onClose={() => setPanel(null)} actions={<>{cancel}<button className="btn primary" form="base-form" disabled={base.isPending || !!sel2.error}>Save as base document</button></>}>
          <form id="base-form" className="stack" onSubmit={(e) => { e.preventDefault(); base.mutate(); }}>
            <p className="muted">Keeps this file as it is — cover, table of contents, section breaks, header/footer, styles, numbering and page setup — and replaces the main body (from the first Heading 1, or at the <code>{"{{ CONTENT }}"}</code> paragraph you inserted) by the <code>{"{{ CONTENT }}"}</code> placeholder.</p>
            <h3>Per-document values</h3>
            <Candidates url={`/imports/${id}/variables`} onPicks={setSel2} />{sel2.error && <ErrorBanner e={new Error(sel2.error)} />}
            <Field label="Base document name">{(i) => <input id={i} value={name} onChange={(e) => setName(e.target.value)} required autoFocus />}</Field><ErrorBanner e={base.error} />
          </form>
        </Modal>)}
    </div>
  );
}
