import { render } from "@testing-library/react-native";
import { deriveView } from "@pifire/core/dashboard/deriveView";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { ProbeCard } from "../src/components/ProbeCard";

// @testing-library/react-native@14's render() is async (see GrillGauge.test.tsx
// / useLive.test.tsx for the same quirk) -- awaited here, unlike the brief's
// literal snippet, before destructuring query helpers off the result.
it("shows the probe name, reading, and target", async () => {
  const { getByText } = await render(
    <ProbeCard name="Brisket" temp={165} targetStr="→ 203°" units="F" stale={null} />,
  );
  expect(getByText("Brisket")).toBeTruthy();
  expect(getByText("165")).toBeTruthy();
  expect(getByText(/203/)).toBeTruthy();
});

it("marks a stale reading", async () => {
  const { getByText } = await render(
    <ProbeCard name="Brisket" temp={165} targetStr="→ 203°" units="F" stale="last data 47s ago" />,
  );
  expect(getByText("last data 47s ago")).toBeTruthy();
});

// Regression test for a real fix-round bug: an earlier version of ProbeCard
// took a raw `target: number` prop and rendered "→ 203°" whenever
// `target > 0`, regardless of whether the target was actually ARMED. But
// deriveView's own probeCard() gates on `fp.target > 0 && fp.targetReq`
// (deriveView.ts:159) -- a probe can have a STORED target (target=203) that
// is not currently requested (targetReq=false), and the web UI correctly
// shows "AMBIENT" for that grill state. ProbeCard must render whatever
// `targetStr` deriveView computed, not re-derive "has a target" from the raw
// number itself, or phone and web diverge on the exact same probe.
it("does not show a target for a stored-but-disarmed target", async () => {
  const dash = {
    ...FIXTURE_DASH,
    foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], target: 203, targetReq: false }],
  };
  const [p] = deriveView(dash).probes;
  expect(p.targetStr).toBe("AMBIENT"); // sanity: this is the case that used to diverge

  const { getByText, queryByText } = await render(
    <ProbeCard name={p.name} temp={p.tempInt} targetStr={p.targetStr} units={p.unit} stale={p.stale} />,
  );
  expect(getByText("AMBIENT")).toBeTruthy();
  expect(queryByText(/203/)).toBeNull();
});
