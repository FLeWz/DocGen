import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, Commit, Doc, ExportStatus, Me } from "../api";
import { canEdit, folderPath, short, useFolders } from "../data";
import { RenamedBadge, RenameButton } from "../renameCommit";
import { PMNode } from "../editor/doc";
import { useStyles } from "../editor/SnippetEditor";
import { PageHead, useActiveFolder } from "../Shell";
import { Badge, Banner, Empty, ErrorBanner, fmtDate, Icon, Skeleton, useUI } from "../ui";
import { DiffText, Sides } from "./DiffView";
import { FolderTabs } from "./Folder";
import { useRestore } from "./Snippet";

type Kind = "folder" | "snippet" | "document";
type Diff = { text: string; summary: { added: number; removed: number; changed: number; unchanged: number } };

function Compare({ id, commits, me }: { id: string; commits: Commit[]; me: Me }) {
  const [sp] = useSearchParams(), styles = useStyles(me).data ?? [];
  const to0 = commits.find((c) => c.sha.startsWith(sp.get("to") ?? "~"))?.sha ?? commits[0]?.sha ?? "";
  const [to, setTo] = useState(to0);
  const idx = commits.findIndex((c) => c.sha === to), [from, setFrom] = useState(commits[idx + 1]?.sha ?? "");
  const [mode, setMode] = useState<"side" | "text">("text");
  useEffect(() => { setFrom(commits[commits.findIndex((c) => c.sha === to) + 1]?.sha ?? ""); }, [to]); // eslint-disable-line react-hooks/exhaustive-deps
  const diff = useQuery({ queryKey: ["diff", id, from, to], enabled: !!from && !!to, queryFn: () => api<Diff>(`/snippets/${id}/diff?from=${from}&to=${to}`) });
  const at = (sha: string) => ({ queryKey: ["snippet-at", id, sha], enabled: !!sha && mode === "side", queryFn: () => api<{ content: Doc }>(`/snippets/${id}?version=${sha}`) });
  const a = useQuery(at(from)), b = useQuery(at(to));
  const opt = (c: Commit) => <option key={c.sha} value={c.sha}>{short(c.sha)} · {c.message.slice(0, 50)}{c.renamed ? " ✎" : ""}</option>;
  return (
    <section className="card" aria-label="Compare versions">
      <div className="toolbar"><h2>Compare</h2>
        <label>From <select aria-label="Compare from" value={from} onChange={(e) => setFrom(e.target.value)}><option value="">—</option>{commits.map(opt)}</select></label>
        <label>To <select aria-label="Compare to" value={to} onChange={(e) => setTo(e.target.value)}>{commits.map(opt)}</select></label>
        {diff.data && <span className="badges"><Badge kind="green">+{diff.data.summary.added}</Badge><Badge kind="red">−{diff.data.summary.removed}</Badge><Badge kind="amber">~{diff.data.summary.changed}</Badge><span className="muted small">blocks</span></span>}
        <span className="spacer" />
        <div className="tabs small"><button className={mode === "text" ? "tab on" : "tab"} onClick={() => setMode("text")}>Text diff</button><button className={mode === "side" ? "tab on" : "tab"} onClick={() => setMode("side")}>Rendered</button></div>
      </div>
      <ErrorBanner e={diff.error} />
      {!from ? <p className="muted">Pick an older version to compare with{commits.length < 2 ? " — this snippet has only one version." : "."}</p>
        : mode === "text" ? (diff.data ? <DiffText text={diff.data.text} /> : <Skeleton rows={4} />)
        : <Sides styles={styles} panes={[{ label: `From ${short(from)}`, doc: a.data?.content as PMNode }, { label: `To ${short(to)}`, doc: b.data?.content as PMNode }]} />}
    </section>
  );
}

