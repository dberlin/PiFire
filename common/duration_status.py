"""Project admitted duration snapshots without using calendar endpoints."""

import math
from collections.abc import Mapping
from typing import TypedDict

from common.clock_domain import ClockStamp, parse_clock_stamp, qualified_stamp_age_s

DURATION_STALE_AFTER_S = 15.0


class DurationStatus(TypedDict):
    modeElapsedS: float | None
    modeRemainingS: float | None
    lidRemainingS: float | None
    cookElapsedS: float | None
    current: bool
    running: bool


def _duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def project_duration_status(
    status: Mapping[str, object],
    *,
    current: ClockStamp | None,
    heartbeat: ClockStamp | None,
) -> DurationStatus:
    """Keep stale values as snapshots; only a fresh admitted sample can advance."""
    age = qualified_stamp_age_s(
        parse_clock_stamp(status.get("clock_stamp")),
        heartbeat=heartbeat,
        current=current,
        heartbeat_stale_after_s=DURATION_STALE_AFTER_S,
    )
    fresh = age is not None and age <= DURATION_STALE_AFTER_S
    running = status.get("running") is True
    elapsed = age if fresh and running and age is not None else 0.0
    mode_elapsed = _duration(status.get("elapsed_seconds"))
    mode_remaining = _duration(status.get("remaining_seconds"))
    lid_remaining = _duration(status.get("lid_open_remaining_seconds"))
    cook_elapsed = _duration(status.get("cook_elapsed_seconds"))
    return {
        "modeElapsedS": None if mode_elapsed is None else mode_elapsed + elapsed,
        "modeRemainingS": None if mode_remaining is None else max(0.0, mode_remaining - elapsed),
        "lidRemainingS": None if lid_remaining is None else max(0.0, lid_remaining - elapsed),
        "cookElapsedS": None if cook_elapsed is None else cook_elapsed + elapsed,
        "current": fresh,
        "running": running,
    }
