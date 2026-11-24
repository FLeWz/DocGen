// "Insert > Revision table": a dynamic table bound to the built-in `revision_history` list, styled like the revision table of the
// reference manual (grey 909FA4 header with white centred text, only horizontal rules, 0.2 cm cell padding top/bottom) with that
// manual's column order: date, revision, edited by, changed sections, change reference (= commit). Generic labels, no document text.
import type { JSONContent } from "@tiptap/core";

export const REVISION_LIST = "revision_history";
export const REVISION_COLS = [
  { label: "Date", key: "date", w: 1391, jc: "center" },
  { label: "Revision", key: "version", w: 1133, jc: "center" },
  { label: "Edited by", key: "author", w: 1276, jc: "center" },
  { label: "Sections changed", key: "sections", w: 2800, jc: undefined },
  { label: "Commit", key: "commit", w: 3028, jc: undefined },
] as const;
const FILL = "909FA4", LINE = { val: "single", sz: 4, color: FILL }, NONE = { val: "none", sz: 0, color: "auto" };

const cell = (type: "tableHeader" | "tableCell", inline: JSONContent, col: (typeof REVISION_COLS)[number]): JSONContent => ({
  type, attrs: { colspan: 1, rowspan: 1, colwidth: [Math.round(col.w / 15)], ...(type === "tableHeader" ? { cfmt: { shd: FILL, vAlign: "center" } } : {}) },
  content: [{ type: "paragraph", attrs: col.jc ? { pfmt: { jc: col.jc } } : {}, content: [inline] }],
});

/** ProseMirror JSON of the table (insert with `insertContent`). */
export function revisionTableJSON(): JSONContent {
  const hdr = REVISION_COLS.map((c) => cell("tableHeader", { type: "text", text: c.label, marks: [{ type: "fmt", attrs: { color: "FFFFFF" } }] }, c));
  const tpl = REVISION_COLS.map((c) => cell("tableCell", { type: "variable", attrs: { name: `row.${c.key}`, default: "", filter: null } }, c));
  const bdr = { top: NONE, left: NONE, bottom: LINE, right: NONE, insideH: LINE, insideV: NONE }, mar = { top: 113, bottom: 113 };
  return {
    type: "table",
    attrs: { grid: REVISION_COLS.map((c) => c.w), dynamicList: REVISION_LIST, dynamicCols: REVISION_COLS.map((c) => c.key), tfmt: { bdr, mar }, tedit: { bdr, mar, layout: "fixed" } },
    content: [{ type: "tableRow", content: hdr }, { type: "tableRow", content: tpl }],
  };
}
