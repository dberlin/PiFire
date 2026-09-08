import type { DashSocketPayload } from "@pifire/core/contracts/core";

/** Cook exposure is independent of mode transitions and wall provenance. */
export function cookElapsed(durations: DashSocketPayload["durations"]): number | null {
  return durations.cookElapsedS === null ? null : Math.max(0, Math.floor(durations.cookElapsedS));
}

/**
 * Flask's adaptive duration format, reproduced exactly: HH:MM:SS above an hour
 * (zero-padded hour), MM:SS above a minute, NNs below, and the literal "--"
 * when no cook is running (dash_default.js:410,599-611).
 *
 * Deliberately a separate function from deriveView's `fmtDuration`, which pads
 * differently and has no seconds-only branch. That one keeps its behaviour and
 * its existing callers.
 */
export function fmtElapsed(seconds: number | null): string {
  if (seconds === null) return "--";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (h > 0) return `${pad(h)}:${pad(m)}:${pad(sec)}`;
  if (m > 0) return `${pad(m)}:${pad(sec)}`;
  return `${pad(sec)}s`;
}
