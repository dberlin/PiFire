# Mode-Transition FSM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize PiFire's implicit mode-transition logic into an explicit state machine — one arbitrated `request_transition()` seam + per-mode legal-exit declarations + a `{mode: handler}` dispatch map — replacing ~21 scattered `control["mode"]=` writes across 4 files, with runtime behavior byte-for-byte preserved.

**Architecture:** A new `controller/runtime/transitions.py` owns `request_transition(store, control, to_mode, ...)`, the single write seam that faithfully reproduces BOTH of today's mechanisms (`next_mode()`'s guarded/setpoint-coupled write AND the mode files' unguarded safety writes). Every mode change routes through it. Each `ControlMode` declares `ALLOWED_EXITS`; the seam asserts legality (dev/test). `Controller.tick()`'s if/elif ladder becomes a dispatch map. Design reference: [`../specs/2026-07-18-mode-transition-fsm-design.md`](../specs/2026-07-18-mode-transition-fsm-design.md). Edge inventory (authoritative side-effect contracts) is reproduced inline per task.

**Tech Stack:** Python 3.14, pytest, `uv`/`uvx ruff`, Serena symbolic tools.

## Global Constraints

- Python 3.14. `except (A, B)` is canonical; do NOT "fix" bare `except A, B` — ruff owns that.
- **TEST COMMAND (exact, always):** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`. Bare `python`/`pytest` gives FALSE failures; missing the offscreen/dummy vars HANGS.
- **SAFETY — READ THIS FIRST.** `controller/runtime/controller.py:477` runs `os.system("sleep 3 && sudo shutdown -h now &")` on the `Shutdown→Stop` edge when `settings["shutdown"]["auto_power_off"]` is true. **Every test that constructs a `Controller` or drives the Shutdown/Stop path MUST `monkeypatch.setattr(os, "system", <recorder>)`** (it is deliberately module-level — see controller.py:18) AND set `auto_power_off=False` in the settings fixture unless the test is specifically asserting that edge with `os.system` already patched. Before running ANY new controller test, grep the touched code for `os.system`/`subprocess`/`sudo`/`shutdown`/`reboot` and confirm it is neutralized. (Repo history: 2 real reboots from unmocked paths.)
- **Behavior-preserving.** No transition semantics change. The persisted control fields (`mode`, `status`, `updated`, `next_mode`, `primary_setpoint`, `safety.reigniteretries`, `safety.reignitelaststate`) keep identical values and identical write ORDER. Existing goldens (`tests/characterization/test_modes_golden.py`, `test_controller_loop_golden.py`, `tests/e2e/test_work_cycle_e2e.py`) must stay green byte-for-byte; the new transition characterization suite (Tasks 1–2) is added green against current code BEFORE any production change and must stay green after every task.
- **Two arbitration variants are load-bearing (inventory gotcha #2).** `next_mode()` is GUARDED (`if not control["updated"]`, after flush+reread); the inline safety writes are UNGUARDED (unconditionally set `updated=True`, operate in place on the held control). The seam must reproduce both exactly — collapsing them loses the "mid-cycle safety trip beats the post-cycle `next_mode`" behavior.
- **Setpoint coupling (gotcha #3).** `next_mode` forces `primary_setpoint = setpoint if to=="Hold" else 0`. The inline safety writes NEVER touch `primary_setpoint`. The seam applies the Hold-rule ONLY on the guarded/setpoint-bearing path.
- Edits via Serena symbolic tools where practical (`create_text_file`, `replace_symbol_body`, `insert_before_symbol`, `replace_content`). **Worktree gotcha:** if executing in a git worktree, Serena may edit the main checkout instead — after each Serena edit, confirm the WORKTREE file changed (not `/home/dannyb/sources/PiFire`); if it misfires, use plain Edit.
- Before every commit: `uvx ruff format <changed>` then `uvx ruff check <changed>`.
- Commit with `git commit -F <msgfile>` (zsh eats backticks in `-m`). Co-author trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `refactor/mode-transition-fsm`, off `massive-reworks-and-new-ui`. One edge-family per commit so a regression bisects to a single commit. This is core, safety-adjacent control flow — do NOT batch.

## Scope

This plan delivers the **write-mechanism FSM**: the seam, the repointing of all 21 sites, the dispatch map, and per-mode legal-exit enforcement (an assertion that rejects illegal edges). It deliberately does NOT build a fully declarative guard-engine (a central table that also OWNS the guard predicates and evaluates them to pick the next state). That is a **documented follow-on** (see "Follow-on" at the end) that builds on this seam once the edge coverage and unified mechanism exist. Guards remain the existing in-code conditionals; this plan makes the edges they fire explicit and centralizes the write.

---

## File Structure

```
controller/runtime/transitions.py                       (NEW — request_transition seam + TransitionError + ALLOWED_EXITS registry)
controller/runtime/controller.py                        (MODIFIED — next_mode → seam; tick if/elif → dispatch map; recipe_mode edges → seam)
controller/runtime/modes/base.py                        (MODIFIED — max-temp + switch-off writes → seam; ALLOWED_EXITS decl)
controller/runtime/modes/smoke.py                       (MODIFIED — 4 safety writes → seam; ALLOWED_EXITS)
controller/runtime/modes/hold.py                        (MODIFIED — 4 safety writes → seam; ALLOWED_EXITS)
controller/runtime/modes/{startup,reignite,monitor,manual,prime,shutdown}.py  (MODIFIED — ALLOWED_EXITS decls)
tests/characterization/test_mode_transitions.py         (NEW — Task 1: mode-file edge coverage)
tests/characterization/test_outer_transitions.py        (NEW — Task 2: controller.py + recipe_mode edge coverage)
tests/unit/runtime/test_request_transition.py           (NEW — Task 3: seam unit tests)
```

Two `ControlMode` subclasses are backed by `next_mode` targets that are data-driven (`control["next_mode"]`), not hardcoded — the FSM models those edges as "to = whatever `next_mode` holds", legality-checked against the union of a mode's declared exits.

---

## Task 1 — Characterize the mode-file safety-trip edges [COMMIT FIRST]

Fills inventory coverage gaps #1–#6: Hold setup_safety→Error/Reignite, Hold check_safety→Error/Reignite, Smoke check_safety (in-loop)→Error/Reignite, base inner-loop switch-off→Stop. These write `control["mode"]` from inside a work cycle and today have NO transition-level test.

**Files:**
- Create: `tests/characterization/test_mode_transitions.py`
- Reference (do not modify yet): `controller/runtime/modes/smoke.py:71-86,133-148`, `hold.py:108-126,317-332`, `base.py:401-409,511-517`

**Interfaces:**
- Consumes: the existing `test_modes_golden.py` harness pattern — an `InMemoryStore`/`ctx` fixture that runs one `ControlMode(...).run()` work cycle and lets you read the resulting `control`. Reuse that fixture module (import its builders); do NOT invent a new harness.
- Produces: named characterization tests other tasks re-run unchanged.

- [ ] **Step 1: Read the existing harness.** Open `tests/characterization/test_modes_golden.py` and identify the fixture/helpers that build `ctx`, seed `control`/`settings`, force a probe temperature, and run a single work cycle. Note how it already tests Smoke setup_safety flameout (the pattern to mirror for Hold).

- [ ] **Step 2: Write the failing Hold-flameout tests.** Add tests that drive each Hold safety edge and assert the EXACT writes from the inventory (side-effect key: U=updated True, WC=write happened, RR-=reigniteretries decremented, RLS=reignitelaststate, N=notification):

```python
# tests/characterization/test_mode_transitions.py
# Reuses the modes-golden harness builders (import them):
from tests.characterization.test_modes_golden import build_ctx, run_cycle  # adjust to real names

