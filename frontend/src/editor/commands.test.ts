import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { clearFormatting, indent, mergeFmt, setFmt, setPfmt } from "./commands";
import { extensions } from "./extensions";
import { findAll, replaceMatches } from "./find";
import { captionLabel, listEntries, nextSecId, outline, seqNumbers } from "./outline";

const p = (t: string, a: Record<string, unknown> = {}) => ({ type: "paragraph", attrs: a, content: [{ type: "text", text: t }] });
const h = (t: string, level = 1, a: Record<string, unknown> = {}) => ({ type: "heading", attrs: { level, ...a }, content: [{ type: "text", text: t }] });
const cap = (label: string, t: string) => ({ type: "paragraph", attrs: { styleId: "Caption" }, content: [{ type: "text", text: `${label} ` }, { type: "field", attrs: { code: "SEQ", arg: label, text: "1" } }, { type: "text", text: `: ${t}` }] });
const mk = (...c: unknown[]) => new Editor({ extensions, content: { type: "doc", content: c as any } });

test("mergeFmt drops empty values and keeps the rest", () => {
  expect(mergeFmt({ color: "FF0000", size: 24 }, { size: null, font: "Arial", strike: false })).toEqual({ color: "FF0000", font: "Arial" });
});

test("setFmt merges into the mark and unsets it when nothing is left", () => {
  const e = mk(p("hello")); e.commands.selectAll();
  setFmt(e, { color: "FF0000" }); setFmt(e, { size: 28 });
  expect(e.getJSON().content![0].content![0].marks).toEqual([{ type: "fmt", attrs: expect.objectContaining({ color: "FF0000", size: 28, font: null }) }]);
  setFmt(e, { color: null }); setFmt(e, { size: null });
  expect(e.getJSON().content![0].content![0].marks ?? []).toEqual([]);
});

test("paragraph formatting: alignment, spacing, indent steps, clear formatting keeps tabs", () => {
  const e = mk(p("a"), p("b")); e.commands.selectAll();
  setPfmt(e, { jc: "both", line: 360, after: 240 });
  indent(e, 1); indent(e, 1); indent(e, -1);
  expect(e.getJSON().content!.map((b) => b.attrs!.pfmt)).toEqual([{ jc: "both", line: 360, after: 240, left: 360 }, { jc: "both", line: 360, after: 240, left: 360 }]);
  e.commands.setTextSelection(2); setPfmt(e, { tabs: [{ pos: 720, val: "left" }] });
  clearFormatting(e); e.commands.selectAll(); clearFormatting(e);
  expect(e.getJSON().content![0].attrs!.pfmt).toEqual({ tabs: [{ pos: 720, val: "left" }] });
  expect(e.getJSON().content![1].attrs!.pfmt).toBeNull();
});

test("outline: headings, marked sections (consecutive blocks with one id), lists and captions", () => {
  const sec = { id: "3", kind: "appendix", title: "Annex" };
  const doc = { type: "doc", content: [h("Intro"), p("x", { sec }), h("Deep", 2, { sec }), p("after"), cap("Figure", "A"), { type: "toc", attrs: { kind: "tof", title: "Figures" } }, { type: "toc", attrs: { kind: "toc", xml: "<w:sdt/>" } }] };
  const o = outline(doc);
  expect(o.map((x) => x.kind)).toEqual(["heading", "section", "heading", "caption", "toc", "toc"]);
  expect(o[1]).toMatchObject({ sec: "appendix", id: "3", title: "Annex", from: 1, to: 3 });
  expect(o[5]).toMatchObject({ imported: true });
  expect(nextSecId(doc)).toBe("4");
  expect(captionLabel(doc.content[4])).toBe("Figure");
});

test("list entries: headings within levels, captions by label", () => {
  const blocks = [h("A"), h("B", 2), h("C", 4), cap("Figure", "cat"), cap("Table", "prices"), cap("Figure", "dog")];
  expect(listEntries(blocks, { kind: "toc", levels: "1-2" }).map((x) => x.text)).toEqual(["A", "B"]);
  expect(listEntries(blocks, { kind: "tof" }).map((x) => x.text)).toEqual(["Figure 1: cat", "Figure 1: dog"]);
  expect(listEntries(blocks, { kind: "tot" }).map((x) => x.text)).toEqual(["Table 1: prices"]);
  expect(seqNumbers(blocks)).toEqual([1, 1, 2]);
});

test("find & replace: plain, regex, case, atoms keep offsets", () => {
  const e = mk(p("Cat and cat"), { type: "paragraph", content: [{ type: "text", text: "x " }, { type: "variable", attrs: { name: "a", default: "" } }, { type: "text", text: " cat" }] });
  expect(findAll(e.state.doc, { find: "cat", regex: false, matchCase: false })).toHaveLength(3);
  expect(findAll(e.state.doc, { find: "cat", regex: false, matchCase: true })).toHaveLength(2);
  expect(findAll(e.state.doc, { find: "c.t", regex: true, matchCase: false })).toHaveLength(3);
  expect(findAll(e.state.doc, { find: "(", regex: true, matchCase: false })).toEqual([]);  // invalid regex: no crash
  const q = { find: "(c)at", regex: true, matchCase: false };
  const ms = findAll(e.state.doc, q);
  e.view.dispatch(replaceMatches(e.state.tr, ms, q, "$1ow"));
  expect(e.getText()).toContain("Cow and cow");
  expect(e.state.doc.child(1).content.size).toBe(2 + 1 + 4);  // "x " + atom + " cow": the atom is untouched
});

test("captionLabel: an imported SEQ arg keeps its switches (`Table \\* ARABIC`) but the label is the sequence name", () => {
  expect(captionLabel(cap("Table \\* ARABIC", "A"))).toBe("Table");
  expect(captionLabel(cap("AD \\* ARABIC", "A"))).toBe("AD");
});
