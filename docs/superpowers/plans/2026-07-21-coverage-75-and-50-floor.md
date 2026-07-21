# Coverage: 75% Overall + ≥50% Per-Module Floor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise overall combined line+branch coverage from 66.7% to ≥75%, and lift every non-excluded module to ≥50%, by adding targeted unit tests one module at a time.

**Architecture:** Pure per-module test addition — no production refactoring. Each module gets a dedicated `tests/unit/**` (or `tests/ui/**` for display drivers) file that imports the module behind hardware mocks and exercises its real logic branches. Hardware libraries absent from the dev/CI env (`spidev`, `luma`, `bluepy`, `RPi`, Qt, ST7789) are stubbed via `sys.modules` overlays, exactly as the existing probe and display-driver tests already do. Latent bugs surfaced by new tests are pinned (test asserts current behavior with a docstring), then fixed only per the escalation rule below.

**Tech Stack:** Python 3.14, pytest, pytest-cov (branch=true, concurrency=thread), uv, ruff.

## Global Constraints

- **Test runner (always):** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <target>`. Bare `python`/`pytest` gives false failures (venv-only PySide6/deps).
- **Coverage measurement (always):** add `--cov --cov-report=term-missing`. `concurrency=["thread"]` is already set in `pyproject.toml` and is REQUIRED — without it web/threaded code reads 0%.
- **ruff format before every commit:** `uvx ruff format <changed files>` (repo standing rule; pre-commit hook also enforces it).
- **Commit messages:** use `git commit -F <file>` (zsh eats backticks in `-m`). End every commit body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Neutralize destructive calls before import:** any module that may call `os.system`/`subprocess`/`sudo`/`reboot`/`shutdown` must have those neutralized via `monkeypatch`/`mock.patch.object` on the BOUND module attribute (e.g. `mock.patch.object(mod, "os")` or patch `threading.Thread` to a no-op) BEFORE the module's `__init__`/loop runs. Moving code out of a patched module silently disarms the mock — patch the name the module actually calls. There is a real history of unmocked display/admin `os.system` triggering actual reboots.
- **Pinning + latent-bug rule:** if a new test reveals behavior that looks like a bug, the test PINS current behavior (asserts what the code does today, with a docstring saying so). Report the bug in the task report. Fix it in this plan ONLY if the correct behavior is unambiguous and low-risk (e.g. a `==`-for-`=` typo with no external contract); the fix flips the pin (RED-before → GREEN-after) in the same commit. Anything touching real-hardware behavior or an ambiguous contract is reported and left pinned for human sign-off.
- **Coverage target per module:** each task states its module's target. The floor is ≥50% combined (line+branch); prefer to go materially past it where cheap so the overall 75% target has margin.
- **Excluded from the floor:** `probes/bt_meater.py`, `probes/bt_meater_exp.py`, `probes/bt_ibbq.py` (BLE-only, deferred — see Task 1). No other module may be added to the omit list without human sign-off.

---

## Coverage math / trajectory

Baseline (this branch, full suite): **17,131 / 25,698 covered measures = 66.7%**. 32 modules below 50%.

- **Task 1 (omit bt_\*):** removes 958 always-0 measures from the denominator → **≈69.2%** overall with zero new tests.
- After Task 1 the remaining sub-50 work is ~1,866 measures to reach every floor. Landing that puts overall at ≈ (17,131 + 1,866) / 24,740 = **≈76.8%**, clearing 75% with margin.
- The ≥50% floor is therefore the binding constraint; 75% overall is a side effect of satisfying it.

## File Structure

New test files (one responsibility each — the module under test):

- `tests/unit/probes/test_virtual_probes.py` — the 4 virtual aggregators + `disabled` + `prototype`
- `tests/unit/controller/test_fuzzy.py`, `tests/unit/controller/test_ml.py`, `tests/unit/controller/test_update_ml.py`
- `tests/unit/runtime/test_system_commands.py`
- `tests/unit/probes/test_max31865_probe.py`, `test_ds18b20_probe.py`, `test_ads1115_probes.py`
- `tests/unit/platform/test_numato_usbrelay.py`, `test_raspberry_pi_all.py`
- `tests/ui/test_flexobject_coverage.py` and `tests/ui/test_fixed_drivers_methods.py`, `tests/ui/test_pygame_qt_drivers.py`

Modified (config only): `pyproject.toml` (omit list).

Production code is modified only where the latent-bug rule fires; each such change is called out in its task.

---

## Shared Harnesses (referenced by tasks — full code here, DRY)

### Harness A — fake-library-then-reload (SPI/I2C/ADC probes)

Copied pattern from `tests/unit/probes/test_max31856_probe.py`. Install a fake hardware module into `sys.modules`, reload the probe module so its top-level `import spidev`/`import board` binds the fake, then build the probe with `ReadProbes.__new__(...)` to bypass the heavy base `__init__` and call the target method directly.

```python
import sys, types, importlib
import pytest

