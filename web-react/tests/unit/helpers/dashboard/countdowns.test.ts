import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { describe, expect, it } from "@rstest/core";

import {
  lidCountdown,
  modeCountdown,
  recipeLabel,
} from "../../../../src/helpers/dashboard/countdowns";

const NOW = 1_700_000_000;

const at = (over: Partial<DashSocketPayload> = {}): DashSocketPayload => ({
  ...FIXTURE_DASH,
  ...over,
});

const inRecipe = (over: Partial<DashSocketPayload> = {}): DashSocketPayload =>
  at({ recipeStatus: { ...FIXTURE_DASH.recipeStatus, recipeMode: true }, ...over });

describe("modeCountdown", () => {
  it("counts Startup and Reignite down against startDuration", () => {
    const dash = at({ currentMode: "Startup", startDuration: 240, modeStartTime: NOW - 60 });
    expect(modeCountdown(dash, NOW)).toBe(180);
    expect(modeCountdown({ ...dash, currentMode: "Reignite" }, NOW)).toBe(180);
  });

  it("counts Prime down against primeDuration", () => {
    expect(
      modeCountdown(at({ currentMode: "Prime", primeDuration: 30, modeStartTime: NOW - 8 }), NOW),
    ).toBe(22);
  });

  it("counts Shutdown down against shutdownDuration", () => {
    expect(
      modeCountdown(
        at({ currentMode: "Shutdown", shutdownDuration: 240, modeStartTime: NOW - 200 }),
        NOW,
      ),
    ).toBe(40);
  });

  it("is null in every other mode", () => {
    for (const mode of ["Hold", "Smoke", "Monitor", "Stop", "Manual", "Error", ""]) {
      expect(modeCountdown(at({ currentMode: mode, startDuration: 240 }), NOW)).toBeNull();
    }
  });

  it("clamps at zero and never goes negative", () => {
    expect(
      modeCountdown(at({ currentMode: "Prime", primeDuration: 30, modeStartTime: NOW - 900 }), NOW),
    ).toBe(0);
  });

  it("is null during a recipe even when the step's sub-mode is timed", () => {
    // Flask keys the arithmetic off control["mode"], which reads "Recipe" for
    // the whole run (dash_default.js:349). The inputs are not published
    // per-step, so a per-step number would be invented.
    expect(
      modeCountdown(
        inRecipe({ currentMode: "Startup", displayMode: "Startup", startDuration: 240 }),
        NOW,
      ),
    ).toBeNull();
  });
});

describe("lidCountdown", () => {
  it("counts down to lidOpenEndTime while a lid is open in Hold", () => {
    expect(
      lidCountdown(
        at({ currentMode: "Hold", lidOpenDetected: true, lidOpenEndTime: NOW + 45 }),
        NOW,
      ),
    ).toBe(45);
  });

  it("clamps at zero", () => {
    expect(
      lidCountdown(
        at({ currentMode: "Hold", lidOpenDetected: true, lidOpenEndTime: NOW - 5 }),
        NOW,
      ),
    ).toBe(0);
  });

  it("is null when no lid is detected open", () => {
    expect(
      lidCountdown(
        at({ currentMode: "Hold", lidOpenDetected: false, lidOpenEndTime: NOW + 45 }),
        NOW,
      ),
    ).toBeNull();
  });

  it("is null outside Hold, where lid detection does not run", () => {
    expect(
      lidCountdown(
        at({ currentMode: "Smoke", lidOpenDetected: true, lidOpenEndTime: NOW + 45 }),
        NOW,
      ),
    ).toBeNull();
  });
});

describe("recipeLabel", () => {
  it("names the running step's sub-mode", () => {
    expect(recipeLabel(inRecipe({ displayMode: "Hold" }))).toBe("Recipe | Hold");
  });

  it("is null when no recipe is running", () => {
    expect(recipeLabel(at({ currentMode: "Hold", displayMode: "Hold" }))).toBeNull();
  });
});
