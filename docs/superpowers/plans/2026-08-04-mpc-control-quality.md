# MPC Control Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans task-by-task. Use TDD and verification-before-completion. Use LSP for symbol lookup/references/refactors and file renames.

**Goal:** Give MPC one normalized, physically coupled combustion command; realize it with measured 2-second pulses in 20-second frames; preserve asynchronous safety-loop actuation; add equilibrium feed-forward and honest upper reachability; and remove the unsupported braking-horizon floor after measurement.

**Architecture:** MPC optimizes normalized combustion load `q ∈ [0,1]`. A coupled allocator maps it to mean auger duty and fan duty. Hold's framed-pulse scheduler realizes auger duty while measuring actual delivery. Revisioned threaded results are consumed when available without blocking actuation. Shared SQLite traces link solve, allocation, frame, hardware, and estimator feedback.

**Tech Stack:** Python 3.14, numpy/scipy, do-mpc/CasADi/IPOPT, Pydantic, SQLite, pytest, React/TypeScript, Jujutsu.

**Approved design:** `docs/superpowers/specs/2026-08-04-mpc-control-quality-design.md`

**Prerequisites, in order:**

1. `docs/superpowers/plans/2026-08-04-controller-catalog-cleanup.md`
2. `docs/superpowers/plans/2026-08-04-controller-control-trace.md`

This plan assumes only PID/PID-SP/MPC remain, shared trace types/recorder/revisioned runner results exist, settings schema version 4 is current, and MPC CSV logging is gone.

---

## Task 1: Normalize combustion load and preserve coupled fuel/air allocation

**Files:**

- Modify: `controller/mpc_model.py`
- Modify: `controller/mpc_allocator.py`
- Modify: `controller/mpc.py`
- Modify: `controller/mpc_net.py`
- Modify: `controller/controllers.json`
- Modify: `common/settings_migration.py`
- Modify: `common/settings_schema.py`
- Modify: `tests/unit/mpc/test_mpc_model.py`
- Modify: `tests/unit/mpc/test_mpc_allocator.py`
- Modify: `tests/unit/mpc/test_mpc_controller.py`
- Create: `tests/unit/common/test_settings_migration_mpc_combustion_load.py`

### Steps

- [ ] **Step 1: Start a normalized-combustion revision**

```bash
jj new -m "refactor(mpc): normalize coupled combustion load"
```

- [ ] **Step 2: Use LSP before exported-symbol changes**

Run LSP references for `allocate`, `Controller.set_output`, `build_do_mpc_model`, `NetPolicy.firing_rate`, `MODEL_SCHEMA`, and settings migration registry symbols. Use workspace symbols for overrides the server cannot connect. Record every caller before signatures change.

- [ ] **Step 3: Write failing allocator contracts**

Assert the typed allocator result obeys:

- `q=0`: auger duty `0`, fan `fan_min_pct` when MPC owns fan;
- `q=1`: auger `u_max`, fan `fan_max_pct`;
- midpoint: both interpolate on the same scalar axis;
- disabled fan authority returns `None` without changing auger;
- out-of-range/non-finite input is rejected or explicitly bounded per API contract;
- inverse auger mapping reconstructs normalized applied load, including zero;
- forward→inverse round trip across boundaries and representative interior points;
- no `u_min`, `Q_min`, or `Q_max` parameter remains.

- [ ] **Step 4: Write failing model/controller contracts**

Assert:

- do-mpc and KF input bounds are structural `[0,1]`;
- default initial/held input is bounded normalized load;
- model heat remains `K_Q * delayed_q`;
- `set_output` uses measured mean auger duty and allocator inverse;
- estimator/history receive applied normalized load;
- MPC never exposes a second independent fan decision variable.

Run focused tests and confirm RED.

- [ ] **Step 5: Implement normalized allocator/model flow**

Use frozen `CombustionCommand`/allocation diagnostics from the shared trace plan. Keep the linear fan envelope and one scalar load. Delete affine Q/`u_min` branches and old inverse logic. Rename model variables/symbols from ambiguous percent `Q` to normalized combustion-load names with LSP rename where safe; serialized trace field names follow the approved schema.

