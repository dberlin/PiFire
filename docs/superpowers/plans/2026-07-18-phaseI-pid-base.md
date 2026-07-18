# Phase I — `PIDControllerBase` + dead controller-API removal

## For agentic workers

**REQUIRED SUB-SKILL → `superpowers:subagent-driven-development`.** Execute the numbered
Tasks in order, one commit per task, each on branch `refactor/pid-base`. After each
migration task, run the exact test command and confirm green before proceeding. Do NOT
batch variant migrations — one variant per task keeps a regression bisectable to a single
commit.

## Goal

Two changes in one branch, gated by the same characterization net:

1. **Remove the dead controller dispatch surface** (`function_list`, `supported_functions`,
   `set_config`, `get_config`, `set_cycle_data`, `set_units`, `set_gains`, `get_k`) from
   `ControllerBase` and every controller. It is designed-but-never-wired: verified repo-wide
   to have zero callers (runtime reconfig rebuilds the controller object via
   `runner._build_core`, never `set_config`). Human-approved full removal.
2. **Introduce `PIDControllerBase(ControllerBase)`** owning the surface still shared by the six
   PID variants after the dead methods are gone. Each variant keeps only its `update()` plus
   the genuinely-different pieces (percent-PB / parallel-form / auto-center / debug-log).

Behavior of every variant's `update()` must be byte/value-identical before and after, and all
six must remain user-selectable via `controller/controllers.json`.

## Architecture

Controllers are loaded dynamically in `controller/runtime/runner.py:_build_core`:

```python
module = importlib.import_module(f"controller.{controller_type}")   # controller_type == module_name from controllers.json
core = module.Controller(settings["controller"]["config"][controller_type], units, cycle_data)
```

So each variant module MUST keep a top-level class named `Controller`. Runtime config changes
go through `HoldMode.reconfigure()` → `runner.reconfigure()` → `_build_core()`, which
**constructs a brand-new `Controller` from fresh config and discards the old core** — it never
calls `set_config`/`set_gains`/etc. That is why the entire `function_list`/`supported_functions`
introspection surface and its sibling setters are dead and removable.

The live controller API the runtime actually uses: `__init__`, `update`, `set_target`,
`get_control_period`, `commands_fan`, `wants_async` (+ module-level
`normalize_controller_output`). Those are KEPT.

`PIDControllerBase` lives in a new module `controller/pid_base.py`, is NOT named `Controller`,
and is NOT in `controllers.json` — it can never be selected. Registration is purely
filename/`module_name` based; we touch neither filenames nor `controllers.json`.

Class hierarchy — before: all six `class Controller(ControllerBase)`. After:
all six `class Controller(PIDControllerBase)`, and `class PIDControllerBase(ControllerBase)`.

## Tech Stack

Python 3.14. `pytest`. `uv` / `uvx ruff`. Serena symbolic tools for edits.

## Global Constraints