def test_hold_setup_safety_flameout_error(monkeypatch):
    # afterstarttemp condition -> evaluate_flameout == ERROR, retries == 0
    ctx, control, settings = build_ctx(mode="Hold", reigniteretries=0)
    control["safety"]["afterstarttemp"] = ...   # value that forces ERROR verdict (mirror the Smoke test)
    notes = _capture_notifications(ctx, monkeypatch)
    run_cycle("Hold", ctx)
    out = ctx.store.read_control()
    assert out["mode"] == "Error"
    assert out["updated"] is True
    assert "Grill_Error_02" in notes
    # reigniteretries NOT decremented on the Error branch:
    assert out["safety"]["reigniteretries"] == 0

def test_hold_setup_safety_flameout_reignite(monkeypatch):
    ctx, control, settings = build_ctx(mode="Hold", reigniteretries=1)
    control["safety"]["afterstarttemp"] = ...   # forces REIGNITE verdict (retries > 0)
    notes = _capture_notifications(ctx, monkeypatch)
    run_cycle("Hold", ctx)
    out = ctx.store.read_control()
    assert out["mode"] == "Reignite"
    assert out["updated"] is True
    assert out["safety"]["reigniteretries"] == 0        # decremented from 1
    assert out["safety"]["reignitelaststate"] == "Hold"
    assert "Grill_Error_03" in notes
