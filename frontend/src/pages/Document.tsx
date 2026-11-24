import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, isAdmin, BaseDoc, BaseDocVersion, Block, Commit, DocBody, DocFull, ExportStatus, LibraryGroup, Me, RefStatus, RevRow, Style, VarDecl } from "../api";
import { insertAt, listBlock, moveTo, newId, paragraphBlock, snippetBlock, TOC_KINDS } from "../builder/blocks";
import { fit, limits, LIB, RIGHT } from "../builder/layout";
import { Resizer } from "../builder/Resizer";
import { ParagraphEditor } from "../builder/ParagraphEditor";
import { PdfPreview } from "../builder/PdfPreview";
import { RevisionTable, VariablesForm } from "../builder/Variables";
import { folderPath, short, useFolders } from "../data";
import { PMNode } from "../editor/doc";
import { useLook } from "../editor/look";
import { Preview, useStyles } from "../editor/SnippetEditor";
import { ContextPanel, PageHead, useActiveFolder } from "../Shell";
import { Badge, Banner, Empty, ErrorBanner, Icon, Modal, Skeleton, Spinner, useUI } from "../ui";
import { DiffText } from "./DiffView";
import { EditBaseButton } from "./BaseDocs";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const nul = (s: string | null) => (s ? s : null);
const pinKey = (v: { type: string; value: string | null }) => `${v.type}:${v.value ?? ""}`;
const DND_SNIPPET = "application/x-docgen-snippet", DND_BLOCK = "application/x-docgen-block";
const bodyOf = (d: DocBody): DocBody => ({ title: d.title, slug: d.slug, base_document: d.base_document, blocks: d.blocks, variables: d.variables, links: d.links, revision_history: d.revision_history ?? null });
type SnipBlock = Extract<Block, { type: "snippet_ref" }>;

function Library({ onAdd, disabled, onDragging }: { onAdd: (g: LibraryGroup, s: LibraryGroup["snippets"][0]) => void; disabled: boolean; onDragging: (b: boolean) => void }) {
  const [q, setQ] = useState(""), [tag, setTag] = useState("");
  const lib = useQuery({ queryKey: ["library", q, tag], queryFn: () => api<LibraryGroup[]>(`/library/snippets?q=${encodeURIComponent(q)}${tag ? `&tag=${encodeURIComponent(tag)}` : ""}`) });
  return (
    <aside className="library" aria-label="Snippet library">
      <h2>Snippet library</h2>
      <div className="inputicon"><Icon n="search" /><input aria-label="Search snippets" placeholder="Search snippets" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="inputicon"><Icon n="tag" /><input aria-label="Filter by tag" placeholder="Tag, e.g. v1.0" value={tag} onChange={(e) => setTag(e.target.value.trim())} /></div>
      <div className="libscroll">
        {lib.isLoading && <Skeleton rows={4} />}
        {lib.data?.length === 0 && <Empty icon="file" title="No committed snippets">Only committed snippets can be added to documents.</Empty>}
        {lib.data?.map((g) => (
          <div key={g.folder_id} className="libgroup">
            <div className="libfolder"><Icon n="folder" />{g.folder_name}{g.tags.map((t) => <Badge key={t} kind="violet">{t}</Badge>)}</div>
            <ul>{g.snippets.map((s) => (
              <li key={s.id} draggable={!disabled} data-testid="lib-item" title={disabled ? undefined : "Drag into the document"}
                onDragStart={(e) => { e.dataTransfer.setData(DND_SNIPPET, JSON.stringify({ f: g.folder_id, s: s.id, sha: s.head_sha })); e.dataTransfer.effectAllowed = "copy"; onDragging(true); }} onDragEnd={() => onDragging(false)}>
                <Icon n="grip" /><span className="t">{s.title}</span><small>@{short(s.head_sha)}</small>
                {!disabled && <button className="btn sm" aria-label={`Add ${s.title}`} onClick={() => onAdd(g, s)}><Icon n="plus" size={14} />Add</button>}
              </li>))}</ul>
          </div>))}
      </div>
      <ErrorBanner e={lib.error} />
    </aside>
  );
}

