import { useCallback, useEffect, useState } from "react";
import {
  changeBranch,
  fetchUpdateCheck,
  fetchUpdateLog,
  fetchUpdateState,
  pullUpdate,
  refreshBranches,
  upgradeDeps,
} from "../../helpers/update/updateApi";
import type {
  UpdateCheck,
  UpdateResult,
  UpdateStarted,
  UpdateState,
} from "../../helpers/update/updateTypes";
import "./update.css";

const refusalText = (r: UpdateResult<unknown>): string =>
  r.status === 409 ? "Stop the grill before updating." : r.message;

export function UpdatePage() {
  const [state, setState] = useState<UpdateState | null>(null);
  const [behind, setBehind] = useState<number | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [log, setLog] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const apply = useCallback((s: UpdateResult<UpdateState>, c: UpdateResult<UpdateCheck>) => {
    if (s.ok && s.data) {
      setState(s.data);
      setSelected(s.data.branch);
    }
    setBehind(c.ok && c.data ? c.data.behind : null);
  }, []);

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

  // After a mutation returns started:true, Task 5 begins polling here.
  const run = async (fn: () => Promise<UpdateResult<UpdateStarted>>) => {
    setNote(null);
    setBusy(true);
    const r = await fn();
    setBusy(false);
    if (!r.ok) {
      setNote(refusalText(r));
      return;
    }
    setNote("Started.");
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
          Current: <strong>{state.version}</strong> on branch <strong>{state.branch}</strong>
        </p>
        <p>Remote: {state.remote_version}</p>
        <p>
          {behind === null
            ? "Update status unavailable"
            : behind > 0
              ? `${behind} commits behind`
              : "Up to date"}
        </p>
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
        <button
          type="button"
          className="pf-admin-btn"
          disabled={busy}
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
        {note && <p className="pf-update-note">{note}</p>}
      </section>

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
