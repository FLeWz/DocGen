/** Split-by-headings: which snippets result from the chosen headings (mirrors the server's `_sections`). */
export type Heading = { index: number; level: number; title: string; blocks: number; chars: number };
export type Piece = { key: number; title: string; start: number; end: number; blocks: number };

export const PREFACE = -1;

/** A snippet runs from its heading to the next SELECTED heading (unselected headings merge into the previous one);
 *  blocks before the first selected heading are a "Preface" piece only when `preface` is on. `titles` overrides per heading index. */
export function groupSections(hs: Heading[], picked: Set<number>, total: number, o: { preface?: boolean; titles?: Record<number, string> } = {}): Piece[] {
  const cuts = hs.filter((h) => picked.has(h.index)).map((h) => h.index).sort((a, b) => a - b);
  const title = (k: number, d: string) => o.titles?.[k]?.trim() || d;
  const out: Piece[] = [];
  if (o.preface && cuts.length && cuts[0] > 0) out.push({ key: PREFACE, title: title(PREFACE, "Preface"), start: 0, end: cuts[0], blocks: cuts[0] });
  cuts.forEach((s, i) => { const e = cuts[i + 1] ?? total; out.push({ key: s, title: title(s, hs.find((h) => h.index === s)!.title), start: s, end: e, blocks: e - s }); });
  return out;
}

export const upToLevel = (hs: Heading[], max: number) => new Set(hs.filter((h) => h.level <= max).map((h) => h.index));

/** Titles used more than once (case-insensitive): their slugs get a -2, -3 suffix. */
export const dupTitles = (ps: Piece[]) => { const c = new Map<string, number>(); ps.forEach((p) => c.set(p.title.toLowerCase(), (c.get(p.title.toLowerCase()) ?? 0) + 1)); return new Set([...c].filter(([, n]) => n > 1).map(([t]) => t)); };
