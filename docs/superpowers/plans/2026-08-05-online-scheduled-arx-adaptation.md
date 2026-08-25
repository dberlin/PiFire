# Online Scheduled-ARX MPC Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in scheduled-ARX challenger that learns continuously from completed production pulse frames and takes over linear MPC at the next frame once it has better prequential evidence.

**Architecture:** Productionize the bakeoff's scheduled ARX and linear QP under `controller/linear_mpc`, feed them immutable 20-second realized-actuation observations through the existing asynchronous runner, and keep current grey-box MPC as the initial incumbent and rollback target. Persist complete learner state in the current controller-model envelope and record exact observations/evaluations through typed control-trace payloads.

**Tech Stack:** Python 3.11+, NumPy, SciPy, Pydantic dataclasses, pytest, Ruff, Jujutsu, SQLite control traces

## Global Constraints

- `enable_online_adaptation` is a separate opt-in and defaults to `false`.
- Disabled behavior must preserve the current MPC command path and valid grey-box snapshot restore.
- The manipulated variable remains one normalized combustion load `q` in `[0, 1]`; the existing allocator owns auger/fan conversion.
- Normal cooks receive no identification-only perturbations.
- Production uses the existing 2-second pulse quantum inside a 20-second frame.
- Hold must never wait for learning, evaluation, persistence, or a controller solve.
- Promotion changes no in-progress frame; the first new command is eligible only for the next frame.
- Completed eligible wins and learned coefficients survive cook boundaries; prediction origins crossing an unknown shutdown gap do not.
- The controller-model JSON snapshot must remain at or below 65,536 bytes.
- Per-frame learner p99 ≤ 5 ms; five-minute evaluation p99 ≤ 250 ms; linear solve p99 ≤ 50 ms.
- Treat failures as appliance-control quality problems: preserve bounded commands and reversibility without overstating ordinary cooking risk.

## File structure

- `controller/linear_mpc/contracts.py`: immutable observations, affine predictions, update/evaluation outcomes, protocols.
- `controller/linear_mpc/arx.py`: scheduled ARX, square-root RLS, physical projection, full snapshot/restore.
- `controller/linear_mpc/policy.py`: deterministic condensed box-QP linear MPC.
- `controller/linear_mpc/adaptation.py`: eligibility, prequential evidence, incumbent/challenger generations, promotion decisions.
- `controller/linear_mpc/grey_box.py`: immutable prediction adapter for the initial production grey-box incumbent.
- `controller/linear_mpc/trace.py`: canonical control-trace-to-observation reconstruction.
- `controller/runtime/runner.py`: bounded asynchronous frame-observation delivery.
- `controller/runtime/modes/hold.py`: exact completed-frame observation construction and trace emission.
- `controller/mpc.py`: opt-in coordinator, active policy handoff, rollback, status, composite snapshot.
- `common/control_trace.py`: typed model observation/evaluation/lifecycle payloads.
- `controller/update_mpc.py`: reuse canonical Celsius/ambient trace conversion.
- `controller/controllers.json`: visible boolean opt-in.
- `docs/superpowers/experiments/linear_mpc_bakeoff/`: consume production scheduled-ARX/policy/adaptation code rather than private copies.
- `docs/superpowers/experiments/online_arx_compare.py`: production-path fixed-seed and real-data evidence runner.
- `docs/superpowers/experiments/_online_arx_compare.json`: generated evidence artifact.

---

### Task 1: Production linear-model contracts and scheduled ARX

**Files:**
- Create: `controller/linear_mpc/__init__.py`
- Create: `controller/linear_mpc/contracts.py`
- Create: `controller/linear_mpc/arx.py`
- Create: `tests/unit/mpc/test_scheduled_arx.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/arx.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/contracts.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/runner.py`

**Interfaces:**
- Produces: `FrameObservation`, `AffinePrediction`, `ModelUpdate`, `ScheduledARXConfig`, `ScheduledARX.fit`, `track`, `observe`, `affine_prediction`, `snapshot`, and `from_snapshot`.
- Snapshot schema: `scheduled-arx/v2`; unlike the experiment's `v1`, it contains all delay candidates, RLS sufficient statistics, bounded lag histories, validation counters, and active delay.

- [ ] **Step 1: Describe the Jujutsu change**

Run:

```bash
jj describe -m "feat(mpc): productionize scheduled ARX learner"
```

- [ ] **Step 2: Write failing immutable-contract and snapshot tests**

Add tests that construct:

```python
observation = FrameObservation(
    frame_start_s=0.0,
    frame_end_s=20.0,
    temp_c=110.0,
    setpoint_c=120.0,
    ambient_c=20.0,
    requested_q=0.4,
    realized_q=0.35,
    requested_auger_duty=0.36,
    delivered_on_s=7.0,
    requested_fan_duty=None,
    actual_fan_duty=None,
    result_revision=3,
    output_source="controller",
    lid_open=False,
    safety_inhibited=False,
    manual_override=False,
    stale=False,
    skipped=False,
    reset=False,
    continuous=True,
    role_generation=0,
)
```

Assert frozen dataclasses reject mutation; invalid time order, non-finite numbers, and `q` outside `[0, 1]` raise `ValueError`. Fit `ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3)))`, assimilate deterministic observations, serialize with `snapshot()`, restore with `from_snapshot()`, and assert the next prediction and next update match to `1e-12`. Assert JSON size for two fully populated ARX snapshots stays below 60,000 bytes, leaving envelope headroom.

- [ ] **Step 3: Run the focused tests red**

Run:

```bash
.venv/bin/python -m pytest -q tests/unit/mpc/test_scheduled_arx.py
```

Expected: collection fails because `controller.linear_mpc` does not exist.

- [ ] **Step 4: Implement immutable production contracts**

Define exact public shapes in `contracts.py`:

```python
@dataclass(frozen=True, slots=True)
class FrameObservation:
    frame_start_s: float
    frame_end_s: float
    temp_c: float
    setpoint_c: float
    ambient_c: float
    requested_q: float
    realized_q: float
    requested_auger_duty: float
    delivered_on_s: float
    requested_fan_duty: float | None
    actual_fan_duty: float | None
    result_revision: int
    output_source: str
    lid_open: bool
    safety_inhibited: bool
    manual_override: bool
    stale: bool
    skipped: bool
    reset: bool
    continuous: bool
    role_generation: int


@dataclass(frozen=True, slots=True)
class AffinePrediction:
    free_output_c: NDArray[np.float64]
    input_response_c: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class ModelUpdate:
    predicted_temp_c: float
    observed_temp_c: float
    innovation_c: float
    updated: bool
```

Copy incoming arrays, mark them read-only, and validate every scalar in `__post_init__`.

- [ ] **Step 5: Port scheduled ARX and add complete restore**

Port the tested `na=2`, `nb=2`, delay-bank, four-knot, square-root-RLS implementation. Keep NumPy work arrays reusable where practical. Persist, for every delay and region:

```python
{
    "theta": [...],
    "information_factor": [[...]],
    "normal_rhs": [...],
    "effective_samples": 0.0,
    "validation_error": 0.0,
    "validation_samples": 0,
    "consecutive_wins": 0,
}
```

Persist only the bounded history needed by `na`, `nb`, and maximum delay. Recompute covariance diagonals for status rather than storing a second matrix. `from_snapshot()` must validate schema, dimensions, finite values, pole bound, positive gain, active delay membership, and history lengths before constructing an owned model.

- [ ] **Step 6: Point the bakeoff scheduled-ARX arm at production code**

Replace private scheduled-ARX imports with:

```python
from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation
```

Keep an experiment adapter only where `SignalRecord` differs from production input. Remove duplicated scheduled-ARX algorithm bodies after all callers use production code.

- [ ] **Step 7: Run scheduled-ARX and existing bakeoff tests green**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_scheduled_arx.py \
  tests/unit/mpc/linear_mpc_bakeoff/test_arx.py \
  tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py
.venv/bin/ruff check controller/linear_mpc tests/unit/mpc/test_scheduled_arx.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 8: Start the next Jujutsu change**

Run:

```bash
jj new -m "Continue after production scheduled ARX"
```

---

### Task 2: Production linear MPC and grey-box prediction adapter

**Files:**
- Create: `controller/linear_mpc/policy.py`
- Create: `controller/linear_mpc/grey_box.py`
- Create: `tests/unit/mpc/test_linear_mpc_policy.py`
- Create: `tests/unit/mpc/test_grey_box_prediction_adapter.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/linear_mpc.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/runner.py`

**Interfaces:**
- Consumes: `AffinePrediction` from Task 1 and current grey-box estimator/model state.
- Produces: `LinearMPCConfig`, `LinearSolve`, `LinearMPC.solve(prediction, setpoint_c, q_previous, equilibrium_q)`, and `GreyBoxPredictionAdapter.affine_prediction(...)`.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(mpc): add production linear MPC policy"
```

- [ ] **Step 2: Write failing policy certificate tests**

Cover an analytic diagonal box-QP, zero horizon, `q` bounds, move penalty, equilibrium baseline, warm-start determinism, non-finite rejection, and KKT residual. Assert:

```python
solve = policy.solve(prediction, setpoint_c=120.0, q_previous=0.2, equilibrium_q=0.35)
assert 0.0 <= solve.sequence_q[0] <= 1.0
assert solve.kkt_residual <= config.tolerance
assert np.isfinite(solve.objective)
```

For the grey-box adapter, freeze a production estimator state, forecast under known future `q`, perturb one future input basis at a time, and assert `free_output + response @ q` matches direct grey-box simulation within `1e-8`.

- [ ] **Step 3: Run focused tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_linear_mpc_policy.py \
  tests/unit/mpc/test_grey_box_prediction_adapter.py
```

