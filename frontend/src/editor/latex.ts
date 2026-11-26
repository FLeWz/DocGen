// LaTeX source support: static completion list (no network, no package scanning) for the LaTeX editing mode.
// Only what the bridge understands (backend/app/latex_text.py KNOWN_*): plain LaTeX, the AMS maths basics, hyperref-style links.
import { Completion, CompletionContext, CompletionResult, snippetCompletion } from "@codemirror/autocomplete";

type Item = [label: string, template: string, detail: string, kind?: "keyword" | "function" | "class" | "constant"];

const cmd = (name: string, detail: string, args = 1): Item => [`\\${name}`, `\\${name}${args ? "{${}}" : ""}`, detail, "function"];
const env = (name: string, detail: string, body = "${}"): Item => [`\\begin{${name}}`, `\\begin{${name}}\n\t${body}\n\\end{${name}}`, detail, "class"];

export const LATEX_ITEMS: Item[] = [
  cmd("chapter", "Heading 1 (sections move down one level)"), cmd("section", "Heading 1"), cmd("subsection", "Heading 2"), cmd("subsubsection", "Heading 3"), cmd("paragraph", "Heading 4"), cmd("subparagraph", "Heading 4"),
  cmd("textbf", "bold"), cmd("textit", "italic"), cmd("emph", "italic"), cmd("underline", "underline"), cmd("texttt", "monospace"), cmd("textsc", "small caps"), cmd("sout", "strike-through"),
  cmd("textsuperscript", "superscript"), cmd("textsubscript", "subscript"), ["\\textcolor", "\\textcolor[HTML]{${FF0000}}{${text}}", "colour", "function"],
  cmd("footnote", "footnote"), cmd("endnote", "endnote"), cmd("label", "label (text only)"), cmd("ref", "cross-reference"), cmd("pageref", "page of a reference"), cmd("eqref", "equation reference"),
  cmd("cite", "citation of a \\bibitem"), ["\\href", "\\href{${https://}}{${text}}", "link", "function"], cmd("url", "link"),
  ["\\includegraphics", "\\includegraphics[width=${300}px]{docgen-image:${id}}", "image of this app (docgen-image:<id>)", "function"], cmd("caption", "table/figure caption"),
  cmd("item", "list item", 0), cmd("hline", "table rule", 0), ["\\multicolumn", "\\multicolumn{${2}}{${c}}{${text}}", "merged columns", "function"], ["\\multirow", "\\multirow{${2}}{*}{${text}}", "merged rows", "function"],
  cmd("newpage", "page break", 0), cmd("clearpage", "page break", 0), cmd("tableofcontents", "table of contents", 0), cmd("listoffigures", "list of figures", 0), cmd("listoftables", "list of tables", 0),
  cmd("maketitle", "title block from \\title/\\author/\\date", 0), cmd("title", "document title"), cmd("author", "author"), cmd("date", "date"), cmd("verb", "inline code", 0), cmd("hrule", "horizontal rule", 0),
  ["\\frac", "\\frac{${a}}{${b}}", "fraction", "function"], cmd("sqrt", "square root"), ["\\sum", "\\sum_{${i=1}}^{${n}}", "sum", "function"], ["\\int", "\\int_{${a}}^{${b}}", "integral", "function"],
  cmd("mathbf", "maths bold"), cmd("mathrm", "maths roman"), cmd("text", "text inside maths"),
  env("itemize", "bullet list", "\\item ${}"), env("enumerate", "numbered list", "\\item ${}"), env("description", "description list", "\\item[${term}] ${}"),
  env("quote", "block quote"), env("quotation", "block quote"), env("verse", "block quote"), env("center", "centred paragraph"), env("flushleft", "left aligned"), env("flushright", "right aligned"),
  env("verbatim", "code block"), env("tabular", "table", "\\hline\n\t${A} & ${B} \\\\\n\t\\hline\n\t${} & \\\\\n\t\\hline"), env("table", "table with caption", "\\caption{${}}\n\t\\begin{tabular}{ll}\n\t\t${}\n\t\\end{tabular}"),
  env("figure", "figure with caption", "\\includegraphics[width=${300}px]{docgen-image:${id}}\n\t\\caption{${}}"), env("equation", "numbered equation"), env("equation*", "equation"),
  env("align", "aligned equations", "${a} &= ${b} \\\\\n\t${}"), env("align*", "aligned equations"), env("gather", "equations"), env("multline", "long equation"), env("cases", "cases"),
  env("pmatrix", "matrix"), env("thebibliography", "bibliography", "\\bibitem{${key}} ${text}"),
];

export const LATEX_COMPLETIONS: Completion[] = LATEX_ITEMS.map(([label, template, detail, kind]) => snippetCompletion(template, { label, detail, type: kind ?? "function" }));

/** `\sec` -> \section{}, `\begin{it` -> the itemize environment. Everything is filtered by label prefix by the completion engine. */
export function latexCompletion(ctx: CompletionContext): CompletionResult | null {
  const m = ctx.matchBefore(/\\(?:begin\{[A-Za-z*]*|[A-Za-z]*\*?)/);
  if (!m || (m.from === m.to && !ctx.explicit)) return null;
  return { from: m.from, options: LATEX_COMPLETIONS, validFor: /^\\(?:begin\{[A-Za-z*]*|[A-Za-z]*\*?)$/ };
}
