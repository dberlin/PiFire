# MPC Online Identification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an MPC-controlled grill learn its own thermal parameters from ordinary cooks and improve across them, without ever letting a learned model drive a grill it cannot brake.

**Architecture:** Two slices. **Slice A** refits the grey-box parameters from each finished cook's own history using the offline fitter that already exists, gates the result through a promotion policy, and persists it in the model store Hold already drives. **Slice B** adds live identification on top, consuming the `FOPDTIdentifier` built for PID-SP and mapping its `(K, tau, theta)` onto the grey-box. Both slices share one promotion policy and one snapshot format, so the online path changes where candidates come from and nothing about how they are judged.

**Tech Stack:** Python 3.14 (numpy/scipy, do-mpc/CasADi/IPOPT, pytest), React 19 + TypeScript (rstest, Testing Library, Biome).

**Spec:** `docs/superpowers/specs/2026-08-02-mpc-online-identification-design.md`

## Global Constraints

- Python tests run as `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <path> -v`. A bare `python`/`pytest` gives false failures (PySide6 lives in the uv venv).
- Format Python with `.venv/bin/ruff format <changed files>` before every commit. Never `uvx ruff` — the repo pins ruff <0.16.
- `web-react` uses **bun**: gates are `bun run typecheck`, `bun run test`, `bun run lint`. Tests are **rstest** (`@rstest/core`), never `bun test`.
- **No deliberate excitation may enter production auger commands.** Both slices use only naturally occurring excitation: the startup ramp, setpoint changes, lid events. (Inherited from `2026-08-01-adaptive-smith-predictor-design.md`.)
- Identification is **off by default**. A grill that has never identified behaves exactly as it does today, and the characterization goldens must prove it.
- Do not change the shipped `_DEFAULTS` values in `controller/mpc.py`.
- A learned model is never adopted without beating the incumbent **on the same data**. There is no "newest wins".
- Learned `tau` may not fall below the incumbent's without markedly stronger evidence than raising it. Overestimating sluggishness costs nothing; underestimating it is the 520 °F incident.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `controller/mpc.py` | `_build_nlp` solver options; `requires_modules`; cook history buffer; snapshot/restore; refit entry point | A1, A2, A4, A6, A7 |
| `common/controller_model_state.py` | Snapshot size cap | A3 |
| `controller/model_promotion.py` | **New.** Bounds, asymmetric `tau` guard, incumbent comparison, horizon adequacy | A5 |
| `controller/runtime/runner.py` | Runner surface for the cook-end refit | A7 |
| `controller/runtime/modes/hold.py` | Fires the refit at cook end | A7 |
| `controller/controllers.json` | `enable_identification` option | A8 |
| `web-react/src/components/settings/tabs/ControllerTab.tsx` | Note that identification forces the NLP policy | A8 |
| `controller/model_mapping.py` | **New.** FOPDT ↔ grey-box, with unit conversion | B1 |
| `controller/mpc.py` | Identifier feed, contention gate, rate-limited promotion | B2–B5 |

## Slices

- **Slice A (batch)** — Tasks A1–A9. **Unblocked today.** Depends only on `update_mpc.fit_params`/`fit_quality`, which landed in `2026-08-02-mpc-fan-authority-and-calibration`. Ships v1.
- **Slice B (online)** — Tasks B1–B7. **Blocked** on `controller/fopdt_identifier.py` (Tasks 2–5 of `2026-08-01-adaptive-smith-predictor.md`, not yet started) and on that plan's Task 17 fix round closing.

Slice A does **not** need the FOPDT mapping: `fit_params` fits the grey-box parameters directly. The mapping exists only because the identifier speaks FOPDT, so it lives in Slice B.

## Parallelization

- **A1, A2, A3 are mutually independent** and independent of everything else. Any of them may land alone; A1 is worth landing on its own regardless of this plan's fate.
- **A4, A5, A6 are mutually independent** (different files; A5 is a new module). All three must land before A7.
- **A7** integrates. **A8** needs A7 (it exposes the switch A7 reads). **A9** is last in the slice.
- **Slice B** starts only after A9. Within it: B1 ∥ B2, then B3 → B4 → B5 → B6, then B7.

Concurrent work requires isolated `jj` workspaces — disjoint file lists alone are not sufficient in this repo. Do **not** use `git worktree`; this is a colocated jj repo and worktree teardown has abandoned commits here before.

---

# Slice A — batch identification

## Task A1: Warm-started, iteration-capped NLP

Implements R5.4. **Independent of everything else in this plan** — it is a solver-configuration fix worth landing on its own.

Measured: `warm_start_init_point` + `max_iter: 10` gives −19 % mean, −24 % p95 and −23 % worst-case solve time against the shipped configuration, for a commanded-ratio difference of 4.25e-03 on a `[0.1, 0.9]` range and an unchanged 444 °F peak. do_mpc already passes `lam_x0`/`lam_g0` on every solve after the first (`optimizer.py:762-768`); without `warm_start_init_point` IPOPT discards them.

**Files:**
- Modify: `controller/mpc.py` (`_build_nlp`, the `set_param(nlpsol_opts=...)` call)
- Test: `tests/unit/mpc/test_mpc_solver_options.py` (create)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/mpc/test_mpc_solver_options.py`:

```python
"""The NLP is warm-started and iteration-capped.

do_mpc hands IPOPT the previous solve's duals on every step after the first;
without warm_start_init_point IPOPT throws them away. The iteration cap bounds
the tail: the cold start needs ~60 iterations with nothing to warm from, and
truncating it is what keeps the worst case under the control period.
"""

import numpy as np
import pytest

from controller.grill_sim import MAKGrillSim
from controller.mpc import Controller

CONFIG = dict(
    n_horizon=24,
    t_step=25.0,
    control_period=5.0,
    Q_w=1.0,
    R_dQ=0.1,
    Q_min=5.0,
    Q_max=100.0,
    C_f=9.0,
    C_c=320.0,
    h_fc=1.3,
    h_amb=0.5,
    T_amb=20.0,
    theta=50.0,
    n_delay=4,
    K_Q=3.5,
    sigma=1.4e-9,
    policy="nlp",
    estimator="ekf",
    est_q_temp=1e-2,
    est_q_dist=0.05,
    est_r_meas=0.04,
    enable_fan_input=True,
    fan_min_pct=40.0,
    fan_max_pct=100.0,
)
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}


def _opts(controller):
    """The options dict handed to nlpsol, as do_mpc stored it."""
    return dict(controller.mpc.settings.nlpsol_opts)


def test_warm_start_is_enabled():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    assert _opts(c)["ipopt.warm_start_init_point"] == "yes"


def test_iterations_are_capped():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    assert int(_opts(c)["ipopt.max_iter"]) == 10


def test_the_cap_does_not_change_the_commanded_trajectory():
    """The cap truncates 7 of 180 solves in the measured run; the resulting
    command differs by well under 1% of the [u_min, u_max] span. This pins
    that, so a future cap change that actually alters control fails here."""
    ratios = {}
    for label, cap in (("capped", 10), ("uncapped", 3000)):
        c = Controller(dict(CONFIG), "C", dict(CYCLE))
        c.mpc.settings.nlpsol_opts["ipopt.max_iter"] = cap
        c.mpc.setup()
        c.set_target(110.0)
        sim = MAKGrillSim(seed=0, T0=40.7, fixed_fan=1.0)
        seq, ratio = [], 0.1
        for t in range(300):
            if t % 5 == 0:
                ratio = float(np.clip(c.update(sim.measured())["cycle_ratio"], 0.1, 0.9))
                seq.append(ratio)
            sim.step((t % 25) < ratio * 25, 1.0)
        ratios[label] = np.array(seq)
    assert np.abs(ratios["capped"] - ratios["uncapped"]).max() < 1e-2
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_solver_options.py -v`
Expected: the first two FAIL with `KeyError` — neither option is set today. The third passes either way (it compares two explicitly-set caps), and is there to catch a future regression, not to drive this change.

- [ ] **Step 3: Set the options**

In `controller/mpc.py`, `_build_nlp`, replace the `nlpsol_opts` value in the `set_param` call:

```python
        self.mpc.set_param(
            n_horizon=int(cfg["n_horizon"]),
            t_step=float(cfg["t_step"]),
            store_full_solution=False,
            nlpsol_opts={
                "ipopt.print_level": 0,
                "print_time": 0,
                "ipopt.sb": "yes",
                # do_mpc supplies the previous solve's primal AND dual point on
                # every step after the first; IPOPT ignores the duals unless
                # warm starting is on. The cap bounds the tail -- the cold
                # start needs ~60 iterations with nothing to warm from, while
                # the median warm solve needs 6, so 10 truncates the spike
                # without touching the typical step.
                "ipopt.warm_start_init_point": "yes",
                "ipopt.max_iter": 10,
            },
        )
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_solver_options.py -v`
Expected: PASS

- [ ] **Step 5: Run the MPC and characterization suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc tests/characterization -q`
Expected: PASS. If an MPC golden moves, read the diff before regenerating: a commanded ratio that shifts by <1e-2 is this change and the golden should be updated; anything larger is not, and means the cap is biting harder on that scenario than on the measured one.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_solver_options.py
jj describe --stdin <<'EOF'
perf(mpc): warm-start the NLP and cap its iterations

