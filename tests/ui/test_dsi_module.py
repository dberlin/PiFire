import importlib

import pytest


# The larger DSI resolutions moved to the Qt Quick display; these two are the
# pygame engine's remaining stubs.
@pytest.mark.parametrize("module_name", ["dsi_800x480t", "dsi_320x240t"])
def test_module_reexports_display(module_name):
    mod = importlib.import_module(f"display.{module_name}")
    from display._base_dsi import Display as BaseDisplay

    assert hasattr(mod, "Display")
    # It is a re-export of the resolution-agnostic class, not a copy.
    assert mod.Display is BaseDisplay
