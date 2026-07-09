from PIL import Image

from display.flexobject import ButtonRow, resolve_accent


def _obj(**kw):
	base = {
		'name': 'button_row',
		'type': 'button_row',
		'position': [0, 0],
		'size': [600, 82],
		'animation_enabled': False,
		'glow': False,
		'accent': resolve_accent('Ember'),
		'button_type': ['Set Temp', 'Hold', 'Stop', 'Shutdown'],
		'button_list': ['input_hold', 'input_hold', 'cmd_stop', 'cmd_shutdown'],
		'button_active': '',
		'button_value': [],
		'touch_areas': [],
	}
	base.update(kw)
	return base


def test_button_row_renders_to_size():
	obj = ButtonRow('button_row', _obj(), Image.new('RGBA', (1280, 720)))
	assert obj.get_object_canvas().size == (600, 82)


def test_button_row_touch_area_per_button():
	obj = ButtonRow('button_row', _obj(), Image.new('RGBA', (1280, 720)))
	assert len(obj.get_object_data()['touch_areas']) == 4


def test_button_row_two_buttons():
	obj = ButtonRow(
		'button_row',
		_obj(button_type=['Startup', 'Stop'], button_list=['cmd_startup', 'cmd_stop']),
		Image.new('RGBA', (1280, 720)),
	)
	assert len(obj.get_object_data()['touch_areas']) == 2
