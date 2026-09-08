import math

import pytest

from controller.runtime.clock import CallableClock, ManualClock, RealClock


def test_manual_clock_starts_at_zero_and_sleep_advances() -> None:
    clock = ManualClock()
    assert clock.wall_time() == clock.monotonic() == 0.0
    clock.sleep(0.5)
    assert clock.wall_time() == clock.monotonic() == 0.5


def test_manual_clock_wall_jumps_preserve_control_time() -> None:
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=10.0)
    clock.jump_wall(-3600.0)
    assert clock.wall_time() == 1_799_996_400.0
    assert clock.monotonic() == 10.0
    clock.sleep(2.0)
    assert clock.wall_time() == 1_799_996_402.0
    assert clock.monotonic() == 12.0
    clock.jump_wall(7200.0)
    assert clock.wall_time() == 1_800_003_602.0
    assert clock.monotonic() == 12.0
    clock.advance(3.0)
    assert clock.wall_time() == 1_800_003_605.0
    assert clock.monotonic() == 15.0


@pytest.mark.parametrize("seconds", [-1.0, math.inf, -math.inf, math.nan])
@pytest.mark.parametrize("through_sleep", [False, True])
def test_manual_clock_invalid_advance_preserves_both_axes(seconds: float, through_sleep: bool) -> None:
    clock = ManualClock(wall_start=100.0, monotonic_start=10.0)
    advance = clock.sleep if through_sleep else clock.advance
    with pytest.raises(ValueError):
        advance(seconds)
    assert clock.wall_time() == 100.0
    assert clock.monotonic() == 10.0


def test_manual_clock_zero_advance_preserves_both_axes() -> None:
    clock = ManualClock(wall_start=100.0, monotonic_start=-20.0)
    clock.advance(0.0)
    assert clock.wall_time() == 100.0
    assert clock.monotonic() == -20.0


def test_callable_clock_samples_independent_sources() -> None:
    wall = ManualClock(wall_start=100.0)
    steady = ManualClock(monotonic_start=10.0)
    clock = CallableClock(wall_clock=wall.wall_time, monotonic_clock=steady.monotonic)
    wall.jump_wall(-30.0)
    assert clock.wall_time() == 70.0
    assert clock.monotonic() == 10.0
    steady.advance(2.0)
    assert clock.wall_time() == 70.0
    assert clock.monotonic() == 12.0


def test_real_clock_sleep_advances_steady_time() -> None:
    clock = RealClock()
    before = clock.monotonic()
    clock.sleep(0.001)
    assert clock.monotonic() >= before + 0.001
    assert math.isfinite(clock.wall_time())
