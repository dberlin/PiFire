"""Every controller answers the four capability methods.

A controller that does not model the plant must be completely unaffected by
applied-output feedback, diagnostics, and model persistence existing.
"""

import importlib

import pytest

from common.control_trace import ActuationMode
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerBase, ControllerLearningDiagnostics

# Controllers with no optional dependency, so this runs on every install.
PLAIN_CONTROLLERS = ["pid", "pid_sp"]

CYCLE_DATA = {}


def test_non_learning_controller_returns_no_learning_diagnostics():
    core = ControllerBase({}, "F", {})
    assert core.get_learning_diagnostics() is None


def test_learning_diagnostics_owns_nested_state():
    source = {"status": "collecting", "gates": [{"passed": False}]}
    snapshot = ControllerLearningDiagnostics(schema_version=1, state=source)

    source["gates"][0]["passed"] = True
    first = snapshot.as_json()
    first["gates"][0]["passed"] = True

    assert snapshot.as_json()["gates"][0]["passed"] is False


@pytest.mark.parametrize("schema_version", [False, True, 0, -1, 1.0, "1", None])
def test_learning_diagnostics_rejects_invalid_schema_versions(schema_version):
    with pytest.raises(ValueError, match="schema_version must be positive"):
        ControllerLearningDiagnostics(schema_version=schema_version, state={})


@pytest.mark.parametrize(
    ("state", "error", "message"),
    [
        ({"sample": float("nan")}, ValueError, "finite"),
        ({"sample": float("inf")}, ValueError, "finite"),
        ({1: "not-json"}, TypeError, "keys must be strings"),
        ({"sample": object()}, TypeError, "unsupported learning diagnostics value"),
    ],
)
def test_learning_diagnostics_rejects_non_json_state(state, error, message):
    with pytest.raises(error, match=message):
        ControllerLearningDiagnostics(schema_version=1, state=state)


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
