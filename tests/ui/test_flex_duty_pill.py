from PIL import Image
from display.flexobject import DutyPill, resolve_accent


def _obj(**kw):
	base = {
		'name': 'duty_pill_left',
		'type': 'duty_pill',
		'position': [0, 0],
		'size': [140, 64],
		'animation_enabled': False,
		'glow': False,
		'accent': resolve_accent('Ember'),
		'data': {'label': 'P-MODE', 'value': 'P-2', 'highlight': False},
		'button_list': [],
		'button_value': [],
		'touch_areas': [],
	}
	base.update(kw)
	return base


def test_duty_pill_renders_to_size():
	obj = DutyPill('duty_pill', _obj(), Image.new('RGBA', (1280, 720)))
	assert obj.get_object_canvas().size == (140, 64)


def test_duty_pill_highlighted():
	obj = DutyPill(
		'duty_pill', _obj(data={'label': 'SMOKE+', 'value': 'ON', 'highlight': True}), Image.new('RGBA', (1280, 720))
	)
	assert obj.get_object_canvas().size == (140, 64)
