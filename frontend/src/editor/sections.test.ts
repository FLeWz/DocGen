import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { BlockSelect, pickBlock, setSelectMode } from "./blockrange";
import { extensions } from "./extensions";
import { outline } from "./outline";
import { insertList, jumpTo, markList, markSection, renameSection, unmarkList, unmarkSection } from "./sections";

const p = (t: string) => ({ type: "paragraph", content: [{ type: "text", text: t }] });
const h = (t: string) => ({ type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: t }] });
const mk = (...c: unknown[]) => new Editor({ extensions: [...extensions, BlockSelect], content: { type: "doc", content: c as any } });

test("mark, rename and unmark a section over a block range; ids stay unique", () => {
  const e = mk(h("A"), p("one"), p("two"), p("three"));
  setSelectMode(e, true); pickBlock(e, 1, false); pickBlock(e, 2, true);
  const id = markSection(e, "appendix", "Annex A");
  expect(outline(e.getJSON() as any).filter((x) => x.kind === "section")).toEqual([{ kind: "section", sec: "appendix", id, title: "Annex A", from: 1, to: 3 }]);
  pickBlock(e, 3, false);
  expect(markSection(e, "section", "Other")).not.toBe(id);
  renameSection(e, id, "Annex B");
  expect(e.getJSON().content![1].attrs!.sec.title).toBe("Annex B");
  unmarkSection(e, id);
  expect(e.getJSON().content!.slice(0, 3).map((b) => b.attrs?.sec ?? null)).toEqual([null, null, null]);
  expect(e.getJSON().content![3].attrs!.sec.title).toBe("Other");
});

test("generated lists: insert, mark a selection as a list, unmark (imported ones become opaque again)", () => {
  const e = mk(h("A"), p("old toc line 1"), p("old toc line 2"), p("end"));
  setSelectMode(e, true); pickBlock(e, 1, false); pickBlock(e, 2, true);
  markList(e, "toc", { title: "Contents" });
  const j = e.getJSON().content!;
  expect(j.map((b) => b.type)).toEqual(["heading", "toc", "paragraph"]);
  expect(j[1].attrs).toMatchObject({ kind: "toc", title: "Contents", levels: "1-3", refresh: true });
  expect(j[1].attrs!.entries).toEqual([{ level: 1, text: "A" }]);  // derived entries, shown as the view's sample
  e.commands.setTextSelection(2);
  insertList(e, "tof");
  expect(e.getJSON().content!.map((b) => b.type)).toEqual(["heading", "toc", "toc", "paragraph"]);
  unmarkList(e, 2);
  expect(e.getJSON().content!.map((b) => b.type)).toEqual(["heading", "toc", "paragraph"]);
  const imp = mk(p("x"), { type: "toc", attrs: { kind: "toc", xml: "<w:sdt/>" } });
  unmarkList(imp, 1);
  expect(imp.getJSON().content![1]).toMatchObject({ type: "opaque", attrs: { kind: "table of contents", xml: "<w:sdt/>" } });
  jumpTo(imp, 0);
  expect(imp.state.selection.from).toBe(1);
});
