import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EditorContent, useEditor } from "@tiptap/react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, Change, Doc, Draft, Me, Style, ThreeWay } from "../api";
import { canEdit, folderPath, useFolders } from "../data";
import { PMNode } from "../editor/doc";
import { extensions } from "../editor/extensions";
import { PageFrame, useLook } from "../editor/look";
import { useStyles } from "../editor/SnippetEditor";
import { PageHead, useActiveFolder } from "../Shell";
import { Badge, Empty, ErrorBanner, Icon, Skeleton, useUI } from "../ui";
import { DiffText, Sides } from "./DiffView";
import { FolderTabs } from "./Folder";

const keyOf = (c: Change) => (c.kind === "document" ? c.document_id! : c.snippet_id!);

/** 3-pane conflict resolver: base | theirs (HEAD) | mine, plus an editable result that starts from any of them. */
function Resolver({ c, styles, onDone }: { c: ThreeWay; styles: Style[]; onDone: () => void }) {
  const look = useLook({}).data;
  const editor = useEditor({ extensions, content: c.mine.content as never });
  const res = useMutation({ mutationFn: () => api(`/snippets/${c.snippet_id}/resolve`, "POST", { content: editor!.getJSON() }), onSuccess: onDone });
  const use = (d?: { content: Doc } | null) => d && editor?.commands.setContent(d.content as never);
  return (
    <div className="resolver" data-testid="resolver">
      <Sides styles={styles} panes={[{ label: "Base (common ancestor)", doc: c.base?.content as PMNode }, { label: "Theirs (committed HEAD)", doc: c.theirs?.content as PMNode }, { label: "Mine (your draft)", doc: c.mine.content as PMNode }]} />
      <section className="result">
        <div className="pane-h">Result <span className="muted small">— edit, or start from another version</span>
          <span className="spacer" /><button className="btn sm" onClick={() => use(c.mine)}>Start from mine</button><button className="btn sm" onClick={() => use(c.theirs)}>Start from theirs</button><button className="btn sm" disabled={!c.base} onClick={() => use(c.base)}>Start from base</button></div>
        <div className="docx-stage"><PageFrame look={look} zoom={0.5}><EditorContent editor={editor} aria-label="Resolved content" /></PageFrame></div>
        <ErrorBanner e={res.error} />
        <div className="rfoot"><button className="btn primary" disabled={!editor || res.isPending} onClick={() => res.mutate()}><Icon n="check" />Use this result</button></div>
      </section>
    </div>
  );
}

function Detail({ c, me, styles, onResolved }: { c: Change; me: Me; styles: Style[]; onResolved: () => void }) {
  const isDoc = c.kind === "document";
  const [mode, setMode] = useState<"side" | "text">(isDoc ? "text" : "side");
  const head = useQuery({ queryKey: ["head", c.snippet_id], enabled: !isDoc && !c.conflict, queryFn: () => api<{ content: Doc; sha: string | null }>(`/snippets/${c.snippet_id}`) });
  const draft = useQuery({ queryKey: ["draft-view", c.snippet_id], enabled: !isDoc && !c.conflict, gcTime: 0, queryFn: () => api<Draft>(`/snippets/${c.snippet_id}/draft`) });
  if (c.three_way && canEdit(me)) return <><Banner2 /><Resolver c={c.three_way} styles={styles} onDone={onResolved} /></>;
  return (
    <div>
      <div className="tabs small" role="tablist">
        {!isDoc && <button role="tab" aria-selected={mode === "side"} className={mode === "side" ? "tab on" : "tab"} onClick={() => setMode("side")}>Side by side</button>}
        <button role="tab" aria-selected={mode === "text"} className={mode === "text" ? "tab on" : "tab"} onClick={() => setMode("text")}>Text diff</button>
      </div>
      {mode === "side" && !isDoc ? (head.isLoading || draft.isLoading ? <Skeleton rows={6} /> :
        <Sides styles={styles} panes={[{ label: c.status === "new" ? "Before (new snippet)" : "Before (committed)", doc: c.status === "new" ? null : (head.data?.content as PMNode) }, { label: "After (your draft)", doc: draft.data?.content as PMNode }]} />)
        : <DiffText text={c.text} />}
    </div>
  );
}
const Banner2 = () => <div className="banner warn" role="note"><Icon n="alert" /><div>Another commit changed this snippet after your draft started. Compare the three versions, edit the result and use it; then the snippet can be committed.</div></div>;

