// Pure derivation of the cook timer's display state from the control-process
// timer block that arrives on the live socket.
//
// The backend (blueprints/mobile/socket_io.py) sends epoch SECONDS, already
// math.trunc'd:
//   { start, paused, end, keepWarm, shutdown }
//
// These functions never read the clock. `nowSeconds` is a parameter so callers
// tick it themselves and tests stay deterministic.

import type { DashSocketPayload } from "../contracts/core.gen"

export type TimerState = "stopped" | "running" | "paused";

export interface DerivedTimer {
  state: TimerState;
  remaining: number;
}

/**
 * Derive the timer's state and whole seconds remaining.
 *
 * `start == 0` means no timer, whatever `end`/`paused` still hold -- the
 * backend's stop/clear paths zero `start` first, so a lingering `end` from a
 * previous cook must not resurrect a stopped timer.
 *
 * While paused the remaining time is frozen at `end - paused`: the backend
 * unpause path recomputes `end` as `(end - paused) + now`, so `end - paused`
 * is exactly the duration that will be restored.
 *
 * Remaining is clamped at 0 -- an expired timer reads 00:00:00, never negative.
 */
export function deriveTimer(timer: DashSocketPayload["timer"], nowSeconds: number): DerivedTimer {
  if (timer.start === 0) return { state: "stopped", remaining: 0 };
  if (timer.paused > 0) {
    return { state: "paused", remaining: clampSeconds(timer.end - timer.paused) };
  }
  return { state: "running", remaining: clampSeconds(timer.end - nowSeconds) };
}

/** Whole seconds, never negative. */
function clampSeconds(seconds: number): number {
  return seconds > 0 ? Math.floor(seconds) : 0;
}

/**
 * Format seconds as HH:MM:SS. Hours are not wrapped at 24 -- a 30-hour timer
 * reads "30:00:00" rather than silently rolling over to "06:00:00".
 */
export function formatRemaining(seconds: number): string {
  const total = clampSeconds(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}
