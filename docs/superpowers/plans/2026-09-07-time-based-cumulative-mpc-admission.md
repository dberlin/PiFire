# Time-Based Cumulative MPC Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the original ten-minute MPC evidence timing, allow compatible cumulative evidence to build a shadow challenger, and preserve strict prospective qualification and durable activation.

**Architecture:** Replace frame-count admission with 600 effective seconds. The segmented fitter will ignore whole segments that remain inside the candidate/incumbent common warm-up mask, apply pooled evidence and regression gates, and retain supported-cook vetoes. The runtime will suppress optimizer retries until a known effective-duration deficit can have closed; all existing causal evaluation and activation authority remains unchanged.

**Tech Stack:** Python 3.14, NumPy, SciPy, Pydantic dataclasses, pytest, Jujutsu, React 19, TypeScript, Rstest, Bun.

**Spec:** `docs/superpowers/specs/2026-09-07-time-based-cumulative-mpc-admission-design.md`

## Global Constraints

- Production default: exactly `600.0` effective seconds.
- Current fit cadence: `FIT_CADENCE_S = 20.0`; never encode the policy as 30 rows.
- Aggregate fit evidence may construct only a shadow challenger.
- Existing causal horizons `(3, 15, 45, 90, 180)` and two consecutive complete wins remain unchanged.
- Existing exact delivery, continuity, warm-up, identifiability, lineage, native-build, target-timing, durable activation, rollback, and fallback gates remain strict.
- Unknown or warm-up-masked rows contribute zero evidence; they are never reconstructed.
- Current builders expose no schema or threshold override and emit no legacy blocker names.
- Preserve historical evidence strings as historical inputs; never re-admit terminal historical candidates.
- Preserve `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.` unless implementation inventory proves the durable model schema changes.
- Shared test helpers live only in `_`-prefixed non-test modules, `conftest.py`, or `tests/fakes/`.
- Use Jujutsu only. At each task boundary describe the blank working-copy revision with `jj describe -m`, and finalize it with `jj new` after verification. Do not use raw Git or direct `jj git push`.

---

### Task 1: Establish the 600-Second Simulator Boundary

**Files:**
- Create: `tests/e2e/_short_cook_mpc_admission_helpers.py`
- Create: `tests/e2e/test_short_cook_mpc_admission.py`
- Reuse: `tests/e2e/_mpc_online_learning_helpers.py`
- Reuse: `tests/fakes/grill.py`
- Reference: `tests/e2e/test_learned_model_closed_loop.py`
- Reference: `controller/grill_sim.py`

**Interfaces:**
- Consumes: production `Controller`, `GrillSim`, `MAKGrillSim`, `fit_segmented_grey()`, `segmented_corpus_fit_job()`, `GreyBoxPredictionAdapter`, and fixed 20-second trajectory contracts.
- Produces: `run_short_cook_campaign(plant_type: type[GrillSim], family: str) -> ShortCookCampaignResult`, used only by the permanent characterization test.

- [ ] **Step 1: Describe the task revision**

```bash
jj describe -m "Characterize ten-minute MPC admission"
```

- [ ] **Step 2: Add deterministic campaign contracts and cook collection helpers**

Create the `_`-prefixed helper module with these owned result contracts:

```python
@dataclass(frozen=True, slots=True)
class CookDwell:
    seed: int
    entry_frame: int
    frames_after_entry: int
    frames_within_15f_after_entry: int


@dataclass(frozen=True, slots=True)
class CandidateScore:
    raw_counts: tuple[int, ...]
    effective_duration_s: float
    warmup_excluded_segment_ids: tuple[str, ...]
    horizon_ratios: tuple[tuple[int, float], ...]
    whole_cook_ratio: float
    closed_loop_iae_ratio: float
    candidate_overshoot_c: float
    incumbent_overshoot_c: float
    horizon_wins: tuple[tuple[int, int], ...]
    whole_cook_wins: int
    closed_loop_iae_wins: int
    overshoot_wins: int


@dataclass(frozen=True, slots=True)
class ShortCookCampaignResult:
    dwell: tuple[CookDwell, ...]
    first_600s: CandidateScore
    full_177: CandidateScore
```

