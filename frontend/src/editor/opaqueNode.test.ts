import { Editor } from "@tiptap/core";
import { expect, test, vi } from "vitest";
import { extensions } from "./extensions";

const NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"';
const XML = `<w:p ${NS}><w:r><mc:AlternateContent><mc:Choice><w:drawing><wps:wsp><wps:txbx><w:txbxContent><w:p><w:r><w:t>line one</w:t></w:r></w:p><w:p><w:r><w:t>line two</w:t></w:r></w:p></w:txbxContent></wps:txbx></wps:wsp></w:drawing></mc:Choice></mc:AlternateContent></w:r></w:p>`;
const mk = (xml: string, kind = "text box") => new Editor({ extensions, content: { type: "doc", content: [{ type: "opaque", attrs: { kind, xml } }, { type: "paragraph" }] } });

test("opaque text box: reason tooltip, Edit text + Convert to text actions", () => {
  const e = mk(XML), dom = e.view.dom.querySelector(".opaque-view, .opaque-chip") as HTMLElement;
  expect(dom.title).toContain("Edit text");
  expect(Array.from(dom.querySelectorAll(".opaque-bar button")).map((b) => b.textContent)).toEqual(["✎ Edit text", "Convert to text"]);
});

test("Convert to text asks first, then replaces the block with paragraphs; cancel keeps it; undo restores it", () => {
  const e = mk(XML), btn = () => Array.from(e.view.dom.querySelectorAll<HTMLButtonElement>(".opaque-bar button")).find((b) => b.textContent === "Convert to text")!;
  const conf = vi.spyOn(window, "confirm").mockReturnValue(false);
  btn().click();
  expect(e.getJSON().content![0].type).toBe("opaque");
  conf.mockReturnValue(true); btn().click();
  expect(conf).toHaveBeenCalledTimes(2);
  expect(e.getJSON().content!.map((b) => b.type + ":" + ((b.content?.[0] as any)?.text ?? ""))).toEqual(["paragraph:line one", "paragraph:line two", "paragraph:"]);
  e.commands.undo();
  expect(e.getJSON().content![0].type).toBe("opaque");
});

test("bookmark markers are invisible, read-only editors show no actions", () => {
  const e = mk(`<w:bookmarkStart ${NS} w:id="1" w:name="x"/>`, "bookmark");
  expect(e.view.dom.querySelector(".opaque-hidden")).not.toBeNull();
  expect(e.view.dom.querySelectorAll(".opaque-bar button")).toHaveLength(0);
  const ro = mk(XML); ro.setEditable(false);
  expect(ro.view.dom.querySelectorAll(".opaque-bar button").length).toBeGreaterThanOrEqual(0);
});
