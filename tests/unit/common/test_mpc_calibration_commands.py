from copy import deepcopy

import pytest

from common import api_commands
from common.modes import Mode


def _control():
    return {"mode": Mode.HOLD, "mpc_calibration": {"revision": 2}}


def _settings():
    return {
        "globals": {"units": "F"},
        "controller": {"selected": "mpc", "config": {"mpc": {"enable_grey_box": True}}},
    }


def _command(revision=3, **overrides):
    value = {
        "action": "start",
        "revision": revision,
        "maximum_temperature_c": 130.0,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
    value.update(overrides)
    return value


def _invoke(monkeypatch, control, settings, command):
    writes = []
    monkeypatch.setattr(api_commands, "read_control", lambda: deepcopy(control))
    monkeypatch.setattr(api_commands, "read_settings", lambda: deepcopy(settings))
    monkeypatch.setattr(api_commands, "write_control", lambda *args, **kwargs: writes.append((args, kwargs)))
    result = api_commands.process_command("set", ["mpc_calibration", command])
    return result, writes


def test_start_persists_exact_validated_calibration_request(monkeypatch):
    command = _command()
    result, writes = _invoke(monkeypatch, _control(), _settings(), command)

    assert result["result"] == "OK"
    assert result["data"]["mpc_calibration"] == command
    assert writes[0][0][0]["set"]["mpc_calibration"] == command


@pytest.mark.parametrize(
    "command",
    [
        _command(maximum_temperature_c="130"),
        _command(ambient_source="unknown"),
        _command(empty_grill_confirmed=False),
        _command(pellets_confirmed=False),
        _command(revision=2),
    ],
)
def test_invalid_or_non_monotonic_start_leaves_control_unchanged(monkeypatch, command):
    result, writes = _invoke(monkeypatch, _control(), _settings(), command)

    assert result["result"] == "ERROR"
    assert writes == []


def test_duplicate_revision_is_idempotent(monkeypatch):
    command = _command()
    control = {"mode": Mode.HOLD, "mpc_calibration": command}
    result, writes = _invoke(monkeypatch, control, _settings(), command)

    assert result["result"] == "OK"
    assert result["data"]["idempotent"] is True
    assert writes == []


@pytest.mark.parametrize(
    "control,settings",
    [
        ({"mode": Mode.SMOKE}, _settings()),
        (_control(), {"globals": {"units": "F"}, "controller": {"selected": "pid", "config": {}}}),
        (_control(), {"globals": {"units": "F"}, "controller": {"selected": "mpc", "config": {"mpc": {"enable_grey_box": False}}}}),
    ],
)
def test_start_requires_mpc_hold_with_grey_box(monkeypatch, control, settings):
    result, writes = _invoke(monkeypatch, control, settings, _command())

    assert result["result"] == "ERROR"
    assert writes == []
