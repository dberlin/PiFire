import time
import uuid
from dataclasses import replace

from common.common import WriteKind
from common.controller_model_state import ControllerModelStore
from common.modes import Mode
from common.control_trace import (
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FramedPulseFramePayload,
    FixedCycleFramePayload,
    InhibitReason,
    ModelEventPayload,
    ModelEventType,
    MpcUpdatePayload,
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
from controller.runtime.logic.pulse import (
    PulseDecision,
    PulseFrameResult,
    PulseReason,
    PulseResetReason,
    PulseScheduler,
)
from controller.runtime.logic.cycle import hold_initial_cycle
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.base import MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.model_promotion import ReachabilityState
from controller.runtime.logic.fan import (
    controller_fan_authority,
    fan_assist_times,
    smoke_plus_max_ratio,
    start_fan,
)
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
    `commands_fan()`), lid-open detection, and the PWM-duty-from-temp-profile /
    fan-assist-PID fan control paths.

    The pre-loop and in-loop flameout checks are DECLARATIVE guard edges
    (GUARDS["Hold"] in transitions.py, fired by evaluate_phase at base.run's
    pre_loop/pre_act points). setup_safety() survives only to abort to 'Inactive'
    if the runner failed to build (controller module load error); there is no
    check_safety override.

    Per-tick, on_tick() first handles the `controller_update` reconfigure
    request, then runs the Hold-specific controller sub-block (submit the
    fresh per-tick ptemp to the runner, normalize its output into a cycle
    ratio + optional fan command, route an MPC fan command into
    `control['duty_cycle']` when one arrives, clamp to u_min/u_max, and
    decide fan_assist), then the shared (non-Hold) auger-cycle toggle via
    `_auger_cycle_tick` (Hold overrides `_on_auger_on` to also recompute
    OnTime/OffTime/CycleTime and publish MQTT PID info -- the shared helper
    itself is untouched). It then runs the Hold-only fan work on the same
    fresh ptemp: the target_temp_achieved latch, lid-open detect/clear,
    PWM-duty-from-temp-profile (gated `not self.state.controller.controls_fan`),
    and fan-assist-PID parts, then delegates to the shared
    `_smoke_plus_fan_tick` helper (gated on target_temp_achieved for Hold,
    unlike Smoke which always runs it).

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
    _actuation_mode: ActuationMode = ActuationMode.FIXED_CYCLE
    _pulse_scheduler: PulseScheduler | None = None
    _reachability_advisory_key: tuple[float, int, str] | None = None

    def _framed_pulse(self) -> bool:
        return self._actuation_mode is ActuationMode.FRAMED_PULSE

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

    def _configure_actuation_mode(self) -> None:
        self._actuation_mode = self._runner.actuation_mode()
        controller = self.state.controller
        if self._framed_pulse():
            self._pulse_scheduler = PulseScheduler(self.grill.auger_timing())
            self.state.cycle.ratio = self.state.cycle.raw_ratio = 0.0
            self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
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
            controller.pulse_frame_stale_command = False
            controller.pulse_feedback_start_s = self.ctx.clock.now()
            controller.pulse_feedback_delivered_on_s = 0.0
            controller.pulse_metrics_delivered_on_s = 0.0
            self.grill.auger_off()
            return
        self._pulse_scheduler = None
        if self.state.cycle.cycle_time == 0.0:
            cycle = hold_initial_cycle(self.settings["cycle_data"])
            self.state.cycle.on_time = cycle.on_time
            self.state.cycle.off_time = cycle.off_time
            self.state.cycle.cycle_time = cycle.cycle_time
            self.state.cycle.ratio = self.state.cycle.raw_ratio = cycle.cycle_ratio

    def _latch_pulse_frame(self) -> None:
        controller = self.state.controller
        controller.pulse_frame_result_revision = max(0, controller.pulse_result_revision)
        controller.pulse_frame_combustion_load = controller.pulse_combustion_load
        controller.pulse_frame_requested_auger_duty = controller.pulse_requested_duty
        controller.pulse_frame_requested_fan_duty = controller.pulse_requested_fan_duty
        controller.pulse_frame_applied_fan_duty = controller.fan_duty if controller.controls_fan else None
        controller.pulse_frame_stale_command = controller.pulse_stale_command

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
        self, now: float, actual_auger_on: bool, *, apply_transition: bool = True
    ) -> PulseDecision:
        scheduler = self._pulse_scheduler
        if scheduler is None:
            raise RuntimeError("framed actuation requires a pulse scheduler")
        previous_frame_revision = self.state.controller.pulse_frame_result_revision
        decision = scheduler.advance(self.state.controller.pulse_requested_duty, now, actual_auger_on)
        self._record_pulse_delivery(decision.delivered_on_s)
        for frame in decision.completed_frames:
            self._trace_pulse_frame(frame, InhibitReason.NONE, self.state.controller.pulse_frame_result_revision)
        if decision.reason in (PulseReason.FRAME_STARTED, PulseReason.FRAME_SKIPPED, PulseReason.RESET):
            self._latch_pulse_frame()
            transition_at_s: float | None = None
            delivered_at_transition_s = decision.delivered_on_s
            if decision.completed_frames:
                transition_at_s = decision.completed_frames[-1].ended_at_s
                delivered_at_transition_s -= decision.frame_delivered_on_s
            elif previous_frame_revision == 0 and self.state.controller.pulse_frame_result_revision > 0:
                transition_at_s = now
            if transition_at_s is not None:
                self._report_framed_feedback(
                    transition_at_s,
                    delivered_at_transition_s,
                    completed_revision=previous_frame_revision,
                )
        if apply_transition and decision.transition is not None:
            if decision.transition.command_on:
                self.grill.auger_on()
            else:
                self.grill.auger_off()
        return decision

    def _reset_framed_pulse(self, reason: PulseResetReason, now: float, inhibit: InhibitReason) -> None:
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
        for completed in decision.completed_frames:
            self._trace_pulse_frame(completed, inhibit, self.state.controller.pulse_frame_result_revision)
        if decision.reason in (PulseReason.FRAME_STARTED, PulseReason.FRAME_SKIPPED, PulseReason.RESET):
            self._latch_pulse_frame()
        frame = scheduler.reset(reason)
        if frame is not None:
            self._trace_pulse_frame(frame, inhibit, self.state.controller.pulse_frame_result_revision)
        controller = self.state.controller
        controller.pulse_metrics_delivered_on_s = decision.delivered_on_s
        controller.pulse_feedback_start_s = now
        controller.pulse_feedback_delivered_on_s = decision.delivered_on_s
        self.grill.auger_off()

    def _report_framed_feedback(
        self, now: float, delivered_on_s: float, *, completed_revision: int | None = None
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
        applied = AppliedOutput(
            ratio=realized_duty,
            source=classify_output_source(
                lid_open=self.state.lid.open_detected,
                manual_override_active=self.state.manual_override["auger"] >= now,
                fan_assist_active=False,
            ),
            timestamp=now,
            requested=controller.pulse_frame_requested_auger_duty,
        )
        inverse_combustion_load = max(0.0, min(1.0, realized_duty / controller.pulse_maximum_duty))
        measured_source = controller.trace_prior_output_source in (OutputSource.CONTROLLER, OutputSource.FAN_ASSIST)
        sample_complete = controller.trace_prior_output_source is OutputSource.SEED or measured_source
        if measured_source:
            controller.trace_interval_result_revision = (
                controller.pulse_frame_result_revision if completed_revision is None else completed_revision
            )
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
            producing_revision=controller.pulse_frame_result_revision,
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

    def _ensure_trace_session(self, now: float) -> None:
        if self._trace_session_id is not None:
            return
        cook_id = self.state.metrics.get("id")
        controller = self._trace_type()
        if not isinstance(cook_id, str) or not cook_id or controller is None:
            return
        config = self.settings["controller"].get("config", {}).get(self._controller_name, {})
        snapshot = self._trace_session_model_snapshot
        provenance = self._trace_session_model_provenance
        if snapshot is None and self._trace_runner_snapshot_fallback_safe:
            snapshot = self._runner.get_model_snapshot() if self._runner is not None else None
            provenance = "persisted"
        model_revision = snapshot.get("revision") if isinstance(snapshot, dict) else None
        if isinstance(model_revision, bool) or not isinstance(model_revision, int) or model_revision < 0:
            model_revision = None
            provenance = None
        cycle_data = self.settings["cycle_data"]
        scheduler = self._pulse_scheduler
        framed = self._framed_pulse()
        payload = SessionPayload(
            controller=controller,
            controller_config=self._trace_settings(config),
            temperature_unit=str(self.settings["globals"]["units"]),
            control_period_seconds=float(
                self._runner.control_period()
                or (scheduler.timing.frame_s if framed and scheduler is not None else cycle_data["HoldCycleTime"])
            ),
            model_revision=model_revision,
            model_provenance=provenance if model_revision is not None else None,
            u_min=None if framed else float(cycle_data["u_min"]),
            u_max=None if framed else float(cycle_data["u_max"]),
            hold_cycle_seconds=None if framed else float(cycle_data["HoldCycleTime"]),
            pulse_slot_seconds=float(scheduler.timing.pulse_s) if framed and scheduler is not None else None,
            pulse_frame_seconds=float(scheduler.timing.frame_s) if framed and scheduler is not None else None,
            fan_authority=self.state.controller.controls_fan,
            fan_pwm_capable=bool(self.settings["platform"]["dc_fan"]),
            fan_min_duty=float(config.get("fan_min_pct", 0.0)),
            fan_max_duty=float(config.get("fan_max_pct", 100.0)),
            setpoint=float(self.control["primary_setpoint"]),
            ambient_temperature=float(config.get("T_amb", 0.0)),
            software_version=str(self.settings.get("versions", {}).get("server", "unknown")),
            build_version=str(self.settings.get("versions", {}).get("build", "unknown")),
        )
        self._trace_session_id = str(uuid.uuid4())
        self._trace_cook_id = cook_id
        if not self._trace_record(TraceEventKind.SESSION, payload, int(now * 1_000)):
            self._trace_session_id = None
            self._trace_cook_id = None
            return
        if self._trace_pending_model_events is not None:
            for pending_payload, pending_ts_ms in self._trace_pending_model_events:
                self._trace_record(TraceEventKind.MODEL_EVENT, pending_payload, pending_ts_ms)
            self._trace_pending_model_events.clear()

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
        if self._trace_session_id is None:
            if self._trace_pending_model_events is not None:
                self._trace_pending_model_events.append((payload, now_ms))
            return
        self._trace_record(TraceEventKind.MODEL_EVENT, payload, now_ms)

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
                fan_assist_active=self.state.fan.assist,
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
            realized_combustion_load = None if self._framed_pulse() else diagnostics.applied_combustion_load
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
        if not stale_observation and not self._framed_pulse():
            self._trace_complete_applied_interval(
                now,
                sample_complete=True,
                realized_combustion_load=realized_combustion_load,
            )
        self.state.controller.trace_result_revision = result.revision
        self._trace_record(TraceEventKind.CONTROL_UPDATE, payload, observed_ms)
        self._trace_last_update_payload = payload
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
                        if realized_combustion_load is None and self._framed_pulse()
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
            self._framed_pulse()
            and controller.pulse_frame_result_revision == 0
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
            else (
                max(0, controller.pulse_frame_result_revision)
                if self._framed_pulse()
                else max(0, controller.trace_result_revision)
            )
        )
        controller.trace_prior_requested_auger_duty = (
            applied.requested if applied.requested is not None else applied.ratio
        )
        controller.trace_prior_realized_auger_duty = applied.ratio
        controller.trace_prior_output_source = applied.source
        controller.trace_prior_fan_duty = controller.fan_duty
        controller.trace_combustion_load = None
        controller.trace_prior_combustion_load = None

    def _trace_start_frame(self, now: float, raw_duty: float, bounded_duty: float, revision: int, active: bool) -> None:
        controller = self.state.controller
        start_ms = int(now * 1_000)
        controller.trace_frame_start_ms = start_ms
        controller.trace_frame_result_revision = revision
        controller.trace_frame_raw_duty = raw_duty
        controller.trace_frame_bounded_duty = bounded_duty
        hold_cycle_seconds = float(self.settings["cycle_data"]["HoldCycleTime"])
        controller.trace_frame_scheduled_on_seconds = hold_cycle_seconds * bounded_duty
        controller.trace_frame_scheduled_off_seconds = hold_cycle_seconds * (1 - bounded_duty)
        controller.trace_frame_actual_on_seconds = 0.0
        controller.trace_frame_transition_count = 0
        controller.trace_frame_active = active
        controller.trace_frame_actual_start_active = active
        controller.trace_frame_on_started_ms = start_ms if active else None

    def _trace_finish_frame(self, now: float, inhibit: InhibitReason = InhibitReason.NONE) -> None:
        controller = self.state.controller
        start_ms = controller.trace_frame_start_ms
        if start_ms is None:
            return
        end_ms = int(now * 1_000)
        if controller.trace_frame_active and controller.trace_frame_on_started_ms is not None:
            controller.trace_frame_actual_on_seconds += (end_ms - controller.trace_frame_on_started_ms) / 1_000
            controller.trace_frame_on_started_ms = None
        self._trace_record(
            TraceEventKind.ACTUATION_FRAME,
            FixedCycleFramePayload(
                result_revision=controller.trace_frame_result_revision,
                raw_requested_duty=controller.trace_frame_raw_duty,
                bounded_duty=controller.trace_frame_bounded_duty,
                u_min=float(self.settings["cycle_data"]["u_min"]),
                u_max=float(self.settings["cycle_data"]["u_max"]),
                cycle_start_ms=start_ms,
                cycle_end_ms=end_ms,
                scheduled_on_seconds=controller.trace_frame_scheduled_on_seconds,
                scheduled_off_seconds=controller.trace_frame_scheduled_off_seconds,
                actual_on_seconds=controller.trace_frame_actual_on_seconds,
                transition_count=controller.trace_frame_transition_count,
                fan_assist_active=self.state.fan.assist,
                inhibit_reason=inhibit,
                output_active=controller.trace_frame_active,
                actual_start_active=controller.trace_frame_actual_start_active,
            ),
            end_ms,
        )
        controller.trace_frame_start_ms = None

        controller.trace_frame_active = False

    name = Mode.HOLD
    _model_store = None

    def setup(self):
        import control as _control

        self._trace_recorder = None
        self._trace_session_id = None
        self._trace_cook_id = None
        self._trace_closed = False
        self._trace_warning_active = False
        self._trace_pending_model_events = []
        self._clear_trace_session_model_authority()
        self._trace_runner_snapshot_fallback_safe = True
        self._trace_last_update_payload = None
        self._reachability_advisory_key = None
        try:
            self._trace_recorder = ControlTraceRecorder(warning=self._trace_warning)
        except Exception as error:
            self._trace_warning(f"Control trace recorder unavailable: {error}")

        start_fan(self.grill, self.settings)
        self.grill.power_on()
        _control.eventLogger.debug("Power ON, Fan ON, Igniter OFF, Auger OFF")

        self.grill.auger_on()
        _control.eventLogger.debug("Auger ON")

        # Initialize cycle to minimum ratio.
        _ct = hold_initial_cycle(self.settings["cycle_data"])
        self.state.cycle.on_time = _ct.on_time
        self.state.cycle.off_time = _ct.off_time
        self.state.cycle.cycle_time = _ct.cycle_time
        self.state.cycle.ratio = _ct.cycle_ratio
        self.state.cycle.raw_ratio = _ct.cycle_ratio
        self.state.lid.open_detected = False
        self.state.lid.expires = 0
        self.state.target_temp_achieved = False

        self._model_store = self._model_store or ControllerModelStore(
            reader=self.ctx.store.read_generic_key, writer=self.ctx.store.write_generic_key
        )
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
            self._configure_actuation_mode()
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
                fan_assist_active=False,
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

        if not self._framed_pulse():
            self._trace_finish_frame(now)
        self._trace_safety(
            SafetyEventType.CONTROLLER_RECONFIGURE,
            now,
            "controller reconfigured",
            InhibitReason.NONE,
        )
        self._trace_complete_applied_interval(now, sample_complete=False, realized_combustion_load=None)
        _control.eventLogger.info("Controller reinitialized with updated settings")
        controller_state = self.state.controller
        self._trace_session_id = None
        self._trace_cook_id = None
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
        self._configure_actuation_mode()
        self._trace_runner_snapshot_fallback_safe = not self._runner.runs_async()
        self._restore_model()
        self._configure_fan_authority()
        self._ensure_trace_session(now)
        self._set_output(
            seed_output(
                self.state.cycle.ratio,
                now,
                lid_open=self.state.lid.open_detected,
                manual_override_active=self.state.manual_override["auger"] > now,
                fan_assist_active=self.state.fan.assist,
                auger_output=current_output_status["auger"],
            ),
            now,
        )
        self._runner_configuration_revision = getattr(self._runner, "configuration_revision", lambda: 0)()

    def on_tick(self, now, ptemp, current_output_status):
        import control as _control

        ctx = self.ctx
        control = self.control
        settings = self.settings
        runner_revision = getattr(self._runner, "configuration_revision", lambda: 0)()
        if runner_revision != self._runner_configuration_revision:
            self._adopt_runner_configuration(now, current_output_status)
        self._ensure_trace_session(now)

        if control["controller_update"]:
            control["controller_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            self.settings = ctx.store.read_settings()
            settings = self.settings
            if self._framed_pulse():
                self._reset_framed_pulse(PulseResetReason.MODE_CHANGE, now, InhibitReason.SAFETY)
            self._controller_status = self._runner.reconfigure(settings, control, logger=ctx.control_log)
            if self._controller_status == "Active" and (
                getattr(self._runner, "configuration_revision", lambda: 0)() != self._runner_configuration_revision
            ):
                self._adopt_runner_configuration(now, current_output_status)
            elif self._controller_status != "Active":
                self._trace_safety(
                    SafetyEventType.CONTROLLER_FALLBACK,
                    now,
                    "controller reconfigure fell back",
                    InhibitReason.SAFETY,
                )

        # Feed the runner every tick so a threaded core always has a fresh temp
        # to solve; for the synchronous runner this just stores the latest temp,
        # so the value read at the gate below is unchanged.
        self._runner.submit(ptemp)
        # Check to see if it's time to update pid and update if needed.
        controller_interval = self._runner.control_period() or (
            0.0 if self._framed_pulse() else self.state.cycle.cycle_time
        )
        framed_feedback_due = False
        if (now - self.state.controller.cycle_start) > controller_interval:
            result = self._runner.latest()
            if isinstance(result.diagnostics, MpcTraceDiagnostics):
                self._observe_reachability_advisory(result.diagnostics)
            if self._framed_pulse():
                controller = self.state.controller
                controller.cycle_start = now
                if result.revision > 0 and result.revision > controller.pulse_result_revision:
                    controller.output = result.cycle_ratio
                    controller.pulse_result_revision = result.revision
                    controller.pulse_requested_duty = max(0.0, min(1.0, result.cycle_ratio))
                    controller.pulse_combustion_load = (
                        result.allocation.normalized_combustion_load if result.allocation is not None else None
                    )
                    controller.pulse_maximum_duty = result.allocation.u_max if result.allocation is not None else 1.0
                    controller.pulse_requested_fan_duty = result.fan["duty"] if result.fan is not None else None
                    if result.fan is not None and controller_fan_authority(settings, control):
                        controller.fan_duty = result.fan["duty"]
                        control["duty_cycle"] = controller.fan_duty
                        ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                controller.pulse_stale_command = result.stale_state is ResultStaleState.STALE
                self.state.cycle.ratio = self.state.cycle.raw_ratio = controller.pulse_requested_duty
                self.state.cycle.on_time = self.state.cycle.off_time = self.state.cycle.cycle_time = 0.0
                self.state.fan.assist = False
                self._trace_update(result, now, controller_interval)
                framed_feedback_due = True
            else:
                self.state.controller.output, fan_cmd = result.cycle_ratio, result.fan
                self.state.controller.cycle_start = now
                self.state.cycle.ratio = self.state.cycle.raw_ratio = (
                    settings["cycle_data"]["u_min"] if self.state.lid.open_detected else self.state.controller.output
                )
                # Controllers that command the fan directly (MPC) route the duty
                # through control['duty_cycle'] so the PWM apply path below uses it.
                # self.state.controller.controls_fan (set at setup from the
                # controller's commands_fan() capability) suppresses the
                # temperature-profile fan logic below so it cannot overwrite the
                # MPC-issued fan command.
                if fan_cmd is not None and controller_fan_authority(settings, control):
                    self.state.controller.fan_duty = fan_cmd["duty"]
                    control["duty_cycle"] = self.state.controller.fan_duty
                    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                # If ratio is less than min set auger ratio to min and control further via fan.
                if self.state.cycle.ratio < settings["cycle_data"]["u_min"]:
                    self.state.cycle.ratio = settings["cycle_data"]["u_min"]
                    # FanPid control is only enabled when the user has enabled it in settings.
                    # It is not compatible with PWM control on DC fans (too many variables).
                    # To use FanPid Control with DC fans, disable PWM control and enable FanPidEnabled in settings.
                    if settings["cycle_data"].get("FanPidEnabled", False) and not control["pwm_control"]:
                        self.state.fan.assist = True
                    else:
                        self.state.fan.assist = False
                else:
                    self.state.fan.assist = False
                # Don't set ratio over maximum.
                self.state.cycle.ratio = min(self.state.cycle.ratio, settings["cycle_data"]["u_max"])
                self._trace_update(result, now, controller_interval)
                self._trace_finish_frame(now)
                self._trace_start_frame(
                    now,
                    self.state.controller.output,
                    self.state.cycle.ratio,
                    max(0, self.state.controller.trace_result_revision),
                    current_output_status["auger"],
                )

                # A live manual override already reported the duty a human commanded
                # (_on_manual_output); the cycle ratio computed here is not what the
                # auger is doing. An override expiring at exactly `now` is still live
                # (matches the `< now` expiry check in base.py's own reset). self._runner
                # is already guaranteed non-None here -- submit() and control_period()
                # above would have raised otherwise.
                if self.state.manual_override["auger"] < now:
                    self._set_output(
                        AppliedOutput(
                            ratio=0.0 if self.state.lid.open_detected else self.state.cycle.ratio,
                            source=classify_output_source(
                                lid_open=self.state.lid.open_detected,
                                manual_override_active=False,
                                fan_assist_active=self.state.fan.assist,
                            ),
                            timestamp=now,
                            requested=self.state.controller.output,
                        ),
                        now,
                    )

                snapshot = self._runner.get_model_snapshot()
                if snapshot is not None and not self._model_store.save(self._controller_name, snapshot):
                    # The overwhelming majority of these are a benign non-advancing
                    # revision (nothing new learned since the last save); the store's
                    # own logger carries the specific reason (rejected vs. write
                    # failure) at warning level. This debug line only keeps the
                    # outcome from being swallowed entirely at the Hold layer.
                    _control.eventLogger.debug(f"Did not persist the {self._controller_name} model this tick")

        lid_will_open = (
            self.state.target_temp_achieved
            and settings["cycle_data"]["LidOpenDetectEnabled"]
            and ptemp < control["primary_setpoint"] * ((100 - settings["cycle_data"]["LidOpenThreshold"]) / 100)
        )
        if self._framed_pulse():
            if self.state.manual_override["auger"] < now and not self.state.lid.open_detected:
                decision = self._advance_framed_pulse(
                    now,
                    current_output_status["auger"],
                    apply_transition=not lid_will_open,
                )
                if framed_feedback_due:
                    self._report_framed_feedback(now, decision.delivered_on_s)
        else:
            self._auger_cycle_tick(now, current_output_status)

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
            if self._framed_pulse():
                self._reset_framed_pulse(PulseResetReason.LID, now, InhibitReason.LID_OPEN)
            else:
                grill_platform.auger_off()
                self._on_auger_off(now)
                self._trace_finish_frame(now, InhibitReason.LID_OPEN)
            self._set_output(
                AppliedOutput(
                    ratio=0.0,
                    source=classify_output_source(
                        lid_open=True,
                        manual_override_active=self.state.manual_override["auger"] >= now,
                        fan_assist_active=self.state.fan.assist,
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
                if self._framed_pulse():
                    self._reset_framed_pulse(PulseResetReason.LID, now, InhibitReason.LID_OPEN)
                else:
                    grill_platform.auger_off()
                    self._on_auger_off(now)
                    self._trace_finish_frame(now, InhibitReason.LID_OPEN)
                self._set_output(
                    AppliedOutput(
                        ratio=0.0,
                        source=classify_output_source(
                            lid_open=True,
                            manual_override_active=self.state.manual_override["auger"] >= now,
                            fan_assist_active=self.state.fan.assist,
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

        # This added section allows for additional pid control by controlling the fan.
        # Implemented for AC fans and DC fans not using PWM Control.
        # If Auger ratio is below minimum Cycle the Fan as additional output control utilizing the pid output.
        if (
            self.state.target_temp_achieved
            and self.state.fan.assist
            and not self.state.lid.open_detected
            and not control["pwm_control"]
        ):
            # If smoke plus mode is active set max fan ratio to smoke plus ratio otherwise set to 1.
            if control["s_plus"]:
                total_fan_cycle = settings["smoke_plus"]["on_time"] + settings["smoke_plus"]["off_time"]
            else:
                total_fan_cycle = self.state.cycle.cycle_time
            max_fan_ratio = smoke_plus_max_ratio(settings["smoke_plus"], control["s_plus"])

            # Divide the pid output by the u_min.
            # This way when we are at u_min our fan will be at 100% fan ratio and will drop proportionally down to 0 as controller_output drops.
            # If pid is returning negative values the best we can do is shut off the fan so set min to 0.
            controller_output_adjusted = max(0, self.state.controller.output / settings["cycle_data"]["u_min"])
            _ft = fan_assist_times(
                self.state.controller.output, total_fan_cycle, max_fan_ratio, settings["cycle_data"]["u_min"]
            )
            fan_ratio = _ft.ratio
            fan_on_time = _ft.on_time
            fan_off_time = _ft.off_time
            _control.eventLogger.debug(
                f"Fan PID: Fan ON, controller_output: {self.state.controller.output}, controller_output_adjusted: {controller_output_adjusted}"
            )
            _control.eventLogger.debug(
                f"Fan ratio: {fan_ratio}, Fan on time: {fan_on_time}, Fan off time: {fan_off_time}"
            )
            if (now - self.state.fan.cycle_toggle_time) > fan_on_time and current_output_status["fan"]:
                grill_platform.fan_off()
                self.state.fan.cycle_toggle_time = now
                _control.eventLogger.debug("Fan PID: Fan OFF")
            elif (now - self.state.fan.cycle_toggle_time) > fan_off_time and not current_output_status["fan"]:
                self.state.fan.cycle_toggle_time = now
                start_fan(grill_platform, settings, control["duty_cycle"])
                _control.eventLogger.debug("Fan PID: Fan ON")

        self._smoke_plus_fan_tick(now, ptemp, current_output_status)
        if self._trace_recorder is not None:
            try:
                self._trace_recorder.flush_due(time.monotonic_ns() // 1_000_000)
            except Exception as error:
                if not self._trace_warning_active:
                    self._trace_warning(f"Control trace flush failed: {error}")
                    self._trace_warning_active = True

    def _on_auger_on(self, now):
        settings = self.settings
        control = self.control

        self.state.cycle.on_time = settings["cycle_data"]["HoldCycleTime"] * self.state.cycle.ratio
        self.state.cycle.off_time = settings["cycle_data"]["HoldCycleTime"] * (1 - self.state.cycle.ratio)
        self.state.cycle.cycle_time = self.state.cycle.on_time + self.state.cycle.off_time
        controller = self.state.controller
        if controller.trace_frame_start_ms is not None and not controller.trace_frame_active:
            controller.trace_frame_active = True
            controller.trace_frame_on_started_ms = int(now * 1_000)
            controller.trace_frame_transition_count += 1

        import control as _control

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

        # publish pid info to mqtt if enabled
        if settings["notify_services"].get("mqtt") is not None and settings["notify_services"]["mqtt"]["enabled"]:
            controller_data = self._runner.controller_state()
            controller_data["cycle_ratio"] = round(self.state.cycle.ratio, 2)
            self.ctx.notifications.check(settings, control, pid_data=controller_data)

    def _on_auger_off(self, now):
        controller = self.state.controller
        if controller.trace_frame_active and controller.trace_frame_on_started_ms is not None:
            controller.trace_frame_actual_on_seconds += (
                int(now * 1_000) - controller.trace_frame_on_started_ms
            ) / 1_000
            controller.trace_frame_on_started_ms = None
            controller.trace_frame_active = False
            controller.trace_frame_transition_count += 1

    def _on_manual_output(self, name, output):
        if name != "auger" or self._runner is None:
            return
        if self._framed_pulse():
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
            self._reset_framed_pulse(PulseResetReason.MANUAL, self._last_now, InhibitReason.MANUAL_OVERRIDE)
            if output and not self.grill.get_output_status()["auger"]:
                self.grill.auger_on()
            elif not output and self.grill.get_output_status()["auger"]:
                self.grill.auger_off()
        else:
            self._trace_finish_frame(self._last_now, InhibitReason.MANUAL_OVERRIDE)
        self._set_output(
            AppliedOutput(
                ratio=1.0 if output else 0.0,
                source=classify_output_source(
                    lid_open=False,
                    manual_override_active=True,
                    fan_assist_active=False,
                ),
                timestamp=self._last_now,
            ),
            self._last_now,
        )
        if not self._framed_pulse():
            self._trace_safety(
                SafetyEventType.MANUAL_TAKEOVER,
                self._last_now,
                "manual auger output applied",
                InhibitReason.MANUAL_OVERRIDE,
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
            if self._framed_pulse():
                self._reset_framed_pulse(PulseResetReason.SAFETY, now, InhibitReason.SAFETY)
            else:
                self._trace_finish_frame(now, InhibitReason.SAFETY)

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
        if self._framed_pulse() and self._pulse_scheduler is not None:
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
            if not config.get(self._controller_name, {}).get("enable_identification"):
                return
            verdict = self._runner.refit_from_cook()
            snapshot = self._runner.get_model_snapshot()
            if snapshot is not None:
                self._clear_trace_session_model_authority()
                self._trace_model(ModelEventType.REFIT, "model refit completed", snapshot)
                if getattr(verdict, "accepted", False):
                    self._trace_model(ModelEventType.ADOPT, "refit model adopted", snapshot)
                else:
                    self._trace_model(ModelEventType.REJECT, "refit model rejected", snapshot)
                if not self._model_store.save(self._controller_name, snapshot):
                    _control.eventLogger.debug(f"Did not persist a refit {self._controller_name} model")
        except Exception as error:
            _control.eventLogger.error(f"Model refit failed at cook end: {error}")

    def teardown(self, ptemp):
        first_trace_teardown = not self._trace_closed and (
            getattr(self, "_trace_recorder", None) is not None or getattr(self, "_trace_session_id", None) is not None
        )
        if self._framed_pulse():
            now = self.ctx.clock.now()
            if first_trace_teardown:
                self._report_framed_feedback(now, self.state.controller.pulse_metrics_delivered_on_s)
            self._reset_framed_pulse(PulseResetReason.MODE_CHANGE, now, InhibitReason.SAFETY)
        try:
            if self._runner is not None:
                self._runner.stop()
                if first_trace_teardown:
                    self._refit_model()
        finally:
            if first_trace_teardown:
                self._trace_closed = True
                now = self.ctx.clock.now()
                self._trace_complete_applied_interval(
                    now,
                    sample_complete=False,
                    realized_combustion_load=None,
                )
                if not self._framed_pulse():
                    self._trace_finish_frame(now)
                if self._trace_recorder is not None:
                    try:
                        self._trace_recorder.close()
                    except Exception as error:
                        self._trace_warning(f"Control trace close failed: {error}")
