import { describe, expect, it } from "vitest";
import {
  absoluteTime,
  compactNumber,
  formatPrice,
  formatScore,
  isOlderThan,
  relativeTime,
  signedCompact,
} from "./format";

describe("compactNumber", () => {
  it("formats thousands and millions", () => {
    expect(compactNumber(1234)).toBe("1.2k");
    expect(compactNumber(5_600_000)).toBe("5.6M");
    expect(compactNumber(42)).toBe("42");
  });
  it("shows a dash for null/undefined", () => {
    expect(compactNumber(null)).toBe("—");
    expect(compactNumber(undefined)).toBe("—");
  });
});

describe("signedCompact", () => {
  it("prefixes a plus/minus sign", () => {
    expect(signedCompact(56)).toBe("+56");
    expect(signedCompact(-1234)).toBe("−1.2k");
    expect(signedCompact(0)).toBe("0");
  });
});

describe("formatScore / formatPrice", () => {
  it("renders 2-decimal score", () => {
    expect(formatScore(0.6123)).toBe("0.61");
  });
  it("renders price / free / unknown", () => {
    expect(formatPrice(1999)).toBe("$19.99");
    expect(formatPrice(null, true)).toBe("Free");
    expect(formatPrice(null)).toBe("—");
  });
});

describe("relativeTime / isOlderThan", () => {
  const now = new Date("2026-07-12T14:00:00Z");
  it("says never for null", () => {
    expect(relativeTime(null, now)).toBe("never");
  });
  it("renders hours ago", () => {
    expect(relativeTime("2026-07-12T11:00:00Z", now)).toMatch(/hours? ago/);
  });
  it("flags stale timestamps past the threshold", () => {
    expect(isOlderThan("2026-07-10T00:00:00Z", 24, now)).toBe(true);
    expect(isOlderThan("2026-07-12T13:00:00Z", 24, now)).toBe(false);
    expect(isOlderThan(null, 24, now)).toBe(true);
  });
  it("absoluteTime handles null", () => {
    expect(absoluteTime(null)).toBe("—");
  });
});
