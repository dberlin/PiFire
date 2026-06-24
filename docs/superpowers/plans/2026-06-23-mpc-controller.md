# MPC Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a selectable Model Predictive Control (MPC) controller that holds grill temperature to a ±1.0 °C steady-state band, using a cascade design (firing-rate MPC + combustion allocator) with offset-free disturbance estimation.

**Architecture:** An outer `do-mpc` MPC manipulates a single scalar heat-release demand `Q` against a 2-state grey-box thermal model augmented with an integrating-disturbance state; a Kalman filter estimates the states (offset-free); an inner combustion allocator maps `Q → (auger duty, fan duty)` along an air-fuel curve. The controller plugs into PiFire's existing `ControllerBase` framework; `update()` returns a dict (auger ratio + fan duty) that control.py applies.

**Tech Stack:** Python 3.14, `do-mpc` + `CasADi`/IPOPT, `numpy`/`scipy`, `pytest`, managed with `uv`.

## Global Constraints

- New controller module: `controller/mpc.py`, class `Controller(ControllerBase)`, `module_name`/manifest key `mpc`.
- Supporting modules: `controller/mpc_allocator.py`, `controller/mpc_model.py`, `controller/grill_sim.py` (test-only plant), `controller/update_mpc.py` (offline calibration).
- Built on `do-mpc` (pulls CasADi/matplotlib/pandas). Add `do-mpc` to `pyproject.toml`. Verified to install/run on this Python 3.14 env (`casadi 3.7.2`, `do-mpc 5.1.1`).
- The MPC operates **internally in °C**. Convert incoming `current`/setpoint from °F when `self.units == 'F'`. Outputs (auger ratio, fan duty %) are unitless — no back-conversion.
- `update(current)` returns `{'cycle_ratio': <float 0..1>, 'fan': {'duty': <float 0..100 or None>}}`. Legacy controllers still return a float; control.py handles both.
- Estimator: a steady-state/standard Kalman filter on the augmented linear model (the spec's accepted drop-in; validated in the spike). Not do-mpc MHE — chosen for simplicity, determinism, and testability; the offset-free property is preserved by the integrating `d` state.
- Auger cycle ratio is clamped to `cycle_data['u_min']`/`['u_max']` (0.1/0.9) by existing control.py logic; the allocator maps within `[u_min, u_max]`.
- Control re-solve cadence is `control_period` (default 1.0 s), advertised via `get_control_period()`; prediction `t_step` stays coarse (default 25 s). Measured warm solve ~8 ms on x86, so ≥1 Hz holds with ~100× margin.
- Settings defaults derive from the manifest `config` `option_default`s via `_default_controller_config()` — no `common.py`/`settings.json` edit needed.
- Tests run with `uv run pytest` from the repo root; tabs in Python source under `controller/` (match existing controllers), 4-space in `tests/`.
- Reference implementation (already validated): `docs/superpowers/experiments/mpc_cascade_spike.py`.

---

### Task 1: Add do-mpc dependency

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_mpc_deps.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `do_mpc`, `casadi` importable in the project environment.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mpc_deps.py`:

```python
def test_do_mpc_and_casadi_import():
    import do_mpc            # noqa: F401
    import casadi            # noqa: F401


def test_do_mpc_solves_trivial_mpc():
    import numpy as np
    import do_mpc
    m = do_mpc.model.Model('continuous')
    x = m.set_variable('_x', 'x')
    u = m.set_variable('_u', 'u')
    m.set_rhs('x', -x + u)
    m.setup()
    mpc = do_mpc.controller.MPC(m)
    mpc.set_param(n_horizon=10, t_step=1.0, store_full_solution=False,
                  nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0})
    mpc.set_objective(mterm=x**2, lterm=x**2)
    mpc.set_rterm(u=1e-2)
    mpc.bounds['lower', '_u', 'u'] = -1
    mpc.bounds['upper', '_u', 'u'] = 1
    mpc.setup()
    mpc.x0 = np.array([[1.0]])
    mpc.set_initial_guess()
    u0 = np.asarray(mpc.make_step(np.array([[1.0]]))).flatten()
    assert abs(float(u0[0])) <= 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_deps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'do_mpc'` (if not yet in the locked env).

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, add to the `dependencies` array:

```toml
    "do-mpc>=5.1.1",
```

- [ ] **Step 4: Sync and run**

Run: `uv sync && uv run pytest tests/test_mpc_deps.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/test_mpc_deps.py
git commit -m "feat: add do-mpc dependency for MPC controller"
```

---

### Task 2: Combustion allocator

**Files:**
- Create: `controller/mpc_allocator.py`
- Test: `tests/test_mpc_allocator.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `allocate(Q, *, Q_min, Q_max, u_min, u_max, fan_min_pct, fan_max_pct, enable_fan) -> (auger, fan_duty)`. `auger` ∈ [u_min, u_max]; `fan_duty` ∈ [fan_min_pct, fan_max_pct] or `None` when `enable_fan` is False. Monotonic in `Q`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mpc_allocator.py`:

```python
import pytest
from controller.mpc_allocator import allocate

CFG = dict(Q_min=5.0, Q_max=100.0, u_min=0.1, u_max=0.9,
           fan_min_pct=40.0, fan_max_pct=100.0, enable_fan=True)


def test_min_fire_maps_to_lower_bounds():
    a, f = allocate(5.0, **CFG)
    assert a == pytest.approx(0.1)
    assert f == pytest.approx(40.0)


def test_max_fire_maps_to_upper_bounds():
    a, f = allocate(100.0, **CFG)
    assert a == pytest.approx(0.9)
    assert f == pytest.approx(100.0)


