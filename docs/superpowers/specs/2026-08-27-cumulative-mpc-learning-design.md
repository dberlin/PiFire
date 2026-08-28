# Cumulative MPC Learning, Cross-Mode State, and Automatic Calibration

**Status:** Approved design

**Date:** 2026-08-27

## 1. Problem

MPC learning currently conflates three different kinds of state:

1. physical state needed to initialize the thermal model;
2. reusable observations used to fit model parameters;
3. incumbent-versus-challenger forecasts used to authorize activation.

All three live under one per-Hold `GreyLearningOrchestrator`. A new Hold, process restart, configuration identity change, or model activation replaces that owner and discards its in-memory observations. The advertised 8,640-observation limit is therefore only a cap on one current contiguous in-memory segment. It is not a rolling corpus across cooks or promotions.

The fitting simulator also initializes every transport-delay state to zero. A fit window beginning after Smoke or midway through a hot Hold is treated as if no heat is in flight. The supplied real cook demonstrated the resulting boundary sensitivity:

| Fit window | `C_c` | `K_Q` | `theta` | RMSE | Identifiability |
|---|---:|---:|---:|---:|---:|
| 21–140, hot calibration window | 1767.501 | 288.210 | 52.241 s | 1.623°C | 0.568 |
| 2–121, includes heat-up ramp | 313.487 | 281.109 | 29.668 s | 3.664°C | 3.179 |
| 2–196, complete pre-Stop window | 337.354 | 280.847 | 32.909 s | 3.412°C | 2.926 |

Prepending frames 2–20 to the hot window changes `C_c` by −1457.7 and `theta` by −20.1 seconds. Extending the hot window only with later coast data changes `theta` by less than one second. The missing pre-window physical state—not calibration classification—is the dominant cause.

Operator-calibration candidates are the same grey-box model as passive candidates, but current policy diverts them to a manual `operator-reviewed` path. That path is undiscoverable for the supplied state and cannot complete an activation created during Stop teardown because no Hold runner remains to execute the frame-boundary swap.

The end state must separate physical pre-roll, durable fit evidence, and generation-specific causal authorization.

## 2. Goals

1. Accumulate at most 8,640 scored Hold observations across compatible cooks, restarts, and model promotions.
2. Preserve physical discontinuities as independent trajectory segments rather than clearing history or concatenating gaps.
3. Observe exact delivered actuation during Smoke without giving MPC actuator authority.
4. Warm the active estimator and every fit candidate from candidate-specific delivered-input pre-roll.
5. Fit one shared parameter set over all compatible retained segments.
6. Keep causal incumbent-versus-challenger evaluation generation-specific.
7. Route passive and operator-calibration candidates through the same automatic causal activation gates.
8. Persist a challenger and completed causal progress across clean Stop/restart boundaries when lineage remains exact.
9. Remove direct cook-end model adoption and the manual review endpoint/UI.
10. Preserve active/rollback safety authority through migrations and corpus failures.
11. Make live collection, fitting, promotion, diagnostics, cookfile export, and replay use the same typed evidence.

## 3. Non-goals

- MPC does not control Smoke mode.
- Smoke temperatures are not fit residuals.
- A process restart does not imply physical continuity or automatic mode resume.
- Unknown actuator delivery is never reconstructed from requested duty.
- The corpus is not stored inside the compact controller-model checkpoint.
- Historical trace schema v6 rows are not fabricated into new exact corpus frames.
- Pending forecast origins are not persisted across a cook boundary.
- Fan dynamics are not added to the current scalar-input grey model in this change.

## 4. Core decisions

### 4.1 One model over many segments

A gap or cook boundary creates a new segment, not a separate model. For every optimizer trial `z = (log C_c, log K_Q, log theta)`, the same candidate parameters are simulated independently over each compatible segment:

```text
r(z) = concat(r_segment_1(z), r_segment_2(z), ..., r_segment_N(z))
```

The optimizer minimizes one pooled objective:

```text
minimize sum_s ||r_segment_s(z)||²
```