Expected: missing modules/classes.

- [ ] **Step 4: Port the condensed deterministic solver**

Move the bakeoff solver into production. Preserve the exact cost condensation and projected-gradient/box-QP certificate. Extend `solve` with `equilibrium_q`; use it as the steady sequence baseline while retaining `q_previous` for the first move penalty. Return immutable:

```python
@dataclass(frozen=True, slots=True)
class LinearSolve:
    sequence_q: NDArray[np.float64]
    objective: float
    kkt_residual: float
    iterations: int
    hessian_condition: float
```

Reject a result unless objective, sequence, condition, and KKT residual are finite and the residual is within tolerance.

- [ ] **Step 5: Implement immutable grey-box forecast origins**

Capture only owned scalars/arrays from the current estimator and model. `GreyBoxPredictionAdapter` must not hold the live controller. Produce a 60/300-second forecast under a supplied realized future `q` sequence and ambient sequence. Derive the affine map by deterministic basis simulations so the initial incumbent and ARX challenger can be scored on identical intervals.

- [ ] **Step 6: Migrate bakeoff policy imports**

Use `controller.linear_mpc.policy.LinearMPC` in the scheduled-ARX bakeoff arm. Remove the duplicated solver implementation after every experiment caller imports production code.

- [ ] **Step 7: Run focused and bakeoff policy tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_linear_mpc_policy.py \
  tests/unit/mpc/test_grey_box_prediction_adapter.py \
  tests/unit/mpc/linear_mpc_bakeoff/test_linear_mpc.py
.venv/bin/ruff check controller/linear_mpc tests/unit/mpc/test_linear_mpc_policy.py tests/unit/mpc/test_grey_box_prediction_adapter.py
```

Expected: all selected tests pass.

- [ ] **Step 8: Start the next change**

```bash
jj new -m "Continue after production linear MPC policy"
```

---

### Task 3: Online adaptation coordinator and prequential scoring

**Files:**
- Create: `controller/linear_mpc/adaptation.py`
- Create: `tests/unit/mpc/test_online_adaptation.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/adaptation.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/runner.py`

**Interfaces:**
- Consumes: Task 1 adaptive models and Task 2 immutable affine predictions.
- Produces: `AdaptationPolicy`, `UpdateGate`, `ObservationOutcome`, `EvaluationDecision`, `OnlineAdaptation.observe`, `evaluate_due`, `prospective_model`, `commit_promotion`, `rollback`, `snapshot`, and `from_snapshot`.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(mpc): add online adaptation coordinator"
```

- [ ] **Step 2: Write failing gate and generation tests**

Parametrize each hard rejection independently: lid, safety, manual, stale, skipped/reset, non-controller source, discontinuity, and unknown actuation. Verify hard rejection does not change coefficients. Verify unknown actuation clears lag warm-up. Verify ordinary but unexcited samples track history without updating parameters.

Add a deterministic origin-alignment test using temperatures indexed `T0..T20` and duties indexed by intervals `[t_k,t_{k+1})`; assert the 60-second origin at `t0` is scored against `T3` and the 300-second origin against `T15`, never `T0`.

- [ ] **Step 3: Write failing promotion and rollback tests**

Build fake incumbent/challenger models with fixed affine maps. Assert:

```python
first = manager.evaluate_due(at_s=300.0)
assert not first.promoted and first.consecutive_wins == 1
second = manager.evaluate_due(at_s=600.0)
assert second.promoted and second.consecutive_wins == 2
assert manager.role_generation == 1
```

Independently make prediction, braking, stability, gain, delay, samples, continuity, and prospective-solve gates fail. Assert a stale role generation cannot promote. Assert rollback restores the exact previous model digest and increments the generation.

- [ ] **Step 4: Run coordinator tests red**

```bash
.venv/bin/python -m pytest -q tests/unit/mpc/test_online_adaptation.py
```

Expected: missing coordinator.

- [ ] **Step 5: Implement update gating and bounded state**

Use exact defaults:

```python
@dataclass(frozen=True, slots=True)
class AdaptationPolicy:
    excitation_window: int = 12
    min_input_variance: float = 1e-3
    min_input_levels: int = 2
    min_effective_updates: int = 20
    evaluation_interval_s: float = 300.0
    required_consecutive_wins: int = 2
    max_delay_steps: int = 15
    max_pole_magnitude: float = 0.999
    braking_tolerance_c: float = 0.0
```