Use exactly:

```python
TRAINING_SEEDS = (0, 1, 2)
HELD_OUT_SEEDS = (10, 11, 12, 13, 14)
SCORED_FRAMES = 59
PRE_ROLL_FRAMES = 8
PRE_ROLL_DUTY = 0.15
FRAME_SECONDS = 20
TARGET_C = (225.0 - 32.0) * 5.0 / 9.0
HORIZONS = (3, 15, 45, 90, 180)
```

Training cooks must use the shipped uncalibrated MPC controller, exact delivered load, independent simulator seeds, eight excluded pre-roll frames, and 59 scored Hold frames. Held-out forecast traces must be long enough to complete horizon 180. Closed-loop scores must run candidate and uncalibrated controllers on identical held-out seeds.

Before Task 3 adds the production exclusion field, derive `warmup_excluded_segment_ids` directly from the fit request's ordered corpus slices and all-false `effective_masks`. After Task 3, assert that this independently derived tuple equals `GreyFitSuccess.warmup_excluded_segment_ids`.

Do not import any collected test module.

- [ ] **Step 3: Add the characterization test**

```python
@pytest.mark.slow
@pytest.mark.parametrize(
    ("plant_type", "family"),
    ((GrillSim, "grill"), (MAKGrillSim, "mak")),
)
def test_three_59_frame_cooks_build_a_better_600_second_candidate(
    ds,
    plant_type: type[GrillSim],
    family: str,
) -> None:
    result = run_short_cook_campaign(plant_type, family)

    assert len(result.dwell) == 3
    assert all(cook.entry_frame <= 59 for cook in result.dwell)
    assert all(cook.frames_after_entry >= 30 for cook in result.dwell)
    assert all(cook.frames_within_15f_after_entry >= cook.frames_after_entry - 1 for cook in result.dwell)

    for score in (result.first_600s, result.full_177):
        assert score.effective_duration_s >= 600.0
        assert dict(score.horizon_wins) == {3: 5, 15: 5, 45: 5, 90: 5, 180: 5}
        assert all(ratio < 1.0 for _, ratio in score.horizon_ratios)
        assert score.whole_cook_ratio < 1.0
        assert score.closed_loop_iae_ratio < 1.0
        assert score.candidate_overshoot_c < score.incumbent_overshoot_c
        assert score.whole_cook_wins == 5
        assert score.closed_loop_iae_wins == 5
        assert score.overshoot_wins == 5
```

The helper must select the earliest immutable prefix whose conservative candidate/incumbent common mask reaches 600 seconds. It must not rewrite a historical prefix through a current builder.

- [ ] **Step 4: Run the exact characterization before changing admission policy**

Run:

```bash
uv run pytest -m slow tests/e2e/test_short_cook_mpc_admission.py -q
```

Expected: both simulator cases pass all unseen comparisons. If either plant loses any required horizon or closed-loop criterion, stop implementation and revise the 600-second design from the measured boundary; do not weaken or delete an assertion.

- [ ] **Step 5: Verify helper isolation**

Run:

```bash
uv run pytest tests/unit/test_no_cross_test_imports.py -q
```

Expected: pass; the collected module imports only the `_`-prefixed helper.

- [ ] **Step 6: Finalize the characterization commit**

```bash
jj new
```

Expected: the parent commit is `Characterize ten-minute MPC admission`; the new working-copy revision is empty.

---

### Task 2: Replace Frame-Count Triggers with Duration

**Files:**
- Modify: `controller/runtime/model_fitting.py:1332-1445`
- Modify: `tests/unit/mpc/test_grey_online_learning.py:48-77`
- Modify: `tests/unit/mpc/test_grey_learning_runtime.py:414-559`
- Modify: `tests/unit/mpc/_grey_online_helpers.py:250-275`
- Modify: `tests/unit/mpc/test_mpc_controller.py:750-775`
- Modify: every remaining static `TriggerConfig(...)` consumer returned by LSP references

**Interfaces:**
- Consumes: validated `FrameObservation.frame_start_s/frame_end_s`, persisted scored-frame monotonic timestamps, and `FIT_CADENCE_S`.
- Produces: `TriggerConfig.min_effective_duration_s: float`, blocker `minimum-observed-duration`, and `_metric_duration_s(metric: GreyFitMetric) -> float` for Task 3.

