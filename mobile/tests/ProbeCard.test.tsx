import { render } from "@testing-library/react-native";
import { ProbeCard } from "../src/components/ProbeCard";

// @testing-library/react-native@14's render() is async (see GrillGauge.test.tsx
// / useLive.test.tsx for the same quirk) -- awaited here, unlike the brief's
// literal snippet, before destructuring query helpers off the result.
it("shows the probe name, reading, and target", async () => {
  const { getByText } = await render(
    <ProbeCard name="Brisket" temp={165} target={203} units="F" stale={null} />,
  );
  expect(getByText("Brisket")).toBeTruthy();
  expect(getByText("165")).toBeTruthy();
  expect(getByText(/203/)).toBeTruthy();
});

it("marks a stale reading", async () => {
  const { getByText } = await render(
    <ProbeCard name="Brisket" temp={165} target={203} units="F" stale="last data 47s ago" />,
  );
  expect(getByText("last data 47s ago")).toBeTruthy();
});
