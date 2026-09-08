# Pulse Scheduling Clock Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make physical pulse accounting independent of wall corrections without manufacturing delivered fuel, completed history, or replay authority across observation gaps.

**Architecture:** The shared-mode cutover supplies one monotonic control axis to Hold, its runner, and framed actuation. Frame/feedback coordinates stay on that axis; trace envelopes capture wall time separately. A discontinuity ends admissible history at the last observed instant, forces hardware safe, and requires a new runtime generation rather than advancing the scheduler through unknown time.

**Tech Stack:** Python, injectable runtime clocks, pytest, existing framed scheduler and trace/trajectory persistence.

**Spec:** `docs/superpowers/specs/2026-09-08-clock-domain-separation-design.md`

**Integrated baseline:** The coupled learning implementation is now in the working tree: trace 10, observation 4, evidence 6, database 13, required independent wall endpoints, and callable clocks through Hold/runner factories and reconfiguration. References below to Main's “in-flight” work describe the drafting baseline, not remaining implementation. Reconcile those steps against the shared spec before executing; the destination shared `Clock` API and remaining timers are still proposals.

## Execution record

The user authorized sequential execution and the shared spec's **Approved
discontinuity and timer policy** supersedes the draft approval restrictions below:
60 seconds for a genuine monotonic observation gap or suspend-offset discontinuity;
wall-only corrections never trigger retirement. The 30-second watchdog is unchanged.

Implemented the pulse development unit: direct no-advance gap invalidation,
discarded partial feedback at the last observed instant, cleared credit, suppressed
thermal history, and Hold hardware-OFF retirement with immutable retry cutoffs.
The shared-mode plan remains responsible for calling this hook before actuation,
generation rotation, and the disconnected-platform full-loop proof. This unit is
not independently deployable.

Verification: 132 focused clock-domain/framed-pulse/Hold tests passed, followed by
358 clock/PID-SP/golden/pulse/Hold-trace/cookfile integration tests. A direct
production FramedPulseRuntime smoke with wall corrections 0 and ±3600 seconds
kept the OFF edge at monotonic 22, two known delivered seconds, and a discarded
gap cutoff at 22 after 120 unobserved seconds; repeated invalidation emitted no
completion. Independent runtime and Hold reviews reported no findings.

The new clock stamp remains a typed value with an explicit TypedDict wire payload;
no Any, casts, or type-ignore escape hatches were introduced. PID/PID-SP clock
injection annotations were audited and completed. Common clock-domain and runtime
clock static diagnostics are clean. Release commands and push have not been run.

## Global Constraints

- This is a proposal, not authorization to implement. Source inspection is the evidence for this plan; no tests, builds, formatters, hardware experiments, or gate commands were executed while writing it.
- `Clock.wall_time() -> float` is epoch seconds; `Clock.monotonic() -> float` is same-boot steady seconds; `sleep(seconds)` is unchanged. Remove `Clock.now()` only in the coordinated shared-mode cutover.
- Preserve `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.` exactly and uniquely. This plan does not authorize a model-schema change.
- PID-SP `Controller.update()` returns historical raw signed demand. `self.u`, allocation, runner duty, and physical pulses stay allocation-bounded.
- Preserve durable cook identity, estimator seed admission, observation completeness, calibration identity, frame/result revisions, causal sequence, mutation and golden expectations. Unknown boot/runtime provenance cannot be live. No numeric-magnitude domain guessing.
- Wall event metadata can regress. Physical durations come only from explicit monotonic intervals. Latest evidence is durable causal order, never highest wall timestamp.
- Suspend is not proof of active control. Do not credit an unobserved interval, fabricate pulses, weaken `PulseScheduler` monotone rejection, or solve backward wall input by clamping it.
- All implementation/VCS work follows `AGENTS.md`, the jujutsu skill, and the sole exact-revision authority `scripts/exact_revision_gate.py`. No direct `jj git push` or raw Git workflow.

## Dependency and Landing Gate

