"""A tree that has not been migrated must not crash the installer.

set_nested_key_value raises when the final key is absent, so writing
platform.devices.distance.i2c_bus (and platform.fan_controller.i2c_bus) into a
tree that still holds i2c_bus_kind/i2c_bus_num used to fail the whole install.
Running the settings migration cascade against the SQLite-stored tree before
the installer runs is what fixes it.
"""

import copy
import logging
import os

import pytest

import wizard
from common import datastore, defaults
from common.common import read_wizard
from common.persistence import runtime as runtime_persistence


@pytest.fixture
def no_install(monkeypatch):
    """Neutralize the real dependency-install side effects so run_wizard only
    exercises the settings-writing logic under test, and prove the real
    installer (os.system/subprocess.Popen) is never reached."""
    monkeypatch.setattr(wizard, "logger", logging.getLogger("wizard_legacy_test"), raising=False)
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: False)
    monkeypatch.setattr(wizard.time, "sleep", lambda *a, **k: None)

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(
        wizard.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("a real subprocess escaped the test harness"),
    )
    system_calls = []
    monkeypatch.setattr(os, "system", lambda cmd: system_calls.append(cmd))
    return system_calls


def _legacy_settings():
    """A settings tree as an install predating the i2c_bus composite holds it."""
    settings = copy.deepcopy(defaults.default_settings())
    settings["versions"] = {"server": "1.10.10", "cookfile": "1.5.0", "recipe": "1.0.0", "build": 70}
    distance = settings["platform"]["devices"]["distance"]
    distance.pop("i2c_bus", None)
    distance["i2c_bus_kind"] = "extended"
    distance["i2c_bus_num"] = "CP2112"
    fan = settings["platform"]["fan_controller"]
    fan.pop("i2c_bus", None)
    fan["i2c_bus_kind"] = "basic"
    fan["i2c_bus_num"] = "1"
    return settings


def _x86_numato_install_info():
    """A wizardInstallInfo selecting x86_numato, driving both i2c_bus
    dependencies (distance sensor and fan controller) it declares."""
    return {
        "modules": {
            "grillplatform": {
                "profile_selected": ["x86_numato"],
                "settings": {
                    "current": "x86_numato",
                    "system_type": "x86_numato",
                    "device_distance_i2c_bus": {"kind": "kernel", "adapter": "CP2112"},
                    "i2c_bus": {"kind": "basic"},
                },
                "config": {},
            },
            "display": {"profile_selected": ["none"], "settings": {}, "config": {}},
            "distance": {"profile_selected": ["none"], "settings": {}, "config": {}},
            "probes": {"profile_selected": [], "settings": {}, "config": {}},
        },
        "probe_map": defaults.default_settings()["probe_settings"]["probe_map"],
    }


def test_the_installer_writes_the_composite_into_a_legacy_tree(ds, no_install):
    """Build a legacy-shaped tree, run the migration, then drive the
    installer's per-dependency write for the i2c_bus dependency and assert it
    lands."""
    runtime_persistence.write_settings_store(_legacy_settings())
    wizard_data = read_wizard()
    info = _x86_numato_install_info()

    # Proves the regression: driving the installer against the unmigrated
    # tree still crashes writing the composite key.
    with pytest.raises(KeyError):
        wizard.run_wizard(runtime_persistence.read_settings(), wizard_data, info)

    datastore._upgrade_settings_in_store()

    # Migrated: the very same write now lands instead of crashing.
    wizard.run_wizard(runtime_persistence.read_settings(), wizard_data, info)

    stored = runtime_persistence.read_settings()
    assert stored["platform"]["devices"]["distance"]["i2c_bus"] == {"kind": "kernel", "adapter": "CP2112"}
    assert stored["platform"]["fan_controller"]["i2c_bus"] == {"kind": "basic"}
    assert not no_install  # os.system was never called
