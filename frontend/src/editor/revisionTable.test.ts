import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { extensions } from "./extensions";
import { REVISION_COLS, REVISION_LIST, revisionTableJSON } from "./revisionTable";

test("revision table: dynamic over revision_history, header + one template row, widths in the grid", () => {
  const e = new Editor({ extensions, content: { type: "doc", content: [{ type: "paragraph" }] } });
  e.commands.insertContent(revisionTableJSON());
  const t: any = e.getJSON().content!.find((n) => n.type === "table");
  expect(t.attrs.dynamicList).toBe(REVISION_LIST);
  expect(t.attrs.grid).toEqual(REVISION_COLS.map((c) => c.w));
  expect(t.attrs.grid.reduce((a: number, b: number) => a + b)).toBe(9628); // the reference table's text width
  expect(t.content).toHaveLength(2);
  expect(t.content[0].content.map((c: any) => c.content[0].content[0].text)).toEqual(REVISION_COLS.map((c) => c.label));
  expect(t.content[0].content[0].attrs.cfmt.shd).toBe("909FA4");
  expect(t.content[1].content.map((c: any) => c.content[0].content[0].attrs.name)).toEqual(["row.date", "row.version", "row.author", "row.sections", "row.commit"]);
  const cols = REVISION_COLS.map((c) => c.key);
  expect(cols).toEqual(expect.arrayContaining(["author", "sections", "commit"]));
});
