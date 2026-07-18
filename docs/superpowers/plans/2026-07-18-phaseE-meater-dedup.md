# Phase E — Meater: shared core, delete `bt_meater.py` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `probes/bt_meater_exp.py` (simplepyble) the sole Meater implementation, extract its pure transport-agnostic protocol math into a new CI-testable `probes/meater_common.py`, delete the redundant bluepy module `probes/bt_meater.py`, and remove its wizard/registry entry — all pinned by characterization tests written first.

**Architecture:** The Meater temperature protocol (byte→temperature math, ambient correction) is pure arithmetic with no BLE dependency, but today it is trapped inside `bt_meater_exp.py` behind a top-level `import simplepyble` (a package that is NOT installed in CI), so it cannot be unit-tested. We extract that math into two dependency-free base classes in `probes/meater_common.py` (`MeaterOriginalProtocol`, `MeaterProProtocol`), have `bt_meater_exp`'s `Meater`/`Meater_Pro` inherit them (keeping only their simplepyble notification/connection code), then delete `bt_meater.py` and its manifest entry. Tests import the math via `meater_common` directly (no simplepyble needed).

**Tech Stack:** Python 3.14, pytest, `uv`/`uvx`, ruff. Serena symbolic tools for edits. BLE transports: `simplepyble` (kept, sole path), `bluepy` (deleted with `bt_meater.py`).

## Global Constraints

