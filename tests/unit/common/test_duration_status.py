from dataclasses import replace

import pytest

from common.duration_status import project_duration_status
from tests.fakes.clock import clock_stamp


def _status() -> dict[str, object]:
    return {
        "elapsed_seconds": 10.0,
        "remaining_seconds": 20.0,
        "lid_open_remaining_seconds": 4.0,
        "cook_elapsed_seconds": 80.0,
        "running": True,
        "clock_stamp": clock_stamp(monotonic_s=100.0).as_dict(),
    }


@pytest.mark.parametrize("wall_delta", [-3600.0, 3600.0])
def test_duration_projection_accounts_for_status_age_without_wall_authority(wall_delta: float):
    now = clock_stamp(monotonic_s=105.0, wall_s=1_800_000_000.0 + wall_delta)
    result = project_duration_status(_status(), current=now, heartbeat=now)
    assert result == {
        "modeElapsedS": 15.0,
        "modeRemainingS": 15.0,
        "lidRemainingS": 0.0,
        "cookElapsedS": 85.0,
        "current": True,
        "running": True,
    }


def test_fresh_heartbeat_does_not_rejuvenate_stale_duration_status():
    now = clock_stamp(monotonic_s=116.0)
    result = project_duration_status(_status(), current=now, heartbeat=now)
    assert result["current"] is False
    assert result["modeElapsedS"] == 10.0
    assert result["modeRemainingS"] == 20.0
    assert result["cookElapsedS"] == 80.0


@pytest.mark.parametrize("failure", ["boot", "runtime", "suspend", "future", "heartbeat"])
def test_untrusted_duration_snapshot_retains_values_without_advancing(failure: str):
    now = clock_stamp(monotonic_s=105.0)
    heartbeat = now
    if failure == "boot":
        now = replace(now, boot_id="f5398d50-5456-4ba8-8e8c-5baf55bf7381")
    elif failure == "runtime":
        heartbeat = replace(now, runtime_id="01b4a094-8a2c-4780-915d-081f5a850a97")
    elif failure == "suspend":
        now = replace(now, suspend_offset_s=62.001)
    elif failure == "future":
        now = replace(now, observed_monotonic_s=99.0)
    else:
        heartbeat = None
    result = project_duration_status(_status(), current=now, heartbeat=heartbeat)
    assert result["current"] is False
    assert result["cookElapsedS"] == 80.0
    assert result["modeRemainingS"] == 20.0


def test_paused_and_unknown_durations_do_not_invent_progress():
    status = _status()
    status.update(running=False, cook_elapsed_seconds=None)
    now = clock_stamp(monotonic_s=105.0)
    result = project_duration_status(status, current=now, heartbeat=now)
    assert result["current"] is True
    assert result["running"] is False
    assert result["modeElapsedS"] == 10.0
    assert result["cookElapsedS"] is None
