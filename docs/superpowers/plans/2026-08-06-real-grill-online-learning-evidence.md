# Real-Grill Online Learning Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record trustworthy real-cook evidence, run guarded empty-grill excitation, evaluate the innovation state-space challenger against untouched future outcomes, and permit exact-digest activation only after every confidence and persistence gate passes.

**Architecture:** Extend the existing typed control trace, `FrameObservation`, `OnlineAdaptation`, `InnovationStateSpace`, runner observation queue, and model checkpoint worker. Raw frame evidence remains in the 30-day trace; a separate typed SQLite ledger stores compact completed origins, decisions, timing, and activation lineage. Grey-box owns every command until an atomic manual activation; after activation, state-space generations use the same causal gate and retain grey-box as immediate fallback.

**Tech Stack:** Python 3.11, frozen Pydantic dataclasses, NumPy/SciPy, SQLite, threaded controller runner, pytest, Ruff, React/TypeScript, Rstest, TanStack Query, Jujutsu.

## Global Constraints

- Work directly in the existing checkout and Jujutsu stack. Do not create another worktree and do not use raw Git commands.
- The controller catalog remains `pid`, `pid_sp`, and `mpc`; this plan adds no controller or compatibility alias.
- Grey-box remains the shipped default and sole baseline command owner until successful manual first activation.
- Before activation, state-space must not alter policy load, allocation, pulse scheduling, fan output, safety handling, or fallback output.
- Ordinary cooks use `probe_q == 0.0` exactly. Only explicit calibration mode may add a probe.
- The manipulated variable remains one coupled normalized combustion load `q` in `[0, 1]`; realized `q` reconstructed from allocator-backed auger delivery is the identification input.
- The canonical sample is one completed 20-second framed-pulse interval with a 2-second pulse quantum. All internal temperatures are Celsius.
- Model fitting, forecasting, scoring, persistence, report generation, confidence bootstrapping, and raw pruning stay off the Hold safety/actuation path.
- Raw trace retention remains 30 days. Compact evidence persists until explicit reset, incompatible schema change, failed provenance validation, or intentional lineage retirement.
- Required forecast horizons are 60, 300, 900, 1800, and 3600 seconds: 3, 15, 45, 90, and 180 canonical frames.
- Absolute RMSE limits are 2.8 °C at 60/300/900 seconds and 5.0 °C at 1800/3600 seconds. State alignment error is at most 2.0 °C, maximum pole magnitude defaults to 0.999, and maximum delay defaults to 15 frames.
- Relative confidence uses a deterministic hierarchical block bootstrap with cook/session as the top-level unit, within-cook contiguous blocks at least as long as the scored horizon, a stored seed, 10,000 replicates, and a one-sided 95% upper bound below 1.0.
- State-space refresh p99 must be no greater than 250 ms on target hardware. Workstation timing is diagnostic and cannot satisfy activation readiness.
- Passing gates yields `ready-for-review`; it never changes control. First activation is explicit and exact-digest. Later parameter-generation promotion is automatic only through the same applicable fail-closed gates.
- Real-grill readiness cannot be claimed by simulator tests. Until qualifying hardware evidence exists, report the exact missing gates and keep grey-box active.

---

### Task 1: Canonical Observation and Trace Schema

**Files:**
- Modify: `common/control_trace.py`
- Modify: `controller/linear_mpc/contracts.py`
- Modify: `controller/linear_mpc/trace.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: `tests/unit/common/test_control_trace_schema.py`
- Modify: `tests/unit/mpc/test_linear_learning_trace.py`
- Modify: `tests/unit/runtime/test_hold_control_trace.py`

**Interfaces:**
- Produces: `AmbientSource`, `AmbientUncertainty`, `CalibrationEventType`, and `CalibrationTracePayload` in `common.control_trace`.
- Produces: expanded frozen `FrameObservation` fields for identity, provenance, baseline/probe/allocated/realized load, fan/auger delivery, and calibration classification.
- Produces: trace schema version 4; schema versions below 4 are evidence-incompatible and cannot authorize readiness.
- Consumes: same-revision `AllocationPayload.u_max` and allocator revision; never restores `SessionPayload.u_max`.

- [ ] **Step 1: Start the schema change**

Run:

```bash
jj new
jj desc -m "feat(trace): record canonical model evidence"
```

- [ ] **Step 2: Write failing schema tests**

Add tests that construct and round-trip one ordinary observation and one calibration observation. The calibration case must preserve distinct values for:

```python
assert payload.baseline_combustion_load == 0.35
assert payload.calibration_probe_load == 0.05
assert payload.requested_combustion_load == 0.40
assert payload.allocated_combustion_load == 0.38
assert payload.realized_combustion_load == 0.30
assert payload.requested_auger_duty == 0.19
assert payload.scheduled_on_seconds == 4.0
assert payload.delivered_on_seconds == 3.0
assert payload.realized_auger_duty == 0.15
assert payload.ambient_source is AmbientSource.CONFIGURED
assert payload.ambient_uncertainty is AmbientUncertainty.UNMEASURED
```

Also assert rejection of non-finite loads, `requested != clip(baseline + probe)`, realized load without same-revision allocation provenance, `ambient_source="measured"` without a measured source identifier, out-of-order observation sequences, and `eligible=True` with any rejection reason.

- [ ] **Step 3: Verify the tests fail for missing fields and schema version**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/mpc/test_linear_learning_trace.py \
  tests/unit/runtime/test_hold_control_trace.py
```

