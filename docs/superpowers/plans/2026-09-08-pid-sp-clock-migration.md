# PID-SP Clock Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete PID-SP shared-clock integration after the coupled learning producer correction, preserving controller numerical policy, predictor coverage checks, strict model admission and raw signed public return.

**Architecture:** PID-SP update/target timestamps and completed-frame history share one monotonic source; wall evidence timestamps and independently captured frame wall endpoints remain provenance. Consume the shared Clock through every builder/reconfigure path. Preserve `SmithPredictor.temperature(measured_f, current_time)` and its exact history coverage/fallback behavior rather than changing correction strategy to make a mixed-domain test pass.

**Tech Stack:** Python, pytest, NumPy-backed FOPDT history, existing runner observation handoff, uv, Jujutsu, exact-revision gate.

**Spec:** `docs/superpowers/specs/2026-09-08-clock-domain-separation-design.md`

**Integrated baseline:** The coupled learning implementation is now in the working tree: trace 10, observation 4, evidence 6, database 13, required independent wall endpoints, and callable clocks through Hold/runner factories and reconfiguration. References below to Main's “in-flight” work describe the drafting baseline, not remaining implementation. Reconcile those steps against the shared spec before executing; the destination shared `Clock` API and remaining timers are still proposals.

## Global Constraints

- This remaining-work plan is a proposal, not authorization to implement it. Drafting performed source research only; no tests, build, lint or formatter commands were run.
- User separately approved **Implement coupled learning boundary** now. Main owns in-flight learning capture, replay/evidence semantics and necessary pulse/PID-SP timestamp producer fixes. Do not describe them as verified/done or overwrite their edits.
- Destination Clock API: `wall_time() -> float` epoch seconds, `monotonic() -> float` same-boot steady seconds, unchanged `sleep(seconds)`; RealClock delegates to `time.time`/`time.monotonic`/`time.sleep`.
- Destination fake: `ManualClock(wall_start=0.0, monotonic_start=0.0)`; `advance`/`sleep` move both axes and `jump_wall(delta)` moves wall only. Shared-mode owns this API, not PID-SP.
- Main may add monotonic access while `Clock.now()` retains wall meaning for untouched callers. Complete `now()` removal belongs to the later shared-mode cutover; no ambiguous alias or mid-cook meaning change.
- Wall timestamps may move in either direction. They establish provenance/calendar events, not physical duration, freshness, causal order or model authority. Never infer a domain from magnitude or synthesize a missing wall endpoint using an old offset.
- Monotonic freshness requires compatible boot/runtime identity. Unknown identity cannot be live; restart closes old history and never subtracts prior-boot coordinates from new uptime.
- Shared spec selects trace schema 10 from current source schema 9, and trajectory observation schema 4. Import production current constants, never invent the next version or expose current fixture schema overrides.
- `FrameObservation.frame_start_s/frame_end_s` are monotonic seconds; required `wall_start_ms/wall_end_ms` are independently captured nonnegative integer provenance that may regress. Main has added those fields and owns existing constructor/fixture migration.
- Preserve evidence admission, durable cook/installation identity, estimator seeds, replay validation, mutation/golden expectations, checkpoint schema and authority fences. Keep exact unique anchors `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.` unchanged.
- Preserve PID-SP public contract: `Controller.update()` returns historical raw signed demand. `self.u`, `AllocationResult.auger_duty`, runner duty and physical pulses remain bounded. Never clip the public return as incidental timing work.
- Preserve all existing PID numerical policy, including `MIN_ELAPSED_SECONDS = 1e-3`, duplicate-reading behavior, target branches and startup reduction. No derivative redesign, integral timing redesign or predictor correction/coverage redesign is authorized here.
- Suspend invalidates live control/history; unobserved hardware delivery is not fabricated. Detector/threshold/platform behavior are separately reviewed policy. An arbitrary one-second loop-gap threshold is not approved.
- Shared test helpers live in `_` modules, conftest or `tests/fakes`; collected tests never import other collected tests. No raw Git operations/direct `jj git push`; no push is authorized by this task.

---

## Named dependencies and joint deployment gate

