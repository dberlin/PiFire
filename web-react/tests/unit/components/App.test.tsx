import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { RouteObject } from "react-router";
import { createMemoryRouter, RouterProvider } from "react-router";
import { queryClient } from "../../../src/helpers/query/queryClient";
import { SETTINGS_TABS } from "../../../src/helpers/settings/settingsTabs";
import { testQueryClient } from "../test-utils";

// AppShell -- the layout route wrapping /, /history and /settings -- is the one
// caller of useLiveState, so every route inside the shell needs this mocked
// even when the test is about a settings tab. A beforeEach below supplies a
// usable default; tests that care about the payload override it.
const useLiveStateMock = rs.fn();
rs.mock("../../../src/helpers/useLiveState", () => ({
  useLiveState: () => useLiveStateMock(),
}));

// Default-resolved so every test has a valid promise: DashboardRoute now calls
// getSettings() unconditionally on mount for the first_time_setup gate, and a
// bare rs.fn() would return undefined and blow up its .then(). Individual tests
// still override this with their own mockResolvedValue.
const getSettingsMock = rs.fn().mockResolvedValue({});
const getModeMock = rs.fn();
const getControllerMetadataMock = rs.fn().mockResolvedValue(null);
rs.mock("../../../src/helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
  getMode: (...args: unknown[]) => getModeMock(...args),
  getControllerMetadata: (...args: unknown[]) => getControllerMetadataMock(...args),
  buildSettingsUrl: (baseUrl: string, path: string) => `${baseUrl}/api/${path}`,
}));

// /settings/probes is the only settings child with its OWN loader, so the route
// tree cannot be driven without stubbing the catalog fetch. Only
// getProbeModules is replaced: readLiveProbeMap/readLiveProfiles are the pure
// narrowing functions ProbesTab seeds from, and stubbing those would hide the
// very wiring this test exists to check. rstest needs a sync factory, hence the
// import attribute.
import * as realProbeMapApi from "../../../src/helpers/probes/probeMapApi" with {
  rstest: "importActual",
};

const getProbeModulesMock = rs.fn().mockResolvedValue({ modules: {}, requires_install: {} });
rs.mock("../../../src/helpers/probes/probeMapApi", () => ({
  ...realProbeMapApi,
  getProbeModules: (...args: unknown[]) => getProbeModulesMock(...args),
}));

const getWizardStateMock = rs.fn();
rs.mock("../../../src/helpers/wizard/wizardApi", () => ({
  getWizardState: (...args: unknown[]) => getWizardStateMock(...args),
  saveDraft: rs.fn().mockResolvedValue(true),
  finishWizard: rs.fn(),
  getInstallStatus: rs.fn(),
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
}));

// Route modules load only after their API mocks above are registered.
const { probeModulesLoader } = await import("../../../src/helpers/probes/probeMapRoutes");
const { AppPrefsProvider } = await import("../../../src/components/AppPrefs");
const { default: App } = await import("../../../src/components/App");
const { routes } = await import("../../../src/components/appRoutes");

afterEach(() => {
  cleanup();
  // biome-ignore lint/suspicious/noExplicitAny: undo the stub above.
  (globalThis as any).fetch = undefined;
});

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

// BuildWatcher (App.tsx) calls useWebUiBuild() unconditionally, which polls
// /api/webui through the real `queryClient` singleton -- every route mounted
// via the default `<App />` export (not just renderApp's routes-only render)
// makes this call. There is no global fetch stub in test-setup.ts, so without
// this the test hits a real, unmocked network request whose outcome depends
// on whatever is listening on localhost. Resolved so fetchBuildId always gets
// a stable answer and never has cause to reload.
const fetchMock = rs
  .fn()
  .mockResolvedValue({ ok: true, json: () => Promise.resolve({ build: "test-build" }) });

beforeEach(() => {
  queryClient.clear();
  useLiveStateMock.mockReset();
  useLiveStateMock.mockReturnValue({
    live: FIXTURE_DASH,
    phase: "live",
    controlAlive: true,
    targetUrl: "http://pifire.local:5000",
    command,
  });
  fetchMock.mockClear();
  // biome-ignore lint/suspicious/noExplicitAny: stubbing the global fetch.
  (globalThis as any).fetch = fetchMock;
});

