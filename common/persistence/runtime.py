"""SQLite persistence for mutable runtime state."""

import json
import logging
import math
import time
from typing import cast

from common import datastore
from common.common import ErrorKind
from common.current_schema import dump_legacy, load_current, snapshot_from, zeroed_current
from common.defaults import default_pellets, default_settings
from common.persistence.transforms import current_snapshot, initial_status
from common.pellets_schema import validate_pellet_db
from common.settings_schema import validate_settings_tree
from common.sqlite_queue import SqliteMembershipList, SqliteQueue


def _read_json_blob(key, default_factory):
    raw = datastore.get_blob(key)
    return json.loads(raw) if raw is not None else default_factory()


def _write_json_blob(key, value):
    datastore.set_blob(key, json.dumps(value))


def read_settings():
    """Read the validated-at-write runtime settings tree."""
    return read_settings_store()


def write_settings(settings):
    """Validate, stamp, and replace the runtime settings tree."""
    settings = validate_settings_tree(settings)
    settings["lastupdated"]["time"] = math.trunc(time.time())
    write_settings_store(settings)


def seed_settings_store():
    """Materialize the current settings value and return it."""
    settings = read_settings()
    datastore.set_blob("settings:general", json.dumps(settings))
    return settings


def read_settings_store():
    """Read settings, producing a fresh default tree when the blob is absent."""
    return _read_json_blob("settings:general", default_settings)


def write_settings_store(settings):
    """Replace the settings blob without applying the public write gate."""
    _write_json_blob("settings:general", settings)


def read_pellet_db():
    """Read the validated-at-write runtime pellet database."""
    return read_pellets_store()


def write_pellet_db(pelletdb):
    """Validate and replace the runtime pellet database."""
    write_pellets_store(validate_pellet_db(pelletdb))


def seed_pellets_store():
    """Materialize the current pellet database and return it."""
    pelletdb = read_pellet_db()
    datastore.set_blob("pellets:general", json.dumps(pelletdb))
    return pelletdb


def read_pellets_store():
    """Read pellets, producing a fresh default tree when the blob is absent."""
    return _read_json_blob("pellets:general", default_pellets)


def write_pellets_store(pelletdb):
    """Replace the pellet blob without applying the public write gate."""
    _write_json_blob("pellets:general", pelletdb)


def write_current(in_data):
    """Merge one control-loop sample into the durable current wire shape."""
    previous = load_current(_read_json_blob("control:current", dict))
    schema = current_snapshot(previous, in_data, int(time.time() * 1000))
    _write_json_blob("control:current", dump_legacy(schema))


def flush_current():
    """Rebuild and persist zeroed current values from the configured probe map."""
    settings = read_settings()
    schema = zeroed_current(settings["probe_settings"]["probe_map"]["probe_info"])
    _write_json_blob("control:current", dump_legacy(schema, exclude_timestamp=True))
    return _read_json_blob("control:current", dict)


def read_current():
    """Read the current probe values in their durable wire shape."""
    return _read_json_blob("control:current", dict)


def read_current_snapshot():
    """Read current probe values as a validated canonical snapshot."""
    return snapshot_from(
        _read_json_blob("control:current", dict),
        lambda: read_settings()["probe_settings"]["probe_map"]["probe_info"],
    )


def write_status(status):
    """Replace the controller status blob."""
    _write_json_blob("control:status", status)


def init_status():
    """Build, persist, and return fresh controller status."""
    status = initial_status(read_settings(), read_pellet_db())
    write_status(status)
    return status


def read_status():
    """Read controller status, returning an empty mapping before initialization."""
    return _read_json_blob("control:status", dict)


def _writable_error_kind(kind):
    if not isinstance(kind, ErrorKind):
        raise ValueError(f"error kind must be an ErrorKind, got {kind!r}")
    if kind is ErrorKind.ALL:
        raise ValueError(f"{kind} is a read-only selector; write and flush need a single owning kind")
    return kind.value


def read_errors(kind):
    """Read one owner's errors, or all owners in declaration order."""
    if not isinstance(kind, ErrorKind):
        raise ValueError(f"error kind must be an ErrorKind, got {kind!r}")
    if kind is ErrorKind.ALL:
        owners = [candidate.value for candidate in ErrorKind if candidate is not ErrorKind.ALL]
        placeholders = ",".join("?" for _ in owners)
        rank = " ".join(f"WHEN ? THEN {index}" for index, _ in enumerate(owners))
        rows = (
            datastore.connection()
            .execute(
                f"SELECT message FROM errors WHERE kind IN ({placeholders}) ORDER BY CASE kind {rank} END, id",
                (*owners, *owners),
            )
            .fetchall()
        )
    else:
        rows = (
            datastore.connection()
            .execute("SELECT message FROM errors WHERE kind = ? ORDER BY id", (kind.value,))
            .fetchall()
        )
    return [row[0] for row in rows]


