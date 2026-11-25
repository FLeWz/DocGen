// Edit locks (round 16): the hook an editor uses, the banner + Grant/Deny modal, lock badges and the notification bell.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MutableRefObject, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, isAdmin, Me } from "./api";
import { bannerState, hhmm, hhmmDate, LockInfo, mmss, requestBlock, secondsLeft, skew } from "./lockstate";
import { Icon, Modal, useUI } from "./ui";

export type Kind = "snippets" | "documents";
/** What an editor registers so the lock bar can flush pending edits (before Grant) and copy unsaved content. */
export type EditorHandle = MutableRefObject<{ flush: () => Promise<unknown>; json: () => unknown } | null>;

const HEARTBEAT_EVERY = 5, POLL_MS = 4000; // 5 polls x 4 s = a heartbeat POST every ~20 s; the polls in between only read the state

export function useEditLock(kind: Kind, id: string, enabled: boolean) {
  const qc = useQueryClient();
  const [s, setS] = useState<LockInfo>();
  const [lost, setLost] = useState<"granted" | "transferred" | null>(null);
  const [epoch, setEpoch] = useState(0); // +1 whenever the lock arrives after having been somebody else's: reload the draft
  const was = useRef<boolean>(), lostWhy = useRef<"granted" | null>(null), skewMs = useRef(0), held = useRef(false);
  const path = (p = "") => `/locks/${kind}/${id}${p}`;
  const apply = useCallback((n: LockInfo) => {
    skewMs.current = skew(n.server_time);
    if (was.current === true && !n.held) { setLost(lostWhy.current ?? "transferred"); lostWhy.current = null; qc.invalidateQueries({ queryKey: ["locks"] }); }
    if (was.current === false && n.held) { setLost(null); setEpoch((e) => e + 1); }
    was.current = held.current = n.held;
    setS(n);
    return n;
  }, [qc]);

  useEffect(() => {
    if (!enabled) return;
    let stop = false, t: number, n = 0;
    const tick = async () => {
      try {
        let r = was.current === undefined ? await api<LockInfo>(path(), "POST") : n % HEARTBEAT_EVERY === 0 ? await api<LockInfo>(path("/heartbeat"), "POST") : await api<LockInfo>(path());
        if (!r.held && !r.holder && !stop) r = await api<LockInfo>(path(), "POST"); // free again (our lock lapsed, or the holder left): take it
        if (!stop) apply(r);
      } catch { /* offline: the next tick tries again */ }
      n++;
      if (!stop) t = window.setTimeout(tick, was.current && n % HEARTBEAT_EVERY === 0 ? 0 : POLL_MS);
    };
    const release = () => { if (held.current) fetch(`/api${path("/release")}`, { method: "POST", keepalive: true, headers: { "X-CSRF-Token": document.cookie.split("; ").find((c) => c.startsWith("docgen_csrf="))?.slice(12) ?? "" } }); };
    const vis = () => { if (!document.hidden) { window.clearTimeout(t); n = 0; tick(); } };
    tick();
    window.addEventListener("pagehide", release);
    document.addEventListener("visibilitychange", vis);
    return () => { stop = true; window.clearTimeout(t); window.removeEventListener("pagehide", release); document.removeEventListener("visibilitychange", vis); release(); was.current = undefined; held.current = false; };
  }, [kind, id, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  const ui = useUI();
  const refresh = () => api<LockInfo>(path()).then(apply).catch(() => {});
  return {
    s, lost, epoch, skewMs, enabled,
    held: !enabled || !!s?.held, // viewers never lock and are read-only anyway
    markLost: () => { if (held.current) { setLost("transferred"); was.current = held.current = false; refresh(); } }, // a save answered 409 locked
    request: () => api<LockInfo>(path("/request"), "POST").then(apply).catch((e: Error) => { ui.toast(e.message, "err"); refresh(); }),
    takeover: () => api<LockInfo>(path("/takeover"), "POST").then(apply).catch((e: Error) => ui.toast(e.message, "err")),
    answer: async (grant: boolean, flush?: () => Promise<unknown>) => {
      const r = s?.incoming; if (!r) return;
      if (grant) { await flush?.(); lostWhy.current = "granted"; } // the holder's last edits reach the shared draft before the rights move
      try { apply(await api<LockInfo>(`/lock-requests/${r.id}/${grant ? "grant" : "deny"}`, "POST")); } catch (e) { lostWhy.current = null; ui.toast((e as Error).message, "err"); refresh(); }
    },
  };
}
export type EditLock = ReturnType<typeof useEditLock>;

function useTick(on: boolean) { const [, set] = useState(0); useEffect(() => { if (!on) return; const t = setInterval(() => set((x) => x + 1), 1000); return () => clearInterval(t); }, [on]); }

/** Banner above the editor: editing / read-only because locked by X / request pending (countdown) / denied / transferred,
 *  plus the holder's Grant/Deny modal with its 5-minute countdown. */
export function LockBar({ lock, me, handle }: { lock: EditLock; me: Me; handle: EditorHandle }) {
  const ui = useUI(), s = lock.s;
  const st = bannerState(s, lock.lost);
  useTick(st === "pending" || !!s?.incoming);
  const [dismissed, setDismissed] = useState<string>();
  if (!lock.enabled || !s) return null;
  const left = (iso: string) => secondsLeft(iso, lock.skewMs.current);
  const block = requestBlock(s, lock.skewMs.current);
  const who = s.holder?.name ?? "someone";
  const copy = async () => {
    const text = JSON.stringify(handle.current?.json() ?? {}, null, 2);
    try { await navigator.clipboard.writeText(text); ui.toast("Unsaved content copied as JSON"); } catch {
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "application/json" })); a.download = "my-unsaved-changes.json"; a.click();
    }
  };
  const take = async () => { if (await ui.confirm({ title: `Take over from ${who}?`, danger: true, ok: "Take over", body: `${who} loses edit rights immediately; their saved draft stays and they are told.` })) lock.takeover(); };
  const msg = {
    loading: "", editing: "",
    readonly: <><Icon n="lock" size={14} /> <b>{who}</b> is editing since {s.holder ? hhmm(s.holder.since) : ""} — read-only for you.</>,
    pending: <><Icon n="clock" size={14} /> Edit rights requested from <b>{who}</b>. Answer expected within <b data-testid="lock-countdown">{mmss(left(s.my_request!.expires_at))}</b>; without one the rights move to you.</>,
    denied: <><Icon n="alert" size={14} /> <b>{who}</b> denied your request. {block}</>,
    transferred: <><Icon n="alert" size={14} /> Edit rights were transferred to <b>{who}</b>; your last changes were saved to the draft. This editor is read-only now.</>,
    granted: <><Icon n="check" size={14} /> Edit rights given to <b>{who}</b>. This editor is read-only now.</>,
  }[st];
  return (
    <>
      {st !== "editing" && st !== "loading" && (
        <div className={`lockbar ${st}`} role="status" data-testid="lock-bar" data-state={st}>
          <span>{msg}</span>
          {(st === "readonly" || st === "denied") && <button className="btn sm" data-testid="request-edit" disabled={!!block} title={block ?? undefined} onClick={lock.request}>Request edit rights</button>}
          {(st === "readonly" || st === "denied") && block && st !== "denied" && <small className="muted" data-testid="request-hint">{block}</small>}
          {st !== "pending" && isAdmin(me.org?.role) && s.holder && <button className="btn sm danger" data-testid="take-over" onClick={take}>Take over</button>}
          {(st === "transferred" || st === "granted") && <button className="btn sm" data-testid="copy-unsaved" onClick={copy}>Copy my unsaved changes</button>}
          {(st === "transferred" || st === "granted") && block !== null && <small className="muted">{block}</small>}
        </div>
      )}
      {s.held && s.incoming && dismissed !== s.incoming.id && (
        <Modal title="Edit rights requested" size="sm" onClose={() => setDismissed(s.incoming!.id)} actions={<>
          <button className="btn" data-testid="lock-deny" onClick={() => lock.answer(false)}>Deny</button>
          <button className="btn primary" data-testid="lock-grant" onClick={() => lock.answer(true, handle.current?.flush)}>Grant</button></>}>
          <div data-testid="lock-request-modal">
            <p><b>{s.incoming.requester?.name}</b> would like to edit this file.</p>
            <p>Grant saves your changes and hands the rights over now. Without an answer in <b data-testid="lock-incoming-countdown">{mmss(left(s.incoming.expires_at))}</b> your changes are saved and the rights go to them automatically.</p>
          </div>
        </Modal>)}
    </>
  );
}

