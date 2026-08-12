"""SQLite persistence for mutable runtime state."""

import json
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
                f"SELECT message FROM errors WHERE kind IN ({placeholders}) "
                f"ORDER BY CASE kind {rank} END, id",
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


def write_generic_key(key, value):
    """Encode a JSON value under an arbitrary runtime key."""
    datastore.set_blob(key, json.dumps(value))