1. `2026-09-08-shared-mode-timers-clock-migration.md` owns Clock/fakes, `ControlMode` timer axis, runner clock injection, and discontinuity admission before actuation.
2. `2026-09-08-pid-sp-clock-migration.md` owns remaining PID-SP clock integration after Main's necessary producer correction; `2026-09-08-pid-clock-migration.md` owns PID timing. Current-update and completed-frame coordinates must migrate together. Do not independently deploy either side or change Smith predictor/FOPDT numerical policy.
3. **Main integration in flight, not completed:** the user approved coupled learning capture, replay/evidence semantics and necessary pulse/PID-SP timestamp producers now. The shared spec defines trace 9→10, trajectory observation schema 4, and required `FrameObservation.wall_start_ms/wall_end_ms` with monotonic frame bounds. Rebase this plan onto that verified integration; do not duplicate or overwrite its producers. Historical ambiguous mappings remain fail-closed. The remaining full shared-clock and suspend-policy work is still proposed.
4. `2026-09-08-peripheral-clock-migration.md` owns shared boot/runtime/suspend provenance and persisted user/recipe timer authority. Shared-mode consumes it. No second resume detector here.
5. **Joint deployment gate:** PID-SP interval consumption + pulse interval producers + shared-mode control axis + Main-approved learning/trace contract + required probe/peripheral consumers form one deployable revision. Development tasks are reviewable separately, not independently releasable.

**Approval boundary:** Task 3 and the suspend/gap portions of Task 4 are separate proposed safety-policy work. Detector platform behavior, thresholds and automatic restart policy require explicit review; none is authorized by the coupled clock correction. Preserve controller numerical policy and existing predictor interval coverage; no Smith correction, predictor coverage, derivative, or extrapolation redesign is part of this plan.

## Source-Derived Migration Inventory

Line numbers describe the inspected source and are navigation hints, not edit anchors. Before implementation, obtain LSP references for every changed exported contract and AST-search dynamic builders; refresh this inventory when source moves.

| File / symbols | Current responsibility | Required disposition |
|---|---|---|
| `controller/runtime/modes/hold.py`: `setup`, controller reconfiguration, `on_tick`, `_HoldTickContext`, `_advance_or_reset_framed_pulse`, `_apply_hold_lid_fan_hardware_and_state`, `_on_manual_output`, `_on_manual_release`, `_inhibit_framed_pulse`, `teardown` | Configure at wall `clock.now`; pass tick `now` into scheduler and feedback; cycle/lid/manual state; terminal dispatch | All physical instants monotonic, wall event captures separate; migrate every reset/configuration/teardown route |
| `controller/runtime/framed_pulse.py`: `configure`, `advance`, `reset`, `report_feedback`, `_record_delivery`, `complete_frame`, `_complete_frame`; `FramedPulseCompletion` | Latches revisions, integrates delivered seconds, emits observations and feedback | Document seconds as monotonic; retain arithmetic and strict completeness; add explicit no-advance discontinuity invalidation |
| `controller/runtime/logic/pulse.py`: `PulseScheduler.advance/reset`, `PulseTransition.at_s`, `PulseFrameResult`, `PulseDecision` | Pure scheduler; `advance` rejects decreasing input; skipped-frame path integrates previous hardware state | Keep rejection, duty/credit algorithm, and ordinary valid-history behavior; ensure production never calls through an inadmissible observation gap |
| `controller/applied_output.py`: `AppliedOutput.timestamp`, `seed_output` | Applied physical state feeds controllers | Explicit same-runtime monotonic seconds; every constructor and consumer must migrate together, not redefine as wall metadata |
| `controller/model_learning/contracts.py`: `FrameObservation`; `controller/runtime/observation_buffer.py`, `controller/runtime/runner.py` | Completed frames, ordered buffering, delivery into core | Same monotonic axis and role/result generation; old-worker completion must not enter replacement generation |
| `controller/pid_sp.py`, `controller/smith_predictor.py`, `controller/fopdt_identifier.py` | Completed applied-duty history and model updates | Owned by PID-SP plan; never compare epoch updates with monotonic frame bounds or invent uncompleted trailing duty |
| `controller/runtime/control_trace_session.py`: `TraceFrameContext`, `TraceOutputContext`, `TraceAppliedIntervalContext`, `TraceUpdateContext`, `TraceAppliedState`; `control_trace_recorder.py` | Physical interval coordinates and event envelopes currently share `timestamp_ms` in several routes | Separate event wall publication from interval/application monotonic endpoints; Main-approved trace contract, serializers and consumers must agree |
| `controller/runtime/modes/hold.py`: `_resume_framed_dispatch`, `_trace` calls, seed/reconfigure/reset/teardown/session rotation | Converts `now`, frame endpoints, and applied times into milliseconds | Wall only for envelopes/evidence; monotonic only for explicitly physical payload fields; no mechanical `timestamp_ms=int(now*1000)` replacement |
| `controller/runtime/learning_trajectory.py`, `common/learning_trajectory.py`, `common/persistence/learning_trajectory.py`, `file_mgmt/cookfile.py`, `controller/model_learning/trace.py` | Capture, validate, restore, replay, exact import | Main-owned prerequisites; no epoch-offset reconstruction or wall-duration equality as physical authority |
| `controller/runtime/modes/hold_learning.py`, `controller/runtime/model_lifecycle.py`, `controller/model_learning/grey_runtime.py` | Frame-derived forecasts and evidence | Capture wall evidence timestamps explicitly, retain monotonic forecast horizon and causal authority |
| `controller/runtime/modes/base.py`, `controller/runtime/store.py`, `common/persistence/history.py` | `augerontime`, pellet estimates, epoch history/metrics | Shared-mode owns monotonic elapsed metrics; pulse contributes delivered delta exactly once, not wall-derived usage |
| `tests/unit/runtime/conftest.py`, `tests/fakes/runner.py`, `tests/fakes/learning_trajectory.py`, `tests/characterization/harness.py` | Tick/runner/learning fixtures | Shared clock axes and real contracts; collected tests never import collected tests |
| `tests/unit/runtime/test_hold_pulse_scheduler.py`, `test_framed_pulse_runtime.py`, `test_pulse_scheduler.py`, `test_hold_applied_output.py`, `test_hold_orchestration.py`, `test_hold_control_trace.py`, `test_hold_trajectory_seed.py` | Scheduler, physical ordering, teardown, capture acceptance | Add observable jump/gap regressions; retain existing credit, saturation, ordering, stale and admission tests |
| `tests/integration/test_cookfile_learning_diagnostics.py`, `tests/unit/controller/test_control_trace_replay.py`, `tests/unit/mpc/test_model_learning_trace.py`, `tests/e2e/test_smoke_hold_learning_trajectory.py` | Current-contract trace producers and replay | Paired unequal clock axes, delayed result publication, wall rollback with causal sequence; owned jointly with Main |

