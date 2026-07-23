from blueprints.wizard.wizard import wizardInstallInfoDefaults

_WIZARD_DATA = {
    "modules": {
        "grillplatform": {
            "x86": {
                "default": True,
                "settings_dependencies": {
                    "i2c_bus_kind": {
                        "options": {"basic": "Basic", "extended": "Extended"},
                        "settings": ["platform", "fan_controller", "i2c_bus_kind"],
                    },
                    "i2c_bus_num": {
                        "type": "i2c_bus_num",
                        "default": "CP2112",
                        "settings": ["platform", "fan_controller", "i2c_bus_num"],
                    },
                },
            }
        },
        "display": {"none": {"default": True, "settings_dependencies": {}}},
        "distance": {"none": {"default": True, "settings_dependencies": {}}},
    },
    "boards": {"x86": {"probe_map": {"probe_devices": []}}},
}


def test_wizard_install_info_defaults_handles_options_free_field():
    settings = {"display": {"config": {"none": {}}}}
    info = wizardInstallInfoDefaults(_WIZARD_DATA, settings)
    # 'options'-based dependency still seeds its first key.
    assert info["modules"]["grillplatform"]["settings"]["i2c_bus_kind"] == "basic"
    # 'type: i2c_bus_num' dependency (no 'options') seeds its explicit 'default'.
    assert info["modules"]["grillplatform"]["settings"]["i2c_bus_num"] == "CP2112"


def test_display_has_single_default_module():
    """Regression: the manifest must mark exactly one display module default
    (was two — ili9341b and ili9488b — causing dropdown/config disagreement)."""
    from common.common import read_wizard

    display = read_wizard()["modules"]["display"]
    defaults = [name for name, entry in display.items() if isinstance(entry, dict) and entry.get("default") is True]
    assert defaults == ["ili9341b"], f"expected single default ili9341b, got {defaults}"


def test_every_display_module_has_default_key():
    """wizardInstallInfoDefaults reads module['default'] UNGUARDED
    (blueprints/wizard/wizard.py:53), so EVERY display module must carry the
    key (true or false) — dropping it (not setting false) crashes fresh setup.
    Regression for the KeyError introduced by removing ili9488b's key."""
    from common.common import read_wizard

    display = read_wizard()["modules"]["display"]
    missing = [name for name, entry in display.items() if isinstance(entry, dict) and "default" not in entry]
    assert missing == [], f"display modules missing 'default' key: {missing}"


def test_real_manifest_defaults_do_not_crash_on_fresh_setup():
    """End-to-end guard against the synthetic-fixture blind spot: run the REAL
    manifest through wizardInstallInfoDefaults on a first-time-setup and assert
    it neither crashes nor mis-resolves the display default."""
    from common.common import read_wizard
    from common.defaults import default_settings

    wizard_data = read_wizard()
    settings = default_settings()
    settings["globals"]["first_time_setup"] = True
    info = wizardInstallInfoDefaults(wizard_data, settings)  # must not KeyError
    assert info["modules"]["display"]["profile_selected"] == ["ili9341b"]


def test_real_manifest_existing_stale_module_yields_empty_list_not_string():
    """profile_selected is ALWAYS a list, everywhere. wizardInstallInfoExisting()'s
    stale/invalid-module recovery path (a settings-referenced module no longer in
    the wizard manifest) used to overwrite profile_selected with a BARE STRING
    (e.g. "none"/"custom") instead of the usual single-item list -- a mixed-type
    footgun that made a naive `profile_selected[0]` silently return one CHARACTER
    of the string instead of a module name. Drive the real stale-module path
    (settings["modules"]["dist"] names a module absent from the real manifest)
    and assert the recovered profile_selected is an EMPTY LIST (no selection),
    not a string."""
    from blueprints.wizard.wizard import wizardInstallInfoExisting
    from common.common import read_wizard
    from common.defaults import default_settings

    wizard_data = read_wizard()
    settings = default_settings()
    settings["globals"]["first_time_setup"] = False
    settings["modules"]["dist"] = "totally_bogus_distance_module"

    info = wizardInstallInfoExisting(wizard_data, settings)

    assert info["modules"]["distance"]["profile_selected"] == []
    assert isinstance(info["modules"]["distance"]["profile_selected"], list)