function renderApp(initialEntry: string) {
  const router = createMemoryRouter(routes, { initialEntries: [initialEntry] });
  const view = render(
    <QueryClientProvider client={testQueryClient()}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
  return { ...view, router };
}

function settingsChildren(): RouteObject[] {
  const shellChildren = (routes as RouteObject[]).flatMap(({ children }) => children ?? []);
  const settingsRoute = shellChildren.find(({ path }) => path === "/settings");
  if (!settingsRoute?.children) throw new Error("The /settings route has no child routes");
  return settingsRoute.children;
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
      globals: { grill_name: "Backyard Smoker" },
    });
    getModeMock.mockResolvedValue("Stop");

    renderApp("/settings/general");

    expect(await screen.findByRole("link", { name: "General" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Backyard Smoker")).toBeInTheDocument();
    expect(getSettingsMock).toHaveBeenCalled();
    expect(getModeMock).toHaveBeenCalled();
  });

  it("redirects the settings index to the General tab", async () => {
    getSettingsMock.mockResolvedValue({
      globals: { grill_name: "Backyard Smoker" },
      platform: { dc_fan: true },
    });
    getModeMock.mockResolvedValue("Stop");

    const { router } = renderApp("/settings");

    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/settings/general");
  });

  it("defines exactly one ordered child route for every manifest ID and no extras", () => {
    const children = settingsChildren();
    const indexed = children.filter(({ index }) => index === true);
    const addressed = children.filter(
      (route): route is RouteObject & { path: string } => typeof route.path === "string",
    );

    expect(indexed).toHaveLength(1);
    expect(children).toHaveLength(SETTINGS_TABS.length + 1);
    expect(addressed.map(({ path }) => path)).toEqual(SETTINGS_TABS.map(({ id }) => id));
  });

  it("keeps probes as the only settings child with a loader", () => {
    const loaded = settingsChildren().filter(({ loader }) => loader !== undefined);

    expect(loaded.map(({ path }) => path)).toEqual(["probes"]);
    expect(loaded[0]?.loader).toBe(probeModulesLoader);
  });

  it("keeps the hidden PWM route addressable on an AC-fan build", async () => {
    getSettingsMock.mockResolvedValue({
      globals: { grill_name: "Backyard Smoker" },
      platform: { dc_fan: false },
    });
    getModeMock.mockResolvedValue("Stop");

    renderApp("/settings/pwm");

    expect(await screen.findByRole("heading", { name: "PWM Fan" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "PWM Fan" })).toBeNull();
    expect(screen.getByText(/PWM fan control is unavailable on this grill/)).toBeInTheDocument();
  });

  it("renders the Probes settings tab at /settings/probes", async () => {
    getSettingsMock.mockResolvedValue({
      globals: { grill_name: "Backyard Smoker" },
      probe_settings: { probe_profiles: {}, probe_map: { probe_devices: [], probe_info: [] } },
    });
    getModeMock.mockResolvedValue("Stop");

    renderApp("/settings/probes");

    expect(await screen.findByRole("region", { name: "Probe devices" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Probe ports" })).toBeInTheDocument();
    // The tab's own loader ran -- it is the only settings child that has one.
    expect(getProbeModulesMock).toHaveBeenCalled();
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
      globals: { grill_name: "Backyard Smoker" },
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

// React Router renders nothing at all for the pending frame of an initial load
// unless the matched branch declares a HydrateFallback, and says so on the
// console. A loader added to a branch that has none is therefore a blank first
// paint on a slow grill, which is invisible in a test that only waits for the
// resolved view.
describe("route tree", () => {
  it("covers every loader-bearing branch with a HydrateFallback", () => {
    const uncovered: string[] = [];
    const walk = (route: RouteObject, trail: string[], covered: boolean) => {
      const here = [...trail, route.path ?? (route.index ? "(index)" : "(layout)")];
      const coveredHere = covered || route.HydrateFallback != null;
      if (route.loader && !coveredHere) uncovered.push(here.join(" > "));
      for (const child of route.children ?? []) walk(child, here, coveredHere);
    };
    for (const route of routes) walk(route, [], false);

    expect(uncovered).toEqual([]);
  });
});
