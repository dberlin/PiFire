# MPC Online Identification Design

## Goal

Let the MPC controller learn its grill's dynamics from ordinary cooks and carry
what it learned across restarts, so a grill converges on a model that describes
it instead of running the shipped defaults forever — without ever letting a
learned model drive a grill it cannot brake.

## Why this exists

On 2026-08-02 a 450 °F Hold cook on a MAK overshot to 520 °F. The MPC's thermal
parameters were bit-identical to the shipped defaults: it believed a chamber
time constant of 640 s while the grill's is several thousand. It held the firing
rate at 100 % until 438 °F, gave itself roughly 25 s of lead, and coasted 263 s
past the setpoint. That is not a tuning error; it is a controller planning
against the wrong plant.

`2026-08-02-mpc-fan-authority-and-calibration-design.md` fixed the tooling: the
offline fitter now fits through the model the controller actually runs, reports
its own error, and the controller says so at startup when every thermal
parameter is still a default. What it did not do is close the loop. A user must
notice the warning, capture a log, run a utility, and paste nine numbers back
into settings. Almost nobody will.

## Relationship to work already in flight

This design is third in a chain and owns none of the identifier.

**`2026-08-01-controller-applied-output-plumbing.md`** already delivered what the
input side needs: `common/controller_model_state.py` (Task 10) — a
revision-gated, size-bounded, per-controller model store whose own docstring
describes its purpose as keeping the model of "a controller that identifies its
grill's dynamics online"; the Hold wiring (Task 11); and MPC's `_applied_Q` /
`_last_Q` split (Task 13), which is what makes an identifier able to regress
against *what the plant received* rather than what the controller asked for.
That plan's checkboxes are unticked in the file — the boxes are not maintained —
but the code is in the tree. **Work stopped mid fix-round on Task 17**, the
open-lid plant and matrix re-capture; that fix round is the immediate
prerequisite, not this document.

**`2026-08-01-adaptive-smith-predictor-design.md`** builds
`controller/fopdt_identifier.py`: a batched RLS bank over 25 dead-time
candidates (0–120 s in 5 s steps) that recovers `K`, `tau` and `theta` with
uncertainty and trust gates. Neither it nor `controller/smith_predictor.py`
exists yet. **This design consumes that identifier unchanged and adds nothing to
it.**

**`smith_predictor.py` is not needed here.** A Smith predictor gives deadtime
compensation to a controller with no internal model. The MPC already carries the
delay explicitly as the Erlang chain (`theta` / `n_delay`) and the optimizer
plans through it.

So the ordering is: plumbing Task 17 closes → smith Tasks 2–5 land →
this.

## Scope

In scope: consuming the FOPDT identifier from MPC; mapping FOPDT parameters onto
the grey-box the MPC runs; persisting and restoring a learned model; the policy,
dependency and horizon consequences of a calibration that changes at runtime;
the safety envelope around promotion.

Two edits land outside MPC and are called out because they touch files another
plan owns: `MAX_SNAPSHOT_BYTES` in `common/controller_model_state.py` (R3.4),
and `requires_modules()` in `controller/mpc.py` becoming unconditional (R4.2),
which changes what a base install may select.

Out of scope: any change to the identifier's mathematics or its trust gates;
regenerating the neural policy artifact for a learned calibration; deliberate
excitation of any kind.

## Requirements

### R1 — Identifier reuse

- **R1.1** MPC feeds the identifier its **applied firing rate** (`_applied_Q`),
  not auger duty. The identifier's input contract must therefore be a generic
  piecewise-constant input series, not a duty-shaped one. This is the one change
  requested of the identifier's *interface* — and it is free if made before
  `FOPDTIdentifier`'s API is frozen, expensive after.
- **R1.2** Units are converted explicitly at the boundary and tested. `tau` and
  `theta` are unit-free; **`K` is not**. The identifier is canonically
  Fahrenheit; MPC's grey-box is Celsius and its input is `Q`, not duty. A gain
  that silently crosses either boundary is a 1.8× error in the one parameter
  that sets steady-state demand.
