from unittest import mock

import pytest


def _build(chip):
	"""Build the platform with hardware mocked. chip=None means no
	fan_controller config at all, exercising the default."""
	import grillplat.x86_numato as mod

	with (
		mock.patch.object(mod, 'NumatoUSBRelay'),
		mock.patch.object(mod, 'EMC2101_LUT') as emc2101_lut,
		mock.patch.object(mod, 'EMC2301') as emc2301,
		mock.patch.object(mod, 'open_i2c_bus'),
	):
		config = {} if chip is None else {'fan_controller': {'chip': chip}}
		platform = mod.GrillPlatform(config)
		return platform, emc2101_lut, emc2301


def test_emc2101_is_default_chip():
	# No fan_controller config -> EMC2101 by default.
	platform, emc2101_lut, emc2301 = _build(None)
	emc2101_lut.assert_called_once()
	emc2301.assert_not_called()
	assert platform.chip == 'emc2101'


def test_emc2301_selected_with_default_address():
	platform, emc2101_lut, emc2301 = _build('emc2301')
	emc2301.assert_called_once()
	# Default EMC2301 address is 0x2F when none configured.
	assert emc2301.call_args.kwargs['address'] == 0x2F
	emc2101_lut.assert_not_called()
	assert platform.chip == 'emc2301'


def test_emc2101_default_address_is_0x4c():
	platform, _, _ = _build('emc2101')
	assert platform.emc_address == 0x4C
