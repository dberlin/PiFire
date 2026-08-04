# MPC Control Quality — Feasibility, Feed-Forward and Cycle Time — Design

**Date:** 2026-08-04
**Status:** Ready for planning
**Predecessor:** `docs/superpowers/specs/2026-08-02-mpc-fan-authority-and-calibration-design.md` (fan authority + calibration correctness)
**Exploration:** `.superpowers/sdd/2026-08-02-mpc-online-identification/explore-control-rethink.md`, numbers committed at `docs/superpowers/experiments/_control_rethink.txt`

Online identification landed and did what it was built to do. This spec covers what is
left, and the finding that reordered the work: **most of the remaining defect is not in
the identification.** It is in the operating point the controller is given, in the
steady firing rate it never learns in time, and in one measurement harness that has been
describing the actuator rather than the controller.

---

## 1. Incident and evidence

### 1.1 What happened, and what the last slice fixed

| Event | Evidence |
|---|---|
| A real 450 °F Hold cook on "Ponce Grill" peaked at **520 °F** (+70 °F) | `controller/mpc_calibration_log.csv` (248 rows, 1240 s); timeline in the predecessor spec §1.1 |
| Online identification, three successive simulated cooks at 450 °F on `MAKGrillSim` | peak **522.5 → 451.5 → 451.3 °F**, overshoot **+72.5 → +1.3 °F** — `.superpowers/sdd/2026-08-02-mpc-online-identification/progress.md` (task A9b), test `tests/e2e/test_mpc_learns_a_grill.py` |

Cook 1 lands within 2.5 °F of the 520 °F the real grill actually hit, so the plant is
reproducing the incident and the fall is attributable: disabling `_adopt_model`'s write to
`cfg` gives `peaks [522.5, 522.5, 522.5]` (mutation evidence, A9b).

### 1.2 What the follow-up exploration then measured

Committed at `docs/superpowers/experiments/_control_rethink.txt`. Everything below traces
to a line of that file or to a named source line. Six runs, R1–R8, on `GrillSim` and
`MAKGrillSim` (`docs/superpowers/experiments/controller_matrix.py` plants; `MAKGrillSim` is
the model fitted from the incident cook).

**E-1 — The measurement harness does not run the shipped actuator configuration.**

| | `HoldCycleTime` | `u_min` | `u_max` |
|---|---|---|---|
| Shipped product | 25 | **0.10** | 0.90 — `common/defaults.py:123,126-127`; mirrored `common/settings_schema.py:85` |
| A9a / matrix harness | 20 | **0.15** | 0.90 — `docs/superpowers/experiments/controller_matrix.py:53` |

**E-2 — At `u_min=0.15` the MAK 325 °F scenario is physically infeasible.** Steady chamber
temperature at a fixed duty, closed form cross-checked against the shipped plant object
(`_control_rethink.txt` R1, lines 43–48):

| `MAKGrillSim` duty | fan 0.0 | fan 1.0 |
|---|---|---|
| 0.10 | 342.0 °F | **283.8 °F** |
| 0.15 | 435.4 °F | **368.9 °F** |

A 325 °F setpoint sits *below* the 368.9 °F floor the harness's own `u_min` imposes.

**E-3 — The shipped MPC, re-measured at both operating points.** Same scenario, seeds and
metric code as A9a, `HoldCycleTime` held at 25 s in both rows so `u_min` is the only
variable (`_control_rethink.txt` R7, lines 167–168):

| `u_min` | floor (fan 1.0) | %<5 °F | overshoot | peak | settle | **final** | mean duty | IAE |
|---|---|---|---|---|---|---|---|---|
| 0.15 | 368.9 °F | 0.21 | 82.4 °F | 407.4 °F | never | **369.1 °F** | 0.2028 | 645 147 |
| 0.10 | 283.8 °F | 63.87 | 79.2 °F | 404.2 °F | 3959 s | **324.9 °F** | 0.1710 | 225 618 |

The 0.15 row reproduces A9a's published U3 numbers (0.21 %, 82.7 °F, 407.7 °F, never)
almost exactly. `final` 369.1 °F against a floor of 368.9 °F is what "tracking an
unreachable setpoint forever" looks like — not a controller failure.

**E-4 — Overshoot is a steady-gain problem, not a horizon or architecture problem.** Same
plant, same seeds, same metric code (`_control_rethink.txt` R5, lines 130–145):

