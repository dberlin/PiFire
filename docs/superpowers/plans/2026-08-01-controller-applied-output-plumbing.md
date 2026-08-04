# Controller Applied-Output Plumbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every controller a way to learn the duty that actually reached the auger, publish JSON-safe diagnostics, and persist a learned model across restarts — then use all three in MPC.

**Architecture:** A new stdlib-only leaf module, `controller/applied_output.py`, defines the report (`AppliedOutput` + `OutputSource`). Four default-inert methods land on `ControllerBase`; both runners forward them; Hold mode reports at every point where the duty reaching the auger diverges from the controller's request. A small SQLite-backed store keyed by controller name persists model snapshots. MPC becomes the first consumer of all of it.

**Tech Stack:** Python 3.14+, numpy, pytest, jujutsu (`jj`), `uv` for the venv, `ruff` for formatting.

**Spec:** `docs/superpowers/specs/2026-08-01-adaptive-smith-predictor-design.md`. Read it before Task 1. This plan implements the sections `applied_output.py`, `base.py`, `runner.py`, `hold.py`, `controller_model_state.py`, `mpc.py`, "The net must keep agreeing with the NLP", and `tests/fakes/runner.py`.

**This plan changes no PID-SP behavior.** PID-SP itself is Plan B (`2026-08-01-adaptive-smith-predictor.md`), which depends entirely on this one.

## Global Constraints

Every task's requirements implicitly include this section.

- **Commit with `jj`, never `git`.** This is a colocated repo, so `git commit` silently works and causes damage. Use `jj new` *before* the first Write of a task, and `jj describe --stdin` to set the message (there is no `-F` flag). Never run `jj squash` after editing — your edits are already in `@`, and squashing moves them into the parent. Recovery from a mistake is `jj op restore`, not `jj restore`.
- **Format before every commit:** `.venv/bin/ruff format <changed files>`. NEVER `uvx ruff` — the repo pins `ruff>=0.8.0,<0.16` and `uvx` fetches a newer one.
- **Run tests with `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`.** A bare `python`/`pytest` gives false failures; the venv has PySide6.
- **Python 3.14+ except syntax:** `except ValueError, TypeError:` (no parentheses) is ruff-canonical in this repo. Do not "fix" it to a tuple.
- **Comments state intent, not history.** Never write a comment narrating the change, a measurement, or your reasoning. Say what the code achieves.
- **SQLite (`pifire.db`) is authoritative.** `settings.json` is only ever an export/backup/first-boot import. Persistence goes through the datastore.
- **The controller layer must not import from `controller/runtime/**`.** `controller/applied_output.py` is a leaf: stdlib only.
- **numpy is available** in `controller/*.py`. do-mpc is not — it is an optional extra, and `tests/unit/mpc/` skips when it is missing.
- **GrillSim (`controller/grill_sim.py`) is the plant of record.** Do not substitute `HiFiGrill`.
- **Never `pgrep -f` into `kill`** — the pattern matches your own shell.
- **Do not run `git clean -fdx`** — it destroys the git-ignored SDD ledger.
- If you are a subagent: use `ctx_execute` / serena for file and test work rather than dumping raw output into context, and ingest your outcome to the Hindsight bank `claude_code` when you finish.

## File Structure

| File | Responsibility |
| --- | --- |
| `controller/applied_output.py` (new) | `OutputSource`, `AppliedOutput`, `classify_output_source`, `seed_output`. Stdlib only, no project imports. |
| `controller/base.py` | Four default-inert capability methods on `ControllerBase`. |
| `controller/runtime/runner.py` | ABC declarations; sync forwarding; threaded queueing/replay; `controller_state()` prefers `get_status()`. |
| `controller/runtime/modes/base.py` | `_on_manual_output` no-op hook + its single call site in `_apply_manual_overrides`. |
| `controller/runtime/modes/hold.py` | Five report sites; model restore at setup and reconfigure; model save on the per-tick path. |
| `common/controller_model_state.py` (new) | `ControllerModelStore`: `load(name)` / `save(name, snapshot)` over one SQLite generic key. |
| `controller/mpc.py` | `get_status()`; `set_output()` and the `_last_Q` / `_applied_Q` split. |
| `tests/fakes/runner.py` | `FakeControllerRunner` grows the three new forwards and records them. |
| `docs/superpowers/experiments/controller_matrix.py` (new) | The GrillSim scenario matrix, before/after, both controllers. |
| `docs/superpowers/experiments/net_vs_nlp_replay.py` (new) | Pointwise net-versus-NLP policy disagreement. |
| `docs/superpowers/experiments/sample_mpc.py` | Conditional: pause intervals in `_episode_span`. |

Tests land beside their existing neighbours:

| Test file | Covers |
| --- | --- |
| `tests/unit/controller/test_applied_output.py` (new) | Precedence table, dataclass, `seed_output`. |
| `tests/unit/controller/test_controller_capabilities.py` (new) | `ControllerBase` defaults are inert. |
| `tests/unit/runtime/test_sync_runner.py` | Sync forwarding, `controller_state()` preference. |
| `tests/unit/runtime/test_threaded_runner.py` | Queueing, ordered replay before `update()`, snapshot/restore across the thread. |
| `tests/unit/runtime/test_hold_applied_output.py` (new) | Each Hold report site. |
| `tests/unit/common/test_controller_model_state.py` (new) | Round-trip, malformed envelopes, non-advancing revision, model-agnosticism. |
| `tests/unit/mpc/test_mpc_controller.py` | `get_status()` JSON-safety; `set_output()` inverse allocation. |

## Parallelization

Tasks 1 and 2 capture baselines and must both start **before any production code changes**, but they are independent of each other and of everything else — they only read. Run them first, concurrently.

After baselines land, three chains are independent and can run in isolated `jj` workspaces:

- **Chain P (plumbing):** 3 → 4 → {5, 6} → 7 → 8 → 9
- **Chain S (store):** 10 (needs nothing; 11 joins it to Chain P)
- **Chain M (MPC):** 12 (needs nothing) and 13 (needs Task 3 only)

Task 11 needs both 9 and 10. Task 14 needs 12 and 13. Task 15 needs 14.

Concurrency requires isolated `jj` workspaces — disjoint file lists alone are not enough. Each workspace needs `.lsp.json` copied in (it is gitignored, so `jj workspace add` skips it). Chains P and M both touch `tests/fakes/runner.py`? No — only Chain P does. They do not overlap.

---

### Task 1: GrillSim scenario matrix and pre-change baseline

Capture the before-numbers for both controllers **before any production code changes**. The harness must run unchanged against pre- and post-change code, so it probes for `set_output` rather than requiring it.

**Files:**
- Create: `docs/superpowers/experiments/controller_matrix.py`
- Create: `docs/superpowers/experiments/_matrix_baseline.json` (generated)

**Interfaces:**
- Produces: `run_scenario(controller, scenario, seed) -> dict` and `SCENARIOS: dict[str, Scenario]`, used again by Tasks 14 and by Plan B's final task.

- [ ] **Step 1: `jj new` before writing anything**

```bash
jj new -m "wip: grillsim matrix harness"
```

- [ ] **Step 2: Write the harness**

Create `docs/superpowers/experiments/controller_matrix.py`:

