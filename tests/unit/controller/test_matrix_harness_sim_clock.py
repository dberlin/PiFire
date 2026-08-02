"""Pins run_scenario's simulated clock for controllers that read time.time().

pid_sp and pid_ac compute their own `dt = time.time() - self.last_update`.
run_scenario calls `core.update()` in a tight loop with no real elapsed wall
time between iterations, so without a simulated clock `dt` collapses to
whatever a handful of Python bytecodes take -- orders of magnitude smaller
than the control period the loop is modeling. Both controllers divide by
`dt` when computing rate-of-change and derivative terms, so an unrealistic
`dt` saturates their output regardless of the temperature error, and the
controller never gets exercised at all. This test asserts on the `dt` the
controller actually observed and on its output staying in a sane range,
because a test that only checks the final temperature would not distinguish
"the controller worked" from "the controller was driven off a cliff and the
plant happened to end up somewhere plausible anyway".

`step_225_275` is included because its setpoint change lands exactly on a
solve boundary: `set_target()` also resets the controller's own last-update
clock, so a solve scheduled for the same tick would otherwise hand it dt=0.
"""

import importlib

import pytest

from docs.superpowers.experiments.controller_matrix import CYCLE_DATA, SCENARIOS, run_scenario


@pytest.mark.parametrize("scenario_name", ["steady_225", "step_225_275"])
@pytest.mark.parametrize("controller", ["pid_sp", "pid_ac"])
def test_controller_observes_the_intended_control_period(controller, scenario_name, monkeypatch):
    mod = importlib.import_module(f"controller.{controller}")
    real_update = mod.Controller.update
    observed_dts = []
    observed_outputs = []

    def _spy_update(self, current):
        before = self.last_update
        out = real_update(self, current)
        observed_dts.append(self.last_update - before)
        observed_outputs.append(out)
        return out

    monkeypatch.setattr(mod.Controller, "update", _spy_update)

    run_scenario(controller, SCENARIOS[scenario_name], seed=0)

    assert observed_dts, "the controller was never solved"
    period = CYCLE_DATA["HoldCycleTime"]
    assert observed_dts == pytest.approx([period] * len(observed_dts))

    # Not [u_min, u_max] -- the controller's raw output is clamped to that
    # range by run_scenario, not by the controller itself, so a legitimate
    # excursion just outside it is fine. What a broken dt actually produces
    # is many orders of magnitude larger than any of this.
    assert all(-50.0 <= out <= 50.0 for out in observed_outputs)
