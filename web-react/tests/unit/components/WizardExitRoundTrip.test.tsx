import type { CommandClient, CommandResult } from "@pifire/core/command";
import type { WizardState } from "@pifire/core/contracts/wizard";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
// The module-singleton client, NOT testQueryClient(): WizardShell's exit
// handler invalidates settingsRoot on that exact singleton (the same one
// useSaveSettings.ts writes through -- see useSaveSettings.test.tsx for the
// precedent), so a render wrapped in a throwaway client would invalidate a
// cache DashboardRoute never reads from, silently hiding the bug this file
// exists to pin.
import { queryClient } from "../../../src/helpers/query/queryClient";
import type { ShellContext } from "../../../src/helpers/shellContext";

// One fake PiFire settings store, shared by BOTH mocked API clients, because
// the defect this file pins lives in the seam BETWEEN them: WizardShell's exit
// calls /api/wizard/cancel, and DashboardRoute reads globals.first_time_setup
// off the shared settings cache (useSettings.ts) and navigates back to
// /wizard while it is still true. Leaving the wizard therefore only works if
// the cancel both clears the flag on the server AND invalidates that cache
// entry -- otherwise the user is bounced straight back into the wizard they
// just left, forever.
//
// `cancelWizard` clearing this object mirrors what the real route does, which
// tests/web/test_api_wizard.py::test_cancel_clears_first_time_setup pins on
// the server end of the same path.
const backend = { first_time_setup: true };

const cancelWizardMock = rs.fn();
const getSettingsMock = rs.fn();
const saveDraftMock = rs.fn();

rs.mock("../../../src/helpers/wizard/wizardApi", () => ({
  getWizardState: rs.fn(),
  saveDraft: (...args: unknown[]) => saveDraftMock(...args),
  cancelWizard: (...args: unknown[]) => cancelWizardMock(...args),
  finishWizard: rs.fn(),
  getInstallStatus: rs.fn(async () => ({ percent: 0, status: "", output: "" })),
  scan: rs.fn(async () => ({ groups: [], error: null })),
  fetchModuleValues: rs.fn(async () => ({ settings: {}, config: {} })),
}));

rs.mock("../../../src/helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
}));

const { WizardShell, HydrateFallback } = await import("../../../src/components/wizard/WizardShell");
const { DashboardRoute } = await import("../../../src/components/DashboardRoute");
const { AppPrefsProvider } = await import("../../../src/components/AppPrefs");

afterEach(() => {
  cleanup();
  // Leaking a cached settings entry into the next test would let that test's
  // DashboardRoute observer read this file's data instead of its own mock.
  queryClient.clear();
});

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
    recipeNextStep: rs.fn(ok),
    recipeUnpause: rs.fn(ok),
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
    // The shell forwards its whole useLiveState() result on Outlet context,
    // so a stub must carry every field the real hook returns. null is what a
    // client that has not yet received socket_pellet_data sees.
    pellets: null,
  };
  const router = createMemoryRouter(
    [
      {
        element: <Outlet context={context} />,
        children: [{ path: "/", element: <DashboardRoute /> }],
      },
      { path: "/wizard", element: <WizardShell />, loader: () => wizardState(), HydrateFallback },
    ],
    { initialEntries: ["/wizard"] },
  );
  return render(
    <QueryClientProvider client={queryClient}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
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

    // The trap: DashboardRoute reads the SHARED settings cache rather than
    // fetching its own copy, so `getSettingsMock` having been called at all
    // proves nothing -- AppPrefsProvider already called it once at boot,
    // before Exit Setup was even clicked. The call that actually matters is
    // the SECOND one: the refetch WizardShell's exit handler triggers by
    // invalidating the cache. Waiting for exactly 2 calls (boot + that
    // invalidation-triggered refetch) is what proves the fresh, cleared value
    // actually landed before concluding we stayed -- asserting on "called at
    // all" would pass even against a version that bounces a beat later.
    await waitFor(() => expect(getSettingsMock).toHaveBeenCalledTimes(2));
    expect(onWizard()).toBe(false);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it('keeps Exit Setup disabled ("Leaving…") until the post-cancel settings refetch settles', async () => {
    // Pins WizardShell.tsx's handleExit ordering: `exiting` must stay true for
    // the WHOLE awaited invalidateQueries call, not just through cancelWizard.
    // Clearing it early re-enables a button that looks idle while the app is
    // still sitting on the wizard -- inviting a double-click that re-runs
    // saveDraft + cancelWizard, with no bounded failure path underneath
    // (getSettings has neither a timeout nor an AbortSignal, and retry is off).
    let releaseRefetch: () => void = () => {};
    let callCount = 0;
    getSettingsMock.mockReset().mockImplementation(() => {
      callCount += 1;
      if (callCount === 1) {
        // The boot-time read AppPrefsProvider issues -- must resolve normally
        // so the wizard actually renders.
        return Promise.resolve({ globals: { first_time_setup: backend.first_time_setup } });
      }
      // The invalidation-triggered refetch: held open until the test releases
      // it, so the in-flight window is observable rather than raced.
      return new Promise((resolve) => {
        releaseRefetch = () => resolve({ globals: { first_time_setup: backend.first_time_setup } });
      });
    });

    renderApp();
    await screen.findByRole("heading", { name: "Welcome" });

    fireEvent.click(screen.getByRole("button", { name: "Exit Setup" }));

    await waitFor(() => expect(getSettingsMock).toHaveBeenCalledTimes(2));
    // Still mid-refetch, still on the wizard: the button must not have gone
    // back to an idle, clickable "Exit Setup".
    expect(onWizard()).toBe(true);
    expect(screen.getByRole("button", { name: "Leaving…" })).toBeDisabled();

    releaseRefetch();

    expect(await screen.findByText("LIVE")).toBeInTheDocument();
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
