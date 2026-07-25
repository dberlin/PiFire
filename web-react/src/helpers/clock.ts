// The app's single wall clock.
//
// Anything that displays elapsed or remaining time needs a "now" that advances
// on its own. Each such component arming its own setInterval would mean N
// timers ticking out of phase with each other, so the interval lives here
// instead: one module-level interval, shared by every subscriber, armed when
// the first subscriber attaches and disarmed when the last one detaches. A
// screen with nothing counting therefore costs no timer at all.
//
// The clock is an external mutable source, so components subscribe to it with
// useSyncExternalStore rather than mirroring it into state via an effect --
// "now" is never stored, only read during render and passed to pure helpers
// such as deriveTimer().

import { useSyncExternalStore } from "react";

const TICK_MS = 1000;

const subscribers = new Set<() => void>();
let intervalId: ReturnType<typeof setInterval> | null = null;

/**
 * The current time in whole epoch SECONDS -- the unit the control process's
 * timer block uses, so callers never have to convert.
 *
 * This is useSyncExternalStore's getSnapshot, which means it must be
 * referentially stable whenever the underlying value has not changed, or React
 * re-renders forever. A number is a primitive compared by value, and flooring
 * to seconds means every call inside the same second returns the identical
 * value -- so stability holds by construction, with no cached snapshot to keep
 * in sync and no sentinel value standing in for "not ticking".
 */
export function readClock(): number {
  return Math.floor(Date.now() / 1000);
}

/**
 * Attach to the shared clock; returns the detach function.
 *
 * The interval is created only for the first subscriber and cleared by the
 * last one, so subscribe/unsubscribe churn never leaks timers.
 */
export function subscribeToClock(onTick: () => void): () => void {
  subscribers.add(onTick);
  if (intervalId === null) {
    intervalId = setInterval(() => {
      // A copy, so a subscriber that detaches while being notified cannot
      // mutate the set mid-iteration.
      for (const notify of Array.from(subscribers)) notify();
    }, TICK_MS);
  }
  return () => {
    subscribers.delete(onTick);
    if (subscribers.size === 0 && intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }
  };
}

const detachNothing = () => {};
/** A subscribe that never fires, for components whose display is not moving. */
function subscribeToNothing(): () => void {
  return detachNothing;
}

/**
 * Read "now" in whole epoch seconds, re-rendering once a second while
 * `ticking` is true.
 *
 * When `ticking` is false the component simply does not subscribe -- so the
 * shared interval is not armed on its behalf -- yet the value returned is
 * still a real time rather than a placeholder. It just stops being refreshed,
 * which is exactly right for a display that is frozen (a paused timer) or
 * absent (a stopped one).
 *
 * Both getSnapshot and getServerSnapshot are `readClock`: there is no server
 * render in this app, and if one is ever added the clock is the one value that
 * is legitimately readable on both sides.
 */
export function useNow(ticking: boolean): number {
  return useSyncExternalStore(
    ticking ? subscribeToClock : subscribeToNothing,
    readClock,
    readClock,
  );
}
