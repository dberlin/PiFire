"""SQLite transactions for durable model evidence and activation state."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from common import datastore
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ActivationEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceDbRow,
    ModelEvidenceRecord,
    RollbackEvidence,
)


@dataclass(frozen=True, slots=True, eq=False)
class ModelActivationPair:
    """Immutable storage projection of one controller-owned grey pair."""

    model_digest: str
    configuration_json: str
    estimator_kind: str
    solver_kind: str
    candidate_generation: int
    role_generation: int
    ownership_digest: str

    @classmethod
    def from_json(cls, value: str) -> ModelActivationPair:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise TypeError("stored pair descriptor must be an object")
        configuration = decoded.get("configuration")
        if not isinstance(configuration, Mapping):
            raise TypeError("stored pair configuration must be an object")
        generation_fields = ("candidate_generation", "role_generation")
        if any(
            isinstance(decoded.get(name), bool) or not isinstance(decoded.get(name), int) or decoded[name] < 0
            for name in generation_fields
        ):
            raise ValueError("stored pair generations must be non-negative integers")
        string_fields = (
            "model_digest",
            "estimator_kind",
            "solver_kind",
            "ownership_digest",
        )
        if any(not isinstance(decoded.get(name), str) or not decoded[name].strip() for name in string_fields):
            raise ValueError("stored pair identity fields must be non-blank strings")
        configuration_json = json.dumps(
            dict(configuration),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return cls(
            model_digest=decoded["model_digest"],
            configuration_json=configuration_json,
            estimator_kind=decoded["estimator_kind"],
            solver_kind=decoded["solver_kind"],
            candidate_generation=decoded["candidate_generation"],
            role_generation=decoded["role_generation"],
            ownership_digest=decoded["ownership_digest"],
        )

    @property
    def configuration(self) -> dict[str, object]:
        configuration = json.loads(self.configuration_json)
        assert isinstance(configuration, dict)
        return configuration

    def to_dict(self) -> dict[str, object]:
        return {
            "model_digest": self.model_digest,
            "configuration": self.configuration,
            "estimator_kind": self.estimator_kind,
            "solver_kind": self.solver_kind,
            "candidate_generation": self.candidate_generation,
            "role_generation": self.role_generation,
            "ownership_digest": self.ownership_digest,
        }

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ModelActivationPair):
            return self.to_dict() == other.to_dict()
        to_dict = getattr(other, "to_dict", None)
        return callable(to_dict) and self.to_dict() == to_dict()


@dataclass(frozen=True, slots=True)
class ModelActivationState:
    """The one active/rollback pair selected by an activation decision."""

    active_snapshot_json: str
    rollback_snapshot_json: str
    evidence_decision_id: str
    controller_configuration_digest: str
    role_generation: int
    phase: str = "active"
    transaction_id: str | None = None
    incumbent_pair_json: str | None = None
    candidate_pair_json: str | None = None
    rollback_pair_json: str | None = None
    origin: str | None = None
    policy: str | None = None
    candidate_generation: int | None = None
    candidate_digest: str | None = None
    reason: str | None = None

    @staticmethod
    def _pair(value: str | None) -> ModelActivationPair | None:
        return None if value is None else ModelActivationPair.from_json(value)

    @property
    def incumbent_pair(self) -> ModelActivationPair | None:
        return self._pair(self.incumbent_pair_json)

    @property
    def candidate_pair(self) -> ModelActivationPair | None:
        return self._pair(self.candidate_pair_json)

    @property
    def rollback_pair(self) -> ModelActivationPair | None:
        return self._pair(self.rollback_pair_json)

    @property
    def active_pair(self) -> ModelActivationPair | None:
        return self.candidate_pair if self.phase == "active" else self.incumbent_pair


@dataclass(frozen=True, slots=True)
class ModelRollbackCommitOutcome:
    """One atomic rollback insert or the exact lifecycle already committed."""

    record: ModelEvidenceRecord
    inserted: bool


def _require_model_identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value.strip()


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
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as conn:
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
    clauses = ["schema_version IN (?, ?, ?)"]
    params: list[object] = [1, 2, MODEL_EVIDENCE_SCHEMA_VERSION]
    if session_id is not None:
        clauses.append("session_id=?")
        params.append(_require_model_identifier(session_id, "session_id"))
    if cook_id is not None:
        clauses.append("cook_id=?")
        params.append(_require_model_identifier(cook_id, "cook_id"))
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
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as conn:
        authority_row = conn.execute(
            """
                SELECT evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
                       model_digest, provenance_digest, schema_version, payload
                FROM model_evidence
                WHERE kind=? AND schema_version=?
                ORDER BY timestamp_ms DESC, evidence_id DESC
                LIMIT 1
                """,
            (EvidenceKind.CONFIDENCE_DECISION.value, MODEL_EVIDENCE_SCHEMA_VERSION),
        ).fetchone()
        if authority_row is None:
            raise ValueError("activation-authority-changed")
        authority = ModelEvidenceRecord.from_db_row(ModelEvidenceDbRow(*authority_row))
        authority_payload = authority.payload
        if (
            not isinstance(authority_payload, ConfidenceDecisionEvidence)
            or authority_payload.blocked
            or authority_payload.reason is not None
            or authority_payload.decision_id != payload.decision_id
            or authority.model_digest != validated.model_digest
            or authority.provenance_digest != validated.provenance_digest
            or authority.schema_version != validated.schema_version
        ):
            raise ValueError("activation-authority-changed")
        provenance_rows = conn.execute(
            """
                SELECT DISTINCT provenance_digest
                FROM model_evidence
                WHERE model_digest=? AND role_generation=? AND provenance_digest IS NOT NULL
                  AND schema_version=?
                """,
            (validated.model_digest, authority.role_generation, MODEL_EVIDENCE_SCHEMA_VERSION),
        ).fetchall()
        if {row[0] for row in provenance_rows} != {validated.provenance_digest}:
            raise ValueError("activation-authority-changed")
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


def _activation_state_row(connection: sqlite3.Connection):
    return connection.execute(
        """
        SELECT active_snapshot_json, rollback_snapshot_json, evidence_decision_id,
               controller_configuration_digest, role_generation, phase,
               transaction_id, incumbent_pair_json, candidate_pair_json,
               rollback_pair_json, origin, policy, candidate_generation,
               candidate_digest, reason
        FROM model_activation_state WHERE singleton=1
        """
    ).fetchone()


def commit_model_activation_phase(
    record,
    *,
    expected_phase=None,
    database_path: str | os.PathLike[str] | None = None,
) -> None:
    """Durably prepare or CAS one exact grey estimator/native-pair transaction."""
    phase = getattr(record.phase, "value", record.phase)
    expected_phase = None if expected_phase is None else getattr(expected_phase, "value", expected_phase)
    if phase not in {"prepared", "active", "aborted"}:
        raise ValueError(f"unknown activation phase: {phase!r}")
    if phase == "prepared" and expected_phase is not None:
        raise ValueError("prepared activation cannot have an expected phase")
    if phase != "prepared" and expected_phase != "prepared":
        raise ValueError("active or aborted activation requires expected prepared phase")

    incumbent_pair_json = json.dumps(record.incumbent.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    candidate_pair_json = json.dumps(record.candidate.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    rollback_pair_json = json.dumps(record.rollback.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    incumbent_snapshot_json = json.dumps(
        record.incumbent.to_dict()["configuration"],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    candidate_snapshot_json = json.dumps(
        record.candidate.to_dict()["configuration"],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as conn:
        state_row = _activation_state_row(conn)
        current = None if state_row is None else ModelActivationState(*state_row)
        if phase == "prepared":
            authority_row = conn.execute(
                """
                    SELECT evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
                           model_digest, provenance_digest, schema_version, payload
                    FROM model_evidence
                    WHERE kind=? AND schema_version=?
                    ORDER BY timestamp_ms DESC, evidence_id DESC
                    LIMIT 1
                    """,
                (EvidenceKind.CONFIDENCE_DECISION.value, MODEL_EVIDENCE_SCHEMA_VERSION),
            ).fetchone()
            if authority_row is None:
                raise ValueError("activation-authority-changed")
            authority = ModelEvidenceRecord.from_db_row(ModelEvidenceDbRow(*authority_row))
            payload = authority.payload
            if (
                not isinstance(payload, ConfidenceDecisionEvidence)
                or payload.blocked
                or payload.reason is not None
                or payload.decision_id != record.decision_id
                or authority.model_digest != record.candidate.model_digest
                or authority.provenance_digest != record.incumbent.model_digest
                or authority.role_generation != record.incumbent.role_generation
            ):
                raise ValueError("activation-authority-changed")
            provenance_rows = conn.execute(
                """
                    SELECT DISTINCT provenance_digest
                    FROM model_evidence
                    WHERE model_digest=? AND role_generation=? AND provenance_digest IS NOT NULL
                      AND schema_version=?
                    """,
                (
                    record.candidate.model_digest,
                    record.incumbent.role_generation,
                    MODEL_EVIDENCE_SCHEMA_VERSION,
                ),
            ).fetchall()
            if {row[0] for row in provenance_rows} != {record.incumbent.model_digest}:
                raise ValueError("activation-authority-changed")
            if current is not None:
                if (
                    current.phase == "prepared"
                    and current.transaction_id == record.transaction_id
                    and current.incumbent_pair_json == incumbent_pair_json
                    and current.candidate_pair_json == candidate_pair_json
                ):
                    return
                if current.phase not in ("active", "aborted") or current.active_pair != record.incumbent:
                    raise ValueError("activation-state-changed")
        else:
            if (
                current is None
                or current.phase != expected_phase
                or current.transaction_id != record.transaction_id
                or current.incumbent_pair_json != incumbent_pair_json
                or current.candidate_pair_json != candidate_pair_json
                or current.rollback_pair_json != rollback_pair_json
                or current.evidence_decision_id != record.decision_id
            ):
                raise ValueError("activation-state-changed")
            if phase == "active":
                authority_row = conn.execute(
                    """
                        SELECT evidence_id, session_id, cook_id, timestamp_ms, kind,
                               role_generation, model_digest, provenance_digest,
                               schema_version, payload
                        FROM model_evidence
                        WHERE kind=? AND schema_version=?
                        ORDER BY timestamp_ms DESC, evidence_id DESC
                        LIMIT 1
                        """,
                    (EvidenceKind.CONFIDENCE_DECISION.value, MODEL_EVIDENCE_SCHEMA_VERSION),
                ).fetchone()
                authority = (
                    None
                    if authority_row is None
                    else ModelEvidenceRecord.from_db_row(ModelEvidenceDbRow(*authority_row))
                )
                payload = None if authority is None else authority.payload
                if (
                    not isinstance(payload, ConfidenceDecisionEvidence)
                    or payload.blocked
                    or payload.reason is not None
                    or payload.decision_id != record.decision_id
                    or authority.model_digest != record.candidate.model_digest
                    or authority.provenance_digest != record.incumbent.model_digest
                    or authority.role_generation != record.incumbent.role_generation
                ):
                    raise ValueError("activation-authority-changed")

        active_pair = record.candidate if phase == "active" else record.incumbent
        active_snapshot_json = candidate_snapshot_json if phase == "active" else incumbent_snapshot_json
        conn.execute("DELETE FROM model_activation_state WHERE singleton=1")
        conn.execute(
            """
                INSERT INTO model_activation_state(
                    singleton, active_snapshot_json, rollback_snapshot_json,
                    evidence_decision_id, controller_configuration_digest,
                    role_generation, phase, transaction_id, incumbent_pair_json,
                    candidate_pair_json, rollback_pair_json, origin, policy,
                    candidate_generation, candidate_digest, reason
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            (
                active_snapshot_json,
                incumbent_snapshot_json,
                record.decision_id,
                record.candidate.ownership_digest,
                active_pair.role_generation,
                phase,
                record.transaction_id,
                incumbent_pair_json,
                candidate_pair_json,
                rollback_pair_json,
                getattr(record.origin, "value", record.origin),
                getattr(record.policy, "value", record.policy),
                record.candidate.candidate_generation,
                record.candidate.model_digest,
                record.reason,
            ),
        )


