import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _manifest():
	with open(os.path.join(BASE, 'wizard', 'wizard_manifest.json')) as f:
		return json.load(f)


def _config_default(entry, option_name):
	for opt in entry['config']:
		if opt['option_name'] == option_name:
			return opt['default']
	raise AssertionError(f'{option_name} not in config')


def test_manifest_entry_present():
	entry = _manifest()['modules']['display']['qtquick_dsi_1024x600t']
	assert entry['filename'] == 'qtquick_dsi_1024x600t'
	assert _config_default(entry, 'display_data_filename') == './display/qtquick_dsi_1024x600t.json'
	assert _config_default(entry, 'input_types_supported') == ['button', 'touch']
	assert 'pyside6>=6.11.1' in entry['py_dependencies']
	assert entry['config'] != []


def test_accent_theme_option_present():
	opts = _manifest()['modules']['display']['qtquick_dsi_1024x600t']['config']
	accent = next(o for o in opts if o['option_name'] == 'accent_theme')
	assert accent['default'] == 'Ember'
	assert set(accent['list_values']) == {'Ember', 'Ice', 'Crimson'}
