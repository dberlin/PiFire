# Real-Grill Online Learning Evidence — Design

**Date:** 2026-08-06  
**Status:** Approved for implementation planning  
**Scope:** Real-cook recording, guarded calibration excitation, shadow learning, confidence reporting, manual first activation, and confidence-gated continuous adaptation

## 1. Purpose

Build the evidence path required to establish that PiFire's innovation state-space model works on a real grill before it becomes the MPC prediction model used for control.

The existing grey-box prediction model remains the production default and continues to own the baseline combustion-load command. The state-space model learns and predicts in shadow. A dedicated empty-grill calibration cook supplies safe independent excitation; later ordinary cooks supply untouched validation evidence under real thermal loads and weather.

The first state-space activation requires explicit operator approval after every confidence gate passes. After that first cutover, state-space parameter learning continues online and candidate parameter generations may replace the active state-space generation automatically, but only through the same fail-closed confidence gates. The grey-box model remains the immediate safety fallback.

## 2. Product decisions

1. The controller remains `mpc`; this work changes its internal prediction-model evidence and activation path, not the controller catalog.
2. The existing grey-box model is the default prediction model and baseline command owner until manual first activation.
3. State-space is shadow-only before activation. It must not influence the grey-box policy, allocation, pulse schedule, fan command, safety decision, or fallback command.
4. Every ordinary cook is recorded automatically while MPC Hold is active. Ordinary cooks receive no identification-only perturbation.
5. One explicit calibration mode may add bounded combustion-load probes around the grey-box request. The operator starts this mode intentionally on an empty grill.
6. The manipulated input remains one normalized coupled combustion load `q` in `[0, 1]`. The model does not optimize fuel and fan independently.
7. The canonical identification sample is the completed 20-second framed-pulse observation. The 2-second auger pulse quantum remains unchanged.
8. Model fitting, forecasting, scoring, snapshot persistence, reporting, and pruning remain asynchronous and off the Hold safety path.
9. There is no fixed cook-count activation rule. Readiness depends on identifiable input, independent future outcomes, cross-session uncertainty, physical validity, runtime, and operational integrity.
10. A single long cook must not masquerade as independent multi-condition evidence. Confidence is grouped by session/cook and is undefined until between-session uncertainty can be estimated.
11. No ambient probe is required. Ambient is recorded with explicit provenance and uncertainty; an unmeasured ambient estimate must never be labeled measured.
12. Raw frame evidence retains the existing 30-day policy. Compact completed-origin evidence, confidence decisions, and model digests persist across raw-data pruning until explicit reset or model-schema invalidation.
13. Old model-schema evidence cannot authorize a new model schema.
14. First activation is manual. Later parameter-generation promotions are automatic only after passing all confidence gates.
15. Missing, stale, contradictory, truncated, or non-causal evidence blocks readiness and promotion.
16. A state-space parameter-generation digest covers the schema, identification configuration, accepted realization parameters/covariances, and structural bounds. Mutable filter state/covariance, lag record, runtime status/timing, refresh-attempt diagnostics, and counters do not change generation identity. Activation and rollback still persist the complete snapshot for exact reconstruction.

## 3. Existing foundation to reuse

The implementation extends the existing control pipeline rather than creating a second logger or learner:

- `ControlTraceRecord` and typed SQLite trace persistence;
- `SessionPayload` for controller, actuator, model, and build provenance;
- `ModelObservationPayload` for completed framed observations;
- `StateSpaceRefreshPayload` for rank, candidate, alignment, covariance, and timing evidence;
- `ModelEvaluationPayload` for generation-bound completed forecast origins and horizon scores;
- `OnlineAdaptation` for immutable future-outcome scoring, prospective promotion, commit, and rollback;
- `InnovationStateSpace` for bounded subspace identification, state alignment, forecasting, snapshots, and restore;
- the Hold checkpoint worker and observation queue for work outside the runtime loop;
- the existing 30-day batch-pruned control trace.

The implementation must not create a CSV-only calibration path, a second model format, or a parallel scoring implementation.

## 4. Canonical recorded observation

Every completed 20-second frame records one immutable model observation. Canonical temperatures are Celsius and canonical loads are normalized fractions.

### 4.1 Identity and time

- session ID;
- cook ID;
- controller and prediction-model role generation;
- result revision;
- wall-clock timestamp;
- monotonic frame start and end;
- exact frame duration;
- observation sequence;
- continuity with the prior eligible frame.

Wall time is for operator correlation. Monotonic frame time is authoritative for dynamics, delay, horizon completion, and timing.

### 4.2 Measured outputs and disturbances

