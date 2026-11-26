// Pure helpers over TipTap JSON for the structure outline, generated lists (TOC/TOF/TOT), caption numbers and notes.
// Server twin of the numbering/entry logic: backend/app/docx_rich.py (seq_numbers, toc_entries).
import type { PMNode } from "./doc";

export type Entry = { level: number; text: string };
export type OutlineItem =
  | { kind: "heading"; level: number; text: string; index: number }
  | { kind: "section"; sec: "section" | "appendix"; id: string; title: string; from: number; to: number }  // blocks [from, to)
  | { kind: "toc"; list: "toc" | "tof" | "tot"; title: string; index: number; imported: boolean }
  | { kind: "caption"; label: string; text: string; index: number };

const walk = (n: PMNode, fn: (n: PMNode) => void) => { fn(n); n.content?.forEach((c) => walk(c, fn)); };

/** Text of a block as a list entry shows it: text nodes plus the numbers of fields (SEQ/REF). */
export function entryText(n: PMNode): string {
  let s = "";
  walk(n, (x) => { if (x.type === "text") s += x.text ?? ""; else if (x.type === "field") s += String(x.attrs?.text ?? ""); });
  return s;
}

/** "Table \\* ARABIC" (imported SEQ argument) -> "Table". */
export const normLabel = (arg: unknown) => String(arg ?? "").split(/\s*\\/)[0].trim() || "Figure";

/** The label of a caption paragraph (its SEQ field argument), or null. */
export function captionLabel(n: PMNode): string | null {
  let label: string | null = null;
  if (n.type !== "paragraph" && n.type !== "heading") return null;
  walk(n, (x) => { if (label === null && x.type === "field" && x.attrs?.code === "SEQ") label = normLabel(x.attrs?.arg); });
  return label;
}

/** Entries of a toc (headings within `levels`, "1-3") or tof/tot (captions with that label) over the top-level blocks. */
export function listEntries(blocks: PMNode[], a: { kind?: string; levels?: string; label?: string | null }): Entry[] {
  if (a.kind === "tof" || a.kind === "tot") {
    const label = a.label || (a.kind === "tot" ? "Table" : "Figure");
    return blocks.filter((b) => captionLabel(b) === label).map((b) => ({ level: 1, text: entryText(b) }));
  }
  const m = /^(\d)-(\d)$/.exec(a.levels ?? "1-3"), lo = m ? +m[1] : 1, hi = m ? +m[2] : 3;
  return blocks.filter((b) => b.type === "heading" && (b.attrs?.level ?? 1) >= lo && (b.attrs?.level ?? 1) <= hi && entryText(b).trim())
    .map((b) => ({ level: b.attrs?.level ?? 1, text: entryText(b) }));
}

/** Structure outline: headings, marked sections (consecutive blocks sharing a `sec.id`), generated lists and captions. */
export function outline(doc: PMNode | null | undefined): OutlineItem[] {
  const blocks = doc?.content ?? [], out: OutlineItem[] = [];
  let open: Extract<OutlineItem, { kind: "section" }> | null = null;
  blocks.forEach((b, i) => {
    const sec = b.attrs?.sec as { id?: string; kind?: string; title?: string } | null | undefined;
    if (open && (!sec || String(sec.id) !== open.id)) { open.to = i; open = null; }
    if (sec && !open && sec.id) {
      open = { kind: "section", sec: sec.kind === "appendix" ? "appendix" : "section", id: String(sec.id), title: sec.title ?? "", from: i, to: blocks.length };
      out.push(open);
    }
    if (b.type === "heading") out.push({ kind: "heading", level: b.attrs?.level ?? 1, text: entryText(b), index: i });
    else if (b.type === "toc") out.push({ kind: "toc", list: (b.attrs?.kind ?? "toc") as "toc", title: b.attrs?.title ?? "", index: i, imported: !!b.attrs?.xml });
    else { const l = captionLabel(b); if (l) out.push({ kind: "caption", label: l, text: entryText(b), index: i }); }
  });
  return out;
}

/** Next free numeric section id. */
export const nextSecId = (doc: PMNode | null | undefined) =>
  String(Math.max(0, ...(doc?.content ?? []).map((b) => Number(b.attrs?.sec?.id) || 0)) + 1);

/** SEQ numbers per label in document order, keyed by the order of the SEQ fields (used to refresh cached results). */
export function seqNumbers(blocks: PMNode[]): number[] {
  const count: Record<string, number> = {}, out: number[] = [];
  blocks.forEach((b) => walk(b, (x) => {
    if (x.type === "field" && x.attrs?.code === "SEQ") { const l = normLabel(x.attrs.arg); out.push((count[l] = (count[l] ?? 0) + 1)); }
  }));
  return out;
}
