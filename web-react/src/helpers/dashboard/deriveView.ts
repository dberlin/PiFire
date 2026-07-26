import type { LiveState } from "../types";
import {
  type BatteryBadge,
  batteryBadge,
  type ConnectionBadge,
  connectionBadge,
} from "./probeStatus";

// Pure presentation logic for the PiFire Dashboard (port of the design's
// renderVals(), but driven by the REAL socket_dash_data contract instead of the
// design's internal simulator). Kept side-effect free so it's unit-testable —
// every color / label / geometry input the widgets need comes out of here.

// One constant per semantic, all of them Theme.qml tokens. The lighter
// companions (OK2/AMBER2/DANGER2) that used to sit beside these were a
// React-only ramp with no Qt counterpart; the 2026-07-26 ruling replaced them
// with the tokens themselves.
const OK = "var(--ok)";
const AMBER = "var(--warn)";
const DANGER = "var(--danger)";
const IDLE = "var(--icon-idle)";
const GRAY = "var(--dot-idle)";
const YELLOW = "var(--cooking)";
const DIM = "var(--label)";
const SURFACE = "var(--card)";
const EDGE = "var(--card-border)";

// Modes where a cook is actively running (drives the live dot + gauge glow +
// cook-time counter). Everything else (Monitor / Shutdown / Stop / Error / "")
// is treated as not-cooking.
const COOKING_MODES = new Set(["Startup", "Smoke", "Hold", "Prime", "Reheat"]);

// Modes in which Flask shows #pmode_group, its P-Mode control
// (dash_default.js:248-293). Notably NOT Hold: the PID owns the cycle there, so
// the P-Mode value is displayed but not adjustable. The badge itself is shown
// in every mode; only the control comes and goes.
const PMODE_EDITABLE_MODES = new Set(["Prime", "Shutdown", "Startup", "Reignite", "Smoke"]);

// A soft accent tint that still tracks the active [data-accent] theme.
const accentMix = (pct: number) => `color-mix(in srgb, var(--accent) ${pct}%, transparent)`;

export interface ProbeCardView {
  name: string;
  /** The probe's `label`, not its display title: every notify write is
   *  addressed by label (common/api_commands.py:441-449), and the title is a
   *  free-text name the user can change. */
  label: string;
  tempInt: number;
  unit: "F" | "C";
  targetStr: string;
  tgtColor: string;
  barPct: number;
  barColor: string;
  /** Whether a TARGET notification is armed on this probe. */
  notifyOn: boolean;
  /** Formatted time-to-target, or null when there is nothing to show. */
  etaStr: string | null;
  /** Bluetooth link state, or null for a probe that has no such concept. */
  conn: ConnectionBadge | null;
  /** Battery level, or null for a probe that has no battery. */
  battery: BatteryBadge | null;
}

export interface OutputView {
  on: boolean;
  color: string;
  status: string;
  dot: string;
  edge: string;
}

export interface PillView {
  label: string;
  value: string;
  valColor: string;
  bg: string;
  border: string;
  labelColor: string;
}

export interface HopperView {
  pct: number;
  /** The level colour. The hopper fill's gradient derives its own lighter stop
   *  from this in CSS, so there is no second colour to carry. */
  color: string;
  label: string;
  labelColor: string;
}

export interface DashView {
  cooking: boolean;
  modeLabel: string;
  liveColor: string;
  units: "F" | "C";
  tempInt: number;
  maxTemp: number;
  gaugeFrac: number;
  hasSetpoint: boolean;
  setpointInt: number;
  hasProbes: boolean;
  probes: ProbeCardView[];
  fan: OutputView;
  auger: OutputView;
  igniter: OutputView;
  pillL: PillView;
  pillR: PillView;
  hopper: HopperView;
  lidOpen: boolean;
  /** Whether the P-MODE pill is a control right now, or a read-only readout. */
  pModeEditable: boolean;
}

function outputView(on: boolean, onColor: string, onStatus: string): OutputView {
  return {
    on,
    color: on ? onColor : IDLE,
    status: on ? onStatus : "IDLE",
    dot: on ? OK : GRAY,
    edge: on ? `color-mix(in srgb, ${onColor} 35%, transparent)` : EDGE,
  };
}

