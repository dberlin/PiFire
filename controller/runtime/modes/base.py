"""ControlMode template-method base: the SHARED skeleton of a single
control work cycle.

Concrete subclasses (Monitor, Manual, ...) override the hooks below to
supply their mode-specific behavior. Each tick follows a strict
sense -> safety -> act -> publish order: read the probes ONCE at the top of
the tick, run the universal max-temp check and the mode `check_safety`
BEFORE any actuation, then a single merged `on_tick` that does the
controller/auger/fan work on that fresh temperature, then publish status and
history. `current_output_status` is likewise captured once per tick (before
the manual-override block) and threaded through the whole tick.
"""

import logging

from common.modes import Mode, StatusState
from common.process_mon import Process_Monitor
from distance.intervals import HOPPER_LEVEL_REFRESH_INTERVAL
from controller.runtime.heartbeat import stamp_control_heartbeat
from controller.runtime.logic.cycle import smoke_cycle_times
from controller.runtime.logic.fan import start_fan
from controller.runtime.logic.pwm import ramp_params
from controller.runtime.logic.smartstart import profile_cycle
from controller.runtime.system_commands import process_system_commands
from controller.runtime.transitions import request_transition, evaluate_phase, TransitionKind


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

    # ---- hooks (safe defaults) ----
    def setup(self):
        pass

    def setup_safety(self, ptemp) -> str:
        return "Active"

    def on_tick(self, now, ptemp, current_output_status):
        pass

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
    def _valid_cook_id(cook_id) -> bool:
        return (
            isinstance(cook_id, str)
            and bool(cook_id)
            and cook_id == cook_id.strip()
        )

    def _refresh_cook_identity(self, control, *, now, preferred=None):
        previous_cook_id = (
            self.control.get("cook_id")
            if isinstance(self.control, dict)
            else None
        )
        cook_id = control.get("cook_id")
        if not self._valid_cook_id(cook_id):
            cook_id = self.ctx.store.ensure_cook_id(preferred=preferred)
            control["cook_id"] = cook_id
        self.control = control
        if (
            self._valid_cook_id(previous_cook_id)
            and previous_cook_id != cook_id
        ):
            self.on_cook_identity_rotated(previous_cook_id, cook_id, now)
        return control


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
        if control["mode"] == Mode.RECIPE:
            if mode in [Mode.SMOKE, Mode.HOLD]:
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
            control["settings_update"] = False
            ctx.store.write_control_snapshot(control, origin="control")
            self.settings = ctx.store.read_settings()
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

        if mode == Mode.MANUAL or self.settings["safety"]["allow_manual_changes"]:
            if control["manual"]["change"] in ["power", "igniter", "fan", "auger", "pwm"]:
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
                    and not control["manual"]["pwm"] == current_output_status["pwm"]
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

    def _build_status_data(self, control, pelletdb, start_time):
        """Build the per-0.5s display status dict (extracted from run()). Returns a
        fresh, fully-populated dict; the caller writes it to the store."""
        mode = self.name
        grill_platform = self.grill
        status_data = {}
        status_data["notify_data"] = control["notify_data"]
        status_data["timer"] = control["timer"]
        status_data["s_plus"] = control["s_plus"]
        status_data["hopper_level_enabled"] = False if self.settings["modules"]["dist"] == "none" else True
        status_data["hopper_level"] = pelletdb["current"]["hopper_level"]
        status_data["units"] = self.settings["globals"]["units"]
        status_data["mode"] = mode
        status_data["recipe"] = True if control["mode"] == Mode.RECIPE else False
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
            status_data["recipe_paused"] = (
                True
                if control["recipe"]["step_data"]["triggered"] and control["recipe"]["step_data"]["pause"]
                else False
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
        if mode == Mode.MANUAL:
            status_data["cycle_ratio"] = 1.0 if current.get("auger") else 0.0
            if not current.get("fan"):
                status_data["fan_duty"] = 0
            elif self.settings["platform"].get("dc_fan"):
                status_data["fan_duty"] = int(current.get("pwm", 0) or 0)
            else:
                status_data["fan_duty"] = 100
        else:
            status_data["cycle_ratio"] = round(self.state.cycle.ratio, 2)
            # Both branches gate on the output, as the Manual branch above does:
            # control['duty_cycle'] is the duty the fan WOULD be given, and
            # reporting it for a fan that is off puts "FAN DUTY 100%" beside
            # "FAN IDLE" on the dashboard.
            if not current.get("fan"):
                status_data["fan_duty"] = 0
            elif self.settings["platform"].get("dc_fan"):
                status_data["fan_duty"] = int(control.get("duty_cycle", 0) or 0)
            else:
                status_data["fan_duty"] = 100
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

    # ---- shared skeleton ----
    def run(self):
        ctx = self.ctx
        mode = self.name
        grill_platform = self.grill
        probe_complex = self.probe_complex

        # Setup Process Monitor and Start
        monitor = Process_Monitor("control", ["supervisorctl", "restart", "control"], timeout=30)
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
            if not pwm_frequency == frequency_status["frequency"]:
                grill_platform.set_pwm_frequency(pwm_frequency)

        # Set Starting Configuration for Igniter, Fan, Auger
        grill_platform.igniter_off()
        grill_platform.auger_off()

        # ---- mode-specific pre-loop setup ----
        self.setup()
        retained_metrics = ctx.store.read_all_metrics()
        retained_id = (
            retained_metrics[0].get("id")
            if retained_metrics and retained_metrics[0].get("mode") == Mode.PRIME
            else None
        )
        control = self._refresh_cook_identity(
            control,
            now=ctx.clock.now(),
            preferred=retained_id,
        )

        ctx.store.append_metric()
        self.state.metrics = ctx.store.read_metrics()
        self.state.metrics["mode"] = mode
        self.state.metrics["smokeplus"] = control["s_plus"]
        self.state.metrics["primary_setpoint"] = control["primary_setpoint"]
        self.state.metrics["pellet_level_start"] = pelletdb["current"]["hopper_level"]
        current_pellet_id = pelletdb["current"]["pelletid"]
        pellet_brand = pelletdb["archive"][current_pellet_id]["brand"]
        pellet_type = pelletdb["archive"][current_pellet_id]["wood"]
        self.state.metrics["pellet_brand_type"] = f"{pellet_brand} {pellet_type}"
        self.on_metrics_stamped()
        ctx.store.update_metrics(self.state.metrics)

        # Get initial probe sensor data, temperatures
        sensor_data = probe_complex.read_probes()
        ptemp = list(sensor_data["primary"].values())[0]  # Primary Temperature or the Pit Temperature

        # ---- mode-specific pre-loop safety check (abort contract) ----
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
        if evaluate_phase(self, ctx, "pre_loop", start_time, ptemp):
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

            process_system_commands(ctx)

            if control["updated"]:
                if control["mode"] in (Mode.STOP, Mode.ERROR):
                    self._on_safety_event(str(control["mode"]).lower(), now)
                break

            # Per-tick settings/distance/hopper/switch flag handling
            last, pelletdb, _should_break = self._process_control_flags(control, now, last, pelletdb)
            if _should_break:
                break

            current_output_status = grill_platform.get_output_status()

            self._apply_manual_overrides(control, now, current_output_status)

            # Grab current probe profiles if they have changed since the last loop.
            if control["probe_profile_update"]:
                self.settings = ctx.store.read_settings()
                control["probe_profile_update"] = False
                ctx.store.write_control_snapshot(control, origin="control")
                probe_complex.update_probe_profiles(self.settings["probe_settings"]["probe_map"]["probe_info"])

            # Get probe device info for frontend
            ctx.store.write_generic_key("probe_device_info", probe_complex.get_device_info())

            # ---- SENSE: single fresh probe read for the whole tick ----
            sensor_data = probe_complex.read_probes()
            ptemp = list(sensor_data["primary"].values())[0]  # Primary Temperature or the Pit Temperature

            in_data["probe_history"] = sensor_data
            in_data["primary_setpoint"] = control["primary_setpoint"] if mode == Mode.HOLD else 0
            in_data["notify_targets"] = ctx.notifications.get_targets(control["notify_data"])

            # If Extended Data Mode is Enabled, Populate Extra Data Here
            if self.settings["globals"]["ext_data"]:
                in_data["ext_data"] = {}
                in_data["ext_data"]["CR"] = 0
                in_data["ext_data"]["RCR"] = 0

            # Save current data to the database
            ctx.store.write_current(in_data)

            # Write Tr data to the database if in tuning mode
            if control["tuning_mode"]:
                ctx.store.write_tr(in_data["probe_history"]["tr"])

            # ---- SAFETY (before any actuation) ----
            # Declarative pre_act guards, evaluated BEFORE the merged on_tick so
            # an unsafe temperature breaks the loop without cycling the auger or
            # advancing the controller. GUARDS["*"]["pre_act"] holds the UNIVERSAL
            # max-temp trip (walked first, so it keeps priority), then the mode's
            # flameout edges (GUARDS["Smoke"]/["Hold"]). A fired guard breaks.
            if evaluate_phase(self, ctx, "pre_act", now, ptemp):
                self._on_safety_event("temperature_guard", now)
                break

            # ---- mode-specific per-tick safety check (base default no-op now
            # that Smoke/Hold flameout are declarative guards; the hook remains
            # for any future mode override) ----
            if self.check_safety(now, ptemp):
                break

            # ---- ACT: merged mode-specific per-tick control/auger/fan logic ----
            self.on_tick(now, ptemp, current_output_status)

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
                ext_data = True if self.settings["globals"]["ext_data"] else False
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

        # Clean-up and Exit
        grill_platform.auger_off()
        grill_platform.igniter_off()

        self.ctx.event_log.debug("Auger OFF, Igniter OFF")

        # ---- mode-specific teardown ----
        self.teardown(ptemp)

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
