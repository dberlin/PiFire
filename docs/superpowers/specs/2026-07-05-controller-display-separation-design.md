# Controller / Display Separation & Controller Refactor — Design

**Date:** 2026-07-05
**Status:** Approved (design); ready for implementation planning

## Goal

Make the PiFire control process and the display fully separable so they can run
as independent OS processes, communicating only through Valkey. As part of the
same effort, clean up the controller so it is simpler, clearer, and readable,
**without changing its runtime behavior**, and make it genuinely testable by
adding tests during the refactor.

Hard requirements:

- **Retain current functionality** of the controller exactly.
- Controller must run fully **headless** — no display present, no display
  handle, unaffected if the display program is absent or crashes.
- The refactored controller must be **unit-testable without live hardware or a
  live Valkey**, with an additional **end-to-end suite against a real Valkey**.

## Current-state findings (why this is tractable)

- `control.py` is a 1695-line monolith: an ~870-line `_work_cycle()`,
  plus `_recipe_mode`, `_next_mode`, helpers, and a ~535-line `__main__` block
  that does all hardware init (grill platform, probes, display, distance) and
  runs the main mode-dispatch loop.
- The **display is already ~90% decoupled**. Every display spawns its own
  thread/process (`_display_loop`). Modern "flex" displays read
  `read_current()`/`read_status()` straight from Valkey and write commands back
  via `write_control(origin='display')`; for them `display_status()`,
  `display_text()`, `clear_display()` are already **no-op stubs**.
- **Legacy** displays (`base_240x320`, `base_240x240`, `base_320x480`) render
  from `self.in_data`/`self.status_data`, which are populated **only** by the
  control loop calling `display_status()`. They do not read Valkey themselves.
- Remaining controller→display coupling is 10 call sites (`control.py:304, 313,
  702, 713, 722, 961, 1023, 1560, 1587`). Everything `display_status` pushes is
  *also* written to Valkey (`write_status`/`write_current`); only the transient
  `text`/`clear` signals are not.
- The `common` module opens a **module-level Valkey connection at import time**
  (`common/common.py:57`), and `control.py` does `from common import *`. This is
  the core testability blocker: importing the controller today requires Valkey.
- Deployment is supervisord; today only `[program:control]` exists, and it
  launches the display internally. No separate display program.

## Key decisions

1. **Two independent processes**, controller fully headless-capable. New
   `display.py` entry point + `[program:display]`; controller stops constructing
   or referencing any display. Communication only via Valkey. The display
   program is optional — the controller does not depend on it.
2. **Dedicated `control:displayq` Valkey queue** for transient display signals
   (`text`, `clear`, `splash`), mirroring the existing `control:systemq`
   pattern. Bulk display data continues to flow through the existing
   `status`/`current` Valkey keys.
3. **Decompose `_work_cycle` into mode handlers** using a template-method state
   machine (Approach A).
4. **Inject a context object** (devices + store + notifier + loggers + clock).
   In-memory fakes for fast unit tests; a parallel **real-Valkey E2E** suite
   proves store parity.
5. **Replace `direct_write` boolean with a required `WriteKind` enum**
   (`OVERWRITE` / `MERGE`) — global sweep across all ~156 call sites; argument
   required so a missed conversion fails loudly (`TypeError`) rather than
   silently misbehaving.
6. **`ControllerRunner` seam** for the temperature controller (PID/MPC/etc.):
   ship a synchronous implementation now (== today's behavior, deterministic,
   testable); document a threaded implementation as a fast-follow. Neutralize
   controller-specific local names; leave persisted settings keys intact.

## Architecture

```
┌─────────────────────┐         Valkey          ┌─────────────────────┐
│  control.py (proc)  │  control / status /     │  display.py (proc)  │
│                     │  current / metrics /    │                     │
│  Controller         │  pellet + queues:       │  DisplayFeeder      │
│   ├ device factory  │  ──────────────────────▶│   ├ device factory  │
│   ├ ControllerCtx   │  control:systemq/o      │   ├ reads status/   │
│   └ ControlMode ×N  │  control:displayq  ────▶ │   │   current       │
│      (state machine)│                         │   └ drains displayq │
└─────────────────────┘                         │      → display API  │
   grill/probes/dist                            └─────────────────────┘
   (NO display handle)                             display device only
```

### Module layout

