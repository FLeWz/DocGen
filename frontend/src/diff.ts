// Unified diff text (as served by the API) -> rows for a side-by-side view. Pure, tested in diff.test.ts.
export type DiffRow = { kind: "hunk" | "ctx" | "chg"; l?: string; r?: string; text?: string };

export function splitDiff(text: string): DiffRow[] {
  const out: DiffRow[] = [];
  let dels: string[] = [], adds: string[] = [];
  const flush = () => { for (let i = 0; i < Math.max(dels.length, adds.length); i++) out.push({ kind: "chg", l: dels[i], r: adds[i] }); dels = []; adds = []; };
  for (const line of text.split("\n")) {
    if (line.startsWith("---") || line.startsWith("+++") || line === "") continue;
    if (line.startsWith("@@")) { flush(); out.push({ kind: "hunk", text: line }); }
    else if (line.startsWith("-")) dels.push(line.slice(1));
    else if (line.startsWith("+")) adds.push(line.slice(1));
    else { flush(); out.push({ kind: "ctx", l: line.slice(1), r: line.slice(1) }); }
  }
  flush();
  return out;
}
