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
- **R3.4** The snapshot fits `MAX_SNAPSHOT_BYTES` (8192). The identifier's raw
  bank — 25 candidates × a 3×3 covariance — does not fit naively; either a
  reduced restart state is persisted or the bank is re-seeded from the trusted
  parameters and covariance is reset. Which one is a decision, not a detail.

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
  **`requires_modules()` must return `("do_mpc",)` whenever identification is
  enabled, irrespective of net match** — failing closed at save time rather than
  open at cook time.
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
- **R5.2** The response is one of: refuse the promotion, or raise `n_horizon`
  within a bounded budget. Raising it costs IPOPT time per control period and
  changes `_CALIB_INTS`, which invalidates the net again — already accepted
  under R4.

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

## Decisions required

Each has a recommendation; none blocks drafting the plan.

| # | Question | Recommendation |
|---|---|---|
| D1 | Path B first, or wait and do both at once? | **B first.** It needs no new mathematics and is testable against the MAK data in the tree today. |
| D2 | Dependency policy under R4.2 | **Require `do_mpc` whenever learning is enabled.** Fails closed at settings-save. |
| D3 | Auto-raise `n_horizon` (R5.2), or refuse and warn? | **Refuse and warn** in v1. Auto-scaling couples model quality to solve-time budget and deserves its own measurement. |
| D4 | One operating-point-local model, or a per-band schedule? | **One model plus a recorded band** in v1; a schedule only if the 450 °F cell shows the single model failing. |
| D5 | Persist the full RLS bank or re-seed it (R3.4)? | **Re-seed** from trusted parameters with reset covariance; it fits the 8192-byte bound trivially and loses only reconvergence time. |

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
- **Automatic horizon scaling** (D3).
- **Per-band model schedules** (D4).
