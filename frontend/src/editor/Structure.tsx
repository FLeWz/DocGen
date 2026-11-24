// Sidebar outline: headings, marked sections (rename, jump, unmark), generated lists and captions of the document.
import type { Editor } from "@tiptap/core";
import { useEffect, useState } from "react";
import { Icon } from "../ui";
import type { PMNode } from "./doc";
import { outline, type OutlineItem } from "./outline";
import { jumpTo, renameSection, unmarkList, unmarkSection } from "./sections";

const NAME: Record<string, string> = { toc: "Contents", tof: "Figures", tot: "Tables", section: "Section", appendix: "Appendix" };

function SectionRow({ it, editor, ro }: { it: Extract<OutlineItem, { kind: "section" }>; editor: Editor; ro: boolean }) {
  const [t, setT] = useState(it.title);
  useEffect(() => setT(it.title), [it.title]);
  const save = () => { if (t.trim() && t.trim() !== it.title) renameSection(editor, it.id, t); else setT(it.title); };
  return (
    <li data-testid="outline-section">
      <span className="kind">{NAME[it.sec]}</span>
      <input aria-label={`Rename ${NAME[it.sec]} ${it.title}`} value={t} disabled={ro} onChange={(e) => setT(e.target.value)} onBlur={save} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLElement).blur(); }} />
      <button type="button" className="btn sm ghost" title={`Jump to block ${it.from + 1}`} onClick={() => jumpTo(editor, it.from)}>Jump</button>
      <button type="button" className="btn sm ghost" disabled={ro} title="Remove the mark (the blocks stay)" onClick={() => unmarkSection(editor, it.id)}>Unmark</button>
    </li>
  );
}

export function Structure({ editor, doc, ro }: { editor: Editor; doc: PMNode; ro: boolean }) {
  const items = outline(doc);
  return (
    <aside className="pane structure" aria-label="Structure" data-testid="structure">
      <div className="pane-h">Structure</div>
      {items.length === 0 ? <p className="muted small" style={{ padding: 10 }}>No headings or marked sections yet. Select blocks and use References → Mark as…</p> :
        <ul>{items.map((it, k) => {
          if (it.kind === "section") return <SectionRow key={`s${it.id}`} it={it} editor={editor} ro={ro} />;
          if (it.kind === "heading") return <li key={k} style={{ paddingLeft: 4 + (it.level - 1) * 12 }}><button type="button" className="grow btn ghost" style={{ textAlign: "left" }} onClick={() => jumpTo(editor, it.index)}>{it.text || "(empty heading)"}</button></li>;
          if (it.kind === "toc") return <li key={k} data-testid="outline-list"><span className="kind">{NAME[it.list]}</span><button type="button" className="grow btn ghost" style={{ textAlign: "left" }} onClick={() => jumpTo(editor, it.index)}>{it.title || (it.imported ? "Imported list" : "Generated list")}</button>
            <button type="button" className="btn sm ghost" disabled={ro} title={it.imported ? "Turn it back into an opaque block" : "Remove the generated list"} onClick={() => unmarkList(editor, it.index)}><Icon n="x" size={12} />Unmark</button></li>;
          return <li key={k}><span className="kind">{it.label}</span><button type="button" className="grow btn ghost" style={{ textAlign: "left" }} onClick={() => jumpTo(editor, it.index)}>{it.text}</button></li>;
        })}</ul>}
    </aside>
  );
}
