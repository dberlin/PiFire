#!/usr/bin/env python3
"""
Generate display/dsi_<W>x<H>t.json from display/dsi_800x480t.json.

Uniform fit-scale (min of per-axis ratios) with centering on the slack axis.
Re-run after changing the 800x480 layout:
    python tools/generate_dsi_layout.py
"""

import json
import copy
import os

SOURCE_W, SOURCE_H = 800, 480

# Target resolutions this generator owns. (width, height) of the landscape
# profile_1 canvas; profile_2 is the same canvas rotated. Adding a resolution
# here also requires a display/dsi_<W>x<H>t.py re-export module, a
# wizard_manifest.json entry, and a paired byte-identical regression assertion
# in tests/test_dsi_layout_generator.py.
RESOLUTIONS = [(1024, 768), (1280, 720), (1024, 600)]

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SOURCE_PATH = os.path.join(BASE, 'display', 'dsi_800x480t.json')


def out_path(width, height):
	return os.path.join(BASE, 'display', f'dsi_{width}x{height}t.json')


def _scale_for(width, height):
	return min(width / SOURCE_W, height / SOURCE_H)


def _profile_dims(width, height):
	# profile_1 is the target as given (landscape); profile_2 is rotated.
	return {
		'profile_1': {'target': (width, height), 'source': (SOURCE_W, SOURCE_H)},
		'profile_2': {'target': (height, width), 'source': (SOURCE_H, SOURCE_W)},
	}


def _offsets(target, source, scale):
	tw, th = target
	sw, sh = source
	return round((tw - sw * scale) / 2), round((th - sh * scale) / 2)


def _scale_obj(obj, scale, xoff, yoff):
	if 'position' in obj:
		x, y = obj['position']
		obj['position'] = [round(x * scale + xoff), round(y * scale + yoff)]
	if 'size' in obj:
		w, h = obj['size']
		obj['size'] = [round(w * scale), round(h * scale)]


def _flex_obj(name, obj_type, position, size, **extra):
	"""Common FlexObject schema keys every dash object needs (see
	display/flexobject.py::FlexObject and display/base_flex.py::_draw_objects,
	both of which access 'animation_enabled'/'glow' directly)."""
	obj = {
		'name': name,
		'type': obj_type,
		'position': list(position),
		'animation_enabled': False,
		'size': list(size),
		'glow': False,
		'data': {},
		'button_list': [],
		'button_value': [],
		'touch_areas': [],
	}
	obj.update(extra)
	return obj


def _dashboard_1280x720():
	"""Bespoke ember-style profile_1.dash for the 1280x720 DSI display (Task 25),
	built from the new flexobject types (Tasks 17-23) wired up by
	display/base_flex.py (Task 24). Decoupled from the 800x480 scaler used by
	every other resolution/profile.

	Layout (see .superpowers/sdd/progress.md "pygame flexobject data
	contracts" for the per-type data shapes):
	  - header_bar spans the full width at the top.
	  - Left column: 5 stacked probe_card_N food-probe cards.
	  - Center column: the big gauge_ember primary gauge, the full-width
	    cook_time bar (which doubles as the lid-open alert), and the
	    mode-dependent button_row.
	  - Right column: system_card (fan/auger/igniter), two duty_pill status
	    pills, and the hopper_vertical pellet-level card.

	'cook_time' uses the horizontal 'cook_time_bar' widget (label left, value
	right, spanning the full gauge width); base_flex._cook_time_data() feeds it
	{'label': ..., 'value': ...} (see display/base_flex.py). When the lid opens
	base_flex feeds a 'Lid Pause' countdown and the bar recolors red, so the
	ember dashboards need no separate lid_alert overlay. The existing 'timer' type
	(TimerStatus) does not fit - it reads data['seconds'] and only shows a
	live countdown ("Ns"), so it can't render the pre-formatted mm:ss/H:MM:SS
	elapsed-cook-time string cook_time also needs to display.
	"""
	dash = [
		_flex_obj(
			'header_bar',
			'header_bar',
			[0, 0],
			[1280, 58],
			data={'ip': '', 'clock': '', 'cooking': False},
			button_list=['menu_main'],
		)
	]
	probe_card_y = [74, 202, 330, 458, 586]
	for index, y in enumerate(probe_card_y):
		dash.append(
			_flex_obj(
				f'probe_card_{index}',
				'probe_card',
				[18, y],
				[298, 116],
				units='F',
				data={'name': '', 'temp': 0, 'target': 0},
				button_list=['input_notify'],
			)
		)
	dash.append(
		_flex_obj(
			'primary_gauge',
			'gauge_ember',
			[332, 74],
			[614, 452],
			animation_enabled=True,
			glow=True,
			temps=[0, 0, 0],
			max_temp=600,
			units='F',
			label='Grill',
			data={'mode_label': ''},
			button_list=['input_notify'],
		)
	)
	dash.append(
		_flex_obj(
			'cook_time', 'cook_time_bar', [332, 538], [614, 52], data={'label': '', 'value': '', 'highlight': False}
		)
	)
	dash.append(_flex_obj('button_row', 'button_row', [332, 602], [614, 100], button_type=[], button_active=''))
	dash.append(
		_flex_obj(
			'system_card',
			'system_card',
			[962, 74],
			[300, 230],
			animation_enabled=True,
			data={'fan': False, 'auger': False, 'igniter': False},
			button_list=['cmd_fan_toggle', 'cmd_auger_toggle', 'cmd_igniter_toggle'],
		)
	)
	dash.append(
		_flex_obj(
			'duty_pill_left', 'duty_pill', [962, 318], [143, 64], data={'label': '', 'value': '', 'highlight': False}
		)
	)
	dash.append(
		_flex_obj(
			'duty_pill_right', 'duty_pill', [1119, 318], [143, 64], data={'label': '', 'value': '', 'highlight': False}
		)
	)
	dash.append(
		_flex_obj(
			'hopper_vertical',
			'hopper_vertical',
			[962, 396],
			[300, 306],
			data={'level': 0, 'enabled': True},
			button_list=['cmd_hopper_level'],
		)
	)
	return dash


