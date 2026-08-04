# MPC Control Quality — Coupled Combustion, Pulse Scheduling, and Calibration

**Date:** 2026-08-04  
**Status:** Approved for planning  
**Supersedes:** The earlier minimum-firing and 60-second Hold-cycle design in this file's history.

## 1. Goal

Improve MPC control quality without letting it learn physically nonsensical fuel/air combinations or block the safety loop:

1. represent heat demand as one normalized combustion load;
2. map that load through one coupled auger/fan allocator;
3. realize arbitrarily low mean fuel rates with mechanically bounded pulses rather than a configured duty floor;
4. consume threaded MPC results asynchronously while actuation and safety continue at normal cadence;
5. use analytic equilibrium feed-forward plus transient MPC correction;
6. report upper reachability honestly;
7. remove the unsupported braking-distance horizon floor after its coast evidence is reproduced;
8. persist enough shared controller/allocator/scheduler/hardware evidence to diagnose failures.

## 2. Prerequisites

Execute these approved designs first:

- `2026-08-04-controller-catalog-cleanup-design.md`: only PID, PID-SP, and MPC remain;
- `2026-08-04-controller-control-trace-design.md`: typed PID/PID-SP/MPC traces persist in SQLite for 30 days.

The trace design owns controller diagnostics, SQLite storage, replay, retention, and removal of MPC CSV logging. This design emits MPC allocator and framed-pulse events into that shared trace; it does not create another logger.

## 3. Incident and prior evidence

A real 450 °F Hold cook on the MAK grill peaked near 520 °F. Online identification itself worked; most remaining error came from the controller's operating point and actuator realization:

- the model's input `Q` was constrained to a configured positive floor;
- the allocator mapped that floor to `u_min`;
- Hold clamped controller output to `u_min` and repeated it in a fixed `HoldCycleTime`;
- the controller had no analytic steady firing baseline;
- the runtime assumed periodic duty was delivered rather than measuring the completed interval;
- a model-derived braking floor forced about 150 seconds of horizon for a measured coast around 1.2 °F.

The old proposal treated `u_min` and `HoldCycleTime` as independent control knobs and proposed a 60-second global cycle. That is rejected. Effective minimum fuel is coupled to pulse duration and scheduling, and PID/PID-SP should not be retuned as a side effect of MPC work.

## 4. Scope

### 4.1 In scope

- shipped matrix harness configuration and explicit overrides;
- one normalized MPC combustion-load input;
- coupled auger/fan allocation;
- 2-second auger pulse quantum and 20-second scheduling frame;
- asynchronous revisioned result consumption and slow/stale solve reporting;
- upper setpoint reachability;
- analytic equilibrium feed-forward with residual MPC correction;
- model/net/log schema cutover required by input semantics;
- closed-loop experiments on `GrillSim` and `MAKGrillSim`;
- calibration-data fit checks;
- measured braking-horizon cutover;
- typed trace emission through the shared control-trace infrastructure.

### 4.2 Out of scope

- learning fuel and fan as independent MPC inputs;
- changing PID or PID-SP control behavior;
- changing the shipped 25-second PID/PID-SP `HoldCycleTime`;
- a global settings migration of `u_min`/`HoldCycleTime`;
- a nonlinear/piecewise fan curve without air/combustion measurements;
- platform-specific pulse timing without measured platform evidence;
- longer horizons, terminal costs, fan-based braking, or offset-free machinery beyond the existing disturbance estimator;
- changing `GrillSim`/`MAKGrillSim` calibration to make an arm pass.

## 5. Core types and enum rules

Internal mode/state discriminants are enums, never ad-hoc strings:

- `ControllerType` from the shared trace design;
- `ActuationMode.FIXED_CYCLE` and `ActuationMode.FRAMED_PULSE`;
- `ReachabilityState.REACHABLE`, `UNREACHABLE_HIGH`, and `UNKNOWN_MODEL`;
- typed clamp, stale-result, safety-inhibit, and scheduler-reset reasons.

Only API/MQTT/SQLite serialization emits enum values.

Use frozen, slotted dataclasses for combustion commands, scheduler frames, revisioned controller results, and feasibility reports.

## 6. Normalized combustion model

MPC controls one dimensionless combustion load:

\[
q \in [0,1]
\]

`q=0` means no requested fuel release; `q=1` means maximum configured combustion authority. The thermal model remains:

