import { afterEach, describe, expect, it } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useTimerVisibility } from "../../../../src/helpers/timer/timerVisibility";

afterEach(cleanup);

const STARTED = "66cc24ec-0bb4-42b7-af5d-c5e21124ec90";

function mount(timerId: string | null = null) {
  return renderHook((id: string | null) => useTimerVisibility(id), {
    initialProps: timerId,
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

    // Wall metadata changes cannot override dismissal of the same timer UUID.
    rerender(STARTED);
    rerender(STARTED);
    expect(result.current.visible).toBe(false);
  });

  it("leaves the bar as the user left it when the timer is cleared", () => {
    const { result, rerender } = mount(STARTED);
    act(() => result.current.toggle());
    expect(result.current.visible).toBe(false);

    rerender(null);

    expect(result.current.visible).toBe(false);
  });

  it("reveals again for the next timer after one was cleared", () => {
    const { result, rerender } = mount(STARTED);
    act(() => result.current.toggle());
    rerender(null);

    rerender("fcc611be-61db-4fd4-87b8-d401d7c10c6e");

    expect(result.current.visible).toBe(true);
  });
});
