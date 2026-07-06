# ThreadedControllerRunner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run an expensive controller's `update()` (the MPC's NLP/net solve, `control_period = 5.0s`) on a background thread so a slow solve never blocks the PiFire control loop's probe reads, max-temp check, or auger/igniter/fan timing.

**Architecture:** A new `ThreadedControllerRunner` implements the existing `ControllerRunner` interface; a per-controller `wants_async()` capability lets `build_runner` pick it (MPC) or `SyncControllerRunner` (everything else). The background thread owns the controller core exclusively and publishes the latest normalized output; `HoldMode` reads that snapshot without blocking and stops the thread at teardown.

**Tech Stack:** Python 3 `threading` (Lock, Event, Thread), pytest.

## Global Constraints

- Run `.venv/bin/ruff format <changed files>` before every commit (config in `pyproject.toml`).
- Tests run under `.venv/bin/python -m pytest`. Full-suite command `.venv/bin/python -m pytest -q` (baseline ~411 passed; a live `valkey-server` on localhost:6379 runs the E2E tier). The branch is shared with concurrent sessions — focus on your own changes, not the absolute test count, and leave any pre-existing `pyproject.toml`/`uv.lock`/thermoworks modifications untouched and out of your commits.
- Commit messages: NO backticks (this is zsh — backticks trigger command substitution and corrupt the message); write the message to a file and use `git commit -F <file>`. End every message with a trailing line: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Follow TDD: failing test first, watch it fail, implement, watch it pass.
- Every test that constructs a `ThreadedControllerRunner` (or a real threaded runner via `build_runner`) MUST call `stop()` on it before the test ends — never leak a thread. Tests synchronize on `threading.Event`, never on wall-clock `sleep`.
- The golden-master oracle `tests/characterization/test_modes_golden.py` must stay UNCHANGED across this whole plan (it injects the synchronous `FakeControllerRunner`; the sync path is behavior-identical). If a golden assertion changes, something is wrong — fix the code, do not edit the golden.
- Interface names are fixed and shared across tasks: `wants_async(self) -> bool`, `stop(self) -> None`, `ThreadedControllerRunner`, `NormalizedOutput(cycle_ratio, fan)`.

---

## Task 1: `wants_async()` capability + uniform `stop()` on the runner interface

**Files:**
- Modify: `controller/base.py` (add `ControllerBase.wants_async`)
- Modify: `controller/mpc.py` (override `wants_async`)
- Modify: `controller/runtime/runner.py` (ABC gains abstract `wants_async` + `stop`; `SyncControllerRunner` implements both)
- Modify: `tests/fakes/runner.py` (`wants_async` kwarg + `stop` no-op)
- Test: `tests/test_mpc_integration.py`, `tests/test_sync_runner.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ControllerBase.wants_async(self) -> bool` (False); `controller.mpc.Controller.wants_async(self) -> bool` (True); `ControllerRunner` ABC abstract `wants_async(self)` and `stop(self)`; `SyncControllerRunner.wants_async(self)` returns `self._core.wants_async()`; `SyncControllerRunner.stop(self)` is a no-op; `FakeControllerRunner(..., wants_async=False)` kwarg + `wants_async()`/`stop()` methods. `build_runner` is UNCHANGED in this task (still always returns `SyncControllerRunner`).

- [ ] **Step 1: Write the failing capability tests**

Add to `tests/test_mpc_integration.py`:
```python
def test_controller_base_wants_async_default_false():
    from controller.base import ControllerBase

    cb = ControllerBase({}, 'C', {})
    assert cb.wants_async() is False
```

Add to `tests/test_sync_runner.py`:
```python
def test_sync_runner_wants_async_reflects_core_and_stop_is_noop():
    from controller.runtime.runner import SyncControllerRunner

    class _Core:
        def wants_async(self):
            return False

    r = SyncControllerRunner(_Core())
    assert r.wants_async() is False
    r.stop()  # must exist and be a harmless no-op for the sync runner
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mpc_integration.py::test_controller_base_wants_async_default_false tests/test_sync_runner.py::test_sync_runner_wants_async_reflects_core_and_stop_is_noop -v`
Expected: FAIL — `AttributeError: 'ControllerBase' object has no attribute 'wants_async'` and `SyncControllerRunner` missing `wants_async`/`stop`.

- [ ] **Step 3: Add the capability + stop plumbing**

