import { describe, expect, it } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { SettingsShell } from "./SettingsShell";

// SettingsShell reads its data via `useLoaderData()`, not outlet context, so
// (unlike a plain tab) it needs a real data router with a loader — the
// shared `renderRoute` harness (which only wires up Outlet context) doesn't
// supply that. Build a minimal one here with the /settings route's own
// loader stubbed to return { settings, mode } synchronously, plus a sibling
// "/" route so the back-to-dashboard navigation (SettingsShell.tsx:21) can
// be observed landing somewhere.
function renderShell() {
  const router = createMemoryRouter(
    [
      {
        path: "/settings",
        element: <SettingsShell />,
        loader: () => ({ settings: { globals: { units: "F" } }, mode: "Stop" }),
        children: [{ index: true, element: <div /> }],
      },
      { path: "/", element: <div data-testid="dashboard-root">Dashboard</div> },
    ],
    { initialEntries: ["/settings"] },
  );
  return render(<RouterProvider router={router} />);
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
    expect(screen.getByRole("button", { name: /Dashboard/ })).toBeInTheDocument();
  });

  it("navigates back to the dashboard when the back button is clicked", async () => {
    renderShell();
    const backButton = await screen.findByRole("button", { name: /Dashboard/ });

    fireEvent.click(backButton);

    expect(await screen.findByTestId("dashboard-root")).toBeInTheDocument();
  });
});
