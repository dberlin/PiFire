"""PiFire-owned schema migration integration.

This module wraps configured connections; it never opens or closes them.
"""

import sqlite3
from typing import Final

from sqlite_utils import Database

LEGACY_SCHEMA_VERSION: Final[int] = 10
CURRENT_SCHEMA_VERSION: Final[int] = LEGACY_SCHEMA_VERSION


def database_for_connection(connection: sqlite3.Connection) -> Database:
    """Wrap an existing PiFire connection without taking ownership of it."""
    return Database(
        connection,
        recursive_triggers=False,
        execute_plugins=False,
    )
