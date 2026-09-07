# Time-Based Cumulative MPC Admission

## 1. Purpose

Restore the original evidence timing for passive MPC learning while preserving every existing activation and actuator-safety boundary.

The original refit floor was 120 observations at a five-second cadence: 600 seconds. The current cumulative trajectory contract emits one scored frame every 20 seconds, but `TriggerConfig.min_samples` still defaults to 120. The same nominal threshold therefore changed from ten minutes to forty minutes. The segmented fitter also refuses every candidate unless at least one individual cook supplies 120 effective frames, even when several shorter compatible cooks jointly identify a model that is materially better than the uncalibrated incumbent.

The replacement contract is:

> Six hundred aggregate effective seconds may build a shadow challenger. Individually supported cooks may veto it. Only untouched prospective causal wins may qualify it for activation.

This change governs candidate construction, not activation.

## 2. Evidence

A throwaway production-path simulator probe exercised `GrillSim` and `MAKGrillSim` with three independent 59-frame training cooks, exact pre-roll, and five unseen seeds. Each scored frame covered 20 seconds.

All training cooks reached 225°F and continued in Hold after first entry:

- `GrillSim`: first entered the ±5°F band at frame 5 and had 55 scored frames remaining; all remaining frames stayed within ±15°F.
- `MAKGrillSim`: first entered the ±5°F band at frame 22 and had 38 scored frames remaining; 37–38 remaining frames stayed within ±15°F. Its persistent positive error was produced by the uncalibrated controller.

At raw counts `(59, 59, 2)`, the Grill candidate had 120 effective rows. Against the uncalibrated model on five unseen seeds, its candidate/incumbent RMSE ratios were 0.190, 0.590, and 0.341 at horizons 3, 15, and 45; whole-cook RMSE ratio was 0.244; closed-loop IAE ratio was 0.695; and overshoot fell from 6.388°C to 0.045°C.

For MAK, the model fitted from the first two 59-frame cooks had 98 effective rows. Its unseen forecast ratios were 0.032, 0.017, and 0.054; whole-cook ratio was 0.009; closed-loop IAE ratio was 0.863; and overshoot fell from 8.246°C to 0.395°C. Every measured comparison won on all five unseen seeds.

The probe does not establish that every candidate at exactly 600 effective seconds is better. That boundary remains an implementation characterization requirement. It does establish that the present per-cook 120-frame gate rejects useful cumulative evidence and that an early shadow challenger can be substantially better than the uncalibrated incumbent.

## 3. Goals

1. Express the fit floor in elapsed effective evidence time, not frame count.
2. Permit compatible segments from one or many cooks to build a shadow challenger after 600 effective seconds.
3. Preserve conservative retrospective per-cook regression vetoes when a cook independently carries enough evidence.
4. Prevent a newly opened, still-warming segment from invalidating older usable segments.
5. Preserve exact corpus identity, candidate-dependent warm-up, replay, stale-result fencing, prospective causal qualification, durable activation, rollback, and fallback.
6. Avoid repeated expensive fits when a known effective-duration deficit cannot yet have closed.
7. Keep the active controller unchanged until every existing prospective and durable activation gate passes.

## 4. Non-goals

- Do not reduce prospective causal horizons or required consecutive wins.
- Do not let fit-corpus observations authorize activation.
- Do not reconstruct unknown delay state or admit masked rows.
- Do not add evidence aging, weighting, quarantine, or cook-count heuristics.
- Do not change model equations, optimizer bounds, fitted parameters, retention limits, or compatibility partitions.
- Do not change PID-SP learning.
- Do not introduce a manual-review or manual-activation path.
- Do not reinterpret historical evidence records as newly accepted candidates.

## 5. Current contract and defect

`TriggerConfig.min_samples` is 120. `persistent_corpus_trigger()` applies it to raw eligible frames before fitting. After candidate-dependent warm-up, `_cook_evidence_supported()` applies the same count to each cook. `compare_segmented_grey()` then requires at least one supported cook and otherwise emits `insufficient-supported-cooks`.

The current behavior has three defects:

1. The floor is cadence-dependent and now represents forty minutes instead of the original ten.
2. Pooled compatible evidence cannot construct a challenger unless one cook independently reaches the full count.
3. `compare_segmented_grey()` fails the complete fit when any segment has no common-mask row, even though that segment contributes no residual and older segments remain valid.

The prospective evaluator already supplies the independent authority the fit gate lacks: a challenger must beat the incumbent at horizons 3, 15, 45, 90, and 180 for two consecutive complete rounds before qualification.

## 6. Evidence-time contract

### 6.1 Configuration

Replace the public configuration field:

```python
min_samples: int = 120
```

with:

```python
min_effective_duration_s: float = 600.0
```

There is no alias and no schema override in current builders. Every constructor, helper, experiment, test, and controller consumer migrates in the same change.

The duration must be finite and strictly positive. Admission code compares seconds, never a hard-coded equivalent row count.

### 6.2 Observed duration

Observed duration is the sum of eligible scored interval durations before candidate/incumbent common warm-up masks. It is an optimistic scheduling signal only.

`persistent_corpus_trigger()` becomes ready to request a passive fit when compatible raw evidence contains at least 600 observed seconds and passes the existing raw excitation, level-count, coverage, and continuity checks.

The trigger cannot claim that 600 seconds are effective because candidate `theta` does not exist until after fitting.

### 6.3 Effective duration

Effective duration is the sum of scored interval durations selected by the conservative common mask:

```text
common_mask = candidate_warmup_mask AND incumbent_warmup_mask
```

The current fit contract requires every scored interval to match `FIT_CADENCE_S`, presently 20 seconds. Implementations may derive duration from effective row count and `FIT_CADENCE_S` inside the fitter, but the policy input and comparison remain seconds. Code outside that fixed-cadence contract must sum the actual validated interval durations.

Only effective duration authorizes construction of a challenger.

## 7. Aggregate build admission

After fitting and freezing the common warm-up masks, the pooled usable corpus must satisfy all of the following:

1. effective duration is at least 600 seconds;
2. input variance meets the existing minimum;
3. distinct input levels meet the existing minimum;
4. temperature span meets the existing minimum;
5. pooled identifiability meets the existing minimum;
6. candidate pooled RMSE is no greater than incumbent pooled RMSE on the exact common masks.

The optimizer uses independently initialized segments and one shared parameter vector exactly as today. Cook boundaries, process restarts, and gaps never carry thermal state.

One cook or several compatible cooks may supply the pooled duration. The number of cooks is not an admission threshold.

Pooled admission authorizes only native preparation and prospective evaluation. It does not qualify or activate the model.

## 8. Per-cook regression veto

A cook is independently supported when its usable compatible segments jointly provide:

- at least 600 effective seconds;
- existing minimum excitation;
- existing minimum input-level count;
- existing minimum temperature span;
- existing minimum identifiability.

For every independently supported cook, candidate RMSE must be no greater than incumbent RMSE on the exact common masks. Any regression emits the existing cook-specific form:

```text
per-cook-regression:<cook-id>
```

Shorter cooks may contribute pooled residuals and pooled identifiability, but cannot independently bless or veto the candidate. Absence of an independently supported cook is not itself a rejection once the aggregate build gate passes.

This preserves the conservative veto where local evidence is strong while allowing truly cumulative evidence to create a shadow challenger.

## 9. Warming-segment projection

A segment whose common mask contains no effective row has unknown usable state for this candidate. It must contribute nothing, but it must not invalidate unrelated segments.

The fitter must:

1. retain the segment and its rows in the immutable request corpus;
2. retain its all-false common mask in the ordered effective-mask result;
3. omit it from pooled, per-segment, and per-cook numerical metrics;
4. report its identity in an ordered `warmup_excluded_segment_ids` result field;
5. require that this field exactly equal the segment identities whose common masks are all false;
6. bind the masks, and therefore the exclusion, into the existing result digest;
7. return `InsufficientWarmup` only when no segment has an effective row.

If one cook contains both excluded and usable segments, its cook metric is computed only from its usable segments.

A later corpus revision naturally re-fits the segment when it acquires common-mask rows. No previous result is mutated.

This is exclusion, not imputation: unknown delay state remains unable to create evidence.

## 10. Rejection reasons

