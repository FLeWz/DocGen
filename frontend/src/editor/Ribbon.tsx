// Ribbon-style toolbar: Home / Insert / Layout / References / Review tabs of small groups, collapsible, with tooltips and shortcuts.
// Used by the document editor and by the header/footer editor (mode="hf": no document-level items). Named styles first (style
// dropdown), direct formatting second (font, size, colour ... go into the `fmt` mark / `pfmt` attr, see fmt.ts).
import type { Editor } from "@tiptap/core";
import { NodeSelection } from "@tiptap/pm/state";
import { useEditorState } from "@tiptap/react";
import { useEffect, useRef, useState } from "react";
import { api, Style } from "../api";
import { Field, Icon, Modal, useUI } from "../ui";
import { clearFormatting, indent, insertBlock, setFmt, setList, setPfmt } from "./commands";
import { effectiveStyle, NAME_RE } from "./doc";
import { revisionTableJSON } from "./revisionTable";
import { findAll, replaceMatches, countWords, type Query } from "./find";
import { captionLabel, entryText } from "./outline";
import { captionExisting, defaultLabel, insertWithCaption, loadCap, saveCap, tableJSON, type CapKind, type CapOpts } from "./captions";
import { textboxLines, withTextboxLines } from "./opaqueText";
import { blockRange } from "./blockrange";
import { insertList, markList, markSection, type ListKind } from "./sections";
import { tableActions, TableTab } from "./TableTools";
import { ctxOf, run as runOp, textToTable } from "./tables";
import { analyzeTable, columnNames, LIST_RE, makeDynamicFromRows, undoDynamic } from "./dynamic";

/** Fields (`row.x` chips) used in the table around the cursor; the last row's chips are the template. */
function rowFields(editor: Editor): string[] {
  const $f = editor.state.selection.$from; let d = $f.depth;
  while (d > 0 && $f.node(d).type.name !== "table") d--;
  const out = new Set<string>(["name"]);
  if ($f.node(d).type.name === "table") $f.node(d).descendants((n) => { if (n.type.name === "variable" && /^row\.\w+$/.test(n.attrs.name)) out.add(n.attrs.name.slice(4)); });
  return [...out];
}

const FONTS = ["Arial", "Calibri", "Cambria", "Courier New", "Georgia", "Times New Roman", "Verdana"];
const SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48];
const LINES: [string, number][] = [["1.0", 240], ["1.15", 276], ["1.5", 360], ["2.0", 480]];
const SPACE = [0, 6, 12, 18, 24];
const BULLETS = [["disc", "● Disc"], ["circle", "○ Circle"], ["square", "■ Square"]];
const NUMBERS = [["decimal", "1. 2. 3."], ["lower-alpha", "a. b. c."], ["upper-alpha", "A. B. C."], ["lower-roman", "i. ii. iii."], ["upper-roman", "I. II. III."], ["decimal-leading-zero", "01. 02."], ["multilevel", "1. 1.1 1.1.1"]];
const SYMBOLS = "©®™°±×÷≠≤≥≈∞µπΩΣ√∂∆←→↑↓↔⇒§¶†‡•…–—‘’“”«»¡¿€£¥¢½¼¾²³¹αβγδελμσφ✓✗★☆♥♦♣♠".split("");
const HF_FIELDS: [string, Record<string, unknown>][] = [
  ["Page number", { type: "field", attrs: { code: "PAGE", arg: "", text: "1" } }], ["Total pages", { type: "field", attrs: { code: "NUMPAGES", arg: "", text: "1" } }],
  ["Section pages", { type: "field", attrs: { code: "SECTIONPAGES", arg: "", text: "1" } }], ["Date", { type: "variable", attrs: { name: "doc.date", default: "", filter: "date:DD.MM.YYYY" } }],
  ["Document title", { type: "variable", attrs: { name: "doc.title", default: "", filter: null } }], ["Version", { type: "variable", attrs: { name: "doc.version", default: "", filter: null } }],
  ["Author", { type: "variable", attrs: { name: "author", default: "", filter: null } }]];
const SHORTCUTS: [string, string][] = [["Bold / italic / underline", "Ctrl+B / I / U"], ["Strikethrough", "Ctrl+Shift+X"], ["Undo / redo", "Ctrl+Z / Ctrl+Y"], ["Select all blocks", "Ctrl+A"], ["Link", "Ctrl+K"],
  ["Find and replace", "Ctrl+F"], ["Line break", "Shift+Enter"], ["Indent / outdent list item", "Tab / Shift+Tab"], ["Insert {{ CONTENT }} (imports)", "Ctrl+Alt+C"], ["Insert caption", "Ctrl+Alt+L"], ["This list", "Ctrl+/"]];
export const BORDERS_SIDE = (val: string) => ({ val, sz: 4, color: "000000" });
const BORDERS_ON = Object.fromEntries(["top", "left", "bottom", "right"].map((k) => [k, BORDERS_SIDE("single")]));
const BORDERS_OFF = Object.fromEntries(["top", "left", "bottom", "right"].map((k) => [k, { val: "nil", sz: 0 }]));

export function Btn({ i, t, label, on, fn, off, flip }: { i: string; t: string; label?: string; on?: boolean; fn: () => void; off?: boolean; flip?: boolean }) {
  return (
    <button type="button" className={`tb ${on ? "on" : ""} ${label ? "wide" : ""}${flip ? " flip" : ""}`} disabled={off} title={t} aria-label={t} aria-pressed={on}
      onMouseDown={(e) => e.preventDefault()} onClick={fn}><Icon n={i} />{label && <span>{label}</span>}</button>
  );
}
const G = ({ label, children }: { label: string; children: React.ReactNode }) => <div className="rgroup" role="group" aria-label={label}><div className="rrow">{children}</div><div className="rlabel">{label}</div></div>;
const num = (v: unknown) => (typeof v === "number" ? v : Number(v) || 0);

type Dlg = null | "var" | "dyn" | "link" | "symbols" | "find" | "shortcuts" | "xref" | "section" | "list" | "caption" | "mark";
type TabId = "home" | "insert" | "layout" | "refs" | "review" | "table";
const TABS: [TabId, string][] = [["home", "Home"], ["insert", "Insert"], ["layout", "Layout"], ["refs", "References"], ["review", "Review"]];
const TABLE_TAB: [TabId, string] = ["table", "Table"];