def test_monotonic_and_clamped():
    a_lo, _ = allocate(-50, **CFG)
    a_hi, _ = allocate(999, **CFG)
    a_mid, _ = allocate(52.5, **CFG)
    assert a_lo == pytest.approx(0.1)      # below Q_min clamps
    assert a_hi == pytest.approx(0.9)      # above Q_max clamps
    assert 0.1 < a_mid < 0.9
    assert allocate(40, **CFG)[0] < allocate(60, **CFG)[0]  # monotonic


def test_air_tracks_fuel_constant_afr():
    # fan fraction over its range should equal auger fraction over its range
    a, f = allocate(52.5, **CFG)
    auger_frac = (a - 0.1) / (0.9 - 0.1)
    fan_frac = (f - 40.0) / (100.0 - 40.0)
    assert auger_frac == pytest.approx(fan_frac)


def test_fan_disabled_returns_none():
    cfg = dict(CFG); cfg['enable_fan'] = False
    a, f = allocate(60, **cfg)
    assert f is None
    assert 0.1 < a < 0.9
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_allocator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.mpc_allocator'`.

- [ ] **Step 3: Implement the allocator**

Create `controller/mpc_allocator.py`:

```python
#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Combustion Allocator
*****************************************

 Maps the MPC's scalar firing-rate demand Q to physical actuators (auger duty
 and, on PWM/DC-fan builds, fan duty) along a sensible air-fuel curve. Air
 tracks fuel so the air-fuel ratio stays near its target across the firing
 range, which keeps combustion sensible by construction.

*****************************************
'''


def allocate(Q, *, Q_min, Q_max, u_min, u_max, fan_min_pct, fan_max_pct, enable_fan):
	'''
	:param Q: firing-rate / heat-release demand
	:returns: (auger_duty, fan_duty_pct or None)
	'''
	span = (Q_max - Q_min) if Q_max > Q_min else 1.0
	frac = (Q - Q_min) / span
	frac = max(0.0, min(1.0, frac))                 # clamp to [0, 1]
	auger = u_min + frac * (u_max - u_min)
	fan = fan_min_pct + frac * (fan_max_pct - fan_min_pct) if enable_fan else None
	return auger, fan
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_mpc_allocator.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add controller/mpc_allocator.py tests/test_mpc_allocator.py
git commit -m "feat: add MPC combustion allocator (firing-rate to auger/fan)"
```

---

### Task 3: Grey-box model + Kalman estimator

**Files:**
- Create: `controller/mpc_model.py`
- Test: `tests/test_mpc_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `build_do_mpc_model(*, C_f, C_c, h_fc, h_amb, T_amb) -> do_mpc.model.Model` with states `T_f, T_c, d`, input `Q`, tvp `T_set`.
  - `class GreyBoxKF(*, C_f, C_c, h_fc, h_amb, T_amb, t_step, q_temp, q_dist, r_meas, x0=(20,20,0))` with `update(Q_applied, y_measured) -> np.ndarray([T_f, T_c, d])`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mpc_model.py`:

```python
import numpy as np
from controller.mpc_model import build_do_mpc_model, GreyBoxKF

PARAMS = dict(C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55, T_amb=20.0)


def test_model_builds():
    m = build_do_mpc_model(**PARAMS)
    assert set(m.x.keys()) >= {'T_f', 'T_c', 'd'}
    assert 'Q' in m.u.keys()


def test_kf_offset_free_under_constant_disturbance():
    # Feed a measurement that is persistently biased above what the model
    # predicts for zero d; the estimated d must converge so the predicted
    # chamber temp matches the measurement (offset-free).
    kf = GreyBoxKF(t_step=25.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04,
                   x0=(100.0, 100.0, 0.0), **PARAMS)
    y = 100.0
    for _ in range(200):
        x = kf.update(Q_applied=49.5, y_measured=y)  # ~steady Q for 100C
    # estimated chamber temp tracks the measurement
    assert abs(x[1] - y) < 0.5
    # disturbance state is non-trivial (it absorbed the model mismatch)
    assert abs(x[2]) > 1e-6


def test_kf_tracks_measured_temperature():
    kf = GreyBoxKF(t_step=25.0, q_temp=1e-2, q_dist=0.5, r_meas=0.04,
                   x0=(20.0, 20.0, 0.0), **PARAMS)
    x = None
    for _ in range(100):
        x = kf.update(Q_applied=49.5, y_measured=110.0)
    assert abs(x[1] - 110.0) < 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.mpc_model'`.

- [ ] **Step 3: Implement the model + KF**

Create `controller/mpc_model.py`:

