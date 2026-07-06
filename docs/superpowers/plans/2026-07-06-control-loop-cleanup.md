# Control-loop cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up the `controller/runtime` control loop: terminate the process-monitor thread on stop, restructure the per-tick loop to sense→safety→act with a single fresh probe read, nest the loop state into focused dataclasses, close the MPC temp-profile startup window via a controller capability, and make every docstring describe current behavior.

**Architecture:** Five sequential tasks against `controller/runtime/**` (plus `common/process_mon.py`, `controller/base.py`, `controller/runtime/runner.py`, `tests/**`). Two tasks (2, 4) intentionally change behavior; the golden-master oracle (`tests/characterization/test_modes_golden.py`) is re-frozen for them with every changed expectation reviewed. The other tasks are behavior-neutral and keep the oracle unchanged.

**Tech Stack:** Python 3, pytest, valkey (real server available on localhost:6379 for the E2E tier), ruff for formatting.

## Global Constraints

- Run `ruff format` on every changed file before each commit (repo convention; config in `pyproject.toml`). ruff binary: use the project venv (`.venv/bin/ruff`) or whatever the repo provides — locate it before Task 1.
- Interpreter for tests: `.venv/bin/python`. Full suite command: `.venv/bin/python -m pytest -q` (baseline before this plan: **393 passed**; a live `valkey-server` on localhost:6379 makes the parity + E2E tiers run rather than skip).
- Commit messages: never use backticks in `git commit -m` (zsh runs command substitution); use plain text or `-F <file>`. End every commit message with a trailing `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` line.
- Scope-review discipline: multiple sessions commit to this branch; review the exact commit SHAs a task produces, not the whole branch diff.
- Modes reference `control.eventLogger` via `import control as _control` (a module-global logging contract) and the shared `process_system_commands` via `controller.runtime.system_commands` — leave both couplings as-is.

---

## Task 1: `Process_Monitor.stop_monitor()` terminates the thread; drop the test workarounds

**Files:**
- Modify: `common/process_mon.py` (`stop_monitor`, remove `kill_monitor`)
- Modify: `tests/conftest.py` (remove the autouse fixture + faulthandler debug lines)
- Test: `tests/test_process_monitor.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Process_Monitor.stop_monitor()` now ends the heartbeat thread (sets `active=False` and `kill=True`). `Process_Monitor.kill_monitor` no longer exists. `base.run()` already calls `stop_monitor()` at teardown (`controller/runtime/modes/base.py:646`) — no change needed there.

Background: `Process_Monitor.__init__` starts a non-daemon `_heartbeat_check` thread. The current `stop_monitor()` only sets `active=False` (pauses); the thread spins forever in its outer `while True`, exiting only when `kill=True`. `base.run()` builds a fresh monitor per work cycle, so each cycle leaked a thread. `kill_monitor()` (the only terminating path) is never called anywhere.

- [ ] **Step 1: Write the failing test**

Create `tests/test_process_monitor.py`:

```python
import time

from common.process_mon import Process_Monitor


def test_stop_monitor_terminates_the_thread():
    mon = Process_Monitor('test', ['true'], timeout=30)
    thread = mon.process_thread
    assert thread.is_alive()
    mon.start_monitor()
    mon.stop_monitor()
    # The heartbeat loop sleeps up to 1s between checks; give it margin to exit.
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert mon.status() == 'killed'


def test_kill_monitor_removed():
    assert not hasattr(Process_Monitor, 'kill_monitor')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_process_monitor.py -v`
Expected: FAIL — `test_stop_monitor_terminates_the_thread` times out on `is_alive()` (thread never exits), and `test_kill_monitor_removed` fails (attribute still present).

- [ ] **Step 3: Make `stop_monitor` terminate the thread and delete `kill_monitor`**

In `common/process_mon.py`, replace the two methods:

```python
    def stop_monitor(self):
        # Terminate the heartbeat thread. base.run() builds a fresh
        # Process_Monitor per work cycle, so stopping always means "done with
        # this one" -- there is no restart-the-same-instance case to preserve.
        self.active = False
        self.kill = True
```

Delete the `kill_monitor` method entirely.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_process_monitor.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Remove the test-only workarounds in conftest**

Edit `tests/conftest.py` to this exact content (drops the `faulthandler` debug block and the autouse `_neutralize_process_monitor` fixture; keeps only the `sys.path` insert that makes the repo importable):

