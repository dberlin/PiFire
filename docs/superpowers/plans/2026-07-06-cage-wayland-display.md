# Run Qt Displays Under Cage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Launch the display process inside the `cage` Wayland kiosk compositor only when the configured display is a QtQuick backend; launch bare (unchanged) otherwise.

**Architecture:** A thin launcher shim (`display_launch.py`) that supervisor calls instead of `display_process.py`. It reads the configured display module name, and `os.execvp`s into either `cage -s -- python display_process.py` (Qt backends) or `python display_process.py` (everything else). Installers add `cage` + `seatd` and seat-group membership.

**Tech Stack:** Python 3, supervisor, cage (wlroots kiosk compositor), seatd, PySide6 (Qt, in the child only).

## Global Constraints

- Detection rule: a display is a Qt backend **iff** `settings['modules']['display']` starts with `qtquick_`. Plain string check — never import the display module (PySide6 must not load in the display parent process).
- The launcher lives at repo root and imports nothing from `display/`.
- Use `os.execvp` (not `subprocess`) so supervisor supervises one process and `stopasgroup=true` still works.
- `XDG_RUNTIME_DIR`: respect an existing value; else default to `/run/user/<uid>` (root → `/run/user/0`); create it `0700` if missing.
- Only the Qt path sets `QT_QPA_PLATFORM=wayland` / `XDG_RUNTIME_DIR`; the bare path leaves the environment untouched.
- Run `ruff format` on any changed Python file before committing (standing repo rule).
- Tabs for indentation in Python (match existing files).

---

### Task 1: Launcher shim `display_launch.py`

**Files:**
- Create: `display_launch.py`
- Test: `tests/test_display_launch.py`

**Interfaces:**
- Consumes: `common.read_settings()` (existing; returns the settings dict).
- Produces:
  - `build_launch_argv(settings: dict, env: Mapping) -> (list[str], dict[str, str])` — pure function returning the exec argv and a dict of env vars to set (empty on the bare path). Does not mutate `env`.
  - `main() -> None` — reads settings, ensures the runtime dir, updates `os.environ`, `execvp`s.

- [ ] **Step 1: Write the failing test**

Create `tests/test_display_launch.py`:

```python
import sys

import display_launch


def test_bare_for_spi_display():
	settings = {'modules': {'display': 'st7789_240x320'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert argv == [sys.executable, 'display_process.py']
	assert env_updates == {}


def test_bare_for_none_display():
	settings = {'modules': {'display': 'none'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert argv == [sys.executable, 'display_process.py']
	assert env_updates == {}


def test_bare_for_pygame_display():
	settings = {'modules': {'display': 'dsi_800x480t'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert argv == [sys.executable, 'display_process.py']
	assert env_updates == {}


def test_cage_for_qtquick_flex():
	settings = {'modules': {'display': 'qtquick_flex'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {'XDG_RUNTIME_DIR': '/run/user/1000'})
	assert argv == ['cage', '-s', '--', sys.executable, 'display_process.py']
	assert env_updates['QT_QPA_PLATFORM'] == 'wayland'
	assert env_updates['XDG_RUNTIME_DIR'] == '/run/user/1000'


def test_cage_for_qtquick_dsi():
	settings = {'modules': {'display': 'qtquick_dsi_1280x720t'}}
	argv, _env = display_launch.build_launch_argv(settings, {})
	assert argv == ['cage', '-s', '--', sys.executable, 'display_process.py']


def test_xdg_runtime_dir_preserved_when_set():
	settings = {'modules': {'display': 'qtquick_flex'}}
	_argv, env_updates = display_launch.build_launch_argv(settings, {'XDG_RUNTIME_DIR': '/custom/run'})
	assert env_updates['XDG_RUNTIME_DIR'] == '/custom/run'


def test_xdg_runtime_dir_defaults_to_run_user_uid(monkeypatch):
	monkeypatch.setattr(display_launch.os, 'getuid', lambda: 0)
	settings = {'modules': {'display': 'qtquick_flex'}}
	_argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert env_updates['XDG_RUNTIME_DIR'] == '/run/user/0'


def test_build_launch_argv_does_not_mutate_env():
	settings = {'modules': {'display': 'qtquick_flex'}}
	env = {'XDG_RUNTIME_DIR': '/run/user/1000'}
	display_launch.build_launch_argv(settings, env)
	assert env == {'XDG_RUNTIME_DIR': '/run/user/1000'}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_display_launch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'display_launch'`.

- [ ] **Step 3: Write minimal implementation**

Create `display_launch.py`:

```python
#!/usr/bin/env python3
"""PiFire display launcher -- run the display process inside a Wayland kiosk.

The QtQuick display backends (module names starting with ``qtquick_``) build a
QGuiApplication and need a real Wayland session to render onto. The SPI and
pygame displays draw straight to hardware / the framebuffer and need no
compositor. Supervisor calls this shim instead of display_process.py directly:
it decides which case applies and ``execvp``s into the right command, so
supervisor keeps supervising a single process and ``stopasgroup`` still tears
everything down.

This file lives at the repo root (not under display/, a package) and imports
nothing from display/, preserving the invariant that PySide6 is never loaded in
the display parent process.
"""

import logging
import os
import sys

from common import read_settings


def build_launch_argv(settings, env):
	"""Return (argv, env_updates) for launching the display process.

	:param settings: settings dict (uses settings['modules']['display'])
	:param env: current environment mapping; read-only, never mutated
	:return: (argv, env_updates). argv is the exec argv list; env_updates is a
		dict of environment variables to set before exec (empty on the bare
		path).
	"""
	child = [sys.executable, 'display_process.py']
	display_name = settings['modules']['display']
	if not display_name.startswith('qtquick_'):
		return child, {}

	runtime_dir = env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
	env_updates = {'QT_QPA_PLATFORM': 'wayland', 'XDG_RUNTIME_DIR': runtime_dir}
	return ['cage', '-s', '--', *child], env_updates


def _ensure_runtime_dir(path):
	"""Create XDG_RUNTIME_DIR 0700 if it does not already exist."""
	os.makedirs(path, mode=0o700, exist_ok=True)
	os.chmod(path, 0o700)


def main():
	settings = read_settings()
	argv, env_updates = build_launch_argv(settings, os.environ)
	if 'XDG_RUNTIME_DIR' in env_updates:
		_ensure_runtime_dir(env_updates['XDG_RUNTIME_DIR'])
	os.environ.update(env_updates)
	try:
		os.execvp(argv[0], argv)
	except OSError:
		logging.basicConfig()
		logging.getLogger('display_launch').exception('Failed to exec: %s', ' '.join(argv))
		sys.exit(1)


if __name__ == '__main__':
	main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_display_launch.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Format**

Run: `.venv/bin/ruff format display_launch.py tests/test_display_launch.py`
Expected: files left unchanged or reformatted; no errors.

- [ ] **Step 6: Commit**

```bash
git add display_launch.py tests/test_display_launch.py
git commit -F - <<'EOF'
feat(display): cage launcher shim for QtQuick backends

Wrap display_process.py in `cage -s --` with QT_QPA_PLATFORM=wayland only
when the configured display module name starts with qtquick_; launch bare
otherwise. execvp keeps supervisor supervising one process.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 2: Point supervisor at the launcher

**Files:**
- Modify: `auto-install/supervisor/display.conf:2`
- Modify: `auto-install/supervisor/legacy/display.conf:2`

**Interfaces:**
- Consumes: `display_launch.py` from Task 1 (at `/usr/local/bin/pifire/display_launch.py`).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Edit `display.conf`**

Change the `command=` line (venv interpreter) from:

```ini
command=/usr/local/bin/pifire/.venv/bin/python /usr/local/bin/pifire/display_process.py
```

to:

```ini
command=/usr/local/bin/pifire/.venv/bin/python /usr/local/bin/pifire/display_launch.py
```

- [ ] **Step 2: Edit `legacy/display.conf`**

Change the `command=` line (non-venv `bin/python`) from:

```ini
command=/usr/local/bin/pifire/bin/python /usr/local/bin/pifire/display_process.py
```

to:

```ini
command=/usr/local/bin/pifire/bin/python /usr/local/bin/pifire/display_launch.py
```

- [ ] **Step 3: Verify no other references to the old direct command remain**

Run: `grep -rn "display_process.py" auto-install/`
Expected: no matches in the supervisor `.conf` files (only `display_launch.py` now). Other references (docs/scripts) are fine.

- [ ] **Step 4: Commit**

```bash
git add auto-install/supervisor/display.conf auto-install/supervisor/legacy/display.conf
git commit -F - <<'EOF'
feat(install): supervisor launches display via cage launcher shim

Point display.conf (and the legacy variant) at display_launch.py so the
QtQuick backends run under cage; non-Qt displays are unchanged (bare).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 3: Installers — cage, seatd, seat groups (all three)

**Files:**
- Modify: `auto-install/install.sh` (apt install line ~L198; group block ~L249)
- Modify: `auto-install/pifire-dietpi.sh` (apt install line ~L127; group/interface block ~L158-170)
- Modify: `auto-install/install-fedora.sh` (dnf block ~L119-124; group block ~L169)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing consumed by later tasks.

**Shared helper snippet** (used in all three, adapt the logging tail per script). Adding a user to a non-existent group makes `usermod` fail, so guard each group. `cage` reads `seat` (created by the seatd package), plus `video`/`input`/`render` for DRM/input:

```bash
# Seat access for the cage Wayland compositor (QtQuick displays).
$SUDO systemctl enable --now seatd
for grp in video input render seat; do
    $SUDO usermod -a -G "$grp" "$USER" 2>/dev/null || true
    $SUDO usermod -a -G "$grp" root 2>/dev/null || true