- **R1.3** A dead-time estimate that lands on the **last candidate** of the grid
  is treated as unidentified, not as `theta = 120`. The MAK's measured deadtime
  is 93–110 s against a 120 s ceiling, so real grills sit near the top of that
  grid and a slower one saturates it. An edge-saturated argmin must block
  promotion and be reported.

### R2 — Mapping FOPDT onto the grey-box

- **R2.1** The identifier yields three numbers; the MPC runs on eight. The
  reduction holds `C_f`, `h_fc` (the firepot is fast and its dynamics are not
  identifiable from chamber temperature), holds `sigma`, maps `theta` directly,
  keeps `n_delay`, and recovers `C_c` and `K_Q` from `tau` and `K` at a fixed
  `h_amb`.
- **R2.2** The mapping is **operating-point local and must be recorded as such.**
  Because `sigma` is nonlinear, radiative loss triples total chamber loss at
  271 °C on the measured MAK data — so a `tau` identified during a 225 °F hold
  is not the `tau` at 450 °F. Every stored model carries the temperature band it
  was identified in.
- **R2.3** Applying a model identified in one band to a cook in another is
  permitted but flagged in `get_status()`. Refusing outright would leave most
  cooks unimproved; pretending the model is global is what produced the incident.

### R3 — Persistence

- **R3.1** MPC implements `get_model_snapshot()` / `restore_model()`, which today
  inherit `ControllerBase`'s `None`.
- **R3.2** The snapshot carries the identifier's own state as well as the trusted
  parameters, so a restart does not re-learn from zero, plus provenance: sample
  count, excitation measure, fit error, temperature band, and the schema version.
- **R3.3** `revision` is **monotonic across process restarts**, not merely within
  one process. `controller_model_state.py` documents that a per-process counter
  is silently rejected forever once it falls behind the persisted value. The
  revision is derived from persisted state, never from a fresh in-process counter.
- **R3.4** The **full identifier bank is persisted**, so a restart resumes with
  its accumulated confidence rather than reconverging. The store's
  `MAX_SNAPSHOT_BYTES` is raised from 8192 to 65536 to stop the bound being a
  design constraint (see Measurements: the bank is 7104 bytes as plain JSON —
  it fits today, but with 13 % headroom, so a wider dead-time grid or a fourth
  regressor would silently push it over). Plain JSON is preferred over a packed
  encoding at that cap: a learned model that drives a fire should stay
  readable in the datastore.

### R4 — Policy and dependency safety

This is the requirement that gates everything else, and the only one that can
fail a cook rather than merely fail to help it.

- **R4.1** Every promoted parameter set changes `C_f`/`C_c`/`h_fc`/`h_amb`, which
  are members of `mpc_net._CALIB_FLOATS` compared at `rtol=1e-3`. So **every
  successful promotion invalidates the shipped net artifact by design**, and the
  controller falls back to the IPOPT NLP.
- **R4.2** That fallback imports `do_mpc` (`mpc.py`, in `_build_nlp`).
  `requires_modules()` — the settings-save gate — answers "`do_mpc` not needed"
  when `policy=net` and the artifact matches *at save time*. A base install
  without the `mpc` extra therefore passes the gate legitimately, learns, stops
  matching, and raises `ImportError` mid-cook on a machine that never had IPOPT.
  **`requires_modules()` returns `("do_mpc",)` unconditionally for MPC** —
  irrespective of policy or net match. This is broader than this design strictly
  needs and is a deliberate simplification: the conditional gate is a
  correctness hazard whose only benefit is letting a base install run one
  policy, and it fails open at exactly the wrong moment. **Product consequence
  to accept explicitly: selecting the MPC controller at all now requires the
  `mpc` extra, including for `policy=net` users who do not enable learning.**
  Revisiting this belongs with the parameter-conditioned net (Deferred), not
  here.
- **R4.3** With identification enabled the effective policy is NLP. This is
  stated in the controller metadata rather than discovered: enabling learning
  trades the numpy fast path for IPOPT solve time every control period.
- **R4.4** The smith design establishes that agreement with the NLP is the
  binding acceptance criterion for the net, and identifies *distributional*
  staleness as invisible to `matches_config`. This design's staleness is
  **calibration** staleness, which `matches_config` does catch — so the net is
  cleanly off rather than silently wrong. That is the desired failure mode and
  must not be "fixed" by loosening `rtol`.

