# Mode-Transition FSM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize PiFire's implicit mode-transition logic into an explicit, declarative state machine — Phase 1: one arbitrated `request_transition()` seam + per-mode legal-exit declarations + a `{mode: handler}` dispatch map (replacing ~21 scattered `control["mode"]=` writes across 4 files); Phase 2: a declarative per-phase guard-engine that drives the transitions from data. Observable runtime behavior preserved (defined by the characterization suite + existing goldens), not the legacy mechanisms.

**Architecture:** A new `controller/runtime/transitions.py` owns `request_transition(ctx, control, to_mode, *, kind, ...)`, one clean seam every genuine mode transition routes through. `kind` (`natural` | `safety` | `terminal`) sets priority: `natural` yields to an already-requested transition (so mid-cycle safety trips win), `safety`/`terminal` are authoritative. Each `ControlMode` declares `ALLOWED_EXITS`; the seam asserts legality (dev/test). `Controller.tick()`'s if/elif ladder becomes a dispatch map. A few bespoke writes that manipulate `updated` in non-standard ways (the Startup↔Prime prime-on-startup handshake; the recipe reignite-retry) are NOT genuine FSM edges — they stay as direct writes (characterized but not seam-routed). Design reference: [`../specs/2026-07-18-mode-transition-fsm-design.md`](../specs/2026-07-18-mode-transition-fsm-design.md); edge inventory: [`../specs/2026-07-18-mode-transition-inventory.md`](../specs/2026-07-18-mode-transition-inventory.md).

**Tech Stack:** Python 3.14, pytest, `uv`/`uvx ruff`, Serena symbolic tools.

## Global Constraints

