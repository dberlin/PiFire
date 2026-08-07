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

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time
from common import datastore
from common.common import (
    ErrorKind,
    WriteKind,
    generate_uuid,
    strip_null_members,
)
from common.control_delta import (
    ControlDeltaError,
    apply_control_delta,
    is_control_delta,
    validate_control_delta,
)
from common.current_schema import (
    build_current,
    dump_legacy,
    load_current,
    snapshot_from,
    zeroed_current,
)
from common.defaults import (
    METRIC_COLUMNS,
    default_control,
    default_metrics,
    default_pellets,
    default_settings,
)
from common.pellets_schema import validate_pellet_db
from common.settings_schema import validate_settings_tree
from common.sqlite_queue import SqliteMembershipList, SqliteQueue
from common.control_trace import TRACE_SCHEMA_VERSION, ControlTraceDbRow, ControlTraceRecord
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ActivationEvidence,
    EvidenceKind,
    ModelEvidenceDbRow,
    ModelEvidenceRecord,
)


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

    :param control: for OVERWRITE/MERGE, a control dictionary or partial; for
                    DELTA, an envelope from common.control_delta.control_delta().
    :param kind: WriteKind.OVERWRITE writes control:general directly.
                 WriteKind.MERGE queues a partial change for deep-merge on execute.
                 WriteKind.DELTA queues a validated intent envelope.
    :param origin: Source label recorded on queued writes.
    """
    if kind is WriteKind.OVERWRITE:
        _write_json_blob("control:general", control)
    elif kind is WriteKind.MERGE:
        control["origin"] = origin
        SqliteQueue("queue_control_write").push(control)
    elif kind is WriteKind.DELTA:
        # Validate HERE, in the writing process: a malformed envelope caught at
        # drain time surfaces as a control-loop log line in a different process.
        # Note the asymmetry with MERGE, and keep it: MERGE stamps `origin` into
        # the caller's dict (observed behaviour the golden fixture records); a
        # delta envelope is an immutable value, so it is copied first.
        validate_control_delta(control)
        payload = dict(control)
        payload["origin"] = origin
        SqliteQueue("queue_control_write").push(payload)
    else:
        raise TypeError(f"write_control: kind must be WriteKind, got {kind!r}")


def read_pending_control_writes():
    """Return an immutable snapshot of queued control deltas in FIFO order."""
    return tuple(SqliteQueue("queue_control_write").list())


def _mpc_calibration_command_from_delta(delta):
    if not isinstance(delta, dict):
        return None
    for op in delta.get("ops", ()):
        if not isinstance(op, dict) or op.get("op") != "mpc_calibration.set":
            continue
        command = op.get("command")
        if isinstance(command, dict):
            return command
    return None


def mpc_calibration_command_state(control=None, pending_writes=None):
    """Return the first accepted command at the highest live-or-queued revision."""
    if control is None and pending_writes is None:
        with datastore.transaction() as connection:
            row = connection.execute("SELECT value FROM kv WHERE key = 'control:general'").fetchone()
            control = json.loads(row[0]) if row is not None else default_control()
            pending_writes = tuple(
                json.loads(queued[0])
                for queued in connection.execute("SELECT value FROM queue_control_write ORDER BY id").fetchall()
            )
    else:
        if control is None:
            control = read_control()
        if pending_writes is None:
            pending_writes = read_pending_control_writes()
    current = control.get("mpc_calibration") if isinstance(control, dict) else None
    accepted = current if isinstance(current, dict) else None
    for delta in pending_writes:
        command = _mpc_calibration_command_from_delta(delta)
        if command is None:
            continue
        revision = command.get("revision")
        accepted_value = accepted.get("revision") if accepted is not None else -1
        accepted_revision = (
            accepted_value if isinstance(accepted_value, int) and not isinstance(accepted_value, bool) else -1
        )
        if isinstance(revision, int) and not isinstance(revision, bool) and revision > accepted_revision:
            accepted = command
    return accepted


def mpc_calibration_command_revision(control=None, pending_writes=None):
    """Return the accepted calibration revision high-water mark."""
    command = mpc_calibration_command_state(control, pending_writes)
    if command is None:
        return 0
    revision = command.get("revision")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else 0


def queue_mpc_calibration_command(delta, command, origin):
    """Atomically admit one revisioned calibration delta to the control FIFO."""
    validate_control_delta(delta)
    payload = dict(delta)
    payload["origin"] = origin
    with datastore.transaction() as connection:
        row = connection.execute("SELECT value FROM kv WHERE key = 'control:general'").fetchone()
        control = json.loads(row[0]) if row is not None else default_control()
        pending = tuple(
            json.loads(queued[0])
            for queued in connection.execute("SELECT value FROM queue_control_write ORDER BY id").fetchall()
        )
        accepted = mpc_calibration_command_state(control, pending)
        accepted_revision = accepted.get("revision") if accepted is not None else -1
        if command["revision"] < accepted_revision or (
            command["revision"] == accepted_revision and command != accepted
        ):
            raise ControlDeltaError(f"MPC calibration revision must exceed {accepted_revision}")
        if command == accepted:
            return False
        connection.execute(
            "INSERT INTO queue_control_write(value) VALUES(?)",
            (json.dumps(payload),),
        )
    return True


def execute_control_writes():
    """
    Execute Control Writes in Queue from SQLite DB.

    Every PiFire writer now queues a DELTA envelope
    (common/control_delta.py): a statement of what it MEANT rather than the
    whole control snapshot it happened to read. Nothing is inferred, so nothing
    is reduced -- the envelope is applied directly to the live blob, and ops
    inside it are evaluated against whatever earlier writes in this same batch
    already left.

    WriteKind.MERGE survives as the raw primitive: a partial, null-stripped and
    deep-merged via SQLite's json_patch(). Nulls are stripped because json_patch
    implements RFC 7386, where a null MEMBER deletes the key, and the merge
    contract only ever adds or overwrites. It has no production call site any
    more (pinned by tests/characterization/test_control_delta_seam.py::
    test_no_production_writer_still_queues_a_whole_control_dict), and what it
    does NOT have any more is the three-way merge that used to sit on top of it:
    reduce_control_patch and merge_notify_data existed to reconstruct a writer's
    intent by diffing its stale snapshot against a common ancestor. A delta
    carries that intent outright, so there is nothing left to reconstruct. The
    primitive itself is pinned by tests/oracle/fixtures/control_merge.json.

    A queued payload carrying ``__control_delta__`` is a DELTA envelope
    (common/control_delta.py): the writer stated what it meant, so it is applied
    directly and never reduced. Everything else is a legacy whole-dict partial and
    takes the three-way-merge path below, unchanged, for as long as any writer
    still sends one. ``base`` stays the pre-drain ancestor for those patches even
    when a delta has landed in between -- it is what THEY read.

    :param None

    :return status : 'OK', 'ERROR'
    """
    while True:
        with datastore.transaction() as conn:
            row = conn.execute("SELECT id, value FROM queue_control_write ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return "OK"
            command = json.loads(row[1])
            control_row = conn.execute("SELECT value FROM kv WHERE key = 'control:general'").fetchone()
            if control_row is None:
                control = default_control()
                conn.execute(
                    "INSERT INTO kv(key, value) VALUES ('control:general', ?)",
                    (json.dumps(control),),
                )
            else:
                control = json.loads(control_row[0])
            if is_control_delta(command):
                # Admission, dequeue, and live-state replacement share this
                # write transaction. A revision checker can therefore observe
                # either the queued command or its applied live value, never
                # the gap between two independent commits.
                apply_control_delta(control, command)
                conn.execute(
                    "UPDATE kv SET value=? WHERE key='control:general'",
                    (json.dumps(control),),
                )
            else:
                origin = command.pop("origin", None)
                stripped = []
                patch = strip_null_members(command, stripped)
                if stripped:
                    logging.getLogger("control").error(
                        "execute_control_writes: stripped null member(s) %s from MERGE partial "
                        "(origin=%r); json_patch would delete these keys. Fix the source to stop "
                        "sending nulls.",
                        stripped,
                        origin,
                    )
                if patch:
                    conn.execute(
                        "UPDATE kv SET value = json_patch(value, ?) WHERE key = 'control:general'",
                        (json.dumps(patch),),
                    )
            conn.execute("DELETE FROM queue_control_write WHERE id=?", (row[0],))


def _writable_error_kind(kind):
    """Resolve ``kind`` to the stored string, rejecting the read-only selector.

    ``ALL`` spans every owner, so writing or flushing it would let one process
    discard another's banners -- the exact failure the kind column exists to
    prevent. Bare strings are rejected too, so a typo is an error rather than a
    silently-unreadable kind.
    """
    if not isinstance(kind, ErrorKind):
        raise ValueError(f"error kind must be an ErrorKind, got {kind!r}")
    if kind is ErrorKind.ALL:
        raise ValueError(f"{kind} is a read-only selector; write and flush need a single owning kind")
    return kind.value


def read_errors(kind):
    """
    Read stored error banners from SQLite DB.

    :param kind: An :class:`ErrorKind`. ``ALL`` returns every kind's messages
        grouped by kind in ErrorKind declaration order, ordered by id within
        each group. Grouping rather than a global id sort keeps the dashboard
        order stable: :func:`write_errors` replaces a kind's rows, so a global
        sort would jump a whole process's banners to the end of the strip every
        time that process restarted.
    :return: errors
    """
    if not isinstance(kind, ErrorKind):
        raise ValueError(f"error kind must be an ErrorKind, got {kind!r}")
    if kind is ErrorKind.ALL:
        owners = [k.value for k in ErrorKind if k is not ErrorKind.ALL]
        placeholders = ",".join("?" for _ in owners)
        rank = " ".join(f"WHEN ? THEN {i}" for i, _ in enumerate(owners))
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
    """
    Clear one kind's stored error list.

    Returns ``[]`` -- the *new* state, not the discarded contents. This is
    deliberately not a read-and-clear: each boot-path caller wants a cleared
    store plus a fresh accumulator to hand to its builder. Returning the
    pre-flush errors would change what the builder accumulates into,
    resurrecting errors from the previous run.

    Previously reachable only as ``read_errors(flush=True)`` -- see
    :func:`flush_history` for why that spelling was a problem.

    :param kind: An :class:`ErrorKind` other than ``ALL``.
    :return: An empty error list.
    """
    write_errors(kind, [])
    return []


def write_errors(kind, errors):
    """
    Replace one kind's error list in SQLite DB.

    The delete and the inserts share one transaction, so a concurrent reader
    sees either the old list or the new one and never a half-written strip.
    Rows of other kinds are untouched.

    :param kind: An :class:`ErrorKind` other than ``ALL``.
    :param errors: Errors
    """
    stored_kind = _writable_error_kind(kind)
    with datastore.transaction() as conn:
        conn.execute("DELETE FROM errors WHERE kind = ?", (stored_kind,))
        conn.executemany("INSERT INTO errors (kind, message) VALUES (?, ?)", [(stored_kind, e) for e in errors])


def read_warnings_snapshot():
    """
    Read the outstanding warnings together with their high-water mark id.

    One query, so ``max_id`` always belongs to the last string in ``warnings``
    -- a caller that clears through it clears exactly what it was handed. Two
    separate reads could not promise that.

    Non-destructive, matching :func:`read_errors`. Consumed by the Socket.IO
    feed (``blueprints/mobile/socket_io.py``), which packs it into the
    ``socket_dash_data`` payload for the React warning banners.

    :return: {"warnings": [str], "max_id": int | None} -- max_id is None when
        there are no outstanding warnings.
    """
    rows = SqliteQueue("list_warnings", raw=True).list_with_ids()
    return {"warnings": [v for _, v in rows], "max_id": rows[-1][0] if rows else None}


def clear_warnings_through(max_id):
    """
    Clear the warnings up to and including ``max_id``.

    The dismiss primitive: a user clears the banner they were shown, identified
    by the high-water mark that came with it. A warning written after that
    snapshot has a larger id and survives, so it is never discarded unread --
    which is why this is bounded rather than a flush.

    :param max_id: High-water mark from :func:`read_warnings_snapshot`.
    """
    SqliteQueue("list_warnings", raw=True).clear_through(max_id)


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
        # Revalidate even a Pydantic model constructed through model_construct()
        # before opening the write transaction.
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
            f"WHERE {where_column}=? AND schema_version=? ORDER BY id",
            (identifier, TRACE_SCHEMA_VERSION),
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
    with datastore.transaction() as conn:
        conn.executemany(f"INSERT INTO control_trace ({_CONTROL_TRACE_COLUMNS_SQL}) VALUES ({placeholders})", values)


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
    """Return at most ``limit`` typed records whose timestamps are in the inclusive range."""
    start_ms = _require_control_trace_timestamp(start_ms, "start_ms")
    end_ms = _require_control_trace_timestamp(end_ms, "end_ms")
    if start_ms > end_ms:
        raise ValueError("start_ms must not exceed end_ms")
    limit = _require_control_trace_limit(limit)
    rows = (
        datastore.connection()
        .execute(
            f"SELECT {_CONTROL_TRACE_COLUMNS_SQL} FROM control_trace "
            "WHERE ts_ms >= ? AND ts_ms <= ? AND schema_version=? ORDER BY id LIMIT ?",
            (start_ms, end_ms, TRACE_SCHEMA_VERSION, limit),
        )
        .fetchall()
    )
    return _control_trace_records(rows)


def prune_control_trace(before_ms: int, *, limit: int) -> int:
    """Delete at most ``limit`` rows whose timestamp is strictly before ``before_ms``."""
    before_ms = _require_control_trace_timestamp(before_ms, "before_ms")
    limit = _require_control_trace_limit(limit)
    with datastore.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM control_trace WHERE id IN ("
            "SELECT id FROM control_trace WHERE ts_ms < ? AND schema_version <= ? ORDER BY id LIMIT ?"
            ")",
            (before_ms, TRACE_SCHEMA_VERSION, limit),
        )
    return cursor.rowcount


def prune_incompatible_control_trace(before_schema_version: int, *, limit: int) -> int:
    """Delete at most ``limit`` rows older than the supplied trace schema."""
    if isinstance(before_schema_version, bool) or not isinstance(before_schema_version, int):
        raise ValueError("before_schema_version must be an integer")
    limit = _require_control_trace_limit(limit)
    with datastore.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM control_trace WHERE id IN ("
            "SELECT id FROM control_trace WHERE schema_version < ? ORDER BY id LIMIT ?"
            ")",
            (before_schema_version, limit),
        )
    return cursor.rowcount


def delete_control_trace_session(session_id: str) -> int:
    """Delete all trace rows for one session and return the deletion count."""
    session_id = _require_control_trace_identifier(session_id, "session_id")
    with datastore.transaction() as conn:
        cursor = conn.execute("DELETE FROM control_trace WHERE session_id=?", (session_id,))
    return cursor.rowcount


@dataclass(frozen=True, slots=True)
class ModelActivationState:
    """The one active/rollback pair selected by an activation decision."""

    active_snapshot_json: str
    rollback_snapshot_json: str
    evidence_decision_id: str
    controller_configuration_digest: str
    role_generation: int


@contextmanager
def _model_evidence_connection(database_path: str | os.PathLike[str] | None) -> Iterator[sqlite3.Connection]:
    """Yield the normal store or an explicitly selected ledger database."""
    if database_path is None:
        yield datastore.connection()
        return
    connection = sqlite3.connect(os.fspath(database_path), timeout=30)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.isolation_level = None
    try:
        datastore._ensure_schema(connection)
        yield connection
    finally:
        connection.close()


def _validated_model_evidence_rows(records: Sequence[ModelEvidenceRecord]) -> list[ModelEvidenceDbRow]:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise TypeError("records must be a sequence of ModelEvidenceRecord values")
    rows = []
    for record in records:
        if not isinstance(record, ModelEvidenceRecord):
            raise TypeError("records must contain only ModelEvidenceRecord values")
        # model_construct() bypasses Pydantic; persist only a full validated copy.
        validated = ModelEvidenceRecord.model_validate_json(record.model_dump_json())
        rows.append(validated.to_db_row())
    return rows


def append_model_evidence(
    records: Sequence[ModelEvidenceRecord], *, database_path: str | os.PathLike[str] | None = None
) -> None:
    """Append validated compact evidence in caller order; identities are immutable."""
    rows = _validated_model_evidence_rows(records)
    if not rows:
        return
    values = [
        (
            row.evidence_id,
            row.session_id,
            row.cook_id,
            row.timestamp_ms,
            row.kind,
            row.role_generation,
            row.model_digest,
            row.provenance_digest,
            row.schema_version,
            row.payload,
        )
        for row in rows
    ]
    with _model_evidence_connection(database_path) as connection:
        with datastore.transaction(connection) as conn:
            conn.executemany(
                """
                INSERT INTO model_evidence(
                    evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
                    model_digest, provenance_digest, schema_version, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )


def read_model_evidence(
    *,
    session_id: str | None = None,
    cook_id: str | None = None,
    kind: EvidenceKind | str | None = None,
    database_path: str | os.PathLike[str] | None = None,
) -> list[ModelEvidenceRecord]:
    """Return compatible compact evidence in deterministic append order."""
    clauses = ["schema_version IN (?, ?)"]
    params: list[object] = [1, MODEL_EVIDENCE_SCHEMA_VERSION]
    if session_id is not None:
        clauses.append("session_id=?")
        params.append(_require_control_trace_identifier(session_id, "session_id"))
    if cook_id is not None:
        clauses.append("cook_id=?")
        params.append(_require_control_trace_identifier(cook_id, "cook_id"))
    if kind is not None:
        try:
            stored_kind = EvidenceKind(kind).value
        except ValueError as exc:
            raise ValueError(f"unknown evidence kind: {kind!r}") from exc
        clauses.append("kind=?")
        params.append(stored_kind)
    with _model_evidence_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation, "
            "model_digest, provenance_digest, schema_version, payload FROM model_evidence "
            f"WHERE {' AND '.join(clauses)} ORDER BY id",
            params,
        ).fetchall()
    return [ModelEvidenceRecord.from_db_row(ModelEvidenceDbRow(*row)) for row in rows]