do_mpc hands IPOPT the previous solve's duals on every step after the first,
but warm starting was never enabled, so they were discarded. Enabling it with
a 10-iteration cap cuts mean solve time 19%, p95 24% and the worst case 23%:
the cap truncates the cold-start spike that warm starting alone makes worse.
The median warm solve takes 6 iterations, so the cap binds rarely and moves
the commanded ratio by well under 1% of actuator span.
EOF
```

---

## Task A2: `do_mpc` is required unconditionally

Implements R4.2.

`requires_modules()` currently answers "no `do_mpc` needed" when `policy=net` and the artifact matches *at settings-save time*. Once a learned calibration lands, the artifact stops matching, the controller falls back to the NLP, and `_build_nlp` imports `do_mpc` — on a base install that never had it. The gate fails open at exactly the wrong moment.

**Product consequence, accepted deliberately:** selecting the MPC controller at all now requires the `mpc` extra, including for `policy=net` users who never enable identification.

**Files:**
- Modify: `controller/mpc.py` (`requires_modules`)
- Test: `tests/unit/mpc/test_mpc_requires_modules.py`

**Interfaces:** `requires_modules(config) -> tuple[str, ...]` — unchanged signature, now constant.

- [ ] **Step 1: Read the existing tests first**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_requires_modules.py -v`

Every test asserting an empty tuple is asserting the behaviour this task removes. Read them before editing; they are the specification of the old gate, and each one needs a decision rather than a bulk delete.

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/mpc/test_mpc_requires_modules.py`:

```python
def test_do_mpc_is_required_even_for_a_matching_net_policy():
    """The gate used to answer from the artifact's calibration, which a learned
    model invalidates mid-cook -- after the save it was consulted for. It now
    answers the same way for every MPC config."""
    cfg = dict(_DEFAULTS)
    cfg.update(policy="net")
    assert requires_modules(cfg) == ("do_mpc",)


def test_do_mpc_is_required_for_the_nlp_policy():
    assert requires_modules(dict(_DEFAULTS, policy="nlp")) == ("do_mpc",)


def test_do_mpc_is_required_for_the_mhe_estimator():
    assert requires_modules(dict(_DEFAULTS, estimator="mhe")) == ("do_mpc",)


def test_an_empty_config_still_requires_do_mpc():
    assert requires_modules({}) == ("do_mpc",)
```

- [ ] **Step 3: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_requires_modules.py -v`
Expected: `test_do_mpc_is_required_even_for_a_matching_net_policy` FAILS with `assert () == ('do_mpc',)` when the shipped artifact is present. Any pre-existing test asserting `()` also fails now — that is the intended removal, not collateral damage.

- [ ] **Step 4: Simplify the gate**

In `controller/mpc.py`, replace the body of `requires_modules` (keeping the docstring, updated):

```python
def requires_modules(config):
    """Import names `Controller(config, ...)` will need but a base install lacks.

    do-mpc's CasADi/IPOPT stack publishes no Linux-ARM wheel and so builds from
    source, which is why it is a PiFire *optional* dependency -- the `mpc` extra
    in pyproject.toml -- installed only when someone selects this controller.

    Every MPC config needs it. The gate used to exempt `policy=net` when the
    artifact's calibration matched, but that answer is only true until the
    calibration changes: a learned model, or any hand-edited thermal parameter,
    makes the artifact stale and drops the controller onto the NLP mid-cook,
    where the import would fail on a machine this gate had already cleared.
    An answer that expires is worse than a conservative one.
    """
    return ("do_mpc",)
```

Delete the now-unused `_load_net_policy` call inside it — but **not** `_load_net_policy` itself, which `Controller.__init__` still uses.

- [ ] **Step 5: Reconcile the pre-existing tests**

Any test that asserted `()` is asserting the removed exemption. Replace each with the corresponding `("do_mpc",)` assertion rather than deleting it, so the file still records that the net-policy case was considered.

- [ ] **Step 6: Run the suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc tests/unit/common -q`
Expected: PASS. `common/controller_deps.py` consumes this function; if its tests move, read them — the settings-save gate refusing an MPC selection on a base install is the intended new behaviour.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_requires_modules.py
jj describe --stdin <<'EOF'
fix(mpc): require do_mpc for every MPC configuration

The dependency gate exempted policy=net when the artifact's calibration
matched, but that answer expires: any change to the thermal parameters makes
the artifact stale and drops the controller onto the NLP, which imports
do_mpc -- on a machine the gate had already cleared for a base install. The
exemption bought one policy on one install type and cost a mid-cook
ImportError, so it is gone.
EOF
```

---

## Task A3: Raise the snapshot size cap

Implements R3.4.

A 25-candidate identifier bank is 7104 bytes as plain JSON against a 8192-byte cap — it fits by luck, not design, and one more regressor or a wider dead-time grid crosses the line. Slice A's own snapshot is far smaller; this is raised now so Slice B does not have to design around it later.

**Files:**
- Modify: `common/controller_model_state.py` (`MAX_SNAPSHOT_BYTES`)
- Test: `tests/unit/common/test_controller_model_state.py` (append)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing test**

Append to the existing model-state test file:

```python
def test_a_full_identifier_bank_round_trips():
    """Slice B persists 25 RLS candidates: coefficients (25x3), covariances
    (25x3x3) and residuals (25). That is ~7 KB of JSON -- under the old cap,
    but with too little headroom to build on."""
    import numpy as np

    rng = np.random.default_rng(0)
    snapshot = {
        "revision": 3,
        "Theta": rng.normal(size=(25, 3)).tolist(),
        "P": rng.normal(size=(25, 3, 3)).tolist(),
        "resid_ew": rng.normal(size=25).tolist(),
        "trusted": {"K": 1.23, "tau": 3750.0, "theta": 95.0},
    }
    store = ControllerModelStore(reader=..., writer=...)  # use the file's existing fake
    assert store.save("mpc", snapshot) is True
    assert store.load("mpc")["revision"] == 3
```

Match the fake reader/writer idiom the surrounding tests already use rather than inventing one.

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_controller_model_state.py -v`
Expected: FAIL — the snapshot is rejected as oversized.

- [ ] **Step 3: Raise the cap**

In `common/controller_model_state.py`:

```python
# Large enough that the bound is not a design constraint on what a controller
# may learn: a 25-candidate RLS bank with 3x3 covariances is ~7 KB of plain
# JSON. Plain JSON is kept over a packed encoding at this size because a model
# that drives a fire should stay readable in the datastore.
MAX_SNAPSHOT_BYTES = 65536
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_controller_model_state.py -v`
Expected: PASS, including the existing oversize-rejection test — it must still reject something, so if it pinned exactly 8192 bytes, raise its payload rather than deleting it.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format common/controller_model_state.py tests/unit/common/test_controller_model_state.py
jj describe --stdin <<'EOF'
feat(store): raise the controller model snapshot cap to 64 KiB

A 25-candidate RLS bank is 7104 bytes of plain JSON against an 8192-byte cap.
It fits, with 13% headroom -- by luck rather than design, and one more
regressor crosses the line. Raising the bound now means the online identifier
does not have to choose a packed encoding to fit, and the snapshot stays
readable in the datastore.
EOF
```

