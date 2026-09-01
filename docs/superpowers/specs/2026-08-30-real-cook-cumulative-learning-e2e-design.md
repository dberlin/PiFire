# Real-Cook Cumulative-Learning E2E Design

## Goal

Add permanent deterministic tests that prove cumulative controller learning consumes exact real-cook evidence, survives restart, rejects incompatible evidence, and authorizes a learned model only after held-out prediction and closed-loop safety checks.

The suite must catch failures in collection, terminal delivery, persistence, fitting, candidate lineage, evaluation, activation, and restore. It must not force the August 28 PID-SP cook to produce a model when its evidence is not identifiable.

## Approved evidence set

Commit five sanitized cook archives and three unique sanitized diagnostic database baselines. The two duplicate diagnostic downloads are byte-identical at the database level and must not become duplicate fixtures.

| Campaign | Diagnostic baseline | Ordered matching cooks | Controller |
|---|---|---|---|
| `mpc-aug27` | `PiFire_Diagnostics_20260827-202055.zip` | `2026-08-22--1636-CookFile.pifire`, then `2026-08-27--2015-CookFile.pifire` | MPC |
| `pid-sp-aug28` | `PiFire_Diagnostics_20260828-210051.zip` | `2026-08-28--1931-CookFile.pifire` | PID-SP |
| `mpc-aug29` | `PiFire_Diagnostics_20260829-Today.zip` | `2026-08-29--1219-CookFile.pifire`, then `2026-08-29--1625-CookFile.pifire` | MPC |

This mapping is accepted because it is date/controller coherent and its MPC fits remain in one physical range rather than producing severely different grill models:

- August 27 campaign candidates observed approximately `C_c=636.83..708.23`, `K_Q=254.47..299.02`, `theta=59.00..82.50`.
- August 29 cumulative candidate observed approximately `C_c=615.66`, `K_Q=263.02`, `theta=66.57`.

The parameter overlap is pairing evidence, not an activation criterion. Tests must still apply compatibility, per-cook regression, prediction, safety, and persistence gates.

## Fixture contract

Store fixtures under `tests/fixtures/real_cook_learning/`:

```text
tests/fixtures/real_cook_learning/
  manifest.json
  cookfiles/
    2026-08-22--1636.pifire
    2026-08-27--2015.pifire
    2026-08-28--1931.pifire
    2026-08-29--1219.pifire
    2026-08-29--1625.pifire
  baselines/
    mpc-aug27.sqlite
    pid-sp-aug28.sqlite
    mpc-aug29.sqlite
```

`manifest.json` is the authority for campaign membership, order, controller, source SHA-256, sanitized SHA-256, trace schema, cook start/end, expected input-frame count, and baseline schema version.

Sanitization removes:

- network, host, path, credential, notification, account, and device-identifying fields;
- unrelated logs, history, recipes, food-probe names, and UI state;
- unrelated SQLite tables and rows;
- duplicate diagnostics and generated reports that the production code under test must recompute.

Sanitization retains:

- chamber samples and timestamps;
- exact actuation-frame interval boundaries, requested output, realized auger/fan delivery, completeness, continuity, and typed boundary reasons;
- controller/mode transitions needed to reproduce segment ownership;
- controller configuration and compatibility digests that affect learning;
- model, evidence, trajectory, fit-request, and checkpoint rows needed to reproduce baseline state;
- cook ID, trace session identity, role generation, and causal revisions rewritten to deterministic fixture-local values while preserving joins.

Every fixture test verifies the sanitized SHA-256 before use. No test reads `/home/dannyb`, `~/Downloads`, `/tmp`, or the source zip after fixture generation.

## Test architecture

### Layer 1: Deterministic exact-evidence replay

Create `tests/e2e/real_cook_replay.py` as a test-only adapter. It decodes the archived production actuation-frame records into immutable `FrameObservation` values and submits them through the production `ControllerRunner`, `HoldLearningRuntime`, `LearningTrajectoryRepository`, evidence persistence, model lifecycle, and model store.

This layer intentionally does not ask a newly running controller to recreate the historical actuator schedule. Recomputing duty from temperature alone changes the input and is not a replay of the cook. Earlier exploratory replays proved this: two byte-identical August 27 database baselines produced the same corpus counts but 288 of 1,601 canonical frames differed only in delivered auger/load/duty and ended with different active model digests. The permanent replay therefore owns the archived exact interval as input and tests the learner/lifecycle against that immutable evidence.

Production boundaries are not mocked:

- production observation validation and outcome envelopes;
- production trace and evidence serialization;
- production trajectory segmentation and compatibility partitions;
- production SQLite repositories and persistence worker;
- production fitter, candidate lineage, evaluation, activation, and restore;
- production Stop/Error finalization and worker drain barriers.

Allowed seams:

- a monotonic/wall virtual clock that preserves original interval differences;
- deterministic executor barriers in place of wall-clock sleeps;
- fixture-local paths and in-memory logging capture.

