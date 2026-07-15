"""Tests for board-config.py's diff-based reboot-required reporting.

board-config.py is invoked as a subprocess by the wizard (command_list entries) and is
never imported as a package -- and its filename has a hyphen, so it can't be `import`ed
normally. It's loaded here via importlib. Importing it this way does NOT execute the
`if __name__ == '__main__':` block (module __name__ is not '__main__'), so no argparse/
logging/file side effects happen at import time.

All file I/O in these tests is redirected to tmp_path -- get_os_info() is monkeypatched
to force board-config.py's "Test Mode" branch (./local/config.txt), and set_backlight's
hardcoded /etc/udev path is never actually written to (create_file is monkeypatched).
Nothing here ever touches real /boot or /etc files, and no sudo/subprocess call is made.
"""

import importlib.util
import pathlib

import pytest

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / 'board-config.py'
_SPEC = importlib.util.spec_from_file_location('board_config_under_test', _MODULE_PATH)
board_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(board_config)


def _settings(system_type='prototype', pwm_pin=13, onewire_pin=None, shutdown_pin=17):
	return {
		'platform': {
			'system_type': system_type,
			'outputs': {'pwm': pwm_pin},
			'system': {'1WIRE': onewire_pin},
			'inputs': {'shutdown': shutdown_pin},
		}
	}


@pytest.fixture(autouse=True)
def _test_mode_config(monkeypatch, tmp_path):
	"""Force rpi_config_write() onto a scratch config.txt under tmp_path -- never the
	real /boot/config.txt or /boot/firmware/config.txt."""
	monkeypatch.setattr(board_config, 'get_os_info', lambda *a, **k: {'VERSION_ID': 'test-mode'})
	monkeypatch.chdir(tmp_path)
	local_dir = tmp_path / 'local'
	local_dir.mkdir()
	config_txt = local_dir / 'config.txt'
	config_txt.write_text('')
	return config_txt


def test_enable_spi_first_run_changes_and_writes_line(monkeypatch, _test_mode_config):
	monkeypatch.setattr(board_config, 'read_settings', lambda: _settings())

	message, changed = board_config.enable_spi()

	assert changed is True
	assert 'dtparam=spi=on' in _test_mode_config.read_text()


def test_enable_spi_second_run_with_same_settings_is_a_noop(monkeypatch, _test_mode_config):
	monkeypatch.setattr(board_config, 'read_settings', lambda: _settings())

	board_config.enable_spi()
	before = _test_mode_config.read_text()
	message, changed = board_config.enable_spi()

	assert changed is False
	assert _test_mode_config.read_text() == before


def test_set_onewire_gpio_disabling_an_already_disabled_pin_is_a_noop(monkeypatch, _test_mode_config):
	monkeypatch.setattr(board_config, 'read_settings', lambda: _settings(onewire_pin=6))
	board_config.set_onewire_gpio()

	monkeypatch.setattr(board_config, 'read_settings', lambda: _settings(onewire_pin=None))
	_, changed_first_disable = board_config.set_onewire_gpio()
	assert changed_first_disable is True

	_, changed_second_disable = board_config.set_onewire_gpio()
	assert changed_second_disable is False


def test_set_backlight_never_reports_a_reboot_even_when_it_writes(monkeypatch, tmp_path):
	"""set_backlight only ever writes a udev rule, never config.txt -- it must never
	require a reboot. create_file is monkeypatched so the test never touches the real
	hardcoded /etc/udev/rules.d path."""
	monkeypatch.setattr(board_config, 'read_settings', lambda: _settings(system_type='raspberry_pi_all'))
	monkeypatch.setattr(board_config, 'create_file', lambda filename, lines: f'wrote {filename}')

	message, changed = board_config.set_backlight()

	assert changed is False


def test_append_file_adds_missing_line(tmp_path):
	target = tmp_path / 'modules'
	target.write_text('some-other-module\n')

	message, changed = board_config.append_file(str(target), 'i2c-dev\n')

	assert changed is True
	assert 'i2c-dev' in target.read_text()


def test_append_file_is_idempotent_when_line_already_present(tmp_path):
	target = tmp_path / 'modules'
	target.write_text('i2c-dev\n')

	message, changed = board_config.append_file(str(target), 'i2c-dev\n')

	assert changed is False
	assert target.read_text().count('i2c-dev') == 1


def test_append_file_creates_missing_file(tmp_path):
	target = tmp_path / 'modules'  # does not exist yet

	message, changed = board_config.append_file(str(target), 'i2c-dev\n')

	assert changed is True
	assert target.read_text() == 'i2c-dev\n'


def test_enable_i2c_changed_is_or_of_config_and_modules_changes(monkeypatch):
	"""Isolates enable_i2c's own OR-aggregation logic from rpi_config_write/append_file
	(covered by their own tests above) and never touches the real /etc/modules path."""
	monkeypatch.setattr(board_config, 'read_settings', lambda: _settings(system_type='raspberry_pi_all'))

	monkeypatch.setattr(board_config, 'rpi_config_write', lambda *a, **k: ('dtparam ok', False))
	monkeypatch.setattr(board_config, 'append_file', lambda *a, **k: ('modules ok', True))
	_, changed = board_config.enable_i2c()
	assert changed is True

	monkeypatch.setattr(board_config, 'rpi_config_write', lambda *a, **k: ('dtparam ok', False))
	monkeypatch.setattr(board_config, 'append_file', lambda *a, **k: ('modules ok', False))
	_, changed = board_config.enable_i2c()
	assert changed is False


class _NullLogger:
	def info(self, *_a, **_k):
		pass


def test_print_results_reports_reboot_required_true_when_any_flag_true(capsys):
	reboot_required = board_config._print_results_and_reboot_flag(
		['thing: SUCCESS'], [False, True, False], _NullLogger()
	)

	assert reboot_required is True
	assert 'REBOOT_REQUIRED=true' in capsys.readouterr().out


def test_print_results_reports_reboot_required_false_when_no_flags_true(capsys):
	reboot_required = board_config._print_results_and_reboot_flag(['thing: SUCCESS'], [False, False], _NullLogger())

	assert reboot_required is False
	assert 'REBOOT_REQUIRED=false' in capsys.readouterr().out


def test_print_results_reports_reboot_required_false_with_no_flags(capsys):
	reboot_required = board_config._print_results_and_reboot_flag([], [], _NullLogger())
	captured = capsys.readouterr().out

	assert reboot_required is False
	assert 'REBOOT_REQUIRED=false' in captured
	assert 'No Arguments Found' in captured
