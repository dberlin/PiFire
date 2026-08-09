# PID-SP Opportunistic 900-Second Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permit a fresh PID-SP model to activate at 900 seconds when every existing evidence gate passes, while slow or weakly excited grills continue evaluating every accepted sample until their evidence becomes defensible.

**Architecture:** Retain the existing passive `FOPDTIdentifier`, delay banks, physical and uncertainty gates, 20-evaluation confirmation, and persisted-model first-tick behavior. Reduce only the coarse pre-confirmation count and accepted-time floors so confirmation can begin near 520 seconds at the fixed 20-second pulse cadence; the twentieth stable evaluation can then activate at 900 seconds. A confirmed current-cook candidate may directly revise a wrong restored model before distrust, while the existing distrust thresholds and drop mechanics remain the backstop when no revision confirms.

**Tech Stack:** Python 3.11+, NumPy, pytest, PiFire PID-SP controller, framed `PulseScheduler`, GrillSim, MAKGrillSim, Jujutsu.

## Global Constraints

- `MIN_ACCEPTED_SECONDS` is exactly `500.0`.
- `MIN_ACCEPTED` is exactly `25`.
- `CONFIRM_WINDOW` remains exactly `20`.
- `FOPDTIdentifier.MIN_HOLD_DUTY_SAMPLES` remains exactly `60`.
- The 15°F temperature span, duty variation, sustained transition, physics, uncertainty, residual-margin, gap, confirmation, blending, restore serialization, persistence, and distrust thresholds/drop mechanics must not change. Exact event ordering between confirmed revision and distrust is not required: either may act first, and a confirmed current-cook candidate may directly replace a wrong restored model.
- A model is never forced active at 900 seconds; 900 seconds is only the earliest activation at the production 20-second cadence.
- After eligibility, evaluation continues on every accepted observation with no later scheduled checkpoint.
- Add no setting, migration, persistence-schema change, fallback model, or deliberate excitation.

---

### Task 1: Reduce the coarse eligibility floors without weakening evidence gates

**Files:**
- Modify: `controller/fopdt_identifier.py:348-355`
- Modify: `tests/unit/controller/test_fopdt_identifier.py:356-358,391-458,697-711,790-809`
- Modify: `docs/superpowers/specs/2026-08-09-pid-sp-opportunistic-learning-design.md`
- Modify: `docs/superpowers/plans/2026-08-09-pid-sp-opportunistic-learning.md`
- Verify: `tests/unit/controller/test_pid_sp.py`
- Behavioral harness: `docs/superpowers/experiments/controller_matrix.py`

**Interfaces:**
- Consumes: `FOPDTIdentifier.observe(temperature_f: float, timestamp: float) -> bool`, `FOPDTIdentifier.status() -> dict[str, object]`, `FOPDTIdentifier.trusted_model() -> dict[str, object] | None`, and the fixed `AUGER_TIMING.frame_s == 20` cadence.
- Produces: the unchanged identifier API with initial eligibility at 25 accepted observations and 500 accepted seconds; trust still requires the existing 20 stable gated candidates.

- [ ] **Step 1: Start one isolated implementation commit**

Run before changing code:

```bash
jj new -m "feat(pid_sp): allow opportunistic model trust at 900 seconds"
jj st
```

Expected: a new empty working-copy change whose parent is the implementation-plan commit. Existing unrelated controller/runtime changes remain in its ancestors and are not modified.

- [ ] **Step 2: Update the production-cadence regression test before changing constants**

In `tests/unit/controller/test_fopdt_identifier.py`, replace `test_no_promotion_before_the_time_gate` with a test that pins both sides of the 900-second boundary:

```python
def _early_excitation_schedule(n):
    """Eighty-second duty blocks that identify the exact plant by 500 s."""
    return [0.2 if (index // 4) % 2 == 0 else 0.6 for index in range(n)]


def test_first_trust_is_900_seconds_at_the_production_cadence():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=800.0, tau=600.0, theta=20.0)
    schedule = _early_excitation_schedule(45)

    _drive(identifier, plant, schedule[:44])

    assert plant.t == 880.0
    assert identifier.status()["confirming"] == CONFIRM_WINDOW - 1
    assert identifier.trusted_model() is None

    _drive(identifier, plant, schedule[44:45])

    assert plant.t == 900.0
    assert identifier.status()["confirming"] is None
    assert identifier.trusted_model() is not None
```

