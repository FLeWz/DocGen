import { expect, test } from "vitest";
import { dupTitles, groupSections, Heading, upToLevel } from "./splitting";

// blocks: 0 intro, 1 H1 A, 2 x, 3 H2 A.1, 4 x, 5 H3 A.1.1, 6 x, 7 H1 B, 8 x, 9 H2 B.1, 10 x   (11 blocks)
const hs: Heading[] = [[1, 1, "A"], [3, 2, "A.1"], [5, 3, "A.1.1"], [7, 1, "B"], [9, 2, "B.1"]].map(([index, level, title]) => ({ index: index as number, level: level as number, title: title as string, blocks: 2, chars: 0 }));
const spans = (p: ReturnType<typeof groupSections>) => p.map((x) => [x.title, x.start, x.end, x.blocks]);

test("non-adjacent headings: unselected ones merge into the previous selected snippet", () => {
  expect(spans(groupSections(hs, new Set([7, 1]), 11))).toEqual([["A", 1, 7, 6], ["B", 7, 11, 4]]);
});
test("preface only when asked and only before the first selected heading", () => {
  expect(groupSections(hs, new Set([3]), 11).map((x) => x.key)).toEqual([3]);
  expect(spans(groupSections(hs, new Set([3, 9]), 11, { preface: true }))).toEqual([["Preface", 0, 3, 3], ["A.1", 3, 9, 6], ["B.1", 9, 11, 2]]);
  expect(groupSections(hs, new Set([0 + 1]), 11, { preface: true }).map((x) => x.title)).toEqual(["Preface", "A"]);
});
test("titles override, blank falls back; nothing selected = nothing", () => {
  expect(groupSections(hs, new Set([1, 7]), 11, { titles: { 1: " Intro ", 7: "  " } }).map((x) => x.title)).toEqual(["Intro", "B"]);
  expect(groupSections(hs, new Set(), 11, { preface: true })).toEqual([]);
});
test("level shortcuts and duplicate titles", () => {
  expect([...upToLevel(hs, 1)]).toEqual([1, 7]);
  expect(upToLevel(hs, 3).size).toBe(5);
  expect([...dupTitles(groupSections(hs, new Set([1, 7]), 11, { titles: { 7: "a" } }))]).toEqual(["a"]);
});
