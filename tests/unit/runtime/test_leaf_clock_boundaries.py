"""Timed leaf modes retain physical release boundaries across wall corrections."""

import pytest

from controller.runtime.clock import ManualClock
from controller.runtime.modes.base import ControlMode
from controller.runtime.modes.prime import PrimeMode
from controller.runtime.modes.reignite import ReigniteMode
from controller.runtime.modes.shutdown import ShutdownMode
from controller.runtime.modes.startup import StartupMode
from controller.runtime.state import WorkCycleState
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx
from tests.fakes.probes import FakeProbes


@pytest.mark.parametrize("mode_type", [StartupMode, ReigniteMode, ShutdownMode, PrimeMode])
@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_timed_leaf_release_and_epoch_provenance_ignore_wall_jumps(mode_type: type[ControlMode], jump: float):
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=10.0)
    settings = base_settings()
    settings["startup"]["duration"] = 60.0
    settings["shutdown"]["shutdown_duration"] = 60.0
    control = base_control(mode=mode_type.name)
    control["prime_amount"] = 18.0
    control["startup_timestamp"] = 1_799_999_700.0
    pellet_db = base_pellet_db()
    ctx, grill, _notifier = make_ctx(settings, control, pellet_db, FakeProbes())
    ctx.clock = clock
    mode = mode_type(ctx, WorkCycleState())
    mode.settings = settings
    mode.control = control
    mode.setup()
    try:
        assert mode.setup_safety(100.0) == "Active"
        mode.state.timers.start_time = clock.monotonic()
        mode.state.timers.start_wall_time = clock.wall_time()
        startup_timestamp = 1_800_000_000.0 if mode_type is StartupMode else 1_799_999_700.0
        assert mode.control["startup_timestamp"] == startup_timestamp

        clock.jump_wall(jump)
        clock.advance(5.0)
        mode._last_now = clock.monotonic()
        assert mode.should_exit(clock.monotonic(), 100.0) is False
        status = mode._build_status_data(mode.control, pellet_db, mode.state.timers.start_time)
        assert status["remaining_seconds"] == 55.0
        assert status["elapsed_seconds"] == 5.0
        assert status["start_time"] == 1_800_000_000.0
        assert status["startup_timestamp"] == startup_timestamp
        if mode_type is ShutdownMode:
            assert grill.get_output_status()["fan"] is True
            assert grill.get_output_status()["power"] is True

        clock.advance(55.0)
        assert mode.should_exit(clock.monotonic(), 100.0) is False
        clock.advance(0.05)
        assert mode.should_exit(clock.monotonic(), 100.0) is True
    finally:
        grill.auger_off()
        grill.igniter_off()
        mode.teardown(100.0, acquired_at_s=None)
