import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api, BaseDoc, BaseDocVersion, isAdmin, Me } from "../api";
import { Badge, Empty, ErrorBanner, Field, fmtDate, Icon, Modal, Skeleton, useUI } from "../ui";

const ph = (v: BaseDocVersion) => v.placeholders.content ? <Badge kind="green">CONTENT{v.placeholders.slots.length ? ` + ${v.placeholders.slots.length} slot${v.placeholders.slots.length > 1 ? "s" : ""}` : ""}</Badge> : <Badge kind="amber">no {"{{ CONTENT }}"}</Badge>;

function Versions({ b, admin }: { b: BaseDoc; admin: boolean }) {
  const qc = useQueryClient(), ui = useUI();
  const vs = useQuery({ queryKey: ["bversions", b.id], queryFn: () => api<BaseDocVersion[]>(`/base-documents/${b.id}/versions`) });
  const up = useMutation({
    mutationFn: (fd: FormData) => api(`/base-documents/${b.id}/versions`, "POST", fd),
    onSuccess: () => { ui.toast("New version uploaded"); qc.invalidateQueries({ queryKey: ["bversions"] }).then(() => qc.invalidateQueries({ queryKey: ["base-docs"] })); },
  });
  return (
    <div className="subtable">
      {vs.isLoading ? <Skeleton rows={2} /> : (
        <table className="tbl compact"><thead><tr><th>Version</th><th>Commit</th><th>File</th><th>Size</th><th>Placeholders</th><th>Message</th><th>Uploaded</th><th /></tr></thead>
          <tbody>{vs.data?.map((v) => <tr key={v.id}><td>v{v.number}</td><td><code>{v.sha.slice(0, 7)}</code></td><td>{v.filename}</td><td>{(v.size / 1024).toFixed(0)} KB</td><td>{ph(v)}</td><td>{v.message}</td><td className="nowrap">{fmtDate(v.created_at)}</td>
            <td className="end"><a className="btn sm ghost" href={`/api/base-documents/${b.id}/download?version=${v.sha}`}><Icon n="download" />Download</a></td></tr>)}</tbody></table>)}
      {admin && <form className="inline" onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; up.mutate(new FormData(f), { onSuccess: () => f.reset() }); }}>
        <div className="field"><label htmlFor={`nv-${b.id}`}>New version (.docx)</label><input id={`nv-${b.id}`} type="file" name="file" accept=".docx" required /></div>
        <div className="field"><label htmlFor={`nm-${b.id}`}>Message</label><input id={`nm-${b.id}`} name="message" placeholder="What changed" /></div>
        <button className="btn primary" disabled={up.isPending}><Icon n="upload" />Upload new version</button></form>}
      <ErrorBanner e={up.error ?? vs.error} />
    </div>
  );
}

export function BaseDocs({ me }: { me: Me }) {
  const qc = useQueryClient(), ui = useUI();
  const admin = isAdmin(me.org?.role);
  const [open, setOpen] = useState<string | null>(null), [adding, setAdding] = useState(false);
  const list = useQuery({ queryKey: ["base-docs", me.org?.id], queryFn: () => api<BaseDoc[]>("/base-documents") });
  const create = useMutation({
    mutationFn: (fd: FormData) => { if (!(fd.get("file") as File)?.size) fd.delete("file"); return api("/base-documents", "POST", fd); },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["base-docs"] }); setAdding(false); ui.toast("Base document created"); },
  });
  return (
    <section className="card"><div className="cardhead"><h2>Base documents</h2>{admin && <button className="btn primary" onClick={() => setAdding(true)}><Icon n="plus" />New base document</button>}</div>
      <p className="muted">The Word template documents are exported on: styles, numbering, header/footer and the <code>{"{{ CONTENT }}"}</code> placeholder. Every upload is a new version; documents pin one.</p>
      {list.isLoading ? <Skeleton rows={3} h={34} /> : !list.data?.length ? <Empty icon="doc" title="No base documents yet">{admin ? "Documents fall back to the bundled default. Create one from a .docx or with the bundled default as a starting point." : "Documents use the bundled default."}</Empty> : (
        <table className="tbl"><thead><tr><th>Name</th><th>Current version</th><th>Placeholders</th><th /></tr></thead>
          <tbody>{list.data.map((b) => (
            <Fragment key={b.id}>
              <tr><td><b>{b.name}</b></td><td>{b.head ? `v${b.head.number}` : "—"}</td><td>{b.head && ph(b.head)}</td>
                <td className="end"><button className="btn sm" aria-expanded={open === b.id} onClick={() => setOpen(open === b.id ? null : b.id)}><Icon n={open === b.id ? "chevD" : "chevR"} />Versions</button></td></tr>
              {open === b.id && <tr><td colSpan={4}><Versions b={b} admin={admin} /></td></tr>}
            </Fragment>))}</tbody></table>)}
      <ErrorBanner e={list.error} />
      {adding && (
        <Modal title="New base document" onClose={() => setAdding(false)} actions={<><button className="btn" onClick={() => setAdding(false)}>Cancel</button><button className="btn primary" form="bd-form" disabled={create.isPending}>Create</button></>}>
          <form id="bd-form" className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate(new FormData(e.currentTarget)); }}>
            <Field label="Base document name">{(id) => <input id={id} name="name" required autoFocus />}</Field>
            <Field label="Base document file" hint="Optional. Without a file the bundled default template is used.">{(id) => <input id={id} type="file" name="file" accept=".docx" />}</Field>
            <ErrorBanner e={create.error} />
          </form>
        </Modal>)}
    </section>
  );
}