Track only bounded excitation history, ARX lag history, immutable forecast-origin matrices, completed score aggregates, and counters. Do not deep-copy live models per origin.

- [ ] **Step 6: Implement prequential origin completion**

At each eligible origin, store immutable 3-step and 15-step affine maps for both roles plus generation and interval identity. As each future realized duty arrives, fill the input vector. Score only when the corresponding future temperature arrives. Reject any origin crossing a discontinuity or generation change.

- [ ] **Step 7: Implement two-phase promotion**

`evaluate_due()` returns a decision and a prospective candidate but does not swap active control. `commit_promotion(decision_id)` performs the role swap only after the caller supplies a valid prospective linear solve. `reject_prospective(decision_id, reason="invalid-solve")` leaves the incumbent unchanged and resets the win count.

Persist completed eligible-win count across cooks. Do not persist partial origins crossing shutdown gaps.

- [ ] **Step 8: Migrate the experiment adaptation arm**

Make the scheduled-ARX bakeoff use production gating/scoring. Preserve experiment-only evidence rendering outside production. Delete duplicated production-equivalent coordinator logic after callers migrate.

- [ ] **Step 9: Run coordinator and experiment adaptation tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_online_adaptation.py \
  tests/unit/mpc/linear_mpc_bakeoff/test_adaptation.py \
  tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py
.venv/bin/ruff check controller/linear_mpc/adaptation.py tests/unit/mpc/test_online_adaptation.py
```

Expected: all selected tests pass.

- [ ] **Step 10: Start the next change**

```bash
jj new -m "Continue after online adaptation coordinator"
```

---

### Task 4: Typed learning trace and canonical replay conversion

**Files:**
- Modify: `common/control_trace.py`
- Create: `controller/linear_mpc/trace.py`
- Modify: `controller/update_mpc.py`
- Modify: `tests/unit/common/test_control_trace_schema.py`
- Modify: `tests/unit/mpc/test_update_mpc.py`
- Create: `tests/unit/mpc/test_linear_learning_trace.py`

**Interfaces:**
- Consumes: `FrameObservation`, `ObservationOutcome`, and `EvaluationDecision`.
- Produces: `ModelObservationPayload`, `ModelEvaluationPayload`, enriched `ModelEventPayload`, and `learning_observations(records) -> tuple[FrameObservation, ...]`.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(trace): record online model learning evidence"
```

- [ ] **Step 2: Write failing schema round-trip tests**

Add valid and invalid Pydantic cases for:

```python
ModelObservationPayload(
    frame_start_ms=1_000,
    frame_end_ms=21_000,
    temp_c=110.0,
    setpoint_c=120.0,
    ambient_c=20.0,
    requested_combustion_load=0.4,
    realized_combustion_load=0.35,
    delivered_on_seconds=7.0,
    eligible=True,
    rejection_reasons=(),
    input_variance=0.01,
    input_levels=3,
    incumbent_innovation_c=1.0,
    challenger_innovation_c=0.5,
    effective_updates=21,
    role_generation=0,
    model_digest="a" * 64,
)
```

Require ordered intervals, bounded loads, eligible/reason consistency, finite scores, non-negative counts, and a 64-character lowercase hexadecimal digest. Add equivalent evaluation and lifecycle cases and database row round trips.

- [ ] **Step 3: Write failing Fahrenheit/ambient replay tests**

Construct a session with `temperature_unit="F"`, `ambient_temperature=68.0`, framed-pulse records, and model observations. Assert canonical output is `temp_c=100.0`, `ambient_c=20.0`. Add Celsius equivalence. Add gaps, partial output, repeated revisions, and unknown-source cases that must raise `TraceSelectionError` rather than interpolate.

- [ ] **Step 4: Run trace tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/mpc/test_linear_learning_trace.py \
  tests/unit/mpc/test_update_mpc.py