| Dependency | Required product | Ownership |
| --- | --- | --- |
| Main coupled implementation | Monotonic learning-facing pulse/PID-SP timestamps; trace10 and trajectory-observation4 producer/reader migration; required independent frame wall endpoints; causal replay/evidence order. | **In flight**, Main-owned; reconcile source before each remaining task. |
| `2026-09-08-pid-clock-migration.md` | `ControllerBase(..., *, logger=None, clock=None)` and `_clock`; monotonic base target setter; existing floored elapsed helper unchanged. | One shared-base mutation owned by PID plan. |
| `2026-09-08-shared-mode-timers-clock-migration.md` | Destination Clock/fake, full dynamic/fallback/reconfigure injection, remaining mode duration cutover and final `Clock.now` removal. | Shared-mode owner. |
| `2026-09-08-pulse-scheduling-clock-migration.md` | Remaining pulse shared-clock integration and explicit reviewed unknown-delivery/suspend policy, distinct from Main's necessary producer correction. | Pulse owner. |
| Probe plan | Frame terminal sample freshness and role/identity provenance on the compatible control axis. | Probe owner. |

PID-SP, pulse intervals and shared-mode controls must not deploy with mixed physical axes. Separate documents allow ownership and review, not partial live deployment. Main's approved coupled subset may land without unapproved numerical or suspend policy changes; remaining work must honor that boundary.

## Source-derived producer/consumer inventory

Refresh actual source and LSP references when executing: line anchors describe research before Main's overlapping changes land.

| File/symbol | Contract and migration responsibility |
| --- | --- |
| `controller/pid_sp.py:158-258`, constructor | Existing `logger`, persistence/repository, `clock_ms` and installation identity seams. Initializes wall `last_update/last_set_time`, then calls `set_target(0)`. Destination adds keyword-only shared `clock=None`, forwards to base and uses its monotonic source. Main may already install a narrower callable source. |
| `controller/base.py.ControllerBase.set_target`, `controller/pid_base.py.set_target` | Additional `last_update` writers. PID plan owns them; no inherited/reset setter may reintroduce wall into PID-SP state. |
| `controller/pid_sp.py:1628-1794`, `update` | One current-time read supplies dt, Smith timestamp and both `cycle_time * 3` guards. Use monotonic without changing arithmetic or predictor call policy. |
| `controller/pid_sp.py:1802-1827`, `set_target` | Stamps last-update and target-change origin; resets PID target terms, preserves learned model. Change source only; preserve center/initialization logic. |
| `controller/pid_sp.py:265-320`, `observe_frame` | Builds PidSpInterval from completed bounds, checks probe/source/continuous/lid/safety/manual/stale/skipped/reset gates, records exact duty in predictor/identifier/episode accumulator. Main supplies monotonic frames and independent wall provenance; do not bypass admission. |
| `controller/pid_sp.py.set_output` | Telemetry-only by design; must not become requested-duty history to plug a coverage hole. |
| `controller/smith_predictor.py.record_interval/temperature/_integrate` | History and `_last_t` share a monotonic axis. `_integrate` requires actual coverage of direct and delayed windows; missing coverage falls back/reseeds under existing policy. Do not extrapolate an unfinished frame or move correction calculation to frame-end in this plan. |
| `controller/smith_predictor.py.reset/trust/_disable` | `reset()` clears disabled state but retains history; it is not a general restart/history disposal API. Keep same-authority sticky-disable protections and trust rules unchanged. A fresh runtime uses a fresh core. |
| `controller/fopdt_identifier.py.DutyHistory.record_interval/covers/average`, `FOPDTIdentifier.observe_interval/observe` | Finite increasing nonoverlapping completed bounds, gaps uncovered, candidate validity masks and regression anchor resets are required. Preserve all exact-interval checks. |
| `controller/pid_sp_observation.py.PidSpInterval/PidSpDutySegment`, model-learning FrameObservation | Domain documentation becomes explicit monotonic; keep tiling/duty/sequence/generation checks. Wall provenance is not another integration coordinate. |
| `controller/pid_sp_delay_evidence.py.EpisodeAccumulator`, `controller/pid_sp_model_selection.py` | Physical delay windows, sequence/generation and strict confirmation/model checkpoint admission; no wall-step excitation/confirmation or changed model authority policy. |
| `controller/model_learning/pid_sp_fitting.py._input_history/_materialize_episodes` | Already subtracts per-segment monotonic origin and fits relative seconds. Keep that approach, never combine raw coordinates across segments/boots. |
| `controller/pid_sp.py._clock_ms`, evidence paths near 395/675 | Wall provenance. Preserve explicit callable input and default wall meaning; Main owns durable causal order for latest/supersession. |
| `controller/pid_sp.py.bind_learning_identity/restore_model`, checkpoint encode/decode | Restore permitted model authority, not live clock/history state. Do not add last-update, target timers, predictor `_last_t`, identifier `_prev` or duty arrays to checkpoints. |
| `controller/base.py.PidSpTraceDiagnostics`, common trace schema, recorder/replay/import | `previous_update_time/target_change_time` become explicitly monotonic under current contract; effective dt retains numerical floor. Envelope timestamps remain wall; Main owns all current/historical schema migration. |
| `controller/runtime/runner.py.build_runner/_build_core/_wrap`, both runners' `reconfigure` | Dynamic import, fallback and replacement must use the same source as Hold frames, not a default real clock in fake/runtime replacement paths. |
| `common/controller_deps.py`, `controller/controllers.json` | Configuration-selected constructor consumers not exhausted by LSP references. Keep registry/model schema unchanged. |
| `tools/experiments/controller_matrix.py`, golden harness | Currently patches wall globally to simulate dt. PID plan/shared-mode migrate explicit Clock; Main owns current FrameObservation constructor edits now. Serialize overlapping mutation. |

