import os
from tools.generate_dsi_layout import build, dumps, RESOLUTIONS

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _committed(width, height):
	path = os.path.join(BASE, 'display', f'dsi_{width}x{height}t.json')
	with open(path) as f:
		return f.read()


def test_reproduces_committed_1024x768_byte_for_byte():
	assert dumps(build(1024, 768)) == _committed(1024, 768)


def test_reproduces_committed_1280x720_byte_for_byte():
	assert dumps(build(1280, 720)) == _committed(1280, 720)


def test_registered_resolutions():
	assert (1024, 768) in RESOLUTIONS
	assert (1280, 720) in RESOLUTIONS


def test_reproduces_committed_1024x600_byte_for_byte():
	assert dumps(build(1024, 600)) == _committed(1024, 600)


def test_registered_resolutions_includes_1024x600():
	assert (1024, 600) in RESOLUTIONS
