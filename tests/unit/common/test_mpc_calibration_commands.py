from copy import deepcopy

import pytest

from common import api_commands
from common.modes import Mode


def _control():
    return {"mode": Mode.HOLD, "mpc_calibration": {"revision": 2}}


def _settings():
    return {
        "globals": {"units": "F"},
        "safety": {"maxtemp": 500},
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
    assert writes[0][0][0]["ops"] == [{"op": "mpc_calibration.set", "command": command}]


@pytest.mark.parametrize(
    "command",
    [
        _command(maximum_temperature_c="130"),
        _command(ambient_source="unknown"),
        _command(empty_grill_confirmed=False),
        _command(pellets_confirmed=False),
    ],
)
def test_invalid_or_non_monotonic_start_leaves_control_unchanged(monkeypatch, command):
    result, writes = _invoke(monkeypatch, _control(), _settings(), command)

    assert result["result"] == "ERROR"
    assert writes == []


def test_two_requests_are_queued_without_stale_revision_arbitration(monkeypatch):
    writes = []
    control = _control()
    settings = _settings()
    monkeypatch.setattr(api_commands, "read_control", lambda: deepcopy(control))
    monkeypatch.setattr(api_commands, "read_settings", lambda: deepcopy(settings))
    monkeypatch.setattr(api_commands, "write_control", lambda *args, **kwargs: writes.append((args, kwargs)))

    four = _command(revision=4)
    three = _command(revision=3)
    assert api_commands.process_command("set", ["mpc_calibration", four])["result"] == "OK"
    assert api_commands.process_command("set", ["mpc_calibration", three])["result"] == "OK"

    assert [write[0][0]["ops"][0]["command"]["revision"] for write in writes] == [4, 3]


@pytest.mark.parametrize(
    "units,maximum_temperature_c",
    [("F", 260.0), ("C", 260.0)],
)
def test_maximum_at_configured_safety_ceiling_is_rejected_before_queueing(
    monkeypatch, units, maximum_temperature_c
):
    settings = _settings()
    settings["globals"]["units"] = units
    settings["safety"]["maxtemp"] = 500 if units == "F" else 260
    result, writes = _invoke(monkeypatch, _control(), settings, _command(maximum_temperature_c=maximum_temperature_c))

    assert result["result"] == "ERROR"
    assert writes == []


@pytest.mark.parametrize("action", ("pause", "resume", "stop", "reset-progress"))
def test_non_start_actions_remain_issuable_after_the_safety_ceiling_is_lowered(monkeypatch, action):
    settings = _settings()
    settings["globals"]["units"] = "C"
    settings["safety"]["maxtemp"] = 100
    result, writes = _invoke(
        monkeypatch,
        _control(),
        settings,
        _command(action=action, maximum_temperature_c=130.0),
    )

    assert result["result"] == "OK"
    assert len(writes) == 1


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
