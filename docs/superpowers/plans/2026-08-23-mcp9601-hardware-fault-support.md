# MCP9601 Hardware Fault Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Playing With Fusion SEN-30010-W/MCP9601 as a PiFire thermocouple probe and safely stop combustion before positive actuation when its opt-in hardware detection confirms a primary open/short fault.

**Architecture:** Introduce one immutable thermocouple-health side channel, aggregate it beside—not inside—the existing temperature dictionaries, and share the common MCP960x I2C/temperature implementation between the existing MCP9600 and new MCP9601 modules. The MCP9601 variant decodes status register `0x04`; the controller performs a safe-off preflight before mode setup and repeats the health guard before every numeric temperature guard and actuation.

**Tech Stack:** Python 3.14, frozen/slotted dataclasses, Adafruit CircuitPython MCP9600 and register descriptors, React 19 wizard, JSON manifest, Pillow, pytest/pytest-cov, Rstest, browser verification, Jujutsu (`jj`).

**Spec:** `docs/superpowers/specs/2026-08-23-mcp9601-thermocouple-health-design.md`

## Global Constraints

- Current implementation scope is MCP9601 temperature support, opt-in hardware OC/SC detection, the shared health contract, controller safety ordering, notifications, and current wizard guidance. Do not implement the five-minute/fast-path inference engine or its `off | observe | enforce` setting in this plan.
- Use Jujutsu only. Never run raw Git commands. Each task ends with `jj desc -m`, `jj new`, and `jj st`; keep the working `@` empty between tasks.
- Follow strict TDD: add one observable failing contract, run it and confirm the named failure, add minimum behavior, then run focused tests green.
- Use LSP references before changing exported `ProbeInterface`, `ProbesMain`, `ControlMode`, or notification contracts; migrate every caller/fake in the same task. Use AST search for structural discovery and context-mode for broad output; do not use text grep.
- Preserve the public module name `mcp9600_adafruit`, port `KTT0`, default address `0x67`, saved configuration shape, filtering, units, and error behavior.
- MCP9601 defaults: port `KTT0`, I2C address `0x61`, thermocouple type `K`, structured basic I2C bus, and `hardware_fault_detection = "False"` in the manifest wire format.
- Hardware detection is trustworthy only when explicitly enabled for a board with the required VSENSE network. SEN-30010-W is verified; chip identity alone is insufficient.
- MCP9601 status register `0x04`: `0x10` means open; `0x20` means short to VDD/VSS. A direct asserted bit is confirmed without software-inference agreement.
- Confirmed readings are invalid and output `None`; never substitute cold-junction or last-known-good temperature.
- Confirmed primary faults request Mode.ERROR and shut down before positive actuation. Confirmed food/aux faults notify once, output `None`, and continue combustion control.
- A primary fault stays latched until device/cook reinitialization. Food/aux requires 60 consecutive clean seconds before recovery.
- No new runtime dependency: `adafruit-circuitpython-mcp9600`, Adafruit register support, and Pillow are already declared.
- New or substantially rewritten Python modules require greater than 90% branch coverage.
- Run project formatting only after behavior is green; never reformat unrelated files.

---

### Task 1: Immutable thermocouple health and aggregation

**Files:**
- Create: `probes/thermocouple_health.py`
- Modify: `probes/base.py:127-220, 434-439`
- Modify: `probes/main.py:23-171`
- Create: `tests/unit/probes/test_thermocouple_health.py`
- Create: `tests/unit/probes/test_probe_health_aggregation.py`

**Interfaces:**
- Produces: `ThermocoupleHealthState(StrEnum)` values `unmonitored`, `healthy`, `suspected`, `confirmed`
- Produces: `ThermocoupleFault(StrEnum)` values `open`, `short`, `malfunction`
- Produces: `ThermocoupleEvidence(StrEnum)` values `hardware`, `junction-collapse`, `stuck-response`, `excitation-response`, `implausible-step`
- Produces: `ThermocoupleHealthReport(state, faults, evidence, temperature_valid, observed_at, detail)` with `confirmed` and `as_dict()`
- Produces: `ThermocoupleHealthTransition(label, previous, current)`
- Produces: `HardwareFaultLatch.update(faults, now, primary, status=None) -> ThermocoupleHealthReport`
- Produces: `ProbeInterface.get_thermocouple_health() -> dict[str, ThermocoupleHealthReport]`, default `{}`
- Produces: `ProbesMain.get_thermocouple_health()` and `ProbesMain.consume_thermocouple_health_transitions()`
- Consumes: existing logical probe labels from `ProbeInterface.port_map`

- [ ] **Step 1: Locate every exported probe contract consumer**

Use LSP references on `ProbeInterface`, `ProbesMain.read_probes`, and `ProbesMain.get_device_info`; use AST search for fake/wrapper class definitions that delegate `read_probes`. Record every constructor/caller that must receive the new default health methods. Do not rename existing methods.

