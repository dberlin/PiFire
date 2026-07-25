import { useId, useState } from "react";
import type { CommandClient } from "../../helpers/command";
import type { LiveState } from "../../helpers/types";
import "./shell.css";

// Ported from templates/_macro_timer.html:31-67 -- hours 0-23, minutes 0-59,
// and the two "when the timer expires" flags.

export function TimerModal({
  timer,
  command,
  onClose,
}: {
  timer: LiveState["timer"];
  command: CommandClient;
  onClose: () => void;
}) {
  const [hours, setHours] = useState(0);
  const [minutes, setMinutes] = useState(0);
  const [shutdown, setShutdown] = useState(timer.shutdown);
  const [keepWarm, setKeepWarm] = useState(timer.keepWarm);
  const [rejected, setRejected] = useState(false);

  const ids = useId();
  const hoursId = `${ids}-hours`;
  const minutesId = `${ids}-minutes`;
  const shutdownId = `${ids}-shutdown`;
  const keepWarmId = `${ids}-keep-warm`;

  const seconds = hours * 3600 + minutes * 60;

  // Render-phase adjustment rather than an effect: the complaint is only ever
  // meaningful for the duration that produced it, so a non-zero duration
  // retires it immediately.
  if (rejected && seconds > 0) setRejected(false);

  async function submit() {
    // A zero duration must never be sent. The backend refuses one on this form
    // (a timer that is already expired when armed fires its expiry action at
    // once), so this is only about complaining where the user can see it.
    if (seconds <= 0) {
      setRejected(true);
      return;
    }
    // One request, carrying the flags AND the countdown, which the server turns
    // into one control write. Sent separately the flags are lost: every
    // web-process control write queues the whole control dict read from a blob
    // that does not reflect the queue, and the queued patches are applied with
    // json_patch (RFC 7396), which replaces the notify_data ARRAY wholesale --
    // so the start would land last and undo the flags.
    //
    // What travels is a DURATION, not an end time: the control process judges
    // expiry against its own clock and therefore computes the end itself, so a
    // browser clock running behind the Pi's cannot arm an already-expired timer
    // -- which, with "Shutdown Grill" ticked, would shut the grill down
    // mid-cook. See helpers/command.ts timerStartWithOptions.
    await command.timerStartWithOptions(seconds, { shutdown, keepWarm });
    onClose();
  }

  return (
    <div className="pf-modal-scrim pf-modal-scrim-fixed">
      <div className="pf-modal" role="dialog" aria-modal="true" aria-label="Set Timer">
        <h2 className="pf-modal-title">Set Timer</h2>
        <div className="pf-timer-fields">
          <div className="pf-timer-dial">
            <output className="pf-timer-readout" htmlFor={hoursId}>
              {hours}
            </output>
            <label className="pf-timer-unit" htmlFor={hoursId}>
              Hours
            </label>
            <input
              id={hoursId}
              type="range"
              min={0}
              max={23}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
            />
          </div>

          <div className="pf-timer-dial">
            <output className="pf-timer-readout" htmlFor={minutesId}>
              {minutes}
            </output>
            <label className="pf-timer-unit" htmlFor={minutesId}>
              Minutes
            </label>
            <input
              id={minutesId}
              type="range"
              min={0}
              max={59}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
            />
          </div>

          <p className="pf-timer-expiry">When the timer expires:</p>
          <div className="pf-timer-check">
            <input
              id={shutdownId}
              type="checkbox"
              checked={shutdown}
              onChange={(e) => setShutdown(e.target.checked)}
            />
            <label htmlFor={shutdownId}>Shutdown Grill</label>
          </div>
          <div className="pf-timer-check">
            <input
              id={keepWarmId}
              type="checkbox"
              checked={keepWarm}
              onChange={(e) => setKeepWarm(e.target.checked)}
            />
            <label htmlFor={keepWarmId}>Start Keep Warm</label>
          </div>

          {rejected ? (
            <p className="pf-timer-error" role="alert">
              Set a duration longer than zero before starting the timer.
            </p>
          ) : null}
        </div>
        <div className="pf-modal-actions">
          <button type="button" className="pf-modal-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="pf-modal-btn accent" onClick={submit}>
            Start
          </button>
        </div>
      </div>
    </div>
  );
}