```python
#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Grey-box Thermal Model + Estimator
*****************************************

 Two lumped thermal masses (firepot T_f, chamber T_c) driven by a scalar
 firing-rate Q, plus an integrating disturbance state d for offset-free
 tracking. Provides the do-mpc model used by the controller and a Kalman
 filter over the same augmented linear model used as the state/disturbance
 estimator.

*****************************************
'''

import numpy as np
from scipy.linalg import expm
import do_mpc


def build_do_mpc_model(*, C_f, C_c, h_fc, h_amb, T_amb):
	model = do_mpc.model.Model('continuous')
	T_f = model.set_variable('_x', 'T_f')
	T_c = model.set_variable('_x', 'T_c')
	d = model.set_variable('_x', 'd')
	Q = model.set_variable('_u', 'Q')
	model.set_variable('_tvp', 'T_set')
	model.set_rhs('T_f', (Q - h_fc * (T_f - T_c)) / C_f)
	model.set_rhs('T_c', (h_fc * (T_f - T_c) - h_amb * (T_c - T_amb) + d) / C_c)
	model.set_rhs('d', d * 0)
	model.setup()
	return model


class GreyBoxKF:
	'''
	Kalman filter over the augmented linear model x = [T_f, T_c, d], input Q.
	The constant ambient term enters as an affine input (held at 1).
	'''

	def __init__(self, *, C_f, C_c, h_fc, h_amb, T_amb, t_step,
	             q_temp, q_dist, r_meas, x0=(20.0, 20.0, 0.0)):
		Ac = np.array([
			[-h_fc / C_f,  h_fc / C_f,          0.0],
			[ h_fc / C_c, -(h_fc + h_amb) / C_c, 1.0 / C_c],
			[ 0.0,         0.0,                  0.0],
		])
		# columns: [Q input, affine constant=1]
		Baug = np.array([
			[1.0 / C_f, 0.0],
			[0.0,       h_amb * T_amb / C_c],
			[0.0,       0.0],
		])
		M = np.zeros((5, 5))
		M[:3, :3] = Ac
		M[:3, 3:] = Baug
		Md = expm(M * t_step)
		self.Ad = Md[:3, :3]
		self.Bd = Md[:3, 3:4]      # for Q
		self.bd = Md[:3, 4:5]      # affine (constant input = 1)
		self.H = np.array([[0.0, 1.0, 0.0]])
		self.Qkf = np.diag([q_temp, q_temp, q_dist])
		self.Rkf = np.array([[r_meas]])
		self.x = np.array(x0, dtype=float)
		self.P = np.eye(3) * 5.0

	def update(self, Q_applied, y_measured):
		# predict
		self.x = self.Ad @ self.x + self.Bd.flatten() * Q_applied + self.bd.flatten()
		self.P = self.Ad @ self.P @ self.Ad.T + self.Qkf
		# update
		S = self.H @ self.P @ self.H.T + self.Rkf
		K = (self.P @ self.H.T) / S
		self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
		self.P = (np.eye(3) - K @ self.H) @ self.P
		return self.x
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_mpc_model.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add controller/mpc_model.py tests/test_mpc_model.py
git commit -m "feat: add MPC grey-box thermal model and Kalman estimator"
```

---

### Task 4: MPC controller (the `mpc` controller module)

**Files:**
- Create: `controller/mpc.py`
- Test: `tests/test_mpc_controller.py`

**Interfaces:**
- Consumes: `build_do_mpc_model`, `GreyBoxKF` (Task 3); `allocate` (Task 2); `ControllerBase` (`controller/base.py`).
- Produces: `class Controller(ControllerBase)` with:
  - `__init__(config, units, cycle_data)` — builds model, do-mpc MPC, KF, resolves config (merging `cycle_data['u_min'/'u_max']`).
  - `update(current) -> {'cycle_ratio': float, 'fan': {'duty': float|None}}`.
  - `set_target(set_point)` — stores setpoint (converted to °C).
  - `get_control_period() -> float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mpc_controller.py`:

```python
import time
import numpy as np
from controller.mpc import Controller

CONFIG = dict(
    n_horizon=20, t_step=25.0, control_period=1.0, Q_w=1.0, R_dQ=0.02,
    Q_min=5.0, Q_max=100.0, C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55,
    T_amb=20.0, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan_input=True,
    est_q_temp=1e-2, est_q_dist=0.5, est_r_meas=0.04,
)
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}


def _make():
    c = Controller(dict(CONFIG), 'C', dict(CYCLE))
    c.set_target(110.0)
    return c


def test_update_returns_dict_contract():
    c = _make()
    out = c.update(100.0)
    assert isinstance(out, dict)
    assert 0.1 <= out['cycle_ratio'] <= 0.9
    assert 'fan' in out and 'duty' in out['fan']
    assert 40.0 <= out['fan']['duty'] <= 100.0


def test_below_setpoint_demands_more_than_at_setpoint():
    # settle the estimator at each measured temperature before comparing
    c = _make()
    for _ in range(5):
        cold = c.update(80.0)['cycle_ratio']
    c2 = _make()
    for _ in range(5):
        hot = c2.update(140.0)['cycle_ratio']
    assert cold > hot  # colder than target -> more auger


def test_control_period_advertised():
    assert _make().get_control_period() == 1.0


def test_fahrenheit_setpoint_converted():
    c = Controller(dict(CONFIG), 'F', dict(CYCLE))
    c.set_target(230.0)            # 230 F = 110 C
    assert abs(c._set_point_c - 110.0) < 0.6


def test_warm_solve_under_budget():
    c = _make()
    c.update(100.0)               # cold
    t0 = time.perf_counter()
    for _ in range(20):
        c.update(100.0)
    avg_ms = (time.perf_counter() - t0) / 20 * 1e3
    assert avg_ms < 200.0         # >=1 Hz with wide margin (x86 ~8 ms)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_controller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.mpc'`.

- [ ] **Step 3: Implement the controller**

Create `controller/mpc.py`:

```python
#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Controller (cascade: firing-rate + combustion allocator)
*****************************************

 Outer MPC manipulates a scalar firing-rate demand Q against a grey-box
 thermal model with an integrating-disturbance state (offset-free tracking via
 a Kalman filter). The inner combustion allocator maps Q to auger/fan. Returns
 a dict: {'cycle_ratio': auger_duty, 'fan': {'duty': pct or None}}.

 Operates internally in Celsius.

*****************************************
'''

import numpy as np
import do_mpc

from controller.base import ControllerBase
from controller.mpc_model import build_do_mpc_model, GreyBoxKF
from controller.mpc_allocator import allocate

_DEFAULTS = dict(
	n_horizon=20, t_step=25.0, control_period=1.0, Q_w=1.0, R_dQ=0.02,
	Q_min=5.0, Q_max=100.0, C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55,
	T_amb=20.0, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan_input=False,
	est_q_temp=1e-2, est_q_dist=0.5, est_r_meas=0.04,
)


def _to_c(value, units):
	return (value - 32.0) * 5.0 / 9.0 if units == 'F' else value


class Controller(ControllerBase):
	def __init__(self, config, units, cycle_data):
		super().__init__(config, units, cycle_data)
		self.function_list.append('get_control_period')

		cfg = dict(_DEFAULTS)
		cfg.update(config or {})
		self.cfg = cfg
		self.u_min = cycle_data.get('u_min', 0.1)
		self.u_max = cycle_data.get('u_max', 0.9)

		self._set_point_c = 0.0
		self._last_Q = cfg['Q_min']

		# grey-box do-mpc model
		self.model = build_do_mpc_model(
			C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'],
			h_amb=cfg['h_amb'], T_amb=cfg['T_amb'])

		# MPC controller
		self.mpc = do_mpc.controller.MPC(self.model)
		self.mpc.set_param(
			n_horizon=int(cfg['n_horizon']), t_step=float(cfg['t_step']),
			store_full_solution=False,
			nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0,
			             'ipopt.sb': 'yes'})
		T_c = self.model.x['T_c']
		T_set = self.model.tvp['T_set']
		self.mpc.set_objective(mterm=cfg['Q_w'] * (T_c - T_set) ** 2,
		                       lterm=cfg['Q_w'] * (T_c - T_set) ** 2)
		self.mpc.set_rterm(Q=cfg['R_dQ'])
		self.mpc.bounds['lower', '_u', 'Q'] = cfg['Q_min']
		self.mpc.bounds['upper', '_u', 'Q'] = cfg['Q_max']

		tvp_template = self.mpc.get_tvp_template()
		def tvp_fun(t_now):
			for k in range(int(cfg['n_horizon']) + 1):
				tvp_template['_tvp', k, 'T_set'] = self._set_point_c
			return tvp_template
		self.mpc.set_tvp_fun(tvp_fun)
		self.mpc.setup()

		# estimator
		self.kf = GreyBoxKF(
			C_f=cfg['C_f'], C_c=cfg['C_c'], h_fc=cfg['h_fc'], h_amb=cfg['h_amb'],
			T_amb=cfg['T_amb'], t_step=float(cfg['t_step']),
			q_temp=cfg['est_q_temp'], q_dist=cfg['est_q_dist'],
			r_meas=cfg['est_r_meas'], x0=(cfg['T_amb'], cfg['T_amb'], 0.0))

		self.mpc.x0 = np.array([[cfg['T_amb']], [cfg['T_amb']], [0.0]])
		self.mpc.set_initial_guess()

	def set_target(self, set_point):
		self.set_point = set_point
		self._set_point_c = _to_c(set_point, self.units)
		self._last_Q = self.cfg['Q_min']

	def get_control_period(self):
		return float(self.cfg['control_period'])

	def update(self, current):
		y = _to_c(current, self.units)
		# 1) estimate states from the measurement
		x_hat = self.kf.update(self._last_Q, y)
		# 2) optimize firing rate Q. The box constraints bound Q; on any solver
		#    error we hold the previous move so the control loop never breaks.
		try:
			Q = float(np.asarray(self.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
		except Exception:
			Q = self._last_Q
		Q = float(np.clip(Q, self.cfg['Q_min'], self.cfg['Q_max']))
		self._last_Q = Q
		# 3) allocate Q -> actuators
		auger, fan_duty = allocate(
			Q, Q_min=self.cfg['Q_min'], Q_max=self.cfg['Q_max'],
			u_min=self.u_min, u_max=self.u_max,
			fan_min_pct=self.cfg['fan_min_pct'], fan_max_pct=self.cfg['fan_max_pct'],
			enable_fan=bool(self.cfg['enable_fan_input']))
		return {'cycle_ratio': auger, 'fan': {'duty': fan_duty}}
```

Note on the solver-success check: do-mpc records solver stats in `self.mpc.data`. The guarded `try/except` plus the `_last_Q` fallback guarantees `update()` always returns a bounded command even if the stats field is absent or the solve raises.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_mpc_controller.py -v`
Expected: 5 passed (including the timing budget test).

- [ ] **Step 5: Commit**

```bash
git add controller/mpc.py tests/test_mpc_controller.py
git commit -m "feat: add MPC controller (firing-rate cascade + offset-free KF)"
```

---

### Task 5: Grill simulator + closed-loop ±1 °C gate

**Files:**
- Create: `controller/grill_sim.py`
- Test: `tests/test_mpc_closed_loop.py`

**Interfaces:**
- Consumes: nothing from PiFire at runtime; the test also imports `controller.mpc.Controller` and `controller.mpc_allocator.allocate`.
- Produces: `class GrillSim(*, seed=0)` with `step(auger, fan_duty_pct) -> measured_chamber_temp_C` (advances one `t_step`), modeling a **mismatched** plant: offset params, AFR-dependent combustion efficiency, ambient drift, a lid-open event, process/measurement noise. `t_now` advances by `Ts=25 s` per step. Exposes `.true_Tc` and `.afr` after each step.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mpc_closed_loop.py`:

```python
import numpy as np
from controller.mpc import Controller
from controller.grill_sim import GrillSim

CONFIG = dict(
    n_horizon=20, t_step=25.0, control_period=1.0, Q_w=1.0, R_dQ=0.02,
    Q_min=5.0, Q_max=100.0, C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55,
    T_amb=20.0, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan_input=True,
    est_q_temp=1e-2, est_q_dist=0.5, est_r_meas=0.04,
)
CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}


def test_closed_loop_holds_one_degree_band():
    c = Controller(dict(CONFIG), 'C', dict(CYCLE))
    c.set_target(110.0)
    sim = GrillSim(seed=0)
    ts, temps = [], []
    for k in range(288):                       # 2 h at 25 s
        t = k * 25.0
        y = sim.measured()
        out = c.update(y)
        # map cycle_ratio back to a firing rate so the plant sees the allocation
        sim.step_from_allocation(out['cycle_ratio'], out['fan']['duty'])
        ts.append(t); temps.append(sim.true_Tc)
    ts = np.array(ts); temps = np.array(temps)
    # steady window: settled hold, excluding warmup and the lid event
    sm = (ts >= 1500) & (ts < 2900)
    err = temps[sm] - 110.0
    assert np.max(np.abs(err)) <= 1.0          # the +-1.0 C gate
    assert np.mean(np.abs(err) <= 1.0) >= 0.95


def test_lid_open_recovers():
    c = Controller(dict(CONFIG), 'C', dict(CYCLE))
    c.set_target(110.0)
    sim = GrillSim(seed=0)
    dipped = False
    recovered_after_dip = False
    for k in range(288):
        y = sim.measured()
        out = c.update(y)
        sim.step_from_allocation(out['cycle_ratio'], out['fan']['duty'])
        t = k * 25.0
        if 3000 <= t < 3200 and sim.true_Tc < 105.0:
            dipped = True
        if dipped and t > 3400 and abs(sim.true_Tc - 110.0) <= 1.0:
            recovered_after_dip = True
    assert dipped and recovered_after_dip
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_closed_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.grill_sim'`.

