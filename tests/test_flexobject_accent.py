from display.flexobject import resolve_accent


def test_resolve_accent_ember_default():
	a = resolve_accent('Ember')
	assert a['accent'][:3] == (255, 138, 43)  # #ff8a2b
	assert resolve_accent('nonsense')['accent'] == a['accent']


def test_resolve_accent_ice_crimson():
	assert resolve_accent('Ice')['accent'][:3] == (60, 199, 208)  # #3cc7d0
	assert resolve_accent('Crimson')['accent'][:3] == (255, 106, 90)  # #ff6a5a
