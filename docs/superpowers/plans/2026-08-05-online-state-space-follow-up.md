# Online Innovation State-Space Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair state-space online candidate refresh, prove state alignment and persistence, and allow it to challenge scheduled ARX only if cross-domain evidence passes.

**Architecture:** Reuse the completed scheduled-ARX observation, runner, trace, prequential scoring, linear MPC, promotion, and rollback framework. Productionize only the innovation state-space model and refresh diagnostics, then run it as a non-commanding challenger against scheduled ARX until deterministic simulator and real-data evidence authorizes a runtime selector.

**Tech Stack:** Python 3.11+, NumPy, SciPy, Pydantic dataclasses, pytest, Ruff, Jujutsu

## Global Constraints

- Begin only after `2026-08-05-online-scheduled-arx-adaptation.md` is implemented and verified.
- Reuse `FrameObservation`, `AffinePrediction`, `OnlineAdaptation`, the linear MPC solver, trace payloads, and runner observation queue; do not create parallel variants.
- A failed refresh is a typed rejection with diagnostics; it never masquerades as an unchanged successful candidate.
- State-space cannot command the grill until candidate refresh, state alignment, snapshot restore, and the empirical proof gate pass.
- Promotion remains frame-boundary, reversible, opt-in, and based on two untouched five-minute wins.
- Refresh p99 ≤ 250 ms and linear solve p99 ≤ 50 ms.
- The shared controller-model snapshot remains at or below 65,536 bytes.

## File structure

- `controller/linear_mpc/state_space.py`: bounded innovation model, deterministic refresh, alignment, full snapshot/restore.
- `controller/linear_mpc/adaptation.py`: add state-alignment evidence to existing generic decisions only where absent.
- `controller/mpc.py`: construct state-space challenger/active model only after the empirical gate authorizes configuration.
- `common/control_trace.py`: extend existing evaluation/lifecycle variants with state-space diagnostics.
- `docs/superpowers/experiments/state_space_online_compare.py`: reproduce failures and compare against scheduled ARX.
- `docs/superpowers/experiments/_state_space_online_compare.json`: complete evidence and activation decision.

---

### Task 1: Reproduce and type candidate-refresh exhaustion

**Files:**
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/state_space.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/runner.py`
- Modify: `tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py`
- Create: `docs/superpowers/experiments/state_space_refresh_diagnostics.py`
- Create: `tests/unit/mpc/test_state_space_refresh_diagnostics.py`

**Interfaces:**
- Produces: `RefreshRejectionReason`, `CandidateAttempt`, and `RefreshDiagnostics` with complete per-order/delay evidence.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "experiment(mpc): diagnose state-space refresh exhaustion"
```

- [ ] **Step 2: Write failing typed-diagnostic tests**

Require these rejection reasons:

```python
class RefreshRejectionReason(StrEnum):
    INSUFFICIENT_SAMPLES = "insufficient-samples"
    RANK_DEFICIENT = "rank-deficient"
    ILL_CONDITIONED = "ill-conditioned"
    UNSTABLE_AFTER_PROJECTION = "unstable-after-projection"
    IMPLAUSIBLE_GAIN = "implausible-gain"
    ALIGNMENT_FAILED = "alignment-failed"
    NONFINITE = "nonfinite"
    NO_VALID_CANDIDATE = "no-valid-candidate"
```

For every configured `(order, delay)`, assert diagnostics contain sample count, block/Hankel shape, singular values, effective rank, condition number, projection result, gain, alignment error, prediction score, braking score, rejection reasons, and elapsed milliseconds.

- [ ] **Step 3: Run diagnostic tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py \
  tests/unit/mpc/test_state_space_refresh_diagnostics.py
```

Expected: refresh currently lacks complete typed candidate attempts.

- [ ] **Step 4: Instrument without changing selection**

Preserve current state-space math. Replace empty candidate exhaustion with an immutable `RefreshDiagnostics` result. Capture values at the point each candidate is rejected; do not recompute approximations after the loop.

- [ ] **Step 5: Reproduce fixed failures**

Run wrong-gain, wrong-pole, and wrong-delay fixed seeds for GrillSim and MAKGrillSim. Write a compact JSON artifact containing every attempt and the terminal reason. The script exits nonzero only for infrastructure/incomplete cells, not for a scientifically valid typed rejection.

- [ ] **Step 6: Run tests and inspect diagnostic completeness**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py \
  tests/unit/mpc/test_state_space_refresh_diagnostics.py
.venv/bin/ruff check docs/superpowers/experiments/state_space_refresh_diagnostics.py
```

Expected: all tests pass and every failed refresh has at least one candidate attempt plus a terminal typed reason.

- [ ] **Step 7: Start the next change**

