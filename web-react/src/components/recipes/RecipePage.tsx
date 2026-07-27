import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { FileRequestError } from "../../helpers/files/apiEnvelope";
import { fetchRecipeDetail } from "../../helpers/files/recipeApi";
import type { RecipeDetail } from "../../helpers/files/recipeTypes";
import { deriveRunView } from "../../helpers/recipes/runStatus";
import { useShellState } from "../../helpers/shellContext";
import { IngredientsEditor } from "./IngredientsEditor";
import { InstructionsEditor } from "./InstructionsEditor";
import { RecipeAssetManager } from "./RecipeAssetManager";
import { RecipeMetaEditor } from "./RecipeMetaEditor";
import { RecipeRunStatus } from "./RecipeRunStatus";
import { RecipeView } from "./RecipeView";
import { StepsEditor } from "./StepsEditor";

// Built on the same shell as CookFilePage.tsx: useParams, a requestId/reload
// pair standing in for the single fetch effect, and a loading/error branch
// derived from render state (not a second mirrored flag). Loading/error live
// in this component while the read-only detail itself is RecipeView's job.
//
// Unlike CookFileDetail's 422, a `.pfrecipe` that fails to parse has no
// repair/upgrade path -- there is no upgrade_cookfile equivalent for
// recipes -- so any error here just renders the message and nothing else.
//
// Every editor below (meta/ingredients/instructions) takes this page's
// `reload` as its `onChanged`, the same wiring CookFilePage uses for
// CookFileMeta/CommentList: each editor calls its endpoint and then asks the
// PAGE to refetch the whole detail, rather than patching its own slice of
// state. That is load-bearing here specifically because renaming or deleting
// an ingredient rewrites/strips instructions the ingredient editor never
// rendered -- only a refetch of the whole detail keeps InstructionsEditor
// (and RecipeView) in sync with that cascade.
interface Problem {
  status: number;
  message: string;
}

interface Outcome {
  filename: string;
  id: number;
  problem: Problem | null;
}

function toProblem(err: unknown): Problem {
  if (err instanceof FileRequestError) {
    return { status: err.detail.status, message: err.detail.message };
  }
  return { status: 0, message: err instanceof Error ? err.message : "Request failed" };
}

export function RecipePage() {
  const { filename = "" } = useParams<{ filename: string }>();
  const { live } = useShellState();
  const [requestId, setRequestId] = useState(0);
  const [detail, setDetail] = useState<RecipeDetail | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  useEffect(() => {
    let cancelled = false;
    const id = requestId;
    fetchRecipeDetail(filename)
      .then((fresh) => {
        if (cancelled) return;
        setDetail(fresh);
        setOutcome({ filename, id, problem: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // A failed reload must not keep showing a stale recipe -- an edit
        // that just landed may have changed what is even valid to show.
        setDetail(null);
        setOutcome({ filename, id, problem: toProblem(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [filename, requestId]);

  const reload = () => setRequestId((n) => n + 1);

  // Plain render-time computation from state -- no effect, no mirrored state.
  // Comparing BOTH filename and id: filename guards a route change, id guards
  // reload() firing again for the SAME filename (a save just landed), same
  // idiom as CookFilePage.tsx.
  const loading = outcome === null || outcome.filename !== filename || outcome.id !== requestId;
  const problem = loading ? null : outcome.problem;

  const runView = deriveRunView(live.recipeStatus, filename);

  return (
    <div className="pf-settings">
      <div className="pf-settings-content pf-settings-content--wide">
        <Link className="pf-settings-back" to="/recipes">
          Back to recipes
        </Link>

        {loading && <p className="pf-settings-hint">Loading recipe…</p>}

        {problem && (
          <div className="pf-banner pf-banner--error">
            {problem.status === 404
              ? "That recipe is not in the recipes folder."
              : `Couldn't load this recipe: ${problem.message}`}
          </div>
        )}

        {detail && (
          <>
            <h2 className="pf-section-title">{detail.metadata.title || detail.filename}</h2>
            <RecipeRunStatus filename={filename} status={runView} />
            <RecipeView detail={detail} activeStep={runView.active ? runView.step : null} />

            <div className="pf-section">
              <h2 className="pf-section-title">Edit Recipe Details</h2>
              <div className="pf-section-body">
                <RecipeMetaEditor
                  file={filename}
                  metadata={detail.metadata}
                  steps={detail.recipe.steps}
                  onChanged={reload}
                />
              </div>
            </div>

            <div className="pf-section">
              <h2 className="pf-section-title">Edit Ingredients</h2>
              <div className="pf-section-body">
                <IngredientsEditor
                  file={filename}
                  ingredients={detail.recipe.ingredients}
                  instructions={detail.recipe.instructions}
                  onChanged={reload}
                />
              </div>
            </div>

            <div className="pf-section">
              <h2 className="pf-section-title">Edit Instructions</h2>
              <div className="pf-section-body">
                <InstructionsEditor
                  file={filename}
                  ingredients={detail.recipe.ingredients}
                  instructions={detail.recipe.instructions}
                  steps={detail.recipe.steps}
                  onChanged={reload}
                />
              </div>
            </div>

            <div className="pf-section">
              <h2 className="pf-section-title">Edit Program Steps</h2>
              <div className="pf-section-body">
                <StepsEditor
                  file={filename}
                  steps={detail.recipe.steps}
                  units={detail.metadata.units}
                  onChanged={reload}
                />
              </div>
            </div>

            <div className="pf-section">
              <h2 className="pf-section-title">Manage Photos</h2>
              <div className="pf-section-body">
                <RecipeAssetManager
                  file={filename}
                  parentId={detail.metadata.id}
                  assets={detail.assets}
                  splash={detail.metadata.image}
                  ingredients={detail.recipe.ingredients}
                  instructions={detail.recipe.instructions}
                  onChanged={reload}
                />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