- Python 3.14. `except (A, B)` is canonical; do NOT "fix" bare `except A, B` — ruff owns that.
- **TEST COMMAND (exact, always):** `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`. Bare `python`/`pytest` gives FALSE failures; missing the offscreen/dummy vars HANGS.
- **SAFETY — READ THIS FIRST.** `controller/runtime/controller.py:477` runs `os.system("sleep 3 && sudo shutdown -h now &")` on the `Shutdown→Stop` edge when `settings["shutdown"]["auto_power_off"]` is true. **Every test that constructs a `Controller` or drives the Shutdown/Stop path MUST `monkeypatch.setattr(os, "system", <recorder>)`** (it is deliberately module-level — see controller.py:18) AND set `auto_power_off=False` in the settings fixture unless the test is specifically asserting that edge with `os.system` already patched. Before running ANY new controller test, grep the touched code for `os.system`/`subprocess`/`sudo`/`shutdown`/`reboot` and confirm it is neutralized. (Repo history: 2 real reboots from unmocked paths.)
- **Observable-behavior-preserving — NOT mechanism-preserving.** We must preserve *how the controller operates as observed from outside*, not the exact legacy code paths. The observable contract is: (a) the **values** of the persisted control fields (`mode`, `status`, `updated`, `next_mode`, `primary_setpoint`, `safety.reigniteretries`, `safety.reignitelaststate`) as another process/display/web would read them from the datastore at each settle point; (b) the notifications sent, display commands pushed, metrics written, and grill outputs toggled; (c) the mode a given event sequence lands you in. We are FREE to use one clean transition mechanism instead of the two legacy ones, to change the number/shape of internal writes, and to change intra-write field order — because a `write_control(OVERWRITE)` is a single atomic replace, so a concurrent reader never sees partial field order (inventory gotcha #7 is a non-issue). The definition of "observable" is exactly what the existing goldens (`test_modes_golden.py`, `test_controller_loop_golden.py`, `test_work_cycle_e2e.py`) + the new transition characterization suite (Tasks 1–2) assert — those MUST stay green. **Because correctness is now defined by the tests, the characterization must be thorough:** Tasks 1–2 assert the FULL observable control subset per edge (not a couple of fields), so a cleaner reimplementation cannot silently drift an unpinned behavior.
- **The arbitration OUTCOME is load-bearing (its mechanism is not).** A mid-cycle safety trip must still beat the post-cycle natural transition — a flameout during Smoke lands in Reignite/Error, never the natural `next_mode`. Today that falls out of the `updated`-guard accident; the seam models it explicitly as transition *priority* (safety/terminal/external are authoritative; a "natural" progression yields to any transition already requested this cycle). Preserve the OUTCOME; the `updated` field's observed values still match (the goldens pin them), but the seam owns setting/checking it cleanly rather than each call site hand-managing it.
- **Setpoint outcome.** Natural transitions still resolve `primary_setpoint = setpoint if to=="Hold" else 0`; safety/terminal transitions still leave `primary_setpoint` untouched. This is part of the observable contract (pinned in Task 2), expressed once in the seam by transition kind — not by mirroring which legacy function did it.
- Edits via Serena symbolic tools where practical (`create_text_file`, `replace_symbol_body`, `insert_before_symbol`, `replace_content`). **Worktree gotcha:** if executing in a git worktree, Serena may edit the main checkout instead — after each Serena edit, confirm the WORKTREE file changed (not `/home/dannyb/sources/PiFire`); if it misfires, use plain Edit.
- Before every commit: `uvx ruff format <changed>` then `uvx ruff check <changed>`.
- Commit with `git commit -F <msgfile>` (zsh eats backticks in `-m`). Co-author trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch `refactor/mode-transition-fsm`, off `massive-reworks-and-new-ui`. One edge-family per commit so a regression bisects to a single commit. This is core, safety-adjacent control flow — do NOT batch.

## Scope

The plan is delivered in **two phases on one branch**, each independently shippable:

- **Phase 1 (Tasks 1–11) — clean single-mechanism transition FSM:** one `request_transition()` seam (transitions carry a *kind* — natural / safety / terminal — that determines priority), all 21 sites repointed onto it, the `{mode: handler}` dispatch map, and per-mode legal-exit enforcement. Because we preserve observable behavior rather than the legacy mechanisms, the seam is designed for clarity, not bug-for-bug fidelity — it just has to make the characterization suite + goldens pass.
- **Phase 2 (Tasks 12–17) — declarative guard-engine:** promote the transition guards into per-phase declarative edge tables (`GUARDS`) that an engine (`evaluate_phase`) drives at the existing pipeline phases, so the whole state graph lives in data and the smoke↔hold safety-check duplication disappears. Guards stop being in-mode conditionals; they become data.

Phase 2 depends entirely on Phase 1 (the seam it calls, the coverage that gates it). If you stop at the Task 11 checkpoint you still have a coherent, shippable FSM.

**Still out of scope** (see "Follow-on"): the two latent stuck-states the inventory found (each a behavior change), a fully modeled `status` second dimension, and the bespoke recipe/startup `updated` handshakes.

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
# request_transition(ctx, control, to_mode, *, kind, setpoint=_UNSET,
#                    reignite_from=None, notify=None, display=None) -> dict
#   kind == "natural"   -> the post-cycle progression. Reads the latest control to see if a
#                          higher-priority transition already landed this cycle; if so (control
#                          already updated) it YIELDS (no-op). Otherwise sets mode, resolves
#                          primary_setpoint = (setpoint if to_mode=="Hold" else 0), updated=True,
#                          writes. Returns the fresh control.
#   kind in ("safety","terminal") -> AUTHORITATIVE. Always applies, in place on `control`:
#                          push display (if given), set mode, if reignite_from: reigniteretries-=1
#                          and reignitelaststate=reignite_from, updated=True, write, send notify.
#   Only "natural" reads state / yields. Authoritative kinds never touch primary_setpoint.
#   raises TransitionError if to_mode not in the source mode's ALLOWED_EXITS (Task 10 wires the
#   check; the Task-3 registry is empty so it is a no-op passthrough until then).
```

This is ONE clean mechanism, not a reproduction of the two legacy ones. The only behavioral requirement is that its OUTPUT matches the characterization suite + goldens: natural transitions yield to an already-requested one (so safety trips win), authoritative ones always apply. Priority lives in `kind`, not in whether a caller happened to use `next_mode` vs an inline write.

- [ ] **Step 1: Verify the dependency surface FIRST.** The call sites live in mode files (which hold `ctx`) and `controller.py` (holds `self.ctx`). Read `controller/runtime/context.py` (and how smoke.py reaches `ctx.store.display_commands()` / `ctx.notifications.send()` / `ctx.store.execute_control_writes()` / `read_control` / `write_control`) and confirm the exact attribute names. The seam takes `ctx` and reaches `ctx.store` + `ctx.notifications`. Do NOT guess names — verify against live code before writing the body.

- [ ] **Step 2: Write the seam file** (adjust attribute access to what Step 1 found):

```python
"""Single seam for every controller mode transition. All mode changes route
through request_transition; transition *kind* sets priority. Designed for
clarity -- correctness is defined by the transition characterization suite +
the mode/loop goldens, not by mirroring the old next_mode/inline-write split."""

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


def request_transition(ctx, control, to_mode, *, kind, setpoint=_UNSET,
                       reignite_from=None, notify=None, display=None):
    store = ctx.store
    _check_legal(control.get("mode"), to_mode)

    if kind == "natural":
        # yield to any higher-priority transition already requested this cycle
        store.execute_control_writes()
        control = store.read_control()
        if control["updated"]:
            return control
        control["mode"] = to_mode
        if setpoint is not _UNSET:
            control["primary_setpoint"] = setpoint if to_mode == "Hold" else 0
        control["updated"] = True
        store.write_control(control, WriteKind.OVERWRITE, origin="control")
        return control

    # authoritative: safety / terminal
    if display is not None:
        store.display_commands().push(display)
    control["mode"] = to_mode
    if reignite_from is not None:
        control["safety"]["reigniteretries"] -= 1
        control["safety"]["reignitelaststate"] = reignite_from
    control["updated"] = True
    store.write_control(control, WriteKind.OVERWRITE, origin="control")
    if notify is not None:
        ctx.notifications.send(notify)
    return control
```

- [ ] **Step 3: Unit-test the seam** in `tests/unit/runtime/test_request_transition.py` with a fake ctx (fake `store` + `notifications`) recording writes/notifies/display pushes. Cover: `kind="natural"` applies when not already updated (mode+setpoint+updated+write; Hold rule and non-Hold→0); `kind="natural"` YIELDS (no-op) when control is already updated; `kind="safety"` Error write (display+mode+updated+write+notify); `kind="safety"` Reignite write (reigniteretries decremented + reignitelaststate set); `kind="terminal"` Stop write. Assert the resulting persisted control values + the side effects (notify/display), which is the observable contract — no need to assert intra-write field order.

- [ ] **Step 4: Run** `... uv run pytest tests/unit/runtime/test_request_transition.py -q` → all pass. Full suite unchanged (nothing calls the seam yet): `... uv run pytest tests/characterization tests/e2e -q` → green.

- [ ] **Step 5: Commit.** `feat(controller): add request_transition seam (natural/safety/terminal kinds)`

---

## Task 4 — Repoint `smoke.py` safety writes to the seam [COMMIT]

Collapses the 4 duplicated inline blocks (smoke.py:71-77, 78-86, 133-139, 140-148) into `request_transition(..., kind="safety", ...)` calls.

**Files:** Modify `controller/runtime/modes/smoke.py`.

- [ ] **Step 1:** Import the seam: `from controller.runtime.transitions import request_transition`.
- [ ] **Step 2:** Replace the Error block (71-77) with a single call passing `to_mode="Error"`, `kind="safety"`, `display=("text","ERROR")`, `notify="Grill_Error_02"`. Replace the Reignite block (78-86) with `to_mode="Reignite"`, `kind="safety"`, `reignite_from="Smoke"` (which does the retries decrement + reignitelaststate), `display=("text","Re-Ignite")`, `notify="Grill_Error_03"`. Do the same for the in-loop check_safety pair (133-148). Preserve the surrounding `status = "Inactive"` / `return True` control flow exactly (the seam does the writes; the `return True`→break stays in the mode).
- [ ] **Step 3: Run the smoke transition characterizations + smoke golden** `... uv run pytest tests/characterization/test_mode_transitions.py tests/characterization/test_modes_golden.py -k smoke tests/e2e -k smoke -q` → all green (the Smoke edges must be byte-identical).
- [ ] **Step 4: Commit.** `refactor(controller): route smoke.py safety transitions through the seam`

---

## Task 5 — Repoint `hold.py` safety writes to the seam [COMMIT]

Same as Task 4 for hold.py:108-117, 118-126, 317-323, 324-332 (with `reignite_from="Hold"`).

**Files:** Modify `controller/runtime/modes/hold.py`.

- [ ] **Step 1:** Import seam; replace the 4 blocks with `request_transition(kind="safety", ...)` calls mirroring Task 4 but `reignite_from="Hold"`. Preserve surrounding control flow.
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_mode_transitions.py -k hold tests/characterization/test_modes_golden.py -k hold tests/e2e -k hold -q` → green.
- [ ] **Step 3: Commit.** `refactor(controller): route hold.py safety transitions through the seam`

---

## Task 6 — Repoint `base.py` universal writes to the seam [COMMIT]

The two skeleton writes: max-temp→Error (base.py:511-517: display "ERROR", notify Grill_Error_01) and inner-loop switch-off→Stop (base.py:401-409: sets `status="active"`, no notify/display).

**Files:** Modify `controller/runtime/modes/base.py`.

- [ ] **Step 1:** Import seam. Replace max-temp block with `request_transition(to_mode="Error", kind="safety", display=("text","ERROR"), notify="Grill_Error_01")`, keeping the `break`. For switch-off: the seam doesn't set `status`; do the `control["status"]="active"` write adjacent to the call, then `request_transition(to_mode="Stop", kind="terminal")`, keeping the `break`. (Intra-write order is unobservable — a single OVERWRITE — so just ensure the final persisted `status="active"` + `mode="Stop"` + `updated=True` match; the switch-off characterization from Task 1 pins it.)
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_mode_transitions.py tests/characterization/test_modes_golden.py tests/e2e -q` → green (max-temp tests for Smoke AND Hold, switch-off test).
- [ ] **Step 3: Commit.** `refactor(controller): route base skeleton max-temp/switch-off transitions through the seam`

---

## Task 7 — Reimplement `Controller.next_mode()` on the seam [COMMIT]

**Files:** Modify `controller/runtime/controller.py`.

- [ ] **Step 1:** Import the seam. Replace the body of `next_mode(self, next_mode, setpoint=0)` (controller.py:88-98) with `return request_transition(self.ctx, self.ctx.store.read_control(), next_mode, kind="natural", setpoint=setpoint)` — the `natural` kind already does the flush+reread+yield-if-updated, so this is behavior-equivalent. Keep the method name/signature (callers and the loop-golden spy rely on `self.next_mode(...)`).
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_outer_transitions.py tests/characterization/test_controller_loop_golden.py -q` → green (the 3 next_mode-semantics tests + the spied dispatch tests both pass).
- [ ] **Step 3: Commit.** `refactor(controller): reimplement next_mode on the transition seam`

---

## Task 8 — Repoint `recipe_mode` transitions to the seam [COMMIT]

**Files:** Modify `controller/runtime/controller.py` (`recipe_mode`, :103-191).

- [ ] **Step 1:** Route ONLY the recipe normal-end→"Stop" write (:184-189) through `request_transition(self.ctx, control, "Stop", kind="terminal")`. LEAVE as bespoke direct writes: the reignite-retry handshake (:160-164, which CLEARS `updated` then sets mode="Recipe" — not a standard authoritative transition), the per-step `updated=False`/`primary_setpoint=hold_temp` setup writes, the cancel `break` edges (:166-174, no mode write), and the missing-file silent return (:113-128). All of these are pinned by Task 2 and stay unchanged — the seam is for genuine transitions, not the recipe's bespoke `updated` bookkeeping.
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
- [ ] **Step 2:** The seam's `_check_legal` (already written in Task 3) now fires. Add one test asserting an illegal edge raises `TransitionError` (e.g. `request_transition(ctx, {"mode":"Manual"}, "Reignite", kind="safety")` raises) and that every edge exercised by Tasks 1–2 is legal (no `TransitionError` in the full suite).
- [ ] **Step 3: Add a graph-dump inspectability test** — a test that imports `ALLOWED_EXITS` and asserts the full edge set matches a committed snapshot (so future edits to the graph are visible in review). This is the single-place "whole state machine" view the design calls for.
- [ ] **Step 4: Run the FULL controller surface** `... uv run pytest tests/characterization tests/e2e tests/unit/runtime -q` → all green, no `TransitionError` from any real path.
- [ ] **Step 5: Commit.** `feat(controller): declare ALLOWED_EXITS and enforce transition legality`

---

## Task 11 — Phase 1 verification checkpoint [COMMIT if formatting only]

Phase 1 (seam + dispatch map + legality) is independently shippable here — if you stop after this task you have a clean single-mechanism FSM. Phase 2 (below) then makes it declarative.

- [ ] **Step 1: Full suite** `... uv run pytest tests/ -q` → same pass count as the pre-refactor baseline PLUS the new Task 1/2/3/10 tests; zero failures. Confirm the existing goldens (`test_modes_golden`, `test_controller_loop_golden`, `test_work_cycle_e2e`) are byte-for-byte unchanged (no golden regenerated).
- [ ] **Step 2: Safety audit** — grep the diff for `os.system`/`subprocess`/`sudo`/`shutdown`/`reboot`; confirm the ONLY such call is the preserved `_dispatch_shutdown` `os.system`, still module-level, and that every controller-constructing test patches it. Confirm no test ever executed it (recorder shows the args, never ran).
- [ ] **Step 3: ruff** `uvx ruff format` + `uvx ruff check` the full changed set.
- [ ] **Step 4: Commit** (only if formatting changed): `style(controller): ruff format transition FSM (phase 1)`

---

# Phase 2 — Declarative guard-engine

Promote the transition *guards* into declarative per-phase edge tables that an engine evaluates, so the whole state graph (states + guards + targets + effects) lives in data and the smoke↔hold `check_safety`/`setup_safety` duplication disappears. Builds directly on Phase 1's seam + coverage + `ALLOWED_EXITS`. **Hard constraint:** guards MUST keep evaluating at their current pipeline phases — `pre_loop` (`setup_safety`, after the first probe read) and `pre_act` (universal max-temp + `check_safety`, in-loop BEFORE any actuation) — because moving a guard relative to actuation changes whether the auger/fan cycle on the trip tick (observable). `should_exit` loop-exit conditions (timer/exit_temp) are NOT transitions (they break and let the outer `next_mode` fire) and stay as-is.

## Task 12 — Characterize guard-phase / actuation timing [COMMIT FIRST of Phase 2]

The net for the engine: pin the behaviors a phased rewrite could disturb.

**Files:** extend `tests/characterization/test_mode_transitions.py`.

- [ ] **Step 1:** Using the modes-golden harness (which records grill-platform output calls), add tests asserting: (a) an in-loop max-temp trip breaks BEFORE actuation — `grill.auger_on` is NOT called on the trip tick; (b) a `check_safety` flameout trip breaks before `on_tick` — same no-actuation assertion; (c) `setup_safety` returning `Inactive` skips the loop entirely but STILL runs `teardown` (assert teardown side effects happen, on_tick does not); (d) priority within a phase — if both a reignite and an error condition could match, the one that fires today wins (observe current order, pin it).
- [ ] **Step 2: Run — expect GREEN** against current code. `... uv run pytest tests/characterization/test_mode_transitions.py -q`.
- [ ] **Step 3: Commit.** `test(controller): pin guard-phase actuation timing (trip-before-actuate, Inactive-skips-loop)`

## Task 13 — Introduce the phased guard-engine (additive, unused) [COMMIT]

**Files:** `controller/runtime/transitions.py` (add `Edge`, `GUARDS`, `evaluate_phase`, guard predicates); `tests/unit/runtime/test_guard_engine.py` (NEW).

**Interfaces — Produces:**
```python
# @dataclass(frozen=True)
# class Edge: guard: Callable[[ctx, control, ptemp, now], bool]; to: str; kind: str
#             notify: str|None=None; display: tuple|None=None; reignite_from_self: bool=False
# GUARDS: dict[str, dict[str, list[Edge]]]   # {mode: {"pre_loop":[...], "pre_act":[...]}} ; "*" mode = applies to all
# def evaluate_phase(mode_obj, ctx, phase, now, ptemp) -> bool:
#     walk GUARDS[mode].get(phase,[]) + GUARDS["*"].get(phase,[]) in priority order;
#     first Edge whose guard(...) is True -> request_transition(ctx, control, edge.to, kind=edge.kind,
#         notify=edge.notify, display=edge.display,
#         reignite_from=mode_obj.name if edge.reignite_from_self else None) ; return True. Else False.
# guard predicates (pure): flameout_error, flameout_reignite (wrap logic/safety.evaluate_flameout),
#     over_max_temp (wrap logic/safety.over_max_temp), switch_off.
```

- [ ] **Step 1:** Read `controller/runtime/logic/safety.py` for the exact `evaluate_flameout`/`over_max_temp` signatures and the smoke/hold call args, so the guard predicates wrap them faithfully. Write `Edge`/`GUARDS` (empty for now)/`evaluate_phase`/predicates.
- [ ] **Step 2:** Unit-test `evaluate_phase` with a fake mode_obj/ctx: an edge whose guard is True fires `request_transition` with the edge's params (and `reignite_from=mode.name` when `reignite_from_self`); priority (first match wins); no match → returns False, no write.
- [ ] **Step 3: Run** `... uv run pytest tests/unit/runtime/test_guard_engine.py -q` + full suite (GUARDS empty, engine unused → nothing changes).
- [ ] **Step 4: Commit.** `feat(controller): add phased guard-engine (Edge/GUARDS/evaluate_phase), unused`

## Task 14 — Wire `evaluate_phase` into base.run() phase points [COMMIT]

**Files:** `controller/runtime/modes/base.py`.

- [ ] **Step 1:** At the existing `pre_loop` point (where `setup_safety` is called, ~base.py:321) add `if evaluate_phase(self, ctx, "pre_loop", now, ptemp): <abort as setup_safety Inactive does>`. At the `pre_act` point (SAFETY section, ~base.py:507-517, where max-temp + `check_safety` run) add `if evaluate_phase(self, ctx, "pre_act", now, ptemp): break`. Keep the existing `setup_safety`/`check_safety`/max-temp calls IN PLACE for now — with `GUARDS` still empty the engine no-ops, so this is a behavior-preserving insertion. Order the engine call so its priority matches today (engine first vs override first — since only one will fire per phase once migration completes, but during the transition BOTH run; ensure no double-fire by having the engine's `request_transition` be authoritative and the subsequent override see `updated` already set and no-op — verify Smoke golden stays green with the engine wired but empty).
- [ ] **Step 2: Run** full modes-golden + e2e → green (engine empty = no-op).
- [ ] **Step 3: Commit.** `refactor(controller): evaluate_phase hooks in base.run() (guards empty, no-op)`

## Task 15 — Migrate Smoke guards → declarative edges [COMMIT]

**Files:** `controller/runtime/transitions.py` (fill `GUARDS["Smoke"]`), `controller/runtime/modes/smoke.py` (delete overrides).

- [ ] **Step 1:** Add `GUARDS["Smoke"] = {"pre_loop": [Edge(flameout_error→"Error", kind="safety", notify="Grill_Error_02", display=("text","ERROR")), Edge(flameout_reignite→"Reignite", kind="safety", reignite_from_self=True, notify="Grill_Error_03", display=("text","Re-Ignite"))], "pre_act": [<same two, in-loop variant>]}` (priority: error before reignite, matching today). Then `safe_delete_symbol` smoke's `setup_safety` and `check_safety` overrides (now engine-driven) — the seam calls added in Task 4 lived in those overrides, so deleting the overrides removes them; the engine now issues the identical `request_transition`.
- [ ] **Step 2: Run** `... uv run pytest tests/characterization/test_mode_transitions.py tests/characterization/test_modes_golden.py -k smoke tests/characterization/test_mode_transitions.py -k switch tests/e2e -k smoke -q` → the Smoke Error/Reignite edges (both pre_loop and pre_act) fire identically. Full suite green.
- [ ] **Step 3: Commit.** `refactor(controller): Smoke safety guards are declarative edges`

## Task 16 — Migrate Hold + universal guards (max-temp, switch-off) → declarative edges [COMMIT]

**Files:** `transitions.py` (`GUARDS["Hold"]`, `GUARDS["*"]`), `hold.py` + `base.py` (delete overrides/inline checks).

- [ ] **Step 1:** Add `GUARDS["Hold"]` mirroring Smoke with `reignite_from_self=True` (RLS="Hold"); delete hold's `setup_safety`/`check_safety` overrides. Add the universal guards to `GUARDS["*"]["pre_act"]`: `Edge(over_max_temp→"Error", kind="safety", notify="Grill_Error_01", display=("text","ERROR"))` and `Edge(switch_off→"Stop", kind="terminal")` (the switch-off also sets `status="active"` — either add a `status` field to `Edge`/`request_transition` for this one edge, or keep the `status="active"` write adjacent in base.run() and let the edge do the mode write; prefer the smallest change that keeps the switch-off characterization green). Delete the inline max-temp + switch-off blocks from base.run() (Task 6's seam calls) now that they're engine edges.
- [ ] **Step 2: Run** full modes-golden + mode_transitions + e2e → green (Hold Error/Reignite, universal max-temp for Smoke AND Hold, switch-off).
- [ ] **Step 3: Commit.** `refactor(controller): Hold + universal (max-temp/switch-off) guards are declarative edges`

## Task 17 — Phase 2 verification + full graph snapshot [COMMIT]

- [ ] **Step 1:** Extend the Task-10 graph-dump test so the committed snapshot now includes the guard edges (`{mode: {phase: [(guard_name, to, kind)]}}`) alongside `ALLOWED_EXITS` — the whole declarative state machine in one asserted view.
- [ ] **Step 2:** Confirm every `GUARDS` edge's `to` is in the source mode's `ALLOWED_EXITS` (add an assertion iterating `GUARDS`); this cross-checks the two declarations agree.
- [ ] **Step 3: Full suite + safety audit** — `... uv run pytest tests/ -q` green, goldens byte-unchanged; re-run the `os.system` audit from Task 11 Step 2.
- [ ] **Step 4: ruff + Commit.** `feat(controller): declarative guard-engine complete; graph snapshot covers guards`

**Rollback:** revert the branch. Phase 1's seam is additive until Tasks 4–9 repoint call sites; Phase 2's engine is additive until Tasks 15–16 delete the overrides — so a partial revert to the Phase 1 checkpoint (Task 11) is clean.

---

## Follow-on (explicitly OUT of scope, documented for later)

- **Fix the two latent stuck-states surfaced by the inventory** (gotcha #6 Stop dead `status="inactive"` assignment; gotcha #9 Recipe silent no-op on missing file → should be an explicit `Recipe→Stop`). Each is a behavior CHANGE requiring its own characterization flip — do NOT fold into this behavior-preserving refactor.
- **`status` as a modeled second dimension** (gotcha #4): the power-on-vs-off decision keys off `status=="monitor" and mode=="Error"`; a fuller FSM would model `status` as an orthogonal axis with its own transitions.
- **Recipe reignite-retry + Startup↔Prime handshakes** (the bespoke `updated`-manipulating writes left as direct writes in Phase 1) could later be modeled as explicit sub-states, but their non-standard `updated` handling makes them poor fits for the current engine.

## Self-Review

- **Spec coverage:** every inventory edge (31 + 7 recipe) is characterized (Tasks 1–2, 12), repointed through the seam (Tasks 4–9), legality-declared (Task 10), and — for the safety/terminal edges — made declarative (Tasks 15–16). Guard-phase actuation timing is pinned (Task 12) before the engine (Tasks 13–17). ✅
- **Placeholder scan:** the verdict-forcing values in Task 1 (`afterstarttemp = ...`) and the harness helper names are marked "adjust to real names / observe, don't guess" — deliberately, because the exact fixture API must be read from the live test file at execution time; the side-effect ASSERTIONS (the contract) are concrete. The seam code is complete and real. Task 3 Step 2 explicitly requires verifying `ctx`/`store` attribute names before finalizing the signature.
- **Type consistency:** `request_transition` signature is identical across Tasks 3–9; `kind`/`setpoint`/`reignite_from`/`notify`/`display` used consistently; `ALLOWED_EXITS`/`TransitionError` names stable.
- **Safety:** the `os.system` shutdown hazard is called out in Global Constraints, re-checked in Tasks 2/9/11, and never executed.
