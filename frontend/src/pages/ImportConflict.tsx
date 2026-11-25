import { Modal, useUI } from "../ui";
import { conflictText, Existing } from "../importSnippets";

type Written = { id: string; folder_id: string; title: string; action: string; warnings?: string[] };

/** Toast after snippets were created/updated from an import; updated drafts get links to the draft and the Commit screen. */
export function useResultToast() {
  const ui = useUI();
  return (r: Written[]) => {
    const up = r.filter((x) => x.action === "updated"), n = r.length - up.length;
    const msg = r.length === 1 && !up.length ? `Created snippet draft "${r[0].title}"` : [n && `Created ${n} snippet draft${n === 1 ? "" : "s"}`, up.length && `Updated ${up.length} draft${up.length === 1 ? "" : "s"} (not committed)`].filter(Boolean).join(", ") || "Nothing written";
    const one = r[0];
    ui.toast(`${msg}${one && r.length === 1 && one.warnings?.length ? ` (${one.warnings[0]})` : ""}`, "ok",
      up.length ? [...(up.length === 1 ? [{ label: "Open draft", to: `/snippets/${up[0].id}` }] : []), { label: "Go to Commit", to: `/commit/${up[0].folder_id}?select=${up.map((x) => x.id).join(",")}` }] : undefined);
  };
}

/** Same title in the folder: update it (new draft version), make a separate one, pick another title, or skip. */
export function ConflictModal({ c, busy, onPick, onRename, onClose }: { c: Existing; busy: boolean; onPick: (a: "update_existing" | "create_new_with_suffix" | "skip") => void; onRename: () => void; onClose: () => void }) {
  return (
    <Modal title="Snippet already exists" onClose={onClose} actions={<>
      <button className="btn" disabled={busy} onClick={() => onPick("skip")}>Skip</button>
      <button className="btn" disabled={busy} onClick={onRename}>Choose another title</button>
      <button className="btn" disabled={busy} onClick={() => onPick("create_new_with_suffix")}>Create a separate snippet</button>
      <button className="btn primary" disabled={busy} onClick={() => onPick("update_existing")}>Update it (new draft version)</button></>}>
      <p data-testid="snippet-conflict">{conflictText(c)} Update it with the imported blocks as a new draft version, or create a separate snippet?</p>
      {c.has_draft && <p className="muted small">You have an unsaved draft on it: updating asks before replacing it.</p>}
    </Modal>
  );
}