function BumpDialog({ docId, block, onApply, onClose }: { docId: string; block: string; onApply: () => void; onClose: () => void }) {
  const d = useQuery({ queryKey: ["bump", docId, block], queryFn: () => api<{ from_sha: string; to_sha: string; text: string; summary: Record<string, number> }>(`/documents/${docId}/blocks/${block}/bump`) });
  return (
    <Modal title="Update snippet version" wide onClose={onClose} actions={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!d.data} onClick={onApply}>Apply bump</button></>}>
      <div data-testid="bump-diff">
        {d.isLoading && <Skeleton rows={4} />}
        {d.data && <><p><code>{short(d.data.from_sha)}</code> → <code>{short(d.data.to_sha)}</code>{" "}<Badge kind="green">+{d.data.summary.added}</Badge> <Badge kind="red">−{d.data.summary.removed}</Badge> <Badge kind="amber">~{d.data.summary.changed}</Badge> <span className="muted small">blocks</span></p>
          <DiffText text={d.data.text} /></>}
        <ErrorBanner e={d.error} />
      </div>
    </Modal>
  );
}

function SnippetCard({ b, st, ro, slots, set, onBump }: { b: SnipBlock; st?: RefStatus; ro: boolean; slots: string[]; set: (n: Block) => void; onBump: () => void }) {
  const hist = useQuery({ queryKey: ["shist", b.snippet_id], queryFn: () => api<Commit[]>(`/snippets/${b.snippet_id}/history`) });
  const cur = pinKey(b.version);
  const opts = [{ v: "latest:", l: "latest (follows every commit)" }, ...(hist.data ?? []).map((c) => ({ v: `commit:${c.sha}`, l: `${short(c.sha)} ${c.tags.map((t) => `[${t}]`).join("")} ${c.message.slice(0, 30)}` })),
    ...(hist.data ?? []).flatMap((c) => c.tags.map((t) => ({ v: `tag:${t}`, l: `tag ${t}` })))];
  if (!opts.some((o) => o.v === cur)) opts.push({ v: cur, l: `${b.version.type} ${short(b.version.value)}` });
  return (
    <div className="bbody">
      <div className="btitle"><Icon n="file" /><span className="loc">{st?.folder_name ?? "?"} /</span> <b>{st?.title ?? <i>missing snippet</i>}</b>
        {st?.missing && <Badge kind="red">missing</Badge>}
        {b.version.type === "latest" && <Badge kind="amber" data-testid="badge-latest" title="Follows every new commit">latest</Badge>}
        {st?.update_available && <Badge kind="blue" data-testid="badge-update">update available (+{st.newer_commits})</Badge>}
        {st?.update_available && !ro && <button className="btn sm" onClick={onBump}>Bump…</button>}</div>
      <div className="bctl">
        <label className="ver">Version <select aria-label="Version" value={cur} disabled={ro} onChange={(e) => { const [t, ...r] = e.target.value.split(":"); set({ ...b, version: { type: t as "commit", value: r.join(":") || null } }); }}>{opts.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}</select></label>
        <label>Slot <select aria-label="Slot" value={b.slot} disabled={ro} onChange={(e) => set({ ...b, slot: e.target.value })} title="Slot in the base document">{slots.map((s) => <option key={s}>{s}</option>)}</select></label>
        <Link className="btn sm ghost" to={`/snippets/${b.snippet_id}`}>Open snippet</Link>
      </div>
    </div>
  );
}