Each segment supplies its own pre-roll, measured Hold-entry temperature, ambient profile, and exact realized input. Segment states are never carried across a Stop, restart, missing interval, or cook boundary.

The incumbent and challenger are scored over the same segment set and common warm-up masks. A candidate must:

- pass the existing pooled parameter-bound, convergence, fit-margin, and identifiability rules;
- be no worse than the incumbent on every sufficiently supported cook;
- pass untouched prospective causal evaluation before activation.

A cook is sufficiently supported for the per-cook veto when its retained compatible segments provide at least 120 effective scored observations and collectively pass the existing excitation, level-count, temperature-span, and identifiability gates. Shorter cooks may contribute pooled residuals after warm-up, but cannot independently bless or veto a candidate.

### 4.2 Fit corpus and causal evaluation are different authorities

The durable fit corpus survives:

- cook boundaries;
- clean process restarts;
- model promotion;
- rollback and fallback;
- candidate rejection.

Causal evaluation state is bound to exact incumbent/candidate/configuration/corpus lineage. Incumbent activation or rollback resets pending origins and completed-as-current evaluation state. Fit observations remain reusable.

### 4.3 One automatic activation policy

Replace `PASSIVE_AUTO` and `OPERATOR_REVIEWED` with:

```text
ActivationPolicy.CAUSAL_AUTO = "causal-auto"
```

`CandidateOrigin.PASSIVE_ONLINE` and `CandidateOrigin.OPERATOR_CALIBRATION` remain provenance. Both origins use identical fit, preparation, causal forecast, durability, activation, and rollback gates. Operator calibration has one additional calibration-manifest completeness gate and no relaxed gate.

### 4.4 No direct cook-end adoption

Stop finalizes the open segment and schedules a cumulative-corpus fit when identification is enabled or an explicit calibration request authorized collection, but a fit result never becomes active solely because the cook ended. A candidate without prospective causal evidence persists as `EVALUATING` with zero or one completed wins and resumes on later eligible Hold frames.

## 5. Terminology

### 5.1 Gap

A gap means physical continuity cannot be defended. Examples:

- Stop/shutdown between cooks;
- process restart;
- missing or invalid probe samples;
- unknown/unjoined actuator delivery;
- manual actuation;
- lid/safety interval excluded from learning;
- clock discontinuity or recorder loss;
- incompatible model structure, actuation mapping, fan regime, or units.

A gap finalizes the current segment. Valid compatible data on both sides remains in the corpus as separate trajectories; invalid or incompatible frames remain audit evidence only.

### 5.2 Pre-roll

Chronological exact delivered-input frames used only to reconstruct delay-chain state before scored Hold evidence. Smoke is the normal source. When Smoke pre-roll is insufficient, leading Hold frames supplement it and their temperatures remain masked until candidate-specific warm-up completes.

### 5.3 Scored observation

A complete, continuous, known-output Hold `FrameObservation` whose temperature enters fitting and identifiability after candidate-specific warm-up.

### 5.4 Evaluation round

One causal incumbent/challenger comparison containing completed origins at all required horizons: 3, 15, 45, 90, and 180 frames. Activation requires two consecutive complete winning rounds.

## 6. Shared trajectory contracts

Add frozen validated contracts in `common/learning_trajectory.py`.

### 6.1 `LearningTrajectoryFrame`

A generic cross-mode delivered interval:

- monotonic and wall start/end;
- canonical Celsius chamber temperature;
- probe validity/source;
- ambient value/source/uncertainty;
- delivered auger-on seconds;
- realized auger duty and normalized combustion load;
- delivered fan-on seconds, fan-duty integral, and mean actual fan duty;
- delivery certainty per channel;
- effective mode and recipe-step identity;
- complete/continuous/partial flags;
- typed boundary reason.

It deliberately contains no MPC result revision, request, allocation, candidate, or generation requirement.

### 6.2 `HoldEntrySample`

- monotonic/wall time;
- measured chamber temperature in Celsius;
- probe validity/source.

It anchors chamber state and never contributes a residual.

