import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { FIXTURE_DASH } from "../helpers/fixture";

const useDashDataMock = rs.fn();
rs.mock("../helpers/useDashData", () => ({
  useDashData: () => useDashDataMock(),
}));

const getSettingsMock = rs.fn();
const getModeMock = rs.fn();
rs.mock("../helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
  getMode: (...args: unknown[]) => getModeMock(...args),
  buildSettingsUrl: (baseUrl: string, path: string) => `${baseUrl}/api/${path}`,
}));

const { AppPrefsProvider } = await import("./AppPrefs");
const { default: App, routes } = await import("./App");

afterEach(cleanup);

const command = {
  setMode: rs.fn(),
  hold: rs.fn(),
  setSmokePlus: rs.fn(),
  setPMode: rs.fn(),
  prime: rs.fn(),
  timerStart: rs.fn(),
  timerPause: rs.fn(),
  timerStop: rs.fn(),
  system: rs.fn(),
  setUnits: rs.fn(),
};

function renderApp(initialEntry: string) {
  const router = createMemoryRouter(routes, { initialEntries: [initialEntry] });
  return render(
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>,
  );
}

describe("App routing", () => {
  it("renders the dashboard at /", () => {
    useDashDataMock.mockReturnValue({
      dash: { ...FIXTURE_DASH, currentMode: "Hold" },
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
      globals: { grill_name: "Backyard Smoker", page_theme: "dark" },
    });
    getModeMock.mockResolvedValue("Stop");

    renderApp("/settings/general");

    expect(await screen.findByRole("link", { name: "General" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Backyard Smoker")).toBeInTheDocument();
    expect(getSettingsMock).toHaveBeenCalled();
    expect(getModeMock).toHaveBeenCalled();
  });

  it("the default export mounts its own AppPrefsProvider + browser router and renders the dashboard at /", () => {
    useDashDataMock.mockReturnValue({
      dash: { ...FIXTURE_DASH, currentMode: "Monitor" },
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
