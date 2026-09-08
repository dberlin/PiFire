// Calendar labels only. Duration receipt clocks live in useLiveState and use
// @pifire/core/liveConnection's monotonicNowMs; never subtract these epochs.

import { useSyncExternalStore } from "react";

const TICK_MS = 1000;

const subscribers = new Set<() => void>();
let intervalId: ReturnType<typeof setInterval> | null = null;

/**
 * The current time in whole epoch SECONDS, for calendar labels only.
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
 * Unsubscribed calendar labels read the current epoch without arming a timer.
 */
export function useWallNow(ticking: boolean): number {
  return useSyncExternalStore(
    ticking ? subscribeToClock : subscribeToNothing,
    readClock,
    readClock,
  );
}