In `controller/base.py`, immediately after the `commands_fan` method of `ControllerBase`:
```python
    def wants_async(self):
        """Whether this controller's update() should run on a background thread
        (expensive solve) rather than inline in the control loop."""
        return False
```

In `controller/mpc.py`, next to the existing `commands_fan` (around line 250):
```python
    def wants_async(self):
        return True
```

In `controller/runtime/runner.py`, add two abstract methods to `ControllerRunner`:
```python
    @abstractmethod
    def wants_async(self): ...
    @abstractmethod
    def stop(self): ...
```
and implement them on `SyncControllerRunner`:
```python
    def wants_async(self):
        return self._core.wants_async()

    def stop(self):
        pass
```

In `tests/fakes/runner.py`, extend `FakeControllerRunner.__init__` signature to `def __init__(self, period=None, commands_fan=False, wants_async=False):`, store `self._wants_async = wants_async`, and add:
```python
    def wants_async(self):
        return self._wants_async

    def stop(self):
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mpc_integration.py::test_controller_base_wants_async_default_false tests/test_sync_runner.py::test_sync_runner_wants_async_reflects_core_and_stop_is_noop -v`
Expected: PASS.

- [ ] **Step 5: Full suite green (behavior-neutral)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, golden file unchanged (this task adds interface surface only; `build_runner` still returns `SyncControllerRunner`).

- [ ] **Step 6: Format and commit**

Run `.venv/bin/ruff format controller/base.py controller/mpc.py controller/runtime/runner.py tests/fakes/runner.py tests/test_mpc_integration.py tests/test_sync_runner.py`, then commit (message to a file, no backticks):
```
feat(control): add wants_async() capability and uniform runner stop()

ControllerBase.wants_async() default False; MPC overrides True. The
ControllerRunner interface gains abstract wants_async()/stop(); SyncControllerRunner
reports the core's value and stop() is a no-op. Plumbing only -- build_runner
still returns the synchronous runner. Behavior unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Task 2: `ThreadedControllerRunner` + `build_runner` selection

**Files:**
- Modify: `controller/runtime/runner.py` (add `ThreadedControllerRunner`; `build_runner` selection)
- Test: `tests/test_threaded_runner.py` (create)

**Interfaces:**
- Consumes: Task 1's `wants_async()` on cores; `NormalizedOutput`, `normalize_controller_output`, `_build_core` (already in runner.py).
- Produces: `ThreadedControllerRunner(core)` implementing the full `ControllerRunner` interface plus `stop()`; `build_runner` returns a `ThreadedControllerRunner` when `core.wants_async()` else a `SyncControllerRunner`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_threaded_runner.py`:
```python
import threading

from controller.runtime.runner import ThreadedControllerRunner, build_runner, SyncControllerRunner


class FakeCore:
    """Deterministic core. update() records temps, returns a fixed dict, and
    sets `updated` so tests synchronize on a real event, not a sleep."""

    def __init__(self, period=0.01, commands_fan=False, ratio=0.5):
        self._period = period
        self._commands_fan = commands_fan
        self._ratio = ratio
        self.target = None
        self.updates = []
        self.updated = threading.Event()
        self.tag = 'core-a'

    def get_control_period(self):
        return self._period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return True

    def set_target(self, sp):
        self.target = sp

    def update(self, temp):
        self.updates.append(temp)
        self.updated.set()
        return {'cycle_ratio': self._ratio, 'fan': None}


class BlockingCore(FakeCore):
    """update() blocks on `gate` so a test can observe latest() not blocking
    while a solve is in flight."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.entered = threading.Event()
        self.gate = threading.Event()

    def update(self, temp):
        self.entered.set()
        self.gate.wait(2.0)
        return super().update(temp)


def test_threaded_runner_solves_submitted_temp():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.updated.wait(2.0)  # thread ran update(70.0)
        assert 70.0 in core.updates
        out = r.latest()
        assert out.cycle_ratio == 0.5 and out.fan is None
        assert r.control_period() == 0.01
        assert r.wants_async() is True
    finally:
        r.stop()


def test_threaded_runner_latest_does_not_block_during_solve():
    core = BlockingCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.entered.wait(2.0)  # thread is inside a blocked update()
        # latest() must return promptly (the default snapshot), not wait for the solve.
        out = r.latest()
        assert out.cycle_ratio == 0.0  # initial default; solve has not stored yet
        core.gate.set()  # let the solve finish
        assert core.updated.wait(2.0)
        assert r.latest().cycle_ratio == 0.5
    finally:
        core.gate.set()
        r.stop()


def test_threaded_runner_stop_terminates_thread():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    thread = r._thread
    assert thread.is_alive()
    r.stop()
    assert not thread.is_alive()
    r.stop()  # idempotent


def test_threaded_runner_set_target_and_reconfigure_applied_by_thread():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.updated.wait(2.0)
        r.set_target(225)
        # target is applied on the thread's next iteration; observe via the core
        deadline = threading.Event()
        for _ in range(200):
            if core.target == 225:
                break
            deadline.wait(0.01)
        assert core.target == 225
    finally:
        r.stop()


def test_threaded_runner_controller_state_snapshot():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        snap = r.controller_state()
        assert snap['tag'] == 'core-a'  # well-formed before first solve
        assert snap is not core.__dict__  # a copy, not the live dict
    finally:
        r.stop()


def test_build_runner_selects_threaded_for_wants_async_core(monkeypatch):
    import controller.runtime.runner as runner_mod

    core = FakeCore()  # wants_async() -> True

    monkeypatch.setattr(runner_mod, '_build_core', lambda *a, **k: (core, 'Active'))
    r, status = build_runner({}, {})
    try:
        assert isinstance(r, ThreadedControllerRunner)
        assert status == 'Active'
    finally:
        r.stop()


def test_build_runner_selects_sync_for_non_async_core(monkeypatch):
    import controller.runtime.runner as runner_mod

    class SyncCore(FakeCore):
        def wants_async(self):
            return False

    monkeypatch.setattr(runner_mod, '_build_core', lambda *a, **k: (SyncCore(), 'Active'))
    r, status = build_runner({}, {})
    assert isinstance(r, SyncControllerRunner)
    r.stop()  # no-op
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_threaded_runner.py -v`
Expected: FAIL — `ImportError: cannot import name 'ThreadedControllerRunner'`.

