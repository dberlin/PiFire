import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _manifest():
	with open(os.path.join(BASE, 'wizard', 'wizard_manifest.json')) as f:
		return json.load(f)


def _config_default(entry, option_name):
	for opt in entry['config']:
		if opt['option_name'] == option_name:
			return opt['default']
	raise AssertionError(f'{option_name} not in config')


def test_manifest_entry_present():
	entry = _manifest()['modules']['display']['dsi_1024x600t']
	assert entry['filename'] == 'dsi_1024x600t'
	assert _config_default(entry, 'display_data_filename') == './display/dsi_1024x600t.json'
	assert entry['config'] != []


def test_default_display_config_includes_entry():
	cwd = os.getcwd()
	os.chdir(BASE)
	try:
		from common.common import _default_display_config

		config = _default_display_config()
	finally:
		os.chdir(cwd)
	assert 'dsi_1024x600t' in config
	assert config['dsi_1024x600t']['display_data_filename'] == './display/dsi_1024x600t.json'


def test_accent_theme_option_present():
	opts = _manifest()['modules']['display']['dsi_1024x600t']['config']
	names = [o['option_name'] for o in opts]
	assert 'accent_theme' in names
	accent = next(o for o in opts if o['option_name'] == 'accent_theme')
	assert accent['default'] == 'Ember'
	assert set(accent['list_values']) == {'Ember', 'Ice', 'Crimson'}
