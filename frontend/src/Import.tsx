import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/core";
import { FormEvent, useMemo, useState } from "react";
import { api, Doc, Folder, isAdmin, Me } from "./api";
import { RichEditor } from "./editor/SnippetEditor";

type ImportRow = { id: string; title: string; filename: string; created_at: string };
type ImportDoc = ImportRow & { content: Doc; warnings: string[] };
type Section = { title: string; start: number; end: number; blocks: number; level: number | null };
type Created = { id: string; title: string; slug: string; warnings: string[] };
const Err = ({ e }: { e: unknown }) => (e ? <p role="alert" style={{ color: "crimson" }}>{(e as Error).message}</p> : null);

/** Top-level block range [start, end) covered by the editor selection (a collapsed cursor = its block). */
export function blockRange(editor: Editor): [number, number] {
  const { from, to } = editor.state.selection, d = editor.state.doc;
  const $f = d.resolve(from), $t = d.resolve(to);
  const start = Math.min($f.index(0), d.childCount - 1);
  const end = $t.depth === 0 ? $t.index(0) : $t.index(0) + 1;
  return [start, Math.max(end, start + 1)];
}

export function Imports({ me }: { me: Me }) {
  const qc = useQueryClient();
  const canEdit = me.org?.role !== "viewer";
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["imports", me.org?.id], queryFn: () => api<ImportRow[]>("/imports") });
  const upload = useMutation({
    mutationFn: (fd: FormData) => api<ImportRow>("/import/docx", "POST", fd),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["imports"] }); setOpen(r.id); },
  });
  const del = useMutation({
    mutationFn: (id: string) => api(`/imports/${id}`, "DELETE"),
    onSuccess: (_, id) => { qc.invalidateQueries({ queryKey: ["imports"] }); if (open === id) setOpen(null); },
  });
  const submit = (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); upload.mutate(new FormData(e.currentTarget)); e.currentTarget.reset(); };
  return (
    <section>
      <h2>Import DOCX</h2>
      {canEdit && <form onSubmit={submit}><input type="file" name="file" accept=".docx" aria-label="DOCX file" required />{" "}<button>Import</button></form>}
      <Err e={upload.error ?? list.error ?? del.error} />
      <ul>{list.data?.map((i) => (
        <li key={i.id}><b>{i.title}</b> <small>({i.filename})</small>{" "}
          <button onClick={() => setOpen(open === i.id ? null : i.id)}>{open === i.id ? "Close" : "Open"}</button>{" "}
          {canEdit && <button onClick={() => confirm(`Delete import "${i.title}"?`) && del.mutate(i.id)}>Delete</button>}</li>))}</ul>
      {open && <ImportEditor key={open} id={open} me={me} />}
    </section>
  );
}

