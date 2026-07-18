from common.common import WriteKind
from common.modes import Mode
from controller.runtime.logic.cycle import hold_initial_cycle
from controller.runtime.logic.fan import start_fan, smoke_plus_max_ratio, fan_assist_times
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

    name = Mode.HOLD

    def setup(self):
        import control as _control

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

        # Load Controller Module (i.e. PID)
        self._runner, self._controller_status = _runner_mod.build_runner(
            self.settings, self.control, logger=self.ctx.control_log
        )

        # Fan ownership is a setup-time capability of the controller (e.g. MPC
        # with enable_fan_input), not a runtime latch -- this closes a startup
        # window where the temp-profile fan path could run before the
        # controller's first fan command.
        self.state.controller.controls_fan = self._runner.commands_fan() if self._runner is not None else False

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
        # set self.state.timers.start_time (that happens after setup_safety(),
        # later in the shared pre-loop) -- like StartupMode, take our own
        # ctx.clock.now() reading here rather than depending on that later value.
        self.state.controller.cycle_start = self.ctx.clock.now()

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

        if control["controller_update"]:
            control["controller_update"] = False
            ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            # Reinitialize the controller with the updated settings
            self.settings = ctx.store.read_settings()
            settings = self.settings
            self._controller_status = self._runner.reconfigure(settings, control, logger=ctx.control_log)
            if self._controller_status == "Active":
                _control.eventLogger.info("Controller reinitialized with updated settings")

        # Feed the runner every tick so a threaded core always has a fresh temp
        # to solve; for the synchronous runner this just stores the latest temp,
        # so the value read at the gate below is unchanged.
        self._runner.submit(ptemp)
        # Check to see if it's time to update pid and update if needed.
        controller_interval = self._runner.control_period() or self.state.cycle.cycle_time
        if (now - self.state.controller.cycle_start) > controller_interval:
            _out = self._runner.latest()
            self.state.controller.output, fan_cmd = _out.cycle_ratio, _out.fan
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
            if fan_cmd is not None and settings["platform"]["dc_fan"] and control["pwm_control"]:
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
            and (ptemp < (control["primary_setpoint"] * ((100 - settings["cycle_data"]["LidOpenThreshold"]) / 100)))
        ):
            self.state.lid.open_detected = True
            # Stop all control during a lid open event, including fan.
            # If we are in a state where the auger ratio is min and we are using the fan for control, turning the fan on here would overshoot the temps.
            # This is a major issue when using piFire for a wood or charcoal pit or a hybrid wood/pellet pit.
            grill_platform.auger_off()
            grill_platform.fan_off()
            self.state.timers.auger_toggle = now
            self.state.lid.expires = now + settings["cycle_data"]["LidOpenPauseTime"]
            self.state.target_temp_achieved = False

        # Clear Lid Open Detect Event, Reset
        if self.state.lid.open_detected and self.ctx.clock.now() > self.state.lid.expires:
            self.state.lid.open_detected = False
            start_fan(grill_platform, settings, control["duty_cycle"])
        if control["lid_open_toggle"]:
            control["lid_open_toggle"] = False
            self.ctx.store.write_control(control, WriteKind.OVERWRITE, origin="control")
            if self.state.lid.open_detected:
                self.state.lid.open_detected = False
            else:
                self.state.lid.open_detected = True
                grill_platform.auger_off()
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

    def _on_auger_on(self, now):
        settings = self.settings
        control = self.control

        self.state.cycle.on_time = settings["cycle_data"]["HoldCycleTime"] * self.state.cycle.ratio
        self.state.cycle.off_time = settings["cycle_data"]["HoldCycleTime"] * (1 - self.state.cycle.ratio)
        self.state.cycle.cycle_time = self.state.cycle.on_time + self.state.cycle.off_time

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

    # check_safety is now a declarative pre_act guard (GUARDS["Hold"]); the base
    # ControlMode default (return False) applies here.

    def status_fragment(self) -> dict:
        return {"lid_open_detected": self.state.lid.open_detected, "lid_open_endtime": self.state.lid.expires}

    def teardown(self, ptemp):
        # Stop the controller runner's background thread (no-op for the
        # synchronous runner). Guard against a failed build leaving no runner.
        if self._runner is not None:
            self._runner.stop()