def commit_model_rollback(
    decision: ModelEvidenceRecord,
    *,
    expected_activation: ModelActivationState,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelRollbackCommitOutcome:
    """Atomically append one rollback only while its exact activation is current."""
    validated = ModelEvidenceRecord.model_validate_json(decision.model_dump_json())
    if validated.kind is not EvidenceKind.ROLLBACK or not isinstance(validated.payload, RollbackEvidence):
        raise ValueError("rollback commit requires rollback evidence")
    row = _validated_model_evidence_rows((validated,))[0]
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as conn:
        state_row = _activation_state_row(conn)
        current = None if state_row is None else ModelActivationState(*state_row)
        if current != expected_activation:
            raise ValueError("activation-state-changed")
        stored_rows = conn.execute(
            """
                SELECT evidence_id, session_id, cook_id, timestamp_ms, kind, role_generation,
                       model_digest, provenance_digest, schema_version, payload
                FROM model_evidence WHERE schema_version=? ORDER BY id
                """,
            (MODEL_EVIDENCE_SCHEMA_VERSION,),
        ).fetchall()
        records = tuple(ModelEvidenceRecord.from_db_row(ModelEvidenceDbRow(*stored_row)) for stored_row in stored_rows)
        if (
            expected_activation.phase == "active"
            and expected_activation.candidate_pair is not None
            and expected_activation.rollback_pair is not None
        ):
            active_digest = expected_activation.candidate_pair.model_digest
        else:
            activation = max(
                (
                    record
                    for record in records
                    if isinstance(record.payload, ActivationEvidence)
                    and record.payload.decision_id == expected_activation.evidence_decision_id
                    and record.payload.active_snapshot_json == expected_activation.active_snapshot_json
                    and record.payload.rollback_snapshot_json == expected_activation.rollback_snapshot_json
                    and record.payload.controller_configuration_digest
                    == expected_activation.controller_configuration_digest
                    and record.role_generation == expected_activation.role_generation
                    and record.model_digest is not None
                ),
                key=lambda record: (record.timestamp_ms, record.evidence_id),
                default=None,
            )
            if activation is None or activation.model_digest is None:
                raise ValueError("activation-lineage-missing")
            active_digest = activation.model_digest
        existing = max(
            (
                record
                for record in records
                if (
                    isinstance(record.payload, RollbackEvidence)
                    and record.payload.decision_id == expected_activation.evidence_decision_id
                    and record.role_generation == expected_activation.role_generation + 1
                    and record.model_digest == active_digest
                )
                or (
                    isinstance(record.payload, FallbackEvidence)
                    and record.payload.failed_generation == expected_activation.role_generation
                    and record.payload.failed_digest == active_digest
                    and record.role_generation == expected_activation.role_generation + 1
                    and record.model_digest == active_digest
                )
            ),
            key=lambda record: (record.timestamp_ms, record.evidence_id),
            default=None,
        )
        if existing is not None:
            return ModelRollbackCommitOutcome(existing, False)
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
        return ModelRollbackCommitOutcome(validated, True)


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
        row = _activation_state_row(connection)
    return None if row is None else ModelActivationState(*row)


def reset_model_evidence(*, database_path: str | os.PathLike[str] | None = None) -> None:
    """Explicitly delete every durable evidence row and activation state."""
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as conn:
        conn.execute("DELETE FROM model_activation_state")
        conn.execute("DELETE FROM model_evidence")


def invalidate_model_evidence_schema(*, database_path: str | os.PathLike[str] | None = None) -> None:
    """Invalidate current evidence explicitly; raw trace retention never calls this."""
    reset_model_evidence(database_path=database_path)
