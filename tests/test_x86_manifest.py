import json
import os


def _manifest():
	path = os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')
	with open(path) as handle:
		return json.load(handle)


def test_x86_platform_entry_present():
	manifest = _manifest()
	entry = manifest['modules']['grillplatform']['x86_numato']
	assert entry['filename'] == 'x86_numato'
	assert 'adafruit-circuitpython-emc2101' in entry['py_dependencies']


def test_x86_platform_settings_dependencies():
	manifest = _manifest()
	deps = manifest['modules']['grillplatform']['x86_numato']['settings_dependencies']
	# Chip selector plus the selectable basic/extended I2C bus and address.
	assert set(deps['fan_controller_chip']['options']) == {'emc2101', 'emc2301'}
	assert deps['fan_controller_chip']['settings'] == ['platform', 'fan_controller', 'chip']
	assert deps['i2c_bus_kind']['settings'] == ['platform', 'fan_controller', 'i2c_bus_kind']
	assert deps['i2c_bus_num']['settings'] == ['platform', 'fan_controller', 'i2c_bus_num']
	assert deps['fan_controller_address']['settings'] == ['platform', 'fan_controller', 'address']
	assert '0x2f' in deps['fan_controller_address']['options']
	assert set(deps['i2c_bus_kind']['options']) == {'basic', 'extended'}
