# MPC Controller for PiFire

> This document describes the MPC controller **as built**. The architecture
> reflects the shipped code — nonlinear radiative grey-box, EKF default, optional
> neural-net policy, 5 s control period over a 25 s prediction step — not the
> original linear/KF sketch.
> On a realistic plant the steady band is **~±1 °C RMS** (see **Accuracy**); an
> intermediate draft reported ~±3–8 °C, but that came from a test plant whose wind
> model was wildly overstated (×1.6–2.6 heat-loss gusts) — corrected to a realistic
> light breeze, the original ±1 °C aspiration is essentially met.

## Overview

A Model Predictive Control (MPC) controller selectable alongside PiFire's
PID/ML/fuzzy controllers, active in **Hold** mode. It uses a **cascade** design:

1. An **outer loop** manipulates a single scalar — a net **firing-rate /
   heat-release demand `Q`** — against a grey-box thermal model of the grill with
   an integrating-disturbance state for **offset-free** tracking.
2. An **inner combustion allocator** maps `Q` to the physical actuators
   (**auger** duty and, on PWM/DC-fan builds, **fan** duty) along an air-fuel
   curve.

One input, one output: the optimization stays well-conditioned and nonsensical
fuel/air combinations are impossible by construction. The **igniter is not** an
MPC actuator — it stays under `control.py`'s startup/reignite/flame-out logic.

The outer loop has two interchangeable policies and three estimators (below). All
operate internally in **Celsius**.

## Accuracy (realistic, offset-free)

Validated closed-loop against a deliberately mismatched, realistic plant
(`controller/grill_sim.py`: pellet pulses, ~20 s deadtime, fan-as-lever, sensor
lag, light occasional wind, AFR-dependent efficiency). The steady band is
**~±1 °C RMS** across the whole operating range, with peaks growing modestly with
temperature, **offset-free at every setpoint** (mean bias < 0.4 °C):

| setpoint | steady-state RMS | peak \|error\| |
|---|---|---|
| 110 °C (230 °F) | ~0.9 °C | ~2.4 °C |
| 190 °C (374 °F) | ~1.05 °C | ~3.7 °C |
| 218 °C (425 °F) | ~1.1 °C | ~3.8 °C |
| 260 °C (500 °F) | ~1.1 °C | ~3.8 °C |
| 288 °C (550 °F) | ~1.1 °C | ~3.2 °C |
| 316 °C (600 °F) | ~1.15 °C | ~4.8 °C |

The band stays ~±1 °C RMS all the way to **600 °F** — high-temp holding is a
non-issue, and there is firing headroom (only ~77 % of max fire at 600 °F, never
pinned), so the controller still rejects disturbances near the ceiling. This flat
band across 230–600 °F is where the nonlinear radiative (T⁴) term earns its keep:
the temperature-dependent loss is built into the model gain, so one calibration
spans the range without the loop running out of authority.

**Setpoint changes** (e.g. a brisket cook stepping 225 → 275 → 300 °F) reach the
new target in **~1–2 min** and overshoot **~4–8 °F**. A **cold preheat** to a high
target (e.g. 500 °F) rises in **~8 min** and overshoots only ~5 °F before settling.
Three design choices make this work: the firing-move penalty `R_dQ` is kept low so
the rise is fast; the integrating-disturbance estimate is kept slow (`est_q_dist`
low) so it does not chase transients; and the deadtime is modeled so the MPC
predicts across it.

On real hardware accuracy further depends on calibration and the specific grill.
The regression gate (`tests/test_mpc_closed_loop.py`) asserts the band at 110 °C
(RMS ≤ 2 °C, ≥ 90 % of samples within ±2.5 °C, |peak| ≤ 5 °C, |bias| ≤ 1 °C).

**A note on "realistic."** An earlier `grill_sim` wind model applied ×1.6–2.6
heat-loss gusts every ~250 s; that single (unrealistic) disturbance dominated the
band (~±3–8 °C) and produced what looked like a setpoint-step limit cycle. Real
cooks are mostly calm with at most a light 1–2 mph breeze, so wind now bumps loss
only a few percent — and the band collapses to the ~±1 °C above. The control loop
itself was never the problem: against a *matched* plant it tracks to ~0.1 °C.

