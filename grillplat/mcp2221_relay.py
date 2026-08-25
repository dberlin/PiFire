#!/usr/bin/env python3

"""MCP2221A IO-triggered relay grill platform.

GP0-GP3 drive the power, igniter, auger, and fan relay inputs. The fan can
remain relay-only or use an EMC2101/EMC2301 on any configured PiFire I2C bus.
"""

import logging
import threading
from typing import cast

from adafruit_emc2101.emc2101_lut import EMC2101_LUT

from common.i2c_bus import open_i2c_bus
from common.i2c_bus_config import parse_i2c_bus
from grillplat.actuator_capabilities import AUGER_TIMING
from grillplat.emc2301 import EMC2301
from grillplat.mcp2221 import open_gpio as open_mcp2221_gpio
from grillplat.system_commands import SystemCommandsMixin

_DEFAULT_OUTPUTS = {"power": "GP0", "igniter": "GP1", "auger": "GP2", "fan": "GP3"}


class _Relay:
    """One relay input driven by an MCP2221A GPIO pin."""

    def __init__(self, gpio, pin_name, active_high):
        self._gpio = gpio
        self._pin_name = pin_name
        self._active_high = active_high
        self._state = False
        # Configure direction and the inactive level in one HID command. This
        # avoids briefly asserting an active-low relay during initialization.
        gpio.setup_output(pin_name, initial_high=not active_high)

    def on(self):
        self._gpio.set(self._pin_name, self._active_high)
        self._state = True

    def off(self):
        self._gpio.set(self._pin_name, not self._active_high)
        self._state = False

    @property
    def is_active(self):
        return self._state

    def close(self):
        # EasyMCP2221 owns one process-lifetime handle per physical adapter.
        pass


