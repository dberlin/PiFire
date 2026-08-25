"""SQLite persistence for control snapshots and versioned delta intents."""

import copy
import json
import logging
from collections.abc import Mapping

from common import datastore
from common.common import generate_uuid
from common.control_delta import ControlDeltaError, is_control_delta, validate_control_delta
from common.defaults import default_control
from common.persistence.history import CLEAR_HISTORY_COMMAND
from common.persistence.transforms import apply_control_delta
from common.sqlite_queue import SqliteQueue

__all__ = (
    "enqueue_control_delta",
    "ensure_cook_id",
    "execute_control_writes",
    "flush_control",
    "mpc_calibration_command_revision",
    "mpc_calibration_command_state",
    "queue_mpc_calibration_command",
    "read_control",
    "read_pending_control_writes",
    "write_control_snapshot",
)


def flush_control(*, cook_id: str | None = None):
    """Reset control state while retaining only durable history-clear commands."""
    for table in ("queue_control_write", "queue_systemo"):
        datastore.execute_write(f"DELETE FROM {table}")
    datastore.execute_write(
        "DELETE FROM queue_systemq WHERE json_type(value) != 'array' OR COALESCE(json_extract(value, '$[0]') != ?, 1)",
        (CLEAR_HISTORY_COMMAND,),
    )
    for key in ("control:general", "control:command"):
        datastore.delete_blob(key)
    control = default_control()
    control["cook_id"] = cook_id
    write_control_snapshot(control, origin="common")
    return control


def ensure_cook_id(*, preferred: str | None = None) -> str:
    """Atomically return the live cook identity, seeding it when absent."""
    with datastore.transaction() as connection:
        row = connection.execute("SELECT value FROM kv WHERE key='control:general'").fetchone()
        control = json.loads(row[0]) if row is not None else default_control()
        current = control.get("cook_id")
        if isinstance(current, str) and bool(current) and current == current.strip():
            return current
        cook_id = (
            preferred
            if isinstance(preferred, str) and bool(preferred) and preferred == preferred.strip()
            else generate_uuid()
        )
        control["cook_id"] = cook_id
        connection.execute(
            "INSERT INTO kv(key,value) VALUES('control:general',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(control),),
        )
        return cook_id


def read_control():
    """Return the authoritative live control snapshot."""
    raw = datastore.get_blob("control:general")
    return json.loads(raw) if raw is not None else default_control()


def write_control_snapshot(control: Mapping[str, object], *, origin: str) -> None:
    """Replace the live snapshot immediately without consuming queued deltas."""
    del origin
    datastore.set_blob("control:general", json.dumps(copy.deepcopy(dict(control))))


def enqueue_control_delta(delta: Mapping[str, object], *, origin: str) -> None:
    """Validate and enqueue one copied, origin-stamped control delta."""
    validate_control_delta(delta)
    payload = copy.deepcopy(dict(delta))
    payload["origin"] = origin
    SqliteQueue("queue_control_write").push(payload)


def read_pending_control_writes():
    """Return an immutable snapshot of queued control deltas in FIFO order."""
    return tuple(SqliteQueue("queue_control_write").list())


def _mpc_calibration_command_from_delta(delta):
    if not isinstance(delta, dict):
        return None
    for op in delta.get("ops", ()):
        if not isinstance(op, dict) or op.get("op") != "mpc_calibration.set":
            continue
        command = op.get("command")
        if isinstance(command, dict):
            return command
    return None


def mpc_calibration_command_state(control=None, pending_writes=None):
    """Return the first accepted command at the highest live-or-queued revision."""
    if control is None and pending_writes is None:
        with datastore.transaction() as connection:
            row = connection.execute("SELECT value FROM kv WHERE key = 'control:general'").fetchone()
            control = json.loads(row[0]) if row is not None else default_control()
            pending_writes = tuple(
                json.loads(queued[0])
                for queued in connection.execute("SELECT value FROM queue_control_write ORDER BY id").fetchall()
            )
    else:
        if control is None:
            control = read_control()
        if pending_writes is None:
            pending_writes = read_pending_control_writes()
    current = control.get("mpc_calibration") if isinstance(control, dict) else None
    accepted = current if isinstance(current, dict) else None
    for delta in pending_writes:
        command = _mpc_calibration_command_from_delta(delta)
        if command is None:
            continue
        revision = command.get("revision")
        accepted_value = accepted.get("revision") if accepted is not None else -1
        accepted_revision = (
            accepted_value if isinstance(accepted_value, int) and not isinstance(accepted_value, bool) else -1
        )
        if isinstance(revision, int) and not isinstance(revision, bool) and revision > accepted_revision:
            accepted = command
    return accepted


def mpc_calibration_command_revision(control=None, pending_writes=None):
    """Return the accepted calibration revision high-water mark."""
    command = mpc_calibration_command_state(control, pending_writes)
    if command is None:
        return 0
    revision = command.get("revision")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else 0


def queue_mpc_calibration_command(delta, command, origin):
    """Atomically admit one revisioned calibration delta to the control FIFO."""
    validate_control_delta(delta)
    payload = copy.deepcopy(dict(delta))
    payload["origin"] = origin
    with datastore.transaction() as connection:
        row = connection.execute("SELECT value FROM kv WHERE key = 'control:general'").fetchone()
        control = json.loads(row[0]) if row is not None else default_control()
        pending = tuple(
            json.loads(queued[0])
            for queued in connection.execute("SELECT value FROM queue_control_write ORDER BY id").fetchall()
        )
        accepted = mpc_calibration_command_state(control, pending)
        accepted_revision = accepted.get("revision") if accepted is not None else -1
        if command["revision"] < accepted_revision or (
            command["revision"] == accepted_revision and command != accepted
        ):
            raise ControlDeltaError(f"MPC calibration revision must exceed {accepted_revision}")
        if command == accepted:
            return False
        connection.execute(
            "INSERT INTO queue_control_write(value) VALUES(?)",
            (json.dumps(payload),),
        )
    return True


def execute_control_writes():
    """Drain queued version-1 deltas FIFO, rejecting malformed persisted rows."""
    log = logging.getLogger("control")
    while True:
        with datastore.transaction() as connection:
            row = connection.execute("SELECT id, value FROM queue_control_write ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return "OK"

            origin = None
            try:
                command = json.loads(row[1])
                origin = command.get("origin") if isinstance(command, Mapping) else None
                if not is_control_delta(command):
                    raise ControlDeltaError("unversioned legacy control write")
                validate_control_delta(command)

                control_row = connection.execute("SELECT value FROM kv WHERE key = 'control:general'").fetchone()
                control = json.loads(control_row[0]) if control_row is not None else default_control()
                apply_control_delta(control, command)
                if control_row is None:
                    connection.execute(
                        "INSERT INTO kv(key, value) VALUES ('control:general', ?)",
                        (json.dumps(control),),
                    )
                else:
                    connection.execute(
                        "UPDATE kv SET value=? WHERE key='control:general'",
                        (json.dumps(control),),
                    )
            except (
                ControlDeltaError,
                TypeError,
                ValueError,
                KeyError,
                IndexError,
                AttributeError,
            ) as error:
                log.error(
                    "execute_control_writes: rejected queued control write id=%s origin=%r: %s",
                    row[0],
                    origin,
                    error,
                )

            connection.execute("DELETE FROM queue_control_write WHERE id=?", (row[0],))
