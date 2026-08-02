# MPC Fan Authority and Calibration Correctness — Design

**Date:** 2026-08-02
**Status:** Ready for planning
**Incident:** 450 °F Hold cook on "Ponce Grill" (x86_numato, DC fan) overshot to 520 °F; fan sat at 100 % for the entire cook.

---

## 1. Incident evidence

Captured from the live install (`/usr/local/bin/pifire`, server 1.11.0 build 74) — data copied out and analysed offline; the live tree was never modified.

Sources: `controller/mpc_calibration_log.csv` (248 rows, 1240 s), `pifire.db` (+WAL), `logs/control.log{,.1,.2,.3}`.

### 1.1 Timeline

| t (s) | Pit temp | Q (firing rate) |
|---|---|---|
| 0 | 105 °F | 100 % |
| 846 | 438 °F | 100 % → begins to roll off |
| 871 | 450 °F | ~79 % — **setpoint crossed, only 25 s of braking lead** |
| 1109 | **520 °F** | 8.6 % — peak, **+70 °F overshoot** |
| 1240 | 518 °F | 5 % (floor) |

### 1.2 Fan

`control:status.outpins` = `{fan: true, pwm: 100}`, `control:general.duty_cycle` = 100 for the whole cook.
Across `control.log.2`, `control.log.1` and `control.log` (17:01 → 17:21, the entire run) there are **zero** `set_duty_cycle` / fan lines. The fan was set once at ignition and never touched again.

### 1.3 Configuration at the time of the incident

| Key | Value |
|---|---|
| `controller.selected` | `mpc` |
| `controller.config.mpc.enable_fan_input` | `true` |
| `controller.config.mpc.log_data` | `true` |
| `platform.dc_fan` | `true` |
| `pwm.pwm_control` | **`false`** |
| `cycle_data.FanPidEnabled` | `false` |
| all `mpc` physical params (`C_f`, `C_c`, `h_fc`, `h_amb`, `T_amb`, `theta`, `n_delay`, `K_Q`, `sigma`) | **bit-identical to the shipped `_DEFAULTS`** |

---

## 2. Root causes

### RC-1 — The MPC's fan command is silently discarded, and it disables the fallbacks on its way out

`controller/runtime/modes/hold.py:180`:

```python
if fan_cmd is not None and settings["platform"]["dc_fan"] and control["pwm_control"]:
```

With `pwm_control == False` the computed duty is dropped on the floor. No log, no warning, no error.

It is worse than a dropped command. `Controller.commands_fan()` returns `enable_fan_input`, and Hold setup does:

```python
self.state.controller.controls_fan = self._runner.commands_fan()   # hold.py:94
```

`controls_fan == True` then suppresses the temperature-profile PWM path as well (`hold.py:298-303`, guarded by `not self.state.controller.controls_fan`). With `FanPidEnabled == False` the fan-assist path (`hold.py:315-320`) is also inert.

**Net effect: with `enable_fan_input=true` and `pwm_control=false`, no code path anywhere can move the fan.** The MPC believes it owns a lever it cannot reach, and its ownership claim disables the only other thing that would have moved it.

This is a capability/authority mismatch that the system neither validates nor reports.

### RC-2 — The MPC ran on a thermal model that was never calibrated to this grill

The MPC's physical parameters are identical to the shipped `_DEFAULTS` in `controller/mpc.py:35-77`, whose own comment reads `Nominal grey-box thermal params -- CALIBRATE to your grill via update_mpc.py`. `log_data: true` shows calibration capture was in progress — i.e. the grill was being *driven* by a model that had never seen it.

Simulating the configured model against the incident's own recorded `(t, Q)` sequence:

| Quantity | Configured model | Reality (fitted to the log) | Error |
|---|---|---|---|
| Time to reach 450 °F | 317 s | 871 s | **2.75× too fast** |
| `C_c` (chamber heat capacity) | 320 | 11465 | **36× too small** |
| `h_amb` | 0.5 | 2.75 | 5.5× too small |
| `theta` (deadtime) | 50 s | ~111 s | 2.2× too short |
| Chamber time constant | 264 s | 3310 s | **13× too fast** |
| Open-loop fit error vs the log | RMSE **108 °C** | RMSE 2.5 °C | — |

A model that believes the plant responds 13× faster than it does also believes it can *arrest* the rise 13× faster. So it brakes far too late: it held `Q = 100 %` until 438 °F and began rolling off only 25 s before crossing setpoint, then the real grill coasted 263 s to a 520 °F peak.

