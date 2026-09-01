import type { ThermocoupleHealthView } from "@pifire/core/contracts/core";
import { deriveView } from "@pifire/core/dashboard/deriveView";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { GrillGauge } from "../../../../src/components/dashboard/GrillGauge";

afterEach(cleanup);

function primaryHealth(
  state: ThermocoupleHealthView["report"]["state"],
  outcome: ThermocoupleHealthView["outcome"],
  current = true,
): ThermocoupleHealthView {
  return {
    device: "Pit amplifier",
    port: "KTT0",
    label: FIXTURE_DASH.primaryProbe.label,
    displayName: FIXTURE_DASH.primaryProbe.title,
    role: "Primary",
    report: {
      state,
      faults: state === "confirmed" ? ["open"] : [],
      evidence: state === "healthy" ? [] : ["hardware"],
      temperatureValid: outcome === "none" || outcome === "notify_only",
      detail: {},
    },
    detector: { source: "hardware", policy: "observe" },
    outcome,
    freshness: { current, lastReportedAgeS: current ? 0 : 60 },
  };
}

function healthGauge(
  state: ThermocoupleHealthView["report"]["state"],
  outcome: ThermocoupleHealthView["outcome"],
  current = true,
) {
  const dash = {
    ...FIXTURE_DASH,
    primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 225 },
    thermocoupleHealth: [primaryHealth(state, outcome, current)],
  };
  const view = deriveView(dash);
  return (
    <GrillGauge
      temp={view.tempInt}
      stale={view.stale}
      health={view.primaryHealth}
      setpoint={dash.primaryProbe.setTemp}
      maxTemp={view.maxTemp}
      frac={view.gaugeFrac}
      hasSetpoint={view.hasSetpoint}
      modeLabel={view.modeLabel}
      units={view.units}
      cooking={view.cooking}
      animate={false}
    />
  );
}

describe("GrillGauge", () => {
  it("shows the rounded temp and setpoint when a setpoint is set, with an uppercased mode label", () => {
    const dash = {
      ...FIXTURE_DASH,
      currentMode: "Hold",
      primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 300, setTemp: 225, maxTemp: 600 },
    };
    const v = deriveView(dash);
    render(
      <GrillGauge
        temp={v.tempInt}
        stale={v.stale}
        setpoint={dash.primaryProbe.setTemp}
        maxTemp={v.maxTemp}
        frac={v.gaugeFrac}
        hasSetpoint={v.hasSetpoint}
        modeLabel={v.modeLabel}
        units={v.units}
        cooking={v.cooking}
        animate={false}
      />,
    );
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("SET 225°")).toBeInTheDocument();
    expect(screen.getByText("HOLD")).toBeInTheDocument();
  });

  it("hides the SET tag when there is no setpoint", () => {
    const dash = {
      ...FIXTURE_DASH,
      currentMode: "Stop",
      primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 9999, setTemp: 0, maxTemp: 600 },
    };
    const v = deriveView(dash);
    render(
      <GrillGauge
        temp={v.tempInt}
        stale={v.stale}
        setpoint={dash.primaryProbe.setTemp}
        maxTemp={v.maxTemp}
        frac={v.gaugeFrac}
        hasSetpoint={v.hasSetpoint}
        modeLabel={v.modeLabel}
        units={v.units}
        cooking={v.cooking}
        animate={false}
      />,
    );
    expect(screen.getByText("9999")).toBeInTheDocument();
    expect(screen.queryByText(/^SET /)).not.toBeInTheDocument();
    expect(screen.getByText("STOP")).toBeInTheDocument();
  });

  it("keeps the numeric primary reading and truthful observe copy for notify-only faults", () => {
    render(healthGauge("confirmed", "notify_only"));

    expect(screen.getByText("225")).toBeInTheDocument();
    expect(screen.getByText("FAULT")).toBeInTheDocument();
    expect(
      screen.getByText("Fault detected — Observe mode did not stop heating."),
    ).toBeInTheDocument();
  });

  it("shows an em dash and stopped copy when the control probe is unavailable", () => {
    render(healthGauge("confirmed", "stopped"));

    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("225")).toBeNull();
    expect(screen.getByText("CONTROL PROBE UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("PiFire stopped heating.")).toBeInTheDocument();
  });

  it("keeps suspected numeric and does not add healthy or unmonitored pills", () => {
    const { container, rerender } = render(healthGauge("suspected", "none"));
    expect(screen.getByText("225")).toBeInTheDocument();
    expect(screen.getByText("CHECK PROBE")).toBeInTheDocument();

    rerender(healthGauge("healthy", "none"));
    expect(container.querySelector(".pf-dash-gauge-health")).toBeNull();
    rerender(healthGauge("unmonitored", "none"));
    expect(container.querySelector(".pf-dash-gauge-health")).toBeNull();
  });

  it("marks an old health report as last reported without changing its outcome", () => {
    render(healthGauge("confirmed", "stopped", false));

    expect(screen.getByText("Last reported: CONTROL PROBE UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("PiFire stopped heating.")).toBeInTheDocument();
  });
});
