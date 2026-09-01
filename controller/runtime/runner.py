"""Temperature-controller execution interface (PID/MPC/etc).

`ControllerRunner` is the abstract interface `HoldMode.on_tick` drives:
set_target/submit/latest to run the control math, reconfigure() to rebuild the
core on a settings change, control_period() for the mode's poll interval, and
commands_fan() so the caller knows whether this controller issues its own fan
command (MPC) or leaves fan control to the temperature-profile logic.
`SyncControllerRunner` runs the underlying controller module's `update()`
synchronously on submit/latest -- control math and probe-read cadence are the
same cadence. `ThreadedControllerRunner` runs the core on a background thread
at its own control period and hands back non-blocking snapshots via
`.latest()`, decoupling control-math cadence from the probe-read cadence.
`build_runner` selects between the two by the core's `wants_async()` (MPC
requests the threaded runner; other controllers get the sync runner), and -- if
the selected controller will not build at all -- substitutes FALLBACK_CONTROLLER
so a live fire never ends up unregulated. See `_build_core` and `build_runner`
for why both of those matter.
"""

from __future__ import annotations

import collections
import importlib
import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from common.control_trace import ActuationMode, ControllerType, ResultStaleState
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
    SessionSummaryEvidence,
)
from common.persistence.model_evidence import ModelActivationState
from controller.base import (
    ControllerLearningDiagnostics,
    ControllerStatusCapture,
    ControllerTraceDiagnostics,
    MpcTraceDiagnostics,
    normalize_controller_output,
)
from controller.model_learning.contracts import CandidateOrigin
from controller.mpc_allocator import AllocationResult
from controller.runtime.model_lifecycle import ModelLifecycleRunner
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    ModelPersistenceWorker,
)

if TYPE_CHECKING:
    from common.persistence.learning_trajectory import LearningTrajectoryRepository
    from controller.model_learning.calibration import CalibrationDecision
    from controller.model_learning.contracts import FrameObservation
    from controller.model_learning.grey_runtime import GreyLearningProcessOwner
    from controller.mpc_calibration import CalibrationCommand
    from controller.mpc_model import EstimatorSeed


type StatusScalar = None | bool | int | float | str
type StatusValue = StatusScalar | Mapping[str, StatusValue] | tuple[StatusValue, ...]


@runtime_checkable
class _ActivationCore(Protocol):
    @property
    def activation_terminated(self) -> bool: ...

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool: ...

    def activation_runtime_failure(self, reason: str) -> bool: ...

    def rollback_activation(self, reason: str) -> bool: ...

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]: ...

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt: ...

    def advance_activation(self) -> bool: ...

    def terminate_mpc_activation(self, reason: str) -> None: ...


@runtime_checkable
class _FrameLearningCore(Protocol):
    def observe_frame(self, observation: FrameObservation) -> object: ...

    def observation_failure(
        self,
        observation: FrameObservation,
        error: BaseException,
    ) -> object: ...


@runtime_checkable
class _ControllerCorpusLearningCore(Protocol):
    def bind_learning_identity(
        self,
        session_id: str,
        cook_id: str | None,
        role_generation: int,
    ) -> None: ...

    def poll_learning_off_path(
        self,
        *,
        live_origin: CandidateOrigin | None = None,
    ) -> object: ...

    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool: ...

    def _schedule_corpus_fit_ticket(
        self,
        origin: CandidateOrigin,
    ) -> str | None: ...

    def _consume_terminal_corpus_fit_ticket(
        self,
        ticket: str,
        origin: CandidateOrigin,
    ) -> bool: ...

    def fail_corpus_fit(
        self,
        ticket: str,
        error: BaseException | str,
    ) -> None: ...

    def get_learning_diagnostics(self) -> ControllerLearningDiagnostics: ...