- [ ] **Step 1: Describe the task revision**

```bash
jj describe -m "Use elapsed time for MPC fit triggers"
```

- [ ] **Step 2: Write failing duration-trigger tests**

Replace the sample-count test with exact duration boundaries:

```python
def test_trigger_uses_elapsed_duration_and_retains_other_evidence_gates() -> None:
    config = TriggerConfig(
        min_effective_duration_s=180.0,
        min_input_variance=0.02,
        min_input_levels=3,
        min_temperature_span_c=8.0,
        min_identifiability=0.5,
    )
    informative = tuple(_frame(index) for index in range(12))

    assert fit_trigger(informative[:8], identifiability=0.8, config=config).blockers == (
        "minimum-observed-duration",
    )
    assert fit_trigger(informative[:9], identifiability=0.8, config=config).ready
```

Add constructor tests proving `0`, negative, boolean, NaN, and infinity durations are rejected. Add persistent-corpus tests proving 599 seconds blocks and 600 seconds passes independently of segment count.

- [ ] **Step 3: Run tests and observe the old API failure**

Run:

```bash
uv run pytest \
  tests/unit/mpc/test_grey_online_learning.py::test_trigger_uses_elapsed_duration_and_retains_other_evidence_gates \
  tests/unit/mpc/test_grey_learning_runtime.py -q
```

Expected: fail because `TriggerConfig` still accepts `min_samples` and emits `minimum-samples`.

- [ ] **Step 4: Implement the duration configuration and raw trigger**

Use this contract:

```python
@dataclass(frozen=True, slots=True)
class TriggerConfig:
    min_effective_duration_s: float = 600.0
    min_input_variance: float = 0.02
    min_input_levels: int = 3
    min_temperature_span_c: float = 8.0
    min_identifiability: float = 0.5
```

Validate `min_effective_duration_s` with `_finite()` and require it to be strictly positive. Remove `min_samples` completely.

For volatile observations:

```python
observed_duration_s = sum(frame.frame_end_s - frame.frame_start_s for frame in frames)
if observed_duration_s < resolved.min_effective_duration_s:
    return TriggerDecision(False, ("minimum-observed-duration",), input_variance, input_levels)
```

For the persistent corpus, sum exact persisted frame durations:

```python
observed_duration_s = sum(
    (frame.monotonic_end_ms - frame.monotonic_start_ms) / 1_000.0
    for segment in snapshot.segments
    for frame in segment.scored_hold_frames
)
```

Do not use `len(frames) * 20` outside the fitter’s fixed-cadence array contract.

Add:

```python
def _metric_duration_s(metric: GreyFitMetric) -> float:
    return metric.sample_count * FIT_CADENCE_S
```

This helper is valid because `GreyFitSegmentArrays` already rejects scored durations that differ from `FIT_CADENCE_S`.

- [ ] **Step 5: Migrate every static constructor**

Use LSP references for `TriggerConfig`; change test-only small thresholds from rows to seconds. For example, nine 20-second rows become `min_effective_duration_s=180.0`, and one row becomes `20.0`.

Do not add an alias, `**kwargs` bridge, or default override.

- [ ] **Step 6: Run focused Python tests**

Run:

```bash
uv run pytest \
  tests/unit/mpc/test_grey_online_learning.py \
  tests/unit/mpc/test_grey_learning_runtime.py \
  tests/unit/mpc/test_mpc_controller.py -q
```

Expected: pass with no current `minimum-samples` emission.

- [ ] **Step 7: Finalize the duration-trigger commit**

```bash
jj new
```

---

### Task 3: Admit Aggregate Evidence and Exclude Warming Segments

**Files:**
- Modify: `controller/runtime/model_fitting.py:311-467, 608-931, 974-1152`
- Modify: `tests/unit/mpc/test_segmented_grey_fit.py:554-706, 817-937`
- Modify: `tests/unit/mpc/_grey_learning_runtime_helpers.py`
- Modify: `tests/unit/mpc/_model_activation_helpers.py`
- Modify: any `GreyFitComparison` or `GreyFitSuccess` constructor returned by LSP references

