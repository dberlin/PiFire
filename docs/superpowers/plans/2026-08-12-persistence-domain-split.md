# Persistence Domain Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 2,337-line accessor monolith and duplicated runtime store semantics with typed durable-domain modules sharing pure transformations, then remove `common/datastore_accessors.py` with every final module above 90% branch coverage.

**Architecture:** `common/persistence/` separates control, runtime blobs, history, traces, model evidence, and install state. SQL modules own storage/transactions; controller model migration policy moves upward to `controller/model_learning/migration.py`. Runtime SQLite/in-memory adapters implement narrow protocols and share pure transformations from `transforms.py`.

**Tech Stack:** Python 3.14, SQLite, Pydantic, dataclasses/protocols, pytest/pytest-cov, Ruff, Pyright/LSP, Jujutsu.

## Global Constraints

- Depends on Slice A's `write_control_snapshot`, `enqueue_control_delta`, and delta-only FIFO.
- Preserve SQLite keys/table schemas, JSON shapes, transaction boundaries, FIFO order, high-water behavior, retention limits, activation CAS semantics, migration inputs, and public errors.
- `common.persistence` must not import `controller.mpc` or concrete controller runtime classes. Controller migration/policy may consume persistence records, never the reverse.
- No final re-export facade at `common/datastore_accessors.py` and no 34-method untyped `Store` ABC.
- Every final `common/persistence/*.py` module and `controller/model_learning/migration.py` must have strictly greater than 90% branch coverage.
- During parallel Tasks 3–7, use LSP references to inventory callers but create/test only the destination module; Task 10 performs the single sequential caller migration and deletes the old implementation.

## Final File Map

- `common/persistence/__init__.py`: package marker only; no re-exports.
- `common/persistence/protocols.py`: narrow `Protocol` definitions and type aliases.
- `common/persistence/transforms.py`: pure status/current/metric/control transformations shared by adapters.
- `common/persistence/control.py`: control snapshot/delta FIFO, calibration command queue/revision.
- `common/persistence/runtime.py`: settings, pellets, current, status, errors/warnings, users, generic keys.
- `common/persistence/history.py`: metrics, history, autotune.
- `common/persistence/control_trace.py`: append/read/prune/delete trace rows.
- `common/persistence/model_evidence.py`: evidence rows and atomic activation phase/rollback state.
- `common/persistence/install_state.py`: OS, wizard, updater status/blob helpers.
- `controller/model_learning/migration.py`: MPC learning authority selection/invalidation policy.
- `controller/runtime/store.py`: composed SQLite/in-memory adapters only; no copied transformations.

---

### Task 1: Pin Domain Transactions and Parity

**Files:**
- Modify: `tests/unit/datastore/test_sqlite_store_parity.py`
- Modify: `tests/unit/common/test_model_evidence_store.py`
- Modify: `tests/unit/datastore/test_control_trace_store.py`
- Modify: `tests/unit/datastore/test_current_accessors.py`
- Modify: `tests/unit/runtime/test_in_memory_store.py`
- Add: `tests/unit/persistence/test_domain_contracts.py`

**Interfaces:**
- Produces behavior contracts independent of old module locations.

- [ ] **Step 1: Use LSP references for every exported accessor family**

Build a migration table from the `datastore_accessors.py` symbol ranges: control `71-292`; errors/metrics/traces `294-698`; model evidence/migration `691-1603`; settings/pellets/history/current/status/install/generic keys `1620-2337`.

- [ ] **Step 2: Add cross-adapter parity tests**

Parameterize SQLite and in-memory implementations for control delta application, status initialization, current snapshot ownership, history flush/write, metrics update, errors, warnings, and generic keys.

- [ ] **Step 3: Add transaction-failure tests**

Inject failures between activation authority/state/evidence writes, trace append rows, warning clear high-water, and control dequeue/live update. Assert rollback leaves no partial durable state.

- [ ] **Step 4: Run the focused baseline**

