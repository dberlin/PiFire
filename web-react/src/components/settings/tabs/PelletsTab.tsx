import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings, SettingsFlag } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { SaveBar } from "../SaveBar";

type Pellets = {
  warning_enabled: boolean;
  warning_time: number;
  warning_level: number;
  empty: number;
  full: number;
  augerrate: number;
  prime_ignition: boolean;
};

function readPellets(s: Settings): Pellets {
  const pl = s.pelletlevel ?? {};
  const gl = s.globals ?? {};
  return {
    warning_enabled: !!pl.warning_enabled,
    warning_time: pl.warning_time ?? 0,
    warning_level: pl.warning_level ?? 0,
    empty: pl.empty ?? 0,
    full: pl.full ?? 0,
    augerrate: gl.augerrate ?? 0,
    prime_ignition: !!gl.prime_ignition,
  };
}

export function PelletsTab() {
  const { settings } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving, status } = useSaveSettings();
  const [v, setV] = useState<Pellets>(() => readPellets(settings));
  const [prev, setPrev] = useState(settings);

  if (settings !== prev) {
    setPrev(settings);
    setV(readPellets(settings));
  }

  const set = <K extends keyof Pellets>(k: K, val: Pellets[K]) => setV((s) => ({ ...s, [k]: val }));

  const onSave = async () => {
    // Get the loaded values to check for distance changes
    const loaded = readPellets(settings);

    let d: object = {};
    // Build delta for pelletlevel fields
    d = setPath(d, "pelletlevel.warning_enabled", v.warning_enabled);
    d = setPath(d, "pelletlevel.warning_time", v.warning_time);
    d = setPath(d, "pelletlevel.warning_level", v.warning_level);
    d = setPath(d, "pelletlevel.empty", v.empty);
    d = setPath(d, "pelletlevel.full", v.full);
    // Build delta for globals fields
    d = setPath(d, "globals.augerrate", v.augerrate);
    d = setPath(d, "globals.prime_ignition", v.prime_ignition);

    // Determine flags: always include settings_update, add distance_update if empty or full changed
    const flags: SettingsFlag[] = ["settings_update"];
    if (v.empty !== loaded.empty || v.full !== loaded.full) {
      flags.push("distance_update");
    }

    await save(d, flags);
  };

  return (
    <Section title="Pellets">
      <Toggle
        label="Warning Enabled"
        checked={v.warning_enabled}
        onChange={(b) => set("warning_enabled", b)}
      />
      <NumberField
        label="Warning Time"
        value={v.warning_time}
        onChange={(n) => set("warning_time", n)}
        // index.html:1325
        min={5}
        max={240}
        suffix="min"
      />
      <NumberField
        label="Warning Level"
        value={v.warning_level}
        onChange={(n) => set("warning_level", n)}
        min={0}
        max={100}
        suffix="%"
      />
      <NumberField
        label="Empty"
        value={v.empty}
        onChange={(n) => set("empty", n)}
        // index.html:1362 — the audit missed this one
        min={1}
        max={100}
        suffix="cm"
      />
      <NumberField
        label="Full"
        value={v.full}
        onChange={(n) => set("full", n)}
        // index.html:1354 — the audit missed this one
        min={0}
        max={100}
        suffix="cm"
      />
      <NumberField
        label="Auger Rate"
        value={v.augerrate}
        onChange={(n) => set("augerrate", n)}
        step={0.1}
      />
      <Toggle
        label="Prime Ignition"
        checked={v.prime_ignition}
        onChange={(b) => set("prime_ignition", b)}
      />
      {/* I16 — safety copy from index.html:1403-1412, dropped in the port.
          This control lights a fire, so the warning travels with it. */}
      <p className="pf-settings-error-text">
        DANGER: Only enable the igniter during Priming if you are absolutely sure that you need to
        do this. Enabling the igniter will ignite pellets and start the firepot, even without the
        fan enabled. This feature will only turn on the igniter if Prime &amp; Startup is selected;
        otherwise, priming without startup will not utilize the igniter.
      </p>
      <SaveBar onSave={onSave} saving={saving} status={status} />
    </Section>
  );
}