Source evidence from inspection before Main's in-flight producer edits: wall `RealClock.now` reached Hold's physical scheduler call. `PulseScheduler.advance` rejects backward input at lines 136–137. `_advance_frames` allocates skipped results across forward intervals; `_skip_frame` credits the previously observed ON state. `FramedPulseRuntime.reset` itself first calls `advance(now, actual_auger_on)`: **calling ordinary reset at resume time is not a safe gap fix**. `_record_delivery` differences cumulative measured seconds; keep its exactly-once baseline across normal resets. These are source findings, not executed reproductions; rebase the later plan on Main's actual integrated producer.

### Task 1: Pin the physical ON → OFF and wall-jump contract

**Files:** Modify `tests/unit/runtime/test_hold_pulse_scheduler.py`, `test_framed_pulse_runtime.py`, `tests/unit/runtime/conftest.py`; shared fake implementation belongs to shared-mode plan.

**Interfaces:** Consume `ManualClock(wall_start=1_800_000_000.0, monotonic_start=20.0)`, `advance(seconds)`, `jump_wall(delta)` and existing `_output`, `_status`, `_advance_runtime`, `_runtime`, `_sample` helpers in their owning modules. Produce regressions that exercise actual fake-grill commands and delivered metrics, not just clock-method calls.

- [ ] Add this physical accounting regression in `test_hold_pulse_scheduler.py` (the existing fixture exposes mutable `hold.ctx.clock`):

```python
@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_wall_jump_does_not_move_physical_off_edge(hold_cycle, jump):
    from controller.runtime.clock import ManualClock

    hold = hold_cycle(FakeControllerRunner(period=1.0), controller="mpc")
    clock = ManualClock(wall_start=1_800_000_000.0, monotonic_start=20.0)
    hold.ctx.clock = clock
    hold.setup()
    hold.state.metrics = {"augerontime": 0.0}
    hold.state.controller.pulse_requested_duty = 0.1
    first = _advance_runtime(hold, clock.monotonic(), False)
    assert first.command_on is True
    assert hold.grill.get_output_status()["auger"] is True
    clock.advance(1.0)
    clock.jump_wall(jump)
    middle = _advance_runtime(hold, clock.monotonic(), True)
    assert middle.command_on is True
    assert middle.delivered_on_s == pytest.approx(1.0)
    clock.advance(1.0)
    final = _advance_runtime(hold, clock.monotonic(), True)
    assert final.command_on is False
    assert hold.grill.get_output_status()["auger"] is False
    assert final.delivered_on_s == pytest.approx(2.0)
    assert final.completed_frames == ()
    assert hold.state.metrics["augerontime"] == pytest.approx(2.0)
```