- [ ] **Step 6: Cut over settings and stored model semantics**

Settings schema version 5 deletes `Q_min` and `Q_max` from stored MPC config and manifest. It preserves all other config and is idempotent. Do not touch global PID/PID-SP cycle settings.

Advance MPC model snapshot/schema revision so old learned snapshots and policy artifacts are rejected with a precise model event/advisory. Do not convert old parameters silently.

- [ ] **Step 7: Update net calibration contracts**

Update `mpc_net.py`, sampler/export scripts, and artifact calibration keys for normalized input. Tests must fail on stale old-scale artifacts and accept regenerated normalized artifacts only. Artifact regeneration itself occurs in Task 7 after feed-forward semantics settle.

- [ ] **Step 8: Run focused tests and static checks**

```bash
uv run pytest tests/unit/mpc/test_mpc_model.py \
  tests/unit/mpc/test_mpc_allocator.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/common/test_settings_migration_mpc_combustion_load.py -v
uv run ruff format controller/mpc_model.py controller/mpc_allocator.py controller/mpc.py \
  controller/mpc_net.py tests/unit/mpc tests/unit/common/test_settings_migration_mpc_combustion_load.py
uv run ruff check controller/mpc_model.py controller/mpc_allocator.py controller/mpc.py \
  controller/mpc_net.py tests/unit/mpc tests/unit/common/test_settings_migration_mpc_combustion_load.py
```

Expected revision: `refactor(mpc): normalize coupled combustion load`.

---

## Task 2: Implement the pure 2-second/20-second framed-pulse scheduler

**Files:**

- Create: `controller/runtime/logic/pulse.py`
- Create: `tests/unit/runtime/test_pulse_scheduler.py`
- Create: `grillplat/actuator_capabilities.py`
- Modify: supported `grillplat/*` GrillPlatform implementations only as required by the shared capability seam.
- Modify: `tests/fakes/grill.py`
- Modify: platform tests discovered through LSP/workspace symbols.

### Steps

- [ ] **Step 1: Start a scheduler revision**

```bash
jj new -m "feat(mpc): schedule bounded auger pulses"
```

- [ ] **Step 2: Use LSP to enumerate hardware implementations**

Use workspace symbols for `GrillPlatform`, `auger_on`, `auger_off`, and `get_output_status`. Use LSP references where supported. Enumerate every real/fake implementation before adding the timing capability; no platform is silently omitted.

- [ ] **Step 3: Write timing capability tests**

Define frozen `AugerTiming(pulse_s=2, frame_s=20)` with validation that both are positive and frame is divisible by pulse. Every supported platform/fake returns the shared timing object. Timing is not read from user settings.

- [ ] **Step 4: Write scheduler behavior tests**

For injected timestamps and actual auger state, assert:

- zero duty never turns on; full allowed duty schedules the correct quanta;
- 10% mean produces 2 seconds ON / 18 seconds OFF;
- sub-frame duty carries credit and produces correct long-window average;
- representative/random duty sequences conserve scheduled credit within one pulse quantum;
- scheduled on-time is a multiple of 2 seconds and no frame exceeds 20 seconds;
- quanta are contiguous and avoid needless toggles;
- max steady transitions match the measured envelope;
- a mid-frame request change waits for the next frame;
- a skipped frame is marked/discarded, never replayed;
- `reset(reason)` clears credit and forces the next frame to start clean;
- delivered accounting follows actual state, not issued command;
- non-monotone/non-finite times or requests are rejected safely.

- [ ] **Step 5: Implement the pure scheduler**

No I/O, settings reads, threads, or controller imports. Inputs are latest bounded auger request, timestamp, and actual state; outputs are typed transition/frame/accounting decisions. Internal reasons are enums.

The scheduler carries fractional on-time seconds, quantizes down to whole 2-second units at each 20-second boundary, caps by frame/authority, and retains only the legitimate remainder.

