# Cumulative MPC Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-Hold volatile MPC learning with a persistent segmented fit corpus, exact Smoke-to-Hold state warm-up, and one automatic causal activation path for passive and calibration candidates.

**Architecture:** A process-owned SQLite corpus stores independently initialized trajectory segments and survives cooks and promotions. A below-mode delivery journal records exact physical input; Smoke supplies pre-roll, Hold supplies scored observations, and one candidate parameter set is fitted across all compatible segments. A separate durable challenger state owns generation-specific causal evaluation and activates only after two complete multi-horizon wins.

**Tech Stack:** Python 3.14, Pydantic, SQLite/WAL, NumPy/SciPy, threaded/spawned controller workers, React/TypeScript generated contracts, pytest/Playwright.

**Spec:** `docs/superpowers/specs/2026-08-27-cumulative-mpc-learning-design.md`

## Global Constraints

- Observation cadence remains exactly 20 seconds.
- Retain at most 8,640 scored Hold observations across compatible cooks/promotions.
- Retain at most 180 pre-roll frames per segment and 8,640 pre-roll frames globally.
- Fit one candidate parameter set over independently initialized segments; never fit per-segment models or concatenate a physical gap.
- Smoke temperatures never enter residuals, identifiability, RMSE, sample counts, or causal validation.
- Actual delivered actuator state is authoritative; requested duty never fills unknown delivery.
- Unknown/manual/lid/safety/reset/gap intervals finalize a segment and fail learning closed without changing control.
- Promotions/rollbacks retain compatible fit data but reset incumbent-bound causal evaluation.
- Passive and operator-calibration candidates use `ActivationPolicy.CAUSAL_AUTO` and identical 3/15/45/90/180-frame, two-win gates; operator origin adds calibration completeness only.
- Stop/cook end never directly adopts a fitted model or mints unblocked confidence.
- No candidate output before durable frame-boundary `PREPARED -> ACTIVE`.
- Pending forecast origins never survive Stop/restart; complete wins survive only under exact lineage.
- Remove all manual MPC operator-review API/UI; explicit rollback remains.
- Corpus/challenger failure cannot invalidate active/rollback authority.
- Persistence/simulation work never runs on the control tick.
- On macOS, dependency setup is `uv sync --no-install-package bluepy`; never build/install `bluepy` on Darwin.
- Prefer the existing `.venv` for focused macOS tests.
- Before broad execution, record the user-provided known baseline failures in the SDD ledger and do not attribute them to this work without a changed-path reproduction.
- Run final bluepy-inclusive dependency and full test verification on the user-provided Linux host.
- Use Jujutsu only; describe each task commit before editing and run `jj new` after verification.
- TDD is mandatory: observe intended RED before production edits.

---

## File and Interface Map

### New files

- `common/learning_trajectory.py`: immutable frame, anchor, segment, corpus identity, and lineage contracts.
- `common/persistence/learning_trajectory.py`: transactional corpus repository, retention/recovery, immutable fit snapshots.
- `common/persistence/model_challenger.py`: durable challenger CAS state.
- `controller/runtime/actuation_delivery.py`: transparent platform decorator and delivered-edge journal.
- `controller/runtime/learning_trajectory.py`: process-level 20-second cross-mode recorder.

### Principal modified files

- `common/datastore.py`, `common/control_trace.py`, `common/cook_diagnostics.py`, `common/model_evidence.py`
- `controller/mpc_model.py`, `controller/mpc_core.py`, `controller/update_mpc.py`
- `controller/runtime/model_fitting.py`, `controller/runtime/model_persistence.py`, `controller/runtime/controller.py`, `controller/runtime/context.py`, `controller/runtime/devices.py`, `controller/runtime/runner.py`
- `controller/runtime/modes/base.py`, `smoke.py`, `hold.py`, `hold_learning.py`
- `controller/model_learning/grey_runtime.py`, `evaluation.py`, `confidence.py`, `activation.py`, `activation_runtime.py`, `report.py`, `migration.py`
- backend web contracts/routes, generated schemas/clients, and `MpcLearningView.tsx`

