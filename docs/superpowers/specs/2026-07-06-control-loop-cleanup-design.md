# Control-loop cleanup — design

Follow-up cleanup after the controller/display separation. Seven changes to the
`controller/runtime` package, ordered so each lands on a green suite. The
golden-master oracle (`tests/characterization/test_modes_golden.py`) is
re-frozen where a change is *intentionally* behavior-altering; every re-freeze
diff is reviewed line-by-line to confirm it is the intended change and not a
regression.

**Standing rule (all tasks):** run `ruff format` on changed files before every
commit.

---

## Task 1 — `Process_Monitor.stop_monitor()` terminates the thread

**Problem.** `Process_Monitor.__init__` starts a non-daemon `_heartbeat_check`
thread. `stop_monitor()` only sets `active = False` (pauses the inner loop); the
thread keeps spinning in the outer `while True`. `base.run()` creates a *fresh*
`Process_Monitor` every work cycle (`base.py:250`) and calls `stop_monitor()` at
teardown (`base.py:646`), so one thread leaks per mode transition. `kill_monitor()`
(the only thing that actually ends the thread) is never called.

**Change.** `stop_monitor()` becomes the terminating stop: set `active = False`
and `kill = True` so the thread exits its outer loop. Delete `kill_monitor()`.
`status()` is unchanged (still reports `'killed'` when `kill` is set). Since each
work cycle builds a new monitor, there is no "restart the same instance" use case
to preserve.

**Validation.** Unit test: a monitor's thread is not alive shortly after
`stop_monitor()`. Full suite green. (The autouse conftest fixture that no-ops
`_heartbeat_check` stays — it is independent belt-and-suspenders.)

---

## Task 2 — Restructure `base.run()` to **sense → safety → act**

