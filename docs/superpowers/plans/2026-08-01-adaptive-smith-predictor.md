# Adaptive Smith Predictor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PID-SP's fixed rate-of-change extrapolator with a Smith predictor driven by an FOPDT model the controller identifies online from applied duty and measured temperature.

**Architecture:** Two new leaf modules. `controller/fopdt_identifier.py` runs a bank of 25 recursive-least-squares estimators — one per dead-time candidate, 0 to 120 s in 5 s steps — as a single batched numpy update, and promotes a candidate only when profile-independent trust gates clear. `controller/smith_predictor.py` owns the trusted parameters, the two model states, and the safety envelope. `controller/pid_sp.py` composes them and models nothing itself: it selects one temperature per tick and that single value drives P, I and D.

**Tech Stack:** Python 3.14+, numpy (now an explicit dependency), pytest, jujutsu (`jj`), `uv`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-08-01-adaptive-smith-predictor-design.md`. Read the sections `Process Model and Smith Equation`, `fopdt_identifier.py`, `smith_predictor.py`, `pid_sp.py`, `pid_base.py`, `controllers.json`, `Confidence and Safety`, and `Verification` before Task 1.

**Depends entirely on Plan A** (`docs/superpowers/plans/2026-08-01-controller-applied-output-plumbing.md`). Do not start until Plan A's completion checklist is green: PID-SP cannot receive applied duty, publish diagnostics, or persist a model without it.

## Global Constraints

Every task's requirements implicitly include this section.

- **Commit with `jj`, never `git`.** Colocated repo — `git commit` silently works and causes damage. `jj new` *before* the first Write of a task; `jj describe --stdin` for the message (no `-F` flag). Never `jj squash` after editing. Recovery is `jj op restore`.
- **Format before every commit:** `.venv/bin/ruff format <changed files>`. NEVER `uvx ruff` (the repo pins `ruff>=0.8.0,<0.16`).
- **Run tests with `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`.**
- **Python 3.14+ except syntax:** `except ValueError, TypeError:` (no parentheses) is ruff-canonical here. Do not "fix" it.
- **Comments state intent, not history.**
- **Canonical units are Fahrenheit** inside the identifier and the predictor; convert at the boundary. A persisted gain must mean the same thing regardless of the configured units.
- **Time enters as an argument, never from a clock.** Every identifier and predictor entry point takes an explicit timestamp — `record_output` from `AppliedOutput.timestamp`, `observe` and `temperature` from the caller. Neither class calls `time.time()` at all, so neither needs an injected clock and tests need no fake one. Only `controller/pid_sp.py` reads the wall clock, through the module-global `time.time` the golden test monkeypatches. (The spec says "the clock is injected"; explicit timestamps are the stronger form of the same requirement and make the injection unnecessary.)
- **numpy only.** Beyond numpy, the identifier and predictor are standard library. No scipy, no sklearn.
- **Per-update work and memory are bounded and fixed-size.** Stacked arrays of fixed shape; duty history retained only as far back as the largest candidate delay plus one sample interval.
- **No deliberate excitation.** Identification is passive; nothing this plan adds may perturb the auger command.
- **GrillSim is the plant of record.** Do not substitute `HiFiGrill`.
- **Do not run `git clean -fdx`** — it destroys the git-ignored SDD ledger.
- If you are a subagent: use `ctx_execute` / serena rather than dumping raw output into context, and ingest your outcome to the Hindsight bank `claude_code` when you finish.

### Trust gates (copy these values exactly)

| Gate | Threshold |
| --- | ---: |
| Accepted observation time | at least 3600 s |
| Accepted observations | at least 240 |
| Applied-duty standard deviation | at least 0.05 |
| Sustained duty transition | change of at least 0.05 held for 60 s |
| Observed temperature span | at least 15 F |
| Process gain `K` | 50-2000 F per unit duty |
| Time constant `tau` | 300-20000 s |
| Gain relative standard error | at most 20% |
| Tau relative standard error | at most 25% |
| Winning delay residual | at least 10% below the runner-up |
| Confirmation window | 20 accepted observations |
| Confirmation stability | gain within 5%, tau within 7.5%, unchanged delay candidate |
| Material revision (post-trust) | gain or tau moves at least 5%, or delay moves at least 5 s |
| Revision blend factor | 0.1 for gain and tau; delay only after a full confirmation window |

### Predictor safety envelope

| Condition | Action |
| --- | --- |
| Model state or predicted temperature non-finite | disable prediction, reinitialize both branches equally |
| Predicted temperature outside -100 to 1200 F | same |
| One-step residual above 100 F for 4 consecutive accepted observations | same |

## File Structure

| File | Responsibility |
| --- | --- |
| `controller/pid_base.py` | The `ti <= 0` guard. One line. |
| `controller/fopdt_identifier.py` (new) | `DutyHistory` (piecewise-constant duty + cumulative integral) and `FOPDTIdentifier` (the batched RLS bank, trust gates, promotion, diagnostics). |
| `controller/smith_predictor.py` (new) | `SmithPredictor`: trusted parameters, the undelayed and delayed model states, segmented integration, the safety envelope. |
| `controller/pid_sp.py` | Composes both. Implements the four capabilities. No modelling of its own. |
| `controller/controllers.json` | Drop `tau`/`theta`; declare the numpy dependency. |
| `pyproject.toml` | numpy becomes an explicit top-level dependency. |
| `tests/characterization/test_pid_variants_golden.py` | PID-SP's golden series is regenerated; its config loses `tau`/`theta`. |

Tests:

| Test file | Covers |
| --- | --- |
| `tests/unit/controller/test_duty_history.py` (new) | Cumulative integral, segmentation, pruning, coverage. |
| `tests/unit/controller/test_fopdt_identifier.py` (new) | The scalar oracle, accuracy against a synthetic FOPDT plant, gates, rejection, bounded memory. |
| `tests/unit/controller/test_smith_predictor.py` (new) | Exact trajectories, segmented integration, the Smith equation, equal-state init, the safety envelope. |
| `tests/unit/controller/test_pid_sp.py` (new) | Composition, `pid_ac` equivalence when untrusted, the 0.65 fix, the four capabilities. |
| `tests/unit/controller/test_pid_base.py` (new) | The `ti <= 0` guard. |

## Parallelization

Task 1 is independent of everything and can land first or concurrently.

Two chains then run in parallel in isolated `jj` workspaces:

- **Chain I (identifier):** 2 → 3 → 4 → 5
- **Chain P (predictor):** 6 → 7 — needs only `DutyHistory` from Task 2, so it can start as soon as Task 2 lands.

Tasks 8 onward are strictly sequential: 8 needs 5 and 7; 9 needs 8; 10 and 11 need 9; 12 needs everything.

Concurrency requires isolated `jj` workspaces — disjoint file lists alone are not enough. Copy `.lsp.json` into each workspace (it is gitignored, so `jj workspace add` skips it).

---

### Task 1: The `ti <= 0` guard in `pid_base.py`

`_calculate_gains` guards `if ti == 0`, which lets a negative `Ti` through and yields a sign-flipped `ki` — an integral term that drives the output the wrong way. This fixes every PID variant at once and has no effect on any valid configuration.

Add a second unguarded division in the same pass: a floor on the elapsed time PID-SP and PID-AC divide by.

`update()` computes `dt = time.time() - self.last_update` and then divides by it three times. Two of those cancel — `roc = (current - self.last) / dt` is multiplied by `1 - exp(-dt / tau)`, and the `dt` dependencies annihilate as `dt` shrinks — but `derv = (predicted_temp - self.last) / dt` has no such cancellation and diverges as `1 / dt`. At `dt == 0` it raises.

`dt == 0` is not exotic. `set_target` assigns `self.last_update = time.time()`, and float64's ULP at the current epoch is 238 ns, so back-to-back `time.time()` calls return the *identical* float about 82% of the time. Any caller that retargets and then solves without an intervening sleep divides by zero. Today the only such call site — `ThreadedControllerRunner._loop` — is unreachable (its `set_target` has no callers, and the threaded runner is selected only for MPC, which never reads the wall clock), so this is hardening, not a live-bug fix. It is worth doing anyway: Plan A Task 6 rewrites that very loop, and the scenario harness hit the crash on first contact.

Floor `dt` at a small positive value rather than returning early — a controller that silently skips an update is harder to diagnose than one that clamps. Put the floor in `pid_base.py` so both variants inherit it, and pin it with a test that calls `update()` twice at the same clock value and asserts a finite in-range output rather than an exception.

**Files:**
- Modify: `controller/pid_base.py`
- Test: `tests/unit/controller/test_pid_base.py` (create)

**Interfaces:**
- Produces: nothing new. Behavior change only, and only for configurations that were already broken.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: pid_base ti guard"
```

Create `tests/unit/controller/test_pid_base.py`:

```python
"""_calculate_gains must never produce a sign-flipped integral gain."""

import pytest

from controller.pid_base import PIDControllerBase


class _Gains(PIDControllerBase):
    def __init__(self):
        pass


@pytest.mark.parametrize("ti", [0, 0.0, -1.0, -180.0])
def test_non_positive_ti_disables_the_integral_term(ti):
    gains = _Gains()
    gains._calculate_gains(60.0, ti, 45.0)
    assert gains.ki == 0


def test_positive_ti_is_unchanged():
    gains = _Gains()
    gains._calculate_gains(60.0, 180.0, 45.0)
    assert gains.ki == pytest.approx((-1 / 60.0) / 180.0)


def test_zero_pb_disables_the_proportional_term():
    gains = _Gains()
    gains._calculate_gains(0, 180.0, 45.0)
    assert gains.kp == 0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_pid_base.py -q`
Expected: FAIL on the negative `ti` cases — `ki` is a positive number instead of 0.

- [ ] **Step 3: Fix the guard**

In `controller/pid_base.py::_calculate_gains`, change `if ti == 0:` to:

```python
        if ti <= 0:
```

- [ ] **Step 4: Run the tests and the goldens**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/ tests/characterization/test_pid_variants_golden.py -q`
Expected: PASS, goldens unchanged — every shipped config uses a positive `Ti`.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/pid_base.py tests/unit/controller/test_pid_base.py
jj describe --stdin <<'EOF'
fix(pid): reject a non-positive Ti instead of only zero

A negative Ti passed the ti == 0 guard and produced a sign-flipped ki, so the
integral term drove the output away from setpoint. No shipped configuration is
affected; the goldens do not move.
EOF
```

---

### Task 2: `DutyHistory` — piecewise-constant duty with a cumulative integral

The delayed-duty regressor is needed for 25 candidates at once, and the predictor needs to integrate its model states across delay boundaries. Both come from one structure: applied duty as a step function with a running integral, so a delayed average is a `searchsorted` plus a linear interpolation instead of 25 windowed scans.

**Files:**
- Create: `controller/fopdt_identifier.py`
- Test: `tests/unit/controller/test_duty_history.py`

**Interfaces:**
- Produces: `DutyHistory(max_delay)` with `.record(timestamp, ratio)`, `.integral(times) -> np.ndarray`, `.average(t_start, t_end, delays) -> (np.ndarray, np.ndarray)` returning values and a validity mask, `.segments(t_start, t_end) -> list[(duration, duty)]`, `.prune(now)`, `.earliest() -> float | None`, `.__len__()`. Tasks 3, 5 and 6 all consume these exact names.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: duty history"
```

Create `tests/unit/controller/test_duty_history.py`:

```python
"""Duty is a step function; its integral is exact, not approximated."""

import numpy as np
import pytest

from controller.fopdt_identifier import DutyHistory


def _history(pairs, max_delay=120.0):
    h = DutyHistory(max_delay)
    for t, u in pairs:
        h.record(t, u)
    return h


def test_integral_is_exact_for_a_constant_duty():
    h = _history([(0.0, 0.4)])
    assert h.integral(np.array([0.0, 10.0, 25.0])) == pytest.approx([0.0, 4.0, 10.0])


def test_integral_accumulates_across_segments():
    h = _history([(0.0, 0.4), (10.0, 0.8), (20.0, 0.0)])
    # 0-10 at 0.4 = 4.0; 10-20 at 0.8 = 8.0 -> 12.0; then flat
    assert h.integral(np.array([10.0, 20.0, 30.0])) == pytest.approx([4.0, 12.0, 12.0])


