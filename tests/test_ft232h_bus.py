from unittest import mock

import pytest

from common import ft232h, i2c_bus


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
		self.read_data = b'\x11\x22\x33'
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
	return controller, mock.patch.object(ft232h, '_new_controller', return_value=controller)


def test_construct_i2c_bus_returns_locked_i2c():
	controller, patch = _patch_controller()
	with patch:
		bus = i2c_bus.open_i2c_bus('ft232h', '')
	assert isinstance(bus, i2c_bus._LockedI2C)


def test_scan_uses_poll():
	controller, patch = _patch_controller()
	with patch:
		backend = ft232h._PyFtdiI2CBackend(controller)
	assert backend.scan() == [0x10, 0x50]


def test_blank_and_one_selector_share_one_controller():
	controller, patch = _patch_controller()
	with patch as new_controller:
		a = i2c_bus.open_i2c_bus('ft232h', '')
		b = i2c_bus.open_i2c_bus('ft232h', '1')
	assert a is b
	assert new_controller.call_count == 1  # one physical controller


def test_i2c_nack_becomes_oserror():
	from pyftdi.i2c import I2cNackError

	controller = FakeController()

	def boom(self, length, **kwargs):
		raise I2cNackError('nack')

	with mock.patch.object(ft232h, '_new_controller', return_value=controller):
		backend = ft232h._PyFtdiI2CBackend(controller)
	with mock.patch.object(FakePort, 'read', boom):
		buf = bytearray(1)
		with pytest.raises(OSError):
			backend.readfrom_into(0x10, buf)


def test_runtime_rejects_basic_after_ft232h():
	controller, patch = _patch_controller()
	with patch:
		i2c_bus.open_i2c_bus('ft232h', '')
		with pytest.raises(i2c_bus.I2CBusConfigError):
			i2c_bus.open_i2c_bus('basic')


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
	with mock.patch.object(ft232h, '_new_controller', return_value=controller):
		gpio = ft232h.open_gpio('')
	gpio.setup_output('C0')  # bit 8
	gpio.setup_output('D4')  # bit 4
	assert port.direction == (1 << 8) | (1 << 4)


def test_set_toggles_only_its_own_bit():
	controller, port = _controller_with_gpio()
	with mock.patch.object(ft232h, '_new_controller', return_value=controller):
		gpio = ft232h.open_gpio('')
	for name in ('C0', 'C1', 'C2', 'C3'):
		gpio.setup_output(name)
	gpio.set('C1', True)  # bit 9
	gpio.set('C3', True)  # bit 11
	assert port.value == (1 << 9) | (1 << 11)
	gpio.set('C1', False)
	assert port.value == (1 << 11)  # C3 untouched


def test_unknown_pin_name_raises():
	controller, port = _controller_with_gpio()
	with mock.patch.object(ft232h, '_new_controller', return_value=controller):
		gpio = ft232h.open_gpio('')
	with pytest.raises(ValueError):
		gpio.setup_output('Z9')


def test_reserved_i2c_pin_raises():
	controller, port = _controller_with_gpio()
	with mock.patch.object(ft232h, '_new_controller', return_value=controller):
		gpio = ft232h.open_gpio('')
	for reserved in ('D0', 'D1', 'D2', 'D3'):
		with pytest.raises(ValueError):
			gpio.setup_output(reserved)


def test_gpio_and_i2c_share_one_controller():
	controller, port = _controller_with_gpio()
	with mock.patch.object(ft232h, '_new_controller', return_value=controller) as new_controller:
		bus = i2c_bus.open_i2c_bus('ft232h', '')
		gpio = ft232h.open_gpio('1')  # '' and '1' alias
	assert new_controller.call_count == 1
	assert isinstance(bus, i2c_bus._LockedI2C)
	assert gpio.set  # smoke


def test_open_gpio_is_cached_per_controller():
	controller, port = _controller_with_gpio()
	with mock.patch.object(ft232h, '_new_controller', return_value=controller):
		a = ft232h.open_gpio('')
		b = ft232h.open_gpio('1')
	assert a is b


def test_configured_pins_can_all_be_cleared_after_setup():
	# Regression test for a bug where setup_output(pin) passed only that pin's
	# bit to pyftdi's set_direction(), which OVERWRITES (not accumulates)
	# pyftdi's internal gpio_mask. That left write_gpio()'s read-modify-write
	# able to clear only the most-recently-configured pin -- earlier output
	# pins got stuck ON once driven high. Configuring several output pins one
	# at a time must still allow every one of them to be cleared afterward.
	controller, port = _controller_with_gpio()
	with mock.patch.object(ft232h, '_new_controller', return_value=controller):
		gpio = ft232h.open_gpio('')
	for name in ('C0', 'C1', 'C2', 'C3'):
		gpio.setup_output(name)
	for name in ('C0', 'C1', 'C2', 'C3'):
		gpio.set(name, True)
	gpio.set('C0', False)
	assert port.value & ft232h.Ft232hGpio.PIN_BITS['C0'] == 0
	for name in ('C1', 'C2', 'C3'):
		assert port.value & ft232h.Ft232hGpio.PIN_BITS[name] != 0