export type LockRow = { entity_type: "snippet" | "document"; entity_id: string; folder_id: string; holder: { id: string; name: string; since: string } };
/** Active locks of the organization (badges in lists and the folder tree). */
export const useLocks = (me: Me) => useQuery({ queryKey: ["locks", me.org?.id], queryFn: () => api<LockRow[]>("/locks"), refetchInterval: 15_000, enabled: !!me.org });
export const LockBadge = ({ l }: { l?: LockRow }) => l ? <span className="badge amber" data-testid="lock-badge" title={`${l.holder.name} is editing since ${hhmmDate(l.holder.since)}`}><Icon n="lock" size={11} />{l.holder.name}</span> : null;

type Note = { id: string; kind: string; read: boolean; created_at: string; payload: { title?: string; link?: string; requester?: { name: string }; holder?: { name: string }; by?: string; kind?: string } };
const TEXT: Record<string, (p: Note["payload"]) => string> = {
  lock_request: (p) => `${p.requester?.name} asks for edit rights on “${p.title}”`,
  lock_granted: (p) => `${p.holder?.name} gave you edit rights on “${p.title}”`,
  lock_denied: (p) => `${p.holder?.name} denied your request for “${p.title}”`,
  lock_timeout: (p) => p.requester ? `Edit rights on “${p.title}” moved to ${p.requester.name} (no answer in time)` : "",
  lock_taken: (p) => `${p.by} took over “${p.title}”`,
};

