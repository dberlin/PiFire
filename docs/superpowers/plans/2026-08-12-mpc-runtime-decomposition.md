# MPC Runtime Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `controller/mpc.py` from a 2,842-line numerical/lifecycle god object into a thin plugin composition root with separately testable core, factory, calibration, activation, and grey-learning runtimes, each above 90% branch coverage.

**Architecture:** `MpcCore` owns only real-time numerical state/resources. `MpcPairFactory` owns configuration and active/candidate construction. Calibration, activation, and grey learning are independent stateful collaborators. `controller.mpc.Controller` composes them and implements the existing plugin contract required by dynamic loading. Flask activation routes use a controller-layer service and shared factory rather than constructing native resources themselves.

**Tech Stack:** Python 3.14, NumPy, Acados native wrapper, dataclasses, Pydantic contracts, pytest/pytest-cov, Ruff, Pyright/LSP, Jujutsu.

## Global Constraints

- Depends on Slice A's runner buffer and Slice D's final model-evidence persistence protocol.
- Preserve dynamic import `controller.mpc.Controller`, `_DEFAULTS` behavior through a new public configuration module, controller output/status/trace/snapshot shapes, native solve fallback, and applied-output feedback.
- Preserve durable activation phase order, CAS identity, compensation, failed generation fencing, confidence FIFO, snapshot revisions, cook refit policy, calibration revisions, and close-on-every-path semantics.
- No Flask imports under `controller/`; routes map typed service outcomes.
- Remove private helper imports from `controller.mpc`; move callers to named public modules.
- Every final module named in this plan, including thin `controller/mpc.py`, must have strictly greater than 90% branch coverage.

## Final File Map

- `controller/mpc_config.py`: defaults, finite/optional conversion, config normalization, model-identification metadata.
- `controller/mpc_core.py`: estimator/native solver pair and numerical update.
- `controller/mpc_factory.py`: `OwnedMpcPair`, descriptor build/restore, candidate dry solve, resource ownership.
- `controller/mpc_calibration.py`: command/state transitions and calibration probe allocation.
- `controller/model_learning/activation_runtime.py`: active/rollback/inert pair lifecycle and durability.
- `controller/model_learning/grey_runtime.py`: observations, fitting/evaluation, snapshots, refit/checkpoint preparation.
- `controller/model_learning/activation_service.py`: operator activation/rollback application used by Flask.
- `controller/runtime/model_lifecycle.py`: exact typed runner lifecycle protocol consumed by Hold.
- `controller/mpc.py`: thin `ControllerBase` plugin composition root.

---

### Task 1: Freeze the Public MPC Contract and Resource Ownership

**Files:**
- Modify: `tests/unit/mpc/test_mpc_controller.py`
- Modify: `tests/unit/mpc/test_mpc_model_snapshot.py`
- Modify: `tests/unit/mpc/test_mpc_calibration_runtime.py`
- Modify: `tests/unit/mpc/test_model_activation.py`
- Modify: `tests/web/test_api_model_evidence.py`
- Add: `tests/unit/mpc/test_mpc_public_contract.py`

**Interfaces:**
- Produces contract tests for the final composed `controller.mpc.Controller`.

- [ ] **Step 1: Use LSP references for `Controller`, `_DEFAULTS`, `_optional_float`, lifecycle methods, snapshot methods, and trace methods**

Classify production callers, tests, tools, and docs experiments. Every private import gets a destination in Task 2.

- [ ] **Step 2: Add public-contract tests**

Pin constructor/config input, `set_target`, `update`, `set_output`, status, trace diagnostics/allocation, snapshot restore/refit, calibration commands, activation restore/rollback/events, `commands_fan`, `wants_async`, period, and idempotent close.

- [ ] **Step 3: Add explicit ownership/failure tests**

For candidate build failure, dry-solve failure, persistence failure, activation compensation, restore rejection, and close, assert each estimator/solver/pair closes exactly once and active resources remain usable when required.

- [ ] **Step 4: Run the MPC baseline**

Run `tests/unit/mpc`, model persistence/runtime activation tests, and web model-evidence tests. Record the baseline.

- [ ] **Step 5: Commit tests**

Describe: `test(mpc): pin composed controller contracts`.

---

### Task 2: Extract Configuration and Numerical `MpcCore`

