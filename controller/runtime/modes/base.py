"""ControlMode template-method base: the SHARED skeleton of a single
control work cycle.

Concrete subclasses (Monitor, Manual, ...) override the hooks below to
supply their mode-specific behavior. Each tick follows a strict
sense -> health -> manual override -> numeric safety -> act -> publish order:
read the probes ONCE at the top of the tick, process thermocouple health
before any positive actuator command, apply pending manual overrides using
`current_output_status` captured once on that fresh tick, run the universal
max-temp check and mode `check_safety`, then a single merged `on_tick` that
does the controller/auger/fan work on that fresh temperature before status
and history publication.
"""

import json
import logging
import math
import time
from functools import partial
from hashlib import sha256

from common.learning_trajectory import TrajectoryBreakReason
from common.modes import Mode, StatusState
from common.process_mon import Process_Monitor
from common.system import restart_control
from controller.runtime.heartbeat import stamp_control_heartbeat
from controller.runtime.learning_trajectory import (
    ModeEntered,
    ModeExited,
    ThermalSample,
    TrajectoryBoundary,
)
from controller.runtime.logic.cycle import smoke_cycle_times
from controller.runtime.logic.fan import start_fan
from controller.runtime.logic.pwm import ramp_params
from controller.runtime.logic.smartstart import profile_cycle
from controller.runtime.system_commands import process_system_commands
from controller.runtime.transitions import TransitionKind, evaluate_phase, request_transition
from distance.intervals import HOPPER_LEVEL_REFRESH_INTERVAL
from probes.thermocouple_health import ThermocoupleEvidence
from probes.thermocouple_inference import ThermocoupleExcitationContext

_ACTIVE_THERMOCOUPLE_INFERENCE_MODES = frozenset(
    {
        Mode.STARTUP,
        Mode.REIGNITE,
        Mode.SMOKE,
        Mode.HOLD,
        Mode.MANUAL,
        Mode.RECIPE,
    }
)

_THERMOCOUPLE_TRANSITION_LOG_PREFIX = "Thermocouple health transition "


def _thermocouple_transition_role(transition, primary_label):
    is_primary = transition.current.detail.get("is_primary")
    if isinstance(is_primary, bool):
        return "primary" if is_primary else "secondary"
    return "primary" if transition.label == primary_label else "secondary"


def _thermocouple_transition_authority(report, role):
    if ThermocoupleEvidence.HARDWARE in report.evidence:
        return "stop" if role == "primary" else "notify_only"
    return report.detail.get("authority")


def _thermocouple_transition_event(transition, primary_label):
    report = transition.current
    if not report.confirmed:
        return None
    role = _thermocouple_transition_role(transition, primary_label)
    if role == "secondary":
        return "Thermocouple_Fault_Secondary"
    authority = _thermocouple_transition_authority(report, role)
    if authority == "notify_only":
        return "Thermocouple_Fault_Primary_Observed"
    if authority == "stop":
        return "Thermocouple_Fault_Primary"
    return None


def _thermocouple_transition_log(transition, primary_label):
    report = transition.current
    serialized = report.as_dict()
    detail = serialized["detail"]
    role = _thermocouple_transition_role(transition, primary_label)
    payload = {
        "label": transition.label,
        "role": role,
        "state": serialized["state"],
        "faults": serialized["faults"],
        "evidence": serialized["evidence"],
        "policy": detail.get("policy"),
        "authority": _thermocouple_transition_authority(report, role),
        "policy_version": detail.get("policy_version"),
        "sample_count": detail.get("sample_count"),
        "coverage_seconds": detail.get("coverage_seconds"),
        "max_gap_seconds": detail.get("max_gap_seconds"),
        "hot_span_c": detail.get("hot_span_c"),
        "cold_span_c": detail.get("cold_span_c"),
        "delta_span_c": detail.get("delta_span_c"),
        "collapse_fraction": detail.get("collapse_fraction"),
        "heat_on_seconds": detail.get("heat_on_seconds"),
        "witness_source": detail.get("witness_source"),
        "witness_rise_c": detail.get("witness_rise_c"),
    }
    if "status" in detail:
        payload["hardware_status"] = detail["status"]
    return _THERMOCOUPLE_TRANSITION_LOG_PREFIX + json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )


