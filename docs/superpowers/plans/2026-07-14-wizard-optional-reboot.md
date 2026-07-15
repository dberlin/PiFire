# Wizard Optional Reboot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static, over-broad `reboot_required` manifest flag in the setup wizard with a dynamic, per-run signal reported by the actual install commands, and make the resulting reboot optional (offer "restart services only" instead of forcing `reboot_system()`).

**Architecture:** Install commands (`board-config.py`, `wizard/ds18b20.sh`) print a final `REBOOT_REQUIRED=true`/`false` line to stdout, based on whether they actually changed something in `/boot/config.txt` (a real device-tree/dtoverlay change) — never based on which module was merely selected. `wizard.py`'s command-execution loop parses that sentinel (fixing a readline/poll ordering bug that would otherwise silently drop it) and aggregates it via OR across all commands run. `wizard-finish.html` shows a modal (reboot now vs. restart services only) instead of silently redirecting to `/admin/reboot` when a reboot is actually needed.

**Tech Stack:** Python 3.14+, Flask/Jinja2, pytest, bash, jQuery/Bootstrap 4 (existing stack — no new dependencies).

## Global Constraints

- Never invoke a real `reboot`, `shutdown`, `systemctl reboot/restart`, or `raspi-config` command during tests — mock/stub every `subprocess`/`os.system`/`sudo` call point. A `real_hw=False`-style flag alone is not sufficient; tests must not exec the real binaries at all.
- Run `ruff format` on every changed Python file before each commit (standing repo rule).
- `except (A, B):` written as bare `except A, B` is ruff-canonical for this repo (3.14+) — do not "fix" it if you see it.
- Tests run via `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/<file> -v`.
- `updater.py` / `updater_manifest.json` (the separate software-updater flow) are out of scope — do not touch them.
- Follow existing repo conventions found during research: tabs for indentation in `.py`/`.html` files (matches existing files), `monkeypatch.setattr(wizard, 'logger', logging.getLogger(...), raising=False)` pattern for wizard.py tests, `datastore._reset_for_tests(...)` fixture pattern for DB isolation.

---

## File Structure

- **Modify `board-config.py`**: `rpi_config_write()` becomes diff-based (only reports/writes a change if content actually differs); every wrapper (`set_pwm_gpio`, `set_onewire_gpio`, `set_backlight`, `enable_spi`, `enable_i2c`, `set_i2c_speed`, `enable_gpio_shutdown`) returns `(message, changed)` instead of just `message`; `append_file()` becomes idempotent; `__main__` aggregates all `changed` flags and prints a final `REBOOT_REQUIRED=<bool>` line via a new small testable helper `_print_results_and_reboot_flag()`.
- **Modify `wizard/ds18b20.sh`**: adds a config.txt idempotency check (skip `raspi-config` + report `false` if the onewire overlay is already present) and prints the `REBOOT_REQUIRED=` sentinel. Adds a `PIFIRE_CONFIG_TXT` env var override so tests can point it at a scratch file instead of real `/boot` paths.
- **Modify `wizard.py`**: extracts the `command_list` execution loop into a standalone `_run_install_commands(command_list, percent, increment, status, python_exec)` function (fixing the readline/poll line-loss bug and adding sentinel parsing), removes the static-manifest-flag aggregation, and calls the new helper from `run_wizard()`.
- **Modify `wizard/wizard_manifest.json`**: removes the now-unused `reboot_required` key from every module entry.
- **Modify `blueprints/wizard/templates/wizard/wizard-finish.html`**: adds the reboot-required modal (matching the existing `cancelModal`/`runningModal` Bootstrap pattern in `blueprints/wizard/templates/wizard/wizard.html`) and wires its two buttons to `/admin/reboot` and `/admin/restart`.
- **Create `tests/test_board_config_reboot.py`**: unit tests for `rpi_config_write`, `append_file`, `set_backlight`, `enable_i2c`, and the `__main__`-level aggregation helper.
- **Create `tests/test_ds18b20_reboot_sentinel.py`**: subprocess-level tests for the shell script, with `sudo` stubbed on `PATH`.
- **Create `tests/test_wizard_reboot_sentinel.py`**: unit tests for `_run_install_commands`, including the readline/poll regression test, plus one `run_wizard`-level integration test.
- **Create `tests/test_wizard_manifest_no_static_reboot_flag.py`**: one-assertion hygiene test.
- **Create `tests/test_wizard_finish_reboot_modal.py`**: a static Jinja-render structure test plus a Playwright e2e test (skips cleanly if Chromium isn't installed, matching `tests/test_wizard_nested_modal_scroll.py`'s existing pattern).

---

## Task 1: `board-config.py` — diff-based `rpi_config_write()` and idempotent `append_file()`

**Files:**
- Modify: `board-config.py:210-304` (`rpi_config_write`), `board-config.py:388-397` (`append_file`)
- Test: `tests/test_board_config_reboot.py` (new)