def test_integral_interpolates_inside_a_segment():
    h = _history([(0.0, 0.4), (10.0, 0.8)])
    assert h.integral(np.array([15.0])) == pytest.approx([4.0 + 0.8 * 5.0])


def test_the_last_duty_stays_in_force_after_the_last_record():
    h = _history([(0.0, 0.5)])
    assert h.integral(np.array([100.0])) == pytest.approx([50.0])


def test_average_over_a_window_that_straddles_several_segments():
    h = _history([(0.0, 1.0), (10.0, 0.0), (20.0, 1.0), (30.0, 0.0)])
    # window [5, 35) with zero delay: 5s at 1.0, 10s at 0.0, 10s at 1.0, 5s at 0.0
    values, valid = h.average(5.0, 35.0, np.array([0.0]))
    assert valid.all()
    assert values == pytest.approx([(5.0 + 0.0 + 10.0 + 0.0) / 30.0])


def test_average_shifts_the_window_by_each_delay():
    h = _history([(0.0, 0.0), (100.0, 1.0)])
    values, valid = h.average(150.0, 160.0, np.array([0.0, 60.0, 120.0]))
    assert valid.tolist() == [True, True, False]  # 150-120=30 predates nothing recorded? see below
    assert values[0] == pytest.approx(1.0)  # [150,160) is entirely at duty 1.0
    assert values[1] == pytest.approx(1.0)  # [90,100) is at 0.0 ... see the note


def test_a_window_reaching_before_retained_history_is_invalid():
    h = _history([(100.0, 0.5)])
    values, valid = h.average(150.0, 160.0, np.array([0.0, 60.0]))
    # 150-60 = 90 < 100, the earliest thing we know
    assert valid.tolist() == [True, False]


def test_segments_splits_a_window_at_every_duty_change():
    h = _history([(0.0, 0.2), (10.0, 0.8), (25.0, 0.5)])
    assert h.segments(5.0, 30.0) == [
        pytest.approx((5.0, 0.2)),
        pytest.approx((15.0, 0.8)),
        pytest.approx((5.0, 0.5)),
    ]


def test_segments_of_an_empty_window_is_empty():
    h = _history([(0.0, 0.4)])
    assert h.segments(10.0, 10.0) == []


def test_a_repeated_duty_does_not_create_a_segment():
    h = _history([(0.0, 0.4), (10.0, 0.4), (20.0, 0.4)])
    assert len(h) == 1