```

Add the analogous in-loop `check_safety` variants for Hold (hold.py:317-332) and Smoke (smoke.py:133-148) — same assertions, but the trigger is a fed in-loop `ptemp` rather than the pre-loop `afterstarttemp`. Add the base inner-loop switch-off test (base.py:401-409): drive a work cycle with the input switch OFF and assert `out["mode"] == "Stop"`, `out["status"] == "active"`, `out["updated"] is True`. (`_capture_notifications` monkeypatches `ctx.notifications.send` to append to a list.)

- [ ] **Step 3: Run — expect GREEN against current code** (this is characterization of existing behavior, not TDD-red):

Run: `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/characterization/test_mode_transitions.py -q`
Expected: all pass. If a verdict-forcing value is wrong, adjust it against `controller/runtime/logic/safety.py:evaluate_flameout` until the intended branch fires (do NOT assert a value you haven't observed).

- [ ] **Step 4: Commit.**

```bash
git add tests/characterization/test_mode_transitions.py
git commit -F <msgfile>   # test(controller): characterize mode-file safety-trip transitions (Hold/in-loop Smoke/switch-off)
```

---

## Task 2 — Characterize the outer-loop + recipe transitions [COMMIT]

Fills inventory gaps #7–#12: `units_change→Stop`, Startup↔Prime prime-on-startup handshake, `next_mode()` field semantics (the `updated`-guard no-op + `setpoint if=="Hold"` rule — currently spied away), Reignite outer dispatch (`next_mode=reignitelaststate` + setpoint carry), and ALL `recipe_mode` internal edges.

**Files:**
- Create: `tests/characterization/test_outer_transitions.py`
- Reference: `controller/runtime/controller.py:88-98` (next_mode), `:103-191` (recipe_mode), `:332-340` (units_change), `:434-457` (startup/prime), `:494-504` (reignite)

**Interfaces:**
- Consumes: the `test_controller_loop_golden.py` harness (builds a `Controller` with an InMemoryStore). **Unlike** the loop-golden tests, these must NOT spy `next_mode`/`recipe_mode` — call the REAL methods and assert what they WRITE.
- Produces: named tests re-run unchanged by later tasks.

- [ ] **Step 1: `next_mode()` semantics — the two behaviors, directly.**

```python
def test_next_mode_transitions_when_not_updated(monkeypatch):
    ctrl = build_controller(monkeypatch)          # patches os.system; auto_power_off False
    c = ctrl.ctx.store.read_control(); c["updated"] = False
    ctrl.ctx.store.write_control(c, WriteKind.OVERWRITE, origin="test")
    ctrl.next_mode("Hold", setpoint=225)
    out = ctrl.ctx.store.read_control()
    assert out["mode"] == "Hold"
    assert out["primary_setpoint"] == 225         # Hold => setpoint applied
    assert out["updated"] is True

def test_next_mode_is_noop_when_already_updated(monkeypatch):
    ctrl = build_controller(monkeypatch)
    c = ctrl.ctx.store.read_control(); c["updated"] = True; c["mode"] = "Error"
    ctrl.ctx.store.write_control(c, WriteKind.OVERWRITE, origin="test")
    ctrl.next_mode("Smoke")                        # guard: must NOT overwrite
    out = ctrl.ctx.store.read_control()
    assert out["mode"] == "Error"                 # safety trip survives

def test_next_mode_forces_setpoint_zero_when_not_hold(monkeypatch):
    ctrl = build_controller(monkeypatch)
    c = ctrl.ctx.store.read_control(); c["updated"] = False; c["primary_setpoint"] = 300
    ctrl.ctx.store.write_control(c, WriteKind.OVERWRITE, origin="test")
    ctrl.next_mode("Smoke", setpoint=225)
    assert ctrl.ctx.store.read_control()["primary_setpoint"] == 0
