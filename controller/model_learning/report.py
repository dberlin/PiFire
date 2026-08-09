"""Deterministic projection of durable and live grey-box learning state."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import cast

from common.model_evidence import ModelEvidenceRecord

from .contracts import CandidateOrigin, CheckStatus, FitStatus, LearningStatus

REPORT_SCHEMA_VERSION = 1
_REPORT_CACHE_MAX_ENTRIES = 8
_REPORT_CACHE: OrderedDict[str, LearningReport] = OrderedDict()
_REPORT_CACHE_LOCK = threading.Lock()


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ModelEvidenceRecord):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("report mappings must have string keys")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"unsupported report value {type(value).__name__}")


def _mapping(value: object, name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    owned = _json_value(value)
    assert isinstance(owned, dict)
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


@dataclass(frozen=True, slots=True)
class LearningReport:
    """Immutable canonical report bytes safe to cache and serve directly."""

    payload_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        decoded = json.loads(self.payload_bytes)
        if not isinstance(decoded, dict):
            raise ValueError("learning report root is not an object")
        return cast(dict[str, object], decoded)


def build_learning_report(
    evidence: Sequence[ModelEvidenceRecord],
    *,
    activation_state: object,
    live_status: object,
    calibration_command_high_water: int,
) -> LearningReport:
    """Combine the four authoritative learning inputs into one projection."""

    records = tuple(evidence)
    if not all(isinstance(record, ModelEvidenceRecord) for record in records):
        raise TypeError("evidence must contain ModelEvidenceRecord values")
    activation = _mapping(activation_state, "activation_state")
    live = _mapping(live_status, "live_status")
    command_high_water = _nonnegative_int(calibration_command_high_water, "calibration_command_high_water")

    errors: list[str] = []
    try:
        status = _enum_value(live.get("status", LearningStatus.COLLECTING), LearningStatus, "learning status")
    except ValueError:
        status = LearningStatus.ERROR.value
        errors.append("live-status-invalid")
    try:
        fit_status = _enum_value(live.get("fit_status", FitStatus.IDLE), FitStatus, "fit status")
    except ValueError:
        fit_status = FitStatus.FAILED.value
        errors.append("live-fit-status-invalid")

    role_generation = live.get("role_generation")
    candidate_generation = live.get("candidate_generation")
    activation_role = activation.get("role_generation")
    activation_candidate = activation.get("candidate_generation")
    if role_generation is not None:
        try:
            role_generation = _nonnegative_int(role_generation, "live role_generation")
        except ValueError:
            errors.append("live-role-generation-invalid")
    if candidate_generation is not None:
        try:
            candidate_generation = _nonnegative_int(candidate_generation, "live candidate_generation")
        except ValueError:
            errors.append("live-candidate-generation-invalid")
    if activation_role is not None and role_generation != activation_role:
        errors.append("live-role-generation-mismatch")
    if activation_candidate is not None and candidate_generation != activation_candidate:
        errors.append("live-candidate-generation-mismatch")

    live_candidate_digest = live.get("candidate_digest")
    activation_candidate_digest = activation.get("candidate_digest")
    if (
        live_candidate_digest is not None
        and activation_candidate_digest is not None
        and live_candidate_digest != activation_candidate_digest
    ):
        errors.append("live-candidate-digest-mismatch")

    checkpoint_digest = live.get("checkpoint_digest")
    incumbent_digest = activation.get("incumbent_digest", checkpoint_digest)
    if checkpoint_digest is not None and incumbent_digest is not None and checkpoint_digest != incumbent_digest:
        errors.append("live-checkpoint-digest-mismatch")

    live_origin_value = live.get("origin")
    activation_origin_value = activation.get("origin")
    try:
        live_origin = (
            None
            if live_origin_value is None
            else _enum_value(live_origin_value, CandidateOrigin, "live candidate origin")
        )
        activation_origin = (
            None
            if activation_origin_value is None
            else _enum_value(activation_origin_value, CandidateOrigin, "activation candidate origin")
        )
        candidate_origin = live_origin if live_origin is not None else activation_origin
        if live_origin is not None and activation_origin is not None and live_origin != activation_origin:
            errors.append("live-candidate-origin-mismatch")
    except ValueError:
        candidate_origin = None
        errors.append("candidate-origin-invalid")

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

    phase = activation.get("phase")
    prepared = phase == "prepared"
    active_digest = activation.get("candidate_digest") if phase == "active" else incumbent_digest
    if errors:
        status = LearningStatus.ERROR.value

    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "evidence": {
            "count": len(records),
            "high_water": (
                max((record.timestamp_ms, record.evidence_id) for record in records)
                if records
                else None
            ),
        },
        "fit": {"status": fit_status},
        "checks": checks,
        "candidate": {
            "digest": live_candidate_digest if live_candidate_digest is not None else activation_candidate_digest,
            "origin": candidate_origin,
            "role_generation": role_generation,
            "candidate_generation": candidate_generation,
        },
        "activation": {
            **activation,
            "pending_frame_boundary_swap": prepared,
        },
        "active_model": {
            "digest": active_digest,
            "role_generation": activation_role,
        },
        "calibration": {"command_high_water": command_high_water},
        "errors": errors,
    }
    return LearningReport(_canonical_bytes(payload))


def current_learning_report(
    evidence: Sequence[ModelEvidenceRecord],
    *,
    activation_state: object,
    live_status: object,
    calibration_command_high_water: int,
) -> LearningReport:
    """Return the value-cached report for all four authority inputs."""

    records = tuple(evidence)
    key_material = {
        "evidence": records,
        "activation": activation_state,
        "live": live_status,
        "calibration_command_high_water": calibration_command_high_water,
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
        live_status=live_status,
        calibration_command_high_water=calibration_command_high_water,
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