- [ ] **Step 6: Verify against committed experiment invariants**

For the exact fixed-load cases in `_mpc_pulse_allocator.json`, assert long-window duty and transition counts match the pure scheduler. Do not assert temperature in this unit test.

- [ ] **Step 7: Run focused tests/static checks**

```bash
uv run pytest tests/unit/runtime/test_pulse_scheduler.py tests/unit/platform -v
uv run ruff format controller/runtime/logic/pulse.py grillplat/actuator_capabilities.py \
  tests/unit/runtime/test_pulse_scheduler.py
uv run ruff check controller/runtime/logic/pulse.py grillplat/actuator_capabilities.py \
  tests/unit/runtime/test_pulse_scheduler.py
```

Expected revision: `feat(mpc): schedule bounded auger pulses`.

---

## Task 3: Add typed actuation capability and slow/stale result reporting

**Files:**

- Modify: `controller/base.py`
- Modify: `controller/mpc.py`
- Modify: `controller/runtime/runner.py`
- Modify: `common/control_trace.py` only if the prerequisite schema needs the approved stale fields/enums completed.
- Modify: `tests/unit/controller/test_controller_capabilities.py`
- Modify: `tests/unit/runtime/test_sync_runner.py`
- Modify: `tests/unit/runtime/test_threaded_runner.py`
- Modify: `tests/unit/mpc/test_mpc_controller.py`

### Steps

- [ ] **Step 1: Start capability/deadline revision**

```bash
jj new -m "feat(mpc): report actuation mode and stale solves"
```

- [ ] **Step 2: Use LSP for runner/base interfaces**

Run references for `ControllerBase.commands_fan`, `ControllerBase.wants_async`, the revisioned result type, `ControllerRunner.latest`, and all proxies. Map every implementation before adding a method.

- [ ] **Step 3: Add enum-returning actuation capability**

`ControllerBase.actuation_mode()` returns `ActuationMode.FIXED_CYCLE`; MPC returns `FRAMED_PULSE`; runner proxies preserve the enum. Never compare controller-name strings in Hold.

- [ ] **Step 4: Write and implement deadline/staleness tests**

Using injected monotonic clocks:

- solve duration greater than `control_period` increments deadline misses;
- no new revision for two periods becomes stale;
- polling the same revision does not increment as a new result;
- a fresh revision clears consecutive stale state and records recovery;
- status/trace diagnostics expose duration, age, total/consecutive misses, and stale enum/state;
- warning callback fires once per stale transition and once on recovery, not each poll.

Do not sleep or block a real worker in unit tests; use barriers/fake clocks.

- [ ] **Step 5: Preserve nonblocking behavior**

Threaded worker still publishes atomically after a solve. Hold-facing `latest()` remains an immediate snapshot read. A stale/failing solver never runs actuator code.

- [ ] **Step 6: Verify**

```bash
uv run pytest tests/unit/controller/test_controller_capabilities.py \
  tests/unit/runtime/test_sync_runner.py tests/unit/runtime/test_threaded_runner.py \
  tests/unit/mpc/test_mpc_controller.py -v
```

Format/Ruff changed files. Expected revision: `feat(mpc): report actuation mode and stale solves`.

---

## Task 4: Integrate framed pulses into Hold without changing PID/PID-SP

**Files:**

- Modify: `controller/runtime/modes/hold.py`
- Modify: `controller/runtime/modes/base.py`
- Modify: `controller/runtime/state.py`
- Modify: `controller/runtime/runner.py` only for already-designed proxy/result fields.
- Modify: `tests/fakes/runner.py`
- Create: `tests/unit/runtime/test_hold_pulse_scheduler.py`
- Modify: Hold applied-output, trace, lid, manual, fan-authority, reconfigure, and teardown tests.

### Steps

- [ ] **Step 1: Start Hold integration revision**

```bash
jj new -m "feat(mpc): actualize framed pulses in hold"
```