Expected: failure because schema v3 lacks the canonical fields and calibration payload.

- [ ] **Step 4: Add immutable schema-v4 payloads**

Advance `TRACE_SCHEMA_VERSION` to 4. Add these enums and the discriminated calibration payload:

```python
class AmbientSource(StrEnum):
    MEASURED = "measured"
    MANUAL = "manual"
    WEATHER = "weather"
    CONFIGURED = "configured"


class AmbientUncertainty(StrEnum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    UNMEASURED = "unmeasured"


class CalibrationEventType(StrEnum):
    START_REQUESTED = "start_requested"
    START_ACCEPTED = "start_accepted"
    START_REJECTED = "start_rejected"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_TIMEOUT = "stage_timeout"
    PROBE_CHANGED = "probe_changed"
    PAUSED = "paused"
    RESUMED = "resumed"
    STOPPED = "stopped"
    SAFETY_ABORTED = "safety_aborted"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class CalibrationTracePayload:
    event: CalibrationEventType
    command_revision: NonNegativeInt
    stage: NonBlankString | None
    intended_probe_load: BoundedSignedLoad
    bounded_probe_load: BoundedSignedLoad
    cumulative_probe_load: FiniteFloat
    eligible_observations: NonNegativeInt
    positive_observations: NonNegativeInt
    negative_observations: NonNegativeInt
    reasons: tuple[NonBlankString, ...]
    payload_type: Literal["calibration"] = "calibration"
```

Use a signed-load validator bounded to `[-1, 1]`. Validate event/reason consistency and finite cumulative probe balance.

- [ ] **Step 5: Expand `ModelObservationPayload` and `FrameObservation`**

Add exact identity and provenance fields: `observation_sequence`, `probe_valid`, `probe_source`, `ambient_source`, `ambient_uncertainty`, `baseline_q`, `probe_q`, `allocated_q`, `scheduled_on_s`, `realized_auger_duty`, `allocator_revision`, `allocation_clamp_reasons`, `calibration_stage`, and `calibration_fit`. Keep `requested_q` as the combined request and `realized_q` as the model input. Make tuples and arrays owned/immutable in `__post_init__`.

Change horizon literals in raw evaluation payloads from `Literal[3, 15]` to `Literal[3, 15, 45, 90, 180]`. Record exact incumbent/challenger digest and precommitted prediction on each origin before a target is available.

- [ ] **Step 6: Populate the contract from completed framed output**

Update `controller.linear_mpc.trace.learning_observations` and Hold frame completion so each observation joins the accepted controller result, allocation, completed framed delivery, actual fan state, probe metadata, and output source by result revision. A missing or mismatched join remains in the trace but is ineligible with an exact reason.

Use the same-revision allocation to normalize delivery:

```python
realized_q = normalized_load_from_auger_duty(
    realized_auger_duty,
    u_max=allocation.u_max,
)
```

Do not derive `u_max` from session state.

- [ ] **Step 7: Verify schema and framed joins**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/mpc/test_linear_learning_trace.py \
  tests/unit/runtime/test_hold_control_trace.py
.venv/bin/ruff check common/control_trace.py controller/linear_mpc/contracts.py \
  controller/linear_mpc/trace.py controller/runtime/modes/hold.py
```

Expected: all pass; ordinary frames serialize `probe_q == 0.0`, and no schema-v3 row can be used for readiness.

- [ ] **Step 8: Finish the change**

Run `jj new` after the focused tests and Ruff pass.

---

### Task 2: Durable Evidence Ledger and Off-Path Persistence

**Files:**
- Create: `common/model_evidence.py`
- Modify: `common/datastore.py`
- Modify: `common/datastore_accessors.py`
- Rename: `controller/runtime/model_checkpoint.py` to `controller/runtime/model_persistence.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: all imports/references returned by LSP for `ModelCheckpointWorker`
- Create: `tests/unit/common/test_model_evidence_store.py`
- Create: `tests/unit/runtime/test_model_persistence.py`
- Modify: `tests/unit/runtime/test_hold_model_persistence.py`

**Interfaces:**
- Produces: `MODEL_EVIDENCE_SCHEMA_VERSION = 1` and frozen discriminated `ModelEvidenceRecord` payloads.
- Produces: `append_model_evidence`, `read_model_evidence`, `commit_model_activation`, `read_model_activation`, `reset_model_evidence`, and `invalidate_model_evidence_schema` datastore APIs.
- Produces: `ModelPersistenceWorker.submit_checkpoint()`, `submit_evidence()`, `commit_activation()`, and `flush_and_stop()`.
- Consumes: schema-v4 trace identities and immutable completed-origin data from Task 1.

- [ ] **Step 1: Start the ledger change**

Run:

```bash
jj desc -m "feat(mpc): persist compact confidence evidence"
```

- [ ] **Step 2: Write failing ledger validation and transaction tests**

Cover append-only forecast origins, duplicate identity rejection, deterministic ordering, raw-trace pruning independence, schema invalidation, corrupt JSON rejection, checkpoint coalescing, bounded evidence queue overflow, and activation transaction rollback. Prove atomic failure with:

```python
with pytest.raises(sqlite3.IntegrityError):
    commit_model_activation(invalid_decision, database_path=db_path)
assert read_model_activation(database_path=db_path) is None
assert read_model_evidence(kind="activation", database_path=db_path) == []
```

