// App shell: top bar (product, org switcher, search, user menu), sidebar (folder tree), main, collapsible right context panel.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, Folder, isAdmin, Me, Org } from "./api";
import { canEdit, folderPath, LAST_FOLDER, useFolders, useOverview } from "./data";
import { Icon, MenuButton, MenuItem, MenuPopup, Modal, Skeleton, useDebounced, useUI } from "./ui";

type Shell = { me: Me; active: string | null; setActive: (id: string | null) => void; slot: HTMLElement | null; setHasPanel: (b: boolean, essential?: boolean) => void };
const ShellCtx = createContext<Shell>(null as unknown as Shell);
export const useShell = () => useContext(ShellCtx);

/** Pages call this with the folder they live in: highlights it in the tree and remembers it as the last open folder. */
export function useActiveFolder(id?: string | null) {
  const { me, setActive } = useShell();
  useEffect(() => {
    setActive(id ?? null);
    if (id && me.org) localStorage.setItem(LAST_FOLDER(me.org.id), id);
    return () => setActive(null);
  }, [id, me.org, setActive]);
}

/** Right context panel content (rendered into the shell's panel slot). */
export function ContextPanel({ children, essential }: { children: ReactNode; essential?: boolean }) {
  const { slot, setHasPanel } = useShell();
  useEffect(() => { setHasPanel(true, essential); return () => setHasPanel(false); }, [setHasPanel, essential]);
  return slot ? createPortal(children, slot) : null;
}

export type Crumb = { label: string; to?: string };
export function PageHead({ crumbs, title, actions, tabs, badge }: { crumbs: Crumb[]; title?: ReactNode; actions?: ReactNode; tabs?: { to: string; label: string; end?: boolean }[]; badge?: ReactNode }) {
  return (
    <div className="pagehead">
      <div className="crumbrow">
        <nav aria-label="Breadcrumb" className="crumbs"><ol>{crumbs.map((c, i) => (
          <li key={i}>{c.to && i < crumbs.length - 1 ? <Link to={c.to}>{c.label}</Link> : <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</span>}</li>))}</ol></nav>
        {!title && <div className="actions">{actions}</div>}
      </div>
      {title && <div className="titlerow"><h1>{title}{badge}</h1><div className="actions">{actions}</div></div>}
      {tabs && <div className="tabs" role="tablist">{tabs.map((t) => <NavLink key={t.to} to={t.to} end={t.end} role="tab" className={({ isActive }) => (isActive ? "tab on" : "tab")}>{t.label}</NavLink>)}</div>}
    </div>
  );
}

// ---------- sidebar tree ----------
const kids = (fs: Folder[], p: string | null) => fs.filter((f) => f.parent_id === p).sort((a, b) => a.name.localeCompare(b.name));
const descendants = (fs: Folder[], id: string): Set<string> => { const s = new Set([id]); kids(fs, id).forEach((k) => descendants(fs, k.id).forEach((x) => s.add(x))); return s; };

