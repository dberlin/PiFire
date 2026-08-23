"""Typed control-trace session ownership for Hold runtime composition."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Protocol

from common.control_trace import (
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTracePayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelEventPayload,
    LearningSnapshotPayload,
    ModelEventType,
    MpcUpdatePayload,
    PidSpUpdatePayload,
    PidUpdatePayload,
    ResultStaleState,
    SafetyEventPayload,
    SafetyEventType,
    TraceEventKind,
    TraceSetting,
    SessionPayload,
)
from common.persistence.protocols import JsonValue
from controller.applied_output import (
    AppliedOutput,
    FrameFeedbackDisposition,
    OutputSource,
    classify_output_source,
)
from controller.base import MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.model_promotion import ReachabilityState
from controller.runtime.framed_pulse import FramedPulseCompletion
from controller.runtime.runner import ControllerUpdateResult


class TraceRecorder(Protocol):
    """The concrete recorder operations owned by a trace session."""

    def record(self, record: ControlTraceRecord) -> None: ...

    def flush_due(self, now_ms: int) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TraceModelAuthority:
    snapshot: Mapping[str, JsonValue]
    provenance: str


@dataclass(frozen=True, slots=True)
class TraceSessionContext:
    controller: ControllerType
    controller_config: Mapping[str, JsonValue]
    temperature_unit: str
    control_period_seconds: float
    fallback_model: TraceModelAuthority | None
    runner_snapshot_fallback_safe: bool
    pulse_slot_seconds: float
    pulse_frame_seconds: float
    fan_authority: bool
    fan_pwm_capable: bool
    fan_min_duty: float
    fan_max_duty: float
    setpoint: float
    ambient_temperature: float
    software_version: str
    build_version: str
    cook_id: str
    runner_generation: int


@dataclass(frozen=True, slots=True)
class TraceSessionIdentity:
    session_id: str
    cook_id: str
    controller: ControllerType
    runner_generation: int


@dataclass(frozen=True, slots=True)
class TraceSessionStatus:
    recorder_available: bool
    warning_active: bool
    pending_model_events: int
    closed: bool


@dataclass(frozen=True, slots=True)
class TraceUpdateContext:
    result: ControllerUpdateResult
    timestamp_ms: int
    controller_interval_seconds: float
    setpoint: float
    prior_requested_auger_duty: float
    prior_realized_auger_duty: float
    prior_fan_duty: float | None
    controls_fan: bool
    lid_open: bool
    manual_override_active: bool
    lifecycle_event: ModelEventPayload | None = None


@dataclass(frozen=True, slots=True)
class TraceUpdateState:
    result_revision: int = -1
    mpc_stale: bool = False


@dataclass(frozen=True, slots=True)
class TraceSafetyContext:
    event: SafetyEventType
    inhibit_reason: InhibitReason
    result_revision: int | None
    detail: str
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class TraceModelContext:
    event: ModelEventType
    detail: str
    snapshot: Mapping[str, JsonValue] | None
    provenance: str | None
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class TraceFrameContext:
    completion: FramedPulseCompletion
    pulse_slot_seconds: float
    frame_seconds: float


@dataclass(frozen=True, slots=True)
class TraceOutputContext:
    timestamp_ms: int
    pulse_frame_result_revision: int
    fan_duty: float | None
    controls_fan: bool = False
    producing_revision: int | None = None
    producing_calibration_revision: int = 0
    producing_calibration_action: str = "none"
    producing_calibration_generation: int = 0
    sample_complete: bool = False
    feedback_disposition: FrameFeedbackDisposition = FrameFeedbackDisposition.PROGRESS
    measured_combustion_load: float | None = None


@dataclass(frozen=True, slots=True)
class TraceAppliedIntervalContext:
    timestamp_ms: int
    sample_complete: bool
    realized_combustion_load: float | None
    controls_fan: bool


@dataclass(frozen=True, slots=True)
class TraceAppliedState:
    interval_start_ms: int | None = None
    result_revision: int = 0
    requested_auger_duty: float = 0.0
    realized_auger_duty: float = 0.0
    output_source: OutputSource | None = None
    fan_duty: float | None = None
    combustion_load: float | None = None


class ControlTraceSession:
    """Own one recorder and all mutable state needed to produce trace envelopes."""

    def __init__(self, recorder: TraceRecorder | None, *, warning: Callable[[str], None]) -> None:
        self._recorder = recorder
        self._warning = warning
        self._identity: TraceSessionIdentity | None = None
        self._warning_active = False
        self._pending_model_events: list[tuple[ModelEventPayload, int]] = []
        self._closed = False
        self._model_authority: TraceModelAuthority | None = None
        self._last_update_payload: MpcUpdatePayload | PidUpdatePayload | PidSpUpdatePayload | None = None
        self._runner_snapshot_fallback_safe = True
        self._update_state = TraceUpdateState()
        self._applied_state = TraceAppliedState()

    @property
    def identity(self) -> TraceSessionIdentity | None:
        return self._identity

    @property
    def status(self) -> TraceSessionStatus:
        return TraceSessionStatus(
            recorder_available=self._recorder is not None,
            warning_active=self._warning_active,
            pending_model_events=len(self._pending_model_events),
            closed=self._closed,
        )

    @property
    def model_authority(self) -> TraceModelAuthority | None:
        return self._model_authority

    @property
    def update_state(self) -> TraceUpdateState:
        return self._update_state

    @property
    def applied_state(self) -> TraceAppliedState:
        return self._applied_state

    def set_model_authority(self, snapshot: Mapping[str, JsonValue], provenance: str) -> None:
        authority = self._validated_authority(snapshot, provenance)
        if authority is None:
            self.clear_model_authority()
            return
        self._model_authority = authority

    def clear_model_authority(self) -> None:
        self._model_authority = None

    def rotate_identity(self, *, runner_snapshot_fallback_safe: bool) -> None:
        """Rotate trace identity and model authority without disturbing the live control interval."""
        self._identity = None
        self.clear_model_authority()
        self._runner_snapshot_fallback_safe = runner_snapshot_fallback_safe

    def rotate(self, *, runner_snapshot_fallback_safe: bool) -> None:
        """Reset all session-local state without losing queued model events."""
        self.rotate_identity(runner_snapshot_fallback_safe=runner_snapshot_fallback_safe)
        self._last_update_payload = None
        self._update_state = TraceUpdateState()
        self._applied_state = TraceAppliedState()

    def ensure_open(self, context: TraceSessionContext, *, timestamp_ms: int) -> TraceSessionIdentity | None:
        if self._closed or self._recorder is None:
            return None
        if self._identity is not None:
            self.flush_pending()
            return self._identity
        if (
            not isinstance(context.controller, ControllerType)
            or not isinstance(context.cook_id, str)
            or not context.cook_id
        ):
            return None

        authority = self._model_authority
        if (
            authority is None
            and self._runner_snapshot_fallback_safe
            and context.runner_snapshot_fallback_safe
            and context.fallback_model is not None
        ):
            authority = self._validated_authority(
                context.fallback_model.snapshot,
                context.fallback_model.provenance,
            )
        revision = self._authority_revision(authority)
        provenance = authority.provenance if authority is not None and revision is not None else None
        payload = SessionPayload(
            controller=context.controller,
            controller_config=self._flatten_settings(context.controller_config),
            temperature_unit=context.temperature_unit,
            control_period_seconds=context.control_period_seconds,
            model_revision=revision,
            model_provenance=provenance,
            pulse_slot_seconds=context.pulse_slot_seconds,
            pulse_frame_seconds=context.pulse_frame_seconds,
            fan_authority=context.fan_authority,
            fan_pwm_capable=context.fan_pwm_capable,
            fan_min_duty=context.fan_min_duty,
            fan_max_duty=context.fan_max_duty,
            setpoint=context.setpoint,
            ambient_temperature=context.ambient_temperature,
            software_version=context.software_version,
            build_version=context.build_version,
        )
        identity = TraceSessionIdentity(
            session_id=str(uuid.uuid4()),
            cook_id=context.cook_id,
            controller=context.controller,
            runner_generation=context.runner_generation,
        )
        self._identity = identity
        if not self.record(TraceEventKind.SESSION, payload, timestamp_ms):
            self._identity = None
            return None
        self.flush_pending()
        return identity

    def record(self, event_kind: TraceEventKind, payload: ControlTracePayload, timestamp_ms: int) -> bool:
        recorder = self._recorder
        identity = self._identity
        if recorder is None or identity is None or self._closed:
            return False
        try:
            recorder.record(
                ControlTraceRecord(
                    ts_ms=timestamp_ms,
                    session_id=identity.session_id,
                    cook_id=identity.cook_id,
                    controller=identity.controller,
                    event_kind=event_kind,
                    payload=payload,
                )
            )
        except Exception as error:
            self._warn_once(f"Control trace record failed: {error}")
            return False
        self._recover_warning()
        return True

    def queue_model_event(self, payload: ModelEventPayload, timestamp_ms: int) -> None:
        self._pending_model_events.append((payload, timestamp_ms))
        self.flush_pending()

    def flush_pending(self) -> None:
        if self._identity is None or self._closed:
            return
        while self._pending_model_events:
            payload, timestamp_ms = self._pending_model_events[0]
            if not self.record(TraceEventKind.MODEL_EVENT, payload, timestamp_ms):
                return
            del self._pending_model_events[0]

    def flush_due(self, now_ms: int) -> bool:
        recorder = self._recorder
        if recorder is None or self._closed:
            return True
        try:
            recorder.flush_due(now_ms)
        except Exception as error:
            self._warn_once(f"Control trace flush failed: {error}")
            return False
        self._recover_warning()
        return True

    def record_safety(self, context: TraceSafetyContext) -> bool:
        revision = context.result_revision
        return self.record(
            TraceEventKind.SAFETY_EVENT,
            SafetyEventPayload(
                event=context.event,
                inhibit_reason=context.inhibit_reason,
                result_revision=revision if revision is not None and revision >= 0 else None,
                detail=context.detail,
            ),
            context.timestamp_ms,
        )

    def record_model(self, context: TraceModelContext) -> bool:
        revision = self._snapshot_revision(context.snapshot)
        payload = ModelEventPayload(
            event=context.event,
            model_revision=revision,
            provenance=context.provenance if revision is not None else None,
            detail=context.detail,
        )
        before = len(self._pending_model_events)
        self.queue_model_event(payload, context.timestamp_ms)
        return len(self._pending_model_events) == before

    def record_update(self, context: TraceUpdateContext) -> bool:
        result = context.result
        if result.revision == 0:
            return False
        diagnostics = result.diagnostics
        stale_observation = (
            isinstance(diagnostics, MpcTraceDiagnostics)
            and result.revision == self._update_state.result_revision
            and not self._update_state.mpc_stale
            and result.stale_state is ResultStaleState.STALE
        )
        if result.revision < self._update_state.result_revision or (
            result.revision == self._update_state.result_revision and not stale_observation
        ):
            return False
        if stale_observation:
            previous = self._last_update_payload
            if not isinstance(previous, MpcUpdatePayload):
                return False
            payload = replace(
                previous,
                result_age_ms=max(0, int(result.result_age_seconds * 1_000)),
                stale=True,
                stale_state=ResultStaleState.STALE,
                recovered=False,
            )
            self.record(TraceEventKind.CONTROL_UPDATE, payload, context.timestamp_ms)
            self._last_update_payload = payload
            self._update_state = TraceUpdateState(result.revision, True)
            return True
        if diagnostics is None or result.completed_wall_time is None or result.solve_end_monotonic is None:
            return False
        learning = (
            None
            if result.learning is None
            else LearningSnapshotPayload(
                schema_version=result.learning.schema_version,
                state=result.learning.as_json(),
            )
        )

        wall_ms = int(result.completed_wall_time * 1_000)
        monotonic_ms = int(result.solve_end_monotonic * 1_000)
        result_age_ms = (
            max(0, int(result.result_age_seconds * 1_000))
            if isinstance(diagnostics, MpcTraceDiagnostics)
            else max(0, context.timestamp_ms - wall_ms)
        )
        observed_dt_seconds = (
            diagnostics.observed_dt_seconds
            if isinstance(diagnostics, PidTraceDiagnostics)
            else result.solve_duration_seconds or 0.0
        )
        raw_output = (
            diagnostics.raw_output
            if isinstance(diagnostics, PidTraceDiagnostics)
            else (
                diagnostics.raw_policy_firing_load
                if diagnostics.raw_policy_firing_load is not None
                else diagnostics.bounded_firing_load
            )
        )
        requested_output = (
            diagnostics.final_output
            if isinstance(diagnostics, PidTraceDiagnostics)
            else diagnostics.bounded_firing_load
        )
        requested_fan_duty = result.fan["duty"] if result.fan is not None else None
        source = classify_output_source(
            lid_open=context.lid_open,
            manual_override_active=context.manual_override_active,
        )
        inhibit = InhibitReason.LID_OPEN if context.lid_open else InhibitReason.NONE
        allocation_payload: AllocationPayload | None = None

        if isinstance(diagnostics, PidSpTraceDiagnostics):
            payload: MpcUpdatePayload | PidUpdatePayload | PidSpUpdatePayload = PidSpUpdatePayload(
                monotonic_ms=monotonic_ms,
                wall_ms=wall_ms,
                result_revision=result.revision,
                result_age_ms=result_age_ms,
                control_period_seconds=context.controller_interval_seconds,
                observed_dt_seconds=observed_dt_seconds,
                setpoint=context.setpoint,
                measured_temperature=result.input_temperature,
                raw_output=raw_output,
                requested_output=requested_output,
                actuation_mode=ActuationMode.FRAMED_PULSE,
                prior_requested_auger_duty=context.prior_requested_auger_duty,
                prior_realized_auger_duty=context.prior_realized_auger_duty,
                requested_fan_duty=requested_fan_duty,
                applied_fan_duty=context.prior_fan_duty,
                output_source=source,
                inhibit_reason=inhibit,
                learning=learning,
                error=diagnostics.error,
                proportional_term=diagnostics.proportional_term,
                integral_term=diagnostics.integral_term,
                derivative_term=diagnostics.derivative_term,
                integral_accumulator=diagnostics.integral_accumulator,
                integral_clamped=diagnostics.integral_clamped,
                derivative_input=diagnostics.derivative_input,
                derivative_state=diagnostics.derivative_state,
                proportional_band=diagnostics.proportional_band,
                kp=diagnostics.kp,
                ki=diagnostics.ki,
                kd=diagnostics.kd,
                center=diagnostics.center,
                previous_temperature=diagnostics.previous_temperature,
                previous_update_ms=max(0, int(diagnostics.previous_update_time * 1_000)),
                measured_rate=diagnostics.measured_rate,
                predicted_temperature=diagnostics.predicted_temperature,
                predicted_error=diagnostics.predicted_error,
                tau_seconds=diagnostics.tau_seconds,
                theta_seconds=diagnostics.theta_seconds,
                stable_window_seconds=diagnostics.stable_window_seconds,
                center_factor=diagnostics.center_factor,
                new_target_before=diagnostics.new_target_before,
                new_target_after=diagnostics.new_target_after,
                target_change_temperature=diagnostics.target_change_temperature,
                target_change_ms=max(0, int(diagnostics.target_change_time * 1_000)),
                branch=diagnostics.branch,
            )
        elif isinstance(diagnostics, PidTraceDiagnostics):
            payload = PidUpdatePayload(
                monotonic_ms=monotonic_ms,
                wall_ms=wall_ms,
                result_revision=result.revision,
                result_age_ms=result_age_ms,
                control_period_seconds=context.controller_interval_seconds,
                observed_dt_seconds=observed_dt_seconds,
                setpoint=context.setpoint,
                measured_temperature=result.input_temperature,
                raw_output=raw_output,
                requested_output=requested_output,
                actuation_mode=ActuationMode.FRAMED_PULSE,
                prior_requested_auger_duty=context.prior_requested_auger_duty,
                prior_realized_auger_duty=context.prior_realized_auger_duty,
                requested_fan_duty=requested_fan_duty,
                applied_fan_duty=context.prior_fan_duty,
                output_source=source,
                inhibit_reason=inhibit,
                learning=learning,
                error=diagnostics.error,
                proportional_term=diagnostics.proportional_term,
                integral_term=diagnostics.integral_term,
                derivative_term=diagnostics.derivative_term,
                integral_accumulator=diagnostics.integral_accumulator,
                integral_clamped=diagnostics.integral_clamped,
                derivative_input=diagnostics.derivative_input,
                derivative_state=diagnostics.derivative_state,
                proportional_band=diagnostics.proportional_band,
                kp=diagnostics.kp,
                ki=diagnostics.ki,
                kd=diagnostics.kd,
                center=diagnostics.center,
                previous_temperature=diagnostics.previous_temperature,
                previous_update_ms=max(0, int(diagnostics.previous_update_time * 1_000)),
            )
        elif isinstance(diagnostics, MpcTraceDiagnostics):
            payload = MpcUpdatePayload(
                monotonic_ms=monotonic_ms,
                wall_ms=wall_ms,
                result_revision=result.revision,
                result_age_ms=result_age_ms,
                control_period_seconds=context.controller_interval_seconds,
                observed_dt_seconds=observed_dt_seconds,
                setpoint=context.setpoint,
                measured_temperature=result.input_temperature,
                raw_output=raw_output,
                requested_output=requested_output,
                actuation_mode=ActuationMode.FRAMED_PULSE,
                prior_requested_auger_duty=context.prior_requested_auger_duty,
                prior_realized_auger_duty=context.prior_realized_auger_duty,
                requested_fan_duty=requested_fan_duty,
                applied_fan_duty=context.prior_fan_duty,
                output_source=source,
                inhibit_reason=inhibit,
                learning=learning,
                state_names=diagnostics.state_names,
                state_values=diagnostics.state_values,
                disturbance_estimate=diagnostics.disturbance_estimate,
                model_revision=diagnostics.model_revision,
                model_provenance=diagnostics.model_provenance,
                raw_policy_firing_load=diagnostics.raw_policy_firing_load,
                equilibrium_feed_forward=diagnostics.equilibrium_feed_forward,
                residual_move=diagnostics.residual_move,
                bounded_firing_load=diagnostics.bounded_firing_load,
                policy_kind=diagnostics.policy_kind,
                failure_state=diagnostics.failure_state,
                solve_start_ms=max(0, int(diagnostics.solve_start_monotonic * 1_000)),
                solve_end_ms=max(0, int(diagnostics.solve_end_monotonic * 1_000)),
                deadline_miss_count=result.deadline_miss_count,
                stale=result.stale_state is ResultStaleState.STALE,
                recovered=result.recovered,
                predicted_feasible=(
                    None
                    if diagnostics.feasibility is None
                    or diagnostics.feasibility.state is ReachabilityState.UNKNOWN_MODEL
                    else diagnostics.feasibility.state is ReachabilityState.REACHABLE
                ),
                predicted_steady_load=(
                    None if diagnostics.feasibility is None else diagnostics.feasibility.predicted_steady_load
                ),
                solve_duration_ms=max(0, int((result.solve_duration_seconds or 0.0) * 1_000)),
                consecutive_deadline_miss_count=result.consecutive_deadline_miss_count,
                stale_state=result.stale_state,
            )
            allocation = result.allocation
            if allocation is not None:
                allocation_payload = AllocationPayload(
                    result_revision=result.revision,
                    normalized_combustion_load=allocation.normalized_combustion_load,
                    requested_auger_duty=allocation.auger_duty,
                    requested_fan_duty=allocation.fan_duty,
                    u_max=allocation.u_max,
                    fan_min_pct=allocation.fan_min_pct,
                    fan_max_pct=allocation.fan_max_pct,
                    fan_enabled=allocation.fan_enabled,
                    mpc_has_fan_authority=context.controls_fan,
                    auger_clamp_reason=allocation.auger_clamp_reason,
                    fan_clamp_reason=allocation.fan_clamp_reason,
                    allocator_revision=allocation.allocator_revision,
                )
        else:
            return False

        self._update_state = TraceUpdateState(
            result.revision,
            isinstance(diagnostics, MpcTraceDiagnostics) and result.stale_state is ResultStaleState.STALE,
        )
        self.record(TraceEventKind.CONTROL_UPDATE, payload, context.timestamp_ms)
        self._last_update_payload = payload
        if context.lifecycle_event is not None and isinstance(diagnostics, MpcTraceDiagnostics):
            self.queue_model_event(context.lifecycle_event, context.timestamp_ms)
        if allocation_payload is not None:
            self.record(TraceEventKind.ALLOCATION, allocation_payload, context.timestamp_ms)
        return True

    def record_frame(self, context: TraceFrameContext) -> bool:
        completion = context.completion
        frame = completion.frame
        if completion.result_revision <= 0 or frame.ended_at_s <= frame.nominal_start_s:
            return False
        return self.record(
            TraceEventKind.ACTUATION_FRAME,
            FramedPulseFramePayload(
                result_revision=completion.result_revision,
                pulse_slot_seconds=context.pulse_slot_seconds,
                frame_seconds=context.frame_seconds,
                frame_start_ms=int(frame.nominal_start_s * 1_000),
                frame_end_ms=int(frame.ended_at_s * 1_000),
                requested_combustion_load=completion.requested_combustion_load,
                requested_auger_duty=frame.latched_request,
                credit_before_seconds=frame.credit_before_s,
                credit_after_seconds=frame.credit_after_s,
                scheduled_on_seconds=frame.scheduled_on_s,
                delivered_on_seconds=frame.delivered_on_s,
                transition_count=frame.observed_transition_count,
                actual_start_active=frame.actual_start_on,
                actual_end_active=frame.actual_end_on,
                requested_fan_duty=completion.requested_fan_duty,
                applied_fan_duty=completion.applied_fan_duty,
                skipped=frame.skipped,
                stale_command=completion.stale_command,
                inhibit_reason=completion.inhibit,
                reset_reason=frame.reset_reason.value if frame.reset_reason is not None else None,
            ),
            int(frame.ended_at_s * 1_000),
        )

    def prepare_applied_output(self, applied: AppliedOutput, context: TraceOutputContext) -> AppliedOutput:
        revision = (
            max(0, context.producing_revision)
            if context.producing_revision is not None
            else max(0, context.pulse_frame_result_revision)
        )
        if context.measured_combustion_load is not None:
            self._applied_state = replace(
                self._applied_state,
                result_revision=revision,
                requested_auger_duty=applied.requested if applied.requested is not None else applied.ratio,
                realized_auger_duty=applied.ratio,
                output_source=applied.source,
                fan_duty=context.fan_duty,
                combustion_load=context.measured_combustion_load,
            )
        coalesce_seed = (
            context.pulse_frame_result_revision == 0
            and self._applied_state.output_source is OutputSource.SEED
            and applied.source is OutputSource.CONTROLLER
        )
        if not coalesce_seed:
            self.record_applied_interval(
                TraceAppliedIntervalContext(
                    timestamp_ms=context.timestamp_ms,
                    sample_complete=context.sample_complete,
                    realized_combustion_load=None,
                    controls_fan=context.controls_fan,
                )
            )
        prepared = replace(
            applied,
            producing_result_revision=revision,
            producing_calibration_revision=context.producing_calibration_revision,
            producing_calibration_action=context.producing_calibration_action,
            producing_calibration_generation=context.producing_calibration_generation,
            sample_complete=context.sample_complete,
            feedback_disposition=context.feedback_disposition,
        )
        if coalesce_seed:
            return prepared
        self._applied_state = TraceAppliedState(
            interval_start_ms=context.timestamp_ms,
            result_revision=revision,
            requested_auger_duty=prepared.requested if prepared.requested is not None else prepared.ratio,
            realized_auger_duty=prepared.ratio,
            output_source=prepared.source,
            fan_duty=context.fan_duty,
            combustion_load=context.measured_combustion_load,
        )
        return prepared

    def promote_seed_interval(self, result_revision: int, source: OutputSource) -> bool:
        if (
            isinstance(result_revision, bool)
            or result_revision <= 0
            or self._applied_state.output_source is not OutputSource.SEED
        ):
            return False
        self._applied_state = replace(
            self._applied_state,
            result_revision=result_revision,
            output_source=source,
        )
        return True

    def record_applied_interval(self, context: TraceAppliedIntervalContext) -> bool:
        state = self._applied_state
        sample_complete = context.sample_complete or (
            state.result_revision == 0 and state.output_source is OutputSource.SEED
        )
        start_ms = state.interval_start_ms
        if (
            start_ms is None
            or start_ms >= context.timestamp_ms
            or state.output_source is None
            or (state.result_revision == 0 and start_ms != 0)
        ):
            return False
        recorded = self.record(
            TraceEventKind.APPLIED_OUTPUT,
            AppliedOutputPayload(
                result_revision=state.result_revision,
                interval_start_ms=start_ms,
                interval_end_ms=context.timestamp_ms,
                realized_auger_duty=state.realized_auger_duty,
                realized_combustion_load=(
                    None
                    if not sample_complete
                    else (
                        state.combustion_load
                        if context.realized_combustion_load is None
                        else context.realized_combustion_load
                    )
                ),
                actual_fan_duty=state.fan_duty if context.controls_fan else None,
                sample_complete=sample_complete,
                output_source=state.output_source,
            ),
            context.timestamp_ms,
        )
        self._applied_state = replace(state, interval_start_ms=context.timestamp_ms)
        return recorded

    def record_terminal_framed_output(self, completion: FramedPulseCompletion, *, controls_fan: bool) -> bool:
        applied = completion.applied
        realized_load = completion.realized_combustion_load
        if applied is None or realized_load is None:
            return False
        frame = completion.frame
        state = self._applied_state
        start_ms = state.interval_start_ms
        trace_start_ms = start_ms if start_ms is not None else int(frame.nominal_start_s * 1_000)
        trace_end_ms = int(frame.ended_at_s * 1_000)
        source = state.output_source or completion.source
        sample_complete = applied.feedback_disposition is FrameFeedbackDisposition.COMPLETE or completion.inhibit in (
            InhibitReason.SAFETY,
            InhibitReason.STALE_COMMAND,
        )
        recorded = False
        if trace_start_ms < trace_end_ms:
            recorded = self.record(
                TraceEventKind.APPLIED_OUTPUT,
                AppliedOutputPayload(
                    result_revision=state.result_revision,
                    interval_start_ms=trace_start_ms,
                    interval_end_ms=trace_end_ms,
                    realized_auger_duty=applied.ratio,
                    realized_combustion_load=realized_load if sample_complete else None,
                    actual_fan_duty=completion.applied_fan_duty if controls_fan else None,
                    sample_complete=sample_complete,
                    output_source=source,
                ),
                trace_end_ms,
            )
        self._applied_state = TraceAppliedState(
            interval_start_ms=trace_end_ms,
            result_revision=completion.result_revision,
            requested_auger_duty=frame.latched_request,
            realized_auger_duty=applied.ratio,
            output_source=completion.source,
            fan_duty=completion.applied_fan_duty,
            combustion_load=realized_load,
        )
        return recorded

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush_pending()
        except Exception as error:
            self._warn_once(f"Control trace pending flush failed: {error}")
        finally:
            self._closed = True
        recorder = self._recorder
        if recorder is None:
            return
        try:
            recorder.close()
        except Exception as error:
            self._warn_once(f"Control trace close failed: {error}")

    def _warn_once(self, message: str) -> None:
        if self._warning_active:
            return
        try:
            self._warning(message)
        except Exception:
            pass
        self._warning_active = True

    def _recover_warning(self) -> None:
        self._warning_active = False

    @staticmethod
    def _snapshot_revision(snapshot: Mapping[str, JsonValue] | None) -> int | None:
        if snapshot is None:
            return None
        revision = snapshot.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return None
        return revision

    @classmethod
    def _validated_authority(
        cls,
        snapshot: Mapping[str, JsonValue],
        provenance: str,
    ) -> TraceModelAuthority | None:
        if cls._snapshot_revision(snapshot) is None or not isinstance(provenance, str) or not provenance.strip():
            return None
        return TraceModelAuthority(MappingProxyType(dict(snapshot)), provenance)

    @classmethod
    def _authority_revision(cls, authority: TraceModelAuthority | None) -> int | None:
        return None if authority is None else cls._snapshot_revision(authority.snapshot)

    @classmethod
    def _flatten_settings(
        cls,
        value: Mapping[str, JsonValue],
        prefix: str = "",
    ) -> tuple[TraceSetting, ...]:
        entries: list[TraceSetting] = []
        for key in sorted(value):
            child = value[key]
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(child, Mapping):
                entries.extend(cls._flatten_settings(child, name))
            elif isinstance(child, str | int | float | bool):
                entries.append(TraceSetting(key=name, value=child))
        return tuple(entries)
