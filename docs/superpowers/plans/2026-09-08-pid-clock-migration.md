# PID Clock Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make classical PID elapsed/reset timing independent of wall-clock corrections while preserving the existing numerical and public-output policy.

**Architecture:** Inject the destination shared Clock into controller construction and use monotonic readings for update and target-reset timestamps. Migrate deterministic simulations and every dynamic/reconfiguration constructor to that same source. Keep existing PID equations, denominator floor and diagnostic shape; the shared-mode owner implements the Clock API and runtime integration.

**Tech Stack:** Python, pytest, uv, existing controller runners and ManualClock, Jujutsu, PiFire exact-revision gate.

**Spec:** `docs/superpowers/specs/2026-09-08-clock-domain-separation-design.md`

**Integrated baseline:** The coupled learning implementation is now in the working tree: trace 10, observation 4, evidence 6, database 13, required independent wall endpoints, and callable clocks through Hold/runner factories and reconfiguration. References below to Main's “in-flight” work describe the drafting baseline, not remaining implementation. Reconcile those steps against the shared spec before executing; the destination shared `Clock` API and remaining timers are still proposals.

## Execution record

The user subsequently authorized sequential execution of all six migration plans. The local `massive-reworks-and-new-ui` bookmark was moved to the completed coupled-learning revision `66e814cb`; remaining migrations are developed in its child revision, without pushing.

- Classical PID constructor, target reset, elapsed read and completion stamp now consume the injected monotonic clock. Equations, the existing denominator floor, two-read update ordering and both golden arrays remain unchanged.
- Shared runner injection was brought forward as a prerequisite: one `CallableClock` adapter preserves existing callable inputs through initial/fallback construction and synchronous/threaded reconfiguration. Hold supplies its context clock. PID-SP accepts the shared clock while retaining its explicit callbacks until the coordinated cleanup.
- The simulation and golden harnesses use explicit `ManualClock` injection, not a global wall-clock patch. `now()` still means wall time; its complete removal and explicit constructor-axis naming remain the shared-mode cutover.
- Before the fix, a backward one-hour correction changed a 20-second PID interval to 0.001 seconds and raw demand from 1.1875 to -1248.75. The real-core smoke now reports `[20.0, 20.0, 20.0]` and maximum raw-output difference `0.0` for both correction signs.
- Focused integration: **322 passed**; evidence in `.artifacts/pid-clock-focused.log` and `.artifacts/pid-clock-smoke.log`. The real Hold trace regression validates the completed-update prefix with nonzero solve/publication delay; it does not claim that classical PID supplies learning outcomes during terminal frame closure.
- Shared-mode, probe/peripheral identity and separately reviewed suspend policy remain deployment prerequisites. Focused checks are not exact-revision release or push authorization; the final gate runs only on the complete coordinated revision.

## Global Constraints

- This document is a proposal, not authorization to implement remaining PID work. No tests, build, lint or format commands were run while drafting.
- The user separately approved the coupled learning boundary: learning capture, replay/evidence semantics and necessary pulse/PID-SP timestamp producers. Main owns that **in-flight implementation**. Do not describe it as already complete or duplicate its edits.
- Destination API: `Clock.wall_time() -> float` epoch seconds, `Clock.monotonic() -> float` steady same-boot seconds, and unchanged `sleep(seconds)`. `RealClock` delegates to `time.time`, `time.monotonic`, `time.sleep`.
- `ManualClock(wall_start=0.0, monotonic_start=0.0)` has independent axes; `advance`/`sleep` advance both, `jump_wall(delta)` changes wall only. Shared-mode owns this destination API.
- During Main's coupled implementation, adding explicit monotonic time while `now()` retains its wall meaning is permitted. The later shared-mode plan removes `Clock.now` after the complete consumer classification; do not remove it piecemeal or silently redefine it.
- Wall timestamps are event provenance/calendar data, not physical duration, freshness, ordering or model authority. Never infer a domain from magnitude. Durations never derive from wall endpoints.
- Monotonic freshness requires compatible boot/runtime identity. Unknown identity cannot be live; restart closes old segments and does not subtract old-boot coordinates from new uptime.
- Preserve controller numerical policy: `MIN_ELAPSED_SECONDS = 1e-3`, integral equations, derivative equations, branch ordering, output clamps and existing normal-cadence golden values. A duplicate-instant derivative redesign is **not** part of this plan.
- Preserve evidence admission, cook/installation identity, estimator seed requirements, replay validation and mutation expectations. Keep the exact unique anchors `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.` unchanged.
- PID-SP `Controller.update()` returns historical raw signed demand; `self.u`, allocation, runner duty and physical pulses remain bounded. Shared-base changes must not clip that public return.
- The shared spec explicitly selects trace schema 10 and trajectory observation schema 4 for the coupled migration (source trace schema was 9 before it). Import production-owned current constants; do not invent another version or expose fixture overrides.
- `FrameObservation.frame_start_s/frame_end_s` are monotonic; required `wall_start_ms/wall_end_ms` are independently captured nonnegative integer provenance and may regress. Main is migrating these constructors and fixtures now.
- Suspend invalidates live control/history; unobserved delivery must never be fabricated. Detector implementation, threshold and platform behavior require separate review. An arbitrary one-second loop-gap threshold is not approved.
- Read `AGENTS.md` and the Jujutsu skill when executing. No raw Git operations or direct `jj git push`; only the exact-revision gate can authorize push evidence, and this task authorizes no push.

