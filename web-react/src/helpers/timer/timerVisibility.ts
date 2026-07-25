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

/**
 * Hidden by default, revealed by the stopwatch button -- and revealed on its
 * own whenever a timer starts.
 *
 * That last rule is Flask's (`timer.js:150-157`): the poller compares the
 * incoming `start` against the last one it saw and slides the bar down when
 * they differ, so a timer started from another browser, the on-device display
 * or a recipe step surfaces itself instead of ticking away invisibly. The same
 * comparison covers a page load onto an already-running cook, because the last
 * seen value starts at 0.
 *
 * `timerStart` is `LiveState["timer"].start`: 0 when no timer is set,
 * otherwise the epoch second the timer was started at.
 */
export function useTimerVisibility(timerStart: number): TimerVisibility {
  const [visible, setVisible] = useState(false);
  // Deliberately 0 rather than the current `timerStart`, so mounting while a
  // timer is already running counts as a change and reveals the bar.
  const [seenStart, setSeenStart] = useState(0);

  // Adjusted synchronously during render (React's recommended pattern for
  // reacting to changed props) rather than in an effect -- see
  // dashboard/SetpointEntry.tsx for the same shape.
  if (seenStart !== timerStart) {
    setSeenStart(timerStart);
    // Only a timer appearing reveals the bar. Flask reveals on any change of
    // `start`, which means clearing a timer also slides the bar down onto an
    // empty "--:--:--"; that is a side effect of its `!=` test, not something
    // the user asked for, so a timer ending leaves the bar exactly as the user
    // last left it.
    if (timerStart !== 0) setVisible(true);
  }

  const toggle = useCallback(() => setVisible((showing) => !showing), []);

  return { visible, toggle };
}
