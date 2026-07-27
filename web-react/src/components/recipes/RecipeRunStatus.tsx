import { useState } from "react";
import { FileRequestError } from "../../helpers/files/apiEnvelope";
import { runRecipe } from "../../helpers/files/recipeApi";
import type { RecipeRunView } from "../../helpers/recipes/runStatus";
import { ConfirmAction } from "../dashboard/ConfirmAction";

interface Props {
  filename: string;
  status: RecipeRunView;
}

// The one destructive action on the page: it starts a real cook. Disabled
// unless the grill is stopped (status.canRun), and confirmed through the same
// modal the dashboard's own destructive actions use.
export function RecipeRunStatus({ filename, status }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = () => {
    setConfirming(false);
    setStarting(true);
    setError(null);
    runRecipe(filename)
      .catch((err: unknown) => {
        // A 409 not_stopped means the grill left "Stop" in the gap between
        // render and click -- surface the endpoint's own message rather than
        // a generic failure.
        setError(
          err instanceof FileRequestError ? err.detail.message : "Couldn't start this recipe.",
        );
      })
      .finally(() => setStarting(false));
  };

  return (
    <div className="pf-rcp-run">
      {status.otherFilename && (
        <p className="pf-banner pf-banner--error">
          {`${status.otherFilename} is currently running. Stop it before starting this recipe.`}
        </p>
      )}

      {status.active && (
        <p className="pf-rcp-run-active">
          {`This recipe is running -- on step ${status.step}.`}
          {status.paused && <span className="pf-rcp-run-paused"> Paused.</span>}
        </p>
      )}

      {error && <p className="pf-banner pf-banner--error">{error}</p>}

      <button
        type="button"
        className="pf-modal-btn accent"
        disabled={!status.canRun || starting}
        onClick={() => setConfirming(true)}
      >
        Run this recipe
      </button>

      <ConfirmAction
        open={confirming}
        title="Start this cook?"
        message="This starts a real cook on the grill, running this recipe's program steps."
        onConfirm={start}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}
