import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import type { Style } from "../api";
import { Icon, useUI } from "../ui";
import { NAME_RE, PMNode } from "../editor/doc";
import { extensions } from "../editor/extensions";
import { Look, pageVars } from "../editor/look";

/** Inline mini-editor of a document's `paragraph` block: the styles of the document's base document, bold/italic, lists, variable chips. */
export function ParagraphEditor({ value, onChange, styles, look, ro }: { value: PMNode; onChange: (d: PMNode) => void; styles: Style[]; look?: Look; ro: boolean }) {
  const ui = useUI();
  const editor = useEditor({ extensions, content: value as never, editable: !ro, onUpdate: ({ editor: e }) => onChange(e.getJSON() as PMNode) });
  const st = useEditorState({
    editor, selector: ({ editor: e }) => (e ? { style: (e.state.selection.$from.parent.attrs.styleId as string | null) ?? (e.isActive("heading") ? `Heading${e.getAttributes("heading").level}` : "Normal"),
      bold: e.isActive("bold"), italic: e.isActive("italic"), bullet: e.isActive("bulletList"), ordered: e.isActive("orderedList") } : null),
  });
  if (!editor) return null;
  const run = () => editor.chain().focus();
  const apply = (sid: string) => {
    const s = styles.find((x) => x.style_id === sid);
    if (!s) return;
    if (s.kind === "heading" && (s.level ?? 9) <= 4) run().setNode("heading", { level: s.level, styleId: s.style_id }).run(); else run().setNode("paragraph", { styleId: s.style_id }).run();
  };
  const variable = async () => {
    const n = await ui.prompt({ title: "Insert variable", label: "Variable name", ok: "Insert", hint: "Letters, digits and _ (e.g. client_name)" });
    if (n && NAME_RE.test(n.trim())) run().insertContent({ type: "variable", attrs: { name: n.trim(), default: "", filter: null } }).run();
    else if (n) ui.toast("Variable name: letters, digits, _", "err");
  };
  const tb = (on: boolean) => `btn sm icon ${on ? "on" : ""}`;
  return (
    <div className="pedit" data-testid="para-editor">
      {!ro && <div className="ptools" role="toolbar" aria-label="Paragraph formatting">
        <select aria-label="Paragraph style" value={st?.style} onChange={(e) => apply(e.target.value)}>{styles.filter((s) => s.kind === "paragraph" || s.kind === "heading").map((s) => <option key={s.style_id} value={s.style_id}>{s.name}</option>)}</select>
        <button type="button" className={tb(!!st?.bold)} title="Bold" aria-label="Bold" aria-pressed={!!st?.bold} onClick={() => run().toggleBold().run()}><Icon n="bold" /></button>
        <button type="button" className={tb(!!st?.italic)} title="Italic" aria-label="Italic" aria-pressed={!!st?.italic} onClick={() => run().toggleItalic().run()}><Icon n="italic" /></button>
        <button type="button" className={tb(!!st?.bullet)} title="Bulleted list" aria-label="Bulleted list" aria-pressed={!!st?.bullet} onClick={() => run().toggleBulletList().run()}><Icon n="ul" /></button>
        <button type="button" className={tb(!!st?.ordered)} title="Numbered list" aria-label="Numbered list" aria-pressed={!!st?.ordered} onClick={() => run().toggleOrderedList().run()}><Icon n="ol" /></button>
        <button type="button" className="btn sm" title="Insert a variable chip" onClick={variable}>{"{ }"} Variable</button>
      </div>}
      <div className="docx pbody" style={look ? pageVars(look.page) : undefined}>{look && <style>{look.css}</style>}<EditorContent editor={editor} aria-label="Paragraph text" /></div>
    </div>
  );
}