def install_fake(name, **attrs):
    fake = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(fake, k, v)
    return fake

def load_probe(monkeypatch, module_path, fakes):
    for name, mod in fakes.items():
        monkeypatch.setitem(sys.modules, name, mod)
    probe = importlib.import_module(module_path)
    importlib.reload(probe)   # rebind top-level hardware imports to the fakes
    return probe

def bare_readprobes(probe, device_info):
    obj = probe.ReadProbes.__new__(probe.ReadProbes)  # skip hardware __init__
    obj.device_info = device_info
    return obj
```

### Harness B — sys.modules overlay for display drivers

Copied pattern from `tests/ui/test_fixed_base_drivers_load.py`. Key rules from that file's docstring, which MUST be followed:

1. `import display.base_fixed` (or `display.base_flex`) **for real, before** installing any hardware overlay — pre-warms PIL/qrcode/common and avoids an intermittent `UnidentifiedImageError` in `_init_background`'s `PIL.Image.open`.
2. Patch `threading.Thread` to a no-op for EVERY driver (not just encoder variants) — `_display_loop` is an infinite `while True` with real `time.sleep`; a real thread hangs process exit.
3. Neutralize `os.system` (menu shells out to `sudo reboot`).
4. Install fakes for `luma.*`, `ST7789`, `spidev`, `gpiozero`, `pyky040`, Qt, `pygame` as each driver requires, scoped to that driver's import only.

See `tests/ui/test_fixed_base_drivers_load.py` and `tests/conftest.py`'s `x86_platform` / `fixed_base_harness` fixtures for the exact overlay dicts to reuse.

### Harness C — grillplat GPIO mock

Copied pattern from `tests/conftest.py`'s `x86_platform` fixture and `tests/unit/platform/test_prototype.py`: `mock.patch.object(mod, HardwareClass)` on the platform module's bound hardware symbols, then drive `fan_on`/`set_duty_cycle`/`get_output_status`/`auger_*` and assert `out_pins`/`current` state.

---

### Task 1: Exclude deferred BLE probes from the coverage floor

**Files:**
- Modify: `pyproject.toml` (`[tool.coverage.run] omit`)
- Test: none (config change; verified by a coverage run)

**Interfaces:**
- Consumes: nothing.
- Produces: the reduced denominator every later task's percentage is measured against.

- [ ] **Step 1: Read the current omit list**

Run: `grep -n "omit" pyproject.toml` — confirm current value is `omit = ["tests/*", "*/__pycache__/*", "*/.venv/*", "*/venv/*"]`.

- [ ] **Step 2: Add the three BLE probe modules with a justifying comment**

```toml
omit = [
    "tests/*",
    "*/__pycache__/*",
    "*/.venv/*",
    "*/venv/*",
    # BLE-only probes: require bluepy + real Bluetooth hardware to exercise
    # meaningfully; deferred from the coverage floor by decision 2026-07-21.
    "probes/bt_meater.py",
    "probes/bt_meater_exp.py",
    "probes/bt_ibbq.py",
]
```

- [ ] **Step 3: Verify the modules are dropped and overall coverage rises**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes -q --cov --cov-report=term-missing 2>&1 | grep -E "bt_meater|bt_ibbq|TOTAL"`
Expected: no `bt_meater*`/`bt_ibbq` rows appear; command exits clean.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -F <msg-file>   # subject: chore(cov): exclude BLE-only probes from coverage floor
```

---

### Task 2: Pure-logic virtual + trivial probes

**Files:**
- Test: `tests/unit/probes/test_virtual_probes.py`
- Modules covered: `probes/virtual_average.py`, `probes/virtual_median.py`, `probes/virtual_highest.py`, `probes/virtual_lowest.py`, `probes/prototype.py`, `probes/disabled.py`

**Interfaces:**
- Consumes: `probes.base.ProbeInterface`. `ReadProbes(probe_info, device_info, units)`; the aggregators implement `read_all_ports(output_data)` reading `device_info["config"]["probes_list"]` and writing `output_data["primary"|"food"|"aux"][label]`; `get_device_info()` returns `device_info` with a `status` dict.
- Produces: nothing consumed downstream.

These modules import only `probes.base` + `statistics` — no hardware, no mocks needed. Build a real `ReadProbes` with a minimal port map and feed `output_data`.

- [ ] **Step 1: Write failing tests for the four aggregators**

For each of average/median/highest/lowest, assert the aggregation math and the `tr=0` side effect. Example (average):

```python
import pytest
from probes.base import ProbeInterface  # noqa: F401  ensures base import path is valid