### Current test and operational inventory

- `tests/unit/controller/test_pid_base.py`, `test_pid_sp.py`, `test_pid_sp_learning.py`, `test_pid_sp_model_selection.py`, `_pid_sp_model_selection_helpers.py`, `test_pid_sp_delay_evidence.py`, `test_pid_sp_fitting.py`, `test_smith_predictor.py`, `test_fopdt_identifier.py`, `test_controller_trace_diagnostics.py`, `test_controller_construct_smoke.py`, `test_controller_capabilities.py`, `test_matrix_harness_sim_clock.py`.
- `tests/unit/runtime/test_sync_runner.py`, `test_threaded_runner.py`, `test_controller_build_failure.py`, `test_hold_learning_runtime.py`, `test_hold_control_trace.py`, `test_hold_pulse_scheduler.py`, `test_framed_pulse_runtime.py`.
- `tests/characterization/test_pid_controllers_golden.py`, `tests/e2e/test_pid_sp_real_cook_learning.py`, `tests/integration/test_cookfile_learning_diagnostics.py`, `tests/unit/controller/test_control_trace_replay.py`.
- `tools/experiments/controller_matrix.py`; smoke tools that construct/wrap runner paths, including `tools/smoke_acados_hold.py`, must retain supported callable/Clock interfaces, though their MPC numerical core is not changed.
- Web PID-SP learning endpoints and report projections consume wall evidence metadata and production schema constants, not monotonic controller state. Main's evidence migration must preserve that distinction.

Research used LSP references on controller/base symbols, Smith `record_interval`, and runtime builders, plus AST dynamic import search. Before exported-symbol edits rerun references, inspect dynamic factories, serializers/readers, restore paths, mutation anchors, smoke tools, characterization and experiments; Main's concurrent source is authoritative.

## Numerical, coverage, and restart invariants

1. Preserve `_elapsed_since_last_update(now) = max(now - last_update, 1e-3)`. Only clock choice changes. A duplicate instant remains protected by the existing floor; do not replace it with zero derivative/no-op behavior.
2. Use the same sampled `current_time` in PID-SP dt, `predictor.temperature(measured_f, current_time)`, both three-cycle guards and final `last_update` assignment. Constructor/target reset must use the same injected source.
3. Update time and completed frame times share a monotonic origin, but need not be equal. A solve 125 ms after completed history may legitimately lack coverage. Preserve the existing fallback/truncation behavior; no invented held duty, timestamp clamp, or new cached correction strategy.
4. Completed intervals are admitted causally with existing sequence/generation/probe/output checks. A wall jump changes metadata only. Actual monotonic gaps and unknown delivery remain gaps and cannot become identification/evidence by conversion.
5. `set_target` preserves learned model and physical history as it does today. A genuine runtime restart/resume with invalid history reconstructs the core under existing admission, not `predictor.reset()` to clear a sticky safety disable.
6. Arbitrary fake-monotonic regression is an invalid source/session. Do not solve it by wall fallback or numerical dt clipping beyond existing policy. Additional detection/error/restart behavior requires shared-runtime policy review, not a new PID exception surface here.

