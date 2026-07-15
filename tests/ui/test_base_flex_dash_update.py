"""
Tests for Task 24: wiring live data + accent into the new "ember" pygame
flexobjects (probe_card, gauge_ember, system_card, duty_pill, hopper_vertical,
header_bar, button_row) in display/base_flex.py.

The bespoke 1280x720 ember layout JSON does not exist yet (Task 25), so this
builds a minimal in-memory layout containing just the new-type objects (using
the placeholder names Task 25 is expected to use) and drives a real
DisplayBase subclass through _configure_dash/_build_objects/_build_dash_map/
_update_dash_objects. The pure per-object computations are also unit tested
directly as staticmethods, independent of the DisplayBase plumbing.
"""

import json
import os

from display.base_flex import DisplayBase, NEW_EMBER_FLEX_TYPES
from display.flexobject import resolve_accent

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _flex_base(name, obj_type, position=(0, 0), size=(100, 100), **extra):
	obj = {
		'name': name,
		'type': obj_type,
		'position': list(position),
		'size': list(size),
		'animation_enabled': False,
		'glow': False,
		'data': {},
	}
	obj.update(extra)
	return obj


def _build_layout_dict():
	dash = [
		_flex_base(
			'primary_gauge',
			'gauge_ember',
			temps=[0, 0, 0],
			max_temp=600,
			units='F',
			label='Grill',
			button_list=[],
			button_value=[],
		),
		_flex_base('probe_card_0', 'probe_card', units='F'),
		_flex_base('probe_card_1', 'probe_card', units='F'),
		_flex_base('system_card', 'system_card', button_list=['fan_toggle', 'auger_toggle', 'igniter_toggle']),
		_flex_base('duty_pill_left', 'duty_pill'),
		_flex_base('duty_pill_right', 'duty_pill'),
		_flex_base('hopper_vertical', 'hopper_vertical'),
		_flex_base('header_bar', 'header_bar'),
		_flex_base(
			'button_row',
			'button_row',
			button_type=['Prime', 'Startup', 'Monitor', 'Stop'],
			button_list=['menu_prime', 'menu_startup', 'cmd_monitor', 'cmd_stop'],
			button_active='',
			button_value=[],
		),
		_flex_base(
			'cook_time',
			'timer',
			data={'seconds': 0},
			label='COOK TIME',
			fg_color=(255, 255, 255, 255),
			bg_color=(0, 0, 0, 255),
		),
		_flex_base(
			'lid_alert',
			'alert',
			data={'text': ['Lid Open']},
			active=False,
			fg_color=(255, 255, 255, 255),
			bg_color=(0, 0, 0, 255),
		),
	]
	return {
		'metadata': {
			'name': 'test_ember_layout',
			'screen_width': 1280,
			'screen_height': 720,
			'splash_delay': 10,
			'framerate': 30,
			'max_food_probes': 5,
			'dash_background': './static/img/display/background_ember_1280x720.png',
			'splash_image': './static/img/display/splash_800x480.png',
		},
		'profile_1': {'home': [], 'dash': dash, 'menus': {'qrcode': {}}, 'input': {}},
	}


def _config(layout_path):
	return {
		'display_data_filename': layout_path,
		'default_profile': 'profile_1',
		'input_types_supported': [],
		'buttonslevel': 'HIGH',
		'accent_theme': 'Ember',
		'probe_info': {
			'primary': {'name': 'Grill'},
			'food': [{'label': 'Probe1', 'name': 'Chicken'}, {'label': 'Probe2', 'name': 'Turkey'}],
		},
	}


class _DummyDisplay(DisplayBase):
	"""Minimal DisplayBase subclass - no pygame, mirrors how dsi_800x480t.Display
	sets display_profile before calling super().__init__()."""

	def __init__(self, config):
		self.display_profile = 'profile_1'
		super().__init__(dev_pins={}, config=config)


def _status_data(**overrides):
	base = {
		'mode': 'Hold',
		'units': 'F',
		'recipe': False,
		'recipe_paused': False,
		'lid_open_detected': False,
		'lid_open_endtime': 0,
		'p_mode': 0,
		's_plus': False,
		'hopper_level': 55,
		'hopper_level_enabled': True,
		'outpins': {'fan': True, 'auger': True, 'igniter': False},
		'cycle_ratio': 0.4,
		'fan_duty': 100,
		'start_time': 0,
		'start_duration': 0,
		'prime_duration': 0,
		'shutdown_duration': 0,
		'startup_timestamp': 0,
	}
	base.update(overrides)
	return base