| arm | `u_min` | %<5 °F | overshoot | peak | settle | IAE |
|---|---|---|---|---|---|---|
| shipped MPC (best, F3 cook 3) | 0.15 | 5.25 | **42.4 °F** | 367.4 °F | never | 403 478 |
| shipped MPC (R7) | 0.10 | 63.87 | **79.2 °F** | 404.2 °F | 3959 s | 225 618 |
| crude `ff` — *knows* `u*` (oracle) | 0.10 | 88.56 | **1.0 °F** | 326.0 °F | 1249 s | 116 971 |
| crude `pi` — *learns* `u*` with an integrator | 0.10 | 65.93 | 34.2 °F | 359.2 °F | 3828 s | 173 328 |

The `ff` arm's best row used **lead = 0 s**. Zero lookahead, plus knowledge of the steady
firing rate, beats a 600 s-horizon MPC by 78 °F of overshoot. Knowing `u*` is worth
78 °F; learning it with an integrator recovers only 45 of those 78.

**E-5 — Why an integrator cannot get there in time.** `MAKGrillSim` τ = 5707 s = **95.1 min**
at fan 1.0, 9274 s at fan 0 (`_control_rethink.txt` R6, lines 151–155), against a 600 s
horizon (ratio 0.105). And recovery is asymmetric with prevention: placed at 325 + 42.4 °F
and firing at `u_min`, the time to fall back inside ±5 °F is **1614 s (26.9 min)** on MAK at
`u_min=0.10`/fan 1.0 and `never` (>4 h) in the other three MAK rows, against 60–107 s on
`GrillSim` (`_control_rethink.txt` R4, lines 109–117). A 27× asymmetry: on this grill,
prevention is worth everything and recovery is worth nothing.

**E-6 — The fan is operating range, not braking authority.** Post-fuel-cut coast rise after
2 h at `u_max` (`_control_rethink.txt` R3, lines 85–100): MAK **0.0 / 1.2 / 0.7 °F** at fan
0.0 / 0.5 / 1.0, `GrillSim` ≤ 0.1 °F. Non-monotone in fan — more air also burns the in-pot
fuel faster. As range it is worth **58 °F**: full fan moves the MAK floor from 342.0 °F to
283.8 °F at `u_min=0.10` (E-2). It follows that the incident's "fan pinned at 100 %" was,
for floor purposes, the *best available setting*; the fan being unavailable as a lever costs
range at the top (992 °F vs 814 °F max), not stopping power at the bottom.

**E-7 — The braking-distance horizon floor is sized for a coast that does not exist.**
`controller/model_promotion.py:494 longest_braking_distance` returns
**150.01211157822874 s** on the shipped nominal model (`_control_rethink.txt` R3), and the
built horizon is floored to cover it. The coast it is sizing for measures ≤ 1.2 °F (E-6).
`braking_distance` (`controller/model_promotion.py:422`) multiplies `_model_coast` by
`_COAST_BOUND = 1.45` (`:178`); that factor was derived correctly for what it bounds — this
finding is about the *magnitude of the thing being bounded*, not the factor.

**E-8 — Duty resolution is not the binding constraint.** A duty-cycled auger cannot express
an arbitrarily small duty; the harness asserts `cycle_time*ratio >= 1`
(`controller_matrix.py:169`), so its smallest expressible duty is `1/HoldCycleTime`. The
binding minimum is `max(u_min, 1/HoldCycleTime)`. At the shipped configuration that is
`max(0.10, 0.040) = 0.10` — **`u_min` binds by 2.5×**. Duty needed across the range the user
reports holding, `MAKGrillSim` (`_control_rethink.txt` R8, lines 200–215):

| setpoint | fan 0 | fan 1.0 | smallest cycle expressing it |
|---|---|---|---|
| 180 °F | 0.0343 | 0.0479 | 25 s (fan 1.0) / **30 s (fan 0)** |
| 225 °F | 0.0503 | 0.0695 | 20 s |
| 325 °F | 0.0920 | 0.1233 | 20 s |
| 520 °F | 0.2053 | 0.2603 | 20 s |
| 600 °F | 0.2682 | 0.3329 | 20 s (against `u_max` 0.90) |

**Plant validation:** `MAKGrillSim` reproduces the 180–600 °F range the user reports this
physical grill holding. The plant is not miscalibrated, so no recommendation here is bent
around a broken plant.

> The 1 s duty granularity above is a **harness artefact, not a physical limit**.
> `_auger_toggle_tick`'s own docstring records that production evaluates the toggle at
> ~20 Hz onto an auger that integrates fuel continuously. The real floor is whatever the
> auger mechanism and pellet delivery impose, and **nobody has measured it.** The table
> bounds the restriction; it does not state it.

---

## 2. Root causes

