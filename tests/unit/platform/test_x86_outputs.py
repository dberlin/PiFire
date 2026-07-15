import pytest
from unittest import mock


@pytest.fixture
def x86_platform():
	"""Override conftest's x86_platform: this file's assertions need the
	mocked relay/EMC/bus-open classes captured on the platform instance, and
	a non-default `frequency` in config, unlike the other x86_* test files.
	"""
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay') as relay_cls,
		mock.patch.object(mod, 'EMC2101_LUT') as emc_cls,
		mock.patch.object(mod, 'EMC2301'),
		mock.patch.object(mod, 'open_i2c_bus') as open_bus,
	):
		config = {'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}, 'frequency': 100}
		plat = mod.GrillPlatform(config)
		plat._relay_cls = relay_cls
		plat._emc_cls = emc_cls
		plat._open_bus = open_bus
		yield plat


def test_init_opens_relay_and_emc(x86_platform):
	# Relay opened on the default device; EMC2101 constructed on the default
	# (basic / integrated) I2C bus, so the extended bus is not used here.
	# Bus-kind selection itself is covered in test_x86_bus_discovery.
	x86_platform._relay_cls.assert_called_once()
	assert x86_platform._relay_cls.call_args.args[0] == '/dev/ttyACM0'
	x86_platform._open_bus.assert_called_once_with('basic', 'CP2112')
	x86_platform._emc_cls.assert_called_once()


def test_auger_on_off_uses_mapped_relay(x86_platform):
	x86_platform.auger_on()
	x86_platform.relay.relay_on.assert_called_with(2)
	x86_platform.auger_off()
	x86_platform.relay.relay_off.assert_called_with(2)


def test_power_and_igniter_use_mapped_relays(x86_platform):
	x86_platform.power_on()
	x86_platform.relay.relay_on.assert_called_with(0)
	x86_platform.igniter_on()
	x86_platform.relay.relay_on.assert_called_with(1)


def test_get_output_status_reflects_cached_state(x86_platform):
	x86_platform.auger_on()
	x86_platform.igniter_on()
	status = x86_platform.get_output_status()
	assert status['auger'] is True
	assert status['igniter'] is True
	assert status['power'] is False
	assert status['fan'] is False


def test_get_input_status_is_false_when_standalone(x86_platform):
	assert x86_platform.get_input_status() is False