---

## Ownership and dependencies

1. **Shared-mode:** `2026-09-08-shared-mode-timers-clock-migration.md` owns the complete `Clock`/fake API, remaining shared timer cutover and `Clock.now` removal. It also owns runner and Hold clock propagation; coordinate those files once rather than implementing competing adapters.
2. **PID-SP:** `2026-09-08-pid-sp-clock-migration.md` consumes the base injection/setter changes here. Its necessary producer timestamps are already Main-owned in flight; future work must reconcile with that implementation, not overwrite it.
3. **Pulse:** `2026-09-08-pulse-scheduling-clock-migration.md` owns remaining pulse integration and proposed discontinuity policy. Its learning-facing monotonic producer correction is Main-owned in flight.
4. **Learning/trace:** Main owns trace10/trajectory-observation4 migration, exact replay/evidence ordering, required frame wall endpoints, historical admission and all affected current-contract builders. The previous warming fix alone does not satisfy this dependency.
5. **Joint deployment gate:** PID-SP, pulse interval and shared-mode clocks must not deploy with mixed physical axes. Separate plans are independent review units, not permission to hot-reload half a control axis.
6. **Probe:** fresh measurements must arrive under the probe plan's compatible receipt-time/identity contract. PID arithmetic cannot compensate for an invalid or stale probe reading.

## Source-derived producer/consumer inventory

Anchors reflect source research on 2026-09-08; refresh ranges and LSP references before execution because Main is changing overlapping producers.

| File/symbol | Current behavior and disposition |
| --- | --- |
| `controller/base.py:158-183`, `ControllerBase.__init__/set_target` | Constructor accepts logger only; generic setter writes wall `last_update`. Add clock injection and monotonic stamp. Consumers include PID base, MPC subclass and direct base capability tests. MPC numerical/solve timing is not changed. |
| `controller/pid_base.py:18-51`, `_elapsed_since_last_update`, `set_target` | Floors `current_time - last_update` to 1 ms and resets target using wall. Preserve floor exactly; only change the source for target reset. |
| `controller/pid.py:44-117`, constructor/update | Reads wall at initialization, elapsed calculation and completion stamp. Replace those readings with injected monotonic calls, preserving the two-read update ordering and all equations. |
| `controller/base.py.PidTraceDiagnostics` | `previous_update_time` becomes a declared monotonic diagnostic coordinate; `observed_dt_seconds` retains its existing effective/floored PID dt meaning. Main must encode this under the explicit current trace contract, not reinterpret it as epoch. |
| `controller/runtime/runner.py`, `build_runner/_build_core/_wrap` | Dynamic `importlib.import_module(f"controller.{controller_type}")`; fallback calls `_build_core` again. Clock must survive initial, fallback and wrapper construction. Shared-mode execution owner. |
| `SyncControllerRunner.__init__/reconfigure`, `ThreadedControllerRunner.__init__/reconfigure` | Existing separate `monotonic_clock`/`wall_clock` callables; replacement construction must not revert PID cores to real time. Both initial and reconfigure paths are consumers. |
| `controller/runtime/modes/hold.py.HoldMode.setup` | Calls `build_runner` with context services; destination passes context Clock. Main's necessary PID-SP producer injection may already cover a portion. |
| `common/controller_deps.py`, `controller/controllers.json` | Configuration-selected controllers evade static reference completeness. No selection schema change needed. |
| `tools/experiments/controller_matrix.py._SimClock/run_scenario` | Global `time.time` patch currently drives PID-family simulated dt. Explicit injected Clock must replace this dependency without changing physical scenario coordinates or MPC simulation behavior. |
| `tests/characterization/test_pid_controllers_golden.py._Clock/_run_variant` | Wall monkeypatch supplies 20-second intervals. Move to explicit Clock and keep both `GOLDEN` arrays unchanged. |
| `tests/unit/controller/test_pid_base.py` | Gain tests plus duplicate wall monkeypatch; current duplicate test pins an artificial large derivative range. Replace implementation-pinning bounds with a behavioral duplicate equivalence test without changing the production floor. |
| `tests/unit/controller/test_controller_trace_diagnostics.py` | Iterator clocks deliberately distinguish update read at 100 from completion stamp at 102. Preserve that sampling order in the injected clock fixture and preserve expected -0.38 arithmetic. |
| `test_controller_construct_smoke.py`, `test_controller_capabilities.py`, `test_matrix_harness_sim_clock.py` | Dynamic constructor and simulation contracts; update fakes if required, no collected-test imports. |
| PID-SP tests and direct callers | `test_pid_sp.py`, `test_pid_sp_learning.py`, `test_pid_sp_model_selection.py`, e2e real-cook test, `test_sync_runner.py`; PID-SP plan/Main coordinate these consumers. |
| Trace serializers/replay/import | `common/control_trace.py`, `controller/runtime/control_trace_session.py`, `controller/model_learning/trace.py`, `file_mgmt/cookfile.py`; Main owns the trace migration. Wall envelopes remain wall, replay uses explicit monotonic fields and causal identities. |