- [ ] Add `test_control_loop_wall_jump_does_not_skip_frames` using the actual `ControlMode.run` path: script a wall correction from `ManualClock.sleep` after the first ON command; keep monotonic advances at 0.05 seconds; stop after one 20-second frame through the store's mode change. Assert trace frame bounds differ by 20 seconds, no `skipped` frames, exactly one completed observation, the same ON/OFF command sequence and auger seconds as the zero-jump run. This second test is necessary because directly passing `clock.monotonic()` to a scheduler cannot detect a production caller still passing wall time.
- [ ] Retain `test_missed_frames_are_recorded_as_skipped_without_catchup` as a pure/runtime explicit-axis test; add a production-loop gap test in Task 3. Do not reinterpret a genuine gap as a wall correction.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_hold_pulse_scheduler.py -q -k 'wall_jump or wall_jump_does_not_move_physical_off_edge'`; require failure on the unmigrated production caller or missing explicit clock API, then success after Task 2. Do not weaken assertions to make the fixture pass.

### Task 2: Migrate complete frame, feedback, and publication boundaries

**Files:** Modify `controller/runtime/modes/hold.py`, `controller/runtime/framed_pulse.py`, `controller/applied_output.py`, `controller/model_learning/contracts.py`, `controller/runtime/control_trace_session.py`; coordinate Main's trace/schema and PID-SP consumers before editing shared files.

**Interfaces:** Existing physical `now` arguments are monotonic; `_HoldTickContext` adds `wall_time: float` only if Main's integrated producer does not already capture it. Required `FrameObservation.wall_start_ms: int` and `wall_end_ms: int` are independent event provenance; trace schema 10 frame/observation payloads carry them and retain monotonic physical bounds. Reuse Main's actual trace context signatures after integration rather than inventing another interval contract; the illustrative `applied_monotonic_ms`/`end_monotonic_ms` names below describe domain separation, not permission to rename integrated fields.

- [ ] Capture wall once when constructing a Hold tick context and propagate it through immutable replacements. `now` stays the shared-loop monotonic tick instant. Use explicit captures for out-of-tick safety/teardown publication:

```python
# _HoldTickContext fields include:
now: float          # physical monotonic seconds
wall_time: float    # epoch event provenance, never a duration origin

# In the existing _HoldTickContext constructor:
wall_time=self.ctx.clock.wall_time(),

# Physical scheduling remains pure and injected:
result = runtime.advance(
    context.now,
    context.output_status.auger,
    sample=self._framed_sample(context.ptemp),
    prior_output_source=context.trace.applied_state.output_source
    if context.trace is not None else None,
)
# TraceUpdateContext construction retains all other current arguments:
# timestamp_ms=int(context.wall_time * 1000),
# applied_monotonic_ms=int(context.now * 1000),
```

- [ ] Change every Hold setup/reconfigure/lid/manual/seed/reset/cycle/teardown clock access by domain using the inventory. Retain physical `AppliedOutput.timestamp` and observation frame starts/ends as monotonic seconds; document the explicit contract in their production dataclasses, and migrate every consumer discovered by references. Trace payload frame milliseconds are just unit conversion, not epoch conversion.
- [ ] Update `ControlTraceSession.record_frame`, `record_output`, `record_applied_interval`, and `record_update` to consume explicit application/interval instants while preserving epoch envelopes, completion wall metadata and solve-end monotonic metadata separately. A solved result's publication can occur after completion: never require envelope `ts_ms == result.monotonic_ms`.
- [ ] Consume Main's approved producer/importer migration before enabling this later full-clock integration: real runtime trace → persistence → cook export → exact import must preserve sequence/revision joins when event wall regresses. Trace schema 10 is the new current contract; historical schema 9 and earlier retain their own validators. Ambiguous epoch-frame archives remain non-replayable unless their historical decoder has sufficient explicit provenance; never re-label them as monotonic.
- [ ] Verify PID-SP records completed frames on the identical axis and preserves its existing predictor interval coverage checks and numerical policy. Do not add a new projection/correction strategy or change treatment of uncompleted trailing intervals. Keep raw signed public return and allocation bounds unchanged.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_framed_pulse_runtime.py tests/unit/runtime/test_hold_pulse_scheduler.py tests/unit/runtime/test_hold_applied_output.py tests/unit/runtime/test_hold_control_trace.py tests/integration/test_cookfile_learning_diagnostics.py -q`.

