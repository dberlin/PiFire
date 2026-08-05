# Online Scheduled-ARX MPC Adaptation — Design

**Date:** 2026-08-05  
**Status:** Approved for implementation planning  
**Scope:** Opt-in production scheduled-ARX learning, same-cook promotion, persistence, trace evidence, and rollback

## 1. Purpose

Add a scheduled-ARX challenger to PiFire's production MPC. The challenger learns from ordinary cooking without identification-only perturbations. Existing grey-box MPC remains in control until the challenger has better untouched prediction and braking evidence. A successful challenger takes over at the next 20-second pulse-frame boundary, regardless of cook boundaries.

This is cooking-appliance control, not an industrial safety system. The design uses conservative, reversible handoffs because bad temperature control wastes food and fuel and can produce poor behavior; it does not describe ordinary cooking as a super-dangerous activity.

## 2. Decisions

- `enable_online_adaptation` is a separate opt-in and defaults to `false`.
- Disabled means the current MPC command, estimator, allocator, pulse scheduler, snapshot, and end-of-cook refit behavior remain unchanged.
- The existing grey-box MPC is the initial incumbent when no accepted scheduled-ARX model exists.
- Scheduled ARX learns continuously across frame and cook boundaries. A cook boundary checkpoints state but is not a promotion boundary.
- The first scheduled-ARX implementation precedes the state-space repair/proof project.
- Normal cooks receive no identification-only dither.
- The model input is realized normalized combustion load `q` in `[0, 1]`, recovered from measured delivered auger duty.
- Model observations use the production 20-second scheduling frame and the fixed 2-second pulse quantum.
- Existing asynchronous controller execution remains mandatory. Model learning, evaluation, handoff, and linear MPC solving do not run on Hold's loop thread.
- The existing combustion allocator remains the only mapping from normalized combustion load to auger and fan commands.
- The existing grey-box estimator remains warm after scheduled-ARX promotion and supplies the analytic equilibrium combustion load. Linear MPC optimizes transient movement around that baseline.

## 3. Meaning of “better”

A shadow challenger does not control the grill, so it cannot demonstrate a counterfactual closed-loop cook before takeover. Pre-promotion “better” therefore means:

1. lower untouched 60-second free-run temperature error;
2. lower untouched 300-second free-run temperature error;
3. no worse error while coasting or following a target decrease;
4. valid stability, gain, delay, excitation, sample-count, continuity, and solver evidence.

Predictions must be fixed before their target observations arrive. A learner may not update a stored prediction after seeing any target it is scored against.

After takeover, the active scheduled-ARX controller is monitored through normal solve certificates, finite forecasts, result freshness, and observed control metrics. A failed active model rolls back to the last-known-good incumbent.

## 4. Canonical frame observation

Exactly one immutable learning observation is produced for each completed production pulse frame. It contains:

- frame start and end monotonic timestamps;
- temperature at the frame end in degrees Celsius;
- setpoint in degrees Celsius;
- ambient temperature in degrees Celsius;
- requested normalized combustion load;
- requested mean auger duty;
- measured realized normalized combustion load;
- measured delivered auger on-time;
- requested and actual fan duty when fan authority exists;
- output source and producing result revision;
- lid-open, manual-override, safety-inhibit, stale-probe/result, skipped-frame, reset, and continuity state;
- current controller/model role generation.

The observed temperature comes from the fresh `ptemp` used by Hold for that tick. Realized load comes from the completed frame's measured delivered on-time, not the controller request.

A frame is eligible for parameter learning only when its temperature and actuation are finite, complete, temporally continuous, and attributed to ordinary controller operation.

The following frames do not update parameters:

- lid-open operation;
- manual output override;
- safety inhibition;
- stale or missing temperature;
- unknown or partial actuation;
- skipped or reset pulse frame;
- discontinuity after a dropped observation or recorder gap;
- insufficient input excitation.

A known but ineligible frame may advance both models' lag histories without changing coefficients. Unknown actuation resets lag warm-up; the learner must not insert a guessed input.

## 5. Runtime data flow

`HoldMode` creates the canonical observation when the pulse scheduler reports a completed frame. A new `ControllerRunner.observe_frame(observation)` method accepts it.

`ThreadedControllerRunner` owns a bounded observation queue. The controller worker drains observations in timestamp order before its next solve. `HoldMode` never waits for RLS, evaluation, persistence, or a linear MPC solve.

If the observation queue overflows:

- normal control continues with the incumbent;
- the oldest unconsumed observations are discarded;
- the active evidence window is invalidated;
- lag warm-up restarts after continuity is restored;
- a structured trace event records the loss.

The synchronous runner forwards observations directly for deterministic unit tests and for controllers that do not request asynchronous execution. Production MPC continues to request asynchronous execution.

## 6. Scheduled-ARX model

The production model retains the proven bakeoff structure:

\[
\Delta T_k =
\sum_{i=1}^{2} a_i(T_k)\Delta T_{k-i}
+ \sum_{j=0}^{1} b_j(T_k)\Delta q_{k-d-j}
+ c(T_k)(T_{amb,k}-T_k) + e(T_k).
\]

