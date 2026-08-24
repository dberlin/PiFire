import type { ThermocoupleHealthView } from "@pifire/core/contracts/core";
import { describe, expect, it } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
  useOutletContext,
  useRevalidator,
  useRouteLoaderData,
} from "react-router";
import { AppPrefsProvider, useAppPrefs } from "../../../../src/components/AppPrefs";
import { HydrateFallback } from "../../../../src/components/HydrateFallback";
import { SettingsShell } from "../../../../src/components/settings/SettingsShell";
import { flushObservers, testQueryClient } from "../../test-utils";

// SettingsShell reads its data via `useLoaderData()`, not outlet context, so
// (unlike a plain tab) it needs a real data router with a loader — the
// shared `renderRoute` harness (which only wires up Outlet context) doesn't
// supply that. Build a minimal one here with the /settings route's own
// loader stubbed to return { settings, mode } synchronously, plus a sibling
// "/" route so the back-to-dashboard navigation (SettingsShell.tsx:21) can
// be observed landing somewhere.
function renderShell(dcFan = true) {
  const router = createMemoryRouter(
    [
      {
        path: "/settings",
        element: <SettingsShell />,
        loader: () => ({
          settings: { globals: { units: "F" }, platform: { dc_fan: dcFan } },
          mode: "Stop",
        }),
        HydrateFallback,
        children: [{ index: true, element: <div /> }],
      },
      { path: "/", element: <div data-testid="dashboard-root">Dashboard</div> },
    ],
    { initialEntries: ["/settings"] },
  );
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
}

// Every tab in SettingsShell's nav, in nav order. Literal IDs as well as
// labels make the route identity observable without inspecting implementation
// source. The title deliberately does not hardcode a count: it drifted while
// still claiming "8".
const TAB_LINKS = [
  { id: "general", label: "General" },
  { id: "work-mode", label: "Work Mode" },
  { id: "controller", label: "Controller" },
  { id: "pwm", label: "PWM Fan" },
  { id: "startup", label: "Startup / Shutdown" },
  { id: "safety", label: "Safety" },
  { id: "pellets", label: "Pellet Levels" },
  { id: "history", label: "History" },
  { id: "notifications", label: "Notifications" },
  { id: "units", label: "Units" },
  { id: "platform", label: "Platform" },
  { id: "probes", label: "Probes" },
] as const;

