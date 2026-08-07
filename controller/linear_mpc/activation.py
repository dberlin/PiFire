"""Exact-digest manual activation and fail-closed prediction ownership."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

import numpy as np

from common.model_evidence import (
    ActivationEvidence,
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceRecord,
    RollbackEvidence,
)
from .policy import LinearMPC, LinearMPCConfig
from .state_space import InnovationStateSpace

STATE_SPACE_KIND = "innovation-state-space"
GREY_BOX_KIND = "grey-box"
_STATE_SPACE_SCHEMA = "innovation-state-space/v2"


@dataclass(frozen=True, slots=True)
class ActivationRequest:
    """The two exact identities an operator reviewed and confirmed."""

    candidate_digest: str
    decision_id: str

    def __post_init__(self) -> None:
        _digest(self.candidate_digest, "candidate_digest")
        _nonblank(self.decision_id, "decision_id")


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    """Owned result of preparation; rejection never carries model authority."""

    accepted: bool
    reason: str | None
    request: ActivationRequest
    candidate_generation: int | None = None
    activation_generation: int | None = None
    provenance_digest: str | None = None
    controller_configuration_digest: str | None = None
    active_snapshot_json: str | None = None
    rollback_snapshot_json: str | None = None
    activation_record: ModelEvidenceRecord | None = None
    candidate: InnovationStateSpace | None = None
    last_safe_command: float | None = None


@dataclass(frozen=True, slots=True)
class ActivationState:
    """Complete runtime ownership and the last explicit fallback outcome."""

    active_kind: Literal["grey-box", "innovation-state-space"] = GREY_BOX_KIND
    active_digest: str | None = None
    decision_id: str | None = None
    role_generation: int = 0
    rollback_kind: Literal["grey-box", "innovation-state-space"] = GREY_BOX_KIND
    rollback_digest: str | None = None
    controller_configuration_digest: str | None = None
    failed_digest: str | None = None
    failed_generation: int | None = None
    last_safe_command: float | None = None
    fallback_kind: Literal["grey-box", "innovation-state-space"] | None = None
    fallback_reason: str | None = None
    failed_generations: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparedInputs:
    decision_record: ModelEvidenceRecord
    candidate_snapshot: dict[str, object]
    rollback_snapshot: dict[str, object]


class ActivationManager:
    """Two-phase state-space activation with persistence-before-ownership.

    ``prepare`` owns and validates every input and performs a production
    reconstruction plus prospective solve without changing ``state`` or the
    current prediction owner. ``commit`` rechecks the mutable authorities,
    persists the activation transaction synchronously, and only then publishes
    the prepared model as the command prediction owner.
    """

    def __init__(
        self,
        ledger: Sequence[ModelEvidenceRecord] | Callable[[], Sequence[ModelEvidenceRecord]],
        *,
        candidate_snapshot: Mapping[str, object] | Callable[[str, int], Mapping[str, object] | None],
        rollback_snapshot: Mapping[str, object] | Callable[[], Mapping[str, object]],
        controller_configuration: str | Mapping[str, object] | Callable[[], str | Mapping[str, object]],
        prospective_solve: Callable[[InnovationStateSpace], float | None] | None = None,
        persist_activation: Callable[..., object] | None = None,
        invalidate_pending_origins: Callable[[int, str], None] | None = None,
        append_evidence: Callable[[ModelEvidenceRecord], object] | None = None,
        session_id: str = "manual-activation",
        cook_id: str | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._ledger_source = ledger
        self._candidate_source = candidate_snapshot
        self._rollback_source = rollback_snapshot
        self._configuration_source = controller_configuration
        self._prospective_solve = prospective_solve or _production_prospective_solve
        self._persist_activation = persist_activation
        self._invalidate_pending_origins = invalidate_pending_origins
        self._append_evidence = append_evidence
        self._session_id = _nonblank(session_id, "session_id")
        self._cook_id = cook_id
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._state = ActivationState()
        self._active_model: InnovationStateSpace | None = None
        self._rollback_model: InnovationStateSpace | None = None
        self._active_snapshot: dict[str, object] | None = None
        self._rollback_snapshot_owned: dict[str, object] | None = None

    @property
    def state(self) -> ActivationState:
        return self._state

    @property
    def active_kind(self) -> str:
        return self._state.active_kind

    @property
    def active_model(self) -> InnovationStateSpace | None:
        return self._active_model

    @property
    def active_snapshot(self) -> dict[str, object] | None:
        return None if self._active_snapshot is None else _owned_mapping(self._active_snapshot, "active snapshot")

    def note_safe_command(self, command: float) -> None:
        """Retain the last certified command for exact fallback evidence."""
        if self._state.active_kind != STATE_SPACE_KIND:
            return
        self._state = replace(
            self._state,
            last_safe_command=_duty(command, "last_safe_command"),
        )

    def prepare(self, request: ActivationRequest) -> ActivationDecision:
        """Validate and prospectively solve an owned candidate without control effects."""
        if not isinstance(request, ActivationRequest):
            raise TypeError("request must be ActivationRequest")
        try:
            inputs = self._prepare_inputs(request)
            record = inputs.decision_record
            generation = record.role_generation
            if generation in self._state.failed_generations:
                return self._reject(request, "failed-generation-cannot-be-reenabled")
            candidate = InnovationStateSpace.from_snapshot(inputs.candidate_snapshot)
            last_safe_command = self._prospective_solve(candidate)
            if last_safe_command is not None:
                last_safe_command = _duty(last_safe_command, "prospective command")
            config_digest = self._configuration_digest()
            active_json = _canonical_json(inputs.candidate_snapshot)
            rollback_json = _canonical_json(inputs.rollback_snapshot)
            activation_generation = max(self._state.role_generation, generation) + 1
            activation_record = ModelEvidenceRecord(
                evidence_id=f"activation:{request.decision_id}:{activation_generation}:{request.candidate_digest}",
                kind=EvidenceKind.ACTIVATION,
                session_id=self._session_id,
                cook_id=self._cook_id,
                timestamp_ms=self._clock_ms(),
                role_generation=activation_generation,
                model_digest=request.candidate_digest,
                provenance_digest=record.provenance_digest,
                payload=ActivationEvidence(
                    decision_id=request.decision_id,
                    active_snapshot_json=active_json,
                    rollback_snapshot_json=rollback_json,
                    controller_configuration_digest=config_digest,
                ),
            )
            return ActivationDecision(
                accepted=True,
                reason=None,
                request=request,
                candidate_generation=generation,
                activation_generation=activation_generation,
                provenance_digest=record.provenance_digest,
                controller_configuration_digest=config_digest,
                active_snapshot_json=active_json,
                rollback_snapshot_json=rollback_json,
                activation_record=activation_record,
                candidate=candidate,
                last_safe_command=last_safe_command,
            )
        except Exception as error:
            return self._reject(request, _rejection_reason(error))

    def commit(self, prepared: ActivationDecision) -> ActivationDecision:
        """Persist a prepared activation before atomically publishing ownership."""
        if not isinstance(prepared, ActivationDecision):
            raise TypeError("prepared must be ActivationDecision")
        if not prepared.accepted:
            return prepared
        if (
            prepared.candidate is None
            or prepared.activation_record is None
            or prepared.activation_generation is None
            or prepared.controller_configuration_digest is None
            or prepared.active_snapshot_json is None
            or prepared.rollback_snapshot_json is None
        ):
            return replace(prepared, accepted=False, reason="prepared-activation-incomplete")
        if prepared.activation_generation in self._state.failed_generations:
            return replace(prepared, accepted=False, reason="failed-generation-cannot-be-reenabled")
        if self._configuration_digest() != prepared.controller_configuration_digest:
            return replace(prepared, accepted=False, reason="controller-configuration-changed")
        try:
            current = self._prepare_inputs(prepared.request)
        except Exception as error:
            return replace(prepared, accepted=False, reason=_rejection_reason(error))
        if _canonical_json(current.candidate_snapshot) != prepared.active_snapshot_json:
            return replace(prepared, accepted=False, reason="candidate-digest-changed")
        if _canonical_json(current.rollback_snapshot) != prepared.rollback_snapshot_json:
            return replace(prepared, accepted=False, reason="rollback-snapshot-changed")
        if self._persist_activation is None:
            return replace(prepared, accepted=False, reason="activation-persistence-unavailable")
        try:
            persisted = _call_activation_persistence(self._persist_activation, prepared.activation_record)
        except Exception:
            persisted = False
        if not persisted:
            return replace(prepared, accepted=False, reason="activation-persistence-failed")

        # The durable transaction is authoritative before either operation below.
        # Pending origins are invalidated before the new owner can emit a command.
        try:
            if self._invalidate_pending_origins is not None:
                self._invalidate_pending_origins(prepared.activation_generation, prepared.request.candidate_digest)
        except Exception:
            return replace(prepared, accepted=False, reason="pending-origin-invalidation-failed")

        active_snapshot = _json_object(prepared.active_snapshot_json, "active snapshot")
        rollback_snapshot = _json_object(prepared.rollback_snapshot_json, "rollback snapshot")
        previous_active = self._active_model
        previous_kind = self._state.active_kind
        previous_digest = self._state.active_digest
        self._rollback_model = previous_active if previous_kind == STATE_SPACE_KIND else None
        self._rollback_snapshot_owned = rollback_snapshot
        self._active_model = prepared.candidate
        self._active_snapshot = active_snapshot
        self._state = ActivationState(
            active_kind=STATE_SPACE_KIND,
            active_digest=prepared.request.candidate_digest,
            decision_id=prepared.request.decision_id,
            role_generation=prepared.activation_generation,
            rollback_kind=previous_kind,
            rollback_digest=previous_digest or prepared.provenance_digest,
            controller_configuration_digest=prepared.controller_configuration_digest,
            last_safe_command=prepared.last_safe_command,
            failed_generations=self._state.failed_generations,
        )
        return prepared

    def fallback(
        self,
        reason: str,
        *,
        failed_digest: str | None = None,
        generation: int | None = None,
        last_safe_command: float | None = None,
    ) -> ActivationState:
        """Immediately leave a failed generation and record the exact outcome."""
        reason = _nonblank(reason, "reason")
        failed_digest = failed_digest or self._state.active_digest
        generation = self._state.role_generation if generation is None else _generation(generation)
        if last_safe_command is None:
            last_safe_command = self._state.last_safe_command
        if last_safe_command is not None:
            last_safe_command = _duty(last_safe_command, "last_safe_command")
        failed_generations = tuple(sorted({*self._state.failed_generations, generation}))
        fallback_kind: Literal["grey-box", "innovation-state-space"] = (
            STATE_SPACE_KIND if self._rollback_model is not None else GREY_BOX_KIND
        )
        fallback_digest = self._state.rollback_digest if fallback_kind == STATE_SPACE_KIND else None
        self._active_model = self._rollback_model
        self._active_snapshot = self._rollback_model.snapshot() if self._rollback_model is not None else None
        self._state = ActivationState(
            active_kind=fallback_kind,
            active_digest=fallback_digest,
            decision_id=self._state.decision_id,
            role_generation=generation + 1,
            rollback_kind=GREY_BOX_KIND,
            rollback_digest=None,
            controller_configuration_digest=self._state.controller_configuration_digest,
            failed_digest=failed_digest,
            failed_generation=generation,
            last_safe_command=last_safe_command,
            fallback_kind=fallback_kind,
            fallback_reason=reason,
            failed_generations=failed_generations,
        )
        self._record_fallback(reason)
        return self._state

    def rollback(self, reason: str) -> ActivationState:
        """Apply an explicit operator rollback; a blank reason is never accepted."""
        reason = _nonblank(reason, "reason")
        decision_id = self._state.decision_id
        state = self.fallback(reason)
        if self._append_evidence is not None and decision_id is not None:
            record = ModelEvidenceRecord(
                evidence_id=f"rollback:{decision_id}:{state.role_generation}:{self._clock_ms()}",
                kind=EvidenceKind.ROLLBACK,
                session_id=self._session_id,
                cook_id=self._cook_id,
                timestamp_ms=self._clock_ms(),
                role_generation=state.role_generation,
                model_digest=state.failed_digest,
                provenance_digest=state.active_digest,
                payload=RollbackEvidence(decision_id=decision_id, reason=reason),
            )
            self._append_evidence(record)
        return state

    def restore(self, persisted: object) -> ActivationDecision:
        """Reconstruct the exact durable active/rollback generations on restart."""
        try:
            active_json = getattr(persisted, "active_snapshot_json")
            rollback_json = getattr(persisted, "rollback_snapshot_json")
            decision_id = _nonblank(getattr(persisted, "evidence_decision_id"), "evidence_decision_id")
            config_digest = _digest(
                getattr(persisted, "controller_configuration_digest"), "controller_configuration_digest"
            )
            generation = _generation(getattr(persisted, "role_generation"))
            active_snapshot = _json_object(active_json, "active snapshot")
            rollback_snapshot = _json_object(rollback_json, "rollback snapshot")
            candidate_digest = canonical_snapshot_digest(active_snapshot)
            request = ActivationRequest(candidate_digest=candidate_digest, decision_id=decision_id)
        except Exception as error:
            request = ActivationRequest(candidate_digest="0" * 64, decision_id="restore")
            return self._reject(request, f"restore-invalid:{_rejection_reason(error)}")
        if config_digest != self._configuration_digest():
            return self._reject(request, "restore-controller-configuration-changed")
        records = self._ledger()
        activation = next(
            (
                record
                for record in reversed(records)
                if isinstance(record.payload, ActivationEvidence)
                and record.payload.decision_id == decision_id
                and record.role_generation == generation
                and record.model_digest == candidate_digest
            ),
            None,
        )
        if activation is None:
            return self._reject(request, "restore-activation-decision-missing")
        rollback_digest = canonical_snapshot_digest(_rollback_provenance_snapshot(rollback_snapshot))
        if activation.provenance_digest != rollback_digest:
            return self._reject(request, "restore-rollback-provenance-changed")
        rollback_model = (
            InnovationStateSpace.from_snapshot(rollback_snapshot)
            if rollback_snapshot.get("schema") == _STATE_SPACE_SCHEMA
            else None
        )
        latest_lifecycle = max(
            (
                record
                for record in records
                if isinstance(record.payload, (ActivationEvidence, RollbackEvidence, FallbackEvidence))
            ),
            key=lambda record: (record.timestamp_ms, record.evidence_id),
            default=None,
        )
        if latest_lifecycle is not None and not isinstance(latest_lifecycle.payload, ActivationEvidence):
            lifecycle_payload = cast(RollbackEvidence | FallbackEvidence, latest_lifecycle.payload)
            fallback_kind = STATE_SPACE_KIND if rollback_model is not None else GREY_BOX_KIND
            self._active_model = rollback_model
            self._active_snapshot = rollback_snapshot if rollback_model is not None else None
            self._rollback_model = None
            self._rollback_snapshot_owned = rollback_snapshot
            self._state = ActivationState(
                active_kind=fallback_kind,
                active_digest=rollback_digest if rollback_model is not None else None,
                decision_id=decision_id,
                role_generation=max(generation, latest_lifecycle.role_generation),
                rollback_kind=GREY_BOX_KIND,
                controller_configuration_digest=config_digest,
                failed_digest=candidate_digest,
                failed_generation=generation,
                fallback_kind=fallback_kind,
                fallback_reason=lifecycle_payload.reason,
                failed_generations=(generation,),
            )
            return self._reject(request, "restore-generation-already-failed")
        try:
            candidate = InnovationStateSpace.from_snapshot(active_snapshot)
            last_safe = self._prospective_solve(candidate)
            if last_safe is not None:
                last_safe = _duty(last_safe, "prospective command")
        except Exception as error:
            self.fallback(
                f"restore-failed:{_rejection_reason(error)}", failed_digest=candidate_digest, generation=generation
            )
            return self._reject(request, self._state.fallback_reason or "restore-failed")
        self._active_model = candidate
        self._active_snapshot = active_snapshot
        self._rollback_model = rollback_model
        self._rollback_snapshot_owned = rollback_snapshot
        self._state = ActivationState(
            active_kind=STATE_SPACE_KIND,
            active_digest=candidate_digest,
            decision_id=decision_id,
            role_generation=generation,
            rollback_kind=STATE_SPACE_KIND if rollback_model is not None else GREY_BOX_KIND,
            rollback_digest=rollback_digest,
            controller_configuration_digest=config_digest,
            last_safe_command=last_safe,
        )
        return ActivationDecision(
            accepted=True,
            reason=None,
            request=request,
            candidate_generation=max(0, generation - 1),
            activation_generation=generation,
            provenance_digest=activation.provenance_digest,
            controller_configuration_digest=config_digest,
            active_snapshot_json=active_json,
            rollback_snapshot_json=rollback_json,
            activation_record=activation,
            candidate=candidate,
            last_safe_command=last_safe,
        )

    def _prepare_inputs(self, request: ActivationRequest) -> _PreparedInputs:
        records = self._ledger()
        decisions = [
            (record, cast(ConfidenceDecisionEvidence, record.payload))
            for record in records
            if isinstance(record.payload, ConfidenceDecisionEvidence)
        ]
        exact = [(record, payload) for record, payload in decisions if payload.decision_id == request.decision_id]
        if not exact:
            raise ValueError("confidence-decision-not-found")
        decision, decision_payload = max(exact, key=lambda item: (item[0].timestamp_ms, item[0].evidence_id))
        if decision.model_digest != request.candidate_digest:
            raise ValueError("candidate-digest-changed")
        if decision_payload.blocked or decision_payload.reason is not None:
            raise ValueError(decision_payload.reason or "confidence-decision-blocked")
        latest, latest_payload = max(decisions, key=lambda item: (item[0].timestamp_ms, item[0].evidence_id))
        if latest_payload.decision_id != request.decision_id:
            raise ValueError("stale-confidence-decision")
        if decision.schema_version != 2:
            raise ValueError("incompatible-evidence-schema")
        candidate_evidence = [
            record
            for record in records
            if record.model_digest == request.candidate_digest and record.role_generation == decision.role_generation
        ]
        provenances = {
            record.provenance_digest for record in candidate_evidence if record.provenance_digest is not None
        }
        if decision.provenance_digest is None or provenances != {decision.provenance_digest}:
            raise ValueError("incompatible-provenance")
        snapshot = self._candidate_snapshot(request.candidate_digest, decision.role_generation)
        if canonical_snapshot_digest(snapshot) != request.candidate_digest:
            raise ValueError("candidate-digest-changed")
        if snapshot.get("schema") != _STATE_SPACE_SCHEMA:
            raise ValueError("incompatible-model-schema")
        rollback = (
            _owned_mapping(self._active_snapshot, "active rollback snapshot")
            if self._state.active_kind == STATE_SPACE_KIND and self._active_snapshot is not None
            else self._rollback_snapshot()
        )
        rollback_provenance = _rollback_provenance_snapshot(rollback)
        if canonical_snapshot_digest(rollback_provenance) != decision.provenance_digest:
            raise ValueError("incompatible-provenance")
        return _PreparedInputs(decision, snapshot, rollback)

    def _ledger(self) -> tuple[ModelEvidenceRecord, ...]:
        source = self._ledger_source() if callable(self._ledger_source) else self._ledger_source
        if isinstance(source, (str, bytes)) or not isinstance(source, Sequence):
            raise TypeError("ledger must be a sequence")
        records = tuple(
            ModelEvidenceRecord.model_validate_json(record.model_dump_json())
            if isinstance(record, ModelEvidenceRecord)
            else (_raise_type("ledger records must be ModelEvidenceRecord"))
            for record in source
        )
        return tuple(sorted(records, key=lambda record: (record.timestamp_ms, record.evidence_id)))

    def _candidate_snapshot(self, digest: str, generation: int) -> dict[str, object]:
        source = self._candidate_source
        value = source(digest, generation) if callable(source) else source
        if value is None:
            raise ValueError("candidate-snapshot-not-found")
        return _owned_mapping(value, "candidate snapshot")

    def _rollback_snapshot(self) -> dict[str, object]:
        source = self._rollback_source
        value = source() if callable(source) else source
        return _owned_mapping(value, "rollback snapshot")

    def _configuration_digest(self) -> str:
        source = self._configuration_source
        value = source() if callable(source) else source
        return value if isinstance(value, str) else canonical_configuration_digest(value)

    def _record_fallback(self, reason: str) -> None:
        if self._append_evidence is None:
            return
        record = ModelEvidenceRecord(
            evidence_id=f"fallback:{self._state.failed_generation}:{self._clock_ms()}:{self._state.failed_digest}",
            kind=EvidenceKind.FALLBACK,
            session_id=self._session_id,
            cook_id=self._cook_id,
            timestamp_ms=self._clock_ms(),
            role_generation=self._state.role_generation,
            model_digest=self._state.failed_digest,
            provenance_digest=self._state.active_digest,
            payload=FallbackEvidence(
                reason=reason,
                failed_digest=self._state.failed_digest,
                failed_generation=self._state.failed_generation,
                last_safe_command=self._state.last_safe_command,
                fallback_kind=self._state.fallback_kind,
            ),
        )
        self._append_evidence(record)

    @staticmethod
    def _reject(request: ActivationRequest, reason: str) -> ActivationDecision:
        return ActivationDecision(False, _nonblank(reason, "reason"), request)


def canonical_snapshot_digest(snapshot: Mapping[str, object]) -> str:
    """Return the canonical snapshot identity used by online adaptation."""
    encoded = _canonical_json(_owned_mapping(snapshot, "snapshot")).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_configuration_digest(configuration: Mapping[str, object]) -> str:
    """Bind activation to the complete controller construction input."""
    encoded = _canonical_json(_owned_mapping(configuration, "controller configuration")).encode()
    return hashlib.sha256(encoded).hexdigest()


def extract_state_space_candidate(checkpoint: Mapping[str, object]) -> dict[str, object]:
    """Extract the production checkpoint's state-space challenger snapshot."""
    root = _owned_mapping(checkpoint, "controller checkpoint")
    online = root.get("online_adaptation")
    if not isinstance(online, Mapping):
        raise ValueError("candidate-snapshot-not-found")
    candidates = [online.get("challenger"), online.get("incumbent")]
    state_spaces = [
        value for value in candidates if isinstance(value, Mapping) and value.get("schema") == _STATE_SPACE_SCHEMA
    ]
    if len(state_spaces) != 1:
        raise ValueError("candidate-snapshot-not-found")
    return _owned_mapping(state_spaces[0], "candidate snapshot")


