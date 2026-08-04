# Linear MPC Model Bake-off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run an isolated three-arm experiment that identifies adaptive linear grill models, controls GrillSim and MAKGrillSim with a common linear MPC, evaluates the measured MAK cook without overstating its evidence, and recommends a model from reproducible JSON results.

**Architecture:** A new experiment package owns immutable signal/result contracts, deterministic datasets, three model arms, shared online-adaptation policy, one condensed linear MPC, and artifact/recommendation logic. Production controller modules remain unchanged; the experiment imports only `controller.grill_sim.GrillSim` and `controller.grill_sim.MAKGrillSim`, and reads the existing MAK CSV fixture. Every arm implements one affine-prediction interface so the same solver and scenario runner exercise it.

**Tech Stack:** Python 3.14, NumPy, SciPy only for offline reference checks and subspace linear algebra, pytest, Ruff, Jujutsu (`jj`), existing PiFire simulators.

## Global Constraints

- Work only in the `linear-mpc-experiment` Jujutsu workspace. Never use raw Git commands.
- Do not modify `controller/mpc.py`, `controller/mpc_model.py`, production settings, schemas, or the active MPC agent's files.
- Shared structure, per-grill parameters; no universal parameter vector.
- Plant input is realized mean auger duty `q` in `[0, 1]`; fan is fixed at 100% for primary evidence.
- Identification/control frame is exactly 20 seconds.
- Model diagnostics use 1, 5, 15, 30, and 60-minute horizons when data supports them.
- MPC compares 600, 800, and 1000-second horizons on validation data, then freezes one horizon across arms before test runs.
- Calibration mode may excite the plant; ordinary cooks may supply passive updates but receive no identification-only perturbation.
- The measured MAK fixture is requested-input reconstruction, not realized-actuation evidence.
- Workstation timings are reported raw and as a conservative `5x` projected RPi 5 distribution.
- Runtime hard-disqualifies only beyond projected p99 limits of 25 ms learner, 1.25 s refresh, or 250 ms MPC solve.
- A target miss remains visible but does not eliminate otherwise valid evidence.
- Use chronological splits only. A model must predict a sample before that sample can update it.
- Runtime-facing result objects are immutable dataclasses. Arrays returned across module boundaries are read-only copies or documented immutable views.
- Run focused tests per task. Run the complete experiment package tests, Ruff, and the end-to-end experiment once in Task 9.

## File Structure

Create this focused package:

```text
docs/superpowers/experiments/linear_mpc_bakeoff/
    __init__.py          Public experiment types and schema version
    __main__.py          CLI entry point
    contracts.py         Immutable arrays, configs, samples, metrics, model protocol
    data.py              Validation, resampling, fixture reconstruction, chronological splits
    datasets.py          Deterministic simulator calibration and scenario inputs
    prediction.py        Multi-horizon free-run scoring and synthetic-system helpers
    arx.py               Scheduled ARX fit, square-root RLS, delay bank
    state_space.py       Subspace bootstrap, Kalman state, rolling refresh
    dmc.py               Laguerre step response and square-root RLS
    adaptation.py        Gating, stratified replay, challenger/promotion policy
    actuation.py         20-second fractional pulse realization
    linear_mpc.py        Affine condensed QP and projected-gradient solver
    scenarios.py         Closed-loop matrix and common metrics
    artifact.py          Checkpoints, JSON schema, aggregation, recommendation
    runner.py            Experiment orchestration
```

Create matching focused tests:

```text
tests/unit/mpc/linear_mpc_bakeoff/
    test_data.py
    test_datasets.py
    test_prediction.py
    test_arx.py
    test_state_space.py
    test_dmc.py
    test_adaptation.py
    test_linear_mpc.py
    test_scenarios.py
    test_artifact.py
    test_cli.py
```

The generated evidence lives at:

```text
docs/superpowers/experiments/_linear_mpc_bakeoff.json
```

---

### Task 1: Signal contracts and input semantics

**Files:**
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/__init__.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/contracts.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/data.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_data.py`

**Interfaces:**
- Produces: `SignalRecord`, `Sample`, `DatasetSplit`, `ValidationError`, `validate_record(record)`, `resample_record(record, frame_s)`, `reconstruct_mak_fixture(path)`, and `chronological_split(record, fit_fraction, validation_fraction)`.
- `SignalRecord` fields: `time_s`, `temp_c`, `q`, `ambient_c`, `provenance`, and `metadata`; all numeric arrays are one-dimensional `numpy.float64` arrays with equal length.
- Later tasks consume only these contracts; they do not parse CSV or reinterpret `Q` themselves.

- [ ] **Step 1: Start an isolated Jujutsu change**

Run:

```bash
jj new -m "Add linear MPC experiment data contracts"
```

Expected: a new empty working-copy change whose parent contains the approved spec and plan.

- [ ] **Step 2: Write failing input-semantics and validation tests**

Create tests that pin the physical mapping and failure behavior:

```python
from pathlib import Path