### Task 1: Reconcile Main's producer correction with destination shared-clock injection

**Files:** Remaining changes in `controller/pid_sp.py`; shared base from PID plan. Tests in `test_pid_sp.py`, `test_pid_base.py`, `test_controller_trace_diagnostics.py`. Main currently owns overlapping necessary timestamp and fixture changes; inspect and coordinate before editing.

**Interfaces:** Destination `pid_sp.Controller(..., *, clock=None, clock_ms=None, ...)` consumes `_clock` from PID plan. Existing other keyword arguments and raw signed return remain unchanged.

- [ ] Read the current constructor/update/set_target and record which monotonic injection Main already landed. Keep its behavior while converging on one shared Clock; migrate every consumer of any temporary callable seam and remove that seam only in the coordinated shared-mode integration. Do not retain parallel aliases or reinterpret an existing wall `clock_ms`.
- [ ] Add/retain keyword-only `clock=None`, forward `super().__init__(config, units, cycle_data, logger=logger, clock=clock)`, and use these destination timestamps in their existing initialization locations:

```python
self.last_update = self._clock.monotonic()
self.last_set_time = self._clock.monotonic()
```

  Preserve constructor sampling order; the later `set_target(0.0)` remains the target origin. Keep explicit evidence callback compatibility and make the default use destination wall time:

```python
self._clock_ms = (lambda: int(self._clock.wall_time() * 1000)) if clock_ms is None else clock_ms
```

  During Main's interim now-wall phase, its equivalent wall callback is valid; do not remove `now()` ahead of shared-mode callers.
- [ ] Keep update structure unchanged except source selection:

```python
current_time = self._clock.monotonic()
previous_update_time = self.last_update
previous_temperature = self.last
dt = self._elapsed_since_last_update(current_time)
```

  The existing predictor invocation remains:

```python
selected = _from_f(self.predictor.temperature(measured_f, current_time), self.units)
```

  Keep the final `self.last_update = current_time` and both existing `current_time - self.last_set_time` guards. Do not change derivative, integral, branch, initialization, seed or coverage policy.
- [ ] In target reset, change only the time source and preserve its shared origin:

```python
self.last_update = self._clock.monotonic()
self.last_set_time = self.last_update
```

  Preserve current center calculation, model/history state, `_integral_seeded`, `start_change_temp` and new-target behavior.
- [ ] Migrate local deterministic fixtures to the destination ManualClock and inject it through `_controller` and every direct constructor. Existing `test_pid_sp.py._Clock` is currently a wall callable and `_controller` dynamically loads the module; change physical `.t` reads to `.monotonic()` and forward movements to `.advance(seconds)`. Preserve explicit `clock_ms` lifecycle fixtures; they still test wall evidence.
- [ ] Add the following wall/target regression, reusing `CONFIG` in `test_pid_sp.py`:

```python
from controller.runtime.clock import ManualClock


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_pid_sp_target_window_uses_monotonic_seconds(jump):
    clock = ManualClock(wall_start=1_700_000_000.0, monotonic_start=1000.0)
    sp = PidSpController(dict(CONFIG), "F", {}, clock=clock)
    sp.set_target(225.0)
    clock.advance(59.0)
    clock.jump_wall(jump)
    before = sp.update(200.0)
    status = sp.get_status()
    assert before == pytest.approx((status["p"] + status["i"] + status["d"]) * STARTUP_REDUCTION)
    assert sp.trace_diagnostics().observed_dt_seconds == 59.0
    clock.advance(1.0)
    at_boundary = sp.update(200.0)
    status = sp.get_status()
    assert at_boundary == pytest.approx(status["p"] + status["i"] + status["d"])
    assert sp.trace_diagnostics().observed_dt_seconds == 1.0
```

- [ ] Before the source change, run `uv run pytest tests/unit/controller/test_pid_sp.py -k 'target_window or reduction_stops or startup_reduction' -q` against the shared-clock prerequisites; record genuine behavioral failure, not unsupported-fixture failure. Repeat after the change.
- [ ] Replace the bare-finiteness duplicate regression with `test_duplicate_pid_sp_readings_are_wall_invariant`: drive two real controllers with equal unchanged monotonic instants and equal in-window temperatures (220 then 221 F), jump one wall between calls, assert both raw outputs and diagnostics match and are finite. Continue with a normal physical step and compare again. Preserve production floor, do not assert an invented zero-time numerical policy.
- [ ] Retain the exact allocation/public-return boundary:

```python
raw_output = self._publish_direct_auger_allocation()
# Existing diagnostics preserve raw_output=raw_output and final_output=self.u.
return raw_output
```

  Add `test_wall_jump_preserves_signed_raw_demand_and_bounded_allocation`: use the current golden schedule at 20-second steps in two controllers with/without wall correction; assert equal complete raw arrays, the known negative result remains `round(raw, 6) == -0.15963`, and at that result `self.u == trace_allocation().auger_duty == 0.0`. Do not rebaseline either `GOLDEN` array.
- [ ] Run `uv run pytest tests/unit/controller/test_pid_base.py tests/unit/controller/test_pid_sp.py tests/unit/controller/test_controller_trace_diagnostics.py tests/characterization/test_pid_controllers_golden.py -q` after the base/fixture changes are coordinated.

### Task 2: Prove completed-history alignment while preserving coverage rejection

**Files:** Main owns necessary `controller/runtime/framed_pulse.py`, Hold, FrameObservation and fixture producer edits. Remaining documentation/tests in `controller/smith_predictor.py`, `controller/fopdt_identifier.py`, `controller/pid_sp_observation.py`, `test_pid_sp.py`, `test_smith_predictor.py`, `test_fopdt_identifier.py`.

**Interfaces:** Existing `observe_frame`, `record_interval(start_s, end_s, duty)`, `temperature(measured_f, timestamp)`, identifier interval calls remain unchanged. Every time argument is same-session monotonic seconds; wall provenance is separate required metadata, not an argument to integration.

- [ ] Inspect Main's current `_observe_completed_frame` helper migration. Required wall endpoints must come from explicit test captures, not defaults copied from monotonic bounds. The following test reuses that helper by explicitly supplying current-contract wall fields and distinct low-uptime/epoch axes:

```python
@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_pid_sp_completed_history_is_wall_invariant(jump):
    clocks = [
        ManualClock(wall_start=1_700_000_000.0, monotonic_start=1000.0),
        ManualClock(wall_start=1_700_000_000.0, monotonic_start=1000.0),
    ]
    cores = [PidSpController(dict(CONFIG), "F", {}, clock=c) for c in clocks]
    for core in cores:
        core.set_target(225.0)
        assert core.predictor.trust({"form": "fopdt", "K": 100.0, "tau": 100.0, "theta": 20.0})
    for index, duty in enumerate((0.2, 0.8)):
        outputs = []
        for side, (clock, core) in enumerate(zip(clocks, cores, strict=True)):
            start = clock.monotonic()
            wall_start = int(clock.wall_time() * 1000)
            clock.advance(20.0)
            if side == 1 and index == 1:
                clock.jump_wall(jump)
            _observe_completed_frame(
                core, start, clock.monotonic(), (200.0 - 32.0) * 5.0 / 9.0,
                duty=duty, wall_start_ms=wall_start,
                wall_end_ms=int(clock.wall_time() * 1000),
            )
            outputs.append(core.update(200.0))
        assert outputs[1] == pytest.approx(outputs[0])
    expected = 200.0 + 60.0 * (1.0 - math.exp(-0.2))
    for core in cores:
        assert core.get_status()["selected_temp"] == pytest.approx(expected)
        assert core.get_status()["predictor"]["truncated"] == 0
```

  Direct `trust` here isolates numerical propagation; it is not evidence that model admission can be bypassed. Existing strict checkpoint/restore tests remain required separately. Both solves occur exactly at fully covered frame endpoints, so the analytic expectation follows the existing algorithm.
- [ ] Add `test_uncompleted_solve_gap_is_not_filled_by_clock_migration`: repeat the same numerical model and two accepted frames, then advance monotonic by 0.125 seconds without another completed frame and call update. Assert existing predictor fallback selects measured temperature, increments `truncated` once and does not record an extra identifier duty segment. Repeat in a wall-corrected paired run and assert identical fallback/result. Missing coverage is real even with a correct clock; do not fix this test by projecting a cached correction or clamping solve time to frame end.
- [ ] Add `test_rejected_frame_does_not_add_pid_sp_history`: after one accepted interval, submit the next interval with `safety_inhibited=True` and explicit wall captures. Assert returned outcome is ineligible with zero effective updates and identifier duty segment count unchanged; preserve the existing source/probe/discontinuous/stale/skipped/reset cases. Do not introduce new predictor reset semantics for rejection as part of this migration.
- [ ] Run `uv run pytest tests/unit/controller/test_pid_sp.py -k 'completed_history or uncompleted_solve_gap or rejected_frame' -q` before/after the necessary producer alignment. If Main already supplies passing clock behavior, this is retained regression coverage for the shared-clock cleanup, not a fabricated pre-change failure claim.
- [ ] Keep the production accepted observation branch as the existing exact contract:

