# `controller/runtime` — the control process

This package holds the PiFire control loop after the controller/display
separation. The goals of that refactor: the **display and controller run as
separate processes**, the control loop is **testable in isolation**, and the
existing runtime behavior is **preserved**.

## Two-process model

PiFire runs as three independent supervisor programs (see
`auto-install/supervisor/`):

| Process | Entry point | Role |
|---|---|---|
| `control` | `control.py` | Reads probes, drives the grill hardware, runs the mode state machine. **Headless** — constructs no display. |
| `display` | `display_process.py` | Renders status to the physical display. Optional; the controller runs without it. |
| `webapp` | Flask/Gunicorn | Web UI. |

The controller and display never call each other. They communicate **only
through the shared SQLite datastore**:

- The controller writes `status` / `current` (probe temps, setpoints, mode) and
  pushes display commands onto the **`control:displayq`** queue
  (`('text', <str>)`, `('clear', None)`, `('splash', None)`).
- `DisplayFeeder` (`display_process.py`) polls `status`/`current` and drains
  `control:displayq`, calling the display driver. It holds no controller state.

Because the display just reads from the datastore like any other consumer, it
can be restarted, disabled, or replaced without touching the controller.

## Testability seams

The control loop takes a `ControllerContext` (`context.py`) instead of reaching
for module globals. The context bundles:

- **`store`** (`store.py`) — all datastore access behind a `Store` ABC.
  `SqliteStore` is the only production code that touches `common.common`'s
  global SQLite-backed accessor functions; `InMemoryStore` is the hermetic
  test double. A parity suite (`tests/unit/datastore/test_sqlite_store_parity.py`) and an
  end-to-end suite (`tests/e2e/`) pin the two to identical semantics against
  the real SQLite datastore.
- **`clock`** (`clock.py`) — `RealClock` in production, `ManualClock` in tests,
  so timers and sleeps are deterministic.
- **`notifications`** (`notifier.py`) — `LiveNotifier` in production,
  `FakeNotifier` in tests.
- **`devices`** (`context.py` / `devices.py`) — grill platform, probes, distance
  sensor; built by `build_devices()`. `build_display()` builds the display for
  the display process.

## Control flow

- **`Controller`** (`controller.py`) is the outer loop. `Controller.tick()` is
  exactly one loop iteration (switch poll, notifications/timers/hopper/settings
  handling, and the mode-dispatch block); `Controller.run()` is `setup()` +
  `while True: tick()`. Its orchestration is pinned by
  `tests/characterization/test_controller_loop_golden.py`.
- **Mode handlers** (`modes/`) are a template-method state machine. `ControlMode`
  (`modes/base.py`) defines the shared per-cycle skeleton; each mode
  (Monitor, Manual, Prime, Startup, Reignite, Smoke, Hold, Shutdown) overrides
  hooks. The inner work cycle is pinned by
  `tests/characterization/test_modes_golden.py` (the golden-master oracle).
- **Pure logic** (`logic/`) — safety, cycle, smartstart, pwm, fan — is extracted
  as side-effect-free functions with direct unit tests.
- **`runner.py`** — `SyncControllerRunner` computes the PID/MPC control output
  inline each cycle; `ThreadedControllerRunner` runs it on a background thread
  instead.

## Controller execution: sync vs threaded

The `ControllerRunner` seam (`runner.py`) supports two execution strategies,
and `build_runner()` picks between them based on the controller core's
`wants_async()`:

- **`SyncControllerRunner`** runs the underlying controller module's
  `update()` inline on submit/latest — control math and probe-read cadence
  are the same cadence. This is the default for controllers that don't
  request async execution (e.g. PID).
- **`ThreadedControllerRunner`** runs the core on a background thread at its
  own control period, decoupling an expensive solve (e.g. MPC) from the
  loop's probe-read cadence. `submit()`/`latest()` are non-blocking snapshots
  guarded by a lock; the thread mutates the running core. `HoldMode` submits
  the latest probe reading every tick and calls `stop()` on the runner during
  teardown to join the background thread cleanly.