import numpy as np
import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.data import (
    ValidationError,
    chronological_split,
    reconstruct_mak_fixture,
    resample_record,
    validate_record,
)

FIXTURE = Path("tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv")


def test_mak_q_is_reconstructed_mean_auger_duty() -> None:
    record = reconstruct_mak_fixture(FIXTURE)
    assert record.q[0] == pytest.approx(0.9)
    assert record.q[-1] == pytest.approx(0.1)
    assert record.provenance == "requested-input-reconstruction"


def test_resampling_preserves_auger_energy() -> None:
    record = make_record(time_s=[0, 5, 10, 15, 20], q=[0, 1, 0, 1, 0])
    framed = resample_record(record, frame_s=20.0)
    assert framed.q.tolist() == pytest.approx([0.5])


def test_validation_rejects_unknown_input_gap() -> None:
    record = make_record(time_s=[0, 20, 65], q=[0.2, 0.3, 0.4])
    with pytest.raises(ValidationError, match="unknown actuation interval"):
        validate_record(record, expected_frame_s=20.0)


def test_chronological_split_never_overlaps() -> None:
    split = chronological_split(make_record(100), 0.5, 0.25)
    assert split.fit.time_s[-1] < split.validation.time_s[0]
    assert split.validation.time_s[-1] < split.test.time_s[0]
```

Include a local `make_record` test helper that produces finite monotonic records without importing later modules.

- [ ] **Step 3: Run tests and confirm contract failures**

Run:

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_data.py -q
```

Expected: collection fails because the experiment contracts do not exist.

- [ ] **Step 4: Implement immutable contracts and exact reconstruction**

Implement frozen dataclasses and normalize arrays once:

```python
@dataclass(frozen=True, slots=True)
class SignalRecord:
    time_s: NDArray[np.float64]
    temp_c: NDArray[np.float64]
    q: NDArray[np.float64]
    ambient_c: NDArray[np.float64]
    provenance: str
    metadata: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    fit: SignalRecord
    validation: SignalRecord
    test: SignalRecord
```

`reconstruct_mak_fixture` must calculate:

```python
frac = np.clip((Q - 5.0) / 95.0, 0.0, 1.0)
q = 0.1 + frac * 0.8
```

Resampling integrates zero-order-held `q` over each complete 20-second frame, samples temperature at the frame boundary by linear interpolation, and rejects frames crossing an unknown cadence gap. `validate_record` checks strict timestamp order, finite values, equal lengths, `0 <= q <= 1`, and at least two samples.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_data.py -q
```

Expected: all Task 1 tests pass.

- [ ] **Step 6: Verify the Jujutsu change**

Run:

```bash
jj st
jj --no-pager diff --summary
```

Expected: only Task 1 files are changed in the current change.

---

### Task 2: Deterministic datasets and prediction scoring

**Files:**
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/datasets.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/prediction.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_datasets.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py`

**Interfaces:**
- Consumes: `SignalRecord` and validation functions from Task 1; `GrillSim` and `MAKGrillSim` from `controller.grill_sim`.
- Produces: `CalibrationProgram`, `generate_calibration_record(plant_name, seed, config)`, `prediction_origins(record, horizons_s)`, and `score_free_run(model, record, horizons_s)`.
- Calibration outputs use fixed fan `1.0`, 1-second plant integration, and energy-preserving 20-second records.

- [ ] **Step 1: Start the dataset change**

```bash
jj new -m "Add deterministic linear model datasets"
```

- [ ] **Step 2: Write failing deterministic-generator tests**

Pin repeatability, excitation, coast, and horizon availability:

