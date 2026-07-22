import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { ControllerMetadata, Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { Toggle } from "../fields/Toggle";

type ControllerValues = Record<string, number | boolean>;

function firstControllerKey(meta: ControllerMetadata | null): string {
  if (!meta) return "";
  return Object.keys(meta.metadata)[0] ?? "";
}

function readSelected(settings: Settings, meta: ControllerMetadata | null): string {
  const sel = settings.controller?.selected;
  if (typeof sel === "string" && meta?.metadata[sel]) return sel;
  return firstControllerKey(meta);
}

function deriveValues(
  selected: string,
  settings: Settings,
  meta: ControllerMetadata | null,
): ControllerValues {
  if (!meta || !selected || !meta.metadata[selected]) return {};
  const saved = settings.controller?.config?.[selected] ?? {};
  const out: ControllerValues = {};
  for (const opt of meta.metadata[selected].config) {
    if (opt.option_type === "bool") {
      out[opt.option_name] =
        typeof saved[opt.option_name] === "boolean" ? saved[opt.option_name] : !!opt.option_default;
    } else if (opt.option_type === "float" || opt.option_type === "int") {
      const v = saved[opt.option_name];
      out[opt.option_name] =
        typeof v === "number" ? v : typeof opt.option_default === "number" ? opt.option_default : 0;
    }
    // unknown option_type values are skipped — not rendered, not included in the save delta
  }
  return out;
}

export function ControllerTab() {
  const { settings, controllerMeta } = useOutletContext<{
    settings: Settings;
    mode: string;
    controllerMeta: ControllerMetadata | null;
  }>();
  const { save, saving } = useSaveSettings();

  const [selected, setSelected] = useState(() => readSelected(settings, controllerMeta));
  const [values, setValues] = useState<ControllerValues>(() =>
    deriveValues(readSelected(settings, controllerMeta), settings, controllerMeta),
  );
  const [prevSettings, setPrevSettings] = useState(settings);
  const [prevSelected, setPrevSelected] = useState(selected);
  const [saved, setSaved] = useState(false);

  if (settings !== prevSettings || selected !== prevSelected) {
    setPrevSettings(settings);
    setPrevSelected(selected);
    setValues(deriveValues(selected, settings, controllerMeta));
  }

  if (!controllerMeta) {
    return (
      <Section title="Controller">
        <p className="pf-settings-error">Controller metadata unavailable.</p>
      </Section>
    );
  }

  const entry = controllerMeta.metadata[selected];
  const set = (name: string, val: number | boolean) => setValues((v) => ({ ...v, [name]: val }));

  const onSave = async () => {
    let d: object = {};
    d = setPath(d, "controller.selected", selected);
    const rebuilt: ControllerValues = {};
    for (const opt of entry?.config ?? []) {
      const v = values[opt.option_name];
      if (opt.option_type === "bool") rebuilt[opt.option_name] = !!v;
      else if (opt.option_type === "int") rebuilt[opt.option_name] = Math.round(Number(v));
      else if (opt.option_type === "float") rebuilt[opt.option_name] = Number(v);
    }
    d = setPath(d, `controller.config.${selected}`, rebuilt);
    setSaved(await save(d, ["controller_update"]));
  };

  return (
    <Section title="Controller">
      <Select
        label="Controller"
        value={selected}
        options={Object.entries(controllerMeta.metadata).map(([key, c]) => ({
          value: key,
          label: c.friendly_name,
        }))}
        onChange={(v) => setSelected(v)}
      />
      {entry?.description && <p className="pf-field-hint">{entry.description}</p>}
      {(entry?.config.length ?? 0) === 0 && (
        <p className="pf-field-hint">This controller has no configuration options.</p>
      )}
      {entry?.config.map((opt) => {
        if (opt.option_type === "bool") {
          return (
            <Toggle
              key={opt.option_name}
              label={opt.option_friendly_name}
              checked={!!values[opt.option_name]}
              onChange={(b) => set(opt.option_name, b)}
            />
          );
        }
        if (opt.option_type === "float" || opt.option_type === "int") {
          return (
            <NumberField
              key={opt.option_name}
              label={opt.option_friendly_name}
              value={Number(values[opt.option_name] ?? 0)}
              onChange={(n) => set(opt.option_name, n)}
              min={opt.option_min ?? undefined}
              max={opt.option_max ?? undefined}
            />
          );
        }
        return null;
      })}
      <div className="pf-settings-actions">
        <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </button>
        {saved && <span className="pf-settings-saved">Saved ✓</span>}
      </div>
    </Section>
  );
}
