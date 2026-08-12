import json
import os

from tests.conftest import manifest_config_default

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _manifest():
    with open(os.path.join(BASE, "wizard", "wizard_manifest.json")) as f:
        return json.load(f)


def test_manifest_entry_present():
    entry = _manifest()["modules"]["display"]["qtquick_dsi_1280x720t"]
    assert entry["filename"] == "qtquick_dsi_1280x720t"
    assert manifest_config_default(entry, "display_data_filename") == "./display/qtquick_dsi_1280x720t.json"
    assert manifest_config_default(entry, "input_types_supported") == ["button", "touch"]
    assert "pyside6>=6.11.1" in entry["py_dependencies"]
    assert entry["config"] != []


def test_default_display_config_includes_entry():
    # _default_display_config reads ./wizard/wizard_manifest.json relative to CWD.
    cwd = os.getcwd()
    os.chdir(BASE)
    try:
        from common.defaults import _default_display_config

        config = _default_display_config()
    finally:
        os.chdir(cwd)
    assert "qtquick_dsi_1280x720t" in config
    assert config["qtquick_dsi_1280x720t"]["display_data_filename"] == "./display/qtquick_dsi_1280x720t.json"


def test_accent_theme_option_present():
    opts = _manifest()["modules"]["display"]["qtquick_dsi_1280x720t"]["config"]
    names = [o["option_name"] for o in opts]
    assert "accent_theme" in names
    accent = next(o for o in opts if o["option_name"] == "accent_theme")
    assert accent["default"] == "Ember"
    assert set(accent["list_values"]) == {"Ember", "Ice", "Crimson"}


# Self-discovering rather than one test per module: the Qt Quick display class
# is resolution-agnostic, so a new resolution is a manifest entry plus two
# small files, and the way that goes wrong is shipping the entry without them.
def _qtquick_modules():
    return sorted(k for k in _manifest()["modules"]["display"] if k.startswith("qtquick_"))


def test_every_qtquick_module_has_its_layout_and_module_file():
    modules = _qtquick_modules()
    # Guards the discovery itself: an empty list would make every assertion
    # below vacuous.
    assert modules == ["qtquick_dsi_1024x600t", "qtquick_dsi_1024x768t", "qtquick_dsi_1280x720t"]

    for name in modules:
        entry = _manifest()["modules"]["display"][name]
        assert entry["filename"] == name
        assert manifest_config_default(entry, "display_data_filename") == f"./display/{name}.json"
        assert "pyside6>=6.11.1" in entry["py_dependencies"]

        assert os.path.exists(os.path.join(BASE, "display", f"{name}.py")), name
        layout_path = os.path.join(BASE, "display", f"{name}.json")
        assert os.path.exists(layout_path), name

        with open(layout_path) as f:
            meta = json.load(f)["metadata"]
        assert meta["name"] == name
        # The resolution in the module name is what the wizard offers the
        # operator; the JSON is what the display actually sizes itself to.
        width, height = name.removeprefix("qtquick_dsi_").removesuffix("t").split("x")
        assert (meta["screen_width"], meta["screen_height"]) == (int(width), int(height)), name
