import { describe, expect, it } from "vitest";
import { datePicture, fieldValue, pageLabel } from "./fields";

const ctx = { page: "VII", pages: "11", now: new Date(2026, 10, 24, 15, 5, 9) };
describe("fields", () => {
  it("PAGE / NUMPAGES per page, others keep the cached text", () => {
    expect(fieldValue(" PAGE   \\* MERGEFORMAT ", ctx)).toBe("VII");
    expect(fieldValue(" NUMPAGES ", ctx)).toBe("11");
    expect(fieldValue(" REF Sl_DocumentTitle \\h ", ctx)).toBeUndefined();
    expect(fieldValue(" PAGE ")).toBeUndefined();
  });
  it("DATE with a Word picture", () => {
    expect(fieldValue(' DATE \\@ "dd.MM.yyyy" ', ctx)).toBe("24.11.2026");
    expect(datePicture("d MMMM yy, HH:mm:ss", ctx.now)).toBe("24 November 26, 15:05:09");
  });
  it("page number formats", () => {
    expect([1, 4, 9, 14, 1994].map((n) => pageLabel(n, "upperRoman"))).toEqual(["I", "IV", "IX", "XIV", "MCMXCIV"]);
    expect(pageLabel(3, "lowerRoman")).toBe("iii");
    expect([1, 26, 27].map((n) => pageLabel(n, "upperLetter"))).toEqual(["A", "Z", "AA"]);
    expect(pageLabel(12, "decimal")).toBe("12");
  });
});
