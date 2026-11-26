import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { captionJSON, captionExisting, insertWithCaption, loadCap, tableJSON } from "./captions";
import { extensions } from "./extensions";
import { captionLabel, listEntries, seqNumbers } from "./outline";
import { contentToHtml } from "./render";

const p = (t: string) => ({ type: "paragraph", content: [{ type: "text", text: t }] });
const old = (n: number) => ({ type: "paragraph", attrs: { styleId: "Caption" }, content: [{ type: "text", text: "Table " }, { type: "field", attrs: { code: "SEQ", arg: "Table \\* ARABIC", text: String(n) } }, { type: "text", text: ": imported" }] });
const mk = (...c: unknown[]) => new Editor({ extensions, content: { type: "doc", content: c as any } });
const st = (o: Record<string, string>) => ({ getItem: (k: string) => o[k] ?? null });

test("remembered choice: default on, tables above, figures below", () => {
  expect(loadCap("table", st({}))).toMatchObject({ on: true, pos: "above", label: "Table" });
  expect(loadCap("figure", st({}))).toMatchObject({ on: true, pos: "below", label: "Figure" });
  expect(loadCap("figure", st({ "docgen.caption.add": "0", "docgen.caption.pos.figure": "above" }))).toMatchObject({ on: false, pos: "above" });
});

test("table with caption above: Caption style, keepNext, SEQ field, numbered after the imported captions (arg 'Table \\* ARABIC' counts as Table)", () => {
  const e = mk(old(1), old(2), p("end")); e.commands.setTextSelection(e.state.doc.content.size - 1);
  insertWithCaption(e, tableJSON(2, 2), { on: true, pos: "above", label: "Table", text: "Sizes" });
  const blocks = e.getJSON().content!;
  const i = blocks.findIndex((b) => b.type === "table");
  expect(blocks[i - 1].attrs).toMatchObject({ styleId: "Caption", pfmt: { keepNext: true } });
  expect(captionLabel(blocks[i - 1] as any)).toBe("Table");
  const f = blocks[i - 1].content!.find((x) => x.type === "field")! as any;
  expect(f.attrs).toMatchObject({ code: "SEQ", arg: "Table", text: "3" });
  expect(seqNumbers(blocks as any)).toEqual([1, 2, 3]);
  expect(listEntries(blocks as any, { kind: "tot", label: "Table" }).map((x) => x.text)).toEqual(["Table 1: imported", "Table 2: imported", "Table 3: Sizes"]);
  expect(contentToHtml({ type: "doc", content: blocks } as any, [])).not.toMatch(/ARABIC|‹SEQ›/);
});

test("figure caption below, custom label, off = no caption; numbering follows reorders", () => {
  const e = mk(p("a"));
  e.commands.setTextSelection(2);
  insertWithCaption(e, { type: "image", attrs: { imageId: "x", alt: "", width: null } }, { on: true, pos: "below", label: "Listing", text: "Code" });
  let b = e.getJSON().content!;
  expect(b.map((x) => x.type)).toEqual(["paragraph", "image", "paragraph"]);
  expect((b[2].content![1] as any).attrs).toMatchObject({ arg: "Listing", text: "1" });
  insertWithCaption(e, tableJSON(1, 1), { on: false, pos: "above", label: "Table", text: "" });
  expect(e.getJSON().content!.filter((x) => x.attrs?.styleId === "Caption")).toHaveLength(1);
});

test("existing table: caption inserted above it from the cursor inside; numbers renumber when a caption is added before", () => {
  const e = mk(captionJSON("Table", "B", 1, true), tableJSON(1, 1)); e.commands.setTextSelection(e.state.doc.content.size - 3);
  captionExisting(e, { pos: "above", label: "Table", text: "A" });
  e.commands.insertContentAt(0, captionJSON("Table", "first", 1, true));
  expect(seqNumbers(e.getJSON().content as any)).toEqual([1, 2, 3]);
  const nums = e.getJSON().content!.filter((x) => x.attrs?.styleId === "Caption").map((x) => (x.content!.find((c) => c.type === "field") as any).attrs.text);
  expect(nums).toEqual(["1", "2", "3"]); // Derived plugin keeps the cached results current
});

test("SEQ chip reads as a plain number: no 'ARABIC', no field code, tooltip says automatic number", () => {
  const e = mk(old(7));
  const el = e.view.dom.querySelector(".fld-SEQ") as HTMLElement;
  expect(el.textContent).toBe("7");
  expect(el.title).toMatch(/Automatic number/);
  expect(e.view.dom.textContent).not.toMatch(/ARABIC|SEQ/);
});
