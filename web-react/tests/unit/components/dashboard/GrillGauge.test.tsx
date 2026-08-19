import { deriveView } from "@pifire/core/dashboard/deriveView";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { GrillGauge } from "../../../../src/components/dashboard/GrillGauge";

afterEach(cleanup);

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
});
