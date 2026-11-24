// Small UI kit: icons, modal dialogs (replace prompt/confirm/alert), toasts, menus, badges, states. Styles: app.css.
import { createContext, FormEvent, ReactNode, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";

const P: Record<string, string> = {
  folder: "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  file: "M6 3h8l4 4v14H6zM14 3v4h4",
  doc: "M5 4h14v16H5zM8 9h8M8 13h8M8 17h5",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  plus: "M12 5v14M5 12h14", x: "M6 6l12 12M18 6L6 18", check: "M5 12l5 5 9-10",
  chevR: "M9 6l6 6-6 6", chevD: "M6 9l6 6 6-6", more: "M5 12h.01M12 12h.01M19 12h.01",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4", home: "M4 11l8-7 8 7v9h-5v-6H9v6H4z",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 4-6 8-6s8 2 8 6", users: "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20c0-3 3-5 6-5s6 2 6 5M16 5a3 3 0 0 1 0 6M18 15c2 .5 3 2 3 5",
  bold: "M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z", italic: "M10 5h8M6 19h8M14 5l-4 14", underline: "M7 4v7a5 5 0 0 0 10 0V4M5 20h14",
  ul: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01", ol: "M10 6h10M10 12h10M10 18h10M4 5l1-1v4M4 14h2l-2 3h2",
  left: "M4 6h16M4 10h10M4 14h16M4 18h10", center: "M4 6h16M7 10h10M4 14h16M7 18h10", right: "M4 6h16M10 10h10M4 14h16M10 18h10", justify: "M4 6h16M4 10h16M4 14h16M4 18h16",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  image: "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M9 9h.01", table: "M4 5h16v14H4zM4 10h16M4 15h16M10 5v14M15 5v14",
  var: "M8 4C5 4 5 6 5 8s0 3-2 4c2 1 2 2 2 4s0 4 3 4M16 4c3 0 3 2 3 4s0 3 2 4c-2 1-2 2-2 4s0 4-3 4",
  rowAdd: "M4 5h16v8H4zM12 5v8M4 9h16M12 16v5M9.5 18.5h5", colAdd: "M5 4h8v16H5zM5 12h8M9 4v16M16 12h5M18.5 9.5v5",
  rowDel: "M4 5h16v8H4zM4 9h16M8 17h8", colDel: "M5 4h8v16H5zM9 4v16M16 12h5", merge: "M4 6h16v12H4zM12 6v4M12 14v4M9 12h6", split: "M4 6h16v12H4zM12 6v12M9 12h-2M17 12h-2",
  trash: "M5 7h14M10 4h4M7 7l1 13h8l1-13", edit: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4", move: "M5 12h14M15 8l4 4-4 4M9 8l-4 4 4 4",
  clock: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 8v4l3 2", commit: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM3 12h5M16 12h5",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z", panel: "M4 5h16v14H4zM15 5v14",
  menu: "M4 6h16M4 12h16M4 18h16", grip: "M9 5h.01M15 5h.01M9 12h.01M15 12h.01M9 19h.01M15 19h.01", mail: "M4 6h16v12H4zM4 7l8 6 8-6",
  alert: "M12 4l9 16H3zM12 10v4M12 17h.01", info: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 11v5M12 8h.01", logout: "M10 4H5v16h5M15 8l4 4-4 4M9 12h10",
  refresh: "M20 5v5h-5M4 19v-5h5M18 10a7 7 0 0 0-12-2M6 14a7 7 0 0 0 12 2", tag: "M4 4h8l8 8-8 8-8-8zM8 8h.01", pdf: "M6 3h8l4 4v14H6zM9 15h6M9 18h4",
  layers: "M12 4l9 5-9 5-9-5zM3 14l9 5 9-5", sidebar: "M4 5h16v14H4zM9 5v14",
  undo: "M9 7H4v5M4 12a8 8 0 1 1 3 6", redo: "M15 7h5v5M20 12a8 8 0 1 0-3 6", strike: "M5 12h14M8 7c0-2 2-3 4-3s4 1 4 3M8 17c0 2 2 3 4 3s4-1 4-3", sup: "M4 19l7-9M11 19L4 10M16 8l3-4M19 8h-4",
  sub: "M4 14l7-9M11 14L4 5M16 20l3-4M19 20h-4", clear: "M5 19l6-14h2M9 14h6M16 17l4 4M20 17l-4 4", restart: "M4 12a8 8 0 1 0 3-6M4 4v4h4M11 9v6", continue: "M4 12h12M12 7l5 5-5 5M19 6v12",
  hash: "M8 4l-2 16M16 4l-2 16M4 9h16M3 15h16", outdent: "M4 6h16M10 10h10M10 14h10M4 18h16M7 9l-3 3 3 3", indent: "M4 6h16M10 10h10M10 14h10M4 18h16M4 9l3 3-3 3",
  quote: "M6 17c-1-3-1-6 2-9M13 17c-1-3-1-6 2-9M5 17h4v-4H5zM12 17h4v-4h-4z", code: "M9 7l-5 5 5 5M15 7l5 5-5 5M13 5l-2 14", codeInline: "M8 8l-4 4 4 4M16 8l4 4-4 4",
  hr: "M4 12h16M7 8h10M7 16h10", omega: "M5 19h4v-2a6 6 0 1 1 6 0v2h4", nbsp: "M5 15v4h14v-4", br: "M5 6h10a4 4 0 0 1 0 8H8M11 11l-3 3 3 3", pagebreak: "M4 12h4M10 12h4M16 12h4M6 4h12v5M6 20h12v-5",
  sectbreak: "M4 12h16M6 5h12v4M6 19h12v-4M3 12l2-2M3 12l2 2", footnote: "M6 5h9a3 3 0 0 1 0 6H6M10 11v8M9 19h2M18 4v4", toc: "M4 6h16M4 10h16M8 14h12M8 18h12M4 14h.01M4 18h.01", lock: "M7 11h10v8H7zM9 11V8a3 3 0 0 1 6 0v3",
  caption: "M4 5h16v10H4zM8 19h8M4 15l4-4 3 3 3-3 6 6", xref: "M5 5h6v6H5zM13 13h6v6h-6zM11 8h4v4M8 11v4h4", bookmark: "M7 4h10v16l-5-4-5 4z", mark: "M5 5h14v4H5zM5 11h14v8H5zM3 5v14", outline: "M4 6h16M8 10h12M12 14h8M16 18h4",
  spell: "M4 17l4-10 4 10M5.5 13h5M14 14l3 3 5-6", keyboard: "M3 7h18v10H3zM7 11h.01M11 11h.01M15 11h.01M8 14h8", keep: "M7 4v16M17 4v16M7 8h10M7 16h10", chevU: "M6 15l6-6 6 6",
};
export const Icon = ({ n, size = 16 }: { n: keyof typeof P | string; size?: number }) => (
  <svg className="ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={P[n] ?? P.file} /></svg>
);

export const Spinner = () => <span className="spinner" role="status" aria-label="Loading" />;
export const Skeleton = ({ rows = 3, h = 18 }: { rows?: number; h?: number }) => (
  <div className="skel" aria-busy="true" aria-label="Loading">{Array.from({ length: rows }, (_, i) => <div key={i} style={{ height: h, width: `${95 - ((i * 17) % 40)}%` }} />)}</div>
);
export const Empty = ({ icon = "file", title, children }: { icon?: string; title: string; children?: ReactNode }) => (
  <div className="empty"><Icon n={icon} size={28} /><h3>{title}</h3>{children && <p>{children}</p>}</div>
);
export const ErrorBanner = ({ e }: { e: unknown }) => (e ? <div className="banner err" role="alert"><Icon n="alert" />{(e as Error).message ?? String(e)}</div> : null);
export const Banner = ({ kind = "warn", children, ...r }: { kind?: "warn" | "info" | "err" | "ok"; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) =>
  <div className={`banner ${kind}`} role={kind === "err" ? "alert" : "note"} {...r}><Icon n={kind === "info" || kind === "ok" ? "info" : "alert"} /><div>{children}</div></div>;
export type Notice = { id: string; kind?: "warn" | "info" | "err"; text: string; items?: string[] };
/** One compact bar for every notice above the editor: collapsed by default (a single line), expandable, state kept per
 *  session under `bar`. A lone notice without details is just the line (no chevron). */
export function NoticeBar({ notices, bar, testid = "notices" }: { notices: Notice[]; bar: string; testid?: string }) {
  const key = `docgen.notices.${bar}`;
  const [open, setOpen] = useState(() => sessionStorage.getItem(key) === "1");
  if (!notices.length) return null;
  const kind = notices.some((n) => n.kind === "err") ? "err" : notices.some((n) => n.kind !== "info") ? "warn" : "info";
  const count = notices.reduce((a, n) => a + (n.items?.length ?? 0), 0);
  const expandable = count > 0 || notices.length > 1;
  const toggle = () => { sessionStorage.setItem(key, open ? "0" : "1"); setOpen(!open); };
  return (
    <div className={`banner noticebar ${kind}`} role={kind === "err" ? "alert" : "note"} data-testid={testid} data-open={open && expandable}>
      <button type="button" className="noticehead" aria-expanded={expandable ? open : undefined} disabled={!expandable} onClick={toggle}>
        <Icon n={kind === "info" ? "info" : "alert"} />
        <span className="noticetext">{notices.map((n) => n.text).join(" · ")}</span>
        {count > 0 && <span className="badge amber" aria-label={`${count} items`}>{count}</span>}
        {expandable && <Icon n={open ? "chevD" : "chevR"} />}
      </button>
      {open && expandable && <div className="noticebody">{notices.map((n) => <div key={n.id}>{notices.length > 1 && <b>{n.text}</b>}{n.items && <ul>{n.items.map((w) => <li key={w}>{w}</li>)}</ul>}</div>)}</div>}
    </div>
  );
}
export const Badge = ({ kind = "gray", children, ...r }: { kind?: "gray" | "blue" | "green" | "amber" | "red" | "violet"; children: ReactNode } & React.HTMLAttributes<HTMLSpanElement>) =>
  <span className={`badge ${kind}`} {...r}>{children}</span>;

export function Field({ label, children, hint }: { label: string; children: (id: string) => ReactNode; hint?: string }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{children(id)}{hint && <small>{hint}</small>}</div>;
}

// ---- modal: native <dialog> (focus trap, Esc, backdrop, a11y role for free) ----
/** size: sm = small prompt/confirm, md (default) = forms, wide = tables/outlines. One shared width scale, see `dialog.modal` in app.css. */
export function Modal({ title, onClose, children, wide, size, actions }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; size?: "sm" | "md" | "wide"; actions?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const d = ref.current; if (d && !d.open) d.showModal(); }, []);
  const tid = useId();
  return (
    <dialog ref={ref} className={`modal ${size ?? (wide ? "wide" : "md")}`} aria-labelledby={tid} onCancel={(e) => { e.preventDefault(); onClose(); }}
      onMouseDown={(e) => { if (e.target === ref.current) onClose(); }}>
      <div className="modal-h"><h2 id={tid}>{title}</h2><button type="button" className="btn ghost icon" aria-label="Close dialog" onClick={onClose}><Icon n="x" /></button></div>
      <div className="modal-b">{children}</div>
      {actions && <div className="modal-f">{actions}</div>}
    </dialog>
  );
}

