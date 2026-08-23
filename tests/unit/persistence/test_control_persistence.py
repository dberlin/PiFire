import json
import sqlite3

import pytest

from common import datastore
from common.control_delta import CONTROL_DELTA_KEY, ControlDeltaError, control_delta
from common.defaults import default_control
from common.persistence import control as control_store
from common.sqlite_queue import SqliteQueue


def _calibration_command(revision: int, action: str = "start") -> dict[str, object]:
    return {
        "action": action,
        "revision": revision,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }


def _calibration_delta(command: dict[str, object]) -> dict[str, object]:
    return control_delta(ops=[{"op": "mpc_calibration.set", "command": command}])


def test_snapshot_is_copied_and_does_not_consume_pending_deltas(ds):
    delta = control_delta(set_values={"primary_setpoint": 225})
    control_store.enqueue_control_delta(delta, origin="web")
    snapshot = {"mode": "Hold", "manual": {"pwm": 50}}

    control_store.write_control_snapshot(snapshot, origin="control")
    snapshot["mode"] = "Stop"
    snapshot["manual"]["pwm"] = 99

    assert control_store.read_control() == {"mode": "Hold", "manual": {"pwm": 50}}
    assert control_store.read_pending_control_writes() == (
        {CONTROL_DELTA_KEY: 1, "set": {"primary_setpoint": 225}, "origin": "web"},
    )


def test_cook_session_identity_round_trips_in_process_readable_control(ds):
    control = default_control()
    assert control["cook_id"] is None
    control["cook_id"] = "cook-session-7"

    control_store.write_control_snapshot(control, origin="control")

    assert control_store.read_control()["cook_id"] == "cook-session-7"
    assert json.loads(datastore.get_blob("control:general"))["cook_id"] == "cook-session-7"


@pytest.mark.parametrize(
    "delta",
    [
        lambda: control_delta(set_values={"cook_id": "client-selected"}),
        lambda: control_delta(delete_paths=[["cook_id"]]),
    ],
)
def test_cook_session_identity_is_not_client_writable(delta):
    with pytest.raises(ControlDeltaError, match="cook_id"):
        delta()


def test_flush_control_can_atomically_preserve_active_cook_identity(ds):
    control_store.write_control_snapshot(
        dict(default_control(), cook_id="cook-session-7"),
        origin="control",
    )

    reset = control_store.flush_control(cook_id="cook-session-7")

    assert reset["cook_id"] == "cook-session-7"
    assert control_store.read_control()["cook_id"] == "cook-session-7"


def test_ensure_cook_id_atomically_reuses_prefers_or_generates(monkeypatch, ds):
    monkeypatch.setattr(control_store, "generate_uuid", lambda: "generated-session", raising=False)
    control_store.write_control_snapshot(
        dict(default_control(), cook_id="existing-session"),
        origin="control",
    )
    assert control_store.ensure_cook_id(preferred="ignored-prime") == "existing-session"

    control_store.flush_control()
    assert control_store.ensure_cook_id(preferred="retained-prime") == "retained-prime"
    assert control_store.read_control()["cook_id"] == "retained-prime"

    control_store.flush_control()
    assert control_store.ensure_cook_id() == "generated-session"
    assert control_store.read_control()["cook_id"] == "generated-session"


def test_enqueue_validates_copies_and_preserves_fifo_origin(ds):
    first = control_delta(set_values={"manual": {"pwm": 25}})
    second = control_delta(set_values={"manual": {"pwm": 75}})

    control_store.enqueue_control_delta(first, origin="display-a")
    control_store.enqueue_control_delta(second, origin="display-b")
    first["set"]["manual"]["pwm"] = 100

    assert control_store.read_pending_control_writes() == (
        {CONTROL_DELTA_KEY: 1, "set": {"manual": {"pwm": 25}}, "origin": "display-a"},
        {CONTROL_DELTA_KEY: 1, "set": {"manual": {"pwm": 75}}, "origin": "display-b"},
    )


def test_enqueue_rejects_invalid_delta_without_queueing(ds):
    with pytest.raises(ControlDeltaError, match="set must be a mapping, got list"):
        control_store.enqueue_control_delta(
            {CONTROL_DELTA_KEY: 1, "set": []},
            origin="malformed-writer",
        )

    assert control_store.read_pending_control_writes() == ()


