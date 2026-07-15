import json
import os


def _manifest():
	path = os.path.join(os.path.dirname(__file__), '..', '..', 'wizard', 'wizard_manifest.json')
	with open(path) as handle:
		return json.load(handle)


def test_vl53l0x_entry_uses_adafruit_circuitpython():
	manifest = _manifest()
	entry = manifest['modules']['distance']['vl53l0x']
	assert entry['py_dependencies'] == ['adafruit-circuitpython-vl53l0x']
	assert entry['apt_dependencies'] == []


def test_vl53l4cd_entry_present():
	manifest = _manifest()
	entry = manifest['modules']['distance']['vl53l4cd']
	assert entry['filename'] == 'vl53l4cd'
	assert entry['py_dependencies'] == ['adafruit-circuitpython-vl53l4cd']
	assert entry['apt_dependencies'] == []
	assert entry['image'] == 'vl53l4cd.png'


def test_vl53l1x_entry_present():
	manifest = _manifest()
	entry = manifest['modules']['distance']['vl53l1x']
	assert entry['filename'] == 'vl53l1x'
	assert entry['py_dependencies'] == ['adafruit-circuitpython-vl53l1x']
	assert entry['apt_dependencies'] == []
	assert entry['image'] == 'vl53l1x.png'


def test_all_platforms_have_distance_i2c_fields():
	manifest = _manifest()
	platforms = manifest['modules']['grillplatform']
	for name, entry in platforms.items():
		deps = entry.get('settings_dependencies', {})

		assert 'device_distance_i2c_bus_kind' in deps, name
		assert deps['device_distance_i2c_bus_kind']['settings'] == ['platform', 'devices', 'distance', 'i2c_bus_kind']
		assert set(deps['device_distance_i2c_bus_kind']['options']) == {'basic', 'extended', 'ft232h', 'mcp2221'}

		assert 'device_distance_i2c_bus_num' in deps, name
		assert deps['device_distance_i2c_bus_num']['settings'] == ['platform', 'devices', 'distance', 'i2c_bus_num']

		assert 'device_distance_address' in deps, name
		assert deps['device_distance_address']['settings'] == ['platform', 'devices', 'distance', 'address']
		assert '0x29' in deps['device_distance_address']['options']


def test_distance_bus_kind_includes_usb_hid():
	import json
	import os

	manifest = json.load(open(os.path.join(os.path.dirname(__file__), '..', '..', 'wizard', 'wizard_manifest.json')))
	found = []

	def walk(node):
		if isinstance(node, dict):
			opts = node.get('options')
			if isinstance(opts, dict) and 'basic' in opts and 'extended' in opts:
				found.append(set(opts))
			for value in node.values():
				walk(value)
		elif isinstance(node, list):
			for value in node:
				walk(value)

	walk(manifest['modules'])
	assert found, 'no bus-kind selectors found'
	assert all({'ft232h', 'mcp2221'} <= opts for opts in found)