**Interfaces:**
- Consumes: `TriggerConfig.min_effective_duration_s`, `_metric_duration_s()`, ordered job segments, and conservative common masks.
- Produces: `GreyFitComparison.warmup_excluded_segment_ids`, `GreyFitSuccess.warmup_excluded_segment_ids`, pooled blockers, and supported-cook vetoes without `insufficient-supported-cooks`.

- [ ] **Step 1: Describe the task revision**

```bash
jj describe -m "Admit aggregate MPC fit evidence"
```

- [ ] **Step 2: Write failing aggregate and warm-up tests**

Add permanent tests covering these observable contracts:

```python
def test_aggregate_600_seconds_can_build_without_one_supported_cook(...):
    # Two independently reset 15-row cooks provide 30 effective 20-second rows.
    # Pin the optimizer to a candidate no worse than the incumbent.
    result = fit_segmented_grey(job)
    assert isinstance(result, GreyFitSuccess)
    assert result.metrics.pooled.sample_count == 30
    assert not any(metric.supports_regression_gate for metric in result.metrics.by_cook)
    assert result.rejection_reasons == ()


def test_supported_600_second_cook_still_vetoes_pooled_improvement(...):
    result = fit_segmented_grey(job)
    assert result.rejection_reasons == ("per-cook-regression:cook-supported",)


def test_pooled_regression_blocks_when_no_cook_is_individually_supported(...):
    result = fit_segmented_grey(job)
    assert result.rejection_reasons == ("pooled-regression",)


def test_warming_segment_is_explicitly_excluded_without_poisoning_older_evidence(...):
    result = fit_segmented_grey(job)
    assert isinstance(result, GreyFitSuccess)
    assert result.warmup_excluded_segment_ids == ("warming",)
    assert tuple(metric.segment_id for metric in result.metrics.by_segment) == ("usable",)
    assert tuple(result.effective_masks[1]) == (False,) * warming_count


def test_all_warming_segments_fail_with_typed_insufficient_warmup(...):
    result = fit_segmented_grey(job)
    assert isinstance(result, GreyFitError)
    assert result.error_type == "InsufficientWarmup"
```

Add boundary tests for 599.999 and 600 effective seconds, candidate-dependent `theta`, pooled excitation, pooled coverage, and pooled identifiability.

- [ ] **Step 3: Run the new tests and verify current failures**

Run:

```bash
uv run pytest tests/unit/mpc/test_segmented_grey_fit.py -q
```

Expected failures: current code rejects no-supported-cook corpora and raises `segment-warmup-incomplete` when one segment is all-masked.

- [ ] **Step 4: Add explicit warm-up exclusion fields**

Extend both immutable results:

```python
@dataclass(frozen=True, slots=True)
class GreyFitComparison:
    metrics: GreyFitMetrics
    incumbent_metrics: GreyFitMetrics
    effective_masks: tuple[Any, ...]
    warmup_excluded_segment_ids: tuple[str, ...]
    identifiability: float
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GreyFitSuccess:
    # existing fields remain in their current order
    warmup_excluded_segment_ids: tuple[str, ...] = ()
```

`GreyFitSuccess.__post_init__()` must derive the expected exclusions from `request.fit_corpus.slices` and `effective_masks`, then require exact ordered equality:

```python
expected = tuple(
    corpus_slice.segment_id
    for corpus_slice, mask in zip(self.request.fit_corpus.slices, masks, strict=True)
    if not any(bool(value) for value in mask)
)
if exclusions != expected:
    raise ValueError("warmup exclusions must exactly match all-false effective masks")
```

Also require mask count to equal corpus slice count. Migrate every constructor; do not use permissive defaults where a production result has masks.

- [ ] **Step 5: Filter all-masked segments from numerical metrics**

Change `_grouped_metrics()` to skip an all-false mask before calculating NumPy reductions:

```python
for segment, trajectory, mask in zip(job.segments, predicted, masks, strict=True):
    if not np.any(mask):
        continue
    errors = (trajectory - segment.scored_temperature_c)[mask]
    # append metrics and group by cook as today
```