```python
#!/usr/bin/env python3
"""Scenario matrix for a controller against GrillSim.

Drives a controller core directly -- no Hold mode, no datastore -- so a run is
reproducible from (controller, scenario, seed) alone. The lid-open scenario
reproduces what Hold does to the auger during a pause and reports it through
`set_output` when the controller has that capability, so the same harness
measures code from before and after applied-output feedback exists.
"""

import argparse
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from controller.grill_sim import GrillSim  # noqa: E402

OUT = "./docs/superpowers/experiments/_matrix_baseline.json"

CYCLE_DATA = {"HoldCycleTime": 20, "u_min": 0.15, "u_max": 0.9, "PMode": 2}

CONTROLLER_CONFIGS = {
    "pid_sp": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "pid_ac": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "mpc": {},
}


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


@dataclass
class Scenario:
    name: str
    duration_s: int
    # (start_second, setpoint_F); the first entry must start at 0
    setpoints: list = field(default_factory=list)
    # (start_second, duration_s) windows where Hold would pin the auger off
    lid_open: list = field(default_factory=list)


SCENARIOS = {
    "steady_225": Scenario("steady_225", 3 * 3600 + 1800, [(0, 225.0)]),
    "steady_350": Scenario("steady_350", 3 * 3600 + 1800, [(0, 350.0)]),
    "steady_450": Scenario("steady_450", 3 * 3600 + 1800, [(0, 450.0)]),
    "step_225_275": Scenario("step_225_275", 4 * 3600, [(0, 225.0), (2 * 3600, 275.0)]),
    "capability_600": Scenario("capability_600", 3 * 3600, [(0, 600.0)]),
    "lid_open_225": Scenario("lid_open_225", 3 * 3600, [(0, 225.0)], [(2 * 3600, 120)]),
}


def _setpoint_at(scenario, t):
    sp = scenario.setpoints[0][1]
    for start, value in scenario.setpoints:
        if t >= start:
            sp = value
    return sp


def _lid_open_at(scenario, t):
    return any(start <= t < start + dur for start, dur in scenario.lid_open)


def _report(core, ratio, source_name, t, requested=None):
    """Report applied duty when the controller can hear it; no-op otherwise."""
    setter = getattr(core, "set_output", None)
    if setter is None:
        return
    from controller.applied_output import AppliedOutput, OutputSource

    setter(AppliedOutput(ratio=ratio, source=OutputSource(source_name), timestamp=float(t), requested=requested))


def _auger_toggle_tick(auger_on, auger_toggle, t, ratio, cycle_time):
    """Port of controller.runtime.modes.base.ControlMode._auger_cycle_tick,
    returning the auger's exact fractional on-time over the window [t, t+1)
    instead of a single boolean sample of it.

    Production evaluates this strict-`>` toggle at its work-loop resolution
    (~20 Hz) and drives a physical auger that integrates fuel delivery
    continuously between samples. GrillSim can only be stepped once per
    simulated second, so sampling the toggle as a boolean once per second
    would quantize fuel delivery to whichever side of a transition the sample
    landed on. Instead, the transition instant within the window is located
    exactly (continuous time, so `>` vs `>=` at that single instant does not
    affect the fraction) and returned as the portion of the window it covers.
    This is arithmetic rather than sub-stepping, and it depends on at most one
    transition ever falling inside a 1 s window -- the assertion below makes
    that requirement explicit instead of leaving it to fail silently.

    `auger_toggle` is carried as the exact (possibly fractional) transition
    time rather than snapped to a tick, so later windows see the true elapsed
    time since the last transition. A re-solve to a smaller ratio can put the
    threshold computed from the old `auger_toggle` in the past relative to the
    current window; `transition` is clamped to `t` so that case reads as "flip
    right now" instead of a negative or >1 fraction, and the return value is
    clamped to [0, 1] as a backstop.
    """
    assert cycle_time * ratio >= 1 and cycle_time * (1 - ratio) >= 1, (
        f"ratio={ratio} at cycle_time={cycle_time} gives an on- or off-phase "
        "shorter than one window -- more than one transition could fall "
        "inside a single tick, which this closed-form arithmetic can't represent"
    )
    was_on = auger_on
    if not was_on:
        transition = max(auger_toggle + cycle_time * (1 - ratio), t)
        if transition >= t + 1:
            return False, auger_toggle, 0.0
        return True, transition, min(max(t + 1 - transition, 0.0), 1.0)
    transition = max(auger_toggle + cycle_time * ratio, t)
    if transition >= t + 1:
        return True, auger_toggle, 1.0
    return False, transition, min(max(transition - t, 0.0), 1.0)


class _SimClock:
    """Callable replacement for `time.time`, advanced once per simulated
    second so a controller reading the wall clock for its own `dt` observes
    the step size this harness actually models, not the wall-clock time
    between tight-loop calls."""

    def __init__(self, t0):
        self.t = t0

    def __call__(self):
        return self.t


def run_scenario(controller, scenario, seed):
    # Some controllers (pid_sp, pid_ac) read time.time() for their own dt;
    # replacing it with a clock this loop drives makes their dt match the
    # simulated second the loop models, instead of the wall-clock nanoseconds
    # between calls in a tight loop. Controllers that don't read the wall
    # clock (mpc) are unaffected. Started one HoldCycleTime before t=0 so the
    # very first solve -- which happens immediately, since next_solve starts
    # at 0.0 -- sees a full period of elapsed time rather than dt=0.
    clock = _SimClock(-float(CYCLE_DATA["HoldCycleTime"]))
    real_time_time = time.time
    time.time = clock
    try:
        mod = importlib.import_module(f"controller.{controller}")
        core = mod.Controller(dict(CONTROLLER_CONFIGS[controller]), "F", dict(CYCLE_DATA))
        plant = GrillSim(seed=seed)
        u_min, u_max = CYCLE_DATA["u_min"], CYCLE_DATA["u_max"]

        setpoint = _setpoint_at(scenario, 0)
        core.set_target(setpoint)
        _report(core, u_min, "seed", 0)

        period = core.get_control_period() or CYCLE_DATA["HoldCycleTime"]
        ratio, fan_frac = u_min, 1.0
        next_solve = 0.0
        auger_on, auger_toggle = False, 0.0

        temps, duties, settle_from = [], [], None
        for t in range(scenario.duration_s):
            clock.t = float(t)
            new_sp = _setpoint_at(scenario, t)
            if new_sp != setpoint:
                setpoint = new_sp
                core.set_target(setpoint)
                # set_target() just reset the controller's own last-update
                # clock to t; if a scheduled solve also lands on t (e.g.
                # step_225_275's setpoint change falls on a solve boundary),
                # calling update() again this same tick would hand it dt=0.
                # Push the next solve a full period out from here instead.
                next_solve = t + period
                settle_from = None

            lid_open = _lid_open_at(scenario, t)
            temp_f = _c_to_f(plant.measured())

            if t >= next_solve:
                next_solve = t + period
                raw = core.update(temp_f)
                if isinstance(raw, dict):
                    requested = float(raw.get("cycle_ratio", 0.0))
                    fan = raw.get("fan") or {}
                    if fan.get("duty") is not None:
                        fan_frac = float(fan["duty"]) / 100.0
                else:
                    requested = float(raw)
                ratio = min(max(requested, u_min), u_max)
                if not lid_open:
                    _report(core, ratio, "controller", t, requested=requested)

            if lid_open:
                auger_on, auger_toggle, auger_frac = False, t, 0.0
                _report(core, 0.0, "lid_open", t)
            else:
                auger_on, auger_toggle, auger_frac = _auger_toggle_tick(
                    auger_on, auger_toggle, t, ratio, CYCLE_DATA["HoldCycleTime"]
                )

            plant.step(auger_on=auger_frac, fan_frac=0.0 if lid_open else fan_frac)

            temps.append(temp_f)
            # The requested ratio, not the realized on-fraction plant.step()
            # received (see _auger_toggle_tick) -- 0.0 during lid-open, when
            # the controller's request is overridden rather than applied.
            duties.append(0.0 if lid_open else ratio)
            if abs(temp_f - setpoint) <= 5.0:
                if settle_from is None:
                    settle_from = t
            else:
                settle_from = None

        temps = np.asarray(temps)
        duties = np.asarray(duties)
        sp_series = np.asarray([_setpoint_at(scenario, t) for t in range(scenario.duration_s)])
        err = temps - sp_series
        result = {
            "controller": controller,
            "scenario": scenario.name,
            "seed": seed,
            "iae": float(np.abs(err).sum()),
            "pct_within_5f": float((np.abs(err) <= 5.0).mean() * 100.0),
            "overshoot_f": float(err.max()),
            "undershoot_f": float(err.min()),
            "settle_s": (None if settle_from is None else int(settle_from)),
            "mean_duty": float(duties.mean()),
            "std_duty": float(duties.std()),
            "final_temp_f": float(temps[-1]),
        }
        status = getattr(core, "get_status", lambda: None)()
        if status is not None:
            result["status"] = json.loads(json.dumps(status, allow_nan=False, default=str))
        return result
    finally:
        time.time = real_time_time


def _job(arg):
    controller, scenario_name, seed = arg
    return run_scenario(controller, SCENARIOS[scenario_name], seed)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the GrillSim controller scenario matrix.")
    ap.add_argument("--controllers", nargs="+", default=["pid_sp", "mpc"])
    ap.add_argument("--scenarios", nargs="+", default=sorted(SCENARIOS))
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("-w", "--workers", type=int, default=None)
    args = ap.parse_args(argv)

    jobs = [(c, s, seed) for c in args.controllers for s in args.scenarios for seed in args.seeds]
    with Pool(args.workers) as pool:
        rows = pool.map(_job, jobs)
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=1, sort_keys=True)
    print(f"{len(rows)} runs -> {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test one short run before spending the full matrix**

```bash
QT_QPA_PLATFORM=offscreen uv run python -c "
from docs.superpowers.experiments.controller_matrix import run_scenario, Scenario
print(run_scenario('pid_ac', Scenario('smoke', 600, [(0, 225.0)]), 0))
"
```
Expected: a dict with a finite `iae` and a `mean_duty` between `u_min` and `u_max`. If `iae` is `nan` or `mean_duty` is exactly `u_min` for the whole run, stop and fix the harness — a broken harness makes every later number meaningless.

`_auger_toggle_tick` is a port of `ControlMode._auger_cycle_tick`
(`controller/runtime/modes/base.py:105-134`): a toggle free-running on
`auger_toggle`, keyed on elapsed time since its own last flip and never reset
when the controller re-solves. `HoldMode.on_tick` gates the controller update
on `controller.cycle_start`/`controller_interval` and only writes
`state.cycle.ratio`; the auger cycles on its own timers regardless. Two
distinct ways to get this wrong, both found the hard way against this exact
harness:

1. **Re-anchoring the window to each solve** (an earlier version of this
   harness did) makes the realized duty `min(1, ratio * HoldCycleTime /
   period)`, so any controller whose control period is shorter than
   `HoldCycleTime` saturates -- MPC solves at 5 s against a 20 s cycle, and
   every steady scenario landed on the same ~520 F terminal temperature no
   matter the set point.
2. **Sampling the toggle as a single boolean once per simulated second**
   (GrillSim's integration step) quantizes fuel delivery to whichever side of
   a transition the sample landed on, biasing realized duty above the
   requested ratio -- worst at small ratios (u_min realized ~21% high). Fixed
   by locating the transition instant exactly within the window and returning
   the fraction of the window it covers (closed-form, valid because
   `cycle_time * ratio` and `cycle_time * (1 - ratio)` are both required to be
   >= 1 s, asserted explicitly) instead of a boolean sample -- paired with
   `GrillSim.step` accepting that fraction directly
   (`fed = self.feed_rate * float(auger_on)`, unchanged for every existing
   boolean caller).

Controllers whose period equals `HoldCycleTime` are unaffected by defect 1,
but *not* by defect 2 or by the wall-clock defect below -- "period matches
HoldCycleTime" is not a general excuse to skip checking a PID variant's
numbers against a fix in this harness.

**A third, unrelated defect hit only PID variants:** `pid_sp.py`/`pid_ac.py`
compute `dt = time.time() - self.last_update` and divide by it twice. Calling
`core.update()` in this loop's tight `for t in range(...)` gives `dt` on the
order of 1e-5 s instead of the intended 20 s control period, saturating PID
output regardless of temperature error -- a saturated relay, not a PID
controller. Fixed with `_SimClock`, which replaces `time.time` for the
duration of `run_scenario` (patched and restored in a `try`/`finally`, safe
under multiprocessing workers since each runs scenarios serially) and is
advanced once per simulated second to match the loop's own step size. That
exposed a fourth, narrower defect: `core.set_target()` resets a controller's
own last-update clock, and `step_225_275`'s setpoint change at t=7200 lands
exactly on a solve boundary (7200 is a multiple of the 20 s period) -- the
scheduled solve landing on the same tick handed `pid_sp` `dt=0` and crashed.
Fixed by pushing `next_solve` a full period past a setpoint change, which
does not disturb the `dt == period` invariant on any other solve.

- [ ] **Step 3b: Pin all four defects with discriminating tests**

- Auger toggle: assert that at a fixed `ratio`, the realized auger on-fraction
  over a long window matches `ratio` for both a 20 s and a 5 s control
  period, at a ratio whose cycle duration is an exact number of ticks (e.g.
  `u_min`) *and* one that is not (e.g. 0.4237, so the fractional-remainder
  arithmetic itself is exercised, not just the toggle timing). Before
  accepting either test, run it against the defect it targets (the
  re-anchored model; boolean-per-tick sampling) and confirm it fails; a test
  that passes under both the broken and fixed model proves nothing.
- Simulated clock: assert the `dt` a real `pid_sp`/`pid_ac` instance observes
  equals `HoldCycleTime` on every solve, across a scenario with no setpoint
  change (`steady_225`) and one with a setpoint change that lands on a solve
  boundary (`step_225_275`), plus a sanity range on the raw output. Confirm
  it fails (dt ~1e-5 s) without the clock.
- `GrillSim.step`'s `float(auger_on)`: assert the same seed and the same
  True/False sequence, once passed as bools and once as the equivalent
  1.0/0.0 floats, produce identical temperature trajectories.

- [ ] **Step 4: Run the baseline matrix**

```bash
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/controller_matrix.py \
  --controllers pid_sp mpc --out docs/superpowers/experiments/_matrix_baseline.json -w 8
```
Expected: `60 runs -> ...` (2 controllers × 6 scenarios × 5 seeds). This takes minutes, not hours; if MPC is running the NLP rather than the net it will be far slower — check `status.policy` in the output.

**Regeneration hazard:** once `mpc.Controller.set_output` exists, re-running this exact command captures the 30 `mpc` rows with applied-duty feedback live; regenerating `_matrix_baseline.json` as a genuine before-arm requires disabling `set_output` first, or it silently becomes a second after-arm.

Disabling it in the parent process is not enough. `main()` runs the jobs through a `multiprocessing.Pool`, and Python 3.14 defaults to the `forkserver` start method on Linux, so the workers are fresh interpreters that never see a parent-process monkeypatch — the run would look patched and produce after-arm numbers. Patch every interpreter instead, via a `sitecustomize.py` on `PYTHONPATH` that replaces `controller.mpc.Controller.set_output` with a no-op. Verify the arm rather than trusting it, on two independent signals, because rows are Pool-distributed and one worker missing the patch would only spoil the rows it happened to draw:

1. Every non-lid `mpc` row must come back **bit-identical** to the committed baseline and must **differ** from the after-arm at ~1e-10. Matching the after-arm exactly means the patch did not reach the workers.
2. `status.applied_Q == status.last_Q` must hold on all 30 baseline `mpc` rows — with `set_output` a no-op, only `update()`'s own assignment writes `_applied_Q` — and must fail on after-arm rows.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format docs/superpowers/experiments/controller_matrix.py
jj describe --stdin <<'EOF'
test(sim): add the GrillSim controller scenario matrix and capture the baseline

Runs a controller core directly against GrillSim from (controller, scenario,
seed), so the before and after numbers for applied-output feedback are
comparable. The lid-open scenario reproduces Hold's auger pause and reports it
through set_output when the controller has that capability, which is what lets
the same harness measure code from before that capability exists.
EOF
```

---

### Task 2: Net-versus-NLP replay harness and pre-change baseline

The binding acceptance criterion for MPC is that its net policy keeps agreeing with the NLP it approximates. Nobody has measured that during a disturbance. Measure it now, before any change.

**Files:**
- Create: `docs/superpowers/experiments/net_vs_nlp_replay.py`
- Create: `docs/superpowers/experiments/_net_vs_nlp_baseline.json` (generated)

**Interfaces:**
- Produces: `replay(seed, lid_open_window) -> dict` with keys `rms_all`, `max_all`, `rms_lid`, `max_lid`, `n`, `n_lid`. Task 14 re-runs it and compares.

- [ ] **Step 1: `jj new`**

```bash
jj new -m "wip: net vs nlp replay"
```

- [ ] **Step 2: Write the harness**

Create `docs/superpowers/experiments/net_vs_nlp_replay.py`:

```python
#!/usr/bin/env python3
"""Pointwise disagreement between the MPC net policy and the NLP it approximates.

Two closed-loop runs diverge on their own and cannot settle whether the net is
still the same policy. This runs the loop ONCE under the NLP, logs every
(x_hat, u_prev, set_point_c) the policy was asked about, and replays those exact
triples through the net. The difference is the approximation error on the states
the controller actually visits -- including the lid-open interval, which no
training episode contains.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from controller.grill_sim import GrillSim  # noqa: E402
from controller.mpc import Controller  # noqa: E402
from controller.mpc_allocator import allocate  # noqa: E402
from controller.mpc_net import NetPolicy, net_path_for  # noqa: E402

OUT = "./docs/superpowers/experiments/_net_vs_nlp_baseline.json"
ARTIFACT = "./controller/mpc_policy_net.npz"


def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def replay(seed=0, duration_s=3 * 3600, lid_open_at=2 * 3600, lid_open_for=120, setpoint_f=225.0):
    core = Controller({"policy": "nlp"}, "F", {"HoldCycleTime": 20, "u_min": 0.15, "u_max": 0.9})
    assert core._net is None, "configure policy=nlp; the point is to log the NLP's answers"
    core.set_target(setpoint_f)
    plant = GrillSim(seed=seed)
    period = core.get_control_period()

    triples, q_nlp, in_lid = [], [], []
    ratio, fan_frac, next_solve, anchor = core.u_min, 1.0, 0.0, 0.0
    for t in range(duration_s):
        lid = lid_open_at <= t < lid_open_at + lid_open_for
        if t >= next_solve:
            next_solve = t + period
            # snapshot the policy inputs BEFORE update() mutates them
            raw = core.update(_c_to_f(plant.measured()))
            triples.append((np.asarray(core._x_hat).reshape(-1).copy(), float(core._policy_u_prev), float(core._set_point_c)))
            q_nlp.append(float(core._last_Q))
            in_lid.append(lid)
            ratio = min(max(float(raw["cycle_ratio"]), core.u_min), core.u_max)
            fan = raw.get("fan") or {}
            if fan.get("duty") is not None:
                fan_frac = float(fan["duty"]) / 100.0
            anchor = t
            if hasattr(core, "set_output"):
                from controller.applied_output import AppliedOutput, OutputSource

                core.set_output(
                    AppliedOutput(
                        ratio=0.0 if lid else ratio,
                        source=OutputSource.LID_OPEN if lid else OutputSource.CONTROLLER,
                        timestamp=float(t),
                    )
                )
        on = (not lid) and ((t - anchor) % 20) < 20 * ratio
        plant.step(auger_on=on, fan_frac=0.0 if lid else fan_frac)

    net = NetPolicy.load(net_path_for(ARTIFACT, bool(core.cfg["enable_fan_input"])))
    diffs = np.asarray([abs(net.firing_rate(x, u, sp) - q) for (x, u, sp), q in zip(triples, q_nlp)])
    lid_mask = np.asarray(in_lid)
    return {
        "seed": seed,
        "n": int(diffs.size),
        "n_lid": int(lid_mask.sum()),
        "rms_all": float(np.sqrt((diffs**2).mean())),
        "max_all": float(diffs.max()),
        "rms_lid": float(np.sqrt((diffs[lid_mask] ** 2).mean())) if lid_mask.any() else None,
        "max_lid": float(diffs[lid_mask].max()) if lid_mask.any() else None,
        "q_span": float(core.cfg["Q_max"] - core.cfg["Q_min"]),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure net-vs-NLP policy disagreement.")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    rows = [replay(seed=s) for s in args.seeds]
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=1, sort_keys=True)
    for r in rows:
        print(f"seed {r['seed']}: rms_all={r['rms_all']:.3f} max_all={r['max_all']:.3f} "
              f"rms_lid={r['rms_lid']} max_lid={r['max_lid']} (Q span {r['q_span']:.0f})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add the two attributes the harness reads**

The harness needs `core._x_hat` and `core._policy_u_prev` — the exact inputs handed to the policy. They do not exist yet. Add them to `controller/mpc.py::update` as pure recording, changing no behavior:

In `controller/mpc.py`, in `__init__` beside `self._last_Q = cfg["Q_min"]` (line ~146) add:

```python
        self._x_hat = None
        self._policy_u_prev = float(cfg["Q_min"])
