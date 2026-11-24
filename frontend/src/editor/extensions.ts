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
import StarterKit from "@tiptap/starter-kit";
import { cellCss, imgCss, listCss, pflags, pfmtCss, rowCss, runCss, safeId, tableCss } from "./fmt";
import { ooxmlToHtml, getMedia } from "./ooxml";
import { Pages } from "./pageplugin";

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
  renderHTML: ({ node, HTMLAttributes }) => ["div", mergeAttributes(HTMLAttributes, { "data-opaque": "", class: "opaque-chip", contenteditable: "false", title: "Not editable: kept as-is in the exported DOCX" }), `⬚ ${node.attrs.kind}`],
  addNodeView() {
    return ({ node }) => {
      const dom = document.createElement("div");
      dom.setAttribute("contenteditable", "false");
      const paint = (n: typeof node) => {
        const html = ooxmlToHtml(String(n.attrs.xml ?? ""), getMedia());
        dom.className = html ? "opaque-view" : "opaque-chip";
        dom.title = `Not editable (${n.attrs.kind}): kept as-is in the exported DOCX`;
        if (html) dom.innerHTML = html; else dom.textContent = `⬚ ${n.attrs.kind}`;
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
      // slot = "CONTENT" | name: the paragraph is a {{ CONTENT }} / {{ SLOT:name }} placeholder of a base document (drawn as a dashed box)
      { types: ["paragraph"], attributes: { slot: { default: null, parseHTML: (el: HTMLElement) => el.getAttribute("data-slot"),
        renderHTML: (a: Record<string, any>) => (/^[A-Za-z_]\w{0,39}$/.test(a.slot ?? "") ? { "data-slot": a.slot } : {}) } } },
      { types: ["bulletList", "orderedList"], attributes: { numId: hidden(), listStyle: { default: null, parseHTML: (el: HTMLElement) => el.style.listStyleType || null,
        renderHTML: (a: Record<string, any>) => st(listCss(a.listStyle)) } } },
      { types: ["table"], attributes: { tblPr: hidden(), grid: hidden(),
        styleId: { default: null, renderHTML: (a: Record<string, any>) => ({ class: `dtbl${safeId(a.styleId) ? ` ts-${safeId(a.styleId)}` : ""}` }) },
        tfmt: json("tfmt", (a) => st(tableCss(a.tfmt, a.grid))) } },
      // eff = derived from the table style (never written back), cfmt = direct formatting (exported); eff first so cfmt wins
      { types: ["tableCell", "tableHeader"], attributes: { eff: json("eff", (a) => st(cellCss(a.eff))), cfmt: json("cfmt", (a) => st(cellCss(a.cfmt))) } },
      { types: ["tableRow"], attributes: { rfmt: json("rfmt", (a) => st(rowCss(a.rfmt))) } },
    ];
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
  Table.extend({ addAttributes() { return { ...this.parent?.(), dynamicList: attr("dynamicList", "data-dynamic-list") }; } })
    .configure({ resizable: false }),
  TableRow, TableHeader, TableCell, Variable, ImageNode, Fmt, HardBreak, Opaque, ImportAttrs, HeadingNumbers,
];

/** The editor of the page view: the schema above plus page-gap decorations (the conflict resolver edits without them). */
export const pagedExtensions = [...extensions, Pages];