### 6.3 `LearningTrajectorySegment`

- segment schema, ID, and content digest;
- cook ID and trajectory-session ID;
- linked trace-session IDs;
- collection/configuration provenance;
- fit-compatibility digest;
- model-structure digest;
- delay-input mapping digest;
- scored fan-regime digest;
- ambient semantics/partition;
- chronological pre-roll frames;
- Hold-entry anchor;
- chronological scored Hold observations;
- model-generation ranges as audit provenance only;
- start/end time and sequence bounds;
- pre-roll end reason and terminal break reason;
- finalized/open/quarantined state;
- source trace/schema/row digest and build provenance.

### 6.4 `FitCorpusIdentity`

- corpus schema and revision;
- fit-partition digest;
- ordered immutable segment-prefix slices:
  - segment ID;
  - through ordinal;
  - prefix digest;
  - pre-roll count;
  - scored count.

The fit corpus identity does not include incumbent digest or learned `C_c`, `K_Q`, or `theta`.

### 6.5 `ModelFitLineage`

- request ID;
- parent incumbent digest/generation;
- candidate generation;
- fit-corpus identity/digest;
- trigger origin;
- result status and candidate digest.

## 7. Exact delivered-actuation observation

### 7.1 Platform journal

Add `controller/runtime/actuation_delivery.py`.

`DeliveredGrillPlatform` transparently decorates the process-wide grill platform. It intercepts actual auger/fan/PWM commands, calls the underlying driver exactly once, reads post-call output state, and appends immutable O(1) delivery edges.

The journal exposes only observation:

```text
integrate(start_s, end_s) -> DeliveredActuationIntegral
mark_uncertain(reason, at_s)
```

No learner-facing API can command an actuator.

Actual readback is authoritative. A driver that only echoes commands is uncertain for scored evidence until certified. Failed command/readback and unobservable asynchronous PWM ramps mark affected channels uncertain rather than interpolating.

### 7.2 Process-level recorder

Add `controller/runtime/learning_trajectory.py` with a process-lifetime `LearningTrajectoryRuntime` owned above individual modes and cooks.

```text
mode_entered(...)
mode_exited(...)
observe_temperature(...)
intervention(...)
configuration_changed(...)
seed_for(candidate_structure, at_s, measured_temp_c)
status()
close()
```

The shared mode skeleton emits the single fresh probe sample and boundary events. The recorder closes nominal 20-second frames on a stable monotonic grid. When a mode transition bisects a frame, it closes an exact short partial tail; partial tails are replay-only and never scored.

Smoke logic never imports/builds MPC and never receives an MPC command.

### 7.3 Segment boundaries

The current segment finalizes on:

- manual auger/fan/PWM;
- lid or safety event;
- Stop/Error;
- invalid/missing probe;
- uncertain auger delivery;
- clock jump or queue loss;
- cook/history rotation;
- units change;
- incompatible structure/actuation/fan/ambient semantics;
- unclean process restart.

Compatible Smoke→Hold is a phase transition within one segment, not a gap. Hold→Smoke finalizes the scored segment and begins new pre-roll.

## 8. Candidate-specific warm start

### 8.1 Delay-chain replay

Add a pure helper to `controller/mpc_model.py`:

```text
replay_delay_chain(intervals, *, theta, n_delay, initial_load) -> delay_states
```

It analytically advances the Erlang chain through exact delivered intervals. It does not read temperatures and cannot update an estimator.

### 8.2 Warm-up bound

For current fixed `n_delay=8` and `theta <= 1200 s`, unknown initial-state influence after `3*theta` is bounded by the Erlang-8 survival probability:

```text
exp(-8t/theta) * sum((8t/theta)^k / k!, k=0..7)
```

At `t=3*theta`, the bound is approximately `4.75e-5`. Retain at most:

```text
3 * 1200 / 20 = 180 pre-roll frames per segment
```

A candidate consumes `ceil(3*theta/20)` frames. If `n_delay` or the theta bound changes, the segment schema and derived quantile/cap must change together.

### 8.3 Hold entry