- Python 3.14. `except (A, B)` is canonical; do NOT "fix" bare `except A, B`.
- **TEST COMMAND (exact, always):**
  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`
- Before every commit: `uvx ruff format <changed>` then `uvx ruff check <changed>`.
- Edits via Serena symbolic tools (`create_text_file`, `safe_delete_symbol`,
  `replace_symbol_body`, `replace_content` for class-header / import lines).
- Commit with `git commit -F <msgfile>` (zsh eats backticks in `-m`). Co-author trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Behavior-preserving, with ONE approved API removal.** Each variant's `update()` output
  MUST be value-identical for the fixed input series in Task 1, before and after. The dead-code
  removal (change #1) deletes a designed-but-unused public-looking introspection API
  (`supported_functions`/`function_list`) and its dead sibling setters — this is intended and
  human-approved (verified zero callers repo-wide), NOT an oversight. It changes no runtime
  behavior because nothing ever called those methods. Everything else is a pure refactor: only
  methods PROVEN identical across all six variants get pulled into `PIDControllerBase`; a method
  that differs in even one variant stays overridden in that variant.
- All 6 variants remain user-selectable via `controller/controllers.json` after the refactor;
  the final task re-verifies each `controller.<module_name>.Controller` still constructs.

---

## Findings (LIVE code, verified this session — the crux of the plan)

### A. The dead dispatch surface (change #1)

Verified repo-wide (py/js/html/json, dynamic `getattr`, tests) — ZERO callers:

| symbol | defined in | notes |
|---|---|---|
| `supported_functions()` | `base.py` | nothing reads it |
| `function_list` | `base.py` + 6 PID `__init__` appends + `mpc.py:86` | only built/returned, never consumed |
| `set_config` | `base.py`; overridden in `pid`, `pid_clamping`, `pid_clamping_percent_pb`, `pid_parallel` | only internal `super().set_config()` chains |
| `get_config` | `base.py` | none |
| `set_cycle_data` | `base.py` | runtime uses `cycle_data` as a plain dict |
| `set_units` | `base.py` | the `_cmd_set_units` grep hits are an unrelated settings handler |
| `set_gains` | all 6 PID variants | none |
| `get_k` | all 6 PID variants | none |

Controllers inheriting `ControllerBase`: the 6 PID variants, plus **`mpc`** (only touches
`function_list` via the `mpc.py:86` append of the *live* `get_control_period` — delete that one
line, KEEP the method), and **`fuzzy`** / **`ml`** (inherit-only — cleaned automatically when the
base loses the methods; they have no dedicated tests, so a construct/import smoke is their net).

`ControllerBase` KEEPS: `__init__` (minus the `function_list` line), `update`, `set_target`,
`get_control_period`, `commands_fan`, `wants_async`, and module-level `normalize_controller_output`.

### B. Per-method identity across the six PID variants (change #2)

After the dead methods (`set_gains`, `get_k`, `set_config`) are gone, only `_calculate_gains`
and `set_target` remain candidates for the shared base:

| method | pid | pid_clamping | percent_pb | pid_ac | pid_parallel | pid_sp |
|---|---|---|---|---|---|---|
| `_calculate_gains` | ✅ std | ✳ std **+ eventLogger.debug** | ✗ `(self)` no-arg, **percent** form | ✅ std | ✗ **parallel** form `-1*kp` | ✅ std |
| `set_target` | ✅ reset | ✅ reset | ✗ **calls `_calculate_gains()`** | ✗ **center/units** | ✅ reset | ✗ **center/units** |
| `update` | keep | keep | keep | keep | keep | keep |

Legend: ✅ = byte/value-identical to the base default we adopt; ✳ = identical logic plus a
debug-log side effect (kept overridden to preserve the log); ✗ = genuinely different, stays
overridden.

`PIDControllerBase` adopts the *standard PB* forms of `_calculate_gains` and `set_target` (the
`pid.py` canonical versions) as base defaults; variants inherit each or override exactly where
the table shows ✗/✳. `_calculate_gains` stays live because `__init__` (all variants) and
`set_target` (percent_pb) call it. `set_target` stays live because `runner` calls it.

**`PIDControllerBase` needs NO `__init__`** — with `function_list` gone there is nothing to add,
so each variant's `super().__init__(config, units, cycle_data)` resolves to
`ControllerBase.__init__` through the MRO, unchanged.

### C. Footguns (still relevant after cleanup)

1. `pid_ac.__init__` uses `self.pb = config["PB"]` (hard key → `KeyError` without `PB`),
   whereas `pid_sp` uses `config.get("PB", 60.0)`. The Task 1 test constructs each variant with
   its real `controllers.json` default config (below), not an empty dict.
2. `pid`/`pid_ac`/`pid_parallel` compute `inter_max`/gains in `__init__` (all variants do), so the
   rebuild path stays correct after `set_gains`/`set_config` are deleted — no live path read those.
   The previously-flagged "pid_sp dead `inter_max` write in `set_gains`" and "don't define
   `set_config` on the base to preserve pid_ac/pid_sp inheritance" concerns are now **moot**:
   `set_gains` and `set_config` are deleted everywhere (dead), so there is no `set_config` on any
   class and nothing to preserve.

`update()` is unique to every variant and is NEVER touched.

---

## File Structure

```
controller/base.py                                   (MODIFIED — remove dead dispatch surface)
controller/pid_base.py                               (NEW — PIDControllerBase)
controller/pid.py                                     (MODIFIED — extends PIDControllerBase)
controller/pid_clamping.py                            (MODIFIED)
controller/pid_clamping_percent_pb.py                 (MODIFIED)
controller/pid_ac.py                                  (MODIFIED)
controller/pid_parallel.py                            (MODIFIED)
controller/pid_sp.py                                  (MODIFIED)
controller/mpc.py                                     (MODIFIED — drop function_list.append line)
controller/fuzzy.py, controller/ml.py                 (UNCHANGED — inherit-only, auto-cleaned)
controller/controllers.json                           (UNCHANGED — verify only, final task)
tests/characterization/test_pid_variants_golden.py    (NEW — Task 1)
tests/unit/controller/__init__.py                     (NEW — empty package marker)
tests/unit/controller/test_controller_construct_smoke.py (NEW — Task 2, net for mpc/fuzzy/ml)
```

Per-variant default config (from `controllers.json`, used by the Task 1 test):

```python
PID_CONFIGS = {
    "pid":                     {"PB": 60.0,  "Ti": 180.0, "Td": 45.0, "center": 0.5},
    "pid_clamping":            {"PB": 100.0, "Ti": 180.0, "Td": 45.0},
    "pid_clamping_percent_pb": {"PB": 42.0,  "Ti": 180.0, "Td": 45.0},
    "pid_ac":                  {"PB": 60.0,  "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "pid_parallel":            {"Kp": 0.01,  "Ki": 0.000055, "Kd": 0.45, "Clamping": True},
    "pid_sp":                  {"PB": 60.0,  "Ti": 180.0, "Td": 45.0, "stable_window": 12,
                                "center_factor": 0.0010, "tau": 115, "theta": 65},
}
```

---

## Task 1 — Characterization test pinning all 6 variants' `update()` output [COMMIT FIRST]

**Why first:** No existing test pins any PID variant's `update()` output.
`tests/characterization/test_controller_loop_golden.py` covers only the OUTER loop
orchestration. This test is the safety net for BOTH the dead-code removal (Task 2) and every
base-extraction task, so it lands (green, against current code) before any change.

**Files:** `tests/characterization/test_pid_variants_golden.py` (NEW).

**Interfaces:** Consumes only `controller.<module>.Controller` construction + public
`set_target(set_point)` / `update(current)`. No knowledge of internals.

**Determinism:** `update()` uses `time.time()`; patch `time.time` with a manually-advanced
clock so `dt` is constant. All modules call `time.time()` via attribute lookup on the stdlib
`time` module, so a single `monkeypatch.setattr(time, "time", clock)` covers `pid_base` and
every variant. Advance `clock.t` by a fixed step before each `update()`.

**Steps:**

1. Create the test file:

```python
"""Golden-master characterization for the six PID variants' update() output.

Pins each variant's update() series for a fixed input under a controlled clock,
so the PIDControllerBase refactor + dead-API removal (Phase I) are provably
behavior-preserving. METHOD: run-then-freeze -- the GOLDEN dict below was captured
from the CURRENT (pre-refactor) code and must not change when methods move into
PIDControllerBase or when the dead dispatch surface is deleted.
"""

import time
import importlib
import pytest

PID_CONFIGS = {
    "pid":                     {"PB": 60.0,  "Ti": 180.0, "Td": 45.0, "center": 0.5},
    "pid_clamping":            {"PB": 100.0, "Ti": 180.0, "Td": 45.0},
    "pid_clamping_percent_pb": {"PB": 42.0,  "Ti": 180.0, "Td": 45.0},
    "pid_ac":                  {"PB": 60.0,  "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "pid_parallel":            {"Kp": 0.01,  "Ki": 0.000055, "Kd": 0.45, "Clamping": True},
    "pid_sp":                  {"PB": 60.0,  "Ti": 180.0, "Td": 45.0, "stable_window": 12,
                                "center_factor": 0.0010, "tau": 115, "theta": 65},
}

CYCLE_DATA = {"HoldCycleTime": 20}
SERIES = [150, 160, 180, 200, 205, 210, 215, 218, 220, 221]
SETPOINT = 220.0
STEP = 20.0
T0 = 1000.0


class _Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def _run_variant(module_name, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)
    mod = importlib.import_module(f"controller.{module_name}")
    c = mod.Controller(dict(PID_CONFIGS[module_name]), "F", dict(CYCLE_DATA))
    c.set_target(SETPOINT)
    out = []
    for i, current in enumerate(SERIES, 1):
        clock.t = T0 + i * STEP
        out.append(round(float(c.update(current)), 6))
    return out


# GOLDEN: captured from pre-refactor code (see step 2). Do NOT hand-edit after capture.
GOLDEN = {
    "pid": [...],
    "pid_clamping": [...],
    "pid_clamping_percent_pb": [...],
    "pid_ac": [...],
    "pid_parallel": [...],
    "pid_sp": [...],
}


@pytest.mark.parametrize("module_name", list(PID_CONFIGS))
def test_pid_variant_update_series_is_stable(module_name, monkeypatch):
    assert _run_variant(module_name, monkeypatch) == GOLDEN[module_name]
```

2. Capture the golden values from CURRENT code. Temporarily print instead of assert (or run
   the helper in a scratch script) and paste the exact lists into `GOLDEN`. The values
   observed this session (pre-refactor) are:

```python
GOLDEN = {
    "pid":                     [1.796296, 1.365741, 0.731481, 0.435185, 0.94213, 0.877315, 0.803241, 0.831944, 0.836111, 0.855093],
    "pid_clamping":            [-0.175, -0.258333, -0.638889, -0.816667, -0.5125, -0.551389, -0.595833, -0.578611, -0.576111, -0.564722],
    "pid_clamping_percent_pb": [-0.189394, -0.279582, -0.691438, -0.883838, -0.554654, -0.596741, -0.644841, -0.626203, -0.623497, -0.611171],
    "pid_ac":                  [1.0, 0.956111, 0.210741, -0.15963, 0.310278, 0.245463, 0.171389, 0.200093, 0.204259, 0.223241],
    "pid_parallel":            [-0.168, -0.252, -0.633, -0.811, -0.507, -0.546, -0.5905, -0.5733, -0.5708, -0.5594],
    "pid_sp":                  [1.0, 0.665488, -0.370505, -0.740875, 0.164966, 0.095348, 0.01647, 0.098495, 0.128841, 0.174964],
}
```

   Re-run and confirm the test discovers these itself (do not trust the paste — regenerate to
   be safe; if a float differs at the 6th decimal on this machine, use the machine's value).

**Test:**
`timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/characterization/test_pid_variants_golden.py -q`
Expected: `6 passed`.

**Commit** (`uvx ruff format`/`check` the new file first):
`test(controller): pin all six PID variants' update() output series`

---

## Task 2 — Remove the dead controller dispatch surface [pure deletion]

**Goal:** Delete the designed-but-unused dispatch API from `ControllerBase` and every
controller, in one reviewable commit. Nothing calls these (verified repo-wide), so Task 1's
golden must stay green byte-for-byte.

**Files:** `controller/base.py`, all 6 PID variants, `controller/mpc.py`,
`tests/unit/controller/__init__.py` (new), `tests/unit/controller/test_controller_construct_smoke.py` (new).

**Steps:**

1. `controller/base.py` — `safe_delete_symbol` the methods `supported_functions`, `set_config`,
   `get_config`, `set_cycle_data`, `set_units`. In `ControllerBase.__init__`, delete the line
   `self.function_list = ["update", "set_target", "get_config", "set_config", "set_cycle_data", "set_units"]`.
   KEEP `__init__` (now just the three `self.config/units/cycle_data` assignments), `update`,
   `set_target`, `get_control_period`, `commands_fan`, `wants_async`, and the module-level
   `normalize_controller_output`.

2. In EACH of the 6 PID variants (`pid`, `pid_clamping`, `pid_clamping_percent_pb`, `pid_ac`,
   `pid_parallel`, `pid_sp`): delete the two `__init__` lines
   `self.function_list.append("set_gains")` / `self.function_list.append("get_k")`;
   `safe_delete_symbol` `set_gains` and `get_k`; and `safe_delete_symbol` `set_config` where it
   exists (`pid`, `pid_clamping`, `pid_clamping_percent_pb`, `pid_parallel` — `pid_ac`/`pid_sp`
   have none). Do NOT touch `_calculate_gains`, `set_target`, `update`, or anything else in
   `__init__`.

3. `controller/mpc.py:86` — delete the single line
   `self.function_list.append("get_control_period")`. KEEP the `get_control_period` method
   (it is live, called by `runner`).

4. Add the construct/import smoke (the net for the untested non-PID controllers):

```python
# tests/unit/controller/test_controller_construct_smoke.py
"""Smoke: every ControllerBase subclass still imports/constructs after the dead
dispatch surface (set_config/get_config/set_cycle_data/set_units/set_gains/get_k/
function_list/supported_functions) was removed. Confirms nothing referenced them."""

import importlib
import pytest

REMOVED = [
    "set_config", "get_config", "set_cycle_data", "set_units",
    "set_gains", "get_k", "supported_functions", "function_list",
]

CONFIGS = {
    "pid":                     {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "center": 0.5},
    "pid_clamping":            {"PB": 100.0, "Ti": 180.0, "Td": 45.0},
    "pid_clamping_percent_pb": {"PB": 42.0, "Ti": 180.0, "Td": 45.0},
    "pid_ac":                  {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "pid_parallel":            {"Kp": 0.01, "Ki": 0.000055, "Kd": 0.45, "Clamping": True},
    "pid_sp":                  {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12,
                                "center_factor": 0.0010, "tau": 115, "theta": 65},
}
CYCLE_DATA = {"HoldCycleTime": 20}


@pytest.mark.parametrize("module_name", list(CONFIGS))
def test_pid_variant_constructs_without_dead_surface(module_name):
    c = importlib.import_module(f"controller.{module_name}").Controller(
        dict(CONFIGS[module_name]), "F", dict(CYCLE_DATA)
    )
    for name in REMOVED:
        assert not hasattr(c, name), f"{module_name} still exposes removed {name}"


@pytest.mark.parametrize("module_name", ["mpc", "fuzzy", "ml"])
def test_non_pid_controller_imports_clean(module_name):
    mod = importlib.import_module(f"controller.{module_name}")
    assert hasattr(mod, "Controller")
    for name in ("set_config", "supported_functions", "get_config", "set_units"):
        assert not hasattr(mod.Controller, name), f"{module_name}.Controller still has {name}"
```

   If constructing `mpc`/`fuzzy`/`ml` is heavy (extra deps), the class-level `hasattr` checks
   in the second test still prove the removal without constructing — that is sufficient. Do NOT
   add construction for them unless it is cheap.

**Test:**
`timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/characterization/test_pid_variants_golden.py tests/unit/controller/ -q`
Expected: `6 passed` (golden unchanged) + the smoke tests pass. Also run the broader controller
suite to catch any hidden reference:
`... uv run pytest tests/characterization tests/unit/mpc tests/unit/runtime -q` → all green.

**Commit:** `refactor(controller): remove dead dispatch surface (set_config/set_gains/get_k/function_list/supported_functions)`

---

## Task 3 — Create `PIDControllerBase(ControllerBase)`

**Files:** `controller/pid_base.py` (NEW). No variant modified yet, so the full suite must
stay green (nothing imports the base until Task 4+).

**Interfaces — Provides:**
- `_calculate_gains(self, pb, ti, td)` — standard PB form (default; overridden by
  clamping/percent_pb/parallel).
- `set_target(self, set_point)` — reset form (default; overridden by percent_pb/ac/sp).
- No `__init__`, no `set_gains`, no `get_k`, no `set_config` (all removed in Task 2).

**Consumes:** `ControllerBase` from `controller.base`; stdlib `time`.

**Steps:** create the file verbatim:

```python
#!/usr/bin/env python3

"""
*****************************************
 PiFire PID Controller Shared Base Class
*****************************************

 Description: Shared scaffolding for the standard-form PID controller variants.
 Owns the standard proportional-band defaults for _calculate_gains / set_target
 that most variants share. Variants override only what genuinely differs.
 update() is never defined here.

*****************************************
"""

import time
from controller.base import ControllerBase


class PIDControllerBase(ControllerBase):
    def _calculate_gains(self, pb, ti, td):
        if pb == 0:
            self.kp = 0
        else:
            self.kp = -1 / pb
        if ti == 0:
            self.ki = 0
        else:
            self.ki = self.kp / ti
        self.kd = self.kp * td

    def set_target(self, set_point):
        self.set_point = set_point
        self.error = 0.0
        self.inter = 0.0
        self.derv = 0.0
        self.last_update = time.time()
```

**Test:** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/characterization/test_pid_variants_golden.py -q`
Expected: `6 passed` (unchanged; base is unused so far). Also sanity-import:
`... uv run python -c "from controller.pid_base import PIDControllerBase; print('ok')"`.

**Commit:** `refactor(controller): add PIDControllerBase with shared PID scaffolding`

---

## Task 4 — Migrate `controller/pid.py`

**Files:** `controller/pid.py`.

**Interfaces — Consumes** `PIDControllerBase._calculate_gains`, `.set_target`.
**Keeps:** `__init__`, `update` (both unchanged from their Task-2 state).

**Steps:**
1. Import: `from controller.base import ControllerBase` → `from controller.pid_base import PIDControllerBase`.
2. Class header: `class Controller(ControllerBase):` → `class Controller(PIDControllerBase):`.
3. `safe_delete_symbol` `_calculate_gains` (identical to base default).
4. `safe_delete_symbol` `set_target` (identical to base default).
5. **KEEP** `__init__` (which still computes gains via `self._calculate_gains(...)` — now the
   inherited base method — and sets `center`/`inter_max`/`set_target(0.0)`) and `update`.

Resulting `pid.py` `Controller` body: `__init__`, `update`.

**Test:** `... uv run pytest tests/characterization/test_pid_variants_golden.py -q`
Expected: `6 passed` (the `pid` golden must be unchanged).

**Commit:** `refactor(controller): pid extends PIDControllerBase`

---

## Task 5 — Migrate `controller/pid_clamping.py`

**Files:** `controller/pid_clamping.py`.

**Interfaces — Consumes** base `set_target`. **Keeps overridden:** `_calculate_gains` (adds
`eventLogger.debug(...)` — a side effect we preserve), `update`. Module-level
`eventLogger`/`create_logger` import stays.

**Steps:**
1. Import → `from controller.pid_base import PIDControllerBase`. Leave the `create_logger`
   import + `eventLogger` module global untouched.
2. Header → `class Controller(PIDControllerBase):`.
3. **KEEP** `_calculate_gains` (has the debug log — do NOT delete).
4. `safe_delete_symbol` `set_target` (identical to base default).
5. **KEEP** `__init__`, `update`.

**Test:** same command. Expected `6 passed` (`pid_clamping` golden unchanged).

**Commit:** `refactor(controller): pid_clamping extends PIDControllerBase`

---

## Task 6 — Migrate `controller/pid_clamping_percent_pb.py`

**Files:** `controller/pid_clamping_percent_pb.py`. This variant overrides BOTH base defaults,
so migration is purely reparenting for hierarchy consistency (it deletes nothing).

**Interfaces — Consumes** nothing from the base behaviorally (overrides both defaults); joins
the PID hierarchy for consistency. **Keeps overridden:** `_calculate_gains` (no-arg `(self)`,
percent-of-setpoint form), `set_target` (calls `self._calculate_gains()`), `update`.

**Steps:**
1. Import → `from controller.pid_base import PIDControllerBase` (keep `create_logger`/`eventLogger`).
2. Header → `class Controller(PIDControllerBase):`.
3. **KEEP** `_calculate_gains`, `set_target`, `__init__`, `update`. Delete nothing.

**Test:** same command. Expected `6 passed` (`pid_clamping_percent_pb` golden unchanged).

**Commit:** `refactor(controller): pid_clamping_percent_pb extends PIDControllerBase`

---

## Task 7 — Migrate `controller/pid_ac.py`

**Files:** `controller/pid_ac.py`.

**Interfaces — Consumes** base `_calculate_gains` (standard form). **Keeps overridden:**
`set_target` (center/units logic), `update`.

**Steps:**
1. Import → `from controller.pid_base import PIDControllerBase`.
2. Header → `class Controller(PIDControllerBase):`.
3. `safe_delete_symbol` `_calculate_gains` (identical to base default).
4. **KEEP** `__init__` (including `self.pb = config["PB"]` — hard key, do not soften),
   `set_target`, `update`.

**Test:** same command. Expected `6 passed` (`pid_ac` golden unchanged).

**Commit:** `refactor(controller): pid_ac extends PIDControllerBase`

---

## Task 8 — Migrate `controller/pid_parallel.py`

**Files:** `controller/pid_parallel.py`.

**Interfaces — Consumes** base `set_target`. **Keeps overridden:** `_calculate_gains` (parallel
form `self.kp = -1 * kp`, etc.), `update`.

**Steps:**
1. Import → `from controller.pid_base import PIDControllerBase` (keep `create_logger`/`eventLogger`).
2. Header → `class Controller(PIDControllerBase):`.
3. **KEEP** `_calculate_gains` (parallel form).
4. `safe_delete_symbol` `set_target` (identical to base default).
5. **KEEP** `__init__` (`self.clamping`, gains via `_calculate_gains(...)`, `set_target(0.0)`),
   `update`.

**Test:** same command. Expected `6 passed` (`pid_parallel` golden unchanged).

**Commit:** `refactor(controller): pid_parallel extends PIDControllerBase`

---

## Task 9 — Migrate `controller/pid_sp.py`

**Files:** `controller/pid_sp.py`.

**Interfaces — Consumes** base `_calculate_gains` (standard form). **Keeps overridden:**
`set_target` (center/units logic), `update`.

**Steps:**
1. Import → `from controller.pid_base import PIDControllerBase` (keep `import math`).
2. Header → `class Controller(PIDControllerBase):`.
3. `safe_delete_symbol` `_calculate_gains` (identical to base default).
4. **KEEP** `__init__`, `set_target`, `update`.

**Test:** same command. Expected `6 passed` (`pid_sp` golden unchanged).

**Commit:** `refactor(controller): pid_sp extends PIDControllerBase`

---

## Task 10 — Final verification: registration + full suite

**Files:** none modified (verification only). `controllers.json` UNCHANGED.

**Steps:**
1. Confirm every registered variant still constructs via the real load path
   (mirrors `runner._build_core`):

```
timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "
import json, importlib
meta = json.load(open('controller/controllers.json'))['metadata']
pid_mods = ['pid','pid_clamping','pid_clamping_percent_pb','pid_ac','pid_parallel','pid_sp']
from controller.pid_base import PIDControllerBase
for name in pid_mods:
    assert meta[name]['module_name'] == name, name
    Controller = importlib.import_module('controller.' + name).Controller
    assert issubclass(Controller, PIDControllerBase), name
    print(name, 'ok')
assert 'pid_base' not in meta
print('all six selectable + subclass PIDControllerBase; pid_base not registered')
"
```
   Expected: all six print `ok` and the final line.

2. Run the PID golden + smoke + the existing controller-loop golden and controller suites:

```
timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/characterization tests/unit/controller tests/unit/mpc tests/unit/runtime -q
```
   Expected: all green, including the 6 `test_pid_variants_golden` cases, the construct smoke,
   and the `test_controller_loop_golden` cases.

3. Full suite:
```
timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```
   Expected: same pass count as the pre-Phase-I baseline plus the new Task 1 + Task 2 tests
   (no new failures).

4. `uvx ruff format controller/base.py controller/pid_base.py controller/pid.py controller/pid_clamping.py controller/pid_clamping_percent_pb.py controller/pid_ac.py controller/pid_parallel.py controller/pid_sp.py controller/mpc.py`
   then `uvx ruff check` the same set.

**Commit** (only if step 4 changed formatting; otherwise nothing to commit):
`style(controller): ruff format PID base + variants`

**Rollback:** revert branch `refactor/pid-base`.

---

## Self-review checklist

- [x] Task 1 lands FIRST (green against current code), pinning each of the six `update()`
      series under a controlled clock; every later task re-runs it expecting no change.
- [x] Task 2 is a pure deletion of the proven-dead surface (`function_list`,
      `supported_functions`, `set_config`, `get_config`, `set_cycle_data`, `set_units`,
      `set_gains`, `get_k`) from `base.py` + 6 PID variants + the one `mpc.py` append line;
      `fuzzy`/`ml` are inherit-only and cleaned automatically; a construct/import smoke is their net.
- [x] `PIDControllerBase` pulls up ONLY `_calculate_gains` (std) and `set_target` (reset) as
      defaults; it defines NO `__init__`/`set_gains`/`get_k`/`set_config` (those are gone). — verified against live code.
- [x] `_calculate_gains` kept overridden in clamping (debug log), percent_pb (no-arg % form),
      parallel (`-1*kp`). Confirmed Tasks 5/6/8.
- [x] `set_target` kept overridden in percent_pb (`_calculate_gains()`), ac and sp (center/units).
      Confirmed Tasks 6/7/9.
- [x] `pid_ac` constructed with real `PB` config in the tests (hard-key `config["PB"]`).
- [x] All six keep a top-level `Controller` class; filenames and `controllers.json` untouched;
      Task 10 re-verifies selection + subclassing. `pid_base` not registered.
- [x] `update()` never modified in any variant.
- [x] One variant per task → any regression bisects to a single commit.
- [x] Removing `supported_functions`/`function_list` is a human-approved API removal (zero
      callers repo-wide), documented in Global Constraints as intended, not an oversight.
- [x] Global constraints (test command, ruff, Serena edits, commit-file, co-author) restated.
