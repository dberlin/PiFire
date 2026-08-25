# MPC Fan Authority and Calibration Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an MPC fan command that cannot reach the hardware impossible to configure and impossible to lose silently, and make MPC calibration fit the model the controller actually runs.

**Architecture:** One predicate (`controller_fan_authority`) decides whether a controller-issued fan duty can physically reach the fan; Hold consults it both when granting fan ownership and when applying a duty, and refuses to grant ownership it cannot honour. The settings UI blocks the conflicting combination on the Controller tab and greys out the PWM controls that ownership makes unreachable. Separately, the grey-box dynamics move into one shared numpy simulator in `controller/mpc_model.py` that both the controller's documentation-of-record and the offline fitter use, so a calibration run can no longer fit a different model than the one it is calibrating.

**Tech Stack:** Python 3.14 (numpy/scipy, pytest), React 19 + TypeScript (rstest, Testing Library, Biome).

**Spec:** `docs/superpowers/specs/2026-08-02-mpc-fan-authority-and-calibration-design.md`

## Global Constraints

- Python tests run as `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <path> -v`. A bare `python`/`pytest` gives false failures (PySide6 lives in the uv venv).
- Format Python with `.venv/bin/ruff format <changed files>` before every commit. Never `uvx ruff` — the repo pins ruff <0.16.
- `web-react` uses **bun**, never bare npm. Its gates are all three of `bun run typecheck`, `bun run test`, `bun run lint` (Biome + eslint).
- `web-react` tests are **rstest**, not vitest: import from `@rstest/core`, run with `bun run test` (never `bun test`).
- Do not change the shipped `_DEFAULTS` values in `controller/mpc.py`.
- Do not make the runtime override `pwm_control`; it is a user statement about hardware wiring.
- Source comments state what the code achieves. Never narrate the change, the incident, or measurements in a source comment.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `controller/runtime/logic/fan.py` | + `controller_fan_authority()` — the single "can a controller fan command reach the hardware?" predicate | 1 |
| `controller/runtime/modes/hold.py` | Consults the predicate at setup (ownership) and per-tick (apply) | 1 |
| `tests/unit/runtime/test_logic_fan.py` | Predicate unit tests | 1 |
| `tests/unit/runtime/test_hold_fan_authority.py` | Hold wiring tests | 1 |
| `controller/mpc_model.py` | + `simulate_grey_box()` — the one forward simulator, with `sigma` and the delay chain | 2 |
| `tests/unit/mpc/test_mpc_model.py` | Simulator behaviour tests | 2 |
| `controller/update_mpc.py` | Fitter rebuilt on the shared simulator; fits `theta`; reports fit quality; emits JSON | 3 |
| `tests/unit/mpc/test_mpc_calibration.py` | Round-trip recovery tests | 3 |
| `controller/mpc.py` | + uncalibrated / under-horizon warnings; `set_target` keeps `_last_Q` | 4, 5 |
| `tests/unit/mpc/test_mpc_controller.py` | Warning + `_last_Q` tests | 4, 5 |
| `web-react/src/components/settings/RangeProfileTable.tsx` | + `disabled` prop | 6 |
| `web-react/src/helpers/settings/mpcFan.ts` | **New.** Shared MPC-fan predicates for both tabs | 7 |
| `web-react/src/components/settings/tabs/ControllerTab.tsx` | Blocking error on the conflicting combination | 8 |
| `web-react/src/components/settings/tabs/PwmTab.tsx` | Informational note + disabling the unreachable controls | 9 |
| `docs/superpowers/audits/2026-08-02-mpc-recalibration-runbook.md` | **New.** Ordered re-capture/calibrate procedure | 10 |

## Parallelization

Three independent tracks. Within a track, tasks are strictly ordered.

- **Track A (Python runtime):** Task 1 → Task 4 → Task 5. Tasks 4 and 5 both edit `controller/mpc.py`, so they must not run concurrently with each other.
- **Track B (Python calibration):** Task 2 → Task 3.
- **Track C (web-react):** Task 6 ∥ Task 7, then Task 8 ∥ Task 9 (both depend on Task 7; Task 9 also depends on Task 6).
- **Task 10** depends on Tasks 1–3 and runs last.

Tracks A, B and C touch disjoint files and may run in concurrent isolated workspaces. Track A's Task 1 and Track B's Task 2 share no file. If you run concurrent agents, give each its own workspace — disjoint file lists alone are not sufficient isolation in this repo.

---

## Task 1: Fan authority predicate and Hold wiring

Implements R1.1–R1.4.

**Files:**
- Modify: `controller/runtime/logic/fan.py` (append after `clamp_duty`)
- Modify: `controller/runtime/modes/hold.py:6` (import), `:90-94` (ownership), `:180` (apply)
- Test: `tests/unit/runtime/test_logic_fan.py` (append)
- Test: `tests/unit/runtime/test_hold_fan_authority.py` (create)

**Interfaces:**
- Produces: `controller_fan_authority(settings: dict, control: dict) -> bool` in `controller.runtime.logic.fan`.
- Consumes: the existing `hold_cycle` fixture (`tests/unit/runtime/conftest.py`) and `FakeControllerRunner(period=..., commands_fan=...)` (`tests/fakes/runner.py`).

- [ ] **Step 1: Write the failing predicate test**

Append to `tests/unit/runtime/test_logic_fan.py`:

```python
from controller.runtime.logic.fan import controller_fan_authority


def _s(dc_fan):
    return {"platform": {"dc_fan": dc_fan}}


def test_authority_requires_both_a_dc_fan_and_pwm_control():
    assert controller_fan_authority(_s(True), {"pwm_control": True}) is True


def test_authority_is_denied_when_pwm_control_is_off():
    assert controller_fan_authority(_s(True), {"pwm_control": False}) is False


def test_authority_is_denied_on_an_ac_fan_build():
    assert controller_fan_authority(_s(False), {"pwm_control": True}) is False
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime/test_logic_fan.py -v`
Expected: FAIL — `ImportError: cannot import name 'controller_fan_authority'`

- [ ] **Step 3: Implement the predicate**

In `controller/runtime/logic/fan.py`, directly after `clamp_duty`:

```python
def controller_fan_authority(settings, control):
    """Whether a controller-issued fan duty can actually reach the hardware.

    A duty only ever lands on a DC-fan build with PWM control switched on. The
    setup-time ownership decision and the per-tick apply path both ask this one
    question so they cannot disagree: an ownership claim that the apply path
    then refuses leaves the fan driven by nobody, because the claim also
    suppresses the temperature-profile and fan-assist paths.
    """
    return bool(settings["platform"]["dc_fan"]) and bool(control["pwm_control"])
```

- [ ] **Step 4: Run the predicate test — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime/test_logic_fan.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing Hold wiring test**

Create `tests/unit/runtime/test_hold_fan_authority.py`:

```python
"""A controller may only own the fan when its command can reach the hardware."""

import logging

from tests.fakes.runner import FakeControllerRunner


def _grant(hold, *, dc_fan, pwm_control):
    hold.settings["platform"]["dc_fan"] = dc_fan
    hold.control["pwm_control"] = pwm_control


def test_ownership_is_granted_when_the_command_can_reach_the_fan(hold_cycle):
    runner = FakeControllerRunner(period=0.01, commands_fan=True)
    hold = hold_cycle(runner, controller="mpc")
    _grant(hold, dc_fan=True, pwm_control=True)
    hold.setup()
    assert hold.state.controller.controls_fan is True


def test_ownership_is_refused_when_pwm_control_is_off(hold_cycle):
    runner = FakeControllerRunner(period=0.01, commands_fan=True)
    hold = hold_cycle(runner, controller="mpc")
    _grant(hold, dc_fan=True, pwm_control=False)
    hold.setup()
    # False, not True: the claim would suppress the temp-profile and fan-assist
    # paths, leaving nothing at all able to move the fan.
    assert hold.state.controller.controls_fan is False


def test_refusing_ownership_logs_an_error_naming_the_controller(hold_cycle, caplog):
    runner = FakeControllerRunner(period=0.01, commands_fan=True)
    hold = hold_cycle(runner, controller="mpc")
    _grant(hold, dc_fan=True, pwm_control=False)
    with caplog.at_level(logging.ERROR):
        hold.setup()
    errors = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("mpc" in m and "PWM" in m for m in errors)


def test_a_controller_that_does_not_command_the_fan_logs_nothing(hold_cycle, caplog):
    runner = FakeControllerRunner(period=0.01, commands_fan=False)
    hold = hold_cycle(runner, controller="pid_sp")
    _grant(hold, dc_fan=True, pwm_control=False)
    with caplog.at_level(logging.ERROR):
        hold.setup()
    assert hold.state.controller.controls_fan is False
    assert [r for r in caplog.records if r.levelno == logging.ERROR] == []
```

- [ ] **Step 6: Run it to make sure it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime/test_hold_fan_authority.py -v`
Expected: `test_ownership_is_refused_when_pwm_control_is_off` and `test_refusing_ownership_logs_an_error_naming_the_controller` FAIL (today `controls_fan` is `True` and nothing is logged). The other two pass.

- [ ] **Step 7: Wire the predicate into Hold**

In `controller/runtime/modes/hold.py`, extend the import on line 6:

```python
from controller.runtime.logic.fan import (
    controller_fan_authority,
    fan_assist_times,
    smoke_plus_max_ratio,
    start_fan,
)
```

Replace the ownership assignment (lines 90-94: the `# Fan ownership is a setup-time capability...` comment block and the single `self.state.controller.controls_fan = ...` line that follows it) with:

```python
        # Fan ownership is a setup-time capability of the controller (e.g. MPC
        # with enable_fan_input), not a runtime latch -- this closes a startup
        # window where the temp-profile fan path could run before the
        # controller's first fan command.
        #
        # Ownership additionally requires that the controller's duty can reach
        # the fan. Granting it otherwise is strictly worse than withholding it:
        # the grant suppresses the temperature-profile and fan-assist paths
        # below, and the apply path then discards the duty, so nothing moves the
        # fan at all.
        wants_fan = self._runner.commands_fan() if self._runner is not None else False
        has_authority = controller_fan_authority(self.settings, self.control)
        if wants_fan and not has_authority:
            _control.eventLogger.error(
                f"Controller '{self.settings['controller']['selected']}' is configured to command "
                "the fan, but its duty cannot reach the hardware (PWM Control is off, or this is "
                "not a DC-fan build). Enable Settings > PWM Fan > PWM Control. Fan commands from "
                "the controller will be ignored; the non-controller fan paths stay active."
            )
        self.state.controller.controls_fan = wants_fan and has_authority
```

Replace the apply-path condition (line 180):

```python
            if fan_cmd is not None and controller_fan_authority(settings, control):
```

- [ ] **Step 8: Run both test files — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime/test_logic_fan.py tests/unit/runtime/test_hold_fan_authority.py -v`
Expected: PASS

- [ ] **Step 9: Run the surrounding suites for regressions**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime tests/characterization tests/e2e -q`
Expected: PASS. `tests/characterization/test_modes_golden.py` is the one most likely to notice a Hold behaviour change — if it fails, read the diff before touching the golden file: a changed `controls_fan` in a `pwm_control=False` fixture is the intended fix and the golden should be updated; anything else is a regression.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/logic/fan.py controller/runtime/modes/hold.py tests/unit/runtime/test_logic_fan.py tests/unit/runtime/test_hold_fan_authority.py
git add controller/runtime/logic/fan.py controller/runtime/modes/hold.py tests/unit/runtime/test_logic_fan.py tests/unit/runtime/test_hold_fan_authority.py
git commit -m "fix(control): refuse fan ownership a controller cannot exercise"
```

---

## Task 2: One shared grey-box forward simulator

Implements R5.1.

**Files:**
- Modify: `controller/mpc_model.py` (add after `_rad_loss`, before `build_do_mpc_model`)
- Test: `tests/unit/mpc/test_mpc_model.py` (append)

**Interfaces:**
- Produces: `simulate_grey_box(t, Q, *, C_f, C_c, h_fc, h_amb, T_amb, T0, K_Q=1.0, sigma=0.0, theta=0.0, n_delay=0, max_dt=1.0) -> np.ndarray`, returning chamber temperature aligned so `out[0] == T0` and `out[i]` is the state **at** `t[i]`. Task 3 consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/mpc/test_mpc_model.py`:

```python
import numpy as np

from controller.mpc_model import simulate_grey_box

_P = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.5, T_amb=20.0, K_Q=3.5)


def test_output_starts_at_t0_and_is_aligned_with_the_time_grid():
    t = np.arange(0.0, 60.0, 5.0)
    out = simulate_grey_box(t, np.full(t.shape, 50.0), T0=20.0, **_P)
    assert out.shape == t.shape
    assert out[0] == 20.0


def test_zero_firing_rate_decays_toward_ambient():
    t = np.arange(0.0, 3000.0, 5.0)
    out = simulate_grey_box(t, np.zeros(t.shape), T0=200.0, **_P)
    assert out[-1] < out[0]
    assert out[-1] > _P["T_amb"] - 1.0


def test_radiative_loss_lowers_the_trajectory():
    t = np.arange(0.0, 1200.0, 5.0)
    Q = np.full(t.shape, 100.0)
    linear = simulate_grey_box(t, Q, T0=20.0, sigma=0.0, **_P)
    radiative = simulate_grey_box(t, Q, T0=20.0, sigma=1.4e-9, **_P)
    assert radiative[-1] < linear[-1]


def test_transport_delay_postpones_the_response():
    t = np.arange(0.0, 600.0, 5.0)
    Q = np.full(t.shape, 100.0)
    prompt = simulate_grey_box(t, Q, T0=20.0, theta=0.0, n_delay=0, **_P)
    delayed = simulate_grey_box(t, Q, T0=20.0, theta=100.0, n_delay=4, **_P)
    # Delay only postpones; it removes no energy, so early samples lag and the
    # gap closes as the chain fills.
    assert delayed[10] < prompt[10]
    assert delayed[-1] < prompt[-1]


def test_substepping_keeps_a_coarse_log_grid_stable():
    # The firepot time constant C_f/h_fc is ~7 s, so a raw Euler step at the
    # 5 s log cadence is on the edge of divergence; sub-stepping must not be
    # sensitive to the sample spacing.
    t_coarse = np.arange(0.0, 600.0, 5.0)
    t_fine = np.arange(0.0, 600.0, 1.0)
    coarse = simulate_grey_box(t_coarse, np.full(t_coarse.shape, 100.0), T0=20.0, **_P)
    fine = simulate_grey_box(t_fine, np.full(t_fine.shape, 100.0), T0=20.0, **_P)
    assert abs(coarse[-1] - fine[-1]) < 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'simulate_grey_box'`

