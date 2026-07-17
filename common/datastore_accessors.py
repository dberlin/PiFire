"""
==============================================================================
 PiFire Datastore Accessors
==============================================================================

Description: Read/write accessors for the SQLite-backed datastore -- the
  control/settings/pellets/status/current blobs, the metrics and history
  tables, and the queue/membership-list backed structures.

  Extracted from common/common.py; common/common.py re-imports these names
  for now so that existing `common.common.X` call sites keep resolving.

==============================================================================
"""

import json
import logging
import math
import time

from common import datastore
from common.common import WriteKind, generate_uuid, strip_null_members
from common.defaults import (
    METRIC_COLUMNS,
    default_control,
    default_metrics,
    default_pellets,
    default_settings,
)
from common.sqlite_queue import SqliteMembershipList, SqliteQueue


def _flush_control():
    """
    Clear the control queues and control blob keys (NOT history/current), then
    reseed default_control().

    :return: The reseeded default control dictionary.
    """
    for table in ("queue_control_write", "queue_systemq", "queue_systemo"):
        datastore.execute_write(f"DELETE FROM {table}")
    for key in ("control:general", "control:command"):
        datastore.delete_blob(key)
    control = default_control()
    write_control(control, WriteKind.OVERWRITE, origin="common")
    return control


def read_control(flush=False):
    """
    Read Control from SQLite DB

    :param flush: True to clean control. False otherwise
    :return: control
    """
    if flush:
        return _flush_control()
    return _read_json_blob("control:general", default_control)


def write_control(control, kind, origin="unknown"):
    """
    Write control to SQLite DB.

    :param control: Control Dictionary
    :param kind: WriteKind.OVERWRITE writes control:general directly.
                             WriteKind.MERGE queues a partial change for deep-merge on execute.
    :param origin: Source label recorded on merge writes.
    """
    if kind is WriteKind.OVERWRITE:
        _write_json_blob("control:general", control)
    elif kind is WriteKind.MERGE:
        control["origin"] = origin
        SqliteQueue("queue_control_write").push(control)
    else:
        raise TypeError(f"write_control: kind must be WriteKind, got {kind!r}")


def execute_control_writes():
    """
    Execute Control Writes in Queue from SQLite DB.

    Each queued MERGE partial is deep-merged into control:general via SQLite's
    json_patch(). Null-valued keys in a partial are stripped first (see
    strip_null_members) so the merge only ever adds or overwrites keys -- never
    deletes -- preserving the historical deep_update contract.

    :param None

    :return status : 'OK', 'ERROR'
    """
    q = SqliteQueue("queue_control_write")
    # Seed the base row if absent so the first merge on a fresh/flushed DB isn't
    # silently dropped by the UPDATE below (mirrors read_control()'s default
    # fallback, which the old read-modify-write path relied on).
    if q.length() > 0 and datastore.get_blob("control:general") is None:
        datastore.set_blob("control:general", json.dumps(default_control()))
    while q.length() > 0:
        command = q.pop()
        if command is None:
            break
        origin = command.pop("origin", None)
        stripped = []
        patch = strip_null_members(command, stripped)
        if stripped:
            # Temporary diagnostic: after the base.py None->False cleanup, no
            # PiFire-internal MERGE should carry nulls. A hit here means a source
            # is still sending them (or a client did via /api/control) -- fix that
            # source, then this strip + log can be removed. Logged at ERROR so it
            # surfaces even when control.log is at its production ERROR level.
            logging.getLogger("control").error(
                "execute_control_writes: stripped null member(s) %s from MERGE partial (origin=%r); "
                "json_patch would delete these keys. Fix the source to stop sending nulls.",
                stripped,
                origin,
            )
        datastore.execute_write(
            "UPDATE kv SET value = json_patch(value, ?) WHERE key = 'control:general'", (json.dumps(patch),)
        )
    return "OK"


def read_errors(flush=False):
    """
    Read Errors from SQLite DB

    :param flush: True to clear errors. False otherwise
    :return: errors
    """
    if flush:
        write_errors([])
        return []
    return _read_json_blob("errors", list)


def write_errors(errors):
    """
    Write Errors to SQLite DB

    :param errors: Errors
    """
    _write_json_blob("errors", errors)


def read_warnings():
    """
    Read Warnings from SQLite DB and then burn them

    :return: warnings
    """
    q = SqliteQueue("list_warnings", raw=True)
    warnings = q.list()
    q.flush()
    return warnings