Before the first MPC solve:

1. select the exact compatible pre-roll suffix ending at Hold entry;
2. replay realized load with incumbent `theta/n_delay`;
3. reset estimator delay states from replay;
4. set chamber state to exact measured Hold-entry temperature;
5. reset disturbance to zero with high initial uncertainty;
6. record a typed estimator-seed diagnostic;
7. run the first solve.

If no valid pre-roll exists, the incumbent controls with an explicitly diagnosed conservative cold start. Learning and candidate activation remain fail-closed until candidate-specific warm-up is available.

### 8.4 Candidate fitting and activation

For each optimizer trial/candidate:

- replay that candidate’s own pre-roll;
- set chamber `T_c` to measured Hold-entry temperature;
- exclude Smoke temperatures and the anchor from residuals;
- use leading Hold inputs as replay-only warm-up when pre-roll is short;
- reject the segment for that candidate if it ends before warm-up completes.

Activation seeds the candidate by replay. It does not copy incumbent delay-chain values into a different theta model.

## 9. Durable segmented corpus

### 9.1 Ownership

Own the corpus repository and persistence worker at the long-lived control-process controller, above Smoke, Hold, MPC Controller, and individual cooks.

Hold teardown finalizes and barriers persistence but does not close the process corpus worker. One process owner performs the final close.

### 9.2 SQLite schema

Add the next datastore migration with:

#### `learning_trajectory_corpus`

Singleton schema/revision, retained counts, eviction/quarantine counters.

#### `learning_trajectory_segment`

Segment identity, state, compatibility/provenance digests, time bounds, Hold anchor, counts, rolling/final content digest, corpus revisions, break reasons, optional source digest.

#### `learning_trajectory_frame`

`(segment_id, ordinal)` primary key, kind (`pre-roll` or `scored`), interval identity, schema, canonical payload, frame digest.

#### `learning_fit_run`

Request/status, fit corpus manifest/digest, parent lineage, candidate generation/result, errors, and timestamps.

#### `model_challenger_state`

A singleton durable challenger state separate from activation authority:

- schema and CAS revision;
- phase: built/evaluating/qualified/activating/retired;
- challenger ID and origin/policy;
- exact incumbent/candidate descriptors;
- fit request/corpus identity/result/preparation;
- optional calibration manifest;
- evaluation epoch/round;
- consecutive/required wins;
- last decision/evidence high water;
- activation transaction link;
- retirement reason/timestamps.

### 9.3 Transaction rules

- Append/finalize/break-and-begin/counters/eviction are atomic.
- Append uses `(segment_id, next_ordinal, chain_digest)` CAS.
- Exact duplicates are idempotent; conflicting duplicates quarantine/finalize rather than overwrite.
- Frame chain digest is `SHA256(previous_digest || canonical_frame)`.
- A scored Hold frame and compact evidence are one worker transaction.
- Persistence queue rejection creates a gap and breaks the segment.
- Activation durability has highest FIFO priority; no SQLite work executes on the control tick.

### 9.4 Recovery

On crash:

- committed frames survive;
- open segment finalizes `unclean-restart` at the last committed frame;
- a new segment starts because monotonic epochs cannot join;
- incomplete fit runs become interrupted;
- pending forecast origins disappear;
- corrupt segment digests/counts quarantine only that segment;
- active/rollback authority remains usable.

## 10. Retention

At every committed revision:

- scored observations across all segments/partitions `<= 8640`;
- pre-roll frames `<= 8640` globally and `<= 180` per segment;
- segments `<= 256`;
- current open segment is never evicted.

Auto-roll a physically continuous segment after 180 scored rows, carrying up to 180 exact preceding intervals as pre-roll into the next segment. Evict oldest finalized whole segments by `(end_wall_ms, segment_id)` until all caps hold. Never trim a finalized segment into an invalid initial condition.

At 20 seconds, 8,640 scored rows represent 48 hours. Fit snapshots deserialize into compact numeric arrays rather than thousands of Pydantic objects.

## 11. Compatibility partitioning