```python
import os
import sys

# Ensure the repository root is importable so `grillplat`, `common`, etc. resolve.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
```

- [ ] **Step 6: Run the full suite and confirm a clean, non-hanging exit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (393), process exits promptly (no multi-second hang at shutdown). Every characterization/E2E work cycle reaches `teardown()` → `stop_monitor()` via the probe-cap clean loop break, so no heartbeat thread lingers.

If (and only if) the suite hangs at shutdown because some test builds a monitor without reaching teardown, make `stop_monitor()` reliable at teardown by wrapping the `base.run()` loop body so teardown always runs — add a `try/finally` around the main loop in `controller/runtime/modes/base.py` with `monitor.stop_monitor()` in the `finally`. Do NOT re-add the conftest fixture.

- [ ] **Step 7: Format and commit**

Run: `.venv/bin/ruff format common/process_mon.py tests/conftest.py tests/test_process_monitor.py`

```bash
git add common/process_mon.py tests/conftest.py tests/test_process_monitor.py
git commit -F <msg-file>
```
Commit message (write to a file, no backticks):
```
fix(process-mon): stop_monitor terminates the heartbeat thread; drop kill_monitor

stop_monitor now sets kill=True so the non-daemon _heartbeat_check thread
exits instead of leaking one thread per work cycle. kill_monitor (never
called) is removed. With stop reliably terminating the thread, the autouse
conftest fixture that no-oped _heartbeat_check and the faulthandler debug
lines are no longer needed and are removed.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Task 2: Restructure `base.run()` to sense → safety → act (single fresh ptemp; merged tick)

**Files:**
- Modify: `controller/runtime/modes/base.py` (`run()` loop order; hook + helper signatures; docstrings)
- Modify: `controller/runtime/modes/hold.py` (merge `on_fan_tick` into `on_tick`; drop ptemp stash; docstrings)
- Modify: `controller/runtime/modes/smoke.py` (merge `on_fan_tick` into `on_tick`; drop ptemp stash; docstrings)
- Modify: `controller/runtime/modes/startup.py`, `prime.py` (merged `on_tick` signature only)
- Modify: `tests/characterization/test_modes_golden.py` (re-freeze changed expectations; add new scenarios)
- Modify: `tests/test_control_mode_base.py` (hook-order structural test — update to new order)

**Interfaces:**
- Consumes: Task 1 (unchanged monitor teardown).
- Produces: new hook signatures used by all modes:
  - `on_tick(self, now, ptemp, current_output_status)` — merged control+fan hook (was two hooks `on_tick(now, cos)` + `on_fan_tick(now, cos)`).
  - `check_safety(self, now, ptemp) -> bool` — unchanged signature, but no longer stashes `self.state.ptemp`.
  - `should_exit(self, now, ptemp) -> bool`, `setup_safety(self, ptemp) -> str` — unchanged.
  - `_smoke_plus_fan_tick(self, now, ptemp, current_output_status)` — gains an explicit `ptemp` param (was `self.state.ptemp`).
  - `_auger_cycle_tick(self, now, current_output_status)` — unchanged.
  - `on_fan_tick` is **removed** from the base and all modes.
  - `self.state.ptemp` is **removed** from `WorkCycleState` (Task 3 will not re-add it).

### The new canonical per-tick order

Rewrite the `while status == 'Active':` body in `base.run()` to this exact order (block contents are unchanged from today except where noted; only their ORDER and the two merged calls change):

```
now = ctx.clock.now()
execute_control_writes(); control = read_control(); self.control = control
process_system_commands(ctx)
if control['updated']: break
# settings_update  -> on_settings_reload()
# distance_update
# hopper_check
# ON/OFF switch check -> (Stop, break)
current_output_status = grill_platform.get_output_status()      # captured ONCE
# manual-override block (uses current_output_status)
# probe_profile_update
write_generic_key('probe_device_info', probe_complex.get_device_info())
sensor_data = probe_complex.read_probes()                       # SINGLE fresh read
ptemp = list(sensor_data['primary'].values())[0]
# in_data populate (probe_history / primary_setpoint / notify_targets / ext_data)
write_current(in_data)
if control['tuning_mode']: write_tr(...)
# ---- SAFETY BEFORE ACTUATION ----
if over_max_temp(ptemp, self.settings['safety']):               # universal
    display push ('text','ERROR'); control Error+updated; write_control;
    ctx.notifications.send('Grill_Error_01'); break
