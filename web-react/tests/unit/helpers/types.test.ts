import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { describe, expect, it } from "@rstest/core";
import { FIXTURE_DASH } from "../../../src/helpers/fixture";

const CONTRACT_DASH: DashSocketPayload = FIXTURE_DASH;

describe("LiveState fixture shape", () => {
  it("has the real top-level keys", () => {
    expect(CONTRACT_DASH).toBe(FIXTURE_DASH);
    for (const k of [
      "uuid",
      "errors",
      "warnings",
      "criticalError",
      "grillName",
      "currentMode",
      "nextMode",
      "displayMode",
      "smokePlus",
      "pwmControl",
      "pMode",
      "hopperLevel",
      "lidOpenDetectEnabled",
      "lidOpenDetected",
      "tempUnits",
      "hasDcFan",
      "hasDistanceSensor",
      "allowManualOutputs",
      "safetyMaxTemp",
      "cycleRatio",
      "fanDuty",
      "timer",
      "outputs",
      "recipeStatus",
      "foodProbes",
      "primaryProbe",
    ]) {
      expect(FIXTURE_DASH).toHaveProperty(k);
    }
  });
  it("primary probe carries the rich structure", () => {
    for (const k of ["title", "temp", "setTemp", "maxTemp", "target", "targetReq", "status"]) {
      expect(FIXTURE_DASH.primaryProbe).toHaveProperty(k);
    }
  });
});
