# Mode + TransitionKind Enums Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bare `"Smoke"`/`"Hold"`/… mode string literals and the `"natural"`/`"safety"`/`"terminal"` transition-kind literals with `Mode` and `TransitionKind` `str`-enums, across the **controller domain** — so the FSM (dispatch map, `ALLOWED_EXITS`, `GUARDS`, `request_transition`) and the mode classes reference typed constants instead of magic strings, with zero runtime behavior change.

**Architecture:** `Mode` is a `class Mode(StrEnum)` (`enum.StrEnum`, Python 3.11+; we're on 3.14) whose members' values are the exact existing strings (`Mode.SMOKE = "Smoke"`). **Use `StrEnum`, NOT a hand-rolled `class Mode(str, Enum)`** — the latter inherits `Enum.__str__`, so `str(Mode.SMOKE)` / `f"{Mode.SMOKE}"` yield `"Mode.SMOKE"`, a real behavior change anywhere a mode is stringified for a log or display. `StrEnum` overrides `__str__`/`__format__` to return the value, so a `Mode` member **is** its string in every context: `Mode.SMOKE == "Smoke"`, `str(Mode.SMOKE) == "Smoke"`, `f"{Mode.SMOKE}" == "Smoke"`, `json.dumps` → `"Smoke"`, and it hashes/compares identically — so the persisted `control["mode"]` JSON is byte-identical, cross-process string writes (web/display) still match, and recipe-file mode strings still dispatch. `Mode` lives in `common/` so every process *can* adopt it later; this plan converts only `controller/**`. `TransitionKind` is controller-internal (never serialized) but is also a `StrEnum` for consistency + clean logging. Datastore values read back from JSON are plain `str` (the enum type is lost on deserialize), which is fine: all mode comparisons use `==` (verified — no `is`, no `isinstance`), so `control["mode"] == Mode.SMOKE` holds whether `control["mode"]` is a `Mode` or a plain `str`.

**Tech Stack:** Python 3.14, `enum.Enum`, pytest, `uv`/`uvx ruff`.

## Global Constraints

- Python 3.14. `except (A, B)` is canonical — don't "fix" bare `except A, B`.
- **TEST COMMAND (exact, always):** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`. Bare python HANGS/false-fails.
- Before every commit: `uvx ruff format <changed>` then `uvx ruff check <changed>`.
- **Tooling:** plain Read/Edit. If executing in a git worktree, do NOT use Serena — it edits the main checkout, not the worktree (bit H, I, FSM).
- Commit with `git commit -F <msgfile>` (zsh eats backticks). Co-author trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **BEHAVIOR-PRESERVING.** A `str`-enum member serializes and compares identically to its string value, so every persisted `control["mode"]`/`status["mode"]` value, every dispatch, and every golden assertion is byte-identical. The existing goldens + the FSM characterization suites are the contract and must stay green with **zero assertion edits and no golden regeneration**. If any assertion value would change, you introduced a real behavior change — stop and reconsider (almost certainly a non-`str` Enum slipped in, or a `.value`/`.name` confusion).
- **SCOPE = `controller/**` only.** Do NOT convert `blueprints/`, `display/`, `notify/`, `common/api_commands.py`, or recipe files — they keep using plain strings and interoperate via `str`-enum equality (documented follow-on). The one shared file this plan adds is `common/modes.py` (the enum home).
- **Do NOT convert non-mode strings.** Notification event keys (`"Grill_Error_01"`, `"Recipe_Step_Message"`), `control["status"]` values (`"active"`/`"monitor"`/`"inactive"`), display command tuples (`("text","ERROR")`) are NOT modes. Leave them. Only the 11 mode strings and the 3 transition-kind strings are in scope.
- Branch `refactor/mode-enums`, off `massive-reworks-and-new-ui`.

## The 11 modes and 3 kinds (exact values — do not alter the strings)

`Mode`: `STARTUP="Startup"`, `SMOKE="Smoke"`, `HOLD="Hold"`, `MONITOR="Monitor"`, `MANUAL="Manual"`, `PRIME="Prime"`, `REIGNITE="Reignite"`, `SHUTDOWN="Shutdown"`, `STOP="Stop"`, `ERROR="Error"`, `RECIPE="Recipe"`.
`TransitionKind`: `NATURAL="natural"`, `SAFETY="safety"`, `TERMINAL="terminal"`.

---

## File Structure

```
common/modes.py                              (NEW — class Mode(StrEnum))
controller/runtime/transitions.py            (MODIFIED — TransitionKind enum; Mode in ALLOWED_EXITS/GUARDS/Edge/kind)
controller/runtime/controller.py             (MODIFIED — _MODE_HANDLERS/_MODE_DISPATCH keys, tick() comparisons, next_mode)
controller/runtime/modes/base.py             (MODIFIED — run() mode comparisons)
controller/runtime/modes/{startup,smoke,hold,monitor,manual,prime,reignite,shutdown}.py  (MODIFIED — name = Mode.X + internal comparisons)
tests/unit/common/test_modes_enum.py         (NEW — Task 1 interop characterization)
tests/unit/runtime/test_transitionkind_enum.py (NEW — Task 2)
```

---

## Task 1 — Add `Mode(StrEnum)` + prove str-interop [COMMIT FIRST]

The whole refactor's safety rests on `str`-enum transparency. Pin it before converting anything.

**Files:** Create `common/modes.py`, `tests/unit/common/test_modes_enum.py`.

**Interfaces — Produces:** `common.modes.Mode`, a `str` enum with the 11 members above.

- [ ] **Step 1: Write the failing test** (`tests/unit/common/test_modes_enum.py`):

```python
import json
from common.modes import Mode


def test_member_is_its_string():
    assert Mode.SMOKE == "Smoke"
    assert isinstance(Mode.SMOKE, str)
    assert Mode.STOP == "Stop" and Mode.ERROR == "Error" and Mode.RECIPE == "Recipe"

def test_str_and_format_return_the_value_not_the_member_repr():
    # This is exactly why StrEnum (not `class Mode(str, Enum)`) is required:
    # `(str, Enum)` would give "Mode.SMOKE" here, a behavior change in any log/display.
    assert str(Mode.SMOKE) == "Smoke"
    assert f"{Mode.SMOKE}" == "Smoke"
    assert "%s" % Mode.HOLD == "Hold"
    assert "mode is " + Mode.ERROR == "mode is Error"

def test_json_serializes_to_plain_string():
    assert json.dumps({"mode": Mode.SMOKE}) == json.dumps({"mode": "Smoke"})
    # round-trip: JSON read gives a plain str that still == the member
    assert json.loads(json.dumps({"mode": Mode.HOLD}))["mode"] == Mode.HOLD

def test_dict_key_and_set_interop_with_plain_strings():
    # dispatch map keyed by Mode, looked up with a plain string from control["mode"]
    d = {Mode.SMOKE: 1, Mode.HOLD: 2}
    assert d["Smoke"] == 1 and d[Mode.HOLD] == 2
    # set membership (ALLOWED_EXITS) works both ways
    s = {Mode.ERROR, Mode.REIGNITE, Mode.STOP}
    assert "Error" in s and Mode.STOP in s

def test_all_eleven_values_exact():
    assert {m.value for m in Mode} == {
        "Startup", "Smoke", "Hold", "Monitor", "Manual",
        "Prime", "Reignite", "Shutdown", "Stop", "Error", "Recipe",
    }
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: common.modes`): `... uv run pytest tests/unit/common/test_modes_enum.py -q`.

- [ ] **Step 3: Implement** `common/modes.py`:

```python
"""Canonical controller mode names as a StrEnum. Members ARE their string
values (Mode.SMOKE == "Smoke", str(Mode.SMOKE) == "Smoke"), so they serialize to
plain JSON, compare/stringify equal to the persisted control["mode"] string, and
interoperate as dict keys / set members with plain strings written by other
processes and recipe files. StrEnum (not `class Mode(str, Enum)`) is required so
str()/format yield the value, not "Mode.SMOKE"."""

from enum import StrEnum


class Mode(StrEnum):
    STARTUP = "Startup"
    SMOKE = "Smoke"
    HOLD = "Hold"
    MONITOR = "Monitor"
    MANUAL = "Manual"
    PRIME = "Prime"
    REIGNITE = "Reignite"
    SHUTDOWN = "Shutdown"
    STOP = "Stop"
    ERROR = "Error"
    RECIPE = "Recipe"
```

- [ ] **Step 4: Run — expect PASS** (4 tests). 
- [ ] **Step 5: Commit** (`ruff format`/`check` first): `feat(common): add Mode str-enum with str-interop characterization`.

---

## Task 2 — Add `TransitionKind(StrEnum)` + convert `transitions.py` kinds [COMMIT]

**Files:** `controller/runtime/transitions.py`, `tests/unit/runtime/test_transitionkind_enum.py` (new). Also updates the `kind=` call sites in `controller/runtime/modes/{smoke,hold,base}.py` and `controller/runtime/controller.py` (the `request_transition(..., kind="...")` calls) — grep `kind=` to find all.

**Interfaces — Produces:** `transitions.TransitionKind` (`NATURAL`/`SAFETY`/`TERMINAL`). `request_transition`'s `kind` param and `Edge.kind` now hold `TransitionKind`.

- [ ] **Step 1: Failing test** (`test_transitionkind_enum.py`): `TransitionKind.NATURAL == "natural"`; assert the seam still dispatches — a `kind=TransitionKind.NATURAL` call yields when updated, `TransitionKind.SAFETY` applies authoritatively (reuse the fake-ctx pattern from `test_request_transition.py`). Run → FAIL (`TransitionKind` undefined).
- [ ] **Step 2: Implement.** In `transitions.py` add `from enum import StrEnum`, then:

```python
class TransitionKind(StrEnum):
    NATURAL = "natural"
    SAFETY = "safety"
    TERMINAL = "terminal"
```

  Then replace the string branches with the enum in `request_transition`: `if kind == TransitionKind.NATURAL:` (str-enum keeps `== "natural"` true, so this is behavior-identical). In `_flameout_edges` and the `GUARDS`/`"*"` `Edge(...)` constructions, replace `"safety"`/`"terminal"` with `TransitionKind.SAFETY`/`TransitionKind.TERMINAL`. Update the `Edge.kind` type hint to `TransitionKind`.
- [ ] **Step 3: Convert the `kind=` call sites** in `modes/smoke.py`, `modes/hold.py`, `modes/base.py`, `controller.py` (recipe end→Stop) from `kind="safety"`/`kind="terminal"` to the enum. Import `TransitionKind` in each. Grep `kind=` to confirm none remain as strings in `controller/`.
- [ ] **Step 4: Run** the seam unit tests + the guard-engine tests + `test_mode_transitions.py` + `test_outer_transitions.py`: `... uv run pytest tests/unit/runtime/ tests/characterization/test_mode_transitions.py tests/characterization/test_outer_transitions.py -q` → all green (behavior identical).
- [ ] **Step 5: Commit:** `refactor(controller): TransitionKind str-enum replaces kind literals`.

---

## Task 3 — Convert `Mode` throughout `transitions.py` [COMMIT]

**Files:** `controller/runtime/transitions.py`.

- [ ] **Step 1:** `from common.modes import Mode`. Convert:
  - `ALLOWED_EXITS`: keys AND set values → `Mode` (e.g. `Mode.PRIME: {Mode.STARTUP, Mode.STOP, Mode.ERROR}`). Because it's a `str`-enum, `_check_legal(control.get("mode"), to_mode)` still works when `control["mode"]` is a plain str read from the store (`"Smoke" in {Mode.ERROR, ...}` and `ALLOWED_EXITS.get("Smoke")` both interoperate).
  - `GUARDS`: the mode keys (`"Smoke"`, `"Hold"`) and the `"*"` key → keep `"*"` as the literal string (it is a wildcard, NOT a mode; document it), convert the real mode keys to `Mode.SMOKE`/`Mode.HOLD`. `evaluate_phase` does `GUARDS.get(mode_obj.name, ...)` where `mode_obj.name` becomes a `Mode` in Task 6 — interoperates either way.
  - `Edge(...)` `to=` targets → `Mode` (`Mode.ERROR`, `Mode.REIGNITE`).
- [ ] **Step 2:** Guard predicates that reference no mode strings need no change; the `_flameout_edges` `to` args become `Mode`.
- [ ] **Step 3: Run** `... uv run pytest tests/unit/runtime/ tests/characterization/test_mode_transitions.py tests/characterization/test_outer_transitions.py tests/characterization/test_modes_golden.py -q` → green (the graph-snapshot test from FSM Task 17 asserts the edge set; confirm it still matches — `Mode` values equal the old strings, so the snapshot compares equal; if the snapshot stringifies members, adjust the snapshot's expected to `Mode` WITHOUT changing the underlying edges — this is a representation change, not a behavior change; flag it if it forces an assertion edit).
- [ ] **Step 4: Commit:** `refactor(controller): Mode enum in transitions ALLOWED_EXITS/GUARDS/edges`.

---

## Task 4 — Convert `Mode` in `controller.py` [COMMIT]

**Files:** `controller/runtime/controller.py`.

- [ ] **Step 1:** `from common.modes import Mode`. Convert:
  - `_MODE_HANDLERS` and `_MODE_DISPATCH` dict keys → `Mode` (`Mode.SMOKE: SmokeMode`, `Mode.SMOKE: _dispatch_smoke`). Lookups use `control["mode"]`/`recipe["steps"][n]["mode"]` — plain strings that match the `Mode` keys via str-enum hashing (recipe files keep plain strings; no recipe-file change needed).
  - `tick()` / `_dispatch_*` comparisons: `self.control["mode"] == "Stop"` → `== Mode.STOP`, `in ("Stop", "Error")` → `in (Mode.STOP, Mode.ERROR)`, etc.
  - `next_mode()` and the dispatch branches that WRITE a mode literal (`control["mode"] = "Stop"`, `control["next_mode"] = "Stop"`, boot-to-monitor `= "Monitor"`, startup→prime `= "Prime"`) → `Mode.X` (serializes to the same string).
  - `next_mode`'s `setpoint if next_mode == "Hold"` → `== Mode.HOLD`.
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_controller_loop_golden.py tests/characterization/test_outer_transitions.py -q` → green.
- [ ] **Step 3: Commit:** `refactor(controller): Mode enum in Controller dispatch/tick/next_mode`.

---

## Task 5 — Convert `Mode` in `base.run()` [COMMIT]

**Files:** `controller/runtime/modes/base.py`.

- [ ] **Step 1:** `from common.modes import Mode`. Convert every mode-string comparison inside `run()` and the shared helpers (`_smoke_plus_fan_tick`'s `self.name == "Smoke"`/`"Hold"`, the recipe-overlay `control["mode"] == "Recipe"` checks, `mode == "Hold"`, `mode == "Manual"`, the `status_data["mode"] = mode` assignment carries a `Mode` which serializes fine). `mode` is `self.name` (a `Mode` after Task 6) — comparisons interoperate throughout the transition.
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_modes_golden.py tests/characterization/test_mode_transitions.py tests/e2e/test_work_cycle_e2e.py -q` → green.
- [ ] **Step 3: Commit:** `refactor(controller): Mode enum in ControlMode.run comparisons`.

---

## Task 6 — Convert `Mode` in the 8 mode classes [COMMIT]

**Files:** `controller/runtime/modes/{startup,smoke,hold,monitor,manual,prime,reignite,shutdown}.py` (and `base.py`'s `name: str = ""` → `name: Mode | str = ""` type hint; leave the default).

- [ ] **Step 1:** In each mode file: `from common.modes import Mode`; change `name = "Smoke"` → `name = Mode.SMOKE` (etc.). Convert any internal mode-string comparisons/writes in the mode bodies (e.g. `reignite.py`/`startup.py` that reference other modes; grep each file for the 11 strings). `reignite_from_self=True` edges use `mode_obj.name` which is now a `Mode` — `reignitelaststate = Mode.SMOKE` serializes to `"Smoke"`, and the outer `next_mode = safety.reignitelaststate` reads it back as a str that dispatches fine.
- [ ] **Step 2: Run** the full controller surface: `... uv run pytest tests/characterization tests/e2e tests/unit/runtime -q` → all green.
- [ ] **Step 3: Commit:** `refactor(controller): mode classes declare name = Mode.*`.

---

## Task 7 — Verification + serialization audit [COMMIT if formatting only]

- [ ] **Step 1: Full suite** `... uv run pytest tests/ -q` → same pass count as the pre-refactor baseline (**1512**) plus the two new enum tests; **zero** assertion edits, no golden regenerated. In particular confirm `tests/characterization/test_process_command_golden.py` (the SHA-pinned golden) is untouched and green — it exercises `common/api_commands.py`, which this plan does NOT convert, so its serialized `control["mode"]` strings are unchanged.
- [ ] **Step 2: Serialization audit.** Grep `controller/` for any remaining bare mode-string literal: `grep -rnE '"(Startup|Smoke|Hold|Monitor|Manual|Prime|Reignite|Shutdown|Stop|Error|Recipe)"' controller/`. Every hit should now be a `Mode.*` reference, EXCEPT: the `"*"` wildcard key in GUARDS (not a mode), and any docstring/comment/log-message text (those are prose, leave them). Confirm no mode value is ever written to the store as anything but a `Mode`/its exact string.
- [ ] **Step 3: Cross-process spot check.** Confirm a `Mode` written by the controller reads back correctly through the store: the existing e2e suite (`test_work_cycle_e2e.py`, real SQLite) already round-trips `control["mode"]` through JSON — its green result IS this confirmation. Note in the commit body that web/display/notify still read `control["mode"]` as plain strings and match via str-enum equality (no change required there).
- [ ] **Step 4: ruff** the full changed set; commit only if formatting changed: `style(controller): ruff format mode-enum conversion`.

**Rollback:** revert the branch — `Mode`/`TransitionKind` are additive; a partial revert leaves the enum defined but unused.

---

## Follow-on (out of scope)

- **Adopt `Mode` in `blueprints/`, `display/`, `notify/`, `common/api_commands.py`, and recipe tooling** (≈400 more sites). They already interoperate with `Mode` via str-enum equality, so this is a cosmetic/consistency sweep, not a behavior change — do it per-area, each gated by that area's tests. `common/api_commands.py` is the highest-value next target (it authors `control["mode"]` from the REST/socket API and is golden-pinned).
- Optionally make `default_control()["mode"]` (`common/defaults.py:444`) a `Mode` too, once `common/` adopts the enum.

## Self-Review

- **Spec coverage:** transition kinds (Task 2) and all controller-domain mode strings (Tasks 3–6) converted; interop proven first (Task 1). ✅
- **Placeholder scan:** enum defs and interop tests are concrete; per-file conversions are described by pattern + the specific comparison/write sites (grep-locatable) rather than quoting all 149 — the conversion is mechanical (`"X"` → `Mode.X`) and the gate (byte-identical goldens) catches any miss. ✅
- **Type consistency:** `Mode(StrEnum)` and `TransitionKind(StrEnum)` (both `enum.StrEnum`, not `(str, Enum)`) names stable across tasks; `Edge.kind: TransitionKind`; `name = Mode.*`. ✅
- **Behavior-preserving:** `str`-enum ⇒ identical serialization/comparison/hashing; goldens + FSM char suites gate every task with zero assertion edits; the `str`-interop is pinned in Task 1 before any conversion. ✅
