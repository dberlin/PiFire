import type {
  DashSocketPayload,
  ProbeDataPayload,
  ThermocoupleHealthView,
} from "../src/contracts/core.gen";
import { describe, expect, it } from "@rstest/core";
import { deriveView, staleLabel } from "../src/dashboard/deriveView";
import { FIXTURE_DASH } from "../src/fixture";

// probeCard()'s existing fields (targetStr / tgtColor / barPct / barColor) are
// covered through components/dashboard/ProbeCard.test.tsx, which renders them.
// These cases cover the fields the card cannot show by itself: the write
// identity and the ETA readout.
const card = (over: Partial<ProbeDataPayload>) =>
  deriveView({ ...FIXTURE_DASH, foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], ...over }] })
    .probes[0];

describe("probeCard identity", () => {
  // `label` is the key every notify write is addressed by
  // (common/api_commands.py:550-551) -- distinct from `name`, the free-text
  // display title, which cannot identify which probe a write is for.
  it("carries the probe's label, distinct from its display title", () => {
    const v = card({ title: "Brisket", label: "Probe1" });
    expect(v.label).toBe("Probe1");
    expect(v.name).toBe("Brisket");
  });
});

// A probe device may have no reading to give: a network-polled one returns
// None for a channel whose cache went stale, and it reaches the wire as null.
// Math.round(null) is 0, so treating a null reading as a temperature would
// render a confident zero -- a plausible-looking value that reads as data
// rather than as absence.
describe("probeCard with no current reading", () => {
  const withLast = (over: Partial<ProbeDataPayload["status"]>) =>
    card({ temp: null, status: { ...FIXTURE_DASH.foodProbes[0].status, ...over } });

  it("shows the last real reading rather than a zero", () => {
    const v = withLast({ lastTemp: 147, lastReadingAge: 47 });
    expect(v.tempInt).toBe(147);
    expect(v.stale).toBe("last data 47s ago");
  });

  it("shows nothing at all for a probe that has never reported", () => {
    const v = card({ temp: null, status: FIXTURE_DASH.foodProbes[0].status });
    expect(v.tempInt).toBeNull();
    expect(v.stale).toBeNull();
  });

  it("is not marked stale while the probe is reporting", () => {
    // lastTemp disagrees with temp on purpose: a live reading must win, so a
    // stale marker here would mean the branch was chosen on the wrong field.
    const v = card({ temp: 210, status: { lastTemp: 147, lastReadingAge: 47 } });
    expect(v.tempInt).toBe(210);
    expect(v.stale).toBeNull();
  });

  it("measures progress against the carried reading, not against zero", () => {
    const v = card({
      temp: null,
      target: 203,
      targetReq: true,
      status: { lastTemp: 202, lastReadingAge: 12 },
    });
    expect(v.barPct).toBeGreaterThan(90);
  });

  it("draws no progress for a probe with nothing to show", () => {
    const v = card({ temp: null, target: 203, targetReq: true, status: {} });
    expect(v.barPct).toBe(0);
  });
});

describe("staleLabel", () => {
  // Worded and bucketed identically in display/staleness.py; the two are
  // pinned separately against this same table.
  it("counts seconds, then minutes, then hours", () => {
    expect(staleLabel(0)).toBe("last data 0s ago");
    expect(staleLabel(47)).toBe("last data 47s ago");
    expect(staleLabel(59)).toBe("last data 59s ago");
    expect(staleLabel(60)).toBe("last data 1m ago");
    expect(staleLabel(3599)).toBe("last data 59m ago");
    expect(staleLabel(3600)).toBe("last data 1h ago");
    expect(staleLabel(7260)).toBe("last data 2h ago");
  });
});

