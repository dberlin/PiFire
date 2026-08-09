# PID-SP Opportunistic 900-Second Learning

## Problem

PID-SP passively identifies a first-order-plus-dead-time or integrating grill model from commanded auger duty and measured chamber temperature. The trusted model drives the Smith predictor and persists across cooks.

The current initial-trust floors require 3,600 accepted seconds and 240 accepted observations. PID-SP runs on the fixed 20-second auger frame, so the observation-count floor is 80 minutes. A candidate then needs 20 stable evaluations. In the simulator matrix, first trust therefore arrived after roughly 87–92 minutes on GrillSim and 90–92 minutes on MAKGrillSim.

The target is to let a sufficiently identifiable grill activate learning after about 900 seconds without forcing an immature model onto a slow grill. Cooking tolerates modest model error, but a model that contradicts basic chamber physics or cannot distinguish dead time must still remain inactive.

## Decision

PID-SP uses 900 seconds as its earliest possible full-model activation, not as a deadline or one-shot checkpoint.

Change the initial eligibility floors in `controller/fopdt_identifier.py`:

- `MIN_ACCEPTED_SECONDS`: 3,600 seconds to 500 seconds;
- `MIN_ACCEPTED`: 240 observations to 25 observations.

Keep unchanged:

- `CONFIRM_WINDOW = 20`;
- `MIN_HOLD_DUTY_SAMPLES = 60`;
- the 15°F temperature-span gate;
- duty standard-deviation and sustained-transition gates;
- FOPDT and IPDT physical bounds;
- relative-standard-error limits;
- the delay-bank promotion margin;
- material-change, blending, distrust, persistence, and restore behavior.

At the fixed 20-second cadence, the 25th accepted regression observation arrives near 520 seconds after the initial temperature anchor. That observation can begin confirmation. Nineteen further agreeing observations put the earliest activation at 900 seconds.

## Continuous Evaluation

Every accepted temperature observation continues to call the identifier evaluation path. There are no scheduled decisions specifically at 900 or 3,600 seconds.

Once the initial count, accepted-time, excitation, and temperature-span gates pass:

1. Every accepted 20-second observation evaluates the FOPDT delay bank and, when needed, the integrating bank.
2. A finite, physical, sufficiently certain winner with the required residual margin enters or advances confirmation.
3. A sample with no gated winner pauses confirmation. It does not discard prior agreeing evidence.
4. A change of model form or dead-time candidate restarts confirmation at one.
5. Continuous parameters that move beyond their confirmation tolerances restart confirmation at one.
6. The twentieth agreeing evaluation activates the model.
7. If no model is ready at 900 seconds, the same process continues on every subsequent accepted observation until a candidate earns trust.

Invalid temperatures, invalid timing intervals, and observations spanning uncommanded output gaps remain rejected and do not advance any gate.

## Why Activation Must Not Be Forced

The retained gates answer different questions:

- count and accepted time prevent decisions from a trivially short history;
- excitation and temperature span establish that the cook contains usable information;
- physical bounds reject models that do not describe a grill;
- relative errors reject parameters the accumulated data does not determine;
- the delay margin rejects an arbitrary winner among indistinguishable delays;
- confirmation rejects transient winners and parameter churn.

Only the coarse count and time floors are reduced. The evidence-dependent gates continue to decide whether a particular grill is ready.

A forced 900-second model is specifically prohibited. The implementation must not select the lowest-residual candidate after the deadline when that candidate fails physics, uncertainty, margin, or confirmation.

## Hold-Duty Learning

The early integrating estimate used for hold duty remains gated by 60 accepted observations. It normally becomes available near 1,220 seconds at the 20-second cadence, or later when its own physical and certainty requirements do not pass.

The hold-duty threshold is not reduced to reach 900 seconds. Isolated simulation showed that doing so slightly reduced time within ±5°F on both plants. Full-model trust may nevertheless provide a hold duty at 900 seconds when every full-model gate passes, as occurred on GrillSim.

## Status and Persistence

The existing identifier status remains the source of learning progress:

- `accepted` and `accepted_seconds` report accumulated evidence;
- `confirming` reports confirmation progress;
- `trusted` distinguishes active learning from collection;
- `candidates_passing`, residuals, excitation, temperature span, and transition status explain why activation is waiting.

