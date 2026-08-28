"""Transactional SQLite repository for bounded cumulative-learning trajectories."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from threading import local
from typing import Literal, Protocol, cast

from common import datastore
from common.learning_trajectory import (
    TRAJECTORY_OBSERVATION_SCHEMA_VERSION,
    FitCorpusIdentity,
    FitCorpusSlice,
    FrameDeliveryCertainty,
    HoldEntrySample,
    LearningTrajectoryFrame,
    LearningTrajectorySegment,
    ModelFitLineage,
    TrajectoryBreakReason,
    canonical_trajectory_digest,
    trajectory_json_value,
)

_MAX_SCORED_ROWS = 8_640
_MAX_PRE_ROLL_ROWS = 8_640
_MAX_PRE_ROLL_PER_SEGMENT = 180
_MAX_SEGMENTS = 256
_MAX_SCORED_PER_SEGMENT = 180
_ZERO_CHAIN_DIGEST = "0" * 64
_CORPUS_SCHEMA_VERSION = 1


class LearningTrajectoryConflictError(RuntimeError):
    """Stored identity or ordinal is already bound to different bytes."""


class StaleSegmentCursorError(RuntimeError):
    """A segment mutation lost its optimistic cursor comparison."""


class FitCorpusEvictedError(RuntimeError):
    """A recorded fit manifest no longer has all of its source frames."""

    code = "corpus-evicted"

    def __init__(self, request_id: str):
        super().__init__(f"corpus-evicted: fit request {request_id}")
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class SegmentCursor:
    segment_id: str
    next_ordinal: int
    chain_digest: str
    corpus_revision: int


@dataclass(frozen=True, slots=True)
class AppendReceipt:
    cursor: SegmentCursor
    inserted_pre_roll_count: int
    inserted_scored_count: int
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class FinalizeReceipt:
    segment_id: str
    corpus_revision: int
    content_digest: str
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    finalized_segment_ids: tuple[str, ...]
    quarantined_segment_ids: tuple[str, ...]
    interrupted_fit_request_ids: tuple[str, ...]
    corpus_revision: int


@dataclass(frozen=True, slots=True)
class CorpusStatus:
    corpus_revision: int
    segment_count: int
    pre_roll_count: int
    scored_count: int
    evicted_segment_count: int
    evicted_pre_roll_count: int
    evicted_scored_count: int
    quarantined_segment_count: int


@dataclass(frozen=True, slots=True)
class FitCorpusSnapshot:
    identity: FitCorpusIdentity
    segments: tuple[LearningTrajectorySegment, ...]


FitRunStatus = Literal[
    "queued", "running", "succeeded", "failed", "interrupted", "stale"
]


@dataclass(frozen=True, slots=True)
class FitRun:
    request_id: str
    status: FitRunStatus
    fit_partition_digest: str
    corpus_revision: int
    corpus_digest: str
    parent_incumbent_digest: str
    parent_incumbent_generation: int
    candidate_generation: int
    trigger_origin: str
    candidate_digest: str | None
    error: str | None
    created_ms: int
    started_ms: int | None
    completed_ms: int | None


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _canonical_json(value: object) -> str:
    return json.dumps(
        trajectory_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _frame_payload(frame: LearningTrajectoryFrame) -> dict[str, object]:
    return {
        "sequence": frame.sequence,
        "monotonic_start_ms": frame.monotonic_start_ms,
        "monotonic_end_ms": frame.monotonic_end_ms,
        "wall_start_ms": frame.wall_start_ms,
        "wall_end_ms": frame.wall_end_ms,
        "chamber_temperature_c": frame.chamber_temperature_c,
        "temperature_sample_monotonic_ms": frame.temperature_sample_monotonic_ms,
        "temperature_sample_wall_ms": frame.temperature_sample_wall_ms,
        "temperature_sample_age_ms": frame.temperature_sample_age_ms,
        "temperature_sample_wall_age_ms": frame.temperature_sample_wall_age_ms,
        "temperature_sample_clock_skew_ms": frame.temperature_sample_clock_skew_ms,
        "source_temperature_units": frame.source_temperature_units,
        "settings_revision": frame.settings_revision,
        "probe_valid": frame.probe_valid,
        "probe_source": frame.probe_source,
        "ambient_temperature_c": frame.ambient_temperature_c,
        "ambient_source": frame.ambient_source,
        "ambient_uncertainty_c": frame.ambient_uncertainty_c,
        "delivered_auger_on_seconds": frame.delivered_auger_on_seconds,
        "realized_auger_duty": frame.realized_auger_duty,
        "normalized_combustion_load": frame.normalized_combustion_load,
        "delivered_fan_on_seconds": frame.delivered_fan_on_seconds,
        "fan_duty_integral_seconds": frame.fan_duty_integral_seconds,
        "mean_actual_fan_duty": frame.mean_actual_fan_duty,
        "auger_delivery_certainty": frame.auger_delivery_certainty.value,
        "fan_delivery_certainty": frame.fan_delivery_certainty.value,
        "effective_mode": frame.effective_mode,
        "recipe_step_id": frame.recipe_step_id,
        "complete": frame.complete,
        "continuous": frame.continuous,
        "partial": frame.partial,
        "boundary_reason": (
            frame.boundary_reason.value if frame.boundary_reason is not None else None
        ),
    }


def _frame_from_json(canonical_json: str) -> LearningTrajectoryFrame:
    payload = json.loads(canonical_json)
    payload["auger_delivery_certainty"] = FrameDeliveryCertainty(
        payload["auger_delivery_certainty"]
    )
    payload["fan_delivery_certainty"] = FrameDeliveryCertainty(
        payload["fan_delivery_certainty"]
    )
    if payload["boundary_reason"] is not None:
        payload["boundary_reason"] = TrajectoryBreakReason(payload["boundary_reason"])
    return LearningTrajectoryFrame(**payload)


def _hold_payload(sample: HoldEntrySample) -> dict[str, object]:
    return {
        "monotonic_ms": sample.monotonic_ms,
        "wall_ms": sample.wall_ms,
        "chamber_temperature_c": sample.chamber_temperature_c,
        "probe_valid": sample.probe_valid,
        "probe_source": sample.probe_source,
    }


def _hold_from_json(value: str | None) -> HoldEntrySample | None:
    if value is None:
        return None
    return HoldEntrySample(**json.loads(value))


def _segment_header(segment: LearningTrajectorySegment) -> dict[str, object]:
    return {
        "schema_version": segment.schema_version,
        "observation_schema_version": segment.observation_schema_version,
        "cook_id": segment.cook_id,
        "trajectory_session_id": segment.trajectory_session_id,
        "trace_session_ids": list(segment.trace_session_ids),
        "collection_provenance": trajectory_json_value(segment.collection_provenance),
        "configuration_provenance": trajectory_json_value(
            segment.configuration_provenance
        ),
        "cadence_digest": segment.cadence_digest,
        "model_structure_digest": segment.model_structure_digest,
        "held_physics_digest": segment.held_physics_digest,
        "delay_input_mapping_digest": segment.delay_input_mapping_digest,
        "actuation_mapping_digest": segment.actuation_mapping_digest,
        "scored_fan_regime_digest": segment.scored_fan_regime_digest,
        "ambient_semantics_digest": segment.ambient_semantics_digest,
        "generation_audit_ranges": [
            trajectory_json_value(item) for item in segment.generation_audit_ranges
        ],
        "source_schema_version": segment.source_schema_version,
        "build_provenance": trajectory_json_value(segment.build_provenance),
    }


def _interval_identity(frame: LearningTrajectoryFrame) -> str:
    return canonical_trajectory_digest(
        {
            "sequence": frame.sequence,
            "monotonic_start_ms": frame.monotonic_start_ms,
            "monotonic_end_ms": frame.monotonic_end_ms,
            "wall_start_ms": frame.wall_start_ms,
            "wall_end_ms": frame.wall_end_ms,
        }
    )


def _next_chain_digest(previous: str, canonical_frame: str) -> str:
    return sha256(
        bytes.fromhex(previous) + canonical_frame.encode()
    ).hexdigest()


def _frames_chain(frames: tuple[LearningTrajectoryFrame, ...]) -> str:
    digest = _ZERO_CHAIN_DIGEST
    for frame in frames:
        digest = _next_chain_digest(digest, _canonical_json(_frame_payload(frame)))
    return digest


def _with_frames(
    segment: LearningTrajectorySegment,
    *,
    pre_roll: tuple[LearningTrajectoryFrame, ...],
    hold_entry: HoldEntrySample | None,
    scored: tuple[LearningTrajectoryFrame, ...],
    state: Literal["open", "finalized", "quarantined"] | None = None,
    terminal_reason: TrajectoryBreakReason | None = None,
) -> LearningTrajectorySegment:
    all_frames = (*pre_roll, *scored)
    if not all_frames:
        raise ValueError("trajectory segment requires at least one frame")
    return replace(
        segment,
        pre_roll_frames=pre_roll,
        hold_entry=hold_entry,
        scored_hold_frames=scored,
        start_monotonic_ms=all_frames[0].monotonic_start_ms,
        end_monotonic_ms=all_frames[-1].monotonic_end_ms,
        start_wall_ms=all_frames[0].wall_start_ms,
        end_wall_ms=all_frames[-1].wall_end_ms,
        start_sequence=all_frames[0].sequence,
        end_sequence=all_frames[-1].sequence,
        pre_roll_end_reason=(
            pre_roll[-1].boundary_reason
            if pre_roll and pre_roll[-1].partial
            else segment.pre_roll_end_reason
        ),
        state=segment.state if state is None else state,
        terminal_break_reason=terminal_reason,
    )


def _rolled_segment(
    source: LearningTrajectorySegment,
    segment_id: str,
    carried: tuple[LearningTrajectoryFrame, ...],
) -> LearningTrajectorySegment:
    first = carried[0]
    last = carried[-1]
    return replace(
        source,
        segment_id=segment_id,
        trajectory_session_id=f"{source.trajectory_session_id}:roll:{segment_id}",
        pre_roll_frames=carried,
        hold_entry=None,
        scored_hold_frames=(),
        generation_audit_ranges=(
            {
                "start_sequence": first.sequence,
                "end_sequence": last.sequence,
                "role_generation": 0,
            },
        ),
        start_monotonic_ms=first.monotonic_start_ms,
        end_monotonic_ms=last.monotonic_end_ms,
        start_wall_ms=first.wall_start_ms,
        end_wall_ms=last.wall_end_ms,
        start_sequence=first.sequence,
        end_sequence=last.sequence,
        pre_roll_end_reason=TrajectoryBreakReason.RETENTION_ROLLOVER,
        terminal_break_reason=None,
        state="open",
        source_trace_digest=sha256(
            f"{source.source_trace_digest}:{segment_id}".encode()
        ).hexdigest(),
        source_row_digest=sha256(
            f"{source.source_row_digest}:{segment_id}".encode()
        ).hexdigest(),
    )


def _corpus_identity(
    *,
    corpus_revision: int,
    fit_partition_digest: str,
    slices: tuple[FitCorpusSlice, ...],
) -> FitCorpusIdentity:
    payload = {
        "schema_version": _CORPUS_SCHEMA_VERSION,
        "corpus_revision": corpus_revision,
        "fit_partition_digest": fit_partition_digest,
        "slices": [
            {
                "segment_id": item.segment_id,
                "through_ordinal": item.through_ordinal,
                "prefix_digest": item.prefix_digest,
                "pre_roll_count": item.pre_roll_count,
                "scored_count": item.scored_count,
            }
            for item in slices
        ],
    }
    return FitCorpusIdentity(
        schema_version=_CORPUS_SCHEMA_VERSION,
        corpus_revision=corpus_revision,
        fit_partition_digest=fit_partition_digest,
        slices=slices,
        corpus_digest=canonical_trajectory_digest(payload),
    )


class _HashWriter(Protocol):
    def update(self, data: bytes, /) -> None: ...


def _hash_part(hasher: _HashWriter, tag: bytes, data: bytes) -> None:
    hasher.update(len(tag).to_bytes(2, "big"))
    hasher.update(tag)
    hasher.update(len(data).to_bytes(8, "big"))
    hasher.update(data)


def _operation_key(kind: str, cursor: SegmentCursor) -> str:
    hasher = sha256(b"learning-trajectory-operation-key-v1")
    _hash_part(hasher, b"kind", kind.encode())
    _hash_part(hasher, b"segment", cursor.segment_id.encode())
    _hash_part(hasher, b"ordinal", cursor.next_ordinal.to_bytes(8, "big"))
    _hash_part(hasher, b"chain", bytes.fromhex(cursor.chain_digest))
    _hash_part(hasher, b"revision", cursor.corpus_revision.to_bytes(8, "big"))
    return hasher.hexdigest()


def _append_request_digest(
    cursor: SegmentCursor,
    *,
    pre_roll: tuple[LearningTrajectoryFrame, ...],
    hold_entry: HoldEntrySample | None,
    scored: tuple[LearningTrajectoryFrame, ...],
) -> str:
    hasher = sha256(b"learning-trajectory-append-receipt-v1")
    _hash_part(
        hasher,
        b"operation",
        bytes.fromhex(_operation_key("append", cursor)),
    )
    if hold_entry is None:
        _hash_part(hasher, b"hold-none", b"")
    else:
        hold_bytes = _canonical_json(_hold_payload(hold_entry)).encode()
        _hash_part(hasher, b"hold", sha256(hold_bytes).digest())
    for frame in pre_roll:
        canonical = _canonical_json(_frame_payload(frame)).encode()
        _hash_part(hasher, b"pre-roll", sha256(canonical).digest())
    for frame in scored:
        canonical = _canonical_json(_frame_payload(frame)).encode()
        _hash_part(hasher, b"scored", sha256(canonical).digest())
    return hasher.hexdigest()


def _break_request_digest(
    cursor: SegmentCursor,
    reason: TrajectoryBreakReason,
    next_segment: LearningTrajectorySegment,
) -> str:
    hasher = sha256(b"learning-trajectory-break-receipt-v1")
    _hash_part(
        hasher,
        b"operation",
        bytes.fromhex(_operation_key("break-and-begin", cursor)),
    )
    _hash_part(hasher, b"reason", reason.value.encode())
    _hash_part(
        hasher,
        b"next-segment",
        sha256(next_segment.segment_id.encode()).digest(),
    )
    _hash_part(
        hasher,
        b"next-prefix",
        bytes.fromhex(next_segment.content_digest),
    )
    return hasher.hexdigest()


class LearningTrajectoryRepository:
    """Own one SQLite corpus through real transaction and reopen boundaries."""

    terminal_fit_run_limit = 64
    # A retained segment can receive 180 one-frame pre-roll appends and 180
    # one-frame scored appends, plus Hold-entry, break/finalize, and retry
    # boundary operations. The next power-of-two bound (512) preserves every
    # possible mutation receipt until whole-segment eviction cascades it.
    operation_receipt_limit_per_segment = 512

    def __init__(self, database_path: str | None = None):
        self._database_path = Path(
            datastore.DB_PATH if database_path is None else database_path
        )
        self._write_state = local()
        self._reopen_quarantined_segment_ids: tuple[str, ...] = ()
        with self._connection(ensure_schema=True):
            pass
        with self._write() as connection:
            before = self._status(connection)
            self._apply_retention(connection)
            after = self._status(connection)
            if (
                before.segment_count,
                before.pre_roll_count,
                before.scored_count,
                before.evicted_segment_count,
            ) != (
                after.segment_count,
                after.pre_roll_count,
                after.scored_count,
                after.evicted_segment_count,
            ):
                self._set_revision(connection, before.corpus_revision + 1)
            corrupt_rows: list[sqlite3.Row] = []
            for row in connection.execute(
                "SELECT * FROM learning_trajectory_segment "
                "WHERE state!='quarantined' ORDER BY start_wall_ms,segment_id"
            ).fetchall():
                try:
                    self._materialize_segment(connection, row)
                except Exception:
                    corrupt_rows.append(row)
            if corrupt_rows:
                revision = self._corpus_revision(connection) + 1
                for row in corrupt_rows:
                    self._quarantine(connection, row, revision=revision)
                self._apply_retention(connection)
                self._set_revision(connection, revision)
                self._reopen_quarantined_segment_ids = tuple(
                    row["segment_id"] for row in corrupt_rows
                )

    @contextmanager
    def _connection(
        self, *, ensure_schema: bool = False
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.isolation_level = None
        try:
            if ensure_schema:
                datastore._ensure_schema(connection)
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Let several repository-ledger mutations share one real transaction."""
        active = getattr(self._write_state, "connection", None)
        if active is not None:
            yield cast(sqlite3.Connection, active)
            return
        with self._connection() as connection, datastore.transaction(connection):
            self._write_state.connection = connection
            try:
                yield connection
            finally:
                del self._write_state.connection

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._write_state, "connection", None)
        if active is not None:
            yield cast(sqlite3.Connection, active)
            return
        with self._connection() as connection, datastore.transaction(connection):
            yield connection

    @staticmethod
    def _corpus_row(connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM learning_trajectory_corpus WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("learning trajectory corpus singleton is missing")
        return row

    @classmethod
    def _corpus_revision(cls, connection: sqlite3.Connection) -> int:
        return cast(int, cls._corpus_row(connection)["corpus_revision"])

    @classmethod
    def _status(cls, connection: sqlite3.Connection) -> CorpusStatus:
        row = cls._corpus_row(connection)
        return CorpusStatus(
            corpus_revision=row["corpus_revision"],
            segment_count=row["segment_count"],
            pre_roll_count=row["pre_roll_count"],
            scored_count=row["scored_count"],
            evicted_segment_count=row["evicted_segment_count"],
            evicted_pre_roll_count=row["evicted_pre_roll_count"],
            evicted_scored_count=row["evicted_scored_count"],
            quarantined_segment_count=row["quarantined_segment_count"],
        )

    @staticmethod
    def _segment_row(
        connection: sqlite3.Connection, segment_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM learning_trajectory_segment WHERE segment_id=?",
            (segment_id,),
        ).fetchone()

    @classmethod
    def _cursor(
        cls, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> SegmentCursor:
        return SegmentCursor(
            segment_id=row["segment_id"],
            next_ordinal=row["next_ordinal"],
            chain_digest=row["rolling_digest"],
            corpus_revision=cls._corpus_revision(connection),
        )

    @classmethod
    def _resolve_current_row(
        cls, connection: sqlite3.Connection, segment_id: str
    ) -> sqlite3.Row | None:
        seen: set[str] = set()
        row = cls._segment_row(connection, segment_id)
        while row is not None and row["roll_successor_segment_id"] is not None:
            if row["segment_id"] in seen:
                raise ValueError("trajectory roll-successor cycle is corrupt")
            seen.add(row["segment_id"])
            row = cls._segment_row(connection, row["roll_successor_segment_id"])
        if row is not None and row["state"] == "quarantined":
            return None
        return row

    @staticmethod
    def _set_roll_successor(
        connection: sqlite3.Connection, source_segment_id: str, successor_id: str
    ) -> None:
        connection.execute(
            "UPDATE learning_trajectory_segment SET roll_successor_segment_id=? "
            "WHERE segment_id=?",
            (successor_id, source_segment_id),
        )

    @staticmethod
    def _operation_receipt(
        connection: sqlite3.Connection, operation_key: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM learning_trajectory_operation_receipt "
            "WHERE operation_key=?",
            (operation_key,),
        ).fetchone()

    @classmethod
    def _store_operation_receipt(
        cls,
        connection: sqlite3.Connection,
        *,
        operation_key: str,
        operation_kind: str,
        source_segment_id: str,
        request_digest: str,
        result_segment_id: str,
        inserted_pre_roll_count: int,
        inserted_scored_count: int,
        revision: int,
    ) -> None:
        connection.execute(
            "INSERT INTO learning_trajectory_operation_receipt("
            "operation_key,operation_kind,source_segment_id,request_digest,"
            "result_segment_id,inserted_pre_roll_count,inserted_scored_count,"
            "created_corpus_revision) VALUES(?,?,?,?,?,?,?,?)",
            (
                operation_key,
                operation_kind,
                source_segment_id,
                request_digest,
                result_segment_id,
                inserted_pre_roll_count,
                inserted_scored_count,
                revision,
            ),
        )
        stale = connection.execute(
            "SELECT operation_key FROM learning_trajectory_operation_receipt "
            "WHERE source_segment_id=? "
            "ORDER BY created_corpus_revision DESC,operation_key DESC "
            "LIMIT -1 OFFSET ?",
            (source_segment_id, cls.operation_receipt_limit_per_segment),
        ).fetchall()
        if stale:
            connection.executemany(
                "DELETE FROM learning_trajectory_operation_receipt "
                "WHERE operation_key=?",
                ((row["operation_key"],) for row in stale),
            )

    @staticmethod
    def _frame_rows(
        connection: sqlite3.Connection,
        segment_id: str,
        *,
        through_ordinal: int | None = None,
        through_revision: int | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["segment_id=?"]
        parameters: list[object] = [segment_id]
        if through_ordinal is not None:
            clauses.append("ordinal<=?")
            parameters.append(through_ordinal)
        if through_revision is not None:
            clauses.append("created_corpus_revision<=?")
            parameters.append(through_revision)
        return connection.execute(
            "SELECT * FROM learning_trajectory_frame WHERE "
            + " AND ".join(clauses)
            + " ORDER BY ordinal",
            tuple(parameters),
        ).fetchall()

    @staticmethod
    def _decode_frame_rows(
        rows: list[sqlite3.Row],
    ) -> tuple[
        tuple[LearningTrajectoryFrame, ...],
        tuple[LearningTrajectoryFrame, ...],
        str,
    ]:
        pre_roll: list[LearningTrajectoryFrame] = []
        scored: list[LearningTrajectoryFrame] = []
        chain_digest = _ZERO_CHAIN_DIGEST
        for expected_ordinal, row in enumerate(rows):
            if row["ordinal"] != expected_ordinal:
                raise ValueError("trajectory frame ordinals are not contiguous")
            if (
                row["payload_schema_version"]
                != TRAJECTORY_OBSERVATION_SCHEMA_VERSION
            ):
                raise ValueError(
                    "older trajectory frame payload schema is non-scoreable"
                )
            canonical_json = row["canonical_json"]
            payload = json.loads(canonical_json)
            if _canonical_json(payload) != canonical_json:
                raise ValueError("trajectory frame payload is not canonical JSON")
            if sha256(canonical_json.encode()).hexdigest() != row["frame_digest"]:
                raise ValueError("trajectory frame digest is corrupt")
            frame = _frame_from_json(canonical_json)
            if _interval_identity(frame) != row["interval_identity"]:
                raise ValueError("trajectory frame interval identity is corrupt")
            chain_digest = _next_chain_digest(chain_digest, canonical_json)
            if row["kind"] == "pre-roll" and scored:
                raise ValueError("pre-roll frame follows a scored frame")
            if row["kind"] == "pre-roll":
                pre_roll.append(frame)
            elif row["kind"] == "scored":
                scored.append(frame)
            else:
                raise ValueError("trajectory frame kind is corrupt")
        return tuple(pre_roll), tuple(scored), chain_digest

    @classmethod
    def _materialize_segment(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        through_revision: int | None = None,
        through_ordinal: int | None = None,
        verify_header: bool = True,
    ) -> LearningTrajectorySegment:
        frames = cls._frame_rows(
            connection,
            row["segment_id"],
            through_ordinal=through_ordinal,
            through_revision=through_revision,
        )
        pre_roll, scored, chain_digest = cls._decode_frame_rows(frames)
        if not frames:
            raise ValueError("trajectory segment has no retained frames")
        header = json.loads(row["header_json"])
        hold_entry = _hold_from_json(row["hold_entry_json"])
        if (
            through_revision is not None
            and row["hold_entry_revision"] is not None
            and row["hold_entry_revision"] > through_revision
        ):
            hold_entry = None

        historical_open = (
            through_revision is not None
            and (
                row["finalized_corpus_revision"] is None
                or row["finalized_corpus_revision"] > through_revision
            )
        )
        state = "open" if historical_open else row["state"]
        terminal_reason = (
            None
            if state == "open" or row["terminal_break_reason"] is None
            else TrajectoryBreakReason(row["terminal_break_reason"])
        )
        first = pre_roll[0] if pre_roll else scored[0]
        last = scored[-1] if scored else pre_roll[-1]
        segment = LearningTrajectorySegment(
            schema_version=header["schema_version"],
            observation_schema_version=header["observation_schema_version"],
            segment_id=row["segment_id"],
            cook_id=header["cook_id"],
            trajectory_session_id=header["trajectory_session_id"],
            trace_session_ids=tuple(header["trace_session_ids"]),
            collection_provenance=header["collection_provenance"],
            configuration_provenance=header["configuration_provenance"],
            cadence_digest=header["cadence_digest"],
            model_structure_digest=header["model_structure_digest"],
            held_physics_digest=header["held_physics_digest"],
            delay_input_mapping_digest=header["delay_input_mapping_digest"],
            actuation_mapping_digest=header["actuation_mapping_digest"],
            scored_fan_regime_digest=header["scored_fan_regime_digest"],
            ambient_semantics_digest=header["ambient_semantics_digest"],
            pre_roll_frames=pre_roll,
            hold_entry=hold_entry,
            scored_hold_frames=scored,
            generation_audit_ranges=tuple(header["generation_audit_ranges"]),
            start_monotonic_ms=first.monotonic_start_ms,
            end_monotonic_ms=last.monotonic_end_ms,
            start_wall_ms=first.wall_start_ms,
            end_wall_ms=last.wall_end_ms,
            start_sequence=first.sequence,
            end_sequence=last.sequence,
            pre_roll_end_reason=(
                TrajectoryBreakReason(row["pre_roll_end_reason"])
                if row["pre_roll_end_reason"] is not None
                else None
            ),
            terminal_break_reason=terminal_reason,
            state=state,
            source_trace_digest=row["source_trace_digest"],
            source_schema_version=row["source_schema_version"],
            source_row_digest=row["source_row_digest"],
            build_provenance=header["build_provenance"],
        )
        if not (
            verify_header and through_revision is None and through_ordinal is None
        ):
            return segment
        if len(pre_roll) != row["pre_roll_count"]:
            raise ValueError("trajectory pre-roll count is corrupt")
        if len(scored) != row["scored_count"]:
            raise ValueError("trajectory scored count is corrupt")
        if len(frames) != row["next_ordinal"]:
            raise ValueError("trajectory next ordinal is corrupt")
        if chain_digest != row["rolling_digest"]:
            raise ValueError("trajectory rolling digest is corrupt")
        if row["state"] != "open" and row["final_digest"] != chain_digest:
            raise ValueError("trajectory final digest is corrupt")
        if segment.fit_partition_digest != row["fit_partition_digest"]:
            raise ValueError("trajectory partition digest is corrupt")
        if segment.content_digest != row["content_digest"]:
            raise ValueError("trajectory content digest is corrupt")
        bounds = (
            segment.start_monotonic_ms,
            segment.end_monotonic_ms,
            segment.start_wall_ms,
            segment.end_wall_ms,
            segment.start_sequence,
            segment.end_sequence,
        )
        stored_bounds = (
            row["start_monotonic_ms"],
            row["end_monotonic_ms"],
            row["start_wall_ms"],
            row["end_wall_ms"],
            row["start_sequence"],
            row["end_sequence"],
        )
        if bounds != stored_bounds:
            raise ValueError("trajectory segment bounds are corrupt")
        return segment

    @staticmethod
    def _insert_frames(
        connection: sqlite3.Connection,
        segment: LearningTrajectorySegment,
        frames: tuple[tuple[str, LearningTrajectoryFrame], ...],
        *,
        start_ordinal: int,
        revision: int,
    ) -> str:
        row = connection.execute(
            "SELECT rolling_digest FROM learning_trajectory_segment WHERE segment_id=?",
            (segment.segment_id,),
        ).fetchone()
        chain_digest = _ZERO_CHAIN_DIGEST if row is None else row["rolling_digest"]
        values = []
        for offset, (kind, frame) in enumerate(frames):
            canonical_json = _canonical_json(_frame_payload(frame))
            chain_digest = _next_chain_digest(chain_digest, canonical_json)
            values.append(
                (
                    segment.segment_id,
                    start_ordinal + offset,
                    kind,
                    segment.observation_schema_version,
                    _interval_identity(frame),
                    canonical_json,
                    sha256(canonical_json.encode()).hexdigest(),
                    revision,
                )
            )
        if values:
            connection.executemany(
                "INSERT INTO learning_trajectory_frame("
                "segment_id,ordinal,kind,payload_schema_version,interval_identity,"
                "canonical_json,frame_digest,created_corpus_revision"
                ") VALUES(?,?,?,?,?,?,?,?)",
                values,
            )
        return chain_digest

    @classmethod
    def _insert_segment(
        cls,
        connection: sqlite3.Connection,
        segment: LearningTrajectorySegment,
        *,
        revision: int,
    ) -> None:
        if segment.state != "open" or segment.terminal_break_reason is not None:
            raise ValueError("begin_segment requires an open segment")
        if len(segment.pre_roll_frames) > _MAX_PRE_ROLL_PER_SEGMENT:
            raise ValueError("pre-roll frames per segment must not exceed 180")
        if len(segment.scored_hold_frames) > _MAX_SCORED_PER_SEGMENT:
            raise ValueError("scored frames per segment must not exceed 180")
        frames = tuple(
            [("pre-roll", frame) for frame in segment.pre_roll_frames]
            + [("scored", frame) for frame in segment.scored_hold_frames]
        )
        rolling_digest = _frames_chain(
            (*segment.pre_roll_frames, *segment.scored_hold_frames)
        )
        connection.execute(
            """
            INSERT INTO learning_trajectory_segment(
                segment_id,state,fit_partition_digest,header_json,
                start_monotonic_ms,end_monotonic_ms,start_wall_ms,end_wall_ms,
                start_sequence,end_sequence,hold_entry_json,hold_entry_revision,
                pre_roll_count,scored_count,next_ordinal,rolling_digest,final_digest,
                content_digest,begin_content_digest,roll_successor_segment_id,
                created_corpus_revision,updated_corpus_revision,
                finalized_corpus_revision,pre_roll_end_reason,terminal_break_reason,
                source_trace_digest,source_schema_version,source_row_digest
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                segment.segment_id,
                segment.state,
                segment.fit_partition_digest,
                _canonical_json(_segment_header(segment)),
                segment.start_monotonic_ms,
                segment.end_monotonic_ms,
                segment.start_wall_ms,
                segment.end_wall_ms,
                segment.start_sequence,
                segment.end_sequence,
                (
                    _canonical_json(_hold_payload(segment.hold_entry))
                    if segment.hold_entry is not None
                    else None
                ),
                revision if segment.hold_entry is not None else None,
                len(segment.pre_roll_frames),
                len(segment.scored_hold_frames),
                len(frames),
                rolling_digest,
                None,
                segment.content_digest,
                segment.content_digest,
                None,
                revision,
                revision,
                None,
                (
                    segment.pre_roll_end_reason.value
                    if segment.pre_roll_end_reason is not None
                    else None
                ),
                None,
                segment.source_trace_digest,
                segment.source_schema_version,
                segment.source_row_digest,
            ),
        )
        cls._insert_frames(
            connection, segment, frames, start_ordinal=0, revision=revision
        )

    @staticmethod
    def _update_segment(
        connection: sqlite3.Connection,
        segment: LearningTrajectorySegment,
        *,
        revision: int,
        rolling_digest: str,
        hold_entry_revision: int | None,
        finalized_revision: int | None,
    ) -> None:
        connection.execute(
            """
            UPDATE learning_trajectory_segment SET
                state=?, fit_partition_digest=?, header_json=?,
                start_monotonic_ms=?, end_monotonic_ms=?,
                start_wall_ms=?, end_wall_ms=?, start_sequence=?, end_sequence=?,
                hold_entry_json=?, hold_entry_revision=?, pre_roll_count=?,
                scored_count=?, next_ordinal=?, rolling_digest=?, final_digest=?,
                content_digest=?, updated_corpus_revision=?,
                finalized_corpus_revision=?, pre_roll_end_reason=?,
                terminal_break_reason=?, source_trace_digest=?,
                source_schema_version=?, source_row_digest=?
            WHERE segment_id=?
            """,
            (
                segment.state,
                segment.fit_partition_digest,
                _canonical_json(_segment_header(segment)),
                segment.start_monotonic_ms,
                segment.end_monotonic_ms,
                segment.start_wall_ms,
                segment.end_wall_ms,
                segment.start_sequence,
                segment.end_sequence,
                (
                    _canonical_json(_hold_payload(segment.hold_entry))
                    if segment.hold_entry is not None
                    else None
                ),
                hold_entry_revision,
                len(segment.pre_roll_frames),
                len(segment.scored_hold_frames),
                len(segment.pre_roll_frames) + len(segment.scored_hold_frames),
                rolling_digest,
                rolling_digest if segment.state != "open" else None,
                segment.content_digest,
                revision,
                finalized_revision,
                (
                    segment.pre_roll_end_reason.value
                    if segment.pre_roll_end_reason is not None
                    else None
                ),
                (
                    segment.terminal_break_reason.value
                    if segment.terminal_break_reason is not None
                    else None
                ),
                segment.source_trace_digest,
                segment.source_schema_version,
                segment.source_row_digest,
                segment.segment_id,
            ),
        )

    @classmethod
    def _normalize_full_open_segment(
        cls,
        connection: sqlite3.Connection,
        segment: LearningTrajectorySegment,
        *,
        revision: int,
    ) -> sqlite3.Row:
        row = cls._segment_row(connection, segment.segment_id)
        if row is None:
            raise RuntimeError("new trajectory segment was not inserted")
        if len(segment.scored_hold_frames) != _MAX_SCORED_PER_SEGMENT:
            return row
        finalized = replace(
            segment,
            state="finalized",
            terminal_break_reason=TrajectoryBreakReason.RETENTION_ROLLOVER,
        )
        cls._update_segment(
            connection,
            finalized,
            revision=revision,
            rolling_digest=row["rolling_digest"],
            hold_entry_revision=row["hold_entry_revision"],
            finalized_revision=revision,
        )
        successor_id = f"{segment.segment_id}:roll:{revision}:1"
        cls._insert_segment(
            connection,
            _rolled_segment(
                finalized,
                successor_id,
                finalized.scored_hold_frames[-_MAX_SCORED_PER_SEGMENT:],
            ),
            revision=revision,
        )
        cls._set_roll_successor(connection, segment.segment_id, successor_id)
        successor = cls._segment_row(connection, successor_id)
        if successor is None:
            raise RuntimeError("rolled trajectory segment was not inserted")
        return successor

    @staticmethod
    def _set_revision(connection: sqlite3.Connection, revision: int) -> None:
        connection.execute(
            "UPDATE learning_trajectory_corpus SET corpus_revision=? WHERE singleton=1",
            (revision,),
        )

    @staticmethod
    def _sync_counts(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM learning_trajectory_segment), "
            "COALESCE(SUM(CASE WHEN s.state!='quarantined' "
            "AND f.kind='pre-roll' THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN s.state!='quarantined' "
            "AND f.kind='scored' THEN 1 ELSE 0 END),0) "
            "FROM learning_trajectory_segment AS s "
            "LEFT JOIN learning_trajectory_frame AS f "
            "ON f.segment_id=s.segment_id"
        ).fetchone()
        connection.execute(
            "UPDATE learning_trajectory_corpus SET segment_count=?, "
            "pre_roll_count=?, scored_count=? WHERE singleton=1",
            tuple(row),
        )

    @classmethod
    def _apply_retention(cls, connection: sqlite3.Connection) -> None:
        cls._sync_counts(connection)
        while True:
            status = cls._status(connection)
            if (
                status.scored_count <= _MAX_SCORED_ROWS
                and status.pre_roll_count <= _MAX_PRE_ROLL_ROWS
                and status.segment_count <= _MAX_SEGMENTS
            ):
                return
            victim = connection.execute(
                "SELECT s.segment_id, "
                "(SELECT COUNT(*) FROM learning_trajectory_frame f "
                " WHERE f.segment_id=s.segment_id AND f.kind='pre-roll') "
                "AS physical_pre_roll_count, "
                "(SELECT COUNT(*) FROM learning_trajectory_frame f "
                " WHERE f.segment_id=s.segment_id AND f.kind='scored') "
                "AS physical_scored_count "
                "FROM learning_trajectory_segment s WHERE s.state='finalized' "
                "ORDER BY s.end_wall_ms,s.segment_id LIMIT 1"
            ).fetchone()
            if victim is None:
                raise ValueError(
                    "retention caps cannot be met without evicting an open segment"
                )
            connection.execute(
                "DELETE FROM learning_trajectory_segment WHERE segment_id=?",
                (victim["segment_id"],),
            )
            connection.execute(
                "UPDATE learning_trajectory_corpus SET "
                "evicted_segment_count=evicted_segment_count+1, "
                "evicted_pre_roll_count=evicted_pre_roll_count+?, "
                "evicted_scored_count=evicted_scored_count+? WHERE singleton=1",
                (
                    victim["physical_pre_roll_count"],
                    victim["physical_scored_count"],
                ),
            )
            cls._sync_counts(connection)

    @classmethod
    def _quarantine(
        cls, connection: sqlite3.Connection, row: sqlite3.Row, *, revision: int
    ) -> None:
        if row["state"] == "quarantined":
            return
        connection.execute(
            "UPDATE learning_trajectory_segment SET state='quarantined', "
            "terminal_break_reason=?, final_digest=rolling_digest, "
            "pre_roll_count=0,scored_count=0,next_ordinal=0, "
            "updated_corpus_revision=?, finalized_corpus_revision=? "
            "WHERE segment_id=?",
            (
                TrajectoryBreakReason.ERROR.value,
                revision,
                revision,
                row["segment_id"],
            ),
        )
        connection.execute(
            "DELETE FROM learning_trajectory_operation_receipt "
            "WHERE source_segment_id=?",
            (row["segment_id"],),
        )
        connection.execute(
            "UPDATE learning_trajectory_corpus SET "
            "quarantined_segment_count=quarantined_segment_count+1 WHERE singleton=1"
        )

    @staticmethod
    def _same_append(
        connection: sqlite3.Connection,
        cursor: SegmentCursor,
        *,
        pre_roll: tuple[LearningTrajectoryFrame, ...],
        hold_entry: HoldEntrySample | None,
        scored: tuple[LearningTrajectoryFrame, ...],
    ) -> bool:
        expected = tuple(
            [("pre-roll", frame) for frame in pre_roll]
            + [("scored", frame) for frame in scored]
        )
        if not expected:
            return False
        rows = connection.execute(
            "SELECT kind,canonical_json FROM learning_trajectory_frame "
            "WHERE segment_id=? AND ordinal>=? AND ordinal<? ORDER BY ordinal",
            (cursor.segment_id, cursor.next_ordinal, cursor.next_ordinal + len(expected)),
        ).fetchall()
        if len(rows) != len(expected):
            return False
        if any(
            row["kind"] != kind
            or row["canonical_json"] != _canonical_json(_frame_payload(frame))
            for row, (kind, frame) in zip(rows, expected, strict=True)
        ):
            return False
        if hold_entry is not None:
            segment_row = connection.execute(
                "SELECT hold_entry_json FROM learning_trajectory_segment WHERE segment_id=?",
                (cursor.segment_id,),
            ).fetchone()
            if segment_row is None or segment_row["hold_entry_json"] != _canonical_json(
                _hold_payload(hold_entry)
            ):
                return False
        return True

    def begin_segment(self, segment: LearningTrajectorySegment) -> SegmentCursor:
        conflict = False
        result: SegmentCursor | None = None
        with self._write() as connection:
            existing = self._segment_row(connection, segment.segment_id)
            if existing is not None:
                exact_prefix = (
                    existing["begin_content_digest"] == segment.content_digest
                )
                if not exact_prefix and existing["state"] != "quarantined":
                    try:
                        current_segment = self._materialize_segment(
                            connection, existing
                        )
                    except Exception:
                        current_segment = None
                    exact_prefix = (
                        current_segment is not None
                        and current_segment.content_digest == segment.content_digest
                    )
                if exact_prefix:
                    current = self._resolve_current_row(
                        connection, existing["segment_id"]
                    )
                    if current is None:
                        raise StaleSegmentCursorError(
                            "delayed begin receipt source was evicted"
                        )
                    return self._cursor(connection, current)
                revision = self._corpus_revision(connection) + 1
                self._quarantine(connection, existing, revision=revision)
                self._set_revision(connection, revision)
                self._sync_counts(connection)
                conflict = True
            else:
                revision = self._corpus_revision(connection) + 1
                self._insert_segment(connection, segment, revision=revision)
                inserted = self._normalize_full_open_segment(
                    connection, segment, revision=revision
                )
                self._apply_retention(connection)
                self._set_revision(connection, revision)
                retained = self._segment_row(connection, inserted["segment_id"])
                if retained is None:
                    raise RuntimeError("new open trajectory segment was evicted")
                result = self._cursor(connection, retained)
        if conflict:
            raise LearningTrajectoryConflictError(
                f"learning trajectory segment identity conflict: {segment.segment_id}"
            )
        if result is None:
            raise RuntimeError("begin_segment produced no cursor")
        return result

    def append(
        self,
        cursor: SegmentCursor,
        *,
        pre_roll: tuple[LearningTrajectoryFrame, ...] = (),
        hold_entry: HoldEntrySample | None = None,
        scored: tuple[LearningTrajectoryFrame, ...] = (),
    ) -> AppendReceipt:
        if type(pre_roll) is not tuple or type(scored) is not tuple:
            raise TypeError("trajectory append batches must be tuples")
        if not pre_roll and not scored:
            raise ValueError("trajectory append requires at least one frame")
        operation_key = _operation_key("append", cursor)
        request_digest = _append_request_digest(
            cursor,
            pre_roll=pre_roll,
            hold_entry=hold_entry,
            scored=scored,
        )
        conflict = False
        result: AppendReceipt | None = None
        with self._write() as connection:
            operation = self._operation_receipt(connection, operation_key)
            if operation is not None and operation["request_digest"] == request_digest:
                current = self._resolve_current_row(
                    connection, operation["result_segment_id"]
                )
                if current is None:
                    raise StaleSegmentCursorError(
                        "delayed append receipt source was evicted"
                    )
                return AppendReceipt(
                    cursor=self._cursor(connection, current),
                    inserted_pre_roll_count=operation[
                        "inserted_pre_roll_count"
                    ],
                    inserted_scored_count=operation["inserted_scored_count"],
                )
            row = self._segment_row(connection, cursor.segment_id)
            if row is None:
                raise StaleSegmentCursorError("stale segment cursor: segment is absent")
            current_revision = self._corpus_revision(connection)
            if self._same_append(
                connection,
                cursor,
                pre_roll=pre_roll,
                hold_entry=hold_entry,
                scored=scored,
            ):
                result = AppendReceipt(
                    cursor=self._cursor(connection, row),
                    inserted_pre_roll_count=len(pre_roll),
                    inserted_scored_count=len(scored),
                    duplicate=False,
                )
            else:
                occupied = connection.execute(
                    "SELECT 1 FROM learning_trajectory_frame "
                    "WHERE segment_id=? AND ordinal>=? LIMIT 1",
                    (cursor.segment_id, cursor.next_ordinal),
                ).fetchone()
                if occupied is not None and cursor.next_ordinal < row["next_ordinal"]:
                    revision = current_revision + 1
                    self._quarantine(connection, row, revision=revision)
                    self._set_revision(connection, revision)
                    self._sync_counts(connection)
                    conflict = True
                elif (
                    row["state"] != "open"
                    or cursor.next_ordinal != row["next_ordinal"]
                    or cursor.chain_digest != row["rolling_digest"]
                    or cursor.corpus_revision != current_revision
                ):
                    raise StaleSegmentCursorError("stale segment cursor CAS")
                else:
                    segment = self._materialize_segment(connection, row)
                    combined_pre_roll = (*segment.pre_roll_frames, *pre_roll)
                    if len(combined_pre_roll) > _MAX_PRE_ROLL_PER_SEGMENT:
                        raise ValueError("pre-roll frames per segment must not exceed 180")
                    if (
                        hold_entry is not None
                        and segment.hold_entry is not None
                        and hold_entry != segment.hold_entry
                    ):
                        raise LearningTrajectoryConflictError(
                            "Hold-entry anchor conflict"
                        )
                    combined_hold = segment.hold_entry or hold_entry
                    revision = current_revision + 1
                    first_capacity = _MAX_SCORED_PER_SEGMENT - len(
                        segment.scored_hold_frames
                    )
                    first_scored = scored[:first_capacity]
                    remaining = scored[first_capacity:]
                    first_combined = _with_frames(
                        segment,
                        pre_roll=combined_pre_roll,
                        hold_entry=combined_hold,
                        scored=(*segment.scored_hold_frames, *first_scored),
                        terminal_reason=None,
                    )
                    appended_frames = tuple(
                        [("pre-roll", frame) for frame in pre_roll]
                        + [("scored", frame) for frame in first_scored]
                    )
                    rolling = self._insert_frames(
                        connection,
                        segment,
                        appended_frames,
                        start_ordinal=row["next_ordinal"],
                        revision=revision,
                    )
                    hold_revision = row["hold_entry_revision"]
                    if hold_revision is None and combined_hold is not None:
                        hold_revision = revision
                    self._update_segment(
                        connection,
                        first_combined,
                        revision=revision,
                        rolling_digest=rolling,
                        hold_entry_revision=hold_revision,
                        finalized_revision=None,
                    )
                    active = first_combined
                    roll_index = 0
                    while len(active.scored_hold_frames) == _MAX_SCORED_PER_SEGMENT:
                        finalized = replace(
                            active,
                            state="finalized",
                            terminal_break_reason=TrajectoryBreakReason.RETENTION_ROLLOVER,
                        )
                        self._update_segment(
                            connection,
                            finalized,
                            revision=revision,
                            rolling_digest=rolling,
                            hold_entry_revision=hold_revision,
                            finalized_revision=revision,
                        )
                        carried = finalized.scored_hold_frames[-180:]
                        roll_index += 1
                        rolled_id = (
                            f"{cursor.segment_id}:roll:{revision}:{roll_index}"
                        )
                        active = _rolled_segment(finalized, rolled_id, carried)
                        self._insert_segment(connection, active, revision=revision)
                        self._set_roll_successor(
                            connection, finalized.segment_id, rolled_id
                        )
                        active_row = self._segment_row(connection, active.segment_id)
                        if active_row is None:
                            raise RuntimeError("failed to create rolled trajectory segment")
                        rolling = active_row["rolling_digest"]
                        hold_revision = None
                        if not remaining:
                            break
                        take = remaining[:_MAX_SCORED_PER_SEGMENT]
                        remaining = remaining[len(take) :]
                        new_hold = _hold_entry_from_frame(take[0])
                        combined = _with_frames(
                            active,
                            pre_roll=active.pre_roll_frames,
                            hold_entry=new_hold,
                            scored=take,
                            terminal_reason=None,
                        )
                        rolling = self._insert_frames(
                            connection,
                            active,
                            tuple(("scored", frame) for frame in take),
                            start_ordinal=len(active.pre_roll_frames),
                            revision=revision,
                        )
                        hold_revision = revision
                        self._update_segment(
                            connection,
                            combined,
                            revision=revision,
                            rolling_digest=rolling,
                            hold_entry_revision=hold_revision,
                            finalized_revision=None,
                        )
                        active = combined
                    if remaining:
                        raise RuntimeError("trajectory auto-roll left unconsumed frames")
                    self._store_operation_receipt(
                        connection,
                        operation_key=operation_key,
                        operation_kind="append",
                        source_segment_id=cursor.segment_id,
                        request_digest=request_digest,
                        result_segment_id=active.segment_id,
                        inserted_pre_roll_count=len(pre_roll),
                        inserted_scored_count=len(scored),
                        revision=revision,
                    )
                    self._apply_retention(connection)
                    self._set_revision(connection, revision)
                    active_row = self._segment_row(connection, active.segment_id)
                    if active_row is None:
                        raise RuntimeError("active trajectory segment was evicted")
                    result = AppendReceipt(
                        cursor=self._cursor(connection, active_row),
                        inserted_pre_roll_count=len(pre_roll),
                        inserted_scored_count=len(scored),
                    )
        if conflict:
            raise LearningTrajectoryConflictError(
                f"learning trajectory frame conflict: {cursor.segment_id}"
            )
        if result is None:
            raise RuntimeError("append produced no receipt")
        return result

    def break_and_begin(
        self,
        cursor: SegmentCursor,
        reason: TrajectoryBreakReason,
        next_segment: LearningTrajectorySegment,
    ) -> SegmentCursor:
        operation_key = _operation_key("break-and-begin", cursor)
        request_digest = _break_request_digest(cursor, reason, next_segment)
        with self._write() as connection:
            operation = self._operation_receipt(connection, operation_key)
            if (
                operation is not None
                and operation["request_digest"] != request_digest
            ):
                raise LearningTrajectoryConflictError(
                    f"break-and-begin receipt conflict: {cursor.segment_id}"
                )
            if operation is not None:
                current = self._resolve_current_row(
                    connection, operation["result_segment_id"]
                )
                if current is None:
                    raise StaleSegmentCursorError(
                        "delayed break-and-begin receipt source was evicted"
                    )
                return self._cursor(connection, current)
            current_row = self._segment_row(connection, cursor.segment_id)
            next_row = self._segment_row(connection, next_segment.segment_id)
            if current_row is None:
                raise StaleSegmentCursorError("stale segment cursor: segment is absent")
            legacy_current: sqlite3.Row | None = None
            if (
                current_row["state"] == "finalized"
                and current_row["terminal_break_reason"] == reason.value
                and next_row is not None
                and next_row["begin_content_digest"] == next_segment.content_digest
            ):
                legacy_current = self._resolve_current_row(
                    connection, next_row["segment_id"]
                )
            if legacy_current is not None:
                return self._cursor(connection, legacy_current)
            current_revision = self._corpus_revision(connection)
            if (
                current_row["state"] != "open"
                or cursor.next_ordinal != current_row["next_ordinal"]
                or cursor.chain_digest != current_row["rolling_digest"]
                or cursor.corpus_revision != current_revision
            ):
                raise StaleSegmentCursorError("stale segment cursor CAS")
            if next_row is not None:
                raise LearningTrajectoryConflictError(
                    f"learning trajectory segment identity conflict: {next_segment.segment_id}"
                )
            revision = current_revision + 1
            current = self._materialize_segment(connection, current_row)
            finalized = replace(
                current, state="finalized", terminal_break_reason=reason
            )
            self._update_segment(
                connection,
                finalized,
                revision=revision,
                rolling_digest=current_row["rolling_digest"],
                hold_entry_revision=current_row["hold_entry_revision"],
                finalized_revision=revision,
            )
            self._insert_segment(connection, next_segment, revision=revision)
            inserted = self._normalize_full_open_segment(
                connection, next_segment, revision=revision
            )
            self._store_operation_receipt(
                connection,
                operation_key=operation_key,
                operation_kind="break-and-begin",
                source_segment_id=cursor.segment_id,
                request_digest=request_digest,
                result_segment_id=inserted["segment_id"],
                inserted_pre_roll_count=0,
                inserted_scored_count=0,
                revision=revision,
            )
            self._apply_retention(connection)
            self._set_revision(connection, revision)
            retained = self._segment_row(connection, inserted["segment_id"])
            if retained is None:
                raise RuntimeError("new open trajectory segment was evicted")
            return self._cursor(connection, retained)

    def finalize(
        self, cursor: SegmentCursor, reason: TrajectoryBreakReason
    ) -> FinalizeReceipt:
        with self._write() as connection:
            row = self._segment_row(connection, cursor.segment_id)
            if row is None:
                raise StaleSegmentCursorError("stale segment cursor: segment is absent")
            if (
                row["state"] == "finalized"
                and row["terminal_break_reason"] == reason.value
                and cursor.next_ordinal == row["next_ordinal"]
                and cursor.chain_digest == row["rolling_digest"]
            ):
                return FinalizeReceipt(
                    segment_id=cursor.segment_id,
                    corpus_revision=row["updated_corpus_revision"],
                    content_digest=row["content_digest"],
                    duplicate=False,
                )
            current_revision = self._corpus_revision(connection)
            if (
                row["state"] != "open"
                or cursor.next_ordinal != row["next_ordinal"]
                or cursor.chain_digest != row["rolling_digest"]
                or cursor.corpus_revision != current_revision
            ):
                raise StaleSegmentCursorError("stale segment cursor CAS")
            revision = current_revision + 1
            segment = self._materialize_segment(connection, row)
            finalized = replace(
                segment, state="finalized", terminal_break_reason=reason
            )
            self._update_segment(
                connection,
                finalized,
                revision=revision,
                rolling_digest=row["rolling_digest"],
                hold_entry_revision=row["hold_entry_revision"],
                finalized_revision=revision,
            )
            self._apply_retention(connection)
            self._set_revision(connection, revision)
            return FinalizeReceipt(
                segment_id=cursor.segment_id,
                corpus_revision=revision,
                content_digest=finalized.content_digest,
            )

    def recover_open_segments(self, now_ms: int) -> RecoveryReport:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("now_ms must be a non-negative integer")
        finalized_ids: list[str] = []
        quarantined_ids = list(self._reopen_quarantined_segment_ids)
        self._reopen_quarantined_segment_ids = ()
        new_quarantine_count = 0
        interrupted_ids: list[str] = []
        with self._write() as connection:
            candidate_rows = connection.execute(
                "SELECT * FROM learning_trajectory_segment "
                "WHERE state!='quarantined' ORDER BY start_wall_ms,segment_id"
            ).fetchall()
            interrupted_ids = [
                row["request_id"]
                for row in connection.execute(
                    "SELECT request_id FROM learning_fit_run "
                    "WHERE status IN ('queued','running') ORDER BY request_id"
                ).fetchall()
            ]
            current_revision = self._corpus_revision(connection)
            revision = current_revision + 1
            for row in candidate_rows:
                try:
                    segment = self._materialize_segment(connection, row)
                except Exception:
                    self._quarantine(connection, row, revision=revision)
                    quarantined_ids.append(row["segment_id"])
                    new_quarantine_count += 1
                    continue
                if row["state"] != "open":
                    continue
                finalized = replace(
                    segment,
                    state="finalized",
                    terminal_break_reason=TrajectoryBreakReason.UNCLEAN_RESTART,
                )
                self._update_segment(
                    connection,
                    finalized,
                    revision=revision,
                    rolling_digest=row["rolling_digest"],
                    hold_entry_revision=row["hold_entry_revision"],
                    finalized_revision=revision,
                )
                finalized_ids.append(row["segment_id"])
            if not finalized_ids and new_quarantine_count == 0 and not interrupted_ids:
                return RecoveryReport(
                    finalized_segment_ids=(),
                    quarantined_segment_ids=tuple(quarantined_ids),
                    interrupted_fit_request_ids=(),
                    corpus_revision=current_revision,
                )
            if interrupted_ids:
                placeholders = ",".join("?" for _ in interrupted_ids)
                connection.execute(
                    "UPDATE learning_fit_run SET status='interrupted',completed_ms=? "
                    f"WHERE request_id IN ({placeholders})",
                    (now_ms, *interrupted_ids),
                )
            self._apply_retention(connection)
            self._set_revision(connection, revision)
            self._prune_fit_manifests(connection)
            return RecoveryReport(
                finalized_segment_ids=tuple(finalized_ids),
                quarantined_segment_ids=tuple(quarantined_ids),
                interrupted_fit_request_ids=tuple(interrupted_ids),
                corpus_revision=revision,
            )

    def status(self) -> CorpusStatus:
        with self._connection() as connection:
            return self._status(connection)

    def read_segment(self, segment_id: str) -> LearningTrajectorySegment | None:
        with self._connection() as connection:
            row = self._segment_row(connection, segment_id)
            if row is None:
                return None
            try:
                return self._materialize_segment(connection, row)
            except Exception:
                if row["state"] == "quarantined":
                    return None
                raise

    def snapshot_fit_corpus(
        self,
        fit_partition_digest: str,
        *,
        through_revision: int | None = None,
    ) -> FitCorpusSnapshot:
        with self._connection() as connection:
            current_revision = self._corpus_revision(connection)
            revision = current_revision if through_revision is None else through_revision
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or not 0 <= revision <= current_revision
            ):
                raise ValueError("through_revision is outside the retained corpus history")
            rows = connection.execute(
                "SELECT * FROM learning_trajectory_segment "
                "WHERE fit_partition_digest=? AND state!='quarantined' "
                "AND created_corpus_revision<=? ORDER BY start_wall_ms,segment_id",
                (fit_partition_digest, revision),
            ).fetchall()
            segments: list[LearningTrajectorySegment] = []
            slices: list[FitCorpusSlice] = []
            for row in rows:
                frame_rows = self._frame_rows(
                    connection,
                    row["segment_id"],
                    through_revision=revision,
                )
                if not frame_rows:
                    continue
                pre_roll_count = sum(item["kind"] == "pre-roll" for item in frame_rows)
                scored_count = sum(item["kind"] == "scored" for item in frame_rows)
                if scored_count == 0:
                    continue
                _, _, prefix_digest = self._decode_frame_rows(frame_rows)
                through_ordinal = frame_rows[-1]["ordinal"]
                segment = self._materialize_segment(
                    connection,
                    row,
                    through_revision=revision,
                    through_ordinal=through_ordinal,
                    verify_header=False,
                )
                segments.append(segment)
                slices.append(
                    FitCorpusSlice(
                        segment_id=row["segment_id"],
                        through_ordinal=through_ordinal,
                        prefix_digest=prefix_digest,
                        pre_roll_count=pre_roll_count,
                        scored_count=scored_count,
                    )
                )
            if not slices:
                raise ValueError("fit corpus snapshot has no scored observations")
            identity = _corpus_identity(
                corpus_revision=revision,
                fit_partition_digest=fit_partition_digest,
                slices=tuple(slices),
            )
            return FitCorpusSnapshot(identity=identity, segments=tuple(segments))

    @staticmethod
    def _fit_run(row: sqlite3.Row) -> FitRun:
        return FitRun(
            request_id=row["request_id"],
            status=cast(FitRunStatus, row["status"]),
            fit_partition_digest=row["fit_partition_digest"],
            corpus_revision=row["corpus_revision"],
            corpus_digest=row["corpus_digest"],
            parent_incumbent_digest=row["parent_incumbent_digest"],
            parent_incumbent_generation=row["parent_incumbent_generation"],
            candidate_generation=row["candidate_generation"],
            trigger_origin=row["trigger_origin"],
            candidate_digest=row["candidate_digest"],
            error=row["result_error"],
            created_ms=row["created_ms"],
            started_ms=row["started_ms"],
            completed_ms=row["completed_ms"],
        )

    @staticmethod
    def _manifest_json(identity: FitCorpusIdentity) -> str:
        return _canonical_json(
            {
                "schema_version": identity.schema_version,
                "corpus_revision": identity.corpus_revision,
                "fit_partition_digest": identity.fit_partition_digest,
                "corpus_digest": identity.corpus_digest,
                "slices": [
                    {
                        "segment_id": item.segment_id,
                        "through_ordinal": item.through_ordinal,
                        "prefix_digest": item.prefix_digest,
                        "pre_roll_count": item.pre_roll_count,
                        "scored_count": item.scored_count,
                    }
                    for item in identity.slices
                ],
            }
        )

    @staticmethod
    def _next_fit_timestamp(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT MAX(value) FROM ("
            "SELECT created_ms AS value FROM learning_fit_run "
            "UNION ALL SELECT started_ms FROM learning_fit_run WHERE started_ms IS NOT NULL "
            "UNION ALL SELECT completed_ms FROM learning_fit_run WHERE completed_ms IS NOT NULL"
            ")"
        ).fetchone()
        latest = -1 if row is None or row[0] is None else row[0]
        return max(_now_ms(), latest + 1)

    @classmethod
    def _prune_fit_manifests(cls, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT request_id FROM learning_fit_run "
            "WHERE status IN ('succeeded','failed','interrupted','stale') "
            "AND manifest_json IS NOT NULL "
            "ORDER BY completed_ms DESC,request_id DESC"
        ).fetchall()
        for row in rows[cls.terminal_fit_run_limit :]:
            connection.execute(
                "UPDATE learning_fit_run SET manifest_json=NULL WHERE request_id=?",
                (row["request_id"],),
            )

    def record_fit_request(
        self, snapshot: FitCorpusSnapshot, lineage: ModelFitLineage
    ) -> FitRun:
        if lineage.fit_corpus != snapshot.identity:
            raise ValueError("fit lineage corpus does not match the supplied snapshot")
        with self._write() as connection:
            now_ms = self._next_fit_timestamp(connection)
            existing = connection.execute(
                "SELECT * FROM learning_fit_run WHERE request_id=?",
                (lineage.request_id,),
            ).fetchone()
            status = cast(FitRunStatus, lineage.result_status)
            if existing is None and status not in ("queued", "running"):
                raise ValueError(
                    "new fit requests must start queued or running, not terminal"
                )
            if existing is None:
                started_ms = now_ms if status == "running" else None
                completed_ms = now_ms if status == "stale" else None
                connection.execute(
                    """
                    INSERT INTO learning_fit_run(
                        request_id,status,fit_partition_digest,corpus_revision,
                        corpus_digest,manifest_json,parent_incumbent_digest,
                        parent_incumbent_generation,candidate_generation,
                        trigger_origin,candidate_digest,result_error,
                        created_ms,started_ms,completed_ms
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        lineage.request_id,
                        status,
                        snapshot.identity.fit_partition_digest,
                        snapshot.identity.corpus_revision,
                        snapshot.identity.corpus_digest,
                        self._manifest_json(snapshot.identity),
                        lineage.parent_incumbent_digest,
                        lineage.parent_incumbent_generation,
                        lineage.candidate_generation,
                        lineage.trigger_origin,
                        lineage.candidate_digest,
                        None,
                        now_ms,
                        started_ms,
                        completed_ms,
                    ),
                )
            else:
                immutable_values = (
                    existing["fit_partition_digest"],
                    existing["corpus_revision"],
                    existing["corpus_digest"],
                    existing["parent_incumbent_digest"],
                    existing["parent_incumbent_generation"],
                    existing["candidate_generation"],
                    existing["trigger_origin"],
                )
                requested_values = (
                    snapshot.identity.fit_partition_digest,
                    snapshot.identity.corpus_revision,
                    snapshot.identity.corpus_digest,
                    lineage.parent_incumbent_digest,
                    lineage.parent_incumbent_generation,
                    lineage.candidate_generation,
                    lineage.trigger_origin,
                )
                if immutable_values != requested_values:
                    raise LearningTrajectoryConflictError(
                        f"fit request identity conflict: {lineage.request_id}"
                    )
                current_status = existing["status"]
                if current_status == status:
                    return self._fit_run(existing)
                allowed = (current_status, status) in {
                    ("queued", "running"),
                    ("queued", "stale"),
                    ("running", "stale"),
                }
                if not allowed:
                    raise LearningTrajectoryConflictError(
                        f"fit request transition conflict: {current_status}->{status}"
                    )
                connection.execute(
                    "UPDATE learning_fit_run SET status=?, "
                    "started_ms=CASE WHEN ?='running' THEN COALESCE(started_ms,?) ELSE started_ms END, "
                    "completed_ms=CASE WHEN ?='stale' THEN ? ELSE completed_ms END "
                    "WHERE request_id=?",
                    (status, status, now_ms, status, now_ms, lineage.request_id),
                )
            self._prune_fit_manifests(connection)
            row = connection.execute(
                "SELECT * FROM learning_fit_run WHERE request_id=?",
                (lineage.request_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("fit request was not stored")
            return self._fit_run(row)

    def complete_fit(
        self,
        request_id: str,
        *,
        candidate_digest: str | None,
        error: str | None,
    ) -> FitRun:
        if candidate_digest is not None and error is None:
            status: FitRunStatus = "succeeded"
        elif candidate_digest is None and isinstance(error, str) and error.strip():
            status = "failed"
        else:
            raise ValueError(
                "complete_fit requires a candidate for success or an error for failure"
            )
        with self._write() as connection:
            now_ms = self._next_fit_timestamp(connection)
            row = connection.execute(
                "SELECT * FROM learning_fit_run WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            terminal = row["status"] in ("succeeded", "failed")
            exact_completion = (
                row["status"] == status
                and row["candidate_digest"] == candidate_digest
                and row["result_error"] == error
            )
            if terminal and exact_completion:
                return self._fit_run(row)
            if terminal:
                raise LearningTrajectoryConflictError(
                    f"fit completion conflict: {request_id}"
                )
            if row["status"] not in ("queued", "running"):
                raise LearningTrajectoryConflictError(
                    f"fit completion conflict from {row['status']}: {request_id}"
                )
            connection.execute(
                "UPDATE learning_fit_run SET status=?,candidate_digest=?,"
                "result_error=?,completed_ms=? WHERE request_id=?",
                (status, candidate_digest, error, now_ms, request_id),
            )
            self._prune_fit_manifests(connection)
            completed = connection.execute(
                "SELECT * FROM learning_fit_run WHERE request_id=?", (request_id,)
            ).fetchone()
            if completed is None:
                raise RuntimeError("fit completion disappeared")
            return self._fit_run(completed)

    def replay_fit(self, request_id: str) -> FitCorpusSnapshot:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM learning_fit_run WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            if row["manifest_json"] is None:
                raise FitCorpusEvictedError(request_id)
            manifest = json.loads(row["manifest_json"])
            slices = tuple(
                FitCorpusSlice(
                    segment_id=item["segment_id"],
                    through_ordinal=item["through_ordinal"],
                    prefix_digest=item["prefix_digest"],
                    pre_roll_count=item["pre_roll_count"],
                    scored_count=item["scored_count"],
                )
                for item in manifest["slices"]
            )
            identity = FitCorpusIdentity(
                schema_version=manifest["schema_version"],
                corpus_revision=manifest["corpus_revision"],
                fit_partition_digest=manifest["fit_partition_digest"],
                slices=slices,
                corpus_digest=manifest["corpus_digest"],
            )
            segments: list[LearningTrajectorySegment] = []
            for item in slices:
                segment_row = self._segment_row(connection, item.segment_id)
                if segment_row is None:
                    raise FitCorpusEvictedError(request_id)
                frame_rows = self._frame_rows(
                    connection,
                    item.segment_id,
                    through_ordinal=item.through_ordinal,
                )
                if len(frame_rows) != item.through_ordinal + 1:
                    raise FitCorpusEvictedError(request_id)
                pre_roll_count = sum(frame["kind"] == "pre-roll" for frame in frame_rows)
                scored_count = sum(frame["kind"] == "scored" for frame in frame_rows)
                try:
                    _, _, prefix_digest = self._decode_frame_rows(frame_rows)
                    segment = self._materialize_segment(
                        connection,
                        segment_row,
                        through_ordinal=item.through_ordinal,
                        through_revision=identity.corpus_revision,
                        verify_header=False,
                    )
                except Exception as exc:
                    raise FitCorpusEvictedError(request_id) from exc
                if (
                    prefix_digest != item.prefix_digest
                    or pre_roll_count != item.pre_roll_count
                    or scored_count != item.scored_count
                ):
                    raise FitCorpusEvictedError(request_id)
                segments.append(segment)
            return FitCorpusSnapshot(identity=identity, segments=tuple(segments))


def _hold_entry_from_frame(frame: LearningTrajectoryFrame) -> HoldEntrySample:
    return HoldEntrySample(
        monotonic_ms=frame.monotonic_start_ms,
        wall_ms=frame.wall_start_ms,
        chamber_temperature_c=frame.chamber_temperature_c,
        probe_valid=frame.probe_valid,
        probe_source=frame.probe_source,
    )
