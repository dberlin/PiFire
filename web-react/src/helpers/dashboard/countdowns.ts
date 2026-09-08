import type { DashSocketPayload } from "@pifire/core/contracts/core";

/** Read controller-projected durations. Local receipt aging happens once in
 * useLiveState, so rendering and route remounts never reanchor old snapshots. */
export function modeCountdown(dash: DashSocketPayload): number | null {
  if (dash.recipeStatus?.recipeMode) return null;
  const value = dash.durations.modeRemainingS;
  return value === null ? null : Math.max(0, Math.floor(value));
}

export function lidCountdown(dash: DashSocketPayload): number | null {
  if (dash.currentMode !== "Hold" || !dash.lidOpenDetected) return null;
  const value = dash.durations.lidRemainingS;
  return value === null ? null : Math.max(0, Math.floor(value));
}

/**
 * The status header a running recipe gets: `Recipe | <step mode>`
 * (dash_default.js:297-300). Null when no recipe is running.
 *
 * displayMode is status["mode"] on the wire (socket_io.py:250) -- the running
 * SUB-mode, not the outer "Recipe".
 */
export function recipeLabel(dash: DashSocketPayload): string | null {
  if (!dash.recipeStatus?.recipeMode) return null;
  return `Recipe | ${dash.displayMode}`;
}
