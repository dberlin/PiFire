"""Typed SQLite persistence for control-trace records."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3

from common import datastore
from common.control_trace import (
    COMPATIBLE_TRACE_SCHEMA_VERSIONS,
    TRACE_SCHEMA_VERSION,
    ControlTraceDbRow,
    ControlTraceRecord,
)


CONTROL_TRACE_MAX_LIMIT = 10_000
_SQLITE_SIGNED_INT_MAX = 2**63 - 1
_CONTROL_TRACE_COLUMNS = (
    "ts_ms",
    "session_id",
    "cook_id",
    "controller",
    "event_kind",
    "schema_version",
    "payload",
)
_CONTROL_TRACE_COLUMNS_SQL = ", ".join(_CONTROL_TRACE_COLUMNS)

ControlTraceSqliteRow = tuple[int, str, str | None, str, str, int, str]


def _require_control_trace_identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value.strip()


def _require_control_trace_timestamp(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _SQLITE_SIGNED_INT_MAX:
        raise ValueError(f"{name} must be an integer from 0 through {_SQLITE_SIGNED_INT_MAX}")
    return value


def _require_control_trace_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= CONTROL_TRACE_MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 through {CONTROL_TRACE_MAX_LIMIT}")
    return limit


def _validated_control_trace_rows(records: Sequence[ControlTraceRecord]) -> list[ControlTraceDbRow]:
    if not isinstance(records, Sequence):
        raise TypeError("records must be a sequence of ControlTraceRecord values")

    rows = []
    for record in records:
        if not isinstance(record, ControlTraceRecord):
            raise TypeError("records must contain only ControlTraceRecord values")
        # Revalidate models built through model_construct() before opening the transaction.
        validated_record = ControlTraceRecord.model_validate_json(record.model_dump_json())
        _require_control_trace_timestamp(validated_record.ts_ms, "ts_ms")
        rows.append(validated_record.to_db_row())
    return rows


def _control_trace_records(rows: Sequence[ControlTraceSqliteRow]) -> list[ControlTraceRecord]:
    return [
        ControlTraceRecord.from_db_row(
            ControlTraceDbRow(
                ts_ms=row[0],
                session_id=row[1],
                cook_id=row[2],
                controller=row[3],
                event_kind=row[4],
                schema_version=row[5],
                payload=row[6],
            )
        )
        for row in rows
    ]


@contextmanager
def _control_trace_connection(database_path: str | os.PathLike[str] | None) -> Iterator[sqlite3.Connection]:
    """Yield the normal datastore connection or one explicit read-only database."""
    if database_path is None:
        yield datastore.connection()
        return

    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"control trace database does not exist: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def _read_control_trace_records(
    where_column: str,
    identifier: str,
    database_path: str | os.PathLike[str] | None,
) -> list[ControlTraceRecord]:
    with _control_trace_connection(database_path) as connection:
        rows = connection.execute(
            f"SELECT {_CONTROL_TRACE_COLUMNS_SQL} FROM control_trace "
            f"WHERE {where_column}=? AND schema_version BETWEEN ? AND ? ORDER BY id",
            (
                identifier,
                COMPATIBLE_TRACE_SCHEMA_VERSIONS[0],
                COMPATIBLE_TRACE_SCHEMA_VERSIONS[-1],
            ),
        ).fetchall()
    return _control_trace_records(rows)


def append_control_trace(records: Sequence[ControlTraceRecord]) -> None:
    """Persist a validated trace batch in one ordered SQLite transaction."""
    rows = _validated_control_trace_rows(records)
    if not rows:
        return

    placeholders = ", ".join("?" for _ in _CONTROL_TRACE_COLUMNS)
    values = [
        (
            row.ts_ms,
            row.session_id,
            row.cook_id,
            row.controller,
            row.event_kind,
            row.schema_version,
            row.payload,
        )
        for row in rows
    ]
    with datastore.transaction() as connection:
        connection.executemany(
            f"INSERT INTO control_trace ({_CONTROL_TRACE_COLUMNS_SQL}) VALUES ({placeholders})",
            values,
        )


def read_control_trace_session(
    session_id: str, *, database_path: str | os.PathLike[str] | None = None
) -> list[ControlTraceRecord]:
    """Return one session's typed trace records in insertion order."""
    return _read_control_trace_records(
        "session_id",
        _require_control_trace_identifier(session_id, "session_id"),
        database_path,
    )


def read_control_trace_cook(
    cook_id: str, *, database_path: str | os.PathLike[str] | None = None
) -> list[ControlTraceRecord]:
    """Return one cook's typed trace records in insertion order."""
    return _read_control_trace_records(
        "cook_id",
        _require_control_trace_identifier(cook_id, "cook_id"),
        database_path,
    )


def read_control_trace_range(start_ms: int, end_ms: int, *, limit: int) -> list[ControlTraceRecord]:
    """Return records in the inclusive timestamp range, ordered by insertion."""
    start_ms = _require_control_trace_timestamp(start_ms, "start_ms")
    end_ms = _require_control_trace_timestamp(end_ms, "end_ms")
    if start_ms > end_ms:
        raise ValueError("start_ms must not exceed end_ms")
    limit = _require_control_trace_limit(limit)
    rows = (
        datastore.connection()
        .execute(
            f"SELECT {_CONTROL_TRACE_COLUMNS_SQL} FROM control_trace "
            "WHERE ts_ms >= ? AND ts_ms <= ? AND schema_version BETWEEN ? AND ? ORDER BY id LIMIT ?",
            (
                start_ms,
                end_ms,
                COMPATIBLE_TRACE_SCHEMA_VERSIONS[0],
                COMPATIBLE_TRACE_SCHEMA_VERSIONS[-1],
                limit,
            ),
        )
        .fetchall()
    )
    return _control_trace_records(rows)


def prune_control_trace(before_ms: int, *, limit: int) -> int:
    """Delete at most ``limit`` current/older-schema rows strictly before a timestamp."""
    before_ms = _require_control_trace_timestamp(before_ms, "before_ms")
    limit = _require_control_trace_limit(limit)
    with datastore.transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM control_trace WHERE id IN ("
            "SELECT id FROM control_trace WHERE ts_ms < ? AND schema_version <= ? ORDER BY id LIMIT ?"
            ")",
            (before_ms, TRACE_SCHEMA_VERSION, limit),
        )
    return cursor.rowcount


def prune_incompatible_control_trace(before_schema_version: int, *, limit: int) -> int:
    """Delete at most ``limit`` older rows outside the compatible schema range."""
    if isinstance(before_schema_version, bool) or not isinstance(before_schema_version, int):
        raise ValueError("before_schema_version must be an integer")
    limit = _require_control_trace_limit(limit)
    with datastore.transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM control_trace WHERE id IN ("
            "SELECT id FROM control_trace "
            "WHERE schema_version < ? AND schema_version NOT BETWEEN ? AND ? ORDER BY id LIMIT ?"
            ")",
            (
                before_schema_version,
                COMPATIBLE_TRACE_SCHEMA_VERSIONS[0],
                COMPATIBLE_TRACE_SCHEMA_VERSIONS[-1],
                limit,
            ),
        )
    return cursor.rowcount


def delete_control_trace_session(session_id: str) -> int:
    """Delete all trace rows for one session and return the deletion count."""
    session_id = _require_control_trace_identifier(session_id, "session_id")
    with datastore.transaction() as connection:
        cursor = connection.execute("DELETE FROM control_trace WHERE session_id=?", (session_id,))
    return cursor.rowcount
