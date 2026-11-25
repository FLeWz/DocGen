// Rename a commit = a display-name override (git is never rewritten). Enter saves, Esc cancels, 'Reset to original' removes it.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { renamedTip, short } from "./data";
import { Badge, Icon, useUI } from "./ui";

type C = { sha: string; message: string; name?: string | null; original_message?: string; renamed?: boolean; renamed_by?: string | null; renamed_at?: string | null };

/** `url` = PATCH endpoint prefix: /folders/{id}/commits or /base-documents/{id}/versions. */
export function RenameButton({ url, c }: { url: string; c: C }) {
  const ui = useUI(), qc = useQueryClient();
  const m = useMutation({
    mutationFn: (name: string) => api(`${url}/${c.sha}`, "PATCH", { name: name || null }),
    onSuccess: () => qc.invalidateQueries(), onError: (e: Error) => ui.toast(e.message, "err"),
  });
  const go = async () => {
    const n = await ui.prompt({ title: `Rename ${short(c.sha)}`, label: "Name", value: c.message.split("\n")[0], ok: "Rename", reset: c.renamed ? "Reset to original" : undefined,
      hint: "Display name only: the git message stays unchanged and versions keep their sha." });
    if (n !== null) m.mutate(n.slice(0, 200));
  };
  return <button className="btn sm ghost" data-testid="rename-commit" aria-label={`Rename ${short(c.sha)}`} title="Rename (display name only)" disabled={m.isPending} onClick={go}><Icon n="edit" />Rename</button>;
}

export const RenamedBadge = ({ c }: { c: C }) => c.renamed ? <Badge kind="amber"><span data-testid="renamed-badge" title={renamedTip(c)}>renamed</span></Badge> : null;
