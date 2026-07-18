# Phase G — grillplat: adopt `SystemCommandsMixin`

**REQUIRED SUB-SKILL for agentic workers:** `superpowers:subagent-driven-development` — dispatch each numbered Task to its own implementation subagent, in order, reviewing between tasks. Do not batch tasks.

## Goal

`grillplat/raspberry_pi_all.py` and `grillplat/prototype.py` each declare a bare `class GrillPlatform:` and re-implement the platform "System / Platform Commands" block inline (~170 / ~166 lines). A shared `grillplat/system_commands.py:SystemCommandsMixin` already exists and is consumed by `x86_numato.py` and `ft232h_relay.py`. Make both remaining platforms extend the mixin and delete **only** the inline methods that are byte-for-byte behavior-identical to the mixin, keeping every method whose behavior differs as an explicit override.

This is a behavior-preserving de-duplication. No output of any platform command may change.

## Architecture

- `SystemCommandsMixin` (target base, UNCHANGED by this phase) provides 9 system methods: `supported_commands`, `check_throttled`, `check_cpu_temp`, `check_wifi_quality`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`, `hardware_info`. Its `check_throttled`/`check_cpu_temp`/`check_wifi_quality`/`hardware_info` are the *portable* (psutil / get_wifi_quality / generic /proc/cpuinfo) variants written for non-Pi hosts.
- Each `GrillPlatform` keeps its own I/O methods (auger/fan/igniter/power/ramp/cleanup) and, after this phase, `class GrillPlatform(SystemCommandsMixin):` — inheriting the shared system methods and overriding only the ones that must behave differently.
- The consuming class supplies `self.logger`; the mixin depends on nothing else.

### CRITICAL FINDINGS — the spec's identity claims are partly WRONG (verified against live code)

The design spec said "delete the 6 identical methods (`supported_commands`, `check_throttled`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`)" and "prototype inherits wholesale." Live-code comparison contradicts this in two places. **The deletion sets below are derived from actual comparison, not the spec.**

1. **`raspberry_pi_all.check_throttled` is NOT identical to the mixin — DO NOT DELETE IT.** The Pi version shells out to `sudo vcgencmd get_throttled` and parses the hex bitmask for real under-voltage/throttle detection; the mixin's version is a hardcoded `{under_voltage: False, throttled: False}` stub for generic hosts. Deleting the Pi copy would silently remove real throttle detection. It is a **third** Pi-specific override to KEEP (alongside `check_cpu_temp` and `hardware_info`). Verified: `subprocess.check_output(["sudo","vcgencmd","get_throttled"])` at `raspberry_pi_all.py:281`.

2. **`prototype` does NOT inherit wholesale — it keeps THREE simulator overrides.** `prototype.check_wifi_quality` returns fake `{60,70,80}`, `prototype.check_cpu_temp` returns a fixed `40.0`, and `prototype.hardware_info` reads three `/proc/cpuinfo` fields. The mixin's versions return *real* wifi/psutil/single-field data. Adopting the mixin for these would change prototype's output (verified empirically: mixin `check_wifi_quality` returned `{54,100,54.0}` vs prototype's `{60,70,80}`; mixin `check_cpu_temp` returns live psutil temp vs prototype's `40.0`). KEEP all three as overrides.

3. **Bonus (safe) — `raspberry_pi_all.check_wifi_quality` IS identical to the mixin** (`return get_wifi_quality(logger=self.logger)`), even though the spec did not list it for deletion. It is included in the Pi deletion set.

4. **`scan_bluetooth` — return value identical, one debug line differs.** Both inline copies add a per-discovered-device `self.logger.debug("scan_bluetooth: Found device ...")` inside the inner `_scan()` that the mixin omits. The returned `data` dict is byte-identical; the only difference is a debug log emitted during a *real* BT scan (never in tests). Treated as a safe deletion with the dropped debug line accepted as an intentional, output-neutral consequence, documented in the commit.

### Resulting per-file plan (verified)

