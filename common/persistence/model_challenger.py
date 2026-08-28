"""Durable singleton authority for one resumable grey-model challenger."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

from common import datastore
from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    FrozenJsonObject,
    ModelFitLineage,
    trajectory_json_value,
)
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ChallengerRoundEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
)
from common.persistence.model_evidence import (
    ModelActivationState,
    _activation_state_row,
    _model_evidence_connection,
    append_model_evidence_in_transaction,
)
from controller.model_learning.activation import (
    ActivationPhase,
    GreyControlPairDescriptor,
    PreparedActivationRecord,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin

_CHALLENGER_PHASES = frozenset({"built", "evaluating", "qualified", "activating", "retired"})
_POLICY_BY_ORIGIN = {
    CandidateOrigin.PASSIVE_ONLINE: ActivationPolicy.PASSIVE_AUTO,
    CandidateOrigin.OPERATOR_CALIBRATION: ActivationPolicy.OPERATOR_REVIEWED,
    CandidateOrigin.COOK_REFIT: ActivationPolicy.COOK_REFIT,
}


class ModelChallengerConflictError(RuntimeError):
    """The singleton or its exact expected revision/lineage changed."""


class _CorruptModelChallenger(ValueError):
    def __init__(self, message: str, salvaged: ModelChallengerState | None = None):
        super().__init__(message)
        self.salvaged = salvaged


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value: object, name: str) -> int:
    normalized = _nonnegative_int(value, name)
    if normalized == 0:
        raise ValueError(f"{name} must be a positive integer")
    return normalized


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _optional_nonblank(value: object, name: str) -> str | None:
    return None if value is None else _nonblank(value, name)


def _owned_json_object(value: object, name: str) -> FrozenJsonObject:
    owned = trajectory_json_value(value)
    if not isinstance(owned, dict):
        raise TypeError(f"{name} must be a JSON object")
    return FrozenJsonObject(tuple(owned.items()))


@dataclass(frozen=True, slots=True)
class ModelChallengerState:
    """Strict immutable state and provenance for the sole durable challenger."""

    schema_version: int
    challenger_id: str
    revision: int
    phase: Literal["built", "evaluating", "qualified", "activating", "retired"]
    origin: CandidateOrigin
    policy: ActivationPolicy
    fit_corpus: FitCorpusIdentity
    fit_lineage: ModelFitLineage
    fit_preparation: Mapping[str, object]
    controller_configuration_digest: str
    incumbent: GreyControlPairDescriptor
    candidate: GreyControlPairDescriptor
    calibration_manifest: Mapping[str, object] | None
    evaluation_epoch: int
    evaluation_round: int
    consecutive_wins: int
    required_wins: int
    last_decision_id: str | None
    last_evidence_id: str | None
    activation_transaction_id: str | None
    retirement_reason: str | None
    created_ms: int
    updated_ms: int
    retired_ms: int | None

    def __post_init__(self) -> None:
        if self.schema_version != 1 or isinstance(self.schema_version, bool):
            raise ValueError("challenger schema_version must be 1")
        _nonblank(self.challenger_id, "challenger_id")
        _nonnegative_int(self.revision, "revision")
        if self.phase not in _CHALLENGER_PHASES:
            raise ValueError("invalid challenger phase")
        if not isinstance(self.origin, CandidateOrigin):
            raise TypeError("challenger origin must be a CandidateOrigin")
        if not isinstance(self.policy, ActivationPolicy):
            raise TypeError("challenger policy must be an ActivationPolicy")
        if _POLICY_BY_ORIGIN[self.origin] is not self.policy:
            raise ValueError("challenger origin-policy mismatch")
        if not isinstance(self.fit_corpus, FitCorpusIdentity):
            raise TypeError("challenger fit corpus must be a FitCorpusIdentity")
        if not isinstance(self.fit_lineage, ModelFitLineage):
            raise TypeError("challenger fit lineage must be a ModelFitLineage")
        if not isinstance(self.incumbent, GreyControlPairDescriptor) or not isinstance(
            self.candidate, GreyControlPairDescriptor
        ):
            raise TypeError("challenger requires exact grey pair descriptors")
        _digest(
            self.controller_configuration_digest,
            "controller configuration digest",
        )
        preparation = _owned_json_object(self.fit_preparation, "fit preparation")
        manifest = (
            None
            if self.calibration_manifest is None
            else _owned_json_object(self.calibration_manifest, "calibration manifest")
        )
        object.__setattr__(self, "fit_preparation", preparation)
        object.__setattr__(self, "calibration_manifest", manifest)

        if self.fit_lineage.fit_corpus != self.fit_corpus:
            raise ValueError("fit corpus and lineage corpus must agree exactly")
        if self.fit_lineage.fit_corpus_digest != self.fit_corpus.corpus_digest:
            raise ValueError("fit corpus lineage digest must agree exactly")
        if self.fit_lineage.parent_incumbent_digest != self.incumbent.model_digest:
            raise ValueError("fit lineage incumbent digest must match incumbent")
        if self.fit_lineage.parent_incumbent_generation != self.incumbent.role_generation:
            raise ValueError("fit lineage incumbent generation must match incumbent")
        if self.fit_lineage.candidate_generation != self.candidate.candidate_generation:
            raise ValueError("fit lineage candidate generation must match candidate")
        if self.fit_lineage.candidate_digest != self.candidate.model_digest:
            raise ValueError("fit lineage candidate digest must match candidate")
        if self.fit_lineage.trigger_origin != self.origin.value:
            raise ValueError("fit lineage origin must match challenger origin")
        if self.fit_lineage.result_status != "succeeded":
            raise ValueError("challenger requires a successful fit lineage")
        if preparation.get("request_id") != self.fit_lineage.request_id:
            raise ValueError("fit preparation request must match fit lineage")
        if preparation.get("accepted") is not True:
            raise ValueError("fit preparation must be accepted")
        if preparation.get("candidate_digest") != self.candidate.model_digest:
            raise ValueError("fit preparation candidate must match challenger")

        _nonnegative_int(self.evaluation_epoch, "evaluation_epoch")
        _nonnegative_int(self.evaluation_round, "evaluation_round")
        _nonnegative_int(self.consecutive_wins, "consecutive_wins")
        _positive_int(self.required_wins, "required_wins")
        if self.consecutive_wins > self.required_wins:
            raise ValueError("challenger wins cannot exceed required wins")
        _optional_nonblank(self.last_decision_id, "last_decision_id")
        _optional_nonblank(self.last_evidence_id, "last_evidence_id")
        if (self.last_decision_id is None) != (self.last_evidence_id is None):
            raise ValueError("challenger decision and evidence high-water must coexist")
        if self.evaluation_round > 0 and self.last_decision_id is None:
            raise ValueError("completed evaluation round requires decision evidence")
        if self.phase == "built" and (
            self.evaluation_epoch or self.evaluation_round or self.consecutive_wins or self.last_decision_id is not None
        ):
            raise ValueError("built challenger cannot contain evaluation progress")
        if self.phase in {"qualified", "activating"}:
            if self.consecutive_wins < self.required_wins:
                raise ValueError("qualified challenger requires all consecutive wins")
            if self.last_decision_id is None:
                raise ValueError("qualified challenger requires decision evidence")
        if self.phase == "activating":
            _nonblank(self.activation_transaction_id, "activation transaction")
        elif self.phase != "retired" and self.activation_transaction_id is not None:
            raise ValueError("activation transaction is legal only while activating")
        if self.phase == "retired":
            _nonblank(self.retirement_reason, "retirement reason")
            _nonnegative_int(self.retired_ms, "retired_ms")
        elif self.retirement_reason is not None or self.retired_ms is not None:
            raise ValueError("retirement fields are legal only for a retired challenger")
        _nonnegative_int(self.created_ms, "created_ms")
        _nonnegative_int(self.updated_ms, "updated_ms")
        if self.updated_ms < self.created_ms:
            raise ValueError("challenger updated_ms cannot precede created_ms")


def _corpus_dict(value: FitCorpusIdentity) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "corpus_revision": value.corpus_revision,
        "fit_partition_digest": value.fit_partition_digest,
        "slices": [
            {
                "segment_id": item.segment_id,
                "through_ordinal": item.through_ordinal,
                "prefix_digest": item.prefix_digest,
                "pre_roll_count": item.pre_roll_count,
                "scored_count": item.scored_count,
            }
            for item in value.slices
        ],
        "corpus_digest": value.corpus_digest,
    }


def _lineage_dict(value: ModelFitLineage) -> dict[str, object]:
    return {
        "request_id": value.request_id,
        "parent_incumbent_digest": value.parent_incumbent_digest,
        "parent_incumbent_generation": value.parent_incumbent_generation,
        "candidate_generation": value.candidate_generation,
        "fit_corpus": _corpus_dict(value.fit_corpus),
        "fit_corpus_digest": value.fit_corpus_digest,
        "trigger_origin": value.trigger_origin,
        "result_status": value.result_status,
        "candidate_digest": value.candidate_digest,
    }


def _state_dict(state: ModelChallengerState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "challenger_id": state.challenger_id,
        "revision": state.revision,
        "phase": state.phase,
        "origin": state.origin.value,
        "policy": state.policy.value,
        "fit_corpus": _corpus_dict(state.fit_corpus),
        "fit_lineage": _lineage_dict(state.fit_lineage),
        "fit_preparation": trajectory_json_value(state.fit_preparation),
        "controller_configuration_digest": state.controller_configuration_digest,
        "incumbent": state.incumbent.to_dict(),
        "candidate": state.candidate.to_dict(),
        "calibration_manifest": (
            None if state.calibration_manifest is None else trajectory_json_value(state.calibration_manifest)
        ),
        "evaluation_epoch": state.evaluation_epoch,
        "evaluation_round": state.evaluation_round,
        "consecutive_wins": state.consecutive_wins,
        "required_wins": state.required_wins,
        "last_decision_id": state.last_decision_id,
        "last_evidence_id": state.last_evidence_id,
        "activation_transaction_id": state.activation_transaction_id,
        "retirement_reason": state.retirement_reason,
        "created_ms": state.created_ms,
        "updated_ms": state.updated_ms,
        "retired_ms": state.retired_ms,
    }


def _canonical_state_json(state: ModelChallengerState) -> str:
    return json.dumps(
        _state_dict(state),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    return value


def _corpus_from_dict(value: object) -> FitCorpusIdentity:
    data = _mapping(value, "fit corpus")
    slices = data.get("slices")
    if not isinstance(slices, list):
        raise TypeError("fit corpus slices must be an array")
    return FitCorpusIdentity(
        schema_version=data.get("schema_version"),
        corpus_revision=data.get("corpus_revision"),
        fit_partition_digest=data.get("fit_partition_digest"),
        slices=tuple(FitCorpusSlice(**_mapping(item, "fit corpus slice")) for item in slices),
        corpus_digest=data.get("corpus_digest"),
    )


def _lineage_from_dict(value: object) -> ModelFitLineage:
    data = _mapping(value, "fit lineage")
    return ModelFitLineage(
        request_id=data.get("request_id"),
        parent_incumbent_digest=data.get("parent_incumbent_digest"),
        parent_incumbent_generation=data.get("parent_incumbent_generation"),
        candidate_generation=data.get("candidate_generation"),
        fit_corpus=_corpus_from_dict(data.get("fit_corpus")),
        fit_corpus_digest=data.get("fit_corpus_digest"),
        trigger_origin=data.get("trigger_origin"),
        result_status=data.get("result_status"),
        candidate_digest=data.get("candidate_digest"),
    )


def _state_from_dict(value: object) -> ModelChallengerState:
    data = _mapping(value, "challenger state")
    return ModelChallengerState(
        schema_version=data.get("schema_version"),
        challenger_id=data.get("challenger_id"),
        revision=data.get("revision"),
        phase=data.get("phase"),
        origin=CandidateOrigin(data.get("origin")),
        policy=ActivationPolicy(data.get("policy")),
        fit_corpus=_corpus_from_dict(data.get("fit_corpus")),
        fit_lineage=_lineage_from_dict(data.get("fit_lineage")),
        fit_preparation=_mapping(data.get("fit_preparation"), "fit preparation"),
        controller_configuration_digest=data.get("controller_configuration_digest"),
        incumbent=GreyControlPairDescriptor.from_dict(_mapping(data.get("incumbent"), "incumbent")),
        candidate=GreyControlPairDescriptor.from_dict(_mapping(data.get("candidate"), "candidate")),
        calibration_manifest=(
            None
            if data.get("calibration_manifest") is None
            else _mapping(data.get("calibration_manifest"), "calibration manifest")
        ),
        evaluation_epoch=data.get("evaluation_epoch"),
        evaluation_round=data.get("evaluation_round"),
        consecutive_wins=data.get("consecutive_wins"),
        required_wins=data.get("required_wins"),
        last_decision_id=data.get("last_decision_id"),
        last_evidence_id=data.get("last_evidence_id"),
        activation_transaction_id=data.get("activation_transaction_id"),
        retirement_reason=data.get("retirement_reason"),
        created_ms=data.get("created_ms"),
        updated_ms=data.get("updated_ms"),
        retired_ms=data.get("retired_ms"),
    )


def _stored_row(connection: sqlite3.Connection):
    return connection.execute(
        """
        SELECT challenger_id, revision, phase, schema_version, state_json, updated_ms
        FROM model_challenger_state WHERE singleton=1
        """
    ).fetchone()


def _decode_stored_row(row: tuple[object, ...]) -> ModelChallengerState:
    try:
        decoded = json.loads(row[4])
        state = _state_from_dict(decoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise _CorruptModelChallenger("corrupt challenger state") from error
    if (
        row[0] != state.challenger_id
        or row[1] != state.revision
        or row[2] != state.phase
        or row[3] != state.schema_version
        or row[5] != state.updated_ms
        or row[4] != _canonical_state_json(state)
    ):
        raise _CorruptModelChallenger("corrupt challenger projection", state)
    return state


def _read_in_transaction(connection: sqlite3.Connection) -> ModelChallengerState | None:
    row = _stored_row(connection)
    return None if row is None else _decode_stored_row(row)


def _write_state(
    connection: sqlite3.Connection,
    state: ModelChallengerState,
    *,
    insert: bool,
    expected_revision: int | None = None,
) -> None:
    values = (
        state.challenger_id,
        state.revision,
        state.phase,
        state.schema_version,
        _canonical_state_json(state),
        state.updated_ms,
    )
    if insert:
        connection.execute(
            """
            INSERT INTO model_challenger_state(
                singleton, challenger_id, revision, phase, schema_version,
                state_json, updated_ms
            ) VALUES(1, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        return
    cursor = connection.execute(
        """
        UPDATE model_challenger_state
        SET challenger_id=?, revision=?, phase=?, schema_version=?,
            state_json=?, updated_ms=?
        WHERE singleton=1 AND revision=?
        """,
        (*values, expected_revision),
    )
    if cursor.rowcount != 1:
        raise ModelChallengerConflictError("model challenger revision changed")


def _same_lineage(current: ModelChallengerState, replacement: ModelChallengerState) -> bool:
    immutable_names = (
        "schema_version",
        "challenger_id",
        "origin",
        "policy",
        "fit_corpus",
        "fit_lineage",
        "fit_preparation",
        "controller_configuration_digest",
        "incumbent",
        "candidate",
        "calibration_manifest",
        "required_wins",
        "created_ms",
    )
    return all(getattr(current, name) == getattr(replacement, name) for name in immutable_names)


def create_model_challenger(
    state: ModelChallengerState,
    *,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState:
    validated = _state_from_dict(_state_dict(state))
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        current = _read_in_transaction(transaction)
        if current is None:
            _write_state(transaction, validated, insert=True)
            return validated
        if current == validated:
            return current
        if current.phase == "retired":
            transaction.execute(
                "DELETE FROM model_challenger_state WHERE singleton=1 AND revision=?",
                (current.revision,),
            )
            _write_state(transaction, validated, insert=True)
            return validated
        raise ModelChallengerConflictError("a different model challenger already exists")


def read_model_challenger(*, database_path: str | os.PathLike[str] | None = None) -> ModelChallengerState | None:
    with _model_evidence_connection(database_path) as connection:
        return _read_in_transaction(connection)


def compare_and_swap_model_challenger(
    *,
    expected_revision: int,
    replacement: ModelChallengerState,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState:
    _nonnegative_int(expected_revision, "expected_revision")
    validated = _state_from_dict(_state_dict(replacement))
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        current = _read_in_transaction(transaction)
        if current is None:
            raise ModelChallengerConflictError("model challenger is absent")
        if current == validated and current.revision == expected_revision + 1:
            return current
        if current.revision != expected_revision:
            raise ModelChallengerConflictError("model challenger revision changed")
        if validated.revision != expected_revision + 1:
            raise ModelChallengerConflictError("replacement revision is not the next revision")
        if not _same_lineage(current, validated):
            raise ModelChallengerConflictError("model challenger lineage changed")
        _write_state(
            transaction,
            validated,
            insert=False,
            expected_revision=expected_revision,
        )
        return validated


def complete_model_challenger_round(
    *,
    expected_revision: int,
    evidence: ModelEvidenceRecord,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState:
    _nonnegative_int(expected_revision, "expected_revision")
    validated_evidence = ModelEvidenceRecord.model_validate_json(evidence.model_dump_json())
    payload = validated_evidence.payload
    if (
        validated_evidence.schema_version != MODEL_EVIDENCE_SCHEMA_VERSION
        or validated_evidence.kind is not EvidenceKind.CHALLENGER_ROUND
        or not isinstance(payload, ChallengerRoundEvidence)
    ):
        raise ValueError("challenger progress requires current challenger-round evidence")
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        current = _read_in_transaction(transaction)
        if current is None:
            raise ModelChallengerConflictError("model challenger is absent")
        if (
            current.revision == expected_revision + 1
            and current.last_evidence_id == validated_evidence.evidence_id
            and current.last_decision_id == payload.decision_id
        ):
            return current
        if current.revision != expected_revision:
            raise ModelChallengerConflictError("model challenger revision changed")
        if current.phase != "evaluating":
            raise ModelChallengerConflictError("model challenger is not evaluating")
        if (
            payload.challenger_id != current.challenger_id
            or payload.evaluation_epoch != current.evaluation_epoch
            or payload.evaluation_round != current.evaluation_round + 1
            or validated_evidence.model_digest != current.candidate.model_digest
            or validated_evidence.provenance_digest != current.incumbent.model_digest
            or validated_evidence.role_generation != current.incumbent.role_generation
        ):
            raise ModelChallengerConflictError("challenger round lineage changed")
        append_model_evidence_in_transaction(transaction, [validated_evidence])
        if payload.accepted:
            progressed = replace(
                current,
                revision=current.revision + 1,
                evaluation_round=payload.evaluation_round,
                consecutive_wins=current.consecutive_wins + 1,
                last_decision_id=payload.decision_id,
                last_evidence_id=validated_evidence.evidence_id,
                updated_ms=validated_evidence.timestamp_ms,
            )
        else:
            progressed = replace(
                current,
                revision=current.revision + 1,
                phase="retired",
                evaluation_round=payload.evaluation_round,
                consecutive_wins=0,
                last_decision_id=payload.decision_id,
                last_evidence_id=validated_evidence.evidence_id,
                retirement_reason="evaluation-lost",
                updated_ms=validated_evidence.timestamp_ms,
                retired_ms=validated_evidence.timestamp_ms,
            )
        _write_state(
            transaction,
            progressed,
            insert=False,
            expected_revision=expected_revision,
        )
        return progressed


def qualify_model_challenger(
    *,
    expected_revision: int,
    qualified_ms: int,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState:
    _nonnegative_int(expected_revision, "expected_revision")
    _nonnegative_int(qualified_ms, "qualified_ms")
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        current = _read_in_transaction(transaction)
        if current is None:
            raise ModelChallengerConflictError("model challenger is absent")
        if (
            current.revision == expected_revision + 1
            and current.phase == "qualified"
            and current.updated_ms == qualified_ms
        ):
            return current
        if current.revision != expected_revision or current.phase != "evaluating":
            raise ModelChallengerConflictError("model challenger cannot be qualified")
        if current.consecutive_wins < current.required_wins:
            raise ModelChallengerConflictError("model challenger lacks required wins")
        qualified = replace(
            current,
            revision=current.revision + 1,
            phase="qualified",
            updated_ms=qualified_ms,
        )
        _write_state(
            transaction,
            qualified,
            insert=False,
            expected_revision=expected_revision,
        )
        return qualified


def retire_model_challenger(
    *,
    expected_revision: int,
    reason: str,
    retired_ms: int,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState:
    _nonnegative_int(expected_revision, "expected_revision")
    _nonblank(reason, "retirement reason")
    _nonnegative_int(retired_ms, "retired_ms")
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        current = _read_in_transaction(transaction)
        if current is None:
            raise ModelChallengerConflictError("model challenger is absent")
        if current.phase == "retired":
            if (
                current.retirement_reason == reason
                and current.retired_ms == retired_ms
                and current.revision in {expected_revision, expected_revision + 1}
            ):
                return current
            raise ModelChallengerConflictError("model challenger is already retired")
        if current.revision != expected_revision:
            raise ModelChallengerConflictError("model challenger revision changed")
        retired = replace(
            current,
            revision=current.revision + 1,
            phase="retired",
            retirement_reason=reason,
            updated_ms=retired_ms,
            retired_ms=retired_ms,
        )
        _write_state(
            transaction,
            retired,
            insert=False,
            expected_revision=expected_revision,
        )
        return retired


def _activation_json(record: PreparedActivationRecord) -> tuple[str, ...]:
    def encoded(value: object) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    return (
        encoded(record.incumbent.to_dict()["configuration"]),
        encoded(record.incumbent.to_dict()["configuration"]),
        encoded(record.incumbent.to_dict()),
        encoded(record.candidate.to_dict()),
        encoded(record.rollback.to_dict()),
    )


def prepare_model_challenger_activation(
    *,
    expected_revision: int,
    activation: PreparedActivationRecord,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState:
    _nonnegative_int(expected_revision, "expected_revision")
    if not isinstance(activation, PreparedActivationRecord):
        raise TypeError("activation must be a PreparedActivationRecord")
    if activation.phase is not ActivationPhase.PREPARED:
        raise ValueError("challenger activation must be PREPARED")
    active_json, rollback_json, incumbent_json, candidate_json, rollback_pair_json = _activation_json(activation)
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        current = _read_in_transaction(transaction)
        if current is None:
            raise ModelChallengerConflictError("model challenger is absent")
        if (
            current.revision == expected_revision + 1
            and current.phase == "activating"
            and current.activation_transaction_id == activation.transaction_id
        ):
            durable = _activation_state_row(transaction)
            if durable is not None and ModelActivationState(*durable).phase == "prepared":
                return current
        if current.revision != expected_revision or current.phase != "qualified":
            raise ModelChallengerConflictError("model challenger is not qualified")
        if (
            activation.incumbent != current.incumbent
            or activation.candidate != current.candidate
            or activation.rollback != current.incumbent
            or activation.origin is not current.origin
            or activation.policy is not current.policy
            or activation.decision_id != current.last_decision_id
        ):
            raise ModelChallengerConflictError("activation lineage changed")
        existing_row = _activation_state_row(transaction)
        if existing_row is not None:
            existing = ModelActivationState(*existing_row)
            if existing.phase == "prepared":
                raise ModelChallengerConflictError("another activation is prepared")
            if existing.active_pair is not None and existing.active_pair != current.incumbent:
                raise ModelChallengerConflictError("active model authority changed")
            transaction.execute("DELETE FROM model_activation_state WHERE singleton=1")
        transaction.execute(
            """
            INSERT INTO model_activation_state(
                singleton, active_snapshot_json, rollback_snapshot_json,
                evidence_decision_id, controller_configuration_digest,
                role_generation, phase, transaction_id, incumbent_pair_json,
                candidate_pair_json, rollback_pair_json, origin, policy,
                candidate_generation, candidate_digest, reason
            ) VALUES(1, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                active_json,
                rollback_json,
                activation.decision_id,
                activation.candidate.ownership_digest,
                activation.incumbent.role_generation,
                activation.transaction_id,
                incumbent_json,
                candidate_json,
                rollback_pair_json,
                activation.origin.value,
                activation.policy.value,
                activation.candidate.candidate_generation,
                activation.candidate.model_digest,
            ),
        )
        activating = replace(
            current,
            revision=current.revision + 1,
            phase="activating",
            activation_transaction_id=activation.transaction_id,
            updated_ms=activation.timestamp_ms,
        )
        _write_state(
            transaction,
            activating,
            insert=False,
            expected_revision=expected_revision,
        )
        return activating


def abort_model_challenger_activation(
    *,
    expected_revision: int,
    activation_transaction_id: str,
    reason: str,
    retired_ms: int,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState:
    """Atomically abort one exact PREPARED handoff and retire its challenger."""

    _nonnegative_int(expected_revision, "expected_revision")
    _nonblank(activation_transaction_id, "activation transaction")
    _nonblank(reason, "retirement reason")
    _nonnegative_int(retired_ms, "retired_ms")
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        current = _read_in_transaction(transaction)
        if (
            current is None
            or current.revision != expected_revision
            or current.phase != "activating"
            or current.activation_transaction_id != activation_transaction_id
        ):
            raise ModelChallengerConflictError("model challenger activation authority changed")
        activation_row = _activation_state_row(transaction)
        activation = None if activation_row is None else ModelActivationState(*activation_row)
        if (
            activation is None
            or activation.phase != "prepared"
            or activation.transaction_id != activation_transaction_id
            or activation.candidate_digest != current.candidate.model_digest
            or activation.incumbent_pair != current.incumbent
        ):
            raise ModelChallengerConflictError("prepared activation authority changed")
        cursor = transaction.execute(
            """
            UPDATE model_activation_state
            SET phase='aborted', reason=?
            WHERE singleton=1 AND phase='prepared' AND transaction_id=?
            """,
            (reason, activation_transaction_id),
        )
        if cursor.rowcount != 1:
            raise ModelChallengerConflictError("prepared activation authority changed")
        retired = _retired_state(current, reason=reason, retired_ms=retired_ms)
        _write_state(
            transaction,
            retired,
            insert=False,
            expected_revision=expected_revision,
        )
        return retired


def _retired_state(state: ModelChallengerState, *, reason: str, retired_ms: int) -> ModelChallengerState:
    if state.phase == "retired":
        return state
    return replace(
        state,
        revision=state.revision + 1,
        phase="retired",
        retirement_reason=reason,
        updated_ms=retired_ms,
        retired_ms=retired_ms,
    )


def recover_model_challenger(
    *,
    incumbent: GreyControlPairDescriptor,
    candidate: GreyControlPairDescriptor,
    controller_configuration_digest: str,
    fit_corpus: FitCorpusIdentity,
    calibration_manifest: Mapping[str, object] | None,
    recovered_ms: int,
    database_path: str | os.PathLike[str] | None = None,
) -> ModelChallengerState | None:
    """Resume exact complete progress in a fresh epoch or retire it durably."""

    _nonnegative_int(recovered_ms, "recovered_ms")
    _digest(controller_configuration_digest, "controller configuration digest")
    with _model_evidence_connection(database_path) as connection, datastore.transaction(connection) as transaction:
        row = _stored_row(transaction)
        if row is None:
            return None
        corrupt = False
        try:
            current = _decode_stored_row(row)
        except _CorruptModelChallenger as error:
            current = error.salvaged
            corrupt = True
        if current is None:
            transaction.execute("DELETE FROM model_challenger_state WHERE singleton=1")
            return None
        if corrupt:
            retired = _retired_state(
                current,
                reason="corrupt-challenger",
                retired_ms=recovered_ms,
            )
            transaction.execute("DELETE FROM model_challenger_state WHERE singleton=1")
            _write_state(transaction, retired, insert=True)
            return None
        if current.phase == "retired":
            return None

        activation_row = _activation_state_row(transaction)
        activation = None if activation_row is None else ModelActivationState(*activation_row)
        if activation is not None and activation.phase == "prepared":
            reason = "prepared activation interrupted during challenger recovery"
            transaction.execute(
                """
                UPDATE model_activation_state
                SET phase='aborted', reason=?
                WHERE singleton=1 AND phase='prepared'
                """,
                (reason,),
            )
            linked = current.activation_transaction_id == activation.transaction_id or (
                activation.candidate_digest == current.candidate.model_digest
                and activation.incumbent_pair == current.incumbent
            )
            if linked:
                retired = _retired_state(
                    current,
                    reason=reason,
                    retired_ms=recovered_ms,
                )
                _write_state(
                    transaction,
                    retired,
                    insert=False,
                    expected_revision=current.revision,
                )
                return None
        if current.phase == "activating":
            retired = _retired_state(
                current,
                reason="activation-reference-mismatch",
                retired_ms=recovered_ms,
            )
            _write_state(
                transaction,
                retired,
                insert=False,
                expected_revision=current.revision,
            )
            return None

        supplied_manifest = (
            None if calibration_manifest is None else _owned_json_object(calibration_manifest, "calibration manifest")
        )
        mismatches = (
            (current.incumbent != incumbent, "incumbent-changed"),
            (current.candidate != candidate, "candidate-changed"),
            (
                current.controller_configuration_digest != controller_configuration_digest,
                "configuration-changed",
            ),
            (current.fit_corpus != fit_corpus, "corpus-changed"),
            (
                current.calibration_manifest != supplied_manifest,
                "calibration-manifest-changed",
            ),
        )
        mismatch_reason = next((reason for changed, reason in mismatches if changed), None)
        if mismatch_reason is not None:
            retired = _retired_state(
                current,
                reason=mismatch_reason,
                retired_ms=recovered_ms,
            )
            _write_state(
                transaction,
                retired,
                insert=False,
                expected_revision=current.revision,
            )
            return None
        if current.phase not in {"built", "evaluating", "qualified"}:
            retired = _retired_state(
                current,
                reason="corrupt-challenger",
                retired_ms=recovered_ms,
            )
            _write_state(
                transaction,
                retired,
                insert=False,
                expected_revision=current.revision,
            )
            return None
        resumed_phase = "evaluating" if current.phase == "built" else current.phase
        recovered = replace(
            current,
            revision=current.revision + 1,
            phase=resumed_phase,
            evaluation_epoch=current.evaluation_epoch + 1,
            evaluation_round=0,
            updated_ms=recovered_ms,
        )
        _write_state(
            transaction,
            recovered,
            insert=False,
            expected_revision=current.revision,
        )
        return recovered