- [ ] **Step 3: Implement the simulator**

Create `controller/grill_sim.py` (port of the validated spike plant; the controller hands an auger ratio + fan %, which the sim converts to actual heat through an AFR-dependent efficiency):

```python
#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Grill Simulator (test-only)
*****************************************

 A deliberately MISMATCHED nonlinear grill plant for closed-loop validation of
 the MPC. Differs from the controller's internal model (parameter offsets,
 air-fuel-ratio dependent combustion efficiency, ambient drift, a lid-open
 event, and process/measurement noise) so a passing +-1.0 C result is not
 tautological. Built on do-mpc's simulator.

*****************************************
'''

import numpy as np
import do_mpc

Ts = 25.0
# truth params (offset ~15% from the controller's nominal)
C_f_t, C_c_t = 70.0, 350.0
h_fc_t, h_amb_t = 1.70, 0.62
# allocator endpoints (must match the controller config used in tests)
Q_MIN, Q_MAX = 5.0, 100.0
U_MIN, U_MAX = 0.1, 0.9
FAN_MIN, FAN_MAX = 40.0, 100.0
FUEL_TO_HEAT = Q_MAX / U_MAX
AFR_OPT, AFR_SIGMA = 1.0, 0.28


class GrillSim:
	def __init__(self, *, seed=0):
		self.rng = np.random.default_rng(seed)
		self.t = 0.0
		self.afr = AFR_OPT
		m = do_mpc.model.Model('continuous')
		T_f = m.set_variable('_x', 'T_f')
		T_c = m.set_variable('_x', 'T_c')
		Qh = m.set_variable('_u', 'Qh')
		T_amb = m.set_variable('_tvp', 'T_amb')
		lid = m.set_variable('_tvp', 'lid')
		m.set_rhs('T_f', (Qh - h_fc_t * (T_f - T_c)) / C_f_t)
		m.set_rhs('T_c', (h_fc_t * (T_f - T_c) - h_amb_t * lid * (T_c - T_amb)) / C_c_t)
		m.setup()
		self.sim = do_mpc.simulator.Simulator(m)
		self.sim.set_param(t_step=Ts)
		tvp_t = self.sim.get_tvp_template()
		def tvp_fun(t_now):
			tvp_t['T_amb'] = 18.0 - 8.0 * (t_now / 7200.0)            # drift
			tvp_t['lid'] = 4.0 if 3000.0 <= t_now < 3090.0 else 1.0   # lid open
			return tvp_t
		self.sim.set_tvp_fun(tvp_fun)
		self.sim.setup()
		self.sim.x0 = np.array([[20.0], [20.0]])
		self.sim.set_initial_guess()

	@property
	def true_Tc(self):
		return float(self.sim.x0['T_c'])

	def measured(self):
		return self.true_Tc + float(self.rng.normal(0, 0.2))

	def step_from_allocation(self, auger, fan_duty_pct):
		fuel = max(auger, 1e-6)
		air_frac = ((fan_duty_pct - FAN_MIN) / (FAN_MAX - FAN_MIN)
		            if fan_duty_pct is not None else (fuel - U_MIN) / (U_MAX - U_MIN))
		fuel_frac = (fuel - U_MIN) / (U_MAX - U_MIN)
		# normalized air/fuel ratio; matched allocation drives air_frac==fuel_frac
		# so afr ~ AFR_OPT (1.0) and combustion stays efficient.
		afr = (air_frac + 1e-6) / (fuel_frac + 1e-6)
		self.afr = afr
		eff = np.exp(-((afr - AFR_OPT) ** 2) / (2 * AFR_SIGMA ** 2))
		Qh = FUEL_TO_HEAT * fuel * eff
		self.sim.make_step(np.array([[Qh]]))
		self.t += Ts
		return self.true_Tc
```

Note: the AFR computation compares the fan and auger *fractions* over their respective ranges; the allocator drives both fractions equal, so `afr ≈ 1.0` (sensible), and `eff ≈ 1.0`. This reproduces the spike's behavior where the cascade keeps combustion on the high-efficiency curve.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_mpc_closed_loop.py -v`
Expected: 2 passed (steady-state band ≤ 1.0 °C; lid recovery).

If the band assertion is marginally exceeded due to estimator tuning, raise `est_q_dist` toward 1.0 (faster disturbance tracking) or lower `est_r_meas`; the spike achieved 0.31 °C max with the defaults above.

- [ ] **Step 5: Commit**

```bash
git add controller/grill_sim.py tests/test_mpc_closed_loop.py
git commit -m "feat: add grill simulator and closed-loop +-1C MPC validation test"
```

---

### Task 6: control.py integration + extended contract

**Files:**
- Modify: `controller/base.py` (add `get_control_period` default AND the `normalize_controller_output` helper)
- Modify: `control.py` (import the helper from `controller.base`; controller dispatch in the Hold work-cycle, ~lines 705-712)
- Test: `tests/test_mpc_integration.py`

**IMPORTANT — do not import `control` in tests.** `control.py` runs an unguarded
`while True:` loop at module top level, so `import control` hangs forever. The
`normalize_controller_output` helper therefore lives in the import-safe
`controller/base.py` (not in `control.py`), and the test imports it from there.

