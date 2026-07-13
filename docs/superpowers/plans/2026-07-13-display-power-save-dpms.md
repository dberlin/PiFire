# Display power-save (cage output DPMS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Power the display off/on under cage after a user-configurable idle timeout, driven by PiFire's existing cook-aware idle machine.

**Architecture:** A new `ScreenPowerController` (keyed on display kind) runs `wlr-randr --output <auto-resolved> --off/--on`; only the `wayland` branch is implemented. The Qt backend's existing `asleep` state machine (which already refuses to sleep during a cook) fires it, and the idle timeout becomes a global `settings['display']['sleep_timeout']` read live (~1 Hz) by the Qt path and at init by the pygame flex path, surfaced on the settings page.

**Tech Stack:** Python 3.14, PySide6/QtQuick, Flask (settings blueprint), `wlr-randr`, cage (wlroots), pytest, uv.

## Global Constraints

- Run tests with: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <paths>` (bare `python` gives false failures).
- Run `uvx ruff format` on every changed `.py` file before each commit (repo standing rule).
- `sleep_timeout` default is **300** seconds; **0 = never sleep**; negatives clamp to 0.
- Never hardcode the output name — resolve it via `wlr-randr` at runtime.
- Do **not** implement the non-`wayland` `ScreenPowerController` branches; they are safe no-ops.
- Commit messages via `git commit -F <file>` or heredoc (backticks in `-m` get mangled by zsh); end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.
- Work happens in the existing worktree `.claude/worktrees/screen-power-save` (branch `screen-power-save`).

---

### Task 1: Global `sleep_timeout` setting + accessor

**Files:**
- Modify: `common/common.py:217` (the `settings['display'] = {...}` default block)
- Modify: `common/common.py` (add `display_sleep_timeout` accessor near other read helpers)
- Test: `tests/test_display_sleep_timeout.py` (new)

**Interfaces:**
- Produces: `common.common.display_sleep_timeout(settings) -> int` (seconds; 0 = never; default 300 on missing/invalid). Consumed by Tasks 3 (via qtapp) and 5.
- Produces: `default_settings()['display']['sleep_timeout'] == 300`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_display_sleep_timeout.py
from common.common import default_settings, display_sleep_timeout


def test_default_settings_has_sleep_timeout():
    assert default_settings()['display']['sleep_timeout'] == 300


def test_accessor_reads_value():
    assert display_sleep_timeout({'display': {'sleep_timeout': 45}}) == 45


def test_accessor_zero_means_never():
    assert display_sleep_timeout({'display': {'sleep_timeout': 0}}) == 0


def test_accessor_missing_defaults_to_300():
    assert display_sleep_timeout({'display': {}}) == 300
    assert display_sleep_timeout({}) == 300


def test_accessor_negative_clamps_to_zero():
    assert display_sleep_timeout({'display': {'sleep_timeout': -5}}) == 0


def test_accessor_non_numeric_defaults_to_300():
    assert display_sleep_timeout({'display': {'sleep_timeout': 'x'}}) == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_display_sleep_timeout.py -v`
Expected: FAIL — `ImportError: cannot import name 'display_sleep_timeout'` (and default missing the key).

- [ ] **Step 3: Add the default key**

In `common/common.py`, change the display default block (currently at ~L217):

```python
	settings['display'] = {'selected': 'none', 'sleep_timeout': 300}
	settings['display']['config'] = _default_display_config()
```

- [ ] **Step 4: Add the accessor**

Add this function at module level in `common/common.py` (place it near the other `read_*` settings helpers):

```python
def display_sleep_timeout(settings):
	"""Idle seconds before the display sleeps; 0 = never. Defaults to 300 on
	missing/invalid values. Negative values clamp to 0."""
	try:
		value = int(settings['display']['sleep_timeout'])
	except (KeyError, TypeError, ValueError):
		return 300
	return value if value > 0 else 0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_display_sleep_timeout.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Format + commit**

```bash
uvx ruff format common/common.py tests/test_display_sleep_timeout.py
git add common/common.py tests/test_display_sleep_timeout.py
git commit -F - <<'EOF'
feat(settings): global display sleep_timeout (default 300, 0=never)

