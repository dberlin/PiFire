import type { SystemAction } from "@pifire/core/contracts/operations";
import type { InstallStatus } from "@pifire/core/contracts/wizard";
import { useEffect, useRef, useState } from "react";

import { adminErrorText, systemAction } from "../../helpers/admin/adminApi";
import { getInstallLog, getInstallStatus } from "../../helpers/wizard/wizardApi";
import { StreamingLogPanel } from "../logs/StreamingLogPanel";

export interface InstallProgressProps {
  baseUrl: string;
  onDone: (mode: "restart") => void;
}

// The backend signals "installation finished, a reboot is required before
// the new modules can load" by pinning percent at the sentinel value 142
// (rather than a plain 100). Any other percent above 100 means "finished,
// just needs the pifire service restarted" -- no reboot required.
const REBOOT_REQUIRED_PERCENT = 142;

// The installer publishes a NEGATIVE percent when it raises
// (common/install_log.py's INSTALL_FAILED_PERCENT). Every real percent is
// positive, the finished sentinels included, so before this a failed install
// was indistinguishable from a slow one: the detached process simply stopped
// writing and the bar stayed where it had got to.
const isFailure = (percent: number) => percent < 0;

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function InstallProgress({ baseUrl, onDone }: InstallProgressProps) {
  const [status, setStatus] = useState<InstallStatus>({
    percent: 0,
    status: "Starting install…",
    output: "",
  });
  const [rebootRequired, setRebootRequired] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showOutput, setShowOutput] = useState(false);
  // Keep the latest onDone without re-running the polling effect on every
  // render caused by a caller passing a fresh callback identity.
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  });

  useEffect(() => {
    let cancelled = false;
    const id = window.setInterval(() => {
      getInstallStatus(baseUrl).then((next) => {
        if (cancelled) return;
        setStatus(next);
        const percent = next.percent ?? 0;
        if (isFailure(percent)) {
          window.clearInterval(id);
          // Opened for them rather than offered: the installer has already
          // logged what went wrong, and the transcript is where it says so.
          setShowOutput(true);
        } else if (percent > 100) {
          window.clearInterval(id);
          if (percent === REBOOT_REQUIRED_PERCENT) {
            setRebootRequired(true);
          } else {
            onDoneRef.current("restart");
          }
        }
      });
    }, 250);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [baseUrl]);

  // POSTs to the admin API rather than linking. /admin/reboot and
  // /admin/restart were Flask page routes, and they went with the rest of the
  // Flask pages -- a link to either just 404s. The API is also POST-only on
  // purpose, so a link could not have done this even if the path had survived.
  async function runSystemAction(action: SystemAction) {
    setActionError(null);
    const result = await systemAction(action);
    if (!result.ok) setActionError(adminErrorText(result));
  }

  if (rebootRequired) {
    return (
      <div
        className="pf-install-reboot-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Reboot required"
      >
        <p className="pf-install-reboot-message">
          Installation finished. A reboot is required to load the new configuration.
        </p>
        {actionError && (
          <p className="pf-settings-error-text" role="alert">
            {actionError}
          </p>
        )}
        <div className="pf-install-reboot-actions">
          <button
            type="button"
            className="pf-btn pf-btn-primary"
            onClick={() => runSystemAction("reboot")}
          >
            Reboot Now
          </button>
          <button type="button" className="pf-btn" onClick={() => runSystemAction("restart")}>
            Restart Service Only
          </button>
        </div>
      </div>
    );
  }

  const currentPercent = status.percent ?? 0;
  const failed = isFailure(currentPercent);
  const percent = Math.min(Math.max(currentPercent, 0), 100);
  const barClassName = prefersReducedMotion()
    ? "pf-install-progress-bar pf-install-progress-bar-reduced-motion"
    : "pf-install-progress-bar";

  return (
    <div className="pf-install-progress">
      <p className={failed ? "pf-install-failed-title" : "pf-install-progress-status"}>
        {status.status}
      </p>
      {failed ? (
        <div className="pf-install-failed" role="alert">
          <p className="pf-install-failed-detail">{status.output}</p>
          <p className="pf-install-failed-hint">
            The grill was not changed past this point. The output below has the details, and the
            full traceback is in logs/wizard.log.
          </p>
        </div>
      ) : (
        <div
          className="pf-install-progress-track"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className={barClassName} style={{ width: `${percent}%` }} />
        </div>
      )}
      {/* A native <details>, like MetricCard's raw-data panel: the disclosure
          state, keyboard handling and find-in-page all come from the browser,
          and the summary is a real control without being wired up as one.
          `open` is bound so a failed install can open it -- the transcript is
          where the installer said what went wrong. */}
      <details
        className="pf-install-output"
        open={showOutput}
        onToggle={(e) => setShowOutput((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary className="pf-install-output-summary">Show output</summary>
        {/* Mounted only while open, so a closed panel starts no polling and the
            viewer is never virtualizing rows nobody asked to see. */}
        {showOutput && (
          <StreamingLogPanel
            fetchDelta={(offset) => getInstallLog(baseUrl, offset)}
            waitingText="Waiting for the installer's first line…"
          />
        )}
      </details>
    </div>
  );
}
