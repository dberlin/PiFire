"""End-to-end persistence coverage for every command the QtQuick touchscreen
UI's hamburger menu (display/qml/Menus.js + Actions.js) can send through
Display._dispatch_command (display/qtquick_flex.py).

The existing test_dispatch_delegates_pmode_to_command_handler in
test_qtquick_display.py monkeypatches `write_control`/`_command_handler`
away entirely, so it only proves routing -- never that a command actually
persists anywhere. These tests run the real dispatch chain against a real
(isolated, temp-file) SQLite-backed datastore, including draining
`queue_control_write` via `execute_control_writes()` -- the same call
controller/runtime/controller.py's loop makes -- since `WriteKind.MERGE`
control writes are queued, not applied synchronously. Without that drain
step, EVERY MERGE-based command (all of them except PMode, which writes
settings directly) looks like a no-op, which is what makes an ad hoc
"just call _dispatch_command and read back" check misleading.
"""

import os
import tempfile

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from common import datastore
from common.common import (
	WriteKind,
	default_control,
	default_settings,
	execute_control_writes,
	read_control,
	read_settings,
	read_status,
	write_control,
	write_settings_store,
	write_status,
)

import display.qtquick_flex as qtquick_flex


@pytest.fixture
def isolated_store():
	tmp_dir = tempfile.mkdtemp(prefix='pifire_test_dispatch_persistence_')
	db_path = os.path.join(tmp_dir, 'dispatch.db')
	datastore._reset_for_tests(db_path)
	datastore.init()
	write_settings_store(default_settings())
	write_control(default_control(), WriteKind.OVERWRITE, origin='test')
	write_status(read_status(init=True))
	yield
	datastore._reset_for_tests(None)


def dispatch_and_drain(command, value=0):
	"""Runs a command through the real (unmocked) dispatch chain, then drains
	queue_control_write -- mirroring what controller.py's loop does."""
	d = qtquick_flex.Display.for_dispatch({}, 'F')
	d._dispatch_command(command, value)
	execute_control_writes()


@pytest.mark.parametrize(
	'command,expected_mode',
	[('cmd_startup', 'Startup'), ('cmd_monitor', 'Monitor'), ('cmd_smoke', 'Smoke'), ('cmd_shutdown', 'Shutdown')],
)
def test_simple_mode_commands_persist_to_control(isolated_store, command, expected_mode):
	dispatch_and_drain(command)
	assert read_control()['mode'] == expected_mode


def test_smoke_plus_toggles_and_persists(isolated_store):
	# cmd_splus is special-cased in _dispatch_command to flip based on
	# read_status()['s_plus'] (the controller's last-reported actual state),
	# not read_control()['s_plus'] (a pending request) -- so the toggle
	# direction here mirrors the controller updating status in response to
	# each control write, the same way it would on real hardware.
	assert read_status()['s_plus'] is False
	dispatch_and_drain('cmd_splus')
	assert read_control()['s_plus'] is True

	write_status({**read_status(), 's_plus': True})
	dispatch_and_drain('cmd_splus')
	assert read_control()['s_plus'] is False


def test_prime_startup_persists_amount_and_next_mode(isolated_store):
	dispatch_and_drain('cmd_primestartup', 25)
	control = read_control()
	assert control['mode'] == 'Prime'
	assert control['prime_amount'] == 25
	assert control['next_mode'] == 'Startup'


def test_prime_only_persists_amount_and_next_mode(isolated_store):
	dispatch_and_drain('cmd_primeonly', 25)
	control = read_control()
	assert control['mode'] == 'Prime'
	assert control['prime_amount'] == 25
	assert control['next_mode'] == 'Stop'


def test_hold_persists_mode_and_setpoint(isolated_store):
	dispatch_and_drain('cmd_hold', 225)
	control = read_control()
	assert control['mode'] == 'Hold'
	assert control['primary_setpoint'] == 225


def test_notify_persists_target_for_matching_origin(isolated_store):
	origin = read_control()['notify_data'][0]['name']
	dispatch_and_drain('cmd_notify', {'origin': origin, 'target': 300})
	entry = next(e for e in read_control()['notify_data'] if e['name'] == origin)
	assert entry['target'] == 300
	assert entry['req'] is True


def test_pmode_persists_to_settings_not_control(isolated_store):
	# The one command that writes settings directly (no queue involved) --
	# see verify_pmode_e2e in the earlier investigation.
	assert read_settings()['cycle_data']['PMode'] != 4
	dispatch_and_drain('cmd_pmode', 4)
	assert read_settings()['cycle_data']['PMode'] == 4


def test_stop_is_special_cased_and_skips_init_framework(isolated_store):
	# cmd_stop is one of the 6 commands _dispatch_command special-cases
	# (see the "Command adapter" comment in qtquick_flex.py) specifically so
	# it does NOT call the inherited _init_framework(), which needs the
	# pygame menu/input JSON this display never ships.
	dispatch_and_drain('cmd_startup')  # move off the default 'Stop' first
	dispatch_and_drain('cmd_stop')
	assert read_control()['mode'] == 'Stop'
