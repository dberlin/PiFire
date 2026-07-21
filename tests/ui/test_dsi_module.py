import importlib

import pytest


@pytest.mark.parametrize("module_name", ["dsi_1024x600t", "dsi_1024x768t", "dsi_1280x720t"])
def test_module_reexports_display(module_name):
    mod = importlib.import_module(f"display.{module_name}")
    from display.dsi_base import Display as BaseDisplay

    assert hasattr(mod, "Display")
    # It is a re-export of the resolution-agnostic class, not a copy.
    assert mod.Display is BaseDisplay
