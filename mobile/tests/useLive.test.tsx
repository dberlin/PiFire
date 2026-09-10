import { createCommand } from "@pifire/core/command";
import { deriveView } from "@pifire/core/dashboard/deriveView";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { createLiveConnection, monotonicNowMs } from "@pifire/core/liveConnection";
import type * as ConnectionModule from "@pifire/core/liveConnection";
import { act, renderHook } from "@testing-library/react-native";
import { AppState, type AppStateStatus } from "react-native";

import { qualifyRetainedHealth, receiptFreshness, useLive, type LiveResult } from "../src/useLive";
import { wireHealth } from "./healthFixture";

jest.mock("@pifire/core/liveConnection", () => ({
  ...jest.requireActual<typeof ConnectionModule>("@pifire/core/liveConnection"),
  createLiveConnection: jest.fn(() => ({ reconnect: jest.fn(), close: jest.fn() })),
  monotonicNowMs: jest.fn(() => 100_000),
}));

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(monotonicNowMs).mockReturnValue(100_000);
});
afterEach(() => jest.restoreAllMocks());

it("invalidates background receipts and requires a new real payload on resume", async () => {
  let handler: ((state: AppStateStatus) => void) | undefined;
  jest.spyOn(AppState, "addEventListener").mockImplementation((_type, h) => {
    handler = h;
    return { remove: jest.fn() };
  });
  const { result } = await renderHook(() => useLive("http://pifire.local:5000"));
  const call = jest.mocked(createLiveConnection).mock.calls[0];
  if (!call) throw new Error("connection was not opened");
  const handlers = call[1];
  const connection = jest.mocked(createLiveConnection).mock.results[0]?.value;
  expect(result.current.lastPayloadMonotonicMs).toBeNull();
  await act(async () => handlers.onPhase("live"));
  expect(receiptFreshness(result.current, 100_000).retained).toBe(true);

  await act(async () => handlers.onDash(FIXTURE_DASH));
  expect(receiptFreshness(result.current, 100_000).retained).toBe(false);
  await act(async () => handler?.("background"));
  expect(result.current.lastPayloadMonotonicMs).toBeNull();
  expect(receiptFreshness(result.current, 100_000).retained).toBe(true);
  expect(connection.reconnect).not.toHaveBeenCalled();
  const stopped = {
    ...FIXTURE_DASH,
    currentMode: "Stop",
    errors: ["The control process did not respond to a request and may be stopped."],
  };
  await act(async () => handlers.onDash(stopped));
  expect(result.current.lastPayloadMonotonicMs).toBeNull();

  await act(async () => handler?.("active"));
  expect(connection.reconnect).toHaveBeenCalledTimes(1);
  await act(async () => handlers.onPhase("live"));
  expect(receiptFreshness(result.current, 100_000).retained).toBe(true);
  expect(result.current.live.errors).toEqual(stopped.errors);
  expect(qualifyRetainedHealth(result.current, 100_000).controlAlive).toBe(false);
  await act(async () => handlers.onDash(FIXTURE_DASH));
  expect(result.current.lastPayloadMonotonicMs).toBe(monotonicNowMs());
  expect(receiptFreshness(result.current, 100_000).retained).toBe(false);
});

function liveResult(health = wireHealth()): LiveResult {
  return {
    live: { ...FIXTURE_DASH, thermocoupleHealth: [health] },
    phase: "live",
    controlAlive: true,
    pellets: null,
    command: createCommand("http://pifire.local:5000"),
    lastPayloadMonotonicMs: 100_000,
    host: "http://pifire.local:5000",
  };
}

it.each([-3_600_000, 3_600_000])("receipt freshness ignores a phone wall step of %s ms", (step) => {
  const result = liveResult();
  jest.spyOn(Date, "now").mockReturnValue(1_800_000_000_000 + step);
  expect(receiptFreshness(result, 130_000).retained).toBe(false);
  expect(receiptFreshness(result, 130_001).retained).toBe(true);
  expect(
    qualifyRetainedHealth(result, 130_001).live.thermocoupleHealth?.[0]?.freshness.current,
  ).toBe(false);
});