- [ ] **Step 2: Write failing immutable-report and latch tests**

Create `tests/unit/probes/test_thermocouple_health.py` with contracts like:

```python
from probes.thermocouple_health import (
    HardwareFaultLatch,
    ThermocoupleEvidence,
    ThermocoupleFault,
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
)


def test_confirmed_report_is_invalid_and_json_safe():
    report = ThermocoupleHealthReport(
        state=ThermocoupleHealthState.CONFIRMED,
        faults=(ThermocoupleFault.OPEN, ThermocoupleFault.SHORT),
        evidence=(ThermocoupleEvidence.HARDWARE,),
        temperature_valid=False,
        observed_at=12.5,
        detail={"status": 0x30},
    )
    assert report.confirmed is True
    assert report.as_dict() == {
        "state": "confirmed",
        "faults": ["open", "short"],
        "evidence": ["hardware"],
        "temperature_valid": False,
        "observed_at": 12.5,
        "detail": {"status": 0x30},
    }


def test_primary_hardware_fault_latches_across_clean_samples():
    latch = HardwareFaultLatch(recovery_seconds=60.0)
    fault = latch.update((ThermocoupleFault.OPEN,), now=10.0, primary=True)
    clean = latch.update((), now=100.0, primary=True)
    assert fault.confirmed
    assert clean.confirmed
    assert clean.faults == (ThermocoupleFault.OPEN,)


def test_secondary_hardware_fault_requires_sixty_consecutive_clean_seconds():
    latch = HardwareFaultLatch(recovery_seconds=60.0)
    latch.update((ThermocoupleFault.SHORT,), now=10.0, primary=False)
    assert latch.update((), now=70.0, primary=False).confirmed
    assert latch.update((), now=129.9, primary=False).confirmed
    recovered = latch.update((), now=130.0, primary=False)
    assert recovered.state is ThermocoupleHealthState.HEALTHY
    assert recovered.temperature_valid is True
```

Add boundary tests for a reasserted fault resetting the clean timer, both fault bits being preserved, first clean update returning healthy, and monotonic timestamps being copied into every result.

- [ ] **Step 3: Run the health tests and verify RED**

Run through context-mode:

```bash
uv run pytest -q tests/unit/probes/test_thermocouple_health.py
```

Expected: collection fails because `probes.thermocouple_health` does not exist.

- [ ] **Step 4: Implement the immutable report and hardware latch**

Create the module with this public shape:

```python
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class ThermocoupleHealthState(StrEnum):
    UNMONITORED = "unmonitored"
    HEALTHY = "healthy"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"


class ThermocoupleFault(StrEnum):
    OPEN = "open"
    SHORT = "short"
    MALFUNCTION = "malfunction"


class ThermocoupleEvidence(StrEnum):
    HARDWARE = "hardware"
    JUNCTION_COLLAPSE = "junction-collapse"
    STUCK_RESPONSE = "stuck-response"
    EXCITATION_RESPONSE = "excitation-response"
    IMPLAUSIBLE_STEP = "implausible-step"


@dataclass(frozen=True, slots=True)
class ThermocoupleHealthReport:
    state: ThermocoupleHealthState
    faults: tuple[ThermocoupleFault, ...] = ()
    evidence: tuple[ThermocoupleEvidence, ...] = ()
    temperature_valid: bool = True
    observed_at: float = 0.0
    detail: Mapping[str, object] = field(default_factory=dict)

    @property
    def confirmed(self) -> bool:
        return self.state is ThermocoupleHealthState.CONFIRMED

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "faults": [item.value for item in self.faults],
            "evidence": [item.value for item in self.evidence],
            "temperature_valid": self.temperature_valid,
            "observed_at": self.observed_at,
            "detail": dict(self.detail),
        }
```

Add `unmonitored(now)`, `healthy(now, evidence=())`, and
`confirmed_hardware(faults, now, status)` class constructors. The last
constructor sets hardware evidence, `temperature_valid=False`, and
`detail={"status": status}`. Validate the invariant that `confirmed` cannot have
`temperature_valid=True`, and non-confirmed reports cannot carry `open`/`short`.
In `__post_init__`, own `detail` with `MappingProxyType(dict(self.detail))`;
`as_dict()` returns a fresh mutable copy. Add a test that mutates both the source
dict and the returned dict and confirms the report remains unchanged.
`HardwareFaultLatch` owns only the current report and clean-since timestamp. On
a fault it calls `confirmed_hardware` and resets clean-since; on primary clean
it preserves the confirmed report; on secondary clean it starts/advances the
60-second recovery window.

- [ ] **Step 5: Write failing aggregation and transition tests**

