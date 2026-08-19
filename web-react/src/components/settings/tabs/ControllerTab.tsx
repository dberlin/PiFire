import type { ControllerCatalog } from "@pifire/core/settings/controllerTypes";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useCallback, useState } from "react";
import { useOutletContext } from "react-router";
import { readSelected } from "../../../helpers/settings/controllerSelection";
import { setPath } from "../../../helpers/settings/delta";
import { SettingsFieldErrorsProvider } from "../../../helpers/settings/fieldErrorContext";
import { MPC_FAN_CONFLICT_MESSAGE, mpcFanConflict } from "../../../helpers/settings/mpcFan";
import { settingsDelta } from "../../../helpers/settings/settingsDelta";
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

function deriveValues(
  selected: string,
  settings: SettingsSchema,
  meta: ControllerCatalog | null,
): ControllerValues {
  const definition = meta?.metadata[selected];
  if (!definition) return {};
  const saved = settings.controller?.config?.[selected] ?? {};
  // Start from whatever is persisted, including any key the current metadata
  // no longer declares (a controller's option set can change between saves);
  // the loop below overwrites every declared key with its coerced value.
  const out: ControllerValues = {};
  for (const [name, value] of Object.entries(saved)) {
    if (value !== undefined) out[name] = value;
  }
  for (const opt of definition.config) {
    if (opt.option_type === "bool") {
      const value = saved[opt.option_name];
      out[opt.option_name] = typeof value === "boolean" ? value : !!opt.option_default;
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
    settings: SettingsSchema;
    mode: string;
    controllerMeta: ControllerCatalog | null;
  }>();
  const { save, saving, status, errors } = useSaveSettings();

  // Held on SettingsShell, so an unfinished edit survives a trip to another
  // tab. `selected` and `values` are ONE draft because they are one choice: the
  // config fields on screen are the fields the selected controller declares, so
  // changing the selection re-derives them in the same update rather than in a
  // second render-phase adjustment.
  const read = useCallback(
    (s: SettingsSchema) => {
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

  // Derived from the draft, so the error appears the moment the toggle is
  // flipped rather than after a save that would have been silently useless.
  const fanConflict = mpcFanConflict({
    selected,
    enableFanInput: !!values.enable_fan_input,
    settings,
  });

  const onSave = async () => {
    if (fanConflict) return; // do NOT call save(): the fan lever would be wired to nothing
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
    // Dropping it from what we send is not enough -- the backend MERGES, so an
    // omitted key survives -- hence the explicit delete paths below.
    const declared = new Set((entry?.config ?? []).map((opt) => opt.option_name));
    const unknownKeys = Object.keys(rebuilt).filter((key) => !declared.has(key));
    for (const key of unknownKeys) delete rebuilt[key];
    // Genuinely dynamic: which controller is selected is a runtime fact, and
    // controller.config stays an open dict server-side so an install can add
    // one. The generated type already models that as a `controller.config.${string}`
    // path with a `Record<string, string | number | boolean>` value, so no cast
    // is needed here.
    d = setPath(d, `controller.config.${selected}`, rebuilt);
    const ok = await save(
      settingsDelta(
        d,
        unknownKeys.map((key) => ["controller", "config", selected, key]),
      ),
      ["controller_update"],
    );
    setDroppedOptions(ok ? unknownKeys : []);
    if (ok) markSaved();
  };

  // Removing an option the controller has retired is the cleanup the
  // controller itself asks for, and the save that reports it SUCCEEDED, so it
  // is a notice rather than a save error -- which is what it read as while it
  // occupied SaveBar's error slot saying "These were not saved."
  const effectiveStatus: SaveStatus = status;
  const droppedNotice =
    status.kind !== "error" && droppedOptions.length > 0
      ? `Removed ${droppedOptions.join(", ")}: ${selected} no longer has ` +
        `${droppedOptions.length === 1 ? "that option" : "those options"}.`
      : null;

  return (
    <SettingsFieldErrorsProvider errors={errors}>
      <Section title="Controller">
        <Select
          label="Controller"
          value={selected}
          options={Object.entries(controllerMeta.metadata).map(([key, c]) => ({
            value: key,
            label: c!.friendly_name,
          }))}
          onChange={(v) => setSelected(v)}
          path="controller.selected"
        />
        {entry?.description && <p className="pf-field-hint">{entry.description}</p>}
        {(entry?.config.length ?? 0) === 0 && (
          <p className="pf-field-hint">This controller has no configuration options.</p>
        )}
        {entry?.config.map((opt) => {
          // controller.config is keyed by the selected controller, and this
          // loop covers exactly the option names it declares.
          const optionPath = `controller.config.${selected}.${opt.option_name}`;
          if (opt.option_type === "bool") {
            return (
              <Toggle
                key={opt.option_name}
                label={opt.option_friendly_name}
                checked={!!values[opt.option_name]}
                onChange={(b) => set(opt.option_name, b)}
                hint={opt.option_description}
                path={optionPath}
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
                hint={opt.option_description}
                path={optionPath}
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
                hint={opt.option_description}
                path={optionPath}
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
                hint={opt.option_description}
                path={optionPath}
              />
            );
          }
          return null;
        })}
        {fanConflict && (
          <p className="pf-settings-error-text" role="alert">
            {MPC_FAN_CONFLICT_MESSAGE}
          </p>
        )}
        {droppedNotice !== null && (
          <p className="pf-settings-hint" role="status">
            {droppedNotice}
          </p>
        )}
        <SaveBar onSave={onSave} saving={saving} status={effectiveStatus} dirty={dirty} />
      </Section>
    </SettingsFieldErrorsProvider>
  );
}