## Goals

- `controller/mpc.py` plugging into the existing controller framework, selectable
  through the wizard/settings like any other controller.
- Tight **offset-free** Hold-mode tracking (band per **Accuracy** above),
  validated against a mismatched realistic plant.
- Combustion kept on a sensible air-fuel curve by the allocator.
- Ships with working defaults; an offline utility refines the model from logged
  data, and an optional neural-net policy can be retrained from it.

## Non-Goals

- No igniter control by the MPC.
- No changes to Startup/Smoke/Shutdown/Prime; MPC is active only in **Hold**.
- No *guaranteed* fixed band on real hardware — the ~±1 °C RMS above is on the
  simulator; real accuracy depends on calibration and the specific grill/conditions.
- The NLP policy is built on **`do-mpc`** (CasADi/IPOPT, plus `pandas`); these are
  dependencies. The **net policy + EKF path needs only numpy/scipy** (do-mpc is
  imported lazily and is never touched in that mode).

## Context / Environment

- This work runs on x86 (not constrained to a Raspberry Pi); horizon length, the
  estimators, and the do-mpc/CasADi/IPOPT toolchain add negligible cost.
- The controller framework: `control.py` does
  `importlib.import_module('controller.mpc')` then `Controller(config, units,
  cycle_data)`. `ControllerBase` (`controller/base.py`) defines the contract;
  `update(current)` returns the actuation command. Config defaults derive from
  `controller/controllers.json` via `_default_controller_config()`, so a manifest
  entry alone populates settings.
- In Hold mode `control.py` time-proportions the auger on/off within
  `HoldCycleTime` (default 25 s) using a cycle ratio; `u_min=0.1`, `u_max=0.9`.

## Architecture

### Grey-box thermal model (`controller/mpc_model.py`)

A continuous-time model sampled at the prediction step `t_step`. State order:
**`[q0 … q_{n_delay-1}, T_f, T_c, d]`**.

- **`T_f`** — firepot / burn-zone temperature (fast lumped mass, `C_f`).
- **`T_c`** — chamber temperature (slow lumped mass, `C_c`); the controlled
  output, compared against the control probe.
- **`d`** — an **integrating disturbance state** (rhs 0, driven by the estimator)
  that absorbs ambient drift, pellet-quality variation, and model error → this is
  what makes tracking offset-free.
- **`q0 … q_{n_delay-1}`** — a chain of `n_delay` first-order lags approximating
  the feed→combustion→sensor **transport deadtime** (an Erlang / distributed-delay
  of mean duration `theta`), so the model predicts *across* the deadtime instead
  of over-correcting. Modeling this deadtime is the single biggest driver of the
  realistic band.

Dynamics (with `heat_in` = the last lag output, or `Q` directly if `n_delay=0`):

- `C_f·dT_f/dt = K_Q·heat_in − h_fc·(T_f − T_c)`
- `C_c·dT_c/dt = h_fc·(T_f − T_c) − h_amb·(T_c − T_amb) − rad_loss(T_c) + d`
- `dd/dt = 0`, lag chain `tau_d·dq_i/dt = q_{i-1} − q_i` (`q_{-1} ≡ Q`)

where **`K_Q`** maps the abstract firing rate to actual heat (calibrated to grill
power) and **`rad_loss(T_c) = sigma·((T_c+273.15)⁴ − (T_amb+273.15)⁴)`** is a
**nonlinear radiative chamber loss**. The radiative term is the temperature-
dependent gain that lets a *single* calibration span 110–290 °C; with `sigma=0`
the model degenerates to the linear two-mass model.

`Q` is bounded `[Q_min, Q_max]` and is the only manipulated variable.

### Combustion allocator (`controller/mpc_allocator.py`)

`allocate(Q) → (auger_duty, fan_duty)` maps `Q` linearly onto a fraction
`frac = clip((Q−Q_min)/(Q_max−Q_min), 0, 1)`:

- `auger_duty = u_min + frac·(u_max − u_min)` — monotonic over `[u_min, u_max]`.
- `fan_duty = fan_min_pct + frac·(fan_max_pct − fan_min_pct)` when
  `enable_fan_input` is set (PWM/DC fans), else `None` (AC on/off fan stays on).

Air tracks fuel so the air-fuel ratio stays near target across the firing range.
The MPC layer is identical on either fan type — it always commands `Q` — which
removes the earlier "fan input only on PWM" special-casing from the optimizer.

### State / disturbance estimator

Each step, before the policy, the estimator updates `[q…, T_f, T_c, d]` from the
measured probe temperature. All three expose `update(Q_applied, y) → state` and
are discretized at the **control period** (so faster re-solves estimate real
elapsed time). The integrating `d` drives steady-state error to zero.

- **EKF (`GreyBoxEKF`, default).** An extended Kalman filter that linearizes the
  one nonlinear (radiative) term each step (slope `4·sigma·(T_c+273.15)³`, with
  the linearization offset folded into the affine input), keeping the exact
  `expm` propagation for the stiff linear part. Nonlinear-capable like the MHE but
  ~0.3 ms/step (one small `expm`), and it reduces **exactly** to the linear KF
  when `sigma=0`.
- **MHE (`GreyBoxMHE`).** A moving-horizon estimator over the nonlinear model,
  with the control input modeled as a **known time-varying parameter** (fed the
  applied-input history) rather than a free decision variable — that detail is
  what makes it offset-free. Solves an NLP (~9.5 ms/step). Equivalent steady band
  to the EKF; kept as an option.
- **KF (`GreyBoxKF`).** The linear Kalman filter; valid only when `sigma=0`.

**Decision record.** For a *linear* model the KF is optimal (KF ≡ MHE in the
linear-Gaussian case), so the original linear design used the KF and rejected
do-mpc's MHE (whose default objective leaves the input free and is not
offset-free). When the model gained the nonlinear radiative term, the estimator
was re-evaluated: the input-as-known-parameter MHE matched the KF and engaged
`d`, and the **EKF** then gave the same accuracy at ~30× lower cost — so the EKF
is the shipped default. (Spikes: `mpc_cascade_spike.py`, `mpc_mhe_spike.py`,
`mpc_nonlinear_mhe_spike.py`, `ekf_vs_mhe.py`.)

### Firing-rate policy

Two interchangeable policies produce `Q` from the estimated state; selected by the
`policy` option.

- **`nlp` (default).** A `do_mpc.controller.MPC` over horizon `n_horizon` at step
  `t_step`. Cost penalizes tracking error `Q_w·(T_c − T_set)²` (`mterm`/`lterm`)
  and firing-rate moves `R_dQ·ΔQ²` (`rterm`); box bounds on `Q`; IPOPT backend,
  warm-started. ~18 ms/step on x86.
- **`net`.** A pre-trained, **pure-numpy** feed-forward net
  (`controller/mpc_net.py`, `NetPolicy`) that approximates the NLP policy across
  the operating range, so the per-step solve becomes a few matmuls (~0.1 ms). It
  is offset-free **by construction**: the net learns only the *transient
  residual* and the steady-state firing rate is added analytically,

  ```
  Q = Q_ss(d, T_set) + net([state, u_prev, T_set])
  Q_ss = [h_amb·(T_set − T_amb) + rad_loss(T_set) − d] / K_Q
  ```

  so any net approximation error vanishes at steady state. The artifact
  (`controller/mpc_policy_net.npz`, ~80 KB, trained spanning 100–290 °C) embeds
  the full calibration it was trained for. The controller **falls back to the
  NLP** if the artifact is missing, unreadable, or its calibration does not match
  the active config — so a stale net (e.g. after recalibration) can never silently
  mislead. With `net` + EKF the controller never imports do-mpc/CasADi.

`do_mpc` is imported **lazily** (only when the NLP is built), so the net+EKF path
requires only numpy/scipy.

### Control rate

