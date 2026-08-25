from unittest import mock

import pytest

from common.i2c_bus_config import KernelBusNumber


class FakeGpio:
    def __init__(self):
        self.setups = []
        self.values = {}

    def setup_output(self, pin_name, initial_high=False):
        self.setups.append((pin_name, initial_high))
        self.values[pin_name] = initial_high

    def set(self, pin_name, high):
        self.values[pin_name] = high


class FailingOffGpio(FakeGpio):
    def __init__(self, fail_pin):
        super().__init__()
        self.fail_pin = fail_pin
        self.off_attempts = []

    def set(self, pin_name, high):
        if high:
            self.off_attempts.append(pin_name)
            if pin_name == self.fail_pin:
                raise OSError(f"failed to deassert {pin_name}")
        super().set(pin_name, high)


class FailingFanController:
    def __init__(self, speed):
        self._speed = speed

    @property
    def manual_fan_speed(self):
        return self._speed

    @manual_fan_speed.setter
    def manual_fan_speed(self, value):
        if value == 0:
            raise OSError("fan controller unavailable")
        self._speed = value


def _config(*, chip="none", triggerlevel="LOW", outputs=None, fan_bus=None):
    return {
        "outputs": outputs or {},
        "mcp2221": {"serial": "RELAY-A"},
        "fan_controller": {
            "chip": chip,
            "i2c_bus": fan_bus or {"kind": "kernel", "bus_num": 7},
            "address": "0x2f" if chip == "emc2301" else "0x4c",
        },
        "triggerlevel": triggerlevel,
        "frequency": 25000,
        "standalone": True,
    }


def _build(config, gpio=None):
    import grillplat.mcp2221_relay as module

    gpio = gpio or FakeGpio()
    bus = mock.sentinel.bus
    emc2101 = mock.Mock()
    emc2301 = mock.Mock()
    with (
        mock.patch.object(module, "open_mcp2221_gpio", return_value=gpio) as open_gpio,
        mock.patch.object(module, "open_i2c_bus", return_value=bus) as open_bus,
        mock.patch.object(module, "EMC2101_LUT", emc2101),
        mock.patch.object(module, "EMC2301", emc2301),
    ):
        platform = module.GrillPlatform(config)
    return platform, gpio, open_gpio, open_bus, emc2101, emc2301


def test_relay_only_defaults_to_gp0_through_gp3_and_deasserts_active_low_outputs():
    platform, gpio, open_gpio, open_bus, emc2101, emc2301 = _build(_config())

    open_gpio.assert_called_once_with("RELAY-A")
    open_bus.assert_not_called()
    emc2101.assert_not_called()
    emc2301.assert_not_called()
    assert gpio.setups == [("GP0", True), ("GP1", True), ("GP2", True), ("GP3", True)]

    platform.power_on()
    platform.igniter_on()
    platform.auger_on()
    platform.fan_on()
    assert gpio.values == {"GP0": False, "GP1": False, "GP2": False, "GP3": False}
    assert platform.get_output_status() == {
        "auger": True,
        "igniter": True,
        "power": True,
        "fan": True,
    }

    platform.cleanup()
    assert gpio.values == {"GP0": True, "GP1": True, "GP2": True, "GP3": True}


def test_custom_mapping_and_active_high_trigger_are_honored():
    outputs = {"power": "GP3", "igniter": "GP2", "auger": "GP1", "fan": "GP0"}
    platform, gpio, *_ = _build(_config(triggerlevel="HIGH", outputs=outputs))

    assert gpio.setups == [("GP3", False), ("GP2", False), ("GP1", False), ("GP0", False)]
    platform.auger_on()
    assert gpio.values["GP1"] is True
    platform.auger_off()
    assert gpio.values["GP1"] is False


def test_pwm_fan_uses_the_independently_selected_standard_i2c_bus():
    platform, gpio, _, open_bus, _, emc2301 = _build(_config(chip="emc2301", fan_bus={"kind": "kernel", "bus_num": 7}))

    open_bus.assert_called_once_with(KernelBusNumber(bus_num=7))
    emc2301.assert_called_once_with(mock.sentinel.bus, address=0x2F)
    platform.fan_on(63)
    assert gpio.values["GP3"] is False
    assert platform.emc.manual_fan_speed == 63
    assert platform.get_output_status()["pwm"] == 63


def test_pwm_fan_can_share_the_relay_mcp2221_or_select_another_one():
    from common.i2c_bus_config import MCP2221Bus

    for serial in ("RELAY-A", "FAN-B"):
        platform, _, _, open_bus, emc2101, _ = _build(
            _config(chip="emc2101", fan_bus={"kind": "mcp2221", "serial": serial})
        )
        open_bus.assert_called_once_with(MCP2221Bus(serial=serial))
        emc2101.assert_called_once_with(mock.sentinel.bus)
        assert platform.pwm_fan is True


def test_cleanup_attempts_to_deassert_every_relay_before_reporting_a_gpio_failure():
    gpio = FailingOffGpio("GP0")
    platform, *_ = _build(_config(), gpio=gpio)
    platform.power_on()
    platform.igniter_on()
    platform.auger_on()
    platform.fan_on()

    with pytest.raises(OSError, match="GP0"):
        platform.cleanup()

    assert gpio.off_attempts == ["GP0", "GP1", "GP2", "GP3"]
    assert gpio.values["GP1"] is True
    assert gpio.values["GP2"] is True
    assert gpio.values["GP3"] is True


def test_fan_off_deasserts_the_relay_when_zeroing_pwm_fails():
    platform, gpio, *_ = _build(_config(chip="emc2301"))
    platform.fan_on(63)
    platform.emc = FailingFanController(63)

    with pytest.raises(OSError, match="fan controller unavailable"):
        platform.fan_off()

    assert gpio.values["GP3"] is True
    assert platform.get_output_status()["fan"] is False
