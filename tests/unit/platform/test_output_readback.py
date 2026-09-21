"""Electrical readback must come from transport/register reads, never commands."""

import importlib
import logging
import threading
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from adafruit_emc2101.emc2101_lut import EMC2101_LUT

from common.learning_trajectory import FrameDeliveryCertainty
from controller.runtime.actuation_delivery import ActuationDeliveryJournal, DeliveredGrillPlatform
from grillplat.emc2301 import EMC2301
from grillplat.ft232h import Ft232hGpio
from grillplat.mcp2221 import Mcp2221Gpio
from grillplat.numato_usbrelay import NumatoResponseError, NumatoUSBRelay
from grillplat.output_readback import EmcPwmReadback


class RegisterDevice:
    def __init__(self, registers):
        self.registers = registers
        self.failure: OSError | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def write_then_readinto(self, output, result, *, out_end=None, in_start=0, in_end=None):
        if self.failure is not None:
            raise self.failure
        end = len(result) if in_end is None else in_end
        register = output[0]
        for offset, index in enumerate(range(in_start, end)):
            # A tachometer or any other unintended register access fails here.
            result[index] = self.registers[register + offset]

    def write(self, payload, *, start=0, end=None):
        if self.failure is not None:
            raise self.failure
        data = payload[start:end]
        self.registers[data[0]] = data[1]


class GpioPort:
    def __init__(self):
        self.levels = 0
        self.failure: OSError | None = None
        self.direction = 0

    def set_direction(self, pins, direction):
        self.direction = direction

    def write(self, value):
        # Accepted writes deliberately do not certify the independent pin read.
        pass

    def read(self, with_output=False):
        if self.failure is not None:
            raise self.failure
        return self.levels if with_output else self.levels & ~self.direction


class McpDevice:
    def __init__(self):
        self.levels: list[int | None] = [0, 0, 0, 0]
        self.failure: OSError | None = None

    def set_pin_function(self, **kwargs):
        pass

    def GPIO_write(self, **kwargs):
        pass

    def GPIO_read(self):
        if self.failure is not None:
            raise self.failure
        return tuple(self.levels)


class RelaySerial:
    def __init__(self):
        self.response = "c"
        self.pending = bytearray()
        self.failure: OSError | None = None

    def reset_input_buffer(self):
        self.pending.clear()

    def write(self, payload):
        if self.failure is not None:
            raise self.failure
        assert payload == b"relay readall\r"
        self.pending.extend(b"relay readall\r\n" + self.response.encode() + b"\r\n>")

    def flush(self):
        pass

    def read(self, size):
        result = bytes(self.pending[:size])
        del self.pending[:size]
        return result


def build_platform(kind, chip="emc2301", active_high=True) -> tuple[Any, Any, RegisterDevice]:
    module = importlib.import_module(f"grillplat.{kind}")
    platform = module.GrillPlatform.__new__(module.GrillPlatform)
    platform.chip = chip
    platform.pwm_fan = chip != "none"
    platform.logger = logging.getLogger("control")
    platform._output_state = {"auger": False, "fan": False, "igniter": False, "power": False}
    platform._fan_speed_percent = 73.125
    platform.frequency = 25000
    platform._ramp_thread = None
    if kind == "ft232h_relay":
        transport = GpioPort()
        gpio = Ft232hGpio(SimpleNamespace(get_gpio=lambda: transport))
        for name in ("C2", "C3"):
            gpio.setup_output(name)
        platform.relays = {
            "auger": module._Relay(gpio, "C2", active_high),
            "fan": module._Relay(gpio, "C3", active_high),
        }
        transport.levels = (1 << 10) | (1 << 11)
    elif kind == "mcp2221_relay":
        transport = McpDevice()
        gpio = Mcp2221Gpio(transport, threading.RLock())
        platform.relays = {
            "auger": module._Relay(gpio, "GP2", active_high),
            "fan": module._Relay(gpio, "GP3", active_high),
        }
        transport.levels[2:] = [1, 1]
    else:
        transport = RelaySerial()
        platform.relay = cast(Any, NumatoUSBRelay.__new__(NumatoUSBRelay))
        platform.relay._serial = transport
        platform.relay._lock = threading.Lock()
        platform.relay_map = {"auger": 2, "fan": 3}
    registers = RegisterDevice(
        {
            0x03: 0,
            0x4A: 0x20,
            0x4B: 0x1F,
            0x4C: 7,
            0x4D: 7,
            0x2A: 0,
            0x30: 128,
            0x32: 0,
            0x33: 0,
            0x36: 0x19,
        }
    )
    controller_type = EMC2301 if chip == "emc2301" else EMC2101_LUT
    platform.emc = cast(Any, controller_type.__new__(controller_type))
    platform.emc.i2c_device = registers
    platform.emc._full_speed_lsb = 63  # Deliberately stale library conversion cache.
    platform._pwm_readback = EmcPwmReadback(platform.emc, chip)
    # Fixtures represent already-running manual hardware, not a fresh spin-up.
    with patch("grillplat.output_readback.monotonic", return_value=-10.0):
        platform._pwm_readback.read(platform._fan_speed_percent, platform.logger)
    return platform, transport, registers