### RC-1 — The matrix harness runs a `u_min` the product does not ship

`controller_matrix.py:53` pins `u_min=0.15, HoldCycleTime=20`; the product ships `0.10, 25`
(`common/defaults.py:123,126`). On `MAKGrillSim` that difference moves the reachable floor
from 283.8 °F to 368.9 °F (E-2) — 85 °F. Every sub-435 °F MAK scenario measured with this
harness has been measuring *the actuator floor*, not the controller. This is a correctness
prerequisite for citing any prior MAK measurement, not a headline result in itself, because
E-3 has now measured what it changes.

### RC-2 — `u_min` is a fixed global constant where it should be a per-grill derivation

`u_min` is read once at `controller/mpc.py:330` (`cycle_data.get("u_min", 0.1)`) from a
single global setting. It is the *dominant* feasibility constraint (E-8: binds by 2.5× over
resolution), and the temperature it corresponds to is grill-specific: 0.10 gives a 283.8 °F
floor on MAK and a 166.3 °F floor on `GrillSim` (`_control_rethink.txt` R1, lines 33, 45).
A number that maps to a 118 °F spread across two grills cannot be one shipped constant. The
bound is closed-form and available at runtime from the identified model.

### RC-3 — The controller has no feed-forward term for the steady firing rate

The MPC discovers the holding duty only through feedback over a τ of 95 min (E-5) while the
chamber is already climbing. E-4 measures the cost directly: 79.2 °F of overshoot against
1.0 °F for a controller that simply *knows* `u*`. **This is the largest remaining control
defect.** It is an identification-priority change, not an architecture change — an accurate
`K_Q`/`h_amb` *is* `u*`. Corroborating negatives from the exploration's closed hypotheses:
longer horizons/terminal costs are not binding (the winning `ff` row used lead = 0 s),
offset-free machinery is already present (`mpc_model.py:9,25,367,458`, estimated at
`mpc.py:364`), and fan-based braking is worth ~1 °F (E-6).

### RC-4 — An unreachable setpoint is tracked forever instead of refused

At `u_min=0.15` the MPC spent a 3 h run parked 0.2 °F above its own floor (E-3), reporting
nothing. The floor is a closed form the controller can evaluate at the moment a Hold is
requested. Today a user asking for a temperature their actuator configuration cannot produce
gets silence and a permanently wrong grill.

### RC-5 — `HoldCycleTime` is set for a different auger regime than this grill wants

At the shipped 25 s a 180 °F hold is expressible only with the fan running (needs 0.0343 at
fan 0, resolution floor 0.0400); `HoldCycleTime >= 30` makes it fan-independent (E-8). The
user operates this grill down to 180 °F and has ruled the default to **60 s**. See §5 for
the provenance of that specific number and §6 for the accepted cost.

### RC-6 — The braking-distance floor demands 150 s of horizon for a ≤1.2 °F coast

E-7. This is a safety constant the project spent three fix rounds deriving, sized against a
phenomenon two orders of magnitude smaller than assumed.

---

## 3. Requirements

Ordered by the exploration's ranking. R1 is a prerequisite for trusting the rest.

### R1 — The matrix harness runs the shipped actuator configuration (RC-1)

- **R1.1** `docs/superpowers/experiments/controller_matrix.py` derives its cycle configuration
  from `common/defaults.py` (`default_settings()["cycle_data"]`) rather than the literal at
  `:53`, in the same way `LID_PAUSE_S` already does at `:56`.
- **R1.2** A scenario may still *override* individual keys, but an override is explicit and
  appears in the committed output header alongside the values actually used, so a reader can
  tell a deliberate sweep point from a drifted default.
- **R1.3** The harness records, per run, the reachable floor implied by the configuration it
  ran (`u*` inverted at `max(u_min, 1/HoldCycleTime)`, both fan extremes) beside the
  setpoint. A run whose setpoint is below its own floor is labelled **INFEASIBLE** in the
  output. E-3 shows the metrics are otherwise silently meaningless.
- **R1.4** `%<5 °F` and `settle_s` carry the A9a metric-trap note where they are degenerate:
  on MAK, τ ≈ 2 h means nothing settles within ±5 °F in a 3 h run in *any* arm, so those two
  columns are not quality scores there. Read overshoot / peak / IAE.
- **R1.5** No MAK number produced before R1.1 lands may be cited without re-measurement. See
  §9.

### R2 — `u_min` is derived per grill, not fixed (RC-2)

- **R2.1** The minimum firing rate is derived from the identified model and the lowest
  supported setpoint, via the closed form the exploration used:
  `u* = [h_amb(fan_max)·(T_sp − T_amb) + rad(T_sp)] / H`.
