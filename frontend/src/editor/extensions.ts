// TipTap schema. Custom attrs (see docs/DECISIONS.md "Content JSON shapes"):
//   paragraph {styleId|null, pfmt|null}  heading {level, styleId|null, pfmt|null}  table {dynamicList|null, ...import attrs}
//   variable (inline atom) {name, default, filter|null}  image (block atom) {imageId, alt, width|null, height, align}
//   marks: `fmt` {color,font,size,hl,strike,va,caps,smallCaps,rStyle}; cells: cfmt; lists: listStyle; hardBreak {page}
import { Extension, mergeAttributes, Mark, Node } from "@tiptap/core";
import { Plugin } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import Heading from "@tiptap/extension-heading";
import Paragraph from "@tiptap/extension-paragraph";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { isInTable } from "@tiptap/pm/tables";
import StarterKit from "@tiptap/starter-kit";
import { gridGuard, moveColumn, moveRow, run as runOp, selectInnerTable, selectOuterTable, type Op } from "./tables";
import { esc, cellCss, imgCss, listCss, pflags, pfmtCss, rowCss, runCss, safeId, tableCss } from "./fmt";
import { ooxmlToHtml, getMedia } from "./ooxml";
import { renderMath } from "./math";
import "katex/dist/katex.min.css";
import { opaqueWhy, textboxLines, visibleLines } from "./opaqueText";
import { BlockSelect } from "./blockrange";
import { Pages } from "./pageplugin";
import { entryText, listEntries, seqNumbers } from "./outline";
import type { PMNode as JNode } from "./doc";

const attr = (name: string, data: string, def: unknown = null) => ({
  default: def,
  parseHTML: (el: HTMLElement) => el.getAttribute(data) ?? def,
  renderHTML: (a: Record<string, any>) => (a[name] == null ? {} : { [data]: a[name] }),
});
/** Object-valued attr, round-tripped through a data-* JSON attribute (clipboard); `css` adds the visual style. */
const json = (name: string, css: (a: Record<string, any>) => Record<string, string>) => ({
  default: null,
  parseHTML: (el: HTMLElement) => { try { return JSON.parse(el.getAttribute(`data-${name}`) ?? "null"); } catch { return null; } },
  renderHTML: (a: Record<string, any>) => (a[name] ? { [`data-${name}`]: JSON.stringify(a[name]), ...css(a) } : css(a)),
});
const st = (s: string): Record<string, string> => (s ? { style: s } : {});
const cls = (a: Record<string, any>, def: string) => ({ class: `st-${safeId(a.styleId) ?? def}` });
const styleAttrs = { styleId: attr("styleId", "data-style") };