### Task 3: Proposed unknown-interval invalidation policy — separate approval required

**Files:** Modify `controller/runtime/framed_pulse.py`, `controller/runtime/modes/hold.py`; shared-mode owns the pre-actuation continuity guard and peripheral owns `common/clock_domain.py` provenance.

**Interfaces:** Add `FramedPulseRuntime.invalidate_observation_gap(*, sample: FramedPulseSample) -> FramedPulseResult`. It has **no resume instant parameter**. Shared-mode calls `ControlMode._on_control_discontinuity(last_observed_monotonic: float, reason: str) -> None`; Hold overrides it to force OFF and invalidate the existing frame. Ordinary `reset(reason, now, ...)` remains for observed safety/lid/manual edges only.

- [ ] Implement gap invalidation using the scheduler's current accumulated state: call `scheduler.reset(PulseResetReason.SAFETY)` directly, never `advance`. The returned partial frame ends at `_last_at_s`; construct its completion through existing `_complete_frame`, with safety inhibit and terminal discarded feedback, mark `sample_complete=False`, and do not treat a post-gap temperature sample as a valid terminal observation. Return zero `delivered_delta_s`, no transition commanding ON, and no synthetic skipped-frame list. Preserve cumulative delivery baselines already reported so metrics neither duplicate nor lose known delivery.

```python
# Core of invalidate_observation_gap; build its result with the existing
# completion/decision constructors and current cumulative delivery values.
scheduler, controller, previous = self._configured()
interrupted = scheduler.reset(PulseResetReason.SAFETY)
# interrupted.ended_at_s is the last observed scheduler instant, not resume.
# Complete the returned partial frame with safety inhibit and discarded
# terminal feedback; a post-gap sample cannot establish sample completeness.
# No call to advance(), _account_until(), or _record_delivery() belongs here.
```