- chamber temperature;
- setpoint;
- probe validity and source identity;
- ambient estimate;
- ambient source: `measured`, `manual`, `weather`, or `configured`;
- ambient uncertainty classification;
- optional measured disturbance fields when available.

For the approved no-extra-probe workflow, ambient normally uses the configured or manually supplied estimate. The state-space disturbance covariance must absorb this uncertainty. Confidence reports must state that ambient was unmeasured and must not claim a separately identified ambient coefficient from constant or configured input.

### 4.3 Commanded and realized actuation

- grey-box baseline normalized combustion load;
- calibration probe contribution, zero outside calibration mode;
- combined requested normalized combustion load;
- allocator-bounded normalized combustion load;
- requested auger duty;
- scheduled auger-on seconds;
- delivered auger-on seconds;
- realized auger duty;
- realized normalized combustion load reconstructed through the allocator inverse;
- requested fan duty;
- applied/actual fan duty;
- allocator revision and auger/fan clamp reasons;
- output source and producing result revision.

Identification uses realized normalized combustion load. Baseline, probe, and requested values remain recorded to attribute excitation, clipping, and actuator error.

### 4.4 Eligibility and interventions

Record every frame, including frames that cannot update a model. Each observation contains explicit flags/reasons for:

- lid open;
- manual takeover;
- safety inhibition;
- stale command;
- skipped frame;
- scheduler reset;
- discontinuity;
- unknown actuation;
- non-controller output source;
- lag warmup;
- insufficient excitation;
- recorder gap or queue eviction.

Ineligible frames remain in the audit trail. They may advance wall time but must not update parameters, complete a forecast origin across a destructive gap, or count toward confidence.

### 4.5 Configuration and provenance

The session record and linked typed records bind observations to:

- software and build version;
- trace and model schema versions;
- controller configuration and settings revision;
- temperature units at the UI boundary;
- control interval;
- pulse slot and frame durations;
- same-revision allocation `u_max` and allocator revision; `u_max` is not restored as a session-level MPC knob;
- fan authority, PWM capability, and fan bounds;
- grey-box snapshot/provenance/digest;
- state-space snapshot/provenance/digest;
- grill identifier when configured;
- optional operator metadata such as pellet type.

Optional metadata may improve diagnosis but its absence must not invalidate otherwise complete evidence.

## 5. Calibration mode

### 5.1 Preconditions

Calibration mode requires:

- an explicit operator start action;
- MPC Hold using the grey-box prediction model;
- an empty grill with normal grates and drip tray installed;
- a valid chamber probe in normal control position;
- closed lid;
- no manual takeover, safety inhibit, stale command, or controller fallback;
- sufficient pellets confirmed by the operator;
- an operator-selected maximum calibration temperature within existing configured safety bounds;
- a recorded ambient estimate and provenance.

Failure of a precondition prevents start or pauses/cancels excitation. It never weakens an existing safety guard.

### 5.2 Temperature stages

The default profile uses low, middle, and high bands centered near 225, 325, and 425 degrees Fahrenheit. Values are converted to Celsius internally and clipped below the operator-selected maximum and configured grill safety ceiling.

The profile records:

1. cold start and rise to the low band;
2. low-band hold and excitation;
3. rise to the middle band;
4. middle-band hold and excitation;
5. rise to the high band;
6. high-band hold and excitation;
7. a downward transition and coast segment.

A hold/excitation stage is complete only after all of the following are true:

- at least 30 eligible 20-second observations were recorded in the stage;
- at least three realized-load levels are present;
- realized-load variance is at least the existing `0.001` update threshold over an excitation window;
- at least six eligible positive-probe and six eligible negative-probe observations were realized without clipping away the move;
- the stage adds effective rank or a previously missing input/temperature region to the cumulative calibration matrix;
- no pending continuity gap or safety intervention crosses the accepted stage evidence.

Each excitation stage has a 60-minute cap measured from its first eligible probe. Timeout marks the calibration incomplete; it does not silently accept weak evidence or extend operation indefinitely. The operator may stop at any time.

The expected total duration is 2.5 to 4 hours. Duration is advisory; the evidence and safety state determine completion.

### 5.3 Probe generation

The grey-box policy computes the baseline request `q_g`. Calibration computes an independent, deterministic, zero-mean probe `q_p`. The final calibration request is:

\[
q_{request} = \operatorname{clip}(q_g + q_p, 0, 1).
\]

The default maximum probe magnitude is `0.05` normalized load. Actual magnitude is the minimum of that bound and the available safe headroom established by:

