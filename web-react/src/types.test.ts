import { describe, it, expect } from "vitest";
import { FIXTURE_DASH } from "./fixture";

describe("DashData fixture shape", () => {
  it("has the real top-level keys", () => {
    for (const k of [
      "uuid", "errors", "warnings", "criticalError", "grillName", "currentMode",
      "nextMode", "displayMode", "smokePlus", "pwmControl", "pMode", "hopperLevel",
      "lidOpenDetectEnabled", "lidOpenDetected", "tempUnits", "hasDcFan",
      "hasDistanceSensor", "allowManualOutputs", "timer", "outputs",
      "recipeStatus", "foodProbes", "primaryProbe",
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