| method | raspberry_pi_all | prototype |
|---|---|---|
| `supported_commands` | DELETE (identical) | DELETE (identical) |
| `check_throttled` | **KEEP** (vcgencmd, differs) | DELETE (stub == mixin stub) |
| `check_cpu_temp` | **KEEP** (vcgencmd, differs) | **KEEP** (fixed 40.0, differs) |
| `check_wifi_quality` | DELETE (identical) | **KEEP** (fake 60/70/80, differs) |
| `check_alive` | DELETE (identical) | DELETE (identical) |
| `scan_bluetooth` | DELETE (return identical; debug line dropped) | DELETE (return identical; debug line dropped) |
| `os_info` | DELETE (identical) | DELETE (identical) |
| `network_info` | DELETE (identical) | DELETE (identical) |
| `hardware_info` | **KEEP** (3-field /proc/cpuinfo, differs) | **KEEP** (3-field /proc/cpuinfo, differs) |

Deletions: 6 per file. Kept overrides: raspberry `{check_throttled, check_cpu_temp, hardware_info}`; prototype `{check_wifi_quality, check_cpu_temp, hardware_info}`.

## Tech Stack

Python 3.14; pytest; ruff; Serena symbolic editing; `uv` for the test venv.

## Global Constraints

- **Python 3.14.** `except (A, B)` is canonical; do not "fix" it to anything else.
- **TEST COMMAND (exact, always):** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`. Bare `python`/`pytest` gives false failures — always `uv run`.
- **Before every commit:** `uvx ruff format <changed files>` then `uvx ruff check <changed files>`.
- **Edits via Serena symbolic tools** (`find_symbol`, `safe_delete_symbol`, `replace_symbol_body`, `insert_*`); use `Edit` only for the `class` declaration line and import lines.
- **Commit with a message file:** `git commit -F <msgfile>` (zsh eats backticks in `-m "...`"). Co-author trailer, exact:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **Behavior-preserving.** If the mixin's version differs from the inline copy for ANY method, that method is NOT part of this phase's deletion — keep it as an override and note why. Deletions are safe ONLY where behavior is identical (return value byte-for-byte; `scan_bluetooth`'s dropped debug line is the single documented exception).
- **SECURITY / SAFETY.** `raspberry_pi_all.py` calls `subprocess.check_output(["sudo","vcgencmd", ...])` (lines 281, 312). Neither file calls `os.system`/`reboot`/`shutdown` (grep-verified), but tests MUST mock `subprocess.check_output` so **no real `sudo vcgencmd` ever runs**, and MUST mock `bleak.BleakScanner.discover` so **no real 5-second Bluetooth scan runs**. All verification runs against fakes/mocks only — never against real hardware paths.
- **Branch:** `refactor/grillplat-mixin`, cut from `massive-reworks-and-new-ui` (the current integration branch; Phases A–D already merged there).

---

## File Structure

```
grillplat/system_commands.py        # TARGET BASE — UNCHANGED this phase (read-only reference)
grillplat/raspberry_pi_all.py       # Task 2: class extends mixin; -6 methods; keep 3 overrides; fix imports
grillplat/prototype.py              # Task 1: class extends mixin; -6 methods; keep 3 overrides; fix imports
grillplat/x86_numato.py             # reference pattern only (class GrillPlatform(SystemCommandsMixin)) — UNCHANGED
grillplat/ft232h_relay.py           # reference pattern only — UNCHANGED
tests/unit/platform/test_prototype_system.py       # Task 0: NEW characterization tests (prototype)
tests/unit/platform/test_raspberry_pi_system.py    # Task 0: NEW characterization tests (raspberry_pi_all)
```

Reference: existing `tests/unit/platform/test_x86_system.py` and `tests/unit/ft232h/test_ft232h_system.py` show the shape of platform-system assertions. `tests/conftest.py:104` `x86_platform` fixture shows the hardware-mock pattern (mocking module attributes then instantiating).

**Import gotcha (verified):** `import grillplat.raspberry_pi_all` FAILS in the test venv because of module-level `from rpi_hardware_pwm import HardwarePWM` (`rpi_hardware_pwm` is Pi-only, not installed). `grillplat.prototype` imports cleanly (`gpiozero` is installed). Therefore the raspberry test module MUST inject a stub `rpi_hardware_pwm` into `sys.modules` *before* importing the platform. Both test modules build a **bare instance** via `object.__new__(GrillPlatform)` + a `logging.getLogger` — the system methods only touch `self.logger`, so no `__init__`/GPIO is needed. (Verified working.)

---

## Task 0 — Characterization tests pinning current inline output (RUN BEFORE ANY DELETION)

Add dedicated system-command tests for both platforms so that "mixin output == prior inline output" is *proven*, and so the kept overrides are pinned against accidental replacement by the mixin. These tests must pass against the **current** (pre-refactor) code, then continue to pass unchanged after Tasks 1 and 2 (the deleted methods resolve to the mixin; the kept ones stay).

### Files
- CREATE `tests/unit/platform/test_prototype_system.py`
- CREATE `tests/unit/platform/test_raspberry_pi_system.py`

### Interfaces
- Both tests exercise the 9 system methods through a bare `GrillPlatform` instance (`object.__new__` + `self.logger`).
- All hardware/OS side-effects are mocked: `subprocess.check_output` (Pi vcgencmd), `bleak.BleakScanner.discover` (BT scan). `os_info`/`network_info`/`hardware_info`/psutil read the host but are side-effect-free and safe to call.

### Steps

- [ ] **0.1** Create `tests/unit/platform/test_prototype_system.py`:

```python
import logging
from unittest import mock

import grillplat.prototype as proto


def _bare():
    # System methods only need self.logger; skip __init__ (no GPIO on host).
    obj = object.__new__(proto.GrillPlatform)
    obj.logger = logging.getLogger("test.prototype")
    return obj


def test_supported_commands_lists_all_nine():
    cmds = _bare().supported_commands([])["data"]["supported_cmds"]
    for name in (
        "check_throttled", "check_wifi_quality", "check_cpu_temp", "supported_commands",
        "check_alive", "scan_bluetooth", "os_info", "network_info", "hardware_info",
    ):
        assert name in cmds


def test_check_throttled_stub_all_false():
    data = _bare().check_throttled([])
    assert data["result"] == "OK"
    assert data["message"] == "No under-voltage or throttling detected."
    assert data["data"] == {"cpu_under_voltage": False, "cpu_throttled": False}


def test_check_alive_ok():
    assert _bare().check_alive([]) == {
        "result": "OK", "message": "The control script is running.", "data": {},
    }


def test_os_info_ok_shape():
    data = _bare().os_info([])
    assert data["result"] == "OK"
    assert data["message"] == "OS information retrieved successfully."
    assert isinstance(data["data"], dict)


def test_network_info_ok_shape():
    data = _bare().network_info([])
    assert data["result"] == "OK"
    assert data["message"] == "Network information retrieved successfully."
    assert isinstance(data["data"], dict)


def test_scan_bluetooth_no_devices(monkeypatch):
    async def _no_devices(*a, **k):
        return []

    fake_scanner = mock.Mock()
    fake_scanner.discover = _no_devices
    monkeypatch.setitem(__import__("sys").modules, "bleak",
                        mock.Mock(BleakScanner=fake_scanner))
    data = _bare().scan_bluetooth([])
    assert data["result"] == "OK"
    assert data["data"]["bt_devices"] == []


# --- KEPT simulator overrides: these MUST stay prototype-specific ---

def test_check_wifi_quality_is_fake_constant():
    # Prototype override returns fixed simulator values (NOT get_wifi_quality()).
    assert _bare().check_wifi_quality([])["data"] == {
        "wifi_quality_value": 60, "wifi_quality_max": 70, "wifi_quality_percentage": 80,
    }


def test_check_cpu_temp_is_fixed_40():
    # Prototype override returns a constant, NOT a live psutil reading.
    assert _bare().check_cpu_temp([])["data"]["cpu_temp"] == 40.0


def test_hardware_info_populates_model_name():
    # Prototype override reads /proc/cpuinfo (3 fields); the mixin's variant
    # would leave model/hardware as "Unknown". Pin that it is NOT the mixin.
    info = _bare().hardware_info([])
    assert info["result"] == "OK"
    assert "cpu_info" in info["data"]
    assert "model" in info["data"]["cpu_info"]  # key present in the 3-field variant
```

- [ ] **0.2** Create `tests/unit/platform/test_raspberry_pi_system.py`. Note the `rpi_hardware_pwm` sys.modules stub MUST precede the platform import:

```python
import logging
import sys
import types
from unittest import mock

# raspberry_pi_all imports `from rpi_hardware_pwm import HardwarePWM` at module
# load; that package is Pi-only and absent in the test venv. Stub it so the
# module imports on a generic host. (gpiozero IS installed.)
if "rpi_hardware_pwm" not in sys.modules:
    _stub = types.ModuleType("rpi_hardware_pwm")
    _stub.HardwarePWM = type("HardwarePWM", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["rpi_hardware_pwm"] = _stub

import grillplat.raspberry_pi_all as rpi  # noqa: E402


def _bare():
    obj = object.__new__(rpi.GrillPlatform)
    obj.logger = logging.getLogger("test.rpi")
    return obj


def test_supported_commands_lists_all_nine():
    cmds = _bare().supported_commands([])["data"]["supported_cmds"]
    for name in (
        "check_throttled", "check_wifi_quality", "check_cpu_temp", "supported_commands",
        "check_alive", "scan_bluetooth", "os_info", "network_info", "hardware_info",
    ):
        assert name in cmds


def test_check_alive_ok():
    assert _bare().check_alive([]) == {
        "result": "OK", "message": "The control script is running.", "data": {},
    }


def test_os_info_ok_shape():
    data = _bare().os_info([])
    assert data["result"] == "OK"
    assert data["message"] == "OS information retrieved successfully."


def test_network_info_ok_shape():
    data = _bare().network_info([])
    assert data["result"] == "OK"
    assert data["message"] == "Network information retrieved successfully."


def test_check_wifi_quality_delegates_to_common(monkeypatch):
    # Pi version is identical to the mixin: `return get_wifi_quality(logger=...)`.
    sentinel = {"result": "OK", "message": "x", "data": {"wifi_quality_value": 1}}
    monkeypatch.setattr(rpi, "get_wifi_quality", lambda logger=None: sentinel)
    assert _bare().check_wifi_quality([]) is sentinel


def test_scan_bluetooth_no_devices(monkeypatch):
    async def _no_devices(*a, **k):
        return []

    fake_scanner = mock.Mock()
    fake_scanner.discover = _no_devices
    monkeypatch.setitem(sys.modules, "bleak", mock.Mock(BleakScanner=fake_scanner))
    data = _bare().scan_bluetooth([])
    assert data["result"] == "OK"
    assert data["data"]["bt_devices"] == []


# --- KEPT Pi-specific overrides: MUST stay vcgencmd/proc-based (NOT the mixin) ---

def test_check_throttled_parses_vcgencmd(monkeypatch):
    # Under-voltage bit (0x10000) set -> WARNING. Proves the vcgencmd override,
    # not the mixin's hardcoded-False stub, is in effect. subprocess mocked:
    # no real `sudo vcgencmd` runs.
    monkeypatch.setattr(
        rpi.subprocess, "check_output", lambda *a, **k: b"throttled=0x10000"
    )
    data = _bare().check_throttled([])
    assert data["result"] == "OK"
    assert data["data"]["cpu_under_voltage"] is True
    assert data["data"]["cpu_throttled"] is False


def test_check_throttled_clean(monkeypatch):
    monkeypatch.setattr(
        rpi.subprocess, "check_output", lambda *a, **k: b"throttled=0x0"
    )
    data = _bare().check_throttled([])
    assert data["data"] == {"cpu_under_voltage": False, "cpu_throttled": False}


def test_check_cpu_temp_parses_vcgencmd(monkeypatch):
    # Proves the vcgencmd override (not the mixin's psutil variant) is in effect.
    monkeypatch.setattr(
        rpi.subprocess, "check_output", lambda *a, **k: b"temp=42.0'C\n"
    )
    data = _bare().check_cpu_temp([])
    assert data["result"] == "OK"
    assert data["data"]["cpu_temp"] == 42.0


def test_hardware_info_populates_model_name():
    info = _bare().hardware_info([])
    assert info["result"] == "OK"
    assert "model" in info["data"]["cpu_info"]  # 3-field variant, not the mixin
```

- [ ] **0.3** Run the new tests against **current** (pre-refactor) code — they must all pass now, proving they characterize existing behavior:

  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/platform/test_prototype_system.py tests/unit/platform/test_raspberry_pi_system.py -q`

  **Expected:** all tests pass (roughly 19 passed).

- [ ] **0.4** `uvx ruff format tests/unit/platform/test_prototype_system.py tests/unit/platform/test_raspberry_pi_system.py` then `uvx ruff check <same>`. Expected: reformatted/clean, no lint errors.

- [ ] **0.5** Commit. Message file body:
  ```
  test(grillplat): characterize prototype & raspberry_pi system commands

  Pin the current output of all 9 System/Platform Commands for both bare
  GrillPlatform classes before adopting SystemCommandsMixin. Includes
  overrides that must NOT be replaced by the mixin: raspberry check_throttled
  & check_cpu_temp (vcgencmd) and hardware_info; prototype check_wifi_quality
  (fake 60/70/80), check_cpu_temp (fixed 40.0) and hardware_info. subprocess
  and bleak are mocked so no real vcgencmd or Bluetooth scan runs.
  ```
  Then `git commit -F <msgfile>` with the co-author trailer.

---

## Task 1 — `prototype.py` adopts `SystemCommandsMixin`

### Files
- `grillplat/prototype.py`

### Interfaces
- **Provides (from mixin, now consumed):** `supported_commands`, `check_throttled`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`.
- **Consumer keeps overriding:** `check_wifi_quality`, `check_cpu_temp`, `hardware_info` (simulator values differ from the mixin).
- Mixin requires only `self.logger` — already set in `__init__`.

### Steps

- [ ] **1.1** Add the import. In the imports block (currently `from common.common import is_float` / `from common.system import get_os_info`), replace the now-unneeded `common.system` import and add the mixin. Exact Edit:
  - OLD:
    ```python
    from gpiozero.threads import GPIOThread
    from common.common import is_float
    from common.system import get_os_info
    ```
  - NEW:
    ```python
    from gpiozero.threads import GPIOThread
    from common.common import is_float
    from grillplat.system_commands import SystemCommandsMixin
    ```
  Rationale: `get_os_info` was used only by the deleted `os_info`; `is_float` is still used by the kept `check_cpu_temp`; `GPIOThread` still used by `_start_ramp`.

- [ ] **1.2** Change the class declaration (Edit):
  - OLD: `class GrillPlatform:`
  - NEW: `class GrillPlatform(SystemCommandsMixin):`

- [ ] **1.3** Delete the 6 inline methods that are behavior-identical to the mixin, via Serena `safe_delete_symbol` (symbol path `GrillPlatform/<method>`), one per method:
  - `supported_commands`
  - `check_throttled`  (prototype stub == mixin stub, verified equal)
  - `check_alive`
  - `scan_bluetooth`  (return identical; per-device debug line dropped — intentional, output-neutral)
  - `os_info`
  - `network_info`

- [ ] **1.4** DO NOT touch these — they stay as prototype overrides (their simulator output differs from the mixin):
  - `check_wifi_quality` → returns `{"wifi_quality_value": 60, "wifi_quality_max": 70, "wifi_quality_percentage": 80}`
  - `check_cpu_temp` → returns fixed `cpu_temp` `40.0`
  - `hardware_info` → reads 3 `/proc/cpuinfo` fields
  Confirm all three remain present and unmodified after the deletions.

- [ ] **1.5** Run the prototype characterization tests + the full platform suite:

  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/platform/test_prototype_system.py tests/unit/platform -q`

  **Expected:** all pass. The 6 deleted methods now resolve through `SystemCommandsMixin` and produce the pinned output; the 3 overrides still pass their pins.

- [ ] **1.6** Sanity-check the module still imports and the mixin is in the MRO:

  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "import grillplat.prototype as p; from grillplat.system_commands import SystemCommandsMixin as M; assert issubclass(p.GrillPlatform, M); assert 'os_info' not in vars(p.GrillPlatform); assert 'check_cpu_temp' in vars(p.GrillPlatform); print('OK')"`

  **Expected:** prints `OK` (mixin inherited; `os_info` no longer defined on the class; `check_cpu_temp` override retained).

- [ ] **1.7** `uvx ruff format grillplat/prototype.py` then `uvx ruff check grillplat/prototype.py`. Expected: clean, no unused-import warning for `get_os_info` (it was removed).

- [ ] **1.8** Commit. Message file body:
  ```
  refactor(grillplat): prototype extends SystemCommandsMixin

  Delete 6 inline system methods identical to the mixin (supported_commands,
  check_throttled, check_alive, scan_bluetooth, os_info, network_info) and
  inherit them. Keep the 3 simulator overrides whose output differs from the
  mixin: check_wifi_quality (fake 60/70/80), check_cpu_temp (fixed 40.0),
  hardware_info (3-field /proc/cpuinfo). scan_bluetooth's per-device debug log
  is intentionally dropped; the returned data is unchanged. Drop the now-unused
  get_os_info import.
  ```
  `git commit -F <msgfile>` + co-author trailer.

---

## Task 2 — `raspberry_pi_all.py` adopts `SystemCommandsMixin`

### Files
- `grillplat/raspberry_pi_all.py`

### Interfaces
- **Provides (from mixin, now consumed):** `supported_commands`, `check_wifi_quality`, `check_alive`, `scan_bluetooth`, `os_info`, `network_info`.
- **Consumer keeps overriding (THREE — note `check_throttled`):** `check_throttled` (vcgencmd), `check_cpu_temp` (vcgencmd), `hardware_info` (3-field /proc/cpuinfo).
- Mixin requires only `self.logger` — already set in `__init__`.

### Steps

- [ ] **2.1** Fix imports (Edit). `get_os_info` (used only by deleted `os_info`) and `get_wifi_quality` (used only by deleted `check_wifi_quality`) become unused; `subprocess` stays (kept `check_throttled` + `check_cpu_temp`); `is_float` stays (kept `check_cpu_temp`). Add the mixin import.
  - OLD:
    ```python
    import logging
    import subprocess
    from common.common import is_float
    from common.system import get_os_info, get_wifi_quality
    from gpiozero import OutputDevice
    ```
  - NEW:
    ```python
    import logging
    import subprocess
    from common.common import is_float
    from grillplat.system_commands import SystemCommandsMixin
    from gpiozero import OutputDevice
    ```

- [ ] **2.2** Change the class declaration (Edit):
  - OLD: `class GrillPlatform:`
  - NEW: `class GrillPlatform(SystemCommandsMixin):`

- [ ] **2.3** Delete the 6 inline methods that are behavior-identical to the mixin, via Serena `safe_delete_symbol` (`GrillPlatform/<method>`):
  - `supported_commands`
  - `check_wifi_quality`  (identical: `return get_wifi_quality(logger=self.logger)` — safe even though the spec omitted it)
  - `check_alive`
  - `scan_bluetooth`  (return identical; per-device debug line dropped — intentional, output-neutral)
  - `os_info`
  - `network_info`

- [ ] **2.4** DO NOT delete these — keep them as Pi-specific overrides:
  - `check_throttled` → **KEEP.** vcgencmd `get_throttled` hex-bitmask parse; differs from the mixin's hardcoded-False stub. (This is the spec's error — the spec told us to delete it.)
  - `check_cpu_temp` → **KEEP.** vcgencmd `measure_temp`; differs from the mixin's psutil variant.
  - `hardware_info` → **KEEP.** 3-field `/proc/cpuinfo`; differs from the mixin's single-field variant.
  Confirm all three remain present and unmodified.

