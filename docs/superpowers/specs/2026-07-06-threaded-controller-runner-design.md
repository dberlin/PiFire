# ThreadedControllerRunner — design

Implement the `ThreadedControllerRunner` follow-up the `ControllerRunner` seam
was built for. Move an expensive controller's `update()` (the MPC's NLP/net
solve, `control_period = 5.0s`) off the safety/actuation control loop onto a
background thread, so a slow solve never blocks probe reads, the max-temp check,
or auger/igniter/fan timing.

## Motivation

`HoldMode.on_tick` currently runs `self._runner.latest()` inline every
`control_period`; for the MPC that call executes the solve synchronously,
stalling the whole control loop for the solve duration. Threading decouples the
control-math cadence from the loop cadence: the loop stays responsive at its
~0.05s tick while the controller solves on its own thread.

## Decision 1 — selection is a per-controller capability

Mirror the `commands_fan()` pattern already in the codebase.

- `ControllerBase.commands_fan`-style addition: `ControllerBase.wants_async(self) -> bool` returns `False`.
- `controller.mpc.Controller.wants_async(self) -> bool` returns `True`.
- `build_runner(settings, control, logger=None)` builds the core, then:
  - if `core.wants_async()` → return `ThreadedControllerRunner(core), status`
  - else → return `SyncControllerRunner(core), status`
