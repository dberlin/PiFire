# Shared-Mode Timers Clock Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate event-wall provenance from process-local control duration across every shared mode, its runner, physical metrics, and runtime status without leaving mixed-clock callers.

**Architecture:** `Clock` exposes explicit wall and monotonic methods; the shared loop supplies a monotonic `now` to all control hooks and captures wall values at publication boundaries. Independent-axis fakes cover both correction directions. A common boot/runtime/suspend provenance contract fences control before probes and positive actuation; persisted user/recipe timers remain the peripheral authority's responsibility, not raw mode deadlines.

**Tech Stack:** Python runtime ABC/dataclasses, pytest, existing fake platform/context/store, shared trace/trajectory and status contracts.

**Spec:** `docs/superpowers/specs/2026-09-08-clock-domain-separation-design.md`

**Integrated baseline:** The coupled learning implementation is now in the working tree: trace 10, observation 4, evidence 6, database 13, required independent wall endpoints, and callable clocks through Hold/runner factories and reconfiguration. References below to Main's “in-flight” work describe the drafting baseline, not remaining implementation. Reconcile those steps against the shared spec before executing; the destination shared `Clock` API and remaining timers are still proposals.

## Global Constraints

- `Clock.wall_time() -> float`: epoch seconds. `Clock.monotonic() -> float`: same-boot steady seconds. `sleep(seconds)` keeps its current role. Main's approved coupled boundary may temporarily retain `Clock.now()` with its wall meaning for untouched callers; migrated physical producers must use monotonic explicitly. This later shared-mode plan removes `now()` after all consumers migrate; no permanent compatibility alias.
- Proposal only: this document does not authorize implementation. Findings come from source inspection, not executed validation; no tests, build, formatter, linter, or runtime check was run during this planning task.
- `ManualClock` has independently movable wall and monotonic axes; ordinary advance/sleep move both, `jump_wall(delta)` moves wall only. Never infer a clock domain from number size or silently convert wall timestamps with a fixed offset.
- Preserve wall logs, history, trace envelopes, metrics start/end timestamps, and public startup event timestamps as wall provenance. Derive physical elapsed time only from explicit monotonic instants.
- Preserve strict evidence admission, durable cook identity, seed requirements, replay validity, mutation expectations and public contracts. Keep `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.` exact and unique. PID-SP `Controller.update()` continues returning raw signed demand; physical allocation remains bounded.
- Boot/runtime identity is required for persisted monotonic freshness; unknown identity cannot be live. Latest evidence means durable causal order, not highest wall time. Resume is not active-control evidence.
- Follow `AGENTS.md`, the jujutsu skill, and exact-revision gate. No raw Git, direct `jj git push`, hand-edited release evidence, or inference that `jj describe/new` ran commit checks.

## Dependencies and Single Landing Gate

- `2026-09-08-pulse-scheduling-clock-migration.md`: owns pulse physical intervals and unknown-gap invalidation; consumes this plan's Clock and monotonic mode tick. Both plans touch `modes/hold.py`; serialize that shared edit boundary under one integration owner.
- `2026-09-08-pid-clock-migration.md` and `2026-09-08-pid-sp-clock-migration.md` own remaining controller clock constructor and dt/history clock integration, preserving numerical behavior. This plan owns `runner.py` propagation through builders and reconfiguration. PID-SP completed-frame history, pulse scheduling, and shared-mode clock selection **must deploy together** without mixed physical axes.
- **Main integration in flight, not completed:** the user approved coupled learning capture, replay/evidence semantics and necessary pulse/PID-SP timestamp producers now. Rebase against that verified integration rather than duplicate edits. The shared spec defines trace 9→10, trajectory observation schema 4 and required `FrameObservation.wall_start_ms/wall_end_ms`, keeping physical frame bounds monotonic. All trajectory hooks and Hold trace publication must consume that actual integrated contract; no new schema version is chosen by this plan.
- `2026-09-08-probe-clock-contract-migration.md`: probe producer/consumer domain contract; shared mode passes monotonic observation time and physical excitation duration, preserving hardware fault safety ordering.
- `2026-09-08-peripheral-clock-migration.md`: owns `common/clock_domain.py`, heartbeat/watchdog safety, persisted recipe/user timer authority and all UI/display consumers. Shared mode owns duration/status production and calls that timer authority; no independently restored wall deadline.
- Release only when all current-contract producers/consumers/fakes and the Main-approved trace/learning changes are ready in one revision. Intermediate commits are development units, not safe deployable mixed versions.

