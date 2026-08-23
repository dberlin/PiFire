"""SQLite persistence for history, metrics, and tuner state."""

import json
import time

from common import datastore
from common.common import generate_uuid
from common.defaults import METRIC_COLUMNS, default_metrics
from common.persistence.transforms import history_row_to_dict
from common.sqlite_queue import SqliteQueue


_HISTORY_SELECT = (
    "SELECT ts,psp,primary_temps,food_temps,aux_temps,notify_targets,ext_data "
    "FROM history ORDER BY id"
)
_AUTOTUNE_QUEUE = "queue_autotune"

CLEAR_HISTORY_COMMAND = "clear_history"


def _metrics_row_to_dict(row):
    metrics = dict(zip(METRIC_COLUMNS, row))
    metrics["smokeplus"] = bool(metrics["smokeplus"])
    return metrics


def read_all_metrics():
    """Return every metrics row in insertion order."""
    columns = ", ".join(METRIC_COLUMNS)
    rows = datastore.connection().execute(f"SELECT {columns} FROM metrics ORDER BY seq").fetchall()
    return [_metrics_row_to_dict(row) for row in rows]


def read_metrics():
    """Return the most recently inserted metrics row or a fresh default."""
    columns = ", ".join(METRIC_COLUMNS)
    row = datastore.connection().execute(
        f"SELECT {columns} FROM metrics ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    return _metrics_row_to_dict(row) if row else default_metrics()


def flush_metrics():
    """Delete every metrics row."""
    datastore.execute_write("DELETE FROM metrics")


def append_metric(metrics=None):
    """Insert a new metrics row after stamping its start time and identity."""
    if metrics is None:
        metrics = default_metrics()
    metrics["starttime"] = time.time() * 1000
    metrics["id"] = generate_uuid()
    columns = ", ".join(METRIC_COLUMNS)
    placeholders = ", ".join(["?"] * len(METRIC_COLUMNS))
    values = [metrics.get(key) for key in METRIC_COLUMNS]
    datastore.execute_write(
        f"INSERT INTO metrics({columns}) VALUES({placeholders})",
        values,
    )


def update_metrics(metrics):
    """Partially update the current metrics row, inserting if none exists."""
    columns = ", ".join(METRIC_COLUMNS)
    placeholders = ", ".join(["?"] * len(METRIC_COLUMNS))

    with datastore.transaction() as connection:
        row = connection.execute("SELECT seq FROM metrics ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            values = [metrics.get(key) for key in METRIC_COLUMNS]
            connection.execute(
                f"INSERT INTO metrics({columns}) VALUES({placeholders})",
                values,
            )
            return

        present_keys = [key for key in METRIC_COLUMNS if key in metrics]
        if present_keys:
            assignments = ", ".join([f"{key}=?" for key in present_keys])
            values = [metrics[key] for key in present_keys]
            connection.execute(
                f"UPDATE metrics SET {assignments} WHERE seq=?",
                values + [row[0]],
            )


def flush_history():
    """Clear history together with its coupled current and metrics state."""
    from common.persistence import control, runtime

    datastore.execute_write("DELETE FROM history")
    runtime.flush_current()
    flush_metrics()

    control_state = control.read_control()
    control_state["cook_id"] = None
    control.write_control_snapshot(control_state, origin="history")


def request_history_clear() -> str:
    """Queue active-session finalization or clear immediately while inactive."""
    from common.modes import Mode, StatusState
    from common.persistence import control

    control_state = control.read_control()
    inactive = (
        control_state.get("mode") in (Mode.STOP, Mode.ERROR)
        and control_state.get("status") not in (StatusState.ACTIVE, StatusState.MONITOR)
    )
    if inactive:
        flush_history()
        return "cleared"
    SqliteQueue("queue_systemq").push([CLEAR_HISTORY_COMMAND])
    return "queued"



def read_history(num_items=0):
    """Return history rows in write order, optionally limited from the end."""
    rows = datastore.connection().execute(_HISTORY_SELECT).fetchall()
    if num_items > 0:
        rows = rows[-num_items:]
    return [history_row_to_dict(row) for row in rows]


def write_history(in_data, maxsizelines=28800, ext_data=False):
    """Append one history sample and trim the oldest rows past retention."""
    timestamp = int(time.time() * 1000)
    extended_data = json.dumps(in_data["ext_data"]) if ext_data else None

    with datastore.transaction() as connection:
        connection.execute(
            "INSERT INTO history(ts,psp,primary_temps,food_temps,aux_temps,"
            "notify_targets,ext_data) VALUES(?,?,?,?,?,?,?)",
            (
                timestamp,
                in_data["primary_setpoint"],
                json.dumps(in_data["probe_history"]["primary"]),
                json.dumps(in_data["probe_history"]["food"]),
                json.dumps(in_data["probe_history"]["aux"]),
                json.dumps(in_data["notify_targets"]),
                extended_data,
            ),
        )
        count = connection.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        if count > maxsizelines:
            connection.execute(
                "DELETE FROM history WHERE id IN "
                "(SELECT id FROM history ORDER BY id LIMIT ?)",
                (count - maxsizelines,),
            )


def write_tr(tr_data):
    """Persist the latest tuner readings."""
    datastore.set_blob("control:tuning", json.dumps(tr_data))


def read_tr():
    """Return the latest tuner readings or a fresh empty mapping."""
    raw = datastore.get_blob("control:tuning")
    return json.loads(raw) if raw is not None else {}


def write_autotune(data):
    """Append one autotune sample."""
    SqliteQueue(_AUTOTUNE_QUEUE).push(data)


def read_autotune():
    """Return every autotune sample in insertion order."""
    return SqliteQueue(_AUTOTUNE_QUEUE).list()


def autotune_length():
    """Return the number of queued autotune samples."""
    return SqliteQueue(_AUTOTUNE_QUEUE).length()


def flush_autotune():
    """Discard every autotune sample and return the empty post-flush shape."""
    SqliteQueue(_AUTOTUNE_QUEUE).flush()
    return []
