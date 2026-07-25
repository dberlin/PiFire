import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
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
function readSafety(s: Settings): Safety {
  const x = s.safety ?? {};
  return {
    minstartuptemp: x.minstartuptemp ?? 75,
    maxstartuptemp: x.maxstartuptemp ?? 100,
    maxtemp: x.maxtemp ?? 550,
    reigniteretries: x.reigniteretries ?? 1,
    startup_check: !!x.startup_check,
    allow_manual_changes: !!x.allow_manual_changes,
    manual_override_time: x.manual_override_time ?? 30,
  };
}

export function SafetyTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving, status } = useSaveSettings();
  const [v, setV] = useState<Safety>(() => readSafety(settings));
  const [prev, setPrev] = useState(settings);
  if (settings !== prev) {
    setPrev(settings);
    setV(readSafety(settings));
  }
  const set = <K extends keyof Safety>(k: K, val: Safety[K]) => setV((s) => ({ ...s, [k]: val }));

  const onSave = async () => {
    let d: object = {};
    for (const [k, val] of Object.entries(v)) d = setPath(d, `safety.${k}`, val);
    await save(d, []); // _settings_safety does a bare write — no control flag
  };

  return (
    <Section title="Safety">
      <NumberField
        label="Min Startup Temp"
        value={v.minstartuptemp}
        onChange={(n) => set("minstartuptemp", n)}
        suffix="°"
      />
      <NumberField
        label="Max Startup Temp"
        value={v.maxstartuptemp}
        onChange={(n) => set("maxstartuptemp", n)}
        suffix="°"
      />
      <NumberField
        label="Max Grill Temp"
        value={v.maxtemp}
        onChange={(n) => set("maxtemp", n)}
        suffix="°"
      />
      <NumberField
        label="Reignite Retries"
        value={v.reigniteretries}
        onChange={(n) => set("reigniteretries", n)}
        min={0}
      />
      <NumberField
        label="Manual Override Time"
        value={v.manual_override_time}
        onChange={(n) => set("manual_override_time", n)}
        min={0}
        suffix="s"
      />
      <Toggle
        label="Startup Check"
        checked={v.startup_check}
        onChange={(b) => set("startup_check", b)}
      />
      <Toggle
        label="Allow Manual Output Changes"
        checked={v.allow_manual_changes}
        onChange={(b) => set("allow_manual_changes", b)}
      />
      <SaveBar onSave={onSave} saving={saving} status={status} />
    </Section>
  );
}
