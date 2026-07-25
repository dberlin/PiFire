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
    // A zero duration must never be sent. The backend parses the seconds
    // segment with is_float() and substitutes 60 seconds for anything
    // non-numeric, so a bad submission arms a timer the user never asked for.
    if (seconds <= 0) {
      setRejected(true);
      return;
    }
    // Flags first: /api/set/timer/start does not touch shutdown/keep_warm, so
    // setting them before starting is safe and makes them live from t=0.
    await command.timerShutdown(shutdown);
    await command.timerKeepWarm(keepWarm);
    await command.timerStart(seconds);
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