@runtime_checkable
class _CorpusFitDisabledCore(Protocol):
    def record_corpus_fit_disabled(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool: ...


@runtime_checkable
class _CorpusFitFailureCore(Protocol):
    def record_corpus_fit_failed(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool: ...


@runtime_checkable
class _MpcLearningCore(Protocol):
    """MPC-only estimator seeding, separate from controller corpus learning."""

    def estimator_seed_requirements(self) -> tuple[float, int]: ...

    def bind_estimator_seed_source(
        self,
        source: Callable[[float, int], object] | None,
    ) -> None: ...


class _ControllerCoreCompatibilityAdapter:
    """Own legacy optional projections while delegating required operations."""

    __slots__ = ("_core",)

    def __init__(self, core) -> None:
        self._core = core

    def __getattr__(self, name):
        return getattr(self._core, name)

    def capture_status(self) -> ControllerStatusCapture:
        capture_status = getattr(self._core, "capture_status", None)
        if callable(capture_status):
            captured = capture_status()
            if not isinstance(captured, ControllerStatusCapture):
                raise TypeError("controller capture_status() must return ControllerStatusCapture")
            return captured
        learning = getattr(
            self._core,
            "get_learning_diagnostics",
            lambda: None,
        )()
        if learning is not None and not isinstance(
            learning,
            ControllerLearningDiagnostics,
        ):
            raise TypeError("controller learning diagnostics must be ControllerLearningDiagnostics or None")
        return ControllerStatusCapture(
            status=self.get_status(),
            learning=learning,
        )

    def get_status(self):
        return getattr(self._core, "get_status", lambda: None)()

    def trace_diagnostics(self) -> ControllerTraceDiagnostics | None:
        return getattr(self._core, "trace_diagnostics", lambda: None)()

    def trace_allocation(self) -> AllocationResult | None:
        return getattr(self._core, "trace_allocation", lambda: None)()

    def trace_baseline_allocation(self) -> AllocationResult | None:
        return getattr(
            self._core,
            "trace_baseline_allocation",
            lambda: None,
        )()

    def trace_calibration(self) -> CalibrationDecision | None:
        return getattr(self._core, "trace_calibration", lambda: None)()

    def register_calibration_result(self, result) -> None:
        register = getattr(self._core, "register_calibration_result", None)
        if callable(register):
            register(result)

    def close(self) -> None:
        close = getattr(self._core, "close", None)
        if callable(close):
            close()


def _adapt_controller_core(core) -> _ControllerCoreCompatibilityAdapter:
    if isinstance(core, _ControllerCoreCompatibilityAdapter):
        return core
    return _ControllerCoreCompatibilityAdapter(core)


def _activation_core_for(core) -> _ActivationCore | None:
    delegated = core._core if isinstance(core, _ControllerCoreCompatibilityAdapter) else core
    return delegated if isinstance(delegated, _ActivationCore) else None


def _frame_learning_core_for(core) -> _FrameLearningCore | None:
    delegated = core._core if isinstance(core, _ControllerCoreCompatibilityAdapter) else core
    return delegated if isinstance(delegated, _FrameLearningCore) else None


def _controller_corpus_learning_core_for(
    core,
) -> _ControllerCorpusLearningCore | None:
    delegated = core._core if isinstance(core, _ControllerCoreCompatibilityAdapter) else core
    return delegated if isinstance(delegated, _ControllerCorpusLearningCore) else None


def _corpus_fit_disabled_core_for(core) -> _CorpusFitDisabledCore | None:
    delegated = core._core if isinstance(core, _ControllerCoreCompatibilityAdapter) else core
    return delegated if isinstance(delegated, _CorpusFitDisabledCore) else None


def _corpus_fit_failure_core_for(core) -> _CorpusFitFailureCore | None:
    delegated = core._core if isinstance(core, _ControllerCoreCompatibilityAdapter) else core
    return delegated if isinstance(delegated, _CorpusFitFailureCore) else None


def _mpc_learning_core_for(core) -> _MpcLearningCore | None:
    delegated = core._core if isinstance(core, _ControllerCoreCompatibilityAdapter) else core
    return delegated if isinstance(delegated, _MpcLearningCore) else None


@dataclass(frozen=True, slots=True)
class ObservationOutcomeEnvelope:
    """One runner-owned learner result for the exact observation the core saw."""

    submission_sequence: int
    configuration_generation: int
    observation: FrameObservation
    outcome: object
    evidence: tuple[ModelEvidenceRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationTerminalDrop:
    """One accepted observation terminalized without a releasable envelope."""

    submission_sequence: int
    configuration_generation: int
    observation: FrameObservation
    reason: str


@dataclass(slots=True)
class _CorpusFitPlan:
    origin: CandidateOrigin
    before_schedule: Callable[[], bool] | None
    ticket: str | None = None
    scheduled: bool = False


@dataclass(frozen=True, slots=True)
class ObservationSubmission:
    submission_sequence: int
    configuration_generation: int
    evicted_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class ModelRestoreOutcome:
    restore_token: str | None
    accepted: bool
    effective_authority: Mapping[str, object] | None
    staged_for_revalidation: bool = False
    pending: bool = False

    def __post_init__(self) -> None:
        if self.restore_token is not None and (
            not isinstance(self.restore_token, str) or not self.restore_token.strip()
        ):
            raise ValueError("restore_token must be non-blank when present")
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be a bool")
        if self.effective_authority is not None and not isinstance(
            self.effective_authority,
            Mapping,
        ):
            raise TypeError("effective_authority must be a mapping when present")
        if not isinstance(self.staged_for_revalidation, bool):
            raise TypeError("staged_for_revalidation must be a bool")
        if not isinstance(self.pending, bool):
            raise TypeError("pending must be a bool")
        if self.pending and (not self.accepted or self.effective_authority is not None or self.staged_for_revalidation):
            raise ValueError("a pending restore must be accepted without effective authority")
        if self.staged_for_revalidation and not self.accepted:
            raise ValueError("only an accepted restore can remain staged for revalidation")


@dataclass(frozen=True, slots=True)
class ObservationOutcomeDrain:
    envelopes: tuple[ObservationOutcomeEnvelope, ...]
    terminal_drops: tuple[ObservationTerminalDrop, ...]
    dropped_count: int
    dropped_sequences: tuple[int, ...]

    def __iter__(self):
        return iter(self.envelopes)


def _freeze_evidence(
    outcome: object,
    session_id: str | None,
    cook_id: str | None,
    observation: FrameObservation,
) -> tuple[ModelEvidenceRecord, ...]:
    """Own typed MPC compact evidence for the exact completed observation."""
    if session_id is None or cook_id is None or not isinstance(outcome, Mapping):
        return ()
    eligible = outcome.get("eligible")
    rejection_reasons = outcome.get("rejection_reasons", ())
    values = outcome.get("forecast_origin_evidence", ())
    if (
        not isinstance(eligible, bool)
        or not isinstance(rejection_reasons, tuple)
        or not all(isinstance(reason, str) and reason for reason in rejection_reasons)
        or not isinstance(values, tuple)
        or not all(isinstance(value, ForecastOriginEvidence) for value in values)
    ):
        return ()
    timestamp_ms = int(observation.frame_end_s * 1_000)
    summary = ModelEvidenceRecord(
        evidence_id=(
            f"{session_id}:session-summary:{timestamp_ms}:"
            f"{observation.observation_sequence}:{observation.role_generation}"
        ),
        kind=EvidenceKind.SESSION_SUMMARY,
        session_id=session_id,
        cook_id=cook_id,
        timestamp_ms=timestamp_ms,
        role_generation=observation.role_generation,
        model_digest=outcome.get("model_digest") if isinstance(outcome.get("model_digest"), str) else None,
        provenance_digest=None,
        payload=SessionSummaryEvidence(
            completed_origins=len(values),
            accepted_observations=int(eligible),
            rejected_observations=int(not eligible),
            rejection_reasons=rejection_reasons,
        ),
    )
    records = tuple(
        ModelEvidenceRecord(
            evidence_id=(
                f"{session_id}:forecast:{value.origin_sequence}:{value.horizon_steps}:{value.completion_time_ms}"
            ),
            kind=EvidenceKind.FORECAST_ORIGIN,
            session_id=session_id,
            cook_id=cook_id,
            timestamp_ms=value.completion_time_ms,
            role_generation=observation.role_generation,
            model_digest=value.challenger_digest,
            provenance_digest=value.incumbent_digest,
            payload=value,
        )
        for value in values
    )
    evaluation = outcome.get("evaluation_payload")
    refresh = outcome.get("refresh_diagnostics_evidence")
    decision_id = getattr(evaluation, "decision_id", None)
    evaluated_at_ms = getattr(evaluation, "evaluated_at_ms", None)
    role_generation = getattr(evaluation, "role_generation", None)
    challenger_digest = getattr(evaluation, "challenger_digest", None)
    incumbent_digest = getattr(evaluation, "incumbent_digest", None)
    if (
        not isinstance(decision_id, str)
        or not isinstance(evaluated_at_ms, int)
        or isinstance(evaluated_at_ms, bool)
        or evaluated_at_ms < 0
        or not isinstance(role_generation, int)
    ):
        return (summary,) + records
    additions: tuple[ModelEvidenceRecord, ...] = ()
    if not bool(outcome.get("confidence_already_persisted", False)):
        rejection_reasons = tuple(getattr(evaluation, "rejection_reasons", ()))
        accepted = outcome.get("confidence_accepted")
        if not isinstance(accepted, bool):
            accepted = not rejection_reasons and int(getattr(evaluation, "consecutive_wins", 0)) >= 2
        reason = (
            None if accepted else (";".join(rejection_reasons) if rejection_reasons else "confidence-window-incomplete")
        )
        additions += (
            ModelEvidenceRecord(
                evidence_id=f"{session_id}:{decision_id}:confidence:{role_generation}",
                kind=EvidenceKind.CONFIDENCE_DECISION,
                session_id=session_id,
                cook_id=cook_id,
                timestamp_ms=evaluated_at_ms,
                role_generation=role_generation,
                model_digest=challenger_digest,
                provenance_digest=incumbent_digest,
                payload=ConfidenceDecisionEvidence(
                    decision_id=decision_id,
                    blocked=not accepted,
                    reason=reason,
                ),
            ),
        )
    if isinstance(refresh, RefreshDiagnosticsEvidence):
        additions += (
            ModelEvidenceRecord(
                evidence_id=f"{session_id}:{decision_id}:refresh:{role_generation}",
                kind=EvidenceKind.REFRESH_DIAGNOSTICS,
                session_id=session_id,
                cook_id=cook_id,
                timestamp_ms=evaluated_at_ms,
                role_generation=role_generation,
                model_digest=challenger_digest,
                provenance_digest=incumbent_digest,
                schema_version=2,
                payload=refresh,
            ),
        )
    return (summary,) + records + additions


def _freeze_status_value(value: object) -> StatusValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, StatusValue] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TypeError("controller status keys must be strings")
            frozen[key] = _freeze_status_value(nested_value)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_status_value(item) for item in value)
    raise TypeError(f"unsupported controller status value: {type(value).__name__}")


def _freeze_status(status: Mapping[str, object]) -> Mapping[str, StatusValue]:
    return MappingProxyType({key: _freeze_status_value(value) for key, value in status.items()})


type MutableStatusValue = StatusScalar | dict[str, MutableStatusValue] | list[MutableStatusValue]


def _thaw_status_value(value: object) -> MutableStatusValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        thawed: dict[str, MutableStatusValue] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TypeError("controller status keys must be strings")
            thawed[key] = _thaw_status_value(nested_value)
        return thawed
    if isinstance(value, tuple | list):
        return [_thaw_status_value(item) for item in value]
    raise TypeError(f"unsupported controller status value: {type(value).__name__}")


def _thaw_status(status: Mapping[str, object]) -> dict[str, MutableStatusValue]:
    thawed = _thaw_status_value(status)
    if not isinstance(thawed, dict):
        raise TypeError("controller status must be a mapping")
    return thawed


def _control_period_seconds(value: object) -> float | None:
    """Return the quality budget, or disable budget checks for legacy cores."""
    if value is None:
        return None
    period = float(value)
    if not math.isfinite(period) or period <= 0:
        return None
    return period


@dataclass(slots=True)
class _ResultQualityTracker:
    """State that belongs to completed runner results, never the solver."""

    control_period: float | None
    deadline_miss_count: int = 0
    consecutive_deadline_miss_count: int = 0
    stale_state: ResultStaleState = ResultStaleState.FRESH

    def completed(self, result: ControllerUpdateResult) -> tuple[ControllerUpdateResult, ResultStaleState | None]:
        deadline_missed = self.control_period is not None and result.solve_duration_seconds > self.control_period
        if deadline_missed:
            self.deadline_miss_count += 1
            self.consecutive_deadline_miss_count += 1
        else:
            self.consecutive_deadline_miss_count = 0
        recovered = self.stale_state is ResultStaleState.STALE
        self.stale_state = ResultStaleState.FRESH
        quality_result = replace(
            result,
            result_age_seconds=0.0,
            deadline_miss_count=self.deadline_miss_count,
            consecutive_deadline_miss_count=self.consecutive_deadline_miss_count,
            stale_state=ResultStaleState.FRESH,
            recovered=recovered,
        )
        return (
            _with_result_quality(quality_result),
            ResultStaleState.FRESH if recovered else None,
        )

    def polled(
        self, result: ControllerUpdateResult, monotonic_now: float
    ) -> tuple[ControllerUpdateResult, ResultStaleState | None]:
        if result.revision == 0 or self.control_period is None:
            return result, None
        age = max(0.0, monotonic_now - result.solve_end_monotonic)
        stale = age >= 2.0 * self.control_period
        next_state = ResultStaleState.STALE if stale else ResultStaleState.FRESH
        transition = next_state is not self.stale_state
        if transition:
            self.stale_state = next_state
        if not transition and age == result.result_age_seconds and next_state is result.stale_state:
            return result, None
        quality_result = replace(
            result,
            result_age_seconds=age,
            stale_state=next_state,
            recovered=result.recovered if next_state is ResultStaleState.FRESH and not transition else False,
        )
        return _with_result_quality(quality_result), next_state if transition else None


def _quality_status(result: ControllerUpdateResult) -> dict[str, StatusScalar]:
    return {
        "solve_duration_seconds": result.solve_duration_seconds,
        "result_age_seconds": result.result_age_seconds,
        "deadline_miss_count": result.deadline_miss_count,
        "consecutive_deadline_miss_count": result.consecutive_deadline_miss_count,
        "result_stale_state": result.stale_state.value,
        "result_recovered": result.recovered,
    }


def _actuation_mode_for(core) -> ActuationMode:
    value = core.actuation_mode()
    if not isinstance(value, ActuationMode):
        raise TypeError("controller actuation_mode() must return ActuationMode")
    return value


def _controller_type_for(value: object) -> ControllerType | None:
    try:
        return ControllerType(value)
    except TypeError, ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ControllerUpdateResult:
    """One atomically captured controller completion."""

    cycle_ratio: float
    fan: Mapping[str, float] | None
    input_temperature: float
    diagnostics: ControllerTraceDiagnostics | None = None
    learning: ControllerLearningDiagnostics | None = None
    allocation: AllocationResult | None = None
    baseline_allocation: AllocationResult | None = None
    calibration: CalibrationDecision | None = None
    status: Mapping[str, StatusValue] | None = None
    revision: int = 0
    solve_start_monotonic: float | None = None
    solve_end_monotonic: float | None = None
    solve_duration_seconds: float | None = None
    completed_wall_time: float | None = None
    result_age_seconds: float = 0.0
    deadline_miss_count: int = 0
    consecutive_deadline_miss_count: int = 0
    stale_state: ResultStaleState = ResultStaleState.FRESH
    recovered: bool = False

    def __post_init__(self):
        if self.fan is not None:
            object.__setattr__(self, "fan", MappingProxyType(dict(self.fan)))
        if self.status is not None:
            object.__setattr__(self, "status", _freeze_status(self.status))
        if not math.isfinite(self.input_temperature):
            raise ValueError("input_temperature must be finite")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        if not math.isfinite(self.result_age_seconds) or self.result_age_seconds < 0:
            raise ValueError("result age must be finite and non-negative")
        if self.deadline_miss_count < 0 or self.consecutive_deadline_miss_count < 0:
            raise ValueError("deadline miss counts must be non-negative")
        if not isinstance(self.stale_state, ResultStaleState):
            raise TypeError("stale_state must be ResultStaleState")
        solve_start = self.solve_start_monotonic
        solve_end = self.solve_end_monotonic
        solve_duration = self.solve_duration_seconds
        completion_wall_time = self.completed_wall_time
        if self.revision == 0:
            if (
                solve_start is not None
                or solve_end is not None
                or solve_duration is not None
                or completion_wall_time is not None
            ):
                raise ValueError("an uncompleted result has no completion timestamps")
            return
        if solve_start is None or solve_end is None or solve_duration is None or completion_wall_time is None:
            raise ValueError("a completed result requires all completion timestamps")
        if not all(math.isfinite(value) for value in (solve_start, solve_end, solve_duration, completion_wall_time)):
            raise ValueError("completion timestamps must be finite")
        if solve_end < solve_start:
            raise ValueError("solve end must not precede solve start")
        if solve_duration != solve_end - solve_start:
            raise ValueError("solve duration must equal its monotonic interval")


def _with_result_quality(result: ControllerUpdateResult) -> ControllerUpdateResult:
    """Copy runner-owned timing into the immutable MPC trace diagnostics."""
    diagnostics = result.diagnostics
    if not isinstance(diagnostics, MpcTraceDiagnostics):
        return result
    return replace(
        result,
        diagnostics=replace(
            diagnostics,
            solve_start_monotonic=result.solve_start_monotonic,
            solve_end_monotonic=result.solve_end_monotonic,
            solve_duration_seconds=result.solve_duration_seconds,
            result_age_seconds=result.result_age_seconds,
            deadline_miss_count=result.deadline_miss_count,
            consecutive_deadline_miss_count=result.consecutive_deadline_miss_count,
            stale_state=result.stale_state,
            recovered=result.recovered,
        ),
    )


def _capture_completed_result(core, temp, revision, *, monotonic_clock, wall_clock):
    core = _adapt_controller_core(core)
    solve_start = monotonic_clock()
    raw = core.update(temp)
    solve_end = monotonic_clock()
    captured = core.capture_status()
    learning = captured.learning
    status = captured.status
    public_cycle_ratio, fan = normalize_controller_output(raw)
    diagnostics = core.trace_diagnostics()
    allocation = core.trace_allocation()
    cycle_ratio = allocation.auger_duty if allocation is not None else public_cycle_ratio
    baseline_allocation = core.trace_baseline_allocation()
    calibration = core.trace_calibration()
    result = ControllerUpdateResult(
        cycle_ratio=cycle_ratio,
        fan=fan,
        input_temperature=float(temp),
        diagnostics=diagnostics,
        learning=learning,
        allocation=allocation,
        baseline_allocation=baseline_allocation,
        calibration=calibration,
        status=status,
        revision=revision,
        solve_start_monotonic=solve_start,
        solve_end_monotonic=solve_end,
        solve_duration_seconds=solve_end - solve_start,
        completed_wall_time=wall_clock(),
    )
    core.register_calibration_result(result)
    return result


class ControllerRunner(ABC, ModelLifecycleRunner):
    @abstractmethod
    def set_target(self, setpoint): ...
    @abstractmethod
    def seed_operating_state(self, seed: EstimatorSeed) -> None: ...

    def estimator_seed_requirements(self) -> tuple[float, int] | None:
        return None

    def bind_estimator_seed_source(
        self,
        source: Callable[[float, int], object] | None,
    ) -> None:
        del source

    @abstractmethod
    def set_safety_ceiling_c(self, ceiling_c): ...
    @abstractmethod
    def request_calibration(self, command: CalibrationCommand) -> None: ...

    @abstractmethod
    def cancel_calibration(self, reason: str) -> None: ...
    @abstractmethod
    def submit(self, temp): ...
    @abstractmethod
    def bind_evidence_context(self, generation: int, session_id: str, cook_id: str | None) -> None: ...
    @abstractmethod
    def retire_evidence_context(self, generation: int) -> None: ...
    @abstractmethod
    def latest(self) -> ControllerUpdateResult: ...
    @abstractmethod
    def reconfigure(self, settings, control, logger=None): ...
    @abstractmethod
    def control_period(self): ...
    @abstractmethod
    def commands_fan(self): ...
    @abstractmethod
    def wants_async(self): ...
    @abstractmethod
    def actuation_mode(self) -> ActuationMode: ...
    @abstractmethod
    def controller_type(self) -> ControllerType | None: ...
    @abstractmethod
    def configuration_revision(self) -> int: ...
    @abstractmethod
    def runs_async(self) -> bool: ...
    @abstractmethod
    def set_output(self, applied): ...
    @abstractmethod
    def observe_frame(self, observation: FrameObservation) -> ObservationSubmission | None: ...
    def complete_frame(self, applied, observation: FrameObservation) -> ObservationSubmission | None:
        """Deliver terminal feedback before its observation as one runner operation."""
        self.set_output(applied)
        return self.observe_frame(observation)

    @abstractmethod
    def drain_observation_outcomes(self) -> ObservationOutcomeDrain: ...
    @abstractmethod
    def restore_model(
        self,
        snapshot,
        *,
        restore_token: str | None = None,
    ) -> ModelRestoreOutcome: ...
    @abstractmethod
    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool: ...
    def record_corpus_fit_disabled(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        return False

    def record_corpus_fit_failed(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        return False

    @abstractmethod
    def controller_state(self) -> dict[str, object]: ...
    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        return False

    def activation_runtime_failure(self, reason: str) -> bool:
        return False

    def rollback_activation(self, reason: str) -> bool:
        return False

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        return ()

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt | None:
        return None

    def drain_restore_outcome(self) -> ModelRestoreOutcome | None:
        """Return the completed worker outcome for one pending restore.

        Synchronous runners return their completed outcome directly. Threaded
        runners return a pending submission first and publish its effective
        authority here after the core has ruled.
        """
        return None

    def stop_and_retain_for_teardown(self) -> bool | None:
        """Stop control ownership while retaining the joined core for teardown."""
        self.stop()
        return None

    def finish_teardown(self, finalizer: Callable[[], None] | None = None) -> None:
        """Run ``finalizer`` after terminal fit work and before core close."""
        if finalizer is not None:
            finalizer()

    @abstractmethod
    def stop(self): ...


class SyncControllerRunner(ControllerRunner):
    def __init__(
        self,
        core,
        *,
        controller_type: ControllerType | None = None,
        model_persistence: ModelPersistenceWorker | None = None,
        trajectory_repository: LearningTrajectoryRepository | None = None,
        fit_partition_digest: Callable[[], str | None] | None = None,
        grey_learning_process: GreyLearningProcessOwner | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        warning_callback: Callable[[ResultStaleState], None] | None = None,
    ):
        from controller.runtime.observation_buffer import ObservationOutcomeBuffer

        self._core = core
        self._activation_core = _activation_core_for(core)
        self._learning_core: _ControllerCorpusLearningCore | None = _controller_corpus_learning_core_for(core)
        self._mpc_learning_core: _MpcLearningCore | None = _mpc_learning_core_for(core)
        self._frame_learning_core: _FrameLearningCore | None = _frame_learning_core_for(core)
        self._temp = None
        self._revision = 0
        self._latest_result = None
        self._observation_buffer = ObservationOutcomeBuffer(_MAX_PENDING_OBSERVATIONS)
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._warning_callback = warning_callback
        self._controller_type = controller_type
        self._model_persistence = model_persistence
        self._trajectory_repository = trajectory_repository
        self._fit_partition_digest = fit_partition_digest
        self._grey_learning_process = grey_learning_process
        self._observation_sequence = 0
        self._configuration_revision = 0
        period = getattr(core, "get_control_period", lambda: None)()
        self._quality = _ResultQualityTracker(_control_period_seconds(period))
        self._stopped = False
        self._retained_for_teardown = False

    def set_target(self, setpoint):
        self._core.set_target(setpoint)

    def seed_operating_state(self, seed: EstimatorSeed) -> None:
        self._core.seed_from_trajectory(seed)

    def estimator_seed_requirements(self) -> tuple[float, int] | None:
        return None if self._mpc_learning_core is None else self._mpc_learning_core.estimator_seed_requirements()

    def bind_estimator_seed_source(
        self,
        source: Callable[[float, int], object] | None,
    ) -> None:
        if self._mpc_learning_core is not None:
            self._mpc_learning_core.bind_estimator_seed_source(source)

    def set_safety_ceiling_c(self, ceiling_c):
        self._core.set_safety_ceiling_c(ceiling_c)

    def request_calibration(self, command: CalibrationCommand) -> None:
        self._core.request_calibration(command)

    def cancel_calibration(self, reason: str) -> None:
        self._core.cancel_calibration(reason)

    def bind_evidence_context(self, generation: int, session_id: str, cook_id: str | None) -> None:
        self._observation_buffer.bind_context(generation, session_id, cook_id)
        if self._learning_core is not None:
            self._learning_core.bind_learning_identity(session_id, cook_id, generation)

    def retire_evidence_context(self, generation: int) -> None:
        self._observation_buffer.retire_context(generation)

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        return False if self._activation_core is None else self._activation_core.restore_activation(persisted, records)

    def activation_runtime_failure(self, reason: str) -> bool:
        return False if self._activation_core is None else self._activation_core.activation_runtime_failure(reason)

    def rollback_activation(self, reason: str) -> bool:
        return False if self._activation_core is None else self._activation_core.rollback_activation(reason)

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        return () if self._activation_core is None else self._activation_core.drain_activation_events()

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt | None:
        return None if self._activation_core is None else self._activation_core.submit_activation_confidence(record)

    def submit(self, temp):
        self._temp = temp

    def latest(self) -> ControllerUpdateResult:
        self._revision += 1
        result = _capture_completed_result(
            self._core,
            self._temp,
            self._revision,
            monotonic_clock=self._monotonic_clock,
            wall_clock=self._wall_clock,
        )
        self._latest_result, transition = self._quality.completed(result)
        if self._latest_result.consecutive_deadline_miss_count >= 2:
            self.activation_runtime_failure("deadline-threshold")
        if transition is not None and self._warning_callback is not None:
            self._warning_callback(transition)
        return self._latest_result

    def latest_from(self, temp):
        self.submit(temp)
        return self.latest()

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(
            settings,
            control,
            logger=logger,
            model_persistence=self._model_persistence,
            trajectory_repository=self._trajectory_repository,
            fit_partition_digest=self._fit_partition_digest,
            grey_learning_process=self._grey_learning_process,
        )
        if status == "Active":
            retired = self._core
            activation_core = _activation_core_for(core)
            learning_core = _controller_corpus_learning_core_for(core)
            mpc_learning_core = _mpc_learning_core_for(core)
            frame_learning_core = _frame_learning_core_for(core)
            self._core = core
            self._activation_core = activation_core
            self._learning_core = learning_core
            self._mpc_learning_core = mpc_learning_core
            self._frame_learning_core = frame_learning_core
            self._controller_type = _controller_type_for(_selected_controller(settings))
            self._configuration_revision += 1
            self._quality.control_period = _control_period_seconds(core.get_control_period())
            _close_core(retired)
        else:
            report_reconfigure_failure(settings, logger=logger)
        return status

    def control_period(self):
        return self._core.get_control_period()

    def commands_fan(self):
        return self._core.commands_fan()

    def wants_async(self):
        return self._core.wants_async()

    def actuation_mode(self) -> ActuationMode:
        return _actuation_mode_for(self._core)

    def controller_type(self) -> ControllerType | None:
        return self._controller_type

    def configuration_revision(self) -> int:
        return self._configuration_revision

    def runs_async(self) -> bool:
        return False

    def stop_and_retain_for_teardown(self) -> bool | None:
        if self._stopped:
            return self._retained_for_teardown
        self._stopped = True
        self._retained_for_teardown = True
        return True

    def finish_teardown(self, finalizer: Callable[[], None] | None = None) -> None:
        if not self._retained_for_teardown:
            return
        self._retained_for_teardown = False
        try:
            if finalizer is not None:
                finalizer()
        except Exception:  # noqa: S110 - teardown callback failure must not block core close
            pass
        finally:
            _close_core(self._core)

    def stop(self):
        if self._stopped:
            if self._retained_for_teardown:
                self.finish_teardown()
            return
        self._stopped = True
        _close_core(self._core)

    def set_output(self, applied):
        self._core.set_output(applied)

    def observe_frame(self, observation: FrameObservation):
        self._observation_sequence += 1
        sequence = self._observation_sequence
        generation = self._configuration_revision
        frame_learning_core = self._frame_learning_core
        if frame_learning_core is None:
            outcome = None
        else:
            try:
                outcome = frame_learning_core.observe_frame(observation)
            except Exception as error:
                outcome = frame_learning_core.observation_failure(observation, error)
        if outcome is None:
            self._observation_buffer.append_terminal_drop(
                ObservationTerminalDrop(sequence, generation, observation, "runner-no-observation-outcome")
            )
        else:
            self._observation_buffer.append_outcome(
                ObservationOutcomeEnvelope(sequence, generation, observation, outcome)
            )
        return ObservationSubmission(sequence, generation)

    def complete_frame(self, applied, observation: FrameObservation):
        self._core.set_output(applied)
        return self.observe_frame(observation)

    def drain_observation_outcomes(self) -> ObservationOutcomeDrain:
        return self._observation_buffer.drain()

    def get_model_snapshot(self):
        return self._core.get_model_snapshot()

    def restore_model(
        self,
        snapshot,
        *,
        restore_token: str | None = None,
    ) -> ModelRestoreOutcome:
        return _completed_restore_outcome(
            self._core,
            _owned_model_snapshot(snapshot),
            restore_token=restore_token,
        )

    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
        return self._schedule_corpus_fit_after_barrier(origin, None)

    def record_corpus_fit_disabled(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        core = _corpus_fit_disabled_core_for(self._core)
        return False if core is None else bool(core.record_corpus_fit_disabled(origin, reason))

    def record_corpus_fit_failed(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        core = _corpus_fit_failure_core_for(self._core)
        return False if core is None else bool(core.record_corpus_fit_failed(origin, reason))

    def _schedule_corpus_fit_after_barrier(
        self,
        origin: CandidateOrigin,
        before_schedule: Callable[[], bool] | None,
    ) -> bool:
        if self._learning_core is None:
            return False
        if before_schedule is not None and not before_schedule():
            return False
        return bool(self._learning_core.schedule_corpus_fit(origin))

    def controller_state(self):
        """A mutable, JSON-safe copy of the current status snapshot."""
        if self._latest_result is not None:
            self._latest_result, transition = self._quality.polled(self._latest_result, self._monotonic_clock())
            if transition is ResultStaleState.STALE:
                self.activation_runtime_failure("stale-result-threshold")
            if transition is not None and self._warning_callback is not None:
                self._warning_callback(transition)
            state = {} if self._latest_result.status is None else _thaw_status(self._latest_result.status)
            if self.actuation_mode() is ActuationMode.FRAMED_PULSE:
                state.update(_quality_status(self._latest_result))
            return state
        status = self._core.get_status()
        return {} if status is None else _thaw_status(status)


_UNSET = object()

# Hold reports once per work-loop tick, and that loop runs at roughly 20 Hz
# (`ControlMode.run` sleeps 0.05 s), while the worker drains only once per
# controller solve. This ceiling spans a stalled solve without letting the
# backlog grow without bound; the oldest reports are the ones to lose, since
# a consumer identifying a process model cares about recent duty.
_MAX_PENDING_OUTPUTS = 2048

# A completed learning frame covers 20 seconds. Thirty retained frames span the
# required ten-minute recovery window while bounding the worker's handoff.
_MAX_PENDING_OBSERVATIONS = 30


def _owned_model_snapshot(snapshot):
    """Deep-copy a snapshot across every caller, core, and worker boundary."""
    return None if snapshot is None else deepcopy(snapshot)


def _completed_restore_outcome(
    core,
    snapshot,
    *,
    restore_token: str | None,
) -> ModelRestoreOutcome:
    prior_authority = _owned_model_snapshot(core.get_model_snapshot())
    accepted = bool(core.restore_model(snapshot))
    restored_state = _owned_model_snapshot(core.get_model_snapshot())
    staged_for_revalidation = accepted and restored_state != snapshot
    effective_authority = prior_authority if staged_for_revalidation else restored_state
    return ModelRestoreOutcome(
        restore_token=restore_token,
        accepted=accepted,
        effective_authority=effective_authority,
        staged_for_revalidation=staged_for_revalidation,
    )


def _safe_initial_status(core):
    """core.get_status() called here runs before the core has proven itself
    with a successful update() -- outside the try/except _build_core uses
    specifically so a constructor bug can never kill the control process with
    the auger already on (see its docstring). An exception here must not
    either."""
    try:
        return core.get_status()
    except Exception:
        return None


def _close_core(core) -> None:
    _adapt_controller_core(core).close()


class ThreadedControllerRunner(ControllerRunner):
    """Runs core.update() on a background thread at the core's control period, so
    an expensive solve never blocks the caller. submit()/latest() are
    non-blocking snapshots; the running core is mutated only by the thread."""

    def __init__(
        self,
        core,
        *,
        controller_type: ControllerType | None = None,
        model_persistence: ModelPersistenceWorker | None = None,
        trajectory_repository: LearningTrajectoryRepository | None = None,
        fit_partition_digest: Callable[[], str | None] | None = None,
        grey_learning_process: GreyLearningProcessOwner | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        warning_callback: Callable[[ResultStaleState], None] | None = None,
        wait_for_period: Callable[[float], None] | None = None,
    ):
        from controller.runtime.observation_buffer import ObservationOutcomeBuffer

        self._lock = threading.Lock()
        with self._lock:
            self._core = core
            self._activation_core = _activation_core_for(core)
            self._learning_core: _ControllerCorpusLearningCore | None = _controller_corpus_learning_core_for(core)
            self._mpc_learning_core: _MpcLearningCore | None = _mpc_learning_core_for(core)
            self._frame_learning_core: _FrameLearningCore | None = _frame_learning_core_for(core)
        self._temp = None
        self._output = ControllerUpdateResult(
            cycle_ratio=0.0,
            fan=None,
            input_temperature=0.0,
            diagnostics=None,
            learning=None,
            status=None,
            revision=0,
            solve_start_monotonic=None,
            solve_end_monotonic=None,
            solve_duration_seconds=None,
            completed_wall_time=None,
        )
        self._revision = 0
        self._pending_target = _UNSET
        self._pending_seed = _UNSET
        self._pending_seed_source = _UNSET
        self._seed_pending_for_solve = False
        self._seed_failure: str | None = None
        self._pending_safety_ceiling_c = _UNSET
        self._pending_core = None
        self._pending_learning_identities: collections.deque[tuple[int, str, str | None]] = collections.deque()
        self._pending_controller_type = None
        self._configuration_revision = 0
        self._pending_dispatches: collections.deque[tuple[str, object]] = collections.deque()
        self._latest_delivered_output = None
        self._pending_dropped = 0
        self._pending_restore = None
        #: The worker owns the completed restore verdict and effective control
        #: authority. Held until a caller drains it.
        self._restore_outcome: ModelRestoreOutcome | None = None
        self._pending_calibrations: collections.deque[tuple[str, object]] = collections.deque()
        self._pending_observations: list[tuple[int, int, FrameObservation]] = []

        self._accepted_observations: dict[int, tuple[int, FrameObservation]] = {}
        self._inflight_observations: set[int] = set()
        self._accept_observations = True
        self._dropped_observations = 0
        self._observations_discontinuous: set[int] = set()
        self._observation_sequence = 0
        self._observation_buffer = ObservationOutcomeBuffer(_MAX_PENDING_OBSERVATIONS)
        self._model_snapshot = _owned_model_snapshot(core.get_model_snapshot())
        self._initial_status = _safe_initial_status(core)
        self._control_period = core.get_control_period()
        self._commands_fan = core.commands_fan()
        self._actuation_mode = _actuation_mode_for(core)
        self._controller_type = controller_type
        self._model_persistence = model_persistence
        self._trajectory_repository = trajectory_repository
        self._fit_partition_digest = fit_partition_digest
        self._grey_learning_process = grey_learning_process
        self._quality = _ResultQualityTracker(_control_period_seconds(self._control_period))
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._warning_callback = warning_callback
        self._stop_event = threading.Event()
        self._learning_stop_event = threading.Event()
        self._learning_condition = threading.Condition(self._lock)
        self._learning_poll_core = None
        self._learning_poll_failure: str | None = None
        self._work_event = threading.Event()
        if wait_for_period is None:

            def wait_for_work(period: float) -> None:
                self._work_event.wait(period)
                self._work_event.clear()

            self._wait_for_period = wait_for_work
        else:
            self._wait_for_period = wait_for_period
        self._retain_core_for_teardown = False
        self._final_core_closed = False
        self._final_core_closing = False
        self._teardown_finalizer: Callable[[], None] | None = None
        self._teardown_finalizer_ran = False
        self._corpus_fit_thread: threading.Thread | None = None
        self._corpus_fit_plans: collections.deque[_CorpusFitPlan] = collections.deque()
        self._close_final_core_when_idle = False
        self._controller_worker_finished = False
        self._learning_thread = threading.Thread(target=self._learning_loop, daemon=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._learning_thread.start()
        self._thread.start()

    def bind_evidence_context(self, generation: int, session_id: str, cook_id: str | None) -> None:
        learning_core: _ControllerCorpusLearningCore | None = None
        with self._lock:
            self._observation_buffer.bind_context(generation, session_id, cook_id)
            pending_generation = self._configuration_revision + 1
            if self._pending_core is not None and generation == pending_generation:
                self._pending_learning_identities.append(
                    (generation, session_id, cook_id),
                )
            else:
                learning_core = self._learning_core
        if learning_core is not None:
            learning_core.bind_learning_identity(session_id, cook_id, generation)

    def retire_evidence_context(self, generation: int) -> None:
        with self._lock:
            self._observation_buffer.retire_context(generation)

    def _terminalize_observation_locked(self, sequence: int, reason: str) -> None:
        accepted = self._accepted_observations.pop(sequence, None)
        self._inflight_observations.discard(sequence)
        if accepted is None:
            return
        generation, observation = accepted
        self._observation_buffer.append_terminal_drop(
            ObservationTerminalDrop(sequence, generation, observation, reason)
        )

    def _complete_observation_locked(
        self, sequence: int, generation: int, observation: FrameObservation, outcome: object
    ) -> None:
        if self._accepted_observations.pop(sequence, None) is None:
            return
        self._inflight_observations.discard(sequence)
        if outcome is None:
            self._observation_buffer.append_terminal_drop(
                ObservationTerminalDrop(sequence, generation, observation, "runner-no-observation-outcome")
            )
            return
        self._observation_buffer.append_outcome(ObservationOutcomeEnvelope(sequence, generation, observation, outcome))

    def _learning_loop(self):
        """Drain fitting results and prepare candidates away from control solves."""
        while not self._learning_stop_event.is_set():
            with self._learning_condition:
                core = self._core
                learning_core = self._learning_core
                self._learning_poll_core = core
            plan: _CorpusFitPlan | None = None
            try:
                if learning_core is not None:
                    with self._lock:
                        plan = self._corpus_fit_plans[0] if self._corpus_fit_plans else None
                    if plan is not None and not plan.scheduled:
                        barrier_ready = False
                        try:
                            barrier_ready = plan.before_schedule is None or bool(plan.before_schedule())
                        except Exception as error:
                            self._fail_scheduled_corpus_fit(
                                "corpus-barrier-failed",
                                f"{type(error).__name__}: {error}",
                                origin=plan.origin,
                                learning_core=learning_core,
                            )
                            with self._lock:
                                self._corpus_fit_plans.clear()
                            plan = None
                        if plan is not None:
                            if not barrier_ready:
                                self._fail_scheduled_corpus_fit(
                                    "corpus-barrier-failed",
                                    "trajectory persistence barrier did not become durable",
                                    origin=plan.origin,
                                    learning_core=learning_core,
                                )
                                with self._lock:
                                    self._corpus_fit_plans.clear()
                                plan = None
                            else:
                                try:
                                    ticket = learning_core._schedule_corpus_fit_ticket(
                                        plan.origin,
                                    )
                                except Exception as error:
                                    self._fail_scheduled_corpus_fit(
                                        "fit-submission-failed",
                                        f"{type(error).__name__}: {error}",
                                        learning_core=learning_core,
                                    )
                                    with self._lock:
                                        self._corpus_fit_plans.clear()
                                    plan = None
                                else:
                                    if not isinstance(ticket, str) or not ticket:
                                        self._fail_scheduled_corpus_fit(
                                            "fit-submission-failed",
                                            "core rejected corpus fit scheduling",
                                            learning_core=learning_core,
                                        )
                                        with self._lock:
                                            self._corpus_fit_plans.clear()
                                        plan = None
                                    else:
                                        with self._lock:
                                            plan.ticket = ticket
                                            plan.scheduled = True
                    learning_core.poll_learning_off_path()
                    ticket_terminal = (
                        plan is not None
                        and plan.ticket is not None
                        and learning_core._consume_terminal_corpus_fit_ticket(
                            plan.ticket,
                            plan.origin,
                        )
                    )
                    if plan is not None and ticket_terminal:
                        with self._lock:
                            if self._corpus_fit_plans and self._corpus_fit_plans[0] is plan:
                                self._corpus_fit_plans.popleft()
            except Exception as error:
                # Learning is optional control evidence. Fail its corpus
                # lifecycle closed without killing the dispatcher or controller.
                self._fail_scheduled_corpus_fit(
                    "fit-poll-failed",
                    error,
                    learning_core=learning_core,
                )
                with self._lock:
                    self._corpus_fit_plans.clear()
                    self._learning_poll_failure = f"{type(error).__name__}: {error}"
            finally:
                with self._learning_condition:
                    if self._learning_poll_core is core:
                        self._learning_poll_core = None
                    self._learning_condition.notify_all()
            self._learning_stop_event.wait(0.05)

    def _wait_for_learning_release(self, core):
        with self._learning_condition:
            while self._learning_poll_core is core:
                self._learning_condition.wait()

    def _terminate_pair_activation(self, reason: str) -> None:
        activation_core = self._activation_core
        if activation_core is not None:
            activation_core.terminate_mpc_activation(reason)

    def _close_final_core(self) -> None:
        with self._lock:
            if self._final_core_closed or self._final_core_closing:
                return
            self._final_core_closing = True
            core = self._core
            finalizer = None if self._teardown_finalizer_ran else self._teardown_finalizer
            if finalizer is not None:
                self._teardown_finalizer_ran = True
        try:
            if finalizer is not None:
                try:
                    finalizer()
                except Exception:  # noqa: S110 - teardown callback failure must not block core close
                    pass
            _close_core(core)
        finally:
            late_finalizer = None
            with self._lock:
                self._final_core_closed = True
                self._final_core_closing = False
                if not self._teardown_finalizer_ran and self._teardown_finalizer is not None:
                    self._teardown_finalizer_ran = True
                    late_finalizer = self._teardown_finalizer
            if late_finalizer is not None:
                try:
                    late_finalizer()
                except Exception:  # noqa: S110 - teardown callback failure must not block core close
                    pass

    def _controller_worker_exiting(self) -> None:
        with self._lock:
            self._controller_worker_finished = True
            if not self._retain_core_for_teardown:
                self._close_final_core_when_idle = True
            fit_pending = self._corpus_fit_thread is not None
            should_close = self._close_final_core_when_idle and not fit_pending
        if should_close:
            self._close_final_core()

    def _advance_pair_activation(self) -> bool:
        """Delegate the nonblocking boundary to the sole activation state owner."""
        return True if self._activation_core is None else self._activation_core.advance_activation()

    def _loop(self):
        while True:
            stopping = self._stop_event.is_set()
            with self._lock:
                mpc_learning_core = self._mpc_learning_core
                frame_learning_core = self._frame_learning_core
                target = self._pending_target
                self._pending_target = _UNSET
                seed = self._pending_seed
                self._pending_seed = _UNSET
                if self._pending_core is None:
                    seed_source = self._pending_seed_source
                    self._pending_seed_source = _UNSET
                else:
                    seed_source = _UNSET
                safety_ceiling_c = self._pending_safety_ceiling_c
                self._pending_safety_ceiling_c = _UNSET
                new_core = None
                new_learning_core = None
                new_mpc_learning_core = None
                new_frame_learning_core = None
                pending_learning_identities = ()
                handoff_output = None
                new_controller_type = None
                retired_core = None
                pending_dispatches = tuple(self._pending_dispatches)
                self._pending_dispatches.clear()
                restore = self._pending_restore
                self._pending_restore = None

                pending_calibrations = tuple(self._pending_calibrations)
                self._pending_calibrations.clear()
            # A pending core is installed only after the old core has drained
            # the returned generation bound to the consuming core.
            if restore is not None:
                restore_token, restore_snapshot = restore
                restore_outcome = _completed_restore_outcome(
                    self._core,
                    restore_snapshot,
                    restore_token=restore_token,
                )
                with self._lock:
                    self._restore_outcome = restore_outcome
                    self._model_snapshot = _owned_model_snapshot(
                        restore_outcome.effective_authority,
                    )
            if seed_source is not _UNSET and mpc_learning_core is not None:
                mpc_learning_core.bind_estimator_seed_source(seed_source)
            if seed is not _UNSET:
                seed_value, seed_ack, seed_outcome = seed
                seed_failure: str | None = None
                try:
                    self._core.seed_from_trajectory(seed_value)
                except Exception as error:
                    seed_failure = f"{type(error).__name__}: {error}"
                with self._lock:
                    self._seed_failure = seed_failure
                    if seed_failure is None and self._pending_seed is _UNSET:
                        self._seed_pending_for_solve = False
                seed_outcome.append(seed_failure)
                seed_ack.set()
            if safety_ceiling_c is not _UNSET:
                self._core.set_safety_ceiling_c(safety_ceiling_c)
            if target is not _UNSET:
                self._core.set_target(target)
            # A command must reach the core before the temperature that command
            # caused, and in the order the auger saw it.
            for operation, payload in pending_dispatches:
                if operation == "output":
                    self._core.set_output(payload)
                    with self._lock:
                        self._latest_delivered_output = payload
                    continue
                if operation == "activation":
                    activation_core = self._activation_core
                    if activation_core is None:
                        continue
                    activation_operation, activation_payload = payload
                    if activation_operation == "restore":
                        persisted, records = activation_payload
                        if not activation_core.restore_activation(
                            persisted,
                            records,
                        ):
                            self._terminate_pair_activation("activation-recovery-failed")
                    elif activation_operation == "rollback":
                        activation_core.rollback_activation(activation_payload)
                    else:
                        activation_core.activation_runtime_failure(activation_payload)
                    continue
                sequence, generation, applied, observation = payload
                self._core.set_output(applied)
                with self._lock:
                    self._latest_delivered_output = applied
                if frame_learning_core is None:
                    outcome = None
                else:
                    try:
                        outcome = frame_learning_core.observe_frame(observation)
                    except Exception as error:
                        outcome = frame_learning_core.observation_failure(
                            observation,
                            error,
                        )
                with self._lock:
                    if outcome is None:
                        self._terminalize_observation_locked(sequence, "runner-no-observation-outcome")
                    else:
                        self._complete_observation_locked(sequence, generation, observation, outcome)
            # Learner calls must never hold _lock. Drain until a lock-protected
            # empty observation queue commits this iteration's temperature
            # update; an observation that wins the lock before that commit is
            # necessarily delivered first.
            while True:
                with self._lock:
                    if self._pending_observations:
                        if self._pending_core is not None:
                            pending_observations = [
                                item for item in self._pending_observations if item[1] == self._configuration_revision
                            ]
                            self._pending_observations = [
                                item for item in self._pending_observations if item[1] != self._configuration_revision
                            ]
                            handoff_batch = bool(pending_observations)
                        else:
                            pending_observations = self._pending_observations
                            self._pending_observations = []
                            handoff_batch = False
                        if pending_observations and pending_observations[0][1] in self._observations_discontinuous:
                            sequence, generation, observation = pending_observations[0]
                            observation = replace(observation, continuous=False)
                            pending_observations[0] = (sequence, generation, observation)
                            self._accepted_observations[sequence] = (generation, observation)
                            self._observations_discontinuous.discard(generation)
                        for sequence, _, _ in pending_observations:
                            self._inflight_observations.add(sequence)
                        update_temp = _UNSET if pending_observations or self._seed_pending_for_solve else self._temp
                    else:
                        pending_observations = []
                        handoff_batch = False
                        update_temp = _UNSET if self._seed_pending_for_solve else self._temp
                if pending_observations:
                    if frame_learning_core is None:
                        for sequence, _, _ in pending_observations:
                            with self._lock:
                                self._terminalize_observation_locked(
                                    sequence,
                                    "runner-no-observation-learner",
                                )
                    else:
                        for sequence, generation, observation in pending_observations:
                            try:
                                outcome = frame_learning_core.observe_frame(observation)
                            except Exception as error:
                                with self._lock:
                                    self._observations_discontinuous.add(generation)
                                try:
                                    outcome = frame_learning_core.observation_failure(
                                        observation,
                                        error,
                                    )
                                except Exception:
                                    outcome = None
                                if outcome is None:
                                    outcome = {
                                        "role_generation": observation.role_generation,
                                        "eligible": False,
                                        "rejection_reasons": ("learner-exception",),
                                        "learner_error": f"{type(error).__name__}: {error}",
                                    }
                            with self._lock:
                                self._complete_observation_locked(
                                    sequence,
                                    generation,
                                    observation,
                                    outcome,
                                )
                    if handoff_batch:
                        break
                    continue
                break
            with self._lock:
                if self._pending_core is not None:
                    new_core = self._pending_core
                    new_learning_core = _controller_corpus_learning_core_for(new_core)
                    new_mpc_learning_core = _mpc_learning_core_for(new_core)
                    new_frame_learning_core = _frame_learning_core_for(new_core)
                    new_controller_type = self._pending_controller_type
                    self._pending_core = None
                    self._pending_controller_type = None
                    retired_core = self._core
                    self._core = new_core
                    self._activation_core = _activation_core_for(new_core)
                    self._learning_core = new_learning_core
                    self._mpc_learning_core = new_mpc_learning_core
                    self._frame_learning_core = new_frame_learning_core
                    self._control_period = new_core.get_control_period()
                    self._commands_fan = new_core.commands_fan()
                    self._actuation_mode = _actuation_mode_for(new_core)
                    self._controller_type = new_controller_type
                    if new_controller_type is ControllerType.MPC:
                        self._seed_pending_for_solve = True
                        self._seed_failure = None
                    self._quality.control_period = _control_period_seconds(self._control_period)
                    self._configuration_revision += 1
                    pending_learning_identities = tuple(self._pending_learning_identities)
                    self._pending_learning_identities.clear()
                    handoff_output = self._latest_delivered_output
            if new_core is not None:
                if new_learning_core is not None:
                    for generation, session_id, cook_id in pending_learning_identities:
                        new_learning_core.bind_learning_identity(
                            session_id,
                            cook_id,
                            generation,
                        )
                # The replacement owns every operation that raced with its
                # installation, before its first physical handoff and solve.
                for operation, payload in pending_calibrations:
                    if operation == "command":
                        new_core.request_calibration(payload)
                    else:
                        new_core.cancel_calibration(payload)
                if handoff_output is not None:
                    new_core.set_output(handoff_output)
                self._wait_for_learning_release(retired_core)
                _close_core(retired_core)
                continue
            if not self._advance_pair_activation():
                if stopping:
                    self._learning_thread.join()
                    self._controller_worker_exiting()
                    return
                self._wait_for_period(self._control_period or 1.0)
                continue
            for operation, payload in pending_calibrations:
                if operation == "command":
                    self._core.request_calibration(payload)
                else:
                    self._core.cancel_calibration(payload)
            if update_temp is not _UNSET and update_temp is not None:
                result = _capture_completed_result(
                    self._core,
                    update_temp,
                    self._revision + 1,
                    monotonic_clock=self._monotonic_clock,
                    wall_clock=self._wall_clock,
                )
                model = _owned_model_snapshot(self._core.get_model_snapshot())
                with self._lock:
                    result, transition = self._quality.completed(result)
                    self._revision = result.revision
                    self._output = result
                    self._model_snapshot = model
                    if result.consecutive_deadline_miss_count >= 2 and self._activation_core is not None:
                        self._append_dispatch_locked(
                            "activation",
                            ("fallback", "deadline-threshold"),
                        )
                if transition is not None and self._warning_callback is not None:
                    self._warning_callback(transition)
            if stopping:
                with self._lock:
                    if self._pending_observations:
                        continue
                self._learning_thread.join()
                self._controller_worker_exiting()
                return
            self._wait_for_period(self._control_period or 1.0)

    def set_target(self, setpoint):
        with self._lock:
            self._pending_target = setpoint

    def seed_operating_state(self, seed: EstimatorSeed) -> None:
        acknowledgement = threading.Event()
        outcome: list[str | None] = []
        with self._lock:
            self._pending_seed = (seed, acknowledgement, outcome)
            self._seed_pending_for_solve = True
            self._seed_failure = None
            timeout = max(2.0, 2.0 * float(self._control_period or 1.0))
        self._work_event.set()
        if not acknowledgement.wait(timeout):
            raise TimeoutError("threaded MPC estimator seed acknowledgement timed out")
        if outcome[0] is not None:
            raise RuntimeError(f"threaded MPC estimator seed failed: {outcome[0]}")

    def estimator_seed_requirements(self) -> tuple[float, int] | None:
        with self._lock:
            learning_core = self._mpc_learning_core
            return None if learning_core is None else learning_core.estimator_seed_requirements()

    def bind_estimator_seed_source(
        self,
        source: Callable[[float, int], object] | None,
    ) -> None:
        with self._lock:
            self._pending_seed_source = source

    def set_safety_ceiling_c(self, ceiling_c):
        with self._lock:
            self._pending_safety_ceiling_c = ceiling_c

    def request_calibration(self, command: CalibrationCommand) -> None:
        with self._lock:
            self._pending_calibrations.append(("command", command))

    def cancel_calibration(self, reason: str) -> None:
        with self._lock:
            self._pending_calibrations.append(("cancel", reason))

    @property
    def mpc_activation_terminated(self) -> bool:
        activation_core = self._activation_core
        return False if activation_core is None else activation_core.activation_terminated

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        if self._activation_core is None:
            return False
        with self._lock:
            self._append_dispatch_locked(
                "activation",
                ("restore", (persisted, tuple(records))),
            )
        return True

    def activation_runtime_failure(self, reason: str) -> bool:
        if self._activation_core is None:
            return False
        with self._lock:
            self._append_dispatch_locked("activation", ("fallback", reason))
        return True

    def rollback_activation(self, reason: str) -> bool:
        if self._activation_core is None:
            return False
        with self._lock:
            self._append_dispatch_locked("activation", ("rollback", reason))
        return True

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        activation_core = self._activation_core
        return () if activation_core is None else activation_core.drain_activation_events()

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt | None:
        activation_core = self._activation_core
        return None if activation_core is None else activation_core.submit_activation_confidence(record)

    def submit(self, temp):
        with self._lock:
            self._temp = temp
            wake_first_solve = self._revision == 0
        if wake_first_solve:
            self._work_event.set()

    def latest(self) -> ControllerUpdateResult:
        with self._lock:
            self._output, transition = self._quality.polled(self._output, self._monotonic_clock())
            result = self._output
            if transition is ResultStaleState.STALE and self._activation_core is not None:
                self._append_dispatch_locked(
                    "activation",
                    ("fallback", "stale-result-threshold"),
                )
        if transition is not None and self._warning_callback is not None:
            self._warning_callback(transition)
        return result

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(
            settings,
            control,
            logger=logger,
            model_persistence=self._model_persistence,
            trajectory_repository=self._trajectory_repository,
            fit_partition_digest=self._fit_partition_digest,
            grey_learning_process=self._grey_learning_process,
        )
        retired_pending = None
        if status == "Active":
            with self._lock:
                retired_pending = self._pending_core
                self._pending_core = core
                self._pending_controller_type = _controller_type_for(
                    _selected_controller(settings),
                )
            _close_core(retired_pending)
        else:
            report_reconfigure_failure(settings, logger=logger)
        return status

    def control_period(self):
        return self._control_period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return True

    def actuation_mode(self) -> ActuationMode:
        with self._lock:
            return self._actuation_mode

    def controller_type(self) -> ControllerType | None:
        with self._lock:
            return self._controller_type

    def configuration_revision(self) -> int:
        with self._lock:
            return self._configuration_revision

    def runs_async(self) -> bool:
        return True

    def controller_state(self):
        with self._lock:
            self._output, transition = self._quality.polled(self._output, self._monotonic_clock())
            status = self._output.status
            source = self._initial_status if status is None else status
            state = {} if source is None else _thaw_status(source)
            if self._actuation_mode is ActuationMode.FRAMED_PULSE and self._output.revision > 0:
                state.update(_quality_status(self._output))
            state["pending_dropped"] = self._pending_dropped
            state["pending_observations"] = len(self._pending_observations)
            state["dropped_observations"] = self._dropped_observations
        if transition is not None and self._warning_callback is not None:
            self._warning_callback(transition)
        return state

    def _append_dispatch_locked(self, operation: str, payload: object) -> None:
        bounded = operation != "activation"
        if bounded:
            bounded_count = sum(queued_operation != "activation" for queued_operation, _ in self._pending_dispatches)
            if bounded_count == _MAX_PENDING_OUTPUTS:
                evicted_index = next(
                    index
                    for index, (queued_operation, _) in enumerate(self._pending_dispatches)
                    if queued_operation != "activation"
                )
                evicted_operation, evicted_payload = self._pending_dispatches[evicted_index]
                del self._pending_dispatches[evicted_index]
                if evicted_operation == "output":
                    self._pending_dropped += 1
                else:
                    sequence, generation, _, observation = evicted_payload
                    self._accepted_observations.pop(sequence, None)
                    self._observation_buffer.append_terminal_drop(
                        ObservationTerminalDrop(
                            sequence,
                            generation,
                            observation,
                            "runner-completed-frame-evicted",
                        )
                    )
        self._pending_dispatches.append((operation, payload))

    def set_output(self, applied):
        with self._lock:
            self._append_dispatch_locked("output", applied)

    def get_model_snapshot(self):
        with self._lock:
            return _owned_model_snapshot(self._model_snapshot)

    def observe_frame(self, observation: FrameObservation):
        with self._lock:
            if not self._accept_observations:
                return None
            self._observation_sequence += 1
            sequence = self._observation_sequence
            generation = self._configuration_revision + (1 if self._pending_core is not None else 0)
            self._accepted_observations[sequence] = (generation, observation)
            index = 0
            while (
                index < len(self._pending_observations)
                and self._pending_observations[index][2].frame_end_s <= observation.frame_end_s
            ):
                index += 1
            self._pending_observations.insert(index, (sequence, generation, observation))
            evicted_sequence = None
            if len(self._pending_observations) > _MAX_PENDING_OBSERVATIONS:
                evicted_sequence, evicted_generation, _ = self._pending_observations.pop(0)
                self._accepted_observations.pop(evicted_sequence, None)
                self._dropped_observations += 1
                self._observations_discontinuous.add(evicted_generation)
            return ObservationSubmission(sequence, generation, evicted_sequence)

    def complete_frame(self, applied, observation: FrameObservation):
        with self._lock:
            if not self._accept_observations:
                return None
            self._observation_sequence += 1
            sequence = self._observation_sequence
            generation = self._configuration_revision
            self._accepted_observations[sequence] = (generation, observation)
            self._append_dispatch_locked("completed-frame", (sequence, generation, applied, observation))
            return ObservationSubmission(sequence, generation)

    def drain_observation_outcomes(self) -> ObservationOutcomeDrain:
        with self._lock:
            return self._observation_buffer.drain()

    def drain_restore_outcome(self) -> ModelRestoreOutcome | None:
        with self._lock:
            outcome = self._restore_outcome
            self._restore_outcome = None
        return outcome

    def restore_model(
        self,
        snapshot,
        *,
        restore_token: str | None = None,
    ) -> ModelRestoreOutcome:
        """Queue a snapshot while leaving effective authority worker-owned."""
        if snapshot is None:
            with self._lock:
                effective_authority = _owned_model_snapshot(self._model_snapshot)
            return ModelRestoreOutcome(
                restore_token=restore_token,
                accepted=False,
                effective_authority=effective_authority,
            )
        with self._lock:
            # A snapshot queued before the worker gets to the previous one
            # supersedes it -- only the most recent restore request matters,
            # since an older one describes a model the caller has moved past.
            self._pending_restore = (
                restore_token,
                _owned_model_snapshot(snapshot),
            )
        return ModelRestoreOutcome(
            restore_token=restore_token,
            accepted=True,
            effective_authority=None,
            pending=True,
        )

    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
        return self._schedule_corpus_fit_after_barrier(origin, None)

    def record_corpus_fit_disabled(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        with self._lock:
            core = _corpus_fit_disabled_core_for(self._core)
        return False if core is None else bool(core.record_corpus_fit_disabled(origin, reason))

    def record_corpus_fit_failed(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        with self._lock:
            core = _corpus_fit_failure_core_for(self._core)
        return False if core is None else bool(core.record_corpus_fit_failed(origin, reason))

    def _schedule_corpus_fit_after_barrier(
        self,
        origin: CandidateOrigin,
        before_schedule: Callable[[], bool] | None,
    ) -> bool:
        with self._lock:
            if self._learning_core is None:
                return False
            live_dispatcher = not self._learning_stop_event.is_set() and not self._controller_worker_finished
            self._corpus_fit_plans.append(
                _CorpusFitPlan(origin, before_schedule),
            )
            if live_dispatcher or self._corpus_fit_thread is not None:
                return True
            thread = threading.Thread(
                target=self._drain_scheduled_corpus_fit,
                daemon=True,
            )
            self._corpus_fit_thread = thread
        thread.start()
        return True

    def _fail_scheduled_corpus_fit(
        self,
        code: str,
        error: BaseException | str,
        *,
        learning_core: _ControllerCorpusLearningCore | None = None,
        origin: CandidateOrigin | None = None,
    ) -> None:
        if learning_core is None:
            with self._lock:
                learning_core = self._learning_core
        if learning_core is None:
            return
        detail = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        try:
            if (
                origin is not None
                and isinstance(learning_core, _CorpusFitFailureCore)
                and learning_core.record_corpus_fit_failed(
                    origin,
                    f"{code}: {detail}",
                )
            ):
                return
            learning_core.fail_corpus_fit(code, error)
        except Exception as failure_error:
            with self._lock:
                self._learning_poll_failure = f"{type(failure_error).__name__}: {failure_error}"

    def _drain_scheduled_corpus_fit(self) -> None:
        try:
            with self._lock:
                learning_core = self._learning_core
            if learning_core is None:
                with self._lock:
                    self._corpus_fit_plans.clear()
                return
            while True:
                with self._lock:
                    if not self._corpus_fit_plans:
                        return
                    plan = self._corpus_fit_plans[0]
                if not plan.scheduled:
                    try:
                        barrier_ready = plan.before_schedule is None or bool(
                            plan.before_schedule(),
                        )
                    except Exception as error:
                        self._fail_scheduled_corpus_fit(
                            "corpus-barrier-failed",
                            error,
                            origin=plan.origin,
                            learning_core=learning_core,
                        )
                        with self._lock:
                            self._corpus_fit_plans.clear()
                        return
                    if not barrier_ready:
                        self._fail_scheduled_corpus_fit(
                            "corpus-barrier-failed",
                            "trajectory persistence barrier did not become durable",
                            origin=plan.origin,
                            learning_core=learning_core,
                        )
                        with self._lock:
                            self._corpus_fit_plans.clear()
                        return
                    try:
                        ticket = learning_core._schedule_corpus_fit_ticket(
                            plan.origin,
                        )
                    except Exception as error:
                        self._fail_scheduled_corpus_fit(
                            "fit-submission-failed",
                            error,
                            learning_core=learning_core,
                        )
                        with self._lock:
                            self._corpus_fit_plans.clear()
                        return
                    if not isinstance(ticket, str) or not ticket:
                        if plan.origin is CandidateOrigin.PASSIVE_ONLINE:
                            with self._lock:
                                if self._corpus_fit_plans and self._corpus_fit_plans[0] is plan:
                                    self._corpus_fit_plans.popleft()
                            continue
                        self._fail_scheduled_corpus_fit(
                            "fit-submission-failed",
                            "core rejected corpus fit scheduling",
                            learning_core=learning_core,
                        )
                        with self._lock:
                            self._corpus_fit_plans.clear()
                        return
                    with self._lock:
                        plan.ticket = ticket
                        plan.scheduled = True
                try:
                    learning_core.poll_learning_off_path()
                except Exception as error:
                    self._fail_scheduled_corpus_fit(
                        "fit-poll-failed",
                        error,
                        learning_core=learning_core,
                    )
                    with self._lock:
                        self._learning_poll_failure = f"{type(error).__name__}: {error}"
                        self._corpus_fit_plans.clear()
                    return
                ticket_terminal = plan.ticket is not None and learning_core._consume_terminal_corpus_fit_ticket(
                    plan.ticket,
                    plan.origin,
                )
                if ticket_terminal:
                    with self._lock:
                        if self._corpus_fit_plans and self._corpus_fit_plans[0] is plan:
                            self._corpus_fit_plans.popleft()
                    continue
                try:
                    state = getattr(
                        learning_core.get_learning_diagnostics(),
                        "state",
                        None,
                    )
                except Exception as error:
                    self._fail_scheduled_corpus_fit(
                        "fit-poll-failed",
                        error,
                        learning_core=learning_core,
                    )
                    with self._lock:
                        self._corpus_fit_plans.clear()
                    return
                if isinstance(state, Mapping) and state.get("failure") is not None:
                    with self._lock:
                        self._corpus_fit_plans.clear()
                    return
                time.sleep(0.02)
        finally:
            with self._lock:
                successor = None
                if self._corpus_fit_plans:
                    successor = threading.Thread(
                        target=self._drain_scheduled_corpus_fit,
                        daemon=True,
                    )
                    self._corpus_fit_thread = successor
                should_close = (
                    successor is None and self._close_final_core_when_idle and self._controller_worker_finished
                )
                if successor is None and not should_close:
                    self._corpus_fit_thread = None
            if successor is not None:
                successor.start()
            elif should_close:
                try:
                    self._close_final_core()
                finally:
                    with self._lock:
                        self._corpus_fit_thread = None

    def _stop_and_join(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._learning_stop_event.set()
            self._work_event.set()
            self._accept_observations = False
        close_wait = getattr(self._wait_for_period, "close", None)
        if callable(close_wait):
            close_wait()
        self._thread.join(timeout=2.0)
        if self._thread.is_alive():
            with self._lock:
                self._pending_observations.clear()
                while self._pending_dispatches:
                    operation, payload = self._pending_dispatches.popleft()
                    if operation == "completed-frame":
                        sequence, _, _, _ = payload
                        self._terminalize_observation_locked(sequence, "runner-stop-timeout")
                for sequence in tuple(self._accepted_observations):
                    self._terminalize_observation_locked(sequence, "runner-stop-timeout")

    def stop_and_retain_for_teardown(self) -> bool | None:
        with self._lock:
            self._retain_core_for_teardown = True
            self._close_final_core_when_idle = False
        self._stop_and_join()
        with self._lock:
            return self._controller_worker_finished

    def finish_teardown(
        self,
        finalizer: Callable[[], None] | None = None,
    ) -> None:
        with self._lock:
            self._retain_core_for_teardown = False
            self._close_final_core_when_idle = True
            immediate_finalizer = None
            if finalizer is not None and self._teardown_finalizer is None and not self._teardown_finalizer_ran:
                self._teardown_finalizer = finalizer
                if self._final_core_closed:
                    self._teardown_finalizer_ran = True
                    immediate_finalizer = finalizer
            should_close = self._controller_worker_finished and self._corpus_fit_thread is None
        if immediate_finalizer is not None:
            try:
                immediate_finalizer()
            except Exception:  # noqa: S110 - teardown callback failure must not block core close
                pass
        elif should_close:
            self._close_final_core()

    def stop(self):
        with self._lock:
            self._retain_core_for_teardown = False
            self._close_final_core_when_idle = True
        self._stop_and_join()
        with self._lock:
            should_close = self._controller_worker_finished and self._corpus_fit_thread is None
        if should_close:
            self._close_final_core()


FALLBACK_CONTROLLER = "pid"


def _selected_controller(settings):
    """settings['controller']['selected'], tolerating a malformed settings tree.

    The reporting paths below run precisely when something is already wrong, so
    they must not be the thing that raises.
    """
    try:
        return settings["controller"]["selected"]
    except KeyError, TypeError:
        return None


def _raise_banner(text, logger=None):
    """Put `text` in front of the user (dashboard banner + control log).

    Same idiom build_devices() uses for a display or grill platform that will not
    load. Deliberately best-effort: a datastore hiccup while reporting a
    controller problem must not itself take down the control loop.
    """
    try:
        from common.common import ErrorKind
        from common.persistence.runtime import read_errors, write_errors

        errors = read_errors(ErrorKind.CONTROL)
        errors.append(text)
        write_errors(ErrorKind.CONTROL, errors)
    except Exception:
        logging.getLogger(__name__).exception("Could not persist controller failure banner")
    if logger is not None:
        try:
            logger.error(text)
        except Exception:
            logging.getLogger(__name__).exception("Configured controller logger rejected failure banner")


def _dependency_hint(controller_type, settings):
    """A dependency/load failure hint, or "" if dependencies are not the problem."""
    try:
        from common.controller_deps import check_controller_dependencies

        config = (settings.get("controller") or {}).get("config", {}).get(controller_type, {})
        missing = check_controller_dependencies(controller_type, config)
    except Exception as exc:
        if controller_type == "mpc":
            detail = str(exc)
            return f"{detail} " if detail else ""
        return ""
    if missing is None:
        return ""
    names = ", ".join(missing.modules)
    return (
        f"It needs the {names} package, which is not part of a standard PiFire install. "
        "Install the missing base dependency before selecting this controller. "
    )


def _build_core(
    settings,
    control,
    logger=None,
    controller_type=None,
    event_logger=None,
    model_persistence: ModelPersistenceWorker | None = None,
    trajectory_repository: LearningTrajectoryRepository | None = None,
    fit_partition_digest: Callable[[], str | None] | None = None,
    grey_learning_process: GreyLearningProcessOwner | None = None,
):
    """Construct the selected controller core without leaking import or startup failures.

    MPC construction also validates the published acados native release. A
    missing or ABI-incompatible release is reported through the normal inactive
    controller path rather than escaping into the live control process.
    """
    controller_type = controller_type or settings["controller"]["selected"]
    try:
        module = importlib.import_module(f"controller.{controller_type}")
    except Exception:
        if logger is not None:
            logger.exception("Error occurred loading controller module. Trace dump: ")
        return None, "Inactive"
    try:
        controller_kwargs = {"logger": event_logger}
        if controller_type in {"mpc", "pid_sp"}:
            controller_kwargs["trajectory_repository"] = trajectory_repository
            controller_kwargs["fit_partition_digest"] = fit_partition_digest
        if controller_type == "mpc":
            controller_kwargs["activation_persistence"] = model_persistence
            controller_kwargs["grey_learning_process"] = grey_learning_process
        elif controller_type == "pid_sp":
            controller_kwargs["model_persistence"] = model_persistence
        core = module.Controller(
            settings["controller"]["config"][controller_type],
            settings["globals"]["units"],
            settings["cycle_data"],
            **controller_kwargs,
        )
        if controller_type != "mpc":
            core.set_target(control["primary_setpoint"])
    except Exception:
        if logger is not None:
            logger.exception(f"Error occurred building the [{controller_type}] controller. Trace dump: ")
        return None, "Inactive"
    corpus_learning_controller = controller_type in {"mpc", "pid_sp"}
    missing_required_learning = (
        corpus_learning_controller
        and (not isinstance(core, _ControllerCorpusLearningCore) or not isinstance(core, _FrameLearningCore))
    ) or (controller_type == "mpc" and not isinstance(core, _MpcLearningCore))
    if missing_required_learning:
        try:
            _close_core(core)
        except Exception:
            logging.getLogger(__name__).exception("Could not close a controller missing required learning capability")
        if logger is not None:
            capability = "MPC learning capability" if controller_type == "mpc" else "PID-SP learning capability"
            logger.exception(
                f"Error occurred building the [{controller_type}] controller: "
                f"missing required {capability}. Trace dump: "
            )
        return None, "Inactive"
    return _adapt_controller_core(core), "Active"


def _wrap(
    core,
    status,
    controller_type,
    model_persistence: ModelPersistenceWorker | None = None,
    trajectory_repository: LearningTrajectoryRepository | None = None,
    fit_partition_digest: Callable[[], str | None] | None = None,
    grey_learning_process: GreyLearningProcessOwner | None = None,
):
    if core is None:
        return None, status
    actual_type = _controller_type_for(controller_type)
    if core.wants_async():
        return (
            ThreadedControllerRunner(
                core,
                controller_type=actual_type,
                model_persistence=model_persistence,
                trajectory_repository=trajectory_repository,
                fit_partition_digest=fit_partition_digest,
                grey_learning_process=grey_learning_process,
            ),
            status,
        )
    return (
        SyncControllerRunner(
            core,
            controller_type=actual_type,
            model_persistence=model_persistence,
            trajectory_repository=trajectory_repository,
            fit_partition_digest=fit_partition_digest,
            grey_learning_process=grey_learning_process,
        ),
        status,
    )


def build_runner(
    settings,
    control,
    logger=None,
    event_logger=None,
    model_persistence: ModelPersistenceWorker | None = None,
    trajectory_repository: LearningTrajectoryRepository | None = None,
    fit_partition_digest: Callable[[], str | None] | None = None,
    grey_learning_process: GreyLearningProcessOwner | None = None,
):
    """Build the runner for a work cycle, substituting the default controller if
    the selected one will not build.

    Substituting is the safe choice here, and it is not the obvious one, so:
    this is called from HoldMode.setup() AFTER power/fan/auger have already been
    commanded on. Refusing to build aborts the Hold cycle at setup_safety with
    the fire lit and no controller regulating it. Holding at setpoint on the
    default PID controller -- with a banner saying exactly what was substituted
    and why -- keeps the user's cook alive and the grill controllable, which is
    the whole point. It mirrors build_devices(), which loads display.none or the
    prototype platform rather than letting a bad module stop the loop.

    Nothing is written back to settings: the user's choice is preserved so that
    re-saving it (once the missing package is installed) just works.
    """
    core, status = _build_core(
        settings,
        control,
        logger=logger,
        event_logger=event_logger,
        model_persistence=model_persistence,
        trajectory_repository=trajectory_repository,
        fit_partition_digest=fit_partition_digest,
        grey_learning_process=grey_learning_process,
    )
    if core is not None:
        return _wrap(
            core,
            status,
            _selected_controller(settings),
            model_persistence=model_persistence,
            trajectory_repository=trajectory_repository,
            fit_partition_digest=fit_partition_digest,
            grey_learning_process=grey_learning_process,
        )

    selected = _selected_controller(settings)
    if selected == FALLBACK_CONTROLLER:
        _raise_banner(
            f"The [{selected}] controller could not be started and PiFire has no fallback for it. "
            f"The current cook cycle has been stopped. Check logs/control.log for details.",
            logger=logger,
        )
        return None, status

    hint = _dependency_hint(selected, settings)
    core, status = _build_core(
        settings,
        control,
        logger=logger,
        controller_type=FALLBACK_CONTROLLER,
        event_logger=event_logger,
        model_persistence=model_persistence,
        trajectory_repository=trajectory_repository,
        fit_partition_digest=fit_partition_digest,
        grey_learning_process=grey_learning_process,
    )
    if core is None:
        _raise_banner(
            f"The [{selected}] controller could not be started, and neither could the fallback "
            f"[{FALLBACK_CONTROLLER}] controller. {hint}The current cook cycle has been stopped. "
            f"Check logs/control.log for details.",
            logger=logger,
        )
        return None, status

    _raise_banner(
        f"The [{selected}] controller could not be started. {hint}"
        f"PiFire is running the [{FALLBACK_CONTROLLER}] controller instead so the grill stays under control. "
        f"Your controller selection has not been changed.",
        logger=logger,
    )
    return _wrap(
        core,
        status,
        FALLBACK_CONTROLLER,
        model_persistence=model_persistence,
        trajectory_repository=trajectory_repository,
        fit_partition_digest=fit_partition_digest,
        grey_learning_process=grey_learning_process,
    )


def report_reconfigure_failure(settings, logger=None):
    """Tell the user a settings-triggered controller swap did not take.

    The runner keeps the core it was already using (see the `status == "Active"`
    guard in both reconfigure implementations), so a mid-cook switch to a
    controller that will not build is a no-op rather than a loss of control --
    but a silent no-op would leave the user believing MPC is regulating the fire.
    """
    selected = _selected_controller(settings)
    _raise_banner(
        f"Could not switch to the [{selected}] controller. {_dependency_hint(selected, settings)}"
        f"The previous controller is still running your cook.",
        logger=logger,
    )
