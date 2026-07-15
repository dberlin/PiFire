from controller.runtime.modes.base import ControlMode
from controller.runtime.logic.cycle import prime_cycle_times


class PrimeMode(ControlMode):
    """Prime mode: fan off, power on at setup (its own branch, distinct from
    the Startup/Reignite/Smoke/Hold/Shutdown fan-on branch); auger ON at
    setup (shared with Startup/Reignite/Smoke/Hold); computes prime_duration/
    OnTime/OffTime/CycleTime/CycleRatio from prime_amount and augerrate, with
    an optional igniter-on if prime_ignition is enabled and next_mode is
    Startup. Per-tick, only runs the shared (non-Hold) auger-cycle toggle via
    `_auger_cycle_tick`. Exits once prime_duration has elapsed since start.
    Teardown is shared with Shutdown/Monitor/Manual: fan+power off."""

    name = "Prime"

    def setup(self):
        import control as _control

        self.grill.fan_off()
        self.grill.power_on()
        _control.eventLogger.debug("Power ON, Fan OFF, Igniter OFF, Auger OFF")

        self.grill.auger_on()
        _control.eventLogger.debug("Auger ON")

        control = self.ctx.store.read_control()
        auger_rate = self.settings["globals"]["augerrate"]
        self.state.prime.amount = control["prime_amount"]
        # Auger On Time = Prime Amount (Grams) / (Grams per Second)
        _ct = prime_cycle_times(self.state.prime.amount, auger_rate)
        self.state.prime.duration = int(_ct.on_time)
        self.state.cycle.on_time = _ct.on_time
        self.state.cycle.off_time = _ct.off_time
        self.state.cycle.cycle_time = _ct.cycle_time
        self.state.cycle.ratio = _ct.cycle_ratio
        self.state.cycle.raw_ratio = _ct.cycle_ratio

        # Allow for the igniter to be turned on during prime mode - user selected
        if self.settings["globals"]["prime_ignition"] and control["next_mode"] == "Startup":
            self.grill.igniter_on()
            _control.eventLogger.debug("Igniter ON")

    def on_tick(self, now, ptemp, current_output_status):
        self._auger_cycle_tick(now, current_output_status)

    def should_exit(self, now, ptemp) -> bool:
        return (now - self.state.timers.start_time) > self.state.prime.duration

    def status_fragment(self) -> dict:
        return {"prime_duration": self.state.prime.duration, "prime_amount": self.state.prime.amount}

    def teardown(self, ptemp):
        self.grill.fan_off()
        self.grill.power_off()
        import control as _control

        _control.eventLogger.debug("Fan OFF, Power OFF")