- **R2.2** The derivation rule is `u_min <= 0.7 × u*(min setpoint, fan max)`. For
  `MAKGrillSim` at 180 °F, `u*` = 0.0479 (E-8), giving `u_min <= 0.0335`, against the
  shipped 0.10.
- **R2.3** The derived value is surfaced to the user with the temperature it corresponds to
  ("your minimum firing rate holds this grill no lower than N °F"), because the number is
  meaningless and the temperature is not.
- **R2.4** The user's configured `u_min` remains authoritative; the derivation informs and
  warns, it does not silently overwrite a setting the user set. The 0.7 margin factor is
  carried as a named constant with this spec cited as its provenance.
- **R2.5** Measured effect, for the acceptance bar: on MAK `steady_325`, `u_min` 0.15 → 0.10
  ends the run at **324.9 °F instead of 369.1 °F** (E-3).

### R3 — Feed forward the steady firing rate (RC-3) — *the largest remaining defect*

- **R3.1** The controller computes `u*` for the active setpoint from its current identified
  model and applies it as a feed-forward baseline, with the optimiser solving for the
  correction rather than for the absolute firing rate.
- **R3.2** `u*` is recomputed on every setpoint change and on every model adoption, and is
  logged with the value and the model revision that produced it.
- **R3.3** The feed-forward term degrades safely when the model is uncalibrated: with the
  shipped `_DEFAULTS` it is a defaults-derived guess, and it must not be *worse* than
  today's behaviour. The verification below pins that as an inequality, not an assertion.
- **R3.4** Acceptance bar, from E-4 on the same plant/scenario/seeds: overshoot must move
  materially toward the oracle's 1.0 °F from the shipped 79.2 °F. The oracle is an upper
  bound on a perfect steady-state model, not an implementable arm — 1.0 °F is the target to
  be measured against, not a number to be promised.

### R4 — Refuse setpoints below the reachable floor (RC-4)

- **R4.1** When a Hold setpoint is requested, the controller compares it against the floor
  implied by the current `max(u_min, resolution)` and the model, at the most favourable fan
  setting available to it.
- **R4.2** A setpoint below that floor is reported as unreachable — surfaced to the user with
  the floor temperature and the setting that produced it — rather than tracked.
- **R4.3** This is a report, not a refusal to run: the grill still heats. What must not
  happen is the E-3 outcome, where a 3 h run sat 0.2 °F above its own floor in silence.
- **R4.4** The comparison is one closed-form evaluation. It removes the entire failure class,
  which is why it ranks above two changes that are individually larger.

### R5 — `HoldCycleTime` default becomes 60 s (RC-5)

- **R5.1** `common/defaults.py:123` changes `HoldCycleTime` from 25 to **60**.
  `common/settings_schema.py:85` changes to match; the two must not diverge.
- **R5.2** One global value, all controllers. No per-controller default, no
  controller-supplied override, no migration branching. Existing installs whose stored
  `cycle_data` already carries 25 keep 25 — the migration path overlays *read* values on top
  of the defaults precisely so new fields are captured without clobbering set ones
  (`common/settings_migration.py:101-103`), and there is no `upgrade_settings` step for
  `HoldCycleTime`. This change therefore reaches new installs and factory resets only. That
  is the intended behaviour and it must be stated in the change's release note, because
  otherwise the user's own grill — whose stored settings already carry 25 — will not pick it
  up. If the user wants the new value on an existing install, that is a deliberate
  `upgrade_settings` entry and it is **not** specced here.
- **R5.3** The mechanism, stated honestly so nobody re-derives it wrongly: a longer cycle
  gives a **finer duty quantum** (`1/60 = 0.0167` against `1/25 = 0.0400`) but a **coarser
  fuel lump** — each auger pulse delivers more fuel at once. Once the cycle exceeds the
  plant dead time (`GrillSim` 20 s, `MAKGrillSim` 100 s) the lump dominates. Duty
  *resolution* is not the binding constraint (E-8: `u_min` binds by 2.5×), so the finer
  quantum is not the justification.
- **R5.4** What the exploration actually supports is `HoldCycleTime >= 30 s`, and only for
  **fan-independent margin at 180 °F** (E-8: needs 0.0343 at fan 0, `1/30 = 0.0333`). It
  does **not** show that 60 s is better than 30 s for any controller. See §5.
- **R5.5** The change does not ship before R6 below has been run and read.

### R6 — Measure MPC at the new cycle time before the default ships (RC-5)

