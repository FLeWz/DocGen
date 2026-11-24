// Builder panes: [library | handle | blocks | handle | right panel]. Blocks never go below BLOCKS_MIN.
export const BLOCKS_MIN = 600, HANDLE = 6;
export const LIB = { def: 320, min: 220 }, RIGHT = { def: 500, min: 360 };
export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Largest width each side panel may take: the other one at its minimum, blocks at BLOCKS_MIN (libMax also capped at half the width). */
export function limits(total: number, hasRight: boolean) {
  const free = total - HANDLE * (hasRight ? 2 : 1) - BLOCKS_MIN;
  return { libMax: Math.max(LIB.min, Math.min(Math.floor(total / 2), free - (hasRight ? RIGHT.min : 0))), rightMax: Math.max(RIGHT.min, free - LIB.min) };
}

/** Widths actually used for the wanted ones: too little room shrinks the right panel to its minimum first, then the library; below that the page scrolls. */
export function fit(total: number, lib: number, right: number, hasRight: boolean) {
  const free = total - HANDLE * (hasRight ? 2 : 1) - BLOCKS_MIN;
  let l = clamp(lib, LIB.min, Math.max(LIB.min, Math.floor(total / 2))), r = hasRight ? Math.max(RIGHT.min, right) : 0;
  let over = l + r - free;
  if (over > 0 && hasRight) { const d = Math.min(over, r - RIGHT.min); r -= d; over -= d; }
  if (over > 0) l -= Math.min(over, l - LIB.min);
  return { lib: l, right: r };
}
