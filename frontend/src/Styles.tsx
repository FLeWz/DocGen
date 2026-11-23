import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent } from "react";
import { api, isAdmin, Me, Style } from "./api";

const Err = ({ e }: { e: unknown }) => (e ? <p role="alert" style={{ color: "crimson" }}>{(e as Error).message}</p> : null);
const num = (v: FormDataEntryValue | null) => (v === "" || v === null ? null : Number(v));
const txt = (v: FormDataEntryValue | null) => (v === "" || v === null ? null : String(v));

/** Org style set: a table, one row = one form (admin edits, others read). style_id/kind are fixed after creation. */
function Row({ s, admin, onDone }: { s: Style; admin: boolean; onDone: () => void }) {
  const save = useMutation({ mutationFn: (b: unknown) => api(`/styles/${s.id}`, "PATCH", b), onSuccess: onDone });
  const del = useMutation({ mutationFn: () => api(`/styles/${s.id}`, "DELETE"), onSuccess: onDone });
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget);
    save.mutate({ name: d.get("name"), color: txt(d.get("color")), font_family: txt(d.get("font_family")), font_size: num(d.get("font_size")),
      numbering: d.get("numbering"), space_before: num(d.get("space_before")) ?? 0, space_after: num(d.get("space_after")) ?? 0,
      bold: d.get("bold") === "on", italic: d.get("italic") === "on" });
  };
  const id = `style-${s.style_id}`;
  const dis = !admin;
  return (
    <tr>
      <td><code>{s.style_id}</code> <small>{s.kind}{s.level ? ` ${s.level}` : ""}</small></td>
      <td><form id={id} onSubmit={submit} /><input form={id} name="name" aria-label={`${s.style_id} name`} defaultValue={s.name} disabled={dis} required /></td>
      <td><input form={id} name="color" aria-label={`${s.style_id} color`} defaultValue={s.color ?? ""} pattern="#[0-9a-fA-F]{6}" placeholder="#rrggbb" size={8} disabled={dis} /></td>
      <td><input form={id} name="font_family" aria-label={`${s.style_id} font`} defaultValue={s.font_family ?? ""} size={12} disabled={dis} /></td>
      <td><input form={id} name="font_size" aria-label={`${s.style_id} size`} type="number" step="0.5" min="1" max="200" defaultValue={s.font_size ?? ""} style={{ width: 60 }} disabled={dis} /></td>
      <td><select form={id} name="numbering" aria-label={`${s.style_id} numbering`} defaultValue={s.numbering} disabled={dis}><option>none</option><option>decimal</option></select></td>
      <td><input form={id} name="space_before" aria-label={`${s.style_id} space before`} type="number" min="0" max="200" defaultValue={s.space_before} style={{ width: 50 }} disabled={dis} /></td>
      <td><input form={id} name="space_after" aria-label={`${s.style_id} space after`} type="number" min="0" max="200" defaultValue={s.space_after} style={{ width: 50 }} disabled={dis} /></td>
      <td><input form={id} name="bold" aria-label={`${s.style_id} bold`} type="checkbox" defaultChecked={s.bold} disabled={dis} /></td>
      <td><input form={id} name="italic" aria-label={`${s.style_id} italic`} type="checkbox" defaultChecked={s.italic} disabled={dis} /></td>
      <td>{admin && <><button form={id} aria-label={`Save ${s.style_id}`}>Save</button>{!s.builtin && <button type="button" onClick={() => confirm(`Delete style ${s.style_id}?`) && del.mutate()}>Delete</button>}</>}
        <Err e={save.error ?? del.error} /></td>
    </tr>
  );
}

export function Styles({ me }: { me: Me }) {
  const qc = useQueryClient();
  const admin = isAdmin(me.org?.role);
  const list = useQuery({ queryKey: ["styles", me.org?.id], queryFn: () => api<Style[]>("/styles") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["styles"] });
  const create = useMutation({ mutationFn: (b: unknown) => api("/styles", "POST", b), onSuccess: refresh });
  const add = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget), kind = String(d.get("kind"));
    create.mutate({ style_id: d.get("style_id"), name: d.get("name"), kind, level: kind === "heading" ? Number(d.get("level") || 1) : null });
    e.currentTarget.reset();
  };
  return (
    <details>
      <summary><h2 style={{ display: "inline" }}>Style set</h2></summary>
      <p><small>Named styles available in the editor. Content refers to them by id; a base document's own styles with the same id win.</small></p>
      <table>
        <thead><tr><th>Id</th><th>Name</th><th>Color</th><th>Font</th><th>Size pt</th><th>Numbering</th><th>Before</th><th>After</th><th>B</th><th>I</th><th /></tr></thead>
        <tbody>{list.data?.map((s) => <Row key={s.id + JSON.stringify(s)} s={s} admin={admin} onDone={refresh} />)}</tbody>
      </table>
      {admin && <form onSubmit={add}>
        <input name="style_id" placeholder="Style id (e.g. Warning)" pattern="[A-Za-z][A-Za-z0-9]{0,39}" required />{" "}
        <input name="name" placeholder="Name" required />{" "}
        <select name="kind" defaultValue="paragraph"><option>paragraph</option><option>heading</option></select>{" "}
        <input name="level" type="number" min="1" max="9" placeholder="level (headings)" />{" "}
        <button>Add style</button>
      </form>}
      <Err e={list.error ?? create.error} />
    </details>
  );
}
