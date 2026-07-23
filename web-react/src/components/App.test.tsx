import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { FIXTURE_DASH } from "../helpers/fixture";

const useDashDataMock = rs.fn();
rs.mock("../helpers/useDashData", () => ({
  useDashData: () => useDashDataMock(),
}));

const getSettingsMock = rs.fn();
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
  timerPause: rs.fn(),
  timerStop: rs.fn(),
  system: rs.fn(),
  setUnits: rs.fn(),
};

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
    useDashDataMock.mockReturnValue({
      dash: { ...FIXTURE_DASH, currentMode: "Hold" },
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

  it("the default export mounts its own AppPrefsProvider + browser router and renders the dashboard at /", () => {
    useDashDataMock.mockReturnValue({
      dash: { ...FIXTURE_DASH, currentMode: "Monitor" },
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
