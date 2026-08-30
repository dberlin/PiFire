"""PiFire-owned schema migration integration.

This module wraps configured connections; it never opens or closes them.
"""

import sqlite3
from typing import Final

from sqlite_utils import Database
from sqlite_utils.migrations import Migrations

LEGACY_SCHEMA_VERSION: Final[int] = 10
REGISTRY_ADOPTION_SCHEMA_VERSION: Final[int] = 11
CURRENT_SCHEMA_VERSION: Final[int] = 12
MIGRATION_SET_NAME: Final[str] = "pifire-schema"
V11_REGISTRY_ADOPTION: Final[str] = "v0011_adopt_sqlite_utils_registry"
V12_TRAJECTORY_ROLE_GENERATION: Final[str] = "v0012_trajectory_role_generation"

_SCHEMA_MIGRATIONS = Migrations(MIGRATION_SET_NAME)
_MIGRATION_TARGETS: Final[dict[str, int]] = {
    V11_REGISTRY_ADOPTION: REGISTRY_ADOPTION_SCHEMA_VERSION,
    V12_TRAJECTORY_ROLE_GENERATION: CURRENT_SCHEMA_VERSION,
}


def database_for_connection(connection: sqlite3.Connection) -> Database:
    """Wrap an existing PiFire connection without taking ownership of it."""
    return Database(
        connection,
        recursive_triggers=False,
        execute_plugins=False,
    )


def _user_version(database: Database) -> int:
    return int(database.execute("PRAGMA user_version").fetchone()[0])


@_SCHEMA_MIGRATIONS(name=V11_REGISTRY_ADOPTION)
def _adopt_sqlite_utils_registry(database: Database) -> None:
    version = _user_version(database)
    if version < LEGACY_SCHEMA_VERSION:
        raise RuntimeError(
            f"legacy schema must reach {LEGACY_SCHEMA_VERSION} before registered migrations; got {version}"
        )
    if version < REGISTRY_ADOPTION_SCHEMA_VERSION:
        if version != LEGACY_SCHEMA_VERSION:
            raise RuntimeError(f"cannot bridge schema version {version} to {REGISTRY_ADOPTION_SCHEMA_VERSION}")
        database.execute(f"PRAGMA user_version={REGISTRY_ADOPTION_SCHEMA_VERSION}")


@_SCHEMA_MIGRATIONS(name=V12_TRAJECTORY_ROLE_GENERATION)
def _migrate_trajectory_role_generation(database: Database) -> None:
    version = _user_version(database)
    if version >= CURRENT_SCHEMA_VERSION:
        return
    if version != REGISTRY_ADOPTION_SCHEMA_VERSION:
        raise RuntimeError(f"cannot migrate schema version {version} to {CURRENT_SCHEMA_VERSION}")
    database.execute("ALTER TABLE learning_trajectory_frame RENAME TO learning_trajectory_frame_v11")
    database.execute(
        """
        CREATE TABLE learning_trajectory_frame (
            segment_id                 TEXT NOT NULL,
            ordinal                    INTEGER NOT NULL,
            kind                       TEXT NOT NULL
                                       CHECK(kind IN ('pre-roll','scored')),
            payload_schema_version     INTEGER NOT NULL
                                       CHECK(payload_schema_version IN (2, 3)),
            interval_identity          TEXT NOT NULL,
            canonical_json             TEXT NOT NULL
                                       CHECK(json_valid(canonical_json)),
            frame_digest               TEXT NOT NULL,
            created_corpus_revision    INTEGER NOT NULL,
            PRIMARY KEY(segment_id, ordinal),
            FOREIGN KEY(segment_id)
                REFERENCES learning_trajectory_segment(segment_id)
                ON DELETE CASCADE
        )
        """
    )
    database.execute(
        """
        INSERT INTO learning_trajectory_frame(
            segment_id, ordinal, kind, payload_schema_version,
            interval_identity, canonical_json, frame_digest,
            created_corpus_revision
        )
        SELECT
            segment_id, ordinal, kind, payload_schema_version,
            interval_identity, canonical_json, frame_digest,
            created_corpus_revision
        FROM learning_trajectory_frame_v11
        """
    )
    database.execute("DROP TABLE learning_trajectory_frame_v11")
    database.execute(
        "CREATE INDEX ix_learning_frame_revision "
        "ON learning_trajectory_frame("
        "segment_id, created_corpus_revision, ordinal)"
    )
    database.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")


def apply_registered_migrations(database: Database) -> None:
    """Apply known migrations only inside PiFire's active transaction."""
    if not database.conn.in_transaction:
        raise RuntimeError("registered migrations require PiFire BEGIN IMMEDIATE")

    _SCHEMA_MIGRATIONS.apply(database)

    version = _user_version(database)
    applied = [migration.name for migration in _SCHEMA_MIGRATIONS.applied(database)]
    applied_set = set(applied)
    known_set = set(_MIGRATION_TARGETS)

    unknown = applied_set - known_set
    if unknown:
        raise RuntimeError(f"unknown migration record in {MIGRATION_SET_NAME}: {sorted(unknown)}")

    ahead = [name for name, target in _MIGRATION_TARGETS.items() if name in applied_set and version < target]
    missing = [name for name, target in _MIGRATION_TARGETS.items() if version >= target and name not in applied_set]
    if ahead or missing or version < CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"migration authority conflict: user_version={version}, applied={applied}, ahead={ahead}, missing={missing}"
        )
