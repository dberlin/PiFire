import contextlib
import types
from unittest import mock


class FakePin:
	"""Records the last value/direction written by digitalio."""

	def __init__(self):
		self.value = None
		self.direction = None
		self.deinit_called = False

	def deinit(self):
		self.deinit_called = True


class FakeDirection:
	OUTPUT = 'OUTPUT'
	INPUT = 'INPUT'


class FakeDigitalIO:
	"""Stand-in for Blinka's digitalio module."""

	Direction = FakeDirection

	def __init__(self):
		self.pins = {}

	def DigitalInOut(self, pin):
		created = FakePin()
		self.pins[pin] = created
		return created


class FakeBoard:
	"""Stand-in for Blinka's board module: C0-C7, D0-D7, SCL, SDA as sentinels."""

	def __init__(self):
		for bank in ('C', 'D'):
			for index in range(8):
				setattr(self, f'{bank}{index}', f'{bank}{index}')
		self.SCL = 'SCL'
		self.SDA = 'SDA'


@contextlib.contextmanager
def make_ft232h_platform(config):
	"""Build a GrillPlatform with FT232H/EMC/I2C hardware faked.

	Yields (platform, harness); harness carries the fakes/mocks for assertions.
	"""
	import grillplat.ft232h_relay as mod

	fake_board = FakeBoard()
	fake_dio = FakeDigitalIO()
	with (
		mock.patch.object(mod, '_load_ft232h', return_value=(fake_board, fake_dio)),
		mock.patch.object(mod, 'open_i2c_bus', return_value=mock.sentinel.ft232h_bus) as open_bus,
		mock.patch.object(mod, 'EMC2101_LUT') as emc2101_cls,
		mock.patch.object(mod, 'EMC2301') as emc2301_cls,
	):
		platform = mod.GrillPlatform(config)
		harness = types.SimpleNamespace(
			board=fake_board, dio=fake_dio, open_bus=open_bus, emc2101_cls=emc2101_cls, emc2301_cls=emc2301_cls
		)
		yield platform, harness