Use tiny fake probe devices whose `read_all_ports()` returns valid existing temperature dictionaries and whose `get_thermocouple_health()` returns scripted reports. Assert:

```python
def test_read_collects_health_by_logical_label_and_emits_state_transition():
    probes = _main_with_device_health(
        {"Grill": ThermocoupleHealthReport.confirmed_hardware(
            (ThermocoupleFault.OPEN,), now=5.0, status=0x10
        )}
    )
    probes.read_probes()
    assert probes.get_thermocouple_health()["Grill"].confirmed
    changes = probes.consume_thermocouple_health_transitions()
    assert [(c.label, c.previous.state, c.current.state) for c in changes] == [
        ("Grill", ThermocoupleHealthState.UNMONITORED, ThermocoupleHealthState.CONFIRMED)
    ]
    assert probes.consume_thermocouple_health_transitions() == ()
```

Also assert that changing only `observed_at`/`detail` does not create a transition, a fault-kind change does, device-map rebuild clears stale health/events, and `ProbeInterface.get_device_info()` adds `thermocouple_health` JSON only when reports exist.

- [ ] **Step 6: Run aggregation tests and verify RED**

Run through context-mode:

```bash
uv run pytest -q \
  tests/unit/probes/test_thermocouple_health.py \
  tests/unit/probes/test_probe_health_aggregation.py
```

Expected: health model tests pass; aggregation tests fail because base/main have no health methods.

- [ ] **Step 7: Add default and aggregated health surfaces**

In `ProbeInterface`, return `{}` by default. In `get_device_info()`, copy `device.get_status()` into a dict and add:

```python
health = self.get_thermocouple_health()
if health:
    status["thermocouple_health"] = {
        label: report.as_dict() for label, report in health.items()
    }
```

In `ProbesMain`, initialize `_thermocouple_health` and `_thermocouple_health_transitions`. At the end of each `read_probes()`, collect every device report, compare `(state, faults)` with the prior report (default prior state is unmonitored), append immutable transitions, and replace the current map. Return shallow copies/tuples from getters; `consume_...` drains atomically. Clear both containers before rebuilding devices in `_setup_probe_devices()`.

- [ ] **Step 8: Run Task 1 tests green and measure branch coverage**

Run through context-mode:

```bash
uv run pytest -q \
  tests/unit/probes/test_thermocouple_health.py \
  tests/unit/probes/test_probe_health_aggregation.py \
  tests/unit/probes/test_base.py \
  tests/unit/probes/test_update_probe_map.py \
  --cov=probes.thermocouple_health --cov=probes.main --cov-branch --cov-report=term-missing
```

Expected: all pass; `probes/thermocouple_health.py` exceeds 90% branch coverage; existing probe map/base contracts remain green.

- [ ] **Step 9: Commit Task 1**

```bash
jj desc -m "Add thermocouple health contract"
jj new
jj st
```

Expected resting shape: empty `@`; Task 1 commit at `@-`.

---

### Task 2: Shared MCP960x driver without MCP9600 behavior change

**Files:**
- Create: `probes/_mcp960x_adafruit.py`
- Modify: `probes/mcp9600_adafruit.py:1-105`
- Modify: `tests/unit/probes/test_mcp9600_probe.py`

**Interfaces:**
- Consumes: Task 1 `HardwareFaultLatch` and health report types
- Produces: `MCP960xDevice(i2c_bus_addr, bus, tc_type)` with `temperature`, `ambient_temperature`, and `get_status()`
- Produces: `MCP960xProbe(ProbeInterface)` class attributes `device_class`, `default_i2c_address`, `port_name`, `supports_hardware_fault_detection`
- Produces: overridable `MCP960xProbe._read_hardware_faults() -> tuple[ThermocoupleFault, ...] | None`; `None` means unmonitored/disabled
- Preserves: public `mcp9600_adafruit.KTTDevice` and `mcp9600_adafruit.ReadProbes`

- [ ] **Step 1: Run LSP references before moving the MCP9600 classes**

Use LSP references for `KTTDevice` and `ReadProbes` in `probes/mcp9600_adafruit.py`. Confirm only the dynamic loader and focused tests consume them. Do not retain module-level aliases; define thin public subclasses.

- [ ] **Step 2: Add failing MCP9600 preservation tests**

Extend `test_mcp9600_probe.py` so the fake sensor exposes both `temperature` and `ambient_temperature`, then assert:

```python
def test_mcp9600_remains_unmonitored_and_does_not_read_status(mcp_probe):
    output = mcp_probe.read_all_ports({})
    report = mcp_probe.get_thermocouple_health()["Grill"]
    assert output["primary"]["Grill"] == 212.0
    assert report.state is ThermocoupleHealthState.UNMONITORED
    assert report.temperature_valid is True


def test_mcp9600_read_keeps_existing_units_and_port_contract(mcp_probe_celsius):
    output = mcp_probe_celsius.read_all_ports({})
    assert mcp_probe_celsius.device_info["ports"] == ["KTT0"]
    assert output["primary"]["Grill"] == 100.0
    assert output["tr"]["Grill"] == 0
```