def commit_model_activation(
    decision: ModelEvidenceRecord, *, database_path: str | os.PathLike[str] | None = None
) -> None:
    """Atomically append an activation decision and replace its singleton state."""
    rows = _validated_model_evidence_rows([decision])
    validated = ModelEvidenceRecord.model_validate_json(decision.model_dump_json())
    if validated.kind is not EvidenceKind.ACTIVATION or not isinstance(validated.payload, ActivationEvidence):
        raise ValueError("activation commit requires activation evidence")
    row = rows[0]
    payload = validated.payload
    with _model_evidence_connection(database_path) as connection:
        with datastore.transaction(connection) as conn:
            conn.execute(
                """
                INSERT INTO model_evidence(
                    evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
                    model_digest, provenance_digest, schema_version, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.evidence_id,
                    row.session_id,
                    row.cook_id,
                    row.timestamp_ms,
                    row.kind,
                    row.role_generation,
                    row.model_digest,
                    row.provenance_digest,
                    row.schema_version,
                    row.payload,
                ),
            )
            conn.execute("DELETE FROM model_activation_state WHERE singleton=1")
            conn.execute(
                """
                INSERT INTO model_activation_state(
                    singleton, active_snapshot_json, rollback_snapshot_json, evidence_decision_id,
                    controller_configuration_digest, role_generation
                ) VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    payload.active_snapshot_json,
                    payload.rollback_snapshot_json,
                    payload.decision_id,
                    payload.controller_configuration_digest,
                    validated.role_generation,
                ),
            )