```bash
jj new -m "Continue after state-space refresh diagnostics"
```

---

### Task 2: Production bounded innovation state-space model

**Files:**
- Create: `controller/linear_mpc/state_space.py`
- Create: `tests/unit/mpc/test_innovation_state_space.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/state_space.py`
- Modify: `docs/superpowers/experiments/linear_mpc_bakeoff/runner.py`

**Interfaces:**
- Consumes: production `FrameObservation`, `AffinePrediction`, and Task 1 refresh diagnostics.
- Produces: `StateSpaceConfig`, `InnovationStateSpace.fit`, `track`, `observe`, `refresh`, `affine_prediction`, `snapshot`, and `from_snapshot`.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(mpc): productionize innovation state-space model"
```

- [ ] **Step 2: Write failing deterministic identification tests**

Generate stable known first- and second-order systems. Fit twice with identical chronological samples and assert identical order, delay, matrices, state, and diagnostics. Permute candidate declaration order and assert selection is unchanged. Feed rank-deficient constant data and assert typed rejection leaves the incumbent byte-for-byte unchanged.

- [ ] **Step 3: Write failing physical-bound tests**

Assert every accepted candidate has finite matrices, pole magnitude below the configured bound, positive bounded steady gain, positive-semidefinite bounded covariance, and finite affine predictions. Use a deliberately unstable/noisy candidate and assert projection or typed rejection—never an unchecked model.

- [ ] **Step 4: Run model tests red**

```bash
.venv/bin/python -m pytest -q tests/unit/mpc/test_innovation_state_space.py
```

Expected: production module missing.

- [ ] **Step 5: Port and repair deterministic refresh**

Use a bounded chronological/stratified record. For each configured order/delay:

1. construct fixed-size block Hankel matrices;
2. compute SVD once;
3. reject if effective rank cannot support the realization;
4. regularize the least-squares solve with the configured penalty;
5. estimate `A`, `B`, `C`, `D`, `E`, `K`, process covariance, and measurement covariance;
6. project stability and gain;
7. score on a disjoint retained validation suffix;
8. choose by `(prediction_score, braking_score, order, delay)`.

Do not let failed candidates consume or mutate incumbent state.

- [ ] **Step 6: Bound allocations and history**

Pre-size reusable NumPy work arrays for configured maximum order/block rows. Bound the refresh record by sample count and strata. Snapshot only the active model, current state, bounded record metadata, and sufficient refresh counters; do not serialize Hankel workspaces.

- [ ] **Step 7: Migrate bakeoff state-space imports**

Make the experiment arm import the production model. Retain only dataset/adaptation evidence rendering in the experiment package. Remove the duplicate algorithm after every caller migrates.

- [ ] **Step 8: Run production and bakeoff state-space tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_innovation_state_space.py \
  tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py \
  tests/unit/mpc/linear_mpc_bakeoff/test_prediction.py
.venv/bin/ruff check controller/linear_mpc/state_space.py tests/unit/mpc/test_innovation_state_space.py
```

Expected: all selected tests pass.

- [ ] **Step 9: Start the next change**

```bash
jj new -m "Continue after production innovation state-space model"
```

---

### Task 3: Exact state alignment and snapshot restore

**Files:**
- Modify: `controller/linear_mpc/state_space.py`
- Modify: `controller/linear_mpc/adaptation.py`
- Modify: `tests/unit/mpc/test_innovation_state_space.py`
- Modify: `tests/unit/mpc/test_online_adaptation.py`
- Modify: `tests/unit/mpc/test_mpc_model_snapshot.py`

**Interfaces:**
- Produces: `AlignmentResult(transform, aligned_state, output_error_c)`, state-space `alignment_error_c` snapshot evidence, and independent nested-state validation.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(mpc): align and persist state-space challengers"
```

- [ ] **Step 2: Write failing similarity-transform tests**

Create two equivalent realizations related by a known invertible similarity transform. Assert alignment recovers an equivalent realization and preserves current output prediction within `1e-10`. Add non-equivalent, rank-deficient, and non-finite cases that return `ALIGNMENT_FAILED` without changing either model.

- [ ] **Step 3: Write failing promotion-alignment tests**

Use the production adaptation coordinator. A candidate with `output_error_c=2.0` may pass; `2.0 + 1e-12` must fail with `state-alignment`. Assert failed alignment resets the win count and does not change the role generation.

- [ ] **Step 4: Write failing snapshot round-trip tests**

Persist and restore `A`, `B`, `C`, `D`, `E`, `K`, covariances, current state, order, delay, record metadata, role generation, and diagnostics. Assert next prediction and next Kalman update match within `1e-12`. Corrupt dimensions, covariance, stability, gain, or state/output consistency and assert only the state-space member is rejected.

- [ ] **Step 5: Run alignment/snapshot tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_innovation_state_space.py \
  tests/unit/mpc/test_online_adaptation.py \
  tests/unit/mpc/test_mpc_model_snapshot.py
```

