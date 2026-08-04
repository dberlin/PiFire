# Shared Controller Control-Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans task-by-task. Use TDD and verification-before-completion. Use LSP for every symbol definition/reference/implementation lookup and every cross-file rename.

**Goal:** Replace MPC's CSV logger with a typed, 30-day SQLite control trace spanning PID, PID-SP, MPC, combustion allocation, scheduling, safety inhibition, physical application, and applied-output feedback.

**Architecture:** Immutable runtime dataclasses cross a Pydantic validation boundary into a shared SQLite `control_trace` table. Hold buffers a small batch and writes it in one transaction every five seconds; teardown only drains the final partial batch. Indexed envelope columns support retention and cook/session reads, while event-specific payloads remain validated JSON. Calibration and replay use typed datastore accessors.

**Tech Stack:** Python 3.14, Pydantic 2, SQLite, pytest, React/TypeScript generation, Jujutsu.

**Approved design:** `docs/superpowers/specs/2026-08-04-controller-control-trace-design.md`

**Prerequisite:** Execute `docs/superpowers/plans/2026-08-04-controller-catalog-cleanup.md` first. This plan assumes only `pid`, `pid_sp`, and `mpc` remain and settings schema version 3 is current.

---

## Task 1: Define the typed trace schema

**Files:**

- Create: `common/control_trace.py`
- Create: `tests/unit/common/test_control_trace_schema.py`

### Steps

- [ ] **Step 1: Start a schema revision**

```bash
jj new -m "feat(trace): define typed controller records"
```

- [ ] **Step 2: Write failing enum and round-trip tests**

Test exact enum members:

- `ControllerType`: `PID`, `PID_SP`, `MPC`;
- `TraceEventKind`: `SESSION`, `CONTROL_UPDATE`, `ALLOCATION`, `ACTUATION_FRAME`, `APPLIED_OUTPUT`, `SAFETY_EVENT`, `MODEL_EVENT`, `RECORDER_GAP`;
- `ActuationMode`: `FIXED_CYCLE`, `FRAMED_PULSE`;
- typed inhibit/model/controller-branch enums required by payloads.

For every payload, build a representative immutable dataclass, wrap it in `ControlTraceRecord`, serialize with Pydantic JSON, deserialize, and assert equality and enum restoration.

Add rejection cases for NaN/infinity, mismatched event/payload types, empty session IDs, negative durations/counts, unsupported schema versions, and unknown enum values.

```bash
uv run pytest tests/unit/common/test_control_trace_schema.py -v
```

Expected RED because the schema does not exist.

- [ ] **Step 3: Implement enums and immutable Pydantic dataclasses**

In `common/control_trace.py`:

- use `StrEnum` internally and serialize only at the Pydantic/JSON boundary;
- define frozen, slotted Pydantic dataclasses for session, PID, PID-SP, MPC, allocation, fixed-cycle frame, framed-pulse frame, applied output, safety/model events, and recorder gaps;
- define a discriminated payload union;
- define a versioned `ControlTraceRecord(BaseModel)` envelope with indexed fields and payload;
- reject non-finite numbers;
- provide explicit `to_db_row()`/`from_db_row()` helpers.

No arbitrary `dict[str, Any]` payload escape hatch. Variable MPC delay state uses a typed value list plus matching stable field names.

- [ ] **Step 4: Add row-level invariants**

Require non-negative revisions, ordered intervals, delivered on-time no greater than duration, positive pulse timing with frame divisible by slot, controller-matched diagnostics, and MPC-only allocation. Cross-row ordering remains replay's job.

- [ ] **Step 5: Verify and close the revision**

```bash
uv run pytest tests/unit/common/test_control_trace_schema.py -v
uv run ruff format common/control_trace.py tests/unit/common/test_control_trace_schema.py
uv run ruff check common/control_trace.py tests/unit/common/test_control_trace_schema.py
```

Expected revision: `feat(trace): define typed controller records`.

---

## Task 2: Add the SQLite table, typed accessors, and retention

**Files:**

- Modify: `common/datastore.py`
- Modify: `common/datastore_accessors.py`
- Create: `tests/unit/common/test_control_trace_store.py`

### Steps

- [ ] **Step 1: Start a datastore revision**

```bash
jj new -m "feat(trace): persist controller records in sqlite"
```

- [ ] **Step 2: Use LSP before changing symbols**

Run LSP references for `common.datastore._ensure_schema`, `common.datastore.connection`, `append_metric`, and `read_history`. Record transaction and return-type conventions before editing; do not replace symbol lookup with text search.

- [ ] **Step 3: Write failing schema/accessor tests**

Using the isolated datastore fixture, assert:

- approved columns and `json_valid(payload)`;
- `(session_id,id)`, `(cook_id,id)`, and timestamp indexes;
- database `user_version` advances from 4 to 5 without altering existing rows;
- batch append preserves insertion order;
- session/cook/time-range reads return typed records;
- invalid records cannot enter through accessors;
- bounded pruning returns its deletion count.

- [ ] **Step 4: Implement DDL and database version 5**

Add `_CONTROL_TRACE_DDL`, append it to `SCHEMA`, and advance `PRAGMA user_version` to 5 after existing migrations. Existing databases only create an empty table; no data migration.

- [ ] **Step 5: Implement typed accessors**

```python
append_control_trace(records: Sequence[ControlTraceRecord]) -> None
read_control_trace_session(session_id: str) -> list[ControlTraceRecord]
read_control_trace_cook(cook_id: str) -> list[ControlTraceRecord]
read_control_trace_range(start_ms: int, end_ms: int, *, limit: int) -> list[ControlTraceRecord]
prune_control_trace(before_ms: int, *, limit: int) -> int
delete_control_trace_session(session_id: str) -> int
```

Validate before opening a transaction; use `executemany`; list columns; order by `id`; expose no raw rows/dicts; bound query limits.

- [ ] **Step 6: Prove the 30-day boundary**

Seed rows immediately before, at, and after `now - 30 days`. Repeated bounded prune calls remove only strictly older rows.

- [ ] **Step 7: Verify and close the revision**

```bash
uv run pytest tests/unit/common/test_control_trace_store.py \
  tests/unit/common/test_datastore.py tests/unit/common/test_datastore_accessors.py -v
uv run ruff format common/datastore.py common/datastore_accessors.py \
  tests/unit/common/test_control_trace_store.py
uv run ruff check common/datastore.py common/datastore_accessors.py \
  tests/unit/common/test_control_trace_store.py
```

Expected revision: `feat(trace): persist controller records in sqlite`.

---

## Task 3: Build the five-second batch recorder

**Files:**

- Create: `controller/runtime/control_trace_recorder.py`
- Create: `tests/unit/runtime/test_control_trace_recorder.py`

### Steps

- [ ] **Step 1: Start a recorder revision**

```bash
jj new -m "feat(trace): batch controller records during hold"
```

- [ ] **Step 2: Write recorder tests first**

With injected append/prune functions and clock, prove:

1. `record()` validates/appends without touching SQLite.
2. `flush_due()` does nothing before five seconds.
3. At five seconds it writes the current batch in one ordered transaction and clears it.
4. Normal persistence occurs during the cook without `close()`.
5. A failed flush keeps the batch and emits one warning; the next success writes it and clears the warning.
6. The emergency cap drops deterministically without unbounded growth and emits one typed `RECORDER_GAP` after recovery.
7. Startup/daily retention uses the 30-day cutoff and bounded deletes.
8. `close()` attempts one final flush and never loops indefinitely.

No wall-clock sleeps; use an injected clock.

- [ ] **Step 3: Implement `ControlTraceRecorder`**

Requirements:

- one in-memory list/deque, no writer thread or queue;
- five-second production flush interval in one constant;
- `record(ControlTraceRecord)` validates then appends;
- `flush_due(now)` writes via `append_control_trace` when due;
- transaction failure retains the whole batch for retry;
- injected warning callback deduplicates failure/recovery;
- bounded emergency capacity sized well above normal five-second volume;
- teardown `close()` performs one best-effort flush only.

- [ ] **Step 4: Implement retention cadence**

Prune on recorder start and at most daily afterward. Repeat bounded deletes until caught up, but do not defer a normal record flush behind pruning.

- [ ] **Step 5: Verify and close the revision**

```bash
uv run pytest tests/unit/runtime/test_control_trace_recorder.py -v
uv run ruff format controller/runtime/control_trace_recorder.py \
  tests/unit/runtime/test_control_trace_recorder.py
uv run ruff check controller/runtime/control_trace_recorder.py \
  tests/unit/runtime/test_control_trace_recorder.py
```

Expected revision: `feat(trace): batch controller records during hold`.

---

## Task 4: Expose retained-controller diagnostics and revisioned results

**Files:**

- Modify: `controller/base.py`
- Modify: `controller/pid.py`
- Modify: `controller/pid_sp.py`
- Modify: `controller/mpc.py`
- Modify: `controller/runtime/runner.py`
- Create: `tests/unit/controller/test_controller_trace_diagnostics.py`
- Modify: `tests/unit/controller/test_controller_capabilities.py`
- Modify: `tests/unit/runtime/test_sync_runner.py`
- Modify: `tests/unit/runtime/test_threaded_runner.py`

### Steps

- [ ] **Step 1: Start the controller/runner revision**

```bash
jj new -m "feat(trace): expose controller update diagnostics"
```

- [ ] **Step 2: Use LSP before editing exported surfaces**

