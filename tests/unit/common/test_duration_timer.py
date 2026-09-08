from dataclasses import replace

import pytest

from tests.fakes.clock import clock_stamp
from common.timer import (
    checkpoint_timer,
    expire_timer,
    parse_timer,
    pause_timer,
    remaining_seconds,
    restore_timer,
    resume_timer,
    start_timer,
)


def test_wall_jump_pause_resume_and_once_only_expiry():
    timer = start_timer(10, clock_stamp())
    paused = pause_timer(timer, clock_stamp(monotonic_s=103, wall_s=100))
    assert remaining_seconds(paused, clock_stamp(monotonic_s=200, wall_s=9e9)) == 7
    resumed = resume_timer(paused, clock_stamp(monotonic_s=200, wall_s=9e9))
    assert not expire_timer(resumed, clock_stamp(monotonic_s=206.9, wall_s=0))[1]
    expired, fired = expire_timer(resumed, clock_stamp(monotonic_s=207, wall_s=0))
    assert fired
    assert not expire_timer(expired, clock_stamp(monotonic_s=208, wall_s=0))[1]


def test_suspend_retains_last_checkpoint_without_expiring():
    timer = checkpoint_timer(start_timer(10, clock_stamp()), clock_stamp(monotonic_s=103))
    suspended = replace(clock_stamp(monotonic_s=104), suspend_offset_s=63)
    assert remaining_seconds(timer, suspended) is None
    interrupted = pause_timer(timer, suspended, interrupted=True)
    assert interrupted["remaining_s"] == 7
    assert not expire_timer(interrupted, suspended)[1]


def test_restart_preserves_paused_but_disarms_running():
    timer = checkpoint_timer(start_timer(10, clock_stamp()), clock_stamp(monotonic_s=103))
    restored, diagnostic = restore_timer(timer)
    assert restored["state"] == "interrupted"
    assert restored["remaining_s"] == 7
    assert not restored["action_armed"]
    assert diagnostic is None
    paused = pause_timer(timer, clock_stamp(monotonic_s=104))
    assert restore_timer(paused)[0]["state"] == "paused"


def test_legacy_running_unknown_and_paused_known():
    restored, diagnostic = restore_timer({"start": 100, "paused": 0, "end": 200})
    assert restored["remaining_s"] is None
    assert diagnostic == {"start": 100, "paused": 0, "end": 200}
    with pytest.raises(ValueError):
        resume_timer(restored, clock_stamp())
    paused, _ = restore_timer({"start": 100, "paused": 150, "end": 200})
    assert remaining_seconds(resume_timer(paused, clock_stamp()), clock_stamp(monotonic_s=110)) == 40


def test_pause_at_zero_never_creates_expiry_action():
    paused = pause_timer(start_timer(10, clock_stamp()), clock_stamp(monotonic_s=110))
    assert not expire_timer(paused, clock_stamp(monotonic_s=111))[1]


@pytest.mark.parametrize("seconds", [True, -1, float("inf"), float("nan")])
def test_invalid_duration_rejected(seconds):
    with pytest.raises(ValueError):
        start_timer(seconds, clock_stamp())


def test_huge_integer_remaining_is_rejected_without_overflow():
    timer = dict(start_timer(10, clock_stamp()))
    timer["remaining_s"] = 10**1000
    with pytest.raises(ValueError):
        parse_timer(timer)