- Python 3.14. `except (A, B)` is canonical; ruff collapses `except A, B` style — do not fight it.
- TEST COMMAND (exact, always): `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`. Bare `python`/`pytest` gives false failures; missing the offscreen/dummy vars HANGS. Every "run the test" step MUST use this form.
- Before every commit: `uvx ruff format <changed files>` then `uvx ruff check <changed files>`.
- Edits via Serena symbolic tools (`find_symbol`, `replace_symbol_body`, `insert_after_symbol`, etc.).
- Commit with `git commit -F <msgfile>` (zsh eats backticks in `-m`). Co-author trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Behavior-preserving EXCEPT the sanctioned `bt_meater.py` deletion (this phase's deliberate behavior change — removes the bluepy transport path). The `bt_meater → bt_meater_exp` settings migration is KEPT.
- Branch for this phase: `refactor/meater-dedup` (cut from `massive-reworks-and-new-ui`, which already contains merged Phases A–D).

---

## Key findings from the live code (READ THIS FIRST — the spec's line numbers/claims are stale)

1. **The two files are NOT byte-identical.** The spec claims they "duplicate the entire Meater protocol byte-for-byte." False. They have different class names, different transport models, and different `Meater_Device`/`ReadProbes` bodies:
   - `probes/bt_meater.py` (493 lines, **bluepy**, polling model): classes `BaseMeater`, `MeaterOriginal(BaseMeater)`, `MeaterPro(BaseMeater)`, `Meater_Device`, `ReadProbes`. Reads via `readCharacteristic()` polling; tracks battery/firmware; `ReadProbes.read_all_ports` works in **Celsius** (`probe_values_C`, `_to_fahrenheit`).
   - `probes/bt_meater_exp.py` (688 lines, **simplepyble**, notification model): classes `Meater`, `Meater_Pro`, `MeaterProbeHandler`, `Meater_Device`, `ReadProbes`. Reads via `notify()` callbacks; NO battery/firmware; `ReadProbes.read_all_ports` works in **Fahrenheit** (`probe_values_F`, `_to_celsius`). Only one tip port (`BT0`).
   The shared surface named in the spec (`toCelsius`, `get_short`, `ambient_correction`, `toFahrenheit*`, `convert_to_temperatures`) exists in BOTH but as **instance methods on the probe classes**, not module-level functions, and with subtly different None-guarding between the two files. `Meater_Device`/`ReadProbes` are transport-specific and NOT shareable.

2. **`simplepyble` is NOT installed in the CI/test venv** (`ModuleNotFoundError`), so `import probes.bt_meater_exp` fails at module load (line 42 `import simplepyble`). `bluepy` IS installed. Consequence: golden output cannot be captured by directly importing `bt_meater_exp`; and the extracted `meater_common.py` MUST NOT import simplepyble or it stays un-testable. The characterization test (Task 1) captures golden values by stubbing `sys.modules['simplepyble']` before import; after Task 2, tests import the math from `meater_common` (no stub needed).

3. **The math methods have NO `None` guard in `bt_meater_exp`** (unlike `bt_meater.py`'s `BaseMeater.toCelsius`/`toFahrenheit`, which do). We preserve `bt_meater_exp`'s behavior exactly: `Meater.toCelsius`/`toFahrenheit` and `Meater_Pro.toCelsius` do NOT guard `None`; only `getTip`/`getTipC` guard `self.data is None`.

4. **`toFahrenheitInternals` mutates its argument list in place** and `getTips`/`getTip` call it on `self.internal_temps`, so calling `getTip()` twice corrupts state. Existing behavior — preserve, and account for it in tests (re-run `convert_to_temperatures` before each getter assertion).

5. **Registry is filename/manifest-based.** Modules load via `probes/main.py:44` `importlib.import_module(f"probes.{modulename}")` where `modulename = device.get("module_filename", device["module"])`; failure falls back to `probes.disabled`. The wizard registry is `wizard/wizard_manifest.json` → `modules.probes.bt_meater` (a full entry, lines ~2719–2765) and `modules.probes.bt_meater_exp` (~2766+). Deleting `bt_meater.py` requires removing the `bt_meater` manifest entry too.

6. **The migration (KEEP it) has a dangling-reference hazard.** `common/settings_migration.py:228-233` (guarded by `prev_ver == 1.9.0 build <= 32`):
   ```python
   if device["module"] == "bt_meater_alt":
       ...["module"] = "bt_meater"
   elif device["module"] == "bt_meater":
       ...["module"] = "bt_meater_exp"
   ```
   Because it is `if/elif` in a single pass, a `bt_meater_alt` device migrates to `bt_meater` and STOPS there (the `elif` is not evaluated for that index). After we delete `bt_meater.py`, such a device's `bt_meater` module no longer exists — it would fall back to `disabled`. Task 4 repoints the `bt_meater_alt` branch to land on `bt_meater_exp` directly, so no terminal `bt_meater` value survives the migration. (This preserves the migration's INTENT — steer legacy users to the working module — which is what "keep the migration" means. Flagged to the human as a judgment call.)

7. **No existing Meater unit/characterization tests** (`grep meater tests/` → only manifest-iterating tests that validate config defaults, which are unaffected by removing the entry). `tests/unit/probes/` holds sibling probe tests (`test_thermoworks_cloud_probe.py`, `test_max31856_probe.py`, `test_mcp9600_probe.py`) whose style we follow (fake/stub missing hw modules via `monkeypatch.setitem(sys.modules, ...)`, load manifest via `json.load`).

### Interpretation note (deviation from a literal reading of the spec)
The spec says to extract "the shared protocol/math/`Meater_Device`/`ReadProbes` into `meater_common.py`." We extract **only the pure protocol math** (no simplepyble import). We deliberately DO NOT move `MeaterProbeHandler`, `Meater_Device`, or `ReadProbes` — they are simplepyble-coupled, so moving them would make `meater_common.py` un-importable in CI and defeat the verification strategy ("unit tests … no hardware in CI"). This satisfies the spirit (a deduplicated, independently unit-tested protocol core) while keeping the core CI-testable. After `bt_meater.py` is deleted there is only one transport consumer anyway, so there is nothing left to deduplicate against.

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `tests/unit/probes/test_meater_protocol.py` | Create (Task 1) | Characterization pins for the Meater math captured from today's `bt_meater_exp` (via simplepyble stub); after Task 2, retargeted at `meater_common`. |
| `probes/meater_common.py` | Create (Task 2) | Dependency-free Meater protocol math: `MeaterOriginalProtocol`, `MeaterProProtocol`. No BLE imports. |
| `probes/bt_meater_exp.py` | Modify (Task 2, Task 4-manifest N/A) | `Meater`/`Meater_Pro` inherit the protocol bases; keep only simplepyble notification/connection code. Fix stale docstring `module` example. |
| `probes/bt_meater.py` | Delete (Task 3) | Redundant bluepy implementation — removed. |
| `wizard/wizard_manifest.json` | Modify (Task 3) | Remove the `modules.probes.bt_meater` entry (keep `bt_meater_exp`). |
| `common/settings_migration.py` | Modify (Task 4) | Repoint the `bt_meater_alt` migration branch to `bt_meater_exp` so no terminal `bt_meater` value survives deletion. |
| `tests/unit/probes/test_meater_registry.py` | Create (Task 3, Task 4) | Load/registry guards: no `bt_meater` manifest entry, `bt_meater.py` gone, `bt_meater_exp` still present, migration produces only live module names. |

---

## Task 1: Characterization pins for the Meater protocol math (prerequisite — commit first)

Capture today's `bt_meater_exp` math outputs as failing-then-passing pins BEFORE any refactor. Because `simplepyble` is absent, the test stubs it in `sys.modules` before importing the module, then asserts against the exact values observed from the live code. These pins must keep passing unchanged through Tasks 2–4.

**Files:**
- Create: `tests/unit/probes/test_meater_protocol.py`

**Interfaces:**
- Consumes: `probes.bt_meater_exp.Meater`, `probes.bt_meater_exp.Meater_Pro` (current live classes) — imported after `sys.modules['simplepyble']` is stubbed.
- Produces: nothing importable; establishes the golden numeric contract reused by Task 2. Golden byte fixture: `struct.pack("<6h", 1000, 1100, 1200, 900, 1300, 800)`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/probes/test_meater_protocol.py`:

```python
"""Characterization pins for the Meater temperature protocol math.

These values were captured from the live probes/bt_meater_exp.py before the
Phase E refactor. simplepyble is not installed in CI, so we stub it in
sys.modules before importing the module. After the math is extracted into
probes/meater_common.py, test_meater_common.py retargets these same pins at
the extracted classes; this file continues to pin the bt_meater_exp classes,
which now inherit that math.
"""

import struct
import sys
import types

import pytest


@pytest.fixture
def meater_mod():
    """Import probes.bt_meater_exp with a stubbed simplepyble module."""
    saved = sys.modules.get("simplepyble")
    sys.modules["simplepyble"] = types.ModuleType("simplepyble")
    # Force a fresh import so the stub is in effect at module load time.
    sys.modules.pop("probes.bt_meater_exp", None)
    import probes.bt_meater_exp as mod

    yield mod

    sys.modules.pop("probes.bt_meater_exp", None)
    if saved is not None:
        sys.modules["simplepyble"] = saved
    else:
        sys.modules.pop("simplepyble", None)


# Six little-endian int16s: 5 internal sensors + 1 ambient.
GOLDEN_DATA = struct.pack("<6h", 1000, 1100, 1200, 900, 1300, 800)


def test_original_to_celsius(meater_mod):
    o = meater_mod.Meater.__new__(meater_mod.Meater)
    assert o.toCelsius(1000) == 63.0


def test_original_to_fahrenheit(meater_mod):
    o = meater_mod.Meater.__new__(meater_mod.Meater)
    assert o.toFahrenheit(1000) == 145.4


def test_original_bytes_to_int(meater_mod):
    o = meater_mod.Meater.__new__(meater_mod.Meater)
    assert o.bytesToInt(0x10, 0x02) == 528


def test_original_convert_ambient(meater_mod):
    o = meater_mod.Meater.__new__(meater_mod.Meater)
    assert o.convertAmbient(GOLDEN_DATA) == pytest.approx(7667.147276395427)


def test_original_getters_with_data(meater_mod):
    o = meater_mod.Meater.__new__(meater_mod.Meater)
    o.data = GOLDEN_DATA
    assert o.getTipC() == 63.0
    assert o.getTip() == 145.4
    assert o.getAmbientC() == pytest.approx(479.6967047747142)
    assert o.getAmbient() == pytest.approx(895.4540685944855)
    assert o.getTemps() == pytest.approx((895.4540685944855, 145.4))


def test_original_getters_none_data(meater_mod):
    o = meater_mod.Meater.__new__(meater_mod.Meater)
    o.data = None
    assert o.getTip() is None
    assert o.getTipC() is None


def test_pro_to_celsius_sign_branches(meater_mod):
    mp = meater_mod.Meater_Pro.__new__(meater_mod.Meater_Pro)
    assert mp.toCelsius(1000) == 31.5
    assert mp.toCelsius(-1000) == -31.5
    assert mp.toCelsius(0) == 0


def test_pro_get_short(meater_mod):
    mp = meater_mod.Meater_Pro.__new__(meater_mod.Meater_Pro)
    assert mp.get_short(GOLDEN_DATA, 0) == 1000
    assert mp.get_short(GOLDEN_DATA, 10) == 800


def test_pro_ambient_correction(meater_mod):
    mp = meater_mod.Meater_Pro.__new__(meater_mod.Meater_Pro)
    assert mp.ambient_correction(25.0, 20.0) == pytest.approx(26.0)


def test_pro_convert_to_temperatures(meater_mod):
    mp = meater_mod.Meater_Pro.__new__(meater_mod.Meater_Pro)
    mp.convert_to_temperatures(GOLDEN_DATA)
    assert mp.internal_temps == pytest.approx([31.5, 34.625, 37.75, 28.375, 40.875])
    assert mp.ambient_temp == pytest.approx(25.25)


def test_pro_fahrenheit_helpers(meater_mod):
    mp = meater_mod.Meater_Pro.__new__(meater_mod.Meater_Pro)
    assert mp.toFahrenheitAmbient(25.25) == pytest.approx(77.45)
    # NOTE: toFahrenheitInternals mutates its list argument in place.
    assert mp.toFahrenheitInternals([31.5, 34.625, 37.75, 28.375, 40.875]) == pytest.approx(
        [88.7, 94.325, 99.95, 83.075, 105.575]
    )


def test_pro_getters(meater_mod):
    mp = meater_mod.Meater_Pro.__new__(meater_mod.Meater_Pro)
    # getAmbient depends only on ambient_temp.
    mp.convert_to_temperatures(GOLDEN_DATA)
    assert mp.getAmbient() == pytest.approx(77.45)
    # getTips/getTip mutate internal_temps in place -> reconvert before each.
    mp.convert_to_temperatures(GOLDEN_DATA)
    assert mp.getTips() == pytest.approx([88.7, 94.325, 99.95, 83.075, 105.575])
    mp.convert_to_temperatures(GOLDEN_DATA)
    assert mp.getTip() == pytest.approx(83.075)
```

- [ ] **Step 2: Run the test to verify it PASSES against the live code**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_protocol.py -q`
Expected: PASS (13 tests). These pin the CURRENT behavior — they must pass immediately, since they characterize today's `bt_meater_exp`. (This is a characterization commit, not red/green — the "failing" state here would only be a stale/wrong golden value; if any assertion fails, fix the golden number to match the observed live output, do not change production code.)

- [ ] **Step 3: Format and lint**

Run: `uvx ruff format tests/unit/probes/test_meater_protocol.py && uvx ruff check tests/unit/probes/test_meater_protocol.py`
Expected: reformatted/clean.

- [ ] **Step 4: Commit**

```bash
cat > /tmp/msg_e1.txt <<'EOF'
test(probes): pin Meater protocol math before Phase E dedup

Characterization pins captured from live bt_meater_exp (simplepyble
stubbed in sys.modules since it is absent in CI). These lock toCelsius,
toFahrenheit, bytesToInt, convertAmbient, get_short, ambient_correction,
convert_to_temperatures and the getters before the meater_common split.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add tests/unit/probes/test_meater_protocol.py
git commit -F /tmp/msg_e1.txt
```

---

## Task 2: Extract the protocol math into `probes/meater_common.py`

Create the dependency-free protocol module and make `bt_meater_exp`'s `Meater`/`Meater_Pro` inherit from it, leaving only simplepyble transport code in `bt_meater_exp`. A new `test_meater_common.py` pins the extracted classes directly (importable in CI with no stub); Task 1's pins keep passing because the `bt_meater_exp` classes now inherit the identical math.

**Files:**
- Create: `probes/meater_common.py`
- Create: `tests/unit/probes/test_meater_common.py`
- Modify: `probes/bt_meater_exp.py` (classes `Meater`, `Meater_Pro`; docstring)

**Interfaces:**
- Produces (in `probes/meater_common.py`):
  - `class MeaterOriginalProtocol` with instance methods, all pure (rely only on `self.data`):
    - `toCelsius(self, value) -> float` = `(float(value) + 8.0) / 16.0`
    - `toFahrenheit(self, value) -> float` = `((self.toCelsius(value) * 9) / 5) + 32.0`
    - `bytesToInt(self, byte0, byte1) -> int` = `byte1 * 256 + byte0`
    - `convertAmbient(self, array) -> float`
    - `getAmbient(self) -> float`, `getAmbientC(self) -> float`
    - `getTip(self) -> float | None`, `getTipC(self) -> float | None` (guard `self.data is None`)
    - `getTemps(self) -> tuple[float, float]`
  - `class MeaterProProtocol` with instance methods (rely on `self.internal_temps`, `self.ambient_temp`, and the passed `data`):
    - `toCelsius(self, value) -> float` (sign-branched, `/32`)
    - `toFahrenheitInternals(self, temps) -> list` (mutates in place — preserved)
    - `toFahrenheitAmbient(self, temp) -> float`
    - `get_short(self, data, offset) -> int`
    - `ambient_correction(self, ambient_temp, internal_temp) -> float`
    - `convert_to_temperatures(self, data) -> None` (sets `self.internal_temps`, `self.ambient_temp`)
    - `getAmbient(self) -> float`, `getTips(self) -> list`, `getTip(self) -> float`
- Consumes (in `bt_meater_exp.py`): `from probes.meater_common import MeaterOriginalProtocol, MeaterProProtocol`.

- [ ] **Step 1: Write the failing test for `meater_common`**

Create `tests/unit/probes/test_meater_common.py`:

```python
"""Unit tests for the extracted, dependency-free Meater protocol math.

Unlike test_meater_protocol.py (which stubs simplepyble to reach the
bt_meater_exp classes), this imports probes.meater_common directly -- it has
NO BLE dependency and imports cleanly in CI.
"""

import struct

import pytest

from probes.meater_common import MeaterOriginalProtocol, MeaterProProtocol

GOLDEN_DATA = struct.pack("<6h", 1000, 1100, 1200, 900, 1300, 800)


def test_original_math():
    o = MeaterOriginalProtocol()
    o.data = GOLDEN_DATA
    assert o.toCelsius(1000) == 63.0
    assert o.toFahrenheit(1000) == 145.4
    assert o.bytesToInt(0x10, 0x02) == 528
    assert o.convertAmbient(GOLDEN_DATA) == pytest.approx(7667.147276395427)
    assert o.getTipC() == 63.0
    assert o.getTip() == 145.4
    assert o.getAmbientC() == pytest.approx(479.6967047747142)
    assert o.getAmbient() == pytest.approx(895.4540685944855)
    assert o.getTemps() == pytest.approx((895.4540685944855, 145.4))


def test_original_none_data():
    o = MeaterOriginalProtocol()
    o.data = None
    assert o.getTip() is None
    assert o.getTipC() is None


def test_pro_math():
    mp = MeaterProProtocol()
    assert mp.toCelsius(1000) == 31.5
    assert mp.toCelsius(-1000) == -31.5
    assert mp.toCelsius(0) == 0
    assert mp.get_short(GOLDEN_DATA, 0) == 1000
    assert mp.get_short(GOLDEN_DATA, 10) == 800
    assert mp.ambient_correction(25.0, 20.0) == pytest.approx(26.0)
    assert mp.toFahrenheitAmbient(25.25) == pytest.approx(77.45)
    assert mp.toFahrenheitInternals([31.5, 34.625, 37.75, 28.375, 40.875]) == pytest.approx(
        [88.7, 94.325, 99.95, 83.075, 105.575]
    )


def test_pro_convert_and_getters():
    mp = MeaterProProtocol()
    mp.convert_to_temperatures(GOLDEN_DATA)
    assert mp.internal_temps == pytest.approx([31.5, 34.625, 37.75, 28.375, 40.875])
    assert mp.ambient_temp == pytest.approx(25.25)
    assert mp.getAmbient() == pytest.approx(77.45)
    mp.convert_to_temperatures(GOLDEN_DATA)
    assert mp.getTips() == pytest.approx([88.7, 94.325, 99.95, 83.075, 105.575])
    mp.convert_to_temperatures(GOLDEN_DATA)
    assert mp.getTip() == pytest.approx(83.075)
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_common.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'probes.meater_common'`.

- [ ] **Step 3: Create `probes/meater_common.py`**

Write `probes/meater_common.py` with exactly this content (values copied verbatim from the live `bt_meater_exp` so behavior is identical). Constructors initialize the state attributes the getters rely on so the classes are usable both standalone (tests) and as bases (transport subclasses set the same attributes):

```python
"""
*****************************************
PiFire Meater Protocol (transport-agnostic)
*****************************************

Description:
  Pure Meater temperature-protocol math shared by the Meater probe modules.
  This module has NO Bluetooth dependency (no bluepy / simplepyble import), so
  it can be unit-tested in CI without probe hardware or BLE libraries.

  Transport modules (e.g. probes/bt_meater_exp.py) subclass these and add the
  BLE-specific connect / notify / disconnect plumbing, setting `self.data`
  (raw notification bytes) which the getters here interpret.
"""

import struct


class MeaterOriginalProtocol:
    """Meater Original / Meater Plus temperature math.

    Reads `self.data`: a bytearray/bytes where the first two bytes are the tip
    reading and bytes [0:6] feed the ambient computation.
    """

    def __init__(self):
        self.data = None

    def toCelsius(self, value):
        return (float(value) + 8.0) / 16.0

    def toFahrenheit(self, value):
        return ((self.toCelsius(value) * 9) / 5) + 32.0

    def bytesToInt(self, byte0, byte1):
        return byte1 * 256 + byte0

    def convertAmbient(self, array):
        tip = self.bytesToInt(array[0], array[1])
        ra = self.bytesToInt(array[2], array[3])
        oa = self.bytesToInt(array[4], array[5])
        return tip + (max(0, (((ra - min(48, oa)) * 16) * 589) / 1487))

    def getAmbient(self):
        """Ambient temperature in Fahrenheit."""
        ambientTemp = self.convertAmbient(self.data)
        return self.toFahrenheit(ambientTemp)

    def getAmbientC(self):
        """Ambient temperature in Celsius."""
        ambientTemp = self.convertAmbient(self.data)
        return self.toCelsius(ambientTemp)

    def getTip(self):
        """Tip temperature in Fahrenheit."""
        if self.data is None:
            return None
        tipTemp = self.bytesToInt(self.data[0], self.data[1])
        return self.toFahrenheit(tipTemp)

    def getTipC(self):
        """Tip temperature in Celsius."""
        if self.data is None:
            return None
        tipTemp = self.bytesToInt(self.data[0], self.data[1])
        return self.toCelsius(tipTemp)

    def getTemps(self):
        """Returns (ambient, tip) in Fahrenheit."""
        return self.getAmbient(), self.getTip()


class MeaterProProtocol:
    """Meater Pro (Meater 2 Plus) temperature math.

    Reads 6 little-endian int16s: five internal sensors followed by ambient.
    `convert_to_temperatures` populates `self.internal_temps` and
    `self.ambient_temp` (both in Celsius).
    """

    def __init__(self):
        self.internal_temps = None
        self.ambient_temp = None
        self.data = None

    def toCelsius(self, value):
        if value > 0:
            return (value + 8) / 32

        if value < 0:
            return (value - 8) / 32

        return 0

    def toFahrenheitInternals(self, temps):
        for i in range(len(temps)):
            temps[i] = temps[i] * 9 / 5 + 32
        return temps

    def toFahrenheitAmbient(self, temp):
        return temp * 9 / 5 + 32

    def get_short(self, data, offset):
        return struct.unpack_from("<h", data, offset)[0]

    def ambient_correction(self, ambient_temp, internal_temp):
        return internal_temp + ((ambient_temp - internal_temp) * 1.2)

    def convert_to_temperatures(self, data):
        self.internal_temps = [
            self.toCelsius(self.get_short(data, 0)),
            self.toCelsius(self.get_short(data, 2)),
            self.toCelsius(self.get_short(data, 4)),
            self.toCelsius(self.get_short(data, 6)),
            self.toCelsius(self.get_short(data, 8)),
        ]

        self.ambient_temp = self.toCelsius(self.get_short(data, 10))
        self.ambient_correction(self.ambient_temp, self.internal_temps[4])

    def getAmbient(self):
        """Ambient temperature in Fahrenheit."""
        return self.toFahrenheitAmbient(self.ambient_temp)

    def getTips(self):
        """Tip temperatures (1-5) in Fahrenheit."""
        return self.toFahrenheitInternals(self.internal_temps)

    def getTip(self):
        """Smallest tip sensor reading (1-5) in Fahrenheit."""
        internal_temps = self.toFahrenheitInternals(self.internal_temps)
        return min(internal_temps)
```

- [ ] **Step 4: Run to verify `test_meater_common.py` PASSES**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_common.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Repoint `bt_meater_exp.Meater` to inherit `MeaterOriginalProtocol`**

Using Serena, add the import and rewrite the `Meater` class so the math methods are gone (inherited) and only transport code remains. Add near the top imports of `probes/bt_meater_exp.py` (after `from probes.base import ProbeInterface`):

```python
from probes.meater_common import MeaterOriginalProtocol, MeaterProProtocol
```

Replace the entire `class Meater:` body (currently lines ~58–230) with:

```python
class Meater(MeaterOriginalProtocol):
    def __init__(self, peripheral, scan_time=5000):
        """
        Initialize the Meater class.

        Parameters
        ----------
        scan_time : int, optional
                Time in milliseconds to scan for the Meater probe. Defaults to 5000.
        """
        super().__init__()
        self.logger = logging.getLogger("control")
        self.is_connected = False
        self.scan_time = scan_time
        self.peripheral = peripheral
        self.data = None

    def printTemps(self):
        """
        Prints the ambient and tip temperatures.
        """
        event = f"(Meater) Ambient: {self.getAmbient()} \N{DEGREE SIGN}F Tip: {self.getTip()} \N{DEGREE SIGN}F"
        # ic(event)
        self.logger.debug(event)

    def disconnect(self):
        """
        Disconnects from the Meater probe.
        """
        self.peripheral.disconnect()
        self.is_connected = False

    def notification_handler(self, data):
        """
        This is a callback function that is called whenever a notification is received from the Meater probe.
        It is responsible for storing the received data and printing it to the console.
        """
        self.data = data
        # self.printTemps()

    def subscribe_to_temps(self):
        """
        Subscribes to notifications from the Meater probe to receive temperature data.

        char uuids:
                a75cc7fc-c956-488f-ac2a-2dbc08b63a04
                7edda774-045e-4bbf-909b-45d1991a2876
        """

        try:
            contents = self.peripheral.notify(
                "a75cc7fc-c956-488f-ac2a-2dbc08b63a04",
                "7edda774-045e-4bbf-909b-45d1991a2876",
                lambda data: self.notification_handler(data),
            )

        except Exception as e:
            # ic(f"Notify Attempt failed: {e}")
            self.logger.debug(f"(Meater) Notify Attempt failed: {e}")
```

- [ ] **Step 6: Repoint `bt_meater_exp.Meater_Pro` to inherit `MeaterProProtocol`**

Replace the entire `class Meater_Pro:` body (currently lines ~233–440) with:

```python
class Meater_Pro(MeaterProProtocol):
    def __init__(self, peripheral, scan_time=5000):
        """
        Initialize the Meater class.

        Parameters
        ----------
        scan_time : int, optional
                Time in milliseconds to scan for the Meater probe. Defaults to 5000.
        """
        super().__init__()
        self.logger = logging.getLogger("control")
        self.is_connected = False
        self.scan_time = scan_time
        self.peripheral = peripheral
        self.internal_temps = None
        self.ambient_temp = None
        self.data = None

    def printTemps(self):
        """
        Prints the ambient and tip temperatures.
        """
        logger_msg = f"(Meater) Ambient: {self.getAmbient()} \N{DEGREE SIGN}F Tip Sensors(1-5): {self.getTips()}"
        self.logger.debug(logger_msg)
        # ic(logger_msg)

    def disconnect(self):
        """
        Disconnects from the Meater probe.
        """
        self.peripheral.disconnect()
        self.is_connected = False

    def notification_handler(self, data):
        """
        This is a callback function that is called whenever a notification is received from the Meater probe.
        It is responsible for storing the received data and printing it to the console.
        """

        self.data = data
        self.convert_to_temperatures(self.data)
        # self.printTemps()

    def subscribe_to_temps(self):
        """
        Subscribes to notifications from the Meater probe to receive temperature data.

        char uuids:
                c9e2746c-59f1-4e54-a0dd-e1e54555cf8b,
                7edda774-045e-4bbf-909b-45d1991a2876
        """

        try:
            contents = self.peripheral.notify(
                "c9e2746c-59f1-4e54-a0dd-e1e54555cf8b",
                "7edda774-045e-4bbf-909b-45d1991a2876",
                lambda data: self.notification_handler(data),
            )
        except Exception as e:
            logger_msg = f"(Meater) Notify Attempt failed: {e}"
            self.logger.debug(logger_msg)
            # ic(f"Notify Attempt failed: {e}")
```

- [ ] **Step 7: Fix the stale docstring `module` example in `bt_meater_exp.py`**

The module docstring's device example still says `'module' : 'bt_meater'`. Since `bt_meater` is being deleted, update the example to reference this module's own filename. Change (in the top docstring, ~line 17):

```
                        'module' : 'bt_meater',  		# Must be populated for this module to load properly
```
to:
```
                        'module' : 'bt_meater_exp',  	# Must be populated for this module to load properly
```

- [ ] **Step 8: Run BOTH pin suites — extracted classes AND inheriting transport classes**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_common.py tests/unit/probes/test_meater_protocol.py -q`
Expected: PASS (17 tests). `test_meater_protocol.py` (Task 1) still passes because the `bt_meater_exp` classes now inherit the identical math via the protocol bases.

- [ ] **Step 9: Format and lint**

Run: `uvx ruff format probes/meater_common.py probes/bt_meater_exp.py tests/unit/probes/test_meater_common.py && uvx ruff check probes/meater_common.py probes/bt_meater_exp.py tests/unit/probes/test_meater_common.py`
Expected: reformatted/clean.

- [ ] **Step 10: Commit**

```bash
cat > /tmp/msg_e2.txt <<'EOF'
refactor(probes): extract Meater protocol math to meater_common

Move the transport-agnostic Meater temperature math out of bt_meater_exp
into probes/meater_common.py (MeaterOriginalProtocol, MeaterProProtocol),
which has no BLE dependency and is unit-testable in CI. bt_meater_exp's
Meater / Meater_Pro now inherit these bases and keep only their simplepyble
notify/connect plumbing. Behavior preserved (Task 1 pins still green).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add probes/meater_common.py probes/bt_meater_exp.py tests/unit/probes/test_meater_common.py
git commit -F /tmp/msg_e2.txt
```

---

## Task 3: Delete `bt_meater.py` and its wizard manifest entry, add registry guards

Remove the redundant bluepy module and its selectable wizard entry, and add a load/registry test that fails if either the file or the manifest entry comes back (or if `bt_meater_exp` disappears).

**Files:**
- Delete: `probes/bt_meater.py`
- Modify: `wizard/wizard_manifest.json` (remove `modules.probes.bt_meater`)
- Create: `tests/unit/probes/test_meater_registry.py`

**Interfaces:**
- Consumes: `wizard/wizard_manifest.json` (`modules.probes`), the `probes/` package directory, `probes.meater_common`.
- Produces: `tests/unit/probes/test_meater_registry.py` guarding absence of `bt_meater` and presence of `bt_meater_exp`.

- [ ] **Step 1: Write the failing registry/load guard test**

Create `tests/unit/probes/test_meater_registry.py`:

```python
"""Guards that Phase E's deletion of bt_meater is complete and stays complete.

- The bluepy module file is gone.
- The wizard manifest no longer offers `bt_meater` as a selectable probe.
- The surviving simplepyble module (`bt_meater_exp`) and the extracted
  `meater_common` are both present/importable.
"""

import json
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _manifest():
    with open(os.path.join(REPO_ROOT, "wizard", "wizard_manifest.json")) as f:
        return json.load(f)


def test_bt_meater_module_file_deleted():
    assert not os.path.exists(os.path.join(REPO_ROOT, "probes", "bt_meater.py"))


def test_bt_meater_not_in_manifest_probes():
    probes = _manifest()["modules"]["probes"]
    assert "bt_meater" not in probes


def test_bt_meater_exp_still_selectable():
    probes = _manifest()["modules"]["probes"]
    assert "bt_meater_exp" in probes
    assert probes["bt_meater_exp"]["filename"] == "bt_meater_exp"


def test_meater_common_importable_without_ble():
    # Imports cleanly with no bluepy/simplepyble required.
    from probes.meater_common import MeaterOriginalProtocol, MeaterProProtocol

    assert MeaterOriginalProtocol is not None
    assert MeaterProProtocol is not None


def test_no_bt_meater_filename_left_in_manifest():
    # No manifest entry anywhere should still point its filename at bt_meater.
    text = json.dumps(_manifest())
    assert '"filename": "bt_meater"' not in text.replace('"filename": "bt_meater_exp"', "")
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_registry.py -q`
Expected: FAIL — `test_bt_meater_module_file_deleted`, `test_bt_meater_not_in_manifest_probes`, and `test_no_bt_meater_filename_left_in_manifest` fail (file and manifest entry still present).

- [ ] **Step 3: Delete the module file**

Run: `git rm probes/bt_meater.py`

- [ ] **Step 4: Remove the `bt_meater` entry from the manifest**

Edit `wizard/wizard_manifest.json`: delete the entire `"bt_meater": { ... }` object under `modules.probes` (the block starting `"bt_meater": {` with `"filename": "bt_meater"` and ending at its closing `},` immediately before `"bt_meater_exp": {`). Leave `"bt_meater_exp": { ... }` intact. Ensure the resulting JSON stays valid (no dangling/missing comma — the object before `bt_meater_exp` must end with `},`).

Verify JSON validity:
Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "import json; json.load(open('wizard/wizard_manifest.json')); print('manifest OK')"`
Expected: `manifest OK`.

- [ ] **Step 5: Run the registry guard to verify it PASSES**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_registry.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Grep for residue**

Run: `git grep -n "bt_meater\b" -- ':!*.png' ':!docs/superpowers/plans/*'`
Expected: remaining hits are ONLY: `common/settings_migration.py` (the migration, handled in Task 4), `probes/bt_meater_exp.py` (its own filename references), the wizard image `bt_meater.png` reference under `bt_meater_exp`'s `image` key (shared image, keep), and the new test files. Confirm NO reference resolves to the deleted `probes/bt_meater.py` as an importable/selectable module. Record the grep output in the commit if anything is surprising.

- [ ] **Step 7: Confirm nothing imports the deleted module**

Run: `git grep -n "bt_meater\b" -- 'probes/*.py' 'common/*.py' | grep -v "bt_meater_exp"`
Expected: only `common/settings_migration.py` migration lines (Task 4). No `import probes.bt_meater` anywhere.

- [ ] **Step 8: Commit**

```bash
cat > /tmp/msg_e3.txt <<'EOF'
refactor(probes): delete bt_meater (bluepy); bt_meater_exp is sole impl

Removes the redundant bluepy Meater module and its selectable wizard
manifest entry. bt_meater_exp (simplepyble) is now the sole Meater
implementation, backed by the shared probes/meater_common protocol math.
Adds a registry guard test so the file and manifest entry cannot silently
return, and bt_meater_exp stays selectable.

Deliberate behavior change: drops the bluepy transport path.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add -A
git commit -F /tmp/msg_e3.txt
```

---

## Task 4: Keep the migration, but repoint the `bt_meater_alt` branch past the deleted module

The `bt_meater → bt_meater_exp` migration is KEPT. But the `bt_meater_alt → bt_meater` branch would leave legacy `alt` users pointing at the now-deleted `bt_meater`. Repoint it directly to `bt_meater_exp` so the migration never yields a dead module name.

**Files:**
- Modify: `common/settings_migration.py:228-233`
- Modify: `tests/unit/probes/test_meater_registry.py` (add a migration-target guard)

**Interfaces:**
- Consumes: `common/settings_migration.py` migration function operating on a `settings` dict with `settings["probe_settings"]["probe_map"]["probe_devices"]` (list of `{"module": ...}`) and `settings["versions"]["build"]`, gated on `prev_ver`.
- Produces: migration mapping where both `bt_meater_alt` and `bt_meater` resolve to `bt_meater_exp`.

- [ ] **Step 1: Write the failing migration-target guard**

Append to `tests/unit/probes/test_meater_registry.py`:

```python
def test_settings_migration_targets_live_modules_only():
    """The v1.9.0 b<=32 Meater migration must never point a device at the
    deleted `bt_meater` module. Both legacy names resolve to bt_meater_exp."""
    import re

    path = os.path.join(REPO_ROOT, "common", "settings_migration.py")
    with open(path) as f:
        src = f.read()

    # Grab the migration block's assignment targets for the two legacy names.
    # Each assignment looks like: ...["module"] = "bt_meater_exp"
    targets = re.findall(r'\["module"\]\s*=\s*"(bt_meater[a-z_]*)"', src)
    assert targets, "migration assignments not found -- did the block move?"
    assert "bt_meater" not in targets, (
        f"migration still assigns the deleted module name 'bt_meater': {targets}"
    )
    assert set(targets) == {"bt_meater_exp"}, targets
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_registry.py::test_settings_migration_targets_live_modules_only -q`
Expected: FAIL — the `bt_meater_alt` branch still assigns `"bt_meater"`, so `targets` contains `"bt_meater"`.

- [ ] **Step 3: Repoint the migration target**

In `common/settings_migration.py`, change the migration block (currently):

```python
        for index, device in enumerate(settings["probe_settings"]["probe_map"]["probe_devices"]):
            if device["module"] == "bt_meater_alt":
                settings["probe_settings"]["probe_map"]["probe_devices"][index]["module"] = "bt_meater"
            elif device["module"] == "bt_meater":
                settings["probe_settings"]["probe_map"]["probe_devices"][index]["module"] = "bt_meater_exp"
```

to:

```python
        for index, device in enumerate(settings["probe_settings"]["probe_map"]["probe_devices"]):
            # Phase E deleted the bluepy `bt_meater` module; steer both legacy
            # names directly to the surviving simplepyble module so the
            # migration never yields a dead module reference.
            if device["module"] in ("bt_meater_alt", "bt_meater"):
                settings["probe_settings"]["probe_map"]["probe_devices"][index]["module"] = "bt_meater_exp"
```

- [ ] **Step 4: Run the migration guard to verify it PASSES**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/test_meater_registry.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Format and lint**

Run: `uvx ruff format common/settings_migration.py tests/unit/probes/test_meater_registry.py && uvx ruff check common/settings_migration.py tests/unit/probes/test_meater_registry.py`
Expected: reformatted/clean.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/msg_e4.txt <<'EOF'
fix(migration): steer legacy Meater devices to bt_meater_exp

Phase E deleted the bluepy `bt_meater` module. The v1.9.0 b<=32 settings
migration previously mapped `bt_meater_alt` -> `bt_meater`, which would now
resolve to a deleted module (falling back to `disabled`). Both legacy names
now migrate straight to `bt_meater_exp`. Migration intent (steer legacy
users to the working module) is preserved.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add common/settings_migration.py tests/unit/probes/test_meater_registry.py
git commit -F /tmp/msg_e4.txt
```

---

## Task 5: Full-suite regression + residue verification

Confirm the whole test suite is green and no dangling `bt_meater` reference remains anywhere that would break loading.

**Files:** none (verification only).

- [ ] **Step 1: Run the Meater tests together**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/probes/ -q`
Expected: PASS — includes `test_meater_protocol.py` (13), `test_meater_common.py` (4), `test_meater_registry.py` (6), plus pre-existing probe tests unchanged.

- [ ] **Step 2: Run the broader manifest/probe-iterating tests**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/ -q`
Expected: PASS. In particular `tests/unit/mcp2210/test_mcp2210_probe_bus.py` (iterates `modules.probes`) and the wizard manifest tests must stay green with the `bt_meater` entry removed.

- [ ] **Step 3: Full suite**

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: PASS (no new failures vs. the pre-Phase-E baseline). If any pre-existing unrelated failure appears, confirm it is present on `massive-reworks-and-new-ui` before Phase E and note it — do not fix out of scope.

- [ ] **Step 4: Final residue grep**

Run: `git grep -n "bt_meater\b" -- ':!*.png' ':!docs/*'`
Expected: hits ONLY in `probes/bt_meater_exp.py` (own filename/docstring), `common/settings_migration.py` (migration source names), and the three `tests/unit/probes/test_meater_*.py` files. Explicitly confirm: NO `import probes.bt_meater` (bare), NO `"bt_meater"` manifest key, NO `probes/bt_meater.py` file.

- [ ] **Step 5: No commit needed**

Verification only. If Steps 1–4 all pass, Phase E is complete on `refactor/meater-dedup`. Hand off per the branch-finishing skill.

---

## Self-Review

**1. Spec coverage:**
- "Make `bt_meater_exp` the sole implementation and delete `bt_meater.py`" → Task 3 (delete file + manifest entry). ✅
- "Extract the shared protocol/math into `probes/meater_common.py` that `bt_meater_exp` consumes (leaving it only its simplepyble transport handler)" → Task 2 (extract math to `meater_common`, `Meater`/`Meater_Pro` inherit, only transport code left). Note the documented deviation: `Meater_Device`/`ReadProbes`/`MeaterProbeHandler` are NOT moved (they are simplepyble-coupled and would break CI-importability of `meater_common`) — flagged in "Interpretation note" and the report. ✅ (with called-out deviation)
- "Keep the settings migration" → Task 4 keeps it, repointing only the dangling `bt_meater_alt` target (required by the deletion). ✅
- "Remove `bt_meater` from the wizard manifest / driver registry" → Task 3 Step 4. ✅
- "unit tests on `convert_to_temperatures`/`ambient_correction` against known byte sequences (no hardware in CI)" → Task 1 + Task 2 tests, importable without BLE. ✅
- "manifest + display_launch/probe-registry load tests confirm no dangling reference" → Task 3 registry guards + Task 5 residue greps and `tests/unit/` run. ✅
- "grep for `bt_meater` residue" → Task 3 Step 6/7, Task 5 Step 4. ✅
- "Prerequisite first commit: characterization/unit tests … capturing today's output … committed separately, THEN refactor" → Task 1 is a standalone commit before Task 2. ✅
- "registry is likely filename/manifest-based … handle manifest/registry removal AND include a verification step" → Task 3 (removal + load/registry guard test) and Task 5 (residue). ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". All code blocks are complete and copied verbatim from the live source or are concrete test code. Real byte fixture (`struct.pack("<6h", 1000, 1100, 1200, 900, 1300, 800)`) and real golden numbers (63.0, 145.4, 31.5, 25.25, 7667.147276395427, 77.45, 83.075, [88.7, 94.325, 99.95, 83.075, 105.575], 479.6967047747142, 895.4540685944855) appear throughout — captured from the live code, not invented. ✅

**3. Type consistency:** Class names `MeaterOriginalProtocol`/`MeaterProProtocol` are identical across Task 2's Interfaces block, the `meater_common.py` source, the `bt_meater_exp` `super().__init__()` bases, and the test imports. Method names (`toCelsius`, `toFahrenheit`, `bytesToInt`, `convertAmbient`, `getTip`, `getTipC`, `getAmbient`, `getAmbientC`, `getTemps`, `get_short`, `ambient_correction`, `convert_to_temperatures`, `toFahrenheitInternals`, `toFahrenheitAmbient`, `getTips`) match the live source and are used consistently in tests. Migration target `bt_meater_exp` is consistent between Task 4's edit and its guard test. ✅

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-18-phaseE-meater-dedup.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
