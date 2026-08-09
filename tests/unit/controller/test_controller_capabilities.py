"""Every controller answers the four capability methods.

A controller that does not model the plant must be completely unaffected by
applied-output feedback, diagnostics, and model persistence existing.
"""

import importlib

import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerBase
from common.control_trace import ActuationMode

# Controllers with no optional dependency, so this runs on every install.
PLAIN_CONTROLLERS = ["pid", "pid_sp"]

CYCLE_DATA = {}


def test_base_defaults_are_inert():
    core = ControllerBase({}, "F", dict(CYCLE_DATA))
    assert core.actuation_mode() is ActuationMode.FRAMED_PULSE
    assert core.set_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0)) is None
    assert core.get_status() is None
    assert core.trace_diagnostics() is None
    assert core.get_model_snapshot() is None
    assert core.restore_model({"revision": 1}) is False


def test_set_output_does_not_change_a_plain_controller_s_output():
    core = ControllerBase({}, "F", dict(CYCLE_DATA))
    before = core.update(200.0)
    core.set_output(AppliedOutput(0.9, OutputSource.LID_OPEN, 1.0))
    assert core.update(200.0) == before


@pytest.mark.parametrize("name", PLAIN_CONTROLLERS)
def test_pid_controllers_inherit_framed_pulse_capability(name):
    mod = importlib.import_module(f"controller.{name}")
    core = mod.Controller({}, "F", dict(CYCLE_DATA))

    assert type(core).actuation_mode is ControllerBase.actuation_mode
    assert core.actuation_mode() is ActuationMode.FRAMED_PULSE
    for method in (
        "set_output",
        "get_status",
        "trace_diagnostics",
        "get_model_snapshot",
        "restore_model",
    ):
        assert callable(getattr(core, method)), f"{name} is missing {method}"
