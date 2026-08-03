import { useCallback, useState } from "react";
import { useOutletContext } from "react-router";
import { setPath } from "../../../helpers/settings/delta";
import type { ControllerMetadata, Settings } from "../../../helpers/settings/settingsApi";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import type { SaveStatus } from "../../../helpers/settings/useSaveSettings";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import { NumberField } from "../fields/NumberField";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";
import { TextField } from "../fields/TextField";
import { Toggle } from "../fields/Toggle";
import { SaveBar } from "../SaveBar";

/** Working state for whichever controller is selected. The option names it is
 *  keyed by come from the runtime metadata list, not from a single static
 *  shape, so it stays a loose bag while the user is editing. */
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
  // Start from whatever is persisted, including any key the current metadata
  // no longer declares (a controller's option set can change between saves);
  // the loop below overwrites every declared key with its coerced value.
  const out: ControllerValues = { ...saved };
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

  // Held on SettingsShell, so an unfinished edit survives a trip to another
  // tab. `selected` and `values` are ONE draft because they are one choice: the
  // config fields on screen are the fields the selected controller declares, so
  // changing the selection re-derives them in the same update rather than in a
  // second render-phase adjustment.
  const read = useCallback(
    (s: Settings) => {
      const selected = readSelected(s, controllerMeta);
      return { selected, values: deriveValues(selected, s, controllerMeta) };
    },
    [controllerMeta],
  );
  const { value: v, setValue: setV, dirty, markSaved } = useSettingsDraft("controller", read);
  const { selected, values } = v;
  // Names dropped from the last save because the selected controller does not
  // declare them. Cleared on selection change: a new controller has its own
  // declared set, and the names no longer describe the fields on screen.
  const [droppedOptions, setDroppedOptions] = useState<string[]>([]);
  const setSelected = (next: string) => {
    setDroppedOptions([]);
    setV({ selected: next, values: deriveValues(next, settings, controllerMeta) });
  };

  if (!controllerMeta) {
    return (
      <Section title="Controller">
        <p className="pf-settings-error">Controller metadata unavailable.</p>
      </Section>
    );
  }

  const entry = controllerMeta.metadata[selected];
  const set = (name: string, val: number | boolean | string) =>
    setV((d) => ({ ...d, values: { ...d.values, [name]: val } }));

  const onSave = async () => {
    let d: object = {};
    d = setPath(d, "controller.selected", selected);
    // Start from the full working bag so a name the selected controller does
    // not declare (carried over from what was persisted) survives long enough
    // to be caught below, instead of just vanishing from the declared-only loop.
    const rebuilt: ControllerValues = { ...values };
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
    // The declared option names are a runtime fact from /api/controller_metadata,
    // not something a generated type can check (the selected controller is a
    // runtime string, so it can never index the generated map statically). Drop
    // anything the selected controller does not declare rather than persist it.
    const declared = new Set((entry?.config ?? []).map((opt) => opt.option_name));
    const unknownKeys = Object.keys(rebuilt).filter((key) => !declared.has(key));
    for (const key of unknownKeys) delete rebuilt[key];
    // Genuinely dynamic: which controller is selected is a runtime fact, and
    // controller.config stays an open dict server-side so an install can add
    // one. The generated type already models that as a `controller.config.${string}`
    // path with a `Record<string, string | number | boolean>` value, so no cast
    // is needed here.
    d = setPath(d, `controller.config.${selected}`, rebuilt);
    const ok = await save(d, ["controller_update"]);
    setDroppedOptions(ok ? unknownKeys : []);
    if (ok) markSaved();
  };

  // A dropped-options warning takes the same slot SaveBar already renders a
  // save error in; a genuine save failure (nothing persisted) takes priority
  // over it, since droppedOptions only reflects the save that just succeeded.
  const effectiveStatus: SaveStatus =
    status.kind !== "error" && droppedOptions.length > 0
      ? {
          kind: "error",
          message: `${selected} does not have options named: ${droppedOptions.join(", ")}. These were not saved.`,
        }
      : status;

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
              step={opt.option_step ?? undefined}
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
      <SaveBar onSave={onSave} saving={saving} status={effectiveStatus} dirty={dirty} />
    </Section>
  );
}
