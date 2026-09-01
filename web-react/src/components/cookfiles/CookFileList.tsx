import type { FileListing } from "@pifire/core/contracts/content";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router";

import {
  cookFileDownloadUrl,
  deleteCookFile,
  uploadCookFile,
} from "../../helpers/files/cookfileApi";
import { fetchFileListing, thumbnailUrl } from "../../helpers/files/filesApi";
import { PER_PAGE_CHOICES } from "../../helpers/files/fileTypes";
import { queryKeys } from "../../helpers/query/keys";
import { ConfirmAction } from "../dashboard/ConfirmAction";

// The saved-cook list, rendered as a second section of /history exactly as
// Flask does it (blueprints/history/templates/history/index.html). React users
// previously could not see, open, upload or delete a saved cook at all.
//
// The window the list opens on is the window the Flask page opens on --
// gotoCFPage(1, true, 10) in history/js/history.js -- so switching UIs does not
// silently reorder the rows.
//
// "Send to Cloud" is NOT ported: it is `disabled` in the Flask template and has
// no handler behind it, so it is decoration, not a capability.

// How the most recent listing request finished, tagged with the request id it
// belongs to. Same idiom as HistoryPage's Outcome: it makes "loading" a value
// derived in render rather than a second state written from inside an effect,
// and an id cannot collide the way a repeated page number could.
interface Outcome {
  id: number;
  failed: boolean;
}

interface Request {
  id: number;
  page: number;
  perPage: number;
  reverse: boolean;
}

