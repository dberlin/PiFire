from controller.runtime.logic.cycle import smoke_cycle_times
from controller.runtime.logic.fan import start_fan
from controller.runtime.logic.smartstart import profile_cycle
from controller.runtime.modes.base import ControlMode


class SmokeMode(ControlMode):
    """Smoke mode: fan+power on at setup (shared branch with Startup/Reignite/
    Hold/Shutdown -- Smoke always takes the plain `start_fan(grill, settings)`
    path, never the Startup/Reignite dc_fan pwm_duty_cycle special case);
    auger ON at setup (shared with Startup/Reignite/Hold/Prime); initializes
    the smoke-cycle timing (shared init path with Startup/Reignite); sets up
    Recipe-mode triggers (shared with Hold). The pre-loop and in-loop flameout
    checks are DECLARATIVE guard edges (GUARDS["Smoke"] in transitions.py, fired
    by evaluate_phase at base.run's pre_loop/pre_act points) -- setup_safety()
    survives only to re-apply smart-start (Smoke skips the Startup/Reignite
    profile-SELECTION sub-branch and just re-applies
    control['smartstart']['profile_selected'] chosen by a prior Startup/
    Reignite run) and no longer has a check_safety override. Per-tick, on_tick
    runs the shared (non-Hold) auger-cycle toggle then delegates the fan work
    entirely to the shared `_smoke_plus_fan_tick` helper -- Smoke never touches
    the Hold-only lid-open/PWM-duty-from-temp/fan-assist parts
    (target_temp_achieved stays False for Smoke, so that gate structurally
    excludes it). on_publish publishes cycle_ratio to MQTT (shared with
    Startup). No mode-specific teardown."""

    name = "Smoke"

    def setup(self):
        # NOTE: Recipe-mode trigger setup (gated on `control['mode'] == 'Recipe'
        # and self.name in ('Smoke', 'Hold')`) lives in the shared pre-loop
        # section of `ControlMode.run()` (base.py), which runs for every
        # ControlMode subclass -- it is not duplicated here.
        import control as _control

        start_fan(self.grill, self.settings)
        self.grill.power_on()
        _control.eventLogger.debug("Power ON, Fan ON, Igniter OFF, Auger OFF")

        self.grill.auger_on()
        _control.eventLogger.debug("Auger ON")

        self._init_smoke_cycle()

    def _init_smoke_cycle(self):
        _ct = smoke_cycle_times(self.settings["cycle_data"])
        self.state.cycle.on_time = _ct.on_time
        self.state.cycle.off_time = _ct.off_time
        self.state.cycle.cycle_time = _ct.cycle_time
        self.state.cycle.ratio = _ct.cycle_ratio
        self.state.cycle.raw_ratio = _ct.cycle_ratio
        self.state.lid.open_detected = False
        self.state.lid.expires = 0
        # Write Metrics (note these will be overwritten if smart start is enabled)
        self.state.metrics["p_mode"] = self.settings["cycle_data"]["PMode"]
        self.state.metrics["auger_cycle_time"] = self.settings["cycle_data"]["SmokeOnCycleTime"]
        self.ctx.store.write_metrics(self.state.metrics)

    def setup_safety(self, ptemp) -> str:
        # Flameout is now a declarative pre_loop guard (GUARDS["Smoke"], fired by
        # evaluate_phase in base.run before the loop). This override survives only
        # for the Smoke-specific smart-start re-application below; it always
        # returns "Active" (the guard sets status to "Inactive" on a flameout).
        ctx = self.ctx
        control = self.control
        settings = self.settings

        # Apply Smart Start Settings if Enabled (Smoke re-applies the profile
        # already selected by a prior Startup/Reignite run -- no selection here)
        if settings["startup"]["smartstart"]["enabled"]:
            profile_selected = control["smartstart"]["profile_selected"]
            profile = settings["startup"]["smartstart"]["profiles"][profile_selected]
            _ct, startup_timer, _mbits = profile_cycle(profile, settings["cycle_data"])
            self.state.cycle.on_time = _ct.on_time
            self.state.cycle.off_time = _ct.off_time
            self.state.cycle.cycle_time = _ct.cycle_time
            self.state.cycle.ratio = _ct.cycle_ratio
            self.state.cycle.raw_ratio = _ct.cycle_ratio
            self.state.startup.timer = startup_timer
            # Write Metrics
            self.state.metrics["smart_start_profile"] = profile_selected
            self.state.metrics["startup_temp"] = control["smartstart"]["startuptemp"]
            self.state.metrics.update(_mbits)
            ctx.store.write_metrics(self.state.metrics)

        return "Active"

    def on_settings_reload(self):
        _ct = smoke_cycle_times(self.settings["cycle_data"])
        self.state.cycle.on_time = _ct.on_time
        self.state.cycle.off_time = _ct.off_time
        self.state.cycle.cycle_time = _ct.cycle_time
        self.state.cycle.ratio = _ct.cycle_ratio
        self.state.cycle.raw_ratio = _ct.cycle_ratio
        # Write Metrics (note these will overwrite the previous value)
        self.state.metrics["p_mode"] = self.settings["cycle_data"]["PMode"]
        self.state.metrics["auger_cycle_time"] = self.settings["cycle_data"]["SmokeOnCycleTime"]
        self.ctx.store.write_metrics(self.state.metrics)

    def on_tick(self, now, ptemp, current_output_status):
        self._auger_cycle_tick(now, current_output_status)
        self._smoke_plus_fan_tick(now, ptemp, current_output_status)

    def on_publish(self, now):
        pid_data = {"cycle_ratio": round(self.state.cycle.ratio, 2)}
        self.ctx.notifications.check(self.settings, self.control, pid_data=pid_data)

    # check_safety is now a declarative pre_act guard (GUARDS["Smoke"]); the base
    # ControlMode default (return False) applies here.
