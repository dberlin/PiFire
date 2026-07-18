# Mode-Transition FSM — Design

**Date:** 2026-07-18
**Status:** Draft design (for review). Successor to the Phase F run()-split — a **separate, larger** refactor.
**Branch (when planned):** `refactor/mode-transition-fsm`

## Problem

PiFire's controller is *half* a state machine. The **states** are already proper polymorphic
objects — `ControlMode` (`controller/runtime/modes/base.py`) is a clean template-method base and
each mode (Startup/Smoke/Hold/Monitor/Manual/Prime/Reignite/Shutdown) is a subclass overriding
typed hooks. That half is good and Phase F (the `run()` per-tick pipeline split) is the right
refactor for the *inside* of a state.

The **transitions** are the mess. Moving between states is done three different ways, in three
layers, with no single place that describes the edge set:

1. **Outer dispatch (`Controller.tick()`, controller.py):** a ~270-line `if control["mode"] ==
   "Startup": … elif "Smoke": …` ladder that runs each mode's work cycle and then selects the
   next mode — sometimes via `next_mode()`, sometimes via direct writes (Startup→Prime,
   boot→Monitor, switch-off→Stop, error→Error).
2. **`next_mode(next_mode, setpoint)` helper:** the *arbitrated* transition — it only applies
   `if not control["updated"]`, so a user/other request already in `control` preempts the mode's
   natural next transition. Setpoint is Hold-conditional. Called 7×.
3. **Inline mode writes (smoke.py, hold.py, base.py skeleton):** work cycles that hit a safety
   verdict write `control["mode"] = "Error"|"Reignite"` directly + `control["updated"]=True` +
   `write_control(OVERWRITE)` + `notifications.send(...)` + display push + (for Reignite)
   `reigniteretries -= 1` / `reignitelaststate = self.name`. This exact block is duplicated
   between smoke.py and hold.py (4 sites).

Plus a nested **Recipe sub-machine** (`Controller.recipe_mode()`) that walks recipe steps,
sets modes per step, and has its own cancel / reignite-retry / normal-end transition logic,
reading `control["mode"]`/`control["updated"]` to arbitrate.

The result: ~21 `control["mode"] = …` write sites across 4 files; the legal edge set, its
guards, and its side effects are implicit and duplicated. Adding or auditing a transition means
reading four files and reverse-engineering the `updated` arbitration.

## Goal

Formalize the transition layer into an **explicit, inspectable state machine** without changing
runtime behavior:

- A single **transition seam** — `request_transition(control, to_mode, *, reason, setpoint=…)` —
  that every mode change routes through, reproducing today's arbitration (`updated` gate),
  setpoint rule, `next_mode` bookkeeping, control-write kind, and notification/side-effect
  contract exactly. The inline smoke/hold duplication collapses into calls to this seam (+ the
  per-edge notification/retry data supplied by the caller or the edge table).
- An explicit **dispatch table** `{mode: handler}` replacing the `tick()` if/elif ladder
  (mirrors the Phase D god-route → dispatch-map pattern already used successfully in this repo).
- An explicit **edge/guard description**: each state declares (or a central table lists) its
  legal exits and the guard that fires each (flameout verdict, safety temp, switch-off, recipe
  end, boot-to-monitor, user request). Guards are small predicates; the FSM evaluates them —
  the mode work cycle no longer hand-writes the destination.

**Non-goals:** No new modes, no changed transition semantics, no datastore-contract change
(`control["mode"]`/`status`/`updated`/`next_mode` remain the persisted fields other processes
read). This is an internal formalization, behavior byte-for-byte preserved.

## Why this is separate from (and after) Phase F

