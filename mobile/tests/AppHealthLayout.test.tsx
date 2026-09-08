import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { monotonicNowMs } from "@pifire/core/liveConnection";
import { act, render, waitFor } from "@testing-library/react-native";

import type { LiveResult } from "../src/useLive";
import { wireHealth } from "./healthFixture";

const mockRequestPermissionsAsync = jest.fn();
const mockScheduleNotificationAsync = jest.fn();
const mockPrefsState = {
  current: { host: null, accent: "ember", alerts: true },
};
const mockLiveState = { current: {} as LiveResult };

jest.mock("expo-router", () => {
  const Stack = () => null;
  Stack.Screen = () => null;
  Stack.Protected = () => null;
  return {
    Stack,
    usePathname: () => "/",
    useRouter: () => ({ replace: jest.fn() }),
  };
});

jest.mock("expo-notifications", () => ({
  AndroidImportance: { HIGH: 4 },
  requestPermissionsAsync: (...args: unknown[]) => mockRequestPermissionsAsync(...args),
  scheduleNotificationAsync: (...args: unknown[]) => mockScheduleNotificationAsync(...args),
  setNotificationChannelAsync: jest.fn(),
  setNotificationHandler: jest.fn(),
}));

jest.mock("../src/host", () => ({ loadHosts: async () => ["http://pifire.local:5000"] }));
jest.mock("../src/prefs", () => ({
  defaultPrefs: { host: null, accent: "ember", alerts: true },
  loadPrefs: async () => mockPrefsState.current,
  savePrefs: jest.fn(),
}));
jest.mock("../src/useLive", () => ({
  ...jest.requireActual("../src/useLive"),
  useLive: () => mockLiveState.current,
}));

import RootLayout from "../app/_layout";
const command = {} as LiveResult["command"];

function liveResult(
  thermocoupleHealth: NonNullable<DashSocketPayload["thermocoupleHealth"]>,
  phase: LiveResult["phase"] = "live",
  lastPayloadMonotonicMs: number | null = monotonicNowMs(),
): LiveResult {
  return {
    live: { ...FIXTURE_DASH, thermocoupleHealth },
    phase,
    controlAlive: true,
    pellets: null,
    command,
    lastPayloadMonotonicMs,
    host: "http://pifire.local:5000",
  };
}

const CONFIRMED_PRIMARY_CURRENT = wireHealth({
  report: { state: "confirmed", faults: ["malfunction"], temperatureValid: true },
  outcome: "notify_only",
});

beforeEach(() => {
  mockRequestPermissionsAsync.mockClear();
  mockScheduleNotificationAsync.mockClear();
  mockPrefsState.current = { host: null, accent: "ember", alerts: true };
  mockLiveState.current = liveResult([CONFIRMED_PRIMARY_CURRENT]);
});

it("keeps transport status separate from a persistent primary health banner", async () => {
  const screen = await render(<RootLayout />);

  await waitFor(() => expect(screen.getByText("Live")).toBeTruthy());
  expect(screen.getByText("FAULT")).toBeTruthy();
  expect(screen.getByText("Fault detected — Observe mode did not stop heating.")).toBeTruthy();
  expect(screen.getByRole("alert").props.accessibilityLabel).toContain("Grill");
  expect(screen.queryByRole("button")).toBeNull();
  expect(mockScheduleNotificationAsync).not.toHaveBeenCalled();
  expect(screen.queryByText("Last reported")).toBeNull();
});

it("retains a current confirmed banner after disconnect, qualified as Last reported", async () => {
  mockLiveState.current = liveResult([CONFIRMED_PRIMARY_CURRENT], "unreachable");
  const screen = await render(<RootLayout />);

  await waitFor(() => expect(screen.getByText("Unreachable")).toBeTruthy());
  expect(screen.getByText("FAULT")).toBeTruthy();
  expect(screen.getByText("Last reported")).toBeTruthy();
});

it("ages a current confirmed banner to Last reported after a silent live socket stall", async () => {
  mockLiveState.current = liveResult([CONFIRMED_PRIMARY_CURRENT], "live", monotonicNowMs() - 31_000);
  const screen = await render(<RootLayout />);

  await waitFor(() => expect(screen.getByText("Stale · 31s ago")).toBeTruthy());
  expect(screen.getByText("FAULT")).toBeTruthy();
  expect(screen.getByText("Last reported")).toBeTruthy();
});

it("removes the primary banner on a recovered payload without changing transport status", async () => {
  const screen = await render(<RootLayout />);
  await waitFor(() => expect(screen.getByText("FAULT")).toBeTruthy());

  mockLiveState.current = liveResult([wireHealth()]);
  await screen.rerender(<RootLayout />);

  await waitFor(() => expect(screen.queryByText("FAULT")).toBeNull());
  expect(screen.getByText("Live")).toBeTruthy();
  expect(mockScheduleNotificationAsync).not.toHaveBeenCalled();
});

it("requests no permission and schedules no confirmed alert when local alerts are disabled", async () => {
  mockPrefsState.current = { host: null, accent: "ember", alerts: false };
  mockLiveState.current = liveResult([wireHealth()]);
  const screen = await render(<RootLayout />);
  await waitFor(() => expect(screen.getByText("Live")).toBeTruthy());

  mockLiveState.current = liveResult([CONFIRMED_PRIMARY_CURRENT]);
  await screen.rerender(<RootLayout />);
  await waitFor(() => expect(screen.getByText("FAULT")).toBeTruthy());

  expect(mockRequestPermissionsAsync).not.toHaveBeenCalled();
  expect(mockScheduleNotificationAsync).not.toHaveBeenCalled();
});

it("does not alert for fixture data or the first real payload", async () => {
  mockLiveState.current = liveResult([], "live", null);
  const screen = await render(<RootLayout />);
  await waitFor(() => expect(screen.getByText("No data yet")).toBeTruthy());
  expect(mockScheduleNotificationAsync).not.toHaveBeenCalled();
  mockLiveState.current = liveResult([CONFIRMED_PRIMARY_CURRENT]);
  await screen.rerender(<RootLayout />);
  await waitFor(() => expect(screen.getByText("FAULT")).toBeTruthy());
  expect(mockScheduleNotificationAsync).not.toHaveBeenCalled();
});

it.each([-3_600_000, 3_600_000])("ages the header and health across a wall step of %s ms", async (step) => {
  jest.useFakeTimers();
  try {
    const wall = Date.now();
    mockLiveState.current = liveResult([CONFIRMED_PRIMARY_CURRENT]);
    const screen = await render(<RootLayout />);
    await waitFor(() => expect(screen.getByText("Live")).toBeTruthy());
    jest.setSystemTime(wall + step);
    await act(async () => jest.advanceTimersByTime(31_000));
    expect(screen.getByText("Stale · 31s ago")).toBeTruthy();
    expect(screen.getByText("Last reported")).toBeTruthy();
    expect(mockScheduleNotificationAsync).not.toHaveBeenCalled();
  } finally {
    jest.useRealTimers();
  }
});

it("shows unknown producer age while keeping its retained fault visible", async () => {
  mockLiveState.current = liveResult([wireHealth({
    report: { state: "confirmed", faults: ["open"], temperatureValid: false },
    outcome: "unavailable",
    freshness: { current: false, lastReportedAgeS: null, reason: "unknown-clock" },
  })]);
  const screen = await render(<RootLayout />);
  await waitFor(() => expect(screen.getByText("Age unknown")).toBeTruthy());
  expect(screen.getByText("Last reported")).toBeTruthy();
  expect(screen.getByRole("alert").props.accessibilityLabel).toContain("Age unknown");
});
