import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, BaseDoc, BaseDocVersion, Block, Commit, DocBody, DocFull, ExportStatus, LibraryGroup, Me, RefStatus, RevRow, Style, VarDecl } from "../api";
import { PMNode } from "../editor/doc";
import { useLook } from "../editor/look";
import { Preview } from "../editor/SnippetEditor";
import { insertAt, moveTo, newId, snippetBlock } from "./blocks";
import { RevisionTable, VariablesForm } from "./Variables";

const Err = ({ e }: { e: unknown }) => (e ? <p role="alert" style={{ color: "crimson" }}>{(e as Error).message}</p> : null);
const short = (s: string | null) => (s ? s.slice(0, 7) : "—");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const nul = (s: string | null) => (s ? s : null);
const pinKey = (v: { type: string; value: string | null }) => `${v.type}:${v.value ?? ""}`;
const DND_SNIPPET = "application/x-docgen-snippet", DND_BLOCK = "application/x-docgen-block";
const bodyOf = (d: DocBody): DocBody => ({ title: d.title, slug: d.slug, base_document: d.base_document, blocks: d.blocks, variables: d.variables, links: d.links, revision_history: d.revision_history ?? null });

function Library({ onAdd, disabled }: { onAdd: (g: LibraryGroup, s: LibraryGroup["snippets"][0]) => void; disabled: boolean }) {
  const [q, setQ] = useState(""), [tag, setTag] = useState("");
  const lib = useQuery({ queryKey: ["library", q, tag], queryFn: () => api<LibraryGroup[]>(`/library/snippets?q=${encodeURIComponent(q)}${tag ? `&tag=${encodeURIComponent(tag)}` : ""}`) });
  return (
    <aside style={{ width: 250 }} aria-label="Snippet library">
      <h4>Snippet library</h4>
      <input placeholder="Search snippets" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: "100%" }} />
      <input placeholder="Filter by tag (e.g. v1.0)" value={tag} onChange={(e) => setTag(e.target.value.trim())} style={{ width: "100%" }} />
      {lib.data?.length === 0 && <p><small>No committed snippets found.</small></p>}
      {lib.data?.map((g) => (
        <div key={g.folder_id}>
          <b>📁 {g.folder_name}</b>{g.tags.length > 0 && <small> [{g.tags.join(", ")}]</small>}
          <ul style={{ listStyle: "none", paddingLeft: 8, margin: "2px 0" }}>{g.snippets.map((s) => (
            <li key={s.id} draggable={!disabled} data-testid="lib-item"
              onDragStart={(e) => e.dataTransfer.setData(DND_SNIPPET, JSON.stringify({ f: g.folder_id, s: s.id, sha: s.head_sha }))}>
              <span title="Drag into the document">⠿</span> {s.title} <small>@{short(s.head_sha)}</small>{" "}
              {!disabled && <button aria-label={`Add ${s.title}`} onClick={() => onAdd(g, s)}>Add</button>}
            </li>))}</ul>
        </div>))}
      <Err e={lib.error} />
    </aside>
  );
}

function BumpDialog({ docId, block, onApply, onClose }: { docId: string; block: string; onApply: () => void; onClose: () => void }) {
  const d = useQuery({ queryKey: ["bump", docId, block], queryFn: () => api<{ from_sha: string; to_sha: string; text: string; summary: Record<string, number> }>(`/documents/${docId}/blocks/${block}/bump`) });
  return (
    <div style={{ border: "1px solid darkorange", padding: 8, margin: "4px 0" }} data-testid="bump-diff">
      {d.data && <><b>{short(d.data.from_sha)} → {short(d.data.to_sha)}</b> +{d.data.summary.added} −{d.data.summary.removed} ~{d.data.summary.changed} blocks
        <pre style={{ maxHeight: 200, overflow: "auto" }}>{d.data.text || "(no textual differences)"}</pre>
        <button onClick={onApply}>Apply bump</button>{" "}</>}
      <button onClick={onClose}>Cancel</button><Err e={d.error} />
    </div>
  );
}

