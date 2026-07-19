# `control["status"]` as a Second State Dimension — Design

**Date:** 2026-07-18
**Status:** Draft design (for review). Successor to the mode-transition FSM; formalizes the *second* state axis.
**Branch (when planned):** `refactor/status-dimension`

## Problem

The controller has a second state variable, `control["status"]`, that is orthogonal to
`control["mode"]` and managed the same way `mode` was before the FSM: scattered ad-hoc writes with
an implicit, undocumented interaction between the two axes. It holds one of four string values:

| value | meaning |
|---|---|
| `""` | uninitialized default (never operated) — `default_control()`, `common/defaults.py:461` |
| `"active"` | normally operating (a cook is running in any active mode) |
| `"monitor"` | in Monitor mode (watching the OEM controller, not actively controlling) |
| `"inactive"` | stopped / errored / idle |

**Write sites (6, all in the controller + defaults):**
- `common/defaults.py:461` — `""` (initial).
- `controller/runtime/controller.py:358` — `"active"` (per tick, when `status != "monitor" and mode != Error`).
- `controller/runtime/controller.py:519` — `"monitor"` (Monitor dispatch; persists so a monitor-mode error is distinguishable).
- `controller/runtime/controller.py:398` — `"inactive"` (Stop cleanup; now persists after the recent bugfix).
- `controller/runtime/controller.py:413` — `"inactive"` (Error cleanup).
- `controller/runtime/modes/base.py:315` — `"active"` (switch-off path, transiently, right before the Stop transition — overwritten by Stop cleanup; a redundant write the formalization should clarify).

**Read sites — TWO axes of consumption:**
1. **Controller decisions (2 sites, the load-bearing coupling)** — `controller/runtime/controller.py`:
   - `:357` — `if status != "monitor" and mode != Error:` → set `"active"` (gate).
   - `:385` — `if status == "monitor" and mode == Error:` → `power_on()` **else** `power_off()`. This is the crux: **a Monitor-mode error keeps the OEM controller powered on**, whereas a normal error powers off. The `status`×`mode` pair decides power.
2. **UI passthrough (read-only display)** — the frontend receives `control["status"]` verbatim and shows it: `blueprints/api/routes.py:71` (`status["status"] = control["status"]`) and `blueprints/mobile/socket_io.py:217` (`"status": control["status"]`). No frontend LOGIC branches on the specific value — it is displayed. **So the string values are a published contract** (web + mobile app read them).

## Assessment (honest right-sizing)

`status` is a *genuine* second dimension, but it is **small** — 4 values, 6 writes, 2 decision-reads, one
coupling rule. A full transition engine like the mode FSM (a `request_transition` seam + `GUARDS` +
`evaluate_phase`) would be **over-engineering** here — the same mistake as forcing an FSM onto the
per-tick pipeline. The right formalization is proportional:

1. **A `StatusState(StrEnum)`** — `ACTIVE="active"`, `MONITOR="monitor"`, `INACTIVE="inactive"`,
   `UNSET=""` — mirroring `Mode`. StrEnum so the **published string values stay byte-identical** for the
   web/mobile UI and the persisted `control["status"]`.
2. **A named, explicit coupling predicate** for the one real 2D interaction:
   `should_keep_power_on(mode, status) -> bool` (currently the inline `status=="monitor" and
   mode=="Error"`). This makes the mode×status interaction a single tested rule instead of a bare
   conditional buried in Stop/Error cleanup.
3. **An explicit status-transition map** documented in one place: what drives each status change
   (first-update → ACTIVE; Monitor dispatch → MONITOR; Stop/Error cleanup → INACTIVE; the persistence
   of MONITOR through an Error). Optionally a thin `set_status(control, state)` helper if it improves
   readability, but 6 direct writes with the enum may be clear enough — decide during planning.

This delivers the value of "a second modeled dimension" (explicit states, explicit transitions, the
coupling made a named rule) without a heavyweight engine the size doesn't justify.

