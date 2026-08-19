import { render } from "@testing-library/react-native";
import { GrillGauge } from "../src/components/GrillGauge";

// @testing-library/react-native@14's render is async (see useLive.test.tsx's
// note on renderHook for the same quirk); it must be awaited before
// destructuring the query helpers off its result.
it("renders the temperature and mode", async () => {
  const { getByText } = await render(
    <GrillGauge temp={225} stale={null} setpoint={225} maxTemp={600} frac={0.375}
      hasSetpoint modeLabel="Hold" units="F" cooking animate={false} />,
  );
  expect(getByText("225")).toBeTruthy();
  expect(getByText("Hold")).toBeTruthy();
});

it("marks a carried-over reading as stale", async () => {
  const { getByText } = await render(
    <GrillGauge temp={225} stale="last data 47s ago" setpoint={225} maxTemp={600}
      frac={0.375} hasSetpoint modeLabel="Hold" units="F" cooking={false} animate={false} />,
  );
  expect(getByText("last data 47s ago")).toBeTruthy();
});
