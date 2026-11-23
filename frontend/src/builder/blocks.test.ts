import { expect, test } from "vitest";
import { insertAt, moveTo } from "./blocks";

test("insertAt clamps", () => {
  expect(insertAt([1, 2], 1, 9)).toEqual([1, 9, 2]);
  expect(insertAt([1, 2], 99, 9)).toEqual([1, 2, 9]);
});

test("moveTo uses drop-zone indexes", () => {
  expect(moveTo(["a", "b", "c"], 0, 3)).toEqual(["b", "c", "a"]); // to the end
  expect(moveTo(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]); // to the front
  expect(moveTo(["a", "b", "c"], 0, 1)).toEqual(["a", "b", "c"]); // onto itself: no-op
});
