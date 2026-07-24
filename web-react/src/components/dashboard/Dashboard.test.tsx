import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { FIXTURE_DASH } from "../../helpers/fixture";
import type { DashData } from "../../helpers/types";
import { renderRoute } from "../../test-utils";
import { Dashboard } from "./Dashboard";

afterEach(cleanup);

const OK: CommandResult = { ok: true, message: "" };

function makeCommand(): CommandClient {
  return {
    setMode: rs.fn(async () => OK),
    hold: rs.fn(async () => OK),
    setSmokePlus: rs.fn(async () => OK),
    setPMode: rs.fn(async () => OK),
    prime: rs.fn(async () => OK),
    timerStart: rs.fn(async () => OK),
    timerPause: rs.fn(async () => OK),
    timerStop: rs.fn(async () => OK),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
    manualOutput: rs.fn(async () => OK),
    manualPwm: rs.fn(async () => OK),
  };
}

function renderDashboard(dash: DashData, overrides: Partial<Parameters<typeof Dashboard>[0]> = {}) {
  return renderRoute(
    <Dashboard
      dash={dash}
      command={makeCommand()}
      phase="live"
      controlAlive={true}
      accent="ember"
      setAccent={rs.fn()}
      animate={false}
      setAnimate={rs.fn()}
      {...overrides}
    />,
    undefined,
  );
}

describe("Dashboard", () => {
  it("renders the header with the grill name and a LIVE label when connected", () => {
    renderDashboard(FIXTURE_DASH, { phase: "live", controlAlive: true });
    expect(screen.getByText(FIXTURE_DASH.grillName)).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("shows a DEMO label when running against the demo backend", () => {
    renderDashboard(FIXTURE_DASH, { phase: "demo" });
    expect(screen.getByText("DEMO")).toBeInTheDocument();
  });

  it("shows the uppercased mode badge and the cook-time counter", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Stop" });
    expect(screen.getByText("STOP")).toBeInTheDocument();
    expect(screen.getByText("Cook Time")).toBeInTheDocument();
    expect(screen.getByText("00:00")).toBeInTheDocument();
  });

  it("renders the food-probe column when foodProbes are present", () => {
    renderDashboard(FIXTURE_DASH);
    expect(screen.getByText("Food Probes")).toBeInTheDocument();
    expect(screen.getAllByText("AMBIENT")).toHaveLength(FIXTURE_DASH.foodProbes.length);
  });

  it("hides the food-probe column when there are no foodProbes", () => {
    renderDashboard({ ...FIXTURE_DASH, foodProbes: [] });
    expect(screen.queryByText("Food Probes")).not.toBeInTheDocument();
  });

  it("shows the P-mode and an ON smoke+ pill when smokePlus is set", () => {
    renderDashboard({ ...FIXTURE_DASH, pMode: 2, smokePlus: true });
    expect(screen.getByText("P-MODE")).toBeInTheDocument();
    expect(screen.getByText("P-2")).toBeInTheDocument();
    expect(screen.getByText("SMOKE+")).toBeInTheDocument();
    expect(screen.getByText("ON")).toBeInTheDocument();
  });

  it("shows an OFF smoke+ pill when smokePlus is unset", () => {
    renderDashboard({ ...FIXTURE_DASH, smokePlus: false });
    expect(screen.getByText("OFF")).toBeInTheDocument();
  });

  it("shows the MONITOR mode badge and a zeroed cook-time counter (non-cooking)", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Monitor" });
    expect(screen.getByText("MONITOR")).toBeInTheDocument();
    expect(screen.getByText("00:00")).toBeInTheDocument();
  });

  it("shows the SHUTDOWN mode badge and a zeroed cook-time counter (non-cooking)", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Shutdown" });
    expect(screen.getByText("SHUTDOWN")).toBeInTheDocument();
    expect(screen.getByText("00:00")).toBeInTheDocument();
  });

  it("resets the cook-time counter across cooking <-> non-cooking transitions on the same instance", () => {
    const { rerender } = renderDashboard({ ...FIXTURE_DASH, currentMode: "Hold" });
    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText("00:00")).toBeInTheDocument();

    // Hold (cooking) -> Stop (not cooking): prevCooking edge fires, cookStart clears.
    rerender(
      <MemoryRouter>
        <Dashboard
          dash={{ ...FIXTURE_DASH, currentMode: "Stop" }}
          command={makeCommand()}
          phase="live"
          controlAlive={true}
          accent="ember"
          setAccent={rs.fn()}
          animate={false}
          setAnimate={rs.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("STOP")).toBeInTheDocument();
    expect(screen.getByText("00:00")).toBeInTheDocument();

    // Stop (not cooking) -> Smoke (cooking): prevCooking edge fires again, cookStart re-seeds.
    rerender(
      <MemoryRouter>
        <Dashboard
          dash={{ ...FIXTURE_DASH, currentMode: "Smoke" }}
          command={makeCommand()}
          phase="live"
          controlAlive={true}
          accent="ember"
          setAccent={rs.fn()}
          animate={false}
          setAnimate={rs.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("SMOKE")).toBeInTheDocument();
    expect(screen.getByText("00:00")).toBeInTheDocument();
  });

  it("clicking an accent swatch calls setAccent with that accent", async () => {
    const user = userEvent.setup();
    const setAccent = rs.fn();
    renderDashboard(FIXTURE_DASH, { setAccent });
    await user.click(screen.getByRole("button", { name: "ice" }));
    expect(setAccent).toHaveBeenCalledWith("ice");
  });

  it("clicking the ANIM toggle calls setAnimate with the flipped value", async () => {
    const user = userEvent.setup();
    const setAnimate = rs.fn();
    renderDashboard(FIXTURE_DASH, { animate: false, setAnimate });
    await user.click(screen.getByText("ANIM"));
    expect(setAnimate).toHaveBeenCalledWith(true);
  });

  it("clicking the settings gear navigates to /settings", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="/"
            element={
              <Dashboard
                dash={FIXTURE_DASH}
                command={makeCommand()}
                phase="live"
                controlAlive={true}
                accent="ember"
                setAccent={rs.fn()}
                animate={false}
                setAnimate={rs.fn()}
              />
            }
          />
          <Route path="/settings" element={<div data-testid="settings-route" />} />
        </Routes>
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "settings" }));
    expect(screen.getByTestId("settings-route")).toBeInTheDocument();
  });
});