**Secondary:** the prediction horizon is `n_horizon × t_step = 24 × 25 = 600 s`, shorter than the *real* dominant time constant (~3310 s). Even a perfectly calibrated model cannot plan a stop it cannot see.

### RC-3 — The calibration utility does not fit the model the controller actually runs

`controller/update_mpc.py:simulate_chamber` implements:

```python
dTf = (K_Q * Q[i] - h_fc * (Tf - Tc)) / C_f
dTc = (h_fc * (Tf - Tc) - h_amb * (Tc - T_amb)) / C_c
```

The controller's model (`controller/mpc_model.py`, module docstring and `build_do_mpc_model`) is:

```
dT_f/dt = (K_Q * heat_in - h_fc * (T_f - T_c)) / C_f
dT_c/dt = (h_fc*(T_f-T_c) - h_amb*(T_c-T_amb) - sigma*((T_c+273.15)^4 - (T_amb+273.15)^4) + d) / C_c
```
where `heat_in` is the tail of an `n_delay`-state Erlang chain of mean duration `theta`.

The fitter therefore **omits the radiative loss term `sigma` and the entire transport-delay chain**, and never fits `theta`. Fitting a structurally different model to the data yields parameters that are wrong for the model that consumes them — most damagingly it will absorb the missing deadtime into the capacitances. Its `init` values (`C_f=60, C_c=306, h_fc=2.0, h_amb=0.55`) are also inconsistent with the shipped controller defaults (`C_f=9, C_c=320, h_fc=1.3, h_amb=0.5`).

It also only *prints* the fitted numbers — there is no supported path from a fit to the running configuration, and no fit-quality report, so a bad fit is indistinguishable from a good one.

### RC-4 (latent) — `set_target()` discards the applied-firing-rate history

`controller/mpc.py:303-307`:

```python
def set_target(self, set_point):
    self.set_point = set_point
    self._set_point_c = _to_c(set_point, self.units)
    self._last_Q = self.cfg["Q_min"]
    self._applied_Q = float(self.cfg["Q_min"])
```

`_applied_Q` is the firing rate the grill actually ran at, recovered from the reported auger duty by `set_output()` (`mpc.py:339`) and fed to the estimator as its known input (`mpc.py:380`). `_last_Q` is the last commanded rate, used as the hold-over on a solve failure (`mpc.py:395`). Both are facts about the plant, not about the target.

Resetting them to `Q_min` on a setpoint change tells the estimator "we were firing at 5 %" when the grill was at 100 %, so the integrating disturbance state absorbs the whole discrepancy and the state estimate is corrupted for several control periods — precisely when a setpoint step needs it most.

Not implicated in this incident (`set_target` is only called at build and reconfigure, `controller/runtime/runner.py:196,363`), but it fires on any live setpoint change.

### 2.1 How the causes interact

RC-1 corrupts the fix for RC-2. `mpc_allocator.allocate()` maps `Q` onto **both** auger duty and fan duty along an air-fuel curve (fan 40 → 100 % as Q goes 5 → 100). With the fan pinned at 100 %, minimum firing rate still gets maximum combustion air — which is why the grill barely came down at `Q = 5` (520 → 500 °F over several minutes).

**Consequence for sequencing: the calibration log captured during the incident is contaminated.** Fitting it would bake in fan-at-100 % behaviour, producing a model that is wrong again the moment `pwm_control` is enabled. The fan path must be fixed and calibration data re-captured *after*.

---

## 3. Requirements

### R1 — Runtime fan authority (RC-1)

- **R1.1** A single predicate decides whether a controller-issued fan command can physically reach the hardware: `settings["platform"]["dc_fan"] and control["pwm_control"]`. It lives in one place and both the Hold setup path and the per-tick apply path use it.
- **R1.2** When a controller reports `commands_fan() == True` but that predicate is false, Hold logs a **single ERROR** at setup naming both the controller and the disabled setting.
- **R1.3** In that situation Hold sets `state.controller.controls_fan = False`, so the fallback fan paths (temperature-profile PWM, fan-assist PID) are **not** suppressed by an ownership claim the controller cannot honour. Degraded-but-working beats silently dead.
- **R1.4** When the predicate is true, behaviour is unchanged from today.

### R2 — Controller settings page: hard error (RC-1, user-specified)

