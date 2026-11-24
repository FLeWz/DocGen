import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api, isAdmin, Me, Style } from "../api";
import { Badge, ErrorBanner, Field, Icon, Modal, Skeleton, useUI } from "../ui";

const num = (v: FormDataEntryValue | null) => (v === "" || v === null ? null : Number(v));
const txt = (v: FormDataEntryValue | null) => (v === "" || v === null ? null : String(v));

/** One style = one row = one form (inputs attach with form=). style_id/kind are fixed after creation. */
function Row({ s, admin, onDone }: { s: Style; admin: boolean; onDone: () => void }) {
  const ui = useUI();
  const [dirty, setDirty] = useState(false);
  const save = useMutation({ mutationFn: (b: unknown) => api(`/styles/${s.id}`, "PATCH", b), onSuccess: () => { ui.toast(`Saved ${s.name}`); onDone(); }, onError: (e: Error) => ui.toast(e.message, "err") });
  const del = async () => {
    if (await ui.confirm({ title: `Delete style ${s.style_id}?`, danger: true, ok: "Delete style", body: "Content that still uses it falls back to Normal / the heading default." }))
      api(`/styles/${s.id}`, "DELETE").then(() => { ui.toast("Style deleted"); onDone(); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget);
    save.mutate({ name: d.get("name"), color: txt(d.get("color")), font_family: txt(d.get("font_family")), font_size: num(d.get("font_size")), numbering: d.get("numbering"),
      space_before: num(d.get("space_before")) ?? 0, space_after: num(d.get("space_after")) ?? 0, bold: d.get("bold") === "on", italic: d.get("italic") === "on" });
  };
  const id = `style-${s.style_id}`, dis = !admin, a = (n: string) => `${s.style_id} ${n}`;
  return (
    <tr>
      <td><code>{s.style_id}</code><small className="slug">{s.kind}{s.level ? ` ${s.level}` : ""}{s.builtin ? " · built-in" : ""}</small></td>
      <td><form id={id} onSubmit={submit} onChange={() => setDirty(true)} /><input form={id} name="name" aria-label={a("name")} defaultValue={s.name} disabled={dis} required /></td>
      <td><span className="colorcell"><i style={{ background: s.color ?? "transparent" }} /><input form={id} name="color" aria-label={a("color")} defaultValue={s.color ?? ""} pattern="#[0-9a-fA-F]{6}" placeholder="#rrggbb" disabled={dis} /></span></td>
      <td><input form={id} name="font_family" aria-label={a("font")} defaultValue={s.font_family ?? ""} placeholder="inherit" disabled={dis} /></td>
      <td><input form={id} name="font_size" aria-label={a("size")} type="number" step="0.5" min="1" max="200" defaultValue={s.font_size ?? ""} className="n" disabled={dis} /></td>
      <td><select form={id} name="numbering" aria-label={a("numbering")} defaultValue={s.numbering} disabled={dis}><option>none</option><option>decimal</option></select></td>
      <td><input form={id} name="space_before" aria-label={a("space before")} type="number" min="0" max="200" defaultValue={s.space_before} className="n" disabled={dis} /></td>
      <td><input form={id} name="space_after" aria-label={a("space after")} type="number" min="0" max="200" defaultValue={s.space_after} className="n" disabled={dis} /></td>
      <td className="c"><input form={id} name="bold" aria-label={a("bold")} type="checkbox" defaultChecked={s.bold} disabled={dis} /></td>
      <td className="c"><input form={id} name="italic" aria-label={a("italic")} type="checkbox" defaultChecked={s.italic} disabled={dis} /></td>
      <td className="end nowrap">{admin && <><button form={id} className="btn sm primary" aria-label={`Save ${s.style_id}`} disabled={!dirty || save.isPending}>Save</button>
        {!s.builtin && <button type="button" className="btn sm ghost" aria-label={`Delete ${s.style_id}`} onClick={del}><Icon n="trash" /></button>}</>}</td>
    </tr>
  );
}

export function Styles({ me }: { me: Me }) {
  const qc = useQueryClient(), ui = useUI();
  const admin = isAdmin(me.org?.role);
  const [adding, setAdding] = useState(false), [kind, setKind] = useState("paragraph");
  const list = useQuery({ queryKey: ["styles", me.org?.id], queryFn: () => api<Style[]>("/styles") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["styles"] });
  const create = useMutation({ mutationFn: (b: unknown) => api("/styles", "POST", b), onSuccess: () => { refresh(); setAdding(false); ui.toast("Style added"); } });
  const add = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = new FormData(e.currentTarget), k = String(d.get("kind"));
    create.mutate({ style_id: d.get("style_id"), name: d.get("name"), kind: k, level: k === "heading" ? Number(d.get("level") || 1) : null });
  };
  return (
    <section className="card"><div className="cardhead"><h2>Style set</h2>{admin && <button className="btn primary" onClick={() => setAdding(true)}><Icon n="plus" />Add style</button>}</div>
      <p className="muted">Named styles offered in the editor. Content refers to them by id; a base document's own styles with the same id win.{!admin && <> <Badge>read-only</Badge></>}</p>
      {list.isLoading ? <Skeleton rows={6} h={34} /> : (
        <div className="tblwrap"><table className="tbl stylestbl">
          <thead><tr><th>Id</th><th>Name</th><th>Colour</th><th>Font</th><th>Size pt</th><th>Numbering</th><th>Before</th><th>After</th><th className="c">B</th><th className="c">I</th><th /></tr></thead>
          <tbody>{list.data?.map((s) => <Row key={s.id + JSON.stringify(s)} s={s} admin={admin} onDone={refresh} />)}</tbody></table></div>)}
      <ErrorBanner e={list.error} />
      {adding && (
        <Modal title="Add style" onClose={() => setAdding(false)} actions={<><button className="btn" onClick={() => setAdding(false)}>Cancel</button><button className="btn primary" form="style-form" disabled={create.isPending}>Add style</button></>}>
          <form id="style-form" className="stack" onSubmit={add}>
            <Field label="Style id" hint="Letters and digits, e.g. Warning">{(id) => <input id={id} name="style_id" pattern="[A-Za-z][A-Za-z0-9]{0,39}" required autoFocus />}</Field>
            <Field label="Name">{(id) => <input id={id} name="name" required />}</Field>
            <Field label="Kind">{(id) => <select id={id} name="kind" value={kind} onChange={(e) => setKind(e.target.value)}><option>paragraph</option><option>heading</option></select>}</Field>
            {kind === "heading" && <Field label="Heading level">{(id) => <input id={id} name="level" type="number" min="1" max="9" defaultValue={1} />}</Field>}
            <ErrorBanner e={create.error} />
          </form>
        </Modal>)}
    </section>
  );
}
