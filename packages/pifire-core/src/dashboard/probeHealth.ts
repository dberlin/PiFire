import type { ThermocoupleHealthView } from "../contracts/core.gen";

export type ProbeHealthSeverity = "quiet" | "info" | "warning" | "danger";
export type ProbeHealthAvailability = "current" | "unavailable";
export type ProbeHealthPriority = 0 | 1 | 2 | 3 | 4;
export type ProbeHealthFreshnessQualifier = "Last reported" | null;

type ProbeHealthState = ThermocoupleHealthView["report"]["state"];
type ProbeHealthFault = ThermocoupleHealthView["report"]["faults"][number];
type ProbeHealthEvidence = ThermocoupleHealthView["report"]["evidence"][number];
type ProbeHealthSource = ThermocoupleHealthView["detector"]["source"];
type ProbeHealthPolicy = ThermocoupleHealthView["detector"]["policy"];
type ProbeHealthOutcome = ThermocoupleHealthView["outcome"];
type ProbeHealthRole = ThermocoupleHealthView["role"];

export interface ProbeHealthView {
  readonly device: string;
  readonly port: string;
  readonly label: string;
  readonly displayName: string;
  readonly role: ProbeHealthRole;
  readonly state: ProbeHealthState;
  readonly faults: readonly ProbeHealthFault[];
  readonly evidence: readonly ProbeHealthEvidence[];
  readonly temperatureValid: boolean;
  readonly source: ProbeHealthSource;
  readonly policy: ProbeHealthPolicy;
  readonly outcome: ProbeHealthOutcome;
  readonly severity: ProbeHealthSeverity;
  readonly availability: ProbeHealthAvailability;
  readonly headline: string | null;
  readonly impactCopy: string | null;
  readonly causeCopy: string | null;
  readonly sourceCopy: string;
  readonly priority: ProbeHealthPriority;
  readonly freshnessCurrent: boolean;
  readonly lastReportedAgeS: number;
  readonly freshnessQualifier: ProbeHealthFreshnessQualifier;
}

export interface ProbeHealthSummary {
  readonly highest: ProbeHealthView;
  readonly additionalCount: number;
  readonly additionalCopy: string | null;
}

interface Presentation {
  readonly severity: ProbeHealthSeverity;
  readonly headline: string | null;
  readonly impactCopy: string | null;
  readonly priority: ProbeHealthPriority;
}

const QUIET: Presentation = Object.freeze({
  severity: "quiet",
  headline: null,
  impactCopy: null,
  priority: 0,
});

const SUSPECTED: Presentation = Object.freeze({
  severity: "warning",
  headline: "CHECK PROBE",
  impactCopy: "Possible thermocouple issue; reading still available.",
  priority: 1,
});

const CONFIRMED_UNAVAILABLE: Presentation = Object.freeze({
  severity: "danger",
  headline: "PROBE UNAVAILABLE",
  impactCopy: "Grill control continues.",
  priority: 2,
});

const CONFIRMED_WITHOUT_OUTCOME: Presentation = Object.freeze({
  severity: "danger",
  headline: "FAULT",
  impactCopy: null,
  priority: 2,
});

const CONFIRMED_NOTIFY_ONLY: Presentation = Object.freeze({
  severity: "danger",
  headline: "FAULT",
  impactCopy: "Fault detected — Observe mode did not stop heating.",
  priority: 3,
});

const CONFIRMED_STOPPED: Presentation = Object.freeze({
  severity: "danger",
  headline: "CONTROL PROBE UNAVAILABLE",
  impactCopy: "PiFire stopped heating.",
  priority: 4,
});

const SOURCE_COPY: Readonly<Record<ProbeHealthSource, string>> = Object.freeze({
  hardware: "Hardware",
  software: "Software",
  mixed: "Hardware + software",
});

const FAULT_ORDER: readonly ProbeHealthFault[] = Object.freeze([
  "open",
  "short",
  "malfunction",
]);

function presentationFor(input: ThermocoupleHealthView): Presentation {
  switch (input.report.state) {
    case "unmonitored":
    case "healthy":
      return QUIET;
    case "suspected":
      return SUSPECTED;
    case "confirmed":
      switch (input.outcome) {
        case "stopped":
          return CONFIRMED_STOPPED;
        case "notify_only":
          return CONFIRMED_NOTIFY_ONLY;
        case "unavailable":
          return CONFIRMED_UNAVAILABLE;
        case "none":
          return CONFIRMED_WITHOUT_OUTCOME;
      }
  }
}

function canonicalFaults(input: readonly ProbeHealthFault[]): readonly ProbeHealthFault[] {
  const present = new Set(input);
  return Object.freeze(FAULT_ORDER.filter((fault) => present.has(fault)));
}

function causeCopy(state: ProbeHealthState, faults: readonly ProbeHealthFault[]): string | null {
  if (state !== "confirmed") return null;

  const causes: string[] = [];
  if (faults.includes("open")) causes.push("Hardware reported an open circuit.");
  if (faults.includes("short")) causes.push("Hardware reported a short circuit.");
  if (faults.includes("malfunction")) {
    causes.push("Software detected an abnormal thermocouple response.");
  }
  return causes.length > 0 ? causes.join(" ") : null;
}

/**
 * Converts one authoritative wire health item into framework-free presentation
 * semantics. The function has no history: a later healthy item is quiet
 * immediately, while stale transport is represented only by its qualifier.
 */
export function projectProbeHealth(input: ThermocoupleHealthView): ProbeHealthView {
  const presentation = presentationFor(input);
  const faults = canonicalFaults(input.report.faults);
  const evidence = Object.freeze([...input.report.evidence]);
  const unavailable =
    input.outcome === "stopped" ||
    input.outcome === "unavailable" ||
    (input.report.state === "confirmed" && !input.report.temperatureValid);

  return Object.freeze({
    device: input.device,
    port: input.port,
    label: input.label,
    displayName: input.displayName,
    role: input.role,
    state: input.report.state,
    faults,
    evidence,
    temperatureValid: input.report.temperatureValid,
    source: input.detector.source,
    policy: input.detector.policy,
    outcome: input.outcome,
    severity: presentation.severity,
    availability: unavailable ? "unavailable" : "current",
    headline: presentation.headline,
    impactCopy: presentation.impactCopy,
    causeCopy: causeCopy(input.report.state, faults),
    sourceCopy: SOURCE_COPY[input.detector.source],
    priority: presentation.priority,
    freshnessCurrent: input.freshness.current,
    lastReportedAgeS: input.freshness.lastReportedAgeS,
    freshnessQualifier: input.freshness.current ? null : "Last reported",
  });
}

export function projectProbeHealthList(
  input: readonly ThermocoupleHealthView[] | undefined,
): readonly ProbeHealthView[] {
  return Object.freeze((input ?? []).map(projectProbeHealth));
}

/** Returns the highest active issue, preserving producer order for ties. */
export function summarizeProbeHealth(
  views: readonly ProbeHealthView[],
): ProbeHealthSummary | null {
  let highest: ProbeHealthView | null = null;
  let issueCount = 0;

  for (const view of views) {
    if (view.priority === 0) continue;
    issueCount += 1;
    if (highest === null || view.priority > highest.priority) highest = view;
  }

  if (highest === null) return null;
  const additionalCount = issueCount - 1;
  return Object.freeze({
    highest,
    additionalCount,
    additionalCopy: additionalCount > 0 ? `+${additionalCount} more` : null,
  });
}