**Interfaces:**
- Produces: `rpi_config_write(config_type, feature, add_config={}, pin=0, param='', pin_type='gpio_pin') -> (result: str, changed: bool)`
- Produces: `append_file(filename, lines) -> (result: str, changed: bool)` (`lines` may be a `str` or a `list[str]`, matching existing callers)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_board_config_reboot.py`:

```python
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

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / 'board-config.py'
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_board_config_reboot.py -v`
Expected: FAIL — `enable_spi()` etc. currently return a plain string, so `message, changed = board_config.enable_spi()` raises `ValueError: too many values to unpack` (a string is iterable character-by-character) or similar; `append_file`/`set_backlight` failures too.

- [ ] **Step 3: Implement `rpi_config_write()` as diff-based**

In `board-config.py`, replace the `rpi_config_write` function body (currently lines 210-304):

```python
def rpi_config_write(config_type, feature, add_config={}, pin=0, param='', pin_type='gpio_pin'):
	result = 'SUCCESS'
	changed = False
	""" Check OS version, so we can get the correct location of config.txt """
	os_info = get_os_info()
	version = os_info.get('VERSION_ID', None)
	if version in ['12', '13']:
		""" Version 12 Bookworm or Version 13 Trixie """
		config_filename = '/boot/firmware/config.txt'
	elif version == '11':
		""" Version 11 Bullseye """
		config_filename = '/boot/config.txt'
	else:
		""" Test Mode """
		config_filename = './local/config.txt'

	""" Modify the configuration file """
	try:
		""" Open the configuration file """
		with open(config_filename, 'r+') as config_txt:
			config_data = config_txt.readlines()

		original_config_data = list(config_data)

		# Remove old pwm overlay lines if adding new pwm-2chan overlay
		if config_type == 'dtoverlay' and feature == 'pwm-2chan':
			new_config_data = []
			for line in config_data:
				# Remove lines like: dtoverlay=pwm,pin=*,func=4 (with or without comments)
				if line.strip().startswith('dtoverlay=pwm,') and 'func=4' in line:
					continue  # skip this line
				new_config_data.append(line)
			config_data = new_config_data

		""" Look for the configuration line if it exists already """
		found = False
		for index in range(0, len(config_data)):
			if config_type in config_data[index] and feature in config_data[index]:
				found = True
				# Check for leading hashtag and remove
				config_line = remove_hashtag(config_data[index])

				# If the pin is marked as disabled / None, then comment out the line
				if pin == None:
					config_data[index] = f'#{config_line}'
				else:
					# Remove the preceding configuration type
					config_line = config_line.replace(f'{config_type}=', '')

					# Get dictionary of the components
					config_dict = parse_config_line(config_line)

					# For dtparams, turn on feature
					if config_type == 'dtparam':
						if param == '':
							config_dict[feature] = 'on'
						else:
							config_dict[feature] = param

					# For dtoverlay, edit gpio-pin and additional features
					elif config_type == 'dtoverlay':
						# Modify pin number
						if pin > 0:
							for noun in ['gpio-pin', 'gpiopin', 'gpio_pin', 'pin']:
								if noun in config_dict[feature].keys():
									config_dict[feature].pop(noun, None)
									config_dict[feature][pin_type] = str(pin)

						# If function, add function number
						if add_config != {}:
							for key, value in add_config.items():
								config_dict[feature][key] = value

					""" Create the modified configuration line """
					config_data[index] = build_config_line(config_type, config_dict)
				break

		if not found and pin is not None:
			config_dict = {}
			if config_type == 'dtoverlay':
				config_dict[feature] = {}
				config_dict[feature][pin_type] = pin
				if add_config != {}:
					for key, value in add_config.items():
						config_dict[feature][key] = value
			elif config_type == 'dtparam':
				config_dict[feature] = 'on'

			config_data.append(build_config_line(config_type, config_dict))

		changed = config_data != original_config_data

		""" Write all data back to the file, only if something actually changed --
		this is what makes re-running the wizard with identical settings correctly
		report no reboot needed. """
		if changed:
			with open(config_filename, 'w') as config_txt:
				config_txt.writelines(config_data)

	except:
		result = 'FAILED '
		changed = False

	return result, changed
```

- [ ] **Step 4: Implement idempotent `append_file()`**

Replace `append_file` (currently lines 388-397):

```python
def append_file(filename, lines):
	result = f'\n - Attempting to append data to {filename}: '
	if isinstance(lines, str):
		lines = [lines]
	changed = False
	try:
		try:
			with open(filename, 'r') as file:
				existing_lines = file.read().splitlines()
		except FileNotFoundError:
			existing_lines = []

		missing_lines = [line for line in lines if line.rstrip('\n') not in existing_lines]

		if missing_lines:
			with open(filename, 'a+') as file:
				for line in missing_lines:
					file.write(line)
			changed = True
			result += f' SUCCESS (appending file {filename}) '
		else:
			result += f' SUCCESS (no change, already present in {filename}) '
	except:
		result += f' FAILED (appending file {filename}) '
	return result, changed
```

- [ ] **Step 5: Thread `changed` through `set_pwm_gpio`, `set_onewire_gpio`, `enable_spi`, `set_i2c_speed`, `enable_gpio_shutdown`**

Replace each of these (currently lines 41-61, 64-84, 109-127, 155-174, 177-200) — same logic, now returning a tuple:

```python
def set_pwm_gpio():
	result = 'Setting the PWM pin: '
	changed = False
	try:
		settings = read_settings()
		pin = settings['platform']['outputs']['pwm']
		system_type = settings['platform']['system_type']
	except:
		result += 'FAILED (error getting settings.json data) '
		return result, changed

	try:
		if system_type == 'raspberry_pi_all' or system_type == 'prototype':
			# "dtoverlay=pwm-2chan,pin=13,func=4"
			pin = int(pin) if pin != None else None
			msg, changed = rpi_config_write('dtoverlay', 'pwm-2chan', add_config={'func': '4'}, pin=pin, pin_type='pin')
			result += msg
		else:
			result += 'NA - No system defined'
	except:
		result += 'FAILED (error making the configuration change) '

	return result, changed


def set_onewire_gpio():
	result = 'Setting the 1Wire pin: '
	changed = False
	try:
		settings = read_settings()
		pin = settings['platform']['system']['1WIRE']
		system_type = settings['platform']['system_type']
	except:
		result += 'FAILED (error getting settings.json data) '
		return result, changed

	try:
		if system_type == 'raspberry_pi_all' or system_type == 'prototype':
			# "dtoverlay=w1-gpio,pin=6"
			pin = int(pin) if pin != None else None
			msg, changed = rpi_config_write('dtoverlay', 'w1-gpio', pin=pin, pin_type='gpiopin')
			result += msg
		else:
			result += 'NA - No system defined'
	except:
		result += 'FAILED (error making the configuration change) '

	return result, changed


def enable_spi():
	result = 'Enabling SPI: '
	changed = False
	try:
		settings = read_settings()
		system_type = settings['platform']['system_type']
	except:
		result += 'FAILED (error getting settings.json data) '
		return result, changed

	try:
		if system_type == 'raspberry_pi_all' or system_type == 'prototype':
			# "dtparam=spi=on"
			msg, changed = rpi_config_write('dtparam', 'spi')
			result += msg
		else:
			result += 'NA - No system defined'
	except:
		result += 'FAILED (error making the configuration change) '

	return result, changed


def set_i2c_speed(baud=100000):
	result = f'Setting I2C speed ({baud} Baud): '
	changed = False
	try:
		settings = read_settings()
		system_type = settings['platform']['system_type']
	except:
		result += 'FAILED (error getting settings.json data) '
		return result, changed

	try:
		if system_type == 'raspberry_pi_all' or system_type == 'prototype':
			# dtparam=i2c_arm_baudrate=100000
			msg, changed = rpi_config_write('dtparam', 'i2c_arm_baudrate', param=baud)
			result += msg
		else:
			result += 'NA - No system defined'

	except:
		result += 'FAILED (error making the configuration change) '

	return result, changed