**Files:**
- Create: `controller/mpc_config.py`
- Create: `controller/mpc_core.py`
- Create: `tests/unit/mpc/test_mpc_core.py`
- Modify: `controller/mpc.py:108-531,2654-2824`
- Modify private-helper importers such as `controller/update_mpc.py` and tools/experiments

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True, slots=True)
class MpcStep:
    cycle_ratio: float
    fan: dict[str, float | None]
    diagnostics: MpcTraceDiagnostics
    allocation: AllocationResult
    baseline_allocation: AllocationResult


class MpcCore:
    def set_target(self, set_point: float) -> None: ...
    def set_output(self, applied: AppliedOutput) -> None: ...
    def update(self, current: float) -> MpcStep: ...
    def snapshot_parameters(self) -> Mapping[str, object]: ...
    def close(self) -> None: ...
```

- `mpc_config.py` exports `DEFAULT_MPC_CONFIG`, `finite_float`, `optional_float`, and normalization/model-identification helpers with public names.

- [ ] **Step 1: Write failing `MpcCore` tests**

Cover estimator update input, successful native solve, malformed native output, solver exception hold-last-safe-command, recovery counters, allocation/fan behavior, applied-output feedback, authorization denial supplied by caller, and idempotent close.

- [ ] **Step 2: Move defaults/helpers with LSP**

Rename private external imports to public symbols; preserve values exactly. Do not retain `_DEFAULTS` alias.

- [ ] **Step 3: Move construction and numerical update into `MpcCore`**

Keep slow lifecycle/persistence/calibration out. Inject activation authorization/fallback callback and calibration probe adjustment as explicit call inputs/callbacks rather than importing runtimes.

- [ ] **Step 4: Adapt `Controller` temporarily to consume `MpcCore`**

Return the existing dictionary shape from the plugin adapter using `MpcStep`; keep trace accessors exact.

- [ ] **Step 5: Run core/public tests and >90% gates**

Gate `mpc_config.py` and `mpc_core.py`. Add tests for real branches only.

- [ ] **Step 6: Commit**

Describe: `refactor(mpc): extract numerical control core`.

---

### Task 3: Establish the Pair Factory and Descriptor Boundary

**Files:**
- Create: `controller/mpc_factory.py`
- Create: `tests/unit/mpc/test_mpc_factory.py`
- Modify: `controller/mpc.py:398-531,774-870,2056-2137`
- Modify: `blueprints/api/routes.py:297-365` later consumed by Task 7

**Interfaces:**
- Produces:

```python
@dataclass(slots=True)
class OwnedMpcPair:
    core: MpcCore
    descriptor: GreyControlPairDescriptor

    def close(self) -> None: ...


class MpcPairFactory:
    def build(self, configuration, *, authorized: bool) -> OwnedMpcPair: ...
    def restore(self, descriptor) -> OwnedMpcPair: ...
    def dry_solve(self, pair, *, temperature_c: float) -> NativeTiming: ...
```

- [ ] **Step 1: Write factory ownership tests**

Cover estimator variants, digest mismatch, descriptor reconstruction, native build failure, dry-solve failure, partial resource construction, successful ownership transfer, and idempotent close.

- [ ] **Step 2: Move `_build_for`, estimator construction, descriptor mapping, and timing**

Factory is the only construction path for runtime restore and operator activation.

- [ ] **Step 3: Migrate `Controller` restore/build paths**

No direct native controller construction remains in `controller/mpc.py` outside factory composition.

- [ ] **Step 4: Run factory/restore tests and >90% gate**

Expected: preserved digests and close counts.

- [ ] **Step 5: Commit**

Describe: `refactor(mpc): centralize control pair construction`.

---

### Task 4: Extract Calibration Runtime

**Files:**
- Create: `controller/mpc_calibration.py`
- Create: `tests/unit/mpc/test_mpc_calibration_runtime_unit.py`
- Modify: `controller/mpc.py:260-291,327-338,2442-2653`

**Interfaces:**
- Produces `CalibrationCommand`, immutable calibration decision/result types, and:

```python
class MpcCalibrationRuntime:
    def request(self, command: CalibrationCommand) -> None: ...
    def cancel(self, reason: str) -> None: ...
    def advance(self, baseline_q: float, temperature_c: float, forecast) -> CalibrationDecision: ...
    def register_output(self, applied: AppliedOutput) -> None: ...
    def status(self) -> Mapping[str, object]: ...