### R5 — Horizon adequacy

- **R5.1** A promotion whose `tau` exceeds the prediction horizon
  (`n_horizon × t_step`) must not be silently accepted. A correct model with too
  short a horizon still overshoots — it simply cannot see far enough ahead to
  brake, which is half of what happened in the incident.
- **R5.2** The horizon is **raised to cover the learned effective time
  constant**, bounded by a solve-time budget rather than refused. Measurement
  (below) settles the affordability question: a 10× horizon costs 16× the solve
  time but still only 8.5 % of the control period. The horizon that matters is
  set by the *effective* τ at cooking temperature, not the linear `C_c/h_amb` —
  radiative loss makes those differ by ~3× — so the target is `n_horizon` ≈ 144
  (3600 s span, 3.0 % of budget), not 240.
- **R5.3** The budget is expressed as a fraction of `control_period` and checked
  at promotion, not hard-coded to a step count. The nominal target is a Pi 5;
  the numbers below come from the machine that actually runs this grill, and a
  slower host must scale the horizon down rather than miss its control period.
- **R5.4** Raising `n_horizon` changes `_CALIB_INTS` and invalidates the net —
  already accepted under R4. `t_step` is the cheaper lever for span (it buys
  horizon without adding decision variables, at coarser resolution) and is
  available if the solve-time budget binds.

### R6 — Estimator contention

- **R6.1** The EKF disturbance state `d` exists to absorb exactly the model error
  the identifier needs to observe. Intervals where `d` is large or moving fast
  are rejected or corrected for, so the two adaptations do not fight.
- **R6.2** `est_q_dist` is already deliberately slow (0.05, with a comment
  recording that a faster disturbance estimate worsens step overshoot). This
  design depends on that and says so, rather than rediscovering it.

### R7 — Safety envelope

- **R7.1** Bounds are **asymmetric by design.** Believing the grill is more
  sluggish than it is makes the controller brake early and costs nothing;
  believing it is less sluggish is precisely the 520 °F failure. Learned `tau`
  may not fall below a floor without markedly stronger evidence than is required
  to raise it.
- **R7.2** A model is promoted only if it scores better than the incumbent **on
  the same data**, using the `fit_quality()` RMSE comparison that
  `controller/update_mpc.py` already provides. Promotion is monotone in measured
  quality; there is no "newest wins".
- **R7.3** Every learned parameter is clamped to a physically plausible range,
  and a rejected model leaves the incumbent untouched.
- **R7.4** Identification is off by default. A grill that has never been
  identified behaves exactly as it does today.

### R8 — Two paths, and which comes first

- **R8.1 (Path B, batch)** At the end of a cook, refit that cook's logged
  history with `update_mpc.fit_params`, score with `fit_quality` against the
  incumbent, and store the winner. Every cook opens with a startup ramp, which is
  the richest excitation available and is free.
- **R8.2 (Path A, online)** The `FOPDTIdentifier` runs live and promotes when its
  trust gates pass.
- **R8.3** Both write through the same store, the same mapping (R2) and the same
  envelope (R7). **Path B ships first**: it reuses machinery that exists and is
  already verified against real data today, it cannot burst mid-cook, and every
  update is auditable offline. Path A follows behind a setting once the
  identifier lands.

## Decisions

Resolved 2026-08-02. Two were settled by measurement rather than judgement.

| # | Question | Resolution |
|---|---|---|
| D1 | Batch or online first? | **Batch first.** End-of-cook refit ships in v1; the live RLS identifier follows behind a setting once `fopdt_identifier.py` lands. |
| D2 | Dependency policy under R4.2 | **`do_mpc` is required for MPC unconditionally**, net policy or not. Simplest correct thing; the conditional gate is revisited with the parameter-conditioned net, not here. |
| D3 | Auto-raise `n_horizon`, or refuse and warn? | **Auto-raise, bounded by a measured budget.** 10× horizon costs 16× solve time but only 8.5 % of the control period; the ~3600 s span that matters costs 3.0 %. Affordable. |
| D4 | One model, or a per-band schedule? | **One model plus a recorded band** in v1; escalate only if the 450 °F cell shows it failing. |
| D5 | Persist the RLS bank, or re-seed it? | **Persist it, and raise the store's cap** to 65536. The 8192 bound was the thing to fix, not to design around. |

