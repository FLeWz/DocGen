// TipTap schema. Custom attrs (see docs/DECISIONS.md "Content JSON shapes"):
//   paragraph {styleId|null}  heading {level, styleId|null}  table {dynamicList|null}
//   variable (inline atom) {name, default, filter|null}  image (block atom) {imageId, alt, width|null}
import { Extension, Node, mergeAttributes } from "@tiptap/core";
import Heading from "@tiptap/extension-heading";
import Paragraph from "@tiptap/extension-paragraph";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import StarterKit from "@tiptap/starter-kit";

const attr = (name: string, data: string, def: unknown = null) => ({
  default: def,
  parseHTML: (el: HTMLElement) => el.getAttribute(data) ?? def,
  renderHTML: (a: Record<string, any>) => (a[name] == null ? {} : { [data]: a[name] }),
});
const styleId = { styleId: attr("styleId", "data-style") };

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
    return { imageId: attr("imageId", "data-image-id", ""), alt: attr("alt", "alt", ""), width: attr("width", "data-width") };
  },
  parseHTML: () => [{ tag: "img[data-image-id]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["img", mergeAttributes(HTMLAttributes, {
    src: `/api/images/${encodeURIComponent(node.attrs.imageId)}`, class: "img",
    ...(node.attrs.width ? { style: `width:${+node.attrs.width}px` } : {}) })],
});

// Anything the importer could not map: raw OOXML of the whole block, shown as a grey chip, written back verbatim.
export const Opaque = Node.create({
  name: "opaque", group: "block", atom: true, selectable: true, draggable: true,
  addAttributes() { return { kind: attr("kind", "data-kind", "unknown"), xml: attr("xml", "data-xml", "") }; },
  parseHTML: () => [{ tag: "div[data-opaque]" }],
  renderHTML: ({ node, HTMLAttributes }) => ["div", mergeAttributes(HTMLAttributes, { "data-opaque": "", class: "opaque-chip", contenteditable: "false", title: "Not editable: kept as-is in the exported DOCX" }), `⬚ ${node.attrs.kind}`],
});

// Imported-DOCX bookkeeping that must survive editing (never shown): Word list ids, table properties and column grid.
const hidden = (def: unknown = null) => ({ default: def, rendered: false });
export const ImportAttrs = Extension.create({
  name: "importAttrs",
  addGlobalAttributes() {
    return [
      { types: ["bulletList", "orderedList"], attributes: { numId: hidden() } },
      { types: ["table"], attributes: { styleId: hidden(), tblPr: hidden(), grid: hidden() } },
    ];
  },
});

export const extensions = [
  StarterKit.configure({ paragraph: false, heading: false, blockquote: false, codeBlock: false, code: false, strike: false,
    horizontalRule: false, link: { openOnClick: false } }),
  Paragraph.extend({ addAttributes: () => styleId }),
  Heading.configure({ levels: [1, 2, 3, 4] }).extend({
    addAttributes() { return { ...this.parent?.(), ...styleId }; },
  }),
  Table.extend({ addAttributes() { return { ...this.parent?.(), dynamicList: attr("dynamicList", "data-dynamic-list") }; } })
    .configure({ resizable: false }),
  TableRow, TableHeader, TableCell, Variable, ImageNode, Opaque, ImportAttrs,
];