**Approval boundary:** This remaining shared-mode migration is a proposal. Task 4's suspend detector, thresholds, safe-stop/restart policies and related assertions require separate explicit approval; they are not authorized clock-correction work. The suggested 1.0-second active-loop gap is not an approved production limit. PID/Smith predictor numerical policy, derivative behavior and interval coverage stay unchanged except for clock choice.

## Exact Source and Consumer Inventory

Use LSP references before modifying exported symbols, and structural search for dynamic builders, modes and test doubles. Line locations are research hints; re-read before edits. The following inventory is the implementation starting set, not permission to skip newly discovered consumers.

| File / symbols | Monotonic duration state or action | Wall/public boundary |
|---|---|---|
| `controller/runtime/clock.py`: `Clock`, `RealClock`, `ManualClock` | Introduce explicit methods; remove `now`; dual-axis fake | `RealClock.wall_time=time.time` |
| `controller/runtime/context.py`: `ControllerContext.clock`; `controller/runtime/controller.py`: setup/work-cycle/idle branches, `_hopper_refresh_time` | Shared clock injection; hopper cadence; probe policy timestamps; continuity lifecycle | Timer notifications consume peripheral authority; do not compare monotonic to persisted `timer.end` |
| `controller/runtime/state.py`: `Timers`, `ControllerState`, `FanState`, `LidState`, `StartupState`, `PrimeState`, `WorkCycleState` | `Timers.start_time/auger_toggle/display_toggle/hopper_toggle/eta_toggle/temp_toggle`, controller cycle/physical interval start, fan cycle/update, lid expires, manual override deadline map | New separately named wall start capture; startup timer/prime duration are duration scalars, not timestamps |
| `controller/runtime/modes/base.py`: `run`, `_auger_cycle_tick`, `_smoke_plus_fan_tick`, `_apply_manual_overrides`, `_read_probes_with_excitation`, `_build_status_data`, `_trajectory_clock_pair`, trajectory event/cook identity/history-clear hooks | One monotonic tick; pre-loop timers; physical ON/OFF accounting; excitation elapsed; ETA/history/display cadence; safety hook instants | Explicit wall captures for metric end, status epoch start, trajectory event pairs, event provenance |
| `controller/runtime/modes/base.py`: `_setup_recipe_triggers`, `_handle_recipe_end`, `notifications.check` routes | Consume authoritative persisted timer remaining/expiry | Peripheral timer schema decides creation/pause/resume/restart; `timer.start/end` cannot become monotonic by replacement |
| `controller/runtime/modes/smoke.py`: setup/on_tick/on_publish; `startup.py`: setup_safety/on_tick/should_exit/_write_startup_timestamp; `reignite.py`; `shutdown.py`: should_exit/teardown; `prime.py`: on_tick/should_exit; `manual.py`, `monitor.py` | Smoke/Startup/Reignite auger cadence; startup/shutdown/prime elapsed; manual/lid/fan behavior and teardown | `control['startup_timestamp']` stays epoch event timestamp; Reignite retains current no-reset policy |
| `controller/runtime/modes/hold.py`: setup/reconfiguration/on_tick/lid/manual/status/teardown; `_HoldTickContext`, `_HoldTeardownState` | Controller cadence, pulse configure/reset/advance, seed/applied output, lid expiry, PWM refresh, physical teardown cutoff | `_HoldTickContext.wall_time` for trace events; restore/evidence timestamps wall; pulse plan owns interval split |
| `controller/runtime/runner.py`: `build_runner`, `_build_core`, `_wrap`, `SyncControllerRunner.reconfigure`, `ThreadedControllerRunner.reconfigure` | Core and runner monotonic callables share injected Clock; reconfigure retains it | Runner completion wall callable uses same Clock.wall_time; PID-SP wall evidence remains wall |
| `controller/runtime/heartbeat.py`: `stamp_control_heartbeat`; `controller/runtime/store.py`: current/history/metrics publication | Heartbeat throttle monotonic (peripheral owner), elapsed metric field explicit | Heartbeat IPC stamp and metrics start/end wall; preserve store write provenance |
| `controller/runtime/learning_trajectory.py`, `controller/runtime/actuation_delivery.py`, `controller/runtime/control_trace_session.py`, `common/learning_trajectory.py`, `common/persistence/learning_trajectory.py` | Monotonic physical capture and ordering with identity | Actual paired wall metadata, Main-owned migration; no domain guessing or wall-duration validation as physical truth |
| `common/persistence/history.py`, `common/app.py`, `file_mgmt/cookfile.py` | Expose/consume explicit new elapsed metric; never subtract wall start/end for duration | Existing calendar graph/export metadata remains wall; rollback may give wall end earlier than start |
| `display/_base_fixed.py`, `display/_base_flex.py`, `display/qtbackend.py`, `common/api_commands.py`, `common/persistence/transforms.py` | Peripheral-owned display/API projection consumes duration snapshots and freshness | Preserve epoch fields as provenance; never feed mode monotonic start into old wall subtraction |