it("rejects a backwards local receipt clock instead of clamping it to fresh", () => {
  const result = liveResult();
  expect(receiptFreshness(result, 99_999).retained).toBe(true);
  expect(
    qualifyRetainedHealth(result, 99_999).live.thermocoupleHealth?.[0]?.freshness.current,
  ).toBe(false);
});

it("ages producer reports from receipt without promoting stale or inventing unknown age", () => {
  const stale = liveResult(
    wireHealth({
      freshness: { current: false, lastReportedAgeS: 20, reason: "stale" },
    }),
  );
  const unknown = liveResult(
    wireHealth({
      freshness: { current: false, lastReportedAgeS: null, reason: "unknown-clock" },
    }),
  );
  expect(qualifyRetainedHealth(stale, 105_000).live.thermocoupleHealth?.[0]?.freshness).toEqual({
    current: false,
    lastReportedAgeS: 25,
    reason: "stale",
  });
  expect(qualifyRetainedHealth(unknown, 140_000).live.thermocoupleHealth?.[0]?.freshness).toEqual({
    current: false,
    lastReportedAgeS: null,
    reason: "unknown-clock",
  });
  expect(stale.live.thermocoupleHealth?.[0]?.freshness.lastReportedAgeS).toBe(20);
});

it("qualifies numeric readings and control liveness together with retained health", () => {
  const result = liveResult();
  result.live = {
    ...result.live,
    primaryProbe: {
      ...result.live.primaryProbe,
      temp: 226,
      status: { lastTemp: 226, lastReadingAge: 2 },
    },
  };
  const retained = qualifyRetainedHealth(result, 131_000);
  expect(retained.controlAlive).toBe(false);
  expect(retained.live.errors).toEqual([]);
  expect(retained.live.thermocoupleHealth?.[0]?.freshness.current).toBe(false);
  expect(deriveView(retained.live).tempInt).toBe(226);
  expect(deriveView(retained.live).stale).toBe("last data 33s ago");
  const fresh = qualifyRetainedHealth({ ...result, lastPayloadMonotonicMs: 140_000 }, 141_000);
  expect(fresh.controlAlive).toBe(true);
  expect(deriveView(fresh.live).stale).toBeNull();
});

it("keeps a reset receipt invalid after the clock catches up until another payload", async () => {
  jest.useFakeTimers();
  try {
    const { result } = await renderHook(() => useLive("http://pifire.local:5000"));
    const call = jest.mocked(createLiveConnection).mock.calls[0];
    if (!call) throw new Error("connection was not opened");
    const handlers = call[1];
    await act(async () => {
      handlers.onPhase("live");
      handlers.onDash(FIXTURE_DASH);
    });
    jest.mocked(monotonicNowMs).mockReturnValue(50_000);
    await act(async () => jest.advanceTimersByTime(1000));
    expect(result.current.lastPayloadMonotonicMs).toBeNull();
    jest.mocked(monotonicNowMs).mockReturnValue(110_000);
    await act(async () => jest.advanceTimersByTime(1000));
    expect(receiptFreshness(result.current, 110_000).retained).toBe(true);
    await act(async () => handlers.onDash(FIXTURE_DASH));
    expect(receiptFreshness(result.current, 110_000).retained).toBe(false);
  } finally {
    jest.useRealTimers();
  }
});

it("projects timers and durations through the shared receipt, including when health is absent", () => {
  const result = liveResult();
  result.live = {
    ...FIXTURE_DASH,
    timer: { ...FIXTURE_DASH.timer, state: "running", remainingS: 600, current: true },
    durations: {
      ...FIXTURE_DASH.durations,
      cookElapsedS: 3723,
      modeRemainingS: 180,
      current: true,
      running: true,
    },
  };
  const fresh = qualifyRetainedHealth(result, 105_000).live;
  expect(fresh.timer.remainingS).toBe(595);
  expect(fresh.durations.cookElapsedS).toBe(3728);
  expect(fresh.durations.modeRemainingS).toBe(175);
  const stale = qualifyRetainedHealth(result, 131_000).live;
  expect(stale.timer.current).toBe(false);
  expect(stale.timer.remainingS).toBe(600);
  expect(stale.durations.cookElapsedS).toBe(3723);
  expect(
    qualifyRetainedHealth({ ...result, lastPayloadMonotonicMs: null }, 105_000).live.durations
      .current,
  ).toBe(false);
});
