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
from common import common as c
from common import datastore


@pytest.fixture
def ds(tmp_path):
	datastore._reset_for_tests(str(tmp_path / 't.db'))
	datastore.init()
	yield datastore
	datastore._reset_for_tests(None)


@pytest.fixture
def no_install(monkeypatch):
	"""Neutralize the real dependency-install side effects so run_wizard only
	exercises the settings-writing logic under test."""
	monkeypatch.setattr(wizard, 'logger', logging.getLogger('wizard_test'), raising=False)
	monkeypatch.setattr(wizard, 'is_real_hardware', lambda *a, **k: False)
	monkeypatch.setattr(wizard.time, 'sleep', lambda *a, **k: None)

	class _Result:
		returncode = 0
		stdout = ''
		stderr = ''

	monkeypatch.setattr(wizard.subprocess, 'run', lambda *a, **k: _Result())


def test_run_wizard_no_probe_devices(ds, no_install):
	settings = c.default_settings()
	settings['probe_settings']['probe_map']['probe_devices'] = []
	c.write_settings_store(settings)

	wizard_data = c.read_wizard()
	install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)

	# No probe devices -> probes profile_selected is empty, but it still carries
	# a units setting. This must not raise.
	assert install_info['modules']['probes']['profile_selected'] == []
	assert 'units' in install_info['modules']['probes']['settings']

	wizard.run_wizard(settings, wizard_data, install_info)


def test_run_wizard_dev_mode_resolves_to_restart_not_reboot(ds, no_install):
	settings = c.default_settings()
	settings['probe_settings']['probe_map']['probe_devices'] = []
	c.write_settings_store(settings)

	wizard_data = c.read_wizard()
	install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)

	wizard.run_wizard(settings, wizard_data, install_info)

	percent, status, output = c.get_wizard_install_status()
	assert percent == 101
