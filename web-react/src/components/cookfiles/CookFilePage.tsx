import type { FileErrorDetail } from "@pifire/core/contracts/content";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link, useParams } from "react-router";
import { FileRequestError } from "../../helpers/files/apiEnvelope";
import { fetchCookFileDetail, recoverCookFile } from "../../helpers/files/cookfileApi";
import { queryKeys } from "../../helpers/query/keys";
import { CommentList } from "./CommentList";
import { CookFileChart } from "./CookFileChart";
import { CookFileMeta } from "./CookFileMeta";
import { EventsTable } from "./EventsTable";
import { MediaPanel } from "./MediaPanel";

// The cook-file detail route. One fetch on mount (and on each reload), no
// polling: a saved cook is a finished dataset except for the edits this page
// itself makes. That is the whole difference from HistoryPage, which is why
// none of its window/refresh machinery is reused here.
//
// Flask reaches this view through a form POST that re-renders the entire page
// server-side after every mutation (render_cookfile_page). Here each panel
// calls its endpoint and then asks the page to refetch, so a title edit does
// not blow away an open comment editor.

function toDetail(err: unknown): FileErrorDetail {
  if (err instanceof FileRequestError) return err.detail;
  return {
    status: 0,
    message: err instanceof Error ? err.message : "Request failed",
    errortype: null,
  };
}

export function CookFilePage() {
  const { filename = "" } = useParams<{ filename: string }>();
  const [recovering, setRecovering] = useState(false);
  // A failed Attempt Repair/Conversion reports its OWN error (e.g. "Repair
  // failed."), not the stale one that sent the user to the button in the
  // first place. Cleared at the start of every attempt.
  const [recoverError, setRecoverError] = useState<FileErrorDetail | null>(null);

  const { data, isPending, error } = useQuery({
    queryKey: queryKeys.cookfileDetail(filename),
    //  helpers/files/apiEnvelope.ts throws FileRequestError already, so this
    //  needs no unwrap(): `error` below IS the FileRequestError, and its
    //  `.detail.errortype` is what the repair prompt branches on.
    queryFn: () => fetchCookFileDetail(filename),
  });

  const queryClient = useQueryClient();
  const reload = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.cookfileRoot(filename) }),
    [queryClient, filename],
  );

  const loading = isPending;
  //  A failed reload must not keep showing a stale cook: the archive may have
  //  just been repaired into a different shape, or deleted. react-query keeps
  //  the last successful `data` around through a background error, so it is
  //  dropped here rather than trusted once `error` is set.
  const detail = error ? null : (data ?? null);
  const problem = recoverError ?? (error ? toDetail(error) : null);

  const recover = (action: "upgrade" | "repair") => {
    setRecovering(true);
    setRecoverError(null);
    recoverCookFile(filename, action)
      .then(reload)
      .catch((err: unknown) => setRecoverError(toDetail(err)))
      .finally(() => setRecovering(false));
  };

  return (
    <div className="pf-settings">
      <div className="pf-settings-content pf-settings-content--wide">
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

            <div className="pf-section">
              <h2 className="pf-section-title">Chart</h2>
              <div className="pf-section-body">
                <CookFileChart filename={detail.filename} />
              </div>
            </div>

            <div className="pf-section">
              <h2 className="pf-section-title">Events</h2>
              <div className="pf-section-body">
                <EventsTable
                  filename={detail.filename}
                  events={detail.events}
                  totals={detail.event_totals}
                  units={detail.metadata.units}
                />
              </div>
            </div>

            <div className="pf-section">
              <h2 className="pf-section-title">Comments</h2>
              <div className="pf-section-body">
                <CommentList
                  filename={detail.filename}
                  parentId={detail.metadata.id}
                  comments={detail.comments}
                  assets={detail.assets}
                  onChanged={reload}
                />
              </div>
            </div>

            <div className="pf-section">
              <h2 className="pf-section-title">Media</h2>
              <div className="pf-section-body">
                <MediaPanel
                  filename={detail.filename}
                  parentId={detail.metadata.id}
                  assets={detail.assets}
                  thumbnail={detail.metadata.thumbnail}
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