def _output_data():
    return {
        "primary": {"Grill1": 100, "Grill2": 200},
        "food": {}, "aux": {}, "tr": {},
    }

def _device(module, probes_list, port="VIRT0"):
    return {
        "device": "virt", "module": module, "ports": [port],
        "config": {"probes_list": probes_list},
    }

def _make(module_path, module, probes_list):
    import importlib
    probe = importlib.import_module(module_path)
    obj = probe.ReadProbes(
        probe_info={"probes": []},
        device_info=_device(module, probes_list),
        units="F",
    )
    return obj

def test_virtual_average_means_primary_probes():
    obj = _make("probes.virtual_average", "virtual_average", ["Grill1", "Grill2"])
    out = obj.read_all_ports(_output_data())
    label = obj.port_map["VIRT0"]
    assert out["primary"][label] == pytest.approx(150.0)
    assert out["tr"][label] == 0
```

Repeat with `virtual_median` (median → 150 for [100,200]; use 3 inputs for a distinct median), `virtual_highest` (max → 200), `virtual_lowest` (min → 100).

Also add: `get_device_info()` returns a dict with a `status` key; and a mixed food/aux probe-location case (probe found in `output_data["food"]`).

**NOTE for the implementer:** `ReadProbes.__init__` calls `ProbeInterface.__init__`, which builds `port_map`/`primary_port`/`food_ports`/`aux_ports` from `device_info["ports"]`. Read `probes/base.py:133` (`__init__`) and `:403` (`get_port_map`) first to construct a `device_info` that yields a valid single primary port. If the base `__init__` needs more of `probe_info`, supply the minimal shape it reads — do not stub the base.

- [ ] **Step 2: Run the tests, verify they fail (module not yet under a test file / assertion mismatch)**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_virtual_probes.py -v`
Expected: collection succeeds; any assertion that mismatches real behavior is either corrected to match real behavior, or pinned with a docstring if it looks like a bug.

- [ ] **Step 3: Add `prototype` and `disabled` probe tests**

`probes/prototype.py` returns simulated/random temps; `probes/disabled.py` returns a fixed "disabled" reading. Exercise `read_all_ports`/`get_device_info` for both. Read each file first to learn the exact return shape; assert on the structure, not on random values (seed or assert type/keys).

