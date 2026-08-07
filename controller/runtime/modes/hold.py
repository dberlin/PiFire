from collections.abc import Mapping
from math import isfinite

import time
import uuid
from dataclasses import replace

from common.common import WriteKind
from common.controller_model_state import ControllerModelStore
from common.modes import Mode
from common.model_evidence import AllocationEvidence, CalibrationSummaryEvidence, EvidenceKind, ModelEvidenceRecord
from common.control_trace import (
    ActuationMode,
    AllocationClampReason,
    AllocationPayload,
    CalibrationEventType,
    CalibrationTracePayload,
    AmbientSource,
    AmbientUncertainty,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    InhibitReason,
    ModelEventPayload,
    ModelEvaluationPayload,
    ModelEventType,
    RecorderGapPayload,
    MpcUpdatePayload,
    ModelObservationPayload,
    ResultStaleState,
    PidSpUpdatePayload,
    PidUpdatePayload,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from controller.applied_output import AppliedOutput, OutputSource, classify_output_source, seed_output
from controller.linear_mpc.contracts import FrameObservation
from controller.mpc import CalibrationCommand
from controller.mpc_allocator import normalized_load_from_auger_duty
from controller.runtime.logic.pulse import (
    PulseDecision,
    PulseFrameResult,
    PulseReason,
    PulseResetReason,
    PulseScheduler,
)

from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.base import MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.model_promotion import ReachabilityState
from controller.runtime.logic.fan import controller_fan_authority, start_fan
from controller.runtime.logic.pwm import hold_duty_cycle
from controller.runtime.modes.base import ControlMode
import controller.runtime.runner as _runner_mod


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

    Per-tick, on_tick() first handles the `controller_update` reconfigure
    request, then runs the Hold-specific controller sub-block (submit the
    fresh per-tick ptemp to the runner, normalize its output into a cycle
    ratio + optional fan command, route an MPC fan command into
    `control['duty_cycle']` when one arrives, clamp to u_min/u_max), then the
    shared (non-Hold) auger-cycle toggle via `_auger_cycle_tick` (Hold
    overrides `_on_auger_on` to also recompute OnTime/OffTime/CycleTime and
    publish MQTT PID info -- the shared helper itself is untouched). It then
    runs the Hold-only fan work on the same fresh ptemp: the
    target_temp_achieved latch, lid-open detect/clear, and
    PWM-duty-from-temp-profile (gated `not self.state.controller.controls_fan`),
    then delegates to the shared `_smoke_plus_fan_tick` helper (gated on
    target_temp_achieved for Hold, unlike Smoke which always runs it).

    status_fragment() adds the Hold-only primary_setpoint/lid_open_detected/
    lid_open_endtime status fields. No mode-specific teardown (Hold is not in
    the Shutdown/Monitor/Manual/Prime power-off teardown gate, nor the
    Startup/Reignite afterstarttemp-write teardown gate)."""

    _trace_recorder: ControlTraceRecorder | None = None
    _trace_session_id: str | None = None
    _trace_cook_id: str | None = None
    _trace_warning_active: bool = False
    _trace_pending_model_events: list[tuple[ModelEventPayload, int]] | None = None
    _trace_closed: bool = False
    _trace_session_model_snapshot: dict | None = None
    _trace_session_model_provenance: str | None = None
    _trace_last_update_payload: MpcUpdatePayload | PidUpdatePayload | PidSpUpdatePayload | None = None
    _trace_runner_snapshot_fallback_safe: bool = True
    _runner_configuration_revision: int = 0
    _actuation_mode: ActuationMode = ActuationMode.FRAMED_PULSE
    _pulse_scheduler: PulseScheduler | None = None
    _pulse_frame_role_generation: int = 0
    _pending_model_observations: (
        dict[int, tuple[FrameObservation, str | None, int, tuple[tuple[TraceEventKind, object], ...] | None]] | None
    ) = None
    _pulse_observation_last_frame_key: tuple[int, int] | None = None
    _pulse_observation_sequence: int = 0
    _last_ptemp: float | None = None
    _persistence_worker: ModelPersistenceWorker | None = None
    _learning_evidence_available: bool = True
    _final_refit_done: bool = False
    _PENDING_MODEL_OBSERVATION_CAPACITY = 60
    _calibration_command_high_water: int = 0



    def _pulse_frame_seconds(self) -> float:
        """The scheduler's frame, or zero before one has been built."""
        scheduler = self._pulse_scheduler
        return 0.0 if scheduler is None else float(scheduler.timing.frame_s)

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

    def _configure_pulse_scheduler(self) -> None:
        mode = self._runner.actuation_mode()
        if mode is not ActuationMode.FRAMED_PULSE:
            raise ValueError("Hold requires framed pulse actuation")
        self._actuation_mode = mode
        self._pulse_scheduler = PulseScheduler(self.grill.auger_timing())
        self.state.cycle.ratio = self.state.cycle.raw_ratio = 0.0
        self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
        controller = self.state.controller
        controller.pulse_result_revision = -1
        controller.pulse_frame_result_revision = 0
        controller.pulse_requested_duty = 0.0
        controller.pulse_combustion_load = None
        controller.pulse_requested_fan_duty = None
        controller.pulse_maximum_duty = 1.0
        controller.pulse_stale_command = False
        controller.pulse_frame_combustion_load = None
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
        self._pulse_observation_sequence = 0
        controller.pulse_feedback_start_s = self.ctx.clock.now()
        controller.pulse_feedback_delivered_on_s = 0.0
        controller.pulse_metrics_delivered_on_s = 0.0
        controller.calibration_command_revision = self._calibration_command_high_water
        controller.pulse_baseline_combustion_load = 0.0
        controller.pulse_calibration_command_revision = 0
        controller.pulse_calibration_command_action = "none"
        controller.pulse_calibration_cancellation_reason = None
        controller.pulse_baseline_allocation = None
        controller.pulse_combined_allocation = None
        controller.pulse_calibration_probe_load = 0.0
        controller.pulse_calibration_stage = None
        self.grill.auger_off()

    def _latch_pulse_frame(self) -> None:
        controller = self.state.controller
        controller.pulse_frame_result_revision = max(0, controller.pulse_result_revision)
        controller.pulse_frame_combustion_load = controller.pulse_combustion_load
        controller.pulse_frame_baseline_combustion_load = controller.pulse_baseline_combustion_load
        controller.pulse_frame_requested_auger_duty = controller.pulse_requested_duty
        controller.pulse_frame_maximum_duty = controller.pulse_maximum_duty
        controller.pulse_frame_requested_fan_duty = controller.pulse_requested_fan_duty
        controller.pulse_frame_maximum_duty = controller.pulse_maximum_duty
        controller.pulse_frame_applied_fan_duty = controller.fan_duty if controller.controls_fan else None
        controller.pulse_frame_stale_command = controller.pulse_stale_command
        controller.pulse_frame_allocator_revision = controller.pulse_allocator_revision
        controller.pulse_frame_allocation_clamp_reasons = controller.pulse_allocation_clamp_reasons
        controller.pulse_frame_allocation_evidence_checked = controller.pulse_allocation_evidence_checked
        controller.pulse_frame_allocation_result_revision = controller.pulse_allocation_result_revision
        controller.pulse_frame_calibration_command_revision = controller.pulse_calibration_command_revision
        controller.pulse_frame_calibration_command_action = controller.pulse_calibration_command_action
        controller.pulse_frame_calibration_cancellation_reason = controller.pulse_calibration_cancellation_reason
        controller.pulse_frame_baseline_allocation = controller.pulse_baseline_allocation
        controller.pulse_frame_combined_allocation = controller.pulse_combined_allocation
        controller.pulse_frame_calibration_probe_load = controller.pulse_calibration_probe_load
        controller.pulse_frame_calibration_stage = controller.pulse_calibration_stage
        self._pulse_frame_role_generation = self._model_role_generation(self._runner_status())

    def _runner_status(self) -> Mapping[str, object]:
        if self._runner is None:
            return {}
        try:
            status = self._runner.controller_state()
        except Exception:
            return {}
        return status if isinstance(status, Mapping) else {}

    def _model_role_generation(self, status: Mapping[str, object]) -> int:
        adaptation = status.get("adaptation")
        source = adaptation if isinstance(adaptation, Mapping) else status
        generation = source.get("role_generation", 0)
        return generation if isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0 else 0

    @staticmethod
    def _to_c(value: float, units: object) -> float:
        temperature = float(value)
        return (temperature - 32.0) * 5.0 / 9.0 if str(units).upper() == "F" else temperature

    @staticmethod
    def _fan_fraction(duty: float | None) -> float | None:
        if duty is None:
            return None
        return max(0.0, min(1.0, float(duty) / 100.0))

    def _frame_output_source(self, frame: PulseFrameResult, inhibit: InhibitReason) -> str:
        if inhibit is InhibitReason.MANUAL_OVERRIDE or frame.reset_reason is PulseResetReason.MANUAL:
            return OutputSource.MANUAL_OVERRIDE.value
        if inhibit is InhibitReason.LID_OPEN or frame.reset_reason is PulseResetReason.LID:
            return OutputSource.LID_OPEN.value
        if (
            inhibit is InhibitReason.SAFETY
            or frame.reset_reason is PulseResetReason.SAFETY
            or frame.reset_reason is PulseResetReason.MODE_CHANGE
            or self.state.controller.pulse_frame_combustion_load is None
        ):
            return "unknown"
        return OutputSource.CONTROLLER.value

    def _build_completed_pulse_observation(
        self, frame: PulseFrameResult, *, ptemp: float | None, sample_at_s: float, inhibit: InhibitReason
    ) -> tuple[tuple[int, int], FrameObservation] | None:
        controller = self.state.controller
        if ptemp is None or controller.pulse_frame_result_revision <= 0 or frame.ended_at_s <= frame.nominal_start_s:
            return None
        frame_key = (int(frame.nominal_start_s * 1_000), int(frame.ended_at_s * 1_000))
        if frame_key == self._pulse_observation_last_frame_key:
            return None
        duration_s = frame.ended_at_s - frame.nominal_start_s
        source = self._frame_output_source(frame, inhibit)
        lid_open = inhibit is InhibitReason.LID_OPEN or frame.reset_reason is PulseResetReason.LID
        manual_override = inhibit is InhibitReason.MANUAL_OVERRIDE or frame.reset_reason is PulseResetReason.MANUAL
        safety_inhibited = inhibit is InhibitReason.SAFETY or frame.reset_reason is PulseResetReason.SAFETY
        stale = controller.pulse_frame_stale_command or inhibit is InhibitReason.STALE_COMMAND
        reset = frame.reset_reason is not None
        continuous = not (
            lid_open
            or manual_override
            or safety_inhibited
            or stale
            or frame.skipped
            or reset
            or source == "unknown"
            or frame.ended_at_s < sample_at_s
        )
        role_generation = self._pulse_frame_role_generation
        self._pulse_observation_sequence += 1
        baseline_q = max(
            0.0,
            min(
                1.0,
                getattr(
                    controller,
                    "pulse_frame_baseline_combustion_load",
                    controller.pulse_frame_combustion_load or 0.0,
                ),
            ),
        )
        probe_q = getattr(controller, "pulse_frame_calibration_probe_load", 0.0)
        requested_q = max(0.0, min(1.0, baseline_q + probe_q))
        realized_auger_duty = frame.delivered_on_s / duration_s
        observation = FrameObservation(
            frame_start_s=frame.nominal_start_s,
            frame_end_s=frame.ended_at_s,
            temp_c=self._to_c(ptemp, self.settings["globals"]["units"]),
            setpoint_c=self._to_c(self.control["primary_setpoint"], self.settings["globals"]["units"]),
            ambient_c=float(
                self.settings["controller"].get("config", {}).get(self._controller_name, {}).get("T_amb", 0.0)
            ),
            requested_q=requested_q,
            realized_q=normalized_load_from_auger_duty(
                realized_auger_duty, u_max=controller.pulse_frame_maximum_duty
            ),
            requested_auger_duty=frame.latched_request,
            delivered_on_s=frame.delivered_on_s,
            requested_fan_duty=self._fan_fraction(controller.pulse_frame_requested_fan_duty),
            actual_fan_duty=self._fan_fraction(controller.pulse_frame_applied_fan_duty),
            result_revision=controller.pulse_frame_result_revision,
            output_source=source,
            lid_open=lid_open,
            safety_inhibited=safety_inhibited,
            manual_override=manual_override,
            stale=stale,
            skipped=frame.skipped,
            reset=reset,
            continuous=continuous,
            role_generation=role_generation,
            observation_sequence=self._pulse_observation_sequence,
            probe_valid=True,
            probe_source="chamber",
            ambient_source=AmbientSource.CONFIGURED,
            ambient_uncertainty=AmbientUncertainty.UNMEASURED,
            baseline_q=baseline_q,
            probe_q=probe_q,
            allocated_q=requested_q,
            scheduled_on_s=frame.scheduled_on_s,
            realized_auger_duty=realized_auger_duty,
            allocator_revision=controller.pulse_frame_allocator_revision,
            allocation_clamp_reasons=controller.pulse_frame_allocation_clamp_reasons,
            calibration_command_revision=getattr(controller, "pulse_frame_calibration_command_revision", 0),
            calibration_command_action=getattr(controller, "pulse_frame_calibration_command_action", "none"),
            calibration_cancellation_reason=getattr(
                controller, "pulse_frame_calibration_cancellation_reason", None
            ),
            baseline_allocation=getattr(controller, "pulse_frame_baseline_allocation", None),
            combined_allocation=getattr(controller, "pulse_frame_combined_allocation", None),
            calibration_stage=getattr(controller, "pulse_frame_calibration_stage", None),
            calibration_fit=getattr(controller, "pulse_frame_calibration_stage", None) is not None,
            allocation_join_reason=(
                None
                if not controller.pulse_frame_allocation_evidence_checked
                else (
                    "missing-allocation"
                    if controller.pulse_frame_allocation_result_revision is None
                    else (
                        None
                        if controller.pulse_frame_allocation_result_revision == controller.pulse_frame_result_revision
                        else "allocation-revision-mismatch"
                    )
                )
            ),
        )
        return frame_key, observation

    def _record_pending_observation_gap(self, observation: FrameObservation, reason: str) -> None:
        self._trace_record(
            TraceEventKind.RECORDER_GAP,
            RecorderGapPayload(
                lost_record_count=1,
                gap_start_ms=int(observation.frame_start_s * 1_000),
                gap_end_ms=int(observation.frame_end_s * 1_000),
                reason=reason,
                frame_start_ms=int(observation.frame_start_s * 1_000),
                frame_end_ms=int(observation.frame_end_s * 1_000),
                result_revision=observation.result_revision,
                observation_sequence=observation.observation_sequence,
            ),
            int(observation.frame_end_s * 1_000),
        )

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
        ):
            return None
        payload = CalibrationSummaryEvidence(
            accepted=observation.calibration_cancellation_reason is None,
            probe_count=1,
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
            delivered_on_seconds=observation.delivered_on_s,
            requested_fan_duty=observation.requested_fan_duty,
            actual_fan_duty=observation.actual_fan_duty,
            cancellation_reason=observation.calibration_cancellation_reason,
        )
        return ModelEvidenceRecord(
            evidence_id=(
                f"{session_id}:calibration-frame:{observation.result_revision}:"
                f"{int(observation.frame_start_s * 1_000)}"
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

    def _deliver_completed_pulse_observation(self, frame_key: tuple[int, int], observation: FrameObservation) -> None:
        if not observation.probe_valid:
            self._pulse_observation_last_frame_key = frame_key
            if self._pending_model_observations is None:
                self._pending_model_observations = {}
            sequence = -1
            while sequence in self._pending_model_observations:
                sequence -= 1
            self._pending_model_observations[sequence] = (
                observation,
                self._trace_session_id,
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
        submission = self._runner.observe_frame(observation)
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
        self._pulse_observation_last_frame_key = frame_key
        if self._pending_model_observations is None:
            self._pending_model_observations = {}
        self._pending_model_observations[sequence] = (observation, self._trace_session_id, generation, None)
        evicted_sequence = getattr(submission, "evicted_sequence", None)
        if isinstance(evicted_sequence, int) and not isinstance(evicted_sequence, bool):
            self._retire_pending_model_observation(evicted_sequence, "runner-observation-evicted")
        self._bound_pending_model_observations()

    def _trace_missing_frame_observation(self, frame: PulseFrameResult, reason: str) -> None:
        if frame.ended_at_s <= frame.nominal_start_s:
            return
        self._pulse_observation_sequence += 1
        self._trace_record(
            TraceEventKind.RECORDER_GAP,
            RecorderGapPayload(
                lost_record_count=1,
                gap_start_ms=int(frame.nominal_start_s * 1_000),
                gap_end_ms=int(frame.ended_at_s * 1_000),
                reason=reason,
                frame_start_ms=int(frame.nominal_start_s * 1_000),
                frame_end_ms=int(frame.ended_at_s * 1_000),
                result_revision=(
                    self.state.controller.pulse_frame_result_revision
                    if self.state.controller.pulse_frame_result_revision > 0
                    else None
                ),
                observation_sequence=self._pulse_observation_sequence,
            ),
            int(frame.ended_at_s * 1_000),
        )

    def _observe_completed_pulse_frame(
        self, frame: PulseFrameResult, *, ptemp: float | None, inhibit: InhibitReason, sample_at_s: float | None = None
    ) -> None:
        captured = self._build_completed_pulse_observation(
            frame, ptemp=ptemp, sample_at_s=frame.ended_at_s if sample_at_s is None else sample_at_s, inhibit=inhibit
        )
        if captured is not None:
            self._deliver_completed_pulse_observation(*captured)
        elif ptemp is None:
            self._trace_missing_frame_observation(frame, "missing-temperature")
        elif self.state.controller.pulse_frame_result_revision < 0:
            self._trace_missing_frame_observation(frame, "missing-result-revision")

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
            frame_start_ms=int(observation.frame_start_s * 1_000), frame_end_ms=int(observation.frame_end_s * 1_000),
            calibration_command_revision=observation.calibration_command_revision,
            calibration_command_action=observation.calibration_command_action,
            calibration_cancellation_reason=observation.calibration_cancellation_reason,
            baseline_allocation=HoldMode._trace_allocation_payload(
                observation.baseline_allocation, observation.result_revision
            ),
            combined_allocation=HoldMode._trace_allocation_payload(
                observation.combined_allocation, observation.result_revision
            ),
            temp_c=observation.temp_c, setpoint_c=observation.setpoint_c, ambient_c=observation.ambient_c,
            observation_sequence=observation.observation_sequence, probe_valid=observation.probe_valid,
            probe_source=observation.probe_source, ambient_source=observation.ambient_source,
            ambient_uncertainty=observation.ambient_uncertainty, baseline_combustion_load=observation.baseline_q,
            calibration_probe_load=observation.probe_q, requested_combustion_load=observation.requested_q,
            allocated_combustion_load=observation.allocated_q, realized_combustion_load=observation.realized_q,
            requested_auger_duty=observation.requested_auger_duty, scheduled_on_seconds=observation.scheduled_on_s,
            delivered_on_seconds=observation.delivered_on_s, realized_auger_duty=observation.realized_auger_duty,
            allocator_revision=observation.allocator_revision, allocation_clamp_reasons=observation.allocation_clamp_reasons,
            calibration_stage=observation.calibration_stage, calibration_fit=observation.calibration_fit,
            result_revision=observation.result_revision, eligible=False, rejection_reasons=(reason,),
            input_variance=0.0, input_levels=0, incumbent_innovation_c=None, challenger_innovation_c=None,
            effective_updates=0, role_generation=observation.role_generation, model_digest=None,
            requested_fan_duty=observation.requested_fan_duty, actual_fan_duty=observation.actual_fan_duty,
            output_source=output_source, lid_open=observation.lid_open, safety_inhibited=observation.safety_inhibited,
            manual_override=observation.manual_override, stale=observation.stale, skipped=observation.skipped,
            reset=observation.reset, continuous=observation.continuous,
        )

    def _queue_rejected_model_observation(self, sequence: int, reason: str) -> None:
        pending = self._pending_model_observations.get(sequence)
        if pending is None or not isinstance(pending[0], FrameObservation):
            self._pending_model_observations.pop(sequence, None)
            return
        self._pending_model_observations[sequence] = (
            *pending[:3],
            ((TraceEventKind.MODEL_OBSERVATION, self._rejected_model_observation(pending[0], reason)),),
        )

    def _flush_pending_model_trace(
        self,
        sequence: int,
        pending: tuple[FrameObservation, str | None, int, tuple[tuple[TraceEventKind, object], ...] | None],
        publication_ms: int,
    ) -> bool:
        remaining = pending[3]
        if not isinstance(remaining, tuple):
            return False
        while remaining:
            event_kind, payload = remaining[0]
            if not self._trace_record(event_kind, payload, publication_ms):
                self._pending_model_observations[sequence] = (*pending[:3], remaining)
                return False
            remaining = remaining[1:]
        self._pending_model_observations.pop(sequence, None)
        return True

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
                if (
                    isinstance(sequence, int)
                    and not isinstance(sequence, bool)
                    and isinstance(reason, str)
                    and reason
                ):
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
            if generation != pending[2] or pending[1] != self._trace_session_id:
                self._queue_rejected_model_observation(sequence, "observation-configuration-mismatch")
                continue
            if not isinstance(delivered, FrameObservation) or not isinstance(outcome, Mapping):
                self._queue_rejected_model_observation(sequence, "observation-outcome-malformed")
                continue
            observation = delivered
            try:
                if observation.allocation_join_reason is not None:
                    self._queue_rejected_model_observation(sequence, observation.allocation_join_reason)
                    continue
                if not observation.probe_valid:
                    self._queue_rejected_model_observation(sequence, "invalid-probe")
                    continue
                role_generation = outcome["role_generation"]
                if role_generation != observation.role_generation:
                    self._queue_rejected_model_observation(sequence, "observation-role-generation-mismatch")
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
                    self._queue_rejected_model_observation(sequence, "observation-gate-mismatch")
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
                    calibration_command_revision=observation.calibration_command_revision,
                    calibration_command_action=observation.calibration_command_action,
                    calibration_cancellation_reason=observation.calibration_cancellation_reason,
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
            except (KeyError, TypeError, ValueError):
                self._queue_rejected_model_observation(sequence, "observation-outcome-malformed")
                continue
            records: list[tuple[TraceEventKind, object]] = [(TraceEventKind.MODEL_OBSERVATION, observation_payload)]
            evaluation_payload = outcome.get("evaluation_payload")
            if isinstance(evaluation_payload, ModelEvaluationPayload):
                records.append((TraceEventKind.MODEL_EVALUATION, evaluation_payload))
            lifecycle_payload = self._model_lifecycle_payload(outcome.get("lifecycle"))
            if lifecycle_payload is not None:
                records.append((TraceEventKind.MODEL_EVENT, lifecycle_payload))
            queued = (*pending[:3], tuple(records))
            self._pending_model_observations[sequence] = queued
            compact = self._calibration_frame_evidence(observation, pending[1], self._trace_cook_id)
            compact_batch = (
                (*(evidence if isinstance(evidence, tuple) else ()), compact)
                if compact is not None
                else (evidence if isinstance(evidence, tuple) else ())
            )
            if (
                compact_batch
                and self._persistence_worker is not None
                and not self._persistence_worker.submit_evidence_batch(compact_batch).accepted
            ):
                self._learning_evidence_available = False
        for sequence, pending in tuple(self._pending_model_observations.items()):
            if pending[3] is None or not self._flush_pending_model_trace(sequence, pending, publication_ms):
                break

    def _trace_pulse_frame(
        self, frame: PulseFrameResult, inhibit: InhibitReason, result_revision: int | None = None
    ) -> None:
        scheduler = self._pulse_scheduler
        if scheduler is None:
            return
        controller = self.state.controller
        revision = controller.pulse_result_revision if result_revision is None else result_revision
        if revision <= 0 or frame.ended_at_s <= frame.nominal_start_s:
            return
        self._trace_record(
            TraceEventKind.ACTUATION_FRAME,
            FramedPulseFramePayload(
                result_revision=revision,
                pulse_slot_seconds=float(scheduler.timing.pulse_s),
                frame_seconds=float(scheduler.timing.frame_s),
                frame_start_ms=int(frame.nominal_start_s * 1_000),
                frame_end_ms=int(frame.ended_at_s * 1_000),
                requested_combustion_load=controller.pulse_frame_combustion_load or 0.0,
                requested_auger_duty=frame.latched_request,
                credit_before_seconds=frame.credit_before_s,
                credit_after_seconds=frame.credit_after_s,
                scheduled_on_seconds=frame.scheduled_on_s,
                delivered_on_seconds=frame.delivered_on_s,
                transition_count=frame.observed_transition_count,
                actual_start_active=frame.actual_start_on,
                actual_end_active=frame.actual_end_on,
                requested_fan_duty=controller.pulse_frame_requested_fan_duty,
                applied_fan_duty=controller.pulse_frame_applied_fan_duty,
                skipped=frame.skipped,
                stale_command=controller.pulse_frame_stale_command,
                inhibit_reason=inhibit,
                reset_reason=frame.reset_reason.value if frame.reset_reason is not None else None,
            ),
            int(frame.ended_at_s * 1_000),
        )

    def _record_pulse_delivery(self, delivered_on_s: float) -> None:
        controller = self.state.controller
        delivered_since_metrics = delivered_on_s - controller.pulse_metrics_delivered_on_s
        if delivered_since_metrics <= 0:
            return
        controller.pulse_metrics_delivered_on_s = delivered_on_s
        self.state.metrics["augerontime"] = self.state.metrics.get("augerontime", 0.0) + delivered_since_metrics
        self.ctx.store.update_metrics(self.state.metrics)

    def _advance_framed_pulse(
        self, now: float, actual_auger_on: bool, *, ptemp: float | None = None, apply_transition: bool = True
    ) -> PulseDecision:
        scheduler = self._pulse_scheduler
        if scheduler is None:
            raise RuntimeError("framed actuation requires a pulse scheduler")
        controller = self.state.controller
        previous_frame_revision = controller.pulse_frame_result_revision
        completed_request = controller.pulse_frame_requested_auger_duty
        completed_maximum_duty = controller.pulse_frame_maximum_duty
        decision = scheduler.advance(controller.pulse_requested_duty, now, actual_auger_on)
        self._record_pulse_delivery(decision.delivered_on_s)
        captured = tuple(
            observation
            for frame in decision.completed_frames
            if (
                observation := self._build_completed_pulse_observation(
                    frame, ptemp=ptemp, sample_at_s=now, inhibit=InhibitReason.NONE
                )
            )
            is not None
        )
        for frame in decision.completed_frames:
            self._trace_pulse_frame(frame, InhibitReason.NONE, previous_frame_revision)
        if decision.reason in (PulseReason.FRAME_STARTED, PulseReason.FRAME_SKIPPED, PulseReason.RESET):
            self._latch_pulse_frame()
            transition_at_s: float | None = None
            delivered_at_transition_s = decision.delivered_on_s
            if decision.completed_frames:
                transition_at_s = decision.completed_frames[-1].ended_at_s
                delivered_at_transition_s -= decision.frame_delivered_on_s
            elif previous_frame_revision == 0 and controller.pulse_frame_result_revision > 0:
                transition_at_s = now
            if not decision.completed_frames:
                previous_frame_revision = controller.pulse_frame_result_revision
                completed_request = controller.pulse_frame_requested_auger_duty
                completed_maximum_duty = controller.pulse_frame_maximum_duty
            if transition_at_s is not None:
                self._report_framed_feedback(
                    transition_at_s,
                    delivered_at_transition_s,
                    ptemp=ptemp,
                    completed_revision=previous_frame_revision,
                    completed_request=completed_request,
                    completed_maximum_duty=completed_maximum_duty,
                )
        if apply_transition and decision.transition is not None:
            if decision.transition.command_on:
                self.grill.auger_on()
            else:
                self.grill.auger_off()
        for frame_key, observation in captured:
            self._deliver_completed_pulse_observation(frame_key, observation)
        return decision

    def _reset_framed_pulse(
        self,
        reason: PulseResetReason,
        now: float,
        inhibit: InhibitReason,
        *,
        ptemp: float | None = None,
        report_feedback: bool = False,
    ) -> None:
        scheduler = self._pulse_scheduler
        if scheduler is None:
            return
        self._trace_safety(
            SafetyEventType.SCHEDULER_RESET,
            now,
            f"framed pulse scheduler reset: {reason.value}",
            inhibit,
            result_revision=self.state.controller.pulse_frame_result_revision,
        )
        actual_auger_on = self.grill.get_output_status()["auger"]
        decision = scheduler.advance(self.state.controller.pulse_requested_duty, now, actual_auger_on)
        self._record_pulse_delivery(decision.delivered_on_s)
        observation_temp = self._last_ptemp if ptemp is None else ptemp
        for completed in decision.completed_frames:
            self._trace_pulse_frame(completed, inhibit, self.state.controller.pulse_frame_result_revision)
            self._observe_completed_pulse_frame(completed, ptemp=observation_temp, sample_at_s=now, inhibit=inhibit)
        if report_feedback:
            self._report_framed_feedback(now, decision.delivered_on_s, ptemp=observation_temp)
        if decision.reason in (PulseReason.FRAME_STARTED, PulseReason.FRAME_SKIPPED, PulseReason.RESET):
            self._latch_pulse_frame()
        frame = scheduler.reset(reason)
        if frame is not None:
            self._observe_completed_pulse_frame(frame, ptemp=observation_temp, sample_at_s=now, inhibit=inhibit)
            self._trace_pulse_frame(frame, inhibit, self.state.controller.pulse_frame_result_revision)
        self._pulse_frame_role_generation = 0
        controller = self.state.controller
        controller.pulse_metrics_delivered_on_s = decision.delivered_on_s
        controller.pulse_feedback_start_s = now
        controller.pulse_feedback_delivered_on_s = decision.delivered_on_s
        self.grill.auger_off()

    def _report_framed_feedback(
        self,
        now: float,
        delivered_on_s: float,
        *,
        ptemp: float | None = None,
        completed_revision: int | None = None,
        completed_request: float | None = None,
        completed_maximum_duty: float | None = None,
    ) -> None:
        controller = self.state.controller
        start = controller.pulse_feedback_start_s
        if start is None:
            controller.pulse_feedback_start_s = now
            controller.pulse_feedback_delivered_on_s = delivered_on_s
            return
        elapsed = now - start
        if elapsed <= 0:
            return
        realized_duty = max(0.0, min(1.0, (delivered_on_s - controller.pulse_feedback_delivered_on_s) / elapsed))
        requested = controller.pulse_frame_requested_auger_duty if completed_request is None else completed_request
        maximum_duty = controller.pulse_frame_maximum_duty if completed_maximum_duty is None else completed_maximum_duty
        revision = controller.pulse_frame_result_revision if completed_revision is None else completed_revision
        applied = AppliedOutput(
            ratio=realized_duty,
            source=classify_output_source(
                lid_open=self.state.lid.open_detected,
                manual_override_active=self.state.manual_override["auger"] >= now,
            ),
            timestamp=now,
            requested=requested,
        )
        inverse_combustion_load = normalized_load_from_auger_duty(realized_duty, u_max=maximum_duty)
        measured_source = controller.trace_prior_output_source is OutputSource.CONTROLLER
        sample_complete = controller.trace_prior_output_source is OutputSource.SEED or measured_source
        if measured_source:
            controller.trace_interval_result_revision = revision
            controller.trace_prior_requested_auger_duty = (
                applied.requested if applied.requested is not None else applied.ratio
            )
            controller.trace_prior_realized_auger_duty = applied.ratio
            controller.trace_prior_output_source = applied.source
            controller.trace_prior_fan_duty = controller.fan_duty
            controller.trace_prior_combustion_load = inverse_combustion_load
        self._set_output(
            applied,
            now,
            producing_revision=revision,
            sample_complete=sample_complete,
        )
        if measured_source:
            controller.trace_prior_combustion_load = inverse_combustion_load
        controller.pulse_feedback_start_s = now
        controller.pulse_feedback_delivered_on_s = delivered_on_s

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

    def _clear_trace_session_model_authority(self) -> None:
        self._trace_session_model_snapshot = None
        self._trace_session_model_provenance = None

    def _set_trace_session_model_authority(self, snapshot: dict, provenance: str) -> None:
        revision = snapshot.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            self._clear_trace_session_model_authority()
            return
        self._trace_session_model_snapshot = snapshot
        self._trace_session_model_provenance = provenance

    def _trace_settings(self, value, prefix: str = "") -> tuple[TraceSetting, ...]:
        entries: list[TraceSetting] = []
        if isinstance(value, dict):
            for key in sorted(value):
                name = f"{prefix}.{key}" if prefix else str(key)
                entries.extend(self._trace_settings(value[key], name))
        elif isinstance(value, str | int | float | bool):
            entries.append(TraceSetting(key=prefix, value=value))
        return tuple(entries)

    def _trace_record(self, event_kind: TraceEventKind, payload, ts_ms: int) -> bool:
        recorder = self._trace_recorder
        controller = self._trace_type()
        if recorder is None or self._trace_session_id is None or self._trace_cook_id is None or controller is None:
            return False
        try:
            recorder.record(
                ControlTraceRecord(
                    ts_ms=ts_ms,
                    session_id=self._trace_session_id,
                    cook_id=self._trace_cook_id,
                    controller=controller,
                    event_kind=event_kind,
                    payload=payload,
                )
            )
        except Exception as error:
            if not self._trace_warning_active:
                self._trace_warning(f"Control trace record failed: {error}")
                self._trace_warning_active = True
            return False
        self._trace_warning_active = False
        return True

    def _flush_pending_model_events(self) -> None:
        if self._trace_session_id is None or self._trace_pending_model_events is None:
            return
        while self._trace_pending_model_events:
            payload, timestamp_ms = self._trace_pending_model_events[0]
            if not self._trace_record(TraceEventKind.MODEL_EVENT, payload, timestamp_ms):
                return
            del self._trace_pending_model_events[0]

    def _queue_model_event(self, payload: ModelEventPayload, timestamp_ms: int) -> None:
        if self._trace_pending_model_events is None:
            return
        self._trace_pending_model_events.append((payload, timestamp_ms))
        self._flush_pending_model_events()

    def _checkpoint_model(self, snapshot: dict[str, object]) -> None:
        worker = self._persistence_worker
        if worker is None or not worker.submit_checkpoint(self._controller_name, snapshot):
            self._learning_evidence_available = False

    def _bind_runner_evidence_context(self, generation: int) -> None:
        bind = getattr(getattr(self, "_runner", None), "bind_evidence_context", None)
        if callable(bind) and self._trace_session_id is not None:
            bind(generation, self._trace_session_id, self._trace_cook_id)

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
            self._trace_session_id = None
            self._trace_cook_id = None
            self._clear_trace_session_model_authority()
            actual_type = getattr(self._runner, "controller_type", lambda: None)()
            if isinstance(actual_type, ControllerType):
                self._controller_name = actual_type.value
            self._trace_runner_snapshot_fallback_safe = not self._runner.runs_async()
            self._runner_configuration_revision = generation
            self._ensure_trace_session(now)
            self._pending_model_observations = {
                sequence: (
                    (observation, self._trace_session_id, pending_generation, records)
                    if pending_generation == generation
                    else (observation, session_id, pending_generation, records)
                )
                for sequence, (observation, session_id, pending_generation, records) in self._pending_model_observations.items()
            }
            self._reconcile_model_observation_outcomes(now)

    def _ensure_trace_session(self, now: float) -> None:
        if self._trace_session_id is not None:
            self._flush_pending_model_events()
            return
        cook_id = self.state.metrics.get("id")
        controller = self._trace_type()
        if not isinstance(cook_id, str) or not cook_id or controller is None:
            return
        config = self.settings["controller"].get("config", {}).get(self._controller_name, {})
        temperature_unit = str(self.settings["globals"]["units"])
        ambient_celsius = float(config.get("T_amb", 0.0))
        session_ambient_temperature = ambient_celsius * 9.0 / 5.0 + 32.0 if temperature_unit == "F" else ambient_celsius
        snapshot = self._trace_session_model_snapshot
        provenance = self._trace_session_model_provenance
        if snapshot is None and self._trace_runner_snapshot_fallback_safe:
            snapshot = self._runner.get_model_snapshot() if self._runner is not None else None
            provenance = "persisted"
        model_revision = snapshot.get("revision") if isinstance(snapshot, dict) else None
        if isinstance(model_revision, bool) or not isinstance(model_revision, int) or model_revision < 0:
            model_revision = None
            provenance = None

        scheduler = self._pulse_scheduler

        payload = SessionPayload(
            controller=controller,
            controller_config=self._trace_settings(config),
            temperature_unit=temperature_unit,
            control_period_seconds=float(self._runner.control_period() or scheduler.timing.frame_s),
            model_revision=model_revision,
            model_provenance=provenance if model_revision is not None else None,
            pulse_slot_seconds=float(scheduler.timing.pulse_s),
            pulse_frame_seconds=float(scheduler.timing.frame_s),
            fan_authority=self.state.controller.controls_fan,
            fan_pwm_capable=bool(self.settings["platform"]["dc_fan"]),
            fan_min_duty=float(config.get("fan_min_pct", 0.0)),
            fan_max_duty=float(config.get("fan_max_pct", 100.0)),
            setpoint=float(self.control["primary_setpoint"]),
            ambient_temperature=session_ambient_temperature,
            software_version=str(self.settings.get("versions", {}).get("server", "unknown")),
            build_version=str(self.settings.get("versions", {}).get("build", "unknown")),
        )
        self._trace_session_id = str(uuid.uuid4())
        self._trace_cook_id = cook_id
        if not self._trace_record(TraceEventKind.SESSION, payload, int(now * 1_000)):
            self._trace_session_id = None
            self._trace_cook_id = None
            return
        self._bind_runner_evidence_context(self._runner_configuration_revision)
        self._flush_pending_model_events()

    def _trace_safety(
        self,
        event: SafetyEventType,
        now: float,
        detail: str,
        inhibit: InhibitReason,
        *,
        result_revision: int | None = None,
    ) -> None:
        revision = self.state.controller.trace_result_revision if result_revision is None else result_revision
        self._trace_record(
            TraceEventKind.SAFETY_EVENT,
            SafetyEventPayload(
                event=event,
                inhibit_reason=inhibit,
                result_revision=revision if revision >= 0 else None,
                detail=detail,
            ),
            int(now * 1_000),
        )

    def _trace_model(self, event: ModelEventType, detail: str, snapshot=None) -> None:
        now_ms = int(self.ctx.clock.now() * 1_000)
        revision = snapshot.get("revision") if isinstance(snapshot, dict) else None
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            revision = None
        payload = ModelEventPayload(
            event=event,
            model_revision=revision,
            provenance="persisted" if revision is not None else None,
            detail=detail,
        )
        self._queue_model_event(payload, now_ms)

    def _trace_update(self, result, now: float, controller_interval: float) -> bool:
        if result is None or result.revision == 0:
            return False
        diagnostics = result.diagnostics
        stale_observation = (
            isinstance(diagnostics, MpcTraceDiagnostics)
            and result.revision == self.state.controller.trace_result_revision
            and not self.state.controller.trace_mpc_stale
            and result.stale_state is ResultStaleState.STALE
        )
        if result.revision < self.state.controller.trace_result_revision or (
            result.revision == self.state.controller.trace_result_revision and not stale_observation
        ):
            return False
        observed_ms = int(now * 1_000)
        if stale_observation:
            previous_payload = self._trace_last_update_payload
            if not isinstance(previous_payload, MpcUpdatePayload):
                return False
            payload = replace(
                previous_payload,
                result_age_ms=max(0, int(result.result_age_seconds * 1_000)),
                stale=True,
                stale_state=ResultStaleState.STALE,
                recovered=False,
            )
            self._trace_record(TraceEventKind.CONTROL_UPDATE, payload, observed_ms)
            self.state.controller.trace_mpc_stale = True
            self._trace_last_update_payload = payload
            return True
        if diagnostics is None or result.completed_wall_time is None or result.solve_end_monotonic is None:
            return False
        wall_ms = int(result.completed_wall_time * 1_000)
        monotonic_ms = int(result.solve_end_monotonic * 1_000)
        common = dict(
            monotonic_ms=monotonic_ms,
            wall_ms=wall_ms,
            result_revision=result.revision,
            result_age_ms=max(0, observed_ms - wall_ms),
            observed_dt_seconds=(
                diagnostics.observed_dt_seconds
                if isinstance(diagnostics, PidTraceDiagnostics)
                else result.solve_duration_seconds or 0.0
            ),
            control_period_seconds=float(controller_interval),
            setpoint=float(self.control["primary_setpoint"]),
            measured_temperature=result.input_temperature,
            raw_output=(
                diagnostics.raw_output
                if isinstance(diagnostics, PidTraceDiagnostics)
                else (
                    diagnostics.raw_policy_firing_load
                    if diagnostics.raw_policy_firing_load is not None
                    else diagnostics.bounded_firing_load
                )
            ),
            requested_output=(
                diagnostics.final_output
                if isinstance(diagnostics, PidTraceDiagnostics)
                else diagnostics.bounded_firing_load
            ),
            actuation_mode=self._actuation_mode,
            prior_requested_auger_duty=self.state.controller.trace_prior_requested_auger_duty,
            prior_realized_auger_duty=self.state.controller.trace_prior_realized_auger_duty,
            requested_fan_duty=result.fan["duty"] if result.fan is not None else None,
            applied_fan_duty=self.state.controller.trace_prior_fan_duty,
            output_source=classify_output_source(
                lid_open=self.state.lid.open_detected,
                manual_override_active=self.state.manual_override["auger"] >= now,
            ),
            inhibit_reason=InhibitReason.LID_OPEN if self.state.lid.open_detected else InhibitReason.NONE,
        )
        allocation_payload: AllocationPayload | None = None
        realized_combustion_load: float | None = None
        if isinstance(diagnostics, PidSpTraceDiagnostics):
            payload = PidSpUpdatePayload(
                **common,
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
                **common,
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
            mpc_common = dict(common, result_age_ms=max(0, int(result.result_age_seconds * 1_000)))
            payload = MpcUpdatePayload(
                **mpc_common,
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
                predicted_steady_load=None
                if diagnostics.feasibility is None
                else diagnostics.feasibility.predicted_steady_load,
                solve_duration_ms=max(0, int(result.solve_duration_seconds * 1_000)),
                consecutive_deadline_miss_count=result.consecutive_deadline_miss_count,
                stale_state=result.stale_state,
            )
            realized_combustion_load = None
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
                    mpc_has_fan_authority=self.state.controller.controls_fan,
                    auger_clamp_reason=allocation.auger_clamp_reason,
                    fan_clamp_reason=allocation.fan_clamp_reason,
                    allocator_revision=allocation.allocator_revision,
                )
        else:
            return False

        self.state.controller.trace_result_revision = result.revision
        self._trace_record(TraceEventKind.CONTROL_UPDATE, payload, observed_ms)
        self._trace_last_update_payload = payload
        if isinstance(diagnostics, MpcTraceDiagnostics):
            lifecycle_payload = self._model_lifecycle_payload(diagnostics.model_lifecycle)
            if lifecycle_payload is not None:
                self._queue_model_event(lifecycle_payload, observed_ms)
        if allocation_payload is not None and not stale_observation:
            self._trace_record(TraceEventKind.ALLOCATION, allocation_payload, observed_ms)
        if isinstance(diagnostics, MpcTraceDiagnostics):
            self.state.controller.trace_mpc_stale = result.stale_state is ResultStaleState.STALE
        return True

    def _trace_complete_applied_interval(
        self,
        now: float,
        *,
        sample_complete: bool,
        realized_combustion_load: float | None,
    ) -> None:
        controller = self.state.controller
        sample_complete = sample_complete or (
            controller.trace_interval_result_revision == 0 and controller.trace_prior_output_source is OutputSource.SEED
        )
        end_ms = int(now * 1_000)
        start_ms = controller.trace_interval_start_ms
        if start_ms is None or start_ms >= end_ms or controller.trace_prior_output_source is None:
            return
        self._trace_record(
            TraceEventKind.APPLIED_OUTPUT,
            AppliedOutputPayload(
                result_revision=controller.trace_interval_result_revision,
                interval_start_ms=start_ms,
                interval_end_ms=end_ms,
                realized_auger_duty=controller.trace_prior_realized_auger_duty,
                realized_combustion_load=(
                    None
                    if not sample_complete
                    else (
                        controller.trace_prior_combustion_load
                        if realized_combustion_load is None
                        else realized_combustion_load
                    )
                ),
                actual_fan_duty=controller.trace_prior_fan_duty if controller.controls_fan else None,
                sample_complete=sample_complete,
                output_source=controller.trace_prior_output_source,
            ),
            end_ms,
        )
        controller.trace_interval_start_ms = end_ms

    def _set_output(
        self,
        applied: AppliedOutput,
        now: float,
        *,
        producing_revision: int | None = None,
        sample_complete: bool = False,
    ) -> None:
        controller = self.state.controller
        coalesce_seed = (
            controller.pulse_frame_result_revision == 0
            and controller.trace_prior_output_source is OutputSource.SEED
            and applied.source is OutputSource.CONTROLLER
        )
        if not coalesce_seed:
            self._trace_complete_applied_interval(
                now,
                sample_complete=sample_complete,
                realized_combustion_load=None,
            )
        self._runner.set_output(applied)
        if coalesce_seed:
            return
        controller.trace_interval_start_ms = int(now * 1_000)
        controller.trace_interval_result_revision = (
            max(0, producing_revision)
            if producing_revision is not None
            else (max(0, controller.pulse_frame_result_revision))
        )
        controller.trace_prior_requested_auger_duty = (
            applied.requested if applied.requested is not None else applied.ratio
        )
        controller.trace_prior_realized_auger_duty = applied.ratio
        controller.trace_prior_output_source = applied.source
        controller.trace_prior_fan_duty = controller.fan_duty
        controller.trace_combustion_load = None
        controller.trace_prior_combustion_load = None

    name = Mode.HOLD
    _model_store = None

    def setup(self):
        import control as _control

        self._trace_recorder = None
        self._trace_session_id = None
        self._trace_cook_id = None
        self._trace_closed = False
        self._trace_warning_active = False
        self._learning_evidence_available = True
        self._trace_pending_model_events = []
        self._clear_trace_session_model_authority()
        self._trace_runner_snapshot_fallback_safe = True
        self._trace_last_update_payload = None
        self._reachability_advisory_key = None
        self._pending_model_observations = {}
        self._pulse_observation_last_frame_key = None
        self._last_ptemp = None
        self._final_refit_done = False
        try:
            self._trace_recorder = ControlTraceRecorder(warning=self._trace_warning)
        except Exception as error:
            self._learning_evidence_available = False
            self._trace_warning(f"Control trace recorder unavailable: {error}")

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
            self._configure_pulse_scheduler()
            self._restore_model()
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
            self._runner.set_output(initial_output)
            controller = self.state.controller
            controller.trace_interval_start_ms = int(initial_output.timestamp * 1_000)
            controller.trace_interval_result_revision = 0
            controller.trace_prior_requested_auger_duty = (
                initial_output.requested if initial_output.requested is not None else initial_output.ratio
            )
            controller.trace_prior_realized_auger_duty = initial_output.ratio
            controller.trace_prior_output_source = initial_output.source
            controller.trace_prior_fan_duty = controller.fan_duty

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
        self._trace_safety(
            SafetyEventType.CONTROLLER_RECONFIGURE,
            now,
            "controller reconfigured",
            InhibitReason.NONE,
        )
        self._trace_complete_applied_interval(now, sample_complete=False, realized_combustion_load=None)
        self._reset_framed_pulse(PulseResetReason.MODE_CHANGE, now, InhibitReason.SAFETY)
        _control.eventLogger.info("Controller reinitialized with updated settings")
        self._reconcile_model_observation_outcomes(now)
        installed_generation = getattr(self._runner, "configuration_revision", lambda: retiring_generation)()
        retained = {
            sequence: pending
            for sequence, pending in self._pending_model_observations.items()
            if pending[2] == installed_generation
        }
        self._retire_runner_evidence_context(retiring_generation)
        controller_state = self.state.controller
        self._trace_session_id = None
        self._trace_cook_id = None
        self._pending_model_observations = retained
        self._pulse_observation_last_frame_key = None
        self._clear_trace_session_model_authority()
        controller_state.trace_result_revision = -1
        controller_state.trace_combustion_load = None
        controller_state.trace_mpc_stale = False
        self._trace_last_update_payload = None
        controller_state.trace_interval_start_ms = None
        controller_state.trace_prior_requested_auger_duty = 0.0
        controller_state.trace_prior_realized_auger_duty = 0.0
        controller_state.trace_prior_fan_duty = None
        controller_state.trace_prior_output_source = None
        controller_state.trace_prior_combustion_load = None
        actual_type = getattr(self._runner, "controller_type", lambda: None)()
        self._controller_name = (
            actual_type.value if isinstance(actual_type, ControllerType) else self.settings["controller"]["selected"]
        )
        self._configure_pulse_scheduler()
        self._trace_runner_snapshot_fallback_safe = not self._runner.runs_async()
        self._restore_model()
        self._configure_fan_authority()
        self._runner_configuration_revision = installed_generation
        self._ensure_trace_session(now)
        self._pending_model_observations = {
            sequence: (observation, self._trace_session_id, generation, records)
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
                maximum_temperature_c=raw["maximum_temperature_c"],
                ambient_c=raw["ambient_c"],
                ambient_source=raw["ambient_source"],
                empty_grill_confirmed=raw["empty_grill_confirmed"],
                pellets_confirmed=raw["pellets_confirmed"],
            )
            self._runner.request_calibration(command)
        except (KeyError, NotImplementedError, TypeError, ValueError) as error:
            self._trace_safety(
                SafetyEventType.SCHEDULER_RESET,
                now,
                f"invalid calibration command: {error}",
                InhibitReason.SAFETY,
            )
            return
        self._calibration_command_high_water = revision
        controller.calibration_command_revision = revision

    def _calibration_cancellation_reason(self, result, now: float) -> str | None:
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

    def _cancel_calibration_probe(
        self, result, reason: str, now: float, ptemp: float, *, notify_runner: bool
    ) -> object:
        """Discard probe credit and latch the producing result's baseline allocation."""
        self._trace_safety(
            SafetyEventType.SCHEDULER_RESET,
            now,
            f"calibration probe cancelled: {reason}",
            InhibitReason.SAFETY,
            result_revision=result.revision,
        )
        self._reset_framed_pulse(
            PulseResetReason.SAFETY,
            now,
            InhibitReason.SAFETY,
            ptemp=ptemp,
            report_feedback=True,
        )
        if notify_runner:
            self._runner.cancel_calibration(reason)
        baseline = result.baseline_allocation
        if baseline is None:
            return result
        return replace(
            result,
            cycle_ratio=baseline.auger_duty,
            fan=None if baseline.fan_duty is None else {"duty": baseline.fan_duty},
            allocation=baseline,
            calibration=replace(result.calibration, active=False, probe_q=0.0),
        )

    def _trace_calibration_result(self, result, now: float) -> None:
        decision = result.calibration
        if decision is None:
            return
        for event in decision.events:
            try:
                event_type = CalibrationEventType(event.kind)
            except ValueError:
                continue
            self._trace_record(
                TraceEventKind.CALIBRATION,
                CalibrationTracePayload(
                    event=event_type,
                    command_revision=decision.command_revision,
                    command_action=decision.command_action,
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

    def on_tick(self, now, ptemp, current_output_status):
        import control as _control

        ctx = self.ctx
        control = self.control
        settings = self.settings
        self._last_ptemp = float(ptemp)
        runner_revision = getattr(self._runner, "configuration_revision", lambda: 0)()
        if runner_revision != self._runner_configuration_revision:
            self._adopt_runner_configuration(now, current_output_status)
            current_output_status = self.grill.get_output_status()
        self._ensure_trace_session(now)

        if control["controller_update"]:
            control["controller_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            self.settings = ctx.store.read_settings()
            settings = self.settings
            self._reset_framed_pulse(
                PulseResetReason.MODE_CHANGE,
                now,
                InhibitReason.SAFETY,
                ptemp=ptemp,
                report_feedback=False,
            )
            self._controller_status = self._runner.reconfigure(settings, control, logger=ctx.control_log)
            if self._controller_status == "Active" and (
                getattr(self._runner, "configuration_revision", lambda: 0)() != self._runner_configuration_revision
            ):
                self._adopt_runner_configuration(now, current_output_status)
                current_output_status = self.grill.get_output_status()
            elif self._controller_status != "Active":
                self._trace_safety(
                    SafetyEventType.CONTROLLER_FALLBACK,
                    now,
                    "controller reconfigure fell back",
                    InhibitReason.SAFETY,
                )

        self._consume_calibration_command(now)

        # Feed the runner every tick so a threaded core always has a fresh temp
        # to solve; for the synchronous runner this just stores the latest temp,
        # so the value read at the gate below is unchanged.
        self._runner.submit(ptemp)
        # Check to see if it's time to update pid and update if needed.
        # A controller that names its own cadence gets it. Otherwise the cadence
        # is the actuation's: a framed scheduler latches one request per frame,
        # so deciding more often than that is discarded work, and a PID asked to
        # decide every tick would integrate at the loop rate its gains were
        # never tuned for.
        self._reconcile_model_observation_outcomes(now)
        controller_interval = self._runner.control_period() or self._pulse_frame_seconds()
        framed_feedback_due = False
        if (now - self.state.controller.cycle_start) > controller_interval:
            result = self._runner.latest()
            cancellation_reason = self._calibration_cancellation_reason(result, now)
            if cancellation_reason is not None:
                result = self._cancel_calibration_probe(
                    result,
                    cancellation_reason,
                    now,
                    ptemp,
                    notify_runner=not cancellation_reason.startswith("operator_"),
                )
            if isinstance(result.diagnostics, MpcTraceDiagnostics):
                self._observe_reachability_advisory(result.diagnostics)
            controller = self.state.controller
            controller.cycle_start = now
            if result.revision > 0 and (
                result.revision > controller.pulse_result_revision or cancellation_reason is not None
            ):
                controller.output = result.cycle_ratio
                controller.pulse_result_revision = result.revision
                controller.pulse_requested_duty = max(0.0, min(1.0, result.cycle_ratio))
                controller.pulse_combustion_load = (
                    result.allocation.normalized_combustion_load if result.allocation is not None else None
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
                controller.pulse_maximum_duty = result.allocation.u_max if result.allocation is not None else 1.0
                controller.pulse_allocator_revision = (
                    result.allocation.allocator_revision if result.allocation is not None else 0
                )
                controller.pulse_allocation_clamp_reasons = (
                    tuple(
                        reason
                        for reason in (result.allocation.auger_clamp_reason, result.allocation.fan_clamp_reason)
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
                controller.pulse_calibration_cancellation_reason = cancellation_reason
                controller.pulse_allocation_evidence_checked = True
                controller.pulse_allocation_result_revision = (
                    result.revision if result.allocation is not None else None
                )
                controller.pulse_requested_fan_duty = result.fan["duty"] if result.fan is not None else None
                if result.fan is not None and controller_fan_authority(settings, control):
                    controller.fan_duty = result.fan["duty"]
                    control["duty_cycle"] = controller.fan_duty
                    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            controller.pulse_stale_command = result.stale_state is ResultStaleState.STALE
            if controller.pulse_stale_command:
                self._reset_framed_pulse(
                    PulseResetReason.SAFETY,
                    now,
                    InhibitReason.SAFETY,
                    report_feedback=True,
                )
            self.state.cycle.ratio = self.state.cycle.raw_ratio = controller.pulse_requested_duty
            self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
            self._trace_update(result, now, controller_interval)
            self._trace_calibration_result(result, now)
            framed_feedback_due = True
            snapshot = self._runner.get_model_snapshot()
            if isinstance(snapshot, dict):
                self._checkpoint_model(snapshot)

        lid_will_open = (
            self.state.target_temp_achieved
            and settings["cycle_data"]["LidOpenDetectEnabled"]
            and ptemp < control["primary_setpoint"] * ((100 - settings["cycle_data"]["LidOpenThreshold"]) / 100)
        )
        if (
            not self.state.controller.pulse_stale_command
            and self.state.manual_override["auger"] < now
            and not self.state.lid.open_detected
        ):
            decision = self._advance_framed_pulse(
                now,
                current_output_status["auger"],
                ptemp=ptemp,
                apply_transition=not lid_will_open,
            )
            if framed_feedback_due:
                self._report_framed_feedback(now, decision.delivered_on_s, ptemp=ptemp)

        # ---- Hold-only fan work on the fresh per-tick ptemp ----
        grill_platform = self.grill

        # Check if target temperature has been achieved before utilizing Smoke Plus Mode
        if ptemp >= control["primary_setpoint"] and not self.state.target_temp_achieved:
            self.state.target_temp_achieved = True

        # Check if a lid open event has occurred only after hold mode has been achieved
        if (
            self.state.target_temp_achieved
            and settings["cycle_data"]["LidOpenDetectEnabled"]
            and ptemp < control["primary_setpoint"] * ((100 - settings["cycle_data"]["LidOpenThreshold"]) / 100)
        ):
            self.state.lid.open_detected = True
            self._trace_safety(SafetyEventType.LID_DETECTED, now, "lid open detected", InhibitReason.LID_OPEN)
            self._trace_complete_applied_interval(
                now,
                sample_complete=False,
                realized_combustion_load=None,
            )
            self._reset_framed_pulse(
                PulseResetReason.LID,
                now,
                InhibitReason.LID_OPEN,
                ptemp=ptemp,
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

        # Clear Lid Open Detect Event, Reset
        if self.state.lid.open_detected and self.ctx.clock.now() > self.state.lid.expires:
            self.state.lid.open_detected = False
            self._trace_safety(SafetyEventType.LID_CLEARED, now, "lid open pause elapsed", InhibitReason.NONE)
            start_fan(grill_platform, settings, control["duty_cycle"])
        if control["lid_open_toggle"]:
            control["lid_open_toggle"] = False
            self.ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            if self.state.lid.open_detected:
                self.state.lid.open_detected = False
                self._trace_safety(SafetyEventType.LID_CLEARED, now, "lid open cleared by operator", InhibitReason.NONE)
            else:
                self.state.lid.open_detected = True
                self._trace_safety(
                    SafetyEventType.LID_DETECTED, now, "lid open set by operator", InhibitReason.LID_OPEN
                )
                self._trace_complete_applied_interval(
                    now,
                    sample_complete=False,
                    realized_combustion_load=None,
                )
                self._reset_framed_pulse(
                    PulseResetReason.LID,
                    now,
                    InhibitReason.LID_OPEN,
                    ptemp=ptemp,
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

        # If PWM Fan Control enabled set duty_cycle based on temperature.
        if (
            settings["platform"]["dc_fan"]
            and control["pwm_control"]
            and not self.state.controller.controls_fan
            and (now - self.state.fan.update_time) > settings["pwm"]["update_time"]
        ):
            self.state.fan.update_time = now
            _duty = hold_duty_cycle(control["primary_setpoint"], ptemp, settings["pwm"])
            if _duty is not None:
                control["duty_cycle"] = _duty
                self.ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")

        self._smoke_plus_fan_tick(now, ptemp, current_output_status)

        if self._trace_recorder is not None:
            try:
                self._trace_recorder.flush_due(time.monotonic_ns() // 1_000_000)
            except Exception as error:
                if not self._trace_warning_active:
                    self._trace_warning(f"Control trace flush failed: {error}")
                    self._trace_warning_active = True

    def _on_manual_output(self, name, output):
        if name != "auger" or self._runner is None:
            return
        self._trace_complete_applied_interval(
            self._last_now,
            sample_complete=(
                self.state.controller.trace_interval_result_revision == 0
                and self.state.controller.trace_prior_output_source is OutputSource.SEED
            ),
            realized_combustion_load=None,
        )
        self._trace_safety(
            SafetyEventType.MANUAL_TAKEOVER,
            self._last_now,
            "manual auger output applied",
            InhibitReason.MANUAL_OVERRIDE,
        )
        self._reset_framed_pulse(
            PulseResetReason.MANUAL,
            self._last_now,
            InhibitReason.MANUAL_OVERRIDE,
            ptemp=self._last_ptemp,
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

    def _on_manual_release(self, name, now):
        if name == "auger":
            self._trace_safety(
                SafetyEventType.MANUAL_RELEASE,
                now,
                "manual auger override expired",
                InhibitReason.NONE,
            )

    def _on_safety_event(self, event, now):
        events = {
            "stop": SafetyEventType.STOP,
            "error": SafetyEventType.ERROR,
            "temperature_guard": SafetyEventType.TEMPERATURE_GUARD,
        }
        event_type = events.get(event)
        if event_type is not None:
            self._ensure_trace_session(now)
            self._trace_safety(event_type, now, event.replace("_", " "), InhibitReason.SAFETY)
            self._reset_framed_pulse(
                PulseResetReason.SAFETY,
                now,
                InhibitReason.SAFETY,
                ptemp=self._last_ptemp,
            )

    def _restore_model(self):
        self._clear_trace_session_model_authority()
        snapshot = self._model_store.load(self._controller_name)
        if snapshot is None:
            return
        import control as _control

        # True means accepted for restore, not adopted -- an asynchronous runner
        # only queues it for its worker thread, so whether it took hold is not
        # knowable from the Hold loop.
        if self._runner.restore_model(snapshot):
            provenance = "restore_submitted" if self._runner.runs_async() else "restored"
            self._set_trace_session_model_authority(snapshot, provenance)
            _control.eventLogger.info(f"Submitted the stored {self._controller_name} model for restore")
            self._trace_model(ModelEventType.RESTORE, "stored model submitted for restore", snapshot)
        else:
            _control.eventLogger.warning(f"Stored {self._controller_name} model was rejected; starting fresh")
            self._trace_model(ModelEventType.REJECT, "stored model rejected for restore", snapshot)

    # check_safety is now a declarative pre_act guard (GUARDS["Hold"]); the base
    # ControlMode default (return False) applies here.

    def status_fragment(self) -> dict:
        status = {
            "lid_open_detected": self.state.lid.open_detected,
            "lid_open_endtime": self.state.lid.expires,
            "actuation_mode": self._actuation_mode.value,
        }
        if self._pulse_scheduler is not None:
            status["pulse"] = {
                "slot_seconds": self._pulse_scheduler.timing.pulse_s,
                "frame_seconds": self._pulse_scheduler.timing.frame_s,
                "result_revision": self.state.controller.pulse_result_revision,
                "stale_command": self.state.controller.pulse_stale_command,
            }
        return status

    def _refit_model(self):
        import control as _control

        try:
            config = self.settings["controller"].get("config", {})
            controller_config = config.get(self._controller_name, {})
            if not (
                controller_config.get("enable_identification") or controller_config.get("enable_online_adaptation")
            ):
                return
        except Exception as error:
            _control.eventLogger.error(f"Model refit failed at cook end: {error}")
            return

        verdict = None
        refit_error = None
        try:
            verdict = self._runner.refit_from_cook()
        except Exception as error:
            refit_error = error
            _control.eventLogger.error(f"Model refit failed at cook end: {error}")
        finally:
            try:
                snapshot = self._runner.get_model_snapshot()
                if snapshot is not None:
                    self._clear_trace_session_model_authority()
                    if refit_error is None:
                        self._trace_model(ModelEventType.REFIT, "model refit completed", snapshot)
                        if getattr(verdict, "accepted", False):
                            self._trace_model(ModelEventType.ADOPT, "refit model adopted", snapshot)
                        else:
                            self._trace_model(ModelEventType.REJECT, "refit model rejected", snapshot)
                    else:
                        self._trace_model(
                            ModelEventType.REFIT, "model checkpoint published after refit failure", snapshot
                        )
                        self._trace_model(ModelEventType.REJECT, f"model refit failed: {refit_error}", snapshot)
                    if isinstance(snapshot, dict):
                        self._checkpoint_model(snapshot)
            except Exception as error:
                _control.eventLogger.error(f"Model refit checkpoint persistence failed: {error}")

    def _refit_model_once(self) -> None:
        if self._final_refit_done:
            return
        self._final_refit_done = True
        self._refit_model()

    def teardown(self, ptemp):
        first_trace_teardown = not self._trace_closed and (
            getattr(self, "_trace_recorder", None) is not None or getattr(self, "_trace_session_id", None) is not None
        )
        now = self.ctx.clock.now()
        if self._pulse_scheduler is not None and self._runner is not None:
            decision = self._advance_framed_pulse(
                now,
                self.grill.get_output_status()["auger"],
                ptemp=ptemp,
                apply_transition=False,
            )
            self._report_framed_feedback(now, decision.delivered_on_s, ptemp=ptemp)
        self._reset_framed_pulse(
            PulseResetReason.MODE_CHANGE,
            now,
            InhibitReason.SAFETY,
            ptemp=ptemp,
        )
        try:
            if self._runner is not None:
                self._runner.stop()
                if getattr(self, "ctx", None) is not None:
                    self._rotate_evidence_sessions_for_reserved_runner_generations(self.ctx.clock.now())
                self._refit_model_once()
        finally:
            self._retire_runner_evidence_context(self._runner_configuration_revision)
            worker = self._persistence_worker
            if worker is not None:
                worker.flush_and_stop()
                self._persistence_worker = None
            if first_trace_teardown:
                self._flush_pending_model_events()
                self._trace_closed = True
                now = self.ctx.clock.now()
                self._trace_complete_applied_interval(
                    now,
                    sample_complete=False,
                    realized_combustion_load=None,
                )

                if self._trace_recorder is not None:
                    try:
                        self._trace_recorder.close()
                    except Exception as error:
                        self._trace_warning(f"Control trace close failed: {error}")
