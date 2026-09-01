import { describe, expect, it } from "@rstest/core";

import { formatUsage } from "../../../../src/helpers/pellets/usage";

describe("formatUsage", () => {
  it("reports ounces at or below one pound", () => {
    // 400 g -> 0.88 lbs, which is NOT > 1, so Flask shows ounces.
    expect(formatUsage(400).imperial).toBe("14.11 ozs");
  });
  it("reports pounds above one pound", () => {
    expect(formatUsage(1000).imperial).toBe("2.2 lbs");
  });
  it("reports grams below a kilo", () => {
    expect(formatUsage(999.456).metric).toBe("999.46 g");
  });
  it("reports kilos at a kilo and above", () => {
    expect(formatUsage(1000).metric).toBe("1 kg");
  });
  it("is zero-safe", () => {
    expect(formatUsage(0)).toEqual({ imperial: "0 ozs", metric: "0 g" });
  });
});
