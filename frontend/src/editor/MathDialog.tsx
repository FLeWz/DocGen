import { useState } from "react";
import { Modal } from "../ui";
import { renderMath } from "./math";

/** Edit the LaTeX of an equation with a live KaTeX preview; shows the parse error instead of failing silently. */
export function MathDialog({ initial, editing, onSubmit, onDelete, onClose }: {
  initial: { latex: string; display: boolean }; editing: boolean; onSubmit: (v: { latex: string; display: boolean }) => void; onDelete?: () => void; onClose: () => void;
}) {
  const [latex, setLatex] = useState(initial.latex), [display, setDisplay] = useState(initial.display);
  const r = renderMath(latex, display);
  return (
    <Modal title={editing ? "Edit equation" : "Insert equation"} size="md" onClose={onClose} actions={<>
      {onDelete && <button type="button" className="btn danger" onClick={onDelete}>Delete</button>}
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary" disabled={!latex.trim() || !!r.error} data-testid="math-ok" onClick={() => onSubmit({ latex: latex.trim(), display })}>{editing ? "Update" : "Insert"}</button></>}>
      <label className="field">LaTeX
        <textarea data-testid="math-latex" autoFocus rows={4} spellCheck={false} style={{ fontFamily: "var(--mono)", width: "100%" }} value={latex} onChange={(e) => setLatex(e.target.value)}
          onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && latex.trim() && !r.error) onSubmit({ latex: latex.trim(), display }); }} placeholder="\\frac{a}{b} + \\sum_{i=1}^{n} x_i" /></label>
      <label className="field" style={{ marginTop: 8 }}>Placement
        <select aria-label="Equation placement" value={display ? "display" : "inline"} onChange={(e) => setDisplay(e.target.value === "display")}>
          <option value="inline">Inline (in the text line)</option><option value="display">Display (own line, centred)</option></select></label>
      <div className="mathprev" data-testid="math-preview" aria-live="polite">{r.error ? <span className="err" role="alert">{r.error}</span> : r.html ? <span dangerouslySetInnerHTML={{ __html: r.html }} /> : <span className="muted">Preview appears here</span>}</div>
      <p className="muted small">Native LaTeX math. It becomes a Word equation in the DOCX and the PDF.</p>
    </Modal>
  );
}
