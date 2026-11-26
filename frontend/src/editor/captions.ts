// Captions: one place for the Caption paragraph (label + SEQ field + ": text"), its placement and the remembered choices.
// Numbers: the SEQ field caches its result; the Derived plugin (extensions.ts) renumbers every SEQ per label in document order after each edit.
import type { Editor } from "@tiptap/core";
import { normLabel } from "./outline";

export type CapKind = "table" | "figure";
export type CapOpts = { on: boolean; pos: "above" | "below"; label: string; text: string };
const KEY = "docgen.caption.";
const DEFAULT_POS: Record<CapKind, CapOpts["pos"]> = { table: "above", figure: "below" };  // Word's convention
export const defaultLabel = (k: CapKind) => (k === "table" ? "Table" : "Figure");

/** Remembered choice (add on/off, position per kind); default: on, tables above, figures below. */
export function loadCap(kind: CapKind, s: Pick<Storage, "getItem"> = localStorage): CapOpts {
  const pos = s.getItem(`${KEY}pos.${kind}`);
  return { on: s.getItem(`${KEY}add`) !== "0", pos: pos === "above" || pos === "below" ? pos : DEFAULT_POS[kind], label: defaultLabel(kind), text: "" };
}
export function saveCap(kind: CapKind, o: CapOpts, s: Pick<Storage, "setItem"> = localStorage) {
  s.setItem(`${KEY}add`, o.on ? "1" : "0"); s.setItem(`${KEY}pos.${kind}`, o.pos);
}

/** SEQ number a new caption with `label` gets when inserted at `pos` (existing captions of that label before it, plus one). */
export function nextSeq(editor: Editor, label: string, pos: number): number {
  let n = 0;
  editor.state.doc.descendants((node, p) => { if (p < pos && node.type.name === "field" && node.attrs.code === "SEQ" && normLabel(node.attrs.arg) === label) n++; });
  return n + 1;
}

/** The Caption paragraph; above = keep it on the page of what follows (keepNext). Label is plain text, the number a real SEQ field. */
export function captionJSON(label: string, text: string, n: number, above: boolean) {
  const l = label.trim() || "Figure";
  return { type: "paragraph", attrs: { styleId: "Caption", ...(above ? { pfmt: { keepNext: true } } : {}) },
    content: [{ type: "text", text: `${l} ` }, { type: "field", attrs: { code: "SEQ", arg: l, text: String(n) } }, ...(text.trim() ? [{ type: "text", text: `: ${text.trim()}` }] : [])] };
}

export const tableJSON = (rows: number, cols: number) => ({ type: "table", content: Array.from({ length: rows }, (_, r) => ({ type: "tableRow",
  content: Array.from({ length: cols }, () => ({ type: r === 0 ? "tableHeader" : "tableCell", content: [{ type: "paragraph" }] })) })) });

/** Insert `block` (table/image JSON) at the cursor, with its caption next to it in the same step (one undo). */
export function insertWithCaption(editor: Editor, block: unknown, o: CapOpts) {
  const above = o.pos === "above", cap = o.on ? captionJSON(o.label, o.text, nextSeq(editor, o.label.trim() || "Figure", editor.state.selection.from), above) : null;
  editor.chain().focus().insertContent(cap ? (above ? [cap, block] : [block, cap]) : block as any).run();
}

/** Caption for the block the cursor is in / the selected block (existing table or image). */
export function captionExisting(editor: Editor, o: Omit<CapOpts, "on">) {
  const $f = editor.state.selection.$from, at = o.pos === "above" ? ($f.depth === 0 ? $f.pos : $f.before(1)) : ($f.depth === 0 ? $f.pos : $f.after(1));
  editor.chain().focus().insertContentAt(at, captionJSON(o.label, o.text, nextSeq(editor, o.label.trim() || "Figure", at), o.pos === "above")).run();
}
