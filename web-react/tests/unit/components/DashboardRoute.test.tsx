import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import type { CommandClient, CommandResult } from "../../../src/helpers/command";
import { FIXTURE_DASH } from "../../../src/helpers/fixture";
import { queryKeys } from "../../../src/helpers/query/keys";
import type { SettingsSchema } from "../../../src/helpers/settings/settingsTypes.gen";
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
  getSettingsMock.mockResolvedValue(OK);
});

const OK: Partial<SettingsSchema> = { globals: { first_time_setup: false } };
const COMMAND_OK: CommandResult = { ok: true, message: "" };

function stubCommand(): CommandClient {
  const ok = async () => COMMAND_OK;
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
  // Returned alongside the render result so a test can inspect the query's
  // settled status directly (client.getQueryState) instead of only knowing
  // the mock was invoked -- "called" and "the promise it returned has
  // resolved/rejected" are different moments, and a test asserting on the
  // gate's post-settle behaviour needs the latter.
  const client = testQueryClient();
  const renderResult = render(
    <QueryClientProvider client={client}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
  return { client, ...renderResult };
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

    const { client } = renderDashboardRoute();

    // "getSettingsMock was called" proves the request was issued, not that it
    // has finished -- the rejection is still a pending microtask at that
    // point, so waiting on it alone can't tell "the read failed" apart from
    // "the read hasn't settled yet". Waiting on the query's own status
    // instead proves the rejection actually landed in the cache before the
    // assertions below run.
    await waitFor(() => expect(client.getQueryState(queryKeys.settings)?.status).toBe("error"));
    expect(wizardShowing()).toBe(false);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("issues no settings request of its own when the cache is already warm", async () => {
    // AppPrefsProvider and /settings' loader read the same entry. The gate must
    // ride that entry, not add a fourth GET to every dashboard paint.
    getSettingsMock.mockResolvedValue(OK);
    renderDashboardRoute();
    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(getSettingsMock).toHaveBeenCalledTimes(1);
  });
});
