import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, BaseDoc, BaseDocVersion, isAdmin, Me, VarDecl } from "../api";
import { useImports } from "../data";
import { PageHead } from "../Shell";
import { Candidates } from "../builder/FindVariables";
import { Pick } from "../builder/picks";
import { VariablesForm } from "../builder/Variables";
import { formatDate } from "../dates";
import { Badge, Banner, Empty, ErrorBanner, Field, fmtDate, Icon, Modal, Skeleton, useUI } from "../ui";

const day = formatDate;
export const ph = (v: BaseDocVersion) => v.placeholders.content ? <Badge kind="green">CONTENT{v.placeholders.slots.length ? ` + ${v.placeholders.slots.length} slot${v.placeholders.slots.length > 1 ? "s" : ""}` : ""}</Badge> : <Badge kind="amber">no {"{{ CONTENT }}"}</Badge>;

/** Variables of the current version: dynamic tables (default rows) and text variables (defaults), editable; "Find variables" turns detected values into new ones. */
function BaseVars({ b, v, admin }: { b: BaseDoc; v: BaseDocVersion; admin: boolean }) {
  const qc = useQueryClient(), ui = useUI();
  const [decls, setDecls] = useState<VarDecl[]>(v.variables), [find, setFind] = useState(false), [sel, setSel] = useState<{ picks: Pick[]; error: string }>({ picks: [], error: "" });
  const done = () => qc.invalidateQueries({ queryKey: ["bversions"] }).then(() => qc.invalidateQueries({ queryKey: ["base-docs"] }));
  const save = useMutation({ mutationFn: () => api(`/base-documents/${b.id}/variables`, "PUT", { declarations: decls, values: {} }), onSuccess: () => { ui.toast("Variables saved"); done(); } });
  const apply = useMutation({ mutationFn: () => api(`/base-documents/${b.id}/find-variables`, "POST", { items: sel.picks }), onSuccess: () => { setFind(false); ui.toast("New version with variables"); done(); } });
  return (
    <details open className="acc" data-testid="base-doc-vars"><summary>Variables of v{v.number} <span className="cnt">{decls.length}</span></summary>
      <p className="muted small">Found in the file as <code>{"{{ name }}"}</code> text and <code>{"{%tr for x in list %}"}</code> table rows. The defaults apply to every document built on this version; a document can override them.</p>
      <VariablesForm fixed="defaults" ro={!admin} vars={{ declarations: decls, values: Object.fromEntries(decls.map((d) => [d.name, d.default])) }}
        onChange={(n) => setDecls(n.declarations.map((d) => ({ ...d, default: n.values[d.name] ?? null })))} />
      {admin && <div className="vact"><button className="btn sm primary" disabled={save.isPending} onClick={() => save.mutate()}>Save defaults</button>
        <button className="btn sm" onClick={() => setFind(true)}>Find variables</button></div>}
      <ErrorBanner e={save.error} />
      {find && <Modal title="Find variables" wide onClose={() => setFind(false)} actions={<><button className="btn" onClick={() => setFind(false)}>Cancel</button><button className="btn primary" disabled={!sel.picks.length || !!sel.error || apply.isPending} onClick={() => apply.mutate()}>Replace with variables (new version)</button></>}>
        <div className="stack"><p className="muted">Values that look like per-document data (cover text, header/footer fields, dates, version numbers). Chosen ones become <code>{"{{ name }}"}</code> in a new version; the detected value is the default.</p>
          <Candidates url={`/base-documents/${b.id}/find-variables`} onPicks={setSel} />{sel.error && <ErrorBanner e={new Error(sel.error)} />}<ErrorBanner e={apply.error} /></div></Modal>}
    </details>
  );
}

type Usage = { documents: { document_id: string; folder_id: string; title: string; state: "committed" | "draft" }[]; exports: number };
export type Conflict = { base_document_id: string; name: string; current_version: number | null };
export const conflictOf = (e: unknown): Conflict | null => { const d = e instanceof ApiError && e.status === 409 ? (e.detail as { error?: string } & Conflict) : null; return d?.error === "name_exists" ? d : null; };