- [ ] **Step 3: Implement `ThreadedControllerRunner` and the selection**

In `controller/runtime/runner.py`, add `import threading` at the top, and add this class after `SyncControllerRunner` (before `_build_core`):
```python
_UNSET = object()


class ThreadedControllerRunner(ControllerRunner):
    """Runs core.update() on a background thread at the core's control period, so
    an expensive solve never blocks the caller. submit()/latest() are
    non-blocking snapshots; the running core is mutated only by the thread."""

    def __init__(self, core):
        self._core = core
        self._lock = threading.Lock()
        self._temp = None
        self._output = NormalizedOutput(cycle_ratio=0.0, fan=None)
        self._pending_target = _UNSET
        self._pending_core = None
        self._state_snapshot = dict(core.__dict__)
        self._control_period = core.get_control_period()
        self._commands_fan = core.commands_fan()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                temp = self._temp
                target = self._pending_target
                self._pending_target = _UNSET
                new_core = self._pending_core
                self._pending_core = None
            if new_core is not None:
                self._core = new_core
            if target is not _UNSET:
                self._core.set_target(target)
            if temp is not None:
                raw = self._core.update(temp)
                ratio, fan = normalize_controller_output(raw)
                snap = dict(self._core.__dict__)
                with self._lock:
                    self._output = NormalizedOutput(cycle_ratio=ratio, fan=fan)
                    self._state_snapshot = snap
            # Interruptible sleep; wait(None/0) would block forever, so floor it.
            self._stop_event.wait(self._control_period or 1.0)

    def set_target(self, setpoint):
        with self._lock:
            self._pending_target = setpoint

    def submit(self, temp):
        with self._lock:
            self._temp = temp

    def latest(self):
        with self._lock:
            return self._output

    def reconfigure(self, settings, control, logger=None):
        core, status = _build_core(settings, control, logger=logger)
        if status == 'Active':
            with self._lock:
                self._pending_core = core
        return status

    def control_period(self):
        return self._control_period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return True

    def controller_state(self):
        with self._lock:
            return dict(self._state_snapshot)

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=2.0)
```
Then change `build_runner` to select by capability:
```python
def build_runner(settings, control, logger=None):
    core, status = _build_core(settings, control, logger=logger)
    if core is None:
        return None, status
    if core.wants_async():
        return ThreadedControllerRunner(core), status
    return SyncControllerRunner(core), status
```

