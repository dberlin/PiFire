import type { CommandClient } from "@pifire/core/command";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { useState } from "react";

import { deriveTimer, formatRemaining } from "../../helpers/timer/timerState";
import { TimerModal } from "./TimerModal";

import "./shell.css";

// The shell owns visibility; the connection owner projects durations even while
// the bar is hidden, so remounting cannot make an old snapshot fresh.
export function TimerBar({
  timer,
  command,
}: {
  timer: DashSocketPayload["timer"];
  command: CommandClient;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  const { state, remaining } = deriveTimer(timer);

  return (
    <div className="pf-timer-bar">
      <span className="pf-timer-time">
        {state === "stopped" ? "--:--:--" : formatRemaining(remaining)}
      </span>
      {state === "interrupted" ? (
        <span role="status">Interrupted — remaining from last checkpoint</span>
      ) : !timer.current && state !== "stopped" ? (
        <span role="status">Last reported</span>
      ) : null}

      {state === "stopped" || state === "expired" || (state === "interrupted" && remaining === null) ? (
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

      {(state === "paused" || state === "interrupted") && remaining !== null ? (
        <button
          type="button"
          className="pf-timer-btn"
          aria-label="Resume timer"
          // Resume is explicit intent; only the controller may rearm it.
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

