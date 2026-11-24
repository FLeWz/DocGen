import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, DocRow, Me, SnippetRow } from "../api";
import { folderLabel, useFolders } from "../data";
import { Empty, ErrorBanner, Field, Modal } from "../ui";

/** "New snippet / document" dialog: title + target folder. */
export function NewItem({ me, kind, folder, onClose }: { me: Me; kind: "snippet" | "document"; folder?: string; onClose: () => void }) {
  const nav = useNavigate(), qc = useQueryClient();
  const folders = useFolders(me).data ?? [];
  const [title, setTitle] = useState(""), [fid, setFid] = useState(folder ?? "");
  const f = fid || folders[0]?.id || "";
  const m = useMutation({
    mutationFn: () => kind === "snippet" ? api<SnippetRow>(`/folders/${f}/snippets`, "POST", { title }) : api<DocRow>("/documents", "POST", { folder_id: f, title }),
    onSuccess: (r) => { qc.invalidateQueries(); onClose(); nav(`/${kind}s/${r.id}`); },
  });
  const go = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <Modal title={`New ${kind}`} onClose={onClose} actions={<><button className="btn" type="button" onClick={onClose}>Cancel</button><button className="btn primary" form="new-item" disabled={!title.trim() || !f || m.isPending}>Create {kind}</button></>}>
      {folders.length === 0 ? <Empty icon="folder" title="Create a folder first">Snippets and documents live in folders. Use “+” above the folder tree.</Empty> : (
        <form id="new-item" className="stack" onSubmit={go}>
          <Field label="Title">{(id) => <input id={id} value={title} onChange={(e) => setTitle(e.target.value)} autoFocus required />}</Field>
          <Field label="Folder">{(id) => <select id={id} value={f} onChange={(e) => setFid(e.target.value)}>{folders.map((x) => <option key={x.id} value={x.id}>{folderLabel(folders, x.id)}</option>)}</select>}</Field>
          <ErrorBanner e={m.error} />
        </form>)}
    </Modal>
  );
}
