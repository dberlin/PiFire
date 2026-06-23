# MPC Controller for PiFire

## Overview

Add a Model Predictive Control (MPC) controller as a selectable alternative to
the existing PID/ML/fuzzy controllers. The MPC uses a **cascade** design:

1. An **outer MPC** manipulates a single scalar — a net **firing-rate / heat-
   release demand `Q`** — against a grey-box thermal model of the grill, with an
   online disturbance estimator for offset-free tracking.
2. An **inner combustion allocator** maps `Q` to the physical actuators
   (**auger** duty and, on PWM/DC-fan builds, **fan** speed) along a sensible
   air-fuel curve.

This keeps the optimization well-conditioned (one input, one output, no
fuel/air degeneracy) and makes nonsensical fuel/air combinations impossible by
construction. The **igniter is not** an MPC actuator; it stays under control.py's
existing startup/reignite/flame-out logic.

Built on `do-mpc` (CasADi/IPOPT). Target: ±1.0 °C steady-state tracking,
validated in closed-loop simulation. A feasibility spike has already validated
the architecture (see "Simulation validation").

## Goals

- A new `controller/mpc.py` that plugs into the existing controller framework
  and is selectable through the wizard/settings like any other controller.
- Tight setpoint tracking in Hold mode — a ±1.0 °C steady-state band, validated
  in a closed-loop simulation whose plant is deliberately mismatched from the
  controller's internal model.
- Combustion always kept on a sensible air-fuel curve by the allocator.
- A control step fast enough to run the loop at **≥ 1 Hz** (measured ~8 ms/step;
  see "Control rate").
- Full integration: controller manifest, settings defaults, and wizard support.
- Ships with working default parameters; an offline calibration utility lets
  users refine the model + firing curve from their own logged data.

## Non-Goals

- No igniter control by the MPC.
- No changes to Startup/Smoke/Shutdown/Prime modes; like every existing
  controller, MPC is active only in **Hold** mode.
- The MPC is built on **`do-mpc`** (which pulls in `CasADi`, plus `matplotlib`/
  `pandas`); these are added as dependencies. No bespoke QP/solver code.
- No claim of ±1.0 °C on real hardware; that depends on calibration and the
  specific grill. The acceptance gate is the closed-loop simulation.

## Context / Environment

- PiFire currently runs on x86 for this work (not constrained to a Raspberry
  Pi), so horizon length, the estimator, and the `do-mpc`/`CasADi`/IPOPT
  toolchain add negligible cost. (`casadi 3.7.2` + `do-mpc 5.1.1` verified to
  install and solve on this Python 3.14 environment.)
- `do-mpc` provides the symbolic model, the MPC controller (IPOPT backend), and
  a simulator used for closed-loop tests. The estimator is a Kalman filter over
  the augmented linear model (not do-mpc's MHE — see "Offset-free estimator" for
  the decision record).
- The controller framework: `control.py` does
  `importlib.import_module(f'controller.{name}')` then
  `Controller(settings['controller']['config'][name], units, cycle_data)`.
  `ControllerBase` (`controller/base.py`) defines the contract; `update(current)`
  returns the actuation command. Controller config defaults derive from
  `controller/controllers.json` via `_default_controller_config()` (option keys
  `option_name`/`option_default`), so a manifest entry alone populates settings.
- In Hold mode, `control.py` calls the controller and uses an auger cycle ratio
  to time the auger on/off within `HoldCycleTime` (default 25 s); `u_min=0.1`,
  `u_max=0.9`. Precedent for an internal model: `controller/pid_sp.py`
  (`tau=115`, `theta=65`). Precedent for an offline trainer:
  `controller/update_ml.py`.

## Architecture

### Grey-box thermal model (2 masses + disturbance), input = `Q`

A continuous-time `do_mpc.model.Model`, sampled at the prediction step `t_step`:

