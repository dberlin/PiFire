from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from threading import Barrier, Event

import pytest

from common import api_commands, datastore_accessors
from common.control_delta import ControlDeltaError, control_delta
from common.datastore_accessors import (
    execute_control_writes,
    mpc_calibration_command_state,
    queue_mpc_calibration_command,
    read_control,
    read_pending_control_writes,
)
from common.modes import Mode


def _control():
    return {"mode": Mode.HOLD, "mpc_calibration": {"revision": 2}}


def _settings():
    return {
        "globals": {"units": "F"},
        "safety": {"maxtemp": 500},
        "controller": {
            "selected": "mpc",
            "config": {"mpc": {"enable_grey_box": True, "enable_online_adaptation": True}},
        },
    }


def _command(revision=3, **overrides):
    value = {
        "action": "start",
        "revision": revision,
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

    def queue(delta, _command, origin):
        writes.append(((delta,), {"origin": origin}))
        return True

    monkeypatch.setattr(api_commands, "queue_mpc_calibration_command", queue)
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
        _command(ambient_c="20"),
        _command(ambient_source="unknown"),
        _command(empty_grill_confirmed=False),
        _command(pellets_confirmed=False),
    ],
)
def test_invalid_or_non_monotonic_start_leaves_control_unchanged(monkeypatch, command):
    result, writes = _invoke(monkeypatch, _control(), _settings(), command)

    assert result["result"] == "ERROR"
    assert writes == []


def test_pending_revision_conflict_is_rejected_before_queueing(ds):
    start = _command(revision=4)
    stop = _command(revision=4, action="stop")

    assert queue_mpc_calibration_command(
        control_delta(ops=[{"op": "mpc_calibration.set", "command": start}]),
        start,
        "test",
    )
    with pytest.raises(ControlDeltaError, match="revision must exceed 4"):
        queue_mpc_calibration_command(
            control_delta(ops=[{"op": "mpc_calibration.set", "command": stop}]),
            stop,
            "test",
        )

    pending = read_pending_control_writes()
    assert len(pending) == 1
    assert mpc_calibration_command_state({}, pending) == start


def test_exact_pending_command_retry_is_idempotent(ds):
    command = _command(revision=4)
    delta = control_delta(ops=[{"op": "mpc_calibration.set", "command": command}])

    assert queue_mpc_calibration_command(delta, command, "test")
    assert queue_mpc_calibration_command(delta, command, "test") is False
    assert len(read_pending_control_writes()) == 1


def test_revision_above_pending_high_water_queues_safety_command(ds):
    start = _command(revision=4)
    stop = _command(revision=5, action="stop")

    assert queue_mpc_calibration_command(
        control_delta(ops=[{"op": "mpc_calibration.set", "command": start}]),
        start,
        "test",
    )
    assert queue_mpc_calibration_command(
        control_delta(ops=[{"op": "mpc_calibration.set", "command": stop}]),
        stop,
        "test",
    )

    assert mpc_calibration_command_state({}, read_pending_control_writes()) == stop


def test_concurrent_equal_revisions_admit_exactly_one_fifo_command(ds):
    barrier = Barrier(2)

    def admit(action):
        command = _command(revision=4, action=action)
        delta = control_delta(ops=[{"op": "mpc_calibration.set", "command": command}])
        barrier.wait()
        try:
            queue_mpc_calibration_command(delta, command, f"test-{action}")
        except ControlDeltaError:
            return "rejected"
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(admit, ("start", "stop")))

    pending = read_pending_control_writes()
    assert sorted(outcomes) == ["accepted", "rejected"]
    assert len(pending) == 1
    assert mpc_calibration_command_state({}, pending) == pending[0]["ops"][0]["command"]


def test_fifo_drain_serializes_live_revision_and_high_water_reads(ds, monkeypatch):
    start = _command(revision=4)
    stop = _command(revision=4, action="stop")
    assert queue_mpc_calibration_command(
        control_delta(ops=[{"op": "mpc_calibration.set", "command": start}]),
        start,
        "test-start",
    )
    entered = Event()
    release = Event()
    apply_control_delta = datastore_accessors.apply_control_delta

    def pause_inside_drain(control, delta):
        entered.set()
        assert release.wait(2)
        return apply_control_delta(control, delta)

    monkeypatch.setattr(datastore_accessors, "apply_control_delta", pause_inside_drain)
    with ThreadPoolExecutor(max_workers=3) as executor:
        drain = executor.submit(execute_control_writes)
        assert entered.wait(2)
        high_water = executor.submit(mpc_calibration_command_state)
        admission = executor.submit(
            queue_mpc_calibration_command,
            control_delta(ops=[{"op": "mpc_calibration.set", "command": stop}]),
            stop,
            "test-stop",
        )
        with pytest.raises(TimeoutError):
            high_water.result(timeout=0.2)
        with pytest.raises(TimeoutError):
            admission.result(timeout=0.2)
        release.set()
        assert drain.result(timeout=2) == "OK"
        assert high_water.result(timeout=2) == start
        with pytest.raises(ControlDeltaError, match="revision must exceed 4"):
            admission.result(timeout=2)

    assert read_control()["mpc_calibration"] == start
    assert read_pending_control_writes() == ()


@pytest.mark.parametrize("action", ("pause", "resume", "stop", "reset-progress"))
def test_non_start_actions_remain_issuable_after_the_safety_ceiling_is_lowered(monkeypatch, action):
    settings = _settings()
    settings["globals"]["units"] = "C"
    settings["safety"]["maxtemp"] = 100
    result, writes = _invoke(
        monkeypatch,
        _control(),
        settings,
        _command(action=action),
    )

    assert result["result"] == "OK"
    assert len(writes) == 1


@pytest.mark.parametrize(
    "control,settings",
    [
        ({"mode": Mode.SMOKE}, _settings()),
        (_control(), {"globals": {"units": "F"}, "controller": {"selected": "pid", "config": {}}}),
        (
            _control(),
            {
                "globals": {"units": "F"},
                "controller": {"selected": "mpc", "config": {"mpc": {"enable_grey_box": False}}},
            },
        ),
    ],
)
def test_start_requires_mpc_hold_with_grey_box(monkeypatch, control, settings):
    result, writes = _invoke(monkeypatch, control, settings, _command())

    assert result["result"] == "ERROR"
    assert writes == []