function PreviewPanel({ blocks, st, styles, baseId }: { blocks: Block[]; st: RefStatus[]; styles: Style[]; baseId?: string }) {
  const look = useLook({ base_doc: baseId ?? null }).data;
  const refs = blocks.filter((b): b is SnipBlock => b.type === "snippet_ref");
  const res = useQueries({ queries: refs.map((b) => {
    const sha = st.find((s) => s.block_id === b.id)?.resolved_sha;
    return { queryKey: ["snippet-at", b.snippet_id, sha], enabled: !!sha, queryFn: () => api<{ content: PMNode }>(`/snippets/${b.snippet_id}?version=${sha}`) };
  }) });
  const text = (t: string): PMNode => ({ type: "paragraph", content: [{ type: "text", text: t }] });
  const doc = useMemo<PMNode>(() => {
    const out: PMNode[] = [];
    for (const slot of ["CONTENT", ...new Set(blocks.map((b) => b.slot).filter((s) => s !== "CONTENT"))]) {
      if (slot !== "CONTENT") out.push(text(`[slot: ${slot}]`));
      for (const b of blocks.filter((x) => x.slot === slot)) {
        if (b.type === "heading") out.push({ type: "heading", attrs: { level: b.level, styleId: b.styleId }, content: [{ type: "text", text: b.text }] });
        else if (b.type === "page_break") out.push(text("— page break —"));
        else if (b.type === "toc") out.push({ type: "toc", attrs: { kind: b.kind ?? "toc", title: b.title ?? "", levels: b.levels ?? "1-4", label: b.label || null, xml: null } });
        else if (b.type === "paragraph") out.push(...(((b.content as PMNode).content ?? []) as PMNode[]));
        else out.push(...(res[refs.indexOf(b)]?.data?.content?.content ?? [text("…")]));
      }
    }
    return { type: "doc", content: out };
  }, [blocks, res.map((r) => r.data).join("|"), refs.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Preview doc={doc} styles={styles} look={look} zoom={0.7} />;
}

/** Wanted widths of the library and the right panel (per user, localStorage) and the room they have; sets --lib-w on the builder and --rp-w on .body. */
function useBuilderWidths(uid: string, ready: boolean) {
  const kl = `docgen.builder.libW.${uid}`, kr = `docgen.builder.rightW.${uid}`;
  const read = (k: string, d: number) => { const n = Number(localStorage.getItem(k)); return n > 0 ? n : d; };
  const [want, setWant] = useState(() => ({ lib: read(kl, LIB.def), right: read(kr, RIGHT.def) }));
  const [room, setRoom] = useState({ total: 1600, hasRight: true });
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    const body = el.closest<HTMLElement>(".body"), rp = body?.querySelector<HTMLElement>(".rpanel");
    const measure = () => {
      const hasRight = !!rp && !rp.hidden && !matchMedia("(max-width: 1280px)").matches; // <= 1280px the panel overlays the page
      setRoom({ total: el.clientWidth + (hasRight ? rp!.offsetWidth : 0), hasRight });
    };
    measure();
    const ro = new ResizeObserver(measure); ro.observe(el); addEventListener("resize", measure);
    return () => { ro.disconnect(); removeEventListener("resize", measure); body?.style.removeProperty("--rp-w"); };
  }, [ready]);
  const eff = fit(room.total, want.lib, want.right, room.hasRight), lim = limits(room.total, room.hasRight);
  useLayoutEffect(() => {
    ref.current?.style.setProperty("--lib-w", `${eff.lib}px`);
    if (room.hasRight) ref.current?.closest<HTMLElement>(".body")?.style.setProperty("--rp-w", `${eff.right}px`);
  }, [eff.lib, eff.right, room.hasRight]);
  const save = (k: string, field: "lib" | "right") => (v: number | null) => {
    if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, String(v));
    setWant((w) => ({ ...w, [field]: v ?? (field === "lib" ? LIB.def : RIGHT.def) }));
  };
  return { ref, eff, lim, hasRight: room.hasRight, setLib: save(kl, "lib"), setRight: save(kr, "right") };
}