Phase F decomposes the *inside* of one state (the per-tick SENSE→SAFETY→ACT→PUBLISH pipeline —
a linear pipeline, correctly modeled as extracted helpers, NOT a state graph). This design
decomposes the *between-states* transition graph. Different code, different model, different
risk. Phase F should land first (it's in flight); this builds on the cleaner mode bodies it
leaves behind.

## Proposed architecture

```
controller/runtime/transitions.py   (NEW)
  - TransitionRequest / request_transition(control, to, *, reason, setpoint, notify, ...)
      the single arbitrated write seam; preserves the `updated` gate + setpoint rule +
      WriteKind + next_mode bookkeeping + optional notification/reignite side effects.
  - TRANSITIONS: the explicit edge table (from_mode -> [(guard, to_mode, side-effects)]),
      OR per-mode `allowed_exits()` declarations — chosen after the inventory review.
  - guard predicates (flameout, safety_temp, switch_off, recipe_end, boot_to_monitor, ...)
      pure functions of (control, settings, ptemp/verdict).

controller/runtime/controller.py    (MODIFIED)
  - tick() if/elif ladder -> {mode: handler} dispatch map over the transition seam.
  - next_mode() folded into request_transition (kept as a thin compatibility shim if needed).

controller/runtime/modes/smoke.py, hold.py, base.py   (MODIFIED)
  - the duplicated inline `mode=Error/Reignite + updated + write + notify` blocks -> one
    request_transition(...) call each, with the edge's notification/retry data.
```

The datastore write pattern, the `updated` arbitration, and every side effect stay identical —
only the *place* they are expressed changes (one seam + one table instead of 21 scattered sites).

## The hard parts (from live-code investigation)

1. **The `updated` arbitration is load-bearing.** `next_mode()` transitions only
   `if not control["updated"]`. A naive `from→to` table would drop the "user request preempts
   mode-natural transition" semantics. `request_transition` MUST model priority: a
   already-`updated` control is not overwritten by a mode-natural edge.
2. **Real shutdown on one edge.** `controller.py:477` runs
   `os.system("sleep 3 && sudo shutdown -h now &")` on `Shutdown→Stop` when
   `settings["shutdown"]["auto_power_off"]` is set. Every characterization test that touches the
   Shutdown path MUST patch `os.system` (it is deliberately module-level for this — see the
   controller.py:18 comment) AND/OR fixture `auto_power_off=False`. This is the #1 safety gate —
   a careless test really powers off the host. (Repo history: 2 real reboots from unmocked paths.)
3. **The Recipe sub-machine** has its own transition logic (step→step-mode, reignite-retry keeps
   the step, cancel→break, normal-end→Stop) that arbitrates on `updated`/`mode`. It must be
   modeled as a nested region, not flattened into the top-level table.
4. **`status` vs `mode`.** Some edges also set `control["status"]` (e.g. Monitor sets
   `status="monitor"`) or `status`-derived metrics. The seam must carry these, not just `mode`.
5. **smoke.py ↔ hold.py duplication is *near*-identical, not identical.** The plan must diff the
   4 inline blocks and confirm the unified seam reproduces each exactly (this is the same class
   of trap the Phase G investigation caught).
6. **Pseudo-states without a class.** `Stop`, `Error`, `Recipe` are `control["mode"]` values
   handled only in the outer loop, not `ControlMode` subclasses. The FSM must model them.
7. **`next_mode` field vs `next_mode()` method.** Modes stash a *pending* destination in
   `control["next_mode"]` (Startup reads `after_startup_mode`, Reignite reads `reignitelaststate`,
   Shutdown sets `Stop`) which the method then applies. Two related mechanisms with the same name —
   the seam must keep both concepts.

## Gate (characterization-first — mandatory)

The per-tick pipeline has strong golden coverage (49 behavioral tests via `test_modes_golden.py`
+ `test_work_cycle_e2e.py`), but the **transition graph** is under-covered. So this refactor
**begins** by adding a transition-level characterization suite that drives each edge and asserts
the resulting `control["mode"]/status/updated/next_mode/setpoint` + side effects (notification
sent, retries decremented, display push) — committed green against current code BEFORE any
formalization. Then the seam + table + dispatch map are introduced under it, byte-for-byte.
Coverage gaps (edges with no existing test) come from the inventory (see the plan). Every
Shutdown-path test patches `os.system`.

## Risk / rollback

Medium-high (core control flow, safety-adjacent). Mitigations: characterization-first, one edge
family per commit, the existing golden as a second net, `os.system` patched everywhere. Rollback:
revert the branch — the seam is additive until the call sites are repointed, so partial revert is
clean.

## Open question for review

Two viable shapes for the edge description, to decide after reading the full inventory:
- **(a) Central `TRANSITIONS` table** — one dict listing every `(from, guard) → (to, effects)`.
  Most inspectable; risks divorcing the edge from the mode that owns it.
- **(b) Per-mode `allowed_exits()`** — each `ControlMode` declares its own exits/guards.
  Keeps edge next to state; the "whole graph" is assembled by asking every mode.
Recommendation leans (b) for cohesion with the existing template-method design, with a
generated/asserted global view for inspectability. Final call in the plan.