LSP references were inspected for `ControllerBase`, PID/PID-SP `Controller`, PID-base `set_target`, `build_runner` and `_build_core`. AST dynamic-import search additionally found controller registry and experiment/characterization factories. Before editing, repeat exported-symbol references and inspect both actual reconfigure methods; LSP alone does not discover every configuration-selected helper.

## Numerical and lifetime policy

- For identical monotonic readings and temperature/target histories, output must be identical regardless of wall history, including ±3600-second corrections.
- Keep `_elapsed_since_last_update(current_time) == max(current_time - last_update, MIN_ELAPSED_SECONDS)`. This is effective PID dt, not new evidence of physical delivery. Do not change duplicate updates into no-ops, freeze integral or force derivative zero as part of a clock migration.
- Preserve current ordering of PID's elapsed read and completion read. Combining them into a single read changes the effective next interval and iterator-clock characterization; any such sampling cleanup requires a separately reviewed behavior decision.
- A genuinely nonmonotone/nonfinite supplied monotonic clock violates the runtime source contract. Do not use wall clamping to hide it or silently rebase retained histories. Safe-stop detection/recovery changes are a separate reviewed shared-runtime policy; this plan does not introduce a new exception surface or numerical dt cap.
- No PID dynamic timestamps, integral, derivative or live diagnostics are persisted for restart. Construct a fresh controller and target reset at its new runtime origin.
- Review point: suspend detection may compare Linux boot-time with monotonic time, but thresholds/platform support are not decided here. Regardless of detector design, no resumed old interval may be claimed as observed delivered heat.

### Task 1: Inject the clock without changing PID equations

**Files:** Modify `controller/base.py`, `controller/pid_base.py`, `controller/pid.py`; test `tests/unit/controller/test_pid_base.py`, `tests/unit/controller/test_controller_trace_diagnostics.py`. Coordinate PID-SP constructor changes from its plan.

**Interfaces:** `ControllerBase(config, units, cycle_data, *, logger=None, clock=None)` and `pid.Controller(..., *, logger=None, clock=None)` store/forward the shared Clock. `_elapsed_since_last_update(current_time)` signature and numerical behavior remain unchanged.

- [ ] Add the following regression in `test_pid_base.py`, using the destination ManualClock after shared-mode supplies it:

```python
from controller.runtime.clock import ManualClock


def _pid(clock):
    core = PIDController(
        {"PB": 20.0, "Ti": 10.0, "Td": 5.0, "center": 0.5},
        "F", {}, clock=clock,
    )
    core.set_target(200.0)
    return core


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_pid_wall_jump_preserves_response(jump):
    a = ManualClock(wall_start=1_700_000_000.0, monotonic_start=100.0)
    b = ManualClock(wall_start=1_700_000_000.0, monotonic_start=100.0)
    left, right = _pid(a), _pid(b)
    for index, measured in enumerate((190.0, 195.0, 198.0)):
        a.advance(20.0)
        b.advance(20.0)
        if index == 1:
            b.jump_wall(jump)
        assert right.update(measured) == pytest.approx(left.update(measured))
        dl, dr = left.trace_diagnostics(), right.trace_diagnostics()
        assert dr.observed_dt_seconds == dl.observed_dt_seconds == 20.0
        assert dr.integral_term == pytest.approx(dl.integral_term)
        assert dr.derivative_term == pytest.approx(dl.derivative_term)


def test_pid_target_reset_uses_monotonic_origin():
    clock = ManualClock(wall_start=1_700_000_000.0, monotonic_start=100.0)
    core = _pid(clock)
    clock.advance(20.0)
    core.update(198.0)
    clock.jump_wall(-3600.0)
    core.set_target(210.0)
    clock.advance(2.0)
    core.update(205.0)
    assert core.trace_diagnostics().observed_dt_seconds == 2.0
    assert core.trace_diagnostics().integral_accumulator == -10.0
```

- [ ] Run `uv run pytest tests/unit/controller/test_pid_base.py -q` and record pre-change failure. If the shared Clock prerequisite is not yet present, land that prerequisite before recording the arithmetic regression; an unavailable fixture is not proof of the wall-jump defect.
- [ ] Add the `clock=None` keyword to `ControllerBase.__init__`, import `RealClock` from `controller.runtime.clock`, preserve existing assignments and logger behavior, then add:

```python
self._clock = RealClock() if clock is None else clock
```

  In the base `set_target`, replace only `self.last_update = time.time()` with `self.last_update = self._clock.monotonic()`.
- [ ] Change PID-base `set_target`'s timestamp source the same way. Keep its error/integral/derivative reset order and helper code unchanged:

```python
return max(current_time - self.last_update, MIN_ELAPSED_SECONDS)
```

  Update its elapsed helper docstring to describe monotonic source and existing duplicate-reading denominator protection. Do not claim this floored dt establishes delivered actuator duration.
- [ ] Add `clock=None` to `pid.Controller.__init__` and forward `super().__init__(config, units, cycle_data, logger=logger, clock=clock)`. Replace constructor initialization with `_clock.monotonic()`. In `update`, preserve the existing location/order of these statements:

```python
dt = self._elapsed_since_last_update(self._clock.monotonic())
# Existing integral/clamp/derivative/output arithmetic remains unchanged.
self.last_update = self._clock.monotonic()
```

  Keep `self.derv = (current - previous_temperature) / dt`, the raw `self.u` return, and all diagnostic fields. Remove unused `time` imports only after confirming there are no remaining wall-provenance uses.
- [ ] Replace the old duplicate test's implementation-specific -4000/-3000 band with `test_duplicate_pid_readings_preserve_response_across_wall_jump`: construct two injected controllers, apply the same first and second temperatures at an unchanged monotonic instant, jump one wall axis between calls, assert both raw outputs and diagnostics are equal and finite. Add a normal 20-second continuation and assert both outputs remain equal. This protects duplicate-clock behavior without redesigning or pinning the 1 ms implementation constant.
- [ ] Preserve `test_pid_trace_diagnostics_reproduce_completed_update`'s exact terms and -0.38 output. Supply a local Clock fixture whose `monotonic()` iterator reproduces the existing constructor/reset/update/completion read sequence `(0, 0, 0, 100, 102)` and whose wall method returns a distinct epoch. Keep direct `last_update=98`/`last=190` as the existing isolated arithmetic setup. Assert subsequent diagnostic consumer semantics, not clock call counts.
- [ ] Run `uv run pytest tests/unit/controller/test_pid_base.py tests/unit/controller/test_controller_trace_diagnostics.py -q` after the PID-SP constructor consumer is reconciled. Normal-cadence outputs must not be recaptured.

### Task 2: Preserve injection through dynamic construction and simulated execution

**Files:** Shared-mode owner edits `controller/runtime/runner.py`, `controller/runtime/modes/hold.py`; PID owner edits `tools/experiments/controller_matrix.py`, `tests/characterization/test_pid_controllers_golden.py`, `tests/unit/controller/test_matrix_harness_sim_clock.py`. Main currently owns changes to experiment FrameObservation constructors; serialize that shared-file edit with Main.

