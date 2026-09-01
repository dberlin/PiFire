import type { RecipeStep } from "@pifire/core/contracts/content";
import { Fragment, useState } from "react";

import { deleteStep, insertStep, updateStep } from "../../helpers/files/recipeApi";
import { ConfirmAction } from "../dashboard/ConfirmAction";
import { NumberField } from "../settings/fields/NumberField";
import { Select } from "../settings/fields/Select";
import { Toggle } from "../settings/fields/Toggle";

// The program-step editor. recipes_api.py's insert_step is POSITIONAL
// (index == steps.length appends at the end, anything else shifts later
// steps down), so the add control below is "insert above/at the end" at
// every position rather than a single trailing "Add step" button that would
// only ever append.
//
// `0` is the disabled sentinel for hold_temp and every trigger_temps member
// (controller.py reads 0 as "no trigger"), not a missing value -- an enable
// switch OFF writes 0 and the paired NumberField goes `disabled` rather than
// rendering 0 as if it were a real temperature. See TriggerField below.
//
// The mode select only ever offers Smoke/Hold, matching the legacy macro
// (_macro_recipes.html:480-487) -- Startup/Shutdown are controller-owned
// transitions, not something this editor constructs. A Startup/Shutdown step
// already in the recipe (seeded defaults, an uploaded recipe) still has to
// RENDER, so those rows fall back to a read-only card instead of the edit
// form, the same split Flask's own template makes
// (render_recipe_step_startup / render_recipe_step_generic vs.
// render_recipe_edit_step_active).

interface Props {
  file: string;
  steps: RecipeStep[];
  units: string;
  onChanged: () => void;
}

const MODE_OPTIONS = [
  { value: "Smoke", label: "Smoke" },
  { value: "Hold", label: "Hold" },
];

function maxTempFor(units: string): number {
  return units === "F" ? 600 : 300;
}

function sameStep(a: RecipeStep, b: RecipeStep): boolean {
  return (
    a.mode === b.mode &&
    a.hold_temp === b.hold_temp &&
    a.timer === b.timer &&
    a.notify === b.notify &&
    a.pause === b.pause &&
    a.message === b.message &&
    a.trigger_temps.primary === b.trigger_temps.primary &&
    a.trigger_temps.food.length === b.trigger_temps.food.length &&
    a.trigger_temps.food.every((t, i) => t === b.trigger_temps.food[i])
  );
}

/** One enable-switch-plus-value pair. Turning the switch ON seeds a usable
 * default (matching the legacy macro's own jQuery: 100 for a temperature
 * trigger, 1 for the timer) rather than leaving 0 sitting in a now-editable
 * field; turning it OFF writes 0 and disables the field, so a disabled field
 * showing "0" is never confused with an armed trigger of 0 degrees/minutes --
 * 0 cannot occur while the switch is on. */
function TriggerField({
  switchLabel,
  fieldLabel,
  value,
  onChange,
  max,
  suffix,
  enableTo,
}: {
  switchLabel: string;
  fieldLabel: string;
  value: number;
  onChange: (v: number) => void;
  max?: number;
  suffix: string;
  enableTo: number;
}) {
  const enabled = value > 0;
  return (
    <div className="pf-rcp-trigger-row">
      <Toggle
        label={switchLabel}
        checked={enabled}
        onChange={(on) => onChange(on ? enableTo : 0)}
      />
      <NumberField
        label={fieldLabel}
        value={value}
        onChange={onChange}
        min={0}
        max={max}
        suffix={suffix}
        disabled={!enabled}
      />
    </div>
  );
}

function ReadOnlyStepRow({
  step,
  index,
  onRequestDelete,
}: {
  step: RecipeStep;
  index: number;
  onRequestDelete: () => void;
}) {
  return (
    <div className="pf-rcp-step-edit">
      <div className="pf-rcp-step-edit-header">{`Step ${index} -- ${step.mode}`}</div>
      {step.mode === "Startup" && (
        <p className="pf-settings-hint">Transitions once startup completes successfully.</p>
      )}
      {step.mode === "Shutdown" && (
        <p className="pf-settings-hint">Runs the controller's shutdown sequence.</p>
      )}
      <div className="pf-rcp-row-actions">
        <button type="button" className="pf-modal-btn danger" onClick={onRequestDelete}>
          {`Delete step ${index}`}
        </button>
      </div>
    </div>
  );
}

