# Hold Mode Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the 2,934-line `HoldMode` to explicit safety/tick orchestration backed by framed-pulse, trace-session, and learning-lifecycle collaborators, with every final module above 90% branch coverage.

**Architecture:** `FramedPulseRuntime` owns frame transitions and applied-output feedback. `ControlTraceSession` owns trace identity/records/buffering. `HoldLearningRuntime` owns observation reconciliation, evidence, activation restore/events, checkpoints/refit, and calibration evidence using the MPC/runtime contracts from prior slices. `HoldMode` retains ordered hardware and safety orchestration only.

**Tech Stack:** Python 3.14, dataclasses, threaded controller runner, pytest/pytest-cov, Acados Hold smoke, Ruff, Pyright/LSP, Jujutsu.

## Global Constraints

- Depends on Slice A's shared observation buffer, Slice D's typed persistence protocols, and Slice E's final MPC activation/learning/calibration contracts.
- Preserve frame latching, pulse timing, actual-output accounting, observation sequence/generation, safety/manual/lid resets, trace ordering, model evidence identity, activation restore, checkpoint/refit, and teardown close order.
- `HoldMode` remains the `Mode.HOLD` registry class. Existing constructor/setup/tick/status/teardown external behavior does not change.
- Moved private methods are deleted; no forwarding methods remain on `HoldMode`.
- Every final `framed_pulse.py`, `control_trace_session.py`, `hold_learning.py`, and `hold.py` file must have strictly greater than 90% branch coverage.

## Final File Map

- `controller/runtime/framed_pulse.py`: scheduler setup/latch/advance/reset, completed frame observation, feedback/applied output.
- `controller/runtime/control_trace_session.py`: session identity, record construction, pending model events, allocation/update/safety/model/applied interval records, close.
- `controller/runtime/modes/hold_learning.py`: observation queue/reconcile, evidence persistence, calibration frame evidence, activation reconciliation/events, model restore/checkpoint/refit/teardown.
- `controller/runtime/modes/hold.py`: setup, tick ordering, hardware writes, safety/manual hooks, status composition, teardown orchestration.

---

### Task 1: Freeze Hold Ordering and Ownership Contracts

**Files:**
- Modify: `tests/unit/runtime/test_hold_pulse_scheduler.py`
- Modify: `tests/unit/runtime/test_hold_applied_output.py`
- Modify: `tests/unit/runtime/test_hold_control_trace.py`
- Modify: `tests/unit/runtime/test_hold_model_persistence.py`
- Modify: `tests/unit/runtime/test_hold_refit_trigger.py`
- Modify: `tests/unit/runtime/test_hold_calibration.py`
- Add: `tests/unit/runtime/test_hold_orchestration.py`

**Interfaces:**
- Produces a behavioral order contract for final collaborators.

- [ ] **Step 1: Use LSP symbols/references on `HoldMode` and every candidate moved method**

Confirm production construction remains only the mode registry and smoke tool. Map tests that call private methods so they can move to collaborator tests instead of forcing aliases.

- [ ] **Step 2: Add ordered event tests**

Record observable callbacks for normal tick, frame boundary, manual takeover/release, lid opening, safety inhibit, controller reconfigure, stale result, activation event, and teardown. Assert relative order, especially output off/reset before trace close/archive/refit/resource close.

- [ ] **Step 3: Add resource ownership tests**

Assert scheduler, runner, persistence worker, trace recorder, active/candidate pair, and retained refit core each close/flush once across success and exception paths.

- [ ] **Step 4: Run Hold baseline**

Run all `test_hold_*.py` and `test_threaded_runner.py`; record results.

- [ ] **Step 5: Commit tests**

Describe: `test(hold): pin orchestration and ownership order`.

---

### Task 2: Extract `FramedPulseRuntime`

**Files:**
- Create: `controller/runtime/framed_pulse.py`
- Create: `tests/unit/runtime/test_framed_pulse_runtime.py`
- Modify: `controller/runtime/modes/hold.py:154-1273,1733-1868`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True, slots=True)
class FramedPulseResult:
    applied: AppliedOutput | None
    completed_observation: FrameObservation | None
    completed_frame: object | None
    trace_event: object | None

class FramedPulseRuntime:
    def configure(self, actuation_mode, controller) -> None: ...
    def latch(self, controller_result, *, now: float) -> None: ...
    def advance(self, *, now, actual_auger_on, temperature, inhibit) -> FramedPulseResult: ...
    def reset(self, *, reason, now, temperature, inhibit, report_feedback=True) -> FramedPulseResult: ...
    def report_feedback(...) -> AppliedOutput: ...
