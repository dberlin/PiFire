# Framed-Only Hold Actuation — Implementation Plan

> **For the implementing engineer:** execute this plan with `skill://executing-plans` or `skill://subagent-driven-development`. Use `skill://test-driven-development` for each behavioral task and `skill://verification-before-completion` before publishing.

**Goal:** Remove fixed-cycle Hold actuation completely. PID, PID-SP, and MPC keep their controller math but all commands flow through the existing 2-second/20-second framed pulse scheduler. Retire the fixed-cycle-only fan-assist setting and invalidate old control-trace rows cleanly.

**Architecture:** `ControllerBase` owns one framed-pulse actuation contract. Hold owns one `PulseScheduler`, one result-adoption path, and one hardware-feedback path. Trace schema 3 contains only framed sessions/frames; SQL readers filter to schema 3 before decoding, while bounded recorder maintenance removes older schemas. Replay and MPC calibration consume only complete framed update/frame/applied-output relationships. Historical JSON evidence remains immutable; obsolete executable fixed-cycle reproducers are removed.

**Tech stack:** Python 3.11+, dataclasses, Pydantic v2, SQLite, pytest, Ruff; React/TypeScript, Bun, Rstest, generated JSON Schema/types.

## Invariants and scope

- Do not retune PID, PID-SP, MPC, allocator, pulse quantum, or frame duration.
- Preserve the current 2-second pulse quantum and 20-second frame.
- Preserve manual, lid-open, safety, and stale-result reset semantics: discarded frames never create catch-up credit.
- Preserve requested duty, realized duty, applied-output feedback, fan authority, and asynchronous MPC behavior.
- `HoldCycleTime`, `u_min`, and `u_max` remain settings because other modes and controller bounds still consume them; only Hold's fixed-cycle use disappears.
- Smoke Plus remains. The removed fan path is only `FanPidEnabled`/`FanState.assist` and its auger/fan cycling behavior.
- Do not rewrite committed experiment JSON. `mpc_pulse_allocator.py` and `control_rethink.py` may keep isolated mathematical fixed-cycle comparators; they must not construct the production Hold runtime or emit current trace rows.
- Every task below ends with a green focused suite and one cohesive Jujutsu revision. Do not publish until the final aggregate verification passes.

---

## Task 1: Make framed pulse the controller default

**Files:**

- Modify: `controller/base.py`
- Modify: `controller/pid_base.py`
- Modify: `controller/mpc.py`
- Modify: `tests/fakes/runner.py`
- Modify: `tests/unit/controller/test_controller_capabilities.py`
- Modify: `tests/unit/runtime/test_sync_runner.py`
- Modify: `tests/unit/runtime/test_threaded_runner.py`
- Modify only if expectations require it: `tests/unit/controller/test_controller_construct_smoke.py`
- Verify unchanged behavior: `tests/unit/mpc/test_mpc_controller.py`

### Steps

1. Change the capability tests first:
   - a bare `ControllerBase` reports `ActuationMode.FRAMED_PULSE`;
   - PID and PID-SP report framed pulse through inheritance;
   - MPC still reports framed pulse;
   - sync/threaded runner construction and reconfiguration preserve framed mode.
2. Run the focused tests and observe the bare-base expectation fail.
3. Change `ControllerBase.actuation_mode()` to return `FRAMED_PULSE`. Update `get_control_period()` documentation: `None` delegates to Hold's framed duration, not a legacy cycle.
4. Remove identical `actuation_mode()` overrides from `PIDControllerBase` and MPC. Keep the rationale in the base contract rather than maintaining two conventions.
5. Change `FakeControllerRunner`'s default mode to framed pulse. Tests that need an invalid value must pass it explicitly; no test helper should silently recreate the retired default.
6. Keep `ControllerRunner.actuation_mode()` typed and validated. Do not remove it: trace construction and reconfiguration still use the explicit contract.
7. Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/controller/test_controller_capabilities.py \
  tests/unit/controller/test_controller_construct_smoke.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/mpc/test_mpc_controller.py
