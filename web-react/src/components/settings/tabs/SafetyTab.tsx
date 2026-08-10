import { setPath } from "../../../helpers/settings/delta";
import { SettingsFieldErrorsProvider } from "../../../helpers/settings/fieldErrorContext";
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import type { SettingsSchema } from "../../../helpers/settings/settingsTypes.gen";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { SaveBar } from "../SaveBar";

type Safety = {
  minstartuptemp: number;
  maxstartuptemp: number;
  maxtemp: number;
  reigniteretries: number;
  startup_check: boolean;
  allow_manual_changes: boolean;
  manual_override_time: number;
};
function readSafety(s: SettingsSchema): Safety {
  const x = s.safety ?? {};
  return {
    minstartuptemp: x.minstartuptemp ?? SETTINGS_DEFAULTS.safety.minstartuptemp,
    maxstartuptemp: x.maxstartuptemp ?? SETTINGS_DEFAULTS.safety.maxstartuptemp,
    maxtemp: x.maxtemp ?? SETTINGS_DEFAULTS.safety.maxtemp,
    reigniteretries: x.reigniteretries ?? SETTINGS_DEFAULTS.safety.reigniteretries,
    startup_check: !!x.startup_check,
    allow_manual_changes: !!x.allow_manual_changes,
    manual_override_time: x.manual_override_time ?? SETTINGS_DEFAULTS.safety.manual_override_time,
  };
}

export function SafetyTab() {
  const { save, saving, status, errors } = useSaveSettings();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const { value: v, setValue: setV, dirty, markSaved } = useSettingsDraft("safety", readSafety);
  const set = <K extends keyof Safety>(k: K, val: Safety[K]) => setV((s) => ({ ...s, [k]: val }));

  const onSave = async () => {
    let d: object = {};
    // Object.entries widens its key to string, which erases exactly the fact
    // this loop depends on: every key of `v` is a field of settings.safety.
    type SafetyKey = keyof NonNullable<SettingsSchema["safety"]>;
    for (const k of Object.keys(v) as SafetyKey[]) d = setPath(d, `safety.${k}`, v[k]);
    // _settings_safety does a bare write — no control flag
    if (await save(d, [])) markSaved();
  };

  return (
    <SettingsFieldErrorsProvider errors={errors}>
      <Section title="Safety">
        <NumberField
          integer
          label="Min Startup Temp"
          value={v.minstartuptemp}
          onChange={(n) => set("minstartuptemp", n)}
          // index.html:1262 — without this React accepts a NEGATIVE grill temp
          min={1}
          suffix="°"
          path="safety.minstartuptemp"
        />
        <NumberField
          integer
          label="Max Startup Temp"
          value={v.maxstartuptemp}
          onChange={(n) => set("maxstartuptemp", n)}
          // index.html:1269
          min={1}
          suffix="°"
          path="safety.maxstartuptemp"
        />
        <NumberField
          integer
          label="Max Grill Temp"
          value={v.maxtemp}
          onChange={(n) => set("maxtemp", n)}
          // index.html:1276
          min={1}
          suffix="°"
          path="safety.maxtemp"
        />
        <NumberField
          integer
          label="Reignite Retries"
          value={v.reigniteretries}
          onChange={(n) => set("reigniteretries", n)}
          // index.html:1286
          min={0}
          max={10}
          path="safety.reigniteretries"
        />
        <NumberField
          integer
          label="Manual Override Time"
          value={v.manual_override_time}
          onChange={(n) => set("manual_override_time", n)}
          min={0}
          suffix="s"
          path="safety.manual_override_time"
        />
        <Toggle
          label="Startup Check"
          checked={v.startup_check}
          onChange={(b) => set("startup_check", b)}
          path="safety.startup_check"
        />
        <Toggle
          label="Allow Manual Output Changes"
          checked={v.allow_manual_changes}
          onChange={(b) => set("allow_manual_changes", b)}
          path="safety.allow_manual_changes"
        />
        <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
      </Section>
    </SettingsFieldErrorsProvider>
  );
}
