import pytest

from app import app as flask_app
from common.common import WriteKind
from common.datastore_accessors import (
    execute_control_writes,
    read_control,
    read_settings,
    write_control,
    write_settings,
)
from common.modes import Mode


def _command(**overrides):
    command = {
        "action": "start",
        "revision": 3,
        "maximum_temperature_c": 130.0,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
    command.update(overrides)
    return command


@pytest.fixture
def client(ds):
    settings = read_settings()
    settings["globals"]["units"] = "F"
    settings["safety"]["maxtemp"] = 500
    settings["controller"] = {"selected": "mpc", "config": {"mpc": {"enable_grey_box": True}}}
    write_settings(settings)
    control = read_control()
    control["mode"] = Mode.HOLD
    control.pop("mpc_calibration", None)
    write_control(control, WriteKind.OVERWRITE, origin="test")
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client


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

    assert response.status_code == 400
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
        json=_command(revision=4, maximum_temperature_c=140.0),
    )
    assert stale.status_code == conflict.status_code == 400
    assert stale.get_json()["message"] == "MPC calibration revision must exceed 4"
    assert conflict.get_json()["message"] == "MPC calibration revision must exceed 4"

    assert "mpc_calibration" not in read_control()
    execute_control_writes()
    assert read_control()["mpc_calibration"] == first
