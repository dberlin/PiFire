import { render } from "@testing-library/react-native";
import { deriveView } from "@pifire/core/dashboard/deriveView";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { ProbeCard } from "../src/components/ProbeCard";

// @testing-library/react-native@14's render() is async (see GrillGauge.test.tsx
// / useLive.test.tsx for the same quirk) -- awaited here before destructuring
// query helpers off the result.
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
  expect(p.targetStr).toBe("AMBIENT"); // stored-but-unarmed target must not render as armed

  const { getByText, queryByText } = await render(
    <ProbeCard name={p.name} temp={p.tempInt} targetStr={p.targetStr} units={p.unit} stale={p.stale} />,
  );
  expect(getByText("AMBIENT")).toBeTruthy();
  expect(queryByText(/203/)).toBeNull();
});

// RN style props arrive as a value or an arbitrarily nested array of them;
// flatten to one object before reading a single property off it.
// biome-ignore lint/suspicious/noExplicitAny: react-test-renderer props are untyped.
const flatStyle = (style: any): Record<string, unknown> =>
  Array.isArray(style) ? Object.assign({}, ...style.flat(Infinity).filter(Boolean)) : (style ?? {});

// The row-uniformity contract. A card must take the width its column gives it
// and size its type from that, never from its own text -- when it sized from
// content, a probe named "BRISKET FLAT" rendered wider than "PROBE 2" and
// three probes wrapped into a ragged 2+1 instead of sharing the row.
it("takes the width it is given, whatever its name", async () => {
  const cardWidth = async (name: string) => {
    const r = await render(
      <ProbeCard name={name} temp={165} targetStr="AMBIENT" units="F" stale={null} width={112} />,
    );
    return flatStyle(r.getByTestId("probe-card").props.style).width as number;
  };
  // Both cards were handed the same column, so neither name may change it.
  expect(await cardWidth("BRISKET FLAT POINT")).toBe(112);
  expect(await cardWidth("P2")).toBe(112);
});

it("shrinks the reading for a narrow column and not for a wide one", async () => {
  const lone = await render(
    <ProbeCard name="Brisket" temp={165} targetStr="AMBIENT" units="F" stale={null} width={360} />,
  );
  const threeAcross = await render(
    <ProbeCard name="Brisket" temp={165} targetStr="AMBIENT" units="F" stale={null} width={112} />,
  );
  const sizeOf = (r: Awaited<ReturnType<typeof render>>) =>
    flatStyle(r.getByText("165").props.style).fontSize as number;
  expect(sizeOf(lone)).toBeGreaterThan(sizeOf(threeAcross));
  expect(sizeOf(threeAcross)).toBeGreaterThanOrEqual(22);
});
