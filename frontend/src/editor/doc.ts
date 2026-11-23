// Pure helpers over TipTap/ProseMirror JSON. No TipTap imports: shared by editor, preview and tests.
export type PMNode = { type: string; attrs?: Record<string, any>; content?: PMNode[]; marks?: { type: string; attrs?: Record<string, any> }[]; text?: string };

export const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$/;
// Built-ins are provided by the builder, not declared per snippet; `row.*` is bound by a dynamic table.
const isBuiltin = (n: string) => /^(row|doc|org)\./.test(n) || ["author", "revision_history", "linked_documents"].includes(n);

/** Style a block uses: explicit styleId, else the default for its type. Plain paragraphs in lists use ListParagraph. */
export function effectiveStyle(n: PMNode, inList = false): string | null {
  if (n.type === "heading") return n.attrs?.styleId ?? `Heading${n.attrs?.level ?? 1}`;
  if (n.type === "paragraph") return n.attrs?.styleId ?? (inList ? "ListParagraph" : "Normal");
  return null;
}

function walk(n: PMNode, fn: (n: PMNode, inList: boolean) => void, inList = false) {
  fn(n, inList);
  const l = inList || n.type === "bulletList" || n.type === "orderedList";
  n.content?.forEach((c) => walk(c, fn, l));
}

export function extractStyles(doc: PMNode): string[] {
  const out = new Set<string>();
  walk(doc, (n, inList) => { const s = effectiveStyle(n, inList); if (s) out.add(s); });
  return [...out].sort();
}

/** Declared variables: variable chips plus the list variable of each dynamic table. */
export function extractVariables(doc: PMNode): string[] {
  const out = new Set<string>();
  walk(doc, (n) => {
    if (n.type === "variable" && n.attrs?.name) out.add(n.attrs.name);
    if (n.type === "table" && n.attrs?.dynamicList) out.add(n.attrs.dynamicList);
  });
  return [...out].filter((v) => !isBuiltin(v)).sort();
}