type Ask = {
  prompt: (o: { title: string; label: string; value?: string; ok?: string; hint?: string }) => Promise<string | null>;
  confirm: (o: { title: string; body?: ReactNode; ok?: string; danger?: boolean }) => Promise<boolean>;
  toast: (msg: string, kind?: "ok" | "err") => void;
};
const Ctx = createContext<Ask>(null as unknown as Ask);
export const useUI = () => useContext(Ctx);

type Pending = { kind: "prompt"; o: Parameters<Ask["prompt"]>[0]; done: (v: string | null) => void } | { kind: "confirm"; o: Parameters<Ask["confirm"]>[0]; done: (v: boolean) => void };

export function UIProvider({ children }: { children: ReactNode }) {
  const [dlg, setDlg] = useState<Pending | null>(null);
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: string }[]>([]);
  const toast = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "err" ? 8000 : 4000);
  }, []);
  const api = useMemo<Ask>(() => ({
    toast,
    prompt: (o) => new Promise((done) => setDlg({ kind: "prompt", o, done })),
    confirm: (o) => new Promise((done) => setDlg({ kind: "confirm", o, done })),
  }), [toast]);
  const close = (v: string | boolean | null) => { (dlg!.done as (v: unknown) => void)(v); setDlg(null); };
  return (
    <Ctx.Provider value={api}>
      {children}
      {dlg?.kind === "prompt" && <PromptDialog o={dlg.o} onDone={close} />}
      {dlg?.kind === "confirm" && (
        <Modal size="sm" title={dlg.o.title} onClose={() => close(false)} actions={<>
          <button className="btn" onClick={() => close(false)}>Cancel</button>
          <button className={`btn ${dlg.o.danger ? "danger" : "primary"}`} autoFocus onClick={() => close(true)}>{dlg.o.ok ?? "Confirm"}</button></>}>
          <div>{dlg.o.body}</div>
        </Modal>)}
      <div className="toasts" aria-live="polite">{toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "err" ? "alert" : "status"}><Icon n={t.kind === "err" ? "alert" : "check"} />{t.msg}
          <button className="btn ghost icon" aria-label="Dismiss" onClick={() => setToasts((l) => l.filter((x) => x.id !== t.id))}><Icon n="x" size={14} /></button></div>))}</div>
    </Ctx.Provider>
  );
}

