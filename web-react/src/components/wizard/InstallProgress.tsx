import { useEffect, useRef, useState } from "react";
import { adminErrorText, systemAction } from "../../helpers/admin/adminApi";
import type { SystemAction } from "../../helpers/admin/adminTypes";
import { getInstallStatus } from "../../helpers/wizard/wizardApi";
import type { InstallStatus } from "../../helpers/wizard/wizardTypes";

export interface InstallProgressProps {
  baseUrl: string;
  onDone: (mode: "restart") => void;
}

// The backend signals "installation finished, a reboot is required before
// the new modules can load" by pinning percent at the sentinel value 142
// (rather than a plain 100). Any other percent above 100 means "finished,
// just needs the pifire service restarted" -- no reboot required.
const REBOOT_REQUIRED_PERCENT = 142;

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
        if (next.percent > 100) {
          window.clearInterval(id);
          if (next.percent === REBOOT_REQUIRED_PERCENT) {
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

  const percent = Math.min(status.percent, 100);
  const barClassName = prefersReducedMotion()
    ? "pf-install-progress-bar pf-install-progress-bar-reduced-motion"
    : "pf-install-progress-bar";

  return (
    <div className="pf-install-progress">
      <p className="pf-install-progress-status">{status.status}</p>
      <div
        className="pf-install-progress-track"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={barClassName} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