Keep the existing address, thermocouple type, structured FT232H bus, manifest, and constructor tests.

- [ ] **Step 3: Run MCP9600 tests as the baseline**

Run through context-mode:

```bash
uv run pytest -q tests/unit/probes/test_mcp9600_probe.py
```

Expected before refactor: existing tests pass and new health tests fail because MCP9600 exposes no health surface.

- [ ] **Step 4: Extract the private common implementation**

Move common construction/read behavior into `_mcp960x_adafruit.py`:

```python
class MCP960xDevice:
    sensor_class = MCP9600

    def __init__(self, i2c_bus_addr=0x67, bus=None, tc_type="K"):
        self.status = {}
        self.i2c = open_i2c_bus(bus or BasicBus())
        self.sensor = self.sensor_class(self.i2c, address=i2c_bus_addr, tctype=tc_type)

    @property
    def temperature(self):
        return self.sensor.temperature

    @property
    def ambient_temperature(self):
        return self.sensor.ambient_temperature

    def get_status(self):
        return self.status
```

`MCP960xProbe._init_device()` parses the same config keys as today, constructs `self.device_class`, creates a `HardwareFaultLatch`, and initializes an unmonitored report. `read_all_ports()` reads hardware faults first through the hook, updates health, then reads temperature only when valid. Preserve the existing Fahrenheit/Celsius rounding and group placement exactly; let central `apply_filters()` continue to handle `None`.

Public MCP9600 remains real classes, not aliases:

```python
class KTTDevice(MCP960xDevice):
    pass


class ReadProbes(MCP960xProbe):
    device_class = KTTDevice
    default_i2c_address = 0x67
```

Update tests to patch `probes._mcp960x_adafruit.open_i2c_bus`/sensor factory rather than leaving compatibility aliases in the public module.

- [ ] **Step 5: Run MCP9600 and shared-driver tests green**

Run through context-mode:

```bash
uv run pytest -q \
  tests/unit/probes/test_mcp9600_probe.py \
  tests/unit/probes/test_thermocouple_health.py \
  tests/unit/probes/test_base.py
```

Expected: all pass; no status-register access occurs for MCP9600; existing manifest/default behavior is unchanged.

- [ ] **Step 6: Commit Task 2**

```bash
jj desc -m "Share MCP960x probe implementation"
jj new
jj st
```

Expected resting shape: empty `@`; Task 2 commit at `@-`.

---

### Task 3: MCP9601 module, hardware fault state, and wizard surface

**Files:**
- Create: `probes/mcp9601_adafruit.py`
- Modify: `wizard/wizard_manifest.json` under `modules.probes`
- Create: `static/img/wizard/mcp9601.png`
- Create: `tests/unit/probes/test_mcp9601_probe.py`
- Modify: `web-react/tests/unit/components/wizard/probes/DeviceForm.test.tsx`
- Modify: `web-react/tests/e2e/fixtures/probe-modules.json` only if the focused wizard browser scenario uses that fixture

**Interfaces:**
- Consumes: Task 2 `MCP960xDevice`/`MCP960xProbe`
- Produces: local `MCP9601Sensor(MCP9600)` with one `ROUnaryStruct(0x04, ">B")` status read
- Produces: public `mcp9601_adafruit.KTTDevice` and `ReadProbes`
- Produces: manifest field `hardware_fault_detection` using list-string values `"False" | "True"`
- Produces: wizard module note identifying SEN-30010-W as verified and warning against enabling detection on unverified VSENSE wiring

- [ ] **Step 1: Write failing MCP9601 constructor/manifest tests**

Build fakes before importing the module. The fake Adafruit base records address/type/temperature; the fake `ROUnaryStruct` descriptor returns a mutable fake status byte. Add contracts:

```python
def test_mcp9601_defaults_to_sen_30010_address_and_detection_off(probe):
    obj = _new_read_probes(probe, config={})
    obj._init_device()
    assert obj.device.sensor.address == 0x61
    assert obj.device.sensor.tctype == "K"
    assert obj.hardware_fault_detection is False


def test_manifest_exposes_opt_in_hardware_detection():
    entry = _probe_manifest()["mcp9601_adafruit"]
    config = {item["label"]: item for item in entry["device_specific"]["config"]}
    assert entry["device_specific"]["ports"] == ["KTT0"]
    assert config["i2c_bus_addr"]["default"] == "0x61"
    assert config["hardware_fault_detection"]["list_values"] == ["False", "True"]
    assert config["hardware_fault_detection"]["default"] == "False"
    assert "SEN-30010-W" in entry["notes"]
    assert (REPO_ROOT / "static/img/wizard" / entry["image"]).is_file()
```

