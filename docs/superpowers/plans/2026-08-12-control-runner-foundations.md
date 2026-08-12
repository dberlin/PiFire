# Control and Runner Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy control-merge queue and centralize controller-runner observation outcome buffering without changing FIFO, generation, eviction, or evidence behavior.

**Architecture:** Control persistence exposes explicit snapshot and delta operations; the queue becomes delta-only after startup rejects legacy rows. A new `ObservationOutcomeBuffer` owns shared sync/threaded queues and generation-bound draining while runners retain execution and locking.

**Tech Stack:** Python 3.14, SQLite, dataclasses, pytest, pytest-cov, Ruff, Pyright/LSP, Jujutsu.

## Global Constraints

- Preserve control-delta version 1, operation order, validation messages, FIFO transaction atomicity, and authoritative snapshot writes.
- Define legacy persisted-row handling before deleting `WriteKind.MERGE`; never interpret an unversioned row as a delta.
- Preserve `ObservationSubmission`, `ObservationOutcomeEnvelope`, `ObservationTerminalDrop`, and `ObservationOutcomeDrain` value shapes.
- The buffer is not internally synchronized. `ThreadedControllerRunner` owns its existing lock; `SyncControllerRunner` calls the same object without a lock.
- Use LSP references before changing `WriteKind`, `process_command`, runner fields, or observation dataclasses.

---

### Task 1: Add an Exact Per-File Branch Coverage Gate

**Files:**
- Create: `scripts/check_branch_coverage.py`
- Create: `tests/unit/tools/test_check_branch_coverage.py`

**Interfaces:**
- Produces: CLI `check_branch_coverage.py --coverage PATH --minimum FLOAT FILE...`.
- Contract: exits 0 only when every named file exists in coverage JSON and `covered_branches / num_branches * 100 > minimum`; zero-branch files are 100%.

- [ ] **Step 1: Write failing CLI unit tests**

Cover: missing file, exactly 90.0 rejection, 90.1 acceptance, zero-branch acceptance, malformed/missing summary fields, and normalized `./path` handling. Use a temporary coverage JSON with entries shaped like:

```python
{"files": {"controller/x.py": {"summary": {"num_branches": 10, "covered_branches": 9}}}}
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `uv run pytest -q tests/unit/tools/test_check_branch_coverage.py`

Expected: FAIL because the script does not exist.

- [ ] **Step 3: Implement the gate**

Use an immutable result and explicit strict comparison:

```python
@dataclass(frozen=True, slots=True)
class BranchCoverage:
    path: str
    covered: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else 100.0 * self.covered / self.total
```

Print one deterministic row per file and a final failure listing. Reject absent files and invalid/non-integer branch counts.

- [ ] **Step 4: Run focused tests and LSP diagnostics**

Run the test above. Expected: PASS. Run Pyright diagnostics on the script and test; expected: no introduced diagnostics.

- [ ] **Step 5: Commit**

Describe the change: `test: enforce per-file branch coverage gates`.

---

### Task 2: Characterize the Delta-Only Upgrade Boundary

**Files:**
- Modify: `tests/characterization/test_control_delta_seam.py`
- Modify: `tests/unit/common/test_control_delta_envelope.py`
- Modify: `tests/unit/common/test_common_blobs.py`
- Modify: `tests/unit/datastore/test_sqlite_store_parity.py`

**Interfaces:**
- Produces tests pinning the final delta-only queue contract.
- Decision: any unversioned row already present in `queue_control_write` is dequeued, logged as an error with its row ID/origin, and not applied. Startup does not guess its intent.

- [ ] **Step 1: Use LSP references to inventory `WriteKind.MERGE`, `strip_null_members`, and `process_command(kind=...)`**

Record production and test callers in the task notes. Confirm no production caller intentionally submits an unversioned partial.

- [ ] **Step 2: Add failing contract tests**

Assert:

1. delta rows apply FIFO;
2. malformed versioned delta rows are rejected atomically and dequeued;
3. unversioned legacy rows are rejected/dequeued/logged without changing live control;
4. in-memory and SQLite stores have identical behavior;
5. snapshot replacement remains immediate and bypasses the queue.

- [ ] **Step 3: Run focused tests and confirm only the new end-state assertions fail**

```bash
uv run pytest -q \
  tests/characterization/test_control_delta_seam.py \
  tests/unit/common/test_control_delta_envelope.py \
  tests/unit/common/test_common_blobs.py \
  tests/unit/datastore/test_sqlite_store_parity.py