```python
def test_calibration_is_repeatable() -> None:
    left = generate_calibration_record("GrillSim", seed=7, config=TEST_CONFIG)
    right = generate_calibration_record("GrillSim", seed=7, config=TEST_CONFIG)
    np.testing.assert_array_equal(left.q, right.q)
    np.testing.assert_array_equal(left.temp_c, right.temp_c)


def test_calibration_contains_plateaus_prbs_and_coast() -> None:
    record = generate_calibration_record("GrillSim", seed=3, config=TEST_CONFIG)
    assert np.count_nonzero(np.diff(record.q)) >= 12
    assert np.any(record.q == 0.0)
    assert {0.15, 0.35, 0.65}.issubset(set(np.round(record.q, 2)))


def test_short_real_tail_marks_long_horizons_unavailable() -> None:
    record = reconstruct_mak_fixture(FIXTURE)
    availability = prediction_origins(record, (60, 300, 900, 1800, 3600))
    assert availability[3600] == ()
    assert availability[1800] == ()
```

Define `FIXTURE` as the existing MAK CSV path and `TEST_CONFIG` as a `CalibrationProgram` with the production plateau/perturbation values but shortened segment durations. The helper configuration changes duration only; it must still produce at least twelve input transitions and a complete coast frame.

- [ ] **Step 3: Confirm tests fail before implementation**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_datasets.py tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py -q
```

Expected: missing generator and scoring symbols.

- [ ] **Step 4: Implement calibration programs**

Use immutable program segments:

```python
@dataclass(frozen=True, slots=True)
class ProgramSegment:
    duration_s: int
    center_q: float
    perturbation_q: float
    dwell_s: int
```

The default program uses three plateaus `(0.15, 0.35, 0.65)`, bounded `+/-0.08` PRBS changes every 120 seconds, and a final `q=0` coast. Generate PRBS from the explicit NumPy RNG seed. Use longer durations for MAK so calibration observes slow dynamics; encode durations in `CalibrationProgram`, not plant-name conditionals inside the integration loop.

- [ ] **Step 5: Implement free-run scoring**

Define immutable score records with `available`, `origins`, `rmse_c`, `max_abs_c`, `bias_c`, and `p90_abs_c`. A horizon is unavailable when no prediction origin has the full future window. Call the model's `forecast(record_prefix, q_future, ambient_future)` without exposing future temperatures.

- [ ] **Step 6: Run focused tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_datasets.py tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py -q
```

Expected: deterministic data and horizon tests pass.

- [ ] **Step 7: Verify the task change**

```bash
jj st
jj --no-pager diff --summary
```

Expected: only Task 2 files are added.

---

### Task 3: Shared model protocol and scheduled ARX arm

**Files:**
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/contracts.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/arx.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_arx.py`

**Interfaces:**
- Produces: `AdaptiveLinearModel` protocol, `AffinePrediction`, `Observation`, `UpdateOutcome`, `ScheduledARX`, and `ARXConfig`.
- `AdaptiveLinearModel` methods are exactly `fit(record)`, `forecast(prefix, q_future, ambient_future)`, `observe(observation)`, `affine_prediction(horizon_steps, q_previous, ambient_future)`, and `snapshot()`.
- `AffinePrediction` contains `free_output_c` with shape `(N,)` and `input_response_c` with shape `(N, N)` such that `y = free_output_c + input_response_c @ q_sequence`.

- [ ] **Step 1: Start the ARX change**

```bash
jj new -m "Add adaptive scheduled ARX model"
```

- [ ] **Step 2: Write failing analytical-system tests**

Use a known delayed stable system:

```python
def synthetic_step(q: np.ndarray) -> np.ndarray:
    y = np.zeros_like(q)
    for k in range(2, len(q) - 1):
        y[k + 1] = 0.92 * y[k] + 0.06 * q[k - 2]
    return y


def test_arx_recovers_delay_and_stable_pole() -> None:
    record = synthetic_record(synthetic_step, samples=1200, seed=4)
    model = ScheduledARX(ARXConfig(na=1, nb=1, delays=(1, 2, 3)))
    model.fit(record)
    snapshot = model.snapshot()
    assert snapshot["delay_steps"] == 2
    assert abs(snapshot["regions"][0]["poles"][0]) < 1.0


def test_arx_update_is_prequential() -> None:
    prefix = training_prefix()
    model = fitted_model(prefix)
    before = model.forecast(prefix, np.array([0.4]), np.array([20.0]))
    outcome = model.observe(observation(temp_c=999.0, q=0.4))
    assert outcome.predicted_temp_c == pytest.approx(before[0])


def test_arx_affine_prediction_matches_direct_forecast() -> None:
    prefix = training_prefix()
    model = fitted_model(prefix)
    affine = model.affine_prediction(10, q_previous=prefix.q[-1], ambient_future=np.full(10, 20.0))
    q = np.linspace(0.2, 0.5, 10)
    expected = model.forecast(prefix, q, np.full(10, 20.0))
    np.testing.assert_allclose(affine.free_output_c + affine.input_response_c @ q, expected, atol=1e-9)
