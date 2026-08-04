# Linear MPC Model Bake-off — Design

**Date:** 2026-08-04  
**Status:** Approved for implementation planning  
**Scope:** Reproducible experiment and recommendation; no production controller changes

## 1. Purpose

Determine whether a low-order linear model can predict and control three plant domains accurately enough for PiFire:

1. `GrillSim`,
2. `MAKGrillSim`, and
3. the measured MAK calibration cook in `tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv`.

The experiment compares three model structures under identical data, adaptation, MPC, and scoring rules. It must establish both control quality and whether parameters can be corrected online within an RPi 5 runtime budget.

This work is independent of the production MPC design. Existing MPC code and prior experiments are evidence, not compatibility requirements.

## 2. Decisions

- All grills share one model structure, but each grill learns separate parameters.
- The manipulated input is realized mean auger duty `q` in `[0, 1]`, treated as one coupled combustion-load signal.
- Calibration mode may deliberately excite the plant. Regular cooks may contribute passive online-learning data but may not receive identification-only perturbations.
- Model accuracy is evaluated at 1, 5, 15, 30, and 60-minute free-run horizons where the source record is long enough. These diagnostic horizons are distinct from the shorter MPC prediction horizon.
- The deliverable is an isolated reproducible experiment, JSON evidence artifact, and recommendation. It does not add a selectable production controller.
- The three experiment arms are scheduled ARX, innovation state-space, and regularized DMC.
- Workstation runtime evidence is acceptable. Raw timing and a conservative `5x` projected RPi 5 timing must both be reported.

## 3. Evidence limits

The real MAK fixture has 247 samples spanning approximately 20.7 minutes. It can supply measured evidence only through the horizons present in a chronological test segment. The experiment must report unavailable real-data horizons as unavailable; it must not extrapolate a measured 30 or 60-minute score.

The fixture records firing-rate request `Q`, not physical auger state. During that cook, `Q_min=5`, `Q_max=100`, `u_min=0.1`, `u_max=0.9`, the fan was fixed at 100%, and Hold used a 25-second cycle. Its reconstructed realized mean auger duty is:

\[
q_{\mathrm{reconstructed}} =
u_{\min} +
\operatorname{clip}\left(\frac{Q-Q_{\min}}{Q_{\max}-Q_{\min}},0,1\right)
\cdot (u_{\max}-u_{\min}).
\]

Every score from this fixture must be labeled **requested-input reconstruction**, not realized-actuation evidence.

The fixture cannot identify a fan channel because fan command did not vary. The primary comparison therefore holds simulator fan command at 100% to match the measured domain and models only the resulting coupled combustion-load response. A predefined fan-curve robustness slice may be reported separately, but it cannot affect winner selection or be described as identified from the MAK record.

## 4. Canonical experiment signals

- Identification and control frame: 20 seconds.
- Input: realized mean auger-on fraction `q` in `[0, 1]`, used as the coupled combustion-load signal.
- Output: chamber temperature in degrees Celsius.
- Measured disturbance: ambient temperature when present.
- Missing ambient: fixed recorded/default ambient plus a separately estimated output disturbance.
- Actuator feedback: realized mean `q`, not requested `q`, for simulator closed-loop runs.
- Historical fixture: reconstructed input under Section 3, with provenance retained.

Resampling must preserve energy over each 20-second frame. Temperature is sampled at the frame boundary; binary auger state is integrated to mean duty. Gaps are never filled across an unknown actuation interval.

## 5. Datasets

### 5.1 Simulator calibration datasets

Generate deterministic calibration records independently for `GrillSim` and `MAKGrillSim`. Each record contains:

- low, middle, and high safe combustion-load plateaus;
- bounded PRBS excitation around each plateau;
- an auger-off coast segment;
- enough duration to observe the slow plant's dominant response.

The simulator calibration generator may be exhaustive. A later recommendation may propose a shorter safe real-grill calibration sequence, but this experiment must not claim that sequence was executed on hardware.

### 5.2 Simulator regular-cook datasets

Use disjoint seeds and command trajectories covering:

- reachable 180, 225, 325, 450, and 600 degrees Fahrenheit operating points;
- cold starts;
- steady holds;
- upward and downward setpoint changes;
- coast and braking behavior;
- a lid-open disturbance and post-override recovery.

Unreachable setpoints are recorded as such and excluded rather than truncating a run into apparent success.

### 5.3 Real MAK dataset

Preserve original chronology. Use only horizons supported by the available tail after each prediction origin. The short record is not sufficient by itself to determine a two-hour dominant time constant; uncertainty and unavailable horizons must remain visible.

### 5.4 Splits

All splits are contiguous and chronological:

1. calibration fit segment,
2. later hyperparameter-validation segment,
3. untouched test segment.

Randomly shuffled rows are prohibited. Test data may be read only after model structure, order, delay-selection rules, regularization, and controller weights are fixed. Any test-segment use during fitting invalidates the run.

## 6. Prediction metrics

For every supported horizon, report:

- free-run RMSE;
- maximum absolute error;
- mean bias;
- 90th-percentile absolute error;
- steady-gain error;
- identified delay error where simulator truth exists;
- coast/braking-temperature error.

Primary accuracy targets are:

- simulator RMSE no greater than 2.8 degrees Celsius through 15 minutes;
- simulator RMSE no greater than 5 degrees Celsius through 60 minutes;
- real MAK RMSE no greater than 5 degrees Celsius for every horizon supported by its test segment.

A target miss is evidence, not an automatic disqualification.

## 7. Model arm A — stable scheduled ARX

Use an incremental ARX model:

\[
\Delta T_k =
\sum_{i=1}^{n_a} a_i(T_k)\Delta T_{k-i}
+ \sum_{j=0}^{n_b} b_j(T_k)\Delta q_{k-d-j}
+ c(T_k)(T_{amb,k}-T_k) + e_k.
\]

Candidate orders are `n_a=2..4` and `n_b=1..3`. Candidate transport delays cover 0 through 300 seconds on the 20-second grid.

Use fixed physical temperature regions selected before test evaluation. Interpolate coefficients continuously between adjacent regions. Square-root recursive least squares updates the active neighboring regions only.

Required constraints and guards:

- projected stable poles;
- positive, bounded DC combustion-to-temperature gain;
- covariance floors and ceilings;
- bounded forgetting factor;
- a delay-model bank;
- validation-window hysteresis before changing delay.

The model must convert deterministically to companion state-space form for MPC prediction.

## 8. Model arm B — innovation state-space

Bootstrap a discrete innovation model using deterministic subspace identification with candidate orders 2 through 5. Use a Kalman state update every frame.

Online adaptation uses a bounded rolling buffer and deterministic re-identification every five minutes. After re-identification, align the new realization and state with the incumbent realization before evaluating promotion. Reject a candidate whose alignment cannot preserve the current output prediction within tolerance.

Required constraints and guards:

- eigenvalues projected inside the stability boundary;
- positive, bounded steady combustion-to-temperature gain;
- bounded process and measurement covariance;
- deterministic order selection using calibration/validation data only;
- no unbounded growth of Hankel matrices or history.

## 9. Model arm C — regularized DMC

Represent a delayed 60-minute step response using 8, 12, or 16 Laguerre coefficients rather than 180 independent response samples. Update coefficients using square-root recursive least squares on informative frames.

Penalize step-response curvature. Constrain final gain to be positive and bounded. Select the basis pole using calibration/validation data. It may change only during the five-minute refresh, under the same challenger-window and hysteresis policy used for delay changes.

The DMC predictor must expose the same state/prediction interface as the other arms.

## 10. Shared online-learning policy

Calibration data bootstraps each arm. Normal-cook samples are processed prequentially: score the prediction first, then permit the sample to update a shadow candidate.

Skip updates when any of these holds:

- realized combustion load is unknown;
- the input window lacks excitation;
- probe data is missing, stale, non-finite, or contains an implausible jump;
- lid-open, safety, or manual override is active;
- actuation provenance is ambiguous;
- cadence gaps cross an unknown input interval.

Maintain a bounded replay buffer stratified by temperature region, combustion-load range, and transient/coast status. Hot steady operation must not erase low-temperature or braking evidence.

Promotion requires:

- stable, finite dynamics;
- plausible gain and delay;
- better untouched rolling-window multi-horizon prediction;
- no worse simulated braking prediction;
- sufficient excitation and effective sample count.

Failed shadow candidates remain diagnostics only. Persist bounded replay data or sufficient statistics, never an unbounded raw history.

## 11. Common linear MPC

Every arm feeds the same controller shell.

- Control frame: 20 seconds.
- Candidate prediction horizons: 600, 800, and 1000 seconds. Select one on validation data, then freeze it across all model arms and test runs.
- Each candidate exceeds the full 0–300 second delay-search range. At the 20-second frame the QP has only 30, 40, or 50 moves, so move blocking is unnecessary.
- Input bound: `0 <= q <= 1`.
- Cost: quadratic temperature error, combustion-load movement, and terminal error.
- Controller weights are common across arms and fixed without test data.
- Offset-free tracking uses a separate output-disturbance state. Bias estimation must not update plant parameters directly.

Condense the model into a convex bound-constrained quadratic program. Solve it with a warm-started projected-gradient implementation using NumPy. Input box constraints are exact. Combustion movement is penalized; no general-purpose nonlinear solver is allowed.

Use fixed iteration and residual limits. Record the residual on every solve. On sampled frames, compare the chosen move against a high-accuracy offline convex reference and verify KKT residuals.

## 12. Actuation in closed-loop simulation

MPC requests realized mean auger duty `q`. A common deterministic pulse realizer drives both simulator plants while fan command is held at 100% for the primary comparison. Fractional on-time carries across frames; skipped runtime frames are discarded rather than replayed.