- **R2.1** On the Controller tab, when the selected controller is `mpc`, the pending `enable_fan_input` is on, and saved `pwm.pwm_control` is off, render a **blocking error** (`role="alert"`), styled with the existing `pf-settings-error-text` class.
- **R2.2** That state **blocks Save**: `onSave` returns without calling `save()`, matching the existing `boundsError` pre-flight pattern in `PwmTab.onSave`.
- **R2.3** The message names the fix: enable **PWM Control** on the **PWM Fan** settings tab.
- **R2.4** The check applies only on DC-fan builds (`platform.dc_fan`). On an AC-fan build there is no PWM fan to own, so the MPC fan option is inapplicable and must not raise this error.

### R3 — PWM settings page: note (RC-1, user-specified)

- **R3.1** On the PWM tab, when the selected controller is `mpc` and `pwm_control` is off *in the tab's current draft value*, render an informational note stating that the MPC is configured to command the fan but PWM Control is off, so its fan commands will not be applied.
- **R3.2** The note is informational — it does **not** block Save. Turning `pwm_control` on in the draft clears it immediately, before saving.

### R4 — PWM settings page: disable the unreachable controls (RC-1, user-specified)

- **R4.1** When MPC fan control is enabled — either in saved settings **or pending in the unsaved Controller-tab draft** — the PWM controls that can never execute are rendered `disabled`.
- **R4.2** The dead controls are exactly those consumed by the `not controls_fan` branch at `hold.py:298-309`:
  - `Update Time` (`pwm.update_time`)
  - the `ΔT range` / `Duty cycle` `RangeProfileTable` (`pwm.temp_range_list`, `pwm.profiles`)
- **R4.3** Controls that remain live because other code paths still consume them stay enabled: `PWM Control`, `Min Duty Cycle`, `Max Duty Cycle` (both used by `clamp_duty` in `start_fan`), and `Frequency`.
- **R4.4** A short explanation is rendered alongside the disabled group. Values are preserved, not cleared — disabling is a UI affordance, not a data migration.
- **R4.5** `RangeProfileTable` gains an optional `disabled` prop that disables every input and the Add/Remove buttons.

### R5 — Calibration fits the model the controller runs (RC-3)

- **R5.1** The grey-box dynamics exist as **one** shared, dependency-light (numpy-only) forward simulator in `controller/mpc_model.py`, including the `sigma` radiative term and the `theta`/`n_delay` Erlang delay chain.
- **R5.2** `controller/update_mpc.py` uses that shared simulator. It no longer carries its own divergent copy of the dynamics.
- **R5.3** The fit estimates `K_Q, C_c, h_fc, h_amb, theta` (holding `C_f` fixed, as today, because it is redundant with `K_Q` for the steady gain). `sigma` and `n_delay` are inputs, defaulting to the controller's shipped defaults.
- **R5.4** The utility's `init` values are the controller's shipped `_DEFAULTS`, imported rather than duplicated.
- **R5.5** The utility reports **fit quality** (RMSE in °C and max absolute error) and emits the fitted parameters as a JSON object keyed exactly as `controller.config.mpc`, so the result can be applied without retyping.

### R6 — Surface an uncalibrated or under-horizoned model (RC-2)

- **R6.1** At MPC construction, if every physical parameter (`C_f, C_c, h_fc, h_amb, theta, n_delay, K_Q, sigma`) equals the shipped default, log a **WARNING** that the model is uncalibrated and name `update_mpc.py`.
- **R6.2** At MPC construction, if the prediction horizon `n_horizon * t_step` is shorter than the model's own chamber time constant `C_c / h_amb`, log a **WARNING** naming both numbers. This is self-consistent: with the incident's fitted parameters (`11465 / 2.75 = 4169 s` vs a 600 s horizon) it fires loudly.
- **R6.3** Both are warnings, not errors. They must not prevent the controller from starting.

### R7 — Preserve the applied-firing-rate history across a setpoint change (RC-4)

- **R7.1** `Controller.set_target()` no longer resets `self._last_Q` or `self._applied_Q`. It sets the target and nothing else.

### R8 — Re-capture and calibrate (operational)

- **R8.1** Documented, ordered procedure: land R1–R4 → enable `pwm_control` → confirm the fan modulates → re-capture a calibration log → run `update_mpc.py` → apply the fitted config → verify the overshoot.
- **R8.2** The procedure states explicitly that logs captured with the fan pinned at 100 % must be discarded.

