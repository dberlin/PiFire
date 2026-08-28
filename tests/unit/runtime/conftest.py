from hashlib import sha256
from math import ceil

import pytest

import controller.runtime.runner as controller_runtime_runner
from controller.mpc_model import EstimatorSeed
from controller.runtime.modes.hold import HoldMode
from controller.runtime.runner import ControllerUpdateResult
from controller.runtime.state import WorkCycleState
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx
from tests.fakes.probes import FakeProbes


def _output(ratio):
    return ControllerUpdateResult(cycle_ratio=ratio, fan=None, input_temperature=0.0)


def _off():
    return {"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100}



class _ExactSeedSource:
    def seed_for(
        self,
        theta: float,
        n_delay: int,
        at_ms: int,
        measured_temp_c: float,
    ) -> EstimatorSeed:
        del at_ms
        required = 0 if n_delay == 0 else min(180, ceil(3.0 * theta / 20.0))
        digest = sha256(f"hold-test-seed:{theta!r}:{n_delay}".encode()).hexdigest()
        return EstimatorSeed(
            delay_states=(0.0,) * n_delay,
            chamber_temperature_c=measured_temp_c,
            disturbance=0.0,
            segment_id="hold-test-segment",
            pre_roll_digest=digest,
            pre_roll_frame_count=required,
            required_frame_count=required,
            status="exact",
        )

    def intervention(self, boundary) -> None:
        del boundary

    def configuration_changed(self, boundary) -> None:
        del boundary

    def observe_hold_frame(self, observation, *, replay_only=False) -> None:
        del observation, replay_only


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
        ctx, _grill, _notifier = make_ctx(settings, control_data, base_pellet_db(), FakeProbes().script([225] * 200))
        if controller == "mpc":
            ctx.learning_trajectory = _ExactSeedSource()
        monkeypatch.setattr(controller_runtime_runner, "build_runner", lambda *a, **k: (runner, "Active"))
        mode = HoldMode(ctx, WorkCycleState())
        mode.settings = settings
        mode.control = control_data
        mode._model_store = model_store
        mode.state.manual_override = {"igniter": 0, "auger": 0, "fan": 0, "power": 0, "pwm": 0}
        return mode

    return build