## Measurements

### Horizon cost (settles D3)

NLP solve time per control step, MPC built on the grey-box parameters fitted
from the 2026-08-02 MAK cook, `t_step = 25 s`, `control_period = 5 s`. Mean of
8–15 warm solves; build time is under 0.8 s at every size and is paid once.

| `n_horizon` | span | mean solve | vs. 24 | % of control period |
|---|---|---|---|---|
| 24 | 600 s | 26.1 ms | 1.00× | 0.5 % |
| 48 | 1200 s | 43.3 ms | 1.66× | 0.9 % |
| 96 | 2400 s | 87.6 ms | 3.36× | 1.8 % |
| 144 | 3600 s | 148.1 ms | 5.68× | 3.0 % |
| 192 | 4800 s | 259.9 ms | 9.96× | 5.2 % |
| 240 | 6000 s | 426.0 ms | 16.32× | 8.5 % |

Scaling is superlinear (~n^1.2), so a 10× horizon is a 16× solve — but the
absolute cost is the binding question and it is small. Worst observed single
solve at `n_horizon = 240` was 504 ms against a 5000 ms period.

**These are real numbers for the deployment** — this grill runs on the machine
that produced them — but the nominal target is a Pi 5, where the same horizon
would be several times slower and could approach the period. Hence R5.3: the
budget is a fraction of `control_period`, evaluated on the host, not a constant.

### Snapshot size (settles D5)

A 25-candidate bank (`Theta` 25×3, `P` 25×3×3, `resid_ew` 25) plus trusted
parameters and provenance:

- plain JSON float lists: **7104 bytes** — under the 8192 cap, with 13 % headroom
- base64-packed `float32`: 1901 bytes

The bank fits today by luck, not by design; one more regressor or a wider
dead-time grid crosses the line. Raising the cap to 65536 removes the
constraint and keeps the snapshot readable in the datastore, which matters for
auditing a model that drives a fire.

## Verification

The acceptance test is closed-loop and uses a plant that reproduces the actual
failure. `controller/grill_sim.py::MAKGrillSim` was identified from the incident
cook and replays it to 2.33 °C RMSE, landing its overshoot peak at 519 °F
against the measured 520 °F.

- **Convergence.** Starting from shipped defaults on `MAKGrillSim`, successive
  simulated cooks reduce 450 °F overshoot monotonically and reach a stated band.
  A run in which overshoot *increases* after a promotion is a failure of R7.2,
  not noise.
- **Negative control.** With identification disabled, behaviour is bit-identical
  to today and the characterization goldens are unchanged.
- **Mapping round trip.** Grey-box → FOPDT → grey-box recovers the chamber time
  constant within tolerance; a unit-conversion error in `K` fails it.
- **Edge saturation.** A synthetic plant with `theta` past the grid ceiling is
  refused, not promoted at 120 s (R1.3).
- **Restart.** A snapshot survives a simulated restart, its revision advances,
  and a deliberately stale revision is rejected without corrupting the store.
- **Dependency gate.** With learning enabled, `requires_modules()` includes
  `do_mpc` and `get_status()` reports `policy = "nlp"` (R4.2, R4.3).
- **Estimator contention.** Identification during a lid-open pause, when `d`
  moves fastest, does not promote (R6.1).

## Constraints

Inherited from the adaptive-smith-predictor design and binding here:

- **No deliberate excitation enters production auger commands.** Both paths use
  only naturally occurring excitation: the startup ramp, setpoint changes, lid
  events.
- numpy is permitted; per-update work and memory are bounded and fixed-size.
- No raw controller output enters any model once the applied value is available.
- The shipped `_DEFAULTS` are not changed by this work.

## Deferred

- **Regenerating the net policy for a learned calibration.**
  `tools/regenerate_mpc_net.py` exists but retraining is an offline project; the
  runtime consequence here is simply NLP-only (R4.3).
- **A parameter-conditioned net** that takes `K`/`tau`/`theta` as inputs and so
  survives adaptation. This is the principled fix for R4 and is a research task.
- **Per-band model schedules** (D4).
