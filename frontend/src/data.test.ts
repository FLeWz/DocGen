import { expect, test } from "vitest";
import { folderLabel, folderPath } from "./data";

const fs = [{ id: "a", parent_id: null, name: "Root" }, { id: "b", parent_id: "a", name: "Kid" }, { id: "c", parent_id: "b", name: "Leaf" }];

test("folderPath walks from the root to the folder", () => {
  expect(folderPath(fs, "c").map((f) => f.id)).toEqual(["a", "b", "c"]);
  expect(folderLabel(fs, "b")).toBe("Root / Kid");
  expect(folderPath(fs, "nope")).toEqual([]);
  expect(folderPath(undefined, "a")).toEqual([]);
});