```python
self.predictor.record_interval(interval.start_s, interval.end_s, interval.realized_duty)
self.identifier.observe_interval(
    interval.start_s, interval.end_s, interval.realized_duty, interval.temperature_f,
)
```

  Required wall fields remain available on FrameObservation for trace/learning provenance but are not passed as physical bounds to PidSpInterval, predictor or identifier. Preserve all preceding admission checks, episode observations and outcome digest/sequence semantics.
- [ ] Change the obsolete Smith history-retention comment describing caller wall clock to caller monotonic control intervals. Clarify the same domain in `record_interval`, `PidSpInterval` and identifier interval docstrings. Do not modify `temperature`, `_integrate`, `reset`, `trust`, retention constants or exact-coverage implementation unless Main's actual approved producer changes require a strictly representational adaptation.
- [ ] Retain `DutyHistory` gap and overlap tests. If the exact case is absent, add `test_completed_monotonic_gap_stays_uncovered`: record `[100,120)` and `[140,160)`, assert `covers(120,140) is False`, and overlap insertion `[119,121)` raises ValueError. No wall timestamp or fabricated zero-duty fill participates.
- [ ] Run `uv run pytest tests/unit/controller/test_pid_sp.py tests/unit/controller/test_smith_predictor.py tests/unit/controller/test_fopdt_identifier.py tests/unit/controller/test_pid_sp_delay_evidence.py -q`. Preserve sticky-disable/same-authority-retrust assertions. Remove in-scope source-text-only test assertions rather than repinning strings; do not weaken behavioral/model checks.

### Task 3: Preserve restart, model restore, evidence, and replay boundaries

**Files:** Existing `test_pid_sp.py`, `test_pid_sp_learning.py`, `test_pid_sp_model_selection.py`, `test_pid_sp_fitting.py`, `test_control_trace_replay.py`; Main-owned learning/trace producers, store/import and fixtures; shared-mode runner integration.

**Interfaces:** Existing checkpoint/model restore contract unchanged; fresh runtime history. Trace10 and trajectory-observation4 use explicit physical axes and independent wall provenance under Main's migration.

- [ ] Add `test_restart_restores_admitted_model_not_live_history` with existing `_fopdt_checkpoint` helper: drive an old core at mono≈10,000 with nonzero predictor correction, create a new core at mono=5 and fresh runtime identity, restore only a valid current model checkpoint using existing admission fixtures, set target, advance 2 and update 200 F. Assert dt=2, selected temperature=200, first derivative=0 and identifier duty segment count=0. Assert selected admitted model identity is preserved where restore succeeds; preserve rejection for ineligible/unknown installation/cook fixtures instead of weakening them for the timing test.
- [ ] Add/retain `test_set_target_preserves_model_across_wall_jump`: restore valid checkpoint, capture model identity, jump wall, set target 275, advance two seconds and solve; assert unchanged admitted model identity and a two-second effective dt. Target reset is not runtime history reset.
- [ ] Verify the production restart boundary constructs a fresh core rather than calling Smith `reset()` on retained history. Prior-boot monotonic timestamps/identifier rows/target timers must not enter new live state. Main's recovery closes old learning segments; no downtime subtraction or added checkpoint fields is needed.
- [ ] In Main's current-contract trajectory tests, exercise physically equal monotonic episodes with actual ±3600 wall endpoint corrections and assert equivalent fitting decisions/parameters using existing tolerances. Preserve `_materialize_episodes` and `_input_history` per-segment monotonic-origin subtraction. Do not synthesize wall duration equality, join across incompatible identities, weaken exact delivery/seed/role requirements, or rewrite old schema2/3 fixtures through current builders.
- [ ] Verify an unknown-boot historical segment cannot become fresh PID-SP duty/probe history. It may be retrospective fit input only where the explicit historical decoder preserves enough evidence; it must not be compared to current uptime or promoted to current exact replay by numerical resemblance.
- [ ] Extend existing lifecycle evidence cases around `clock_ms`: emit a causally newer decision after a backward wall correction, assert actual decreasing wall provenance is retained and Main's durable append/revision ordering makes that later decision authoritative. Do not change confirmation count or model admission to compensate for timestamp order.
- [ ] Verify a real trace producer at epoch wall and low monotonic uptime: `target_change_time/previous_update_time` are explicit monotonic diagnostics under trace10, envelope `ts_ms` remains wall publication, and nonzero solve-to-publication delay does not become a join-equality requirement. Main owns versioned serializers/readers and historical exact import policy; retain unknown-mapping rejection.
- [ ] Run `uv run pytest tests/unit/controller/test_pid_sp_learning.py tests/unit/controller/test_pid_sp_model_selection.py tests/unit/controller/test_pid_sp_fitting.py tests/unit/controller/test_control_trace_replay.py tests/unit/runtime/test_hold_learning_runtime.py tests/unit/runtime/test_hold_control_trace.py tests/integration/test_cookfile_learning_diagnostics.py -q` after Main/shared owners finish their consumer edits. A current-contract fixture failure is fixed at the owning contract, not by relaxing schema/evidence validation.