def read_model_activation(*, database_path: str | os.PathLike[str] | None = None) -> ModelActivationState | None:
    """Return the exact active snapshot state, if an activation has committed."""
    with _model_evidence_connection(database_path) as connection:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_activation_state'"
            ).fetchone()
            is None
        ):
            return None
        row = connection.execute(
            """
            SELECT active_snapshot_json, rollback_snapshot_json, evidence_decision_id,
                   controller_configuration_digest, role_generation
            FROM model_activation_state WHERE singleton=1
            """
        ).fetchone()
    return None if row is None else ModelActivationState(*row)


def reset_model_evidence(*, database_path: str | os.PathLike[str] | None = None) -> None:
    """Explicitly delete every durable evidence row and activation state."""
    with _model_evidence_connection(database_path) as connection:
        with datastore.transaction(connection) as conn:
            conn.execute("DELETE FROM model_activation_state")
            conn.execute("DELETE FROM model_evidence")


def invalidate_model_evidence_schema(*, database_path: str | os.PathLike[str] | None = None) -> None:
    """Invalidate current evidence explicitly; raw trace retention never calls this."""
    reset_model_evidence(database_path=database_path)


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
    #
    # A pure read: migrating a legacy tree is datastore.init()'s job, run once
    # by every entry point at startup (see
    # tests/unit/datastore/test_entry_points_initialise_the_datastore.py).
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

    Strict-validates first, exactly as write_settings() does: a rejected write
    leaves the store untouched, and the normalized dump the gate returns -- not
    the caller's raw dict -- is what gets persisted. No bypass parameter.

    :param pelletdb: Pellet Database
    """
    write_pellets_store(validate_pellet_db(pelletdb))


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
    previous = load_current(_read_json_blob("control:current", dict))
    schema = build_current(in_data, previous, int(time.time() * 1000))
    _write_json_blob("control:current", dump_legacy(schema))


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
    schema = zeroed_current(settings["probe_settings"]["probe_map"]["probe_info"])
    # TS is dropped: a flushed structure holds no readings, so there is no time
    # at which they were taken.
    _write_json_blob("control:current", dump_legacy(schema, exclude_timestamp=True))

    return _read_json_blob("control:current", dict)


def read_current():
    """
    Read current.log and populate a list of data

    :return: Current probe temps structure
    """
    return _read_json_blob("control:current", dict)


def read_current_snapshot():
    """
    Read the current probe temps as a validated snapshot.

    An unparseable blob is discarded rather than repaired -- it is a cache of
    the last control pass, and the next pass refills it -- and rebuilt from the
    configured probe map, same as :func:`flush_current`. An absent/empty blob
    (a fresh install) validates cleanly instead, and yields a snapshot with
    every section empty rather than zeroed from the probe map.

    :return: CurrentSnapshot
    """
    return snapshot_from(
        _read_json_blob("control:current", dict),
        lambda: read_settings()["probe_settings"]["probe_map"]["probe_info"],
    )


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
    """Cache the OS/architecture probe (see common.system.refresh_os_info).

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


def delete_wizard_install_info():
    """
    Remove Wizard Install Info from SQLite DB

    :return:
    """
    datastore.delete_blob("wizard:install")


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
    """
    Write generic data to SQLite DB
    :param key: key name
    :parma value: value to write
    """
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
    with datastore.transaction() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", ("controller_model_state",)).fetchone()
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
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("controller_model_state", encoded_state),
        )
    return True
