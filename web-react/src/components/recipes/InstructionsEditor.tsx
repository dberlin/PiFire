import { useState } from "react";
import {
  addInstruction,
  deleteInstruction,
  updateInstruction,
} from "../../helpers/files/recipeApi";
import type { Ingredient, Instruction, RecipeStep } from "../../helpers/contracts/content.gen";
import { ConfirmAction } from "../dashboard/ConfirmAction";

// recipes_api.py's update_instruction rejects any ingredient name that is not
// CURRENTLY in recipe["ingredients"] (400 data.field == "ingredients"), so the
// picker below is a multi-select over the live ingredient list rather than
// free text -- there is no way for this UI to construct a name the endpoint
// would refuse.
//
// `step` is an INDEX into recipe.steps, and step 0 is merely labelled "Prep"
// rather than "Step 0" -- the value and the label are separate things
// (_macro_recipes.html:375-384). Numbering the options 1..N instead would both
// store the wrong index for every step and offer a final option pointing one
// past the end of the array.
//
// Like IngredientsEditor, `onChanged` asks the PAGE to refetch the whole
// detail rather than patching local state.

interface Props {
  file: string;
  ingredients: Ingredient[];
  instructions: Instruction[];
  steps: RecipeStep[];
  onChanged: () => void;
}

function sameNames(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((name) => b.includes(name));
}

function toggle(names: string[], name: string, checked: boolean): string[] {
  return checked ? [...names, name] : names.filter((n) => n !== name);
}

function InstructionRow({
  file,
  index,
  instruction,
  ingredients,
  steps,
  onRequestDelete,
  onChanged,
}: {
  file: string;
  index: number;
  instruction: Instruction;
  ingredients: Ingredient[];
  steps: RecipeStep[];
  onRequestDelete: () => void;
  onChanged: () => void;
}) {
  // Render-phase reseed -- same idiom as IngredientsEditor's row: a refetch
  // triggered by the ingredients editor (a rename/delete cascades into THIS
  // instruction's own `ingredients` list) must land immediately, not after an
  // effect tick.
  const [draft, setDraft] = useState({
    text: instruction.text,
    ingredients: instruction.ingredients,
    step: instruction.step,
  });
  const [seeded, setSeeded] = useState(instruction);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (instruction !== seeded) {
    setSeeded(instruction);
    setDraft({
      text: instruction.text,
      ingredients: instruction.ingredients,
      step: instruction.step,
    });
    setError(null);
  }

  const dirty =
    draft.text !== instruction.text ||
    draft.step !== instruction.step ||
    !sameNames(draft.ingredients, instruction.ingredients);

  const save = () => {
    setBusy(true);
    setError(null);
    updateInstruction(file, index, draft.text, draft.ingredients, draft.step)
      .then(() => onChanged())
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Save failed."))
      .finally(() => setBusy(false));
  };

  return (
    <div className="pf-rcp-instruction-row">
      <label className="pf-field pf-field-column">
        <span className="pf-field-label">{`Direction ${index + 1}`}</span>
        <textarea
          className="pf-input"
          rows={2}
          value={draft.text}
          onChange={(e) => setDraft((d) => ({ ...d, text: e.target.value }))}
        />
      </label>

      <fieldset className="pf-field pf-field-column">
        <legend className="pf-field-label">{`Ingredients used in direction ${index + 1}`}</legend>
        {ingredients.length === 0 && (
          <p className="pf-settings-hint">No ingredients to choose from yet.</p>
        )}
        {ingredients.map((ingredient, i) => (
          // Ingredient rows have no id besides position, same as
          // IngredientsEditor.
          <label className="pf-rcp-ingredient-check" key={i}>
            <input
              type="checkbox"
              checked={draft.ingredients.includes(ingredient.name)}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  ingredients: toggle(d.ingredients, ingredient.name, e.target.checked),
                }))
              }
            />
            {ingredient.name || "(unnamed ingredient)"}
          </label>
        ))}
      </fieldset>

      <label className="pf-field">
        <span className="pf-field-label">{`Program step for direction ${index + 1}`}</span>
        <select
          className="pf-input"
          value={draft.step}
          onChange={(e) => setDraft((d) => ({ ...d, step: Number(e.target.value) }))}
        >
          {/* One option per program step, valued by its own array index --
              _macro_recipes.html:375-384 iterates the steps and emits
              `value="{{ loop.index0 }}"`. So the count matches steps exactly,
              and index 0 is labelled Prep rather than being an extra entry. */}
          {steps.map((_, i) => (
            <option key={i} value={i}>
              {i === 0 ? "Prep" : `Step ${i}`}
            </option>
          ))}
        </select>
      </label>

      {error && <div className="pf-settings-error-text">{error}</div>}

      <div className="pf-rcp-row-actions">
        <button
          type="button"
          className="pf-modal-btn accent"
          disabled={busy || !dirty}
          onClick={save}
        >
          {`Save direction ${index + 1}`}
        </button>
        <button type="button" className="pf-modal-btn danger" onClick={onRequestDelete}>
          {`Delete direction ${index + 1}`}
        </button>
      </div>
    </div>
  );
}

export function InstructionsEditor({ file, ingredients, instructions, steps, onChanged }: Props) {
  const [adding, setAdding] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);

  const addRow = () => {
    setAdding(true);
    addInstruction(file)
      .then(() => onChanged())
      .finally(() => setAdding(false));
  };

  return (
    <>
      {instructions.length === 0 && <p className="pf-settings-hint">No instructions listed.</p>}

      {/* Rows have no stable id -- the endpoint itself addresses them by index. */}
      {instructions.map((instruction, index) => (
        <InstructionRow
          key={index}
          file={file}
          index={index}
          instruction={instruction}
          ingredients={ingredients}
          steps={steps}
          onRequestDelete={() => setPendingDelete(index)}
          onChanged={onChanged}
        />
      ))}

      <button type="button" className="pf-modal-btn accent" disabled={adding} onClick={addRow}>
        Add instruction
      </button>

      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete this instruction?"
        message="The direction text is removed. Nothing else references an instruction, so nothing else changes."
        onConfirm={() => {
          const index = pendingDelete;
          setPendingDelete(null);
          if (index !== null) deleteInstruction(file, index).then(() => onChanged());
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
