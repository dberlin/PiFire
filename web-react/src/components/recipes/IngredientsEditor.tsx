import type { Ingredient, Instruction } from "@pifire/core/contracts/content";
import { useState } from "react";
import { addIngredient, deleteIngredient, updateIngredient } from "../../helpers/files/recipeApi";
import { ConfirmAction } from "../dashboard/ConfirmAction";

// recipes_api.py's cascade: renaming ingredient `index` rewrites its OLD name
// to the new one inside every instruction that referenced it; deleting it
// strips the name out of every instruction that referenced it. Both are
// server-side rewrites of recipe.json this editor never renders -- `onChanged`
// asks the PAGE to refetch the whole detail (RecipePage.tsx), which is what
// keeps InstructionsEditor, rendered as a sibling rather than a child, in sync
// with rows this editor itself never touched.

interface Props {
  file: string;
  ingredients: Ingredient[];
  instructions: Instruction[];
  onChanged: () => void;
}

function referenceCount(instructions: Instruction[], name: string): number {
  return instructions.filter((instruction) => instruction.ingredients.includes(name)).length;
}

function IngredientRow({
  file,
  index,
  ingredient,
  onRequestDelete,
  onChanged,
}: {
  file: string;
  index: number;
  ingredient: Ingredient;
  onRequestDelete: () => void;
  onChanged: () => void;
}) {
  // Render-phase reseed, not useEffect + setState -- same idiom as
  // CookFileMeta's LabelRow: a refetch triggered by a SIBLING row's save (or
  // by InstructionsEditor) must not leave a stale draft on screen for a frame.
  const [draft, setDraft] = useState({ name: ingredient.name, quantity: ingredient.quantity });
  const [seeded, setSeeded] = useState(ingredient);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (ingredient !== seeded) {
    setSeeded(ingredient);
    setDraft({ name: ingredient.name, quantity: ingredient.quantity });
    setError(null);
  }

  const dirty = draft.name !== ingredient.name || draft.quantity !== ingredient.quantity;

  const save = () => {
    setBusy(true);
    setError(null);
    updateIngredient(file, index, draft.name, draft.quantity)
      .then(() => onChanged())
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Save failed."))
      .finally(() => setBusy(false));
  };

  return (
    <tr>
      <td>
        <input
          className="pf-input"
          aria-label={`Quantity for ingredient ${index + 1}`}
          value={draft.quantity}
          onChange={(e) => setDraft((d) => ({ ...d, quantity: e.target.value }))}
        />
      </td>
      <td>
        <input
          className="pf-input"
          aria-label={`Name for ingredient ${index + 1}`}
          value={draft.name}
          onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
        />
        {error && <div className="pf-settings-error-text">{error}</div>}
      </td>
      <td className="pf-rcp-actions-col">
        <div className="pf-rcp-row-actions">
          <button
            type="button"
            className="pf-modal-btn accent"
            disabled={busy || !dirty}
            onClick={save}
          >
            {`Save ingredient ${index + 1}`}
          </button>
          <button type="button" className="pf-modal-btn danger" onClick={onRequestDelete}>
            {`Delete ingredient ${index + 1}`}
          </button>
        </div>
      </td>
    </tr>
  );
}

export function IngredientsEditor({ file, ingredients, instructions, onChanged }: Props) {
  const [adding, setAdding] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);

  const addRow = () => {
    setAdding(true);
    addIngredient(file)
      .then(() => onChanged())
      .finally(() => setAdding(false));
  };

  const confirmName = pendingDelete === null ? "" : ingredients[pendingDelete].name;
  const confirmRefs = pendingDelete === null ? 0 : referenceCount(instructions, confirmName);

  return (
    <>
      {ingredients.length > 0 ? (
        <table className="pf-rcp-table">
          <thead>
            <tr>
              <th>Quantity</th>
              <th>Ingredient</th>
              <th> </th>
            </tr>
          </thead>
          <tbody>
            {ingredients.map((ingredient, index) => (
              <IngredientRow
                // Rows have no stable id -- the endpoint itself addresses
                // them by index.
                key={index}
                file={file}
                index={index}
                ingredient={ingredient}
                onRequestDelete={() => setPendingDelete(index)}
                onChanged={onChanged}
              />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="pf-settings-hint">No ingredients listed.</p>
      )}

      <button type="button" className="pf-modal-btn accent" disabled={adding} onClick={addRow}>
        Add ingredient
      </button>

      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete this ingredient?"
        message={
          confirmRefs > 0
            ? `“${confirmName}” is used in ${confirmRefs} instruction${confirmRefs === 1 ? "" : "s"}. Deleting it removes it from all of them.`
            : `“${confirmName}” will be removed.`
        }
        onConfirm={() => {
          const index = pendingDelete;
          setPendingDelete(null);
          if (index !== null) deleteIngredient(file, index).then(() => onChanged());
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
