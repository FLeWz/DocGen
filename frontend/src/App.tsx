import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { BaseDocs } from "./BaseDocs";
import { Imports } from "./Import";
import { Styles } from "./Styles";
import { Workspace } from "./Workspace";
import { api, ApiError, Invite, isAdmin, Lookup, Me, Member, Org, ROLES, Role } from "./api";

const form = (fn: (d: FormData) => void) => (e: FormEvent<HTMLFormElement>) => {
  e.preventDefault();
  fn(new FormData(e.currentTarget));
};
const Err = ({ e }: { e: unknown }) => (e ? <p role="alert" style={{ color: "crimson" }}>{(e as Error).message}</p> : null);

function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
}

function AuthForm({ onDone }: { onDone?: () => void }) {
  const qc = useQueryClient();
  const [wantSignup, setSignup] = useState(false);
  const cfg = useQuery({ queryKey: ["auth-config"], queryFn: () => api<{ allow_signup: boolean }>("/auth/me/config") });
  const signup = wantSignup && !!cfg.data?.allow_signup;
  const m = useMutation({
    mutationFn: (d: FormData) =>
      api(signup ? "/auth/signup" : "/auth/login", "POST", Object.fromEntries(d)),
    onSuccess: async () => { await qc.invalidateQueries(); onDone?.(); },
  });
  return (
    <form onSubmit={form((d) => m.mutate(d))}>
      <h2>{signup ? "Sign up" : "Log in"}</h2>
      {signup && <p><input name="name" placeholder="Your name" required /></p>}
      {signup && <p><input name="org_name" placeholder="Organization name" required /></p>}
      <p><input name="email" type="email" placeholder="Email" required /></p>
      <p><input name="password" type="password" placeholder="Password (min 10)" minLength={10} required /></p>
      <button>{signup ? "Create account" : "Log in"}</button>{" "}
      {cfg.data?.allow_signup && (
        <button type="button" onClick={() => setSignup(!signup)}>{signup ? "I have an account" : "Need an account?"}</button>
      )}
      {!cfg.data?.allow_signup && <p><small>Accounts are by invitation only.</small></p>}
      <Err e={m.error} />
    </form>
  );
}

