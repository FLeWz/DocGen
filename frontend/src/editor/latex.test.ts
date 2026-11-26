import { CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { expect, test } from "vitest";
import { LATEX_COMPLETIONS, LATEX_ITEMS, latexCompletion } from "./latex";

const at = (doc: string, explicit = false) => latexCompletion(new CompletionContext(EditorState.create({ doc }), doc.length, explicit));
const labels = (doc: string) => (at(doc)?.options ?? []).map((o) => o.label);

test("the static list has the commands and environments the bridge understands, without duplicates", () => {
  const all = LATEX_ITEMS.map((i) => i[0]);
  expect(new Set(all).size).toBe(all.length);
  for (const l of ["\\section", "\\subsection", "\\textbf", "\\emph", "\\footnote", "\\ref", "\\cite", "\\caption", "\\label", "\\includegraphics", "\\multicolumn", "\\tableofcontents",
    "\\begin{itemize}", "\\begin{enumerate}", "\\begin{tabular}", "\\begin{equation}", "\\begin{align}", "\\begin{figure}", "\\begin{table}"]) expect(all).toContain(l);
  expect(LATEX_COMPLETIONS).toHaveLength(LATEX_ITEMS.length);
});

test("snippet templates are balanced and environments close what they open", () => {
  for (const [label, tpl] of LATEX_ITEMS) {
    const bare = tpl.replace(/\$\{[^}]*\}/g, "");
    expect((bare.match(/\{/g) ?? []).length, label).toBe((bare.match(/\}/g) ?? []).length);
    const m = label.match(/^\\begin\{(.+)\}$/);
    if (m) expect(tpl, label).toContain(`\\end{${m[1]}}`);
  }
});

test("typing a backslash prefix offers the list, plain text offers nothing", () => {
  expect(at("some text")).toBeNull();
  expect(at("\\sec")?.from).toBe(0);
  expect(labels("a \\sec")).toContain("\\section");
  expect(at("a \\begin{it")?.from).toBe(2);
  expect(at("", false)).toBeNull();
  expect(at("\\", true)?.options.length).toBeGreaterThan(30);
});