```bash
uv run pytest -q \
  tests/unit/persistence/test_domain_contracts.py \
  tests/unit/datastore/test_sqlite_store_parity.py \
  tests/unit/common/test_model_evidence_store.py \
  tests/unit/datastore/test_control_trace_store.py \
  tests/unit/datastore/test_current_accessors.py \
  tests/unit/runtime/test_in_memory_store.py
```

- [ ] **Step 5: Commit tests**

Describe: `test(persistence): pin domain transactions and parity`.

---

### Task 2: Create Protocols and Shared Pure Transformations

**Files:**
- Create: `common/persistence/__init__.py`
- Create: `common/persistence/protocols.py`
- Create: `common/persistence/transforms.py`
- Create: `tests/unit/persistence/test_transforms.py`
- Modify: `controller/runtime/context.py`

**Interfaces:**
- Produces narrow protocols: `ControlPersistence`, `RuntimePersistence`, `HistoryPersistence`, `TracePersistence`, `ModelEvidencePersistence`, and `ControllerStore` composed from only runtime-required capabilities.
- Produces pure functions: `initial_status(settings, pellet_db)`, `current_snapshot(previous, incoming, schema)`, `history_row_to_dict(row)`, and shared control-delta application helpers.

- [ ] **Step 1: Write failing pure transformation tests**

Use existing expected values from datastore/in-memory tests. Cover missing distance module, hopper enablement, optional current fields, history timestamps, and copy/no-alias behavior.

- [ ] **Step 2: Implement immutable/narrow protocols**

Use method signatures with concrete mapping/dataclass types. Do not create a generic CRUD interface or retain `store: object`.

- [ ] **Step 3: Implement transformations by moving existing logic**

Move, do not reinterpret, status/current/history calculations. Both adapters must call these functions after this task.

- [ ] **Step 4: Type `ControllerContext.store`**

Use the smallest composed runtime protocol. Resolve LSP diagnostics rather than widening back to `Any` or `object`.

- [ ] **Step 5: Run tests and >90% branch gate**

Measure `protocols.py` and `transforms.py`; add behavioral branches until both exceed 90%.

- [ ] **Step 6: Commit**

Describe: `refactor(persistence): define typed domain contracts`.

---

### Task 3: Extract Delta-Only Control Persistence

**Files:**
- Create: `common/persistence/control.py`
- Create: `tests/unit/persistence/test_control.py`
- Read all LSP-reported control accessor imports; do not edit shared callers until Task 10

**Interfaces:**
- Produces `flush_control`, `read_control`, `write_control_snapshot`, `enqueue_control_delta`, `read_pending_control_writes`, `execute_control_writes`, `mpc_calibration_command_state`, `mpc_calibration_command_revision`, and `queue_mpc_calibration_command`.

- [ ] **Step 1: Extract the complete transaction implementation**

Use the Task 1 LSP inventory to preserve the public signatures. Keep queue/live update/dequeue in one transaction and the Slice A legacy-row rejection policy. Leave the source implementation and production imports untouched until Task 10 so this task can run concurrently with Tasks 4–7.

- [ ] **Step 2: Move calibration delta interpretation with the queue**

Calibration command state/revision belongs here because it is derived from accepted/pending control deltas.

- [ ] **Step 3: Record the caller migration set**

Record API, runtime, display, test, and common-module callsites for Task 10. Direct module tests import `common.persistence.control`; production callers still import the monolith at this intermediate commit.

- [ ] **Step 4: Run control/cross-writer suites and >90% gate**

Include characterization golden tests. Add tests for every malformed/rollback branch, not source assertions.

- [ ] **Step 5: Commit**

Describe: `refactor(persistence): extract control storage`.

---

### Task 4: Extract Runtime Blob Persistence

**Files:**
- Create: `common/persistence/runtime.py`
- Create: `tests/unit/persistence/test_runtime.py`
- Read all LSP-reported settings, pellets, current, status, warnings/errors, users, and generic-key callers; do not edit them until Task 10

**Interfaces:**
- Produces the existing named settings/pellets/current/status/error/warning/user/generic-key operations without behavior changes.

- [ ] **Step 1: Move validation and JSON blob helpers needed only by runtime state**

