# Manual Output Duty Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Manual-mode auger and fan duty fields report the actuator outputs the controller actually applied.

**Architecture:** Keep the status schema and all consumers unchanged. In the shared status publisher, branch only for `Mode.MANUAL` and derive duty fields from the single hardware-output snapshot already taken for that frame; preserve existing automatic-mode semantics.

**Tech Stack:** Python 3.14, pytest characterization harness, Ruff, React dashboard smoke harness, Jujutsu.

## Global Constraints

- Manual auger ON reports `cycle_ratio == 1.0`; OFF reports `0.0`.
- Manual DC fan ON reports the snapshot's actual `pwm`; OFF reports `0`.
- Manual relay fan ON reports `100`; OFF reports `0`.
- All non-Manual mode duty semantics remain unchanged.
- No Socket.IO, React, attached-display, schema, or compatibility-alias changes.
- Fix the shared source; do not mask stale values in a consumer.

---

## File Structure

- Modify `controller/runtime/modes/base.py`: derive Manual duty fields from `get_output_status()`.
- Modify `tests/characterization/test_modes_golden.py`: cover Manual auger, DC-fan, selected-PWM, off-state, and automatic-mode contracts through real work cycles.
- No production or test files are created.

### Task 1: Characterize Manual Duty Status and Fix Its Source

**Files:**
- Modify: `tests/characterization/test_modes_golden.py:19-24,229-242,293-342`
- Modify: `controller/runtime/modes/base.py:476-521`

**Interfaces:**
- Consumes: `ControlMode._build_status_data(control: dict, pelletdb: dict, start_time: float) -> dict`, `FakeGrillPlatform.get_output_status() -> dict`, and `run_mode(...) -> CaptureResult`.
- Produces: unchanged status keys `cycle_ratio: float` and `fan_duty: int`; no new API.

- [ ] **Step 1: Add imports and a deterministic mid-cycle Manual-command probe helper**

Add these imports to `tests/characterization/test_modes_golden.py`:

```python
from common.common import WriteKind
from controller.runtime.store import InMemoryStore
```

Add this helper immediately before the Manual-mode tests:

```python
class _InjectManualPwm:
    def __init__(self, probes, store, pwm):
        self._probes = probes
        self._store = store
        self._pwm = pwm
        self._reads = 0

    def read_probes(self):
        self._reads += 1
        if self._reads == 2:
            self._store.write_control(
                {"manual": {"change": "pwm", "pwm": self._pwm}},
                WriteKind.MERGE,
                origin="test-manual-pwm",
            )
        return self._probes.read_probes()

    def __getattr__(self, name):
        return getattr(self._probes, name)
```

The first loop applies Fan ON. The helper queues PWM 55 after the second probe read; the third loop drains and applies it while the fan is on. The 0.5-second publisher then captures the resulting hardware state.

- [ ] **Step 2: Write failing Manual auger and fan status tests**

Add these tests beside the existing Manual-mode characterization tests:

```python
def test_manual_auger_on_publishes_full_duty():
    settings = base_settings()
    control_data = base_control(mode="Manual")
    control_data["manual"].update(change="auger", output=True)

    result = run_mode(
        "Manual",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([120]),
        probe_cap=15,
    )

    assert result.final_status["outpins"]["auger"] is True
    assert result.final_status["cycle_ratio"] == 1.0


def test_manual_dc_fan_on_publishes_actual_pwm_not_automatic_duty():
    settings = base_settings()
    settings["platform"]["dc_fan"] = True
    control_data = base_control(mode="Manual")
    control_data["duty_cycle"] = 0
    control_data["manual"].update(change="fan", output=True)

    result = run_mode(
        "Manual",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([120]),
        grill=FakeGrillPlatform(dc_fan=True),
        probe_cap=15,
    )

    assert result.final_status["outpins"]["fan"] is True
    assert result.final_status["outpins"]["pwm"] == 100
    assert result.final_status["fan_duty"] == 100
```

- [ ] **Step 3: Write failing selected-PWM test and boundary protections**

```python
def test_manual_dc_fan_publishes_selected_pwm():
    settings = base_settings()
    settings["platform"]["dc_fan"] = True
    control_data = base_control(mode="Manual")
    control_data["duty_cycle"] = 0
    control_data["manual"].update(change="fan", output=True)
    pellet_db = base_pellet_db()
    store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
    probes = _InjectManualPwm(FakeProbes().script([120]), store, 55)

    result = run_mode(
        "Manual",
        settings=settings,
        control_data=control_data,
        pellet_db=pellet_db,
        probes=probes,
        grill=FakeGrillPlatform(dc_fan=True),
        probe_cap=15,
        store=store,
    )

    assert result.final_status["outpins"]["fan"] is True
    assert result.final_status["outpins"]["pwm"] == 55
    assert result.final_status["fan_duty"] == 55


def test_manual_outputs_off_publish_zero_duties():
    settings = base_settings()
    settings["platform"]["dc_fan"] = True

    result = run_mode(
        "Manual",
        settings=settings,
        control_data=base_control(mode="Manual"),
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([120]),
        grill=FakeGrillPlatform(dc_fan=True),
        probe_cap=15,
    )

    assert result.final_status["outpins"]["auger"] is False
    assert result.final_status["outpins"]["fan"] is False
    assert result.final_status["cycle_ratio"] == 0.0
    assert result.final_status["fan_duty"] == 0


def test_smoke_keeps_automatic_fan_duty_semantics():
    settings = base_settings()
    settings["platform"]["dc_fan"] = True
    control_data = base_control(mode="Smoke")
    control_data["duty_cycle"] = 37

    result = run_mode(
        "Smoke",
        settings=settings,
        control_data=control_data,
        pellet_db=base_pellet_db(),
        probes=FakeProbes().script([200]),
        grill=FakeGrillPlatform(dc_fan=True),
        probe_cap=15,
    )

    assert result.final_status["fan_duty"] == 37
```