**Interfaces:**
- Consumes: `Controller.update()` dict contract and `get_control_period()` (Task 4).
- Produces:
  - `ControllerBase.get_control_period(self) -> None` (default; legacy controllers keep CycleTime cadence).
  - `controller.base.normalize_controller_output(output) -> (cycle_ratio: float, fan: dict|None)` (module-level function in `controller/base.py`).
  - control.py imports that helper, applies the fan duty, and uses the controller's control period for the update cadence.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mpc_integration.py`:

```python
import importlib


def test_base_default_control_period_is_none():
    from controller.base import ControllerBase
    cb = ControllerBase({}, 'C', {})
    assert cb.get_control_period() is None


def test_normalize_handles_float_and_dict():
    # NOTE: import the helper from controller.base, NOT from control --
    # importing control hangs (it runs an unguarded while True: loop).
    from controller.base import normalize_controller_output
    # legacy float
    ratio, fan = normalize_controller_output(0.42)
    assert ratio == 0.42 and fan is None
    # mpc dict
    ratio, fan = normalize_controller_output(
        {'cycle_ratio': 0.3, 'fan': {'duty': 80.0}})
    assert ratio == 0.3 and fan == {'duty': 80.0}
    # dict without fan
    ratio, fan = normalize_controller_output({'cycle_ratio': 0.5})
    assert ratio == 0.5 and fan is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_integration.py -v`
Expected: FAIL — `AttributeError: module 'control' has no attribute 'normalize_controller_output'` (and the base method test fails until added).

- [ ] **Step 3: Add `get_control_period` to the base class**

In `controller/base.py`, add to `ControllerBase` (after `supported_functions`):

```python
	def get_control_period(self):
		'''
		Desired re-solve / actuation period in seconds. Return None to use the
		mode's CycleTime (legacy behavior). Controllers that run faster than the
		auger cycle (e.g. MPC) return a fixed period such as 1.0.
		'''
		return None
```

- [ ] **Step 4: Add the normalize helper to `controller/base.py`**

In `controller/base.py`, add a module-level function (outside the
`ControllerBase` class, e.g. after it). `base.py` only imports `time`, so it is
safe to import from tests and from `control.py`:

```python
def normalize_controller_output(output):
	'''
	Normalize a controller's update() return into (cycle_ratio, fan).

	Legacy controllers return a float cycle ratio; the MPC controller returns
	{'cycle_ratio': float, 'fan': {'duty': pct or None}}. fan is returned only
	when a duty is present.
	'''
	if isinstance(output, dict):
		ratio = float(output.get('cycle_ratio', 0.0))
		fan = output.get('fan')
		if isinstance(fan, dict) and fan.get('duty') is not None:
			return ratio, fan
		return ratio, None
	return float(output), None
```

- [ ] **Step 5: Wire the dispatch into the Hold work-cycle**

First, in `control.py`'s import section (near the top, with the other imports),
add:

```python
from controller.base import normalize_controller_output
```

Then replace the Hold update block (currently around lines 709-712):

```python
				if (now - controllerCycleStart) > CycleTime:
					pid_output = controllerCore.update(ptemp)
					controllerCycleStart = now
					CycleRatio = RawCycleRatio = settings['cycle_data']['u_min'] if LidOpenDetect else pid_output
```

with:

```python
				controller_interval = controllerCore.get_control_period() or CycleTime
				if (now - controllerCycleStart) > controller_interval:
					raw_output = controllerCore.update(ptemp)
					pid_output, fan_cmd = normalize_controller_output(raw_output)
					controllerCycleStart = now
					CycleRatio = RawCycleRatio = settings['cycle_data']['u_min'] if LidOpenDetect else pid_output
					# Controllers that command the fan directly (MPC) apply duty
					# here, only when a PWM/DC fan is present.
					if fan_cmd is not None and settings['platform']['dc_fan'] and control['pwm_control']:
						grill_platform.set_duty_cycle(fan_cmd['duty'])
```

`pid_output` remains a float, so the existing `CycleRatio`/`u_min`/`u_max` clamping (lines ~714-726) and FanPid logic (lines ~936-940) are unchanged.

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_mpc_integration.py -v`
Expected: 2 passed.

- [ ] **Step 7: Sanity-check the helper (do NOT import `control` — it hangs)**

Run: `uv run python -c "from controller.base import normalize_controller_output; print(normalize_controller_output(0.5))"`
Expected: prints `(0.5, None)` with no import error.

- [ ] **Step 8: Commit**

```bash
git add controller/base.py control.py tests/test_mpc_integration.py
git commit -m "feat: integrate MPC dict output and control period into control.py Hold cycle"
```

---

### Task 7: Register the controller (manifest + settings)

**Files:**
- Modify: `controller/controllers.json` (add `metadata.mpc`)
- Test: `tests/test_mpc_manifest.py`

**Interfaces:**
- Consumes: `_default_controller_config()` (`common/common.py`), which reads `option_name`/`option_default` from each controller's `config` array.
- Produces: a `metadata.mpc` entry with `module_name == "mpc"` and a full `config` array; `settings['controller']['config']['mpc']` then derives automatically.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mpc_manifest.py`:

```python
import json
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _meta():
    with open(os.path.join(BASE, 'controller', 'controllers.json')) as f:
        return json.load(f)['metadata']


def test_mpc_entry_present():
    e = _meta()['mpc']
    assert e['module_name'] == 'mpc'
    names = {o['option_name'] for o in e['config']}
    # a representative subset of the required options
    assert {'n_horizon', 'control_period', 'C_c', 'h_amb', 'Q_max',
            'enable_fan_input', 'est_r_meas'} <= names


def test_default_controller_config_includes_mpc():
    cwd = os.getcwd(); os.chdir(BASE)
    try:
        from common.common import _default_controller_config
        cfg = _default_controller_config()
    finally:
        os.chdir(cwd)
    assert 'mpc' in cfg
    assert cfg['mpc']['control_period'] == 1.0
    assert cfg['mpc']['enable_fan_input'] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_manifest.py -v`
