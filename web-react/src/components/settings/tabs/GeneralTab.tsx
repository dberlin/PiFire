import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { TextField } from "../fields/TextField";
import { SaveBar } from "../SaveBar";

const THEMES = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

type General = {
  grill_name: string;
  page_theme: string;
  sleep_timeout: number;
};

function readGeneral(s: Settings): General {
  return {
    grill_name: s.globals?.grill_name ?? "",
    page_theme: s.globals?.page_theme ?? "light",
    // Same default as common/common.py's display_sleep_timeout() and the
    // schema (settings_schema.py: ge=0, default 300).
    sleep_timeout: s.display?.sleep_timeout ?? 300,
  };
}

export function GeneralTab() {
  const { save, saving, status } = useSaveSettings();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const { value: v, setValue: setV, dirty, markSaved } = useSettingsDraft("general", readGeneral);
  const set = <K extends keyof General>(k: K, val: General[K]) => setV((s) => ({ ...s, [k]: val }));

  const onSave = async () => {
    let delta = setPath({}, "globals.grill_name", v.grill_name);
    delta = setPath(delta, "globals.page_theme", v.page_theme);
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
        value={v.page_theme}
        options={THEMES}
        onChange={(x) => set("page_theme", x)}
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
