"""Immutable grey estimator/native-pair activation transactions."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Generic, Protocol, TypeVar, runtime_checkable
from common.web_contracts.learning import ModelActivationRequest

from .contracts import ActivationPolicy, CandidateOrigin


_PairT = TypeVar("_PairT", bound="ActivationPair")
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


def _canonical_json(value: object) -> str:
    return json.dumps(_owned_json(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_snapshot_digest(snapshot: Mapping[str, object]) -> str:
    """Hash a complete canonical grey configuration."""

    if not isinstance(snapshot, Mapping) or not all(isinstance(key, str) for key in snapshot):
        raise ValueError("snapshot must be an object with string keys")
    return hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()


class ActivationPhase(StrEnum):
    PREPARED = "prepared"
    ACTIVE = "active"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class GreyControlPairDescriptor:
    """Complete durable identity of one grey estimator/native solver owner."""

    model_digest: str
    configuration: Mapping[str, object]
    estimator_kind: str
    solver_kind: str
    candidate_generation: int
    role_generation: int
    ownership_digest: str = ""

    def __post_init__(self) -> None:
        _digest(self.model_digest, "model_digest")
        _nonblank(self.estimator_kind, "estimator_kind")
        _nonblank(self.solver_kind, "solver_kind")
        _generation(self.candidate_generation, "candidate_generation")
        _generation(self.role_generation, "role_generation")
        if not isinstance(self.configuration, Mapping):
            raise ValueError("configuration must be a mapping")
        owned = _owned_json(self.configuration)
        assert isinstance(owned, dict)
        if canonical_snapshot_digest(owned) != self.model_digest:
            raise ValueError("model_digest does not match configuration")
        identity = {
            "model_digest": self.model_digest,
            "configuration": owned,
            "estimator_kind": self.estimator_kind,
            "solver_kind": self.solver_kind,
            "candidate_generation": self.candidate_generation,
            "role_generation": self.role_generation,
        }
        ownership_digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        if self.ownership_digest and self.ownership_digest != ownership_digest:
            raise ValueError("ownership_digest does not match pair descriptor")
        object.__setattr__(self, "configuration", _freeze(owned))
        object.__setattr__(self, "ownership_digest", ownership_digest)

    def to_dict(self) -> dict[str, object]:
        return {
            "model_digest": self.model_digest,
            "configuration": _owned_json(self.configuration),
            "estimator_kind": self.estimator_kind,
            "solver_kind": self.solver_kind,
            "candidate_generation": self.candidate_generation,
            "role_generation": self.role_generation,
            "ownership_digest": self.ownership_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> GreyControlPairDescriptor:
        configuration = value.get("configuration")
        if not isinstance(configuration, Mapping):
            raise ValueError("configuration must be a mapping")
        ownership_digest = value.get("ownership_digest", "")
        if not isinstance(ownership_digest, str):
            raise ValueError("ownership_digest must be a string")
        return cls(
            model_digest=_digest(value.get("model_digest"), "model_digest"),
            configuration=configuration,
            estimator_kind=_nonblank(value.get("estimator_kind"), "estimator_kind"),
            solver_kind=_nonblank(value.get("solver_kind"), "solver_kind"),
            candidate_generation=_generation(
                value.get("candidate_generation"),
                "candidate_generation",
            ),
            role_generation=_generation(value.get("role_generation"), "role_generation"),
            ownership_digest=ownership_digest,
        )

@runtime_checkable
class ActivationPair(Protocol):
    """Concrete structural contract consumed by the pure activation domain."""

    descriptor: GreyControlPairDescriptor

    def close(self) -> None: ...



@dataclass(frozen=True, slots=True)
class PreparedActivationRecord:
    """One durable prepared/active/aborted pair transaction."""

    phase: ActivationPhase
    transaction_id: str
    timestamp_ms: int
    incumbent: GreyControlPairDescriptor
    candidate: GreyControlPairDescriptor
    rollback: GreyControlPairDescriptor
    origin: CandidateOrigin
    policy: ActivationPolicy
    decision_id: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ActivationPhase):
            object.__setattr__(self, "phase", ActivationPhase(self.phase))
        _digest(self.transaction_id, "transaction_id")
        _generation(self.timestamp_ms, "timestamp_ms")
        if not all(isinstance(pair, GreyControlPairDescriptor) for pair in (self.incumbent, self.candidate, self.rollback)):
            raise TypeError("activation record requires complete pair descriptors")
        if self.rollback != self.incumbent:
            raise ValueError("prepared rollback owner must be the exact incumbent pair")
        if not isinstance(self.origin, CandidateOrigin) or not isinstance(self.policy, ActivationPolicy):
            raise TypeError("activation origin and policy must be typed")
        if _POLICY_BY_ORIGIN[self.origin] is not self.policy:
            raise ValueError("origin-policy-mismatch")
        _nonblank(self.decision_id, "decision_id")
        if self.phase is ActivationPhase.ABORTED:
            _nonblank(self.reason, "reason")
        elif self.reason is not None:
            raise ValueError("reason is legal only for aborted activation")

    @classmethod
    def prepared(
        cls,
        *,
        timestamp_ms: int,
        incumbent: GreyControlPairDescriptor,
        candidate: GreyControlPairDescriptor,
        origin: CandidateOrigin,
        policy: ActivationPolicy,
        decision_id: str,
    ) -> PreparedActivationRecord:
        identity = {
            "incumbent": incumbent.to_dict(),
            "candidate": candidate.to_dict(),
            "origin": origin.value,
            "policy": policy.value,
            "decision_id": decision_id,
        }
        transaction_id = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        return cls(
            phase=ActivationPhase.PREPARED,
            transaction_id=transaction_id,
            timestamp_ms=timestamp_ms,
            incumbent=incumbent,
            candidate=candidate,
            rollback=incumbent,
            origin=origin,
            policy=policy,
            decision_id=decision_id,
        )

    def transition(self, phase: ActivationPhase, *, reason: str | None = None) -> PreparedActivationRecord:
        phase = ActivationPhase(phase)
        if self.phase is not ActivationPhase.PREPARED:
            raise ValueError("only a prepared activation may transition")
        if phase not in {ActivationPhase.ACTIVE, ActivationPhase.ABORTED}:
            raise ValueError("prepared activation may transition only to active or aborted")
        return replace(self, phase=phase, reason=reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "transaction_id": self.transaction_id,
            "timestamp_ms": self.timestamp_ms,
            "incumbent": self.incumbent.to_dict(),
            "candidate": self.candidate.to_dict(),
            "rollback": self.rollback.to_dict(),
            "origin": self.origin.value,
            "policy": self.policy.value,
            "decision_id": self.decision_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PreparedActivationRecord:
        return cls(
            phase=ActivationPhase(value.get("phase")),
            transaction_id=value.get("transaction_id"),  # type: ignore[arg-type]
            timestamp_ms=value.get("timestamp_ms"),  # type: ignore[arg-type]
            incumbent=GreyControlPairDescriptor.from_dict(value.get("incumbent")),  # type: ignore[arg-type]
            candidate=GreyControlPairDescriptor.from_dict(value.get("candidate")),  # type: ignore[arg-type]
            rollback=GreyControlPairDescriptor.from_dict(value.get("rollback")),  # type: ignore[arg-type]
            origin=CandidateOrigin(value.get("origin")),
            policy=ActivationPolicy(value.get("policy")),
            decision_id=value.get("decision_id"),  # type: ignore[arg-type]
            reason=value.get("reason"),  # type: ignore[arg-type]
        )


class _DurableReceipt(Protocol):
    accepted: bool
    completed: bool
    durable: bool

    def wait(self, timeout: float | None = None) -> bool: ...


@dataclass(frozen=True, slots=True)
class ActivationDecision(Generic[_PairT]):
    accepted: bool
    reason: str
    phase: ActivationPhase | None
    incumbent_pair: ActivationPair
    candidate_pair: _PairT | None = None
    record: PreparedActivationRecord | None = None


class ActivationManager(Generic[_PairT]):
    """Validate and durably prepare one complete pair without installing it."""

    def __init__(
        self,
        *,
        incumbent_pair: ActivationPair,
        build_candidate: Callable[[GreyControlPairDescriptor], _PairT],
        validate_candidate: Callable[[_PairT], bool],
        native_dry_solve: Callable[[_PairT], bool],
        persist_prepared: Callable[[PreparedActivationRecord], _DurableReceipt],
        clock_ms: Callable[[], int] | None = None,
        receipt_timeout: float | None = None,
    ) -> None:
        if not isinstance(incumbent_pair, ActivationPair):
            raise TypeError("incumbent_pair must satisfy ActivationPair")
        self._active_pair = incumbent_pair
        self._build_candidate = build_candidate
        self._validate_candidate = validate_candidate
        self._native_dry_solve = native_dry_solve
        self._persist_prepared = persist_prepared
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._receipt_timeout = receipt_timeout
        self._prepared: ActivationDecision[_PairT] | None = None

    @property
    def active_pair(self) -> ActivationPair:
        return self._active_pair

    @property
    def prepared(self) -> ActivationDecision[_PairT] | None:
        return self._prepared

    def prepare(
        self,
        request: ModelActivationRequest,
        candidate: GreyControlPairDescriptor,
        *,
        origin: CandidateOrigin,
        policy: ActivationPolicy,
    ) -> ActivationDecision[_PairT]:
        if not isinstance(request, ModelActivationRequest):
            raise TypeError("request must be a ModelActivationRequest")
        if not isinstance(candidate, GreyControlPairDescriptor):
            raise TypeError("candidate must be a GreyControlPairDescriptor")
        if not isinstance(origin, CandidateOrigin) or not isinstance(policy, ActivationPolicy):
            raise TypeError("origin and policy must be typed")
        if request.candidate_digest != candidate.model_digest:
            return self._reject("candidate-digest-changed")
        if _POLICY_BY_ORIGIN[origin] is not policy:
            return self._reject("origin-policy-mismatch")
        if self._prepared is not None:
            record = self._prepared.record
            if record is not None and record.candidate == candidate and record.decision_id == request.decision_id:
                return self._prepared
            return self._reject("activation-already-prepared")

        try:
            candidate_pair = self._build_candidate(candidate)
        except Exception:
            return self._reject("candidate-build-failed")
        if not isinstance(candidate_pair, ActivationPair) or candidate_pair.descriptor != candidate:
            self._close_failed(candidate_pair)
            return self._reject("candidate-build-failed")
        try:
            if self._validate_candidate(candidate_pair) is not True:
                self._close_failed(candidate_pair)
                return self._reject("candidate-validation-failed")
        except Exception:
            self._close_failed(candidate_pair)
            return self._reject("candidate-validation-failed")
        try:
            if self._native_dry_solve(candidate_pair) is not True:
                self._close_failed(candidate_pair)
                return self._reject("native-dry-solve-failed")
        except Exception:
            self._close_failed(candidate_pair)
            return self._reject("native-dry-solve-failed")
        try:
            timestamp_ms = self._clock_ms()
            _generation(timestamp_ms, "timestamp_ms")
            record = PreparedActivationRecord.prepared(
                timestamp_ms=timestamp_ms,
                incumbent=self._active_pair.descriptor,
                candidate=candidate,
                origin=origin,
                policy=policy,
                decision_id=request.decision_id,
            )
            receipt = self._persist_prepared(record)
        except Exception:
            self._close_failed(candidate_pair)
            return self._reject("activation-persistence-failed")
        if not (
            hasattr(receipt, "accepted")
            and hasattr(receipt, "durable")
            and hasattr(receipt, "wait")
            and receipt.accepted
        ):
            self._close_failed(candidate_pair)
            return self._reject("activation-persistence-unavailable")
        try:
            drained = receipt.wait(self._receipt_timeout)
        except Exception:
            drained = False
        if drained is not True or receipt.durable is not True:
            self._close_failed(candidate_pair)
            receipt_error = getattr(receipt, "error", None)
            if isinstance(receipt_error, str) and "activation-authority-changed" in receipt_error:
                return self._reject("activation-authority-changed")
            if isinstance(receipt_error, str) and "activation-state-changed" in receipt_error:
                return self._reject("activation-state-changed")
            return self._reject("activation-persistence-not-durable")
        decision = ActivationDecision(
            accepted=True,
            reason="prepared",
            phase=ActivationPhase.PREPARED,
            incumbent_pair=self._active_pair,
            candidate_pair=candidate_pair,
            record=record,
        )
        self._prepared = decision
        return decision

    def _reject(self, reason: str) -> ActivationDecision[_PairT]:
        return ActivationDecision(False, reason, None, self._active_pair)

    @staticmethod
    def _close_failed(pair: ActivationPair) -> None:
        try:
            pair.close()
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class StartupActivationRecovery:
    """The sole pair identities startup may reconstruct from durable authority."""

    phase: ActivationPhase
    restore: GreyControlPairDescriptor
    rollback: GreyControlPairDescriptor
    record: PreparedActivationRecord


def _record_from_persisted_state(state: object) -> PreparedActivationRecord:
    def pair(name: str) -> GreyControlPairDescriptor:
        raw = getattr(state, f"{name}_pair_json", None)
        if not isinstance(raw, str):
            raise ValueError(f"persisted activation is missing {name} pair")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError(f"persisted {name} pair must be an object")
        return GreyControlPairDescriptor.from_dict(decoded)

    return PreparedActivationRecord(
        phase=ActivationPhase(getattr(state, "phase", None)),
        transaction_id=_digest(getattr(state, "transaction_id", None), "transaction_id"),
        timestamp_ms=0,
        incumbent=pair("incumbent"),
        candidate=pair("candidate"),
        rollback=pair("rollback"),
        origin=CandidateOrigin(getattr(state, "origin", None)),
        policy=ActivationPolicy(getattr(state, "policy", None)),
        decision_id=_nonblank(getattr(state, "evidence_decision_id", None), "decision_id"),
        reason=getattr(state, "reason", None),
    )


def recover_startup_activation(
    state: object,
    *,
    persist_aborted: Callable[[PreparedActivationRecord], _DurableReceipt],
    receipt_timeout: float | None = None,
) -> StartupActivationRecovery:
    """Converge a process restart to the pair selected by durable phase authority."""
    record = _record_from_persisted_state(state)
    if record.phase is ActivationPhase.PREPARED:
        aborted = record.transition(
            ActivationPhase.ABORTED,
            reason="interrupted-activation",
        )
        receipt = persist_aborted(aborted)
        if not (
            hasattr(receipt, "accepted")
            and hasattr(receipt, "durable")
            and hasattr(receipt, "wait")
            and receipt.accepted
        ):
            raise RuntimeError("could not durably abort interrupted activation")
        try:
            completed = receipt.wait(receipt_timeout)
        except Exception as error:
            raise RuntimeError("could not durably abort interrupted activation") from error
        if completed is not True or receipt.durable is not True:
            raise RuntimeError("could not durably abort interrupted activation")
        record = aborted
    restore = record.candidate if record.phase is ActivationPhase.ACTIVE else record.incumbent
    return StartupActivationRecovery(
        phase=record.phase,
        restore=restore,
        rollback=record.rollback,
        record=record,
    )