- [ ] **Step 3: Implement the simulator**

In `controller/mpc_model.py`, after `_rad_loss` and before `build_do_mpc_model`:

```python
def simulate_grey_box(
    t,
    Q,
    *,
    C_f,
    C_c,
    h_fc,
    h_amb,
    T_amb,
    T0,
    K_Q=1.0,
    sigma=0.0,
    theta=0.0,
    n_delay=0,
    max_dt=1.0,
):
    """Forward-simulate chamber temperature for the plant the MPC plans against.

    The single executable statement of the dynamics documented at the top of
    this module, including the radiative loss and the Erlang transport-delay
    chain. The offline calibration utility fits through this function so the
    parameters it produces describe the model that consumes them.

    `out[i]` is the chamber temperature AT `t[i]`, so `out[0] == T0`; each step
    advances the state from `t[i]` to `t[i+1]` under the input `Q[i]`. The
    disturbance state `d` is absent: it exists to absorb model error at run
    time, and fitting against it would let it absorb the very mismatch a
    calibration exists to remove.
    """
    t = np.asarray(t, dtype=float)
    Q = np.asarray(Q, dtype=float)
    n = max(int(n_delay), 0)
    lag_tau = (float(theta) / n) if (n > 0 and theta > 0.0) else 0.0
    lags = np.zeros(n)
    T_f = float(T0)
    T_c = float(T0)
    out = np.empty_like(t)
    for i in range(len(t)):
        out[i] = T_c
        if i == len(t) - 1:
            break
        span = float(t[i + 1] - t[i])
        if span <= 0.0:
            continue
        # Sub-step: C_f/h_fc is a handful of seconds, well under a typical log
        # cadence, so integrating at the sample spacing alone diverges.
        steps = max(1, int(np.ceil(span / max_dt)))
        dt = span / steps
        u = float(Q[i])
        for _ in range(steps):
            if lag_tau > 0.0:
                prev = u
                for j in range(n):
                    lags[j] += dt * (prev - lags[j]) / lag_tau
                    prev = lags[j]
                heat_in = lags[-1]
            else:
                heat_in = u
            dT_f = (K_Q * heat_in - h_fc * (T_f - T_c)) / C_f
            dT_c = (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c
            T_f += dt * dT_f
            T_c += dt * dT_c
    return out
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_model.py -v`
Expected: PASS

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/mpc_model.py tests/unit/mpc/test_mpc_model.py
git add controller/mpc_model.py tests/unit/mpc/test_mpc_model.py
git commit -m "feat(mpc): add the shared grey-box forward simulator"
```

---

## Task 3: Rebuild the calibration utility on the shared simulator

Implements R5.2–R5.5. Depends on Task 2.

**Files:**
- Modify: `controller/update_mpc.py` (replace `simulate_chamber`, `fit_params`, `main`)
- Test: `tests/unit/mpc/test_mpc_calibration.py` (replace tests referencing `simulate_chamber`)

**Interfaces:**
- Consumes: `controller.mpc_model.simulate_grey_box` (Task 2), `controller.mpc._DEFAULTS`.
- Produces: `fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0) -> dict` with keys `K_Q, C_c, h_fc, h_amb, theta, C_f, sigma, n_delay, T_amb`; and `fit_quality(t, temp, Q, fitted, *, T_amb) -> tuple[float, float]` returning `(rmse, max_abs_error)` in °C.

- [ ] **Step 1: Write the failing round-trip tests**

Replace the contents of `tests/unit/mpc/test_mpc_calibration.py` with:

```python
"""The fitter must recover parameters through the same dynamics the MPC uses."""

import numpy as np
import pytest

from controller.mpc_model import simulate_grey_box
from controller.update_mpc import fit_params, fit_quality

TRUTH = dict(C_f=9.0, C_c=11000.0, h_fc=1.3, h_amb=2.7, K_Q=32.0, theta=110.0)
T_AMB = 20.0
N_DELAY = 4
SIGMA = 1.4e-9


def _dataset():
    """A heat-up to a plateau then a step down -- enough excitation to identify
    the gain, the loss and the deadtime."""
    t = np.arange(0.0, 6000.0, 5.0)
    Q = np.where(t < 3000.0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=T_AMB, sigma=SIGMA, n_delay=N_DELAY, **TRUTH)
    return t, Q, temp


def test_fit_recovers_the_generating_parameters():
    t, Q, temp = _dataset()
    init = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.5, K_Q=3.5, theta=50.0)
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=SIGMA, n_delay=N_DELAY)
    # C_c and K_Q are recovered as a ratio with h_amb, so compare the quantity
    # the controller's braking distance actually depends on: the time constant.
    assert fitted["C_c"] / fitted["h_amb"] == pytest.approx(TRUTH["C_c"] / TRUTH["h_amb"], rel=0.20)
    assert fitted["theta"] == pytest.approx(TRUTH["theta"], rel=0.30)


def test_fit_quality_is_reported_and_is_tight_on_its_own_data():
    t, Q, temp = _dataset()
    init = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.5, K_Q=3.5, theta=50.0)
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=SIGMA, n_delay=N_DELAY)
    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_AMB)
    assert rmse < 2.0
    assert max_err < 10.0


def test_the_fitted_dict_carries_every_key_the_controller_config_needs():
    t, Q, temp = _dataset()
    init = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.5, K_Q=3.5, theta=50.0)
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=SIGMA, n_delay=N_DELAY)
    for key in ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma"):
        assert key in fitted