**Interfaces:** Destination `build_runner(..., *, clock=None)`, `_build_core(..., *, clock=None)`, `_wrap(..., *, clock=None)` pass one source through initial/fallback/replacement construction. Existing runner `monotonic_clock`/`wall_clock` callable seams remain supported and must not silently lose fake inputs.

- [ ] Shared-mode owner supplies the resolved Clock from Hold context to `build_runner`; passes it through both `_build_core` calls and both `_wrap` paths. Only PID-family core constructors get the PID Clock keyword:

```python
if controller_type in {"pid", "pid_sp"}:
    controller_kwargs["clock"] = clock
```

  Resolve the source once at the initial builder boundary, not independently for wrapper/core. `_wrap` supplies its existing runner timing keywords from `clock.monotonic` and `clock.wall_time`. Preserve the same source when `SyncControllerRunner.reconfigure` and `ThreadedControllerRunner.reconfigure` invoke `_build_core`. Direct runner tests with explicit timing callables retain that contract; shared-mode owns any required adapter with a single implementation.
- [ ] Add `test_clock_survives_pid_reconfigure` in `test_sync_runner.py` using its real selected-controller settings helper: build PID with ManualClock, advance 20 seconds, solve, reconfigure PID-SP and back to PID, jump wall, advance 20 and solve again. Assert accepted result dt=20 and completion wall metadata equals the fake epoch. Extend the existing builder-failure fallback case to assert a fallback PID solve observes the injected physical interval. Do not merely assert forwarded object identity. Shared-mode owns exact runner test integration.
- [ ] In `_run_variant`, construct `ManualClock(wall_start=T0, monotonic_start=T0)`, pass `clock=clock`, and `advance(STEP)` before each update. Delete the old global wall monkeypatch dependency and leave both `GOLDEN` arrays unchanged. Normal sampling at a fixed fake instant still preserves PID's existing two-read behavior.
- [ ] In `run_scenario`, replace PID-family dependence on global `time.time` patching with an explicit Clock initialized at the same simulated physical origin (currently `-float(AUGER_TIMING.frame_s)`). The wall axis starts at a nonnegative epoch suitable for required provenance. Constructor cutover is:

```python
controller_kwargs = {"clock": clock} if controller in {"pid", "pid_sp"} else {}
core = mod.Controller(dict(core_config), "F", dict(cycle_data), **controller_kwargs)
```

  For each existing simulated physical coordinate `t`, call `clock.advance(t - clock.monotonic())` only as the scenario advances forward. Use `clock.monotonic()` for scheduler/FrameObservation interval bounds and independent captured `clock.wall_time()` values for required wall endpoints. Preserve Main's in-flight frame-wall construction changes rather than reconstructing them from one old offset. Do not pass unsupported PID-only kwargs to MPC or change its existing simulation-time API.
- [ ] Remove `_SimClock`/global patch and corresponding restoration only after every remaining consumer in this function is explicitly clocked. Preserve the simulation's target-reset ordering and physical scenario duration; do not offset setpoint time to conceal a duplicate dt.
- [ ] Run `uv run pytest tests/characterization/test_pid_controllers_golden.py tests/unit/controller/test_matrix_harness_sim_clock.py tests/unit/controller/test_controller_construct_smoke.py tests/unit/controller/test_controller_capabilities.py tests/unit/runtime/test_sync_runner.py tests/unit/runtime/test_controller_build_failure.py -q` after shared owners finish overlapping edits.

### Task 3: Verify restart boundaries, trace meaning, and real controller output

**Files:** Existing `test_pid_base.py`, `test_hold_orchestration.py`, `test_hold_control_trace.py`, `test_control_trace_replay.py`; Main-owned trace producers/consumers from the inventory.

**Interfaces:** Fresh controller construction on restart; trace10 explicit monotonic diagnostic coordinates and wall envelopes; no new PID checkpoint fields.

- [ ] Add `test_pid_restart_does_not_restore_old_elapsed_state`: drive an old core at mono≈10,000; create a new core at mono=5 retaining only configuration and target, advance 2, update 198 F, assert dt=2 and integral accumulator=-4 for the Task 1 configuration. Do not copy `last_update`, integral, derivative or live diagnostics across the boundary.
- [ ] Validate Main's trace producer output with a real PID result at wall≈1,700,000,000 and mono≈100. Assert envelope `ts_ms` is wall, PID previous-update coordinate is explicitly monotonic under current serialization, and replay reconstructs the same output with nonzero solve/publication delay and a backward wall correction. Historical traces remain literal versioned input and are rejected as current exact evidence when the old mapping is unknown; no field relabeling or numeric-domain guessing.
- [ ] Record the suspend/restart review requirement without implementing a new threshold: after the shared-mode owner obtains policy approval and supplies its actual detector, its Hold scenario must assert output inhibition before resumed solve, no unobserved delivered seconds, old result/history invalidation, and fresh probe/seed admission. This unresolved policy does not permit claiming hardware suspend safety from a PID clock unit test.
- [ ] Run the focused set:

