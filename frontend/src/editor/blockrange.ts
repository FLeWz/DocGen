// Block selection: the editor selection (text, cell, node or all) -> the whole top-level blocks it touches, plus a "Select
// blocks" mode with gutter handles (click = one block, Shift+click = range) that does not depend on the DOM selection.
import { Extension, type Editor } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { Node as PMNode } from "@tiptap/pm/model";

type BS = { on: boolean; a: number | null; b: number | null };
export const blocksKey = new PluginKey<BS>("blockselect");
const get = (s: { doc: PMNode } & Parameters<typeof blocksKey.getState>[0]) => blocksKey.getState(s) ?? { on: false, a: null, b: null };

/** Top-level block range [start, end) covered by the editor selection (a collapsed cursor = its block). A selection that
 *  ends at the very start of a block does not include that block; the handle selection (select mode) wins when set. */
export function blockRange(editor: Editor): [number, number] {
  const st = editor.state, d = st.doc, bs = get(st);
  if (bs.on && bs.a !== null && bs.b !== null) return [Math.min(bs.a, bs.b), Math.max(bs.a, bs.b) + 1];
  const { from, to } = st.selection;
  const $f = d.resolve(from), $t = d.resolve(to);
  const start = Math.min($f.index(0), d.childCount - 1);
  let end = $t.depth === 0 ? $t.index(0) : $t.index(0) + 1;
  if (to > from && $t.depth > 0) {
    const atStart = to - $t.before(1) === $t.depth && Array.from({ length: $t.depth - 1 }, (_, i) => $t.index(i + 1)).every((x) => x === 0);
    if (atStart && end - 1 > start) end--;
  }
  return [start, Math.max(end, start + 1)];
}

/** The selected top-level blocks as TipTap JSON (what the snippet request carries). */
export const selectedBlocks = (editor: Editor): unknown[] => {
  const [a, b] = blockRange(editor), out: unknown[] = [];
  editor.state.doc.forEach((n, _o, i) => { if (i >= a && i < b) out.push(n.toJSON()); });
  return out;
};

/** True when the user has a non-empty selection (or handle selection) worth offering "create snippet" for. */
export const hasSelection = (editor: Editor) => { const bs = get(editor.state); return (bs.on && bs.a !== null) || !editor.state.selection.empty; };

export const setSelectMode = (editor: Editor, on: boolean) => editor.view.dispatch(editor.state.tr.setMeta(blocksKey, { on, a: null, b: null }).setMeta("addToHistory", false));
export const pickBlock = (editor: Editor, i: number, extend: boolean) => {
  const bs = get(editor.state);
  editor.view.dispatch(editor.state.tr.setMeta(blocksKey, { on: true, a: extend && bs.a !== null ? bs.a : i, b: i }).setMeta("addToHistory", false));
};
export const selectModeOn = (editor: Editor) => get(editor.state).on;

export const BlockSelect = Extension.create({
  name: "blockSelect",
  addProseMirrorPlugins() {
    return [new Plugin<BS>({
      key: blocksKey,
      state: { init: () => ({ on: false, a: null, b: null }), apply(tr, v) { return tr.getMeta(blocksKey) ?? (tr.docChanged ? { ...v, a: null, b: null } : v); } },
      props: {
        decorations(state) {
          const bs = get(state);
          if (!bs.on) return null;
          const lo = Math.min(bs.a ?? 1e9, bs.b ?? 1e9), hi = Math.max(bs.a ?? -1, bs.b ?? -1), out: Decoration[] = [];
          state.doc.forEach((n, pos, i) => {
            out.push(Decoration.widget(pos, (view) => {
              const h = document.createElement("button");
              h.type = "button"; h.className = "blk-handle"; h.contentEditable = "false"; h.tabIndex = -1;
              h.title = "Select block (Shift+click extends)"; h.setAttribute("aria-label", `Select block ${i + 1}`);
              h.textContent = String(i + 1);
              h.onmousedown = (e) => e.preventDefault();
              h.onclick = (e) => { e.preventDefault(); const bsx = get(view.state); view.dispatch(view.state.tr.setMeta(blocksKey, { on: true, a: e.shiftKey && bsx.a !== null ? bsx.a : i, b: i }).setMeta("addToHistory", false)); };
              return h;
            }, { side: -1, key: `bh${i}` }));
            if (i >= lo && i <= hi) out.push(Decoration.node(pos, pos + n.nodeSize, { class: "blk-sel" }));
          });
          return DecorationSet.create(state.doc, out);
        },
      },
    })];
  },
});
