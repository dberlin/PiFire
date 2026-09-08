import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { projectLiveDurations } from "@pifire/core/liveConnection";
import { describe, expect, it } from "@rstest/core";
import { lidCountdown, modeCountdown, recipeLabel } from "../../../../src/helpers/dashboard/countdowns";

const dash: DashSocketPayload = {
  ...FIXTURE_DASH,
  currentMode: "Startup",
  durations: { modeElapsedS: 60, modeRemainingS: 180, lidRemainingS: null, cookElapsedS: 3723, current: true, running: true },
};

it("uses explicit remaining and cook exposure, regardless of wall provenance", () => {
  for (const wall of [1_800_000_000, 1_800_003_600, 1_799_996_400]) {
    const payload = { ...dash, modeStartTime: wall, startupTimestamp: wall - 100 };
    const projected = projectLiveDurations(payload, 100_000, 105_000, true);
    expect(modeCountdown(projected)).toBe(175);
    expect(projected.durations.cookElapsedS).toBe(3728);
  }
});

it("retains unknown durations and freezes stale or inactive snapshots", () => {
  expect(modeCountdown(FIXTURE_DASH)).toBeNull();
  expect(modeCountdown(projectLiveDurations(dash, 100_000, 131_000, true))).toBe(180);
  expect(modeCountdown(projectLiveDurations({ ...dash, durations: { ...dash.durations, current: false } }, 100_000, 105_000, true))).toBe(180);
  expect(modeCountdown(projectLiveDurations({ ...dash, durations: { ...dash.durations, running: false } }, 100_000, 105_000, true))).toBe(180);
});

it("preserves cook elapsed when the mode changes and reanchors fresh snapshots", () => {
  const next = { ...dash, currentMode: "Reignite", durations: { ...dash.durations, modeElapsedS: 0, modeRemainingS: 240, cookElapsedS: 4000 } };
  const projected = projectLiveDurations(next, 140_000, 142_000, true);
  expect(projected.durations.modeElapsedS).toBe(2);
  expect(projected.durations.cookElapsedS).toBe(4002);
  expect(modeCountdown(projected)).toBe(238);
});

it("bounds a local countdown at zero without changing mode", () => {
  const projected = projectLiveDurations({ ...dash, durations: { ...dash.durations, modeRemainingS: 2 } }, 100_000, 110_000, true);
  expect(modeCountdown(projected)).toBe(0);
  expect(projected.currentMode).toBe("Startup");
});

describe("lid and recipe readouts", () => {
  it("shows explicit lid remaining only for open lid in Hold", () => {
    const open = { ...dash, currentMode: "Hold", lidOpenDetected: true, durations: { ...dash.durations, lidRemainingS: 45 } };
    expect(lidCountdown(projectLiveDurations(open, 100_000, 105_000, true))).toBe(40);
    expect(lidCountdown({ ...open, lidOpenDetected: false })).toBeNull();
    expect(lidCountdown({ ...open, currentMode: "Smoke" })).toBeNull();
  });
  it("keeps the recipe submode label and suppresses the outer mode countdown", () => {
    const recipe = { ...dash, displayMode: "Hold", recipeStatus: { ...dash.recipeStatus, recipeMode: true } };
    expect(recipeLabel(recipe)).toBe("Recipe | Hold");
    expect(modeCountdown(recipe)).toBeNull();
    expect(recipeLabel(dash)).toBeNull();
  });
});
