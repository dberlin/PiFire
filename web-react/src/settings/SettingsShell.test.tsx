// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";
import { SettingsShell } from "./SettingsShell";

// SettingsShell reads its data via `useLoaderData()`, not outlet context, so
// (unlike a plain tab) it needs a real data router with a loader — the
// shared `renderRoute` harness (which only wires up Outlet context) doesn't
// supply that. Build a minimal one here with the /settings route's own
// loader stubbed to return { settings, mode } synchronously.
function renderShell() {
  const router = createMemoryRouter(
    [
      {
        path: "/settings",
        element: <SettingsShell />,
        loader: () => ({ settings: { globals: { units: "F" } }, mode: "Stop" }),
        children: [{ index: true, element: <div /> }],
      },
    ],
    { initialEntries: ["/settings"] },
  );
  return render(<RouterProvider router={router} />);
}

const TAB_LABELS = [
  "General",
  "Work Mode",
  "PWM Fan",
  "Startup / Shutdown",
  "Safety",
  "Pellet Levels",
  "History",
  "Units",
];

describe("SettingsShell", () => {
  it("renders all 8 nav tabs and the back-to-dashboard control", async () => {
    renderShell();
    // Loader data resolves asynchronously even though the loader itself is
    // synchronous — wait for the first tab link before asserting on the rest.
    await screen.findByRole("link", { name: "General" });
    for (const label of TAB_LABELS) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /Dashboard/ })).toBeInTheDocument();
  });
});