- [ ] **Step 3: Verify the new tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/common/test_model_evidence_store.py \
  tests/unit/runtime/test_model_persistence.py \
  tests/unit/runtime/test_hold_model_persistence.py
```

Expected: import failures for the missing evidence types and worker.

- [ ] **Step 4: Define typed compact evidence**

In `common/model_evidence.py`, define an envelope with `schema_version`, `evidence_id`, `kind`, `session_id`, `cook_id`, `timestamp_ms`, `role_generation`, `model_digest`, `provenance_digest`, and a discriminated payload. Provide payloads for session summary, calibration summary, forecast origin, refresh diagnostics, timing distribution, confidence decision, activation, rollback, fallback, recorder gap, and schema invalidation.

A forecast-origin payload must include both precommitted predictions and the later immutable completion:

```python
@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class ForecastOriginEvidence:
    origin_sequence: NonNegativeInt
    origin_time_ms: NonNegativeInt
    completion_time_ms: NonNegativeInt
    horizon_steps: Literal[3, 15, 45, 90, 180]
    incumbent_digest: Digest
    challenger_digest: Digest
    incumbent_prediction_c: FiniteFloat
    challenger_prediction_c: FiniteFloat
    observed_temperature_c: FiniteFloat
    incumbent_error_c: FiniteFloat
    challenger_error_c: FiniteFloat
    temperature_band: NonBlankString
    phase: Literal["heating", "coasting"]
    ambient_source: AmbientSource
    calibration_fit: bool
```

Reject `calibration_fit=True` records when read as validation evidence.

- [ ] **Step 5: Add SQLite ledger and activation-state tables**

Add an append-only `model_evidence` table indexed by session, cook, kind, generation, and digest. Add a `model_activation_state` table containing the exact active snapshot, rollback snapshot, evidence decision ID, controller configuration digest, and role generation. `commit_model_activation` must append the activation decision and replace the singleton activation state in one SQLite transaction.

Deletion is allowed only through explicit reset, schema invalidation, failed provenance validation, or lineage retirement APIs. Raw `prune_control_trace` must not touch either table.

- [ ] **Step 6: Replace the checkpoint worker with one persistence worker**

Use LSP `rename_file` and symbol rename so every reference migrates. Preserve latest-only checkpoint coalescing, add a bounded FIFO for append-only evidence, and serialize activation commits. Submission copies/validates inputs before enqueueing and returns immediately. Queue overflow emits a typed recorder-gap result and blocks confidence; it must not silently discard activation or pretend evidence persisted.

- [ ] **Step 7: Wire one worker into Hold lifecycle**

Construct one `ModelPersistenceWorker` for MPC Hold. Submit ledger batches only from completed runner outcomes, never directly from the safety tick. On teardown, flush the last evidence batch and latest checkpoint exactly once. Recorder construction failure disables learning/probes while leaving normal temperature control available.

- [ ] **Step 8: Verify persistence and nonblocking ownership**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/common/test_model_evidence_store.py \
  tests/unit/runtime/test_model_persistence.py \
  tests/unit/runtime/test_hold_model_persistence.py
.venv/bin/ruff check common/model_evidence.py common/datastore.py \
  common/datastore_accessors.py controller/runtime/model_persistence.py \
  controller/runtime/modes/hold.py
```

Expected: compact evidence survives raw trace pruning; partial activation never changes active state; blocked writes do not block `HoldMode.on_tick()`.

- [ ] **Step 9: Finish the change**

Run `jj new` after the focused tests and Ruff pass.

---

### Task 3: Causal Multi-Horizon Forecast Evidence

**Files:**
- Modify: `controller/linear_mpc/adaptation.py`
- Modify: `controller/mpc.py`
- Modify: `controller/runtime/runner.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: `tests/unit/mpc/test_online_adaptation.py`
- Modify: `tests/unit/mpc/test_online_adaptation_integration.py`
- Modify: `tests/unit/mpc/test_state_space_shadow_integration.py`
- Create: `tests/unit/mpc/test_model_evidence_origins.py`
- Modify: `tests/unit/runtime/test_threaded_runner.py`

**Interfaces:**
- Produces: `ForecastOrigin` and `CompletedForecastOrigin` carrying exact generation, digest, prediction, observation sequence, and continuity.
- Produces: `ObservationOutcomeEnvelope.evidence`, an immutable tuple submitted by Hold to `ModelPersistenceWorker`.
- Consumes: expanded `FrameObservation` and evidence-store types from Tasks 1–2.

- [ ] **Step 1: Start the causal scoring change**

Run:

```bash
jj desc -m "feat(mpc): retain causal multi-horizon evidence"
```

- [ ] **Step 2: Write failing causality tests**

Add tests proving that origins are stored before targets exist, complete at 3/15/45/90/180 frames, retain the original model digests after refresh, cannot complete through a destructive gap, cannot count twice, and cannot use calibration fit rows as validation targets. Include this invariant:

```python
origin = manager.pending_origins[0]
old_prediction = origin.challenger_prediction_c
manager.refresh_challenger(new_model)
assert origin.challenger_prediction_c == old_prediction
assert origin.challenger_digest != manager.challenger_digest
```

- [ ] **Step 3: Verify the causal tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/mpc/test_model_evidence_origins.py \
  tests/unit/mpc/test_online_adaptation.py \
  tests/unit/mpc/test_online_adaptation_integration.py \
  tests/unit/mpc/test_state_space_shadow_integration.py \
  tests/unit/runtime/test_threaded_runner.py
```

