import { expect, test } from "vitest";
import { autosaveAction, DEFAULT_MODE, leaveText, MODES, modeOf, offsetOf, textKey } from "./modes";

const doc = { type: "doc", content: [{ type: "paragraph" }] };

test("registry: Word is the default and has no text format; text modes name their backend format", () => {
  expect(DEFAULT_MODE).toBe("word");
  expect(modeOf("word").format).toBeNull();
  expect(MODES.filter((m) => m.format).map((m) => m.format)).toEqual(["markdown"]);
  expect(new Set(MODES.map((m) => m.id)).size).toBe(MODES.length);
});

test("leaving a text mode: clean parse goes, errors stay, warnings ask first and pass once confirmed", () => {
  expect(leaveText({ content: doc, warnings: [], errors: [] }).kind).toBe("go");
  const bad = leaveText({ content: null, warnings: [], errors: [{ line: 3, column: 1, message: "x" }] });
  expect(bad).toEqual({ kind: "stay", errors: [{ line: 3, column: 1, message: "x" }] });
  expect(leaveText({ content: doc, warnings: ["hr dropped"], errors: [] }).kind).toBe("ask");
  expect(leaveText({ content: doc, warnings: ["hr dropped"], errors: [] }, true).kind).toBe("go");
  expect(leaveText({ content: doc, warnings: ["w"], errors: [{ line: 1, column: 1, message: "e" }] }, true).kind).toBe("stay"); // lossy never overrides errors
  expect(leaveText({ content: null, warnings: [], errors: [] }).kind).toBe("stay");
});

test("autosave in text mode: clean parse -> save JSON; failed parse -> no JSON, keep the text locally", () => {
  expect(autosaveAction({ content: doc, warnings: [], errors: [] })).toEqual({ save: doc, keepText: false });
  expect(autosaveAction({ content: doc, warnings: ["w"], errors: [] })).toEqual({ save: doc, keepText: false });
  expect(autosaveAction({ content: null, warnings: [], errors: [{ line: 1, column: 1, message: "e" }] })).toEqual({ save: null, keepText: true });
  expect(autosaveAction({ content: doc, warnings: [], errors: [{ line: 1, column: 1, message: "e" }] }).save).toBeNull();
});

test("error jump: 1-based line/column to a clamped offset", () => {
  const t = "ab\ncde\n\nf";
  expect(offsetOf(t, 1, 1)).toBe(0);
  expect(offsetOf(t, 2, 2)).toBe(4);
  expect(offsetOf(t, 4, 1)).toBe(8);
  expect(offsetOf(t, 99, 99)).toBe(9);
  expect(offsetOf(t, 1, 50)).toBe(2);
  expect(offsetOf(t, 0, 0)).toBe(0);
});

test("the unsaved text of a failed parse is keyed per editor and mode", () => {
  expect(textKey(["draft", "abc"], "markdown")).toBe("docgen.text.markdown.draft/abc");
  expect(textKey(["draft", "abc"], "markdown")).not.toBe(textKey(["draft", "xyz"], "markdown"));
});