```

- [ ] **Step 4: Commit tests separately**

Describe: `test: define the delta-only control queue boundary`.

---

### Task 3: Introduce Explicit Snapshot and Delta Operations

**Files:**
- Modify: `common/datastore_accessors.py:71-292`
- Modify: `controller/runtime/store.py:61-192,410-515`
- Modify: `controller/runtime/context.py`
- Modify callers reported by LSP references to `write_control`

**Interfaces:**
- Produces:

```python
def write_control_snapshot(control: Mapping[str, object], *, origin: str) -> None: ...
def enqueue_control_delta(delta: Mapping[str, object], *, origin: str) -> None: ...
```

- `Store` protocol methods use the same names.
- `enqueue_control_delta` validates and copies before queueing; it never mutates the caller value.

- [ ] **Step 1: Add the two operations beside the existing API**

Move the current `OVERWRITE` and `DELTA` branches unchanged into their explicit functions. Add tests for copy/no-mutation and invalid-delta rejection.

- [ ] **Step 2: Migrate callers with LSP**

Authoritative control-loop/reset writers use `write_control_snapshot`. Web/display/API intent writers use `enqueue_control_delta`. Do not infer based on variable names; inspect each caller's ownership and existing `WriteKind` argument.

- [ ] **Step 3: Migrate `SqliteStore`, `InMemoryStore`, and `ControllerContext` typing**

Expose the explicit methods through the runtime store contract. Keep the old `write_control` temporarily only inside this task so tests can demonstrate all production callers moved; it is deleted in Task 4.

- [ ] **Step 4: Run control writer contracts**

Run the Task 2 suite plus `tests/characterization/test_control_writes_cross_writer.py` and `tests/characterization/test_process_command_golden.py`. Expected: PASS.

- [ ] **Step 5: Commit**

Describe: `refactor: separate control snapshots from deltas`.

---

### Task 4: Remove Merge and Command Compatibility Paths

**Files:**
- Modify: `common/common.py:31-45,209-237`
- Modify: `common/api_commands.py:47-1095`
- Modify: `common/control_delta.py:118-120`
- Modify: `common/datastore_accessors.py:101-292`
- Modify: `controller/runtime/store.py:164-191`
- Modify affected tests and fixtures

**Interfaces:**
- `process_command(action=None, arglist=None, origin="unknown")` has no `kind` parameter.
- Every command handler accepts `(data, control, settings, arglist, origin)`.
- Queue drain accepts only `is_control_delta(payload)`; legacy rows follow Task 2's reject/dequeue/log policy.

- [ ] **Step 1: Remove `kind` from command handlers and `_write_control_delta`**

The helper becomes:

```python
def _write_control_delta(delta, origin):
    enqueue_control_delta(delta, origin=origin)