The fit partition includes semantics that cannot be mixed:

- corpus/observation schema;
- model kind/physics structure and fixed delay count;
- held physics (`h_amb`, `sigma`);
- 20-second cadence;
- realized-load normalization, `u_max`, allocator/inverse mapping;
- scored fan authority/regime;
- ambient source/uncertainty/calibration semantics.

It excludes:

- incumbent/candidate digest and generation;
- learned `C_c`, `K_Q`, `theta`;
- setpoint and control cost/horizon weights;
- estimator covariance/tuning;
- numeric ambient values, which are supplied per frame.

A promotion alone is not a fit-partition change.

## 12. Multi-segment fit math

`GreyFitJob` carries immutable segment arrays.

For segment `s`:

1. replay candidate-specific pre-roll;
2. anchor chamber state to measured Hold-entry `T0`;
3. consume any required leading-Hold warm-up as zero-weight rows;
4. simulate scored rows using exact load and ambient;
5. emit residual/Jacobian rows only after warm-up.

For candidate-dependent warm-up masks, keep the optimization residual dimension fixed by zero-weighting masked rows. After convergence, freeze the final theta-derived masks and run one polish fit. Reject `warmup-mask-unstable` if theta crosses a mask boundary during polish.

Candidate and incumbent RMSE comparison uses the common conservative mask `max(w_candidate, w_incumbent)`.

Metrics:

- effective sample count excludes anchors/pre-roll/masked rows;
- pooled RMSE/max error use effective rows;
- per-segment and per-cook RMSE/bias/band are reported;
- identifiability stacks independently initialized Jacobian rows and computes the existing normalized smallest singular value;
- fit trigger reports pooled and per-cook excitation/levels/span.

Calibration frames enter fitting when otherwise eligible but never become causal validation origins.

## 13. Automatic challenger state machine

```text
CORPUS
  -> FITTING
  -> BUILT/EVALUATING (durable challenger, wins=0)
  -> completed all-horizon round
       loss -> RETIRED
       win  -> persist round/wins
  -> wins=2 + shared qualification -> QUALIFIED
  -> atomic challenger QUALIFIED -> activation PREPARED
  -> completed frame boundary -> activation ACTIVE
  -> authorize output on next solve
```

### 13.1 Shared gates

Both passive and operator candidates require:

- exact fit corpus/lineage;
- convergence, parameter bounds, fit margin, and identifiability;
- native estimator/controller build;
- dry solve and hardware timing;
- model/provenance/schema integrity;
- exact paired forecasts at 3/15/45/90/180 frames;
- two consecutive complete rounds where challenger beats incumbent at every horizon;
- durable final assessment and unblocked confidence;
- frame-boundary PREPARED→ACTIVE ordering;
- rollback/fallback compensation.

Operator origin additionally requires an immutable complete low/middle/high/coast calibration manifest. Explicit calibration fits and evaluates even when passive adaptation is disabled; the calibration command authorizes collection, not activation.

### 13.2 Stop/restart

Stop while evaluating:

- fences new origins;
- finalizes the trajectory segment;
- persists only complete rounds/wins;
- records partial epoch interrupted;
- closes ephemeral candidate resources;
- retains durable challenger.

Next cook/restart:

1. restore active/rollback authority;
2. reconcile/abort any legacy PREPARED activation;
3. validate challenger against final incumbent/config/corpus lineage;
4. rebuild/dry-solve candidate;
5. warm from new pre-roll;
6. increment evaluation epoch and begin fresh origins;
7. retain prior completed win count only for exact unchanged lineage.

Pending origins never cross the boundary.

A teardown-created candidate starts `EVALUATING`, wins 0. Teardown cannot mint confidence or activate it.

### 13.3 Staleness

Cook/session/Stop alone does not stale a challenger. It retires on:

- incumbent digest/generation change;
- candidate descriptor/generation change;
- incompatible config/structure/actuation/corpus identity;
- calibration-manifest invalidation;
- losing causal round;
- activation abort/failure;
- rollback/fallback;
- schema corruption.