function ImportEditor({ id, me }: { id: string; me: Me }) {
  const qc = useQueryClient();
  const qkey = useMemo(() => ["import", id], [id]);
  const q = useQuery({ queryKey: qkey, queryFn: () => api<ImportDoc>(`/imports/${id}`) });
  const folders = useQuery({ queryKey: ["folders", me.org?.id], queryFn: () => api<Folder[]>("/folders") });
  const [panel, setPanel] = useState<"sel" | "split" | "base" | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 1]);
  const [folder, setFolder] = useState(""), [title, setTitle] = useState(""), [level, setLevel] = useState(1), [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  const f = folder || folders.data?.[0]?.id || "";
  const editor = canEditOf(me), admin = isAdmin(me.org?.role);
  const preview = useQuery({ queryKey: ["split", id, level], enabled: panel === "split", queryFn: () => api<Section[]>(`/imports/${id}/split?level=${level}`) });
  const done = (t: string) => { setMsg(t); setPanel(null); qc.invalidateQueries(); };
  const sel = useMutation({ mutationFn: () => api<Created>(`/imports/${id}/selection-snippet`, "POST", { folder_id: f, title, start: range[0], end: range[1] }),
    onSuccess: (r) => done(`Created snippet draft "${r.title}"${r.warnings.length ? ` (${r.warnings[0]})` : ""}`) });
  const split = useMutation({ mutationFn: () => api<Created[]>(`/imports/${id}/split`, "POST", { folder_id: f, level }),
    onSuccess: (r) => done(`Created ${r.length} snippet drafts`) });
  const base = useMutation({ mutationFn: () => api<{ name: string }>(`/imports/${id}/base-document`, "POST", { name }),
    onSuccess: (r) => done(`Saved as base document "${r.name}"`) });
  const FolderPick = <select aria-label="Target folder" value={f} onChange={(e) => setFolder(e.target.value)}>
    {folders.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>;
  const warnings = q.data?.warnings ?? [];

  return (
    <div>
      <h3>Imported document</h3>
      {warnings.length > 0 && <div role="note" data-testid="import-warnings" style={{ background: "#fff6e0", padding: "4px 12px" }}>
        <b>Not fully editable ({warnings.length}):</b><ul>{warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>}
      <RichEditor qkey={qkey} me={me} keyOf={() => id} lookSrc={{ import: id }}
        load={() => api<ImportDoc>(`/imports/${id}`)}
        save={(b) => api<ImportDoc>(`/imports/${id}`, "PUT", { title: b.title, content: b.content })}
        actions={({ editor: ed, flush }) => <>
          {editor && <button onClick={async () => { await flush(); setRange(blockRange(ed)); setTitle(""); setMsg(""); setPanel("sel"); }}>Create snippet from selection</button>}{" "}
          {editor && <button onClick={async () => { await flush(); setMsg(""); setPanel("split"); }}>Split by headings</button>}{" "}
          {admin && <button onClick={async () => { await flush(); setMsg(""); setPanel("base"); }}>Use as base document</button>}{" "}
          <button onClick={async () => { await flush(); location.href = `/api/imports/${id}/export`; }}>Export DOCX</button>
        </>} />
      {panel === "sel" && <form onSubmit={(e) => { e.preventDefault(); sel.mutate(); }} aria-label="Create snippet">
        <p>Blocks {range[0] + 1}–{range[1]} of the selection become a snippet draft (styles and images kept).</p>
        {FolderPick} <input aria-label="Snippet title" placeholder="Snippet title" value={title} onChange={(e) => setTitle(e.target.value)} required />{" "}
        <button disabled={!f}>Create snippet</button> <button type="button" onClick={() => setPanel(null)}>Cancel</button></form>}
      {panel === "split" && <div aria-label="Split by headings">
        <label>Split at <select aria-label="Heading level" value={level} onChange={(e) => setLevel(+e.target.value)}>
          <option value={1}>Heading 1</option><option value={2}>Heading 1 and 2</option></select></label>
        <ol data-testid="split-preview">{preview.data?.map((s) => <li key={s.start}>{s.title} <small>({s.blocks} blocks)</small></li>)}</ol>
        {preview.data?.length === 0 && <p>Nothing to split.</p>}
        {FolderPick} <button disabled={!f || !preview.data?.length} onClick={() => split.mutate()}>Create {preview.data?.length ?? 0} snippets</button>{" "}
        <button onClick={() => setPanel(null)}>Cancel</button></div>}
      {panel === "base" && <form onSubmit={(e) => { e.preventDefault(); base.mutate(); }} aria-label="Use as base document">
        <p>Keeps this file's styles, numbering, header/footer and page setup; the body becomes a {"{{ CONTENT }}"} placeholder.</p>
        <input aria-label="Base document name" placeholder="Base document name" value={name} onChange={(e) => setName(e.target.value)} required />{" "}
        <button>Save as base document</button> <button type="button" onClick={() => setPanel(null)}>Cancel</button></form>}
      {msg && <p role="status" data-testid="import-msg">{msg}</p>}
      <Err e={sel.error ?? split.error ?? base.error} />
    </div>
  );
}
const canEditOf = (me: Me) => me.org?.role !== "viewer";
