import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, Change, Commit, DocRow, Folder, Me, pretty, SnippetRow, Style, ThreeWay } from "./api";
import { DocBuilder } from "./builder/DocBuilder";
import { PMNode } from "./editor/doc";
import { Preview, SnippetEditor, useStyles } from "./editor/SnippetEditor";

const Err = ({ e }: { e: unknown }) => (e ? <p role="alert" style={{ color: "crimson" }}>{(e as Error).message}</p> : null);
const short = (s: string | null) => (s ? s.slice(0, 7) : "—");
const canEdit = (me: Me) => me.org?.role !== "viewer";

function Tree({ folders, parent, sel, onSel }: { folders: Folder[]; parent: string | null; sel: string | null; onSel: (id: string) => void }) {
  const kids = folders.filter((f) => f.parent_id === parent);
  return (
    <ul style={{ listStyle: "none", paddingLeft: parent ? 14 : 0, margin: 0 }}>
      {kids.map((f) => (
        <li key={f.id}>
          <a href="#" onClick={(e) => { e.preventDefault(); onSel(f.id); }} style={{ fontWeight: f.id === sel ? "bold" : "normal" }}>📁 {f.name}</a>
          <Tree folders={folders} parent={f.id} sel={sel} onSel={onSel} />
        </li>
      ))}
    </ul>
  );
}

function Editor({ id, folder, me, onClose, onCommit }: { id: string; folder: string; me: Me; onClose: () => void; onCommit: () => void }) {
  const [epoch, setEpoch] = useState(0); // bumped after restore so the editor reloads the new draft
  return (
    <section>
      <h3>Edit snippet <button onClick={onClose}>Close</button></h3>
      <SnippetEditor id={id} me={me} epoch={epoch} onCommit={onCommit} />
      <SnippetHistory id={id} folder={folder} me={me} onRestored={() => setEpoch((e) => e + 1)} />
      <UsedBy id={id} />
    </section>
  );
}

function SnippetHistory({ id, folder, me, onRestored }: { id: string; folder: string; me: Me; onRestored: () => void }) {
  const qc = useQueryClient();
  const h = useQuery({ queryKey: ["shist", id], queryFn: () => api<Commit[]>(`/snippets/${id}/history`) });
  const [from, setFrom] = useState(""), [to, setTo] = useState("");
  const diff = useQuery({
    queryKey: ["diff", id, from, to], enabled: !!from && !!to,
    queryFn: () => api<{ text: string; summary: Record<string, unknown> }>(`/snippets/${id}/diff?from=${from}&to=${to}`),
  });
  const restore = useMutation({
    mutationFn: (v: string) => api(`/snippets/${id}/restore`, "POST", { version: v }),
    onSuccess: async () => { qc.invalidateQueries({ queryKey: ["changes", folder] }); await qc.refetchQueries({ queryKey: ["draft", id] }); onRestored(); },
  });
  return (
    <div>
      <h4>History</h4>
      <table><tbody>
        {h.data?.map((c) => (
          <tr key={c.sha}>
            <td><code>{short(c.sha)}</code> {c.tags.map((t) => <b key={t}>[{t}] </b>)}</td>
            <td>{c.message}</td><td>{c.author_name}</td>
            <td><label>from <input type="radio" name="from" onChange={() => setFrom(c.sha)} /></label>{" "}
              <label>to <input type="radio" name="to" onChange={() => setTo(c.sha)} /></label></td>
            <td>{canEdit(me) && <button onClick={() => restore.mutate(c.sha)}>Restore to draft</button>}</td>
          </tr>
        ))}
      </tbody></table>
      <Err e={restore.error ?? diff.error} />
      {diff.data && <><p>{JSON.stringify(diff.data.summary, (k, v) => (k === "blocks" ? undefined : v))}</p><pre>{diff.data.text || "(no differences)"}</pre></>}
    </div>
  );
}

function UsedBy({ id }: { id: string }) {
  const u = useQuery({ queryKey: ["usedby", id], queryFn: () => api<{ document_id: string; title: string; folder_name: string; versions: { commit_sha: string; current: boolean; pin: { type: string; value: string | null } }[] }[]>(`/snippets/${id}/used-by`) });
  return (
    <div>
      <h4>Used by</h4>
      {u.data?.length === 0 && <p><small>No committed document references this snippet.</small></p>}
      <ul>{u.data?.map((d) => <li key={d.document_id}>{d.folder_name} / <b>{d.title}</b>:{" "}
        {d.versions.map((v) => <span key={v.commit_sha}><code>{short(v.commit_sha)}</code>{v.current ? " (current)" : ""} pins {v.pin.type === "latest" ? "latest" : short(v.pin.value)}; </span>)}</li>)}</ul>
    </div>
  );
}