- [ ] In Hold's discontinuity hook, command auger/igniter OFF before callback dispatch; invalidate calibration, predictor/identifier history and estimator seed continuity; persist a safety/trajectory break with actual wall provenance and last observed monotonic boundary. Retire the worker generation, discard late results, and require a fresh health read and authorized safe restart with a new runtime/clock identity. Do not auto-resume remembered manual ON state or rebuild pulse credit.
- [ ] Add `test_observation_gap_does_not_claim_delivery`: advance frame at 20 OFF→ON, observe at 21 ON (one second delivered), simulate suspend while wall or monotonic moves, invoke invalidation, and assert delivered total remains 1, partial frame ends at 21, `complete=False`, feedback is `DISCARDED`, credit is zero, no ON command, no new complete observation. Repeat for both an ON and OFF last state because an ON state is the fabrication hazard.
- [ ] Add `test_resume_rejects_old_result_and_requires_fresh_generation`: queue a completed result in the old worker, trigger shared continuity rejection, deliver it after resume, and assert no positive hardware command and no accepted frame until a newly seeded authorized generation produces a fresh result.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_framed_pulse_runtime.py tests/unit/runtime/test_hold_orchestration.py tests/unit/runtime/test_hold_pulse_scheduler.py -q -k 'gap or resume or stale or teardown'`.

### Task 4: Preserve terminal teardown, retries, and evidence exactly once

**Files:** Modify `controller/runtime/modes/hold.py` (`_HoldTeardownState`, `teardown`, `_resume_framed_dispatch`), `tests/unit/runtime/test_hold_orchestration.py`, `test_hold_pulse_scheduler.py`, `test_mode_metrics_staging.py`.

**Interfaces:** Teardown stores a monotonic physical cutoff and separate wall publication capture; `_TeardownPhase` and prepared dispatch objects remain retry guards. Normal observed stop uses ordinary final accounting; known discontinuity uses Task 3 invalidation and cannot re-enter `runtime.advance(resume_now)` later.

- [ ] Replace `max(clock.now(), last_tick)` wall masking with one explicit monotonic capture validated against the continuity guard. Store the chosen terminal cutoff once; subsequent retries cannot extend it. Add a teardown state flag marking discontinuity finalization so later phase retries skip ordinary final advance/reset.
- [ ] Preserve order: capture pre-OFF output → force hardware OFF → finalize only the known interval → dispatch feedback once → stop/join runner → drain persistence/barrier → schedule stop fit only when stopped and durable → close trace/learning in existing finally blocks. Do not label unknown delivery complete to save a fit.
- [ ] Add `test_wall_jump_during_teardown_keeps_one_terminal_interval`: after 2 delivered seconds inject wall rollback, fail one callback then retry; assert hardware is OFF before first callback, final auger metric remains 2, one terminal frame and one terminal feedback, trace envelopes retain actual wall provenance, and no duplicate fit scheduling.
- [ ] Add `test_discontinuity_teardown_never_extends_last_observation`: gap invalidation followed by two teardown calls at much later clock instants cannot increase delivered seconds, close a complete interval, restore credit, or accept late worker evidence.
- [ ] At implementation time run `uv run pytest tests/unit/runtime/test_hold_orchestration.py tests/unit/runtime/test_hold_pulse_scheduler.py tests/unit/runtime/test_mode_metrics_staging.py tests/unit/runtime/test_hold_trajectory_seed.py -q`.

## Deterministic Acceptance and Operational Proof

- Wall-only ±3600-second jumps at physical monotonic 21 do not change the 22-second OFF edge, two delivered seconds, requested/realized bounded duty, frame count, or completed-history sequence.
- At epoch wall 1,800,000,000 and monotonic 20, frame payloads remain near 20–40 seconds while envelopes remain epoch milliseconds. A delayed solver publication has distinct solve-end/application/event coordinates and imports successfully under the new exact contract.
- Resume/ordinary inadmissible observation gaps end history at last observation, not at resume. Unknown boot/runtime identity, rejected sample completeness, and old-generation completion cannot become estimator seed or activation evidence.
- The pure scheduler still raises `ValueError("at_s must be monotone")` for an actual monotonic regression. Production prevents wall corrections from reaching that invariant; it does not suppress it.
- Implementation must additionally exercise the real control loop with a disconnected/fake grill platform and independent dual clock, recording actual ON/OFF commands and trace archive. Hardware-in-loop fuel delivery is not claimed by a fake-platform proof. Do not run a fueled grill merely to test wall corrections.

## Migration, Restart, Rollback, and Release

No hot mixed-axis rollout. Stop active control safely, drain workers and persistence, preserve traces/metrics, then deploy the whole joint-gate revision. Re-enter through startup/health/estimator admission; do not restore in-memory frame credit, deadline, PID predictor history or worker results. Persisted monotonic coordinates need boot/runtime identity; unknown historical data is audit-only, never fresh.

Rollback is also a stopped deployment of a coherent old revision. New physical trace contracts must be rejected by an old reader unless an explicitly reviewed decoder exists; retain archives, do not rewrite their numbers or discard them. Operator restart is required after discontinuity; no automatic ON replay. User/recipe timers follow the peripheral authority's persisted downtime/resume policy rather than restoring raw mode deadlines.

After implementation and smoke proof, update existing `controller/runtime/README.md` and relevant existing trace/learning documentation with the approved contract; remove throwaway smoke scripts. Do not create arbitrary documentation or golden rewrites as cleanup.

Run targeted commands above during authorized implementation, then integration owner runs commit checks and the sole release authority once against the complete revision:

```bash
uv run pytest tests/unit/runtime/test_pulse_scheduler.py tests/unit/runtime/test_framed_pulse_runtime.py tests/unit/runtime/test_hold_pulse_scheduler.py tests/unit/runtime/test_hold_orchestration.py tests/unit/runtime/test_hold_control_trace.py tests/integration/test_cookfile_learning_diagnostics.py -q
prek run --all-files
uv run python scripts/exact_revision_gate.py verify-bookmark --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
# Only when publication is explicitly requested:
uv run python scripts/exact_revision_gate.py push --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

The wrapper owns contract preflight and all five ordered release commands. Failed/interrupted/revision-drifted preflight means the five remain not run. Only schema-v2 evidence authorizes push; preserve `.artifacts/exact-revision/<full-revision>/evidence.json` and every referenced stdout/stderr/hash, including separate preflight evidence. Do not claim focused tests or this plan authorize publication.