export function CookFileList() {
  const [request, setRequest] = useState<Request>({
    id: 0,
    page: 1,
    perPage: 10,
    reverse: true,
  });
  // The last listing that loaded. Kept across a refetch AND across a failure so
  // paging never flashes an empty table and a transient error does not throw
  // away what the user was looking at.
  const [listing, setListing] = useState<FileListing | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const uploadId = useId();
  const perPageId = useId();
  const queryClient = useQueryClient();

  useEffect(() => {
    let cancelled = false;
    const { id, page, perPage, reverse } = request;
    fetchFileListing("cookfiles", { page, perPage, reverse })
      .then((fresh) => {
        if (cancelled) return;
        setListing(fresh);
        setOutcome({ id, failed: false });
      })
      .catch(() => {
        if (!cancelled) setOutcome({ id, failed: true });
      });
    return () => {
      cancelled = true;
    };
  }, [request]);

  // Plain render-time computation from state -- no effect, no mirrored state.
  const loading = outcome === null || outcome.id !== request.id;
  const failed = !loading && outcome.failed;
  const items = listing?.items ?? [];
  const lastPage = listing?.last_page ?? 1;

  const reload = () => setRequest((r) => ({ ...r, id: r.id + 1 }));
  const goTo = (page: number) =>
    setRequest((r) => ({ ...r, id: r.id + 1, page: Math.min(Math.max(1, page), lastPage) }));

  const confirmDelete = () => {
    const file = pendingDelete;
    setPendingDelete(null);
    if (!file) return;
    setActionError(null);
    deleteCookFile(file)
      .then(() => {
        // This list keeps its own listing state, not react-query's -- reload()
        // alone refreshes it fine. But CookFilePage caches the SAME file under
        // queryKeys.cookfileRoot(file) with its own staleTime, and that cache
        // does not know a delete happened here. Without this, a back-navigation
        // to the deleted cook's detail page within the gc window renders data
        // for a file that no longer exists.
        void queryClient.invalidateQueries({ queryKey: queryKeys.cookfileRoot(file) });
        reload();
      })
      .catch((err: Error) => setActionError(err.message));
  };

  const onUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const archive = event.target.files?.[0];
    // Clear the input so choosing the same file twice fires change again.
    event.target.value = "";
    if (!archive) return;
    setActionError(null);
    uploadCookFile(archive)
      .then(reload)
      .catch((err: Error) => setActionError(err.message));
  };

  return (
    <>
      <div className="pf-cf-toolbar">
        <button
          type="button"
          className="pf-modal-btn"
          onClick={() => setRequest((r) => ({ ...r, id: r.id + 1, page: 1, reverse: !r.reverse }))}
        >
          {request.reverse ? "Sort: newest first" : "Sort: oldest first"}
        </button>

        <label htmlFor={perPageId}>Per page</label>
        <select
          id={perPageId}
          className="pf-input"
          value={request.perPage}
          onChange={(e) =>
            setRequest((r) => ({
              ...r,
              id: r.id + 1,
              page: 1,
              perPage: Number(e.target.value),
            }))
          }
        >
          {PER_PAGE_CHOICES.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>

        <span className="pf-cf-toolbar-spacer" />

        <label htmlFor={uploadId} className="pf-modal-btn accent">
          Upload cook file
        </label>
        <input
          id={uploadId}
          type="file"
          accept=".pifire"
          aria-label="Upload a cook file"
          onChange={onUpload}
          className="pf-cf-file-input"
        />
      </div>

      {failed && (
        <div className="pf-banner pf-banner--error">
          Couldn't load saved cooks. Check the PiFire connection and try again.
        </div>
      )}
      {actionError && <div className="pf-banner pf-banner--error">{actionError}</div>}
      {loading && listing === null && <p className="pf-settings-hint">Loading saved cooks…</p>}

      {items.length > 0 ? (
        <table className="pf-cf-table">
          <thead>
            <tr>
              <th className="pf-cf-thumb-col"> </th>
              <th>Cook</th>
              <th className="pf-cf-actions-col"> </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.filename}>
                <td>
                  <img className="pf-cf-thumb" src={thumbnailUrl(item.thumbnail)} alt="" />
                </td>
                <td>
                  {/* An ERROR-titled row is exactly the one a user needs to open
                      to reach the repair prompt, so it is never disabled. */}
                  <Link
                    className="pf-cf-link"
                    to={`/cookfiles/${encodeURIComponent(item.filename)}`}
                  >
                    {item.title || item.filename}
                  </Link>
                  {item.title && <span className="pf-cf-name">{item.filename}</span>}
                </td>
                <td className="pf-cf-actions-col">
                  <div className="pf-cf-row-actions">
                    {/* A plain href: the browser has to own the save dialog. */}
                    <a className="pf-modal-btn" href={cookFileDownloadUrl(item.filename)} download>
                      {`Download ${item.filename}`}
                    </a>
                    <button
                      type="button"
                      className="pf-modal-btn danger"
                      onClick={() => setPendingDelete(item.filename)}
                    >
                      {`Delete ${item.filename}`}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !loading && <p className="pf-settings-hint">No saved cooks yet.</p>
      )}

      {lastPage > 1 && (
        <div className="pf-cf-pager">
          <button
            type="button"
            className="pf-modal-btn"
            disabled={request.page <= 1}
            onClick={() => goTo(1)}
          >
            First page
          </button>
          <button
            type="button"
            className="pf-modal-btn"
            disabled={request.page <= 1}
            onClick={() => goTo(request.page - 1)}
          >
            Previous page
          </button>
          <span className="pf-cf-pager-current">{`Page ${request.page} of ${lastPage}`}</span>
          <button
            type="button"
            className="pf-modal-btn"
            disabled={request.page >= lastPage}
            onClick={() => goTo(request.page + 1)}
          >
            Next page
          </button>
          <button
            type="button"
            className="pf-modal-btn"
            disabled={request.page >= lastPage}
            onClick={() => goTo(lastPage)}
          >
            Last page
          </button>
        </div>
      )}

      <ConfirmAction
        open={pendingDelete !== null}
        title={pendingDelete ? `Delete ${pendingDelete}?` : "Delete cook file?"}
        message="The cook file and everything in it — chart, events, comments and photos — are removed for good."
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