### Task 4: Exercise real runner/frames and finalize the coordinated release

**Files:** Existing runtime/characterization/e2e paths in the inventory; no new permanent smoke helper. Shared runtime changes remain with their named owners.

**Interfaces:** Real SyncControllerRunner, real PID-SP, real completed FrameObservation and pulse runtime all use the same source; independent wall provenance and causal admission remain explicit.

- [ ] Shared-mode owner migrates `build_runner`, `_build_core`, `_wrap`, both runner constructors/reconfigure paths and Hold setup to retain one Clock. Confirm fallback and MPC→PID-SP reconfiguration do not silently select real monotonic time while frames remain on ManualClock. Preserve current direct runner callable timing seams or migrate every consumer together; no half-compatible alias.
- [ ] Add `test_pid_sp_completed_frame_clock_contract_through_hold` in `test_hold_learning_runtime.py`, or extend Main's equivalent newly added regression, using the real-runner/pulse harness: start wall at epoch and mono=100, complete real frames, apply each sign of a one-hour wall jump during ordinary monotonic advancement, compare no-jump/jump runs. Assert equal delivered seconds, monotonic frame bounds, dt, raw demand and predictor status; required wall endpoints record the actual jump. At exact covered solve boundaries predictor behavior matches normal operation; with a genuine 125 ms missing-coverage interval both runs must exhibit the same existing fallback, not a redesigned correction.
- [ ] Keep suspend safe-restart as a separately reviewed policy acceptance scenario: after the shared-mode detector's platform/threshold decision is approved, it must inhibit output before resumed solve, close old history without synthetic delivery, prevent old results/observations entering the new core, and require fresh probe/seed admission. Do not implement an arbitrary one-second threshold or claim this scenario was verified by a wall-jump test.
- [ ] Run the integrated focused commands:

```bash
uv run pytest tests/unit/controller/test_pid_base.py tests/unit/controller/test_pid_sp.py tests/unit/controller/test_smith_predictor.py tests/unit/controller/test_fopdt_identifier.py tests/unit/controller/test_pid_sp_delay_evidence.py tests/unit/controller/test_pid_sp_learning.py tests/unit/controller/test_pid_sp_model_selection.py tests/unit/controller/test_pid_sp_fitting.py tests/unit/controller/test_controller_trace_diagnostics.py tests/unit/controller/test_matrix_harness_sim_clock.py tests/characterization/test_pid_controllers_golden.py -q
uv run pytest tests/unit/runtime/test_sync_runner.py tests/unit/runtime/test_hold_learning_runtime.py tests/unit/runtime/test_hold_control_trace.py tests/unit/runtime/test_hold_pulse_scheduler.py tests/unit/runtime/test_framed_pulse_runtime.py tests/integration/test_cookfile_learning_diagnostics.py tests/e2e/test_pid_sp_real_cook_learning.py -q
```