if self.check_safety(now, ptemp): break                         # mode safety
# ---- ACT ----
self.on_tick(now, ptemp, current_output_status)                 # merged control+fan
# ---- PUBLISH ----
# eta_toggle / update_eta
control = ctx.notifications.check(...); self.control = control
self.on_publish(now)
# status publish (every 0.5s) incl status_fragment()
# write_history + monitor.heartbeat() (every 3s)
if self.should_exit(now, ptemp): break
# recipe end-of-loop check
ctx.clock.sleep(0.05)
```

Notes on what MOVED relative to today:
- The single in-loop `read_probes()`/`ptemp` (was `base.py:503`) now provides the one fresh `ptemp` for the whole tick. The old pre-loop stash `self.state.ptemp = ptemp` (`base.py:333`) is deleted — `setup_safety(ptemp)` still receives the pre-loop probe read directly, and nothing else reads a stashed ptemp.
- `over_max_temp` (was at end, `base.py:598`) and `check_safety` (was `base.py:580`) now run BEFORE the mode tick, so an unsafe temperature breaks the loop without cycling the auger or advancing the controller.
- The merged `on_tick(now, ptemp, cos)` runs once, AFTER safety, replacing the old `on_tick` (was `base.py:490`, before the probe read) and `on_fan_tick` (was `base.py:584`).
- `notifications.check` + `on_publish` + status publish now run AFTER the mode tick, so published status reflects this tick's actuation.

- [ ] **Step 1: Update base hook + helper signatures and docstrings**

In `controller/runtime/modes/base.py`:
- Change the default hook `def on_tick(self, now, current_output_status)` → `def on_tick(self, now, ptemp, current_output_status)` (body stays `pass`).
- Delete the default `def on_fan_tick(self, now, current_output_status): pass`.
- Change `def _smoke_plus_fan_tick(self, now, current_output_status)` → `def _smoke_plus_fan_tick(self, now, ptemp, current_output_status)`; inside it, delete the line `ptemp = self.state.ptemp` (now the parameter).
- Rewrite the `ControlMode` class docstring and the `_smoke_plus_fan_tick`/`_auger_cycle_tick` docstrings to describe the merged single-tick hook and the sense→safety→act order (no "stale-by-one", no "on_fan_tick", no "legacy inline block ~NNN", no "blueprint"/"workcycle-map"). Describe current behavior only.

- [ ] **Step 2: Rewrite the `run()` loop body to the new order**

Apply the ordering above in `controller/runtime/modes/base.py`'s `run()`. Delete the pre-loop `self.state.ptemp = ptemp` stash (and its comment) at ~`base.py:333` (keep `status = self.setup_safety(ptemp)` right after the pre-loop probe read). Move the `over_max_temp` and `check_safety` blocks to before the single merged `self.on_tick(now, ptemp, current_output_status)` call; move `notifications.check`/`on_publish`/status/history to after it; delete the separate `on_fan_tick` call site.

- [ ] **Step 3: Merge each mode's fan hook into `on_tick`**

`controller/runtime/modes/prime.py` and `startup.py`: change signature only:
```python
    def on_tick(self, now, ptemp, current_output_status):
        self._auger_cycle_tick(now, current_output_status)
```

`controller/runtime/modes/smoke.py`: merge and drop the stash:
```python
    def on_tick(self, now, ptemp, current_output_status):
        self._auger_cycle_tick(now, current_output_status)
        self._smoke_plus_fan_tick(now, ptemp, current_output_status)
```
Delete `smoke.py`'s `on_fan_tick`, and delete the `self.state.ptemp = ptemp` line in its `check_safety`.

`controller/runtime/modes/hold.py`: merge `on_fan_tick`'s body into `on_tick` AFTER the existing controller+auger logic, feeding the parameter `ptemp` where it read `self.state.ptemp`:
```python
    def on_tick(self, now, ptemp, current_output_status):
        # ... existing controller_update + controller submit/normalize/clamp block ...
        #     (change `self._runner.submit(self.state.ptemp)` -> `self._runner.submit(ptemp)`)
        # ... existing `self._auger_cycle_tick(now, current_output_status)` ...
        # ---- merged from the former on_fan_tick (uses `ptemp` param) ----
        # target_temp_achieved latch; lid-open detect/clear/toggle;
        # PWM-duty-from-temp-profile (gated `not self.state.mpc_fan_active`);
        # fan-assist-PID; then self._smoke_plus_fan_tick(now, ptemp, current_output_status)
