from collections.abc import Mapping
from math import isfinite

import time
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Literal, cast

from common.controller_model_state import ControllerModelStore
from controller.model_learning.migration import migrate_mpc_learning_authority
from common.modes import Mode
from common.control_trace import (
    ActuationMode,
    AllocationClampReason,
    ControllerType,
    InhibitReason,
    RecorderGapPayload,
    ResultStaleState,
    SafetyEventType,
    TraceEventKind,
)
from common.persistence.protocols import JsonValue
from controller.applied_output import (
    AppliedOutput,
    FrameFeedbackDisposition,
    OutputSource,
    classify_output_source,
    seed_output,
)
from controller.runtime.modes.hold_learning import (
    CalibrationHandoff,
    HoldLearningRuntime,
    parse_model_lifecycle_payload,
)
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
    TraceOutputContext,
    TraceSafetyContext,
    TraceSessionContext,
    TraceUpdateContext,
)
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.model_persistence import ModelPersistenceWorker
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
        return getattr(self, name)


@dataclass(frozen=True, slots=True)
class _HoldTickContext:
    now: float
    ptemp: float
    output_status: _HoldOutputStatus
    trace: ControlTraceSession | None
    active_calibration_reset: bool
    runner_adopted: bool
    calibration_handled: bool


@dataclass(frozen=True, slots=True)
class _HoldRunnerResult:
    result: _runner_mod.ControllerUpdateResult | None
    controller_interval: float
    cancellation_reason: str | None
    calibration: CalibrationHandoff | None
    calibration_handled: bool
    calibration_pending: bool


@dataclass(frozen=True, slots=True)
class _CalibrationCancellation:
    reason: str
    reset_reason: PulseResetReason
    inhibit_reason: InhibitReason
    cancellation_command_revision: int
    cancellation_command_action: Literal[
        "pause",
        "stop",
        "reset-progress",
        "safety-cancel",
    ]
    notify_runner: bool
    terminal_feedback: bool
    report_feedback: bool
    safety_event: SafetyEventType | None
    safety_detail: str
    safety_result_revision: int | None
    calibration_command_revision: int
    calibration_command_action: str
    calibration_command_generation: int
    calibration_stage: str | None
    calibration_completed_stages: tuple[str, ...]


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


class _TeardownPhase(IntEnum):
    ACTIVE = 0
    HARDWARE_OFF = 1
    FRAMED_FINALIZED = 2
    FINISHED = 3


@dataclass(slots=True)
class _FramedDispatchState:
    result: FramedPulseResult
    record_terminal_trace: bool
    scheduler_reset: (
        tuple[
            PulseResetReason,
            float,
            InhibitReason,
            int,
            tuple[SafetyEventType, str, int | None] | None,
        ]
        | None
    ) = None
    delivered_recorded: bool = False
    completion_delivery_index: int = 0
    scheduler_reset_recorded: bool = False
    completion_trace_index: int = 0
    feedback_dispatched: bool = False