function PromptDialog({ o, onDone }: { o: Parameters<Ask["prompt"]>[0]; onDone: (v: string | null) => void }) {
  const [v, setV] = useState(o.value ?? "");
  const submit = (e: FormEvent) => { e.preventDefault(); onDone(v.trim() || null); };
  return (
    <Modal size="sm" title={o.title} onClose={() => onDone(null)} actions={<>
      <button className="btn" type="button" onClick={() => onDone(null)}>Cancel</button>
      <button className="btn primary" form="prompt-form" disabled={!v.trim()}>{o.ok ?? "OK"}</button></>}>
      <form id="prompt-form" onSubmit={submit}>
        <Field label={o.label} hint={o.hint}>{(id) => <input id={id} value={v} onChange={(e) => setV(e.target.value)} autoFocus onFocus={(e) => e.target.select()} />}</Field>
      </form>
    </Modal>
  );
}

// ---- menus ----
export type MenuItem = { label: string; icon?: string; onClick: () => void; danger?: boolean; disabled?: boolean } | "sep";
export function MenuPopup({ x, y, items, onClose }: { x: number; y: number; items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  useEffect(() => {
    const r = ref.current!; r.querySelector<HTMLElement>("button:not(:disabled)")?.focus();
    setPos({ x: Math.min(x, innerWidth - r.offsetWidth - 8), y: Math.min(y, innerHeight - r.offsetHeight - 8) });
    const away = (e: Event) => { if (!r.contains(e.target as Node)) onClose(); };
    document.addEventListener("mousedown", away); window.addEventListener("blur", onClose); window.addEventListener("resize", onClose);
    return () => { document.removeEventListener("mousedown", away); window.removeEventListener("blur", onClose); window.removeEventListener("resize", onClose); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const key = (e: React.KeyboardEvent) => {
    const bs = [...ref.current!.querySelectorAll<HTMLElement>("button:not(:disabled)")], i = bs.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); onClose(); } else if (e.key === "ArrowDown") { e.preventDefault(); bs[(i + 1) % bs.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length]?.focus(); } else if (e.key === "Tab") onClose();
  };
  return (
    <div ref={ref} className="menu" role="menu" style={{ left: pos.x, top: pos.y }} onKeyDown={key}>
      {items.map((it, i) => it === "sep" ? <hr key={i} /> :
        <button key={i} role="menuitem" disabled={it.disabled} className={it.danger ? "danger" : ""} onClick={() => { onClose(); it.onClick(); }}>{it.icon && <Icon n={it.icon} />}{it.label}</button>)}
    </div>
  );
}

/** Button that opens a menu under itself. */
export function MenuButton({ label, children, items, className = "btn", align = "left" }: { label: string; children: ReactNode; items: MenuItem[]; className?: string; align?: "left" | "right" }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLButtonElement>(null);
  const open = () => { const r = ref.current!.getBoundingClientRect(); setAt({ x: align === "right" ? r.right - 220 : r.left, y: r.bottom + 4 }); };
  return (
    <>
      <button ref={ref} type="button" className={className} aria-haspopup="menu" aria-expanded={!!at} aria-label={label} onClick={open}>{children}</button>
      {at && <MenuPopup {...at} items={items} onClose={() => { setAt(null); ref.current?.focus(); }} />}
    </>
  );
}

export const useDebounced = <T,>(v: T, ms = 250) => {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
};

export const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");
export const ago = (s: string) => {
  const m = Math.round((Date.now() - new Date(s).getTime()) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};