def test_a_deadtime_dataset_is_not_explained_by_a_zero_deadtime_structure():
    """The negative control for the defect this replaces: the old fitter had no
    delay chain and no radiative term, so it could only absorb them into the
    capacitances."""
    t, Q, temp = _dataset()
    init = dict(C_f=9.0, C_c=320.0, h_fc=1.3, h_amb=0.5, K_Q=3.5, theta=50.0)
    crippled = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=0.0, n_delay=0)
    rmse, _ = fit_quality(t, temp, Q, crippled, T_amb=T_AMB)
    assert rmse > 2.0
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_calibration.py -v`
Expected: FAIL — `ImportError: cannot import name 'fit_quality'` (and `fit_params` does not accept `sigma`/`n_delay`).

- [ ] **Step 3: Rewrite the utility**

Replace `controller/update_mpc.py` in full:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Offline Calibration Utility
*****************************************

 Fits the grey-box thermal parameters to a logged history CSV so the MPC model
 describes a specific grill. Fitting runs through controller.mpc_model's shared
 forward simulator, so the parameters produced describe the same dynamics the
 controller plans against -- radiative loss and transport deadtime included.

 CSV columns: time_s, temp_c, Q  (Q is the firing-rate demand; if you logged
 auger duty instead, map it back through the allocator first). Capture the log
 with the fan under the controller's command: a log taken with the fan pinned
 at one duty only describes the grill at that duty.

 Usage: python -m controller.update_mpc history.csv [--t-amb 20] [--json]
*****************************************
"""

import argparse
import json

import numpy as np
from scipy.optimize import least_squares

from controller.mpc_model import simulate_grey_box

# Keys the controller reads back out of a fitted result.
CONFIG_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

# Fitted free parameters. C_f is held at its init value: it is redundant with
# K_Q for the steady gain, so fitting both is ill-posed.
_FREE = ("K_Q", "C_c", "h_fc", "h_amb", "theta")


def _sim_kwargs(params):
    return {k: params[k] for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "sigma", "theta", "n_delay")}


def fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0):
    """Fit the free grey-box parameters to a logged temperature series."""
    temp = np.asarray(temp, dtype=float)
    C_f = float(init["C_f"])
    x0 = np.array([float(init[k]) for k in _FREE], dtype=float)

    def residual(x):
        params = dict(zip(_FREE, x))
        params.update(C_f=C_f, sigma=sigma, n_delay=n_delay)
        return simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params)) - temp

    # Strictly positive: theta divides the lag time constant, and every other
    # free parameter is a capacitance or a conductance.
    res = least_squares(residual, x0, method="trf", bounds=(1e-9, np.inf), max_nfev=2000)
    out = dict(zip(_FREE, (float(v) for v in res.x)))
    out.update(C_f=C_f, sigma=float(sigma), n_delay=int(n_delay), T_amb=float(T_amb))
    return out


def fit_quality(t, temp, Q, fitted, *, T_amb):
    """(RMSE, max absolute error) in degrees C between the fit and the log."""
    temp = np.asarray(temp, dtype=float)
    params = dict(fitted)
    params["T_amb"] = T_amb
    sim = simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params))
    err = sim - temp
    return float(np.sqrt(np.mean(err**2))), float(np.max(np.abs(err)))


def main():
    ap = argparse.ArgumentParser(description="Fit MPC grey-box parameters to a calibration log.")
    ap.add_argument("csv")
    ap.add_argument("--t-amb", type=float, default=None, help="Ambient temperature in C")
    ap.add_argument("--json", action="store_true", help="Print only the fitted config JSON")
    args = ap.parse_args()

    import pandas as pd

    from controller.mpc import _DEFAULTS

    df = pd.read_csv(args.csv)
    t = df["time_s"].values
    temp = df["temp_c"].values
    Q = df["Q"].values

    T_amb = args.t_amb if args.t_amb is not None else float(_DEFAULTS["T_amb"])
    init = {k: float(_DEFAULTS[k]) for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "theta")}
    fitted = fit_params(
        t,
        temp,
        Q,
        T_amb=T_amb,
        init=init,
        sigma=float(_DEFAULTS["sigma"]),
        n_delay=int(_DEFAULTS["n_delay"]),
    )
    payload = {k: fitted[k] for k in CONFIG_KEYS}

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
    print(f"Fit quality: RMSE {rmse:.2f} C, max error {max_err:.2f} C")
    if rmse > 10.0:
        print(
            "WARNING: RMSE above 10 C. This fit does not describe the log. Check that the log\n"
            "         covers a full heat-up and at least one step down, and that the fan was\n"
            "         under the controller's command throughout."
        )
    horizon = float(_DEFAULTS["n_horizon"]) * float(_DEFAULTS["t_step"])
    tau = payload["C_c"] / payload["h_amb"]
    if horizon < tau:
        print(
            f"WARNING: fitted chamber time constant is {tau:.0f} s but the default prediction\n"
            f"         horizon is only {horizon:.0f} s. Raise n_horizon or t_step, or the\n"
            "         controller cannot see far enough ahead to stop in time."
        )
    print("\nPaste into Settings > Controller (controller.config.mpc):")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_calibration.py -v`
Expected: PASS. If `test_fit_recovers_the_generating_parameters` fails on tolerance, do **not** widen the tolerance first — check `test_fit_quality_is_reported_and_is_tight_on_its_own_data`. A tight RMSE with unrecovered parameters means the dataset lacks the excitation to identify them, so fix `_dataset()` (add a second step) rather than the assertion.

- [ ] **Step 5: Run the whole MPC suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc -q`
Expected: PASS

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/update_mpc.py tests/unit/mpc/test_mpc_calibration.py
git add controller/update_mpc.py tests/unit/mpc/test_mpc_calibration.py
git commit -m "fix(mpc): fit calibration through the model the controller runs"
```

---

## Task 4: Warn on an uncalibrated or under-horizoned model

Implements R6.1–R6.3. Touches `controller/mpc.py` — must not run concurrently with Task 5.

**Files:**
- Modify: `controller/mpc.py` (add `_warn_about_model`, call it from `__init__`)
- Test: `tests/unit/mpc/test_mpc_controller.py` (append)

**Interfaces:**
- Consumes: `_DEFAULTS` (module-level, already present).
- Produces: `_warn_about_model(cfg) -> None`, printing to stdout. `mpc.py` already reports policy fallbacks with bare `print("[mpc] ...")` (`mpc.py:119, 124, 127`) and `control.py` captures stdout into the control log, so this matches the module's existing reporting channel.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/mpc/test_mpc_controller.py`:

```python
from controller.mpc import _DEFAULTS, _warn_about_model

_PHYSICAL = ("C_f", "C_c", "h_fc", "h_amb", "theta", "n_delay", "K_Q", "sigma")


def test_shipped_defaults_are_reported_as_uncalibrated(capsys):
    _warn_about_model(dict(_DEFAULTS))
    out = capsys.readouterr().out
    assert "uncalibrated" in out.lower()
    assert "update_mpc" in out


def test_calibrated_params_are_not_reported_as_uncalibrated(capsys):
    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=32.0, theta=110.0, n_horizon=200)
    _warn_about_model(cfg)
    assert "uncalibrated" not in capsys.readouterr().out.lower()


def test_a_horizon_shorter_than_the_chamber_time_constant_is_reported(capsys):
    cfg = dict(_DEFAULTS)
    # 11000/2.7 = 4074 s time constant against a 24*25 = 600 s horizon.
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=32.0, theta=110.0)
    _warn_about_model(cfg)
    out = capsys.readouterr().out
    assert "horizon" in out.lower()
    assert "600" in out


def test_an_adequate_horizon_is_not_reported(capsys):
    cfg = dict(_DEFAULTS)
    cfg.update(C_c=11000.0, h_amb=2.7, K_Q=32.0, theta=110.0, n_horizon=200, t_step=25.0)
    _warn_about_model(cfg)
    assert "horizon" not in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_controller.py -v -k warn or horizon or uncalibrated`