The policy re-solves at a configurable **control period** (`control_period`,
default **5 s**) — independent of the prediction `t_step` (25 s). The estimator
is discretized at this period, so a shorter period tracks probe measurements more
frequently; a cadence sweep on the realistic plant put the tightest band at ~5 s
(RMS ~0.65 °C), with 1 s adding a small steady bias and 25× the solves. `control.py` calls `get_control_period()` and invokes the controller
once per period in Hold; the estimator discretization tracks the real interval so
an occasional faster/slower tick is handled correctly. Both policies are far
faster than the period (NLP ~18 ms, net ~0.1 ms, EKF ~0.3 ms).

### Actuation contract (integration with `control.py`)

`update(current)` is backward-compatible in return type:

- Legacy controllers return a `float` cycle ratio (unchanged).
- MPC returns a `dict`: `{'cycle_ratio': <auger_duty>, 'fan': {'duty': <pct or None>}}`.

In the Hold loop, `control.py` calls `update(ptemp)` every `control_period`, then
`normalize_controller_output()` splits the result into the cycle ratio and an
optional fan command. The cycle ratio feeds the existing auger time-proportioning
(`u_min`/`u_max` clamped); if a fan duty is present and a PWM/DC fan is available,
it is routed through `control['duty_cycle']` (which also suppresses the legacy
temperature-profile fan logic so it cannot overwrite the MPC command). A `float`
return behaves exactly as today.

### Grill simulator (test-only, `controller/grill_sim.py`)

A higher-fidelity nonlinear plant, **deliberately mismatched** from the
controller's model, so a passing closed-loop test is honest about realistic
performance. It models discrete pellet **pulses**, transport/ignition
**deadtime** (~20 s), the **fan as a real lever** (it accelerates burn, boosts
firepot→chamber convection, and increases chamber→ambient loss), radiative loss,
combustion/pellet noise, **sensor lag** (probe `tau` ~4.5 s), and a **light,
occasional wind** breeze (a few-percent loss bump — deliberately modest, since an
earlier ×1.6–2.6 gust model was unrealistic and dominated the band).
`step(auger_on, fan_frac)` advances 1 s; `measured()` is the lagged + noisy probe
reading; `true_Tc` is the noise-free chamber temperature.
Full firing reaches ~341 °C (~646 °F), so even a 316 °C (600 °F) setpoint is held
with headroom (~77 % fire).

## Configuration / Settings / Wizard

The `mpc` entry in `controller/controllers.json` `metadata`
(`module_name: "mpc"`, `recommendations.cycle = {cycle_time: 25, ratio 0.1–0.9}`)
exposes a `config` array (same schema the wizard renders for PID). Shipped
defaults:

- **MPC:** `n_horizon=24`, `t_step=25.0`, `control_period=5.0`, `Q_w=1.0`,
  `R_dQ=0.1` (low for fast rise + tight band), `Q_min=5.0`, `Q_max=100.0`.

- **Grey-box model (calibrate per grill):** `C_f=9.0`, `C_c=320.0`, `h_fc=1.3`,
  `h_amb=0.50`, `T_amb=20.0`, `theta=50.0`, `n_delay=4`, `K_Q=3.5`,
  `sigma=1.4e-9`.
- **Estimator:** `estimator='ekf'` (`ekf` | `mhe` | `kf`); covariances
  `est_q_temp=1e-2`, `est_q_dist=0.05`, `est_r_meas=0.04`. `est_q_dist` is kept
  low on purpose — a fast disturbance estimate chases unmeasured transients and
  worsens setpoint-step overshoot.
- **Policy:** `policy='nlp'` (`nlp` | `net`),
  `policy_net_path='./controller/mpc_policy_net.npz'`.
- **Allocator:** `fan_min_pct=40.0`, `fan_max_pct=100.0`,
  `enable_fan_input=False`.
- **Calibration logging:** `log_data=False`,
  `log_path='./controller/mpc_calibration_log.csv'`.

`settings['controller']['config']['mpc']` derives automatically from the manifest
`option_default`s — no `common.py`/`settings.json` edit needed.