```
Delete `hold.py`'s `on_fan_tick`, delete the `self.state.ptemp = ptemp` line in its `check_safety`, and change the controller submit to use the `ptemp` parameter. Update all three mode docstrings to describe the merged single-tick hook and drop "stale-by-one"/"on_fan_tick"/"stashed" language.

- [ ] **Step 4: Add new golden scenarios pinning the new guarantees**

Both new scenarios use `FakeControllerRunner`; first make it record submitted
temps — in `tests/fakes/runner.py`, add `self.submitted_temps = []` in
`__init__` and append `temp` in `submit()` (test-double change only). Then add
to `tests/characterization/test_modes_golden.py`:

```python
def test_hold_over_maxtemp_does_not_submit_controller_that_tick():
    # safety-before-actuation: when max-temp trips, the loop breaks BEFORE the
    # merged on_tick, so the controller is never advanced on the over-temp tick.
    # (In the old order on_tick ran before the safety check.)
    settings = base_settings()
    settings['safety']['maxtemp'] = 500
    settings['controller'] = settings.get('controller', {})
    control_data = base_control(mode='Hold')
    control_data['primary_setpoint'] = 225
    probes = FakeProbes().script([550, 550, 550])  # over maxtemp from tick 1
    runner = FakeControllerRunner(period=0.0).script(
        [NormalizedOutput(cycle_ratio=0.5, fan=None)] * 4
    )
    result = run_mode('Hold', settings=settings, control_data=control_data,
                      pellet_db=base_pellet_db(), probes=probes,
                      grill=FakeGrillPlatform(), runner=runner)
    assert result.final_control['mode'] == 'Error'
    assert runner.submitted_temps == []  # controller never advanced -- safety first


def test_hold_controller_receives_current_tick_ptemp():
    # sense->act: the controller is submitted an in-loop probe value, not a
    # pre-loop-only stash. Below maxtemp so the loop runs a few ticks.
    settings = base_settings()
    control_data = base_control(mode='Hold')
    control_data['primary_setpoint'] = 225
    probes = FakeProbes().script([200, 205, 210, 215, 220])
    runner = FakeControllerRunner(period=0.0).script(
        [NormalizedOutput(cycle_ratio=0.5, fan=None)] * 8
    )
    run_mode('Hold', settings=settings, control_data=control_data,
             pellet_db=base_pellet_db(), probes=probes, probe_cap=4,
             grill=FakeGrillPlatform(), runner=runner)
    # Every submitted temp is an in-loop read (200..220), proving on_tick uses
    # the fresh per-tick ptemp parameter.
    assert runner.submitted_temps
    assert all(t in (200, 205, 210, 215, 220) for t in runner.submitted_temps)
```

If the controller-submit gate (`(now - controller.cycle_start) > interval`)
does not fire on the very first tick under `ManualClock` with `period=0.0`, keep
`probe_cap` ≥ 3 so at least one submit occurs; the assertions hold regardless of
which in-loop tick submits.

- [ ] **Step 5: Run the golden suite and RE-FREEZE with reviewed diffs**

Run: `.venv/bin/python -m pytest tests/characterization/test_modes_golden.py -v`

For EACH failing pre-existing assertion: read the failure, confirm the new value is the *intended* consequence of sense→safety→act + fresh ptemp (e.g. the Hold sticky-latch scenario `test_hold_controller_fan_duty_sticky_latch_suppresses_temp_profile`, auger/fan call ordering, published-status timing). Update the expected value to the observed one ONLY when you have confirmed it matches the new order; if a diff looks like an unintended regression, fix the code instead. Add a one-line comment on any re-frozen assertion noting why it changed. Update `tests/test_control_mode_base.py`'s hook-order structural test to assert the new order (probe read → over_max_temp → check_safety → on_tick → publish).

- [ ] **Step 6: Run the full suite (all tiers)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Pure-logic and E2E tiers unaffected; only golden + base-structural expectations changed.

- [ ] **Step 7: Format and commit**

Run `ruff format` on every changed file, then:
```bash
git add controller/runtime/modes/ tests/characterization/test_modes_golden.py tests/test_control_mode_base.py tests/fakes/runner.py
git commit -F <msg-file>
```
Commit message (no backticks):
```
refactor(control): sense->safety->act tick with a single fresh probe read

