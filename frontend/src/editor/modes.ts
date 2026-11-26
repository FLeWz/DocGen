// Editing modes: Word (the TipTap page editor) plus text views of the same JSON (markdown now, LaTeX = round 21).
// Adding a mode = one entry in MODES (+ its `format`, which the backend bridge must know) - nothing else enumerates them.
import type { PMNode } from "./doc";

export type ModeId = "word" | "markdown";
export const MODES: { id: ModeId; label: string; format: string | null; hint: string }[] = [
  { id: "word", label: "Word", format: null, hint: "The page editor" },
  { id: "markdown", label: "Markdown", format: "markdown", hint: "The same content as markdown text (Word-only formatting as {attributes})" },
];
export const DEFAULT_MODE: ModeId = "word"; // every fresh editor opens in Word; the mode is never persisted
export const modeOf = (id: ModeId) => MODES.find((m) => m.id === id)!;

export type { TextError } from "../api";
import type { TextError } from "../api";
export type Converted = { content: PMNode | null; warnings: string[]; errors: TextError[] };

export type Leave =
  | { kind: "go"; content: PMNode }                          // parsed clean: switch
  | { kind: "stay"; errors: TextError[] }                    // syntax errors: stay in the text mode
  | { kind: "ask"; warnings: string[]; content: PMNode };    // lossy: switch only on "Switch anyway"

/** Text -> Word: errors keep the user in the text, warnings need a confirmation (`lossy` = already confirmed). */
export function leaveText(r: Converted, lossy = false): Leave {
  if (r.errors.length || !r.content) return { kind: "stay", errors: r.errors.length ? r.errors : [{ line: 1, column: 1, message: "could not be converted" }] };
  return r.warnings.length && !lossy ? { kind: "ask", warnings: r.warnings, content: r.content } : { kind: "go", content: r.content };
}

/** What the debounced autosave does with a parse result: a clean parse becomes the draft, a failed one never does (the text is kept locally). */
export function autosaveAction(r: Converted): { save: PMNode | null; keepText: boolean } {
  const ok = !r.errors.length && !!r.content;
  return { save: ok ? r.content : null, keepText: !ok };
}

/** 0-based offset of a 1-based line/column in `text` (clamped), for jump-to-error. */
export function offsetOf(text: string, line: number, column: number): number {
  const lines = text.split("\n");
  const l = Math.min(Math.max(line, 1), lines.length);
  let off = 0;
  for (let i = 0; i < l - 1; i++) off += lines[i].length + 1;
  return off + Math.min(Math.max(column - 1, 0), lines[l - 1].length);
}

export const textKey = (qkey: unknown[], mode: ModeId) => `docgen.text.${mode}.${qkey.join("/")}`;
