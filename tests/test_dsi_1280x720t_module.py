def test_module_reexports_display():
	import display.dsi_1280x720t as mod
	from display.dsi_800x480t import Display as BaseDisplay

	assert hasattr(mod, 'Display')
	# It is a re-export of the resolution-agnostic class, not a copy.
	assert mod.Display is BaseDisplay