```

Keep `synthetic_record`, `training_prefix`, `fitted_model(prefix)`, and `observation` as local deterministic helpers in `test_arx.py`. `fitted_model` must fit only the supplied prefix and leave its internal history at that prefix's final sample.

- [ ] **Step 3: Confirm ARX tests fail**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_arx.py -q
```

Expected: missing protocol and model symbols.

- [ ] **Step 4: Implement the protocol and square-root RLS**

Use fixed temperature knots `(82.2, 162.8, 232.2, 315.6)` degrees Celsius, clamping below/above the endpoint knots and linearly weighting adjacent regions. Store an upper-triangular covariance factor per region. Update with QR-based square-root RLS; do not invert a covariance matrix per sample.

Construct each feature vector from temperature differences, delayed input differences, and ambient error. Project AR roots to radius `0.999`; reject or project a non-positive DC gain. Maintain one candidate per configured delay and switch only after the challenger wins a validation window by the configured margin for two consecutive refreshes.

- [ ] **Step 5: Implement snapshots and affine conversion**

Snapshots contain schema, order, delay, knots, coefficients, poles, DC gain, covariance diagonal, effective samples, and update timing. `affine_prediction` must derive the companion recursion exactly; it must not finite-difference repeated forecasts.

- [ ] **Step 6: Run focused ARX tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_arx.py tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py -q
```

Expected: analytical delay, stability, prequential, and affine-equivalence tests pass.

- [ ] **Step 7: Verify the ARX change**

```bash
jj st
jj --no-pager diff --summary
```

---

### Task 4: Innovation state-space arm

**Files:**
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/state_space.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py`

**Interfaces:**
- Consumes: `AdaptiveLinearModel`, `AffinePrediction`, `SignalRecord`, and `Observation`.
- Produces: `InnovationStateSpace`, `StateSpaceConfig`, `subspace_fit(record, order, block_rows)`, and deterministic five-minute `refresh(record)`.

- [ ] **Step 1: Start the state-space change**

```bash
jj new -m "Add adaptive innovation state-space model"
```

- [ ] **Step 2: Write failing known-realization tests**

Generate a stable order-two SISO system with a two-step input delay and fixed Gaussian noise. Assert:

```python
def test_subspace_fit_recovers_order_two_dynamics() -> None:
    truth = known_state_space(seed=9)
    split = chronological_split(truth.record(samples=2400), 0.75, 0.05)
    model = InnovationStateSpace(StateSpaceConfig(orders=(1, 2, 3, 4), delays=(1, 2, 3)))
    model.fit(split.fit)
    assert model.snapshot()["order"] == 2
    assert max(abs(p) for p in model.snapshot()["poles"]) < 1.0
    assert free_run_rmse(model, split.test) < 0.25


def test_refresh_aligns_state_without_output_jump() -> None:
    model, extension = fitted_then_extended_record()
    before = model.current_output_c
    result = model.refresh(extension)
    assert result.accepted
    assert model.current_output_c == pytest.approx(before, abs=0.05)
```

Define `known_state_space`, `free_run_rmse`, and `fitted_then_extended_record` as local helpers in `test_state_space.py`. The known system uses fixed matrices and seeded noise; the extended record begins strictly after the model's fit prefix.

Also assert a refresh that cannot align within tolerance is rejected without mutating the incumbent matrices or state.

- [ ] **Step 3: Confirm state-space tests fail**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py -q
```

- [ ] **Step 4: Implement deterministic subspace identification**

Build past/future block Hankel matrices from centered `q`, ambient error, and temperature. Use deterministic full SVD, truncate to each candidate order, recover `A/B/C/D` by least squares, and select order/delay on chronological validation error plus a fixed parameter-count penalty. Normalize state sign deterministically so repeated runs serialize identical matrices.

Project eigenvalues outside radius `0.999` radially inward. Reject non-positive or implausible steady gain. Fit innovation covariance from training residuals with numeric floors.

- [ ] **Step 5: Implement Kalman update and rolling refresh**

Update state every 20-second observation. Every five minutes, refit from the bounded buffer and align the new realization using an observability-based least-squares transform. Calculate the candidate state from the incumbent output/history; accept only when the first predicted output differs by at most 0.05 degrees Celsius.

- [ ] **Step 6: Implement affine prediction and snapshots**

Build `free_output_c` and the lower-triangular Markov response matrix from powers of `A`. Include order, delay, matrices, poles, gain, covariance, alignment error, buffer samples, and refresh duration in snapshots.

- [ ] **Step 7: Run focused state-space tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py -q
```