This single restructure covers four of the requested items, which are all facets
of the same loop shape: fix "auger cycles before safety" (#2), consolidate the
split tick (#1), merge `on_tick`/`on_fan_tick`, and stop stashing `ptemp` in
`check_safety`.

**Problem.** The in-loop probe read sits *mid-tick* (`base.py:503`), after
`on_tick`. So `on_tick` (auger cycle, Hold controller submit) runs on a
**stale-by-one** `ptemp` (previous iteration's read), while `check_safety` and
`on_fan_tick` run on the fresh read. This forces modes to stash `self.state.ptemp`
in `check_safety` for `on_fan_tick` to reuse, and it means the auger toggles and
the controller act *before* the max-temp safety check for that iteration.

**New canonical per-tick order:**

```
now = clock.now()
execute_control_writes(); control = read_control()
process_system_commands()
if control['updated']: break
...housekeeping: settings_update / distance_update / hopper_check / switch...
current_output_status = get_output_status()      # captured once (load-bearing)
...manual-override block (uses current_output_status)...
probe_profile_update; write probe_device_info
ptemp = read_probes()                            # SINGLE fresh read per tick
write_current(in_data); tuning
if over_max_temp(ptemp): -> Error, break         # universal safety BEFORE actuation
if check_safety(now, ptemp): break               # mode safety BEFORE actuation
on_tick(now, ptemp, current_output_status)       # merged: controller + auger + fan
notifications.check(); on_publish(now)
status publish (every 0.5s)
write history + heartbeat (every 3s)
if should_exit(now, ptemp): break
recipe end-of-loop check
sleep(0.05)
```

**Key points.**
- **One fresh `ptemp` per tick**, passed as a parameter to `check_safety`,
  `on_tick`, and `should_exit`. `self.state.ptemp` and all stashing are removed.
- **`on_tick` and `on_fan_tick` merge into one hook** `on_tick(now, ptemp,
  current_output_status)`. Hold's merged `on_tick` runs, in order: the
  `controller_update` reconfigure, the controller submit/normalize/clamp block,
  the shared auger-cycle toggle, then the lid-open / PWM-duty / fan-assist /
  smoke-plus fan logic. (The controller now receives *this* tick's fresh `ptemp`,
  not the stale-by-one value.)
- **Safety before actuation.** `over_max_temp` and `check_safety` run *before*
  `on_tick`, so on the tick an unsafe temperature is detected the loop breaks
  without cycling the auger or advancing the controller.
- The pre-loop `setup_safety(ptemp)` still runs once against a pre-loop probe read
  (unchanged abort-before-loop contract).

**Behavior changes (intentional, re-froze oracle):** controller/auger see fresh
instead of stale-by-one `ptemp`; auger/controller do not actuate on a tick that
trips max-temp or mode safety. The status published on a given tick now reflects
that tick's actuation.

**Validation.** Run the golden suite; for every changed expectation, review the
diff and confirm it matches the new order (re-freeze) vs. flag a regression. Add
new golden scenarios that pin the new guarantees: (a) over-max-temp on a tick does
NOT call `auger_on`/controller submit that tick; (b) the Hold controller receives
the current tick's `ptemp`. The pure-logic and E2E suites must stay green.

---

## Task 3 — Nest `WorkCycleState` into clean sub-dataclasses

**Problem.** `WorkCycleState` is a flat bag of ~30 fields spanning unrelated
concerns.

**Change.** Group into focused frozen-ish dataclasses (mutable, but each a single
concern), e.g.:

- `CycleState` — `ratio`, `raw_ratio`, `on_time`, `off_time`, `cycle_time`
- `ControllerState` — `output`, `fan_duty`, `controls_fan` (see Task 5),
  `cycle_start`
- `FanState` — `assist`, `pwm_ramping`, `cycle_toggle_time`, `update_time`
- `LidState` — `open_detected`, `expires`
- `StartupState` — `timer`, `raw_temp`
- `PrimeState` — `duration`, `amount`
- `WorkCycleState` — the above via `field(default_factory=...)`, plus
  `target_temp_achieved`, `start_time`, `auger_toggle_time`, `manual_override`,
  `metrics`. (`ptemp` is gone after Task 2.)

Access sites update from `self.state.cycle_ratio` to `self.state.cycle.ratio`,
etc. This is a mechanical rename guarded by the (already re-frozen) oracle.

**Open sub-decision:** whether to also pull the run()-local toggle timers
(`temp_toggle_time`, `display_toggle_time`, `eta_toggle_time`, `hopper_toggle_time`)
onto a `Timers` sub-dataclass. Recommend yes, for one clear home; flag in the plan.

**Validation.** Behavior-neutral; full suite green with no oracle change.

---

## Task 4 — Docstring sweep (fold into the tasks that touch each file)

**Problem.** Many docstrings describe the *old* control loop: they reference
`_work_cycle`, "the inline block", "the legacy ...", "matching the blueprint",
"stale-by-one", `.superpowers/sdd/workcycle-map.md`, plan steps, etc. After
Tasks 2–3 much of that is not just stale prose but describes behavior that no
longer exists.

**Change.** Every module/class/method docstring in `controller/runtime/**`,
`control.py`, and `display_process.py` describes the **current, intended**
behavior and how it is achieved — no references to prior loops, legacy code,
migration plans, or "matching the inline". Do the bulk of this *inside* Tasks 2–3
(rewrite each docstring as its code changes), then a final dedicated pass sweeps
whatever remains (untouched files, cross-references).

**Validation.** Grep for banned phrases (`_work_cycle`, `legacy`, `inline`,
`blueprint`, `stale-by-one`, `workcycle-map`, `plan`, `Task 7`) across the package
returns nothing in docstrings.

---

## Task 5 — Temp-profile fan never runs when the controller commands the fan

**Problem.** `mpc_fan_active` is a runtime latch set the first time the MPC emits a
fan command (`hold.py` `on_tick`). Before that first command (the startup window),
`self.state.mpc_fan_active` is False, so the temperature-profile fan path
(`hold_duty_cycle`, gated `not mpc_fan_active`) runs and drives the fan even though
an MPC controller owns it.

**Change.** Determine "the active controller commands the fan" **at setup**, from
the controller's capability, not from the first observed command. Add a capability
to the controller core (`ControllerBase.commands_fan()` -> `False` default; MPC
overrides to reflect its `enable_fan_input`/allocator config) exposed via
`ControllerRunner.commands_fan()`. In Hold `setup()`, set
`self.state.controller.controls_fan = self._runner.commands_fan()`. Gate the
temp-profile fan path on `not self.state.controller.controls_fan` from tick 1. The
MPC fan command is still applied when it arrives; only the *suppression* of the
temp profile now holds from the start. Rename the flag `mpc_fan_active` ->
`controls_fan` to match its new (capability, not latched) meaning.

**Validation.** New golden scenario: Hold + MPC-that-commands-fan + `pwm_control`
+ `dc_fan`, before the first controller interval elapses — assert the
temp-profile duty (`hold_duty_cycle`) is NOT applied during the window. Existing
Hold fan tests re-frozen as needed.

---

## Task ordering & validation strategy

1. Process_Monitor (independent, small).
2. `base.run()` sense→safety→act restructure (+ docstrings for touched code) —
   the behavior-changing core; re-freeze oracle with reviewed diffs.
3. Nest `WorkCycleState` (+ docstrings for touched code) — behavior-neutral.
4. MPC fan-capability window fix (+ docstrings) — small behavior change, new test.
5. Final docstring sweep across the package.

Each task: `ruff format`, full suite green (`.venv/bin/python -m pytest -q`),
independent review of the diff, commit. The two behavior-changing tasks (2, 4)
get an explicit oracle-diff review: every changed golden expectation is confirmed
intended.

## Out of scope (deferred, not in this plan)

Consolidating `Process_Monitor` beyond Task 1; the `import control as _control`
logger coupling in the modes (the `sys.modules` alias stands); the
`ThreadedControllerRunner`. The user has "more" items coming that may extend this
plan.
