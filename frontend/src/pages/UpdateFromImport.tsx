import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, Me, SnippetRow } from "../api";
import { folderLabel, useFolders, useImports } from "../data";
import { changeText, defaultChecked, draftHeld, PlanRow, rankSnippets, similarity } from "../importSnippets";
import { Heading, upToLevel } from "../splitting";
import { Badge, Banner, ErrorBanner, Field, Modal, Skeleton, useUI } from "../ui";
import { useResultToast } from "./ImportConflict";

/** Refresh existing snippets from an import (the open one, or a newer upload of the same manual): headings are matched to the
 *  folder's snippets (remembered source, same title, fuzzy), rows with changes are applied to the user's DRAFTS (never committed).
 *  With `snippet` the dialog is the snippet page's "Update from import…": exactly one heading goes into that snippet. */
export function UpdateFromImportDialog({ me, importId, snippet, onClose }: { me: Me; importId?: string; snippet?: { id: string; title: string; folder_id: string }; onClose: () => void }) {
  const qc = useQueryClient(), ui = useUI(), folders = useFolders(me), imports = useImports(me), showResult = useResultToast();
  const [imp, setImp] = useState(importId ?? ""), [folder, setFolder] = useState(snippet?.folder_id ?? ""), [level, setLevel] = useState(1);
  const [targets, setTargets] = useState<Record<number, string>>({}), [ticks, setTicks] = useState<Record<number, boolean>>({}), [pick, setPick] = useState<number | null>(null);
  const iid = imp || imports.data?.[0]?.id || "", f = folder || folders.data?.[0]?.id || "";
  const hq = useQuery({ queryKey: ["headings", iid], enabled: !!iid, queryFn: () => api<Heading[]>(`/imports/${iid}/headings`) });
  const hs = hq.data ?? [], cuts = snippet ? hs.map((h) => h.index) : [...upToLevel(hs, level)].sort((a, b) => a - b);
  const best = snippet ? hs.map((h) => ({ h, s: similarity(h.title, snippet.title) })).sort((a, b) => b.s - a.s)[0]?.h.index ?? null : null, one = pick ?? best;
  const eff = snippet ? Object.fromEntries(cuts.map((k) => [k, k === one ? snippet.id : "skip"])) : targets;
  const plan = useQuery({ queryKey: ["update-plan", iid, f, cuts, eff], enabled: !!iid && !!f && cuts.length > 0, placeholderData: (p) => p,
    queryFn: () => api<PlanRow[]>(`/imports/${iid}/update-plan`, "POST", { folder_id: f, headings: cuts, targets: Object.keys(eff).length ? eff : undefined }) });
  const snips = useQuery({ queryKey: ["folder-snippets", f], enabled: !!f, queryFn: () => api<SnippetRow[]>(`/folders/${f}/snippets`) });
  const rows = plan.data ?? [], dflt = defaultChecked(rows);
  const on = (r: PlanRow) => snippet ? r.key === one : ticks[r.key] ?? dflt.has(r.key);
  const chosen = rows.filter(on);
  const apply = useMutation({
    mutationFn: (overwrite: boolean) => api<{ id: string; folder_id: string; title: string; action: string }[]>(`/imports/${iid}/update-apply`, "POST", {
      folder_id: f, overwrite_draft: overwrite,
      items: chosen.map((r) => ({ key: r.key, action: r.target === "skip" ? "skip" : r.target === "new" ? "create" : "update", snippet_id: r.target === "new" || r.target === "skip" ? undefined : r.target, end: snippet ? r.end : undefined })) }),
    onSuccess: (r) => { showResult(r); qc.invalidateQueries(); onClose(); },
    onError: async (e) => { if (draftHeld(e) && await ui.confirm({ title: "Replace your unsaved drafts?", ok: "Overwrite drafts", danger: true, body: "You have unsaved changes in the draft of a snippet that is about to be updated. Overwriting replaces them; cancel keeps them." })) apply.mutate(true); },
  });
  const minLevel = Math.min(...hs.map((h) => h.level), 9), lv = (r: PlanRow) => (r.level ?? minLevel) - minLevel;
  const noChange = rows.filter((r) => r.changes?.same).length;
  return (
    <Modal title={snippet ? `Update “${snippet.title}” from an import` : "Update snippets from an import"} wide onClose={onClose} actions={<><button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!chosen.length || apply.isPending || plan.isFetching} onClick={() => apply.mutate(false)}>Apply to drafts ({chosen.length})</button></>}>
      <div className="stack" data-testid="update-dialog">
        <Field label="Import" hint="The open import, or a newer upload of the same manual.">{(i) => <select id={i} value={iid} onChange={(e) => { setImp(e.target.value); setTargets({}); setTicks({}); setPick(null); }}>{imports.data?.map((x) => <option key={x.id} value={x.id}>{x.title} ({x.filename})</option>)}</select>}</Field>
        {!snippet && <Field label="Snippets in folder">{(i) => <select id={i} value={f} onChange={(e) => { setFolder(e.target.value); setTargets({}); setTicks({}); }}>{folders.data?.map((x) => <option key={x.id} value={x.id}>{folderLabel(folders.data, x.id)}</option>)}</select>}</Field>}
        {!snippet && <div className="splitbar" role="group" aria-label="Headings">{[1, 2, 3].map((l) => <button key={l} type="button" className={l === level ? "btn sm primary" : "btn sm"} onClick={() => setLevel(l)}>{l === 1 ? "Level 1" : `Level 1–${l}`}</button>)}
          <span className="muted small">Each snippet runs from its heading to the next ticked heading.</span></div>}
        {hq.isLoading || (plan.isLoading && cuts.length > 0) ? <Skeleton rows={4} /> : !hs.length ? <Banner kind="info">This import has no headings.</Banner> : <>
          <div className="outline" data-testid="update-rows" role="list" aria-label="Headings and matched snippets">{rows.map((r) => {
            const list = rankSnippets(r.title, snips.data ?? []), m = r.match;
            return (
              <div className="maprow" role="listitem" key={r.key} data-testid="update-row" style={{ paddingLeft: 8 + lv(r) * 14 }}>
                <input type={snippet ? "radio" : "checkbox"} name="heading" checked={on(r)} onChange={(e) => snippet ? setPick(r.key) : setTicks({ ...ticks, [r.key]: e.target.checked })} aria-label={`Apply ${r.title}`} />
                <span className="t" title={r.title}>{r.title} <small className="muted">{r.blocks} block{r.blocks === 1 ? "" : "s"}</small></span>
                {snippet ? <span className="muted small">{snippet.title}</span> : <select aria-label={`Target for ${r.title}`} value={r.target} onChange={(e) => setTargets({ ...targets, [r.key]: e.target.value })}>
                  <option value="new">Create new snippet</option><option value="skip">Skip</option>
                  {list.map(({ x, score }) => <option key={x.id} value={x.id}>{x.title}{m?.snippet_id === x.id ? ` · ${m.how}` : score >= 0.5 ? ` · ${Math.round(score * 100)}%` : ""}</option>)}</select>}
                <span>{r.target === "skip" ? <Badge kind="gray">not applied</Badge> : r.changes ? (r.changes.same ? <Badge kind="gray">no changes</Badge> : <Badge kind="blue">{changeText(r.changes)}</Badge>) : <Badge kind="green">new snippet</Badge>}{r.has_draft && <Badge kind="amber">has draft</Badge>}{!snippet && m && r.target === m.snippet_id && m.how === "fuzzy" && <small className="muted"> {Math.round(m.score * 100)}% similar</small>}</span>
              </div>); })}</div>
          <p className="muted small" data-testid="update-summary">{chosen.length} of {rows.length} selected · {noChange} without changes (not ticked) · drafts are written on top of HEAD; commit them afterwards to create the new versions.</p>
        </>}
        <ErrorBanner e={hq.error ?? plan.error ?? (draftHeld(apply.error) ? null : apply.error)} />
      </div>
    </Modal>
  );
}