def enable_gpio_shutdown():
	result = 'Enabling the GPIO Shutdown pin: '
	changed = False
	try:
		settings = read_settings()
		pin = settings['platform']['inputs']['shutdown']
		system_type = settings['platform']['system_type']
	except:
		result += 'FAILED (error getting settings.json data) '
		return result, changed

	try:
		if system_type == 'raspberry_pi_all' or system_type == 'prototype':
			# dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up
			add_config = {'active_low': '1', 'gpio_pull': 'up'}
			pin = int(pin) if pin != None else None
			msg, changed = rpi_config_write(
				'dtoverlay', 'gpio-shutdown', add_config=add_config, pin=pin, pin_type='gpio_pin'
			)
			result += msg
		else:
			result += 'NA - No system defined'
	except:
		result += 'FAILED (error making the configuration change) '

	return result, changed
```

- [ ] **Step 6: Update `set_backlight()` and `enable_i2c()`**

Replace `set_backlight` (currently lines 87-106):

```python
def set_backlight():
	result = 'Enabling Backlight Control for DSI Touch Display: '
	# A udev rule, not a config.txt/device-tree change -- never requires a reboot.
	changed = False
	try:
		settings = read_settings()
		system_type = settings['platform']['system_type']
	except:
		result += 'FAILED (error getting settings.json data) '
		return result, changed

	try:
		if system_type == 'raspberry_pi_all':
			lines = [
				'SUBSYSTEM=="backlight",RUN+="/bin/chmod 666 /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power"\n'
			]
			file = '/etc/udev/rules.d/backlight-permissions.rules'
			result += create_file(file, lines)
	except:
		result += 'FAILED (error making the configuration change) '

	return result, changed
```

Replace `enable_i2c` (currently lines 130-152):

```python
def enable_i2c():
	result = 'Enabling I2C: '
	changed = False
	try:
		settings = read_settings()
		system_type = settings['platform']['system_type']
	except:
		result += 'FAILED (error getting settings.json data) '
		return result, changed

	try:
		if system_type == 'raspberry_pi_all':
			# dtparam=i2c_arm=on
			msg, dtparam_changed = rpi_config_write('dtparam', 'i2c_arm')
			result += msg
			# To enable userspace access to I2C ensure that /etc/modules contains "i2c-dev"
			msg, modules_changed = append_file('/etc/modules', 'i2c-dev\n')
			result += msg
			changed = dtparam_changed or modules_changed
		else:
			result += 'NA - No system defined'

	except:
		result += 'FAILED (error making the configuration change) '

	return result, changed
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_board_config_reboot.py -v`
Expected: PASS (all tests green)

- [ ] **Step 8: Format and commit**

```bash
uvx ruff format board-config.py tests/test_board_config_reboot.py
git add board-config.py tests/test_board_config_reboot.py
git commit -m "$(cat <<'EOF'
feat(wizard): make board-config.py report reboot necessity dynamically

rpi_config_write() and append_file() now only write and only report a
change when the target file's content actually differs, and every
config-writing wrapper returns (message, changed) instead of just a
message. set_backlight() (used by all DSI/QtQuick displays) always
reports changed=False since it only writes a udev rule, never
config.txt -- fixing DSI/QtQuick being wrongly flagged as always
requiring a reboot.
EOF
)"
```

---

## Task 2: `board-config.py` — `__main__` aggregation and `REBOOT_REQUIRED=` sentinel

**Files:**
- Modify: `board-config.py:493-566` (`__main__` block)
- Test: `tests/test_board_config_reboot.py` (extend)

**Interfaces:**
- Consumes: every wrapper from Task 1 returning `(message, changed)`
- Produces: `_print_results_and_reboot_flag(results: list[str], reboot_flags: list[bool], logger) -> bool` — prints the existing human-readable `Results:` block, then a final `REBOOT_REQUIRED=true`/`REBOOT_REQUIRED=false` line (lowercase), and returns the aggregated bool. This is what `wizard.py` (Task 5) parses out of subprocess stdout.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board_config_reboot.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_board_config_reboot.py -v -k print_results`
Expected: FAIL with `AttributeError: module 'board_config_under_test' has no attribute '_print_results_and_reboot_flag'`

- [ ] **Step 3: Implement the helper and wire up `__main__`**

Add this function to `board-config.py`, just above the `if __name__ == '__main__':` block:

```python
def _print_results_and_reboot_flag(results, reboot_flags, logger):
	"""Print the human-readable results block, then a final REBOOT_REQUIRED=<bool>
	sentinel line that wizard.py's command-execution loop parses to decide whether a
	reboot is actually needed. Returns the aggregated bool."""
	if len(results) == 0:
		print('No Arguments Found. Use --help to see available arguments')
	else:
		print('Results:')
		for item in results:
			print(f' - {item}')
			logger.info(f'{item}')

	reboot_required = any(reboot_flags)
	sentinel = f'REBOOT_REQUIRED={str(reboot_required).lower()}'
	print(sentinel)
	logger.info(sentinel)
	return reboot_required
```

Replace the tail of the `if __name__ == '__main__':` block (currently from `results = []` at line 528 through the end at line 566):

```python
	results = []
	reboot_flags = []

	if args.pwm:
		msg, changed = set_pwm_gpio()
		results.append(msg)
		reboot_flags.append(changed)

	if args.onewire:
		msg, changed = set_onewire_gpio()
		results.append(msg)
		reboot_flags.append(changed)

	if args.backlight:
		msg, changed = set_backlight()
		results.append(msg)
		reboot_flags.append(changed)

	if args.spi:
		msg, changed = enable_spi()
		results.append(msg)
		reboot_flags.append(changed)

	if args.i2c:
		msg, changed = enable_i2c()
		results.append(msg)
		reboot_flags.append(changed)

	if args.i2cspeed:
		msg, changed = set_i2c_speed(baud=args.i2cspeed)
		results.append(msg)
		reboot_flags.append(changed)

	if args.gpioshutdown:
		msg, changed = enable_gpio_shutdown()
		results.append(msg)
		reboot_flags.append(changed)

	if args.osversion:
		os_info = get_os_info(loggername='board_config')
		event = 'OS Version Information: '
		results.append(event)
		for key, value in os_info.items():
			event = f'   {key} : {value}'
			results.append(event)

	_print_results_and_reboot_flag(results, reboot_flags, logger)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_board_config_reboot.py -v`