```

Expected: missing payloads/converter and Fahrenheit assertion failure.

- [ ] **Step 5: Add discriminated trace payloads**

Add `MODEL_OBSERVATION` and `MODEL_EVALUATION` event kinds and payload discriminator values. Keep records immutable. Extend `ModelEventPayload` with optional model kind, nested schema, role generation, snapshot digest, and bounded structured parameters. Increment `TRACE_SCHEMA_VERSION`; keep old database rows readable through their stored envelope version only if the existing reader already supports version migration. Otherwise reject old rows explicitly rather than guessing fields.

- [ ] **Step 6: Implement canonical learning reconstruction**

`learning_observations` must prefer exact `ModelObservationPayload` records. For pre-extension traces, reconstruct only when one complete framed-pulse interval, one unambiguous control revision, and a frame-end temperature are available. Convert session setpoint/temperature/ambient with one `_to_c(value, unit)` helper. Never use `_DEFAULTS["T_amb"]` when the session recorded ambient.

- [ ] **Step 7: Make `update_mpc.py` reuse the converter**

Replace its local temperature/actuation join with the canonical session conversion. Preserve its grey-box fitting output shape. Add a regression asserting Fahrenheit and Celsius equivalent sessions fit the same parameters within solver tolerance.

- [ ] **Step 8: Run trace/replay tests green**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/mpc/test_linear_learning_trace.py \
  tests/unit/mpc/test_update_mpc.py \
  tests/unit/mpc/test_mpc_calibration.py
.venv/bin/ruff check common/control_trace.py controller/linear_mpc/trace.py controller/update_mpc.py tests/unit/mpc/test_linear_learning_trace.py
```

Expected: all selected tests pass.

- [ ] **Step 9: Start the next change**

```bash
jj new -m "Continue after online learning trace schema"
```

---

### Task 5: Runner observation delivery

**Files:**
- Modify: `controller/runtime/runner.py`
- Modify: `tests/fakes/runner.py`
- Modify: `tests/unit/runtime/test_sync_runner.py`
- Modify: `tests/unit/runtime/test_threaded_runner.py`
- Modify: `tests/unit/runtime/test_fake_runner_signature_parity.py`

**Interfaces:**
- Consumes: `FrameObservation`.
- Produces: `ControllerRunner.observe_frame(observation)`, synchronous forwarding, bounded timestamp-ordered threaded delivery, and status keys `pending_observations`/`dropped_observations`.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(runtime): queue completed frame observations"
```

- [ ] **Step 2: Write failing sync/threaded/parity tests**

Use a fake core with `observe_frame`. Assert sync forwarding is immediate. For threaded runner, enqueue observations out of call timing but with ascending frame timestamps, release one worker cycle, and assert core order is `[20.0, 40.0, 60.0]` before the next `update`. Fill the bounded queue, assert oldest records are dropped, and assert the dropped count is visible. Verify cores without `observe_frame` remain compatible no-ops.

- [ ] **Step 3: Run runner tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_fake_runner_signature_parity.py
```

Expected: runner interface/signature failures.

- [ ] **Step 4: Add the runner method and bounded queue**

Add the abstract method and implementation. Use a small bounded deque sized for at least ten minutes of 20-second frames. Drain under the existing lock, sort by `frame_end_s`, release the lock, then call core methods. Do not call learner code while holding the runner lock.

When overflow occurs, increment the dropped counter and inject a discontinuity marker before the next retained observation so the core invalidates lag/scoring continuity.

- [ ] **Step 5: Update fake runner parity**

Add `observe_frame` to the shared fake and its signature-parity assertion. Fakes should append owned observations for inspection and never mutate them.

- [ ] **Step 6: Run runner tests green**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_fake_runner_signature_parity.py
.venv/bin/ruff check controller/runtime/runner.py tests/fakes/runner.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Start the next change**

```bash
jj new -m "Continue after frame observation delivery"
```

---

### Task 6: Hold frame observation and trace emission

**Files:**
- Modify: `controller/runtime/modes/hold.py`
- Modify: `tests/unit/runtime/test_hold_pulse_scheduler.py`
- Modify: `tests/unit/runtime/test_hold_control_trace.py`

**Interfaces:**
- Consumes: completed `PulseFrameResult`, fresh `ptemp`, controller/setpoint/settings state, and Task 5 runner method.
- Produces: exactly one `FrameObservation` and one `ModelObservationPayload` per completed eligible/ineligible frame after the core returns its gate outcome.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(runtime): observe completed MPC pulse frames"
```

- [ ] **Step 2: Write failing exact-alignment tests**

Drive production `PulseScheduler` through two complete 20-second frames with 2-second pulses. Assert the runner receives:

```python
assert observation.frame_start_s == 0.0
assert observation.frame_end_s == 20.0
assert observation.delivered_on_s == 6.0
assert observation.realized_q == pytest.approx((6.0 / 20.0) / u_max)
assert observation.temp_c == pytest.approx(to_c(frame_end_ptemp))
```

Assert no duplicate when Hold polls multiple controller results inside one frame. Parametrize lid, manual, safety, stale, skipped, reset, and unknown source. Assert these observations are delivered with explicit flags but cannot appear eligible in the emitted learning trace.

- [ ] **Step 3: Run Hold tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py
```

Expected: no frame observation is reported.

- [ ] **Step 4: Thread fresh temperature through completion**

