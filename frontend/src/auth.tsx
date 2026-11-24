// Screens outside the shell: login and accept-invite.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, ReactNode, useState } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, Lookup, Me } from "./api";
import { ErrorBanner, Field, Skeleton } from "./ui";

const form = (fn: (d: FormData) => void) => (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); fn(new FormData(e.currentTarget)); };

const Card = ({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) => (
  <div className="authwrap"><main className="authcard"><div className="brand big"><span className="logo">D</span>DocGen</div><h1>{title}</h1>{sub && <p className="muted">{sub}</p>}{children}</main></div>
);

function LoginForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ["auth-config"], queryFn: () => api<{ allow_signup: boolean }>("/auth/me/config") });
  const [signup, setSignup] = useState(false);
  const up = signup && !!cfg.data?.allow_signup;
  const m = useMutation({ mutationFn: (d: FormData) => api(up ? "/auth/signup" : "/auth/login", "POST", Object.fromEntries(d)), onSuccess: async () => { await qc.invalidateQueries(); onDone(); } });
  return (
    <form onSubmit={form((d) => m.mutate(d))} className="stack">
      {up && <Field label="Your name">{(id) => <input id={id} name="name" autoComplete="name" required />}</Field>}
      {up && <Field label="Organization name">{(id) => <input id={id} name="org_name" required />}</Field>}
      <Field label="Email">{(id) => <input id={id} name="email" type="email" autoComplete="username" required autoFocus />}</Field>
      <Field label="Password" hint={up ? "At least 10 characters" : undefined}>{(id) => <input id={id} name="password" type="password" autoComplete={up ? "new-password" : "current-password"} minLength={10} required />}</Field>
      <ErrorBanner e={m.error} />
      <button className="btn primary block" disabled={m.isPending}>{m.isPending ? "Please wait…" : up ? "Create account" : "Log in"}</button>
      {cfg.data?.allow_signup
        ? <button type="button" className="btn ghost block" onClick={() => setSignup(!signup)}>{up ? "I have an account" : "Need an account?"}</button>
        : <p className="muted small center">Accounts are by invitation only.</p>}
    </form>
  );
}

export function Login({ me }: { me?: Me }) {
  const nav = useNavigate(), from = (useLocation().state as { from?: string } | null)?.from ?? "/";
  if (me) return <Navigate to={from} replace />;
  return <Card title="Log in to DocGen" sub="Versioned snippets, documents and DOCX export."><LoginForm onDone={() => nav(from, { replace: true })} /></Card>;
}

export function AcceptInvite({ me }: { me?: Me }) {
  const qc = useQueryClient(), nav = useNavigate();
  const token = useSearchParams()[0].get("token") ?? "";
  const lk = useQuery({ queryKey: ["invite", token], queryFn: () => api<Lookup>("/invites/lookup", "POST", { token }), retry: false });
  const accept = useMutation({ mutationFn: (d?: FormData) => api("/invites/accept", "POST", { token, ...(d ? Object.fromEntries(d) : {}) }), onSuccess: async () => { await qc.invalidateQueries(); nav("/", { replace: true }); } });
  const logout = useMutation({ mutationFn: () => api("/auth/logout", "POST"), onSuccess: () => qc.invalidateQueries() });
  if (lk.isLoading) return <Card title="Checking your invitation…"><Skeleton rows={3} /></Card>;
  if (lk.error) return <Card title="Invitation not available"><ErrorBanner e={(lk.error as ApiError).status === 404 ? new Error("This invite link is invalid.") : lk.error} /></Card>;
  const i = lk.data!;
  if (i.status !== "pending") return <Card title="Invitation not available"><ErrorBanner e={new Error(`This invite is ${i.status}. Ask an admin of ${i.org_name} for a new one.`)} /></Card>;
  return (
    <Card title={`Join ${i.org_name}`} sub={`Invitation for ${i.email} as ${i.role}.`}>
      {me && me.user.email === i.email && <button className="btn primary block" disabled={accept.isPending} onClick={() => accept.mutate(undefined)}>Join {i.org_name}</button>}
      {me && me.user.email !== i.email && <div className="stack"><p>You are logged in as <b>{me.user.email}</b>, but this invitation is for <b>{i.email}</b>.</p><button className="btn block" onClick={() => logout.mutate()}>Log out</button></div>}
      {!me && i.user_exists && <div className="stack"><p className="muted">You already have an account. Log in to accept.</p><LoginForm onDone={() => {}} /></div>}
      {!me && !i.user_exists && (
        <form className="stack" onSubmit={form((d) => accept.mutate(d))}>
          <Field label="Your name">{(id) => <input id={id} name="name" autoComplete="name" required autoFocus />}</Field>
          <Field label="Password" hint="At least 10 characters">{(id) => <input id={id} name="password" type="password" autoComplete="new-password" minLength={10} required />}</Field>
          <button className="btn primary block" disabled={accept.isPending}>Create account &amp; join</button>
        </form>)}
      <ErrorBanner e={accept.error} />
    </Card>
  );
}