describe("probeCard notifyOn", () => {
  // hasNotifications, NOT targetReq. blueprints/mobile/socket_io.py:877-892
  // sets it when ANY of the probe's three notify entries is armed, and the
  // bell opens a modal that edits all three -- so gating on targetReq alone
  // would show a struck-through bell for a probe carrying only a high-limit
  // alert, even though it has a real notification armed.
  it("is set by an armed target", () => {
    expect(card({ targetReq: true, hasNotifications: true, target: 203 }).notifyOn).toBe(true);
    expect(card({ targetReq: false, hasNotifications: false, target: 203 }).notifyOn).toBe(false);
  });

  it("is set by a limit alert with no target armed", () => {
    expect(card({ targetReq: false, hasNotifications: true, highLimitReq: true }).notifyOn).toBe(
      true,
    );
  });
});

describe("probeCard etaStr", () => {
  // The Flask ETA button is rendered only while the probe notification is
  // requested (_macro_dash_default.html:123-131) and shows a spinner until the
  // backend has computed one (dash_default.js:632-636); null here is that
  // "nothing to show" state.
  it("formats a numeric eta while the notification is armed", () => {
    expect(card({ targetReq: true, target: 203, eta: 3661 }).etaStr).toBe("1:01:01");
    expect(card({ targetReq: true, target: 203, eta: 65 }).etaStr).toBe("01:05");
  });

  it("is null when the notification is not armed", () => {
    expect(card({ targetReq: false, target: 203, eta: 3661 }).etaStr).toBeNull();
  });

  it("is null when the backend has no eta yet", () => {
    expect(card({ targetReq: true, target: 203, eta: null }).etaStr).toBeNull();
  });

  // types.ts:17 types eta as `number | string | null` because the real capture
  // has been seen carrying a string; only a number is formattable.
  it("is null for a non-numeric eta", () => {
    expect(card({ targetReq: true, target: 203, eta: "--" }).etaStr).toBeNull();
  });
});

// The P-Mode pill is a control only where it is shown at all, which is Smoke.
// Flask offered the control in five modes (dash_default.js:248-293), but it
// also showed the badge in all of them; here the badge is Smoke-only, and the
// left pill reads AUGER DUTY everywhere else -- a pill that must never open a
// P-Mode picker.
describe("deriveView.pModeEditable", () => {
  const at = (over: Partial<DashSocketPayload>): DashSocketPayload => ({
    ...FIXTURE_DASH,
    ...over,
  });

  it("is editable in Smoke", () => {
    expect(deriveView(at({ currentMode: "Smoke" })).pModeEditable).toBe(true);
  });

  it("is NOT editable in Hold, where the PID owns the cycle", () => {
    expect(deriveView(at({ currentMode: "Hold" })).pModeEditable).toBe(false);
  });

  it("is NOT editable wherever the left pill is the auger duty", () => {
    for (const mode of [
      "Prime",
      "Shutdown",
      "Startup",
      "Reignite",
      "Stop",
      "Monitor",
      "Manual",
      "Error",
      "",
    ]) {
      const view = deriveView(at({ currentMode: mode }));
      expect(view.pillL.label, mode).toBe("AUGER DUTY");
      expect(view.pModeEditable, mode).toBe(false);
    }
  });

  it("is NOT editable during a recipe, whatever the sub-mode reads", () => {
    expect(
      deriveView(
        at({
          currentMode: "Smoke",
          recipeStatus: { ...FIXTURE_DASH.recipeStatus, recipeMode: true },
        }),
      ).pModeEditable,
    ).toBe(false);
  });
});