Expected: existing 3/15-only origins lack long horizons and durable evidence output.

- [ ] **Step 4: Generalize `OnlineAdaptation` horizons without recomputation**

Set `_HORIZONS = (3, 15, 45, 90, 180)`. Capture both affine forecasts and exact model snapshots/digests at the origin. Complete an origin only when the matching future sequence arrives with uninterrupted eligible continuity. Expire origins on destructive gap, role-generation change, incompatible refresh, queue eviction, or session end.

Keep fit and validation roles distinct: calibration-fit observations may update/refresh a challenger but never complete a validation origin and never enter relative confidence samples.

- [ ] **Step 5: Return immutable evidence through the existing outcome queue**

Add `evidence: tuple[ModelEvidenceRecord, ...]` to `ObservationOutcomeEnvelope`. Freeze it with the same ownership rules as status and model snapshots. The threaded runner remains the only producer; Hold drains and submits it without parsing or recomputing predictions.

- [ ] **Step 6: Emit raw and compact records from one event**

`controller/mpc.py` should create one canonical completed-origin object, serialize it to `ModelEvaluationPayload` for the 30-day trace, and wrap the same values in `ForecastOriginEvidence` for the durable ledger. Do not implement a second scoring path.

- [ ] **Step 7: Verify long-horizon causality and queue behavior**

Run the Step 3 command, then:

```bash
.venv/bin/ruff check controller/linear_mpc/adaptation.py controller/mpc.py \
  controller/runtime/runner.py controller/runtime/modes/hold.py
```

Expected: all tests pass; stale generations and dropped observations produce explicit blocked evidence instead of repaired origins.

- [ ] **Step 8: Finish the change**

Run `jj new`.

---

### Task 4: Guarded Calibration State Machine

**Files:**
- Create: `controller/linear_mpc/calibration.py`
- Create: `tests/unit/mpc/test_calibration_coordinator.py`
- Create: `tests/unit/mpc/test_calibration_simulators.py`

**Interfaces:**
- Produces: frozen `CalibrationConfig`, `CalibrationCommand`, `CalibrationRuntimeContext`, `CalibrationDecision`, `CalibrationProgress`, and `CalibrationEvent`.
- Produces: `CalibrationCoordinator.start()`, `stop()`, `cancel_probe()`, `advance()`, and `snapshot()/from_snapshot()`.
- Consumes: a prediction callback that reports bounded prospective maximum temperature without applying output.

- [ ] **Step 1: Start the calibration-domain change**

Run:

```bash
jj desc -m "feat(mpc): add guarded calibration coordinator"
```

- [ ] **Step 2: Write failing state-machine tests**

Cover every start precondition, 225/325/425 °F band conversion to Celsius, upward transitions, low/middle/high excitation, coast, 30 eligible observations, three realized-load levels, variance `>= 0.001`, six positive and six negative realized probes, rank/coverage progress, continuity, 60-minute stage timeout, operator stop, and snapshot restore.

Parameterize cancellation over lid open, manual mode/output, safety, temperature guard, invalid probe, stale result, skipped/reset frame, discontinuity, unknown actuation, fallback, inadequate headroom, overshoot prediction, stop, and timeout. Every case must return `probe_q == 0.0` immediately.

- [ ] **Step 3: Verify the state-machine tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short tests/unit/mpc/test_calibration_coordinator.py
```

Expected: import failure for `controller.linear_mpc.calibration`.

- [ ] **Step 4: Implement immutable configuration and guards**

Use these defaults:

```python
@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    band_centers_c: tuple[float, ...] = (
        (225.0 - 32.0) * 5.0 / 9.0,
        (325.0 - 32.0) * 5.0 / 9.0,
        (425.0 - 32.0) * 5.0 / 9.0,
    )
    max_probe_q: float = 0.05
    min_stage_observations: int = 30
    min_realized_levels: int = 3
    min_realized_variance: float = 0.001
    min_positive_observations: int = 6
    min_negative_observations: int = 6
    stage_timeout_s: float = 3600.0
```

Validate all finite bounds and require the operator-selected maximum temperature to remain below existing configured safety limits.

- [ ] **Step 5: Implement deterministic balanced excitation**

Generate symmetric signed dwell pairs with frame counts `(2, 3, 5, 4, 3, 2)`. The stored seed changes pair order and initial sign, never the multiset or zero-sum property. Bound each intended probe by allocator headroom, error/rate guard, capability, saturation, and prospective overshoot. Track realized probe contribution and attempt a safe bounded compensating move when clipping creates imbalance. A stage completes only if `abs(sum(probe_q)) <= 0.05`; otherwise it remains incomplete.

The challenger is never an input to probe generation. The prediction callback is the active grey-box prospective path.

- [ ] **Step 6: Implement stage completion and audit events**

Advance only after all observation, level, variance, sign-count, rank/coverage, and continuity gates pass. Emit explicit accepted/rejected start, stage start/completion/timeout, probe change, pause/resume, stop, safety abort, and complete/incomplete events. Timeout never extends itself.

- [ ] **Step 7: Exercise the pure coordinator against both simulators**

Add deterministic tests that feed coordinator decisions into `GrillSim` and `MAKGrillSim` with fixed seeds. Assert the probe bound, cancellation behavior, completed-stage zero mean, temperature ceiling, and that both plants accumulate rank/coverage progress without granting activation readiness.

Run:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/pytest -q --tb=short \
  tests/unit/mpc/test_calibration_coordinator.py \
  tests/unit/mpc/test_calibration_simulators.py
.venv/bin/ruff check controller/linear_mpc/calibration.py
```

