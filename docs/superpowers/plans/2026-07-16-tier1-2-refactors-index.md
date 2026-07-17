# Tier 1 & 2 Refactors — Plan Index

Master index for the nine-phase refactoring effort. Design: [`../specs/2026-07-16-tier1-2-refactors-design.md`](../specs/2026-07-16-tier1-2-refactors-design.md).

Each phase is one branch + one PR, gated by the existing golden/characterization suite plus new characterization tests where coverage is missing. Detailed task-level plans are written **just-in-time**, one per phase, because later phases' exact code depends on state that only exists after earlier phases merge (e.g. Phase D imports from modules Phase A creates).

## Phase status

| Phase | Title | Depends on | Branch | Detailed plan | Status |
|------|-------|-----------|--------|---------------|--------|
| A | Split `common/common.py` + in-file simplifications | — | `refactor/common-split` | [phaseA-common-split.md](2026-07-16-phaseA-common-split.md) | **✅ MERGED** into `massive-reworks-and-new-ui` (merge `ec7995f`; `common.py` 3,351→660, 7 modules, suite 879→1029) |
| D | Blueprints service layer + dispatch maps | A | `refactor/blueprints-service` | _tbw (A merged — ready to author)_ | pending |
| B | Merge legacy fixed-display bases | — (snapshot harness first) | `refactor/display-fixed-base` | [phaseB-display-fixed-base.md](2026-07-16-phaseB-display-fixed-base.md) | **plan ready** |
| C | Collapse driver clone matrix + encoder mixin | B | `refactor/display-driver-matrix` | _tbw after B merges_ | pending |
| E | Meater shared core + delete `bt_meater.py` | — | `refactor/meater-dedup` | _tbw_ | pending |
| F | Split `ControlMode.run()` | — | `refactor/controlmode-run-split` | _tbw_ | pending |
| G | grillplat adopt `SystemCommandsMixin` | — | `refactor/grillplat-mixin` | _tbw_ | pending |
| H | notifications event table | — | `refactor/notify-event-table` | _tbw_ | pending |
| I | `PIDControllerBase` | — | `refactor/pid-base` | _tbw_ | pending |

## Recommended landing order

1. **A first, merge fast** — its import rewrite touches the whole tree; long-lived divergence is the main risk.
2. Then D (needs A), and B→C for display; E/F/G/H/I in any order or in parallel.

## Why plans are generated per-phase, not all at once

Bite-sized TDD steps require real code, not pseudo-code. For phases that consume Phase A's new module layout (D), or Phase B's merged base (C), the exact import paths and extraction targets are not knowable until the predecessor lands. Writing them now would produce placeholder steps — a plan failure. Each phase's plan is authored (via the writing-plans skill) at the point its predecessors are merged, reading the then-current code with Serena.

## Per-phase gate (applies to every plan)

- Behavior-preserving; the only intentional behavior changes are Phase A's `is_not_blank` fix and Phase E's module deletion, both characterization-gated.
- Add a characterization snapshot **first** where the touched code lacks coverage (legacy fixed displays in B; per-action settings tests in D; `process_command` in A).
- Full suite green + `/verify` on the affected runtime surface before the PR is done.
