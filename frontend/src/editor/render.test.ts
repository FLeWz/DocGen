import { expect, test } from "vitest";
import type { Style } from "../api";
import { PMNode } from "./doc";
import { contentToHtml } from "./render";

const st = (o: Partial<Style>): Style => ({ id: "x", style_id: "Normal", name: "Normal", kind: "paragraph", level: null, color: null, font_size: 11,
  font_family: "Calibri", numbering: "none", space_before: 0, space_after: 8, bold: false, italic: false, builtin: true, ...o });
const H = (n: number) => st({ style_id: `Heading${n}`, name: `Heading ${n}`, kind: "heading", level: n, numbering: "decimal", color: "#1f3864", font_size: 20 - n, bold: true });
const styles = [st({}), H(1), H(2), st({ style_id: "Quote", italic: true, color: "#595959" })];
const t = (text: string, marks?: PMNode["marks"]): PMNode => ({ type: "text", text, marks });
const doc = (...content: PMNode[]): PMNode => ({ type: "doc", content });

test("paragraph and heading get the style class (the look CSS supplies the appearance)", () => {
  const h = contentToHtml(doc(
    { type: "heading", attrs: { level: 1, styleId: null }, content: [t("Intro")] },
    { type: "paragraph", attrs: { styleId: "Quote" }, content: [t("hi")] }), styles);
  expect(h).toContain('<h1 class="st-Heading1">');
  expect(h).toContain('<p class="st-Quote">');
  expect(h).not.toContain("font-family"); // no inline style duplicates the stylesheet
});

test("direct formatting: fmt mark, paragraph, cell, table, list, page break, image size", () => {
  const h = contentToHtml(doc(
    { type: "paragraph", attrs: { styleId: "Callout", pfmt: { jc: "center", left: 720, first: -360, before: 240, after: 120, line: 360, lineRule: "auto",
      shd: "EEEEEE", bdr: { bottom: { val: "single", sz: 8, color: "FF0000" } } } },
      content: [t("x", [{ type: "fmt", attrs: { color: "00AA00", font: "Courier New", size: 28, hl: "FFFF00", strike: true, va: "superscript", rStyle: "Strong" } }]),
        { type: "hardBreak", attrs: { page: true } }] },
    { type: "orderedList", attrs: { listStyle: "lower-alpha" }, content: [{ type: "listItem", content: [{ type: "paragraph", attrs: { pfmt: { left: 720 } }, content: [t("i")] }] }] },
    { type: "table", attrs: { styleId: "TableGrid", grid: [2000, 1000], tfmt: { bdr: { top: { val: "single", sz: 12, color: "00FF00" } } } }, content: [
      { type: "tableRow", content: [{ type: "tableCell", attrs: { colspan: 1, rowspan: 1, cfmt: { shd: "D9E2F3", vAlign: "center" } }, content: [{ type: "paragraph" }] }] }] },
    { type: "image", attrs: { imageId: "i", width: 120, height: 40, align: "center" } }), styles);
  expect(h).toContain("text-align:center;margin-left:36pt;margin-top:12pt;margin-bottom:6pt;text-indent:-18pt;line-height:1.8;background:#EEEEEE;border-bottom:1pt solid #FF0000");
  expect(h).toContain('class="cs-Strong" style="font-family:&#39;Courier New&#39;, &#39;Liberation Mono&#39;, &#39;Cousine&#39;, sans-serif;font-size:14pt;color:#00AA00;background:#FFFF00;text-decoration:line-through;vertical-align:super;font-size:smaller"');
  expect(h).toContain('<span class="pgbrk"></span>');
  expect(h).toContain('<ol style="list-style-type:lower-alpha"><li><p class="st-ListParagraph">i</p></li></ol>'); // the list owns the indent: the item's own left indent is dropped
  expect(h).toContain('<td style="background:#D9E2F3;vertical-align:middle">');
  expect(h).toContain('class="tbl ts-TableGrid" style="--b-top:1.5pt solid #00FF00;width:150pt;table-layout:fixed;max-width:100%"');
  expect(h).toContain('<col style="width:66.67%"><col style="width:33.33%">');
  expect(h).toContain('style="width:120px;height:40px;align-self:center"');
});