Expected: PASS (all tests in the file green)

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format board-config.py tests/test_board_config_reboot.py
git add board-config.py tests/test_board_config_reboot.py
git commit -m "$(cat <<'EOF'
feat(wizard): print REBOOT_REQUIRED sentinel from board-config.py CLI

Aggregates every invoked config function's changed flag and prints a
final REBOOT_REQUIRED=true/false line, which wizard.py's command loop
will parse instead of relying on a static per-module manifest flag.
EOF
)"
```

---

## Task 3: `wizard/ds18b20.sh` — idempotency check and sentinel

**Files:**
- Modify: `wizard/ds18b20.sh`
- Test: `tests/test_ds18b20_reboot_sentinel.py` (new)

**Interfaces:**
- Produces: prints `REBOOT_REQUIRED=true` (onewire overlay newly enabled) or `REBOOT_REQUIRED=false` (already present) to stdout. Reads config.txt path from `PIFIRE_CONFIG_TXT` env var if set (test-only override), else falls back to real `/boot/firmware/config.txt` / `/boot/config.txt` detection.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ds18b20_reboot_sentinel.py`:

```python
"""Tests for wizard/ds18b20.sh's reboot-required sentinel and idempotency.

Runs the real script via bash, but with `sudo` stubbed out on PATH -- so the real
`raspi-config` (a real system config change) is never invoked regardless of what's
installed on the test host -- and with the config.txt path pointed at a scratch file
via PIFIRE_CONFIG_TXT, so /boot is never touched.
"""

import os
import pathlib
import stat
import subprocess

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / 'wizard' / 'ds18b20.sh'


@pytest.fixture
def fake_sudo_bin(tmp_path):
	"""A directory containing a fake `sudo` that logs its args and exits 0 -- never
	execs anything for real. Prepending this to PATH means bash resolves `sudo` to
	this stub before it ever finds the real /usr/bin/sudo."""
	bin_dir = tmp_path / 'fakebin'
	bin_dir.mkdir()
	sudo_log = tmp_path / 'sudo-calls.log'
	fake_sudo = bin_dir / 'sudo'
	fake_sudo.write_text(f'#!/bin/bash\necho "$*" >> "{sudo_log}"\nexit 0\n')
	fake_sudo.chmod(fake_sudo.stat().st_mode | stat.S_IEXEC)
	return bin_dir, sudo_log


def _run_script(config_txt_path, fake_sudo_bin):
	bin_dir, _ = fake_sudo_bin
	env = dict(os.environ)
	env['PATH'] = f'{bin_dir}:{env["PATH"]}'
	env['PIFIRE_CONFIG_TXT'] = str(config_txt_path)
	return subprocess.run(['bash', str(_SCRIPT)], env=env, capture_output=True, text=True, timeout=10)


def test_enables_onewire_when_not_yet_configured(tmp_path, fake_sudo_bin):
	config_txt = tmp_path / 'config.txt'
	config_txt.write_text('# empty config\n')
	_, sudo_log = fake_sudo_bin

	result = _run_script(config_txt, fake_sudo_bin)

	assert 'REBOOT_REQUIRED=true' in result.stdout
	assert sudo_log.read_text().strip() == 'raspi-config nonint do_onewire 0'


def test_is_a_noop_when_already_configured(tmp_path, fake_sudo_bin):
	config_txt = tmp_path / 'config.txt'
	config_txt.write_text('dtoverlay=w1-gpio\n')
	_, sudo_log = fake_sudo_bin

	result = _run_script(config_txt, fake_sudo_bin)

	assert 'REBOOT_REQUIRED=false' in result.stdout
	assert not sudo_log.exists()


def test_commented_out_overlay_is_treated_as_not_configured(tmp_path, fake_sudo_bin):
	config_txt = tmp_path / 'config.txt'
	config_txt.write_text('#dtoverlay=w1-gpio\n')
	_, sudo_log = fake_sudo_bin

	result = _run_script(config_txt, fake_sudo_bin)

	assert 'REBOOT_REQUIRED=true' in result.stdout
	assert sudo_log.read_text().strip() == 'raspi-config nonint do_onewire 0'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ds18b20_reboot_sentinel.py -v`
Expected: FAIL — current script has no `PIFIRE_CONFIG_TXT` support and never prints `REBOOT_REQUIRED=`, so all three assertions on `result.stdout` fail.

- [ ] **Step 3: Implement the idempotency check and sentinel**

Replace the entire contents of `wizard/ds18b20.sh`:

```bash
# This file will add the kernel support for 1-wire on (GPIO 4)
# Skips the raspi-config call (and reports no reboot needed) if the w1-gpio overlay is
# already active in config.txt, so re-running the wizard with the same selection
# doesn't force a reboot for something that hasn't changed.

CONFIG="${PIFIRE_CONFIG_TXT:-}"
if [ -z "$CONFIG" ]; then
	if [ -f /boot/firmware/config.txt ]; then
		CONFIG='/boot/firmware/config.txt'
	else
		CONFIG='/boot/config.txt'
	fi
fi

if grep -Eq '^dtoverlay=w1-gpio(,|[[:space:]]|$)' "$CONFIG" 2>/dev/null; then
	echo "1-Wire (GPIO4) already enabled in $CONFIG"
	echo "REBOOT_REQUIRED=false"
else
	sudo raspi-config nonint do_onewire 0   # Enable 1-wire support
	echo "REBOOT_REQUIRED=true"
fi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ds18b20_reboot_sentinel.py -v`
Expected: PASS (all three tests green)

- [ ] **Step 5: Commit**

```bash
git add wizard/ds18b20.sh tests/test_ds18b20_reboot_sentinel.py
git commit -m "$(cat <<'EOF'
feat(wizard): make ds18b20.sh idempotent and report reboot necessity

Checks config.txt for an existing, uncommented w1-gpio overlay before
calling raspi-config, and prints a REBOOT_REQUIRED sentinel instead of
always assuming a reboot is needed.
EOF
)"
```

---

