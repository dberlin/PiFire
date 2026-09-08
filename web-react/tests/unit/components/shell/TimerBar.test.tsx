import { createCommand } from "@pifire/core/command";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { projectLiveDurations } from "@pifire/core/liveConnection";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TimerBar } from "../../../../src/components/shell/TimerBar";

const running: DashSocketPayload = {
  ...FIXTURE_DASH,
  timer: { ...FIXTURE_DASH.timer, timerId: "66cc24ec-0bb4-42b7-af5d-c5e21124ec90", state: "running", current: true, remainingS: 600 },
};

afterEach(() => { cleanup(); rs.unstubAllGlobals(); });

function mount(timer: DashSocketPayload["timer"] = FIXTURE_DASH.timer) {
  const fetchMock = rs.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => ({ ok: true, json: async () => ({ result: "OK" }) }));
  rs.stubGlobal("fetch", fetchMock);
  const command = createCommand("");
  return { ...render(<TimerBar timer={timer} command={command} />), command, fetchMock };
}

describe("TimerBar", () => {
  it("opens a duration form when stopped", () => {
    mount();
    expect(screen.getByText("--:--:--")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start timer" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders projected zero without emitting an expiry action", () => {
    const view = mount(running.timer);
    expect(screen.getByText("00:10:00")).toBeTruthy();
    const nearExpiry = { ...running, timer: { ...running.timer, remainingS: 2 } };
    const timer = projectLiveDurations(nearExpiry, 100_000, 110_000, true).timer;
    view.rerender(<TimerBar timer={timer} command={view.command} />);
    expect(screen.getByText("00:00:00")).toBeTruthy();
    expect(view.fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Pause timer" })).toBeTruthy();
  });

  it("pauses and stops only on operator commands", async () => {
    const { fetchMock } = mount(running.timer);
    fireEvent.click(screen.getByRole("button", { name: "Pause timer" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop timer" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual(["/api/set/timer/pause", "/api/set/timer/stop"]);
  });

  it("keeps interrupted checkpoint remaining visible and requires explicit resume", async () => {
    const { fetchMock } = mount({ ...running.timer, state: "interrupted", current: false });
    expect(screen.getByText(/remaining from last checkpoint/)).toBeTruthy();
    expect(screen.getByText("00:10:00")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Resume timer" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/set/timer/start/600");
  });

  it("unknown interrupted timers offer a new duration, not resume", () => {
    mount({ ...running.timer, state: "interrupted", remainingS: null, current: false });
    expect(screen.getByText("--:--:--")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Resume timer" })).toBeNull();
    expect(screen.getByRole("button", { name: "Start timer" })).toBeTruthy();
  });

  it("qualifies retained data and leaves paused remaining frozen", () => {
    const paused: DashSocketPayload = { ...running, timer: { ...running.timer, state: "paused" } };
    mount(projectLiveDurations(paused, null, 500_000, true).timer);
    expect(screen.getByText("Last reported")).toBeTruthy();
    expect(screen.getByText("00:10:00")).toBeTruthy();
  });
});
