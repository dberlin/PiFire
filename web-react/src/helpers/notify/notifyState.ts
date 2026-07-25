import type { ProbeData } from "../types";
import { getNotifyData, type NotifyEntry, postNotifyData } from "./notifyApi";

// The backend runs `if shutdown: ... elif keep_warm: ...`
// (notify/notifications.py:142-159), so ticking both means shutdown and silently
// ignores keep-warm. Model it as ONE choice rather than two booleans so the UI
// cannot express a state the backend will not honour. (The Flask modal offers
// two independent checkboxes and does not say which wins.)
export type TargetAction = "none" | "shutdown" | "keepWarm";

export interface TargetEdit {
  enabled: boolean;
  target: number;
  action: TargetAction;
}

// Hard-coded in blueprints/dash/templates/default/_macro_dash_default.html:174-186.
// NOT probe.maxTemp from the dash payload -- that is the gauge ceiling out of
// settings.dashboard.dashboards.Default.config, a different number.
export function targetRange(isPrimary: boolean, units: "F" | "C"): { min: number; max: number } {
  if (isPrimary) return { min: 0, max: units === "F" ? 600 : 300 };
  return { min: 0, max: units === "F" ? 300 : 225 };
}

/** Seed an edit from the live socket payload, which already carries every field
 *  (blueprints/mobile/socket_io.py:823-848 flattens the type:"probe" entry onto
 *  each probe). No REST read is needed to open the modal. */
export function readTargetEdit(probe: ProbeData): TargetEdit {
  return {
    enabled: probe.targetReq,
    target: Math.round(probe.target),
    action: probe.targetShutdown ? "shutdown" : probe.targetKeepWarm ? "keepWarm" : "none",
  };
}

// Edits ONLY the `type === "probe"` entry for `label`. Up to three entries share
// a label (common/defaults.py:512-538) -- probe, probe_limit_high,
// probe_limit_low -- and the limit pair is a separate feature. Everything else
// in the array comes back untouched because the caller posts the WHOLE array,
// and an entry the posted array omits is read as a DELETION rather than as
// silence (common/common.py::merge_notify_data).
export function applyTargetEdit(
  entries: NotifyEntry[],
  label: string,
  edit: TargetEdit,
): NotifyEntry[] {
  return entries.map((e) => {
    if (e.type !== "probe" || e.label !== label) return e;
    if (!edit.enabled) {
      // Only the target entry is cleared. Flask's cancelNotify
      // (dash_default.js:807-813) clears every entry sharing the label, wiping
      // the high/low limit alerts as a side effect; that is not ported.
      return { ...e, req: false, target: 0, shutdown: false, keep_warm: false };
    }
    return {
      ...e,
      req: true,
      target: Math.round(edit.target),
      shutdown: edit.action === "shutdown",
      keep_warm: edit.action === "keepWarm",
    };
  });
}

// Read-modify-write, exactly as the Flask dashboard does
// (dash_default.js:784-799). One POST, so the array is replaced atomically and
// nothing else in control is patched. The result is NOT echoed back
// immediately: the write is queued and drained by the control loop (~110 ms in
// Stop mode, measured), so callers must render from the socket payload rather
// than mirroring the new value locally.
export async function saveTargetEdit(
  baseUrl: string,
  label: string,
  edit: TargetEdit,
): Promise<void> {
  const entries = await getNotifyData(baseUrl);
  await postNotifyData(baseUrl, applyTargetEdit(entries, label, edit));
}
