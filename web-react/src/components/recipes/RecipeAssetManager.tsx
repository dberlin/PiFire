import { useId, useState } from "react";
import {
  assetUrl,
  deleteRecipeAssets,
  type RecipeAssetSection,
  setRecipeAssets,
  uploadRecipeAssets,
} from "../../helpers/files/recipeApi";
import type { Ingredient, Instruction, RecipeAsset } from "../../helpers/contracts/content.gen";
import { ConfirmAction } from "../dashboard/ConfirmAction";

// The recipe's asset library: upload, delete-from-archive, and attaching a
// photo to one of the recipe's THREE asset-bearing sections
// (recipes_api.py's set_assets). Templates: MediaPanel.tsx for the
// upload/grid/select/delete shell, CommentList.tsx's `picking` modal for the
// per-item whole-list attach flow -- recipes attach per INGREDIENT/
// INSTRUCTION index rather than per comment, and `splash` is a single
// recipe-wide choice (0 or 1 assets) rather than a list, so it gets its own
// per-asset "use as splash" action instead of a picker.
//
// Every write here is a WHOLE-LIST replace, never a per-item toggle: Flask
// infers add-versus-remove from a `state` string the client sends
// (blueprints/recipes/routes.py:396-428), and a stale client view of that
// direction silently inverts the request. Sending the complete resulting
// list sidesteps that entirely.

interface Props {
  file: string;
  /** metadata.id -- the /static/img/tmp/{id} folder assets are served from. */
  parentId: string;
  assets: RecipeAsset[];
  /** metadata.image -- "" when the recipe has no splash image set. */
  splash: string;
  ingredients: Ingredient[];
  instructions: Instruction[];
  onChanged: () => void;
}

interface Picking {
  section: Extract<RecipeAssetSection, "ingredients" | "instructions">;
  index: number;
  label: string;
  selected: string[];
}

