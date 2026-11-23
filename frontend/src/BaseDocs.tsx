import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, BaseDoc, BaseDocVersion, isAdmin, Me } from "./api";

const Err = ({ e }: { e: unknown }) => (e ? <p role="alert" style={{ color: "crimson" }}>{(e as Error).message}</p> : null);
const ph = (v: BaseDocVersion) => v.placeholders.content ? `CONTENT${v.placeholders.slots.length ? ", slots: " + v.placeholders.slots.join(", ") : ""}` : "⚠ no {{ CONTENT }} placeholder";

function Versions({ b, admin }: { b: BaseDoc; admin: boolean }) {
  const qc = useQueryClient();
  const vs = useQuery({ queryKey: ["bversions", b.id], queryFn: () => api<BaseDocVersion[]>(`/base-documents/${b.id}/versions`) });
  const up = useMutation({
    mutationFn: (fd: FormData) => api(`/base-documents/${b.id}/versions`, "POST", fd),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bversions"] }).then(() => qc.invalidateQueries({ queryKey: ["base-docs"] })),
  });
  return (
    <div style={{ marginLeft: 16 }}>
      <table><tbody>{vs.data?.map((v) => (
        <tr key={v.id}><td>v{v.number}</td><td><code>{v.sha.slice(0, 7)}</code></td><td>{v.filename}</td><td>{(v.size / 1024).toFixed(0)} KB</td>
          <td>{ph(v)}</td><td>{v.message}</td><td><a href={`/api/base-documents/${b.id}/download?version=${v.sha}`}>download</a></td></tr>))}</tbody></table>
      {admin && <form onSubmit={(e) => { e.preventDefault(); up.mutate(new FormData(e.currentTarget)); e.currentTarget.reset(); }}>
        <input type="file" name="file" accept=".docx" required aria-label="New version file" /> <input name="message" placeholder="Message" />{" "}
        <button>Upload new version</button></form>}
      <Err e={up.error ?? vs.error} />
    </div>
  );
}

export function BaseDocs({ me }: { me: Me }) {
  const qc = useQueryClient();
  const admin = isAdmin(me.org?.role);
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["base-docs", me.org?.id], queryFn: () => api<BaseDoc[]>("/base-documents") });
  const create = useMutation({
    mutationFn: (fd: FormData) => { if (!(fd.get("file") as File)?.size) fd.delete("file"); return api("/base-documents", "POST", fd); },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["base-docs"] }),
  });
  return (
    <section>
      <h2>Base documents</h2>
      <ul>{list.data?.map((b) => (
        <li key={b.id}><b>{b.name}</b> – v{b.head?.number} {b.head && <small>({ph(b.head)})</small>}{" "}
          <button onClick={() => setOpen(open === b.id ? null : b.id)}>{open === b.id ? "Hide versions" : "Versions"}</button>
          {open === b.id && <Versions b={b} admin={admin} />}</li>))}</ul>
      {list.data?.length === 0 && <p>No base documents yet{admin ? "; documents fall back to the bundled default." : "."}</p>}
      {admin && <form onSubmit={(e) => { e.preventDefault(); create.mutate(new FormData(e.currentTarget)); e.currentTarget.reset(); }}>
        <input name="name" placeholder="Base document name" required /> <input type="file" name="file" accept=".docx" aria-label="Base document file" />{" "}
        <button>Upload (no file = bundled default)</button></form>}
      <Err e={create.error ?? list.error} />
    </section>
  );
}