Keep settings/pellet Pydantic validation at write boundaries. Use `transforms.initial_status` and `transforms.current_snapshot` rather than copied bodies.

- [ ] **Step 2: Preserve warning/error ownership semantics**

Test writable `ErrorKind`, read-only ALL selection, warning high-water snapshot/clear, and concurrent append during clear.

- [ ] **Step 3: Record every LSP reference for Task 10**

Inventory web routes, mobile socket, displays, controller devices, notifications, defaults/migrations, and tests. Direct tests exercise `common.persistence.runtime`; shared caller imports remain unchanged.

- [ ] **Step 4: Run runtime/blob suites and >90% gate**

Cover absent blobs/default factories, invalid writes, and transactional failures.

- [ ] **Step 5: Commit**

Describe: `refactor(persistence): extract runtime state storage`.

---

### Task 5: Extract History and Metrics Persistence

**Files:**
- Create: `common/persistence/history.py`
- Create: `tests/unit/persistence/test_history.py`
- Read all history/metrics/autotune callers; do not edit them until Task 10

**Interfaces:**
- Produces metrics read/append/update/flush, history read/write/flush, and autotune read/write/length/flush operations.

- [ ] **Step 1: Move SQL and use shared row transforms**

Preserve column ordering, retention trimming, extended-data representation, and flush coupling.

- [ ] **Step 2: Record file management, tuner API, metrics API, common app, and test callsites**

Use LSP references and preserve the migration list for Task 10. Direct tests import the new history module; do not edit shared caller import statements in this parallel task.

- [ ] **Step 3: Run focused history/metrics/tuner tests and >90% gate**

Cover empty tables, limit boundaries, optional columns, update-vs-insert, and retention deletion.

- [ ] **Step 4: Commit**

Describe: `refactor(persistence): extract history and metrics storage`.

---

### Task 6: Extract Control-Trace Persistence

**Files:**
- Create: `common/persistence/control_trace.py`
- Create/modify: `tests/unit/datastore/test_control_trace_store.py`
- Read all trace recorder/replay/update imports; do not edit them until Task 10

**Interfaces:**
- Produces `append_control_trace`, read by session/cook/range, prune by time/schema, and session delete.

- [ ] **Step 1: Move validators, row mapping, connection helper, and SQL atomically**

Keep strict identifier/timestamp/limit checks and maximum limit.

- [ ] **Step 2: Record `ControlTraceRecorder`, replay, MPC update/report callsites**

Keep controller trace contract types in `common/control_trace.py`; only SQL belongs in the destination. Direct tests import the new persistence module; Task 10 performs caller migration.

- [ ] **Step 3: Run trace tests and >90% gate**

Cover invalid ranges/limits, alternate database path, prune caps, incompatible schema, and rollback on append failure.

- [ ] **Step 4: Commit**

Describe: `refactor(persistence): extract control trace storage`.

---

### Task 7: Extract Install-State Persistence

**Files:**
- Create: `common/persistence/install_state.py`
- Create: `tests/unit/persistence/test_install_state.py`
- Read all updater/wizard/system imports; do not edit them until Task 10

**Interfaces:**
- Produces OS info, wizard install blob/status, and updater status operations.

- [ ] **Step 1: Move key constants and generic helpers**

Keep prefix/key names and default payloads exact.

- [ ] **Step 2: Record API wizard/update, detached wizard/updater, and system callsites**

Keep installation logic outside persistence. Direct tests import `install_state`; Task 10 migrates production callers.

- [ ] **Step 3: Run updater/wizard/install tests and >90% gate**

Cover missing/corrupt blobs, set/get/delete, and status field preservation.

- [ ] **Step 4: Commit**

Describe: `refactor(persistence): extract installer state storage`.

---

### Task 8: Extract Model-Evidence Transactions and Move Policy Upward

**Files:**
- Create: `common/persistence/model_evidence.py`
- Create: `controller/model_learning/migration.py`
- Create: `tests/unit/persistence/test_model_evidence.py`
- Create/modify: migration/model activation tests
- Modify model persistence/report/Hold/API imports