- [ ] **Step 8: Finish the change**

Run `jj new`.

---

### Task 5: Calibration Runtime, Commands, and Framed Actuation

**Files:**
- Modify: `common/api_commands.py`
- Modify: `controller/base.py`
- Modify: `controller/mpc.py`
- Modify: `controller/runtime/runner.py`
- Modify: `controller/runtime/modes/hold.py`
- Create: `tests/unit/common/test_mpc_calibration_commands.py`
- Create: `tests/unit/mpc/test_mpc_calibration_runtime.py`
- Create: `tests/unit/runtime/test_hold_calibration.py`
- Modify: `tests/unit/runtime/test_hold_pulse_scheduler.py`
- Modify: `tests/unit/runtime/test_hold_fan_authority.py`

**Interfaces:**
- Produces: `request_calibration(command: CalibrationCommand) -> None` on MPC core and runner protocols.
- Produces: optional `baseline_allocation` and `calibration` fields on `ControllerUpdateResult`.
- Produces: revisioned `set_mpc_calibration` commands for start, pause, resume, stop, and reset-progress actions.
- Consumes: `CalibrationCoordinator` from Task 4 and trace/ledger persistence from Tasks 1–3.

- [ ] **Step 1: Start runtime integration**

Run:

```bash
jj desc -m "feat(mpc): integrate guarded calibration actuation"
```

- [ ] **Step 2: Write failing command and safety tests**

Prove rejected starts leave control unchanged; accepted starts carry exact max-temperature/ambient/empty-grill/pellet confirmations; duplicate command revisions are idempotent; ordinary cooks always report zero probe; and stop returns to baseline grey-box allocation.

For each lid/manual/safety/stale/reset guard, assert the current framed scheduler does not deliver newly requested probe on-time after the cancellation boundary and retains an attributable raw/compact event.

- [ ] **Step 3: Verify the tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/common/test_mpc_calibration_commands.py \
  tests/unit/mpc/test_mpc_calibration_runtime.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_fan_authority.py
```

Expected: missing command, runner method, and baseline allocation metadata.

- [ ] **Step 4: Add revisioned calibration commands**

Validate a command payload with `action`, monotonic `revision`, internal-Celsius maximum temperature, ambient value/source, `empty_grill_confirmed`, and `pellets_confirmed`. Start requires MPC Hold with grey-box active. Store only the latest revision in control state; Hold consumes each revision once.

- [ ] **Step 5: Integrate baseline and overlay inside MPC**

Compute `q_g` through the existing grey-box path. Ask `CalibrationCoordinator` for bounded `q_p`, then compute:

```python
requested_q = float(np.clip(q_g + q_p, 0.0, 1.0))
allocation_kwargs = {
    "u_max": self.u_max,
    "fan_min_pct": self.cfg["fan_min_pct"],
    "fan_max_pct": self.cfg["fan_max_pct"],
    "enable_fan": bool(self.cfg["enable_fan_input"]),
}
baseline_allocation = allocate(q_g, **allocation_kwargs)
allocation = allocate(requested_q, **allocation_kwargs)
```

Return both allocations plus the immutable calibration decision. The state-space challenger may observe the result but must not influence `q_g`, `q_p`, or either allocation.

- [ ] **Step 6: Make Hold own same-boundary cancellation**

Before applying a fresh result, Hold checks current lid/manual/safety/stale/reset and calibration command state. When a probe is invalidated, reset remaining probe frame credit and apply the result's `baseline_allocation` through the normal framed scheduler. Stronger existing safety behavior still wins. Never replay a discarded frame.

Record baseline, probe, combined request, both allocation identities, scheduled/delivered output, and cancellation reason by result revision.

- [ ] **Step 7: Verify runtime safety and attribution**

Run the Step 3 tests and:

```bash
.venv/bin/ruff check common/api_commands.py controller/base.py controller/mpc.py \
  controller/runtime/runner.py controller/runtime/modes/hold.py
```

Expected: all pass; normal cooks have byte-for-byte equivalent output decisions to the pre-calibration grey-box path.

- [ ] **Step 8: Finish the change**

Run `jj new`.

---

### Task 6: Deterministic Confidence Engine

**Files:**
- Create: `controller/linear_mpc/confidence.py`
- Create: `tests/unit/mpc/test_model_confidence.py`
- Create: `tests/unit/mpc/test_confidence_bootstrap.py`
- Modify: `controller/linear_mpc/adaptation.py`
- Modify: `tests/unit/mpc/test_online_adaptation.py`

**Interfaces:**
- Produces: frozen `ConfidenceConfig`, `GateResult`, `BootstrapInterval`, `ConfidenceReport`, and `ConfidenceStatus`.
- Produces: `evaluate_confidence(evidence, *, activation_state, target_timing, config) -> ConfidenceReport`.
- Consumes: compact ledger records only; raw trace rows are not an authority source.

- [ ] **Step 1: Start confidence evaluation**

Run:

```bash
jj desc -m "feat(mpc): evaluate fail-closed model confidence"
```

- [ ] **Step 2: Write one failing test per independent gate**

Create fixtures where exactly one of identifiability, pole, gain, delay, covariance, alignment, snapshot round-trip, absolute RMSE, signed bias, band error, braking, relative RMSE, bootstrap interval, repeated windows, generation continuity, target timing, persistence, provenance, or prospective construction fails. Assert each produces its exact blocking reason and never `ready-for-review`.

Also prove duplicated frames and one cook cannot manufacture cross-session confidence.

- [ ] **Step 3: Write failing deterministic-bootstrap tests**

Use two synthetic cooks with correlated origin blocks. Assert the same evidence and seed produce byte-identical interval output; changing row order does not change it; treating rows as independent produces a different result and is not used.

- [ ] **Step 4: Verify confidence tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/mpc/test_model_confidence.py \
  tests/unit/mpc/test_confidence_bootstrap.py
```

