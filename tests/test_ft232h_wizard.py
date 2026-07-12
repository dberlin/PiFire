import json

from wizard import select_grillplat_module


def _settings(system_type, chip='none'):
	return {
		'modules': {'grillplat': 'prototype'},
		'platform': {'system_type': system_type, 'dc_fan': False, 'fan_controller': {'chip': chip}},
	}


def test_manifest_registers_ft232h_relay():
	with open('wizard/wizard_manifest.json') as handle:
		manifest = json.load(handle)
	entry = manifest['modules']['grillplatform']['ft232h_relay']
	assert entry['friendly_name'] == 'FT232H IO-Triggered Relay'
	assert entry['filename'] == 'ft232h_relay'
	assert 'pyftdi' in entry['py_dependencies']
	# Output pin dropdowns expose C0-C7 and D4-D7 only.
	pin_options = set(entry['settings_dependencies']['output_power']['options'])
	assert pin_options == {f'C{i}' for i in range(8)} | {f'D{i}' for i in range(4, 8)}
	# Fan mode option maps to fan_controller.chip and includes 'none'.
	fan_mode = entry['settings_dependencies']['fan_mode']
	assert fan_mode['settings'] == ['platform', 'fan_controller', 'chip']
	assert set(fan_mode['options']) == {'none', 'emc2101', 'emc2301'}


def test_ft232h_relay_selection_relay_mode_leaves_dc_fan_false():
	settings = _settings('ft232h_relay', chip='none')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'ft232h_relay'
	assert settings['platform']['dc_fan'] is False


def test_ft232h_relay_selection_emc_mode_sets_dc_fan_true():
	settings = _settings('ft232h_relay', chip='emc2101')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'ft232h_relay'
	assert settings['platform']['dc_fan'] is True


def test_existing_platforms_still_map():
	settings = _settings('x86_numato')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'x86_numato'
	assert settings['platform']['dc_fan'] is True

	settings = _settings('raspberry_pi_all')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'raspberry_pi_all'

	settings = _settings('something_unknown')
	select_grillplat_module(settings)
	assert settings['modules']['grillplat'] == 'prototype'