function MoveDialog({ f, folders, onClose, onMove }: { f: Folder; folders: Folder[]; onClose: () => void; onMove: (parent: string | null) => void }) {
  const bad = descendants(folders, f.id), [to, setTo] = useState(f.parent_id ?? "");
  const opts: { id: string; label: string }[] = [];
  const walk = (p: string | null, d: number) => kids(folders, p).forEach((k) => { if (!bad.has(k.id)) { opts.push({ id: k.id, label: "\u2003".repeat(d) + k.name }); walk(k.id, d + 1); } });
  walk(null, 0);
  return (
    <Modal title={`Move “${f.name}”`} onClose={onClose} actions={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={() => onMove(to || null)}>Move here</button></>}>
      <div className="field"><label htmlFor="mv">New parent folder</label>
        <select id="mv" value={to} onChange={(e) => setTo(e.target.value)} autoFocus><option value="">(top level)</option>{opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select></div>
    </Modal>
  );
}

function Tree({ me }: { me: Me }) {
  const { active } = useShell();
  const ui = useUI(), nav = useNavigate(), qc = useQueryClient();
  const folders = useFolders(me), ov = useOverview(me);
  const key = `docgen.tree.${me.org?.id}`;
  const [open, setOpen] = useState<Set<string>>(() => new Set(JSON.parse(localStorage.getItem(key) ?? "[]")));
  const [menu, setMenu] = useState<{ x: number; y: number; f: Folder } | null>(null);
  const [moving, setMoving] = useState<Folder | null>(null);
  const edit = canEdit(me), admin = isAdmin(me.org?.role);
  const toggle = (id: string, on?: boolean) => setOpen((o) => { const n = new Set(o); if (on ?? !n.has(id)) n.add(id); else n.delete(id); localStorage.setItem(key, JSON.stringify([...n])); return n; });
  useEffect(() => { // reveal the active folder
    const fs = folders.data; if (!fs || !active) return;
    const need = folderPath(fs, active).slice(0, -1).map((f) => f.id).filter((id) => !open.has(id));
    if (need.length) setOpen((o) => new Set([...o, ...need]));
  }, [active, folders.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const done = () => { qc.invalidateQueries({ queryKey: ["folders"] }); qc.invalidateQueries({ queryKey: ["overview"] }); };
  const run = (fn: () => Promise<unknown>, ok?: string) => fn().then(() => { done(); if (ok) ui.toast(ok); }).catch((e: Error) => ui.toast(e.message, "err"));

  const create = async (parent: Folder | null) => {
    const name = await ui.prompt({ title: parent ? `New folder in “${parent.name}”` : "New folder", label: "Folder name", ok: "Create" });
    if (!name) return;
    run(async () => { const f = await api<Folder>("/folders", "POST", { name, parent_id: parent?.id ?? null }); if (parent) toggle(parent.id, true); nav(`/folders/${f.id}`); });
  };
  const rename = async (f: Folder) => {
    const name = await ui.prompt({ title: "Rename folder", label: "Folder name", value: f.name, ok: "Rename" });
    if (name && name !== f.name) run(() => api(`/folders/${f.id}`, "PATCH", { name }), "Folder renamed");
  };
  const del = async (f: Folder) => {
    if (!(await ui.confirm({ title: `Delete “${f.name}”?`, danger: true, ok: "Delete folder", body: "This deletes the folder, its subfolders, every snippet and document inside and ALL their history. It cannot be undone." }))) return;
    run(async () => { await api(`/folders/${f.id}`, "DELETE"); if (active && descendants(folders.data!, f.id).has(active)) nav("/"); }, "Folder deleted");
  };
  const itemsFor = (f: Folder): MenuItem[] => [
    { label: "Open", icon: "folder", onClick: () => nav(`/folders/${f.id}`) },
    ...(edit ? ([{ label: "New subfolder…", icon: "plus", onClick: () => create(f) }, { label: "Rename…", icon: "edit", onClick: () => rename(f) },
      { label: "Move…", icon: "move", onClick: () => setMoving(f) }] as MenuItem[]) : []),
    ...(admin ? (["sep", { label: "Delete…", icon: "trash", danger: true, onClick: () => del(f) }] as MenuItem[]) : []),
  ];

  const node = (f: Folder, depth: number): ReactNode => {
    const ch = kids(folders.data ?? [], f.id), isOpen = open.has(f.id), c = ov.data?.counts[f.id], n = (c?.snippets ?? 0) + (c?.documents ?? 0);
    return (
      <li key={f.id} role="treeitem" aria-expanded={ch.length ? isOpen : undefined} aria-selected={active === f.id}>
        <div className={`node ${active === f.id ? "on" : ""}`} style={{ paddingLeft: 6 + depth * 14 }} onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, f }); }}>
          <button className="chev" tabIndex={-1} aria-label={isOpen ? `Collapse ${f.name}` : `Expand ${f.name}`} style={{ visibility: ch.length ? "visible" : "hidden" }} onClick={() => toggle(f.id)}><Icon n={isOpen ? "chevD" : "chevR"} size={14} /></button>
          <Link to={`/folders/${f.id}`} className="nodelink" data-testid="tree-folder" title={f.name}><Icon n="folder" /><span className="nm">{f.name}</span>{n > 0 && <span className="cnt" title={`${c?.snippets ?? 0} snippets, ${c?.documents ?? 0} documents`}>{n}</span>}</Link>
          <button className="more" aria-label={`Actions for ${f.name}`} onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu({ x: r.left, y: r.bottom + 2, f }); }}><Icon n="more" size={14} /></button>
        </div>
        {ch.length > 0 && isOpen && <ul role="group">{ch.map((k) => node(k, depth + 1))}</ul>}
      </li>
    );
  };
  const roots = kids(folders.data ?? [], null);
  return (
    <>
      <div className="side-h"><span>Folders</span>{edit && <button className="btn ghost icon" aria-label="New folder" title="New folder" onClick={() => create(null)}><Icon n="plus" /></button>}</div>
      {folders.isLoading ? <Skeleton rows={4} /> : roots.length === 0
        ? <p className="side-empty">No folders yet.{edit && <> <button className="link" onClick={() => create(null)}>Create the first one</button>.</>}</p>
        : <ul role="tree" aria-label="Folders" className="tree">{roots.map((f) => node(f, 0))}</ul>}
      {menu && <MenuPopup x={menu.x} y={menu.y} items={itemsFor(menu.f)} onClose={() => setMenu(null)} />}
      {moving && <MoveDialog f={moving} folders={folders.data ?? []} onClose={() => setMoving(null)} onMove={(p) => { run(() => api(`/folders/${moving.id}`, "PATCH", { parent_id: p }), "Folder moved"); setMoving(null); }} />}
    </>
  );
}

