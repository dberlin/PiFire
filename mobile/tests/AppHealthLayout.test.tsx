import { render, waitFor } from "@testing-library/react-native";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import type { LiveResult } from "../src/useLive";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { wireHealth } from "./healthFixture";

const mockScheduleNotificationAsync = jest.fn();
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
  requestPermissionsAsync: jest.fn(),
  scheduleNotificationAsync: (...args: unknown[]) => mockScheduleNotificationAsync(...args),
  setNotificationChannelAsync: jest.fn(),
  setNotificationHandler: jest.fn(),
}));

jest.mock("../src/host", () => ({ loadHosts: async () => ["http://pifire.local:5000"] }));
jest.mock("../src/prefs", () => ({
  defaultPrefs: { host: null, accent: "ember", alerts: true },
  loadPrefs: async () => ({ host: null, accent: "ember", alerts: true }),
  savePrefs: jest.fn(),
}));
jest.mock("../src/useLive", () => ({ useLive: () => mockLiveState.current }));

import RootLayout from "../app/_layout";
const command = {} as LiveResult["command"];

function liveResult(
  thermocoupleHealth: NonNullable<DashSocketPayload["thermocoupleHealth"]>,
  phase: LiveResult["phase"] = "live",
): LiveResult {
  return {
    live: { ...FIXTURE_DASH, thermocoupleHealth },
    phase,
    controlAlive: true,
    pellets: null,
    command,
    lastPayloadAt: Date.now(),
    host: "http://pifire.local:5000",
  };
}

const CONFIRMED_PRIMARY_CURRENT = wireHealth({
  report: { state: "confirmed", faults: ["malfunction"], temperatureValid: true },
  outcome: "notify_only",
});
const CONFIRMED_PRIMARY_LAST_REPORTED = wireHealth({
  report: { state: "confirmed", faults: ["malfunction"], temperatureValid: true },
  outcome: "notify_only",
  freshness: { current: false, lastReportedAgeS: 63 },
});

beforeEach(() => {
  mockScheduleNotificationAsync.mockClear();
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
});

it("retains a confirmed banner offline, qualified as Last reported", async () => {
  mockLiveState.current = liveResult([CONFIRMED_PRIMARY_LAST_REPORTED], "unreachable");
  const screen = await render(<RootLayout />);

  await waitFor(() => expect(screen.getByText("Unreachable")).toBeTruthy());
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
