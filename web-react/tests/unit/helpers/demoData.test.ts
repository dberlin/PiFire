import { describe, expect, it } from "@rstest/core";
import { demoDashAt } from "../../../src/helpers/demoData";

describe("demo simulator", () => {
  it("starts below setpoint and climbs toward it", () => {
    const t0 = demoDashAt(0).primaryProbe.temp;
    const t60 = demoDashAt(60).primaryProbe.temp;
    expect(t0).toBeLessThan(225);
    expect(t60).toBeGreaterThan(t0);
    expect(t60).toBeLessThanOrEqual(230); // hovers near setpoint, small overshoot ok
  });

  it("holds setpoint at 225 and food target at 203", () => {
    const d = demoDashAt(120);
    expect(d.primaryProbe.setTemp).toBe(225);
    expect(d.foodProbes[0].target).toBe(203);
    expect(d.currentMode).toBe("Hold");
  });

  it("food probe is monotonic non-decreasing and capped at 205", () => {
    expect(demoDashAt(200).foodProbes[0].temp).toBeGreaterThan(demoDashAt(10).foodProbes[0].temp);
    expect(demoDashAt(10000).foodProbes[0].temp).toBe(205);
  });

  it("auger pulses on and off over its cycle", () => {
    expect(demoDashAt(5).outputs.auger).toBe(true);
    expect(demoDashAt(18).outputs.auger).toBe(false);
  });
});
