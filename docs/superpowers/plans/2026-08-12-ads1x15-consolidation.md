# Adafruit ADS1x15 Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace cloned ADS1015/ADS1115 Adafruit probe logic with one shared implementation while retaining both persisted convention-loaded module names.

**Architecture:** A private module owns shared device reads and probe initialization. Each public adapter imports its chip module, subclasses the shared device with the chip factory/channel constants, and keeps a module-local `ReadProbes._init_device` call so existing monkeypatch and dynamic-loader seams remain intact.

**Tech Stack:** Python 3.14, Adafruit CircuitPython ADS1x15, pytest, Ruff, Pyright/LSP, Jujutsu.

## Global Constraints

- Preserve public modules `probes.ads1015_adafruit` and `probes.ads1115_adafruit`, classes `ADSDevice` and `ReadProbes`, four port names, 8 ms delay, voltage flooring, error-to-zero behavior, and log text chip identity.
- Preserve process-wide cached I²C bus ownership; do not add `close()` to these adapters.
- Preserve optional import isolation: importing one public module must not require the other chip submodule.
- Preserve module-local `ADSDevice` lookup from `ReadProbes._init_device` so tests and integrations can replace it.

---

### Task 1: Strengthen the Shared Adapter Contract

**Files:**
- Modify: `tests/unit/probes/test_ads1115_probes.py:241-416`

**Interfaces:**
- Produces one parameterized contract for both public modules.

- [ ] **Step 1: Use LSP symbols/references on both `ADSDevice` and `ReadProbes` classes**

Confirm static references are test-only and production loading is convention-based through the wizard manifest/device loader.

- [ ] **Step 2: Add missing behavior tests**

For both modules assert:

- importing only the selected Adafruit chip module works;
- `ADSDevice` calls `open_i2c_bus` once with the parsed bus;
- address and P0–P3 mapping are exact;
- positive voltage is floored to integer millivolts;
- read exception logs the correct port and returns zero;
- default and explicit structured buses reach the shared factory unchanged;
- `_init_device` uses the module-local monkeypatched `ADSDevice`;
- no close is attempted on the shared bus.

- [ ] **Step 3: Run and record the baseline**

Run: `uv run pytest -q tests/unit/probes/test_ads1115_probes.py`. Expected: existing tests pass; new tests pass against clones or expose only missing characterization.

- [ ] **Step 4: Commit tests**

Describe: `test(probes): pin shared ADS1x15 behavior`.

---

### Task 2: Extract the Shared Device and Initialization Logic

**Files:**
- Create: `probes/_ads1x15_adafruit.py`
- Modify: `probes/ads1015_adafruit.py`
- Modify: `probes/ads1115_adafruit.py`

**Interfaces:**
- Produces:

```python
class AdafruitADSDevice:
    CHIP_FACTORY: ClassVar[Callable[..., object]]
    CHANNELS: ClassVar[Mapping[str, object]]

    def __init__(self, i2c_bus_addr=0x48, bus=None): ...
    def read_voltage(self, port): ...
    def get_status(self): ...


def initialize_ads_probe(owner, device_class, chip_name: str) -> None: ...
```

`initialize_ads_probe` sets delay/ports, parses address/bus, constructs `device_class`, and logs/reraises initialization failure using `chip_name`.

- [ ] **Step 1: Implement the private device base**

It calls `open_i2c_bus(bus or BasicBus())`, constructs `CHIP_FACTORY(i2c, address=...)`, and reads `AnalogIn(self.ads, self.CHANNELS[port]).voltage` exactly once.

- [ ] **Step 2: Make each public `ADSDevice` a selector**

Example shape:

```python
class ADSDevice(AdafruitADSDevice):
    CHIP_FACTORY = ADS.ADS1115
    CHANNELS = {"ADC0": ADS.P0, "ADC1": ADS.P1, "ADC2": ADS.P2, "ADC3": ADS.P3}
```

The ADS1015 module uses only its own imported constants.

- [ ] **Step 3: Keep public `ReadProbes` classes thin and monkeypatchable**

```python
def _init_device(self):
    initialize_ads_probe(self, ADSDevice, "ADS1115")
```

Do not capture `ADSDevice` in a factory closure.

- [ ] **Step 4: Delete cloned implementation and banner noise**

Keep concise module docstrings describing the stable plugin name. Do not rename either file.

- [ ] **Step 5: Run focused tests and diagnostics**

Run Task 1 tests, Ruff on the three modules, and Pyright diagnostics. Expected: PASS/no introduced diagnostics.

- [ ] **Step 6: Commit**

Describe: `refactor(probes): share Adafruit ADS1x15 logic`.

---

### Task 3: Verify Dynamic Loading and Settings Compatibility

**Files:**
- Modify only if needed: `tests/web/test_api_wizard.py`
- Modify only if needed: settings migration tests naming `ads1115_adafruit`

- [ ] **Step 1: Add/import dynamic-loader smoke cases**

Load both module names from representative wizard device records and instantiate `ReadProbes` with fake chip dependencies. Assert the public module/class names remain exact.

- [ ] **Step 2: Run focused compatibility suites**

```bash
uv run pytest -q \
  tests/unit/probes/test_ads1115_probes.py \
  tests/unit/common/test_settings_migration_i2c.py \
  tests/unit/common/test_settings_migration_matrix.py \
  tests/web/test_api_wizard.py
```

Expected: PASS.

- [ ] **Step 3: Inspect LSP references and manifest diff**

No wizard key or filename changes. No import from ADS1015 module appears in ADS1115 adapter or vice versa.

- [ ] **Step 4: Commit any compatibility-test additions**

Describe: `test(probes): verify ADS1x15 plugin compatibility`.
