import type { Block } from "../api";

export function insertAt<T>(a: T[], i: number, x: T): T[] {
  const n = a.slice();
  n.splice(Math.min(Math.max(i, 0), n.length), 0, x);
  return n;
}

/** Move item `from` so it lands before the item currently at index `to` (drop-zone index, 0..length). */
export function moveTo<T>(a: T[], from: number, to: number): T[] {
  if (from < 0 || from >= a.length) return a;
  const n = a.slice(), [x] = n.splice(from, 1);
  n.splice(to > from ? to - 1 : to, 0, x);
  return n;
}

export const newId = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(16).slice(2) + Date.now().toString(16));
export const snippetBlock = (folder_id: string, snippet_id: string, sha: string): Block =>
  ({ id: newId(), slot: "CONTENT", type: "snippet_ref", folder_id, snippet_id, version: { type: "commit", value: sha } });

export type Row = Record<string, string | number | null>;
/** Rows of a list variable after the column set changed: keep known cells, drop removed columns. */
export const reshape = (rows: Row[], cols: string[]): Row[] => rows.map((r) => Object.fromEntries(cols.map((c) => [c, r[c] ?? ""])));
export const parseCols = (s: string): string[] => [...new Set(s.split(",").map((c) => c.trim()).filter((c) => /^[A-Za-z_]\w*$/.test(c)))];