def test_pruning_bounds_memory():
    h = DutyHistory(120.0)
    for t in range(0, 100_000, 10):
        h.record(float(t), (t // 10) % 2 * 0.5)
        h.prune(float(t))
    # max_delay 120s at one change per 10s is ~13 segments; allow slack, forbid growth
    assert len(h) < 40


def test_pruning_keeps_enough_history_for_the_largest_delay():
    h = DutyHistory(120.0)
    for t in range(0, 1000, 10):
        h.record(float(t), (t // 10) % 2 * 0.5)
        h.prune(float(t))
    assert h.earliest() <= 990.0 - 120.0


def test_a_non_advancing_timestamp_is_ignored():
    h = _history([(10.0, 0.4)])
    h.record(5.0, 0.9)
    h.record(10.0, 0.9)
    assert len(h) == 1
    assert h.integral(np.array([20.0])) == pytest.approx([4.0])
```

Two of the assertions above have inline notes because the window arithmetic is easy to get backwards. Work them out by hand from `[t_start - theta, t_end - theta)` before implementing, and correct the expected values in the test if your hand calculation disagrees — then make the implementation match your hand calculation, not the other way round.

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_duty_history.py -q`
Expected: FAIL — `ImportError: cannot import name 'DutyHistory'`.

- [ ] **Step 3: Write `DutyHistory`**

Create `controller/fopdt_identifier.py`:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire FOPDT Identifier
*****************************************

 Description: Online identification of a first-order-plus-dead-time grill model
 from applied auger duty and measured temperature.

     T(t) = T_offset + x_d(t)
     dx/dt = (K * u - x) / tau
     x_d(t) = x(t - theta)

 Dead time is not estimated continuously. A bank of recursive-least-squares
 estimators runs one candidate delay each, 0 to 120 s in 5 s steps, and the bank
 is a single batched numpy update rather than a loop -- fixed shapes, bounded
 work, no Python iteration over candidates anywhere.

*****************************************
"""

import numpy as np

#: Dead-time candidates, seconds.
DELAYS = np.arange(0.0, 125.0, 5.0)
N_CANDIDATES = DELAYS.size


class DutyHistory:
    """Applied auger duty as a step function, with a running cumulative integral.

    An auger is on or off, so duty between reports really is piecewise constant
    and the integral is exact rather than approximated. That turns a delayed
    window average -- needed for every candidate delay on every observation --
    into one searchsorted plus a linear interpolation.
    """

    def __init__(self, max_delay):
        self._max_delay = float(max_delay)
        self._t = []  # segment start times
        self._u = []  # duty in force from _t[i] until _t[i + 1]
        self._i = []  # integral of duty dt from _t[0] to _t[i]
        self._ta = np.empty(0)
        self._ua = np.empty(0)
        self._ia = np.empty(0)

    def __len__(self):
        return len(self._t)

    def earliest(self):
        return self._t[0] if self._t else None

    def record(self, timestamp, ratio):
        """Append a duty segment. Ignores a non-advancing timestamp or a repeat."""
        timestamp = float(timestamp)
        ratio = float(ratio)
        if self._t:
            if timestamp <= self._t[-1]:
                return
            if ratio == self._u[-1]:
                return
            self._i.append(self._i[-1] + self._u[-1] * (timestamp - self._t[-1]))
        else:
            self._i.append(0.0)
        self._t.append(timestamp)
        self._u.append(ratio)
        self._sync()

    def _sync(self):
        self._ta = np.asarray(self._t, dtype=float)
        self._ua = np.asarray(self._u, dtype=float)
        self._ia = np.asarray(self._i, dtype=float)

    def integral(self, times):
        """Integral of duty from the earliest retained time to each of `times`.

        Times after the last record extrapolate the last duty forward, which is
        what the auger is actually doing until the next report.
        """
        times = np.asarray(times, dtype=float)
        if self._ta.size == 0:
            return np.zeros_like(times)
        idx = np.clip(np.searchsorted(self._ta, times, side="right") - 1, 0, self._ta.size - 1)
        return self._ia[idx] + self._ua[idx] * np.maximum(times - self._ta[idx], 0.0)

    def average(self, t_start, t_end, delays):
        """Mean duty over [t_start - theta, t_end - theta) for every theta.

        Returns (values, valid). A candidate is invalid when its window reaches
        back before the earliest retained segment: there is no duty to average
        there, and guessing one would fabricate an observation.
        """
        delays = np.asarray(delays, dtype=float)
        span = float(t_end) - float(t_start)
        if span <= 0.0 or self._ta.size == 0:
            return np.zeros_like(delays), np.zeros(delays.shape, dtype=bool)
        lo = float(t_start) - delays
        hi = float(t_end) - delays
        values = (self.integral(hi) - self.integral(lo)) / span
        return values, lo >= self._ta[0]

    def segments(self, t_start, t_end):
        """[(duration, duty)] covering [t_start, t_end), split at every change."""
        t_start, t_end = float(t_start), float(t_end)
        if t_end <= t_start or self._ta.size == 0:
            return []
        edges = [t_start]
        edges.extend(t for t in self._t if t_start < t < t_end)
        edges.append(t_end)
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            if hi <= lo:
                continue
            idx = max(int(np.searchsorted(self._ta, lo, side="right")) - 1, 0)
            out.append((hi - lo, float(self._ua[idx])))
        return out

    def prune(self, now):
        """Drop segments no candidate delay can still reach."""
        horizon = float(now) - self._max_delay
        keep = 0
        while keep + 1 < len(self._t) and self._t[keep + 1] <= horizon:
            keep += 1
        if keep:
            del self._t[:keep]
            del self._u[:keep]
            del self._i[:keep]
            self._sync()
```

Note `prune` keeps the segment *containing* the horizon, not merely those after it: the duty in force at `now - max_delay` is part of the oldest window a candidate can ask for.

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_duty_history.py -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/fopdt_identifier.py tests/unit/controller/test_duty_history.py
jj describe --stdin <<'EOF'
feat(controller): add DutyHistory, applied duty as an integrable step function

An auger is on or off, so duty between reports really is piecewise constant and
its integral is exact. That turns the delayed window average -- needed for every
dead-time candidate on every observation -- into one searchsorted plus a linear
interpolation, instead of 25 windowed scans.

A window reaching back before retained history is reported invalid rather than
filled in: guessing there would fabricate an observation.
EOF
```

---

### Task 3: The batched RLS bank, guarded by a scalar oracle

The bank and the duty lookup are the two places where a wrong `einsum` subscript, a transposed axis, or an off-by-one produces plausible numbers instead of an error. The oracle is therefore written **first**, from the equations in the spec — not by refactoring the implementation, which would prove only that the code agrees with itself.

**Files:**
- Modify: `controller/fopdt_identifier.py`
- Test: `tests/unit/controller/test_fopdt_identifier.py` (create)

**Interfaces:**
- Consumes: `DutyHistory`, `DELAYS`, `N_CANDIDATES` (Task 2).
- Produces: `RLSBank(n_candidates)` with `.Theta` `(N, 3)`, `.P` `(N, 3, 3)`, `.resid_ew` `(N,)`, `.update(phi, y)`, `.reset(mask)`; module constants `LAM`, `P0`, `EW_ALPHA`, `T_REF`, `T_SCALE`.

- [ ] **Step 1: `jj new`, then write the oracle and the parity test**

```bash
jj new -m "wip: rls bank"
```

Create `tests/unit/controller/test_fopdt_identifier.py`:

```python
"""A deliberately naive reference guards the vectorized bank.

Both references below are written from the equations in
docs/superpowers/specs/2026-08-01-adaptive-smith-predictor-design.md, NOT
adapted from the production code. A reference derived by refactoring the
implementation proves only that it agrees with itself.
"""

import numpy as np
import pytest

from controller.fopdt_identifier import DELAYS, EW_ALPHA, LAM, P0, DutyHistory, RLSBank


# ---------------------------------------------------------------- scalar oracle


def _oracle_rls(observations, n):
    """One plain 3x3 RLS update per candidate, in a Python loop."""
    theta = [np.zeros(3) for _ in range(n)]
    p = [P0 * np.eye(3) for _ in range(n)]
    resid = [0.0] * n
    for phi_all, y in observations:
        for j in range(n):
            phi = np.asarray(phi_all[j], dtype=float)
            pphi = p[j] @ phi
            denom = LAM + float(phi @ pphi)
            gain = pphi / denom
            err = float(y - phi @ theta[j])
            theta[j] = theta[j] + gain * err
            p[j] = (p[j] - np.outer(gain, pphi)) / LAM
            p[j] = 0.5 * (p[j] + p[j].T)
            resid[j] = EW_ALPHA * err**2 + (1.0 - EW_ALPHA) * resid[j]
    return np.array(theta), np.array(p), np.array(resid)


def _oracle_delayed_average(records, t_start, t_end, delays):
    """Average duty over [t_start - theta, t_end - theta) by direct scan."""
    out, valid = [], []
    for theta in delays:
        lo, hi = t_start - theta, t_end - theta
        if lo < records[0][0]:
            out.append(0.0)
            valid.append(False)
            continue
        total = 0.0
        for k, (t, u) in enumerate(records):
            seg_lo = t
            seg_hi = records[k + 1][0] if k + 1 < len(records) else max(hi, t)
            a, b = max(seg_lo, lo), min(seg_hi, hi)
            if b > a:
                total += u * (b - a)
        out.append(total / (t_end - t_start))
        valid.append(True)
    return np.asarray(out), np.asarray(valid)


# ------------------------------------------------------------------- sequences


def _messy_sequence(seed=7):
    """Variable dt, windows straddling several segments and reaching back before
    retained history, duty constant over some stretches and stepping over others."""
    rng = np.random.default_rng(seed)
    records, t, u = [], 0.0, 0.15
    for k in range(60):
        if k % 7 < 3:
            pass  # hold the duty constant for a stretch
        else:
            u = float(rng.choice([0.0, 0.15, 0.4, 0.75, 0.9]))
        records.append((t, u))
        t += float(rng.uniform(3.0, 40.0))
    return records


# ------------------------------------------------------------------------ tests


def test_delayed_average_matches_the_direct_scan():
    records = _messy_sequence()
    history = DutyHistory(float(DELAYS.max()))
    for t, u in records:
        history.record(t, u)
    # collapse repeats the way DutyHistory does, so the oracle sees the same steps
    collapsed = [records[0]]
    for t, u in records[1:]:
        if u != collapsed[-1][1]:
            collapsed.append((t, u))

    for t_start in (200.0, 450.0, 700.0):
        t_end = t_start + 30.0
        got, got_valid = history.average(t_start, t_end, DELAYS)
        want, want_valid = _oracle_delayed_average(collapsed, t_start, t_end, DELAYS)
        assert got_valid.tolist() == want_valid.tolist()
        np.testing.assert_allclose(got[got_valid], want[want_valid], rtol=1e-12, atol=1e-12)


def test_batched_bank_matches_the_scalar_loop():
    rng = np.random.default_rng(11)
    n = DELAYS.size
    observations = []
    for _ in range(200):
        shared = np.array([1.0, rng.normal(0.0, 1.0)])
        third = rng.uniform(0.0, 1.0, size=n)
        phi_all = np.column_stack([np.repeat(shared[0], n), np.repeat(shared[1], n), third])
        observations.append((phi_all, float(rng.normal(0.0, 0.05))))

    bank = RLSBank(n)
    for phi_all, y in observations:
        bank.update(phi_all, y)
    theta, p, resid = _oracle_rls(observations, n)

    np.testing.assert_allclose(bank.Theta, theta, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(bank.P, p, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(bank.resid_ew, resid, rtol=1e-9, atol=1e-12)


def test_covariance_stays_symmetric():
    rng = np.random.default_rng(3)
    bank = RLSBank(DELAYS.size)
    for _ in range(500):
        phi = rng.normal(size=(DELAYS.size, 3))
        bank.update(phi, float(rng.normal()))
    np.testing.assert_allclose(bank.P, bank.P.transpose(0, 2, 1), rtol=0, atol=0)


def test_reset_clears_only_the_masked_candidates():
    rng = np.random.default_rng(5)
    bank = RLSBank(DELAYS.size)
    for _ in range(50):
        bank.update(rng.normal(size=(DELAYS.size, 3)), float(rng.normal()))
    before = bank.Theta.copy()
    mask = np.zeros(DELAYS.size, dtype=bool)
    mask[3] = True
    bank.reset(mask)
    assert np.all(bank.Theta[3] == 0.0)
    np.testing.assert_allclose(bank.P[3], P0 * np.eye(3))
    assert bank.resid_ew[3] == 0.0
    np.testing.assert_allclose(bank.Theta[~mask], before[~mask])
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_fopdt_identifier.py -q`
Expected: FAIL — `ImportError: cannot import name 'RLSBank'`.

- [ ] **Step 3: Write the bank**

Append to `controller/fopdt_identifier.py`:

```python
#: Forgetting factor. Slow enough that an hour of observations still counts,
#: fast enough that a re-seasoned grill is eventually re-learned.
LAM = 0.9995
#: Initial covariance: no prior belief about the coefficients.
P0 = 1e6
#: Weight of the newest squared residual in the exponentially weighted mean.
EW_ALPHA = 0.02

#: Temperature regressors are centered and scaled before each update and
#: transformed back afterwards -- absolute grill temperatures condition the
#: matrix badly. The reference is FIXED rather than a running mean: a moving
#: reference would silently change the meaning of the covariance already
#: accumulated under the old one.
T_REF = 250.0
T_SCALE = 100.0


class RLSBank:
    """One recursive-least-squares estimator per dead-time candidate, batched.

    Theta is (N, 3), P is (N, 3, 3), resid_ew is (N,). Only the third regressor
    column differs per candidate; [1, T_scaled] is shared and broadcast by the
    caller.
    """

    def __init__(self, n_candidates):
        self._n = int(n_candidates)
        self.Theta = np.zeros((self._n, 3))
        self.P = np.tile(P0 * np.eye(3), (self._n, 1, 1))
        self.resid_ew = np.zeros(self._n)

    def update(self, phi, y):
        """One accepted observation into the whole bank. `phi` is (N, 3)."""
        phi = np.asarray(phi, dtype=float)
        Pphi = np.einsum("nij,nj->ni", self.P, phi)
        denom = LAM + np.einsum("ni,ni->n", phi, Pphi)
        gain = Pphi / denom[:, None]
        err = y - np.einsum("ni,ni->n", phi, self.Theta)
        self.Theta += gain * err[:, None]
        self.P = (self.P - np.einsum("ni,nj->nij", gain, Pphi)) / LAM
        # Hold P symmetric against accumulated float drift.
        self.P = 0.5 * (self.P + self.P.transpose(0, 2, 1))
        self.resid_ew = EW_ALPHA * err**2 + (1.0 - EW_ALPHA) * self.resid_ew
        self._reset_degenerate()

    def reset(self, mask):
        """Return the masked candidates to their initial state."""
        mask = np.asarray(mask, dtype=bool)
        if not mask.any():
            return
        self.Theta[mask] = 0.0
        self.P[mask] = P0 * np.eye(3)
        self.resid_ew[mask] = 0.0

    def _reset_degenerate(self):
        """A candidate that has lost positive-definiteness or gone non-finite
        starts over rather than poisoning the bank."""
        bad = ~np.isfinite(self.Theta).all(axis=1)
        bad |= ~np.isfinite(self.P).all(axis=(1, 2))
        bad |= ~np.isfinite(self.resid_ew)
        diag = np.einsum("nii->ni", self.P)
        bad |= (diag <= 0.0).any(axis=1)
        self.reset(bad)
```

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_fopdt_identifier.py -q`
Expected: PASS.

- [ ] **Step 5: Negative-control the oracle**

A parity test that passes against a broken implementation is worse than no test. Prove it fails when the production path is wrong. Run each of these three perturbations, confirm `test_batched_bank_matches_the_scalar_loop` FAILS, then revert:

1. In `RLSBank.update`, change `LAM` in the `denom` line to `LAM * 0.999`.
2. Change `np.einsum("nij,nj->ni", self.P, phi)` to `np.einsum("nji,nj->ni", self.P, phi)`.
3. Change `EW_ALPHA * err**2` to `EW_ALPHA * err`.

For the duty oracle, do the same with `test_delayed_average_matches_the_direct_scan`: change `side="right"` to `side="left"` in `DutyHistory.integral` and confirm it FAILS.

Record all four results in your task report. If any perturbation leaves the test passing, the oracle is not actually checking that path — fix the oracle before continuing.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/fopdt_identifier.py tests/unit/controller/test_fopdt_identifier.py
jj describe --stdin <<'EOF'
feat(controller): add the batched RLS bank for the dead-time candidates

25 estimators as one einsum update: fixed shapes, bounded work, no Python loop
over candidates. A scalar reference written from the design equations -- not
refactored out of the implementation -- checks Theta, P and resid_ew across
every candidate, and is itself negative-controlled by perturbing the forgetting
factor, a covariance index and the residual accumulator.

The temperature reference is fixed rather than a running mean: a moving
reference would change the meaning of covariance already accumulated under the
old one.
EOF
```

---

### Task 4: Parameter recovery, uncertainty, and promotion

**Files:**
- Modify: `controller/fopdt_identifier.py`
- Test: `tests/unit/controller/test_fopdt_identifier.py`

**Interfaces:**
- Consumes: `RLSBank` (Task 3).
- Produces: `recover_parameters(Theta) -> dict` of `(N,)` arrays `K`, `tau`, `T_offset`; `relative_standard_errors(Theta, P, resid_ew) -> (rse_K, rse_tau)`; `gate_mask(params, rse_K, rse_tau) -> np.ndarray[bool]`; `promote(resid_ew, mask) -> (winner_index | None, margin)`. Task 5 composes these.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: parameter recovery and gates"
```

Append to `tests/unit/controller/test_fopdt_identifier.py`:

```python
from controller.fopdt_identifier import (
    GAIN_MAX,
    GAIN_MIN,
    T_REF,
    T_SCALE,
    TAU_MAX,
    TAU_MIN,
    gate_mask,
    promote,
    recover_parameters,
    relative_standard_errors,
)


def _theta_for(K, tau, T_offset, n=1):
    """Invert the recovery: build the coefficient row a true model produces."""
    beta_T = -1.0 / tau
    beta_u = -K * beta_T
    beta_0 = -T_offset * beta_T
    # scaled coefficients: c_T = beta_T * T_SCALE, c_0 = beta_0 + beta_T * T_REF
    return np.tile([beta_0 + beta_T * T_REF, beta_T * T_SCALE, beta_u], (n, 1))


def test_recovery_inverts_a_known_model():
    theta = _theta_for(K=800.0, tau=600.0, T_offset=70.0, n=3)
    params = recover_parameters(theta)
    np.testing.assert_allclose(params["K"], 800.0)
    np.testing.assert_allclose(params["tau"], 600.0)
    np.testing.assert_allclose(params["T_offset"], 70.0)


def test_recovery_masks_a_degenerate_candidate_instead_of_raising():
    theta = np.array([[0.0, 0.0, 0.0]])  # beta_T == 0 -> tau is infinite
    params = recover_parameters(theta)
    assert not np.isfinite(params["tau"]).any() or params["tau"][0] > TAU_MAX


@pytest.mark.parametrize(
    "K,tau,expected",
    [
        (800.0, 600.0, True),
        (GAIN_MIN - 1.0, 600.0, False),
        (GAIN_MAX + 1.0, 600.0, False),
        (-800.0, 600.0, False),
        (800.0, TAU_MIN - 1.0, False),
        (800.0, TAU_MAX + 1.0, False),
        (800.0, -600.0, False),
    ],
)
def test_gate_mask_rejects_unphysical_estimates(K, tau, expected):
    params = recover_parameters(_theta_for(K=K, tau=tau, T_offset=70.0))
    mask = gate_mask(params, rse_K=np.array([0.05]), rse_tau=np.array([0.05]))
    assert bool(mask[0]) is expected


def test_gate_mask_rejects_an_uncertain_estimate():
    params = recover_parameters(_theta_for(K=800.0, tau=600.0, T_offset=70.0))
    assert not gate_mask(params, rse_K=np.array([0.25]), rse_tau=np.array([0.05]))[0]
    assert not gate_mask(params, rse_K=np.array([0.05]), rse_tau=np.array([0.30]))[0]


def test_gate_mask_rejects_a_non_finite_estimate():
    params = {"K": np.array([np.nan]), "tau": np.array([600.0]), "T_offset": np.array([70.0])}
    assert not gate_mask(params, rse_K=np.array([0.05]), rse_tau=np.array([0.05]))[0]


def test_relative_standard_errors_shrink_as_the_residual_shrinks():
    theta = _theta_for(K=800.0, tau=600.0, T_offset=70.0, n=2)
    P = np.tile(np.eye(3) * 1e-4, (2, 1, 1))
    loud = relative_standard_errors(theta, P, np.array([1.0, 1.0]))
    quiet = relative_standard_errors(theta, P, np.array([1e-6, 1e-6]))
    assert quiet[0][0] < loud[0][0]
    assert quiet[1][0] < loud[1][0]


def test_promote_requires_a_clear_margin_over_the_runner_up():
    mask = np.ones(4, dtype=bool)
    # winner 10% below runner-up exactly: accepted at the boundary
    winner, margin = promote(np.array([0.90, 1.00, 1.20, 1.50]), mask)
    assert winner == 0
    assert margin == pytest.approx(0.10)
    # indistinguishable: refused
    winner, margin = promote(np.array([0.99, 1.00, 1.20, 1.50]), mask)
    assert winner is None


def test_promote_ignores_gated_out_candidates():
    mask = np.array([False, True, True, True])
    winner, _ = promote(np.array([0.01, 0.90, 1.50, 1.60]), mask)
    assert winner == 1


def test_promote_refuses_when_nothing_passes_the_gates():
    winner, _ = promote(np.array([0.1, 0.2]), np.zeros(2, dtype=bool))
    assert winner is None


def test_promote_refuses_with_a_single_surviving_candidate():
    """One candidate cannot be 10% better than a runner-up that does not exist."""
    winner, _ = promote(np.array([0.1, 0.2]), np.array([True, False]))
    assert winner is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_fopdt_identifier.py -q`
Expected: FAIL — `ImportError: cannot import name 'recover_parameters'`.

- [ ] **Step 3: Implement recovery, uncertainty and promotion**

Append to `controller/fopdt_identifier.py`:

```python
#: Physical bounds a grill's identified model must satisfy.
GAIN_MIN, GAIN_MAX = 50.0, 2000.0  # F per unit duty
TAU_MIN, TAU_MAX = 300.0, 20000.0  # seconds
RSE_K_MAX = 0.20
RSE_TAU_MAX = 0.25
#: A winner must beat the runner-up by this fraction of its residual.
PROMOTION_MARGIN = 0.10


def recover_parameters(Theta):
    """Physical parameters from the scaled regression coefficients.

    Undoes the fixed centering and scaling, then
    tau = -1/beta_T, K = -beta_u/beta_T, T_offset = -beta_0/beta_T.
    A candidate whose recovery is not finite comes back non-finite rather than
    raising; gate_mask drops it.
    """
    Theta = np.atleast_2d(np.asarray(Theta, dtype=float))
    c0, cT, cu = Theta[:, 0], Theta[:, 1], Theta[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        beta_T = cT / T_SCALE
        beta_0 = c0 - beta_T * T_REF
        tau = -1.0 / beta_T
        K = -cu / beta_T
        T_offset = -beta_0 / beta_T
    return {"K": K, "tau": tau, "T_offset": T_offset}


def relative_standard_errors(Theta, P, resid_ew):
    """Delta-method relative standard errors for K and tau.

    tau is proportional to 1/c_T, so its relative error is c_T's. K is a ratio
    of two estimated coefficients, so its relative variance carries their
    covariance term.
    """
    Theta = np.atleast_2d(np.asarray(Theta, dtype=float))
    P = np.asarray(P, dtype=float)
    resid_ew = np.asarray(resid_ew, dtype=float)
    cT, cu = Theta[:, 1], Theta[:, 2]
    var_T = resid_ew * P[:, 1, 1]
    var_u = resid_ew * P[:, 2, 2]
    cov_uT = resid_ew * P[:, 2, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        rel_T2 = var_T / cT**2
        rel_u2 = var_u / cu**2
        rse_tau = np.sqrt(np.maximum(rel_T2, 0.0))
        rse_K = np.sqrt(np.maximum(rel_u2 + rel_T2 - 2.0 * cov_uT / (cu * cT), 0.0))
    return rse_K, rse_tau


def gate_mask(params, rse_K, rse_tau):
    """Candidates whose estimate is finite, physical and sufficiently certain."""
    K, tau = np.asarray(params["K"]), np.asarray(params["tau"])
    rse_K, rse_tau = np.asarray(rse_K), np.asarray(rse_tau)
    with np.errstate(invalid="ignore"):
        mask = np.isfinite(K) & np.isfinite(tau) & np.isfinite(rse_K) & np.isfinite(rse_tau)
        mask &= (K >= GAIN_MIN) & (K <= GAIN_MAX)
        mask &= (tau >= TAU_MIN) & (tau <= TAU_MAX)
        mask &= rse_K <= RSE_K_MAX
        mask &= rse_tau <= RSE_TAU_MAX
    return mask


def promote(resid_ew, mask):
    """The winning candidate index and its margin, or (None, 0.0).

    A candidate is never promoted merely for having the lowest residual. If the
    best two are statistically indistinguishable there is no evidence for either
    delay, and refusing keeps the controller on measured temperature.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.sum() < 2:
        return None, 0.0
    resid = np.where(mask, np.asarray(resid_ew, dtype=float), np.inf)
    best, runner_up = np.partition(resid, 1)[:2]
    if not np.isfinite(runner_up) or runner_up <= 0.0:
        return None, 0.0
    margin = (runner_up - best) / runner_up
    if margin < PROMOTION_MARGIN:
        return None, 0.0
    return int(np.argmin(resid)), float(margin)
```

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_fopdt_identifier.py -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/fopdt_identifier.py tests/unit/controller/test_fopdt_identifier.py
jj describe --stdin <<'EOF'
feat(controller): recover FOPDT parameters and gate them on physics and certainty

Recovery undoes the fixed scaling and comes back non-finite rather than raising
on a degenerate candidate. Gates are a boolean mask over the whole bank: finite,
inside the physical bands, and inside the delta-method uncertainty limits.

Promotion needs a clear margin over the runner-up. A candidate is never promoted
merely for having the lowest residual -- if the best two are indistinguishable
there is no evidence for either delay, and refusing keeps the controller on
measured temperature.
EOF
```

---

### Task 5: `FOPDTIdentifier` — intake, rejection, confirmation, diagnostics

**Files:**
- Modify: `controller/fopdt_identifier.py`
- Test: `tests/unit/controller/test_fopdt_identifier.py`

**Interfaces:**
- Consumes: everything from Tasks 2-4.
- Produces: `FOPDTIdentifier()` with `.record_output(applied)`, `.observe(temperature_f, timestamp) -> bool`, `.trusted_model() -> dict | None` returning `{"K", "tau", "theta", "revision"}`, `.restore(model) -> bool`, `.status() -> dict`.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: fopdt identifier"
```

Append to `tests/unit/controller/test_fopdt_identifier.py`:

```python
from controller.applied_output import AppliedOutput, OutputSource
from controller.fopdt_identifier import FOPDTIdentifier


class _FOPDTPlant:
    """The exact process the identifier assumes. A true answer exists here."""

    def __init__(self, K=800.0, tau=600.0, theta=35.0, T_offset=70.0, dt=20.0):
        self.K, self.tau, self.theta, self.T_offset, self.dt = K, tau, theta, T_offset, dt
        self.x = 0.0
        self.t = 0.0
        self._history = [(0.0, 0.0)]

    def step(self, u):
        self._history.append((self.t, u))
        # delayed input
        target = self.t - self.theta
        u_d = self._history[0][1]
        for ts, uu in self._history:
            if ts <= target:
                u_d = uu
        self.x += (self.K * u_d - self.x) / self.tau * self.dt
        self.t += self.dt
        return self.T_offset + self.x


def _drive(identifier, plant, duties, commanded=True):
    """Run the plant on a duty schedule, reporting and observing each step."""
    for u in duties:
        identifier.record_output(
            AppliedOutput(
                ratio=u,
                source=OutputSource.CONTROLLER if commanded else OutputSource.LID_OPEN,
                timestamp=plant.t,
            )
        )
        temp = plant.step(u)
        identifier.observe(temp, plant.t)


def _excitation_schedule(n, dt=20.0):
    """Alternating duty levels with sustained transitions, long enough to clear
    the 3600 s and 240-observation gates."""
    out = []
    for k in range(n):
        out.append(0.25 if (k // 30) % 2 == 0 else 0.55)
    return out


def test_identifies_a_synthetic_fopdt_plant():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=800.0, tau=600.0, theta=35.0)
    _drive(identifier, plant, _excitation_schedule(600))
    model = identifier.trusted_model()
    assert model is not None, identifier.status()
    assert model["K"] == pytest.approx(800.0, rel=0.10)
    assert model["tau"] == pytest.approx(600.0, rel=0.15)
    assert abs(model["theta"] - 35.0) <= 5.0


def test_no_promotion_under_constant_duty():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    _drive(identifier, plant, [0.4] * 600)
    assert identifier.trusted_model() is None
    assert identifier.status()["duty_std"] < 0.05


def test_no_promotion_without_enough_temperature_span():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=5.0)  # barely moves the temperature
    _drive(identifier, plant, _excitation_schedule(600))
    assert identifier.trusted_model() is None


def test_no_promotion_before_the_time_gate():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    _drive(identifier, plant, _excitation_schedule(100))  # 2000 s < 3600 s
    assert identifier.trusted_model() is None
    assert identifier.status()["accepted_seconds"] < 3600.0


def test_a_paused_interval_creates_no_cross_gap_observation():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    _drive(identifier, plant, _excitation_schedule(300))
    before = identifier.status()["accepted"]
    _drive(identifier, plant, [0.0] * 10, commanded=False)
    assert identifier.status()["accepted"] == before
    # the first observation AFTER the gap is also rejected: it would span it
    _drive(identifier, plant, _excitation_schedule(1))
    assert identifier.status()["accepted"] == before
    _drive(identifier, plant, _excitation_schedule(2))
    assert identifier.status()["accepted"] > before


def test_a_non_finite_temperature_is_rejected():
    identifier = FOPDTIdentifier()
    identifier.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    identifier.observe(200.0, 0.0)
    before = identifier.status()["accepted"]
    assert identifier.observe(float("nan"), 20.0) is False
    assert identifier.status()["accepted"] == before


@pytest.mark.parametrize("dt", [0.0, -20.0, 100000.0])
def test_an_implausible_dt_is_rejected(dt):
    identifier = FOPDTIdentifier()
    identifier.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    identifier.observe(200.0, 100.0)
    before = identifier.status()["accepted"]
    identifier.observe(210.0, 100.0 + dt)
    assert identifier.status()["accepted"] == before


def test_memory_is_bounded():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    _drive(identifier, plant, _excitation_schedule(3000))
    assert identifier.status()["duty_segments"] < 40
    assert identifier.Theta.shape == (DELAYS.size, 3)


def test_restore_adopts_a_valid_model_and_rejects_an_impossible_one():
    identifier = FOPDTIdentifier()
    assert identifier.restore({"K": 800.0, "tau": 600.0, "theta": 35.0, "revision": 4}) is True
    assert identifier.trusted_model()["K"] == 800.0
    assert identifier.trusted_model()["revision"] == 4
    assert identifier.restore({"K": -1.0, "tau": 600.0, "theta": 35.0, "revision": 5}) is False
    assert identifier.restore({"K": 800.0, "tau": 1.0, "theta": 35.0, "revision": 5}) is False
    assert identifier.restore({"K": 800.0, "tau": 600.0, "theta": 999.0, "revision": 5}) is False
    assert identifier.restore({"K": 800.0, "tau": 600.0}) is False


def test_the_revision_advances_only_on_a_material_change():
    identifier = FOPDTIdentifier()
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 35.0, "revision": 1})
    rev = identifier.trusted_model()["revision"]
    plant = _FOPDTPlant(K=802.0, tau=602.0, theta=35.0)  # within the material band
    _drive(identifier, plant, _excitation_schedule(600))
    assert identifier.trusted_model()["revision"] == rev


def test_status_reports_what_the_gates_are_waiting_for():
    identifier = FOPDTIdentifier()
    status = identifier.status()
    for key in (
        "accepted",
        "accepted_seconds",
        "duty_std",
        "temp_span",
        "duty_segments",
        "best_residual",
        "runner_up_residual",
        "trusted",
        "candidates_passing",
    ):
        assert key in status
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_fopdt_identifier.py -q`
Expected: FAIL — `ImportError: cannot import name 'FOPDTIdentifier'`.

- [ ] **Step 3: Implement the identifier**

Append to `controller/fopdt_identifier.py`:

```python
#: Trust gates. Profile-independent, so no cook shape is privileged.
MIN_ACCEPTED_SECONDS = 3600.0
MIN_ACCEPTED = 240
MIN_DUTY_STD = 0.05
MIN_TRANSITION = 0.05
MIN_TRANSITION_HOLD = 60.0
MIN_TEMP_SPAN_F = 15.0
CONFIRM_WINDOW = 20
CONFIRM_K_TOL = 0.05
CONFIRM_TAU_TOL = 0.075
#: After initial trust, a candidate is a revision only when it moves this far.
MATERIAL_K = 0.05
MATERIAL_TAU = 0.05
MATERIAL_THETA = 5.0
#: How much of a passing revision blends into the trusted values.
BLEND = 0.1
#: A dt outside this band is a clock jump or a stalled loop, not an observation.
DT_MIN, DT_MAX = 1.0, 600.0


class FOPDTIdentifier:
    """Passive online identification of the grill's FOPDT parameters.

    Nothing here perturbs the auger: the identifier learns from whatever
    excitation the controller's own regulation happens to produce, and stays
    untrusted until the gates say the data earned it.
    """

    def __init__(self):
        self._bank = RLSBank(N_CANDIDATES)
        self._history = DutyHistory(float(DELAYS.max()))
        self._prev = None  # (timestamp, temperature) anchor
        self._gap = True  # the next observation would span an undriven interval
        self._accepted = 0
        self._accepted_seconds = 0.0
        self._temp_lo = None
        self._temp_hi = None
        self._duty_n = 0
        self._duty_sum = 0.0
        self._duty_sq = 0.0
        self._transition_seen = False
        self._transition_from = None
        self._transition_at = None
        self._trusted = None
        self._revision = 0
        self._confirm = None

    # -------------------------------------------------------------- properties
    @property
    def Theta(self):
        return self._bank.Theta

    # ------------------------------------------------------------------ intake
    def record_output(self, applied):
        """Take an AppliedOutput. Every command enters the duty history -- the
        grill really did run at that duty -- but one the controller did not
        command opens a gap that suppresses identification across it."""
        now = float(applied.timestamp)
        self._history.record(now, applied.ratio)
        self._history.prune(now)
        if not applied.controller_commanded:
            self._gap = True
            return
        self._note_transition(now, applied.ratio)

    def _note_transition(self, now, ratio):
        """A sustained duty change is the excitation this design waits for."""
        if self._transition_from is None:
            self._transition_from, self._transition_at = ratio, now
            return
        if abs(ratio - self._transition_from) >= MIN_TRANSITION:
            if now - self._transition_at >= MIN_TRANSITION_HOLD:
                self._transition_seen = True
            self._transition_from, self._transition_at = ratio, now

    def observe(self, temperature_f, timestamp):
        """One temperature sample. True when it became a regression row."""
        now = float(timestamp)
        temp = float(temperature_f)
        if not np.isfinite(temp):
            self._prev = None
            self._gap = True
            return False
        prev, self._prev = self._prev, (now, temp)
        if prev is None or self._gap:
            self._gap = False
            return False
        t0, y0 = prev
        dt = now - t0
        if not (DT_MIN <= dt <= DT_MAX):
            return False
        duty, valid = self._history.average(t0, now, DELAYS)
        if not valid.any():
            return False
        # A candidate whose window predates retained history contributes its
        # last known duty rather than dropping the whole observation.
        duty = np.where(valid, duty, duty[valid][0])

        shared = np.array([1.0, (y0 - T_REF) / T_SCALE])
        phi = np.empty((N_CANDIDATES, 3))
        phi[:, 0] = shared[0]
        phi[:, 1] = shared[1]
        phi[:, 2] = duty
        self._bank.update(phi, (temp - y0) / dt)

        self._accepted += 1
        self._accepted_seconds += dt
        self._temp_lo = temp if self._temp_lo is None else min(self._temp_lo, temp)
        self._temp_hi = temp if self._temp_hi is None else max(self._temp_hi, temp)
        mean_duty = float(duty[valid].mean())
        self._duty_n += 1
        self._duty_sum += mean_duty
        self._duty_sq += mean_duty * mean_duty
        self._evaluate()
        return True

    # ------------------------------------------------------------------- trust
    def _duty_std(self):
        if self._duty_n < 2:
            return 0.0
        mean = self._duty_sum / self._duty_n
        var = max(self._duty_sq / self._duty_n - mean * mean, 0.0)
        return float(np.sqrt(var))

    def _excited(self):
        return (
            self._accepted >= MIN_ACCEPTED
            and self._accepted_seconds >= MIN_ACCEPTED_SECONDS
            and self._duty_std() >= MIN_DUTY_STD
            and self._transition_seen
            and self._temp_lo is not None
            and (self._temp_hi - self._temp_lo) >= MIN_TEMP_SPAN_F
        )

    def _evaluate(self):
        if not self._excited():
            return
        params = recover_parameters(self._bank.Theta)
        rse_K, rse_tau = relative_standard_errors(self._bank.Theta, self._bank.P, self._bank.resid_ew)
        mask = gate_mask(params, rse_K, rse_tau)
        winner, _ = promote(self._bank.resid_ew, mask)
        if winner is None:
            self._confirm = None
            return
        candidate = {
            "K": float(params["K"][winner]),
            "tau": float(params["tau"][winner]),
            "theta": float(DELAYS[winner]),
        }
        if self._trusted is not None and not self._material(candidate):
            self._confirm = None
            return
        if not self._confirmed(candidate):
            return
        self._adopt(candidate)

    def _material(self, candidate):
        return (
            abs(candidate["K"] - self._trusted["K"]) / self._trusted["K"] >= MATERIAL_K
            or abs(candidate["tau"] - self._trusted["tau"]) / self._trusted["tau"] >= MATERIAL_TAU
            or abs(candidate["theta"] - self._trusted["theta"]) >= MATERIAL_THETA
        )

    def _confirmed(self, candidate):
        """A candidate must hold still for a full window before it is believed."""
        window = self._confirm
        if window is None or window["theta"] != candidate["theta"]:
            self._confirm = {"n": 1, **candidate}
            return False
        if (
            abs(candidate["K"] - window["K"]) / window["K"] > CONFIRM_K_TOL
            or abs(candidate["tau"] - window["tau"]) / window["tau"] > CONFIRM_TAU_TOL
        ):
            self._confirm = {"n": 1, **candidate}
            return False
        window["n"] += 1
        window["K"], window["tau"] = candidate["K"], candidate["tau"]
        return window["n"] >= CONFIRM_WINDOW

    def _adopt(self, candidate):
        self._confirm = None
        if self._trusted is None:
            self._trusted = dict(candidate)
        else:
            # Delay moves outright once confirmed; the continuous parameters
            # blend, so one noisy window cannot swing the model.
            self._trusted = {
                "K": (1.0 - BLEND) * self._trusted["K"] + BLEND * candidate["K"],
                "tau": (1.0 - BLEND) * self._trusted["tau"] + BLEND * candidate["tau"],
                "theta": candidate["theta"],
            }
        self._revision += 1

    def trusted_model(self):
        if self._trusted is None:
            return None
        return {**self._trusted, "revision": self._revision}

    def restore(self, model):
        """Adopt a persisted model, re-checking the physics the store does not
        judge. A restored model is trusted immediately: a process restart is not
        a reason to doubt parameters that were earned."""
        if not isinstance(model, dict):
            return False
        try:
            K, tau, theta = float(model["K"]), float(model["tau"]), float(model["theta"])
            revision = int(model["revision"])
        except KeyError, TypeError, ValueError:
            return False
        if not all(np.isfinite([K, tau, theta])) or revision < 0:
            return False
        if not (GAIN_MIN <= K <= GAIN_MAX) or not (TAU_MIN <= tau <= TAU_MAX):
            return False
        if theta < float(DELAYS.min()) or theta > float(DELAYS.max()):
            return False
        self._trusted = {"K": K, "tau": tau, "theta": theta}
        self._revision = revision
        return True

    def status(self):
        resid = self._bank.resid_ew
        ordered = np.partition(resid, 1)[:2] if resid.size > 1 else np.array([0.0, 0.0])
        params = recover_parameters(self._bank.Theta)
        rse_K, rse_tau = relative_standard_errors(self._bank.Theta, self._bank.P, self._bank.resid_ew)
        return {
            "accepted": self._accepted,
            "accepted_seconds": round(self._accepted_seconds, 1),
            "duty_std": round(self._duty_std(), 4),
            "temp_span": round((self._temp_hi - self._temp_lo) if self._temp_lo is not None else 0.0, 2),
            "transition_seen": self._transition_seen,
            "duty_segments": len(self._history),
            "best_residual": float(ordered[0]),
            "runner_up_residual": float(ordered[1]),
            "candidates_passing": int(gate_mask(params, rse_K, rse_tau).sum()),
            "confirming": None if self._confirm is None else self._confirm["n"],
            "trusted": self.trusted_model(),
        }
```

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_fopdt_identifier.py -q`
Expected: PASS. If `test_identifies_a_synthetic_fopdt_plant` fails on tolerance, print `identifier.status()` and find which gate is blocking before touching a threshold — the thresholds are the spec's, not yours to tune.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/fopdt_identifier.py tests/unit/controller/test_fopdt_identifier.py
jj describe --stdin <<'EOF'
feat(controller): add the passive FOPDT identifier

Learns K, tau and theta from whatever excitation the controller's own regulation
produces -- nothing here perturbs the auger -- and stays untrusted until the
gates say the data earned it.

An interval the controller did not command still enters duty history, because
the grill really did run at that duty; what it opens is a gap that suppresses
identification across it, so no regression row spans time the controller did not
drive.
EOF
```

---

### Task 6: `SmithPredictor` — the two model states

**Files:**
- Create: `controller/smith_predictor.py`
- Test: `tests/unit/controller/test_smith_predictor.py`

**Interfaces:**
- Consumes: `DutyHistory` (Task 2).
- Produces: `SmithPredictor()` with `.record_output(applied)`, `.trust(model)`, `.temperature(measured_f, timestamp) -> float`, `.active` (bool), `.status() -> dict`, `.reset()`.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: smith predictor states"
```

Create `tests/unit/controller/test_smith_predictor.py`:

```python
"""The predictor's two branches are exact first-order trajectories, and the
correction between them starts at exactly zero."""

import math

import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.smith_predictor import SmithPredictor

MODEL = {"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1}


def _predictor():
    return SmithPredictor()


def test_returns_measured_temperature_until_trusted():
    p = _predictor()
    p.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    assert p.temperature(212.0, 20.0) == 212.0
    assert p.active is False


def test_the_correction_is_exactly_zero_at_the_moment_of_trust():
    p = _predictor()
    p.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 20.0)
    p.trust(MODEL)
    assert p.temperature(212.0, 20.0) == 212.0
    assert p.active is True


def test_the_undelayed_branch_follows_the_exact_first_order_solution():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 600.0)
    # x0 starts at 0 and is driven by u=0.5 for 600 s with tau=600
    expected = MODEL["K"] * 0.5 * (1.0 - math.exp(-600.0 / MODEL["tau"]))
    assert p.status()["x0"] == pytest.approx(expected, rel=1e-9)


def test_the_delayed_branch_lags_by_exactly_theta():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 40.0)
    # at t = theta the delayed branch has seen no input at all
    assert p.status()["xd"] == pytest.approx(0.0, abs=1e-9)


def test_integration_splits_at_a_duty_change_between_samples():
    """A duty change landing between two controller updates is integrated in two
    segments, not rounded to the sample interval."""
    p = _predictor()
    p.trust({"K": 800.0, "tau": 600.0, "theta": 0.0, "revision": 1})
    p.record_output(AppliedOutput(0.0, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.record_output(AppliedOutput(1.0, OutputSource.CONTROLLER, 10.0))
    p.temperature(212.0, 30.0)
    # 10 s at u=0 leaves x at 0; then 20 s at u=1
    expected = 800.0 * (1.0 - math.exp(-20.0 / 600.0))
    assert p.status()["x0"] == pytest.approx(expected, rel=1e-9)


def test_the_smith_equation_is_measured_plus_the_state_difference():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    out = p.temperature(215.0, 300.0)
    status = p.status()
    assert out == pytest.approx(215.0 + status["x0"] - status["xd"])


def test_a_constant_offset_cancels_out_of_the_correction():
    """The unknown T_offset is not part of the persisted model, and must not be
    needed: the correction is a difference of two states driven by the same K."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    a = p.temperature(212.0, 300.0)

    q = _predictor()
    q.trust(MODEL)
    q.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    q.temperature(500.0, 0.0)
    b = q.temperature(500.0, 300.0)
    assert (a - 212.0) == pytest.approx(b - 500.0)


def test_reset_reinitializes_both_branches_equally():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.9, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 900.0)
    assert p.status()["x0"] != p.status()["xd"]
    p.reset()
    assert p.status()["x0"] == p.status()["xd"]
    assert p.temperature(212.0, 920.0) == 212.0


def test_units_are_canonical_fahrenheit():
    """A gain identified in F means the same thing whatever the display units."""
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 600.0)
    assert p.status()["x0"] == pytest.approx(800.0 * 0.5 * (1.0 - math.exp(-1.0)), rel=1e-9)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_smith_predictor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.smith_predictor'`.

- [ ] **Step 3: Write the predictor**

Create `controller/smith_predictor.py`:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire Smith Predictor
*****************************************

 Description: Removes identified dead time from the temperature a controller
 sees, without giving up feedback.

 Two model states are driven by the same applied-duty history: x0 by the duty as
 it is applied, xd by the same history shifted back by theta. The controller
 input is the measured-output Smith form

     T_smith = T_measured + x0 - xd

 The measured term preserves feedback for plant/model mismatch and for
 disturbances the model knows nothing about; the difference removes the
 identified delay from that signal. The unknown constant offset appears in both
 branches identically and cancels, which is why it is estimated for
 identification but never persisted.

 Canonically Fahrenheit inside, so a persisted gain means the same thing
 regardless of the configured units.

*****************************************
"""

import math

from controller.fopdt_identifier import DELAYS, DutyHistory

#: Prediction outside this band is not a grill temperature.
TEMP_MIN_F, TEMP_MAX_F = -100.0, 1200.0
#: A one-step residual this large means the model has stopped describing the plant.
MAX_RESIDUAL_F = 100.0
MAX_RESIDUAL_STREAK = 4


class SmithPredictor:
    def __init__(self):
        self._history = DutyHistory(float(DELAYS.max()))
        self._model = None
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._last_prediction = None
        self._residual_streak = 0
        self._disabled = False

    @property
    def active(self):
        return self._model is not None and not self._disabled

    def record_output(self, applied):
        self._history.record(applied.timestamp, applied.ratio)
        self._history.prune(applied.timestamp)

    def trust(self, model):
        """Adopt identified parameters.

        Going from untrusted to trusted starts both branches equal, so the
        correction begins at exactly zero and control never steps.

        A later revision of K or tau updates in place and KEEPS the states: the
        identifier only revises after a confirmation window, and snapping a
        live correction back to zero would be the very step equal-state
        initialization exists to avoid. A revision of theta does reinitialize,
        because the delayed state was accumulated under the old delay and no
        longer means what it did.

        A disable is sticky. PID-SP re-asserts the trusted model every tick, so
        clearing the flag on any trust() call would undo a safety disable on the
        next tick and leave the envelope doing nothing.
        """
        if model is None:
            return
        incoming = {"K": float(model["K"]), "tau": float(model["tau"]), "theta": float(model["theta"])}
        if self._model is None:
            self._model = incoming
            self.reset()
            return
        if self._disabled:
            if incoming != self._model:
                self._model = incoming
                self.reset()
            return
        if incoming["theta"] != self._model["theta"]:
            self._model = incoming
            self.reset()
            return
        self._model = incoming

    def reset(self):
        self._x0 = 0.0
        self._xd = 0.0
        self._last_t = None
        self._last_prediction = None
        self._residual_streak = 0
        self._disabled = False

    def temperature(self, measured_f, timestamp):
        """The temperature the controller should regulate on."""
        measured = float(measured_f)
        now = float(timestamp)
        if not self.active:
            self._last_t = now
            return measured
        if self._last_t is None:
            self._last_t = now
            self._last_prediction = measured
            return measured

        self._integrate(self._last_t, now)
        self._last_t = now

        predicted = measured + self._x0 - self._xd
        if not self._safe(predicted, measured):
            self.reset()
            self._disabled = True
            return measured
        self._last_prediction = predicted
        return predicted

    def _integrate(self, t0, t1):
        """Advance both branches, splitting at every duty change.

        A command due between two controller updates splits the integration into
        segments; delay is never rounded to the controller interval.
        """
        tau = self._model["tau"]
        gain = self._model["K"]
        theta = self._model["theta"]
        for duration, duty in self._history.segments(t0, t1):
            self._x0 = self._step(self._x0, duty, duration, gain, tau)
        for duration, duty in self._history.segments(t0 - theta, t1 - theta):
            self._xd = self._step(self._xd, duty, duration, gain, tau)

    @staticmethod
    def _step(x, u, dt, gain, tau):
        """Exact first-order response to a constant input over dt."""
        decay = math.exp(-dt / tau)
        return x * decay + gain * u * (1.0 - decay)

    def _safe(self, predicted, measured):
        if not math.isfinite(predicted) or not math.isfinite(self._x0) or not math.isfinite(self._xd):
            return False
        if not TEMP_MIN_F <= predicted <= TEMP_MAX_F:
            return False
        if self._last_prediction is not None and abs(measured - self._last_prediction) > MAX_RESIDUAL_F:
            self._residual_streak += 1
        else:
            self._residual_streak = 0
        return self._residual_streak < MAX_RESIDUAL_STREAK

    def status(self):
        return {
            "active": self.active,
            "disabled": self._disabled,
            "x0": self._x0,
            "xd": self._xd,
            "residual_streak": self._residual_streak,
            "model": None if self._model is None else dict(self._model),
        }
```

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_smith_predictor.py -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/smith_predictor.py tests/unit/controller/test_smith_predictor.py
jj describe --stdin <<'EOF'
feat(controller): add the Smith predictor

Two model states driven by the same duty history, one shifted by theta; the
controller input is measured + x0 - xd, so feedback survives while the
identified delay comes out of the signal.

Both branches start equal on trust and after any reset, so the correction begins
at exactly zero and control never steps. Integration splits at every duty
change, so a command due between two controller updates is never rounded to the
sample interval.
EOF
```

---

### Task 7: The predictor's safety envelope

Task 6 sketched `_safe`; this task proves each disable condition and the recovery behavior, and fixes whatever the tests expose.

**Files:**
- Modify: `controller/smith_predictor.py`
- Test: `tests/unit/controller/test_smith_predictor.py`

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: predictor safety envelope"
```

Append to `tests/unit/controller/test_smith_predictor.py`:

```python
def test_a_non_finite_model_state_disables_prediction():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p._x0 = float("nan")
    assert p.temperature(212.0, 20.0) == 212.0
    assert p.active is False
    assert p.status()["disabled"] is True


@pytest.mark.parametrize("measured", [-500.0, 2000.0])
def test_a_prediction_outside_the_grill_band_disables_prediction(measured):
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    assert p.temperature(measured, 20.0) == measured
    assert p.active is False


def test_a_sustained_large_residual_disables_prediction():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    t = 20.0
    for _ in range(3):
        p.temperature(212.0 + 0.0, t)
        t += 20.0
    # four consecutive samples each more than 100 F from the last prediction
    for k in range(4):
        out = p.temperature(212.0 + (k + 1) * 150.0, t)
        t += 20.0
    assert p.active is False
    assert out == pytest.approx(212.0 + 4 * 150.0)


def test_one_large_residual_does_not_disable_prediction():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 20.0)
    p.temperature(500.0, 40.0)  # one jump
    p.temperature(505.0, 60.0)  # then settled
    assert p.active is True


def test_the_last_valid_parameters_stay_observable_after_a_disable():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p._x0 = float("inf")
    p.temperature(212.0, 20.0)
    assert p.status()["model"]["K"] == MODEL["K"]


def test_re_trusting_the_same_model_does_not_restart_the_states():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 600.0)
    x0 = p.status()["x0"]
    p.trust(dict(MODEL))
    assert p.status()["x0"] == x0


def _warmed():
    p = _predictor()
    p.trust(MODEL)
    p.record_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 0.0))
    p.temperature(212.0, 0.0)
    p.temperature(212.0, 600.0)
    return p


def test_a_revised_gain_updates_in_place_and_keeps_the_correction():
    """The identifier only revises after a confirmation window; snapping a live
    correction back to zero would be the step equal-state init exists to avoid."""
    p = _warmed()
    before = p.status()["x0"] - p.status()["xd"]
    assert before != 0.0
    p.trust({**MODEL, "K": 900.0})
    assert p.status()["model"]["K"] == 900.0
    assert p.status()["x0"] - p.status()["xd"] == pytest.approx(before)


def test_a_revised_delay_reinitializes_both_branches():
    """The delayed state was accumulated under the old theta and no longer means
    what it did."""
    p = _warmed()
    p.trust({**MODEL, "theta": 60.0})
    assert p.status()["x0"] == p.status()["xd"] == 0.0


def test_a_disable_is_sticky_across_re_trusting_the_same_model():
    """PID-SP re-asserts the trusted model every tick. If that cleared the flag,
    the safety envelope would do nothing."""
    p = _warmed()
    p._x0 = float("nan")
    p.temperature(212.0, 620.0)
    assert p.active is False
    p.trust(dict(MODEL))
    assert p.active is False


def test_a_genuinely_new_model_clears_a_disable():
    p = _warmed()
    p._x0 = float("nan")
    p.temperature(212.0, 620.0)
    p.trust({**MODEL, "K": 900.0})
    assert p.active is True
    assert p.status()["x0"] == p.status()["xd"] == 0.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_smith_predictor.py -q`
Expected: at least `test_a_non_finite_model_state_disables_prediction` FAILS — `_safe` checks the prediction after computing it from a NaN state, and `reset()` clears `_disabled` that the caller then sets, so the ordering has to be right.

- [ ] **Step 3: Fix the disable path**

The bug the tests expose: `temperature()` calls `self.reset()` (which clears `_disabled`) and *then* sets `_disabled = True`, so the flag survives — but `reset()` also clears `_residual_streak`, which is what the streak counter needs. Separate the two:

```python
    def _disable(self):
        """Fall back to measured temperature and reinitialize both branches
        equally, so a later re-trust starts from a zero correction."""
        self._x0 = 0.0
        self._xd = 0.0
        self._last_prediction = None
        self._residual_streak = 0
        self._disabled = True
```

and in `temperature()` replace the `self.reset(); self._disabled = True` pair with `self._disable()`.

`reset()` keeps clearing `_disabled` — it is the "start over cleanly" entry point, called from `trust()` on a changed model.

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_smith_predictor.py -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/smith_predictor.py tests/unit/controller/test_smith_predictor.py
jj describe --stdin <<'EOF'
feat(controller): give the Smith predictor a safety envelope

Prediction disables on a non-finite state, on a predicted temperature outside
-100 to 1200 F, and on a one-step residual above 100 F sustained for four
consecutive samples. Both branches reinitialize equally, control falls back to
measured temperature, and the last valid parameters stay observable in status().

A disable is not a reset: the streak counter and the fallback flag have opposite
lifetimes and separating them is what makes both correct.
EOF
```

---

### Task 8: Rewrite `controller/pid_sp.py`

**Files:**
- Modify: `controller/pid_sp.py`
- Test: `tests/unit/controller/test_pid_sp.py` (create)

**Interfaces:**
- Consumes: `FOPDTIdentifier` (Task 5), `SmithPredictor` (Tasks 6-7).
- Produces: a `Controller` whose `update()` is `pid_ac`'s with the selected temperature substituted, plus the startup reduction actually applied.

#### Three dead stores the rewrite must not carry over

A rewrite that transcribes the current `update()` faithfully will reproduce all three of these, because each one *looks* like a working feature. Each needs a test that fails against the current code.

**The startup reduction is discarded.** `self.u = self.u * 0.65` at `pid_sp.py:142` is overwritten by `self.u = self.p + self.i + self.d` three lines later, so the overshoot suppression has never once taken effect. Applying it to the newly computed `self.u` is already this task's `STARTUP_REDUCTION` change — noted here so it is understood as fixing a dead store, not adding a new behavior.

**The derivative suppression is discarded.** `self.derv = 0.0` at `pid_sp.py:126`, guarded by `(self.new_target and self.set_point < current) or (abs(error) > self.pb / 2)`, is unconditionally overwritten by `self.derv = (predicted_temp - self.last) / dt` at `:137`. The comment above it — "Minimize derivative to maximize descent rate when setting new lower Set Point" — describes a feature that has never executed. Decide deliberately whether the rewrite keeps it: with the Smith predictor supplying the selected temperature, suppressing D on a downward set-point change may no longer be wanted. Whichever way it goes, it must be a choice with a test behind it, not an accident that survives the port. `pid_ac.py` carries the same two dead stores; do not fix them there in this task, but say so in the commit message.

**`self.last` starts at a fictitious 150.** `pid_sp.py:80` initializes `self.last = 150`, and `update()`'s repair — `if self.last == 0.0 and self.new_target: self.last = current` — tests against `0.0`, so it never fires. Every construction therefore makes the first solve compute `roc` and `derv` against a previous temperature of 150 °F that was never measured; from a 68 °F cold start that is a phantom −4.1 °F/s. This matters more than it looks, because production rebuilds the controller on a set-point change, so it recurs on every change rather than only at startup.

Initialize `self.last` to `None` and seed it from the first observed temperature, making the first update's rate exactly zero. Pin it with a test asserting the first `update()` after construction produces the same output for a cold start and a hot one when both are at steady state — under the current code they differ, because one is 82 °F below the phantom and the other is above it.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: pid_sp composition"
```

Create `tests/unit/controller/test_pid_sp.py`:

```python
"""PID-SP composes the identifier and the predictor and models nothing itself."""

import importlib
import time

import pytest

from controller.applied_output import AppliedOutput, OutputSource

CONFIG = {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010}
CYCLE_DATA = {"HoldCycleTime": 20}


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(time, "time", c)
    return c


def _controller(name, clock):
    mod = importlib.import_module(f"controller.{name}")
    return mod.Controller(dict(CONFIG), "F", dict(CYCLE_DATA))


def test_untrusted_pid_sp_is_term_for_term_pid_ac(clock):
    """Identification is passive, so a fresh install is plain PID for about an
    hour. That is accepted by design -- and it must be EXACTLY pid_ac."""
    sp = _controller("pid_sp", clock)
    ac = _controller("pid_ac", clock)
    sp.set_target(225.0)
    ac.set_target(225.0)
    for temp in [150, 160, 180, 200, 205, 210, 215, 218, 220, 221]:
        clock.t += 20.0
        assert sp.update(float(temp)) == pytest.approx(ac.update(float(temp)))


def test_the_startup_reduction_is_applied_to_the_new_output(clock):
    """The old code multiplied the PREVIOUS cycle's output and then overwrote
    it, so the reduction never had any effect."""
    sp = _controller("pid_sp", clock)
    ac = _controller("pid_ac", clock)
    sp.set_target(225.0)
    ac.set_target(225.0)
    clock.t += 20.0  # inside cycle_time * 3 of the setpoint change
    out_sp = sp.update(200.0)
    out_ac = ac.update(200.0)
    assert out_sp == pytest.approx(out_ac * 0.65)


def test_the_reduction_stops_after_three_cycles(clock):
    sp = _controller("pid_sp", clock)
    ac = _controller("pid_ac", clock)
    sp.set_target(225.0)
    ac.set_target(225.0)
    clock.t += 20.0 * 3 + 1
    assert sp.update(200.0) == pytest.approx(ac.update(200.0))


def test_a_trusted_model_makes_the_selected_temperature_diverge_from_measured(clock):
    """The first tick only anchors the clock; the correction appears on the
    second, once duty has been integrated across a real interval."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1})
    sp.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    clock.t += 20.0
    assert sp.update(200.0) is not None
    assert sp.get_status()["predictor"]["active"] is True
    assert sp.get_status()["selected_temp"] == 200.0  # anchored, correction still zero
    clock.t += 20.0
    sp.update(200.0)
    assert sp.get_status()["selected_temp"] > 200.0  # x0 has moved, xd has not


def test_the_derivative_never_mixes_a_measured_and_a_predicted_sample(clock):
    """Both terms of the derivative come from the selected series."""
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1})
    sp.set_output(AppliedOutput(0.9, OutputSource.CONTROLLER, clock.t))
    clock.t += 20.0
    sp.update(200.0)
    first = sp.get_status()["selected_temp"]
    clock.t += 20.0
    sp.update(205.0)
    status = sp.get_status()
    second = status["selected_temp"]
    assert second != 205.0, "the predictor is not correcting; the test proves nothing"
    assert status["d"] == pytest.approx(sp.kd * (second - first) / 20.0)


