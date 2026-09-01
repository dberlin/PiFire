import type {
  SystemAction,
  UpdateCheck,
  UpdateStarted,
  UpdateState,
  UpdateStatus,
} from "@pifire/core/contracts/operations";
import { useCallback, useEffect, useState } from "react";

import { adminErrorText, systemAction } from "../../helpers/admin/adminApi";
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
import { ConfirmAction } from "../dashboard/ConfirmAction";
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
  // Dismissal of the restart prompt, and ONLY of the prompt: "Restart Later"
  // leaves the server's restart_pending flag standing, so the ask comes back
  // on the next visit and keeps coming back until something actually restarts.
  const [restartDeferred, setRestartDeferred] = useState(false);

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
    // Cancellation-safe: a user who navigates away before the requests
    // land must not set state on an unmounted tree.
    let cancelled = false;
    Promise.all([fetchUpdateState(), fetchUpdateCheck(), fetchUpdateStatus()]).then(
      ([s, c, status]) => {
        if (cancelled) return;
        apply(s, c);
        // Reattach to a run that is STILL GOING. Progress used to live only in
        // this component's memory, set by the click that started the run, so a
        // reload -- or a phone picked up in place of the laptop -- during a
        // multi-minute update left `progress` null, which switched the poll
        // below off, which meant the finished state never arrived and the
        // restart it asks for was never offered. The update completed and
        // nothing ever said so.
        //
        // Terminal percents are deliberately NOT restored: the store keeps the
        // last run's status forever, so honouring 101 here would show "Update
        // complete" on every visit from now on. What genuinely outlives a run
        // is the restart_pending flag, which is server state with an owner that
        // clears it. Percent 0 is excluded for the same reason -- the route
        // writes it before launching the updater, and a launch that failed
        // leaves it lying there.
        const percent = status.ok && status.data ? status.data.percent : null;
        if (percent !== null && percent > 0 && percent <= 100) setProgress(status.data);
      },
    );
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
      clearInterval(id);
      // Reload BEFORE latching `done`, not after. This is how the build-log
      // offer below learns a rebuild failed -- and now also how the finished
      // panel learns whether the updater restarted PiFire itself or left that
      // to be asked for. Setting `done` first rendered one frame of "PiFire is
      // restarting" against stale state, which is precisely the claim this
      // change exists to stop making falsely.
      await load();
      setDone(failed ? "failed" : r.data.percent === REBOOT_REQUIRED_PERCENT ? "reboot" : "ok");
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
    // A restart deferred for the PREVIOUS run has no bearing on this one, and
    // this run will publish a pending flag of its own if it needs to.
    setRestartDeferred(false);
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

      {state.manual_dependency_actions.length > 0 && (
        <section className="pf-admin-card pf-admin-wide" role="alert">
          <h3>Manual dependency action required</h3>
          <p>Complete these platform-specific steps before restarting PiFire:</p>
          <ul>
            {state.manual_dependency_actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </section>
      )}

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
          {/* No "Restart Now" button here any more. The updater restarts PiFire
              itself when the grill is stopped, and when it is lit it publishes
              restart_pending and the modal below asks. A button that appeared
              only while this component happened to be mounted, at the end of a
              run it had to have watched from the start, was the reason updates
              shipped code nothing ever loaded. */}
          {done === "ok" && (
            <p className="pf-update-done">
              {state.manual_dependency_actions.length > 0
                ? "Update complete. Complete the manual dependency actions before restarting PiFire."
                : state.restart_pending
                  ? "Update complete. PiFire must restart to run the new code."
                  : "Update complete — PiFire is restarting to load the new code."}
            </p>
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

      {/* Driven by SERVER state, not by anything this run remembers, so it is
          equally there for the tab that started the update and for a phone
          opening the page an hour later. It stays until something restarts
          PiFire: app.py clears the flag at its own boot, so the ask ends when
          -- and only when -- the code it is asking about is actually loaded. */}
      <ConfirmAction
        open={
          state.restart_pending && state.manual_dependency_actions.length === 0 && !restartDeferred
        }
        title="Restart required — the grill is running"
        message="An update is installed but PiFire is still running the code from before it. Restarting stops the control process, which will drop an active fire, so it was left to you. Restart once the cook is finished."
        confirmLabel="Restart Anyway"
        cancelLabel="Restart Later"
        onConfirm={() => {
          setRestartDeferred(true);
          void runSystemAction("restart");
        }}
        onCancel={() => setRestartDeferred(true)}
      />
    </div>
  );
}