The real MAK fixture retains its historical 25-second requested-input reconstruction only for identification and prediction evidence. New simulator control runs use the common 20-second actuation path. An optional predefined fan-curve robustness slice must be labeled separately from the fixed-fan primary evidence.

Lid and safety behavior has precedence outside MPC. Disturbance scenarios measure estimator/model recovery after the override; they do not credit MPC for defeating an override.

## 13. Closed-loop matrix and metrics

For every arm, plant, and fixed seed, run:

- cold start to low, middle, and high setpoints;
- upward and downward steps;
- long steady hold;
- lid-open excursion and recovery;
- deliberately wrong initial gain;
- deliberately wrong dominant pole;
- deliberately wrong delay;
- frozen calibrated model;
- online-adapting model.

Report:

- temperature RMSE and IAE;
- overshoot and undershoot;
- settling time;
- peak-to-peak hold band;
- requested-versus-realized duty error;
- pulse transitions per hour;
- promotion count and rejection reasons;
- missed deadlines;
- per-frame learner time;
- refresh time;
- MPC solve time.

Online adaptation must improve deliberately wrong-model runs without materially degrading correctly initialized runs. The report must quantify both effects rather than using a qualitative label.

## 14. Runtime evidence

Record p50, p95, p99, and maximum timing after warm-up.

Original projected RPi 5 targets:

- per-frame learner p99 no greater than 5 milliseconds;
- five-minute refresh p99 no greater than 250 milliseconds;
- MPC solve p99 no greater than 50 milliseconds.

Direct RPi 5 measurement is preferred but not required. Workstation timing is acceptable evidence when the report includes:

1. raw workstation distributions, and
2. a conservative `5x` projected RPi 5 distribution.

Thus the effective workstation target values are 1 millisecond, 50 milliseconds, and 10 milliseconds respectively. Projected values must be labeled projections, not measured Pi timings.

Runtime becomes a hard disqualifier only beyond five times the original projected RPi 5 targets:

- learner p99 greater than 25 milliseconds;
- refresh p99 greater than 1.25 seconds;
- MPC solve p99 greater than 250 milliseconds.

## 15. Evidence artifact

Write one versioned JSON artifact and a concise console table. The artifact contains:

- schema version;
- source revision and environment/package versions;
- complete scenario definitions and seeds;
- signal semantics and sample periods;
- train/validation/test boundaries;
- all model hyperparameters;
- fitted poles, gains, delays, stability margins, and uncertainty;
- per-run prediction, control, adaptation, and timing metrics;
- promotion history and structured failures;
- aggregate median, worst case, and bootstrap confidence intervals;
- provenance for every metric: simulated, measured, reconstructed-input, measured workstation timing, or projected timing.

Optional plots must be generated from the JSON. The JSON and console table must be sufficient to reproduce and interpret the recommendation.

Checkpoint after every dataset/arm cell. A resumed run must produce the same final artifact as a clean run.

## 16. Failure handling

Validate before fitting:

- strictly increasing timestamps;
- finite temperatures and inputs;
- input bounds;
- cadence gaps;
- requested-versus-realized semantics;
- enough samples and excitation for the requested model order and horizon.

One arm failure records a structured failure and does not abort other arms. A timeout, non-converged solver, projected unstable model, non-finite trajectory, input-semantics violation, or test leakage fails that run. It must never be silently clipped or replaced with a nominal fallback.

## 17. Recommendation rule

1. Eliminate only invalid evidence: leakage, incorrect input semantics, unstable/non-finite behavior, irreproducibility, or runtime beyond the hard `5x` disqualifier in Section 14.
2. Missing an original prediction or runtime target remains a clearly reported target miss, not automatic elimination.
3. Rank valid arms first by worst-domain closed-loop control, then multi-horizon prediction, online recovery from wrong parameters, and runtime.
4. Recommend the simplest arm within 5% of the best worst-domain closed-loop score.
5. If tradeoffs are material, report the Pareto frontier rather than forcing one winner.
6. A model may be recommended with target misses only when the miss and its operational consequence are explicit.
7. Report frozen and online-adapting results separately; online learning must earn its complexity.

## 18. Verification

The experiment requires these checks:

- tiny deterministic smoke scenario for every arm;
- analytical synthetic systems with known order, delay, and gain;
- no-lookahead assertion on every online update;
- stable-pole/eigenvalue and positive-gain invariant checks;
- solver comparison against a high-accuracy convex reference;
- KKT residual verification;
- exact requested-to-reconstructed-input check for the MAK fixture;
- repeatability check: the same seed and configuration reproduces numerical JSON results within a declared floating-point tolerance;
- isolated execution proving no production MPC module or settings file is modified.

## 19. Non-goals

- Replacing or modifying the active production MPC implementation.
- Independently optimizing fuel and fan.
- Claiming fan-response identification from the fixed-fan MAK record.
- Claiming measured 30 or 60-minute real-grill accuracy from a 20.7-minute record.
- Injecting identification-only perturbations during ordinary cooks.
- Selecting a winner before the evidence artifact exists.