def _production_prospective_solve(candidate: InnovationStateSpace) -> float:
    snapshot = candidate.snapshot()
    record = snapshot.get("record")
    lag = record.get("lag") if isinstance(record, Mapping) else None
    inputs = lag.get("realized_q") if isinstance(lag, Mapping) else None
    ambients = lag.get("ambient_c") if isinstance(lag, Mapping) else None
    if not isinstance(inputs, Sequence) or not inputs or not isinstance(ambients, Sequence) or not ambients:
        raise ValueError("prospective-state-unavailable")
    previous = _duty(inputs[-1], "prospective previous command")
    ambient_value = ambients[-1]
    if isinstance(ambient_value, bool) or not isinstance(ambient_value, (int, float)):
        raise ValueError("prospective ambient is not numeric")
    ambient = float(ambient_value)
    if not math.isfinite(ambient):
        raise ValueError("prospective ambient is not finite")
    config = LinearMPCConfig(horizon_steps=1)
    prediction = candidate.affine_prediction(1, previous, np.asarray([ambient], dtype=np.float64))
    setpoint = float(prediction.free_output_c[0])
    solve = LinearMPC(config).solve(
        prediction,
        setpoint_c=setpoint,
        q_previous=previous,
        equilibrium_q=previous,
    )
    command = float(solve.sequence_q[0])
    if not math.isfinite(command):
        raise ValueError("prospective solve is non-finite")
    return _duty(command, "prospective command")


def _call_activation_persistence(callback: Callable[..., object], record: ModelEvidenceRecord) -> bool:
    try:
        parameters = inspect.signature(callback).parameters
    except TypeError, ValueError:
        parameters = {}
    result = callback(record, wait=True) if "wait" in parameters else callback(record)
    return result is not False


def _rollback_provenance_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    online = snapshot.get("online_adaptation")
    incumbent = online.get("incumbent") if isinstance(online, Mapping) else None
    if isinstance(incumbent, Mapping):
        return _owned_mapping(incumbent, "rollback incumbent")
    return _owned_mapping(snapshot, "rollback snapshot")


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("snapshot-is-not-canonical-json") from error


def _json_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be JSON text")
    try:
        decoded = json.loads(value, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} is invalid JSON") from error
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise ValueError(f"{name} must be an object")
    return decoded


def _owned_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return _json_object(_canonical_json(dict(value)), name)


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank")
    return value


def _generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("role generation must be a non-negative integer")
    return value


def _duty(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be finite and in [0, 1]")
    return result


def _raise_type(message: str) -> Any:
    raise TypeError(message)


def _rejection_reason(error: Exception) -> str:
    text = str(error).strip()
    return text if text else f"{type(error).__name__}"
