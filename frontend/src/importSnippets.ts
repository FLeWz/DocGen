/** Making snippets from an import: same-title conflicts, per-row actions, and ranking snippets for a heading (round 12). */
import { ApiError } from "./api";
import { formatDate } from "./dates";

export type Existing = { snippet_id: string; title: string; slug: string; head_sha: string | null; folder_id: string; folder_name: string; last_commit_at: string | null; last_commit_author: string | null; has_draft: boolean; key?: number; new_title?: string };
export type Action = "update_existing" | "create_new_with_suffix" | "skip";
export const ACTION_LABEL: Record<Action, string> = { update_existing: "Update existing", create_new_with_suffix: "Create new (suffix)", skip: "Skip" };

const detail = (e: unknown) => (e instanceof ApiError && e.status === 409 ? (e.detail as { error?: string; conflicts?: Existing[] } | undefined) : undefined);
/** The 409 `name_exists` payload of selection-snippet / split, or null. */
export const conflictsOf = (e: unknown): Existing[] | null => { const d = detail(e); return d?.error === "name_exists" ? d.conflicts ?? [] : null; };
/** The 409 `draft_exists` payload: the user has unsaved draft content on the snippet(s) to be updated. */
export const draftHeld = (e: unknown) => detail(e)?.error === "draft_exists";

export const conflictText = (c: Existing) =>
  `A snippet titled “${c.title}” already exists in ${c.folder_name} (${c.last_commit_at ? `last commit ${formatDate(c.last_commit_at)} by ${c.last_commit_author}` : "not committed yet"}).`;

/** Rows of a split: those whose title exists get an action (default: update existing), new ones none. */
export function rowActions(keys: number[], existing: Record<number, Existing | null | undefined>, chosen: Record<number, Action>): Record<number, Action> {
  return Object.fromEntries(keys.filter((k) => existing[k]).map((k) => [k, chosen[k] ?? "update_existing"]));
}

export function summarize(keys: number[], existing: Record<number, Existing | null | undefined>, chosen: Record<number, Action>) {
  const acts = rowActions(keys, existing, chosen), v = Object.values(acts);
  return { created: keys.length - v.length + v.filter((a) => a === "create_new_with_suffix").length, updated: v.filter((a) => a === "update_existing").length, skipped: v.filter((a) => a === "skip").length };
}

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();
const bigrams = (s: string) => { const m = new Map<string, number>(); for (let i = 0; i < s.length - 1; i++) m.set(s.slice(i, i + 2), (m.get(s.slice(i, i + 2)) ?? 0) + 1); return m; };
/** 0..1 (Sørensen–Dice on character pairs, case/space-insensitive): only used to sort the target dropdown; the server does the real matching. */
export function similarity(a: string, b: string): number {
  const x = norm(a), y = norm(b);
  if (x === y) return 1;
  if (x.length < 2 || y.length < 2) return 0;
  const bx = bigrams(x), by = bigrams(y);
  let hit = 0;
  bx.forEach((n, k) => { hit += Math.min(n, by.get(k) ?? 0); });
  return (2 * hit) / (x.length - 1 + y.length - 1);
}

export const rankSnippets = <T extends { title: string }>(title: string, xs: T[]) => xs.map((x) => ({ x, score: similarity(title, x.title) })).sort((a, b) => b.score - a.score || a.x.title.localeCompare(b.x.title));

export type PlanRow = { key: number; title: string; start: number; end: number; blocks: number; level: number | null; match: { snippet_id: string; how: "source" | "title" | "fuzzy"; score: number } | null; target: string; changes: { added: number; removed: number; changed: number; same: boolean } | null; has_draft: boolean };
/** Rows ticked by default: the ones with a target that has changes (a 'no changes' row is skipped), and every row that would create a new snippet. */
export const defaultChecked = (rows: PlanRow[]) => new Set(rows.filter((r) => r.target !== "skip" && !r.changes?.same).map((r) => r.key));
export const changeText = (c: PlanRow["changes"]) => !c ? "new snippet" : c.same ? "no changes" : [c.changed && `${c.changed} changed`, c.added && `${c.added} added`, c.removed && `${c.removed} removed`].filter(Boolean).join(", ");
