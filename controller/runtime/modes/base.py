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

from common.common import WriteKind
from common.modes import Mode
from common.process_mon import Process_Monitor
from controller.runtime.logic.fan import start_fan
from controller.runtime.logic.pwm import ramp_params
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

    # ---- shared helpers ----
    def _auger_cycle_tick(self, now, current_output_status):
        """Shared (non-Hold) auger toggle: turn the auger on/off based on
        elapsed time vs. cycle_time/cycle_ratio, honoring manual overrides and
        accumulating augerontime metrics on auger-off. Hold overrides
        `_on_auger_on` to also recompute OnTime/OffTime/CycleTime and publish
        MQTT PID info -- that part is NOT reproduced here."""
        import control as _control

        if self.state.manual_override["auger"] < now:
            self.state.manual_override["auger"] = 0
            # If Auger is OFF and time since toggle is greater than Off Time
            if not current_output_status["auger"] and (now - self.state.timers.auger_toggle) > (
                self.state.cycle.cycle_time * (1 - self.state.cycle.ratio)
            ):
                self.grill.auger_on()
                self.state.timers.auger_toggle = now
                _control.eventLogger.debug("Cycle Event: Auger On")
                self._on_auger_on(now)

            # If Auger is ON and time since toggle is greater than On Time
            if current_output_status["auger"] and (now - self.state.timers.auger_toggle) > (
                self.state.cycle.cycle_time * self.state.cycle.ratio
            ):
                self.grill.auger_off()
                # Add auger ON time to the metrics
                self.state.metrics["augerontime"] += now - self.state.timers.auger_toggle
                self.ctx.store.write_metrics(self.state.metrics)
                # Set current last toggle time to now
                self.state.timers.auger_toggle = now
                _control.eventLogger.debug("Cycle Event: Auger Off")

    def _smoke_plus_fan_tick(self, now, ptemp, current_output_status):
        """Smoke-plus fan cycling + the elif restore chain. Gated to Smoke
        always, and Hold only once target_temp_achieved -- Hold's on_tick runs
        the Hold-only lid-open/PWM-duty-from-temp/fan-assist parts BEFORE
        calling this helper. `ptemp` is the fresh probe reading for this
        tick."""
        import control as _control

        settings = self.settings
        control = self.control
        grill_platform = self.grill

        # If in Smoke Plus Mode but not calling for fan pid control, Cycle the Fan
        if (
            (self.name == Mode.SMOKE or (self.name == Mode.HOLD and self.state.target_temp_achieved))
            and control["s_plus"]
            and not self.state.fan.assist
            and not self.state.lid.open_detected
        ):
            # If Temperature is > settings['smoke_plus']['max_temp']
            # or Temperature is < settings['smoke_plus']['min_temp'] then turn on fan
            if (
                ptemp > settings["smoke_plus"]["max_temp"] or ptemp < settings["smoke_plus"]["min_temp"]
            ) and self.state.manual_override["fan"] < now:
                if not current_output_status["fan"]:
                    start_fan(grill_platform, settings, control["duty_cycle"])
                    _control.eventLogger.debug("Smoke Plus: Over or Under Temp Fan ON")
            elif (now - self.state.fan.cycle_toggle_time) > settings["smoke_plus"]["on_time"] and current_output_status[
                "fan"
            ]:
                if self.state.manual_override["fan"] < now:
                    self.state.manual_override["fan"] = 0
                    grill_platform.fan_off()
                    self.state.fan.cycle_toggle_time = now
                    _control.eventLogger.debug("Smoke Plus: Fan OFF")
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
                    _control.eventLogger.debug("Smoke Plus: Fan Ramping Up")
                else:
                    start_fan(grill_platform, settings, control["duty_cycle"])
                    _control.eventLogger.debug("Smoke Plus: Fan ON")

        # If Smoke Plus was disabled when fan is OFF return fan to ON
        elif (
            not current_output_status["fan"]
            and not control["s_plus"]
            and not self.state.fan.assist
            and not self.state.lid.open_detected
            and self.state.manual_override["fan"] < now
        ):
            start_fan(grill_platform, settings, control["duty_cycle"])
            _control.eventLogger.debug("Smoke Plus: Fan Returned to On")

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
            _control.eventLogger.debug("Smoke Plus: Fan Returned to " + str(control["duty_cycle"]) + "% duty cycle")

        # Set Fan Duty Cycle based on Average Grill Temp Using Profile
        elif (
            settings["platform"]["dc_fan"]
            and control["pwm_control"]
            and current_output_status["pwm"] != control["duty_cycle"]
            and self.state.manual_override["fan"] < now
        ):
            grill_platform.set_duty_cycle(control["duty_cycle"])
            _control.eventLogger.debug("Temp Fan Control: Fan Set to " + str(control["duty_cycle"]) + "% duty cycle")

        # If PWM Fan Control is turned off check current Duty Cycle and set back to max_duty_cycle if required
        elif (
            settings["platform"]["dc_fan"]
            and not control["pwm_control"]
            and current_output_status["pwm"] != settings["pwm"]["max_duty_cycle"]
            and self.state.manual_override["fan"] < now
        ):
            control["duty_cycle"] = settings["pwm"]["max_duty_cycle"]
            self.ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            grill_platform.set_duty_cycle(control["duty_cycle"])
            _control.eventLogger.debug("Temp Fan Control: Set to OFF, Fan Returned to Max Duty Cycle")

    def _setup_recipe_triggers(self, control):
        """Pre-loop recipe trigger setup (extracted from run()). Mutates control
        in place and writes it when any trigger was set."""
        import control as _control  # module global: eventLogger

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
                    ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                else:
                    _control.eventLogger.warning("No trigger set for Hold/Smoke mode in recipe.")

    def _process_control_flags(self, control, now, last, pelletdb):
        """Per-tick settings/distance/hopper/switch flag handling (extracted from
        run()). Mutates control in place; returns (last, pelletdb, should_break)."""
        import control as _control  # module global: eventLogger

        ctx = self.ctx
        grill_platform = self.grill
        dist_device = self.dist_device

        # Check if user changed settings and reload
        if control["settings_update"]:
            control["settings_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            self.settings = ctx.store.read_settings()
            if self.settings["globals"]["debug_mode"]:
                _control.eventLogger.setLevel(logging.DEBUG)
            else:
                _control.eventLogger.setLevel(logging.INFO)
            self.on_settings_reload()

        # Check if user changed hopper levels and update if required
        if control["distance_update"]:
            empty = self.settings["pelletlevel"]["empty"]
            full = self.settings["pelletlevel"]["full"]
            dist_device.update_distances(empty, full)
            control["distance_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")

        # Check hopper level when requested or every 300 seconds
        if control["hopper_check"] or (now - self.state.timers.hopper_toggle) > 60:
            pelletdb = ctx.store.read_pellet_db()
            override = False
            if control["hopper_check"]:
                control["hopper_check"] = False
                ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                override = True
            pelletdb["current"]["hopper_level"] = dist_device.get_level(override=override)
            ctx.store.write_pellet_db(pelletdb)
            self.state.timers.hopper_toggle = now
            _control.eventLogger.info("Hopper Level Checked @ " + str(pelletdb["current"]["hopper_level"]) + "%")

        # Check for update in ON/OFF Switch
        if not self.settings["platform"]["standalone"] and last != grill_platform.get_input_status():
            last = grill_platform.get_input_status()
            if not last:
                _control.eventLogger.info("Switch set to off, going to monitor mode.")
                # The seam sets mode="Stop"/updated + writes; status is not part
                # of the transition, so set it on control first (single OVERWRITE).
                control["status"] = "active"
                request_transition(ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
                return (last, pelletdb, True)

        return (last, pelletdb, False)

    def _apply_manual_overrides(self, control, now, current_output_status):
        """Per-tick manual output overrides (extracted from run()). Mutates control
        and self.state.manual_override in place."""
        import control as _control  # module global: eventLogger

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
                        _control.eventLogger.debug("Fan ON")
                    elif not control["manual"]["output"] and current_output_status["fan"]:
                        grill_platform.fan_off()
                        _control.eventLogger.debug("Fan OFF")
                    manual_override["fan"] = override_time

                if control["manual"]["change"] == "auger":
                    if control["manual"]["output"] and not current_output_status["auger"]:
                        grill_platform.auger_on()
                        _control.eventLogger.debug("Auger ON")
                    elif not control["manual"]["output"] and current_output_status["auger"]:
                        grill_platform.auger_off()
                        _control.eventLogger.debug("Auger OFF")
                    manual_override["auger"] = override_time

                if control["manual"]["change"] == "igniter":
                    if control["manual"]["output"] and not current_output_status["igniter"]:
                        grill_platform.igniter_on()
                        _control.eventLogger.debug("Igniter ON")
                    elif not control["manual"]["output"] and current_output_status["igniter"]:
                        grill_platform.igniter_off()
                        _control.eventLogger.debug("Igniter OFF")
                    manual_override["igniter"] = override_time

                if control["manual"]["change"] == "power":
                    if control["manual"]["output"] and not current_output_status["power"]:
                        grill_platform.power_on()
                        _control.eventLogger.debug("Power ON")
                    elif not control["manual"]["output"] and current_output_status["power"]:
                        grill_platform.power_off()
                        _control.eventLogger.debug("Power OFF")
                    manual_override["power"] = override_time

                if (
                    self.settings["platform"]["dc_fan"]
                    and control["manual"]["change"] == "pwm"
                    and current_output_status["fan"]
                    and not control["manual"]["pwm"] == current_output_status["pwm"]
                ):
                    speed = control["manual"]["pwm"]
                    _control.eventLogger.debug("PWM Speed: " + str(speed) + "%")
                    grill_platform.set_duty_cycle(speed)
                    manual_override["pwm"] = override_time
                    control["manual"]["pwm"] = 100  # Reset PWM

                # Reset to False (not None) to match default_control()'s seed and
                # keep control free of dict-nested nulls: every consumer treats
                # these as falsy (== 'pwm', `in [...]`, truthiness), so behavior is
                # identical, and a null here would be a delete under json_patch merge.
                control["manual"]["change"] = False
                control["manual"]["output"] = False
                ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")

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
        status_data["cycle_ratio"] = round(self.state.cycle.ratio, 2)
        if self.settings["platform"].get("dc_fan"):
            status_data["fan_duty"] = int(control.get("duty_cycle", 0) or 0)
        else:
            status_data["fan_duty"] = 100 if status_data["outpins"].get("fan") else 0
        # ---- mode-specific status fields ----
        status_data.update(self.status_fragment())
        return status_data

    # ---- shared skeleton ----
    def run(self):
        import control as _control  # module global: eventLogger

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
        ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")

        _control.eventLogger.info(f"{mode} Mode started.")

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

        ctx.store.write_metrics(new_metric=True)
        self.state.metrics = ctx.store.read_metrics()
        self.state.metrics["mode"] = mode
        self.state.metrics["smokeplus"] = control["s_plus"]
        self.state.metrics["primary_setpoint"] = control["primary_setpoint"]
        self.state.metrics["pellet_level_start"] = pelletdb["current"]["hopper_level"]
        current_pellet_id = pelletdb["current"]["pelletid"]
        pellet_brand = pelletdb["archive"][current_pellet_id]["brand"]
        pellet_type = pelletdb["archive"][current_pellet_id]["wood"]
        self.state.metrics["pellet_brand_type"] = f"{pellet_brand} {pellet_type}"
        ctx.store.write_metrics(self.state.metrics)

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

        # ---- declarative pre_loop guards (empty until Tasks 15-16; then the
        # flameout edges live here instead of in setup_safety). A fired guard
        # aborts the loop exactly as setup_safety returning "Inactive" does. This
        # reuses start_time (no extra clock read) -- the pre_loop flameout guards
        # do not use `now`. ----
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

            ctx.store.execute_control_writes()
            control = ctx.store.read_control()
            self.control = control

            process_system_commands(ctx)

            # Check if new mode has been requested
            if control["updated"]:
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
                ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
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
            if control["mode"] == Mode.RECIPE:
                if control["recipe"]["step_data"]["triggered"] and not control["recipe"]["step_data"]["pause"]:
                    if control["recipe"]["step_data"]["notify"]:
                        ctx.notifications.send("Recipe_Step_Message")
                    break
                elif control["recipe"]["step_data"]["triggered"] and control["recipe"]["step_data"]["pause"]:
                    if control["recipe"]["step_data"]["notify"]:
                        ctx.notifications.send("Recipe_Step_Message")
                        control["recipe"]["step_data"]["notify"] = False
                        ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
                    # Continue until 'pause' variable is cleared

            ctx.clock.sleep(0.05)

        # *********
        # END Mode Loop
        # *********

        # Clean-up and Exit
        grill_platform.auger_off()
        grill_platform.igniter_off()

        _control.eventLogger.debug("Auger OFF, Igniter OFF")

        # ---- mode-specific teardown ----
        self.teardown(ptemp)

        _control.eventLogger.info(f"{mode} mode ended.")

        # Save Pellets Used
        pelletdb = ctx.store.read_pellet_db()
        pelletdb["current"]["est_usage"] += self.state.metrics["augerontime"] * self.settings["globals"]["augerrate"]
        ctx.store.write_pellet_db(pelletdb)

        # Log the end time
        self.state.metrics["endtime"] = ctx.clock.now() * 1000
        self.state.metrics["pellet_level_end"] = pelletdb["current"]["hopper_level"]
        ctx.store.write_metrics(self.state.metrics)

        monitor.stop_monitor()

        if status_data != {}:
            status_data["mode"] = control["mode"]

        return ()