def test_set_target_preserves_the_learned_model(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 1})
    sp.set_target(275.0)
    assert sp.get_model_snapshot()["K"] == 800.0
    assert sp.inter == 0.0  # but the target-dependent PID terms do reset


def test_no_tau_or_theta_config_is_read(clock):
    """A user-supplied tau=115 is outside the design's own trusted band; the
    options are gone, and reading them would resurrect them."""
    import controller.pid_sp as mod

    source = open(mod.__file__).read()
    assert 'config.get("tau"' not in source
    assert 'config.get("theta"' not in source
    assert "math.exp" not in source
    assert "self.roc" not in source
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_pid_sp.py -q`
Expected: FAIL — the equivalence test fails because today's PID-SP uses `roc` extrapolation.

- [ ] **Step 3: Rewrite the controller**

Replace the whole of `controller/pid_sp.py`:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire PID Controller with a Smith Predictor
*****************************************

 Description: The auto-centering PID controller, regulating on a temperature
 with identified dead time removed instead of on the raw probe reading.

 This controller models nothing itself. controller/fopdt_identifier.py learns
 the grill's FOPDT parameters passively from applied duty, and
 controller/smith_predictor.py turns those parameters into one temperature per
 tick. That single value drives P, I and D -- the derivative compares
 consecutive SELECTED temperatures and never subtracts a measured sample from a
 predicted one.

 Until the identifier's gates clear, the selected temperature is the measured
 one and this is term-for-term the pid_ac controller.

 PID controller based on proportional band in standard PID form
 https://en.wikipedia.org/wiki/PID_controller#Ideal_versus_standard_PID_form
   u = Kp (e(t)+ 1/Ti INT + Td de/dt)
  PB = Proportional Band
  Ti = Goal of eliminating in Ti seconds
  Td = Predicts error value at Td in seconds

  Configuration Defaults:
  "config": {
      "PB": 60.0,
      "Td": 45.0,
      "Ti": 180.0,
      "center": 0.5
   }

*****************************************
"""

import time

from controller.fopdt_identifier import FOPDTIdentifier
from controller.pid_base import PIDControllerBase
from controller.smith_predictor import SmithPredictor

#: Output reduction for the first three cycles after a setpoint change.
STARTUP_REDUCTION = 0.65


def _to_f(value, units):
    return value if units == "F" else value * 9.0 / 5.0 + 32.0


def _from_f(value, units):
    return value if units == "F" else (value - 32.0) * 5.0 / 9.0


class Controller(PIDControllerBase):
    def __init__(self, config, units, cycle_data):
        super().__init__(config, units, cycle_data)

        self._calculate_gains(config.get("PB", 60.0), config.get("Ti", 180.0), config.get("Td", 45.0))

        self.p = 0.0
        self.i = 0.0
        self.d = 0.0
        self.u = 0

        self.pb = config.get("PB", 60.0)

        self.units = units

        self.last_update = time.time()
        self.last_set_time = time.time()
        self.error = 0.0
        self.set_point = 0

        self.center = 0.5
        self.center_factor = config.get("center_factor", 0.0010)

        self.stable_window = config.get("stable_window", 12)
        self.cycle_time = cycle_data["HoldCycleTime"]

        self.derv = 0.0
        self.inter = 0.0

        self.last = 150
        self.start_change_temp = 0.0
        self.new_target = False

        self.identifier = FOPDTIdentifier()
        self.predictor = SmithPredictor()
        self._selected = None

        self.set_target(0.0)

    # ------------------------------------------------------------ capabilities
    def set_output(self, applied):
        self.identifier.record_output(applied)
        self.predictor.record_output(applied)

    def get_status(self):
        return {
            "p": self.p,
            "i": self.i,
            "d": self.d,
            "u": self.u,
            "error": self.error,
            "set_point": self.set_point,
            "center": self.center,
            "selected_temp": self._selected,
            "last_selected": self.last,
            "identifier": self.identifier.status(),
            "predictor": self.predictor.status(),
        }

    def get_model_snapshot(self):
        return self.identifier.trusted_model()

    def restore_model(self, snapshot):
        if not self.identifier.restore(snapshot):
            return False
        self.predictor.trust(self.identifier.trusted_model())
        return True

    # ------------------------------------------------------------------ control
    def update(self, current):
        current_time = time.time()
        dt = current_time - self.last_update

        measured_f = _to_f(current, self.units)
        self.identifier.observe(measured_f, current_time)
        self.predictor.trust(self.identifier.trusted_model())
        selected = _from_f(self.predictor.temperature(measured_f, current_time), self.units)
        self._selected = selected

        # Fix self.last being set to 0.0 on set point change
        if self.last == 0.0 and self.new_target:
            self.last = selected

        error = selected - self.set_point

        if error < -self.pb:
            self.u = 1.0
        # If overshooting, minimize output
        elif error > self.stable_window:
            self.u = 0.0
        else:
            # Reset integral term when the temperature first reaches or exceeds
            # set point after a set point change
            if self.new_target and abs(error) <= 3:
                self.new_target = False

            # Reset integral if the system is not within the stable window, or
            # has not reached halfway to the set point within 3 cycles. Prevents
            # overshoots on small set point changes.
            if (abs(error) > self.stable_window) or (
                self.new_target
                and current_time - self.last_set_time >= self.cycle_time * 3
                and abs(error) <= abs(self.start_change_temp - self.set_point) / 2
            ):
                self.inter = 0.0

            # Minimize derivative to maximize descent rate when setting a new
            # lower set point
            if (self.new_target and self.set_point < selected) or (abs(error) > self.pb / 2):
                self.derv = 0.0

            # P
            self.p = self.kp * error + self.center

            # I
            self.inter += error * dt
            self.i = self.ki * self.inter
            self.i = max(min(self.i, self.center), -self.center)

            # D
            self.derv = (selected - self.last) / dt
            self.d = self.kd * self.derv

            # PID
            self.u = self.p + self.i + self.d

            # Ease off for the first three cycles after a set point change, so a
            # small change does not overshoot.
            if error < self.pb and current_time - self.last_set_time < self.cycle_time * 3:
                self.u = self.u * STARTUP_REDUCTION

        self.error = error
        self.last = selected
        self.last_update = current_time

        return self.u

    def set_target(self, set_point):
        self.set_point = set_point
        self.error = 0.0
        self.inter = 0.0
        self.derv = 0.0
        self.last_update = time.time()
        self.last_set_time = self.last_update
        self.start_change_temp = self.last
        self.new_target = True
        # Higher centers are needed to reach higher temps, lower centers keep
        # low set points stable.
        if self.units == "F":
            if set_point <= 240:
                self.center = set_point * self.center_factor
            else:
                self.center = set_point * self.center_factor * 1.2
        elif self.units == "C":
            if set_point <= 115:
                self.center = (set_point * 9 / 5 + 32) * self.center_factor
            else:
                self.center = (set_point * 9 / 5 + 32) * self.center_factor * 1.2
```