// The two pills under the system card. P-mode and Smoke+ describe the smoke
// cycle, so they appear only in Smoke; in every other mode they reported
// settings that governed nothing running, and the pills carry the actuator
// duties instead. The attached display makes the same swap on the same
// condition (display/qml/screens/DashScreen.qml).
describe("duty pills", () => {
  const at = (over: Partial<DashSocketPayload>): DashSocketPayload => ({
    ...FIXTURE_DASH,
    ...over,
  });

  it("shows P-mode and Smoke+ in Smoke, and only there", () => {
    const view = deriveView(at({ currentMode: "Smoke", pMode: 3, smokePlus: true }));
    expect(view.pillL.label).toBe("P-MODE");
    expect(view.pillL.value).toBe("P-3");
    expect(view.pillR.label).toBe("SMOKE+");
    expect(view.pillR.value).toBe("ON");
  });

  it("reports Smoke+ off in Smoke when it is off", () => {
    const view = deriveView(at({ currentMode: "Smoke", smokePlus: false }));
    expect(view.pillR.value).toBe("OFF");
  });

  it("shows the actuator duties in every mode but Smoke", () => {
    // Smoke+ is deliberately left ON here: outside Smoke it must not reach the
    // pill at all, so a passing run proves the mode gate and not a false value.
    for (const mode of [
      "Hold",
      "Startup",
      "Stop",
      "Shutdown",
      "Prime",
      "Reignite",
      "Monitor",
      "Manual",
      "Error",
      "",
    ]) {
      const view = deriveView(
        at({ currentMode: mode, cycleRatio: 0.42, fanDuty: 65, pMode: 3, smokePlus: true }),
      );
      expect(view.pillL.label, mode).toBe("AUGER DUTY");
      expect(view.pillL.value, mode).toBe("42%");
      expect(view.pillR.label, mode).toBe("FAN DUTY");
      expect(view.pillR.value, mode).toBe("65%");
    }
  });

  it("rounds the auger's cycle share to whole percent", () => {
    const view = deriveView(at({ currentMode: "Hold", cycleRatio: 0.335, fanDuty: 0 }));
    expect(view.pillL.value).toBe("34%");
    expect(view.pillR.value).toBe("0%");
  });

  it("survives a payload from a backend too old to send the duties", () => {
    const { cycleRatio, fanDuty, ...older } = at({ currentMode: "Hold" });
    void cycleRatio;
    void fanDuty;
    const view = deriveView(older as DashSocketPayload);
    expect(view.pillL.value).toBe("0%");
    expect(view.pillR.value).toBe("0%");
  });
});

const wireHealth = (
  over: Omit<Partial<ThermocoupleHealthView>, "report" | "freshness"> & {
    report?: Partial<ThermocoupleHealthView["report"]>;
    freshness?: Partial<ThermocoupleHealthView["freshness"]>;
  } = {},
): ThermocoupleHealthView => ({
  device: "mcp9601",
  port: "TC0",
  label: "Grill",
  displayName: "Grill",
  role: "Primary",
  detector: { source: "software", policy: "observe" },
  outcome: "none",
  ...over,
  report: {
    state: "healthy",
    faults: [],
    evidence: [],
    temperatureValid: true,
    detail: {},
    ...over.report,
  },
  freshness: { current: true, lastReportedAgeS: 0, ...over.freshness },
});

