import type { LogFamily } from "@pifire/core/contracts/operations";
import { useState } from "react";

import { logDownloadUrl } from "../../helpers/logs/logsApi";
import { LogViewer } from "./LogViewer";

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}

/** Family picker plus the viewer.
 *
 * A family is its current file plus that file's rotated backups, and the
 * current one is being written right now -- control.log by the control loop,
 * webapp.log by gunicorn. Only the backups are history, so tailing belongs
 * here as much as it does on the Events tab. */
export function LogFilesTab({ families }: { families: LogFamily[] }) {
  const [stem, setStem] = useState(families[0]?.stem ?? "");
  const [follow, setFollow] = useState(true);
  const selected = families.find((family) => family.stem === stem) ?? families[0];

  if (families.length === 0) {
    return <p className="pf-admin-note">No log files yet.</p>;
  }

  return (
    <>
      <div className="pf-log-controls">
        <label className="pf-field">
          <span className="pf-field-label">Log file</span>
          {/* aria-label as well as the wrapping label: a label that wraps a
              select contributes its whole text content, options included, to
              the accessible name. */}
          <select
            aria-label="Log file"
            className="pf-input"
            value={selected?.stem ?? ""}
            onChange={(e) => setStem(e.target.value)}
          >
            {families.map((family) => (
              <option key={family.stem} value={family.stem}>
                {`${family.stem} (${family.members.length} file${
                  family.members.length === 1 ? "" : "s"
                }, ${formatBytes(family.bytes)})`}
              </option>
            ))}
          </select>
        </label>
        {/* The whole family, via the same endpoint the viewer reads -- so what
            downloads is what was on screen. A link to the newest member alone
            would hand over the tail of a history that mostly lives in the
            rotated backups. */}
        <label className="pf-field">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          <span className="pf-field-label">Follow new lines</span>
        </label>
        {selected && (
          <a className="pf-admin-btn" href={logDownloadUrl(selected.stem)} download>
            Download
          </a>
        )}
      </div>
      {selected && <LogViewer stem={selected.stem} follow={follow} />}
    </>
  );
}