```

8. Commit: `refactor(controller): default all controllers to framed pulse`

---

## Task 2: Retire fixed-cycle fan assist and its setting

**Files:**

- Modify: `controller/runtime/state.py`
- Modify: `controller/runtime/modes/base.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: `common/defaults.py`
- Modify: `common/settings_schema.py`
- Modify: `common/settings_migration.py`
- Modify: `tests/unit/runtime/test_work_cycle_state.py`
- Modify: `tests/unit/runtime/test_hold_applied_output.py`
- Modify: `tests/unit/runtime/test_hold_pulse_scheduler.py`
- Modify: `tests/unit/common/test_settings_schema.py`
- Modify: `tests/unit/common/test_settings_shape_digest.py`
- Create: `tests/unit/common/test_settings_migration_retired_fan_pid.py`
- Modify: `web-react/src/components/settings/tabs/WorkModeTab.tsx`
- Modify: `web-react/tests/unit/components/settings/tabs/WorkModeTab.test.tsx`
- Regenerate, do not hand-edit: `web-react/schema/settings.schema.json`
- Regenerate, do not hand-edit: `web-react/src/helpers/settings/settingsTypes.gen.ts`
- Regenerate, do not hand-edit: `web-react/src/helpers/settings/settingsDefaults.gen.ts`
- Modify if strict fixture parsing requires it: `web-react/tests/e2e/fixtures/settings.json`

### Steps

1. Add failing backend contracts:
   - `FanState` has no `assist` field;
   - current-schema settings omit `cycle_data.FanPidEnabled`;
   - upgrading a schema-5 tree removes `FanPidEnabled`, preserves adjacent cycle values, advances to schema 6, and is idempotent;
   - a future-schema tree remains untouched by the migration.
2. Add/update the Work Mode component test so the retired switch is absent while P-mode, lid detection, and lid timing still render and save normally. Run the focused Python/frontend tests and observe the expected failures.
3. Remove `FanState.assist`; update its docstring to describe Smoke Plus cycling/PWM state. Remove assist guards from `HoldModeBase` Smoke Plus logic. Smoke Plus behavior itself must not change.
4. Remove Hold's `FanPidEnabled` branches and the special fan/auger assist tick. Until Tasks 3 and 4 remove the remaining fixed scheduler and trace contracts, pass a literal false cause to the old applied-output API; no runtime path may produce fan-assist output.
5. Remove `FanPidEnabled` from `default_settings()` and `CycleData`. Bump `SETTINGS_SCHEMA_VERSION` from 5 to 6.
6. Add `_remove_retired_fan_pid(settings)` to the shape-migration sequence at version 6. It removes only `settings["cycle_data"]["FanPidEnabled"]`, tolerates missing/malformed optional containers consistently with existing migrations, and never rewrites a future-version tree.
7. Remove the Work Mode switch and its local draft binding. Regenerate contracts:

```bash
cd web-react
bun run gen:types
bun run gen:types:check
```

8. Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/runtime/test_work_cycle_state.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/common/test_settings_schema.py \
  tests/unit/common/test_settings_shape_digest.py \
  tests/unit/common/test_settings_migration.py \
  tests/unit/common/test_settings_migration_matrix.py \
  tests/unit/common/test_settings_migration_retired_fan_pid.py
