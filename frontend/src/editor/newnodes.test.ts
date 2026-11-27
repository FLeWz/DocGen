import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { extensions } from "./extensions";
import { contentToHtml } from "./render";

const p = (t: string) => ({ type: "paragraph", attrs: {}, content: t ? [{ type: "text", text: t }] : [] });
const mk = (...blocks: unknown[]) => new Editor({ extensions, content: { type: "doc", content: blocks as any } });

test("textbox, sdtBlock-free sectBreak and FORMTEXT fields keep their verbatim attrs through the editor", () => {
  const e = mk(
    { type: "textbox", attrs: { xml: "<w:p/>", orig: { s: 1, e: 1, h: "x" } }, content: [p("inside")] },
    { type: "sectBreak", attrs: { brk: "continuous", orient: "portrait", top: 1134, xml: "<w:p><w:pPr><w:sectPr/></w:pPr></w:p>", orig: { s: 2, e: 2, h: "y" } } },
    { type: "paragraph", attrs: {}, content: [{ type: "field", attrs: { code: "FORMTEXT", arg: "", text: "T", ff: "<w:ffData/>", bm: "Bm", bmo: true } }] });
  const j: any = e.getJSON().content;
  expect(j[0].type).toBe("textbox"); expect(j[0].attrs.xml).toBe("<w:p/>"); expect(j[0].attrs.orig.h).toBe("x"); expect(j[0].content[0].content[0].text).toBe("inside");
  expect(j[1].attrs.xml).toContain("sectPr"); expect(j[1].attrs.orig.s).toBe(2); expect(j[1].attrs.brk).toBe("continuous");
  expect(j[2].content[0].attrs).toMatchObject({ code: "FORMTEXT", text: "T", ff: "<w:ffData/>", bm: "Bm", bmo: true });
});

test("a text box is editable content: typing inside it changes the nested paragraph only", () => {
  const e = mk(p("a"), { type: "textbox", attrs: { xml: "<w:p/>" }, content: [p("box")] });
  let hit = -1; e.state.doc.descendants((n, pos) => { if (n.isText && n.text === "box") hit = pos + 1; });
  e.commands.setTextSelection(hit); e.commands.insertContent("X");
  expect(JSON.stringify(e.getJSON())).toContain("bXox");
});

test("the HTML view draws the text box as a framed block with its paragraphs", () => {
  const h = contentToHtml({ type: "doc", content: [{ type: "textbox", attrs: { xml: "<w:p/>" }, content: [p("hello")] }] } as any, []);
  expect(h).toContain('class="txbx"'); expect(h).toContain("hello");
});