def _in_data(**overrides):
	base = {
		'P': {'Grill': 225},
		'F': {'Probe1': 120, 'Probe2': 80},
		'NT': {'Grill': 225, 'Probe1': 165, 'Probe2': 0},
		'AUX': {},
		'PSP': 225,
	}
	base.update(overrides)
	return base


def _make_display(tmp_path, status_data=None):
	layout_path = os.path.join(tmp_path, 'ember_test_layout.json')
	with open(layout_path, 'w') as f:
		json.dump(_build_layout_dict(), f)

	display = _DummyDisplay(_config(layout_path))
	display._configure_dash()
	display.status_data = status_data if status_data is not None else _status_data()
	# Mirrors production: by the time the dash is showing, at least one prior
	# frame has run, so last_status_data['lid_open_detected'] is always present
	# (the legacy "In Hold Mode, Check Lid Indicator" branch reads it directly).
	display.last_status_data = {'lid_open_detected': False}
	display.in_data = None
	display.last_in_data = {}
	display.display_active = 'dash'
	display._build_objects(None)
	display._build_dash_map()
	return display


def _obj_data(display, name):
	return display.display_object_list[display.dash_map[name]].get_object_data()


# ---------------------------------------------------------------------------
# Accent injection (Part A)
# ---------------------------------------------------------------------------


def test_accent_resolved_at_init(tmp_path):
	display = _make_display(tmp_path)
	assert display.accent == resolve_accent('Ember')


def test_accent_injected_into_new_type_objects(tmp_path):
	display = _make_display(tmp_path)
	for name in ('header_bar', 'duty_pill_left', 'system_card', 'hopper_vertical', 'primary_gauge', 'button_row'):
		assert _obj_data(display, name)['accent'] == resolve_accent('Ember')


def test_accent_not_injected_into_non_ember_types():
	assert 'timer' not in NEW_EMBER_FLEX_TYPES
	assert 'alert' not in NEW_EMBER_FLEX_TYPES


# ---------------------------------------------------------------------------
# probe_card_N mapping (Part B)
# ---------------------------------------------------------------------------


def test_configure_dash_builds_probe_card_maps(tmp_path):
	display = _make_display(tmp_path)
	assert display.probe_card_label_map == {'probe_card_0': 'Probe1', 'probe_card_1': 'Probe2'}
	assert display.probe_card_name_map == {'probe_card_0': 'Chicken', 'probe_card_1': 'Turkey'}


# ---------------------------------------------------------------------------
# _update_dash_objects wiring (Part C) - live integration through a real
# DisplayBase subclass, using the placeholder ember layout above.
# ---------------------------------------------------------------------------


def test_update_dash_hold_mode_duty_pills_and_system_card(tmp_path):
	display = _make_display(tmp_path)
	display.in_data = _in_data()
	display._update_dash_objects()

	assert _obj_data(display, 'duty_pill_left')['data'] == {'label': 'AUGER DUTY', 'value': '40%', 'highlight': False}
	assert _obj_data(display, 'duty_pill_right')['data'] == {'label': 'FAN DUTY', 'value': '100%', 'highlight': True}
	assert _obj_data(display, 'system_card')['data'] == {'fan': True, 'auger': True, 'igniter': False}
	assert _obj_data(display, 'hopper_vertical')['data'] == {'level': 55, 'enabled': True}

	button_row = _obj_data(display, 'button_row')
	assert button_row['button_type'] == ['Set Temp', 'Smoke', 'Stop', 'Shutdown']
	assert button_row['button_list'] == ['input_hold', 'cmd_smoke', 'cmd_stop', 'cmd_shutdown']

	header = _obj_data(display, 'header_bar')
	assert header['data']['ip'] == display.ip_address
	assert len(header['data']['clock']) == 5  # 'HH:MM'
	assert header['data']['cooking'] is True  # Hold is a "cooking" mode

	assert _obj_data(display, 'lid_alert')['active'] is False


