// Word tab stops -> HTML. Pure: segments (already-built inline HTML, split at each tab character) + stops -> one block of HTML.
// Two layouts, both approximations of Word's line layout (they assume the text before a tab is shorter than its stop):
//  * "abs": no leaders. Segment k>0 is positioned at its stop (left: from the stop; center/right: translated by -50% / -100%).
//  * "flex": some stop has a leader (TOC dot leaders). Flex row: text | filler (dotted border = leader) | text; a left stop
//    gives the preceding segment a min-width up to the stop.
export type Stop = { pos: number; val: "left" | "center" | "right" | "decimal" | "bar"; leader?: string };
const DEFAULT_TAB = 720; // twips (Word's default 0.5 inch)
const pt = (tw: number) => `${+(tw / 20).toFixed(2)}pt`;
const LEADER: Record<string, string> = { dot: "dotted", middleDot: "dotted", hyphen: "dashed", underscore: "solid", heavy: "solid" };

export function validStops(raw: unknown): Stop[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((t) => t && typeof t === "object" && Number.isFinite(t.pos) && t.pos >= 0 && t.pos <= 31680 && ["left", "center", "right", "decimal"].includes(t.val))
    .map((t) => ({ pos: t.pos, val: t.val, ...(LEADER[t.leader] ? { leader: t.leader } : {}) })).sort((a, b) => a.pos - b.pos).slice(0, 20);
}

/** The stop each tab character jumps to: the next explicit stop after the previous one, then default 0.5" stops. */
export function assignStops(nTabs: number, stops: Stop[]): Stop[] {
  const out: Stop[] = [];
  let at = 0;
  for (let k = 0; k < nTabs; k++) {
    const s = stops.find((x) => x.pos > at) ?? { pos: (Math.floor(at / DEFAULT_TAB) + 1) * DEFAULT_TAB, val: "left" as const };
    out.push(s);
    at = s.pos;
  }
  return out;
}

/** `segs`: n+1 HTML fragments for n tabs. `indent`: the paragraph's left indent in twips (stop positions are measured from the margin). */
export function tabbed(segs: string[], stops: Stop[], indent = 0): string {
  const use = assignStops(segs.length - 1, stops);
  if (use.some((s) => s.leader)) {
    let h = "";
    segs.forEach((s, k) => {
      if (k > 0) {
        const st = use[k - 1];  // a plain left stop is just the previous segment's min-width; right/centre stops and leaders need a filler
        if (st.leader || st.val !== "left") h += `<span class="tab-fill${st.leader ? " lead" : ""}"${st.leader ? ` style="border-bottom-style:${LEADER[st.leader]}"` : ""}></span>`;
      }
      const nxt = use[k], prev = k > 0 ? use[k - 1].pos : indent;
      const min = nxt && nxt.val === "left" && !nxt.leader && k < segs.length - 1 && nxt.pos - prev > 0 ? ` style="min-width:${pt(nxt.pos - prev)}"` : "";
      h += `<span class="tab-seg"${min}>${s}</span>`;
    });
    return `<span class="tabrow flex">${h}</span>`;
  }
  const first = `<span class="tab-seg">${segs[0] || "&#8203;"}</span>`;
  const rest = segs.slice(1).map((s, k) => {
    const st = use[k], x = Math.max(st.pos - indent, 0), t = st.val === "center" ? "translateX(-50%)" : st.val === "right" ? "translateX(-100%)" : "";
    return `<span class="tab-abs" style="left:${pt(x)}${t ? `;transform:${t}` : ""}">${s}</span>`;
  }).join("");
  return `<span class="tabrow abs">${first}${rest}</span>`;
}
