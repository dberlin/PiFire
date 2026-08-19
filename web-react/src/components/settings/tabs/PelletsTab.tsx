import type { SettingsFlag } from "@pifire/core/settings/controllerTypes";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import { SettingsFieldErrorsProvider } from "../../../helpers/settings/fieldErrorContext";
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
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

function readPellets(s: SettingsSchema): Pellets {
  const pl = s.pelletlevel ?? {};
  const gl = s.globals ?? {};
  return {
    warning_enabled: !!pl.warning_enabled,
    warning_time: pl.warning_time ?? SETTINGS_DEFAULTS.pelletlevel.warning_time,
    warning_level: pl.warning_level ?? SETTINGS_DEFAULTS.pelletlevel.warning_level,
    empty: pl.empty ?? SETTINGS_DEFAULTS.pelletlevel.empty,
    full: pl.full ?? SETTINGS_DEFAULTS.pelletlevel.full,
    augerrate: gl.augerrate ?? SETTINGS_DEFAULTS.globals.augerrate,
    prime_ignition: !!gl.prime_ignition,
  };
}

export function PelletsTab() {
  const { settings } = useOutletContext<{ settings: SettingsSchema; mode: string }>();
  const { save, saving, status, errors } = useSaveSettings();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const { value: v, setValue: setV, dirty, markSaved } = useSettingsDraft("pellets", readPellets);

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

    if (await save(d, flags)) markSaved();
  };

  return (
    <SettingsFieldErrorsProvider errors={errors}>
      <Section title="Pellets">
        <Toggle
          label="Warning Enabled"
          checked={v.warning_enabled}
          onChange={(b) => set("warning_enabled", b)}
          path="pelletlevel.warning_enabled"
        />
        <NumberField
          integer
          label="Warning Time"
          value={v.warning_time}
          onChange={(n) => set("warning_time", n)}
          // index.html:1325
          min={5}
          max={240}
          suffix="min"
          path="pelletlevel.warning_time"
        />
        <NumberField
          integer
          label="Warning Level"
          value={v.warning_level}
          onChange={(n) => set("warning_level", n)}
          min={0}
          max={100}
          suffix="%"
          path="pelletlevel.warning_level"
        />
        <NumberField
          integer
          label="Empty"
          value={v.empty}
          onChange={(n) => set("empty", n)}
          // index.html:1362 — the audit missed this one
          min={1}
          max={100}
          suffix="cm"
          path="pelletlevel.empty"
        />
        <NumberField
          integer
          label="Full"
          value={v.full}
          onChange={(n) => set("full", n)}
          // index.html:1354 — the audit missed this one
          min={0}
          max={100}
          suffix="cm"
          path="pelletlevel.full"
        />
        <NumberField
          label="Auger Rate"
          value={v.augerrate}
          onChange={(n) => set("augerrate", n)}
          step={0.1}
          path="globals.augerrate"
        />
        <Toggle
          label="Prime Ignition"
          checked={v.prime_ignition}
          onChange={(b) => set("prime_ignition", b)}
          path="globals.prime_ignition"
        />
        {/* I16 — safety copy from index.html:1403-1412, dropped in the port.
            This control lights a fire, so the warning travels with it. */}
        <p className="pf-settings-error-text">
          DANGER: Only enable the igniter during Priming if you are absolutely sure that you need to
          do this. Enabling the igniter will ignite pellets and start the firepot, even without the
          fan enabled. This feature will only turn on the igniter if Prime &amp; Startup is
          selected; otherwise, priming without startup will not utilize the igniter.
        </p>
        <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
      </Section>
    </SettingsFieldErrorsProvider>
  );
}