---

### Task 1: Define trajectory, corpus, and lineage contracts

**Files:**
- Create: `common/learning_trajectory.py`
- Test: `tests/unit/common/test_learning_trajectory_contracts.py`

**Interfaces:**
- Produces: `LearningTrajectoryFrame`, `HoldEntrySample`, `LearningTrajectorySegment`, `FitCorpusSlice`, `FitCorpusIdentity`, `ModelFitLineage`, `TrajectoryBreakReason`, `FrameDeliveryCertainty`, `canonical_trajectory_digest()`.
- Consumes: strict validation/canonical JSON conventions from `common/control_trace.py`.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Add learning trajectory contracts"
```

- [ ] **Step 2: Write failing contract tests**

Cover frozen ownership, finite/bounded values, chronology, exact 20-second full frames, partial-frame rules, pre-roll/scored separation, compatibility identity, and deterministic digest:

```python
def test_segment_digest_owns_frames_and_excludes_generation():
    segment = segment_fixture(role_generation=2)
    digest = segment.content_digest
    mutated_source["temperature_c"] = 999.0
    assert segment.content_digest == digest
    assert replace_generation(segment, 8).fit_partition_digest == segment.fit_partition_digest


def test_segment_rejects_scored_gap():
    with pytest.raises(ValueError, match="scored frames must be contiguous"):
        segment_fixture(scored=(frame(1), frame(3)))
```

Run:

```bash
.venv/bin/pytest -q tests/unit/common/test_learning_trajectory_contracts.py
```

Expected: missing module/types.

- [ ] **Step 3: Implement strict frozen contracts**

Define actual signatures:

```python
@dataclass(frozen=True, slots=True)
class FitCorpusSlice:
    segment_id: str
    through_ordinal: int
    prefix_digest: str
    pre_roll_count: int
    scored_count: int

@dataclass(frozen=True, slots=True)
class FitCorpusIdentity:
    schema_version: int
    corpus_revision: int
    fit_partition_digest: str
    slices: tuple[FitCorpusSlice, ...]
    corpus_digest: str
```

Canonical serialization sorts keys, rejects non-finite/oversized values, and hashes exact bytes. Fit compatibility excludes incumbent/generation/free parameters but includes structure, cadence, held physics, actuation mapping, fan regime, and ambient semantics.

- [ ] **Step 4: Verify GREEN**

```bash
.venv/bin/pytest -q tests/unit/common/test_learning_trajectory_contracts.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 2: Persist bounded trajectory segments

**Files:**
- Create: `common/persistence/learning_trajectory.py`
- Modify: `common/datastore.py`
- Test: `tests/unit/common/test_learning_trajectory_store.py`

**Interfaces:**
- Consumes: Task 1 contracts.
- Produces: `LearningTrajectoryRepository.begin_segment`, `append`, `break_and_begin`, `finalize`, `recover_open_segments`, `snapshot_fit_corpus`, `record_fit_request`, `complete_fit`, `replay_fit`.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Persist bounded learning trajectory segments"
```

- [ ] **Step 2: Write failing DDL/transaction/retention tests**

Test additive migration, CAS append, idempotent duplicate, conflict quarantine, atomic break/new begin, crash recovery, and deterministic whole-segment eviction:

```python
def test_retention_evicts_oldest_finalized_whole_segments():
    repo = repository(tmp_db)
    fill(repo, scored=8_700, segment_size=180)
    assert repo.status().scored_count <= 8_640
    assert repo.read_segment(oldest_id) is None
    assert repo.read_segment(open_id).state == "open"
```

- [ ] **Step 3: Implement corpus tables/repository**

Add `learning_trajectory_corpus`, `learning_trajectory_segment`, `learning_trajectory_frame`, and `learning_fit_run`. Every append atomically updates ordinal, chain digest, corpus counters, and retention. Use:

```python
next_digest = sha256(bytes.fromhex(previous_digest) + canonical_frame).hexdigest()
```

Auto-roll at 180 scored rows with up to 180 exact pre-roll rows. Enforce scored<=8640, pre-roll<=8640, segments<=256; never evict open segment.

- [ ] **Step 4: Verify GREEN and randomized invariants**

```bash
.venv/bin/pytest -q tests/unit/common/test_learning_trajectory_store.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 3: Establish one process-owned persistence lifetime