export function RecipeAssetManager({
  file,
  parentId,
  assets,
  splash,
  ingredients,
  instructions,
  onChanged,
}: Props) {
  const [selected, setSelected] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [picking, setPicking] = useState<Picking | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const uploadId = useId();

  const run = (work: Promise<unknown>, onDone?: () => void) => {
    setBusy(true);
    setError(null);
    work
      .then(() => {
        onDone?.();
        onChanged();
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Request failed"))
      .finally(() => setBusy(false));
  };

  const onUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const images = Array.from(event.target.files ?? []);
    // Cleared so re-picking the same file fires change again.
    event.target.value = "";
    if (images.length === 0) return;
    run(uploadRecipeAssets(file, images));
  };

  return (
    <>
      {error && <div className="pf-banner pf-banner--error">{error}</div>}

      <div className="pf-rcp-toolbar">
        <label htmlFor={uploadId} className="pf-modal-btn accent">
          Upload photos
        </label>
        <input
          id={uploadId}
          type="file"
          accept="image/*"
          multiple
          aria-label="Upload photos"
          onChange={onUpload}
          className="pf-rcp-file-input"
        />
        <button
          type="button"
          className="pf-modal-btn danger"
          disabled={busy || selected.length === 0}
          onClick={() => setConfirming(true)}
        >
          {`Remove selected photos (${selected.length})`}
        </button>
        {splash && (
          <button
            type="button"
            className="pf-modal-btn"
            disabled={busy}
            onClick={() => run(setRecipeAssets(file, "splash", []))}
          >
            Clear splash image
          </button>
        )}
      </div>

      {assets.length === 0 ? (
        <p className="pf-settings-hint">No photos yet — upload one to illustrate this recipe.</p>
      ) : (
        <div className="pf-rcp-media-grid">
          {assets.map((asset) => {
            const isSplash = asset.filename === splash;
            const isSelected = selected.includes(asset.filename);
            return (
              <div className="pf-rcp-media-item" key={asset.id}>
                <a href={assetUrl(parentId, asset.filename)} target="_blank" rel="noreferrer">
                  <img
                    className={`pf-rcp-media-img${isSplash ? " pf-rcp-media-img--selected" : ""}`}
                    src={assetUrl(parentId, asset.filename)}
                    alt={asset.filename}
                  />
                </a>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`Select ${asset.filename}`}
                    checked={isSelected}
                    onChange={(e) =>
                      setSelected((current) =>
                        e.target.checked
                          ? [...current, asset.filename]
                          : current.filter((name) => name !== asset.filename),
                      )
                    }
                  />{" "}
                  Select
                </label>
                <button
                  type="button"
                  className="pf-modal-btn"
                  disabled={busy || isSplash}
                  onClick={() => run(setRecipeAssets(file, "splash", [asset.filename]))}
                >
                  {isSplash ? "Current splash image" : `Use ${asset.filename} as splash image`}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {assets.length > 0 && (
        <>
          <div className="pf-rcp-attach-heading">Attach photos to an ingredient</div>
          {ingredients.length === 0 && (
            <p className="pf-settings-hint">No ingredients to attach photos to yet.</p>
          )}
          {ingredients.map((ingredient, index) => {
            const label = ingredient.name || `ingredient ${index + 1}`;
            return (
              <button
                key={index}
                type="button"
                className="pf-modal-btn"
                onClick={() =>
                  setPicking({
                    section: "ingredients",
                    index,
                    label,
                    selected: [...ingredient.assets],
                  })
                }
              >
                {`Manage photos for ${label}`}
              </button>
            );
          })}

          <div className="pf-rcp-attach-heading">Attach photos to an instruction</div>
          {instructions.length === 0 && (
            <p className="pf-settings-hint">No instructions to attach photos to yet.</p>
          )}
          {instructions.map((instruction, index) => (
            <button
              key={index}
              type="button"
              className="pf-modal-btn"
              onClick={() =>
                setPicking({
                  section: "instructions",
                  index,
                  label: `direction ${index + 1}`,
                  selected: [...instruction.assets],
                })
              }
            >
              {`Manage photos for direction ${index + 1}`}
            </button>
          ))}
        </>
      )}

      {picking && (
        <div className="pf-modal-scrim">
          <div className="pf-modal">
            <div className="pf-modal-title">{`Attach photos to ${picking.label}`}</div>
            <div className="pf-rcp-media-grid">
              {assets.map((asset) => {
                const isSelected = picking.selected.includes(asset.filename);
                return (
                  <label className="pf-rcp-media-item" key={asset.id}>
                    <img
                      className={`pf-rcp-media-img${isSelected ? " pf-rcp-media-img--selected" : ""}`}
                      src={assetUrl(parentId, asset.filename)}
                      alt=""
                    />
                    <input
                      type="checkbox"
                      aria-label={`Attach ${asset.filename}`}
                      checked={isSelected}
                      onChange={(e) =>
                        setPicking((current) =>
                          current === null
                            ? current
                            : {
                                ...current,
                                selected: e.target.checked
                                  ? [...current.selected, asset.filename]
                                  : current.selected.filter((name) => name !== asset.filename),
                              },
                        )
                      }
                    />
                  </label>
                );
              })}
            </div>
            <div className="pf-modal-actions">
              <button type="button" className="pf-modal-btn" onClick={() => setPicking(null)}>
                Cancel attach
              </button>
              <button
                type="button"
                className="pf-modal-btn accent"
                disabled={busy}
                onClick={() => {
                  const target = picking;
                  run(setRecipeAssets(file, target.section, target.selected, target.index), () =>
                    setPicking(null),
                  );
                }}
              >
                Save attachments
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmAction
        open={confirming}
        title={`Remove ${selected.length} ${selected.length === 1 ? "photo" : "photos"}?`}
        message="The images are deleted from the recipe archive, and from the splash image, ingredient or instruction that used them."
        onConfirm={() => {
          setConfirming(false);
          run(deleteRecipeAssets(file, selected), () => setSelected([]));
        }}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