Run the exact tests. Expected: import/manifest failures because the module and entry do not exist.

- [ ] **Step 2: Add failing status-order, validity, and recovery tests**

Use a fake sensor that logs property access. Cover disabled, clean, open, short, both, primary latch, secondary recovery, and exceptions:

```python
def test_enabled_open_fault_is_read_before_temperature_and_invalidates_output(probe):
    obj = _configured_probe(probe, primary=True, detection="True", status=0x10, temp_c=250.0)
    output = obj.read_all_ports({})
    report = obj.get_thermocouple_health()["Grill"]
    assert obj.device.sensor.accesses == ["status"]
    assert output["primary"]["Grill"] is None
    assert report.faults == (ThermocoupleFault.OPEN,)
    assert report.confirmed


def test_detection_off_never_reads_status(probe):
    obj = _configured_probe(probe, primary=True, detection="False", status=0x30, temp_c=100.0)
    output = obj.read_all_ports({})
    assert obj.device.sensor.accesses == ["temperature"]
    assert output["primary"]["Grill"] == 212.0
    assert obj.get_thermocouple_health()["Grill"].state is ThermocoupleHealthState.UNMONITORED
```

For recovery, monkeypatch `probes._mcp960x_adafruit.time.monotonic` to `10.0`, `69.9`, and `70.0`; assert a food probe remains `None` until the exact 60-second boundary. Assert a primary stays invalid after any clean duration. Assert a status-read exception propagates unchanged and does not become `open`/`short`.

- [ ] **Step 3: Implement MCP9601 status decoding**

Use one descriptor read per sample:

```python
from adafruit_mcp9600 import MCP9600
from adafruit_register.i2c_struct import ROUnaryStruct


class MCP9601Sensor(MCP9600):
    status_register = ROUnaryStruct(0x04, ">B")


class KTTDevice(MCP960xDevice):
    sensor_class = MCP9601Sensor

    @property
    def fault_status(self) -> int:
        return int(self.sensor.status_register)


class ReadProbes(MCP960xProbe):
    device_class = KTTDevice
    default_i2c_address = 0x61
    supports_hardware_fault_detection = True

    def _read_hardware_faults(self):
        if not self.hardware_fault_detection:
            return None
        status = self.device.fault_status
        faults = []
        if status & 0x10:
            faults.append(ThermocoupleFault.OPEN)
        if status & 0x20:
            faults.append(ThermocoupleFault.SHORT)
        self._last_hardware_status = status
        return tuple(faults)
```

The common driver passes `_last_hardware_status` to
`HardwareFaultLatch.update(..., status=...)`, so the report includes the exact
status byte without rereading the register. It never reads hot temperature
while a report is invalid.

- [ ] **Step 4: Add the manifest entry and product asset**

Use the existing manifest shape and string-list boolean convention:

```json
{
  "label": "hardware_fault_detection",
  "friendly_name": "Hardware Thermocouple Fault Detection",
  "description": "Enable only when the installed MCP9601 board includes the required VSENSE open/short detection network. SEN-30010-W is verified.",
  "type": "list",
  "list_values": ["False", "True"],
  "list_labels": ["Disabled", "Enabled — board has VSENSE detection"],
  "default": "False",
  "hidden": false
}
```

Exact module note:

> Hardware fault detection is disabled by default. A disconnected or electrically shorted/collapsed thermocouple can read as ambient temperature instead of reporting a fault. Enable hardware detection only when the board includes the required MCP9601 VSENSE network; SEN-30010-W is verified.

Use `friendly_name = "MCP9601 Thermocouple Amplifier (SEN-30010-W)"`, `filename = "mcp9601_adafruit"`, `image = "mcp9601.png"`, the existing MCP9600 Python dependencies, thermocouple types B/E/J/K/N/R/S/T, I2C addresses 0x60 through 0x67, structured `i2c_bus`, and existing `transient` field.

Generate a 128×128 transparent PNG from the vendor's product image, preserving aspect ratio and centering rather than stretching:

```bash
uv run python -c 'from io import BytesIO; from urllib.request import urlopen; from PIL import Image; src=Image.open(BytesIO(urlopen("https://www.playingwithfusion.com/img/1459_400_300.webp").read())).convert("RGBA"); src.thumbnail((128,128), Image.Resampling.LANCZOS); out=Image.new("RGBA",(128,128)); out.alpha_composite(src,((128-src.width)//2,(128-src.height)//2)); out.save("static/img/wizard/mcp9601.png")'
```

- [ ] **Step 5: Pin the existing wizard note presentation**