- [ ] **Step 4: Run coverage, verify each module ≥50% (target: ≥90% — they are tiny and pure)**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_virtual_probes.py -q --cov=probes.virtual_average --cov=probes.virtual_median --cov=probes.virtual_highest --cov=probes.virtual_lowest --cov=probes.prototype --cov=probes.disabled --cov-report=term-missing`
Expected: every listed module ≥50% (aim ≥90%).

- [ ] **Step 5: ruff format + commit**

```bash
uvx ruff format tests/unit/probes/test_virtual_probes.py
git add tests/unit/probes/test_virtual_probes.py
git commit -F <msg-file>   # subject: test(probes): cover virtual aggregators + prototype/disabled
```

---

### Task 3: Controller logic — fuzzy, ml, update_ml, system_commands

**Files:**
- Test: `tests/unit/controller/test_fuzzy.py`, `tests/unit/controller/test_ml.py`, `tests/unit/controller/test_update_ml.py`, `tests/unit/runtime/test_system_commands.py`
- Modules: `controller/fuzzy.py`, `controller/ml.py`, `controller/update_ml.py`, `controller/runtime/system_commands.py`

**Interfaces:**
- `controller/fuzzy.py`: `Controller(config, units, cycle_data)`; `update(current) -> cycleratio`; `set_target(set_point)`. Loads `./controller/fuzzy.pickle` (exists in repo). `cycle_data["HoldCycleTime"]` required.
- `controller/ml.py`: `Controller(...)` loads `./controller/ml_model.joblib` via `joblib.load` — mock `joblib.load` (Harness A style: patch `controller.ml.load`) to return a fake model with `.predict`.
- `controller/update_ml.py`: `create_new_model(infile, outfile, test=False)` reads a CSV via `pandas`, fits `sklearn.linear_model.LinearRegression`, dumps via `joblib.dump` — mock `dump` and feed a tiny in-memory/tmp CSV.

- [ ] **Step 1: fuzzy — cover `set_target` unit conversion and `update` happy path**

```python
def _controller(units="F"):
    from controller.fuzzy import Controller
    return Controller(config={}, units=units, cycle_data={"HoldCycleTime": 20})

def test_set_target_converts_celsius_to_fahrenheit():
    c = _controller(units="C")
    c.set_target(100)          # 100C -> 212F
    assert c.set_point == 212

def test_update_returns_cycle_ratio_between_0_and_1():
    c = _controller()
    c.set_target(225)
    ratio = c.update(200)
    assert 0.0 <= ratio <= 1.0
```

**LATENT BUG to pin (do NOT silently fix without applying the latent-bug rule):** `controller/fuzzy.py` first-run branch contains `self.last_temp == current` (a `==` comparison, not the intended `self.last_temp = current` assignment). Add a test that reaches the `last_temp == -99` first-call branch and documents the observed behavior. Per the latent-bug rule this typo is unambiguous and has no external contract, so fix it (`==` → `=`) in the same commit and flip the pin from RED to GREEN, with a docstring explaining the before/after. Confirm the fuzzy pickle still loads and `update` still returns a valid ratio after the fix.

- [ ] **Step 2: ml — mock the model loader**

```python
def test_ml_controller_predicts_cycle_ratio(monkeypatch):
    import controller.ml as ml
    class FakeModel:
        def predict(self, X): return [0.42]
    monkeypatch.setattr(ml, "load", lambda path: FakeModel())
    c = ml.Controller(config={}, units="F", cycle_data={"HoldCycleTime": 20})
    c.set_target(225)
    # call update per the real signature (read controller/ml.py for update()'s args)
```

Read `controller/ml.py` fully first for `update()`'s exact body/signature and assert the predicted ratio flows through. Cover the `load` failure branch (raises) too.

- [ ] **Step 3: update_ml — fit on a tiny CSV with dump mocked**

```python
def test_create_new_model_fits_and_dumps(monkeypatch, tmp_path):
    import controller.update_ml as um
    csv = tmp_path / "ds.csv"
    csv.write_text("current,setpoint,rate_change,cycle_ratio\n105,165,1,1\n139,165,1.6,0.22\n169,165,1.4,0.05\n")
    dumped = {}
    monkeypatch.setattr(um, "dump", lambda model, outfile: dumped.setdefault("out", outfile))
    um.create_new_model(infile=str(csv), outfile=str(tmp_path / "m.joblib"), test=True)
    assert dumped["out"].endswith("m.joblib")
```

Read `create_new_model` fully first — cover the `test=True` branch and any print/return path.

- [ ] **Step 4: system_commands — neutralize then assert dispatch**

`controller/runtime/system_commands.py` (currently 33%) wraps shell/system actions. Read it; for each command, `mock.patch.object` the bound `os`/`subprocess` symbol and assert the correct command string/args are produced WITHOUT executing. Never let a real `reboot`/`shutdown`/`os.system` run.

- [ ] **Step 5: Run all four modules' coverage, verify ≥50% each**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/controller/test_fuzzy.py tests/unit/controller/test_ml.py tests/unit/controller/test_update_ml.py tests/unit/runtime/test_system_commands.py -q --cov=controller.fuzzy --cov=controller.ml --cov=controller.update_ml --cov=controller.runtime.system_commands --cov-report=term-missing`
Expected: all four ≥50%.

