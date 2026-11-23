import { expect, test } from "vitest";
import { isAdmin, readCookie } from "./api";

test("readCookie picks the named cookie", () => {
  expect(readCookie("docgen_csrf", "a=1; docgen_csrf=tok.sig; b=2")).toBe("tok.sig");
  expect(readCookie("nope", "a=1")).toBeUndefined();
});

test("isAdmin", () => {
  expect(isAdmin("owner") && isAdmin("admin")).toBe(true);
  expect(isAdmin("editor") || isAdmin("viewer") || isAdmin(undefined)).toBe(false);
});
