import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, screen } from "@testing-library/react";
import type { CommandClient, CommandResult } from "../command";
import { FIXTURE_DASH } from "../fixture";
import { renderRoute } from "../test-utils";
import type { DashData } from "../types";
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
});
