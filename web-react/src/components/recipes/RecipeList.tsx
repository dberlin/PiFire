import { useEffect, useId, useState } from "react";
import { Link, useNavigate } from "react-router";
import type { FileListing } from "../../helpers/contracts/content.gen";
import { fetchFileListing, thumbnailUrl } from "../../helpers/files/filesApi";
import { PER_PAGE_CHOICES } from "../../helpers/files/fileTypes";
import {
  createRecipe,
  deleteRecipe,
  recipeDownloadUrl,
  uploadRecipe,
} from "../../helpers/files/recipeApi";
import { ConfirmAction } from "../dashboard/ConfirmAction";

// The recipe list, rendered at /recipes exactly as Flask does it
// (blueprints/recipes/templates/recipes/index.html). React users previously
// could not see, create, open, upload or delete a recipe at all.
//
// The window the list opens on is the window the Flask page opens on --
// gotoRFPage(1, false, 10) in recipes/js/recipes.js -- reverse=false, the
// OPPOSITE of the cook-file list, so fetchFileListing's own reverse=true
// default is overridden explicitly below.
//
// "New Recipe" has no cook-file equivalent: a cook file is produced by
// cooking, a recipe is authored, so only this list gets a create button.

// How the most recent listing request finished, tagged with the request id it
// belongs to. Same idiom as CookFileList's Outcome: it makes "loading" a value
// derived in render rather than a second state written from inside an effect.
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

export function RecipeList() {
  const navigate = useNavigate();
  const [request, setRequest] = useState<Request>({
    id: 0,
    page: 1,
    perPage: 10,
    reverse: false,
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

  useEffect(() => {
    let cancelled = false;
    const { id, page, perPage, reverse } = request;
    fetchFileListing("recipes", { page, perPage, reverse })
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
    deleteRecipe(file)
      .then(reload)
      .catch((err: Error) => setActionError(err.message));
  };

  const onUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const archive = event.target.files?.[0];
    // Clear the input so choosing the same file twice fires change again.
    event.target.value = "";
    if (!archive) return;
    setActionError(null);
    uploadRecipe(archive)
      .then(reload)
      .catch((err: Error) => setActionError(err.message));
  };

  const onCreate = () => {
    setActionError(null);
    createRecipe()
      .then(({ filename }) => navigate(`/recipes/${encodeURIComponent(filename)}`))
      .catch((err: Error) => setActionError(err.message));
  };

  return (
    <>
      <div className="pf-rcp-toolbar">
        <button type="button" className="pf-modal-btn accent" onClick={onCreate}>
          New recipe
        </button>

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

        <span className="pf-rcp-toolbar-spacer" />

        <label htmlFor={uploadId} className="pf-modal-btn">
          Upload recipe
        </label>
        <input
          id={uploadId}
          type="file"
          accept=".pifire"
          aria-label="Upload a recipe"
          onChange={onUpload}
          className="pf-rcp-file-input"
        />
      </div>

      {failed && (
        <div className="pf-banner pf-banner--error">
          Couldn't load recipes. Check the PiFire connection and try again.
        </div>
      )}
      {actionError && <div className="pf-banner pf-banner--error">{actionError}</div>}
      {loading && listing === null && <p className="pf-settings-hint">Loading recipes…</p>}

      {items.length > 0 ? (
        <table className="pf-rcp-table">
          <thead>
            <tr>
              <th className="pf-rcp-thumb-col"> </th>
              <th>Recipe</th>
              <th className="pf-rcp-actions-col"> </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.filename}>
                <td>
                  <img className="pf-rcp-thumb" src={thumbnailUrl(item.thumbnail)} alt="" />
                </td>
                <td>
                  {/* An ERROR-titled row is exactly the one a user needs to open
                      to reach the repair prompt, so it is never disabled. */}
                  <Link
                    className="pf-rcp-link"
                    to={`/recipes/${encodeURIComponent(item.filename)}`}
                  >
                    {item.title || item.filename}
                  </Link>
                  {item.title && <span className="pf-rcp-name">{item.filename}</span>}
                </td>
                <td className="pf-rcp-actions-col">
                  <div className="pf-rcp-row-actions">
                    {/* A plain href: the browser has to own the save dialog. */}
                    <a className="pf-modal-btn" href={recipeDownloadUrl(item.filename)} download>
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
        !loading && <p className="pf-settings-hint">No recipes yet.</p>
      )}

      {lastPage > 1 && (
        <div className="pf-rcp-pager">
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
          <span className="pf-rcp-pager-current">{`Page ${request.page} of ${lastPage}`}</span>
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
        title={pendingDelete ? `Delete ${pendingDelete}?` : "Delete recipe?"}
        message="The recipe and everything in it -- steps, ingredients and photos -- are removed for good."
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
