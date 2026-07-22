import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, screen } from "@testing-library/react";
import { FIXTURE_DASH } from "../helpers/fixture";
import { renderRoute } from "../test-utils";

const useDashDataMock = rs.fn();

rs.mock("../helpers/useDashData", () => ({
  useDashData: () => useDashDataMock(),
}));

const { DashboardRoute } = await import("./DashboardRoute");
const { AppPrefsProvider } = await import("./AppPrefs");

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

describe("DashboardRoute", () => {
  it("renders ConnectionStatus when there is no live/demo data yet", () => {
    useDashDataMock.mockReturnValue({
      dash: FIXTURE_DASH,
      phase: "connecting",
      controlAlive: false,
      targetUrl: "http://pifire.local:5000",
      command,
    });

    renderRoute(
      <AppPrefsProvider>
        <DashboardRoute />
      </AppPrefsProvider>,
      undefined,
    );

    expect(screen.getByText("Connecting to PiFire…")).toBeInTheDocument();
    expect(screen.getByText("http://pifire.local:5000")).toBeInTheDocument();
  });

  it("renders the Dashboard with the current mode badge once phase is live", () => {
    useDashDataMock.mockReturnValue({
      dash: { ...FIXTURE_DASH, currentMode: "Hold" },
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command,
    });

    renderRoute(
      <AppPrefsProvider>
        <DashboardRoute />
      </AppPrefsProvider>,
      undefined,
    );

    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });
});
