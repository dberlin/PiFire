import { useRef, useState } from "react";
import {
  adminErrorText,
  backupDownloadUrl,
  createBackup,
  restoreBackup,
  uploadBackup,
} from "../../helpers/admin/adminApi";
import type { AdminResult, BackupKind, BackupListing } from "../../helpers/admin/adminTypes";
import { ConfirmAction } from "../dashboard/ConfirmAction";

const STOPPED = "Stop";

const KINDS: { kind: BackupKind; label: string; noun: string }[] = [
  { kind: "settings", label: "Settings", noun: "settings" },
  { kind: "pelletdb", label: "Pellet Database", noun: "pellet database" },
];

/** What a restore of this kind will actually do. Settings and the pellet
 * database differ in the one way a user cares about: settings are read once at
 * boot by processes a web request cannot reach, so restoring them restarts the
 * server; the pellet database is re-read on demand, so restoring it does not. */
function restoreWarning(kind: BackupKind): string {
  return kind === "settings"
    ? "Every setting is replaced with the contents of this file, and PiFire then RESTARTS. The grill must be stopped first."
    : "Every brand, wood, profile and log entry is replaced with the contents of this file. Nothing restarts.";
}

interface Pending {
  kind: BackupKind;
  file: string;
}

/**
 * Backup list, create, download, upload and restore.
 *
 * Files are named by BARE FILENAME throughout, in both directions. The server
 * resolves them through resolve_managed_file and answers with basenames only;
 * nothing here ever builds or receives a path. Flask's equivalent concatenated
 * a client string onto the backup folder, and because a restore reads a file
 * and writes it over live settings, that was an arbitrary-file-LOAD.
 */
export function BackupsCard({
  backups,
  mode,
  onChanged,
}: {
  backups: BackupListing;
  /** Gates a settings restore, which the server refuses with 409 unless
   * stopped. A pellet-database restore is allowed in any mode. */
  mode: string;
  onChanged: () => void | Promise<void>;
}) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadKind, setUploadKind] = useState<BackupKind>("settings");
  const stopped = mode === STOPPED;

  const report = (result: AdminResult<unknown>, done: string) => {
    if (result.ok) {
      setNotice(done);
      setError(null);
    } else {
      setError(adminErrorText(result));
      setNotice(null);
    }
    return result.ok;
  };

  const create = async (kind: BackupKind) => {
    const result = await createBackup(kind);
    if (report(result, `Saved ${result.data?.filename ?? "a new backup"}.`)) await onChanged();
  };

  const restore = async ({ kind, file }: Pending) => {
    setPending(null);
    const result = await restoreBackup(kind, file);
    if (report(result, `Restored from ${file}.`)) await onChanged();
  };

  const upload = async (file: File) => {
    const result = await uploadBackup(uploadKind, file);
    //  Cleared whether or not it worked: leaving the name in the input makes a
    //  failed upload look like a queued one.
    if (fileInput.current) fileInput.current.value = "";
    if (report(result, `Uploaded ${result.data?.filename ?? file.name}.`)) await onChanged();
  };

  return (
    <section className="pf-admin-card" aria-labelledby="admin-backups">
      <h2 className="pf-admin-card-title" id="admin-backups">
        Backups
      </h2>

      {notice && (
        <p className="pf-admin-notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="pf-settings-error-text" role="alert">
          {error}
        </p>
      )}

      <div className="pf-admin-scroll">
        {KINDS.map(({ kind, label, noun }) => {
          const files = backups[kind];
          //  A settings restore is the one action here the server mode-gates.
          const restorable = kind !== "settings" || stopped;
          return (
            <div className="pf-admin-backup-group" key={kind}>
              <div className="pf-admin-backup-head">
                <h3 className="pf-admin-subtitle">{label}</h3>
                <button type="button" className="pf-admin-btn" onClick={() => void create(kind)}>
                  Back Up Now
                </button>
              </div>
              {files.length === 0 ? (
                <p className="pf-admin-note">{`No ${noun} backups yet.`}</p>
              ) : (
                <ul className="pf-admin-backup-list">
                  {files.map((file) => (
                    <li className="pf-admin-backup-row" key={file}>
                      <span className="pf-admin-backup-name">{file}</span>
                      {/* An anchor rather than a fetch: the browser's own save
                          dialog is better than anything here, and the bytes
                          never enter JS memory. */}
                      <a
                        className="pf-admin-btn"
                        href={backupDownloadUrl(kind, file)}
                        download={file}
                      >
                        Download
                      </a>
                      <button
                        type="button"
                        className="pf-admin-btn danger"
                        disabled={!restorable}
                        onClick={() => setPending({ kind, file })}
                      >
                        Restore
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      <div className="pf-admin-upload">
        <label className="pf-field">
          <span className="pf-field-label">Upload into</span>
          {/* aria-label as well as the wrapping <label>: a label that wraps a
              <select> contributes its whole text content to the accessible
              name, options included, so without this the control announces
              itself as "Upload intoSettingsPellet Database". */}
          <select
            aria-label="Upload into"
            className="pf-input"
            value={uploadKind}
            onChange={(e) => setUploadKind(e.target.value as BackupKind)}
          >
            {KINDS.map(({ kind, label }) => (
              <option key={kind} value={kind}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {/* .json only. The server refuses any other extension outright, so
            narrowing the picker keeps the rejection out of the user's way
            rather than being the check itself. */}
        <input
          ref={fileInput}
          className="pf-input"
          type="file"
          accept=".json,application/json"
          aria-label="Backup file to upload"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
          }}
        />
      </div>

      <ConfirmAction
        open={pending !== null}
        title={pending ? `Restore from ${pending.file}?` : ""}
        message={pending ? restoreWarning(pending.kind) : undefined}
        onConfirm={() => {
          if (pending) void restore(pending);
        }}
        onCancel={() => setPending(null)}
      />
    </section>
  );
}