- [ ] **Step 8: Verify the state-space change**

```bash
jj st
jj --no-pager diff --summary
```

---

### Task 5: Regularized Laguerre DMC arm

**Files:**
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/dmc.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_dmc.py`

**Interfaces:**
- Consumes: common model protocol and signal contracts.
- Produces: `LaguerreDMC`, `DMCConfig`, `laguerre_basis(length, terms, pole)`, and a 60-minute step-response snapshot.

- [ ] **Step 1: Start the DMC change**

```bash
jj new -m "Add adaptive Laguerre DMC model"
```

- [ ] **Step 2: Write failing basis and recovery tests**

```python
def test_laguerre_basis_is_deterministic_and_well_conditioned() -> None:
    basis = laguerre_basis(length=180, terms=12, pole=0.92)
    assert basis.shape == (180, 12)
    assert np.linalg.cond(basis.T @ basis) < 1e8


def test_dmc_recovers_delayed_step_response() -> None:
    split = chronological_split(delayed_first_order_record(delay_steps=5, pole=0.97, samples=1800), 0.75, 0.05)
    model = LaguerreDMC(DMCConfig(terms=(8, 12, 16), poles=(0.85, 0.92, 0.97)))
    model.fit(split.fit)
    response = model.snapshot()["step_response"]
    assert max(abs(response[:5])) < 0.02
    assert response[-1] > 0.0
    assert free_run_rmse(model, split.test) < 0.35


def test_dmc_rejects_negative_final_gain() -> None:
    candidate = fitted_model_with_forced_negative_gain()
    assert candidate.promotion_eligible is False
```

Define `delayed_first_order_record`, `free_run_rmse`, and `fitted_model_with_forced_negative_gain` as local deterministic helpers in `test_dmc.py`. The forced candidate must enter through the same projection/eligibility path used by fitted candidates.

- [ ] **Step 3: Confirm DMC tests fail**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_dmc.py -q
```

- [ ] **Step 4: Implement Laguerre basis and regularized fitting**

Generate discrete Laguerre responses by a stable recurrence; never construct them through symbolic expressions. Select 8, 12, or 16 terms and the basis pole on chronological validation error. Fit coefficients with curvature regularization:

```python
normal = Phi.T @ Phi + lambda_curve * (D2 @ basis).T @ (D2 @ basis)
rhs = Phi.T @ delta_temp
coefficients = np.linalg.solve(normal, rhs)
```

Search the same 0–300 second delay grid as other arms. Project final gain into the positive configured range and record whether projection occurred.

- [ ] **Step 5: Add square-root RLS and affine prediction**

Update Laguerre coefficients prequentially on informative frames. Re-evaluate basis pole and delay only at five-minute refreshes with challenger hysteresis. Construct the affine response matrix by shifting the identified step response, not by repeatedly simulating one input at a time.

- [ ] **Step 6: Run focused DMC tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_dmc.py tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py -q
```

- [ ] **Step 7: Verify the DMC change**

```bash
jj st
jj --no-pager diff --summary
```

---

### Task 6: Shared passive adaptation and promotion policy

**Files:**
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/adaptation.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_adaptation.py`

**Interfaces:**
- Consumes: any `AdaptiveLinearModel`, `Observation`, rolling prediction scores, and override/provenance flags.
- Produces: `AdaptationManager`, `UpdateGate`, `StratifiedReplay`, `PromotionDecision`, and structured rejection reasons.
- The manager owns incumbent/challenger separation. Model arms do not replace themselves.

- [ ] **Step 1: Start the adaptation change**

```bash
jj new -m "Add passive online model promotion"
```

- [ ] **Step 2: Write failing gate, replay, and promotion tests**

```python
@pytest.mark.parametrize("reason", ["lid-open", "safety", "manual", "stale-probe", "unknown-actuation", "unexcited"])
def test_blocked_samples_never_update(reason: str) -> None:
    manager, spy = manager_with_spy_model()
    outcome = manager.observe(blocked_observation(reason))
    assert outcome.updated is False
    assert spy.observe_calls == 0


def test_replay_retains_temperature_and_transient_strata() -> None:
    replay = StratifiedReplay(capacity=120, seed=1)
    replay.extend(hot_hold_samples(500))
    replay.extend(low_coast_samples(20))
    assert replay.count(stratum="low-coast") == 20
    assert len(replay) <= 120


def test_candidate_needs_two_validation_wins() -> None:
    manager = promotion_fixture()
    assert not manager.evaluate(candidate_score=0.8, incumbent_score=1.0).promoted
    assert manager.evaluate(candidate_score=0.8, incumbent_score=1.0).promoted
```

