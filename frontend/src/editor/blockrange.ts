import type { Editor } from "@tiptap/core";

/** Top-level block range [start, end) covered by the editor selection (a collapsed cursor = its block). */
export function blockRange(editor: Editor): [number, number] {
  const { from, to } = editor.state.selection, d = editor.state.doc;
  const $f = d.resolve(from), $t = d.resolve(to);
  const start = Math.min($f.index(0), d.childCount - 1);
  const end = $t.depth === 0 ? $t.index(0) : $t.index(0) + 1;
  return [start, Math.max(end, start + 1)];
}
