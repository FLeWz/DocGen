import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ReactNode, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Change, DocRow, Me, RefStatus, SnippetRow } from "../api";
import { canEdit, folderPath, short, useFolders, useOverview } from "../data";
import { PageHead, useActiveFolder } from "../Shell";
import { Badge, Empty, ErrorBanner, Icon, MenuButton, Skeleton, useUI } from "../ui";
import { LockBadge, useLocks } from "../locks";
import { NewItem } from "./NewItem";

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

export const FolderTabs = (id: string, changes?: number) => [
  { to: `/folders/${id}`, label: "Contents", end: true }, { to: `/commit/${id}`, label: changes ? `Changes (${changes})` : "Changes" }, { to: `/history/folder/${id}`, label: "History" }];

export function FolderPage({ me }: { me: Me }) {
  const { id = "" } = useParams(), nav = useNavigate(), qc = useQueryClient(), ui = useUI();
  const folders = useFolders(me), ov = useOverview(me);
  const f = folders.data?.find((x) => x.id === id), path = folderPath(folders.data, id);
  useActiveFolder(f ? id : null);
  const [q, setQ] = useState(""), [type, setType] = useState<"all" | "snippet" | "document">("all"), [mk, setMk] = useState<"snippet" | "document" | null>(null);
  const edit = canEdit(me), lk = useLocks(me), lockOf = (kind: string, id: string) => lk.data?.find((l) => l.entity_type === kind && l.entity_id === id);
  const sn = useQuery({ queryKey: ["snippets", id], queryFn: () => api<SnippetRow[]>(`/folders/${id}/snippets`), enabled: !!f });
  const dc = useQuery({ queryKey: ["documents", id], queryFn: () => api<DocRow[]>(`/documents?folder_id=${id}`), enabled: !!f });
  const ch = useQuery({ queryKey: ["changes", id], queryFn: () => api<Change[]>(`/folders/${id}/changes`), enabled: !!f });
  const upd = useQueries({ queries: (dc.data ?? []).filter((d) => d.head_sha).map((d) => ({ queryKey: ["updates", d.id], queryFn: () => api<RefStatus[]>(`/documents/${d.id}/updates`) })) });
  const updates = new Set((dc.data ?? []).filter((d) => d.head_sha).filter((_, i) => upd[i]?.data?.some((s) => s.update_available)).map((d) => d.id));
  const changed = useMemo(() => new Map((ch.data ?? []).map((c) => [(c.kind === "document" ? c.document_id : c.snippet_id)!, c])), [ch.data]);
  const del = async (kind: "snippet" | "document", x: { id: string; title: string }) => {
    if (!(await ui.confirm({ title: `Delete ${kind} “${x.title}”?`, danger: true, ok: "Delete", body: kind === "snippet" ? "The snippet disappears from the folder. Its git history stays." : "The document is removed from the folder." }))) return;
    api(`/${kind}s/${x.id}`, "DELETE").then(() => { qc.invalidateQueries(); ui.toast(`${kind[0].toUpperCase()}${kind.slice(1)} deleted`); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
  if (folders.isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (!f) return <div className="page"><Empty icon="folder" title="Folder not found">It may have been deleted or moved to another organization. <Link to="/">Back to the dashboard</Link></Empty></div>;
  const needle = q.trim().toLowerCase(), hit = (t: string, s: string) => !needle || (t + " " + s).toLowerCase().includes(needle);
  const rows = [
    ...(type !== "document" ? (sn.data ?? []).filter((s) => hit(s.title, s.slug)).map((s) => ({ kind: "snippet" as const, ...s })) : []),
    ...(type !== "snippet" ? (dc.data ?? []).filter((d) => hit(d.title, d.slug)).map((d) => ({ kind: "document" as const, ...d })) : []),
  ];
  const subs = (folders.data ?? []).filter((x) => x.parent_id === id);
  const status = (r: (typeof rows)[0]): ReactNode => {
    const c = changed.get(r.id);
    return <>
      {c?.conflict ? <Badge kind="red">Conflict</Badge> : c ? <Badge kind="amber">{r.head_sha ? "Draft · modified" : "Draft · new"}</Badge> : r.head_sha ? <Badge kind="green">Committed</Badge> : <Badge>Empty</Badge>}
      <LockBadge l={lockOf(r.kind, r.id)} />
      {updates.has(r.id) && <Badge kind="blue" data-testid="badge-update-available">Update available</Badge>}
    </>;
  };
  return (
    <div className="page">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, ...path.map((p, i) => ({ label: p.name, to: i < path.length - 1 ? `/folders/${p.id}` : undefined }))]} title={f.name} tabs={FolderTabs(id, ch.data?.length)}
        actions={edit && <><button className="btn primary" onClick={() => setMk("snippet")}><Icon n="plus" />New snippet</button><button className="btn" onClick={() => setMk("document")}><Icon n="doc" />New document</button></>} />
      <div className="toolbar">
        <div className="inputicon"><Icon n="search" /><input aria-label="Filter this folder" placeholder="Filter by title…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <select aria-label="Show" value={type} onChange={(e) => setType(e.target.value as typeof type)}><option value="all">Snippets and documents</option><option value="snippet">Snippets only</option><option value="document">Documents only</option></select>
        <span className="muted small">{plural(ov.data?.counts[id]?.snippets ?? 0, "snippet")} · {plural(ov.data?.counts[id]?.documents ?? 0, "document")}</span>
      </div>
      {subs.length > 0 && <div className="chips" aria-label="Subfolders">{subs.map((s) => <Link key={s.id} className="chip" to={`/folders/${s.id}`}><Icon n="folder" />{s.name}</Link>)}</div>}
      <ErrorBanner e={sn.error ?? dc.error} />
      {sn.isLoading || dc.isLoading ? <Skeleton rows={5} h={34} /> : rows.length === 0 ? (
        <Empty icon="file" title={needle ? "No matches" : "This folder is empty"}>{needle ? "Try another search." : edit ? "Create a snippet for reusable content or a document that assembles snippets." : "Nothing has been added yet."}</Empty>
      ) : (
        <table className="tbl" data-testid="folder-items">
          <thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Version</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.kind + r.id} data-testid="item-row">
              <td><Link to={`/${r.kind}s/${r.id}`} className="titlelink"><Icon n={r.kind === "snippet" ? "file" : "doc"} />{r.title}</Link><small className="slug">{r.slug}</small></td>
              <td>{r.kind === "snippet" ? "Snippet" : "Document"}</td><td className="badges">{status(r)}</td><td><code>{short(r.head_sha)}</code></td>
              <td className="end"><MenuButton label={`Actions for ${r.title}`} className="btn ghost icon" align="right" items={[
                { label: "Open", icon: "edit", onClick: () => nav(`/${r.kind}s/${r.id}`) }, { label: "History", icon: "clock", onClick: () => nav(`/history/${r.kind}/${r.id}`) },
                ...(edit ? (["sep", { label: "Delete…", icon: "trash", danger: true, onClick: () => del(r.kind, r) }] as const) : [])]}><Icon n="more" /></MenuButton></td>
            </tr>))}</tbody>
        </table>)}
      {mk && <NewItem me={me} kind={mk} folder={id} onClose={() => setMk(null)} />}
    </div>
  );
}
