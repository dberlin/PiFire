import { useState } from "react";
import { useOutletContext } from "react-router";
import type { Settings } from "../settingsApi";
import { useSaveSettings } from "../useSaveSettings";
import { setPath } from "../delta";
import { Section } from "../fields/Section";
import { Toggle } from "../fields/Toggle";
import { NumberField } from "../fields/NumberField";

type History = {
  minutes: number;
  datapoints: number;
  clearhistoryonstart: boolean;
  autorefresh: boolean;
  ext_data: boolean;
};

function readHistory(s: Settings): History {
  const hp = s.history_page ?? {};
  const g = s.globals ?? {};
  return {
    minutes: hp.minutes ?? 240,
    datapoints: hp.datapoints ?? 100,
    clearhistoryonstart: !!hp.clearhistoryonstart,
    autorefresh: hp.autorefresh === "on",
    ext_data: !!g.ext_data,
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

  const onSave = async () => {
    let d: object = {};
    d = setPath(d, "history_page.minutes", v.minutes);
    d = setPath(d, "history_page.datapoints", v.datapoints);
    d = setPath(d, "history_page.clearhistoryonstart", v.clearhistoryonstart);
    d = setPath(d, "history_page.autorefresh", v.autorefresh ? "on" : "off");
    d = setPath(d, "globals.ext_data", v.ext_data);
    setSaved(await save(d, [])); // bare write — no control flag
  };

  const ext_data_disabled = mode !== "Stop";

  return (
    <Section title="History">
      <NumberField label="Minutes" value={v.minutes} onChange={(n) => set("minutes", n)} min={0} />
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
          <span className="pf-settings-hint">Stop the grill to change extended-data logging</span>
        )}
      </div>
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