```

and in `update()`, replace lines 291-296:

```python
        x_hat = self.estimator.update(self._last_Q, y)
        self._x_hat = x_hat
        self._policy_u_prev = float(self._last_Q)
        # 2) compute firing rate Q from the active policy (net or NLP). On any
        #    error we hold the previous move so the control loop never breaks.
        try:
            if self._net is not None:
                Q = self._net.firing_rate(x_hat, self._policy_u_prev, self._set_point_c)
```

Nothing else changes: `self._policy_u_prev` is `self._last_Q` at this point, so the firing rate is bit-identical.

- [ ] **Step 4: Verify MPC is unchanged**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/mpc/ -q`
Expected: PASS, same count as before your change. If `do-mpc` is not installed these skip — in that case say so in your report and run the NLP-dependent steps in the main checkout.

- [ ] **Step 5: Capture the baseline**

```bash
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/net_vs_nlp_replay.py
```
Expected: three lines of numbers. **Record them verbatim in your task report and in the commit message** — Task 14 compares against them and there is no other copy.

Sanity check before trusting them: `rms_all` should be small relative to `q_span`, and `n_lid` should be roughly `lid_open_for / control_period` (about 24). If `n_lid` is 0 the lid window never fired and the measurement is worthless.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format docs/superpowers/experiments/net_vs_nlp_replay.py controller/mpc.py
jj describe --stdin <<'EOF'
test(mpc): measure net-vs-NLP policy disagreement, including lid-open

The net policy is only worth having while it agrees with the NLP it
approximates, and that has never been measured on the states a disturbance
actually visits. Runs the loop once under the NLP, logs the exact policy inputs,
and replays them through the net -- pointwise, because two closed-loop runs
diverge on their own and cannot answer the question.

Recording _x_hat and _policy_u_prev on the controller is what makes the inputs
observable; the firing rate is bit-identical.
EOF
```

---

### Task 3: `controller/applied_output.py`

**Files:**
- Create: `controller/applied_output.py`
- Test: `tests/unit/controller/test_applied_output.py`

**Interfaces:**
- Produces: `OutputSource` (enum), `AppliedOutput` (frozen dataclass with `.ratio`, `.source`, `.timestamp`, `.requested`, and the derived `.controller_commanded`), `classify_output_source(lid_open, manual_override_active, fan_assist_active)`, `seed_output(ratio, timestamp, *, lid_open, manual_override_active, fan_assist_active, auger_output)`. Every later task in both plans consumes these names.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: applied output"
```

Create `tests/unit/controller/test_applied_output.py`:

```python
import pytest

from controller.applied_output import (
    AppliedOutput,
    OutputSource,
    classify_output_source,
    seed_output,
)


@pytest.mark.parametrize(
    "lid,manual,fan,expected",
    [
        (False, False, False, OutputSource.CONTROLLER),
        (False, False, True, OutputSource.FAN_ASSIST),
        (True, False, False, OutputSource.LID_OPEN),
        (True, False, True, OutputSource.LID_OPEN),
        (False, True, False, OutputSource.MANUAL_OVERRIDE),
        (True, True, False, OutputSource.MANUAL_OVERRIDE),
        (True, True, True, OutputSource.MANUAL_OVERRIDE),
    ],
)
def test_precedence(lid, manual, fan, expected):
    assert classify_output_source(lid, manual, fan) is expected


def test_controller_commanded_is_derived_from_source():
    assert AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0).controller_commanded is True
    for source in (OutputSource.LID_OPEN, OutputSource.MANUAL_OVERRIDE, OutputSource.FAN_ASSIST, OutputSource.SEED):
        assert AppliedOutput(0.4, source, 1.0).controller_commanded is False


def test_applied_output_is_frozen():
    applied = AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0)
    with pytest.raises(Exception):
        applied.ratio = 0.9


def test_requested_defaults_to_none():
    assert AppliedOutput(0.9, OutputSource.CONTROLLER, 1.0).requested is None
    assert AppliedOutput(0.9, OutputSource.CONTROLLER, 1.0, requested=1.4).requested == 1.4


def test_seed_output_is_seed_when_nothing_else_applies():
    applied = seed_output(
        0.15, 100.0, lid_open=False, manual_override_active=False, fan_assist_active=False, auger_output=True
    )
    assert applied.source is OutputSource.SEED
    assert applied.ratio == 0.15
    assert applied.timestamp == 100.0
    assert applied.requested is None


def test_seed_output_keeps_a_real_reason_when_one_exists():
    applied = seed_output(
        0.15, 100.0, lid_open=True, manual_override_active=False, fan_assist_active=False, auger_output=False
    )
    assert applied.source is OutputSource.LID_OPEN


def test_seed_output_reports_zero_when_the_auger_is_off():
    applied = seed_output(
        0.5, 100.0, lid_open=False, manual_override_active=False, fan_assist_active=False, auger_output=False
    )
    assert applied.ratio == 0.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_applied_output.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'controller.applied_output'`.

- [ ] **Step 3: Write the module**