This fixture uses an exact synthetic plant whose 20-second delay lies on the candidate grid and sustained 0.20/0.60 duty blocks that make its parameters identifiable when the proposed coarse gates open. The ordinary `_excitation_schedule` does not distinguish a delay by 500 seconds and must not be used to manufacture this boundary. The test observes 44 accepted regression intervals by 900 seconds because the first temperature observation establishes the regression anchor.

- [ ] **Step 3: Shorten the existing confirmation-sequence test to the new boundary**

Change `test_confirmation_requires_a_full_window_before_trust` so it stops after the first adoption rather than continuing for 260 frames and potentially observing later material revisions:

```python
def test_confirmation_requires_a_full_window_before_trust():
    """A candidate must hold still for CONFIRM_WINDOW evaluations before it is
    believed: confirming climbs 1..CONFIRM_WINDOW-1 with no trusted model, then
    the window closes and a model appears in the same step confirming clears."""
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=800.0, tau=600.0, theta=20.0)
    seen_confirming = []
    for u in _early_excitation_schedule(45):
        _drive(identifier, plant, [u])
        confirming = identifier.status()["confirming"]
        if confirming is not None:
            assert identifier.trusted_model() is None
            seen_confirming.append(confirming)
    assert seen_confirming == list(range(1, CONFIRM_WINDOW))
    assert plant.t == 900.0
    assert identifier.trusted_model() is not None
```

- [ ] **Step 4: Add a regression test for continuous post-900 evaluation**

Place this test beside the confirmation tests. It records the timestamp of every `_evaluate` call without changing the original evaluation result:

```python
def test_evaluation_continues_on_every_accepted_observation_after_900(monkeypatch):
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    evaluated_at = []
    original_evaluate = identifier._evaluate

    def recording_evaluate():
        evaluated_at.append(identifier._prev[0])
        original_evaluate()

    monkeypatch.setattr(identifier, "_evaluate", recording_evaluate)
    _drive(identifier, plant, _excitation_schedule(50))

    assert [at for at in evaluated_at if at >= 900.0] == [900.0, 920.0, 940.0, 960.0, 980.0, 1000.0]
```

This defends the accepted-sample cadence directly. It prohibits a future implementation from treating 900 seconds as a one-shot decision followed by a later scheduled retry.

- [ ] **Step 5: Update the independent time/count gate tests**

Replace the old 3,600-second/240-observation fixtures with cases that isolate each proposed floor.

For count satisfied but accepted time insufficient:

```python
def test_no_promotion_when_the_count_gate_is_satisfied_but_the_time_gate_is_not():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(dt=5.0)
    _drive(identifier, plant, _excitation_schedule(80))
    status = identifier.status()
    assert status["accepted"] >= MIN_ACCEPTED
    assert status["accepted_seconds"] < MIN_ACCEPTED_SECONDS
    assert status["candidates_passing"] > 0
    assert identifier.trusted_model() is None
```

For accepted time satisfied but count insufficient, use sustained two-sample duty blocks so the transition and variation evidence exist despite the short count:

```python
def test_no_promotion_when_the_time_gate_is_satisfied_but_the_count_gate_is_not():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(dt=60.0)
    duties = [0.25 if (index // 2) % 2 == 0 else 0.55 for index in range(20)]
    _drive(identifier, plant, duties)
    status = identifier.status()
    assert status["accepted"] < MIN_ACCEPTED
    assert status["accepted_seconds"] >= MIN_ACCEPTED_SECONDS
    assert status["candidates_passing"] > 0
    assert identifier.trusted_model() is None
```


- [ ] **Step 6: Move the restore-confirmation boundary fixture to 900 seconds**

Update `test_restore_clears_a_stale_confirmation_window` to build exactly 19 confirmation decisions under the new floors:

