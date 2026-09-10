import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { afterEach, describe, expect, it } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useTimerVisibility } from "../../../../src/helpers/timer/timerVisibility";

afterEach(cleanup);

type Timer = Pick<DashSocketPayload["timer"], "state" | "timerId">;
const STOPPED: Timer = { state: "stopped", timerId: "00000000-0000-0000-0000-000000000000" };
const RUNNING: Timer = { state: "running", timerId: "66cc24ec-0bb4-42b7-af5d-c5e21124ec90" };

function mount(timer: Timer = STOPPED) {
  return renderHook((next: Timer) => useTimerVisibility(next), { initialProps: timer });
}

describe("useTimerVisibility", () => {
  it("does not reveal a stopped timer with the backend default UUID", () => {
    const { result } = mount();
    expect(result.current.visible).toBe(false);
  });

  it("allows manually showing and hiding a stopped timer", () => {
    const { result } = mount();
    act(() => result.current.toggle());
    expect(result.current.visible).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.visible).toBe(false);
  });

  it.each(["running", "paused", "interrupted"] as const)(
    "reveals an existing %s timer even without a known ID",
    (state) => {
      const { result } = mount({ state, timerId: null });
      expect(result.current.visible).toBe(true);
    },
  );

  it("reveals a timer that starts elsewhere", () => {
    const { result, rerender } = mount();
    rerender(RUNNING);
    expect(result.current.visible).toBe(true);
  });

  it("respects dismissal across ticks, pauses, and interruption of the same timer", () => {
    const { result, rerender } = mount(RUNNING);
    act(() => result.current.toggle());
    for (const state of ["running", "paused", "interrupted", "running"] as const) {
      rerender({ ...RUNNING, state });
      expect(result.current.visible).toBe(false);
    }
  });

  it("respects dismissal of an interrupted timer with no known ID", () => {
    const { result, rerender } = mount({ state: "interrupted", timerId: null });
    act(() => result.current.toggle());
    rerender({ state: "interrupted", timerId: null });
    expect(result.current.visible).toBe(false);
  });

  it.each(["stopped", "expired"] as const)(
    "never reveals a %s snapshot even when its ID changes",
    (state) => {
      const { result, rerender } = mount(RUNNING);
      act(() => result.current.toggle());
      rerender({ state, timerId: STOPPED.timerId });
      expect(result.current.visible).toBe(false);
      rerender({ state, timerId: null });
      expect(result.current.visible).toBe(false);
      rerender({ state, timerId: RUNNING.timerId });
      expect(result.current.visible).toBe(false);
      rerender(RUNNING);
      expect(result.current.visible).toBe(false);
    },
  );

  it("leaves an already-visible strip open at expiry", () => {
    const { result, rerender } = mount(RUNNING);
    rerender({ ...RUNNING, state: "expired" });
    expect(result.current.visible).toBe(true);
  });

  it("reveals a genuinely new active timer after dismissal and stop", () => {
    const { result, rerender } = mount(RUNNING);
    act(() => result.current.toggle());
    rerender(STOPPED);
    rerender({ ...RUNNING, timerId: "fcc611be-61db-4fd4-87b8-d401d7c10c6e" });
    expect(result.current.visible).toBe(true);
  });
});
