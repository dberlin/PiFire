def test_module_reexports_display():
	import display.dsi_1024x600t as mod
	from display.dsi_800x480t import Display as BaseDisplay

	assert hasattr(mod, 'Display')
	assert mod.Display is BaseDisplay
