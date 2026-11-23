import { RevRow, VarDecl, Variables } from "../api";
import { NAME_RE } from "../editor/doc";
import { parseCols, reshape, Row } from "./blocks";

const rowsOf = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);

function Value({ d, v, ro, set }: { d: VarDecl; v: unknown; ro: boolean; set: (x: unknown) => void }) {
  if (d.type === "list") {
    const cols = d.columns ?? [], rows = rowsOf(v);
    return (
      <div>
        <table data-testid={`list-${d.name}`} style={{ borderCollapse: "collapse" }}>
          <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}<th /></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => (
              <td key={c}><input aria-label={`${d.name} ${i + 1} ${c}`} size={8} value={String(r[c] ?? "")} disabled={ro}
                onChange={(e) => set(rows.map((x, j) => (j === i ? { ...x, [c]: e.target.value } : x)))} /></td>))}
              <td>{!ro && <button aria-label={`Remove row ${i + 1} of ${d.name}`} onClick={() => set(rows.filter((_, j) => j !== i))}>×</button>}</td></tr>))}</tbody>
        </table>
        {!ro && <button disabled={cols.length === 0} onClick={() => set([...rows, reshape([{}], cols)[0]])}>+ Row</button>}
        {cols.length === 0 && <small> Define columns first.</small>}
      </div>
    );
  }
  const num = d.type === "number";
  return <input aria-label={`Value of ${d.name}`} type={num ? "number" : d.type === "date" ? "date" : "text"} disabled={ro} value={String(v ?? "")} placeholder={String(d.default ?? "")}
    onChange={(e) => set(e.target.value === "" ? null : num ? Number(e.target.value) : e.target.value)} />;
}

export function VariablesForm({ vars, ro, onChange }: { vars: Variables; ro: boolean; onChange: (v: Variables) => void }) {
  const setDecl = (d: VarDecl, p: Partial<VarDecl>) => onChange({ ...vars, declarations: vars.declarations.map((x) => (x === d ? { ...x, ...p } : x)) });
  return (
    <div>
      {vars.declarations.map((d) => (
        <fieldset key={d.name} style={{ margin: "4px 0", padding: "2px 6px" }}>
          <legend>{d.name}{" "}
            <select aria-label={`Type of ${d.name}`} value={d.type} disabled={ro} onChange={(e) => setDecl(d, { type: e.target.value as VarDecl["type"], default: null })}>
              {["text", "date", "number", "list"].map((t) => <option key={t}>{t}</option>)}</select>
            {!ro && <button aria-label={`Remove variable ${d.name}`} onClick={() => onChange({ declarations: vars.declarations.filter((x) => x !== d), values: Object.fromEntries(Object.entries(vars.values).filter(([k]) => k !== d.name)) })}>×</button>}
          </legend>
          {d.type === "list"
            ? <p><input aria-label={`Columns of ${d.name}`} placeholder="columns, comma separated" disabled={ro} defaultValue={(d.columns ?? []).join(", ")}
                onBlur={(e) => { const cols = parseCols(e.target.value); onChange({ declarations: vars.declarations.map((x) => (x === d ? { ...x, columns: cols } : x)), values: { ...vars.values, [d.name]: reshape(rowsOf(vars.values[d.name]), cols) } }); }} /></p>
            : <p><input aria-label={`Default of ${d.name}`} placeholder="default" size={10} disabled={ro} type={d.type === "number" ? "number" : d.type === "date" ? "date" : "text"} value={String(d.default ?? "")}
                onChange={(e) => setDecl(d, { default: e.target.value === "" ? null : d.type === "number" ? Number(e.target.value) : e.target.value })} /></p>}
          <Value d={d} v={vars.values[d.name]} ro={ro} set={(x) => onChange({ ...vars, values: { ...vars.values, [d.name]: x } })} />
        </fieldset>))}
      {!ro && <form onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget), n = String(f.get("name")).trim(), type = String(f.get("type")) as VarDecl["type"];
        if (NAME_RE.test(n) && !vars.declarations.some((d) => d.name === n)) onChange({ ...vars, declarations: [...vars.declarations, { name: n, type, default: null, columns: [] }] });
        e.currentTarget.reset();
      }}>
        <input name="name" placeholder="New variable name" size={14} />{" "}
        <select name="type" aria-label="New variable type">{["text", "date", "number", "list"].map((t) => <option key={t}>{t}</option>)}</select>{" "}
        <button>Add</button></form>}
    </div>
  );
}

export function RevisionTable({ rows, auto, ro, onChange }: { rows: RevRow[]; auto: boolean; ro: boolean; onChange: (r: RevRow[] | null) => void }) {
  const keys = ["version", "date", "author", "message"] as const;
  return (
    <div data-testid="revision-history">
      <small>{auto ? "Built from the git log of the document and its pinned snippets." : "Edited by hand (stored in the document)."}</small>
      <table style={{ borderCollapse: "collapse" }}>
        <tbody>{rows.map((r, i) => (
          <tr key={i}>{keys.map((k) => (
            <td key={k}><input aria-label={`Revision ${i + 1} ${k}`} size={k === "message" ? 14 : 8} value={r[k]} disabled={ro}
              onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))} /></td>))}
            <td>{!ro && <button aria-label={`Remove revision ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>}</td></tr>))}</tbody>
      </table>
      {rows.length === 0 && <small>No commits yet.</small>}
      {!ro && <><button onClick={() => onChange([...rows, { version: "", date: new Date().toISOString().slice(0, 10), author: "", message: "" }])}>+ Row</button>{" "}
        {!auto && <button onClick={() => onChange(null)}>Reset to auto</button>}</>}
    </div>
  );
}