PLATFORMS = ("ft232h_relay", "mcp2221_relay", "x86_numato")


@pytest.mark.parametrize("kind", PLATFORMS)
def test_readback_uses_electrical_state_not_command_cache(kind):
    platform, transport, registers = build_platform(kind)
    observed = platform.get_output_readback()
    assert observed == {"auger": True, "fan": True, "pwm": pytest.approx(50.19607843137255)}
    assert platform.get_output_status()["pwm"] == 73.125
    registers.registers[0x30] = 0
    if kind == "ft232h_relay":
        transport.levels = 0
    elif kind == "mcp2221_relay":
        transport.levels = [0, 0, 0, 0]
    else:
        transport.response = "0"
    assert platform.get_output_readback() == {"auger": False, "fan": False, "pwm": 0.0}


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize("raw,expected", [(0, 0.0), (1, 0.39215686274509803), (128, 50.19607843137255), (255, 100.0)])
def test_emc2301_readback_respects_eight_bit_duty(kind, raw, expected):
    platform, _, registers = build_platform(kind)
    registers.registers[0x30] = raw
    assert platform.get_output_readback()["pwm"] == pytest.approx(expected)


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize(
    "raw,pwm_f,expected",
    [
        (0, 7, 0.0),
        (1, 7, 7.142857142857143),
        (7, 7, 50.0),
        (14, 7, 100.0),
        (63, 7, 100.0),
        (1, 0, 50.0),
        (31, 31, 50.0),
        (0xC7, 0xE7, 50.0),
    ],
)
def test_emc2101_readback_uses_live_pwm_resolution(kind, raw, pwm_f, expected):
    platform, _, registers = build_platform(kind, "emc2101")
    registers.registers.update({0x4C: raw, 0x4D: pwm_f})
    assert platform.get_output_readback()["pwm"] == pytest.approx(expected)


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize("config,fan_config", [(0x10, 0x20), (0, 0)])
def test_emc2101_nonmanual_pwm_modes_are_unknown(kind, config, fan_config):
    platform, _, registers = build_platform(kind, "emc2101")
    registers.registers.update({0x03: config, 0x4A: fan_config})
    assert platform.get_output_readback() == {"auger": True, "fan": True}


@pytest.mark.parametrize("kind", ("ft232h_relay", "mcp2221_relay"))
def test_active_low_relay_readback_converts_pin_levels(kind):
    platform, transport, _ = build_platform(kind, active_high=False)
    if kind == "ft232h_relay":
        transport.levels = 1 << 11
    else:
        transport.levels[2:] = [0, 1]
    assert platform.get_output_readback() == {"auger": True, "fan": False, "pwm": pytest.approx(50.19607843137255)}


@pytest.mark.parametrize("kind", ("ft232h_relay", "mcp2221_relay"))
def test_relay_only_readback_does_not_invent_pwm(kind):
    platform, _, _ = build_platform(kind, "none")
    assert platform.get_output_readback() == {"auger": True, "fan": True}


def test_mcp2221_non_gpio_pin_is_unknown_not_off():
    platform, transport, _ = build_platform("mcp2221_relay")
    transport.levels[3] = None
    assert platform.get_output_readback() == {"auger": True, "pwm": pytest.approx(50.19607843137255)}