Create `controller/applied_output.py`:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire Applied Controller Output
*****************************************

 Description: The duty that actually reached the auger, and why.

 A controller's request and the grill's behavior are not the same thing: the
 mode clamps the request to [u_min, u_max], pins the auger off for a lid-open
 pause, and hands the auger to a human on a manual override. A controller that
 models the plant has to follow what the grill did, not what it asked for.

 Deliberately a leaf module -- standard library only, importing from neither
 controller/*.py nor controller/runtime/** -- so the controller layer and the
 runtime layer can both use it without an inversion.

*****************************************
"""

from dataclasses import dataclass
from enum import Enum


class OutputSource(Enum):
    """Why the auger is running at the duty it is running at."""

    CONTROLLER = "controller"
    LID_OPEN = "lid_open"
    MANUAL_OVERRIDE = "manual_override"
    FAN_ASSIST = "fan_assist"
    SEED = "seed"


@dataclass(frozen=True)
class AppliedOutput:
    """One report of the duty that reached the auger.

    `requested` carries the controller's pre-clamp value when there was one, so
    a saturated interval is distinguishable from an unsaturated one: an auger
    held at u_max while the controller asked for 1.4 describes the clamp, not
    the process gain.
    """

    ratio: float
    source: OutputSource
    timestamp: float
    requested: float | None = None

    @property
    def controller_commanded(self):
        """Whether the controller's own request is what reached the auger.

        Derived rather than stored so it cannot drift out of agreement with the
        reason, and so no call site can assert "not the controller's" without
        saying why.
        """
        return self.source is OutputSource.CONTROLLER


def classify_output_source(lid_open, manual_override_active, fan_assist_active):
    """The reason the auger is at its current duty.

    A human toggling the auger during a lid-open pause reads as manual, not as
    lid-open: the more specific cause wins.
    """
    if manual_override_active:
        return OutputSource.MANUAL_OVERRIDE
    if lid_open:
        return OutputSource.LID_OPEN
    if fan_assist_active:
        return OutputSource.FAN_ASSIST
    return OutputSource.CONTROLLER


def seed_output(ratio, timestamp, *, lid_open, manual_override_active, fan_assist_active, auger_output):
    """Actuator state that no command produced -- at setup, or after a rebuild.

    `auger_output` is the platform's current auger state; an auger that is off
    is applying zero duty whatever the cycle ratio says.
    """
    source = classify_output_source(lid_open, manual_override_active, fan_assist_active)
    if source is OutputSource.CONTROLLER:
        source = OutputSource.SEED
    return AppliedOutput(
        ratio=float(ratio) if auger_output else 0.0,
        source=source,
        timestamp=float(timestamp),
    )
```

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_applied_output.py -q`
Expected: PASS, 13 tests.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/applied_output.py tests/unit/controller/test_applied_output.py
jj describe --stdin <<'EOF'
feat(controller): add AppliedOutput, the duty that reached the auger

A controller's request and the grill's behavior diverge whenever the mode clamps
it, a lid-open pause pins the auger off, or a human takes the auger. Controllers
that model the plant need the second one.

controller_commanded is derived from the reason rather than stored, so a call
site cannot claim "not the controller's" without saying why.
EOF
```

---

### Task 4: Four capabilities on `ControllerBase`

**Files:**
- Modify: `controller/base.py`
- Test: `tests/unit/controller/test_controller_capabilities.py`

**Interfaces:**
- Consumes: `controller.applied_output.AppliedOutput` (Task 3) — for the docstring and the test, not an import in `base.py`.
- Produces: `ControllerBase.set_output(applied) -> None`, `.get_status() -> dict | None`, `.get_model_snapshot() -> dict | None`, `.restore_model(snapshot) -> bool`. Tasks 5, 6, 13 and all of Plan B rely on these exact names and return contracts.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: controller capabilities"
```

Create `tests/unit/controller/test_controller_capabilities.py`:

```python
"""Every controller answers the four capability methods; the defaults are inert.

A controller that does not model the plant must be completely unaffected by
applied-output feedback, diagnostics, and model persistence existing.
"""

import importlib

import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerBase

# Controllers with no optional dependency, so this runs on every install.
PLAIN_CONTROLLERS = ["pid", "pid_clamping", "pid_clamping_percent_pb", "pid_ac", "pid_parallel", "pid_sp"]

CYCLE_DATA = {"HoldCycleTime": 20}


def test_base_defaults_are_inert():
    core = ControllerBase({}, "F", dict(CYCLE_DATA))
    assert core.set_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0)) is None
    assert core.get_status() is None
    assert core.get_model_snapshot() is None
    assert core.restore_model({"revision": 1}) is False


def test_set_output_does_not_change_a_plain_controller_s_output():
    core = ControllerBase({}, "F", dict(CYCLE_DATA))
    before = core.update(200.0)
    core.set_output(AppliedOutput(0.9, OutputSource.LID_OPEN, 1.0))
    assert core.update(200.0) == before


@pytest.mark.parametrize("name", PLAIN_CONTROLLERS)
def test_every_shipped_controller_answers_all_four(name):
    mod = importlib.import_module(f"controller.{name}")
    core = mod.Controller({}, "F", dict(CYCLE_DATA))
    for method in ("set_output", "get_status", "get_model_snapshot", "restore_model"):
        assert callable(getattr(core, method)), f"{name} is missing {method}"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_controller_capabilities.py -q`
Expected: FAIL — `AttributeError: 'ControllerBase' object has no attribute 'set_output'`.

- [ ] **Step 3: Add the four methods**

In `controller/base.py`, after `wants_async` (the last method on `ControllerBase`), add:

```python
    def set_output(self, applied):
        """Report the duty that actually reached the auger.

        `applied` is a controller.applied_output.AppliedOutput. Controllers that
        model the plant use it so their model follows the grill rather than the
        request. A report whose `controller_commanded` is False is NOT a report
        to discard -- the grill really did run at that duty, so it belongs in
        the command history. What it suppresses is *identification* across the
        interval, so no estimator computes a temperature slope over time the
        controller did not drive.
        """

    def get_status(self):
        """JSON-safe diagnostics for the MQTT payload.

        Return None to publish the controller's __dict__, which is the legacy
        behavior and correct for controllers whose attributes are all scalars.
        """
        return None

    def get_model_snapshot(self):
        """A JSON-encodable record of learned plant parameters, or None.

        Must carry an integer `revision` that increases whenever the model
        changes; the store uses it to skip writes that would learn nothing.
        """
        return None

    def restore_model(self, snapshot):
        """Adopt a persisted snapshot. True when it was adopted.

        The store validates that a snapshot is a bounded, JSON-safe record; the
        controller validates that its numbers describe a possible grill.
        """
        return False
```

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/controller/test_controller_capabilities.py tests/unit/controller/test_controller_construct_smoke.py -q`
Expected: PASS. The smoke test pins the controller construction surface; if it fails, you added something it forbids.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/base.py tests/unit/controller/test_controller_capabilities.py
jj describe --stdin <<'EOF'
feat(controller): add four default-inert capabilities to ControllerBase

set_output, get_status, get_model_snapshot and restore_model join the existing
get_control_period/commands_fan/wants_async overridable-method idiom -- plain
methods, no function_list, no string reflection.

Every default is inert, so a controller that does not model the plant is
unaffected by any of this existing.
EOF
```

---

### Task 5: `SyncControllerRunner` forwarding and `controller_state()`

**Files:**
- Modify: `controller/runtime/runner.py`
- Test: `tests/unit/runtime/test_sync_runner.py`

**Interfaces:**
- Consumes: `ControllerBase.set_output/get_status/get_model_snapshot/restore_model` (Task 4).
- Produces: `ControllerRunner.set_output(applied)`, `.get_model_snapshot()`, `.restore_model(snapshot)`, `.controller_state()` — now all on the ABC. Tasks 6, 8, 9, 11 call these through the runner.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: sync runner forwarding"
```

Append to `tests/unit/runtime/test_sync_runner.py`:

```python
from controller.applied_output import AppliedOutput, OutputSource
from controller.runtime.runner import SyncControllerRunner


class _RecordingCore:
    def __init__(self, status=None):
        self.applied = []
        self._status = status
        self.restored = None
        self.snapshot = {"revision": 3, "K": 700.0}

    def update(self, temp):
        return 0.5

    def set_target(self, sp):
        pass

    def get_control_period(self):
        return None

    def commands_fan(self):
        return False

    def wants_async(self):
        return False

    def set_output(self, applied):
        self.applied.append(applied)

    def get_status(self):
        return self._status

    def get_model_snapshot(self):
        return self.snapshot

    def restore_model(self, snapshot):
        self.restored = snapshot
        return True


def test_sync_runner_forwards_set_output():
    core = _RecordingCore()
    runner = SyncControllerRunner(core)
    applied = AppliedOutput(0.4, OutputSource.CONTROLLER, 12.0, requested=0.4)
    runner.set_output(applied)
    assert core.applied == [applied]


def test_sync_runner_forwards_snapshot_and_restore():
    core = _RecordingCore()
    runner = SyncControllerRunner(core)
    assert runner.get_model_snapshot() == {"revision": 3, "K": 700.0}
    assert runner.restore_model({"revision": 9}) is True
    assert core.restored == {"revision": 9}


def test_controller_state_prefers_get_status():
    runner = SyncControllerRunner(_RecordingCore(status={"K": 700.0}))
    assert runner.controller_state() == {"K": 700.0}


def test_controller_state_falls_back_to_dunder_dict():
    core = _RecordingCore(status=None)
    core.p = 0.25
    state = SyncControllerRunner(core).controller_state()
    assert state["p"] == 0.25
    # a copy, not the live __dict__
    state["p"] = 99
    assert core.p == 0.25
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/test_sync_runner.py -q`
Expected: FAIL — `AttributeError: 'SyncControllerRunner' object has no attribute 'set_output'`.

- [ ] **Step 3: Add the ABC declarations**

In `controller/runtime/runner.py`, inside `class ControllerRunner(ABC)`, after `def wants_async(self): ...`, add:

```python
    @abstractmethod
    def set_output(self, applied): ...
    @abstractmethod
    def get_model_snapshot(self): ...
    @abstractmethod
    def restore_model(self, snapshot): ...
    @abstractmethod
    def controller_state(self): ...
```

- [ ] **Step 4: Implement them on `SyncControllerRunner`**

Replace `SyncControllerRunner.controller_state` (currently `return dict(self._core.__dict__)`) with:

```python
    def set_output(self, applied):
        self._core.set_output(applied)

    def get_model_snapshot(self):
        return self._core.get_model_snapshot()

    def restore_model(self, snapshot):
        return self._core.restore_model(snapshot)

    def controller_state(self):
        status = self._core.get_status()
        if status is None:
            return dict(self._core.__dict__)
        return status
```

- [ ] **Step 5: Run the runtime tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/ -q`
Expected: PASS for the sync tests. `test_threaded_runner.py` may now fail on the ABC — that is Task 6. If it does, note it and continue; do not weaken the ABC to make it pass.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/runner.py tests/unit/runtime/test_sync_runner.py
jj describe --stdin <<'EOF'
feat(runner): forward the controller capabilities through SyncControllerRunner

controller_state() prefers get_status() and keeps dict(__dict__) only as the
fallback, so a controller holding non-serializable objects can publish a payload
that survives the MQTT encoder.
EOF
```

---

### Task 6: `ThreadedControllerRunner` queueing and ordered replay

The threaded runner mutates its core only on the worker thread. Applied-output reports, snapshot reads and restores all arrive from the control thread, so each has to cross the lock without racing an in-flight `update()`.

**Files:**
- Modify: `controller/runtime/runner.py`
- Test: `tests/unit/runtime/test_threaded_runner.py`

**Interfaces:**
- Consumes: the ABC from Task 5.
- Produces: threaded semantics that Task 11 depends on — `restore_model()` **queues** and returns True when the snapshot was accepted for restore, rather than reporting adoption; `get_model_snapshot()` returns the snapshot the worker computed after its last `update()`, not a live read.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: threaded runner replay"
```

Append to `tests/unit/runtime/test_threaded_runner.py`:

```python
import threading
import time

from controller.applied_output import AppliedOutput, OutputSource
from controller.runtime.runner import ThreadedControllerRunner


class _OrderRecordingCore:
    """Records the interleaving of set_output and update calls."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.snapshot = {"revision": 1}
        self.restored = []

    def update(self, temp):
        with self.lock:
            self.calls.append(("update", temp))
        return 0.5

    def set_output(self, applied):
        with self.lock:
            self.calls.append(("set_output", applied.timestamp))

    def set_target(self, sp):
        pass

    def get_control_period(self):
        return 0.01

    def commands_fan(self):
        return False

    def wants_async(self):
        return True

    def get_status(self):
        return None

    def get_model_snapshot(self):
        return dict(self.snapshot)

    def restore_model(self, snapshot):
        with self.lock:
            self.restored.append(snapshot)
        return True


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_applied_outputs_replay_in_timestamp_order_before_update():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        # queue out of order; the worker must sort them
        runner.set_output(AppliedOutput(0.2, OutputSource.CONTROLLER, 20.0))
        runner.set_output(AppliedOutput(0.1, OutputSource.CONTROLLER, 10.0))
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        with core.lock:
            calls = list(core.calls)
        first_update = next(i for i, c in enumerate(calls) if c[0] == "update")
        reports = [c for c in calls[:first_update] if c[0] == "set_output"]
        assert reports == [("set_output", 10.0), ("set_output", 20.0)]
    finally:
        runner.stop()


def test_restore_model_is_applied_on_the_worker_thread():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.restore_model({"revision": 7}) is True
        runner.submit(212.0)
        assert _wait_for(lambda: core.restored == [{"revision": 7}])
    finally:
        runner.stop()


def test_restore_model_rejects_none_without_touching_the_core():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.restore_model(None) is False
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        assert core.restored == []
    finally:
        runner.stop()


def test_get_model_snapshot_reads_the_worker_s_snapshot():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        core.snapshot = {"revision": 4}
        runner.submit(212.0)
        assert _wait_for(lambda: runner.get_model_snapshot() == {"revision": 4})
    finally:
        runner.stop()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/test_threaded_runner.py -q`
Expected: FAIL — `TypeError: Can't instantiate abstract class ThreadedControllerRunner` (the ABC from Task 5).

- [ ] **Step 3: Add the queues to `__init__`**

In `ThreadedControllerRunner.__init__`, beside `self._pending_core = None`, add:

```python
        self._pending_outputs = collections.deque(maxlen=_MAX_PENDING_OUTPUTS)
        self._pending_dropped = 0
        self._pending_restore = None
        self._model_snapshot = core.get_model_snapshot()
```

and at module level:

```python
# Hold reports once per work-loop tick, and that loop runs at roughly 20 Hz
# (`ControlMode.run` sleeps 0.05 s), while the worker drains only once per
# controller solve. This ceiling spans a stalled solve without letting the
# backlog grow without bound; the oldest reports are the ones to lose, since
# a consumer identifying a process model cares about recent duty.
_MAX_PENDING_OUTPUTS = 2048
```

A bounded deque is required, not a convenience. The producer is Hold's tick loop at ~20 Hz; the consumer runs once per control period. In steady state that is about 100 entries for MPC's 5 s period, but a slow or hung NLP solve makes the producer unbounded against a stopped consumer, and the replay loop below would then feed the entire backlog into the controller in a single burst.

`maxlen` alone would drop silently, so count the drops:

```python
        if len(self._pending_outputs) == self._pending_outputs.maxlen:
            self._pending_dropped += 1
```

immediately before the `append` in Step 6, and surface `_pending_dropped` in `controller_state()`. A nonzero value means the worker could not keep up, which is a fact worth seeing rather than inferring.

Pin both halves with a test in `tests/unit/runtime/test_threaded_runner.py`:

```python
def test_a_stalled_worker_bounds_the_backlog_and_counts_the_drops():
    runner, core = _threaded_runner_with_blocked_worker()
    for i in range(_MAX_PENDING_OUTPUTS + 50):
        runner.set_output(_output(ratio=0.3, timestamp=float(i)))

    assert len(runner._pending_outputs) == _MAX_PENDING_OUTPUTS
    assert runner._pending_dropped == 50
    assert runner.controller_state()["pending_dropped"] == 50

    # the survivors are the newest, and the oldest are what went
    oldest = min(a.timestamp for a in runner._pending_outputs)
    assert oldest == 50.0
```

The `oldest == 50.0` assertion is the load-bearing one: it proves the deque discards from the correct end. A `maxlen` deque that dropped the newest would keep the backlog bounded and still starve the controller of current duty, and every other assertion here would pass.

**Drain by copy-and-clear, never by rebinding.** In `_loop`, take `list(self._pending_outputs)` and then `self._pending_outputs.clear()`. Rebinding to `[]` — which is what the pre-existing code does — replaces the bounded deque with an unbounded list, so the ceiling survives exactly one drain and every guarantee above quietly evaporates. The drop-counting test would still pass, because it never drains. Add a second test that drains first and *then* overfills, so the bound is proven to outlive a drain:

```python
def test_the_backlog_stays_bounded_after_a_drain():
    runner, core = _threaded_runner_with_blocked_worker()
    for i in range(10):
        runner.set_output(_output(ratio=0.3, timestamp=float(i)))
    _drain_once(runner)

    for i in range(_MAX_PENDING_OUTPUTS + 25):
        runner.set_output(_output(ratio=0.3, timestamp=float(100 + i)))
    assert len(runner._pending_outputs) == _MAX_PENDING_OUTPUTS
    assert isinstance(runner._pending_outputs, collections.deque)
```

**While this file is open, fix the sync/threaded asymmetry Task 5 left behind.** `ThreadedControllerRunner.controller_state()` already returns a copy; `SyncControllerRunner.controller_state()`'s `get_status()` branch returns the controller's dict as-is. `HoldMode._on_auger_on` mutates what it receives (`controller_data["cycle_ratio"] = ...`) before publishing, so once a controller has a real `get_status()` that mutation writes into controller state. The legacy `dict(self._core.__dict__)` path was a fresh copy every time, so this would be a silent weakening of a guarantee callers already had.

Make the sync branch `return dict(status)`, and say in a docstring that the returned mapping belongs to the caller. A shallow copy is enough for the known consumer. Pin it: assert that mutating the dict `controller_state()` returned leaves a subsequent call unchanged — with a core whose `get_status()` returns a cached dict, since one that builds a fresh dict each call cannot fail the test.

- [ ] **Step 4: Drain and replay in `_loop`**

Replace the body of `_loop` with:

```python
    def _loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                temp = self._temp
                target = self._pending_target
                self._pending_target = _UNSET
                new_core = self._pending_core
                self._pending_core = None
                pending_outputs = list(self._pending_outputs)
                self._pending_outputs.clear()
                restore = self._pending_restore
                self._pending_restore = None
            if new_core is not None:
                self._core = new_core
            if restore is not None:
                self._core.restore_model(restore)
            if target is not _UNSET:
                self._core.set_target(target)
            # A command must reach the core before the temperature that command
            # caused, and in the order the auger saw it.
            for applied in sorted(pending_outputs, key=lambda a: a.timestamp):
                self._core.set_output(applied)
            if temp is not None:
                raw = self._core.update(temp)
                ratio, fan = normalize_controller_output(raw)
                snap = dict(self._core.__dict__)
                status = self._core.get_status()
                model = self._core.get_model_snapshot()
                with self._lock:
                    self._output = NormalizedOutput(cycle_ratio=ratio, fan=fan)
                    self._state_snapshot = status if status is not None else snap
                    self._model_snapshot = model
            # Interruptible sleep; wait(None/0) would block forever, so floor it.
            self._stop_event.wait(self._control_period or 1.0)
```

Note the ordering: core swap, then restore, then target, then applied outputs, then `update()`. A restore lands on the rebuilt core, and the seed report that follows a reconfigure lands after the restore.

- [ ] **Step 5: Add the three public methods**

Beside `controller_state`, add:

```python
    def set_output(self, applied):
        with self._lock:
            self._pending_outputs.append(applied)

    def get_model_snapshot(self):
        with self._lock:
            return self._model_snapshot

    def restore_model(self, snapshot):
        """Queue a snapshot for the worker to adopt.

        True means accepted for restore, not adopted: the core is mutated only
        on the worker thread, so the adoption result is not knowable here. It
        surfaces in get_status().
        """
        if snapshot is None:
            return False
        with self._lock:
            self._pending_restore = snapshot
        return True
```

`controller_state()` needs no change — `_state_snapshot` now already holds `get_status()` output when there is any.

- [ ] **Step 6: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/ -q`
Expected: PASS, everything.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/runner.py tests/unit/runtime/test_threaded_runner.py
jj describe --stdin <<'EOF'
feat(runner): queue applied outputs and replay them before update()

The threaded runner mutates its core only on the worker thread, so reports,
snapshot reads and restores all cross the lock. Reports replay in timestamp
order ahead of update(), so the core always hears about a command before it
hears the temperature that command caused.

restore_model() returns "accepted for restore" rather than "adopted", because
adoption happens on the worker; the result surfaces in get_status().
EOF
```

---

### Task 7: `_on_manual_output` hook on `ControlMode`

**Files:**
- Modify: `controller/runtime/modes/base.py`
- Test: `tests/unit/runtime/test_control_mode_base.py`

**Interfaces:**
- Produces: `ControlMode._on_manual_output(name, output)`, a no-op called once per handled manual change with `control["manual"]["change"]` and `control["manual"]["output"]`. Task 9 overrides it in `HoldMode`.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: manual output hook"
```

Append to `tests/unit/runtime/test_control_mode_base.py` (follow the existing fixtures in that file for building a mode; the assertions below are what matters):

```python
def test_on_manual_output_is_called_with_the_change_and_output(hold_mode_factory):
    """The hook fires while control['manual'] still names the actuator."""
    mode = hold_mode_factory()
    seen = []
    mode._on_manual_output = lambda name, output: seen.append((name, output))
    control = mode.control
    control["manual"]["change"] = "auger"
    control["manual"]["output"] = True
    mode.settings["safety"]["allow_manual_changes"] = True

    mode._apply_manual_overrides(control, now=100.0, current_output_status={"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100})

    assert seen == [("auger", True)]
    # and the reset still happened afterwards
    assert control["manual"]["change"] is False


def test_on_manual_output_is_not_called_when_no_change_is_pending(hold_mode_factory):
    mode = hold_mode_factory()
    seen = []
    mode._on_manual_output = lambda name, output: seen.append((name, output))
    control = mode.control
    control["manual"]["change"] = False

    mode._apply_manual_overrides(control, now=100.0, current_output_status={"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100})

    assert seen == []


def test_on_manual_output_default_is_a_no_op(hold_mode_factory):
    assert hold_mode_factory()._on_manual_output("auger", True) is None
```

If `hold_mode_factory` does not exist in that file, build the mode the way the file's existing tests do and keep the three assertions.

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/test_control_mode_base.py -q`
Expected: FAIL — the hook is never called, so `seen == []`.

- [ ] **Step 3: Add the no-op hook**

In `controller/runtime/modes/base.py`, beside `_on_auger_on` (line ~102):

```python
    def _on_manual_output(self, name, output):
        """A human just drove an actuator directly. `name` is the actuator."""
```

- [ ] **Step 4: Call it from `_apply_manual_overrides`**

In `_apply_manual_overrides`, immediately after the `"pwm"` block and **before** the `control["manual"]["change"] = False` reset:

```python
                # Fires while control['manual'] still names the actuator, and at
                # override START -- a mode reporting duty to a controller needs
                # the whole override window covered, not just its expiry.
                self._on_manual_output(control["manual"]["change"], control["manual"]["output"])
```

- [ ] **Step 5: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/ tests/characterization/ -q`
Expected: PASS. The characterization suite proves the hook changed no mode behavior.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/modes/base.py tests/unit/runtime/test_control_mode_base.py
jj describe --stdin <<'EOF'
feat(modes): add the _on_manual_output hook to ControlMode

Same shape as _on_auger_on: a no-op on the base, called once per handled manual
change while control['manual'] still names the actuator. Fires at override
start, so a mode that reports duty to its controller covers the whole override
window rather than leaving a hole.
EOF
```

---

### Task 8: `FakeControllerRunner` forwards, Hold per-tick report, setup seed

**Files:**
- Modify: `tests/fakes/runner.py`
- Modify: `controller/runtime/modes/hold.py`
- Test: `tests/unit/runtime/test_hold_applied_output.py` (create)

**Interfaces:**
- Consumes: `AppliedOutput`, `classify_output_source`, `seed_output` (Task 3); `runner.set_output` (Tasks 5, 6).
- Produces: `FakeControllerRunner.applied` — the list of `AppliedOutput` reports, used by Task 9 and Task 11 tests.

- [ ] **Step 1: `jj new`, then grow the fake**

```bash
jj new -m "wip: hold per-tick applied output"
```

In `tests/fakes/runner.py`, add to `__init__`:

```python
        self.applied = []
        self.restored = []
        self.snapshot = None
```

and add the three forwards beside `stop`:

```python
    def set_output(self, applied):
        self.applied.append(applied)

    def get_model_snapshot(self):
        return self.snapshot

    def restore_model(self, snapshot):
        self.restored.append(snapshot)
        return snapshot is not None

    def controller_state(self):
        return {"fake": True}
```

Without these, every existing Hold golden test raises `AttributeError` the moment Hold starts reporting.

- [ ] **Step 2: Write the shared fixture**

Tasks 8, 9 and 11 all drive `HoldMode` directly — calling `setup()` and `on_tick()` — rather than running a whole work cycle. `tests/characterization/harness.py::run_mode` cannot do that: it runs the loop and hands back captured effects. So build the mode by hand.

Create `tests/unit/runtime/conftest.py` (or extend the existing one):

```python
import pytest

import controller.runtime.runner
from controller.runtime.modes.hold import HoldMode
from controller.runtime.runner import NormalizedOutput
from controller.runtime.state import WorkCycleState
from tests.characterization.harness import make_ctx
from tests.characterization.test_modes_golden import base_control, base_pellet_db, base_settings
from tests.fakes.probes import FakeProbes


def _output(ratio):
    return NormalizedOutput(cycle_ratio=ratio, fan=None)


def _off():
    return {"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100}


@pytest.fixture
def hold_cycle(monkeypatch):
    """A HoldMode wired to a FakeControllerRunner, driven tick by tick."""

    def build(runner, *, cycle_data_extra=None, model_store=None, controller="pid_sp"):
        settings = base_settings()
        settings["controller"]["selected"] = controller
        settings["cycle_data"].update(cycle_data_extra or {})
        control_data = base_control(mode="Hold")
        control_data["primary_setpoint"] = 225
        ctx, _grill, _notifier = make_ctx(
            settings, control_data, base_pellet_db(), FakeProbes().script([225] * 200)
        )
        monkeypatch.setattr(controller_runtime_runner, "build_runner", lambda *a, **k: (runner, "Active"))
        mode = HoldMode(ctx, WorkCycleState())
        mode.settings = settings
        mode.control = control_data
        mode._model_store = model_store
        return mode

    import controller.runtime.runner as controller_runtime_runner  # noqa: E402

    return build
```

Import `controller.runtime.runner` at module top rather than inside the closure; the inline import above is a reminder of *what* to patch, not the shape to ship. If `base_settings`/`base_control`/`base_pellet_db` are not importable from `test_modes_golden`, move them into `tests/characterization/harness.py` and import from there — do not duplicate them.

`_model_store` does not exist on `HoldMode` until Task 11; passing `None` is harmless until then.

Every test module in Tasks 8, 9 and 11 uses `_output` and `_off`, so import them explicitly — `from tests.unit.runtime.conftest import _off, _output`. `tests/unit/runtime/` is a package, so this resolves; pytest does not inject plain conftest functions into test modules the way it injects fixtures.

- [ ] **Step 3: Write the failing test**

Create `tests/unit/runtime/test_hold_applied_output.py`:

```python
"""Hold reports the duty that actually reached the auger, at every site where it
diverges from the controller's request."""

from controller.applied_output import OutputSource
from tests.fakes.runner import FakeControllerRunner


def test_setup_seeds_the_initial_ratio(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner)
    hold.setup()
    assert [a.source for a in runner.applied] == [OutputSource.SEED]
    assert runner.applied[0].ratio == hold.settings["cycle_data"]["u_min"]


def test_per_tick_reports_the_clamped_ratio_and_the_raw_request(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(1.4)])
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    (applied,) = runner.applied
    assert applied.source is OutputSource.CONTROLLER
    assert applied.ratio == hold.settings["cycle_data"]["u_max"]
    assert applied.requested == 1.4
    assert applied.timestamp == 100.0
    assert applied.controller_commanded is True


def test_per_tick_reports_fan_assist_when_the_auger_is_pinned_at_u_min(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.01)])
    hold = hold_cycle(runner, cycle_data_extra={"FanPidEnabled": True})
    hold.setup()
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    (applied,) = runner.applied
    assert applied.source is OutputSource.FAN_ASSIST
    assert applied.ratio == hold.settings["cycle_data"]["u_min"]
    assert applied.controller_commanded is False


def test_per_tick_is_suppressed_while_a_manual_override_is_live(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.state.manual_override["auger"] = 200.0
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert runner.applied == []
```

- [ ] **Step 4: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/test_hold_applied_output.py -q`
Expected: FAIL — `runner.applied` is empty.

- [ ] **Step 5: Report at setup**

In `hold.py::setup`, after `self.state.controller.cycle_start = self.ctx.clock.now()` (the last line):

```python
        if self._runner is not None:
            self._runner.set_output(
                seed_output(
                    self.state.cycle.ratio,
                    self.state.controller.cycle_start,
                    lid_open=False,
                    manual_override_active=False,
                    fan_assist_active=False,
                    auger_output=True,
                )
            )
```

`auger_output=True` because setup called `self.grill.auger_on()` a few lines above; nothing has turned it off yet.

- [ ] **Step 6: Report per tick**

In `hold.py::on_tick`, immediately after the `u_max` clamp (`self.state.cycle.ratio = min(...)`, line ~162), inside the same `if (now - self.state.controller.cycle_start) > controller_interval:` block:

```python
            # A live manual override already reported the duty a human commanded
            # (_on_manual_output); the cycle ratio computed here is not what the
            # auger is doing.
            if self.state.manual_override["auger"] <= now:
                self._runner.set_output(
                    AppliedOutput(
                        ratio=self.state.cycle.ratio,
                        source=classify_output_source(
                            lid_open=self.state.lid.open_detected,
                            manual_override_active=False,
                            fan_assist_active=self.state.fan.assist,
                        ),
                        timestamp=now,
                        requested=self.state.controller.output,
                    )
                )
```

Add the import at the top of `hold.py`:

```python
from controller.applied_output import AppliedOutput, OutputSource, classify_output_source, seed_output
```

(`OutputSource` is used in Task 9; import it now so that task touches only the report sites.)

- [ ] **Step 7: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/test_hold_applied_output.py tests/characterization/ -q`
Expected: PASS. The characterization suite proves no Hold behavior changed — only new reports were added.

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/modes/hold.py tests/fakes/runner.py tests/unit/runtime/test_hold_applied_output.py
jj describe --stdin <<'EOF'
feat(hold): report the applied auger duty to the controller each tick

Reports the ratio AFTER the u_min/u_max clamps with the controller's raw request
alongside it, so a saturated interval is distinguishable from an unsaturated
one. setup() seeds the initial ratio, because a model with no history for the
duty already running would fill the gap by guessing.

Suppressed while a manual override is live: the cycle ratio computed here is not
what the auger is doing.
EOF
```

---

### Task 9: Hold lid-open and manual-override reports

**Files:**
- Modify: `controller/runtime/modes/hold.py`
- Test: `tests/unit/runtime/test_hold_applied_output.py`

**Interfaces:**
- Consumes: `_on_manual_output` (Task 7), `AppliedOutput`/`OutputSource` (Task 3), the report sites from Task 8.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: hold lid-open reports"
```

Append to `tests/unit/runtime/test_hold_applied_output.py`:

```python
def test_lid_open_detection_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    runner.applied.clear()
    # far enough below setpoint to trip LidOpenThreshold
    hold.on_tick(now=100.0, ptemp=100.0, current_output_status=_off())
    lid_reports = [a for a in runner.applied if a.source is OutputSource.LID_OPEN]
    assert lid_reports and lid_reports[0].ratio == 0.0
    assert lid_reports[0].timestamp == 100.0


def test_lid_open_toggle_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999).script([_output(0.5)])
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    runner.applied.clear()
    hold.on_tick(now=100.0, ptemp=225.0, current_output_status=_off())
    lid_reports = [a for a in runner.applied if a.source is OutputSource.LID_OPEN]
    assert lid_reports and lid_reports[0].ratio == 0.0


def test_manual_auger_on_reports_full_duty(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold._on_manual_output("auger", True)
    (applied,) = runner.applied
    assert applied.source is OutputSource.MANUAL_OVERRIDE
    assert applied.ratio == 1.0
    assert applied.controller_commanded is False


def test_manual_auger_off_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold._on_manual_output("auger", False)
    assert runner.applied[0].ratio == 0.0


def test_manual_changes_to_other_actuators_report_nothing(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    for name in ("fan", "igniter", "power", "pwm"):
        hold._on_manual_output(name, True)
    assert runner.applied == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/test_hold_applied_output.py -q`
Expected: FAIL on the four new tests.

- [ ] **Step 3: Report at both lid-open sites**

In `hold.py::on_tick`, after `grill_platform.auger_off()` in the **detection** branch (line ~183, followed by `grill_platform.fan_off()`):

```python
            self._runner.set_output(AppliedOutput(ratio=0.0, source=OutputSource.LID_OPEN, timestamp=now))
```

And after `grill_platform.auger_off()` in the **toggle** branch (line ~200):

```python
                self._runner.set_output(AppliedOutput(ratio=0.0, source=OutputSource.LID_OPEN, timestamp=now))
```

Both, not just the toggle: `_auger_cycle_tick` in `modes/base.py` has no lid guard, so the auger genuinely resumes cycling at `u_min` during a pause and both transitions are equally real.

- [ ] **Step 4: Override `_on_manual_output` in `HoldMode`**

Beside `_on_auger_on` in `hold.py`:

```python
    def _on_manual_output(self, name, output):
        if name != "auger" or self._runner is None:
            return
        self._runner.set_output(
            AppliedOutput(
                ratio=1.0 if output else 0.0,
                source=OutputSource.MANUAL_OVERRIDE,
                timestamp=self.ctx.clock.now(),
            )
        )
```

- [ ] **Step 5: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/ tests/characterization/ -q`
Expected: PASS.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/modes/hold.py tests/unit/runtime/test_hold_applied_output.py
jj describe --stdin <<'EOF'
feat(hold): report zero duty on both lid-open paths and on a manual auger

Both lid-open branches report, not just the toggle: _auger_cycle_tick has no lid
guard, so the auger really does resume cycling at u_min during a pause and both
transitions are equally real to a model.

The manual report fires at override start, covering the whole window rather than
leaving a hole a predictor would fill by assuming the last duty persisted.
EOF
```

---

### Task 10: `common/controller_model_state.py`

**Files:**
- Create: `common/controller_model_state.py`
- Test: `tests/unit/common/test_controller_model_state.py`

**Interfaces:**
- Produces: `ControllerModelStore(reader=None, writer=None)` with `.load(name) -> dict | None` and `.save(name, snapshot) -> bool`; module constants `MODEL_STATE_KEY`, `SCHEMA_VERSION`, `MAX_SNAPSHOT_BYTES`. Task 11 constructs it; Plan B's snapshots must satisfy its envelope rules.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: controller model store"
```

Create `tests/unit/common/test_controller_model_state.py`:

```python
"""The store owns "is this a bounded, JSON-safe record" and nothing else.

It never judges model fields or physics -- that is the controller's job in
restore_model -- so these tests use two unrelated snapshot shapes to prove it is
genuinely model-agnostic.
"""

import json

import pytest

from common.controller_model_state import (
    MAX_SNAPSHOT_BYTES,
    MODEL_STATE_KEY,
    SCHEMA_VERSION,
    ControllerModelStore,
)

FOPDT = {"revision": 1, "K": 761.0, "tau": 635.0, "theta": 25.0}
UNRELATED = {"revision": 1, "coefficients": [0.1, 0.2], "label": "something else entirely"}


class _FakeStore:
    def __init__(self, initial=None):
        self.blobs = dict(initial or {})
        self.writes = 0

    def read(self, key):
        # matches read_generic_key: json.loads(None) on an absent key
        return json.loads(self.blobs[key]) if key in self.blobs else json.loads(None)

    def write(self, key, value):
        self.writes += 1
        self.blobs[key] = json.dumps(value)


def _store():
    fake = _FakeStore()
    return ControllerModelStore(reader=fake.read, writer=fake.write), fake


@pytest.mark.parametrize("snapshot", [FOPDT, UNRELATED])
def test_round_trips_any_json_safe_shape(snapshot):
    store, _ = _store()
    assert store.save("pid_sp", snapshot) is True
    assert store.load("pid_sp") == snapshot


def test_load_returns_none_for_an_absent_key():
    store, _ = _store()
    assert store.load("pid_sp") is None


def test_load_returns_none_for_an_absent_controller():
    store, _ = _store()
    store.save("pid_sp", FOPDT)
    assert store.load("mpc") is None


def test_controllers_do_not_cross_contaminate():
    store, _ = _store()
    store.save("pid_sp", FOPDT)
    store.save("mpc", UNRELATED)
    assert store.load("pid_sp") == FOPDT
    assert store.load("mpc") == UNRELATED


def test_skips_a_non_advancing_revision():
    store, fake = _store()
    assert store.save("pid_sp", {"revision": 5, "K": 700.0}) is True
    writes = fake.writes
    assert store.save("pid_sp", {"revision": 5, "K": 999.0}) is False
    assert store.save("pid_sp", {"revision": 4, "K": 999.0}) is False
    assert fake.writes == writes
    assert store.load("pid_sp")["K"] == 700.0
    assert store.save("pid_sp", {"revision": 6, "K": 999.0}) is True


def test_load_primes_the_revision_cache():
    fake = _FakeStore()
    first = ControllerModelStore(reader=fake.read, writer=fake.write)
    first.save("pid_sp", {"revision": 5, "K": 700.0})
    second = ControllerModelStore(reader=fake.read, writer=fake.write)
    second.load("pid_sp")
    assert second.save("pid_sp", {"revision": 5, "K": 999.0}) is False


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        [],
        "not a dict",
        {"K": 700.0},  # no revision
        {"revision": "1"},  # revision not an int
        {"revision": True},  # bool is not an acceptable int here
        {"revision": -1},
        {"revision": 1, "K": float("nan")},
        {"revision": 1, "K": float("inf")},
        {"revision": 1, "obj": object()},
    ],
)
def test_rejects_a_malformed_snapshot(snapshot):
    store, fake = _store()
    assert store.save("pid_sp", snapshot) is False
    assert fake.writes == 0


def test_rejects_an_oversized_snapshot():
    store, fake = _store()
    assert store.save("pid_sp", {"revision": 1, "blob": "x" * MAX_SNAPSHOT_BYTES}) is False
    assert fake.writes == 0


@pytest.mark.parametrize(
    "raw",
    [
        "not a dict",
        {"version": SCHEMA_VERSION + 1, "models": {"pid_sp": FOPDT}},
        {"models": {"pid_sp": FOPDT}},
        {"version": SCHEMA_VERSION, "models": "not a dict"},
        {"version": SCHEMA_VERSION},
    ],
)
def test_reads_are_fail_closed_on_a_bad_envelope(raw):
    fake = _FakeStore({MODEL_STATE_KEY: json.dumps(raw)})
    store = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert store.load("pid_sp") is None


def test_a_bad_member_does_not_poison_a_good_one():
    fake = _FakeStore(
        {MODEL_STATE_KEY: json.dumps({"version": SCHEMA_VERSION, "models": {"pid_sp": FOPDT, "mpc": {"no": "revision"}}})}
    )
    store = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert store.load("pid_sp") == FOPDT
    assert store.load("mpc") is None


def test_a_read_failure_does_not_raise():
    def boom(key):
        raise RuntimeError("datastore is down")

    store = ControllerModelStore(reader=boom, writer=lambda k, v: None)
    assert store.load("pid_sp") is None


def test_a_write_failure_returns_false():
    def boom(key, value):
        raise RuntimeError("datastore is down")

    store = ControllerModelStore(reader=lambda k: json.loads(None), writer=boom)
    assert store.save("pid_sp", FOPDT) is False
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/common/test_controller_model_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'common.controller_model_state'`.

- [ ] **Step 3: Write the store**

Create `common/controller_model_state.py`:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire Controller Model Persistence
*****************************************

 Description: Per-controller learned-model snapshots in SQLite.

 A controller that identifies its grill's dynamics online spends the first hour
 of a fresh install learning what it already knew last cook. This keeps that
 model across restarts.

 Everything lives under one generic key as
 {"version": 1, "models": {<controller name>: <snapshot>}}, keyed by controller
 name so switching controllers does not cross-contaminate.

 There is no staging, no flush, no write throttle and no atomic-replace
 sequence: the SQLite transaction is the atomicity and a write is cheap. The one
 guard is that a revision which is not an advance does not write, so the
 per-tick call costs a dict lookup on the overwhelming majority of ticks where
 nothing was learned.

*****************************************
"""

import json

from common.datastore_accessors import read_generic_key, write_generic_key

MODEL_STATE_KEY = "controller_model_state"
SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 8192


def _valid(snapshot):
    """Envelope validation only: bounded, JSON-safe, carrying a revision.

    Deliberately says nothing about model field names or physics. The store owns
    "is this a bounded, JSON-safe record"; the controller owns "do these numbers
    describe a possible grill" and re-checks in restore_model.
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    revision = snapshot.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return False
    try:
        encoded = json.dumps(snapshot, allow_nan=False)
    except ValueError, TypeError:
        return False
    return len(encoded.encode("utf-8")) <= MAX_SNAPSHOT_BYTES


class ControllerModelStore:
    def __init__(self, reader=None, writer=None):
        self._reader = reader or read_generic_key
        self._writer = writer or write_generic_key
        self._revisions = {}

    def load(self, name):
        snapshot = self._read_models().get(name)
        if snapshot is None:
            return None
        self._revisions[name] = snapshot["revision"]
        return snapshot

    def save(self, name, snapshot):
        if not _valid(snapshot):
            return False
        revision = snapshot["revision"]
        if name in self._revisions and revision <= self._revisions[name]:
            return False
        models = self._read_models()
        models[name] = snapshot
        try:
            self._writer(MODEL_STATE_KEY, {"version": SCHEMA_VERSION, "models": models})
        except Exception:
            return False
        self._revisions[name] = revision
        return True

    def _read_models(self):
        """Every stored snapshot, fail-closed.

        A storage error, a root-schema mismatch or a bad member yields nothing
        rather than a half-trusted mix. read_generic_key raises TypeError for an
        absent key (it calls json.loads(None)); that is caught here with
        everything else.
        """
        try:
            raw = self._reader(MODEL_STATE_KEY)
        except Exception:
            return {}
        if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
            return {}
        models = raw.get("models")
        if not isinstance(models, dict):
            return {}
        return {name: snap for name, snap in models.items() if _valid(snap)}
```

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/common/test_controller_model_state.py -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format common/controller_model_state.py tests/unit/common/test_controller_model_state.py
jj describe --stdin <<'EOF'
feat(common): persist per-controller model snapshots in SQLite

One generic key holding {"version": 1, "models": {<name>: <snapshot>}}, keyed by
controller so switching controllers does not cross-contaminate.

Validation is envelope-only -- non-empty dict, integer revision, JSON-encodable
with allow_nan=False, bounded size -- because the store owns "is this a bounded,
JSON-safe record" and the controller owns "do these numbers describe a possible
grill". Reads are fail-closed; a bad member yields nothing rather than a
half-trusted mix.
EOF
```

---

### Task 11: Wire the store into Hold

No shipped controller returns a snapshot until Plan B, so this call site no-ops in production today. Its coverage comes from unit tests and a stub controller that does implement the hooks — not from an integration run.

**Files:**
- Modify: `controller/runtime/modes/hold.py`
- Test: `tests/unit/runtime/test_hold_model_persistence.py` (create)

**Interfaces:**
- Consumes: `ControllerModelStore` (Task 10); `runner.get_model_snapshot`/`restore_model` (Tasks 5, 6); the Hold report sites (Tasks 8, 9).

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: hold model persistence"
```

Create `tests/unit/runtime/test_hold_model_persistence.py`:

```python
"""Hold restores a controller's model at setup and saves it as it changes."""

from controller.applied_output import OutputSource
from tests.fakes.runner import FakeControllerRunner


class _FakeModelStore:
    def __init__(self, initial=None):
        self.models = dict(initial or {})
        self.saves = []

    def load(self, name):
        return self.models.get(name)

    def save(self, name, snapshot):
        self.saves.append((name, snapshot))
        self.models[name] = snapshot
        return True


def test_setup_restores_a_stored_model_before_seeding(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    store = _FakeModelStore({"pid_sp": {"revision": 3, "K": 700.0}})
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    assert runner.restored == [{"revision": 3, "K": 700.0}]
    # the seed report comes after the restore, so it lands on the restored model
    assert [a.source for a in runner.applied] == [OutputSource.SEED]


def test_setup_with_no_stored_model_restores_nothing(hold_cycle):
    runner = FakeControllerRunner(period=0.01)
    hold = hold_cycle(runner, model_store=_FakeModelStore(), controller="pid_sp")
    hold.setup()
    assert runner.restored == []


def test_per_tick_saves_the_controller_snapshot(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    store = _FakeModelStore()
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    runner.snapshot = {"revision": 1, "K": 700.0}
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert store.saves == [("pid_sp", {"revision": 1, "K": 700.0})]


def test_a_controller_with_no_snapshot_saves_nothing(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    store = _FakeModelStore()
    hold = hold_cycle(runner, model_store=store, controller="pid")
    hold.setup()
    runner.snapshot = None
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert store.saves == []


def test_reconfigure_restores_the_model_and_reseeds(hold_cycle):
    runner = FakeControllerRunner(period=0.0).script([_output(0.5)])
    store = _FakeModelStore({"pid_sp": {"revision": 3, "K": 700.0}})
    hold = hold_cycle(runner, model_store=store, controller="pid_sp")
    hold.setup()
    runner.restored.clear()
    runner.applied.clear()
    hold.control["controller_update"] = True
    hold.on_tick(now=100.0, ptemp=200.0, current_output_status=_off())
    assert runner.restored == [{"revision": 3, "K": 700.0}]
    assert runner.applied[0].source is OutputSource.SEED
```

`hold_cycle` already takes `model_store` and `controller` (Task 8's fixture); `_output` and `_off` come from the same conftest.

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/test_hold_model_persistence.py -q`
Expected: FAIL — `runner.restored` is empty.

- [ ] **Step 3: Construct the store in `HoldMode.setup`**

At the top of `hold.py`:

```python
from common.controller_model_state import ControllerModelStore
```

In `setup()`, before the `build_runner` call:

```python
        self._model_store = self._model_store or ControllerModelStore()
        self._controller_name = self.settings["controller"]["selected"]
```

Declare `_model_store = None` as a class attribute on `HoldMode` (beside `name = Mode.HOLD`) so tests can inject one.

- [ ] **Step 4: Restore after the runner is built**

In `setup()`, immediately after the `build_runner` call and before the fan-ownership line:

```python
        if self._runner is not None:
            self._restore_model()
```

And add the helper beside `_on_manual_output`:

```python
    def _restore_model(self):
        snapshot = self._model_store.load(self._controller_name)
        if snapshot is None:
            return
        import control as _control

        if self._runner.restore_model(snapshot):
            _control.eventLogger.info(f"Restored the stored {self._controller_name} model")
        else:
            _control.eventLogger.warning(f"Stored {self._controller_name} model was rejected; starting fresh")
```

The seed report added in Task 8 already sits after this, so it lands on the restored model.

- [ ] **Step 5: Save on the per-tick path**

In `on_tick`, at the end of the `if (now - self.state.controller.cycle_start) > controller_interval:` block, after the applied-output report:

```python
            snapshot = self._runner.get_model_snapshot()
            if snapshot is not None:
                self._model_store.save(self._controller_name, snapshot)
```

The store skips a non-advancing revision, so this is a dict lookup on almost every tick. There is no teardown flush: a snapshot worth keeping was already saved on the tick that produced it.

- [ ] **Step 6: Restore and reseed on reconfigure**

In `on_tick`'s `controller_update` block, inside `if self._controller_status == "Active":` after the existing log line:

```python
                self._controller_name = settings["controller"]["selected"]
                self._restore_model()
                self._runner.set_output(
                    seed_output(
                        self.state.cycle.ratio,
                        now,
                        lid_open=self.state.lid.open_detected,
                        manual_override_active=self.state.manual_override["auger"] > now,
                        fan_assist_active=self.state.fan.assist,
                        auger_output=current_output_status["auger"],
                    )
                )
```

- [ ] **Step 7: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/runtime/ tests/characterization/ -q`
Expected: PASS.

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format controller/runtime/modes/hold.py tests/unit/runtime/test_hold_model_persistence.py
jj describe --stdin <<'EOF'
feat(hold): restore and persist the controller's learned model

Restores at setup and after a settings-triggered rebuild, ahead of the seed
report so the report lands on the restored model. Saves on the per-tick path,
where the store's non-advancing-revision guard makes it a dict lookup on almost
every tick.

No shipped controller returns a snapshot yet, so this is inert in production
until PID-SP does.
EOF
```

---

### Task 12: MPC `get_status()`

`controller_state()` publishes `dict(core.__dict__)` to the MQTT notification payload. For MPC that dict holds do-mpc solver objects, numpy arrays and the estimator.

**Files:**
- Modify: `controller/mpc.py`
- Test: `tests/unit/mpc/test_mpc_controller.py`

**Interfaces:**
- Produces: `Controller.get_status() -> dict` on MPC, consumed by `controller_state()` (Task 5).

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: mpc get_status"
```

Append to `tests/unit/mpc/test_mpc_controller.py` (keep the file's existing do-mpc skip marker):

```python
import json


def test_get_status_is_json_safe(mpc_controller):
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    status = mpc_controller.get_status()
    # the real bar: it survives the MQTT encoder
    encoded = json.dumps(status, allow_nan=False)
    assert "do_mpc" not in encoded
    assert set(status) >= {"set_point", "set_point_c", "last_Q", "applied_Q", "policy", "x_hat"}
    assert isinstance(status["x_hat"], list)
    assert all(isinstance(v, float) for v in status["x_hat"])


def test_dunder_dict_is_not_json_safe(mpc_controller):
    """The reason get_status exists; if this ever passes, revisit the fallback."""
    mpc_controller.update(200.0)
    with pytest.raises(TypeError):
        json.dumps(dict(mpc_controller.__dict__))
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/mpc/test_mpc_controller.py -q`
Expected: FAIL — `AttributeError: 'Controller' object has no attribute 'get_status'`. If the whole module skips, do-mpc is not installed; run it in the main checkout.

- [ ] **Step 3: Implement `get_status`**

In `controller/mpc.py`, beside `wants_async`:

```python
    def get_status(self):
        return {
            "set_point": self.set_point,
            "set_point_c": float(self._set_point_c),
            "last_Q": float(self._last_Q),
            "applied_Q": float(self._applied_Q),
            "policy": "net" if self._net is not None else "nlp",
            "u_min": float(self.u_min),
            "u_max": float(self.u_max),
            "x_hat": None if self._x_hat is None else [float(v) for v in np.asarray(self._x_hat).reshape(-1)],
        }
```

`self._applied_Q` does not exist yet — add it in `__init__` beside `self._last_Q` as `self._applied_Q = float(cfg["Q_min"])`. Task 13 gives it its real meaning.

- [ ] **Step 4: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/mpc/ tests/unit/runtime/ -q`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_controller.py
jj describe --stdin <<'EOF'
fix(mpc): publish JSON-safe diagnostics instead of the controller __dict__

controller_state() publishes dict(core.__dict__) to the MQTT payload, which for
MPC means do-mpc solver objects, numpy arrays and the estimator.
EOF
```

---

### Task 13: MPC `set_output()` and the `_last_Q` / `_applied_Q` split

**Files:**
- Modify: `controller/mpc.py`
- Test: `tests/unit/mpc/test_mpc_controller.py`

**Interfaces:**
- Consumes: `AppliedOutput` (Task 3).
- Produces: `Controller.set_output(applied)` on MPC; `self._applied_Q` as the estimator's input and (clamped) the net's `Q_prev`; `self._last_Q` retained as the previous *commanded* move.

- [ ] **Step 1: `jj new`, then write the failing test**

```bash
jj new -m "wip: mpc set_output"
```

Append to `tests/unit/mpc/test_mpc_controller.py`:

```python
from controller.applied_output import AppliedOutput, OutputSource


def test_set_output_inverts_the_allocation_exactly(mpc_controller):
    """allocate() is affine, so applied ratio -> applied Q round-trips."""
    from controller.mpc_allocator import allocate

    cfg = mpc_controller.cfg
    for q in (cfg["Q_min"], 0.5 * (cfg["Q_min"] + cfg["Q_max"]), cfg["Q_max"]):
        auger, _ = allocate(
            q,
            Q_min=cfg["Q_min"],
            Q_max=cfg["Q_max"],
            u_min=mpc_controller.u_min,
            u_max=mpc_controller.u_max,
            fan_min_pct=cfg["fan_min_pct"],
            fan_max_pct=cfg["fan_max_pct"],
            enable_fan=bool(cfg["enable_fan_input"]),
        )
        mpc_controller.set_output(AppliedOutput(auger, OutputSource.CONTROLLER, 1.0))
        assert mpc_controller._applied_Q == pytest.approx(q)


def test_a_lid_open_report_goes_below_q_min(mpc_controller):
    """The estimator gets the honest input; being told Q_min for a pause it did
    not take is the defect being fixed."""
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    assert mpc_controller._applied_Q < mpc_controller.cfg["Q_min"]


def test_the_estimator_is_driven_by_the_applied_input(mpc_controller, monkeypatch):
    seen = []
    real = mpc_controller.estimator.update
    monkeypatch.setattr(mpc_controller.estimator, "update", lambda u, y: (seen.append(u), real(u, y))[1])
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    applied = mpc_controller._applied_Q
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(applied)


def test_with_no_report_the_command_is_assumed_applied(mpc_controller, monkeypatch):
    """Preserves today's behavior for the sync path and controller-only tests."""
    seen = []
    real = mpc_controller.estimator.update
    monkeypatch.setattr(mpc_controller.estimator, "update", lambda u, y: (seen.append(u), real(u, y))[1])
    mpc_controller.set_target(225.0)
    mpc_controller.update(200.0)
    commanded = mpc_controller._last_Q
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(commanded)


def test_the_net_sees_the_applied_input_clamped_to_its_trained_span(mpc_controller, monkeypatch):
    if mpc_controller._net is None:
        pytest.skip("net policy not loaded")
    seen = []
    monkeypatch.setattr(
        mpc_controller._net, "firing_rate", lambda x, u_prev, sp: (seen.append(u_prev), 50.0)[1]
    )
    mpc_controller.set_target(225.0)
    mpc_controller.set_output(AppliedOutput(0.0, OutputSource.LID_OPEN, 1.0))
    mpc_controller.update(200.0)
    assert seen[-1] == pytest.approx(mpc_controller.cfg["Q_min"])


def test_a_degenerate_actuator_span_is_ignored(mpc_controller):
    before = mpc_controller._applied_Q
    mpc_controller.u_max = mpc_controller.u_min
    mpc_controller.set_output(AppliedOutput(0.5, OutputSource.CONTROLLER, 1.0))
    assert mpc_controller._applied_Q == before
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/mpc/test_mpc_controller.py -q`
Expected: FAIL — `AttributeError: 'Controller' object has no attribute 'set_output'`.

- [ ] **Step 3: Implement `set_output`**

In `controller/mpc.py`, beside `get_status`:

```python
    def set_output(self, applied):
        """Take the auger duty that actually ran and recover the firing rate.

        allocate() is affine, so this inverts it exactly for any ratio the
        allocator produced. A lid-open or manual-off report arrives as 0.0,
        below u_min, and inverts to a Q below Q_min -- that is the honest answer
        and the estimator gets it unmodified.
        """
        span = self.u_max - self.u_min
        if span <= 0:
            return
        q_span = self.cfg["Q_max"] - self.cfg["Q_min"]
        self._applied_Q = self.cfg["Q_min"] + (float(applied.ratio) - self.u_min) / span * q_span
```

- [ ] **Step 4: Drive the estimator and the net from `_applied_Q`**

In `update()`, replace the block Task 2 left at lines 291-302 with:

```python
        x_hat = self.estimator.update(self._applied_Q, y)
        self._x_hat = x_hat
        # The net's Q_prev feature was trained on the value the sampler drove the
        # plant with, and only ever inside [Q_min, Q_max].
        self._policy_u_prev = float(np.clip(self._applied_Q, self.cfg["Q_min"], self.cfg["Q_max"]))
        # 2) compute firing rate Q from the active policy (net or NLP). On any
        #    error we hold the previous move so the control loop never breaks.
        try:
            if self._net is not None:
                Q = self._net.firing_rate(x_hat, self._policy_u_prev, self._set_point_c)
            else:
                Q = float(np.asarray(self.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
        except Exception:
            Q = self._last_Q
        Q = float(np.clip(Q, self.cfg["Q_min"], self.cfg["Q_max"]))
        self._last_Q = Q
        # Assume the command is applied until a report says otherwise.
        self._applied_Q = Q
```

`self._last_Q` stays the previous *commanded* move and stays the `except` fallback: holding the previous move is the intent there, and a lid-open zero must not silently become the new command.

In `set_target`, beside `self._last_Q = self.cfg["Q_min"]`:

```python
        self._applied_Q = float(self.cfg["Q_min"])
```

- [ ] **Step 5: Run the tests**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/mpc/ -q`
Expected: PASS. `test_mpc_closed_loop.py` and `test_mpc_net_loop.py` may move — MPC's behavior genuinely changes only when something reports a non-commanded duty, which those tests do not do, so if they fail investigate rather than re-baseline.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format controller/mpc.py tests/unit/mpc/test_mpc_controller.py
jj describe --stdin <<'EOF'
fix(mpc): drive the state estimator with the applied duty, not the command

The estimator read _last_Q -- the firing rate MPC commanded. Hold then clamps the
derived ratio to [u_min, u_max] and forces the auger off outright during a
lid-open pause, so the estimator spent every pause believing a command that
never reached the auger.

_applied_Q is now what the plant received and what the estimator sees, recovered
by inverting the affine allocation; _last_Q keeps its own meaning as the previous
commanded move and stays the exception fallback. The net's Q_prev takes the
applied value clamped to the span it was trained on.
EOF
```

---

### Task 14: MPC after-numbers, and the agreement decision

**Files:**
- Create: `docs/superpowers/experiments/_matrix_after_mpc.json` (generated)
- Create: `docs/superpowers/experiments/_net_vs_nlp_after.json` (generated)

> **CORRECTION (Task 17, superseding Task 16) — this task's GrillSim matrix conclusion is replaced. Its replay/agreement conclusion is re-measured by Task 18 below, which reaches the same decision.**
>
> Task 14's matrix figures (`IAE -6.2% mean, pct_within_5f +2.09, overshoot 9.9 -> 7.5 F, settle ~300 s sooner` on `lid_open_225`) were measured under a `controller_matrix.py` lid model that held the auger off and reported applied duty `0.0` for all 120 s of the pause. Task 16 corrected that *actuator* model to `hold.py`'s: one `AppliedOutput(ratio=0.0)` at the detection instant (`hold.py:238-266`, one-shot via the `target_temp_achieved` interlock), then `cycle.ratio` pinned to `cycle_data["u_min"]` (`hold.py:171-173`) with the auger still cycling at that duty (`hold.py:228` -> `base.py:118-147`, no lid gate). Measured under that correction, the effect of applied-duty feedback was `+0.86%` IAE.
>
> **That `+0.86%` is retired too.** Task 16 left the scenario with no lid on the *plant* side. `GrillSim` scales both `h_fc` and `h_amb` with the fan, so cutting the fan trapped heat and the chamber *warmed* 224.9 -> 227.3 F across a "lid open" event — an excursion of 2.9 F at its deepest, against the >=33.75 F fall that arms `hold.py:241`'s detector at a 225 F setpoint. There was no disturbance for applied-duty feedback to reject, so `+0.86%` was measured on an event production cannot reach. Task 17 gave `GrillSim.step` a `lid_open` chamber-to-ambient leak and wired the scenario to it; the chamber now falls to 177-180 F, past the 191.25 F trigger, on every controller and seed.
>
> Task 17's fix round corrected the *pause length* as well. The harness had pinned the auger at `u_min` for the whole 120 s lid window; `hold.py:265` and `hold.py:296` both arm the pause for `LidOpenPauseTime` (default **60 s**) and `hold.py:269-271` clears it on that timer, restarting the fan, with no reference to the lid. A lid window is therefore two independent things — the physical opening that leaks heat for its whole duration, and a `LidOpenPauseTime` actuator pause — and a window longer than the pause ends with the controller back at full authority while the chamber is still losing heat. The scenario now models all three phases, and `LID_PAUSE_S` is read from settings rather than hard-coded.
>
> Re-measured with a lid that actually opens and a pause the length production grants — MPC, `lid_open_225`, 5 seeds, the two arms differing only in whether `mpc.Controller.set_output` is live:
>
> | metric | Task 14 (no plant lid, whole-pause auger off) | Task 16 (no plant lid, `hold.py` actuators) | Task 17 (open lid, 60 s pause) |
> |---|---|---|---|
> | IAE | -6.17% (better) | +0.86% (worse) | **-5.96% (better)** |
> | `pct_within_5f` | +2.09 | -0.074 | **+0.77** |
> | overshoot | 9.9 -> 7.5 F | 7.54 -> 7.59 F | **15.81 -> 11.10 F** |
> | settle | ~300 s sooner | 5.4 s later | **61 s sooner** |
>
> **The mechanism, measured on `mpc/lid_open_225/0`:** `hold.py:171-173` pins `state.cycle.ratio` to `u_min` — not the controller's command — and `hold.py:206` reports `requested=state.controller.output`, the command itself. Production behaves the same way. MPC's `control_period` is 5.0 s, so 13 of its reports land inside the 60 s pause; 12 are per-solve reports and **all 12 diverge**. With the chamber falling, the requested duty ranges `0.1663`-`0.6460` (mean `0.380`) while the applied duty holds at `u_min = 0.15`. Without `set_output` the estimator integrates the command, so it believes roughly two and a half times the fuel actually delivered went into the firepot, and the recovery overshoots by 15.8 F on average. With `set_output` live the estimator integrates the pinned `u_min`, and overshoot is 11.1 F. This is the divergence the plumbing was built to correct; under Task 16's fanless-but-sealed chamber the equivalent ticks diverged only `0.1566`-`0.2041` against `0.15`, which is why the effect was invisible.
>
> **The disturbance amplitude is chosen, not identified.** `GrillSim`'s `h_lid = 1.5` was calibrated so the excursion crosses `LidOpenThreshold` with margin, not fitted to a real grill, so the *magnitude* of the percentages below is a function of that choice; the direction and the mechanism are not.
>
> **What this does and does not establish.** `-5.96%` over 5 seeds, sign consistent on all 5, range `-5.61%`..`-6.36%`: on this scenario the effect is unambiguous, and it replaces both earlier numbers. It is one scenario at one setpoint on one plant at one chosen leak coefficient, so it establishes that applied-duty feedback pays off when commanded and applied duty diverge by several times over a sustained window — not a figure to quote for closed-loop performance generally. The paths this matrix still does not cover (manual override, `u_max` clamping outside a pause, the sub-`u_min` reports Task 15's retraining addressed) remain uncovered. Task 14's Step 4 decision — *"Task 15 runs, the net is not shipped unchanged"* — rested on the **replay** excursion/RMS gate, not on the matrix. That gate was itself measured on a lid the replay never opened; Task 18 below re-measures it.
>
> **Artifact state.** Both matrix artifacts were re-captured by Task 17 under the open-lid plant, and they remain a genuine before/after pair on their 30 `mpc` rows:
>
> - `_matrix_baseline.json` — **before**: `mpc.Controller.set_output` replaced by a no-op, so nothing writes `_applied_Q` from outside and `update()`'s own `self._applied_Q = Q` (`controller/mpc.py:401`) feeds the estimator the *commanded* duty. That is exactly pre-Task-13 MPC, which inherited `ControllerBase.set_output`'s no-op.
> - `_matrix_after_mpc.json` — **after**: `set_output` live, applied-duty feedback in the loop.
> - The 30 `pid_sp` rows in `_matrix_baseline.json` belong to neither arm: `pid_sp` does not override `set_output`, so it is bit-identical under both (measured, not assumed). Their `lid_open_225` rows moved with the plant change; the rest did not.
> - The arms are verified against the pre-Task-17 capture: all 50 non-lid rows across both controllers are **bit-identical** to it, so the plant change is confined to the scenario that opens the lid.
> - Rows now carry `lid_min_temp_f`, the coldest reading from the first lid opening to the end of the run, and `lid_recovery_s`, the seconds from that opening until the chamber is back inside the 5 F band. Both are `null` on the five scenarios with no lid window; on `lid_open_225` the depth is 177.1-180.3 F and the recovery 128-172 s. Depth and width are both recorded in the artifact rather than inferred from it, and they fail in opposite directions: a plant that leaks no heat leaves the chamber near setpoint, while a pause modelled as lasting the whole lid window digs the trough *deeper* than required. Depth alone cannot distinguish the pause length.
>
> **What the Step 4 diff actually shows.** On the 25 non-lid rows, ~1e-10 relative drift (worst `3.06e-10`, `mpc/steady_450/2` `overshoot_f`) — `set_output` inverts `allocate()` in floating point, and 3.5 h of closed-loop integration amplifies the round-trip's few-ULP error to that. It is ULP noise, not an effect. On the 5 `lid_open_225` rows, the `-5.96%` mean IAE above.

- [ ] **Step 1: `jj new`**

```bash
jj new -m "wip: mpc after numbers"
```

- [ ] **Step 2: Re-run the replay**

```bash
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/net_vs_nlp_replay.py \
  --out docs/superpowers/experiments/_net_vs_nlp_after.json
```

- [ ] **Step 3: Re-run the matrix for MPC**

```bash
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/controller_matrix.py \
  --controllers mpc --out docs/superpowers/experiments/_matrix_after_mpc.json -w 8
```

The baseline was captured with do-mpc installed, so every MPC row solved the real NLP rather than the net approximation. Confirm the same is true here before comparing — a run that fell back to the net is measuring a different controller, and the difference would read as an effect of this plan's changes.

- [ ] **Step 4: Compare and decide**

```bash
QT_QPA_PLATFORM=offscreen uv run python - <<'EOF'
import json
base = {(r["scenario"], r["seed"]): r for r in json.load(open("docs/superpowers/experiments/_matrix_baseline.json")) if r["controller"] == "mpc"}
after = {(r["scenario"], r["seed"]): r for r in json.load(open("docs/superpowers/experiments/_matrix_after_mpc.json"))}
for key in sorted(base):
    b, a = base[key], after[key]
    print(f"{key[0]:16s} seed{key[1]} iae {b['iae']:10.0f} -> {a['iae']:10.0f}  "
          f"within5 {b['pct_within_5f']:5.1f} -> {a['pct_within_5f']:5.1f}")
nb = json.load(open("docs/superpowers/experiments/_net_vs_nlp_baseline.json"))
na = json.load(open("docs/superpowers/experiments/_net_vs_nlp_after.json"))
for b, a in zip(nb, na):
    print(f"replay seed{b['seed']}: "
          f"lid excursions {b['excursion_n_lid']} -> {a['excursion_n_lid']} | "
          f"lid margin to Q_min {b['margin_min_to_q_min_lid']:+.3f} -> {a['margin_min_to_q_min_lid']:+.3f} | "
          f"warm excursions {b['excursion_n_warm']} -> {a['excursion_n_warm']} | "
          f"rms_all_raw_warm {b['rms_all_raw_warm']:.3f} -> {a['rms_all_raw_warm']:.3f} | "
          f"warm_start_s {b['warm_start_s']} -> {a['warm_start_s']}")
EOF
```

Those key names are the ones the script actually emits — check them against a baseline record before trusting this snippet, since a `KeyError` here is the good outcome and a silently-renamed key is not.

The matrix half of this snippet compares two real arms after Task 17's re-capture, but it separates them on one scenario only. Expect ~1e-10 relative drift on the 25 non-lid rows — floating-point noise from `set_output`'s inverse of `allocate()`, amplified by 3.5 h of integration, not an effect — and `-5.96%` mean IAE on the 5 `lid_open_225` rows. See the Task 17 correction at the top of this task before reading any matrix number below. The replay half is unaffected.

> **CORRECTION (Task 18) — the replay's lid window had the same defect, and the agreement decision survives it.**
>
> `net_vs_nlp_replay.py:337` called `plant.step` without `lid_open`, exactly as `controller_matrix.py` did before Task 17. Everything around it was faithful to `hold.py` — the single `AppliedOutput(0.0)` at detection, `u_min` pinning, the auger still cycling — but the chamber never lost heat, and cutting the fan *lowers* `h_amb` (`grill_sim.py:112`), so it warmed across the window. Measured on the replay's own operating point: the deepest dip was **1.5 F**, with the chamber peaking at 228.3 F against the 223.8 F it started from, versus the 33.75 F fall that arms `hold.py:241`. The replay also pinned the actuators for the whole 120 s window rather than `LidOpenPauseTime`. Both are fixed, and `lid_min_temp_f` is now recorded in every row so the excursion is evidence in the artifact rather than a claim in prose.
>
> **The gate's primary quantity is unchanged: `excursion_n_lid` is `0/24` on every seed of every arm.** By the reading order this task fixes below — excursion count decides, `rms_all_raw_warm` may size but never overrule it — the retrained net still passes under a lid that opens. The net's closest approach to `Q_min` in the lid window *widened*, from `+1.437` before the fix to `+1.856..+2.002` after, out of a 95-wide box.
>
> Applied-duty feedback, compared within `lid_model="faithful"` as the comparability rule requires (3 seeds, sign consistent on all 3):
>
> | metric | `estimator_input="command"` | `estimator_input="applied"` | change |
> |---|---|---|---|
> | `rms_lid_raw` | 6.70 / 6.70 / 6.72 | 5.27 / 5.28 / 5.34 | **-21%** |
> | `rms_all_raw_warm` | 0.928 / 0.928 / 0.926 | 0.659 / 0.660 / 0.658 | **-29%** |
> | `excursion_n_lid` | 0/24 | 0/24 | unchanged |
>
> **What the corrected lid does change** is the size of the disagreement it is measuring. On the same arm, `rms_lid_raw` went `1.20 -> 5.27` and `max_lid_raw` `1.62 -> 10.04` once the lid actually opened: the window now visits genuinely cold states, and the net disagrees with the NLP far more there than the old figures suggested. It stays inside `[Q_min, Q_max]` throughout, so this sizes the margin rather than spending it.
>
> **Two artifacts could not be re-captured and are pre-Task-18.** `_net_vs_nlp_baseline.json` is a genuine pre-change arm (pre-`_applied_Q`, pre-`set_output`) and must stay one. `_net_vs_nlp_after_faithful.json` was measured against the net as it stood *before* Task 15 retrained it, which this checkout no longer has. Do not compare either against the three arms above. The rows record `lid_model` and `estimator_input` but **no identity for the `.npz`**, so nothing in the files themselves distinguishes a pre- from a post-retrain arm — the provenance lives only in the filenames and in this note.
>
> **`sample_mpc.py` still has no thermal lid**, so the net's training episodes contain the actuator pause but never the cold chamber the replay now visits. That is the most plausible source of the `1.20 -> 5.27` growth. It is not blocking — excursions stay at zero — but retraining on a thermally-correct lid is the obvious next lever if that margin ever needs widening. Not done here: it is the most expensive step in either plan.

The gate is **relative**, and the agreement gate binds. Read the quantities in this order:

- **Primary: excursion count in the lid window.** The number of net *raw* outputs falling outside `[Q_min, Q_max]` while the auger is paused. Baseline is exactly `0` on every seed, so any nonzero after-value is the predicted failure appearing, not noise. This is the decision.
- **Secondary: `rms_all_raw_warm`.** Raw difference, warm window only. Use it to size a change the excursion count already flagged, not to overrule a clean excursion count.
- **Context only: the whole-run and clamped figures.** `max_all` over the whole run is dominated by the ignition transient — its `argmax` sits about 45 s in, four times anything the controller does afterwards — so it compares startups, not policies. Do not gate on it.

Then:

- **Excursion count still zero in the lid window and `rms_all_raw_warm` at or below baseline** → the net ships unchanged. Record the numbers that justified not retraining and **skip Task 15**.
- **Either grew** → go to Task 15. Do not reason about whether the matrix "looks fine anyway"; the net is only worth having while it approximates the NLP.

Do not substitute the clamped difference for the raw one anywhere in this comparison. Both policies are bounded by the same box, so clamping first makes a net demanding `-63` against an NLP asking `5.0` read as perfect agreement — which is precisely the condition this gate exists to catch.

If the matrix regresses while the replay holds, that is a different finding — the applied-duty change itself hurt closed-loop performance — and it goes back to the user rather than being tuned around.

This matrix gate never bound the decision, and still does not: under the open-lid plant the arms differ by `-5.96%` IAE on the only scenario that separates them, which is an improvement rather than the regression the gate was written to catch. See the Task 17 correction at the top of this task.

- [ ] **Step 5: Commit the numbers**

```bash
jj describe --stdin <<'EOF'
test(mpc): capture the after numbers for applied-duty feedback

Replay disagreement and the GrillSim matrix, both against the pre-change
baselines. <Paste the comparison table here, then state the decision: net ships
unchanged, or Task 15 runs.>
EOF
```

---

### Task 15 (conditional): sampler pause intervals and net regeneration

**Run this only if Task 14's replay disagreement grew.**

Regenerating from the current sampler produces another net that has never seen a pause: `_episode_span` dithers around the solver's command, clips to `[qmin, qmax]`, and never turns the auger off. Fix the coverage hole first.

**Files:**
- Modify: `docs/superpowers/experiments/sample_mpc.py`
- Modify: `controller/mpc_policy_net.npz`, `controller/mpc_policy_net_fan.npz` (regenerated binaries)

- [ ] **Step 1: `jj new`**

```bash
jj new -m "wip: sampler pause episodes"
```

- [ ] **Step 2: Add pause intervals to `_episode_span`**

In `docs/superpowers/experiments/sample_mpc.py::_episode_span`, after the warm-start block and before the step loop:

```python
    # Lid-open pauses: the auger is off while the solver keeps commanding, so the
    # estimator's transport-lag states carry inputs below Q_min. Production
    # visits those states on every pause; without them here the net extrapolates
    # exactly where the NLP does not.
    pauses = []
    if rng.random() < 0.35:
        n_pause = int(rng.integers(1, 3))
        for _ in range(n_pause):
            start = int(rng.integers(8, max(9, nsteps - 8)))
            length = int(rng.integers(2, 7))  # 25 s steps -> 50-150 s
            pauses.append((start, start + length))
```

and inside the loop, replace the actuator/plant block so a paused step drives the plant with the auger off and feeds back the applied value:

```python
        paused = any(lo <= k < hi for lo, hi in pauses)
        q_app = q_exp + (rng.normal(0, dither) if rng.random() < 0.5 else 0.0)
        q_app = float(np.clip(q_app, qmin, qmax))
        auger, fan_duty = allocate(
            q_app,
            Q_min=qmin,
            Q_max=qmax,
            u_min=c.u_min,
            u_max=c.u_max,
            fan_min_pct=cfg["fan_min_pct"],
            fan_max_pct=cfg["fan_max_pct"],
            enable_fan=bool(cfg["enable_fan_input"]),
        )
        if paused:
            ratio, fan = 0.0, 0.0
            # the honest input: invert the affine allocation at ratio 0
            q_app = qmin + (0.0 - c.u_min) / (c.u_max - c.u_min) * (qmax - qmin)
        else:
            ratio = float(np.clip(auger, c.u_min, c.u_max))
            fan = fan_duty if fan_duty is not None else 100.0
        on = int(round(ratio * 25))
        for s in range(25):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
        lastQ = q_app
```

The `Up` feature logged at `k >= 4` is `lastQ` from the previous step, so pause values flow into the training features without any further change.

- [ ] **Step 3: Verify the sampler still runs**

```bash
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/sample_mpc.py --mode span -e 4 -w 4
```
Expected: it completes and prints a sample count. Confirm pauses actually fired — a 35 % per-episode chance over 4 episodes may produce none; bump `-e` until the sampler's printed sample count differs from an unpaused run, or add a temporary counter.

- [ ] **Step 4: Regenerate both artifacts**

```bash
QT_QPA_PLATFORM=offscreen uv run python tools/regenerate_mpc_net.py --mode both --episodes 500 -w <cores>
```

500 is already the default; `--mode both` covers the fan-off and fan-on nets, which are separate files via `net_path_for`. **This is the most expensive step in either plan** — 1000 episodes across two modes. Run it in the main checkout with `-w` set to the real core count, never in a subagent worktree.

The tool prints its acceptance gate on completion: the fan ablation, `|bias| <= 0.10 C` and `RMS <= 0.72 C` at a 5 s control period over 110-288 C. **The artifact does not get committed until it clears that gate.**

- [ ] **Step 5: Re-run the replay and the matrix**

```bash
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/net_vs_nlp_replay.py \
  --out docs/superpowers/experiments/_net_vs_nlp_retrained.json
QT_QPA_PLATFORM=offscreen uv run python docs/superpowers/experiments/controller_matrix.py \
  --controllers mpc --out docs/superpowers/experiments/_matrix_retrained_mpc.json -w 8
```

Disagreement must come back to at or below the pre-change baseline. If it does not, **stop and report to the user** — that is evidence against the applied-duty change itself, and it is not something to tune around.

- [ ] **Step 6: Run the MPC test suite against the new artifact**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/unit/mpc/ -q`
Expected: PASS. `test_mpc_calibration.py` checks the artifact matches the active config; a regenerated net must still satisfy it.

- [ ] **Step 7: Commit the artifact on its own**

```bash
.venv/bin/ruff format docs/superpowers/experiments/sample_mpc.py
jj describe --stdin <<'EOF'
fix(mpc): train the net policy on lid-open pauses and regenerate it

_episode_span never turned the auger off, so no training episode contained a
pause and the transport-lag states never held an input below Q_min. Production
visits those states on every lid-open interval, where the net extrapolated and
the NLP did not -- a coverage hole that predates applied-duty feedback and that
feeding the estimator the honest input made measurable.

Acceptance gate: <paste the tool's output>
Replay disagreement: <baseline -> after-change -> retrained>
GrillSim matrix: <paste the comparison>
EOF
```

---

## Completion

Before reporting this plan complete:

- [ ] `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` is green in the **main checkout**. Subagent worktrees without Chromium SKIP the `[chromium]` web tests; re-run any touched `tests/web/*.py` here.
- [ ] `.venv/bin/ruff format --check` is clean on every file this plan touched.
- [ ] The MPC decision from Task 14 is written down with its numbers, whether or not Task 15 ran.
- [ ] Plan B (`docs/superpowers/plans/2026-08-01-adaptive-smith-predictor.md`) can start.
