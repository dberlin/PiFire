import { describe, expect, it } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { SettingsError } from "../../../../src/components/settings/SettingsError";

// SettingsError is wired as the /settings route's errorElement in App.tsx —
// mirror that wiring with a loader that throws so it renders the same way
// it does in production.
function renderErrorRoute() {
  const router = createMemoryRouter(
    [
      {
        path: "/settings",
        element: <div />,
        loader: () => {
          throw new Error("boom");
        },
        errorElement: <SettingsError />,
      },
      { path: "/", element: <div data-testid="dashboard-root">Dashboard</div> },
    ],
    { initialEntries: ["/settings"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("SettingsError", () => {
  it("renders the error message with Retry and Dashboard affordances", async () => {
    renderErrorRoute();

    expect(await screen.findByText("Couldn't load settings.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("Retry re-invokes the loader (still errors, page stays on the error view)", async () => {
    renderErrorRoute();
    await screen.findByText("Couldn't load settings.");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    // The stubbed loader always throws, so retrying lands back on the same
    // error view rather than crashing the test.
    expect(await screen.findByText("Couldn't load settings.")).toBeInTheDocument();
  });

  it("Dashboard button navigates back to the root route", async () => {
    renderErrorRoute();
    await screen.findByText("Couldn't load settings.");

    fireEvent.click(screen.getByRole("button", { name: "Dashboard" }));

    expect(await screen.findByTestId("dashboard-root")).toBeInTheDocument();
  });
});