**Fake/test clock cutover set:** `tests/unit/runtime/test_clock.py`; `tests/characterization/harness.py`, `_controller_harness.py`, `test_controller_loop_golden.py`; `tests/unit/runtime/conftest.py`, `test_control_mode_base.py`, `test_mode_metrics_staging.py`, `test_mode_settings_reload.py`, `test_smoke_learning_trajectory.py`, `test_hold_trajectory_seed.py`, `test_hold_orchestration.py`, `test_hold_control_trace.py`, `test_hold_pulse_scheduler.py`; `tests/unit/controller/test_heartbeat.py`; `tests/e2e/test_smoke_hold_learning_trajectory.py` (`_RealCookClock` and runner period gate), `test_thermocouple_inference_e2e.py` (sample/actuator/notifier times); `tests/ui/test_display_feeder.py`, `test_base_flex_dash_update.py`, `test_fixed_base_golden.py`, `test_fixed_drivers_methods.py`, `test_pygame_qt_drivers.py`, `test_qtbackend.py`; PID fake clocks in `tests/unit/controller/test_pid_sp.py` and `tests/characterization/test_pid_controllers_golden.py` belong to their respective plans. Preserve time-module fakes in distance tests that already expose `monotonic`: those are not `Clock.now` migration targets.

Source findings before Main's in-flight producer edits: `base.run` passed epoch wall `now` to auger toggles, manual deadlines, startup/shutdown exit and Hold scheduler; `hold.status_fragment` exported a lid expiry that UI subtracted from wall; `_trajectory_clock_pair` read raw system clocks instead of the injected Clock; `FramedPulseRuntime.reset` advanced before resetting, so ordinary reset at resume cannot erase a gap safely. The shared status `start_time` was also a wall countdown origin. A blanket clock replacement therefore changes exported semantics and can break UI, trace, probe and model-history consumers. Main's necessary learning producers are being corrected now; classify remaining callers after that integration instead of repeating obsolete edits.

### Task 1: Make the Clock API and all fake axes explicit

**Files:** Modify `controller/runtime/clock.py`, the fake/test clock cutover set above, `controller/runtime/context.py` where documentation or constructor typing requires it. Do not build a second competing test helper module.

**Interfaces:** `Clock.wall_time() -> float`, `Clock.monotonic() -> float`, `Clock.sleep(seconds: float) -> None`; `ManualClock(*, wall_start: float = 0.0, monotonic_start: float = 0.0)`, `advance(seconds)`, `jump_wall(delta)`.

- [ ] Replace `Clock.now` with the two abstract methods in the coordinated change; RealClock delegates directly to `time.time()` and `time.monotonic()`. No magnitude heuristic, no `now` alias, no auto wall-to-monotonic coercion.
- [ ] Implement the dual-axis fake using the existing module:

```python
class ManualClock(Clock):
    def __init__(self, *, wall_start: float = 0.0, monotonic_start: float = 0.0):
        self._wall = float(wall_start)
        self._monotonic = float(monotonic_start)

    def wall_time(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot move monotonic clock backwards")
        self._wall += seconds
        self._monotonic += seconds

    def jump_wall(self, delta: float) -> None:
        self._wall += delta
```

- [ ] Replace every `ManualClock(start=x)`/positional constructor with explicit axis arguments according to the caller: a deadline epoch fixture sets wall, a physical tick fixture sets monotonic, and fixtures intentionally wanting equal axes specify both. Migrate `_RealCookClock` with independent event wall values and monotonic sample offsets; runner period gate reads monotonic, not a renamed wall accessor.
- [ ] Add these assertions to `tests/unit/runtime/test_clock.py`; preserve the ordinary real-clock/sleep behavior test without pinning implementation method names:

```python
def test_manual_clock_wall_jump_does_not_advance_control_time():
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=10.0)
    clock.jump_wall(-3600.0)
    assert clock.monotonic() == 10.0
    assert clock.wall_time() == 1_799_996_400.0
    clock.sleep(2.0)
    assert clock.monotonic() == 12.0
    assert clock.wall_time() == 1_799_996_402.0
    clock.jump_wall(7200.0)
    assert clock.monotonic() == 12.0
```

- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_clock.py -q`; first observe failure for the missing API, then success. Whole-repository clock migration must be complete before integration, even though this focused API test can pass earlier.

### Task 2: Put mode timing, physical output and metrics on the shared axis

**Files:** Modify `controller/runtime/state.py`, `controller/runtime/modes/base.py`, `startup.py`, `shutdown.py`, `prime.py`, `smoke.py`, `reignite.py`, `hold.py`, `controller.py`, `tests/unit/runtime/test_control_mode_base.py`, `test_mode_metrics_staging.py`, `test_mode_settings_reload.py`.

**Interfaces:** Keep hook signatures `on_tick(now, ptemp, current_output_status)`, `check_safety(now, ptemp)`, `should_exit(now, ptemp)` but define `now` as monotonic seconds in their base contract. Existing `Timers.start_time` becomes internal monotonic; add `Timers.start_wall_time: float = 0.0`. Public status `start_time` remains epoch from `start_wall_time`. `metrics['elapsed_seconds']` is explicit physical duration, separate from epoch `starttime/endtime` milliseconds.

- [ ] At mode entry capture monotonic and wall separately. Initialize all internal toggle fields from monotonic, not wall. At each loop iteration capture monotonic once for all guards/manual/output/cadence branches and use this value when restamping toggles; capture wall separately only at provenance boundaries.

```python
start_time = ctx.clock.monotonic()
self.state.timers.start_time = start_time
self.state.timers.start_wall_time = ctx.clock.wall_time()
# Existing toggle initialization uses start_time unchanged.
# Each admitted iteration:
now = ctx.clock.monotonic()
# On normal terminal closure, after physical accounting:
self.state.metrics["elapsed_seconds"] = now - self.state.timers.start_time
self.state.metrics["endtime"] = ctx.clock.wall_time() * 1000
```

- [ ] Retain current `>` versus `>=` boundary policy for startup/shutdown/prime and shared non-Hold pulses; changing equality behavior is not necessary for clock repair. `augerontime += now - auger_toggle` uses physical monotonic edges. Preserve Hold's separate delivered-delta contribution; do not double-count it in shared teardown.
- [ ] Add non-Hold last observed output/monotonic bookkeeping so an ON pulse ending at normal teardown contributes its final observed interval exactly once, including Prime and manual output edges. Use one existing-mode helper `_account_auger_delivery(now: float, actual_auger_on: bool) -> None` called before an admitted command change and normal teardown. It integrates prior observed state over `now-last_observation`, updates last observation, and owns non-Hold metrics; remove the old per-OFF direct increment when adopting it. Hold remains on framed delivery accounting. On discontinuity, do not advance this helper to resume time: retain known accumulated seconds and mark metrics delivery incomplete.
- [ ] Stamp `control['startup_timestamp']` via `wall_time()`. Reignite still does not reset that public startup event. Set internal cook active-elapsed origin separately, so a wall correction does not reset or enlarge it. Do not reconstruct cook active time from an old epoch startup timestamp after reboot.
- [ ] Move `_excitation_last_read_at`, elapsed heat-on union calculation, probe policy setter times, hopper refresh, fan cycling/PWM refresh, manual override deadlines and lid expiry to monotonic. Keep thermocouple health preflight/after-setup/per-tick rejection before any positive manual override or `on_tick`; probe contract migration must land together.
- [ ] Change `_trajectory_clock_pair` to use `ctx.clock` rather than raw `time.*`; its exact return remains `(monotonic_ms, wall_ms)`. Main's learning capture must accept genuine wall discontinuities and no longer guess frame domains before this producer is enabled.
- [ ] Add a direct physical shared-mode test in `test_control_mode_base.py` using existing `_make_mode` helper (not imports from another collected test):

```python
@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_smoke_off_edge_uses_elapsed_control_time(jump):
    mode = _make_mode()
    mode.name = "Smoke"
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=10.0)
    mode.ctx.clock = clock
    mode.state.manual_override = {k: 0.0 for k in ("auger", "fan", "power", "igniter", "pwm")}
    mode.state.cycle.cycle_time = 20.0
    mode.state.cycle.ratio = 0.1
    mode.state.timers.auger_toggle = 10.0
    mode.state.metrics = {"augerontime": 0.0}
    mode.grill.auger_on()
    mode._account_auger_delivery(clock.monotonic(), True)
    clock.jump_wall(jump)
    clock.advance(1.0)
    mode._auger_cycle_tick(clock.monotonic(), mode.grill.get_output_status())
    assert mode.grill.get_output_status()["auger"] is True
    clock.advance(1.1)
    mode._auger_cycle_tick(clock.monotonic(), mode.grill.get_output_status())
    assert mode.grill.get_output_status()["auger"] is False
    assert mode.state.metrics["augerontime"] == pytest.approx(2.1)
