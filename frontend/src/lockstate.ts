/** Pure helpers for the edit-lock UI (round 16): countdowns, banner state, "you can request again" text. */
import { formatDate } from "./dates";

export type Person = { id: string; name: string; email?: string };
export type LockReq = { id: string; status: "pending" | "granted" | "denied" | "timed_out" | "cancelled"; created_at: string; expires_at: string; requester?: Person; holder?: Person };
export type LockInfo = {
  held: boolean; holder: { id: string; name: string; since: string } | null; incoming: LockReq | null;
  pending: { requester: Person; expires_at: string } | null; my_request: LockReq | null; cooldown_until: string | null; server_time: string;
};
export type Banner = "loading" | "editing" | "readonly" | "pending" | "denied" | "transferred" | "granted";

const p2 = (n: number) => String(n).padStart(2, "0");
/** server clock minus client clock (ms): countdowns use the server's expires_at, whatever the client clock says */
export const skew = (serverTime: string, now = Date.now()) => new Date(serverTime).getTime() - now;
export const secondsLeft = (expires: string, skewMs = 0, now = Date.now()) => Math.max(0, Math.ceil((new Date(expires).getTime() - (now + skewMs)) / 1000));
export const mmss = (s: number) => `${Math.floor(s / 60)}:${p2(s % 60)}`;
/** HH:mm (DD.MM.YYYY), local time */
export const hhmmDate = (iso: string) => { const d = new Date(iso); return `${p2(d.getHours())}:${p2(d.getMinutes())} (${formatDate(d)})`; };
export const hhmm = (iso: string) => { const d = new Date(iso); return `${p2(d.getHours())}:${p2(d.getMinutes())}`; };

export function bannerState(s: LockInfo | undefined, lost: "granted" | "transferred" | null): Banner {
  if (!s) return "loading";
  if (s.held) return "editing";
  if (lost) return lost;
  const r = s.my_request;
  if (r?.status === "pending") return "pending";
  if (r?.status === "denied" && s.cooldown_until) return "denied";
  return "readonly";
}

/** null = the button is enabled, else why it is not */
export function requestBlock(s: LockInfo, skewMs = 0, now = Date.now()): string | null {
  if (s.cooldown_until && new Date(s.cooldown_until).getTime() > now + skewMs) return `You can request again at ${hhmmDate(s.cooldown_until)}`;
  if (s.pending) return `${s.pending.requester.name} has already asked for edit rights`;
  return null;
}