- [ ] **Step 2: Use LSP to map Hold/base hooks**

Find references/workspace symbols for `HoldMode.setup`, `HoldMode.on_tick`, `_auger_cycle_tick`, `_on_auger_on`, `_on_manual_output`, `AppliedOutput`, `set_output`, and trace recorder methods before edits.

- [ ] **Step 3: Write failing strategy-selection tests**

Assert:

- PID/PID-SP still initialize and use existing fixed cycles byte-for-byte;
- MPC starts auger-off, creates framed scheduler, and never reads/clamps by `u_min`/`HoldCycleTime`;
- fallback PID selects fixed cycle from the actual runner capability;
- runtime branches on `ActuationMode`, never controller IDs.

- [ ] **Step 4: Write safety/lifecycle tests**

For MPC framed mode:

- new results are accepted only on revision advance and apply next frame;
- stale polls continue actualizing last bounded command;
- Stop/Error, universal guard, lid-open, manual takeover, reconfigure/fallback, and teardown force/reset exactly as designed;
- suppressed credit never reappears;
- missed frames do not catch up;
- fan command remains coupled to latest accepted combustion command;
- MPC fan-assist-below-`u_min` is unreachable/disabled; PID fan assist is unchanged.

- [ ] **Step 5: Apply transitions and measured accounting**

Call scheduler after safety/controller handling using the tick's already-read actual output state. Perform only requested auger transition. Track actual on-time across frame/feedback intervals and update existing auger metrics including teardown flush.

At controller feedback boundaries, send measured mean duty/inverse load through `AppliedOutput`, not requested duty.

- [ ] **Step 6: Emit shared trace/status**

Emit framed-pulse `ACTUATION_FRAME`, `APPLIED_OUTPUT`, reset/safety, and stale/recovery records joined to result revision. Call five-second trace flush after actuation.

Keep legacy `cycle_ratio` as requested mean duty; set fixed-cycle timing fields to zero for framed MPC; expose structured enum actuation status.

- [ ] **Step 7: Run focused runtime tests and smoke**

```bash
uv run pytest tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_fan_authority.py -v
```

Run a fake-clock Hold scenario with delayed MPC results. Observe pulse transitions, Stop preemption, and persisted trace before teardown.

Format/Ruff changed files. Expected revision: `feat(mpc): actualize framed pulses in hold`.

---

## Task 5: Make the matrix harness use shipped config, delivered pulses, and feasibility labels

**Files:**

- Modify: `docs/superpowers/experiments/controller_matrix.py`
- Create: `tests/unit/controller/test_matrix_harness_configuration.py`
- Modify: existing matrix harness sim-clock, auger-toggle, lid-sequence, capability, and output tests.
- Regenerate: `docs/superpowers/experiments/_matrix_baseline.json` only after tests pass.

### Steps

- [ ] **Step 1: Start harness revision**

```bash
jj new -m "fix(experiment): model shipped controller actuation"
```

- [ ] **Step 2: Write dynamic-default tests**

Monkeypatch shipped defaults/manifest after import and assert a run observes replacements. Every row/header records effective controller config, cycle config, actuation mode, pulse timing, plant, seed, and overrides.

No module-level copied config mapping may remain.

- [ ] **Step 3: Write framed-pulse harness contracts**

Assert MPC uses the production pure scheduler with actual delivered duty/load feedback; PID/PID-SP retain fixed cycles. Lid/manual pauses reset/discard credit. Use identical fake clocks to production timing.

- [ ] **Step 4: Add feasibility-aware scoring**

Rows include typed reachability state and binding max authority. Unreachable-high rows are labelled and excluded from winner/ranking comparisons that assume reachable setpoints. A metric trap test proves an infeasible row cannot win by a superficially small metric.

- [ ] **Step 5: Run harness tests and regenerate evidence**

```bash
uv run pytest tests/unit/controller/test_matrix_harness_configuration.py \
  tests/unit/controller/test_matrix_harness_sim_clock.py \
  tests/unit/controller/test_matrix_harness_auger_toggle.py \
  tests/unit/controller/test_matrix_harness_lid_sequence.py -v
uv run python docs/superpowers/experiments/controller_matrix.py --help
```

