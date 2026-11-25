import { describe, expect, it } from "vitest";
import { bannerState, LockInfo, mmss, requestBlock, secondsLeft, skew } from "./lockstate";

const base: LockInfo = { held: false, holder: { id: "u", name: "Ann", since: "2026-11-25T09:05:00Z" }, incoming: null, pending: null, my_request: null, cooldown_until: null, server_time: "2026-11-25T10:00:00Z" };
const req = (status: string) => ({ id: "r", status, created_at: "", expires_at: "" }) as never;

describe("lock countdown", () => {
  it("counts down on the server clock", () => {
    const now = Date.parse("2026-11-25T10:00:00Z") - 7000; // client clock 7 s behind the server
    const k = skew("2026-11-25T10:00:00Z", now);
    expect(k).toBe(7000);
    expect(secondsLeft("2026-11-25T10:05:00Z", k, now)).toBe(300);
    expect(secondsLeft("2026-11-25T09:59:00Z", k, now)).toBe(0);
  });
  it("formats m:ss", () => { expect(mmss(300)).toBe("5:00"); expect(mmss(61)).toBe("1:01"); expect(mmss(0)).toBe("0:00"); });
});

describe("banner state", () => {
  it("covers editing / read-only / pending / denied / transferred", () => {
    expect(bannerState(undefined, null)).toBe("loading");
    expect(bannerState({ ...base, held: true }, null)).toBe("editing");
    expect(bannerState(base, null)).toBe("readonly");
    expect(bannerState({ ...base, my_request: req("pending") }, null)).toBe("pending");
    expect(bannerState({ ...base, my_request: req("denied"), cooldown_until: "2026-11-25T10:30:00Z" }, null)).toBe("denied");
    expect(bannerState({ ...base, my_request: req("timed_out") }, null)).toBe("readonly");
    expect(bannerState(base, "transferred")).toBe("transferred");
    expect(bannerState(base, "granted")).toBe("granted");
  });
  it("blocks requests during the cooldown or while someone else waits", () => {
    const now = Date.parse("2026-11-25T10:00:00Z");
    expect(requestBlock(base, 0, now)).toBeNull();
    expect(requestBlock({ ...base, cooldown_until: "2026-11-25T10:30:00Z" }, 0, now)).toMatch(/^You can request again at \d\d:\d\d \(\d\d\.\d\d\.\d{4}\)$/);
    expect(requestBlock({ ...base, cooldown_until: "2026-11-25T09:30:00Z" }, 0, now)).toBeNull();
    expect(requestBlock({ ...base, pending: { requester: { id: "x", name: "Bo" }, expires_at: "" } }, 0, now)).toBe("Bo has already asked for edit rights");
  });
});
