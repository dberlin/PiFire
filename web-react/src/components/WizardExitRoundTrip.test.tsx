import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import type { CommandClient, CommandResult } from "../helpers/command";
import { FIXTURE_DASH } from "../helpers/fixture";
import type { ShellContext } from "../helpers/shellContext";
import type { WizardState } from "../helpers/wizard/wizardTypes";

// One fake PiFire settings store, shared by BOTH mocked API clients, because
// the defect this file pins lives in the seam BETWEEN them: WizardShell's exit
// calls /api/wizard/cancel, and DashboardRoute independently re-reads
// globals.first_time_setup after mount and navigates back to /wizard while it
// is still set (DashboardRoute.tsx:26-38). Leaving the wizard therefore only
// works if the cancel actually clears the flag -- otherwise the user is
// bounced straight back into the wizard they just left, forever.
//
// `cancelWizard` clearing this object mirrors what the real route does, which
// tests/web/test_api_wizard.py::test_cancel_clears_first_time_setup pins on
// the server end of the same path.
const backend = { first_time_setup: true };

const cancelWizardMock = rs.fn();
const getSettingsMock = rs.fn();
const saveDraftMock = rs.fn();

rs.mock("../helpers/wizard/wizardApi", () => ({
  getWizardState: rs.fn(),
  saveDraft: (...args: unknown[]) => saveDraftMock(...args),
  cancelWizard: (...args: unknown[]) => cancelWizardMock(...args),
  finishWizard: rs.fn(),
  getInstallStatus: rs.fn(async () => ({ percent: 0, status: "", output: "" })),
  scan: rs.fn(async () => ({ groups: [], error: null })),
  fetchModuleValues: rs.fn(async () => ({ settings: {}, config: {} })),
}));

rs.mock("../helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
}));

const { WizardShell } = await import("./wizard/WizardShell");
const { DashboardRoute } = await import("./DashboardRoute");
const { AppPrefsProvider } = await import("./AppPrefs");

afterEach(cleanup);

beforeEach(() => {
  backend.first_time_setup = true;
  saveDraftMock.mockReset().mockResolvedValue(true);
  getSettingsMock.mockReset().mockImplementation(async () => ({
    globals: { first_time_setup: backend.first_time_setup },
  }));
  cancelWizardMock.mockReset().mockImplementation(async () => {
    backend.first_time_setup = false;
    return true;
  });
});

const OK: CommandResult = { ok: true, message: "" };

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
    timerStartWithOptions: rs.fn(ok),
    system: rs.fn(ok),
    setUnits: rs.fn(ok),
    manualOutput: rs.fn(ok),
    manualPwm: rs.fn(ok),
  };
}

function wizardState(): WizardState {
  return {
    modules_metadata: { grillplatform: {}, probes: {}, distance: {}, display: {} },
    selections: { grillplatform: null, probes: null, distance: null, display: null },
    settings_dep_values: { grillplatform: {}, probes: {}, distance: {}, display: {} },
    display_config: {},
    probe_map: { probe_devices: [], probe_info: [] },
    probe_profiles: [],
    probes_units: "F",
    board_probe_maps: {},
    control_mode: "Stop",
    first_time_setup: true,
    has_draft: false,
  };
}

// Both real routes in one router: the wizard the user is escaping from, and
// the dashboard it escapes to, with the dashboard's own first_time_setup gate
// live. A stand-in for either end would hide exactly the bug under test.
function renderApp() {
  const context: ShellContext = {
    live: FIXTURE_DASH,
    phase: "live",
    controlAlive: true,
    targetUrl: "http://pifire.local:5000",
    command: stubCommand(),
  };
  const router = createMemoryRouter(
    [
      {
        element: <Outlet context={context} />,
        children: [{ path: "/", element: <DashboardRoute /> }],
      },
      { path: "/wizard", element: <WizardShell />, loader: () => wizardState() },
    ],
    { initialEntries: ["/wizard"] },
  );
  return render(
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>,
  );
}

const onWizard = () => screen.queryByText("Setup Wizard") !== null;

describe("wizard exit round trip", () => {
  it("Exit Setup reaches the dashboard and STAYS there once the flag is cleared", async () => {
    renderApp();
    await screen.findByRole("heading", { name: "Welcome" });

    fireEvent.click(screen.getByRole("button", { name: "Exit Setup" }));

    expect(await screen.findByText("LIVE")).toBeInTheDocument();
    expect(backend.first_time_setup).toBe(false);

    // The trap: DashboardRoute re-checks the flag after mount. Wait for that
    // check to have actually run before concluding we stayed -- asserting
    // straight after the navigation would pass even against a version that
    // bounces a beat later.
    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(onWizard()).toBe(false);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("a cancel that leaves first_time_setup set bounces the user right back", async () => {
    // Not a hypothetical: this is what shipping the exit control without the
    // /api/wizard/cancel route would have produced -- an exit button that
    // looks like it works and returns the user to the wizard a moment later.
    cancelWizardMock.mockResolvedValue(true); // "succeeds" without clearing
    renderApp();
    await screen.findByRole("heading", { name: "Welcome" });

    fireEvent.click(screen.getByRole("button", { name: "Exit Setup" }));

    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    await waitFor(() => expect(onWizard()).toBe(true));
  });
});
