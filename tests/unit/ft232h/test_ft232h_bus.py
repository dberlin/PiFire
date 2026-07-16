from unittest import mock

import pytest

from common import i2c_bus
from grillplat import ft232h


class FakePort:
    def __init__(self, controller, address):
        self.controller = controller
        self.address = address

    def write(self, data, **kwargs):
        self.controller.writes.append((self.address, bytes(data)))

    def read(self, length, **kwargs):
        return bytes(self.controller.read_data[:length])

    def exchange(self, out, readlen=0, **kwargs):
        self.controller.writes.append((self.address, bytes(out)))
        return bytes(self.controller.read_data[:readlen])


class FakeController:
    def __init__(self):
        self.configured_url = None
        self.frequency = None
        self.writes = []
        self.read_data = b"\x11\x22\x33"
        self.present = {0x10, 0x50}
        self.terminated = False

    def get_port(self, address):
        return FakePort(self, address)

    def poll(self, address, write=False, relax=True):
        return address in self.present

    def terminate(self):
        self.terminated = True


@pytest.fixture(autouse=True)
def _clean():
    ft232h.reset_state()
    i2c_bus.reset_bus_state()
    yield
    ft232h.reset_state()
    i2c_bus.reset_bus_state()


def _patch_controller():
    controller = FakeController()
    return controller, mock.patch.object(ft232h, "_new_controller", return_value=controller)


def test_construct_i2c_bus_returns_locked_i2c():
    controller, patch = _patch_controller()
    with patch:
        bus = i2c_bus.open_i2c_bus("ft232h", "")
    assert isinstance(bus, i2c_bus._LockedI2C)


def test_scan_uses_poll():
    controller, patch = _patch_controller()
    with patch:
        backend = ft232h._PyFtdiI2CBackend(controller)
    assert backend.scan() == [0x10, 0x50]


def test_blank_and_one_selector_share_one_controller():
    controller, patch = _patch_controller()
    with patch as new_controller:
        a = i2c_bus.open_i2c_bus("ft232h", "")
        b = i2c_bus.open_i2c_bus("ft232h", "1")
    assert a is b
    assert new_controller.call_count == 1  # one physical controller


def test_blank_selector_produces_a_real_pyftdi_url():
    # Regression test for a production crash: canonical_url() used to return
    # the bare string "1" for a blank/None/"1" selector, which fails pyftdi's
    # own URL scheme check (urlsplit("1").scheme == "") and raises
    # UsbToolsError("Invalid URL: 1") -- see discover_ft232h_devices() in
    # common/ft232h.py, which already builds "ftdi://ftdi:232h/1" for this
    # exact case.
    controller, patch = _patch_controller()
    with patch as new_controller:
        i2c_bus.open_i2c_bus("ft232h", "")
    called_url = new_controller.call_args[0][0]
    assert called_url.startswith("ftdi://"), called_url


def test_canonical_url_blank_none_and_one_all_map_to_first_device_url():
    assert ft232h.canonical_url("") == "ftdi://ftdi:232h/1"
    assert ft232h.canonical_url(None) == "ftdi://ftdi:232h/1"
    assert ft232h.canonical_url("1") == "ftdi://ftdi:232h/1"


def test_canonical_url_passes_through_explicit_urls():
    assert ft232h.canonical_url("ftdi://ftdi:232h:AB1234/1") == "ftdi://ftdi:232h:AB1234/1"


def test_i2c_nack_becomes_oserror():
    from pyftdi.i2c import I2cNackError

    controller = FakeController()

    def boom(self, length, **kwargs):
        raise I2cNackError("nack")

    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        backend = ft232h._PyFtdiI2CBackend(controller)
    with mock.patch.object(FakePort, "read", boom):
        buf = bytearray(1)
        with pytest.raises(OSError):
            backend.readfrom_into(0x10, buf)