---

## 4. Design decisions

**D1 — Do not auto-enable `pwm_control`.** The obvious "fix" is for the runtime to honour the MPC's fan command whenever `dc_fan` is true, ignoring `pwm_control`. Rejected: `pwm_control` is a user statement about their hardware wiring, and overriding it would drive a PWM signal on a build the user said is not PWM-driven. The correct resolution is to make the conflict *impossible to create* (R2) and *loud* when it already exists (R1.2).

**D2 — Drop the ownership claim rather than the command.** Given the conflict exists, `controls_fan = False` (R1.3) is strictly better than today: it re-enables the fallback paths. The alternative — keeping `controls_fan = True` and merely logging — leaves the fan dead.

**D3 — The Controller tab's error is MPC-specific but lives behind a shared predicate.** `ControllerTab` is a generic metadata-driven renderer, so the MPC special case is isolated in one helper (`helpers/settings/mpcFan.ts`) consumed by both tabs. No MPC knowledge leaks into the generic field-rendering loop.

**D4 — "Pending" is read from the draft store, not from a save.** `useSettingsDraft` already keeps each tab's unsaved edit on `SettingsShell` under a stable key (`"controller"`, `"pwm"`). PwmTab reads the `"controller"` draft through the same Outlet context to satisfy R4.1's "pending" clause. This is a read-only peek; neither tab writes the other's draft.

**D5 — One simulator, two consumers.** R5.1 puts the dynamics in `mpc_model.py` because that module already owns the model definition and is imported by both the controller and (newly) the fitter. Keeping the fitter's copy in sync by convention is what produced RC-3.

**D6 — Warnings, not gates, for calibration state.** R6 could refuse to start an uncalibrated MPC. Rejected: the shipped defaults are a legitimate starting point for a first cook, and a controller that refuses to run is worse than one that warns. The UI hard error in R2 is reserved for the case that is *unambiguously* broken (a lever wired to nothing).

## 5. Deliberately out of scope

- **`t_step` / `control_period` mismatch.** `do_mpc.make_step()` is called every `control_period` (5 s) while the MPC's own discretisation is `t_step` (25 s), so the move-suppression term `R_dQ` is applied on a different cadence than it is tuned for. Real, but it makes the controller *more* sluggish, not less — it is not a contributor to this overshoot, and changing it would invalidate the existing controller-matrix baselines.
- **Changing the shipped `_DEFAULTS`.** They are wrong for *this* grill; there is no evidence they are wrong in general. R6.1 surfaces the "never calibrated" state instead.
- **Auto-applying fitted parameters to the live database.** R5.5 emits paste-ready JSON; wiring a writer into `update_mpc.py` is a separate change with its own validation and safety story.
- **Re-tuning `n_horizon` / `t_step` defaults.** R6.2 makes the inadequacy visible; choosing new values needs the re-captured calibration data from R8.

## 6. Verification

| Requirement | How it is proven |
|---|---|
| R1.1–R1.4 | `tests/unit/runtime/` — Hold setup with `commands_fan=True` + `pwm_control=False` asserts `controls_fan is False` and that an ERROR was logged; the converse case asserts unchanged behaviour. |
| R2.1–R2.4 | `ControllerTab.test.tsx` — asserts the alert renders and `saveMock` is **not** called; AC-fan build asserts no alert. |
| R3.1–R3.2 | `PwmTab.test.tsx` — asserts the note renders, and that toggling `pwm_control` on in the draft clears it without a save. |
| R4.1–R4.5 | `PwmTab.test.tsx` (saved-on and draft-pending-on cases assert `disabled`; min/max/frequency assert **not** disabled) and `RangeProfileTable.test.tsx` (`disabled` prop). |
| R5.1–R5.5 | `tests/unit/mpc/test_mpc_calibration.py` — round-trip: simulate with known params through the shared simulator, fit, assert recovery within tolerance; assert a `sigma > 0` / `theta > 0` dataset is **not** recoverable by the old structure. |
| R6.1–R6.3 | `tests/unit/mpc/` — construct with defaults, assert the warning; construct with calibrated params, assert silence. |
| R7.1 | `tests/unit/mpc/test_mpc_controller.py` — `set_target()` then assert both `_last_Q` and `_applied_Q` are unchanged. |
| R8 | Manual, on the grill, after the above land. |