function SnippetRow({ b, st, ro, slots, set, onBump }: {
  b: Extract<Block, { type: "snippet_ref" }>; st?: RefStatus; ro: boolean; slots: string[]; set: (n: Block) => void; onBump: () => void;
}) {
  const hist = useQuery({ queryKey: ["shist", b.snippet_id], queryFn: () => api<Commit[]>(`/snippets/${b.snippet_id}/history`) });
  const cur = pinKey(b.version);
  const opts = [{ v: "latest:", l: "latest ⚠" }, ...(hist.data ?? []).map((c) => ({ v: `commit:${c.sha}`, l: `${short(c.sha)} ${c.tags.map((t) => `[${t}]`).join("")} ${c.message.slice(0, 30)}` })),
    ...(hist.data ?? []).flatMap((c) => c.tags.map((t) => ({ v: `tag:${t}`, l: `tag ${t}` })))];
  if (!opts.some((o) => o.v === cur)) opts.push({ v: cur, l: `${b.version.type} ${short(b.version.value)}` });
  return (
    <span>
      <b>{st?.folder_name ?? "?"}</b> / {st?.title ?? <i>missing snippet</i>} @{" "}
      <select aria-label="Version" value={cur} disabled={ro} onChange={(e) => {
        const [t, ...r] = e.target.value.split(":");
        set({ ...b, version: { type: t as "commit", value: r.join(":") || null } });
      }}>{opts.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}</select>{" "}
      {b.version.type === "latest" && <span data-testid="badge-latest" title="Follows every new commit" style={{ background: "#fff3cd", padding: "0 4px" }}>⚠ latest</span>}{" "}
      {st?.update_available && <><span data-testid="badge-update" style={{ background: "#d1ecf1", padding: "0 4px" }}>update available (+{st.newer_commits})</span>{" "}
        {!ro && <button onClick={onBump}>Bump…</button>}</>}{" "}
      <select aria-label="Slot" value={b.slot} disabled={ro} onChange={(e) => set({ ...b, slot: e.target.value })} title="Slot in the base document">
        {slots.map((s) => <option key={s}>{s}</option>)}</select>
    </span>
  );
}

function PreviewPanel({ blocks, st, styles, baseId }: { blocks: Block[]; st: RefStatus[]; styles: Style[]; baseId?: string }) {
  const look = useLook({ base_doc: baseId ?? null }).data;
  const refs = blocks.filter((b): b is Extract<Block, { type: "snippet_ref" }> => b.type === "snippet_ref");
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
        else if (b.type === "toc") out.push(text("[Table of contents]"));
        else out.push(...(res[refs.indexOf(b)]?.data?.content?.content ?? [text("…")]));
      }
    }
    return { type: "doc", content: out };
  }, [blocks, res.map((r) => r.data).join("|"), refs.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Preview doc={doc} styles={styles} look={look} zoom={0.55} />;
}

