// Editing a base document in the import editor (round 9): the banner with the diff against the version the draft started from,
// Commit… (new version, conflict = discard/override), Discard draft, and the version history with "restore into draft".
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { Badge, Banner, ErrorBanner, Field, Icon, Modal, ago, useUI } from "../ui";

export type BaseEditInfo = { base_document: { id: string; name: string }; started_number: number | null; head_number: number; stale: boolean;
  diff: { added: number; removed: number; changed: number; headers_footers: number; blocks: { op: string; index: number; type: string; text: string }[] };
  versions: { number: number; sha: string; message: string; created_at: string; filename: string }[] };
type Committed = { version: number; sha: string; placeholders: { content: boolean; slots: string[] }; variables: { name: string; type: string }[]; warnings: string[] };

export const useBaseEdit = (id: string, on: boolean) => useQuery({ queryKey: ["base-edit", id], enabled: on, refetchInterval: 4000, queryFn: () => api<BaseEditInfo>(`/imports/${id}/base-edit`) });

export const diffText = (d: BaseEditInfo["diff"]) => {
  const n = d.added + d.removed + d.changed;
  return n || d.headers_footers ? `${d.changed} changed, ${d.added} added, ${d.removed} removed block${n === 1 ? "" : "s"}${d.headers_footers ? `, ${d.headers_footers} header/footer part${d.headers_footers === 1 ? "" : "s"} changed` : ""}` : "no changes yet";
};

export function BaseEditBar({ info }: { info: BaseEditInfo }) {
  return (
    <Banner kind={info.stale ? "warn" : "info"} data-testid="base-edit-bar">
      <b>Editing base document “{info.base_document.name}”</b> — draft of version {info.started_number ?? "?"} <span data-testid="base-diff" className="muted">· {diffText(info.diff)}</span>
      {info.stale && <div data-testid="base-stale">Version {info.head_number} was committed since this draft started. Discard the draft to start again from it, or commit anyway to build on your draft (override).</div>}
    </Banner>
  );
}

export function CommitBaseModal({ id, info, onClose, onDone }: { id: string; info: BaseEditInfo; onClose: () => void; onDone: (r: Committed) => void }) {
  const [message, setMessage] = useState(""), [override, setOverride] = useState(false), [res, setRes] = useState<Committed | null>(null);
  const m = useMutation({ mutationFn: () => api<Committed>(`/imports/${id}/base-commit`, "POST", { message, override }), onSuccess: (r) => { setRes(r); onDone(r); } });
  const conflict = m.error instanceof ApiError && m.error.status === 409 ? (m.error.detail as { error?: string }) : null;
  return (
    <Modal title="Commit base document" onClose={onClose} actions={res ? <button className="btn primary" onClick={onClose}>Close</button> : <>
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      {conflict?.error === "base_changed" && <button className="btn danger" onClick={() => { setOverride(true); setTimeout(() => m.mutate()); }} disabled={m.isPending}>Commit anyway (override)</button>}
      <button className="btn primary" form="base-commit" disabled={!message.trim() || m.isPending}>Commit as version {info.head_number + 1}</button></>}>
      {res ? (
        <div className="stack" data-testid="base-committed">
          <Banner kind="ok">Version {res.version} of “{info.base_document.name}” committed (<code>{res.sha.slice(0, 7)}</code>). Documents keep the version they pinned; bump them from the document builder.</Banner>
          <p className="small">Placeholders: {res.placeholders.content ? <code>{"{{ CONTENT }}"}</code> : <Badge kind="amber">no CONTENT slot</Badge>}{res.placeholders.slots.map((s) => <code key={s}> {`{{ SLOT:${s} }}`}</code>)}. Variables: {res.variables.length ? res.variables.map((v) => <code key={v.name}> {v.name}</code>) : "none"}.</p>
          {res.warnings.length > 0 && <ul className="small">{res.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
        </div>
      ) : (
        <form id="base-commit" className="stack" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <p className="muted">{diffText(info.diff)}. The version is committed to the organization's base-document history with you as author.</p>
          {info.diff.blocks.length > 0 && <ul className="small" data-testid="base-diff-blocks">{info.diff.blocks.slice(0, 8).map((b) => <li key={b.index}><Badge kind={b.op === "added" ? "green" : "amber"}>{b.op}</Badge> {b.type}: {b.text || "…"}</li>)}</ul>}
          <Field label="Commit message">{(i) => <input id={i} value={message} onChange={(e) => setMessage(e.target.value)} maxLength={500} required autoFocus placeholder="What changed and why" />}</Field>
          {conflict?.error === "base_changed" ? <Banner data-testid="base-conflict">{m.error!.message}. Close this dialog and discard the draft, or commit anyway.</Banner> : <ErrorBanner e={m.error} />}
        </form>)}
    </Modal>
  );
}

export function BaseHistoryModal({ id, info, onClose, onRestored }: { id: string; info: BaseEditInfo; onClose: () => void; onRestored: () => void }) {
  const ui = useUI();
  const r = useMutation({ mutationFn: (sha: string) => api(`/imports/${id}/base-restore`, "POST", { sha }), onSuccess: () => { ui.toast("Version restored into the draft"); onRestored(); onClose(); }, onError: (e: Error) => ui.toast(e.message, "err") });
  return (
    <Modal title={`Versions of “${info.base_document.name}”`} wide onClose={onClose}>
      <div className="scrollbox"><table className="tbl compact" data-testid="base-versions"><thead><tr><th>Version</th><th>Message</th><th>Date</th><th /></tr></thead>
        <tbody>{info.versions.map((v) => <tr key={v.sha}><td>v{v.number}</td><td>{v.message || v.filename}</td><td className="nowrap">{ago(v.created_at)}</td>
          <td className="end nowrap"><a className="btn sm ghost" href={`/api/base-documents/${info.base_document.id}/download?version=${v.sha}`}><Icon n="download" />Download</a>
            <button className="btn sm" disabled={r.isPending} onClick={() => ui.confirm({ title: `Restore v${v.number} into the draft?`, body: "Your current draft is replaced. Nothing is committed until you commit.", ok: "Restore" }).then((y) => y && r.mutate(v.sha))}>Restore into draft</button></td></tr>)}</tbody></table></div>
    </Modal>
  );
}

export function useDiscard(id: string, name: string) {
  const ui = useUI(), nav = useNavigate(), qc = useQueryClient();
  return async () => {
    if (!(await ui.confirm({ title: "Discard the draft?", danger: true, ok: "Discard draft", body: `Your edits of “${name}” are removed. The committed versions stay.` }))) return;
    await api(`/imports/${id}`, "DELETE").then(() => { qc.invalidateQueries({ queryKey: ["base-docs"] }); ui.toast("Draft discarded"); nav("/base-documents"); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
}
