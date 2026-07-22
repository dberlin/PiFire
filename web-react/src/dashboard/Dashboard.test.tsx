// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, cleanup } from "@testing-library/react";
import { renderRoute } from "../test-utils";
import { Dashboard } from "./Dashboard";
import { FIXTURE_DASH } from "../fixture";
import type { CommandClient, CommandResult } from "../command";
import type { DashData } from "../types";

afterEach(cleanup);

const OK: CommandResult = { ok: true, message: "" };

function makeCommand(): CommandClient {
  return {
    setMode: vi.fn(async () => OK),
    hold: vi.fn(async () => OK),
    setSmokePlus: vi.fn(async () => OK),
    setPMode: vi.fn(async () => OK),
    prime: vi.fn(async () => OK),
    timerStart: vi.fn(async () => OK),
    timerPause: vi.fn(async () => OK),
    timerStop: vi.fn(async () => OK),
    system: vi.fn(async () => OK),
    setUnits: vi.fn(async () => OK),
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
      setAccent={vi.fn()}
      animate={false}
      setAnimate={vi.fn()}
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
});
