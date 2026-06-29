import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(BASE, 'display', 'dsi_800x480t.json')
OUT = os.path.join(BASE, 'display', 'dsi_1280x720t.json')

SCALE = 1.5
OFFSETS = {'profile_1': (40, 0), 'profile_2': (0, 40)}
SCREEN = {'profile_1': (1280, 720), 'profile_2': (720, 1280)}


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


def test_transform_matches_source():
	src = _load(SRC)
	out = _load(OUT)
	for profile, (xoff, yoff) in OFFSETS.items():
		sp, op = src[profile], out[profile]
		for section in ('home', 'dash'):
			for so, oo in zip(sp.get(section, []), op.get(section, [])):
				_assert_scaled(so, oo, xoff, yoff)
		for section in ('menus', 'input'):
			for key in sp.get(section, {}):
				_assert_scaled(sp[section][key], op[section][key], xoff, yoff)


def _assert_scaled(so, oo, xoff, yoff):
	if 'position' in so:
		x, y = so['position']
		assert oo['position'] == [round(x * SCALE + xoff), round(y * SCALE + yoff)]
	if 'size' in so:
		w, h = so['size']
		assert oo['size'] == [round(w * SCALE), round(h * SCALE)]