cd web-react
bun run test -- tests/unit/components/settings/tabs/WorkModeTab.test.tsx
bun run gen:types:check
bun run typecheck
```

9. Start the web app with its normal development command, open Settings → Work Mode in a browser, and verify at desktop and narrow widths that the retired Fan PID control is absent and the neighboring lid/P-mode fields remain aligned and usable. Do not rewrite the immutable pre-migration fidelity baselines merely to silence a visual difference.
10. Commit: `refactor(settings): retire fixed-cycle fan assist`

---

## Task 3: Collapse Hold to one pulse scheduler path

**Files:**

- Modify: `controller/runtime/modes/hold.py`
- Modify: `controller/runtime/logic/cycle.py`
- Modify: `controller/runtime/runner.py`
- Modify: `controller/runtime/state.py`
- Modify: `tests/unit/runtime/test_hold_pulse_scheduler.py`
- Modify: `tests/unit/runtime/test_hold_applied_output.py`
- Modify: `tests/unit/runtime/test_hold_control_trace.py`
- Modify: `tests/unit/runtime/test_hold_fan_authority.py`
- Modify: `tests/unit/runtime/test_hold_model_persistence.py`
- Modify: `tests/unit/runtime/test_hold_refit_trigger.py`
- Modify: `tests/unit/runtime/test_hold_controller_advisories.py`
- Modify if framed timing changes its expected observations: `tests/characterization/test_modes_golden.py`
- Modify: `tests/unit/runtime/test_logic_cycle.py`

### Steps

1. Rewrite `test_hold_pulse_scheduler.py` around the one-path contract before implementation:
   - PID, PID-SP, and MPC setup all create `PulseScheduler` and start with the auger off;
   - a request below `u_min` accumulates across frames and eventually emits one quantum rather than being clamped up;
   - controller reconfiguration framed→framed resets the scheduler and discards prior credit;
   - missed frames are recorded as skipped and do not catch up;
   - safety, manual override, lid-open, stale command, mode change, and teardown reset/discard credit;
   - requested fan duty and auger duty are adopted together from one result revision.
2. Run the Hold-focused suite and observe failures against the conditional implementation.
3. In `HoldMode.setup()`, always build the existing production `PulseScheduler` with the configured 2-second/20-second timing. Validate the runner's typed actuation result, but do not branch on it.
4. Remove `_framed_pulse()` and every `if framed / else fixed` branch. Rename helpers only where the old name would lie about the one remaining behavior.
5. Resolve a `None` controller period to the pulse frame duration. Preserve explicit PID/MPC solve periods; scheduling and solving remain separate clocks.
6. Replace result adoption with one path: store raw requested duty, maximum realizable duty, fan duty, stale state, result revision, and combustion load, then ask the scheduler for frames.
7. Route auger callbacks, fan application, reset events, frame trace accounting, and applied-output feedback through the framed path unconditionally. Preserve the ordering: account observed output → record/reset frame → actuate safety/manual/lid transition.
8. Delete only controller-state fields proven by LSP references to serve the fixed-frame trace path (for example fixed raw/bounded/scheduled accumulators). Keep `ControllerState.cycle_start`: it governs controller solve cadence in the framed path. Keep `CycleState` fields used by Smoke, Startup, or public status.
9. Delete `hold_initial_cycle()` and `hold_update_cycle()` from `runtime/logic/cycle.py` and their tests. Keep `CycleTimes` and non-Hold cycle behavior.
10. Remove `FixedCycleFramePayload` imports/producers from Hold; Task 4 removes the schema type itself.
11. Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_fan_authority.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_hold_refit_trigger.py \
  tests/unit/runtime/test_hold_controller_advisories.py \
  tests/unit/runtime/test_logic_cycle.py \
  tests/characterization/test_modes_golden.py
```

12. Smoke-test the actual Hold mode with the fake platform for PID, PID-SP, and MPC. Observe at least two frames per controller and assert: one scheduler exists, low duty is not floored, hardware transitions match frame output, and realized duty is fed back to the runner.
13. Commit: `refactor(runtime): make Hold framed-pulse only`

---

## Task 4: Cut control traces to schema 3 and prune old rows

**Files:**