```

- [ ] Add an actual `run()` test driven by a dual clock whose `sleep` applies one wall jump. Record fake-grill command instants; assert the above OFF edge and delivered seconds match the zero-jump run. Direct hook tests alone do not prove the shared loop selected the right clock.
- [ ] Add `test_partial_prime_teardown_counts_observed_delivery_once`, `test_manual_auger_edge_accounts_actual_not_requested_time`, and `test_wall_rollback_keeps_metrics_elapsed_positive`: respectively assert final known ON duration survives an early stop, requested OFF with actual ON counts observed exposure, and epoch end can precede epoch start while explicit elapsed stays correct. Unknown-gap delivery must remain incomplete, never converted to zero-known-complete.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_control_mode_base.py tests/unit/runtime/test_mode_metrics_staging.py tests/unit/runtime/test_mode_settings_reload.py tests/e2e/test_thermocouple_inference_e2e.py -q`.

### Task 3: Propagate one Clock through every runner rebuild

**Files:** Modify `controller/runtime/runner.py`, `controller/runtime/modes/hold.py`, `tests/fakes/runner.py`, `tests/unit/runtime/conftest.py`, `tests/unit/runtime/test_hold_orchestration.py`; core constructors belong to PID/PID-SP plans.

**Interfaces:** `build_runner`, `_build_core`, `_wrap` accept keyword-only `clock: Clock`; Hold supplies `clock=self.ctx.clock`. PID and PID-SP constructors accept keyword-only `clock=None` and store `_clock`, defaulting to `RealClock`. Runner existing `monotonic_clock`/`wall_clock` callables use the injected methods; runner stores the Clock for both reconfiguration paths. Non-PID core constructor signatures do not acquire an unused clock parameter.

- [ ] Propagate the clock through the existing factory chain without introducing another registry or clock adapter. Forward `clock` only into PID/PID-SP core construction and into runner time callables. Preserve existing model/estimator construction and admission.

```python
# In the existing PID/PID-SP selection branches of _build_core:
if controller_type in {"pid", "pid_sp"}:
    controller_kwargs["clock"] = clock
# At existing runner wrapper construction:
monotonic_clock=clock.monotonic,
wall_clock=clock.wall_time,
# Hold calls the existing builder with:
clock=self.ctx.clock,
```

- [ ] Store the clock on both synchronous and threaded wrappers and forward it on each reconfigure/core replacement. Existing reconfigure generation fences remain; a late old result cannot carry new authority because its time axes happen to match.
- [ ] Add `test_reconfigure_retains_dual_clock_control_axis`: use a real synchronous PID-SP runner built through `build_runner`, feed accepted completed frames, wall-jump, reconfigure, and feed a new-generation frame. Assert bounded runner duty and predictor completed-history boundaries match a no-jump run, completion wall reflects the correction, solve elapsed is monotonic, and stale old-generation results are rejected. This is a consumer behavior test, not an assertion that a constructor received a method object.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_hold_orchestration.py tests/unit/controller/test_pid_sp.py tests/characterization/test_pid_controllers_golden.py -q`. PID-SP plan supplies completed-frame regression and approved raw signed public-return assertions.

### Task 4: Proposed suspend/gap and terminal safety policy — separate approval required

**Files:** Modify `controller/runtime/modes/base.py`, `hold.py`, `controller.py`, `context.py`, `tests/unit/runtime/test_control_mode_base.py`, `test_hold_orchestration.py`; consume peripheral-owned `common/clock_domain.py` and pulse-owned invalidation hook.

**Interfaces:** Peripheral defines `RuntimeClockDomain(clock: Clock, *, boot_id: str | None, boottime: Callable[[], float])`, `.capture() -> ClockStamp`, `.rotate_runtime() -> str`; `continuity_lost(previous: ClockStamp, current: ClockStamp, *, max_active_gap_s: float | None = None) -> bool`. `ClockStamp` carries schema 1, boot/runtime identity, wall/monotonic capture and suspend offset. Shared mode adds `_on_control_discontinuity(last_observed_monotonic: float, reason: str) -> None`; Hold overrides it using `FramedPulseRuntime.invalidate_observation_gap` from the pulse plan.

- [ ] Before active-mode setup, require known boot/runtime identity and healthy platform. Before each probe/positive actuator tick, capture a stamp and apply the common continuity predicate. It rejects unknown/different identity, monotonic regression, suspend-offset change over 0.25 seconds, or a configured maximum active observation gap. On Linux `CLOCK_BOOTTIME - monotonic` detects suspend that `time.monotonic()` may exclude; do not use wall delta to detect suspend (a wall-only correction must remain harmless).
- [ ] The proposed ordinary observation-gap bound is 1.0 second (20 nominal 50 ms loop periods), implemented as a named production constant `MAX_ACTIVE_CONTROL_GAP_S = 1.0`. This is a conservative **design approval item**, not a source-established safe threshold. Measure admitted loop latency in the authorized disconnected-platform smoke before accepting it; if rejected, revise the spec and both runtime/gap regression together. Never silently disable continuity fencing to keep a slow fixture running.

```python
stamp = self.ctx.clock_domain.capture()
if previous_stamp is not None and continuity_lost(
    previous_stamp, stamp, max_active_gap_s=MAX_ACTIVE_CONTROL_GAP_S
):
    self._on_control_discontinuity(
        previous_stamp.observed_monotonic_s, "control-clock-discontinuity"
    )
    # Stop this work cycle; do not read a fresh probe as proof for the gap.
    status = "Inactive"
    break
