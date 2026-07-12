import { describe, expect, it } from "vitest";
import { shapeBreakdown } from "./ScoreBars";

describe("shapeBreakdown", () => {
  const breakdown = {
    momentum: { weight: 0.4, value: 0.9, weighted: 0.36, reason: "rising fast" },
    hype: { weight: 0.2, value: 0.1, weighted: -0.05, reason: "cooling" },
    "penalty:cooldown": { multiplier: 0.5, reason: "recently streamed" },
  };

  it("excludes penalties from the bars and scales to max abs weighted", () => {
    const { bars } = shapeBreakdown(breakdown);
    expect(bars.map((b) => b.key)).toEqual(["momentum", "hype"]);
    const momentum = bars.find((b) => b.key === "momentum")!;
    expect(momentum.widthPct).toBeCloseTo(100);
    expect(momentum.positive).toBe(true);
    const hype = bars.find((b) => b.key === "hype")!;
    expect(hype.positive).toBe(false);
    expect(hype.widthPct).toBeCloseTo((0.05 / 0.36) * 100);
  });

  it("extracts penalty rows", () => {
    const { penalties } = shapeBreakdown(breakdown);
    expect(penalties).toEqual([
      { key: "cooldown", multiplier: 0.5, reason: "recently streamed" },
    ]);
  });

  it("handles null/empty breakdown", () => {
    expect(shapeBreakdown(null)).toEqual({ bars: [], penalties: [] });
    expect(shapeBreakdown({})).toEqual({ bars: [], penalties: [] });
  });
});
