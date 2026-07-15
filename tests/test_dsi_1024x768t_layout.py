from tests.conftest import DSI_LAYOUT_SRC, dsi_layout_out_path, load_json

OUT = dsi_layout_out_path('dsi_1024x768t')
SRC = DSI_LAYOUT_SRC

SCALE = 1024 / 800
OFFSETS = {'profile_1': (0, 77), 'profile_2': (77, 0)}


def test_transform_matches_source():
	src = load_json(SRC)
	out = load_json(OUT)
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
