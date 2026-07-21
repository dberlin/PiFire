"""Coverage for grillplat/numato_usbrelay.py: the low-level ASCII/serial
protocol driver for the Numato 4-Channel USB Solid State Relay board.

Unlike the platform-specific grillplat modules (prototype.py, x86_numato.py,
raspberry_pi_all.py), this module does not define a GrillPlatform class --
it's the NumatoUSBRelay serial driver that x86_numato.py's GrillPlatform
wraps (see the x86_platform fixture in tests/conftest.py, which mocks this
class wholesale). Here we test NumatoUSBRelay itself: pyserial is mocked out
(no real tty is opened), and each public method's on-the-wire command string
and response parsing is exercised directly.

Wire protocol being simulated (see module docstring in numato_usbrelay.py):
the board echoes the command, optionally emits a response, then emits a
'>' prompt. `_send_command` writes `<command>\\r`, reads byte-by-byte until
'>', then `_extract_response` strips the echoed command line and returns
the remaining response text.
"""

from unittest import mock

import pytest

import grillplat.numato_usbrelay as numato
from grillplat.numato_usbrelay import (
    NumatoResponseError,
    NumatoUSBRelay,
)


# ---------------------------------------------------------------------------
# Serial mocking helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def serial_mock():
    """Patch the `serial` module referenced by numato_usbrelay.py so no real
    tty is opened. `serial.Serial(...)` returns a fresh Mock instance that
    stands in for the open port; instance.read(1) is driven per-test via
    `_respond()` below.
    """
    with mock.patch.object(numato, "serial") as serial_module:
        instance = mock.Mock()
        instance.is_open = True
        serial_module.Serial.return_value = instance
        yield serial_module, instance


def _one_byte_chunks(data: bytes):
    return [data[i : i + 1] for i in range(len(data))]


def _respond(instance, command, response=None):
    """Queue the next `instance.read(1)` call sequence to represent the
    board's echo of `command` (plus an optional response line), followed by
    the '>' prompt that terminates `_read_until_prompt`.
    """
    text = command + "\r\n"
    if response is not None:
        text += response + "\r\n"
    instance.read.side_effect = _one_byte_chunks(text.encode("ascii") + b">")


# ---------------------------------------------------------------------------
# __init__ / lifecycle
# ---------------------------------------------------------------------------


def test_init_configures_serial_port_with_expected_parameters(serial_mock):
    serial_module, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0", baudrate=115200, timeout=2.0)
    serial_module.Serial.assert_called_once_with(
        port="/dev/ttyACM0",
        baudrate=115200,
        bytesize=serial_module.EIGHTBITS,
        parity=serial_module.PARITY_NONE,
        stopbits=serial_module.STOPBITS_ONE,
        timeout=2.0,
    )
    assert relay.device == "/dev/ttyACM0"


def test_init_default_baudrate_and_timeout(serial_mock):
    serial_module, instance = serial_mock
    NumatoUSBRelay("/dev/ttyACM0")
    _, kwargs = serial_module.Serial.call_args
    assert kwargs["baudrate"] == 921600
    assert kwargs["timeout"] == 1.0


def test_close_closes_an_open_port(serial_mock):
    _, instance = serial_mock
    instance.is_open = True
    relay = NumatoUSBRelay("/dev/ttyACM0")
    relay.close()
    instance.close.assert_called_once()


def test_close_is_a_noop_when_already_closed(serial_mock):
    _, instance = serial_mock
    instance.is_open = False
    relay = NumatoUSBRelay("/dev/ttyACM0")
    relay.close()
    instance.close.assert_not_called()


def test_context_manager_returns_self_and_closes_on_exit(serial_mock):
    _, instance = serial_mock
    instance.is_open = True
    with NumatoUSBRelay("/dev/ttyACM0") as relay:
        assert isinstance(relay, NumatoUSBRelay)
    instance.close.assert_called_once()


# ---------------------------------------------------------------------------
# Relay control: relay_on / relay_off / relay_set
# ---------------------------------------------------------------------------