- [ ] **Step 4: Run the threaded-runner tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_threaded_runner.py -v`
Expected: PASS (7 tests), and pytest exits promptly (no leaked threads).

- [ ] **Step 5: Full suite green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, no hang at shutdown, golden file unchanged.

- [ ] **Step 6: Format and commit**

Run `.venv/bin/ruff format controller/runtime/runner.py tests/test_threaded_runner.py`, then commit:
```
feat(control): add ThreadedControllerRunner; build_runner selects by wants_async

A background thread owns the controller core and runs update() at the core's
control period; submit()/latest() are non-blocking, lock-guarded snapshots, and
the running core is mutated only by the thread. build_runner returns the threaded
runner for wants_async cores (MPC) and the synchronous runner otherwise. stop()
signals the thread and joins it; the thread is a daemon so it can never hang
process shutdown.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Task 3: Wire `HoldMode` to the threaded runner (submit every tick; stop at teardown)

**Files:**
- Modify: `controller/runtime/modes/hold.py` (`on_tick`: move `submit` out of the gate; add `teardown`)
- Test: `tests/characterization/test_modes_golden.py` (must remain UNCHANGED — verification only), `tests/test_threaded_runner.py` or a small Hold-teardown test

**Interfaces:**
- Consumes: Task 1/2 runner (`submit`, `latest`, `stop`, `control_period`, `commands_fan`).
- Produces: `HoldMode.teardown(self, ptemp)` stops the runner; `on_tick` submits every tick.