Configuration:

- autoregressive order `na=2`;
- input order `nb=2`;
- candidate delays of 1, 2, and 3 frames;
- temperature knots at 82.2, 162.8, 232.2, and 315.6 degrees Celsius;
- square-root recursive least squares;
- forgetting factor `0.995`;
- initial covariance `10.0` for a fresh challenger;
- 32-sample delay-validation window;
- two validation-window wins before changing the active delay.

Each observation updates the two neighboring temperature regions according to interpolation weight. Physical projection enforces:

- autoregressive poles strictly inside the unit circle;
- finite, positive, bounded steady combustion gain;
- non-negative ambient-loss coefficient;
- finite affine predictions;
- calibration-bounded forecast response.

The production snapshot must include every delay candidate and every RLS sufficient statistic required to continue learning. The experiment's inspectable active-delay snapshot is not sufficient for restore.

## 7. Incumbent, challenger, and bootstrap

### 7.1 Initial grey-box incumbent

When online adaptation is enabled without a restorable scheduled-ARX incumbent:

- current grey-box MPC controls the grill unchanged;
- a scheduled-ARX challenger tracks and learns eligible frames;
- a prediction adapter exposes untouched grey-box forecasts from the current estimator/model state;
- grey-box and scheduled-ARX predictions are scored over the same realized input and temperature intervals.

The ARX challenger cannot affect the command before promotion.

### 7.2 After first promotion

At first promotion:

- scheduled ARX becomes the active prediction model;
- linear MPC becomes the active transient policy;
- the last grey-box incumbent remains the rollback target;
- a frozen scheduled-ARX copy becomes the new incumbent evidence model;
- a separate scheduled-ARX copy becomes the adapting challenger.

For later promotions, the incumbent tracks lag state without changing coefficients; only the challenger adapts. Promotion swaps complete model roles atomically and starts a fresh evidence generation.

## 8. Linear MPC policy

The production linear policy consumes the scheduled ARX affine horizon map. It optimizes one normalized combustion-load sequence and never treats fuel and air as independent manipulated variables.

Required policy behavior:

- 20-second model/control frame;
- bounded `q` in `[0, 1]`;
- existing analytic equilibrium load as the steady baseline;
- transient residual moves around that baseline;
- move and tracking penalties fixed by the validated bakeoff configuration;
- deterministic box-constrained QP;
- finite objective and KKT residual at or below the configured tolerance;
- first sequence element passed to the existing allocator;
- actual delivered load fed back at the next completed frame.

The controller must produce one valid scheduled-ARX/linear-MPC command before promotion is committed. A failed prospective solve rejects that promotion.

## 9. Evaluation and promotion

Evaluation runs every five minutes on the worker thread.

The runtime stores immutable affine prediction evidence at each origin rather than deep-copying full models. As future realized inputs and temperatures arrive, it completes 60-second and 300-second prediction scores with exact interval alignment.

An evaluation is eligible only when all scored origins belong to the current role generation and no unknown interval crosses the window.

Promotion requires all of the following:

- at least 20 eligible challenger updates;
- input excitation measured over 12 frames;
- at least two distinct realized input levels;
- input variance at least `1e-3`;
- candidate prediction score strictly lower than incumbent score;
- candidate braking score no worse than incumbent braking score;
- stable finite dynamics;
- plausible positive gain within the training-derived bound;
- active delay no greater than 15 frames;
- complete state/lag alignment evidence, or an explicit not-applicable result for incremental ARX;
- valid prospective linear-MPC solve;
- two consecutive eligible five-minute wins.

Any failed gate resets the consecutive-win count. Promotion occurs on the controller worker and is published for the next pulse frame. The in-progress frame is never rewritten.

## 10. Rollback

The runtime retains an immutable last-known-good incumbent snapshot and role generation.

Rollback occurs when the active scheduled-ARX path produces:

- a non-finite affine forecast;
- an invalid QP certificate;
- repeated solve failure under the existing stale-result policy;
- an invalid or non-restorable active model snapshot.

Rollback restores the prior prediction model/policy, preserves the current pulse frame, holds the last safe command until the fallback produces a fresh result, resets challenger scoring, increments the role generation, and records the reason. A single transient solve miss continues to use existing held-command behavior rather than forcing an immediate rollback.

## 11. Persistence

The existing `ControllerModelStore` remains the storage boundary. Its 65,536-byte JSON limit applies.

The MPC snapshot gains an optional online-adaptation member without invalidating a valid current grey-box snapshot. The nested member has its own schema revision and contains:

- active policy/model kind;
- last-known-good incumbent;
- complete scheduled-ARX incumbent and challenger state;
- all delay candidates;
- RLS information factors and normal right-hand sides;
- effective sample, validation-loss, and delay-win counters;
- bounded temperature, realized-input, and ambient lag histories;
- excitation history;
- role generation;
- most recent evaluation and promotion decision;
- consecutive eligible-win count;
- monotonic model revision.

