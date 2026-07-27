import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { FileRequestError } from "../../helpers/files/apiEnvelope";
import { fetchRecipeDetail } from "../../helpers/files/recipeApi";
import type { RecipeDetail } from "../../helpers/files/recipeTypes";
import { deriveRunView } from "../../helpers/recipes/runStatus";
import { useShellState } from "../../helpers/shellContext";
import { RecipeRunStatus } from "./RecipeRunStatus";
import { RecipeView } from "./RecipeView";

// Built on the same shell as CookFilePage.tsx: useParams, one fetch, a
// loading/error branch derived from render state (not a second mirrored
// flag), and Loading/error live in this component while the read-only detail
// itself is RecipeView's job.
//
// Unlike CookFileDetail's 422, a `.pfrecipe` that fails to parse has no
// repair/upgrade path -- there is no upgrade_cookfile equivalent for
// recipes -- so any error here just renders the message and nothing else.
interface Problem {
  status: number;
  message: string;
}

interface Outcome {
  filename: string;
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
  const [detail, setDetail] = useState<RecipeDetail | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRecipeDetail(filename)
      .then((fresh) => {
        if (cancelled) return;
        setDetail(fresh);
        setOutcome({ filename, problem: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDetail(null);
        setOutcome({ filename, problem: toProblem(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [filename]);

  // Plain render-time computation from state -- no effect, no mirrored state.
  const loading = outcome === null || outcome.filename !== filename;
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
          </>
        )}
      </div>
    </div>
  );
}
