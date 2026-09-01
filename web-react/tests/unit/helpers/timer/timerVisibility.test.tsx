import { afterEach, describe, expect, it } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useTimerVisibility } from "../../../../src/helpers/timer/timerVisibility";

afterEach(cleanup);

// LiveState["timer"].start values: 0 = no timer, otherwise the epoch second the
// timer was started at.
const STARTED = 1_700_000_000;

function mount(start = 0) {
  return renderHook((timerStart: number) => useTimerVisibility(timerStart), {
    initialProps: start,
  });
}

describe("useTimerVisibility", () => {
  it("starts hidden, as _macro_timer.html:2 does", () => {
    const { result } = mount();
    expect(result.current.visible).toBe(false);
  });

  it("shows and hides on the stopwatch toggle", () => {
    const { result } = mount();

    act(() => result.current.toggle());
    expect(result.current.visible).toBe(true);

    act(() => result.current.toggle());
    expect(result.current.visible).toBe(false);
  });

  it("keeps the toggle stable across re-renders so it can be a plain prop", () => {
    const { result, rerender } = mount();
    const first = result.current.toggle;
    rerender(0);
    expect(result.current.toggle).toBe(first);
  });

  it("reveals itself when a timer starts elsewhere (timer.js:150-157)", () => {
    const { result, rerender } = mount();
    expect(result.current.visible).toBe(false);

    rerender(STARTED);

    expect(result.current.visible).toBe(true);
  });

  it("reveals immediately when mounted onto an already-running timer", () => {
    const { result } = mount(STARTED);
    expect(result.current.visible).toBe(true);
  });

  it("respects a hide while the same timer keeps ticking", () => {
    const { result, rerender } = mount(STARTED);

    act(() => result.current.toggle());
    expect(result.current.visible).toBe(false);

    // Every socket payload re-renders with the same `start`; only a NEW timer
    // may override the user's choice.
    rerender(STARTED);
    rerender(STARTED);
    expect(result.current.visible).toBe(false);
  });

  it("leaves the bar as the user left it when the timer is cleared", () => {
    const { result, rerender } = mount(STARTED);
    act(() => result.current.toggle());
    expect(result.current.visible).toBe(false);

    rerender(0);

    expect(result.current.visible).toBe(false);
  });

  it("reveals again for the next timer after one was cleared", () => {
    const { result, rerender } = mount(STARTED);
    act(() => result.current.toggle());
    rerender(0);

    rerender(STARTED + 900);

    expect(result.current.visible).toBe(true);
  });
});