export function CommitPage({ me }: { me: Me }) {
  const { folderId = "" } = useParams(), [sp] = useSearchParams(), qc = useQueryClient(), ui = useUI();
  const folders = useFolders(me), f = folders.data?.find((x) => x.id === folderId);
  useActiveFolder(f ? folderId : null);
  const styles = useStyles(me).data ?? [];
  const ch = useQuery({ queryKey: ["changes", folderId], queryFn: () => api<Change[]>(`/folders/${folderId}/changes`), enabled: !!f, refetchOnWindowFocus: false });
  // ?select=<id> (coming from an editor) is checked from the first paint: an effect would race with early clicks
  const want = sp.get("select");
  const [picked, setPicked] = useState<Set<string>>(() => new Set(want ? want.split(",") : [])), [focus, setFocus] = useState<string | null>(want?.split(",")[0] ?? null), [msg, setMsg] = useState("");
  const refresh = () => qc.invalidateQueries();
  const chosen = (ch.data ?? []).filter((c) => picked.has(keyOf(c)) && !c.conflict);
  const commit = useMutation({
    mutationFn: () => api<{ sha: string }>(`/folders/${folderId}/commit`, "POST", {
      message: msg, snippet_ids: chosen.filter((c) => c.kind !== "document").map((c) => c.snippet_id),
      document_ids: chosen.filter((c) => c.kind === "document").map((c) => c.document_id) }),
    onSuccess: (r) => { ui.toast(`Committed ${chosen.length} item${chosen.length > 1 ? "s" : ""} (${r.sha?.slice(0, 7) ?? ""})`); setMsg(""); setPicked(new Set()); refresh(); },
  });
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const path = folderPath(folders.data, folderId);
  if (folders.isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (!f) return <div className="page"><Empty icon="folder" title="Folder not found"><Link to="/">Back to the dashboard</Link></Empty></div>;
  const rows = ch.data ?? [], cur = rows.find((c) => keyOf(c) === focus) ?? rows[0], edit = canEdit(me);
  const ok = rows.filter((c) => !c.conflict);
  return (
    <div className="page fill">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, ...path.map((p) => ({ label: p.name, to: `/folders/${p.id}` })), { label: "Changes" }]} title={`Commit changes in ${f.name}`} tabs={FolderTabs(folderId, rows.length)} />
      <ErrorBanner e={ch.error} />
      {ch.isLoading || (ch.isFetching && !rows.length) ? <Skeleton rows={5} h={34} /> : rows.length === 0 ? (  // a stale empty cache must not flash "no changes"
        <div data-testid="no-changes"><Empty icon="check" title="No changes to commit">Everything in this folder is committed. <Link to={`/folders/${folderId}`}>Back to the folder</Link></Empty></div>
      ) : (
        <div className="commitgrid">
          <aside className="files" aria-label="Changed files">
            <div className="files-h">
              {edit && <label className="chk"><input type="checkbox" aria-label="Select all" checked={ok.length > 0 && ok.every((c) => picked.has(keyOf(c)))} onChange={(e) => setPicked(e.target.checked ? new Set(ok.map(keyOf)) : new Set())} /> Select all</label>}
              <span className="muted small">{rows.length} changed</span>
            </div>
            <ul>{rows.map((c) => (
              <li key={keyOf(c)} data-testid="change-row" className={cur && keyOf(cur) === keyOf(c) ? "on" : ""}>
                {edit && <input type="checkbox" aria-label={`Commit ${c.title}`} checked={picked.has(keyOf(c))} disabled={c.conflict} title={c.conflict ? "Resolve the conflict first" : undefined} onChange={() => toggle(keyOf(c))} />}
                <button className="fbtn" onClick={() => setFocus(keyOf(c))}>
                  <span className="t"><Icon n={c.kind === "document" ? "doc" : "file"} />{c.title}</span>
                  <span className="b">{c.conflict ? <Badge kind="red">Conflict</Badge> : <Badge kind={c.status === "new" ? "violet" : "amber"}>{c.status === "new" ? "New" : "Modified"}</Badge>}
                    <small className="plus">+{c.summary.added}</small><small className="minus">−{c.summary.removed}</small><small>~{c.summary.changed}</small></span>
                </button>
              </li>))}</ul>
          </aside>
          <section className="detail" aria-label="Diff">{cur && <><h2>{cur.title} <small className="muted">{cur.slug}</small></h2><Detail key={keyOf(cur) + (cur.conflict ? "c" : "")} c={cur} me={me} styles={styles} onResolved={refresh} /></>}</section>
        </div>)}
      {edit && rows.length > 0 && (
        <form className="commitbar" onSubmit={(e) => { e.preventDefault(); commit.mutate(); }}>
          <input aria-label="Commit message" placeholder="Commit message — what changed and why" value={msg} onChange={(e) => setMsg(e.target.value)} />
          <button className="btn primary" disabled={!msg.trim() || !chosen.length || commit.isPending}><Icon n="commit" />Commit {chosen.length || ""}</button>
          {commit.error && <div className="banner err" role="alert"><Icon n="alert" />{(commit.error as Error).message}</div>}
        </form>)}
    </div>
  );
}