done
```

- [ ] **Step 1: `install.sh` — add packages**

In the apt install line (~L198), add `cage seatd` before the trailing `-y`:

```bash
$SUDO apt install python3-dev python3-pip python3-venv python3-scipy nginx git supervisor ttf-mscorefonts-installer valkey-server gfortran libopenblas-dev liblapack-dev libopenjp2-7 libglib2.0-dev bluetooth bluez cage seatd -y 2>&1 | tee -a ~/logs/pifire_install.log
```

- [ ] **Step 2: `install.sh` — enable seatd + groups**

Immediately after the existing group block (after `$SUDO usermod -a -G pifire root` at ~L251), insert:

```bash
# Seat access for the cage Wayland compositor (QtQuick displays).
$SUDO systemctl enable --now seatd 2>&1 | tee -a ~/logs/pifire_install.log
for grp in video input render seat; do
    $SUDO usermod -a -G "$grp" $USER 2>/dev/null || true
    $SUDO usermod -a -G "$grp" root 2>/dev/null || true
done
```

- [ ] **Step 3: `pifire-dietpi.sh` — add packages**

In the apt install line (~L127), add `cage seatd` before the trailing `-y`:

```bash
$SUDO apt install python3-dev python3-pip python3-venv python3-scipy python3-rpi-lgpio build-essential nginx git supervisor ttf-mscorefonts-installer valkey-server gfortran libatlas-base-dev libopenblas-dev liblapack-dev libopenjp2-7 libglib2.0-dev bluez bluez-firmware libnss-mdns cage seatd -y 2>&1 | tee -a ~/logs/pifire_install.log
```

- [ ] **Step 4: `pifire-dietpi.sh` — enable seatd + groups**

After the interface-permissions block (after `$SUDO adduser $USER i2c` at ~L170), insert:

```bash
# Seat access for the cage Wayland compositor (QtQuick displays).
$SUDO systemctl enable --now seatd 2>&1 | tee -a ~/logs/pifire_install.log
for grp in video input render seat; do
    $SUDO usermod -a -G "$grp" $USER 2>/dev/null || true
    $SUDO usermod -a -G "$grp" root 2>/dev/null || true
done
```

- [ ] **Step 5: `install-fedora.sh` — add packages**

In the dnf install block (~L119-124), add `cage seatd` to the package list (e.g. on the `nginx git supervisor valkey` line):

```bash
    nginx git supervisor valkey cage seatd \
```

- [ ] **Step 6: `install-fedora.sh` — enable seatd + groups**

After the group block (after `$SUDO usermod -a -G pifire root` at ~L171), insert (matching this script's `tee -a "$LOG"` / `log` style):

```bash
# Seat access for the cage Wayland compositor (QtQuick displays).
$SUDO systemctl enable --now seatd 2>&1 | tee -a "$LOG" || log " ! seatd not enabled (continuing)."
for grp in video input render seat; do
    $SUDO usermod -a -G "$grp" "$USER" 2>/dev/null || true
    $SUDO usermod -a -G "$grp" root 2>/dev/null || true
done
```

- [ ] **Step 7: Syntax-check all three scripts**

Run: `bash -n auto-install/install.sh && bash -n auto-install/pifire-dietpi.sh && bash -n auto-install/install-fedora.sh && echo OK`
Expected: `OK` (no syntax errors).

- [ ] **Step 8: Commit**

```bash
git add auto-install/install.sh auto-install/pifire-dietpi.sh auto-install/install-fedora.sh
git commit -F - <<'EOF'
feat(install): install cage + seatd and grant seat access

Add the cage kiosk compositor and seatd to all three installers, enable
seatd, and add the pifire user + root to the video/input/render/seat
groups so the QtQuick display can run under cage.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Notes for the implementer

- Run pytest via the project venv: `.venv/bin/python -m pytest`.
- Do not spawn cage or Qt in any test — Task 1's tests only pin argv/env construction.
- The tests import `display_launch` from the repo root; run pytest from the repo root so `common` and `display_launch` resolve.