Adds settings['display']['sleep_timeout'] and a display_sleep_timeout()
accessor; existing installs pick up the new key via the settings overlay.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 2: `ScreenPowerController` (wayland output DPMS)

**Files:**
- Create: `display/screen_power.py`
- Test: `tests/test_screen_power.py` (new)

**Interfaces:**
- Produces: `display.screen_power.ScreenPowerController(display_kind, run=subprocess.run)` with
  `.resolve_output() -> str | None` and `.set_output_power(on: bool) -> None`. Consumed by Task 4.
- `run` matches `subprocess.run(args, capture_output=True, text=True, timeout=5)` and returns an object with `.stdout`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_screen_power.py
import subprocess

from display.screen_power import ScreenPowerController

WLR_SAMPLE = (
	'DP-1 "Dell Inc. DELL 24"\n'
	'  Enabled: yes\n'
	'  Modes:\n'
	'    1280x720 px, 60.000000 Hz\n'
)


class FakeRun:
	def __init__(self, stdout='', raises=None):
		self.stdout_text = stdout
		self.raises = raises
		self.calls = []

	def __call__(self, args, **kwargs):
		self.calls.append(args)
		if self.raises:
			raise self.raises
		return subprocess.CompletedProcess(args, 0, stdout=self.stdout_text, stderr='')


def test_resolve_output_parses_name():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	assert c.resolve_output() == 'DP-1'
	assert run.calls[0] == ['wlr-randr']


def test_resolve_output_caches():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	c.resolve_output()
	c.resolve_output()
	assert sum(1 for a in run.calls if a == ['wlr-randr']) == 1


def test_set_output_power_off_argv():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	c.set_output_power(False)
	assert ['wlr-randr', '--output', 'DP-1', '--off'] in run.calls


def test_set_output_power_on_argv():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	c.set_output_power(True)
	assert ['wlr-randr', '--output', 'DP-1', '--on'] in run.calls


def test_missing_binary_is_safe():
	run = FakeRun(raises=FileNotFoundError())
	c = ScreenPowerController('wayland', run=run)
	assert c.resolve_output() is None
	c.set_output_power(False)  # must not raise


def test_non_wayland_is_noop():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('sdl', run=run)
	assert c.resolve_output() is None
	c.set_output_power(False)
	assert run.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_screen_power.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'display.screen_power'`.

- [ ] **Step 3: Write the implementation**

```python
# display/screen_power.py
"""Screen power control keyed on display kind.

Only the ``wayland`` kind is implemented: it drives ``wlr-randr`` to power the
compositor output off/on (cage supports zwlr_output_manager_v1). Other kinds are
safe no-ops so callers can construct and drive a controller unconditionally.
"""

import logging
import subprocess

log = logging.getLogger('screen_power')


class ScreenPowerController:
	def __init__(self, display_kind, run=subprocess.run):
		self._kind = display_kind
		self._run = run
		self._output = None

	def resolve_output(self):
		"""Return the compositor output name (cached), or None if unavailable."""
		if self._kind != 'wayland':
			return None
		if self._output:
			return self._output
		try:
			proc = self._run(['wlr-randr'], capture_output=True, text=True, timeout=5)
		except (OSError, subprocess.SubprocessError):
			log.exception('wlr-randr failed to run')
			return None
		self._output = self._parse_output_name(proc.stdout)
		return self._output

	@staticmethod
	def _parse_output_name(text):
		# wlr-randr prints each head starting at column 0: `DP-1 "..."`;
		# indented lines are that head's properties. Take the first head.
		for line in text.splitlines():
			if line and not line[0].isspace():
				return line.split()[0]
		return None

	def set_output_power(self, on):
		"""Power the output on (True) or off (False). No-op if not wayland or
		no output could be resolved. Never raises into the caller."""
		if self._kind != 'wayland':
			return
		name = self.resolve_output()
		if not name:
			return
		flag = '--on' if on else '--off'
		try:
			self._run(['wlr-randr', '--output', name, flag], capture_output=True, text=True, timeout=5)
		except (OSError, subprocess.SubprocessError):
			log.exception('wlr-randr power toggle failed')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_screen_power.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Format + commit**