Implement `manager_with_spy_model`, `blocked_observation`, `hot_hold_samples`, `low_coast_samples`, and `promotion_fixture` as local deterministic helpers. The spy implements the full `AdaptiveLinearModel` protocol and records calls without changing promotion behavior.

Add tests that reject unstable dynamics, implausible gain/delay, worse braking, insufficient excitation, and state-alignment failure while leaving the incumbent object identity unchanged.

- [ ] **Step 3: Confirm adaptation tests fail**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_adaptation.py -q
```

- [ ] **Step 4: Implement gating and bounded stratified replay**

Use explicit enum reasons. Excitation requires a minimum variance and at least two input levels in the rolling window. Stratify by four temperature regions, three `q` bands, and transient/hold/coast state. Use deterministic reservoir replacement within each stratum.

- [ ] **Step 5: Implement challenger evaluation**

Score challenger and incumbent on the same untouched rolling window. Require two consecutive wins, stable finite dynamics, plausible gain/delay, sufficient effective samples, and no worse braking prediction. Return an immutable `PromotionDecision` containing every gate value and reason. Promotion swaps the complete model snapshot atomically.

- [ ] **Step 6: Run focused adaptation tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_adaptation.py -q
```

- [ ] **Step 7: Verify the adaptation change**

```bash
jj st
jj --no-pager diff --summary
```

---

### Task 7: Common linear MPC and pulse realization

**Files:**
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/actuation.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/linear_mpc.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_linear_mpc.py`

**Interfaces:**
- Consumes: `AffinePrediction` from any model arm.
- Produces: `PulseRealizer`, `MPCConfig`, `LinearMPC`, `SolveResult`, `condense_cost(prediction, setpoint_c, q_previous, weights)`, and `projected_gradient_qp(H, f, lower, upper, warm_start)`.
- Candidate horizons are exactly 30, 40, and 50 steps, corresponding to 600, 800, and 1000 seconds.

- [ ] **Step 1: Start the controller change**

```bash
jj new -m "Add common linear MPC experiment controller"
```

- [ ] **Step 2: Write failing solver and pulse tests**

```python
def test_projected_gradient_matches_scipy_reference() -> None:
    H, f = positive_definite_box_qp(seed=5, size=40)
    actual = projected_gradient_qp(H, f, np.zeros(40), np.ones(40), np.full(40, 0.5))
    expected = scipy_box_reference(H, f)
    np.testing.assert_allclose(actual.x, expected, atol=1e-5)
    assert actual.kkt_residual < 1e-6


def test_mpc_uses_only_selected_horizon() -> None:
    model = affine_integrator_model()
    mpc = LinearMPC(MPCConfig(horizon_s=800, frame_s=20))
    result = mpc.solve(model.affine_prediction(40, 0.2, np.full(40, 20.0)), setpoint_c=120.0)
    assert result.sequence_q.shape == (40,)


def test_fractional_pulse_carries_between_frames() -> None:
    pulse = PulseRealizer(frame_s=20, quantum_s=2)
    realized = [pulse.frame(0.15) for _ in range(10)]
    assert sum(frame.on_seconds for frame in realized) == pytest.approx(30.0, abs=2.0)
```

Define `positive_definite_box_qp`, `scipy_box_reference`, and `affine_integrator_model` locally in `test_linear_mpc.py`. Build the reference with `scipy.optimize.minimize(method="L-BFGS-B", jac=exact_gradient)` and assert that it reports success before comparing solutions.

Also test exact `q` bounds, deterministic warm starts, skipped-frame discard, and no allocations proportional to elapsed skipped time.

- [ ] **Step 3: Confirm MPC tests fail**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_linear_mpc.py -q
```

- [ ] **Step 4: Implement condensed quadratic cost**

For `y = y_free + G q`, minimize temperature error plus `lambda_move * ||D q - d0||^2` and terminal error. Build symmetric `H` and `f` once per affine prediction. Estimate the Lipschitz constant from `eigvalsh(H)[-1]`. Use warm-started accelerated projected gradient with exact `[0,1]` projection, fixed maximum iterations, dual/projected-gradient residual, and deterministic restart when acceleration increases objective.

- [ ] **Step 5: Implement horizon validation selection**

Evaluate 600, 800, and 1000 seconds on validation scenarios only. Freeze the selected seconds in the runner configuration before any test scenario. Tie-break to the shorter horizon when control score differs by less than 1%.