Nobody has measured MPC at 60 s. The only 60 s evidence in this project is `pid_sp`
(§6). MPC has a model and can anticipate a fuel lump, so it may handle a 60 s cycle far
better *or worse*; both directions are open.

- **R6.1 Arms.** `HoldCycleTime` ∈ {25 (shipped baseline), 30 (the derived minimum, R5.4),
  60 (the ruled default)}. `u_min` at the shipped 0.10 in every arm — not 0.15 (RC-1).
- **R6.2 Plants.** `MAKGrillSim` (the user's grill, dead time 100 s) and `GrillSim` (dead
  time 20 s). Both, because R5.3's mechanism predicts the two plants respond differently and
  a single-plant result cannot distinguish "60 s is fine" from "MAK's long dead time hid it".
- **R6.3 Setpoints.** 180 °F (the fan-independence case R5.4 is *for*), 225 °F (where the
  `pid_sp` regression was measured, so the two are comparable), 325 °F and 450 °F (the
  incident setpoint, and where `MAKGrillSim` sits inside its identified range).
- **R6.4 Metric.** Peak-to-peak ripple and standard deviation over the holding window,
  steady-state error, and overshoot/peak on the approach — reported per arm, medians over at
  least 3 seeds, using the A9a metric code. **Peak-to-peak is the primary metric**, because
  it is the quantity the `pid_sp` regression moved (7.5 → 40–47 °F) and the quantity a
  coarser fuel lump is predicted to move. `%<5 °F` and `settle_s` are reported but carry the
  R1.4 degeneracy note on MAK.
- **R6.5 Configuration.** Runs use the shipped `cycle_data` per R1.1 with only
  `HoldCycleTime` swept, and the output states the values used.
- **R6.6 Decision rule, fixed before the run.** If MPC ripple at 60 s is within noise of the
  25 s arm on both plants, ship 60 s as ruled. If MPC degrades materially at 60 s but is
  clean at 30 s, **the fallback is 30 s** — the value the exploration does support, which
  still buys the fan-independent 180 °F hold (R5.4) — and the result is taken back to the
  user rather than substituted silently. If MPC degrades at 30 s as well, the whole
  cycle-time change is withdrawn and RC-5 is closed as "not the lever".

### R7 — Re-derive or drop the braking-distance horizon floor (RC-6)

- **R7.1** `controller/model_promotion.py:494 longest_braking_distance` and the horizon floor
  built from it are re-derived against a measured coast, not an assumed one.
- **R7.2** Sequenced **after** R2–R4, which change what the horizon is being asked to do. Any
  re-derivation done before them measures a controller that is still tracking infeasible
  setpoints.
- **R7.3** `_COAST_BOUND = 1.45` (`:178`) is not the target of this requirement and is not
  loosened by it. The question is the magnitude of the coast being bounded (E-7), not the
  safety factor over it. The existing tests that pin the bound as a *product* rather than a
  literal stay as they are.

---

## 4. Design decisions

**D1 — R1 is a prerequisite, not the headline.** The instinct on finding a harness that runs
the wrong constant is to re-run everything first. Rejected as the ordering: E-3 already
measured what the wrong constant changes on the scenario that matters, so the ranked work
can proceed while the harness is fixed. What R1 gates is *citation* (R1.5, §9), not progress.

**D2 — Scope the fan as range, not as overshoot.** E-6 inverts an assumption this project
has carried since the incident. The fan is worth 58 °F of operating range and ~1 °F of
braking, and the incident's "fan pinned at 100 %" was for floor purposes the best available
setting. The predecessor spec's fan-authority requirements are still right; what changes is
the *reason* they are right. No fan-based braking work is scoped here.

**D3 — Feed-forward is an identification change, not an architecture change.** `u*` falls
out of an accurate `K_Q`/`h_amb`. R3 therefore builds on the identification slice that just
landed rather than replacing the MPC formulation. This is also why R3 ranks above the
horizon work: the winning R5 row used lead = 0 s, so lookahead is demonstrably not the
missing ingredient.

**D4 — `u_min` derivation informs, it does not overwrite (R2.4).** The alternative — computing
`u_min` and writing it into `cycle_data` — was rejected on the same ground the predecessor
spec rejected auto-enabling `pwm_control` (its D1): a setting the user set is a statement
about their hardware. Making the consequence visible is the fix; silently substituting a
derived value is a different failure mode with the same shape.

