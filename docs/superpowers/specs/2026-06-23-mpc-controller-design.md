# MPC Controller for PiFire

## Overview

Add a Model Predictive Control (MPC) controller as a selectable alternative to
the existing PID/ML/fuzzy controllers. The MPC controls the **auger** (pellet
feed cycle ratio) and, where hardware supports it, the **fan**, using a
grey-box thermal model of the grill, an online disturbance estimator for
offset-free tracking, and a constrained optimization solved each control cycle.
The goal is tight setpoint tracking (target: ±1.0 °C steady-state band),
validated in closed-loop simulation.

The igniter is **not** an MPC actuator; it remains under control.py's existing
startup/reignite/flame-out logic.

## Goals

- A new `controller/mpc.py` that plugs into the existing controller framework
  and is selectable through the wizard/settings like any other controller.
- Tight setpoint tracking in Hold mode, validated to a ±1.0 °C steady-state
  band in a closed-loop simulation whose plant is deliberately mismatched from
  the controller's internal model (so the result is not tautological).
- Full integration: controller manifest, settings defaults, and wizard support.
- Ships with working default model parameters; an offline calibration utility
  lets users refine the model from their own logged data.

## Non-Goals

- No igniter control by the MPC.
- No changes to Startup/Smoke/Shutdown/Prime modes; like every existing
  controller, MPC is active only in **Hold** mode.
- The MPC is built on **`do-mpc`** (which pulls in `CasADi`); these are added
  as dependencies. No bespoke QP/solver code is hand-rolled.
- No claim of ±1.0 °C on real hardware; that depends on calibration and the
  specific grill. The acceptance gate is the closed-loop simulation.

## Context / Environment

- PiFire currently runs on x86 for this work (not constrained to a Raspberry
  Pi), so horizon length, discretization granularity, the estimator, and the
  `do-mpc`/`CasADi`/IPOPT toolchain add negligible cost.
- `do-mpc` (with `CasADi`) is added to `pyproject.toml`. `do-mpc` provides the
  three pieces we need from one coherent framework: the symbolic model, the MPC
  controller (IPOPT backend), and the estimator (MHE) — plus a simulator used
  for closed-loop tests.
- The controller framework: `control.py` does
  `importlib.import_module(f'controller.{name}')` then
  `Controller(settings['controller']['config'][name], units, cycle_data)`.
  `ControllerBase` (`controller/base.py`) defines the contract; the key method
  is `update(current) -> cycle_ratio`.
- In Hold mode, `control.py` calls `controllerCore.update(ptemp)` once per
  `CycleTime`, clamps the returned ratio to `cycle_data['u_min'/'u_max']`, and
  uses it to time the auger on/off within `HoldCycleTime` (default 25 s).
  Defaults: `u_min=0.1`, `u_max=0.9`, `HoldCycleTime=25`.
- Precedent for an internal model: `controller/pid_sp.py` already carries FOPDT
  parameters `tau=115`, `theta=65`. Precedent for an offline trainer:
  `controller/update_ml.py` + `controller/ml_dataset.csv`.

## Architecture

### Grey-box model (state-space, 2 thermal masses + disturbance)

A discrete linear state-space model, sampled at `Ts = HoldCycleTime`:

- **States:**
  - `T_f` — firepot / burn-zone temperature (fast lumped mass).
  - `T_c` — chamber/grate temperature (slow lumped mass); this is the
    controlled output, compared against the control probe.
  - `d` — an **integrating disturbance state** (offset-free augmentation) that
    absorbs ambient drift, pellet-quality variation, and model error.
- **Inputs:**
  - `u_auger` ∈ [u_min, u_max] — pellet feed duty (heat into the firepot).
  - `u_fan` ∈ [fan_min, fan_max] — combustion air / convection; an MPC input
    only when a PWM/DC fan is present, otherwise held constant (auger-only MPC).
- **Continuous dynamics (before discretization):**
  - `C_f·dT_f/dt = K_a·u_auger·g(u_fan) − h_fc·(T_f − T_c)`
  - `C_c·dT_c/dt = h_fc·(T_f − T_c) − h_amb·(T_c − T_amb)`  ← **ambient loss**
  - `g(u_fan)` is the fan's effect on burn/heat-transfer (linearized about
    `fan_nom`); `h_amb·(T_c − T_amb)` is the loss term that sets steady state.
  - The output bias is `T_c + d`, with `d` modeled as integrating (`d[k+1]=d[k]`
    in the model, driven by the estimator).
- **Parameters (config, with shipped defaults):** `C_f, C_c, h_fc, h_amb, K_a,
  K_f (fan gain), fan_nom, T_amb`. Defaults are chosen to reproduce the
  `pid_sp` first-order response (`tau≈115`, effective deadtime from the
  two-mass lag) at a nominal mid-range setpoint. `T_amb` defaults to a constant
  and is overridden by an ambient probe reading when one is configured.

The dynamics are expressed once as a `do_mpc.model.Model` (continuous-time,
CasADi symbolic). `do-mpc` discretizes internally (orthogonal collocation) for
the controller and integrates the continuous model in the simulator, so the
same parameterization drives the controller, estimator, and test plant.

### Offset-free estimator

Each control cycle, before optimizing, a `do_mpc.estimator.MHE`
(moving-horizon estimator) updates the estimated states `[T_f, T_c, d]` from
the measured probe temperature, weighting process vs. measurement noise per the
configured covariances. The integrating disturbance `d` drives the steady-state
output error to zero under unmeasured disturbances — this is what makes the
±1.0 °C band achievable despite ambient changes and pellet variability,
independent of nominal model accuracy. (If MHE proves heavier than warranted,
an EKF over the same augmented model is an acceptable drop-in; MHE is the
default because it handles the input/state bounds naturally.)

