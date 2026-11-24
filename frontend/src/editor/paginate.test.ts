import { describe, expect, it } from "vitest";
import { Block, paginate } from "./paginate";

const body = () => 100;
const b = (h: number, o: Partial<Block> = {}): Block => ({ h, ...o });
const starts = (bl: Block[], f = body) => paginate(bl, f).map((p) => `${p.from.block}.${p.from.row}-${p.to.block}.${p.to.row}`);

describe("paginate", () => {
  it("fills pages greedily at block boundaries", () => {
    expect(starts([b(40), b(40), b(40), b(40), b(40)])).toEqual(["0.0-2.0", "2.0-4.0", "4.0-5.0"]);
    expect(paginate([b(40), b(40), b(40)], body).map((p) => p.used)).toEqual([80, 40]);
  });
  it("no blocks -> no pages; one block -> one page", () => {
    expect(paginate([], body)).toEqual([]);
    expect(paginate([b(10)], body)).toHaveLength(1);
  });
  it("an oversized block gets its own page and overflows", () => {
    expect(starts([b(30), b(250), b(30)])).toEqual(["0.0-1.0", "1.0-2.0", "2.0-3.0"]);
    expect(paginate([b(250)], body)[0].used).toBe(250);
  });
  it("page-break-before and explicit breaks (also not on an empty page)", () => {
    expect(starts([b(10), b(10, { breakBefore: true }), b(10)])).toEqual(["0.0-1.0", "1.0-3.0"]);
    expect(starts([b(10, { breakAfter: true }), b(10), b(10)])).toEqual(["0.0-1.0", "1.0-3.0"]);
    expect(starts([b(10, { breakBefore: true }), b(10)])).toEqual(["0.0-2.0"]); // first block: no empty page before it
    expect(starts([b(10, { breakAfter: true })])).toEqual(["0.0-1.0"]); // trailing break: no empty last page
  });
  it("keep-with-next moves a heading with its paragraph", () => {
    expect(starts([b(60), b(20, { keepNext: true }), b(30)])).toEqual(["0.0-1.0", "1.0-3.0"]);
    expect(starts([b(60), b(20), b(10)])).toEqual(["0.0-3.0"]);
    // a chain taller than a page is not honoured (it would never fit)
    expect(starts([b(10), b(60, { keepNext: true }), b(60)])).toEqual(["0.0-2.0", "2.0-3.0"]);
  });
  it("tables split between rows and repeat their header rows", () => {
    const t = b(120 + 4, { rows: [20, 20, 20, 20, 20, 20], headerRows: 1 });
    const pages = paginate([b(40), t], body);
    expect(pages.map((p) => `${p.from.block}.${p.from.row}-${p.to.block}.${p.to.row}`)).toEqual(["0.0-1.2", "1.2-2.0"]);
    expect(pages[0].used).toBe(40 + 4 + 40);
    expect(pages[1].repeat).toBe(1);
    expect(pages[1].used).toBe(20 + 80); // repeated header + the remaining 4 rows
  });
  it("a long table over several pages; header never stranded alone at the page end", () => {
    const pages = paginate([b(70), b(360, { rows: Array(12).fill(30), headerRows: 1 })], body);
    // 30 left < header row + one body row: the table starts on page 2 (3 rows), then header + 2 rows per page
    expect(pages.map((p) => `${p.from.block}.${p.from.row}-${p.to.block}.${p.to.row}`)).toEqual(["0.0-1.0", "1.0-1.3", "1.3-1.5", "1.5-1.7", "1.7-1.9", "1.9-1.11", "1.11-2.0"]);
    expect(pages.slice(2).every((p) => p.repeat === 1)).toBe(true);
  });
  it("cantSplit tables and rows taller than a page", () => {
    expect(starts([b(50), b(80, { rows: [40, 40], cantSplit: true })])).toEqual(["0.0-1.0", "1.0-2.0"]);
    const big = paginate([b(300, { rows: [300] })], body);
    expect(big).toHaveLength(1);
  });
  it("adjacent margins collapse (the larger wins); spacing before is dropped at the top of every page but the first", () => {
    expect(paginate([b(30, { mb: 8 }), b(30, { mt: 12, mb: 8 }), b(30, { mt: 3 })], () => 1000)[0].used).toBe(30 + 12 + 30 + 8 + 30); // 12 not 8 + 12
    const bl = [b(60, { mt: 20, mb: 5 }), b(60, { mt: 20 }), b(20, { mt: 20 })];
    expect(paginate(bl, body).map((p) => p.used)).toEqual([20 + 60, 60 + 20 + 20]); // page 1 keeps its top margin, page 2 starts flush
    expect(starts([b(50, { mt: 10 }), b(60, { mt: 30 }), b(10, { mt: 30 })])).toEqual(["0.0-1.0", "1.0-3.0"]);
  });
  it("sections: a new section starts a page unless continuous, and page height follows the section", () => {
    const bl = [b(10, { section: 0 }), b(10, { section: 1, breakBefore: true }), b(10, { section: 1 }), b(10, { section: 2, breakBefore: false })];
    const pages = paginate(bl, (_, s) => (s === 1 ? 15 : 100)); // section 1 pages hold one block each
    expect(pages.map((p) => [p.section, p.from.block, p.to.block])).toEqual([[0, 0, 1], [1, 1, 2], [1, 2, 3], [2, 3, 4]]);
  });
});
