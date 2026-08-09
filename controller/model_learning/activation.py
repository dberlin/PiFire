"""Durable grey-candidate preparation without runtime ownership transfer."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar

from .contracts import ActivationPolicy, CandidateOrigin

_BuiltPair = TypeVar("_BuiltPair")
_POLICY_BY_ORIGIN = {
    CandidateOrigin.PASSIVE_ONLINE: ActivationPolicy.PASSIVE_AUTO,
    CandidateOrigin.OPERATOR_CALIBRATION: ActivationPolicy.OPERATOR_REVIEWED,
    CandidateOrigin.COOK_REFIT: ActivationPolicy.COOK_REFIT,
}


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank")
    return value


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _generation(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _owned_json(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("snapshot objects must have string keys")
        return {key: _owned_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_owned_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("snapshot must contain only canonical JSON values")


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def canonical_snapshot_digest(snapshot: Mapping[str, object]) -> str:
    """Hash the complete canonical grey candidate snapshot."""

    if not isinstance(snapshot, Mapping) or not all(isinstance(key, str) for key in snapshot):
        raise ValueError("snapshot must be an object with string keys")
    canonical = json.dumps(
        _owned_json(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class ActivationRequest:
    """The exact candidate and confidence decision reviewed by an operator."""

    candidate_digest: str
    decision_id: str

    def __post_init__(self) -> None:
        _digest(self.candidate_digest, "candidate_digest")
        _nonblank(self.decision_id, "decision_id")


@dataclass(frozen=True, slots=True)
class ActivationCandidate:
    """A typed grey candidate plus immutable lineage and generation identity."""

    incumbent_digest: str
    candidate_digest: str
    candidate_generation: int
    role_generation: int
    origin: CandidateOrigin
    decision_id: str
    snapshot: Mapping[str, object]

    def __post_init__(self) -> None:
        _digest(self.incumbent_digest, "incumbent_digest")
        _digest(self.candidate_digest, "candidate_digest")
        object.__setattr__(
            self,
            "candidate_generation",
            _generation(self.candidate_generation, "candidate_generation"),
        )
        object.__setattr__(self, "role_generation", _generation(self.role_generation, "role_generation"))
        if not isinstance(self.origin, CandidateOrigin):
            raise ValueError("origin must be a CandidateOrigin")
        _nonblank(self.decision_id, "decision_id")
        if not isinstance(self.snapshot, Mapping):
            raise ValueError("snapshot must be a mapping")
        owned = _owned_json(self.snapshot)
        assert isinstance(owned, dict)
        if canonical_snapshot_digest(owned) != self.candidate_digest:
            raise ValueError("candidate_digest does not match snapshot")
        object.__setattr__(self, "snapshot", _freeze(owned))


@dataclass(frozen=True, slots=True)
class PreparedActivationRecord:
    phase: str
    timestamp_ms: int
    incumbent_digest: str
    candidate_digest: str
    role_generation: int
    candidate_generation: int
    origin: CandidateOrigin
    policy: ActivationPolicy
    decision_id: str
    snapshot: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    accepted: bool
    reason: str
    phase: str | None
    incumbent_digest: str
    candidate_digest: str
    role_generation: int
    candidate_generation: int
    origin: CandidateOrigin
    policy: ActivationPolicy
    decision_id: str
    record: PreparedActivationRecord | None = None


class ActivationManager(Generic[_BuiltPair]):
    """Prepare one durable activation; frame-boundary ownership belongs elsewhere."""

    def __init__(
        self,
        *,
        validate_candidate: Callable[[ActivationCandidate], bool],
        build_candidate: Callable[[ActivationCandidate], _BuiltPair],
        native_dry_solve: Callable[[_BuiltPair], bool],
        persist_prepared: Callable[[PreparedActivationRecord], bool],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._validate_candidate = validate_candidate
        self._build_candidate = build_candidate
        self._native_dry_solve = native_dry_solve
        self._persist_prepared = persist_prepared
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._prepared: ActivationDecision | None = None

    @property
    def prepared(self) -> ActivationDecision | None:
        return self._prepared

    def prepare(
        self,
        request: ActivationRequest,
        candidate: ActivationCandidate,
        *,
        policy: ActivationPolicy,
    ) -> ActivationDecision:
        if not isinstance(request, ActivationRequest):
            raise TypeError("request must be an ActivationRequest")
        if not isinstance(candidate, ActivationCandidate):
            raise TypeError("candidate must be an ActivationCandidate")
        if not isinstance(policy, ActivationPolicy):
            raise TypeError("policy must be an ActivationPolicy")
        if request.candidate_digest != candidate.candidate_digest:
            return self._reject(candidate, policy, "candidate-digest-changed")
        if request.decision_id != candidate.decision_id:
            return self._reject(candidate, policy, "stale-decision")
        if _POLICY_BY_ORIGIN[candidate.origin] is not policy:
            return self._reject(candidate, policy, "origin-policy-mismatch")

        try:
            if self._validate_candidate(candidate) is not True:
                return self._reject(candidate, policy, "candidate-validation-failed")
        except Exception:
            return self._reject(candidate, policy, "candidate-validation-failed")
        try:
            built_pair = self._build_candidate(candidate)
        except Exception:
            return self._reject(candidate, policy, "candidate-build-failed")
        try:
            if self._native_dry_solve(built_pair) is not True:
                return self._reject(candidate, policy, "native-dry-solve-failed")
        except Exception:
            return self._reject(candidate, policy, "native-dry-solve-failed")

        try:
            timestamp_ms = self._clock_ms()
        except Exception:
            return self._reject(candidate, policy, "activation-persistence-failed")
        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
            return self._reject(candidate, policy, "activation-persistence-failed")
        record = PreparedActivationRecord(
            phase="prepared",
            timestamp_ms=timestamp_ms,
            incumbent_digest=candidate.incumbent_digest,
            candidate_digest=candidate.candidate_digest,
            role_generation=candidate.role_generation,
            candidate_generation=candidate.candidate_generation,
            origin=candidate.origin,
            policy=policy,
            decision_id=candidate.decision_id,
            snapshot=candidate.snapshot,
        )
        try:
            if self._persist_prepared(record) is not True:
                return self._reject(candidate, policy, "activation-persistence-failed")
        except Exception:
            return self._reject(candidate, policy, "activation-persistence-failed")

        decision = ActivationDecision(
            accepted=True,
            reason="prepared",
            phase="prepared",
            incumbent_digest=candidate.incumbent_digest,
            candidate_digest=candidate.candidate_digest,
            role_generation=candidate.role_generation,
            candidate_generation=candidate.candidate_generation,
            origin=candidate.origin,
            policy=policy,
            decision_id=candidate.decision_id,
            record=record,
        )
        self._prepared = decision
        return decision

    @staticmethod
    def _reject(
        candidate: ActivationCandidate,
        policy: ActivationPolicy,
        reason: str,
    ) -> ActivationDecision:
        return ActivationDecision(
            accepted=False,
            reason=reason,
            phase=None,
            incumbent_digest=candidate.incumbent_digest,
            candidate_digest=candidate.candidate_digest,
            role_generation=candidate.role_generation,
            candidate_generation=candidate.candidate_generation,
            origin=candidate.origin,
            policy=policy,
            decision_id=candidate.decision_id,
        )