Extend `DeviceForm.test.tsx` with a module containing the exact `notes` text and assert it renders in `.pf-module-notes`. No `DeviceForm` source change is expected because line 32 already implements the existing warning presentation. If the test fails, fix only the manifest-to-contract projection dropping `notes`; do not add an MCP9601-specific React branch.

Run from `web-react/`:

```bash
bun run test tests/unit/components/wizard/probes/DeviceForm.test.tsx
```

Expected: pass after fixture/test data includes notes.

- [ ] **Step 6: Run Task 3 tests and coverage green**

Run through context-mode:

```bash
uv run pytest -q \
  tests/unit/probes/test_mcp9601_probe.py \
  tests/unit/probes/test_mcp9600_probe.py \
  tests/unit/probes/test_thermocouple_health.py \
  --cov=probes.mcp9601_adafruit --cov=probes._mcp960x_adafruit \
  --cov-branch --cov-report=term-missing
```

Expected: all pass; MCP9601/shared new branches exceed 90%; MCP9600 regressions remain green.

- [ ] **Step 7: Commit Task 3**

```bash
jj desc -m "Add MCP9601 hardware fault probe"
jj new
jj st
```

Expected resting shape: empty `@`; Task 3 commit at `@-`.

---

### Task 4: Primary preflight, per-tick safety, and fault notifications

**Files:**
- Modify: `controller/runtime/modes/base.py:14-25, 646-897`
- Modify: `notify/notifications.py:219-307`
- Modify: `tests/fakes/probes.py`
- Modify: `tests/unit/runtime/test_control_mode_base.py`
- Modify: `tests/unit/notify/test_notifications_events.py`
- Modify: `tests/characterization/test_modes_golden.py` only for observable safety-order coverage not expressible in the focused unit harness

**Interfaces:**
- Consumes: Task 1 `get_thermocouple_health()` and `consume_thermocouple_health_transitions()`
- Produces: `ControlMode._process_thermocouple_health(sensor_data) -> bool`
- Produces notification keys `Thermocouple_Fault_Primary` and `Thermocouple_Fault_Secondary`
- Preserves: existing `request_transition()` signature and transition graph; no second mode-write path

- [ ] **Step 1: Use LSP before changing runtime/fake contracts**

Run LSP references for `ControlMode.run`, `_on_safety_event`, and notification `EVENTS`; use AST search to locate fake probe classes implementing `read_probes`. Confirm every fake used by runtime tests gains empty health methods so existing modes remain behavior-identical.

- [ ] **Step 2: Write failing notification event tests**

Add table rows to `test_notifications_events.py`:

```python
pytest.param(
    "Thermocouple_Fault_Primary",
    "Primary Thermocouple Fault!",
    "Primary thermocouple fault detected. PiFire is shutting down because the control temperature is unavailable.",
    True,
    "pifire_error_alerts",
    {"value1": "Primary thermocouple fault"},
),
pytest.param(
    "Thermocouple_Fault_Secondary",
    "Thermocouple Fault!",
    "A food or auxiliary thermocouple fault was detected. The affected probe is unavailable; grill control continues.",
    True,
    "pifire_error_alerts",
    {"value1": "Secondary thermocouple fault"},
),
```

Follow the file's existing `now` suffix/exactness convention rather than changing its harness. Run the two node IDs through context-mode. Expected: unknown-event output because the builders/keys are absent.

- [ ] **Step 3: Add notification builders and exact-key mappings**

Add `_evt_thermocouple_fault_primary(ctx)` and `_evt_thermocouple_fault_secondary(ctx)` returning the exact titles/bodies/channel/query args above, then register both exact keys in `EVENTS`. Do not overload `Grill_Error_01/02`; those bodies describe unrelated max-temperature/flameout conditions.

- [ ] **Step 4: Write failing preflight and in-loop safety tests**

Extend `FakeProbes` with empty current health and a drainable transition list. Provide test helpers to script report changes without affecting its existing temperature script. Add contracts:

```python
def test_confirmed_primary_fault_preflight_skips_mode_setup_and_positive_actuation():
    ctx = _make_ctx_with_health(
        temperature=None,
        report=_confirmed("Grill", ThermocoupleFault.OPEN),
    )
    mode = _RecordingMode(ctx, WorkCycleState())
    mode.run()
    assert ctx.store.read_control()["mode"] == "Error"
    assert "setup" not in mode.calls
    assert "on_tick" not in mode.calls
    assert ctx.notifications.sent == ["Thermocouple_Fault_Primary"]
    assert not _positive_actuator_calls(ctx.devices.grill_platform.calls)


def test_confirmed_primary_fault_on_tick_breaks_before_numeric_guards_and_actuation():
    ctx = _make_ctx_with_health_sequence(
        temperatures=[225.0, None],
        reports=[_healthy("Grill"), _confirmed("Grill", ThermocoupleFault.SHORT)],
    )
    mode = _RecordingMode(ctx, WorkCycleState())
    mode.run()
    assert ctx.store.read_control()["mode"] == "Error"
    assert mode.calls.count("on_tick") == 0
    assert ctx.notifications.sent == ["Thermocouple_Fault_Primary"]
```

