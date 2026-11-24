import { describe, expect, it } from "vitest";
import { assignStops, tabbed, validStops } from "./tabs";

const FOOTER = [{ pos: 4513, val: "center" as const }, { pos: 9026, val: "right" as const }];

describe("tab stops", () => {
  it("assigns each tab to the next stop, then to default half-inch stops", () => {
    expect(assignStops(2, FOOTER).map((s) => s.pos)).toEqual([4513, 9026]);
    expect(assignStops(3, FOOTER).map((s) => [s.pos, s.val])[2]).toEqual([9360, "left"]);
    expect(assignStops(2, []).map((s) => s.pos)).toEqual([720, 1440]);
  });
  it("'Title<tab>Page X of Y' with a right stop: right-aligned segment pinned to the stop", () => {
    const h = tabbed(["Title", "Page 3 of 9"], [{ pos: 9026, val: "right" }]);
    expect(h).toContain('class="tabrow abs"');
    expect(h).toContain('<span class="tab-seg">Title</span>');
    expect(h).toContain('left:451.3pt;transform:translateX(-100%)">Page 3 of 9');
  });
  it("centered segment between two tabs (footer): -50% around the center stop, empty first segment keeps the line height", () => {
    const h = tabbed(["", "Mid", "Right"], FOOTER);
    expect(h).toContain("&#8203;");
    expect(h).toContain('left:225.65pt;transform:translateX(-50%)">Mid');
    expect(h).toContain('left:451.3pt;transform:translateX(-100%)">Right');
  });
  it("stop positions are relative to the margin: the paragraph indent is subtracted", () => {
    expect(tabbed(["a", "b"], [{ pos: 2000, val: "left" }], 400)).toContain("left:80pt");
  });
  it("leader stops use the flex layout with a dotted filler and keep a left stop's column width", () => {
    const h = tabbed(["1", "Introduction", "3"], [{ pos: 440, val: "left" }, { pos: 9628, val: "right", leader: "dot" }]);
    expect(h).toContain('class="tabrow flex"');
    expect(h).toContain('<span class="tab-seg" style="min-width:22pt">1</span>');
    expect(h).toContain('class="tab-fill lead" style="border-bottom-style:dotted"');
    expect(h.indexOf("Introduction")).toBeLessThan(h.lastIndexOf("tab-fill lead"));
    expect(h.endsWith("3</span></span>")).toBe(true);
  });
  it("drops junk stops", () => {
    expect(validStops([{ pos: -1, val: "left" }, { pos: 10, val: "evil" }, { pos: 5, val: "left", leader: "x\"" }, null, { pos: 1, val: "right", leader: "dot" }]))
      .toEqual([{ pos: 1, val: "right", leader: "dot" }, { pos: 5, val: "left" }]);
  });
});
