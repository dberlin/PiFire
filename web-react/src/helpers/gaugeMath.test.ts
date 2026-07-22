import { describe, expect, it } from "@rstest/core";
import { arcLength, clampFraction, GAUGE_START, GAUGE_SWEEP, valueAngle } from "./gaugeMath";

describe("gauge math", () => {
  it("clamps fraction to [0,1]", () => {
    expect(clampFraction(-10, 600)).toBe(0);
    expect(clampFraction(300, 600)).toBe(0.5);
    expect(clampFraction(900, 600)).toBe(1);
    expect(clampFraction(100, 0)).toBe(0); // guard divide-by-zero
  });

  it("maps value 0 to the start angle and max to start+sweep", () => {
    expect(valueAngle(0, 600)).toBe(GAUGE_START);
    expect(valueAngle(600, 600)).toBe(GAUGE_START + GAUGE_SWEEP);
    expect(valueAngle(300, 600)).toBe(GAUGE_START + GAUGE_SWEEP / 2); // midpoint = top (0°)
  });

  it("arc length scales with radius over the 270° sweep", () => {
    const r = 104;
    expect(arcLength(r)).toBeCloseTo((r * GAUGE_SWEEP * Math.PI) / 180, 5);
  });
});
