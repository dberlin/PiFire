import { useEffect, useState } from "react";
import { useOutletContext } from "react-router";
import type { Settings } from "../settingsApi";
import { useSaveSettings } from "../useSaveSettings";
import { setPath } from "../delta";
import { Section } from "../fields/Section";
import { TextField } from "../fields/TextField";
import { Select } from "../fields/Select";

const THEMES = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function GeneralTab() {
  const { settings } = useOutletContext<{ settings: Settings }>();
  const { save, saving } = useSaveSettings();
  const [name, setName] = useState<string>(settings.globals?.grill_name ?? "");
  const [theme, setTheme] = useState<string>(settings.globals?.page_theme ?? "dark");
  const [saved, setSaved] = useState(false);

  // Re-sync when the loader revalidates (settings identity changes).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setName(settings.globals?.grill_name ?? "");
    setTheme(settings.globals?.page_theme ?? "dark");
  }, [settings]);

  const onSave = async () => {
    let delta = setPath({}, "globals.grill_name", name);
    delta = setPath(delta, "globals.page_theme", theme);
    setSaved(await save(delta, [])); // display-only: no control flag
  };

  return (
    <Section title="General">
      <TextField label="Grill Name" value={name} onChange={setName} />
      <Select label="Theme" value={theme} options={THEMES} onChange={setTheme} />
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>{saving ? "Saving…" : "Save"}</button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