**D5 — Refusal (R4) is a report, not a mode.** A controller that declines to run is worse
than one that runs and says so. The predecessor spec's D6 made the same call for calibration
state, and the same reasoning applies: hard errors are reserved for configurations that are
unambiguously broken, and "you asked for 150 °F on a grill that floors at 284 °F" is a user
mistake to be surfaced, not a system fault to be blocked.

**D6 — One global `HoldCycleTime`, per the user's ruling.** A per-controller default or a
controller-supplied override was considered — `controller/controllers.json` already carries
an unused `recommendations.cycle.cycle_time` per controller (`pid_sp`: 15, `mpc`: 25), and
no repository code reads it, so the plumbing would be new work. The user has ruled that PID
is fixed later; the setting stays global and single-valued and the PID cost is accepted
(§6). Recording the mechanism here so a future reader does not mistake it for an oversight:
the hook exists, it was seen, and it was deliberately not used.

---

## 5. Provenance of the 60 s value — read this before changing it

`HoldCycleTime = 60` is a **user ruling based on operational experience with this physical
grill.** It is not derived from anything measured in this project, and this spec does not
claim otherwise.

What this project has measured about cycle time:

- Duty **resolution** is not the binding constraint: `max(0.10, 1/25) = 0.10`, so the `u_min`
  setting binds by 2.5× (E-8). A finer duty quantum buys nothing while `u_min` is 0.10.
- The one concrete thing a longer cycle buys is `HoldCycleTime >= 30 s` making a **180 °F
  hold fan-independent** (needs 0.0343 at fan 0; `1/30 = 0.0333`) — E-8. That is a margin
  change, not an enabling one: 180 °F is already expressible at 25 s with the fan running.
- Nothing in this project shows 60 s is better than 30 s, for MPC or for anything else.
- The only measurement of *any* controller at 60 s is `pid_sp`, and it is bad (§6).

R6 exists because of exactly this gap, and R6.6 fixes the fallback in advance so a bad
result does not turn into an ad-hoc renegotiation.

---

## 6. Risks

### RISK-1 — PID hold quality regresses at 60 s. Known, measured, accepted, deferred.

**Measured.** `pid_sp` holding 225 °F, 3 seeds, `HoldCycleTime` swept
(`.superpowers/sdd/2026-08-01-live-setpoint-set-target/progress.md:28`, task 2, Q1):

| cycle | σ | peak-to-peak | verdict recorded |
|---|---|---|---|
| 10 s | 0.47 °F | — | +18 °F steady offset |
| **15 s** | 1.3 °F | **7.5 °F** | **100 % within ±5 °F**, err −0.1 °F — best; and `pid_sp`'s own recommendation in `controller/controllers.json` (`cycle_time: 15`) |
| 25 s (shipped) | 2.3 °F | — | 96 % within ±5 °F |
| 40 s | 3.9–6.4 °F | — | fails |
| **60 s** | **7.5–9.6 °F** | **40–47 °F** | **fails** |
| 100 s | — | 58–68 °F | — |
| 150 s | — | 124–167 °F | — |

> **Citation gap, stated rather than papered over.** The figure "**33 % within ±5 °F** at
> 60 s" was carried into this spec from the coordinator's brief. The committed record at
> `progress.md:28` gives σ 7.5–9.6 °F and peak-to-peak 40–47 °F for the 60 s arm and records
> the verdict as "fails", but does **not** carry an in-band percentage for that arm. The
> 33 % figure is therefore reported here as unverified against a committed output. It does
> not change the conclusion — a 40–47 °F peak-to-peak ripple cannot be mostly inside a 10 °F
> band — but it should be re-derived or dropped rather than propagated. This project has
> shipped two under-derived constants and had to correct both.