- Modify: `common/control_trace.py`
- Modify: `common/datastore_accessors.py`
- Modify: `controller/runtime/control_trace_recorder.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: `controller/applied_output.py`
- Modify: `controller/control_trace_replay.py`
- Modify: `tests/unit/common/test_control_trace_schema.py`
- Modify: `tests/unit/datastore/test_control_trace_store.py`
- Modify: `tests/unit/runtime/test_control_trace_recorder.py`
- Modify: `tests/unit/runtime/test_hold_control_trace.py`
- Modify: `tests/unit/controller/test_applied_output.py`
- Modify: `tests/unit/controller/test_control_trace_replay.py`

### Steps

1. Add failing schema tests:
   - `TRACE_SCHEMA_VERSION == 3`;
   - a framed PID/PID-SP/MPC session round-trips;
   - session payload requires pulse slot and frame timing and has no fixed timing fields;
   - update payloads accept only `FRAMED_PULSE`;
   - `FixedCycleFramePayload` is absent from the payload union;
   - the event/controller cross-validation still rejects wrong diagnostic, allocation, and frame ownership.
   - `OutputSource` has no `FAN_ASSIST` value and current records accept no such source;
2. Add failing datastore tests using raw SQL rows:
   - schema-2 rows are invisible to session, cook, and time-range readers;
   - range `limit` counts current rows, not filtered old rows;
   - bounded incompatible pruning deletes at most `limit` rows with `schema_version < 3` in id order;
   - repeated calls drain old rows;
   - schema-3 and future-schema rows are never deleted.
3. Add failing recorder tests:
   - incompatible pruning runs during existing maintenance without blocking record enqueue;
   - a full batch schedules another bounded pass rather than an unbounded loop;
   - prune failure warns once, retries, and emits the existing recovery warning when successful;
   - append/close semantics remain unchanged.
4. Advance `TRACE_SCHEMA_VERSION` to 3. Remove `SessionPayload.u_min`, `.u_max`, and `.hold_cycle_seconds`; framed timing is mandatory. Simplify all three update validators to require `ActuationMode.FRAMED_PULSE`.
5. Delete `FixedCycleFramePayload`, its validator, its discriminated-union member, event-kind mapping, and controller checks. Keep one `FramedPulseFramePayload` contract.
6. Remove `OutputSource.FAN_ASSIST`. Simplify `classify_output_source()` and `seed_output()` to accept only lid/manual causes, update every caller, and make replay treat only `CONTROLLER` as controller-measured output.
7. Update Hold's session builder to emit only pulse timing and its frame recorder to emit only framed payloads.
8. Filter SQL before Pydantic decoding. Add `schema_version = TRACE_SCHEMA_VERSION` to session/cook/range reads so old rows cannot poison an otherwise valid current session. Apply the filter before `ORDER BY ... LIMIT`.
9. Add `prune_incompatible_control_trace(before_schema_version, *, limit)` using `schema_version < ?`, never `!=`. Delete by bounded id-ordered subquery. A binary running schema 3 must be incapable of deleting future schema 4 data.
10. Inject that operation into `ControlTraceRecorder`. Perform one bounded incompatible batch per maintenance pass. If the batch is full, retain a backlog flag so a later `flush_due()` performs the next bounded pass; never loop through the whole table in one call. Reuse retention degraded/recovery warnings and keep failures non-fatal.
11. Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/controller/test_applied_output.py \
  tests/unit/controller/test_control_trace_replay.py \
  tests/unit/datastore/test_control_trace_store.py \
  tests/unit/runtime/test_control_trace_recorder.py \
  tests/unit/runtime/test_hold_control_trace.py
```

12. Run one SQLite smoke scenario: insert schema-2, schema-3, and schema-4 rows directly; verify normal reads expose only schema 3; invoke incompatible pruning repeatedly; verify only schema 2 disappears.
13. Commit: `feat(trace): cut over to framed-only schema 3`

---

## Task 5: Remove fixed replay and calibration reconstruction

**Files:**

- Modify: `controller/control_trace_replay.py`
- Modify: `controller/update_mpc.py`
- Modify: `tests/unit/controller/test_control_trace_replay.py`
- Modify: `tests/unit/mpc/test_update_mpc.py`
- Modify: `tests/unit/mpc/test_mpc_calibration.py`

### Steps

