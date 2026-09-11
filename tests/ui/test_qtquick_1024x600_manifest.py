import json
import os


BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _manifest():
    with open(os.path.join(BASE, "wizard", "wizard_manifest.json")) as f:
        return json.load(f)


def test_accent_theme_option_present():
    opts = _manifest()["modules"]["display"]["qtquick_dsi_1024x600t"]["config"]
    accent = next(o for o in opts if o["option_name"] == "accent_theme")
    assert accent["default"] == "Ember"
    assert set(accent["list_values"]) == {"Ember", "Ice", "Crimson"}