Current emitters stop producing `minimum-samples` and `insufficient-supported-cooks`.

Current decisions use:

- `minimum-observed-duration` before fitting;
- `minimum-effective-duration` after common-mask construction;
- `insufficient-excitation`;
- `insufficient-coverage`;
- `identifiability`;
- `pooled-regression`;
- `per-cook-regression:<cook-id>`;
- existing fit, native-build, dry-solve, target-timing, stale-lineage, causal-evaluation, and activation reasons.

Historical evidence retains its original reason strings. Historical schema decoders may display them as historical records, but no current builder or runtime emitter may generate them.

Dashboard text for duration blockers must describe usable cooking time rather than frame count or an individual-cook requirement.

## 11. Retry scheduling

The raw trigger may run before 600 seconds survive candidate-dependent warm-up. Re-running the optimizer every 20 seconds would waste controller resources.

When a completed fit is rejected only because of `minimum-effective-duration`, compute:

```text
deficit_s = 600 - effective_duration_s
retry_observed_duration_s = current_observed_duration_s
                            + ceil_to_fit_cadence(deficit_s)
```

The runtime stores this retry watermark transiently with:

- fit partition digest;
- incumbent digest and generation;
- current corpus revision;
- required observed duration.

A passive request remains suppressed until the compatible corpus reaches the watermark. Configuration, partition, incumbent, or generation change discards it. Process restart may lose it and perform one extra safe fit; no durable admission state depends on it.

A later fit may derive a different `theta` and a new deficit. In that case it installs a new watermark rather than assuming the earlier mask remains valid.

Other evidence blockers continue to use existing scheduling behavior because one new frame may genuinely change excitation, coverage, or identifiability.

## 12. Candidate lifecycle

The accepted aggregate fit follows the existing lifecycle without shortcuts:

```text
compatible corpus
  -> raw duration trigger
  -> immutable segmented fit
  -> common-mask aggregate admission
  -> supported-cook vetoes
  -> native build
  -> finite dry solve
  -> target-hardware timing
  -> BUILT/EVALUATING shadow challenger
  -> prospective horizons 3, 15, 45, 90, and 180
  -> two consecutive complete winning rounds
  -> QUALIFIED
  -> durable PREPARED activation
  -> frame-boundary ACTIVE
```

Candidate output cannot reach hardware before durable `ACTIVE`. Existing stale-result checks, lineage matching, activation compensation, rollback, fallback, and crash recovery remain authoritative.

A causal loss continues to reset consecutive wins. Missing, discontinuous, calibration, stale-generation, or digest-mismatched forecast evidence remains inadmissible.

## 13. Replay and contract migration

This is a repository-wide contract migration.

The migration inventory includes:

- `TriggerConfig` constructors and test helpers;
- raw and persistent trigger paths;
- segmented support and comparison functions;
- `GreyFitComparison` and `GreyFitSuccess` construction and validation;
- worker messages and runtime delivery;
- fit-result digest generation and replay;
- retry scheduling and status reporting;
- diagnostics and cookfile replay;
- model evidence reports;
- generated web contracts and dashboard reason text;
- smoke tools and `tools/experiments/promotion_signal.py`;
- mutation expectations and characterization tests;
- cumulative, closed-loop, real-cook, restart, and retention E2E paths.

`sample_count` remains diagnostic metadata. It no longer determines admission.

The ordered effective masks already bind excluded rows into the numerical result digest. `warmup_excluded_segment_ids` is validated as a redundant readable projection of those masks, not an independent authority.

Existing terminal historical fit and assessment records remain terminal. Upgrade does not re-admit or reactivate them under the new policy. New fits may reuse compatible retained trajectory evidence through a new immutable request and corpus revision.

No controller-model schema version changes unless implementation inventory proves that the new readable exclusion projection enters that durable payload. The preserved `MODEL_SCHEMA = 7` and migration-input mutation anchors remain unchanged otherwise.

## 14. Failure handling

