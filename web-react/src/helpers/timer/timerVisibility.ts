// Whether the timer bar is showing -- the state behind the navbar's stopwatch
// button, ported from static/js/timer.js:17-27,150-157.
//
// This is UI state, not grill state: it says which pane the user is looking
// at, so it belongs to the shell and never travels on the live socket. It is
// held in React state (not localStorage): Flask starts every page load with
// the bar hidden, and the auto-reveal rule below already puts the bar on
// screen after a reload whenever there is a live timer to look at, so
// persisting a stale choice would only fight that rule.

import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { useCallback, useState } from "react";

export interface TimerVisibility {
  /** Whether the shell should render the timer bar. */
  visible: boolean;
  /** The navbar stopwatch button's click handler. */
  toggle: () => void;
}

/** Reveal active timers without treating stopped/default identities as activity. */
export function useTimerVisibility({
  timerId,
  state,
}: Pick<DashSocketPayload["timer"], "timerId" | "state">): TimerVisibility {
  const [visible, setVisible] = useState(false);
  // undefined means no active timer has been seen; null is a real observation
  // of an active timer whose identity could not be recovered.
  const [seenId, setSeenId] = useState<string | null | undefined>(undefined);
  const active = state === "running" || state === "paused" || state === "interrupted";

  // Adjusted synchronously during render (React's recommended pattern for
  // reacting to changed props) rather than in an effect -- see
  // dashboard/SetpointEntry.tsx for the same shape.
  // Terminal snapshots do not replace the last active identity: a reset UUID
  // must neither reveal the bar nor erase dismissal of that same active timer.
  if (active && seenId !== timerId) {
    setSeenId(timerId);
    setVisible(true);
  }

  const toggle = useCallback(() => setVisible((showing) => !showing), []);

  return { visible, toggle };
}
