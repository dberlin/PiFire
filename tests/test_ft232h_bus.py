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