Expected: FAIL — `KeyError: 'mpc'`.

- [ ] **Step 3: Add the manifest entry**

In `controller/controllers.json`, add a new key `mpc` inside `metadata` (sibling of `pid`). Insert this object (mind surrounding commas; keep the file valid JSON):

```json
"mpc": {
    "friendly_name": "Model Predictive Control (MPC)",
    "module_name": "mpc",
    "image": "pid.png",
    "description": "Model Predictive Controller. An outer optimizer commands a firing-rate demand against a grey-box thermal model with offset-free disturbance estimation; an inner combustion allocator maps it to auger and (on PWM/DC-fan builds) fan. Targets a tight temperature band. Built on do-mpc.",
    "author": "PiFire",
    "link": "",
    "contributors": [],
    "attributions": [],
    "recommendations": {
        "cycle": {
            "cycle_time": 25,
            "cycle_ratio_min": 0.1,
            "cycle_ratio_max": 0.9
        }
    },
    "config": [
        {"option_name": "n_horizon", "option_friendly_name": "Prediction Horizon (steps)", "option_description": "Number of prediction steps. [Default=20]", "option_type": "int", "option_default": 20, "option_min": 5, "option_max": 60, "option_step": 1, "hidden": false},
        {"option_name": "t_step", "option_friendly_name": "Prediction Step (s)", "option_description": "Prediction discretization interval in seconds. [Default=25]", "option_type": "float", "option_default": 25.0, "option_min": 1.0, "option_max": 60.0, "option_step": 1.0, "hidden": false},
        {"option_name": "control_period", "option_friendly_name": "Control Period (s)", "option_description": "How often the MPC re-solves and updates the actuators. [Default=1.0]", "option_type": "float", "option_default": 1.0, "option_min": 0.5, "option_max": 25.0, "option_step": 0.5, "hidden": false},
        {"option_name": "Q_w", "option_friendly_name": "Tracking Weight", "option_description": "Penalty on temperature tracking error. [Default=1.0]", "option_type": "float", "option_default": 1.0, "option_min": 0.0, "option_max": null, "option_step": 0.1, "hidden": false},
        {"option_name": "R_dQ", "option_friendly_name": "Move Suppression", "option_description": "Penalty on firing-rate changes (smoothness). [Default=0.02]", "option_type": "float", "option_default": 0.02, "option_min": 0.0, "option_max": null, "option_step": 0.01, "hidden": false},
        {"option_name": "Q_min", "option_friendly_name": "Min Firing Rate", "option_description": "Minimum sustainable firing-rate demand. [Default=5.0]", "option_type": "float", "option_default": 5.0, "option_min": 0.0, "option_max": null, "option_step": 1.0, "hidden": false},
        {"option_name": "Q_max", "option_friendly_name": "Max Firing Rate", "option_description": "Maximum firing-rate demand. [Default=100.0]", "option_type": "float", "option_default": 100.0, "option_min": 1.0, "option_max": null, "option_step": 1.0, "hidden": false},
        {"option_name": "C_f", "option_friendly_name": "Firepot Heat Capacity", "option_description": "Firepot lumped thermal mass. [Default=60.0]", "option_type": "float", "option_default": 60.0, "option_min": 1.0, "option_max": null, "option_step": 1.0, "hidden": false},
        {"option_name": "C_c", "option_friendly_name": "Chamber Heat Capacity", "option_description": "Chamber lumped thermal mass. [Default=306.0]", "option_type": "float", "option_default": 306.0, "option_min": 1.0, "option_max": null, "option_step": 1.0, "hidden": false},
        {"option_name": "h_fc", "option_friendly_name": "Firepot-Chamber Coupling", "option_description": "Heat-transfer coefficient firepot->chamber. [Default=2.0]", "option_type": "float", "option_default": 2.0, "option_min": 0.0, "option_max": null, "option_step": 0.1, "hidden": false},
        {"option_name": "h_amb", "option_friendly_name": "Ambient Loss Coefficient", "option_description": "Chamber->ambient heat-loss coefficient (sets steady state). [Default=0.55]", "option_type": "float", "option_default": 0.55, "option_min": 0.0, "option_max": null, "option_step": 0.01, "hidden": false},
        {"option_name": "T_amb", "option_friendly_name": "Ambient Temperature (C)", "option_description": "Nominal ambient temperature in Celsius. [Default=20.0]", "option_type": "float", "option_default": 20.0, "option_min": -40.0, "option_max": 60.0, "option_step": 1.0, "hidden": false},
        {"option_name": "fan_min_pct", "option_friendly_name": "Fan Duty at Min Fire (%)", "option_description": "Fan duty percent at minimum firing rate (PWM fans). [Default=40.0]", "option_type": "float", "option_default": 40.0, "option_min": 0.0, "option_max": 100.0, "option_step": 1.0, "hidden": false},
        {"option_name": "fan_max_pct", "option_friendly_name": "Fan Duty at Max Fire (%)", "option_description": "Fan duty percent at maximum firing rate (PWM fans). [Default=100.0]", "option_type": "float", "option_default": 100.0, "option_min": 0.0, "option_max": 100.0, "option_step": 1.0, "hidden": false},
        {"option_name": "enable_fan_input", "option_friendly_name": "MPC Controls Fan", "option_description": "If enabled, the MPC commands fan duty (PWM/DC-fan builds only). [Default=false]", "option_type": "bool", "option_default": false, "hidden": false},
        {"option_name": "est_q_temp", "option_friendly_name": "Estimator Temp Noise", "option_description": "Process-noise variance on temperature states. [Default=0.01]", "option_type": "float", "option_default": 0.01, "option_min": 0.0, "option_max": null, "option_step": 0.001, "hidden": false},
        {"option_name": "est_q_dist", "option_friendly_name": "Estimator Disturbance Noise", "option_description": "Random-walk variance on the disturbance state (drift tracking). [Default=0.5]", "option_type": "float", "option_default": 0.5, "option_min": 0.0, "option_max": null, "option_step": 0.1, "hidden": false},
        {"option_name": "est_r_meas", "option_friendly_name": "Estimator Measurement Noise", "option_description": "Measurement-noise variance (sensor). [Default=0.04]", "option_type": "float", "option_default": 0.04, "option_min": 0.0, "option_max": null, "option_step": 0.01, "hidden": false}
    ]
}
```