---

## Task A4: The cook history buffer

Implements R8.1's data source.

The refit needs the cook's own `(t, temp, applied Q)` series. `_log_row` already writes exactly that to CSV when `log_data` is on, but the CSV is append-only across cooks, user-toggled, and disabled by default — it is the manual offline path, not a dependable input. The controller keeps its own bounded in-memory history instead.

**Files:**
- Modify: `controller/mpc.py` (`__init__`, `update`)
- Test: `tests/unit/mpc/test_mpc_cook_history.py` (create)

**Interfaces:**
- Produces: `Controller.cook_history() -> list[tuple[float, float, float]]` of `(t_s, temp_c, Q_applied)`, oldest first. Task A7 consumes it.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/mpc/test_mpc_cook_history.py`:

```python
"""The controller keeps the record its own refit will consume."""

import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.mpc import _HISTORY_MAX, Controller

CONFIG = dict(
    n_horizon=20,
    t_step=25.0,
    control_period=1.0,
    Q_w=1.0,
    R_dQ=0.02,
    Q_min=5.0,
    Q_max=100.0,
    C_f=60.0,
    C_c=306.0,
    h_fc=2.0,
    h_amb=0.55,
    T_amb=20.0,
    enable_fan_input=True,
    fan_min_pct=40.0,
    fan_max_pct=100.0,
    est_q_temp=1e-2,
    est_q_dist=0.5,
    est_r_meas=0.04,
)
CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}


def test_history_records_one_row_per_update():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    for _ in range(5):
        c.update(100.0)
    assert len(c.cook_history()) == 5


def test_history_records_the_applied_rate_not_the_command():
    """The estimator is fed _applied_Q for the same reason: a lid-open pause
    means the plant did not receive what the controller asked for, and a fit
    against the command would attribute the resulting cooling to the model."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    c.update(100.0)
    c.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    c.update(100.0)
    _t, _temp, q_applied = c.cook_history()[-1]
    assert q_applied == pytest.approx(c._applied_Q)
    assert q_applied != pytest.approx(c._last_Q)


def test_history_is_bounded():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    for _ in range(_HISTORY_MAX + 50):
        c.update(100.0)
    assert len(c.cook_history()) == _HISTORY_MAX


def test_history_keeps_the_most_recent_rows_when_it_overflows():
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    for i in range(_HISTORY_MAX + 10):
        c.update(100.0 + i * 1e-3)
    temps = [row[1] for row in c.cook_history()]
    assert temps == sorted(temps)  # oldest dropped, order preserved


def test_history_survives_a_setpoint_change():
    """A setpoint change is not a new cook. The grill is the same grill, and
    the samples either side of the change are the excitation a fit wants most."""
    c = Controller(dict(CONFIG), "C", dict(CYCLE))
    c.set_target(110.0)
    c.update(100.0)
    c.set_target(150.0)
    c.update(100.0)
    assert len(c.cook_history()) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_cook_history.py -v`
Expected: FAIL — `ImportError: cannot import name '_HISTORY_MAX'`

- [ ] **Step 3: Implement the buffer**

In `controller/mpc.py`, at module level beside `_DEFAULTS`:

```python
# One row per control period. At the 5 s default that is ~12 hours, which is
# longer than any single cook; a longer one loses its beginning rather than
# its end, and the end is what describes the grill's current state.
_HISTORY_MAX = 8640
```

In `Controller.__init__`, beside the other per-cook state:

```python
        self._history = collections.deque(maxlen=_HISTORY_MAX)
```

(add `import collections` at the top if absent).

In `update`, immediately after the existing `if self._log_path: self._log_row(y, Q)`:

```python
        # The applied rate, not the command: a paused or clamped interval means
        # the plant did not receive what was asked for, and a fit against the
        # command would credit the model with the difference.
        self._history.append((time.time(), float(y), float(self._applied_Q)))
```

Add the accessor beside `get_status`:

```python
    def cook_history(self):
        """The cook's (time_s, temp_c, Q_applied) rows, oldest first."""
        return list(self._history)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_cook_history.py -v`
Expected: PASS

- [ ] **Step 5: Confirm the buffer is not in the MQTT payload**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_controller.py -k status -v`
Expected: PASS. `get_status()` is an explicit allow-list, so a new attribute should not leak — this run is the proof, not the assumption. If 8640 rows appear in an MQTT payload, fix `get_status`, not the test.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_cook_history.py
jj describe --stdin <<'EOF'
feat(mpc): keep a bounded in-cook history for refitting

The offline CSV is append-only across cooks, user-toggled and off by default,
so it cannot be the input to an automatic refit. The controller now keeps its
own bounded record of (time, measured temp, APPLIED firing rate) -- applied
rather than commanded, for the same reason the estimator is fed the applied
value: a paused interval means the plant did not get what was asked for.
EOF
```

---

## Task A5: The promotion policy

Implements R5.1, R7.1–R7.3. Pure functions, no I/O — this is where the safety argument lives, so it is tested hardest.

**Files:**
- Create: `controller/model_promotion.py`
- Test: `tests/unit/mpc/test_model_promotion.py` (create)

**Interfaces:**
- Produces:
  - `PROMOTION_BOUNDS: dict[str, tuple[float, float]]`
  - `evaluate(candidate: dict, incumbent: dict | None, *, candidate_rmse: float, incumbent_rmse: float | None, n_horizon: int, t_step: float) -> Verdict`
  - `Verdict` — a dataclass with `accepted: bool`, `reason: str`, `horizon_needed: int | None`.

Tasks A7 and B5 both consume `evaluate`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/mpc/test_model_promotion.py`:

```python
"""What may replace a model that is currently driving a fire."""

import pytest

from controller.model_promotion import PROMOTION_BOUNDS, evaluate

GOOD = dict(C_f=9.0, C_c=2520.0, h_fc=0.39, h_amb=0.224, T_amb=20.0, theta=93.0, n_delay=4, K_Q=6.95, sigma=1.4e-9)
INCUMBENT = dict(GOOD, C_c=2000.0, h_amb=0.30)  # tau 6667 vs candidate 11250
HORIZON = dict(n_horizon=144, t_step=25.0)


def _ev(candidate, incumbent=INCUMBENT, cand_rmse=2.0, inc_rmse=5.0, **kw):
    return evaluate(candidate, incumbent, candidate_rmse=cand_rmse, incumbent_rmse=inc_rmse, **{**HORIZON, **kw})


def test_a_better_fit_is_accepted():
    assert _ev(GOOD).accepted is True


def test_a_worse_fit_is_refused():
    v = _ev(GOOD, cand_rmse=9.0, inc_rmse=5.0)
    assert v.accepted is False
    assert "rmse" in v.reason.lower()


def test_the_first_model_is_accepted_when_there_is_no_incumbent():
    assert _ev(GOOD, incumbent=None, inc_rmse=None).accepted is True


def test_a_parameter_outside_its_physical_range_is_refused():
    v = _ev(dict(GOOD, C_c=-5.0))
    assert v.accepted is False
    assert "C_c" in v.reason


def test_a_non_finite_parameter_is_refused():
    assert _ev(dict(GOOD, K_Q=float("nan"))).accepted is False


def test_shrinking_tau_needs_more_evidence_than_raising_it():
    """The asymmetry is the safety argument. Believing the grill is more
    sluggish than it is makes the controller brake early and costs nothing;
    believing it is less sluggish is the 520 F incident."""
    slower = dict(GOOD, C_c=4000.0, h_amb=0.224)  # tau up
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)  # tau down
    margin = dict(cand_rmse=4.9, inc_rmse=5.0)  # barely better
    assert _ev(slower, **margin).accepted is True
    assert _ev(faster, **margin).accepted is False


def test_a_large_tau_reduction_is_accepted_on_strong_evidence():
    faster = dict(GOOD, C_c=1000.0, h_amb=0.224)
    assert _ev(faster, cand_rmse=0.5, inc_rmse=5.0).accepted is True


def test_a_model_needing_more_horizon_than_configured_reports_it():
    """tau 11250 s against a 24*25 = 600 s horizon."""
    v = _ev(GOOD, n_horizon=24, t_step=25.0)
    assert v.horizon_needed is not None
    assert v.horizon_needed > 24


def test_an_adequate_horizon_asks_for_nothing():
    assert _ev(GOOD, n_horizon=600, t_step=25.0).horizon_needed is None


def test_every_fitted_parameter_has_a_bound():
    for key in ("C_f", "C_c", "h_fc", "h_amb", "theta", "K_Q"):
        assert key in PROMOTION_BOUNDS
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_model_promotion.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the policy**

Create `controller/model_promotion.py`:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Model Promotion Policy
*****************************************

 Decides whether a freshly identified thermal model may replace the one
 currently driving the grill. Pure functions: the caller owns the fitting, the
 storage and the timing, so this file is only the judgement, and can be
 exercised without a solver, a datastore or a cook.

*****************************************
"""

import math
from dataclasses import dataclass

#: Ranges a fitted parameter must fall inside to be considered at all. Wide on
#: purpose -- this rejects nonsense, it does not express a preference.
PROMOTION_BOUNDS = {
    "C_f": (0.1, 1e4),
    "C_c": (1.0, 1e6),
    "h_fc": (1e-3, 1e3),
    "h_amb": (1e-4, 1e3),
    "theta": (0.0, 1200.0),
    "K_Q": (1e-3, 1e4),
}

#: A candidate must beat the incumbent's error by this fraction to be adopted
#: at all. Below it the two models describe the data equally well and churn
#: buys nothing.
_RMSE_MARGIN = 0.02

#: A candidate that SHORTENS the believed chamber time constant must beat the
#: incumbent by this much instead. Braking distance scales with tau, so a
#: wrongly-short tau brakes late -- the failure this whole design exists to
#: prevent -- while a wrongly-long tau brakes early and merely costs settling
#: time. The evidence bar is therefore deliberately asymmetric.
_RMSE_MARGIN_FASTER = 0.50

#: Ignore tau changes smaller than this; they are noise, not a direction.
_TAU_DEADBAND = 0.10


@dataclass
class Verdict:
    accepted: bool
    reason: str
    horizon_needed: int | None = None


def _tau(params):
    h_amb = float(params["h_amb"])
    return float(params["C_c"]) / h_amb if h_amb > 0 else math.inf


def evaluate(candidate, incumbent, *, candidate_rmse, incumbent_rmse, n_horizon, t_step):
    """Whether `candidate` may replace `incumbent`, and what horizon it needs."""
    for key, (lo, hi) in PROMOTION_BOUNDS.items():
        value = candidate.get(key)
        if value is None or not math.isfinite(float(value)):
            return Verdict(False, f"{key} is not a finite number")
        if not (lo <= float(value) <= hi):
            return Verdict(False, f"{key}={value:g} is outside [{lo:g}, {hi:g}]")

    if not math.isfinite(float(candidate_rmse)):
        return Verdict(False, "candidate RMSE is not finite")

    horizon_needed = None
    tau = _tau(candidate)
    if math.isfinite(tau) and n_horizon * t_step < tau:
        horizon_needed = int(math.ceil(tau / t_step))

    if incumbent is None or incumbent_rmse is None:
        return Verdict(True, "no incumbent", horizon_needed)

    # A shorter tau is the dangerous direction, so it carries the higher bar.
    ratio = _tau(candidate) / _tau(incumbent) if _tau(incumbent) > 0 else 1.0
    faster = ratio < (1.0 - _TAU_DEADBAND)
    margin = _RMSE_MARGIN_FASTER if faster else _RMSE_MARGIN
    if candidate_rmse > incumbent_rmse * (1.0 - margin):
        direction = "shorter" if faster else "comparable-or-longer"
        return Verdict(
            False,
            f"candidate RMSE {candidate_rmse:.3g} does not beat incumbent "
            f"{incumbent_rmse:.3g} by the {margin:.0%} required for a {direction} tau",
            horizon_needed,
        )
    return Verdict(True, "better fit on the same data", horizon_needed)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_model_promotion.py -v`
Expected: PASS

- [ ] **Step 5: Prove the asymmetry is load-bearing**

Temporarily set `_RMSE_MARGIN_FASTER = _RMSE_MARGIN` and re-run.
Expected: `test_shrinking_tau_needs_more_evidence_than_raising_it` FAILS. Restore the value. A safety rule that no test can distinguish from its absence is not a safety rule.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/model_promotion.py tests/unit/mpc/test_model_promotion.py
jj describe --stdin <<'EOF'
feat(mpc): add the model promotion policy

One judgement, shared by the batch and online paths: physical bounds, a
finite-value check, and a candidate that must beat the incumbent on the same
data rather than merely being newer. The evidence bar is asymmetric in tau --
a model claiming the grill is FASTER than believed must clear a much wider
margin, because braking distance scales with tau and a wrongly-short tau
brakes late, which is the failure this design exists to prevent.
EOF
```

---

## Task A6: MPC snapshot and restore

Implements R3.1–R3.3.

Hold already persists whatever `get_model_snapshot()` returns (`hold.py:239-240`) and restores it at setup (`hold.py:419`). `ControllerBase` returns `None`, so MPC currently stores nothing.

**Files:**
- Modify: `controller/mpc.py` (`__init__`, plus the two methods)
- Test: `tests/unit/mpc/test_mpc_model_snapshot.py` (create)

**Interfaces:**
- Produces: `Controller.get_model_snapshot() -> dict | None` and `Controller.restore_model(snapshot) -> bool`, satisfying `ControllerBase`'s contract and `common/controller_model_state.py`'s `revision` rule.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/mpc/test_mpc_model_snapshot.py`:

```python
"""What the MPC persists between cooks, and what it refuses to adopt."""

import json

import pytest

from controller.mpc import _DEFAULTS, Controller

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
PARAMS = dict(C_f=9.0, C_c=2520.0, h_fc=0.39, h_amb=0.224, T_amb=20.0, theta=93.0, n_delay=4, K_Q=6.95, sigma=1.4e-9)


def _c(**over):
    return Controller(dict(_DEFAULTS, policy="nlp", **over), "C", dict(CYCLE))


def test_an_unidentified_controller_snapshots_nothing():
    """Nothing learned, nothing to say. This keeps the store empty for the
    overwhelming majority of installs."""
    assert _c().get_model_snapshot() is None


def test_a_snapshot_is_json_safe_and_carries_its_provenance():
    c = _c()
    c._adopt_model(PARAMS, rmse=2.1, samples=1730, band_c=(40.0, 232.0))
    snap = c.get_model_snapshot()
    json.dumps(snap, allow_nan=False)
    assert snap["params"]["C_c"] == pytest.approx(2520.0)
    assert snap["rmse"] == pytest.approx(2.1)
    assert snap["samples"] == 1730
    assert tuple(snap["band_c"]) == (40.0, 232.0)
    assert isinstance(snap["revision"], int)


def test_the_revision_advances_on_each_adoption():
    c = _c()
    c._adopt_model(PARAMS, rmse=2.1, samples=10, band_c=(40.0, 232.0))
    first = c.get_model_snapshot()["revision"]
    c._adopt_model(dict(PARAMS, C_c=2600.0), rmse=2.0, samples=20, band_c=(40.0, 232.0))
    assert c.get_model_snapshot()["revision"] > first


def test_a_restored_revision_is_carried_forward_not_restarted():
    """controller_model_state.py rejects a non-advancing revision FOREVER once
    its counter falls behind, so a per-process counter would silently stop
    persisting after the first restart."""
    c = _c()
    assert (
        c.restore_model(
            {"version": 1, "revision": 41, "params": dict(PARAMS), "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
        )
        is True
    )
    c._adopt_model(dict(PARAMS, C_c=2600.0), rmse=1.9, samples=200, band_c=(40.0, 232.0))
    assert c.get_model_snapshot()["revision"] == 42


def test_restore_applies_the_parameters_to_the_running_model():
    c = _c()
    c.restore_model(
        {"version": 1, "revision": 1, "params": dict(PARAMS), "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
    )
    assert c.cfg["C_c"] == pytest.approx(2520.0)


def test_an_unphysical_snapshot_is_refused():
    c = _c()
    bad = dict(PARAMS, C_c=-1.0)
    assert (
        c.restore_model(
            {"version": 1, "revision": 1, "params": bad, "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
        )
        is False
    )
    assert c.cfg["C_c"] == pytest.approx(_DEFAULTS["C_c"])


def test_a_snapshot_from_a_future_schema_is_refused():
    c = _c()
    assert (
        c.restore_model(
            {"version": 99, "revision": 1, "params": dict(PARAMS), "rmse": 2.0, "samples": 100, "band_c": [40.0, 232.0]}
        )
        is False
    )


def test_a_malformed_snapshot_is_refused_rather_than_raising():
    c = _c()
    for junk in (None, {}, {"version": 1}, {"version": 1, "revision": "x", "params": {}}):
        assert c.restore_model(junk) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_model_snapshot.py -v`
