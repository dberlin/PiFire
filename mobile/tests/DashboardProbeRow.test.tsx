import { render } from "@testing-library/react-native";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { PROBE_GAP, probeGrid } from "@pifire/core/dashboard/scale";
import { wireHealth } from "./healthFixture";

// The screen reads its connection and preferences through the root layout's
// contexts, which only exist inside the running app. Mocking them is what lets
// this file render the dashboard itself rather than a component in isolation
// -- and the probe row's whole contract is about how cards relate to EACH
// OTHER, which a single-card test cannot see.
// Assigned in beforeEach, not here: jest hoists the jest.mock calls below
// above the imports, so module-scope initialisers cannot read an import.
const live = {} as { dash: DashSocketPayload };

jest.mock("../app/_layout", () => ({
  useLiveContext: () => ({
    live: live.dash,
    phase: "live",
    lastPayloadAt: Date.now(),
    command: {},
    reconnect: jest.fn(),
  }),
  usePrefsContext: () => ({
    prefs: { accent: "ember", host: "pifire.local", units: "F" },
    updatePrefs: jest.fn(),
    setActiveHost: jest.fn(),
  }),
}));

const SCREEN_WIDTH = 393; // iPhone 17 Pro Max logical width class
jest.mock("react-native/Libraries/Utilities/useWindowDimensions", () => ({
  __esModule: true,
  default: () => ({ width: 393, height: 852, scale: 3, fontScale: 1 }),
}));

import Dashboard from "../app/(tabs)/index";

/** RN style props arrive as a value or a nested array of them. */
// biome-ignore lint/suspicious/noExplicitAny: react-test-renderer props are untyped.
const flatStyle = (style: any): Record<string, unknown> =>
  Array.isArray(style) ? Object.assign({}, ...style.flat(Infinity).filter(Boolean)) : (style ?? {});

/** Builds a payload carrying exactly `n` food probes. */
function withProbes(n: number): DashSocketPayload {
  const template = FIXTURE_DASH.foodProbes[0];
  return {
    ...FIXTURE_DASH,
    foodProbes: Array.from({ length: n }, (_, i) => ({
      ...template,
      // Deliberately uneven name lengths: the old layout sized each card from
      // its own text, so these would have produced three different widths.
      title: i === 0 ? "BRISKET FLAT POINT" : `P${i + 1}`,
      label: `Probe${i + 1}`,
    })),
  };
}

async function cardWidths(n: number): Promise<number[]> {
  live.dash = withProbes(n);
  const { getAllByTestId } = await render(<Dashboard />);
  const widths = getAllByTestId("probe-card").map((c) => flatStyle(c.props.style).width);
  // Guard against the vacuous pass: if the screen stopped handing cards an
  // explicit width, every entry would be `undefined` and the equal-widths
  // assertion below would still hold.
  for (const w of widths) {
    expect(typeof w).toBe("number");
    expect(Number.isFinite(w as number)).toBe(true);
  }
  return widths as number[];
}

beforeEach(() => {
  live.dash = FIXTURE_DASH;
});

describe("the probe row", () => {
  it.each([1, 2, 3, 4, 5, 6])("renders every probe when there are %i", async (n) => {
    expect(await cardWidths(n)).toHaveLength(n);
  });

  it.each([1, 2, 3, 4, 5, 6])("gives all %i cards the same width", async (n) => {
    const widths = await cardWidths(n);
    expect(new Set(widths).size).toBe(1);
  });

  it.each([1, 2, 3])("fills the row exactly with %i across", async (n) => {
    const widths = await cardWidths(n);
    const rowWidth = SCREEN_WIDTH - 16 * 2;
    // n cards plus the gaps between them span the content width, which is what
    // keeps the row flush with the full-width hopper below it.
    expect(widths[0] * n + PROBE_GAP * (n - 1)).toBeCloseTo(rowWidth, 5);
  });

  it("caps at three across and balances the rows", async () => {
    // 4 probes are 2x2, not 3+1 -- a greedy fill would leave a lone card on
    // the second row at a different width from its neighbours.
    expect(probeGrid(4, 361).columns).toBe(2);
    const widths = await cardWidths(4);
    expect(widths[0]).toBeCloseTo(probeGrid(4, 361).width, 5);
  });

  it("does not let a long probe name widen its card", async () => {
    // The first probe is named "BRISKET FLAT POINT" and the rest "P2", "P3".
    const [first, ...rest] = await cardWidths(3);
    for (const w of rest) {
      expect(w).toBe(first);
    }
  });
});