- Fewer than 600 observed compatible seconds: do not submit a worker fit.
- Fewer than 600 effective seconds: reject construction, preserve active control, and install the retry watermark.
- No effective segment: return typed `InsufficientWarmup`; preserve active control.
- Pooled evidence gate failure: reject construction with exact blockers.
- Pooled or supported-cook regression: reject construction with exact reasons.
- Worker, optimizer, native-build, dry-solve, or timing failure: retain current fail-closed behavior.
- Stale corpus, configuration, incumbent, or generation: discard the result through existing stale-result authority.
- Corrupt or non-replayable evidence: quarantine or reject through existing strict corpus behavior; never reinterpret it as a duration shortfall.

No failure in learning changes the active controller.

## 15. Verification

### 15.1 Duration boundaries

Verify:

- 599.999 effective seconds blocks construction;
- 600 effective seconds passes the duration gate;
- equivalent duration split over compatible segments produces the same pooled decision;
- masked pre-roll and leading Hold rows do not count;
- changing candidate `theta` changes effective duration only through the exact common mask.

### 15.2 Aggregate and per-cook behavior

Verify:

- several individually short cooks totaling 600 effective seconds may build a challenger;
- no individually supported cook is required for shadow construction;
- a supported cook can veto a pooled improvement;
- a short noisy cook cannot independently veto;
- pooled regression blocks when no cook is independently supported;
- pooled excitation, coverage, and identifiability remain strict.

### 15.3 Warming segments

Verify:

- an all-masked current segment is reported in `warmup_excluded_segment_ids`;
- it contributes zero residual, duration, excitation, coverage, identifiability, and regression authority;
- older usable segments continue to fit;
- all-excluded corpora return `InsufficientWarmup`;
- a later immutable revision includes the segment after warm-up;
- result replay reproduces masks, exclusions, metrics, configuration, and digest exactly.

### 15.4 Lifecycle safety

Verify:

- retrospective admission produces only a shadow challenger;
- no candidate actuation occurs before durable activation;
- one causal loss resets confidence under the existing policy;
- only two complete winning rounds across horizons 3, 15, 45, 90, and 180 qualify the challenger;
- stale, discontinuous, calibration, restart-crossing, and mismatched-lineage origins remain inadmissible;
- rollback and fallback behavior are unchanged.

### 15.5 Simulator characterization

Use production `GrillSim`, `MAKGrillSim`, the production segmented fitter, and the production MPC controller with:

- three independent 59-frame training cooks;
- exact pre-roll excluded from scored duration;
- candidate snapshots at the first 600 effective seconds and at full `3 × 59` evidence;
- unseen forecast and closed-loop seeds.

The characterization must report, without weakening any gate:

- setpoint-entry frame and post-entry duration for every training cook;
- candidate and incumbent RMSE at horizons 3, 15, 45, 90, and 180;
- whole-cook RMSE;
- closed-loop IAE and overshoot;
- per-seed win counts;
- raw, masked, and effective duration;
- warm-up-excluded segment identities.

The change is acceptable only if both simulators produce an early candidate that beats the uncalibrated incumbent on the required unseen comparisons and no candidate controls hardware before prospective qualification. If the exact 600-second boundary fails this characterization, the duration threshold must be re-established from evidence rather than weakened assertions or bypassed gates.

### 15.6 Repository gates

Run the contract preflight required by `AGENTS.md`, then the affected focused suites, formatter/linter hooks, and all authoritative release commands when the implementation is prepared for publication. Evidence admission, replay, mutation anchors, and exact-revision requirements remain strict.

## 16. Acceptance criteria

The design is implemented only when all are true:

1. Current code contains no admission decision based on `min_samples` or a literal 120-row floor.
2. The production default is 600 effective seconds.
3. Compatible evidence from multiple cooks can construct a shadow challenger.
4. Supported-cook regression vetoes remain active.
5. A warming segment cannot invalidate older usable segments and cannot contribute evidence itself.
6. The retry watermark prevents provably premature repeated fits.
7. Current status and UI language describe durations accurately.
8. Historical records remain historical and cannot authorize new activation.
9. Replay reproduces exact corpus, masks, exclusions, candidate, metrics, and digest.
10. Prospective causal qualification and durable activation are unchanged.
11. The permanent simulator characterization proves the new boundary against the uncalibrated model on unseen seeds.
12. No test weakens continuity, exact delivery, warm-up, identifiability, regression, lineage, or activation assertions to obtain a pass.
