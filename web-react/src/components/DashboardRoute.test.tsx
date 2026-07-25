import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { FIXTURE_DASH } from "../helpers/fixture";

const useLiveStateMock = rs.fn();
const navigateMock = rs.fn();
const getSettingsMock = rs.fn();

rs.mock("../helpers/useLiveState", () => ({
  useLiveState: () => useLiveStateMock(),
}));
rs.mock("react-router", () => ({
  useNavigate: () => navigateMock,
}));
rs.mock("../helpers/settings/settingsApi", () => ({
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
}));

const { DashboardRoute } = await import("./DashboardRoute");
const { AppPrefsProvider } = await import("./AppPrefs");

function renderDashboardRoute() {
  return render(
    <AppPrefsProvider>
      <DashboardRoute />
    </AppPrefsProvider>,
  );
}

afterEach(cleanup);

beforeEach(() => {
  navigateMock.mockClear();
  getSettingsMock.mockReset();
  getSettingsMock.mockResolvedValue({ globals: { first_time_setup: false } });
});

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

describe("DashboardRoute", () => {
  it("renders ConnectionStatus when there is no live/demo data yet", () => {
    useLiveStateMock.mockReturnValue({
      live: FIXTURE_DASH,
      phase: "connecting",
      controlAlive: false,
      targetUrl: "http://pifire.local:5000",
      command,
    });

    renderDashboardRoute();

    expect(screen.getByText("Connecting to PiFire…")).toBeInTheDocument();
    expect(screen.getByText("http://pifire.local:5000")).toBeInTheDocument();
  });

  it("renders the Dashboard with the current mode badge once phase is live", () => {
    useLiveStateMock.mockReturnValue({
      live: { ...FIXTURE_DASH, currentMode: "Hold" },
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command,
    });

    renderDashboardRoute();

    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("navigates to /wizard when first_time_setup is true", async () => {
    useLiveStateMock.mockReturnValue({
      live: FIXTURE_DASH,
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command,
    });
    getSettingsMock.mockResolvedValue({ globals: { first_time_setup: true } });

    renderDashboardRoute();

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/wizard"));
  });

  it("does not navigate when first_time_setup is false", async () => {
    useLiveStateMock.mockReturnValue({
      live: FIXTURE_DASH,
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command,
    });
    getSettingsMock.mockResolvedValue({ globals: { first_time_setup: false } });

    renderDashboardRoute();

    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("still renders the dashboard when the check fails (advisory only)", async () => {
    useLiveStateMock.mockReturnValue({
      live: FIXTURE_DASH,
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command,
    });
    getSettingsMock.mockRejectedValue(new Error("offline"));

    renderDashboardRoute();

    await waitFor(() => expect(getSettingsMock).toHaveBeenCalled());
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