describe("probe health layering", () => {
  it("keeps Aux detail-only while surfacing its confirmed causes in the summary", async () => {
    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({
          role: "Aux",
          label: "Stack",
          displayName: "Stack",
          port: "TC2",
          report: {
            state: "confirmed",
            faults: ["short", "open"],
            temperatureValid: false,
          },
          outcome: "unavailable",
        }),
      ],
    };

    const { getAllByTestId, getByRole, getByText } = await render(<Dashboard />);

    expect(getAllByTestId("probe-card")).toHaveLength(FIXTURE_DASH.foodProbes.length);
    expect(getByText("Stack")).toBeTruthy();
    expect(getByText(/Hardware reported an open circuit/)).toBeTruthy();
    expect(getByText(/Hardware reported a short circuit/)).toBeTruthy();
    expect(getByRole("summary").props.accessibilityLabel).toContain("Stack");
  });

  it("matches an Aux-only live region to dynamic appearance and escalation", async () => {
    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({ role: "Aux", label: "Stack", displayName: "Stack" }),
      ],
    };
    const screen = await render(<Dashboard />);
    expect(screen.queryByTestId("health-summary")).toBeNull();

    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({
          role: "Aux",
          label: "Stack",
          displayName: "Stack",
          report: { state: "suspected" },
        }),
      ],
    };
    await screen.rerender(<Dashboard />);
    expect(screen.getByTestId("health-summary").props.accessibilityLiveRegion).toBe("polite");

    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({
          role: "Aux",
          label: "Stack",
          displayName: "Stack",
          report: {
            state: "confirmed",
            faults: ["open"],
            temperatureValid: false,
          },
          outcome: "unavailable",
        }),
      ],
    };
    await screen.rerender(<Dashboard />);
    expect(screen.getByTestId("health-summary").props.accessibilityLiveRegion).toBe("assertive");
  });

  it("keeps the summary quiet when a configured probe card owns the announcement", async () => {
    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({
          role: "Food",
          label: FIXTURE_DASH.foodProbes[0].label,
          displayName: FIXTURE_DASH.foodProbes[0].title,
          report: { state: "suspected" },
        }),
      ],
    };

    const screen = await render(<Dashboard />);

    expect(screen.getByTestId("probe-health-inline").props.accessibilityLiveRegion).toBe("polite");
    expect(screen.getByTestId("health-summary").props.accessibilityLiveRegion).toBeUndefined();
  });

  it("shows the shared additional-issue count and lets long detail wrap", async () => {
    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({
          role: "Aux",
          label: "Stack",
          displayName: "Stack",
          port: "TC2",
          report: { state: "suspected" },
        }),
        wireHealth({
          role: "Food",
          label: FIXTURE_DASH.foodProbes[0].label,
          displayName: FIXTURE_DASH.foodProbes[0].title,
          port: "TC1",
          report: { state: "suspected" },
        }),
      ],
    };

    const { getAllByText, getByTestId, getByText } = await render(<Dashboard />);

    expect(getByText("+1 more")).toBeTruthy();
    expect(getByTestId("health-summary").props.style).toEqual(
      expect.arrayContaining([expect.objectContaining({ width: "100%" })]),
    );
    for (const copy of getAllByText("Possible thermocouple issue; reading still available.")) {
      expect(copy.props.numberOfLines).toBeUndefined();
    }
  });

  it("removes the summary when the latest payload reports recovery", async () => {
    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({
          role: "Aux",
          label: "Stack",
          displayName: "Stack",
          report: { state: "suspected" },
        }),
      ],
    };
    const screen = await render(<Dashboard />);
    expect(screen.getByTestId("health-summary")).toBeTruthy();

    live.dash = {
      ...FIXTURE_DASH,
      thermocoupleHealth: [
        wireHealth({ role: "Aux", label: "Stack", displayName: "Stack" }),
      ],
    };
    await screen.rerender(<Dashboard />);

    expect(screen.queryByRole("summary")).toBeNull();
    expect(screen.queryByTestId("health-summary")).toBeNull();
  });
});

// The hopper's orientation has been changed twice -- ported from web as a
// vertical silo, then turned horizontal because a full-width silo rendered as
// a slab that outweighed the gauge. This pins the current decision so the next
// change is a deliberate one.
describe("the hopper", () => {
  it("fills horizontally, by width, not by height", async () => {
    const { getByTestId } = await render(<Dashboard />);
    const fill = flatStyle(getByTestId("hopper-fill").props.style);
    expect(typeof fill.width).toBe("string");
    expect(fill.width).toMatch(/%$/);
    expect(fill.height).toBe("100%");
  });

  it("keeps the track slim enough not to outweigh the gauge", async () => {
    const { getByTestId } = await render(<Dashboard />);
    const track = flatStyle(getByTestId("hopper-track").props.style);
    // 140 was the vertical silo's height; anything near it is a slab again.
    expect(track.height as number).toBeLessThanOrEqual(16);
  });
});
