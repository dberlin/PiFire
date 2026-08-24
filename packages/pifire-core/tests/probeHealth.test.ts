import type { ThermocoupleHealthView } from "../src/contracts/core.gen";
import { describe, expect, it } from "@rstest/core";
import {
  projectProbeHealth,
  projectProbeHealthList,
  summarizeProbeHealth,
} from "../src/dashboard/probeHealth";

const health = (
  over: Omit<Partial<ThermocoupleHealthView>, "report" | "detector" | "freshness"> & {
    report?: Partial<ThermocoupleHealthView["report"]>;
    detector?: Partial<ThermocoupleHealthView["detector"]>;
    freshness?: Partial<ThermocoupleHealthView["freshness"]>;
  } = {},
): ThermocoupleHealthView => ({
  device: "mcp9601",
  port: "TC0",
  label: "Grill",
  displayName: "Grill",
  role: "Primary",
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
  detector: {
    source: "software",
    policy: "observe",
    ...over.detector,
  },
  freshness: {
    current: true,
    lastReportedAgeS: 0,
    ...over.freshness,
  },
});
const pickPresentation = (input: ThermocoupleHealthView) => {
  const view = projectProbeHealth(input);
  return {
    severity: view.severity,
    availability: view.availability,
    headline: view.headline,
    impactCopy: view.impactCopy,
    priority: view.priority,
  };
};

describe("projectProbeHealth state/outcome copy matrix", () => {
  it.each([
    [
      "unmonitored",
      health({ report: { state: "unmonitored" }, detector: { policy: "off" } }),
      {
        severity: "quiet",
        availability: "current",
        headline: null,
        impactCopy: null,
        priority: 0,
      },
    ],
    [
      "healthy",
      health(),
      {
        severity: "quiet",
        availability: "current",
        headline: null,
        impactCopy: null,
        priority: 0,
      },
    ],
    [
      "suspected",
      health({ report: { state: "suspected" } }),
      {
        severity: "warning",
        availability: "current",
        headline: "CHECK PROBE",
        impactCopy: "Possible thermocouple issue; reading still available.",
        priority: 1,
      },
    ],
    [
      "confirmed primary notify-only",
      health({
        report: { state: "confirmed", faults: ["malfunction"] },
        outcome: "notify_only",
      }),
      {
        severity: "danger",
        availability: "current",
        headline: "FAULT",
        impactCopy: "Fault detected — Observe mode did not stop heating.",
        priority: 3,
      },
    ],
    [
      "confirmed primary stopped",
      health({
        report: { state: "confirmed", faults: ["open"], temperatureValid: false },
        outcome: "stopped",
      }),
      {
        severity: "danger",
        availability: "unavailable",
        headline: "CONTROL PROBE UNAVAILABLE",
        impactCopy: "PiFire stopped heating.",
        priority: 4,
      },
    ],
    [
      "confirmed Food unavailable",
      health({
        label: "Food1",
        displayName: "Brisket",
        role: "Food",
        report: { state: "confirmed", faults: ["short"], temperatureValid: false },
        outcome: "unavailable",
      }),
      {
        severity: "danger",
        availability: "unavailable",
        headline: "PROBE UNAVAILABLE",
        impactCopy: "Grill control continues.",
        priority: 2,
      },
    ],
    [
      "confirmed Aux unavailable",
      health({
        label: "Aux1",
        displayName: "Stack",
        role: "Aux",
        report: { state: "confirmed", faults: ["open"], temperatureValid: false },
        outcome: "unavailable",
      }),
      {
        severity: "danger",
        availability: "unavailable",
        headline: "PROBE UNAVAILABLE",
        impactCopy: "Grill control continues.",
        priority: 2,
      },
    ],
    [
      "confirmed without an operational outcome",
      health({
        report: { state: "confirmed", faults: ["open"], temperatureValid: false },
        outcome: "none",
      }),
      {
        severity: "danger",
        availability: "unavailable",
        headline: "FAULT",
        impactCopy: null,
        priority: 2,
      },
    ],
  ] as const)("projects %s", (_name, input, expected) => {
    expect(pickPresentation(input)).toEqual(expected);
  });

  it("projects recovery as quiet current state without retaining the earlier fault", () => {
    const confirmed = projectProbeHealth(
      health({
        report: { state: "confirmed", faults: ["open"], temperatureValid: false },
        outcome: "stopped",
      }),
    );
    const recovered = projectProbeHealth(health());

    expect(confirmed.severity).toBe("danger");
    expect(recovered).toMatchObject({
      severity: "quiet",
      availability: "current",
      headline: null,
      impactCopy: null,
      priority: 0,
    });
  });
});

