import type { CommandClient } from "@pifire/core/command";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { useState } from "react";

import { useNow } from "../../helpers/clock";
import { deriveTimer, formatRemaining } from "../../helpers/timer/timerState";
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
  timer: DashSocketPayload["timer"];
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
          // Stop also resets the shutdown/keep-warm flags on the control
          // process, which is why the modal always re-sends them on start.
          // Nothing re-sends them separately: there is nothing to restore, and
          // a standalone flag write only arms an expiry action on a timer that
          // is not running.
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

// Every timer gesture queues an OP, not a computed timer state
// (common/control_delta.py). Two gestures in one control cycle compose in the
// drain against live state -- a stop followed by a pause pauses a timer that is
// already cleared, i.e. nothing -- so the bar does not need to serialize them.
// Pinned in Python at tests/characterization/test_control_delta_seam.py and
// here by the ControlProcess model in TimerBar.controlCycle.test.tsx.
