// The shapes of the /api/tuner/* surface.
//
// Written from live responses (confirmed against a running backend
// 2026-07-28), not from the Python literals. The one member worth naming here:
// `trohms` is `number | null`, and the null is load-bearing -- it means "this
// probe is not reporting". Flask answered 0 for that case, which is
// indistinguishable from a shorted probe reading a real zero.

/** The three points a manual tune records, in the order the page shows them. */
export type Segment = "High" | "Medium" | "Low";

export interface TunerPoint {
  segment: Segment;
  /** In the grill's configured units, as the operator read it off a thermometer. */
  temp: number;
  /** Resistance in ohms, captured from the live reading. */
  trohms: number;
}

export interface TunerSession {
  open: boolean;
  /** The mode the grill is in after the call. */
  mode: string;
  /** Whether this call actually MOVED the mode. False on a no-op close, and
   * false when a cook was running and was deliberately left alone. */
  restored: boolean;
}

export interface TrReading {
  probe: string;
  /** Ohms, or null when the probe is not reporting. Never coerce to 0. */
  trohms: number | null;
  /** False when no session is open, in which case the reading is stale:
   * control.py only refreshes the tuning blob in tuning mode. */
  tuning: boolean;
}

export interface Coefficients {
  a: number;
  b: number;
  c: number;
  /** Temp (x) vs Tr (y). Empty when the curve could not be evaluated. */
  chart: { x: number; y: number }[];
  /** Whether `chart` is empty because it failed, rather than because there was
   * nothing to draw. calc_shh_chart abandons the whole series on one bad
   * point, which its own docstring says is common. */
  chart_ok: boolean;
}

export interface ProfileInput {
  name: string;
  a: number;
  b: number;
  c: number;
  /** A probe LABEL to attach this profile to, or null for save-only. */
  apply_to: string | null;
}

export interface SavedProfile {
  id: string;
  applied: string | null;
}

/** Resolves rather than throws, matching helpers/admin/adminApi.ts: a refusal
 * is an expected outcome on this page (the grill is lit; the maths did not
 * converge), so every caller renders the reason instead of escaping past it. */
export interface TunerResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
  /** From a 409's data.mode: the mode that blocked the session. */
  mode?: string;
  /** From a 400's data.field. */
  field?: string;
}