def test_mcp2221_invalid_pin_level_is_not_certified():
    platform, transport, _ = build_platform("mcp2221_relay")
    transport.levels[3] = 2
    with pytest.raises(OSError):
        platform.get_output_readback()


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize("chip", ("emc2101", "emc2301"))
@pytest.mark.parametrize("source", ("gpio", "pwm"))
def test_transport_failure_propagates_instead_of_certifying_off(kind, chip, source):
    platform, transport, registers = build_platform(kind, chip)
    target = transport if source == "gpio" else registers
    target.failure = OSError("device unavailable")
    with pytest.raises(OSError, match="device unavailable"):
        platform.get_output_readback()


def test_numato_invalid_reply_is_not_an_all_off_observation():
    platform, transport, _ = build_platform("x86_numato")
    transport.response = ""
    with pytest.raises(NumatoResponseError):
        platform.get_output_readback()


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize(
    "chip,requested,register,raw,expected",
    [
        ("emc2301", 50.0, 0x30, 128, 50.19607843137255),
        ("emc2101", 48.0, 0x4C, 7, 50.0),
    ],
)
def test_normal_quantization_is_not_a_pwm_mismatch(kind, chip, requested, register, raw, expected, caplog):
    platform, _, registers = build_platform(kind, chip)
    platform._fan_speed_percent = requested
    registers.registers[register] = raw
    with caplog.at_level(logging.WARNING, logger="control"):
        assert platform.get_output_readback()["pwm"] == pytest.approx(expected)
    assert not caplog.records


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize(
    "chip,register,raw,expected",
    [
        ("emc2301", 0x30, 128, 50.19607843137255),
        ("emc2101", 0x4C, 7, 50.0),
    ],
)
def test_valid_unexpected_pwm_warns_but_preserves_observation(kind, chip, register, raw, expected, caplog):
    platform, _, registers = build_platform(kind, chip)
    platform._fan_speed_percent = 20.0
    registers.registers[register] = raw
    with caplog.at_level(logging.WARNING, logger="control"):
        assert platform.get_output_readback()["pwm"] == pytest.approx(expected)
    assert any(record.levelno == logging.WARNING and "PWM readback" in record.message for record in caplog.records)


@pytest.mark.parametrize("kind", PLATFORMS)
def test_emc2101_output_polarity_is_read_from_hardware(kind):
    platform, _, registers = build_platform(kind, "emc2101")
    registers.registers.update({0x4A: 0x30, 0x4C: 3, 0x4D: 6})
    assert platform.get_output_readback()["pwm"] == 75.0


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize("register,flag", [(0x32, 0x80), (0x33, 0x40)])
def test_emc2301_autonomous_modes_are_unknown(kind, register, flag):
    platform, _, registers = build_platform(kind)
    registers.registers[register] = flag
    assert platform.get_output_readback() == {"auger": True, "fan": True}


@pytest.mark.parametrize("kind", PLATFORMS)
def test_emc2301_polarity_preserves_unexpected_electrical_setting(kind, caplog):
    platform, _, registers = build_platform(kind)
    registers.registers.update({0x2A: 1, 0x30: 51})
    platform._fan_speed_percent = 20.0
    with caplog.at_level(logging.WARNING, logger="control"):
        assert platform.get_output_readback()["pwm"] == 80.0
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize(
    "chip,setting,startup,value,duration",
    [
        ("emc2301", 0x30, 0x36, 0x18, 0.25),
        ("emc2301", 0x30, 0x36, 0x19, 0.5),
        ("emc2301", 0x30, 0x36, 0x1A, 1.0),
        ("emc2301", 0x30, 0x36, 0x1B, 2.0),
        ("emc2101", 0x4C, 0x4B, 0x1F, 3.2),
        ("emc2101", 0x4C, 0x4B, 0x1A, 0.1),
    ],
)
def test_spinup_is_bounded_and_repeated_nonzero_writes_do_not_extend_it(
    kind,
    chip,
    setting,
    startup,
    value,
    duration,
    monkeypatch,
):
    platform, _, registers = build_platform(kind, chip)
    registers.registers.update({setting: 0, startup: value})
    platform.emc._full_speed_lsb = 14
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    platform.set_duty_cycle(50)
    assert "pwm" not in platform.get_output_readback()
    assert platform.get_output_status()["pwm"] == 50
    now[0] += duration / 2
    platform.set_duty_cycle(75)
    assert "pwm" not in platform.get_output_readback()
    now[0] = 100.0 + duration
    registers.failure = OSError("fresh read failed")
    with pytest.raises(OSError, match="fresh read failed"):
        platform.get_output_readback()
    registers.failure = None
    observed = platform.get_output_readback()["pwm"]
    expected = (round(0.75 * (255 if chip == "emc2301" else 14)) / (255 if chip == "emc2301" else 14)) * 100
    assert observed == pytest.approx(expected)


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize("startup", [0x18, 0x07, 0x21])
def test_emc2101_disabled_startup_is_immediately_observable(kind, startup):
    platform, _, registers = build_platform(kind, "emc2101")
    registers.registers.update({0x4C: 0, 0x4B: startup})
    platform.emc._full_speed_lsb = 14
    platform.set_duty_cycle(50)
    assert platform.get_output_readback()["pwm"] == 50.0


