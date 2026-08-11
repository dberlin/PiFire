import { useCallback, useEffect, useState } from "react";
import { adminErrorText, systemAction } from "../../helpers/admin/adminApi";
import type {
  SystemAction,
  UpdateCheck,
  UpdateStarted,
  UpdateState,
  UpdateStatus,
} from "../../helpers/contracts/operations.gen";
import { behindText } from "../../helpers/update/behindText";
import {
  buildLogDownloadUrl,
  changeBranch,
  fetchBuildLog,
  fetchUpdateCheck,
  fetchUpdateLog,
  fetchUpdateState,
  fetchUpdateStatus,
  pullUpdate,
  rebuildAcados,
  rebuildWebUi,
  refreshBranches,
  upgradeDeps,
} from "../../helpers/update/updateApi";
import type { UpdateResult } from "../../helpers/update/updateTypes";
import { StreamingLogPanel } from "../logs/StreamingLogPanel";
import "./update.css";

const refusalText = (r: UpdateResult<unknown>): string => {
  if (r.status === 409) return "Stop the grill before updating.";
  if (r.status === 400) return "That branch is no longer available — refresh the branch list.";
  return r.message;
};

// Matches wizard/InstallProgress.tsx and updater.py:548 -- the backend pins
// percent at 142 to mean "finished, but a reboot is required" rather than a
// plain > 100 "finished, service restart is enough".
const REBOOT_REQUIRED_PERCENT = 142;

// updater.py publishes a NEGATIVE percent when a run it owns end-to-end fails
// (common/install_log.py's INSTALL_FAILED_PERCENT). Every real percent is
// positive, the finished sentinels included, so without this a failed rebuild
// left the poll below running forever against a process that had already
// stopped writing.
const isFailure = (percent: number) => percent < 0;

