import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { blockRange } from "./blockrange";
import { extensions } from "./extensions";

const mk = () => new Editor({ extensions, content: { type: "doc", content: [
  { type: "paragraph", content: [{ type: "text", text: "one" }] },
  { type: "paragraph", content: [{ type: "text", text: "two" }] },
  { type: "opaque", attrs: { kind: "text box", xml: "<w:p/>" } },
  { type: "paragraph", content: [{ type: "text", text: "four" }] }] } });

test("blockRange maps the selection to top-level block indexes", () => {
  const e = mk();
  e.commands.setTextSelection(2);  // cursor in block 0
  expect(blockRange(e)).toEqual([0, 1]);
  e.commands.setTextSelection({ from: 7, to: 8 });
  expect(blockRange(e)).toEqual([1, 2]);
  e.commands.selectAll();
  expect(blockRange(e)).toEqual([0, 4]);
  e.commands.setTextSelection({ from: 2, to: 8 });
  expect(blockRange(e)).toEqual([0, 2]);
  expect(e.getJSON().content![2].type).toBe("opaque");  // schema keeps the node and its attrs
  expect(e.getJSON().content![2].attrs!.xml).toBe("<w:p/>");
});