function EditableStepRow({
  file,
  index,
  step,
  units,
  onRequestDelete,
  onChanged,
}: {
  file: string;
  index: number;
  step: RecipeStep;
  units: string;
  onRequestDelete: () => void;
  onChanged: () => void;
}) {
  // Render-phase reseed -- same idiom as IngredientsEditor's/InstructionsEditor's
  // rows: a refetch triggered by a sibling save, or by lowering food_probes in
  // RecipeMetaEditor (which reshapes every step's trigger_temps.food), must
  // land immediately rather than after an effect tick.
  const [draft, setDraft] = useState(step);
  const [seeded, setSeeded] = useState(step);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (step !== seeded) {
    setSeeded(step);
    setDraft(step);
    setError(null);
  }

  const dirty = !sameStep(draft, step);
  const maxTemp = maxTempFor(units);

  const set = <K extends keyof RecipeStep>(key: K, value: RecipeStep[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const setFoodTrigger = (i: number, value: number) =>
    setDraft((d) => ({
      ...d,
      trigger_temps: {
        ...d.trigger_temps,
        food: d.trigger_temps.food.map((t, idx) => (idx === i ? value : t)),
      },
    }));

  const save = () => {
    setBusy(true);
    setError(null);
    updateStep(file, index, draft)
      .then(() => onChanged())
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Save failed."))
      .finally(() => setBusy(false));
  };

  return (
    <div className="pf-rcp-step-edit">
      <div className="pf-rcp-step-edit-header">{`Step ${index}`}</div>

      <Select
        label="Mode"
        value={draft.mode}
        options={MODE_OPTIONS}
        onChange={(value) => {
          if (value === "Smoke" || value === "Hold") set("mode", value);
        }}
      />

      {draft.mode === "Hold" && (
        <NumberField
          label="Hold temperature"
          value={draft.hold_temp}
          onChange={(v) => set("hold_temp", v)}
          min={0}
          max={maxTemp}
          suffix={`°${units}`}
        />
      )}

      <TriggerField
        switchLabel="Enable primary trigger"
        fieldLabel="Primary trigger temperature"
        value={draft.trigger_temps.primary}
        onChange={(v) => set("trigger_temps", { ...draft.trigger_temps, primary: v })}
        max={maxTemp}
        suffix={`°${units}`}
        enableTo={100}
      />

      {draft.trigger_temps.food.map((t, i) => (
        <TriggerField
          key={i}
          switchLabel={`Enable food probe ${i + 1} trigger`}
          fieldLabel={`Food probe ${i + 1} trigger temperature`}
          value={t}
          onChange={(v) => setFoodTrigger(i, v)}
          max={maxTemp}
          suffix={`°${units}`}
          enableTo={100}
        />
      ))}

      {/* Minutes, not a bare count -- controller.py multiplies by 60. */}
      <TriggerField
        switchLabel="Enable timer"
        fieldLabel="Timer (minutes)"
        value={draft.timer}
        onChange={(v) => set("timer", v)}
        suffix="min"
        enableTo={1}
      />

      <Toggle label="Pause for input" checked={draft.pause} onChange={(v) => set("pause", v)} />
      <Toggle
        label="Send a notification"
        checked={draft.notify}
        onChange={(v) => set("notify", v)}
      />

      <label className="pf-field pf-field-column">
        <span className="pf-field-label">Notification message</span>
        <textarea
          className="pf-input"
          rows={2}
          value={draft.message}
          onChange={(e) => set("message", e.target.value)}
        />
      </label>

      {error && <div className="pf-settings-error-text">{error}</div>}

      <div className="pf-rcp-row-actions">
        <button
          type="button"
          className="pf-modal-btn accent"
          disabled={busy || !dirty}
          onClick={save}
        >
          {`Save step ${index}`}
        </button>
        <button type="button" className="pf-modal-btn danger" onClick={onRequestDelete}>
          {`Delete step ${index}`}
        </button>
      </div>
    </div>
  );
}

function InsertControl({
  file,
  index,
  label,
  onChanged,
}: {
  file: string;
  index: number;
  label: string;
  onChanged: () => void;
}) {
  const [inserting, setInserting] = useState(false);
  return (
    <div className="pf-rcp-insert-row">
      <button
        type="button"
        className="pf-modal-btn"
        disabled={inserting}
        onClick={() => {
          setInserting(true);
          insertStep(file, index)
            .then(() => onChanged())
            .finally(() => setInserting(false));
        }}
      >
        {label}
      </button>
    </div>
  );
}

export function StepsEditor({ file, steps, units, onChanged }: Props) {
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);

  return (
    <>
      {steps.length === 0 && <p className="pf-settings-hint">No program steps yet.</p>}

      <InsertControl
        file={file}
        index={0}
        label={steps.length === 0 ? "Insert a step" : "Insert a step above Step 0"}
        onChanged={onChanged}
      />

      {/* Rows have no stable id -- the endpoint itself addresses them by index. */}
      {steps.map((step, index) => (
        <Fragment key={index}>
          {step.mode === "Smoke" || step.mode === "Hold" ? (
            <EditableStepRow
              file={file}
              index={index}
              step={step}
              units={units}
              onRequestDelete={() => setPendingDelete(index)}
              onChanged={onChanged}
            />
          ) : (
            <ReadOnlyStepRow
              step={step}
              index={index}
              onRequestDelete={() => setPendingDelete(index)}
            />
          )}
          <InsertControl
            file={file}
            index={index + 1}
            label={
              index + 1 < steps.length
                ? `Insert a step above Step ${index + 1}`
                : "Insert a step at the end"
            }
            onChanged={onChanged}
          />
        </Fragment>
      ))}

      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete this step?"
        message="The program step is removed. Any instruction pointing at it, or at a later step, keeps its own stored index -- check the instruction list after deleting."
        onConfirm={() => {
          const index = pendingDelete;
          setPendingDelete(null);
          if (index !== null) deleteStep(file, index).then(() => onChanged());
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