- allocator bounds;
- current setpoint error;
- temperature rate of rise;
- configured temperature guard margin;
- fan/auger capability;
- current command saturation;
- probe-specific predicted overshoot guard.

Probe dwell varies deterministically over multiple 20-second frames to distinguish transport delay from thermal poles. The sequence seed, intended probe, bounded probe, and cancellation reason are recorded. The sequence includes positive and negative moves. A completed stage must have `abs(sum(q_p)) <= 0.05` across its equal-duration eligible frames; otherwise a safe compensating move is attempted within the same bounds or the stage remains incomplete. The probe must not add a persistent firing bias.

The state-space challenger never supplies or modifies `q_p`.

### 5.4 Probe cancellation

The probe becomes zero immediately for any of:

- lid opening;
- manual output or mode change;
- safety event or temperature guard;
- invalid/missing chamber temperature;
- stale controller result;
- skipped or reset frame;
- discontinuity or unknown actuation;
- controller fallback;
- insufficient positive or negative command headroom;
- predicted overshoot outside the probe guard;
- operator pause/stop;
- calibration stage timeout.

After cancellation, the final request equals the current grey-box request subject to the normal allocator and safety path. Calibration may resume only after continuity and all start guards are re-established; a destructive gap invalidates pending forecast origins.

### 5.5 Calibration audit events

Add a typed calibration trace payload for:

- requested start;
- accepted/rejected start and reasons;
- stage start/completion/timeout;
- intended and bounded probe transitions;
- guard pause/resume;
- operator stop;
- safety abort;
- complete/incomplete outcome;
- final identifiability summary.

These events augment model observations; they do not replace frame-level actuation evidence.

## 6. Ordinary cooks

Ordinary cooks receive no calibration probe. `q_p` is exactly zero and the active grey-box request is unchanged.

Every eligible completed frame is delivered to both models:

- the grey-box incumbent tracks state and creates forecasts without parameter mutation unless its existing behavior already requires it;
- the state-space challenger observes realized input, updates only through guarded online identification, and creates forecasts.

Calibration data may initialize a candidate, but ordinary-cook outcomes decide whether it generalizes to food load, disturbances, pellet variation, and weather. Calibration fit rows cannot be reused as validation outcomes.

## 7. Causal forecast scoring

At each eligible origin, both model snapshots predict chamber temperature at 60, 300, 900, 1800, and 3600 seconds using the same known command/ambient assumptions. The origin stores the exact model digest, generation, observation sequence, and prediction before any target temperature exists.

When the future observation arrives:

- the origin completes only if every intervening frame satisfies the horizon's continuity rules;
- the observed temperature and both errors are appended immutably;
- a destructive gap expires rather than repairs the origin;
- a model refresh starts a new generation and invalidates incompatible pending origins;
- no origin may be counted twice or rebound to another model digest;
- no later refit may recompute an earlier claimed prediction.

Scores are grouped by session/cook, temperature band, horizon, heating/coasting classification, ambient provenance, and model generation. This prevents dense frames from one cook from being treated as independent cooks.

## 8. Durable confidence ledger

Add a typed append-only confidence ledger in the existing SQLite control-evidence boundary. It stores compact evidence rather than duplicating raw frame traces:

- cook/session summary and provenance digest;
- calibration completeness and identifiability summary;
- completed immutable forecast origins;
- per-horizon and per-band scores;
- state-space refresh diagnostics and selected snapshot digest;
- timing distributions;
- gate decisions and exact rejection reasons;
- manual activation and rollback decisions.

Raw trace records remain subject to 30-day batch pruning off the safety path. Ledger evidence required to justify the current candidate or active model persists until:

- the operator explicitly resets learning;
- the model/trace schema changes incompatibly;
- provenance validation fails;
- the associated model lineage is intentionally retired.

Pruning must never leave a confidence decision that references missing compact evidence. Schema invalidation clears readiness and requires new evidence; it does not reinterpret old rows.

## 9. Confidence gates

The implementation distinguishes per-frame update eligibility from first-activation readiness. Existing `AdaptationPolicy` thresholds remain the minimum online update gates; the following cumulative gates additionally control readiness and promotion.

### 9.1 Identifiability

- completed calibration includes low, middle, high, and coast evidence;
- realized load, not requested load, has sufficient variance and distinct levels;
- selected order/delay has full effective rank under the state-space rank policy;
- singular-value and condition diagnostics are finite and bounded;
- delay and gain are identifiable without unresolved rank-deficient terminal attempts;
- enough eligible completed frames exist to fit and independently validate the selected structure;
- ambient is labeled by actual provenance and unmeasured ambient does not count as independently excited input.