- [ ] **2.5** Run the raspberry characterization tests + the full platform suite:

  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/platform/test_raspberry_pi_system.py tests/unit/platform -q`

  **Expected:** all pass. The `check_throttled`/`check_cpu_temp` vcgencmd pins prove the overrides survived; the 6 deleted methods resolve through the mixin.

- [ ] **2.6** Sanity-check import + MRO + retained overrides (with the same `rpi_hardware_pwm` stub the test uses):

  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "import sys,types; m=types.ModuleType('rpi_hardware_pwm'); m.HardwarePWM=type('H',(),{}); sys.modules['rpi_hardware_pwm']=m; import grillplat.raspberry_pi_all as r; from grillplat.system_commands import SystemCommandsMixin as M; assert issubclass(r.GrillPlatform,M); v=vars(r.GrillPlatform); assert 'os_info' not in v and 'check_wifi_quality' not in v; assert 'check_throttled' in v and 'check_cpu_temp' in v and 'hardware_info' in v; print('OK')"`

  **Expected:** prints `OK` (mixin inherited; `os_info`/`check_wifi_quality` gone; the 3 Pi overrides retained).

- [ ] **2.7** `uvx ruff format grillplat/raspberry_pi_all.py` then `uvx ruff check grillplat/raspberry_pi_all.py`. Expected: clean; no unused-import warnings for `get_os_info`/`get_wifi_quality` (removed) and none for `subprocess`/`is_float` (still used).

