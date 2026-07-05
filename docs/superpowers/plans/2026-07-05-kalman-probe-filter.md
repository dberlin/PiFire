# Constant-velocity Kalman probe filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the standard-deviation-gated moving average (`probes/temp_queue.py`) with a constant-velocity Kalman filter so probe temperatures track ramps with low lag and stop "jumping".

**Architecture:** A new `probes/kalman.py` provides a `TempKalman` class with a 2-state `[temperature, rate]` model. It does predict + innovation-gate + update in one `update(reading)` call, measures its own timestep, rejects outlier spikes, and handles disconnected-probe `None` reads. `probes/base.py` constructs one filter per port and calls `update()` in the read loop.

**Tech Stack:** Python 3, standard library only (`time`, no numpy). pytest for tests.

## Global Constraints

- **Indentation: TAB characters**, not spaces — matches the whole repo (`probes/base.py`, `probes/temp_queue.py`, existing tests).
- **Standard library only** — no numpy or third-party math libs. All matrix math is written out by hand (2×2).
- Filter output is a **float rounded to one decimal** for both `F` and `C` units.
- Tuning constants are **hardcoded module-level values**, not read from `settings.json`.
- Design reference: `docs/superpowers/specs/2026-07-05-kalman-probe-filter-design.md`.

---

### Task 1: Core Kalman filter (valid-reading path)

**Files:**
- Create: `probes/kalman.py`
- Test: `tests/test_kalman.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `TempKalman(units='F')` — constructor. Sets `self.R`, `self.q`, `self.gate2`, and calls `self.reset()`.
  - `TempKalman.reset() -> None` — clears state (`self.x=None`, `self.v=0.0`, `self.P`, `self.last_time=None`, `self.none_streak=0`).
  - `TempKalman.update(reading, now=None) -> float | None` — returns the filtered temperature rounded to 1 decimal. `now` is an injectable monotonic timestamp in seconds (tests pass it explicitly; production leaves it `None` so the filter reads `time.monotonic()`). In this task `update` handles only non-`None` readings; `None` handling and the outlier gate arrive in Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kalman.py`:

```python
import math
import random
import statistics

from probes.kalman import TempKalman


def _feed_constant(kf, value, steps, dt=0.05, start=0.0):
	t = start
	out = None
	for _ in range(steps):
		t += dt
		out = kf.update(value, now=t)
	return out, t


def test_converges_to_constant():
	kf = TempKalman(units='F')
	out, _ = _feed_constant(kf, 250.0, steps=60)
	assert abs(out - 250.0) < 0.5


def test_first_reading_returns_immediately():
	kf = TempKalman(units='F')
	out = kf.update(137.0, now=0.05)
	assert out == 137.0


def test_reduces_noise_on_constant():
	rng = random.Random(0)
	kf = TempKalman(units='F')
	ins, outs = [], []
	t = 0.0
	for i in range(300):
		t += 0.05
		z = 250.0 + rng.gauss(0, 2.0)
		o = kf.update(z, now=t)
		if i >= 20:
			ins.append(z)
			outs.append(o)
	assert statistics.pstdev(outs) < statistics.pstdev(ins)


def test_tracks_ramp_with_low_lag():
	kf = TempKalman(units='F')
	rate, dt = 1.5, 0.05
	t, temp, out = 0.0, 100.0, None
	for _ in range(400):
		temp += rate * dt
		t += dt
		out = kf.update(temp, now=t)
	lag = (temp - out) / rate
	assert -0.2 < lag < 0.2


def test_irregular_dt_stays_stable():
	rng = random.Random(1)
	kf = TempKalman(units='F')
	t, out = 0.0, None
	for _ in range(200):
		t += 0.05 + rng.uniform(-0.02, 0.05)
		out = kf.update(250.0, now=t)
	assert math.isfinite(out)
	assert abs(out - 250.0) < 1.0


def test_celsius_returns_one_decimal_and_scaled_tuning():
	kf = TempKalman(units='C')
	assert kf.R == 1.25
	out = kf.update(100.0, now=0.05)
	assert isinstance(out, float)
	assert out == 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_kalman.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'probes.kalman'`

- [ ] **Step 3: Write the module (valid-reading path only)**

Create `probes/kalman.py` (TAB-indented):