```bash
uvx ruff format display/screen_power.py tests/test_screen_power.py
git add display/screen_power.py tests/test_screen_power.py
git commit -F - <<'EOF'
feat(display): ScreenPowerController drives wlr-randr output DPMS

Wayland-kind controller resolves the cage output via wlr-randr and powers
it off/on; other display kinds are no-ops. Injectable runner for tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 3: Qt backend reads `sleep_timeout` (init + live ~1 Hz), 0 disables

**Files:**
- Modify: `display/qtbackend.py` (`__init__` ~L85-126; poll settings-recheck ~L170-172; `_update_idle` ~L206-212)
- Modify: `display/qtapp.py` (`build_backend` ~L45-62)
- Test: `tests/test_qtbackend.py` (extend; fix existing `test_sleep_wake_state_machine`)

**Interfaces:**
- Consumes: `common.common.display_sleep_timeout` (Task 1).
- Produces: `PiFireBackend(fetch_fn, command_fn, probe_info, accent_fn=None, timeout_fn=None, parent=None)`; `self.TIMEOUT` seeded from `timeout_fn()` at init and refreshed on the ~1 Hz settings recheck; `_update_idle` never sleeps when `TIMEOUT <= 0`.

- [ ] **Step 1: Write the failing tests (add to `tests/test_qtbackend.py`)**

```python
def test_timeout_seeded_from_timeout_fn():
	b = PiFireBackend(
		lambda: ({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}}),
		lambda c, d: None,
		{'primary': {'name': 'Grill'}, 'food': [], 'aux': []},
		timeout_fn=lambda: 42,
	)
	assert b.TIMEOUT == 42


def test_zero_timeout_never_sleeps():
	clock = {'t': 1000.0}
	b = PiFireBackend(
		lambda: ({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}}),
		lambda c, d: None,
		{'primary': {'name': 'Grill'}, 'food': [], 'aux': []},
		timeout_fn=lambda: 0,
	)
	b._now = lambda: clock['t']
	b._last_interaction = clock['t']
	clock['t'] = 999999.0
	b.poll()
	assert b.asleep is False


def test_timeout_live_reread():
	clock = {'t': 1000.0}
	state = {'timeout': 30}
	b = PiFireBackend(
		lambda: ({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}}),
		lambda c, d: None,
		{'primary': {'name': 'Grill'}, 'food': [], 'aux': []},
		timeout_fn=lambda: state['timeout'],
	)
	b._now = lambda: clock['t']
	state['timeout'] = 5
	clock['t'] = 1002.0  # >1s since last settings check -> re-read
	b.poll()
	assert b.TIMEOUT == 5
```

- [ ] **Step 2: Fix the existing `test_sleep_wake_state_machine`**

That test relies on the old default `TIMEOUT = 10`. Add one line right after the `PiFireBackend(...)` construction and before the first `b.poll()`:

```python
	b.TIMEOUT = 10
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_qtbackend.py -v`
Expected: FAIL — new tests error on unexpected `timeout_fn` kwarg / `TIMEOUT` defaults to 10 not 300.

- [ ] **Step 4: Add `timeout_fn` + default 300 in `__init__`**

In `display/qtbackend.py`, change the constructor signature:

```python
	def __init__(self, fetch_fn, command_fn, probe_info, accent_fn=None, timeout_fn=None, parent=None):
```

Just below `self._accent_fn = accent_fn` (~L91) add:

```python
		self._timeout_fn = timeout_fn
```

Rename the recheck timestamp field for clarity — change `self._last_accent_check = 0.0` (~L93) to:

```python
		self._last_settings_check = 0.0
```

Change the idle-state defaults block (~L123-126) so `TIMEOUT` defaults to 300 and honors `timeout_fn` at init:

```python
		# Idle / sleep state
		self.TIMEOUT = self._timeout_fn() if self._timeout_fn is not None else 300
		self._last_interaction = self._now()
		self._asleep = False