export function DocumentPage({ me }: { me: Me }) {
  const { id = "" } = useParams(), nav = useNavigate(), qc = useQueryClient(), ui = useUI();
  const ro = me.org?.role === "viewer";
  const q = useQuery({ queryKey: ["doc", id], queryFn: () => api<DocFull>(`/documents/${id}`), staleTime: Infinity, gcTime: 0 });
  const folders = useFolders(me);
  useActiveFolder(q.data?.folder_id ?? null);
  const [w, setW] = useState<DocBody | null>(null);
  const bw = useBuilderWidths(me.user.id, !!w);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState("");
  const [bump, setBump] = useState<string | null>(null);
  const [preview, setPreview] = useState(false), [pvMode, setPvMode] = useState<"pdf" | "draft">("pdf"), [dragging, setDragging] = useState(false);
  const [dl, setDl] = useState<{ fmt: "docx" | "pdf"; msg: string } | null>(null), [dlErr, setDlErr] = useState(""), [missing, setMissing] = useState<{ name: string; type: VarDecl["type"] }[]>([]);
  const [over, setOver] = useState<number | null>(null);
  const wRef = useRef<DocBody | null>(null), timer = useRef<number>(), pending = useRef(false), chain = useRef<Promise<unknown>>(Promise.resolve());

  const flush = useCallback((): Promise<unknown> => {
    window.clearTimeout(timer.current);
    if (!pending.current || !wRef.current) return chain.current;
    pending.current = false;
    const b = wRef.current;
    setStatus("saving");
    chain.current = chain.current.then(() => api<DocFull>(`/documents/${id}/draft`, "PUT", {
      title: b.title, slug: b.slug, base_document: b.base_document, blocks: b.blocks, variables: b.variables,
      links: b.links.map((l) => ({ ...l, doc_id: nul(l.doc_id), version: nul(l.version), url: nul(l.url) })), revision_history: b.revision_history,
    })).then((r) => {
      setErr("");
      const cur = wRef.current;
      if (cur?.base_document && !cur.base_document.version && r.base_document?.id === cur.base_document.id) { // server pinned the head
        wRef.current = { ...cur, base_document: r.base_document };
        setW(wRef.current);
      }
      qc.invalidateQueries({ queryKey: ["updates", id] });
      qc.invalidateQueries({ queryKey: ["rev", id] });
      qc.invalidateQueries({ queryKey: ["changes"] });
      setStatus(pending.current ? "saving" : "saved");
    }).catch((e: Error) => { pending.current = true; setErr(e.message); setStatus("error"); });
    return chain.current;
  }, [id, qc]);
  const set = useCallback((n: DocBody) => {
    wRef.current = n; setW(n); pending.current = true; setStatus("saving");
    window.clearTimeout(timer.current); timer.current = window.setTimeout(flush, 800);
  }, [flush]);
  useEffect(() => { if (q.data && !wRef.current) { wRef.current = bodyOf(q.data); setW(wRef.current); } }, [q.data]);
  useEffect(() => () => { flush(); }, [flush]);

  const updates = useQuery({ queryKey: ["updates", id], queryFn: () => api<RefStatus[]>(`/documents/${id}/updates`) });
  const bases = useQuery({ queryKey: ["base-docs", me.org?.id], queryFn: () => api<BaseDoc[]>("/base-documents") });
  const baseId = w?.base_document?.id;
  const bvers = useQuery({ queryKey: ["bversions", baseId], enabled: !!baseId, queryFn: () => api<BaseDocVersion[]>(`/base-documents/${baseId}/versions`) });
  const styles = useStyles(me, baseId ?? null);
  const look = useLook({ base_doc: baseId ?? null }).data;
  const baseVars = bvers.data?.find((v) => v.sha === w?.base_document?.version)?.variables ?? [];
  const slots = ["CONTENT", ...(bvers.data?.find((v) => v.sha === w?.base_document?.version)?.placeholders.slots ?? [])];

  const doBump = useMutation({
    mutationFn: async (bid: string) => { await flush(); return api<DocFull>(`/documents/${id}/blocks/${bid}/bump`, "POST"); },
    onSuccess: (d) => { wRef.current = bodyOf(d); setW(wRef.current); setBump(null); qc.invalidateQueries({ queryKey: ["updates", id] }); qc.invalidateQueries({ queryKey: ["changes"] }); ui.toast("Snippet updated to the latest version"); },
    onError: (e: Error) => ui.toast(e.message, "err"),
  });
  const rev = useQuery({ queryKey: ["rev", id], queryFn: () => api<RevRow[]>(`/documents/${id}/revision-history`), enabled: !!w && w.revision_history === null });
  const download = async (fmt: "docx" | "pdf") => {
    setDlErr(""); setMissing([]); setDl({ fmt, msg: "Saving…" });
    try {
      await flush();
      setDl({ fmt, msg: `Preparing ${fmt.toUpperCase()}…` });
      const { export_id } = await api<{ export_id: string }>(`/documents/${id}/export?format=${fmt}`);
      for (;;) {
        const s = await api<ExportStatus>(`/exports/${export_id}`);
        if (s.status === "error") throw new Error(s.error ?? "export failed");
        if (s.status === "done") { ui.toast(s.warnings.length ? `${fmt.toUpperCase()} downloaded with warnings: ${s.warnings.join("; ")}` : `${fmt.toUpperCase()} downloaded`); window.location.assign(`/api/exports/${export_id}/download`); break; }
        setDl({ fmt, msg: s.status === "running" ? `Rendering ${fmt.toUpperCase()}…` : `Queued…` });
        await sleep(500);
      }
      setDl(null);
    } catch (e) {
      setDl(null); setDlErr((e as Error).message);
      const d = e instanceof ApiError ? (e.detail as { error?: string; variables?: typeof missing } | undefined) : undefined;
      if (d?.error === "missing_variables") setMissing(d.variables ?? []);
    }
  };

  const path = folderPath(folders.data, q.data?.folder_id);
  if (q.error) return <div className="page"><Empty icon="doc" title="Document not found">It may have been deleted. <Link to="/">Back to the dashboard</Link></Empty></div>;
  if (!w) return <div className="page"><Skeleton rows={6} h={30} /></div>;
  const upd = (p: Partial<DocBody>) => set({ ...w, ...p });
  const setBlock = (i: number, n: Block) => upd({ blocks: w.blocks.map((b, j) => (j === i ? n : b)) });
  const add = (i: number, b: Block) => upd({ blocks: insertAt(w.blocks, i, b) });
  const drop = (i: number) => (e: React.DragEvent) => {
    e.preventDefault(); setOver(null); setDragging(false);
    const s = e.dataTransfer.getData(DND_SNIPPET), m = e.dataTransfer.getData(DND_BLOCK);
    if (s) { const j = JSON.parse(s); add(i, snippetBlock(j.f, j.s, j.sha)); }
    else if (m !== "") upd({ blocks: moveTo(w.blocks, Number(m), i) });
  };
  const zone = (i: number) => (
    <li data-testid="drop-zone" className={`dz-line ${over === i ? "over" : ""}`} aria-hidden="true" onDragOver={(e) => { e.preventDefault(); setOver(i); }} onDragLeave={() => setOver(null)} onDrop={drop(i)}>
      <span>Drop here</span></li>
  );
  const vars = w.variables, st = (bid: string) => updates.data?.find((s) => s.block_id === bid);
  const busy = !!dl;
  const dlBtn = (fmt: "docx" | "pdf") => (
    <button className="btn" disabled={busy} onClick={() => download(fmt)}>{dl?.fmt === fmt ? <Spinner /> : <Icon n={fmt === "docx" ? "download" : "pdf"} />}{dl?.fmt === fmt ? dl.msg : `Download ${fmt.toUpperCase()}`}</button>
  );

  return (
    <div className="page fill">
      <PageHead crumbs={[{ label: "Dashboard", to: "/" }, ...path.map((p) => ({ label: p.name, to: `/folders/${p.id}` })), { label: w.title || "Document" }]}
        actions={<Link className="btn ghost sm" to={`/history/document/${id}`}><Icon n="clock" />History</Link>} />
      <div className="edhead">
        <input className="titleinput" aria-label="Document title" placeholder="Untitled document" value={w.title} disabled={ro} onChange={(e) => upd({ title: e.target.value })} />
        <span className={`savestatus ${status}`} data-testid="doc-save-status" data-status={status} role="status">{status === "saving" ? <><Spinner />Saving…</> : status === "saved" ? <><Icon n="check" size={14} />Draft saved</> : status === "error" ? <><Icon n="alert" size={14} />Save failed</> : null}</span>
        <div className="actions">
          <button className="btn" onClick={() => { setPvMode("pdf"); setPreview(true); }}><Icon n="eye" />Preview</button>
          {dlBtn("docx")}{dlBtn("pdf")}
          {!ro && <button className="btn primary" onClick={() => flush().then(() => nav(`/commit/${q.data!.folder_id}?select=${id}`))}><Icon n="commit" />Commit…</button>}
        </div>
      </div>
      <ErrorBanner e={err ? new Error(err) : null} />
      {dlErr && !missing.length && <ErrorBanner e={new Error(dlErr)} />}
      {missing.length > 0 && (
        <Banner kind="err" data-testid="missing-vars"><b>The export needs values for {missing.length} variable{missing.length > 1 ? "s" : ""}:</b>
          <ul className="checklist">{missing.map((m) => {
            const declared = vars.declarations.some((d) => d.name === m.name);
            return <li key={m.name}><Icon n="x" size={14} /><code>{m.name}</code> <span className="muted">({m.type})</span>
              {declared ? <span className="muted"> — declared; give it a value or default in the Variables panel</span> : !ro && <button className="btn sm" onClick={() => upd({ variables: { ...vars, declarations: [...vars.declarations, { name: m.name, type: m.type, default: null, columns: [] }] } })}>Declare</button>}</li>; })}</ul></Banner>)}
      <div className="builder" ref={bw.ref}>
        <Library disabled={ro} onAdd={(g, s) => add(w.blocks.length, snippetBlock(g.folder_id, s.id, s.head_sha))} onDragging={setDragging} />
        <Resizer label="Resize snippet library" testid="resize-lib" dir={1} value={bw.eff.lib} min={LIB.min} max={bw.lim.libMax} onChange={bw.setLib} onReset={() => bw.setLib(null)} />
        <section className="blocks" aria-label="Document blocks">
          <div className="blocks-h"><h2>Blocks <span className="cnt">{w.blocks.length}</span></h2>
            {!ro && <div className="actions">
              <button className="btn sm" onClick={() => add(w.blocks.length, { id: newId(), slot: "CONTENT", type: "heading", text: "New heading", level: 1, styleId: null, numbered: false })}><Icon n="plus" size={14} />Heading</button>
              <button className="btn sm" onClick={() => add(w.blocks.length, paragraphBlock())}><Icon n="plus" size={14} />Paragraph</button>
              <button className="btn sm" onClick={() => add(w.blocks.length, listBlock("toc"))}><Icon n="plus" size={14} />TOC</button>
              <button className="btn sm" onClick={() => add(w.blocks.length, listBlock("tot"))}><Icon n="plus" size={14} />List of tables</button>
              <button className="btn sm" onClick={() => add(w.blocks.length, listBlock("tof"))}><Icon n="plus" size={14} />List of figures</button>
              <button className="btn sm" onClick={() => add(w.blocks.length, { id: newId(), slot: "CONTENT", type: "page_break" })}><Icon n="plus" size={14} />Page break</button></div>}</div>
          {w.blocks.length === 0 && <div className="emptydrop" onDragOver={(e) => { e.preventDefault(); setOver(0); }} onDragLeave={() => setOver(null)} onDrop={drop(0)}>
            <Icon n="layers" size={26} /><b>Drag snippets here</b><span className="muted">or use “Add” in the library</span></div>}
          <ol className={`blocklist ${dragging ? "dragging" : ""}`} data-testid="blocks">
            {w.blocks.map((b, i) => (
              <Fragment key={b.id}>
                {!ro && zone(i)}
                <li data-testid="block" className={`block t-${b.type}`}>
                  {!ro && <span className="grip" draggable aria-label="Drag handle" title="Drag to reorder" onDragStart={(e) => { e.dataTransfer.setData(DND_BLOCK, String(i)); e.dataTransfer.effectAllowed = "move"; setDragging(true); }} onDragEnd={() => setDragging(false)}><Icon n="grip" /></span>}
                  <span className="bnum">{i + 1}</span>
                  {b.type === "snippet_ref" && <SnippetCard b={b} st={st(b.id)} ro={ro} slots={slots} set={(n) => setBlock(i, n)} onBump={() => setBump(b.id)} />}
                  {b.type === "heading" && <div className="bbody"><div className="btitle"><Icon n="edit" /><input className="hin" aria-label="Heading text" value={b.text} disabled={ro} onChange={(e) => setBlock(i, { ...b, text: e.target.value })} /></div>
                    <div className="bctl"><label>Level <select aria-label="Heading level" value={b.level} disabled={ro} onChange={(e) => setBlock(i, { ...b, level: Number(e.target.value) })}>{[1, 2, 3, 4].map((l) => <option key={l}>{l}</option>)}</select></label>
                      <button className={`btn sm ${b.numbered ? "on" : ""}`} aria-pressed={b.numbered} disabled={ro} onClick={() => setBlock(i, { ...b, numbered: !b.numbered })}>Numbered</button>
                      <label>Slot <select aria-label="Slot" value={b.slot} disabled={ro} onChange={(e) => setBlock(i, { ...b, slot: e.target.value })}>{slots.map((s) => <option key={s}>{s}</option>)}</select></label></div></div>}
                  {b.type === "page_break" && <div className="bbody"><div className="btitle"><Icon n="split" />Page break</div></div>}
                  {b.type === "toc" && <div className="bbody"><div className="btitle"><Icon n="ul" />{TOC_KINDS[b.kind ?? "toc"]}<input className="hin" aria-label="List title" placeholder="Title" value={b.title ?? ""} disabled={ro} maxLength={200} onChange={(e) => setBlock(i, { ...b, title: e.target.value })} /></div>
                    <div className="bctl"><label>Kind <select aria-label="List kind" value={b.kind ?? "toc"} disabled={ro} onChange={(e) => { const kind = e.target.value as keyof typeof TOC_KINDS; setBlock(i, { ...b, kind, title: b.title === TOC_KINDS[b.kind ?? "toc"] ? TOC_KINDS[kind] : b.title, label: kind === "tot" ? "Table" : kind === "tof" ? "Figure" : "" }); }}>{Object.entries(TOC_KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
                      {(b.kind ?? "toc") === "toc" ? <label>Levels <select aria-label="Heading levels" value={b.levels ?? "1-4"} disabled={ro} onChange={(e) => setBlock(i, { ...b, levels: e.target.value })}>{["1-1", "1-2", "1-3", "1-4"].map((l) => <option key={l}>{l}</option>)}</select></label>
                        : <label>Caption label <input aria-label="Caption label" value={b.label ?? ""} disabled={ro} maxLength={60} onChange={(e) => setBlock(i, { ...b, label: e.target.value })} /></label>}</div></div>}
                  {b.type === "paragraph" && <div className="bbody"><div className="btitle"><Icon n="edit" />Paragraph</div>
                    <ParagraphEditor value={b.content as PMNode} styles={styles.data ?? []} look={look} ro={ro} onChange={(d) => setBlock(i, { ...b, content: d as never })} /></div>}
                  {!ro && <button className="btn ghost icon" aria-label="Remove block" title="Remove block" onClick={() => upd({ blocks: w.blocks.filter((_, j) => j !== i) })}><Icon n="trash" /></button>}
                </li>
              </Fragment>))}
            {!ro && w.blocks.length > 0 && zone(w.blocks.length)}
          </ol>
          <ErrorBanner e={doBump.error} />
        </section>
        {bw.hasRight && <Resizer label="Resize right panel" testid="resize-right" dir={-1} value={bw.eff.right} min={RIGHT.min} max={bw.lim.rightMax} onChange={bw.setRight} onReset={() => bw.setRight(null)} />}
      </div>
      <ContextPanel essential>
        <details open className="acc"><summary>Base document</summary>
          <div className="stack">
            <select aria-label="Base document" disabled={ro} value={w.base_document?.id ?? ""} onChange={(e) => upd({ base_document: e.target.value ? { id: e.target.value, version: null } : null })}>
              <option value="">(bundled default)</option>{bases.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select>
            {w.base_document && isAdmin(me.org?.role) && <EditBaseButton id={w.base_document.id} />}
            {w.base_document && bvers.data?.[0] && bvers.data[0].sha !== w.base_document.version && (
              <div className="banner info" data-testid="base-newer"><Icon n="info" /><div>Newer base document version available: v{bvers.data[0].number}{bvers.data[0].message ? ` — ${bvers.data[0].message}` : ""}.{" "}
                {!ro && <button className="btn sm" onClick={() => upd({ base_document: { ...w.base_document!, version: bvers.data![0].sha } })}>Use v{bvers.data[0].number}</button>}</div></div>)}
            {w.base_document && <select aria-label="Base document version" disabled={ro} value={w.base_document.version ?? ""} onChange={(e) => upd({ base_document: { ...w.base_document!, version: e.target.value } })}>
              {bvers.data?.map((v) => <option key={v.sha} value={v.sha}>v{v.number} {v.message}</option>)}</select>}
            {baseVars.length > 0 && <>
              <p className="muted small">Variables of this base document (dynamic tables, cover and header/footer values). Empty fields use the base document's default.</p>
              <div data-testid="base-vars"><VariablesForm fixed="values" ro={ro} vars={{ declarations: baseVars, values: { ...Object.fromEntries(baseVars.filter((d) => d.type === "list").map((d) => [d.name, d.default])), ...w.variables.values } }}
                onChange={(v) => upd({ variables: { ...w.variables, values: v.values } })} /></div></>}</div></details>
        <details open className="acc"><summary>Variables <span className="cnt">{vars.declarations.length}</span></summary>
          <VariablesForm vars={vars} ro={ro} onChange={(v) => upd({ variables: v })} />
          <p className="muted small">Built-ins: doc.title, doc.version, doc.date, org.name, author</p></details>
        <details className="acc"><summary>Linked documents <span className="cnt">{w.links.length}</span></summary>
          {w.links.map((l, i) => (
            <div key={i} className="linkcard">{(["title", "doc_id", "version", "url"] as const).map((k) =>
              <input key={k} aria-label={`Link ${k}`} placeholder={k === "doc_id" ? "document id" : k} value={l[k] ?? ""} disabled={ro} onChange={(e) => upd({ links: w.links.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) })} />)}
              {!ro && <button className="btn ghost icon" aria-label="Remove link" onClick={() => upd({ links: w.links.filter((_, j) => j !== i) })}><Icon n="x" size={14} /></button>}</div>))}
          {!ro && <button className="btn sm" onClick={() => upd({ links: [...w.links, { title: "Untitled", doc_id: null, version: null, url: null }] })}><Icon n="plus" size={14} />Link</button>}</details>
        <details className="acc"><summary>Revision history</summary>
          <RevisionTable rows={w.revision_history ?? rev.data ?? []} auto={w.revision_history === null} ro={ro} onChange={(r) => upd({ revision_history: r })} /></details>
      </ContextPanel>
      {bump && <BumpDialog docId={id} block={bump} onApply={() => doBump.mutate(bump)} onClose={() => setBump(null)} />}
      {preview && <Modal title="Document preview" wide tall onClose={() => setPreview(false)}>
        <div className="tabs small" role="tablist" aria-label="Preview type">
          <button role="tab" className={pvMode === "pdf" ? "tab on" : "tab"} aria-selected={pvMode === "pdf"} onClick={() => setPvMode("pdf")}>PDF (as exported)</button>
          <button role="tab" className={pvMode === "draft" ? "tab on" : "tab"} aria-selected={pvMode === "draft"} onClick={() => setPvMode("draft")}>Draft view (fast, approximate)</button></div>
        {pvMode === "pdf" ? <PdfPreview docId={id} flush={flush} /> : <PreviewPanel blocks={w.blocks} st={updates.data ?? []} styles={styles.data ?? []} baseId={baseId} />}</Modal>}
    </div>
  );
}
