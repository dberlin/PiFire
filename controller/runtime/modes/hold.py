from collections.abc import Mapping
from math import isfinite

import time
from dataclasses import dataclass, replace
from typing import Literal, cast

from common.controller_model_state import ControllerModelStore
from common.persistence.model_evidence import read_model_activation, read_model_evidence
from controller.model_learning.migration import migrate_mpc_learning_authority
from common.modes import Mode
from common.model_evidence import (
    AllocationEvidence,
    CalibrationSummaryEvidence,
    EvidenceKind,
    FallbackEvidence,
    ModelEvidenceRecord,
    RecorderGapEvidence,
    RollbackEvidence,
)
from common.control_trace import (
    ActuationMode,
    AllocationClampReason,
    AllocationPayload,
    CalibrationEventType,
    CalibrationTracePayload,
    ControlTracePayload,
    ControllerType,
    InhibitReason,
    ModelEventPayload,
    ModelEvaluationPayload,
    ModelEventType,
    ModelObservationPayload,
    RecorderGapPayload,
    ResultStaleState,
    SafetyEventType,
    TraceEventKind,
    TraceSetting,
)
from common.persistence.protocols import JsonValue
from controller.applied_output import (
    AppliedOutput,
    FrameFeedbackDisposition,
    OutputSource,
    classify_output_source,
    seed_output,
)
from controller.model_learning.contracts import FrameObservation
from controller.mpc_calibration import CalibrationCommand
from controller.runtime.framed_pulse import (
    FramedPulseCompletion,
    FramedPulseFeedback,
    FramedPulseResult,
    FramedPulseRuntime,
    FramedPulseSample,
    PulseControllerState,
)
from controller.runtime.logic.pulse import PulseResetReason

from controller.runtime.control_trace_session import (
    ControlTraceSession,
    TraceAppliedIntervalContext,
    TraceFrameContext,
    TraceModelAuthority,
    TraceModelContext,
    TraceOutputContext,
    TraceSafetyContext,
    TraceSessionContext,
    TraceUpdateContext,
)
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.model_fitting import (
    TeardownRefitOutcome,
)
from controller.base import MpcTraceDiagnostics
from controller.model_promotion import ReachabilityState
from controller.runtime.logic.fan import controller_fan_authority, start_fan
from controller.runtime.logic.pwm import hold_duty_cycle
from controller.runtime.modes.base import ControlMode
import controller.runtime.runner as _runner_mod


@dataclass(frozen=True, slots=True)
class _HoldOutputStatus:
    auger: bool
    fan: bool
    pwm: int | float

    def __getitem__(self, name: str) -> bool | int | float:
        if name == "auger":
            return self.auger
        if name == "fan":
            return self.fan
        if name == "pwm":
            return self.pwm
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class _HoldTickContext:
    now: float
    ptemp: float
    output_status: _HoldOutputStatus
    trace: ControlTraceSession | None
    active_calibration_reset: bool
    runner_adopted: bool


@dataclass(frozen=True, slots=True)
class _HoldRunnerResult:
    result: _runner_mod.ControllerUpdateResult | None
    controller_interval: float
    cancellation_reason: str | None


@dataclass(frozen=True, slots=True)
class _HoldInhibitionDecision:
    lid_will_open: bool
    permit_framed_pulse: bool
    framed_feedback_due: bool


@dataclass(frozen=True, slots=True)
class _HoldFramedPulse:
    result: FramedPulseResult | None
    lid_will_open: bool
    report_feedback: bool