Expected: FAIL — `_adopt_model` does not exist and `get_model_snapshot()` inherits `None`.

- [ ] **Step 3: Implement**

In `controller/mpc.py`, add to `Controller.__init__`:

```python
        self._model_revision = 0
        self._model_meta = None  # provenance of an adopted model, or None
```

Add the three methods (place them beside `get_status`):

```python
_MODEL_SCHEMA = 1
_MODEL_PARAM_KEYS = ("C_f", "C_c", "h_fc", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")


def _adopt_model(self, params, *, rmse, samples, band_c):
    """Take `params` into the running config and bump the revision.

    Rebuilding the NLP is the CALLER's business: adoption between cooks
    needs no rebuild because the next Hold builds fresh, and adoption
    during one is rate-limited elsewhere.
    """
    self.cfg.update({k: params[k] for k in self._MODEL_PARAM_KEYS if k in params})
    self._model_revision += 1
    self._model_meta = {
        "rmse": float(rmse),
        "samples": int(samples),
        "band_c": [float(band_c[0]), float(band_c[1])],
    }


def get_model_snapshot(self):
    if self._model_meta is None:
        return None
    return {
        "version": self._MODEL_SCHEMA,
        "revision": int(self._model_revision),
        "params": {k: float(self.cfg[k]) for k in self._MODEL_PARAM_KEYS},
        **self._model_meta,
    }


def restore_model(self, snapshot):
    from controller.model_promotion import PROMOTION_BOUNDS

    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("version") != self._MODEL_SCHEMA:
        return False
    params, revision = snapshot.get("params"), snapshot.get("revision")
    if not isinstance(params, dict) or not isinstance(revision, int):
        return False
    for key, (lo, hi) in PROMOTION_BOUNDS.items():
        value = params.get(key)
        try:
            value = float(value)
        except TypeError, ValueError:
            return False
        if not (lo <= value <= hi):
            return False
    self.cfg.update({k: float(params[k]) for k in self._MODEL_PARAM_KEYS if k in params})
    # Continue the persisted counter rather than starting a new one: the
    # store rejects a revision that does not advance, permanently.
    self._model_revision = revision
    self._model_meta = {
        "rmse": float(snapshot.get("rmse", float("inf"))),
        "samples": int(snapshot.get("samples", 0)),
        "band_c": [float(v) for v in snapshot.get("band_c", (0.0, 0.0))],
    }
    return True
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_model_snapshot.py -v`
Expected: PASS

- [ ] **Step 5: Surface the identified band in `get_status()` (R2.3)**

A model identified during a 225 °F hold is not the model at 450 °F — radiative
loss makes the effective time constant differ by ~3× — so the band is carried
rather than assumed global. Add a test asserting `get_status()` reports the
band and the fit error when a model has been adopted, and `None` when it has
not, then extend `get_status`'s allow-list to include them:

```python
def test_status_reports_the_identified_band(capsys):
    c = _c()
    assert c.get_status()["model"] is None
    c._adopt_model(PARAMS, rmse=2.1, samples=100, band_c=(40.0, 232.0))
    model = c.get_status()["model"]
    assert model["band_c"] == [40.0, 232.0]
    assert model["rmse"] == pytest.approx(2.1)
    json.dumps(model, allow_nan=False)
```

`get_status()` is an explicit allow-list precisely so that new state does not
leak into the MQTT payload by accident; this adds one bounded, JSON-safe entry
deliberately.

- [ ] **Step 6: Run the Hold persistence suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime/test_hold_model_persistence.py tests/unit/mpc -q`
Expected: PASS. Hold now has a controller that actually returns snapshots, so this suite exercises a path it previously could not reach.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_model_snapshot.py
jj describe --stdin <<'EOF'
feat(mpc): persist and restore a learned thermal model

Hold has driven the model store since the applied-output plumbing landed, but
MPC inherited ControllerBase's None and so stored nothing. It now snapshots
its parameters with provenance -- fit error, sample count and the temperature
band identified in -- and restores them through the same bounds the promotion
policy applies, so a corrupt or hand-edited record cannot put an unphysical
model on the grill. The revision continues the persisted counter rather than
restarting per process, which the store requires: it rejects a non-advancing
revision permanently.
EOF
```

---

## Task A7: Refit at the end of a cook

Implements R8.1 and R8.3. Depends on A4, A5, A6.

A refit re-simulates the whole history on every least-squares evaluation, so a 12-hour cook is minutes of CPU. It must not run on the control path, and it must not block teardown. The refit therefore runs on a worker thread and writes its result into the store; the **next** cook picks it up through the restore path that already exists. Nothing is rebuilt mid-cook.

**Files:**
- Modify: `controller/mpc.py` (`refit_from_cook`), `controller/runtime/runner.py` (surface), `controller/runtime/modes/hold.py` (`teardown`)
- Test: `tests/unit/mpc/test_mpc_refit.py` (create), `tests/unit/runtime/test_hold_refit_trigger.py` (create)

**Interfaces:**
- Produces: `Controller.refit_from_cook(history=None) -> Verdict` — fits, judges, and on acceptance calls `_adopt_model`. Returns the `Verdict` from `model_promotion.evaluate`.
- Produces: `ControllerRunner.refit_from_cook()` on both runners, delegating to the core.
- Consumes: `cook_history()` (A4), `model_promotion.evaluate` (A5), `_adopt_model` (A6), `update_mpc.fit_params`/`fit_quality`.

- [ ] **Step 1: Write the failing refit test**

Create `tests/unit/mpc/test_mpc_refit.py`:

```python
"""A finished cook improves the model, or is refused with a reason."""

import numpy as np
import pytest

from controller.mpc import _DEFAULTS, Controller
from controller.mpc_model import simulate_grey_box

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
TRUTH = dict(C_f=9.0, C_c=11000.0, h_fc=1.3, h_amb=2.7, K_Q=32.0, theta=110.0)


def _synthetic_cook():
    """A heat-up then a step down, from a grill that is NOT the default."""
    t = np.arange(0.0, 6000.0, 5.0)
    Q = np.where(t < 3000.0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=4, **TRUTH)
    return list(zip(t.tolist(), temp.tolist(), Q.tolist()))


def _c():
    return Controller(dict(_DEFAULTS, policy="nlp"), "C", dict(CYCLE))


def test_a_refit_moves_the_model_toward_the_grill_that_produced_the_cook():
    c = _c()
    before = c.cfg["C_c"] / c.cfg["h_amb"]
    verdict = c.refit_from_cook(_synthetic_cook())
    assert verdict.accepted is True
    after = c.cfg["C_c"] / c.cfg["h_amb"]
    truth = TRUTH["C_c"] / TRUTH["h_amb"]
    assert abs(after - truth) < abs(before - truth)


def test_a_refit_records_the_band_it_learned_in():
    c = _c()
    c.refit_from_cook(_synthetic_cook())
    lo, hi = c.get_model_snapshot()["band_c"]
    assert lo < hi
    assert hi > 200.0  # the synthetic cook is a high-temperature run


def test_too_few_samples_is_refused_without_fitting():
    c = _c()
    v = c.refit_from_cook([(0.0, 20.0, 50.0), (5.0, 21.0, 50.0)])
    assert v.accepted is False
    assert "sample" in v.reason.lower()
    assert c.get_model_snapshot() is None


def test_a_refit_uses_the_live_history_when_given_none():
    c = _c()
    c.set_target(110.0)
    for _ in range(3):
        c.update(100.0)
    v = c.refit_from_cook()  # too short to accept, but must not raise
    assert v.accepted is False


def test_a_second_worse_cook_does_not_replace_a_good_model():
    c = _c()
    assert c.refit_from_cook(_synthetic_cook()).accepted is True
    good = c.cfg["C_c"]
    # A flat, uninformative cook: no excitation, so any model fits it equally.
    flat = [(float(i * 5), 100.0, 50.0) for i in range(400)]
    assert c.refit_from_cook(flat).accepted is False
    assert c.cfg["C_c"] == pytest.approx(good)


def test_the_refit_is_bounded_in_time():
    """A 12-hour cook is ~8640 rows and each least-squares evaluation
    re-simulates all of them. Decimation keeps this off the minutes scale."""
    import time

    t = np.arange(0.0, 43200.0, 5.0)
    Q = np.where((t // 1800) % 2 == 0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=20.0, sigma=1.4e-9, n_delay=4, **TRUTH)
    history = list(zip(t.tolist(), temp.tolist(), Q.tolist()))
    c = _c()
    t0 = time.perf_counter()
    c.refit_from_cook(history)
    assert time.perf_counter() - t0 < 30.0
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_refit.py -v`
Expected: FAIL — `refit_from_cook` does not exist.