- [ ] **2.8** Commit. Message file body:
  ```
  refactor(grillplat): raspberry_pi_all extends SystemCommandsMixin

  Delete 6 inline system methods identical to the mixin (supported_commands,
  check_wifi_quality, check_alive, scan_bluetooth, os_info, network_info) and
  inherit them. KEEP three Pi-specific overrides whose behavior differs from
  the mixin: check_throttled and check_cpu_temp (both vcgencmd) and
  hardware_info (3-field /proc/cpuinfo). NOTE: the design spec incorrectly
  listed check_throttled as a safe delete; the Pi version does real
  under-voltage/throttle detection via `sudo vcgencmd get_throttled` and is
  retained. scan_bluetooth's per-device debug log is intentionally dropped;
  returned data is unchanged. Drop the now-unused get_os_info/get_wifi_quality
  imports.
  ```
  `git commit -F <msgfile>` + co-author trailer.

---

## Task 3 — Full-suite regression + branch wrap-up

### Steps

- [ ] **3.1** Run the whole platform + ft232h suites (unchanged mixin consumers must still pass, confirming no cross-impact):

  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/platform tests/unit/ft232h -q`

  **Expected:** all pass, including the pre-existing `test_x86_system.py` and `test_ft232h_system.py`.

- [ ] **3.2** Run the full unit suite to confirm no import-time breakage elsewhere (e.g. bootstrap/wizard modules that reference the platforms):

  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit -q`

  **Expected:** green (same pass count as before + the ~19 new Task-0 tests).