Expected: alignment/restore contracts missing.

- [ ] **Step 6: Implement deterministic alignment**

Use shared observability matrices over a fixed horizon to solve the candidate-to-incumbent transform. Map candidate matrices and current state into incumbent coordinates. Compute current predicted output before and after mapping. Return an owned immutable result; commit nothing until all dimensions, conditioning, finite-value, and `≤2 °C` checks pass.

- [ ] **Step 7: Extend generic promotion evidence**

Populate existing `state_aligned`, alignment evidence kind, and error fields. Scheduled ARX continues to report `not-applicable`; do not add an ARX-specific branch to generic scoring.

- [ ] **Step 8: Run tests green and measure snapshot size**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_innovation_state_space.py \
  tests/unit/mpc/test_online_adaptation.py \
  tests/unit/mpc/test_mpc_model_snapshot.py
```

Expected: tests pass. `test_mpc_model_snapshot.py` must encode a maximally populated composite snapshot with `json.dumps(..., allow_nan=False)` and assert its UTF-8 byte length is below 65,536.

- [ ] **Step 9: Start the next change**

```bash
jj new -m "Continue after state-space alignment and persistence"
```

---

### Task 4: Trace state-space refresh and shadow it against scheduled ARX

**Files:**
- Modify: `common/control_trace.py`
- Modify: `controller/mpc.py`
- Modify: `controller/linear_mpc/adaptation.py`
- Modify: `tests/unit/common/test_control_trace_schema.py`
- Create: `tests/unit/mpc/test_state_space_shadow_integration.py`
- Modify: `tests/unit/mpc/test_mpc_online_adaptation.py`

**Interfaces:**
- Reuses model observation payload unchanged.
- Extends model evaluation/lifecycle evidence with order, delay, singular values, effective rank, alignment error, covariance/pole summary, refresh duration, and state-space digest.
- Adds an internal state-space challenger factory for experiments; no operator setting yet.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "feat(mpc): shadow state-space challenger"
```

- [ ] **Step 2: Write failing trace-variant tests**

Round-trip accepted, rejected, and exhausted refresh evaluations. Reject missing attempts for `no-valid-candidate`, non-finite singular values, invalid rank, negative duration, or an accepted alignment error above 2 degrees Celsius.

- [ ] **Step 3: Write failing shadow integration tests**

Start from a restored scheduled-ARX incumbent. Feed canonical observations to both roles. Assert state-space refresh runs every five minutes on the worker, can earn wins, and cannot affect commands while the internal experiment-only activation gate is false. Assert queue gaps and stale generations reject its evidence identically to ARX.

- [ ] **Step 4: Run trace/shadow tests red**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/mpc/test_state_space_shadow_integration.py \
  tests/unit/mpc/test_mpc_online_adaptation.py
```

Expected: state-space variants/factory missing.

- [ ] **Step 5: Extend existing payload variants**

Add optional state-space fields guarded by model kind. Keep payload discriminators and common promotion fields unchanged. Enforce model-kind-specific required/forbidden combinations in Pydantic validators.

- [ ] **Step 6: Add internal challenger construction**

Use a private constructor argument or experiment factory—not persisted user configuration—to select state-space as challenger. The active scheduled-ARX solve path remains unchanged until a later evidence artifact explicitly authorizes activation.

- [ ] **Step 7: Run trace/shadow tests green**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/mpc/test_state_space_shadow_integration.py \
  tests/unit/mpc/test_mpc_online_adaptation.py
.venv/bin/ruff check common/control_trace.py controller/linear_mpc/state_space.py controller/linear_mpc/adaptation.py
```

Expected: all selected tests pass.

- [ ] **Step 8: Start the next change**

```bash
jj new -m "Continue after state-space shadow integration"
```

---

### Task 5: Cross-domain proof artifact and activation decision

**Files:**
- Create: `docs/superpowers/experiments/state_space_online_compare.py`
- Create: `tests/unit/mpc/test_state_space_online_compare.py`
- Generate: `docs/superpowers/experiments/_state_space_online_compare.json`
- Conditionally modify only when artifact `ship=true`: `controller/mpc.py`
- Conditionally modify only when artifact `ship=true`: `controller/controllers.json`
- Conditionally modify only when artifact `ship=true`: `tests/unit/mpc/test_mpc_online_adaptation.py`

**Interfaces:**
- Produces a complete scheduled-ARX-versus-state-space artifact and either an exposed `online_model` selector or an explicit evidence-backed refusal to expose one.

- [ ] **Step 1: Describe the change**