Restructure ControlMode.run() so each tick reads probes once at the top, runs
the universal max-temp check and the mode check_safety BEFORE any actuation,
then a single merged on_tick(now, ptemp, current_output_status) that does the
controller/auger/fan work, then publish/status/history. Merges the former
on_fan_tick into on_tick and passes ptemp as a parameter, so modes no longer
stash self.state.ptemp. The controller and auger now act on the current
tick's temperature instead of the previous iteration's, and the auger no
longer cycles on a tick that trips a safety check. Golden-master oracle
re-frozen for the intended behavior changes, with new scenarios pinning
safety-before-actuation and the fresh-ptemp controller submit.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Task 3: Nest `WorkCycleState` into focused sub-dataclasses

**Files:**
- Modify: `controller/runtime/state.py` (nested dataclasses)
- Modify: `controller/runtime/modes/base.py`, `hold.py`, `smoke.py`, `startup.py`, `reignite.py`, `prime.py`, `shutdown.py`, `monitor.py`, `manual.py` (field access renames)
- Modify: `controller/runtime/controller.py` if it touches `self.state.*` (it does not today — verify)
- Modify: `tests/test_work_cycle_state.py` (update to nested shape); any test reading `state.<field>` directly

**Interfaces:**
- Consumes: Task 2 (ptemp field already gone).
- Produces: `WorkCycleState` with nested sub-objects; every `self.state.<flat>` becomes `self.state.<group>.<field>` per the map below. Behavior-neutral — the golden oracle does NOT change.

### Exact field map

| old `self.state.X` | new |
|---|---|
| `cycle_ratio` | `cycle.ratio` |
| `raw_cycle_ratio` | `cycle.raw_ratio` |
| `on_time` | `cycle.on_time` |
| `off_time` | `cycle.off_time` |
| `cycle_time` | `cycle.cycle_time` |
| `controller_output` | `controller.output` |
| `controller_fan_duty` | `controller.fan_duty` |
| `mpc_fan_active` | `controller.controls_fan` *(renamed; Task 4 changes its meaning)* |
| `controller_cycle_start` | `controller.cycle_start` |
| `fan_assist` | `fan.assist` |
| `pwm_fan_ramping` | `fan.pwm_ramping` |
| `fan_cycle_toggle_time` | `fan.cycle_toggle_time` |
| `fan_update_time` | `fan.update_time` |
| `lid_open_detect` | `lid.open_detected` |
| `lid_open_expires` | `lid.expires` |
| `startup_timer` | `startup.timer` |
| `raw_startup_temp` | `startup.raw_temp` |
| `prime_duration` | `prime.duration` |
| `prime_amount` | `prime.amount` |
| `start_time` | `timers.start_time` |
| `auger_toggle_time` | `timers.auger_toggle` |
| `target_temp_achieved` | *(stays top-level)* `target_temp_achieved` |
| `manual_override` | *(stays top-level)* `manual_override` |
| `metrics` | *(stays top-level)* `metrics` |

Also move these `run()`-local toggle timers onto `timers` (per approved design — one clean home): `display_toggle_time` → `timers.display_toggle`, `hopper_toggle_time` → `timers.hopper_toggle`, `eta_toggle_time` → `timers.eta_toggle`, `temp_toggle_time` → `timers.temp_toggle`. (In `run()` they are currently plain locals; replace each with the `self.state.timers.*` field so all loop timers live in one place.)

- [ ] **Step 1: Write the new `state.py`**

