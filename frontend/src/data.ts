// Shared queries and helpers used by the shell and every page.
import { useQuery } from "@tanstack/react-query";
import { api, Folder, Me } from "./api";
import { formatDateTime } from "./dates";

export type Item = { kind: "snippet" | "document"; id: string; folder_id: string; title: string; slug: string; head_sha: string | null; created_at: string };
export type Overview = { counts: Record<string, { snippets: number; documents: number }>; items: Item[] };
export type ImportRow = { id: string; title: string; filename: string; created_at: string; updated_at: string };

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
export const useFolders = (me: Me) => useQuery({ queryKey: ["folders", me.org?.id], queryFn: () => api<Folder[]>("/folders") });
export const useOverview = (me: Me) => useQuery({ queryKey: ["overview", me.org?.id], queryFn: () => api<Overview>("/overview") });
export const useImports = (me: Me) => useQuery({ queryKey: ["imports", me.org?.id], queryFn: () => api<ImportRow[]>("/imports") });

export const short = (s: string | null) => (s ? s.slice(0, 7) : "—");
export const canEdit = (me: Me) => me.org?.role !== "viewer";
export const LAST_FOLDER = (org?: string) => `docgen.folder.${org}`;

/** Folder path root → leaf, for breadcrumbs. */
export function folderPath(folders: Folder[] | undefined, id?: string | null): Folder[] {
  const out: Folder[] = [];
  for (let f = folders?.find((x) => x.id === id); f; f = folders?.find((x) => x.id === f!.parent_id)) out.unshift(f);
  return out;
}

export const folderLabel = (folders: Folder[] | undefined, id: string) => folderPath(folders, id).map((f) => f.name).join(" / ");

/** Tooltip of the 'renamed' badge: the immutable git message and who/when (DD.MM.YYYY HH:mm). */
export const renamedTip = (c: { original_message?: string; renamed_by?: string | null; renamed_at?: string | null }) =>
  `Originally: ${c.original_message || "(no message)"}${c.renamed_by ? ` — renamed by ${c.renamed_by}` : ""}${c.renamed_at ? ` on ${formatDateTime(c.renamed_at)}` : ""}`;
