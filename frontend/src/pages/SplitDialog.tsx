import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, Me } from "../api";
import { folderLabel, useFolders } from "../data";
import { dupTitles, groupSections, Heading, PREFACE, upToLevel } from "../splitting";
import { Banner, ErrorBanner, Field, Modal, Skeleton, useUI } from "../ui";

/** Split an import at individually chosen headings (checkboxes are fine in a modal; the editor area has none). */
export function SplitDialog({ me, id, total, onClose }: { me: Me; id: string; total: number; onClose: () => void }) {
  const qc = useQueryClient(), ui = useUI(), folders = useFolders(me);
  const hq = useQuery({ queryKey: ["headings", id], queryFn: () => api<Heading[]>(`/imports/${id}/headings`) });
  const hs = hq.data ?? [];
  const [picked, setPicked] = useState<Set<number> | null>(null), [titles, setTitles] = useState<Record<number, string>>({}), [preface, setPreface] = useState(false), [folder, setFolder] = useState("");
  const sel = picked ?? upToLevel(hs, 1); // default: the top-level headings
  const f = folder || folders.data?.[0]?.id || "";
  const pieces = useMemo(() => groupSections(hs, sel, total, { preface, titles }), [hs, sel, total, preface, titles]);
  const dups = dupTitles(pieces), minLevel = Math.min(...hs.map((h) => h.level), 9), first = [...sel].sort((a, b) => a - b)[0] ?? 0;
  const toggle = (i: number) => setPicked(() => { const n = new Set(sel); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  const run = useMutation({
    mutationFn: () => api<{ id: string }[]>(`/imports/${id}/split`, "POST", { folder_id: f, headings: pieces.filter((p) => p.key !== PREFACE).map((p) => p.key), preface, titles }),
    onSuccess: (r) => { ui.toast(`Created ${r.length} snippet draft${r.length === 1 ? "" : "s"}`); qc.invalidateQueries(); onClose(); },
  });
  const set = (s: Set<number>) => setPicked(s);
  return (
    <Modal title="Split by headings" wide onClose={onClose} actions={<><button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!f || !pieces.length || run.isPending} onClick={() => run.mutate()}>Create {pieces.length} snippet{pieces.length === 1 ? "" : "s"}</button></>}>
      <div className="stack" data-testid="split-dialog">
        {hq.isLoading ? <Skeleton rows={4} /> : !hs.length ? <Banner kind="info">This import has no headings to split at.</Banner> : <>
          <div className="splitbar" role="group" aria-label="Select headings">
            <button type="button" className="btn sm" onClick={() => set(new Set(hs.map((h) => h.index)))}>All</button>
            <button type="button" className="btn sm" onClick={() => set(new Set())}>None</button>
            {[1, 2, 3].map((l) => <button key={l} type="button" className="btn sm" onClick={() => set(upToLevel(hs, l))}>{l === 1 ? "Level 1" : `Level 1–${l}`}</button>)}
            <span className="muted small">{sel.size} of {hs.length} selected</span>
          </div>
          <ul className="outline" data-testid="split-headings" aria-label="Headings">{hs.map((h) => (
            <li key={h.index} style={{ paddingLeft: (h.level - minLevel) * 18 }} className={sel.has(h.index) ? "on" : ""}>
              <label className="chk"><input type="checkbox" checked={sel.has(h.index)} onChange={() => toggle(h.index)} aria-label={`Split at ${h.title}`} /><span className="lv">H{h.level}</span><span className="t">{h.title}</span></label>
              <small className="muted">{h.blocks} block{h.blocks === 1 ? "" : "s"} · {h.chars.toLocaleString()} chars</small>
              {sel.has(h.index) && <input className="ttl" aria-label={`Snippet title for ${h.title}`} placeholder={h.title} value={titles[h.index] ?? ""} onChange={(e) => setTitles({ ...titles, [h.index]: e.target.value })} maxLength={200} />}
            </li>))}</ul>
          {first > 0 && sel.size > 0 && <label className="chk"><input type="checkbox" checked={preface} onChange={(e) => setPreface(e.target.checked)} />Also create a “Preface” snippet from the {first} block{first === 1 ? "" : "s"} before the first selected heading
            {preface && <input className="ttl" aria-label="Preface title" placeholder="Preface" value={titles[PREFACE] ?? ""} onChange={(e) => setTitles({ ...titles, [PREFACE]: e.target.value })} maxLength={200} />}</label>}
          <p className="muted small">Each snippet runs from its heading to the next <b>selected</b> heading; unselected headings stay inside the snippet before them.{first > 0 && !preface && sel.size > 0 ? ` The ${first} block${first === 1 ? "" : "s"} before the first selected heading are left out.` : ""}</p>
          <h3>Result: {pieces.length} snippet{pieces.length === 1 ? "" : "s"}</h3>
          <ol data-testid="split-preview" className="splitlist">{pieces.map((p) => <li key={p.key}>{p.title} <small>({p.blocks} block{p.blocks === 1 ? "" : "s"})</small>{dups.has(p.title.toLowerCase()) && <small className="warn"> same title as another: its slug gets a number</small>}</li>)}</ol>
          {!pieces.length && <Banner kind="info">Select at least one heading.</Banner>}
          <Field label="Target folder">{(i) => <select id={i} value={f} onChange={(e) => setFolder(e.target.value)}>{folders.data?.map((x) => <option key={x.id} value={x.id}>{folderLabel(folders.data, x.id)}</option>)}</select>}</Field>
          <p className="muted small">Existing snippets with the same title are not touched; a clashing slug in the folder gets a -2, -3 … suffix.</p>
        </>}
        <ErrorBanner e={hq.error ?? run.error} />
      </div>
    </Modal>
  );
}
