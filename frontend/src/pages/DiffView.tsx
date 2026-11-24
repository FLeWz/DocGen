import { Style } from "../api";
import { splitDiff } from "../diff";
import { PMNode } from "../editor/doc";
import { Preview } from "../editor/SnippetEditor";
import { Empty } from "../ui";

/** Side-by-side text diff of a unified diff. */
export function DiffText({ text }: { text: string }) {
  const rows = splitDiff(text);
  if (!rows.length) return <Empty icon="check" title="No differences" />;
  return (
    <div className="difftext" data-testid="diff-text" role="table" aria-label="Text diff">
      {rows.map((r, i) => r.kind === "hunk" ? <div key={i} className="dh" role="row">{r.text}</div> : (
        <div key={i} className="dr" role="row"><pre className={r.l === undefined ? "e" : r.kind === "chg" ? "del" : ""} role="cell">{r.l ?? ""}</pre><pre className={r.r === undefined ? "e" : r.kind === "chg" ? "add" : ""} role="cell">{r.r ?? ""}</pre></div>))}
    </div>
  );
}

/** Two rendered versions next to each other (before/after, or base/theirs/mine). */
export function Sides({ panes, styles }: { panes: { label: string; doc: PMNode | null | undefined }[]; styles: Style[] }) {
  return (
    <div className="sides" data-testid="diff-sides">
      {panes.map((p) => <section key={p.label} className="side"><div className="pane-h">{p.label}</div>{p.doc ? <Preview doc={p.doc} styles={styles} zoom={panes.length > 2 ? 0.3 : 0.42} /> : <div className="pane-msg">(none)</div>}</section>)}
    </div>
  );
}