If every common mask is all false, raise `ValueError("segment-warmup-incomplete:<first-segment-id>")`; `fit_segmented_grey()` continues mapping that exact condition to `GreyFitError.error_type == "InsufficientWarmup"`.

Keep all masks ordered against every request slice. Omitted metrics are a readable projection only; masks remain the numerical authority.

- [ ] **Step 6: Implement pooled admission and duration-based cook support**

Replace count-based support with:

```python
def _cook_evidence_supported(metric: GreyFitMetric) -> bool:
    thresholds = TriggerConfig()
    return (
        _metric_duration_s(metric) >= thresholds.min_effective_duration_s
        and metric.input_excitation >= thresholds.min_input_variance
        and metric.input_levels >= thresholds.min_input_levels
        and metric.temperature_span_c >= thresholds.min_temperature_span_c
    )
```

After identifiability is attached, build blockers in deterministic order:

```python
reasons: list[str] = []
pooled = candidate_metrics.pooled
thresholds = TriggerConfig()
if _metric_duration_s(pooled) < thresholds.min_effective_duration_s:
    reasons.append("minimum-effective-duration")
if pooled.input_excitation < thresholds.min_input_variance or pooled.input_levels < thresholds.min_input_levels:
    reasons.append("insufficient-excitation")
if pooled.temperature_span_c < thresholds.min_temperature_span_c:
    reasons.append("insufficient-coverage")
if pooled.identifiability < thresholds.min_identifiability:
    reasons.append("identifiability")
if pooled.rmse_c > incumbent_metrics.pooled.rmse_c:
    reasons.append("pooled-regression")
```

Append `per-cook-regression:<cook-id>` for every supported candidate cook whose RMSE exceeds the incumbent metric for the same cook. Delete the `insufficient-supported-cooks` branch.

- [ ] **Step 7: Preserve digest and replay authority**

Pass the exact exclusion tuple from `compare_segmented_grey()` to `GreyFitSuccess`. Keep `_result_digest()` based on the complete ordered common masks plus the usable metrics. Update `_independent_result_digest()` in the test to reproduce the exact production payload.

Add a constructor test proving mismatched exclusions or mask cardinality fail closed. Add a replay test proving the same warming corpus reproduces config, masks, exclusions, metrics, and digest.

- [ ] **Step 8: Run focused fit and worker suites**

Run:

```bash
uv run pytest \
  tests/unit/mpc/test_segmented_grey_fit.py \
  tests/unit/mpc/test_grey_fit_worker.py \
  tests/unit/mpc/test_update_mpc.py -q
```

Expected: pass; no assertion accepts masked evidence or removes supported-cook vetoes.

- [ ] **Step 9: Finalize the aggregate-admission commit**

```bash
jj new
```

---

### Task 4: Add Deterministic Fit-Retry Watermarks

**Files:**
- Modify: `controller/model_learning/grey_runtime.py:326-440, 789-1140, 2276-2446`
- Modify: `tests/unit/mpc/test_grey_learning_runtime.py:414-559`
- Modify: `tests/unit/mpc/_grey_learning_runtime_helpers.py`

**Interfaces:**
- Consumes: terminal `GreyFitSuccess.sample_count`, `TriggerConfig.min_effective_duration_s`, `FIT_CADENCE_S`, immutable request corpus slices, partition identity, incumbent digest, and generation.
- Produces: transient `_FitRetryWatermark` and optimizer-submission suppression; no persisted schema.

- [ ] **Step 1: Describe the task revision**

```bash
jj describe -m "Throttle premature MPC refits"
```

- [ ] **Step 2: Write failing runtime tests**

Add tests proving:

```python
def test_effective_duration_rejection_defers_optimizer_until_deficit_can_close(...):
    # A 600-observed-second result has only 400 effective seconds.
    # The next nine 20-second revisions terminalize as not ready without worker submission.
    # The tenth additional frame permits exactly one new submission.


def test_retry_watermark_is_discarded_when_incumbent_or_partition_changes(...):
    # Same corpus duration remains suppressed under matching identity.
    # A changed partition, incumbent digest, or role generation removes the suppression.


def test_restart_may_repeat_one_safe_fit_but_persists_no_retry_authority(...):
    # Construct a fresh runtime over the same repository and verify no durable watermark was restored.
```