def _dashboard_1024x600():
	"""Bespoke ember-style profile_1.dash for the 1024x600 DSI display.

	Same three-column ember design as _dashboard_1280x720(), reflowed for the
	shorter/wider 1024x600 canvas (14px margins; content bottoms at y=574).
	Widgets resize their rendered image to these boxes (see
	display/flexobject.py). One exception: 'cook_time' uses the horizontal
	'cook_time_bar' widget (label left, value right) instead of the vertical
	'duty_pill' - spanning the full gauge width as a stadium-shaped pill
	distorts the stacked pill text, so this bar renders on an aspect-matched
	canvas. When the lid opens the bar recolors red (base_flex feeds a 'Lid
	Pause' countdown); there is no separate lid_alert overlay.
	"""
	dash = [
		_flex_obj(
			'header_bar',
			'header_bar',
			[0, 0],
			[1024, 50],
			data={'ip': '', 'clock': '', 'cooking': False},
			button_list=['menu_main'],
		)
	]
	probe_card_y = [62, 166, 270, 374, 478]
	for index, y in enumerate(probe_card_y):
		dash.append(
			_flex_obj(
				f'probe_card_{index}',
				'probe_card',
				[14, y],
				[238, 96],
				units='F',
				data={'name': '', 'temp': 0, 'target': 0},
				button_list=['input_notify'],
			)
		)
	dash.append(
		_flex_obj(
			'primary_gauge',
			'gauge_ember',
			[266, 62],
			[490, 352],
			animation_enabled=True,
			glow=True,
			temps=[0, 0, 0],
			max_temp=600,
			units='F',
			label='Grill',
			data={'mode_label': ''},
			button_list=['input_notify'],
		)
	)
	dash.append(
		_flex_obj(
			'cook_time', 'cook_time_bar', [266, 424], [490, 40], data={'label': '', 'value': '', 'highlight': False}
		)
	)
	dash.append(_flex_obj('button_row', 'button_row', [266, 474], [490, 100], button_type=[], button_active=''))
	dash.append(
		_flex_obj(
			'system_card',
			'system_card',
			[770, 62],
			[240, 190],
			animation_enabled=True,
			data={'fan': False, 'auger': False, 'igniter': False},
			button_list=['cmd_fan_toggle', 'cmd_auger_toggle', 'cmd_igniter_toggle'],
		)
	)
	dash.append(
		_flex_obj(
			'duty_pill_left', 'duty_pill', [770, 262], [115, 40], data={'label': '', 'value': '', 'highlight': False}
		)
	)
	dash.append(
		_flex_obj(
			'duty_pill_right', 'duty_pill', [895, 262], [115, 40], data={'label': '', 'value': '', 'highlight': False}
		)
	)
	dash.append(
		_flex_obj(
			'hopper_vertical',
			'hopper_vertical',
			[770, 312],
			[240, 262],
			data={'level': 0, 'enabled': True},
			button_list=['cmd_hopper_level'],
		)
	)
	return dash


def build(width, height):
	with open(SOURCE_PATH) as f:
		src = json.load(f)
	data = copy.deepcopy(src)
	data['metadata']['name'] = f'dsi_{width}x{height}t'
	data['metadata']['screen_width'] = width
	data['metadata']['screen_height'] = height
	scale = _scale_for(width, height)
	dims = _profile_dims(width, height)
	for profile in ('profile_1', 'profile_2'):
		if profile not in data:
			continue
		xoff, yoff = _offsets(dims[profile]['target'], dims[profile]['source'], scale)
		prof = data[profile]
		for section in ('home', 'dash'):
			for obj in prof.get(section, []):
				_scale_obj(obj, scale, xoff, yoff)
		for section in ('menus', 'input'):
			for obj in prof.get(section, {}).values():
				_scale_obj(obj, scale, xoff, yoff)

	bespoke = {
		(1280, 720): (_dashboard_1280x720, './static/img/display/background_ember_1280x720.png'),
		(1024, 600): (_dashboard_1024x600, './static/img/display/background_ember_1024x600.png'),
	}
	if (width, height) in bespoke:
		# Bespoke ember dashboard - decoupled from the 800x480 scaler. Only
		# profile_1.dash and the dash background change; profile_2 and
		# profile_1's home/menus/input stay the scaled 800x480-derived values.
		make_dash, background = bespoke[(width, height)]
		data['profile_1']['dash'] = make_dash()
		data['metadata']['dash_background'] = background

	return data


def dumps(data):
	return json.dumps(data, indent=2) + '\n'


def main():
	for width, height in RESOLUTIONS:
		path = out_path(width, height)
		with open(path, 'w') as f:
			f.write(dumps(build(width, height)))
		print(f'Wrote {path}')


if __name__ == '__main__':
	main()