Run the explicit baseline regeneration command documented by the script for both plants/fixed seeds. Commit command, headers, rows, and summary together.

Expected revision: `fix(experiment): model shipped controller actuation`.

---

## Task 6: Add equilibrium feed-forward and upper-only reachability

**Files:**

- Modify: `controller/mpc_model.py`
- Modify: `controller/mpc.py`
- Modify: `controller/model_promotion.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: `tests/unit/mpc/test_mpc_model.py`
- Modify: `tests/unit/mpc/test_mpc_controller.py`
- Modify: `tests/unit/mpc/test_model_promotion.py`
- Create: `tests/unit/runtime/test_hold_controller_advisories.py`

### Steps

- [ ] **Step 1: Start equilibrium/feasibility revision**

```bash
jj new -m "feat(mpc): add equilibrium load and reachability"
```

- [ ] **Step 2: Use LSP for model/promotion interfaces**

Find references to equilibrium/braking helpers, `evaluate`, `Verdict`, MPC status/snapshot, and Hold warning surfaces before signature changes.

- [ ] **Step 3: Write closed-form equilibrium tests**

Test `steady_combustion_load(params, setpoint, disturbance=0)` and inverse `steady_temperature` across ambient, representative targets, radiation/no-radiation, fitted MAK params, and invalid/no-model inputs. Round-trip within explicit numeric tolerance.

- [ ] **Step 4: Implement analytic feed-forward baseline**

Compute model-derived `q_ss`; policy optimizes/returns residual `delta_q`; combined command clips once to `[0,1]`. Store/trace baseline, residual, raw total, and bounded total. Feed-forward can be disabled only by the experiment mutation seam, not a production user setting.

- [ ] **Step 5: Replace minimum-floor feasibility with upper-only report**

Delete low-floor calculation/advisory and settings-mutation recommendations. Implement frozen `FeasibilityReport` with `ReachabilityState`, target, required load, max authority/steady maximum, model revision/provenance, and binding reason.

Uncalibrated model returns `UNKNOWN_MODEL`; `q_ss>1` returns `UNREACHABLE_HIGH`; otherwise reachable.

- [ ] **Step 6: Surface one deduplicated Hold advisory**

On unreachable-high transition, warn once while heat continues at max safe authority. Repeated frames/model polls do not duplicate. Target/model/reachability changes clear/re-arm. Never mutate settings or block Hold.

- [ ] **Step 7: Remove obsolete low-floor tests/docs**

Delete tests/reason text tied to `u_min` reachability. Replace with upper, unknown, recovery, model-revision, and no-settings-mutation contracts.

- [ ] **Step 8: Verify**

```bash
uv run pytest tests/unit/mpc/test_mpc_model.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/mpc/test_model_promotion.py \
  tests/unit/runtime/test_hold_controller_advisories.py -v