// ---------- top bar ----------
function OrgSwitcher({ me }: { me: Me }) {
  const qc = useQueryClient(), nav = useNavigate(), ui = useUI();
  const orgs = useQuery({ queryKey: ["orgs", me.user.id], queryFn: () => api<Org[]>("/orgs") });
  const go = () => { nav("/"); qc.invalidateQueries(); };
  const sw = useMutation({ mutationFn: (id: string) => api("/orgs/switch", "POST", { org_id: id }), onSuccess: go, onError: (e: Error) => ui.toast(e.message, "err") });
  const create = async () => {
    const name = await ui.prompt({ title: "New organization", label: "Organization name", ok: "Create" });
    if (name) api("/orgs", "POST", { name }).then(go).catch((e: Error) => ui.toast(e.message, "err"));
  };
  const items: MenuItem[] = [...(orgs.data ?? []).map((o): MenuItem => ({ label: `${o.name} (${o.role})`, icon: o.id === me.org?.id ? "check" : "layers", onClick: () => o.id !== me.org?.id && sw.mutate(o.id) })), "sep",
    { label: "New organization…", icon: "plus", onClick: create }];
  return <MenuButton label={`Organization: ${me.org?.name ?? "none"}`} className="btn orgbtn" items={items}><Icon n="layers" /><span className="nm">{me.org?.name ?? "No organization"}</span>{me.org && <span className="role">{me.org.role}</span>}<Icon n="chevD" size={14} /></MenuButton>;
}

function Search({ me }: { me: Me }) {
  const nav = useNavigate(), ref = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState(""), [open, setOpen] = useState(false), [cur, setCur] = useState(0);
  const dq = useDebounced(q.trim().toLowerCase(), 120);
  const ov = useOverview(me), folders = useFolders(me);
  const hits = useMemo(() => {
    if (!dq) return [];
    const f = (folders.data ?? []).filter((x) => x.name.toLowerCase().includes(dq)).map((x) => ({ k: `f${x.id}`, icon: "folder", title: x.name, sub: "Folder", to: `/folders/${x.id}` }));
    const i = (ov.data?.items ?? []).filter((x) => (x.title + " " + x.slug).toLowerCase().includes(dq)).map((x) => ({ k: x.id, icon: x.kind === "snippet" ? "file" : "doc", title: x.title,
      sub: `${x.kind === "snippet" ? "Snippet" : "Document"} · ${folders.data?.find((y) => y.id === x.folder_id)?.name ?? ""}`, to: `/${x.kind}s/${x.id}` }));
    return [...f, ...i].slice(0, 8);
  }, [dq, ov.data, folders.data]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement).tagName) && !(e.target as HTMLElement).isContentEditable) { e.preventDefault(); ref.current?.focus(); } };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, []);
  const pick = (to: string) => { setOpen(false); setQ(""); nav(to); };
  return (
    <div className="search" role="search">
      <Icon n="search" />
      <input ref={ref} type="search" placeholder="Search snippets, documents, folders  ( / )" aria-label="Search" role="combobox" aria-expanded={open && !!dq} aria-controls="search-list" aria-activedescendant={hits[cur] ? `sr-${cur}` : undefined}
        value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); setCur(0); }} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setCur((c) => Math.min(c + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setCur((c) => Math.max(c - 1, 0)); }
          else if (e.key === "Enter" && hits[cur]) pick(hits[cur].to);
          else if (e.key === "Escape") { setOpen(false); (e.target as HTMLElement).blur(); }
        }} />
      {open && dq && (
        <ul id="search-list" role="listbox" className="results">
          {hits.length === 0 && <li className="none">No matches for “{q}”</li>}
          {hits.map((h, i) => <li key={h.k} id={`sr-${i}`} role="option" aria-selected={i === cur} className={i === cur ? "on" : ""} onMouseDown={(e) => { e.preventDefault(); pick(h.to); }} onMouseEnter={() => setCur(i)}>
            <Icon n={h.icon} /><span className="t">{h.title}</span><small>{h.sub}</small></li>)}
        </ul>)}
    </div>
  );
}