### MPC optimization

- A `do_mpc.controller.MPC` over a prediction horizon `n_horizon` (default
  `≈ 2·tau/Ts`, e.g. ~16 steps) at step `t_step = Ts`. x86 allows generous
  horizons.
- **Cost:** lterm/mterm penalizing tracking error `(T_c − setpoint)²` (weight
  `Q`), with `rterm` penalizing input moves `(Δu)²` (weight `R_Δ`); an input
  effort weight `R` about the disturbance-corrected steady-state input.
- **Constraints:** box bounds on `u_auger` (`u_min`/`u_max`) and `u_fan`
  (`fan_min`/`fan_max`); per-step rate limits via `rterm`/`du` bounds.
- **Solver:** IPOPT via `do-mpc`, warm-started from the previous solution
  (do-mpc keeps the last solution). Apply the first move (`mpc.make_step`).
- Fan input is included only when a PWM/DC fan is available
  (`settings['platform']['dc_fan']` and PWM control active); otherwise the
  optimization is auger-only and the fan stays on, consistent with the existing
  "PWM + FanPid not compatible" note in `control.py`.

### Actuation contract (integration with control.py)

`update(current)` becomes **backward-compatible** in its return type:

- Legacy controllers return a `float` cycle ratio (unchanged).
- MPC returns a `dict`: `{'cycle_ratio': <float>, 'fan': {'duty': <pct or None>}}`.

In `control.py`'s Hold work-cycle, immediately after
`pid_output = controllerCore.update(ptemp)`:

- If `pid_output` is a `dict`: use `pid_output['cycle_ratio']` exactly where the
  float was used today (same `u_min`/`u_max` clamping and auger timing), and, if
  `fan.duty` is not `None` and a PWM fan is available, call
  `grill_platform.set_duty_cycle(duty)`.
- If `pid_output` is a `float`: behavior is identical to today.

A small helper normalizes the return so the change to `control.py` is minimal
and every existing controller is byte-for-byte unaffected. `ControllerBase`
documents the extended return contract.

### Grill simulator (test-only)

`controller/grill_sim.py` — a plant simulator used only by tests, built on
`do_mpc.simulator.Simulator`. To keep the ±1.0 °C validation honest, the
simulator's model is **deliberately mismatched** from the MPC's internal model:
parameter offsets (and/or extra dynamics), additive process/measurement noise,
an **ambient temperature drift**, and a **wind-gust / lid-open disturbance**
(injected through the simulator's `tvp`/`p` hooks). This exercises the
offset-free estimator rather than rewarding a model that matches itself.

## Configuration / Settings / Wizard

- New `mpc` entry in `controller/controllers.json` `metadata`, with:
  - `friendly_name`, `module_name: "mpc"`, `description`, author/attribution.
  - `recommendations.cycle` (cycle_time, cycle_ratio_min/max).
  - a `config` array (same schema the wizard already renders for PID) exposing:
    horizon `Np`/`Nc`; weights `Q`/`R`/`R_delta`; model params `C_f, C_c, h_fc,
    h_amb, K_a, K_f, fan_nom, T_amb`; estimator noise `q_process`/`r_meas`;
    `enable_fan_input`; fan bounds `fan_min`/`fan_max`.
- `settings['controller']['config']['mpc']` default is derived from the manifest
  `config` defaults (same mechanism PID uses), so wizard + settings support is
  automatic once the manifest entry exists.

## Calibration path (secondary)

`controller/update_mpc.py` — an offline utility mirroring `update_ml.py`. It
reads a logged history CSV (time, probe temp, auger duty, fan, ambient if
available) and fits the grey-box parameters (`C_f, C_c, h_fc, h_amb, K_a, K_f`)
via least-squares, writing them back as the `mpc` config defaults. The
controller ships with working defaults and does not require calibration to run.

## Error Handling

- If IPOPT reports a non-success status on a cycle (checked via the do-mpc
  solver stats), the MPC returns the previous applied move (or the
  disturbance-corrected steady-state input), clamped to bounds — never an
  unbounded or NaN command.
- The relatively expensive do-mpc/IPOPT setup happens once at controller
  construction (`controller/mpc.py.__init__`); `update()` only runs
  `estimator.make_step()` then `mpc.make_step()` and maps the result.
- Construction validates config; missing keys fall back to shipped defaults.
- The dict/float return is normalized defensively so a malformed return cannot
  break the Hold cycle.

## Testing (hardware-free)

All in `tests/`:

1. **Model:** the `do_mpc.model.Model` builds; open-loop step response (via the
   simulator) matches expected first-order-like behavior; the ambient-loss term
   yields the correct steady-state gain.
2. **Estimator:** with a constant unmeasured disturbance, the MHE estimate of
   `d` converges so predicted output bias → 0 (offset-free property).
3. **Optimizer:** `mpc.make_step` returns a first move within box and rate
   constraints; a forced non-success solver status falls back safely to a
   bounded command.
4. **Contract/integration:** `update()` returns the documented dict; the
   control.py dispatch helper handles both dict and float; fan duty is applied
   only when PWM is available; legacy float controllers are unaffected.
5. **Closed-loop ±1.0 °C gate:** run `mpc` against `grill_sim` (mismatched plant
   + noise + ambient drift + wind/lid disturbance); after settling, the
   steady-state error stays within ±1.0 °C for a sustained window, inputs stay
   within constraints, and the controller recovers from the disturbance.
6. **Config/registration:** the `mpc` manifest entry exists with
   `module_name == "mpc"`; `_default_*` derivation includes an `mpc` config.