Note what `set_target` does **not** touch: `self.identifier` and `self.predictor` keep their learned parameters, RLS state, model states and duty history. A setpoint change is not new information about the grill's physics.

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/ -q`
Expected: PASS. `tests/characterization/test_pid_variants_golden.py` FAILS — expected, and Task 11 regenerates it. Do not touch the golden yet.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/pid_sp.py tests/unit/controller/test_pid_sp.py
jj describe --stdin <<'EOF'
feat(pid_sp): regulate on a Smith-corrected temperature from an identified model

Replaces the fixed rate-of-change extrapolator and its tau/theta options with an
online-identified FOPDT model. One selected temperature drives P, I and D, and
the derivative compares consecutive selected samples rather than mixing a
measured one with a predicted one.

Until the identifier's gates clear this is term-for-term pid_ac, which is the
accepted consequence of identifying passively.

Also fixes the startup reduction: it multiplied the PREVIOUS cycle's output and
was then overwritten wholesale, so it has never had any effect. Applying it to
the newly calculated output is what it was always for, and it moves the goldens.
EOF
```

---

### Task 9: Persist PID-SP's model end to end

Plan A's store call site has been inert because no controller returned a snapshot. Now one does. This task proves the whole path.

**Files:**
- Test: `tests/unit/controller/test_pid_sp.py`

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: pid_sp persistence"
```

Append to `tests/unit/controller/test_pid_sp.py`:

```python
import json