```bash
uv run pytest tests/unit/controller/test_pid_base.py tests/unit/controller/test_controller_trace_diagnostics.py tests/characterization/test_pid_controllers_golden.py tests/unit/controller/test_matrix_harness_sim_clock.py tests/unit/runtime/test_sync_runner.py tests/unit/runtime/test_controller_build_failure.py tests/unit/runtime/test_hold_orchestration.py tests/unit/runtime/test_hold_control_trace.py tests/unit/controller/test_control_trace_replay.py -q
```

- [ ] Write `/tmp/pid_clock_smoke.py` with the real PID two-controller wall-jump schedule shown in Task 1, execute `uv run python /tmp/pid_clock_smoke.py`, and print/assert dt series `[20.0, 20.0, 20.0]` and maximum raw-output difference `0.0`. Preserve stdout and remove the script afterwards. This is core smoke proof, not live grill/hardware verification. The real Hold/trace scenarios above are separate required evidence.

## Migration, rollback, cleanup, and release

- Do not add persistence for PID dynamic state. Stop/inhibit control before installing a coordinated clock-domain revision, finish/drain old workers and close the old trace/learning segment under Main's terminal contract. Restart with fresh controller state; never reinterpret an existing instance's timestamps during hot reload.
- Rollback also requires a coherent-version stop/restart, not replacing only PID-SP or pulse. Preserve new archives and exact-revision evidence. If the older reader cannot decode trace10/observation4, do not let it open those records as current evidence; use an identified compatible pre-cutover backup or stay stopped. Never relabel schema fields or delete audit evidence.
- After smoke proof, remove throwaway scripts, update affected existing controller/simulation docstrings and the existing release changelog entry as appropriate. No parallel clock documentation or schema-override helpers. Review intentionally unchanged historical fixtures separately from current-contract builders.
- Final integration owner runs `prek run --all-files` explicitly; Jujutsu describe/new does not execute hooks. Use established Jujutsu changes, not raw Git commits. Stop editing before exact-revision evidence collection.
- With the authorized bookmark on the frozen revision, run:

```bash
uv run python scripts/exact_revision_gate.py verify-bookmark --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

- The wrapper must run the separate preflight `uv run pytest tests/unit/test_no_cross_test_imports.py tests/unit/mpc/test_mutation_score.py tests/unit/common/test_current_contract_fixtures.py -q` before all five commands: `./rebuild-acados.sh --if-needed`, `uv run pytest tests/`, `uv run pytest -m slow tests/`, `bun run test` at root, `bun run test:e2e` in `web-react`. Failed/interrupted/revision-drifted preflight leaves all five not run. Focused tests cannot substitute for these.
- Preserve schema v2 evidence, separate preflight stdout/stderr and SHA-256, all command logs in `.artifacts/exact-revision/<full-revision>/evidence.json` and referenced paths, and before/after/final publication revision checks. Missing/skipped/interrupted/timed-out/nonzero/reordered/different-revision commands fail closed. Schema v1 evidence is historical only.
- No push is authorized. If later requested, use only `uv run python scripts/exact_revision_gate.py push --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision` or repository `jj push` alias invoking it. It revalidates remote revision and local artifacts; direct `jj git push` is prohibited.

## Completion checklist

- [ ] Both signs of a one-hour wall jump leave raw output, PID dt and terms unchanged for identical physical readings; duplicate behavior and ordinary goldens remain unchanged.
- [ ] All constructor/reset/factory/fallback/reconfigure/simulation consumers use the declared source; no shared Clock implementation is duplicated.
- [ ] Trace10, observation4 and required wall frame provenance agree with Main's completed implementation, not assumptions about in-flight work.
- [ ] Fresh restart state cannot import prior-boot timing; suspend detector and numerical-policy redesign remain explicit separately reviewed work.
- [ ] PID-SP/pulse/shared-mode joint axis gate, strict evidence/model anchors and exact-revision release evidence are satisfied at execution.
