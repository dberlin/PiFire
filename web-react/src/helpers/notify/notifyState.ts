import type { ProbeData } from "../types";
import { postNotifyUpdates } from "./notifyApi";

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

// The four fields this modal owns on the `type === "probe"` entry. Up to three
// entries share a label (common/defaults.py:512-538) -- probe,
// probe_limit_high, probe_limit_low -- and the limit pair is a separate
// feature, so only the "probe" entry is addressed and the limit alerts keep
// whatever the user set. (Flask's cancelNotify, dash_default.js:807-813, clears
// every entry sharing the label and wipes the limit alerts as a side effect;
// that is not ported.)
export function targetEditFields(edit: TargetEdit): Record<string, unknown> {
  if (!edit.enabled) return { req: false, target: 0, shutdown: false, keep_warm: false };
  return {
    req: true,
    target: Math.round(edit.target),
    shutdown: edit.action === "shutdown",
    keep_warm: edit.action === "keepWarm",
  };
}

// One POST naming one entry and four fields -- no read first, because nothing
// here depends on the array's current contents, and a read-modify-write of the
// whole array would revert whatever another writer changed in the same control
// cycle. The result is NOT echoed back immediately: the write is queued and
// drained by the control loop (~110 ms in Stop mode, measured), so callers must
// render from the socket payload rather than mirroring the new value locally.
export function saveTargetEdit(baseUrl: string, label: string, edit: TargetEdit): Promise<void> {
  return postNotifyUpdates(baseUrl, [{ label, type: "probe", fields: targetEditFields(edit) }]);
}
