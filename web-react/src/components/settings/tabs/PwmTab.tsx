import { useState } from "react";
import { useOutletContext } from "react-router";
import { clampToBounds } from "../../../helpers/settings/bounds";
import { setPath } from "../../../helpers/settings/delta";
import { hasDcFan } from "../../../helpers/settings/platform";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import type { PwmProfile } from "../../../helpers/settings/settingsTypes.gen";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { type RangeProfileColumn, RangeProfileTable } from "../RangeProfileTable";
import { SaveBar } from "../SaveBar";

const PWM_DEFAULTS = SETTINGS_DEFAULTS.pwm;

type Pwm = {
  pwm_control: boolean;
  update_time: number;
  min_duty_cycle: number;
  max_duty_cycle: number;
  frequency: number;
  temp_range_list: number[];
  profiles: Record<string, number>[];
};

function readPwm(settings: Settings): Pwm {
  const p = settings.pwm ?? {};
  return {
    pwm_control: !!p.pwm_control,
    update_time: p.update_time ?? PWM_DEFAULTS.update_time,
    min_duty_cycle: p.min_duty_cycle ?? PWM_DEFAULTS.min_duty_cycle,
    max_duty_cycle: p.max_duty_cycle ?? PWM_DEFAULTS.max_duty_cycle,
    frequency: p.frequency ?? PWM_DEFAULTS.frequency,
    // Cloned (never aliased into the widget's onChange arrays) — the widget
    // emits fresh arrays on edit, but our local state must not share
    // references with `settings`.
    temp_range_list: structuredClone(
      (p.temp_range_list ?? PWM_DEFAULTS.temp_range_list) as number[],
    ),
    profiles: structuredClone(
      (p.profiles ?? PWM_DEFAULTS.profiles) as unknown as Record<string, number>[],
    ),
  };
}

