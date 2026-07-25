import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { useTimerVisibility } from "../../helpers/timer/timerVisibility";
import type { LiveState } from "../../helpers/types";
import { NavBar } from "./NavBar";
import { TimerBar } from "./TimerBar";

// The navbar stopwatch and the timer bar are two halves of one behaviour
// (base.html:50-57 + _macro_timer.html:2): the bar is hidden until the button
// reveals it. Neither component owns that state -- the shell does, via
// useTimerVisibility -- so this exercises them wired together exactly as the
// shell wires them.

const OK: CommandResult = { ok: true, message: "" };
const NOW = 1_700_000_000;

type Timer = LiveState["timer"];

const timerBlock = (over: Partial<Timer> = {}): Timer => ({
  start: 0,
  paused: 0,
  end: 0,
  keepWarm: false,
  shutdown: false,
  ...over,
});

function stubCommand(): CommandClient {
  const ok = async () => OK;
  return {
    setMode: rs.fn(ok),
    hold: rs.fn(ok),
    setSmokePlus: rs.fn(ok),
    setPMode: rs.fn(ok),
    prime: rs.fn(ok),
    timerStart: rs.fn(ok),
    timerPause: rs.fn(ok),
    timerStop: rs.fn(ok),
    timerShutdown: rs.fn(ok),
    timerKeepWarm: rs.fn(ok),
    system: rs.fn(ok),
    setUnits: rs.fn(ok),
    manualOutput: rs.fn(ok),
    manualPwm: rs.fn(ok),
  };
}

// The shape AppShell will have: one visibility hook feeding the navbar button
// and gating the bar.
function Shell({ timer, command }: { timer: Timer; command: CommandClient }) {
  const { visible, toggle } = useTimerVisibility(timer.start);
  const running = timer.start !== 0 && timer.paused === 0;
  return (
    <>
      <NavBar grillName="" timerVisible={visible} timerRunning={running} onToggleTimer={toggle} />
      {visible ? <TimerBar timer={timer} command={command} /> : null}
    </>
  );
}

function mount(over: Partial<Timer> = {}) {
  const timer = timerBlock(over);
  const router = createMemoryRouter(
    [{ path: "*", element: <Shell timer={timer} command={stubCommand()} /> }],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

const stopwatch = () => screen.getByRole("button", { name: /toggle timer bar/i });
const bar = (container: HTMLElement) => container.querySelector(".pf-timer-bar");

// The bar reads the clock itself, and one case counts live intervals, so the
// fake clock is installed before any render. Clicks go through fireEvent
// rather than userEvent: userEvent's own scheduling deadlocks against fake
// timers, and nothing here needs its richer event sequence.
beforeEach(() => {
  rs.useFakeTimers();
  rs.setSystemTime(NOW * 1000);
});

afterEach(() => {
  rs.useRealTimers();
});

describe("navbar stopwatch and timer bar", () => {
  it("hides the bar until the stopwatch is pressed", () => {
    const { container } = mount();
    expect(bar(container)).toBeNull();

    fireEvent.click(stopwatch());

    expect(bar(container)).toBeTruthy();
    expect(stopwatch().getAttribute("aria-pressed")).toBe("true");
  });

  it("hides the bar again on a second press", () => {
    const { container } = mount();

    fireEvent.click(stopwatch());
    fireEvent.click(stopwatch());

    expect(bar(container)).toBeNull();
    expect(stopwatch().getAttribute("aria-pressed")).toBe("false");
  });

  it("shows the bar unprompted when a timer is already running", () => {
    const { container } = mount({ start: NOW - 60, end: NOW + 600 });
    expect(bar(container)).toBeTruthy();
    expect(stopwatch().className).toContain("running");
  });

  it("arms no clock interval while the bar is hidden, even mid-cook", () => {
    mount({ start: NOW - 60, end: NOW + 600 });
    expect(rs.getTimerCount()).toBeGreaterThan(0);

    fireEvent.click(stopwatch());

    // Hiding unmounts the bar, which detaches the shell's last subscriber from
    // the shared clock -- nothing is left ticking for an invisible display.
    expect(rs.getTimerCount()).toBe(0);
  });
});