## 14. Remove operator review

Delete in one clean cutover:

- `ActivationPolicy.OPERATOR_REVIEWED`;
- MPC `READY_FOR_REVIEW` state/outcome;
- reviewed checkpoint and teardown confidence bypass;
- manual activation request/response contracts;
- activation POST endpoint and service workflow;
- generated schema/client API;
- digest/decision confirmation inputs and buttons in `MpcLearningView`;
- operator-review UI copy/tests.

Retain explicit rollback. PID-SP’s unrelated ready-for-review UI semantics remain.

The report exposes automatic evaluation progress:

- warming/collecting/evaluating/interrupted/qualified/activating/active;
- epoch/round;
- completed and required horizons;
- wins/required wins;
- resumed-from-previous-cook;
- pending origins;
- exact corpus/candidate lineage.

## 15. Offline refit cutover

Remove production single-cook authorities:

- `PassiveGreyHistory` and `TeardownGreyHistory` as fit stores;
- raw `MpcCore._history` refit dependence;
- synchronous `refit_from_cook(history)` production path;
- direct `accepted-next-cook` adoption;
- operator teardown unblocked confidence.

`enable_identification` continues to schedule a cumulative-corpus fit at Stop. `enable_online_adaptation` controls passive mid-cook fitting/activation. Explicit calibration schedules fitting independently. Every resulting candidate requires causal authorization.

## 16. Migration

Use forward-only migrations:

1. Add corpus, frame, fit-run, and challenger tables.
2. Preserve existing active/rollback model authority and evidence.
3. Bump model evidence to schema 4, grey checkpoint to v5, learning report to v3, and trace schema for new segment/seed diagnostics.
4. Keep historical evidence immutable as audit.
5. Legacy active operator-reviewed models remain active; metadata canonicalizes to causal-auto without reactivation.
6. Legacy PREPARED activation aborts safely. An exact validated candidate may import as `EVALUATING`, wins 0; otherwise retire it.
7. Legacy ready-for-review candidates may import as `EVALUATING`, wins 0 only when descriptor/window/incumbent/calibration completeness validate. Old in-sample confidence is ignored.
8. Current v6/v7 database model authority migrates safely.
9. Trace schema v7 imports idempotently into the corpus only when exact allocation/delivery semantics validate.
10. Trace schema v6 remains audit-only; no requested/zero actuation is fabricated.
11. Historical cookfiles remain readable and explicitly report absent pre-roll.
12. Remove the manual activation endpoint with no compatibility shim; deploy backend and versioned web assets atomically.

The stale checkpoint-provenance defect disappears because reports project one durable challenger row rather than combining live candidate fields with old checkpoint origin/policy fields.

## 17. Diagnostics and cookfiles

Expose:

- corpus schema/revision and current partition;
- scored retained/8640;
- pre-roll retained/caps;
- open/final/quarantined/evicted segment counts;
- distinct cooks/sessions and time range;
- break reasons and last persistence/recovery error;
- fit corpus/manifest digest;
- retained/effective/warm-up-masked rows;
- segment/cook support and errors;
- excitation/levels/span;
- singular values/condition/identifiability;
- challenger epoch, horizons, wins, and reset reason;
- estimator seed status: exact/short/absent/uncertain.

Cookfiles include this cook’s segment IDs/digests/counts and normalized global report. They do not duplicate the global cross-cook corpus.

Exact replay validates content and prefix digests, then runs the production segmented simulator. Evicted/corrupt source is an explicit non-replayable result.

## 18. Failure and safety invariants

1. Observation code has no actuator authority.
2. Actual delivered input is authoritative.
3. Recorder/corpus failure disables learning, records a gap, and does not affect control/safety.
4. No fit or forecast crosses a physical/configuration gap.
5. Smoke temperature never contributes to fitting or causal confidence.
6. Every segment simulates independently under one shared model.
7. Unknown candidate initial state blocks evidence; it is never silently zeroed.
8. Candidate commands cannot reach hardware before durable ACTIVE.
9. Promotions do not clear fit corpus.
10. Incumbent changes reset causal state.
11. Pending origins do not survive Stop/restart.
12. Corrupt corpus/challenger state cannot invalidate the active safe model.
13. Persistence work remains off the control worker.
14. Existing PREPARED crash-abort and rollback compensation remain authoritative.

