import { expect, test } from "vitest";
import type { PMNode } from "./doc";
import { Look, partHtml, withHf } from "./look";
import { contentBlocks } from "./render";

const p = (...c: PMNode[]): PMNode => ({ type: "paragraph", attrs: {}, content: c });
const t = (text: string): PMNode => ({ type: "text", text });
const f = (code: string, arg = "", text = ""): PMNode => ({ type: "field", attrs: { code, arg, text } });
const h = (s: string, level = 1): PMNode => ({ type: "heading", attrs: { level }, content: [t(s)] });
const html = (doc: PMNode[]) => contentBlocks({ type: "doc", content: doc }, []).map((b) => b.html).join("");

test("fields, footnotes and generated lists render in the HTML view", () => {
  const out = html([h("Alpha"), h("Beta", 2), p(t("Figure "), f("SEQ", "Figure", "1"), t(": cat")), { type: "toc", attrs: { kind: "toc", title: "Contents", levels: "1-2" } },
    { type: "toc", attrs: { kind: "tof", label: "Figure" } }, p(t("see "), f("REF", "_Ref1", "Alpha"), { type: "footnote", attrs: { kind: "footnote", text: "Source <b>" } }), { type: "sectBreak", attrs: {} }]);
  expect(out).toContain('class="toc-block toc-toc"');
  expect(out).toMatch(/toc-l2[^>]*><span>Beta/);
  expect(out).toContain("<span>Figure 1: cat</span>");        // list of figures entry
  expect(out).toContain('<sup class="fn" title="Source &lt;b&gt;">1</sup>');
  expect(out).toContain('class="fnlist"');
  expect(out).toContain("Source &lt;b&gt;");                   // escaped, never raw HTML
  expect(out).toContain('class="sectbrk"');
});

test("edited header/footer docs replace the original part; PAGE/NUMPAGES are filled per page", () => {
  const look = { parts: [{ xml: "<w:hdr/>", media: {}, path: "word/header1.xml", kind: "header" }, { xml: "<w:ftr/>", media: {}, path: "word/footer1.xml" }], header: null, footer: null } as unknown as Look;
  const edited = withHf(look, { "word/header1.xml": { type: "doc", content: [p(t("ACME "), f("PAGE"), t(" / "), f("NUMPAGES"))] } })!;
  expect(edited.parts![0].doc).toBeTruthy();
  expect(edited.parts![1].doc).toBeUndefined();
  expect(partHtml(edited.parts![0], { page: "3", pages: "12", now: new Date() })).toContain(">3</span>");
  expect(partHtml(edited.parts![0], { page: "3", pages: "12", now: new Date() })).toContain(">12</span>");
  expect(partHtml(edited.parts![0], { page: "3", pages: "12", now: new Date() })).not.toContain("‹PAGE›");
  expect(withHf(look, {})).toBe(look);                          // no edits: same object (layout caches stay valid)
});
