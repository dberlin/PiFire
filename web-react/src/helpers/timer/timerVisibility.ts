// Whether the timer bar is showing -- the state behind the navbar's stopwatch
// button, ported from static/js/timer.js:17-27,150-157.
//
// This is UI state, not grill state: it says which pane the user is looking
// at, so it belongs to the shell and never travels on the live socket. It is
// held in React state (not localStorage): Flask starts every page load with
// the bar hidden, and the auto-reveal rule below already puts the bar on
// screen after a reload whenever there is a live timer to look at, so
// persisting a stale choice would only fight that rule.

import { useCallback, useState } from "react";

export interface TimerVisibility {
  /** Whether the shell should render the timer bar. */
  visible: boolean;
  /** The navbar stopwatch button's click handler. */
  toggle: () => void;
}

/** Reveal a newly accepted timer, independent of its wall-clock labels. */
export function useTimerVisibility(timerId: string | null): TimerVisibility {
  const [visible, setVisible] = useState(false);
  const [seenId, setSeenId] = useState<string | null>(null);

  // Adjusted synchronously during render (React's recommended pattern for
  // reacting to changed props) rather than in an effect -- see
  // dashboard/SetpointEntry.tsx for the same shape.
  if (seenId !== timerId) {
    setSeenId(timerId);
    if (timerId !== null) setVisible(true);
  }

  const toggle = useCallback(() => setVisible((showing) => !showing), []);

  return { visible, toggle };
}