```python
#!/usr/bin/env python3

"""
Constant-velocity Kalman filter for smoothing probe temperature readings.

Replaces the standard-deviation-gated moving average (TempQueue). Estimates
both temperature and its rate of change, so it tracks ramps with little lag
while smoothing noise.
"""

import time

# Tuning constants selected by units at construction.
#   R    : measurement variance (sensor noise squared)
#   q    : white-acceleration process-noise spectral density
#   gate : reject readings farther than this many sigma from the prediction
_TUNING = {
	'F': {'R': 4.0, 'q': 0.5, 'gate': 5.0},
	'C': {'R': 1.25, 'q': 0.15, 'gate': 5.0},
}

_DT_MIN = 0.01
_DT_MAX = 1.0


class TempKalman:
	def __init__(self, units='F'):
		tuning = _TUNING['C'] if units == 'C' else _TUNING['F']
		self.units = units
		self.R = tuning['R']
		self.q = tuning['q']
		self.gate2 = tuning['gate'] ** 2
		self.reset()

	def reset(self):
		self.x = None			# temperature estimate
		self.v = 0.0			# rate estimate (deg/sec)
		self.P = [[self.R, 0.0], [0.0, self.R]]
		self.last_time = None
		self.none_streak = 0

	def update(self, reading, now=None):
		if now is None:
			now = time.monotonic()

		# First valid reading (fresh or post-reset): initialize, don't predict.
		if self.x is None or self.last_time is None:
			self.x = float(reading)
			self.v = 0.0
			self.P = [[self.R, 0.0], [0.0, self.R]]
			self.last_time = now
			return round(self.x, 1)

		dt = now - self.last_time
		if dt < _DT_MIN:
			dt = _DT_MIN
		elif dt > _DT_MAX:
			dt = _DT_MAX
		self.last_time = now

		# --- Predict: x = F x ; P = F P F^T + Q  (F = [[1, dt], [0, 1]]) ---
		self.x += self.v * dt
		P = self.P
		p00 = P[0][0] + dt * (P[1][0] + P[0][1]) + dt * dt * P[1][1]
		p01 = P[0][1] + dt * P[1][1]
		p10 = P[1][0] + dt * P[1][1]
		p11 = P[1][1]
		dt2 = dt * dt
		dt3 = dt2 * dt
		dt4 = dt3 * dt
		p00 += self.q * dt4 / 4.0
		p01 += self.q * dt3 / 2.0
		p10 += self.q * dt3 / 2.0
		p11 += self.q * dt2

		# --- Update (measure temperature only, H = [1, 0]) ---
		y = reading - self.x
		s = p00 + self.R
		k0 = p00 / s
		k1 = p10 / s
		self.x += k0 * y
		self.v += k1 * y
		self.P = [
			[(1 - k0) * p00, (1 - k0) * p01],
			[p10 - k1 * p00, p11 - k1 * p01],
		]
		return round(self.x, 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_kalman.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add probes/kalman.py tests/test_kalman.py
git commit -m "feat(probes): add constant-velocity Kalman temperature filter

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Outlier gate and disconnected-probe (`None`) handling

**Files:**
- Modify: `probes/kalman.py` (the `update` method)
- Test: `tests/test_kalman.py` (add cases)

**Interfaces:**
- Consumes: `TempKalman` from Task 1.
- Produces: `update(reading, now=None)` now (a) returns `None` for a `None` reading, resetting after 3 consecutive `None`s, and (b) rejects readings whose squared normalized innovation `y²/S` exceeds `gate2`, holding the predicted estimate. Adds module constant `_NONE_RESET_THRESHOLD = 3`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kalman.py`:

```python
def test_rejects_single_spike():
	kf = TempKalman(units='F')
	_, t = _feed_constant(kf, 250.0, steps=40)
	before = kf.update(250.0, now=t + 0.05)
	after = kf.update(900.0, now=t + 0.10)
	assert abs(after - before) < 1.0


def test_none_reading_returns_none():
	kf = TempKalman(units='F')
	kf.update(250.0, now=0.05)
	assert kf.update(None) is None


def test_resets_after_three_nones():
	kf = TempKalman(units='F')
	_, t = _feed_constant(kf, 250.0, steps=40)
	assert kf.update(None) is None
	assert kf.update(None) is None
	assert kf.update(None) is None
	# After reset the next valid reading re-initializes and is returned as-is.
	assert kf.update(100.0, now=t + 0.05) == 100.0


def test_single_none_keeps_state_warm():
	kf = TempKalman(units='F')
	out, t = _feed_constant(kf, 250.0, steps=40)
	assert kf.update(None) is None
	resumed = kf.update(250.0, now=t + 0.10)
	# One dropped read must not force a re-init; estimate stays near 250.
	assert abs(resumed - 250.0) < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_kalman.py -k "spike or none or nones or warm" -v`
Expected: FAIL — `test_none_reading_returns_none` raises `TypeError` (subtracting from `None`), and the spike test fails because the outlier is currently blended in.

- [ ] **Step 3: Add the `None` guard and gate to `update`**

In `probes/kalman.py`, add the threshold constant next to the others:

```python
_DT_MIN = 0.01
_DT_MAX = 1.0
_NONE_RESET_THRESHOLD = 3
```

Replace the entire `update` method with this version:

```python
	def update(self, reading, now=None):
		if reading is None:
			self.none_streak += 1
			if self.none_streak >= _NONE_RESET_THRESHOLD:
				self.reset()
			return None

		self.none_streak = 0
		if now is None:
			now = time.monotonic()

		# First valid reading (fresh or post-reset): initialize, don't predict.
		if self.x is None or self.last_time is None:
			self.x = float(reading)
			self.v = 0.0
			self.P = [[self.R, 0.0], [0.0, self.R]]
			self.last_time = now
			return round(self.x, 1)

		dt = now - self.last_time
		if dt < _DT_MIN:
			dt = _DT_MIN
		elif dt > _DT_MAX:
			dt = _DT_MAX
		self.last_time = now

		# --- Predict: x = F x ; P = F P F^T + Q  (F = [[1, dt], [0, 1]]) ---
		self.x += self.v * dt
		P = self.P
		p00 = P[0][0] + dt * (P[1][0] + P[0][1]) + dt * dt * P[1][1]
		p01 = P[0][1] + dt * P[1][1]
		p10 = P[1][0] + dt * P[1][1]
		p11 = P[1][1]
		dt2 = dt * dt
		dt3 = dt2 * dt
		dt4 = dt3 * dt
		p00 += self.q * dt4 / 4.0
		p01 += self.q * dt3 / 2.0
		p10 += self.q * dt3 / 2.0
		p11 += self.q * dt2

		# --- Gate: reject readings too far from the prediction ---
		y = reading - self.x
		s = p00 + self.R
		if (y * y) / s > self.gate2:
			self.P = [[p00, p01], [p10, p11]]
			return round(self.x, 1)

		# --- Update (measure temperature only, H = [1, 0]) ---
		k0 = p00 / s
		k1 = p10 / s
		self.x += k0 * y
		self.v += k1 * y
		self.P = [
			[(1 - k0) * p00, (1 - k0) * p01],
			[p10 - k1 * p00, p11 - k1 * p01],
		]
		return round(self.x, 1)
```

- [ ] **Step 4: Run the full filter test file to verify all pass**

Run: `python -m pytest tests/test_kalman.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add probes/kalman.py tests/test_kalman.py
git commit -m "feat(probes): add innovation gate and None handling to Kalman filter

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire the filter into the probe read loop; remove `TempQueue`

**Files:**
- Modify: `probes/base.py:24` (import), `probes/base.py:233-235` (construction), `probes/base.py:349-355` (read loop)
- Delete: `probes/temp_queue.py`

**Interfaces:**
- Consumes: `TempKalman` from Tasks 1–2. `self.units` is already set on the object at `probes/base.py:176`.
- Produces: no new public interface; `read_all_ports()` output structure is unchanged (still fills `output_data['primary'|'food'|'aux']`), values are now filtered floats.

- [ ] **Step 1: Confirm `TempQueue` has no other consumers**

Run: `grep -rn "temp_queue\|TempQueue" --include=*.py .`
Expected: only matches in `probes/base.py` and `probes/temp_queue.py` itself. If anything else references it, stop and reassess.

- [ ] **Step 2: Swap the import**

In `probes/base.py`, change line 24 from:

```python
from probes.temp_queue import TempQueue
```

to:

```python
from probes.kalman import TempKalman
```

- [ ] **Step 3: Swap the per-port construction**

In `probes/base.py` `_build_ports()` (around lines 233-235), change:

```python
		self.port_queues = {}
		for port in self.port_map:
			self.port_queues[port] = TempQueue(qlength=10, units=self.units)
```

to:

```python
		self.port_filters = {}
		for port in self.port_map:
			self.port_filters[port] = TempKalman(units=self.units)
```

- [ ] **Step 4: Swap the read-loop call**

In `probes/base.py` `read_all_ports()` (around lines 349-355), change:

```python
			""" Enqueue the Temperature Readings to Port Queues """
			if port_values[port] == None:
				""" If the read value is None, pass that to the output instead of adding to the queue """
				output_value = None
			else:
				self.port_queues[port].enqueue(port_values[port])
				output_value = self.port_queues[port].average()
```

to:

```python
			""" Filter the Temperature Reading (Kalman); None passes through """
			output_value = self.port_filters[port].update(port_values[port])
```

- [ ] **Step 5: Delete the obsolete module**

```bash
git rm probes/temp_queue.py
```

- [ ] **Step 6: Verify nothing imports the deleted module and the suite passes**

Run: `grep -rn "temp_queue\|TempQueue" --include=*.py .`
Expected: no matches.

Run: `python -m pytest tests/ -q`
Expected: PASS — the full suite, including the probe/device tests that exercise `probes/base.py` (e.g. `tests/test_x86_ramp.py`, `tests/test_max31856_probe.py`) and the new `tests/test_kalman.py`.

- [ ] **Step 7: Commit**

```bash
git add probes/base.py
git commit -m "feat(probes): use Kalman filter in read loop, remove TempQueue

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Accepted behavior change (documented in the spec):** the notification
  `'equal'` condition at `notify/notifications.py:715` (`current == target`)
  becomes effectively unfireable now that °F output is a float. This is
  intentional and out of scope — do **not** change notifications.
- **Startup difference:** the old `TempQueue` returned `0` for the first ~500 ms
  until its window filled; `TempKalman` returns the first real reading
  immediately. This is expected and covered by `test_first_reading_returns_immediately`.
- Do not add `settings.json` knobs for the tuning constants — hardcoded by design.
