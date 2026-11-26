// Source editor of the text modes (CodeMirror 6): markdown highlighting, wrapping, search, undo within the text. Colours come from the
// app's CSS variables, so light and dark themes need no second theme.
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { EditorState } from "@codemirror/state";
import { drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

export type TextHandle = { jump: (offset: number) => void; focus: () => void };

const look = HighlightStyle.define([
  { tag: t.heading, fontWeight: "700", color: "var(--primary)" }, { tag: t.strong, fontWeight: "700" }, { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.link, color: "var(--primary)" }, { tag: t.url, color: "var(--muted)" }, { tag: t.monospace, fontFamily: "var(--mono)" },
  { tag: t.quote, color: "var(--muted)" }, { tag: [t.processingInstruction, t.meta, t.contentSeparator], color: "var(--muted)" }, { tag: t.strikethrough, textDecoration: "line-through" },
]);
const theme = EditorView.theme({
  "&": { height: "100%", color: "var(--text)", backgroundColor: "var(--surface)", fontSize: "13px" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55", overflow: "auto" },
  ".cm-content": { caretColor: "var(--text)", padding: "10px 0" },
  ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", border: "none" },
  ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--primary) 8%, transparent)" },
  "&.cm-focused": { outline: "none" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { backgroundColor: "color-mix(in srgb, var(--primary) 28%, transparent)" },
  ".cm-panels": { backgroundColor: "var(--surface-2)", color: "var(--text)" },
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--primary) 25%, transparent)" },
});

export const TextEditor = forwardRef<TextHandle, { value: string; onChange: (v: string) => void; readOnly?: boolean; label?: string }>(function TextEditor({ value, onChange, readOnly, label }, ref) {
  const host = useRef<HTMLDivElement>(null), view = useRef<EditorView | null>(null), cb = useRef(onChange);
  cb.current = onChange;
  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({ doc: value, extensions: [
        lineNumbers(), history(), drawSelection(), highlightActiveLine(), highlightSelectionMatches(), search({ top: true }), markdown(), EditorView.lineWrapping,
        syntaxHighlighting(look), theme, keymap.of([...searchKeymap, ...historyKeymap, ...defaultKeymap, indentWithTab]),
        EditorState.readOnly.of(!!readOnly), EditorView.editable.of(!readOnly),
        EditorView.contentAttributes.of({ "aria-label": label ?? "Markdown source" }),
        EditorView.updateListener.of((u) => { if (u.docChanged) cb.current(u.state.doc.toString()); }),
      ] }),
    });
    view.current = v;
    return () => { v.destroy(); view.current = null; };
  }, [readOnly]); // eslint-disable-line react-hooks/exhaustive-deps  (value is the initial text; later changes come from the editor itself)
  useImperativeHandle(ref, () => ({
    jump: (o) => { const v = view.current; if (v) { v.dispatch({ selection: { anchor: Math.min(o, v.state.doc.length) }, scrollIntoView: true }); v.focus(); } },
    focus: () => view.current?.focus(),
  }), []);
  return <div className="mdsrc" data-testid="text-editor" ref={host} />;
});
