#!/usr/bin/env python3
"""Exercise the real acados controller through Hold's framed lifecycle."""

from __future__ import annotations

import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import controller.runtime.runner as runner_module
from common.controller_model_state import ControllerModelStore
from controller.mpc import Controller
from controller.runtime.modes.hold import HoldMode
from controller.runtime.runner import SyncControllerRunner
from controller.runtime.state import WorkCycleState
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx
from tests.fakes.learning_trajectory import bind_exact_learning_inputs
from tests.fakes.probes import FakeProbes


def main() -> int:
    settings = base_settings()
    settings["controller"]["selected"] = "mpc"
    mpc_config = settings["controller"]["config"]["mpc"]
    mpc_config["enable_online_adaptation"] = True
    mpc_config["enable_identification"] = True
    control = base_control(mode="Hold")
    control["primary_setpoint"] = 225
    context, grill, _notifier = make_ctx(
        settings,
        control,
        base_pellet_db(),
        FakeProbes().script([180.0] * 100),
    )
    bind_exact_learning_inputs(context, control, cook_id="smoke-acados-hold")

    persisted: dict[str, object] = {}
    source = Controller(mpc_config, "F", settings["cycle_data"])
    source.set_target(control["primary_setpoint"])
    source_snapshot = source.get_model_snapshot()
    source.close()
    if source_snapshot is None:
        raise RuntimeError("source MPC owner did not produce a restorable checkpoint")

    def read_model(_key: str):
        if "state" not in persisted:
            raise TypeError("missing model state")
        return persisted["state"]

    def write_model(_key: str, value: object) -> None:
        persisted["state"] = value

    model_store = ControllerModelStore(reader=read_model, writer=write_model)
    if not model_store.save("mpc", source_snapshot):
        raise RuntimeError("source MPC checkpoint was not persisted")
    core = Controller(mpc_config, "F", settings["cycle_data"])
    core.set_target(control["primary_setpoint"])
    completed_frames = 0
    observe_frame = core.observe_frame

    def observe_and_count(observation):
        nonlocal completed_frames
        outcome = observe_frame(observation)
        completed_frames += 1
        return outcome

    core.observe_frame = observe_and_count
    runner = SyncControllerRunner(core)
    original_build_runner = runner_module.build_runner
    mode: HoldMode | None = None
    torn_down = False
    runner_module.build_runner = lambda *_args, **_kwargs: (runner, "Active")
    try:
        mode = HoldMode(context, WorkCycleState())
        mode.settings = settings
        mode.control = control
        mode._model_store = model_store
        mode.state.manual_override = {
            "igniter": 0,
            "auger": 0,
            "fan": 0,
            "power": 0,
            "pwm": 0,
        }
        mode.setup()
        if core.active_control_pair.core.set_point_c != (control["primary_setpoint"] - 32.0) * 5.0 / 9.0:
            raise RuntimeError("restored MPC owner lost the live Hold target")
        mode.state.metrics = {"id": "smoke-acados-hold"}

        clock_now = getattr(context.clock, "now", None)
        clock_advance = getattr(context.clock, "advance", None)
        if not callable(clock_now) or not callable(clock_advance):
            raise RuntimeError("smoke context requires a controllable clock")  # noqa: TRY004  invariant on already-normalized input, not caller type validation

        for _ in range(45):
            mode.on_tick(clock_now(), 180.0, grill.get_output_status())
            clock_advance(1.0)

        if completed_frames < 1:
            raise RuntimeError("Hold did not deliver a completed framed observation")
        if not any(name == "auger_on" for name, _args in grill.calls):
            raise RuntimeError("Hold never commanded heat below setpoint")
        controller_status = core.get_status()
        if "applied_combustion_load" not in controller_status:
            raise RuntimeError("acados solve/applied-output feedback did not reach the controller")

        mode.teardown(180.0)
        torn_down = True
        checkpoint = model_store.load("mpc")
        if checkpoint is None:
            raise RuntimeError("final teardown lost the durable model checkpoint")
        if not core.active_control_pair.core.close_complete:
            raise RuntimeError("native controller resources remained open after teardown")
        print(
            "acados Hold smoke passed: "
            f"frames={completed_frames} "
            f"target_f={control['primary_setpoint']} "
            f"revision={checkpoint['revision']}"
        )
        return 0
    finally:
        runner_module.build_runner = original_build_runner
        if mode is not None and not torn_down:
            mode.teardown(180.0)


if __name__ == "__main__":
    raise SystemExit(main())