- **States:**
  - `T_f` — firepot / burn-zone temperature (fast lumped mass).
  - `T_c` — chamber/grate temperature (slow lumped mass); the controlled output,
    compared against the control probe.
  - `d` — an **integrating disturbance state** (offset-free augmentation; rhs 0,
    driven by the estimator) absorbing ambient drift, pellet-quality variation,
    and model error.
- **Input:** `Q` — net firing-rate / heat-release demand, bounded
  `[Q_min, Q_max]`. This is the *only* manipulated variable the optimizer sees.
- **Dynamics:**
  - `C_f·dT_f/dt = Q − h_fc·(T_f − T_c)`
  - `C_c·dT_c/dt = h_fc·(T_f − T_c) − h_amb·(T_c − T_amb) + d`  ← **ambient loss**
  - `dd/dt = 0`
  - `h_amb·(T_c − T_amb)` is the loss term that sets steady state; `T_amb`
    defaults to a constant and is overridden by an ambient probe when configured.
- **Parameters (config, shipped defaults):** `C_f, C_c, h_fc, h_amb, T_amb,
  Q_min, Q_max`. Defaults reproduce the `pid_sp` first-order response at a
  nominal mid-range setpoint.

Because the single input is `Q`, the model is linear and has no input
degeneracy or bilinearity — this is the core reason the cascade tracks cleanly.

### Combustion allocator (inner layer)

`Q → (auger_duty, fan)` along a sensible air-fuel curve, encoding the combustion
knowledge the MPC must not have to learn:

- `auger_duty` increases monotonically with `Q` over `[u_min, u_max]`.
- `fan` (on PWM/DC-fan builds) increases with `Q` so air tracks fuel, holding
  the air-fuel ratio near its target across the firing range (the principle is
  fuel-air **cross-limiting** — lead with air on ramp-up, cut fuel first on
  ramp-down).
- Honors min/max fire (a pellet grill has a minimum sustainable burn).
- **Hardware-aware:** on a PWM/DC fan it sets fuel **and** air; on an AC (on/off)
  fan it maps `Q` to auger only and the fan stays on. The MPC layer is identical
  either way — it always commands `Q` — which cleanly removes the earlier
  "fan input only on PWM" special-casing from the optimizer.
- **Parameters (config):** firing-curve endpoints (auger/fan at min and max
  fire) and the air-fuel target; calibration refines these.

### Offset-free estimator

Each control step, before optimizing, a **Kalman filter** over the augmented
linear model updates the estimated states `[T_f, T_c, d]` from the measured
probe temperature, weighting process vs. measurement noise per configured
covariances. The integrating disturbance `d` drives the steady-state output
error to zero under unmeasured disturbances — this, more than nominal model
fidelity, is what makes the ±1.0 °C band achievable.

**Estimator choice — KF vs. MHE (decision record).** do-mpc's MHE was evaluated
as the estimator and rejected for this design. For a *linear* grey-box model the
Kalman filter is the optimal estimator (KF and MHE coincide in the
linear-Gaussian case), and the spikes bore this out empirically against the same
mismatched plant:

| | Kalman filter | do-mpc MHE (default objective) |
|---|---|---|
| steady band, max | **0.31 °C** | 1.53 °C |
| within ±1.0 °C | **100%** | 34% |
| mean bias | −0.02 °C | −1.11 °C (not offset-free) |
| per-step solve | ~8 ms | ~16 ms (~2×) |

The MHE failed to be offset-free because do-mpc treats the control input as a
free decision variable, so it absorbed the model mismatch into the estimated
input and left the disturbance state at `d≈0`. Making MHE offset-free would
require extra plumbing to fix its inputs to the applied values — more complexity
and ~2× compute for no accuracy gain on a linear model. The KF is therefore the
chosen estimator (simple, deterministic, ~30 lines, validated at 0.31 °C).

