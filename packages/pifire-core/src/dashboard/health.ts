import type { DashSocketPayload } from "../contracts/core.gen";

// Substring of common/app.py's CONTROL_DOWN_ERROR, which
// socket_io._get_dash_data composes into `errors` on every frame while the last
// 30s liveness check says the control process is unreachable. It is NOT stored
// server-side, so it clears itself on the first frame after control answers.
const CONTROL_DOWN_MARKER = "control process did not respond";

export function deriveControlAlive(dash: DashSocketPayload): boolean {
  return !(dash.errors ?? []).some((e) => e.includes(CONTROL_DOWN_MARKER));
}

// Floor of a Hold setpoint, per units. Only the floor is a constant: the
// ceiling is the grill's own limit, settings.safety.maxtemp, which reaches the
// dashboard as LiveState.safetyMaxTemp already converted into these units.
export const SETPOINT_MIN: Record<"F" | "C", number> = { F: 150, C: 65 };

// Stands in when safetyMaxTemp is absent or nonsensical — a backend too old to
// send it, or a hand-edited settings tree. These are the fixed ceilings this UI
// shipped with before the real limit was on the wire.
const SETPOINT_MAX_FALLBACK: Record<"F" | "C", number> = { F: 500, C: 260 };

/** The bounds a Hold setpoint may take, given the grill's shutdown limit.
 *  A `safetyMaxTemp` at or below the floor cannot bound anything, so the
 *  fallback ceiling is used rather than collapsing the range to a point. */
export function setpointRange(
  units: "F" | "C",
  safetyMaxTemp: number | undefined,
): { min: number; max: number } {
  const min = SETPOINT_MIN[units];
  const max =
    safetyMaxTemp !== undefined && Number.isFinite(safetyMaxTemp) && safetyMaxTemp > min
      ? Math.round(safetyMaxTemp)
      : SETPOINT_MAX_FALLBACK[units];
  return { min, max };
}

export function clampSetpoint(temp: number, units: "F" | "C", safetyMaxTemp?: number): number {
  const { min, max } = setpointRange(units, safetyMaxTemp);
  const t = Math.round(temp);
  return t < min ? min : t > max ? max : t;
}