def test_execute_applies_shared_delta_transform_in_fifo_order(ds):
    control_store.write_control_snapshot(
        {"mode": "Stop", "primary_setpoint": 100, "manual": {"pwm": 0}},
        origin="seed",
    )
    control_store.enqueue_control_delta(
        control_delta(set_values={"primary_setpoint": 225, "manual": {"pwm": 25}}),
        origin="first",
    )
    control_store.enqueue_control_delta(
        control_delta(set_values={"primary_setpoint": 275, "manual": {"pwm": 75}}),
        origin="second",
    )

    assert control_store.execute_control_writes() == "OK"

    assert control_store.read_control() == {
        "mode": "Stop",
        "primary_setpoint": 275,
        "manual": {"pwm": 75},
    }
    assert control_store.read_pending_control_writes() == ()


@pytest.mark.parametrize(
    ("raw_value", "expected_error"),
    [
        (json.dumps({"mode": "Startup", "origin": "legacy-web"}), "unversioned legacy control write"),
        (
            json.dumps({CONTROL_DELTA_KEY: 1, "set": [], "origin": "malformed-writer"}),
            "set must be a mapping, got list",
        ),
    ],
)
def test_malformed_persisted_rows_are_logged_dequeued_and_do_not_mutate_live_control(
    ds, caplog, raw_value, expected_error
):
    opening = {"mode": "Stop", "primary_setpoint": 100}
    control_store.write_control_snapshot(opening, origin="seed")
    ds.connection().execute(
        "INSERT INTO queue_control_write(value) VALUES(?)",
        (raw_value,),
    )

    with caplog.at_level("ERROR", logger="control"):
        assert control_store.execute_control_writes() == "OK"

    assert control_store.read_control() == opening
    assert control_store.read_pending_control_writes() == ()
    assert any(
        "rejected queued control write" in record.getMessage() and expected_error in record.getMessage()
        for record in caplog.records
    )


def test_control_queue_rejects_non_json_rows_at_storage_boundary(ds):
    with pytest.raises(sqlite3.IntegrityError):
        ds.connection().execute(
            "INSERT INTO queue_control_write(value) VALUES(?)",
            ("{not-json",),
        )

    assert control_store.read_pending_control_writes() == ()