def test_update_dash_smoke_mode_duty_pills_and_probe_cards(tmp_path):
	display = _make_display(tmp_path)
	display.in_data = _in_data()
	display._update_dash_objects()  # seed last_in_data / last_status_data

	display.status_data = _status_data(
		mode='Smoke', p_mode=3, s_plus=True, outpins={'fan': False, 'auger': True, 'igniter': True}
	)
	display.in_data = _in_data(F={'Probe1': 130, 'Probe2': 80})
	display._update_dash_objects()

	assert _obj_data(display, 'duty_pill_left')['data'] == {'label': 'P-MODE', 'value': 'P-3', 'highlight': False}
	assert _obj_data(display, 'duty_pill_right')['data'] == {'label': 'SMOKE+', 'value': 'ON', 'highlight': True}

	button_row = _obj_data(display, 'button_row')
	assert button_row['button_type'] == ['Set Temp', 'Hold', 'Stop', 'Shutdown']
	assert button_row['button_list'] == ['input_hold', 'input_hold', 'cmd_stop', 'cmd_shutdown']

	probe_0 = _obj_data(display, 'probe_card_0')
	assert probe_0['data']['name'] == 'Chicken'
	assert probe_0['data']['temp'] == 130
	assert probe_0['data']['target'] == 165
	assert probe_0['units'] == 'F'


def test_update_dash_lid_alert_tracks_lid_open_detected(tmp_path):
	display = _make_display(tmp_path)
	display.in_data = _in_data()
	display._update_dash_objects()
	assert _obj_data(display, 'lid_alert')['active'] is False

	display.status_data = _status_data(lid_open_detected=True)
	display._update_dash_objects()
	assert _obj_data(display, 'lid_alert')['active'] is True


def test_update_dash_cook_time_elapsed_when_no_active_timer(tmp_path):
	import time

	now = time.time()
	display = _make_display(tmp_path, status_data=_status_data(mode='Hold', startup_timestamp=now - 125))
	display.in_data = _in_data()
	display._update_dash_objects()

	cook_time = _obj_data(display, 'cook_time')
	assert cook_time['data']['label'] == 'COOK TIME'
	assert cook_time['data']['value'] == '02:05'


def test_update_dash_cook_time_zero_when_stopped(tmp_path):
	import time

	display = _make_display(tmp_path, status_data=_status_data(mode='Stop', startup_timestamp=time.time() - 500))
	display.in_data = _in_data()
	display._update_dash_objects()

	assert _obj_data(display, 'cook_time')['data']['value'] == '00:00'


def test_hopper_vertical_hidden_when_disabled(tmp_path):
	display = _make_display(tmp_path, status_data=_status_data(hopper_level_enabled=False))
	assert 'hopper_vertical' not in display.dash_map


# ---------------------------------------------------------------------------
# Pure helper unit tests (fast, no DisplayBase construction needed)
# ---------------------------------------------------------------------------


def test_button_row_for_mode_smoke():
	button_type, button_list, _ = DisplayBase._button_row_for_mode('Smoke', False, False)
	assert button_type == ['Set Temp', 'Hold', 'Stop', 'Shutdown']
	assert button_list == ['input_hold', 'input_hold', 'cmd_stop', 'cmd_shutdown']


def test_button_row_for_mode_hold():
	button_type, button_list, _ = DisplayBase._button_row_for_mode('Hold', False, False)
	assert button_type == ['Set Temp', 'Smoke', 'Stop', 'Shutdown']
	assert button_list == ['input_hold', 'cmd_smoke', 'cmd_stop', 'cmd_shutdown']


def test_button_row_for_mode_startup_reignite():
	for mode in ('Startup', 'Reignite'):
		button_type, button_list, _ = DisplayBase._button_row_for_mode(mode, False, False)
		assert button_type == ['Startup', 'Smoke', 'Hold', 'Stop']
		assert button_list == ['cmd_startup', 'cmd_smoke', 'input_hold', 'cmd_stop']


def test_button_row_for_mode_shutdown():
	button_type, button_list, _ = DisplayBase._button_row_for_mode('Shutdown', False, False)
	assert button_type == ['Smoke', 'Hold', 'Stop', 'Shutdown']
	assert button_list == ['cmd_smoke', 'input_hold', 'cmd_stop', 'cmd_shutdown']