- [ ] **3.3** Net-line-count check (informational): `git diff --stat massive-reworks-and-new-ui...refactor/grillplat-mixin` should show `raspberry_pi_all.py` and `prototype.py` net-shrinking (roughly −60 to −90 lines each after keeping overrides), plus two new test files.

- [ ] **3.4** Finish the branch per `superpowers:finishing-a-development-branch` (merge to `massive-reworks-and-new-ui` or open a PR, per the human's preference). **Rollback:** if anything regresses, revert the branch — the change is isolated to two platform files and two new test files.

---

## Self-review checklist (fix inline before dispatching Task 0)

- [x] **Every "delete" method verified byte-for-byte equal to the mixin** (not assumed from spec). `check_throttled` on **raspberry** flagged as NOT equal → moved to KEEP. `check_wifi_quality`/`check_cpu_temp`/`hardware_info` on **prototype** flagged as NOT equal → KEEP. `check_wifi_quality` on **raspberry** verified equal → added to deletes.
- [x] **Kept-override sets are asymmetric and correct:** raspberry `{check_throttled, check_cpu_temp, hardware_info}`; prototype `{check_wifi_quality, check_cpu_temp, hardware_info}`. Reflected in Steps 1.4 / 2.4 and in the char-test pins.
- [x] **`scan_bluetooth` dropped-debug-line difference** surfaced explicitly and accepted as output-neutral (not hidden).
- [x] **Import fixups match the retained methods:** prototype drops `get_os_info` only (keeps `is_float`, `GPIOThread`); raspberry drops `get_os_info` + `get_wifi_quality` only (keeps `subprocess`, `is_float`). No dangling/unused imports; no missing ones.
- [x] **SAFETY:** vcgencmd `subprocess.check_output` and `bleak.BleakScanner.discover` are mocked in every test that touches them — no real `sudo vcgencmd`, no real BT scan. No `os.system`/`reboot`/`shutdown` in either file (grep-verified).
- [x] **Import gotcha handled:** raspberry test stubs `sys.modules["rpi_hardware_pwm"]` before import; both tests use bare `object.__new__` instances needing only `self.logger` (empirically verified to work).
- [x] **Characterization tests run BEFORE deletion (Task 0)** and are unchanged by Tasks 1–2, so "mixin output == prior inline output" is proven, not assumed.
- [x] **Mixin (`system_commands.py`) is not modified** — it is a shared base already relied on by `x86_numato`/`ft232h_relay`; Task 3.1 re-runs their suites to confirm no cross-impact.
- [x] Every Task: Files, Interfaces, bite-sized checkbox steps with exact code, exact test command + expected output, ruff, and a `-F`-file commit with the co-author trailer.