```python
def test_restore_clears_a_stale_confirmation_window():
    """A confirmation window accumulated against the pre-restore trusted state
    must not count toward confirming a candidate against the restored one."""
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=800.0, tau=600.0, theta=20.0)
    schedule = _early_excitation_schedule(45)
    _drive(identifier, plant, schedule[:44])
    assert identifier.status()["confirming"] == CONFIRM_WINDOW - 1
    assert identifier.trusted_model() is None
    assert identifier.restore({"K": 700.0, "tau": 900.0, "theta": 10.0, "revision": 9}) is True
    assert identifier.status()["confirming"] is None
    _drive(identifier, plant, schedule[44:45])
    assert identifier.trusted_model() == {
        "form": FORM_FOPDT,
        "K": 700.0,
        "tau": 900.0,
        "theta": 10.0,
        "revision": 9,
    }
```

- [ ] **Step 7: Update the schedule comment and run the new tests against the old constants**

Change `_excitation_schedule`'s docstring to describe the generic gates without embedding the retired values:

```python
def _excitation_schedule(n, dt=20.0):
    """Alternating sustained duty levels that clear the excitation gates."""
```

Run:

```bash
python -m pytest -q \
  tests/unit/controller/test_fopdt_identifier.py::test_first_trust_is_900_seconds_at_the_production_cadence \
  tests/unit/controller/test_fopdt_identifier.py::test_confirmation_requires_a_full_window_before_trust \
  tests/unit/controller/test_fopdt_identifier.py::test_restore_clears_a_stale_confirmation_window
```

Expected before production changes: FAIL because the current 3,600-second/240-observation floors have not started confirmation at 880 seconds and have no trusted model at 900 seconds. The continuous-evaluation test may already pass because it locks an existing behavior that this change must preserve.

- [ ] **Step 8: Change only the two production eligibility constants**

In `controller/fopdt_identifier.py`, replace the trust-gate header with:

```python
#: Initial trust can begin after 25 accepted observations spanning 500 s.
#: At PID-SP's fixed 20 s cadence, the subsequent 20-sample confirmation
#: window makes 900 s the earliest activation. Every evidence-dependent gate
#: below remains authoritative, so a slow or unexcited grill keeps learning.
MIN_ACCEPTED_SECONDS = 500.0
MIN_ACCEPTED = 25
MIN_DUTY_STD = 0.05
MIN_TRANSITION = 0.05
MIN_TRANSITION_HOLD = 60.0
MIN_TEMP_SPAN_F = 15.0
CONFIRM_WINDOW = 20
```

Do not modify `_excited`, `_evaluate`, `_confirmed`, `gate_mask`, `integrating_gate_mask`, `promote`, `MIN_HOLD_DUTY_SAMPLES`, or any restore/persistence code.

- [ ] **Step 9: Run the focused contract tests**

Run:

```bash
python -m pytest -q \
  tests/unit/controller/test_fopdt_identifier.py::test_first_trust_is_900_seconds_at_the_production_cadence \
  tests/unit/controller/test_fopdt_identifier.py::test_evaluation_continues_on_every_accepted_observation_after_900 \
  tests/unit/controller/test_fopdt_identifier.py::test_no_promotion_when_the_count_gate_is_satisfied_but_the_time_gate_is_not \
  tests/unit/controller/test_fopdt_identifier.py::test_no_promotion_when_the_time_gate_is_satisfied_but_the_count_gate_is_not \
  tests/unit/controller/test_fopdt_identifier.py::test_confirmation_requires_a_full_window_before_trust \
  tests/unit/controller/test_fopdt_identifier.py::test_restore_clears_a_stale_confirmation_window
```

Expected: all six tests PASS. The production-cadence test must report `plant.t == 900.0`; changing the test to accept a range is not permitted.

- [ ] **Step 10: Run the complete PID-SP identifier and controller unit suites**

First run the restored-model path that was affected by the lower floors:

```bash
python -m pytest -q \
  tests/unit/controller/test_fopdt_identifier.py::test_confirmed_current_cook_model_directly_replaces_wrong_restored_model
```