- [ ] **Step 4: Run the new tests and verify the reported bug fails**

Run:

```bash
uv run pytest \
  tests/characterization/test_modes_golden.py::test_manual_auger_on_publishes_full_duty \
  tests/characterization/test_modes_golden.py::test_manual_dc_fan_on_publishes_actual_pwm_not_automatic_duty \
  tests/characterization/test_modes_golden.py::test_manual_dc_fan_publishes_selected_pwm \
  tests/characterization/test_modes_golden.py::test_manual_outputs_off_publish_zero_duties \
  tests/characterization/test_modes_golden.py::test_smoke_keeps_automatic_fan_duty_semantics -vv
```

Expected before the fix: the auger-on assertion receives `0.0`; both DC-fan-on assertions receive the stale automatic value `0`. The off-state and Smoke boundary tests pass.

- [ ] **Step 5: Implement Manual hardware-derived duty status**

Replace the duty derivation in `ControlMode._build_status_data()` with:

```python
        if mode == Mode.MANUAL:
            status_data["cycle_ratio"] = 1.0 if current.get("auger") else 0.0
            if not current.get("fan"):
                status_data["fan_duty"] = 0
            elif self.settings["platform"].get("dc_fan"):
                status_data["fan_duty"] = int(current.get("pwm", 0) or 0)
            else:
                status_data["fan_duty"] = 100
        else:
            status_data["cycle_ratio"] = round(self.state.cycle.ratio, 2)
            if self.settings["platform"].get("dc_fan"):
                status_data["fan_duty"] = int(control.get("duty_cycle", 0) or 0)
            else:
                status_data["fan_duty"] = 100 if current.get("fan") else 0
```

Use the existing `current` snapshot directly; do not call `get_output_status()` again.

- [ ] **Step 6: Run focused characterization and formatting**

Run:

```bash
uv run pytest \
  tests/characterization/test_modes_golden.py::test_manual_auger_on_publishes_full_duty \
  tests/characterization/test_modes_golden.py::test_manual_dc_fan_on_publishes_actual_pwm_not_automatic_duty \
  tests/characterization/test_modes_golden.py::test_manual_dc_fan_publishes_selected_pwm \
  tests/characterization/test_modes_golden.py::test_manual_outputs_off_publish_zero_duties \
  tests/characterization/test_modes_golden.py::test_smoke_keeps_automatic_fan_duty_semantics -vv
uv run ruff format controller/runtime/modes/base.py tests/characterization/test_modes_golden.py
uv run ruff check controller/runtime/modes/base.py tests/characterization/test_modes_golden.py
```

Expected: five tests pass; Ruff reports success.

- [ ] **Step 7: Record the implementation revision**

Use Jujutsu:

```bash
jj desc -m "fix(control): report Manual actuator duties"
jj new
```

Expected: the implementation commit contains only the two modified Python files.

### Task 2: Verify Every Consumer and Finalize

**Files:**
- Verify only: `blueprints/mobile/socket_io.py`
- Verify only: `web-react/src/helpers/dashboard/deriveView.ts`
- Verify only: `display/qtbackend.py`

**Interfaces:**
- Consumes: corrected status keys `cycle_ratio` and `fan_duty`.
- Produces: no code changes; browser and suite evidence for the unchanged consumer path.

- [ ] **Step 1: Run the relevant mode characterization module**

Run:

```bash
uv run pytest tests/characterization/test_modes_golden.py -q
```

Expected: all tests in the module pass under the configured randomized ordering.

- [ ] **Step 2: Run a browser smoke scenario**

Build a temporary external harness under `/tmp/pifire-manual-duty-harness` that renders `Dashboard` with `currentMode: "Manual"`, `cycleRatio: 1`, `fanDuty: 55`, and `status.outputs` showing auger/fan on. Serve it locally, open it in Chromium, and assert the visible pills read `AUGER DUTY 100%` and `FAN DUTY 55%`. Remove the temporary harness afterward; do not add it to the repository.

Expected: both values render without React or CSS changes.

- [ ] **Step 3: Run the full randomized Python suite**

Run:

```bash
uv run pytest -q
```

Expected: the full suite passes with the generated random-order seed reported by pytest.

- [ ] **Step 4: Request a final code review**

Review from the implementation base through the implementation head. Check Manual-only branching, actual PWM reporting, automatic-mode preservation, coherent single-snapshot use, and test realism. Resolve every correctness finding and repeat the affected verification.

- [ ] **Step 5: Run completion verification and publish**

Run the full Python suite again after review. Then move `massive-reworks-and-new-ui` to the completed implementation commit and push it with Jujutsu:

```bash
jj bookmark move massive-reworks-and-new-ui --to @-
jj git push -b massive-reworks-and-new-ui
```

Expected: the bookmark advances to the reviewed commit and the working revision is clean.