**Files:**
- Modify: `controller/runtime/model_persistence.py`
- Modify: `controller/runtime/controller.py`
- Modify: `controller/runtime/context.py`
- Modify: `controller/runtime/modes/hold_learning.py`
- Modify: `controller/model_learning/activation_runtime.py`
- Test: `tests/unit/runtime/test_model_persistence.py`
- Test: `tests/unit/runtime/test_hold_model_persistence.py`

**Interfaces:**
- Produces: process-owned `submit_trajectory_batch`, `barrier`, single-owner `close`; process context carries repository/worker.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Make learning persistence process-owned"
```

- [ ] **Step 2: Write failing lifetime/FIFO tests**

Assert Hold Stop barriers but does not close; process shutdown closes once; activation durability outranks trajectory work; scored frame/evidence commit atomically; queue rejection emits explicit gap.

- [ ] **Step 3: Refactor worker ownership**

```python
class ModelPersistenceWorker:
    def barrier(self, timeout: float = 2.0) -> bool: ...
    def close(self, timeout: float = 2.0) -> bool: ...
    def submit_trajectory_batch(self, batch: TrajectoryAppendBatch) -> Receipt: ...
```

Process controller constructs/owns one worker/repository. ActivationRuntime closes only internally created workers. Hold teardown finalizes and barriers.

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/runtime/test_model_persistence.py tests/unit/runtime/test_hold_model_persistence.py tests/unit/mpc/test_activation_runtime.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 4: Journal exact delivered actuation

**Files:**
- Create: `controller/runtime/actuation_delivery.py`
- Modify: `controller/runtime/devices.py`
- Modify: `controller/runtime/context.py`
- Test: `tests/unit/runtime/test_actuation_delivery.py`
- Modify: `tests/fakes/grill.py`

**Interfaces:**
- Produces: `DeliveredActuationEdge`, `DeliveredActuationIntegral`, `ActuationDeliveryJournal.integrate/mark_uncertain`, `DeliveredGrillPlatform`.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Journal exact delivered grill actuation"
```

- [ ] **Step 2: Write failing transparent-wrapper tests**

Prove each actuator call reaches underlying driver once, preserves result/exception, records post-call readback/time, integrates auger/fan/PWM exactly, and marks failed/unobservable intervals uncertain.

- [ ] **Step 3: Implement bounded O(1) edge journal**

```python
class ActuationDeliveryJournal:
    def integrate(self, start_s: float, end_s: float) -> DeliveredActuationIntegral: ...
    def mark_uncertain(self, reason: str, at_s: float) -> None: ...
```

Construct once in `build_devices`. No persistence/model import. Command-echo-only readback remains uncertain; asynchronous ramps without exact callback are never interpolated.

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/runtime/test_actuation_delivery.py tests/unit/runtime/test_devices.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 5: Capture Smoke pre-roll and Hold segments

**Files:**
- Create: `controller/runtime/learning_trajectory.py`
- Modify: `controller/runtime/modes/base.py`
- Modify: `controller/runtime/modes/smoke.py`
- Modify: `controller/runtime/modes/hold.py`
- Modify: `controller/runtime/controller.py`
- Test: `tests/unit/runtime/test_smoke_learning_trajectory.py`
- Test: `tests/characterization/test_mode_transitions.py`

