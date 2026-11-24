import { RevRow, VarDecl, Variables } from "../api";
import { NAME_RE } from "../editor/doc";
import { formatDate, todayIso } from "../dates";
import { Icon } from "../ui";
import { DateInput } from "./DateInput";
import { parseCols, reshape, Row } from "./blocks";

const rowsOf = (v: unknown): Row[] => (Array.isArray(v) ? (v as Row[]) : []);
const inputType = (t: VarDecl["type"]) => (t === "number" ? "number" : "text");

function Value({ d, v, ro, set }: { d: VarDecl; v: unknown; ro: boolean; set: (x: unknown) => void }) {
  if (d.type === "list") {
    const cols = d.columns ?? [], rows = rowsOf(v);
    return (
      <div>
        {cols.length > 0 && <table className="tbl compact" data-testid={`list-${d.name}`}>
          <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}<th /></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}><input aria-label={`${d.name} ${i + 1} ${c}`} value={String(r[c] ?? "")} disabled={ro} onChange={(e) => set(rows.map((x, j) => (j === i ? { ...x, [c]: e.target.value } : x)))} /></td>)}
              <td>{!ro && <button className="btn ghost icon" aria-label={`Remove row ${i + 1} of ${d.name}`} onClick={() => set(rows.filter((_, j) => j !== i))}><Icon n="x" size={14} /></button>}</td></tr>))}</tbody>
        </table>}
        {!ro && <button className="btn sm" disabled={cols.length === 0} onClick={() => set([...rows, reshape([{}], cols)[0]])}><Icon n="plus" size={14} />Row</button>}
        {cols.length === 0 && <small className="muted"> Define columns first.</small>}
      </div>
    );
  }
  if (d.type === "date") return <DateInput label={`Value of ${d.name}`} value={v} disabled={ro} placeholder={`value (default: ${d.default ? formatDate(d.default) : "none"})`} onChange={set} />;
  return <input aria-label={`Value of ${d.name}`} type={inputType(d.type)} disabled={ro} value={String(v ?? "")} placeholder={`value (default: ${d.default ?? "none"})`}
    onChange={(e) => set(e.target.value === "" ? null : d.type === "number" ? Number(e.target.value) : e.target.value)} />;
}

/** `fixed`: the declarations come from a base document (no add/remove/columns/default field; the value field is what is edited:
 *  "defaults" = the base document's defaults, "values" = a document's values, type locked). */
export function VariablesForm({ vars, ro, onChange, fixed = false }: { vars: Variables; ro: boolean; onChange: (v: Variables) => void; fixed?: false | "values" | "defaults" }) {
  const setDecl = (d: VarDecl, p: Partial<VarDecl>) => onChange({ ...vars, declarations: vars.declarations.map((x) => (x === d ? { ...x, ...p } : x)) });
  return (
    <div className="vars">
      {vars.declarations.length === 0 && <p className="muted small">{fixed ? "The base document uses no variables of its own." : "No variables yet. Variables used in snippets are filled in here."}</p>}
      {vars.declarations.map((d) => (
        <fieldset key={d.name} className="varcard">
          <legend><code>{d.name}</code></legend>
          <div className="vhead">
            <select aria-label={`Type of ${d.name}`} value={d.type} disabled={ro || fixed === "values"} onChange={(e) => setDecl(d, { type: e.target.value as VarDecl["type"], default: null })}>{["text", "date", "number", "list"].map((t) => <option key={t}>{t}</option>)}</select>
            {!ro && !fixed && <button className="btn ghost icon" aria-label={`Remove variable ${d.name}`} title="Remove variable" onClick={() => onChange({ declarations: vars.declarations.filter((x) => x !== d), values: Object.fromEntries(Object.entries(vars.values).filter(([k]) => k !== d.name)) })}><Icon n="trash" size={14} /></button>}
          </div>
          {fixed ? null : d.type === "list"
            ? <input aria-label={`Columns of ${d.name}`} placeholder="columns, comma separated" disabled={ro} defaultValue={(d.columns ?? []).join(", ")}
                onBlur={(e) => { const cols = parseCols(e.target.value); onChange({ declarations: vars.declarations.map((x) => (x === d ? { ...x, columns: cols } : x)), values: { ...vars.values, [d.name]: reshape(rowsOf(vars.values[d.name]), cols) } }); }} />
            : d.type === "date" ? <DateInput label={`Default of ${d.name}`} value={d.default} disabled={ro} placeholder="default" onChange={(x) => setDecl(d, { default: x })} />
            : <input aria-label={`Default of ${d.name}`} placeholder="default" disabled={ro} type={inputType(d.type)} value={String(d.default ?? "")}
                onChange={(e) => setDecl(d, { default: e.target.value === "" ? null : d.type === "number" ? Number(e.target.value) : e.target.value })} />}
          <Value d={d} v={vars.values[d.name]} ro={ro} set={(x) => onChange({ ...vars, values: { ...vars.values, [d.name]: x } })} />
        </fieldset>))}
      {!ro && !fixed && <form className="addvar" onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget), n = String(f.get("name")).trim(), type = String(f.get("type")) as VarDecl["type"];
        if (NAME_RE.test(n) && !vars.declarations.some((d) => d.name === n)) onChange({ ...vars, declarations: [...vars.declarations, { name: n, type, default: null, columns: [] }] });
        e.currentTarget.reset();
      }}>
        <input name="name" aria-label="New variable name" placeholder="New variable name" />
        <select name="type" aria-label="New variable type">{["text", "date", "number", "list"].map((t) => <option key={t}>{t}</option>)}</select>
        <button className="btn sm">Add</button></form>}
    </div>
  );
}

export function RevisionTable({ rows, auto, ro, onChange }: { rows: RevRow[]; auto: boolean; ro: boolean; onChange: (r: RevRow[] | null) => void }) {
  const keys = ["version", "date", "author", "message", "sections", "commit"] as const;
  return (
    <div data-testid="revision-history">
      <p className="muted small">{auto ? "Built automatically from the git log of the document, its pinned snippets and its base document (with the sections each commit changed)." : "Edited by hand (stored in the document)."}</p>
      {rows.map((r, i) => (
        <div key={i} className="revrow">{keys.map((k) => <input key={k} className={`r-${k}`} aria-label={`Revision ${i + 1} ${k}`} placeholder={k === "message" ? "description" : k === "sections" ? "sections changed" : k === "commit" ? "commit name" : k} value={r[k] ?? ""} disabled={ro} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))} />)}
          {!ro && <button className="btn ghost icon" aria-label={`Remove revision ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}><Icon n="x" size={14} /></button>}</div>))}
      {rows.length === 0 && <p className="muted small">No commits yet.</p>}
      {!ro && <div className="vact"><button className="btn sm" onClick={() => onChange([...rows, { version: "", date: formatDate(todayIso()), author: "", message: "", sections: "", commit: "" }])}><Icon n="plus" size={14} />Row</button>
        {!auto && <button className="btn sm ghost" onClick={() => onChange(null)}>Reset to auto</button>}</div>}
    </div>
  );
}