Failure reports `insufficient-excitation` or the exact state-space rejection reasons. More elapsed time at a constant input cannot satisfy this gate.

### 9.2 Physical validity

- every selected pole is finite and below the configured maximum magnitude (`0.999` by default);
- combustion-to-temperature steady gain is finite, positive, and within established physical bounds;
- selected delay is within the configured maximum (`15` frames by default);
- process and measurement covariance are finite, positive where required, and bounded;
- state/output reconstruction is finite;
- measured state alignment succeeds with error no greater than `2.0` Celsius;
- model snapshot round-trip preserves predictions and diagnostics.

### 9.3 Absolute prediction accuracy

Thresholds are fixed before real validation evidence is inspected:

- RMSE no greater than `2.8` Celsius at 60, 300, and 900 seconds;
- RMSE no greater than `5.0` Celsius at 1800 and 3600 seconds;
- no systematic signed bias or temperature-band error that would conceal unsafe overshoot/coasting behavior;
- braking/coasting error no worse than the grey-box incumbent under the configured zero default tolerance.

Unsupported horizons remain unavailable and block first activation. They are not replaced with extrapolated scores.

### 9.4 Relative prediction confidence

For every required horizon and applicable operating band:

- challenger RMSE is lower than incumbent RMSE;
- the one-sided 95% confidence interval for the cook-grouped challenger/incumbent RMSE ratio has an upper bound below `1.0`;
- the interval uses a deterministic hierarchical block bootstrap: session/cook is the top-level resampling unit, contiguous origin blocks at least as long as the scored horizon are the within-cook unit, the seed is stored in the evidence artifact, and 10,000 replicates are used;
- confidence computation accounts for within-cook correlation rather than treating every 20-second row as independent;
- no single cook, seed, temperature band, or calibration stage supplies all effective weight;
- the win repeats across sequential evaluation windows under the existing consecutive-win policy;
- a challenger refresh resets generation-specific wins and scoring evidence.

There is no fixed cook count. If cross-session uncertainty cannot be estimated, this gate remains unavailable rather than guessing confidence.

### 9.5 Runtime and persistence

On target hardware:

- observation submission remains nonblocking;
- forecast/solve p99 is within the controller budget;
- state-space refresh p99 is no greater than 250 ms;
- no model work delays the Hold safety/actuation path;
- queue eviction, recorder gaps, or deadline/stale failures are represented and block affected evidence;
- checkpoint write/restore is atomic and generation-safe;
- restart resumes only evidence whose provenance and schema validate.

Workstation timing may diagnose regressions but cannot satisfy the target-hardware activation gate.

### 9.6 Operational integrity

- zero pre-activation commands are owned or modified by state-space;
- calibration overlay is present only in explicit calibration mode;
- every applied interval is attributable to a baseline, probe, allocator result, and actual actuator outcome;
- all safety/manual/lid interventions are retained;
- evidence artifact validation reports no contract errors;
- prospective activation succeeds through the real controller/model construction path before readiness is reported.

## 10. Status and reports

Expose a concise state without presenting a weak fit as success:

- `collecting`;
- `insufficient-excitation`;
- `fitting`;
- `evaluating`;
- `ready-for-review`;
- `active`;
- `fallback`;
- `schema-invalidated`.

The confidence report contains:

- current active/default model kind and digest;
- candidate kind, generation, and digest;
- calibration progress and missing stages;
- eligible/ineligible frame counts and reasons;
- identifiability diagnostics;
- horizon and temperature-band scores with confidence intervals;
- physical/alignment gate results;
- target timing results;
- missing evidence and exact blocking reasons;
- activation/rollback history;
- ambient provenance limitation.

Generate a deterministic machine-readable evidence artifact from the ledger so a run can be reviewed or reproduced without granting the artifact authority to alter the model.

## 11. First activation

Passing all gates sets status to `ready-for-review`; it does not alter control.

The operator's explicit activation action must:

1. identify the exact ready candidate digest and evidence decision;
2. reconstruct the candidate through the production model path;
3. run a bounded prospective solve without applying output;
4. atomically persist the activation decision and rollback snapshot;
5. change the MPC prediction-model owner only after persistence succeeds;
6. start a new role generation and invalidate incompatible pending origins;
7. retain the grey-box model as immediate fallback.

A stale readiness decision, changed candidate digest, failed prospective solve, failed persistence, or changed controller configuration rejects activation.

## 12. Continuous online learning after activation

After manual first activation, the current state-space generation becomes incumbent. A separately refreshed state-space generation is the challenger.