Expected: import failure for `controller.linear_mpc.confidence`.

- [ ] **Step 5: Implement grouped scoring and hierarchical bootstrap**

Group by cook/session, horizon, temperature band, heating/coasting phase, ambient provenance, and model generation. For each of 10,000 replicates:

1. resample cook IDs with replacement;
2. within each selected cook, resample contiguous origin blocks of at least `horizon_steps` frames until the original group size is reached;
3. compute challenger RMSE divided by incumbent RMSE;
4. store the finite ratio.

The one-sided upper bound is the deterministic 95th percentile. Fewer than two independent cook groups reports unavailable confidence rather than guessing a variance.

- [ ] **Step 6: Implement all fail-closed gates**

Use the exact thresholds in Global Constraints and require low/middle/high/coast calibration completeness, finite full-rank refresh evidence, untouched validation rows, no single cook supplying all effective weight, repeated sequential wins, target-hardware timing provenance, atomic persistence evidence, and successful production-path prospective construction.

Map status to `collecting`, `insufficient-excitation`, `fitting`, `evaluating`, `ready-for-review`, `active`, `fallback`, or `schema-invalidated`. Unsupported horizons remain explicit blockers.

- [ ] **Step 7: Verify confidence behavior and runtime**

Run the Step 4 tests and:

```bash
.venv/bin/ruff check controller/linear_mpc/confidence.py \
  controller/linear_mpc/adaptation.py
```

Expected: all gates block independently; a fully qualifying fixture yields `ready-for-review` without changing active ownership.

- [ ] **Step 8: Finish the change**

Run `jj new`.

---

### Task 7: Confidence Reports and Operator Surface

**Files:**
- Create: `controller/linear_mpc/report.py`
- Modify: `blueprints/api/routes.py`
- Create: `web-react/src/helpers/modelEvidence/types.ts`
- Create: `web-react/src/helpers/modelEvidence/modelEvidenceApi.ts`
- Create: `web-react/src/components/dashboard/MpcLearningPanel.tsx`
- Modify: `web-react/src/components/dashboard/ControlButtons.tsx`
- Modify: `web-react/src/components/dashboard/Dashboard.tsx`
- Create: `web-react/tests/unit/components/dashboard/MpcLearningPanel.test.tsx`
- Create: `tests/web/test_api_model_evidence.py`
- Create: `tests/unit/mpc/test_model_evidence_report.py`

**Interfaces:**
- Produces: `build_evidence_artifact(report, records) -> bytes`, canonical UTF-8 JSON with sorted keys and no model-changing authority.
- Produces: read-only `GET /api/model-evidence/report` and `GET /api/model-evidence/artifact`.
- Produces: operator calibration actions routed through the revisioned command from Task 5.
- Consumes: `ConfidenceReport` from Task 6.

- [ ] **Step 1: Start the reporting change**

Run:

```bash
jj desc -m "feat(ui): expose MPC learning evidence"
```

- [ ] **Step 2: Write failing report/API/component tests**

Assert the report includes active/default and candidate kinds/digests, calibration progress, eligible/ineligible counts, identifiability, every horizon/band score and confidence interval, physical/alignment/timing results, blockers, activation/rollback history, and ambient limitation.

Frontend tests must show exact blockers, require empty-grill and pellet confirmations before start, convert the operator maximum temperature at the UI boundary, show progress/timeout/incomplete states, expose stop immediately, and never render an activate action unless status is `ready-for-review`.

- [ ] **Step 3: Verify tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/web/test_api_model_evidence.py \
  tests/unit/mpc/test_model_evidence_report.py
cd web-react && bun run test tests/unit/components/dashboard/MpcLearningPanel.test.tsx
```

Expected: missing report routes and component.

- [ ] **Step 4: Build deterministic report artifacts**

Serialize only validated ledger records used by the decision. Include schema, provenance digest, bootstrap seed/replicate count, decision ID, and evidence IDs. Sort groups and keys, reject missing references, and never load an artifact into model state.

- [ ] **Step 5: Add read-only report endpoints and typed frontend client**

Return the current report even when evidence is empty. Empty state must be `collecting` with exact missing gates. Artifact generation failure returns an error and cannot mutate readiness.

- [ ] **Step 6: Add the MPC learning panel**

Render the panel only while MPC is selected. Provide explicit start/pause/resume/stop controls, maximum temperature, confirmations, current stage/probe, progress gates, candidate digest, score tables, runtime provenance, and blockers. Keep first activation disabled here until Task 8 supplies the exact-digest action.

- [ ] **Step 7: Verify API and responsive UI behavior**

Run the Step 3 tests, then the existing frontend typecheck/build command from `web-react/package.json`. Launch the app, open desktop and 390-pixel mobile dashboard views, exercise start rejection and stop, and verify controls remain reachable without horizontal overflow.

- [ ] **Step 8: Finish the change**

Run `jj new` after API tests, component tests, typecheck, build, and browser smoke pass.

---

### Task 8: Exact-Digest Manual Activation and Fallback

**Files:**
- Create: `controller/linear_mpc/activation.py`
- Modify: `controller/mpc.py`
- Modify: `controller/runtime/runner.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: `blueprints/api/routes.py`
- Modify: `web-react/src/helpers/modelEvidence/modelEvidenceApi.ts`
- Modify: `web-react/src/components/dashboard/MpcLearningPanel.tsx`
- Create: `tests/unit/mpc/test_model_activation.py`
- Create: `tests/unit/mpc/test_state_space_active_policy.py`
- Modify: `tests/unit/runtime/test_hold_model_persistence.py`
- Modify: `web-react/tests/unit/components/dashboard/MpcLearningPanel.test.tsx`