## 19. Verification requirements

### 19.1 Unit/property tests

- contract validation, ownership, canonical digests, size bounds;
- delivery journal exactness and uncertain readback;
- segment begin/append/break/finalize/idempotency/conflict/quarantine;
- crash boundaries and CAS behavior;
- randomized retention proving scored/pre-roll/segment caps after every mutation;
- independent simulation versus illegal concatenation;
- Smoke temperature perturbation does not alter residuals;
- Smoke delivered-input perturbation changes warmed state;
- candidate theta warm-up masks and stability rejection;
- pooled residual, per-cook veto, and Jacobian identifiability math;
- causal progress persistence and lineage resets;
- automatic operator/passive gate equivalence;
- no manual review route/contracts.

### 19.2 Default-running E2E

1. Smoke→Hold warms incumbent before first solve without any MPC call in Smoke.
2. Cold Hold and Smoke→Hold versions of one physical trajectory fit stable parameters.
3. Cook A and Cook B contribute to one fit manifest.
4. Corpus survives promotion, rollback, Stop, and restart.
5. Corpus exceeds 8,640 rows and evicts deterministic whole segments while fitting remains valid.
6. Supplied cook sequence-140 candidate survives Stop, resumes next cook, obtains two full winning rounds, and activates automatically.
7. Operator and passive origins traverse identical production gates; operator includes calibration completeness.
8. No candidate output exists before durable ACTIVE.
9. v6/v7 authority migration preserves active/rollback and safely imports/retires challengers.
10. Abrupt death preserves committed frames, finalizes unclean segment, interrupts fit, drops pending origins, and restores completed progress.
11. Manual/lid/safety/unknown/gap each produce exact boundaries with no cross-gap residual.
12. Cookfile export/import reproduces segment and fit digests.
13. Corrupt one segment while active control continues; segment quarantines and confidence fails closed.
14. Dashboard/browser shows automatic progress and contains no activation confirmation flow.

The supplied-cook replay remains a permanent fixture because it has already exposed malformed observation projection, Stop history loss, stale provenance, insufficient causal horizon, and zero-delay-state boundary bias.

## 20. Rollout sequence

1. Land typed trajectory/corpus/challenger contracts and forward database migration.
2. Establish one process-owned persistence worker and corpus repository.
3. Add delivered-actuation journal and cross-mode recorder without any consumer.
4. Add Hold-entry estimator seed and candidate-specific delay replay with diagnostics.
5. Cut fitting, triggering, identifiability, teardown scheduling, and replay to segmented corpus in one change; delete old volatile fit authorities.
6. Persist/restore challenger and complete causal progress.
7. Unify passive/operator causal-auto activation and eliminate teardown confidence/direct adoption.
8. Bump reports/evidence/checkpoints/contracts and regenerate clients.
9. Remove manual activation endpoint/service/UI and stale tests in one clean cutover.
10. Run focused, production E2E, supplied-cook replay, migration, restart, retention, and browser verification.

## 21. Acceptance criteria

The architecture is complete only when all are true:

- 8,640 scored observations accumulate across compatible cooks/promotions and remain bounded;
- Smoke exact delivery warms Hold without entering fit residuals;
- the same candidate parameters are fitted over independently initialized compatible segments;
- model promotion never clears fit corpus;
- causal evaluation resets only on lineage change and can resume completed wins across clean cook boundaries;
- calibration candidates have no manual path and cannot activate with weaker evidence than passive candidates;
- cook-end fitting cannot directly activate a model;
- active output remains gated by durable frame-boundary activation;
- current v6/v7 authority migrates safely;
- exact diagnostics/cookfile replay reproduces fit identity/results;
- default-running E2E proves the complete lifecycle and supplied-cook regression.