- [ ] **Step 6: Implement pulse realization**

Use 2-second quanta in a 20-second frame. Carry fractional requested on-time in a bounded balance, emit at most ten quanta per frame, and reset frame phase after skipped frames without replaying missed pulses. Return immutable requested/realized duty and transition counts.

- [ ] **Step 7: Run focused MPC tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_linear_mpc.py -q
```

- [ ] **Step 8: Verify the controller change**

```bash
jj st
jj --no-pager diff --summary
```

---

### Task 8: Closed-loop runner, evidence schema, and recommendation

**Files:**
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/scenarios.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/artifact.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/runner.py`
- Create: `docs/superpowers/experiments/linear_mpc_bakeoff/__main__.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_scenarios.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_artifact.py`
- Create: `tests/unit/mpc/linear_mpc_bakeoff/test_cli.py`

**Interfaces:**
- Consumes: all datasets, models, adaptation policy, MPC, and actuation modules.
- Produces: `run_experiment(config) -> ExperimentArtifact`, atomic checkpoint files, JSON schema version `1`, concise table rendering, and `recommend(artifact) -> Recommendation`.
- Primary control score is `rmse_c + mean_abs_error_c + 0.5 * max(overshoot_c, 0)`. Worst-domain score is the maximum domain median across required scenarios.

- [ ] **Step 1: Start the runner change**

```bash
jj new -m "Add linear MPC bake-off runner"
```

- [ ] **Step 2: Write failing scenario and recommendation tests**

```python
def test_fixed_fan_primary_scenario() -> None:
    result = run_tiny_scenario(plant="GrillSim", seed=2)
    assert set(result.fan_fraction) == {1.0}
    assert result.provenance == "simulated-fixed-fan"


def test_runtime_only_disqualifies_beyond_five_times_budget() -> None:
    artifact = artifact_with_timings(projected_solve_p99_ms=200.0)
    assert recommend(artifact).arms["arx"].valid
    artifact = artifact_with_timings(projected_solve_p99_ms=251.0)
    assert not recommend(artifact).arms["arx"].valid


def test_simplest_arm_wins_within_five_percent() -> None:
    artifact = artifact_with_scores(arx=10.4, state_space=10.0, dmc=12.0)
    assert recommend(artifact).selected_arm == "scheduled-arx"


def test_resume_matches_clean_artifact(tmp_path: Path) -> None:
    clean = run_tiny_matrix(tmp_path / "clean", resume=False)
    interrupted_then_resumed = run_interrupted_matrix(tmp_path / "resume")
    assert clean.to_json() == interrupted_then_resumed.to_json()
```

Define the tiny scenario, artifact builders, and interrupted-run helpers locally in their respective test modules. Artifact builders must construct schema-valid immutable result objects; they may vary only the field named by each test.

Add tests for structured arm failure, unavailable real horizons, raw and `5x` projected timing, bootstrap confidence intervals, source revision, package versions, and no forced winner when the Pareto frontier is material.

- [ ] **Step 3: Confirm runner tests fail**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_scenarios.py tests/unit/mpc/linear_mpc_bakeoff/test_artifact.py tests/unit/mpc/linear_mpc_bakeoff/test_cli.py -q
```

- [ ] **Step 4: Implement scenario matrix and metrics**

Run both plants with fixed fan `1.0`, fixed seeds, cold starts, low/middle/high targets, up/down steps, long hold, lid excursion, and wrong gain/pole/delay initializations. Run frozen and online modes. Integrate each plant at 1 second while solving and learning at 20 seconds. Record requested and realized `q` separately.

Metrics include RMSE, IAE, mean absolute error, over/undershoot, settling, peak-to-peak hold band, requested-versus-realized duty error, transitions/hour, promotion events, deadline misses, learner/refresh/solve timings, and prediction errors.

- [ ] **Step 5: Implement artifact and checkpoints**

Use frozen Pydantic or dataclass structures serialized through an explicit `to_document` method. Write checkpoints and final JSON atomically via temporary file plus `os.replace`. Include config, seeds, splits, model snapshots, failures, metrics, raw timings, `5x` projections, provenance, environment versions, and revision. Sort all dictionary keys and result rows deterministically.

- [ ] **Step 6: Implement recommendation logic**

Invalid reasons are limited to leakage, wrong input semantics, non-finite/unstable behavior, irreproducibility, and runtime beyond hard limits. Rank valid arms by worst-domain control score, multi-horizon prediction, wrong-model recovery, then runtime. Choose the structurally simplest arm within 5% of best control score; use complexity order ARX, DMC, state-space. Emit a Pareto frontier when no arm dominates materially. Preserve every target miss in output.

- [ ] **Step 7: Implement CLI modes**

Support:

```text
python -m docs.superpowers.experiments.linear_mpc_bakeoff --quick
python -m docs.superpowers.experiments.linear_mpc_bakeoff --output PATH --resume
python -m docs.superpowers.experiments.linear_mpc_bakeoff --output PATH
```

`--quick` runs tiny deterministic smoke scenarios and never overwrites the committed full artifact. Full mode defaults to `docs/superpowers/experiments/_linear_mpc_bakeoff.json`.

- [ ] **Step 8: Run focused runner tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff/test_scenarios.py tests/unit/mpc/linear_mpc_bakeoff/test_artifact.py tests/unit/mpc/linear_mpc_bakeoff/test_cli.py -q
```