- `ControllerRunner` ABC gains abstract `wants_async(self)`; both concrete
  runners implement it (Sync returns the core's value; Threaded returns `True`).

PID and the other cheap controllers stay synchronous — their `update()` is
trivial, so threading them is pure risk with no benefit.

## Decision 2 — Hold keeps its gate; `latest()` is non-blocking

`HoldMode.on_tick` keeps its existing `if (now - controller.cycle_start) > control_period:`
gate for WHEN it reads and applies the controller output. What changes:

- **`submit(ptemp)` moves OUT of the gate — called every tick** (refinement of
  the sketch). `submit` only stores the latest temp (cheap, lock-guarded), so
  the thread is fed a fresh temperature continuously and always has a recent
  solve ready. This is **behavior-identical for `SyncControllerRunner`**: at the
  gate, `self._temp` holds this tick's `ptemp` exactly as today, so `latest()`
  solves the same value. It removes the cold-start chicken-and-egg (the thread
  would otherwise have no temp until the first gate fired).
- **`latest()` + apply stay inside the gate.** For the threaded runner `latest()`
  returns the freshest thread-computed snapshot without blocking; the applied
  output lags real-time by at most one `control_period` (≤5s for MPC). `cycle.ratio`
  therefore still updates once per `control_period`, exactly as today, so auger
  timing (`_auger_cycle_tick`, `_on_auger_on`) is unchanged.

Concretely, Hold's on_tick controller block becomes:

```
self._runner.submit(ptemp)                     # every tick (was inside the gate)
controller_interval = self._runner.control_period() or self.state.cycle.cycle_time
if (now - self.state.controller.cycle_start) > controller_interval:
    _out = self._runner.latest()               # non-blocking snapshot
    self.state.controller.output, fan_cmd = _out.cycle_ratio, _out.fan
    self.state.controller.cycle_start = now
    ... (unchanged: cycle.ratio, fan apply, fan_assist, u_max clamp) ...
```

## Component — `ThreadedControllerRunner`

Lives in `controller/runtime/runner.py` beside `SyncControllerRunner`. Same
`ControllerRunner` interface plus `stop()`. The background thread owns the core
exclusively; every main-thread method is non-blocking and lock-guarded.

State (guarded by one `threading.Lock`):
- `_temp` — latest submitted temperature (or `None`).
- `_output` — latest `NormalizedOutput` (initialized to a safe default before
  the first solve completes; see cold start).
- `_pending_target` — a setpoint to apply, or a sentinel meaning "none".
- `_pending_core` — a newly-built core to swap in, or `None`.
- `_state_snapshot` — `dict(core.__dict__)` published by the thread for
  `controller_state()` (MQTT), so the main thread never reads core internals
  directly. Initialized at construction from the initial core so
  `controller_state()` is well-formed before the first solve.
- `_stop_event` — `threading.Event`.

Cached at construction (read from the core once; static thereafter):
`_control_period = core.get_control_period()`, `_commands_fan = core.commands_fan()`.

Thread loop (`daemon=True`, started in `__init__`):
```
while not self._stop_event.is_set():
    with self._lock:
        temp = self._temp
        target = self._pending_target; self._pending_target = _UNSET
        new_core = self._pending_core; self._pending_core = None
    if new_core is not None:
        self._core = new_core                 # only the thread mutates _core
    if target is not _UNSET:
        self._core.set_target(target)
    if temp is not None:
        raw = self._core.update(temp)         # the slow solve — OUTSIDE the lock
        out = NormalizedOutput(*normalize_controller_output(raw))
        snap = dict(self._core.__dict__)
        with self._lock:
            self._output = out
            self._state_snapshot = snap
    self._stop_event.wait(self._control_period)   # interruptible sleep
```

Main-thread methods:
- `submit(temp)` — `with lock: self._temp = temp`.
- `latest()` — `with lock: return self._output`.
- `set_target(sp)` — `with lock: self._pending_target = sp`.
- `reconfigure(settings, control, logger=None)` — build a new core on the main
  thread via `_build_core`; if `status == 'Active'`, `with lock: self._pending_core = core`;
  return `status` synchronously (unchanged contract). The running core is never
  touched by the main thread; the thread swaps it in on its next iteration.
- `control_period()` / `commands_fan()` / `wants_async()` — return cached values.
- `controller_state()` — `with lock: return dict(self._state_snapshot)`.
- `stop()` — `self._stop_event.set(); self._thread.join(timeout=2.0)`. The
  thread's `_stop_event.wait(control_period)` sleep is interrupted immediately by
  `set()`, so `join` normally returns at once; if the thread is mid-solve, `join`
  waits up to 2.0s and the `daemon=True` flag covers the rare longer overrun.
  Idempotent (a second `stop()` is a no-op).

### Cold start

`_output` is initialized to `NormalizedOutput(cycle_ratio=0.0, fan=None)` so
`latest()` always returns a well-formed value (Hold clamps `cycle.ratio` up to
`u_min`, so 0.0 is safe). Because Hold now submits every
tick from tick 1, the thread has been solving for ~`control_period` before Hold's
first gate fires, so by the first `latest()` a real solve is normally ready. If a
solve is somehow not yet done, `latest()` returns the safe default for that one
period — never blocks. (Hold does not use the controller output before the first
gate anyway; it runs on `hold_initial_cycle` until then.)

## Lifecycle

- Hold builds the runner in `setup()` (unchanged) and stops it in a new
  `HoldMode.teardown(self, ptemp)` → `self._runner.stop()` (guard for a `None`
  runner from a failed build). `ControlMode.run()` calls `teardown` on every
  loop-exit path, so a normal end/mode-change/error stops the thread and joins it.
- The thread is `daemon=True` purely as a safety net for the one path `teardown`
  cannot cover: an exception propagating out of `run()` (its main loop is not
  wrapped in `try/finally`). Explicit `stop()`+`join` is the real mechanism; the
  daemon flag only guarantees a stray thread can never hang process shutdown.
  This is the idiomatic worker-thread pattern, not the masking that the
  Process_Monitor fixture was.
- `SyncControllerRunner` gains a no-op `stop(self)` so `HoldMode.teardown` is
  uniform regardless of which runner it holds.

## Testing

- **`ThreadedControllerRunner` in isolation** (`tests/test_threaded_runner.py`),
  synchronized on events/conditions, never on wall-clock sleeps:
  - submit a temp → wait (bounded) until `latest()` reflects a solve of that
    temp against a deterministic fake core → assert the `NormalizedOutput`.
  - **non-blocking proof:** a fake core whose `update()` blocks on an event; assert
    `latest()` returns promptly (the last snapshot) while a solve is in flight.
  - `set_target` / `reconfigure` are picked up by the thread (observe via the fake
    core) without touching the running core from the main thread.
  - `controller_state()` returns the thread-published snapshot.
  - **`stop()` terminates the thread:** `stop()` then `thread.join`; assert
    `not thread.is_alive()` (the Process_Monitor regression guard).
  - `wants_async()` / `commands_fan()` / `control_period()` return the cached
    core values.
- **`build_runner` selection** (`tests/test_sync_runner.py` or a new test): a
  fake core with `wants_async()=True` yields a `ThreadedControllerRunner`;
  `False` yields a `SyncControllerRunner`. Stop any threaded runner the test
  builds.
- **`wants_async` capability** default False on `ControllerBase`, True on the MPC
  (`tests/test_mpc_integration.py`).
- **Hold behavior-neutral for sync:** the golden oracle injects
  `FakeControllerRunner` (sync), so moving `submit` to every tick must not change
  any golden assertion. Add `submit`/`stop` to `FakeControllerRunner` if missing
  (record submitted temps already exists; add a no-op `stop`). Confirm the golden
  file is unchanged.

## Files

- `controller/base.py` — add `ControllerBase.wants_async()` (default False).
- `controller/mpc.py` — override `wants_async()` → True.
- `controller/runtime/runner.py` — abstract `wants_async` + `stop` on the ABC;
  `SyncControllerRunner.wants_async`/`stop`; new `ThreadedControllerRunner`;
  `build_runner` selection.
- `controller/runtime/modes/hold.py` — `submit` every tick; new `teardown` →
  `runner.stop()`.
- `tests/fakes/runner.py` — `wants_async` kwarg + `stop` no-op on the fake.
- `tests/test_threaded_runner.py` (new); additions to `tests/test_sync_runner.py`,
  `tests/test_mpc_integration.py`.
- Docstrings: `runner.py` module docstring and `controller/runtime/README.md`
  "Documented follow-up" section updated to describe the shipped threaded runner.

## Out of scope

- Threading any non-MPC controller.
- Changing `control_period`, the MPC solver, or `normalize_controller_output`.
- Wrapping `ControlMode.run()`'s loop in `try/finally` (the daemon flag covers
  the exception path; a broader teardown-guarantee change is separate).