Run LSP references for `ControllerBase.get_status`, `ControllerBase.update`, `ControllerRunner.latest`, `ControllerRunner.controller_state`, and `NormalizedOutput`. Use LSP workspace symbols to enumerate dynamic overrides the Python server cannot connect. Only then may text search locate unresolved dynamic calls.

- [ ] **Step 3: Write failing diagnostic tests**

With fixed clocks/configs, assert PID/PID-SP diagnostics numerically match update arithmetic. For MPC, stub policy/estimator and assert state vector/names, disturbance, raw/bounded firing load, equilibrium/residual components, model revision, failure state, and solve timing.

- [ ] **Step 4: Add explicit `trace_diagnostics()`**

Add a typed base capability and overrides for all retained controllers. Never scrape `__dict__`. Keep public `get_status()` separate.

- [ ] **Step 5: Replace `NormalizedOutput` with a revisioned result dataclass**

Carry normalized output, atomically matching diagnostics/status, monotone revision, solve monotonic start/end/duration, and completion wall timestamp. Sync/threaded runners return the same type. Re-polling a threaded result preserves its revision.

- [ ] **Step 6: Update every LSP-discovered caller**

Migrate Hold/tests; remove the namedtuple and compatibility aliases after all callers move.

- [ ] **Step 7: Verify and close the revision**

```bash
uv run pytest tests/unit/controller/test_controller_trace_diagnostics.py \
  tests/unit/controller/test_controller_capabilities.py \
  tests/unit/runtime/test_sync_runner.py tests/unit/runtime/test_threaded_runner.py \
  tests/unit/mpc/test_mpc_controller.py -v
```

Format/Ruff changed files. Expected revision: `feat(trace): expose controller update diagnostics`.

---

## Task 5: Trace Hold, allocation, scheduling, safety, and feedback

**Files:**

- Modify: `controller/runtime/modes/hold.py`
- Modify: `controller/runtime/modes/base.py`
- Modify: `controller/runtime/state.py`
- Modify: `controller/mpc_allocator.py`
- Create: `tests/unit/runtime/test_hold_control_trace.py`
- Modify: existing Hold applied-output, fan-authority, lid/manual, reconfigure, and Stop/Error tests.

### Steps

- [ ] **Step 1: Start runtime integration**

```bash
jj new -m "feat(trace): connect hold control pipeline"
```

- [ ] **Step 2: Use LSP to map runtime hooks**

Run LSP references/workspace symbols for `ControlMode.run`, `HoldMode.setup`, `HoldMode.on_tick`, `_auger_cycle_tick`, `_on_auger_on`, `AppliedOutput`, `allocate`, and `set_output` before signatures change.

- [ ] **Step 3: Write end-to-end trace tests**

Drive Hold with fake clock/devices/runner/recorder. Assert ordered records for:

- session → PID/PID-SP update → fixed-cycle frame → applied output;
- MPC update → allocation → current fixed-cycle frame;
- lid detect/clear, manual takeover/release, fallback/reconfigure, Stop/Error, and fan authority.

Join by session/result revision and verify requested versus actual values.

- [ ] **Step 4: Own recorder lifecycle and continuous flushing in Hold**

Create one recorder per Hold session, emit `SESSION` once the cook ID exists, append events immediately, and call `flush_due(now)` after actuation every loop. Teardown calls one final `close()` flush. Trace failures warn but do not change control.

- [ ] **Step 5: Emit controller and allocation records**

Emit `CONTROL_UPDATE` once per new result revision. Refactor `mpc_allocator.allocate` to return a frozen typed allocation result and emit `ALLOCATION` for MPC. Do not duplicate records when polling the same threaded revision.

- [ ] **Step 6: Emit fixed-cycle and applied-output records**

Track actual auger-on time/transitions per PID/PID-SP cycle; emit the frame and the exact applied output fed back. Record current pre-scheduler MPC similarly; the MPC plan later replaces only its actuation producer.

- [ ] **Step 7: Emit safety/model events**

Record resets, inhibits, fallback/reconfigure, model restore/adopt/refit at the branch performing them, not inferred from later status.

- [ ] **Step 8: Verify runtime and smoke Hold**

```bash
uv run pytest tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_applied_output.py \
  tests/unit/runtime/test_hold_fan_authority.py \
  tests/unit/runtime/test_hold_refit_trigger.py -v
```

Run the fake-device Hold scenario and read persisted typed rows before teardown to prove five-second in-cook flushing. Format/Ruff changed files.

Expected revision: `feat(trace): connect hold control pipeline`.

---

## Task 6: Delete CSV logging and calibrate from database traces

**Files:**