```

Format/Ruff changed files. Expected revision: `feat(mpc): add equilibrium load and reachability`.

---

## Task 7: Measure feed-forward, run closed-loop scheduler gate, and regenerate policy artifacts

**Files:**

- Modify: `docs/superpowers/experiments/mpc_pulse_allocator.py`
- Regenerate: `docs/superpowers/experiments/_mpc_pulse_allocator.json`
- Create: `docs/superpowers/experiments/mpc_feed_forward.py`
- Create: `docs/superpowers/experiments/_mpc_feed_forward.json`
- Create: `tests/e2e/test_mpc_feed_forward.py`
- Create: `tests/e2e/test_mpc_pulse_scheduler.py`
- Modify: `tools/regenerate_mpc_net.py`
- Modify: net sampler/export experiment scripts required by its generated commands.
- Regenerate: `controller/mpc_policy_net.npz`
- Regenerate: `controller/mpc_policy_net_fan.npz`
- Modify: net-policy/regeneration tests.

### Steps

- [ ] **Step 1: Start the empirical-gate revision**

```bash
jj new -m "experiment(mpc): validate pulse scheduling and feed-forward"
```

- [ ] **Step 2: Lock experiment conditions in tests**

Tests assert both plants, fixed seeds/scenarios, three required arms, 2-second/20-second timing, unchanged plant calibration, and required metrics. The no-feed-forward arm uses an explicit injected experiment seam; no production config flag.

- [ ] **Step 3: Run the three-arm closed-loop matrix**

Arms:

1. legacy affine/fixed-25 MPC baseline;
2. normalized allocator + framed scheduler, no feed-forward;
3. same with feed-forward.

Run steady low/mid/high, step, lid, and unreachable-high scenarios across both plants/fixed seeds. Record trace session IDs and all design metrics: RMSE/IAE, overshoot/undershoot/settle/band, duty error, auger time, transitions, deadline/stale events, reachability.

- [ ] **Step 4: Run delayed-solver injection**

Inject one-, two-, and multi-period solve delays. Assert scheduler/safety cadence, revision handling, bounded commands, immediate Stop/lid/manual, and warning/recovery traces.

- [ ] **Step 5: Apply the decision rule honestly**

If the normalized scheduler fails safety/actuation fidelity, do not ship it; retain evidence and amend design. If feed-forward does not materially beat the same scheduler without it on ranked reachable scenarios, do not ship feed-forward. Never retune plants or omit losing rows.

- [ ] **Step 6: Regenerate normalized policy artifacts only after semantics settle**

Update sampler/export math and `tools/regenerate_mpc_net.py`. Run its tested command plan for fan-off and fan-on policies. Artifacts embed new model/allocator schema, normalized calibration, setpoint span, and reference pairs.

- [ ] **Step 7: Verify artifact fidelity and closed-loop acceptance**

```bash
uv run pytest tests/unit/mpc/test_regenerate_mpc_net.py \
  tests/unit/mpc/test_mpc_net.py tests/unit/mpc/test_mpc_net_loop.py -v
uv run pytest tests/e2e/test_mpc_feed_forward.py tests/e2e/test_mpc_pulse_scheduler.py -v
```

Commit scripts, raw rows, summaries, artifacts, and exact commands in one evidence revision.

Expected revision: `experiment(mpc): validate pulse scheduling and feed-forward`.

---

## Task 8: Measure coast and remove the unsupported horizon floor

**Files:**

- Create: `docs/superpowers/experiments/braking_horizon.py`
- Create: `docs/superpowers/experiments/_braking_horizon.json`
- Modify: `controller/model_promotion.py`
- Modify: `controller/mpc.py`
- Modify: `controller/update_mpc.py`
- Modify: `tests/unit/mpc/test_model_promotion.py`
- Modify: `tests/unit/mpc/test_mpc_controller.py`
- Modify: update/calibration tests.

### Steps

- [ ] **Step 1: Start measurement before deletion**

```bash
jj new -m "experiment(mpc): measure braking horizon"
```

- [ ] **Step 2: Write and run coast experiment**

On both plants/fixed seeds, preheat at max authority, cut combustion load to zero, and record cut temperature, peak, rise, seconds-to-peak, nominal model bound, and maximum measured rise. Use production plant definitions unchanged.

Commit script/output before deleting horizon code.

- [ ] **Step 3: Start ordered cutover revision**

```bash
jj new -m "refactor(mpc): remove derived horizon floor"
```

- [ ] **Step 4: Use LSP references before deleting helpers**

Trace `longest_braking_distance`, effective/derived horizon helpers, promotion verdict horizon fields, MPC build horizon, warnings, status, and CLI consumers. Update every caller; leave no alias.

- [ ] **Step 5: Remove runtime/promotion horizon derivation**

Build with configured `n_horizon` only. Delete auto-raise messaging and accepted-verdict promise. `refit_from_cook` evaluates fit quality without runtime horizon arguments/outputs. Keep `_built_n_horizon` compatibility only if public status requires it, always equal configured horizon; otherwise clean-cutover remove it with every caller.

- [ ] **Step 6: Keep coast safety factor invariant**

Existing product tests pin `COAST_BOUND=1.45` and coast functions independently; they remain unchanged. Remove only the use that turns the bound into a mandatory planning horizon.

- [ ] **Step 7: Verify**

```bash
uv run pytest tests/unit/mpc/test_model_promotion.py \
  tests/unit/mpc/test_mpc_controller.py tests/unit/mpc/test_mpc_calibration.py -v