function Conflict({ c, onDone, styles }: { c: ThreeWay; onDone: () => void; styles: Style[] }) {
  const [text, setText] = useState(pretty(c.mine.content));
  const res = useMutation({
    mutationFn: () => api(`/snippets/${c.snippet_id}/resolve`, "POST", { content: JSON.parse(text) }),
    onSuccess: onDone,
  });
  return (
    <div style={{ border: "1px solid darkorange", padding: 8 }}>
      <b>Conflict: {c.slug}</b>
      <div style={{ display: "flex", gap: 8 }}>
        {([["Base", c.base?.content], ["Theirs (HEAD)", c.theirs?.content]] as const).map(([n, v]) =>
          <div key={n} style={{ flex: 1 }}><i>{n}</i>{v ? <Preview doc={v as PMNode} styles={styles} small /> : <pre>(none)</pre>}</div>)}
        <div><i>Resolved (edit; starts from mine)</i><br /><textarea rows={12} cols={50} value={text} onChange={(e) => setText(e.target.value)} /></div>
      </div>
      <button onClick={() => res.mutate()}>Use resolved content</button>
      <Err e={res.error} />
    </div>
  );
}

const key = (c: Change) => (c.kind === "document" ? c.document_id! : c.snippet_id!);

function CommitTab({ folder, me }: { folder: string; me: Me }) {
  const qc = useQueryClient();
  const styles = useStyles(me).data ?? [];
  const ch = useQuery({ queryKey: ["changes", folder], queryFn: () => api<Change[]>(`/folders/${folder}/changes`) });
  const [picked, setPicked] = useState<Set<string>>(new Set()), [msg, setMsg] = useState("");
  const refresh = () => qc.invalidateQueries();
  const commit = useMutation({
    mutationFn: () => api(`/folders/${folder}/commit`, "POST", {
      message: msg, snippet_ids: ch.data!.filter((c) => c.kind !== "document" && picked.has(key(c))).map((c) => c.snippet_id),
      document_ids: ch.data!.filter((c) => c.kind === "document" && picked.has(key(c))).map((c) => c.document_id) }),
    onSuccess: () => { setMsg(""); setPicked(new Set()); refresh(); },
  });
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  if (!ch.data) return <p>Loading…</p>;
  return (
    <section>
      <h3>Commit</h3>
      {ch.data.length === 0 && <p>No changes.</p>}
      {ch.data.map((c) => (
        <div key={key(c)}>
          <label><input type="checkbox" checked={picked.has(key(c))} onChange={() => toggle(key(c))} />{" "}
            <b>{c.kind === "document" ? "📄 " : ""}{c.title}</b> ({c.status}{c.conflict ? ", CONFLICT" : ""}) +{c.summary.added} −{c.summary.removed} ~{c.summary.changed} blocks</label>
          {c.three_way ? <Conflict c={c.three_way} onDone={refresh} styles={styles} /> : <pre style={{ maxHeight: 200, overflow: "auto" }}>{c.text}</pre>}
        </div>
      ))}
      {canEdit(me) && <p><input placeholder="Commit message" value={msg} onChange={(e) => setMsg(e.target.value)} size={50} />{" "}
        <button disabled={!msg || !picked.size} onClick={() => commit.mutate()}>Commit {picked.size || ""}</button></p>}
      <Err e={commit.error} />
    </section>
  );
}

function FolderHistory({ folder, me }: { folder: string; me: Me }) {
  const qc = useQueryClient();
  const h = useQuery({ queryKey: ["fhist", folder], queryFn: () => api<Commit[]>(`/folders/${folder}/history`) });
  const [tag, setTag] = useState<Record<string, string>>({});
  const mut = useMutation({
    mutationFn: (v: { sha: string; name: string } | { del: string }) => "del" in v
      ? api(`/folders/${folder}/tags/${v.del}`, "DELETE") : api(`/folders/${folder}/tags`, "POST", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["fhist"] }),
  });
  return (
    <section>
      <h3>Folder history</h3>
      <table><tbody>
        {h.data?.map((c) => (
          <tr key={c.sha}>
            <td><code>{short(c.sha)}</code></td><td>{c.message}</td><td>{c.author_name}</td><td>{new Date(c.date).toLocaleString()}</td>
            <td>{c.tags.map((t) => <span key={t}><b>[{t}]</b>{canEdit(me) && <button onClick={() => mut.mutate({ del: t })}>×</button>} </span>)}
              {canEdit(me) && <><input size={8} placeholder="v1.0" value={tag[c.sha] ?? ""} onChange={(e) => setTag({ ...tag, [c.sha]: e.target.value })} />
                <button onClick={() => tag[c.sha] && mut.mutate({ sha: c.sha, name: tag[c.sha] })}>Tag</button></>}</td>
          </tr>
        ))}
      </tbody></table>
      <Err e={mut.error} />
    </section>
  );
}

