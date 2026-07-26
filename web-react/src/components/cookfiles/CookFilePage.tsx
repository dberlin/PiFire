import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import {
  type CookFileDetail,
  type CookFileError,
  CookFileRequestError,
  fetchCookFileDetail,
  recoverCookFile,
} from "../../helpers/files/cookfileApi";
import { CookFileMeta } from "./CookFileMeta";

// The cook-file detail route. One fetch on mount (and on each reload), no
// polling: a saved cook is a finished dataset except for the edits this page
// itself makes. That is the whole difference from HistoryPage, which is why
// none of its window/refresh machinery is reused here.
//
// Flask reaches this view through a form POST that re-renders the entire page
// server-side after every mutation (render_cookfile_page). Here each panel
// calls its endpoint and then asks the page to refetch, so a title edit does
// not blow away an open comment editor.

interface Outcome {
  id: number;
  problem: CookFileError | null;
}

function toDetail(err: unknown): CookFileError {
  if (err instanceof CookFileRequestError) return err.detail;
  return {
    status: 0,
    message: err instanceof Error ? err.message : "Request failed",
    errortype: null,
  };
}

export function CookFilePage() {
  const { filename = "" } = useParams<{ filename: string }>();
  const [requestId, setRequestId] = useState(0);
  const [detail, setDetail] = useState<CookFileDetail | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [recovering, setRecovering] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const id = requestId;
    fetchCookFileDetail(filename)
      .then((fresh) => {
        if (cancelled) return;
        setDetail(fresh);
        setOutcome({ id, problem: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        //  A failed reload must not keep showing a stale cook: the archive may
        //  have just been repaired into a different shape, or deleted.
        setDetail(null);
        setOutcome({ id, problem: toDetail(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [filename, requestId]);

  // Plain render-time computation from state -- no effect, no mirrored state.
  const loading = outcome === null || outcome.id !== requestId;
  const problem = loading ? null : outcome.problem;

  const reload = () => setRequestId((n) => n + 1);

  const recover = (action: "upgrade" | "repair") => {
    setRecovering(true);
    recoverCookFile(filename, action)
      .then(reload)
      .catch((err: unknown) => setOutcome({ id: requestId, problem: toDetail(err) }))
      .finally(() => setRecovering(false));
  };

  return (
    <div className="pf-settings">
      <div className="pf-settings-content" style={{ maxWidth: 1100 }}>
        <Link className="pf-settings-back" to="/history">
          Back to history
        </Link>

        {loading && <p className="pf-settings-hint">Loading cook file…</p>}

        {problem?.status === 422 && (
          <div className="pf-banner pf-banner--error">
            <p>This cook file could not be loaded.</p>
            <p className="pf-settings-hint">{problem.message}</p>
            {/* cferror.html branches on exactly this: a version error offers
                conversion, anything else offers repair. Both warn about data
                loss, because upgrade_cookfile rewrites every member in place. */}
            <button
              type="button"
              className="pf-modal-btn"
              disabled={recovering}
              onClick={() => recover(problem.errortype === "version" ? "upgrade" : "repair")}
            >
              {problem.errortype === "version" ? "Attempt Conversion" : "Attempt Repair"}
            </button>
            <p className="pf-settings-hint">Warning: some file elements may be lost.</p>
          </div>
        )}

        {problem && problem.status !== 422 && (
          <div className="pf-banner pf-banner--error">
            {problem.status === 404
              ? "That cook file is not in the history folder."
              : `Couldn't load this cook file: ${problem.message}`}
          </div>
        )}

        {detail && (
          <>
            <div className="pf-section">
              <h2 className="pf-section-title">{detail.metadata.title || detail.filename}</h2>
              <div className="pf-section-body">
                <CookFileMeta
                  filename={detail.filename}
                  metadata={detail.metadata}
                  labels={detail.graph_labels}
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
