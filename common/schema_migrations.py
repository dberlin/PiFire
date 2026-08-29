"""PiFire-owned schema migration integration.

This module wraps configured connections; it never opens or closes them.
"""

import sqlite3
from typing import Final

from sqlite_utils import Database
from sqlite_utils.migrations import Migrations

LEGACY_SCHEMA_VERSION: Final[int] = 10
CURRENT_SCHEMA_VERSION: Final[int] = 11
MIGRATION_SET_NAME: Final[str] = "pifire-schema"
V11_REGISTRY_ADOPTION: Final[str] = "v0011_adopt_sqlite_utils_registry"

_SCHEMA_MIGRATIONS = Migrations(MIGRATION_SET_NAME)
_MIGRATION_TARGETS: Final[dict[str, int]] = {
    V11_REGISTRY_ADOPTION: CURRENT_SCHEMA_VERSION,
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
    if version < CURRENT_SCHEMA_VERSION:
        if version != LEGACY_SCHEMA_VERSION:
            raise RuntimeError(f"cannot bridge schema version {version} to {CURRENT_SCHEMA_VERSION}")
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