function FolderView({ folder, me }: { folder: Folder; me: Me }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"snippets" | "documents" | "commit" | "history">("snippets");
  const [open, setOpen] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const docs = useQuery({ queryKey: ["documents", folder.id], queryFn: () => api<DocRow[]>(`/documents?folder_id=${folder.id}`) });
  const createDoc = useMutation({
    mutationFn: (title: string) => api<DocRow>("/documents", "POST", { folder_id: folder.id, title }),
    onSuccess: (d) => { qc.invalidateQueries({ queryKey: ["documents"] }); setOpenDoc(d.id); },
  });
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["snippets", folder.id, q], queryFn: () => api<SnippetRow[]>(`/folders/${folder.id}/snippets?q=${encodeURIComponent(q)}`) });
  const create = useMutation({
    mutationFn: (title: string) => api<SnippetRow>(`/folders/${folder.id}/snippets`, "POST", { title }),
    onSuccess: (s) => { qc.invalidateQueries(); setOpen(s.id); },
  });
  return (
    <div>
      <h2>{folder.name}</h2>
      <nav>{(["snippets", "documents", "commit", "history"] as const).map((t) =>
        <button key={t} disabled={tab === t} onClick={() => setTab(t)}>{t}</button>)}</nav>
      {tab === "snippets" && (open ? <Editor id={open} folder={folder.id} me={me} onClose={() => setOpen(null)} onCommit={() => setTab("commit")} /> : (
        <section>
          <p><input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} /></p>
          <ul>{list.data?.map((s) => (
            <li key={s.id}><a href="#" onClick={(e) => { e.preventDefault(); setOpen(s.id); }}>{s.title}</a>{" "}
              <small>{s.slug} · {short(s.head_sha)}{s.has_draft ? " · draft" : ""}</small></li>))}</ul>
          {canEdit(me) && <form onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; create.mutate(new FormData(f).get("title") as string); f.reset(); }}>
            <input name="title" placeholder="New snippet title" required /> <button>Add snippet</button></form>}
          <Err e={create.error ?? list.error} />
        </section>))}
      {tab === "documents" && (openDoc ? <DocBuilder id={openDoc} me={me} onClose={() => { setOpenDoc(null); qc.invalidateQueries({ queryKey: ["documents"] }); }} onCommit={() => { setOpenDoc(null); setTab("commit"); }} /> : (
        <section>
          <ul>{docs.data?.map((d) => (
            <li key={d.id}><a href="#" onClick={(e) => { e.preventDefault(); setOpenDoc(d.id); }}>{d.title}</a>{" "}
              <small>{d.slug} · {short(d.head_sha)}{d.has_draft ? " · draft" : ""}</small></li>))}</ul>
          {canEdit(me) && <form onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; createDoc.mutate(new FormData(f).get("title") as string); f.reset(); }}>
            <input name="title" placeholder="New document title" required /> <button>Add document</button></form>}
          <Err e={createDoc.error ?? docs.error} />
        </section>))}
      {tab === "commit" && <CommitTab folder={folder.id} me={me} />}
      {tab === "history" && <FolderHistory folder={folder.id} me={me} />}
    </div>
  );
}

export function Workspace({ me }: { me: Me }) {
  const qc = useQueryClient();
  const folders = useQuery({ queryKey: ["folders", me.org?.id], queryFn: () => api<Folder[]>("/folders") });
  const [sel, setSel] = useState<string | null>(null);
  const cur = folders.data?.find((f) => f.id === sel);
  const mut = useMutation({
    mutationFn: (v: () => Promise<unknown>) => v(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["folders"] }),
  });
  const admin = me.org?.role === "admin" || me.org?.role === "owner";
  const add = () => {
    const name = prompt("Folder name" + (cur ? ` (inside ${cur.name})` : ""));
    if (name) mut.mutate(() => api("/folders", "POST", { name, parent_id: cur?.id ?? null }));
  };
  const rename = () => {
    const name = cur && prompt("Rename", cur.name);
    if (cur && name) mut.mutate(() => api(`/folders/${cur.id}`, "PATCH", { name }));
  };
  const move = () => {
    const to = prompt("Move under folder (name; empty = root)");
    const p = folders.data?.find((f) => f.name === to);
    if (cur && to !== null && (!to || p)) mut.mutate(() => api(`/folders/${cur.id}`, "PATCH", { parent_id: p?.id ?? null }));
  };
  const del = () => {
    if (cur && confirm(`Delete "${cur.name}", its subfolders and ALL history?`))
      mut.mutate(async () => { await api(`/folders/${cur.id}`, "DELETE"); setSel(null); });
  };
  return (
    <div style={{ display: "flex", gap: 24 }}>
      <aside style={{ minWidth: 200 }}>
        <h3>Folders</h3>
        <Tree folders={folders.data ?? []} parent={null} sel={sel} onSel={setSel} />
        {canEdit(me) && <>
          <p><button onClick={add}>+ Folder</button></p>
          {cur && <p><button onClick={rename}>Rename</button> <button onClick={move}>Move</button> {admin && <button onClick={del}>Delete</button>}</p>}
        </>}
        <Err e={mut.error} />
      </aside>
      <div style={{ flex: 1 }}>{cur ? <FolderView key={cur.id} folder={cur} me={me} /> : <p>Select or create a folder.</p>}</div>
    </div>
  );
}