- [ ] **Step 4: Verify JSON validity and run**

Run: `python3 -c "import json; json.load(open('controller/controllers.json'))" && uv run pytest tests/test_mpc_manifest.py -v`
Expected: no JSON error; 2 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all MPC tests plus the pre-existing suite pass.

- [ ] **Step 6: Commit**

```bash
git add controller/controllers.json tests/test_mpc_manifest.py
git commit -m "feat: register MPC controller in controllers manifest"
```

---

### Task 8: Offline calibration utility

**Files:**
- Create: `controller/update_mpc.py`
- Test: `tests/test_mpc_calibration.py`

**Interfaces:**
- Consumes: `GreyBoxKF`/model parameterization conventions (same names: `C_f, C_c, h_fc, h_amb`).
- Produces: `fit_params(t, temp, Q, *, T_amb, init) -> dict` returning fitted `{C_f, C_c, h_fc, h_amb}` by least-squares simulation fit; a `main()` CLI reading a CSV and printing the fitted params.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mpc_calibration.py`:

```python
import numpy as np
from controller.update_mpc import simulate_chamber, fit_params

TRUE = dict(C_f=55.0, C_c=320.0, h_fc=1.8, h_amb=0.5)


def test_simulate_chamber_runs():
    t = np.arange(0, 3000, 25.0)
    Q = np.full_like(t, 49.5)
    temp = simulate_chamber(t, Q, T_amb=20.0, **TRUE, T0=20.0)
    assert temp.shape == t.shape
    assert temp[-1] > temp[0]                 # heats up


def test_fit_recovers_params_on_synthetic_data():
    t = np.arange(0, 6000, 25.0)
    # excitation: step Q up then down so dynamics are identifiable
    Q = np.where(t < 3000, 60.0, 35.0)
    temp = simulate_chamber(t, Q, T_amb=20.0, **TRUE, T0=20.0)
    fitted = fit_params(t, temp, Q, T_amb=20.0,
                        init=dict(C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55))
    # steady-state gain h_amb and chamber capacity should recover closely
    assert abs(fitted['h_amb'] - TRUE['h_amb']) < 0.1
    assert abs(fitted['C_c'] - TRUE['C_c']) / TRUE['C_c'] < 0.25
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mpc_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.update_mpc'`.

- [ ] **Step 3: Implement the calibration utility**

Create `controller/update_mpc.py`:

```python
#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Offline Calibration Utility
*****************************************

 Fits the grey-box thermal parameters (C_f, C_c, h_fc, h_amb) to a logged
 history CSV so the MPC model can be refined for a specific grill. The
 controller ships with working defaults and does not require calibration.

 CSV columns: time_s, temp_c, Q  (Q is the firing-rate demand; if you logged
 auger duty instead, map it back through the allocator first).

 Usage: python -m controller.update_mpc history.csv
*****************************************
'''

import argparse
import numpy as np
from scipy.optimize import least_squares


def simulate_chamber(t, Q, *, C_f, C_c, h_fc, h_amb, T_amb, T0):
	'''Forward-simulate chamber temperature for the grey-box model (Euler).

	out[i] is the chamber temperature AT time t[i] (so out[0] == T0); each step
	advances the state from t[i] to t[i+1] using the input Q[i]. This alignment
	matters when fitting real logs, where the measured series starts at T0.
	'''
	t = np.asarray(t, dtype=float)
	Q = np.asarray(Q, dtype=float)
	Tf = T0
	Tc = T0
	out = np.empty_like(t)
	for i in range(len(t)):
		out[i] = Tc                      # record state at t[i] (out[0] == T0)
		if i < len(t) - 1:
			dt = t[i + 1] - t[i]
			dTf = (Q[i] - h_fc * (Tf - Tc)) / C_f
			dTc = (h_fc * (Tf - Tc) - h_amb * (Tc - T_amb)) / C_c
			Tf += dTf * dt
			Tc += dTc * dt
	return out


def fit_params(t, temp, Q, *, T_amb, init):
	temp = np.asarray(temp, dtype=float)
	keys = ['C_f', 'C_c', 'h_fc', 'h_amb']
	x0 = np.array([init[k] for k in keys], dtype=float)

	def residual(x):
		params = dict(zip(keys, np.abs(x)))     # keep params positive
		sim = simulate_chamber(t, Q, T_amb=T_amb, T0=float(temp[0]), **params)
		return sim - temp

	res = least_squares(residual, x0, method='trf', max_nfev=2000)
	return dict(zip(keys, np.abs(res.x)))


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument('csv')
	ap.add_argument('--t-amb', type=float, default=20.0)
	args = ap.parse_args()
	import pandas as pd
	df = pd.read_csv(args.csv)
	fitted = fit_params(df['time_s'].values, df['temp_c'].values, df['Q'].values,
	                    T_amb=args.t_amb,
	                    init=dict(C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55))
	print('Fitted grey-box params:')
	for k, v in fitted.items():
		print(f'  {k}: {v:.4f}')


if __name__ == '__main__':
	main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_mpc_calibration.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add controller/update_mpc.py tests/test_mpc_calibration.py
git commit -m "feat: add MPC offline grey-box calibration utility"
```
