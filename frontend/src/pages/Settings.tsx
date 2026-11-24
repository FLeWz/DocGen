import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, Invite, isAdmin, Me, Member, Role, ROLES } from "../api";
import { PageHead } from "../Shell";
import { Badge, Empty, ErrorBanner, fmtDate, Icon, Skeleton, useUI } from "../ui";
import { BaseDocs } from "./BaseDocs";
import { Styles } from "./Styles";

const form = (fn: (d: FormData, f: HTMLFormElement) => void) => (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); fn(new FormData(e.currentTarget), e.currentTarget); };

function Members({ me }: { me: Me }) {
  const qc = useQueryClient(), ui = useUI(), nav = useNavigate();
  const admin = isAdmin(me.org?.role);
  const members = useQuery({ queryKey: ["members", me.org?.id], queryFn: () => api<Member[]>("/members") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["members"] });
  const setRole = useMutation({ mutationFn: (v: [string, Role]) => api(`/members/${v[0]}`, "PATCH", { role: v[1] }), onSuccess: () => { refresh(); ui.toast("Role updated"); }, onError: (e: Error) => { refresh(); ui.toast(e.message, "err"); } });
  const remove = async (m: Member) => {
    const self = m.user_id === me.user.id;
    if (!(await ui.confirm({ title: self ? `Leave ${me.org!.name}?` : `Remove ${m.name}?`, danger: true, ok: self ? "Leave" : "Remove member", body: self ? "You lose access to this organization's content." : `${m.email} loses access to this organization.` }))) return;
    api(`/members/${m.user_id}`, "DELETE").then(() => { ui.toast(self ? "You left the organization" : "Member removed"); if (self) { nav("/"); qc.invalidateQueries(); } else refresh(); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
  return (
    <section className="card"><h2>Members</h2>
      {members.isLoading ? <Skeleton rows={4} h={34} /> : (
        <table className="tbl"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>{members.data?.map((m) => (
            <tr key={m.user_id}><td>{m.name}{m.user_id === me.user.id && <Badge kind="blue">you</Badge>}</td><td>{m.email}</td>
              <td>{admin ? <select aria-label={`Role of ${m.email}`} value={m.role} onChange={(e) => setRole.mutate([m.user_id, e.target.value as Role])}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select> : <Badge>{m.role}</Badge>}</td>
              <td className="end">{(admin || m.user_id === me.user.id) && <button className="btn sm ghost" onClick={() => remove(m)}>{m.user_id === me.user.id ? "Leave" : "Remove"}</button>}</td></tr>))}</tbody></table>)}
      <ErrorBanner e={members.error} />
    </section>
  );
}

const statusKind = (s: string) => (s === "pending" ? "amber" : s === "accepted" ? "green" : "gray") as "amber" | "green" | "gray";
function Invites({ me }: { me: Me }) {
  const qc = useQueryClient(), ui = useUI();
  const list = useQuery({ queryKey: ["invites", me.org?.id], queryFn: () => api<Invite[]>("/invites") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["invites"] });
  const create = useMutation({ mutationFn: (d: FormData) => api<Invite>("/invites", "POST", Object.fromEntries(d)),
    onSuccess: (i) => { refresh(); i.email_sent === false ? ui.toast("Invite created, but the email could not be sent. Use Resend later.", "err") : ui.toast(`Invitation sent to ${i.email}`); } });
  const resend = useMutation({ mutationFn: (id: string) => api(`/invites/${id}/resend`, "POST"), onSuccess: () => { refresh(); ui.toast("Invitation re-sent"); }, onError: (e: Error) => ui.toast(e.message, "err") });
  const revoke = async (i: Invite) => {
    if (await ui.confirm({ title: `Revoke the invite for ${i.email}?`, danger: true, ok: "Revoke invite", body: "The link in the email stops working." }))
      api(`/invites/${i.id}`, "DELETE").then(() => { refresh(); ui.toast("Invite revoked"); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
  return (
    <section className="card"><h2>Invites</h2>
      <form className="inline" onSubmit={form((d, f) => create.mutate(d, { onSuccess: () => f.reset() }))}>
        <div className="field"><label htmlFor="inv-email">Email</label><input id="inv-email" name="email" type="email" required placeholder="name@company.com" /></div>
        <div className="field"><label htmlFor="inv-role">Role</label><select id="inv-role" name="role" defaultValue="editor">{ROLES.filter((r) => r !== "owner" || me.org?.role === "owner").map((r) => <option key={r}>{r}</option>)}</select></div>
        <button className="btn primary" disabled={create.isPending}><Icon n="mail" />Send invite</button>
      </form>
      <ErrorBanner e={create.error ?? list.error} />
      {list.isLoading ? <Skeleton rows={3} h={34} /> : !list.data?.length ? <Empty icon="mail" title="No invitations yet">Invite colleagues by email. Roles: viewer reads, editor edits and commits, admin manages the organization.</Empty> : (
        <table className="tbl" data-testid="invites"><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Expires</th><th><span className="sr">Actions</span></th></tr></thead>
          <tbody>{list.data.map((i) => (
            <tr key={i.id}><td>{i.email}</td><td>{i.role}</td><td><Badge kind={statusKind(i.status)}>{i.status}</Badge></td><td className="nowrap">{fmtDate(i.expires_at)}</td>
              <td className="end">{(i.status === "pending" || i.status === "expired") && <>
                <button className="btn sm" onClick={() => resend.mutate(i.id)}>Resend</button><button className="btn sm ghost" onClick={() => revoke(i)}>Revoke</button></>}</td></tr>))}</tbody></table>)}
    </section>
  );
}

function General({ me }: { me: Me }) {
  const qc = useQueryClient(), ui = useUI(), nav = useNavigate();
  const del = async () => {
    if (!(await ui.confirm({ title: `Delete ${me.org!.name}?`, danger: true, ok: "Delete organization", body: "All folders, snippets, documents, history, base documents and members of this organization are deleted permanently." }))) return;
    api("/orgs/current", "DELETE").then(() => { nav("/"); qc.invalidateQueries(); ui.toast("Organization deleted"); }).catch((e: Error) => ui.toast(e.message, "err"));
  };
  return (
    <>
      <section className="card"><h2>Organization</h2><dl className="kv"><dt>Name</dt><dd>{me.org!.name}</dd><dt>Your role</dt><dd><Badge>{me.org!.role}</Badge></dd></dl></section>
      {me.org!.role === "owner" && (
        <section className="card danger-zone"><h2>Danger zone</h2>
          <div className="dz"><div><b>Delete this organization</b><p className="muted">Permanently removes all content and history. This cannot be undone.</p></div><button className="btn danger" onClick={del}><Icon n="trash" />Delete organization</button></div></section>)}
    </>
  );
}

export function Settings({ me }: { me: Me }) {
  const admin = isAdmin(me.org?.role);
  const tabs = [{ to: "/settings/members", label: "Members" }, ...(admin ? [{ to: "/settings/invites", label: "Invites" }] : []), { to: "/settings/base-documents", label: "Base documents" }, { to: "/settings/styles", label: "Styles" }, { to: "/settings/general", label: "General" }];
  return (
    <div className="page">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, { label: "Settings" }]} title={`${me.org!.name} settings`} tabs={tabs} />
      <Routes>
        <Route index element={<Navigate to="members" replace />} />
        <Route path="members" element={<Members me={me} />} />
        <Route path="invites" element={admin ? <Invites me={me} /> : <Navigate to="../members" replace />} />
        <Route path="base-documents" element={<BaseDocs me={me} />} />
        <Route path="styles" element={<Styles me={me} />} />
        <Route path="general" element={<General me={me} />} />
        <Route path="*" element={<Navigate to="members" replace />} />
      </Routes>
    </div>
  );
}