\[
C_c \dot T_c = K_Q q_{\text{delayed}} - h_{amb}(T_c-T_{amb}) - q_{rad}(T_c,T_{amb}) + d
\]

`K_Q` is learned against normalized load. Delay, ambient loss, radiation, and disturbance estimation keep their current meanings.

Remove user-configurable MPC `Q_min` and `Q_max`; the normalized bounds are structural. This is not independent fuel/air learning.

## 7. Coupled combustion allocator

MPC returns only `q`. The allocator produces one immutable `CombustionCommand`:

\[
u_{auger,requested}=q\,u_{max}
\]

When MPC has PWM fan authority:

\[
f_{requested}=f_{min}+q(f_{max}-f_{min})
\]

When it lacks fan authority, fan command is `None` and Hold's existing fan path remains authoritative.

The scalar axis preserves a physically constrained monotone fuel/air relationship. MPC cannot trade extra fuel against too little air or independently exploit an unmeasured fan effect.

`u_max`, `fan_min_pct`, and `fan_max_pct` remain explicit hardware/combustion authority bounds. The model may learn aggregate thermal response inside them; it may not learn past them.

The allocator exposes exact forward/inverse pure functions and a revision identifier. Applied normalized load is reconstructed from measured mean auger duty through the inverse; any fan/quantization mismatch remains visible in trace diagnostics.

## 8. Why the fan curve stays linear

The recorded MAK calibration cook varied `Q` from 5 to 100 but held fan at 100% throughout. It cannot identify a fan curve.

Open-loop comparison on both shipped simulators found no material control-quality justification for a speculative low-fire piecewise curve: it shifted mid-fire equilibrium by roughly 2 °F and did not improve ripple materially. Therefore the existing monotone linear-envelope idea remains, normalized to zero-origin firing load.

A future fan-curve change requires measurements that vary fan and fuel under observable combustion constraints; chamber temperature alone is insufficient to identify an optimal air/fuel mixture.

## 9. Framed pulse scheduler

### 9.1 Timing authority

MPC ignores `cycle_data.u_min` and `cycle_data.HoldCycleTime`.

The grill-platform actuation capability supplies a non-user-configurable timing dataclass:

- pulse quantum: 2 seconds;
- scheduling frame: 20 seconds.

These are hardware/mechanical constraints, not thermal tuning parameters. PID/PID-SP continue using the existing fixed 25-second cycle and configured `u_min`/`u_max`.

### 9.2 Algorithm

At each 20-second frame boundary:

1. take the latest accepted `CombustionCommand`;
2. add `requested_auger_duty × 20 s` to fractional on-time credit;
3. schedule the largest whole number of 2-second pulse quanta not exceeding that credit or the frame;
4. subtract scheduled on-time and carry the remainder to the next frame;
5. place scheduled quanta contiguously, avoiding needless relay toggles;
6. measure actual auger-on time and transitions from hardware state.

This realizes duties below one pulse per frame by carrying credit across frames. For example, 10% fuel demand becomes 2 seconds ON / 18 seconds OFF; lower demand skips whole frames while preserving its long-window mean.

A skipped runtime frame is discarded, not replayed. A controller result arriving mid-frame applies at the next frame boundary. Stop, Error, manual override, and lid inhibit preempt immediately rather than waiting for a boundary.

### 9.3 Reset rules

Reset credit and scheduler state on:

- Hold entry/exit;
- Stop/Error or universal safety guard;
- lid-open inhibit;
- manual auger takeover;
- controller replacement/fallback/reconfigure;
- actuation-mode change.

Suppressed fuel is never delivered later as catch-up heat.

## 10. Applied-output accounting

The scheduler integrates actual auger-on time over completed intervals. At each controller feedback boundary it reports:

- requested auger duty;
- realized auger duty;
- inverse-mapped applied normalized load;
- actual fan duty;
- interval start/end and completeness;
- output-source/inhibit enum.

MPC's estimator and cook history consume applied normalized load, not the requested load. Manual/lid/safety intervals remain in the record with correct provenance.

Legacy top-level `cycle_ratio` remains requested mean auger duty for compatibility. For framed-pulse MPC, fixed-cycle on/off/cycle fields are zero and structured actuation status reports the real frame/slot values.

## 11. Asynchronous solver/result flow

The threaded runner publishes immutable results containing output, matching diagnostics, monotone revision, solve start/end/duration, and completion timestamp.

Hold never waits for a solve. It:

- polls the latest result;
- accepts it only when revision advances;
- keeps actualizing the last valid combustion command otherwise;
- continues the 2-second/20-second scheduler and all safety checks at normal loop cadence.

A solve duration greater than the configured control period is a deadline miss. A result older than two control periods is stale. Status and shared trace record solve duration, result age, total/consecutive misses, and stale/recovered transitions. One deduplicated user advisory appears on the first sustained stale condition and clears on recovery.

A stale command remains bounded by the last accepted authority limits. Existing policy-failure behavior may hold the last move briefly, but repeated failure remains visible and never blocks Stop/lid/manual control.

## 12. Equilibrium feed-forward and residual MPC

Shared thermal primitives compute equilibrium load:

\[
q_{ss}(T_{set})=
\frac{h_{amb}(T_{set}-T_{amb})+q_{rad}(T_{set},T_{amb})-d}
{K_Q}
\]

MPC uses bounded `q_ss` as the steady baseline and optimizes only transient residual `Δq`:

\[
q = \operatorname{clip}(q_{ss}+\Delta q,0,1)
\]

The objective penalizes residual motion without making the policy relearn the steady firing rate on every solve. Traces record raw equilibrium, residual, raw combined load, and bounded load.

Feed-forward ships only after a fixed-seed experiment demonstrates material closed-loop improvement over the same scheduler with feed-forward disabled.

## 13. Feasibility and advisory behavior

There is no minimum-firing-floor advisory. Framed pulses can realize arbitrarily low long-window mean fuel without clamping to `u_min`.

Upper reachability remains:

- compute model-derived `q_ss` at the active target;
- `REACHABLE` when `q_ss ≤ 1` within explicit tolerance;
- `UNREACHABLE_HIGH` when `q_ss > 1`;
- `UNKNOWN_MODEL` when no calibrated model can support the claim.

An unreachable-high report contains target, predicted steady maximum temperature/load, model revision/provenance, and binding authority. Heating continues at maximum safe authority; one deduplicated warning explains that the learned model predicts the target cannot be reached.

No settings are mutated automatically.

## 14. Matrix harness

`docs/superpowers/experiments/controller_matrix.py` must:

- derive shipped controller/cycle/model defaults at call time;
- accept explicit per-run overrides and print them in output;
- run both `GrillSim` and `MAKGrillSim`;
- emulate fixed-cycle PID/PID-SP and framed-pulse MPC faithfully;
- feed measured delivered duty/load back to controllers;
- label upper infeasibility separately from control error;
- never let an infeasible row win ranking by a misleading metric.

A regression test substitutes defaults after import to prove there is no captured second convention.

## 15. Empirical scheduler and allocator evidence

Committed experiment:

- `docs/superpowers/experiments/mpc_pulse_allocator.py`;
- `docs/superpowers/experiments/_mpc_pulse_allocator.json`.

### 15.1 Calibration fit

| Input representation | RMSE | Meaning |
|---|---:|---|
| recorded Q percent | 2.3358 °C | existing scale |
| normalized `q=Q/100` | 2.3358 °C | identical trajectory, scaled `K_Q` |
| inferred old affine auger duty | 2.4324 °C | worse no-offset grey-box fit |
| proposed linear auger duty | 2.3358 °C | pure scale of normalized `q` |

This supports a zero-origin normalized firing axis. It does not validate a fan curve because fan was fixed.

### 15.2 Pulse/frame sweep at `q=0.5`

| Scheduler | Transitions/hour | GrillSim band | MAKGrillSim band |
|---|---:|---:|---:|
| 1 s / 10 s | 720 | 7.12 °F | 0.67 °F |
| **2 s / 20 s** | **360** | **8.97 °F** | **1.12 °F** |
| legacy affine / 25 s | 288 | 9.22 °F | 1.29 °F |
| 2 s / 30 s | 240 | 12.99 °F | 1.76 °F |

The approved 2-second/20-second point halves switching versus 1-second/10-second scheduling while retaining slightly lower open-loop ripple than the legacy cycle on both plants.

At normalized load 0.01, zero-origin allocation delivered about 0.009 duty. The old affine floor delivered 0.10–0.12 and held GrillSim around 185–207 °F and MAKGrillSim around 316–354 °F. This is the floor defect the scheduler removes.

These are open-loop evidence, not the shipment gate.

## 16. Closed-loop shipment gate

Before production defaults change, run fixed scenarios/seeds on both plants comparing:

1. current affine allocator + fixed 25-second MPC cycle;
2. normalized coupled allocator + 2-second/20-second scheduler, feed-forward disabled;
3. the same scheduler with equilibrium feed-forward enabled.

Record:

- RMSE/IAE, overshoot, undershoot, settle time, and percent within band;
- steady peak-to-peak band;
- auger on-time/pellet proxy;
- requested-versus-realized load error;
- transitions/hour;
- solver duration, deadline misses, stale-result episodes;
- upper feasibility labels;
- complete shared trace session IDs.

The new path ships only if:

- it eliminates the low-fire floor;
- it does not regress safety/lid recovery;
- switching respects the 2-second quantum and measured transition envelope;
- control quality materially improves or remains comparable while applied-load fidelity improves;
- feed-forward beats the same scheduler without feed-forward on the ranked scenarios;
- delayed-solver injection proves actualization and safety remain responsive.

## 17. Slow-solver injection

Inject solve delays of one, two, and several control periods. Verify:

- Hold/safety loop cadence remains normal;
- frame boundaries and actual outputs remain driven by the last accepted command;
- no result is accepted twice;
- no stale command exceeds authority;
- Stop/lid/manual preempt immediately;
- one stale advisory appears and later clears;
- shared trace contains the complete deadline/staleness sequence.

## 18. Model, policy, settings, and calibration cutover

Normalized input changes learned gain scale and allocator behavior. Therefore:

- advance MPC model snapshot schema and reject old snapshots cleanly;
- regenerate neural policy artifacts against normalized input and new allocator assumptions;
- version allocator and model provenance in snapshots/traces;
- remove MPC `Q_min`/`Q_max` manifest settings through the next settings shape migration;
- delete their stale stored config keys;
- keep PID/PID-SP `u_min`, `u_max`, and `HoldCycleTime` settings unchanged;
- calibrate from typed SQLite control traces per the shared trace design;
- do not silently mix legacy CSV/input semantics with normalized samples.

## 19. Braking-horizon cutover

The existing braking-distance horizon floor is re-derived against a coast phenomenon two orders of magnitude larger than the measured incident. Before removal:

1. run a committed open-loop coast experiment on both plants;
2. record cut temperature, peak, rise, seconds-to-peak, and nominal bound;
3. reproduce the design evidence;
4. then remove `longest_braking_distance`, effective/derived horizon promotion, and auto-raise messaging;
5. build MPC with configured `n_horizon` only;
6. keep the existing `COAST_BOUND=1.45` product-factor tests unchanged.

The experiment precedes deletion in revision order.

## 20. Shared trace requirements

The shared controller trace must connect, by session/result revision:

- measured temperature/setpoint and MPC state estimate;
- equilibrium, residual, raw/bounded normalized load;
- allocator auger/fan request and bounds;
- scheduler credit, scheduled/actual on-time, transitions, and resets;
- actual fan/auger output and applied load;
- solve timing/staleness;
- safety/manual/lid provenance;
- model revision and upper feasibility.

Rows flush to SQLite every five seconds during the cook and expire after 30 days. Replay must detect corrupted frame accounting. No MPC CSV logger remains.

## 21. Acceptance requirements

- **R1:** Harness uses shipped defaults dynamically, records explicit overrides, both plants, delivered actuation, and feasibility-aware rankings.
- **R2:** MPC has one normalized combustion-load input and one coupled auger/fan allocator; no independent fuel/air optimization.
- **R3:** MPC ignores fixed-cycle `u_min`/`HoldCycleTime` and uses measured 2-second/20-second framed pulses with fractional carry.
- **R4:** Safety/manual/lid/controller changes reset/preempt without catch-up heat.
- **R5:** Threaded results are revisioned/nonblocking; deadline/stale state warns and recovers visibly.
- **R6:** Applied load comes from measured delivered auger duty and feeds estimator/history.
- **R7:** Analytic equilibrium feed-forward plus residual MPC passes the measured improvement gate.
- **R8:** Only upper reachability remains; heating continues safely and settings never mutate.
- **R9:** Model/net/settings/calibration semantics cut over cleanly with no compatibility shims.
- **R10:** Closed-loop scheduler/allocator runs pass on GrillSim and MAKGrillSim with trace evidence.
- **R11:** Braking floor is removed only after committed coast evidence; coast factor remains unchanged.
- **R12:** Shared typed SQLite trace covers controller, allocator, scheduler, hardware, and safety for PID/PID-SP/MPC.