export function DocBuilder({ id, me, onClose, onCommit }: { id: string; me: Me; onClose: () => void; onCommit: () => void }) {
  const qc = useQueryClient();
  const ro = me.org?.role === "viewer";
  const q = useQuery({ queryKey: ["doc", id], queryFn: () => api<DocFull>(`/documents/${id}`), staleTime: Infinity, gcTime: 0 });
  const [w, setW] = useState<DocBody | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState("");
  const [bump, setBump] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [dl, setDl] = useState(""), [dlErr, setDlErr] = useState(""), [missing, setMissing] = useState<{ name: string; type: VarDecl["type"] }[]>([]);
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
  const styles = useQuery({ queryKey: ["styles-for", me.org?.id, baseId], queryFn: () => api<Style[]>(`/styles${baseId ? `?base_doc=${baseId}` : ""}`) });
  const slots = ["CONTENT", ...(bvers.data?.find((v) => v.sha === w?.base_document?.version)?.placeholders.slots ?? [])];

  const doBump = useMutation({
    mutationFn: async (bid: string) => { await flush(); return api<DocFull>(`/documents/${id}/blocks/${bid}/bump`, "POST"); },
    onSuccess: (d) => { wRef.current = bodyOf(d); setW(wRef.current); setBump(null); qc.invalidateQueries({ queryKey: ["updates", id] }); qc.invalidateQueries({ queryKey: ["changes"] }); },
  });
  const rev = useQuery({ queryKey: ["rev", id], queryFn: () => api<RevRow[]>(`/documents/${id}/revision-history`), enabled: !!w && w.revision_history === null });
  const download = async (fmt: "docx" | "pdf") => {
    setDlErr(""); setMissing([]); setDl(`Preparing ${fmt.toUpperCase()}…`);
    try {
      await flush();
      const { export_id } = await api<{ export_id: string }>(`/documents/${id}/export?format=${fmt}`);
      for (;;) {
        const s = await api<ExportStatus>(`/exports/${export_id}`);
        if (s.status === "error") throw new Error(s.error ?? "export failed");
        if (s.status === "done") { setDl(s.warnings.length ? `Downloaded with warnings: ${s.warnings.join("; ")}` : "Downloaded."); window.location.assign(`/api/exports/${export_id}/download`); return; }
        await sleep(500);
      }
    } catch (e) {
      setDl(""); setDlErr((e as Error).message);
      const d = e instanceof ApiError ? (e.detail as { error?: string; variables?: typeof missing } | undefined) : undefined;
      if (d?.error === "missing_variables") setMissing(d.variables ?? []);
    }
  };

  if (!w) return <p>Loading…{q.error && <Err e={q.error} />}</p>;
  const upd = (p: Partial<DocBody>) => set({ ...w, ...p });
  const setBlock = (i: number, n: Block) => upd({ blocks: w.blocks.map((b, j) => (j === i ? n : b)) });
  const add = (i: number, b: Block) => upd({ blocks: insertAt(w.blocks, i, b) });
  const drop = (i: number) => (e: React.DragEvent) => {
    e.preventDefault(); setOver(null);
    const s = e.dataTransfer.getData(DND_SNIPPET), m = e.dataTransfer.getData(DND_BLOCK);
    if (s) { const j = JSON.parse(s); add(i, snippetBlock(j.f, j.s, j.sha)); }
    else if (m !== "") upd({ blocks: moveTo(w.blocks, Number(m), i) });
  };
  const zone = (i: number, key?: string) => (
    <li key={key} data-testid="drop-zone" onDragOver={(e) => { e.preventDefault(); setOver(i); }} onDragLeave={() => setOver(null)} onDrop={drop(i)}
      style={{ height: over === i ? 28 : 10, background: over === i ? "#cfe3ff" : "transparent", listStyle: "none" }} />
  );
  const vars = w.variables;
  const status_ = (i: string) => updates.data?.find((s) => s.block_id === i);

  return (
    <section>
      <h3>
        <input aria-label="Document title" value={w.title} disabled={ro} onChange={(e) => upd({ title: e.target.value })} size={40} />{" "}
        <small data-testid="doc-save-status" data-status={status}>{status === "saving" ? "saving…" : status === "saved" ? "draft saved" : ""}</small>{" "}
        <button onClick={onClose}>Close</button>
      </h3>
      <Err e={err ? new Error(err) : null} />
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <Library disabled={ro} onAdd={(g, s) => add(w.blocks.length, snippetBlock(g.folder_id, s.id, s.head_sha))} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h4>Blocks {!ro && <small>
            <button onClick={() => add(w.blocks.length, { id: newId(), slot: "CONTENT", type: "heading", text: "New heading", level: 1, styleId: null, numbered: false })}>+ Heading</button>{" "}
            <button onClick={() => add(w.blocks.length, { id: newId(), slot: "CONTENT", type: "toc" })}>+ TOC</button>{" "}
            <button onClick={() => add(w.blocks.length, { id: newId(), slot: "CONTENT", type: "page_break" })}>+ Page break</button></small>}</h4>
          {w.blocks.length === 0 && <p onDragOver={(e) => e.preventDefault()} onDrop={drop(0)} style={{ border: "2px dashed #aaa", padding: 24 }}>Drag snippets here, or use “Add” in the library.</p>}
          <ol style={{ padding: 0, margin: 0 }} data-testid="blocks">
            {w.blocks.map((b, i) => (
              <Fragment key={b.id}>
                {!ro && zone(i)}
                <li data-testid="block" style={{ listStyle: "none", border: "1px solid #ccc", padding: "4px 8px", background: "#fafafa" }}>
                  {!ro && <span draggable aria-label="Drag handle" style={{ cursor: "grab" }} onDragStart={(e) => e.dataTransfer.setData(DND_BLOCK, String(i))}>⠿ </span>}
                  {b.type === "snippet_ref" && <SnippetRow b={b} st={status_(b.id)} ro={ro} slots={slots} set={(n) => setBlock(i, n)} onBump={() => setBump(b.id)} />}
                  {b.type === "heading" && <span>
                    <input aria-label="Heading text" value={b.text} disabled={ro} onChange={(e) => setBlock(i, { ...b, text: e.target.value })} />{" "}
                    <select aria-label="Heading level" value={b.level} disabled={ro} onChange={(e) => setBlock(i, { ...b, level: Number(e.target.value) })}>{[1, 2, 3, 4].map((l) => <option key={l}>{l}</option>)}</select>{" "}
                    <label><input type="checkbox" checked={b.numbered} disabled={ro} onChange={(e) => setBlock(i, { ...b, numbered: e.target.checked })} /> numbered</label>{" "}
                    <select aria-label="Slot" value={b.slot} disabled={ro} onChange={(e) => setBlock(i, { ...b, slot: e.target.value })}>{slots.map((s) => <option key={s}>{s}</option>)}</select></span>}
                  {b.type === "page_break" && <i>— page break —</i>}
                  {b.type === "toc" && <i>Table of contents</i>}
                  {!ro && <button aria-label="Remove block" style={{ float: "right" }} onClick={() => upd({ blocks: w.blocks.filter((_, j) => j !== i) })}>×</button>}
                  {bump === b.id && <BumpDialog docId={id} block={b.id} onApply={() => doBump.mutate(b.id)} onClose={() => setBump(null)} />}
                </li>
              </Fragment>))}
            {!ro && w.blocks.length > 0 && zone(w.blocks.length)}
          </ol>
          <Err e={doBump.error} />
          {preview && <PreviewPanel blocks={w.blocks} st={updates.data ?? []} styles={styles.data ?? []} baseId={baseId} />}
        </div>
        <aside style={{ width: 300 }}>
          <p>
            <button onClick={() => setPreview(!preview)}>{preview ? "Hide preview" : "Preview"}</button>{" "}
            <button onClick={() => download("docx")}>Download DOCX</button>{" "}
            <button onClick={() => download("pdf")}>Download PDF</button>{" "}
            {!ro && <button onClick={() => flush().then(onCommit)}>Commit…</button>}
          </p>
          {dl && <p data-testid="dl-status">{dl}</p>}<Err e={dlErr ? new Error(dlErr) : null} />
          {missing.length > 0 && <ul data-testid="missing-vars">{missing.map((m) => <li key={m.name}><code>{m.name}</code> ({m.type}){" "}
            {!ro && !vars.declarations.some((d) => d.name === m.name) && <button onClick={() => upd({ variables: { ...vars, declarations: [...vars.declarations, { name: m.name, type: m.type, default: null, columns: [] }] } })}>Declare</button>}</li>)}</ul>}
          <h4>Base document</h4>
          <select aria-label="Base document" disabled={ro} value={w.base_document?.id ?? ""} onChange={(e) => upd({ base_document: e.target.value ? { id: e.target.value, version: null } : null })}>
            <option value="">(bundled default)</option>{bases.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select>
          {w.base_document && <select aria-label="Base document version" disabled={ro} value={w.base_document.version ?? ""} onChange={(e) => upd({ base_document: { ...w.base_document!, version: e.target.value } })}>
            {bvers.data?.map((v) => <option key={v.sha} value={v.sha}>v{v.number} {v.message}</option>)}</select>}
          <h4>Variables</h4>
          <VariablesForm vars={vars} ro={ro} onChange={(v) => upd({ variables: v })} />
          <small>Built-ins: doc.title, doc.version, doc.date, org.name, author</small>
          <h4>Revision history</h4>
          <RevisionTable rows={w.revision_history ?? rev.data ?? []} auto={w.revision_history === null} ro={ro} onChange={(r) => upd({ revision_history: r })} />
          <h4>Linked documents</h4>
          {w.links.map((l, i) => (
            <p key={i}>{(["title", "doc_id", "version", "url"] as const).map((k) =>
              <input key={k} aria-label={`Link ${k}`} placeholder={k} size={k === "url" ? 14 : 8} value={l[k] ?? ""} disabled={ro}
                onChange={(e) => upd({ links: w.links.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) })} />)}
              {!ro && <button aria-label="Remove link" onClick={() => upd({ links: w.links.filter((_, j) => j !== i) })}>×</button>}</p>))}
          {!ro && <button onClick={() => upd({ links: [...w.links, { title: "Untitled", doc_id: null, version: null, url: null }] })}>+ Link</button>}
        </aside>
      </div>
    </section>
  );
}
