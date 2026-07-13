from unittest import mock


def _base_config(**fan):
	return {
		'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3},
		'numato': {'device': '/dev/ttyACM0'},
		'fan_controller': fan,
		'frequency': 25000,
	}


def _make(config):
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'open_i2c_bus', return_value=mock.sentinel.bus) as open_bus,
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT') as emc2101,
		mock.patch.object(mod, 'EMC2301') as emc2301,
	):
		platform = mod.GrillPlatform(config)
		return platform, open_bus, emc2101, emc2301


def test_emc_bus_opened_via_factory_mcp2221():
	platform, open_bus, emc2101, emc2301 = _make(
		_base_config(chip='emc2101', i2c_bus_kind='mcp2221', i2c_bus_num='SERIAL9')
	)
	open_bus.assert_called_once_with('mcp2221', 'SERIAL9')
	emc2101.assert_called_once_with(mock.sentinel.bus)
	emc2301.assert_not_called()