Use the existing `_CorpusWorker` probes to count actual worker submissions rather than inspecting log text.

- [ ] **Step 3: Run the tests and verify repeated submission behavior**

Run:

```bash
uv run pytest \
  tests/unit/mpc/test_grey_learning_runtime.py::test_effective_duration_rejection_defers_optimizer_until_deficit_can_close \
  tests/unit/mpc/test_grey_learning_runtime.py::test_retry_watermark_is_discarded_when_incumbent_or_partition_changes \
  tests/unit/mpc/test_grey_learning_runtime.py::test_restart_may_repeat_one_safe_fit_but_persists_no_retry_authority -q
```

Expected: fail because no retry watermark exists.

- [ ] **Step 4: Implement the transient watermark**

Add an immutable private contract near `_CorpusFitIntent`:

```python
@dataclass(frozen=True, slots=True)
class _FitRetryWatermark:
    fit_partition_digest: str
    incumbent_digest: str
    incumbent_generation: int
    through_corpus_revision: int
    required_observed_duration_s: float
```

Store `self._fit_retry_watermark: _FitRetryWatermark | None = None` in `GreyLearningRuntime`.

Compute observed corpus duration from immutable slices inside the fixed-cadence fit contract:

```python
def _corpus_observed_duration_s(identity: FitCorpusIdentity) -> float:
    return sum(item.scored_count for item in identity.slices) * FIT_CADENCE_S
```

When a non-stale terminal delivery has blockers exactly `("minimum-effective-duration",)`, install:

```python
effective_duration_s = outcome.sample_count * FIT_CADENCE_S
deficit_s = learning.trigger_config.min_effective_duration_s - effective_duration_s
required = current_observed_duration_s + math.ceil(deficit_s / FIT_CADENCE_S) * FIT_CADENCE_S
```

Tie it to the request partition, parent incumbent digest, parent incumbent generation, and corpus revision.

- [ ] **Step 5: Suppress only premature worker submission**

After taking the immutable snapshot and before `persistent_corpus_trigger()`, compare a matching watermark with current observed duration. If duration is below the watermark, call `_terminalize_not_ready_corpus_fit()` with `minimum-effective-duration` and do not construct or submit a worker job.

Clear the watermark when:

- its partition differs;
- incumbent digest differs;
- incumbent generation differs;
- the threshold has been reached and a new fit is submitted;
- a candidate is prepared;
- runtime closes.

Do not persist it. Do not suppress fits for excitation, coverage, identifiability, regression, fit errors, operator calibration, or stale results.

- [ ] **Step 6: Run runtime and restart tests**

Run:

```bash
uv run pytest tests/unit/mpc/test_grey_learning_runtime.py tests/unit/runtime/test_hold_learning_runtime.py -q
```

Expected: pass with exactly the asserted worker submission counts.

- [ ] **Step 7: Finalize the retry commit**

```bash
jj new
```

---

### Task 5: Migrate Diagnostics, Experiments, and Dashboard Copy

**Files:**
- Modify: `tools/experiments/promotion_signal.py:122-145`
- Modify: `web-react/src/components/dashboard/learning/MpcLearningView.tsx:69-103`
- Modify: `web-react/tests/unit/components/dashboard/MpcLearningView.test.tsx:540-595`
- Modify: Python tests/helpers containing current `minimum-samples` or `insufficient-supported-cooks`
- Regenerate only if changed schema inputs require it: `web-react/src/types/generated/api-contract.ts`

**Interfaces:**
- Consumes: current blocker names and `TriggerConfig.min_effective_duration_s`.
- Produces: duration-accurate experiment scope and user-visible remediation text; no current legacy blocker emission.

- [ ] **Step 1: Describe the task revision**

```bash
jj describe -m "Report time-based MPC evidence"
```

- [ ] **Step 2: Write failing dashboard behavior tests**

Change the status fixture to emit `minimum-observed-duration` and assert the user sees time-based guidance:

```typescript
expect(readiness).toHaveTextContent(
  "Learning is still collecting enough usable cooking time.",
);
expect(readiness).toHaveTextContent(
  "Continue normal cooks; the existing evidence remains saved.",
);
expect(readiness).toHaveTextContent(
  "Technical code: minimum-observed-duration",
);
```

Add equivalent coverage for `minimum-effective-duration`. Remove current tests that pin `minimum-samples` as a current reason. Do not test source text or mapping implementation.

- [ ] **Step 3: Run the focused UI test and observe failure**

Run:

```bash
bun --cwd web-react run test tests/unit/components/dashboard/MpcLearningView.test.tsx
```

Expected: fail because the new reasons lack explicit copy.

- [ ] **Step 4: Replace current blocker mappings**

Use current mappings:

```typescript
"minimum-observed-duration": {
  summary: "Learning is still collecting enough usable cooking time.",
  action: "Continue normal cooks; the existing evidence remains saved.",
},
"minimum-effective-duration": {
  summary: "Some collected cooking time is still warming the thermal model and cannot be scored yet.",
  action: "Continue normal cooking; masked warm-up evidence is never treated as a measurement.",
},
"pooled-regression": {
  summary: "The new model fits the collected cooks worse than the active model.",
  action: "The active model remains in use while later cooks add evidence.",
},
```

Remove current `minimum-samples` and `insufficient-supported-cooks` mappings. Historical-schema rendering may retain historical descriptions only in its versioned decoder; do not route current status through them.

- [ ] **Step 5: Correct the production experiment’s unit**

Replace the row-derived gate:

```python
_NOMINAL_GATE_DURATION_S = TriggerConfig().min_effective_duration_s
```

Delete `_EFFECTIVE_ROW_GATE` and `_EFFECTIVE_ROW_PERIOD_S`. Update output labels and scope comments to describe 600 effective seconds. Continue reporting effective row counts as diagnostics where useful.

- [ ] **Step 6: Remove obsolete current reason literals repository-wide**

Search current controller, tests, tools, and web code for `minimum-samples` and `insufficient-supported-cooks`. Remaining occurrences must be either:

- an explicit historical migration fixture; or
- a negative assertion proving current emitters cannot produce them.

Do not rewrite historical experiment findings that accurately describe the old policy.

- [ ] **Step 7: Run focused UI, experiment-import, and type checks**

Run:

```bash
bun --cwd web-react run test tests/unit/components/dashboard/MpcLearningView.test.tsx
bun --cwd web-react run typecheck
uv run python -m py_compile tools/experiments/promotion_signal.py
```

If any generated API schema changed, additionally run:

```bash
bun --cwd web-react run gen:types
bun --cwd web-react run gen:types:check
```

Expected: all commands pass; generated output is clean after one regeneration.

- [ ] **Step 8: Finalize the diagnostics commit**

```bash
jj new
```

---

### Task 6: Prove End-to-End Admission and Preserve Activation Authority

**Files:**
- Modify: `tests/e2e/test_cumulative_mpc_learning.py`
- Modify: `tests/e2e/test_short_cook_mpc_admission.py`
- Modify: `tests/e2e/_short_cook_mpc_admission_helpers.py`
- Modify if required by exact replay assertions: `tests/e2e/test_smoke_hold_learning_trajectory.py`
- Modify if required by current-contract fixtures: `_`-prefixed helpers under `tests/unit/mpc/`

**Interfaces:**
- Consumes: time-based trigger, aggregate fit admission, warm-up exclusions, retry watermark, and unchanged prospective evaluator.
- Produces: permanent end-to-end proof that short cumulative cooks build only a shadow challenger and that activation still requires five horizons and two complete wins.

- [ ] **Step 1: Describe the task revision**

```bash
jj describe -m "Prove cumulative short-cook MPC admission"
```

- [ ] **Step 2: Write the failing production-lifecycle regression**

Add an E2E test that persists three distinct 59-frame cooks into one compatible partition, requests the production passive fit, and asserts:

```python
assert fit.rejection_reasons == ()
assert fit.sample_count * FIT_CADENCE_S >= 600.0
assert fit.request.fit_corpus == snapshot.identity
assert prepared.accepted
assert runtime.active_control_pair.descriptor.model_digest == incumbent_digest
assert not runtime.activation_output_authorized
```