**Interfaces:**
- Consumes: Tasks 1/2/4.
- Produces: process-level `LearningTrajectoryRuntime.mode_entered/mode_exited/observe_temperature/intervention/configuration_changed/status`.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Capture cross-mode learning trajectories"
```

- [ ] **Step 2: Write failing Smoke→Hold/boundary tests**

Assert no MPC construction/call in Smoke; exact 20-second integrals; compatible Smoke→Hold keeps one segment/anchor; Stop/manual/lid/safety/gap/restart/config/unit split with exact reason; partial transition unscored; Recipe effective-mode transition works.

- [ ] **Step 3: Implement shared mode hooks and recorder**

Shared mode loop emits one fresh probe sample/events. Recorder consumes journal and queues immutable batches off-path. Smoke frames are only pre-roll; eligible Hold frames are scored after existing reconciliation.

```python
class LearningTrajectoryRuntime:
    def mode_entered(self, event: ModeEntered) -> None: ...
    def mode_exited(self, event: ModeExited) -> None: ...
    def observe_temperature(self, sample: ThermalSample) -> None: ...
    def intervention(self, boundary: TrajectoryBoundary) -> None: ...
    def configuration_changed(self, boundary: TrajectoryBoundary) -> None: ...
    def status(self) -> TrajectoryStatus: ...
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/runtime/test_smoke_learning_trajectory.py tests/characterization/test_mode_transitions.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 6: Replay delay state and seed Hold before first solve

**Files:**
- Modify: `controller/mpc_model.py`
- Modify: `controller/mpc_core.py`
- Modify: `controller/runtime/runner.py`
- Modify: `controller/runtime/modes/hold.py`
- Test: `tests/unit/mpc/test_delay_chain_replay.py`
- Test: `tests/unit/runtime/test_hold_trajectory_seed.py`

**Interfaces:**
- Produces: `replay_delay_chain(intervals, theta, n_delay, initial_load)`, `EstimatorSeed`, `MpcCore.seed_from_trajectory`, `ControllerRunner.seed_operating_state`.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Warm MPC state from exact pre-roll"
```

- [ ] **Step 2: Write failing analytic/first-solve tests**

Compare replay against direct Erlang simulation; prove 180-frame bound; prove estimator lags and measured T0 set before first solve; absent/uncertain pre-roll reports cold start and withholds learning.

- [ ] **Step 3: Implement replay and seed**

Use existing Erlang coefficients. Replay candidate-specific delivered intervals, set chamber to anchor, reset disturbance with high uncertainty, record seed diagnostic. Replace candidate state copying with candidate-specific replay.

```python
def replay_delay_chain(
    intervals: tuple[LearningTrajectoryFrame, ...],
    *,
    theta: float,
    n_delay: int,
    initial_load: float,
) -> tuple[float, ...]: ...

@dataclass(frozen=True, slots=True)
class EstimatorSeed:
    delay_states: tuple[float, ...]
    chamber_temperature_c: float
    disturbance: float
    segment_id: str
    pre_roll_digest: str
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/mpc/test_delay_chain_replay.py tests/unit/runtime/test_hold_trajectory_seed.py tests/unit/mpc/test_mpc_model.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 7: Fit one model over independent corpus segments

**Files:**
- Modify: `controller/runtime/model_fitting.py`
- Modify: `controller/update_mpc.py`
- Modify: `controller/mpc_model.py`
- Test: `tests/unit/mpc/test_segmented_grey_fit.py`
- Test: `tests/unit/mpc/test_update_mpc.py`

**Interfaces:**
- Consumes: immutable corpus segment arrays and Task 6 replay.
- Produces: segmented `GreyFitJob`, pooled result and per-segment/cook metrics, common-mask scoring.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Fit grey model over segmented corpus"
```

- [ ] **Step 2: Write failing fit-math tests**

Prove independent simulation equals manual pooled residuals and differs from illegal concatenation; Smoke temperature changes do not alter fit; Smoke load changes warmed state; masks exclude anchors/warm-up; common incumbent/candidate mask; mask polish/rejection; per-cook no-regression veto; supplied 21–140 cannot exploit zero lag.

- [ ] **Step 3: Implement segmented optimizer**

Change `GreyFitJob` to compact segments. Replay each candidate, anchor chamber, zero-weight warm-up rows, concatenate residual/Jacobian rows. Freeze final masks and polish once; reject `warmup-mask-unstable`. Report pooled/per-cook errors and stacked-SVD identifiability.

```python
@dataclass(frozen=True, slots=True)
class GreyFitJob:
    request: FitRequest
    corpus: FitCorpusIdentity
    segments: tuple[GreyFitSegmentArrays, ...]
    config: GreyBoxMPCConfig

