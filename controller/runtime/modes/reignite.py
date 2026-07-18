from common.modes import Mode
from controller.runtime.modes.startup import StartupMode


class ReigniteMode(StartupMode):
    """Reignite mode: reuses StartupMode entirely (fan/power/igniter/auger
    setup, smoke-cycle init, safety baseline, smart-start select+apply,
    on_settings_reload, auger on_tick, check_safety afterstarttemp, should_exit
    timer/exit-temp, teardown afterstarttemp), overriding only two hooks:
      1. It does not write control['startup_timestamp'] -- that timestamp
         marks the start of a fresh Startup run, not a reignite attempt.
      2. It does not publish cycle_ratio over MQTT the way Startup/Smoke do.
    """

    name = Mode.REIGNITE

    def _write_startup_timestamp(self):
        pass  # Reignite does not (re)write startup_timestamp

    def on_publish(self, now):
        pass  # Reignite is excluded from the cycle-ratio MQTT publish (Startup/Smoke only)