No countdown or promise that activation will occur at 900 seconds is introduced. User-facing wording must describe 900 seconds as the earliest activation and state that slow or unexcited cooks continue collecting evidence.

A restored model remains trusted on the first tick of a subsequent cook. The reduced initial floors apply only to earning or revising a model from current-cook evidence; they do not delay persisted models.

## Simulation Evidence

The design was evaluated with the production PID-SP controller, production framed-pulse scheduler, and unchanged GrillSim and MAKGrillSim plants. The paired matrix used ten deterministic seeds for each plant and each of these scenarios:

- 3.5-hour steady 225°F;
- 3.5-hour steady 450°F;
- 4-hour 225→275°F step.

The proposed arm changed only the two initial eligibility floors. It retained the 20-observation confirmation and the 60-observation hold-duty threshold.

### Opportunistic design versus current behavior

| Plant and scenario | Current median trust | Proposed median trust | Mean change within ±5°F | Mean overshoot change |
|---|---:|---:|---:|---:|
| GrillSim 225°F | 5,200 s | 900 s | +3.32 percentage points, 95% CI [-0.67, +7.14] | 0.00°F |
| GrillSim 450°F | 5,520 s | 900 s | -0.73 points, 95% CI [-3.09, +1.72] | -0.62°F |
| GrillSim 225→275°F | 5,200 s | 900 s | +3.74 points, 95% CI [+1.85, +5.48] | 0.00°F |
| MAKGrillSim 225°F | 5,380 s | 5,200 s | 0.00 points | 0.00°F |
| MAKGrillSim 450°F | 5,520 s | 5,270 s | 0.00 points | 0.00°F |
| MAKGrillSim 225→275°F | 5,380 s | 5,200 s | 0.00 points | 0.00°F |

All 30 GrillSim runs activated at exactly 900 seconds. None of the 30 MAKGrillSim runs activated by 900 seconds. MAK activation ranged from 4,240 through 6,760 seconds because its slower chamber needed more data to pass the unchanged evidence gates.

### Forced activation evidence

A diagnostic arm forced the best available integrating candidate into service at 900 seconds even when the normal gates refused it.

On GrillSim:

- steady 225°F time within ±5°F changed by a mean -9.12 percentage points and a worst-seed -31.34 points;
- the 225→275°F step changed by a mean -4.75 points and a worst-seed -21.07 points;
- step overshoot increased by a mean 1.00°F and a worst-seed 6.06°F.

On MAKGrillSim at 450°F:

- time within ±5°F changed by -4.61 percentage points, 95% CI [-5.30, -3.94];
- all ten forced models estimated a positive no-duty rate, meaning the chamber would heat with the auger off;
- median gain relative standard error was approximately 369, or 36,900%;
- every run selected zero dead time against MAKGrillSim's 100-second transport dead time;
- the median winner margin was zero.

A separate arm reduced confirmation to one evaluation while retaining the other gates. It reduced time within ±5°F by 28.73 percentage points on the MAK step scenario and 6.75 points on GrillSim at 225°F. Confirmation therefore remains load-bearing.

## Implementation Scope

Implementation is limited to:

- changing the two eligibility constants;
- updating comments and tests that encode the old one-hour/240-observation contract;
- adding focused tests for the 500-second/25-observation eligibility floor, continuous reevaluation, and 900-second earliest activation at the production cadence;
- running the paired simulator scenarios as the behavioral acceptance check.

No controller setting, migration, persistence-schema change, deliberate excitation, learning deadline, fallback model, or forced promotion path is added.

## Acceptance Criteria

1. No candidate can activate before 900 simulated seconds at the production 20-second cadence from a fresh identifier.
2. An identifiable GrillSim candidate can activate at 900 seconds after 20 stable gated evaluations.
3. A candidate that is not ready at 900 seconds is evaluated on every later accepted observation and can activate as soon as it completes confirmation.
4. Physics, uncertainty, delay-margin, excitation, temperature-span, gap, confirmation, distrust, and restore tests retain their existing behavior.
5. The 60-observation hold-duty floor remains unchanged.
6. Ten paired seeds across steady 225°F, steady 450°F, and 225→275°F show no material overshoot regression on either simulator.
7. MAKGrillSim is not forced active at 900 seconds when its evidence gates fail.
