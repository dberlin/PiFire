import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
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
};

function readGeneral(s: Settings): General {
  return {
    grill_name: s.globals?.grill_name ?? "",
    page_theme: s.globals?.page_theme ?? "light",
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
    // display-only: no control flag
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
      <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
    </Section>
  );
}