def fit_segmented_grey(job: GreyFitJob) -> GreyFitSuccess | GreyFitError: ...
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/mpc/test_segmented_grey_fit.py tests/unit/mpc/test_update_mpc.py tests/unit/mpc/test_mpc_refit.py
```

Pin supplied-fixture result digest.

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 8: Cut Grey runtime to persistent corpus fits

**Files:**
- Modify: `controller/model_learning/grey_runtime.py`
- Modify: `controller/runtime/model_fitting.py`
- Modify: `controller/runtime/modes/hold_learning.py`
- Modify: `controller/mpc.py`
- Modify: `controller/mpc_core.py`
- Test: `tests/unit/mpc/test_grey_learning_runtime.py`
- Test: `tests/unit/runtime/test_hold_refit_trigger.py`

**Interfaces:**
- Consumes: corpus snapshots/segmented worker.
- Produces: corpus-backed fit scheduling; Stop never adopts.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Use persistent corpus for grey learning"
```

- [ ] **Step 2: Write failing survival/cutover tests**

Assert data survives cook/promotion/rollback/restart; activation resets evaluator only; Stop finalizes/barriers/schedules; no direct adoption; incompatible partitions exclude without harming active model.

- [ ] **Step 3: Implement clean cutover**

Delete production fit ownership in `PassiveGreyHistory`, `TeardownGreyHistory`, `_operator_history`, `MpcCore._history`, raw cook callbacks, synchronous production refit, and `accepted-next-cook`. Runtime snapshots corpus manifests and submits segmented jobs. Identification setting schedules Stop fit; passive setting controls passive mid-cook submission; explicit calibration submits independently.

```python
def schedule_corpus_fit(
    repository: LearningTrajectoryRepository,
    partition: str,
    lineage: ModelFitLineage,
) -> FitSubmission:
    snapshot = repository.snapshot_fit_corpus(partition)
    return fit_worker.submit(GreyFitJob.from_snapshot(snapshot, lineage))
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/mpc/test_grey_learning_runtime.py tests/unit/runtime/test_hold_refit_trigger.py tests/unit/runtime/test_hold_model_persistence.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 9: Persist resumable challenger and exact provenance

**Files:**
- Create: `common/persistence/model_challenger.py`
- Modify: `common/datastore.py`
- Modify: `common/model_evidence.py`
- Modify: `common/persistence/model_evidence.py`
- Modify: `controller/model_learning/evaluation.py`
- Modify: `controller/mpc_snapshot.py`
- Test: `tests/unit/common/test_model_challenger_store.py`
- Test: `tests/unit/mpc/test_challenger_recovery.py`
- Modify: exact v6 provenance E2E fixture/test

**Interfaces:**
- Produces: `ModelChallengerState`, read/CAS/progress/qualify/retire APIs, evidence schema 4, checkpoint v5 challenger reference.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Persist resumable causal challengers"
```

- [ ] **Step 2: Write failing CAS/restart/migration tests**

Cover built/evaluating/qualified/retired; wins0/1 resume; no pending origins; epoch separation; lineage retirement; qualified→PREPARED CAS; PREPARED crash abort; legacy v4 ready-review import wins0; exact restored-v6 passive candidate must replace stale operator provenance in checkpoint/report/restart.

- [ ] **Step 3: Implement challenger authority**

Add `model_challenger_state` with phase, descriptors, corpus identity, preparation, calibration manifest, epoch/round/wins, evidence high water, activation link, CAS revision. Progress/evidence commit atomically. Snapshot v5 references exact row; report reads one challenger authority, removing stale checkpoint precedence.