```

- [ ] **Step 1: Write state-transition tests**

Cover revision ordering/stale commands, start/pause/resume/stop/reset, confirmation requirements, safety ceiling, forecast maximum, unknown actuation, feedback disposition, cancellation, and completed frame registration.

- [ ] **Step 2: Move calibration state and methods**

The runtime receives forecast and clock dependencies. It does not access persistence, activation, or controller private fields.

- [ ] **Step 3: Compose it into `Controller`/`MpcCore`**

`MpcCore.update` accepts the runtime's probe adjustment; plugin methods delegate public calibration commands to the runtime while preserving behavior.

- [ ] **Step 4: Run calibration suites and >90% gate**

Expected: existing and new tests pass.

- [ ] **Step 5: Commit**

Describe: `refactor(mpc): extract calibration runtime`.

---

### Task 5: Extract Durable Activation Runtime

**Files:**
- Create: `controller/model_learning/activation_runtime.py`
- Create: `controller/runtime/model_lifecycle.py`
- Create: `tests/unit/mpc/test_activation_runtime.py`
- Modify: `controller/mpc.py:552-887,1086-1120,1426-1578`
- Modify: `controller/runtime/runner.py:516-544,604-622,682-793,1437-1465` to type the existing methods; add no second dispatch path

**Interfaces:**
- Produces `ActivationRuntime` owning active, rollback, inert/prepared pair, authorization, failed generations, persistence receipts, transitions, events, restore, compensation, and close.
- Produces the exact `ModelLifecycleRunner` protocol from the master plan using `ModelActivationState`, `ModelEvidenceRecord`, `DurableActivationReceipt`, and `TeardownRefitOutcome`.

- [ ] **Step 1: Write the activation state-machine matrix**

Cover prepared→durable→authorized→installed→active, every persistence failure phase, duplicate transaction IDs, stale role/candidate generation, compensation success/failure, runtime solve failure, rollback, restart restore, confidence ordering, and close.

- [ ] **Step 2: Move state and methods as one cohesive owner**

Inject `MpcPairFactory` and `ModelEvidencePersistence`. Do not let the runtime reach into `Controller` fields.

- [ ] **Step 3: Land and implement the fixed runner-facing protocol**

Type the existing sync/threaded methods against `ModelLifecycleRunner`. Preserve ordered drain-and-clear, record copying, durable receipt ownership, and the stop-for-refit → optional finalize → finish-teardown close sequence. The plugin delegates directly to `ActivationRuntime`/grey-learning collaborators; the numerical core knows only an authorization/fallback callback.

- [ ] **Step 4: Run activation/model persistence tests and >90% gate**

Expected: exact durable ordering and resource ownership preserved.

- [ ] **Step 5: Commit**

Describe: `refactor(mpc): extract activation runtime`.

---

### Task 6: Extract Grey Learning, Snapshot, and Refit Runtime

**Files:**
- Create: `controller/model_learning/grey_runtime.py`
- Create: `tests/unit/mpc/test_grey_learning_runtime.py`
- Modify: `controller/mpc.py:889-1425,1580-2439`

**Interfaces:**
- Produces `GreyLearningRuntime` for observation/forecast registration, off-path fitting/evaluation, lifecycle evidence payloads, snapshots/restores, reviewed checkpoints, cook history/refit, teardown outcomes, and status projection.
- It returns typed candidate preparations/transitions to `ActivationRuntime`; it never installs an active pair itself.

- [ ] **Step 1: Write learning state and stale-request tests**

Cover disabled/collecting/fitting/evaluating/rejected/accepted states, insufficient samples/excitation, stale identity/generation/digest, forecast failures, checkpoint failure, operator versus passive policy, cook refit outcomes, snapshot version/delay mismatch, and worker exceptions.

- [ ] **Step 2: Move observation and evaluation code**

Inject fitter, pair factory, persistence protocol, clocks, and current active descriptor callback. Preserve locks and off-path execution ownership.

- [ ] **Step 3: Move snapshot/history/refit code**

Keep wire version and parameter schema exact. `Controller` asks the runtime for status/snapshot and routes candidate transitions to activation.

- [ ] **Step 4: Run learning/refit/snapshot suites and >90% gate**

Expected: behavior and evidence IDs/reasons remain exact.

- [ ] **Step 5: Commit**

Describe: `refactor(mpc): extract grey learning runtime`.

---

### Task 7: Move Operator Activation Out of Flask

**Files:**
- Create: `controller/model_learning/activation_service.py`
- Create: `tests/unit/mpc/test_activation_service.py`
- Modify: `blueprints/api/routes.py:297-517`
- Modify: `tests/web/test_api_model_evidence.py`

**Interfaces:**
- Produces typed accepted/rejected outcomes and:

```python
class ModelActivationService:
    def activate(self, request, *, now_ms: int) -> ActivationOutcome: ...
    def rollback(self, request, *, now_ms: int) -> RollbackOutcome: ...