Add food/aux coverage: a transition to confirmed sends `Thermocouple_Fault_Secondary` once, its value is `None`, primary remains numeric, and `on_tick` still runs. Add a repeated confirmed sample proving no duplicate notification. Add a post-setup initial-read fault proving `setup_safety(None)` and `evaluate_phase(..., None)` are skipped.

- [ ] **Step 5: Run runtime tests and verify RED**

Run through context-mode:

```bash
uv run pytest -q \
  tests/unit/runtime/test_control_mode_base.py \
  tests/unit/notify/test_notifications_events.py
```

Expected: notification RED from missing events and runtime RED because `ControlMode` ignores health and calls setup/numeric paths.

- [ ] **Step 6: Implement one health-processing method**

Add a private method with this behavior:

```python
def _process_thermocouple_health(self, sensor_data) -> bool:
    reports = self.probe_complex.get_thermocouple_health()
    transitions = self.probe_complex.consume_thermocouple_health_transitions()
    primary_label = next(iter(sensor_data["primary"]), None)
    primary_notified = False
    for transition in transitions:
        if not transition.current.confirmed:
            continue
        if transition.label == primary_label:
            self.ctx.notifications.send("Thermocouple_Fault_Primary")
            primary_notified = True
        else:
            self.ctx.notifications.send("Thermocouple_Fault_Secondary")
    primary = reports.get(primary_label)
    if primary is None or not primary.confirmed:
        return False
    if not primary_notified:
        self.ctx.notifications.send("Thermocouple_Fault_Primary")
    request_transition(
        self.ctx,
        self.control,
        Mode.ERROR,
        kind=TransitionKind.SAFETY,
        display=("text", "ERROR"),
    )
    return True
```

There is still one mode-transition writer: `request_transition`. The fallback notification handles a confirmed report even if a wrapper consumed or omitted its transition; the method returns immediately into a break/return, so it cannot repeat within that mode run.

- [ ] **Step 7: Insert the safe-off preflight before mode setup**

Immediately after shared `igniter_off()`/`auger_off()` and before `self.setup()`:

```python
preflight_data = probe_complex.read_probes()
ctx.store.write_generic_key("probe_device_info", probe_complex.get_device_info())
if self._process_thermocouple_health(preflight_data):
    grill_platform.fan_off()
    grill_platform.power_off()
    monitor.stop_monitor()
    self.ctx.event_log.error("Primary thermocouple fault blocked mode setup.")
    return ()
```

Do not call mode teardown or metrics finalization on this branch: mode setup/stamping never ran, so setup-owned Hold state and the metrics row do not exist. Only safe-off outputs, the authoritative Error transition, device status, notification, monitor stop, and return occur.

Keep the existing post-setup initial read. After it, write current device info and call the same health method before `setup_safety` or pre-loop guards:

```python
if self._process_thermocouple_health(sensor_data):
    self._on_safety_event("thermocouple_fault", ctx.clock.now())
    status = "Inactive"
else:
    status = self.setup_safety(ptemp)
```

Gate `evaluate_phase(..., "pre_loop", ...)` on `status == "Active"` so `None` cannot reach flameout logic.

- [ ] **Step 8: Insert current-status health processing into the tick**

Move `write_generic_key("probe_device_info", ...)` from before `read_probes()` to immediately after the fresh read. Preserve current/history writes, then call `_process_thermocouple_health(sensor_data)` before existing max-temperature/flameout guards. On confirmed primary, call `_on_safety_event("thermocouple_fault", now)` and break. Food/aux confirmation returns false, so existing primary control continues.

Update the `ControlMode` module/class ordering comments to include health between sense and numeric safety. Do not alter existing temperature guard priority relative to each other.

- [ ] **Step 9: Run focused runtime, notification, and characterization tests green**

Run through context-mode:

```bash
uv run pytest -q \
  tests/unit/runtime/test_control_mode_base.py \
  tests/unit/runtime/test_guard_engine.py \
  tests/unit/runtime/test_request_transition.py \
  tests/unit/notify/test_notifications_events.py \
  tests/characterization/test_modes_golden.py \
  tests/characterization/test_mode_transitions.py
```

Expected: all pass; preflight fault has no positive actuator calls; in-loop primary fault never reaches numeric guards/on_tick; food/aux continues and notifies once; all old golden mode transitions stay unchanged when health is empty.

- [ ] **Step 10: Commit Task 4**

```bash
jj desc -m "Stop control on confirmed primary thermocouple faults"
jj new
jj st
```