def test_runtime_rejects_basic_after_ft232h():
    controller, patch = _patch_controller()
    with patch:
        i2c_bus.open_i2c_bus("ft232h", "")
        with pytest.raises(i2c_bus.I2CBusConfigError):
            i2c_bus.open_i2c_bus("basic")


class FakeGpioPort:
    def __init__(self):
        self.direction = 0  # accumulates, for tests that check the full output-pin set
        self._gpio_mask = 0  # pyftdi semantics: OVERWRITTEN (not accumulated) each call
        self.value = 0

    def set_direction(self, pins, direction):
        # pyftdi semantics: 1 bits in `pins` are (re)configured to `direction`.
        self.direction = (self.direction & ~pins) | (direction & pins)
        # pyftdi.i2c.I2cController._set_gpio_direction: self._gpio_mask = gpio_mask & pins
        # -- it OVERWRITES the mask with only the pins passed in *this* call.
        self._gpio_mask = pins

    def write(self, value):
        # pyftdi.i2c.I2cController.write_gpio: masked read-modify-write, only
        # clearing bits within the current _gpio_mask before applying `value`.
        self.value = (self.value & ~self._gpio_mask) | value

    def read(self, with_output=False):
        return self.value


def _controller_with_gpio():
    controller = FakeController()
    port = FakeGpioPort()
    controller.get_gpio = lambda: port
    return controller, port


def test_setup_output_sets_direction_bits():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        gpio = ft232h.open_gpio("")
    gpio.setup_output("C0")  # bit 8
    gpio.setup_output("D4")  # bit 4
    assert port.direction == (1 << 8) | (1 << 4)


def test_set_toggles_only_its_own_bit():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        gpio = ft232h.open_gpio("")
    for name in ("C0", "C1", "C2", "C3"):
        gpio.setup_output(name)
    gpio.set("C1", True)  # bit 9
    gpio.set("C3", True)  # bit 11
    assert port.value == (1 << 9) | (1 << 11)
    gpio.set("C1", False)
    assert port.value == (1 << 11)  # C3 untouched


def test_unknown_pin_name_raises():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        gpio = ft232h.open_gpio("")
    with pytest.raises(ValueError):
        gpio.setup_output("Z9")


def test_reserved_i2c_pin_raises():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        gpio = ft232h.open_gpio("")
    for reserved in ("D0", "D1", "D2", "D3"):
        with pytest.raises(ValueError):
            gpio.setup_output(reserved)


def test_gpio_and_i2c_share_one_controller():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller) as new_controller:
        bus = i2c_bus.open_i2c_bus("ft232h", "")
        gpio = ft232h.open_gpio("1")  # '' and '1' alias
    assert new_controller.call_count == 1
    assert isinstance(bus, i2c_bus._LockedI2C)
    assert gpio.set  # smoke


def test_open_gpio_is_cached_per_controller():
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        a = ft232h.open_gpio("")
        b = ft232h.open_gpio("1")
    assert a is b


def test_configured_pins_can_all_be_cleared_after_setup():
    # Regression test for a bug where setup_output(pin) passed only that pin's
    # bit to pyftdi's set_direction(), which OVERWRITES (not accumulates)
    # pyftdi's internal gpio_mask. That left write_gpio()'s read-modify-write
    # able to clear only the most-recently-configured pin -- earlier output
    # pins got stuck ON once driven high. Configuring several output pins one
    # at a time must still allow every one of them to be cleared afterward.
    controller, port = _controller_with_gpio()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        gpio = ft232h.open_gpio("")
    for name in ("C0", "C1", "C2", "C3"):
        gpio.setup_output(name)
    for name in ("C0", "C1", "C2", "C3"):
        gpio.set(name, True)
    gpio.set("C0", False)
    assert port.value & ft232h.Ft232hGpio.PIN_BITS["C0"] == 0
    for name in ("C1", "C2", "C3"):
        assert port.value & ft232h.Ft232hGpio.PIN_BITS[name] != 0