## Task 4: `wizard.py` — extract `_run_install_commands`, fix the readline/poll bug, parse the sentinel

**Files:**
- Modify: `wizard.py:220-372` (`run_wizard`, the `command_list` loop, and the reboot-required percent logic)
- Test: `tests/test_wizard_reboot_sentinel.py` (new)

**Interfaces:**
- Consumes: `is_real_hardware()`, `subprocess.Popen`, `set_wizard_install_status`, `set_updater_install_status`, `logger` — all already module-level names in `wizard.py`
- Produces: `_run_install_commands(command_list, percent, increment, status, python_exec) -> (percent: float, reboot_required: bool)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wizard_reboot_sentinel.py`:

```python
"""Tests for wizard.py's dynamic reboot-required detection.

_run_install_commands() replaces the old static-manifest-flag lookup: it runs each
command in command_list and ORs together whichever ones print a REBOOT_REQUIRED=true
sentinel as (usually) their last line of stdout.

Includes a regression test for a readline/poll ordering bug: the original loop checked
`process.poll()` immediately after `readline()`, before processing the line just read.
Since our sentinel line is always the last thing a script prints right before exiting,
a process that has already exited by the time poll() is checked would have its final
line silently discarded -- exactly when the sentinel matters most.

subprocess.Popen is always mocked here; no real command is ever executed.
"""

import logging

import pytest

import wizard


class _FakeProcess:
	"""Models `poll()` returning "still running" (None) as long as there are unread
	lines, and "exited" (0) once the last line has been read -- i.e. the process exits
	right as its final line becomes available, which is the real-world case that
	drops the last line under the old (buggy) loop ordering."""

	def __init__(self, lines):
		self._lines = list(lines)
		self._index = 0

	@property
	def stdout(self):
		return self

	def readline(self):
		if self._index < len(self._lines):
			line = self._lines[self._index]
			self._index += 1
			return line
		return ''

	def poll(self):
		return 0 if self._index >= len(self._lines) else None


@pytest.fixture(autouse=True)
def _quiet_status(monkeypatch):
	monkeypatch.setattr(wizard, 'logger', logging.getLogger('wizard_reboot_test'), raising=False)
	monkeypatch.setattr(wizard, 'set_wizard_install_status', lambda *a, **k: None)
	monkeypatch.setattr(wizard, 'set_updater_install_status', lambda *a, **k: None)


def test_reboot_required_sentinel_as_last_line_is_not_dropped(monkeypatch):
	monkeypatch.setattr(wizard, 'is_real_hardware', lambda *a, **k: True)
	fake = _FakeProcess(['doing setup things\n', 'REBOOT_REQUIRED=true\n'])
	monkeypatch.setattr(wizard.subprocess, 'Popen', lambda *a, **k: fake)

	percent, reboot_required = wizard._run_install_commands(
		command_list=[['sudo', 'python', 'board-config.py', '-s']],
		percent=50,
		increment=10,
		status='Installing...',
		python_exec='python',
	)

	assert reboot_required is True
	assert percent == 60


def test_reboot_required_false_sentinel_as_last_line(monkeypatch):
	monkeypatch.setattr(wizard, 'is_real_hardware', lambda *a, **k: True)
	fake = _FakeProcess(['doing setup things\n', 'REBOOT_REQUIRED=false\n'])
	monkeypatch.setattr(wizard.subprocess, 'Popen', lambda *a, **k: fake)

	_, reboot_required = wizard._run_install_commands(
		command_list=[['sudo', 'python', 'board-config.py', '-bl']],
		percent=0,
		increment=10,
		status='Installing...',
		python_exec='python',
	)

	assert reboot_required is False


def test_no_sentinel_at_all_defaults_to_false(monkeypatch):
	"""Matches raspi5.sh/bluepy.sh, which never print a sentinel."""
	monkeypatch.setattr(wizard, 'is_real_hardware', lambda *a, **k: True)
	fake = _FakeProcess(['some output\n', 'more output\n'])
	monkeypatch.setattr(wizard.subprocess, 'Popen', lambda *a, **k: fake)

	_, reboot_required = wizard._run_install_commands(
		command_list=[['bash', 'wizard/raspi5.sh']], percent=0, increment=10, status='Installing...', python_exec='python'
	)

	assert reboot_required is False


def test_multiple_commands_are_ored_together(monkeypatch):
	fakes = [
		_FakeProcess(['ok\n', 'REBOOT_REQUIRED=false\n']),
		_FakeProcess(['ok\n', 'REBOOT_REQUIRED=true\n']),
	]
	monkeypatch.setattr(wizard, 'is_real_hardware', lambda *a, **k: True)
	monkeypatch.setattr(wizard.subprocess, 'Popen', lambda *a, **k: fakes.pop(0))

	_, reboot_required = wizard._run_install_commands(
		command_list=[['cmd1'], ['cmd2']], percent=0, increment=10, status='Installing...', python_exec='python'
	)

	assert reboot_required is True


def test_dev_mode_never_runs_a_subprocess_and_never_requires_reboot(monkeypatch):
	monkeypatch.setattr(wizard, 'is_real_hardware', lambda *a, **k: False)
	monkeypatch.setattr(wizard.time, 'sleep', lambda *a, **k: None)
	called = []
	monkeypatch.setattr(wizard.subprocess, 'Popen', lambda *a, **k: called.append(1))

	_, reboot_required = wizard._run_install_commands(
		command_list=[['whatever']], percent=0, increment=10, status='Installing...', python_exec='python'
	)

	assert reboot_required is False
	assert called == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_wizard_reboot_sentinel.py -v`
Expected: FAIL with `AttributeError: module 'wizard' has no attribute '_run_install_commands'`

- [ ] **Step 3: Extract `_run_install_commands` and fix the readline/poll bug**

In `wizard.py`, remove the `reboot_required = False` initialization at line 223 and the manifest-based aggregation at lines 235-236 (the `if WizardData[...][...]['reboot_required']: reboot_required = True` block) — the module-gathering loop at lines 225-236 becomes:

```python
	for module in WizardInstallInfo['modules']:
		for selected in WizardInstallInfo['modules'][module]['profile_selected']:
			if module == 'grillplatform':
				selected = WizardInstallInfo['modules'][module]['settings']['current']
			for py_dependency in WizardData['modules'][module][selected]['py_dependencies']:
				py_dependencies.append(py_dependency)
			for apt_dependency in WizardData['modules'][module][selected]['apt_dependencies']:
				apt_dependencies.append(apt_dependency)
			for command in WizardData['modules'][module][selected]['command_list']:
				command_list.append(command)
```

