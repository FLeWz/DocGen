// Header/footer editing of an import: pick section / header|footer / variant, edit the part with the same ribbon and TipTap schema as
// the body (paragraphs, runs, tables, images, tab stops, PAGE/NUMPAGES fields, variable chips). Every change goes to `onChange`
// (stored in the import draft as `hf[part path]`) and shows live in the headers/footers of all pages.
import { useEditor, EditorContent } from "@tiptap/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Style } from "../api";
import { Modal } from "../ui";
import type { PMNode } from "./doc";
import { extensions } from "./extensions";
import { Look, pageVars } from "./look";
import { Ribbon } from "./Ribbon";

export type HfPart = { path: string; kind: "header" | "footer"; doc: PMNode; edited: boolean; warnings: string[] };
export type HfTarget = { si: number; kind: "header" | "footer"; type: "default" | "first" | "even" };
const TYPES: [HfTarget["type"], string][] = [["default", "Default"], ["first", "First page"], ["even", "Even pages"]];

/** Part path of a section's header/footer variant (the look resolves "same as previous" by repeating the earlier section's part). */
export const partOf = (look: Look | undefined, t: HfTarget): string | undefined => {
  const idx = look?.sections?.[t.si]?.[t.kind]?.[t.type];
  return idx === undefined ? undefined : look?.parts?.[idx]?.path;
};

export function HeaderFooterEditor({ look, parts, docs, target, setTarget, onChange, onClose, styles, ro }: {
  look: Look | undefined; parts: HfPart[]; docs: Record<string, PMNode>; target: HfTarget; setTarget: (t: HfTarget) => void;
  onChange: (path: string, doc: PMNode) => void; onClose: () => void; styles: Style[]; ro: boolean;
}) {
  const path = partOf(look, target), part = parts.find((p) => p.path === path);
  const [err, setErr] = useState("");
  const loaded = useRef("");
  const cb = useRef(onChange);
  cb.current = onChange;
  const editor = useEditor({ extensions, content: { type: "doc", content: [] }, editable: false, onUpdate: ({ editor: e }) => { if (loaded.current) cb.current(loaded.current, e.getJSON() as PMNode); } });
  useEffect(() => {
    if (!editor || !path || !part || loaded.current === path) return;
    loaded.current = "";  // setContent must not count as an edit
    editor.commands.setContent((docs[path] ?? part.doc) as never, { emitUpdate: false });
    editor.setEditable(!ro, false);
    loaded.current = path;
  }, [editor, path, part, docs, ro]);
  const sect = look?.sections?.[target.si];
  const shared = useMemo(() => (look?.sections ?? []).map((s, i) => (s[target.kind]?.[target.type] === sect?.[target.kind]?.[target.type] && sect?.[target.kind]?.[target.type] !== undefined ? i + 1 : 0)).filter(Boolean), [look, target, sect]);
  const page = sect?.page ?? look?.page;
  return (
    <Modal wide title={`Edit ${target.kind}`} onClose={onClose} actions={<button className="btn primary" type="button" onClick={onClose}>Done</button>}>
      <div className="stack" data-testid="hf-editor">
        <div className="row">
          <label>Section <select aria-label="Section" value={target.si} onChange={(e) => { loaded.current = ""; setTarget({ ...target, si: Number(e.target.value) }); }}>
            {(look?.sections ?? []).map((_, i) => <option key={i} value={i}>{i + 1}</option>)}</select></label>
          <label>Part <select aria-label="Header or footer" value={target.kind} onChange={(e) => { loaded.current = ""; setTarget({ ...target, kind: e.target.value as HfTarget["kind"] }); }}><option value="header">Header</option><option value="footer">Footer</option></select></label>
          <label>Variant <select aria-label="Variant" value={target.type} onChange={(e) => { loaded.current = ""; setTarget({ ...target, type: e.target.value as HfTarget["type"] }); }}>
            {TYPES.map(([v, l]) => <option key={v} value={v} disabled={sect?.[target.kind]?.[v] === undefined}>{l}{sect?.[target.kind]?.[v] === undefined ? " (none)" : ""}</option>)}</select></label>
        </div>
        {!path ? <p className="muted">This section has no {target.type} {target.kind}. Creating a new one is not supported yet: add it in Word, then import again.</p> : <>
          <p className="muted small" data-testid="hf-shared">{shared.length > 1 ? `Same as previous: this ${target.kind} is shared by sections ${shared.join(", ")}; editing it changes all of them.` : `Only section ${target.si + 1} uses this ${target.kind}.`} Fields: Insert tab → Field.</p>
          {part?.warnings.length ? <p className="muted small">{part.warnings.join(" · ")}</p> : null}
          {editor && <Ribbon editor={editor} ro={ro} styles={styles} mode="hf" onErr={setErr} />}
          {err && <div className="banner err" role="alert">{err}</div>}
          <div className="docx hfedit" style={page ? pageVars(page) : undefined}>{look && <style>{look.css}</style>}
            <div className="hfbox" data-testid="hf-box"><EditorContent editor={editor} aria-label={`${target.kind} editor`} /></div></div>
        </>}
      </div>
    </Modal>
  );
}
