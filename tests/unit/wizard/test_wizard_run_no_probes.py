"""Regression test: run_wizard must not crash when no probe devices are
configured.

The settings-writing loop in run_wizard indexed `profile_selected[0]` for every
module, but the probes module's `profile_selected` list is populated only from
`probe_map['probe_devices']`. With no probe devices configured that list is
empty, while the probes module still carries a `units` setting -- so the loop
reached the probes module and raised IndexError. Because the wizard runs as a
detached subprocess, the uncaught exception silently froze `wizard:output` at
the last line written (the display module's `buttonslevel`), which the browser
polled forever, presenting as a hang partway through the install.
"""

import logging

import pytest

import wizard
from common import defaults
from common.persistence import runtime as runtime_persistence
from common.persistence import install_state as install_persistence
from common.common import read_wizard


@pytest.fixture
def no_install(monkeypatch):
    """Neutralize the real dependency-install side effects so run_wizard only
    exercises the settings-writing logic under test."""
    monkeypatch.setattr(wizard, "logger", logging.getLogger("wizard_test"), raising=False)
    monkeypatch.setattr(wizard, "is_real_hardware", lambda *a, **k: False)
    monkeypatch.setattr(wizard.time, "sleep", lambda *a, **k: None)

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(wizard.subprocess, "run", lambda *a, **k: _Result())


def test_run_wizard_no_probe_devices(ds, no_install):
    settings = defaults.default_settings()
    settings["probe_settings"]["probe_map"]["probe_devices"] = []
    runtime_persistence.write_settings_store(settings)

    wizard_data = read_wizard()
    install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)

    # No probe devices -> probes profile_selected is empty, but it still carries
    # a units setting. This must not raise.
    assert install_info["modules"]["probes"]["profile_selected"] == []
    assert "units" in install_info["modules"]["probes"]["settings"]

    wizard.run_wizard(settings, wizard_data, install_info)


def test_run_wizard_dev_mode_resolves_to_restart_not_reboot(ds, no_install):
    settings = defaults.default_settings()
    settings["probe_settings"]["probe_map"]["probe_devices"] = []
    runtime_persistence.write_settings_store(settings)

    wizard_data = read_wizard()
    install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)

    wizard.run_wizard(settings, wizard_data, install_info)

    percent, status, output = install_persistence.get_wizard_install_status()
    assert percent == 101


def test_run_wizard_skips_unknown_setting_key(ds, no_install):
    """A dependency key that isn't in the selected module's manifest entry must
    not crash the detached installer.

    Reachable from the React wizard: 12 of 30 display modules carry a
    `buttonslevel` dependency. Switching to one of the other 18 used to leave
    that stale key in the display section's settings, which reached this loop
    and raised KeyError inside the detached process -- silently freezing the
    install at its last status line while the browser polled forever.
    """
    settings = defaults.default_settings()
    settings["probe_settings"]["probe_map"]["probe_devices"] = []
    runtime_persistence.write_settings_store(settings)

    wizard_data = read_wizard()
    install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)
    # A setting name that exists in no module's settings_dependencies.
    install_info["modules"]["display"]["settings"]["totally_bogus_setting"] = "x"

    # Must not raise.
    wizard.run_wizard(settings, wizard_data, install_info)

    percent, status, output = install_persistence.get_wizard_install_status()
    assert percent == 101  # ran to completion (restart-only), not frozen mid-install


def test_run_wizard_skips_unknown_module_name(ds, no_install):
    """A previous fix guarded an unknown dependency KEY within a known
    module. The selected MODULE name itself is also indexed unguarded in the
    detached installer -- a stale draft naming a module that a later
    manifest upgrade removed raises KeyError and silently freezes the
    install, the same failure class as the unknown-setting-key bug above.
    """
    settings = defaults.default_settings()
    settings["probe_settings"]["probe_map"]["probe_devices"] = []
    runtime_persistence.write_settings_store(settings)

    wizard_data = read_wizard()
    install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)
    # A module name that exists in no section of the manifest.
    install_info["modules"]["display"]["profile_selected"] = ["totally_bogus_display_module"]

    # Must not raise.
    wizard.run_wizard(settings, wizard_data, install_info)

    percent, status, output = install_persistence.get_wizard_install_status()
    assert percent == 101  # ran to completion (restart-only), not frozen mid-install