- [ ] Write `/tmp/pid_sp_clock_smoke.py` using the real two-core/two-frame numerical schedule in Task 2 and direct current FrameObservation construction with explicit independently captured wall endpoints. Do not import a collected test. Run `uv run python /tmp/pid_sp_clock_smoke.py`; print/assert selected temperature `200 + 60 * (1 - exp(-0.2))`, zero truncations at the covered frame boundaries, equal raw demand across wall correction, and bounded physical allocation. Then exercise the uncovered +0.125-second solve and assert measured fallback in both runs. Preserve stdout, remove the script after proof. This is a core smoke, not physical-grill hardware verification; real Hold producer/runner evidence remains separately required.
- [ ] After smoke proof, remove transient scripts and update existing controller/predictor docstrings and existing release changelog as appropriate. Preserve archives/evidence/historical fixtures, and review changed public-return, authority, schema, trace, restore, experiment and mutation consumers.
- [ ] Final integration owner explicitly runs `prek run --all-files`; Jujutsu describe/new does not run commit hooks. Freeze the integrated revision through the established Jujutsu workflow, not raw Git commands.
- [ ] With the authorized bookmark on that frozen revision, run:

```bash
uv run python scripts/exact_revision_gate.py verify-bookmark --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

  The wrapper owns the separate contract preflight (`uv run pytest tests/unit/test_no_cross_test_imports.py tests/unit/mpc/test_mutation_score.py tests/unit/common/test_current_contract_fixtures.py -q`) before the five commands: `./rebuild-acados.sh --if-needed`; `uv run pytest tests/`; `uv run pytest -m slow tests/`; `bun run test` at root; `bun run test:e2e` in `web-react`. A failed/interrupted/revision-drifted preflight leaves the five not run; focused tests do not substitute.
- [ ] Preserve schema-v2 `.artifacts/exact-revision/<full-revision>/evidence.json`, separate preflight logs/SHA-256, every command stdout/stderr hash and before/after/final-publication revision check. Skipped/missing/interrupted/timed-out/nonzero/reordered/different-revision results fail closed; schema-v1 evidence is historical only. No push authorized. If later requested, use only `uv run python scripts/exact_revision_gate.py push --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision` or repository `jj push` wrapper alias; no direct `jj git push`.

## Migration and rollback policy

- Main's in-flight necessary producer work must be inspected and integrated, not reimplemented as though absent. Later shared-clock cleanup migrates every remaining callable/Clock consumer together and removes obsolete seams, without changing numerical behavior.
- No live timestamps or duty arrays are added to model checkpoints. Preserve admitted durable model/evidence only; new runtime constructs fresh predictor/identifier/target origins and obtains compatible probe/history identity.
- Install a coordinated time-axis revision with control safely inhibited and old runtime stopped, finish worker/observation teardown and finalize old trace/learning boundaries. Do not change time meaning inside a live predictor or scheduler.
- Wall corrections alone do not require model reset or operator restart. Actual lost physical history, incompatible identity or suspend requires safe history invalidation; detector details remain reviewed shared-runtime policy, not inferred from a wall discontinuity.
- Rollback is another full coherent-version stop/restart. Preserve trace10/observation4 archives. An older reader lacking their explicit contract must not treat them as current data; use an identified compatible backup or remain stopped. Never relabel monotonic endpoints as epoch values or weaken checkpoint/schema/evidence admission to load a rollback.

## Acceptance and missing prerequisites

- [ ] Equal physical histories with either sign of a one-hour wall correction produce equal dt, target decisions, raw demand, predictor response and completed delivery.
- [ ] Repeated readings preserve existing numerical floor behavior; ordinary golden arrays and negative raw return remain unchanged; physical allocation stays bounded.
- [ ] Predictor and FOPDT share the completed monotonic history axis and still reject uncovered/skipped/overlapping/invalid intervals. No new correction strategy or synthetic duty is introduced.
- [ ] Restart uses fresh dynamic state and compatible identity; setpoint changes preserve existing admitted model behavior; unknown boot data cannot be live.
- [ ] Main's trace10/observation4, independent wall endpoints and causal evidence/replay migration are complete and verified before claiming this dependency satisfied. Their current state is in flight, not assumed done.
- [ ] Remaining shared Clock implementation/removal, runner injection and any separately approved suspend policy are delivered by their named owners; no hidden one-second threshold or numerical-policy decision is included.
- [ ] Every current-contract consumer, strict model anchor and exact-revision release evidence requirement remains satisfied at execution.
