import pytest

from common.modes import Mode
from common.persistence.control import (
    execute_control_writes,
    read_control,
    write_control_snapshot,
)
from common.persistence.runtime import (
    read_settings,
    write_settings,
)


def _command(**overrides):
    command = {
        "action": "start",
        "revision": 3,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
    command.update(overrides)
    return command


@pytest.fixture
def client(ds, client):  # noqa: F811 -- intentionally wraps the conftest fixture
    settings = read_settings()
    settings["globals"]["units"] = "F"
    settings["safety"]["maxtemp"] = 500
    settings["controller"] = {
        "selected": "mpc",
        "config": {"mpc": {"enable_grey_box": True, "enable_online_adaptation": True}},
    }
    write_settings(settings)
    control = read_control()
    control["mode"] = Mode.HOLD
    control.pop("mpc_calibration", None)
    write_control_snapshot(control, origin="test")
    return client


def test_json_post_dispatches_the_validated_calibration_command(client):
    command = _command()

    response = client.post("/api/set_mpc_calibration", json=command)

    assert response.status_code == 201
    assert response.get_json()["data"]["mpc_calibration"] == command
    assert "mpc_calibration" not in read_control()
    execute_control_writes()
    assert read_control()["mpc_calibration"] == command


def test_json_post_rejects_invalid_body_without_queuing_a_control_change(client):
    response = client.post("/api/set_mpc_calibration", json={"action": "start"})

    assert response.status_code == 422
    assert response.get_json()["result"] == "ERROR"
    execute_control_writes()
    assert "mpc_calibration" not in read_control()


def test_path_arguments_cannot_transport_a_calibration_object(client):
    response = client.post("/api/set/mpc_calibration", json=_command())

    assert response.status_code == 400
    assert response.get_json()["result"] == "ERROR"
    execute_control_writes()
    assert "mpc_calibration" not in read_control()


def test_drain_keeps_the_first_newest_queued_command_when_older_and_conflicting_commands_follow(client):
    first = _command(revision=4)
    assert client.post("/api/set_mpc_calibration", json=first).status_code == 201
    stale = client.post("/api/set_mpc_calibration", json=_command(revision=3))
    conflict = client.post(
        "/api/set_mpc_calibration",
        json=_command(revision=4, ambient_c=21.0),
    )
    assert stale.status_code == conflict.status_code == 400
    assert stale.get_json()["message"] == "MPC calibration revision must exceed 4"
    assert conflict.get_json()["message"] == "MPC calibration revision must exceed 4"

    assert "mpc_calibration" not in read_control()
    execute_control_writes()
    assert read_control()["mpc_calibration"] == first


def test_start_is_refused_when_nothing_will_consume_what_calibration_records(client):
    """Calibration excites the plant for the online learner. With the learner
    off every frame is dropped, so the run can neither complete nor report --
    it simply looks like nothing happened. Say so at the button instead."""
    settings = read_settings()
    settings["controller"]["config"]["mpc"]["enable_online_adaptation"] = False
    write_settings(settings)

    response = client.post("/api/set_mpc_calibration", json=_command())

    assert response.status_code == 400
    assert "Online Model Adaptation" in response.get_json()["message"]
    execute_control_writes()
    assert "mpc_calibration" not in read_control()


def test_stop_does_not_require_online_adaptation(client):
    """An operator must always be able to end a run, whatever the settings say."""
    settings = read_settings()
    settings["controller"]["config"]["mpc"]["enable_online_adaptation"] = False
    write_settings(settings)

    response = client.post("/api/set_mpc_calibration", json=_command(action="stop", revision=9))

    assert response.status_code == 201
    assert response.get_json()["result"] != "ERROR"


def test_retargeting_an_active_hold_does_not_ask_the_control_loop_to_rebuild(client):
    """`updated` breaks the mode work cycle, so Hold is re-entered and the
    controller rebuilt -- losing the estimator, the learner and any calibration
    run. A new setpoint for a cook that is already holding is not a mode change."""
    control = read_control()
    control["mode"] = Mode.HOLD
    control["updated"] = False
    write_control_snapshot(control, origin="test")

    assert client.get("/api/set/psp/275").status_code == 201
    execute_control_writes()

    control = read_control()
    assert control["primary_setpoint"] == 275
    assert control["mode"] == Mode.HOLD
    assert control["updated"] is False


def test_entering_hold_from_another_mode_still_asks_for_the_mode_change(client):
    control = read_control()
    control["mode"] = Mode.STOP
    control["updated"] = False
    write_control_snapshot(control, origin="test")

    assert client.get("/api/set/psp/225").status_code == 201
    execute_control_writes()

    control = read_control()
    assert control["mode"] == Mode.HOLD
    assert control["updated"] is True