**Interfaces:**
- Produces: `ActivationRequest`, `ActivationDecision`, `ActivationState`, and `ActivationManager.prepare()/commit()/fallback()/rollback()`.
- Produces: `POST /api/model-evidence/activate` requiring exact candidate digest and confidence decision ID.
- Produces: `POST /api/model-evidence/rollback` requiring an explicit reason.
- Consumes: atomic activation persistence from Task 2 and `ready-for-review` report from Task 6.

- [ ] **Step 1: Start activation work**

Run:

```bash
jj desc -m "feat(mpc): gate manual state-space activation"
```

- [ ] **Step 2: Write failing activation transaction tests**

Cover stale decision, changed digest, changed controller configuration, incompatible schema/provenance, candidate reconstruction failure, prospective solve failure, persistence failure, successful activation, generation rollover, pending-origin invalidation, grey-box fallback retention, explicit rollback, and restart restore.

The central invariant is:

```python
assert manager.active_kind == "grey_box"
assert manager.prepare(stale_request).accepted is False
assert manager.active_kind == "grey_box"
```

- [ ] **Step 3: Verify activation tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/mpc/test_model_activation.py \
  tests/unit/mpc/test_state_space_active_policy.py \
  tests/unit/runtime/test_hold_model_persistence.py
```

Expected: missing activation manager and state-space ownership path.

- [ ] **Step 4: Implement prepare without control effects**

`prepare()` loads the exact ready decision and candidate snapshot from the ledger, validates schema/provenance/configuration, reconstructs `InnovationStateSpace` through the production loader, aligns current state, and runs one bounded prospective solve. It returns an owned prepared object but cannot change controller state.

- [ ] **Step 5: Commit persistence before ownership**

The API thread submits the activation transaction to `ModelPersistenceWorker.commit_activation()` and waits there, never on Hold's tick. Only a successful transaction allows `ActivationManager.commit()` to swap prediction ownership, increment role generation, and invalidate incompatible origins. Persist the grey-box rollback snapshot in the same transaction.

- [ ] **Step 6: Add active state-space policy with immediate fallback**

Use the existing linear MPC solve path with the activated state-space affine prediction. Invalid state/prediction, solve failure, repeated policy exception, stale/deadline threshold, restore failure, residual degradation, or explicit rollback selects the last safe state-space snapshot or grey-box immediately. Record failed digest, generation, last safe command, fallback kind, and reason. Never auto-reenable the failed generation.

- [ ] **Step 7: Add exact-digest operator confirmation**

Enable activation only for `ready-for-review`. Display the digest and decision ID, require explicit confirmation, send both values, and refresh the report after the response. Failure leaves the UI/report on grey-box with the exact rejection.

- [ ] **Step 8: Verify activation and fallback end to end**

Run Steps 2–3 tests plus the panel test. Exercise activation failure and success through the API against a temporary SQLite database. Assert no catalog/default setting changed and restart reconstructs the same active/rollback generations.

- [ ] **Step 9: Finish the change**

Run `jj new`.

---

### Task 9: Confidence-Gated State-Space Generation Adaptation

**Files:**
- Modify: `controller/linear_mpc/adaptation.py`
- Modify: `controller/linear_mpc/confidence.py`
- Modify: `controller/linear_mpc/activation.py`
- Modify: `controller/mpc.py`
- Modify: `tests/unit/mpc/test_state_space_online_compare.py`
- Modify: `tests/unit/mpc/test_innovation_state_space.py`
- Create: `tests/unit/mpc/test_active_state_space_adaptation.py`
- Modify: `tests/unit/mpc/test_mpc_model_snapshot.py`

**Interfaces:**
- Produces: active state-space incumbent plus separately refreshed state-space challenger after first activation.
- Produces: automatic parameter-generation promotion through the existing prospective commit/rollback lifecycle.
- Consumes: all confidence, timing, persistence, and exact-generation evidence from Tasks 2–8.

- [ ] **Step 1: Start post-activation adaptation**

Run:

```bash
jj desc -m "feat(mpc): gate active state-space adaptation"
```

- [ ] **Step 2: Write failing generation-isolation tests**

Prove a refreshed challenger starts with zero consecutive wins, pre-refresh origins cannot complete for it, calibration fit rows cannot validate it, a timing/persistence/physical failure blocks it, and a complete qualifying decision promotes atomically. Prove a model structure/schema change remains manual even when parameter scores win.

- [ ] **Step 3: Verify tests fail**

Run:

```bash
.venv/bin/pytest -q --tb=short \
  tests/unit/mpc/test_active_state_space_adaptation.py \
  tests/unit/mpc/test_state_space_online_compare.py \
  tests/unit/mpc/test_innovation_state_space.py \
  tests/unit/mpc/test_mpc_model_snapshot.py
