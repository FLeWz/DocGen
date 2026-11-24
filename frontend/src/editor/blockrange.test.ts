import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { blockRange, BlockSelect, pickBlock, selectedBlocks, setSelectMode } from "./blockrange";
import { extensions } from "./extensions";

const p = (t: string) => ({ type: "paragraph", content: [{ type: "text", text: t }] });
const cell = (t: string) => ({ type: "tableCell", content: [p(t)] });
const mk = (n = 4) => new Editor({ extensions: [...extensions, BlockSelect], content: { type: "doc", content: [
  p("one"), p("two"), { type: "opaque", attrs: { kind: "text box", xml: "<w:p/>" } }, p("four"),
  { type: "table", content: [{ type: "tableRow", content: [cell("a"), cell("b")] }, { type: "tableRow", content: [cell("c"), cell("d")] }] },
  ...Array.from({ length: n }, (_, i) => p(`extra ${i}`))] } });

test("blockRange maps the selection to top-level block indexes", () => {
  const e = mk();
  e.commands.setTextSelection(2);
  expect(blockRange(e)).toEqual([0, 1]);
  e.commands.setTextSelection({ from: 7, to: 8 });
  expect(blockRange(e)).toEqual([1, 2]);
  e.commands.selectAll();
  expect(blockRange(e)).toEqual([0, 9]);
  e.commands.setTextSelection({ from: 2, to: 8 });
  expect(blockRange(e)).toEqual([0, 2]);
  expect(e.getJSON().content![2].attrs!.xml).toBe("<w:p/>");
});

test("a selection ending at the start of a block does not take that block", () => {
  const e = mk();
  e.commands.setTextSelection({ from: 2, to: 6 });  // to = start of "two"
  expect(blockRange(e)).toEqual([0, 1]);
  e.commands.setTextSelection({ from: 2, to: 7 });  // one char into "two"
  expect(blockRange(e)).toEqual([0, 2]);
});

test("selection starting and ending inside a table covers the whole table block", () => {
  const e = mk(), at: Record<string, number> = {};
  e.state.doc.descendants((n, pos) => { if (n.isText) at[n.text!] = pos; });
  e.commands.setTextSelection({ from: at.a + 1, to: at.d + 1 });
  expect(blockRange(e)).toEqual([4, 5]);
  e.commands.setTextSelection({ from: at.four + 1, to: at.c + 1 });  // paragraph -> into the table
  expect(blockRange(e)).toEqual([3, 5]);
});

test("block mode: handles select a block, Shift extends, any range works regardless of the DOM selection", () => {
  const e = mk(600);
  setSelectMode(e, true);
  pickBlock(e, 3, false);
  expect(blockRange(e)).toEqual([3, 4]);
  pickBlock(e, 450, true);
  expect(blockRange(e)).toEqual([3, 451]);
  expect(selectedBlocks(e)).toHaveLength(448);
  pickBlock(e, 1, true);
  expect(blockRange(e)).toEqual([1, 4]);
  setSelectMode(e, false);
  expect(blockRange(e)).toEqual([0, 1]);
});
