import type { SettingsSchema } from "./settingsTypes.gen";

/**
 * Does this build drive a PWM-controlled DC fan?
 *
 * The single source for every DC-fan-only control in the settings tree. Flask
 * gates four places on `settings['platform']['dc_fan']`
 * (`blueprints/settings/templates/settings/index.html:63-65`, `:405-423`,
 * `:581-768`, `:857-868`); this is their one predicate.
 *
 * Absence reads as AC, never as "unknown, so show it": the Setup Wizard
 * DERIVES `dc_fan` for `x86_numato` / `ft232h_relay` (see PlatformTab's note),
 * so a missing field means the wizard concluded there is no DC fan.
 */
export function hasDcFan(settings: SettingsSchema): boolean {
  return !!settings.platform?.dc_fan;
}