Forbidden seams:

- monkeypatching learner outcomes, fitted parameters, assessment results, blockers, persistence receipts, or model snapshots;
- replacing SQLite repositories with dictionaries;
- changing frame order, timestamps, realized duty, completeness, or compatibility digests;
- accepting a candidate because the fixture expects one.

### Layer 2: Full Hold runtime smoke

Keep the existing synthetic full-Hold E2E in `tests/e2e/test_smoke_hold_learning_trajectory.py` responsible for pulse scheduling, terminal feedback ordering, runner adoption, and teardown barriers. Add one real-cook smoke per controller that streams chamber samples through the actual Hold runtime and verifies completion, no crashes, no open segments, and fail-safe output.

These smoke tests do not assert exact fitted model digests because rerunning control changes realized actuator delivery and creates a new experiment. Exact model assertions belong to Layer 1.

### Layer 3: Independent closed-loop simulator validation

Real recorded cook evidence proves learning and persistence, not future control performance. Add deterministic GrillSim and MAKGrillSim campaigns that create a candidate on one training trajectory and validate it on separate seeds before and after activation.

For each simulator family:

1. Train on seeds `0..4` through the production observation/fitting path.
2. Evaluate held-out prediction on seeds `5..9` without authorizing output.
3. If all gates pass, cold-restart the checkpoint and run closed-loop steady 225°F, steady 450°F, and 225→275°F scenarios on seeds `5..9`.
4. Transplant the accepted model into the other simulator family as a deliberate mismatch and rerun the same scenarios.

This separates real-cook regression coverage from a controlled experiment with known matched and mismatched plants.

## Matched campaign contracts

Each campaign starts from a copy of its sanitized baseline database. Cooks run in manifest order against the same evolving copy. Restart the production repositories and controller runner between cooks without clearing durable state.

For every cook assert:

- exactly one fixture frame maps to exactly one submitted terminal observation or one typed rejected/gap record;
- sequence, generation, cook ID, session ID, result revision, and frame boundaries remain joined;
- no run/log error and no unaccounted observation loss;
- Stop/Error finalization leaves zero open segments and zero live fit workers;
- cold restart preserves all finalized compatible corpus content and accepted lifecycle state;
- no quarantined segment exists unless the fixture explicitly contains an incompatibility or malformed record;
- database/cookfile diagnostics agree on accepted, rejected, and gap counts.

### `mpc-aug27`

Expected cumulative corpus deltas from the exploratory production replay are:

- August 22: `+1` segment, `+49` pre-roll frames, `+1` scored frame;
- August 27: `+2` segments, `+254` pre-roll frames, `+196` scored frames.

The August 22 cook alone is insufficient evidence and must not force activation. After August 27, a candidate may advance only if its held-out forecasts beat the incumbent and every supported-cook regression gate passes. Cold restart must preserve the exact active/candidate lineage and corpus.

### `pid-sp-aug28`

The cook supplies enough coarse excitation evidence to pass the observed collection gates: approximately 387 accepted samples, 8,127 accepted seconds, duty standard deviation 0.1443, a realized-duty transition, and 214.5°F temperature span.

Those coarse gates do not prove a unique delay or model. The accepted outcome is fail-closed when the PID-SP selector reports insufficient delay identifiability. The test must reject false success and false evidence loss equally:

- no forced checkpoint or predictor activation;
- typed accepted/rejected observations rather than one `runner-no-observation-outcome` per frame once the PID-SP observation contract is implemented;
- retained compatible evidence available to a later independent cook;
- exact blocker and candidate-form comparison disclosed.

Add the PID-SP fixture test atomically with the typed PID-SP observation contract from the separate PID-SP defect plan. Under TDD, the initial replay must fail on the current false gaps; the committed permanent expectation is typed observations and no false `runner-no-observation-outcome` records. Never commit a regression test that treats the known gap defect as correct.

### `mpc-aug29`

Expected cumulative corpus deltas are:

- August 29 12:19: `+2` segments, `+351` pre-roll frames, `+296` scored frames;
- August 29 16:25: `+2` segments, `+208` pre-roll frames, `+246` scored frames.

The final exploratory candidate had `C_c≈615.66`, `K_Q≈263.02`, `theta≈66.57` and remained blocked by per-cook regression plus an assessment-digest mismatch. The permanent test must not bless that candidate. It asserts that digest mismatches abort assessment, per-cook regressions retain the incumbent, and a later coherent fit may proceed without deleting supported cook history.

## Held-out prediction contract

A candidate is prediction-eligible only after fitting on a strict prefix of the matching corpus. Evaluate on later frames that did not contribute to the fit.

For each horizon `3`, `15`, `45`, `90`, and `180` seconds, persist candidate and incumbent error from the same frame identities. Require:

