import type { DashSocketPayload, ProbeStatusPayload } from "../contracts/core.gen";
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

// A soft accent tint that still tracks the active [data-accent] theme.
const accentMix = (pct: number) => `color-mix(in srgb, var(--accent) ${pct}%, transparent)`;

/** How a stale reading says so. Worded and bucketed exactly as the attached
 *  display words it (display/staleness.py), so an operator moving between the
 *  panel and their phone reads one story about the same probe. */
export function staleLabel(seconds: number): string {
  if (seconds < 60) return `last data ${seconds}s ago`;
  if (seconds < 3600) return `last data ${Math.floor(seconds / 60)}m ago`;
  return `last data ${Math.floor(seconds / 3600)}h ago`;
}

/** What a card shows for a probe, and whether that number is live.
 *
 *  A probe with no current reading keeps showing its last real one -- 40 s of
 *  age is still worth something to someone deciding whether to open the lid --
 *  but it must not read as live, so it carries the age with it. `shown` is
 *  null only for a probe that has produced nothing at all. */
export function reading(
  temp: number | null,
  status: ProbeStatusPayload,
): { shown: number | null; stale: string | null } {
  if (temp !== null) return { shown: temp, stale: null };
  const last = status.lastTemp;
  if (typeof last !== "number") return { shown: null, stale: null };
  return {
    shown: last,
    stale: staleLabel(typeof status.lastReadingAge === "number" ? status.lastReadingAge : 0),
  };
}

export interface ProbeCardView {
  name: string;
  /** The probe's `label`, not its display title: every notify write is
   *  addressed by label (common/api_commands.py:441-449), and the title is a
   *  free-text name the user can change. */
  label: string;
  /** The temperature to display, which is the last real reading when the probe
   *  has no current one. Null only when it has produced nothing at all. */
  tempInt: number | null;
  /** Set when `tempInt` is a carried-over reading rather than a live one, e.g.
   *  "last data 47s ago". Null while the probe is reporting. */
  stale: string | null;
  unit: "F" | "C";
  targetStr: string;
  tgtColor: string;
  barPct: number;
  barColor: string;
  /** Whether ANY notification -- target, high limit or low limit -- is armed on
   *  this probe. */
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
  /** The pit temperature to display -- the last real reading when there is no
   *  current one, null when the probe has produced nothing at all. */
  tempInt: number | null;
  /** Set when `tempInt` is a carried-over reading rather than a live one. */
  stale: string | null;
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
  /** Whether the SMOKE+ pill toggles Smoke+ right now. Same gate as the P-MODE
   *  pill: outside Smoke that pill reads FAN DUTY, which toggles nothing. */
  smokePlusEditable: boolean;
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

function probeCard(fp: DashSocketPayload["foodProbes"][number], units: "F" | "C"): ProbeCardView {
  const hasTarget = fp.target > 0 && fp.targetReq;
  const { shown, stale } = reading(fp.temp, fp.status);
  // Progress is measured against whatever the card is showing: a stale reading
  // still places the probe on its way to target, and a probe with no reading
  // at all has no progress to draw.
  const done = hasTarget && shown !== null && shown >= fp.target - 1;
  return {
    name: fp.title,
    label: fp.label,
    tempInt: shown === null ? null : Math.round(shown),
    stale,
    unit: units,
    targetStr: hasTarget ? `→ ${Math.round(fp.target)}°` : "AMBIENT",
    tgtColor: hasTarget ? (done ? OK : YELLOW) : DIM,
    barPct: hasTarget && shown !== null ? Math.max(2, Math.min(100, (shown / fp.target) * 100)) : 0,
    barColor: done ? OK : "var(--accent)",
    // hasNotifications, NOT targetReq: the backend sets it when ANY of the
    // probe's three notify entries is armed (blueprints/mobile/socket_io.py:
    // 770-795), and the bell this feeds opens a modal that edits all three. A
    // probe carrying only a high/low LIMIT alert would otherwise show a
    // struck-through bell for a notification it really had.
    notifyOn: fp.hasNotifications,
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

export function deriveView(dash: DashSocketPayload): DashView {
  const mode = dash.currentMode || "Stop";
  const cooking = COOKING_MODES.has(mode);
  const units = dash.tempUnits;
  const p = dash.primaryProbe;
  const maxTemp = p.maxTemp > 0 ? p.maxTemp : 600;

  const probes = (dash.foodProbes ?? []).map((fp) => probeCard(fp, units));

  const smokeOn = dash.smokePlus;
  // P-mode and Smoke+ describe the smoke cycle, so they are shown only where
  // that cycle is what the grill is doing. Everywhere else the pair reported
  // settings that governed nothing running -- a P-number in Stop describes a
  // cycle that is not turning. The two pills carry the actuator duties instead,
  // which are true in every mode. P-mode stays reachable off the dashboard:
  // Settings > Work Mode here, the PMode menu on the attached display.
  const smoking = mode === "Smoke";
  const neutral = { valColor: "var(--row-label)", bg: SURFACE, border: EDGE, labelColor: DIM };
  const pillL: PillView = smoking
    ? { label: "P-MODE", value: `P-${dash.pMode}`, ...neutral }
    : { label: "AUGER DUTY", value: `${Math.round((dash.cycleRatio ?? 0) * 100)}%`, ...neutral };
  const pillR: PillView = !smoking
    ? { label: "FAN DUTY", value: `${Math.round(dash.fanDuty ?? 0)}%`, ...neutral }
    : smokeOn
      ? {
          label: "SMOKE+",
          value: "ON",
          valColor: OK,
          bg: "color-mix(in srgb, var(--ok) 14%, transparent)",
          border: OK,
          labelColor: OK,
        }
      : {
          label: "SMOKE+",
          value: "OFF",
          valColor: DIM,
          bg: SURFACE,
          border: EDGE,
          labelColor: DIM,
        };

  const igniter = outputView(dash.outputs.igniter, "var(--igniter)", "HOT");

  // The pit probe is usually a wired thermocouple that always has a number,
  // but nothing guarantees it: the same carry-over applies here so a gauge
  // never reads 0° for a probe that simply did not report.
  const primary = reading(p.temp, p.status);

  return {
    cooking,
    modeLabel: mode.toUpperCase(),
    liveColor: cooking ? OK : DIM,
    units,
    tempInt: primary.shown === null ? null : Math.round(primary.shown),
    stale: primary.stale,
    maxTemp,
    gaugeFrac: primary.shown === null ? 0 : Math.max(0, Math.min(1, primary.shown / maxTemp)),
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
    // Tied to `smoking` rather than to a set of editable modes: this flag makes
    // the LEFT pill clickable, and outside Smoke that pill is AUGER DUTY, which
    // must not open a P-Mode picker. A recipe drives the mode itself, and Flask
    // hides the whole control panel during one -- so the pill stays read-only
    // however the sub-mode reads.
    pModeEditable: !dash.recipeStatus?.recipeMode && smoking,
    // A recipe drives Smoke+ itself, the same reason P-mode stays read-only
    // during one.
    smokePlusEditable: !dash.recipeStatus?.recipeMode && smoking,
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