Add this new function above `run_wizard`:

```python
def _run_install_commands(command_list, percent, increment, status, python_exec):
	"""Run each command in command_list, updating install status as we go.

	Returns (percent, reboot_required), where reboot_required is True if any command
	printed a REBOOT_REQUIRED=true sentinel on stdout (see board-config.py and
	wizard/ds18b20.sh). Absence of the sentinel is treated as False, so commands that
	never need a reboot (raspi5.sh, bluepy.sh) require no changes at all.
	"""
	reboot_required = False
	for command in command_list:
		if 'sudo' in command and 'python' in command:
			# replace "python" with python_exec in command list object
			command = [python_exec if item == 'python' else item for item in command]
		if is_real_hardware():
			process = subprocess.Popen(command, stdout=subprocess.PIPE, encoding='utf-8')
			while True:
				output = process.stdout.readline()
				if output:
					stripped = output.strip()
					set_wizard_install_status(percent, status, stripped)
					print(f'command output: {stripped}')
					logger.info(stripped)
					if stripped.lower().startswith('reboot_required='):
						if stripped.split('=', 1)[1].strip().lower() == 'true':
							reboot_required = True
				elif process.poll() is not None:
					break
		else:
			# This path is for development/testing
			time.sleep(2)

		percent += increment
		output = f' - Completed General Dependency Item'
		logger.info(output)
		set_updater_install_status(percent, status, output)

	return percent, reboot_required
```

Replace the old inline loop (previously lines 330-352) in `run_wizard` with a call to the new helper:

```python
	# Run system commands dependencies
	status = 'Installing General Dependencies...'
	output = ' - Installing General Dependencies'
	set_wizard_install_status(percent, status, output)

	percent, reboot_required = _run_install_commands(command_list, percent, increment, status, python_exec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_wizard_reboot_sentinel.py -v`
Expected: PASS (all five tests green)

- [ ] **Step 5: Extend `test_wizard_run_no_probes.py` to assert the dev-mode percent**

This confirms the spec's "dev/test mode never forces a reboot" behavior end-to-end through the real `run_wizard()`. Add to `tests/test_wizard_run_no_probes.py`:

```python
def test_run_wizard_dev_mode_resolves_to_restart_not_reboot(ds, no_install):
	settings = c.default_settings()
	settings['probe_settings']['probe_map']['probe_devices'] = []
	c.write_settings_store(settings)

	wizard_data = c.read_wizard()
	install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)

	wizard.run_wizard(settings, wizard_data, install_info)

	percent, status, output = c.get_wizard_install_status()
	assert percent == 101
```

- [ ] **Step 6: Run the extended test file to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_wizard_run_no_probes.py -v`
Expected: PASS (including the new test)

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format wizard.py tests/test_wizard_reboot_sentinel.py tests/test_wizard_run_no_probes.py
git add wizard.py tests/test_wizard_reboot_sentinel.py tests/test_wizard_run_no_probes.py
git commit -m "$(cat <<'EOF'
feat(wizard): parse REBOOT_REQUIRED sentinel instead of a static flag

Extracts the command_list execution loop into _run_install_commands(),
which ORs together each command's REBOOT_REQUIRED=<bool> stdout
sentinel instead of reading a static per-module manifest flag. Also
fixes a readline/poll ordering bug that would have silently dropped
the sentinel line, since it's always the last thing a script prints
before exiting.
EOF
)"
```

---

## Task 5: `wizard/wizard_manifest.json` — remove the unused static flag

**Files:**
- Modify: `wizard/wizard_manifest.json`
- Test: `tests/test_wizard_manifest_no_static_reboot_flag.py` (new)

**Interfaces:**
- None (data-only change; nothing else reads this field after Task 4)

- [ ] **Step 1: Write the failing test**

Create `tests/test_wizard_manifest_no_static_reboot_flag.py`:

```python
"""reboot_required is now determined dynamically per wizard run (see wizard.py's
_run_install_commands and board-config.py's REBOOT_REQUIRED sentinel) rather than
declared statically per module -- the old static flag in wizard_manifest.json is
unused and should not reappear."""

import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _find_reboot_required_paths(obj, path=()):
	found = []
	if isinstance(obj, dict):
		if 'reboot_required' in obj:
			found.append('.'.join(str(p) for p in path))
		for key, value in obj.items():
			found.extend(_find_reboot_required_paths(value, path + (key,)))
	elif isinstance(obj, list):
		for index, value in enumerate(obj):
			found.extend(_find_reboot_required_paths(value, path + (index,)))
	return found


def test_wizard_manifest_has_no_static_reboot_required_flags():
	with open(os.path.join(BASE, 'wizard', 'wizard_manifest.json')) as f:
		manifest = json.load(f)

	assert _find_reboot_required_paths(manifest) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_wizard_manifest_no_static_reboot_flag.py -v`
Expected: FAIL — lists ~80 paths like `modules.grillplatform.pcb_3.01a`

- [ ] **Step 3: Remove the field from the manifest**

```bash
python3 -c "
import json

path = 'wizard/wizard_manifest.json'
with open(path) as f:
	manifest = json.load(f)


def strip(obj):
	if isinstance(obj, dict):
		obj.pop('reboot_required', None)
		for value in obj.values():
			strip(value)
	elif isinstance(obj, list):
		for value in obj:
			strip(value)


strip(manifest)

with open(path, 'w') as f:
	json.dump(manifest, f, indent=2)
	f.write('\n')
"
```

Inspect the diff to confirm only `reboot_required` lines were removed and nothing else in the file's formatting shifted unexpectedly:

```bash
git diff --stat wizard/wizard_manifest.json
git diff wizard/wizard_manifest.json | head -60
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_wizard_manifest_no_static_reboot_flag.py -v`
Expected: PASS

- [ ] **Step 5: Run the full wizard test suite to confirm nothing else broke**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -v -k wizard`
Expected: PASS (all wizard-related tests, including the pre-existing ones from before this plan)

- [ ] **Step 6: Commit**

```bash
git add wizard/wizard_manifest.json tests/test_wizard_manifest_no_static_reboot_flag.py
git commit -m "$(cat <<'EOF'
chore(wizard): remove unused static reboot_required manifest flag

