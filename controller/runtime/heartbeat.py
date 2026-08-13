"""Liveness stamp published by the control process for the web process.

The two processes share only SQLite, and SQLite has no cross-process change
notification (`sqlite3_update_hook` is in-process only), so there is nothing
for a reader to block on -- every cross-process signal here is a poll. What a
poll costs, though, depends entirely on its shape:

* A REQUEST/RESPONSE probe (push "check_alive" onto queue_systemq, wait for the
  control loop to answer on queue_systemo) needs the control process to
  cooperate, and a stopped process cannot. The waiter therefore always runs out
  its full timeout in the down case -- the exact case it exists to detect -- so
  it has to be run rarely, which makes both detection AND recovery slow.
* A HEARTBEAT is a plain read of a value the control loop already stamps as it
  goes. The reader compares it against its own clock and needs no cooperation
  from a process that may be gone. It costs one SELECT, so it can run every
  second, and recovery is immediate: a freshly restarted control process stamps
  on its first tick and the next read sees it.

This module is the second shape.
"""

from common.persistence.runtime import CONTROL_HEARTBEAT_KEY

#: How often the control process refreshes the stamp. Both call sites run far
#: hotter than this (the idle tick every 0.1s, the mode work cycle every
#: 0.05s), so without a throttle this would be ~10-20 datastore writes a second
#: for a value nobody reads faster than once a second.
HEARTBEAT_WRITE_INTERVAL = 1.0

#: None means "never stamped in this process", which is NOT the same as
#: "stamped at t=0" -- a 0.0 sentinel suppresses the very first stamp whenever
#: the clock reads below HEARTBEAT_WRITE_INTERVAL, so the first heartbeat of a
#: run goes missing until a full interval of uptime has passed.
_last_write = None


def stamp_control_heartbeat(ctx):
    """Refresh the control-process liveness stamp, at most every
    HEARTBEAT_WRITE_INTERVAL.

    Called from the idle tick AND from the per-mode work cycle: a cook never
    returns to the idle tick, so stamping in only one of the two would read as
    "control is down" for the whole cook.

    The stamped value is `ctx.clock.now()`, which RealClock defines as
    `time.time()` -- the reader is a different PROCESS comparing this against
    its own `time.time()`, so the stamp has to be wall-clock epoch seconds and
    not a monotonic or otherwise process-local reading.
    """
    global _last_write
    now = ctx.clock.now()
    if _last_write is not None and now - _last_write < HEARTBEAT_WRITE_INTERVAL:
        return
    _last_write = now
    ctx.store.write_generic_key(CONTROL_HEARTBEAT_KEY, now)


def reset_for_tests():
    """Clear the throttle so a test's first stamp always writes."""
    global _last_write
    _last_write = None
