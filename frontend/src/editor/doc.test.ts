import { expect, test } from "vitest";
import { effectiveStyle, extractStyles, extractVariables, PMNode } from "./doc";

const p = (text: string, styleId: string | null = null): PMNode => ({ type: "paragraph", attrs: { styleId }, content: [{ type: "text", text }] });
const v = (name: string): PMNode => ({ type: "variable", attrs: { name, default: "", filter: null } });

test("extractVariables: chips + dynamic table lists, unique, sorted, without row.* and built-ins", () => {
  const doc: PMNode = { type: "doc", content: [
    { type: "paragraph", content: [v("project"), v("client"), v("project"), v("doc.title"), v("author")] },
    { type: "table", attrs: { dynamicList: "items" }, content: [{ type: "tableRow", content: [{ type: "tableCell", content: [
      { type: "paragraph", content: [v("row.name")] }] }] }] },
    { type: "bulletList", content: [{ type: "listItem", content: [{ type: "paragraph", content: [v("deep")] }] }] },
  ] };
  expect(extractVariables(doc)).toEqual(["client", "deep", "items", "project"]);
  expect(extractVariables({ type: "doc" })).toEqual([]);
});

test("extractStyles: explicit ids, type defaults, list paragraphs", () => {
  const doc: PMNode = { type: "doc", content: [
    { type: "heading", attrs: { level: 2, styleId: null }, content: [] },
    { type: "heading", attrs: { level: 1, styleId: "Chapter" } },
    p("a"), p("b", "Quote"),
    { type: "orderedList", content: [{ type: "listItem", content: [p("x")] }] },
  ] };
  expect(extractStyles(doc)).toEqual(["Chapter", "Heading2", "ListParagraph", "Normal", "Quote"]);
  expect(effectiveStyle(p("x"), true)).toBe("ListParagraph");
  expect(effectiveStyle({ type: "text" })).toBeNull();
});