previous_stamp = stamp
now = stamp.observed_monotonic_s
```

- [ ] Base discontinuity handling immediately commands auger/igniter OFF, invokes the existing safety transition path and records a trajectory/safety boundary at the last observed physical instant plus actual event wall metadata. Do not assert unknown hardware exposure was zero. Hold discards incomplete history/credit and cancels calibration through pulse invalidation without `advance(resume_now)`.
- [ ] Keep fan/power cooling policy explicit: a discontinuity during Shutdown must not mark its old countdown complete; after health admission and operator acknowledgment, restart a full Shutdown cooling duration. Other active modes do not resume stored ON overrides, startup/prime progress or controller history automatically. Use existing safe error/stop output policy while awaiting restart; any separate independent fan safeguard remains active.
- [ ] Stop/join the worker, drain persistence, finalize old trajectory as interrupted, invalidate probe/excitation history and runner/estimator/calibration state, then rotate runtime identity. Reject delayed old-generation results. Fresh health + explicit restart establishes new timers and estimator seed through normal admission; no prior monotonic deadline survives.
- [ ] Wrap the shared active work-cycle body with `try/finally` preserving existing teardown order and metric writes so unexpected exceptions cannot skip hardware-off/monitor cleanup. Make teardown idempotent and use a recorded terminal physical cutoff; after a known discontinuity never advance physical accounting at a later cleanup time. Pulse teardown owns its dispatch-once phases.
- [ ] Add `test_suspend_preempts_before_manual_on_and_clears_excitation`, `test_unknown_boot_cannot_enter_active_control`, `test_large_tick_gap_requires_restart`, `test_exception_runs_terminal_hardware_off_once`, `test_shutdown_resume_restarts_full_cooling`. Assert command order, no positive command after the discontinuity, no delivered/history backfill, old result rejection and incomplete evidence. For a wall-only jump, assert none of these safety fences fire when monotonic and suspend offset are continuous.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_control_mode_base.py tests/unit/runtime/test_hold_orchestration.py tests/unit/runtime/test_hold_pulse_scheduler.py tests/unit/runtime/test_smoke_learning_trajectory.py -q -k 'suspend or gap or resume or teardown or exception or wall'`.

### Task 5: Publish authoritative duration snapshots, not disguised epoch deadlines

**Files:** Modify `controller/runtime/modes/base.py` (`_build_status_data`), `hold.py` (`status_fragment`), `controller.py` (idle status), `store.py`; peripheral plan owns `display/_base_fixed.py`, `_base_flex.py`, `qtbackend.py`, API/browser projections and persisted user/recipe timer consumers.

