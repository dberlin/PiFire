import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import type { ProbeChartConfig } from "../../../helpers/settings/settingsTypes.gen";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { ColorField } from "../fields/ColorField";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { SaveBar } from "../SaveBar";

// The schema is the source of truth for this shape, so the generated type is
// used directly rather than restated. Notably the setpoint colors are
// `string | null` there, not `string` -- see COLOR_FIELD_SPECS below.
type ProbeColorConfig = ProbeChartConfig;
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
//
// "Present" means "holds a color string". defaults.py OMITS the setpoint keys
// for Food probes, while the schema declares them `string | null` defaulting to
// null — so both absent and null mean "not applicable to this probe" and must
// render nothing rather than an empty color input.
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
  fidelity_degrees: number;
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
    minutes: hp.minutes ?? SETTINGS_DEFAULTS.history_page.minutes,
    datapoints: hp.datapoints ?? SETTINGS_DEFAULTS.history_page.datapoints,
    fidelity_degrees: hp.fidelity_degrees ?? SETTINGS_DEFAULTS.history_page.fidelity_degrees,
    clearhistoryonstart: !!hp.clearhistoryonstart,
    autorefresh: hp.autorefresh === "on",
    ext_data: !!g.ext_data,
    probeConfig: structuredClone(hp.probe_config ?? {}),
  };
}

export function HistoryTab() {
  const { mode } = useOutletContext<{ settings: Settings; mode: string }>();
  const { save, saving, status } = useSaveSettings();
  // Held on SettingsShell, so an unfinished edit survives a trip to another tab.
  const { value: v, setValue: setV, dirty, markSaved } = useSettingsDraft("history", readHistory);
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
    d = setPath(d, "history_page.fidelity_degrees", v.fidelity_degrees);
    d = setPath(d, "history_page.clearhistoryonstart", v.clearhistoryonstart);
    d = setPath(d, "history_page.autorefresh", v.autorefresh ? "on" : "off");
    d = setPath(d, "history_page.probe_config", v.probeConfig);
    d = setPath(d, "globals.ext_data", v.ext_data);
    // bare write — no control flag
    if (await save(d, [])) markSaved();
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
        {/* Floor is 1, not 0: blueprints/api_history/routes.py:36 rejects
            minutes < 1 with a 400 (invalid_minutes). */}
        <NumberField
          integer
          label="Minutes"
          value={v.minutes}
          onChange={(n) => set("minutes", n)}
          min={1}
        />
        {/* NOT "how many points to draw": file_mgmt/downsample.py's
            select_indices returns EVERY index when the window holds this many
            samples or fewer, and only downsamples above it. Two samples are
            the fewest that can draw a line, so that is the floor. */}
        <NumberField
          integer
          label="Downsample above (samples)"
          value={v.datapoints}
          onChange={(n) => set("datapoints", n)}
          min={2}
        />
        <p className="pf-settings-hint">
          Windows holding this many samples or fewer are drawn from every sample. Above it, the
          chart is thinned to the fewest points that still meet the fidelity limit below.
        </p>
        <NumberField
          label="Chart Fidelity"
          value={v.fidelity_degrees}
          onChange={(n) => set("fidelity_degrees", n)}
          min={0}
          step={0.1}
          suffix="degrees"
        />
        <p className="pf-settings-hint">
          The most the drawn line may deviate from the real reading. Smaller keeps more detail and
          sends more points; larger sends fewer.
        </p>
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
        <div className="relative">
          <Toggle
            label="Extended Data Logging"
            checked={v.ext_data}
            onChange={(b) => set("ext_data", b)}
            disabled={ext_data_disabled}
            hint={
              ext_data_disabled
                ? modeUnknown
                  ? "Can't confirm the grill is stopped — extended-data logging stays locked"
                  : "Stop the grill to change extended-data logging"
                : undefined
            }
          />
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
                {COLOR_FIELD_SPECS.map((f) => {
                  const color = entry[f.key];
                  // Narrows away both undefined (key omitted) and null (schema
                  // default for a probe the color does not apply to).
                  if (typeof color !== "string") return null;
                  return (
                    <ColorField
                      key={f.key}
                      label={`${labelPrefix} ${f.label}`}
                      value={color}
                      onChange={(c) => setProbe(probeKey, f.key, c)}
                    />
                  );
                })}
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
        <SaveBar onSave={onSave} saving={saving} status={status} dirty={dirty} />
      </Section>
    </>
  );
}