| File | Purpose |
|---|---|
| `control.py` | Slim entry point (~80 lines): build context, run `Controller`. |
| `controller/runtime/controller.py` | `Controller` orchestrator: main mode-dispatch loop (today's `__main__` while-loop). |
| `controller/runtime/context.py` | `ControllerContext` — devices + store + notifier + loggers + clock. |
| `controller/runtime/store.py` | `Store` interface + `ValkeyStore` (wraps `common.py`) + `InMemoryStore` (tests); `WriteKind` re-exported from `common`. |
| `controller/runtime/devices.py` | `build_devices()` factory — grill/probe/dist (and display, for `display.py`) init. Shared by both processes. |
| `controller/runtime/runner.py` | `ControllerRunner` / `SyncControllerRunner` (+ future `ThreadedControllerRunner`). |
| `controller/runtime/modes/base.py` | `ControlMode` template (shared loop skeleton). |
| `controller/runtime/modes/*.py` | One module per mode. |
| `controller/runtime/logic/{cycle,smartstart,pwm,safety,fan}.py` | Pure decision/arithmetic functions. |
| `display.py` | New entry point: build display device, run `DisplayFeeder`. |
| `auto-install/supervisor/display.conf` | New `[program:display]`; `control.conf` unchanged. |

`controller/` already holds the temperature-controller algorithms (PID/MPC);
process-runtime code lives in the new `controller/runtime/` subpackage to avoid
conflating the two.

## The `WriteKind` enum (Decision 5)

`common.py` gains:

```python
class WriteKind(Enum):
    OVERWRITE = "overwrite"   # replace control:general wholesale (was direct_write=True)
    MERGE     = "merge"       # queue a partial change, deep-merged on execute (was direct_write=False)

def write_control(control, kind: WriteKind, origin="unknown"):   # kind REQUIRED, positional
    global cmdsts
    if kind is WriteKind.OVERWRITE:
        cmdsts.set("control:general", json.dumps(control))
    elif kind is WriteKind.MERGE:
        control["origin"] = origin
        cmdsts.rpush("control:write", json.dumps(control))
    else:
        raise TypeError(f"write_control: kind must be WriteKind, got {kind!r}")
```

All ~156 call sites (52 `direct_write=True` → `OVERWRITE`; 104 implicit-merge →
`MERGE`) across control, displays, webapp blueprints, and notify are converted
explicitly. No default: a missed site raises `TypeError` the moment it runs.
`execute_control_writes()` semantics (drain `control:write`, `deep_update` each
partial into current control, write back `OVERWRITE`) are unchanged.

## The context object & store (Decision 4)

```python
@dataclass
class ControllerContext:
    devices: Devices          # grill_platform, probe_complex, dist_device
    store: Store              # all Valkey-backed state access
    notifications: Notifier   # send_notifications / check_notify / get_notify_targets
    clock: Clock              # now() / sleep() — injected, never wall-clock directly
    event_log: Logger
    control_log: Logger
```

`Store` (ABC) covers exactly the enumerated state surface — nothing more:

- Control: `read_control`, `write_control(data, kind, origin)`,
  `execute_control_writes`, `default_control`
- Settings: `read_settings`
- Status/current: `read_status`, `write_status`, `read_current`, `write_current`
- History/metrics: `read_history`, `write_history`, `read_metrics`,
  `write_metrics`, `write_tr`
- Pellet: `read_pellet_db`, `write_pellet_db`
- Errors/misc: `read_errors`, `write_errors`, `write_generic_key`
- Queues: `system_commands()`, `system_output()` (existing), and
  **`display_commands()`** (new `control:displayq`)

Implementations:

- **`ValkeyStore`** — thin pass-through to the existing `common.py` functions.
  The *only* production code importing those globals, quarantining the
  module-level Valkey connection to one place.
- **`InMemoryStore`** — dicts + `collections.deque` queues. Must replicate
  `OVERWRITE`/`MERGE` and the `deep_update`-on-execute merge semantics so tests
  behave like production.

`Notifier` wraps `send_notifications`/`check_notify`/`get_notify_targets`; the
test double records calls (assert "Grill_Error_02 was sent") without a real
backend. `Clock` abstracts `time.now()`/`time.sleep()`; production uses a real
clock, tests use `ManualClock`.

The **E2E suite** builds the context with the real `ValkeyStore` + `Notifier`
against a live `valkey-server` and fake devices, exercising the true
read/write/queue/merge semantics.

## Mode handlers — template-method state machine (Decision 3)

`ControlMode` (base) owns the shared loop skeleton; subclasses fill only
differences. **The base contains no `if mode == …` conditionals** — mode
specifics come exclusively through hooks.

```python
class ControlMode:
    name: str
    def __init__(self, ctx: ControllerContext, state: WorkCycleState): ...

    # --- template hooks, overridden per mode ---
    def setup(self): ...                       # pre-loop device state, cycle params, runner init
    def setup_safety(self): ...                # pre-loop safety (mode-specific)
    def on_tick(self, now): ...                # per-iteration mode logic
    def check_safety(self, now): ...           # per-iteration safety (mode-specific; default no-op)
    def should_exit(self, now) -> bool: ...    # mode-specific exit conditions
    def status_fragment(self) -> dict: ...     # mode-specific status fields (default {})
    def teardown(self): ...                    # mode-specific post-loop cleanup

    # --- shared skeleton, NOT overridden ---
    def run(self):
        self.setup(); self.setup_safety()
        while self._active():
            now = self.ctx.clock.now()
            self._drain_control_and_system_commands()
            if self._mode_change_requested(): break
            self._apply_settings_updates()
            self._handle_manual_overrides()
            self._read_probes_and_write_current()
            self.on_tick(now)
            self._universal_safety(now)          # ONLY the max-temp cutoff
            self.check_safety(now)               # mode-specific safety
            self._publish_status_and_history(now)
            if self.should_exit(now): break
            self.ctx.clock.sleep(0.05)
        self.teardown()
        self._final_cleanup()
```

Per-mode responsibilities:

| Mode | setup | on_tick | should_exit |
|---|---|---|---|
| `StartupMode` / `ReigniteMode` | igniter+fan+auger on, smoke cycle params, smart-start | auger cycle | startup timer / exit temp |
| `SmokeMode` | smoke cycle params | auger cycle, smoke-plus fan | (mode change only) |
| `HoldMode` | init `ControllerRunner`, hold cycle params | runner output → cycle ratio, PWM/fan-assist, smoke-plus, lid-open | (mode change only) |
| `ShutdownMode` | fan on, power off | — | shutdown duration |
| `PrimeMode` | auger on, prime duration, optional igniter | auger cycle | prime elapsed |
| `MonitorMode` / `ManualMode` | power/fan off | manual overrides only | (mode change only) |

`RecipeMode` stays a thin orchestrator (today's `_recipe_mode`) that *invokes*
the above modes step by step; it is not a `ControlMode` subclass.

The `Controller` orchestrator replaces the `__main__` dispatch loop, preserving
the exact Prime-on-startup, next-mode, reignite, and Stop/Error transitions.

### Safety is per-mode, not uniform

Only the **max-temp cutoff** (`control.py:960`) is universal → lives in the base
skeleton (`_universal_safety`). The **startup-temp / reignite-or-error** check
(`:710-727`) is Smoke/Hold only; the **`afterstarttemp` bookkeeping**
(`:708-709`) is Startup/Reignite only; the pre-loop counterpart (`:288-317`) has
the same mode split. These are implemented via `setup_safety()`/`check_safety()`
overrides so Prime/Monitor/Manual/Shutdown never accidentally inherit
Smoke/Hold's flameout logic.

### Status fragments (no base conditionals)

The shared publisher builds only universal fields, then
`status.update(self.status_fragment())`. Hold owns `primary_setpoint`,
`lid_open_detected`, `lid_open_endtime`; Prime owns `prime_duration`,
`prime_amount`; etc. The base never references these fields.

### `WorkCycleState` replaces fragile `locals()` checks

Conditionally-defined loop variables (`CycleRatio`, `RawCycleRatio`,
`prime_duration`, `prime_amount`, `LidOpenDetect`, `LidOpenEventExpires`) become
explicit, typed fields on a `WorkCycleState` (default `0`/`None`), removing the
`'CycleRatio' in locals()` / `'RCR' in locals()` inspection (`control.py:640-641,
662`).

## Pure-logic modules (Decision 3 support)

The arithmetic/decision logic extracts into pure functions — plain dict/scalar
inputs, scalar/decision outputs, no device or Valkey I/O. Mode handlers *decide*
via these (tested) functions, then *actuate* via faked devices. That
decision/actuation split is what makes the loop both testable and readable.

```python
# logic/cycle.py
def smoke_cycle_times(cycle_data) -> CycleTimes            # control.py:234-244, 427-437
def hold_initial_cycle(cycle_data) -> CycleTimes           # :246-251
def hold_update_cycle(output, cycle_data, *, lid_open) -> CycleTimes   # :553-587

# logic/smartstart.py
def select_profile(startup_temp, temp_range_list) -> int   # :326-336
def profile_cycle(profile, cycle_data) -> tuple[CycleTimes, startup_timer, metrics_bits]  # :338-353

# logic/pwm.py
def hold_duty_cycle(setpoint, ptemp, pwm_settings) -> int  # :776-791
def ramp_params(smoke_plus, pwm_settings) -> RampParams    # :863-867

# logic/safety.py
def startup_temp_bounds(ptemp, safety_settings) -> int     # :293-296
def evaluate_flameout(ptemp, startup_temp, retries) -> SafetyVerdict   # OK|REIGNITE|ERROR :711-727
def over_max_temp(ptemp, safety_settings) -> bool          # :960

# logic/fan.py
def clamp_duty(duty, pwm_settings) -> int                  # :56-64
def fan_assist_times(output, cycle, smoke_plus, *, s_plus) -> FanTimes  # :804-818
def smoke_plus_decision(ptemp, smoke_plus_settings, ...) -> FanAction   # :839-872
```

`normalize_controller_output` stays in `controller/base.py`; these modules sit
under `controller/runtime/logic/`.

## `ControllerRunner` seam (Decision 6)

```python
class ControllerRunner(ABC):
    def set_target(self, setpoint): ...
    def submit(self, temp): ...                  # feed newest measurement
    def latest(self) -> NormalizedOutput: ...    # most recent (output, fan_cmd), normalized
    def reconfigure(self, settings, control): ...

class SyncControllerRunner(ControllerRunner):
    """Computes inline; deterministic; == today's behavior. Ships now."""

class ThreadedControllerRunner(ControllerRunner):
    """Same interface, compute on its own timer; latest() returns a snapshot.
       Fast-follow — pays off for expensive controllers (MPC). Not on this
       refactor's critical path."""
```

The work cycle only calls `runner.submit(ptemp)` / `runner.latest()`; it no
longer knows whether compute is inline or threaded. Rationale for deferring the
threaded implementation: PID `.update()` is trivial (threading is pure risk);
MPC `.update()` can block the safety/actuation loop, which is the real
motivation — but thread concurrency (shared controller state, cross-thread
`__dict__` reads for MQTT at `control.py:604`, reinit races at `:439-446`,
nondeterministic tests) conflicts with this refactor's retain-functionality and
testability goals. The seam captures the benefit's option value at near-zero
risk.

**Naming:** neutralize controller-specific locals — `pid_output` →
`controller_output`, `pid_data` → `controller_data`, `mpc_fan_duty` →
`controller_fan_duty`, `ControlFanPid` → `fan_assist`. Persisted settings keys
(`FanPidEnabled`, the `controller` config blocks) are on-disk state — left
unchanged to protect functionality (renaming them is a separate migration).

## Display separation (Decision 1 & 2)

**Producer (controller):** the 10 `display_device.*` call sites become
`control:displayq` pushes. They originate in both the mode handlers and the
orchestrator, so both use the same queue via the store:

```python
ctx.store.display_commands().push(('text', 'ERROR'))   # was display_device.display_text('ERROR')
ctx.store.display_commands().push(('clear', None))      # was display_device.clear_display()
```

`display_status(in_data, status_data)` (`:702, :1023`) is **not** pushed — that
data already lands in Valkey via `write_current`/`write_status`. The queue
carries only transient `text`/`clear`/`splash` signals.

**Consumer (`display.py`):** a thin `DisplayFeeder` reusing `build_devices()`
(display only), driving the **unchanged** display API:

```python
class DisplayFeeder:
    def __init__(self, display, store, clock):
        self.display, self.store, self.clock = display, store, clock
    def run(self):
        while True:
            in_data, status = self.store.read_current(), self.store.read_status()
            if in_data and status:
                self.display.display_status(in_data, status)   # legacy: renders; flex: no-op
            for cmd, arg in self.store.display_commands().drain():
                {'text': lambda: self.display.display_text(arg),
                 'clear': self.display.clear_display,
                 'splash': self.display.display_splash}[cmd]()
            self.clock.sleep(0.1)
```

Flex displays self-serve from Valkey and no-op the pushed calls; legacy displays
are driven exactly as the control loop drives them today. **Zero changes to any
display module.**

**Lifecycle:** new `[program:display]` (`autostart`/`autorestart`). Controller
constructs no display; if the display program is absent or crashes, the
controller is wholly unaffected. Ordering preserved: controller pushes
`text:ERROR` then, on the Stop transition, `clear` — drained in order, so the
display shows `ERROR` then clears exactly as before (`:1560/:1587`).

## Testing strategy

1. **Characterization tests first (golden master).** Before decomposing, pin
   today's `_work_cycle` behavior with fakes + `InMemoryStore` + `ManualClock`,
   recording the exact sequence of device calls and control/status writes.
   Scenarios: Startup exit-on-temp/timer, smart-start profile selection, Smoke
   auger cycling, Hold controller cycle + PWM fan + smoke-plus + lid-open,
   flameout→Reignite, flameout→Error, max-temp→Error, Prime duration, Shutdown
   duration, Monitor/Manual overrides, Recipe step progression + reignite retry.
   These same assertions run against both the pre-refactor loop and the
   post-refactor handlers — proving equivalence.
2. **Pure-logic unit tests** — exhaustive, no fakes, cover arithmetic/decision
   edges (clamps, boundaries, retries=0 vs >0). Target ~100% branch coverage.
3. **Two-tier integration tests:**
   - **Fast tier** — handlers + `InMemoryStore` + fake devices + injected clock.
     Deterministic, always run.
   - **E2E tier** — same scenarios against a real `valkey-server` via
     `ValkeyStore`, proving `OVERWRITE`/`MERGE`/`deep_update`/queue parity.
     Gated by a `valkey` availability fixture (`skipif`/opt-in marker) so the
     default suite stays hermetic.
4. **Fakes** (alongside existing `tests/_fake_hid.py`): `FakeGrillPlatform`
   (records all output calls, scriptable status), `FakeProbes` (scripted temp
   sequence), `FakeDistance`, `FakeNotifier` (records notifications),
   `FakeControllerRunner` (scriptable output), `ManualClock`.

**Clock injection** is the one unavoidable loop touch: the base skeleton takes
`clock` from the context (`clock.now()`/`clock.sleep()`); production passes a
real clock, tests a `ManualClock`.

## Build sequence

Ordered so the suite is green at every step and structure only moves after the
safety net exists. Each step is independently committable.

1. **`WriteKind` enum + global sweep** — required arg; convert all ~156 sites.
2. **`Store` seam + `ValkeyStore` + `InMemoryStore`** — repoint `control.py` at
   an injected store, no logic change. Loop now runnable under `InMemoryStore`.
3. **Fakes + `ManualClock` + characterization tests** against the *current*
   loop. The equivalence oracle — green before any decomposition.
4. **`ControllerContext` + `build_devices()` factory** — extract hardware init.
5. **`ControllerRunner` seam** — `SyncControllerRunner`; replace inline
   `.update()` + normalization; apply neutral naming.
6. **Pure-logic modules + unit tests** — extract `logic/*`; call from the
   current loop. Characterization tests stay green.
7. **Mode-handler decomposition** — `ControlMode` base + per-mode subclasses;
   `Controller` orchestrator. Run characterization tests against the new
   handlers. Add fast-tier integration tests.
8. **Display separation** — 10 call sites → `control:displayq`; add `display.py`
   + `DisplayFeeder` + `display.conf`. Controller stops constructing a display.
9. **E2E tier** — Section-6 scenarios against real `valkey-server`.
10. **Slim `control.py` entry point + docs** — ~80 lines; note
    `ThreadedControllerRunner` as the documented follow-up.

## Out of scope / follow-ups

- `ThreadedControllerRunner` implementation (seam ships; threaded compute later,
  primarily for MPC).
- Renaming persisted settings keys (`FanPidEnabled`, `controller` config blocks)
  — requires a data migration.
- Any change to display rendering logic or display modules themselves.
