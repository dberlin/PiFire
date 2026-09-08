import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { projectLiveDurations } from "@pifire/core/liveConnection";
import { describe, expect, it } from "@rstest/core";
import { deriveTimer, formatRemaining } from "../../../../src/helpers/timer/timerState";

const running: DashSocketPayload = {
  ...FIXTURE_DASH,
  timer: { ...FIXTURE_DASH.timer, timerId: "66cc24ec-0bb4-42b7-af5d-c5e21124ec90", state: "running", remainingS: 600, current: true },
};

describe("timer duration projection", () => {
  it("ignores wall labels and preserves timer identity", () => {
    for (const wall of [1_800_000_000, 1_799_996_400, 1_800_003_600]) {
      const dash = { ...running, timer: { ...running.timer, startedWallS: wall, projectedEndWallS: wall + 600 } };
      const projected = projectLiveDurations(dash, 100_000, 105_000, true);
      expect(deriveTimer(projected.timer)).toEqual({ state: "running", remaining: 595 });
      expect(projected.timer.timerId).toBe(running.timer.timerId);
    }
  });

  it("freezes paused and interrupted checkpoints and preserves unknown remaining", () => {
    const states: DashSocketPayload["timer"]["state"][] = ["paused", "interrupted"];
    for (const state of states) {
      const dash = { ...running, timer: { ...running.timer, state } };
      expect(projectLiveDurations(dash, 100_000, 120_000, true).timer.remainingS).toBe(600);
    }
    expect(deriveTimer({ ...running.timer, state: "interrupted", remainingS: null }).remaining).toBeNull();
    expect(formatRemaining(null)).toBe("--:--:--");
  });

  it("local zero never changes the controller's running state", () => {
    const dash = { ...running, timer: { ...running.timer, remainingS: 2 } };
    expect(deriveTimer(projectLiveDurations(dash, 100_000, 110_000, true).timer))
      .toEqual({ state: "running", remaining: 0 });
  });

  it("allows exact 30 seconds but rejects stale, negative and disconnected receipts", () => {
    expect(projectLiveDurations(running, 100_000, 130_000, true).timer.remainingS).toBe(570);
    const invalidReceipts: [number | null, number, boolean][] = [
      [100_000, 130_001, true], [100_000, 99_999, true],
      [100_000, 105_000, false], [null, 105_000, true],
    ];
    for (const [received, now, connected] of invalidReceipts) {
      const timer = projectLiveDurations(running, received, now, connected).timer;
      expect(timer.current).toBe(false);
      expect(timer.remainingS).toBe(600);
    }
  });

  it("a fresh receipt reanchors to server remaining, not the earlier projection", () => {
    const refreshed = { ...running, timer: { ...running.timer, remainingS: 480 } };
    expect(projectLiveDurations(refreshed, 140_000, 142_000, true).timer.remainingS).toBe(478);
  });
});

describe("formatRemaining", () => {
  it("formats duration boundaries without wrapping hours", () => {
    expect(formatRemaining(0)).toBe("00:00:00");
    expect(formatRemaining(3661)).toBe("01:01:01");
    expect(formatRemaining(108000)).toBe("30:00:00");
  });
});
