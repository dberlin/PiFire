// Which rows a metrics card shows, per mode.
//
// This is blueprints/metrics/templates/metrics/_macro_metrics.html's eight
// macros as data. They are eight near-copies of one table differing only in
// which rows they include, so they collapse to one row builder and a per-mode
// row list -- and as data they are testable without rendering anything.

import type { MetricRecord } from "@pifire/core/contracts/content";

/** One row of a card: the label, the raw column, and its readable form. */
export interface MetricRow {
  label: string;
  value: string;
  converted: string;
}

/** Shown wherever a value does not exist yet, matching the Jinja `--`. */
const NONE = "—";

/** The colour band across the top of a card. */
export type ModeAccent = "start" | "stop" | "warn" | "neutral";

/** Ported from index.html's dispatch, which picks a macro whose card-header
 * class is bg-success (Startup), bg-danger (Stop, Error), bg-warning
 * (Reignite and the else branch) or bg-secondary (everything else). */
export function modeAccent(mode: string): ModeAccent {
  if (mode === "Startup") return "start";
  if (mode === "Stop" || mode === "Error") return "stop";
  if (mode === "Reignite") return "warn";
  return "neutral";
}

/** Which labels each mode shows, in order. `startup` is also the fallback:
 * index.html's dispatch ends in `{% else %}{{ render_reignite(...) }}`, whose
 * row set is identical to render_startup's. */
const ROW_SETS = {
  stop: ["Stop Time"],
  timing: ["Start Time", "End Time", "Time in Mode"],
  hold: [
    "Start Time",
    "End Time",
    "Time in Mode",
    "Auger On Time",
    "Estimated Pellet Usage",
    "Smoke Plus",
    "Grill Set Temp",
  ],
  smoke: [
    "Start Time",
    "End Time",
    "Time in Mode",
    "Auger On Time",
    "Estimated Pellet Usage",
    "Smoke Plus",
    "Smart Start Profile",
    "Smart Startup Temp",
    "P Mode",
    "Auger Cycle Time",
  ],
  startup: [
    "Start Time",
    "End Time",
    "Time in Mode",
    "Auger On Time",
    "Estimated Pellet Usage",
    "Smart Start Profile",
    "Smart Startup Temp",
    "P Mode",
    "Auger Cycle Time",
  ],
} as const satisfies Record<string, readonly string[]>;

const MODE_ROWS: Record<string, keyof typeof ROW_SETS> = {
  Stop: "stop",
  Error: "stop",
  Shutdown: "timing",
  Monitor: "timing",
  Manual: "timing",
  Hold: "hold",
  Smoke: "smoke",
  Startup: "startup",
  Reignite: "startup",
};

/** The rows for one record, already stringified for display.
 *
 * `units` is the grill's configured unit letter, appended to the two
 * temperature rows exactly as the Jinja macros append it. */
export function metricRows(record: MetricRecord, units: string): MetricRow[] {
  const running = record.endtime === 0;

  const build: Record<string, () => MetricRow> = {
    "Stop Time": () => ({
      label: "Stop Time",
      value: String(record.starttime),
      converted: String(record.starttime_c),
    }),
    "Start Time": () => ({
      label: "Start Time",
      value: String(record.starttime),
      converted: String(record.starttime_c),
    }),
    "End Time": () => ({
      label: "End Time",
      value: String(record.endtime),
      //  endtime_c is the NUMBER 0 while a mode runs, not a placeholder
      //  string, so this branches on endtime rather than rendering it bare.
      converted: running ? NONE : String(record.endtime_c),
    }),
    "Time in Mode": () => ({
      label: "Time in Mode",
      value: running ? NONE : String(record.endtime - record.starttime),
      converted: record.timeinmode,
    }),
    "Auger On Time": () => ({
      label: "Auger On Time",
      value: String(record.augerontime),
      converted: record.augerontime_c,
    }),
    "Estimated Pellet Usage": () => ({
      label: "Estimated Pellet Usage",
      value: record.estusage_m,
      converted: record.estusage_i,
    }),
    "Smoke Plus": () => ({
      label: "Smoke Plus",
      value: String(record.smokeplus),
      converted: record.smokeplus ? "Active" : "Disabled",
    }),
    "Smart Start Profile": () => ({
      label: "Smart Start Profile",
      value: String(record.smart_start_profile),
      converted: String(record.smart_start_profile),
    }),
    "Smart Startup Temp": () => ({
      label: "Smart Startup Temp",
      value: String(record.startup_temp),
      converted: `${record.startup_temp} ${units}`,
    }),
    "P Mode": () => ({
      label: "P Mode",
      value: String(record.p_mode),
      converted: String(record.p_mode),
    }),
    "Auger Cycle Time": () => ({
      label: "Auger Cycle Time",
      value: String(record.auger_cycle_time),
      converted: `${record.auger_cycle_time}s`,
    }),
    //  From primary_setpoint. _macro_metrics.html reads metric['grill_settemp'],
    //  which matches no column in the metrics table -- Jinja renders a missing
    //  key as empty, so Flask's Hold card has always shown a blank setpoint.
    "Grill Set Temp": () => ({
      label: "Grill Set Temp",
      value: String(record.primary_setpoint),
      converted: `${record.primary_setpoint} ${units}`,
    }),
  };

  const set = ROW_SETS[MODE_ROWS[record.mode] ?? "startup"];
  return set.map((label) => build[label]());
}
