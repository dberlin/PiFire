import type { CookFileAsset, CookFileComment } from "@pifire/core/contracts/content";
import { useCallback, useState } from "react";

import {
  addCookFileComment,
  assetThumbUrl,
  assetUrl,
  deleteCookFileComment,
  setCommentAssets,
  updateCookFileComment,
} from "../../helpers/files/cookfileApi";
import { useDismissOnEscape } from "../../helpers/useDismissOnEscape";
import { ConfirmAction } from "../dashboard/ConfirmAction";

// Comments, their attached photos, and a lightbox.
//
// Comment text is rendered as a TEXT NODE with white-space: pre-wrap. Flask
// stores plain text and substitutes \n -> <br> at render time because Jinja
// emits it as HTML; the API deliberately does not, so there is nothing here to
// dangerouslySetInnerHTML and no XSS surface.
//
// The attach picker posts the WHOLE resulting asset list. Flask toggles one
// asset per click and infers add-versus-remove from a `state` string the client
// sends, so a stale client view performs the opposite of what the user clicked.
//
// Prev/next in the lightbox wraps within THIS comment's assets, matching the
// legacy navimage action -- but as client-side arithmetic, so the new API has
// no navimage equivalent to secure.

interface Props {
  filename: string;
  /** metadata.id -- the /static/img/tmp/{id} folder assets are served from. */
  parentId: string;
  comments: CookFileComment[];
  assets: CookFileAsset[];
  onChanged: () => void;
}

interface Lightbox {
  commentId: string;
  index: number;
}

export function CommentList({ filename, parentId, comments, assets, onChanged }: Props) {
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [picking, setPicking] = useState<{ id: string; selected: string[] } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<Lightbox | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // useCallback keeps the dismiss handler stable so the key listener is not
  // torn down and re-registered on every render.
  const closeLightbox = useCallback(() => setLightbox(null), []);
  useDismissOnEscape(lightbox !== null, closeLightbox);

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

  const lightboxComment = lightbox
    ? (comments.find((c) => c.id === lightbox.commentId) ?? null)
    : null;
  const lightboxAssets = lightboxComment?.assets ?? [];
  const step = (delta: number) =>
    setLightbox((current) =>
      current === null || lightboxAssets.length === 0
        ? current
        : {
            ...current,
            //  Wraps within THIS comment's assets, as navimage does.
            index: (current.index + delta + lightboxAssets.length) % lightboxAssets.length,
          },
    );

  return (
    <>
      {error && <div className="pf-banner pf-banner--error">{error}</div>}

      <label htmlFor="cf-new-comment">Add a comment</label>
      <textarea
        id="cf-new-comment"
        className="pf-input"
        rows={3}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <button
        type="button"
        className="pf-modal-btn accent"
        disabled={busy || draft.trim() === ""}
        onClick={() => run(addCookFileComment(filename, draft), () => setDraft(""))}
      >
        Add comment
      </button>

      {comments.length === 0 && <p className="pf-settings-hint">No comments on this cook yet.</p>}

      {comments.map((comment) => (
        <div className="pf-cf-comment" key={comment.id}>
          <div className="pf-cf-comment-meta">
            {`${comment.date} ${comment.time}`}
            {comment.edited && <span>{` — edited ${comment.edited}`}</span>}
          </div>

          {editing?.id === comment.id ? (
            <>
              <textarea
                className="pf-input"
                aria-label={`Edit comment from ${comment.date} ${comment.time}`}
                rows={3}
                value={editing.text}
                onChange={(e) => setEditing({ id: comment.id, text: e.target.value })}
              />
              <button
                type="button"
                className="pf-modal-btn accent"
                disabled={busy}
                onClick={() =>
                  run(updateCookFileComment(filename, comment.id, editing.text), () =>
                    setEditing(null),
                  )
                }
              >
                Save comment
              </button>
              <button type="button" className="pf-modal-btn" onClick={() => setEditing(null)}>
                Cancel edit
              </button>
            </>
          ) : (
            <p className="pf-cf-comment-text">{comment.text}</p>
          )}

          {comment.assets.length > 0 && (
            <div className="pf-cf-comment-assets">
              {comment.assets.map((name, index) => (
                <button
                  type="button"
                  key={name}
                  onClick={() => setLightbox({ commentId: comment.id, index })}
                >
                  <img
                    className="pf-cf-comment-thumb"
                    src={assetThumbUrl(parentId, name)}
                    //  Not "Photo ...": screen readers already announce an
                    //  <img> as an image, so naming the type here repeats it.
                    alt={`Attachment ${name}`}
                  />
                </button>
              ))}
            </div>
          )}

          <div className="pf-cf-row-actions">
            <button
              type="button"
              className="pf-modal-btn"
              onClick={() => setEditing({ id: comment.id, text: comment.text })}
            >
              {`Edit comment from ${comment.date} ${comment.time}`}
            </button>
            <button
              type="button"
              className="pf-modal-btn"
              disabled={assets.length === 0}
              onClick={() => setPicking({ id: comment.id, selected: [...comment.assets] })}
            >
              {`Attach media to comment from ${comment.date} ${comment.time}`}
            </button>
            <button
              type="button"
              className="pf-modal-btn danger"
              onClick={() => setPendingDelete(comment.id)}
            >
              {`Delete comment from ${comment.date} ${comment.time}`}
            </button>
          </div>
        </div>
      ))}

      {picking && (
        <div className="pf-modal-scrim">
          <div className="pf-modal">
            <div className="pf-modal-title">Attach media</div>
            <div className="pf-cf-media-grid">
              {assets.map((asset) => {
                const selected = picking.selected.includes(asset.filename);
                return (
                  <label className="pf-cf-media-item" key={asset.id}>
                    <img
                      className={`pf-cf-media-img${selected ? " pf-cf-media-img--selected" : ""}`}
                      src={assetThumbUrl(parentId, asset.filename)}
                      alt=""
                    />
                    <input
                      type="checkbox"
                      aria-label={`Attach ${asset.filename}`}
                      checked={selected}
                      onChange={(e) =>
                        setPicking({
                          id: picking.id,
                          selected: e.target.checked
                            ? [...picking.selected, asset.filename]
                            : picking.selected.filter((name) => name !== asset.filename),
                        })
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
                onClick={() =>
                  //  The WHOLE resulting list, not a toggle: a per-asset toggle
                  //  inverts whenever the client's view of "selected" is stale.
                  run(setCommentAssets(filename, picking.id, picking.selected), () =>
                    setPicking(null),
                  )
                }
              >
                Save attachments
              </button>
            </div>
          </div>
        </div>
      )}

      {lightbox && lightboxAssets.length > 0 && (
        <div className="pf-modal-scrim" onClick={() => setLightbox(null)}>
          <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
            <img
              src={assetUrl(parentId, lightboxAssets[lightbox.index])}
              alt={lightboxAssets[lightbox.index]}
            />
            <div className="pf-modal-actions">
              <button type="button" className="pf-modal-btn" onClick={() => step(-1)}>
                Previous photo
              </button>
              <button type="button" className="pf-modal-btn" onClick={() => step(1)}>
                Next photo
              </button>
              <button type="button" className="pf-modal-btn" onClick={() => setLightbox(null)}>
                Close photo
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete this comment?"
        message="The comment text is removed. The photos attached to it stay in the cook file."
        onConfirm={() => {
          const id = pendingDelete;
          setPendingDelete(null);
          if (id) run(deleteCookFileComment(filename, id));
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
