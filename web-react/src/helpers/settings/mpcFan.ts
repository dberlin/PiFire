import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";

import { hasDcFan } from "./platform";
import type { SettingsDrafts } from "./settingsDrafts";

/** The controller key whose allocator emits a fan duty. */
const MPC = "mpc";

export const MPC_FAN_CONFLICT_MESSAGE =
  "MPC Controls Fan is on, but PWM Control is off on the PWM Fan tab. The controller's fan " +
  "commands cannot reach the fan, so it would run at a fixed speed for the whole cook. Enable " +
  "PWM Control on the PWM Fan tab, or turn MPC Controls Fan off.";

export const MPC_FAN_PWM_NOTE =
  "The MPC controller is set to command the fan, but PWM Control is off — its fan commands will " +
  "not be applied. Turn PWM Control on to give the controller the fan.";

export const MPC_FAN_DISABLED_NOTE =
  "The MPC controller commands the fan directly, so the duty-cycle-from-temperature profile below " +
  "is never used. Its values are kept, and it becomes editable again if you turn off MPC " +
  "Controls Fan on the Controller tab.";

type ControllerDraft = { selected?: unknown; values?: Record<string, unknown> };

/**
 * Is the MPC set to command the fan, counting an unsaved Controller-tab edit?
 *
 * The PWM tab has to answer this for a choice the user has made but not yet
 * saved, so the draft store is consulted first and saved settings are the
 * fallback. Read-only: the PWM tab never writes the Controller tab's draft.
 */
export function mpcFanPending(settings: SettingsSchema, drafts: SettingsDrafts): boolean {
  const draft = drafts.controller?.value as ControllerDraft | undefined;
  if (draft && typeof draft.selected === "string") {
    return draft.selected === MPC && !!draft.values?.enable_fan_input;
  }
  return (
    settings.controller?.selected === MPC && !!settings.controller?.config?.[MPC]?.enable_fan_input
  );
}

/**
 * A configuration whose fan lever is wired to nothing: the MPC is set to
 * command the fan on a DC-fan build whose PWM control is switched off. On an
 * AC-fan build there is no PWM fan to command, so the option is simply
 * inapplicable rather than broken.
 */
export function mpcFanConflict({
  selected,
  enableFanInput,
  settings,
}: {
  selected: string;
  enableFanInput: boolean;
  settings: SettingsSchema;
}): boolean {
  if (selected !== MPC || !enableFanInput) return false;
  if (!hasDcFan(settings)) return false;
  return !settings.pwm?.pwm_control;
}
