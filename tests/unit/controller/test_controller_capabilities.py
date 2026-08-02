"""Every controller answers the four capability methods; the defaults are inert.

A controller that does not model the plant must be completely unaffected by
applied-output feedback, diagnostics, and model persistence existing.
"""

import importlib

import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerBase

# Controllers with no optional dependency, so this runs on every install.
PLAIN_CONTROLLERS = ["pid", "pid_clamping", "pid_clamping_percent_pb", "pid_ac", "pid_parallel", "pid_sp"]

CYCLE_DATA = {"HoldCycleTime": 20}

# pid_ac indexes config["PB"] directly with no default; the other plain
# controllers construct fine from {}. Same fixture used by
# test_controller_construct_smoke.py and test_pid_variants_golden.py.
CONTROLLER_CONFIGS = {
    "pid_ac": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
}


def test_base_defaults_are_inert():
    core = ControllerBase({}, "F", dict(CYCLE_DATA))
    assert core.set_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0)) is None
    assert core.get_status() is None
    assert core.get_model_snapshot() is None
    assert core.restore_model({"revision": 1}) is False


def test_set_output_does_not_change_a_plain_controller_s_output():
    core = ControllerBase({}, "F", dict(CYCLE_DATA))
    before = core.update(200.0)
    core.set_output(AppliedOutput(0.9, OutputSource.LID_OPEN, 1.0))
    assert core.update(200.0) == before


@pytest.mark.parametrize("name", PLAIN_CONTROLLERS)
def test_every_shipped_controller_answers_all_four(name):
    mod = importlib.import_module(f"controller.{name}")
    core = mod.Controller(dict(CONTROLLER_CONFIGS.get(name, {})), "F", dict(CYCLE_DATA))
    for method in ("set_output", "get_status", "get_model_snapshot", "restore_model"):
        assert callable(getattr(core, method)), f"{name} is missing {method}"