export function Shell({ me }: { me: Me }) {
  const qc = useQueryClient(), nav = useNavigate();
  const [active, setActive] = useState<string | null>(null);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [has, setHas] = useState(false);
  const wide = innerWidth >= 1360;
  const [rp, setRp] = useState(() => (localStorage.getItem("docgen.rpanel") ?? (wide ? "1" : "0")) === "1");
  const [sb, setSb] = useState(() => localStorage.getItem("docgen.sidebar") !== "0");
  const logout = useMutation({ mutationFn: () => api("/auth/logout", "POST"), onSuccess: () => { nav("/login"); qc.clear(); qc.invalidateQueries(); } });
  const flip = (k: string, v: boolean, set: (b: boolean) => void) => { set(!v); localStorage.setItem(k, v ? "0" : "1"); };
  // essential panels (document builder) open by default unless the user closed them before
  const setHasPanel = useCallback((b: boolean, ess?: boolean) => { setHas(b); if (b && ess && localStorage.getItem("docgen.rpanel") === null) setRp(true); }, []);
  const ctx = useMemo(() => ({ me, active, setActive, slot, setHasPanel }), [me, active, slot, setHasPanel]);
  const initials = me.user.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return (
    <ShellCtx.Provider value={ctx}>
      <a href="#main" className="skip">Skip to content</a>
      <div className="app">
        <header className="topbar">
          <button className="btn ghost icon" aria-label={sb ? "Hide sidebar" : "Show sidebar"} aria-pressed={sb} onClick={() => flip("docgen.sidebar", sb, setSb)}><Icon n="sidebar" /></button>
          <Link to="/" className="brand"><span className="logo">D</span>DocGen</Link>
          <OrgSwitcher me={me} />
          <Search me={me} />
          <div className="spacer" />
          {has && <button className="btn ghost" aria-pressed={rp} onClick={() => flip("docgen.rpanel", rp, setRp)} title="Toggle the context panel"><Icon n="panel" />Details</button>}
          <MenuButton label="User menu" className="btn ghost avatar-btn" align="right" items={[
            { label: `${me.user.name} · ${me.user.email}`, icon: "user", onClick: () => {}, disabled: true }, "sep",
            ...(me.org ? [{ label: "Organization settings", icon: "settings", onClick: () => nav("/settings") }] : []),
            { label: "Log out", icon: "logout", onClick: () => logout.mutate() }]}><span className="avatar">{initials}</span></MenuButton>
        </header>
        <div className={`body ${sb ? "" : "nosb"} ${has && rp ? "withrp" : ""}`}>
          <nav className="sidebar" aria-label="Navigation" hidden={!sb}>
            <div className="navlinks">
              <NavLink to="/" end><Icon n="home" />Dashboard</NavLink>
              <NavLink to="/imports"><Icon n="upload" />Imports</NavLink>
              <NavLink to="/base-documents"><Icon n="layers" />Base documents</NavLink>
              <NavLink to="/styles"><Icon n="edit" />Styles</NavLink>
              <NavLink to="/settings"><Icon n="settings" />Settings</NavLink>
            </div>
            {me.org ? <Tree me={me} /> : null}
          </nav>
          <main id="main" className="main" tabIndex={-1}>{me.org ? <Outlet /> : <NoOrg />}</main>
          <aside className="rpanel" aria-label="Details" hidden={!has || !rp} ref={setSlot} />
        </div>
      </div>
    </ShellCtx.Provider>
  );
}

function NoOrg() {
  return <div className="page"><div className="empty"><Icon n="layers" size={28} /><h3>You have no organization</h3><p>Use the organization menu in the top bar to create one, or ask an admin for an invitation.</p></div></div>;
}