class HoldMode(ControlMode):
    """Hold mode: fan+power on at setup (shared branch with Startup/Reignite/
    Smoke/Shutdown -- Hold always takes the plain `start_fan(grill, settings)`
    path, never the Startup/Reignite dc_fan pwm_duty_cycle special case);
    auger ON at setup (shared with Startup/Reignite/Smoke/Prime); sets up
    Recipe-mode triggers (shared with Smoke, reproduced UNCONDITIONALLY in
    base.run()'s shared pre-loop section -- not duplicated here). Hold is the
    most intricate mode: it owns a ControllerRunner (PID/MPC) built at setup
    and reconfigured on `control['controller_update']`, its own auger-cycle
    timing driven by the controller's cycle_ratio output (not the plain
    elapsed-time toggle used by other cycling modes), a setup-time fan-ownership
    capability (`self.state.controller.controls_fan`, from the runner's
    `commands_fan()`), lid-open detection, and PWM-duty-from-temp-profile fan
    control paths.
    The pre-loop and in-loop flameout checks are DECLARATIVE guard edges
    (GUARDS["Hold"] in transitions.py, fired by evaluate_phase at base.run's
    pre_loop/pre_act points). setup_safety() survives only to abort to 'Inactive'
    if the runner failed to build (controller module load error); there is no
    check_safety override.
    Per tick, on_tick() visibly sequences configuration/session adoption,
    expired manual-authority release, safety/calibration publication, runner
    submission/result retrieval,
    safety/manual/lid inhibition, framed-pulse scheduling, Hold-owned hardware
    commands, and trace/feedback/reconciliation/flush. The runner result phase
    retains controller cadence and optional fan authority; the framed runtime
    retains frame transitions and accounting; Hold retains all grill commands,
    control persistence, safety/lid/manual decisions, and lifecycle ordering.
    Hold-only fan work uses the same fresh ptemp for the target-temperature
    latch, lid-open detect/clear, PWM-duty-from-temperature-profile (gated by
    `not self.state.controller.controls_fan`), and the shared
    `_smoke_plus_fan_tick` behavior.

    status_fragment() adds the Hold-only primary_setpoint/lid_open_detected/
    lid_open_endtime status fields. No mode-specific teardown (Hold is not in
    the Shutdown/Monitor/Manual/Prime power-off teardown gate, nor the
    Startup/Reignite afterstarttemp-write teardown gate)."""

    _control_trace: ControlTraceSession | None = None
    _runner_configuration_revision: int = 0
    _framed_pulse: FramedPulseRuntime | None = None
    _pending_model_observations: (
        dict[int, tuple[FrameObservation, str | None, int, tuple[tuple[TraceEventKind, object], ...] | None]] | None
    ) = None
    _last_ptemp: float | None = None
    _persistence_worker: ModelPersistenceWorker | None = None
    _learning_evidence_available: bool = True
    _final_refit_done: bool = False
    _activation_state_identity: tuple[object, ...] | None = None
    _activation_lifecycle_evidence_id: str | None = None
    _PENDING_MODEL_OBSERVATION_CAPACITY = 60
    _calibration_command_high_water: int = 0
    _last_target: float | None = None
    _safety_ceiling_fault: str | None = None


    def _observe_reachability_advisory(self, diagnostics: MpcTraceDiagnostics) -> None:
        report = diagnostics.feasibility
        if report is None:
            return
        if report.state is not ReachabilityState.UNREACHABLE_HIGH:
            self._reachability_advisory_key = None
            return
        key = (report.target_temperature, report.model_revision, report.model_provenance)
        if key == self._reachability_advisory_key:
            return
        self._reachability_advisory_key = key
        import control as _control

        _control.eventLogger.warning(
            "MPC learned model predicts the target cannot be reached at maximum safe combustion authority "
            f"(target {report.target_temperature:.1f}, model {report.model_provenance} r{report.model_revision})."
        )


    def _runner_status(self) -> Mapping[str, object]:
        if self._runner is None:
            return {}
        try:
            status = self._runner.controller_state()
        except Exception:
            return {}
        return status if isinstance(status, Mapping) else {}

    def _model_role_generation(self, status: Mapping[str, object]) -> int:
        activation = status.get("activation")
        if isinstance(activation, Mapping):
            generation = activation.get("role_generation")
            if isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0:
                return generation
        adaptation = status.get("adaptation")
        source = adaptation if isinstance(adaptation, Mapping) else status
        generation = source.get("role_generation", 0)
        return generation if isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0 else 0
    def _framed_sample(self, ptemp: float | None) -> FramedPulseSample:
        control = self.control
        if control is None:
            raise RuntimeError("Hold framed pulse runtime requires control state")
        controller_settings = self.settings.get("controller", {})
        config = controller_settings.get("config", {}) if isinstance(controller_settings, Mapping) else {}
        selected = config.get(self._controller_name, {}) if isinstance(config, Mapping) else {}
        globals_settings = self.settings.get("globals", {})
        units = globals_settings.get("units", "F") if isinstance(globals_settings, Mapping) else "F"
        ambient_c = float(selected.get("T_amb", 0.0)) if isinstance(selected, Mapping) else 0.0
        return FramedPulseSample(
            temperature=ptemp,
            setpoint=float(control["primary_setpoint"]),
            ambient_c=ambient_c,
            units=str(units),
            role_generation=self._model_role_generation(self._runner_status()),
        )

    def _record_framed_delivery(self, delivered_delta_s: float) -> None:
        if delivered_delta_s <= 0.0:
            return
        self.state.metrics["augerontime"] = self.state.metrics.get("augerontime", 0.0) + delivered_delta_s
        self.ctx.store.update_metrics(self.state.metrics)

    def _dispatch_framed_feedback(self, feedback: FramedPulseFeedback) -> None:
        applied = feedback.applied
        self._set_output(
            applied,
            applied.timestamp,
            producing_revision=applied.producing_result_revision,
            producing_calibration_revision=applied.producing_calibration_revision,
            producing_calibration_action=applied.producing_calibration_action,
            producing_calibration_generation=applied.producing_calibration_generation,
            sample_complete=applied.sample_complete,
            feedback_disposition=applied.feedback_disposition,
            measured_combustion_load=(
                feedback.realized_combustion_load if feedback.measured_source else None
            ),
            dispatch=feedback.dispatch,
        )

    def _trace_framed_completion(
        self,
        completion: FramedPulseCompletion,
        *,
        record_terminal_trace: bool,
    ) -> None:
        trace = self._control_trace
        runtime = self._framed_pulse
        scheduler = None if runtime is None else runtime.scheduler
        if trace is not None and scheduler is not None:
            trace.record_frame(
                TraceFrameContext(
                    completion=completion,
                    pulse_slot_seconds=float(scheduler.timing.pulse_s),
                    frame_seconds=float(scheduler.timing.frame_s),
                )
            )
            if completion.applied is not None and record_terminal_trace:
                trace.record_terminal_framed_output(
                    completion,
                    controls_fan=self.state.controller.controls_fan,
                )
        if (
            completion.missing_observation_reason is not None
            and completion.result_revision > 0
        ):
            self._trace_missing_frame_observation(completion)

    def _deliver_framed_completion(self, completion: FramedPulseCompletion) -> None:
        if completion.observation is not None:
            assert completion.frame_key is not None
            if completion.applied is None:
                self._deliver_completed_pulse_observation(
                    completion.frame_key,
                    completion.observation,
                )
            else:
                self._deliver_completed_pulse_observation(
                    completion.frame_key,
                    completion.observation,
                    completion.applied,
                )
        elif completion.applied is not None:
            runner = self._runner
            if runner is None:
                raise RuntimeError("Hold framed pulse runtime requires a controller runner")
            runner.set_output(completion.applied)

    def _dispatch_framed_result(
        self,
        result: FramedPulseResult,
        *,
        record_terminal_trace: bool,
        scheduler_reset: tuple[
            PulseResetReason,
            float,
            InhibitReason,
            int,
            tuple[SafetyEventType, str, int | None] | None,
        ]
        | None = None,
    ) -> None:
        self._record_framed_delivery(result.delivered_delta_s)
        for completion in result.completions:
            self._deliver_framed_completion(completion)
        if scheduler_reset is not None:
            reason, now, inhibit, result_revision, safety_trace = scheduler_reset
            trace = self._control_trace
            if trace is not None:
                if safety_trace is not None:
                    event, detail, safety_revision = safety_trace
                    trace.record_safety(
                        TraceSafetyContext(
                            event=event,
                            inhibit_reason=inhibit,
                            result_revision=safety_revision,
                            detail=detail,
                            timestamp_ms=int(now * 1_000),
                        )
                    )
                trace.record_safety(
                    TraceSafetyContext(
                        event=SafetyEventType.SCHEDULER_RESET,
                        inhibit_reason=inhibit,
                        result_revision=result_revision,
                        detail=f"framed pulse scheduler reset: {reason.value}",
                        timestamp_ms=int(now * 1_000),
                    )
                )
        for completion in result.completions:
            self._trace_framed_completion(
                completion,
                record_terminal_trace=record_terminal_trace,
            )
        if result.feedback is not None:
            self._dispatch_framed_feedback(result.feedback)

    def _inhibit_framed_pulse(
        self,
        reason: PulseResetReason,
        now: float,
        inhibit: InhibitReason,
        *,
        ptemp: float | None,
        terminal_feedback: bool,
        report_feedback: bool = False,
        cancellation_reason: str | None = None,
        cancellation_command_revision: int = 0,
        cancellation_command_action: str = "safety-cancel",
        safety_event: SafetyEventType | None = None,
        safety_detail: str = "",
        safety_result_revision: int | None = None,
    ) -> None:
        runtime = self._framed_pulse
        if runtime is None or runtime.scheduler is None:
            return
        source = classify_output_source(
            lid_open=self.state.lid.open_detected or inhibit is InhibitReason.LID_OPEN,
            manual_override_active=(
                self.state.manual_override["auger"] >= now or inhibit is InhibitReason.MANUAL_OVERRIDE
            ),
        )
        result = runtime.reset(
            reason,
            now,
            inhibit,
            actual_auger_on=self.grill.get_output_status()["auger"],
            sample=self._framed_sample(ptemp),
            terminal_feedback=terminal_feedback,
            report_feedback=report_feedback,
            cancellation_reason=cancellation_reason,
            cancellation_command_revision=cancellation_command_revision,
            cancellation_command_action=cancellation_command_action,
            feedback_source=source,
            prior_output_source=(
                None
                if self._control_trace is None
                else self._control_trace.applied_state.output_source
            ),
        )
        self.grill.auger_off()
        self._dispatch_framed_result(
            result,
            record_terminal_trace=(
                cancellation_reason is not None
                or inhibit in (InhibitReason.SAFETY, InhibitReason.STALE_COMMAND)
            ),
            scheduler_reset=(
                reason,
                now,
                inhibit,
                self.state.controller.pulse_frame_result_revision,
                None
                if safety_event is None
                else (safety_event, safety_detail, safety_result_revision),
            ),
        )



    def _record_pending_observation_gap(self, observation: FrameObservation, reason: str) -> None:
        publication_ms = int(observation.frame_end_s * 1_000)
        trace = self._control_trace
        if trace is not None:
            trace.record(
                TraceEventKind.RECORDER_GAP,
                RecorderGapPayload(
                    lost_record_count=1,
                    gap_start_ms=int(observation.frame_start_s * 1_000),
                    gap_end_ms=publication_ms,
                    reason=reason,
                    frame_start_ms=int(observation.frame_start_s * 1_000),
                    frame_end_ms=publication_ms,
                    result_revision=observation.result_revision,
                    observation_sequence=observation.observation_sequence,
                ),
                publication_ms,
            )
        worker = self._persistence_worker
        identity = None if trace is None else trace.identity
        if worker is None or identity is None:
            return
        session_id = identity.session_id
        gap = ModelEvidenceRecord(
            evidence_id=(
                f"{session_id}:recorder-gap:{observation.role_generation}:"
                f"{observation.observation_sequence}:{publication_ms}"
            ),
            kind=EvidenceKind.RECORDER_GAP,
            session_id=session_id,
            cook_id=identity.cook_id,
            timestamp_ms=publication_ms,
            role_generation=observation.role_generation,
            model_digest=None,
            provenance_digest=None,
            payload=RecorderGapEvidence(lost_record_count=1, reason=reason),
        )
        if not worker.submit_evidence_batch((gap,)).accepted:
            self._learning_evidence_available = False

    @staticmethod
    def _allocation_evidence(allocation):
        if allocation is None:
            return None
        return AllocationEvidence(
            normalized_combustion_load=allocation.normalized_combustion_load,
            auger_duty=allocation.auger_duty,
            fan_duty=allocation.fan_duty,
            u_max=allocation.u_max,
            fan_min_pct=allocation.fan_min_pct,
            fan_max_pct=allocation.fan_max_pct,
            fan_enabled=allocation.fan_enabled,
            auger_clamp_reason=allocation.auger_clamp_reason,
            fan_clamp_reason=allocation.fan_clamp_reason,
            allocator_revision=allocation.allocator_revision,
        )

    @staticmethod
    def _trace_allocation_payload(allocation, result_revision: int):
        if allocation is None:
            return None
        return AllocationPayload(
            result_revision=result_revision,
            normalized_combustion_load=allocation.normalized_combustion_load,
            requested_auger_duty=allocation.auger_duty,
            requested_fan_duty=allocation.fan_duty,
            u_max=allocation.u_max,
            fan_min_pct=allocation.fan_min_pct,
            fan_max_pct=allocation.fan_max_pct,
            fan_enabled=allocation.fan_enabled,
            mpc_has_fan_authority=allocation.fan_enabled,
            auger_clamp_reason=allocation.auger_clamp_reason,
            fan_clamp_reason=allocation.fan_clamp_reason,
            allocator_revision=allocation.allocator_revision,
        )

    def _calibration_frame_evidence(
        self, observation: FrameObservation, session_id: str | None, cook_id: str | None
    ) -> ModelEvidenceRecord | None:
        if (
            session_id is None
            or observation.calibration_command_revision == 0
            or observation.baseline_allocation is None
            or observation.combined_allocation is None
            or (
                observation.calibration_status == "active"
                and observation.probe_q == 0.0
                and observation.calibration_stage != "coast"
            )
        ):
            return None
        payload = CalibrationSummaryEvidence(
            accepted=observation.calibration_status in {"accepted", "active"},
            probe_count=1 if observation.calibration_status == "active" and observation.probe_q != 0.0 else 0,
            reason=observation.calibration_cancellation_reason,
            result_revision=observation.result_revision,
            command_revision=observation.calibration_command_revision,
            command_action=observation.calibration_command_action,
            baseline_q=observation.baseline_q,
            probe_q=observation.probe_q,
            combined_q=observation.requested_q,
            baseline_allocation=self._allocation_evidence(observation.baseline_allocation),
            combined_allocation=self._allocation_evidence(observation.combined_allocation),
            scheduled_on_seconds=observation.scheduled_on_s,
            cancellation_command_revision=observation.cancellation_command_revision,
            cancellation_command_action=observation.cancellation_command_action,
            delivered_on_seconds=observation.delivered_on_s,
            status=observation.calibration_status,
            requested_fan_duty=observation.requested_fan_duty,
            actual_fan_duty=observation.actual_fan_duty,
            cancellation_reason=observation.calibration_cancellation_reason,
            stage=observation.calibration_stage,
            completed_stages=observation.completed_calibration_stages,
            continuous=observation.continuous,
        )
        return ModelEvidenceRecord(
            evidence_id=(
                f"{session_id}:calibration-frame:{observation.result_revision}:{int(observation.frame_start_s * 1_000)}"
            ),
            kind=EvidenceKind.CALIBRATION_SUMMARY,
            session_id=session_id,
            cook_id=cook_id,
            timestamp_ms=int(observation.frame_end_s * 1_000),
            role_generation=observation.role_generation,
            model_digest=None,
            provenance_digest=None,
            payload=payload,
        )

    def _retire_pending_model_observation(self, sequence: int, reason: str) -> None:
        pending = self._pending_model_observations.pop(sequence, None)
        if pending is not None and isinstance(pending[0], FrameObservation):
            self._record_pending_observation_gap(pending[0], reason)

    def _bound_pending_model_observations(self) -> None:
        while len(self._pending_model_observations) > self._PENDING_MODEL_OBSERVATION_CAPACITY:
            self._retire_pending_model_observation(
                next(iter(self._pending_model_observations)),
                "pending-observation-overflow",
            )

    def _submit_calibration_frame_evidence(self, observation: FrameObservation) -> None:
        """Record what calibration did with this frame, whatever the learner does.

        The operator's calibration run is reported from this evidence, so it
        cannot depend on the learner accepting the observation. With online
        adaptation off the learner returns no outcome at all and every frame is
        retired as a gap; a run that started, probed and aborted would then
        leave the report reading "inactive" with nothing to show the operator.
        """
        worker = self._persistence_worker
        trace = self._control_trace
        identity = None if trace is None else trace.identity
        if worker is None or identity is None:
            return
        compact = self._calibration_frame_evidence(
            observation,
            identity.session_id,
            identity.cook_id,
        )
        if compact is None:
            return
        if not worker.submit_evidence_batch((compact,)).accepted:
            self._learning_evidence_available = False

    def _deliver_completed_pulse_observation(
        self,
        frame_key: tuple[int, int],
        observation: FrameObservation,
        feedback: AppliedOutput | None = None,
    ) -> None:
        self._submit_calibration_frame_evidence(observation)
        if not observation.probe_valid:
            if self._pending_model_observations is None:
                self._pending_model_observations = {}
            sequence = -1
            while sequence in self._pending_model_observations:
                sequence -= 1
            trace = self._control_trace
            identity = None if trace is None else trace.identity
            self._pending_model_observations[sequence] = (
                observation,
                None if identity is None else identity.session_id,
                -1,
                ((TraceEventKind.MODEL_OBSERVATION, self._rejected_model_observation(observation, "invalid-probe")),),
            )
            self._bound_pending_model_observations()
            return
        worker = self._persistence_worker
        if not self._learning_evidence_available or (worker is not None and worker.evidence_blocked):
            self._learning_evidence_available = False
            self._record_pending_observation_gap(observation, "model-persistence-unavailable")
            return
        if self._runner is None:
            return
        submission = (
            self._runner.complete_frame(feedback, observation)
            if feedback is not None
            else self._runner.observe_frame(observation)
        )
        sequence = getattr(submission, "submission_sequence", None)
        generation = getattr(submission, "configuration_generation", None)
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            return
        if self._pending_model_observations is None:
            self._pending_model_observations = {}
        trace = self._control_trace
        identity = None if trace is None else trace.identity
        self._pending_model_observations[sequence] = (
            observation,
            None if identity is None else identity.session_id,
            generation,
            None,
        )
        evicted_sequence = getattr(submission, "evicted_sequence", None)
        if isinstance(evicted_sequence, int) and not isinstance(evicted_sequence, bool):
            self._retire_pending_model_observation(evicted_sequence, "runner-observation-evicted")
        self._bound_pending_model_observations()

    def _trace_missing_frame_observation(self, completion: FramedPulseCompletion) -> None:
        frame = completion.frame
        reason = completion.missing_observation_reason
        sequence = completion.observation_sequence
        if reason is None or sequence is None:
            return
        trace = self._control_trace
        if trace is not None:
            trace.record(
                TraceEventKind.RECORDER_GAP,
                RecorderGapPayload(
                    lost_record_count=1,
                    gap_start_ms=int(frame.nominal_start_s * 1_000),
                    gap_end_ms=int(frame.ended_at_s * 1_000),
                    reason=reason,
                    frame_start_ms=int(frame.nominal_start_s * 1_000),
                    frame_end_ms=int(frame.ended_at_s * 1_000),
                    result_revision=completion.result_revision,
                    observation_sequence=sequence,
                ),
                int(frame.ended_at_s * 1_000),
            )

    @staticmethod
    def _model_lifecycle_payload(value: object) -> ModelEventPayload | None:
        if not isinstance(value, Mapping):
            return None
        try:
            raw_parameters = value["parameters"]
            if not isinstance(raw_parameters, tuple | list):
                return None
            parameters = tuple(
                parameter
                if isinstance(parameter, TraceSetting)
                else TraceSetting(key=parameter["key"], value=parameter["value"])
                for parameter in raw_parameters
            )
            return ModelEventPayload(
                event=ModelEventType(value["event"]),
                model_revision=value["model_revision"],
                provenance=value["provenance"],
                detail=value["detail"],
                model_kind=value["model_kind"],
                model_schema=value["model_schema"],
                role_generation=value["role_generation"],
                snapshot_digest=value["snapshot_digest"],
                parameters=parameters,
            )
        except KeyError, TypeError, ValueError:
            return None

    @staticmethod
    def _rejected_model_observation(observation: FrameObservation, reason: str) -> ModelObservationPayload:
        output_source = OutputSource(observation.output_source) if observation.output_source != "unknown" else None
        return ModelObservationPayload(
            frame_start_ms=int(observation.frame_start_s * 1_000),
            frame_end_ms=int(observation.frame_end_s * 1_000),
            cancellation_command_revision=observation.cancellation_command_revision,
            cancellation_command_action=observation.cancellation_command_action,
            calibration_command_revision=observation.calibration_command_revision,
            calibration_command_action=observation.calibration_command_action,
            calibration_cancellation_reason=observation.calibration_cancellation_reason,
            calibration_status=observation.calibration_status,
            baseline_allocation=HoldMode._trace_allocation_payload(
                observation.baseline_allocation, observation.result_revision
            ),
            combined_allocation=HoldMode._trace_allocation_payload(
                observation.combined_allocation, observation.result_revision
            ),
            temp_c=observation.temp_c,
            setpoint_c=observation.setpoint_c,
            ambient_c=observation.ambient_c,
            observation_sequence=observation.observation_sequence,
            probe_valid=observation.probe_valid,
            probe_source=observation.probe_source,
            ambient_source=observation.ambient_source,
            ambient_uncertainty=observation.ambient_uncertainty,
            baseline_combustion_load=observation.baseline_q,
            calibration_probe_load=observation.probe_q,
            requested_combustion_load=observation.requested_q,
            allocated_combustion_load=observation.allocated_q,
            realized_combustion_load=observation.realized_q,
            requested_auger_duty=observation.requested_auger_duty,
            scheduled_on_seconds=observation.scheduled_on_s,
            delivered_on_seconds=observation.delivered_on_s,
            realized_auger_duty=observation.realized_auger_duty,
            allocator_revision=observation.allocator_revision,
            allocation_clamp_reasons=observation.allocation_clamp_reasons,
            calibration_stage=observation.calibration_stage,
            calibration_fit=observation.calibration_fit,
            result_revision=observation.result_revision,
            eligible=False,
            rejection_reasons=(reason,),
            input_variance=0.0,
            input_levels=0,
            incumbent_innovation_c=None,
            challenger_innovation_c=None,
            effective_updates=0,
            role_generation=observation.role_generation,
            model_digest=None,
            requested_fan_duty=observation.requested_fan_duty,
            actual_fan_duty=observation.actual_fan_duty,
            output_source=output_source,
            lid_open=observation.lid_open,
            safety_inhibited=observation.safety_inhibited,
            manual_override=observation.manual_override,
            stale=observation.stale,
            skipped=observation.skipped,
            reset=observation.reset,
            continuous=observation.continuous,
        )

    def _queue_rejected_model_observation(
        self,
        sequence: int,
        reason: str,
        evaluation_payload: ModelEvaluationPayload | None = None,
    ) -> None:
        pending = self._pending_model_observations.get(sequence)
        if pending is None or not isinstance(pending[0], FrameObservation):
            self._pending_model_observations.pop(sequence, None)
            return
        try:
            rejected = self._rejected_model_observation(pending[0], reason)
        except ValueError:
            self._retire_pending_model_observation(sequence, reason)
            return
        records: tuple[tuple[TraceEventKind, object], ...] = ((TraceEventKind.MODEL_OBSERVATION, rejected),)
        if evaluation_payload is not None:
            records += ((TraceEventKind.MODEL_EVALUATION, evaluation_payload),)
        self._pending_model_observations[sequence] = (*pending[:3], records)

    def _flush_pending_model_trace(
        self,
        sequence: int,
        pending: tuple[FrameObservation, str | None, int, tuple[tuple[TraceEventKind, object], ...] | None],
        publication_ms: int,
    ) -> bool:
        remaining = pending[3]
        if not isinstance(remaining, tuple):
            return False
        trace = self._control_trace
        if trace is None:
            return False
        while remaining:
            event_kind, payload = remaining[0]
            if not trace.record(event_kind, cast(ControlTracePayload, payload), publication_ms):
                self._pending_model_observations[sequence] = (*pending[:3], remaining)
                return False
            remaining = remaining[1:]
        self._pending_model_observations.pop(sequence, None)
        return True

    def _persist_controller_evidence(self, evidence) -> None:
        compact_batch = evidence if isinstance(evidence, tuple) else ()
        confidence = tuple(
            record
            for record in compact_batch
            if isinstance(record, ModelEvidenceRecord) and record.kind is EvidenceKind.CONFIDENCE_DECISION
        )
        ordinary = tuple(record for record in compact_batch if record not in confidence)
        for record in confidence:
            receipt = self._runner.submit_activation_confidence(record)
            if receipt is None or not receipt.accepted:
                self._learning_evidence_available = False
        if (
            ordinary
            and self._persistence_worker is not None
            and not self._persistence_worker.submit_evidence_batch(ordinary).accepted
        ):
            self._learning_evidence_available = False

    def _reconcile_model_observation_outcomes(self, now: float | None = None) -> None:
        publication_ms = int((self.ctx.clock.now() if now is None else now) * 1_000)
        if not self._pending_model_observations or self._runner is None:
            return
        drain = getattr(self._runner, "drain_observation_outcomes", None)
        if drain is None:
            return
        batch = drain()
        terminal_drops = getattr(batch, "terminal_drops", ())
        if isinstance(terminal_drops, tuple):
            for drop in terminal_drops:
                sequence = getattr(drop, "submission_sequence", None)
                reason = getattr(drop, "reason", None)
                if isinstance(sequence, int) and not isinstance(sequence, bool) and isinstance(reason, str) and reason:
                    self._retire_pending_model_observation(sequence, reason)
        envelopes = getattr(batch, "envelopes", batch)
        for envelope in envelopes:
            sequence = getattr(envelope, "submission_sequence", None)
            generation = getattr(envelope, "configuration_generation", None)
            delivered = getattr(envelope, "observation", None)
            outcome = getattr(envelope, "outcome", None)
            evidence = getattr(envelope, "evidence", ())
            pending = self._pending_model_observations.get(sequence)
            if pending is None:
                continue
            trace = self._control_trace
            identity = None if trace is None else trace.identity
            if generation != pending[2] or pending[1] != (
                None if identity is None else identity.session_id
            ):
                self._queue_rejected_model_observation(sequence, "observation-configuration-mismatch")
                continue
            if not isinstance(delivered, FrameObservation) or not isinstance(outcome, Mapping):
                self._queue_rejected_model_observation(sequence, "observation-outcome-malformed")
                continue
            self._persist_controller_evidence(evidence)
            observation = delivered
            evaluation_payload = outcome.get("evaluation_payload")
            evaluation_payload = evaluation_payload if isinstance(evaluation_payload, ModelEvaluationPayload) else None
            try:
                if observation.allocation_join_reason is not None:
                    self._queue_rejected_model_observation(
                        sequence, observation.allocation_join_reason, evaluation_payload
                    )
                    continue
                if not observation.probe_valid:
                    self._queue_rejected_model_observation(sequence, "invalid-probe", evaluation_payload)
                    continue
                role_generation = outcome["role_generation"]
                if role_generation != observation.role_generation:
                    self._queue_rejected_model_observation(
                        sequence, "observation-role-generation-mismatch", evaluation_payload
                    )
                    continue
                if outcome.get("eligible") is True and (
                    observation.output_source != OutputSource.CONTROLLER.value
                    or observation.lid_open
                    or observation.safety_inhibited
                    or observation.manual_override
                    or observation.stale
                    or observation.skipped
                    or observation.reset
                    or not observation.continuous
                ):
                    self._queue_rejected_model_observation(sequence, "observation-gate-mismatch", evaluation_payload)
                    continue
                output_source = (
                    OutputSource(observation.output_source) if observation.output_source != "unknown" else None
                )
                observation_payload = ModelObservationPayload(
                    frame_start_ms=int(observation.frame_start_s * 1_000),
                    frame_end_ms=int(observation.frame_end_s * 1_000),
                    temp_c=observation.temp_c,
                    setpoint_c=observation.setpoint_c,
                    ambient_c=observation.ambient_c,
                    observation_sequence=observation.observation_sequence,
                    probe_valid=observation.probe_valid,
                    probe_source=observation.probe_source,
                    ambient_source=observation.ambient_source,
                    ambient_uncertainty=observation.ambient_uncertainty,
                    baseline_combustion_load=observation.baseline_q,
                    calibration_probe_load=observation.probe_q,
                    requested_combustion_load=observation.requested_q,
                    allocated_combustion_load=observation.allocated_q,
                    realized_combustion_load=observation.realized_q,
                    requested_auger_duty=observation.requested_auger_duty,
                    scheduled_on_seconds=observation.scheduled_on_s,
                    delivered_on_seconds=observation.delivered_on_s,
                    realized_auger_duty=observation.realized_auger_duty,
                    allocator_revision=observation.allocator_revision,
                    allocation_clamp_reasons=observation.allocation_clamp_reasons,
                    calibration_stage=observation.calibration_stage,
                    calibration_fit=observation.calibration_fit,
                    result_revision=observation.result_revision,
                    eligible=outcome["eligible"],
                    rejection_reasons=tuple(outcome["rejection_reasons"]),
                    input_variance=outcome["input_variance"],
                    input_levels=outcome["input_levels"],
                    incumbent_innovation_c=outcome["incumbent_innovation_c"],
                    challenger_innovation_c=outcome["challenger_innovation_c"],
                    cancellation_command_revision=observation.cancellation_command_revision,
                    cancellation_command_action=observation.cancellation_command_action,
                    calibration_command_revision=observation.calibration_command_revision,
                    calibration_command_action=observation.calibration_command_action,
                    calibration_cancellation_reason=observation.calibration_cancellation_reason,
                    calibration_status=observation.calibration_status,
                    baseline_allocation=self._trace_allocation_payload(
                        observation.baseline_allocation, observation.result_revision
                    ),
                    combined_allocation=self._trace_allocation_payload(
                        observation.combined_allocation, observation.result_revision
                    ),
                    effective_updates=outcome["effective_updates"],
                    role_generation=role_generation,
                    model_digest=outcome["model_digest"],
                    requested_fan_duty=observation.requested_fan_duty,
                    actual_fan_duty=observation.actual_fan_duty,
                    output_source=output_source,
                    lid_open=observation.lid_open,
                    safety_inhibited=observation.safety_inhibited,
                    manual_override=observation.manual_override,
                    stale=observation.stale,
                    skipped=observation.skipped,
                    reset=observation.reset,
                    continuous=observation.continuous,
                )
            except KeyError, TypeError, ValueError:
                self._queue_rejected_model_observation(sequence, "observation-outcome-malformed", evaluation_payload)
                continue
            records: list[tuple[TraceEventKind, object]] = [(TraceEventKind.MODEL_OBSERVATION, observation_payload)]
            if evaluation_payload is not None:
                records.append((TraceEventKind.MODEL_EVALUATION, evaluation_payload))
            lifecycle_payload = self._model_lifecycle_payload(outcome.get("lifecycle"))
            if lifecycle_payload is not None:
                records.append((TraceEventKind.MODEL_EVENT, lifecycle_payload))
            queued = (*pending[:3], tuple(records))
            self._pending_model_observations[sequence] = queued
        for sequence, pending in tuple(self._pending_model_observations.items()):
            if pending[3] is None or not self._flush_pending_model_trace(sequence, pending, publication_ms):
                break



    def _trace_warning(self, message: str) -> None:
        try:
            self.ctx.control_log.warning(message)
        except Exception:
            return

    def _trace_type(self) -> ControllerType | None:
        try:
            return ControllerType(self._controller_name)
        except TypeError, ValueError:
            return None

    def _configure_fan_authority(self) -> None:
        """Grant fan ownership only when the configured controller can drive it."""
        import control as _control

        wants_fan = self._runner.commands_fan() if self._runner is not None else False
        has_authority = controller_fan_authority(self.settings, self.control)
        if wants_fan and not has_authority:
            _control.eventLogger.error(
                f"Controller '{self.settings['controller']['selected']}' is configured to command "
                "the fan, but its duty cannot reach the hardware (PWM Control is off, or this is "
                "not a DC-fan build). Enable Settings > PWM Fan > PWM Control. Fan commands from "
                "the controller will be ignored; the non-controller fan paths stay active."
            )
        self.state.controller.controls_fan = wants_fan and has_authority

    def _trace_session_context(self) -> TraceSessionContext | None:
        trace = self._control_trace
        runner = self._runner
        runtime = self._framed_pulse
        scheduler = None if runtime is None else runtime.scheduler
        controller = self._trace_type()
        cook_id = self.state.metrics.get("id")
        if (
            trace is None
            or runner is None
            or scheduler is None
            or controller is None
            or not isinstance(cook_id, str)
            or not cook_id
        ):
            return None
        controller_settings = self.settings.get("controller", {})
        configs = controller_settings.get("config", {}) if isinstance(controller_settings, Mapping) else {}
        config = configs.get(self._controller_name, {}) if isinstance(configs, Mapping) else {}
        if not isinstance(config, Mapping):
            config = {}
        globals_settings = self.settings.get("globals", {})
        temperature_unit = (
            str(globals_settings.get("units", "F"))
            if isinstance(globals_settings, Mapping)
            else "F"
        )
        ambient_celsius = float(config.get("T_amb", 0.0))
        ambient = ambient_celsius * 9.0 / 5.0 + 32.0 if temperature_unit == "F" else ambient_celsius
        fallback_safe = not runner.runs_async()
        fallback_model: TraceModelAuthority | None = None
        if trace.model_authority is None and fallback_safe:
            snapshot = runner.get_model_snapshot()
            if isinstance(snapshot, Mapping):
                fallback_model = TraceModelAuthority(
                    cast(Mapping[str, JsonValue], snapshot),
                    "persisted",
                )
        versions = self.settings.get("versions", {})
        platform = self.settings.get("platform", {})
        return TraceSessionContext(
            controller=controller,
            controller_config=cast(Mapping[str, JsonValue], config),
            temperature_unit=temperature_unit,
            control_period_seconds=float(runner.control_period() or scheduler.timing.frame_s),
            fallback_model=fallback_model,
            runner_snapshot_fallback_safe=fallback_safe,
            pulse_slot_seconds=float(scheduler.timing.pulse_s),
            pulse_frame_seconds=float(scheduler.timing.frame_s),
            fan_authority=self.state.controller.controls_fan,
            fan_pwm_capable=bool(platform.get("dc_fan", False)) if isinstance(platform, Mapping) else False,
            fan_min_duty=float(config.get("fan_min_pct", 0.0)),
            fan_max_duty=float(config.get("fan_max_pct", 100.0)),
            setpoint=float(self.control["primary_setpoint"]),
            ambient_temperature=ambient,
            software_version=(
                str(versions.get("server", "unknown"))
                if isinstance(versions, Mapping)
                else "unknown"
            ),
            build_version=(
                str(versions.get("build", "unknown"))
                if isinstance(versions, Mapping)
                else "unknown"
            ),
            cook_id=cook_id,
            runner_generation=self._runner_configuration_revision,
        )

    def _checkpoint_model(self, snapshot: dict[str, object]) -> bool:
        worker = self._persistence_worker
        accepted = worker is not None and worker.submit_checkpoint(self._controller_name, snapshot)
        if not accepted:
            self._learning_evidence_available = False
        return bool(accepted)

    def _bind_runner_evidence_context(self, generation: int) -> None:
        bind = getattr(getattr(self, "_runner", None), "bind_evidence_context", None)
        trace = self._control_trace
        identity = None if trace is None else trace.identity
        if callable(bind) and identity is not None:
            bind(generation, identity.session_id, identity.cook_id)

    def _retire_runner_evidence_context(self, generation: int) -> None:
        retire = getattr(getattr(self, "_runner", None), "retire_evidence_context", None)
        if callable(retire):
            retire(generation)

    def _rotate_evidence_sessions_for_reserved_runner_generations(self, now: float) -> None:
        """Release every reserved generation before teardown closes evidence sinks."""
        if self._runner is None:
            return
        pending = self._pending_model_observations or {}
        installed_generation = getattr(
            self._runner, "configuration_revision", lambda: self._runner_configuration_revision
        )()
        generations = sorted(
            {self._runner_configuration_revision, installed_generation, *(value[2] for value in pending.values())}
        )
        for generation in generations:
            if generation == self._runner_configuration_revision:
                self._reconcile_model_observation_outcomes(now)
                continue
            self._retire_runner_evidence_context(self._runner_configuration_revision)
            trace = self._control_trace
            if trace is not None:
                trace.rotate_identity(runner_snapshot_fallback_safe=not self._runner.runs_async())
            actual_type = getattr(self._runner, "controller_type", lambda: None)()
            if isinstance(actual_type, ControllerType):
                self._controller_name = actual_type.value
            self._runner_configuration_revision = generation
            context = self._trace_session_context()
            identity = (
                None
                if trace is None or context is None
                else trace.ensure_open(context, timestamp_ms=int(now * 1_000))
            )
            if identity is not None:
                self._bind_runner_evidence_context(generation)
            self._pending_model_observations = {
                sequence: (
                    (observation, None if identity is None else identity.session_id, pending_generation, records)
                    if pending_generation == generation
                    else (observation, session_id, pending_generation, records)
                )
                for sequence, (
                    observation,
                    session_id,
                    pending_generation,
                    records,
                ) in self._pending_model_observations.items()
            }
            self._reconcile_model_observation_outcomes(now)


    def _set_output(
        self,
        applied: AppliedOutput,
        now: float,
        *,
        producing_revision: int | None = None,
        producing_calibration_revision: int | None = None,
        producing_calibration_action: str | None = None,
        producing_calibration_generation: int | None = None,
        sample_complete: bool = False,
        feedback_disposition: FrameFeedbackDisposition = FrameFeedbackDisposition.PROGRESS,
        measured_combustion_load: float | None = None,
        dispatch: bool = True,
    ) -> AppliedOutput:
        controller = self.state.controller
        trace = self._control_trace
        if trace is None:
            raise RuntimeError("Hold control trace session is unavailable")
        prepared = trace.prepare_applied_output(
            applied,
            TraceOutputContext(
                timestamp_ms=int(now * 1_000),
                pulse_frame_result_revision=controller.pulse_frame_result_revision,
                fan_duty=controller.fan_duty,
                controls_fan=controller.controls_fan,
                producing_revision=producing_revision,
                producing_calibration_revision=(
                    getattr(controller, "pulse_frame_calibration_command_revision", 0)
                    if producing_calibration_revision is None
                    else producing_calibration_revision
                ),
                producing_calibration_action=(
                    getattr(controller, "pulse_frame_calibration_command_action", "none")
                    if producing_calibration_action is None
                    else producing_calibration_action
                ),
                producing_calibration_generation=(
                    getattr(controller, "pulse_frame_calibration_command_generation", 0)
                    if producing_calibration_generation is None
                    else producing_calibration_generation
                ),
                sample_complete=sample_complete,
                feedback_disposition=feedback_disposition,
                measured_combustion_load=measured_combustion_load,
            ),
        )
        if dispatch:
            self._runner.set_output(prepared)
        return prepared

    name = Mode.HOLD
    _model_store = None

    def setup(self):
        import control as _control

        self._control_trace = None
        self._learning_evidence_available = True
        self._reachability_advisory_key = None
        self._pending_model_observations = {}
        self._framed_pulse = FramedPulseRuntime()
        self._last_ptemp = None
        self._final_refit_done = False
        self._final_checkpoint_done = False
        self._final_checkpoint_outcome = None
        self._final_refit_outcome = None
        self._teardown_done = False
        self._activation_state_identity = None
        self._activation_lifecycle_evidence_id = None
        recorder: ControlTraceRecorder | None = None
        try:
            recorder = ControlTraceRecorder(warning=self._trace_warning)
        except Exception as error:
            self._learning_evidence_available = False
            self._trace_warning(f"Control trace recorder unavailable: {error}")
        self._control_trace = ControlTraceSession(recorder, warning=self._trace_warning)

        start_fan(self.grill, self.settings)
        self.grill.power_on()
        _control.eventLogger.debug("Power ON, Fan ON, Igniter OFF, Auger OFF")
        # Initialize cycle to minimum ratio.
        self.state.cycle.ratio = self.state.cycle.raw_ratio = 0.0

        self.state.lid.open_detected = False
        self.state.lid.expires = 0
        self.state.target_temp_achieved = False
        self._model_store = self._model_store or ControllerModelStore(
            reader=self.ctx.store.read_generic_key,
            writer=self.ctx.store.write_generic_key,
            conditional_writer=self.ctx.store.save_model_checkpoint,
        )
        try:
            self._persistence_worker = ModelPersistenceWorker(self._model_store, _control.eventLogger)
        except Exception as error:
            self._learning_evidence_available = False
            self._persistence_worker = None
            self._trace_warning(f"Model persistence unavailable: {error}")
        self._controller_name = self.settings["controller"]["selected"]

        # Load Controller Module (i.e. PID)
        self._runner, self._controller_status = _runner_mod.build_runner(
            self.settings, self.control, logger=self.ctx.control_log
        )
        actual_type = getattr(self._runner, "controller_type", lambda: None)() if self._runner is not None else None
        if isinstance(actual_type, ControllerType):
            self._controller_name = actual_type.value
        self._runner_configuration_revision = getattr(self._runner, "configuration_revision", lambda: 0)()

        if self._runner is not None:
            self._framed_pulse.configure(
                self._runner.actuation_mode(),
                controller=cast(PulseControllerState, self.state.controller),
                timing=self.grill.auger_timing(),
                now=self.ctx.clock.now(),
                calibration_command_revision=self._calibration_command_high_water,
            )
            self.state.cycle.ratio = self.state.cycle.raw_ratio = 0.0
            self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
            self.grill.auger_off()
            if self._controller_name == "mpc":
                try:
                    from controller.mpc_config import DEFAULT_MPC_CONFIG

                    selected_config = self.settings.get("controller", {}).get("config", {}).get("mpc", {})
                    defaults = dict(DEFAULT_MPC_CONFIG)
                    if isinstance(selected_config, Mapping):
                        defaults.update(selected_config)
                    migrate_mpc_learning_authority(defaults=defaults)
                except Exception as error:
                    self._learning_evidence_available = False
                    self._trace_warning(f"Model authority migration failed: {error}")
            self._restore_model()
            self._reconcile_activation_state()
        self._configure_fan_authority()

        _control.eventLogger.debug(
            "On Time = "
            + str(self.state.cycle.on_time)
            + ", OffTime = "
            + str(self.state.cycle.off_time)
            + ", CycleTime = "
            + str(self.state.cycle.cycle_time)
            + ", CycleRatio = "
            + str(self.state.cycle.ratio)
        )

        # Initialize the cycle start time to now. `ControlMode.run()` has not yet
        # set self.state.timers.start_time (that happens after setup_safety,
        # later in the shared pre-loop).
        self.state.controller.cycle_start = self.ctx.clock.now()
        if self._runner is not None:
            initial_output = seed_output(
                self.state.cycle.ratio,
                self.state.controller.cycle_start,
                lid_open=False,
                manual_override_active=False,
                auger_output=self.grill.get_output_status()["auger"],
            )
            self._set_output(initial_output, initial_output.timestamp)

    def setup_safety(self, ptemp) -> str:
        # Flameout is now a declarative pre_loop guard (GUARDS["Hold"], fired by
        # evaluate_phase in base.run before the loop). This override survives only
        # for the Hold-specific controller-build-failure abort: if the runner
        # failed to build (controller module load error), skip the work loop.
        return "Inactive" if self._controller_status == "Inactive" else "Active"

    def _adopt_runner_configuration(self, now, current_output_status):
        """Adopt one actually installed runner generation exactly once."""
        import control as _control

        retiring_generation = self._runner_configuration_revision
        trace = self._control_trace
        if trace is not None:
            trace.record_safety(
                TraceSafetyContext(
                    event=SafetyEventType.CONTROLLER_RECONFIGURE,
                    inhibit_reason=InhibitReason.NONE,
                    result_revision=trace.update_state.result_revision,
                    detail="controller reconfigured",
                    timestamp_ms=int(now * 1_000),
                )
            )
            trace.record_applied_interval(
                TraceAppliedIntervalContext(
                    timestamp_ms=int(now * 1_000),
                    sample_complete=False,
                    realized_combustion_load=None,
                    controls_fan=self.state.controller.controls_fan,
                )
            )
        self._inhibit_framed_pulse(
            PulseResetReason.MODE_CHANGE,
            now,
            InhibitReason.SAFETY,
            ptemp=self._last_ptemp,
            terminal_feedback=False,
        )
        _control.eventLogger.info("Controller reinitialized with updated settings")
        self._reconcile_model_observation_outcomes(now)
        installed_generation = getattr(self._runner, "configuration_revision", lambda: retiring_generation)()
        retained = {
            sequence: pending
            for sequence, pending in self._pending_model_observations.items()
            if pending[2] == installed_generation
        }
        self._retire_runner_evidence_context(retiring_generation)
        self._pending_model_observations = retained
        if trace is not None:
            trace.rotate(runner_snapshot_fallback_safe=not self._runner.runs_async())
        actual_type = getattr(self._runner, "controller_type", lambda: None)()
        self._controller_name = (
            actual_type.value if isinstance(actual_type, ControllerType) else self.settings["controller"]["selected"]
        )
        runtime = self._framed_pulse
        if runtime is None:
            raise RuntimeError("Hold framed pulse runtime is unavailable")
        runtime.configure(
            self._runner.actuation_mode(),
            controller=cast(PulseControllerState, self.state.controller),
            timing=self.grill.auger_timing(),
            now=self.ctx.clock.now(),
            calibration_command_revision=self._calibration_command_high_water,
        )
        self.state.cycle.ratio = self.state.cycle.raw_ratio = 0.0
        self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
        self.grill.auger_off()
        self._restore_model()
        self._activation_state_identity = None
        self._reconcile_activation_state()
        self._configure_fan_authority()
        self._runner_configuration_revision = installed_generation
        context = self._trace_session_context()
        identity = (
            None
            if trace is None or context is None
            else trace.ensure_open(context, timestamp_ms=int(now * 1_000))
        )
        if identity is not None:
            self._bind_runner_evidence_context(installed_generation)
        self._pending_model_observations = {
            sequence: (
                observation,
                None if identity is None else identity.session_id,
                generation,
                records,
            )
            for sequence, (observation, _session, generation, records) in self._pending_model_observations.items()
        }
        self._reconcile_model_observation_outcomes(now)
        self._set_output(
            seed_output(
                self.state.cycle.ratio,
                now,
                lid_open=self.state.lid.open_detected,
                manual_override_active=self.state.manual_override["auger"] > now,
                auger_output=False,
            ),
            now,
        )
        self._runner_configuration_revision = installed_generation

    def _retarget_running_controller(self) -> None:
        """Give a new setpoint to the controller that is already running.

        The alternative -- and what a setpoint change used to do -- is to raise
        control['updated'], break the work cycle and re-enter Hold, which builds
        a new controller core. For a PID that costs the integrator, which
        set_target resets anyway; for the MPC it costs the state estimator, the
        online learner and any calibration run in progress.

        The first tick only records the target: build_runner already applied it.
        """
        try:
            target = float(self.control["primary_setpoint"])
        except KeyError, TypeError, ValueError:
            return
        previous, self._last_target = self._last_target, target
        if previous is None or previous == target or self._runner is None:
            return
        self._runner.set_target(target)

    def _publish_safety_ceiling(self, now: float) -> None:
        """Push the grill's configured maximum down to the controller.

        There is no separate limit for the MPC: this is
        settings['safety']['maxtemp'], the same value the universal max-temp
        guard trips on. It is read every tick rather than stamped onto a
        calibration command, so lowering it mid-cook binds the next probe
        instead of the next operator action.
        """
        try:
            configured = self.settings["safety"]["maxtemp"]
            units = self.settings["globals"]["units"]
            if isinstance(configured, bool) or not isinstance(configured, (int, float)) or not isfinite(configured):
                raise ValueError("grill maximum temperature is not finite")
            if units == "F":
                ceiling_c = (float(configured) - 32.0) * 5.0 / 9.0
            elif units == "C":
                ceiling_c = float(configured)
            else:
                raise ValueError("grill temperature units must be Celsius or Fahrenheit")
            self._runner.set_safety_ceiling_c(ceiling_c)
        except (KeyError, NotImplementedError, TypeError, ValueError) as error:
            if self._safety_ceiling_fault != str(error):
                self._safety_ceiling_fault = str(error)
                trace = self._control_trace
                if trace is not None:
                    trace.record_safety(
                        TraceSafetyContext(
                            event=SafetyEventType.SCHEDULER_RESET,
                            inhibit_reason=InhibitReason.SAFETY,
                            result_revision=trace.update_state.result_revision,
                            detail=f"cannot read the grill maximum temperature: {error}",
                            timestamp_ms=int(now * 1_000),
                        )
                    )
            return
        self._safety_ceiling_fault = None

    def _consume_calibration_command(self, now: float) -> None:
        """Forward each control-plane revision once before its next controller solve."""
        raw = self.control.get("mpc_calibration")
        if not isinstance(raw, dict):
            return
        revision = raw.get("revision")
        if not isinstance(revision, int) or revision <= self._calibration_command_high_water:
            return
        controller = self.state.controller
        try:
            command = CalibrationCommand(
                action=raw["action"],
                command_revision=revision,
                ambient_c=raw["ambient_c"],
                ambient_source=raw["ambient_source"],
                empty_grill_confirmed=raw["empty_grill_confirmed"],
                pellets_confirmed=raw["pellets_confirmed"],
            )
            self._runner.request_calibration(command)
        except (KeyError, NotImplementedError, TypeError, ValueError) as error:
            import control as _control

            # Consume the revision anyway. Its content is fixed, so no later
            # tick can build it, and leaving it unconsumed re-attempted the
            # same command every tick for the rest of the cook -- calibration
            # never started, and the only record was a trace event. A operator
            # is told, because "nothing happened" is what they would otherwise
            # see. Re-entering Hold reconsiders it, which is what makes a
            # command rejected for a controller that cannot calibrate usable
            # again once one that can is configured.
            self._calibration_command_high_water = revision
            _control.eventLogger.error(f"Rejected calibration command revision {revision}: {error}")
            trace = self._control_trace
            if trace is not None:
                trace.record_safety(
                    TraceSafetyContext(
                        event=SafetyEventType.SCHEDULER_RESET,
                        inhibit_reason=InhibitReason.SAFETY,
                        result_revision=trace.update_state.result_revision,
                        detail=f"invalid calibration command: {error}",
                        timestamp_ms=int(now * 1_000),
                    )
                )
            return
        self._calibration_command_high_water = revision
        controller.calibration_command_revision = revision

    #: How a finished run is named in the operator's report. "inactive" is
    #: reserved for a run that never began, so a stopped or aborted one is never
    #: reported as though the operator had not asked for anything.
    _CALIBRATION_OUTCOME_STATUS = {
        "start_rejected": "rejected",
        "safety_aborted": "cancelled",
        "stage_timeout": "cancelled",
        "stopped": "cancelled",
        "completed": "accepted",
    }

    @classmethod
    def _calibration_status(cls, calibration) -> str:
        if calibration is None:
            return "inactive"
        if calibration.active:
            return "active"
        named = cls._CALIBRATION_OUTCOME_STATUS.get(calibration.outcome or "")
        if named is not None:
            return named
        return "accepted" if any(event.kind == "start_accepted" for event in calibration.events) else "inactive"

    @staticmethod
    def _calibration_reason(calibration) -> str | None:
        """The coordinator's own terminal reasons.

        They reach the control trace but nothing else, so the report can say a
        run stopped without ever saying why.
        """
        if calibration is None or not calibration.outcome_reasons:
            return None
        return ", ".join(calibration.outcome_reasons)

    def _calibration_cancellation_reason(
        self,
        result: _runner_mod.ControllerUpdateResult,
        now: float,
    ) -> str | None:
        calibration = result.calibration
        if calibration is None or not calibration.active or calibration.probe_q == 0.0:
            return None
        if self.state.lid.open_detected:
            return "lid_open"
        if self.state.manual_override["auger"] >= now:
            return "manual_override"
        if result.stale_state is ResultStaleState.STALE:
            return "stale_result"
        if self.control.get("controller_update"):
            return "reset"
        if self.control.get("mode") != Mode.HOLD:
            return "safety"
        raw = self.control.get("mpc_calibration")
        if isinstance(raw, dict):
            revision = raw.get("revision")
            action = raw.get("action")
            if (
                isinstance(revision, int)
                and revision > calibration.command_revision
                and action in {"pause", "stop", "reset-progress"}
            ):
                return f"operator_{action}"
        return None

    @staticmethod
    def _without_calibration_probe(
        result: _runner_mod.ControllerUpdateResult,
    ) -> _runner_mod.ControllerUpdateResult:
        """Return the same completed result with its exact baseline allocation."""
        baseline = result.baseline_allocation
        if baseline is None or result.calibration is None:
            return result
        return replace(
            result,
            cycle_ratio=baseline.auger_duty,
            fan=None if baseline.fan_duty is None else {"duty": baseline.fan_duty},
            allocation=baseline,
            calibration=replace(result.calibration, active=False, probe_q=0.0),
        )

    def _cancel_calibration_probe(
        self,
        result: _runner_mod.ControllerUpdateResult,
        reason: str,
        now: float,
        ptemp: float,
        *,
        notify_runner: bool,
    ) -> _runner_mod.ControllerUpdateResult:
        """Close completed frames before marking the scheduler-reset partial."""
        raw = self.control.get("mpc_calibration")
        if reason.startswith("operator_") and isinstance(raw, dict) and isinstance(raw.get("revision"), int):
            cancellation_command_revision = raw["revision"]
            cancellation_command_action = reason.removeprefix("operator_")
        else:
            cancellation_command_revision = 0
            cancellation_command_action = "safety-cancel"
        self._inhibit_framed_pulse(
            PulseResetReason.SAFETY,
            now,
            InhibitReason.SAFETY,
            ptemp=ptemp,
            terminal_feedback=True,
            report_feedback=True,
            cancellation_reason=reason,
            cancellation_command_revision=cancellation_command_revision,
            cancellation_command_action=cancellation_command_action,
            safety_event=SafetyEventType.SCHEDULER_RESET,
            safety_detail=f"calibration probe cancelled: {reason}",
            safety_result_revision=result.revision,
        )
        if notify_runner:
            self._runner.cancel_calibration(reason)
        return self._without_calibration_probe(result)

    def _trace_calibration_result(
        self,
        result: _runner_mod.ControllerUpdateResult,
        now: float,
    ) -> None:
        decision = result.calibration
        if decision is None:
            return
        trace = self._control_trace
        if trace is None:
            return
        command_action = cast(
            Literal[
                "none",
                "start",
                "pause",
                "resume",
                "stop",
                "reset-progress",
                "safety-cancel",
            ],
            decision.command_action,
        )
        for event in decision.events:
            try:
                event_type = CalibrationEventType(event.kind)
            except ValueError:
                continue
            trace.record(
                TraceEventKind.CALIBRATION,
                CalibrationTracePayload(
                    event=event_type,
                    command_revision=decision.command_revision,
                    command_action=command_action,
                    result_revision=result.revision,
                    stage=event.stage,
                    intended_probe_load=event.intended_probe_q,
                    bounded_probe_load=event.bounded_probe_q,
                    cumulative_probe_load=event.realized_probe_sum,
                    eligible_observations=decision.progress.eligible_observations,
                    positive_observations=decision.progress.positive_observations,
                    negative_observations=decision.progress.negative_observations,
                    reasons=event.reasons,
                ),
                int(now * 1_000),
            )

    def on_tick(
        self,
        now: float,
        ptemp: float,
        current_output_status: Mapping[str, bool | int | float],
    ) -> None:
        context = self._adopt_tick_configuration_and_session(
            now,
            ptemp,
            current_output_status,
        )
        context = self._release_expired_manual_auger(context)
        self._publish_safety_ceiling_and_consume_calibration(context)
        runner_result = self._submit_obtain_and_handle_calibration_cancellation(context)
        inhibition = self._decide_safety_manual_lid_inhibition(context, runner_result)
        framed_pulse = self._advance_or_reset_framed_pulse(
            context,
            runner_result,
            inhibition,
        )
        self._command_grill_hardware(framed_pulse)
        self._dispatch_framed_trace_and_feedback(context, framed_pulse)
        self._apply_hold_lid_fan_hardware_and_state(context, inhibition.lid_will_open)
        self._flush_tick_trace(context.trace)

    def _adopt_tick_configuration_and_session(
        self,
        now: float,
        ptemp: float,
        current_output_status: Mapping[str, bool | int | float],
    ) -> _HoldTickContext:
        ctx = self.ctx
        control = self.control
        self._last_ptemp = float(ptemp)
        runner_adopted = False
        runner_revision = getattr(self._runner, "configuration_revision", lambda: 0)()
        if runner_revision != self._runner_configuration_revision:
            self._adopt_runner_configuration(now, current_output_status)
            runner_adopted = True
            current_output_status = self.grill.get_output_status()
        trace = self._control_trace
        session_context = self._trace_session_context()
        previous_identity = None if trace is None else trace.identity
        identity = (
            None
            if trace is None or session_context is None
            else trace.ensure_open(session_context, timestamp_ms=int(now * 1_000))
        )
        if previous_identity is None and identity is not None:
            self._bind_runner_evidence_context(self._runner_configuration_revision)
        self._reconcile_activation_state()
        self._drain_activation_events()
        active_calibration_reset = False

        if control["controller_update"]:
            controller = self.state.controller
            latched_probe = getattr(controller, "pulse_frame_calibration_probe_load", 0.0)
            if (
                getattr(controller, "pulse_frame_calibration_status", "inactive") == "active"
                and isinstance(latched_probe, (int, float))
                and not isinstance(latched_probe, bool)
                and latched_probe != 0.0
            ):
                active_calibration_reset = True
                self._runner.cancel_calibration("reset")
            control["controller_update"] = False
            ctx.store.write_control_snapshot(control, origin="control")
            self.settings = ctx.store.read_settings()
            self._inhibit_framed_pulse(
                PulseResetReason.MODE_CHANGE,
                now,
                InhibitReason.SAFETY,
                ptemp=ptemp,
                terminal_feedback=False,
                report_feedback=False,
            )
            self._controller_status = self._runner.reconfigure(
                self.settings,
                control,
                logger=ctx.control_log,
            )
            if self._controller_status == "Active" and (
                getattr(self._runner, "configuration_revision", lambda: 0)()
                != self._runner_configuration_revision
            ):
                self._adopt_runner_configuration(now, current_output_status)
                runner_adopted = True
                current_output_status = self.grill.get_output_status()
            elif self._controller_status != "Active" and trace is not None:
                trace.record_safety(
                    TraceSafetyContext(
                        event=SafetyEventType.CONTROLLER_FALLBACK,
                        inhibit_reason=InhibitReason.SAFETY,
                        result_revision=trace.update_state.result_revision,
                        detail="controller reconfigure fell back",
                        timestamp_ms=int(now * 1_000),
                    )
                )

        return _HoldTickContext(
            now=now,
            ptemp=ptemp,
            output_status=_HoldOutputStatus(
                auger=bool(current_output_status["auger"]),
                fan=bool(current_output_status["fan"]),
                pwm=current_output_status["pwm"],
            ),
            trace=trace,
            active_calibration_reset=active_calibration_reset,
            runner_adopted=runner_adopted,
        )

    def _release_expired_manual_auger(
        self,
        context: _HoldTickContext,
    ) -> _HoldTickContext:
        manual_auger_until = self.state.manual_override["auger"]
        if manual_auger_until == 0 or manual_auger_until >= context.now:
            return context
        self._on_manual_release(
            "auger",
            context.now,
            reseed=not context.runner_adopted,
        )
        current_output_status = self.grill.get_output_status()
        return replace(
            context,
            output_status=_HoldOutputStatus(
                auger=bool(current_output_status["auger"]),
                fan=bool(current_output_status["fan"]),
                pwm=current_output_status["pwm"],
            ),
        )

    def _publish_safety_ceiling_and_consume_calibration(
        self,
        context: _HoldTickContext,
    ) -> None:
        self._retarget_running_controller()
        self._publish_safety_ceiling(context.now)
        self._consume_calibration_command(context.now)

    def _submit_obtain_and_handle_calibration_cancellation(
        self,
        context: _HoldTickContext,
    ) -> _HoldRunnerResult:
        # Feed the runner every tick so a threaded core always has a fresh temp
        # to solve; for the synchronous runner this just stores the latest temp,
        # so the value read at the gate below is unchanged.
        self._runner.submit(context.ptemp)
        self._reconcile_model_observation_outcomes(context.now)
        runtime = self._framed_pulse
        controller_interval = self._runner.control_period() or (
            0.0 if runtime is None else runtime.frame_seconds
        )
        if (context.now - self.state.controller.cycle_start) <= controller_interval:
            return _HoldRunnerResult(
                result=None,
                controller_interval=controller_interval,
                cancellation_reason=None,
            )

        result = self._runner.latest()
        self._drain_activation_events()
        cancellation_reason = None
        if context.active_calibration_reset:
            result = self._without_calibration_probe(result)
        else:
            cancellation_reason = self._calibration_cancellation_reason(result, context.now)
            if cancellation_reason is not None:
                result = self._cancel_calibration_probe(
                    result,
                    cancellation_reason,
                    context.now,
                    context.ptemp,
                    notify_runner=not cancellation_reason.startswith("operator_"),
                )
        if isinstance(result.diagnostics, MpcTraceDiagnostics):
            self._observe_reachability_advisory(result.diagnostics)
        return _HoldRunnerResult(
            result=result,
            controller_interval=controller_interval,
            cancellation_reason=cancellation_reason,
        )

    def _decide_safety_manual_lid_inhibition(
        self,
        context: _HoldTickContext,
        runner_result: _HoldRunnerResult,
    ) -> _HoldInhibitionDecision:
        ctx = self.ctx
        control = self.control
        settings = self.settings
        result = runner_result.result
        framed_feedback_due = result is not None
        if result is not None:
            controller = self.state.controller
            controller.cycle_start = context.now
            if result.revision > 0 and (
                result.revision > controller.pulse_result_revision
                or runner_result.cancellation_reason is not None
            ):
                controller.output = result.cycle_ratio
                controller.pulse_result_revision = result.revision
                controller.pulse_requested_duty = max(0.0, min(1.0, result.cycle_ratio))
                controller.pulse_combustion_load = (
                    result.allocation.normalized_combustion_load
                    if result.allocation is not None
                    else None
                )
                controller.pulse_baseline_combustion_load = (
                    result.baseline_allocation.normalized_combustion_load
                    if result.baseline_allocation is not None
                    else controller.pulse_combustion_load or 0.0
                )
                controller.pulse_calibration_probe_load = (
                    result.calibration.probe_q if result.calibration is not None else 0.0
                )
                controller.pulse_calibration_stage = (
                    result.calibration.stage
                    if result.calibration is not None and result.calibration.active
                    else None
                )
                controller.pulse_calibration_completed_stages = (
                    result.calibration.completed_stages
                    if result.calibration is not None
                    else ()
                )
                controller.pulse_maximum_duty = (
                    result.allocation.u_max if result.allocation is not None else 1.0
                )
                controller.pulse_allocator_revision = (
                    result.allocation.allocator_revision if result.allocation is not None else 0
                )
                controller.pulse_allocation_clamp_reasons = (
                    tuple(
                        reason
                        for reason in (
                            result.allocation.auger_clamp_reason,
                            result.allocation.fan_clamp_reason,
                        )
                        if reason is not AllocationClampReason.NONE
                    )
                    if result.allocation is not None
                    else ()
                )
                controller.pulse_baseline_allocation = result.baseline_allocation
                controller.pulse_combined_allocation = result.allocation
                controller.pulse_calibration_command_revision = (
                    result.calibration.command_revision if result.calibration is not None else 0
                )
                controller.pulse_calibration_command_action = (
                    result.calibration.command_action if result.calibration is not None else "none"
                )
                controller.pulse_calibration_command_generation = (
                    result.calibration.command_generation if result.calibration is not None else 0
                )
                controller.pulse_calibration_cancellation_reason = (
                    runner_result.cancellation_reason or self._calibration_reason(result.calibration)
                )
                controller.pulse_calibration_status = self._calibration_status(result.calibration)
                controller.pulse_allocation_evidence_checked = True
                controller.pulse_allocation_result_revision = (
                    result.revision if result.allocation is not None else None
                )
                controller.pulse_requested_fan_duty = (
                    result.fan["duty"] if result.fan is not None else None
                )
                if result.fan is not None and controller_fan_authority(settings, control):
                    controller.fan_duty = result.fan["duty"]
                    control["duty_cycle"] = controller.fan_duty
                    ctx.store.write_control_snapshot(control, origin="control")
            controller.pulse_stale_command = result.stale_state is ResultStaleState.STALE

        lid_will_open = (
            self.state.target_temp_achieved
            and settings["cycle_data"]["LidOpenDetectEnabled"]
            and context.ptemp
            < control["primary_setpoint"]
            * ((100 - settings["cycle_data"]["LidOpenThreshold"]) / 100)
        )
        permit_framed_pulse = (
            not self.state.controller.pulse_stale_command
            and self.state.manual_override["auger"] < context.now
            and not self.state.lid.open_detected
        )
        return _HoldInhibitionDecision(
            lid_will_open=lid_will_open,
            permit_framed_pulse=permit_framed_pulse,
            framed_feedback_due=framed_feedback_due,
        )

    def _advance_or_reset_framed_pulse(
        self,
        context: _HoldTickContext,
        runner_result: _HoldRunnerResult,
        inhibition: _HoldInhibitionDecision,
    ) -> _HoldFramedPulse:
        result = runner_result.result
        if result is not None:
            controller = self.state.controller
            control = self.control
            if controller.pulse_stale_command:
                self._inhibit_framed_pulse(
                    PulseResetReason.SAFETY,
                    context.now,
                    InhibitReason.STALE_COMMAND,
                    ptemp=context.ptemp,
                    terminal_feedback=True,
                    report_feedback=True,
                )
            self.state.cycle.ratio = self.state.cycle.raw_ratio = controller.pulse_requested_duty
            self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
            trace = context.trace
            if trace is not None:
                applied_state = trace.applied_state
                lifecycle = (
                    self._model_lifecycle_payload(result.diagnostics.model_lifecycle)
                    if isinstance(result.diagnostics, MpcTraceDiagnostics)
                    else None
                )
                trace.record_update(
                    TraceUpdateContext(
                        result=result,
                        timestamp_ms=int(context.now * 1_000),
                        controller_interval_seconds=float(runner_result.controller_interval),
                        setpoint=float(control["primary_setpoint"]),
                        prior_requested_auger_duty=applied_state.requested_auger_duty,
                        prior_realized_auger_duty=applied_state.realized_auger_duty,
                        prior_fan_duty=applied_state.fan_duty,
                        controls_fan=controller.controls_fan,
                        lid_open=self.state.lid.open_detected,
                        manual_override_active=self.state.manual_override["auger"] >= context.now,
                        lifecycle_event=lifecycle,
                    )
                )
            self._trace_calibration_result(result, context.now)
            snapshot = self._runner.get_model_snapshot()
            if isinstance(snapshot, dict):
                self._checkpoint_model(snapshot)
        if not inhibition.permit_framed_pulse:
            return _HoldFramedPulse(
                result=None,
                lid_will_open=inhibition.lid_will_open,
                report_feedback=inhibition.framed_feedback_due,
            )
        runtime = self._framed_pulse
        if runtime is None:
            raise RuntimeError("Hold framed pulse runtime is unavailable")
        prior_output_source = (
            None if context.trace is None else context.trace.applied_state.output_source
        )
        result = runtime.advance(
            context.now,
            context.output_status.auger,
            sample=self._framed_sample(context.ptemp),
            prior_output_source=prior_output_source,
        )
        return _HoldFramedPulse(
            result=result,
            lid_will_open=inhibition.lid_will_open,
            report_feedback=inhibition.framed_feedback_due,
        )

    def _command_grill_hardware(self, framed_pulse: _HoldFramedPulse) -> None:
        result = framed_pulse.result
        if result is None:
            return
        transition = result.decision.transition
        if not framed_pulse.lid_will_open and transition is not None:
            if transition.command_on:
                self.grill.auger_on()
            else:
                self.grill.auger_off()
        if framed_pulse.lid_will_open:
            self.grill.auger_off()

    def _dispatch_framed_trace_and_feedback(
        self,
        context: _HoldTickContext,
        framed_pulse: _HoldFramedPulse,
    ) -> None:
        now = context.now
        trace = context.trace
        pulse_result = framed_pulse.result
        if pulse_result is not None:
            runtime = self._framed_pulse
            if runtime is None:
                raise RuntimeError("Hold framed pulse runtime is unavailable")
            self._dispatch_framed_result(
                pulse_result,
                record_terminal_trace=False,
            )
            if (
                trace is not None
                and self.state.controller.pulse_frame_result_revision > 0
            ):
                trace.promote_seed_interval(
                    self.state.controller.pulse_frame_result_revision,
                    self.state.controller.pulse_frame_output_source,
                )
            if framed_pulse.report_feedback:
                feedback = runtime.report_feedback(
                    now,
                    pulse_result.decision.delivered_on_s,
                    source=classify_output_source(
                        lid_open=self.state.lid.open_detected,
                        manual_override_active=self.state.manual_override["auger"] >= now,
                    ),
                    prior_output_source=(
                        None if trace is None else trace.applied_state.output_source
                    ),
                )
                if feedback is not None:
                    self._dispatch_framed_feedback(feedback)

    def _apply_hold_lid_fan_hardware_and_state(
        self,
        context: _HoldTickContext,
        lid_will_open: bool,
    ) -> None:
        now = context.now
        ptemp = context.ptemp
        trace = context.trace
        control = self.control
        settings = self.settings

        grill_platform = self.grill
        if ptemp >= control["primary_setpoint"] and not self.state.target_temp_achieved:
            self.state.target_temp_achieved = True

        if lid_will_open:
            self.state.lid.open_detected = True
            if trace is not None:
                trace.record_applied_interval(
                    TraceAppliedIntervalContext(
                        timestamp_ms=int(now * 1_000),
                        sample_complete=False,
                        realized_combustion_load=None,
                        controls_fan=self.state.controller.controls_fan,
                    )
                )
            self._inhibit_framed_pulse(
                PulseResetReason.LID,
                now,
                InhibitReason.LID_OPEN,
                ptemp=ptemp,
                terminal_feedback=True,
                safety_event=SafetyEventType.LID_DETECTED,
                safety_detail="lid open detected",
            )
            self._set_output(
                AppliedOutput(
                    ratio=0.0,
                    source=classify_output_source(
                        lid_open=True,
                        manual_override_active=self.state.manual_override["auger"] >= now,
                    ),
                    timestamp=now,
                    requested=self.state.controller.output,
                ),
                now,
            )
            grill_platform.fan_off()
            self.state.timers.auger_toggle = now
            self.state.lid.expires = now + settings["cycle_data"]["LidOpenPauseTime"]
            self.state.target_temp_achieved = False

        if self.state.lid.open_detected and self.ctx.clock.now() > self.state.lid.expires:
            self.state.lid.open_detected = False
            if trace is not None:
                trace.record_safety(
                    TraceSafetyContext(
                        event=SafetyEventType.LID_CLEARED,
                        inhibit_reason=InhibitReason.NONE,
                        result_revision=trace.update_state.result_revision,
                        detail="lid open pause elapsed",
                        timestamp_ms=int(now * 1_000),
                    )
                )
            start_fan(grill_platform, settings, control["duty_cycle"])
        if control["lid_open_toggle"]:
            control["lid_open_toggle"] = False
            self.ctx.store.write_control_snapshot(control, origin="control")
            if self.state.lid.open_detected:
                self.state.lid.open_detected = False
                if trace is not None:
                    trace.record_safety(
                        TraceSafetyContext(
                            event=SafetyEventType.LID_CLEARED,
                            inhibit_reason=InhibitReason.NONE,
                            result_revision=trace.update_state.result_revision,
                            detail="lid open cleared by operator",
                            timestamp_ms=int(now * 1_000),
                        )
                    )
            else:
                self.state.lid.open_detected = True
                if trace is not None:
                    trace.record_applied_interval(
                        TraceAppliedIntervalContext(
                            timestamp_ms=int(now * 1_000),
                            sample_complete=False,
                            realized_combustion_load=None,
                            controls_fan=self.state.controller.controls_fan,
                        )
                    )
                self._inhibit_framed_pulse(
                    PulseResetReason.LID,
                    now,
                    InhibitReason.LID_OPEN,
                    ptemp=ptemp,
                    terminal_feedback=True,
                    safety_event=SafetyEventType.LID_DETECTED,
                    safety_detail="lid open set by operator",
                )
                self._set_output(
                    AppliedOutput(
                        ratio=0.0,
                        source=classify_output_source(
                            lid_open=True,
                            manual_override_active=self.state.manual_override["auger"] >= now,
                        ),
                        timestamp=now,
                        requested=self.state.controller.output,
                    ),
                    now,
                )
                grill_platform.fan_off()
                self.state.timers.auger_toggle = now
                self.state.lid.expires = now + settings["cycle_data"]["LidOpenPauseTime"]

        if (
            settings["platform"]["dc_fan"]
            and control["pwm_control"]
            and not self.state.controller.controls_fan
            and (now - self.state.fan.update_time) > settings["pwm"]["update_time"]
        ):
            self.state.fan.update_time = now
            duty = hold_duty_cycle(control["primary_setpoint"], ptemp, settings["pwm"])
            if duty is not None:
                control["duty_cycle"] = duty
                self.ctx.store.write_control_snapshot(control, origin="control")

        self._smoke_plus_fan_tick(now, ptemp, context.output_status)


    def _flush_tick_trace(self, trace: ControlTraceSession | None) -> None:
        if trace is not None:
            trace.flush_due(time.monotonic_ns() // 1_000_000)

    def _on_manual_output(self, name, output):
        if name != "auger" or self._runner is None:
            return
        trace = self._control_trace
        if trace is not None:
            trace.record_applied_interval(
                TraceAppliedIntervalContext(
                    timestamp_ms=int(self._last_now * 1_000),
                    sample_complete=False,
                    realized_combustion_load=None,
                    controls_fan=self.state.controller.controls_fan,
                )
            )
        self._inhibit_framed_pulse(
            PulseResetReason.MANUAL,
            self._last_now,
            InhibitReason.MANUAL_OVERRIDE,
            ptemp=self._last_ptemp,
            terminal_feedback=False,
            safety_event=SafetyEventType.MANUAL_TAKEOVER,
            safety_detail="manual auger output applied",
        )
        if output and not self.grill.get_output_status()["auger"]:
            self.grill.auger_on()
        elif not output and self.grill.get_output_status()["auger"]:
            self.grill.auger_off()
        self._set_output(
            AppliedOutput(
                ratio=1.0 if output else 0.0,
                source=classify_output_source(
                    lid_open=False,
                    manual_override_active=True,
                ),
                timestamp=self._last_now,
            ),
            self._last_now,
        )

    def _on_manual_release(
        self,
        name: str,
        now: float,
        *,
        reseed: bool = True,
    ) -> None:
        if name != "auger":
            return
        if self.grill.get_output_status()["auger"]:
            self.grill.auger_off()
        self.state.manual_override["auger"] = 0
        trace = self._control_trace
        if trace is not None:
            trace.record_safety(
                TraceSafetyContext(
                    event=SafetyEventType.MANUAL_RELEASE,
                    inhibit_reason=InhibitReason.NONE,
                    result_revision=trace.update_state.result_revision,
                    detail="manual auger override expired",
                    timestamp_ms=int(now * 1_000),
                )
            )
        if self._runner is None or not reseed:
            return
        auger_on = self.grill.get_output_status()["auger"]
        seeded = seed_output(
            self.state.cycle.ratio,
            now,
            lid_open=self.state.lid.open_detected,
            manual_override_active=False,
            auger_output=auger_on,
        )
        if trace is None:
            self._runner.set_output(seeded)
        else:
            self._set_output(seeded, seeded.timestamp)

    def _on_safety_event(self, event, now):
        events = {
            "stop": SafetyEventType.STOP,
            "error": SafetyEventType.ERROR,
            "temperature_guard": SafetyEventType.TEMPERATURE_GUARD,
        }
        event_type = events.get(event)
        if event_type is not None:
            trace = self._control_trace
            context = self._trace_session_context()
            previous_identity = None if trace is None else trace.identity
            identity = (
                None
                if trace is None or context is None
                else trace.ensure_open(context, timestamp_ms=int(now * 1_000))
            )
            if previous_identity is None and identity is not None:
                self._bind_runner_evidence_context(self._runner_configuration_revision)
            self._inhibit_framed_pulse(
                PulseResetReason.SAFETY,
                now,
                InhibitReason.SAFETY,
                ptemp=self._last_ptemp,
                terminal_feedback=True,
                safety_event=event_type,
                safety_detail=event.replace("_", " "),
            )

    def _restore_model(self):
        trace = self._control_trace
        if trace is not None:
            trace.clear_model_authority()
        snapshot = self._model_store.load(self._controller_name)
        if snapshot is None:
            return
        import control as _control

        # True means accepted for restore, not adopted -- an asynchronous runner
        # only queues it for its worker thread, so whether it took hold is not
        # knowable from the Hold loop.
        if self._runner.restore_model(snapshot):
            provenance = "restore_submitted" if self._runner.runs_async() else "restored"
            if trace is not None:
                trace.set_model_authority(cast(Mapping[str, JsonValue], snapshot), provenance)
            _control.eventLogger.info(f"Submitted the stored {self._controller_name} model for restore")
            if trace is not None:
                trace.record_model(
                    TraceModelContext(
                        event=ModelEventType.RESTORE,
                        detail="stored model submitted for restore",
                        snapshot=cast(Mapping[str, JsonValue], snapshot),
                        provenance="persisted",
                        timestamp_ms=int(self.ctx.clock.now() * 1_000),
                    )
                )
        else:
            _control.eventLogger.warning(f"Stored {self._controller_name} model was rejected; starting fresh")
            if trace is not None:
                trace.record_model(
                    TraceModelContext(
                        event=ModelEventType.REJECT,
                        detail="stored model rejected for restore",
                        snapshot=cast(Mapping[str, JsonValue], snapshot),
                        provenance="persisted",
                        timestamp_ms=int(self.ctx.clock.now() * 1_000),
                    )
                )

    @staticmethod
    def _activation_identity(state) -> tuple[object, ...] | None:
        if state is None or state.transaction_id is None:
            return None
        return (
            state.phase,
            state.transaction_id,
            state.role_generation,
            state.incumbent_pair_json,
            state.candidate_pair_json,
            state.rollback_pair_json,
            state.reason,
        )

    @staticmethod
    def _pair_activation_lifecycle(state, records):
        candidate = getattr(state, "candidate_pair", None)
        if candidate is None:
            return None
        matches = [
            record
            for record in records
            if (
                isinstance(record.payload, RollbackEvidence)
                and record.payload.decision_id == state.evidence_decision_id
                and record.model_digest == candidate.model_digest
            )
            or (
                isinstance(record.payload, FallbackEvidence)
                and record.payload.failed_digest == candidate.model_digest
                and record.payload.failed_generation == candidate.role_generation
            )
        ]
        return max(matches, key=lambda record: (record.timestamp_ms, record.evidence_id), default=None)

    def _reconcile_activation_state(self) -> None:
        """Submit durable ownership changes without persistence work on Hold."""
        if self._runner is None or self._controller_name != "mpc":
            return
        try:
            state = read_model_activation()
            records = tuple(read_model_evidence())
        except Exception as error:
            self._learning_evidence_available = False
            self._trace_warning(f"Model activation state unavailable: {error}")
            return
        identity = self._activation_identity(state)
        if state is not None and identity is None:
            self._learning_evidence_available = False
            self._trace_warning("Model activation authority uses a retired schema")
            return
        lifecycle = None if state is None else self._pair_activation_lifecycle(state, records)
        if state is not None and identity != self._activation_state_identity:
            self._runner.restore_activation(state, records)
            self._activation_state_identity = identity
            self._activation_lifecycle_evidence_id = None if lifecycle is None else lifecycle.evidence_id
            return
        if lifecycle is None or lifecycle.evidence_id == self._activation_lifecycle_evidence_id:
            return
        self._activation_lifecycle_evidence_id = lifecycle.evidence_id
        if isinstance(lifecycle.payload, RollbackEvidence):
            self._runner.rollback_activation(lifecycle.payload.reason)
        elif isinstance(lifecycle.payload, FallbackEvidence):
            self._runner.activation_runtime_failure(lifecycle.payload.reason)

    def _drain_activation_events(self) -> None:
        if self._runner is None:
            return
        events = tuple(self._runner.drain_activation_events())
        if not events:
            return
        worker = self._persistence_worker
        if worker is None or not worker.submit_evidence_batch(events).accepted:
            self._learning_evidence_available = False
            self._trace_warning("Model activation fallback evidence was not persisted")

    # check_safety is now a declarative pre_act guard (GUARDS["Hold"]); the base
    # ControlMode default (return False) applies here.

    def status_fragment(self) -> dict:
        status = {
            "lid_open_detected": self.state.lid.open_detected,
            "lid_open_endtime": self.state.lid.expires,
            "actuation_mode": ActuationMode.FRAMED_PULSE.value,
        }
        learning = self._runner_status().get("learning")
        if isinstance(learning, Mapping):
            status["learning"] = dict(learning)
        runtime = self._framed_pulse
        scheduler = None if runtime is None else runtime.scheduler
        if scheduler is not None:
            status["pulse"] = {
                "slot_seconds": scheduler.timing.pulse_s,
                "frame_seconds": scheduler.timing.frame_s,
                "result_revision": self.state.controller.pulse_result_revision,
                "stale_command": self.state.controller.pulse_stale_command,
            }
        return status

    def _refit_model(self) -> tuple[TeardownRefitOutcome, object | None]:
        import control as _control

        try:
            config = self.settings["controller"].get("config", {})
            controller_config = config.get(self._controller_name, {})
            identification_enabled = controller_config.get("enable_identification") is True
        except Exception as error:
            _control.eventLogger.error(f"Model refit failed at cook end: {error}")
            return TeardownRefitOutcome.DISABLED, None
        if not identification_enabled:
            _control.eventLogger.info("Model refit skipped at cook end: Learn This Grill is disabled.")
            return TeardownRefitOutcome.DISABLED, None

        try:
            verdict = self._runner.refit_from_cook()
        except Exception as error:
            _control.eventLogger.error(f"Model refit failed at cook end: {error}")
            return TeardownRefitOutcome.FAILED, None

        reason = getattr(verdict, "reason", None) or "no reason recorded"
        outcome = getattr(verdict, "outcome", None)
        if not isinstance(outcome, TeardownRefitOutcome):
            if verdict is None:
                outcome = TeardownRefitOutcome.INSUFFICIENT
            elif getattr(verdict, "accepted", False):
                origin = getattr(verdict, "origin", None)
                outcome = (
                    TeardownRefitOutcome.READY_FOR_REVIEW
                    if getattr(origin, "value", origin) == "operator-calibration"
                    else TeardownRefitOutcome.ACCEPTED_NEXT_COOK
                )
            else:
                outcome = TeardownRefitOutcome.REJECTED
        _control.eventLogger.info(f"Model refit at cook end: {outcome.value} ({reason}).")
        return outcome, verdict

    def _refit_model_once(self) -> tuple[TeardownRefitOutcome, object | None]:
        if self._final_refit_done:
            return self._final_refit_outcome
        self._final_refit_done = True
        self._final_refit_outcome = self._refit_model()
        return self._final_refit_outcome

    def _publish_final_checkpoint_once(
        self,
        outcome: TeardownRefitOutcome,
        verdict: object | None,
    ) -> bool:
        if getattr(self, "_final_checkpoint_done", False):
            return True
        final_outcome = getattr(self, "_final_checkpoint_outcome", None) or outcome
        try:
            if self._runner.finalize_cook_refit(final_outcome) is False:
                raise RuntimeError("refit outcome was not finalized")
        except Exception:
            final_outcome = TeardownRefitOutcome.CHECKPOINT_FAILURE
            self._final_checkpoint_outcome = final_outcome
            try:
                if self._runner.finalize_cook_refit(final_outcome) is False:
                    return False
            except Exception:
                return False
        snapshot = self._runner.get_model_snapshot()
        if not isinstance(snapshot, dict):
            self._learning_evidence_available = False
            return False
        trace = self._control_trace
        if trace is not None:
            trace.clear_model_authority()
            trace.record_model(
                TraceModelContext(
                    event=ModelEventType.REFIT,
                    detail=f"model refit outcome: {final_outcome.value}",
                    snapshot=cast(Mapping[str, JsonValue], snapshot),
                    provenance="persisted",
                    timestamp_ms=int(self.ctx.clock.now() * 1_000),
                )
            )
            if final_outcome in {
                TeardownRefitOutcome.READY_FOR_REVIEW,
                TeardownRefitOutcome.ACCEPTED_NEXT_COOK,
            }:
                trace.record_model(
                    TraceModelContext(
                        event=ModelEventType.ADOPT,
                        detail=getattr(verdict, "reason", final_outcome.value),
                        snapshot=cast(Mapping[str, JsonValue], snapshot),
                        provenance="persisted",
                        timestamp_ms=int(self.ctx.clock.now() * 1_000),
                    )
                )
            elif final_outcome is not TeardownRefitOutcome.DISABLED:
                trace.record_model(
                    TraceModelContext(
                        event=ModelEventType.REJECT,
                        detail=getattr(verdict, "reason", final_outcome.value),
                        snapshot=cast(Mapping[str, JsonValue], snapshot),
                        provenance="persisted",
                        timestamp_ms=int(self.ctx.clock.now() * 1_000),
                    )
                )
        accepted = self._checkpoint_model(snapshot)
        if accepted:
            self._final_checkpoint_done = True
            return True
        if final_outcome is not TeardownRefitOutcome.CHECKPOINT_FAILURE:
            try:
                self._runner.finalize_cook_refit(
                    TeardownRefitOutcome.CHECKPOINT_FAILURE
                )
                self._final_checkpoint_outcome = TeardownRefitOutcome.CHECKPOINT_FAILURE
            except Exception:
                pass
        return False

    def teardown(self, ptemp):
        if getattr(self, "_teardown_done", False):
            return
        trace = getattr(self, "_control_trace", None)
        first_trace_teardown = trace is not None and not trace.status.closed
        now = self.ctx.clock.now()
        runtime = getattr(self, "_framed_pulse", None)
        runner = getattr(self, "_runner", None)
        if runtime is not None and runtime.scheduler is not None and runner is not None:
            prior_output_source = None if trace is None else trace.applied_state.output_source
            pulse_result = runtime.advance(
                now,
                self.grill.get_output_status()["auger"],
                sample=self._framed_sample(ptemp),
                prior_output_source=prior_output_source,
            )
            self.grill.auger_off()
            self._dispatch_framed_result(pulse_result, record_terminal_trace=False)
            feedback = runtime.report_feedback(
                now,
                pulse_result.decision.delivered_on_s,
                source=classify_output_source(
                    lid_open=self.state.lid.open_detected,
                    manual_override_active=self.state.manual_override["auger"] >= now,
                ),
                prior_output_source=(
                    None if trace is None else trace.applied_state.output_source
                ),
                dispatch=not bool(pulse_result.decision.completed_frames),
            )
            if feedback is not None:
                self._dispatch_framed_feedback(feedback)
        self._inhibit_framed_pulse(
            PulseResetReason.MODE_CHANGE,
            now,
            InhibitReason.SAFETY,
            ptemp=ptemp,
            terminal_feedback=False,
        )
        try:
            if runner is not None:
                stopped = runner.stop_for_refit()
                if getattr(self, "ctx", None) is not None:
                    self._rotate_evidence_sessions_for_reserved_runner_generations(self.ctx.clock.now())
                if stopped is False:
                    self._trace_warning("Controller worker did not stop; final checkpoint was not queued")
                else:
                    outcome, verdict = self._refit_model_once()
                    checkpointed = self._publish_final_checkpoint_once(outcome, verdict)
                    if not checkpointed:
                        self._publish_final_checkpoint_once(outcome, verdict)
        finally:
            self._retire_runner_evidence_context(self._runner_configuration_revision)
            worker = getattr(self, "_persistence_worker", None)
            flushed = True
            if worker is not None:
                flushed = worker.flush_and_stop() and not bool(getattr(worker, "failed", False))
                self._persistence_worker = None
            if not flushed and runner is not None:
                try:
                    runner.finalize_cook_refit(TeardownRefitOutcome.CHECKPOINT_FAILURE)
                except Exception:
                    pass
            if first_trace_teardown and trace is not None:
                trace.flush_pending()
                trace.record_applied_interval(
                    TraceAppliedIntervalContext(
                        timestamp_ms=int(self.ctx.clock.now() * 1_000),
                        sample_complete=False,
                        realized_combustion_load=None,
                        controls_fan=self.state.controller.controls_fan,
                    )
                )
                trace.close()
            if runner is not None:
                try:
                    runner.finish_teardown()
                except Exception as error:
                    self._trace_warning(f"Controller teardown close failed: {error}")
            self._teardown_done = True
