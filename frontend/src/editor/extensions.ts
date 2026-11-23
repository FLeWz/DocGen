// TipTap schema. Custom attrs (see docs/DECISIONS.md "Content JSON shapes"):
//   paragraph {styleId|null}  heading {level, styleId|null}  table {dynamicList|null}
//   variable (inline atom) {name, default, filter|null}  image (block atom) {imageId, alt, width|null}
import { Node, mergeAttributes } from "@tiptap/core";
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

export const extensions = [
  StarterKit.configure({ paragraph: false, heading: false, blockquote: false, codeBlock: false, code: false, strike: false,
    horizontalRule: false, link: { openOnClick: false } }),
  Paragraph.extend({ addAttributes: () => styleId }),
  Heading.configure({ levels: [1, 2, 3, 4] }).extend({
    addAttributes() { return { ...this.parent?.(), ...styleId }; },
  }),
  Table.extend({ addAttributes() { return { ...this.parent?.(), dynamicList: attr("dynamicList", "data-dynamic-list") }; } })
    .configure({ resizable: false }),
  TableRow, TableHeader, TableCell, Variable, ImageNode,
];
