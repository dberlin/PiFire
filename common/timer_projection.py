"""One public timer projection for HTTP and socket consumers."""

from collections.abc import Mapping

from common.clock_domain import ClockStamp, parse_clock_stamp, qualified_stamp_age_s
from common.duration_status import DURATION_STALE_AFTER_S
from common.timer import parse_timer
from common.web_contracts.core import TimerPayload


def project_timer_status(
    timer_data: object,
    options: Mapping[str, bool],
    *,
    current: ClockStamp | None,
    heartbeat: ClockStamp | None,
) -> TimerPayload:
    try:
        timer = parse_timer(timer_data)
    except ValueError:
        return TimerPayload(
            timerId=None,
            state="interrupted",
            remainingS=None,
            current=False,
            startedWallS=None,
            projectedEndWallS=None,
            keepWarm=options["keep_warm"],
            shutdown=options["shutdown"],
        )
    age = qualified_stamp_age_s(
        parse_clock_stamp(timer["checkpoint"]),
        heartbeat=heartbeat,
        current=current,
        heartbeat_stale_after_s=DURATION_STALE_AFTER_S,
    )
    fresh = age is not None and age <= DURATION_STALE_AFTER_S
    remaining = timer["remaining_s"]
    if timer["state"] == "running" and fresh and age is not None and remaining is not None:
        remaining = max(0.0, remaining - age)
    return TimerPayload(
        timerId=timer["timer_id"],
        state=timer["state"],
        remainingS=remaining,
        current=fresh,
        startedWallS=timer["started_wall_s"],
        projectedEndWallS=timer["projected_end_wall_s"],
        keepWarm=options["keep_warm"],
        shutdown=options["shutdown"],
    )