/** Top-bar bell with the unread count; polls every 10 s. */
export function Bell({ me }: { me: Me }) {
  const qc = useQueryClient(), [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["notifications", me.org?.id], queryFn: () => api<{ unread: number; items: Note[] }>("/notifications"), refetchInterval: 10_000, enabled: !!me.org });
  if (!me.org) return null;
  const toggle = () => { const o = !open; setOpen(o); if (!o && q.data?.unread) api("/notifications/read", "POST").then(() => qc.invalidateQueries({ queryKey: ["notifications"] })); };
  return (
    <div className="bellwrap">
      <button className="btn ghost icon" data-testid="bell" aria-label={`Notifications${q.data?.unread ? `, ${q.data.unread} unread` : ""}`} aria-expanded={open} onClick={toggle}>
        <Icon n="bell" />{!!q.data?.unread && <span className="bellcount" data-testid="bell-count">{q.data.unread}</span>}</button>
      {open && <div className="bellpanel" role="dialog" aria-label="Notifications" data-testid="bell-panel">
        {!q.data?.items.length ? <p className="muted small">No notifications.</p> : <ul>{q.data.items.filter((n) => TEXT[n.kind]?.(n.payload)).map((n) => (
          <li key={n.id} className={n.read ? "" : "unread"}><Link to={n.payload.link ?? "/"} onClick={() => setOpen(false)}>{TEXT[n.kind](n.payload)}</Link><small>{hhmm(n.created_at)}</small></li>))}</ul>}
      </div>}
    </div>
  );
}
