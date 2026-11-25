import { expect, test } from "vitest";
import { ApiError } from "./api";
import { conflictsOf, conflictText, defaultChecked, draftHeld, Existing, PlanRow, rankSnippets, rowActions, similarity, summarize } from "./importSnippets";

const ex = (title: string): Existing => ({ snippet_id: title, title, slug: title, head_sha: "a", folder_id: "f", folder_name: "Manuals", last_commit_at: "2026-11-03T10:00:00+00:00", last_commit_author: "Ana", has_draft: false });

test("similarity: exact, case/space-insensitive, fuzzy ordering", () => {
  expect(similarity("Revision history", "  revision  HISTORY ")).toBe(1);
  expect(similarity("Revision histories", "Revision history")).toBeGreaterThan(0.8);
  expect(similarity("Revision histories", "Opaque things")).toBeLessThan(0.3);
  expect(similarity("a", "b")).toBe(0);
  expect(rankSnippets("Revision histories", [{ title: "Opaque things" }, { title: "Revision history" }])[0].x.title).toBe("Revision history");
});

test("conflict rows: default update for existing, none for new, summary counts", () => {
  const e = { 1: ex("A"), 5: null, 9: ex("C"), 12: ex("D") };
  expect(rowActions([1, 5, 9, 12], e, {})).toEqual({ 1: "update_existing", 9: "update_existing", 12: "update_existing" });
  const chosen = { 9: "create_new_with_suffix", 12: "skip" } as const;
  expect(rowActions([1, 5, 9, 12], e, chosen)[9]).toBe("create_new_with_suffix");
  expect(summarize([1, 5, 9, 12], e, chosen)).toEqual({ created: 2, updated: 1, skipped: 1 });
});

test("409 payloads and the dialog sentence (DD.MM.YYYY)", () => {
  const n = new ApiError(409, "x", { error: "name_exists", conflicts: [ex("A")] });
  expect(conflictsOf(n)?.[0].title).toBe("A");
  expect(conflictsOf(new ApiError(409, "x", { error: "draft_exists" }))).toBeNull();
  expect(draftHeld(new ApiError(409, "x", { error: "draft_exists" }))).toBe(true);
  expect(conflictsOf(new ApiError(422, "x"))).toBeNull();
  expect(conflictText(ex("A"))).toBe("A snippet titled “A” already exists in Manuals (last commit 03.11.2026 by Ana).");
});

test("update dialog: no-change rows are not ticked by default", () => {
  const row = (key: number, target: string, same: boolean | null): PlanRow => ({ key, title: "t", start: key, end: key + 1, blocks: 1, level: 1, match: null, target, has_draft: false, changes: same === null ? null : { added: 0, removed: 0, changed: same ? 0 : 1, same } });
  expect([...defaultChecked([row(1, "s", false), row(2, "s", true), row(3, "skip", null), row(4, "new", null)])]).toEqual([1, 4]);
});