1. Convert fixture builders to schema-3 framed sessions. Each accepted MPC sample must have a coherent controller update, allocation, framed pulse frame, and applied-output interval for the same result revision.
2. Add/retain failing behavior contracts for missing frame, partial applied interval, revision mismatch, inhibit, stale update, recorder gap, and non-controller output source. These are rejected or excluded according to the current safety contract; do not weaken them to make the new fixtures pass.
3. Remove fixed-cycle frame collection, validation, reconciliation, and warm-up branches from replay. `OutputSource.CONTROLLER` is the only controller-measured source; lid/manual/seed remain non-controller causes.
4. Remove `FixedCycleFramePayload` and fixed-mode branches from `update_mpc._records_to_arrays()`. Calibration derives delivered combustion load only from complete framed intervals and still rejects mixed provenance, missing start/end, gaps, unsafe temperatures, and insufficient samples.
5. Remove now-dead imports/helpers and update error text to describe the framed contract precisely.
6. Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/controller/test_control_trace_replay.py \
  tests/unit/mpc/test_update_mpc.py \
  tests/unit/mpc/test_mpc_calibration.py
```

7. Smoke-test both CLIs against a temporary schema-3 trace database: replay reports valid relationships; calibration loads the same delivered series. Repeat with a schema-2-only database and verify it is reported as no selectable/current trace, not as a decode crash.
8. Commit: `refactor(trace): remove fixed replay and calibration`

---

## Task 6: Make executable experiments framed-only

**Files:**

- Modify: `docs/superpowers/experiments/controller_matrix.py`
- Modify: `tests/unit/controller/test_matrix_harness_configuration.py`
- Modify as required by shared harness behavior: `tests/unit/controller/test_matrix_harness_sim_clock.py`
- Modify as required by shared harness behavior: `tests/unit/controller/test_matrix_harness_auger_toggle.py`
- Modify as required by shared harness behavior: `tests/unit/controller/test_matrix_harness_lid_sequence.py`
- Modify as required by shared harness behavior: `tests/unit/controller/test_matrix_harness_lid_excursion.py`
- Delete: `docs/superpowers/experiments/mpc_feed_forward.py`
- Delete: `tests/e2e/test_mpc_feed_forward.py`
- Keep unchanged: `docs/superpowers/experiments/_mpc_feed_forward.json`
- Keep as isolated mathematical fixtures: `docs/superpowers/experiments/mpc_pulse_allocator.py`, `docs/superpowers/experiments/control_rethink.py`

### Steps

1. Update harness tests first so a controller matrix run always reports `actuation_mode: framed_pulse`, always includes pulse timing, and delivers low duty through accumulated pulse credit. Remove fake fixed-core cases.
2. Make `controller_matrix.run_scenario()` instantiate one `PulseScheduler` unconditionally. Delete `CycleTimes`, fixed-cycle hardware toggling, fixed trace accounting, and mode branching. Preserve solve timing, plant stepping, lid events, requested/realized metrics, and refit behavior.
3. Ensure `effective_run.scheduler` always records the actual 2-second/20-second framed configuration. A `cycle_config` override may still change independent controller bounds, but cannot select another scheduler.
4. Delete the obsolete feed-forward reproducer and its executable e2e test. Its purpose was to compare the now-retired `legacy_affine_fixed_25s` arm with the framed implementation; the committed JSON remains immutable historical evidence and must not be regenerated.
5. Do not delete the pulse allocator's isolated `_FixedCycle` math comparator. It does not invoke Hold, hardware callbacks, current controllers, or current trace schema, and remains the documented historical comparison exception.
6. Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/controller/test_matrix_harness_configuration.py \
  tests/unit/controller/test_matrix_harness_sim_clock.py \
  tests/unit/controller/test_matrix_harness_auger_toggle.py \
  tests/unit/controller/test_matrix_harness_lid_sequence.py \
  tests/unit/controller/test_matrix_harness_lid_excursion.py \
  tests/e2e/test_mpc_pulse_scheduler.py
```

7. Execute one short `controller_matrix.py` PID scenario and one MPC scenario. Inspect their emitted effective configuration and trace summaries; both must be framed and contain requested-versus-realized measurements.
8. Commit: `refactor(experiments): remove executable fixed Hold paths`

---

## Task 7: Remove the retired enum member and all dead compatibility code

**Files:**

