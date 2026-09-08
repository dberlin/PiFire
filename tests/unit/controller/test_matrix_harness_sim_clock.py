"""PID-family solves use simulated physical intervals, independent of wall time.

The step scenario resets the target on a solve boundary. Its next solve must
still wait a full period rather than hiding a duplicate timestamp with an offset.
"""

import importlib
import math

import pytest

from controller.runtime.clock import ManualClock
from tools.experiments import controller_matrix
from tools.experiments.controller_matrix import SCENARIOS, Scenario, run_scenario


@pytest.mark.parametrize("scenario_name", ["steady_225", "step_225_275"])
@pytest.mark.parametrize("controller", ["pid", "pid_sp"])
def test_controller_observes_the_intended_control_period(controller, scenario_name, monkeypatch):
    mod = importlib.import_module(f"controller.{controller}")
    real_update = mod.Controller.update
    observed_dts = []
    observed_outputs = []

    def _spy_update(self, current):
        out = real_update(self, current)
        observed_dts.append(self.trace_diagnostics().observed_dt_seconds)
        observed_outputs.append(out)
        return out

    monkeypatch.setattr(mod.Controller, "update", _spy_update)

    row = run_scenario(controller, SCENARIOS[scenario_name], seed=0)

    assert observed_dts, "the controller was never solved"
    # Both PID variants use the pulse-frame fallback cadence.
    period = row["effective_run"]["scheduler"]["frame_seconds"]
    assert observed_dts == pytest.approx([period] * len(observed_dts))

    assert all(math.isfinite(out) for out in observed_outputs)


def test_frame_wall_endpoints_are_sampled_independently(monkeypatch):
    class WallJumpClock(ManualClock):
        def advance(self, seconds):
            super().advance(seconds)
            if self.monotonic() == 10.0:
                self.jump_wall(-3600.0)

    monkeypatch.setattr(controller_matrix, "ManualClock", WallJumpClock)
    observations = []

    def capture(core):
        observe = core.observe_frame

        def capture_frame(frame):
            observations.append(frame)
            return observe(frame)

        monkeypatch.setattr(core, "observe_frame", capture_frame)

    run_scenario("pid_sp", Scenario("wall_jump", 61, [(0, 225.0)]), seed=0, core_setup=capture)

    first, second, third = observations
    assert [(frame.frame_start_s, frame.frame_end_s) for frame in observations] == [
        (0.0, 20.0),
        (20.0, 40.0),
        (40.0, 60.0),
    ]
    assert first.wall_start_ms >= 0
    assert first.wall_end_ms - first.wall_start_ms == -3_580_000
    assert second.wall_start_ms == first.wall_end_ms
    assert second.wall_end_ms - second.wall_start_ms == 20_000
    assert third.wall_start_ms == second.wall_end_ms
    assert third.wall_end_ms - third.wall_start_ms == 20_000
