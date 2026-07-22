import { describe, expect, it } from "@rstest/core";
import { FIXTURE_DASH } from "../fixture";
import { clampSetpoint, deriveControlAlive } from "./health";

const CONTROL_DOWN = "The control process did not respond to a request and may be stopped.";

describe("deriveControlAlive", () => {
  it("true when no control-down error present", () => {
    expect(deriveControlAlive({ ...FIXTURE_DASH, errors: [] })).toBe(true);
  });
  it("false when the control-down error is present", () => {
    expect(deriveControlAlive({ ...FIXTURE_DASH, errors: [CONTROL_DOWN] })).toBe(false);
  });
});

describe("clampSetpoint", () => {
  it("clamps to the F range", () => {
    expect(clampSetpoint(50, "F")).toBe(150);
    expect(clampSetpoint(999, "F")).toBe(500);
    expect(clampSetpoint(225, "F")).toBe(225);
  });
  it("clamps to the C range", () => {
    expect(clampSetpoint(10, "C")).toBe(65);
    expect(clampSetpoint(999, "C")).toBe(260);
  });
});