class GrillPlatform(SystemCommandsMixin):
    def __init__(self, config):
        self.logger = logging.getLogger("control")
        self.config = config

        outputs = config.get("outputs", {}) or {}
        self.pin_map = {name: str(outputs.get(name, default)) for name, default in _DEFAULT_OUTPUTS.items()}

        mcp2221_cfg = config.get("mcp2221", {}) or {}
        self.serial = str(mcp2221_cfg.get("serial", ""))

        fan_cfg = config.get("fan_controller", {}) or {}
        self.chip = str(fan_cfg.get("chip", "none")).lower()
        self.pwm_fan = self.chip in ("emc2101", "emc2301")
        self.bus = parse_i2c_bus(fan_cfg.get("i2c_bus") or {"kind": "basic"})

        address = fan_cfg.get("address")
        if address is None:
            address = 0x2F if self.chip == "emc2301" else 0x4C
        elif isinstance(address, str):
            address = int(address, 16)
        self.emc_address = address

        self.frequency = config.get("frequency", 25000)
        self.standalone = config.get("standalone", True)

        active_high = config.get("triggerlevel", "HIGH") == "HIGH"
        self._output_state = {"auger": False, "fan": False, "igniter": False, "power": False}
        self._fan_speed_percent = 0
        self._ramp_thread = None
        self._ramp_stop = threading.Event()
        self.current: dict[str, object] = {}

        gpio = open_mcp2221_gpio(self.serial)
        self.relays = {}
        try:
            for name, pin_name in self.pin_map.items():
                self.relays[name] = _Relay(gpio, pin_name, active_high)
        except Exception:
            for relay in self.relays.values():
                try:
                    relay.off()
                    relay.close()
                except Exception:
                    pass
            raise

        self.emc: EMC2101_LUT | EMC2301 | None = None
        if self.pwm_fan:
            self._init_fan_controller()

    def _init_fan_controller(self):
        i2c = open_i2c_bus(self.bus)
        if self.chip == "emc2301":
            self.emc = EMC2301(i2c, address=self.emc_address)
        else:
            self.emc = EMC2101_LUT(i2c)
            self.emc.lut_enabled = False
        self.emc.manual_fan_speed = 0
        self.set_pwm_frequency(self.frequency)

    def _set_output(self, name, state):
        relay = self.relays[name]
        if state:
            relay.on()
        else:
            relay.off()
        self._output_state[name] = state

    def auger_on(self):
        self.logger.debug("auger_on: Turning on auger")
        self._set_output("auger", True)

    def auger_off(self):
        self.logger.debug("auger_off: Turning off auger")
        self._set_output("auger", False)

    def auger_timing(self):
        return AUGER_TIMING

    def igniter_on(self):
        self.logger.debug("igniter_on: Turning on igniter")
        self._set_output("igniter", True)

    def igniter_off(self):
        self.logger.debug("igniter_off: Turning off igniter")
        self._set_output("igniter", False)

    def power_on(self):
        self.logger.debug("power_on: Powering on grill platform")
        self._set_output("power", True)

    def power_off(self):
        self.logger.debug("power_off: Powering off grill platform")
        self._set_output("power", False)

    def get_input_status(self):
        return False

    def fan_on(self, fan_speed_percent=100):
        self.logger.debug("fan_on: Enabling fan power, speed " + str(fan_speed_percent))
        self._set_output("fan", True)
        if self.pwm_fan:
            self._stop_ramp()
            self.set_duty_cycle(fan_speed_percent)

    def fan_off(self):
        self.logger.debug("fan_off: Stopping fan and removing power")
        try:
            emc = self.emc
            if self.pwm_fan and emc is not None:
                self._stop_ramp()
                emc.manual_fan_speed = 0
                self._fan_speed_percent = 0
        finally:
            self._set_output("fan", False)

    def fan_toggle(self):
        if self._output_state["fan"]:
            self.fan_off()
        else:
            self.fan_on()

    def set_duty_cycle(self, fan_speed_percent, override_ramping=True):
        emc = self.emc
        if not self.pwm_fan or emc is None:
            return
        if override_ramping:
            self._stop_ramp()
        fan_speed_percent = max(0, min(100, fan_speed_percent))
        emc.manual_fan_speed = fan_speed_percent
        self._fan_speed_percent = fan_speed_percent

    def set_pwm_frequency(self, frequency=25000):
        self.frequency = frequency
        emc = self.emc
        if not self.pwm_fan or emc is None:
            return
        try:
            if self.chip == "emc2301":
                cast(EMC2301, emc).pwm_frequency = frequency
            else:
                pwm_f = max(1, min(31, round(360000 / (2 * frequency))))
                emc2101 = cast(EMC2101_LUT, emc)
                emc2101.set_pwm_clock(use_preset=False, use_slow=False)
                emc2101.pwm_frequency_divisor = 1
                emc2101.pwm_frequency = pwm_f
        except (ValueError, OSError, AttributeError) as exc:
            self.logger.warning("set_pwm_frequency: controller rejected frequency: " + str(exc))

    def _stop_ramp(self):
        if self._ramp_thread is not None:
            self._ramp_stop.set()
            if self._ramp_thread is not threading.current_thread():
                self._ramp_thread.join(timeout=5)
            self._ramp_thread = None

    def pwm_fan_ramp(self, on_time=5, min_duty_cycle=20, max_duty_cycle=100):
        self._set_output("fan", True)
        if not self.pwm_fan:
            return
        self._start_ramp(on_time, min_duty_cycle, max_duty_cycle)

    def _start_ramp(self, on_time, min_duty_cycle, max_duty_cycle):
        self._stop_ramp()
        self._ramp_stop = threading.Event()
        self._ramp_thread = threading.Thread(
            target=self._ramp_device,
            args=(on_time, min_duty_cycle, max_duty_cycle),
            daemon=True,
        )
        self._ramp_thread.start()

    def _ramp_device(self, on_time, min_duty_cycle, max_duty_cycle, fps=25):
        steps = max(int(fps * on_time), 1)
        for i in range(steps):
            fraction = i / steps
            percent = min_duty_cycle + (max_duty_cycle - min_duty_cycle) * fraction
            self.set_duty_cycle(round(percent, 2), override_ramping=False)
            if self._ramp_stop.wait(1.0 / fps):
                break
        self.set_duty_cycle(max_duty_cycle, override_ramping=False)

    def cleanup(self):
        self.logger.debug("cleanup: Shutting down outputs")
        self._stop_ramp()
        if self.pwm_fan and self.emc is not None:
            try:
                self.emc.manual_fan_speed = 0
            except Exception:
                pass
        first_failure = None
        for relay in self.relays.values():
            try:
                relay.off()
            except Exception as exc:
                first_failure = first_failure or exc
            try:
                relay.close()
            except Exception as exc:
                first_failure = first_failure or exc
        if first_failure is not None:
            raise first_failure

    def get_output_status(self):
        self.current = {
            "auger": self._output_state["auger"],
            "igniter": self._output_state["igniter"],
            "power": self._output_state["power"],
            "fan": self._output_state["fan"],
        }
        if self.pwm_fan:
            self.current["pwm"] = self._fan_speed_percent
            self.current["frequency"] = self.frequency
        return self.current