Expected resting shape: empty `@`; Task 4 commit at `@-`.

---

## Final verification

- [ ] **Step 1: Run LSP diagnostics on every changed Python and TypeScript file**

Use LSP diagnostics for:

```text
probes/thermocouple_health.py
probes/base.py
probes/main.py
probes/_mcp960x_adafruit.py
probes/mcp9600_adafruit.py
probes/mcp9601_adafruit.py
controller/runtime/modes/base.py
notify/notifications.py
web-react/tests/unit/components/wizard/probes/DeviceForm.test.tsx
```

Expected: no new diagnostics. For every exported symbol changed during implementation, rerun LSP references and confirm all callsites/fakes are migrated.

- [ ] **Step 2: Format and statically check only after behavior is green**

Run via context-mode so output is filtered to actionable diagnostics:

```bash
uv run ruff format \
  probes/thermocouple_health.py probes/base.py probes/main.py \
  probes/_mcp960x_adafruit.py probes/mcp9600_adafruit.py probes/mcp9601_adafruit.py \
  controller/runtime/modes/base.py notify/notifications.py \
  tests/unit/probes/test_thermocouple_health.py \
  tests/unit/probes/test_probe_health_aggregation.py \
  tests/unit/probes/test_mcp9600_probe.py tests/unit/probes/test_mcp9601_probe.py \
  tests/unit/runtime/test_control_mode_base.py tests/unit/notify/test_notifications_events.py
uv run ruff check \
  probes/thermocouple_health.py probes/base.py probes/main.py \
  probes/_mcp960x_adafruit.py probes/mcp9600_adafruit.py probes/mcp9601_adafruit.py \
  controller/runtime/modes/base.py notify/notifications.py \
  tests/unit/probes/test_thermocouple_health.py \
  tests/unit/probes/test_probe_health_aggregation.py \
  tests/unit/probes/test_mcp9600_probe.py tests/unit/probes/test_mcp9601_probe.py \
  tests/unit/runtime/test_control_mode_base.py tests/unit/notify/test_notifications_events.py
```

From `web-react/`:

```bash
bunx biome check tests/unit/components/wizard/probes/DeviceForm.test.tsx
bun run typecheck
```

Expected: clean. Re-run affected tests if either formatter changes content.

- [ ] **Step 3: Run the complete focused behavioral suite and branch gate**

Run through context-mode:

```bash
uv run pytest -q \
  tests/unit/probes \
  tests/unit/runtime/test_control_mode_base.py \
  tests/unit/runtime/test_guard_engine.py \
  tests/unit/runtime/test_request_transition.py \
  tests/unit/notify/test_notifications_events.py \
  tests/characterization/test_modes_golden.py \
  tests/characterization/test_mode_transitions.py \
  --cov=probes.thermocouple_health \
  --cov=probes._mcp960x_adafruit \
  --cov=probes.mcp9601_adafruit \
  --cov=controller.runtime.modes.base \
  --cov-branch --cov-report=term-missing
```

From `web-react/`:

```bash
bun run test tests/unit/components/wizard/probes/DeviceForm.test.tsx
bun run build
```

Expected: all pass; each new/substantially rewritten module exceeds 90% branch coverage; frontend typecheck/build succeeds.

- [ ] **Step 4: Browser-drive the actual wizard surface**

Start the real Rsbuild dev server through the harness process manager using `bun run dev` in `web-react/`, respecting `ports.ts` instead of hard-coding a new port. Open the wizard with the browser tool, navigate to Probes, add `MCP9601 Thermocouple Amplifier (SEN-30010-W)`, and verify visually and from accessible labels:

```text
product image renders
I2C address defaults to 0x61
thermocouple type defaults to K
hardware detection defaults to Disabled
structured I2C bus selector renders
warning note names ambient-temperature failure and SEN-30010-W/VSENSE requirement
```

Change hardware detection to Enabled and save the draft; reopen the device and verify the value persists. This is UI smoke evidence, not a substitute for the manifest/unit contracts.

- [ ] **Step 5: Optional real-board smoke when SEN-30010-W is attached**

Do not block software completion when hardware is unavailable. When available: configure address `0x61`, enable hardware detection, verify a clean thermocouple temperature, disconnect the thermocouple and observe status `0x10`/invalid output, then perform only a vendor-approved current-limited short-to-rail test for `0x20`. Never create a VDD-to-GND supply short.

- [ ] **Step 6: Verify final Jujutsu shape**

```bash
jj --no-pager st
jj --no-pager log -r '@ | @-' --no-graph -T 'commit_id.short() ++ "  " ++ bookmarks ++ "  " ++ description.first_line() ++ "\n"'
```

Expected: empty, undescribed `@` above the last implementation commit at `@-`. Do not move or push a bookmark until the user chooses integration.