wizard.py now determines reboot necessity dynamically per run (see
_run_install_commands); the static per-module flag was never read
anywhere else and is a stale/misleading field now that it's unused.
EOF
)"
```

---

## Task 6: `wizard-finish.html` — reboot-optional modal

**Files:**
- Modify: `blueprints/wizard/templates/wizard/wizard-finish.html`
- Test: `tests/test_wizard_finish_reboot_modal.py` (new)

**Interfaces:**
- None new (pure template/JS change); consumes the existing `/wizard/installstatus`, `/admin/reboot`, `/admin/restart` endpoints unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wizard_finish_reboot_modal.py`:

```python
"""Tests for wizard-finish.html's reboot-required modal.

Two layers:
1. A cheap static Jinja render check that the modal markup/JS wiring exists.
2. A Playwright e2e check (skips cleanly if Chromium isn't installed, matching
   tests/test_wizard_nested_modal_scroll.py's existing pattern) that drives the real
   page: percent==142 shows the modal instead of auto-redirecting, and each button
   navigates to the right /admin/* URL; percent==101 still auto-redirects with no modal.

Network calls are mocked entirely in-browser via Playwright's page.route() -- nothing
here ever talks to a real Flask server, launches the real `python wizard.py &`
subprocess (which the real POST /wizard/finish route does), or hits the real
/admin/reboot or /admin/restart routes.
"""

import os

import jinja2
import pytest

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WIZARD_TEMPLATE_DIR = os.path.join(BASE, 'blueprints', 'wizard', 'templates')
BASE_TEMPLATE_DIR = os.path.join(BASE, 'templates')


def _render_wizard_finish():
	env = jinja2.Environment(loader=jinja2.FileSystemLoader([WIZARD_TEMPLATE_DIR, BASE_TEMPLATE_DIR]))
	env.globals['url_for'] = lambda *a, **k: '#'
	template = env.get_template('wizard/wizard-finish.html')
	return template.render(page_theme='light', grill_name='Test Grill')


def test_modal_markup_present_and_forces_a_choice():
	html = _render_wizard_finish()

	assert 'id="rebootModal"' in html
	assert 'data-backdrop="static"' in html
	assert 'data-keyboard="false"' in html
	assert 'id="rebootNowBtn"' in html
	assert 'id="restartServicesBtn"' in html
	# No dismiss/close (X) button inside the reboot modal specifically -- the user
	# must click one of the two explicit buttons.
	reboot_modal_start = html.index('id="rebootModal"')
	reboot_modal_chunk = html[reboot_modal_start : reboot_modal_start + 800]
	assert 'data-dismiss="modal"' not in reboot_modal_chunk


def test_js_shows_modal_on_142_and_auto_redirects_on_101():
	html = _render_wizard_finish()

	assert "data.percent == 142" in html
	assert "$('#rebootModal').modal('show')" in html
	assert "location.href = '/admin/restart'" in html


_PLAYWRIGHT_UNAVAILABLE_REASON = None
try:
	from playwright.sync_api import sync_playwright

	with sync_playwright() as _pw:
		if not os.path.exists(_pw.chromium.executable_path):
			_PLAYWRIGHT_UNAVAILABLE_REASON = (
				f'chromium not installed at {_pw.chromium.executable_path!r} -- '
				'run `uv run playwright install chromium`'
			)
except Exception as exc:  # pragma: no cover - only exercised if playwright itself is unusable here
	_PLAYWRIGHT_UNAVAILABLE_REASON = f'playwright unavailable: {exc}'


@pytest.mark.skipif(_PLAYWRIGHT_UNAVAILABLE_REASON is not None, reason=_PLAYWRIGHT_UNAVAILABLE_REASON or '')
class TestRebootModalInteraction:
	"""Serves the real rendered template over a real (local, static-asset-only) Flask
	dev server so jQuery/Bootstrap load correctly, but via a test-only route added
	directly to the running app instance in this fixture -- never through the real
	POST /wizard/finish route, which kicks off a real `python wizard.py &` process."""

	@pytest.fixture(scope='class')
	def live_server(self):
		import threading

		from werkzeug.serving import make_server

		from app import app as flask_app
		from flask import render_template

		@flask_app.route('/test-only/wizard-finish')
		def _test_only_wizard_finish():
			return render_template('wizard/wizard-finish.html', page_theme='light', grill_name='Test Grill')

		srv = make_server('127.0.0.1', 0, flask_app)
		port = srv.server_address[1]
		thread = threading.Thread(target=srv.serve_forever, daemon=True)
		thread.start()
		try:
			yield f'http://127.0.0.1:{port}'
		finally:
			srv.shutdown()
			thread.join(timeout=5)

	def _goto_with_mocked_status(self, page, base_url, percent):
		def _fulfill_status(route):
			route.fulfill(json={'percent': percent, 'status': 'Finished!', 'output': ' - Finished!'})

		def _fulfill_admin(route):
			route.fulfill(status=200, body='ok')

		page.route('**/wizard/installstatus', _fulfill_status)
		page.route('**/admin/reboot', _fulfill_admin)
		page.route('**/admin/restart', _fulfill_admin)
		page.goto(f'{base_url}/test-only/wizard-finish', wait_until='networkidle')

	def test_percent_142_shows_modal_instead_of_auto_redirecting(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=142)

		page.wait_for_selector('#rebootModal.show', timeout=3000)
		assert page.url.endswith('/test-only/wizard-finish'), 'must not auto-navigate away when a reboot is required'

	def test_percent_142_restart_services_button_navigates_to_admin_restart(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=142)
		page.wait_for_selector('#rebootModal.show', timeout=3000)

		page.click('#restartServicesBtn')
		page.wait_for_url('**/admin/restart', timeout=3000)

	def test_percent_142_reboot_now_button_navigates_to_admin_reboot(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=142)
		page.wait_for_selector('#rebootModal.show', timeout=3000)

		page.click('#rebootNowBtn')
		page.wait_for_url('**/admin/reboot', timeout=3000)

	def test_percent_101_still_auto_redirects_with_no_modal(self, live_server, page):
		self._goto_with_mocked_status(page, live_server, percent=101)

		page.wait_for_url('**/admin/restart', timeout=3000)
		assert not page.evaluate("document.querySelector('#rebootModal')?.classList.contains('show')")
```

