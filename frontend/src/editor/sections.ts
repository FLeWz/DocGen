// Marking blocks as typed sections (bookmark ranges in the DOCX), generated lists, jumping to a block.
import type { Editor } from "@tiptap/core";
import { NodeSelection, TextSelection } from "@tiptap/pm/state";
import { blockRange } from "./blockrange";

export type SecKind = "section" | "appendix";
export type ListKind = "toc" | "tof" | "tot";

const forBlocks = (editor: Editor, fn: (n: ReturnType<Editor["state"]["doc"]["child"]>, pos: number, i: number) => void) =>
  editor.state.doc.forEach((n, pos, i) => fn(n, pos, i));

const nextId = (editor: Editor) => { let m = 0; forBlocks(editor, (n) => { m = Math.max(m, Number(n.attrs.sec?.id) || 0); }); return String(m + 1); };

/** Mark the top-level blocks of the selection (or [from,to) given) as one section. Returns the section id. */
export function markSection(editor: Editor, kind: SecKind, title: string, range?: [number, number]): string {
  const [a, b] = range ?? blockRange(editor), id = nextId(editor), tr = editor.state.tr;
  forBlocks(editor, (n, pos, i) => { if (i >= a && i < b) tr.setNodeMarkup(pos, undefined, { ...n.attrs, sec: { id, kind, title: title.trim().slice(0, 60) } }); });
  editor.view.dispatch(tr);
  return id;
}

const patchSec = (editor: Editor, id: string, fn: (s: Record<string, unknown>) => Record<string, unknown> | null) => {
  const tr = editor.state.tr;
  forBlocks(editor, (n, pos) => { if (String(n.attrs.sec?.id) === id) tr.setNodeMarkup(pos, undefined, { ...n.attrs, sec: fn(n.attrs.sec) }); });
  editor.view.dispatch(tr);
};
export const unmarkSection = (editor: Editor, id: string) => patchSec(editor, id, () => null);
export const renameSection = (editor: Editor, id: string, title: string) => patchSec(editor, id, (s) => ({ ...s, title: title.trim().slice(0, 60) }));

const tocNode = (kind: ListKind, o: { title?: string; levels?: string; label?: string | null } = {}) =>
  ({ type: "toc", attrs: { kind, title: o.title ?? "", levels: o.levels ?? "1-3", label: kind === "tof" ? o.label || "Figure" : kind === "tot" ? o.label || "Table" : null, xml: null, refresh: true } });

/** Replace the selected blocks by a generated list (the old entries are regenerated from the headings/captions). */
export function markList(editor: Editor, kind: ListKind, o?: Parameters<typeof tocNode>[1]) {
  const [a, b] = blockRange(editor);
  let from = 0, to = 0;
  forBlocks(editor, (n, pos, i) => { if (i === a) from = pos; if (i === b - 1) to = pos + n.nodeSize; });
  editor.view.dispatch(editor.state.tr.replaceWith(from, to, editor.schema.nodeFromJSON(tocNode(kind, o))));
}

/** Insert a generated list after the block holding the cursor. */
export function insertList(editor: Editor, kind: ListKind, o?: Parameters<typeof tocNode>[1]) {
  const $f = editor.state.selection.$from, pos = $f.depth === 0 ? $f.pos : $f.after(1);
  editor.chain().focus().insertContentAt(pos, tocNode(kind, o)).run();
}

/** Remove a generated list; an imported one (original XML) becomes an opaque block again so nothing is lost. */
export function unmarkList(editor: Editor, index: number) {
  const hit = { pos: -1, size: 0, xml: null as string | null };
  forBlocks(editor, (n, pos, i) => { if (i === index) { hit.pos = pos; hit.size = n.nodeSize; hit.xml = n.attrs.xml ?? null; } });
  if (hit.pos < 0) return;
  const tr = editor.state.tr;
  if (hit.xml) tr.replaceWith(hit.pos, hit.pos + hit.size, editor.schema.nodeFromJSON({ type: "opaque", attrs: { kind: "table of contents", xml: hit.xml } }));
  else tr.delete(hit.pos, hit.pos + hit.size);
  editor.view.dispatch(tr);
}

/** Select block `index` (node selection for atoms, cursor at its start otherwise) and scroll to it. */
export function jumpTo(editor: Editor, index: number) {
  let found = false;
  forBlocks(editor, (n, pos, i) => {
    if (i !== index || found) return;
    found = true;
    const sel = n.isAtom ? NodeSelection.create(editor.state.doc, pos) : TextSelection.near(editor.state.doc.resolve(pos + 1));
    editor.view.dispatch(editor.state.tr.setSelection(sel).scrollIntoView());
    editor.view.focus();
    (editor.view.nodeDOM(pos) as HTMLElement | null)?.scrollIntoView?.({ block: "center" });
  });
}
