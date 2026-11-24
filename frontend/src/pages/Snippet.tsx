import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Commit, Draft, Me } from "../api";
import { canEdit, folderPath, short, useFolders } from "../data";
import { SnippetEditor } from "../editor/SnippetEditor";
import { ContextPanel, PageHead, useActiveFolder } from "../Shell";
import { Badge, Empty, fmtDate, Icon, Skeleton, useUI } from "../ui";

type Used = { document_id: string; title: string; folder_name: string; versions: { commit_sha: string; current: boolean; pin: { type: string; value: string | null } }[] }[];

export function useRestore(id: string, done: () => void) {
  const qc = useQueryClient(), ui = useUI();
  return useMutation({
    mutationFn: (v: string) => api(`/snippets/${id}/restore`, "POST", { version: v }),
    onSuccess: async () => { qc.invalidateQueries({ queryKey: ["changes"] }); await qc.refetchQueries({ queryKey: ["draft", id] }); ui.toast("Version restored into your draft"); done(); },
    onError: (e: Error) => ui.toast(e.message, "err"),
  });
}

function SidePanel({ id, me, onRestored }: { id: string; me: Me; onRestored: () => void }) {
  const ui = useUI();
  const [tab, setTab] = useState<"v" | "u">("v");
  const h = useQuery({ queryKey: ["shist", id], queryFn: () => api<Commit[]>(`/snippets/${id}/history`) });
  const used = useQuery({ queryKey: ["usedby", id], queryFn: () => api<Used>(`/snippets/${id}/used-by`) });
  const restore = useRestore(id, onRestored);
  const ask = async (c: Commit) => { if (await ui.confirm({ title: `Restore ${short(c.sha)}?`, ok: "Restore to draft", body: "Your current draft is replaced by this version. Nothing is committed until you commit." })) restore.mutate(c.sha); };
  return (
    <ContextPanel>
      <div className="tabs small" role="tablist">
        <button role="tab" aria-selected={tab === "v"} className={tab === "v" ? "tab on" : "tab"} onClick={() => setTab("v")}>Versions</button>
        <button role="tab" aria-selected={tab === "u"} className={tab === "u" ? "tab on" : "tab"} onClick={() => setTab("u")}>Used by{used.data?.length ? ` (${used.data.length})` : ""}</button>
      </div>
      {tab === "v" && (h.isLoading ? <Skeleton rows={4} /> : !h.data?.length ? <Empty icon="clock" title="No versions yet">Commit this snippet to create the first version.</Empty> : (
        <ul className="vlist" data-testid="version-list">{h.data.map((c, i) => (
          <li key={c.sha}><div><code>{short(c.sha)}</code>{i === 0 && <Badge kind="green">current</Badge>}{c.tags.map((t) => <Badge key={t} kind="violet"><Icon n="tag" size={11} />{t}</Badge>)}</div>
            <p>{c.message}</p><small>{c.author_name} · {fmtDate(c.date)}</small>
            <div className="vact">{canEdit(me) && <button className="btn sm" onClick={() => ask(c)}>Restore to draft</button>}<Link className="btn sm ghost" to={`/history/snippet/${id}?to=${c.sha}`}>Compare</Link></div></li>))}</ul>))}
      {tab === "u" && (used.isLoading ? <Skeleton rows={3} /> : !used.data?.length ? <Empty icon="doc" title="Not used yet">No committed document references this snippet.</Empty> : (
        <ul className="vlist">{used.data.map((d) => <li key={d.document_id}><Link to={`/documents/${d.document_id}`}><Icon n="doc" /> {d.title}</Link><small>{d.folder_name}</small>
          <p>{d.versions.map((v) => <span key={v.commit_sha} className="pinline"><code>{short(v.commit_sha)}</code>{v.current ? " (current)" : ""} pins {v.pin.type === "latest" ? "latest" : short(v.pin.value)}</span>)}</p></li>)}</ul>))}
    </ContextPanel>
  );
}

export function SnippetPage({ me }: { me: Me }) {
  const { id = "" } = useParams(), nav = useNavigate();
  const [epoch, setEpoch] = useState(0); // bumped after restore so the editor reloads the new draft
  const d = useQuery({ queryKey: ["draft", id], queryFn: () => api<Draft & { folder_id: string }>(`/snippets/${id}/draft`), gcTime: 0 });
  const folders = useFolders(me);
  const fid = (d.data as { folder_id?: string } | undefined)?.folder_id ?? null;
  useActiveFolder(fid);
  const path = folderPath(folders.data, fid);
  if (d.error) return <div className="page"><Empty icon="file" title="Snippet not found">It may have been deleted. <Link to="/">Back to the dashboard</Link></Empty></div>;
  return (
    <div className="page fill">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, ...path.map((p) => ({ label: p.name, to: `/folders/${p.id}` })), { label: d.data?.title ?? "Snippet" }]}
        actions={<Link className="btn ghost sm" to={`/history/snippet/${id}`}><Icon n="clock" />History</Link>} />
      <SnippetEditor id={id} me={me} epoch={epoch} onCommit={() => nav(`/commit/${fid ?? ""}?select=${id}`)} />
      <SidePanel id={id} me={me} onRestored={() => setEpoch((e) => e + 1)} />
    </div>
  );
}
