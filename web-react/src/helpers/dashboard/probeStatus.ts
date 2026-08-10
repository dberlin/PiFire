import type { ProbeStatusPayload } from "../contracts/core.gen";

// The two per-probe badges the Flask card carries and this port declared in its
// types but never read: link state and battery level
// (_macro_dash_default.html:20-64, dash_default.js:492-538).
//
// Both are Bluetooth-only in practice. blueprints/mobile/socket_io.py:858-859
// copies each key onto the wire ONLY if the driver set it, and only the BT
// drivers do (probes/bt_ibbq.py:361, probes/bt_meater.py:284,
// probes/disabled.py:60). So "key absent" means "this probe has no such
// concept" -- a wired ADC probe must show no badge at all, which is exactly how
// the Jinja gates it. That is why both functions distinguish absent from false
// and from null.

export interface ConnectionBadge {
  label: string;
  tone: "ok" | "off";
}

export function connectionBadge(status: ProbeStatusPayload | undefined): ConnectionBadge | null {
  if (status === undefined || !("connected" in status)) return null;
  return status.connected === true
    ? { label: "Connected", tone: "ok" }
    : { label: "Disconnected", tone: "off" };
}

export interface BatteryBadge {
  text: string;
  tone: "unknown" | "danger" | "warn" | "ok";
  /** 0 empty, 1 half, 2 three-quarters, 3 full -- Flask's four icons. */
  level: 0 | 1 | 2 | 3;
}

export function batteryBadge(status: ProbeStatusPayload | undefined): BatteryBadge | null {
  if (status === undefined || !("batteryPercentage" in status)) return null;
  const raw = status.batteryPercentage;
  if (raw === null || raw === undefined || typeof raw !== "number" || Number.isNaN(raw)) {
    return { text: "Unknown", tone: "unknown", level: 0 };
  }
  const pct = Math.min(100, Math.max(0, Math.round(raw)));
  // A genuine 0% renders as "0%" in the danger tone. Flask writes
  // `battery_percentage || null` (dash_default.js:443,453), which coerces 0 into
  // Unknown -- i.e. it hides a flat battery behind "no idea". That is a bug and
  // is deliberately not copied; this test exists so a later "parity" pass
  // cannot reintroduce it.
  if (pct < 10) return { text: `${pct}%`, tone: "danger", level: 0 };
  if (pct < 40) return { text: `${pct}%`, tone: "warn", level: 1 };
  if (pct < 90) return { text: `${pct}%`, tone: "ok", level: 2 };
  return { text: `${pct}%`, tone: "ok", level: 3 };
}