from common.controller_model_state import ControllerModelStore


class _FakeBlobs:
    def __init__(self):
        self.blobs = {}

    def read(self, key):
        return json.loads(self.blobs[key]) if key in self.blobs else json.loads(None)

    def write(self, key, value):
        self.blobs[key] = json.dumps(value)


def test_a_snapshot_survives_the_store_round_trip(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)

    assert store.save("pid_sp", sp.get_model_snapshot()) is True

    fresh = _controller("pid_sp", clock)
    assert fresh.get_model_snapshot() is None
    assert fresh.restore_model(store.load("pid_sp")) is True
    assert fresh.get_model_snapshot() == {"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3}


def test_a_restored_model_is_active_on_the_first_tick(clock):
    """From the second cook onward there is no hour of plain PID."""
    blobs = _FakeBlobs()
    store = ControllerModelStore(reader=blobs.read, writer=blobs.write)
    store.save("pid_sp", {"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})

    sp = _controller("pid_sp", clock)
    sp.restore_model(store.load("pid_sp"))
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    assert sp.get_status()["predictor"]["active"] is True


def test_the_snapshot_satisfies_the_store_s_envelope_rules(clock):
    sp = _controller("pid_sp", clock)
    sp.restore_model({"K": 800.0, "tau": 600.0, "theta": 40.0, "revision": 3})
    snapshot = sp.get_model_snapshot()
    encoded = json.dumps(snapshot, allow_nan=False)
    assert len(encoded.encode("utf-8")) <= 8192
    assert isinstance(snapshot["revision"], int)


def test_an_untrusted_controller_offers_nothing_to_persist(clock):
    assert _controller("pid_sp", clock).get_model_snapshot() is None


def test_get_status_survives_the_mqtt_encoder(clock):
    sp = _controller("pid_sp", clock)
    sp.set_target(225.0)
    clock.t += 20.0
    sp.update(200.0)
    json.dumps(sp.get_status(), allow_nan=False)
```

- [ ] **Step 2: Run it**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_pid_sp.py -q`
Expected: most pass already from Task 8. Any that fail are real gaps — most likely `get_status()` carrying a numpy scalar that `json.dumps` refuses.

- [ ] **Step 3: Fix whatever the encoder test exposes**

`FOPDTIdentifier.status()` returns numpy scalars from `float(...)`-wrapped expressions; confirm every one is a Python `float`/`int`/`bool`, not a `np.float64` or `np.bool_`. `json.dumps` rejects `np.bool_`. In `status()`, wrap `candidates_passing` as `int(...)` (already done) and `transition_seen` as `bool(self._transition_seen)`.

- [ ] **Step 4: Run the full controller suite**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/ tests/unit/runtime/ -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/fopdt_identifier.py tests/unit/controller/test_pid_sp.py
jj describe --stdin <<'EOF'
test(pid_sp): prove the learned model survives a restart

Plan A's persistence call site has been inert because no controller returned a
snapshot. PID-SP is the first that does, so this exercises the whole path:
snapshot, store envelope, reload, and an active predictor on the first tick of
the next cook.
EOF
```

---

### Task 10: Controller metadata and the numpy dependency

**Files:**
- Modify: `controller/controllers.json`
- Modify: `pyproject.toml`
- Test: `tests/unit/mpc/test_mpc_manifest.py` (extend, or the nearest manifest test)

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: pid_sp manifest"
```

Add to the manifest test module:

```python
def test_pid_sp_declares_numpy_without_an_extra():
    """numpy missing means a broken install, not a missing opt-in, so there is
    nothing for PiFire to offer to install."""
    meta = _manifest()["metadata"]["pid_sp"]
    assert meta["dependencies"] == {"modules": ["numpy"]}
    assert "extra" not in meta["dependencies"]


def test_pid_sp_no_longer_offers_tau_or_theta():
    """Identification is online; a user-supplied tau=115 is not merely unused,
    it is outside the design's own trusted band of 300-20000 s."""
    options = {o["option_name"] for o in _manifest()["metadata"]["pid_sp"]["config"]}
    assert "tau" not in options
    assert "theta" not in options
    assert options == {"PB", "Td", "Ti", "stable_window", "center_factor"}


def test_numpy_is_an_explicit_project_dependency():
    import tomllib

    with open("pyproject.toml", "rb") as f:
        project = tomllib.load(f)["project"]
    assert any(d.split(">")[0].split("=")[0].strip() == "numpy" for d in project["dependencies"])
```

Reuse the module's existing manifest-loading helper rather than adding a new one.

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/mpc/test_mpc_manifest.py -q`
Expected: FAIL — `KeyError: 'dependencies'`.

- [ ] **Step 3: Edit the manifest**

In `controller/controllers.json`, in `metadata.pid_sp`:

1. Delete the two `config` entries whose `option_name` is `"tau"` and `"theta"`.
2. Add, beside `"recommendations"`:

```json
  "dependencies": {
   "modules": [
    "numpy"
   ]
  },
```

Match the file's existing indentation exactly — it is one-space-indented JSON.

- [ ] **Step 4: Make numpy explicit in `pyproject.toml`**

Add `"numpy>=1.26"` to the top-level `[project] dependencies` list, keeping the list's existing sort order. numpy is already in every install transitively via `scikit-learn` and `scikit-fuzzy`; this states the reality rather than changing it.

- [ ] **Step 5: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/ -q`
Expected: PASS. `tests/unit/deps/` and the wizard/controller-settings tests read this manifest — if any fail, they are asserting on the old option list and need updating in this task, not later.

- [ ] **Step 6: Commit**

```bash
jj describe --stdin <<'EOF'
feat(pid_sp): drop the tau/theta options and declare numpy

With identification online a user-supplied tau=115 is not merely unused: it is
outside the design's own trusted band of 300-20000 s, and there is no K option
at all, so a seeded model would be one the design would refuse to trust.

numpy is declared with no extra, because a missing numpy means a broken install
rather than a missing opt-in and there is nothing to offer to install. It is
already present transitively; pyproject now says so.
EOF
```

---

### Task 11: Regenerate the PID-SP golden

**Files:**
- Modify: `tests/characterization/test_pid_variants_golden.py`

- [ ] **Step 1: `jj new`**

```bash
jj new -m "wip: pid_sp golden"
```

- [ ] **Step 2: Confirm only PID-SP moved**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/characterization/test_pid_variants_golden.py -q`
Expected: FAIL for `pid_sp` **only**. If any other variant moved, stop — Task 1's `ti <= 0` change was supposed to be inert for every shipped config, and something else is wrong.

- [ ] **Step 3: Drop `tau`/`theta` from the test's config**

In `PID_CONFIGS["pid_sp"]`, delete the `"tau": 115` and `"theta": 65` entries. They are no longer options, and leaving them would pin a surface that no longer exists.

- [ ] **Step 4: Regenerate the series**

```bash
QT_QPA_PLATFORM=offscreen uv run python - <<'EOF'
import importlib, time, json
import tests.characterization.test_pid_variants_golden as g

class _Clock:
    def __init__(self): self.t = g.T0
    def __call__(self): return self.t

clock = _Clock()
time.time = clock
mod = importlib.import_module("controller.pid_sp")
c = mod.Controller({k: v for k, v in g.PID_CONFIGS["pid_sp"].items()}, "F", dict(g.CYCLE_DATA))
c.set_target(g.SETPOINT)
out = []
for temp in g.SERIES:
    clock.t += g.STEP
    out.append(round(c.update(float(temp)), 10))
print(json.dumps(out))
EOF
```

If the module's `_run_variant` helper differs from this in any way — a different clock advance, a `set_target` before or after construction — mirror **its** sequence exactly instead. A golden captured by a different procedure than the test uses is not a golden.

- [ ] **Step 5: Paste the series into `GOLDEN["pid_sp"]` and add the note**

Above the `pid_sp` entry:

```python
    # Recaptured after the Smith-predictor rewrite. Two things moved it: the
    # startup reduction now applies to the newly calculated output (it multiplied
    # the previous cycle's and was then overwritten), and the rate-of-change
    # extrapolator is gone. The identifier stays untrusted over a ten-sample
    # series, so this is pid_ac's response with the reduction applied.
```

- [ ] **Step 6: Verify PID-SP is pid_ac times the reduction where the reduction applies**

The whole series sits inside `cycle_time * 3 = 60 s` of the setpoint change? No — `STEP` is 20 s over 10 samples, so only the first three do. Check that by hand against `GOLDEN["pid_ac"]`: the first three PID-SP values should be `0.65 ×` the corresponding `pid_ac` values wherever `pid_ac` took the `else` branch, and equal afterwards. If they are not, the rewrite is wrong and the golden would enshrine the bug.

- [ ] **Step 7: Run the suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff format tests/characterization/test_pid_variants_golden.py
jj describe --stdin <<'EOF'
test(pid): recapture the PID-SP golden after the Smith-predictor rewrite

Two changes moved it: the startup reduction now applies to the newly calculated
output, and the rate-of-change extrapolator is gone. Over a ten-sample series
the identifier stays untrusted, so the new golden is pid_ac's response with the
reduction applied -- checked term by term against GOLDEN["pid_ac"] rather than
accepted because the code produced it.
EOF
```

---

### Task 12: PID-SP on GrillSim

**Files:**
- Create: `docs/superpowers/experiments/_matrix_after_pid_sp.json` (generated)

- [ ] **Step 1: `jj new`**

```bash
jj new -m "wip: pid_sp grillsim after numbers"
```

- [ ] **Step 2: Run the matrix for PID-SP**

```bash
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/controller_matrix.py \
  --controllers pid_sp --out docs/superpowers/experiments/_matrix_after_pid_sp.json -w 8
```

- [ ] **Step 3: Compare against Plan A's baseline**

```bash
QT_QPA_PLATFORM=offscreen uv run python - <<'EOF'
import json
base = {(r["scenario"], r["seed"]): r for r in json.load(open("docs/superpowers/experiments/_matrix_baseline.json")) if r["controller"] == "pid_sp"}
after = {(r["scenario"], r["seed"]): r for r in json.load(open("docs/superpowers/experiments/_matrix_after_pid_sp.json"))}
for key in sorted(base):
    b, a = base[key], after[key]
    ident = (a.get("status") or {}).get("identifier", {})
    trusted = ident.get("trusted")
    print(f"{key[0]:16s} seed{key[1]} iae {b['iae']:9.0f} -> {a['iae']:9.0f} | "
          f"within5 {b['pct_within_5f']:5.1f} -> {a['pct_within_5f']:5.1f} | "
          f"over {b['overshoot_f']:6.1f} -> {a['overshoot_f']:6.1f} | "
          f"trusted={trusted} accepted_s={ident.get('accepted_seconds')}")
EOF
```

- [ ] **Step 4: Judge it against the right bar**

The bar is **no regression, reported as measured**. No target improvement is set, because a target invites tuning the test.

Identifier accuracy on GrillSim is judged on **plausibility only** — GrillSim is a two-state radiative model, not FOPDT, so there is no true answer to be within a percentage of. Absolute tolerances live in Task 5's unit tests against a synthetic FOPDT plant. What must hold here:

- identified `K`, `tau`, `theta` stay inside the physical bands;
- a candidate promotes once and stays promoted rather than flapping between delays;
- the implausible-residual fallback never trips.

Also record, as findings rather than pass/fail criteria:

- **activation time** per scenario, from `accepted_seconds` at first trust. If the gates never clear on a 3.5 hour hold, that is a finding about the design, not a reason to lower a gate.
- whether the **450 F cell** reproduces the overshoot that
  `2026-07-25-high-temperature-transition-tuning-design.md` describes. If it does, that tuning gets its own plan with thresholds derived from these numbers. If it does not, say so — it stays deferred.

If a scenario regresses, report the numbers and stop. Do not adjust a gate to recover it.

- [ ] **Step 5: Commit the numbers**

```bash
jj describe --stdin <<'EOF'
test(pid_sp): capture the GrillSim after numbers for the Smith predictor

<Paste the comparison table.>

Activation time per scenario: <...>
Identifier plausibility: <bands / promotion stability / fallback never tripped>
450 F overshoot: <reproduced or not>
EOF
```

---

## Completion

Before reporting this plan complete:

- [ ] `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` is green in the **main checkout**. Subagent worktrees without Chromium SKIP the `[chromium]` web tests; re-run any touched `tests/web/*.py` here.
- [ ] `.venv/bin/ruff format --check` is clean on every file this plan touched.
- [ ] Task 3's four negative-control results are written down. A parity test that passes against a broken implementation is worse than no test, and nothing else in this plan catches a wrong `einsum` subscript.
- [ ] The GrillSim comparison and the activation times are recorded, whatever they say.
- [ ] The 450 F finding is stated either way, so the deferred high-temperature tuning has an answer to start from.