- [ ] **Step 6: ruff format + commit** (subject: `test(controller): cover fuzzy/ml/update_ml/system_commands (+fix fuzzy last_temp typo)`).

---

### Task 4: SPI/I2C/ADC probes (Harness A)

**Files:**
- Test: `tests/unit/probes/test_max31865_probe.py`, `tests/unit/probes/test_ds18b20_probe.py`, `tests/unit/probes/test_ads1115_probes.py`
- Modules: `probes/max31865.py` (`import spidev`), `probes/ds18b20.py`, `probes/ads1115.py`, `probes/ads1115_adafruit.py`, `probes/ads1015_adafruit.py`

**Interfaces:** all expose `ReadProbes(probe_info, device_info, units)` with `read_all_ports(output_data)` and `get_device_info()`. `probes/base.py:320` (`read_all_ports`) and `:427` (`read_voltage`) define the ADC contract. Follow `tests/unit/probes/test_max31856_probe.py` verbatim as the structural template.

- [ ] **Step 1: max31865 — fake `spidev`, exercise temperature read**

Use Harness A: `load_probe(monkeypatch, "probes.max31865", {"spidev": install_fake("spidev", SpiDev=FakeSpiDev)})` where `FakeSpiDev` supplies `open`/`xfer2`/`close` returning canned register bytes. Then `bare_readprobes(...)` and call `read_all_ports`, asserting a plausible temperature is written. Read `probes/max31865.py` first to learn the exact register/xfer protocol so the fake returns bytes that decode to a known temperature; cover the fault/short branch too.

- [ ] **Step 2: ds18b20 — fake the 1-wire read path**

Read `probes/ds18b20.py` for its file/sysfs or library read; mock that read to return a known raw value; assert the decoded temperature. Cover the read-error branch.

- [ ] **Step 3: ads1115 family — fake the ADC library, exercise `read_voltage`→temperature**