```python
@dataclass(frozen=True, slots=True)
class ModelChallengerState:
    revision: int
    phase: Literal["built", "evaluating", "qualified", "activating", "retired"]
    origin: CandidateOrigin
    policy: ActivationPolicy
    corpus: FitCorpusIdentity
    incumbent: GreyControlPairDescriptor
    candidate: GreyControlPairDescriptor
    evaluation_epoch: int
    evaluation_round: int
    consecutive_wins: int
    calibration_manifest: CalibrationManifest | None
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/common/test_model_challenger_store.py tests/unit/mpc/test_challenger_recovery.py tests/unit/mpc/test_grey_learning_snapshot_migration.py tests/e2e/test_mpc_online_learning_e2e.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 10: Unify passive and calibration causal-auto activation

**Files:**
- Modify: `controller/model_learning/contracts.py`
- Modify: `controller/model_learning/activation.py`
- Modify: `controller/model_learning/activation_runtime.py`
- Modify: `controller/model_learning/confidence.py`
- Modify: `controller/model_learning/grey_runtime.py`
- Modify: `controller/mpc.py`
- Test: `tests/unit/mpc/test_grey_online_learning.py`
- Test: `tests/unit/mpc/test_model_confidence.py`
- Test: `tests/unit/mpc/test_activation_runtime.py`

**Interfaces:**
- Produces: `ActivationPolicy.CAUSAL_AUTO`, one qualification/handoff path, manifest gate, Stop/next-cook resume.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Automatically qualify calibration candidates"
```

- [ ] **Step 2: Write failing gate-equivalence tests**

Parameterize origins; assert identical fit/preparation/horizon/two-win/durability/activation records. Operator adds complete low/middle/high/coast manifest. Calibration works with passive setting false. Supplied sequence-140 challenger survives Stop, resumes wins0, activates only after two complete rounds.

- [ ] **Step 3: Implement one state machine**

Map both origins to `CAUSAL_AUTO`; persist candidate before evaluation; persist completed rounds only; restore/rebuild/warm under exact lineage. Remove operator-reviewed/teardown confidence/direct branch. Preserve existing frame-boundary activation and rollback.

```python
_POLICY_BY_ORIGIN = {
    CandidateOrigin.PASSIVE_ONLINE: ActivationPolicy.CAUSAL_AUTO,
    CandidateOrigin.OPERATOR_CALIBRATION: ActivationPolicy.CAUSAL_AUTO,
}

def qualification_gates(state: ModelChallengerState) -> QualificationDecision: ...
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/mpc/test_grey_online_learning.py tests/unit/mpc/test_model_confidence.py tests/unit/mpc/test_activation_runtime.py tests/unit/runtime/test_hold_learning_runtime.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 11: Remove manual review from report, API, contracts, and UI

**Files:**
- Modify: `controller/model_learning/report.py`
- Modify: `common/web_contracts/learning.py`, inventory, registry
- Modify: `blueprints/api/routes.py`
- Rename: `controller/model_learning/activation_service.py` -> `rollback_service.py`
- Modify: generated JSON/TypeScript contracts
- Modify: `modelEvidenceApi.ts`, `MpcLearningView.tsx`
- Test: backend contract/report/API and React unit/browser tests

**Interfaces:**
- Produces: report v3 causal progress and rollback-only service.
- Removes: activation POST/wire types, review policy/status, form/button.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Remove manual MPC model review"
```

- [ ] **Step 2: Write failing backend/client/UI tests**

Assert warming/collecting/evaluating/interrupted/qualified/activating/active progress with epoch/horizons/wins/resume/lineage. Assert activation POST absent/404 and absent generated client. UI contains no digest/decision inputs/button; calibration/rollback and PID-SP review remain.

- [ ] **Step 3: Implement clean cutover**

Delete `OPERATOR_REVIEWED`, MPC `READY_FOR_REVIEW`, wire types, route, activation service, client and form. Rename rollback service/types. Regenerate schemas/TS. Keep socket invalidation and errors.

```python
class MpcLearningReportV3(BaseModel):
    status: Literal[
        "warming", "collecting", "fitting", "evaluating",
        "interrupted", "qualified", "activating", "active", "fallback", "error",
    ]
    evaluation: CausalEvaluationProgress | None
    candidate: CandidateReport | None
    corpus: CorpusStatusReport
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/web/test_api_model_evidence.py tests/unit/controller/test_learning_report.py
bun test web-react/tests/unit/components/dashboard/MpcLearningView.test.tsx
```