/** Shown when a name is taken: offer a new version of the existing base document instead. `onVersion` is absent when there is no file to version with (default template). */
export function NameConflictModal({ c, onVersion, busy, error, onRename, onClose }: { c: Conflict; onVersion?: () => void; busy: boolean; error: unknown; onRename: () => void; onClose: () => void }) {
  return (
    <Modal title="Name already in use" onClose={onClose} actions={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn" onClick={onRename}>Choose another name</button>
      {onVersion && <button className="btn primary" disabled={busy} onClick={onVersion}>Create new version</button>}</>}>
      <p data-testid="name-conflict">A base document named <b>{c.name}</b> already exists{c.current_version ? ` (current version v${c.current_version})` : ""}. Create a new version of it instead?</p>
      {!onVersion && <p className="muted">The bundled default template cannot be added as a version: choose another name.</p>}
      <ErrorBanner e={error} />
    </Modal>
  );
}

function NewVersion({ b, onClose }: { b: BaseDoc; onClose: () => void }) {
  const qc = useQueryClient(), ui = useUI();
  const up = useMutation({ mutationFn: (fd: FormData) => api(`/base-documents/${b.id}/versions`, "POST", fd),
    onSuccess: () => { ui.toast(`New version of ${b.name} uploaded`); qc.invalidateQueries({ queryKey: ["bversions"] }); qc.invalidateQueries({ queryKey: ["base-docs"] }); onClose(); } });
  return (
    <Modal title={`New version of “${b.name}”`} onClose={onClose} actions={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" form="nv-form" disabled={up.isPending}><Icon n="upload" />Upload new version</button></>}>
      <form id="nv-form" className="stack" onSubmit={(e) => { e.preventDefault(); up.mutate(new FormData(e.currentTarget)); }}>
        <Field label="File (.docx)">{(i) => <input id={i} type="file" name="file" accept=".docx" required autoFocus />}</Field>
        <Field label="Message">{(i) => <input id={i} name="message" placeholder="What changed" maxLength={500} />}</Field>
        <ErrorBanner e={up.error} />
      </form>
    </Modal>
  );
}

function DeleteDialog({ b, onClose }: { b: BaseDoc; onClose: () => void }) {
  const qc = useQueryClient(), ui = useUI();
  const use = useQuery({ queryKey: ["base-usage", b.id], queryFn: () => api<Usage>(`/base-documents/${b.id}/usage`) });
  const del = useMutation({ mutationFn: () => api(`/base-documents/${b.id}`, "DELETE"), onSuccess: () => { ui.toast(`Base document “${b.name}” deleted`); qc.invalidateQueries({ queryKey: ["base-docs"] }); onClose(); },
    onError: () => use.refetch() });
  const docs = use.data?.documents ?? [];
  return (
    <Modal title={`Delete “${b.name}”?`} onClose={onClose} actions={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn danger" disabled={use.isLoading || docs.length > 0 || del.isPending} onClick={() => del.mutate()}><Icon n="trash" />Delete base document</button></>}>
      {use.isLoading ? <Skeleton rows={2} /> : docs.length ? (
        <div data-testid="in-use"><Banner kind="warn">Still used by {docs.length} document{docs.length === 1 ? "" : "s"} (committed version or draft). Switch these to another base document, then delete.</Banner>
          <ul className="vlist">{docs.map((d, i) => <li key={i}><Link to={`/documents/${d.document_id}`} onClick={onClose}>{d.title}</Link> <Badge kind={d.state === "draft" ? "amber" : "gray"}>{d.state}</Badge></li>)}</ul></div>
      ) : <p>All versions of this template leave the library. The file stays in the organization's version history, so exports already made from it keep working. This cannot be undone from the app.{use.data?.exports ? ` ${use.data.exports} existing export${use.data.exports === 1 ? "" : "s"} used it.` : ""}</p>}
      <ErrorBanner e={del.error ?? use.error} />
    </Modal>
  );
}

export function EditBaseButton({ id }: { id: string }) {
  const nav = useNavigate(), ui = useUI();
  const open = useMutation({ mutationFn: () => api<{ import_id: string }>(`/base-documents/${id}/edit`, "POST"), onSuccess: (r) => nav(`/imports/${r.import_id}`), onError: (e: Error) => ui.toast(e.message, "err") });
  return <button className="btn" data-testid="edit-base" disabled={open.isPending} title="Open the current version in the editor; Commit… saves a new version" onClick={() => open.mutate()}><Icon n="edit" />Edit</button>;
}

function Versions({ b, admin }: { b: BaseDoc; admin: boolean }) {
  const vs = useQuery({ queryKey: ["bversions", b.id], queryFn: () => api<BaseDocVersion[]>(`/base-documents/${b.id}/versions`) });
  const use = useQuery({ queryKey: ["base-usage", b.id], queryFn: () => api<Usage>(`/base-documents/${b.id}/usage`) });
  return (
    <div className="subtable">
      {vs.isLoading ? <Skeleton rows={2} /> : (
        <table className="tbl compact"><thead><tr><th>Version</th><th>Commit</th><th>File</th><th>Size</th><th>Placeholders</th><th>Message</th><th>Uploaded</th><th /></tr></thead>
          <tbody>{vs.data?.map((v) => <tr key={v.id}><td>v{v.number}</td><td><code>{v.sha.slice(0, 7)}</code></td><td>{v.filename}</td><td>{(v.size / 1024).toFixed(0)} KB</td><td>{ph(v)}</td><td>{v.message}</td><td className="nowrap">{fmtDate(v.created_at)}</td>
            <td className="end nowrap"><a className="btn sm ghost" href={`/api/base-documents/${b.id}/download?version=${v.sha}`}><Icon n="download" />Download</a></td></tr>)}</tbody></table>)}
      {admin && <p><EditBaseButton id={b.id} /></p>}
      {use.data?.documents.length ? <p className="small">Used by: {[...new Map(use.data.documents.map((d) => [d.document_id, d])).values()].map((d, i) => <Fragment key={d.document_id}>{i ? ", " : ""}<Link to={`/documents/${d.document_id}`}>{d.title}</Link></Fragment>)}</p> : <p className="muted small">No document uses this base document.</p>}
      {vs.data?.[0] && <BaseVars key={vs.data[0].sha} b={b} v={vs.data[0]} admin={admin} />}
      <ErrorBanner e={vs.error} />
    </div>
  );
}

type Mode = "upload" | "default" | "import";
function CreateDialog({ me, onClose, onConflict }: { me: Me; onClose: () => void; onConflict: (c: Conflict, name: string, via: { file?: File; importId?: string }) => void }) {
  const qc = useQueryClient(), ui = useUI(), imports = useImports(me);
  const [mode, setMode] = useState<Mode>("upload");
  const create = useMutation({
    mutationFn: async (v: { name: string; file?: File; importId?: string }) => {
      if (v.importId) return api<{ name: string }>(`/imports/${v.importId}/base-document`, "POST", { name: v.name });
      const fd = new FormData(); fd.append("name", v.name); if (v.file) fd.append("file", v.file);
      return api<{ name: string }>("/base-documents", "POST", fd);
    },
    onSuccess: (_r, v) => { qc.invalidateQueries({ queryKey: ["base-docs"] }); ui.toast(`Base document “${v.name}” created`); onClose(); },
    onError: (e, v) => { const c = conflictOf(e); if (c) onConflict(c, v.name, { file: v.file, importId: v.importId }); },
  });
  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget), name = String(d.get("name")).trim();
    create.mutate({ name, file: mode === "upload" ? (d.get("file") as File) : undefined, importId: mode === "import" ? String(d.get("import")) : undefined });
  };
  return (
    <Modal title="New base document" onClose={onClose} actions={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" form="bd-form" disabled={create.isPending}>Create</button></>}>
      <form id="bd-form" className="stack" onSubmit={submit}>
        <Field label="Base document name">{(id) => <input id={id} name="name" required autoFocus maxLength={100} />}</Field>
        <Field label="Source">{(id) => <select id={id} value={mode} onChange={(e) => setMode(e.target.value as Mode)}><option value="upload">Upload a .docx</option><option value="default">Use default template</option><option value="import">From an import</option></select>}</Field>
        {mode === "upload" && <Field label="Base document file (.docx)">{(id) => <input id={id} type="file" name="file" accept=".docx" required />}</Field>}
        {mode === "default" && <p className="muted">Starts from the bundled default template (styles, header/footer fields, <code>{"{{ CONTENT }}"}</code>).</p>}
        {mode === "import" && <Field label="Import" hint="The import keeps its cover, header/footer and styles; its body becomes the {{ CONTENT }} placeholder.">{(id) => <select id={id} name="import" required>{imports.data?.map((i) => <option key={i.id} value={i.id}>{i.title} ({i.filename})</option>)}</select>}</Field>}
        <ErrorBanner e={conflictOf(create.error) ? null : create.error} />
      </form>
    </Modal>
  );
}

export function BaseDocsPage({ me }: { me: Me }) {
  const qc = useQueryClient(), ui = useUI();
  const admin = isAdmin(me.org?.role);
  const [open, setOpen] = useState<string | null>(null), [adding, setAdding] = useState(false), [ver, setVer] = useState<BaseDoc | null>(null), [del, setDel] = useState<BaseDoc | null>(null);
  const [clash, setClash] = useState<{ c: Conflict; name: string; via: { file?: File; importId?: string } } | null>(null);
  const list = useQuery({ queryKey: ["base-docs", me.org?.id], queryFn: () => api<BaseDoc[]>("/base-documents") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["base-docs"] }).then(() => qc.invalidateQueries({ queryKey: ["bversions"] }));
  const asVersion = useMutation({
    mutationFn: () => { const { c, via } = clash!; if (via.importId) return api(`/imports/${via.importId}/base-document`, "POST", { base_document_id: c.base_document_id }); const fd = new FormData(); fd.append("file", via.file!); return api(`/base-documents/${c.base_document_id}/versions`, "POST", fd); },
    onSuccess: () => { ui.toast(`New version of “${clash!.c.name}” created`); setClash(null); setAdding(false); refresh(); },
  });
  const rename = async (b: BaseDoc) => {
    const name = await ui.prompt({ title: "Rename base document", label: "Name", value: b.name, ok: "Rename" });
    if (name && name.trim() !== b.name) api(`/base-documents/${b.id}`, "PATCH", { name }).then(() => { ui.toast("Base document renamed"); refresh(); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
  return (
    <div className="page">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, { label: "Base documents" }]} title="Base documents" actions={admin && <button className="btn primary" onClick={() => setAdding(true)}><Icon n="plus" />New base document</button>} />
      <p className="muted">The Word templates documents are exported on: styles, numbering, header/footer and the <code>{"{{ CONTENT }}"}</code> placeholder. Every upload is a new version; documents pin one. Names are unique.{!admin && " Only admins can change them."}</p>
      {list.isLoading ? <Skeleton rows={3} h={34} /> : !list.data?.length ? <Empty icon="doc" title="No base documents yet">{admin ? "Documents fall back to the bundled default. Create one from a .docx, from an import, or from the default template." : "Documents use the bundled default."}</Empty> : (
        <div className="tblwrap"><table className="tbl" data-testid="base-docs"><thead><tr><th>Name</th><th>Current version</th><th>Placeholders</th><th>Variables</th><th>Used by</th><th>Created</th><th>Updated</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>{list.data.map((b) => (
            <Fragment key={b.id}>
              <tr data-testid="base-row"><td><b>{b.name}</b></td><td>{b.head ? `v${b.head.number}` : "—"}</td><td>{b.head && ph(b.head)}</td><td>{b.head?.variables.length ?? 0}</td>
                <td title="Documents (committed or draft) pinned to this base document">{b.used_by}</td><td className="nowrap" title={fmtDate(b.created_at)}>{day(b.created_at)}</td><td className="nowrap" title={fmtDate(b.updated_at)}>{day(b.updated_at)}</td>
                <td className="end nowrap"><button className="btn sm" aria-expanded={open === b.id} aria-label={`Details of ${b.name}`} onClick={() => setOpen(open === b.id ? null : b.id)}><Icon n={open === b.id ? "chevD" : "chevR"} />Details</button>
                  {admin && <><button className="btn sm ghost" aria-label={`Rename ${b.name}`} title="Rename" onClick={() => rename(b)}><Icon n="edit" /></button>
                    <button className="btn sm ghost" aria-label={`New version of ${b.name}`} title="Upload new version" onClick={() => setVer(b)}><Icon n="upload" /></button>
                    <button className="btn sm ghost" aria-label={`Delete ${b.name}`} title="Delete" onClick={() => setDel(b)}><Icon n="trash" /></button></>}</td></tr>
              {open === b.id && <tr><td colSpan={8}><Versions b={b} admin={admin} /></td></tr>}
            </Fragment>))}</tbody></table></div>)}
      <ErrorBanner e={list.error} />
      {adding && <CreateDialog me={me} onClose={() => setAdding(false)} onConflict={(c, name, via) => setClash({ c, name, via })} />}
      {clash && <NameConflictModal c={clash.c} busy={asVersion.isPending} error={asVersion.error} onVersion={clash.via.file || clash.via.importId ? () => asVersion.mutate() : undefined}
        onRename={() => setClash(null)} onClose={() => { setClash(null); setAdding(false); }} />}
      {ver && <NewVersion b={ver} onClose={() => setVer(null)} />}
      {del && <DeleteDialog b={del} onClose={() => setDel(null)} />}
    </div>
  );
}
