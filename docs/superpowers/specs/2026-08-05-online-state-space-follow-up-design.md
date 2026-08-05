# Online Innovation State-Space Follow-up — Design

**Date:** 2026-08-05  
**Status:** Approved for planning after scheduled ARX  
**Depends on:** `2026-08-05-online-scheduled-arx-adaptation-design.md`

## 1. Purpose

Repair and prove the innovation state-space learner after the scheduled-ARX production path is complete. The bakeoff found the best closed-loop simulator control from the state-space arm, but its online challenger did not demonstrate reliable refresh/recovery. This project fixes that failure before state-space can become a promotion candidate.

The project reuses the scheduled-ARX observation, asynchronous runner, persistence, trace, scoring, promotion, rollback, allocator, and 2-second/20-second pulse contracts. It does not build a parallel framework.

## 2. Entry criteria

Work starts only after the scheduled-ARX project provides:

- canonical completed-frame observations;
- bounded asynchronous observation delivery;
- persisted incumbent/challenger roles;
- prequential 60/300-second scoring;
- structured update, evaluation, promotion, and rollback trace records;
- fixed-seed GrillSim, MAKGrillSim, and real-MAK evidence generation;
- a working linear MPC production policy.

## 3. Required investigation

Reproduce state-space candidate-refresh exhaustion from the final bakeoff using the original fixed seeds and wrong-gain, wrong-pole, and wrong-delay initializations.

For every failed refresh, record:

- available and admitted samples;
- Hankel/block dimensions;
- candidate orders and delays attempted;
- rank and singular-value spectrum;
- regularization and conditioning;
- stability/gain projection outcome;
- state-alignment residual;
- prediction and braking scores;
- explicit rejection reason;
- refresh runtime.

A refresh that yields no candidate is a typed rejection, never an empty success.

## 4. Model and refresh contract

Retain the innovation model:

\[
x_{k+1}=Ax_k+Bq_k+E(T_{amb,k}-T_k)+K\nu_k,
\qquad
T_k=Cx_k+Dq_k+\nu_k.
\]

The refresh implementation must:

- use a bounded rolling/stratified record from canonical observations;
- perform deterministic subspace identification;
- evaluate configured candidate orders and delays without depending on dictionary or worker ordering;
- reject rank-deficient candidates with structured evidence;
- project poles inside the configured stability boundary;
- require finite positive bounded steady gain;
- estimate bounded process and measurement covariance;
- align the candidate realization to the incumbent realization;
- map the current incumbent state into the candidate coordinates;
- reject a candidate whose aligned current-output error exceeds 2 degrees Celsius;
- leave the incumbent untouched on every failed refresh.

No fallback may silently reuse the incumbent and report it as a new candidate.

## 5. Persistence

Add a nested state-space snapshot schema under the existing online-adaptation snapshot. It contains:

- model order and delay;
- `A`, `B`, `C`, `D`, `E`, and `K` matrices;
- process and measurement covariance;
- current aligned state;
- bounded refresh record metadata;
- role generation, effective samples, and refresh counters;
- last complete refresh diagnostics.

Snapshot validation checks dimensions, finite values, stability, gain, covariance, and state/output consistency. A rejected state-space member does not invalidate a restorable scheduled-ARX or grey-box incumbent.

## 6. Evaluation and promotion

State-space uses the same untouched 60/300-second prediction, braking, excitation, continuity, two-win, prospective-solve, frame-boundary handoff, and rollback rules as scheduled ARX.

State-space may replace a scheduled-ARX incumbent only when:

- both models are scored on identical origins and realized future inputs;
- aligned state error is at most 2 degrees Celsius;
- state-space prediction score is strictly lower;
- state-space braking is no worse;
- the state-space prospective linear-MPC solve is valid;
- two consecutive eligible five-minute windows pass;
- the empirical cross-domain gate in Section 8 passes.

A promotion switches complete prediction-model state. The existing linear MPC policy, analytic equilibrium baseline, allocator, and pulse scheduler remain unchanged.

## 7. Trace and status

Reuse the model observation event unchanged. Extend model evaluation and lifecycle variants with:

- candidate order and delay;
- singular values and effective rank;
- candidate-exhaustion reasons;
- alignment transform digest;
- aligned output/state error;
- covariance and pole summaries;
- refresh duration;
- state-space snapshot digest.

Status adds current state-space order, delay, refresh outcome, alignment error, and refresh p99 without exposing new tuning controls.

## 8. Proof gate

Run the same fixed-seed GrillSim, MAKGrillSim, and chronological real-MAK evidence used by scheduled ARX.

State-space runtime selection remains unavailable unless all conditions hold:

- wrong-gain, wrong-pole, and wrong-delay scenarios produce explicit valid candidates or scientifically justified typed rejections;
- at least one wrong-model scenario demonstrates accepted recovery without contaminating its scoring window;
- state-space aggregate closed-loop score is strictly better than scheduled ARX;
- neither simulator regresses overshoot, settling, braking, relay transitions, stale results, or reachability behavior;
- real-MAK 60/300-second prediction does not regress;
- snapshot/restore reproduces the next prediction and update;
- refresh p99 is no greater than 250 ms;
- linear MPC solve p99 remains no greater than 50 ms.

## 9. Non-goals

- No new observation or trace framework.
- No second linear MPC solver.
- No automatic preference for state-space because its earlier static control score was better.
- No runtime selector before candidate refresh, alignment, persistence, and empirical proof pass.
- No changes to fuel/fan coupling, allocator ownership, pulse timing, or trace retention.
