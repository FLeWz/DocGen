import { expect, it } from "vitest";

const files = import.meta.glob(["./**/*.ts", "./**/*.tsx", "!./**/dates*.ts", "!./nolocale.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

it("no locale-dependent date output or ISO slicing outside dates.ts", () => {
  const bad = /toLocale(Date|Time)?String|Intl\.DateTimeFormat|toISOString\(\)\.slice/;
  expect(Object.keys(files).length).toBeGreaterThan(20);
  expect(Object.keys(files).filter((f) => bad.test(files[f]))).toEqual([]);
});
