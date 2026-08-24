import { render } from "@testing-library/react-native";
import { GrillGauge } from "../src/components/GrillGauge";
import { projectedHealth } from "./healthFixture";

// @testing-library/react-native@14's render is async (see useLive.test.tsx's
// note on renderHook for the same quirk); it must be awaited before
// destructuring the query helpers off its result.
it("renders the temperature and mode", async () => {
  const { getByText } = await render(
    <GrillGauge accent="ember" temp={225} stale={null} setpoint={225} maxTemp={600} frac={0.375}
      hasSetpoint modeLabel="Hold" units="F" cooking animate={false} />,
  );
  expect(getByText("225")).toBeTruthy();
  expect(getByText("Hold")).toBeTruthy();
});

it("marks a carried-over reading as stale", async () => {
  const { getByText } = await render(
    <GrillGauge accent="ember" temp={225} stale="last data 47s ago" setpoint={225} maxTemp={600}
      frac={0.375} hasSetpoint modeLabel="Hold" units="F" cooking={false} animate={false} />,
  );
  expect(getByText("last data 47s ago")).toBeTruthy();
});

// The point of threading `accent` through at all: a non-default accent must
// render just as well as ember, picking up GAUGE_ACCENT's ice stops instead
// of crashing or silently falling back to ember.
it("renders with a non-default accent", async () => {
  const { getByText } = await render(
    <GrillGauge accent="ice" temp={225} stale={null} setpoint={225} maxTemp={600} frac={0.375}
      hasSetpoint modeLabel="Hold" units="F" cooking animate={false} />,
  );
  expect(getByText("225")).toBeTruthy();
  expect(getByText("Hold")).toBeTruthy();
});

it("keeps a suspected primary numeric and surfaces it inline", async () => {
  const health = projectedHealth({ report: { state: "suspected" } });
  const { getByText, getByRole } = await render(
    <GrillGauge
      accent="ember"
      temp={225}
      stale={null}
      setpoint={250}
      maxTemp={600}
      frac={0.375}
      hasSetpoint
      modeLabel="Hold"
      units="F"
      cooking
      animate={false}
      health={health}
    />,
  );

  expect(getByText("225")).toBeTruthy();
  expect(getByText("CHECK PROBE")).toBeTruthy();
  expect(getByRole("alert").props.accessibilityLabel).toContain("reading still available");
});

it("shows an em dash without a unit for a confirmed-invalid primary", async () => {
  const health = projectedHealth({
    report: { state: "confirmed", faults: ["open"], temperatureValid: false },
    outcome: "stopped",
  });
  const { getByText, queryByText } = await render(
    <GrillGauge
      accent="ember"
      temp={null}
      stale={null}
      setpoint={250}
      maxTemp={600}
      frac={0}
      hasSetpoint
      modeLabel="Error"
      units="F"
      cooking={false}
      animate={false}
      health={health}
    />,
  );

  expect(getByText("—")).toBeTruthy();
  expect(queryByText("°F")).toBeNull();
  expect(getByText("CONTROL PROBE UNAVAILABLE")).toBeTruthy();
});

it("does not render a health pill after recovery", async () => {
  const { queryByText } = await render(
    <GrillGauge
      accent="ember"
      temp={225}
      stale={null}
      setpoint={250}
      maxTemp={600}
      frac={0.375}
      hasSetpoint
      modeLabel="Hold"
      units="F"
      cooking
      animate={false}
      health={projectedHealth()}
    />,
  );

  expect(queryByText("CHECK PROBE")).toBeNull();
  expect(queryByText("FAULT")).toBeNull();
});
