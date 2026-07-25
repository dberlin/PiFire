import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { TextField } from "../fields/TextField";
import { SaveBar } from "../SaveBar";

const THEMES = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function GeneralTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving, status } = useSaveSettings();
  const [name, setName] = useState<string>(settings.globals?.grill_name ?? "");
  const [theme, setTheme] = useState<string>(settings.globals?.page_theme ?? "light");

  const [prevSettings, setPrevSettings] = useState(settings);
  if (settings !== prevSettings) {
    setPrevSettings(settings);
    setName(settings.globals?.grill_name ?? "");
    setTheme(settings.globals?.page_theme ?? "light");
  }

  const onSave = async () => {
    let delta = setPath({}, "globals.grill_name", name);
    delta = setPath(delta, "globals.page_theme", theme);
    await save(delta, []); // display-only: no control flag
  };

  return (
    <Section title="General">
      <TextField label="Grill Name" value={name} onChange={setName} />
      <Select label="Theme" value={theme} options={THEMES} onChange={setTheme} />
      <SaveBar onSave={onSave} saving={saving} status={status} />
    </Section>
  );
}