```

- [ ] **Step 1: Write service tests independent of Flask**

Cover blocked policy, stale decision/digest, missing checkpoint, pair build/dry solve failure, persistence timeout/failure, accepted activation, rollback CAS mismatch/idempotence, and close-on-all-paths.

- [ ] **Step 2: Move domain workflow into the service**

Use `MpcPairFactory` and persistence protocols. Return reason/status categories; do not return Flask responses.

- [ ] **Step 3: Make Flask routes thin**

Validate Pydantic request, call service, map typed outcomes to exact existing 200/409/422/503 envelopes. Delete native helpers/imports from routes.

- [ ] **Step 4: Run service/web tests and >90% gate**

Gate the service as part of #9. Expected exact API responses.

- [ ] **Step 5: Commit**

Describe: `refactor(mpc): move activation workflow out of Flask`.

---

### Task 8: Make `controller.mpc.Controller` the Thin Composition Root

**Files:**
- Rewrite: `controller/mpc.py`
- Modify: `controller/runtime/runner.py` construction only if signatures changed
- Modify tests/tools importing moved public symbols

**Interfaces:**
- `Controller` directly implements `ControllerBase` methods by coordinating `MpcCore`, calibration, activation, and learning collaborators.
- It owns collaborator construction/close order, not their internal state.

- [ ] **Step 1: Construct collaborators explicitly**

Order: configuration → pair factory → initial pair/core → activation → calibration → grey learning. Pass callbacks/protocols at construction; no collaborator imports the plugin to access it.

- [ ] **Step 2: Reduce methods to public orchestration only**

Keep target/update/output/status/trace/snapshot/refit/calibration/activation APIs. Delete moved private methods and fields; no forwarding aliases for private names.

- [ ] **Step 3: Make close order explicit and idempotent**

Stop learning submissions, flush/stop persistence, retire candidate/rollback, then close active numerical resources exactly once.

- [ ] **Step 4: Run public contract, runner, Hold integration, and closed-loop tests**

Expected: all pass.

- [ ] **Step 5: Commit**

Describe: `refactor(mpc): compose the controller from focused runtimes`.

---

### Task 9: Remove Old Paths and Enforce >90% Branch Coverage

**Files:**
- Remove obsolete implementation blocks/imports/tests tied to private fields
- Modify behavioral tests to close real coverage gaps

- [ ] **Step 1: Run LSP references and AST imports**

Expected: no external imports of old private `controller.mpc` helpers, no Flask/native construction duplication, and no duplicate lifecycle implementation.

- [ ] **Step 2: Run aggregate #9 coverage**

Measure `mpc.py`, `mpc_config.py`, `mpc_core.py`, `mpc_factory.py`, `mpc_calibration.py`, `activation_runtime.py`, `grey_runtime.py`, `activation_service.py`, and `model_lifecycle.py` in one JSON report.
- [ ] **Step 3: Enforce the strict gate**

Use `scripts/check_branch_coverage.py --minimum 90` with every module. Each must exceed 90.0%.

- [ ] **Step 4: Add tests for uncovered behavior branches**

Prioritize native errors, stale generations, persistence failures, compensation, malformed snapshots, fit rejection, calibration cancellation, and close paths. Do not add coverage exclusions.

- [ ] **Step 5: Run MPC/runtime/web aggregate and native smoke**

Run `tests/unit/mpc`, relevant runtime model persistence/threaded runner suites, `tests/web/test_api_model_evidence.py`, slow tests only where they defend moved contracts, and `tools/smoke_acados_hold.py`.

- [ ] **Step 6: Run Ruff/LSP and commit**

Describe: `test(mpc): exceed branch coverage gate`.
