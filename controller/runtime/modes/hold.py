import time
import uuid

from common.common import WriteKind
from common.controller_model_state import ControllerModelStore
from common.modes import Mode
from common.control_trace import (
    ActuationMode,
    AllocationPayload,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FixedCycleFramePayload,
    InhibitReason,
    ModelEventPayload,
    ModelEventType,
    MpcUpdatePayload,
    PidSpUpdatePayload,
    PidUpdatePayload,
    SafetyEventPayload,
    SafetyEventType,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from controller.applied_output import AppliedOutput, classify_output_source, seed_output
from controller.base import MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.runtime.logic.cycle import hold_initial_cycle
from controller.runtime.control_trace_recorder import ControlTraceRecorder
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
    _trace_runner_snapshot_fallback_safe: bool = True

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
        is_mpc = controller is ControllerType.MPC
        payload = SessionPayload(
            controller=controller,
            controller_config=self._trace_settings(config),
            temperature_unit=str(self.settings["globals"]["units"]),
            control_period_seconds=float(self._runner.control_period() or cycle_data["HoldCycleTime"]),
            model_revision=model_revision,
            model_provenance=provenance if model_revision is not None else None,
            u_min=None if is_mpc else float(cycle_data["u_min"]),
            u_max=None if is_mpc else float(cycle_data["u_max"]),
            hold_cycle_seconds=None if is_mpc else float(cycle_data["HoldCycleTime"]),
            pulse_slot_seconds=2.0 if is_mpc else None,
            pulse_frame_seconds=20.0 if is_mpc else None,
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

    def _trace_safety(self, event: SafetyEventType, now: float, detail: str, inhibit: InhibitReason) -> None:
        self._trace_record(
            TraceEventKind.SAFETY_EVENT,
            SafetyEventPayload(
                event=event,
                inhibit_reason=inhibit,
                result_revision=self.state.controller.trace_result_revision
                if self.state.controller.trace_result_revision >= 0
                else None,
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
        if result is None or result.revision <= self.state.controller.trace_result_revision or result.revision == 0:
            return False
        diagnostics = result.diagnostics
        if diagnostics is None or result.completed_wall_time is None or result.solve_end_monotonic is None:
            return False
        wall_ms = int(result.completed_wall_time * 1_000)
        monotonic_ms = int(result.solve_end_monotonic * 1_000)
        common = dict(
            monotonic_ms=monotonic_ms,
            wall_ms=wall_ms,
            result_revision=result.revision,
            result_age_ms=max(0, int(now * 1_000) - wall_ms),
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
            actuation_mode=ActuationMode.FIXED_CYCLE,
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
            payload = MpcUpdatePayload(
                **common,
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
                deadline_miss_count=diagnostics.consecutive_policy_failures,
                stale=diagnostics.consecutive_policy_failures > 0,
                recovered=(self.state.controller.trace_mpc_stale and diagnostics.consecutive_policy_failures == 0),
                predicted_feasible=None,
                predicted_steady_load=None,
            )
            realized_combustion_load = diagnostics.applied_combustion_load
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
        self._trace_complete_applied_interval(
            now,
            sample_complete=True,
            realized_combustion_load=realized_combustion_load,
        )
        self.state.controller.trace_result_revision = result.revision
        self._trace_record(TraceEventKind.CONTROL_UPDATE, payload, wall_ms)
        if allocation_payload is not None:
            self._trace_record(TraceEventKind.ALLOCATION, allocation_payload, wall_ms)
            self.state.controller.trace_mpc_stale = diagnostics.consecutive_policy_failures > 0

        return True

    def _trace_complete_applied_interval(
        self,
        now: float,
        *,
        sample_complete: bool,
        realized_combustion_load: float | None,
    ) -> None:
        controller = self.state.controller
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
                realized_combustion_load=realized_combustion_load,
                actual_fan_duty=controller.trace_prior_fan_duty if controller.controls_fan else None,
                sample_complete=sample_complete,
                output_source=controller.trace_prior_output_source,
            ),
            end_ms,
        )
        controller.trace_interval_start_ms = end_ms

    def _set_output(self, applied: AppliedOutput, now: float) -> None:
        controller = self.state.controller
        self._trace_complete_applied_interval(
            now,
            sample_complete=False,
            realized_combustion_load=None,
        )
        self._runner.set_output(applied)
        controller.trace_interval_start_ms = int(now * 1_000)
        controller.trace_interval_result_revision = max(0, controller.trace_result_revision)
        controller.trace_prior_requested_auger_duty = (
            applied.requested if applied.requested is not None else applied.ratio
        )
        controller.trace_prior_realized_auger_duty = applied.ratio
        controller.trace_prior_output_source = applied.source
        controller.trace_prior_fan_duty = controller.fan_duty
        controller.trace_combustion_load = None

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

        if self._runner is not None:
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

    def on_tick(self, now, ptemp, current_output_status):
        import control as _control

        ctx = self.ctx
        control = self.control
        settings = self.settings
        self._ensure_trace_session(now)

        if control["controller_update"]:
            control["controller_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            self.settings = ctx.store.read_settings()
            settings = self.settings
            self._controller_status = self._runner.reconfigure(settings, control, logger=ctx.control_log)
            if self._controller_status == "Active":
                self._trace_finish_frame(now)
                self._trace_safety(
                    SafetyEventType.CONTROLLER_RECONFIGURE,
                    now,
                    "controller reconfigured",
                    InhibitReason.NONE,
                )
                self._trace_complete_applied_interval(
                    now,
                    sample_complete=False,
                    realized_combustion_load=None,
                )
                _control.eventLogger.info("Controller reinitialized with updated settings")
                controller_state = self.state.controller
                self._trace_session_id = None
                self._trace_cook_id = None
                self._clear_trace_session_model_authority()
                controller_state.trace_result_revision = -1
                controller_state.trace_combustion_load = None
                controller_state.trace_mpc_stale = False
                controller_state.trace_interval_start_ms = None
                controller_state.trace_prior_requested_auger_duty = 0.0
                controller_state.trace_prior_realized_auger_duty = 0.0
                controller_state.trace_prior_fan_duty = None
                controller_state.trace_prior_output_source = None
                controller_state.trace_prior_combustion_load = None
                self._controller_name = settings["controller"]["selected"]
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
            else:
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
        controller_interval = self._runner.control_period() or self.state.cycle.cycle_time
        if (now - self.state.controller.cycle_start) > controller_interval:
            result = self._runner.latest()
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
            grill_platform.auger_off()
            self._on_auger_off(now)
            self._trace_finish_frame(now, InhibitReason.LID_OPEN)
            self._trace_safety(SafetyEventType.LID_DETECTED, now, "lid open detected", InhibitReason.LID_OPEN)
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
                grill_platform.auger_off()
                self._on_auger_off(now)
                self._trace_finish_frame(now, InhibitReason.LID_OPEN)
                self._trace_safety(
                    SafetyEventType.LID_DETECTED, now, "lid open set by operator", InhibitReason.LID_OPEN
                )
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
            self._trace_finish_frame(now, InhibitReason.SAFETY)
            self._trace_safety(event_type, now, event.replace("_", " "), InhibitReason.SAFETY)

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
        return {"lid_open_detected": self.state.lid.open_detected, "lid_open_endtime": self.state.lid.expires}

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
                self._trace_finish_frame(now)
                if self._trace_recorder is not None:
                    try:
                        self._trace_recorder.close()
                    except Exception as error:
                        self._trace_warning(f"Control trace close failed: {error}")