- [ ] **Step 2: Run the static-render tests to verify they fail**

Run: `uv run pytest tests/test_wizard_finish_reboot_modal.py -v -k "not TestRebootModalInteraction"`
Expected: FAIL — current template has no `rebootModal` markup

- [ ] **Step 3: Add the modal markup and JS to `wizard-finish.html`**

Replace the `{% block content %}...{% endblock %}` section (currently lines 7-37) — keep the existing progress card, add the modal after it:

```html
{% block content %}
<div class="container">

	<!-- Output Window/Textbox -->
	<div class="card shadow">
		<div class="card-body text-center">
			<br><br><br>
			<H2 id="status">Starting Install...</H2>
			<br>
			<div class="progress">
				<div id="percent" class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" aria-valuenow="2" aria-valuemin="0" aria-valuemax="100" style="height: 40px; width: 0%"></div>
			</div>
			<br><br><br>
			<i style="font:small">This operation may take several minutes.</i>
			<br> 
			<button class="btn btn-outline-primary" type="button" data-toggle="collapse" data-target="#collapseOutput" aria-expanded="false" aria-controls="collapseOutput">
				<i class="fas fa-angle-down"></i>&nbsp; Show Output
			</button>
		</div>
	</div>
	<br>
	<br>
	<div class="collapse" id="collapseOutput">
		<div class="card card-body" style="font-family:courier;">
			<div class="form-group">
				<textarea class="form-control" id="installOutput" rows="10" readonly></textarea>
			</div>
		</div>
	</div>
</div>

<!-- Reboot Required Modal -->
<div class="modal fade" id="rebootModal" data-backdrop="static" data-keyboard="false" tabindex="-1" aria-labelledby="rebootModalLabel" aria-hidden="true">
	<div class="modal-dialog">
	  <div class="modal-content">
		<div class="modal-header">
		  <h5 class="modal-title" id="rebootModalLabel"><b>Reboot Recommended</b></h5>
		</div>
		<div class="modal-body">
			<strong>Some of the changes you made (e.g. GPIO/I2C/SPI/1-Wire configuration) need a full system reboot to take effect.</strong><br><br>
			You can reboot now, or just restart the PiFire services instead -- but any hardware-level changes won't be active until you reboot manually later.
		</div>
		<div class="modal-footer">
		  <button type="button" class="btn btn-secondary" id="restartServicesBtn">Restart Services Only</button>
		  <button type="button" class="btn btn-warning" id="rebootNowBtn">Reboot Now</button>
		</div>
	  </div>
	</div>
  </div>
{% endblock %} 
```

Replace the `{% block scripts %}...{% endblock %}` section (currently lines 42-80):

```html
{% block scripts %} 
<script>

// On Document Ready
$(document).ready(function() {
	var output = "";
	installStatus = setInterval(function(){
		// Get Dash Data
			req = $.ajax({
				url : '/wizard/installstatus',
				type : 'GET'
			});

			req.done(function(data) {
				// Update Status
				$('#status').html(data.status);
				document.getElementById("percent").style.width = data.percent + "%";
				if(output != data.output) {
					var textArea = document.getElementById("installOutput");
					textArea.value +=  data.output + '\r\n';
                    textArea.scrollTop = textArea.scrollHeight;
					//$('#installOutput').append(data.output);
					output = data.output;
				}
				if (data.percent > 100) {
					clearInterval(installStatus);
					setTimeout(function() {console.log('Done!')}, 5000);  // 2-second delay
					if (data.percent == 142) {
						$('#rebootModal').modal('show');  // Let the user choose reboot vs. restart-only
					} else {
						location.href = '/admin/restart';  // Server Restart
					}
				};
			});
		}, 250); // Update every 0.25 second
});

$('#rebootNowBtn').on('click', function() {
	location.href = '/admin/reboot';  // Server Reboot
});

$('#restartServicesBtn').on('click', function() {
	location.href = '/admin/restart';  // Server Restart
});

</script>
{% endblock %}
```

- [ ] **Step 4: Run the static-render tests to verify they pass**

Run: `uv run pytest tests/test_wizard_finish_reboot_modal.py -v -k "not TestRebootModalInteraction"`
Expected: PASS

- [ ] **Step 5: Install Chromium if needed, then run the Playwright tests**

Run: `uv run playwright install chromium` (one-time, if not already installed)
Run: `uv run pytest tests/test_wizard_finish_reboot_modal.py -v -k TestRebootModalInteraction`
Expected: PASS (4 tests). If Chromium truly cannot be installed in this environment, confirm the whole class is cleanly skipped (not errored) with the printed skip reason.

- [ ] **Step 6: Commit**

```bash
git add blueprints/wizard/templates/wizard/wizard-finish.html tests/test_wizard_finish_reboot_modal.py
git commit -m "$(cat <<'EOF'
feat(wizard): make the post-install reboot optional

When the wizard's dynamically-computed reboot flag is set (percent ==
142), show a modal offering "Reboot Now" or "Restart Services Only"
instead of silently redirecting straight to /admin/reboot. The
restart-only path already existed (/admin/restart -> restart_scripts())
and now becomes user-selectable instead of implicit.
EOF
)"
```

---

## Task 7: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -v`
Expected: PASS (no regressions in any pre-existing test)

- [ ] **Step 2: Confirm no real system-mutating command was ever invoked**

Run: `git log --oneline -8` and re-read the diffs of the two shell-touching tests (`test_ds18b20_reboot_sentinel.py`) to double check the `sudo` stub is always on `PATH` before the real `raspi-config`/`sudo` could resolve, and that no test in `test_board_config_reboot.py` writes outside `tmp_path`.

- [ ] **Step 3: Manual/hardware follow-up note (not automatable here)**

Confirm on real Raspberry Pi hardware, before merging to a production install:
- `raspi-config nonint do_onewire 0` actually writes `dtoverlay=w1-gpio` (no explicit pin) to config.txt, matching the grep pattern in `wizard/ds18b20.sh` — this assumption can't be verified in this dev environment (no `raspi-config` binary, no real `/boot`).
- The wizard's end-to-end flow (select a GPIO-based module → finish → see the reboot modal → click "Restart Services Only" → confirm the hardware overlay is *not* yet active until a manual reboot, as the modal copy warns).
