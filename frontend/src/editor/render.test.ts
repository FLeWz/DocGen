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

test("paragraph and heading use the resolved style", () => {
  const h = contentToHtml(doc(
    { type: "heading", attrs: { level: 1, styleId: null }, content: [t("Intro")] },
    { type: "paragraph", attrs: { styleId: "Quote" }, content: [t("hi")] }), styles);
  expect(h).toContain('<h1 class="st-Heading1" style="color:#1f3864;font-size:19pt;font-family:');
  expect(h).toContain("font-weight:bold");
  expect(h).toContain('<p class="st-Quote" style="color:#595959;font-size:11pt;');
  expect(h).toContain("font-style:italic");
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

test("opaque block renders as an escaped grey chip, not its XML", () => {
  const h = contentToHtml(doc({ type: "opaque", attrs: { kind: "text <box>", xml: "<w:p>SECRET</w:p>" } }), styles);
  expect(h).toContain('class="opaque-chip"');
  expect(h).toContain("⬚ text &lt;box&gt;");
  expect(h).not.toContain("SECRET");
});