export function UpdatePage() {
  const [state, setState] = useState<UpdateState | null>(null);
  const [behind, setBehind] = useState<number | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [log, setLog] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<UpdateStatus | null>(null);
  const [done, setDone] = useState<null | "ok" | "reboot" | "failed">(null);
  const [showBuildLog, setShowBuildLog] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const apply = useCallback((s: UpdateResult<UpdateState>, c: UpdateResult<UpdateCheck>) => {
    if (s.ok && s.data) {
      setState(s.data);
      // Falls back to the first branch offered: a detached checkout has no
      // current branch, and leaving the picker on "" would post an empty
      // target the moment someone reached for the control that fixes it.
      setSelected(s.data.branch || s.data.branches[0] || "");
    }
    setBehind(c.ok && c.data ? c.data.behind : null);
  }, []);

  const load = useCallback(() => {
    return Promise.all([fetchUpdateState(), fetchUpdateCheck()]).then(([s, c]) => apply(s, c));
  }, [apply]);

  useEffect(() => {
    // Cancellation-safe: a user who navigates away before both requests
    // land must not set state on an unmounted tree.
    let cancelled = false;
    Promise.all([fetchUpdateState(), fetchUpdateCheck()]).then(([s, c]) => {
      if (!cancelled) apply(s, c);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  // Polls GET /api/update/status once a mutation has started a run. Gated on
  // a stable boolean rather than the `progress` object itself, so the
  // interval is created ONCE per run and not torn down/recreated on every
  // tick's setProgress; it clears itself from inside the callback when the
  // run finishes, and on unmount via the cleanup function.
  const polling = progress !== null && done === null;
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(async () => {
      const r = await fetchUpdateStatus();
      if (!r.ok || !r.data || r.data.percent === null) return;
      setProgress(r.data);
      const failed = isFailure(r.data.percent);
      if (r.data.percent <= 100 && !failed) return;
      setDone(failed ? "failed" : r.data.percent === REBOOT_REQUIRED_PERCENT ? "reboot" : "ok");
      // Reload the state the run just changed -- which is how the build-log
      // offer below learns a rebuild failed.
      void load();
      clearInterval(id);
    }, 250); // matches wizard/InstallProgress.tsx's polling cadence
    return () => clearInterval(id);
  }, [polling, load]);

  const run = async (fn: () => Promise<UpdateResult<UpdateStarted>>) => {
    setNote(null);
    setBusy(true);
    const r = await fn();
    setBusy(false);
    if (!r.ok) {
      setNote(refusalText(r));
      return;
    }
    if (!r.data?.started) {
      setNote("Updates run on PiFire hardware.");
      return;
    }
    setProgress({ percent: 0, status: "Starting…", output: "" });
    setDone(null);
    // A new run writes a new transcript. Leaving the panel open would show the
    // previous build's failure until the first line of this one lands.
    setShowBuildLog(false);
  };

  // POSTs to the admin API rather than linking, for the reason InstallProgress
  // documents: /admin/restart and /admin/reboot were Flask page routes and went
  // with the rest of them, and the API is POST-only on purpose.
  const runSystemAction = async (action: SystemAction) => {
    setActionError(null);
    const result = await systemAction(action);
    if (!result.ok) setActionError(adminErrorText(result));
  };

  const showLog = async () => {
    const r = await fetchUpdateLog(10);
    setLog(r.ok && r.data ? r.data.output : `Log failed: ${r.message}`);
  };

  if (!state) return <div className="pf-admin">Loading updater…</div>;

  return (
    <div className="pf-admin">
      <section className="pf-admin-card pf-admin-wide">
        <h2>System Update</h2>
        <p>
          Current: <strong>{state.version}</strong>{" "}
          {state.detached ? (
            <>
              at detached commit <strong>{state.detached}</strong>
            </>
          ) : (
            <>
              on branch <strong>{state.branch}</strong>
            </>
          )}
        </p>
        <p>Remote: {state.remote_version}</p>
        {/* An update is `git merge origin/<branch>`, so a checkout that is not
            on a branch has nothing to update to. Said here rather than left for
            the button to fail on, and paired with the control that fixes it. */}
        {state.detached ? (
          <p className="pf-update-note" role="alert">
            This checkout is not on a branch, so there is nothing to update to. Pick a branch below
            and change to it first.
          </p>
        ) : (
          <p>{behindText(behind)}</p>
        )}
      </section>

      <section className="pf-admin-card">
        <h3>Branch</h3>
        <label>
          Branch
          <select value={selected} onChange={(e) => setSelected(e.target.value)}>
            {state.branches.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="pf-admin-btn"
          disabled={busy}
          onClick={() => void run(() => changeBranch(selected))}
        >
          Change Branch
        </button>
        <button
          type="button"
          className="pf-admin-btn"
          disabled={busy}
          onClick={() => void run(() => refreshBranches())}
        >
          Refresh remote branches
        </button>
      </section>

      <section className="pf-admin-card">
        <h3>Actions</h3>
        {/* Disabled while detached, alone among these: /pull is the only one
            that needs a branch. Changing branch is the way out and upgrading
            dependencies or rebuilding the bundle work wherever HEAD is. */}
        <button
          type="button"
          className="pf-admin-btn"
          disabled={busy || state.detached !== null}
          onClick={() => void run(() => pullUpdate())}
        >
          Update to latest
        </button>
        <button
          type="button"
          className="pf-admin-btn"
          disabled={busy}
          onClick={() => void run(() => upgradeDeps())}
        >
          Upgrade dependencies
        </button>
        {/* web-react/dist is a build artifact, so pulling new sources does not
            produce a new bundle on its own. An update rebuilds it, but this is
            the way back when that build did not run or failed -- otherwise the
            only route is a shell on the grill. */}
        <button
          type="button"
          className="pf-admin-btn"
          disabled={busy}
          onClick={() => void run(() => rebuildWebUi())}
        >
          Rebuild web UI
        </button>
        <button
          type="button"
          className="pf-admin-btn"
          disabled={busy}
          onClick={() => void run(() => rebuildAcados())}
        >
          Rebuild Acados
        </button>
        {state.web_ui_stale && (
          <p className="pf-update-note" role="status">
            The web interface is older than the code on disk. Rebuild it to pick up the update.
          </p>
        )}
        {/* Offered only on failure. A build that worked has nothing to say that
            the interface itself does not already show. */}
        {state.web_ui_build_failed && (
          <div className="pf-update-build-failed" role="alert">
            <p>
              The last web UI rebuild failed. The previously built interface is still being served.
            </p>
            <div className="pf-update-build-actions">
              <button
                type="button"
                className="pf-admin-btn"
                onClick={() => setShowBuildLog((open) => !open)}
              >
                {showBuildLog ? "Hide build log" : "Show build log"}
              </button>
              {/* A real link, not a fetch-and-blob: the browser streams it
                  straight to disk and the file is a plain GET anyone can paste
                  into a bug report. */}
              <a className="pf-admin-btn" href={buildLogDownloadUrl()} download>
                Download build log
              </a>
            </div>
            {/* Mounted only while open, so a closed panel starts no polling. */}
            {showBuildLog && (
              <StreamingLogPanel
                fetchDelta={fetchBuildLog}
                waitingText="No build output was recorded."
              />
            )}
          </div>
        )}
        {note && <p className="pf-update-note">{note}</p>}
      </section>

      {progress && (
        <section className="pf-admin-card pf-admin-wide" aria-label="update progress">
          <div
            className="pf-update-progress"
            // Clamped at both ends: the failure sentinel is a negative percent,
            // which as a width is a bar that renders inside out.
            style={{ width: `${Math.min(Math.max(progress.percent ?? 0, 0), 100)}%` }}
          />
          <p>{progress.status}</p>
          <pre className="pf-update-log">{progress.output}</pre>
          {/* The run says "Finished! Restarting Server..." and then nothing
              restarts anything -- the updater is a detached process that cannot
              restart the service it was launched from, and the page was only
              announcing the finish. So the page offers it, the way the wizard's
              InstallProgress does at the end of an install. Until the service
              restarts, what is running is still the pre-update code. */}
          {done === "ok" && (
            <div className="pf-update-done">
              <p>Update complete. Restart the service to run the new code.</p>
              <button
                type="button"
                className="pf-admin-btn"
                onClick={() => void runSystemAction("restart")}
              >
                Restart Now
              </button>
            </div>
          )}
          {done === "reboot" && (
            <div className="pf-update-done">
              <p>Update complete — a reboot is required to load the new configuration.</p>
              <div className="pf-update-done-actions">
                <button
                  type="button"
                  className="pf-admin-btn"
                  onClick={() => void runSystemAction("reboot")}
                >
                  Reboot Now
                </button>
                <button
                  type="button"
                  className="pf-admin-btn"
                  onClick={() => void runSystemAction("restart")}
                >
                  Restart Service Only
                </button>
              </div>
            </div>
          )}
          {done === "failed" && <p role="alert">{progress.status}</p>}
          {actionError && (
            <p className="pf-update-note" role="alert">
              {actionError}
            </p>
          )}
        </section>
      )}

      <section className="pf-admin-card pf-admin-wide">
        <h3>Update log</h3>
        <button type="button" className="pf-admin-btn" onClick={() => void showLog()}>
          Show log
        </button>
        {log !== null && <pre className="pf-update-log">{log}</pre>}
      </section>
    </div>
  );
}
