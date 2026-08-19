from common.modes import Mode
from controller.runtime.modes.base import ControlMode


class MonitorMode(ControlMode):
    """Monitor mode: idles with fan/power off. No auger cycle, no controller,
    no mode-specific safety checks or exit conditions. Relies entirely on
    the shared skeleton's universal breaks (mode-change, switch-off,
    max-temp, Recipe)."""

    name = Mode.MONITOR

    def setup(self):
        self.grill.fan_off()
        self.grill.power_off()
        self.ctx.event_log.debug("Power OFF, Fan OFF, Igniter OFF, Auger OFF")

    def teardown(self, ptemp):
        self.grill.fan_off()
        self.grill.power_off()
        self.ctx.event_log.debug("Fan OFF, Power OFF")