**Interfaces:** Runtime status fields are `elapsed_seconds` (current mode active elapsed), `remaining_seconds` (startup/reignite/prime/shutdown, otherwise `None`), `lid_open_remaining_seconds`, and `cook_elapsed_seconds` (active monotonic cook duration, `None` when unknown), plus `clock_stamp` containing the serialized peripheral-owned `ClockStamp` captured at that admitted tick. Peripheral wire projection uses `modeElapsedS`, `modeRemainingS`, `lidRemainingS`, `cookElapsedS`, `current`, and `running`. Current status requires both a matching fresh heartbeat runtime and valid status-stamp age no greater than 15 seconds under the proposed peripheral freshness policy; a fresh heartbeat alone cannot freshen an old duration snapshot. Missing/stale/unknown-identity values display unavailable/paused, never fall back to epoch subtraction. `start_time`, `startup_timestamp` and any retained `lid_open_endtime` are epoch provenance/estimates, never countdown authority.

- [ ] Compute all duration snapshots from the admitted tick; do not resample wall during duration math. The existing `_build_status_data` accepts the internal start instant, so change its use of that argument and keep the public epoch field explicit:

```python
elapsed = now - self.state.timers.start_time
status_data["start_time"] = self.state.timers.start_wall_time
status_data["elapsed_seconds"] = elapsed
status_data["lid_open_remaining_seconds"] = (
    max(0.0, self.state.lid.expires - now) if self.state.lid.open_detected else 0.0
)
# For each timed mode, duration is its existing startup/shutdown/prime value.
status_data["remaining_seconds"] = (
    max(0.0, duration - elapsed) if duration is not None else None
)
```

- [ ] Serialize the same admitted tick's stamp beside its duration snapshot and persist both atomically through `write_status`; do not replace the stamp with API read time, heartbeat publication time, or a later store write. Consume the peripheral `ClockStamp` serializer rather than defining a competing wire shape.

