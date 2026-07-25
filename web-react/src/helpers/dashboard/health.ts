import type { LiveState } from "../types";

// Substring of the exact error socket_io._check_control_status appends every 30s
// when the control process is unreachable.
const CONTROL_DOWN_MARKER = "control process did not respond";

export function deriveControlAlive(dash: LiveState): boolean {
  return !(dash.errors ?? []).some((e) => e.includes(CONTROL_DOWN_MARKER));
}

export const SETPOINT_RANGE: Record<"F" | "C", { min: number; max: number }> = {
  F: { min: 150, max: 500 },
  C: { min: 65, max: 260 },
};

export function clampSetpoint(temp: number, units: "F" | "C"): number {
  const { min, max } = SETPOINT_RANGE[units];
  const t = Math.round(temp);
  return t < min ? min : t > max ? max : t;
}
