import { expect, test } from "vitest";
import { splitDiff } from "./diff";

test("splitDiff pairs removed and added lines and keeps context", () => {
  const rows = splitDiff("--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n keep\n-old\n-gone\n+new\n tail\n");
  expect(rows).toEqual([
    { kind: "hunk", text: "@@ -1,3 +1,3 @@" }, { kind: "ctx", l: "keep", r: "keep" },
    { kind: "chg", l: "old", r: "new" }, { kind: "chg", l: "gone", r: undefined }, { kind: "ctx", l: "tail", r: "tail" }]);
});
