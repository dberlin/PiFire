import { useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { ControllerMetadata, Settings } from "../../../helpers/settings/settingsApi";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { TextField } from "../fields/TextField";
import { Toggle } from "../fields/Toggle";
import { SaveBar } from "../SaveBar";

type ControllerValues = Record<string, number | boolean | string>;

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
    } else if (opt.option_type === "list" || opt.option_type === "string") {
      const v = saved[opt.option_name];
      out[opt.option_name] =
        v !== undefined && v !== null
          ? String(v)
          : opt.option_default !== undefined && opt.option_default !== null
            ? String(opt.option_default)
            : "";
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
  const { save, saving, status } = useSaveSettings();

  const [selected, setSelected] = useState(() => readSelected(settings, controllerMeta));
  const [values, setValues] = useState<ControllerValues>(() =>
    deriveValues(readSelected(settings, controllerMeta), settings, controllerMeta),
  );
  const [prevSettings, setPrevSettings] = useState(settings);
  const [prevSelected, setPrevSelected] = useState(selected);

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
  const set = (name: string, val: number | boolean | string) =>
    setValues((v) => ({ ...v, [name]: val }));

  const onSave = async () => {
    let d: object = {};
    d = setPath(d, "controller.selected", selected);
    const rebuilt: ControllerValues = {};
    for (const opt of entry?.config ?? []) {
      const v = values[opt.option_name];
      if (opt.option_type === "bool") rebuilt[opt.option_name] = !!v;
      else if (opt.option_type === "int") rebuilt[opt.option_name] = Math.round(Number(v));
      else if (opt.option_type === "float") rebuilt[opt.option_name] = Number(v);
      else if (opt.option_type === "list") {
        // Flask leaves "list" uncoerced (raw HTML form string); we mirror that by
        // saving the string as-listed, but recover the original metadata value
        // type (e.g. a numeric list_values entry) when one matches.
        const strVal = String(v ?? "");
        const values_ = opt.list_values ?? [];
        const idx = values_.findIndex((lv) => String(lv) === strVal);
        rebuilt[opt.option_name] = idx >= 0 ? values_[idx] : strVal;
      } else if (opt.option_type === "string") rebuilt[opt.option_name] = String(v ?? "");
    }
    d = setPath(d, `controller.config.${selected}`, rebuilt);
    await save(d, ["controller_update"]);
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
        if (opt.option_type === "list") {
          const listValues = opt.list_values ?? [];
          const listLabels = opt.list_labels ?? [];
          return (
            <Select
              key={opt.option_name}
              label={opt.option_friendly_name}
              value={String(values[opt.option_name] ?? "")}
              options={listValues.map((lv, i) => ({
                value: String(lv),
                label: listLabels[i] ?? String(lv),
              }))}
              onChange={(v) => set(opt.option_name, v)}
            />
          );
        }
        if (opt.option_type === "string") {
          return (
            <TextField
              key={opt.option_name}
              label={opt.option_friendly_name}
              value={String(values[opt.option_name] ?? "")}
              onChange={(v) => set(opt.option_name, v)}
            />
          );
        }
        return null;
      })}
      <SaveBar onSave={onSave} saving={saving} status={status} />
    </Section>
  );
}