**Interfaces:**
- Persistence produces append/read/reset/invalidate evidence; commit/read activation; phase CAS; rollback CAS; immutable activation state/outcome values.
- Controller migration produces `migrate_mpc_learning_authority(...)` and `GreyLearningMigrationResult`.

- [ ] **Step 1: Write dependency-direction tests**

Assert importing `common.persistence.model_evidence` does not import `controller.mpc`, `controller.mpc_snapshot`, or concrete activation manager classes.

- [ ] **Step 2: Move SQL transaction code only**

Model-evidence module may validate common evidence records and persistence DTOs. It must not choose controller configurations or snapshot winners.

- [ ] **Step 3: Move authority selection/migration to controller layer**

`controller/model_learning/migration.py` consumes persisted state and `controller.mpc_snapshot` to choose/invalidate authority. Preserve current invalidation evidence and reason strings.

- [ ] **Step 4: Migrate callers**

`ModelPersistenceWorker`, report generation, Hold restore, and Flask activation routes import the appropriate persistence or migration layer directly.

- [ ] **Step 5: Run activation/migration tests and per-file >90% gates**

Cover every CAS mismatch, idempotent rollback, malformed row, missing authority, schema invalidation, and migration source selection branch.

- [ ] **Step 6: Commit**

Describe: `refactor(persistence): separate model storage from migration policy`.

---

### Task 9: Compose Runtime Store Adapters Without Semantic Copies

**Files:**
- Modify: `controller/runtime/store.py`
- Modify: `control.py`
- Modify: `display_process.py`
- Modify: runtime store tests/fakes

**Interfaces:**
- `SqliteStore` and `InMemoryStore` implement the narrow protocols from Task 2.
- In-memory adapters reuse `transforms.py` and the same delta application function; SQLite adapters delegate to domain modules.

- [ ] **Step 1: Replace the broad `Store` ABC**

Delete methods no runtime consumer uses. Compose protocols structurally; do not add forwarding methods merely to match the old 34-method surface.

- [ ] **Step 2: Remove duplicated status/current/control/history transformations**

Call shared functions. Keep in-memory storage mechanics only.

- [ ] **Step 3: Migrate entry-point construction and fakes**

Use concrete composed adapters at `control.py` and `display_process.py`; context typing must pass without casts to `object`.

- [ ] **Step 4: Run parity/runtime suites**

Expected: SQLite and memory parameterized contracts pass identically.

- [ ] **Step 5: Commit**

Describe: `refactor(runtime): compose typed persistence adapters`.

---

### Task 10: Remove the Accessor Monolith

**Files:**
- Remove: `common/datastore_accessors.py`
- Follow and refresh: `docs/superpowers/plans/2026-08-12-persistence-import-migration.md`
- Modify every importer assigned in that matrix
- Modify import smoke tests

- [ ] **Step 1: Refresh and lock the migration matrix**

Run LSP references for every remaining exported symbol and regenerate the AST importer inventory. Update the checked matrix for additions/removals before editing callers. Every importer/symbol must have exactly one destination domain; multi-domain files intentionally have multiple rows. Do not create `misc.py`: classify by durable table/key ownership.

- [ ] **Step 2: Cut over control and runtime-blob callers**

Migrate the matrix rows for `common.persistence.control` and `common.persistence.runtime`, including production entry points, blueprints/mobile, common helpers, controller runtime/devices, displays, notifications, tools, fixtures, and tests. Run:

```bash
uv run pytest -q \
  tests/characterization/test_control_delta_seam.py \
  tests/characterization/test_control_writes_cross_writer.py \
  tests/characterization/test_process_command_golden.py \
  tests/e2e/test_work_cycle_e2e.py \
  tests/unit/common/test_common_blobs.py \
  tests/unit/controller/test_pid_sp_learning.py \
  tests/unit/datastore/test_current_accessors.py \
  tests/unit/datastore/test_sqlite_store_parity.py \
  tests/unit/runtime/test_in_memory_store.py \
  tests/unit/runtime/test_devices.py \
  tests/unit/wizard \
  tests/web/test_api_settings_update.py \
  tests/web/test_api_pellets.py \
  tests/web/test_socketio_app_data.py
```