export function Ribbon({ editor, ro, styles, mode = "body", onErr, structure, onStructure }: {
  editor: Editor; ro: boolean; styles: Style[]; mode?: "body" | "hf"; onErr: (s: string) => void; structure?: boolean; onStructure?: () => void;
}) {
  const ui = useUI(), hf = mode === "hf";
  const [tab, setTab] = useState<TabId>(() => (localStorage.getItem("docgen.ribbon.tab") as TabId) || "home");
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("docgen.ribbon.collapsed") === "1");
  const [dlg, setDlg] = useState<Dlg>(null), [spell, setSpell] = useState(true);
  const pick = (t: TabId) => { setTab(t); setCollapsed(false); localStorage.setItem("docgen.ribbon.tab", t); localStorage.setItem("docgen.ribbon.collapsed", "0"); };
  const toggle = () => { setCollapsed(!collapsed); localStorage.setItem("docgen.ribbon.collapsed", collapsed ? "0" : "1"); };

  const st = useEditorState({
    editor, selector: ({ editor: e }) => {
      const $f = e.state.selection.$from, p = $f.parent, sel = e.state.selection, fmt = e.getAttributes("fmt");
      const inList = [...Array(Math.max($f.depth, 0) + 1).keys()].some((i) => ["bulletList", "orderedList"].includes($f.node(i).type.name));
      const nodeSel = sel instanceof NodeSelection ? sel.node : null, pf = (p.attrs.pfmt ?? {}) as Record<string, any>;
      const text = e.state.doc.textBetween(0, e.state.doc.content.size, " ");
      const list = e.isActive("orderedList") ? e.getAttributes("orderedList") : e.isActive("bulletList") ? e.getAttributes("bulletList") : null;
      return { style: effectiveStyle({ type: p.type.name, attrs: p.attrs }, inList) ?? "Normal", bold: e.isActive("bold"), italic: e.isActive("italic"), underline: e.isActive("underline"), link: e.isActive("link"),
        strike: fmt.strike === true, sup: fmt.va === "superscript", sub: fmt.va === "subscript", font: (fmt.font as string) ?? "", size: fmt.size ? String(fmt.size / 2) : "", color: (fmt.color as string) ?? "", hl: (fmt.hl as string) ?? "",
        jc: (pf.jc as string) ?? "left", line: pf.line ? String(pf.line) : "", before: pf.before != null ? String(Math.round(pf.before / 20)) : "", after: pf.after != null ? String(Math.round(pf.after / 20)) : "",
        bullet: e.isActive("bulletList"), ordered: e.isActive("orderedList"), listStyle: (list?.listStyle as string) ?? "", table: e.isActive("table"), headerRow: e.isActive("tableHeader"),
        dynamic: (e.getAttributes("table").dynamicList as string | null) ?? null, variable: nodeSel?.type.name === "variable", footnote: nodeSel?.type.name === "footnote",
        toc: nodeSel?.type.name === "toc", image: nodeSel?.type.name === "image" ? (nodeSel.attrs as Record<string, any>) : null, canUndo: e.can().undo(), canRedo: e.can().redo(),
        words: countWords(text), chars: text.length, styleBq: p.attrs.styleId === "Quote", styleCode: p.attrs.styleId === "Code" };
    },
  });
  const run = () => editor.chain().focus();
  const off = ro || !editor.isEditable;
  const prevTab = useRef<TabId | null>(null);
  useEffect(() => {  // the contextual Table tab: selected on entering a table, the previous tab comes back on leaving it
    if (st.table && !ro) { if (tab !== "table") { prevTab.current = tab; setTab("table"); setCollapsed(false); } }
    else if (tab === "table") setTab(prevTab.current ?? "home");
  }, [st.table]); // eslint-disable-line react-hooks/exhaustive-deps
  const [lock, setLock] = useState(true);

  // shortcuts that open dialogs (Ctrl+K, Ctrl+F, Ctrl+/) come from the editor as `docgen:cmd`
  useEffect(() => {
    const h = (e: Event) => { const c = (e as CustomEvent).detail; if (!editor.isFocused) return; if (c === "link") setDlg("link"); else if (c === "find") setDlg("find"); else if (c === "shortcuts") setDlg("shortcuts"); else if (c === "dynamic") openDynamic(); else if (c === "caption") openCaption(); };
    window.addEventListener("docgen:cmd", h);
    return () => window.removeEventListener("docgen:cmd", h);
  }, [editor]);
  useEffect(() => { editor.view.dom.setAttribute("spellcheck", String(spell)); }, [editor, spell]);
  const [tb, setTb] = useState<{ pos: number; text: string } | null>(null);  // "Edit text box content" (raised by the opaque node view)
  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent).detail as { editor: Editor; pos: number };
      const n = d.editor === editor ? editor.state.doc.nodeAt(d.pos) : null, lines = n && textboxLines(String(n.attrs.xml ?? ""));
      if (lines) setTb({ pos: d.pos, text: lines.join("\n") });
    };
    window.addEventListener("docgen:textbox", h);
    return () => window.removeEventListener("docgen:textbox", h);
  }, [editor]);
  const saveTextbox = () => {
    const n = tb && editor.state.doc.nodeAt(tb.pos), xml = n && withTextboxLines(String(n.attrs.xml ?? ""), tb!.text.split("\n"));
    if (!n || !xml) { onErr("The text box could not be updated"); setTb(null); return; }
    editor.view.dispatch(editor.state.tr.setNodeMarkup(tb!.pos, undefined, { ...n.attrs, xml }));
    setTb(null);
  };

  // ---- dialog state ----
  const [vf, setVf] = useState({ name: "", default: "", kind: "" as "" | "upper" | "date", format: "DD.MM.YYYY", existing: false });
  const [dynName, setDynName] = useState(""), [dynFields, setDynFields] = useState(""), [href, setHref] = useState("");
  const [dynCols, setDynCols] = useState<{ names: string[]; rows: string[][]; convert: boolean; refused: string } | null>(null);
  const [q, setQ] = useState<Query & { repl: string; idx: number }>({ find: "", regex: false, matchCase: false, repl: "", idx: 0 });
  const [sec, setSec] = useState({ brk: "nextPage", orient: "portrait", top: "", bottom: "", left: "", right: "" });
  const [lst, setLst] = useState({ kind: "toc" as ListKind, title: "", levels: "1-3", label: "Figure" });
  const [cap, setCap] = useState<{ label: string; text: string; pos: "above" | "below" }>({ label: "Figure", text: "", pos: "below" });
  const [mark, setMark] = useState({ kind: "section" as "section" | "appendix" | ListKind, title: "" });
  const [xref, setXref] = useState<{ kind: "REF" | "PAGEREF"; targets: { index: number; text: string; bm: string | null; what: string }[] }>({ kind: "REF", targets: [] });
  const err = (e: unknown) => onErr((e as Error).message ?? String(e));

  const openVariable = () => {
    const sel = editor.state.selection;
    if (sel instanceof NodeSelection && sel.node.type.name === "variable") {
      const a = sel.node.attrs, [k, ...f] = String(a.filter ?? "").split(":");
      setVf({ name: a.name, default: a.default ?? "", kind: (k as "") || "", format: f.join(":") || "DD.MM.YYYY", existing: true });
    } else {
      const txt = editor.state.doc.textBetween(sel.from, sel.to, " ").trim();
      setVf({ name: txt.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "_$1"), default: txt, kind: "", format: "DD.MM.YYYY", existing: false });
    }
    setDlg("var");
  };
  /** Opens the Make dynamic dialog; a table that already has content proposes header cells -> variables, body rows -> default values. */
  const openDynamic = () => {
    const c = ctxOf(editor.state), plan = c && !c.table.attrs.dynamicList ? analyzeTable(c.table) : null;
    setDynName(st.dynamic ?? ""); setDynFields("");
    setDynCols(plan?.ok ? { names: columnNames(plan.headers), rows: plan.rows, convert: plan.headers.some(Boolean) || plan.rows.some((r) => r.some(Boolean)), refused: "" } : plan ? { names: [], rows: [], convert: false, refused: plan.reason } : null);
    setDlg("dyn");
  };
  const makeDynamic = () => {
    const v = dynName.trim(), fields = dynFields.split(",").map((x) => x.trim()).filter(Boolean);
    if (v && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(v)) { onErr("List variable name: letters, digits, _"); return; }
    if (fields.some((f) => !/^[A-Za-z_]\w*$/.test(f))) { onErr("Row fields: letters, digits, _"); return; }
    onErr("");
    if (dynCols?.convert && v) {  // header cells -> columns, body rows -> default values, first body row -> template
      if (dynCols.names.some((n) => !/^[A-Za-z_]\w{0,39}$/.test(n)) || new Set(dynCols.names).size !== dynCols.names.length) { onErr("Column names: unique, letters, digits, _ (max 40)"); return; }
      if (!runOp(editor, makeDynamicFromRows(v, dynCols.names))) { onErr("This table cannot be converted"); return; }
      setDlg(null); return;
    }
    if (!v && st.dynamic && ctxOf(editor.state)?.table.attrs.dynamicRows) { runOp(editor, undoDynamic); setDlg(null); return; } // static again: rows from the defaults
    run().updateAttributes("table", { dynamicList: v || null }).run();
    if (v && fields.length) {  // chips into the last row, replacing what the cells held (the template row)
      const { state } = editor, $f = state.selection.$from;
      let d = $f.depth; while (d > 0 && $f.node(d).type.name !== "table") d--;
      const tbl = $f.node(d), start = $f.before(d), row = tbl.lastChild!;
      let pos = start + 1 + tbl.content.size - row.nodeSize + 1;
      const cells: { pos: number; size: number }[] = [];
      row.forEach((c) => { cells.push({ pos, size: c.nodeSize }); pos += c.nodeSize; });
      const tr = state.tr, S = state.schema;
      for (let i = Math.min(fields.length, cells.length) - 1; i >= 0; i--)
        tr.replaceWith(cells[i].pos + 1, cells[i].pos + cells[i].size - 1, S.nodes.paragraph.create(null, S.nodes.variable.create({ name: `row.${fields[i]}`, default: "", filter: null })));
      editor.view.dispatch(tr);
    }
    setDlg(null);
  };
  const saveVariable = () => {
    if (!NAME_RE.test(vf.name)) { onErr("Variable name: letters, digits, _ (optionally row.field)"); return; }
    onErr("");
    const attrs = { name: vf.name, default: vf.default, filter: vf.kind === "date" ? `date:${vf.format}` : vf.kind || null };
    if (vf.existing) run().updateAttributes("variable", attrs).run(); else run().insertContent({ type: "variable", attrs }).run();
    setDlg(null);
  };
  const upload = async (f?: File) => {
    if (!f) return;
    try {
      const fd = new FormData(); fd.append("file", f);
      const img = await api<{ id: string }>("/images", "POST", fd);
      onErr(""); if (hf) run().insertContent({ type: "image", attrs: { imageId: img.id, alt: f.name, width: null } }).run(); else openIns({ kind: "figure", id: img.id, name: f.name });
    } catch (e) { err(e); }
  };
  const cellFmt = (patch: Record<string, unknown>) => {
    const { selection } = editor.state, cells: { pos: number; attrs: Record<string, unknown> }[] = [];
    editor.state.doc.nodesBetween(selection.from, selection.to, (n, pos) => { if (n.type.name === "tableCell" || n.type.name === "tableHeader") { cells.push({ pos, attrs: n.attrs }); return false; } });
    if (!cells.length) { const $f = selection.$from; for (let d = $f.depth; d > 0; d--) if (["tableCell", "tableHeader"].includes($f.node(d).type.name)) { cells.push({ pos: $f.before(d), attrs: $f.node(d).attrs }); break; } }
    const tr = editor.state.tr;
    for (const c of cells) tr.setNodeMarkup(c.pos, undefined, { ...c.attrs, cfmt: { ...((c.attrs.cfmt as object) ?? {}), ...patch } });
    editor.view.dispatch(tr); editor.view.focus();
  };
  const applyStyle = (sid: string) => {
    const s = styles.find((x) => x.style_id === sid);
    if (!s) return;
    if (s.kind === "heading" && (s.level ?? 9) <= 4) run().setNode("heading", { level: s.level, styleId: s.style_id }).run(); else run().setNode("paragraph", { styleId: s.style_id }).run();
  };
  const hasStyle = (id: string) => styles.some((s) => s.style_id === id);
  const toggleStyle = (id: string, on: boolean) => (on ? run().setNode("paragraph", { styleId: null }).run() : run().setNode("paragraph", { styleId: id }).run());
  const listStyle = (kind: "bulletList" | "orderedList", style: string) => {
    if (!style) return;
    if (!editor.isActive(kind)) (kind === "bulletList" ? run().toggleBulletList() : run().toggleOrderedList()).run();
    setList(editor, { listStyle: style });
  };
  const startAt = async () => {
    const v = await ui.prompt({ title: "Start numbering at", label: "Number", value: "1", ok: "Apply" });
    const n = Number(v);
    if (v !== null && Number.isInteger(n) && n >= 1 && n <= 9999) setList(editor, { start: n, continue: false });
  };
  const hr = () => insertBlock(editor, { type: "paragraph", attrs: { pfmt: { bdr: { bottom: { val: "single", sz: 8, color: "808080" } }, after: 120 } } });
  const footnote = async (kind: "footnote" | "endnote", existing = false) => {
    const cur = existing && editor.state.selection instanceof NodeSelection ? String(editor.state.selection.node.attrs.text ?? "") : "";
    const t = await ui.prompt({ title: existing ? "Edit note" : kind === "footnote" ? "Insert footnote" : "Insert endnote", label: "Note text", value: cur, ok: existing ? "Save" : "Insert" });
    if (t === null || !t.trim()) return;
    if (existing) run().updateAttributes("footnote", { text: t.trim() }).run(); else run().insertContent({ type: "footnote", attrs: { kind, text: t.trim() } }).run();
  };
  const bookmark = async () => {
    const t = editor.state.selection.$from.parent;
    const name = (await ui.prompt({ title: "Bookmark", label: "Name", hint: "Letters, digits and _ (starts with a letter). Empty removes the bookmark of this paragraph.", value: String(t.attrs.bm ?? ""), ok: "Set" }))?.trim();
    if (name === undefined) return;
    if (name && !/^[A-Za-z_][A-Za-z0-9_]{0,39}$/.test(name)) { onErr("Bookmark names: letters, digits, _ (max 40, not starting with a digit)"); return; }
    run().updateAttributes(t.type.name, { bm: name || null }).run();
  };
  const openXref = () => {
    const targets: typeof xref.targets = [];
    editor.state.doc.forEach((n, _o, i) => {
      const j = n.toJSON(), cap = captionLabel(j);
      if (n.type.name === "heading" || cap || n.attrs.bm) targets.push({ index: i, text: entryText(j) || String(n.attrs.bm), bm: (n.attrs.bm as string) ?? null, what: n.type.name === "heading" ? `Heading ${n.attrs.level}` : cap ?? "Bookmark" });
    });
    setXref({ kind: "REF", targets }); setDlg("xref");
  };
  const insertXref = (t: (typeof xref.targets)[number]) => {
    let bm = t.bm;
    if (!bm) {  // headings and captions get an automatic bookmark (_Ref + number), like Word's cross-reference dialog
      bm = `_Ref${Math.floor(Math.random() * 9e8 + 1e8)}`;
      let at = -1; editor.state.doc.forEach((_n, pos, i) => { if (i === t.index) at = pos; });
      const n = editor.state.doc.nodeAt(at)!;
      editor.view.dispatch(editor.state.tr.setNodeMarkup(at, undefined, { ...n.attrs, bm }));
    }
    run().insertContent({ type: "field", attrs: { code: xref.kind, arg: bm, text: xref.kind === "REF" ? t.text : "1" } }).run();
    setDlg(null);
  };
  const openCaption = () => { const k: CapKind = st.table || editor.isActive("table") ? "table" : "figure"; setCap({ label: defaultLabel(k), text: "", pos: loadCap(k).pos }); setDlg("caption"); };
  const insertCaption = () => { captionExisting(editor, cap); setDlg(null); };
  // Insert > Table / Image: a dialog with the size or file and the caption options (the checkbox lives here, not in the editor area)
  const [ins, setIns] = useState<null | { kind: "table"; rows: number; cols: number } | { kind: "figure"; id: string; name: string }>(null);
  const [copt, setCopt] = useState<CapOpts>(() => loadCap("table"));
  const openIns = (i: NonNullable<typeof ins>) => { setCopt(loadCap(i.kind)); setIns(i); };
  const doInsert = () => {
    if (!ins) return;
    const k: CapKind = ins.kind;
    if (!hf) saveCap(k, copt);
    insertWithCaption(editor, ins.kind === "table" ? tableJSON(Math.min(Math.max(ins.rows, 1), 50), Math.min(Math.max(ins.cols, 1), 20)) : { type: "image", attrs: { imageId: ins.id, alt: ins.name, width: null } }, { ...copt, on: copt.on && !hf });
    setIns(null);
  };
  const insertSection = () => {
    const tw = (s: string) => { const n = Math.round(Number(s) * 567); return s.trim() && Number.isFinite(n) && n >= 0 && n <= 10000 ? n : null; }; // cm -> twips
    insertBlock(editor, { type: "sectBreak", attrs: { brk: sec.brk, orient: sec.orient, top: tw(sec.top), bottom: tw(sec.bottom), left: tw(sec.left), right: tw(sec.right) } });
    setDlg(null);
  };
  const doMark = () => {
    if (mark.kind === "section" || mark.kind === "appendix") { if (!mark.title.trim()) { onErr("Give the section a name"); return; } markSection(editor, mark.kind, mark.title); }
    else markList(editor, mark.kind, { title: mark.title });
    setDlg(null);
  };
  const matches = () => findAll(editor.state.doc, q);
  const goto = (dir: 1 | -1) => {
    const ms = matches();
    if (!ms.length) return;
    const from = editor.state.selection.to, i = dir > 0 ? Math.max(ms.findIndex((m) => m.from >= from), 0) : Math.max(ms.map((m) => m.to).reverse().findIndex((t) => t < editor.state.selection.from), 0);
    const m = dir > 0 ? ms[i] : ms[ms.length - 1 - i];
    editor.chain().setTextSelection({ from: m.from, to: m.to }).scrollIntoView().run();
  };
  const replaceOne = () => {
    const ms = matches(), sel = editor.state.selection, m = ms.find((x) => x.from === sel.from && x.to === sel.to);
    if (m) { editor.view.dispatch(replaceMatches(editor.state.tr, [m], q, q.repl)); goto(1); } else goto(1);
  };
  const replaceAll = () => { const ms = matches(); if (ms.length) { editor.view.dispatch(replaceMatches(editor.state.tr, ms, q, q.repl)); ui.toast(`Replaced ${ms.length}`); } };

  const I = (p: Parameters<typeof Btn>[0]) => <Btn {...p} off={p.off ?? off} />;

  const home = <>
    <G label="Undo">
      <I i="undo" t="Undo (Ctrl+Z)" off={off || !st.canUndo} fn={() => run().undo().run()} /><I i="redo" t="Redo (Ctrl+Y)" off={off || !st.canRedo} fn={() => run().redo().run()} />
    </G>
    <G label="Style">
      <select aria-label="Paragraph style" title="Paragraph style" disabled={off} value={st.style} onChange={(e) => applyStyle(e.target.value)}>
        {styles.filter((s) => s.kind === "paragraph" || s.kind === "heading").map((s) => <option key={s.style_id} value={s.style_id}>{s.name}</option>)}</select>
    </G>
    <G label="Font">
      <select aria-label="Font family" title="Font family" disabled={off} value={st.font} onChange={(e) => setFmt(editor, { font: e.target.value || null })}><option value="">Style font</option>{FONTS.map((f) => <option key={f}>{f}</option>)}</select>
      <select aria-label="Font size" title="Font size (pt)" disabled={off} value={st.size} onChange={(e) => setFmt(editor, { size: e.target.value ? Number(e.target.value) * 2 : null })}><option value="">Size</option>{SIZES.map((s) => <option key={s} value={s}>{s}</option>)}</select>
      <I i="bold" t="Bold (Ctrl+B)" on={st.bold} fn={() => run().toggleBold().run()} /><I i="italic" t="Italic (Ctrl+I)" on={st.italic} fn={() => run().toggleItalic().run()} /><I i="underline" t="Underline (Ctrl+U)" on={st.underline} fn={() => run().toggleUnderline().run()} />
      <I i="strike" t="Strikethrough (Ctrl+Shift+X)" on={st.strike} fn={() => setFmt(editor, { strike: st.strike ? null : true })} />
      <I i="sup" t="Superscript" on={st.sup} fn={() => setFmt(editor, { va: st.sup ? null : "superscript" })} /><I i="sub" t="Subscript" on={st.sub} fn={() => setFmt(editor, { va: st.sub ? null : "subscript" })} />
      <input type="color" className="tb-color" aria-label="Text colour" title="Text colour" disabled={off} value={`#${st.color || "000000"}`} onChange={(e) => setFmt(editor, { color: e.target.value.slice(1).toUpperCase() })} />
      <input type="color" className="tb-color" aria-label="Highlight colour" title="Highlight colour" disabled={off} value={`#${st.hl || "FFFF00"}`} onChange={(e) => setFmt(editor, { hl: e.target.value.slice(1).toUpperCase() })} />
      <I i="x" t="Clear text colour and highlight" fn={() => setFmt(editor, { color: null, hl: null })} />
      <I i="clear" t="Clear formatting" fn={() => clearFormatting(editor)} />
    </G>
    <G label="Paragraph">
      <I i="ul" t="Bulleted list" on={st.bullet} fn={() => run().toggleBulletList().run()} />
      <select aria-label="Bullet style" title="Bullet style" disabled={off} value={st.bullet ? st.listStyle : ""} onChange={(e) => listStyle("bulletList", e.target.value)}><option value="">Bullets…</option>{BULLETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <I i="ol" t="Numbered list" on={st.ordered} fn={() => run().toggleOrderedList().run()} />
      <select aria-label="Numbering style" title="Numbering style (multi-level: 1. 1.1 1.1.1)" disabled={off} value={st.ordered ? st.listStyle : ""} onChange={(e) => listStyle("orderedList", e.target.value)}><option value="">Numbering…</option>{NUMBERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <I i="restart" t="Restart numbering at 1" off={off || !st.ordered} fn={() => setList(editor, { start: 1, continue: false })} /><I i="continue" t="Continue numbering of the previous list" off={off || !st.ordered} fn={() => setList(editor, { continue: true })} />
      <I i="hash" t="Start numbering at…" off={off || !st.ordered} fn={startAt} />
      <I i="outdent" t="Decrease indent" fn={() => indent(editor, -1)} /><I i="indent" t="Increase indent" fn={() => indent(editor, 1)} />
      {([["left", "left", "Align left"], ["center", "center", "Align center"], ["right", "right", "Align right"], ["both", "justify", "Justify"]] as const).map(([jc, i, t]) =>
        <I key={jc} i={i} t={t} on={st.jc === jc} fn={() => setPfmt(editor, { jc })} />)}
      <select aria-label="Line spacing" title="Line spacing" disabled={off} value={st.line} onChange={(e) => setPfmt(editor, { line: e.target.value ? Number(e.target.value) : null, lineRule: e.target.value ? "auto" : null })}><option value="">Line</option>{LINES.map(([l, v]) => <option key={v} value={v}>{l}</option>)}</select>
      <select aria-label="Space before" title="Space before (pt)" disabled={off} value={st.before} onChange={(e) => setPfmt(editor, { before: e.target.value === "" ? null : Number(e.target.value) * 20 })}><option value="">Before</option>{SPACE.map((s) => <option key={s} value={s}>{s} pt</option>)}</select>
      <select aria-label="Space after" title="Space after (pt)" disabled={off} value={st.after} onChange={(e) => setPfmt(editor, { after: e.target.value === "" ? null : Number(e.target.value) * 20 })}><option value="">After</option>{SPACE.map((s) => <option key={s} value={s}>{s} pt</option>)}</select>
    </G>
    <G label="Blocks">
      <I i="quote" t={hasStyle("Quote") ? "Block quote (style Quote)" : "Block quote: this style set has no Quote style"} on={st.styleBq} off={off || !hasStyle("Quote")} fn={() => toggleStyle("Quote", st.styleBq)} />
      <I i="code" t={hasStyle("Code") ? "Code block (style Code)" : "Code block: this style set has no Code style"} on={st.styleCode} off={off || !hasStyle("Code")} fn={() => toggleStyle("Code", st.styleCode)} />
      <I i="codeInline" t="Inline code" fn={() => setFmt(editor, { font: "Courier New", hl: "F2F2F2" })} />
      <I i="link" t="Link (Ctrl+K)" on={st.link} fn={() => { setHref(editor.getAttributes("link").href ?? "https://"); setDlg("link"); }} />
      <I i="var" t="Make variable" label={st.variable ? "Edit variable" : "Variable"} on={st.variable} fn={openVariable} />
    </G>
  </>;

  // "Make dynamic table": whenever the cursor is in a table (Insert and Layout tabs)
  const dynBtn = <I i="refresh" t="Make dynamic table: the rows come from a list variable (the last row is the template)" label={st.dynamic ? `Dynamic: ${st.dynamic}` : "Dynamic table"} on={!!st.dynamic} off={off || !st.table || hf}
    fn={openDynamic} />;

  const insert = <>
    <G label="Tables">
      <I i="table" t="Insert table 3×3" fn={() => hf ? run().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() : openIns({ kind: "table", rows: 3, cols: 3 })} />
      <select className="tb-sel tb-size" aria-label="Insert table with size" disabled={off} value="" onChange={(e) => { const [r, c] = e.target.value.split("x").map(Number); if (r) hf ? run().insertTable({ rows: r, cols: c, withHeaderRow: true }).run() : openIns({ kind: "table", rows: r, cols: c }); }}>
        <option value="">Rows × cols</option>{["2x2", "3x2", "3x3", "4x3", "5x4", "6x5", "8x6", "10x4"].map((v) => <option key={v} value={v}>{v.replace("x", " × ")}</option>)}</select>
      <I i="table" t="Convert the selected paragraphs to a table (columns split at tabs)" label="Text to table" fn={() => { if (!tableActions(editor, ui).r(textToTable)) onErr("Select some paragraphs outside a table first"); }} />
      {dynBtn}
      <I i="table" t="Insert the revision table: one row per revision (date, revision, author, sections changed, commit), filled from the document's history" label="Revision table" off={off || hf} fn={() => run().insertContent(revisionTableJSON()).run()} />
    </G>
    <G label="Illustrations">
      <I i="image" t="Insert image from file" fn={() => document.getElementById(hf ? "img-in-hf" : "img-in")?.click()} />
      <input id={hf ? "img-in-hf" : "img-in"} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden aria-label="Image file" onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
      {st.image && <>
        <input type="number" aria-label="Image width (px)" title="Width (px)" min={8} max={1200} value={num(st.image.width) || ""} onChange={(e) => { const w = Number(e.target.value); if (w >= 8) { const a = st.image!, r = num(a.width) && num(a.height) ? num(a.height) / num(a.width) : 0; run().updateAttributes("image", { width: w, ...(lock && r ? { height: Math.round(w * r) } : {}) }).run(); } }} />
        <I i="lock" t={lock ? "Aspect ratio locked" : "Aspect ratio unlocked"} on={lock} fn={() => setLock(!lock)} />
        <I i="edit" t="Alt text" fn={async () => { const t = await ui.prompt({ title: "Alt text", label: "Description", value: String(st.image?.alt ?? ""), ok: "Save" }); if (t !== null) run().updateAttributes("image", { alt: t.slice(0, 200) }).run(); }} />
        {(["left", "center", "right"] as const).map((a) => <I key={a} i={a} t={`Image ${a}`} on={(st.image?.align ?? "left") === a} fn={() => run().updateAttributes("image", { align: a }).run()} />)}
      </>}
    </G>
    <G label="Symbols">
      <I i="hr" t="Horizontal rule" fn={hr} /><I i="omega" t="Symbols and special characters" fn={() => setDlg("symbols")} /><I i="nbsp" t="Non-breaking space" fn={() => run().insertContent("\u00a0").run()} />
      <I i="br" t="Line break (Shift+Enter)" fn={() => run().setHardBreak().run()} />
    </G>
    <G label="Pages">
      <I i="pagebreak" t="Page break" fn={() => run().insertContent({ type: "hardBreak", attrs: { page: true } }).run()} />
      {!hf && <I i="sectbreak" t="Section break (orientation, margins)" label="Section break" fn={() => setDlg("section")} />}
    </G>
    <G label={hf ? "Fields" : "Notes and fields"}>
      {!hf && <><I i="footnote" t="Insert footnote" label="Footnote" fn={() => footnote("footnote")} /><I i="footnote" t="Insert endnote" label="Endnote" fn={() => footnote("endnote")} />
        {st.footnote && <I i="edit" t="Edit selected note" label="Edit note" fn={() => footnote("footnote", true)} />}</>}
      <select aria-label="Insert field" title="Insert field: page number, total pages, date, document title, version, author" disabled={off} value="" onChange={(e) => { const f = HF_FIELDS[Number(e.target.value)]; if (f) run().insertContent(f[1]).run(); }}>
        <option value="">Field…</option>{HF_FIELDS.map(([l], i) => <option key={l} value={i}>{l}</option>)}</select>
      <I i="var" t="Make variable" label="Variable" on={st.variable} fn={openVariable} />
    </G>
    {!hf && <G label="Generated">
      <I i="toc" t="Insert table of contents" label="Contents" fn={() => { setLst({ kind: "toc", title: "Contents", levels: "1-3", label: "Figure" }); setDlg("list"); }} />
      <I i="toc" t="Insert list of figures" label="Figures" fn={() => { setLst({ kind: "tof", title: "List of figures", levels: "1-3", label: "Figure" }); setDlg("list"); }} />
      <I i="toc" t="Insert list of tables" label="Tables" fn={() => { setLst({ kind: "tot", title: "List of tables", levels: "1-3", label: "Table" }); setDlg("list"); }} />
    </G>}
  </>;

  const layout = <>
    <G label="Paragraph">
      <I i="pagebreak" t="Start the paragraph on a new page" on={false} fn={() => setPfmt(editor, (c) => ({ pageBreakBefore: c.pageBreakBefore ? null : true }))} label="Page before" />
      <I i="keep" t="Keep with next paragraph" fn={() => setPfmt(editor, (c) => ({ keepNext: c.keepNext ? null : true }))} label="Keep with next" />
    </G>
    <G label="Table">
      <I i="rowAdd" t="Add row below" off={off || !st.table} fn={() => run().addRowAfter().run()} /><I i="colAdd" t="Add column right" off={off || !st.table} fn={() => run().addColumnAfter().run()} />
      <I i="rowDel" t="Delete row" off={off || !st.table} fn={() => run().deleteRow().run()} /><I i="colDel" t="Delete column" off={off || !st.table} fn={() => run().deleteColumn().run()} />
      <I i="rowAdd" t="Toggle header row" label="Header row" on={st.headerRow} off={off || !st.table} fn={() => run().toggleHeaderRow().run()} />
      <I i="merge" t="Merge cells" off={off || !st.table} fn={() => run().mergeCells().run()} /><I i="split" t="Split cell" off={off || !st.table} fn={() => run().splitCell().run()} />
      <I i="trash" t="Delete table" off={off || !st.table} fn={() => run().deleteTable().run()} />
      {dynBtn}
    </G>
    <G label="Cells">
      <input type="color" className="tb-color" aria-label="Cell shading" title="Cell shading" disabled={off || !st.table} defaultValue="#d9e2f3" onChange={(e) => cellFmt({ shd: e.target.value.slice(1).toUpperCase() })} />
      <I i="table" t="Borders on selected cells" label="Borders" off={off || !st.table} fn={() => cellFmt({ bdr: BORDERS_ON })} /><I i="table" t="Remove borders of selected cells" label="No borders" off={off || !st.table} fn={() => cellFmt({ bdr: BORDERS_OFF })} />
      <select aria-label="Cell vertical alignment" title="Cell vertical alignment" disabled={off || !st.table} value="" onChange={(e) => e.target.value && cellFmt({ vAlign: e.target.value })}><option value="">Vertical…</option><option value="top">Top</option><option value="center">Middle</option><option value="bottom">Bottom</option></select>
      <select aria-label="Table alignment" title="Table alignment" disabled={off || !st.table} value="" onChange={(e) => { const t = editor.getAttributes("table"); run().updateAttributes("table", { tfmt: { ...(t.tfmt ?? {}), jc: e.target.value } }).run(); }}><option value="">Table…</option><option value="left">Left</option><option value="center">Centre</option><option value="right">Right</option></select>
      <input type="number" aria-label="Column width (px)" title="Column width (px)" min={20} max={1200} disabled={off || !st.table} placeholder="Col px" onChange={(e) => { const w = Number(e.target.value); if (w >= 20) run().setCellAttribute("colwidth", [w]).run(); }} />
    </G>
  </>;

  const refs = <>
    {!hf && <G label="Contents">
      <I i="toc" t="Insert table of contents" label="Table of contents" fn={() => { setLst({ kind: "toc", title: "Contents", levels: "1-3", label: "Figure" }); setDlg("list"); }} />
      <I i="toc" t="Insert list of figures" label="Figures" fn={() => { setLst({ kind: "tof", title: "List of figures", levels: "1-3", label: "Figure" }); setDlg("list"); }} />
      <I i="toc" t="Insert list of tables" label="Tables" fn={() => { setLst({ kind: "tot", title: "List of tables", levels: "1-3", label: "Table" }); setDlg("list"); }} />
      {st.toc && <I i="edit" t="Options of the selected list" label="List options" fn={() => { const a = (editor.state.selection as NodeSelection).node.attrs; setLst({ kind: a.kind, title: a.title ?? "", levels: a.levels ?? "1-3", label: a.label ?? "Figure" }); setDlg("list"); }} />}
    </G>}
    <G label="Captions">
      <I i="caption" t="Insert caption (Figure/Table, SEQ numbering, style Caption)" label="Caption" fn={openCaption} />
    </G>
    <G label="Links">
      <I i="xref" t="Cross-reference to a heading, caption or bookmark" label="Cross-reference" off={off || hf} fn={openXref} />
      <I i="bookmark" t="Bookmark on this paragraph" label="Bookmark" off={off || hf} fn={bookmark} />
      <I i="link" t="Hyperlink (Ctrl+K)" on={st.link} fn={() => { setHref(editor.getAttributes("link").href ?? "https://"); setDlg("link"); }} />
    </G>
    {!hf && <G label="Sections">
      <I i="mark" t="Mark the selected blocks as a section, appendix or generated list" label="Mark as…" fn={() => { setMark({ kind: "section", title: "" }); setDlg("mark"); }} />
      {onStructure && <I i="outline" t="Show the structure outline" label="Structure" on={structure} off={false} fn={onStructure} />}
    </G>}
    {!hf && <G label="Notes">
      <I i="footnote" t="Insert footnote" label="Footnote" fn={() => footnote("footnote")} /><I i="footnote" t="Insert endnote" label="Endnote" fn={() => footnote("endnote")} />
    </G>}
  </>;

  const review = <>
    <G label="Find"><I i="search" t="Find and replace (Ctrl+F)" label="Find & replace" off={false} fn={() => setDlg("find")} /></G>
    <G label="Proofing">
      <I i="spell" t="Browser spell check on/off" label="Spelling" on={spell} off={false} fn={() => setSpell(!spell)} />
      <span className="small muted" data-testid="word-count" title="Words and characters of this document">{st.words} words · {st.chars} characters</span>
    </G>
    <G label="Help"><I i="keyboard" t="Keyboard shortcuts (Ctrl+/)" label="Shortcuts" off={false} fn={() => setDlg("shortcuts")} /></G>
    <G label="Comments"><span className="small muted" title="Comments of an imported DOCX are kept and written back; adding new ones is not supported.">Existing comments are kept</span></G>
  </>;

  const tabs = st.table && !ro ? [...TABS, TABLE_TAB] : TABS, shown = tab === "table" && !(st.table && !ro) ? "home" : tab;
  const table = <TableTab editor={editor} ui={ui} off={off} styles={styles} onCaption={() => { setCap({ label: "Table", text: "", pos: loadCap("table").pos }); setDlg("caption"); }} />;
  const body = { home, insert, layout, refs, review, table }[shown];
  return (
    <div className="ribbon" data-testid="ribbon" onKeyDown={(e) => e.stopPropagation()}>
      <div className="rtabs" role="tablist" aria-label="Ribbon">
        {tabs.map(([id, label]) => <button key={id} type="button" role="tab" id={`rtab-${hf ? "hf-" : ""}${id}`} className={`rtab ${id === "table" ? "ctx" : ""}`} aria-selected={shown === id} aria-controls={`rbody-${hf ? "hf" : "doc"}`} onClick={() => pick(id)}>{label}</button>)}
        <span style={{ flex: 1 }} />
        <button type="button" className="tb" title={collapsed ? "Expand the ribbon" : "Collapse the ribbon"} aria-label={collapsed ? "Expand ribbon" : "Collapse ribbon"} aria-expanded={!collapsed} onClick={toggle}><Icon n={collapsed ? "chevD" : "chevU"} /></button>
      </div>
      <div className={`rbody ${collapsed ? "collapsed" : ""}`} role="tabpanel" id={`rbody-${hf ? "hf" : "doc"}`} aria-label={tabs.find((t) => t[0] === shown)?.[1]}>{body}</div>

      {dlg === "var" && <Modal title={vf.existing ? "Edit variable" : "Make variable"} onClose={() => setDlg(null)} actions={<><button className="btn" type="button" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" form="var-form">{vf.existing ? "Update" : "Create"} variable</button></>}>
        <form id="var-form" className="stack" onSubmit={(e) => { e.preventDefault(); saveVariable(); }}>
          <Field label="Variable name" hint={st.dynamic ? `Inside the dynamic table ${st.dynamic} use row.field` : "Letters, digits and _ (e.g. client_name)"}>{(id) => <input id={id} value={vf.name} onChange={(e) => setVf({ ...vf, name: e.target.value })} autoFocus />}</Field>
          {st.dynamic && <div className="row-chips" data-testid="row-chips" role="group" aria-label="Row fields">{rowFields(editor).map((f) => <button key={f} type="button" className="btn sm" onClick={() => setVf({ ...vf, name: f })}>{`row.${f}`}</button>)}</div>}
          <Field label="Variable default" hint="Shown in the preview when the document gives no value">{(id) => <input id={id} value={vf.default} onChange={(e) => setVf({ ...vf, default: e.target.value })} />}</Field>
          <Field label="Variable filter">{(id) => <select id={id} value={vf.kind} onChange={(e) => setVf({ ...vf, kind: e.target.value as typeof vf.kind })}><option value="">No filter</option><option value="upper">UPPERCASE</option><option value="date">Date format</option></select>}</Field>
          {vf.kind === "date" && <Field label="Date format" hint="e.g. DD.MM.YYYY">{(id) => <input id={id} value={vf.format} onChange={(e) => setVf({ ...vf, format: e.target.value })} />}</Field>}
        </form></Modal>}
      {dlg === "link" && <Modal title="Link" onClose={() => setDlg(null)} actions={<><button className="btn" type="button" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" form="link-form">{href.trim() ? "Apply link" : "Remove link"}</button></>}>
        <form id="link-form" onSubmit={(e) => { e.preventDefault(); const h = href.trim(); if (!h) run().extendMarkRange("link").unsetLink().run(); else run().extendMarkRange("link").setLink({ href: h }).run(); setDlg(null); }}>
          <Field label="Link URL" hint="http, https or mailto. Empty removes the link.">{(id) => <input id={id} value={href} onChange={(e) => setHref(e.target.value)} autoFocus />}</Field></form></Modal>}
      {dlg === "dyn" && <Modal title="Dynamic table" size="wide" onClose={() => setDlg(null)} actions={<>
        {st.dynamic && <button className="btn" type="button" data-testid="undo-dynamic" title="Rebuild the static rows from the default values" onClick={() => { runOp(editor, undoDynamic); setDlg(null); }}>Undo dynamic</button>}
        <button className="btn" type="button" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" form="dyn-form">{dynName.trim() ? "Make dynamic" : "Make static"}</button></>}>
        <form id="dyn-form" className="stack" onSubmit={(e) => { e.preventDefault(); makeDynamic(); }}>
          <p className="muted">The rows of a dynamic table come from a list variable. The <b>last row is the row template</b>: its cells use <code>{"{{ row.field }}"}</code> variable chips (or that text). Leave the list name empty to make the table static again.</p>
          <Field label="List variable" hint="Letters, digits, _ (e.g. team)">{(id) => <input id={id} value={dynName} onChange={(e) => setDynName(e.target.value)} autoFocus />}</Field>
          {dynCols?.refused && <p className="notice warn" role="alert" data-testid="dyn-refused">{dynCols.refused}</p>}
          {dynCols && !dynCols.refused && <div data-testid="dyn-convert">
            <label className="check"><input type="checkbox" checked={dynCols.convert} onChange={(e) => setDynCols({ ...dynCols, convert: e.target.checked })} /> Use the table's content: header row → variables, {dynCols.rows.length} body row{dynCols.rows.length === 1 ? "" : "s"} → default values</label>
            {dynCols.convert && <table className="tbl" aria-label="Column variables"><thead><tr><th>Column</th><th>Variable (editable)</th><th>First default value</th></tr></thead><tbody>
              {dynCols.names.map((n, i) => <tr key={i}><td>{i + 1}</td><td><input aria-label={`Variable of column ${i + 1}`} value={n} onChange={(e) => setDynCols({ ...dynCols, names: dynCols.names.map((x, j) => (j === i ? e.target.value : x)) })} /></td><td className="muted">{dynCols.rows[0]?.[i] ?? "—"}</td></tr>)}</tbody></table>}
            {dynCols.convert && <p className="muted small">The header row stays static. The first body row stays as the formatted template; the other rows are removed from the table and kept as default values of <code>{dynName.trim() || "the list"}</code> (shown under Base document &gt; variables after “Use as base document”; used when a document gives no rows).</p>}
          </div>}
          {!(dynCols?.convert) && <Field label="Row fields (optional)" hint="Comma separated, e.g. name, email: fills the cells of the last row, left to right, with row.name, row.email chips">{(id) => <input id={id} value={dynFields} onChange={(e) => setDynFields(e.target.value)} placeholder="name, email" />}</Field>}</form></Modal>}
      {dlg === "symbols" && <Modal title="Symbols and special characters" onClose={() => setDlg(null)}><div className="symgrid">{SYMBOLS.map((c) => <button key={c} type="button" title={`U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}`} onClick={() => { run().insertContent(c).run(); }}>{c}</button>)}</div></Modal>}
      {dlg === "shortcuts" && <Modal title="Keyboard shortcuts" onClose={() => setDlg(null)}><table className="tbl"><tbody>{SHORTCUTS.map(([a, k]) => <tr key={a}><td>{a}</td><td><span className="kbd">{k}</span></td></tr>)}</tbody></table></Modal>}
      {dlg === "find" && <Modal title="Find and replace" onClose={() => setDlg(null)} actions={<button className="btn" type="button" onClick={() => setDlg(null)}>Close</button>}>
        <div className="stack">
          <Field label="Find">{(id) => <input id={id} value={q.find} autoFocus onChange={(e) => setQ({ ...q, find: e.target.value })} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); goto(e.shiftKey ? -1 : 1); } }} />}</Field>
          <Field label="Replace with">{(id) => <input id={id} value={q.repl} onChange={(e) => setQ({ ...q, repl: e.target.value })} />}</Field>
          <div className="row">
            <button type="button" className={`btn sm ${q.regex ? "on" : ""}`} aria-pressed={q.regex} onClick={() => setQ({ ...q, regex: !q.regex })}>Regex</button>
            <button type="button" className={`btn sm ${q.matchCase ? "on" : ""}`} aria-pressed={q.matchCase} onClick={() => setQ({ ...q, matchCase: !q.matchCase })}>Match case</button>
            <span className="muted small" data-testid="find-count">{q.find ? `${matches().length} match${matches().length === 1 ? "" : "es"}` : ""}</span>
          </div>
          <div className="row"><button type="button" className="btn" onClick={() => goto(-1)}>Previous</button><button type="button" className="btn" onClick={() => goto(1)}>Next</button>
            <button type="button" className="btn" disabled={off} onClick={replaceOne}>Replace</button><button type="button" className="btn primary" disabled={off} onClick={replaceAll}>Replace all</button></div>
        </div></Modal>}
      {dlg === "xref" && <Modal title="Cross-reference" onClose={() => setDlg(null)}>
        <div className="stack">
          <div className="row"><button type="button" className={`btn sm ${xref.kind === "REF" ? "on" : ""}`} onClick={() => setXref({ ...xref, kind: "REF" })}>Text</button><button type="button" className={`btn sm ${xref.kind === "PAGEREF" ? "on" : ""}`} onClick={() => setXref({ ...xref, kind: "PAGEREF" })}>Page number</button></div>
          {xref.targets.length === 0 ? <p className="muted">No headings, captions or bookmarks yet.</p> : <ul className="structure">{xref.targets.map((t) => <li key={t.index}><span className="kind">{t.what}</span><button type="button" className="grow btn ghost" onClick={() => insertXref(t)}>{t.text}</button></li>)}</ul>}
        </div></Modal>}
      <datalist id="cap-labels"><option value="Table" /><option value="Figure" /><option value="Listing" /></datalist>
      {ins && <Modal title={ins.kind === "table" ? "Insert table" : "Insert figure"} size="sm" onClose={() => setIns(null)} actions={<><button className="btn" type="button" onClick={() => setIns(null)}>Cancel</button><button className="btn primary" form="ins-form">Insert</button></>}>
        <form id="ins-form" className="stack" onSubmit={(e) => { e.preventDefault(); doInsert(); }}>
          {ins.kind === "table" ? <div className="row">
            <Field label="Rows">{(id) => <input id={id} type="number" min={1} max={50} value={ins.rows} onChange={(e) => setIns({ ...ins, rows: Number(e.target.value) })} />}</Field>
            <Field label="Columns">{(id) => <input id={id} type="number" min={1} max={20} value={ins.cols} onChange={(e) => setIns({ ...ins, cols: Number(e.target.value) })} />}</Field></div>
            : <p className="muted">{ins.name}</p>}
          {!hf && <label className="check"><input type="checkbox" checked={copt.on} onChange={(e) => setCopt({ ...copt, on: e.target.checked })} /> Add caption</label>}
          {copt.on && !hf && <>
            <div className="row">
              <Field label="Position">{(id) => <select id={id} value={copt.pos} onChange={(e) => setCopt({ ...copt, pos: e.target.value as CapOpts["pos"] })}><option value="above">Above</option><option value="below">Below</option></select>}</Field>
              <Field label="Label" hint="Table, Figure, Listing…">{(id) => <input id={id} list="cap-labels" value={copt.label} onChange={(e) => setCopt({ ...copt, label: e.target.value })} maxLength={30} />}</Field></div>
            <Field label="Caption text" hint="Numbered with a SEQ field, style Caption: lists of tables/figures find it.">{(id) => <input id={id} value={copt.text} onChange={(e) => setCopt({ ...copt, text: e.target.value })} autoFocus />}</Field></>}
        </form></Modal>}
      {tb && <Modal title="Edit text box content" onClose={() => setTb(null)} actions={<><button className="btn" type="button" onClick={() => setTb(null)}>Cancel</button><button className="btn primary" form="tb-form">Save text</button></>}>
        <form id="tb-form" className="stack" onSubmit={(e) => { e.preventDefault(); saveTextbox(); }}>
          <Field label="Text" hint="One paragraph per line. The shape, its position and the paragraph formatting stay as they are.">{(id) => <textarea id={id} rows={10} value={tb.text} onChange={(e) => setTb({ ...tb, text: e.target.value })} autoFocus style={{ fontFamily: "monospace" }} />}</Field></form></Modal>}
      {dlg === "caption" && <Modal title="Insert caption" onClose={() => setDlg(null)} actions={<><button className="btn" type="button" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" form="cap-form">Insert caption</button></>}>
        <form id="cap-form" className="stack" onSubmit={(e) => { e.preventDefault(); insertCaption(); }}>
          <div className="row"><Field label="Label">{(id) => <input id={id} list="cap-labels" value={cap.label} onChange={(e) => setCap({ ...cap, label: e.target.value, pos: e.target.value === "Table" ? "above" : e.target.value === "Figure" ? "below" : cap.pos })} maxLength={30} />}</Field>
            <Field label="Position">{(id) => <select id={id} value={cap.pos} onChange={(e) => setCap({ ...cap, pos: e.target.value as CapOpts["pos"] })}><option value="above">Above</option><option value="below">Below</option></select>}</Field></div>
          <Field label="Caption text" hint="Numbered with a SEQ field, style Caption. Default: tables above, figures below.">{(id) => <input id={id} value={cap.text} onChange={(e) => setCap({ ...cap, text: e.target.value })} autoFocus />}</Field></form></Modal>}
      {dlg === "section" && <Modal title="Section break" onClose={() => setDlg(null)} actions={<><button className="btn" type="button" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" form="sec-form">Insert section break</button></>}>
        <form id="sec-form" className="stack" onSubmit={(e) => { e.preventDefault(); insertSection(); }}>
          <p className="muted">The settings apply to the section that <b>ends</b> at this break (OOXML keeps a section's page setup in the break after it).</p>
          <Field label="Section starts">{(id) => <select id={id} value={sec.brk} onChange={(e) => setSec({ ...sec, brk: e.target.value })}><option value="nextPage">On the next page</option><option value="continuous">Continuous</option><option value="evenPage">On an even page</option><option value="oddPage">On an odd page</option></select>}</Field>
          <Field label="Orientation">{(id) => <select id={id} value={sec.orient} onChange={(e) => setSec({ ...sec, orient: e.target.value })}><option value="portrait">Portrait</option><option value="landscape">Landscape</option></select>}</Field>
          <div className="row">{(["top", "bottom", "left", "right"] as const).map((k) => <Field key={k} label={`${k[0].toUpperCase()}${k.slice(1)} margin (cm)`}>{(id) => <input id={id} type="number" min={0} max={17} step={0.1} value={sec[k]} placeholder="keep" onChange={(e) => setSec({ ...sec, [k]: e.target.value })} />}</Field>)}</div></form></Modal>}
      {dlg === "list" && <Modal title={st.toc ? "List options" : { toc: "Table of contents", tof: "List of figures", tot: "List of tables" }[lst.kind]} onClose={() => setDlg(null)} actions={<><button className="btn" type="button" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" form="list-form">{st.toc ? "Update" : "Insert"}</button></>}>
        <form id="list-form" className="stack" onSubmit={(e) => { e.preventDefault(); const o = { title: lst.title, levels: lst.levels, label: lst.label }; if (st.toc) run().updateAttributes("toc", { title: lst.title, levels: lst.levels, label: lst.kind === "toc" ? null : lst.label }).run(); else insertList(editor, lst.kind, o); setDlg(null); }}>
          <Field label="Title (empty: none)">{(id) => <input id={id} value={lst.title} onChange={(e) => setLst({ ...lst, title: e.target.value })} autoFocus />}</Field>
          {lst.kind === "toc" ? <Field label="Heading levels">{(id) => <select id={id} value={lst.levels} onChange={(e) => setLst({ ...lst, levels: e.target.value })}>{["1-1", "1-2", "1-3", "1-4"].map((v) => <option key={v}>{v}</option>)}</select>}</Field>
            : <Field label="Caption label" hint="Lists the captions numbered with this label.">{(id) => <select id={id} value={lst.label} onChange={(e) => setLst({ ...lst, label: e.target.value })}><option>Figure</option><option>Table</option></select>}</Field>}
          <p className="muted small">Entries are generated from the document; Word refreshes the list (and its page numbers) when the file is opened.</p></form></Modal>}
      {dlg === "mark" && <Modal title="Mark selection as…" onClose={() => setDlg(null)} actions={<><button className="btn" type="button" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" form="mark-form">Mark {blockRange(editor)[1] - blockRange(editor)[0]} block{blockRange(editor)[1] - blockRange(editor)[0] === 1 ? "" : "s"}</button></>}>
        <form id="mark-form" className="stack" onSubmit={(e) => { e.preventDefault(); doMark(); }}>
          <Field label="Kind">{(id) => <select id={id} value={mark.kind} onChange={(e) => setMark({ ...mark, kind: e.target.value as typeof mark.kind })}>
            <option value="section">Section</option><option value="appendix">Appendix</option><option value="toc">Table of contents</option><option value="tot">List of tables</option><option value="tof">List of figures</option></select>}</Field>
          <Field label={mark.kind === "section" || mark.kind === "appendix" ? "Name" : "Title (empty: none)"}>{(id) => <input id={id} value={mark.title} onChange={(e) => setMark({ ...mark, title: e.target.value })} autoFocus />}</Field>
          {(mark.kind === "toc" || mark.kind === "tof" || mark.kind === "tot") && <p className="muted small">The selected blocks are replaced by a generated list.</p>}
        </form></Modal>}
    </div>
  );
}
