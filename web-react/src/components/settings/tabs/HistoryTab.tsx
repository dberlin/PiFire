import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { ColorField } from "../fields/ColorField";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";

type ProbeColorConfig = {
  name: string;
  type: string;
  enabled: boolean;
  line_color: string;
  bg_color: string;
  line_color_setpoint?: string;
  bg_color_setpoint?: string;
  line_color_target?: string;
  bg_color_target?: string;
  dash_setpoint: boolean;
  fill: boolean;
};
type ProbeConfig = Record<string, ProbeColorConfig>;

type ColorFieldKey =
  | "line_color"
  | "bg_color"
  | "line_color_setpoint"
  | "bg_color_setpoint"
  | "line_color_target"
  | "bg_color_target";

// Presence-driven: a probe only carries the color keys relevant to its type
// (see common/defaults.py default_probe_config — setpoint colors are
// Primary-only). Rendered in this fixed order when present.
const COLOR_FIELD_SPECS: { key: ColorFieldKey; label: string }[] = [
  { key: "line_color", label: "Line Color" },
  { key: "bg_color", label: "Background Color" },
  { key: "line_color_setpoint", label: "Line Color (Setpoint)" },
  { key: "bg_color_setpoint", label: "Background Color (Setpoint)" },
  { key: "line_color_target", label: "Line Color (Target)" },
  { key: "bg_color_target", label: "Background Color (Target)" },
];

type History = {
  minutes: number;
  datapoints: number;
  clearhistoryonstart: boolean;
  autorefresh: boolean;
  ext_data: boolean;
  probeConfig: ProbeConfig;
};

// Prefer the probe's display name (matches the card header) over the dict key
// as the label prefix, falling back to the key when the name is missing or
// shared by more than one probe (ambiguous as a label prefix).
function computeLabelPrefixes(probeConfig: ProbeConfig): Record<string, string> {
  const nameCounts = new Map<string, number>();
  for (const entry of Object.values(probeConfig)) {
    if (entry.name) nameCounts.set(entry.name, (nameCounts.get(entry.name) ?? 0) + 1);
  }
  const prefixes: Record<string, string> = {};
  for (const [key, entry] of Object.entries(probeConfig)) {
    prefixes[key] = entry.name && nameCounts.get(entry.name) === 1 ? entry.name : key;
  }
  return prefixes;
}

function readHistory(s: Settings): History {
  const hp = s.history_page ?? {};
  const g = s.globals ?? {};
  return {
    minutes: hp.minutes ?? 240,
    datapoints: hp.datapoints ?? 100,
    clearhistoryonstart: !!hp.clearhistoryonstart,
    autorefresh: hp.autorefresh === "on",
    ext_data: !!g.ext_data,
    probeConfig: structuredClone(hp.probe_config ?? {}),
  };
}

export function HistoryTab() {
  const { settings, mode } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving } = useSaveSettings();
  const [v, setV] = useState<History>(() => readHistory(settings));
  const [prev, setPrev] = useState(settings);
  const [saved, setSaved] = useState(false);
  if (settings !== prev) {
    setPrev(settings);
    setV(readHistory(settings));
  }
  const set = <K extends keyof History>(k: K, val: History[K]) => setV((s) => ({ ...s, [k]: val }));
  const setProbe = <K extends keyof ProbeColorConfig>(
    label: string,
    k: K,
    val: ProbeColorConfig[K],
  ) =>
    setV((s) => ({
      ...s,
      probeConfig: { ...s.probeConfig, [label]: { ...s.probeConfig[label], [k]: val } },
    }));

  const onSave = async () => {
    let d: object = {};
    d = setPath(d, "history_page.minutes", v.minutes);
    d = setPath(d, "history_page.datapoints", v.datapoints);
    d = setPath(d, "history_page.clearhistoryonstart", v.clearhistoryonstart);
    d = setPath(d, "history_page.autorefresh", v.autorefresh ? "on" : "off");
    d = setPath(d, "history_page.probe_config", v.probeConfig);
    d = setPath(d, "globals.ext_data", v.ext_data);
    setSaved(await save(d, [])); // bare write — no control flag
  };

  // Extended-data logging changes the history schema, so it is gated to a
  // stopped grill. getMode() returns "" when it cannot read the mode, which
  // gates too (fail closed) -- but "stop the grill" is the wrong explanation
  // in that case, since the grill may well already be stopped.
  const modeUnknown = mode === "";
  const ext_data_disabled = mode !== "Stop";
  const probeLabels = Object.keys(v.probeConfig);
  const labelPrefixes = computeLabelPrefixes(v.probeConfig);

  return (
    <>
      <Section title="History">
        <NumberField
          label="Minutes"
          value={v.minutes}
          onChange={(n) => set("minutes", n)}
          min={0}
        />
        <NumberField
          label="Data Points"
          value={v.datapoints}
          onChange={(n) => set("datapoints", n)}
          min={0}
        />
        <Toggle
          label="Clear History on Start"
          checked={v.clearhistoryonstart}
          onChange={(b) => set("clearhistoryonstart", b)}
        />
        <Toggle
          label="Auto Refresh"
          checked={v.autorefresh}
          onChange={(b) => set("autorefresh", b)}
        />
        <div style={{ position: "relative" }}>
          <Toggle
            label="Extended Data Logging"
            checked={v.ext_data}
            onChange={(b) => set("ext_data", b)}
            disabled={ext_data_disabled}
          />
          {ext_data_disabled && (
            <span className="pf-settings-hint">
              {modeUnknown
                ? "Can't confirm the grill is stopped — extended-data logging stays locked"
                : "Stop the grill to change extended-data logging"}
            </span>
          )}
        </div>
      </Section>

      <Section title="Chart Colors">
        {probeLabels.length === 0 ? (
          <p className="pf-settings-hint">No probes configured.</p>
        ) : (
          probeLabels.map((probeKey) => {
            const entry = v.probeConfig[probeKey];
            const labelPrefix = labelPrefixes[probeKey];
            return (
              <div className="pf-probe-card" key={probeKey}>
                <div className="pf-probe-card-header">
                  <span className="pf-probe-card-name">{entry.name}</span>
                  <span className="pf-probe-chip">{entry.type}</span>
                  <Toggle
                    label={`${labelPrefix} Enabled`}
                    checked={entry.enabled}
                    onChange={(b) => setProbe(probeKey, "enabled", b)}
                  />
                </div>
                {COLOR_FIELD_SPECS.filter((f) => entry[f.key] !== undefined).map((f) => (
                  <ColorField
                    key={f.key}
                    label={`${labelPrefix} ${f.label}`}
                    value={entry[f.key] as string}
                    onChange={(c) => setProbe(probeKey, f.key, c)}
                  />
                ))}
                <Toggle
                  label={`${labelPrefix} Dash Setpoint`}
                  checked={entry.dash_setpoint}
                  onChange={(b) => setProbe(probeKey, "dash_setpoint", b)}
                />
                <Toggle
                  label={`${labelPrefix} Fill`}
                  checked={entry.fill}
                  onChange={(b) => setProbe(probeKey, "fill", b)}
                />
              </div>
            );
          })
        )}
        <div className="pf-settings-actions">
          <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
            {saving ? "Saving…" : "Save"}
          </button>
          {saved && <span className="pf-settings-saved">Saved ✓</span>}
        </div>
      </Section>
    </>
  );
}