```

- [ ] **Step 2: Recipe sub-machine edges.** Add tests over `recipe_mode()` for: step→step-mode dispatch (assert per-step `recipe.step`, `primary_setpoint=hold_temp`, `updated=False` written), reignite-during-recipe retry (mode returns "Reignite"+updated → recipe re-runs the step), cancel (a non-Recipe mode + updated during a step → break, requested mode left in control), normal end (steps exhausted → `mode="Stop"`, `updated=True`), and missing-file silent return (assert it returns `()` and leaves mode unchanged — pinning gotcha #9's current stuck-state behavior). Drive them with a tiny 2-step recipe fixture and a work_cycle stubbed to simulate each outcome.

- [ ] **Step 3: units_change→Stop, startup/prime handshake, reignite dispatch.** One test each, asserting the exact control writes from inventory rows 3, 15–16, 21. For the reignite dispatch test, seed `safety.reignitelaststate="Hold"` and `primary_setpoint=250` and assert `next_mode` is called with the carried setpoint.

- [ ] **Step 4: Run — expect GREEN.** `... uv run pytest tests/characterization/test_outer_transitions.py -q`. Confirm `os.system` is patched in `build_controller` (grep to verify) BEFORE running.

- [ ] **Step 5: Commit.** `test(controller): characterize outer-loop + recipe transitions (next_mode guard, recipe edges, units/prime/reignite)`

---

## Task 3 — Introduce the `request_transition()` seam (additive, no callers) [COMMIT]

**Files:**
- Create: `controller/runtime/transitions.py`
- Create: `tests/unit/runtime/test_request_transition.py`

**Interfaces — Produces:**
```python
# request_transition(store, control, to_mode, *, guarded, setpoint=_UNSET,
#                    reignite_from=None, notify=None, display=None, write=True) -> dict
#   guarded=True  reproduces Controller.next_mode(): flush+reread, then IF not control["updated"]:
#                 set mode, set primary_setpoint = (setpoint if to_mode=="Hold" else 0), updated=True, write. Returns fresh control.
#   guarded=False reproduces the inline safety writes: operate in place on `control`:
#                 push display (if given), set mode, if reignite_from: reigniteretries-=1 then reignitelaststate=reignite_from,
#                 set updated=True, write (OVERWRITE), send notify (if given). Returns control.
#   setpoint is only consulted on the guarded path (inline writes never touch primary_setpoint).
# raises TransitionError if to_mode not in the source mode's ALLOWED_EXITS (Task 10 wires the check;
#   in Task 3 the registry is empty so the check is a no-op passthrough).
```

- [ ] **Step 1: Write the seam file.** Reproduce BOTH mechanisms exactly, preserving field-write ORDER (inventory §3): guarded = mode→setpoint→updated→write; unguarded = display→mode→(RR-→RLS)→updated→write→notify.

```python
"""Single arbitrated seam for every controller mode transition. Reproduces the
two legacy mechanisms (guarded next_mode + unguarded inline safety writes)
verbatim so the transition contract lives in one place."""

from common.common import WriteKind

_UNSET = object()

# Filled in Task 10; empty here so the legality check is a no-op until then.
ALLOWED_EXITS: dict[str, set[str]] = {}


class TransitionError(RuntimeError):
    pass


def _check_legal(from_mode, to_mode):
    exits = ALLOWED_EXITS.get(from_mode)
    if exits is not None and to_mode not in exits:
        raise TransitionError(f"illegal transition {from_mode} -> {to_mode}")


def request_transition(store, control, to_mode, *, guarded, setpoint=_UNSET,
                       reignite_from=None, notify=None, display=None, write=True):
    from_mode = control.get("mode")
    _check_legal(from_mode, to_mode)

    if guarded:
        store.execute_control_writes()
        control = store.read_control()
        if not control["updated"]:
            control["mode"] = to_mode
            if setpoint is not _UNSET:
                control["primary_setpoint"] = setpoint if to_mode == "Hold" else 0
            control["updated"] = True
            if write:
                store.write_control(control, WriteKind.OVERWRITE, origin="control")
        return control

    # unguarded (inline safety write): operate in place, exact legacy order
    if display is not None:
        store.display_commands().push(display)
    control["mode"] = to_mode
    if reignite_from is not None:
        control["safety"]["reigniteretries"] -= 1
        control["safety"]["reignitelaststate"] = reignite_from
    control["updated"] = True
    if write:
        store.write_control(control, WriteKind.OVERWRITE, origin="control")
    if notify is not None:
        store.notifications_send(notify)   # see Step 2 re: the send seam
    return control
