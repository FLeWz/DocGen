import { Editor } from "@tiptap/core";
import { expect, test } from "vitest";
import { extensions } from "./extensions";
import { renderMath } from "./math";
import { contentToHtml } from "./render";

test("KaTeX renders the sample formulas (fractions, sums, matrices, aligned, greek, text)", () => {
  for (const f of ["\\frac{a+b}{c^2}", "\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}", "\\alpha+\\beta\\to\\gamma", "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}",
    "\\begin{aligned} x &= 1 \\\\ y &= 2 \\end{aligned}", "\\text{speed} = \\sqrt{v_x^2+v_y^2}"]) {
    const r = renderMath(f, true);
    expect(r.error, f).toBeNull();
    expect(r.html).toContain("katex");
  }
});

test("a bad formula reports the error and renders nothing; empty is not an error", () => {
  const r = renderMath("\\frac{", false);
  expect(r.html).toBe("");
  expect(r.error).toBeTruthy();
  expect(renderMath("  ", false)).toEqual({ html: "", error: null });
  expect(renderMath("\\href{javascript:alert(1)}{x}", false).html).not.toMatch(/href=/);  // trust is off: no live link
});

test("math node: attrs round-trip through JSON and HTML, atom inline", () => {
  const doc = { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "a " }, { type: "math", attrs: { latex: "x^2", display: false } }, { type: "math", attrs: { latex: "\\frac{a}{b}", display: true } }] }] };
  const e = new Editor({ extensions, content: doc });
  const out = e.getJSON().content![0].content!;
  expect(out[1]).toEqual({ type: "math", attrs: { latex: "x^2", display: false } });
  expect(out[2].attrs).toEqual({ latex: "\\frac{a}{b}", display: true });
  expect(e.view.dom.querySelectorAll(".math-node .katex").length).toBe(2);
  expect(e.view.dom.querySelector(".math-display")).not.toBeNull();
  const again = new Editor({ extensions, content: e.getHTML() }).getJSON().content![0].content!;
  expect(again.filter((n) => n.type === "math").map((n) => n.attrs)).toEqual([{ latex: "x^2", display: false }, { latex: "\\frac{a}{b}", display: true }]);
});

test("preview HTML draws equations and escapes broken ones", () => {
  const h = contentToHtml({ type: "doc", content: [{ type: "paragraph", content: [{ type: "math", attrs: { latex: "x^2", display: false } }, { type: "math", attrs: { latex: "<b>\\frac{", display: false } }] }] }, []);
  expect(h).toContain("katex");
  expect(h).not.toContain("<b>");
});
