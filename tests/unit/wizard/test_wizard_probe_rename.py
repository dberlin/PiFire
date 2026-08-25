"""run_wizard has the same stale-probe-label hole the live /api/probe_map path
had, and ruling 6 (2026-07-26, docs/superpowers/backlogs/react-migration-backlog.md)
closes both.

wizard.py wrote settings["probe_settings"]["probe_map"] and regenerated only
settings["history_page"]["probe_config"]. Re-running the wizard with a probe
renamed therefore left settings["recipe"]["probe_map"] and
control["notify_data"] naming the old label -- and because this is the
INSTALLER, that stale state is what a user lands on after the reboot the
wizard finishes with. It also made the live path's identical hole look
deliberate ("match wizard.py exactly"), which is why it is fixed here too.

SAFETY: wizard.py's only destructive calls are subprocess.Popen (three sites,
all behind `is_real_hardware()`) and one subprocess.run. No os.system, no
reboot/shutdown -- run_wizard signals a reboot by writing percent 142 and
returns; the caller in `__main__` is what acts on it, and it is never reached
here. The `no_install` fixture below patches `is_real_hardware` to False and
`subprocess.run` to a stub, the same neutralization
tests/unit/wizard/test_wizard_run_no_probes.py uses.
"""

import logging

import pytest

import wizard
from common import defaults
from common.common import read_wizard
from common.persistence import control as control_persistence
from common.persistence import runtime as runtime_persistence


@pytest.fixture
def no_install(monkeypatch):
    """Neutralize the real dependency-install side effects so run_wizard only
    exercises the settings-writing logic under test."""
    monkeypatch.setattr(wizard, "logger", logging.getLogger("wizard_rename_test"), raising=False)
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: False)
    monkeypatch.setattr(wizard.time, "sleep", lambda *a, **k: None)

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: _Result())


def _seeded_settings():
    """A known two-probe map with every derived structure consistent with it."""
    settings = defaults.default_settings()
    settings["probe_settings"]["probe_map"]["probe_info"] = [
        probe
        for probe in settings["probe_settings"]["probe_map"]["probe_info"]
        if probe["label"] in ("Grill", "Probe1")
    ]
    settings["history_page"]["probe_config"] = defaults.default_probe_config(settings)
    settings["recipe"]["probe_map"] = {"primary": "Grill", "food": ["Probe1"]}
    runtime_persistence.write_settings_store(settings)

    control = defaults.default_control()
    control["notify_data"] = defaults.default_notify(settings)
    control_persistence.write_control_snapshot(control, origin="test")
    return settings


def _run_with_renamed_probe(settings):
    wizard_data = read_wizard()
    install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)
    for probe in install_info["probe_map"]["probe_info"]:
        if probe["label"] == "Probe1":
            probe["label"] = "Brisket"
            probe["name"] = "Brisket"
    wizard.run_wizard(settings, wizard_data, install_info)


def test_installer_rename_updates_the_recipe_probe_map(ds, no_install):
    settings = _seeded_settings()

    _run_with_renamed_probe(settings)

    assert runtime_persistence.read_settings()["recipe"]["probe_map"] == {
        "primary": "Grill",
        "food": ["Brisket"],
    }


def test_installer_rename_leaves_no_stale_notify_entry(ds, no_install):
    settings = _seeded_settings()

    _run_with_renamed_probe(settings)

    control_persistence.execute_control_writes()
    notify_data = control_persistence.read_control()["notify_data"]
    probe_labels = {e["label"] for e in notify_data if e["type"].startswith("probe")}
    assert probe_labels == {"Grill", "Brisket"}


def test_installer_still_regenerates_the_history_probe_config(ds, no_install):
    """The one derived structure the installer already rebuilt, kept pinned so
    the shared helper cannot quietly drop it."""
    settings = _seeded_settings()

    _run_with_renamed_probe(settings)

    assert set(runtime_persistence.read_settings()["history_page"]["probe_config"]) == {"Grill", "Brisket"}
