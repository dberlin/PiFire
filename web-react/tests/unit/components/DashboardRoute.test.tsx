import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import type { CommandClient, CommandResult } from "../../../src/helpers/command";
import { FIXTURE_DASH } from "../../../src/helpers/fixture";
import type { ShellContext } from "../../../src/helpers/shellContext";
import { testQueryClient } from "../test-utils";

const getSettingsMock = rs.fn();

rs.mock("../../../src/helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
}));

const { DashboardRoute } = await import("../../../src/components/DashboardRoute");
const { AppPrefsProvider } = await import("../../../src/components/AppPrefs");

afterEach(cleanup);

beforeEach(() => {
  getSettingsMock.mockReset();
  getSettingsMock.mockResolvedValue({ globals: { first_time_setup: false } });
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
    timerStartWithOptions: rs.fn(ok),
    timerPause: rs.fn(ok),
    timerStop: rs.fn(ok),
    timerShutdown: rs.fn(ok),
    timerKeepWarm: rs.fn(ok),
    system: rs.fn(ok),
    setUnits: rs.fn(ok),
    manualOutput: rs.fn(ok),
    manualPwm: rs.fn(ok),
    recipeNextStep: rs.fn(ok),
    recipeUnpause: rs.fn(ok),
  };
}

// DashboardRoute reads its live state off the shell's Outlet context now
// (AppShell owns the app's single useLiveState subscription), so the harness
// supplies that context through a real memory router instead of mocking the
// hook. /wizard is mounted as a sibling stand-in so the first_time_setup
// redirect can be asserted by where the router actually ends up, rather than
// by a spy on useNavigate.
function renderDashboardRoute(over: Partial<ShellContext> = {}) {
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
    ...over,
  };
  const router = createMemoryRouter(
    [
      {
        element: <Outlet context={context} />,
        children: [{ path: "/", element: <DashboardRoute /> }],
      },
      { path: "/wizard", element: <div>wizard stand-in</div> },
    ],
    { initialEntries: ["/"] },
  );
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
}

const wizardShowing = () => screen.queryByText("wizard stand-in") !== null;

describe("DashboardRoute", () => {
  it("renders ConnectionStatus when there is no live/demo data yet", () => {
    renderDashboardRoute({ phase: "connecting", controlAlive: false });

    expect(screen.getByText("Connecting to PiFire…")).toBeInTheDocument();
    expect(screen.getByText("http://pifire.local:5000")).toBeInTheDocument();
  });

  it("renders the Dashboard with the current mode badge once phase is live", () => {
    renderDashboardRoute({ live: { ...FIXTURE_DASH, currentMode: "Hold" } });

    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("navigates to /wizard when first_time_setup is true", async () => {
    getSettingsMock.mockResolvedValue({ globals: { first_time_setup: true } });

    renderDashboardRoute();

    await waitFor(() => expect(wizardShowing()).toBe(true));
  });

  it("does not navigate when first_time_setup is false", async () => {
    getSettingsMock.mockResolvedValue({ globals: { first_time_setup: false } });

    renderDashboardRoute();

    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(wizardShowing()).toBe(false);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("still renders the dashboard when the check fails (advisory only)", async () => {
    getSettingsMock.mockRejectedValue(new Error("offline"));

    renderDashboardRoute();

    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(wizardShowing()).toBe(false);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });
});