```python
from dataclasses import dataclass, field


@dataclass
class CycleState:
    ratio: float = 0.0
    raw_ratio: float = 0.0
    on_time: float = 0.0
    off_time: float = 0.0
    cycle_time: float = 0.0


@dataclass
class ControllerState:
    output: float = 0.0
    fan_duty: float | None = None
    controls_fan: bool = False
    cycle_start: float = 0.0


@dataclass
class FanState:
    assist: bool = False
    pwm_ramping: bool = False
    cycle_toggle_time: float = 0.0
    update_time: float = 0.0


@dataclass
class LidState:
    open_detected: bool = False
    expires: float = 0.0


@dataclass
class StartupState:
    timer: float = 0.0
    raw_temp: float = 0.0


@dataclass
class PrimeState:
    duration: float = 0.0
    amount: float = 0.0


@dataclass
class Timers:
    start_time: float = 0.0
    auger_toggle: float = 0.0
    display_toggle: float = 0.0
    hopper_toggle: float = 0.0
    eta_toggle: float = 0.0
    temp_toggle: float = 0.0


@dataclass
class WorkCycleState:
    """Loop-local state for one work cycle, grouped by concern."""

    cycle: CycleState = field(default_factory=CycleState)
    controller: ControllerState = field(default_factory=ControllerState)
    fan: FanState = field(default_factory=FanState)
    lid: LidState = field(default_factory=LidState)
    startup: StartupState = field(default_factory=StartupState)
    prime: PrimeState = field(default_factory=PrimeState)
    timers: Timers = field(default_factory=Timers)
    target_temp_achieved: bool = False
    manual_override: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
```

- [ ] **Step 2: Rename all field access sites**

Apply the map to `base.py` and every mode file. Verify none missed:
Run: `grep -rnoE "self\.state\.(cycle_ratio|raw_cycle_ratio|on_time|off_time|cycle_time|controller_output|controller_fan_duty|mpc_fan_active|controller_cycle_start|fan_assist|pwm_fan_ramping|fan_cycle_toggle_time|fan_update_time|lid_open_detect|lid_open_expires|startup_timer|raw_startup_temp|prime_duration|prime_amount|start_time|auger_toggle_time)\b" controller/runtime/`
Expected after edits: no matches (all flat names replaced).

- [ ] **Step 3: Update `tests/test_work_cycle_state.py` and any direct state readers**

Update `tests/test_work_cycle_state.py` to construct/inspect the nested shape (e.g. `WorkCycleState().cycle.ratio == 0.0`, dict-independence via `state.manual_override`/`state.metrics`). Grep tests for `.state.` and `state\.` flat accesses and fix any.
Run: `grep -rn "\.cycle_ratio\|\.mpc_fan_active\|\.lid_open_detect\|\.startup_timer" tests/` → fix matches.

- [ ] **Step 4: Run the full suite (oracle must be unchanged)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS with NO change to `test_modes_golden.py` expectations (this task is behavior-neutral). If a golden test changes, a rename was wrong — fix it.

- [ ] **Step 5: Docstrings for touched code**

Update `state.py` and any mode docstring that named a flat field to use the nested names and describe current behavior.

- [ ] **Step 6: Format and commit**

`ruff format` changed files, then commit:
```
refactor(control): nest WorkCycleState into focused sub-dataclasses

Group the flat WorkCycleState fields into CycleState/ControllerState/FanState/
LidState/StartupState/PrimeState/Timers sub-dataclasses, and pull the loop's
toggle timers into Timers for one clear home. Pure mechanical rename; behavior
unchanged and the golden oracle is untouched.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Task 4: Temp-profile fan never runs when the controller commands the fan

**Files:**
- Modify: `controller/base.py` (`ControllerBase.commands_fan()`; simplify `normalize_controller_output`)
- Modify: `controller/mpc.py` (override `commands_fan()`)
- Modify: `controller/runtime/runner.py` (`ControllerRunner.commands_fan()` + `SyncControllerRunner.commands_fan()`)
- Modify: `controller/runtime/modes/hold.py` (`setup()` sets `state.controller.controls_fan`; on_tick applies fan under capability; temp-profile gated on capability)
- Modify: `tests/test_mpc_integration.py` (normalize simplification), `tests/fakes/runner.py` (`commands_fan` on the fake), `tests/characterization/test_modes_golden.py` (new window scenario; re-freeze Hold fan tests if needed)

**Interfaces:**
- Consumes: Task 3 (`state.controller.controls_fan` exists).
- Produces:
  - `ControllerBase.commands_fan(self) -> bool` (default `False`).
  - `controller.mpc.Controller.commands_fan(self) -> bool` (True when its config enables fan output).
  - `ControllerRunner.commands_fan(self) -> bool` and `SyncControllerRunner.commands_fan(self)` delegating to `self._core.commands_fan()`.
  - `FakeControllerRunner.commands_fan(self) -> bool` (constructor-controlled, default False).

Background: today `mpc_fan_active` latches the first time the MPC emits a fan command, so the temp-profile fan path runs during the startup window before that first command. The capability is known at setup, closing the window. The capability is orthogonal to `normalize_controller_output`, which still coerces the heterogeneous controller returns (legacy float vs MPC dict) into a cycle ratio; the capability only makes the fan-*presence* branch redundant.

- [ ] **Step 1: Write the failing capability + window tests**

Add to `tests/test_mpc_integration.py`:
```python
def test_controller_base_commands_fan_default_false():
    from controller.base import ControllerBase
    cb = ControllerBase({}, 'C', {})
    assert cb.commands_fan() is False
