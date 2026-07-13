from PIL import Image
from display.flexobject import CookTimeBar, resolve_accent


def _obj(**kw):
	base = {
		'name': 'cook_time',
		'type': 'cook_time_bar',
		'position': [0, 0],
		'size': [490, 40],
		'animation_enabled': False,
		'glow': False,
		'accent': resolve_accent('Ember'),
		'data': {'label': 'COOK TIME', 'value': '1:30:15', 'highlight': False},
		'button_list': [],
		'button_value': [],
		'touch_areas': [],
	}
	base.update(kw)
	return base


def test_cook_time_bar_renders_to_size():
	obj = CookTimeBar('cook_time_bar', _obj(), Image.new('RGBA', (1024, 600)))
	assert obj.get_object_canvas().size == (490, 40)


def test_cook_time_bar_empty_value_still_renders():
	obj = CookTimeBar(
		'cook_time_bar',
		_obj(data={'label': 'COOK TIME', 'value': '', 'highlight': False}),
		Image.new('RGBA', (1024, 600)),
	)
	assert obj.get_object_canvas().size == (490, 40)
