import { describe, expect, it } from "@rstest/core";

import { shouldResetScales } from "../../../../src/components/history/scaleReset";

describe("shouldResetScales", () => {
  it("resets when the scale covers the whole current data range (not zoomed)", () => {
    expect(shouldResetScales(0, 3600, 0, 3600)).toBe(true);
  });

  it("preserves the scale when it is a strict subset of the current data range (zoomed in)", () => {
    expect(shouldResetScales(1000, 2000, 0, 3600)).toBe(false);
  });

  it("resets on a time-window change: the untouched scale still equals the OLD data range, even though the incoming data will cover a different range", () => {
    // The caller passes the range of the data the plot CURRENTLY holds (the
    // old 60-minute window), not the new 240-minute data it's about to
    // receive. Since the untouched scale equals that old range, this is
    // indistinguishable from "not zoomed" -- which is exactly right: the
    // new, differently-ranged data needs a reset, or it renders off-screen.
    const oldWindowMin = 0;
    const oldWindowMax = 3600; // 60 minutes of data, in epoch seconds
    expect(shouldResetScales(oldWindowMin, oldWindowMax, oldWindowMin, oldWindowMax)).toBe(true);
  });

  it("resets for empty data (no meaningful range)", () => {
    expect(shouldResetScales(0, 100, undefined, undefined)).toBe(true);
  });

  it("resets for a single data point (zero-width range)", () => {
    expect(shouldResetScales(5, 5, 5, 5)).toBe(true);
  });

  it("resets when scale bounds are null/undefined, e.g. before the first render", () => {
    expect(shouldResetScales(null, null, 0, 3600)).toBe(true);
    expect(shouldResetScales(undefined, undefined, 0, 3600)).toBe(true);
  });

  it("does not throw on non-finite input and picks the safe reset branch", () => {
    expect(() => shouldResetScales(Number.NaN, 100, 0, 100)).not.toThrow();
    expect(shouldResetScales(Number.NaN, 100, 0, 100)).toBe(true);
    expect(shouldResetScales(0, Number.POSITIVE_INFINITY, 0, 100)).toBe(true);
  });

  it("tolerates float drift at the edges of the range as 'not zoomed'", () => {
    expect(shouldResetScales(0.0000001, 3599.9999999, 0, 3600)).toBe(true);
  });
});
