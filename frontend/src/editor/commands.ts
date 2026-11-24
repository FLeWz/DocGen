// Editor commands behind the ribbon: direct formatting (fmt mark / pfmt attr) merged into what is there, indent, clear.
import type { Editor } from "@tiptap/core";

export type Attrs = Record<string, any>;
const clean = (o: Attrs) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== false && v !== ""));

/** Merge a patch into formatting attrs; null/undefined/false/"" in the patch remove the key. */
export const mergeFmt = (cur: Attrs | null | undefined, patch: Attrs): Attrs => clean({ ...(cur ?? {}), ...patch });

const FMT_KEYS = ["color", "font", "size", "hl", "strike", "va", "caps", "smallCaps", "rStyle"];
/** Apply run formatting (colour, font, size in half-points, highlight, strike, va ...) to the selection. */
export function setFmt(editor: Editor, patch: Attrs) {
  const next = mergeFmt(editor.getAttributes("fmt"), patch);
  const c = editor.chain().focus();
  if (!Object.keys(next).length) c.unsetMark("fmt").run();
  else c.setMark("fmt", Object.fromEntries(FMT_KEYS.map((k) => [k, next[k] ?? null]))).run();
}

/** Paragraph formatting on every paragraph/heading touched by the selection. */
export function setPfmt(editor: Editor, patch: Attrs | ((cur: Attrs) => Attrs)) {
  const { from, to } = editor.state.selection, tr = editor.state.tr;
  editor.state.doc.nodesBetween(from, to, (n, pos) => {
    if (n.type.name !== "paragraph" && n.type.name !== "heading") return true;
    const p = typeof patch === "function" ? patch(n.attrs.pfmt ?? {}) : patch;
    tr.setNodeMarkup(pos, undefined, { ...n.attrs, pfmt: (m => (Object.keys(m).length ? m : null))(mergeFmt(n.attrs.pfmt, p)) });
    return false;
  });
  editor.view.dispatch(tr); editor.view.focus();
}

/** Indent (twips, 0.25 inch steps). In a list the list item is sunk/lifted instead. */
export function indent(editor: Editor, dir: 1 | -1) {
  if (editor.isActive("listItem")) { (dir > 0 ? editor.chain().focus().sinkListItem("listItem") : editor.chain().focus().liftListItem("listItem")).run(); return; }
  setPfmt(editor, (cur) => ({ left: Math.max((Number(cur.left) || 0) + 360 * dir, 0) || null }));
}

/** Remove direct character and paragraph formatting (named style stays). */
export function clearFormatting(editor: Editor) {
  editor.chain().focus().unsetAllMarks().run();
  setPfmt(editor, (cur) => Object.fromEntries(Object.keys(cur).filter((k) => k !== "tabs").map((k) => [k, null])));
}

/** Attrs of the closest list around the cursor, updated. */
export function setList(editor: Editor, patch: Attrs) {
  const name = editor.isActive("orderedList") ? "orderedList" : editor.isActive("bulletList") ? "bulletList" : null;
  if (name) editor.chain().focus().updateAttributes(name, patch).run();
}

/** Insert a block at the end of the top-level block holding the cursor (or before it). */
export function insertBlock(editor: Editor, node: Attrs, before = false) {
  const $f = editor.state.selection.$from, pos = $f.depth === 0 ? $f.pos : before ? $f.before(1) : $f.after(1);
  editor.chain().focus().insertContentAt(pos, node).run();
}