Pass current `ptemp` into `_advance_framed_pulse`/`_report_framed_feedback` rather than reading a cached controller-result temperature. Build the observation from the completed frame before latching the next request. Use `normalized_load_from_auger_duty(realized_duty, u_max=latched_u_max)`.

Do not emit an observation for the seed interval or a zero-duration frame. Mark continuity false after resets, skipped intervals, runner drops, or unknown provenance.

- [ ] **Step 5: Emit learning trace evidence**

The core returns its latest immutable observation outcome through runner status/result metadata. Record the typed model-observation payload with the same frame identity. If the asynchronous outcome is not yet available at frame completion, record it when published, keyed by frame end and role generation; never synthesize eligibility in Hold.

- [ ] **Step 6: Run Hold tests green**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_control_trace_recorder.py
.venv/bin/ruff check controller/runtime/modes/hold.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Start the next change**

```bash
jj new -m "Continue after completed-frame learning observations"
```

---

### Task 7: Production MPC integration, persistence, promotion, and rollback

**Files:**
- Modify: `controller/mpc.py`
- Modify: `controller/controllers.json`
- Modify: `tests/unit/mpc/test_mpc_controller.py`
- Modify: `tests/unit/mpc/test_mpc_model_snapshot.py`
- Modify: `tests/unit/mpc/test_mpc_refit.py`
- Create: `tests/unit/mpc/test_mpc_online_adaptation.py`
- Modify: `tests/unit/common/test_controller_model_state.py`

**Interfaces:**
- Consumes: all Tasks 1–6.
- Produces: config flag, adaptation coordinator lifecycle, grey-box/linear active-policy selection, prospective solve, atomic promotion, rollback, composite snapshots, and status.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(mpc): enable opt-in live scheduled ARX promotion"
```

- [ ] **Step 2: Write disabled-path characterization tests**

Run identical temperature/applied-output sequences through current MPC and MPC configured with `enable_online_adaptation=False`. Assert exact equality of command dictionaries, diagnostics fields, and grey-box snapshot content. Add controller-catalog assertion that the setting exists, is boolean, and defaults false.

- [ ] **Step 3: Write failing bootstrap and takeover tests**

Use fake grey-box/ARX models and a fake linear policy. Assert:

1. grey-box commands every frame before promotion;
2. ARX sees completed realized frames but cannot change output;
3. two valid evaluations plus a valid prospective solve commit promotion;
4. the current pulse request is unchanged;
5. the next update uses scheduled ARX and the existing allocator;
6. actual delivered load returns through `set_output`/`observe_frame`.

- [ ] **Step 4: Write failing snapshot and rollback tests**

Persist a composite snapshot, restore into a new controller, and assert next ARX prediction/update, active policy, role generation, consecutive wins, and monotonic revision match. Remove/corrupt only the nested online member and assert the valid grey-box model still restores. Force non-finite forecast, invalid KKT, and repeated active solve failure; assert the last-known-good policy returns and one lifecycle event records the exact reason.

- [ ] **Step 5: Run MPC integration tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_mpc_online_adaptation.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/mpc/test_mpc_model_snapshot.py \
  tests/unit/mpc/test_mpc_refit.py
```

Expected: missing flag/coordinator behavior.

- [ ] **Step 6: Add opt-in configuration and coordinator construction**

Add to `_DEFAULTS` and `controllers.json`:

```python
enable_online_adaptation = False
```

Friendly label: `Online Model Adaptation`. Description: `Learn a scheduled linear model during cooks and let it take over after repeated validation wins. Experimental. [Default=false]`.

Construct no online objects when false. When true, restore valid online state or create grey-box incumbent adapter plus fresh scheduled-ARX challenger.

- [ ] **Step 7: Integrate observation and active solve paths**

Add `Controller.observe_frame(observation)`. Feed it to the coordinator on the worker thread. During grey-box incumbency, leave `update()` unchanged. During ARX incumbency:

```python
prediction = active_arx.affine_prediction(
    horizon_steps=linear_config.horizon_steps,
    q_previous=self._applied_combustion_load,
    ambient_future=np.full(linear_config.horizon_steps, self.cfg["T_amb"]),
)
equilibrium = self._equilibrium_load(self._set_point_c, disturbance)
solve = self._linear_policy.solve(
    prediction,
    setpoint_c=self._set_point_c,
    q_previous=self._applied_combustion_load,
    equilibrium_q=equilibrium,
)
combustion_load = float(solve.sequence_q[0])
```

Pass `combustion_load` through the existing `allocate` call. Preserve existing held-command/stale-result behavior.

- [ ] **Step 8: Implement prospective promotion and rollback**

At evaluation due, solve once with the candidate. Commit only when the certificate is valid. Publish a model-lifecycle outcome with the new generation. Keep an owned last-known-good controller/model snapshot. Roll back only after existing repeated-failure policy is met; one miss holds the prior command.