**Re-evaluate MHE if the model becomes non-linear.** MHE earns its keep on
nonlinear models and when hard state/parameter constraints matter — e.g. if a
future revision replaces the linear grey-box with a nonlinear combustion/heat-
transfer model, or estimates physical parameters online. At that point MHE (with
its inputs properly fixed to applied values) should be reconsidered over the
linear KF / an EKF. (Reference spikes:
`docs/superpowers/experiments/mpc_cascade_spike.py` (KF) and
`mpc_mhe_spike.py` (MHE comparison).)

### MPC optimization

- A `do_mpc.controller.MPC` over a prediction horizon `n_horizon` (default
  `≈ 2·tau/t_step`, e.g. ~16–20 steps) at prediction step `t_step`.
- **Cost:** `mterm`/`lterm` penalize tracking error `(T_c − setpoint)²` (weight
  `Q_w`); `rterm` penalizes firing-rate moves `(ΔQ)²` (weight `R_dQ`).
- **Constraints:** box bounds on `Q` (`Q_min`/`Q_max`); optional rate limit on
  `ΔQ`.
- **Solver:** IPOPT via `do-mpc`, warm-started from the previous solution. Apply
  the first move (`mpc.make_step`) → `Q`, hand to the allocator.

### Control rate

The MPC re-solves at a configurable **control period** (`control_period`,
default 1.0 s) — independent of the prediction `t_step`, which stays coarse
(seconds) because grill dynamics are slow. Measured per-step cost on this x86
environment (3 states, `n_horizon=20`, IPOPT, warm-started):

- warm MPC solve: **mean ≈ 8.4 ms, p95 ≈ 11 ms, max ≈ 13 ms**; cold (first)
  solve ≈ 31 ms; Kalman/estimator update ≈ 0.04 ms.
- ⇒ ~120 control steps/second achievable — the **≥ 1 Hz** requirement clears by
  ~100×.

So `control.py` calls the MPC every `control_period` (default 1 s) rather than
once per auger cycle. A timing test asserts the warm solve stays within a budget
(e.g. < 200 ms) so the ≥ 1 Hz guarantee is regression-protected.

### Actuation contract (integration with control.py)

`update(current)` is **backward-compatible** in its return type:

- Legacy controllers return a `float` cycle ratio (unchanged).
- MPC returns a `dict`: `{'cycle_ratio': <auger_duty>, 'fan': {'duty': <pct or None>}}`.

In `control.py`'s Hold loop:

- The MPC is invoked every `control_period`; internally `update()` runs the
  estimator then the optimizer, maps `Q` through the allocator, and returns the
  dict.
- If the return is a `dict`: `cycle_ratio` feeds the existing auger
  time-proportioning (refreshed each control tick, same `u_min`/`u_max`
  clamping); if `fan.duty` is not `None` and a PWM fan is available, call
  `grill_platform.set_duty_cycle(duty)`.
- If the return is a `float`: behavior is identical to today.

A small helper normalizes the return so the `control.py` change is minimal and
every existing controller is unaffected. `ControllerBase` documents the
extended return contract.

### Grill simulator (test-only)

`controller/grill_sim.py` — a plant simulator built on
`do_mpc.simulator.Simulator`, used only by tests. To keep the ±1.0 °C validation
honest, the simulated plant is **deliberately mismatched** from the MPC's model:
parameter offsets, an **AFR-dependent combustion efficiency** (so bad fuel/air
actually loses heat), additive process/measurement noise, an **ambient drift**,
and a **lid-open disturbance**. The allocator's job is to keep the plant on the
high-efficiency part of that curve; the estimator handles the residual.

## Configuration / Settings / Wizard

- New `mpc` entry in `controller/controllers.json` `metadata`, with
  `friendly_name`, `module_name: "mpc"`, `description`, `recommendations.cycle`,
  and a `config` array (same schema the wizard already renders for PID) exposing:
  horizon `n_horizon`, prediction `t_step`, `control_period`, weights `Q_w`/
  `R_dQ`; model params `C_f, C_c, h_fc, h_amb, T_amb`; firing-rate bounds
  `Q_min`/`Q_max`; allocator params (firing-curve endpoints + air-fuel target);
  estimator covariances `q_process`/`r_meas`.
