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
from common.settings_schema import validate_settings_tree
from common.sqlite_queue import SqliteMembershipList, SqliteQueue


def flush_control():
    """
    Clear the control queues and control blob keys (NOT history/current), then
    reseed default_control().

    Previously reachable only as ``read_control(flush=True)`` -- see
    :func:`flush_history` for why hiding a delete behind a ``read_`` name was a
    problem. Like flush_current (and unlike flush_history) this one returns the
    new state, because every caller rebinds its local ``control`` to it.

    :return: The reseeded default control dictionary.
    """
    for table in ("queue_control_write", "queue_systemq", "queue_systemo"):
        datastore.execute_write(f"DELETE FROM {table}")
    for key in ("control:general", "control:command"):
        datastore.delete_blob(key)
    control = default_control()
    write_control(control, WriteKind.OVERWRITE, origin="common")
    return control


def read_control():
    """
    Read Control from SQLite DB

    :return: control
    """
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


def read_errors():
    """
    Read Errors from SQLite DB

    :return: errors
    """
    return _read_json_blob("errors", list)


def flush_errors():
    """
    Clear the stored error list.

    Returns ``[]`` -- the *new* state, not the discarded contents. This is
    deliberately not a read-and-clear: the sole caller (``control.py``'s boot
    path) wants a cleared store plus a fresh accumulator to hand to
    ``build_devices()``. Returning the pre-flush errors would change what
    build_devices accumulates into, resurrecting errors from the previous run.

    Previously reachable only as ``read_errors(flush=True)`` -- see
    :func:`flush_history` for why that spelling was a problem.

    :return: An empty error list.
    """
    write_errors([])
    return []


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


def read_all_metrics():
    """
    Read every metrics record, in insertion order.

    Split out of ``read_metrics(all=True)``: the flag did not change what was
    read so much as what TYPE came back -- a list here, a dict without it. A
    caller could not tell which it was going to get without opening the callee.

    :return: List of metrics dictionaries (empty when the table is empty).
    """
    cols_sql = ", ".join(METRIC_COLUMNS)
    rows = datastore.connection().execute(f"SELECT {cols_sql} FROM metrics ORDER BY seq").fetchall()
    return [_metrics_row_to_dict(row) for row in rows]


def read_metrics():
    """
    Read the current metrics record, i.e. the last one written.

    :return: A single metrics dictionary; default_metrics() when none exist.
    """
    cols_sql = ", ".join(METRIC_COLUMNS)
    row = datastore.connection().execute(f"SELECT {cols_sql} FROM metrics ORDER BY seq DESC LIMIT 1").fetchone()
    return _metrics_row_to_dict(row) if row else default_metrics()


def flush_metrics():
    """
    Delete every metrics row.

    Previously ``write_metrics(flush=True)``. The name was not lying about
    mutation, but it bundled a table-wide DELETE together with an INSERT and an
    UPDATE behind two booleans -- see :func:`append_metric` /
    :func:`update_metrics`.
    """
    datastore.execute_write("DELETE FROM metrics")


def append_metric(metrics=None):
    """
    INSERT a new metrics row, stamping a fresh ``starttime`` and ``id``.

    This is "start recording a new cook segment". Previously
    ``write_metrics(metrics, new_metric=True)`` -- one keyword away from
    :func:`update_metrics`, which amends the CURRENT record instead of starting
    a new one. That one-keyword gap between "insert" and "update" is the reason
    this split exists.

    Keys outside METRIC_COLUMNS are dropped; columns the caller omits are
    stored as NULL (this is an INSERT -- there is no prior row to inherit from).

    :param metrics: Metrics data; defaults to default_metrics().
    """
    if metrics is None:
        metrics = default_metrics()
    metrics["starttime"] = time.time() * 1000
    metrics["id"] = generate_uuid()
    cols_sql = ", ".join(METRIC_COLUMNS)
    placeholders = ", ".join(["?"] * len(METRIC_COLUMNS))
    values = [metrics.get(k) for k in METRIC_COLUMNS]
    datastore.execute_write(f"INSERT INTO metrics({cols_sql}) VALUES({placeholders})", values)