def test_relay_on_sends_relay_on_command(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay on 0")
    relay.relay_on(0)
    instance.write.assert_called_once_with(b"relay on 0\r")
    instance.flush.assert_called_once()
    instance.reset_input_buffer.assert_called_once()


def test_relay_off_sends_relay_off_command(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay off 3")
    relay.relay_off(3)
    instance.write.assert_called_once_with(b"relay off 3\r")


def test_relay_set_true_delegates_to_relay_on():
    relay = object.__new__(NumatoUSBRelay)
    with mock.patch.object(relay, "relay_on") as relay_on, mock.patch.object(relay, "relay_off") as relay_off:
        relay.relay_set(2, True)
    relay_on.assert_called_once_with(2)
    relay_off.assert_not_called()


def test_relay_set_false_delegates_to_relay_off():
    relay = object.__new__(NumatoUSBRelay)
    with mock.patch.object(relay, "relay_on") as relay_on, mock.patch.object(relay, "relay_off") as relay_off:
        relay.relay_set(2, False)
    relay_off.assert_called_once_with(2)
    relay_on.assert_not_called()


def test_relay_on_rejects_out_of_range_index(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError, match="relay index"):
        relay.relay_on(4)
    instance.write.assert_not_called()


def test_relay_on_rejects_negative_index(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError):
        relay.relay_on(-1)


# ---------------------------------------------------------------------------
# relay_read / relay_read_all / relay_write_all / reset
# ---------------------------------------------------------------------------


def test_relay_read_true_on(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay read 0", "on")
    assert relay.relay_read(0) is True


def test_relay_read_false_off(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay read 1", "off")
    assert relay.relay_read(1) is False


def test_relay_read_unparseable_response_raises(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay read 0", "maybe")
    with pytest.raises(NumatoResponseError):
        relay.relay_read(0)


def test_relay_read_all_decodes_bitmask_lsb_first(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    # 0x5 = 0b0101 -> relays 0 and 2 on, 1 and 3 off.
    _respond(instance, "relay readall", "5")
    assert relay.relay_read_all() == [True, False, True, False]


def test_relay_read_all_unparseable_response_raises(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay readall", "notahex")
    with pytest.raises(NumatoResponseError):
        relay.relay_read_all()


def test_relay_write_all_accepts_int_mask(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay writeall a")
    relay.relay_write_all(0xA)
    instance.write.assert_called_once_with(b"relay writeall a\r")


def test_relay_write_all_accepts_iterable_of_booleans(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "relay writeall a")
    # bit0=False, bit1=True, bit2=False, bit3=True -> 0b1010 = 0xa
    relay.relay_write_all([False, True, False, True])
    instance.write.assert_called_once_with(b"relay writeall a\r")


def test_relay_write_all_rejects_too_many_states(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError, match="at most"):
        relay.relay_write_all([True, True, True, True, True])
    instance.write.assert_not_called()


def test_relay_write_all_rejects_out_of_range_int_mask(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError, match="relay mask"):
        relay.relay_write_all(16)
    instance.write.assert_not_called()


def test_reset_sends_reset_command(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "reset")
    relay.reset()
    instance.write.assert_called_once_with(b"reset\r")


# ---------------------------------------------------------------------------
# GPIO control
# ---------------------------------------------------------------------------


def test_gpio_set_sends_gpio_set_command(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "gpio set 2")
    relay.gpio_set(2)
    instance.write.assert_called_once_with(b"gpio set 2\r")


def test_gpio_clear_sends_gpio_clear_command(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "gpio clear 2")
    relay.gpio_clear(2)
    instance.write.assert_called_once_with(b"gpio clear 2\r")


def test_gpio_write_true_delegates_to_gpio_set():
    relay = object.__new__(NumatoUSBRelay)
    with mock.patch.object(relay, "gpio_set") as gpio_set, mock.patch.object(relay, "gpio_clear") as gpio_clear:
        relay.gpio_write(1, True)
    gpio_set.assert_called_once_with(1)
    gpio_clear.assert_not_called()


def test_gpio_write_false_delegates_to_gpio_clear():
    relay = object.__new__(NumatoUSBRelay)
    with mock.patch.object(relay, "gpio_set") as gpio_set, mock.patch.object(relay, "gpio_clear") as gpio_clear:
        relay.gpio_write(1, False)
    gpio_clear.assert_called_once_with(1)
    gpio_set.assert_not_called()


def test_gpio_read_true_high(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "gpio read 3", "on")
    assert relay.gpio_read(3) is True


def test_gpio_set_rejects_out_of_range_index(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError, match="gpio index"):
        relay.gpio_set(4)


# ---------------------------------------------------------------------------
# ADC
# ---------------------------------------------------------------------------


def test_adc_read_returns_raw_counts(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "adc read 0", "512")
    assert relay.adc_read(0) == 512


def test_adc_read_non_numeric_response_raises(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "adc read 0", "notanumber")
    with pytest.raises(NumatoResponseError):
        relay.adc_read(0)


def test_adc_read_voltage_converts_counts_to_volts(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "adc read 0", "1023")
    assert relay.adc_read_voltage(0) == pytest.approx(5.0)


def test_adc_read_voltage_uses_custom_reference(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "adc read 1", "1023")
    assert relay.adc_read_voltage(1, reference=3.3) == pytest.approx(3.3)


def test_adc_read_rejects_out_of_range_index(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError, match="adc index"):
        relay.adc_read(4)


# ---------------------------------------------------------------------------
# System / informational
# ---------------------------------------------------------------------------


def test_version_returns_firmware_string(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "ver", "00000107")
    assert relay.version() == "00000107"


def test_id_get_returns_module_id(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "id get", "ABCD1234")
    assert relay.id_get() == "ABCD1234"


def test_id_set_sends_id_set_command(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    _respond(instance, "id set ABCD1234")
    relay.id_set("ABCD1234")
    instance.write.assert_called_once_with(b"id set ABCD1234\r")


def test_id_set_rejects_ids_of_wrong_length(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError, match="8 alphanumeric"):
        relay.id_set("short")
    instance.write.assert_not_called()


def test_id_set_rejects_non_alphanumeric_ids(serial_mock):
    _, instance = serial_mock
    relay = NumatoUSBRelay("/dev/ttyACM0")
    with pytest.raises(ValueError, match="8 alphanumeric"):
        relay.id_set("AB-D123!")
    instance.write.assert_not_called()
