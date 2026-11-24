import { describe, expect, it } from "vitest";
import { BLOCKS_MIN, fit, HANDLE, limits } from "./layout";

describe("fit", () => {
  it("keeps the wanted widths when there is room", () => expect(fit(1900, 320, 500, true)).toEqual({ lib: 320, right: 500 }));
  it("shrinks the right panel first, then the library; blocks stay >= 600", () => {
    expect(fit(1400, 320, 500, true)).toEqual({ lib: 320, right: 500 - (320 + 500 + 2 * HANDLE + BLOCKS_MIN - 1400) });
    expect(fit(1200, 320, 500, true)).toEqual({ lib: 228, right: 360 });
    expect(fit(900, 320, 500, true)).toEqual({ lib: 220, right: 360 }); // too narrow: minimums, the page scrolls
  });
  it("no right panel (overlay / hidden)", () => expect(fit(1000, 320, 500, false)).toEqual({ lib: 320, right: 0 }));
  it("limits leave the other panel its minimum and blocks 600", () => {
    expect(limits(1500, true)).toEqual({ libMax: 1500 - 12 - 600 - 360, rightMax: 1500 - 12 - 600 - 220 });
    expect(limits(500, false).libMax).toBe(220);
  });
});