- [ ] Keep status `start_duration`, `shutdown_duration`, `prime_duration` as configuration durations. `lid_open_endtime`, if retained for existing epoch metadata consumers, is captured wall at pause start plus configured duration and labeled an estimate; never reuse `state.lid.expires` directly as an epoch value. Current consumers all migrate to explicit remaining fields in this same revision; no permanent fallback.
- [ ] Track `cook_elapsed_seconds` across same-runtime mode transitions from a monotonic cook origin; clear on history clear/new cook, retain only known active portions across a safe paused lifecycle, and publish unknown after unclean restart unless a durable explicit-duration accumulator supplies the prior value. Never reconstruct active time from `startup_timestamp`. Discontinuity freezes known elapsed and marks it not running/current until re-admission.
- [ ] Route recipe timer start at `base.py`'s recipe start branch and idle `Controller` expiry through the peripheral timer authority. Active notifications, UI countdown, pause/resume, persisted schema, and generation fences must all consume that authority. A user timer can trigger shutdown, so this dependency is safety tier, not cosmetic display work.
- [ ] Add `test_startup_shutdown_prime_elapsed_ignore_wall_jumps`, `test_lid_and_manual_release_ignore_wall_jumps`, and `test_status_duration_does_not_expose_uptime_as_epoch`. At monotonic start 10, duration 60, tick 15 with wall changed by either sign: assert mode remaining 55; expiry remains false; `start_time` equals the original epoch capture; lid deadline 40 yields remaining 25; manual deadline 20 remains active until its monotonic release boundary. At exactly existing `>` boundaries assert current behavior, then advance 0.05 and assert expiry.
- [ ] Coordinate peripheral display tests: status `remaining_seconds=55` must render `00:55` regardless of a mocked wall `time.time()`, and lid remaining 25 renders `00:25`; stale/unknown generation must not render a live countdown. Add `test_fresh_heartbeat_does_not_freshen_stale_duration_snapshot`: keep heartbeat fresh while status remains at monotonic 10, read at 26, assert `current=False` and no live countdown; publish a new same-runtime status at 26 and assert it becomes current. Preserve actual render/Qt/fixed display smoke as the UI proof, not only snapshot field tests.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_control_mode_base.py tests/unit/runtime/test_mode_metrics_staging.py tests/unit/runtime/test_mode_settings_reload.py tests/ui/test_base_flex_dash_update.py tests/ui/test_qtbackend.py tests/ui/test_fixed_base_golden.py tests/characterization/test_controller_loop_golden.py -q`.

## Deterministic Scenario Matrix

| Scenario | Setup | Required consumer assertion |
|---|---|---|
| Smoke/Startup ON→OFF | Mono 10 ON, wall epoch, 2-second ON budget; wall ±3600 at mono 11 | OFF at first admitted tick after mono 12 under existing strict boundary; delivered 2.05 at 50 ms ticks, not ±3600 seconds |
| Hold pulse | Mono 20 frame starts, 10% duty/20-second frame, wall jump at 21 | Physical OFF at 22; two delivered seconds; no wall-induced skipped frames; pulse plan preserves scheduler rejection |
| Startup/Reignite | 60-second duration, mono elapsed 5 | Remaining 55, startup epoch stamp unchanged; temperature exits still work independently |
| Shutdown | 90-second cooling budget with forward wall jump | Fan/power teardown cannot occur before admitted monotonic duration; resume restarts full budget after admission |
| Prime partial stop | ON 3 seconds then normal stop; teardown called twice | Exactly 3 known delivered seconds; second teardown adds nothing |
| Lid and manual | Lid pause 30, manual override 10; wall jumps while pending | Original monotonic release boundaries; no wall-induced release/reinhibit; manual source precedence retained |
| Display/history/ETA/hopper/PWM | Monotonic tick increments, wall moves backward | Cadences continue; epoch publication visibly reflects rollback; status remains valid with explicit remaining values |
| Excitation | Actual auger/igniter union ON for 2 seconds | Exactly 2 heat-on seconds, not double counted; resume clears untrusted history instead of adding suspend duration |
| Runner reconfiguration | Distinct epoch and uptime, PID→PID-SP and replacement worker | Both builder/reconfigure paths use one axis; stale generation rejected; raw signed PID-SP return unchanged |
| Restart/suspend | Unknown/new boot, changed runtime, or BOOTTIME offset increase | No live old stamp, no pulse catch-up, no old deadline restoration; fresh health/admission and explicit restart required |

## Verification, Migration, Rollback, and Publication

Before implementing this later plan, complete exported-symbol references and dynamic builder inventory and obtain joint trace/probe/timer contract approval. Task 4 additionally requires explicit review of detector/platform behavior and any 1.0-second gap bound; this is not a gate that silently expands Main's currently authorized coupled learning correction. Do not execute a mixed-axis plan revision on an active fueled controller.

During authorized implementation, run focused commands per task and use a disconnected/fake-platform real-loop smoke with independent clock axes. Inject wall ±3600 during an ON pulse, startup/shutdown, lid/manual pause, and status publish; record hardware commands, metrics, trace and display output. Also simulate BOOTTIME offset change separately from wall jumps. Fake-platform command evidence does not establish physical sensor/actuator behavior; report that boundary. UI changes need an actual display/browser/Qt surface smoke under the peripheral plan.

Migration: safely stop active modes, force outputs to the existing safe state, stop workers, persist known final intervals, and deploy the joint revision. Start new mode timers and predictor/estimator histories; never deserialize raw same-process timer fields into a new runtime. Old epoch logs retain original values. Persisted recipe/user timer downtime and explicit resume policy comes only from the peripheral authority. Existing open learning segments retain unclean-restart finalization without stretching old monotonic endpoints.

Rollback: safely stop again, preserve every new trace/archive and evidence artifact, roll back the entire joint revision, and perform fresh startup/health admission. An older reader must reject unknown new trace/status/timer contracts rather than reinterpret uptime as epoch. No hot compatibility shim, automatic replay of previous ON state, or data rewrite to make an old reader accept it.

After smoke proves the changed behavior, update existing runtime and timer/display documentation to state domains and restart policy, remove throwaway scripts, and update only behaviorally affected fixtures. Do not rewrite historical fixtures through current builders or relax golden/mutation assertions. The integration owner then runs:

```bash
uv run pytest tests/unit/runtime/test_clock.py tests/unit/runtime/test_control_mode_base.py tests/unit/runtime/test_mode_metrics_staging.py tests/unit/runtime/test_mode_settings_reload.py tests/unit/runtime/test_hold_orchestration.py tests/unit/runtime/test_hold_pulse_scheduler.py tests/unit/runtime/test_smoke_learning_trajectory.py tests/unit/controller/test_heartbeat.py tests/characterization/test_controller_loop_golden.py -q
prek run --all-files
uv run python scripts/exact_revision_gate.py verify-bookmark --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
# Only on an explicit publication request:
uv run python scripts/exact_revision_gate.py push --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

The authoritative wrapper runs the separate contract preflight before the five ordered release commands and verifies exact revision before/after each and before publication. A failed/interrupted/drifted preflight leaves all five not run. Preserve schema-v2 evidence at `.artifacts/exact-revision/<full-revision>/evidence.json`, its preflight evidence, and all referenced stdout/stderr/hash artifacts; schema-v1 evidence is historical-only. Focused test success or a Jujutsu operation is not release evidence and does not authorize push.
