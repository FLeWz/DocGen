import { describe, expect, it } from "vitest";
import { applyDateFilter, formatDate, formatDateTime, parseDateInput } from "./dates";

describe("formatDate / formatDateTime", () => {
  it("formats ISO days, DD.MM.YYYY, Date objects and epoch, zero padded", () => {
    expect(formatDate("2026-11-24")).toBe("24.11.2026");
    expect(formatDate("2026-01-05")).toBe("05.01.2026");
    expect(formatDate("5.1.2026")).toBe("05.01.2026");
    expect(formatDate(new Date(2026, 0, 5, 23, 59))).toBe("05.01.2026");
    expect(formatDate(new Date(2026, 0, 5, 12).getTime())).toBe("05.01.2026");
  });
  it("is robust for null / empty / garbage", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatDateTime("")).toBe("");
    expect(formatDate("not a date")).toBe("not a date");
  });
  it("date-only values never shift with the time zone; timestamps use local parts", () => {
    expect(formatDate("2026-03-01")).toBe("01.03.2026"); // local midnight / UTC midnight give the same day
    const local = new Date(2026, 2, 1, 0, 30);
    expect(formatDateTime(local)).toBe("01.03.2026 00:30");
    expect(formatDateTime(local.toISOString())).toBe("01.03.2026 00:30");
  });
  it("datetime is 24 h", () => {
    expect(formatDateTime(new Date(2026, 10, 24, 15, 5))).toBe("24.11.2026 15:05");
    expect(formatDateTime(new Date(2026, 10, 24, 0, 0))).toBe("24.11.2026 00:00");
  });
});

describe("parseDateInput", () => {
  it("empty is null without an error", () => expect(parseDateInput("  ")).toEqual({ iso: null }));
  it("parses DD.MM.YYYY, D.M.YYYY and ISO to ISO", () => {
    expect(parseDateInput("24.11.2026")).toEqual({ iso: "2026-11-24" });
    expect(parseDateInput("1.2.2026")).toEqual({ iso: "2026-02-01" });
    expect(parseDateInput("2026-02-01")).toEqual({ iso: "2026-02-01" });
  });
  it("leap days", () => {
    expect(parseDateInput("29.02.2024").iso).toBe("2024-02-29");
    expect(parseDateInput("29.02.2026").error).toBe("Not a real date");
    expect(parseDateInput("29.02.1900").error).toBe("Not a real date");
    expect(parseDateInput("29.02.2000").iso).toBe("2000-02-29");
  });
  it("rejects impossible and malformed input with an inline error", () => {
    expect(parseDateInput("31.02.2026").error).toBe("Not a real date");
    expect(parseDateInput("32.01.2026").error).toBe("Not a real date");
    expect(parseDateInput("12.13.2026").error).toBe("Not a real date");
    expect(parseDateInput("24/11/2026").error).toBe("Use DD.MM.YYYY");
    expect(parseDateInput("24.11.26").error).toBe("Use DD.MM.YYYY");
  });
});

describe("applyDateFilter", () => {
  it("empty format is DD.MM.YYYY, explicit formats are honoured", () => {
    expect(applyDateFilter("2026-11-24", "")).toBe("24.11.2026");
    expect(applyDateFilter("2026-11-24", "YYYY-MM-DD")).toBe("2026-11-24");
    expect(applyDateFilter("2026-11-24", "%d/%m/%y")).toBe("24/11/26");
    expect(applyDateFilter("junk", "DD.MM.YYYY")).toBe("junk");
  });
});
