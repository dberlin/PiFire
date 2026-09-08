import type { DashSocketPayload } from "@pifire/core/contracts/core";

export type TimerState = DashSocketPayload["timer"]["state"];

export interface DerivedTimer {
  state: TimerState;
  remaining: number | null;
}

/** The connection owner ages snapshots once, not each mounted timer view. */
export function deriveTimer(timer: DashSocketPayload["timer"]): DerivedTimer {
  return {
    state: timer.state,
    remaining: timer.remainingS === null ? null : clampSeconds(timer.remainingS),
  };
}

function clampSeconds(seconds: number): number {
  return Math.max(0, Math.floor(seconds));
}

/**
 * Format seconds as HH:MM:SS. Hours are not wrapped at 24 -- a 30-hour timer
 * reads "30:00:00" rather than silently rolling over to "06:00:00".
 */
export function formatRemaining(seconds: number | null): string {
  if (seconds === null) return "--:--:--";
  const total = clampSeconds(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}
