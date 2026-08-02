import { describe, expect, it } from "@rstest/core";
import {
  clampSetpoint,
  deriveControlAlive,
  setpointRange,
} from "../../../../src/helpers/dashboard/health";
import { FIXTURE_DASH } from "../../../../src/helpers/fixture";

const CONTROL_DOWN = "The control process did not respond to a request and may be stopped.";

describe("deriveControlAlive", () => {
  it("true when no control-down error present", () => {
    expect(deriveControlAlive({ ...FIXTURE_DASH, errors: [] })).toBe(true);
  });
  it("false when the control-down error is present", () => {
    expect(deriveControlAlive({ ...FIXTURE_DASH, errors: [CONTROL_DOWN] })).toBe(false);
  });
});

describe("setpointRange", () => {
  it("takes its ceiling from the grill's shutdown limit", () => {
    expect(setpointRange("F", 550)).toEqual({ min: 150, max: 550 });
    expect(setpointRange("F", 400)).toEqual({ min: 150, max: 400 });
    expect(setpointRange("C", 290)).toEqual({ min: 65, max: 290 });
  });
  it("rounds a fractional limit rather than handing a float to an input", () => {
    expect(setpointRange("F", 549.6)).toEqual({ min: 150, max: 550 });
  });
  it("falls back to the fixed ceiling when the limit is missing or unusable", () => {
    // A backend too old to send it.
    expect(setpointRange("F", undefined)).toEqual({ min: 150, max: 500 });
    expect(setpointRange("C", undefined)).toEqual({ min: 65, max: 260 });
    // A limit at or below the floor would leave one selectable temperature.
    expect(setpointRange("F", 150)).toEqual({ min: 150, max: 500 });
    expect(setpointRange("F", 0)).toEqual({ min: 150, max: 500 });
    expect(setpointRange("F", Number.NaN)).toEqual({ min: 150, max: 500 });
  });
});

describe("clampSetpoint", () => {
  it("clamps to the F range", () => {
    expect(clampSetpoint(50, "F", 550)).toBe(150);
    expect(clampSetpoint(999, "F", 550)).toBe(550);
    expect(clampSetpoint(225, "F", 550)).toBe(225);
  });
  it("clamps to the C range", () => {
    expect(clampSetpoint(10, "C", 290)).toBe(65);
    expect(clampSetpoint(999, "C", 290)).toBe(290);
  });
  it("clamps to the fallback ceiling with no limit supplied", () => {
    expect(clampSetpoint(999, "F")).toBe(500);
    expect(clampSetpoint(999, "C")).toBe(260);
  });
});
