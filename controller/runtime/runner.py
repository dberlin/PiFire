"""Temperature-controller execution seam (PID/MPC/etc).

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
from copy import deepcopy
import importlib
import math
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

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

from controller.base import ControllerTraceDiagnostics, MpcTraceDiagnostics, normalize_controller_output
from controller.mpc_allocator import AllocationResult
from controller.runtime.model_fitting import TeardownRefitOutcome
from controller.runtime.model_lifecycle import ModelLifecycleRunner
from controller.runtime.model_persistence import DurableActivationReceipt

if TYPE_CHECKING:
    from controller.model_learning.calibration import CalibrationDecision
    from controller.model_learning.contracts import FrameObservation
    from controller.mpc_calibration import CalibrationCommand


StatusScalar: TypeAlias = None | bool | int | float | str
StatusValue: TypeAlias = StatusScalar | Mapping[str, "StatusValue"] | tuple["StatusValue", ...]


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
class _RefitFinalizer(Protocol):
    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool: ...

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


@dataclass(frozen=True, slots=True)
class ObservationSubmission:
    submission_sequence: int
    configuration_generation: int
    evicted_sequence: int | None = None


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
                f"{session_id}:forecast:{value.origin_sequence}:"
                f"{value.horizon_steps}:{value.completion_time_ms}"
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
            accepted = not rejection_reasons and int(
                getattr(evaluation, "consecutive_wins", 0)
            ) >= 2
        reason = (
            None
            if accepted
            else (
                ";".join(rejection_reasons)
                if rejection_reasons
                else "confidence-window-incomplete"
            )
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


MutableStatusValue: TypeAlias = StatusScalar | dict[str, "MutableStatusValue"] | list["MutableStatusValue"]


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

    def completed(self, result: "ControllerUpdateResult") -> tuple["ControllerUpdateResult", ResultStaleState | None]:
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
        self, result: "ControllerUpdateResult", monotonic_now: float
    ) -> tuple["ControllerUpdateResult", ResultStaleState | None]:
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


def _quality_status(result: "ControllerUpdateResult") -> dict[str, StatusScalar]:
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
    solve_start = monotonic_clock()
    raw = core.update(temp)
    solve_end = monotonic_clock()
    cycle_ratio, fan = normalize_controller_output(raw)
    status = getattr(core, "get_status", lambda: None)()
    diagnostics = getattr(core, "trace_diagnostics", lambda: None)()
    allocation = getattr(core, "trace_allocation", lambda: None)()
    baseline_allocation = getattr(core, "trace_baseline_allocation", lambda: None)()
    calibration = getattr(core, "trace_calibration", lambda: None)()
    result = ControllerUpdateResult(
        cycle_ratio=cycle_ratio,
        fan=fan,
        input_temperature=float(temp),
        diagnostics=diagnostics,
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
    register = getattr(core, "register_calibration_result", None)
    if callable(register):
        register(result)
    return result


class ControllerRunner(ABC, ModelLifecycleRunner):
    @abstractmethod
    def set_target(self, setpoint): ...

    @abstractmethod
    def set_safety_ceiling_c(self, ceiling_c): ...
    @abstractmethod
    def request_calibration(self, command: "CalibrationCommand") -> None: ...

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
    def restore_model(self, snapshot): ...
    @abstractmethod
    def refit_from_cook(self): ...
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

    def stop_for_refit(self) -> bool | None:
        """Stop control ownership while retaining the joined core for teardown fitting."""
        self.stop()
        return None

    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool:
        return False

    def finish_teardown(self) -> None:
        """Release resources retained by stop_for_refit()."""




    @abstractmethod
    def stop(self): ...


class SyncControllerRunner(ControllerRunner):
    def __init__(
        self,
        core,
        *,
        controller_type: ControllerType | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        warning_callback: Callable[[ResultStaleState], None] | None = None,
    ):
        from controller.runtime.observation_buffer import ObservationOutcomeBuffer

        self._core = core
        self._activation_core = core if isinstance(core, _ActivationCore) else None
        self._refit_finalizer = core if isinstance(core, _RefitFinalizer) else None
        self._temp = None
        self._revision = 0
        self._latest_result = None
        self._observation_buffer = ObservationOutcomeBuffer(_MAX_PENDING_OBSERVATIONS)
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._warning_callback = warning_callback
        self._controller_type = controller_type
        self._observation_sequence = 0
        self._configuration_revision = 0
        period = getattr(core, "get_control_period", lambda: None)()
        self._quality = _ResultQualityTracker(_control_period_seconds(period))
        self._stopped = False
        self._retained_for_refit = False

    def set_target(self, setpoint):
        self._core.set_target(setpoint)

    def set_safety_ceiling_c(self, ceiling_c):
        self._core.set_safety_ceiling_c(ceiling_c)

    def request_calibration(self, command: "CalibrationCommand") -> None:
        self._core.request_calibration(command)

    def cancel_calibration(self, reason: str) -> None:
        self._core.cancel_calibration(reason)

    def bind_evidence_context(self, generation: int, session_id: str, cook_id: str | None) -> None:
        self._observation_buffer.bind_context(generation, session_id, cook_id)
        bind = getattr(self._core, "bind_learning_identity", None)
        if callable(bind):
            bind(session_id, cook_id, generation)

    def retire_evidence_context(self, generation: int) -> None:
        self._observation_buffer.retire_context(generation)

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        return (
            False
            if self._activation_core is None
            else self._activation_core.restore_activation(persisted, records)
        )

    def activation_runtime_failure(self, reason: str) -> bool:
        return (
            False
            if self._activation_core is None
            else self._activation_core.activation_runtime_failure(reason)
        )

    def rollback_activation(self, reason: str) -> bool:
        return (
            False
            if self._activation_core is None
            else self._activation_core.rollback_activation(reason)
        )

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        return (
            ()
            if self._activation_core is None
            else self._activation_core.drain_activation_events()
        )

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt | None:
        return (
            None
            if self._activation_core is None
            else self._activation_core.submit_activation_confidence(record)
        )


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
        core, status = _build_core(settings, control, logger=logger)
        if status == "Active":
            retired = self._core
            self._core = core
            self._activation_core = (
                core if isinstance(core, _ActivationCore) else None
            )
            self._refit_finalizer = (
                core if isinstance(core, _RefitFinalizer) else None
            )
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

    def stop_for_refit(self) -> bool | None:
        if self._stopped:
            return self._retained_for_refit
        self._stopped = True
        self._retained_for_refit = True
        return True

    def finish_teardown(self) -> None:
        if not self._retained_for_refit:
            return
        self._retained_for_refit = False
        _close_core(self._core)

    def stop(self):
        if self._stopped:
            if self._retained_for_refit:
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
        observe = getattr(self._core, "observe_frame", None)
        outcome = observe(observation) if observe is not None else None
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

    def restore_model(self, snapshot):
        return self._core.restore_model(snapshot)

    def refit_from_cook(self):
        """Ask the core to learn from the cook that just ended, if it can.

        A controller with no identification of its own simply has nothing to
        refit, which is None rather than an error.
        """
        fn = getattr(self._core, "refit_from_cook", None)
        return fn() if fn is not None else None

    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool:
        return (
            False
            if self._refit_finalizer is None
            else self._refit_finalizer.finalize_cook_refit(outcome)
        )



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
    close = getattr(core, "close", None)
    if callable(close):
        close()




class ThreadedControllerRunner(ControllerRunner):
    """Runs core.update() on a background thread at the core's control period, so
    an expensive solve never blocks the caller. submit()/latest() are
    non-blocking snapshots; the running core is mutated only by the thread."""

    def __init__(
        self,
        core,
        *,
        controller_type: ControllerType | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        warning_callback: Callable[[ResultStaleState], None] | None = None,
        wait_for_period: Callable[[float], None] | None = None,
    ):
        from controller.runtime.observation_buffer import ObservationOutcomeBuffer

        self._core = core
        self._activation_core = core if isinstance(core, _ActivationCore) else None
        self._refit_finalizer = core if isinstance(core, _RefitFinalizer) else None
        self._lock = threading.Lock()
        self._temp = None
        self._output = ControllerUpdateResult(
            cycle_ratio=0.0,
            fan=None,
            input_temperature=0.0,
            diagnostics=None,
            status=None,
            revision=0,
            solve_start_monotonic=None,
            solve_end_monotonic=None,
            solve_duration_seconds=None,
            completed_wall_time=None,
        )
        self._revision = 0
        self._pending_target = _UNSET
        self._pending_safety_ceiling_c = _UNSET
        self._pending_core = None
        self._pending_controller_type = None
        self._configuration_revision = 0
        self._pending_dispatches: collections.deque[tuple[str, object]] = collections.deque()
        self._latest_delivered_output = None
        self._pending_dropped = 0
        self._pending_restore = None
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
        self._quality = _ResultQualityTracker(_control_period_seconds(self._control_period))
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._warning_callback = warning_callback
        self._stop_event = threading.Event()
        self._learning_stop_event = threading.Event()
        self._learning_condition = threading.Condition(self._lock)
        self._learning_poll_core = None
        self._wait_for_period = self._stop_event.wait if wait_for_period is None else wait_for_period
        self._retain_core_for_refit = False
        self._final_core_closed = False
        self._learning_thread = threading.Thread(target=self._learning_loop, daemon=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._learning_thread.start()
        self._thread.start()

    def bind_evidence_context(self, generation: int, session_id: str, cook_id: str | None) -> None:
        with self._lock:
            self._observation_buffer.bind_context(generation, session_id, cook_id)
            target = (
                self._pending_core
                if self._pending_core is not None
                and generation == self._configuration_revision + 1
                else self._core
            )
        bind = getattr(target, "bind_learning_identity", None)
        if callable(bind):
            bind(session_id, cook_id, generation)

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
        self._observation_buffer.append_outcome(
            ObservationOutcomeEnvelope(sequence, generation, observation, outcome)
        )


    def _learning_loop(self):
        """Drain fitting results and prepare candidates away from control solves."""
        while not self._learning_stop_event.is_set():
            with self._learning_condition:
                core = self._core
                self._learning_poll_core = core
            try:
                poll = getattr(core, "poll_learning_off_path", None)
                if callable(poll):
                    poll()
            except Exception:
                # Learning is optional control evidence. A failed drain must not
                # kill either the lifecycle dispatcher or the live controller.
                pass
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
            if self._final_core_closed:
                return
            self._final_core_closed = True
            core = self._core
        _close_core(core)

    def _advance_pair_activation(self) -> bool:
        """Delegate the nonblocking boundary to the sole activation state owner."""
        return (
            True
            if self._activation_core is None
            else self._activation_core.advance_activation()
        )
    def _loop(self):
        while True:
            stopping = self._stop_event.is_set()
            with self._lock:
                target = self._pending_target
                self._pending_target = _UNSET
                safety_ceiling_c = self._pending_safety_ceiling_c
                self._pending_safety_ceiling_c = _UNSET
                new_core = None
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
                self._core.restore_model(restore)
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
                            self._terminate_pair_activation(
                                "activation-recovery-failed"
                            )
                    elif activation_operation == "rollback":
                        activation_core.rollback_activation(activation_payload)
                    else:
                        activation_core.activation_runtime_failure(
                            activation_payload
                        )
                    continue
                sequence, generation, applied, observation = payload
                self._core.set_output(applied)
                with self._lock:
                    self._latest_delivered_output = applied
                observe = getattr(self._core, "observe_frame", None)
                try:
                    outcome = None if observe is None else observe(observation)
                except Exception as error:
                    reject = getattr(self._core, "observation_failure", None)
                    outcome = reject(observation, error) if callable(reject) else None
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
                        update_temp = _UNSET if pending_observations else self._temp
                    else:
                        pending_observations = []
                        handoff_batch = False
                        update_temp = self._temp
                if pending_observations:
                    observe = getattr(self._core, "observe_frame", None)
                    for sequence, generation, observation in pending_observations:
                        if observe is None:
                            with self._lock:
                                self._terminalize_observation_locked(sequence, "runner-no-observation-learner")
                            continue
                        try:
                            outcome = observe(observation)
                        except Exception as error:
                            with self._lock:
                                self._observations_discontinuous.add(generation)
                            reject = getattr(self._core, "observation_failure", None)
                            if callable(reject):
                                try:
                                    outcome = reject(observation, error)
                                except Exception:
                                    outcome = None
                            else:
                                outcome = None
                            if outcome is None:
                                outcome = {
                                    "role_generation": observation.role_generation,
                                    "eligible": False,
                                    "rejection_reasons": ("learner-exception",),
                                    "learner_error": f"{type(error).__name__}: {error}",
                                }
                        with self._lock:
                            self._complete_observation_locked(sequence, generation, observation, outcome)
                    if handoff_batch:
                        break
                    continue
                break
            with self._lock:
                if self._pending_core is not None:
                    new_core = self._pending_core
                    new_controller_type = self._pending_controller_type
                    self._pending_core = None
                    self._pending_controller_type = None
                    retired_core = self._core
                    self._core = new_core
                    self._activation_core = (
                        new_core
                        if isinstance(new_core, _ActivationCore)
                        else None
                    )
                    self._refit_finalizer = (
                        new_core
                        if isinstance(new_core, _RefitFinalizer)
                        else None
                    )
                    self._control_period = new_core.get_control_period()
                    self._commands_fan = new_core.commands_fan()
                    self._actuation_mode = _actuation_mode_for(new_core)
                    self._controller_type = new_controller_type
                    self._quality.control_period = _control_period_seconds(self._control_period)
                    self._configuration_revision += 1
                    handoff_output = self._latest_delivered_output
            if new_core is not None:
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
                    with self._lock:
                        retain_core = self._retain_core_for_refit
                    if not retain_core:
                        self._close_final_core()
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
                    if (
                        result.consecutive_deadline_miss_count >= 2
                        and self._activation_core is not None
                    ):
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
                    retain_core = self._retain_core_for_refit
                self._learning_thread.join()
                if not retain_core:
                    self._close_final_core()
                return
            self._wait_for_period(self._control_period or 1.0)

    def set_target(self, setpoint):
        with self._lock:
            self._pending_target = setpoint

    def set_safety_ceiling_c(self, ceiling_c):
        with self._lock:
            self._pending_safety_ceiling_c = ceiling_c

    def request_calibration(self, command: "CalibrationCommand") -> None:
        with self._lock:
            self._pending_calibrations.append(("command", command))

    def cancel_calibration(self, reason: str) -> None:
        with self._lock:
            self._pending_calibrations.append(("cancel", reason))

    @property
    def mpc_activation_terminated(self) -> bool:
        activation_core = self._activation_core
        return (
            False
            if activation_core is None
            else activation_core.activation_terminated
        )

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
        return (
            ()
            if activation_core is None
            else activation_core.drain_activation_events()
        )

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt | None:
        activation_core = self._activation_core
        return (
            None
            if activation_core is None
            else activation_core.submit_activation_confidence(record)
        )


    def submit(self, temp):
        with self._lock:
            self._temp = temp

    def latest(self) -> ControllerUpdateResult:
        with self._lock:
            self._output, transition = self._quality.polled(self._output, self._monotonic_clock())
            result = self._output
            if (
                transition is ResultStaleState.STALE
                and self._activation_core is not None
            ):
                self._append_dispatch_locked(
                    "activation",
                    ("fallback", "stale-result-threshold"),
                )
        if transition is not None and self._warning_callback is not None:
            self._warning_callback(transition)
        return result

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(settings, control, logger=logger)
        retired_pending = None
        if status == "Active":
            with self._lock:
                retired_pending = self._pending_core
                self._pending_core = core
                self._pending_controller_type = _controller_type_for(_selected_controller(settings))
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
            bounded_count = sum(
                queued_operation != "activation"
                for queued_operation, _ in self._pending_dispatches
            )
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

    def restore_model(self, snapshot):
        """Queue a snapshot for the worker to attempt to adopt.

        True means accepted for restore, not adopted: the core is mutated only
        on the worker thread, so whether the snapshot was actually adopted is
        not knowable from here.
        """
        if snapshot is None:
            return False
        with self._lock:
            # A snapshot queued before the worker gets to the previous one
            # supersedes it -- only the most recent restore request matters,
            # since an older one describes a model the caller has moved past.
            self._pending_restore = _owned_model_snapshot(snapshot)
        return True

    def refit_from_cook(self):
        """Refit the core's model from the cook that just ended.

        Runs synchronously on the CALLER's thread, which is why teardown asks
        for it only after `stop()`: a refit takes seconds and mutates the
        core's config, so it must never overlap a solve.

        `stop()` joins with a timeout and so cannot promise the worker is
        gone. A worker still running would overwrite the republish below on
        its next pass and the cook's learning would vanish without a trace, so
        that case raises instead -- losing a refit is acceptable, losing it
        silently is not.

        The worker is what normally republishes the model snapshot, and it has
        stopped by now, so this republishes it directly: otherwise an adopted
        model would exist in the core and be invisible to the
        `get_model_snapshot()` the caller persists it through.
        """
        if self._thread.is_alive():
            raise RuntimeError("the controller worker did not stop; refusing to refit behind it")
        fn = getattr(self._core, "refit_from_cook", None)
        if fn is None:
            return None
        refit_error = None
        try:
            return fn()
        except BaseException as error:
            refit_error = error
            raise
        finally:
            try:
                model = _owned_model_snapshot(self._core.get_model_snapshot())
                with self._lock:
                    self._model_snapshot = model
            except Exception:
                if refit_error is None:
                    raise

    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool:
        if self._thread.is_alive():
            raise RuntimeError("the controller worker did not stop; refusing to finalize behind it")
        finalized = (
            False
            if self._refit_finalizer is None
            else self._refit_finalizer.finalize_cook_refit(outcome)
        )
        model = _owned_model_snapshot(self._core.get_model_snapshot())
        with self._lock:
            self._model_snapshot = model
        return finalized

    def _stop_and_join(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._learning_stop_event.set()
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

    def stop_for_refit(self) -> bool | None:
        with self._lock:
            self._retain_core_for_refit = True
        self._stop_and_join()
        return not self._thread.is_alive()

    def finish_teardown(self) -> None:
        with self._lock:
            self._retain_core_for_refit = False
            worker_alive = self._thread.is_alive()
        if not worker_alive:
            self._close_final_core()



    def stop(self):
        with self._lock:
            self._retain_core_for_refit = False
        self._stop_and_join()
        if not self._thread.is_alive():
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
        pass
    if logger is not None:
        try:
            logger.error(text)
        except Exception:
            pass


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


def _build_core(settings, control, logger=None, controller_type=None):
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
        core = module.Controller(
            settings["controller"]["config"][controller_type], settings["globals"]["units"], settings["cycle_data"]
        )
        core.set_target(control["primary_setpoint"])
    except Exception:
        if logger is not None:
            logger.exception(f"Error occurred building the [{controller_type}] controller. Trace dump: ")
        return None, "Inactive"
    return core, "Active"


def _wrap(core, status, controller_type):
    if core is None:
        return None, status
    actual_type = _controller_type_for(controller_type)
    if core.wants_async():
        return ThreadedControllerRunner(core, controller_type=actual_type), status
    return SyncControllerRunner(core, controller_type=actual_type), status


def build_runner(settings, control, logger=None):
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
    core, status = _build_core(settings, control, logger=logger)
    if core is not None:
        return _wrap(core, status, _selected_controller(settings))

    selected = _selected_controller(settings)
    if selected == FALLBACK_CONTROLLER:
        _raise_banner(
            f"The [{selected}] controller could not be started and PiFire has no fallback for it. "
            f"The current cook cycle has been stopped. Check logs/control.log for details.",
            logger=logger,
        )
        return None, status

    hint = _dependency_hint(selected, settings)
    core, status = _build_core(settings, control, logger=logger, controller_type=FALLBACK_CONTROLLER)
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
    return _wrap(core, status, FALLBACK_CONTROLLER)


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
