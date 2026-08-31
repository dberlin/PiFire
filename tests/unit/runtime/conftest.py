import pytest

import controller.runtime.runner as controller_runtime_runner
from controller.runtime.modes.hold import HoldMode
from controller.runtime.runner import ControllerRunner, ControllerUpdateResult
from controller.runtime.state import WorkCycleState
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx
from tests.fakes.learning_trajectory import ExactEstimatorSeedSource
from tests.fakes.probes import FakeProbes


def _output(ratio):
    return ControllerUpdateResult(cycle_ratio=ratio, fan=None, input_temperature=0.0)


def _off():
    return {"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100}




@pytest.fixture
def hold_cycle(monkeypatch):
    """A HoldMode wired to a FakeControllerRunner, driven tick by tick.

    Tasks 8, 9 and 11 call setup() and on_tick() directly rather than running
    a whole work cycle, so this fixture reproduces the state ControlMode.run()
    would otherwise seed before the loop starts.
    """

    def build(runner, *, cycle_data_extra=None, model_store=None, controller="pid_sp", dc_fan=False):
        settings = base_settings()
        settings["controller"]["selected"] = controller
        settings["platform"]["dc_fan"] = dc_fan
        settings["cycle_data"].update(cycle_data_extra or {})
        control_data = base_control(mode="Hold")
        control_data["primary_setpoint"] = 225
        control_data["cook_id"] = "hold-test-cook"
        ctx, _grill, _notifier = make_ctx(settings, control_data, base_pellet_db(), FakeProbes().script([225] * 200))
        if controller == "mpc":
            ctx.learning_trajectory = ExactEstimatorSeedSource()
        if runner is not None:
            if not hasattr(runner, "estimator_seed_requirements"):
                monkeypatch.setattr(
                    runner,
                    "estimator_seed_requirements",
                    ControllerRunner.estimator_seed_requirements.__get__(
                        runner,
                        type(runner),
                    ),
                    raising=False,
                )
            if not hasattr(runner, "bind_estimator_seed_source"):
                monkeypatch.setattr(
                    runner,
                    "bind_estimator_seed_source",
                    ControllerRunner.bind_estimator_seed_source.__get__(
                        runner,
                        type(runner),
                    ),
                    raising=False,
                )
        monkeypatch.setattr(controller_runtime_runner, "build_runner", lambda *a, **k: (runner, "Active"))
        mode = HoldMode(ctx, WorkCycleState())
        mode.settings = settings
        mode.control = control_data
        mode._model_store = model_store
        mode.state.manual_override = {"igniter": 0, "auger": 0, "fan": 0, "power": 0, "pwm": 0}
        return mode

    return build
