import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { useId, useState } from "react";
import type { CommandClient } from "../../helpers/command";
import "./shell.css";

// Ported from templates/_macro_timer.html:31-67 -- hours 0-23, minutes 0-59,
// and what happens when the timer expires. Flask offers that last part as two
// independent checkboxes; this offers one choice, because the backend only
// honours one (see the note on `action` below).

type TimerAction = "none" | "shutdown" | "keepWarm";

const TIMER_ACTIONS: { value: TimerAction; label: string }[] = [
  { value: "none", label: "Nothing" },
  { value: "shutdown", label: "Shutdown Grill" },
  { value: "keepWarm", label: "Start Keep Warm" },
];

export function TimerModal({
  timer,
  command,
  onClose,
}: {
  timer: DashSocketPayload["timer"];
  command: CommandClient;
  onClose: () => void;
}) {
  const [hours, setHours] = useState(0);
  const [minutes, setMinutes] = useState(0);
  // One choice, not two independent flags: the backend runs
  // `if shutdown: ... elif keep_warm: ...` (notify/notifications.py:141-159), so
  // arming both silently drops keep-warm. A user who ticked "Start Keep Warm"
  // expecting their food held at temperature would get the grill shut down
  // instead. ProbeNotifyModal models the same backend rule the same way.
  const [action, setAction] = useState<TimerAction>(
    timer.shutdown ? "shutdown" : timer.keepWarm ? "keepWarm" : "none",
  );
  const [rejected, setRejected] = useState(false);

  const ids = useId();
  const hoursId = `${ids}-hours`;
  const minutesId = `${ids}-minutes`;
  const actionName = `${ids}-expiry-action`;

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
    // One request, carrying the flags AND the countdown. What travels is a
    // DURATION, not an end time: the control process judges expiry against its
    // own clock and therefore computes the end itself, so a browser clock
    // running behind the Pi's cannot arm an already-expired timer -- which,
    // with "Shutdown Grill" ticked, would shut the grill down mid-cook. That,
    // plus this form's refusal of a zero/negative/non-numeric duration and of a
    // paused timer, is why it is used rather than the bare start command plus
    // two flag writes. See helpers/command.ts timerStartWithOptions.
    await command.timerStartWithOptions(seconds, {
      shutdown: action === "shutdown",
      keepWarm: action === "keepWarm",
    });
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
          {TIMER_ACTIONS.map((a) => {
            const id = `${actionName}-${a.value}`;
            return (
              <div className="pf-timer-check" key={a.value}>
                <input
                  id={id}
                  type="radio"
                  name={actionName}
                  value={a.value}
                  checked={action === a.value}
                  onChange={() => setAction(a.value)}
                />
                <label htmlFor={id}>{a.label}</label>
              </div>
            );
          })}

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
