import { useState } from "react";

import {
  adminErrorText,
  deleteLogs,
  diagnosticsDownloadUrl,
  logsDownloadUrl,
} from "../../helpers/admin/adminApi";
import { ConfirmAction } from "../dashboard/ConfirmAction";

/**
 * The log files: what is there, a zip of all of them, and a confirmed delete.
 *
 * The delete reports the names that ACTUALLY went rather than assuming.
 * Flask's equivalent ran `os.system("rm logs/*.log")` inside a bare `except:`,
 * where a failure was indistinguishable from success; the endpoint behind this
 * globs server-side and answers with the list, so the card shows it.
 *
 * There is no per-file download, matching the server: /logs/download builds one
 * archive of everything and takes no filename, which is the same path rule the
 * rest of this page follows.
 */
export function LogsCard({
  logs,
  onChanged,
}: {
  logs: string[];
  onChanged: () => void | Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remove = async () => {
    setConfirming(false);
    const result = await deleteLogs();
    if (result.ok) {
      const removed = result.data?.removed ?? [];
      setNotice(
        removed.length === 0 ? "There was nothing to clear." : `Cleared ${removed.join(", ")}.`,
      );
      setError(null);
      await onChanged();
    } else {
      setError(adminErrorText(result));
      setNotice(null);
    }
  };

  return (
    <section className="pf-admin-card" aria-labelledby="admin-logs">
      <h2 className="pf-admin-card-title" id="admin-logs">
        Logs
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
        {logs.length === 0 ? (
          <p className="pf-admin-note">No log files.</p>
        ) : (
          <ul className="pf-admin-backup-list">
            {logs.map((name) => (
              <li className="pf-admin-backup-row" key={name}>
                <span className="pf-admin-backup-name">{name}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="pf-admin-actions">
        <a className="pf-admin-btn" href={logsDownloadUrl()} download>
          Download All
        </a>
        {/* Here rather than in BackupsCard: the bundle is these logs plus the
            database, and .pf-admin-actions WRAPS rather than scrolling, so the
            button cannot be pushed out of reach. The backups card is height
            constrained by its grid row -- parked beside its .pf-admin-scroll
            this squeezed the backup lists to a sliver, and inside it the button
            fell below the fold. */}
        <a className="pf-admin-btn" href={diagnosticsDownloadUrl()} download>
          Download Diagnostics
        </a>
        <button
          type="button"
          className="pf-admin-btn danger"
          disabled={logs.length === 0}
          onClick={() => setConfirming(true)}
        >
          Delete All Logs
        </button>
      </div>

      <ConfirmAction
        open={confirming}
        title="Delete every log file?"
        message="Every .log file is emptied, and rotated backups are removed. The live files stay in place so the running processes keep writing to them. Nothing else in the logs folder is touched, and the logs start again from empty."
        onConfirm={() => void remove()}
        onCancel={() => setConfirming(false)}
      />
    </section>
  );
}