- finite errors and complete horizon coverage;
- candidate weighted aggregate loss strictly below incumbent loss;
- no supported cook with regression beyond the production tolerance;
- candidate assessment digest, fit-corpus digest, parent incumbent digest, and role generation exactly match the lifecycle candidate;
- repeated evaluation of identical evidence produces identical canonical assessment bytes.

The August 27 exploratory candidate met this shape: weighted score about 114.26 versus 152.64, with lower RMSE at each of the five horizons. These values are evidence for thresholds and fixture validation, not broad equality assertions; permanent tests assert the production comparison and canonical identities rather than floating-point snapshots of an exploratory implementation.

## Closed-loop acceptance contract

Compare each authorized matched model with the same controller forced to measured-temperature fallback on identical simulator, scenario, and seed.

A matched model passes only when all are true:

- zero safety events, actuator contract violations, stale-output authorizations, or persistence/restore errors;
- integrated absolute error is no worse than fallback for every simulator/scenario aggregate;
- overshoot is no worse than fallback for every simulator/scenario aggregate;
- fuel use does not exceed fallback by more than 5% unless integrated absolute error improves by at least 10%;
- no individual seed exceeds the configured chamber-temperature safety ceiling;
- the restored model is active before the first authorized output and its digest matches the durable checkpoint.

A transplanted mismatched model passes only by failing safe:

- it is rejected before output by compatibility/validation, or the predictor disables and falls back;
- zero safety events and ceiling violations;
- no stale or mismatched model digest authorizes output;
- evidence records the exact rejection/fallback reason.

The test does not require a mismatched model to improve control.

## Duplicate and determinism contracts

The discarded duplicate August 27 and August 28 downloads are used once during fixture generation to prove database equality. They are not committed.

Permanent determinism assertions:

- fixture manifest hashes are stable;
- two fresh copies of one baseline plus the same exact frame sequence yield byte-identical canonical corpus, fit request, candidate assessment, and model lifecycle records;
- model/evidence primary identities are deterministic fixture-local identities, not random UUIDs;
- database row ordering is normalized before canonical comparison;
- timestamps come only from fixture clocks.

Any duplicate replay divergence is a test failure, not an accepted tolerance.

## Failure and recovery matrix

Add explicit cases for:

- observation queue eviction;
- discontinuity and missing probe;
- evidence persistence unavailable before submission;
- evidence persistence failing after learner completion;
- fit worker failure;
- stale fit result after reconfiguration;
- candidate assessment-digest mismatch;
- per-cook regression;
- Stop and Error terminal paths;
- process restart with an open segment;
- malformed or incompatible checkpoint;
- model persistence failure after qualification.

Each case asserts one terminal outcome, exact trace/evidence reason, no orphan pending observation, no open worker, no unauthorized output, and cold-recovery idempotence.

## Test files

- Create `tests/e2e/real_cook_replay.py`: deterministic fixture decoder and production-boundary driver.
- Create `tests/e2e/test_real_cook_cumulative_learning.py`: matched campaigns, restart, lineage, prediction, mismatch, and failure matrix.
- Create `tests/e2e/test_learned_model_closed_loop.py`: GrillSim/MAKGrillSim training, held-out prediction, activation, and transplant safety.
- Modify `tests/e2e/test_smoke_hold_learning_trajectory.py`: one real-cook full-Hold smoke per controller; retain existing terminal-order regressions.
- Create `tests/fixtures/real_cook_learning/manifest.json` and the eight sanitized fixtures listed above.
- Add unit tests only if fixture construction exposes a new isolated contract not already defended by `tests/unit/runtime/test_hold_learning_runtime.py`, `tests/unit/runtime/test_smoke_learning_trajectory.py`, or the controller fitter suites. Do not duplicate the existing terminal feedback, persistence barrier, delayed anchor, rollover, digest, Stop, or Error regressions.

## Verification

Run the fixture/campaign file first, then the closed-loop file, then the existing focused unit/E2E set:

```bash
uv run pytest -q tests/e2e/test_real_cook_cumulative_learning.py
uv run pytest -q tests/e2e/test_learned_model_closed_loop.py
uv run pytest -q \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/unit/runtime/test_smoke_learning_trajectory.py \
  tests/e2e/test_smoke_hold_learning_trajectory.py \
  tests/e2e/test_real_cook_cumulative_learning.py \
  tests/e2e/test_learned_model_closed_loop.py
```

The final suite passes only when every matched campaign, mismatch arm, restart, and terminal failure case is accounted for. No deselection is permitted.

## Non-goals

- No production policy for aging out conflicting grills or quarantining anomalous but structurally valid cooks; that belongs to the separately deferred conflicting-evidence recovery plan.
- No weakening of per-cook regression, safety, compatibility, persistence, confirmation, or checkpoint gates.
- No forced adoption of the August 28 PID-SP cook.
- No snapshot assertion on exploratory random model digests.
- No dependence on the original private archives after fixture generation.
