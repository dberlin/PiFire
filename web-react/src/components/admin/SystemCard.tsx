import type { SystemAction } from "@pifire/core/contracts/operations";
import { useState } from "react";
import { adminErrorText, factoryReset, systemAction } from "../../helpers/admin/adminApi";
import type { AdminResult } from "../../helpers/admin/adminTypes";
import { ConfirmAction } from "../dashboard/ConfirmAction";

/** The mode the server requires before it will honour any of these. */
const STOPPED = "Stop";

/** The card's own key space: the three system actions plus factory reset,
 * which is a separate endpoint but belongs beside them on the page. */
type CardAction = SystemAction | "factory_reset";

interface Entry {
  key: CardAction;
  label: string;
  /** The confirm dialog's headline. */
  title: string;
  /** What will actually happen, stated plainly. A confirmation the user cannot
   * read the consequence off is a click-through, not a confirmation. */
  message: string;
  /** Shown after the request is accepted. Every one of these ends with the
   * server going away, so there is no state to refetch and no success to
   * observe -- the notice is the entire feedback. */
  done: string;
}

const ENTRIES: Entry[] = [
  {
    key: "reboot",
    label: "Reboot",
    title: "Reboot the system?",
    message:
      "The machine PiFire runs on will power cycle. This page will lose its connection and stay unreachable until the machine comes back up.",
    done: "Reboot requested. The machine is going down now.",
  },
  {
    key: "shutdown",
    label: "Shut Down",
    title: "Shut the system down?",
    message:
      "The machine PiFire runs on will power off and STAY off. Nothing on this page can turn it back on — someone has to power cycle it by hand.",
    done: "Shutdown requested. The machine is powering off now.",
  },
  {
    key: "restart",
    label: "Restart PiFire",
    title: "Restart the PiFire scripts?",
    message:
      "The control process and the web server both restart. The grill is not powered off, but this page will drop its connection for a moment.",
    done: "Restart requested. The scripts are coming back up.",
  },
  {
    key: "factory_reset",
    label: "Restore Factory Defaults",
    title: "Reset everything to factory defaults?",
    message:
      "Settings, control state, cook history AND the pellet database — every profile and every log entry — are replaced with defaults, and PiFire then restarts. Nothing here can be undone.",
    done: "Factory reset requested. PiFire is restarting with default settings.",
  },
];

/**
 * The four actions that can take the machine away from you.
 *
 * Every one goes through ConfirmAction with copy naming the actual
 * consequence, and every one is disabled unless the grill is stopped. That
 * disabled state is a courtesy, not the guard: the server refuses these with
 * 409 `not_stopped` on its own, re-reading control at request time, because a
 * mode this page fetched a minute ago is not what should stand between a web
 * request and a power-off. The 409 is surfaced if the two ever race.
 *
 * There is no `onChanged` refetch here, unlike every other card: all four end
 * with the server gone. The notice is the whole feedback.
 */
export function SystemCard({ mode }: { mode: string }) {
  const [pending, setPending] = useState<Entry | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stopped = mode === STOPPED;

  const confirm = async (entry: Entry) => {
    setPending(null);
    setNotice(null);
    setError(null);
    const result: AdminResult<unknown> =
      entry.key === "factory_reset" ? await factoryReset() : await systemAction(entry.key);
    if (result.ok) setNotice(entry.done);
    else setError(adminErrorText(result));
  };

  return (
    <section className="pf-admin-card" aria-labelledby="admin-system-actions">
      <h2 className="pf-admin-card-title" id="admin-system-actions">
        Power
      </h2>

      {!stopped && (
        <p className="pf-admin-note">
          These are available only while the grill is stopped. It is currently in {mode} mode.
        </p>
      )}
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

      <div className="pf-admin-actions">
        {ENTRIES.map((entry) => (
          <button
            key={entry.key}
            type="button"
            className="pf-admin-btn danger"
            disabled={!stopped}
            onClick={() => setPending(entry)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      <ConfirmAction
        open={pending !== null}
        title={pending?.title ?? ""}
        message={pending?.message}
        onConfirm={() => {
          if (pending) void confirm(pending);
        }}
        onCancel={() => setPending(null)}
      />
    </section>
  );
}
