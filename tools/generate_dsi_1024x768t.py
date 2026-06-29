#!/usr/bin/env python3
"""
Generate display/dsi_1024x768t.json from display/dsi_800x480t.json.

Strategy A: uniform 1.28x scale (1024/800) with centering on the slack axis.
Re-run after changing the 800x480 layout:
    python tools/generate_dsi_1024x768t.py
"""

import json
import copy
import os

SCALE = 1024 / 800  # 1.28 ; 768/480 = 1.6, so width is the binding dimension

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(BASE, 'display', 'dsi_800x480t.json')
OUT = os.path.join(BASE, 'display', 'dsi_1024x768t.json')

# Rotated canvas each profile is authored for: (width, height).
TARGET_DIMS = {'profile_1': (1024, 768), 'profile_2': (768, 1024)}
SOURCE_DIMS = {'profile_1': (800, 480), 'profile_2': (480, 800)}


def _offsets(profile):
	tw, th = TARGET_DIMS[profile]
	sw, sh = SOURCE_DIMS[profile]
	return round((tw - sw * SCALE) / 2), round((th - sh * SCALE) / 2)


def _scale_obj(obj, xoff, yoff):
	if 'position' in obj:
		x, y = obj['position']
		obj['position'] = [round(x * SCALE + xoff), round(y * SCALE + yoff)]
	if 'size' in obj:
		w, h = obj['size']
		obj['size'] = [round(w * SCALE), round(h * SCALE)]


def scale_layout(src):
	data = copy.deepcopy(src)
	data['metadata']['name'] = 'dsi_1024x768t'
	data['metadata']['screen_width'] = 1024
	data['metadata']['screen_height'] = 768
	for profile in ('profile_1', 'profile_2'):
		if profile not in data:
			continue
		xoff, yoff = _offsets(profile)
		prof = data[profile]
		for section in ('home', 'dash'):
			for obj in prof.get(section, []):
				_scale_obj(obj, xoff, yoff)
		for section in ('menus', 'input'):
			for obj in prof.get(section, {}).values():
				_scale_obj(obj, xoff, yoff)
	return data


def main():
	with open(SRC) as f:
		src = json.load(f)
	out = scale_layout(src)
	with open(OUT, 'w') as f:
		json.dump(out, f, indent=2)
		f.write('\n')
	print(f'Wrote {OUT}')


if __name__ == '__main__':
	main()