```bash
jj describe -m "experiment(mpc): prove online state-space challenger"
```

- [ ] **Step 2: Write failing artifact-contract tests**

Require fixed seeds, all three mismatch types, GrillSim, MAKGrillSim, chronological real-MAK, refresh attempts, alignment, prediction/control metrics, raw timing, complete-cell accounting, and an explicit decision. Require at least one accepted wrong-model recovery for `ship=true`.

- [ ] **Step 3: Run artifact tests red**

```bash
.venv/bin/python -m pytest -q tests/unit/mpc/test_state_space_online_compare.py
```

Expected: experiment/artifact missing.

- [ ] **Step 4: Implement the comparison using production paths**

Use the production frame observation, pulse scheduler, scheduled-ARX incumbent, state-space challenger, linear policy, scoring, and trace diagnostics. Run identical scenario commands for both arms. Keep real-MAK results prediction-only and chronological.

- [ ] **Step 5: Generate the full artifact**

```bash
.venv/bin/python docs/superpowers/experiments/state_space_online_compare.py \
  --output docs/superpowers/experiments/_state_space_online_compare.json
```

Expected: exit 0 and zero incomplete/duplicate cells.

- [ ] **Step 6: Apply the machine-checkable activation decision**

If and only if artifact `decision.ship` is `true`, add:

```python
online_model="scheduled-arx"
```

and expose `online_model` in `controllers.json` with choices `scheduled-arx` and `state-space`. Add tests proving state-space is restored/selected only when explicitly configured and still requires `enable_online_adaptation=true`.

If `decision.ship` is `false`, do not add `online_model`; add a regression assertion that the catalog has no state-space choice, and preserve the artifact's exact rejection reasons. This is a completed negative experimental result, not unfinished implementation.

- [ ] **Step 7: Run artifact and production integration tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_state_space_online_compare.py \
  tests/unit/mpc/test_state_space_shadow_integration.py \
  tests/unit/mpc/test_mpc_online_adaptation.py \
  tests/unit/mpc/test_online_adaptation_integration.py
.venv/bin/ruff check docs/superpowers/experiments/state_space_online_compare.py tests/unit/mpc/test_state_space_online_compare.py
```

Expected: all selected tests pass and code/catalog state matches the artifact decision.

- [ ] **Step 8: Start the next change**

```bash
jj new -m "Continue after state-space online proof"
```

---

### Task 6: Final verification and review

**Files:**
- Review all files changed in Tasks 1–5.
- No new production behavior belongs in this task.

**Interfaces:**
- Produces fresh verification and review evidence.

- [ ] **Step 1: Describe the verification change**

```bash
jj describe -m "chore(mpc): verify online state-space adaptation"
```

- [ ] **Step 2: Run focused state-space tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/mpc/test_state_space_refresh_diagnostics.py \
  tests/unit/mpc/test_innovation_state_space.py \
  tests/unit/mpc/test_state_space_shadow_integration.py \
  tests/unit/mpc/test_state_space_online_compare.py \
  tests/unit/mpc/test_online_adaptation.py \
  tests/unit/mpc/test_mpc_model_snapshot.py \
  tests/unit/common/test_control_trace_schema.py
```

Expected: zero failures.

- [ ] **Step 3: Run affected MPC/runtime suites**

```bash
.venv/bin/python -m pytest -q tests/unit/mpc tests/unit/runtime tests/unit/common/test_control_trace_schema.py
```

Expected: zero failures; record exact pass/skip counts.

- [ ] **Step 4: Run formatting and lint**

```bash
.venv/bin/ruff format --check controller/linear_mpc controller/mpc.py common/control_trace.py docs/superpowers/experiments/state_space_online_compare.py tests/unit/mpc
.venv/bin/ruff check controller/linear_mpc controller/mpc.py common/control_trace.py docs/superpowers/experiments/state_space_online_compare.py tests/unit/mpc
```

Expected: no formatting changes and no lint errors.

- [ ] **Step 5: Smoke the full comparison**

Rerun `state_space_online_compare.py` from Task 5 and verify complete cells, at least one accepted recovery for a positive decision, raw p99 timing, alignment evidence, and decision/code agreement.

- [ ] **Step 6: Request code review**

Use `superpowers:requesting-code-review`. Require review of refresh determinism, rank/conditioning math, state alignment, ownership, persistence dimensions, no-command shadow isolation, artifact completeness, and conditional selector exposure.

- [ ] **Step 7: Resolve findings and rerun verification**

For each accepted finding, write the failing regression first, implement the source correction, run the focused test, then repeat Steps 2–5.

- [ ] **Step 8: Leave a clean continuation change**

```bash
jj new -m "Continue after verified online state-space adaptation"
```
