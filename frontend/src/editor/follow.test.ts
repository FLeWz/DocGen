import { describe, expect, it } from "vitest";
import { blockPage, lineBlock, lineChunks, loadFollow, paused, pdfPageCount, scrollPlan, tagBlock, topBlock } from "./follow";

describe("follow helpers", () => {
  it("selection -> top-level block index, clamped", () => {
    const r = (i: number) => ({ index: () => i });
    expect(topBlock(r(3), 10)).toBe(3);
    expect(topBlock(r(10), 10)).toBe(9);
    expect(topBlock(r(0), 0)).toBe(0);
  });
  it("tags the root element only", () => {
    expect(tagBlock('<p class="a"><b>x</b></p>', 4)).toBe('<p data-block="4" class="a"><b>x</b></p>');
    expect(tagBlock("", 1)).toBe("");
  });
  it("block -> pdf page keeps the ratio when the estimate is off", () => {
    const spans = [{ to: { block: 3, row: 0 } }, { to: { block: 6, row: 0 } }, { to: { block: 9, row: 0 } }, { to: { block: 12, row: 0 } }];
    expect(blockPage(spans, 0, 4)).toBe(1);
    expect(blockPage(spans, 7, 4)).toBe(3);
    expect(blockPage(spans, 7, 8)).toBe(6); // est page 3 of 4 -> 6 of 8
    expect(blockPage(spans, 99, 4)).toBe(4);
    expect(blockPage(spans, 7, 0)).toBe(3);
    expect(blockPage([], 7, 5)).toBe(1);
    expect(blockPage([{ to: { block: 2, row: 3 } }, { to: { block: 4, row: 0 } }], 2, 2)).toBe(1); // table split: block 2 starts on page 1
  });
  it("counts pdf pages", () => {
    expect(pdfPageCount("<< /Type /Pages /Count 2 >> << /Type /Page >> << /Type/Page/Parent 1 >>")).toBe(2);
    expect(pdfPageCount("<< /Type /Pages /Count 7 >>")).toBe(7);
  });
  it("scrollPlan: visible = no-op; else 30% from the top; far = instant", () => {
    expect(scrollPlan(100, 50, 500, 1000)).toBeNull();
    expect(scrollPlan(-1, 50, 500, 1000)).not.toBeNull();
    expect(scrollPlan(460, 50, 500, 1000)).not.toBeNull(); // sticks out at the bottom
    expect(scrollPlan(900, 50, 500, 1000)).toEqual({ top: 1750, smooth: true });
    expect(scrollPlan(5000, 50, 500, 1000)?.smooth).toBe(false);
    expect(scrollPlan(900, 50, 500, 1000, true)?.smooth).toBe(false);
    expect(scrollPlan(-5000, 50, 500, 100)?.top).toBe(0);
    expect(scrollPlan(-100, 900, 500, 0)).toBeNull(); // tall block covering the 30% line
  });
  it("manual scroll pause", () => {
    expect(paused(1000, 3000)).toBe(true);
    expect(paused(3000, 3000)).toBe(false);
  });
  it("follow pref defaults to on", () => {
    expect(loadFollow({ getItem: () => null })).toBe(true);
    expect(loadFollow({ getItem: () => "0" })).toBe(false);
  });
  it("markdown: line -> block", () => {
    const t = "# T\n\npara one\nstill one\n\n```\ncode\n\nmore\n```\n\n- a\n- b\n\n<!-- -->\n\n1. x";
    expect(lineChunks(t, false)).toEqual([0, 0, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4]);
    expect(lineBlock(t, 7, 5, false)).toBe(2); // inside the fence, blank line included
    expect(lineBlock(t, 12, 5, false)).toBe(3);
    expect(lineBlock(t, 17, 5, false)).toBe(4);
    expect(lineBlock(t, 17, 10, false)).toBe(8); // block count differs: proportional
    expect(lineBlock("", 1, 3, false)).toBe(0);
  });
  it("latex: annotation comment stays with its block, verbatim is whole", () => {
    const t = '% docgen:{"jc":"center"}\nCentred\n\n\\begin{verbatim}\na\n\nb\n\\end{verbatim}\n\n% lone comment\n\nlast';
    expect(lineBlock(t, 2, 3, true)).toBe(0);
    expect(lineBlock(t, 7, 3, true)).toBe(1);
    expect(lineBlock(t, 13, 3, true)).toBe(2);
  });
});
