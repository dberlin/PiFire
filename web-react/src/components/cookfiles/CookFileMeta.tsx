import { useState } from "react";
import {
  assetThumbUrl,
  type CookFileLabels,
  type CookFileMetadata,
  CookFileRequestError,
  cookFileDownloadUrl,
  cookFileExportUrl,
  renameCookFileLabel,
  setCookFileTitle,
} from "../../helpers/files/cookfileApi";
import { FALLBACK_THUMB } from "../../helpers/files/fileTypes";

// The metadata card: title (editable), filename, units, start/end, thumbnail,
// the probe-label rename table, and the three downloads.
//
// Flask saves each of these through a form POST that re-renders the whole page
// server-side. Here each save calls its endpoint and then asks the page to
// refetch, which is also what re-keys a renamed label row: the server's
// create_safe_name strips non-alphanumerics ("Main Grill" -> "MainGrill"), so
// the new key is the SERVER's answer, never the text the user typed.

interface Props {
  filename: string;
  metadata: CookFileMetadata;
  labels: CookFileLabels;
  onChanged: () => void;
}

function friendlyLabelError(err: unknown): string {
  if (err instanceof CookFileRequestError && err.detail.status === 409) {
    return "That label already exists in this cook file.";
  }
  return err instanceof Error ? err.message : "Rename failed.";
}

function LabelRow({
  filename,
  probeKey,
  current,
  onChanged,
}: {
  filename: string;
  probeKey: string;
  current: string;
  onChanged: () => void;
}) {
  // Render-phase adjustment, not useEffect + setState: the draft is reseeded
  // in the same render the prop changes, so a refetch cannot leave a stale
  // value visible for a frame. Same idiom as SetpointEntry's seedKey.
  const [draft, setDraft] = useState(current);
  const [seeded, setSeeded] = useState(current);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (current !== seeded) {
    setSeeded(current);
    setDraft(current);
    setError(null);
  }

  const save = () => {
    setBusy(true);
    setError(null);
    renameCookFileLabel(filename, probeKey, draft)
      .then(() => onChanged())
      .catch((err: unknown) => setError(friendlyLabelError(err)))
      .finally(() => setBusy(false));
  };

  return (
    <tr>
      <td>{current}</td>
      <td>
        <input
          className="pf-input"
          aria-label={`New name for ${probeKey}`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        {error && <div className="pf-settings-error-text">{error}</div>}
      </td>
      <td>
        <button
          type="button"
          className="pf-modal-btn accent"
          //  The probe KEY, not the display name: keys are the identity the
          //  server renames, and two probes can share a display name.
          aria-label={`Rename ${probeKey}`}
          disabled={busy || draft.trim() === "" || draft === current}
          onClick={save}
        >
          Rename
        </button>
      </td>
    </tr>
  );
}

export function CookFileMeta({ filename, metadata, labels, onChanged }: Props) {
  const [titleDraft, setTitleDraft] = useState(metadata.title);
  const [seededTitle, setSeededTitle] = useState(metadata.title);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [savingTitle, setSavingTitle] = useState(false);
  if (metadata.title !== seededTitle) {
    setSeededTitle(metadata.title);
    setTitleDraft(metadata.title);
    setTitleError(null);
  }

  const saveTitle = () => {
    setSavingTitle(true);
    setTitleError(null);
    setCookFileTitle(filename, titleDraft)
      .then(() => onChanged())
      .catch((err: unknown) => {
        //  Restore what is actually stored. Leaving the failed draft in the box
        //  tells the user their edit landed when it did not.
        setTitleDraft(metadata.title);
        setTitleError(err instanceof Error ? err.message : "Could not save the title.");
      })
      .finally(() => setSavingTitle(false));
  };

  const probes = Object.entries(labels.probes);

  return (
    <div className="pf-cf-meta">
      {/* metadata.thumbnail is an ASSET filename; its thumb lives under
          /{parentId}/thumbs/. Empty means the archive has none. */}
      <img
        className="pf-cf-meta-thumb"
        src={metadata.thumbnail ? assetThumbUrl(metadata.id, metadata.thumbnail) : FALLBACK_THUMB}
        alt=""
      />

      <div className="pf-cf-meta-fields">
        <label htmlFor="cf-title">Title</label>
        <input
          id="cf-title"
          className="pf-input"
          value={titleDraft}
          onChange={(e) => setTitleDraft(e.target.value)}
        />
        <button
          type="button"
          className="pf-modal-btn accent"
          disabled={savingTitle || titleDraft === metadata.title}
          onClick={saveTitle}
        >
          Save title
        </button>
        {titleError && <div className="pf-settings-error-text">{titleError}</div>}

        <dl className="pf-cf-dl">
          <dt>File</dt>
          <dd>{filename}</dd>
          <dt>Units</dt>
          <dd>{metadata.units}</dd>
          <dt>Start</dt>
          <dd>{metadata.starttime}</dd>
          <dt>End</dt>
          <dd>{metadata.endtime}</dd>
        </dl>

        <div className="pf-settings-actions">
          {/* Plain hrefs: the browser owns the save dialog, and these live
              under /api/files, which the dev server proxies. */}
          <a
            className="pf-modal-btn"
            href={cookFileDownloadUrl(filename)}
            download
            style={{ textAlign: "center", textDecoration: "none" }}
          >
            Download cook file
          </a>
          <a
            className="pf-modal-btn"
            href={cookFileExportUrl(filename, "data")}
            download
            style={{ textAlign: "center", textDecoration: "none" }}
          >
            Download raw CSV
          </a>
          <a
            className="pf-modal-btn"
            href={cookFileExportUrl(filename, "events")}
            download
            style={{ textAlign: "center", textDecoration: "none" }}
          >
            Download events CSV
          </a>
        </div>

        {probes.length > 0 && (
          <table className="pf-cf-table">
            <caption>Probe labels</caption>
            <thead>
              <tr>
                <th>Current name</th>
                <th>New name</th>
                <th> </th>
              </tr>
            </thead>
            <tbody>
              {probes.map(([probeKey, name]) => (
                <LabelRow
                  key={probeKey}
                  filename={filename}
                  probeKey={probeKey}
                  current={name}
                  onChanged={onChanged}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