@pytest.mark.parametrize("kind", PLATFORMS)
def test_emc2101_fast_tach_startup_is_unknown_without_tach_reads(kind, monkeypatch):
    platform, _, registers = build_platform(kind, "emc2101")
    registers.registers.update({0x03: 0x04, 0x4C: 0, 0x4B: 0x21})
    platform.emc._full_speed_lsb = 14
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    platform.set_duty_cycle(50)
    assert "pwm" not in platform.get_output_readback()
    now[0] += 0.05
    assert platform.get_output_readback()["pwm"] == 50.0


@pytest.mark.parametrize("kind", PLATFORMS)
def test_unobserved_zero_to_nonzero_command_is_not_missed(kind, monkeypatch):
    platform, _, _ = build_platform(kind)
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    platform.set_duty_cycle(0)
    platform.set_duty_cycle(50)
    assert "pwm" not in platform.get_output_readback()
    now[0] += 0.5
    assert platform.get_output_readback()["pwm"] == pytest.approx(128 / 255 * 100)


@pytest.mark.parametrize("kind", PLATFORMS)
def test_initial_nonzero_history_requires_bounded_fresh_observation(kind, monkeypatch):
    platform, _, _ = build_platform(kind)
    platform._pwm_readback = EmcPwmReadback(platform.emc, platform.chip)
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    assert "pwm" not in platform.get_output_readback()
    now[0] += 0.5
    assert platform.get_output_readback()["pwm"] == pytest.approx(128 / 255 * 100)


@pytest.mark.parametrize("kind", PLATFORMS)
@pytest.mark.parametrize(
    "chip,setting,maximum",
    [
        ("emc2301", 0x30, 2.0),
        ("emc2101", 0x4C, 3.2),
    ],
)
def test_failed_prewrite_read_never_promotes_the_command_to_evidence(
    kind,
    chip,
    setting,
    maximum,
    monkeypatch,
):
    platform, _, registers = build_platform(kind, chip)
    registers.registers[setting] = 0
    platform.emc._full_speed_lsb = 14
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    original_read = registers.write_then_readinto

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(registers, "write_then_readinto", original_read)
        raise OSError("pre-write observation unavailable")

    monkeypatch.setattr(registers, "write_then_readinto", fail_once)
    platform.set_duty_cycle(50)
    assert "pwm" not in platform.get_output_readback()
    now[0] = 100.0 + maximum - 0.001
    assert "pwm" not in platform.get_output_readback()
    now[0] = 100.0 + maximum
    expected = 128 / 255 * 100 if chip == "emc2301" else 50.0
    assert platform.get_output_readback()["pwm"] == pytest.approx(expected)


@pytest.mark.parametrize("kind", PLATFORMS)
def test_zero_write_is_not_delayed_or_prevented_by_optional_readback(kind, monkeypatch):
    platform, _, registers = build_platform(kind)

    def unavailable(*args, **kwargs):
        raise OSError("cannot observe")

    monkeypatch.setattr(registers, "write_then_readinto", unavailable)
    platform.set_duty_cycle(0)
    assert registers.registers[0x30] == 0
    with pytest.raises(OSError, match="cannot observe"):
        platform.get_output_readback()