class ControlMode:
    """Template-method base for a single mode work cycle.

    Subclasses set `name` (the control `mode` string this class handles, e.g.
    'Smoke', 'Hold') and override hooks with safe no-op defaults:
      - setup(): pre-loop mode-specific configuration (fan/power, cycle
        params, runner, ...).
      - setup_safety(ptemp) -> str: pre-loop safety check, called AFTER the
        first probe read (unlike setup(), which runs before it). Return
        'Active' to allow the loop to run, 'Inactive' to skip it entirely (abort
        contract -- teardown still runs).
      - on_tick(now, ptemp, current_output_status): per-iteration
        mode-specific control/auger/fan logic, run once per tick AFTER the
        safety checks. `ptemp` is the fresh probe reading for this tick and
        `current_output_status` is captured ONCE per tick by the shared
        skeleton, BEFORE the manual-override block -- never re-fetch either
        inside a hook. This is the merged control+fan hook: it runs the
        controller/auger work and the fan/smoke-plus/lid-open work together.
      - on_metrics_stamped(): called once, after run() has stamped this
        run's metrics row and staged the fields shared by every mode, and
        before that row is first written (default no-op). Mode-specific
        metrics belong here rather than in setup(), which runs while
        `self.state.metrics` is still the state's empty default and is about
        to be replaced wholesale by the stamped row.
      - on_settings_reload(): called after `self.settings` is reloaded in
        the `settings_update` block (default no-op).
      - on_publish(now): called immediately after the notifications-check
        control rebind, at the cycle-ratio MQTT publish position (default
        no-op).
      - check_safety(now, ptemp) -> bool: per-iteration mode-specific safety
        check, run BEFORE on_tick on the fresh ptemp. Return True to break the
        loop IMMEDIATELY, before any actuation happens for this tick -- default
        False (no-op, never breaks).
      - should_exit(now, ptemp) -> bool: per-iteration mode-specific exit
        condition (default False -- rely on the universal breaks).
      - status_fragment() -> dict: extra fields merged into status_data at
        publish time (default {}).
      - teardown(ptemp): mode-specific cleanup after the loop ends.
    """

    name: Mode | str = ""
    # Loop-consistent `now`, refreshed at the top of `_apply_manual_overrides`
    # (the same value `run()` will go on to pass to `on_tick` this iteration).
    # `_on_manual_output` has no `now` of its own; it reads this instead of taking
    # a fresh clock reading, so a manual report never sorts after the tick reports
    # it actually preceded (the runner replays by timestamp).
    _last_now: float = 0.0

    def __init__(self, ctx, state):
        self.ctx = ctx
        self.state = state
        self.grill = ctx.devices.grill_platform
        self.probe_complex = ctx.devices.probe_complex
        self.dist_device = ctx.devices.dist_device
        self.settings = None
        self.control = None
        self._excitation_last_read_at = None
        self._trajectory_active_event = None

    # ---- hooks (safe defaults) ----
    def setup(self):
        pass

    def setup_safety(self, ptemp) -> str:
        return "Active"

    def on_tick(self, now, ptemp, current_output_status):
        pass

    def realized_cycle_ratio(self):
        """The duty measured to have reached the auger, or None if unmeasured.

        Default None: most modes drive the auger open-loop from a configured
        cycle, so the commanded ratio IS the whole story and reporting it again
        under a second name would draw two identical lines and imply a clamp
        was measured when none was. Hold overrides this -- its framed-pulse
        machinery measures delivered on-time.
        """
        return

    def on_metrics_stamped(self):
        pass

    def on_cook_identity_rotated(self, previous_cook_id, cook_id, now):
        pass

    def on_settings_reload(self):
        pass

    def on_publish(self, now):
        pass

    def check_safety(self, now, ptemp) -> bool:
        return False

    def should_exit(self, now, ptemp) -> bool:
        return False

    def status_fragment(self) -> dict:
        return {}

    def teardown(self, ptemp):
        pass

    def _on_auger_on(self, now):
        pass

    def _on_manual_output(self, name, output):
        """A human just drove an actuator directly."""

    def _on_manual_release(self, name, now):
        pass

    def _on_safety_event(self, event, now):
        pass

    def _on_auger_off(self, now):
        pass

    # ---- shared helpers ----
    @staticmethod
    def _trajectory_digest(value) -> str:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    @staticmethod
    def _trajectory_clock_pair():
        return time.monotonic_ns() // 1_000_000, time.time_ns() // 1_000_000

    @staticmethod
    def _mode_value(mode):
        return mode.value if hasattr(mode, "value") else str(mode)

    def _trajectory_mode_event(self, monotonic_ms, wall_ms):
        settings = self.settings if isinstance(self.settings, dict) else {}
        control = self.control if isinstance(self.control, dict) else {}
        globals_settings = settings.get("globals", {})
        controller_settings = settings.get("controller", {})
        selected = (
            controller_settings.get("selected", "unknown") if isinstance(controller_settings, dict) else "unknown"
        )
        controller_configs = controller_settings.get("config", {}) if isinstance(controller_settings, dict) else {}
        selected_config = controller_configs.get(selected, {}) if isinstance(controller_configs, dict) else {}
        units = globals_settings.get("units", "F") if isinstance(globals_settings, dict) else "F"
        settings_digest = self._trajectory_digest(settings)
        settings_revision = int(settings_digest[:16], 16)
        persisted_mode = self._mode_value(control.get("mode", self.name))
        recipe_step_id = None
        if persisted_mode == self._mode_value(Mode.RECIPE):
            recipe = control.get("recipe", {})
            if isinstance(recipe, dict):
                recipe_step_id = f"step-{recipe.get('step', 0)}"
        cook_id = control.get("cook_id")
        if not self._valid_cook_id(cook_id):
            cook_id = "uncooked"
        effective_mode = self._mode_value(self.name)
        trajectory = getattr(self.ctx, "learning_trajectory", None)
        runtime_session_id = getattr(trajectory, "trajectory_session_id", None)
        trajectory_session_id = runtime_session_id if isinstance(runtime_session_id, str) else ""
        trace_session_id = getattr(trajectory, "trace_session_id", None) or ""
        versions = settings.get("versions", {})
        build = (
            {
                "server": str(versions.get("server", "unknown")),
                "build": str(versions.get("build", "unknown")),
            }
            if isinstance(versions, dict)
            else {"server": "unknown", "build": "unknown"}
        )
        return ModeEntered(
            effective_mode=effective_mode,
            persisted_mode=persisted_mode,
            monotonic_ms=monotonic_ms,
            wall_ms=wall_ms,
            cook_id=cook_id,
            trajectory_session_id=trajectory_session_id,
            trace_session_id=trace_session_id,
            recipe_step_id=recipe_step_id,
            units=str(units),
            settings_revision=settings_revision,
            collection_provenance={"origin": "passive-online"},
            configuration_provenance={
                "settings_digest": settings_digest,
                "controller": str(selected),
            },
            cadence_digest=self._trajectory_digest({"frame_ms": 20_000, "sample_sleep_ms": 50}),
            model_structure_digest=self._trajectory_digest({"controller": selected, "structure": selected_config}),
            held_physics_digest=self._trajectory_digest(selected_config),
            delay_input_mapping_digest=self._trajectory_digest({"input": "normalized-combustion-load", "version": 1}),
            actuation_mapping_digest=self._trajectory_digest(
                {
                    "cycle_data": settings.get("cycle_data", {}),
                    "platform": settings.get("platform", {}),
                    "pwm": settings.get("pwm", {}),
                }
            ),
            scored_fan_regime_digest=self._trajectory_digest(
                {
                    "platform": settings.get("platform", {}),
                    "smoke_plus": settings.get("smoke_plus", {}),
                }
            ),
            ambient_semantics_digest=self._trajectory_digest(
                {
                    "source": "configured",
                    "T_amb": (selected_config.get("T_amb", 20.0) if isinstance(selected_config, dict) else 20.0),
                }
            ),
            source_trace_digest=self._trajectory_digest(
                {
                    "cook_id": cook_id,
                    "trace_session_id": trace_session_id or None,
                }
            ),
            source_schema_version=1,
            source_row_digest=self._trajectory_digest(
                {
                    "trace_session_id": trace_session_id or None,
                    "trajectory_session_id": trajectory_session_id,
                    "settings_revision": settings_revision,
                }
            ),
            build_provenance=build,
        )

    def _emit_trajectory_mode_entered(self, monotonic_ms, wall_ms):
        recorder = getattr(self.ctx, "learning_trajectory", None)
        if recorder is None:
            return
        event = self._trajectory_mode_event(monotonic_ms, wall_ms)
        self._trajectory_active_event = event
        try:
            recorder.mode_entered(event)
        except Exception as error:
            self.ctx.event_log.warning(f"Learning trajectory mode entry failed: {error}")

    def _emit_trajectory_temperature(self, sensor_data, ptemp, monotonic_ms, wall_ms):
        recorder = getattr(self.ctx, "learning_trajectory", None)
        if recorder is None:
            return
        settings = self.settings if isinstance(self.settings, dict) else {}
        globals_settings = settings.get("globals", {})
        units = str(globals_settings.get("units", "F")) if isinstance(globals_settings, dict) else "F"
        controller_settings = settings.get("controller", {})
        selected = controller_settings.get("selected") if isinstance(controller_settings, dict) else None
        configs = controller_settings.get("config", {}) if isinstance(controller_settings, dict) else {}
        selected_config = configs.get(selected, {}) if isinstance(configs, dict) else {}
        ambient_c = float(selected_config.get("T_amb", 20.0)) if isinstance(selected_config, dict) else 20.0
        ambient = ambient_c * 9.0 / 5.0 + 32.0 if units == "F" else ambient_c
        primary = sensor_data.get("primary", {}) if isinstance(sensor_data, dict) else {}
        probe_source = next(iter(primary), None) if isinstance(primary, dict) else None
        valid = isinstance(ptemp, (int, float)) and not isinstance(ptemp, bool) and math.isfinite(float(ptemp))
        mode_event = self._trajectory_active_event
        if mode_event is None:
            mode_event = self._trajectory_mode_event(monotonic_ms, wall_ms)
            self._trajectory_active_event = mode_event
        try:
            recorder.observe_temperature(
                ThermalSample(
                    monotonic_ms=monotonic_ms,
                    wall_ms=wall_ms,
                    chamber_temperature=float(ptemp) if valid else None,
                    units=units,
                    probe_valid=valid,
                    probe_source=str(probe_source) if valid and probe_source else None,
                    ambient_temperature=ambient,
                    ambient_source="configured",
                    ambient_uncertainty=0.0,
                    settings_revision=mode_event.settings_revision,
                    recipe_step_id=mode_event.recipe_step_id,
                )
            )
        except Exception as error:
            self.ctx.event_log.warning(f"Learning trajectory temperature capture failed: {error}")

    def _emit_trajectory_mode_exited(
        self,
        control,
        monotonic_ms,
        wall_ms,
        *,
        next_effective_mode=None,
        exit_reason=None,
    ):
        recorder = getattr(self.ctx, "learning_trajectory", None)
        if recorder is None:
            return
        persisted_next = control.get("mode", Mode.STOP)
        if control.get("updated") and persisted_next != Mode.RECIPE:
            next_candidate = persisted_next
        else:
            next_candidate = (
                next_effective_mode or getattr(self.ctx, "trajectory_next_effective_mode", None) or persisted_next
            )
        next_mode = self._mode_value(next_candidate)
        reason = exit_reason
        if reason is None and next_mode == self._mode_value(Mode.STOP):
            reason = TrajectoryBreakReason.STOP
        elif reason is None and next_mode == self._mode_value(Mode.ERROR):
            reason = TrajectoryBreakReason.ERROR
        try:
            recorder.mode_exited(
                ModeExited(
                    effective_mode=self._mode_value(self.name),
                    next_effective_mode=next_mode,
                    monotonic_ms=monotonic_ms,
                    wall_ms=wall_ms,
                    reason=reason,
                )
            )
        except Exception as error:
            self.ctx.event_log.warning(f"Learning trajectory mode exit failed: {error}")

    def _emit_trajectory_boundary(
        self,
        reason,
        now,
        detail,
        *,
        configuration=False,
        replacement=False,
    ):
        recorder = getattr(self.ctx, "learning_trajectory", None)
        if recorder is None:
            return
        monotonic_ms, wall_ms = self._trajectory_clock_pair()
        replacement_mode = self._trajectory_mode_event(monotonic_ms, wall_ms) if replacement else None
        if replacement_mode is not None:
            self._trajectory_active_event = replacement_mode
        boundary = TrajectoryBoundary(
            reason=reason,
            monotonic_ms=monotonic_ms,
            wall_ms=wall_ms,
            detail=f"{detail}@{now}",
            replacement_mode=replacement_mode,
        )
        try:
            if configuration:
                recorder.configuration_changed(boundary)
            else:
                recorder.intervention(boundary)
        except Exception as error:
            self.ctx.event_log.warning(f"Learning trajectory boundary capture failed: {error}")

    @staticmethod
    def _valid_cook_id(cook_id) -> bool:
        return isinstance(cook_id, str) and bool(cook_id) and cook_id == cook_id.strip()

    def _refresh_cook_identity(self, control, *, now=None, preferred=None):
        previous_cook_id = self.control.get("cook_id") if isinstance(self.control, dict) else None
        cook_id = control.get("cook_id")
        if not self._valid_cook_id(cook_id):
            cook_id = self.ctx.store.ensure_cook_id(preferred=preferred)
            control["cook_id"] = cook_id
        self.control = control
        if now is not None and self._valid_cook_id(previous_cook_id) and previous_cook_id != cook_id:
            self._emit_trajectory_boundary(
                TrajectoryBreakReason.COOK_ROTATED,
                now,
                f"cook rotated from {previous_cook_id} to {cook_id}",
                replacement=True,
            )
            self.on_cook_identity_rotated(previous_cook_id, cook_id, now)
        return control

    def _stamp_mode_metric(self, control, pelletdb) -> None:
        self.ctx.store.append_metric()
        self.state.metrics = self.ctx.store.read_metrics()
        self.state.metrics["mode"] = self.name
        self.state.metrics["smokeplus"] = control["s_plus"]
        self.state.metrics["primary_setpoint"] = control["primary_setpoint"]
        self.state.metrics["pellet_level_start"] = pelletdb["current"]["hopper_level"]
        current_pellet_id = pelletdb["current"]["pelletid"]
        pellet_brand = pelletdb["archive"][current_pellet_id]["brand"]
        pellet_type = pelletdb["archive"][current_pellet_id]["wood"]
        self.state.metrics["pellet_brand_type"] = f"{pellet_brand} {pellet_type}"
        self.on_metrics_stamped()
        self.ctx.store.update_metrics(self.state.metrics)

    def _handle_history_clear(self, *, now):
        previous_cook_id = self.control.get("cook_id")
        self.ctx.store.flush_history()
        cook_id = self.ctx.store.ensure_cook_id()
        control = self.ctx.store.read_control()
        control["cook_id"] = cook_id
        self.control = control
        self._stamp_mode_metric(control, self.ctx.store.read_pellet_db())
        self.state.timers.auger_toggle = now
        if self._valid_cook_id(previous_cook_id) and previous_cook_id != cook_id:
            self._emit_trajectory_boundary(
                TrajectoryBreakReason.HISTORY_CLEARED,
                now,
                f"history cleared from {previous_cook_id} to {cook_id}",
                replacement=True,
            )
            self.on_cook_identity_rotated(previous_cook_id, cook_id, now)
        return {
            "result": "OK",
            "message": "History cleared and cook identity rotated.",
            "data": {"cook_id": cook_id},
        }

    def _auger_cycle_tick(self, now, current_output_status):
        """Shared (non-Hold) auger toggle: turn the auger on/off based on
        elapsed time vs. cycle_time/cycle_ratio, honoring manual overrides and
        accumulating augerontime metrics on auger-off. Hold overrides
        `_on_auger_on` to also recompute OnTime/OffTime/CycleTime and publish
        MQTT PID info -- that part is NOT reproduced here."""
        if self.state.manual_override["auger"] < now:
            had_manual_override = self.state.manual_override["auger"] != 0
            self.state.manual_override["auger"] = 0
            if had_manual_override:
                self._on_manual_release("auger", now)
            if not current_output_status["auger"] and (now - self.state.timers.auger_toggle) > (
                self.state.cycle.cycle_time * (1 - self.state.cycle.ratio)
            ):
                self.grill.auger_on()
                self.state.timers.auger_toggle = now
                self.ctx.event_log.debug("Cycle Event: Auger On")
                self._on_auger_on(now)

            # If Auger is ON and time since toggle is greater than On Time
            if current_output_status["auger"] and (now - self.state.timers.auger_toggle) > (
                self.state.cycle.cycle_time * self.state.cycle.ratio
            ):
                self.grill.auger_off()
                self._on_auger_off(now)
                # Add auger ON time to the metrics
                self.state.metrics["augerontime"] += now - self.state.timers.auger_toggle
                self.ctx.store.update_metrics(self.state.metrics)
                # Set current last toggle time to now
                self.state.timers.auger_toggle = now
                self.ctx.event_log.debug("Cycle Event: Auger Off")

    def _smoke_plus_fan_tick(self, now, ptemp, current_output_status):
        """Smoke Plus fan cycling + the elif restore chain. Gated to Smoke
        always, and Hold only once target_temp_achieved. `ptemp` is the fresh
        probe reading for this tick."""
        settings = self.settings
        control = self.control
        grill_platform = self.grill

        # Smoke Plus fan cycling.
        if (
            (self.name == Mode.SMOKE or (self.name == Mode.HOLD and self.state.target_temp_achieved))
            and control["s_plus"]
            and not self.state.lid.open_detected
        ):
            # If Temperature is > settings['smoke_plus']['max_temp']
            # or Temperature is < settings['smoke_plus']['min_temp'] then turn on fan
            if (
                ptemp > settings["smoke_plus"]["max_temp"] or ptemp < settings["smoke_plus"]["min_temp"]
            ) and self.state.manual_override["fan"] < now:
                if not current_output_status["fan"]:
                    start_fan(grill_platform, settings, control["duty_cycle"])
                    self.ctx.event_log.debug("Smoke Plus: Over or Under Temp Fan ON")
            elif (now - self.state.fan.cycle_toggle_time) > settings["smoke_plus"]["on_time"] and current_output_status[
                "fan"
            ]:
                if self.state.manual_override["fan"] < now:
                    self.state.manual_override["fan"] = 0
                    grill_platform.fan_off()
                    self.state.fan.cycle_toggle_time = now
                    self.ctx.event_log.debug("Smoke Plus: Fan OFF")
            elif (
                (now - self.state.fan.cycle_toggle_time) > settings["smoke_plus"]["off_time"]
                and not current_output_status["fan"]
            ) and self.state.manual_override["fan"] < now:
                self.state.fan.cycle_toggle_time = now
                if (
                    settings["platform"]["dc_fan"]
                    and (self.name == Mode.SMOKE or (self.name == Mode.HOLD and not control["pwm_control"]))
                    and settings["smoke_plus"]["fan_ramp"]
                ):
                    grill_platform.pwm_fan_ramp(*ramp_params(settings["smoke_plus"], settings["pwm"]))
                    self.state.fan.pwm_ramping = True
                    self.ctx.event_log.debug("Smoke Plus: Fan Ramping Up")
                else:
                    start_fan(grill_platform, settings, control["duty_cycle"])
                    self.ctx.event_log.debug("Smoke Plus: Fan ON")

        # If Smoke Plus was disabled when fan is OFF return fan to ON
        elif (
            not current_output_status["fan"]
            and not control["s_plus"]
            and not self.state.lid.open_detected
            and self.state.manual_override["fan"] < now
        ):
            start_fan(grill_platform, settings, control["duty_cycle"])
            self.ctx.event_log.debug("Smoke Plus: Fan Returned to On")

        # If Smoke Plus was disabled while fan was ramping return it to the correct duty cycle
        elif (
            settings["platform"]["dc_fan"]
            and current_output_status["pwm"] != control["duty_cycle"]
            and not control["s_plus"]
            and self.state.fan.pwm_ramping
            and self.state.manual_override["fan"] < now
        ):
            self.state.fan.pwm_ramping = False
            grill_platform.set_duty_cycle(control["duty_cycle"])
            self.ctx.event_log.debug("Smoke Plus: Fan Returned to " + str(control["duty_cycle"]) + "% duty cycle")

        # Set Fan Duty Cycle based on Average Grill Temp Using Profile
        elif (
            settings["platform"]["dc_fan"]
            and control["pwm_control"]
            and current_output_status["pwm"] != control["duty_cycle"]
            and self.state.manual_override["fan"] < now
        ):
            grill_platform.set_duty_cycle(control["duty_cycle"])
            self.ctx.event_log.debug("Temp Fan Control: Fan Set to " + str(control["duty_cycle"]) + "% duty cycle")

        # If PWM Fan Control is turned off check current Duty Cycle and set back to max_duty_cycle if required
        elif (
            settings["platform"]["dc_fan"]
            and not control["pwm_control"]
            and current_output_status["pwm"] != settings["pwm"]["max_duty_cycle"]
            and self.state.manual_override["fan"] < now
        ):
            control["duty_cycle"] = settings["pwm"]["max_duty_cycle"]
            self.ctx.store.write_control_snapshot(control, origin="control")
            grill_platform.set_duty_cycle(control["duty_cycle"])
            self.ctx.event_log.debug("Temp Fan Control: Set to OFF, Fan Returned to Max Duty Cycle")

    def _stage_smoke_cycle_metrics(self):
        """Shared `on_metrics_stamped()` body for Startup/Reignite/Smoke: record
        the cycle settings this run started under on the row run() has just
        stamped.

        `p_mode` is what the attached display's P-MODE pill and
        `/api/get/status` report, and both values end up in the cookfile, so a
        run that never stages them reports zeros for its whole life however
        `cycle_data` is configured.

        Runs before `setup_safety()`, which is what lets SmartStart overwrite
        `p_mode` with the selected profile's -- during a ramp the profile is
        the number actually driving the cycle."""
        self.state.metrics["p_mode"] = self.settings["cycle_data"]["PMode"]
        self.state.metrics["auger_cycle_time"] = self.settings["cycle_data"]["SmokeOnCycleTime"]

    def _reload_smoke_cycle_from_settings(self):
        """Shared `on_settings_reload()` body for Startup/Reignite/Smoke
        (identical in all three today). A settings save mid-mode (any save
        that flips `control['settings_update']`, e.g. editing an unrelated
        field from the web UI) must not clobber SmartStart-derived cycle
        timing chosen at mode entry. When SmartStart is enabled, re-derive
        `self.state.cycle.*` from the ALREADY-selected profile
        (`control['smartstart']['profile_selected']` -- selection is a
        mode-entry decision keyed on ambient temp at ignition, made once in
        `setup_safety()`/`select_profile()`, and is never re-selected here)
        via the same `profile_cycle()` setup uses, instead of unconditionally
        overwriting with `smoke_cycle_times(cycle_data)`. If the profiles
        list was shortened by the very settings save being reloaded
        (`profile_selected` now out of range), clamp to the last valid
        index, persist the clamp back to control (should_exit()/a future
        setup_safety() read it back -- an unpersisted clamp would just
        IndexError on the next tick) and log a warning the same way the rest
        of this module does. SmartStart disabled (or no profiles configured
        at all): falls back to the pre-fix `smoke_cycle_times()` path,
        unchanged."""
        settings = self.settings
        control = self.control

        if settings["startup"]["smartstart"]["enabled"]:
            profiles = settings["startup"]["smartstart"]["profiles"]
            profile_selected = control["smartstart"].get("profile_selected", 0)

            if not profiles:
                self.ctx.event_log.warning(
                    "SmartStart enabled but no profiles configured on settings reload; "
                    "falling back to cycle_data timing."
                )
            else:
                if not (0 <= profile_selected < len(profiles)):
                    clamped = len(profiles) - 1
                    self.ctx.event_log.warning(
                        f"SmartStart profile_selected ({profile_selected}) is out of range "
                        f"for {len(profiles)} profile(s) on settings reload; clamping to {clamped}."
                    )
                    profile_selected = clamped
                    control["smartstart"]["profile_selected"] = profile_selected
                    self.ctx.store.write_control_snapshot(control, origin="control")

                profile = profiles[profile_selected]
                _ct, startup_timer, _mbits = profile_cycle(profile, settings["cycle_data"])
                self.state.cycle.on_time = _ct.on_time
                self.state.cycle.off_time = _ct.off_time
                self.state.cycle.cycle_time = _ct.cycle_time
                self.state.cycle.ratio = _ct.cycle_ratio
                self.state.cycle.raw_ratio = _ct.cycle_ratio
                self.state.startup.timer = startup_timer
                # Write Metrics (note these will overwrite the previous value)
                self.state.metrics.update(_mbits)
                self.ctx.store.update_metrics(self.state.metrics)
                return

        _ct = smoke_cycle_times(settings["cycle_data"])
        self.state.cycle.on_time = _ct.on_time
        self.state.cycle.off_time = _ct.off_time
        self.state.cycle.cycle_time = _ct.cycle_time
        self.state.cycle.ratio = _ct.cycle_ratio
        self.state.cycle.raw_ratio = _ct.cycle_ratio
        # Write Metrics (note these will overwrite the previous value)
        self.state.metrics["p_mode"] = settings["cycle_data"]["PMode"]
        self.state.metrics["auger_cycle_time"] = settings["cycle_data"]["SmokeOnCycleTime"]
        self.ctx.store.update_metrics(self.state.metrics)

    def _setup_recipe_triggers(self, control):
        """Pre-loop recipe trigger setup (extracted from run()). Mutates control
        in place and writes it when any trigger was set."""
        ctx = self.ctx
        mode = self.name
        if control["mode"] == Mode.RECIPE and mode in [Mode.SMOKE, Mode.HOLD]:
            recipe_trigger_set = False
            if control["recipe"]["step_data"]["timer"] > 0:
                for index, item in enumerate(control["notify_data"]):
                    if item["type"] == "timer":
                        control["notify_data"][index]["req"] = True
                        timer_start = ctx.clock.now()
                        control["timer"]["start"] = timer_start
                        control["timer"]["paused"] = 0
                        control["timer"]["end"] = timer_start + (control["recipe"]["step_data"]["timer"] * 60)
                        control["timer"]["shutdown"] = False
                        control["notify_data"][index]["shutdown"] = False
                        control["notify_data"][index]["keep_warm"] = False
                        recipe_trigger_set = True

            for probe, value in control["recipe"]["step_data"]["trigger_temps"].items():
                if value > 0:
                    for index, item in enumerate(control["notify_data"]):
                        if item["type"] == "probe" and item["label"] == probe:
                            control["notify_data"][index]["target"] = value
                            control["notify_data"][index]["req"] = True
                            recipe_trigger_set = True
                            break

            if recipe_trigger_set:
                ctx.store.write_control_snapshot(control, origin="control")
            else:
                self.ctx.event_log.warning("No trigger set for Hold/Smoke mode in recipe.")

    def _process_control_flags(self, control, now, last, pelletdb):
        """Per-tick settings/distance/hopper/switch flag handling (extracted from
        run()). Mutates control in place; returns (last, pelletdb, should_break)."""
        ctx = self.ctx
        grill_platform = self.grill
        dist_device = self.dist_device

        # Check if user changed settings and reload
        if control["settings_update"]:
            previous_settings = self.settings
            control["settings_update"] = False
            ctx.store.write_control_snapshot(control, origin="control")
            self.settings = ctx.store.read_settings()
            previous_globals = previous_settings.get("globals", {})
            current_globals = self.settings.get("globals", {})
            previous_controller = previous_settings.get("controller", {})
            current_controller = self.settings.get("controller", {})
            previous_selected = previous_controller.get("selected")
            current_selected = current_controller.get("selected")
            previous_configs = previous_controller.get("config", {})
            current_configs = current_controller.get("config", {})
            previous_selected_config = dict(previous_configs.get(previous_selected, {}))
            current_selected_config = dict(current_configs.get(current_selected, {}))
            previous_ambient = previous_selected_config.pop("T_amb", None)
            current_ambient = current_selected_config.pop("T_amb", None)
            reason = None
            if previous_globals.get("units") != current_globals.get("units"):
                reason = TrajectoryBreakReason.UNITS_CHANGED
            elif previous_selected != current_selected or previous_selected_config != current_selected_config:
                reason = TrajectoryBreakReason.STRUCTURE_CHANGED
            elif previous_ambient != current_ambient:
                reason = TrajectoryBreakReason.AMBIENT_SEMANTICS_CHANGED
            elif previous_settings.get("smoke_plus") != self.settings.get("smoke_plus"):
                reason = TrajectoryBreakReason.FAN_MAPPING_CHANGED
            elif (
                previous_settings.get("cycle_data") != self.settings.get("cycle_data")
                or previous_settings.get("pwm") != self.settings.get("pwm")
                or previous_settings.get("platform") != self.settings.get("platform")
            ):
                reason = TrajectoryBreakReason.ACTUATION_MAPPING_CHANGED
            if reason is not None:
                self._emit_trajectory_boundary(
                    reason,
                    now,
                    "settings changed",
                    configuration=True,
                    replacement=True,
                )
            else:
                revision_monotonic_ms, revision_wall_ms = self._trajectory_clock_pair()
                self._trajectory_active_event = self._trajectory_mode_event(
                    revision_monotonic_ms,
                    revision_wall_ms,
                )
            self.probe_complex.set_thermocouple_inference_policy(
                self.settings["thermocouple_health"]["inference_policy"],
                now=now,
            )
            if self.settings["globals"]["debug_mode"]:
                self.ctx.event_log.setLevel(logging.DEBUG)
            else:
                self.ctx.event_log.setLevel(logging.INFO)
            self.on_settings_reload()

        # Check if user changed hopper levels and update if required
        if control["distance_update"]:
            empty = self.settings["pelletlevel"]["empty"]
            full = self.settings["pelletlevel"]["full"]
            dist_device.update_distances(empty, full)
            control["distance_update"] = False
            ctx.store.write_control_snapshot(control, origin="control")

        # A requested check ASKS the sampling thread for a fresh reading and
        # returns immediately -- mid-cook, this loop is timing the auger and
        # igniter and must not wait on a sensor for any length of time. The
        # requested reading is published by the timed refresh below, which is
        # deliberately not restamped here.
        if control["hopper_check"]:
            control["hopper_check"] = False
            ctx.store.write_control_snapshot(control, origin="control")
            dist_device.request_sample()
            self.ctx.event_log.info("Hopper Level Check requested.")

        # Automatic refresh every HOPPER_LEVEL_REFRESH_INTERVAL seconds. (The
        # literal here used to be 60, under a comment claiming 300.) Reads a
        # value the sampling thread already produced; get_level() has no
        # blocking path. Not logged -- several times a minute, for a whole cook,
        # would bury the event log the requests above are visible in.
        if (now - self.state.timers.hopper_toggle) > HOPPER_LEVEL_REFRESH_INTERVAL:
            pelletdb = ctx.store.read_pellet_db()
            pelletdb["current"]["hopper_level"] = dist_device.get_level()
            ctx.store.write_pellet_db(pelletdb)
            self.state.timers.hopper_toggle = now

        # Check for update in ON/OFF Switch
        if not self.settings["platform"]["standalone"] and last != grill_platform.get_input_status():
            last = grill_platform.get_input_status()
            if not last:
                self.ctx.event_log.info("Switch set to off, going to monitor mode.")
                # request_transition sets mode="Stop"/updated + writes; status is
                # not part of the transition, so set it on control first (single
                # OVERWRITE).
                control["status"] = StatusState.ACTIVE
                request_transition(ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
                return (last, pelletdb, True)

        return (last, pelletdb, False)

    def _apply_manual_overrides(self, control, now, current_output_status):
        """Per-tick manual output overrides (extracted from run()). Mutates control
        and self.state.manual_override in place."""
        self._last_now = now
        ctx = self.ctx
        mode = self.name
        grill_platform = self.grill
        manual_override = self.state.manual_override

        if (mode == Mode.MANUAL or self.settings["safety"]["allow_manual_changes"]) and control["manual"]["change"] in [
            "power",
            "igniter",
            "fan",
            "auger",
            "pwm",
        ]:
            if control["manual"]["change"] in {"fan", "auger", "pwm"}:
                self._emit_trajectory_boundary(
                    TrajectoryBreakReason.MANUAL,
                    now,
                    f"manual-{control['manual']['change']}",
                )
            if mode != Mode.MANUAL:
                override_time = now + self.settings["safety"]["manual_override_time"]
            else:
                override_time = 0

            if control["manual"]["change"] == "fan":
                if control["manual"]["output"] and not current_output_status["fan"]:
                    grill_platform.fan_on()
                    self.ctx.event_log.debug("Fan ON")
                elif not control["manual"]["output"] and current_output_status["fan"]:
                    grill_platform.fan_off()
                    self.ctx.event_log.debug("Fan OFF")
                manual_override["fan"] = override_time
                self._on_manual_output("fan", control["manual"]["output"])

            if control["manual"]["change"] == "auger":
                if control["manual"]["output"] and not current_output_status["auger"]:
                    grill_platform.auger_on()
                    self.ctx.event_log.debug("Auger ON")
                elif not control["manual"]["output"] and current_output_status["auger"]:
                    grill_platform.auger_off()
                    self.ctx.event_log.debug("Auger OFF")
                manual_override["auger"] = override_time
                self._on_manual_output("auger", control["manual"]["output"])

            if control["manual"]["change"] == "igniter":
                if control["manual"]["output"] and not current_output_status["igniter"]:
                    grill_platform.igniter_on()
                    self.ctx.event_log.debug("Igniter ON")
                elif not control["manual"]["output"] and current_output_status["igniter"]:
                    grill_platform.igniter_off()
                    self.ctx.event_log.debug("Igniter OFF")
                manual_override["igniter"] = override_time
                self._on_manual_output("igniter", control["manual"]["output"])

            if control["manual"]["change"] == "power":
                if control["manual"]["output"] and not current_output_status["power"]:
                    grill_platform.power_on()
                    self.ctx.event_log.debug("Power ON")
                elif not control["manual"]["output"] and current_output_status["power"]:
                    grill_platform.power_off()
                    self.ctx.event_log.debug("Power OFF")
                manual_override["power"] = override_time
                self._on_manual_output("power", control["manual"]["output"])

            if (
                self.settings["platform"]["dc_fan"]
                and control["manual"]["change"] == "pwm"
                and current_output_status["fan"]
                and control["manual"]["pwm"] != current_output_status["pwm"]
            ):
                speed = control["manual"]["pwm"]
                self.ctx.event_log.debug("PWM Speed: " + str(speed) + "%")
                grill_platform.set_duty_cycle(speed)
                manual_override["pwm"] = override_time
                # Fires only when this branch's own gate accepts the request --
                # before the reset below wipes the applied speed.
                self._on_manual_output("pwm", speed)
                control["manual"]["pwm"] = 100  # Reset PWM

            # Reset to False (not None) to match default_control()'s seed and
            # keep control free of dict-nested nulls: every consumer treats
            # these as falsy (== 'pwm', `in [...]`, truthiness), so behavior is
            # identical, and a null here would be a delete under json_patch merge.
            control["manual"]["change"] = False
            control["manual"]["output"] = False
            ctx.store.write_control_snapshot(control, origin="control")

    def _duty_snapshot(self, control, mode, outputs):
        """The duty driving this tick: what the auger is commanded to, and what the fan got.

        One implementation, two consumers -- the status blob the dashboard
        renders and the history row the chart plots. Both are written from this
        loop but at different cadences (0.5s and 3s), and a second copy of this
        logic would drift on exactly the branches nobody checks: Manual mode's
        auger-bool coercion and the dc_fan PWM split. A dashboard reading 0%
        beside a history row plotting 100% for the same instant is a
        disagreement no one would think to look for.

        `outputs` is a fresh grill_platform.get_output_status(). Fan duty gates
        on the output in every branch rather than on the request:
        control['duty_cycle'] is the duty the fan WOULD be given, and reporting
        it for a fan that is off puts "FAN DUTY 100%" beside "FAN IDLE".

        `cycle_ratio` is what the controller COMMANDED -- the same quantity
        the dashboard's duty tile has always shown. `realized_cycle_ratio` is
        what the framed-pulse machinery measured actually reaching the auger,
        and is None in modes that do not measure it. The gap between the two is
        where a clamp acted.
        """
        if mode == Mode.MANUAL:
            # Manual has no controller and no cycle: the auger is simply on or
            # off, and the fan's duty is whatever the operator's PWM is set to.
            ratio = 1.0 if outputs.get("auger") else 0.0
            pwm = int(outputs.get("pwm", 0) or 0)
        else:
            ratio = round(self.state.cycle.ratio, 2)
            pwm = int(control.get("duty_cycle", 0) or 0)

        if not outputs.get("fan"):
            fan_duty = 0
        elif self.settings["platform"].get("dc_fan"):
            fan_duty = pwm
        else:
            fan_duty = 100

        return {"cycle_ratio": ratio, "realized_cycle_ratio": self.realized_cycle_ratio(), "fan_duty": fan_duty}

    def _build_status_data(self, control, pelletdb, start_time):
        """Build the per-0.5s display status dict (extracted from run()). Returns a
        fresh, fully-populated dict; the caller writes it to the store."""
        mode = self.name
        grill_platform = self.grill
        status_data = {}
        status_data["notify_data"] = control["notify_data"]
        status_data["timer"] = control["timer"]
        status_data["s_plus"] = control["s_plus"]
        status_data["hopper_level_enabled"] = self.settings["modules"]["dist"] != "none"
        status_data["hopper_level"] = pelletdb["current"]["hopper_level"]
        status_data["units"] = self.settings["globals"]["units"]
        status_data["mode"] = mode
        status_data["recipe"] = control["mode"] == Mode.RECIPE
        status_data["start_time"] = start_time
        status_data["start_duration"] = self.state.startup.timer
        status_data["shutdown_duration"] = self.settings["shutdown"]["shutdown_duration"]
        status_data["prime_duration"] = 0
        status_data["prime_amount"] = 0
        status_data["lid_open_detected"] = False
        status_data["lid_open_endtime"] = 0
        status_data["p_mode"] = self.state.metrics.get("p_mode", None)
        status_data["startup_timestamp"] = control["startup_timestamp"]
        if control["mode"] == Mode.RECIPE:
            status_data["recipe_paused"] = bool(
                control["recipe"]["step_data"]["triggered"] and control["recipe"]["step_data"]["pause"]
            )
        else:
            status_data["recipe_paused"] = False
        status_data["outpins"] = {}
        current = grill_platform.get_output_status()
        for item in self.settings["platform"]["outputs"]:
            try:
                status_data["outpins"][item] = current[item]
            except KeyError:
                continue
        duty = self._duty_snapshot(control, mode, current)
        status_data["cycle_ratio"] = duty["cycle_ratio"]
        status_data["fan_duty"] = duty["fan_duty"]
        # requested_cycle_ratio is deliberately NOT published to status: the
        # dashboard's duty tiles report what the grill is doing, and the
        # requested-vs-applied gap is a history-chart reading, not a live one.
        # ---- mode-specific status fields ----
        status_data.update(self.status_fragment())
        return status_data

    def _handle_recipe_end(self, control):
        """End-of-loop recipe step check (extracted from run()). Returns True when
        the work loop must break."""
        ctx = self.ctx
        if control["mode"] == Mode.RECIPE:
            if control["recipe"]["step_data"]["triggered"] and not control["recipe"]["step_data"]["pause"]:
                if control["recipe"]["step_data"]["notify"]:
                    ctx.notifications.send("Recipe_Step_Message")
                return True
            elif control["recipe"]["step_data"]["triggered"] and control["recipe"]["step_data"]["pause"]:
                if control["recipe"]["step_data"]["notify"]:
                    ctx.notifications.send("Recipe_Step_Message")
                    control["recipe"]["step_data"]["notify"] = False
                    ctx.store.write_control_snapshot(control, origin="control")
                # Continue until 'pause' variable is cleared
        return False

    def _read_probes_with_excitation(self, now=None):
        settings = self.settings
        control = self.control
        if settings is None or control is None:
            raise RuntimeError("thermocouple excitation requires loaded control settings")
        if now is None:
            now = self.ctx.clock.now()
        output_status = self.grill.get_output_status()
        elapsed = 0.0 if self._excitation_last_read_at is None else max(0.0, now - self._excitation_last_read_at)
        delivered_heat_on_s = (
            elapsed if output_status.get("auger", False) or output_status.get("igniter", False) else 0.0
        )
        self._excitation_last_read_at = now
        primary_setpoint_c = float(control["primary_setpoint"])
        if settings["globals"]["units"] == "F":
            primary_setpoint_c = (primary_setpoint_c - 32.0) * 5.0 / 9.0
        excitation = ThermocoupleExcitationContext(
            active_cook=self.name in _ACTIVE_THERMOCOUPLE_INFERENCE_MODES,
            primary_setpoint_c=primary_setpoint_c,
            delivered_heat_on_s=delivered_heat_on_s,
        )
        sensor_data = self.probe_complex.read_probes(
            excitation=excitation,
            now=now,
        )
        self.ctx.store.write_generic_key(
            "probe_device_info",
            self.probe_complex.get_device_info(),
        )
        return sensor_data, output_status

    def _process_thermocouple_health(self, sensor_data) -> bool:
        reports = self.probe_complex.get_thermocouple_health()
        transitions = self.probe_complex.consume_thermocouple_health_transitions()
        primary_label = next(iter(sensor_data["primary"]), None)

        for transition in transitions:
            event = _thermocouple_transition_event(transition, primary_label)
            if event is None:
                continue
            self.ctx.event_log.info(_thermocouple_transition_log(transition, primary_label))
            self.ctx.notifications.send(event)

        primary = reports.get(primary_label)
        if primary is None or not primary.confirmed or primary.temperature_valid:
            return False
        hardware_authority = ThermocoupleEvidence.HARDWARE in primary.evidence
        if not hardware_authority and primary.detail.get("authority") != "stop":
            return False
        request_transition(
            self.ctx,
            self.control,
            Mode.ERROR,
            kind=TransitionKind.SAFETY,
            display=("text", "ERROR"),
        )
        return True

    # ---- shared skeleton ----
    def run(self):
        ctx = self.ctx
        mode = self.name
        grill_platform = self.grill
        probe_complex = self.probe_complex

        # Setup Process Monitor and Start
        monitor = Process_Monitor("control", restart_control, timeout=30)
        monitor.start_monitor()

        # Precondition for entering into main control loop
        status = "Active"

        # Setup Cycle Parameters
        self.settings = ctx.store.read_settings()
        control = ctx.store.read_control()
        self.control = control
        pelletdb = ctx.store.read_pellet_db()
        control["hopper_check"] = True
        ctx.store.write_control_snapshot(control, origin="control")

        self.ctx.event_log.info(f"{mode} Mode started.")

        # Pre-Loop Setup Recipe Triggers
        self._setup_recipe_triggers(control)

        # Get ON/OFF Switch state and set as last state
        last = grill_platform.get_input_status()

        # Set DC fan frequency if it has changed since init
        if self.settings["platform"]["dc_fan"]:
            pwm_frequency = self.settings["pwm"]["frequency"]
            frequency_status = grill_platform.get_output_status()
            if pwm_frequency != frequency_status["frequency"]:
                grill_platform.set_pwm_frequency(pwm_frequency)

        # Set Starting Configuration for Igniter, Fan, Auger
        grill_platform.igniter_off()
        grill_platform.auger_off()

        trajectory_entry_monotonic_ms, trajectory_entry_wall_ms = self._trajectory_clock_pair()
        self._emit_trajectory_mode_entered(
            trajectory_entry_monotonic_ms,
            trajectory_entry_wall_ms,
        )
        preflight_data, _ = self._read_probes_with_excitation()
        preflight_ptemp = next(iter(preflight_data["primary"].values()), None)
        preflight_monotonic_ms, preflight_wall_ms = self._trajectory_clock_pair()
        self._emit_trajectory_temperature(
            preflight_data,
            preflight_ptemp,
            preflight_monotonic_ms,
            preflight_wall_ms,
        )
        last_valid_ptemp = preflight_ptemp if isinstance(preflight_ptemp, (int, float)) else None
        if self._process_thermocouple_health(preflight_data):
            grill_platform.fan_off()
            grill_platform.power_off()
            fault_monotonic_ms, fault_wall_ms = self._trajectory_clock_pair()
            self._emit_trajectory_boundary(
                TrajectoryBreakReason.SAFETY,
                ctx.clock.now(),
                "preflight-thermocouple-fault",
            )
            self._emit_trajectory_mode_exited(
                control,
                fault_monotonic_ms,
                fault_wall_ms,
                next_effective_mode=Mode.ERROR,
                exit_reason=TrajectoryBreakReason.ERROR,
            )
            monitor.stop_monitor()
            self.ctx.event_log.error("Primary thermocouple fault blocked mode setup.")
            return ()

        # ---- mode-specific pre-loop setup ----
        self.setup()
        retained_metrics = ctx.store.read_all_metrics()
        retained_id = (
            retained_metrics[0].get("id")
            if retained_metrics and retained_metrics[0].get("mode") == Mode.PRIME
            else None
        )
        control = self._refresh_cook_identity(control, preferred=retained_id)

        self._stamp_mode_metric(control, pelletdb)

        # Get initial probe sensor data, temperatures
        sensor_data, _ = self._read_probes_with_excitation()
        ptemp = next(iter(sensor_data["primary"].values()))  # Primary Temperature or the Pit Temperature
        sample_monotonic_ms, sample_wall_ms = self._trajectory_clock_pair()
        self._emit_trajectory_temperature(
            sensor_data,
            ptemp,
            sample_monotonic_ms,
            sample_wall_ms,
        )

        # ---- thermocouple health precedes mode-specific numeric safety ----
        if self._process_thermocouple_health(sensor_data):
            self._emit_trajectory_boundary(
                TrajectoryBreakReason.SAFETY,
                ctx.clock.now(),
                "thermocouple-fault",
            )
            self._on_safety_event("thermocouple_fault", ctx.clock.now())
            status = "Inactive"
        else:
            if isinstance(ptemp, (int, float)):
                last_valid_ptemp = ptemp
            status = self.setup_safety(ptemp)

        # Apply Smart Start Settings if Enabled (default; Startup/Reignite/Smoke
        # override self.state.startup.timer from their own setup())
        self.state.startup.timer = self.settings["startup"]["duration"]

        # Set the start time
        start_time = ctx.clock.now()
        self.state.timers.start_time = start_time

        # ---- declarative pre_loop guards: the flameout edges live here instead
        # of in setup_safety. A fired guard aborts the loop exactly as
        # setup_safety returning "Inactive" does. This reuses start_time (no
        # extra clock read) -- the pre_loop flameout guards do not use `now`. ----
        if status == "Active" and evaluate_phase(self, ctx, "pre_loop", start_time, ptemp):
            status = "Inactive"

        # Set time since toggle for temperature
        self.state.timers.temp_toggle = start_time
        # Set time since toggle for checking ETA
        self.state.timers.eta_toggle = start_time
        # Set time since toggle for auger
        self.state.timers.auger_toggle = start_time
        # Set time since toggle for display
        self.state.timers.display_toggle = start_time
        # Initializing Start Time for Fan
        self.state.fan.cycle_toggle_time = start_time
        # Set time since toggle for hopper check
        self.state.timers.hopper_toggle = start_time
        # Set time since fan speed update
        self.state.fan.update_time = start_time

        # Setup Display Data
        status_data = {}
        in_data = {}

        # Clear Manual Overrides
        manual_override = {"igniter": 0, "auger": 0, "fan": 0, "power": 0, "pwm": 0}
        self.state.manual_override = manual_override

        # ============ Main Work Cycle ============
        while status == "Active":
            now = ctx.clock.now()

            stamp_control_heartbeat(ctx)

            ctx.store.execute_control_writes()
            control = self._refresh_cook_identity(
                ctx.store.read_control(),
                now=now,
            )

            control = process_system_commands(
                ctx,
                clear_history=partial(self._handle_history_clear, now=now),
            )
            self.control = control

            if control["updated"]:
                if control["mode"] in (Mode.STOP, Mode.ERROR):
                    self._on_safety_event(str(control["mode"]).lower(), now)
                break

            # Per-tick settings/distance/hopper/switch flag handling
            last, pelletdb, _should_break = self._process_control_flags(control, now, last, pelletdb)
            if _should_break:
                break

            # Grab current probe profiles if they have changed since the last loop.
            if control["probe_profile_update"]:
                self.settings = ctx.store.read_settings()
                control["probe_profile_update"] = False
                ctx.store.write_control_snapshot(control, origin="control")
                probe_complex.update_probe_profiles(self.settings["probe_settings"]["probe_map"]["probe_info"])

            # ---- SENSE: single fresh probe read for the whole tick ----
            sensor_data, current_output_status = self._read_probes_with_excitation(now)
            ptemp = next(iter(sensor_data["primary"].values()))  # Primary Temperature or the Pit Temperature
            sample_monotonic_ms, sample_wall_ms = self._trajectory_clock_pair()
            self._emit_trajectory_temperature(
                sensor_data,
                ptemp,
                sample_monotonic_ms,
                sample_wall_ms,
            )

            in_data["probe_history"] = sensor_data
            in_data["primary_setpoint"] = control["primary_setpoint"] if mode == Mode.HOLD else 0
            in_data["notify_targets"] = ctx.notifications.get_targets(control["notify_data"])

            # Save current data to the database
            ctx.store.write_current(in_data)

            # Write Tr data to the database if in tuning mode
            if control["tuning_mode"]:
                ctx.store.write_tr(in_data["probe_history"]["tr"])

            # ---- HEALTH (before numeric safety or actuation) ----
            if self._process_thermocouple_health(sensor_data):
                self._emit_trajectory_boundary(
                    TrajectoryBreakReason.SAFETY,
                    now,
                    "thermocouple-fault",
                )
                self._on_safety_event("thermocouple_fault", now)
                break
            if isinstance(ptemp, (int, float)):
                last_valid_ptemp = ptemp
            # Manual outputs are fenced behind the fresh primary health check.
            self._apply_manual_overrides(control, now, current_output_status)

            # ---- SAFETY (before mode-specific actuation) ----
            # Declarative pre_act guards, evaluated BEFORE the merged on_tick so
            # an unsafe temperature breaks the loop without cycling the auger or
            # advancing the controller. GUARDS["*"]["pre_act"] holds the UNIVERSAL
            # max-temp trip (walked first, so it keeps priority), then the mode's
            # flameout edges (GUARDS["Smoke"]/["Hold"]). A fired guard breaks.
            if evaluate_phase(self, ctx, "pre_act", now, ptemp):
                self._emit_trajectory_boundary(
                    TrajectoryBreakReason.SAFETY,
                    now,
                    "temperature-guard",
                )
                self._on_safety_event("temperature_guard", now)
                break

            # ---- mode-specific per-tick safety check (base default no-op now
            # that Smoke/Hold flameout are declarative guards; the hook remains
            # for any future mode override) ----
            if self.check_safety(now, ptemp):
                self._emit_trajectory_boundary(
                    TrajectoryBreakReason.SAFETY,
                    now,
                    "mode-safety",
                )
                break

            # ---- ACT: merged mode-specific per-tick control/auger/fan logic ----
            self.on_tick(now, ptemp, current_output_status)

            # The duty that drove this tick, captured AFTER on_tick: on_tick is
            # what sets the cycle ratio and moves the outputs, so reading it any
            # earlier records the previous tick's duty against this tick's
            # temperatures. The history write below fires every 3s and takes
            # whatever the most recent tick left here.
            in_data["duty"] = self._duty_snapshot(control, mode, grill_platform.get_output_status())

            # ---- PUBLISH ----
            # Every 20 seconds, update ETA for any pending notifications
            if (now - self.state.timers.eta_toggle) > 20:
                self.state.timers.eta_toggle = ctx.clock.now()
                update_eta = True
            else:
                update_eta = False
            control = ctx.notifications.check(
                self.settings,
                control,
                in_data=in_data,
                pelletdb=pelletdb,
                grill_platform=grill_platform,
                update_eta=update_eta,
            )
            self.control = control
            self.on_publish(now)

            # Send Current Status / Temperature Data to Display Device every 0.5 second
            if (now - self.state.timers.display_toggle) > 0.5:
                status_data = self._build_status_data(control, pelletdb, start_time)
                ctx.store.write_status(status_data)
                self.state.timers.display_toggle = ctx.clock.now()

            # Write History & Issue Heartbeat after 3 seconds has passed
            if (now - self.state.timers.temp_toggle) > 3:
                self.state.timers.temp_toggle = ctx.clock.now()
                ext_data = bool(self.settings["globals"]["ext_data"])
                ctx.store.write_history(in_data, ext_data=ext_data)
                monitor.heartbeat()

            # ---- mode-specific per-tick exit condition ----
            if self.should_exit(now, ptemp):
                break

            # End of Loop Recipe Check
            if self._handle_recipe_end(control):
                break

            ctx.clock.sleep(0.05)

        # *********
        # END Mode Loop
        # *********

        trajectory_exit_monotonic_ms, trajectory_exit_wall_ms = self._trajectory_clock_pair()

        # Clean-up and Exit
        grill_platform.auger_off()
        grill_platform.igniter_off()

        self.ctx.event_log.debug("Auger OFF, Igniter OFF")

        # ---- mode-specific teardown ----
        self.teardown(last_valid_ptemp)
        if mode == Mode.HOLD:
            trajectory_exit_monotonic_ms, trajectory_exit_wall_ms = self._trajectory_clock_pair()
        self._emit_trajectory_mode_exited(
            control,
            trajectory_exit_monotonic_ms,
            trajectory_exit_wall_ms,
        )

        self.ctx.event_log.info(f"{mode} mode ended.")

        # Save Pellets Used
        pelletdb = ctx.store.read_pellet_db()
        pelletdb["current"]["est_usage"] += self.state.metrics["augerontime"] * self.settings["globals"]["augerrate"]
        ctx.store.write_pellet_db(pelletdb)

        # Log the end time
        self.state.metrics["endtime"] = ctx.clock.now() * 1000
        self.state.metrics["pellet_level_end"] = pelletdb["current"]["hopper_level"]
        ctx.store.update_metrics(self.state.metrics)

        monitor.stop_monitor()

        if status_data != {}:
            status_data["mode"] = control["mode"]

        return ()