Describe this sequential change: `refactor(persistence): migrate control and runtime callers`.

- [ ] **Step 3: Cut over history and install-state callers**

Migrate the matrix rows for `common.persistence.history` and `common.persistence.install_state`. Run:

```bash
uv run pytest -q \
  tests/unit/common/test_common_history.py \
  tests/unit/common/test_common_metrics.py \
  tests/unit/common/test_install_status.py \
  tests/unit/common/test_os_info_read_path_is_pure.py \
  tests/unit/datastore/test_datastore.py \
  tests/unit/file_mgmt/test_cookfile.py \
  tests/unit/updater/test_acados_build.py \
  tests/unit/wizard \
  tests/web/test_api_metrics.py \
  tests/web/test_api_tuner.py \
  tests/web/test_api_tuner_auto.py \
  tests/web/test_api_wizard.py
```

Describe this sequential change: `refactor(persistence): migrate history and install callers`.

- [ ] **Step 4: Cut over control-trace callers**

Migrate the matrix rows for `common.persistence.control_trace`. Run:

```bash
uv run pytest -q \
  tests/unit/datastore/test_control_trace_store.py \
  tests/unit/mpc/test_model_evidence_report.py \
  tests/unit/mpc/test_update_mpc.py \
  tests/unit/runtime/test_hold_control_trace.py
```

Describe this sequential change: `refactor(persistence): migrate control trace callers`.

- [ ] **Step 5: Cut over model-evidence and migration-policy callers**

Migrate the matrix rows for `common.persistence.model_evidence` and `controller.model_learning.migration`. Run:

```bash
uv run pytest -q \
  tests/unit/common/test_model_evidence_store.py \
  tests/unit/mpc/test_grey_learning_snapshot_migration.py \
  tests/unit/mpc/test_model_evidence_report.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_model_persistence.py \
  tests/web/test_api_model_evidence.py
```

Describe this sequential change: `refactor(persistence): migrate model evidence callers`.

- [ ] **Step 6: Delete the monolith**

Remove `common/datastore_accessors.py` and compatibility-import assertions. Update import smoke tests to import each domain directly. Run LSP workspace references and an AST import-boundary search. Expected: zero `common.datastore_accessors` imports and zero `common.persistence` imports of `controller.*`.

- [ ] **Step 7: Run aggregate cutover tests and commit deletion**

Run `tests/unit/persistence`, `tests/unit/datastore`, `tests/unit/runtime`, `tests/unit/common`, and `tests/web`. Expected: PASS. Describe: `refactor(persistence): remove the accessor monolith`.

These cutovers are sequential Jujutsu changes. Several files consume multiple domains; never dispatch Steps 2–6 concurrently.

---

### Task 11: Enforce Persistence Branch Coverage and Integration

**Files:**
- Modify tests only where a real uncovered branch lacks a contract.

- [ ] **Step 1: Run one aggregate persistence coverage pass**

Measure every `common/persistence/*.py` and `controller/model_learning/migration.py` with `--cov-branch --cov-report=json:.coverage-persistence.json`.

- [ ] **Step 2: Run the exact per-file gate**

Use `scripts/check_branch_coverage.py --minimum 90` with every final file explicitly listed. Expected: each is strictly greater than 90.0%.

- [ ] **Step 3: Close meaningful gaps**

Add tests only for observable failure/edge branches: database rollback, malformed rows, absent keys, retention boundaries, concurrency/CAS, validation rejection, and migration precedence. Do not exclude branches or add pragmas to reach the target.

- [ ] **Step 4: Run repository-facing integration suites**

Run datastore, common, runtime, web, updater, wizard, file management, notification, and display tests that import persistence.

- [ ] **Step 5: Run Ruff and LSP diagnostics**

Expected: clean changed files and no circular import diagnostics.

- [ ] **Step 6: Commit test additions separately**

Describe: `test(persistence): exceed branch coverage gate`.