Checkpoint after each evaluation, promotion, rollback, and teardown. A process restart may discard only affine prediction origins from the partial evaluation window since the last checkpoint. A shutdown gap invalidates origins whose forecast horizon crosses that gap, but completed eligible wins and learned coefficients survive cook boundaries. Cook teardown must checkpoint the current online state even when no end-of-cook grey-box refit is accepted.

Malformed, oversized, non-finite, schema-incompatible, or physically implausible online state is rejected without discarding an otherwise valid grey-box incumbent.

## 12. Trace schema and replay

Extend the typed control trace with three auditable records.

### 12.1 Model observation

Records the exact canonical observation, gate outcome, gate reasons, excitation variance and levels, incumbent innovation, challenger innovation, effective-update count, role generation, and model digest.

### 12.2 Model evaluation

Records window identity and interval bounds, matured origin identifiers, 60/300-second candidate and incumbent errors, braking errors, every promotion gate, consecutive-win count, observe/evaluation/solve timings, candidate and incumbent digests, and promote/reject outcome.

### 12.3 Model lifecycle

Extends model lifecycle evidence with exact model kind, nested schema, coefficients/sufficient statistics needed to identify the active snapshot, role generation, and restore/promote/rollback/teardown reason.

The trace remains batched every five seconds, retained for 30 days, and flushed once at teardown. Online control never depends on trace I/O succeeding.

Add one canonical trace-to-learning-record API. It must:

- convert recorded Fahrenheit temperatures and setpoints to Celsius;
- use recorded session ambient temperature rather than the MPC default;
- join only complete applied-output/frame intervals;
- reject gaps, unknown actuation, and ambiguous revision joins;
- preserve session, cook, frame, model, and provenance identifiers;
- reproduce the ordered observations seen by the online learner.

`controller/update_mpc.py` must consume this canonical conversion instead of maintaining a second unit/ambient interpretation.

## 13. Status and operator visibility

MPC status reports:

- online adaptation enabled/disabled;
- active model kind;
- role generation;
- eligible and rejected update counts;
- current rejection reason;
- active delay;
- effective samples;
- time and outcome of the last evaluation;
- candidate/incumbent scores;
- promotion and rollback counts;
- learner/evaluation/linear-solve durations;
- pending/dropped observation count.

This is diagnostic status, not a new set of tuning controls.

## 14. Verification requirements

### 14.1 Compatibility

- With `enable_online_adaptation=false`, current production MPC output remains unchanged for identical inputs.
- Existing grey-box snapshots restore without an online member.
- Enabling online adaptation never changes a command before promotion.

### 14.2 Observation and learning

- Completed pulse frames produce exactly one aligned observation.
- Realized load is derived from measured delivered on-time.
- Inhibited, incomplete, skipped, unknown, stale, and discontinuous frames cannot update coefficients.
- Unknown input resets lag warm-up.
- No normal-cook identification dither is introduced.

### 14.3 Promotion and rollback

- Predictions are scored before update and against correctly aligned future intervals.
- Stale role generations cannot promote.
- Every gate independently prevents promotion.
- Two eligible wins promote once.
- The current frame is preserved; the new command begins no earlier than the next frame.
- A prospective solve failure prevents promotion.
- Active-path repeated failure restores the last-known-good incumbent.

### 14.4 Persistence and replay

- Snapshot round-trip reproduces the next ARX prediction and next update.
- Revisions advance across restarts.
- Oversized or malformed online state leaves the grey-box model usable.
- Trace replay reproduces observation eligibility, parameter updates, and promotion decisions.
- Fahrenheit and Celsius sessions yield equivalent Celsius learning records.
- Recorded ambient affects replayed fit/prediction.

### 14.5 Empirical gate

Regenerate fixed-seed GrillSim and MAKGrillSim comparisons and the chronological real-MAK calibration comparison. Report:

- within-band percentage;
- overshoot;
- settling time;
- setpoint RMSE;
- steady peak-to-peak band;
- auger on-time;
- relay transitions per hour;
- requested-versus-realized load error;
- solver deadline misses and stale-result episodes;
- 60/300-second prediction error;
- coast/braking error;
- promotion and rollback chronology.

The opt-in path may ship only if aggregate control quality improves strictly over current production MPC and neither simulator shows a safety-inhibit, reachability, relay-transition, or stale-result regression.

### 14.6 Runtime budgets

Measured on the development workstation and reported with raw distributions:

- per-frame learner p99 no greater than 5 ms;
- five-minute evaluation p99 no greater than 250 ms;
- linear MPC solve p99 no greater than 50 ms.

## 15. Non-goals

- No independent fuel and fan optimization.
- No identification-only perturbations during ordinary cooks.
- No automatic default enablement.
- No state-space runtime selection in this project.
- No replacement of the production pulse scheduler.
- No change to the 30-day trace-retention policy.
- No claim that shadow prediction superiority proves counterfactual closed-loop superiority.