def write_warning(warning):
    """
    Write a warning to SQLite DB

    :param warning: Warning string
    """
    SqliteQueue("list_warnings", raw=True).push(warning)


def _metrics_row_to_dict(row):
    metrics = dict(zip(METRIC_COLUMNS, row))
    metrics["smokeplus"] = bool(metrics["smokeplus"])
    return metrics


def read_metrics(all=False):
    """
    Read Metrics from SQLite DB

    :param all: True to read entire list. False for top of list.
    """
    conn = datastore.connection()
    cols_sql = ", ".join(METRIC_COLUMNS)
    if all:
        # Read entire list of Metrics, in insertion order
        rows = conn.execute(f"SELECT {cols_sql} FROM metrics ORDER BY seq").fetchall()
        return [_metrics_row_to_dict(row) for row in rows]

    # Read current Metrics Record (i.e. last one written)
    row = conn.execute(f"SELECT {cols_sql} FROM metrics ORDER BY seq DESC LIMIT 1").fetchone()
    return _metrics_row_to_dict(row) if row else default_metrics()


def write_metrics(metrics=None, flush=False, new_metric=False):
    """
    Write metrics to SQLite DB

    :param metrics: Metrics Data
    :param flush: True to clear metrics. False otherwise
    :param new_metric:
    """
    if metrics is None:
        metrics = default_metrics()

    if flush:
        datastore.execute_write("DELETE FROM metrics")
        return

    cols_sql = ", ".join(METRIC_COLUMNS)
    placeholders = ", ".join(["?"] * len(METRIC_COLUMNS))

    if new_metric:
        metrics["starttime"] = time.time() * 1000
        metrics["id"] = generate_uuid()
        values = [metrics.get(k) for k in METRIC_COLUMNS]
        datastore.execute_write(f"INSERT INTO metrics({cols_sql}) VALUES({placeholders})", values)
        return

    # Replace the last record (or insert if the table is empty)
    values = [metrics.get(k) for k in METRIC_COLUMNS]
    with datastore.transaction() as conn:
        row = conn.execute("SELECT seq FROM metrics ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            conn.execute(f"INSERT INTO metrics({cols_sql}) VALUES({placeholders})", values)
        else:
            set_sql = ", ".join([f"{k}=?" for k in METRIC_COLUMNS])
            conn.execute(f"UPDATE metrics SET {set_sql} WHERE seq=?", values + [row[0]])


def read_settings(filename="settings.json", init=False, retry_count=0):
    """
    Read Settings from SQLite DB (source of truth at runtime).

    :param filename: Unused; kept for signature compatibility.
    :param init: Unused; kept for signature compatibility.
    :param retry_count: Unused; kept for signature compatibility.
    """
    return read_settings_store()


def write_settings(settings):
    """
    Write all settings to SQLite DB (source of truth at runtime).

    :param settings: Settings
    """
    settings["lastupdated"]["time"] = math.trunc(time.time())

    write_settings_store(settings)


def read_settings_store(init=False):
    if init:
        settings = read_settings()
        datastore.set_blob("settings:general", json.dumps(settings))

    # Self-heal like read_control()/default_control(): callers throughout the
    # codebase (is_real_hardware(), default_control(), the mobile blueprint,
    # etc.) assume read_settings() always returns a fully-populated dict.
    # Before this SQLite source-of-truth split, that guarantee came from the
    # settings.json file always existing; now it must come from here until
    # the first-boot import (Task 13) seeds settings:general at startup.
    return _read_json_blob("settings:general", default_settings)


def write_settings_store(settings):
    """
    Write Settings to SQLite DB

    :param settings: Settings
    """
    _write_json_blob("settings:general", settings)


def read_connected_users(flush=False):
    """
    Read Connected Users from SQLite DB

    :param flush: True to clean connected_users. False otherwise
    :return: connected_users (List of Client ID's)
    """
    m = SqliteMembershipList("list_users_connected")
    if flush:
        m.flush()
    return m.list()


def write_connected_user(client_id):
    """
    Write a Connected User to SQLite DB

    :param client_id: Users Client ID from Socket IO/Flask
    """
    SqliteMembershipList("list_users_connected").add(client_id)


def remove_connected_user(client_id):
    """
    Removes a Connected User from SQLite DB

    :param client_id: Users Client ID from Socket IO/Flask
    """
    SqliteMembershipList("list_users_connected").remove(client_id)


def read_pellet_db(filename="pelletdb.json"):
    """
    Read Pellet DataBase from SQLite DB (source of truth at runtime).

    :param filename: Unused; kept for signature compatibility.
    """
    return read_pellets_store()


def write_pellet_db(pelletdb):
    """
    Write Pellet DataBase to SQLite DB (source of truth at runtime).

    :param pelletdb: Pellet Database
    """
    write_pellets_store(pelletdb)


def read_pellets_store(init=False):
    if init:
        pelletdb = read_pellet_db()
        datastore.set_blob("pellets:general", json.dumps(pelletdb))

    # Self-heal like read_settings_store(); see comment there.
    return _read_json_blob("pellets:general", default_pellets)


def write_pellets_store(pelletdb):
    """
    Write Settings to SQLite DB

    :param settings: Settings
    """
    _write_json_blob("pellets:general", pelletdb)


def read_history(num_items=0, flushhistory=False):
    """
    Read history from the datastore and populate a list of data

    :param num_items: Items from end of the history (set to 0 for all items)
    :param flushhistory: True=flush history & current, False=normal history read
    :return: List of history dictionaries (each list item is timestamped 'T')
    """
    if flushhistory:
        datastore.execute_write("DELETE FROM history")  # deletes the history
        read_current(zero_out=True)  # zero-out current data
        write_metrics(flush=True)
        return []

    sql = "SELECT ts,psp,primary_temps,food_temps,aux_temps,notify_targets,ext_data FROM history ORDER BY id"
    rows = datastore.connection().execute(sql).fetchall()
    if num_items > 0:
        rows = rows[-num_items:]

    return [_history_row_to_dict(row) for row in rows]


def _history_row_to_dict(row):
    ts, psp, p, f, aux, nt, exd = row
    d = {"T": ts, "P": json.loads(p), "F": json.loads(f), "PSP": psp, "NT": json.loads(nt), "AUX": json.loads(aux)}
    if exd is not None:
        d["EXD"] = json.loads(exd)
    return d


def write_history(in_data, maxsizelines=28800, ext_data=False):
    """
    Write History to the datastore

    :param in_data: History data to be written to the database
    :param maxsizelines: Maximum Line Size (Default 28800)
    :param ext_data: Extended data to be written to the databse
    """

    ts = int(time.time() * 1000)
    exd = json.dumps(in_data["ext_data"]) if ext_data else None

    with datastore.transaction() as conn:
        conn.execute(
            "INSERT INTO history(ts,psp,primary_temps,food_temps,aux_temps,"
            "notify_targets,ext_data) VALUES(?,?,?,?,?,?,?)",
            (
                ts,
                in_data["primary_setpoint"],  # Setpoint for the primary probe (non-notify setpoint) [value]
                json.dumps(in_data["probe_history"]["primary"]),  # primary probe temperature [key:value]
                json.dumps(in_data["probe_history"]["food"]),  # food probe temperature(s) [key:value pairs]
                json.dumps(in_data["probe_history"]["aux"]),  # auxilliary probe temperature history [key:value]
                json.dumps(in_data["notify_targets"]),  # Notification Target Temps for all probes
                exd,
            ),
        )
        count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        if count > maxsizelines:
            conn.execute(
                "DELETE FROM history WHERE id IN (SELECT id FROM history ORDER BY id LIMIT ?)", (count - maxsizelines,)
            )


def write_current(in_data):
    """
    Write current and populate a dictionary of data

    :param in_data: dictionary containing current temperatures
    """
    current = {}
    current["P"] = in_data["probe_history"]["primary"]
    current["F"] = in_data["probe_history"]["food"]
    current["AUX"] = in_data["probe_history"]["aux"]
    current["PSP"] = in_data["primary_setpoint"]
    current["NT"] = in_data["notify_targets"]
    current["TS"] = int(time.time() * 1000)  # Timestamp
    _write_json_blob("control:current", current)


def read_current(zero_out=False):
    """
    Read current.log and populate a list of data

    :param zero_out: True to zero out current. False otherwise
    :return: Current probe temps structure
    """
    if zero_out:
        """ Build Probe Structure """
        settings = read_settings()
        current = {"P": {}, "F": {}, "PSP": 0, "NT": {}, "AUX": {}}

        for probe in settings["probe_settings"]["probe_map"]["probe_info"]:
            if probe["type"] == "Primary":
                current["P"][probe["label"]] = 0
            if probe["type"] == "Food":
                current["F"][probe["label"]] = 0
            if probe["type"] == "Aux":
                current["AUX"][probe["label"]] = 0
            current["NT"][probe["label"]] = 0

        datastore.set_blob("control:current", json.dumps(current))

    return _read_json_blob("control:current", dict)


def write_tr(tr_data):
    """
    Write tr values to SQLite DB

    """
    _write_json_blob("control:tuning", tr_data)


def read_tr():
    """
    Read tr from SQLite DB and return structure

    :return: Current probe Tr values structure
    """
    return _read_json_blob("control:tuning", dict)


def write_autotune(data):
    SqliteQueue("queue_autotune").push(data)


def read_autotune(flush=False, size_only=False):
    q = SqliteQueue("queue_autotune")
    if flush:
        q.flush()
        return []
    if size_only:
        return q.length()
    return q.list()


def _read_json_key_or_none(key):
    raw = datastore.get_blob(key)
    return json.loads(raw) if raw is not None else None


def _get_install_status(prefix):
    return (
        _read_json_key_or_none(f"{prefix}:percent"),
        _read_json_key_or_none(f"{prefix}:status"),
        _read_json_key_or_none(f"{prefix}:output"),
    )


def _set_install_status(prefix, percent, status, output):
    datastore.set_blob(f"{prefix}:percent", json.dumps(percent))
    datastore.set_blob(f"{prefix}:status", json.dumps(status))
    datastore.set_blob(f"{prefix}:output", json.dumps(output))


def _read_json_blob(key, default_factory):
    raw = datastore.get_blob(key)
    return json.loads(raw) if raw is not None else default_factory()


def _write_json_blob(key, value):
    datastore.set_blob(key, json.dumps(value))


def load_wizard_install_info():
    """
    Load Wizard Install Info from SQLite DB

    :return: wizard_install_info
    """
    return json.loads(datastore.get_blob("wizard:install"))


def store_wizard_install_info(wizard_install_info):
    """
    Write Wizard Install Info to SQLite DB

    :param wizard_install_info: Wizard Install Info
    :return:
    """
    datastore.set_blob("wizard:install", json.dumps(wizard_install_info))


def get_wizard_install_status():
    """
    Read Wizard Install Status from SQLite DB

    :return: Wizard Install (Percent, Status, Output)
    """
    return _get_install_status("wizard")


def set_wizard_install_status(percent, status, output):
    """
    Write Wizard Install Status to SQLite DB

    :param percent: Percent Complete
    :param status: Current Status
    :param output: Output
    """
    _set_install_status("wizard", percent, status, output)


def get_updater_install_status():
    """
    Read Updater Install Status from SQLite DB

    :return: Wizard Updater (Percent, Status, Output)
    """
    return _get_install_status("updater")


def set_updater_install_status(percent, status, output):
    """
    Write Updater Install Status to SQLite DB

    :param percent: Percent Complete
    :param status: Current Status
    :param output: Output
    """
    _set_install_status("updater", percent, status, output)


def write_status(status):
    """
    Write Status to SQLite DB

    :param status: Status Dictionary
    """
    _write_json_blob("control:status", status)


def read_status(init=False):
    """
    Read Status dictionary from SQLite DB
    """
    if init:
        settings = read_settings()
        pellet_db = read_pellet_db()
        hopper_level_enabled = False if settings["modules"]["dist"] == "none" else True
        status = {
            "s_plus": False,
            "hopper_level_enabled": hopper_level_enabled,
            "hopper_level": pellet_db["current"]["hopper_level"],
            "units": settings["globals"]["units"],
            "mode": "Stop",
            "recipe": False,
            "startup_timestamp": 0,
            "start_time": 0,
            "start_duration": 0,
            "shutdown_duration": 0,
            "prime_duration": 0,
            "prime_amount": 0,
            "lid_open_detected": False,
            "lid_open_endtime": 0,
            "p_mode": 0,
            "recipe_paused": False,
            "outpins": {"auger": False, "fan": False, "igniter": False, "power": False},
            "cycle_ratio": 0,
            "fan_duty": 0,
        }
        write_status(status)
    else:
        # Match InMemoryStore semantics: absent status reads back as {} (falsy),
        # not a crash. In production the controller seeds status via init=True
        # before any init=False reader runs; this guards the pre-seed/fresh-DB case.
        status = _read_json_blob("control:status", dict)

    return status


def read_generic_key(key):
    """
    Read generic data from SQLite DB
    :param key: key name
    """
    return json.loads(datastore.get_blob(key))


def write_generic_key(key, value):
    """
    Write generic data to SQLite DB
    :param key: key name
    :parma value: value to write
    """
    datastore.set_blob(key, json.dumps(value))
