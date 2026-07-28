// The shape of GET /api/metrics.
//
// Written from a LIVE response, not from common/defaults.py's metrics_items:
// that list declares ("starttime_c", 0) and ("timeinmode", 0), and
// common/common.py's process_metrics overwrites BOTH with strings before the
// record ever leaves the server. A type taken from the defaults would be wrong
// about every field it named.

/**
 * One metrics record: a single mode the grill passed through.
 *
 * The `_c` suffix is PiFire's own: the "converted", human-readable form of the
 * raw column beside it, computed server-side by process_metrics(). Nothing here
 * is recomputed in the client -- there is one definition of what "1 m 30 s"
 * means and it is in Python.
 */
export interface MetricRecord {
  /** A uuid string on any record append_metric wrote, which is all of them. */
  id: string;
  /** Epoch MILLISECONDS. process_metrics divides by 1000 before formatting. */
  starttime: number;
  /** "%H:%M:%S", server-local. */
  starttime_c: string;
  /** Epoch milliseconds, or 0 while the mode is still running. */
  endtime: number;
  /**
   * "%H:%M:%S" for a finished mode, and the NUMBER 0 for a running one --
   * process_metrics assigns `endtime_c = 0` rather than a placeholder string.
   * Branch on `endtime === 0`; never render this field bare.
   */
  endtime_c: string | number;
  /**
   * "NA" in Stop mode, "Active" while running, else "3 m 20 s" / "45 s".
   * The minute form needs `seconds > 60`, so exactly 60 reads "60 s".
   */
  timeinmode: string;
  /** "Startup" | "Smoke" | "Hold" | "Shutdown" | "Stop" | "Monitor" | "Manual"
   * | "Reignite" | "Error", but typed as a string: the mode vocabulary lives in
   * common/modes.py and a record written by an older build can carry anything. */
  mode: string;
  /** Seconds. */
  augerontime: number;
  /** "<seconds> s". */
  augerontime_c: string;
  /** "<grams> grams". */
  estusage_m: string;
  /** "<pounds> pounds (<ounces> ounces)". */
  estusage_i: string;
  fanontime: number;
  /**
   * Declared TEXT in the metrics DDL and never written by process_metrics, so
   * it carries whatever control.py last put there -- as a string, because TEXT
   * affinity coerces a numeric write ("0", not 0), or null if never written.
   */
  fanontime_c: string | null;
  /** Coerced to a real boolean by _metrics_row_to_dict, not left as 0/1. */
  smokeplus: boolean;
  /** The Hold setpoint, in the grill's configured units. NOT `grill_settemp`:
   * that name appears only in the Jinja macro and matches no column. */
  primary_setpoint: number;
  smart_start_profile: number;
  startup_temp: number;
  p_mode: number;
  /** Seconds. */
  auger_cycle_time: number;
  pellet_level_start: number;
  pellet_level_end: number;
  pellet_brand_type: string;
}

export interface MetricsPayload {
  /** Insertion order -- oldest first, as the server read them. */
  metrics: MetricRecord[];
  /** "F" or "C". The suffix on every temperature row. */
  units: string;
  /** Grams per second. The stated assumption behind every usage estimate. */
  augerrate: number;
}

/**
 * How a read finished. Resolves rather than throws, matching
 * helpers/admin/adminApi.ts: the page renders the failure in place and offers a
 * retry, so an exception would only have to be caught and turned back into this.
 */
export interface MetricsResult {
  ok: boolean;
  /** HTTP status, or 0 when the request never reached a server. */
  status: number;
  message: string;
  data: MetricsPayload | null;
}
