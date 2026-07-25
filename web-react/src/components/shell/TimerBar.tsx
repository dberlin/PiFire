import { useCallback, useState, useSyncExternalStore } from "react";
import type { CommandClient } from "../../helpers/command";
import { deriveTimer, formatRemaining } from "../../helpers/timer/timerState";
import type { LiveState } from "../../helpers/types";
import { TimerModal } from "./TimerModal";
import "./shell.css";

// Ported from templates/_macro_timer.html:1-29. The Flask bar is hidden until
// the navbar stopwatch reveals it; here it is always present, so the timer is
// one click away from every page instead of behind a toggle.

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

export function TimerBar({
  timer,
  command,
}: {
  timer: LiveState["timer"];
  command: CommandClient;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  // The wall clock is an external mutable source, so it is subscribed to rather
  // than mirrored into state: the remaining time is derived at render from
  // timer + now and never stored. A running timer is the only state whose
  // display changes on its own, so no interval is armed when stopped or paused
  // -- and the snapshot is then a constant, which keeps renders quiet too.
  const ticking = timer.start !== 0 && timer.paused === 0;
  const subscribe = useCallback(
    (onClockTick: () => void) => {
      if (!ticking) return () => {};
      const id = window.setInterval(onClockTick, 1000);
      return () => window.clearInterval(id);
    },
    [ticking],
  );
  const readClock = useCallback(() => (ticking ? nowSeconds() : 0), [ticking]);
  const now = useSyncExternalStore(subscribe, readClock, readClock);

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