```

- [ ] **Step 2: Resolve the notify/display access.** The inline sites call `ctx.notifications.send(...)` and `ctx.store.display_commands().push(...)`. Decide the seam's dependency: pass `store` that exposes `display_commands()` and a notifications sender, OR pass `ctx` instead of `store`. Read `controller/runtime/context.py` (or wherever `ctx`/`store` are defined) and choose the parameter that both the mode files (have `ctx`) and `controller.py` (has `ctx`/`store`) can supply. Adjust the signature to match reality (e.g. `request_transition(ctx, control, ...)` reading `ctx.store` and `ctx.notifications`). Do NOT guess the attribute names — verify them.

- [ ] **Step 3: Unit-test the seam** in `tests/unit/runtime/test_request_transition.py` with a fake store/ctx recording writes/notifies/display pushes. Cover: guarded transition when not updated (mode+setpoint+updated+write, Hold rule and non-Hold→0); guarded no-op when already updated; unguarded Error write (display+mode+updated+write+notify, order via a recording list); unguarded Reignite write (RR- then RLS before updated); `write=False` suppresses the write. Assert the ORDER of recorded operations, not just the final state.

- [ ] **Step 4: Run** `... uv run pytest tests/unit/runtime/test_request_transition.py -q` → all pass. Full suite unchanged (nothing calls the seam yet): `... uv run pytest tests/characterization tests/e2e -q` → green.

- [ ] **Step 5: Commit.** `feat(controller): add request_transition seam (guarded + unguarded variants)`

---

## Task 4 — Repoint `smoke.py` safety writes to the seam [COMMIT]

Collapses the 4 duplicated inline blocks (smoke.py:71-77, 78-86, 133-139, 140-148) into `request_transition(..., guarded=False, ...)` calls.

**Files:** Modify `controller/runtime/modes/smoke.py`.

- [ ] **Step 1:** Import the seam: `from controller.runtime.transitions import request_transition`.
- [ ] **Step 2:** Replace the Error block (71-77) with a single call passing `to_mode="Error"`, `display=("text","ERROR")`, `notify="Grill_Error_02"`, `guarded=False`. Replace the Reignite block (78-86) with `to_mode="Reignite"`, `reignite_from="Smoke"` (which does RR- then RLS), `display=("text","Re-Ignite")`, `notify="Grill_Error_03"`, `guarded=False`. Do the same for the in-loop check_safety pair (133-148). Preserve the surrounding `status = "Inactive"` / `return True` control flow exactly (the seam does the writes; the `return True`→break stays in the mode).
- [ ] **Step 3: Run the smoke transition characterizations + smoke golden** `... uv run pytest tests/characterization/test_mode_transitions.py tests/characterization/test_modes_golden.py -k smoke tests/e2e -k smoke -q` → all green (the Smoke edges must be byte-identical).
- [ ] **Step 4: Commit.** `refactor(controller): route smoke.py safety transitions through the seam`

---

## Task 5 — Repoint `hold.py` safety writes to the seam [COMMIT]

Same as Task 4 for hold.py:108-117, 118-126, 317-323, 324-332 (with `reignite_from="Hold"`).

**Files:** Modify `controller/runtime/modes/hold.py`.

- [ ] **Step 1:** Import seam; replace the 4 blocks with `request_transition(guarded=False, ...)` calls mirroring Task 4 but `reignite_from="Hold"`. Preserve surrounding control flow.
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_mode_transitions.py -k hold tests/characterization/test_modes_golden.py -k hold tests/e2e -k hold -q` → green.
- [ ] **Step 3: Commit.** `refactor(controller): route hold.py safety transitions through the seam`

---

## Task 6 — Repoint `base.py` universal writes to the seam [COMMIT]

The two skeleton writes: max-temp→Error (base.py:511-517: display "ERROR", notify Grill_Error_01) and inner-loop switch-off→Stop (base.py:401-409: sets `status="active"`, no notify/display).

**Files:** Modify `controller/runtime/modes/base.py`.

