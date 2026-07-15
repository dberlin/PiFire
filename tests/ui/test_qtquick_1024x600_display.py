import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_module_reexports_display():
    import display.qtquick_dsi_1024x600t as wrapper
    import display.qtquick_flex as mod

    assert wrapper.Display is mod.Display


def test_layout_json_metadata():
    with open(os.path.join(BASE, "display", "qtquick_dsi_1024x600t.json")) as f:
        meta = json.load(f)["metadata"]
    assert meta["name"] == "qtquick_dsi_1024x600t"
    assert meta["screen_width"] == 1024
    assert meta["screen_height"] == 600
    assert meta["default_profile"] == "profile_1"