function probeCard(fp: LiveState["foodProbes"][number], units: "F" | "C"): ProbeCardView {
  const hasTarget = fp.target > 0 && fp.targetReq;
  const done = hasTarget && fp.temp >= fp.target - 1;
  return {
    name: fp.title,
    label: fp.label,
    tempInt: Math.round(fp.temp),
    unit: units,
    targetStr: hasTarget ? `→ ${Math.round(fp.target)}°` : "AMBIENT",
    tgtColor: hasTarget ? (done ? OK : YELLOW) : DIM,
    barPct: hasTarget ? Math.max(2, Math.min(100, (fp.temp / fp.target) * 100)) : 0,
    barColor: done ? OK : "var(--accent)",
    // targetReq, NOT hasNotifications: the latter is also true when only a
    // high/low LIMIT alert is armed (blueprints/mobile/socket_io.py:832-848),
    // which the bell on this card does not control.
    notifyOn: fp.targetReq,
    // The backend recomputes eta each control pass for armed target entries and
    // writes back seconds or None (notify/notifications.py:81-99); the wire type
    // also allows a string. Matches the Flask ETA button, which is rendered only
    // while the notification is requested (_macro_dash_default.html:123-131).
    etaStr: fp.targetReq && typeof fp.eta === "number" ? fmtDuration(fp.eta) : null,
    conn: connectionBadge(fp.status),
    battery: batteryBadge(fp.status),
  };
}

function hopperView(level: number): HopperView {
  const pct = Math.max(0, Math.min(100, Math.round(level)));
  const low = pct < 15;
  const mid = pct < 35;
  return {
    pct,
    color: low ? DANGER : mid ? AMBER : OK,
    label: low ? "REFILL PELLETS" : mid ? "RUNNING LOW" : "LEVEL OK",
    labelColor: low ? DANGER : mid ? AMBER : DIM,
  };
}

export function deriveView(dash: LiveState): DashView {
  const mode = dash.currentMode || "Stop";
  const cooking = COOKING_MODES.has(mode);
  const units = dash.tempUnits;
  const p = dash.primaryProbe;
  const maxTemp = p.maxTemp > 0 ? p.maxTemp : 600;

  const probes = (dash.foodProbes ?? []).map((fp) => probeCard(fp, units));

  const smokeOn = dash.smokePlus;
  const pillL: PillView = {
    label: "P-MODE",
    value: `P-${dash.pMode}`,
    valColor: "var(--row-label)",
    bg: SURFACE,
    border: EDGE,
    labelColor: DIM,
  };
  const pillR: PillView = smokeOn
    ? {
        label: "SMOKE+",
        value: "ON",
        valColor: OK,
        bg: "color-mix(in srgb, var(--ok) 14%, transparent)",
        border: OK,
        labelColor: OK,
      }
    : { label: "SMOKE+", value: "OFF", valColor: DIM, bg: SURFACE, border: EDGE, labelColor: DIM };

  const igniter = outputView(dash.outputs.igniter, "var(--igniter)", "HOT");

  return {
    cooking,
    modeLabel: mode.toUpperCase(),
    liveColor: cooking ? OK : DIM,
    units,
    tempInt: Math.round(p.temp),
    maxTemp,
    gaugeFrac: Math.max(0, Math.min(1, p.temp / maxTemp)),
    hasSetpoint: p.setTemp > 0,
    setpointInt: Math.round(p.setTemp),
    hasProbes: probes.length > 0,
    probes,
    fan: outputView(dash.outputs.fan, "var(--accent)", "RUNNING"),
    auger: outputView(dash.outputs.auger, "var(--accent)", "FEEDING"),
    // igniter uses its fixed ember dot (not the shared green) when hot.
    igniter: { ...igniter, dot: igniter.on ? "var(--igniter)" : GRAY },
    pillL,
    pillR,
    hopper: hopperView(dash.hopperLevel),
    lidOpen: dash.lidOpenDetected,
    // A recipe drives the mode itself, and Flask hides the whole control panel
    // during one -- so the pill stays read-only however the sub-mode reads.
    pModeEditable: !dash.recipeStatus?.recipeMode && PMODE_EDITABLE_MODES.has(mode),
  };
}

// Auger stroke tint (accent-following) used by the auger icon when running.
export const augerRunStroke = accentMix(60);

export function fmtDuration(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