def update_metrics(metrics):
    """
    Amend the CURRENT (last-written) metrics record in place.

    Inserts instead when the table is empty, so the first update on a flushed
    store is not silently dropped. Previously ``write_metrics(metrics)``.

    :param metrics: Metrics data; a partial dict is allowed (see below).
    """
    cols_sql = ", ".join(METRIC_COLUMNS)
    placeholders = ", ".join(["?"] * len(METRIC_COLUMNS))

    with datastore.transaction() as conn:
        row = conn.execute("SELECT seq FROM metrics ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            values = [metrics.get(k) for k in METRIC_COLUMNS]
            conn.execute(f"INSERT INTO metrics({cols_sql}) VALUES({placeholders})", values)
        else:
            # Only touch the keys the caller actually provided -- presence, not
            # truthiness, decides. A partial dict (e.g. {"mode": "Hold"}) must
            # update just that column and leave the rest of the last row alone;
            # blasting every METRIC_COLUMNS value nulls out columns the caller
            # never mentioned. A caller that genuinely wants to null a column
            # passes it explicitly (e.g. {"col": None}).
            present_keys = [k for k in METRIC_COLUMNS if k in metrics]
            if present_keys:
                set_sql = ", ".join([f"{k}=?" for k in present_keys])
                values = [metrics[k] for k in present_keys]
                conn.execute(f"UPDATE metrics SET {set_sql} WHERE seq=?", values + [row[0]])


def read_settings():
    """
    Read Settings from SQLite DB (source of truth at runtime).

    The old signature carried three parameters -- filename="settings.json",
    init=False, retry_count=0 -- all three documented "Unused; kept for
    signature compatibility" since the JSON-file backend was replaced by SQLite.
    They are gone: a dead ``filename`` default in particular implied this
    function still reads a file next to the process, which it does not.
    """
    return read_settings_store()


def write_settings(settings):
    """
    Write all settings to SQLite DB (source of truth at runtime).

    Strict-validates the tree first: validate_settings_tree()
    raises SettingsValidationError on any schema violation, BEFORE
    lastupdated.time is stamped or anything is persisted -- a rejected write
    leaves the store untouched (atomic). The normalized dump it returns
    (not the caller's raw dict) is what actually gets persisted, so callers
    relying on type coercion/aliasing (e.g. platform.system's "1WIRE") get it
    too. No bypass parameter -- every write_settings() call goes through
    this gate.

    :param settings: Settings
    """
    settings = validate_settings_tree(settings)
    settings["lastupdated"]["time"] = math.trunc(time.time())

    write_settings_store(settings)


def seed_settings_store():
    """
    Materialize settings:general in the datastore and return it.

    read_settings_store() self-heals a missing blob by *returning*
    default_settings() without persisting it; this writes that value back so the
    blob actually exists. On an already-seeded store it rewrites the current
    value unchanged.

    Previously ``read_settings_store(init=True)`` -- a ``read_`` name whose flag
    turned it into a write. Same defect as the old ``read_history(flushhistory=True)``
    (see :func:`flush_history`), just with a different word.

    :return: The settings dictionary now persisted.
    """
    settings = read_settings()
    datastore.set_blob("settings:general", json.dumps(settings))
    return settings


def read_settings_store():
    # Self-heal like read_control()/default_control(): callers throughout the
    # codebase (is_real_hardware(), default_control(), the mobile blueprint,
    # etc.) assume read_settings() always returns a fully-populated dict.
    # Before this SQLite source-of-truth split, that guarantee came from the
    # settings.json file always existing; now it must come from here until
    # the first-boot import seeds settings:general at startup.
    return _read_json_blob("settings:general", default_settings)


def write_settings_store(settings):
    """
    Write Settings to SQLite DB

    :param settings: Settings
    """
    _write_json_blob("settings:general", settings)


def read_connected_users():
    """
    Read Connected Users from SQLite DB

    :return: connected_users (List of Client ID's)
    """
    return SqliteMembershipList("list_users_connected").list()


def flush_connected_users():
    """
    Drop every connected-user client ID.

    Called once at web-process import time: any client IDs still in the list
    belong to sockets of a previous process and can never reconnect.

    Previously reachable only as ``read_connected_users(flush=True)`` -- see
    :func:`flush_history` for why that spelling was a problem.

    :return: An empty user list (the post-flush state).
    """
    m = SqliteMembershipList("list_users_connected")
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


def read_pellet_db():
    """
    Read Pellet DataBase from SQLite DB (source of truth at runtime).

    The old signature carried a dead filename="pelletdb.json" parameter; see
    :func:`read_settings` for why that default was actively misleading.
    """
    return read_pellets_store()


def write_pellet_db(pelletdb):
    """
    Write Pellet DataBase to SQLite DB (source of truth at runtime).

    :param pelletdb: Pellet Database
    """
    write_pellets_store(pelletdb)


def seed_pellets_store():
    """
    Materialize pellets:general in the datastore and return it.

    See :func:`seed_settings_store` -- same shape, same reason for the rename.

    :return: The pellet database now persisted.
    """
    pelletdb = read_pellet_db()
    datastore.set_blob("pellets:general", json.dumps(pelletdb))
    return pelletdb


def read_pellets_store():
    # Self-heal like read_settings_store(); see comment there.
    return _read_json_blob("pellets:general", default_pellets)


def write_pellets_store(pelletdb):
    """
    Write Settings to SQLite DB

    :param settings: Settings
    """
    _write_json_blob("pellets:general", pelletdb)


def flush_history():
    """
    Erase all history: the history rows, the current probe values, and metrics.

    Used when stored history would no longer mean what it says -- a units
    change (the temperatures are recorded in whatever unit was active), a new
    cook starting, or an explicit wipe from the admin page.

    This was previously reachable only as ``read_history(flushhistory=True)``,
    inherited from upstream PiFire's Redis implementation. That spelling hid a
    three-table delete behind a name that reads as a query, so call sites like
    a units-change handler gave no hint that they destroyed the history store.
    """
    datastore.execute_write("DELETE FROM history")
    flush_current()
    flush_metrics()


def read_history(num_items=0):
    """
    Read history from the datastore and populate a list of data

    :param num_items: Items from end of the history (set to 0 for all items)
    :return: List of history dictionaries (each list item is timestamped 'T')
    """
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


def flush_current():
    """
    Reset the current probe values to a zeroed structure and return it.

    Rebuilt from the configured probe_map rather than blanked in place, so a
    probe added or removed since the last write is reflected.

    Previously reachable only as ``read_current(zero_out=True)`` -- see
    :func:`flush_history` for why that spelling was a problem. Unlike
    flush_history this one does return the new state, because callers
    legitimately want to hand the zeroed structure straight to a client.

    :return: Zeroed current probe temps structure
    """
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


def read_current():
    """
    Read current.log and populate a list of data

    :return: Current probe temps structure
    """
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


def read_autotune():
    """
    Read every queued autotune sample.

    :return: List of autotune sample dictionaries.
    """
    return SqliteQueue("queue_autotune").list()


def autotune_length():
    """
    Count the queued autotune samples without materializing them.

    Split out of ``read_autotune(size_only=True)``, which returned an **int**
    where the same name otherwise returned a **list** -- invisible at the call
    site.

    :return: Number of queued samples.
    """
    return SqliteQueue("queue_autotune").length()


def flush_autotune():
    """
    Discard every queued autotune sample.

    Previously reachable only as ``read_autotune(flush=True)`` -- see
    :func:`flush_history` for why that spelling was a problem.

    :return: An empty sample list (the post-flush state).
    """
    SqliteQueue("queue_autotune").flush()
    return []


def _read_json_key_or_none(key):
    raw = datastore.get_blob(key)
    return json.loads(raw) if raw is not None else None


_OS_INFO_KEY = "system:os_info"


def store_os_info(os_info):
    """Cache the OS/architecture probe (see common.system.get_os_info).

    Lives in the datastore rather than an os_info.json next to the process:
    the file was resolved against the CWD, so where it landed depended on who
    started PiFire, and a mere read could create one in the wrong place.
    """
    datastore.set_blob(_OS_INFO_KEY, json.dumps(os_info))


def load_os_info():
    """Return the cached OS info, or {} when nothing has been cached yet."""
    return _read_json_key_or_none(_OS_INFO_KEY) or {}


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


def init_status():
    """
    Build a fresh status dictionary from settings/pellets, persist it, return it.

    Previously ``read_status(init=True)`` -- a ``read_`` name whose flag turned
    it into a write (it calls write_status()). Same defect as the old
    ``read_history(flushhistory=True)``; see :func:`flush_history`.

    :return: The status dictionary now persisted.
    """
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
    return status


def read_status():
    """
    Read Status dictionary from SQLite DB
    """
    # Match InMemoryStore semantics: absent status reads back as {} (falsy),
    # not a crash. In production the controller seeds status via init_status()
    # before any read_status() caller runs; this guards the pre-seed/fresh-DB
    # case. Now that the two are separate functions that ordering constraint is
    # a caller-visible contract rather than a branch, but it is no less required.
    return _read_json_blob("control:status", dict)


def read_generic_key(key):
    """
    Read generic data from SQLite DB
    :param key: key name
    """
    return json.loads(datastore.get_blob(key))


def read_probe_status(probe_info):
    """
    Creates a structured status report for all probes in the system by combining probe configuration
    information with current device status information.

    Args:
            probe_info (list): List of probe configuration dictionaries containing information about each
                    probe such as type, label, device, etc.

    Returns:
            dict: A nested dictionary containing probe status information organized by probe type:
                    {
                            'P': {    # Primary probes
                                    '<probe_label>': {
                                            'status': {},
                                            'config': {},
                                            'enabled': bool,
                                            'profile': str or None,
                                            'port': str or None,
                                            'type': str or None,
                                            'device': str or None,
                                            'label': str or None,
                                            'name': str or None
                                    }
                            },
                            'F': {},  # Food probes (same structure as P)
                            'AUX': {} # Auxiliary probes (same structure as P)
                    }

    Example:
            probe_info = [
                    {
                            'type': 'Primary',
                            'label': 'Grill',
                            'device': 'device1',
                            ...
                    },
                    ...
            ]
            status = read_probe_status(probe_info)
            # Returns structured status information for all probes
    """
    # Get current device status information from the datastore
    probe_device_info = read_generic_key("probe_device_info")
    # print(f'Probe Device Info: {probe_device_info}')

    # Initialize the status structure
    probe_status = {
        "P": {},  # Primary probes
        "F": {},  # Food probes
        "AUX": {},  # Auxiliary probes
    }

    # Process each probe in the configuration
    for probe in probe_info:
        # Determine section based on probe type
        if probe["type"] == "Primary":
            section = "P"
        elif probe["type"] == "Food":
            section = "F"
        elif probe["type"] == "Aux":
            section = "AUX"
        else:
            # Unknown/unexpected probe type: there is no valid bucket for it
            # (downstream only consumes the fixed P/F/AUX sections). Skip it
            # rather than raising UnboundLocalError on the first probe or --
            # worse -- silently misfiling it into whichever section a prior
            # probe happened to set. Log so the bad config surfaces.
            logging.getLogger("control").warning(
                "read_probe_status: skipping probe %r with unexpected type %r (expected Primary/Food/Aux).",
                probe.get("label"),
                probe.get("type"),
            )
            continue
        probe_device = probe["device"]

        # Find matching device status and combine with probe configuration
        for device in probe_device_info:
            if device["device"] == probe_device:
                probe_status[section][probe["label"]] = {}  # Initialize dict for this probe
                probe_status[section][probe["label"]]["status"] = device.get("status", {})
                probe_status[section][probe["label"]]["config"] = device.get("config", {})
                probe_status[section][probe["label"]]["enabled"] = probe.get("enabled", True)
                probe_status[section][probe["label"]]["profile"] = probe.get("profile", None)
                probe_status[section][probe["label"]]["port"] = probe.get("port", None)
                probe_status[section][probe["label"]]["type"] = probe.get("type", None)
                probe_status[section][probe["label"]]["device"] = probe.get("device", None)
                probe_status[section][probe["label"]]["label"] = probe.get("label", None)
                probe_status[section][probe["label"]]["name"] = probe.get("name", None)

    return probe_status


def write_generic_key(key, value):
    """
    Write generic data to SQLite DB
    :param key: key name
    :parma value: value to write
    """
    datastore.set_blob(key, json.dumps(value))
