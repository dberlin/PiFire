// Elapsed cook time, derived from the CONTROLLER's clock rather than from when
// this browser happened to mount.
//
// startup_timestamp is epoch seconds, set once at ignition
// (controller/runtime/modes/startup.py:120), deliberately NOT rewritten by
// Reignite (controller/runtime/modes/reignite.py:17-18), zeroed when the cook
// ends (controller/runtime/controller.py:405,428), and math.trunc'd onto the
// wire (blueprints/mobile/socket_io.py:234). Flask has always read exactly this
// field (dash_default.js:400-412).

/**
 * Seconds since ignition, or null when no cook is running.
 *
 * Both arguments are epoch SECONDS. `nowSeconds` is the browser's clock and
 * `startupTimestamp` is the Pi's, so a browser running behind the Pi produces a
 * negative difference; clamping is enough here because nothing is armed from
 * this value (contrast the timer, where the skew forced the arithmetic
 * server-side -- see helpers/command.ts).
 */
export function cookElapsed(startupTimestamp: number, nowSeconds: number): number | null {
  if (startupTimestamp === 0) return null;
  const elapsed = Math.floor(nowSeconds) - Math.floor(startupTimestamp);
  return elapsed < 0 ? 0 : elapsed;
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