describe("projectProbeHealth cause and source copy", () => {
  it.each([
    [["open"], "Hardware reported an open circuit."],
    [["short"], "Hardware reported a short circuit."],
    [
      ["short", "open"],
      "Hardware reported an open circuit. Hardware reported a short circuit.",
    ],
    [["malfunction"], "Software detected an abnormal thermocouple response."],
    [
      ["malfunction", "open"],
      "Hardware reported an open circuit. Software detected an abnormal thermocouple response.",
    ],
  ] as const)("preserves the confirmed causes for %j", (faults, expected) => {
    expect(
      projectProbeHealth(
        health({ report: { state: "confirmed", faults: [...faults] }, outcome: "notify_only" }),
      ).causeCopy,
    ).toBe(expected);
  });

  it("never turns suspected evidence into confirmed-fault language", () => {
    const view = projectProbeHealth(
      health({
        report: {
          state: "suspected",
          faults: ["open", "short", "malfunction"],
          evidence: ["hardware", "stuck-response"],
        },
        detector: { source: "mixed" },
      }),
    );

    expect(view.causeCopy).toBeNull();
    expect(`${view.headline} ${view.impactCopy}`).toBe(
      "CHECK PROBE Possible thermocouple issue; reading still available.",
    );
  });

  it.each([
    ["hardware", "Hardware"],
    ["software", "Software"],
    ["mixed", "Hardware + software"],
  ] as const)("gives %s reports stable details copy", (source, expected) => {
    expect(projectProbeHealth(health({ detector: { source } })).sourceCopy).toBe(expected);
  });
});

describe("projectProbeHealth freshness", () => {
  it("qualifies retained health as Last reported without changing health semantics", () => {
    const current = projectProbeHealth(
      health({ report: { state: "suspected" }, freshness: { current: true, lastReportedAgeS: 2 } }),
    );
    const stale = projectProbeHealth(
      health({
        report: { state: "suspected" },
        freshness: { current: false, lastReportedAgeS: 75 },
      }),
    );

    expect(current.freshnessQualifier).toBeNull();
    expect(stale.freshnessQualifier).toBe("Last reported");
    expect(stale).toMatchObject({
      severity: current.severity,
      availability: current.availability,
      headline: current.headline,
      impactCopy: current.impactCopy,
      lastReportedAgeS: 75,
    });
  });
});

describe("probe health aggregation", () => {
  it("orders multiple issues by operational impact and emits exact +N more copy", () => {
    const views = projectProbeHealthList([
      health({ report: { state: "suspected" }, label: "Food2", role: "Food" }),
      health({
        report: { state: "confirmed", temperatureValid: false },
        outcome: "unavailable",
        label: "Food1",
        role: "Food",
      }),
      health({ report: { state: "confirmed" }, outcome: "notify_only", label: "Pit2" }),
      health({
        report: { state: "confirmed", temperatureValid: false },
        outcome: "stopped",
        label: "Grill",
      }),
      health({ label: "Healthy", role: "Aux" }),
    ]);

    const summary = summarizeProbeHealth(views);
    expect(summary?.highest.label).toBe("Grill");
    expect(summary?.highest.priority).toBe(4);
    expect(summary?.additionalCount).toBe(3);
    expect(summary?.additionalCopy).toBe("+3 more");
  });

  it("uses configured order to break equal-priority ties", () => {
    const first = health({
      report: { state: "confirmed", temperatureValid: false },
      outcome: "unavailable",
      label: "Food2",
      role: "Food",
    });
    const second = health({
      report: { state: "confirmed", temperatureValid: false },
      outcome: "unavailable",
      label: "Food1",
      role: "Food",
    });

    expect(summarizeProbeHealth(projectProbeHealthList([first, second]))?.highest.label).toBe(
      "Food2",
    );
  });

  it("has no dashboard summary for healthy and unmonitored probes", () => {
    expect(
      summarizeProbeHealth(
        projectProbeHealthList([
          health(),
          health({ report: { state: "unmonitored" }, detector: { policy: "off" } }),
        ]),
      ),
    ).toBeNull();
  });
});

describe("projectProbeHealth immutability", () => {
  it("returns frozen values without mutating the wire projection", () => {
    const input = health({
      report: { state: "confirmed", faults: ["short", "open"] },
      outcome: "notify_only",
    });
    const originalFaults = [...input.report.faults];
    const view = projectProbeHealth(input);
    const list = projectProbeHealthList([input]);

    expect(input.report.faults).toEqual(originalFaults);
    expect(view.faults).toEqual(["open", "short"]);
    expect(Object.isFrozen(view)).toBe(true);
    expect(Object.isFrozen(view.faults)).toBe(true);
    expect(Object.isFrozen(list)).toBe(true);
  });
});