Expected: FAIL — `ImportError: cannot import name '_warn_about_model'`

- [ ] **Step 3: Implement the warnings**

In `controller/mpc.py`, between the end of `_load_net_policy` and the `def requires_modules(config):` on line 132:

```python
_PHYSICAL_PARAMS = ("C_f", "C_c", "h_fc", "h_amb", "theta", "n_delay", "K_Q", "sigma")


def _warn_about_model(cfg):
    """Report a model that cannot govern this grill well.

    Both conditions are advisory: the shipped parameters are a legitimate
    starting point for a first cook, and a controller that refuses to run is
    worse than one that says what is wrong.
    """
    if all(cfg.get(k) == _DEFAULTS[k] for k in _PHYSICAL_PARAMS):
        print(
            "[mpc] model is uncalibrated (every thermal parameter is still the shipped default). "
            "Expect large overshoot until you fit this grill with controller/update_mpc.py."
        )
    h_amb = float(cfg.get("h_amb") or 0.0)
    if h_amb > 0.0:
        tau = float(cfg["C_c"]) / h_amb
        horizon = float(cfg["n_horizon"]) * float(cfg["t_step"])
        if horizon < tau:
            print(
                f"[mpc] prediction horizon is {horizon:.0f} s but the model's chamber time "
                f"constant is {tau:.0f} s; the controller cannot see far enough ahead to stop "
                "in time. Raise n_horizon or t_step."
            )
```

In `Controller.__init__`, immediately after `self.cfg = cfg` (line 165):

```python
        _warn_about_model(cfg)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_controller.py -v`
Expected: PASS

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_controller.py
git add controller/mpc.py tests/unit/mpc/test_mpc_controller.py
git commit -m "feat(mpc): report an uncalibrated or under-horizoned model at startup"
```

---

## Task 5: Preserve the applied firing rate across a setpoint change

Implements R7.1. Touches `controller/mpc.py` — run after Task 4.

**Files:**
- Modify: `controller/mpc.py:303-307` (`set_target`)
- Test: `tests/unit/mpc/test_mpc_controller.py` (append)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/mpc/test_mpc_controller.py`. `CONFIG` and `CYCLE` are the module-level constants that file's other tests already build controllers from (`Controller(dict(CONFIG), "C", dict(CYCLE))`).

```python
def test_set_target_keeps_the_applied_firing_rate_history():
    """_applied_Q is the rate the grill actually ran at (recovered by
    set_output) and is what the estimator is given as its known input;
    _last_Q is the last command, held over on a solve failure. Both describe
    the grill, not the target, so a new setpoint must not rewrite either."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c._last_Q = 87.5
    c._applied_Q = 84.0
    c.set_target(300)
    assert c._last_Q == 87.5
    assert c._applied_Q == 84.0


def test_set_target_still_updates_the_target():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(300)
    assert c.set_point == 300
    assert c._set_point_c == 300  # units are "C" here, so no conversion
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_controller.py -v -k set_target`
Expected: `test_set_target_keeps_the_applied_firing_rate_history` FAILS with `assert 5.0 == 87.5`; `test_set_target_still_updates_the_target` passes.

- [ ] **Step 3: Remove the resets**

In `controller/mpc.py`, `set_target` becomes:

```python
    def set_target(self, set_point):
        self.set_point = set_point
        self._set_point_c = _to_c(set_point, self.units)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc -q`