Background: today `HoldMode.on_tick` calls `self._runner.submit(ptemp)` and `self._runner.latest()` together inside the `if (now - controller.cycle_start) > controller_interval:` gate. Moving `submit` out of the gate feeds the thread continuously (so it always has a fresh solve ready) and is behavior-identical for `SyncControllerRunner` (at the gate, `self._temp` is still this tick's `ptemp`).

- [ ] **Step 1: Move `submit(ptemp)` out of the control-period gate**

In `controller/runtime/modes/hold.py` `on_tick`, change the controller block so `submit` runs every tick and only `latest()`/apply stay gated. The current block is:
```python
        controller_interval = self._runner.control_period() or self.state.cycle.cycle_time
        if (now - self.state.controller.cycle_start) > controller_interval:
            # Submit the fresh per-tick ptemp read at the top of this tick.
            self._runner.submit(ptemp)
            _out = self._runner.latest()
            self.state.controller.output, fan_cmd = _out.cycle_ratio, _out.fan
            self.state.controller.cycle_start = now
            ...
```
Change it to:
```python
        # Feed the runner every tick so a threaded core always has a fresh temp
        # to solve; for the synchronous runner this just stores the latest temp,
        # so the value read at the gate below is unchanged.
        self._runner.submit(ptemp)
        controller_interval = self._runner.control_period() or self.state.cycle.cycle_time
        if (now - self.state.controller.cycle_start) > controller_interval:
            _out = self._runner.latest()
            self.state.controller.output, fan_cmd = _out.cycle_ratio, _out.fan
            self.state.controller.cycle_start = now
            ...
```
(Leave everything after `_out = ... .latest()` exactly as-is: the `cycle.ratio`/`raw_ratio` assignment, the fan-command apply, the `fan_assist` decision, the `u_max` clamp.)

- [ ] **Step 2: Add `teardown` to stop the runner**

Add this method to `HoldMode` (e.g. after `status_fragment`):
```python
    def teardown(self, ptemp):
        # Stop the controller runner's background thread (no-op for the
        # synchronous runner). Guard against a failed build leaving no runner.
        if self._runner is not None:
            self._runner.stop()
```

- [ ] **Step 3: Verify the golden oracle is UNCHANGED**

Run: `.venv/bin/python -m pytest tests/characterization/test_modes_golden.py -q`
Expected: PASS with zero edits to the test file (the goldens inject the synchronous `FakeControllerRunner`; submit-every-tick + a no-op `stop` in teardown do not change any asserted value). If any golden fails, the `submit` move altered behavior — re-examine; do NOT edit the golden.
Also confirm no diff: `git diff --stat tests/characterization/test_modes_golden.py` is empty.

- [ ] **Step 4: Add a Hold-teardown-stops-runner test**

Add to `tests/test_threaded_runner.py`:
```python
def test_hold_teardown_stops_threaded_runner():
    # HoldMode.teardown must stop the runner thread. Drive it directly with a
    # minimal HoldMode instance holding a real ThreadedControllerRunner.
    from controller.runtime.modes.hold import HoldMode

    core = FakeCore()
    runner = ThreadedControllerRunner(core)
    thread = runner._thread

    hold = HoldMode.__new__(HoldMode)  # bypass __init__/setup; we only test teardown
    hold._runner = runner
    hold.teardown(70.0)
    assert not thread.is_alive()
```

Run: `.venv/bin/python -m pytest tests/test_threaded_runner.py::test_hold_teardown_stops_threaded_runner -v`
Expected: PASS.

- [ ] **Step 5: Full suite green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, no hang, golden file unchanged.

- [ ] **Step 6: Format and commit**

Run `.venv/bin/ruff format controller/runtime/modes/hold.py tests/test_threaded_runner.py`, then commit:
```
feat(control): drive the threaded runner from Hold (submit each tick, stop at teardown)

HoldMode.on_tick now submits the fresh ptemp to the runner every tick (feeding
a threaded core continuously) while still reading/applying the output only at the
control-period gate -- behavior-identical for the synchronous runner. HoldMode.teardown
stops the runner, terminating the background thread on every loop-exit path. Golden
oracle unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Task 4: Documentation

**Files:**
- Modify: `controller/runtime/runner.py` (module docstring), `controller/runtime/README.md` (follow-up section)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the runner module docstring**

In `controller/runtime/runner.py`, replace the module docstring's final sentences (the ones saying a `ThreadedControllerRunner` "could later" be added / "no threaded implementation exists yet") with a description of the shipped behavior: `SyncControllerRunner` computes inline; `ThreadedControllerRunner` runs the core on a background thread at its control period and hands back non-blocking snapshots via `latest()`; `build_runner` selects between them by the core's `wants_async()` (MPC → threaded). Do not reference "follow-up", "not yet", or "could".

- [ ] **Step 2: Update the README follow-up section**

In `controller/runtime/README.md`, rewrite the "Documented follow-up: `ThreadedControllerRunner`" section so it describes the runner as implemented (not a follow-up): capability-selected via `wants_async()`, background-thread solve decoupled from the loop cadence, non-blocking `latest()`, stopped at Hold teardown. Retitle the section accordingly (e.g. "Controller execution: sync vs threaded").

- [ ] **Step 3: Confirm no code changed and suite still green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (docs-only change).
Run: `grep -rniE "not yet implemented|could later|follow-up|fast-follow" controller/runtime/runner.py controller/runtime/README.md` → no hits describing the threaded runner as future.

- [ ] **Step 4: Format and commit**

Run `.venv/bin/ruff format controller/runtime/runner.py`, then commit:
```
docs(control): describe ThreadedControllerRunner as shipped

Update the runner module docstring and the runtime README to describe the
threaded runner as implemented and capability-selected, not a future follow-up.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Self-Review notes (author)

- **Spec coverage:** Task 1 = `wants_async` capability + uniform `stop` (spec Decision 1 + lifecycle plumbing). Task 2 = `ThreadedControllerRunner` internals, synchronization, `build_runner` selection, cold-start default, isolated tests incl. stop-terminates-thread and non-blocking proof (spec Component + Testing). Task 3 = Hold integration: submit-every-tick refinement + teardown/stop (spec Decision 2 + Lifecycle). Task 4 = docs. All spec sections covered.
- **Behavior-neutrality:** the golden oracle stays unchanged in every task (sync path is behavior-identical); Tasks 1/3/4 are behavior-neutral, Task 2 adds new isolated code.
- **Type consistency:** `wants_async()`/`stop()` signatures identical across `ControllerBase`, `mpc.Controller`, `ControllerRunner` ABC, `SyncControllerRunner`, `ThreadedControllerRunner`, `FakeControllerRunner`. `NormalizedOutput(cycle_ratio, fan)` used consistently. `_build_core`/`build_runner` signatures unchanged. `control_period()` returns the cached core period; the thread floors it with `or 1.0` to avoid an infinite wait.
- **Thread-leak discipline:** every test that builds a threaded runner stops it (`try/finally` or direct `stop()`); the stop-terminates-thread test is the Process_Monitor regression guard.
