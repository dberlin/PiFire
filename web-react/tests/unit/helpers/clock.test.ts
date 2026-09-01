import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";

import { readClock, subscribeToClock } from "../../../src/helpers/clock";

// Epoch seconds, on a whole second so the boundary maths is obvious.
const NOW = 1_700_000_000;

beforeEach(() => {
  rs.useFakeTimers();
  rs.setSystemTime(NOW * 1000);
});

afterEach(() => {
  rs.useRealTimers();
});

describe("readClock", () => {
  it("reports whole epoch seconds", () => {
    expect(readClock()).toBe(NOW);
  });

  it("returns the same value for every call within the same second", () => {
    // The useSyncExternalStore footgun: a getSnapshot that returns a fresh
    // value on each call re-renders forever. Numbers compare by value, so
    // stability means "identical number", and it must survive sub-second
    // movement of the underlying clock.
    const first = readClock();
    rs.advanceTimersByTime(400);
    const second = readClock();
    rs.advanceTimersByTime(599);
    const third = readClock();

    expect(second).toBe(first);
    expect(third).toBe(first);
  });

  it("advances exactly one per second", () => {
    rs.advanceTimersByTime(1_000);
    expect(readClock()).toBe(NOW + 1);
    rs.advanceTimersByTime(10_000);
    expect(readClock()).toBe(NOW + 11);
  });
});

describe("subscribeToClock", () => {
  it("arms exactly one interval no matter how many subscribers attach", () => {
    expect(rs.getTimerCount()).toBe(0);

    const detach = [rs.fn(), rs.fn(), rs.fn()].map((fn) => subscribeToClock(fn));

    expect(rs.getTimerCount()).toBe(1);

    for (const off of detach) off();
  });

  it("notifies every subscriber on each tick", () => {
    const a = rs.fn();
    const b = rs.fn();
    const offA = subscribeToClock(a);
    const offB = subscribeToClock(b);

    rs.advanceTimersByTime(1_000);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);

    rs.advanceTimersByTime(2_000);
    expect(a).toHaveBeenCalledTimes(3);
    expect(b).toHaveBeenCalledTimes(3);

    offA();
    offB();
  });

  it("keeps the interval while any subscriber remains and stops on the last detach", () => {
    const a = rs.fn();
    const b = rs.fn();
    const offA = subscribeToClock(a);
    const offB = subscribeToClock(b);

    offA();
    expect(rs.getTimerCount()).toBe(1);
    rs.advanceTimersByTime(1_000);
    expect(a).not.toHaveBeenCalled();
    expect(b).toHaveBeenCalledTimes(1);

    offB();
    expect(rs.getTimerCount()).toBe(0);
    rs.advanceTimersByTime(5_000);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it("re-arms a fresh interval after a full teardown", () => {
    subscribeToClock(rs.fn())();
    expect(rs.getTimerCount()).toBe(0);

    const later = rs.fn();
    const off = subscribeToClock(later);
    expect(rs.getTimerCount()).toBe(1);
    rs.advanceTimersByTime(1_000);
    expect(later).toHaveBeenCalledTimes(1);

    off();
  });

  it("tolerates a subscriber detaching from inside its own notification", () => {
    const survivor = rs.fn();
    let offSelf = () => {};
    offSelf = subscribeToClock(() => offSelf());
    const offSurvivor = subscribeToClock(survivor);

    rs.advanceTimersByTime(1_000);
    expect(survivor).toHaveBeenCalledTimes(1);
    expect(rs.getTimerCount()).toBe(1);

    offSurvivor();
    expect(rs.getTimerCount()).toBe(0);
  });
});