# --- Integration test against the REAL pyftdi GPIO logic -------------------
#
# The FakeGpioPort tests above encode OUR understanding of pyftdi's mask
# semantics in the fake. The original stuck-relay bug slipped through precisely
# because that model was wrong -- a fake can't catch a mistake baked into the
# fake itself. The test below instead drives the real pyftdi I2cController /
# I2cGpioPort classes and stubs ONLY the lowest-level FTDI transport with a
# dumb pin latch (SET_BITS writes, GET_BITS reads). All GPIO masking is pyftdi's
# own code, so this catches any mismatch between Ft232hGpio and pyftdi's actual
# behavior -- the class of bug the fakes cannot.


class _FakeFtdi:
    """FT232H pin latch at the MPSSE-command level. Stores what SET_BITS_LOW/HIGH
    drive and returns it on GET_BITS_LOW/HIGH. Encodes NO GPIO masking -- that
    lives in the real I2cController under test."""

    def __init__(self):
        self.latch = 0  # 16-bit hardware pin state
        self._pending = bytearray()

    @property
    def is_connected(self):
        return True

    def write_data(self, data):
        from pyftdi.ftdi import Ftdi

        data = bytes(data)
        i = 0
        while i < len(data):
            cmd = data[i]
            if cmd == Ftdi.SET_BITS_LOW:
                value, direction = data[i + 1], data[i + 2]
                self.latch = (self.latch & 0xFF00) | (value & direction)
                i += 3
            elif cmd == Ftdi.SET_BITS_HIGH:
                value, direction = data[i + 1], data[i + 2]
                self.latch = (self.latch & 0x00FF) | ((value & direction) << 8)
                i += 3
            elif cmd == Ftdi.GET_BITS_LOW:
                self._pending.append(self.latch & 0xFF)
                i += 1
            elif cmd == Ftdi.GET_BITS_HIGH:
                self._pending.append((self.latch >> 8) & 0xFF)
                i += 1
            else:  # SEND_IMMEDIATE and anything else: no state change
                i += 1
        return len(data)

    def read_data_bytes(self, size, attempt=1, request_gen=None):
        out = self._pending[:size]
        del self._pending[:size]
        return bytearray(out)


def _real_pyftdi_controller():
    """A real pyftdi I2cController with only the USB transport faked, wired up
    for wide-port (FT232H) GPIO without a physical device."""
    from pyftdi.i2c import I2cController

    controller = I2cController()
    controller._ftdi = _FakeFtdi()
    controller._wide_port = True  # FT232H is a 16-bit wide port
    controller._i2c_mask = I2cController.I2C_MASK  # reserve AD0/AD1/AD2 for I2C
    return controller


def test_real_pyftdi_multi_setup_allows_clearing_each_relay():
    # Same scenario as test_configured_pins_can_all_be_cleared_after_setup, but
    # against the REAL pyftdi masking logic (only the FTDI transport is faked).
    # This is the test that would have caught the original bug: with
    # setup_output passing a single bit to set_direction(), pyftdi's gpio_mask
    # ends up holding only the last-configured pin, so write_gpio() cannot clear
    # the earlier pins -- they stay driven high in the hardware latch.
    controller = _real_pyftdi_controller()
    with mock.patch.object(ft232h, "_new_controller", return_value=controller):
        gpio = ft232h.open_gpio("")
    for name in ("C0", "C1", "C2", "C3"):
        gpio.setup_output(name)
    for name in ("C0", "C1", "C2", "C3"):
        gpio.set(name, True)
    latch = controller._ftdi.latch
    assert latch & ft232h.Ft232hGpio.PIN_BITS["C0"]  # all four driven high
    gpio.set("C0", False)
    latch = controller._ftdi.latch
    assert latch & ft232h.Ft232hGpio.PIN_BITS["C0"] == 0  # C0 actually cleared
    for name in ("C1", "C2", "C3"):
        assert latch & ft232h.Ft232hGpio.PIN_BITS[name] != 0  # others untouched
