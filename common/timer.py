"""Controller-owned duration timers. Persisted wall coordinates are labels only."""

import math
from collections.abc import Mapping
from typing import Literal, TypedDict, TypeGuard
from uuid import UUID, uuid4

from common.clock_domain import ClockStamp, ClockStampPayload, parse_clock_stamp, stamp_age_s

TIMER_SCHEMA_VERSION = 2
type TimerState = Literal["stopped", "running", "paused", "interrupted", "expired"]


class TimerRecord(TypedDict):
    schema_version: int
    timer_id: str
    state: TimerState
    remaining_s: float | None
    checkpoint: ClockStampPayload | None
    started_wall_s: float | None
    paused_wall_s: float | None
    projected_end_wall_s: float | None
    action_armed: bool


def _finite(value: object) -> TypeGuard[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _timer_state(value: object) -> TypeGuard[TimerState]:
    return value in ("stopped", "running", "paused", "interrupted", "expired")


def _timer_identity(value: object) -> TypeGuard[str]:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _wall(value: object) -> float | None:
    if value is None:
        return None
    if not _finite(value):
        raise ValueError("Timer wall metadata must be finite or unknown")
    return float(value)


def default_timer() -> TimerRecord:
    return {
        "schema_version": TIMER_SCHEMA_VERSION,
        "timer_id": "00000000-0000-0000-0000-000000000000",
        "state": "stopped",
        "remaining_s": 0.0,
        "checkpoint": None,
        "started_wall_s": None,
        "paused_wall_s": None,
        "projected_end_wall_s": None,
        "action_armed": False,
    }


def parse_timer(value: object) -> TimerRecord:
    """Strictly admit current durable records; never coerce legacy epochs."""
    if not isinstance(value, Mapping) or frozenset(value) != TimerRecord.__required_keys__:
        raise ValueError("Timer fields do not match current schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != TIMER_SCHEMA_VERSION:
        raise ValueError("Unsupported timer schema")
    identity = value["timer_id"]
    if not _timer_identity(identity):
        raise ValueError("Invalid timer identity")
    state = value["state"]
    if not _timer_state(state):
        raise ValueError("Invalid timer state")
    remaining = value["remaining_s"]
    if remaining is not None and (not _finite(remaining) or remaining < 0):
        raise ValueError("Timer remaining must be finite and nonnegative")
    checkpoint = parse_clock_stamp(value["checkpoint"])
    if value["checkpoint"] is not None and checkpoint is None:
        raise ValueError("Invalid timer checkpoint")
    armed = value["action_armed"]
    if not isinstance(armed, bool) or (armed and state != "running"):
        raise ValueError("Only running timers may be armed")
    if state == "running" and (checkpoint is None or remaining is None or not armed):
        raise ValueError("Running timer requires a known checkpoint and armed duration")
    if state in ("stopped", "expired") and remaining != 0:
        raise ValueError("Terminal timers have zero remaining")
    if state != "running" and checkpoint is not None:
        raise ValueError("Only running timers have an active checkpoint")
    return {
        "schema_version": TIMER_SCHEMA_VERSION,
        "timer_id": identity,
        "state": state,
        "remaining_s": None if remaining is None else float(remaining),
        "checkpoint": None if checkpoint is None else checkpoint.as_dict(),
        "started_wall_s": _wall(value["started_wall_s"]),
        "paused_wall_s": _wall(value["paused_wall_s"]),
        "projected_end_wall_s": _wall(value["projected_end_wall_s"]),
        "action_armed": armed,
    }


def remaining_seconds(timer: TimerRecord, now: ClockStamp) -> float | None:
    if timer["state"] in ("stopped", "expired"):
        return 0.0
    remaining = timer["remaining_s"]
    if timer["state"] != "running":
        return remaining
    checkpoint = parse_clock_stamp(timer["checkpoint"])
    if checkpoint is None or remaining is None:
        return None
    age = stamp_age_s(
        checkpoint,
        monotonic_s=now.observed_monotonic_s,
        boot_id=now.boot_id,
        runtime_id=now.runtime_id,
        suspend_offset_s=now.suspend_offset_s,
    )
    return None if age is None or not math.isfinite(age) else max(0.0, remaining - age)


def start_timer(seconds: float, now: ClockStamp) -> TimerRecord:
    if not _finite(seconds) or seconds < 0 or not math.isfinite(now.observed_wall_s + seconds):
        raise ValueError("Timer duration must be finite and nonnegative")
    if (
        stamp_age_s(
            now,
            monotonic_s=now.observed_monotonic_s,
            boot_id=now.boot_id,
            runtime_id=now.runtime_id,
            suspend_offset_s=now.suspend_offset_s,
        )
        is None
    ):
        raise ValueError("Timer requires a known current clock domain")
    timer = default_timer()
    timer.update(
        timer_id=str(uuid4()),
        state="running",
        remaining_s=float(seconds),
        checkpoint=now.as_dict(),
        started_wall_s=now.observed_wall_s,
        projected_end_wall_s=now.observed_wall_s + seconds,
        action_armed=True,
    )
    return timer


def pause_timer(timer: TimerRecord, now: ClockStamp, *, interrupted: bool = False) -> TimerRecord:
    result = timer.copy()
    if timer["state"] != "running":
        return result
    remaining = timer["remaining_s"] if interrupted else remaining_seconds(timer, now)
    if remaining is None:
        interrupted = True
        remaining = timer["remaining_s"]
    result.update(
        state="interrupted" if interrupted else "paused",
        remaining_s=remaining,
        checkpoint=None,
        paused_wall_s=now.observed_wall_s,
        action_armed=False,
    )
    return result


def resume_timer(timer: TimerRecord, now: ClockStamp) -> TimerRecord:
    remaining = timer["remaining_s"]
    if timer["state"] not in ("paused", "interrupted") or remaining is None:
        raise ValueError("Resume requires a paused/interrupted timer with known remaining duration")
    result = start_timer(remaining, now)
    result["timer_id"] = timer["timer_id"]
    result["started_wall_s"] = timer["started_wall_s"]
    return result


def expire_timer(timer: TimerRecord, now: ClockStamp) -> tuple[TimerRecord, bool]:
    result = timer.copy()
    if timer["state"] != "running" or not timer["action_armed"] or remaining_seconds(timer, now) != 0:
        return result, False
    result.update(state="expired", remaining_s=0.0, checkpoint=None, action_armed=False)
    return result, True


def checkpoint_timer(timer: TimerRecord, now: ClockStamp) -> TimerRecord:
    result = timer.copy()
    if timer["state"] == "running":
        remaining = remaining_seconds(timer, now)
        if remaining is None:
            return pause_timer(timer, now, interrupted=True)
        result.update(
            remaining_s=remaining, checkpoint=now.as_dict(), projected_end_wall_s=now.observed_wall_s + remaining
        )
    return result


def restore_timer(value: object) -> tuple[TimerRecord, dict[str, object] | None]:
    """Restore without crediting downtime. Preserve legacy input outside authority."""
    try:
        timer = parse_timer(value)
    except ValueError:
        diagnostic = (
            {key: item for key, item in value.items() if isinstance(key, str)}
            if isinstance(value, Mapping)
            else {"invalid_timer": value}
        )
        timer = default_timer()
        if isinstance(value, Mapping) and "schema_version" not in value:
            start, paused, end = value.get("start"), value.get("paused"), value.get("end")
            if start == 0 and paused == 0 and end == 0:
                return timer, diagnostic
            timer.update(state="interrupted", remaining_s=None)
            if all(_finite(item) for item in (start, paused, end)):
                assert _finite(start) and _finite(paused) and _finite(end)
                timer["started_wall_s"] = start
                timer["projected_end_wall_s"] = end
                if paused > 0 and start <= paused <= end:
                    timer["remaining_s"] = max(0.0, end - paused)
                    timer["paused_wall_s"] = paused
        else:
            timer.update(state="interrupted", remaining_s=None)
        return timer, diagnostic
    if timer["state"] == "running":
        timer.update(state="interrupted", checkpoint=None, action_armed=False)
    return timer, None