```

Update `_COMMAND_DISPATCH` handlers and all direct tests. Remove tests whose sole purpose was the test-only overwrite escape hatch; replace them with snapshot-operation tests from Task 3.

- [ ] **Step 2: Delete `WriteKind.MERGE`, `write_control`, and null-stripping merge code**

Retain no alias. If `WriteKind` has no references after LSP migration, delete the enum; otherwise retain only semantically valid members until Slice D replaces the remaining enum use.

- [ ] **Step 3: Make both queue drains delta-only**

SQLite rejection and dequeue happen in the same transaction. The in-memory fake logs/rejects with the same semantic result.

- [ ] **Step 4: Run all control contracts**

Run Task 3's suite plus `tests/unit/runtime/test_in_memory_store.py` and `tests/web`. Expected: PASS with no production merge-path assertion.

- [ ] **Step 5: Run LSP references again**

Expected: no references to `WriteKind.MERGE`, `strip_null_members` from a control drain, `process_command(kind=...)`, or `write_control`.

- [ ] **Step 6: Commit**

Describe: `refactor: remove legacy control merge writes`.

---

### Task 5: Specify the Shared Observation Buffer Contract

**Files:**
- Create: `tests/unit/runtime/test_observation_buffer.py`
- Read/modify only as needed: `tests/unit/runtime/test_threaded_runner.py`

**Interfaces:**
- Produces behavioral tests for `ObservationOutcomeBuffer` before implementation.

- [ ] **Step 1: Write failing tests for the complete state machine**

Cover:

- monotonically supplied sequence/generation storage;
- bounded envelope eviction creates `runner-outcome-evicted` terminal drop and increments counters;
- outcome drain freezes evidence only when a generation context exists;
- unbound envelopes and drops remain withheld in original order;
- binding later releases withheld items;
- retiring a context prevents future release until rebound;
- drain clears only delivered counters/sequences;
- no aliasing of caller-owned mutable evidence.

- [ ] **Step 2: Run and confirm import failure**

Run: `uv run pytest -q tests/unit/runtime/test_observation_buffer.py`.

- [ ] **Step 3: Commit tests**

Describe: `test: specify controller observation buffering`.

---

### Task 6: Implement `ObservationOutcomeBuffer`

**Files:**
- Create: `controller/runtime/observation_buffer.py`
- Modify: `controller/runtime/runner.py:62-262` only to import shared value types/helpers if moved
- Test: `tests/unit/runtime/test_observation_buffer.py`

**Interfaces:**
- Produces:

```python
class ObservationOutcomeBuffer:
    def __init__(self, capacity: int): ...
    def bind_context(self, generation: int, session_id: str, cook_id: str) -> None: ...
    def retire_context(self, generation: int) -> None: ...
    def append_outcome(self, envelope: ObservationOutcomeEnvelope) -> None: ...
    def append_terminal_drop(self, drop: ObservationTerminalDrop) -> None: ...
    def drain(self) -> ObservationOutcomeDrain: ...
```

- Keep `_freeze_evidence` as the single evidence conversion function; move it with LSP if the buffer becomes its only caller.

- [ ] **Step 1: Implement the minimal bounded buffer**

Use `deque(maxlen=capacity)` only where silent eviction is not possible; explicit eviction must record its sequence and terminal drop before append.

- [ ] **Step 2: Implement generation context binding and drain**

Drain must build new immutable envelopes with `dataclasses.replace`, preserve withheld order, then reset only delivered drop counters.

- [ ] **Step 3: Run the focused contract**

Expected: all buffer tests pass.

- [ ] **Step 4: Run branch coverage on the new module**

Generate JSON and use Task 1's gate at minimum 90. Expected: >90%; add behavioral tests for uncovered real branches.

- [ ] **Step 5: Commit**

Describe: `refactor: centralize observation outcome buffering`.

---

### Task 7: Migrate Sync and Threaded Runners

**Files:**
- Modify: `controller/runtime/runner.py:553-810,878-1749`
- Modify: `tests/unit/runtime/test_threaded_runner.py`
- Modify: `tests/fakes/runner.py` if it mirrors buffer behavior

**Interfaces:**
- Both runners own `_observation_buffer: ObservationOutcomeBuffer`.
- Public runner methods and immutable return types do not change.

- [ ] **Step 1: Use LSP references on every removed queue field**

Map `_observation_outcomes`, `_terminal_drops_since_drain`, `_outcome_drops_since_drain`, `_outcome_dropped_sequences`, and `_evidence_contexts`. Replace all reads/writes through the buffer, including reconfigure, stop, bind, retire, completion, eviction, and drain paths.

- [ ] **Step 2: Migrate `SyncControllerRunner`**

Replace duplicated append/evict/drain code with buffer calls. Keep sequence allocation and core invocation in the runner.

- [ ] **Step 3: Migrate `ThreadedControllerRunner`**

Call the buffer only while holding the existing runner lock. Do not add a second lock to the buffer or change worker wakeup behavior.

- [ ] **Step 4: Delete duplicated fields and drain implementations**

No compatibility properties. LSP references to deleted fields must be zero outside tests intentionally inspecting runner internals; update those tests to assert public drains.

- [ ] **Step 5: Run focused runtime suites**

```bash
uv run pytest -q \
  tests/unit/runtime/test_observation_buffer.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_hold_model_persistence.py
```

Expected: PASS under random order and xdist defaults.

- [ ] **Step 6: Run the real runner smoke path**

Run: `uv run python tools/smoke_acados_hold.py`. Expected: one framed Hold update and teardown complete without observation loss or thread leak.

- [ ] **Step 7: Commit**

Describe: `refactor: share observation buffering across runners`.