- [ ] **Step 3: Implement the refit**

In `controller/mpc.py`, module level:

```python
#: A refit re-simulates the whole series per least-squares evaluation, so cost
#: is linear in samples and the fit is not improved by density -- a cook's
#: SHAPE identifies it. Decimate to this many rows before fitting.
_REFIT_MAX_SAMPLES = 1200

#: Below this, a record is an interrupted cook rather than a description of a
#: grill, and fitting it would produce a confident answer from nothing.
_REFIT_MIN_SAMPLES = 120
```

Add the method:

```python
def refit_from_cook(self, history=None):
    """Refit the thermal model from a finished cook and judge the result."""
    import numpy as np

    from controller.model_promotion import evaluate
    from controller.update_mpc import fit_params, fit_quality

    rows = list(history if history is not None else self._history)
    if len(rows) < _REFIT_MIN_SAMPLES:
        return _Verdict(False, f"only {len(rows)} samples; need {_REFIT_MIN_SAMPLES}")

    step = max(1, len(rows) // _REFIT_MAX_SAMPLES)
    rows = rows[::step]
    t = np.array([r[0] for r in rows], dtype=float)
    temp = np.array([r[1] for r in rows], dtype=float)
    Q = np.array([r[2] for r in rows], dtype=float)
    t = t - t[0]

    T_amb = float(self.cfg["T_amb"])
    init = {k: float(self.cfg[k]) for k in ("C_f", "C_c", "h_fc", "h_amb", "K_Q", "theta")}
    try:
        fitted = fit_params(
            t, temp, Q, T_amb=T_amb, init=init, sigma=float(self.cfg["sigma"]), n_delay=int(self.cfg["n_delay"])
        )
        cand_rmse, _ = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
        incumbent = {k: float(self.cfg[k]) for k in self._MODEL_PARAM_KEYS}
        inc_rmse, _ = fit_quality(t, temp, Q, incumbent, T_amb=T_amb)
    except (ValueError, FloatingPointError) as e:
        return _Verdict(False, f"fit failed: {e}")

    verdict = evaluate(
        fitted,
        incumbent,
        candidate_rmse=cand_rmse,
        incumbent_rmse=inc_rmse,
        n_horizon=int(self.cfg["n_horizon"]),
        t_step=float(self.cfg["t_step"]),
    )
    print(f"[mpc] refit: {verdict.reason} (candidate RMSE {cand_rmse:.2f} C, incumbent {inc_rmse:.2f} C)")
    if verdict.horizon_needed:
        print(
            f"[mpc] refit: this model wants n_horizon >= {verdict.horizon_needed} at t_step {self.cfg['t_step']:.0f} s"
        )
    if verdict.accepted:
        self._adopt_model(fitted, rmse=cand_rmse, samples=len(rows), band_c=(float(temp.min()), float(temp.max())))
    return verdict
```

Import the `Verdict` dataclass at the top of the module as `_Verdict`:

```python
from controller.model_promotion import Verdict as _Verdict
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_mpc_refit.py -v`
Expected: PASS. If `test_the_refit_is_bounded_in_time` fails, lower `_REFIT_MAX_SAMPLES` — do **not** raise the time bound; the whole point is that this cannot become a minutes-long job.

- [ ] **Step 5: Write the failing trigger test**

Create `tests/unit/runtime/test_hold_refit_trigger.py`:

```python
"""Hold asks the controller to refit when the cook ends -- and never during."""

from tests.fakes.runner import FakeControllerRunner


def test_teardown_requests_a_refit(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["controller"]["config"]["mpc"] = {"enable_identification": True}
    hold.setup()
    hold.teardown(225)
    assert runner.refits == 1


def test_no_refit_when_identification_is_off(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["controller"]["config"]["mpc"] = {"enable_identification": False}
    hold.setup()
    hold.teardown(225)
    assert runner.refits == 0


def test_no_refit_during_the_cook(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["controller"]["config"]["mpc"] = {"enable_identification": True}
    hold.setup()
    for _ in range(5):
        hold.on_tick(225)
    assert runner.refits == 0


def test_a_refit_failure_does_not_break_teardown(hold_cycle):
    """Teardown runs on the way out of a cook. A refit is a nicety; losing the
    orderly shutdown is not."""
    runner = FakeControllerRunner(period=0.01)
    runner.refit_raises = RuntimeError("solver exploded")
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["controller"]["config"]["mpc"] = {"enable_identification": True}
    hold.setup()
    hold.teardown(225)  # must not raise
```

- [ ] **Step 6: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime/test_hold_refit_trigger.py -v`
Expected: FAIL — `FakeControllerRunner` has no `refits`.

- [ ] **Step 7: Extend the runner surface and the fake**

In `controller/runtime/runner.py`, add to the `ControllerRunner` ABC beside `restore_model`:

```python
    @abstractmethod
    def refit_from_cook(self): ...
```

In `SyncControllerRunner`:

```python
    def refit_from_cook(self):
        fn = getattr(self._core, "refit_from_cook", None)
        return fn() if fn else None
```

In `ThreadedControllerRunner`, the same body — the refit runs on the caller's thread at teardown, after the worker has been asked to stop, so it does not contend with a solve.

In `tests/fakes/runner.py`, add to `FakeControllerRunner.__init__`:

```python
        self.refits = 0
        self.refit_raises = None
```

and the method:

```python
    def refit_from_cook(self):
        self.refits += 1
        if self.refit_raises:
            raise self.refit_raises
```

- [ ] **Step 8: Fire it from Hold's teardown**

In `controller/runtime/modes/hold.py`, `teardown`, before the existing body:

```python
        # A refit is a between-cooks activity: it re-simulates the whole
        # history many times over, and its result is picked up by the NEXT
        # cook's restore rather than rebuilding anything now.
        cfg = self.settings["controller"].get("config", {}).get("mpc", {})
        if self._runner is not None and cfg.get("enable_identification"):
            try:
                self._runner.refit_from_cook()
            except Exception as e:  # a refit must never cost an orderly shutdown
                _control.eventLogger.error(f"Model refit failed at cook end: {e}")
```

- [ ] **Step 9: Run both suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/runtime tests/unit/mpc -q`
Expected: PASS. `tests/unit/runtime/test_fake_runner_signature_parity.py` exists to catch a fake that has drifted from the real interface — if it fails, the fake is missing the new method, which is the check working.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py controller/runtime/runner.py controller/runtime/modes/hold.py tests/fakes/runner.py tests/unit/mpc/test_mpc_refit.py tests/unit/runtime/test_hold_refit_trigger.py
jj describe --stdin <<'EOF'
feat(mpc): refit the thermal model at the end of each cook

Every cook opens with a startup ramp, which is the richest excitation the
grill will ever produce and is free. At teardown the controller refits its
grey-box parameters from that cook's own history, scores the candidate against
the incumbent ON THE SAME DATA, and adopts it only if the promotion policy
agrees. The result reaches the grill through the next cook's restore, so
nothing is rebuilt mid-cook.

