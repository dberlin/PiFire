import { useState } from "react";
import { useNow } from "../../helpers/clock";
import type { CommandClient } from "../../helpers/command";
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
          onClick={() => command.timerPause()}
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
          onClick={() => command.timerStart(remaining)}
        >
          Resume
        </button>
      ) : null}

      {state !== "stopped" ? (
        <button
          type="button"
          className="pf-timer-btn"
          aria-label="Stop timer"
          // Note: stop also resets the shutdown/keep-warm flags on the control
          // process, which is why the modal always re-sends them on start.
          onClick={() => command.timerStop()}
        >
          Stop
        </button>
      ) : null}

      {modalOpen ? (
        <TimerModal timer={timer} command={command} onClose={() => setModalOpen(false)} />
      ) : null}
    </div>
  );
}