- `settings['controller']['config']['mpc']` derives automatically from the
  manifest `option_default`s — no `common.py` or `settings.json` edit needed.

## Calibration path (secondary)

`controller/update_mpc.py` — an offline utility mirroring `update_ml.py`. It
reads a logged history CSV (time, probe temp, auger duty, fan, ambient if
available) and fits the grey-box parameters and firing curve via least-squares,
writing them back as the `mpc` config defaults. Ships with working defaults and
does not require calibration to run.

## Error Handling

- If IPOPT reports a non-success status on a step (checked via do-mpc solver
  stats), the MPC returns the previous applied move (or the disturbance-corrected
  steady-state firing rate), clamped to bounds — never an unbounded/NaN command.
- The expensive do-mpc/IPOPT setup happens once at construction
  (`controller/mpc.py.__init__`); `update()` only runs `estimator.make_step()`,
  `mpc.make_step()`, and the allocator map.
- Construction validates config; missing keys fall back to shipped defaults.
- The dict/float return is normalized defensively so a malformed return cannot
  break the Hold cycle.

## Testing (hardware-free)

All in `tests/`:

1. **Model:** the `do_mpc.model.Model` builds; open-loop step response (via the
   simulator) is first-order-like; the ambient-loss term yields the correct
   steady-state gain.
2. **Allocator:** `Q → (auger, fan)` is monotonic, respects `u_min`/`u_max` and
   fan bounds, holds the air-fuel target across the range, and falls back to
   auger-only when no PWM fan is present.
3. **Estimator:** with a constant unmeasured disturbance, the estimate of `d`
   converges so predicted output bias → 0 (offset-free property).
4. **Optimizer:** `mpc.make_step` returns a `Q` within bounds; a forced
   non-success solver status falls back safely to a bounded command.
5. **Timing:** warm solve stays within the configured budget (regression guard
   for the ≥ 1 Hz requirement).
6. **Contract/integration:** `update()` returns the documented dict; the
   control.py dispatch helper handles both dict and float; fan duty is applied
   only when PWM is available; legacy float controllers are unaffected.
7. **Closed-loop ±1.0 °C gate:** run `mpc` against `grill_sim` (mismatched plant
   + AFR efficiency + noise + ambient drift + lid disturbance); after settling,
   steady-state error stays within ±1.0 °C for a sustained window, the air-fuel
   ratio stays near target, inputs respect constraints, and the controller
   recovers from the lid disturbance.
8. **Config/registration:** the `mpc` manifest entry exists with
   `module_name == "mpc"`; `_default_controller_config()` includes an `mpc`
   config.

## Simulation validation (feasibility spike)

A standalone spike (`docs/superpowers/experiments/mpc_cascade_spike.py`)
validated the architecture against a mismatched plant (~15% parameter error,
ambient drift 18→10 °C, 0.2 °C sensor noise, a lid-open event, a +14 °C setpoint
step):

- **Cascade:** steady-state |error| max **0.31 °C**, RMS 0.12 °C, mean bias
  −0.02 °C, **100%** within ±1.0 °C; air-fuel ratio pinned at target. Transients
  (excluded from the steady band): a +14 °C setpoint step overshoots the new
  target by only **~1.6 °C** and settles to ±1 °C in **~175 s**; a lid-open
  event (temperature dips ~28 °C below target) recovers to ±1 °C in **~210 s**.
  (The instantaneous error at a step equals the step size — that is lag, not
  overshoot, since temperature cannot change instantly.)
- **Direct two-input baseline (no allocator):** drove air-fuel ratio off target
  (efficiency collapsing to 0), carried a persistent ~1.2 °C bias, held the band
  only ~27% of the time — confirming the cascade is the right architecture.

These results gate the design; the production implementation reproduces them via
the `grill_sim` closed-loop test.
