import { expect, test } from "vitest";
import { folderLabel, folderPath, renamedTip } from "./data";

const fs = [{ id: "a", parent_id: null, name: "Root" }, { id: "b", parent_id: "a", name: "Kid" }, { id: "c", parent_id: "b", name: "Leaf" }];

test("folderPath walks from the root to the folder", () => {
  expect(folderPath(fs, "c").map((f) => f.id)).toEqual(["a", "b", "c"]);
  expect(folderLabel(fs, "b")).toBe("Root / Kid");
  expect(folderPath(fs, "nope")).toEqual([]);
  expect(folderPath(undefined, "a")).toEqual([]);
});

test("renamedTip shows the original message, author and a DD.MM.YYYY HH:mm date", () => {
  expect(renamedTip({ original_message: "fix typo", renamed_by: "Ann", renamed_at: "2026-03-04T10:05:00" })).toBe("Originally: fix typo — renamed by Ann on 04.03.2026 10:05");
  expect(renamedTip({})).toBe("Originally: (no message)");
});
