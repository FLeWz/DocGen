// LaTeX math -> HTML with KaTeX (bundled, no CDN). The same string is exported to Word as a native equation by the backend (pandoc, OMML).
import katex from "katex";

export type Rendered = { html: string; error: string | null };

export function renderMath(latex: string, display: boolean): Rendered {
  const src = latex.trim();
  if (!src) return { html: "", error: null };
  try {
    return { html: katex.renderToString(src, { displayMode: display, throwOnError: true, trust: false, strict: "ignore", maxSize: 50, maxExpand: 500 }), error: null };
  } catch (e) {
    return { html: "", error: String((e as Error).message ?? e).replace(/^KaTeX parse error: /, "") };
  }
}