## Proposed design

```
common/status.py  (NEW, or fold into common/modes.py as a sibling enum)
  class StatusState(StrEnum): ACTIVE / MONITOR / INACTIVE / UNSET

controller/runtime/transitions.py  (or a small status.py)
  def should_keep_power_on(mode, status) -> bool:
      # the ONE mode×status coupling: a Monitor-mode error keeps power on.
      return status == StatusState.MONITOR and mode == Mode.ERROR
  # (optional) def set_status(control, state): control["status"] = state

controller/runtime/controller.py + modes/base.py  (MODIFIED)
  - the 6 status writes use StatusState.*
  - the :385 power branch calls should_keep_power_on(self.control["mode"], self.control["status"])
  - the :357 active-gate reads StatusState.MONITOR / Mode.ERROR
```

The **state/transition table** (documented in the plan, asserted by a snapshot test):

| from | to | trigger |
|---|---|---|
| UNSET / any (not MONITOR, mode≠Error) | ACTIVE | an update lands while operating |
| any | MONITOR | Monitor mode dispatched |
| ACTIVE / MONITOR | INACTIVE | Stop or Error cleanup |
| MONITOR | MONITOR (persists) | through an Error — enables the keep-power-on rule |

## The coupling, made explicit (the whole point)

Today the mode×status interaction is one inline conditional at controller.py:385. The design surfaces
it as `should_keep_power_on(mode, status)` — a pure, unit-tested predicate. This is the "second
dimension" formalized: power is a function of BOTH axes, stated once.

## Gate (characterization-first)

Behavior-preserving. The published string values MUST stay identical (StrEnum guarantees it), and the
power decision must be unchanged. Begin by pinning: the status value after each transition
(startup→active, monitor dispatch→monitor, stop→inactive, error→inactive, **monitor+error→power stays
on**), plus the API/socket passthrough (`api/routes` and `socket_io` still emit the same strings). The
existing `test_controller_loop_golden.py` already exercises the Stop/Error/Monitor cleanup and the
power on/off calls — extend it (or a new `test_status_dimension.py`) to pin the monitor+error keep-power
path explicitly, which is currently the weakest-covered edge, BEFORE refactoring.

## Risks / notes

- **Published contract:** web + mobile display `control["status"]` verbatim; the four string values are
  external API. StrEnum keeps them byte-identical — do NOT change the strings.
- **The `""` (UNSET) default** is a real persisted value (a never-operated grill). Keep it; a
  `StatusState.UNSET=""` member models it honestly. (Note the recent Stop bugfix already made Stop
  persist `"inactive"` instead of `""`, so `""` now means only "never operated".)
- **The base.py:315 transient `"active"` write** on the switch-off path is redundant (Stop cleanup
  overwrites it). The design can drop it or keep it; either way document that status settles to INACTIVE
  after a switch-off→Stop. Behavior-preserving either way (the final persisted value is unchanged).
- **Do NOT model this as a full FSM engine.** The coupling is one rule; the transitions are four. An
  enum + a predicate + a documented table is the correct weight.

## Open question for review

Two shapes for #3 (the transition sites):
- **(a) Direct enum writes** — `control["status"] = StatusState.ACTIVE` at each of the ~5 live sites,
  plus the `should_keep_power_on` predicate and a snapshot-tested transition table. Smallest change.
- **(b) A `set_status(control, state)` seam** — every status write routes through one helper (mirrors
  `request_transition`), giving a single place to add logging/validation later.
Recommendation leans **(a)** — 5 writes don't justify a seam, and the real win is the enum + the named
coupling predicate. Revisit if status grows more states/consumers later.

## Scope
`common/` (new enum) + `controller/runtime/` (writes + the predicate). The web/mobile passthrough needs
NO change (it forwards the string). This closes the FSM-family follow-ons; the only remaining Tier-1&2
item after this is Phase E (Meater, parked).