```

Search live code for deleted horizon symbols/promises. Expected revisions remain ordered measurement then cutover.

---

## Task 9: Integrated verification and evidence audit

**Files:**

- Modify only for real failures/stale current documentation.

### Steps

- [x] **Step 1: Run focused retained-controller/runtime suites**

```bash
uv run pytest tests/unit/mpc tests/unit/runtime tests/unit/controller \
  tests/unit/common/test_settings_migration_mpc_combustion_load.py -v
```

- [x] **Step 2: Run slow/e2e experiments**

```bash
uv run pytest tests/e2e/test_mpc_feed_forward.py \
  tests/e2e/test_mpc_pulse_scheduler.py -v
```

Verify both plants, fixed seeds, delayed-solver injection, upper infeasibility, and trace replay.

- [x] **Step 3: Run full project verification**

Use context-mode for large outputs:

```bash
uv run pytest
cd web-react && bun run gen:types && bun run typecheck && bun run lint && bun run test && bun run build
```

Run Python formatting/static checks configured by the project. Record exact observed totals.

- [x] **Step 4: Smoke the complete path**

Run a fake/simulated Hold with MPC, observe asynchronous results, 2-second/20-second pulse transitions, coupled fan commands, applied-load feedback, upper advisory behavior, five-second SQLite trace rows before teardown, and immediate Stop.

Replay the session and run database-backed calibration extraction. This observed scenario—not a narrowed unit test—is final behavior proof.

- [x] **Step 5: Audit clean-cutover invariants**

Confirm:

- one normalized MPC input and no independent fan optimization;
- no MPC `Q_min`, `Q_max`, `u_min`, or `HoldCycleTime` authority;
- PID/PID-SP fixed-cycle behavior unchanged;
- no old-scale snapshot/net accepted;
- no low-firing advisory/settings mutation;
- no skipped-frame catch-up;
- all slow/stale transitions traced and deduplicated;
- no MPC CSV logger/reader;
- no derived horizon floor/auto-raise message;
- `COAST_BOUND` unchanged;
- experiments include both plants and recorded MAK calibration evidence.

- [x] **Step 6: Review Jujutsu revisions**

```bash
jj --no-pager log -r 'trunk()..@' --no-graph -T 'change_id.short() ++ " " ++ description.first_line() ++ "\n"'
jj --no-pager diff --git --from 'trunk()' --to '@'
```

One logical revision per task/evidence cut, no unrelated files, no empty descriptions.

---

## Requirement coverage

| Requirement | Tasks |
|---|---|
| R1 dynamic, feasibility-aware two-plant harness | 5, 7, 9 |
| R2 normalized scalar combustion and coupled allocator | 1, 7 |
| R3 measured 2-second/20-second framed scheduler | 2, 4, 7 |
| R4 safety/reset/no catch-up and PID isolation | 4, 9 |
| R5 revisioned nonblocking slow/stale behavior | 3, 4, 7 |
| R6 measured applied-load feedback | 1, 2, 4 |
| R7 equilibrium feed-forward measured gate | 6, 7 |
| R8 upper-only reachability/advisory | 5, 6, 7 |
| R9 clean model/net/settings/calibration cutover | 1, 7, 9 |
| R10 closed-loop both-plant evidence | 7, 9 |
| R11 coast-first horizon removal, unchanged factor | 8, 9 |
| R12 shared SQLite trace across pipeline | prerequisite trace plan, 4, 7, 9 |
