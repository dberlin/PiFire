import type { CookFileAsset } from "@pifire/core/contracts/content";
import { useId, useState } from "react";
import {
  assetThumbUrl,
  assetUrl,
  deleteCookFileAssets,
  setCookFileThumbnail,
  uploadCookFileAssets,
} from "../../helpers/files/cookfileApi";
import { ConfirmAction } from "../dashboard/ConfirmAction";

// The asset grid: upload, delete-selected, and pick the cook's thumbnail.
//
// Every flow ends with onChanged() so the page refetches and every panel sees
// the new list. That is the whole synchronisation story: remove_assets already
// scrubs deleted names out of comments[].assets and metadata.thumbnail
// server-side, so a comment's photo disappearing is a consequence of the
// refetch, not of bookkeeping here.

interface Props {
  filename: string;
  /** metadata.id -- the /static/img/tmp/{id} folder assets are served from. */
  parentId: string;
  assets: CookFileAsset[];
  /** metadata.thumbnail -- an asset filename, or "". */
  thumbnail: string;
  onChanged: () => void;
}

export function MediaPanel({ filename, parentId, assets, thumbnail, onChanged }: Props) {
  const [selected, setSelected] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
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
    //  One request for the whole selection: the endpoint reads
    //  request.files.getlist("assets").
    run(uploadCookFileAssets(filename, images));
  };

  return (
    <>
      {error && <div className="pf-banner pf-banner--error">{error}</div>}

      <div className="pf-cf-toolbar">
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
          className="pf-cf-file-input"
        />
        <button
          type="button"
          className="pf-modal-btn danger"
          disabled={busy || selected.length === 0}
          onClick={() => setConfirming(true)}
        >
          {`Remove selected photos (${selected.length})`}
        </button>
      </div>

      {assets.length === 0 ? (
        <p className="pf-settings-hint">No photos yet — upload one to illustrate this cook.</p>
      ) : (
        <div className="pf-cf-media-grid">
          {assets.map((asset) => {
            const isCurrent = asset.filename === thumbnail;
            const isSelected = selected.includes(asset.filename);
            return (
              <div className="pf-cf-media-item" key={asset.id}>
                <a href={assetUrl(parentId, asset.filename)} target="_blank" rel="noreferrer">
                  <img
                    className={`pf-cf-media-img${isCurrent ? " pf-cf-media-img--selected" : ""}`}
                    src={assetThumbUrl(parentId, asset.filename)}
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
                  disabled={busy || isCurrent}
                  onClick={() => run(setCookFileThumbnail(filename, asset.filename))}
                >
                  {isCurrent ? "Current thumbnail" : `Use ${asset.filename} as thumbnail`}
                </button>
              </div>
            );
          })}
        </div>
      )}

      <ConfirmAction
        open={confirming}
        title={`Remove ${selected.length} ${selected.length === 1 ? "photo" : "photos"}?`}
        message="The images are deleted from the cook file and from any comment that used them."
        onConfirm={() => {
          setConfirming(false);
          run(deleteCookFileAssets(filename, selected), () => setSelected([]));
        }}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