export function HistoryPage({ me }: { me: Me }) {
  const { kind = "folder", id = "" } = useParams() as { kind: Kind; id: string }, nav = useNavigate(), qc = useQueryClient(), ui = useUI();
  const folders = useFolders(me), edit = canEdit(me);
  const item = useQuery({ queryKey: ["item-meta", kind, id], enabled: kind !== "folder", queryFn: () => api<{ folder_id: string; title: string }>(`/${kind}s/${id}`) });
  const fid = kind === "folder" ? id : item.data?.folder_id ?? null;
  useActiveFolder(fid);
  const h = useQuery({ queryKey: ["history", kind, id], queryFn: () => api<Commit[]>(`/${kind}s/${id}/history`) });
  const tag = useMutation({
    mutationFn: (v: { sha: string; name: string } | { del: string }) => "del" in v ? api(`/folders/${id}/tags/${v.del}`, "DELETE") : api(`/folders/${id}/tags`, "POST", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["history"] }), onError: (e: Error) => ui.toast(e.message, "err"),
  });
  // document versions: export as of a commit through the job flow; `cached` comes back with the start response
  const [dl, setDl] = useState<{ sha: string; fmt: string; msg: string } | null>(null), [cached, setCached] = useState<Record<string, boolean>>({});
  const [dlErr, setDlErr] = useState<{ sha: string; text: string; missing: { name: string }[] } | null>(null);
  const download = async (c: Commit, fmt: "docx" | "pdf") => {
    setDlErr(null); setDl({ sha: c.sha, fmt, msg: "Preparing…" });
    try {
      const { export_id, cached: hit } = await api<{ export_id: string; cached: boolean }>(`/documents/${id}/versions/${c.sha}/export?format=${fmt}`);
      setCached((m) => ({ ...m, [`${c.sha}:${fmt}`]: hit }));
      for (;;) {
        const s = await api<ExportStatus>(`/exports/${export_id}`);
        if (s.status === "error") throw new Error(s.error ?? "export failed");
        if (s.status === "done") { window.location.assign(`/api/exports/${export_id}/download`); break; }
        setDl({ sha: c.sha, fmt, msg: s.status === "running" ? "Rendering…" : "Queued…" });
        await new Promise((r) => setTimeout(r, 500));
      }
    } catch (e) {
      const d = e instanceof ApiError ? (e.detail as { error?: string; variables?: { name: string }[] } | undefined) : undefined;
      setDlErr({ sha: c.sha, text: (e as Error).message, missing: d?.error === "missing_variables" ? d.variables ?? [] : [] });
    }
    setDl(null);
  };
  const restore = useRestore(id, () => nav(`/snippets/${id}`));
  const folderId = fid ?? "";
  const path = folderPath(folders.data, fid), name = kind === "folder" ? path.at(-1)?.name : item.data?.title;
  const addTag = async (c: Commit) => { const n = await ui.prompt({ title: `Tag ${short(c.sha)}`, label: "Tag name", hint: "e.g. v1.0 — documents can pin a tag", ok: "Add tag" }); if (n) tag.mutate({ sha: c.sha, name: n }); };
  const ask = async (c: Commit) => { if (await ui.confirm({ title: `Restore ${short(c.sha)}?`, ok: "Restore to draft", body: "Your current draft of this snippet is replaced by this version. Nothing is committed until you commit." })) restore.mutate(c.sha); };
  return (
    <div className="page">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, ...path.map((p) => ({ label: p.name, to: `/folders/${p.id}` })), ...(kind !== "folder" ? [{ label: name ?? "", to: `/${kind}s/${id}` }] : []), { label: "History" }]}
        title={`History${name ? ` of ${name}` : ""}`} tabs={kind === "folder" ? FolderTabs(id) : undefined}
        actions={kind !== "folder" && <Link className="btn" to={`/${kind}s/${id}`}><Icon n="edit" />Open {kind}</Link>} />
      <ErrorBanner e={h.error} />
      {h.isLoading ? <Skeleton rows={5} h={34} /> : !h.data?.length ? <Empty icon="clock" title="No commits yet">Commit changes to start a history.</Empty> : <>
        {kind === "snippet" && <Compare key={h.data[0].sha} id={id} commits={h.data} me={me} />}
        {dlErr && <Banner kind="err" data-testid="dl-error"><b>{dlErr.text}</b> ({short(dlErr.sha)})
          {dlErr.missing.length > 0 && <ul>{dlErr.missing.map((m) => <li key={m.name}>{m.name}</li>)}</ul>}</Banner>}
        <table className="tbl" data-testid="history">
          <thead><tr><th>Version</th><th>Message</th><th>Author</th><th>Date</th><th>Tags</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>{h.data.map((c, i) => (
            <tr key={c.sha} data-testid="commit-row">
              <td><code>{short(c.sha)}</code>{i === 0 && <Badge kind="green">latest</Badge>}</td><td>{c.message} <RenamedBadge c={c} /></td><td>{c.author_name}</td><td className="nowrap">{fmtDate(c.date)}</td>
              <td className="badges">{c.tags.map((t) => <Badge key={t} kind="violet"><Icon n="tag" size={11} />{t}{kind === "folder" && edit && <button className="x" aria-label={`Remove tag ${t}`} onClick={() => tag.mutate({ del: t })}><Icon n="x" size={11} /></button>}</Badge>)}</td>
              <td className="end">{edit && <RenameButton url={`/folders/${folderId}/commits`} c={c} />}{kind === "folder" && edit && <button className="btn sm" onClick={() => addTag(c)}><Icon n="tag" />Add tag</button>}
                {kind === "document" && (["docx", "pdf"] as const).map((f) => <span key={f} className="nowrap">
                  <button className="btn sm" data-testid={`dl-${f}`} disabled={!!dl} aria-label={`Download ${f.toUpperCase()} of ${short(c.sha)}`}
                    title="The document as committed here, with its own pins, variables and revision history. A 'latest' pin follows its snippet's newest commit at download time. Renders are cached until a tag changes the revision table."
                    onClick={() => download(c, f)}><Icon n="download" />{dl?.sha === c.sha && dl.fmt === f ? dl.msg : f.toUpperCase()}</button>
                  {cached[`${c.sha}:${f}`] && <Badge kind="green">cached</Badge>}</span>)}
                {kind === "snippet" && edit && <button className="btn sm" data-testid="restore" aria-label={`Restore ${short(c.sha)} to draft`} onClick={() => ask(c)}>Restore to draft</button>}</td>
            </tr>))}</tbody>
        </table></>}
    </div>
  );
}
