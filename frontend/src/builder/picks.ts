import { NAME_RE } from "../editor/doc";

export type Pick = { value: string; name: string };
const BUILTIN = new Set(["doc", "org", "author", "revision_history", "linked_documents"]);

/** Picks of the included rows; `error` names the first problem (bad name, same name for different values). */
export function validatePicks(rows: { value: string; name: string; include: boolean }[]): { picks: Pick[]; error: string } {
  const picks = rows.filter((r) => r.include).map((r) => ({ value: r.value, name: r.name.trim() }));
  const bad = picks.find((p) => !NAME_RE.test(p.name));
  if (bad) return { picks, error: `“${bad.name}” is not a valid variable name (letters, digits, _; optionally group.field)` };
  const seen = new Map<string, string>();
  for (const p of picks) {
    if (!BUILTIN.has(p.name.split(".")[0]) && seen.has(p.name) && seen.get(p.name) !== p.value) return { picks, error: `The name “${p.name}” is used for two different values` };
    seen.set(p.name, p.value);
  }
  return { picks, error: "" };
}

/** Does the text hold a whole-paragraph `{{ CONTENT }}` slot (what the exporter and the base-document parser look for)? */
export const hasContentSlot = (text: string) => /\{\{\s*CONTENT\s*\}\}/.test(text);
