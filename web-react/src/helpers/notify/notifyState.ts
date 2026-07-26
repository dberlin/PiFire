import type { ProbeData } from "../types";
import { type NotifyUpdate, postNotifyUpdates } from "./notifyApi";

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

// ---------------------------------------------------------------------------
// High / low limit alerts. A THIRD and FOURTH entry share the probe's label
// (common/defaults.py:540-550): probe_limit_high, condition "equal_above", and
// probe_limit_low, condition "equal_below". Unlike the target, a limit alert is
// not one-shot -- it stays armed for the whole cook and re-arms when the
// temperature comes back into range, which is what its `triggered` latch tracks.
// ---------------------------------------------------------------------------

export type LimitKind = "high" | "low";

/** ONE choice, exactly like TargetAction and for the same reason: the backend
 *  runs `if shutdown ... elif keep_warm ... elif reignite`
 *  (notify/notifications.py:157-174), so an entry carrying both `shutdown` and
 *  `reignite` silently drops the re-ignite. Flask spells this as two checkboxes
 *  that uncheck each other in JavaScript (_macro_dash_default.html:294-308) --
 *  the weaker form of the same idea. Keep-warm is deliberately absent: no UI in
 *  either app has ever offered it on a limit, and the socket payload publishes
 *  no flag to read it back from (blueprints/mobile/socket_io.py:781-793). */
export type LimitAction = "none" | "shutdown" | "reignite";

export interface LimitEdit {
  enabled: boolean;
  target: number;
  action: LimitAction;
}

/** Everything the probe's notify modal edits: the three entries that share its
 *  label, each addressed separately when it is saved. */
export interface NotifyEdit {
  target: TargetEdit;
  high: LimitEdit;
  low: LimitEdit;
}

const LIMIT_TYPE: Record<LimitKind, string> = {
  high: "probe_limit_high",
  low: "probe_limit_low",
};

const LIMIT_CONDITION: Record<LimitKind, string> = {
  high: "equal_above",
  low: "equal_below",
};

/** Seed both limit sections from the live socket payload, which flattens the
 *  two limit entries onto each probe (blueprints/mobile/socket_io.py:781-795).
 *  `shutdown` is read before `reignite` because the backend acts on it first. */
export function readLimitEdit(probe: ProbeData, kind: LimitKind): LimitEdit {
  if (kind === "high") {
    return {
      enabled: probe.highLimitReq,
      target: Math.round(probe.highLimitTemp),
      // No highLimitReignite exists on the wire, which is why the high limit
      // offers no re-ignite choice: there would be no way to show it back.
      action: probe.highLimitShutdown ? "shutdown" : "none",
    };
  }
  return {
    enabled: probe.lowLimitReq,
    target: Math.round(probe.lowLimitTemp),
    action: probe.lowLimitShutdown ? "shutdown" : probe.lowLimitReignite ? "reignite" : "none",
  };
}

export function readNotifyEdit(probe: ProbeData): NotifyEdit {
  return {
    target: readTargetEdit(probe),
    high: readLimitEdit(probe, "high"),
    low: readLimitEdit(probe, "low"),
  };
}

/** The fields of ONE limit entry.
 *
 *  `triggered` is why this feature cannot use the per-field REST grammar:
 *  /api/set/limit_high|limit_low/... takes req/shutdown/keep_warm/reignite/
 *  target (common/api_commands.py:544-551) and cannot set `triggered` at all.
 *  Saving a limit whose condition is ALREADY satisfied without pre-arming it
 *  sounds the alarm on the very next control pass (notify/notifications.py:112),
 *  instantly, before anything has gone wrong. Pre-armed, the backend stays quiet
 *  until the temperature leaves the range and comes back.
 *
 *  Pre-arming uses the SAME comparison the backend fires on -- `>=` for
 *  equal_above, `<=` for equal_below (notify/notifications.py:745-748). Flask
 *  uses a strict `>` / `<` (dash_default.js:724, :766), so at exactly the limit
 *  it writes triggered:false for a condition that already holds and the alarm
 *  sounds immediately; that boundary is the one case pre-arming exists for.
 *
 *  Every input to the backend's action tail is named, not just the one the UI
 *  shows: a `keep_warm` or `reignite` left armed by the mobile DTO or by
 *  /api/set/limit_* would otherwise act on an alert whose UI displays no action.
 *  `condition` is a per-type constant rather than user state -- it is named
 *  because notify.set APPENDS an entry it cannot find
 *  (common/control_delta.py:263-270) and check_notify reads item["condition"]
 *  unguarded, so an appended entry without it would raise on the next pass. */
export function limitEditFields(
  kind: LimitKind,
  edit: LimitEdit,
  currentTemp: number,
): Record<string, unknown> {
  const condition = LIMIT_CONDITION[kind];
  if (!edit.enabled) {
    return {
      req: false,
      target: 0,
      triggered: false,
      shutdown: false,
      keep_warm: false,
      reignite: false,
      condition,
    };
  }
  const target = Math.round(edit.target);
  return {
    req: true,
    target,
    triggered: kind === "high" ? currentTemp >= target : currentTemp <= target,
    shutdown: edit.action === "shutdown",
    keep_warm: false,
    reignite: edit.action === "reignite",
    condition,
  };
}

/** The three addressed patches one Set writes. `currentTemp` is the probe's
 *  reading at save time, used only to pre-arm the two `triggered` latches. */
export function notifyEditUpdates(
  label: string,
  edit: NotifyEdit,
  currentTemp: number,
): NotifyUpdate[] {
  return [
    { label, type: "probe", fields: targetEditFields(edit.target) },
    { label, type: LIMIT_TYPE.high, fields: limitEditFields("high", edit.high, currentTemp) },
    { label, type: LIMIT_TYPE.low, fields: limitEditFields("low", edit.low, currentTemp) },
  ];
}

/** One POST carrying all three entries -- no read first, because nothing here
 *  depends on the array's current contents, and a read-modify-write of the whole
 *  array would revert whatever another writer changed in the same control cycle.
 *  Each patch names the entry it means and the fields it sets, so the drain
 *  applies it against live state and every entry this probe does not own
 *  survives untouched. The result is NOT echoed back immediately: the write is
 *  queued and drained by the control loop (~110 ms in Stop mode, measured), so
 *  callers must render from the socket payload rather than mirroring locally. */
export function saveNotifyEdit(
  baseUrl: string,
  label: string,
  edit: NotifyEdit,
  currentTemp: number,
): Promise<void> {
  return postNotifyUpdates(baseUrl, notifyEditUpdates(label, edit, currentTemp));
}
