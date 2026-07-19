# `control["status"]` Second-Dimension Formalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Formalize the controller's second state axis `control["status"]` — right-sized to its actual complexity — as a `StatusState(StrEnum)` plus a named `should_keep_power_on(mode, status)` predicate for the one `mode`×`status` coupling, converting the ~6 direct write sites and 2 read sites. Zero runtime behavior change. Design: [`../specs/2026-07-18-status-second-dimension-design.md`](../specs/2026-07-18-status-second-dimension-design.md).

**Architecture:** `StatusState(StrEnum)` (`ACTIVE="active"`, `MONITOR="monitor"`, `INACTIVE="inactive"`, `UNSET=""`) — StrEnum so the four string values, which the web/mobile UI display verbatim and which persist in `control["status"]`, stay byte-identical. **Direct enum writes** (no `set_status` seam — the size doesn't justify it, per the resolved open question). The single `mode`×`status` interaction (a Monitor-mode error keeps the OEM controller powered on) becomes a pure predicate `should_keep_power_on(mode, status)` in `controller/runtime/transitions.py`.

**Tech Stack:** Python 3.14, `enum.StrEnum`, pytest, `uv`/`uvx ruff`.

## Global Constraints

- Python 3.14. `except (A, B)` canonical.
- **TEST COMMAND (exact):** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`.
- Before every commit: `uvx ruff format <changed>` then `uvx ruff check <changed>` (leave pre-existing errors in untouched lines — controller.py has 2 known ones).
- Plain Read/Edit. In a worktree, do NOT use Serena (edits the main checkout).
- Commit `git commit -F <msgfile>`; Co-author `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; per-task messages below.
- **BEHAVIOR-PRESERVING.** StrEnum serializes/compares/stringifies identically. The four `status` strings are a **published contract** (web/mobile display them) — do NOT change them. The power decision (`power_on`/`power_off` calls) must be identical. Goldens + the new characterization must stay green with zero assertion edits, no golden regeneration. Baseline: **1521**.
- **`SValue`/`.value`/`.name` care:** `StatusState.UNSET` has value `""`; never emit `"StatusState.UNSET"` (StrEnum's `str()` returns `""`, so it's fine — but never use `.name`).
- Branch `refactor/status-dimension`, off `massive-reworks-and-new-ui`.

## The exact sites (from the design's inventory)

**Writes (6):** `common/defaults.py:461` (`""`), `controller/runtime/controller.py:358` (`"active"`), `:519` (`"monitor"`), `:398` (`"inactive"` Stop), `:413` (`"inactive"` Error), `controller/runtime/modes/base.py:315` (`"active"`, transient switch-off). **Reads (2):** `controller.py:357` (`status != "monitor"`), `:385` (`status == "monitor" and mode == Error` → power). *(Line numbers pre-this-plan; re-anchor on the surrounding code.)*

**Do NOT touch:** the web/mobile passthrough (`blueprints/api/routes.py:71`, `blueprints/mobile/socket_io.py:217`) — it forwards the string, no change needed.

---

## Task 1 — Characterize status transitions + the power coupling [COMMIT FIRST]

The `monitor+error → keep power on` edge is the weakest-covered; pin it and the status values before refactoring.

**Files:** Create `tests/characterization/test_status_dimension.py` (reuse the `test_controller_loop_golden.py` harness — `make_controller`/`_neutralize_externals`/`_spy_dispatch`; read that file for the real helper names).

- [ ] **Step 1: Write the tests** (green against current code — characterization):
  - `test_stop_persists_inactive` — Stop cleanup → `control["status"] == "inactive"` (already asserted in `test_tick_stop_mode_cleanup`; mirror it here for the status-focused suite).
  - `test_error_persists_inactive` — Error cleanup → `control["status"] == "inactive"`.
  - `test_monitor_dispatch_sets_monitor` — dispatching Monitor → `control["status"] == "monitor"`.
  - `test_active_set_when_operating` — an update in a normal mode (not Monitor, not Error) → `control["status"] == "active"`.
  - **`test_monitor_error_keeps_power_on`** (the coupling — THE point): drive the terminal block with `status="monitor"` + `mode="Error"`; assert `grill.calls` contains `power_on` and NOT `power_off`.
  - **`test_normal_error_powers_off`**: `status="active"` (or non-monitor) + `mode="Error"` → `power_off`, not `power_on`.
  - Patch `os.system` in any controller-constructing test (the shutdown line) — reuse `_neutralize_externals`.
- [ ] **Step 2: Run — expect GREEN** against current code: `... uv run pytest tests/characterization/test_status_dimension.py -q`. If a value differs, observe and pin the real one (don't assert unverified).
- [ ] **Step 3: Commit:** `test(controller): characterize status transitions + monitor/error power coupling`.

---

## Task 2 — Add `StatusState(StrEnum)` + `should_keep_power_on` [COMMIT]

**Files:** `common/modes.py` (add `StatusState` alongside `Mode`), `controller/runtime/transitions.py` (add the predicate), `tests/unit/common/test_modes_enum.py` + `tests/unit/runtime/test_request_transition.py` (or a new unit test) for the new symbols.

- [ ] **Step 1: Failing tests:** `StatusState.ACTIVE == "active"`, `str(StatusState.UNSET) == ""`, `json.dumps({"s": StatusState.MONITOR}) == json.dumps({"s": "monitor"})`; and `should_keep_power_on(Mode.ERROR, StatusState.MONITOR) is True`, `should_keep_power_on(Mode.ERROR, StatusState.ACTIVE) is False`, `should_keep_power_on(Mode.STOP, StatusState.MONITOR) is False`. Run → FAIL.
- [ ] **Step 2: Implement.** In `common/modes.py`:

```python
class StatusState(StrEnum):
    ACTIVE = "active"
    MONITOR = "monitor"
    INACTIVE = "inactive"
    UNSET = ""
```

  In `controller/runtime/transitions.py` (imports `Mode` already; add `StatusState`):

```python
def should_keep_power_on(mode, status):
    """The one mode x status coupling: a Monitor-mode error keeps the OEM
    controller powered on; every other terminal condition powers off."""
    return status == StatusState.MONITOR and mode == Mode.ERROR
```

- [ ] **Step 3: Run** the new unit tests + full char/e2e (nothing consumes the new symbols yet → unchanged): green.
- [ ] **Step 4: Commit:** `feat(controller): add StatusState enum + should_keep_power_on predicate`.

---

## Task 3 — Convert the write/read sites to `StatusState` + the predicate [COMMIT]

**Files:** `common/defaults.py`, `controller/runtime/controller.py`, `controller/runtime/modes/base.py`.

- [ ] **Step 1:** Import `StatusState` (and, in controller.py, `should_keep_power_on`) where needed.
- [ ] **Step 2:** Convert the 6 write sites to `StatusState.*` (`defaults.py` `""`→`StatusState.UNSET`; the `"active"`/`"monitor"`/`"inactive"` writes → the members). Convert the 2 read comparisons: `:357` `status != "monitor"` → `!= StatusState.MONITOR`; `:385` replace `if self.control["status"] == "monitor" and self.control["mode"] == Mode.ERROR:` with `if should_keep_power_on(self.control["mode"], self.control["status"]):`.
- [ ] **Step 3:** Leave the transient `base.py:315` `"active"` as `StatusState.ACTIVE` (behavior-preserving; the design noted it's redundant but do NOT change behavior in this plan — a separate cleanup could drop it).
- [ ] **Step 4: Run** `... uv run pytest tests/characterization/test_status_dimension.py tests/characterization/test_controller_loop_golden.py tests/e2e -q` → green, zero assertion edits (StrEnum values identical, predicate equivalent to the old conditional).
- [ ] **Step 5: Commit:** `refactor(controller): status writes use StatusState; power decision via should_keep_power_on`.

---

## Task 4 — Transition-table snapshot + verification [COMMIT]

- [ ] **Step 1: Snapshot the transition table.** Add a small `STATUS_TRANSITIONS` doc-dict (or an asserted test) in one place capturing the documented edges (first-update→ACTIVE; Monitor→MONITOR; Stop/Error→INACTIVE; MONITOR persists through Error). A test asserts the committed table matches, giving the "second dimension" a single inspectable definition (parallels the mode `ALLOWED_EXITS` snapshot).
- [ ] **Step 2: Full suite** `... uv run pytest tests/ -q` → **1521** (+ the new tests), zero assertion edits, no golden regenerated. Confirm the web/mobile passthrough still emits the same strings (the `tests/web` suite covers `api/routes`/`socket_io` rendering).
- [ ] **Step 3: Audit** — grep `controller/ common/` for remaining bare `"active"`/`"monitor"`/`"inactive"` at status-semantic sites; every survivor should be intentional (log text). ruff the changed set.
- [ ] **Step 4: Commit** (if formatting/table): `feat(controller): snapshot the status transition table`.

**Rollback:** revert the branch — additive enum + predicate + equal-value substitutions.

## Self-Review
- **Scope coverage:** all 6 write + 2 read sites; the coupling extracted to a predicate; the table snapshotted. ✅
- **Behavior-preserving:** StrEnum ⇒ identical published strings + serialization; predicate ≡ the old `status=="monitor" and mode=="Error"`; characterization (incl. the previously-weak keep-power path) gates every task. ✅
- **Right-sized:** enum + predicate + table, NOT a transition engine — matches the design's assessment. ✅
