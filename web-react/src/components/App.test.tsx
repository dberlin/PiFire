import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { FIXTURE_DASH } from "../helpers/fixture";

// AppShell -- the layout route wrapping /, /history and /settings -- is the one
// caller of useLiveState, so every route inside the shell needs this mocked
// even when the test is about a settings tab. A beforeEach below supplies a
// usable default; tests that care about the payload override it.
const useLiveStateMock = rs.fn();
rs.mock("../helpers/useLiveState", () => ({
  useLiveState: () => useLiveStateMock(),
}));

// Default-resolved so every test has a valid promise: DashboardRoute now calls
// getSettings() unconditionally on mount for the first_time_setup gate, and a
// bare rs.fn() would return undefined and blow up its .then(). Individual tests
// still override this with their own mockResolvedValue.
const getSettingsMock = rs.fn().mockResolvedValue({});
const getModeMock = rs.fn();
const getControllerMetadataMock = rs.fn().mockResolvedValue(null);
rs.mock("../helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
  getMode: (...args: unknown[]) => getModeMock(...args),
  getControllerMetadata: (...args: unknown[]) => getControllerMetadataMock(...args),
  buildSettingsUrl: (baseUrl: string, path: string) => `${baseUrl}/api/${path}`,
}));

const getWizardStateMock = rs.fn();
rs.mock("../helpers/wizard/wizardApi", () => ({
  getWizardState: (...args: unknown[]) => getWizardStateMock(...args),
  saveDraft: rs.fn().mockResolvedValue(true),
  finishWizard: rs.fn(),
  getInstallStatus: rs.fn(),
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
}));

const { AppPrefsProvider } = await import("./AppPrefs");
const { default: App, routes } = await import("./App");

afterEach(cleanup);

const command = {
  setMode: rs.fn(),
  hold: rs.fn(),
  setSmokePlus: rs.fn(),
  setPMode: rs.fn(),
  prime: rs.fn(),
  timerStart: rs.fn(),
  timerStartWithOptions: rs.fn(),
  timerPause: rs.fn(),
  timerStop: rs.fn(),
  timerShutdown: rs.fn(),
  timerKeepWarm: rs.fn(),
  system: rs.fn(),
  setUnits: rs.fn(),
};

beforeEach(() => {
  useLiveStateMock.mockReset();
  useLiveStateMock.mockReturnValue({
    live: FIXTURE_DASH,
    phase: "live",
    controlAlive: true,
    targetUrl: "http://pifire.local:5000",
    command,
  });
});

function renderApp(initialEntry: string) {
  const router = createMemoryRouter(routes, { initialEntries: [initialEntry] });
  return render(
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>,
  );
}

describe("App routing", () => {
  it("renders the dashboard at /", () => {
    useLiveStateMock.mockReturnValue({
      live: { ...FIXTURE_DASH, currentMode: "Hold" },
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command,
    });

    renderApp("/");

    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("renders the settings shell and GeneralTab at /settings/general", async () => {
    getSettingsMock.mockResolvedValue({
      globals: { grill_name: "Backyard Smoker", page_theme: "dark" },
    });
    getModeMock.mockResolvedValue("Stop");

    renderApp("/settings/general");

    expect(await screen.findByRole("link", { name: "General" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Backyard Smoker")).toBeInTheDocument();
    expect(getSettingsMock).toHaveBeenCalled();
    expect(getModeMock).toHaveBeenCalled();
  });

  it("renders the wizard shell at /wizard", async () => {
    getWizardStateMock.mockResolvedValue({
      modules_metadata: { grillplatform: {}, probes: {}, distance: {}, display: {} },
      selections: { grillplatform: null, probes: null, distance: null, display: null },
      settings_dep_values: { grillplatform: {}, probes: {}, distance: {}, display: {} },
      display_config: {},
      board_probe_maps: {},
      control_mode: "Stop",
      first_time_setup: false,
      has_draft: false,
    });

    renderApp("/wizard");

    expect(await screen.findByRole("heading", { name: "Welcome" })).toBeInTheDocument();
    expect(screen.getByText("Setup Wizard")).toBeInTheDocument();
    expect(getWizardStateMock).toHaveBeenCalled();
  });

  it("also exposes the /wizard route object directly on the exported routes array", () => {
    const wizardRoute = routes.find((r) => r.path === "/wizard");
    expect(wizardRoute).toBeDefined();
    expect(wizardRoute?.loader).toBeDefined();
  });

  it("wraps the dashboard in the app shell so the other pages are reachable", () => {
    renderApp("/");

    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "History" })).toHaveAttribute("href", "/history");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("wraps the history page in the app shell too", () => {
    renderApp("/history");

    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "History" })).toBeInTheDocument();
  });

  it("renders the settings page inside the app shell", async () => {
    getSettingsMock.mockResolvedValue({
      globals: { grill_name: "Backyard Smoker", page_theme: "dark" },
    });
    getModeMock.mockResolvedValue("Stop");

    renderApp("/settings/general");

    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("renders the wizard WITHOUT the shell, so a fresh install cannot wander out of it", async () => {
    getWizardStateMock.mockResolvedValue({
      modules_metadata: { grillplatform: {}, probes: {}, distance: {}, display: {} },
      selections: { grillplatform: null, probes: null, distance: null, display: null },
      settings_dep_values: { grillplatform: {}, probes: {}, distance: {}, display: {} },
      display_config: {},
      board_probe_maps: {},
      control_mode: "Stop",
      first_time_setup: false,
      has_draft: false,
    });

    renderApp("/wizard");

    expect(await screen.findByRole("heading", { name: "Welcome" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
    // No shell means no second live-state subscriber either.
    expect(useLiveStateMock).not.toHaveBeenCalled();
  });

  it("the default export mounts its own AppPrefsProvider + browser router and renders the dashboard at /", () => {
    useLiveStateMock.mockReturnValue({
      live: { ...FIXTURE_DASH, currentMode: "Monitor" },
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command,
    });

    render(<App />);

    expect(screen.getByText("MONITOR")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });
});