```

Use existing concrete frame/decision types instead of `object` in implementation after LSP resolves them.

- [ ] **Step 1: Write failing runtime tests**

Cover scheduler absent/present, frame latch, transition timing, maximum duty, fan authority, continuous/framed source, realized duty, inverse combustion load, sample completeness, calibration stamps, duplicate frame suppression, reset with/without feedback, and stale/manual/lid/safety dispositions.

- [ ] **Step 2: Move pulse state and methods together**

State includes scheduler, frame role generation, last frame key, last temperature, and delivery metrics. Inject clocks/callbacks; do not reach through `HoldMode.state`.

- [ ] **Step 3: Integrate minimally into Hold**

Replace moved bodies with direct runtime calls in setup/tick/safety/manual/teardown. Delete old private methods immediately; update tests to call runtime for unit behavior.

- [ ] **Step 4: Run pulse/applied-output tests and >90% gate**

Expected: `framed_pulse.py` >90% and existing Hold pulse tests pass.

- [ ] **Step 5: Commit**

Describe: `refactor(hold): extract framed pulse runtime`.

---

### Task 3: Extract `ControlTraceSession`

**Files:**
- Create: `controller/runtime/control_trace_session.py`
- Create: `tests/unit/runtime/test_control_trace_session.py`
- Modify: `controller/runtime/modes/hold.py:124-133,1274-1803`

**Interfaces:**
- Produces a session owner with `ensure_open`, `record_update`, `record_safety`, `record_model`, `record_applied_interval`, `queue_model_event`, `flush_model_events`, `checkpoint_model`, and `close`.
- Inputs are immutable typed payloads/results; the session does not inspect `HoldMode` or hardware.

- [ ] **Step 1: Write trace session tests**

Cover session/cook identity creation, settings flattening, model snapshot/provenance authority, warning state, pending event flush order, recorder exceptions, update/allocation/calibration payloads, safety/model records, applied interval boundaries, duplicate close, and close after partial setup.

- [ ] **Step 2: Move trace state and payload construction**

Keep wire schemas/event order exact. Reuse `ControlTraceRecorder`; do not move persistence SQL into the session.

- [ ] **Step 3: Integrate with Hold and framed pulse results**

Hold passes completed immutable pulse/update values to the session. Trace session never reads runner private fields.

- [ ] **Step 4: Run trace suites and >90% gate**

Include replay/contract tests where trace bytes are validated.

- [ ] **Step 5: Commit**

Describe: `refactor(hold): extract control trace session`.

---

### Task 4: Make Hold Tick a Visible Orchestration Pipeline

**Files:**
- Modify: `controller/runtime/modes/hold.py:1871-2644`
- Modify: `tests/unit/runtime/test_hold_orchestration.py`

**Interfaces:**
- `on_tick` follows named phases: adopt configuration → publish safety ceiling → consume calibration command → obtain runner result → decide safety/manual/lid inhibit → advance/reset pulse → command hardware → trace/reconcile.

- [ ] **Step 1: Extract only small pure decision helpers needed to name phases**

Do not create a generic event bus. Keep hardware commands in Hold.

- [ ] **Step 2: Replace direct pulse/trace state access with collaborator results**

Each phase receives explicit values. Preserve all early-return and exception safety behavior.

- [ ] **Step 3: Run ordered orchestration tests**

Expected: Task 1 event sequences unchanged.

- [ ] **Step 4: Commit**

Describe: `refactor(hold): expose the tick orchestration pipeline`.

---

### Task 5: Extract Observation and Evidence Reconciliation

**Files:**
- Create: `controller/runtime/modes/hold_learning.py`
- Create: `tests/unit/runtime/test_hold_learning_runtime.py`
- Modify: `controller/runtime/modes/hold.py:411-940,1347-1418`

**Interfaces:**
- Produces `HoldLearningRuntime.submit_completed_observation`, `reconcile_outcomes`, `record_gap`, `bind_generation`, `retire_generation`, and `persist_evidence`.
- Consumes runner's public `ObservationSubmission`/`ObservationOutcomeDrain`, Slice A buffer semantics, persistence worker protocol, and trace-session callbacks.

- [ ] **Step 1: Write reconciliation matrix tests**

Cover accepted/evicted/terminal-dropped observations, capacity overflow, missing identity, generation mismatch, stale/retired context, evidence batches, confidence records, persistence refusal/failure, calibration evidence, and trace gap publication.

- [ ] **Step 2: Move pending-observation and evidence state**

Keep sequence/generation identity and ordering exact. No direct deque duplication of runner outcomes beyond the existing bounded pending-observation ownership.

- [ ] **Step 3: Integrate framed pulse completion with learning runtime**

Hold passes `FramedPulseResult.completed_observation`; learning returns trace/evidence effects for the trace session.

- [ ] **Step 4: Run learning/trace/model persistence tests and intermediate >90% gate**

Expected: `hold_learning.py` >90% for implemented branches.

- [ ] **Step 5: Commit**

Describe: `refactor(hold): extract learning evidence reconciliation`.

---

### Task 6: Move Activation Restore, Checkpoint, and Refit Lifecycle

**Files:**
- Modify: `controller/runtime/modes/hold_learning.py`
- Modify: `tests/unit/runtime/test_hold_learning_runtime.py`
- Modify: `controller/runtime/modes/hold.py:2645-2934`
- Read/consume: `controller/runtime/model_lifecycle.py`

**Interfaces:**
- Adds `restore_model`, `reconcile_activation`, `drain_activation_events`, `status_fragment`, `refit_once`, `publish_final_checkpoint_once`, and `finish_teardown` to `HoldLearningRuntime`.
- Consumes the exact `ModelLifecycleRunner` protocol fixed in the master plan and landed by Slice E Task 5; no private `controller.mpc` imports or optional `getattr` lifecycle dispatch remains.

- [ ] **Step 1: Write lifecycle tests**

Cover absent/valid/invalid checkpoint, activation identity changes, prepared/active/aborted restore, schema invalidation, event persistence, refit disabled/insufficient/rejected/accepted/failure, checkpoint refusal, persistence flush timeout, and retained core close.

- [ ] **Step 2: Move lifecycle state and methods**

State includes model store, persistence worker, activation identity/evidence high-water, final refit/checkpoint flags/outcomes. Keep operator authority and evidence ordering.

- [ ] **Step 3: Integrate setup/status/teardown**

Hold delegates lifecycle work but retains actuator-off and runner-stop ordering. Status merges the collaborator's typed fragment.

- [ ] **Step 4: Run model activation/refit/persistence suites and >90% gate**

Expected: lifecycle branches and failure cleanup covered.

- [ ] **Step 5: Commit**

Describe: `refactor(hold): extract model lifecycle orchestration`.

---

### Task 7: Finish Calibration and Safety Handoff

**Files:**
- Modify: `controller/runtime/modes/hold.py:1986-2287,2580-2644`
- Modify: `controller/runtime/modes/hold_learning.py`
- Modify: `tests/unit/runtime/test_hold_calibration.py`
- Modify: `tests/unit/runtime/test_hold_fan_authority.py`
- Modify: `tests/unit/runtime/test_hold_controller_advisories.py`

**Interfaces:**
- Hold owns command admission from current settings/control and safety/manual events.
- MPC calibration runtime owns calibration state; Hold learning owns persisted calibration evidence; framed pulse carries exact frame calibration stamps.

- [ ] **Step 1: Remove duplicated calibration projection/state from Hold**

Use the public MPC calibration status/result. Hold keeps only command high-water needed to prevent duplicate control consumption if that ownership cannot move to control persistence.

- [ ] **Step 2: Route safety/manual/lid cancellation once**

One cancellation decision feeds pulse reset, MPC calibration cancellation, evidence, trace, and hardware output. Preserve reason strings.

- [ ] **Step 3: Run calibration/fan/safety suites**

Expected: no duplicated cancellation or missing frame evidence.

- [ ] **Step 4: Commit**

Describe: `refactor(hold): unify calibration and safety handoff`.

---

### Task 8: Reduce `HoldMode` and Delete Moved Paths

**Files:**
- Rewrite/reduce: `controller/runtime/modes/hold.py`
- Modify tests that instantiate private state directly

**Interfaces:**
- Final `HoldMode` contains constructor/composition, setup, setup_safety, `on_tick`, manual/safety hooks, status composition, and teardown.
- All detailed pulse, trace, observation, activation, checkpoint, and refit behavior lives in collaborators.

- [ ] **Step 1: Delete moved fields and private methods**

Use LSP references before deletion. Tests move to collaborator public APIs; do not add private aliases for tests.

- [ ] **Step 2: Pass explicit dependencies at collaborator construction**

No collaborator stores `HoldMode`, `ctx` as `object`, or mutable `WorkCycleState` when a narrow callback/value suffices.

- [ ] **Step 3: Re-run Task 1 ordering/ownership contracts**

Expected: exact relative event order.

- [ ] **Step 4: Run LSP symbols**

Confirm final Hold class is an orchestration-sized unit and no deleted private method has external references.

- [ ] **Step 5: Commit**

Describe: `refactor(hold): reduce HoldMode to orchestration`.

---

### Task 9: Enforce >90% Branch Coverage and Smoke the Real Path

**Files:**
- Modify behavioral tests only for meaningful uncovered branches.

- [ ] **Step 1: Run aggregate Hold coverage**

Measure `framed_pulse.py`, `control_trace_session.py`, `hold_learning.py`, and final `hold.py` together with all Hold/threaded runner tests.

- [ ] **Step 2: Enforce strict per-file gate**

Use `scripts/check_branch_coverage.py --minimum 90`. Every module must exceed 90.0%.

- [ ] **Step 3: Close behavior gaps**

Prioritize reset/inhibit branches, trace recorder failure, observation drop/generation branches, activation restore/compensation, refit/checkpoint failure, safety/manual/lid transitions, partial setup, and teardown exceptions. Do not use coverage pragmas.

- [ ] **Step 4: Run full focused runtime/MPC integration**

Run all `test_hold_*.py`, `test_threaded_runner.py`, MPC calibration/activation/public contract tests, and control trace replay tests.

- [ ] **Step 5: Run the real framed Hold smoke**

Run `uv run python tools/smoke_acados_hold.py`. Require native controller construction, framed update, applied-output feedback, trace/evidence handoff, and clean teardown.

- [ ] **Step 6: Run Ruff/LSP and commit**

Describe: `test(hold): exceed branch coverage gate`.
