from PIL import Image

from display.flexobject import HopperVertical, resolve_accent


def _obj(**kw):
	base = {
		'name': 'hopper_vertical',
		'type': 'hopper_vertical',
		'position': [0, 0],
		'size': [300, 300],
		'animation_enabled': False,
		'glow': False,
		'accent': resolve_accent('Ember'),
		'data': {'level': 74, 'enabled': True},
		'button_list': ['cmd_hopper_level'],
		'button_value': [],
		'touch_areas': [],
	}
	base.update(kw)
	return base


def test_hopper_vertical_renders_to_size():
	obj = HopperVertical('hopper_vertical', _obj(), Image.new('RGBA', (1280, 720)))
	assert obj.get_object_canvas().size == (300, 300)


def test_hopper_vertical_low_level():
	obj = HopperVertical('hopper_vertical', _obj(data={'level': 8, 'enabled': True}), Image.new('RGBA', (1280, 720)))
	assert obj.get_object_canvas().size == (300, 300)