- [ ] **Step 9: Implement composite snapshot and status**

Keep the current top-level grey-box schema/version/params. Add optional `online_adaptation` with its own schema. Increment top-level revision at evaluation checkpoint, promotion, rollback, and teardown. Validate nested state independently. Before returning a snapshot, encode with `json.dumps(..., allow_nan=False)` and refuse online state exceeding the store limit while retaining the prior valid snapshot.

Expose the exact status fields from Design Section 13 without adding tuning controls.

- [ ] **Step 10: Run MPC integration tests green**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_mpc_online_adaptation.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/mpc/test_mpc_model_snapshot.py \
  tests/unit/mpc/test_mpc_refit.py \
  tests/unit/common/test_controller_model_state.py
.venv/bin/ruff check controller/mpc.py controller/linear_mpc tests/unit/mpc/test_mpc_online_adaptation.py
```

Expected: all selected tests pass.

- [ ] **Step 11: Start the next change**

```bash
jj new -m "Continue after opt-in scheduled ARX integration"
```

---

### Task 8: End-to-end runtime trace, persistence, and restart tests

**Files:**
- Modify: `tests/unit/runtime/test_hold_pulse_scheduler.py`
- Modify: `tests/unit/runtime/test_hold_control_trace.py`
- Create: `tests/unit/mpc/test_online_adaptation_integration.py`
- Modify: `tests/fakes/runner.py`

**Interfaces:**
- Exercises the complete Hold → runner → MPC → learner → trace → snapshot path.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "test(mpc): cover live ARX handoff end to end"
```

- [ ] **Step 2: Add a deterministic multi-frame integration test**

Drive production Hold, production `PulseScheduler`, `ThreadedControllerRunner`, and an MPC core with deterministic fake prediction models. Complete enough frames for two five-minute windows. Assert chronological trace sequence:

```text
session → control updates/frames → model observations → model evaluation(reject, win=1)
→ model evaluation(promote, win=2) → model lifecycle(promote) → next-frame ARX command
```

Assert no command before the next frame uses the promoted model.

- [ ] **Step 3: Add cook-boundary/restart integration tests**

Stop after one complete eligible win, persist, construct a new Hold/controller from the snapshot, warm lag history after the shutdown gap, and complete one new eligible window. Assert the second win promotes. Assert an affine origin crossing shutdown is absent from the score.

- [ ] **Step 4: Add trace-failure and queue-overflow tests**

Make trace append fail and verify online control/learning continues while the recorder emits a gap after recovery. Overflow the observation queue and assert promotion is impossible until continuity and minimum sample gates are rebuilt.

- [ ] **Step 5: Run integration tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_online_adaptation_integration.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_threaded_runner.py
```

Expected: all selected tests pass without sleeps dependent on wall-clock timing.

- [ ] **Step 6: Start the next change**

```bash
jj new -m "Continue after live ARX integration coverage"
```

---

### Task 9: Production-path empirical comparison and artifact

**Files:**
- Create: `docs/superpowers/experiments/online_arx_compare.py`
- Create: `tests/unit/mpc/test_online_arx_compare.py`
- Generate: `docs/superpowers/experiments/_online_arx_compare.json`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/runner.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/__main__.py`

**Interfaces:**
- Consumes the production MPC, scheduled ARX, linear policy, allocator, production pulse scheduler, GrillSim, MAKGrillSim, and real-MAK fixture.
- Produces deterministic JSON with raw per-run metrics, timing distributions, promotion chronology, and a machine-checkable ship decision.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "experiment(mpc): compare online scheduled ARX takeover"
```

- [ ] **Step 2: Write failing artifact-contract tests**

Require schema, source revision, fixed seeds, plant/scenario identities, baseline and online rows, no duplicate cell keys, raw timing samples, and these metrics:

```python
REQUIRED = {
    "pct_within_5f",
    "overshoot_f",
    "settle_s",
    "rmse_f",
    "steady_peak_to_peak_f",
    "auger_on_s",
    "transitions_per_hour",
    "requested_realized_load_error",
    "deadline_misses",
    "stale_result_episodes",
    "prediction_rmse_60_c",
    "prediction_rmse_300_c",
    "braking_error_c",
    "promotions",
    "rollbacks",
}
```

For real MAK, accept only chronological prediction metrics supported by the fixture; control metrics remain unavailable rather than fabricated.

- [ ] **Step 3: Run artifact tests red**

```bash
.venv/bin/python -m pytest -q tests/unit/mpc/test_online_arx_compare.py
```

Expected: experiment module/artifact missing.

- [ ] **Step 4: Implement deterministic scenarios**

Run identical current-MPC and opt-in-online paths across fixed GrillSim and MAKGrillSim seeds, including cold start, hold, target increase, target decrease/coast, and lid interruption. Use production `PulseScheduler`, actual delivered load feedback, and no experiment-private actuator realization. Run real-MAK chronological replay for 60/300-second prediction where data exists.

Record learner, evaluation, and solve raw durations. Do not infer Raspberry Pi timing from workstation timing in the ship decision.

- [ ] **Step 5: Implement decision logic**

The artifact sets `ship=true` only when:

- online aggregate control score is strictly better than baseline;
- neither simulator regresses safety inhibit/reachability outcomes;
- neither simulator regresses relay transitions, stale episodes, or requested/realized error;
- real-MAK supported prediction does not regress;
- measured p99 budgets pass;
- every requested cell completed without failure.

Always publish failed results with explicit reasons; never select a winner from incomplete rows.

- [ ] **Step 6: Run focused experiment smoke tests**

```bash
.venv/bin/python -m pytest -q tests/unit/mpc/test_online_arx_compare.py -k "tiny or contract"
```

Expected: tiny GrillSim and MAKGrillSim paths pass.

- [ ] **Step 7: Generate the full artifact**

```bash
.venv/bin/python docs/superpowers/experiments/online_arx_compare.py \
  --output docs/superpowers/experiments/_online_arx_compare.json