export const Variable = Node.create({
  name: "variable", group: "inline", inline: true, atom: true, selectable: true,
  addAttributes() {
    return { name: attr("name", "data-variable", ""), default: attr("default", "data-default", ""), filter: attr("filter", "data-filter") };
  },
  parseHTML: () => [{ tag: "span[data-variable]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["span", mergeAttributes(HTMLAttributes, { class: "var-chip" }), `{{ ${node.attrs.name} }}`],
});

/** Equation: LaTeX in `latex`, drawn with KaTeX; `display` puts it on its own centred line. Exported to Word as a native equation (OMML). */
export const MathNode = Node.create({
  name: "math", group: "inline", inline: true, atom: true, selectable: true,
  addAttributes() { return { latex: attr("latex", "data-latex", ""), display: { default: false, parseHTML: (el: HTMLElement) => el.hasAttribute("data-display"), rendered: false } }; },
  parseHTML: () => [{ tag: "span[data-math]" }],
  renderHTML: ({ node }) => ["span", { "data-math": "", "data-latex": node.attrs.latex, ...(node.attrs.display ? { "data-display": "1" } : {}), class: "math-node" }, `$${node.attrs.latex}$`],
  renderText: ({ node }) => (node.attrs.display ? `$$${node.attrs.latex}$$` : `$${node.attrs.latex}$`),
  addNodeView() {
    return ({ node, editor, getPos }) => {
      const dom = document.createElement("span");
      dom.setAttribute("contenteditable", "false");
      const paint = (n: typeof node) => {
        const r = renderMath(String(n.attrs.latex ?? ""), !!n.attrs.display);
        dom.className = `math-node${n.attrs.display ? " math-display" : ""}${r.error ? " math-bad" : ""}`;
        dom.title = r.error ?? "Equation (click to edit)";
        if (r.error || !r.html) dom.textContent = `$${n.attrs.latex}$`; else dom.innerHTML = r.html;
      };
      paint(node);
      dom.addEventListener("click", () => { const pos = getPos(); if (typeof pos === "number" && editor.isEditable) window.dispatchEvent(new CustomEvent("docgen:math", { detail: { editor, pos } })); });
      return { dom, update: (n) => { if (n.type !== node.type) return false; node = n; paint(n); return true; } };
    };
  },
});

export const ImageNode = Node.create({
  name: "image", group: "block", atom: true, draggable: true,
  addAttributes() {
    return { imageId: attr("imageId", "data-image-id", ""), alt: attr("alt", "alt", ""), width: attr("width", "data-width"),
      height: attr("height", "data-height"), align: attr("align", "data-align"), anchored: { default: null, rendered: false } };
  },
  parseHTML: () => [{ tag: "img[data-image-id]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["img", mergeAttributes(HTMLAttributes, {
    src: `/api/images/${encodeURIComponent(node.attrs.imageId)}`, class: "img", ...st(imgCss(node.attrs)) })],
});

/** Inline text formatting that is not bold/italic/underline: colour, font, size, highlight, strike, super/subscript, char style. */
export const Fmt = Mark.create({
  name: "fmt",
  addAttributes() {
    const a = (k: string) => [k, { default: null, rendered: false }];
    return Object.fromEntries(["color", "font", "size", "hl", "strike", "va", "caps", "smallCaps", "rStyle"].map(a));
  },
  parseHTML: () => [{ tag: "span[data-fmt]", getAttrs: (el) => { try { return JSON.parse((el as HTMLElement).getAttribute("data-fmt") ?? "{}"); } catch { return {}; } } }],
  renderHTML: ({ mark }) => {
    const a = Object.fromEntries(Object.entries(mark.attrs).filter(([, v]) => v != null));
    return ["span", { "data-fmt": JSON.stringify(a), ...(safeId(a.rStyle) ? { class: `cs-${safeId(a.rStyle)}` } : {}), ...st(runCss(a)) }, 0];
  },
});

// Line break; with {page: true} a page break (drawn as a dashed rule, written back as <w:br w:type="page"/>).
export const HardBreak = Node.create({
  name: "hardBreak", group: "inline", inline: true, selectable: false,
  addAttributes() { return { page: { default: false, parseHTML: (el: HTMLElement) => el.hasAttribute("data-page"), rendered: false } }; },
  parseHTML: () => [{ tag: "span[data-page]" }, { tag: "br" }],
  renderHTML: ({ node }) => (node.attrs.page ? ["span", { class: "pgbrk", "data-page": "1", contenteditable: "false" }] : ["br"]),
  renderText: () => "\n",
  addCommands() { return { setHardBreak: () => ({ commands }) => commands.insertContent({ type: this.name }) }; },
  addKeyboardShortcuts() { return { "Shift-Enter": () => this.editor.commands.setHardBreak(), "Mod-Enter": () => this.editor.commands.setHardBreak() }; },
});

// Anything the importer could not map: raw OOXML of the whole block. Read-only, drawn as a faithful HTML approximation of the
// OOXML (text, formatting, tables, images) and falling back to a labelled chip; written back verbatim on export.
export const Opaque = Node.create({
  name: "opaque", group: "block", atom: true, selectable: true, draggable: true,
  addAttributes() { return { kind: attr("kind", "data-kind", "unknown"), xml: attr("xml", "data-xml", "") }; },
  parseHTML: () => [{ tag: "div[data-opaque]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["div", mergeAttributes(HTMLAttributes, { "data-opaque": "", class: "opaque-chip", contenteditable: "false", title: opaqueWhy(String(node.attrs.kind)) }), `⬚ ${node.attrs.kind}`],
  addNodeView() {
    return ({ node, editor, getPos }) => {
      const dom = document.createElement("div"), body = document.createElement("div"), bar = document.createElement("div");
      dom.setAttribute("contenteditable", "false");
      bar.className = "opaque-bar";
      dom.append(body, bar);
      const btn = (label: string, title: string, fn: () => void) => {
        const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.title = title;
        b.addEventListener("mousedown", (e) => e.preventDefault()); b.addEventListener("click", (e) => { e.stopPropagation(); fn(); }); bar.appendChild(b);
      };
      const at = () => { const pos = getPos(); return typeof pos === "number" ? pos : null; };
      const paint = (n: typeof node) => {
        const xml = String(n.attrs.xml ?? ""), html = ooxmlToHtml(xml, getMedia());
        dom.className = n.attrs.kind === "bookmark" ? "opaque-hidden" : html ? "opaque-view" : "opaque-chip";
        dom.title = opaqueWhy(String(n.attrs.kind));
        if (html) body.innerHTML = html; else body.textContent = `⬚ ${n.attrs.kind}`;
        bar.replaceChildren();
        if (n.attrs.kind === "bookmark") return;  // shown only while the editor is editable (CSS), so no repaint on the read-only -> ready switch
        if (textboxLines(xml)) btn("✎ Edit text", "Edit the text of this text box (the shape stays)", () => { const pos = at(); if (pos !== null && editor.isEditable) window.dispatchEvent(new CustomEvent("docgen:textbox", { detail: { editor, pos } })); });
        btn("Convert to text", "Replace this block by plain paragraphs holding its text (the drawing, shape and formatting are lost; undo brings it back)", () => {
          const pos = at(); if (pos === null || !editor.isEditable) return;
          if (!window.confirm("Convert to plain paragraphs? The drawing/shape/field and its formatting are lost (Ctrl+Z undoes it).")) return;
          const ls = visibleLines(String(n.attrs.xml ?? ""));
          const paras = (ls.length ? ls : [{ text: "", style: null }]).map((l) => ({ type: "paragraph", attrs: { styleId: l.style && /^[A-Za-z0-9_-]{1,60}$/.test(l.style) ? l.style : null }, ...(l.text ? { content: [{ type: "text", text: l.text }] } : {}) }));
          editor.chain().focus().insertContentAt({ from: pos, to: pos + n.nodeSize }, paras).run();
        });
      };
      paint(node);
      return { dom, update: (n) => { if (n.type !== node.type) return false; paint(n); return true; } };
    };
  },
});

// Imported-DOCX attrs that must survive editing and are (mostly) drawn: Word list ids/styles, table properties, cell/paragraph formats.
const hidden = (def: unknown = null) => ({ default: def, rendered: false });
export const ImportAttrs = Extension.create({
  name: "importAttrs",
  addGlobalAttributes() {
    return [
      // orig = {s,e,h}: the body children this block was parsed from + a hash; unchanged blocks are exported as that original XML
      { types: ["paragraph", "heading", "table", "bulletList", "orderedList"], attributes: { orig: hidden() } },
      // sec = {id, kind: section|appendix, title}: the block belongs to a marked section (a bookmark range in the DOCX); bm = bookmark name (REF target)
      { types: ["paragraph", "heading", "table", "bulletList", "orderedList", "image", "opaque", "toc", "sectBreak"], attributes: {
        sec: json("sec", (a): Record<string, string> => (a.sec ? { class: `in-sec sec-${safeId(a.sec.kind) ?? "section"}` } : {})) } },
      { types: ["paragraph", "heading"], attributes: { bm: attr("bm", "data-bm") } },
      { types: ["orderedList"], attributes: { continue: hidden() } },
      // slot = "CONTENT" | name: the paragraph is a {{ CONTENT }} / {{ SLOT:name }} placeholder of a base document (drawn as a dashed box)
      { types: ["paragraph"], attributes: { slot: { default: null, parseHTML: (el: HTMLElement) => el.getAttribute("data-slot"),
        renderHTML: (a: Record<string, any>) => (/^[A-Za-z_]\w{0,39}$/.test(a.slot ?? "") ? { "data-slot": a.slot } : {}) } } },
      { types: ["bulletList", "orderedList"], attributes: { numId: hidden(), listStyle: { default: null, parseHTML: (el: HTMLElement) => el.style.listStyleType || null,
        renderHTML: (a: Record<string, any>) => st(listCss(a.listStyle)) } } },
      { types: ["table"], attributes: { tblPr: hidden(), grid: hidden(), tedit: hidden(),
        styleId: { default: null, renderHTML: (a: Record<string, any>) => ({ class: `dtbl${safeId(a.styleId) ? ` ts-${safeId(a.styleId)}` : ""}` }) },
        tfmt: json("tfmt", (a) => st(tableCss(a.tfmt, a.grid))) } },
      // eff = derived from the table style (never written back), cfmt = direct formatting (exported); eff first so cfmt wins
      { types: ["tableCell", "tableHeader"], attributes: { eff: json("eff", (a) => st(cellCss(a.eff))), cfmt: json("cfmt", (a) => st(cellCss(a.cfmt))) } },
      { types: ["tableRow"], attributes: { rfmt: json("rfmt", (a) => st(rowCss(a.rfmt))) } },
    ];
  },
});


const fieldLabel = (a: Record<string, any>) => (a.code === "SEQ" || a.code === "REF" || a.code === "PAGEREF") ? String(a.text || "?") : `‹${a.code}›`;
/** Word field: PAGE / NUMPAGES / SECTIONPAGES (value per page), SEQ (caption number), REF / PAGEREF (cross reference). */
export const Field = Node.create({
  name: "field", group: "inline", inline: true, atom: true, selectable: true,
  addAttributes() { return { code: attr("code", "data-field", "PAGE"), arg: attr("arg", "data-arg", ""), text: attr("text", "data-text", "") }; },
  parseHTML: () => [{ tag: "span[data-field]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["span", mergeAttributes(HTMLAttributes, { class: `fld fld-${safeId(node.attrs.code) ?? "x"}`, title: node.attrs.code === "SEQ" ? "Automatic number: follows the order of the captions" : node.attrs.code === "REF" ? "Cross-reference (updates with its target)" : `Field ${node.attrs.code} ${node.attrs.arg ?? ""}`.trim() }), fieldLabel(node.attrs)],
  renderText: ({ node }) => fieldLabel(node.attrs),
});

/** Footnote / endnote: the number is derived (document order), the text is a plain string edited in a prompt. */
export const Footnote = Node.create({
  name: "footnote", group: "inline", inline: true, atom: true, selectable: true,
  addAttributes() { return { kind: attr("kind", "data-kind", "footnote"), text: attr("text", "data-text", ""), n: { default: 1, rendered: false } }; },
  parseHTML: () => [{ tag: "sup[data-footnote]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["sup", mergeAttributes(HTMLAttributes, { "data-footnote": "", class: `fn fn-${node.attrs.kind === "endnote" ? "end" : "foot"}`, title: String(node.attrs.text ?? "") }), node.attrs.kind === "endnote" ? roman(node.attrs.n) : String(node.attrs.n)],
  renderText: ({ node }) => `[${node.attrs.n}]`,
});
const roman = (n: number) => ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"][n - 1] ?? String(n);

/** Table of contents / list of figures / list of tables: a generated block. `entries` (derived from headings/captions) is what the
 *  view shows; `xml` is an imported original (cached entries drawn from it, written back unchanged). */
export const Toc = Node.create({
  name: "toc", group: "block", atom: true, selectable: true, draggable: true,
  addAttributes() {
    return { kind: attr("kind", "data-kind", "toc"), title: attr("title", "data-title", ""), levels: attr("levels", "data-levels", "1-3"), label: attr("label", "data-label"),
      xml: attr("xml", "data-xml"), refresh: { default: false, rendered: false }, entries: { default: null, rendered: false } };
  },
  parseHTML: () => [{ tag: "div[data-toc]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["div", mergeAttributes(HTMLAttributes, { "data-toc": "", class: "toc-block", contenteditable: "false" }), `${TOC_NAME[node.attrs.kind as string] ?? "Table of contents"}`],
  addNodeView() {
    return ({ node }) => {
      const dom = document.createElement("div");
      dom.setAttribute("contenteditable", "false");
      const paint = (n: typeof node) => {
        const a = n.attrs, imported = !!a.xml;
        dom.className = `toc-block toc-${safeId(a.kind) ?? "toc"}`;
        dom.title = imported ? "Imported list: cached entries, refreshed by Word on open" : "Generated list: entries come from the headings/captions, Word refreshes it on open";
        const entries: { level: number; text: string }[] = Array.isArray(a.entries) ? a.entries : [];
        const head = `<div class="toc-h"><span class="toc-kind">${esc(TOC_NAME[a.kind as string] ?? "Table of contents")}</span>${a.title ? ` <b>${esc(a.title)}</b>` : ""}</div>`;
        const body = imported ? ooxmlToHtml(String(a.xml), getMedia()) || `<div class="toc-empty">cached entries not drawn</div>`
          : entries.length ? entries.map((e) => `<div class="toc-line toc-l${Math.min(Math.max(e.level, 1), 9)}"><span>${esc(e.text)}</span><i></i><span>·</span></div>`).join("") : `<div class="toc-empty">No ${a.kind === "toc" ? "headings" : "captions"} yet: entries appear here as you add them.</div>`;
        dom.innerHTML = head + body;
      };
      paint(node);
      return { dom, update: (n) => { if (n.type !== node.type) return false; paint(n); return true; } };
    };
  },
});
const TOC_NAME: Record<string, string> = { toc: "Table of contents", tof: "List of figures", tot: "List of tables" };

/** Section break: ends a section; orientation/margins/start type describe the section that ends here (as in OOXML). */
export const SectBreak = Node.create({
  name: "sectBreak", group: "block", atom: true, selectable: true, draggable: true,
  addAttributes() {
    return { brk: attr("brk", "data-brk", "nextPage"), orient: attr("orient", "data-orient", "portrait"), top: attr("top", "data-top"), right: attr("right", "data-right"), bottom: attr("bottom", "data-bottom"), left: attr("left", "data-left") };
  },
  parseHTML: () => [{ tag: "div[data-sectbreak]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["div", mergeAttributes(HTMLAttributes, { "data-sectbreak": "", class: "sectbrk-edit", contenteditable: "false" }),
    `Section break (${BRK[node.attrs.brk as string] ?? "next page"}${node.attrs.orient === "landscape" ? ", landscape" : ""})`],
});
const BRK: Record<string, string> = { nextPage: "next page", continuous: "continuous", evenPage: "even page", oddPage: "odd page" };

/** Derived values kept current by a follow-up transaction (not undoable on their own): SEQ numbers, REF results, footnote numbers,
 *  generated list entries. Not run for setContent, so merely opening a document never changes it. */
export const Derived = Extension.create({
  name: "derived",
  addProseMirrorPlugins() {
    return [new Plugin({
      appendTransaction(trs, _old, state) {
        if (!trs.some((t) => t.docChanged) || trs.some((t) => t.getMeta("preventUpdate"))) return null;
        const blocks: JNode[] = [];
        state.doc.forEach((n) => blocks.push(n.toJSON() as JNode));
        const seq = seqNumbers(blocks), bm = new Map<string, string>(), notes = { footnote: 0, endnote: 0 };
        state.doc.forEach((n) => { if (n.attrs.bm) bm.set(String(n.attrs.bm), entryText(n.toJSON() as JNode)); });
        let si = 0;
        const tr = state.tr;
        state.doc.descendants((n, pos) => {
          let attrs: Record<string, unknown> | null = null;
          if (n.type.name === "field") {
            const a = n.attrs;
            const want = a.code === "SEQ" ? String(seq[si++]) : a.code === "REF" && bm.has(a.arg) ? bm.get(a.arg)! : null;
            if (want !== null && want !== a.text) attrs = { ...a, text: want };
          } else if (n.type.name === "footnote") {
            const k = n.attrs.kind === "endnote" ? "endnote" : "footnote", num = ++notes[k];
            if (n.attrs.n !== num) attrs = { ...n.attrs, n: num };
          } else if (n.type.name === "toc" && !n.attrs.xml) {
            const e = listEntries(blocks, n.attrs);
            if (JSON.stringify(e) !== JSON.stringify(n.attrs.entries)) attrs = { ...n.attrs, entries: e };
          }
          if (attrs) tr.setNodeMarkup(pos, undefined, attrs);
          return true;
        });
        return tr.docChanged ? tr.setMeta("addToHistory", false) : null;
      },
    })];
  },
});

/** Keyboard shortcuts that open ribbon dialogs (the ribbon listens for `docgen:cmd`). */
export const Shortcuts = Extension.create({
  name: "shortcuts",
  addKeyboardShortcuts() {
    const go = (cmd: string) => () => { window.dispatchEvent(new CustomEvent("docgen:cmd", { detail: cmd })); return true; };
    return { "Mod-k": go("link"), "Mod-f": go("find"), "Mod-h": go("find"), "Mod-/": go("shortcuts"), "Mod-Alt-l": go("caption"), "Mod-Alt-e": () => { window.dispatchEvent(new CustomEvent("docgen:math", { detail: { editor: this.editor, pos: null } })); return true; }, "Mod-Shift-x": () => { const e = this.editor, on = e.getAttributes("fmt").strike === true; e.chain().focus().setMark("fmt", { ...e.getAttributes("fmt"), strike: on ? null : true }).run(); return true; } };
  },
});

/** Table keys: Alt+Shift+arrows move the row/column, Shift+F10 / the context-menu key open the table menu (see TableTools); gridGuard keeps attrs.grid honest. */
export const TableKeys = Extension.create({
  name: "tableKeys",
  addProseMirrorPlugins() { return [gridGuard()]; },
  addKeyboardShortcuts() {
    const e = () => this.editor, mv = (op: Op) => () => isInTable(e().state) && (runOp(e(), op) || true);
    return { "Alt-ArrowUp": mv(selectOuterTable), "Alt-ArrowDown": mv(selectInnerTable), "Alt-Shift-ArrowUp": mv(moveRow(-1)), "Alt-Shift-ArrowDown": mv(moveRow(1)), "Alt-Shift-ArrowLeft": mv(moveColumn(-1)), "Alt-Shift-ArrowRight": mv(moveColumn(1)),
      "Shift-F10": () => isInTable(e().state) && window.dispatchEvent(new CustomEvent("docgen:tablemenu")), ContextMenu: () => isInTable(e().state) && window.dispatchEvent(new CustomEvent("docgen:tablemenu")) };
  },
});

/** Heading numbers of styles that carry list numbering (Word "1.2 Title"), drawn as widgets so the editor matches the preview. */
export const headingNumbering = { styles: new Map<string, { level: number | null }>() };
export const HeadingNumbers = Extension.create({
  name: "headingNumbers",
  addProseMirrorPlugins() {
    return [new Plugin({ props: { decorations: ({ doc }) => {
      const count = [0, 0, 0, 0, 0, 0, 0, 0, 0], out: Decoration[] = [];
      doc.descendants((n, pos) => {
        if (n.type.name !== "heading") return false;
        const st = headingNumbering.styles.get(n.attrs.styleId ?? `Heading${n.attrs.level}`);
        if (!st) return false;
        const d = Math.min(Math.max(st.level ?? n.attrs.level, 1), 9) - 1;
        count[d]++; count.fill(0, d + 1);
        const text = count.slice(0, d + 1).map((c) => c || 1).join(".");
        out.push(Decoration.widget(pos + 1, () => { const s = document.createElement("span"); s.className = "num"; s.contentEditable = "false"; s.textContent = text; return s; }, { side: -1, key: `n${text}` }));
        return false;
      });
      return DecorationSet.create(doc, out);
    } } })];
  },
});

export const extensions = [
  StarterKit.configure({ paragraph: false, heading: false, blockquote: false, codeBlock: false, code: false, strike: false,
    horizontalRule: false, hardBreak: false, link: { openOnClick: false } }),
  Paragraph.extend({ addAttributes() { return { ...styleAttrs, pfmt: json("pfmt", (a) => ({ ...st(pfmtCss(a.pfmt)), ...pflags(a.pfmt) })) }; },
    renderHTML({ node, HTMLAttributes }) { return ["p", mergeAttributes(HTMLAttributes, cls(node.attrs, "Normal")), 0]; } }),
  Heading.configure({ levels: [1, 2, 3, 4] }).extend({
    addAttributes() { return { ...this.parent?.(), ...styleAttrs, pfmt: json("pfmt", (a) => ({ ...st(pfmtCss(a.pfmt)), ...pflags(a.pfmt) })) }; },
    renderHTML({ node, HTMLAttributes }) {
      return [`h${node.attrs.level}`, mergeAttributes(HTMLAttributes, cls(node.attrs, `Heading${node.attrs.level}`)), 0];
    },
  }),
  Table.extend({ addAttributes() { return { ...this.parent?.(), dynamicList: attr("dynamicList", "data-dynamic-list"), dynamicRows: hidden(), dynamicCols: hidden() }; } })
    .configure({ resizable: false }),
  TableRow, TableHeader, TableCell, Variable, MathNode, ImageNode, Fmt, HardBreak, Opaque, ImportAttrs, HeadingNumbers,
  Field, Footnote, Toc, SectBreak, Derived, Shortcuts, TableKeys,
];

/** The editor of the page view: the schema above plus page-gap decorations (the conflict resolver edits without them). */
export const pagedExtensions = [...extensions, Pages, BlockSelect];
