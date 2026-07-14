import json
import os

from common.i2c_bus import find_i2c_bus


def _manifest():
	path = os.path.join(os.path.dirname(__file__), '..', 'wizard', 'wizard_manifest.json')
	return json.load(open(path))


def test_every_bridge_selector_offers_mcp2221():
	"""Every i2c_bus_num settings-dependency that offers the CP2112 bridge-name
	match must also offer MCP2221, so the MCP2221 kernel i2c bridge is selectable
	on the Extended bus."""
	manifest = _manifest()
	found = 0

	def walk(node):
		nonlocal found
		if isinstance(node, dict):
			opts = node.get('options')
			if isinstance(opts, dict) and 'CP2112' in opts:
				found += 1
				assert 'MCP2221' in opts, f'CP2112 bridge selector missing MCP2221 option: {list(opts)}'
				assert opts['MCP2221']  # non-empty label
			for value in node.values():
				walk(value)
		elif isinstance(node, list):
			for value in node:
				walk(value)

	walk(manifest['modules'])
	assert found, 'no CP2112 bridge selectors found'


def test_busio_probe_bus_num_is_free_text_and_documents_bridges():
	"""The busio probe i2c_bus_num field (which drives the Extended bus) is
	free text with a Discover button, and its description documents both
	bridge-name matches and the serial: selector."""
	manifest = _manifest()
	checked = 0
	for name in ('mcp9600_adafruit', 'ads1115_adafruit', 'ads1015_adafruit'):
		cfg = manifest['modules']['probes'][name]['device_specific']['config']
		field = next(c for c in cfg if c['label'] == 'i2c_bus_num')
		assert field['type'] == 'i2c_bus_num'
		assert 'list_values' not in field
		assert 'CP2112' in field['description']
		assert 'MCP2221' in field['description']
		assert 'serial:' in field['description']
		checked += 1
	assert checked == 3


def test_find_i2c_bus_matches_mcp2221_adapter(tmp_path):
	"""find_i2c_bus resolves an MCP2221 kernel i2c adapter by its 'MCP2221' name,
	the same substring-match mechanism CP2112 uses."""
	bus = tmp_path / 'i2c-7'
	bus.mkdir()
	(bus / 'name').write_text('MCP2221 usb-i2c bridge\n')
	# An unrelated adapter present alongside must not confuse the match.
	other = tmp_path / 'i2c-0'
	other.mkdir()
	(other / 'name').write_text('SMBus PIIX4 adapter\n')

	assert find_i2c_bus(match='MCP2221', devices_path=str(tmp_path)) == 7
