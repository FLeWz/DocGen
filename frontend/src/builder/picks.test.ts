import { describe, expect, it } from "vitest";
import { hasContentSlot, validatePicks } from "./picks";

const row = (value: string, name: string, include = true) => ({ value, name, include });

describe("validatePicks", () => {
  it("returns only the included rows, names trimmed", () => {
    expect(validatePicks([row("Acme", " doc.title "), row("x", "skipped", false)])).toEqual({ picks: [{ value: "Acme", name: "doc.title" }], error: "" });
  });
  it("rejects bad names and one name for two values (built-ins may repeat)", () => {
    expect(validatePicks([row("a", "bad name")]).error).toMatch(/not a valid variable name/);
    expect(validatePicks([row("a", "customer"), row("b", "customer")]).error).toMatch(/two different values/);
    expect(validatePicks([row("a", "doc.title"), row("b", "doc.title")]).error).toBe("");
  });
});

describe("hasContentSlot", () => {
  it("finds the placeholder with any spacing", () => {
    expect(hasContentSlot("before {{CONTENT}} after")).toBe(true);
    expect(hasContentSlot("{{ CONTENT }}")).toBe(true);
    expect(hasContentSlot("{{ SLOT:appendix }} {{ content }}")).toBe(false);
  });
});
