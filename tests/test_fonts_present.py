import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FONTS = [
	'Barlow-Regular.ttf',
	'Barlow-Medium.ttf',
	'Barlow-SemiBold.ttf',
	'Barlow-Bold.ttf',
	'BarlowSemiCondensed-Medium.ttf',
	'BarlowSemiCondensed-SemiBold.ttf',
	'BarlowSemiCondensed-Bold.ttf',
	'BarlowSemiCondensed-ExtraBold.ttf',
]


def test_barlow_fonts_bundled():
	for name in FONTS:
		p = os.path.join(BASE, 'static', 'font', name)
		assert os.path.exists(p), f'missing {name}'
		assert os.path.getsize(p) > 1000