def test_live_update_and_dequeue_roll_back_together_then_recover(ds):
    opening = {"mode": "Stop", "primary_setpoint": 100}
    control_store.write_control_snapshot(opening, origin="seed")
    control_store.enqueue_control_delta(
        control_delta(set_values={"primary_setpoint": 225}),
        origin="web",
    )
    pending = control_store.read_pending_control_writes()
    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_control_dequeue
        BEFORE DELETE ON queue_control_write
        BEGIN
            SELECT RAISE(ABORT, 'simulated control dequeue failure');
        END
        """
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated control dequeue failure"):
            control_store.execute_control_writes()

        assert control_store.read_control() == opening
        assert control_store.read_pending_control_writes() == pending
    finally:
        ds.connection().execute("DROP TRIGGER fail_control_dequeue")

    assert control_store.execute_control_writes() == "OK"
    assert control_store.read_control() == {"mode": "Stop", "primary_setpoint": 225}
    assert control_store.read_pending_control_writes() == ()


def test_rejected_row_does_not_block_later_valid_fifo_work(ds, caplog):
    control_store.write_control_snapshot({"mode": "Stop"}, origin="seed")
    ds.connection().execute(
        "INSERT INTO queue_control_write(value) VALUES(?)",
        (json.dumps({"mode": "legacy", "origin": "old-client"}),),
    )
    control_store.enqueue_control_delta(
        control_delta(set_values={"mode": "Hold"}),
        origin="new-client",
    )

    with caplog.at_level("ERROR", logger="control"):
        assert control_store.execute_control_writes() == "OK"

    assert control_store.read_control() == {"mode": "Hold"}
    assert control_store.read_pending_control_writes() == ()


def test_flush_clears_control_owned_blobs_and_queues_and_reseeds_default(ds):
    control_store.write_control_snapshot({"mode": "Hold"}, origin="seed")
    datastore.set_blob("control:command", json.dumps({"command": "legacy"}))
    datastore.set_blob("unrelated", json.dumps({"kept": True}))
    control_store.enqueue_control_delta(control_delta(set_values={"mode": "Stop"}), origin="web")
    SqliteQueue("queue_systemq").push({"action": "reboot"})
    SqliteQueue("queue_systemo").push({"result": "queued"})

    assert control_store.flush_control() == default_control()

    assert control_store.read_control() == default_control()
    assert control_store.read_pending_control_writes() == ()
    assert SqliteQueue("queue_systemq").list() == []
    assert SqliteQueue("queue_systemo").list() == []
    assert datastore.get_blob("control:command") is None
    assert json.loads(datastore.get_blob("unrelated")) == {"kept": True}


def test_calibration_state_uses_first_command_at_highest_valid_revision():
    live = {"mpc_calibration": _calibration_command(2)}
    revision_four = _calibration_command(4, action="pause")
    conflicting_four = _calibration_command(4, action="stop")
    invalid_boolean_revision = {
        CONTROL_DELTA_KEY: 1,
        "ops": [{"op": "mpc_calibration.set", "command": _calibration_command(True, action="reset-progress")}],
    }
    pending = (
        {"ops": ["legacy", {"op": "other"}]},
        None,
        {
            CONTROL_DELTA_KEY: 1,
            "ops": [{"op": "mpc_calibration.set", "command": "legacy"}],
        },
        _calibration_delta(revision_four),
        _calibration_delta(conflicting_four),
        invalid_boolean_revision,
    )

    assert control_store.mpc_calibration_command_state(live, pending) == revision_four
    assert control_store.mpc_calibration_command_revision(live, pending) == 4
    assert control_store.mpc_calibration_command_revision({}, ()) == 0


def test_calibration_state_loads_each_unspecified_persistence_source(ds):
    live = _calibration_command(2)
    queued = _calibration_command(4, action="pause")
    control_store.write_control_snapshot({"mpc_calibration": live}, origin="seed")
    control_store.enqueue_control_delta(_calibration_delta(queued), origin="runtime")

    assert control_store.mpc_calibration_command_state(None, ()) == live
    assert control_store.mpc_calibration_command_state({}, None) == queued


def test_queue_calibration_command_preserves_origin_revision_and_idempotency(ds):
    command = _calibration_command(4)
    delta = _calibration_delta(command)

    assert control_store.queue_mpc_calibration_command(delta, command, "api") is True
    assert control_store.queue_mpc_calibration_command(delta, command, "api") is False

    assert control_store.read_pending_control_writes() == (
        {CONTROL_DELTA_KEY: 1, "ops": [{"op": "mpc_calibration.set", "command": command}], "origin": "api"},
    )
    assert control_store.mpc_calibration_command_state() == command
    assert control_store.mpc_calibration_command_revision() == 4


def test_queue_calibration_command_rejects_equal_conflict_and_lower_revision(ds):
    accepted = _calibration_command(4)
    assert control_store.queue_mpc_calibration_command(_calibration_delta(accepted), accepted, "first")

    for rejected in (
        _calibration_command(4, action="stop"),
        _calibration_command(3, action="pause"),
    ):
        with pytest.raises(ControlDeltaError, match="revision must exceed 4"):
            control_store.queue_mpc_calibration_command(_calibration_delta(rejected), rejected, "conflict")

    assert len(control_store.read_pending_control_writes()) == 1


def test_queue_calibration_command_rolls_back_failed_insert_then_recovers(ds):
    command = _calibration_command(4)
    delta = _calibration_delta(command)
    ds.connection().execute(
        """
        CREATE TEMP TRIGGER fail_calibration_enqueue
        BEFORE INSERT ON queue_control_write
        BEGIN
            SELECT RAISE(ABORT, 'simulated calibration enqueue failure');
        END
        """
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated calibration enqueue failure"):
            control_store.queue_mpc_calibration_command(delta, command, "api")
        assert control_store.read_pending_control_writes() == ()
    finally:
        ds.connection().execute("DROP TRIGGER fail_calibration_enqueue")

    assert control_store.queue_mpc_calibration_command(delta, command, "api") is True
    assert control_store.mpc_calibration_command_revision() == 4