For `ads1115.py`, `ads1115_adafruit.py`, `ads1015_adafruit.py`: install the fake ADC lib (`Adafruit_ADS1x15` / `adafruit_ads1x15.*` — read each file's imports), return a known raw ADC count, and assert `read_all_ports` maps it through the Steinhart/Tr math to a temperature. One test file, one test class per module.

- [ ] **Step 4: Run coverage, verify each module ≥50%**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_max31865_probe.py tests/unit/probes/test_ds18b20_probe.py tests/unit/probes/test_ads1115_probes.py -q --cov=probes.max31865 --cov=probes.ds18b20 --cov=probes.ads1115 --cov=probes.ads1115_adafruit --cov=probes.ads1015_adafruit --cov-report=term-missing`
Expected: all five ≥50%.

- [ ] **Step 5: ruff format + commit** (subject: `test(probes): cover max31865/ds18b20/ads1x15 via spidev/ADC mocks`).

---

### Task 5: grillplat numato_usbrelay + raspberry_pi_all (Harness C)

**Files:**
- Test: `tests/unit/platform/test_numato_usbrelay.py`, `tests/unit/platform/test_raspberry_pi_all.py`
- Modules: `grillplat/numato_usbrelay.py` (29%), `grillplat/raspberry_pi_all.py` (34%)

**Interfaces:** `GrillPlatform(config)` with `auger_on/off`, `fan_on/off`, `igniter_on/off`, `power_on/off`, `set_duty_cycle`, `get_output_status`, `pwm_fan_ramp`. Follow `tests/unit/platform/test_prototype.py` and `tests/conftest.py`'s `x86_platform` fixture for the mock overlay.

- [ ] **Step 1: numato_usbrelay — mock the serial/relay device, drive all output methods**

Read `grillplat/numato_usbrelay.py` for its serial/relay hardware handle; `mock.patch.object` it; call each `*_on`/`*_off` and assert the relay-command bytes/state. Cover `get_output_status`.

- [ ] **Step 2: raspberry_pi_all — mock HardwarePWM + gpiozero, drive fan/duty/ramp**

`raspberry_pi_all.py` imports `from rpi_hardware_pwm import HardwarePWM` and `gpiozero` `OutputDevice`. See `tests/unit/platform/test_raspberry_pi_system.py` (already stubs these) for the overlay. Cover `fan_on`/`set_duty_cycle` (the inverted `100 - percent` duty math), `get_output_status` (should now report the unified `current_fan_speed_percent = 0` initial seed), and `fan_off`.

- [ ] **Step 3: Run coverage, verify each ≥50%**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/platform/test_numato_usbrelay.py tests/unit/platform/test_raspberry_pi_all.py -q --cov=grillplat.numato_usbrelay --cov=grillplat.raspberry_pi_all --cov-report=term-missing`
Expected: both ≥50%.

- [ ] **Step 4: ruff format + commit** (subject: `test(grillplat): cover numato_usbrelay + raspberry_pi_all output methods`).

---

### Task 6: display/flexobject.py (Harness B, flex base)

**Files:**
- Test: `tests/ui/test_flexobject_coverage.py`
- Module: `display/flexobject.py` (46.8% → target ≥60%; +62 measures clears the floor, aim higher)

**Interfaces:** `flexobject.py` builds/draws flex dashboard widgets on a PIL canvas. See existing `tests/ui/test_flexobject_accent.py`, `test_flex_gauge_ember.py`, `test_flexobject_accent.py`, `test_flex_status_icon_smokeplus.py` for how a flexobject is constructed and rendered offscreen.

- [ ] **Step 1: Enumerate uncovered branches**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui -q --cov=display.flexobject --cov-report=term-missing 2>&1 | tail -30` and read the missing-line ranges. Map each range to a widget type / draw branch (gauge, number, icon, hopper, status) not yet exercised.

- [ ] **Step 2: Add render tests for the uncovered widget/draw branches**

Following the existing `tests/ui/test_flex_*` construction, instantiate a flexobject configured for each uncovered widget variant and call its draw/update path against an offscreen surface. Assert on produced state/pixels the way the existing flex tests do (they are the template — match their fixture usage, do not invent a new harness).

- [ ] **Step 3: Run coverage, verify ≥60%**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_flexobject_coverage.py tests/ui -q --cov=display.flexobject --cov-report=term-missing 2>&1 | grep flexobject`
Expected: `display/flexobject.py` ≥60%.

- [ ] **Step 4: ruff format + commit** (subject: `test(display): raise flexobject coverage over the 50% floor`).

---

### Task 7: Fixed OLED/TFT display drivers (Harness B)

**Files:**
- Test: `tests/ui/test_fixed_drivers_methods.py`
- Modules: `display/ssd1306b.py` (0%), `display/ssd1306.py` (0%), `display/ili9341f.py` (0%), `display/st7789p.py` (0%), `display/protoflex.py` (0%), `display/prototype.py` (0%)

**Interfaces:** each subclasses a `display.base_fixed`/`display.base_flex` base and implements `_init_display_device`, `_display_canvas`/`_display_loop` body, `_menu_*`. `tests/ui/test_fixed_base_drivers_load.py` already imports and constructs all 16 drivers under the overlay — reuse its overlay dicts and pre-warm/thread/os.system rules exactly.

- [ ] **Step 1: Import + construct each driver under the overlay (proves import + `__init__`)**

Reuse the `tests/ui/test_fixed_base_drivers_load.py` machinery (import `display.base_fixed` first; fake `luma.*`/`ST7789`/`spidev`/`gpiozero`/`pyky040`; `threading.Thread` no-op; `os.system` neutralized). Construct each of the six modules' `Display(...)` with `FULL_DEV_PINS`. This alone lifts them off 0%.

- [ ] **Step 2: Drive the public render/status methods for each driver**

For each driver call the non-loop public methods the base defines (e.g. `display_status`, `display_splash`, `clear_display`, `_display_canvas`) with a minimal in-status payload, against the faked device. Assert the driver forwards draw calls to its (faked) device without error. Read each driver for its exact method set; do not start the real `_display_loop`.

- [ ] **Step 3: Run coverage, verify each ≥50%**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_fixed_drivers_methods.py -q --cov=display.ssd1306b --cov=display.ssd1306 --cov=display.ili9341f --cov=display.st7789p --cov=display.protoflex --cov=display.prototype --cov-report=term-missing`
Expected: all six ≥50%.

- [ ] **Step 4: ruff format + commit** (subject: `test(display): cover fixed OLED/TFT driver init + render methods`).

---

### Task 8: pygame/Qt/DSI display drivers (Harness B)

**Files:**
- Test: `tests/ui/test_pygame_qt_drivers.py`
- Modules: `display/pygame_64x128.py` (0%), `display/pygame_240x320.py` (20.5%), `display/pygame_240x320b.py` (18.2%), `display/dsi_800x480t.py` (7%), `display/qtapp.py` (35%)

**Interfaces:** pygame drivers render to an SDL surface (already run under `SDL_VIDEODRIVER=dummy`); `qtapp.py` drives a Qt app (run under `QT_QPA_PLATFORM=offscreen`). `dsi_800x480t.py` is a flex driver over a framebuffer. See `tests/ui/test_display_launch.py`, `test_base_flex_dash_update.py`, `test_display_sleep_timeout.py` for offscreen pygame/Qt construction.

- [ ] **Step 1: Construct + render each pygame driver offscreen**

With `SDL_VIDEODRIVER=dummy` (already in the runner env), construct each pygame driver and call its status-render path once; assert it draws without error and updates its surface. Patch `threading.Thread`/loops as in Harness B so no infinite loop starts.

- [ ] **Step 2: Cover qtapp + dsi_800x480t**

For `qtapp.py`, construct the Qt app offscreen and exercise its update/paint entry once. For `dsi_800x480t.py`, follow the flex-driver render template (`test_base_flex_dash_update.py`) with the framebuffer faked. Read each module for the exact entry points.

- [ ] **Step 3: Run coverage, verify each ≥50%**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui/test_pygame_qt_drivers.py -q --cov=display.pygame_64x128 --cov=display.pygame_240x320 --cov=display.pygame_240x320b --cov=display.dsi_800x480t --cov=display.qtapp --cov-report=term-missing`
Expected: all five ≥50%.

- [ ] **Step 4: ruff format + commit** (subject: `test(display): cover pygame/Qt/DSI drivers offscreen`).

---

### Task 9: Whole-suite verification of both targets

**Files:** none (measurement + report).

- [ ] **Step 1: Full suite with JSON coverage**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest -q --cov --cov-report=json:/tmp/cov-final.json --cov-report=term 2>&1 | tail -5`
Expected: all tests pass; `TOTAL` ≥ 75%.

- [ ] **Step 2: Assert the per-module floor with the analysis script**

Reuse the analysis approach from planning: load `/tmp/cov-final.json`, compute combined (line+branch) percent per file, and print any file < 50%. Expected: empty list (bt_* are omitted from the report entirely).

- [ ] **Step 3: Regenerate the risk-ranked gap report for the record**

Run: `uv run python scripts/coverage_gaps.py /tmp/cov-final.json > docs/coverage/gap-report-2026-07-21.md` and commit it.

- [ ] **Step 4: Commit the report** (subject: `docs(cov): record 75%/floor gap report after coverage push`).

---

## Self-Review

- **Spec coverage:** every one of the 32 sub-50 modules is assigned — bt_* → Task 1 (omit); virtual/trivial probes → Task 2; controllers → Task 3; SPI/ADC probes → Task 4; grillplat → Task 5; display flexobject → Task 6; fixed drivers → Task 7; pygame/Qt/DSI → Task 8; final verification → Task 9. The 75% overall target is verified in Task 9 Step 1; the ≥50% floor in Task 9 Step 2.
- **Harness reuse is explicit** (A/B/C) with real code and named existing template files, not "similar to".
- **Latent-bug handling** is a global constraint and is instantiated concretely in Task 3 (fuzzy `==` typo).
- **Destructive-call safety** is a global constraint and re-stated in the display (os.system/reboot) and system_commands tasks.
- **Interfaces** name the real constructor signatures and the `probes/base.py` line numbers an implementer must read before writing.