@pytest.mark.parametrize("kind", PLATFORMS)
def test_spinup_edge_uses_raw_setting_even_with_inverted_polarity(kind, monkeypatch):
    platform, _, registers = build_platform(kind)
    registers.registers.update({0x2A: 1, 0x30: 0})
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    assert platform.get_output_readback()["pwm"] == 100.0
    platform.set_duty_cycle(20)
    assert "pwm" not in platform.get_output_readback()
    now[0] += 0.5
    assert platform.get_output_readback()["pwm"] == 80.0


@pytest.mark.parametrize("kind", PLATFORMS)
def test_return_to_manual_mode_requires_fresh_stable_setting(kind, monkeypatch):
    platform, _, registers = build_platform(kind)
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    registers.registers[0x32] = 0x80
    assert "pwm" not in platform.get_output_readback()
    registers.registers[0x32] = 0
    assert "pwm" not in platform.get_output_readback()
    now[0] += 0.5
    assert platform.get_output_readback()["pwm"] == pytest.approx(128 / 255 * 100)


def test_snapshot_started_during_spinup_cannot_cross_deadline_into_evidence(monkeypatch):
    platform, _, registers = build_platform("ft232h_relay")
    registers.registers[0x30] = 0
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    platform.set_duty_cycle(50)
    now[0] = 100.49
    original_read = registers.write_then_readinto

    def delayed_read(*args, **kwargs):
        original_read(*args, **kwargs)
        now[0] = 100.51

    monkeypatch.setattr(registers, "write_then_readinto", delayed_read)
    assert "pwm" not in platform.get_output_readback()
    assert platform.get_output_readback()["pwm"] == pytest.approx(128 / 255 * 100)


def test_failed_write_does_not_leave_old_stable_history_certified(monkeypatch):
    platform, _, registers = build_platform("ft232h_relay")
    now = [100.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    registers.failure = OSError("write outcome unknown")
    with pytest.raises(OSError, match="write outcome unknown"):
        platform.set_duty_cycle(50)
    registers.failure = None
    assert "pwm" not in platform.get_output_readback()
    now[0] += 0.5
    assert platform.get_output_readback()["pwm"] == pytest.approx(128 / 255 * 100)


@pytest.mark.parametrize("kind", PLATFORMS)
def test_stable_startup_readback_recovers_without_rewriting_fan_command(kind, monkeypatch):
    platform, _, registers = build_platform(kind)
    registers.registers[0x30] = 0
    now = [0.0]
    monkeypatch.setattr("grillplat.output_readback.monotonic", lambda: now[0])
    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: round(now[0] * 1_000), wall_clock=lambda: round(now[0] * 1_000)
    )
    delivered = DeliveredGrillPlatform(platform, journal=journal)
    delivered.set_duty_cycle(50)
    now[0] = 20.0
    # Read-only frame-boundary refresh must not issue any hardware write.
    monkeypatch.setattr(registers, "write", lambda *args, **kwargs: pytest.fail("unexpected PWM write"))
    delivered.observe_outputs()
    assert journal.integrate(0, 20_000).fan_certainty is FrameDeliveryCertainty.UNKNOWN
    assert journal.integrate(20_000, 40_000).fan_certainty is FrameDeliveryCertainty.EXACT
    assert journal.mean_fan_duty(20_000, 40_000) == pytest.approx(128 / 255 * 100)


@pytest.mark.parametrize("kind", PLATFORMS)
def test_readback_refresh_cannot_certify_an_active_software_ramp(kind):
    platform, _, _ = build_platform(kind)
    ramp_running = [True]
    platform._ramp_thread = SimpleNamespace(is_alive=lambda: ramp_running[0])
    now = [0]
    journal = ActuationDeliveryJournal(monotonic_clock=lambda: now[0], wall_clock=lambda: now[0])
    delivered = DeliveredGrillPlatform(platform, journal=journal)
    delivered.observe_outputs()
    assert journal.integrate(0, 20_000).fan_certainty is FrameDeliveryCertainty.UNKNOWN
    ramp_running[0] = False
    now[0] = 20_000
    delivered.observe_outputs()
    assert journal.mean_fan_duty(20_000, 40_000) == pytest.approx(128 / 255 * 100)
