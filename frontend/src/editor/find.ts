// Find & replace over a ProseMirror document. Pure (takes a doc and a query), the toolbar dialog applies the result.
import type { Node } from "@tiptap/pm/model";
import type { Transaction } from "@tiptap/pm/state";

export type Query = { find: string; regex: boolean; matchCase: boolean };
export type Match = { from: number; to: number };
const LEAF = "\ufffc"; // every inline atom is one character, so text offsets map 1:1 onto positions

export function toRegExp(q: Query): RegExp | null {
  if (!q.find) return null;
  try { return new RegExp(q.regex ? q.find : q.find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), q.matchCase ? "g" : "gi"); } catch { return null; }
}

export function findAll(doc: Node, q: Query, limit = 5000): Match[] {
  const re = toRegExp(q), out: Match[] = [];
  if (!re) return out;
  doc.descendants((n, pos) => {
    if (!n.isTextblock) return true;
    const text = n.textBetween(0, n.content.size, "\n", LEAF);
    re.lastIndex = 0;
    for (let m = re.exec(text); m && out.length < limit; m = re.exec(text)) {
      if (!m[0]) { re.lastIndex++; continue; }
      out.push({ from: pos + 1 + m.index, to: pos + 1 + m.index + m[0].length });
    }
    return false;
  });
  return out;
}

/** Replace the given matches (later ones first so positions stay valid); `$1` style groups work in regex mode. */
export function replaceMatches(tr: Transaction, ms: Match[], q: Query, repl: string): Transaction {
  const re = toRegExp(q);
  for (const m of [...ms].sort((a, b) => b.from - a.from)) {
    const old = tr.doc.textBetween(m.from, m.to, "\n", LEAF);
    const text = q.regex && re ? old.replace(new RegExp(re.source, q.matchCase ? "" : "i"), repl) : repl;
    tr.insertText(text, m.from, m.to);
  }
  return tr;
}

export const countWords = (text: string) => (text.trim() ? text.trim().split(/\s+/).length : 0);
