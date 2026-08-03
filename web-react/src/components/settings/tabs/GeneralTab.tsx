import { useOutletContext } from "react-router";
import { accentPath, readAccent, storedAccentName } from "../../../helpers/settings/accent";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import type { AccentName } from "../../../helpers/types";
import { useAppPrefs } from "../../AppPrefs";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { TextField } from "../fields/TextField";
import { SaveBar } from "../SaveBar";

// The three accents Theme.qml resolves (display/qml/Theme.qml:32-36), spelled
// the way the store holds them.
const ACCENTS = [
  { value: "Ember", label: "Ember" },
  { value: "Ice", label: "Ice" },
  { value: "Crimson", label: "Crimson" },
];

type General = {
  grill_name: string;
  accent_theme: string;
  sleep_timeout: number;
};

function readGeneral(s: Settings): General {
  return {
    grill_name: s.globals?.grill_name ?? SETTINGS_DEFAULTS.globals.grill_name,
    accent_theme: storedAccentName(readAccent(s)),
    sleep_timeout: s.display?.sleep_timeout ?? SETTINGS_DEFAULTS.display.sleep_timeout,
  };
}

export function GeneralTab() {
  const { save, saving, status } = useSaveSettings();
  const { settings } = useOutletContext<{ settings: Settings }>();
  const { setAccent } = useAppPrefs();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const { value: v, setValue: setV, dirty, markSaved } = useSettingsDraft("general", readGeneral);
  const set = <K extends keyof General>(k: K, val: General[K]) => setV((s) => ({ ...s, [k]: val }));

  const onSave = async () => {
    let delta = setPath({}, "globals.grill_name", v.grill_name);
    const path = accentPath(settings);
    if (path) delta = setPath(delta, path, v.accent_theme);
    delta = setPath(delta, "display.sleep_timeout", v.sleep_timeout);
    // No control flag: Flask's _settings_display does a bare write_settings
    // too, and the display process re-reads the store itself once a second.
    if (await save(delta, [])) markSaved();
  };

  return (
    <Section title="General">
      <TextField label="Grill Name" value={v.grill_name} onChange={(x) => set("grill_name", x)} />
      <Select
        label="Theme"
        value={v.accent_theme}
        options={ACCENTS}
        // Applied as it is picked so the choice can be seen before it is saved;
        // Save is what writes it to the store the display reads.
        onChange={(x) => {
          set("accent_theme", x);
          setAccent(x.toLowerCase() as AccentName);
        }}
      />
      {/* Flask kept this on its Display pane (settings/index.html:1080); the
          React app has no Display tab and General is where it belongs. It is
          live, not decorative: display/qtapp.py re-reads it once a second and
          drives the backlight plus `swaymsg output * dpms off` through
          ScreenPowerController. */}
      <NumberField
        label="Screen Sleep Timeout"
        value={v.sleep_timeout}
        onChange={(x) => set("sleep_timeout", x)}
        min={0}
        step={1}
        suffix="s"
        hint="Idle seconds before the attached screen sleeps. 0 = never sleep."
      />
      <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
    </Section>
  );
}