```

Add to `tests/characterization/test_modes_golden.py` (uses the fake runner's new `commands_fan`):
```python
def test_hold_mpc_commands_fan_suppresses_temp_profile_from_first_tick():
    # Startup window: an MPC that commands the fan must suppress the temp-profile
    # duty from tick 1, BEFORE its first controller interval elapses.
    settings = base_settings()
    settings['platform']['dc_fan'] = True
    settings['pwm']['update_time'] = 0  # temp-profile branch would fire every tick
    control_data = base_control(mode='Hold')
    control_data['pwm_control'] = True
    control_data['primary_setpoint'] = 225
    probes = FakeProbes().script([210] * 8)
    grill = FakeGrillPlatform(dc_fan=True)
    runner = FakeControllerRunner(period=999, commands_fan=True).script(
        [NormalizedOutput(cycle_ratio=0.5, fan=None)] * 8  # never reaches a fan command in-window
    )
    result = run_mode('Hold', settings=settings, control_data=control_data,
                      pellet_db=base_pellet_db(), probes=probes, probe_cap=6,
                      grill=grill, runner=runner)
    # Temp-profile duty (would be 75 for setpoint-ptemp=15) must NOT be applied.
    assert ('set_duty_cycle', (75,)) not in result.grill_calls
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_mpc_integration.py::test_controller_base_commands_fan_default_false tests/characterization/test_modes_golden.py::test_hold_mpc_commands_fan_suppresses_temp_profile_from_first_tick -v`
Expected: FAIL — `commands_fan` undefined; window test applies the temp-profile duty (75).

- [ ] **Step 3: Add the capability through the stack**

`controller/base.py` — add to `ControllerBase`:
```python
    def commands_fan(self):
        """Whether this controller issues fan duty commands (vs. auger-only)."""
        return False
```
`controller/mpc.py` — override on its `Controller`:
```python
    def commands_fan(self):
        return bool(self.cfg.get('enable_fan_input', False))
```
(Confirm the exact config key/attr the MPC uses to enable fan output — `enable_fan_input` per `controller/mpc.py`; use whatever governs whether `update()` returns a `fan` duty.)

`controller/runtime/runner.py` — add to the ABC and `SyncControllerRunner`:
```python
    def commands_fan(self):
        return self._core.commands_fan()
```
(Add an abstract `commands_fan` to `ControllerRunner` too.)

`tests/fakes/runner.py` — add a `commands_fan` constructor kwarg (default `False`) and method returning it.

- [ ] **Step 4: Use the capability in Hold; gate the temp profile on it**

In `controller/runtime/modes/hold.py`:
- In `setup()`, after building the runner, set:
```python
        self.state.controller.controls_fan = (
            self._runner.commands_fan() if self._runner is not None else False
        )
```
  Remove the `self.state.mpc_fan_active = False` line (the field is now the capability, set here).
- In `on_tick`, the MPC-fan application block: keep applying `control['duty_cycle'] = fan_cmd['duty']` when a fan command arrives, but do NOT set the latch (the capability already holds). Where it read `self.state.mpc_fan_active = True`, remove that assignment.
- The temp-profile gate becomes `not self.state.controller.controls_fan` (already renamed in Task 3), which now holds from tick 1.

- [ ] **Step 5: Simplify `normalize_controller_output` (optional cleanup within this task)**

Keep `normalize_controller_output` (still needed for float/dict ratio coercion). It already returns `fan=None` for controllers without a duty; leave its signature `(ratio, fan)` intact so `test_normalize_handles_float_and_dict` still passes unchanged. No behavior change required here — do NOT delete it (full removal is a separate, out-of-scope controller-standardization task).

- [ ] **Step 6: Run new tests, then re-freeze Hold fan tests as needed**

Run: `.venv/bin/python -m pytest tests/test_mpc_integration.py tests/characterization/test_modes_golden.py -v`
Expected: new tests PASS. If a pre-existing Hold fan scenario changes because the capability now suppresses the temp profile earlier, confirm it is the intended change and re-freeze that assertion with a one-line comment.

- [ ] **Step 7: Full suite + format + commit**

Run: `.venv/bin/python -m pytest -q` (expect PASS). `ruff format` changed files. Commit:
```
feat(control): decide MPC fan ownership at setup, closing the temp-profile window