Then feed one complete causal winning round and assert the candidate remains shadow-only. Feed the second complete winning round for horizons `(3, 15, 45, 90, 180)`, persist qualification, reconcile activation, and only then assert the learned digest becomes active.

Use actual evaluator observations and durable receipts; do not fake `confidence_accepted=True` past production gates.

- [ ] **Step 3: Run the E2E test and verify the pre-fix contract failure**

Run:

```bash
uv run pytest tests/e2e/test_cumulative_mpc_learning.py -q
```

Expected before the complete migration: failure at the obsolete per-cook support blocker or duration authority.

- [ ] **Step 4: Complete all current-contract fixture migrations**

Update shared builders to import production `FIT_CADENCE_S` and the production default duration. Builders expose no threshold override. Keep historical payloads literal in the tests that own their schema history.

Ensure serialized and replayed fit evidence preserves:

- ordered corpus slices;
- complete common masks;
- warming exclusions;
- candidate and incumbent metrics;
- candidate config;
- result digest;
- terminal fit status;
- prospective evaluation lineage.

- [ ] **Step 5: Run focused contract and E2E suites**

Run:

```bash
uv run pytest \
  tests/unit/mpc/test_segmented_grey_fit.py \
  tests/unit/mpc/test_grey_fit_worker.py \
  tests/unit/mpc/test_grey_online_learning.py \
  tests/unit/mpc/test_grey_learning_runtime.py \
  tests/e2e/test_cumulative_mpc_learning.py \
  tests/e2e/test_smoke_hold_learning_trajectory.py -q
```

Then run the permanent slow characterization:

```bash
uv run pytest -m slow tests/e2e/test_short_cook_mpc_admission.py -q
```

Expected: all pass without weakening evidence, replay, mutation, or activation assertions.

- [ ] **Step 6: Finalize the end-to-end commit**

```bash
jj new
```

---

### Task 7: Final Contract Verification

**Files:**
- Modify only if verification identifies a defect in code already changed by Tasks 1–6.
- Do not create additional documentation; the approved spec and this plan are authoritative.

**Interfaces:**
- Consumes: complete implementation commits.
- Produces: focused, hook, contract-preflight, default-suite, slow-suite, web-workspace, and browser-E2E evidence for one unchanged final revision.

- [ ] **Step 1: Describe any final correction revision before editing**

If no correction is needed, remain on the empty working-copy revision. If verification exposes a defect:

```bash
jj describe -m "Fix time-based MPC admission verification"
```

Fix the source defect rather than suppressing or special-casing the failing input.

- [ ] **Step 2: Run the required contract preflight first**

```bash
uv run pytest \
  tests/unit/test_no_cross_test_imports.py \
  tests/unit/mpc/test_mutation_score.py \
  tests/unit/common/test_current_contract_fixtures.py -q
```

Expected: pass. Preserve its output separately if preparing exact-revision publication evidence.

- [ ] **Step 3: Run commit-time hooks explicitly**

```bash
prek run --all-files
```

Expected: pass. Jujutsu does not run these hooks automatically.

- [ ] **Step 4: Run the complete default Python suite**

```bash
uv run pytest tests/
```

Expected: pass with the repository’s default `not slow` selection.

- [ ] **Step 5: Run the complete slow Python suite**

```bash
uv run pytest -m slow tests/
```

Expected: pass, including both simulator plants and exact 600-second characterization.

- [ ] **Step 6: Run web workspace tests and browser E2E**

```bash
bun run test
bun --cwd web-react run test:e2e
```

Expected: pass.

- [ ] **Step 7: Verify task scope with Jujutsu**

```bash
jj --no-pager status
jj --no-pager log -r 'trunk()..@' --summary
```

Expected: working copy empty; implementation commits contain only the approved admission migration, its tests, generated contracts if required, and the two approved design documents.

- [ ] **Step 8: Stop before publication**

Do not move `massive-reworks-and-new-ui` or push unless the user explicitly requests publication. When publication is requested, use `scripts/exact_revision_gate.py` as the sole command/evidence authority and the repository `jj push` wrapper; never run direct `jj git push`.
