import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/core";
import { NodeSelection } from "@tiptap/pm/state";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Draft, Me, Style } from "../api";
import { effectiveStyle, extractStyles, extractVariables, NAME_RE, PMNode } from "./doc";
import "./editor.css";
import { extensions } from "./extensions";
import { contentToHtml } from "./render";

export const useStyles = (me: Me) =>
  useQuery({ queryKey: ["styles", me.org?.id], queryFn: () => api<Style[]>("/styles"), staleTime: 60_000 });

export function Preview({ doc, styles, small }: { doc: PMNode | null | undefined; styles: Style[]; small?: boolean }) {
  const html = useMemo(() => contentToHtml(doc, styles), [doc, styles]);
  return <div className="page-scroll"><div className={small ? "page small" : "page"} data-testid="preview" dangerouslySetInnerHTML={{ __html: html }} /></div>;
}

type VForm = { name: string; default: string; kind: "" | "upper" | "date"; format: string; existing: boolean };
type Status = "idle" | "saving" | "saved" | "error";

export function SnippetEditor({ id, me, epoch, onCommit }: { id: string; me: Me; epoch: number; onCommit: () => void }) {
  const qc = useQueryClient();
  const ro = me.org?.role === "viewer";
  const d = useQuery({ queryKey: ["draft", id], queryFn: () => api<Draft>(`/snippets/${id}/draft`) });
  const styles = useStyles(me).data ?? [];
  const [doc, setDoc] = useState<PMNode>({ type: "doc" });
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [err, setErr] = useState("");
  const [vf, setVf] = useState<VForm | null>(null);
  const titleRef = useRef(""), timer = useRef<number>(), pending = useRef(false), chain = useRef<Promise<unknown>>(Promise.resolve());
  const editorRef = useRef<Editor | null>(null);

  // Save everything pending now. Saves are chained so a slow earlier PUT can't overwrite a later one.
  const flush = useCallback((): Promise<unknown> => {
    window.clearTimeout(timer.current);
    const ed = editorRef.current;
    if (!pending.current || !ed) return chain.current;
    pending.current = false;
    const content = ed.getJSON() as PMNode, t = titleRef.current;
    setStatus("saving");
    chain.current = chain.current.then(() => api<Draft>(`/snippets/${id}/draft`, "PUT",
      { title: t || undefined, content, styles: extractStyles(content), variables: extractVariables(content) }))
      .then((r) => { setErr(""); qc.setQueryData(["draft", id], r); qc.invalidateQueries({ queryKey: ["changes"] }); setStatus(pending.current ? "saving" : "saved"); })
      .catch((e: Error) => { pending.current = true; setErr(e.message); setStatus("error"); });
    return chain.current;
  }, [id, qc]);
  const touch = useCallback(() => { pending.current = true; setStatus("saving"); window.clearTimeout(timer.current); timer.current = window.setTimeout(flush, 800); }, [flush]);

  const editor = useEditor({
    extensions, content: { type: "doc", content: [] }, editable: !ro,
    onUpdate: ({ editor }) => { setDoc(editor.getJSON() as PMNode); touch(); },
  });
  editorRef.current = editor;
  useEffect(() => () => { flush(); }, [flush]); // leaving the editor never drops edits
  useEffect(() => { editor?.setEditable(!ro); }, [editor, ro]);

  // Reload the editor only when the server draft changes identity (restore/discard/resolve), not per autosave.
  const loaded = useRef("");
  useEffect(() => {
    if (!d.data || !editor) return;
    const k = [id, epoch, d.data.base_sha, d.data.exists, d.data.conflict, d.data.head_sha].join();
    if (loaded.current === k) return;
    loaded.current = k;
    editor.commands.setContent(d.data.content as any, { emitUpdate: false });
    titleRef.current = d.data.title; setTitle(d.data.title);
    setDoc(editor.getJSON() as PMNode); pending.current = false; setStatus("idle");
  }, [d.data, editor, id, epoch]);

  const discard = useMutation({
    mutationFn: () => api(`/snippets/${id}/draft`, "DELETE"),
    onSuccess: async () => { pending.current = false; await qc.invalidateQueries(); loaded.current = ""; setErr(""); },
  });

  const st = useEditorState({
    editor, selector: ({ editor: e }) => {
      if (!e) return null;
      const $f = e.state.selection.$from, p = $f.parent;
      const inList = [...Array(Math.max($f.depth, 0) + 1).keys()].some((i) => ["bulletList", "orderedList"].includes($f.node(i).type.name));
      const sel = e.state.selection;
      return { style: effectiveStyle({ type: p.type.name, attrs: p.attrs }, inList) ?? "Normal",
        bold: e.isActive("bold"), italic: e.isActive("italic"), underline: e.isActive("underline"), link: e.isActive("link"),
        bullet: e.isActive("bulletList"), ordered: e.isActive("orderedList"), table: e.isActive("table"),
        dynamic: (e.getAttributes("table").dynamicList as string | null) ?? null,
        variable: sel instanceof NodeSelection && sel.node.type.name === "variable" };
    },
  });

  if (!editor) return null;
  const run = () => editor.chain().focus();
  const applyStyle = (sid: string) => {
    const s = styles.find((x) => x.style_id === sid);
    if (!s) return;
    if (s.kind === "heading") run().setNode("heading", { level: s.level, styleId: s.style_id }).run();
    else run().setNode("paragraph", { styleId: s.style_id }).run();
  };
  const openVariable = () => {
    const sel = editor.state.selection;
    if (sel instanceof NodeSelection && sel.node.type.name === "variable") {
      const a = sel.node.attrs, [k, ...f] = String(a.filter ?? "").split(":");
      setVf({ name: a.name, default: a.default ?? "", kind: (k as VForm["kind"]) || "", format: f.join(":") || "YYYY-MM-DD", existing: true });
    } else {
      const txt = editor.state.doc.textBetween(sel.from, sel.to, " ").trim();
      setVf({ name: txt.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "_$1"), default: txt, kind: "", format: "YYYY-MM-DD", existing: false });
    }
  };
  const saveVariable = () => {
    if (!vf) return;
    if (!NAME_RE.test(vf.name)) { setErr("Variable name: letters, digits, _ (optionally row.field)"); return; }
    setErr("");
    const attrs = { name: vf.name, default: vf.default, filter: vf.kind === "date" ? `date:${vf.format}` : vf.kind || null };
    if (vf.existing) run().updateAttributes("variable", attrs).run();
    else run().insertContent({ type: "variable", attrs }).run();
    setVf(null);
  };
  const markDynamic = () => {
    const v = prompt("List variable that supplies the rows (empty = make static).\nThe last row is the row template; use {{ row.field }} chips in it.", st?.dynamic ?? "");
    if (v === null) return;
    if (v && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(v)) { setErr("List variable name: letters, digits, _"); return; }
    setErr(""); run().updateAttributes("table", { dynamicList: v || null }).run();
  };
  const setLink = () => {
    const href = prompt("Link URL (empty removes)", editor.getAttributes("link").href ?? "https://");
    if (href === null) return;
    if (!href) run().extendMarkRange("link").unsetLink().run(); else run().extendMarkRange("link").setLink({ href }).run();
  };
  const upload = async (f?: File) => {
    if (!f) return;
    try {
      const fd = new FormData(); fd.append("file", f);
      const img = await api<{ id: string }>("/images", "POST", fd);
      setErr(""); run().insertContent({ type: "image", attrs: { imageId: img.id, alt: f.name, width: null } }).run();
    } catch (e) { setErr((e as Error).message); }
  };
  const Btn = ({ l, on, fn, dis, t }: { l: string; on?: boolean; fn: () => void; dis?: boolean; t?: string }) =>
    <button type="button" className={on ? "on" : ""} disabled={ro || dis} title={t ?? l} aria-label={t ?? l} aria-pressed={on}
      onMouseDown={(e) => e.preventDefault()} onClick={fn}>{l}</button>;
  const label = { idle: "no changes", saving: "saving…", saved: "draft saved", error: "save failed" }[status];

  return (
    <section>
      <p>
        <input value={title} disabled={ro} aria-label="Title" onChange={(e) => { setTitle(e.target.value); titleRef.current = e.target.value; touch(); }} />{" "}
        <span data-testid="save-status" data-status={status} role="status" style={{ color: status === "error" ? "crimson" : undefined }}>
          {status === "idle" && d.data?.dirty ? "draft saved" : label}</span>
        {status === "error" && <> <button onClick={() => flush()}>Retry</button></>}{" "}
        <button onClick={async () => { await flush(); onCommit(); }}>Commit…</button>{" "}
        {!ro && <button onClick={() => confirm("Discard your draft?") && discard.mutate()}>Discard draft</button>}
      </p>
      {d.data?.conflict && <p role="alert" style={{ color: "darkorange" }}>HEAD moved since your draft started — resolve it on the commit tab.</p>}
      <div className="ed-tb">
        <select aria-label="Paragraph style" disabled={ro} value={st?.style ?? "Normal"} onChange={(e) => applyStyle(e.target.value)}>
          {styles.filter((s) => s.kind === "paragraph" || s.kind === "heading").map((s) => <option key={s.style_id} value={s.style_id}>{s.name}</option>)}
        </select>
        <Btn l="B" t="Bold" on={st?.bold} fn={() => run().toggleBold().run()} />
        <Btn l="I" t="Italic" on={st?.italic} fn={() => run().toggleItalic().run()} />
        <Btn l="U" t="Underline" on={st?.underline} fn={() => run().toggleUnderline().run()} />
        <Btn l="• List" t="Bullet list" on={st?.bullet} fn={() => run().toggleBulletList().run()} />
        <Btn l="1. List" t="Ordered list" on={st?.ordered} fn={() => run().toggleOrderedList().run()} />
        <Btn l="Link" on={st?.link} fn={setLink} />
        <Btn l="Image" fn={() => document.getElementById("img-in")?.click()} />
        <input id="img-in" type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden aria-label="Image file"
          onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
        <Btn l="Table" t="Insert table" fn={() => run().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()} />
        {st?.table && <>
          <Btn l="+Row" fn={() => run().addRowAfter().run()} /><Btn l="+Col" fn={() => run().addColumnAfter().run()} />
          <Btn l="−Row" fn={() => run().deleteRow().run()} /><Btn l="−Col" fn={() => run().deleteColumn().run()} />
          <Btn l="Merge" t="Merge cells" fn={() => run().mergeCells().run()} /><Btn l="Split" t="Split cell" fn={() => run().splitCell().run()} />
          <Btn l="Delete table" fn={() => run().deleteTable().run()} />
          <Btn l={st.dynamic ? `Dynamic: ${st.dynamic}` : "Mark table dynamic"} t="Mark table dynamic" on={!!st.dynamic} fn={markDynamic} />
        </>}
        <Btn l={st?.variable ? "Edit variable" : "Make variable"} t="Make variable" on={st?.variable} fn={openVariable} />
      </div>
      {vf && (
        <form className="ed-tb" onSubmit={(e) => { e.preventDefault(); saveVariable(); }}>
          <input aria-label="Variable name" placeholder="name" value={vf.name} onChange={(e) => setVf({ ...vf, name: e.target.value })} autoFocus />
          <input aria-label="Variable default" placeholder="default" value={vf.default} onChange={(e) => setVf({ ...vf, default: e.target.value })} />
          <select aria-label="Variable filter" value={vf.kind} onChange={(e) => setVf({ ...vf, kind: e.target.value as VForm["kind"] })}>
            <option value="">no filter</option><option value="upper">upper</option><option value="date">date format</option>
          </select>
          {vf.kind === "date" && <input aria-label="Date format" value={vf.format} onChange={(e) => setVf({ ...vf, format: e.target.value })} size={12} />}
          <button>{vf.existing ? "Update" : "Create"} variable</button>
          <button type="button" onClick={() => setVf(null)}>Cancel</button>
        </form>
      )}
      {err && <p role="alert" style={{ color: "crimson" }}>{err}</p>}
      <div className="ed-wrap">
        <div><EditorContent editor={editor} aria-label="Editor" /></div>
        <div><Preview doc={doc} styles={styles} /></div>
      </div>
      <p><small>Styles: {extractStyles(doc).join(", ") || "—"} · Variables: {extractVariables(doc).join(", ") || "—"}</small></p>
      {discard.error && <p role="alert" style={{ color: "crimson" }}>{(discard.error as Error).message}</p>}
    </section>
  );
}