Expected: PASS

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_controller.py
git add controller/mpc.py tests/unit/mpc/test_mpc_controller.py
git commit -m "fix(mpc): keep the applied firing rate across a setpoint change"
```

---

## Task 6: `RangeProfileTable` gains a `disabled` prop

Implements R4.5.

**Files:**
- Modify: `web-react/src/components/settings/RangeProfileTable.tsx`
- Test: `web-react/tests/unit/components/settings/RangeProfileTable.test.tsx` (append)

**Interfaces:**
- Produces: `RangeProfileTable` accepts an optional `disabled?: boolean`. Task 9 consumes it.

- [ ] **Step 1: Write the failing test**

Append to `web-react/tests/unit/components/settings/RangeProfileTable.test.tsx`:

```tsx
it("disables every input and both buttons when disabled", () => {
  render(
    <RangeProfileTable
      boundaries={[3, 7]}
      profiles={[{ duty_cycle: 20 }, { duty_cycle: 50 }, { duty_cycle: 100 }]}
      columns={[{ key: "duty_cycle", label: "Duty cycle", suffix: "%", min: 1, max: 100 }]}
      rangeHeader="ΔT range"
      unit="°F"
      onChange={() => {}}
      disabled
    />,
  );
  for (const input of screen.getAllByRole("spinbutton")) {
    expect(input).toBeDisabled();
  }
  expect(screen.getByRole("button", { name: "+ Add" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Remove row 1" })).toBeDisabled();
});
```

The file already imports `{ fireEvent, render, screen }` from `@testing-library/react` and `{ type RangeProfileColumn, RangeProfileTable }` from the component, so no import changes are needed. It renders the component with a bare `render` (no router), which is what the test above does.

- [ ] **Step 2: Run to verify failure**

Run: `cd web-react && bun run test RangeProfileTable`
Expected: FAIL — inputs are not disabled.

- [ ] **Step 3: Thread the prop through**

In `RangeProfileTable.tsx`, add `disabled` to both the destructured params and the type:

```tsx
export function RangeProfileTable({
  boundaries,
  profiles,
  columns,
  rangeHeader,
  unit,
  onChange,
  boundaryMin,
  boundaryMax,
  disabled = false,
}: {
  boundaries: number[];
  profiles: Record<string, number>[];
  columns: RangeProfileColumn[];
  rangeHeader: string;
  unit: string;
  onChange: (boundaries: number[], profiles: Record<string, number>[]) => void;
  boundaryMin?: number;
  boundaryMax?: number;
  /** Render read-only. The rows still show their values: the settings they
   *  describe are unreachable, not unset. */
  disabled?: boolean;
}) {
```

Change `canRemove`:

```tsx
  const canRemove = !disabled && profiles.length > 2;
```

Add `disabled={disabled}` to the boundary input, the cell input, and the Add button:

```tsx
                      <input
                        type="number"
                        className="pf-input pf-rpt-boundary-input"
                        aria-label={`Boundary ${rowIndex + 1}`}
                        min={lower}
                        max={upper}
                        disabled={disabled}
                        value={draft?.index === rowIndex ? draft.text : boundaries[rowIndex]}
                        onChange={(e) => handleBoundaryChange(rowIndex, e.target.value)}
                        onBlur={() => handleBoundaryBlur(rowIndex)}
                      />
```

```tsx
                  <input
                    type="number"
                    className="pf-input"
                    aria-label={`${col.label} row ${rowIndex + 1}`}
                    min={col.min}
                    max={col.max}
                    disabled={disabled}
                    value={row[col.key] ?? 0}
                    onChange={(e) => handleCellChange(rowIndex, col, e.target.value)}
                  />
```

```tsx
      <button type="button" className="pf-rpt-add" disabled={disabled} onClick={handleAdd}>
        + Add
      </button>
```

The Remove button already reads `disabled={!canRemove}`, so the `canRemove` change covers it.

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd web-react && bun run test RangeProfileTable`
Expected: PASS

- [ ] **Step 5: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
cd .. && git add web-react/src/components/settings/RangeProfileTable.tsx web-react/tests/unit/components/settings/RangeProfileTable.test.tsx
git commit -m "feat(web): let RangeProfileTable render disabled"
```

---

## Task 7: Shared MPC-fan predicates for the settings tabs

Implements D3. Both Task 8 and Task 9 depend on this.

**Files:**
- Create: `web-react/src/helpers/settings/mpcFan.ts`
- Test: `web-react/tests/unit/helpers/settings/mpcFan.test.ts` (create; match the directory the other helper tests use)

**Interfaces:**
- Produces:
  - `mpcFanPending(settings: Settings, drafts: SettingsDrafts): boolean` — is MPC selected *and* its `enable_fan_input` on, counting an unsaved Controller-tab draft ahead of saved settings?
  - `mpcFanConflict(args: { selected: string; enableFanInput: boolean; settings: Settings }): boolean` — is the selected controller `mpc` with fan input on, on a DC-fan build whose saved `pwm.pwm_control` is off?
  - `MPC_FAN_CONFLICT_MESSAGE: string`, `MPC_FAN_PWM_NOTE: string`, `MPC_FAN_DISABLED_NOTE: string`.

- [ ] **Step 1: Write the failing tests**

Create `web-react/tests/unit/helpers/settings/mpcFan.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import type { Settings } from "../../../../src/helpers/settings/settingsApi";
import { mpcFanConflict, mpcFanPending } from "../../../../src/helpers/settings/mpcFan";

const dcFan = (pwmControl: boolean, fanInput: boolean): Settings =>
  ({
    platform: { dc_fan: true },
    pwm: { pwm_control: pwmControl },
    controller: { selected: "mpc", config: { mpc: { enable_fan_input: fanInput } } },
  }) as unknown as Settings;

describe("mpcFanConflict", () => {
  it("fires when MPC owns the fan but PWM control is off", () => {
    expect(
      mpcFanConflict({ selected: "mpc", enableFanInput: true, settings: dcFan(false, true) }),
    ).toBe(true);
  });

  it("does not fire when PWM control is on", () => {
    expect(
      mpcFanConflict({ selected: "mpc", enableFanInput: true, settings: dcFan(true, true) }),
    ).toBe(false);
  });

  it("does not fire when MPC is not commanding the fan", () => {
    expect(
      mpcFanConflict({ selected: "mpc", enableFanInput: false, settings: dcFan(false, false) }),
    ).toBe(false);
  });

  it("does not fire for another controller", () => {
    expect(
      mpcFanConflict({ selected: "pid", enableFanInput: true, settings: dcFan(false, true) }),
    ).toBe(false);
  });

  it("does not fire on an AC-fan build", () => {
    const ac = { ...dcFan(false, true), platform: { dc_fan: false } } as unknown as Settings;
    expect(mpcFanConflict({ selected: "mpc", enableFanInput: true, settings: ac })).toBe(false);
  });
});

describe("mpcFanPending", () => {
  it("reads saved settings when there is no draft", () => {
    expect(mpcFanPending(dcFan(true, true), {})).toBe(true);
    expect(mpcFanPending(dcFan(true, false), {})).toBe(false);
  });

  it("prefers an unsaved controller draft over saved settings", () => {
    const drafts = {
      controller: {
        value: { selected: "mpc", values: { enable_fan_input: true } },
        saved: false,
      },
    };
    expect(mpcFanPending(dcFan(true, false), drafts)).toBe(true);
  });

  it("honours a draft that turns fan control off", () => {
    const drafts = {
      controller: {
        value: { selected: "mpc", values: { enable_fan_input: false } },
        saved: false,
      },
    };
    expect(mpcFanPending(dcFan(true, true), drafts)).toBe(false);
  });

  it("is false when the draft selects another controller", () => {
    const drafts = {
      controller: { value: { selected: "pid", values: {} }, saved: false },
    };
    expect(mpcFanPending(dcFan(true, true), drafts)).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web-react && bun run test mpcFan`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the helper**

Create `web-react/src/helpers/settings/mpcFan.ts`:

```ts
import type { SettingsDrafts } from "./settingsDrafts";
import type { Settings } from "./settingsApi";
import { hasDcFan } from "./platform";

/** The controller key whose allocator emits a fan duty. */
const MPC = "mpc";

export const MPC_FAN_CONFLICT_MESSAGE =
  "MPC Controls Fan is on, but PWM Control is off on the PWM Fan tab. The controller's fan " +
  "commands cannot reach the fan, so it would run at a fixed speed for the whole cook. Enable " +
  "PWM Control on the PWM Fan tab, or turn MPC Controls Fan off.";

export const MPC_FAN_PWM_NOTE =
  "The MPC controller is set to command the fan, but PWM Control is off — its fan commands will " +
  "not be applied. Turn PWM Control on to give the controller the fan.";

export const MPC_FAN_DISABLED_NOTE =
  "The MPC controller commands the fan directly, so the duty-cycle-from-temperature profile below " +
  "is never used. Its values are kept, and it becomes editable again if you turn off MPC " +
  "Controls Fan on the Controller tab.";

type ControllerDraft = { selected?: unknown; values?: Record<string, unknown> };

/**
 * Is the MPC set to command the fan, counting an unsaved Controller-tab edit?
 *
 * The PWM tab has to answer this for a choice the user has made but not yet
 * saved, so the draft store is consulted first and saved settings are the
 * fallback. Read-only: the PWM tab never writes the Controller tab's draft.
 */
export function mpcFanPending(settings: Settings, drafts: SettingsDrafts): boolean {
  const draft = drafts.controller?.value as ControllerDraft | undefined;
  if (draft && typeof draft.selected === "string") {
    return draft.selected === MPC && !!draft.values?.enable_fan_input;
  }
  return (
    settings.controller?.selected === MPC &&
    !!settings.controller?.config?.[MPC]?.enable_fan_input
  );
}

/**
 * A configuration whose fan lever is wired to nothing: the MPC is set to
 * command the fan on a DC-fan build whose PWM control is switched off. On an
 * AC-fan build there is no PWM fan to command, so the option is simply
 * inapplicable rather than broken.
 */
export function mpcFanConflict({
  selected,
  enableFanInput,
  settings,
}: {
  selected: string;
  enableFanInput: boolean;
  settings: Settings;
}): boolean {
  if (selected !== MPC || !enableFanInput) return false;
  if (!hasDcFan(settings)) return false;
  return !settings.pwm?.pwm_control;
}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd web-react && bun run test mpcFan`
Expected: PASS

- [ ] **Step 5: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
cd .. && git add web-react/src/helpers/settings/mpcFan.ts web-react/tests/unit/helpers/settings/mpcFan.test.ts
git commit -m "feat(web): add the shared MPC fan-authority predicates"
```

---

## Task 8: Controller tab blocks the conflicting combination

Implements R2.1–R2.4. Depends on Task 7.

**Files:**
- Modify: `web-react/src/components/settings/tabs/ControllerTab.tsx`
- Test: `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx` (append)

**Interfaces:**
- Consumes: `mpcFanConflict`, `MPC_FAN_CONFLICT_MESSAGE` (Task 7).

- [ ] **Step 1: Write the failing tests**

Append to `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx`. Add an `mpc` entry to the shared `controllerMeta` object at the top of the file:

```tsx
    mpc: {
      friendly_name: "Model Predictive Control (MPC)",
      description: "Model Predictive Controller.",
      config: [
        {
          option_name: "enable_fan_input",
          option_friendly_name: "MPC Controls Fan",
          option_description: "Let the controller command fan duty.",
          option_type: "bool",
          option_default: false,
          option_min: null,
          option_max: null,
        },
      ],
    },
```

Then append:

```tsx
const mpcContext = (pwmControl: boolean, dcFan = true) => ({
  settings: {
    platform: { dc_fan: dcFan },
    pwm: { pwm_control: pwmControl },
    controller: { selected: "mpc", config: { mpc: { enable_fan_input: true } } },
  },
  mode: "Stop",
  controllerMeta,
});

describe("ControllerTab MPC fan authority", () => {
  it("shows a blocking error when MPC owns the fan but PWM control is off", () => {
    renderRoute(<ControllerTab />, mpcContext(false));
    expect(screen.getByRole("alert")).toHaveTextContent(/PWM Control is off/i);
  });

  it("refuses to save while the conflict stands", () => {
    renderRoute(<ControllerTab />, mpcContext(false));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("has no error and saves normally when PWM control is on", () => {
    renderRoute(<ControllerTab />, mpcContext(true));
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(saveMock).toHaveBeenCalled();
  });

  it("does not fire on an AC-fan build", () => {
    renderRoute(<ControllerTab />, mpcContext(false, false));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("clears once MPC Controls Fan is toggled off", () => {
    renderRoute(<ControllerTab />, mpcContext(false));
    fireEvent.click(screen.getByRole("button", { name: "MPC Controls Fan" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web-react && bun run test ControllerTab`
Expected: FAIL — no `alert` is rendered and `saveMock` is called.

- [ ] **Step 3: Implement the guard**

In `ControllerTab.tsx`, add the import:

```tsx
import { MPC_FAN_CONFLICT_MESSAGE, mpcFanConflict } from "../../../helpers/settings/mpcFan";
```

After `const set = (name: string, ...)` and before `onSave`, derive the conflict from the tab's *current* values:

```tsx
  // Derived from the draft, so the error appears the moment the toggle is
  // flipped rather than after a save that would have been silently useless.
  const fanConflict = mpcFanConflict({
    selected,
    enableFanInput: !!values.enable_fan_input,
    settings,
  });
```

Make `onSave` refuse, mirroring `PwmTab`'s pre-flight pattern — add this as the first statement in `onSave`:

```tsx
    if (fanConflict) return; // do NOT call save(): the fan lever would be wired to nothing
```

Render the message immediately above `<SaveBar ...>`:

```tsx
      {fanConflict && (
        <p className="pf-settings-error-text" role="alert">
          {MPC_FAN_CONFLICT_MESSAGE}
        </p>
      )}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd web-react && bun run test ControllerTab`
Expected: PASS

- [ ] **Step 5: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
cd .. && git add web-react/src/components/settings/tabs/ControllerTab.tsx web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx
git commit -m "feat(web): block saving an MPC fan command that cannot reach the fan"
```

---

## Task 9: PWM tab note and disabled unreachable controls

Implements R3.1–R3.2 and R4.1–R4.4. Depends on Tasks 6 and 7.

**Files:**
- Modify: `web-react/src/components/settings/tabs/PwmTab.tsx`
- Test: `web-react/tests/unit/components/settings/tabs/PwmTab.test.tsx` (append)

**Interfaces:**
- Consumes: `mpcFanPending`, `MPC_FAN_PWM_NOTE`, `MPC_FAN_DISABLED_NOTE` (Task 7); `RangeProfileTable`'s `disabled` prop (Task 6); `SettingsDraftContext` from `settingsDrafts`.

- [ ] **Step 1: Write the failing tests**

Append to `web-react/tests/unit/components/settings/tabs/PwmTab.test.tsx`:

```tsx
const mpcFanSettings = (pwmControl: boolean, enableFanInput: boolean) => ({
  platform: { dc_fan: true },
  pwm: {
    pwm_control: pwmControl,
    update_time: 10,
    min_duty_cycle: 20,
    max_duty_cycle: 100,
    frequency: 25000,
  },
  controller: { selected: "mpc", config: { mpc: { enable_fan_input: enableFanInput } } },
});

describe("PwmTab MPC fan interaction", () => {
  it("notes that MPC fan commands are inert while PWM control is off", () => {
    renderRoute(<PwmTab />, { settings: mpcFanSettings(false, true), mode: "Stop" });
    expect(screen.getByText(/fan commands will not be applied/i)).toBeInTheDocument();
  });

  it("drops the note as soon as PWM control is toggled on, without saving", () => {
    renderRoute(<PwmTab />, { settings: mpcFanSettings(false, true), mode: "Stop" });
    fireEvent.click(screen.getByRole("button", { name: "PWM Control" }));
    expect(screen.queryByText(/fan commands will not be applied/i)).toBeNull();
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("disables the update time and the profile table when MPC owns the fan", () => {
    renderRoute(<PwmTab />, { settings: mpcFanSettings(true, true), mode: "Stop" });
    expect(screen.getByDisplayValue("10")).toBeDisabled();
    expect(screen.getByRole("button", { name: "+ Add" })).toBeDisabled();
    expect(screen.getByText(/never used/i)).toBeInTheDocument();
  });

  it("leaves min, max and frequency editable — other paths still read them", () => {
    renderRoute(<PwmTab />, { settings: mpcFanSettings(true, true), mode: "Stop" });
    expect(screen.getByDisplayValue("20")).not.toBeDisabled();
    expect(screen.getByDisplayValue("100")).not.toBeDisabled();
    expect(screen.getByDisplayValue("25000")).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "PWM Control" })).not.toBeDisabled();
  });

  it("disables them for a pending, unsaved controller draft too", () => {
    renderRoute(
      <PwmTab />,
      { settings: mpcFanSettings(true, false), mode: "Stop" },
      {
        drafts: {
          controller: {
            value: { selected: "mpc", values: { enable_fan_input: true } },
            saved: false,
          },
        },
      },
    );
    expect(screen.getByDisplayValue("10")).toBeDisabled();
  });

  it("keeps them editable when MPC does not command the fan", () => {
    renderRoute(<PwmTab />, { settings: mpcFanSettings(true, false), mode: "Stop" });
    expect(screen.getByDisplayValue("10")).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "+ Add" })).not.toBeDisabled();
  });
});
```

- [ ] **Step 1a: Let `renderRoute` seed another tab's draft**

The fifth test above passes a third argument, which `renderRoute` does not yet accept — only one settings tab is ever mounted, so seeding the store is the only way to observe a cross-tab dependency.

In `web-react/tests/unit/test-utils.tsx`, change the signature and the `Outlet` context (currently lines 12-22):

```tsx
export function renderRoute(ui: ReactElement, context: unknown, overrides?: object) {
  // Stands in for SettingsShell: it owns the draft store a settings tab writes
  // its in-progress edits into (helpers/settings/settingsDrafts.ts), so a tab
  // rendered in isolation behaves exactly as it does inside the real shell.
  // Cross-TAB persistence is not observable from here -- only one tab is ever
  // mounted -- and is covered by settingsDrafts.test.tsx, which drives the real
  // shell. Declared inside so this module still exports only `renderRoute`.
  function RouteHost() {
    const store = useSettingsDraftStore((context as { settings?: unknown })?.settings);
    // `overrides` lands last so a test can seed the draft ANOTHER tab would
    // have written. A seeded `drafts` is fixed for the render: the rendered
    // tab's own writes go to the real store but stay masked, so use this for
    // read-only assertions about a neighbouring tab's pending edit.
    return <Outlet context={{ ...(context as object), ...store, ...overrides }} />;
  }
```

The rest of the function is unchanged. Existing two-argument callers are unaffected.

- [ ] **Step 2: Run to verify failure**

Run: `cd web-react && bun run test PwmTab`
Expected: FAIL — no note, nothing disabled.

- [ ] **Step 3: Implement**

In `PwmTab.tsx`, add imports:

```tsx
import { useOutletContext } from "react-router";
import {
  MPC_FAN_DISABLED_NOTE,
  MPC_FAN_PWM_NOTE,
  mpcFanPending,
} from "../../../helpers/settings/mpcFan";
import type { SettingsDraftContext } from "../../../helpers/settings/settingsDrafts";
```

Change the existing context read at the top of `PwmTab` so it also picks up the draft store (the Outlet already carries it):

```tsx
  const { settings, drafts } = useOutletContext<SettingsDraftContext & { mode: string }>();
```

After `const dcFan = hasDcFan(settings);`, derive the two states:

```tsx
  // The controller's own duty replaces the temperature profile entirely
  // (controller/runtime/modes/hold.py gates that path on the controller NOT
  // owning the fan), so those inputs describe settings nothing will read.
  const mpcOwnsFan = mpcFanPending(settings, drafts);
  // Read from the draft, not from `settings`, so flipping the toggle clears
  // the warning before the user saves.
  const mpcFanInert = mpcOwnsFan && !pwm.pwm_control;
```

Add `disabled={mpcOwnsFan}` to the `Update Time` field and `disabled={mpcOwnsFan}` to `RangeProfileTable`. Render the note above `Update Time`:

```tsx
      {mpcFanInert && <p className="pf-settings-hint">{MPC_FAN_PWM_NOTE}</p>}
```

and the explanation immediately above the table:

```tsx
      {mpcOwnsFan && <p className="pf-settings-hint">{MPC_FAN_DISABLED_NOTE}</p>}
```

Leave `PWM Control`, `Min Duty Cycle`, `Max Duty Cycle` and `Frequency` untouched — `clamp_duty` in `controller/runtime/logic/fan.py` still bounds every duty the controller emits, and the frequency still configures the output.

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd web-react && bun run test PwmTab`
Expected: PASS

- [ ] **Step 5: Full web gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
cd .. && git add web-react/src/components/settings/tabs/PwmTab.tsx web-react/tests/unit/components/settings/tabs/PwmTab.test.tsx web-react/tests/unit/test-utils.tsx
git commit -m "feat(web): flag and disable PWM settings the MPC makes unreachable"
```

---

## Task 10: Recalibration runbook

Implements R8.1–R8.2. Depends on Tasks 1–3.

**Files:**
- Create: `docs/superpowers/audits/2026-08-02-mpc-recalibration-runbook.md`

**Interfaces:** none.

- [ ] **Step 1: Write the runbook**

Create `docs/superpowers/audits/2026-08-02-mpc-recalibration-runbook.md`:

```markdown
# MPC Recalibration Runbook

Follow this in order. Steps 1–2 must land before any calibration data is worth capturing.

## 1. Deploy the fan-authority fix

Deploy a build containing `controller_fan_authority` (Task 1 of
`docs/superpowers/plans/2026-08-02-mpc-fan-authority-and-calibration.md`).

## 2. Give the controller the fan

Settings > PWM Fan > **PWM Control: on**. Save. Settings > Controller with MPC
selected must now save without the fan-authority error.

## 3. Confirm the fan actually modulates

Start a Hold cook and watch the control log:

```
grep set_duty_cycle logs/control.log | tail -20
```

Expect duty changes tracking the firing rate. **If this is empty, stop** — the
calibration below would capture a grill running at one fixed airflow, which is
what invalidated the previous attempt.

## 4. Discard the contaminated log

Any `controller/mpc_calibration_log.csv` captured before step 2 describes the
grill with the fan pinned at 100 %. Move it aside; do not fit it.

## 5. Capture a fresh log

Settings > Controller > **Log Calibration Data: on**. Run a cook that includes a
full heat-up to a high setpoint and at least one step change down — the fit
needs both to separate the steady gain from the deadtime. 60–90 minutes is
enough.

## 6. Fit

```bash
uv run python -m controller.update_mpc controller/mpc_calibration_log.csv
```

Read the reported RMSE first. Above 10 °C the fit does not describe the log —
re-capture with more excitation rather than accepting the numbers. Heed the
horizon warning if it appears.

## 7. Apply

Paste the emitted JSON into Settings > Controller, field by field. Turn **Log
Calibration Data off**. Save.

## 8. Verify

Run a Hold cook at 450 °F. Expect the firing rate to begin rolling off several
minutes before the setpoint rather than at it. Record peak temperature; the
pre-fix baseline for this grill was **+70 °F** (520 °F peak at a 450 °F
setpoint), reached 263 s after braking began.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/audits/2026-08-02-mpc-recalibration-runbook.md
git commit -m "docs: add the MPC recalibration runbook"
```

---

## Final verification

- [ ] Full Python suite: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
- [ ] Full web gate: `cd web-react && bun run typecheck && bun run test && bun run lint`
- [ ] Confirm no source comment added by this work narrates the incident, the change or a measurement.