```

- [ ] **Step 5: Re-read timeout on the ~1 Hz settings recheck in `poll`**

Replace the accent recheck block (~L170-172) with a combined settings recheck:

```python
		if (now - self._last_settings_check) >= 1.0:
			self._last_settings_check = now
			if self._accent_fn is not None:
				self._set('_accent_theme', self._accent_fn() or 'Ember', self.accentThemeChanged)
			if self._timeout_fn is not None:
				self.TIMEOUT = self._timeout_fn()
```

- [ ] **Step 6: Make `_update_idle` honor 0 = never**

Change `_update_idle` (~L206-212):

```python
	def _update_idle(self, mode, now):
		# The screen never sleeps during an active cook; in Stop it sleeps after
		# TIMEOUT seconds of no interaction (TIMEOUT <= 0 disables sleeping).
		# Leaving Stop auto-wakes.
		if mode != 'Stop':
			self._set('_asleep', False, self.asleepChanged)
		elif self.TIMEOUT > 0 and now - self._last_interaction > self.TIMEOUT:
			self._set('_asleep', True, self.asleepChanged)
```

- [ ] **Step 7: Pass a `timeout_fn` from `build_backend`**

In `display/qtapp.py`, `build_backend`, extend the imports and add a timeout reader. Change the `from common import ...` line inside `build_backend` to include the accessor and add `_timeout_fn`:

```python
	from common import read_settings_store
	from common.common import display_sleep_timeout

	def _accent_fn():
		try:
			s = read_settings_store()
			module = s['modules']['display']
			return s['display']['config'][module].get('accent_theme', 'Ember')
		except Exception:
			return 'Ember'

	def _timeout_fn():
		try:
			return display_sleep_timeout(read_settings_store())
		except Exception:
			return 300

	dispatcher = Display.for_dispatch(config, config.get('units', 'F'))
	backend = PiFireBackend(
		_fetch,
		dispatcher._dispatch_command,
		config.get('probe_info', {}),
		accent_fn=_accent_fn,
		timeout_fn=_timeout_fn,
	)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_qtbackend.py -v`
Expected: PASS (existing + 3 new).

- [ ] **Step 9: Format + commit**

```bash
uvx ruff format display/qtbackend.py display/qtapp.py tests/test_qtbackend.py
git add display/qtbackend.py display/qtapp.py tests/test_qtbackend.py
git commit -F - <<'EOF'
feat(display): Qt idle timeout from sleep_timeout setting, live re-read

TIMEOUT is seeded from a timeout_fn at init and refreshed on the existing
~1Hz settings recheck; 0 disables sleeping. build_backend feeds it from
settings['display']['sleep_timeout'].

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 4: Wire `ScreenPowerController` to the Qt sleep signal

**Files:**
- Modify: `display/qtapp.py` (`run_app` ~L90-122; add `bind_backend_power` helper)
- Test: `tests/test_qtapp_power.py` (new)

**Interfaces:**
- Consumes: `ScreenPowerController` (Task 2), a backend exposing `asleep` and `asleepChanged.connect`.
- Produces: `display.qtapp.bind_backend_power(backend, controller) -> callable` — connects the sleep signal to `controller.set_output_power(not backend.asleep)` and applies once immediately.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qtapp_power.py
from display.qtapp import bind_backend_power


class FakeSignal:
	def __init__(self):
		self._cbs = []

	def connect(self, cb):
		self._cbs.append(cb)

	def emit(self):
		for cb in self._cbs:
			cb()


class FakeBackend:
	def __init__(self):
		self.asleep = False
		self.asleepChanged = FakeSignal()


class FakeController:
	def __init__(self):
		self.calls = []

	def set_output_power(self, on):
		self.calls.append(on)


def test_applies_once_on_bind_awake():
	b, c = FakeBackend(), FakeController()
	bind_backend_power(b, c)
	assert c.calls == [True]  # not asleep -> power on


