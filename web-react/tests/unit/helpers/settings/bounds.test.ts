import { describe, expect, it } from "@rstest/core";

import { clampToBounds } from "../../../../src/helpers/settings/bounds";

describe("clampToBounds", () => {
  it("returns a value inside the range unchanged", () => {
    expect(clampToBounds(50, 0, 100)).toBe(50);
    expect(clampToBounds(0, 0, 100)).toBe(0);
    expect(clampToBounds(100, 0, 100)).toBe(100);
  });

  it("clamps a value below min up to min", () => {
    expect(clampToBounds(-5, 0, 100)).toBe(0);
    expect(clampToBounds(3, 20, 100)).toBe(20);
  });

  it("clamps a value above max down to max", () => {
    expect(clampToBounds(500, 0, 100)).toBe(100);
    expect(clampToBounds(9, 0, 9)).toBe(9);
    expect(clampToBounds(10, 0, 9)).toBe(9);
  });

  it("applies no lower clamp when min is undefined", () => {
    expect(clampToBounds(-999, undefined, 100)).toBe(-999);
  });

  it("applies no upper clamp when max is undefined", () => {
    expect(clampToBounds(999, 0, undefined)).toBe(999);
  });

  it("is the identity when both bounds are undefined", () => {
    expect(clampToBounds(42, undefined, undefined)).toBe(42);
    expect(clampToBounds(-42)).toBe(-42);
  });

  it("returns NaN unchanged — the caller decides, the helper does not invent a number", () => {
    expect(clampToBounds(Number.NaN, 0, 100)).toBeNaN();
    expect(clampToBounds(Number.NaN)).toBeNaN();
  });
});