Run dashboard browser E2E.

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 12: Extend trace, diagnostics, cookfile, and replay

**Files:**
- Modify: `common/control_trace.py`, `common/cook_diagnostics.py`
- Modify: trace session/replay and `file_mgmt/cookfile.py`
- Test: control trace, diagnostics, cookfile integration, exact E2E

**Interfaces:**
- Produces: estimator-seed/segment/challenger payloads, corpus report, v7 importer, exact replay.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Trace and replay segmented MPC learning"
```

- [ ] **Step 2: Write failing schema/cookfile/replay tests**

Assert seed metadata, segment links, corpus/challenger progress, current-cook segment refs, digest round trip, corrupt provenance failure, v7 idempotent import, v6 audit-only, exact supplied fixture replay.

- [ ] **Step 3: Implement diagnostics/import**

Bump schemas. Cookfile references current cook segments/global report only. Import exact v7 joins; never synthesize v6 pre-roll. Replay validates all digests.

```python
class CookLearningDiagnostics(BaseModel):
    # Existing trace/evidence/report fields remain.
    trajectory_segments: tuple[CookTrajectorySegmentReference, ...]
    trajectory_schema_versions: tuple[int, ...]
```

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest -q tests/unit/common/test_control_trace_schema.py tests/unit/common/test_cook_diagnostics.py tests/integration/test_cookfile_learning_diagnostics.py tests/e2e/test_mpc_online_learning_e2e.py
```

- [ ] **Step 5: Finalize**

```bash
jj st
jj new
```

---

### Task 13: Full migration, retention, clean-deletion, and production E2E gate

**Files:**
- Create: `tests/e2e/test_smoke_hold_learning_trajectory.py`
- Create: `tests/e2e/test_cumulative_mpc_learning.py`
- Modify: `tests/e2e/test_mpc_online_learning_e2e.py`
- Modify: migration/restart/rollback tests
- Delete: obsolete volatile-history/manual-review production/tests

**Interfaces:**
- Consumes all prior tasks.
- Produces default-running complete lifecycle proof.

- [ ] **Step 1: Describe commit**

```bash
jj desc -m "Verify cumulative MPC learning end to end"
```

- [ ] **Step 2: Add required failing scenarios**

Cover Smoke→Hold pre-first-solve warm state/no MPC in Smoke; cold versus Smoke-start parameter stability; two-cook one-manifest fit; corpus survival; >8640 deterministic eviction; supplied candidate Stop/resume/two-win activation; origin gate equivalence; no pre-ACTIVE output; v6/v7 authority/provenance migration; crash/open segment/pending origins; manual/lid/safety/unknown gaps; corruption quarantine; cookfile/database digest replay.

- [ ] **Step 3: Remove obsolete paths cleanly**

Use LSP references before deletion. Remove single-cook raw refit, volatile fit authorities, manual review endpoint/service/contracts/UI, obsolete policy/status/outcomes, and aliases. Keep only historical migration readers that validate persisted input.

- [ ] **Step 4: Run macOS verification without bluepy**

```bash
uv sync --no-install-package bluepy
.venv/bin/pytest -q tests/unit/common tests/unit/mpc tests/unit/runtime tests/integration tests/e2e
bun test web-react/tests/unit
```

Compare failures to the baseline ledger; every new/changed-path failure must be resolved.

- [ ] **Step 5: Run Linux full verification**

On the user-provided Linux host:

```bash
uv sync
.venv/bin/pytest -q tests
bun test web-react/tests/unit
```

Run backend type/lint checks and dashboard browser E2E. Record exact commands, versions, pass/fail/skip counts, and known-baseline disposition in the SDD ledger.

- [ ] **Step 6: Independent review and final commit**

Request whole-branch safety/code review with spec, plan, diff, rulings, and verification. Address Critical/Important findings and rerun affected verification.

```bash
jj st
jj new
```