- [ ] **Step 9: Verify the runner change**

```bash
jj st
jj --no-pager diff --summary
```

---

### Task 9: Execute the bake-off and publish the evidence

**Files:**
- Create: `docs/superpowers/experiments/_linear_mpc_bakeoff.json`
- Modify only if evidence exposes a real experiment bug: files under `docs/superpowers/experiments/linear_mpc_bakeoff/` and their focused tests

**Interfaces:**
- Consumes: the complete experiment CLI.
- Produces: the final versioned evidence artifact and observed recommendation. No hand-authored metric may enter the JSON.

- [ ] **Step 1: Start the evidence change**

```bash
jj new -m "Record linear MPC bake-off evidence"
```

- [ ] **Step 2: Run the deterministic smoke experiment**

Run through context-mode or capture output to a file before summarizing:

```bash
uv run python -m docs.superpowers.experiments.linear_mpc_bakeoff --quick --output /tmp/linear-mpc-quick.json
```

Expected: all three arms complete on both tiny plant scenarios; artifact schema is `1`; no non-finite values; repeated quick runs are byte-identical after excluding explicit wall-clock timestamps.

- [ ] **Step 3: Run all focused experiment tests**

```bash
uv run pytest tests/unit/mpc/linear_mpc_bakeoff -q
```

Expected: all focused tests pass under randomized test order.

- [ ] **Step 4: Run Ruff once across changed Python files**

```bash
uv run ruff check docs/superpowers/experiments/linear_mpc_bakeoff tests/unit/mpc/linear_mpc_bakeoff
```

Expected: no errors.

- [ ] **Step 5: Run the full experiment**

```bash
uv run python -m docs.superpowers.experiments.linear_mpc_bakeoff --output docs/superpowers/experiments/_linear_mpc_bakeoff.json --resume
```

Expected: JSON contains GrillSim, MAKGrillSim, and real-MAK requested-input-reconstruction evidence; frozen and online arms; 600/800/1000-second validation selection; all available 1–60 minute diagnostic horizons; raw workstation and `5x` projected timing; and structured failures rather than missing rows.

- [ ] **Step 6: Validate the final artifact independently**

Use a small analysis command that reads the entire JSON and prints only:

- schema and source revision;
- completed/failed cell counts;
- selected MPC horizon;
- per-arm worst-domain control score;
- per-arm available prediction scores by domain/horizon;
- frozen-versus-online wrong-model deltas;
- raw and projected p99 timings;
- target misses, hard invalidations, Pareto frontier, and recommendation.

Expected: every recommendation statement is derivable from artifact fields; no measured 30/60-minute MAK claim exists; no runtime is labeled measured RPi 5.

- [ ] **Step 7: Re-run the exact bug reproduction if a cell failed**

For each failed cell, invoke the runner's exact arm/plant/scenario/seed selector and determine whether the failure is valid evidence or an experiment defect. Fix only experiment defects, add a regression test that fails on the defect, rerun the focused test, then rerun the affected cell and final aggregation. Do not replace a valid model failure with a fallback.

- [ ] **Step 8: Verify production isolation**

Run:

```bash
jj --no-pager diff --summary -r 'trunk()..@'
```

Expected: only the approved spec/plan, the new experiment package, focused tests, and generated evidence artifact appear. No production controller, settings, schema, lockfile, or web file appears.

- [ ] **Step 9: Verify the final Jujutsu changes**

Run:

```bash
jj st
jj --no-pager log -r 'trunk()..@' --no-graph
```

Expected: atomic described changes, no conflicts, and no unrelated files.