def test_button_row_for_mode_stop_prime_monitor_default():
	for mode in ('Stop', 'Prime', 'Monitor'):
		button_type, button_list, _ = DisplayBase._button_row_for_mode(mode, False, False)
		assert button_type == ['Prime', 'Startup', 'Monitor', 'Stop']
		assert button_list == ['menu_prime', 'menu_startup', 'cmd_monitor', 'cmd_stop']


def test_button_row_for_mode_recipe_active():
	button_type, button_list, button_active = DisplayBase._button_row_for_mode('Smoke', True, False)
	assert button_type == ['Next', 'Smoke', 'Stop', 'Shutdown']
	assert button_list == ['cmd_next_step', 'cmd_none', 'cmd_stop', 'cmd_shutdown']
	assert button_active == 'Smoke'


def test_button_row_for_mode_recipe_paused_highlights_next():
	_, _, button_active = DisplayBase._button_row_for_mode('Hold', True, True)
	assert button_active == 'Next'


def test_button_row_for_mode_recipe_ignored_in_shutdown():
	button_type, button_list, _ = DisplayBase._button_row_for_mode('Shutdown', True, False)
	assert button_type == ['Smoke', 'Hold', 'Stop', 'Shutdown']


def test_duty_pills_hold_mode():
	left, right = DisplayBase._duty_pills(
		{'mode': 'Hold', 'cycle_ratio': 0.4, 'fan_duty': 100, 'outpins': {'fan': True}}
	)
	assert left == {'label': 'AUGER DUTY', 'value': '40%', 'highlight': False}
	assert right == {'label': 'FAN DUTY', 'value': '100%', 'highlight': True}


def test_duty_pills_non_hold_mode():
	left, right = DisplayBase._duty_pills({'mode': 'Smoke', 'p_mode': 5, 's_plus': False, 'outpins': {}})
	assert left == {'label': 'P-MODE', 'value': 'P-5', 'highlight': False}
	assert right == {'label': 'SMOKE+', 'value': 'OFF', 'highlight': False}


def test_duty_pills_smoke_plus_on_highlights():
	_, right = DisplayBase._duty_pills({'mode': 'Smoke', 'p_mode': 0, 's_plus': True, 'outpins': {}})
	assert right == {'label': 'SMOKE+', 'value': 'ON', 'highlight': True}


def test_cook_time_data_active_countdown_timer():
	now = 1000.0
	status = {'mode': 'Startup', 'start_time': now - 5, 'start_duration': 10}
	data = DisplayBase._cook_time_data(status, now)
	assert data == {'label': 'Timer', 'value': '00:05'}


def test_cook_time_data_lid_pause_countdown():
	now = 1000.0
	status = {'mode': 'Hold', 'lid_open_detected': True, 'lid_open_endtime': now + 30}
	data = DisplayBase._cook_time_data(status, now)
	assert data == {'label': 'Lid Pause', 'value': '00:30'}


def test_cook_time_data_elapsed_cook_time():
	now = 1000.0
	status = {'mode': 'Hold', 'startup_timestamp': now - 125}
	data = DisplayBase._cook_time_data(status, now)
	assert data == {'label': 'COOK TIME', 'value': '02:05'}


def test_cook_time_data_elapsed_with_hours():
	now = 5000.0
	status = {'mode': 'Hold', 'startup_timestamp': now - 3665}  # 1h 1m 5s
	data = DisplayBase._cook_time_data(status, now)
	assert data == {'label': 'COOK TIME', 'value': '1:01:05'}


def test_cook_time_data_zero_when_stopped():
	now = 1000.0
	assert DisplayBase._cook_time_data({'mode': 'Stop', 'startup_timestamp': now - 500}, now) == {
		'label': 'COOK TIME',
		'value': '00:00',
	}


def test_cook_time_data_zero_when_monitor():
	now = 1000.0
	assert DisplayBase._cook_time_data({'mode': 'Monitor', 'startup_timestamp': now - 500}, now) == {
		'label': 'COOK TIME',
		'value': '00:00',
	}


def test_cook_time_data_zero_when_no_timestamp():
	now = 1000.0
	assert DisplayBase._cook_time_data({'mode': 'Hold', 'startup_timestamp': 0}, now) == {
		'label': 'COOK TIME',
		'value': '00:00',
	}
