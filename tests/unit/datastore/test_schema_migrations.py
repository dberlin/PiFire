"""Contracts for PiFire's selective sqlite-utils migration registry."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from common import datastore, schema_migrations

_MIGRATION_SET = "pifire-schema"
_V11_MIGRATION = "v0011_adopt_sqlite_utils_registry"


def _open_configured_connection(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.isolation_level = None
    return connection


def _seed_committed_v10_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(datastore.SCHEMA + datastore._queue_ddl())
        connection.execute(datastore._logs_retention_ddl())
        connection.execute("PRAGMA user_version=10")
        connection.commit()
    finally:
        connection.close()


def _create_migrations_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE _sqlite_migrations (
            id INTEGER PRIMARY KEY,
            migration_set TEXT,
            name TEXT,
            applied_at TEXT
        );
        CREATE UNIQUE INDEX idx__sqlite_migrations_migration_set_name
            ON _sqlite_migrations (migration_set, name);
        """
    )


def _seed_committed_v11_database(path: Path) -> None:
    _seed_committed_v10_database(path)
    connection = sqlite3.connect(path)
    try:
        _create_migrations_table(connection)
        connection.execute(
            "INSERT INTO _sqlite_migrations(migration_set, name, applied_at) VALUES(?, ?, ?)",
            (_MIGRATION_SET, _V11_MIGRATION, "2026-08-29 00:00:00+00:00"),
        )
        connection.execute("PRAGMA user_version=11")
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def v10_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "committed-v10.db"
    _seed_committed_v10_database(path)
    connection = _open_configured_connection(path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def v11_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "committed-v11.db"
    _seed_committed_v11_database(path)
    connection = _open_configured_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def _audit_rows(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return connection.execute("SELECT migration_set, name, applied_at FROM _sqlite_migrations ORDER BY id").fetchall()


def test_current_schema_version_is_centralized_at_v11() -> None:
    assert datastore.DB_SCHEMA_VERSION == schema_migrations.CURRENT_SCHEMA_VERSION == 11
    assert schema_migrations.LEGACY_SCHEMA_VERSION == 10


def test_migration_discovery_occurs_after_pifire_begin_immediate(
    v10_connection: sqlite3.Connection,
) -> None:
    traced: list[str] = []
    v10_connection.set_trace_callback(traced.append)

    datastore._ensure_schema(v10_connection)

    normalized = ["".join(statement.lower().split()) for statement in traced]
    begin = normalized.index("beginimmediate")
    discovery = next(index for index, statement in enumerate(normalized) if "_sqlite_migrations" in statement)
    assert begin < discovery


def test_v10_upgrades_to_one_named_v11_audit_record_and_is_reconnect_idempotent(
    v10_connection: sqlite3.Connection,
) -> None:
    datastore._ensure_schema(v10_connection)

    assert v10_connection.execute("PRAGMA user_version").fetchone() == (11,)
    rows = _audit_rows(v10_connection)
    assert len(rows) == 1
    assert rows[0][0:2] == (_MIGRATION_SET, _V11_MIGRATION)
    assert rows[0][2]

    path = v10_connection.execute("PRAGMA database_list").fetchone()[2]
    reconnected = _open_configured_connection(path)
    try:
        datastore._ensure_schema(reconnected)
        assert reconnected.execute("PRAGMA user_version").fetchone() == (11,)
        assert _audit_rows(reconnected) == rows
    finally:
        reconnected.close()


def test_v11_tracking_table_has_public_sqlite_utils_index_shape(
    v10_connection: sqlite3.Connection,
) -> None:
    datastore._ensure_schema(v10_connection)

    columns = [(row[1], row[2], row[5]) for row in v10_connection.execute("PRAGMA table_info(_sqlite_migrations)")]
    assert columns == [
        ("id", "INTEGER", 1),
        ("migration_set", "TEXT", 0),
        ("name", "TEXT", 0),
        ("applied_at", "TEXT", 0),
    ]
    unique_indexes = [row[1] for row in v10_connection.execute("PRAGMA index_list(_sqlite_migrations)") if row[2]]
    assert [
        tuple(row[2] for row in v10_connection.execute(f'PRAGMA index_info("{index_name}")'))
        for index_name in unique_indexes
    ] == [("migration_set", "name")]


def test_v11_failure_rolls_back_tracking_ddl_and_retries(
    v10_connection: sqlite3.Connection,
) -> None:
    def deny_v11(action, arg1, arg2, database_name, trigger_name):
        if action == sqlite3.SQLITE_PRAGMA and arg1.lower() == "user_version" and arg2 == "11":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    v10_connection.set_authorizer(deny_v11)
    with pytest.raises(sqlite3.DatabaseError):
        datastore._ensure_schema(v10_connection)
    v10_connection.set_authorizer(None)

    assert v10_connection.execute("PRAGMA user_version").fetchone() == (10,)
    assert (
        v10_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_sqlite_migrations'"
        ).fetchone()
        is None
    )

    datastore._ensure_schema(v10_connection)
    assert v10_connection.execute("PRAGMA user_version").fetchone() == (11,)
    assert [row[0:2] for row in _audit_rows(v10_connection)] == [(_MIGRATION_SET, _V11_MIGRATION)]


def test_applied_v11_record_with_user_version_10_fails_closed(
    v11_connection: sqlite3.Connection,
) -> None:
    original_rows = _audit_rows(v11_connection)
    v11_connection.execute("PRAGMA user_version=10")

    with pytest.raises(RuntimeError, match="migration authority conflict"):
        datastore._ensure_schema(v11_connection)

    assert v11_connection.execute("PRAGMA user_version").fetchone() == (10,)
    assert _audit_rows(v11_connection) == original_rows


@pytest.mark.parametrize("newer_version", [11, 12])
def test_existing_version_without_v11_record_is_a_public_audited_noop(
    v10_connection: sqlite3.Connection,
    newer_version: int,
) -> None:
    v10_connection.execute(f"PRAGMA user_version={newer_version}")

    datastore._ensure_schema(v10_connection)

    assert v10_connection.execute("PRAGMA user_version").fetchone() == (newer_version,)
    assert [row[0:2] for row in _audit_rows(v10_connection)] == [(_MIGRATION_SET, _V11_MIGRATION)]


def test_missing_known_record_after_public_apply_fails_before_outer_commit(
    v10_connection: sqlite3.Connection,
) -> None:
    _create_migrations_table(v10_connection)
    v10_connection.execute(
        """
        CREATE TRIGGER suppress_migration_audit
        BEFORE INSERT ON _sqlite_migrations
        BEGIN
            SELECT RAISE(IGNORE);
        END
        """
    )
    v10_connection.execute("PRAGMA user_version=11")

    with pytest.raises(RuntimeError, match="migration authority conflict"):
        datastore._ensure_schema(v10_connection)

    assert v10_connection.execute("PRAGMA user_version").fetchone() == (11,)
    assert _audit_rows(v10_connection) == []


def test_unknown_record_in_pifire_migration_set_fails_closed(
    v11_connection: sqlite3.Connection,
) -> None:
    v11_connection.execute(
        "INSERT INTO _sqlite_migrations(migration_set, name, applied_at) VALUES(?, ?, ?)",
        (_MIGRATION_SET, "unknown_future_name", "2026-08-29 00:00:00+00:00"),
    )
    original_rows = _audit_rows(v11_connection)

    with pytest.raises(RuntimeError, match="unknown migration record"):
        datastore._ensure_schema(v11_connection)

    assert v11_connection.execute("PRAGMA user_version").fetchone() == (11,)
    assert _audit_rows(v11_connection) == original_rows


def test_registered_runner_rejects_plain_apply_without_pifire_transaction(
    v10_connection: sqlite3.Connection,
) -> None:
    database = schema_migrations.database_for_connection(v10_connection)
    assert not v10_connection.in_transaction

    with pytest.raises(RuntimeError, match="PiFire BEGIN IMMEDIATE"):
        schema_migrations.apply_registered_migrations(database)

    assert v10_connection.execute("PRAGMA user_version").fetchone() == (10,)
    assert (
        v10_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_sqlite_migrations'"
        ).fetchone()
        is None
    )