Expected: PASS through unmodified `observe()`/`record_output()` behavior. The 20-evaluation current-cook confirmation directly replaces the wrong restored model before distrust in this fixture, and `distrust_count` remains zero. The direct distrust threshold and drop tests remain the backstop coverage.

Then run:


```bash
python -m pytest -q \
  tests/unit/controller/test_fopdt_identifier.py \
  tests/unit/controller/test_ipdt_identification.py \
  tests/unit/controller/test_smith_predictor.py \
  tests/unit/controller/test_pid_sp.py
```

Expected: PASS with no deselected identifier, Smith-predictor, or PID-SP tests. In particular, constant-duty, insufficient-span, physics, uncertainty, residual-margin, confirmation-reset, direct restored-model revision, direct distrust backstop, persistence, and first-tick restore tests must remain green.

- [ ] **Step 11: Run the paired behavioral simulator matrix**

Run the same 60-run proposed arm used to approve the design:

```bash
uv run --no-sync python docs/superpowers/experiments/controller_matrix.py \
  --controllers pid_sp \
  --scenarios steady_225 steady_450 step_225_275 \
  --seeds 0 1 2 3 4 5 6 7 8 9 \
  --plants GrillSim MAKGrillSim \
  --out /tmp/pid-sp-opportunistic-900.json
```

Then summarize the deterministic output:

```bash
python - <<'PY'
import json
import statistics
from pathlib import Path
rows = json.loads(Path("/tmp/pid-sp-opportunistic-900.json").read_text())["rows"]
for plant in ("GrillSim", "MAKGrillSim"):
    for scenario in ("steady_225", "steady_450", "step_225_275"):
        selected = [row for row in rows if row["plant"] == plant and row["scenario"] == scenario]
        assert len(selected) == 10
        print(
            plant,
            scenario,
            f"within5={statistics.mean(row['pct_within_5f'] for row in selected):.4f}",
            f"overshoot={statistics.mean(row['overshoot_f'] for row in selected):.4f}",
            f"rmse={statistics.mean(row['rmse_f'] for row in selected):.4f}",
        )
PY
```

Expected means from the approved design experiment, allowing only floating-point display-rounding differences:

| Plant | Scenario | Within ±5°F | Overshoot °F | RMSE °F |
|---|---|---:|---:|---:|
| GrillSim | steady_225 | 54.9079% | 30.3802 | 12.8781 |
| GrillSim | steady_450 | 22.5460% | 21.4037 | 38.3778 |
| GrillSim | step_225_275 | 52.1875% | 30.3802 | 13.0127 |
| MAKGrillSim | steady_225 | 91.5897% | 24.7835 | 21.1992 |
| MAKGrillSim | steady_450 | 88.6722% | 31.4091 | 66.9281 |
| MAKGrillSim | step_225_275 | 90.2146% | 24.7835 | 20.6502 |

A meaningful mismatch requires investigation; do not update the approved values to fit unexplained output.

- [ ] **Step 12: Smoke the changed runtime path**

Run a single production-harness scenario, not a test module:

```bash
uv run --no-sync python docs/superpowers/experiments/controller_matrix.py \
  --controllers pid_sp \
  --scenarios steady_225 \
  --seeds 0 \
  --plants GrillSim \
  --out /tmp/pid-sp-900-smoke.json
```

Inspect the generated row and require a finite `pct_within_5f`, `overshoot_f`, and `rmse_f`, plus `status.identifier.trusted` not `None` at cook end. This exercises controller construction, measured-temperature intake, framed applied-output feedback, online identification, predictor trust, and final status serialization.

- [ ] **Step 13: Verify the Jujutsu change is atomic**

Run:

```bash
jj st
jj --no-pager diff --git
```

Expected changed files only:

```text
controller/fopdt_identifier.py
tests/unit/controller/test_fopdt_identifier.py
docs/superpowers/specs/2026-08-09-pid-sp-opportunistic-learning-design.md
docs/superpowers/plans/2026-08-09-pid-sp-opportunistic-learning.md
```

The working-copy description remains `feat(pid_sp): allow opportunistic model trust at 900 seconds`. Do not squash it into the design or plan commits.
