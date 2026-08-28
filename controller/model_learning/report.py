"""Canonical cached projection of every durable and live grey-learning input."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import cast

from common.cook_diagnostics import ControllerLearningReport
from common.model_evidence import (
    MODEL_EVIDENCE_SCHEMA_VERSION,
    ActivationLifecycleEvidence,
    CandidateAssessmentEvidence,
    ConfidenceDecisionEvidence,
    FitLifecycleEvidence,
    LearningFailureEvidence,
    ModelEvidenceRecord,
    SchemaInvalidationEvidence,
)
from common.persistence.protocols import JsonValue
from common.web_contracts.learning import ModelEvidenceReport

from .contracts import ActivationPolicy, CandidateOrigin, CheckStatus, FitStatus, LearningStatus

REPORT_SCHEMA_VERSION = 2
ARTIFACT_SCHEMA = "pifire-grey-learning-report/v2"
_REPORT_CACHE_MAX_ENTRIES = 8
_REPORT_CACHE: OrderedDict[str, LearningReport] = OrderedDict()
_REPORT_CACHE_LOCK = threading.Lock()
_LEGACY_COOK_REFIT_OUTCOMES = frozenset(
    {
        "disabled",
        "insufficient",
        "rejected",
        "failed",
        "ready-for-review",
        "accepted-next-cook",
        "checkpoint-failure",
    }
)


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ModelEvidenceRecord):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("report mappings must have string keys")
        return {key: _json_value(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"unsupported report value {type(value).__name__}")


def _mapping(value: object, name: str) -> dict[str, object]:
    if value is None:
        return {}
    owned = _json_value(value)
    if not isinstance(owned, dict):
        raise TypeError(f"{name} must be an object")
    return owned


def _enum_value(value: object, enum_type: type[Enum], name: str) -> str:
    normalized = value.value if isinstance(value, enum_type) else value
    try:
        return cast(Enum, enum_type(normalized)).value  # type: ignore[call-arg]
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid {name}") from error


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _ledger_order(record: ModelEvidenceRecord) -> tuple[int, str]:
    return (record.timestamp_ms, record.evidence_id)


def _latest(records: Sequence[ModelEvidenceRecord], payload_type: type):
    matches = [record for record in records if isinstance(record.payload, payload_type)]
    return max(matches, key=_ledger_order) if matches else None


def _superseded_invalidation(
    records: Sequence[ModelEvidenceRecord],
    invalidation: ModelEvidenceRecord,
) -> bool:
    """Report whether the ledger recorded any later evidence of the current schema."""

    return any(
        not isinstance(record.payload, SchemaInvalidationEvidence)
        and _ledger_order(record) > _ledger_order(invalidation)
        for record in records
    )


def _latest_payload[PayloadT](
    records: Sequence[ModelEvidenceRecord],
    payload_type: type[PayloadT],
) -> PayloadT | None:
    record = _latest(records, payload_type)
    return None if record is None else cast(PayloadT, record.payload)


def _pair_digest(activation: Mapping[str, object], name: str):
    value = activation.get(name)
    if isinstance(value, Mapping):
        digest = value.get("model_digest")
        return digest if isinstance(digest, str) else None
    json_value = activation.get(f"{name}_json")
    if isinstance(json_value, str):
        try:
            decoded = json.loads(json_value)
        except TypeError, ValueError, json.JSONDecodeError:
            return None
        if isinstance(decoded, Mapping):
            digest = decoded.get("model_digest")
            return digest if isinstance(digest, str) else None
    return None


def _candidate_identity(
    authority: Mapping[str, object],
) -> tuple[str, int] | None:
    digest = authority.get("candidate_digest")
    generation = authority.get("candidate_generation")
    if not isinstance(digest, str) or isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        return None
    return digest, generation


def _validated_checkpoint(checkpoint: dict[str, object]) -> dict[str, object]:
    from controller.mpc_snapshot import migrate_grey_learning_snapshot

    return migrate_grey_learning_snapshot(checkpoint)


def _challenger_projection(value: object) -> dict[str, object]:
    if value is None:
        return {}
    from common.persistence.model_challenger import ModelChallengerState

    if not isinstance(value, ModelChallengerState):
        raise TypeError("challenger_state must be a ModelChallengerState")
    preparation = _json_value(value.fit_preparation)
    if not isinstance(preparation, dict):
        raise TypeError("challenger fit preparation must be an object")
    window = preparation.get("window")
    configuration = _json_value(value.candidate.configuration)
    if not isinstance(configuration, dict):
        raise TypeError("challenger candidate configuration must be an object")
    nested_parameters = configuration.get("parameters")
    parameter_source = nested_parameters if isinstance(nested_parameters, dict) else configuration
    candidate_parameters = {name: parameter_source[name] for name in ("C_c", "K_Q", "T_amb", "h_amb", "sigma", "theta")}
    candidate_parameters["n_delay"] = configuration.get(
        "n_delay",
        configuration.get("delay_states"),
    )
    return {
        "challenger_id": value.challenger_id,
        "revision": value.revision,
        "phase": value.phase,
        "origin": value.origin.value,
        "policy": value.policy.value,
        "candidate_digest": value.candidate.model_digest,
        "candidate_generation": value.candidate.candidate_generation,
        "candidate_parameters": candidate_parameters,
        "incumbent_digest": value.incumbent.model_digest,
        "role_generation": value.incumbent.role_generation,
        "decision_id": value.last_decision_id,
        "window": window,
    }


@dataclass(frozen=True, slots=True)
class LearningReport:
    """Immutable canonical report bytes safe to cache and serve directly."""

    payload_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        decoded = json.loads(self.payload_bytes)
        if not isinstance(decoded, dict):
            raise TypeError("learning report root is not an object")
        return cast(dict[str, object], decoded)

    def to_dict(self) -> dict[str, object]:
        return self.as_dict()

    @property
    def revision(self) -> str:
        value = self.as_dict().get("revision")
        if not isinstance(value, str):
            raise TypeError("learning report revision is missing")
        return value


def build_learning_report(
    evidence: Sequence[ModelEvidenceRecord],
    *,
    activation_state: object,
    live_status: object,
    checkpoint_required: bool = False,
    calibration_command_high_water: int,
    checkpoint: object = None,
    challenger_state: object = None,
) -> LearningReport:
    """Project ledger, durable authorities, live phases, and calibration once."""

    records = tuple(evidence)
    if not all(isinstance(record, ModelEvidenceRecord) for record in records):
        raise TypeError("evidence must contain ModelEvidenceRecord values")
    activation = _mapping(activation_state, "activation_state")
    live = _mapping(live_status, "live_status")
    checkpoint_map = _mapping(checkpoint, "checkpoint")
    command_high_water = _nonnegative_int(
        calibration_command_high_water,
        "calibration_command_high_water",
    )
    current_records = tuple(record for record in records if record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION)

    errors: list[str] = []
    schema_invalidated = False
    if checkpoint_map:
        try:
            checkpoint_map = _validated_checkpoint(checkpoint_map)
        except TypeError, ValueError:
            errors.append("checkpoint-schema-invalid")
            schema_invalidated = True
    elif checkpoint_required or (not activation and not live):
        errors.append("checkpoint-missing")
    challenger = _challenger_projection(challenger_state)
    if challenger.get("phase") == "retired":
        challenger = {}
    challenger_reference = checkpoint_map.get("challenger_authority")
    expected_reference = (
        None
        if not challenger
        else {
            "challenger_id": challenger["challenger_id"],
            "revision": challenger["revision"],
        }
    )
    if challenger_reference != expected_reference:
        if challenger_reference is not None or challenger:
            errors.append("challenger-reference-mismatch")
        challenger = {}

    try:
        status = _enum_value(
            live.get("status", LearningStatus.COLLECTING),
            LearningStatus,
            "learning status",
        )
    except ValueError:
        status = LearningStatus.ERROR.value
        errors.append("live-status-invalid")
    try:
        fit_status = _enum_value(
            live.get("fit_status", FitStatus.IDLE),
            FitStatus,
            "fit status",
        )
    except ValueError:
        fit_status = FitStatus.FAILED.value
        errors.append("live-fit-status-invalid")

    cook_refit_value = checkpoint_map.get("cook_refit", {"status": FitStatus.IDLE.value, "latest": None})
    if not isinstance(cook_refit_value, Mapping) or set(cook_refit_value) != {"status", "latest"}:
        raise ValueError("invalid cook_refit")
    cook_refit_status = _enum_value(
        cook_refit_value["status"],
        FitStatus,
        "cook_refit status",
    )
    cook_refit_latest = cook_refit_value["latest"]
    if cook_refit_latest is not None and (
        not isinstance(cook_refit_latest, str) or cook_refit_latest not in _LEGACY_COOK_REFIT_OUTCOMES
    ):
        raise ValueError("invalid cook_refit latest")
    if cook_refit_latest is None:
        cook_refit_authorization = "not-run"
    elif cook_refit_latest == "ready-for-review":
        cook_refit_authorization = "operator-review"
    else:
        cook_refit_authorization = "blocked"

    identities = checkpoint_map.get("identities")
    identities = identities if isinstance(identities, Mapping) else {}
    challenger_candidate_identity = _candidate_identity(challenger)
    live_candidate_identity = _candidate_identity(live)
    activation_candidate_identity = _candidate_identity(activation)
    current_candidate_identities = tuple(
        identity
        for identity in (
            challenger_candidate_identity,
            live_candidate_identity,
        )
        if identity is not None
    )
    activation_matches_candidate = activation_candidate_identity is not None and all(
        activation_candidate_identity == identity for identity in current_candidate_identities
    )
    activation_authority = activation if not current_candidate_identities or activation_matches_candidate else {}
    phase = activation_authority.get(
        "phase",
        live.get("activation_phase", "aborted"),
    )
    active_pair_digest = (
        _pair_digest(activation_authority, "candidate_pair")
        if phase == "active"
        else _pair_digest(activation_authority, "incumbent_pair")
    )
    incumbent_digest = challenger.get("incumbent_digest")
    if not isinstance(incumbent_digest, str):
        incumbent_digest = activation_authority.get("incumbent_digest")
    if not isinstance(incumbent_digest, str):
        incumbent_digest = _pair_digest(activation_authority, "incumbent_pair")
    if not isinstance(incumbent_digest, str):
        incumbent_digest = identities.get("active_digest")
    candidate_digest = challenger.get("candidate_digest")
    if not isinstance(candidate_digest, str):
        candidate_digest = activation_authority.get("candidate_digest")
    if not isinstance(candidate_digest, str):
        candidate_digest = live.get("candidate_digest")
    active_digest = (
        active_pair_digest
        if isinstance(active_pair_digest, str)
        else (candidate_digest if phase == "active" else incumbent_digest)
    )
    role_generation = challenger.get(
        "role_generation",
        activation_authority.get(
            "role_generation",
            identities.get("active_generation", live.get("role_generation")),
        ),
    )
    candidate_generation = challenger.get(
        "candidate_generation",
        activation_authority.get(
            "candidate_generation",
            live.get("candidate_generation"),
        ),
    )

    live_role = live.get("role_generation")
    if live_role is not None and role_generation is not None and live_role != role_generation:
        errors.append("live-role-generation-mismatch")
    live_candidate_generation = live.get("candidate_generation")
    if (
        live_candidate_generation is not None
        and candidate_generation is not None
        and live_candidate_generation != candidate_generation
    ):
        errors.append("live-candidate-generation-mismatch")
    live_candidate_digest = live.get("candidate_digest")
    if live_candidate_digest is not None and candidate_digest is not None and live_candidate_digest != candidate_digest:
        errors.append("live-candidate-digest-mismatch")
    checkpoint_digest = live.get("checkpoint_digest")
    if checkpoint_digest is not None and incumbent_digest is not None and checkpoint_digest != incumbent_digest:
        errors.append("live-checkpoint-digest-mismatch")

    live_origin = live.get("origin")
    durable_origin = challenger.get("origin", activation_authority.get("origin"))
    try:
        normalized_live_origin = (
            None if live_origin is None else _enum_value(live_origin, CandidateOrigin, "live candidate origin")
        )
        normalized_durable_origin = (
            None if durable_origin is None else _enum_value(durable_origin, CandidateOrigin, "durable candidate origin")
        )
        if (
            normalized_live_origin is not None
            and normalized_durable_origin is not None
            and normalized_live_origin != normalized_durable_origin
        ):
            errors.append("live-candidate-origin-mismatch")
        origin = normalized_durable_origin or normalized_live_origin
    except ValueError:
        origin = None
        errors.append("candidate-origin-invalid")

    durable_policy = challenger.get("policy", activation_authority.get("policy"))
    try:
        candidate_policy = (
            None
            if durable_policy is None
            else _enum_value(
                durable_policy,
                ActivationPolicy,
                "durable candidate policy",
            )
        )
    except ValueError:
        candidate_policy = None
        errors.append("candidate-policy-invalid")

    checks_input = live.get("checks", {})
    checks: dict[str, str] = {}
    if isinstance(checks_input, Mapping) and all(isinstance(key, str) for key in checks_input):
        for name, value in checks_input.items():
            try:
                checks[name] = _enum_value(value, CheckStatus, f"check {name}")
            except ValueError:
                checks[name] = CheckStatus.FAILED.value
                errors.append(f"check-status-invalid:{name}")
    else:
        errors.append("checks-invalid")

    fit_payload = _latest_payload(current_records, FitLifecycleEvidence)
    assessment_record = _latest(current_records, CandidateAssessmentEvidence)
    assessment = None if assessment_record is None else cast(CandidateAssessmentEvidence, assessment_record.payload)
    lifecycle = _latest_payload(current_records, ActivationLifecycleEvidence)
    failure = _latest_payload(current_records, LearningFailureEvidence)
    confidence = _latest_payload(current_records, ConfidenceDecisionEvidence)
    invalidation = _latest(current_records, SchemaInvalidationEvidence)
    if fit_payload is not None and fit_status == FitStatus.IDLE.value:
        fit_status = FitStatus(fit_payload.status).value
    if fit_payload is not None and fit_payload.status == FitStatus.FAILED.value and fit_payload.error is not None:
        errors.append(fit_payload.error)

    pending_persistence = bool(live.get("pending_persistence", False))
    pending_swap = bool(live.get("pending_swap", False)) or phase == "prepared"
    if fit_status in (FitStatus.QUEUED.value, FitStatus.RUNNING.value):
        status = LearningStatus.FITTING.value
    if pending_persistence or pending_swap:
        status = LearningStatus.ACTIVATING.value
    if lifecycle is not None and lifecycle.phase in ("active", "aborted") and not pending_swap:
        status = LearningStatus.ACTIVE.value if lifecycle.phase == "active" else status
    if failure is not None and failure.terminal:
        errors.append(failure.code)
    live_failure = live.get("failure")
    if live_failure is not None:
        if (
            isinstance(live_failure, Mapping)
            and isinstance(live_failure.get("code"), str)
            and bool(live_failure.get("code"))
            and isinstance(live_failure.get("detail"), str)
            and bool(live_failure.get("detail"))
            and live_failure.get("terminal") is True
        ):
            errors.append(cast(str, live_failure["code"]))
        else:
            errors.append("live-failure-invalid")
    if invalidation is not None and not _superseded_invalidation(current_records, invalidation):
        schema_invalidated = True
    if schema_invalidated:
        status = LearningStatus.SCHEMA_INVALIDATED.value
    elif errors:
        status = LearningStatus.ERROR.value

    candidate_parameters = challenger.get("candidate_parameters")
    candidate_metadata: Mapping[str, object] = {}
    rejection_reasons = [] if assessment is None else list(assessment.rejection_reasons)
    assessment_policy = None
    if assessment is not None:
        if (
            candidate_digest is not None
            and assessment_record is not None
            and assessment_record.model_digest != candidate_digest
        ):
            errors.append("assessment-candidate-digest-mismatch")
        elif origin is not None and assessment.origin != origin:
            errors.append("assessment-candidate-origin-mismatch")
        elif challenger and assessment.policy != candidate_policy:
            errors.append("assessment-candidate-policy-mismatch")
        else:
            assessment_policy = assessment.policy
            if not challenger:
                candidate_policy = assessment_policy
    if errors and not schema_invalidated:
        status = LearningStatus.ERROR.value
    if (
        assessment_policy == ActivationPolicy.OPERATOR_REVIEWED.value
        and assessment is not None
        and not assessment.rejection_reasons
        and status
        in {
            LearningStatus.COLLECTING.value,
            LearningStatus.EVALUATING.value,
        }
        and not errors
        and not schema_invalidated
        and candidate_digest is not None
    ):
        status = LearningStatus.READY_FOR_REVIEW.value
    blockers = list(dict.fromkeys([*rejection_reasons, *errors]))
    decision_id = challenger.get("decision_id")
    if not isinstance(decision_id, str):
        decision_id = activation_authority.get(
            "evidence_decision_id",
            activation_authority.get("decision_id"),
        )
    if not isinstance(decision_id, str) and assessment is not None:
        decision_id = assessment.decision_id
    if not isinstance(decision_id, str) and confidence is not None:
        decision_id = confidence.decision_id

    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "mode": origin,
        "decision_id": decision_id,
        "evidence": {
            "count": len(current_records),
            "audit_count": len(records),
            "high_water": (
                list(max((record.timestamp_ms, record.evidence_id) for record in records)) if records else None
            ),
            "retired_excluded": len(records) - len(current_records),
        },
        "fit": {
            "status": fit_status,
            "request_id": None if fit_payload is None else fit_payload.request_id,
            "window_id": None if fit_payload is None else fit_payload.window_id,
            "error": None if fit_payload is None else fit_payload.error,
        },
        "cook_refit": {
            "status": cook_refit_status,
            "latest": cook_refit_latest,
            "final_status": cook_refit_latest or cook_refit_status,
            "authorization": cook_refit_authorization,
            "next_cook": False,
        },
        "window": challenger.get("window"),
        "checks": checks,
        "candidate": {
            "digest": candidate_digest,
            "origin": origin,
            "policy": candidate_policy,
            "role_generation": role_generation,
            "candidate_generation": candidate_generation,
            "parameters": candidate_parameters,
            "parameter_deltas": live.get("parameter_deltas"),
            "fit_quality": candidate_metadata.get("rmse"),
            "identifiability": live.get("identifiability"),
            "assessment": None if assessment is None else _json_value(assessment),
        },
        "activation": {
            **activation_authority,
            "phase": phase,
            "reason": None if lifecycle is None else lifecycle.reason,
            "pending_persistence": pending_persistence,
            "pending_frame_boundary_swap": pending_swap,
        },
        "active_model": {
            "digest": active_digest,
            "role_generation": role_generation,
        },
        "identities": {
            "active_digest": active_digest,
            "active_generation": role_generation,
            "candidate_digest": candidate_digest,
            "candidate_generation": candidate_generation,
            "rollback_digest": (
                _pair_digest(activation_authority, "rollback_pair") or identities.get("rollback_digest")
            ),
            "rollback_generation": identities.get("rollback_generation"),
        },
        "calibration": {
            "revision": command_high_water,
            "command_high_water": command_high_water,
        },
        "latest_lifecycle": None if lifecycle is None else _json_value(lifecycle),
        "failure": (
            _json_value(live_failure) if live_failure is not None else None if failure is None else _json_value(failure)
        ),
        "gates": [
            {
                "name": name,
                "passed": value == CheckStatus.PASSED.value,
                "reason": None if value == CheckStatus.PASSED.value else name,
            }
            for name, value in checks.items()
        ],
        "blockers": blockers,
        "errors": errors,
    }
    revision_material = {**payload, "revision": None}
    payload["revision"] = hashlib.sha256(_canonical_bytes(revision_material)).hexdigest()
    contract = ModelEvidenceReport.model_validate_json(_canonical_bytes(payload), strict=True)
    return LearningReport(_canonical_bytes(contract.model_dump(mode="json", exclude_unset=True)))


def current_learning_report(
    evidence: Sequence[ModelEvidenceRecord],
    *,
    activation_state: object,
    live_status: object,
    calibration_command_high_water: int,
    checkpoint_required: bool = False,
    checkpoint: object = None,
    challenger_state: object = None,
) -> LearningReport:
    """Return the value-cached report over every authority input."""

    records = tuple(evidence)
    key_material = {
        "evidence": records,
        "activation": activation_state,
        "checkpoint": checkpoint,
        "challenger": _challenger_projection(challenger_state),
        "live": live_status,
        "calibration_command_high_water": calibration_command_high_water,
        "checkpoint_required": checkpoint_required,
    }
    key = hashlib.sha256(_canonical_bytes(key_material)).hexdigest()
    with _REPORT_CACHE_LOCK:
        cached = _REPORT_CACHE.get(key)
        if cached is not None:
            _REPORT_CACHE.move_to_end(key)
            return cached
    projected = build_learning_report(
        records,
        activation_state=activation_state,
        checkpoint=checkpoint,
        checkpoint_required=checkpoint_required,
        live_status=live_status,
        calibration_command_high_water=calibration_command_high_water,
        challenger_state=challenger_state,
    )
    with _REPORT_CACHE_LOCK:
        existing = _REPORT_CACHE.get(key)
        if existing is not None:
            _REPORT_CACHE.move_to_end(key)
            return existing
        _REPORT_CACHE[key] = projected
        _REPORT_CACHE.move_to_end(key)
        while len(_REPORT_CACHE) > _REPORT_CACHE_MAX_ENTRIES:
            _REPORT_CACHE.popitem(last=False)
    return projected


def build_learning_artifact(
    report: LearningReport,
    records: Sequence[ModelEvidenceRecord],
) -> bytes:
    """Serialize the exact report projection plus current grey audit references."""

    if not isinstance(report, LearningReport):
        raise TypeError("report must be a LearningReport")
    current = [
        record.model_dump(mode="json")
        for record in records
        if isinstance(record, ModelEvidenceRecord) and record.schema_version == MODEL_EVIDENCE_SCHEMA_VERSION
    ]
    return _canonical_bytes(
        {
            "artifact_schema": ARTIFACT_SCHEMA,
            "revision": report.revision,
            "report": report.as_dict(),
            "records": current,
        }
    )


def backend_learning_report() -> tuple[LearningReport, tuple[ModelEvidenceRecord, ...]]:
    """Read every backend authority once and return its one cached projection."""

    from common.controller_model_state import ControllerModelStore
    from common.persistence.control import (
        mpc_calibration_command_revision,
    )
    from common.persistence.model_challenger import read_model_challenger
    from common.persistence.model_evidence import read_model_activation, read_model_evidence
    from common.persistence.runtime import (
        read_status,
    )

    records = tuple(read_model_evidence())
    status = read_status()
    live = None
    if isinstance(status, Mapping):
        direct = status.get("learning")
        controller = status.get("controller")
        nested = controller.get("learning") if isinstance(controller, Mapping) else None
        live = direct if isinstance(direct, Mapping) else nested
    try:
        challenger = read_model_challenger()
    except ValueError:
        challenger = None
    report = current_learning_report(
        records,
        activation_state=read_model_activation(),
        checkpoint=ControllerModelStore().load("mpc"),
        checkpoint_required=True,
        live_status=live,
        calibration_command_high_water=mpc_calibration_command_revision(),
        challenger_state=challenger,
    )
    return report, records


def diagnostic_learning_report() -> ControllerLearningReport:
    """Return the generic owned envelope for the final MPC report."""

    report, _records = backend_learning_report()
    return ControllerLearningReport(
        controller="mpc",
        schema_version=1,
        revision=report.revision,
        report=cast(Mapping[str, JsonValue], report.as_dict()),
    )
