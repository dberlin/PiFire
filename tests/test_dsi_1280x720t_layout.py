import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(BASE, 'display', 'dsi_800x480t.json')
OUT = os.path.join(BASE, 'display', 'dsi_1280x720t.json')

SCALE = 1.5
OFFSETS = {'profile_1': (40, 0), 'profile_2': (0, 40)}
SCREEN = {'profile_1': (1280, 720), 'profile_2': (720, 1280)}

EMBER_DASH_OBJECT_NAMES = [
	'header_bar',
	'probe_card_0',
	'probe_card_1',
	'probe_card_2',
	'probe_card_3',
	'probe_card_4',
	'primary_gauge',
	'cook_time',
	'lid_alert',
	'button_row',
	'system_card',
	'duty_pill_left',
	'duty_pill_right',
	'hopper_vertical',
]


def _load(path):
	with open(path) as f:
		return json.load(f)


def _iter_objects(profile):
	for section in ('home', 'dash'):
		for obj in profile.get(section, []):
			yield obj
	for section in ('menus', 'input'):
		for obj in profile.get(section, {}).values():
			yield obj


def test_metadata():
	d = _load(OUT)
	assert d['metadata']['name'] == 'dsi_1280x720t'
	assert d['metadata']['screen_width'] == 1280
	assert d['metadata']['screen_height'] == 720


def test_splash_image_unchanged():
	assert _load(OUT)['metadata']['splash_image'] == './static/img/display/splash_800x480.png'


def test_dash_background_is_bespoke_ember_background():
	assert _load(OUT)['metadata']['dash_background'].endswith('background_ember_1280x720.png')


def test_all_elements_on_screen():
	d = _load(OUT)
	for profile, (W, H) in SCREEN.items():
		for obj in _iter_objects(d[profile]):
			if 'position' not in obj or 'size' not in obj:
				continue
			x, y = obj['position']
			w, h = obj['size']
			assert 0 <= x and x + w <= W, f'{profile}:{obj.get("name")} x out of bounds'
			assert 0 <= y and y + h <= H, f'{profile}:{obj.get("name")} y out of bounds'


def test_profile_1_dash_is_bespoke_ember_layout():
	"""Task 25: profile_1.dash is a bespoke layout built from the new ember
	flexobject types (Tasks 17-23), not a scaled copy of the 800x480 source."""
	d = _load(OUT)
	dash = d['profile_1']['dash']
	names = [obj['name'] for obj in dash]
	assert names == EMBER_DASH_OBJECT_NAMES

	by_name = {obj['name']: obj for obj in dash}
	assert by_name['header_bar']['type'] == 'header_bar'
	assert by_name['primary_gauge']['type'] == 'gauge_ember'
	assert by_name['system_card']['type'] == 'system_card'
	assert by_name['duty_pill_left']['type'] == 'duty_pill'
	assert by_name['duty_pill_right']['type'] == 'duty_pill'
	assert by_name['hopper_vertical']['type'] == 'hopper_vertical'
	assert by_name['button_row']['type'] == 'button_row'
	assert by_name['lid_alert']['type'] == 'alert'
	for index in range(5):
		assert by_name[f'probe_card_{index}']['type'] == 'probe_card'


def test_profile_1_dash_objects_have_common_flexobject_keys():
	d = _load(OUT)
	for obj in d['profile_1']['dash']:
		assert isinstance(obj['animation_enabled'], bool)
		assert isinstance(obj['glow'], bool)
		assert isinstance(obj['data'], dict)
		assert isinstance(obj['button_list'], list)
		assert isinstance(obj['button_value'], list)
		assert isinstance(obj['touch_areas'], list)


def test_profile_2_dash_is_untouched_scaled_layout():
	"""profile_2 (portrait) is not part of Task 25 - it stays the scaled
	800x480-derived layout used by every other resolution."""
	d = _load(OUT)
	names = [obj['name'] for obj in d['profile_2']['dash']]
	assert 'primary_gauge' in names
	assert d['profile_2']['dash'][0]['type'] == 'gauge'
	assert 'header_bar' not in names


def _assert_scaled(so, oo, xoff, yoff):
	if 'position' in so:
		x, y = so['position']
		assert oo['position'] == [round(x * SCALE + xoff), round(y * SCALE + yoff)]
	if 'size' in so:
		w, h = so['size']
		assert oo['size'] == [round(w * SCALE), round(h * SCALE)]


def test_transform_matches_source_for_still_scaled_sections():
	"""Task 25 only replaces profile_1.dash and metadata.dash_background.
	profile_1's home/menus/input and everything in profile_2 (home/dash/
	menus/input) remain uniformly scaled from the 800x480 source, exactly
	like every other resolution this generator produces."""
	src = _load(SRC)
	out = _load(OUT)
	for profile, (xoff, yoff) in OFFSETS.items():
		sp, op = src[profile], out[profile]
		sections = ('home',) if profile == 'profile_1' else ('home', 'dash')
		for section in sections:
			for so, oo in zip(sp.get(section, []), op.get(section, [])):
				_assert_scaled(so, oo, xoff, yoff)
		for section in ('menus', 'input'):
			for key in sp.get(section, {}):
				_assert_scaled(sp[section][key], op[section][key], xoff, yoff)
