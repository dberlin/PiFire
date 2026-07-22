import type { DashData } from "../types";

// Pure presentation logic for the PiFire Dashboard (port of the design's
// renderVals(), but driven by the REAL socket_dash_data contract instead of the
// design's internal simulator). Kept side-effect free so it's unit-testable —
// every color / label / geometry input the widgets need comes out of here.

const OK = "#5ec96f";
const OK2 = "#8fe09a";
const AMBER = "#ffb020";
const AMBER2 = "#ffce6a";
const DANGER = "#ff5a4d";
const DANGER2 = "#ff8b82";
const IDLE = "#57514a";
const GRAY = "#4a443c";
const YELLOW = "#ffd23f";
const DIM = "#7d7264";
const SURFACE = "#2c231a";
const EDGE = "rgba(255,255,255,0.13)";

// Modes where a cook is actively running (drives the live dot + gauge glow +
// cook-time counter). Everything else (Monitor / Shutdown / Stop / Error / "")
// is treated as not-cooking.
const COOKING_MODES = new Set(["Startup", "Smoke", "Hold", "Prime", "Reheat"]);

// A soft accent tint that still tracks the active [data-accent] theme.
const accentMix = (pct: number) => `color-mix(in srgb, var(--accent) ${pct}%, transparent)`;

export interface ProbeCardView {
  name: string;
  tempInt: number;
  unit: "F" | "C";
  targetStr: string;
  tgtColor: string;
  barPct: number;
  barColor: string;
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
  color: string;
  color2: string;
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

function probeCard(fp: DashData["foodProbes"][number], units: "F" | "C"): ProbeCardView {
  const hasTarget = fp.target > 0 && fp.targetReq;
  const done = hasTarget && fp.temp >= fp.target - 1;
  return {
    name: fp.title,
    tempInt: Math.round(fp.temp),
    unit: units,
    targetStr: hasTarget ? `→ ${Math.round(fp.target)}°` : "AMBIENT",
    tgtColor: hasTarget ? (done ? OK : YELLOW) : DIM,
    barPct: hasTarget ? Math.max(2, Math.min(100, (fp.temp / fp.target) * 100)) : 0,
    barColor: done ? OK : "var(--accent)",
  };
}

function hopperView(level: number): HopperView {
  const pct = Math.max(0, Math.min(100, Math.round(level)));
  const low = pct < 15;
  const mid = pct < 35;
  return {
    pct,
    color: low ? DANGER : mid ? AMBER : OK,
    color2: low ? DANGER2 : mid ? AMBER2 : OK2,
    label: low ? "REFILL PELLETS" : mid ? "RUNNING LOW" : "LEVEL OK",
    labelColor: low ? DANGER2 : mid ? AMBER2 : DIM,
  };
}

export function deriveView(dash: DashData): DashView {
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
    valColor: "#cfc6b8",
    bg: SURFACE,
    border: EDGE,
    labelColor: DIM,
  };
  const pillR: PillView = smokeOn
    ? { label: "SMOKE+", value: "ON", valColor: OK2, bg: "color-mix(in srgb, #5ec96f 14%, transparent)", border: OK, labelColor: OK2 }
    : { label: "SMOKE+", value: "OFF", valColor: DIM, bg: SURFACE, border: EDGE, labelColor: DIM };

  const igniter = outputView(dash.outputs.igniter > 0, "#ff7a1a", "HOT");

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
    fan: outputView(dash.outputs.fan > 0, "var(--accent)", "RUNNING"),
    auger: outputView(dash.outputs.auger > 0, "var(--accent)", "FEEDING"),
    // igniter uses its fixed ember dot (not the shared green) when hot.
    igniter: { ...igniter, dot: igniter.on ? "#ff7a1a" : GRAY },
    pillL,
    pillR,
    hopper: hopperView(dash.hopperLevel),
    lidOpen: dash.lidOpenDetected,
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