```

Expected: current coordinator does not enforce the durable post-activation confidence transaction.

- [ ] **Step 4: Switch challenger construction after activation**

When `active_kind == "state_space"`, build the incumbent from the exact active snapshot and a separately refreshed `InnovationStateSpace` challenger. Preserve eligible frame delivery to both; only the challenger mutates parameters before promotion.

- [ ] **Step 5: Gate promotion with complete current-generation evidence**

Require current digest/generation causal origins, physical validity, relative/absolute confidence, alignment, target timing, prospective construction, and successful atomic persistence. On success, promote the exact challenger snapshot, increment role generation, retain the prior active snapshot for rollback, and reset all generation-specific wins/origins.

- [ ] **Step 6: Preserve failure evidence and fallback lineage**

A rejected refresh or promotion remains in the ledger with exact reasons. A failed active generation cannot reenter without a fresh confidence decision. Snapshot/restore must preserve active, challenger, rollback, decision, and role generations exactly.

- [ ] **Step 7: Verify adaptation and snapshot round-trip**

Run the Step 3 tests and:

```bash
.venv/bin/ruff check controller/linear_mpc/adaptation.py \
  controller/linear_mpc/confidence.py controller/linear_mpc/activation.py \
  controller/mpc.py
```

Expected: all pass; no parameter generation promotes on stale or incomplete evidence.

- [ ] **Step 8: Finish the change**

Run `jj new`.

---

### Task 10: End-to-End Acceptance and Rollout Boundary

**Files:**
- Create: `tests/integration/test_mpc_real_grill_evidence.py`
- Modify: `tests/unit/mpc/test_state_space_refresh_diagnostics.py`
- Modify: `tests/unit/mpc/test_model_confidence.py`
- Modify: `tests/unit/runtime/test_hold_calibration.py`
- Modify: `web-react/tests/e2e/dashboard-panel.spec.ts`
- Modify: `docs/superpowers/specs/2026-08-06-real-grill-online-learning-evidence-design.md` only if the implemented public contract differs; otherwise leave it unchanged

**Interfaces:**
- Verifies the complete public behavior; produces no new runtime abstraction.
- Consumes every interface from Tasks 1–9.

- [ ] **Step 1: Start the acceptance change**

Run:

```bash
jj desc -m "test(mpc): prove real-grill evidence lifecycle"
```

- [ ] **Step 2: Write the deterministic end-to-end lifecycle test**

For fixed seeds on both `GrillSim` and `MAKGrillSim`, exercise:

1. ordinary MPC Hold with `probe_q == 0.0` and grey-box ownership;
2. guarded empty-grill calibration with low/middle/high/coast evidence;
3. calibration fit that cannot count as validation;
4. later ordinary cooks completing untouched future origins;
5. compact ledger survival after raw trace pruning;
6. `ready-for-review` without activation;
7. stale-digest activation rejection;
8. exact-digest successful activation;
9. qualifying state-space parameter-generation promotion;
10. active failure and grey-box rollback without frame replay.

Assert that simulated success still reports the target-hardware timing gate as missing until target-provenance timing evidence is inserted.

- [ ] **Step 3: Add failure-path matrix coverage**

Inject queue eviction, recorder gap, disk write failure, partial activation transaction, restart, schema invalidation, lid opening, manual takeover, stale result, scheduler reset, bad ambient provenance, rank deficiency, unsupported horizon, covariance failure, and refresh timeout. Every case must leave temperature control on the existing safe path and record a precise blocker.

- [ ] **Step 4: Run the integration and UI smoke scenarios**

Run:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
.venv/bin/pytest -q --tb=short tests/integration/test_mpc_real_grill_evidence.py
```

Launch PiFire, enter MPC Hold, start and stop calibration through the UI, inspect the report, attempt a stale activation, then activate the exact ready digest in the deterministic fixture. Observe framed auger/fan status, evidence status, and fallback result in the running application.

- [ ] **Step 5: Run focused contract suites**

Run all tests added or modified by Tasks 1–10, then the existing MPC, runtime Hold, datastore, API, and frontend suites. Use single-threaded BLAS for MPC suites. Fix only failures caused by this plan; do not weaken existing safety assertions.

- [ ] **Step 6: Run repository quality gates**

Run the repository's canonical Ruff format check, Ruff lint, Python type check, full Python tests, frontend tests/typecheck/production build, and existing end-to-end suite. Capture exact pass/fail counts and target-hardware timing provenance in the evidence report.

- [ ] **Step 7: Perform post-smoke cleanup**

After the smoke test proves the lifecycle, remove obsolete experiment-only aliases, stale schema readers, duplicate scoring helpers, unused payload fields, and temporary fixtures introduced during implementation. Do not add compatibility shims for schema versions that cannot authorize the new model.

Re-run Steps 4–6 after cleanup.

- [ ] **Step 8: Confirm the rollout boundary**

Verify the shipped/default status remains grey-box unless an exact qualifying activation transaction exists. Simulator evidence may prove software behavior but must not set real-grill readiness. A real grill must still supply qualifying recorded hardware evidence for every Section 9–11 gate.

- [ ] **Step 9: Finish the change**

Run `jj new` only after integration, browser smoke, quality gates, and cleanup reruns pass.
