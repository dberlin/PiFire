import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { CommandClient, CommandResult } from "../../helpers/command";
import type { LiveState } from "../../helpers/types";
import { TimerBar } from "./TimerBar";

const OK: CommandResult = { ok: true, message: "" };

type Timer = LiveState["timer"];

// A fixed wall clock so "remaining" is deterministic. Epoch SECONDS, matching
// the control process's math.trunc'd timer block.
const NOW = 1_700_000_000;

const timer = (over: Partial<Timer> = {}): Timer => ({
  start: 0,
  paused: 0,
  end: 0,
  keepWarm: false,
  shutdown: false,
  ...over,
});

function stubCommand(): CommandClient {
  return {
    setMode: rs.fn(async () => OK),
    hold: rs.fn(async () => OK),
    setSmokePlus: rs.fn(async () => OK),
    setPMode: rs.fn(async () => OK),
    prime: rs.fn(async () => OK),
    timerStart: rs.fn(async () => OK),
    timerPause: rs.fn(async () => OK),
    timerStop: rs.fn(async () => OK),
    timerShutdown: rs.fn(async () => OK),
    timerKeepWarm: rs.fn(async () => OK),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
    manualOutput: rs.fn(async () => OK),
    manualPwm: rs.fn(async () => OK),
  };
}

function mount(over: Partial<Timer> = {}) {
  const command = stubCommand();
  const view = render(<TimerBar timer={timer(over)} command={command} />);
  return { command, view };
}

async function tick(ms: number) {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(ms);
  });
}

// Frozen clock for every case: the bar reads Date.now() itself, so the fake
// timers must be installed before any render.
beforeEach(() => {
  rs.useFakeTimers();
  rs.setSystemTime(NOW * 1000);
});

afterEach(() => {
  rs.useRealTimers();
});

const btn = (name: RegExp | string) => screen.queryByRole("button", { name });

describe("TimerBar when stopped", () => {
  it("offers only a start affordance", () => {
    mount();
    expect(btn(/start timer/i)).toBeTruthy();
    expect(btn(/pause timer/i)).toBeNull();
    expect(btn(/resume timer/i)).toBeNull();
    expect(btn(/stop timer/i)).toBeNull();
  });

  it("shows a placeholder rather than a zeroed clock", () => {
    const { view } = mount();
    expect(view.container.querySelector(".pf-timer-time")?.textContent).toBe("--:--:--");
  });

  it("ignores a stale end time left over from a previous cook", () => {
    mount({ end: NOW + 5_000, paused: NOW - 10 });
    expect(btn(/start timer/i)).toBeTruthy();
    expect(btn(/stop timer/i)).toBeNull();
  });

  it("opens the set-timer modal from the start affordance", () => {
    mount();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /start timer/i }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("TimerBar when running", () => {
  it("offers pause and stop but no start", () => {
    mount({ start: NOW - 60, end: NOW + 600 });
    expect(btn(/pause timer/i)).toBeTruthy();
    expect(btn(/stop timer/i)).toBeTruthy();
    expect(btn(/start timer/i)).toBeNull();
  });

  it("renders the remaining time and ticks it down", async () => {
    const { view } = mount({ start: NOW - 60, end: NOW + 600 });
    const time = () => view.container.querySelector(".pf-timer-time")?.textContent;
    expect(time()).toBe("00:10:00");

    await tick(5_000);
    expect(time()).toBe("00:09:55");

    await tick(55_000);
    expect(time()).toBe("00:09:00");
  });

  it("clamps an expired timer at zero instead of counting into the negative", async () => {
    const { view } = mount({ start: NOW - 60, end: NOW + 2 });
    await tick(10_000);
    expect(view.container.querySelector(".pf-timer-time")?.textContent).toBe("00:00:00");
  });

  it("pauses via the pause command", () => {
    const { command } = mount({ start: NOW - 60, end: NOW + 600 });
    fireEvent.click(screen.getByRole("button", { name: /pause timer/i }));
    expect(command.timerPause).toHaveBeenCalled();
  });

  it("stops via the stop command", () => {
    const { command } = mount({ start: NOW - 60, end: NOW + 600 });
    fireEvent.click(screen.getByRole("button", { name: /stop timer/i }));
    expect(command.timerStop).toHaveBeenCalled();
  });

  it("stops ticking once unmounted", async () => {
    const { view } = mount({ start: NOW - 60, end: NOW + 600 });
    expect(rs.getTimerCount()).toBeGreaterThan(0);
    view.unmount();
    expect(rs.getTimerCount()).toBe(0);
  });
});

describe("TimerBar when paused", () => {
  it("offers resume and stop but no pause", () => {
    mount({ start: NOW - 600, paused: NOW - 10, end: NOW + 590 });
    expect(btn(/resume timer/i)).toBeTruthy();
    expect(btn(/stop timer/i)).toBeTruthy();
    expect(btn(/pause timer/i)).toBeNull();
    expect(btn(/start timer/i)).toBeNull();
  });

  it("freezes the remaining time while the wall clock advances", async () => {
    const { view } = mount({ start: NOW - 600, paused: NOW - 10, end: NOW + 590 });
    const time = () => view.container.querySelector(".pf-timer-time")?.textContent;
    expect(time()).toBe("00:10:00");

    await tick(120_000);
    expect(time()).toBe("00:10:00");
  });

  // /api/set/timer/start doubles as the unpause command: when timer.paused is
  // non-zero the backend shifts the existing end time and IGNORES the seconds
  // argument entirely (common/api_commands.py _cmd_set_timer).
  it("resumes by re-issuing the start command", () => {
    const { command } = mount({ start: NOW - 600, paused: NOW - 10, end: NOW + 590 });
    fireEvent.click(screen.getByRole("button", { name: /resume timer/i }));
    expect(command.timerStart).toHaveBeenCalled();
    expect(command.timerPause).not.toHaveBeenCalled();
  });
});
