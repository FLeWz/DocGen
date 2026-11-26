import { expect, test } from "vitest";
import { opaqueWhy, textboxLines, visibleLines, withTextboxLines } from "./opaqueText";

const NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" xmlns:v="urn:schemas-microsoft-com:vml"';
const box = (inner: string) => `<w:txbxContent>${inner}</w:txbxContent>`;
const P = (t: string) => `<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>${t}</w:t></w:r></w:p>`;
const XML = `<w:p ${NS}><w:r><w:t>outer </w:t></w:r><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wps:wsp><wps:spPr/><wps:txbx>${box(P("one") + P("two"))}</wps:txbx></wps:wsp></w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape><v:textbox>${box(P("one") + P("two"))}</v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>`;

test("text box lines come from the Choice copy only", () => expect(textboxLines(XML)).toEqual(["one", "two"]));
test("no text box -> null", () => expect(textboxLines(`<w:p ${NS}><w:r><w:t>x</w:t></w:r></w:p>`)).toBeNull());

test("editing the lines rewrites both copies and leaves the shape untouched", () => {
  const out = withTextboxLines(XML, ["uno", "dos", "tres\tx"])!;
  expect(textboxLines(out)).toEqual(["uno", "dos", "tres\tx"]);
  expect((out.match(/<w:txbxContent>/g) ?? []).length).toBe(2);
  expect(out.match(/uno/g)?.length).toBe(2); // Choice + Fallback
  expect(out).toContain("<wps:spPr/>"); expect(out).toContain('w:val="Code"'); expect(out).toContain("<w:b/>"); // paragraph + run formatting kept
  expect(textboxLines(withTextboxLines(XML, ["only"])!)).toEqual(["only"]);
});

test("visible lines: own text first, then the box; the fallback copy is not counted twice", () => {
  expect(visibleLines(XML).map((l) => l.text)).toEqual(["outer ", "one", "two"]);
  expect(visibleLines(XML)[1].style).toBe("Code");
});
test("reasons", () => { expect(opaqueWhy("text box")).toContain("Edit text"); expect(opaqueWhy("zzz")).toContain("zzz"); });