**Mechanism.** R5.3. A longer cycle gives a finer duty quantum but a coarser fuel lump; once
the cycle exceeds the plant dead time the lump dominates. The "finer duty quantum" framing
was **retracted by the user themselves on exactly this ground** (`progress.md:28`: *"My
'finer duty quantum' framing was the wrong lens"*). Nobody should re-introduce it.

**Status: accepted regression, deferred to later work.** The user has ruled: *"No, we can fix
PID later."* This is not an oversight and it is not something this spec solves. `HoldCycleTime`
lives in `cycle_data` (`common/defaults.py:123`) and is shared across every controller
(`controller/pid_sp.py:75`, `pid_ac.py:64`, `fuzzy.py:62`, `ml.py:44`, and
`controller/runtime/logic/cycle.py:26-40`), so raising the global default degrades PID users
in order to buy MPC headroom. That trade was made knowingly.

### RISK-2 — 60 s may be wrong for MPC too, and this spec cannot say.

My reading of the evidence: the case for **60 s specifically** is not established by anything
this project has measured, and the one controller measured at 60 s failed badly. The physical
mechanism that broke `pid_sp` — the fuel lump exceeding the dead time — is a *plant* property,
not a controller property, so it acts on MPC as well. What differs is that MPC has a model and
can anticipate the lump within a cycle, which `pid_sp` cannot; whether that is enough is
exactly the open question. MAK's 100 s dead time is longer than a 60 s cycle, which is the one
structural reason to expect MPC on this grill to fare better than `pid_sp` on `GrillSim` did.

**The change is specced as directed, under the stated assumption that the user's operational
experience with this grill is evidence this project has not reproduced** (§5). R6 is the
measurement that settles it, and R6.6 fixes the fallback (30 s) in advance.

**What would settle it:** R6.4's peak-to-peak ripple for MPC at 25/30/60 s on both plants at
180/225/325/450 °F. If MPC's ripple at 60 s is within noise of 25 s, the ruling is vindicated
and this risk closes. If it moves the way `pid_sp`'s did, 30 s is the answer and the user
should be told with the number.

### RISK-3 — R2 and R5 interact, and R6 must hold `u_min` fixed.

Lowering `u_min` (R2) and lengthening the cycle (R5) both move `max(u_min, 1/HoldCycleTime)`.
If `u_min` reaches the 0.0335 that R2.2 implies for a 180 °F MAK hold, then at 25 s the
resolution floor (0.0400) becomes binding and the two changes swap roles. R6.1 therefore pins
`u_min = 0.10` across all its arms so the cycle-time result is not confounded, and a re-run
is required if R2 lands first.

---

## 7. Deferred

- **PID hold quality at the new cycle time.** `pid_sp` (and by extension the other PID
  variants sharing `cycle_data`) needs its own fix at `HoldCycleTime = 60`. Its own
  recommendation in `controller/controllers.json` is 15 s and it measured best there
  (§6). Not designed here, by ruling. The likely shapes — a per-controller cycle time
  honouring the existing unused `recommendations.cycle` block (D6), or a PID-side change
  that tolerates a coarser lump — are recorded as starting points only.
- **Measuring the real auger's duty resolution.** The `1/HoldCycleTime` floor is a harness
  artefact (E-8 note); production evaluates the toggle at ~20 Hz. Nobody has measured what
  the physical auger and pellet delivery actually impose, and every resolution argument in
  this document is bounded by a number nobody has confirmed.

---

## 8. Deliberately out of scope

- **Longer horizons and terminal costs.** Closed as not binding: τ = 5707 s against a 600 s
  horizon is a real ratio, but the best row in the entire R5 grid used lead = 0 s and beat
  the 600 s-horizon MPC by 42× on overshoot (E-4). Zero lookahead already wins.
- **Fan-based braking.** Worth ~1 °F (E-6). Fan work is scoped as *range*, in the predecessor
  spec.
- **Offset-free machinery.** Already present (`mpc_model.py:9,25,367,458`, estimated at
  `mpc.py:364`). "Never settles" in E-3 was an infeasible setpoint, not missing integral
  action.
- **Changing `GrillSim`'s `H` calibration.** Flagged as an open decision in
  `.superpowers/sdd/2026-08-01-live-setpoint-set-target/progress.md` (H = 420 vs a
  PiFire-`augerrate`-implied ~140). Every banked MPC result used H = 420, so changing it
  breaks comparability with everything this spec cites. Separate decision, separate re-run.
- **`t_step` / `control_period` mismatch.** Carried forward unchanged from the predecessor
  spec's out-of-scope list.
- **The `_COAST_BOUND` factor itself** (R7.3).
- **Auto-writing derived `u_min` into settings** (D4).

---

## 9. Prior measurements this spec invalidates

RC-1 invalidates, for citation purposes, every `MAKGrillSim` number produced by the matrix
harness at `u_min=0.15` where the setpoint sits below the 368.9 °F floor that setting
imposes. These must be re-measured at the shipped configuration (R1.1) before being used as
evidence for anything:

| Measurement | Where | Why it is invalid | Status |
|---|---|---|---|
| **A9a's entire MAK column** — U5 `0.21/81.2/never`, F5 cooks 1–3 `0.21/81.2/never`, U3 `0.21/82.7/never`, F3 cook 3 `5.25/42.4/never` at 325 °F | `.superpowers/sdd/2026-08-02-mpc-online-identification/progress.md` (A9a); `docs/superpowers/experiments/_structure_compare.txt` | 325 °F is below the 368.9 °F floor at `u_min=0.15`. The controller could not reach the setpoint at any quality. | **Must be re-measured.** E-3 has re-run the U3 arm at 0.10 (`63.87/79.2/3959`); the F3, U5 and F5 arms have not been. |
| The MAK **`%<5 °F` and `settle_s`** columns generally | same | Degenerate, not merely noisy: τ ≈ 2 h, so nothing settles within ±5 °F in a 3 h run in any arm (A9a metric trap). | Do not cite as quality scores at all, at any `u_min`. Read overshoot / peak / IAE. |
| Any other sub-435 °F MAK scenario in `_structure_compare.txt` | `docs/superpowers/experiments/_structure_compare.txt` | Same floor argument; 435.4 °F is the fan-0 floor at `u_min=0.15`. | Must be re-measured before citation. |

**Not invalidated:**

- **All `GrillSim` results.** Its floor at `u_min=0.15` is 213.0–260.8 °F (E-2's companion
  rows, `_control_rethink.txt:34-36`), below every scenario run on it.
- **The A9b acceptance test** (`tests/e2e/test_mpc_learns_a_grill.py`, 522.5 → 451.5 →
  451.3 °F). 450 °F is above the 435.4 °F fan-0 floor even at `u_min=0.15`, which is why that
  setpoint was chosen. The result stands.
- **Everything in `_control_rethink.txt`.** It states its configuration per run and sweeps
  `u_min` explicitly as a variable.
- **The A10/A11/A12 identification and promotion-gate findings.** They are fits against logged
  records, not closed-loop runs through this harness.

---

## 10. Verification

| Requirement | How it is proven |
|---|---|
| R1.1–R1.2 | `tests/unit/controller/` — a test asserts the harness's cycle configuration equals `default_settings()["cycle_data"]` for every key it does not explicitly override, and **fails** if the literal at `:53` is reintroduced. Mutation: change one default, assert the test catches it. |
| R1.3–R1.4 | Harness unit test: a scenario whose setpoint is below its own derived floor produces an `INFEASIBLE` label; the E-3 configuration (`u_min=0.15`, 325 °F, MAK) is the fixture, and its expected label is `INFEASIBLE`. |
| R2.1–R2.2 | `tests/unit/mpc/` — the closed form reproduces the committed `u*` table (E-8) for `MAKGrillSim` at 180/225/325/450/520/600 °F, both fan extremes, to the printed precision. This is a golden test against `_control_rethink.txt`, so a change in the formula cannot pass silently. |
| R2.3–R2.4 | Test asserts the derivation emits a warning and leaves `cycle_data["u_min"]` unmodified; negative control asserts a configuration already below the derived bound emits nothing. |
| R3.1–R3.2 | `tests/unit/mpc/` — with a known model, assert the applied baseline equals the closed-form `u*` for the active setpoint; assert it is recomputed on `set_target()` and on model adoption (guarding the RC-4 class of defect the predecessor spec's R7 already pinned). |
| R3.3 | Inequality test on the shipped `_DEFAULTS`: the feed-forward arm's overshoot is **not worse** than the no-feed-forward arm's on the same plant/seed. |
| R3.4 | Closed-loop scenario run (`slow`-marked, excluded from the default suite per the A9b `addopts` change): MAK 325 °F at shipped `cycle_data`, medians over ≥3 seeds, reported against E-4's 79.2 °F shipped and 1.0 °F oracle. |
| R4.1–R4.4 | `tests/unit/mpc/` — a setpoint below the derived floor produces the unreachable report carrying the floor temperature and the binding setting; a setpoint above it produces silence; the grill still heats in both cases. |
| R5.1–R5.2 | `common/defaults.py` and `common/settings_schema.py` assert-equal test (the pairing is already the schema's stated contract at `settings_schema.py:83`); plus a migration test asserting a stored `cycle_data` carrying `HoldCycleTime: 25` is **unchanged** by upgrade. |
| R5.3–R5.4 | Documentation-only; the mechanism and the derived 30 s minimum are pinned by this spec, and R6 is the empirical check. |
| R6.1–R6.6 | A new experiment script under `docs/superpowers/experiments/` with committed output, run **before** R5 ships. Not run as part of this spec. |
| R7.1–R7.3 | Re-derivation lands with its own committed measurement; the existing `model_promotion` tests that pin `braking_distance` as a product of `_COAST_BOUND` and `_model_coast` must still pass unmodified. |

**Standing constraint:** the closed-loop scenario runs above are `slow`-marked and deselected
by `pyproject.toml`'s `addopts = "-m 'not slow'"`. They are run deliberately, and their output
is committed, not re-derived on demand.