- [ ] **Step 1:** Import seam. Replace max-temp block with `request_transition(to_mode="Error", display=("text","ERROR"), notify="Grill_Error_01", guarded=False)`, keeping the `break`. For switch-off: the seam doesn't set `status`; do the `control["status"]="active"` write adjacent to the call (or extend the seam with an optional `status=` kwarg — prefer the adjacent write to keep the seam minimal), then `request_transition(to_mode="Stop", guarded=False)`, keeping the `break`. Verify the resulting field-write order matches the inventory (status set before or after mode per the original — check base.py:401-409 and preserve it).
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_mode_transitions.py tests/characterization/test_modes_golden.py tests/e2e -q` → green (max-temp tests for Smoke AND Hold, switch-off test).
- [ ] **Step 3: Commit.** `refactor(controller): route base skeleton max-temp/switch-off transitions through the seam`

---

## Task 7 — Reimplement `Controller.next_mode()` on the seam [COMMIT]

**Files:** Modify `controller/runtime/controller.py`.

- [ ] **Step 1:** Import the seam. Replace the body of `next_mode(self, next_mode, setpoint=0)` (controller.py:88-98) with a single delegation: `return request_transition(self.ctx, self.ctx.store.read_control(), next_mode, guarded=True, setpoint=setpoint)` — but preserve today's exact sequence (flush+reread happen INSIDE the guarded seam path, so pass what the seam expects; verify the seam's guarded branch already does `execute_control_writes()` + `read_control()` and therefore `next_mode` should hand it a store/ctx, not a stale control). Keep the method name/signature (callers and the loop-golden spy rely on `self.next_mode(...)`).
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_outer_transitions.py tests/characterization/test_controller_loop_golden.py -q` → green (the 3 next_mode-semantics tests + the spied dispatch tests both pass).
- [ ] **Step 3: Commit.** `refactor(controller): reimplement next_mode on the transition seam`

---

## Task 8 — Repoint `recipe_mode` transitions to the seam [COMMIT]

**Files:** Modify `controller/runtime/controller.py` (`recipe_mode`, :103-191).

- [ ] **Step 1:** Route the recipe-internal mode writes (retry→"Recipe" at :160-164, normal-end→"Stop" at :184-189) through `request_transition(self.ctx, control, ..., guarded=False, write=True)`, preserving the per-step `updated=False`/`primary_setpoint=hold_temp` writes (those are step-data setup, NOT transitions — leave them as direct writes). The cancel `break` edges (:166-174) write no mode — leave them. Keep the missing-file silent return (:113-128) as-is (pinned by Task 2).
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_outer_transitions.py -k recipe tests/characterization/test_controller_loop_golden.py -k recipe -q` → green.
- [ ] **Step 3: Commit.** `refactor(controller): route recipe_mode transitions through the seam`

---

## Task 9 — Convert `tick()` dispatch to a `{mode: handler}` map [COMMIT]

Replaces the if/elif ladder (controller.py:347-504) with a dispatch dict, mirroring the Phase D god-route→dispatch-map pattern. Pure structural — each branch becomes a small `_dispatch_<mode>(self)` method registered in a class-level map; the terminal Stop/Error block and the `critical_error`/`updated` gate stay in `tick()`.

**Files:** Modify `controller/runtime/controller.py`.

- [ ] **Step 1:** Extract each `elif self.control["mode"] == "X":` body into `_dispatch_X(self)`; build `_MODE_DISPATCH = {"Prime": _dispatch_prime, "Startup": _dispatch_startup, "Smoke": ..., "Hold": ..., "Shutdown": ..., "Monitor": ..., "Manual": ..., "Recipe": ..., "Reignite": ...}`. In `tick()`, after the `updated`/`critical_error` gate and the Stop/Error terminal block, do `handler = _MODE_DISPATCH.get(self.control["mode"]); if handler: handler(self)`. Keep the Stop/Error block inline (it is terminal cleanup, not a per-mode work cycle). **Preserve the os.system Shutdown line inside `_dispatch_shutdown`** exactly (still module-level `os.system`).
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_controller_loop_golden.py tests/characterization/test_outer_transitions.py -q` → green (the loop-golden dispatch tests assert the same calls happen for each mode).
- [ ] **Step 3: Commit.** `refactor(controller): dispatch tick() modes through a {mode: handler} map`

---

## Task 10 — Declare `ALLOWED_EXITS` and enforce edge legality [COMMIT]

Makes the graph explicit and rejects illegal transitions — the "proper state machine" enforcement.

**Files:** Modify `controller/runtime/transitions.py` (fill `ALLOWED_EXITS`), all mode files + controller.py as needed to expose each mode's declared exits.

