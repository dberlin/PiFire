import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { describe, expect, it } from "@rstest/core";
import { deriveTimer, formatRemaining } from "../../../../src/helpers/timer/timerState";

type Timer = DashSocketPayload["timer"];

const timer = (over: Partial<Timer> = {}): Timer => ({
  start: 0,
  paused: 0,
  end: 0,
  keepWarm: false,
  shutdown: false,
  ...over,
});

describe("deriveTimer", () => {
  it("reports stopped when start is 0", () => {
    expect(deriveTimer(timer(), 1_000)).toEqual({ state: "stopped", remaining: 0 });
  });

  it("reports stopped even if stale end/paused values linger with start 0", () => {
    expect(deriveTimer(timer({ end: 5_000, paused: 900 }), 1_000)).toEqual({
      state: "stopped",
      remaining: 0,
    });
  });

  it("reports running with remaining = end - now while start > 0 and paused is 0", () => {
    expect(deriveTimer(timer({ start: 900, end: 1_600 }), 1_000)).toEqual({
      state: "running",
      remaining: 600,
    });
  });

  it("reports paused with remaining = end - paused, frozen against a ticking now", () => {
    const t = timer({ start: 900, paused: 1_000, end: 1_600 });
    expect(deriveTimer(t, 1_000)).toEqual({ state: "paused", remaining: 600 });
    // now advances; a paused timer's remaining must not move.
    expect(deriveTimer(t, 1_500)).toEqual({ state: "paused", remaining: 600 });
    expect(deriveTimer(t, 9_999)).toEqual({ state: "paused", remaining: 600 });
  });

  it("clamps an expired running timer to 0 rather than going negative", () => {
    expect(deriveTimer(timer({ start: 900, end: 1_000 }), 1_500)).toEqual({
      state: "running",
      remaining: 0,
    });
  });

  it("clamps an expired paused timer to 0 as well", () => {
    expect(deriveTimer(timer({ start: 900, paused: 1_200, end: 1_000 }), 1_200)).toEqual({
      state: "paused",
      remaining: 0,
    });
  });

  it("treats a timer that ends exactly now as 0 remaining, still running", () => {
    expect(deriveTimer(timer({ start: 900, end: 1_000 }), 1_000)).toEqual({
      state: "running",
      remaining: 0,
    });
  });

  it("rounds a fractional now down to whole seconds of remaining", () => {
    expect(deriveTimer(timer({ start: 900, end: 1_600 }), 1_000.7).remaining).toBe(599);
  });
});

describe("formatRemaining", () => {
  it("formats zero", () => {
    expect(formatRemaining(0)).toBe("00:00:00");
  });
  it("formats sub-minute values", () => {
    expect(formatRemaining(59)).toBe("00:00:59");
  });
  it("formats exactly one minute", () => {
    expect(formatRemaining(60)).toBe("00:01:00");
  });
  it("formats exactly one hour", () => {
    expect(formatRemaining(3_600)).toBe("01:00:00");
  });
  it("formats 23h59m", () => {
    expect(formatRemaining(23 * 3_600 + 59 * 60)).toBe("23:59:00");
  });
  it("formats the maximum the modal can set (23h59m59s)", () => {
    expect(formatRemaining(23 * 3_600 + 59 * 60 + 59)).toBe("23:59:59");
  });
  it("does not wrap past 24 hours -- hours keep counting up", () => {
    expect(formatRemaining(25 * 3_600)).toBe("25:00:00");
  });
  it("clamps negative input to zero rather than rendering a negative clock", () => {
    expect(formatRemaining(-5)).toBe("00:00:00");
  });
  it("truncates fractional seconds", () => {
    expect(formatRemaining(59.9)).toBe("00:00:59");
  });
});