Add ControllerBase.commands_fan() (MPC overrides based on its fan config),
exposed via ControllerRunner. Hold sets state.controller.controls_fan at setup
and gates the temperature-profile fan path on it, so the temp profile is
suppressed from the first tick when an MPC owns the fan -- instead of only
after the MPC's first fan command latched the old mpc_fan_active flag. The MPC
fan command is still applied when it arrives.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Task 5: Final docstring sweep — every docstring describes current behavior

**Files:**
- Modify: docstrings across `controller/runtime/**`, `control.py`, `display_process.py` (and any `controller/runtime/*.py` helper whose docstring still references old behavior)

**Interfaces:** none (documentation only; no code changes).

Tasks 2–4 already rewrote the docstrings on the code they touched. This task sweeps everything else so nothing references a previous loop, legacy code, migration plans, or "the inline"/"blueprint".

- [ ] **Step 1: Find the offenders**

Run: `grep -rniE "_work_cycle|legacy|inline|blueprint|stale-by-one|workcycle-map|\.superpowers|Task [0-9]|migrat|the old |plan(ned)?\b" controller/runtime/ control.py display_process.py`
Review each hit inside a docstring/comment.

- [ ] **Step 2: Rewrite each to describe current behavior**

For every module/class/method docstring, ensure it states what the unit does now, how it does it, and what it depends on — with no reference to prior implementations, migration history, or planning docs. Delete stale cross-references (e.g. `.superpowers/sdd/workcycle-map.md`). Keep docstrings accurate to the post-Task-2/3/4 code (single merged `on_tick`, nested state, capability-gated fan).

- [ ] **Step 3: Verify the sweep**

Run the Step 1 grep again.
Expected: no remaining hits in docstrings/comments (a legitimate code identifier match, if any, is fine — but there should be no prose referencing old behavior).

- [ ] **Step 4: Confirm nothing else changed**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (docstring-only changes).

- [ ] **Step 5: Format and commit**

`ruff format` changed files, then commit:
```
docs(control): rewrite docstrings to describe current behavior only

Sweep controller/runtime, control.py, and display_process.py so no docstring
references _work_cycle, the old inline loop, migration plans, the blueprint,
or "stale-by-one" -- each now describes the current, intended behavior and how
it is achieved.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Self-Review notes (author)

- **Spec coverage:** Task 1 = Process_Monitor + conftest removal (spec Task 1). Task 2 = sense→safety→act, merge on_tick/on_fan_tick, drop ptemp stash (spec Task 2, items #1/#2/merge/stash). Task 3 = nested state incl. Timers (spec Task 3, user-confirmed). Task 4 = MPC capability window (spec Task 5). Task 5 = docstring sweep (spec Task 4, final pass; per-task docstrings folded into 2–4). All spec sections covered.
- **Behavior-changing tasks (2, 4)** carry an explicit oracle re-freeze step with per-assertion review; behavior-neutral tasks (1, 3, 5) must leave the golden oracle unchanged.
- **Type consistency:** `commands_fan()` name used identically in `ControllerBase`, `mpc.Controller`, `ControllerRunner`, `SyncControllerRunner`, `FakeControllerRunner`. `state.controller.controls_fan` used identically in Task 3 map and Task 4 usage. Merged hook `on_tick(now, ptemp, current_output_status)` consistent across base + all modes; `_smoke_plus_fan_tick(now, ptemp, current_output_status)` consistent in base + smoke + hold.
- **Deferred (not in this plan):** full removal of `normalize_controller_output` via controller return standardization; the additional cleanup items the user will provide as a separate plan.