- [ ] **Step 1:** From the inventory edge table, populate `ALLOWED_EXITS` with every real edge's `from → {to,...}` set. Include the data-driven `next_mode` targets as the union of what each cycling mode can legally reach (Startup→{Prime, Smoke, Hold, Stop, Error, Reignite}, Reignite→{Smoke, Hold, Startup, Stop, Error}, Smoke/Hold→{Error, Reignite, Stop, their next_mode}, Prime→{Startup, Stop}, Shutdown→{Stop}, Monitor→{Stop, Error}, Manual→{Stop, Error}, Recipe→{Recipe, Stop, + step modes}, Stop/Error→terminal). Derive the exact sets from Tasks 1–2's now-green tests, not guesses.
- [ ] **Step 2:** The seam's `_check_legal` (already written in Task 3) now fires. Add one test asserting an illegal edge raises `TransitionError` (e.g. `request_transition(ctx, {"mode":"Manual"}, "Reignite", guarded=False)` raises) and that every edge exercised by Tasks 1–2 is legal (no `TransitionError` in the full suite).
- [ ] **Step 3: Add a graph-dump inspectability test** — a test that imports `ALLOWED_EXITS` and asserts the full edge set matches a committed snapshot (so future edits to the graph are visible in review). This is the single-place "whole state machine" view the design calls for.
- [ ] **Step 4: Run the FULL controller surface** `... uv run pytest tests/characterization tests/e2e tests/unit/runtime -q` → all green, no `TransitionError` from any real path.
- [ ] **Step 5: Commit.** `feat(controller): declare ALLOWED_EXITS and enforce transition legality`

---

## Task 11 — Final verification [COMMIT if formatting only]

- [ ] **Step 1: Full suite** `... uv run pytest tests/ -q` → same pass count as the pre-refactor baseline PLUS the new Task 1/2/3/10 tests; zero failures. Confirm the existing goldens (`test_modes_golden`, `test_controller_loop_golden`, `test_work_cycle_e2e`) are byte-for-byte unchanged (no golden regenerated).
- [ ] **Step 2: Safety audit** — grep the final diff for `os.system`/`subprocess`/`sudo`/`shutdown`/`reboot`; confirm the ONLY such call is the preserved `_dispatch_shutdown` `os.system`, still module-level, and that every controller-constructing test patches it. Confirm no test ever executed it (recorder shows the args, never ran).
- [ ] **Step 3: ruff** `uvx ruff format` + `uvx ruff check` the full changed set.
- [ ] **Step 4: Commit** (only if formatting changed): `style(controller): ruff format transition FSM`

**Rollback:** revert the branch — the seam is additive until Tasks 4–9 repoint call sites, so a partial revert restores the inline writes cleanly.

---

## Follow-on (explicitly OUT of scope, documented for later)

- **Declarative guard-engine:** promote the guard predicates (flameout verdict, max-temp, switch-off, recipe-end, boot-to-monitor) into the transition table so the FSM EVALUATES guards and picks the next state, rather than the mode conditionals calling `request_transition`. Builds on this plan's seam + `ALLOWED_EXITS` + full edge coverage.
- **Fix the two latent stuck-states surfaced by the inventory** (gotcha #6 Stop dead `status="inactive"` assignment; gotcha #9 Recipe silent no-op on missing file → should be an explicit `Recipe→Stop`). Each is a behavior change requiring its own characterization flip — do NOT fold into this behavior-preserving refactor.
- **`status` as a modeled second dimension** (gotcha #4): the power-on-vs-off decision keys off `status=="monitor" and mode=="Error"`; a fuller FSM would model `status` as an orthogonal axis with its own transitions.

## Self-Review

- **Spec coverage:** every inventory edge (31 + 7 recipe) is either characterized (Tasks 1–2) or repointed through the seam (Tasks 4–9) and legality-declared (Task 10). ✅
- **Placeholder scan:** the verdict-forcing values in Task 1 (`afterstarttemp = ...`) and the harness helper names are marked "adjust to real names / observe, don't guess" — deliberately, because the exact fixture API must be read from the live test file at execution time; the side-effect ASSERTIONS (the contract) are concrete. The seam code is complete and real. Task 3 Step 2 explicitly requires verifying `ctx`/`store` attribute names before finalizing the signature.
- **Type consistency:** `request_transition` signature is identical across Tasks 3–9; `guarded`/`setpoint`/`reignite_from`/`notify`/`display` used consistently; `ALLOWED_EXITS`/`TransitionError` names stable.
- **Safety:** the `os.system` shutdown hazard is called out in Global Constraints, re-checked in Tasks 2/9/11, and never executed.