function Members({ me }: { me: Me }) {
  const qc = useQueryClient();
  const admin = isAdmin(me.org?.role);
  const members = useQuery({ queryKey: ["members", me.org?.id], queryFn: () => api<Member[]>("/members") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["members"] });
  const setRole = useMutation({ mutationFn: (v: [string, Role]) => api(`/members/${v[0]}`, "PATCH", { role: v[1] }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => api(`/members/${id}`, "DELETE"), onSuccess: refresh });
  return (
    <section>
      <h2>Members</h2>
      <table><tbody>
        {members.data?.map((m) => (
          <tr key={m.user_id}>
            <td>{m.name}</td><td>{m.email}</td>
            <td>{admin ? (
              <select value={m.role} onChange={(e) => setRole.mutate([m.user_id, e.target.value as Role])}>
                {ROLES.map((r) => <option key={r}>{r}</option>)}
              </select>) : m.role}</td>
            <td>{(admin || m.user_id === me.user.id) && <button onClick={() => remove.mutate(m.user_id)}>{m.user_id === me.user.id ? "Leave" : "Remove"}</button>}</td>
          </tr>
        ))}
      </tbody></table>
      <Err e={setRole.error ?? remove.error} />
    </section>
  );
}

function Invites({ me }: { me: Me }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["invites", me.org?.id], queryFn: () => api<Invite[]>("/invites") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["invites"] });
  const create = useMutation({ mutationFn: (d: FormData) => api<Invite>("/invites", "POST", Object.fromEntries(d)), onSuccess: refresh });
  const resend = useMutation({ mutationFn: (id: string) => api(`/invites/${id}/resend`, "POST"), onSuccess: refresh });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/invites/${id}`, "DELETE"), onSuccess: refresh });
  return (
    <section>
      <h2>Invites</h2>
      <form onSubmit={form((d) => create.mutate(d))}>
        <input name="email" type="email" placeholder="Email" required />{" "}
        <select name="role" defaultValue="editor">{ROLES.filter((r) => r !== "owner" || me.org?.role === "owner").map((r) => <option key={r}>{r}</option>)}</select>{" "}
        <button>Send invite</button>
      </form>
      <Err e={create.error ?? resend.error ?? revoke.error} />
      <ul>
        {list.data?.map((i) => (
          <li key={i.id}>{i.email} ({i.role}) – {i.status}{" "}
            {(i.status === "pending" || i.status === "expired") && <>
              <button onClick={() => resend.mutate(i.id)}>Resend</button>{" "}
              <button onClick={() => revoke.mutate(i.id)}>Revoke</button></>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Dashboard({ me }: { me: Me }) {
  const qc = useQueryClient();
  const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => api<Org[]>("/orgs") });
  const reload = () => qc.invalidateQueries();
  const switchOrg = useMutation({ mutationFn: (id: string) => api("/orgs/switch", "POST", { org_id: id }), onSuccess: reload });
  const createOrg = useMutation({ mutationFn: (d: FormData) => api("/orgs", "POST", Object.fromEntries(d)), onSuccess: reload });
  const delOrg = useMutation({ mutationFn: () => api("/orgs/current", "DELETE"), onSuccess: reload });
  const logout = useMutation({ mutationFn: () => api("/auth/logout", "POST"), onSuccess: reload });
  return (
    <main>
      <header>
        <b>DocGen</b> · {me.user.name} ·{" "}
        <select aria-label="Organization" value={me.org?.id ?? ""} onChange={(e) => switchOrg.mutate(e.target.value)}>
          {!me.org && <option value="">(no organization)</option>}
          {orgs.data?.map((o) => <option key={o.id} value={o.id}>{o.name} ({o.role})</option>)}
        </select>{" "}
        <button onClick={() => logout.mutate()}>Log out</button>
      </header>
      <form onSubmit={form((d) => createOrg.mutate(d))}>
        <input name="name" placeholder="New organization" required /> <button>Create org</button>
      </form>
      <Err e={switchOrg.error ?? createOrg.error ?? delOrg.error} />
      {me.org && <>
        <h1>{me.org.name}</h1>
        <Workspace me={me} />
        <Imports me={me} />
        <BaseDocs me={me} />
        <Styles me={me} />
        <Members me={me} />
        {isAdmin(me.org.role) && <Invites me={me} />}
        {me.org.role === "owner" && <button onClick={() => confirm("Delete this organization and all its data?") && delOrg.mutate()}>Delete organization</button>}
      </>}
    </main>
  );
}

function AcceptInvite({ me }: { me?: Me }) {
  const qc = useQueryClient();
  const token = new URLSearchParams(location.search).get("token") ?? "";
  const lk = useQuery({ queryKey: ["invite", token], queryFn: () => api<Lookup>("/invites/lookup", "POST", { token }) });
  const accept = useMutation({
    mutationFn: (d?: FormData) => api("/invites/accept", "POST", { token, ...(d ? Object.fromEntries(d) : {}) }),
    onSuccess: () => { location.href = "/"; },
  });
  const logout = useMutation({ mutationFn: () => api("/auth/logout", "POST"), onSuccess: () => qc.invalidateQueries() });
  if (lk.isLoading) return <p>Loading…</p>;
  if (lk.error) return <p>{(lk.error as ApiError).status === 404 ? "This invite link is invalid." : (lk.error as Error).message}</p>;
  const i = lk.data!;
  if (i.status !== "pending") return <p>This invite is {i.status}.</p>;
  return (
    <main>
      <h1>Join {i.org_name}</h1>
      <p>Invitation for {i.email} as {i.role}.</p>
      {me && me.user.email === i.email && <button onClick={() => accept.mutate(undefined)}>Join {i.org_name}</button>}
      {me && me.user.email !== i.email && <p>You are logged in as {me.user.email}. <button onClick={() => logout.mutate()}>Log out</button></p>}
      {!me && i.user_exists && <><p>Log in to accept.</p><AuthForm /></>}
      {!me && !i.user_exists && (
        <form onSubmit={form((d) => accept.mutate(d))}>
          <p><input name="name" placeholder="Your name" required /></p>
          <p><input name="password" type="password" placeholder="Password (min 10)" minLength={10} required /></p>
          <button>Create account & join</button>
        </form>
      )}
      <Err e={accept.error} />
    </main>
  );
}

export function App() {
  const me = useMe();
  if (me.isLoading) return <p>Loading…</p>;
  if (location.pathname === "/accept-invite") return <AcceptInvite me={me.data} />;
  return me.data ? <Dashboard me={me.data} /> : <AuthForm />;
}