@dataclass(slots=True)
class _HoldTeardownState:
    phase: _TeardownPhase = _TeardownPhase.ACTIVE
    now: float | None = None
    ptemp: float | None = None
    auger_on: bool = False
    prior_output_source: OutputSource | None = None
    advance_dispatch: _FramedDispatchState | None = None
    feedback_prepared: bool = False
    feedback: FramedPulseFeedback | None = None
    feedback_dispatched: bool = False
    reset_dispatch: _FramedDispatchState | None = None


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
    _last_ptemp: float | None = None
    _hold_learning: HoldLearningRuntime | None = None
    _teardown: _HoldTeardownState
    _last_tick_s: float | None = None
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
        self.ctx.event_log.warning(
            "MPC learned model predicts the target cannot be reached at maximum safe combustion authority "
            f"(target {report.target_temperature:.1f}, model {report.model_provenance} r{report.model_revision})."
        )

    def _runner_status(self) -> Mapping[str, object]:
        runner = self._runner
        if runner is None:
            return {}
        try:
            status = runner.controller_state()
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
            measured_combustion_load=(feedback.realized_combustion_load if feedback.measured_source else None),
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
            # The frame trace is a record of what the fire already did, never part
            # of driving it. Its payloads are built here, outside the recorder's
            # own guard, so a model the trace refuses would otherwise unwind
            # through the tick and stop the controller mid-cook.
            try:
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
            except Exception as error:
                self.ctx.event_log.warning(f"Framed pulse trace failed: {error}")
        if completion.missing_observation_reason is not None and completion.result_revision > 0:
            self._trace_missing_frame_observation(completion)

    def _deliver_framed_completion(self, completion: FramedPulseCompletion) -> None:
        if completion.observation is not None:
            assert completion.frame_key is not None
            learning = cast(HoldLearningRuntime, self._hold_learning)
            learning.submit_completed_observation(
                completion.frame_key,
                completion.observation,
                completion.applied,
            )
        elif completion.applied is not None:
            runner = cast(_runner_mod.ControllerRunner, self._runner)
            runner.set_output(completion.applied)

    def _resume_framed_dispatch(
        self,
        dispatch: _FramedDispatchState,
    ) -> None:
        result = dispatch.result
        if not dispatch.delivered_recorded:
            self._record_framed_delivery(result.delivered_delta_s)
            dispatch.delivered_recorded = True
        while dispatch.completion_delivery_index < len(result.completions):
            completion = result.completions[dispatch.completion_delivery_index]
            self._deliver_framed_completion(completion)
            dispatch.completion_delivery_index += 1
        scheduler_reset = dispatch.scheduler_reset
        if scheduler_reset is not None and not dispatch.scheduler_reset_recorded:
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
                        detail=(f"framed pulse scheduler reset: {reason.value}"),
                        timestamp_ms=int(now * 1_000),
                    )
                )
            dispatch.scheduler_reset_recorded = True
        while dispatch.completion_trace_index < len(result.completions):
            completion = result.completions[dispatch.completion_trace_index]
            self._trace_framed_completion(
                completion,
                record_terminal_trace=dispatch.record_terminal_trace,
            )
            dispatch.completion_trace_index += 1
        if result.feedback is not None and not dispatch.feedback_dispatched:
            self._dispatch_framed_feedback(result.feedback)
            dispatch.feedback_dispatched = True

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
        self._resume_framed_dispatch(
            _FramedDispatchState(
                result=result,
                record_terminal_trace=record_terminal_trace,
                scheduler_reset=scheduler_reset,
            )
        )

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
                None if self._control_trace is None else self._control_trace.applied_state.output_source
            ),
        )
        self.grill.auger_off()
        self._dispatch_framed_result(
            result,
            record_terminal_trace=(
                cancellation_reason is not None or inhibit in (InhibitReason.SAFETY, InhibitReason.STALE_COMMAND)
            ),
            scheduler_reset=(
                reason,
                now,
                inhibit,
                self.state.controller.pulse_frame_result_revision,
                None if safety_event is None else (safety_event, safety_detail, safety_result_revision),
            ),
        )

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

    def _trace_warning(self, message: str) -> None:
        self.ctx.control_log.warning(message)

    def _trace_type(self) -> ControllerType | None:
        try:
            return ControllerType(self._controller_name)
        except TypeError, ValueError:
            return None

    def _configure_fan_authority(self) -> None:
        """Grant fan ownership only when the configured controller can drive it."""
        wants_fan = self._runner.commands_fan() if self._runner is not None else False
        has_authority = controller_fan_authority(self.settings, self.control)
        if wants_fan and not has_authority:
            self.ctx.event_log.error(
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
        cook_id = self.control.get("cook_id")
        if (
            trace is None
            or runner is None
            or scheduler is None
            or controller is None
            or not isinstance(cook_id, str)
            or not cook_id
            or cook_id != cook_id.strip()
        ):
            return None
        controller_settings = self.settings.get("controller", {})
        configs = controller_settings.get("config", {}) if isinstance(controller_settings, Mapping) else {}
        config = configs.get(self._controller_name, {}) if isinstance(configs, Mapping) else {}
        if not isinstance(config, Mapping):
            config = {}
        globals_settings = self.settings.get("globals", {})
        temperature_unit = str(globals_settings.get("units", "F")) if isinstance(globals_settings, Mapping) else "F"
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
            software_version=(str(versions.get("server", "unknown")) if isinstance(versions, Mapping) else "unknown"),
            build_version=(str(versions.get("build", "unknown")) if isinstance(versions, Mapping) else "unknown"),
            cook_id=cook_id,
            runner_generation=self._runner_configuration_revision,
        )

    def _rotate_evidence_sessions_for_reserved_runner_generations(
        self,
        now: float,
    ) -> None:
        """Release every reserved generation before teardown closes evidence sinks."""
        runner = self._runner
        learning = self._hold_learning
        if runner is None or learning is None:
            return
        current_generation = self._runner_configuration_revision
        learning.reconcile_outcomes(now)
        remaining_generations = learning.retire_generation(current_generation)
        installed_generation = runner.configuration_revision()
        generations = sorted({installed_generation, *remaining_generations})
        for generation in generations:
            if generation == current_generation:
                continue
            trace = self._control_trace
            if trace is not None:
                trace.rotate_identity(runner_snapshot_fallback_safe=not runner.runs_async())
            actual_type = runner.controller_type()
            if isinstance(actual_type, ControllerType):
                self._controller_name = actual_type.value
            self._runner_configuration_revision = generation
            context = self._trace_session_context()
            identity = (
                None if trace is None or context is None else trace.ensure_open(context, timestamp_ms=int(now * 1_000))
            )
            if identity is not None:
                learning.bind_generation(generation)
            learning.reconcile_outcomes(now)

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
        self._teardown = _HoldTeardownState()

        self._control_trace = None
        self._hold_learning = None
        self._runner = None
        self._persistence_worker = None
        learning_evidence_available = True
        self._reachability_advisory_key = None
        self._framed_pulse = FramedPulseRuntime()
        self._last_tick_s = None
        self._last_ptemp = None
        recorder: ControlTraceRecorder | None = None
        try:
            recorder = ControlTraceRecorder(warning=self._trace_warning)
        except Exception as error:
            learning_evidence_available = False
            self._trace_warning(f"Control trace recorder unavailable: {error}")
        self._control_trace = ControlTraceSession(recorder, warning=self._trace_warning)

        start_fan(self.grill, self.settings)
        self.grill.power_on()
        self.ctx.event_log.debug("Power ON, Fan ON, Igniter OFF, Auger OFF")
        # Initialize cycle to minimum ratio.
        self.state.cycle.ratio = self.state.cycle.raw_ratio = 0.0

        self.state.lid.open_detected = False
        self.state.lid.expires = 0
        self.state.target_temp_achieved = False
        model_store = self._model_store or ControllerModelStore(
            reader=self.ctx.store.read_generic_key,
            writer=self.ctx.store.write_generic_key,
            conditional_writer=self.ctx.store.save_model_checkpoint,
        )
        self._model_store = None
        try:
            persistence_worker = ModelPersistenceWorker(
                model_store,
                self.ctx.event_log,
            )
            self._persistence_worker = persistence_worker
        except Exception as error:
            learning_evidence_available = False
            persistence_worker = None
            self._trace_warning(f"Model persistence unavailable: {error}")
        self._controller_name = self.settings["controller"]["selected"]

        # Load Controller Module (i.e. PID)
        self._runner, self._controller_status = _runner_mod.build_runner(
            self.settings, self.control, logger=self.ctx.control_log, event_logger=self.ctx.event_log
        )
        actual_type = getattr(self._runner, "controller_type", lambda: None)() if self._runner is not None else None
        if isinstance(actual_type, ControllerType):
            self._controller_name = actual_type.value
        self._runner_configuration_revision = getattr(self._runner, "configuration_revision", lambda: 0)()
        self._hold_learning = HoldLearningRuntime(
            runner=self._runner,
            model_store=model_store,
            persistence=persistence_worker,
            trace=self._control_trace,
            controller_name=self._controller_name,
            logger=self.ctx.event_log,
            initial_generation=self._runner_configuration_revision,
        )
        if not learning_evidence_available:
            self._hold_learning.mark_evidence_unavailable()

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
                    self._hold_learning.mark_evidence_unavailable()
                    self._trace_warning(f"Model authority migration failed: {error}")
            self._hold_learning.restore_model(timestamp_ms=int(self.ctx.clock.now() * 1_000))
            self._hold_learning.reconcile_activation()
        self._configure_fan_authority()

        self.ctx.event_log.debug(
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
        runner = cast(_runner_mod.ControllerRunner, self._runner)

        retiring_generation = self._runner_configuration_revision
        trace = cast(ControlTraceSession, self._control_trace)
        learning = cast(HoldLearningRuntime, self._hold_learning)
        runtime = cast(FramedPulseRuntime, self._framed_pulse)
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
        controller = self.state.controller
        already_reset = controller.pulse_frame_calibration_status == "cancelled"
        if not already_reset and not self._cancel_active_framed_calibration(
            "reset",
            now=now,
            ptemp=self._last_ptemp,
            reset_reason=PulseResetReason.MODE_CHANGE,
            inhibit_reason=InhibitReason.SAFETY,
            terminal_feedback=False,
            safety_event=None,
            safety_detail="",
            notify_runner=False,
        ):
            self._inhibit_framed_pulse(
                PulseResetReason.MODE_CHANGE,
                now,
                InhibitReason.SAFETY,
                ptemp=self._last_ptemp,
                terminal_feedback=False,
            )
        self.ctx.event_log.info("Controller reinitialized with updated settings")
        learning.reconcile_outcomes(now)
        installed_generation = runner.configuration_revision()
        learning.retire_generation(retiring_generation)
        trace.rotate(runner_snapshot_fallback_safe=not runner.runs_async())
        actual_type = runner.controller_type()
        self._controller_name = (
            actual_type.value if isinstance(actual_type, ControllerType) else self.settings["controller"]["selected"]
        )
        runtime.configure(
            runner.actuation_mode(),
            controller=cast(PulseControllerState, self.state.controller),
            timing=self.grill.auger_timing(),
            now=self.ctx.clock.now(),
            calibration_command_revision=self._calibration_command_high_water,
        )
        self.state.cycle.ratio = self.state.cycle.raw_ratio = 0.0
        self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
        self.grill.auger_off()
        learning.restore_model(
            timestamp_ms=int(now * 1_000),
            controller_name=self._controller_name,
        )
        learning.reconcile_activation()
        self._configure_fan_authority()
        self._runner_configuration_revision = installed_generation
        context = self._trace_session_context()
        identity = (
            None if trace is None or context is None else trace.ensure_open(context, timestamp_ms=int(now * 1_000))
        )
        if identity is not None:
            learning.bind_generation(installed_generation)
        learning.reconcile_outcomes(now)
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
            # Consume the revision anyway. Its content is fixed, so no later
            # tick can build it, and leaving it unconsumed re-attempted the
            # same command every tick for the rest of the cook -- calibration
            # never started, and the only record was a trace event. A operator
            # is told, because "nothing happened" is what they would otherwise
            # see. Re-entering Hold reconsiders it, which is what makes a
            # command rejected for a controller that cannot calibrate usable
            # again once one that can is configured.
            self._calibration_command_high_water = revision
            self.ctx.event_log.error(f"Rejected calibration command revision {revision}: {error}")
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

    def _admit_calibration_cancellation(
        self,
        result: _runner_mod.ControllerUpdateResult,
        now: float,
    ) -> _CalibrationCancellation | None:
        calibration = result.calibration
        if calibration is None or not calibration.active or calibration.probe_q == 0.0:
            return None
        raw = self.control.get("mpc_calibration")
        if self.state.lid.open_detected:
            reason = "lid_open"
        elif self.state.manual_override["auger"] >= now:
            reason = "manual_override"
        elif result.stale_state is ResultStaleState.STALE:
            reason = "stale_result"
        elif self.control.get("controller_update"):
            reason = "reset"
        elif self.control.get("mode") != Mode.HOLD:
            reason = "safety"
        elif isinstance(raw, dict):
            revision = raw.get("revision")
            action = raw.get("action")
            if (
                isinstance(revision, int)
                and revision > calibration.command_revision
                and action in {"pause", "stop", "reset-progress"}
            ):
                reason = f"operator_{action}"
            else:
                return None
        else:
            return None
        operator_action = reason.removeprefix("operator_")
        if (
            reason.startswith("operator_")
            and isinstance(raw, dict)
            and isinstance(raw.get("revision"), int)
            and operator_action in {"pause", "stop", "reset-progress"}
        ):
            cancellation_command_revision = raw["revision"]
            cancellation_command_action = cast(
                Literal["pause", "stop", "reset-progress"],
                operator_action,
            )
        else:
            cancellation_command_revision = 0
            cancellation_command_action = "safety-cancel"
        return _CalibrationCancellation(
            reason=reason,
            reset_reason=PulseResetReason.SAFETY,
            inhibit_reason=InhibitReason.SAFETY,
            cancellation_command_revision=cancellation_command_revision,
            cancellation_command_action=cancellation_command_action,
            notify_runner=not reason.startswith("operator_"),
            terminal_feedback=True,
            report_feedback=True,
            safety_event=SafetyEventType.SCHEDULER_RESET,
            safety_detail=f"calibration probe cancelled: {reason}",
            safety_result_revision=result.revision,
            calibration_command_revision=calibration.command_revision,
            calibration_command_action=calibration.command_action,
            calibration_command_generation=calibration.command_generation,
            calibration_stage=calibration.stage,
            calibration_completed_stages=tuple(calibration.completed_stages),
        )

    def _result_matches_cancelled_frame(
        self,
        result: _runner_mod.ControllerUpdateResult,
    ) -> bool:
        calibration = result.calibration
        controller = self.state.controller
        return (
            calibration is not None
            and calibration.active
            and calibration.probe_q != 0.0
            and controller.pulse_frame_calibration_status == "cancelled"
            and controller.pulse_frame_calibration_cancellation_reason is not None
            and controller.pulse_frame_calibration_command_revision == calibration.command_revision
            and controller.pulse_frame_calibration_command_action == calibration.command_action
            and controller.pulse_frame_calibration_command_generation == calibration.command_generation
        )

    def _route_calibration_cancellation(
        self,
        cancellation: _CalibrationCancellation,
        *,
        now: float,
        ptemp: float | None,
    ) -> None:
        self._inhibit_framed_pulse(
            cancellation.reset_reason,
            now,
            cancellation.inhibit_reason,
            ptemp=ptemp,
            terminal_feedback=cancellation.terminal_feedback,
            report_feedback=cancellation.report_feedback,
            cancellation_reason=cancellation.reason,
            cancellation_command_revision=(cancellation.cancellation_command_revision),
            cancellation_command_action=(cancellation.cancellation_command_action),
            safety_event=cancellation.safety_event,
            safety_detail=cancellation.safety_detail,
            safety_result_revision=cancellation.safety_result_revision,
        )
        if cancellation.notify_runner:
            self._runner.cancel_calibration(cancellation.reason)

    def _result_matches_cancelled_projection(
        self,
        result: _runner_mod.ControllerUpdateResult,
    ) -> bool:
        calibration = result.calibration
        controller = self.state.controller
        return (
            calibration is not None
            and calibration.active
            and calibration.probe_q != 0.0
            and controller.pulse_calibration_cancellation_reason is not None
            and controller.pulse_calibration_command_revision == calibration.command_revision
            and controller.pulse_calibration_command_action == calibration.command_action
            and controller.pulse_calibration_command_generation == calibration.command_generation
        )

    def _cancel_active_framed_calibration(
        self,
        reason: str,
        *,
        now: float,
        ptemp: float | None,
        reset_reason: PulseResetReason,
        inhibit_reason: InhibitReason,
        terminal_feedback: bool,
        safety_event: SafetyEventType | None,
        safety_detail: str,
        notify_runner: bool = True,
        cancellation_command_revision: int = 0,
        cancellation_command_action: Literal[
            "pause",
            "stop",
            "reset-progress",
            "safety-cancel",
        ] = "safety-cancel",
    ) -> bool:
        if self._runner is None:
            return False
        controller = self.state.controller
        probe_load = controller.pulse_frame_calibration_probe_load
        if (
            controller.pulse_frame_calibration_status != "active"
            or isinstance(probe_load, bool)
            or not isinstance(probe_load, int | float)
            or probe_load == 0.0
        ):
            return False
        self._route_calibration_cancellation(
            _CalibrationCancellation(
                reason=reason,
                reset_reason=reset_reason,
                inhibit_reason=inhibit_reason,
                cancellation_command_revision=cancellation_command_revision,
                cancellation_command_action=cancellation_command_action,
                notify_runner=notify_runner,
                terminal_feedback=terminal_feedback,
                report_feedback=False,
                safety_event=safety_event,
                safety_detail=safety_detail,
                safety_result_revision=(controller.pulse_frame_result_revision),
                calibration_command_revision=(controller.pulse_frame_calibration_command_revision),
                calibration_command_action=(controller.pulse_frame_calibration_command_action),
                calibration_command_generation=(controller.pulse_frame_calibration_command_generation),
                calibration_stage=controller.pulse_frame_calibration_stage,
                calibration_completed_stages=tuple(controller.pulse_frame_calibration_completed_stages),
            ),
            now=now,
            ptemp=ptemp,
        )
        return True

    def _cancel_newer_operator_command(
        self,
        *,
        now: float,
        ptemp: float,
    ) -> bool:
        controller = self.state.controller
        control = self.control
        if control is None:
            return False
        raw = control.get("mpc_calibration")
        if not isinstance(raw, dict):
            return False
        revision = raw.get("revision")
        action = raw.get("action")
        if (
            not isinstance(revision, int)
            or revision <= controller.pulse_frame_calibration_command_revision
            or action not in {"pause", "stop", "reset-progress"}
        ):
            return False
        return self._cancel_active_framed_calibration(
            f"operator_{action}",
            now=now,
            ptemp=ptemp,
            reset_reason=PulseResetReason.SAFETY,
            inhibit_reason=InhibitReason.SAFETY,
            terminal_feedback=True,
            safety_event=SafetyEventType.SCHEDULER_RESET,
            safety_detail=f"calibration probe cancelled: operator_{action}",
            notify_runner=False,
            cancellation_command_revision=revision,
            cancellation_command_action=cast(
                Literal["pause", "stop", "reset-progress"],
                action,
            ),
        )

    @staticmethod
    def _without_calibration_probe(
        result: _runner_mod.ControllerUpdateResult,
    ) -> _runner_mod.ControllerUpdateResult:
        """Return the same completed result with its exact baseline allocation."""
        baseline = result.baseline_allocation
        calibration = result.calibration
        if calibration is None:
            return result
        if baseline is None:
            return replace(
                result,
                cycle_ratio=0.0,
                fan=None,
                allocation=None,
                calibration=replace(
                    calibration,
                    active=False,
                    probe_q=0.0,
                ),
            )
        return replace(
            result,
            cycle_ratio=baseline.auger_duty,
            fan=None if baseline.fan_duty is None else {"duty": baseline.fan_duty},
            allocation=baseline,
            calibration=replace(calibration, active=False, probe_q=0.0),
        )

    def _cancel_calibration_probe(
        self,
        result: _runner_mod.ControllerUpdateResult,
        cancellation: _CalibrationCancellation,
        now: float,
        ptemp: float,
    ) -> _runner_mod.ControllerUpdateResult:
        """Route one admitted cancellation before restoring the exact baseline."""
        self._route_calibration_cancellation(
            cancellation,
            now=now,
            ptemp=ptemp,
        )
        return self._without_calibration_probe(result)

    def on_tick(
        self,
        now: float,
        ptemp: float,
        current_output_status: Mapping[str, bool | int | float],
    ) -> None:
        self._last_tick_s = now
        context = self._adopt_tick_configuration_and_session(
            now,
            ptemp,
            current_output_status,
        )
        context = self._release_expired_manual_auger(context)
        calibration_handled = self._publish_safety_ceiling_and_consume_calibration(context)
        context = replace(
            context,
            calibration_handled=(context.calibration_handled or calibration_handled),
        )
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
        learning = self._hold_learning
        if previous_identity is None and identity is not None and learning is not None:
            learning.bind_generation(self._runner_configuration_revision)
        if learning is not None:
            learning.reconcile_activation()
            learning.drain_activation_events()
        active_calibration_reset = False

        if control["controller_update"]:
            active_calibration_reset = self._cancel_active_framed_calibration(
                "reset",
                now=now,
                ptemp=ptemp,
                reset_reason=PulseResetReason.MODE_CHANGE,
                inhibit_reason=InhibitReason.SAFETY,
                terminal_feedback=False,
                safety_event=None,
                safety_detail="",
            )
            control["controller_update"] = False
            ctx.store.write_control_snapshot(control, origin="control")
            self.settings = ctx.store.read_settings()
            if not active_calibration_reset:
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
                getattr(self._runner, "configuration_revision", lambda: 0)() != self._runner_configuration_revision
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
            calibration_handled=(
                self.state.controller.pulse_frame_calibration_status == "cancelled"
                and self.state.controller.pulse_calibration_status != "inactive"
            ),
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
    ) -> bool:
        self._retarget_running_controller()
        self._publish_safety_ceiling(context.now)
        calibration_handled = self._cancel_newer_operator_command(
            now=context.now,
            ptemp=context.ptemp,
        )
        self._consume_calibration_command(context.now)
        return calibration_handled

    def _submit_obtain_and_handle_calibration_cancellation(
        self,
        context: _HoldTickContext,
    ) -> _HoldRunnerResult:
        # Feed the runner every tick so a threaded core always has a fresh temp
        # to solve; for the synchronous runner this just stores the latest temp,
        # so the value read at the gate below is unchanged.
        runner = cast(_runner_mod.ControllerRunner, self._runner)
        runner.submit(context.ptemp)
        learning = cast(HoldLearningRuntime, self._hold_learning)
        learning.reconcile_outcomes(context.now)
        runtime = cast(FramedPulseRuntime, self._framed_pulse)
        controller_interval = runner.control_period() or runtime.frame_seconds
        if (context.now - self.state.controller.cycle_start) <= controller_interval:
            return _HoldRunnerResult(
                result=None,
                calibration_pending=context.calibration_handled,
                controller_interval=controller_interval,
                cancellation_reason=None,
                calibration=None,
                calibration_handled=context.calibration_handled,
            )
        result = runner.latest()
        learning.drain_activation_events()
        cancellation: _CalibrationCancellation | None = None
        matched_cancelled_identity = self._result_matches_cancelled_frame(
            result
        ) or self._result_matches_cancelled_projection(result)
        calibration = result.calibration
        inactive_acknowledgement = context.calibration_handled and (calibration is None or not calibration.active)
        calibration_handled = context.active_calibration_reset or inactive_acknowledgement or matched_cancelled_identity
        calibration_pending = matched_cancelled_identity
        if calibration_handled:
            result = self._without_calibration_probe(result)
        else:
            cancellation = self._admit_calibration_cancellation(
                result,
                context.now,
            )
            if cancellation is not None:
                result = self._cancel_calibration_probe(
                    result,
                    cancellation,
                    context.now,
                    context.ptemp,
                )
        cancellation_reason = None if cancellation is None else cancellation.reason
        calibration = (
            learning.handoff_calibration(
                result.calibration,
                result_revision=result.revision,
                timestamp_ms=int(context.now * 1_000),
            )
            if learning is not None
            else None
        )
        if isinstance(result.diagnostics, MpcTraceDiagnostics):
            self._observe_reachability_advisory(result.diagnostics)
        return _HoldRunnerResult(
            result=result,
            calibration_pending=calibration_pending,
            controller_interval=controller_interval,
            cancellation_reason=cancellation_reason,
            calibration=calibration,
            calibration_handled=calibration_handled,
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
        calibration = runner_result.calibration
        framed_feedback_due = result is not None
        if result is not None:
            controller = self.state.controller
            controller.cycle_start = context.now
            if result.revision > 0 and (
                result.revision > controller.pulse_result_revision
                or runner_result.cancellation_reason is not None
                or runner_result.calibration_handled
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
                controller.pulse_calibration_probe_load = 0.0 if calibration is None else calibration.probe_load
                controller.pulse_calibration_stage = None if calibration is None else calibration.stage
                controller.pulse_calibration_completed_stages = (
                    () if calibration is None else calibration.completed_stages
                )
                controller.pulse_maximum_duty = result.allocation.u_max if result.allocation is not None else 1.0
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
                    0 if calibration is None else calibration.command_revision
                )
                controller.pulse_calibration_command_action = (
                    "none" if calibration is None else calibration.command_action
                )
                controller.pulse_calibration_command_generation = (
                    0 if calibration is None else calibration.command_generation
                )
                if runner_result.cancellation_reason is not None:
                    controller.pulse_calibration_cancellation_reason = runner_result.cancellation_reason
                elif not runner_result.calibration_handled:
                    controller.pulse_calibration_cancellation_reason = (
                        None if calibration is None else calibration.reason
                    )
                controller.pulse_calibration_status = "inactive" if calibration is None else calibration.status
                controller.pulse_allocation_evidence_checked = True
                controller.pulse_allocation_result_revision = result.revision if result.allocation is not None else None
                controller.pulse_requested_fan_duty = result.fan["duty"] if result.fan is not None else None
                if result.fan is not None and controller_fan_authority(settings, control):
                    controller.fan_duty = result.fan["duty"]
                    control["duty_cycle"] = controller.fan_duty
                    ctx.store.write_control_snapshot(control, origin="control")
            controller.pulse_stale_command = result.stale_state is ResultStaleState.STALE

        lid_will_open = (
            self.state.target_temp_achieved
            and settings["cycle_data"]["LidOpenDetectEnabled"]
            and context.ptemp < control["primary_setpoint"] * ((100 - settings["cycle_data"]["LidOpenThreshold"]) / 100)
        )
        permit_framed_pulse = (
            not self.state.controller.pulse_stale_command
            and self.state.manual_override["auger"] < context.now
            and not self.state.lid.open_detected
            and not runner_result.calibration_pending
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
        learning = self._hold_learning
        if result is not None:
            controller = self.state.controller
            control = self.control
            if (
                controller.pulse_stale_command
                and runner_result.cancellation_reason is None
                and not runner_result.calibration_handled
            ):
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
                    parse_model_lifecycle_payload(result.diagnostics.model_lifecycle)
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
            snapshot = self._runner.get_model_snapshot()
            if isinstance(snapshot, dict) and learning is not None:
                learning.submit_online_checkpoint(snapshot)
        if runner_result.calibration_pending:
            return _HoldFramedPulse(
                result=None,
                lid_will_open=inhibition.lid_will_open,
                report_feedback=inhibition.framed_feedback_due,
            )
        if not inhibition.permit_framed_pulse:
            return _HoldFramedPulse(
                result=None,
                lid_will_open=inhibition.lid_will_open,
                report_feedback=inhibition.framed_feedback_due,
            )
        runtime = self._framed_pulse
        if runtime is None:
            raise RuntimeError("Hold framed pulse runtime is unavailable")
        prior_output_source = None if context.trace is None else context.trace.applied_state.output_source
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
            if trace is not None and self.state.controller.pulse_frame_result_revision > 0:
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
                    prior_output_source=(None if trace is None else trace.applied_state.output_source),
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
            if not self._cancel_active_framed_calibration(
                "lid_open",
                now=now,
                ptemp=ptemp,
                reset_reason=PulseResetReason.LID,
                inhibit_reason=InhibitReason.LID_OPEN,
                terminal_feedback=True,
                safety_event=SafetyEventType.LID_DETECTED,
                safety_detail="lid open detected",
            ):
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
                if not self._cancel_active_framed_calibration(
                    "lid_open",
                    now=now,
                    ptemp=ptemp,
                    reset_reason=PulseResetReason.LID,
                    inhibit_reason=InhibitReason.LID_OPEN,
                    terminal_feedback=True,
                    safety_event=SafetyEventType.LID_DETECTED,
                    safety_detail="lid open set by operator",
                ):
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
        if not self._cancel_active_framed_calibration(
            "manual_override",
            now=self._last_now,
            ptemp=self._last_ptemp,
            reset_reason=PulseResetReason.MANUAL,
            inhibit_reason=InhibitReason.MANUAL_OVERRIDE,
            terminal_feedback=False,
            safety_event=SafetyEventType.MANUAL_TAKEOVER,
            safety_detail="manual auger output applied",
        ):
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
        calibration_cancelled = self._runner is not None and self._cancel_active_framed_calibration(
            "manual_override",
            now=now,
            ptemp=self._last_ptemp,
            reset_reason=PulseResetReason.MANUAL,
            inhibit_reason=InhibitReason.MANUAL_OVERRIDE,
            terminal_feedback=False,
            safety_event=SafetyEventType.MANUAL_RELEASE,
            safety_detail="manual auger override expired",
        )
        if self.grill.get_output_status()["auger"]:
            self.grill.auger_off()
        self.state.manual_override["auger"] = 0
        trace = self._control_trace
        if trace is not None and not calibration_cancelled:
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
                None if trace is None or context is None else trace.ensure_open(context, timestamp_ms=int(now * 1_000))
            )
            learning = self._hold_learning
            if previous_identity is None and identity is not None and learning is not None:
                learning.bind_generation(self._runner_configuration_revision)
            if not self._cancel_active_framed_calibration(
                "safety",
                now=now,
                ptemp=self._last_ptemp,
                reset_reason=PulseResetReason.SAFETY,
                inhibit_reason=InhibitReason.SAFETY,
                terminal_feedback=True,
                safety_event=event_type,
                safety_detail=event.replace("_", " "),
            ):
                self._inhibit_framed_pulse(
                    PulseResetReason.SAFETY,
                    now,
                    InhibitReason.SAFETY,
                    ptemp=self._last_ptemp,
                    terminal_feedback=True,
                    safety_event=event_type,
                    safety_detail=event.replace("_", " "),
                )

    # check_safety is now a declarative pre_act guard (GUARDS["Hold"]); the base
    # ControlMode default (return False) applies here.

    def status_fragment(self) -> dict:
        status = {
            "lid_open_detected": self.state.lid.open_detected,
            "lid_open_endtime": self.state.lid.expires,
            "actuation_mode": ActuationMode.FRAMED_PULSE.value,
        }
        learning_runtime = self._hold_learning
        if learning_runtime is not None:
            status.update(learning_runtime.status_fragment())
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

    def teardown(self, ptemp):
        teardown = self._teardown
        if teardown.phase >= _TeardownPhase.FINISHED:
            return
        trace = self._control_trace
        if teardown.now is None:
            clock_now = self.ctx.clock.now()
            teardown.now = max(
                clock_now,
                (clock_now if self._last_tick_s is None else self._last_tick_s),
            )
            teardown.ptemp = ptemp
        now = teardown.now
        runtime = self._framed_pulse
        runner = self._runner
        learning = self._hold_learning
        if teardown.phase < _TeardownPhase.HARDWARE_OFF:
            teardown.auger_on = bool(self.grill.get_output_status()["auger"])
            self.grill.auger_off()
            self.grill.fan_off()
            self.grill.igniter_off()
            self.grill.power_off()
            teardown.phase = _TeardownPhase.HARDWARE_OFF
        if teardown.phase < _TeardownPhase.FRAMED_FINALIZED:
            if runtime is not None and runtime.scheduler is not None and runner is not None:
                advance_dispatch = teardown.advance_dispatch
                if advance_dispatch is None:
                    teardown.prior_output_source = None if trace is None else trace.applied_state.output_source
                    advance_dispatch = _FramedDispatchState(
                        result=runtime.advance(
                            now,
                            teardown.auger_on,
                            sample=self._framed_sample(teardown.ptemp),
                            prior_output_source=(teardown.prior_output_source),
                        ),
                        record_terminal_trace=False,
                    )
                    teardown.advance_dispatch = advance_dispatch
                self._resume_framed_dispatch(advance_dispatch)
                pulse_result = advance_dispatch.result
                if not teardown.feedback_prepared:
                    teardown.feedback = runtime.report_feedback(
                        now,
                        pulse_result.decision.delivered_on_s,
                        source=classify_output_source(
                            lid_open=self.state.lid.open_detected,
                            manual_override_active=(self.state.manual_override["auger"] >= now),
                        ),
                        prior_output_source=(teardown.prior_output_source),
                        dispatch=not bool(pulse_result.decision.completed_frames),
                    )
                    teardown.feedback_prepared = True
                feedback = teardown.feedback
                if feedback is not None and not teardown.feedback_dispatched:
                    self._dispatch_framed_feedback(feedback)
                    teardown.feedback_dispatched = True
            if runtime is not None and runtime.scheduler is not None:
                reset_dispatch = teardown.reset_dispatch
                if reset_dispatch is None:
                    prior_output_source = (
                        teardown.prior_output_source
                        if teardown.advance_dispatch is not None
                        else (None if trace is None else trace.applied_state.output_source)
                    )
                    source = classify_output_source(
                        lid_open=self.state.lid.open_detected,
                        manual_override_active=(self.state.manual_override["auger"] >= now),
                    )
                    reset_dispatch = _FramedDispatchState(
                        result=runtime.reset(
                            PulseResetReason.MODE_CHANGE,
                            now,
                            InhibitReason.SAFETY,
                            actual_auger_on=self.grill.get_output_status()["auger"],
                            sample=self._framed_sample(teardown.ptemp),
                            terminal_feedback=True,
                            feedback_source=source,
                            prior_output_source=prior_output_source,
                        ),
                        record_terminal_trace=True,
                        scheduler_reset=(
                            PulseResetReason.MODE_CHANGE,
                            now,
                            InhibitReason.SAFETY,
                            self.state.controller.pulse_frame_result_revision,
                            None,
                        ),
                    )
                    teardown.reset_dispatch = reset_dispatch
                self._resume_framed_dispatch(reset_dispatch)
            teardown.phase = _TeardownPhase.FRAMED_FINALIZED
        stop_error: Exception | None = None
        stopped: bool | None = None
        try:
            if runner is not None:
                try:
                    stopped = runner.stop_for_refit()
                except Exception as error:
                    stop_error = error
                if stop_error is None:
                    if stopped is False:
                        self._trace_warning("Controller worker did not stop; final checkpoint was not queued")
                    elif learning is not None:
                        refit = learning.refit_once(cast(Mapping[str, JsonValue], self.settings))
                        learning.publish_final_checkpoint_once(
                            refit,
                            timestamp_ms=int(self.ctx.clock.now() * 1_000),
                        )
                if stop_error is None:
                    self._rotate_evidence_sessions_for_reserved_runner_generations(self.ctx.clock.now())
        finally:
            try:
                if trace is not None and not trace.status.closed:
                    trace.record_applied_interval(
                        TraceAppliedIntervalContext(
                            timestamp_ms=int(self.ctx.clock.now() * 1_000),
                            sample_complete=False,
                            realized_combustion_load=None,
                            controls_fan=self.state.controller.controls_fan,
                        )
                    )
            finally:
                if learning is not None:
                    learning.finish_teardown(generation=self._runner_configuration_revision)
                else:
                    persistence = self._persistence_worker
                    if persistence is not None:
                        try:
                            persistence.flush_and_stop()
                        except Exception as error:
                            self._trace_warning(f"Model persistence close failed: {error}")
                    if trace is not None:
                        trace.close()
                    if runner is not None:
                        try:
                            runner.finish_teardown()
                        except Exception as error:
                            self._trace_warning(f"Controller teardown close failed: {error}")
                teardown.phase = _TeardownPhase.FINISHED
        if stop_error is not None:
            raise stop_error
