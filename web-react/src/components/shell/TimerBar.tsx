import { useState } from "react";
import { useNow } from "../../helpers/clock";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { deriveTimer, formatRemaining } from "../../helpers/timer/timerState";
import type { LiveState } from "../../helpers/types";
import { TimerModal } from "./TimerModal";
import "./shell.css";

// Ported from templates/_macro_timer.html:1-29. Like the Flask bar this one is
// hidden until the navbar's stopwatch button reveals it: the shell renders it
// only while useTimerVisibility (helpers/timer/timerVisibility.ts) says it is
// showing, so hiding the bar also unmounts it and detaches it from the clock.

export function TimerBar({
  timer,
  command,
}: {
  timer: LiveState["timer"];
  command: CommandClient;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  // The remaining time is derived at render from timer + now and never stored.
  // `now` comes from the app's shared clock (helpers/clock.ts) rather than an
  // interval of this component's own, and is subscribed to only while a timer
  // is actually counting down: a stopped or paused bar reads a real time, it
  // just has no reason to be woken when that time changes.
  const ticking = timer.start !== 0 && timer.paused === 0;
  const now = useNow(ticking);

  const { state, remaining } = deriveTimer(timer, now);

  // ONE control write per gesture -- see the block below the component for why a
  // second one inside the same control cycle undoes the first. The write is
  // "landed" once the live timer block changes, which is exactly when the
  // control loop has drained the queue and the socket has republished.
  const signature = `${timer.start}|${timer.paused}|${timer.end}`;
  const [pendingAt, setPendingAt] = useState<string | null>(null);
  // Render-phase adjustment rather than an effect (React Compiler is on, and
  // this is derived state): the guard is only meaningful for the state that was
  // on screen when the button was pressed.
  if (pendingAt !== null && pendingAt !== signature) setPendingAt(null);
  const pending = pendingAt !== null;

  async function write(issue: () => Promise<CommandResult>) {
    setPendingAt(signature);
    const result = await issue();
    // A rejected or failed command queues nothing, so nothing will ever arrive
    // to release the guard. Release it here instead of stranding the controls.
    if (!result.ok) setPendingAt(null);
  }

  return (
    <div className="pf-timer-bar">
      <span className="pf-timer-time">
        {state === "stopped" ? "--:--:--" : formatRemaining(remaining)}
      </span>

      {state === "stopped" ? (
        <button
          type="button"
          className="pf-timer-btn"
          aria-label="Start timer"
          onClick={() => setModalOpen(true)}
        >
          Start timer
        </button>
      ) : null}

      {state === "running" ? (
        <button
          type="button"
          className="pf-timer-btn"
          aria-label="Pause timer"
          disabled={pending}
          onClick={() => write(() => command.timerPause())}
        >
          Pause
        </button>
      ) : null}

      {state === "paused" ? (
        <button
          type="button"
          className="pf-timer-btn"
          aria-label="Resume timer"
          // /api/set/timer/start is ALSO the unpause command: with
          // timer.paused non-zero the backend shifts the existing end time and
          // ignores this argument entirely. It is passed anyway so the call
          // still reads as "run for the time that is left" if that ever changes.
          disabled={pending}
          onClick={() => write(() => command.timerStart(remaining))}
        >
          Resume
        </button>
      ) : null}

      {state !== "stopped" ? (
        <button
          type="button"
          className="pf-timer-btn"
          aria-label="Stop timer"
          // Stop also resets the shutdown/keep-warm flags on the control
          // process, which is why the modal always re-sends them on start.
          // Nothing re-sends them separately: that would be a second write.
          disabled={pending}
          onClick={() => write(() => command.timerStop())}
        >
          Stop
        </button>
      ) : null}

      {/* The modal issues its own single write (timerStartWithOptions) and is
          deliberately NOT covered by the guard: it is reachable only from the
          `stopped` branch, where Pause/Resume/Stop are not rendered at all, so
          its write cannot be followed by another one before the loop publishes.
          Re-arming twice in a cycle is harmless -- each arm write is complete,
          so the later one simply wins. */}
      {modalOpen ? (
        <TimerModal timer={timer} command={command} onClose={() => setModalOpen(false)} />
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------
// Why the write buttons go inert until the socket republishes
//
// Every web-process control write queues the WHOLE control dict
// (common/datastore_accessors.py write_control), read_control() serves the
// persisted blob and never the pending queue, and only the control loop drains
// that queue -- applying each partial with SQLite json_patch (RFC 7396), which
// merges objects key by key but REPLACES arrays wholesale. So two commands
// issued inside one control cycle both read pre-write state, and the one queued
// LAST wins outright, including for keys it never meant to touch.
//
// Pause and Stop are on screen together while a timer runs, and this component
// re-renders only when the live socket republishes -- which cannot happen until
// the loop drains. Clicking Stop and then Pause inside that window used to
// RESURRECT the timer: the pause command read the pre-stop blob, so its partial
// carried the old start/end and the old notify_data, and landed after the
// stop's zeros. Worse than a lost click -- a timer the user stopped came back
// with `shutdown` still armed, and shuts the grill down when it later expires.
// Stop-then-Resume from the paused state is the same bug.
//
// The fix is the invariant the API's 4-argument start form already encodes: one
// control write per operation, carrying everything that operation needs. Here
// that means one write per gesture, and no next gesture until this one is
// visible.
// --------------------------------------------------------------------------