test("hostile formatting values never reach a style attribute", () => {
  const h = contentToHtml(doc({ type: "paragraph", attrs: { pfmt: { jc: "x;y", shd: "red;background:url(x)", left: "1pt;color:red", bdr: { top: { val: "single", sz: 8, color: "1;2" } } },
    styleId: "a b" }, content: [t("x", [{ type: "fmt", attrs: { color: "#f00;x", font: "a;b{", size: "9;9", hl: "url(x)", rStyle: '"><b>' } }])] }), styles);
  expect(h).not.toMatch(/url\(|red;|a;b|9;9|<b>/);
  expect(h).toContain('class="st-Normal"');
});

test("heading numbering counts per level and resets deeper levels", () => {
  const hd = (level: number, s: string): PMNode => ({ type: "heading", attrs: { level, styleId: null }, content: [t(s)] });
  const h = contentToHtml(doc(hd(1, "A"), hd(2, "A1"), hd(2, "A2"), hd(1, "B"), hd(2, "B1")), styles);
  expect([...h.matchAll(/class="num">([\d.]+)</g)].map((m) => m[1])).toEqual(["1", "1.1", "1.2", "2", "2.1"]);
});

test("marks, links, variables, lists", () => {
  const h = contentToHtml(doc(
    { type: "paragraph", content: [t("b", [{ type: "bold" }, { type: "italic" }]), t("u", [{ type: "underline" }]),
      t("l", [{ type: "link", attrs: { href: "https://x.co/?a=1&b=2" } }]),
      { type: "variable", attrs: { name: "client", default: "", filter: null } },
      { type: "variable", attrs: { name: "city", default: "oslo", filter: "upper" } }] },
    { type: "bulletList", content: [{ type: "listItem", content: [{ type: "paragraph", content: [t("item")] }] }] }), styles);
  expect(h).toContain("<em><strong>b</strong></em>");
  expect(h).toContain("<u>u</u>");
  expect(h).toContain('href="https://x.co/?a=1&amp;b=2"');
  expect(h).toContain(">{{ client }}</span>");
  expect(h).toContain(">OSLO</span>");
  expect(h).toMatch(/<ul><li><p class="st-ListParagraph"/);
});

test("tables keep merged cells; dynamic tables are flagged", () => {
  const cell = (type: string, text: string, attrs: object = {}): PMNode => ({ type, attrs, content: [{ type: "paragraph", content: [t(text)] }] });
  const h = contentToHtml(doc({ type: "table", attrs: { dynamicList: "items" }, content: [
    { type: "tableRow", content: [cell("tableHeader", "H", { colspan: 2, rowspan: 1 })] },
    { type: "tableRow", content: [cell("tableCell", "a", { colspan: 1, rowspan: 2 }), cell("tableCell", "b")] },
  ] }), styles);
  expect(h).toContain('<th colspan="2">');
  expect(h).toContain('<td rowspan="2">');
  expect(h).toContain('<tr class="tpl">');
  expect(h).toContain("{{ items }}");
});

test("images use the authenticated endpoint; hostile input is escaped", () => {
  const h = contentToHtml(doc(
    { type: "image", attrs: { imageId: 'a"b', alt: "<x>", width: 200 } },
    { type: "paragraph", attrs: { styleId: '"><script>' }, content: [t("<script>alert(1)</script>", [{ type: "link", attrs: { href: "javascript:alert(1)" } }])] }), styles);
  expect(h).toContain('src="/api/images/a%22b"');
  expect(h).toContain('alt="&lt;x&gt;"');
  expect(h).not.toContain("<script>");
  expect(h).toContain('href="#"');
});

test("empty doc and unknown nodes do not throw", () => {
  expect(contentToHtml(null, styles)).toBe("");
  expect(contentToHtml(doc({ type: "mystery", content: [{ type: "paragraph" }] }), styles)).toContain("<p");
});

test("opaque block renders as its OOXML text, falling back to a chip", () => {
  const xml = '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>BOX</w:t></w:r></w:p>';
  const h = contentToHtml(doc({ type: "opaque", attrs: { kind: "text box", xml } }), styles);
  expect(h).toContain('class="opaque-view"');
  expect(h).toContain(">BOX<");
});

test("opaque block renders as an escaped grey chip, not its XML", () => {
  const h = contentToHtml(doc({ type: "opaque", attrs: { kind: "text <box>", xml: "<w:p>SECRET</w:p>" } }), styles);
  expect(h).toContain('class="opaque-chip"');
  expect(h).toContain("⬚ text &lt;box&gt;");
  expect(h).not.toContain("SECRET");
});