def flush_errors(kind):
    """Clear one owner's errors and return the new empty state."""
    write_errors(kind, [])
    return []


def write_errors(kind, errors):
    """Atomically replace one owner's error list."""
    stored_kind = _writable_error_kind(kind)
    with datastore.transaction() as connection:
        connection.execute("DELETE FROM errors WHERE kind = ?", (stored_kind,))
        connection.executemany(
            "INSERT INTO errors (kind, message) VALUES (?, ?)",
            [(stored_kind, error) for error in errors],
        )


def read_warnings_snapshot():
    """Read outstanding warnings with the last returned row's high-water id."""
    rows = SqliteQueue("list_warnings", raw=True).list_with_ids()
    return {
        "warnings": [value for _, value in rows],
        "max_id": rows[-1][0] if rows else None,
    }


def clear_warnings_through(max_id):
    """Clear warnings no newer than a previously observed high-water id."""
    SqliteQueue("list_warnings", raw=True).clear_through(max_id)


def write_warning(warning):
    """Append one warning string."""
    SqliteQueue("list_warnings", raw=True).push(warning)


def read_connected_users():
    """Read connected client ids in insertion order."""
    return SqliteMembershipList("list_users_connected").list()


def flush_connected_users():
    """Remove all connected client ids and return the empty state."""
    users = SqliteMembershipList("list_users_connected")
    users.flush()
    return users.list()


def write_connected_user(client_id):
    """Append one connected client id."""
    SqliteMembershipList("list_users_connected").add(client_id)


def remove_connected_user(client_id):
    """Remove every occurrence of a connected client id."""
    SqliteMembershipList("list_users_connected").remove(client_id)


def read_generic_key(key):
    """Decode a JSON value stored under an arbitrary runtime key."""
    return json.loads(cast(str, datastore.get_blob(key)))


#: Datastore key carrying the control process's liveness stamp. Written by the
#: control process only, through its Store, from BOTH the idle tick and the
#: per-mode work cycle -- a cook never returns to the idle tick, so a stamp in
#: only one of the two would read as "control is down" for the whole cook.
CONTROL_HEARTBEAT_KEY = "control:heartbeat"

#: How stale the stamp may get before a reader calls the control process down.
#: Deliberately several times the write interval
#: (controller.runtime.heartbeat.HEARTBEAT_WRITE_INTERVAL): the stamp is written
#: BY the control loop, so it measures "the loop is servicing work", not merely
#: "the process exists" -- which is the property worth reporting, but it does
#: mean a legitimately blocking tick (a mode transition drives the output relays
#: and can write a cookfile) must not read as a failure. Detection is therefore
#: this slow; RECOVERY is not, and recovery is the half users notice.
#: Lives here, beside the key, because the writer and the reader are different
#: PROCESSES -- the web process must not import from controller.runtime.
CONTROL_HEARTBEAT_STALE_AFTER = 15.0


def read_control_heartbeat():
    """Epoch seconds of the control process's last heartbeat, or None if it has
    never stamped one (fresh DB, or a control process too old to publish it).

    A read, not a round trip: callers decide liveness by comparing this against
    their own clock, so a stopped control process needs no cooperation to be
    detected -- which is the whole point, since a stopped process cannot answer
    a request.
    """
    return _read_json_blob(CONTROL_HEARTBEAT_KEY, lambda: None)


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
    """Encode a JSON value under an arbitrary runtime key."""
    datastore.set_blob(key, json.dumps(value))


def write_controller_model_checkpoint(name, snapshot):
    """Atomically persist a strictly newer controller snapshot."""
    if not isinstance(name, str) or not name.strip() or not isinstance(snapshot, dict):
        return False
    revision = snapshot.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return False
    try:
        encoded_snapshot = json.dumps(snapshot, allow_nan=False)
    except ValueError, TypeError:
        return False
    with datastore.transaction() as connection:
        row = connection.execute("SELECT value FROM kv WHERE key=?", ("controller_model_state",)).fetchone()
        if row is None:
            models = {}
        else:
            try:
                state = json.loads(row[0])
            except TypeError, ValueError:
                return False
            if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("models"), dict):
                return False
            models = state["models"]
        existing = models.get(name)
        if isinstance(existing, dict):
            existing_revision = existing.get("revision")
            if (
                isinstance(existing_revision, bool)
                or not isinstance(existing_revision, int)
                or existing_revision >= revision
            ):
                return False
        elif existing is not None:
            return False
        updated_models = dict(models)
        updated_models[name] = json.loads(encoded_snapshot)
        encoded_state = json.dumps({"version": 1, "models": updated_models})
        connection.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("controller_model_state", encoded_state),
        )
    return True
