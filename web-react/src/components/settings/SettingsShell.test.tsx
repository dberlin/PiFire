import { describe, expect, it } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { AppPrefsProvider } from "../AppPrefs";
import { SettingsShell } from "./SettingsShell";

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
        children: [{ index: true, element: <div /> }],
      },
      { path: "/", element: <div data-testid="dashboard-root">Dashboard</div> },
    ],
    { initialEntries: ["/settings"] },
  );
  return render(
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>,
  );
}

// Every tab in SettingsShell's nav, in nav order. Keep in sync with TABS there
// (the title deliberately does NOT hardcode a count -- it drifted from 8 to 9
// to 11 while still claiming "8").
const TAB_LABELS = [
  "General",
  "Work Mode",
  "Controller",
  "PWM Fan",
  "Startup / Shutdown",
  "Safety",
  "Pellet Levels",
  "History",
  "Notifications",
  "Units",
  "Platform",
  "Probes",
];

describe("SettingsShell", () => {
  it("renders every nav tab and the back-to-dashboard control", async () => {
    renderShell();
    // Loader data resolves asynchronously even though the loader itself is
    // synchronous — wait for the first tab link before asserting on the rest.
    await screen.findByRole("link", { name: "General" });
    for (const label of TAB_LABELS) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(TAB_LABELS);
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
    for (const label of TAB_LABELS.filter((l) => l !== "PWM Fan")) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    const rendered = screen.getAllByRole("link").map((a) => a.textContent);
    expect(rendered).toEqual(TAB_LABELS.filter((l) => l !== "PWM Fan"));
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
});
