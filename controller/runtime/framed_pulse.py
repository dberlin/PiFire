"""Framed-pulse scheduling, frame accounting, and immutable result construction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from common.control_trace import (
    ActuationMode,
    AllocationClampReason,
    AmbientSource,
    AmbientUncertainty,
    InhibitReason,
)
from controller.applied_output import AppliedOutput, FrameFeedbackDisposition, OutputSource
from controller.model_learning.contracts import FrameObservation
from controller.mpc_allocator import AllocationResult, normalized_load_from_auger_duty
from controller.runtime.logic.pulse import (
    PulseDecision,
    PulseFrameResult,
    PulseReason,
    PulseResetReason,
    PulseScheduler,
)
from grillplat.actuator_capabilities import AugerTiming


class PulseControllerState(Protocol):
    """Concrete controller-state fields owned by framed-pulse actuation."""

    output: float
    fan_duty: float | None
    controls_fan: bool
    pulse_result_revision: int
    pulse_frame_result_revision: int
    pulse_requested_duty: float
    pulse_combustion_load: float | None
    pulse_baseline_combustion_load: float
    pulse_requested_fan_duty: float | None
    pulse_maximum_duty: float
    pulse_stale_command: bool
    pulse_frame_combustion_load: float | None
    pulse_frame_baseline_combustion_load: float
    pulse_frame_requested_auger_duty: float
    pulse_frame_requested_fan_duty: float | None
    pulse_frame_applied_fan_duty: float | None
    pulse_frame_maximum_duty: float
    pulse_frame_stale_command: bool
    pulse_feedback_start_s: float | None
    pulse_feedback_delivered_on_s: float
    pulse_metrics_delivered_on_s: float
    pulse_allocator_revision: int
    pulse_allocation_clamp_reasons: tuple[AllocationClampReason, ...]
    pulse_frame_allocator_revision: int
    pulse_frame_allocation_clamp_reasons: tuple[AllocationClampReason, ...]
    pulse_allocation_evidence_checked: bool
    pulse_allocation_result_revision: int | None
    pulse_frame_allocation_evidence_checked: bool
    pulse_frame_allocation_result_revision: int | None
    calibration_command_revision: int
    pulse_calibration_command_revision: int
    pulse_calibration_command_action: str
    pulse_calibration_command_generation: int
    pulse_calibration_cancellation_reason: str | None
    pulse_baseline_allocation: AllocationResult | None
    pulse_calibration_status: str
    pulse_cancellation_command_revision: int
    pulse_cancellation_command_action: str
    pulse_combined_allocation: AllocationResult | None
    pulse_calibration_probe_load: float
    pulse_calibration_stage: str | None
    pulse_calibration_completed_stages: tuple[str, ...]
    pulse_frame_calibration_command_revision: int
    pulse_frame_calibration_command_action: str
    pulse_frame_calibration_command_generation: int
    pulse_frame_calibration_cancellation_reason: str | None
    pulse_frame_output_source: OutputSource
    pulse_frame_baseline_allocation: AllocationResult | None
    pulse_frame_calibration_status: str
    pulse_frame_cancellation_command_revision: int
    pulse_frame_cancellation_command_action: str
    pulse_frame_combined_allocation: AllocationResult | None
    pulse_frame_calibration_probe_load: float
    pulse_frame_calibration_stage: str | None
    pulse_frame_calibration_completed_stages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FramedPulseSample:
    """Thermal and model identity sampled when a frame is observed."""

    temperature: float | None
    setpoint: float
    ambient_c: float
    units: str
    role_generation: int


@dataclass(frozen=True, slots=True)
class FramedPulseFeedback:
    """One immutable applied output plus its normalized realized load."""

    applied: AppliedOutput
    realized_combustion_load: float
    measured_source: bool
    dispatch: bool = True


@dataclass(frozen=True, slots=True)
class FramedPulseCompletion:
    """Everything Hold must dispatch after commanding a completed frame edge."""

    frame: PulseFrameResult
    inhibit: InhibitReason
    result_revision: int
    source: OutputSource
    requested_combustion_load: float
    requested_fan_duty: float | None
    stale_command: bool
    applied_fan_duty: float | None
    frame_key: tuple[int, int] | None
    observation: FrameObservation | None
    applied: AppliedOutput | None
    realized_combustion_load: float | None
    missing_observation_reason: str | None
    observation_sequence: int | None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class FramedPulseResult:
    """A scheduler decision and deferred side effects for Hold orchestration."""

    decision: PulseDecision
    completions: tuple[FramedPulseCompletion, ...]
    feedback: FramedPulseFeedback | None
    delivered_delta_s: float


@dataclass(frozen=True, slots=True)
class _LatchedFrame:
    result_revision: int
    combustion_load: float | None
    baseline_combustion_load: float
    requested_auger_duty: float
    requested_fan_duty: float | None
    applied_fan_duty: float | None
    maximum_duty: float
    stale_command: bool
    allocator_revision: int
    allocation_clamp_reasons: tuple[AllocationClampReason, ...]
    allocation_evidence_checked: bool
    allocation_result_revision: int | None
    calibration_command_revision: int
    calibration_command_action: str
    calibration_command_generation: int
    calibration_cancellation_reason: str | None
    output_source: OutputSource
    baseline_allocation: AllocationResult | None
    calibration_status: str
    cancellation_command_revision: int
    cancellation_command_action: str
    combined_allocation: AllocationResult | None
    calibration_probe_load: float
    calibration_stage: str | None
    calibration_completed_stages: tuple[str, ...]
    role_generation: int


class FramedPulseRuntime:
    """Own pulse scheduling and typed frame-local construction without I/O."""

    def __init__(self) -> None:
        self._scheduler: PulseScheduler | None = None
        self._controller: PulseControllerState | None = None
        self._frame: _LatchedFrame | None = None
        self._last_observation_key: tuple[int, int] | None = None
        self._observation_sequence = 0

    @property
    def scheduler(self) -> PulseScheduler | None:
        return self._scheduler

    @property
    def frame_seconds(self) -> float:
        scheduler = self._scheduler
        return 0.0 if scheduler is None else float(scheduler.timing.frame_s)

    @property
    def actuation_mode(self) -> ActuationMode:
        if self._scheduler is None:
            raise RuntimeError("framed pulse runtime is not configured")
        return ActuationMode.FRAMED_PULSE

    @property
    def observation_sequence(self) -> int:
        return self._observation_sequence

    def configure(
        self,
        actuation_mode: ActuationMode,
        *,
        controller: PulseControllerState,
        timing: AugerTiming,
        now: float,
        calibration_command_revision: int = 0,
    ) -> None:
        if str(actuation_mode) != ActuationMode.FRAMED_PULSE.value:
            raise ValueError("FramedPulseRuntime requires framed pulse actuation")
        self._controller = controller
        self._scheduler = PulseScheduler(timing)
        self._last_observation_key = None
        self._observation_sequence = 0
        controller.pulse_result_revision = -1
        controller.pulse_frame_result_revision = 0
        controller.pulse_requested_duty = 0.0
        controller.pulse_combustion_load = None
        controller.pulse_baseline_combustion_load = 0.0
        controller.pulse_requested_fan_duty = None
        controller.pulse_maximum_duty = 1.0
        controller.pulse_stale_command = False
        controller.pulse_frame_combustion_load = None
        controller.pulse_frame_baseline_combustion_load = 0.0
        controller.pulse_frame_requested_auger_duty = 0.0
        controller.pulse_frame_requested_fan_duty = None
        controller.pulse_frame_applied_fan_duty = None
        controller.pulse_frame_maximum_duty = 1.0
        controller.pulse_frame_stale_command = False
        controller.pulse_allocator_revision = 0
        controller.pulse_allocation_clamp_reasons = ()
        controller.pulse_frame_allocator_revision = 0
        controller.pulse_frame_allocation_clamp_reasons = ()
        controller.pulse_allocation_evidence_checked = False
        controller.pulse_allocation_result_revision = None
        controller.pulse_frame_allocation_evidence_checked = False
        controller.pulse_frame_allocation_result_revision = None
        controller.pulse_feedback_start_s = now
        controller.pulse_feedback_delivered_on_s = 0.0
        controller.pulse_metrics_delivered_on_s = 0.0
        controller.calibration_command_revision = calibration_command_revision
        controller.pulse_calibration_command_revision = 0
        controller.pulse_calibration_command_action = "none"
        controller.pulse_calibration_command_generation = 0
        controller.pulse_calibration_cancellation_reason = None
        controller.pulse_baseline_allocation = None
        controller.pulse_calibration_status = "inactive"
        controller.pulse_cancellation_command_revision = 0
        controller.pulse_cancellation_command_action = "none"
        controller.pulse_combined_allocation = None
        controller.pulse_calibration_probe_load = 0.0
        controller.pulse_calibration_stage = None
        controller.pulse_calibration_completed_stages = ()
        self._frame = self._latch_frame(0)

    def latch(self, role_generation: int) -> None:
        """Latch current controller request and calibration identity for one frame."""
        self._configured()
        self._frame = self._latch_frame(role_generation)

    def advance(
        self,
        now: float,
        actual_auger_on: bool,
        *,
        sample: FramedPulseSample,
        prior_output_source: OutputSource | None = OutputSource.CONTROLLER,
    ) -> FramedPulseResult:
        scheduler, controller, previous = self._configured()
        previous_revision = previous.result_revision
        decision = scheduler.advance(controller.pulse_requested_duty, now, actual_auger_on)
        delivered_delta_s = self._record_delivery(decision.delivered_on_s)
        completions = tuple(
            self._complete_frame(
                frame,
                latched=previous,
                sample=sample,
                sample_at_s=now,
                inhibit=InhibitReason.NONE,
                terminal_feedback=True,
                feedback_source=previous.output_source,
            )
            for frame in decision.completed_frames
        )
        if decision.reason in (PulseReason.FRAME_STARTED, PulseReason.FRAME_SKIPPED, PulseReason.RESET):
            self._frame = self._latch_frame(sample.role_generation)
        current = self._frame
        assert current is not None

        feedback: FramedPulseFeedback | None = None
        transition_at_s: float | None = None
        delivered_at_transition_s = decision.delivered_on_s
        if decision.completed_frames:
            transition_at_s = decision.completed_frames[-1].ended_at_s
            delivered_at_transition_s -= decision.frame_delivered_on_s
        elif previous_revision == 0 and current.result_revision > 0:
            transition_at_s = now
        if transition_at_s is not None:
            completed = previous if decision.completed_frames else current
            feedback = self.report_feedback(
                transition_at_s,
                delivered_at_transition_s,
                source=completed.output_source,
                completed=completed,
                disposition=FrameFeedbackDisposition.PROGRESS,
                prior_output_source=prior_output_source,
                dispatch=not bool(decision.completed_frames),
            )
        return FramedPulseResult(decision, completions, feedback, delivered_delta_s)

    def reset(
        self,
        reason: PulseResetReason,
        now: float,
        inhibit: InhibitReason,
        *,
        actual_auger_on: bool,
        sample: FramedPulseSample,
        terminal_feedback: bool,
        report_feedback: bool = False,
        cancellation_reason: str | None = None,
        cancellation_command_revision: int = 0,
        cancellation_command_action: str = "safety-cancel",
        feedback_source: OutputSource | None = None,
        prior_output_source: OutputSource | None = OutputSource.CONTROLLER,
    ) -> FramedPulseResult:
        scheduler, controller, previous = self._configured()
        decision = scheduler.advance(controller.pulse_requested_duty, now, actual_auger_on)
        delivered_delta_s = self._record_delivery(decision.delivered_on_s)
        completions = [
            self._complete_frame(
                frame,
                latched=previous,
                sample=sample,
                sample_at_s=now,
                inhibit=inhibit,
                terminal_feedback=True,
                feedback_source=previous.output_source,
            )
            for frame in decision.completed_frames
        ]
        if decision.reason in (PulseReason.FRAME_STARTED, PulseReason.FRAME_SKIPPED, PulseReason.RESET):
            self._frame = self._latch_frame(sample.role_generation)
        self._stamp_calibration_cancellation(
            cancellation_reason or ("reset" if reason is PulseResetReason.MODE_CHANGE else reason.value),
            command_revision=cancellation_command_revision,
            command_action=cancellation_command_action,
        )
        current = self._frame
        assert current is not None
        interrupted = scheduler.reset(reason)
        if interrupted is not None:
            latched = current
            completions.append(
                self._complete_frame(
                    interrupted,
                    latched=latched,
                    sample=sample,
                    sample_at_s=now,
                    inhibit=inhibit,
                    terminal_feedback=terminal_feedback,
                    feedback_source=feedback_source or latched.output_source,
                )
            )
        feedback = None
        if report_feedback and not decision.completed_frames and cancellation_reason is None and not terminal_feedback:
            feedback = self.report_feedback(
                now,
                decision.delivered_on_s,
                source=feedback_source or current.output_source,
                disposition=FrameFeedbackDisposition.PROGRESS,
                prior_output_source=prior_output_source,
            )
        controller.pulse_metrics_delivered_on_s = decision.delivered_on_s
        controller.pulse_feedback_start_s = now
        controller.pulse_feedback_delivered_on_s = decision.delivered_on_s
        return FramedPulseResult(decision, tuple(completions), feedback, delivered_delta_s)

    def complete_frame(
        self,
        frame: PulseFrameResult,
        *,
        sample: FramedPulseSample,
        inhibit: InhibitReason,
        sample_at_s: float | None = None,
        terminal_feedback: bool = False,
        feedback_source: OutputSource | None = None,
    ) -> FramedPulseCompletion:
        latched = self._configured()[2]
        return self._complete_frame(
            frame,
            latched=latched,
            sample=sample,
            sample_at_s=frame.ended_at_s if sample_at_s is None else sample_at_s,
            inhibit=inhibit,
            terminal_feedback=terminal_feedback,
            feedback_source=feedback_source or latched.output_source,
        )

    def report_feedback(
        self,
        now: float,
        delivered_on_s: float,
        *,
        source: OutputSource,
        completed: _LatchedFrame | None = None,
        disposition: FrameFeedbackDisposition = FrameFeedbackDisposition.PROGRESS,
        prior_output_source: OutputSource | None = OutputSource.CONTROLLER,
        dispatch: bool = True,
    ) -> FramedPulseFeedback | None:
        _, controller, latched = self._configured()
        frame = latched if completed is None else completed
        start = controller.pulse_feedback_start_s
        if start is None:
            controller.pulse_feedback_start_s = now
            controller.pulse_feedback_delivered_on_s = delivered_on_s
            return None
        elapsed = now - start
        if elapsed <= 0.0:
            return None
        realized_duty = max(
            0.0,
            min(1.0, (delivered_on_s - controller.pulse_feedback_delivered_on_s) / elapsed),
        )
        realized_load = normalized_load_from_auger_duty(realized_duty, u_max=frame.maximum_duty)
        measured_source = prior_output_source is OutputSource.CONTROLLER
        applied = AppliedOutput(
            ratio=realized_duty,
            source=source,
            timestamp=now,
            requested=frame.requested_auger_duty,
            producing_result_revision=max(0, frame.result_revision),
            producing_calibration_revision=frame.calibration_command_revision,
            producing_calibration_action=frame.calibration_command_action,
            producing_calibration_generation=frame.calibration_command_generation,
            feedback_disposition=disposition,
            sample_complete=prior_output_source in (OutputSource.SEED, OutputSource.CONTROLLER),
        )
        controller.pulse_feedback_start_s = now
        controller.pulse_feedback_delivered_on_s = delivered_on_s
        return FramedPulseFeedback(applied, realized_load, measured_source, dispatch)

    def _configured(self) -> tuple[PulseScheduler, PulseControllerState, _LatchedFrame]:
        if self._scheduler is None or self._controller is None or self._frame is None:
            raise RuntimeError("framed pulse runtime requires a pulse scheduler")
        return self._scheduler, self._controller, self._frame

    def _record_delivery(self, delivered_on_s: float) -> float:
        controller = self._configured()[1]
        delta = delivered_on_s - controller.pulse_metrics_delivered_on_s
        if delta <= 0.0:
            return 0.0
        controller.pulse_metrics_delivered_on_s = delivered_on_s
        return delta

    def _latch_frame(self, role_generation: int) -> _LatchedFrame:
        if self._controller is None:
            raise RuntimeError("framed pulse runtime is not configured")
        controller = self._controller
        revision = max(0, controller.pulse_result_revision)
        source = (
            OutputSource.CONTROLLER
            if revision > 0 or controller.pulse_combustion_load is not None
            else OutputSource.SEED
        )
        frame = _LatchedFrame(
            result_revision=revision,
            combustion_load=controller.pulse_combustion_load,
            baseline_combustion_load=controller.pulse_baseline_combustion_load,
            requested_auger_duty=controller.pulse_requested_duty,
            requested_fan_duty=controller.pulse_requested_fan_duty,
            applied_fan_duty=controller.fan_duty if controller.controls_fan else None,
            maximum_duty=controller.pulse_maximum_duty,
            stale_command=controller.pulse_stale_command,
            allocator_revision=controller.pulse_allocator_revision,
            allocation_clamp_reasons=controller.pulse_allocation_clamp_reasons,
            allocation_evidence_checked=controller.pulse_allocation_evidence_checked,
            allocation_result_revision=controller.pulse_allocation_result_revision,
            calibration_command_revision=controller.pulse_calibration_command_revision,
            calibration_command_action=controller.pulse_calibration_command_action,
            calibration_command_generation=controller.pulse_calibration_command_generation,
            calibration_cancellation_reason=controller.pulse_calibration_cancellation_reason,
            output_source=source,
            baseline_allocation=controller.pulse_baseline_allocation,
            calibration_status=controller.pulse_calibration_status,
            cancellation_command_revision=controller.pulse_cancellation_command_revision,
            cancellation_command_action=controller.pulse_cancellation_command_action,
            combined_allocation=controller.pulse_combined_allocation,
            calibration_probe_load=controller.pulse_calibration_probe_load,
            calibration_stage=controller.pulse_calibration_stage,
            calibration_completed_stages=controller.pulse_calibration_completed_stages,
            role_generation=role_generation,
        )
        self._mirror_frame(frame)
        return frame

    def _mirror_frame(self, frame: _LatchedFrame) -> None:
        if self._controller is None:
            return
        controller = self._controller
        controller.pulse_frame_result_revision = frame.result_revision
        controller.pulse_frame_combustion_load = frame.combustion_load
        controller.pulse_frame_baseline_combustion_load = frame.baseline_combustion_load
        controller.pulse_frame_requested_auger_duty = frame.requested_auger_duty
        controller.pulse_frame_requested_fan_duty = frame.requested_fan_duty
        controller.pulse_frame_applied_fan_duty = frame.applied_fan_duty
        controller.pulse_frame_maximum_duty = frame.maximum_duty
        controller.pulse_frame_stale_command = frame.stale_command
        controller.pulse_frame_allocator_revision = frame.allocator_revision
        controller.pulse_frame_allocation_clamp_reasons = frame.allocation_clamp_reasons
        controller.pulse_frame_allocation_evidence_checked = frame.allocation_evidence_checked
        controller.pulse_frame_allocation_result_revision = frame.allocation_result_revision
        controller.pulse_frame_calibration_command_revision = frame.calibration_command_revision
        controller.pulse_frame_calibration_command_action = frame.calibration_command_action
        controller.pulse_frame_calibration_command_generation = frame.calibration_command_generation
        controller.pulse_frame_calibration_cancellation_reason = frame.calibration_cancellation_reason
        controller.pulse_frame_output_source = frame.output_source
        controller.pulse_frame_baseline_allocation = frame.baseline_allocation
        controller.pulse_frame_calibration_status = frame.calibration_status
        controller.pulse_frame_cancellation_command_revision = frame.cancellation_command_revision
        controller.pulse_frame_cancellation_command_action = frame.cancellation_command_action
        controller.pulse_frame_combined_allocation = frame.combined_allocation
        controller.pulse_frame_calibration_probe_load = frame.calibration_probe_load
        controller.pulse_frame_calibration_stage = frame.calibration_stage
        controller.pulse_frame_calibration_completed_stages = frame.calibration_completed_stages

    def _stamp_calibration_cancellation(self, reason: str, *, command_revision: int, command_action: str) -> None:
        if self._frame is None:
            return
        if self._frame.calibration_status != "active" or self._frame.calibration_probe_load == 0.0:
            return
        self._frame = replace(
            self._frame,
            calibration_cancellation_reason=reason,
            calibration_status="cancelled",
            cancellation_command_revision=command_revision,
            cancellation_command_action=command_action,
        )
        self._mirror_frame(self._frame)

    def _complete_frame(
        self,
        frame: PulseFrameResult,
        *,
        latched: _LatchedFrame,
        sample: FramedPulseSample,
        sample_at_s: float,
        inhibit: InhibitReason,
        terminal_feedback: bool,
        feedback_source: OutputSource,
    ) -> FramedPulseCompletion:
        duration_s = frame.ended_at_s - frame.nominal_start_s
        frame_key = None if duration_s <= 0.0 else self._frame_key(frame)
        duplicate = frame_key is not None and frame_key == self._last_observation_key
        observation = None
        missing_reason = None
        sequence = None
        if not duplicate and duration_s > 0.0:
            self._observation_sequence += 1
            sequence = self._observation_sequence
            self._last_observation_key = frame_key
            if sample.temperature is None:
                missing_reason = "missing-temperature"
            elif latched.result_revision <= 0:
                missing_reason = "missing-result-revision"
            else:
                observation = self._observation(
                    frame,
                    latched=latched,
                    sample=sample,
                    sample_at_s=sample_at_s,
                    inhibit=inhibit,
                    sequence=sequence,
                )

        applied = None
        realized_load = None
        if terminal_feedback and duration_s > 0.0:
            ratio = frame.delivered_on_s / duration_s
            realized_load = normalized_load_from_auger_duty(ratio, u_max=latched.maximum_duty)
            disposition = (
                FrameFeedbackDisposition.DISCARDED
                if frame.skipped or frame.reset_reason is not None
                else FrameFeedbackDisposition.COMPLETE
            )
            applied = AppliedOutput(
                ratio=ratio,
                source=feedback_source,
                timestamp=frame.ended_at_s,
                requested=frame.latched_request,
                producing_result_revision=latched.result_revision,
                producing_calibration_revision=latched.calibration_command_revision,
                producing_calibration_action=latched.calibration_command_action,
                producing_calibration_generation=latched.calibration_command_generation,
                feedback_disposition=disposition,
                sample_complete=(
                    disposition is FrameFeedbackDisposition.COMPLETE and feedback_source is OutputSource.CONTROLLER
                ),
            )
        return FramedPulseCompletion(
            frame=frame,
            inhibit=inhibit,
            result_revision=latched.result_revision,
            source=feedback_source,
            requested_combustion_load=latched.combustion_load or 0.0,
            requested_fan_duty=latched.requested_fan_duty,
            stale_command=latched.stale_command,
            applied_fan_duty=latched.applied_fan_duty,
            frame_key=frame_key,
            observation=observation,
            applied=applied,
            realized_combustion_load=realized_load,
            missing_observation_reason=missing_reason,
            observation_sequence=sequence,
            duplicate=duplicate,
        )

    def _observation(
        self,
        frame: PulseFrameResult,
        *,
        latched: _LatchedFrame,
        sample: FramedPulseSample,
        sample_at_s: float,
        inhibit: InhibitReason,
        sequence: int,
    ) -> FrameObservation:
        assert sample.temperature is not None
        duration_s = frame.ended_at_s - frame.nominal_start_s
        source = self._observation_source(frame, inhibit, latched)
        lid_open = inhibit is InhibitReason.LID_OPEN or frame.reset_reason is PulseResetReason.LID
        manual_override = inhibit is InhibitReason.MANUAL_OVERRIDE or frame.reset_reason is PulseResetReason.MANUAL
        safety_inhibited = inhibit is InhibitReason.SAFETY or frame.reset_reason is PulseResetReason.SAFETY
        stale = latched.stale_command or inhibit is InhibitReason.STALE_COMMAND
        reset = frame.reset_reason is not None
        # The paired temperature is the control tick's fresh sample, which lands on
        # the first tick at or after the frame boundary. A sample from before the
        # boundary predates the actuation it is meant to describe, and one a whole
        # frame late belongs to a frame the loop never closed.
        sample_lag_s = sample_at_s - frame.ended_at_s
        continuous = not (
            lid_open
            or manual_override
            or safety_inhibited
            or stale
            or frame.skipped
            or reset
            or source == "unknown"
            or sample_lag_s < 0.0
            or sample_lag_s >= duration_s
        )
        baseline_q = max(0.0, min(1.0, latched.baseline_combustion_load))
        requested_q = max(0.0, min(1.0, baseline_q + latched.calibration_probe_load))
        realized_auger_duty = frame.delivered_on_s / duration_s
        return FrameObservation(
            frame_start_s=frame.nominal_start_s,
            frame_end_s=frame.ended_at_s,
            temp_c=self._to_c(sample.temperature, sample.units),
            setpoint_c=self._to_c(sample.setpoint, sample.units),
            ambient_c=sample.ambient_c,
            requested_q=requested_q,
            realized_q=normalized_load_from_auger_duty(
                realized_auger_duty,
                u_max=latched.maximum_duty,
            ),
            requested_auger_duty=frame.latched_request,
            delivered_on_s=frame.delivered_on_s,
            requested_fan_duty=self._fan_fraction(latched.requested_fan_duty),
            actual_fan_duty=self._fan_fraction(latched.applied_fan_duty),
            result_revision=latched.result_revision,
            output_source=source,
            lid_open=lid_open,
            safety_inhibited=safety_inhibited,
            manual_override=manual_override,
            stale=stale,
            skipped=frame.skipped,
            reset=reset,
            continuous=continuous,
            role_generation=latched.role_generation,
            observation_sequence=sequence,
            probe_valid=True,
            probe_source="chamber",
            ambient_source=AmbientSource.CONFIGURED,
            ambient_uncertainty=AmbientUncertainty.UNMEASURED,
            baseline_q=baseline_q,
            probe_q=latched.calibration_probe_load,
            allocated_q=requested_q,
            scheduled_on_s=frame.scheduled_on_s,
            realized_auger_duty=realized_auger_duty,
            allocator_revision=latched.allocator_revision,
            allocation_clamp_reasons=latched.allocation_clamp_reasons,
            calibration_command_revision=latched.calibration_command_revision,
            calibration_command_action=latched.calibration_command_action,
            calibration_cancellation_reason=latched.calibration_cancellation_reason,
            baseline_allocation=latched.baseline_allocation,
            calibration_status=latched.calibration_status,
            cancellation_command_revision=latched.cancellation_command_revision,
            cancellation_command_action=latched.cancellation_command_action,
            combined_allocation=latched.combined_allocation,
            calibration_stage=latched.calibration_stage,
            completed_calibration_stages=latched.calibration_completed_stages,
            calibration_fit=latched.calibration_stage is not None,
            allocation_join_reason=(
                None
                if not latched.allocation_evidence_checked
                else (
                    "missing-allocation"
                    if latched.allocation_result_revision is None
                    else (
                        None
                        if latched.allocation_result_revision == latched.result_revision
                        else "allocation-revision-mismatch"
                    )
                )
            ),
        )

    @staticmethod
    def _frame_key(frame: PulseFrameResult) -> tuple[int, int]:
        return int(frame.nominal_start_s * 1_000), int(frame.ended_at_s * 1_000)

    @staticmethod
    def _observation_source(
        frame: PulseFrameResult,
        inhibit: InhibitReason,
        latched: _LatchedFrame,
    ) -> str:
        if inhibit is InhibitReason.MANUAL_OVERRIDE or frame.reset_reason is PulseResetReason.MANUAL:
            return OutputSource.MANUAL_OVERRIDE.value
        if inhibit is InhibitReason.LID_OPEN or frame.reset_reason is PulseResetReason.LID:
            return OutputSource.LID_OPEN.value
        if (
            inhibit is InhibitReason.SAFETY
            or frame.reset_reason in (PulseResetReason.SAFETY, PulseResetReason.MODE_CHANGE)
            or latched.combustion_load is None
        ):
            return "unknown"
        return OutputSource.CONTROLLER.value

    @staticmethod
    def _to_c(value: float, units: str) -> float:
        return (float(value) - 32.0) * 5.0 / 9.0 if units.upper() == "F" else float(value)

    @staticmethod
    def _fan_fraction(duty: float | None) -> float | None:
        if duty is None:
            return None
        return max(0.0, min(1.0, float(duty) / 100.0))