- Modify: `controller/mpc.py`
- Modify: `controller/update_mpc.py`
- Modify: `controller/controllers.json`
- Modify: `common/settings_migration.py`
- Modify: `common/settings_schema.py`
- Create: `tests/unit/common/test_settings_migration_controller_trace.py`
- Modify: `tests/unit/mpc/test_mpc_calibration.py`
- Modify: `tests/unit/mpc/test_mpc_logging.py`
- Rename/modify: MPC logging tests using LSP file rename.
- Modify: generated React types/fixtures/tests.

### Steps

- [ ] **Step 1: Start the clean cutover**

```bash
jj new -m "refactor(mpc): calibrate from sqlite control traces"
```

- [ ] **Step 2: Write settings migration tests**

Settings schema version 4 deletes `log_data` and `log_path` from `controller.config.mpc`, preserves all other settings, is idempotent, and leaves malformed trees to normal repair. No CSV migration/import/delete.

- [ ] **Step 3: Remove CSV settings and runtime code**

Delete both manifest options, regenerate frontend types, and remove `_log_path`, `_log_row`, file writes, and CSV status. Use LSP references before deleting Python symbols.

- [ ] **Step 4: Load calibration samples through typed accessors**

`controller.update_mpc` accepts cook/session selection and optional database path, validates MPC trace rows, rejects gaps/mixed schemas by default, and extracts ordered `(time, temperature, applied combustion load)` arrays. Keep fit math unchanged and separate `load_trace_samples(...)` from CLI parsing.

- [ ] **Step 5: Seed the recorded MAK cook into a test database**

Construct typed trace rows from committed MAK evidence in a test helper. Assert database-backed calibration reproduces the established fit (about 2.3358 °C RMSE within existing tolerances).

The historical CSV may remain immutable test/experiment evidence; production code never reads it.

- [ ] **Step 6: Verify migration, generation, and calibration**

```bash
uv run pytest tests/unit/common/test_settings_migration_controller_trace.py \
  tests/unit/mpc/test_update_mpc.py tests/unit/mpc -v
cd web-react
bun run gen:types
bun run typecheck
bun run test -- src/unit/scripts/emitControllerTypes.test.ts
```

- [ ] **Step 7: Audit the cutover**

Search live runtime/config/current docs for `log_data`, `log_path`, `mpc_calibration_log.csv`, and CSV reader/writer symbols. Expected: no production references. Format/static-check changes.

Expected revision: `refactor(mpc): calibrate from sqlite control traces`.

---

## Task 7: Add typed replay and integrated verification

**Files:**

- Create: `controller/control_trace_replay.py`
- Create: `tests/unit/controller/test_control_trace_replay.py`
- Modify: current controller/runtime documentation and release notes.

### Steps

- [ ] **Step 1: Start replay work**

```bash
jj new -m "feat(trace): replay and validate control sessions"
```

- [ ] **Step 2: Write replay contracts**

Verify monotone revisions, exact allocation, fixed-cycle scheduled/actual accounting, applied-output reconciliation, and safety-reset explanation. Corrupt each separately and require a precise typed result. Include framed-pulse payload/accounting validation; the MPC plan adds production framed-pulse traces.

- [ ] **Step 3: Implement a pure typed consumer**

Read typed accessors, group one session by insertion order, and return a structured report. Never mutate state or import current controller behavior to guess history; recorded schema/allocator revisions select pure rules.

- [ ] **Step 4: Document use**

Document 30-day retention, session/cook selection, trace gaps, database calibration, and CSV removal. Add no parallel export/storage format.

- [ ] **Step 5: Run focused and full verification**

Use context-mode for large output:

```bash
uv run pytest tests/unit/common/test_control_trace_schema.py \
  tests/unit/common/test_control_trace_store.py \
  tests/unit/runtime/test_control_trace_recorder.py \
  tests/unit/controller/test_controller_trace_diagnostics.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/controller/test_control_trace_replay.py \
  tests/unit/mpc/test_update_mpc.py -v
uv run pytest
cd web-react && bun run test
```

Run Python static checks and frontend typecheck/lint/build; record exact totals.

- [ ] **Step 6: Review the Jujutsu series**

```bash
jj --no-pager log -r 'trunk()..@' --no-graph -T 'change_id.short() ++ " " ++ description.first_line() ++ "\n"'
jj --no-pager diff --git --from 'trunk()' --to '@'
```

Expected: one logical revision per task, no unrelated files, no compatibility CSV path.

---

## Requirement coverage

| Requirement | Tasks |
|---|---|
| Pydantic schema, dataclasses, and enums | 1 |
| SQLite typed storage and 30-day retention | 2 |
| Continuous five-second batching and explicit gaps | 3, 5 |
| PID/PID-SP/MPC computation diagnostics | 4, 5 |
| Allocator/scheduler/safety/applied-output chain | 5 |
| CSV removal and database-backed calibration | 6 |
| Typed replay and corruption detection | 7 |
| Framed-pulse production records | MPC control-quality plan scheduler integration |
