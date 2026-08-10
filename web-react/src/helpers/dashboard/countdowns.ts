import type { DashSocketPayload } from "../contracts/core.gen";

// The three status readouts the Flask dashboard has always carried and the
// React port dropped: how long is left in a timed mode, how long the PID stays
// paused for an open lid, and which recipe step is running.
//
// Every value on this path is SECONDS (blueprints/mobile/socket_io.py:259-266
// math.truncs them onto the wire). Arithmetic copied from
// blueprints/dash/static/default/js/dash_default.js:348-368 and :386-398.

/** Modes that run against a fixed duration, and which duration each one uses. */
const MODE_DURATION: Record<string, keyof DashSocketPayload> = {
  Startup: "startDuration",
  Reignite: "startDuration",
  Prime: "primeDuration",
  Shutdown: "shutdownDuration",
};

/**
 * Seconds left in a timed mode, or null when the current mode is not one.
 *
 * Returns null during a recipe even when the step's sub-mode is Startup or
 * Prime. Flask keys this off the OUTER mode variable, which is control["mode"]
 * and therefore reads "Recipe" for the whole run (dash_default.js:349), so no
 * countdown is shown. That is reproduced rather than improved on: the countdown's
 * inputs are not published per-step, so a per-step number would be invented.
 */
export function modeCountdown(dash: DashSocketPayload, nowSeconds: number): number | null {
  if (dash.recipeStatus?.recipeMode) return null;
  const durationKey = MODE_DURATION[dash.currentMode];
  if (durationKey === undefined) return null;
  const duration = dash[durationKey];
  if (typeof duration !== "number") return null;
  const left = Math.floor(duration - (Math.floor(nowSeconds) - Math.floor(dash.modeStartTime)));
  return left < 0 ? 0 : left;
}

/**
 * Seconds the PID stays paused for an open lid, or null when that is not the
 * situation. Shown ONLY in Hold: lid-open detection does not run in any other
 * mode (dash_default.js:386).
 */
export function lidCountdown(dash: DashSocketPayload, nowSeconds: number): number | null {
  if (dash.currentMode !== "Hold" || !dash.lidOpenDetected) return null;
  const left = Math.floor(Math.floor(dash.lidOpenEndTime) - Math.floor(nowSeconds));
  return left < 0 ? 0 : left;
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