export function PwmTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving, status } = useSaveSettings();
  // Held on SettingsShell, so an unfinished table edit survives a trip to
  // another tab; re-read from the loader whenever there is no draft.
  const { value: pwm, setValue: setPwm, dirty, markSaved } = useSettingsDraft("pwm", readPwm);
  // Client-side and pre-flight, so it is deliberately NOT routed through
  // SaveBar's `status` — that channel carries the server's verdict.
  const [boundsError, setBoundsError] = useState<string | null>(null);

  const set = <K extends keyof Pwm>(k: K, v: Pwm[K]) => setPwm((s) => ({ ...s, [k]: v }));

  // Flask hides this entire pane on an AC-fan build
  // (settings/index.html:581-768). We render a notice instead of early-return
  // BEFORE the hooks above — an early return there would break the Rules of
  // Hooks — and the route stays registered so a bookmarked URL still resolves.
  const dcFan = hasDcFan(settings);

  // Column min/max come from the tab's CURRENT local values so a duty-cycle
  // edit clamps against whatever is on screen (including an un-saved
  // min/max edit), not the last-saved settings.
  const DUTY_COLUMNS: RangeProfileColumn[] = [
    {
      key: "duty_cycle",
      label: "Duty cycle",
      suffix: "%",
      min: pwm.min_duty_cycle,
      max: pwm.max_duty_cycle,
    },
  ];
  const units = settings.globals?.units === "C" ? "°C" : "°F";

  const onSave = async () => {
    // Ported from index.html:747-758 (validateDutyCycle, wired as
    // onclick="return validateDutyCycle()" on the submit button). Flask's test
    // is `>=`: with equal bounds every profile would have to equal exactly that
    // value, and PwmSettings._check_profiles would reject anything else.
    if (pwm.min_duty_cycle >= pwm.max_duty_cycle) {
      setBoundsError("Max Duty Cycle must be greater than Min Duty Cycle.");
      return; // do NOT call save()
    }
    setBoundsError(null);

    // Ported from blueprints/settings/routes.py:485-495 — narrowing min/max
    // alone leaves these two outside the new range, which write_settings()
    // then rejects (PwmSettings._check_profiles /
    // SettingsSchema._check_startup_pwm_duty_cycle). The table only clamps a
    // cell when that cell is edited, so it does not cover this.
    const clamped: Pwm = {
      ...pwm,
      profiles: pwm.profiles.map((p) => ({
        ...p,
        duty_cycle: clampToBounds(p.duty_cycle, pwm.min_duty_cycle, pwm.max_duty_cycle),
      })),
    };

    let d: object = {};
    // pwm.temp_range_list rides the same delta wholesale (single Save per
    // tab, using the existing ["settings_update"] flag) — it's already a key
    // of `pwm`, so this loop covers it too. `profiles` is written separately
    // below, since its local shape needs a cast the other keys don't.
    // Object.entries widens its key to string, which erases exactly the fact
    // this loop depends on: every key of `clamped` (bar `profiles`) is a
    // field of settings.pwm with a matching value type.
    type PwmKey = keyof NonNullable<Settings["pwm"]>;
    const scalarKeys = (Object.keys(clamped) as PwmKey[]).filter(
      (k) => k !== "profiles",
    ) as Exclude<PwmKey, "profiles">[];
    for (const k of scalarKeys) {
      d = setPath(d, `pwm.${k}`, clamped[k]);
    }
    // `profiles` is RangeProfileTable's generic Record<string, number>[]
    // representation of the schema's closed PwmProfile[] (duty_cycle only);
    // this is the one field of settings.pwm that needs the cast.
    d = setPath(d, "pwm.profiles", clamped.profiles as unknown as PwmProfile[]);
    // Cross-section: startup.pwm_duty_cycle lives on another tab but is bound
    // to this range (routes.py:495). Written unconditionally — writing back an
    // unchanged value is harmless and keeps this branch-free.
    d = setPath(
      d,
      "startup.pwm_duty_cycle",
      clampToBounds(
        settings.startup?.pwm_duty_cycle ?? SETTINGS_DEFAULTS.startup.pwm_duty_cycle,
        pwm.min_duty_cycle,
        pwm.max_duty_cycle,
      ),
    );
    if (await save(d, ["settings_update"])) markSaved(); // control loop must re-read pwm
  };

  if (!dcFan) {
    return (
      <Section title="PWM Fan">
        <p className="pf-settings-hint">
          PWM fan control is unavailable on this grill. These settings apply only to a DC fan driven
          by a PWM output; this platform is configured for an AC fan. Change the fan type in the
          Setup Wizard to enable them.
        </p>
      </Section>
    );
  }

  return (
    <Section title="PWM Fan">
      <Toggle
        label="PWM Control"
        checked={pwm.pwm_control}
        onChange={(v) => set("pwm_control", v)}
      />
      <NumberField
        integer
        label="Update Time"
        value={pwm.update_time}
        onChange={(v) => set("update_time", v)}
        min={1}
        suffix="s"
      />
      <NumberField
        integer
        label="Min Duty Cycle"
        value={pwm.min_duty_cycle}
        onChange={(v) => set("min_duty_cycle", v)}
        min={1}
        max={100}
        suffix="%"
      />
      <NumberField
        integer
        label="Max Duty Cycle"
        value={pwm.max_duty_cycle}
        onChange={(v) => set("max_duty_cycle", v)}
        min={1}
        max={100}
        suffix="%"
      />
      <NumberField
        integer
        label="Frequency"
        value={pwm.frequency}
        onChange={(v) => set("frequency", v)}
        min={1}
        suffix="Hz"
      />
      <RangeProfileTable
        boundaries={pwm.temp_range_list}
        profiles={pwm.profiles}
        columns={DUTY_COLUMNS}
        rangeHeader="ΔT range"
        unit={units}
        // index.html:932, 978
        boundaryMin={0}
        boundaryMax={200}
        onChange={(temp_range_list, profiles) =>
          setPwm((s) => ({ ...s, temp_range_list, profiles }))
        }
      />
      {boundsError && (
        <p className="pf-settings-error-text" role="alert">
          {boundsError}
        </p>
      )}
      <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
    </Section>
  );
}