describe("SettingsShell", () => {
  it("renders every nav tab and the back-to-dashboard control", async () => {
    renderShell();
    // Loader data resolves asynchronously even though the loader itself is
    // synchronous — wait for the first tab link before asserting on the rest.
    await screen.findByRole("link", { name: "General" });
    for (const { id, label } of TAB_LINKS) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", `/settings/${id}`);
    }
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(
      TAB_LINKS.map(({ label }) => label),
    );
    expect(screen.getByRole("button", { name: /Dashboard/ })).toBeInTheDocument();
  });

  // Flask gates the PWM nav pill on settings['platform']['dc_fan']
  // (blueprints/settings/templates/settings/index.html:63-65). The /settings/pwm
  // ROUTE stays registered either way so a bookmarked URL still resolves — only
  // the pill goes away.
  it("hides the PWM Fan tab on an AC-fan build, keeping every other tab in order", async () => {
    renderShell(false);
    await screen.findByRole("link", { name: "General" });

    expect(screen.queryByRole("link", { name: "PWM Fan" })).toBeNull();
    const visibleTabs = TAB_LINKS.filter(({ id }) => id !== "pwm");
    for (const { id, label } of visibleTabs) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", `/settings/${id}`);
    }
    const rendered = screen.getAllByRole("link").map((a) => a.textContent);
    expect(rendered).toEqual(visibleTabs.map(({ label }) => label));
  });

  // A string `name` is a FULL accessible-name match, so this cannot start
  // silently matching "Probe Profiles" the day this sub-project's second half
  // adds that tab. (ByRoleOptions has no `exact` member -- that belongs to the
  // ByText queries, and typescript7 rejects it here.)
  it("links the Probes pill at the probes child route, last in the strip", async () => {
    renderShell();
    await screen.findByRole("link", { name: "General" });

    const probes = screen.getByRole("link", { name: "Probes" });
    expect(probes).toHaveAttribute("href", "/settings/probes");
    expect(screen.getAllByRole("link").at(-1)).toBe(probes);
  });

  it("navigates back to the dashboard when the back button is clicked", async () => {
    renderShell();
    const backButton = await screen.findByRole("button", { name: /Dashboard/ });

    fireEvent.click(backButton);

    expect(await screen.findByTestId("dashboard-root")).toBeInTheDocument();
  });

  // GeneralTab applies a picked theme live before it is saved
  // (GeneralTab.tsx:68-73), and saving any OTHER tab calls revalidate(), which
  // hands this shell a fresh `settings` object. Seeding on every identity
  // change rather than once per mount would snap the preview back to the
  // stored accent while the General draft still holds the picked one.
  it("does not re-seed the accent over a live pick when the loader revalidates", async () => {
    let loads = 0;
    const router = createMemoryRouter(
      [
        {
          id: "settings",
          path: "/settings",
          element: <SettingsShell />,
          loader: () => ({
            // A NEW object every load, same stored accent -- exactly what a
            // revalidation after saving another tab produces.
            settings: {
              modules: { display: "d" },
              display: { config: { d: { accent_theme: "Ice" } } },
              platform: { dc_fan: true },
            },
            mode: "Stop",
            loads: ++loads,
          }),
          HydrateFallback,
          children: [{ index: true, element: <AccentProbe /> }],
        },
      ],
      { initialEntries: ["/settings"] },
    );
    render(
      <QueryClientProvider client={testQueryClient()}>
        <AppPrefsProvider>
          <RouterProvider router={router} />
        </AppPrefsProvider>
      </QueryClientProvider>,
    );

    await screen.findByText("loads:1");
    await waitFor(() => expect(document.documentElement.getAttribute("data-accent")).toBe("ice"));

    fireEvent.click(screen.getByRole("button", { name: "pick crimson" }));
    expect(document.documentElement.getAttribute("data-accent")).toBe("crimson");

    fireEvent.click(screen.getByRole("button", { name: "revalidate" }));
    await screen.findByText("loads:2");
    // The re-seed this guards against lands a render LATER than the loader
    // data it reacts to: new settings -> shell effect -> provider setState ->
    // provider effect writes the attribute. Without this flush the assertion
    // samples before that chain finishes and passes against a broken guard.
    await flushObservers();

    expect(document.documentElement.getAttribute("data-accent")).toBe("crimson");
  });

  it("forwards thermocouple health and transport phase from the parent shell context", async () => {
    const health: ThermocoupleHealthView = {
      device: "Aux amplifier",
      port: "KTT2",
      label: "Ambient",
      displayName: "Ambient",
      role: "Aux",
      report: {
        state: "confirmed",
        faults: ["open"],
        evidence: ["hardware"],
        temperatureValid: false,
        detail: {},
      },
      detector: { source: "hardware", policy: "observe" },
      outcome: "unavailable",
      freshness: { current: true, lastReportedAgeS: 0 },
    };
    function LiveParent() {
      return (
        <Outlet context={{ live: { thermocoupleHealth: [health] }, phase: "unreachable" }} />
      );
    }
    function HealthProbe() {
      const { thermocoupleHealth = [], phase } = useOutletContext<{
        thermocoupleHealth?: ThermocoupleHealthView[];
        phase?: string;
      }>();
      return (
        <span>
          health:{thermocoupleHealth.map((item) => item.displayName).join(",")}; phase:{phase}
        </span>
      );
    }
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <LiveParent />,
          children: [
            {
              path: "settings",
              element: <SettingsShell />,
              loader: () => ({
                settings: { globals: { units: "F" }, platform: { dc_fan: true } },
                mode: "Stop",
                controllerMeta: null,
              }),
              HydrateFallback,
              children: [{ index: true, element: <HealthProbe /> }],
            },
          ],
        },
      ],
      { initialEntries: ["/settings"] },
    );
    render(
      <QueryClientProvider client={testQueryClient()}>
        <AppPrefsProvider>
          <RouterProvider router={router} />
        </AppPrefsProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("health:Ambient; phase:unreachable")).toBeInTheDocument();
  });
});

// Rendered as SettingsShell's child route: reports how many times the loader
// has run, and exposes the two actions the re-seeding bug needs to be driven
// through -- a live accent pick and a revalidation.
function AccentProbe() {
  const { loads } = useRouteLoaderData("settings") as { loads: number };
  const { setAccent } = useAppPrefs();
  const revalidator = useRevalidator();
  return (
    <div>
      <span>loads:{loads}</span>
      <button type="button" onClick={() => setAccent("crimson")}>
        pick crimson
      </button>
      <button type="button" onClick={() => revalidator.revalidate()}>
        revalidate
      </button>
    </div>
  );
}