def test_sleep_then_wake_toggles_power():
	b, c = FakeBackend(), FakeController()
	bind_backend_power(b, c)
	b.asleep = True
	b.asleepChanged.emit()
	b.asleep = False
	b.asleepChanged.emit()
	assert c.calls == [True, False, True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_qtapp_power.py -v`
Expected: FAIL — `ImportError: cannot import name 'bind_backend_power'`.

- [ ] **Step 3: Add the helper and use it in `run_app`**

In `display/qtapp.py`, add the helper at module level (near `_make_backlight`):

```python
def bind_backend_power(backend, controller):
	"""Drive the screen-power controller from the backend's asleep signal.
	Applies once immediately and returns the apply callable."""

	def _apply():
		controller.set_output_power(not backend.asleep)

	backend.asleepChanged.connect(_apply)
	_apply()
	return _apply
```

Add the import at the top of `display/qtapp.py`:

```python
from display.screen_power import ScreenPowerController
```

In `run_app`, right after the existing `backend.asleepChanged.connect(_apply_backlight)` / `_apply_backlight()` lines (~L113-114), add:

```python
	screen_power = ScreenPowerController('wayland')
	bind_backend_power(backend, screen_power)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_qtapp_power.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Format + commit**

```bash
uvx ruff format display/qtapp.py tests/test_qtapp_power.py
git add display/qtapp.py tests/test_qtapp_power.py
git commit -F - <<'EOF'
feat(display): power the cage output off/on from the Qt sleep signal

run_app binds a wayland ScreenPowerController to backend.asleepChanged, so
the cook-aware idle machine powers the DP output down on sleep and back up
on wake (alongside the existing backlight toggle).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 5: Pygame flex path reads `sleep_timeout` at init

**Files:**
- Modify: `display/base_flex.py` (import list ~L31; `self.TIMEOUT = 10` ~L81)

**Interfaces:**
- Consumes: `common.display_sleep_timeout` (Task 1). No new produced interface.

- [ ] **Step 1: Import the accessor**

In `display/base_flex.py`, add `display_sleep_timeout` to the existing `from common import (...)` block (the one that already imports `read_settings` at ~L31):

```python
	display_sleep_timeout,
	read_settings,
```

- [ ] **Step 2: Seed `TIMEOUT` from settings**

Replace `self.TIMEOUT = 10` (~L81) with:

```python
		self.TIMEOUT = display_sleep_timeout(read_settings())
```

- [ ] **Step 3: Verify no regression in the display suite**

This path constructs a pygame `Display` (hardware/framebuffer) that is not unit-instantiable in CI; the timeout logic itself lives in the Task-1 accessor, which is unit-tested. Guard against import/wiring regressions by running the display-related suite:

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -k "display or flex or qtquick or qtbackend or screen_power" -q`
Expected: PASS (no import errors from `base_flex`).

- [ ] **Step 4: Format + commit**

```bash
uvx ruff format display/base_flex.py
git add display/base_flex.py
git commit -F - <<'EOF'
feat(display): flex display idle timeout from sleep_timeout setting

Replaces the hardcoded 10s flex-display TIMEOUT with the global
settings['display']['sleep_timeout'] value.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 6: Settings-page field for the sleep timeout

**Files:**
- Modify: `blueprints/settings/routes.py` (add `action == 'display'` handler after the `dashboard_config` block ~L36)
- Modify: `blueprints/settings/templates/settings/index.html` (add a card in the `#v-pills-dash` pane, before the `<br><br><br>` at ~L1072)
- Test: `tests/test_webapp_sqlite.py` (add one test; reuses its SQLite-seeded app)

**Interfaces:**
- Consumes: the setting from Task 1; `is_not_blank` (already imported in routes), `write_settings` (already imported).

- [ ] **Step 1: Write the failing test (append to `tests/test_webapp_sqlite.py`)**

```python
def test_settings_display_post_sets_sleep_timeout():
	from app import app as flask_app

	client = flask_app.test_client()
	client.post('/settings/display', data={'sleep_timeout': '123'})
	assert read_settings()['display']['sleep_timeout'] == 123


def test_settings_display_post_clamps_negative():
	from app import app as flask_app

	client = flask_app.test_client()
	client.post('/settings/display', data={'sleep_timeout': '-9'})
	assert read_settings()['display']['sleep_timeout'] == 0
```

`read_settings` is already imported at the top of `tests/test_webapp_sqlite.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_webapp_sqlite.py -k sleep_timeout -v`
Expected: FAIL — no `display` action handler, so `sleep_timeout` is never written (stays 300).

- [ ] **Step 3: Add the route handler**

In `blueprints/settings/routes.py`, immediately after the `dashboard_config` handler block (the one that ends ~L36, before the `probe_select` handler), insert:

```python
	if request.method == 'POST' and action == 'display':
		response = request.form
		if is_not_blank(response, 'sleep_timeout'):
			settings['display']['sleep_timeout'] = max(0, int(response['sleep_timeout']))
		write_settings(settings)
```

(No early `return`: it falls through to the shared final `render_template(...)`, matching the `cycle` handler which writes then falls through.)

- [ ] **Step 4: Add the settings-page card**

In `blueprints/settings/templates/settings/index.html`, inside the `#v-pills-dash` pane, insert this block immediately before the `<br><br><br>` line (~L1072, right after the dashboard-selection card's closing `</div><!-- End of Card -->`):

```html
                    <br>
                    <div class="card shadow">
                        <form name="displaypower" action="/settings/display" method="POST">
                            <div class="card-header bg-primary text-white">
                                <h5><i class="fas fa-tools"></i>&nbsp; Screen Power Save</h5>
                            </div>
                            <div class="card-body">
                                <div class="form-group row">
                                    <label for="sleep_timeout" class="col-sm-8 col-form-label">Screen sleep timeout (seconds, 0 = never)</label>
                                    <div class="col-sm-4">
                                        <input id="sleep_timeout" type="number" inputmode="numeric" min="0" step="1" class="form-control" placeholder="{{ settings['display']['sleep_timeout'] }}" value="{{ settings['display']['sleep_timeout'] }}" name="sleep_timeout" style="min-width: 6ch;">
                                    </div>
                                </div>
                                <i class="small">When the grill is stopped and the screen is untouched for this long, the display powers off. It never sleeps during a cook; touch the screen to wake it.</i>
                            </div>
                            <div class="card-footer bg-light">
                                <button type="submit" class="btn btn-outline-danger">Save</button>
                            </div>
                        </form>
                    </div><!-- End of Card -->
```

- [ ] **Step 5: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/test_webapp_sqlite.py -k sleep_timeout -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Format + commit**

```bash
uvx ruff format blueprints/settings/routes.py tests/test_webapp_sqlite.py
git add blueprints/settings/routes.py blueprints/settings/templates/settings/index.html tests/test_webapp_sqlite.py
git commit -F - <<'EOF'
feat(settings-ui): screen sleep timeout field on the Dashboard settings tab

Adds a Screen Power Save card posting to /settings/display, which writes
settings['display']['sleep_timeout'] (clamped >= 0).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 7: Installers add `wlr-randr`

**Files:**
- Modify: `auto-install/install.sh:198`, `auto-install/install-debian.sh:136`, `auto-install/install-fedora.sh:123`, `auto-install/pifire-dietpi.sh:127`

**Interfaces:** none (packaging only).

- [ ] **Step 1: Add `wlr-randr` beside `cage seatd` in each installer**

In each of the four files, change the package token `cage seatd` to `cage seatd wlr-randr` on the line noted above. (In `install-fedora.sh` and `install-debian.sh` the tokens are on a continuation line `... cage seatd \`; in `install.sh` and `pifire-dietpi.sh` they are inline in the `apt install ...` command.)

- [ ] **Step 2: Verify all four updated**

Run: `grep -rn "cage seatd wlr-randr" auto-install/`
Expected: 4 matches (install.sh, install-debian.sh, install-fedora.sh, pifire-dietpi.sh).

- [ ] **Step 3: Commit**

```bash
git add auto-install/install.sh auto-install/install-debian.sh auto-install/install-fedora.sh auto-install/pifire-dietpi.sh
git commit -F - <<'EOF'
build(install): add wlr-randr for cage output power control

wlr-randr is used by ScreenPowerController to power the cage output
off/on for display sleep.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

## Final verification (after all tasks)

- [ ] Run the full suite: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` — expect all green.
- [ ] On the real box: set the timeout in the settings UI to a small value (e.g. 20s), leave the grill in Stop, don't touch the screen, and confirm the DP monitor powers off after the timeout and wakes on touch. Confirm it does **not** sleep during a cook. (Use `/verify` or `/run` to drive the app.)
