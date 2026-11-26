import { describe, expect, it } from "vitest";
import { loadPrefs } from "./prefs";

const st = (o: Record<string, string>) => ({ getItem: (k: string) => o[k] ?? null });
describe("loadPrefs", () => {
  it("defaults to no preview at 100%", () => expect(loadPrefs(st({}))).toEqual({ pv: "off", zoom: 1 }));
  it("restores the last choice", () => expect(loadPrefs(st({ "docgen.editor.v2.preview": "word", "docgen.editor.v2.zoom": "0.6" }))).toEqual({ pv: "word", zoom: 0.6 }));
  it("falls back on garbage", () => expect(loadPrefs(st({ "docgen.editor.v2.preview": "x", "docgen.editor.v2.zoom": "7" }))).toEqual({ pv: "off", zoom: 1 }));
});
