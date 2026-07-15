import os

import pytest

from tests.conftest import REPO_BASE, load_wizard_manifest, manifest_config_default

RESOLUTIONS = ["dsi_1024x600t", "dsi_1024x768t", "dsi_1280x720t"]


@pytest.mark.parametrize("resolution", RESOLUTIONS)
def test_manifest_entry_present(resolution):
    entry = load_wizard_manifest()["modules"]["display"][resolution]
    assert entry["filename"] == resolution
    assert manifest_config_default(entry, "display_data_filename") == f"./display/{resolution}.json"
    assert entry["config"] != []


@pytest.mark.parametrize("resolution", RESOLUTIONS)
def test_default_display_config_includes_entry(resolution):
    # _default_display_config reads ./wizard/wizard_manifest.json relative to CWD.
    cwd = os.getcwd()
    os.chdir(REPO_BASE)
    try:
        from common.common import _default_display_config

        config = _default_display_config()
    finally:
        os.chdir(cwd)
    assert resolution in config
    assert config[resolution]["display_data_filename"] == f"./display/{resolution}.json"


@pytest.mark.parametrize("resolution", ["dsi_1024x600t", "dsi_1280x720t"])
def test_accent_theme_option_present(resolution):
    # Note: dsi_1024x768t deliberately has no accent_theme option in the
    # manifest, so it is excluded from this parametrization.
    opts = load_wizard_manifest()["modules"]["display"][resolution]["config"]
    names = [o["option_name"] for o in opts]
    assert "accent_theme" in names
    accent = next(o for o in opts if o["option_name"] == "accent_theme")
    assert accent["default"] == "Ember"
    assert set(accent["list_values"]) == {"Ember", "Ice", "Crimson"}


def test_dsi_1024x768t_has_no_accent_theme_option():
    """dsi_1024x768t's manifest entry does not include the accent_theme option
    present on the other two DSI resolutions."""
    opts = load_wizard_manifest()["modules"]["display"]["dsi_1024x768t"]["config"]
    names = [o["option_name"] for o in opts]
    assert "accent_theme" not in names
