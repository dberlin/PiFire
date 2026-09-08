import { createCommand } from "@pifire/core/command";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TimerBar } from "../../../../src/components/shell/TimerBar";

// Queue application belongs to the Python controller tests. These scenarios
// exercise the actual client across delayed socket acknowledgements, without
// duplicating a second timer authority in a TypeScript fake controller.
afterEach(() => { cleanup(); rs.unstubAllGlobals(); });

it("preserves rapid command order before a new socket payload arrives", async () => {
  const urls: string[] = [];
  rs.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    return { ok: true, json: async () => ({ result: "OK" }) };
  });
  const command = createCommand("");
  const timer: DashSocketPayload["timer"] = {
    ...FIXTURE_DASH.timer, timerId: "66cc24ec-0bb4-42b7-af5d-c5e21124ec90", state: "running", current: true, remainingS: 600,
  };
  const view = render(<TimerBar timer={timer} command={command} />);
  fireEvent.click(screen.getByRole("button", { name: "Stop timer" }));
  fireEvent.click(screen.getByRole("button", { name: "Pause timer" }));
  await waitFor(() => expect(urls).toEqual(["/api/set/timer/stop", "/api/set/timer/pause"]));
  view.rerender(<TimerBar timer={FIXTURE_DASH.timer} command={command} />);
  expect(screen.getByRole("button", { name: "Start timer" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Resume timer" })).toBeNull();
});

it("allows retry after a failed command without an optimistic state transition", async () => {
  const urls: string[] = [];
  rs.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    return { ok: urls.length > 1, status: 500, json: async () => ({ result: "OK" }) };
  });
  const timer: DashSocketPayload["timer"] = {
    ...FIXTURE_DASH.timer, state: "paused", remainingS: 600,
  };
  render(<TimerBar timer={timer} command={createCommand("")} />);
  fireEvent.click(screen.getByRole("button", { name: "Resume timer" }));
  await waitFor(() => expect(urls).toHaveLength(1));
  expect(screen.getByRole("button", { name: "Resume timer" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Resume timer" }));
  await waitFor(() => expect(urls).toEqual(["/api/set/timer/start/600", "/api/set/timer/start/600"]));
});
