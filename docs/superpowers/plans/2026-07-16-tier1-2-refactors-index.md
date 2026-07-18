# Tier 1 & 2 Refactors — Plan Index

Master index for the nine-phase refactoring effort. Design: [`../specs/2026-07-16-tier1-2-refactors-design.md`](../specs/2026-07-16-tier1-2-refactors-design.md).

Each phase is one branch + one PR, gated by the existing golden/characterization suite plus new characterization tests where coverage is missing. Detailed task-level plans are written **just-in-time**, one per phase, because later phases' exact code depends on state that only exists after earlier phases merge (e.g. Phase D imports from modules Phase A creates).

## Phase status

| Phase | Title | Depends on | Branch | Detailed plan | Status |
|------|-------|-----------|--------|---------------|--------|
| A | Split `common/common.py` + in-file simplifications | — | `refactor/common-split` | [phaseA-common-split.md](2026-07-16-phaseA-common-split.md) | **✅ MERGED** into `massive-reworks-and-new-ui` (merge `ec7995f`; `common.py` 3,351→660, 7 modules, suite 879→1029) |
| D | Blueprints service layer + dispatch maps | A | `refactor/blueprints-service` | [phaseD-blueprints-service.md](2026-07-16-phaseD-blueprints-service.md) | **✅ MERGED + PUSHED** (merge `78769f9`; D1 5 service helpers + context_processor, D2 god-routes → dispatch maps: settings 665→21, admin 326→63, probeconfig 378→42, socketio _get 90→7 / _post 315→12; suite 1332→1400) |
| B | Merge legacy fixed-display bases | — (snapshot harness first) | `refactor/display-fixed-base` | [phaseB-display-fixed-base.md](2026-07-16-phaseB-display-fixed-base.md) | **✅ MERGED + PUSHED** (merge `2dd7d1b`; 3 bases → `base_fixed` + 3 shims, ~2,760 lines removed, suite 1029→1154; golden net never re-baselined) |
| C | Collapse driver clone matrix + encoder mixin | B | `refactor/display-driver-matrix` | [phaseC-display-driver-matrix.md](2026-07-16-phaseC-display-driver-matrix.md) | **✅ MERGED + PUSHED** (merge `08c8402`; 3 input/panel mixins, 16 drivers thinned, net −642 lines; file count + manifest kept, shims kept) |
| E | Meater shared core + delete `bt_meater.py` | — | `refactor/meater-dedup` | [phaseE-meater-dedup.md](2026-07-18-phaseE-meater-dedup.md) | **plan written** — ⚠️ 2 human sign-offs (extract math-only not `ReadProbes`; `bt_meater_alt` migration repoint) |
| F | Split `ControlMode.run()` | — | `refactor/controlmode-run-split` | [phaseF-controlmode-run-split.md](2026-07-18-phaseF-controlmode-run-split.md) | **plan written** — gate is 49 behavioral characterization tests (no `run()` SHA pin) |
| G | grillplat adopt `SystemCommandsMixin` | — | `refactor/grillplat-mixin` | [phaseG-grillplat-mixin.md](2026-07-18-phaseG-grillplat-mixin.md) | **plan written** — spec corrected: keep 3 Pi overrides (`check_throttled` real), prototype keeps 3 sim overrides |
| H | notifications event table | — | `refactor/notify-event-table` | [phaseH-notify-event-table.md](2026-07-18-phaseH-notify-event-table.md) | **plan written** — EVENTS = builder callables + exact-key `.get()` lookup; drops 2 dead events (`Grill_Error_00`/`Grill_Warning`) + fixes `exceded` typo |
| I | `PIDControllerBase` + dead controller-API removal | — | `refactor/pid-base` | [phaseI-pid-base.md](2026-07-18-phaseI-pid-base.md) | **plan written** — folds in full removal of the dead dispatch surface (`set_config`/`set_gains`/`get_k`/`function_list`/`supported_functions`); base = `_calculate_gains`+`set_target` defaults |

## Recommended landing order

1. **A first, merge fast** — its import rewrite touches the whole tree; long-lived divergence is the main risk.
2. Then D (needs A), and B→C for display; E/F/G/H/I in any order or in parallel.

## Why plans are generated per-phase, not all at once

Bite-sized TDD steps require real code, not pseudo-code. For phases that consume Phase A's new module layout (D), or Phase B's merged base (C), the exact import paths and extraction targets are not knowable until the predecessor lands. Writing them now would produce placeholder steps — a plan failure. Each phase's plan is authored (via the writing-plans skill) at the point its predecessors are merged, reading the then-current code with Serena.

## Per-phase gate (applies to every plan)

- Behavior-preserving; the only intentional behavior changes are Phase A's `is_not_blank` fix and Phase E's module deletion, both characterization-gated.
- Add a characterization snapshot **first** where the touched code lacks coverage (legacy fixed displays in B; per-action settings tests in D; `process_command` in A).
- Full suite green + `/verify` on the affected runtime surface before the PR is done.