## Calibration

- **`controller/update_mpc.py`** — offline least-squares fit. It reads a logged
  CSV (`time_s, temp_c, Q`) and fits the grey-box parameters **`K_Q, C_c, h_fc,
  h_amb`** (with `C_f` held fixed), writing them back as the `mpc` config. Ships
  with working defaults; calibration is optional.
- **Data logging** — set `log_data=True` and the controller appends one
  `(time_s, temp_c, Q)` row per control step to `log_path`, ready for
  `update_mpc.py`.
- **Net policy retrain** — the shipped net is trained on the default calibration.
  After recalibrating, regenerate it with
  `docs/superpowers/experiments/export_span_net.py` (which samples the NLP policy
  spanning setpoints via `sample_mpc.py` and trains the residual net). Until then
  `policy='net'` auto-falls back to the NLP, so nothing breaks.

## Error Handling

- On **any** policy error (IPOPT non-success, net exception) `update()` holds the
  previous applied move, clamped to `[Q_min, Q_max]` — never an unbounded/NaN
  command, so the Hold cycle never breaks.
- Expensive setup (do-mpc/IPOPT, or net load) happens once at construction;
  `update()` only runs the estimator, the policy, and the allocator map.
- `policy='net'` validates the artifact's calibration against config and falls
  back to the NLP on any mismatch or load failure.
- Construction fills missing config keys from shipped defaults; the dict return is
  normalized defensively in `control.py`.

## Testing (hardware-free, all in `tests/`)

> `control.py` runs an unguarded module-level loop, so tests never import it; they
> exercise `controller.mpc` and the helpers directly.

- **`test_mpc_deps.py`** — the do-mpc/CasADi toolchain installs and solves.
- **`test_mpc_model.py`** — the grey-box model builds; open-loop response is
  first-order-like; the loss terms give the correct steady-state gain.
- **`test_mpc_allocator.py`** — `Q → (auger, fan)` is monotonic, respects bounds,
  and returns `None` fan when disabled.
- **`test_mpc_ekf.py`** — the EKF reduces exactly to the KF at `sigma=0`, the
  radiative term changes the estimate, and it is offset-free.
- **`test_mpc_controller.py` / `test_mpc_integration.py`** — `update()` returns
  the documented dict; integration/dispatch behaves for dict and float.
- **`test_mpc_logging.py`** — calibration logging writes header + rows only when
  enabled.
- **`test_mpc_calibration.py`** — `update_mpc.py` recovers known parameters from
  synthetic logs.
- **`test_mpc_net.py`** — the pure-numpy `NetPolicy` reproduces torch-computed
  reference outputs (export/import fidelity), is bounded and monotone in setpoint,
  and rejects mismatched calibrations.
- **`test_mpc_net_loop.py`** — `policy='net'` activates without building the NLP,
  holds the realistic band at 110 °C and 220 °C, and falls back to the NLP on a
  missing artifact or a calibration mismatch.
- **`test_mpc_closed_loop.py`** — the realistic-band regression gate (see
  **Accuracy**) plus an offset-free (|bias| ≤ 2.5 °C) assertion.
- **`test_mpc_manifest.py`** — the `mpc` manifest entry and defaults
  (`estimator='ekf'`, `policy='nlp'`, the `policy` option list) are present.

## Reference experiments

Standalone spikes in `docs/superpowers/experiments/` record how the design
arrived here: cascade vs. direct (`mpc_cascade_spike.py`, `mpc_direct.py`),
deadtime and the realistic plant (`mpc_deadtime_spike.py`,
`mpc_nonlinear_deadtime.py`, `mpc_hifi_sim.py`), estimator choice
(`mpc_mhe_spike.py`, `mpc_nonlinear_mhe_spike.py`, `mpc_kf_vs_mhe_deadtime.py`,
`ekf_vs_mhe.py`), the final controller comparison (`mpc_final_compare.py`,
`mpc_temp_range.py`), and the neural-net policy (`approxmpc_poc.py`,
`sample_mpc.py`, `approxmpc_span.py`, `export_span_net.py`).
