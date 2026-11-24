import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Doc, isAdmin, Me } from "../api";
import { blockRange } from "../editor/blockrange";
import { RichEditor } from "../editor/SnippetEditor";
import { canEdit, folderLabel, ImportRow, useFolders, useImports } from "../data";
import { PageHead } from "../Shell";
import { Banner, Empty, ErrorBanner, Field, Icon, Modal, Skeleton, Spinner, ago, useUI } from "../ui";

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
  const [panel, setPanel] = useState<"sel" | "split" | "base" | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 1]);
  const [folder, setFolder] = useState(""), [title, setTitle] = useState(""), [level, setLevel] = useState(1), [name, setName] = useState("");
  const f = folder || folders.data?.[0]?.id || "";
  const edit = canEdit(me), admin = isAdmin(me.org?.role);
  const preview = useQuery({ queryKey: ["split", id, level], enabled: panel === "split", queryFn: () => api<Section[]>(`/imports/${id}/split?level=${level}`) });
  const done = (t: string) => { ui.toast(t); setPanel(null); qc.invalidateQueries(); };
  const sel = useMutation({ mutationFn: () => api<Created>(`/imports/${id}/selection-snippet`, "POST", { folder_id: f, title, start: range[0], end: range[1] }), onSuccess: (r) => done(`Created snippet draft "${r.title}"${r.warnings.length ? ` (${r.warnings[0]})` : ""}`) });
  const split = useMutation({ mutationFn: () => api<Created[]>(`/imports/${id}/split`, "POST", { folder_id: f, level }), onSuccess: (r) => done(`Created ${r.length} snippet drafts`) });
  const base = useMutation({ mutationFn: () => api<{ name: string }>(`/imports/${id}/base-document`, "POST", { name }), onSuccess: (r) => done(`Saved as base document "${r.name}"`) });
  const warnings = q.data?.warnings ?? [];
  const folderPick = <Field label="Target folder">{(i) => <select id={i} value={f} onChange={(e) => setFolder(e.target.value)}>{folders.data?.map((x) => <option key={x.id} value={x.id}>{folderLabel(folders.data, x.id)}</option>)}</select>}</Field>;
  const cancel = <button type="button" className="btn" onClick={() => setPanel(null)}>Cancel</button>;
  if (q.error) return <div className="page"><Empty icon="doc" title="Import not found"><Link to="/imports">Back to imports</Link></Empty></div>;
  return (
    <div className="page fill">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, { label: "Imports", to: "/imports" }, { label: q.data?.title ?? "Import" }]} />
      <RichEditor qkey={qkey} me={me} notices={warnings.length ? [{ id: "warn", kind: "warn", text: `${warnings.length} element${warnings.length === 1 ? "" : "s"} not editable`, items: warnings }] : []} noticeBar={`import:${id}`} noticeTestId="import-warnings" keyOf={() => id} lookSrc={{ import: id }}
        load={() => api<ImportDoc>(`/imports/${id}`)} save={(b) => api<ImportDoc>(`/imports/${id}`, "PUT", { title: b.title, content: b.content })}
        actions={({ editor: ed, flush }) => <>
          {edit && <button className="btn" onClick={async () => { await flush(); setRange(blockRange(ed)); setTitle(""); setPanel("sel"); }}><Icon n="file" />Create snippet from selection</button>}
          {edit && <button className="btn" onClick={async () => { await flush(); setPanel("split"); }}><Icon n="layers" />Split by headings</button>}
          {admin && <button className="btn" onClick={async () => { await flush(); setPanel("base"); }}><Icon n="doc" />Use as base document</button>}
          <button className="btn primary" onClick={async () => { await flush(); location.href = `/api/imports/${id}/export`; }}><Icon n="download" />Export DOCX</button>
        </>} />
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
      {panel === "base" && (
        <Modal title="Use as base document" onClose={() => setPanel(null)} actions={<>{cancel}<button className="btn primary" form="base-form" disabled={base.isPending}>Save as base document</button></>}>
          <form id="base-form" className="stack" onSubmit={(e) => { e.preventDefault(); base.mutate(); }}>
            <p className="muted">Keeps this file's styles, numbering, header/footer and page setup; the body becomes a <code>{"{{ CONTENT }}"}</code> placeholder.</p>
            <Field label="Base document name">{(i) => <input id={i} value={name} onChange={(e) => setName(e.target.value)} required autoFocus />}</Field><ErrorBanner e={base.error} />
          </form>
        </Modal>)}
    </div>
  );
}