```

Expected: exit 0, complete unique rows, decision and rejection reasons present, no non-finite JSON values.

- [ ] **Step 8: Validate artifact and focused production suite**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_online_arx_compare.py \
  tests/unit/mpc/test_online_adaptation_integration.py \
  tests/unit/mpc/test_mpc_closed_loop.py
.venv/bin/ruff check docs/superpowers/experiments/online_arx_compare.py tests/unit/mpc/test_online_arx_compare.py
```

Expected: all selected tests pass and artifact contract reports zero missing/duplicate cells.

- [ ] **Step 9: Start the next change**

```bash
jj new -m "Continue after online ARX empirical evidence"
```

---

### Task 10: Final verification and review

**Files:**
- Review all files changed in Tasks 1–9.
- No new production behavior belongs in this task.

**Interfaces:**
- Produces fresh verification evidence and review findings only.

- [ ] **Step 1: Describe the verification change**

```bash
jj describe -m "chore(mpc): verify online scheduled ARX adaptation"
```

- [ ] **Step 2: Run the focused feature suite**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_scheduled_arx.py \
  tests/unit/mpc/test_linear_mpc_policy.py \
  tests/unit/mpc/test_grey_box_prediction_adapter.py \
  tests/unit/mpc/test_online_adaptation.py \
  tests/unit/mpc/test_linear_learning_trace.py \
  tests/unit/mpc/test_mpc_online_adaptation.py \
  tests/unit/mpc/test_online_adaptation_integration.py \
  tests/unit/mpc/test_online_arx_compare.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_hold_pulse_scheduler.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/common/test_controller_model_state.py
```

Expected: zero failures.

- [ ] **Step 3: Run affected broader suites**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc \
  tests/unit/runtime \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/common/test_controller_model_state.py
```

Expected: zero failures; report exact pass/skip counts.

- [ ] **Step 4: Run formatting and lint**

```bash
.venv/bin/ruff format --check controller/linear_mpc controller/mpc.py controller/runtime/runner.py controller/runtime/modes/hold.py common/control_trace.py controller/update_mpc.py tests/unit/mpc tests/unit/runtime
.venv/bin/ruff check controller/linear_mpc controller/mpc.py controller/runtime/runner.py controller/runtime/modes/hold.py common/control_trace.py controller/update_mpc.py tests/unit/mpc tests/unit/runtime
```

Expected: no formatting changes required and no lint errors.

- [ ] **Step 5: Smoke the actual production path**

Run the production-path comparison from Task 9 and inspect the artifact decision, promotion chronology, rollback count, complete cells, and raw p99 timing. This command output and artifact—not a unit test alone—are the proof that the behavior works end to end.

- [ ] **Step 6: Request code review**

Use `superpowers:requesting-code-review`. Require reviewers to check:

- disabled-path compatibility;
- interval alignment and no prediction leakage;
- model ownership/threading;
- persistence completeness and size;
- promotion/rollback frame boundary;
- trace replay equivalence;
- artifact completeness and decision honesty.

- [ ] **Step 7: Resolve every finding and rerun affected verification**

For each accepted finding, add or strengthen a failing regression test first, implement the source fix, rerun the smallest affected suite, then rerun Steps 2–5.

- [ ] **Step 8: Leave a clean continuation change**

```bash
jj new -m "Continue after verified online scheduled ARX adaptation"
```