The challenger observes the same eligible frames and is evaluated on untouched future outcomes. Candidate generations promote automatically only when all applicable update, physical, causal, relative, alignment, timing, and persistence gates pass. Promotion is atomic and starts a new role generation.

Automatic promotion never means automatic model-structure or schema promotion. A new model structure/schema requires a new explicit evidence cycle and manual activation.

## 13. Fallback and rollback

The controller immediately uses the last safe state-space snapshot or grey-box fallback for:

- invalid/non-finite state or prediction;
- snapshot/schema/provenance failure;
- failed prospective or active solve;
- repeated policy exceptions;
- stale/deadline behavior beyond the existing safety threshold;
- model restore failure;
- measured residual degradation beyond the active-model monitor;
- explicit operator rollback.

Fallback must not erase the failed model or its evidence. It records the reason, generation, digest, last safe command, and chosen fallback. Re-entry requires a fresh confidence decision; it never automatically re-enables the failed generation.

## 14. Failure behavior

- Trace/ledger unavailability disables learning and confidence accumulation, not temperature control.
- Calibration recorder failure cancels the probe and continues ordinary grey-box Hold if the normal safety path permits.
- Learner exceptions reject the candidate update and leave the incumbent/default unchanged.
- Evidence generation failure reports an error and cannot activate a model.
- Raw-data prune failure retries off the safety path and cannot block Hold.
- Disk pressure may disable new evidence after flushing a recorder-gap/error event; it must not delete the active model snapshot or block safety actuation.
- Incomplete calibration remains explicitly incomplete.

## 15. Verification and acceptance

### 15.1 Typed schema and persistence

Tests must prove:

- every new payload rejects non-finite, contradictory, out-of-order, or mismatched evidence;
- baseline, probe, requested, allocated, and realized loads round-trip distinctly;
- ambient source cannot claim measured without measured provenance;
- compact ledger evidence survives raw trace pruning;
- schema invalidation clears readiness;
- restart restores exact model/evidence generations;
- corrupted or partial writes fail closed.
- state/lag updates and rejected refresh diagnostics preserve a state-space generation digest, while an accepted parameter refresh changes it;
- activation binds the reviewed parameter-generation digest while persisting the complete exact snapshot payload.

### 15.2 Calibration safety

Deterministic simulator tests on both `GrillSim` and `MAKGrillSim` must prove:

- state-space never owns calibration commands;
- probe is bounded, zero-mean by completed stage, and recorded;
- all cancellation conditions remove the probe on the same runtime tick/frame boundary allowed by the actuator path;
- normal cooks have exactly zero probe contribution;
- calibration stop returns to unmodified grey-box control;
- no calibration behavior weakens lid, manual, stale, reset, or temperature safety behavior.

### 15.3 Identification and confidence

Tests must prove:

- the old constant-input real-MAK fixture remains explicitly unidentifiable;
- the simulator calibration profile reaches identifiable bounded models on both plants;
- fit rows cannot count as validation origins;
- predictions are committed before targets and are immutable;
- gaps, refreshes, and generation changes invalidate incompatible origins;
- cook-grouped confidence cannot be satisfied by duplicating frames or one session;
- every absolute/relative/physical/timing gate blocks independently;
- passing gates produces `ready-for-review` without activation;
- stale or changed evidence cannot activate.

### 15.4 Activation, adaptation, and rollback

End-to-end tests must prove:

- manual first activation uses the exact reviewed digest;
- failed persistence or prospective solve leaves grey-box active;
- successful activation preserves grey-box fallback;
- later state-space candidate promotion requires complete confidence evidence and is atomic;
- pre-refresh wins never carry into a refreshed generation;
- active-model failure rolls back without replaying missed actuator frames;
- no state-space catalog/default change occurs before manual activation.

### 15.5 Real-grill acceptance boundary

Software completion means the recorder, calibration guards, learner, ledger, report, activation gate, and rollback path behave end to end in deterministic tests and smoke runs. It does not mean the real-grill model is validated.

Real-grill readiness requires recorded hardware evidence satisfying Sections 9 through 11. Until that evidence exists, the shipped/default state remains grey-box and reports the exact missing confidence gates.

## 16. Rollout

1. Ship recording and shadow learning with grey-box unchanged.
2. Run the guarded empty-grill calibration cook.
3. Continue recording ordinary cooks with no probe.
4. Review the cumulative deterministic confidence report.
5. Improve the candidate or collect missing operating evidence until all gates pass.
6. Manually activate the exact reviewed state-space digest.
7. Continue automatic confidence-gated parameter adaptation with grey-box fallback.

No rollout step may skip the prior evidence boundary.