The history is decimated before fitting: cost is linear in samples and a
cook's shape, not its density, is what identifies the grill. A refit that
fails is logged and dropped -- it must never cost an orderly shutdown.
EOF
```

---

## Task A8: The settings surface

Implements R7.4 and R4.3. Depends on A7.

**Files:**
- Modify: `controller/controllers.json` (mpc options)
- Modify: `web-react/src/components/settings/tabs/ControllerTab.tsx`
- Test: `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx` (append)

**Interfaces:** consumes `mpcFanConflict`'s neighbouring pattern in `helpers/settings/mpcFan.ts`; add the note beside it rather than inventing a second mechanism.

- [ ] **Step 1: Add the option to the metadata**

In `controller/controllers.json`, in the `mpc` `config` array, after `log_data`:

```json
        {
          "option_name": "enable_identification",
          "option_friendly_name": "Learn This Grill",
          "option_description": "After each cook, refit the thermal model from that cook and keep it if it describes the grill better. Requires the do-mpc extra and disables the fast neural policy. [Default=false]",
          "option_type": "bool",
          "option_default": false,
          "hidden": false
        }
```

- [ ] **Step 2: Write the failing UI test**

Append to `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx`, adding `enable_identification` to the shared `controllerMeta` mpc config first (mirroring the `enable_fan_input` entry already there):

```tsx
describe("ControllerTab identification note", () => {
  const ctx = (learning: boolean) => ({
    settings: {
      platform: { dc_fan: true },
      pwm: { pwm_control: true },
      controller: {
        selected: "mpc",
        config: { mpc: { enable_fan_input: false, enable_identification: learning } },
      },
    },
    mode: "Stop",
    controllerMeta,
  });

  it("explains the policy cost when learning is on", () => {
    renderRoute(<ControllerTab />, ctx(true));
    expect(screen.getByText(/neural policy/i)).toBeInTheDocument();
  });

  it("says nothing when learning is off", () => {
    renderRoute(<ControllerTab />, ctx(false));
    expect(screen.queryByText(/neural policy/i)).toBeNull();
  });

  it("appears as soon as the toggle is flipped, before saving", () => {
    renderRoute(<ControllerTab />, ctx(false));
    fireEvent.click(screen.getByRole("button", { name: "Learn This Grill" }));
    expect(screen.getByText(/neural policy/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd web-react && bun run test ControllerTab`
Expected: FAIL — no such text.

- [ ] **Step 4: Render the note**

In `ControllerTab.tsx`, beside the existing `fanConflict` derivation:

```tsx
  // Derived from the draft so the consequence is visible while deciding, not
  // after saving.
  const learning = selected === "mpc" && !!values.enable_identification;
```

and immediately above the `fanConflict` message:

```tsx
      {learning && (
        <p className="pf-settings-hint">
          While this grill is learning, the controller solves the full optimisation each
          step: a learned model no longer matches the pre-trained neural policy, so that
          fast path is disabled and the do-mpc extra is required.
        </p>
      )}
```

- [ ] **Step 5: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
cd .. && .venv/bin/ruff format controller/controllers.json 2>/dev/null || true
jj describe --stdin <<'EOF'
feat(web): expose grill learning and its policy cost

Learning is off by default and says what it costs where it is switched on: a
learned calibration cannot match the pre-trained neural policy, so enabling it
trades that fast path for the full solve and requires the do-mpc extra.
EOF
```

---

## Task A9: Closed-loop acceptance

Implements the spec's Verification section. Depends on A1–A8.

**Files:**
- Test: `tests/e2e/test_mpc_learns_a_grill.py` (create)

**Interfaces:** none new.

- [ ] **Step 1: Write the acceptance test**

Create `tests/e2e/test_mpc_learns_a_grill.py`:

```python
"""Successive cooks on a grill the controller has never seen must get better.

MAKGrillSim reproduces the 2026-08-02 incident: a real MAK, ~10x slower than
the shipped model, which overshot a 450 F setpoint by 70 F. This is the test
the whole design exists to pass.
"""

import numpy as np
import pytest

from controller.grill_sim import MAKGrillSim
from controller.mpc import _DEFAULTS, Controller

CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}
SETPOINT_C = (450 - 32) * 5 / 9


def _cook(controller, seconds=5400):
    """One cook. Returns peak chamber temperature in F."""
    sim = MAKGrillSim(seed=0, T0=40.7, fixed_fan=1.0)
    controller.set_target(SETPOINT_C)
    ratio, peak = 0.1, 0.0
    for t in range(seconds):
        if t % int(controller.cfg["control_period"]) == 0:
            ratio = float(np.clip(controller.update(sim.measured())["cycle_ratio"], 0.1, 0.9))
        sim.step((t % 25) < ratio * 25, 1.0)
        peak = max(peak, sim.true_Tc)
    return peak * 9 / 5 + 32


def _fresh(cfg):
    return Controller(dict(cfg), "C", dict(CYCLE))


@pytest.mark.slow
def test_overshoot_falls_across_successive_cooks():
    cfg = dict(_DEFAULTS, policy="nlp", enable_identification=True)
    peaks = []
    for _ in range(3):
        c = _fresh(cfg)
        peaks.append(_cook(c))
        if c.refit_from_cook().accepted:
            snap = c.get_model_snapshot()
            cfg.update(snap["params"])
            # A learned model is useless in a horizon that cannot see it.
            tau = cfg["C_c"] / cfg["h_amb"]
            cfg["n_horizon"] = int(min(600, max(cfg["n_horizon"], np.ceil(tau / cfg["t_step"]))))
    assert peaks[-1] < peaks[0] - 10.0, f"overshoot did not improve: {peaks}"
    assert peaks[-1] == min(peaks), f"a later cook was worse: {peaks}"


@pytest.mark.slow
def test_learning_off_is_unchanged():
    """The negative control. Identification must be invisible until asked for."""
    c = _fresh(dict(_DEFAULTS, policy="nlp", enable_identification=False))
    peak = _cook(c, seconds=1200)
    assert c.get_model_snapshot() is None
    baseline = _fresh(dict(_DEFAULTS, policy="nlp"))
    assert peak == pytest.approx(_cook(baseline, seconds=1200), abs=1e-6)
```

- [ ] **Step 2: Run it**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/e2e/test_mpc_learns_a_grill.py -v`
Expected: PASS.

If `test_overshoot_falls_across_successive_cooks` fails, read which of the three cooks refused promotion and why — `refit_from_cook` prints its verdict and both RMSEs. **Do not widen the assertion.** A refusal means either the cook lacked the excitation to identify the grill (fix the scenario: a single flat hold identifies nothing) or the promotion margins are wrong (fix them in A5, with a test). An overshoot that fails to improve while promotions are being accepted means the mapping from a better fit to better control is broken, which is a finding, not a tolerance.

- [ ] **Step 3: Run the whole suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
.venv/bin/ruff format tests/e2e/test_mpc_learns_a_grill.py
jj describe --stdin <<'EOF'
test(mpc): acceptance -- successive cooks reduce overshoot on a real grill

Three cooks on MAKGrillSim, the plant identified from the incident that
started this work, starting from the shipped defaults that overshot 450 F by
70 F. Each cook refits, and the peak must fall and never regress. The negative
control pins that a controller with identification off is bit-identical to
today, so this whole mechanism is invisible until it is asked for.
EOF
```

---

# Slice B — online identification

**Blocked on `controller/fopdt_identifier.py`** (Tasks 2–5 of `2026-08-01-adaptive-smith-predictor.md`) and on that plan's Task 17 fix round. Do not start B1 until `FOPDTIdentifier` exists and Slice A has landed.

**Tasks B2–B7 are specified at intent level, not as literal code.** Every other
task in this plan carries the exact code to write; these deliberately do not,
because their call sites depend on `FOPDTIdentifier`'s interface, which has not
been written yet. Code written against a guessed API would have to be
discarded, and would read as authoritative while being invented. **Expand each
into full TDD steps once that module exists** — the tests named in each are the
behaviours to pin, not a summary of them.

**One request to make of that plan before its Task 5 is written:** `FOPDTIdentifier`'s intake should be a generic piecewise-constant **input series**, not a duty-shaped one. MPC feeds applied firing rate `Q`; PID-SP feeds duty. If the contract is `(input, output)` rather than `(duty, temp)`, MPC consumes it unchanged and B3 is wiring instead of adaptation. This is a comment on one task today versus a refactor later.

## Task B1: FOPDT ↔ grey-box mapping

Implements R2.1 and R1.2.

**Files:**
- Create: `controller/model_mapping.py`
- Test: `tests/unit/mpc/test_model_mapping.py` (create)

**Interfaces:**
- Produces: `fopdt_to_grey_box(K, tau, theta, *, base) -> dict` and `grey_box_to_fopdt(cfg) -> tuple[float, float, float]`.

The reduction: hold `C_f` and `h_fc` (the firepot is fast and not identifiable from chamber temperature), hold `sigma` and `n_delay`, map `theta` straight through, and recover `C_c` and `K_Q` from `tau` and `K` at the incumbent's `h_amb`. Only the ratios are identifiable, which is why `h_amb` is held rather than fitted.

- [ ] **Step 1: Write the failing round-trip tests**

Create `tests/unit/mpc/test_model_mapping.py`:

```python
"""FOPDT is what the identifier speaks; the grey-box is what the MPC runs."""

import pytest

from controller.mpc import _DEFAULTS
from controller.model_mapping import fopdt_to_grey_box, grey_box_to_fopdt

BASE = dict(_DEFAULTS)


def test_round_trip_preserves_the_time_constant():
    K, tau, theta = 0.9, 3750.0, 95.0
    cfg = fopdt_to_grey_box(K, tau, theta, base=BASE)
    K2, tau2, theta2 = grey_box_to_fopdt(cfg)
    assert tau2 == pytest.approx(tau, rel=1e-6)
    assert theta2 == pytest.approx(theta, rel=1e-6)
    assert K2 == pytest.approx(K, rel=1e-6)


def test_the_held_parameters_are_untouched():
    cfg = fopdt_to_grey_box(0.9, 3750.0, 95.0, base=BASE)
    for key in ("C_f", "h_fc", "sigma", "n_delay", "T_amb"):
        assert cfg[key] == BASE[key]


def test_a_longer_time_constant_raises_the_chamber_capacity():
    slow = fopdt_to_grey_box(0.9, 7500.0, 95.0, base=BASE)
    fast = fopdt_to_grey_box(0.9, 3750.0, 95.0, base=BASE)
    assert slow["C_c"] > fast["C_c"]


def test_gain_is_converted_from_the_identifier_units():
    """The identifier is canonically Fahrenheit per unit input; the grey-box is
    Celsius per unit Q. A gain that crosses that boundary unconverted is a 1.8x
    error in the parameter that sets steady-state demand."""
    cfg_f = fopdt_to_grey_box(1.8, 3750.0, 95.0, base=BASE, units="F")
    cfg_c = fopdt_to_grey_box(1.0, 3750.0, 95.0, base=BASE, units="C")
    assert cfg_f["K_Q"] == pytest.approx(cfg_c["K_Q"], rel=1e-9)


def test_a_degenerate_time_constant_is_rejected():
    for bad in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            fopdt_to_grey_box(0.9, bad, 95.0, base=BASE)
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/test_model_mapping.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the mapping**

Create `controller/model_mapping.py` with `fopdt_to_grey_box` computing `C_c = tau * h_amb` and `K_Q = K * h_amb` (in Celsius units, converting `K` by `5/9` when `units="F"`), holding `C_f`, `h_fc`, `sigma`, `n_delay`, `T_amb` and `h_amb` from `base`, and setting `theta` directly; and `grey_box_to_fopdt` inverting it. Raise `ValueError` on a non-finite or non-positive `tau`.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Format and commit**

---

## Task B2: Reject an edge-saturated dead time

Implements R1.3. The grid runs 0–120 s in 5 s steps; the MAK measures 93–110 s, so real grills sit near the ceiling and a slower one saturates it. An argmin landing on the last candidate means "at least 120 s", not "120 s", and must not promote.

**Files:** `controller/model_promotion.py` (extend `evaluate` with an `at_grid_edge` argument), tests alongside.

- [ ] **Step 1** Write a test asserting a candidate flagged `at_grid_edge=True` is refused with a reason naming the grid.
- [ ] **Step 2** Run it; expect failure.
- [ ] **Step 3** Add the parameter, defaulting to `False` so Slice A's callers are unaffected.
- [ ] **Step 4** Run; expect pass. **Step 5** Commit.

---

## Task B3: Feed the identifier from the applied-output stream

Implements R1.1. Depends on B1 and on `FOPDTIdentifier` existing.

**Files:** `controller/mpc.py`, `tests/unit/mpc/test_mpc_identifier_feed.py` (create).

The identifier consumes `(t, input, output)` where input is `_applied_Q` and output is the measured temperature — the same rows `cook_history()` already collects in A4, delivered live instead of at the end. Intervals whose `AppliedOutput` was not controller-commanded are rejected by the identifier itself; MPC's job is only to hand over the rows.

- [ ] **Step 1** Test that each `update()` submits exactly one observation, carrying the applied rate rather than the command.
- [ ] **Step 2** Test that no observation is submitted when identification is off.
- [ ] **Step 3** Run; expect failure. **Step 4** Implement. **Step 5** Run; expect pass. **Step 6** Commit.

---

## Task B4: Gate on estimator contention

Implements R6.1. The EKF's disturbance state `d` absorbs exactly the mismatch the identifier needs to see; if both chase it, the identifier learns nothing and the disturbance hides the error.

**Files:** `controller/mpc.py`, tests alongside.

- [ ] **Step 1** Test that observations are withheld while `|d|` is moving faster than a threshold.
- [ ] **Step 2** Test that a steady `d`, however large, does not withhold — a constant offset is not contention.
- [ ] **Step 3** Run; expect failure. **Step 4** Implement. **Step 5** Run; expect pass. **Step 6** Commit.

---

## Task B5: Rate-limited, quiescent-only promotion

Implements R7.5. Adopting parameters mid-cook rebuilds the NLP and discards the warm start: one build (0.2–0.8 s) plus one cold solve (up to 610 ms at `n_horizon` 240), which an unrestricted promoter would spend precisely when the grill is moving fastest.

**Files:** `controller/mpc.py`, tests alongside.

- [ ] **Step 1** Test that two promotions cannot occur within the minimum interval.
- [ ] **Step 2** Test that a promotion is deferred while the measured temperature is moving faster than a threshold, and applied once it settles.
- [ ] **Step 3** Test that a deferred promotion is not lost.
- [ ] **Step 4** Run; expect failure. **Step 5** Implement. **Step 6** Run; expect pass. **Step 7** Commit.

---

## Task B6: Horizon adequacy on promotion

Implements R5.2–R5.4. `evaluate` already reports `horizon_needed` (A5); this acts on it.

Measured budget at `t_step = 25 s`, `control_period = 5 s`, warm-started and capped: `n_horizon` 144 costs 5.5 % of the period at p95, 240 costs 10.8 %. The cap is expressed as a fraction of `control_period` and evaluated on the host, because the nominal target is a Pi 5 and these numbers come from a faster machine.

**Files:** `controller/mpc.py`, tests alongside.

- [ ] **Step 1** Test that a promotion needing a longer horizon raises `n_horizon` up to the budget.
- [ ] **Step 2** Test that the budget caps the rise rather than the model dictating it.
- [ ] **Step 3** Test that a horizon rise is reported.
- [ ] **Step 4** Run; expect failure. **Step 5** Implement. **Step 6** Run; expect pass. **Step 7** Commit.

---

## Task B7: Online acceptance

Mirrors A9 for the live path: one long cook on `MAKGrillSim` starting from the shipped defaults must improve *within* the cook, with a negative control proving the online path is inert when switched off, and an assertion that no promotion occurred during a transient.

- [ ] **Step 1** Write it. **Step 2** Run. **Step 3** Full suite. **Step 4** Commit.

---

## Final verification

- [ ] Full Python suite: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
- [ ] Full web gate: `cd web-react && bun run typecheck && bun run test && bun run lint`
- [ ] A grill with identification off behaves exactly as it did before this plan — the characterization goldens prove it, not an argument.
- [ ] No source comment added by this work narrates the incident, the change, or a measurement.