describe("deriveView thermocouple health integration", () => {
  it("keeps old payloads compatible when the optional projection is absent", () => {
    const { thermocoupleHealth, ...older } = FIXTURE_DASH;
    void thermocoupleHealth;

    const view = deriveView(older as DashSocketPayload);

    expect(view.probeHealth).toEqual([]);
    expect(view.probeHealthSummary).toBeNull();
    expect(view.primaryHealth).toBeNull();
  });

  it("projects all roles and keeps Aux available to summary/details without making a card", () => {
    const food = wireHealth({
      port: "TC1",
      label: FIXTURE_DASH.foodProbes[0].label,
      displayName: FIXTURE_DASH.foodProbes[0].title,
      role: "Food",
      report: { state: "suspected" },
    });
    const aux = wireHealth({
      port: "TC2",
      label: "Stack",
      displayName: "Stack",
      role: "Aux",
      report: { state: "confirmed", temperatureValid: false },
      outcome: "unavailable",
    });
    const view = deriveView({ ...FIXTURE_DASH, thermocoupleHealth: [food, aux] });

    expect(view.probeHealth.map(({ role }) => role)).toEqual(["Food", "Aux"]);
    expect(view.probes).toHaveLength(FIXTURE_DASH.foodProbes.length);
    expect(view.probes[0].health?.headline).toBe("CHECK PROBE");
    expect(view.probeHealthSummary?.highest.label).toBe("Stack");
  });

  it("never falls back to last-good for a confirmed-invalid primary", () => {
    const view = deriveView({
      ...FIXTURE_DASH,
      primaryProbe: {
        ...FIXTURE_DASH.primaryProbe,
        temp: null,
        status: { lastTemp: 225, lastReadingAge: 12 },
      },
      thermocoupleHealth: [
        wireHealth({
          report: { state: "confirmed", faults: ["open"], temperatureValid: false },
          outcome: "stopped",
        }),
      ],
    });

    expect(view.primaryHealth?.availability).toBe("unavailable");
    expect(view.tempInt).toBeNull();
    expect(view.stale).toBeNull();
    expect(view.gaugeFrac).toBe(0);
  });

  it("never falls back to last-good for a confirmed-invalid Food probe", () => {
    const label = FIXTURE_DASH.foodProbes[0].label;
    const view = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [
        {
          ...FIXTURE_DASH.foodProbes[0],
          temp: null,
          status: { lastTemp: 155, lastReadingAge: 18 },
        },
      ],
      thermocoupleHealth: [
        wireHealth({
          port: "TC1",
          label,
          displayName: "Brisket",
          role: "Food",
          report: { state: "confirmed", faults: ["short"], temperatureValid: false },
          outcome: "unavailable",
        }),
      ],
    });

    expect(view.probes[0].health?.availability).toBe("unavailable");
    expect(view.probes[0].tempInt).toBeNull();
    expect(view.probes[0].stale).toBeNull();
    expect(view.probes[0].barPct).toBe(0);
  });

  it.each([
    ["suspected", "none"],
    ["confirmed", "notify_only"],
  ] as const)("retains the current primary number for %s/%s", (state, outcome) => {
    const view = deriveView({
      ...FIXTURE_DASH,
      primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 227.4 },
      thermocoupleHealth: [
        wireHealth({
          report: {
            state,
            faults: state === "confirmed" ? ["malfunction"] : [],
            temperatureValid: true,
          },
          outcome,
        }),
      ],
    });

    expect(view.primaryHealth?.availability).toBe("current");
    expect(view.tempInt).toBe(227);
    expect(view.stale).toBeNull();
  });

  it("keeps health freshness orthogonal to numeric availability", () => {
    const view = deriveView({
      ...FIXTURE_DASH,
      primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 226 },
      thermocoupleHealth: [
        wireHealth({
          report: { state: "suspected", temperatureValid: true },
          freshness: { current: false, lastReportedAgeS: 70 },
        }),
      ],
    });

    expect(view.primaryHealth).toMatchObject({
      availability: "current",
      freshnessQualifier: "Last reported",
    });
    expect(view.tempInt).toBe(226);
    expect(view.stale).toBeNull();
  });

  it("matches validity by role and label instead of applying an Aux fault to a Food card", () => {
    const label = FIXTURE_DASH.foodProbes[0].label;
    const view = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [
        {
          ...FIXTURE_DASH.foodProbes[0],
          temp: null,
          status: { lastTemp: 160, lastReadingAge: 9 },
        },
      ],
      thermocoupleHealth: [
        wireHealth({
          label,
          displayName: "Stack",
          role: "Aux",
          report: { state: "confirmed", temperatureValid: false },
          outcome: "unavailable",
        }),
      ],
    });

    expect(view.probes[0].health).toBeNull();
    expect(view.probes[0].tempInt).toBe(160);
    expect(view.probes[0].stale).toBe("last data 9s ago");
  });
});