- Modify: `common/control_trace.py`
- Modify affected imports/comments in: `controller/`, `common/`, `tests/`
- Modify current generated docs/contracts only if referenced by current code; do not rewrite archived plans, specs, audits, or JSON evidence.

### Steps

1. Change the enum contract test to require exactly `{ActuationMode.FRAMED_PULSE}` and run it red while `FIXED_CYCLE` still exists.
2. Use LSP references on `ActuationMode.FIXED_CYCLE`, then remove the enum member and every executable caller/test branch. Remove obsolete aliases, fallback text, and compatibility comments rather than replacing them with another shim.
3. Search plain serialized strings separately. Classify every remaining `fixed_cycle` occurrence:
   - allowed: committed JSON evidence, archived plans/specs/audits, and the two named mathematical comparison fixtures;
   - forbidden: `common/`, `controller/`, active runtime tests, generated settings contracts, `controller_matrix.py`, or any executable trace/replay/calibration producer.
4. Run Ruff on changed Python files, then the aggregate focused suite:

```bash
.venv/bin/ruff format common controller tests docs/superpowers/experiments/controller_matrix.py
.venv/bin/ruff check common controller tests docs/superpowers/experiments/controller_matrix.py
.venv/bin/pytest -q --tb=short \
  tests/unit/controller/test_controller_capabilities.py \
  tests/unit/controller/test_applied_output.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/datastore/test_control_trace_store.py \
  tests/unit/runtime/test_control_trace_recorder.py \
  tests/unit/controller/test_control_trace_replay.py \
  tests/unit/mpc/test_update_mpc.py \
  tests/unit/controller/test_matrix_harness_configuration.py
```

5. Commit: `refactor(controller): delete fixed-cycle actuation contract`

---

## Task 8: End-to-end verification and publication gate

**Files:**

- Modify only for real regressions exposed by verification; do not suppress environment failures or weaken contracts.

### Steps

1. Run the controller/runtime/common/settings/calibration aggregate, including all Hold tests and matrix harness tests:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/controller \
  tests/unit/runtime \
  tests/unit/common \
  tests/unit/datastore/test_control_trace_store.py \
  tests/unit/mpc/test_update_mpc.py \
  tests/unit/mpc/test_mpc_calibration.py \
  tests/characterization/test_modes_golden.py \
  tests/e2e/test_mpc_pulse_scheduler.py
```

2. Run the full Python suite in CI/Linux:

```bash
.venv/bin/pytest -q --tb=short
```

On this Darwin workstation, use the established platform-compatible invocation: ignore `tests/ui` and `tests/unit/probes/test_bt_probe_close.py`, and deselect only the already-known hardware/OS/MQTT cases that require Linux sensors, `/proc`, Bluepy, system fonts, or live MQTT. Record every deselection and confirm no changed control/settings/trace test is excluded.
3. Run all frontend gates:

```bash
cd web-react
bun run gen:types:check
bun run typecheck
bun run lint
bun run test
bun run build
```

4. Repeat the browser check at desktop and narrow widths for Settings → Work Mode. Exercise a save of the remaining fields and confirm the request succeeds without `FanPidEnabled`.
5. Run one end-to-end fake Hold session per controller (PID, PID-SP, MPC) long enough to observe multiple frames, a low-duty accumulated pulse, a lid reset, and teardown. Persist and reread the trace, replay it, and run MPC calibration on the MPC session. Verify:
   - one framed scheduler path;
   - no `u_min` floor for low requested duty;
   - no catch-up after inhibit;
   - coupled fan/auger result revision;
   - requested/realized duty and applied-output feedback agree;
   - schema-3 persistence/replay/calibration succeed.
6. Inspect the final Jujutsu diff against the integration parent. Confirm no unrelated PID coefficients, MPC policy code, pulse timing, immutable evidence JSON, or historical documents changed.
7. Use `skill://requesting-code-review`. Resolve every Critical/Important finding and rerun the affected checks.
8. Use `skill://finishing-a-development-branch`, advance the intended bookmark only after all gates pass, fetch immediately before push, and push only on explicit user request.
