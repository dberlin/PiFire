# Backend Docs Import Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close `docs/superpowers/backlogs/backend-backlog.md` item 7 by moving reusable experiment implementations into a real tooling package while preserving reproducible documentation-local entry points and enforcing that nothing outside `docs/` imports from `docs/`.

**Architecture:** `tools/experiments/` becomes the importable home for the five experiment modules that still have consumers after the acados cutover. The existing files under `docs/superpowers/experiments/` become thin, stable regeneration launchers so commands embedded in committed evidence remain executable; they contain no reusable implementation. Tests and other experiment scripts import `tools.experiments`, and an AST boundary test prevents any future non-docs source from importing `docs` or adding `sys.path` mutation to the tooling package.

**Tech Stack:** Python 3.14, pytest/pytest-xdist, Ruff, Pyright/LSP file rename and reference updates, Jujutsu.

## Global Constraints

- End state: **nothing outside `docs/` imports from `docs/`**.
- Preserve the current behavior, function names, constants, CLI arguments, exit codes, evidence validation, scenario timing, and numerical calculations of all five modules.
- Preserve committed evidence bytes. In particular, do not rewrite `_matrix_baseline.json`, `_braking_horizon.json`, `_residual_mpc_compare.json`, or `_promotion_signal.txt` merely because implementation files move.
- Keep the historical regeneration commands embedded in those artifacts executable through thin launchers at their existing `docs/superpowers/experiments/*.py` paths.
- `tools/experiments/` must be an ordinary explicit package and must not mutate `sys.path`.
- Production packages must not import `tools.experiments`; it remains development/experiment tooling.
- Remove `sys.path.insert(...)` from affected tests when it exists only to make a `docs` import resolve. Preserve `sys` imports used for `monkeypatch.setitem(sys.modules, ...)`.
- Use LSP references and `rename_file` for every implementation move. Do not perform cross-file import renames with text replacement.
- Use Jujutsu commands, always with `--no-pager`; do not use raw Git commands.
- Do not run the expensive multi-cook or mutation experiments as migration smoke tests. Their focused contract tests and import/CLI checks are the acceptance evidence.

## Current State and Exact Scope

The backlog entry predates the acados cleanup. The live tree now has **10 non-docs import sites across 10 test files**, depending on **five** modules:

| New implementation module | Current non-docs consumers |
|---|---|
| `tools.experiments.controller_matrix` | `tests/e2e/test_mpc_learns_a_grill.py`; `tests/unit/controller/test_matrix_harness_{auger_toggle,configuration,lid_excursion,lid_sequence,sim_clock}.py` |
| `tools.experiments.braking_horizon` | `tests/unit/mpc/test_braking_horizon.py` |
| `tools.experiments.promotion_signal` | `tests/unit/mpc/test_mpc_refit.py` |
| `tools.experiments.residual_mpc_compare` | `tests/unit/controller/test_residual_mpc_compare.py` |
| `tools.experiments.mutation_score` | `tests/unit/mpc/test_mutation_score.py` |

`controller_matrix.run_scenario` also has live docs-local consumers in `control_rethink.py`, `cook_chain.py`, `mpc_default_mass.py`, `mpc_midcook_adopt.py`, `mpc_online_window.py`, `residual_mpc_compare.py`, and `structure_compare.py`. `promotion_signal` has one docs-local consumer, `offset_nuisance.py`. These callers must switch to `tools.experiments` so only the thin launcher imports the moved implementation.

---

### Task 1: Establish the Tooling Package and Move the Controller Matrix

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/experiments/__init__.py`
- Move implementation: `docs/superpowers/experiments/controller_matrix.py` → `tools/experiments/controller_matrix.py`
- Recreate launcher: `docs/superpowers/experiments/controller_matrix.py`
- Modify: `tests/e2e/test_mpc_learns_a_grill.py`
- Modify: `tests/unit/controller/test_matrix_harness_auger_toggle.py`
- Modify: `tests/unit/controller/test_matrix_harness_configuration.py`
- Modify: `tests/unit/controller/test_matrix_harness_lid_excursion.py`
- Modify: `tests/unit/controller/test_matrix_harness_lid_sequence.py`
- Modify: `tests/unit/controller/test_matrix_harness_sim_clock.py`
- Modify: `docs/superpowers/experiments/control_rethink.py`
- Modify: `docs/superpowers/experiments/cook_chain.py`
- Modify: `docs/superpowers/experiments/mpc_default_mass.py`
- Modify: `docs/superpowers/experiments/mpc_midcook_adopt.py`
- Modify: `docs/superpowers/experiments/mpc_online_window.py`
- Modify: `docs/superpowers/experiments/residual_mpc_compare.py`
- Modify: `docs/superpowers/experiments/structure_compare.py`

**Interfaces:**
- Consumes: `controller.grill_sim`, `controller.runtime.logic.pulse`, `controller.runtime.runner`, `grillplat.actuator_capabilities`.
- Produces: unchanged `Scenario`, `SCENARIOS`, `ReachabilityState`, `run_scenario(...)`, `rank_reachable_rows(...)`, `_effective_configuration(...)`, `_recovery_s(...)`, `_refit_after_cook(...)`, and `main(argv=None)` from `tools.experiments.controller_matrix`.
- Preserves: direct command `uv run --no-sync python docs/superpowers/experiments/controller_matrix.py ...` and its existing artifact-local output path.

- [ ] **Step 1: Create explicit package markers**

Create `tools/__init__.py`:

```python
"""Repository maintenance and experiment tooling."""
```

Create `tools/experiments/__init__.py`:

```python
"""Importable experiment harnesses kept out of the documentation tree."""
```

- [ ] **Step 2: Move the implementation with symbol-aware tooling**

Use LSP `rename_file` from `docs/superpowers/experiments/controller_matrix.py` to `tools/experiments/controller_matrix.py`. Inspect the applied reference edits against the 30 references reported for `run_scenario`; every import should now resolve through `tools.experiments.controller_matrix`.

In the moved implementation:

1. Delete the repository-root `sys.path.insert(...)` bootstrap.
2. Delete `sys` only if LSP references show no remaining use.
3. Keep `OUT = "./docs/superpowers/experiments/_matrix_baseline.json"` unchanged.
4. Keep the `regeneration_command` value pointing at the docs launcher so old and newly generated evidence use the stable public command.
5. Do not change scenario definitions, pulse timing, controller cadence, plant selection, scoring, multiprocessing, or JSON structure.

- [ ] **Step 3: Recreate the stable documentation launcher**

Create `docs/superpowers/experiments/controller_matrix.py` with only:

```python
#!/usr/bin/env python3
"""Stable regeneration entry point for the controller matrix evidence."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.experiments.controller_matrix import main  # noqa: E402

if __name__ == "__main__":
    main()
```

The launcher may bootstrap the repository because it is executed from `docs`; no file outside `docs` may import it.

- [ ] **Step 4: Update all importers and remove obsolete test path bootstraps**

Use these imports:

```python
import tools.experiments.controller_matrix as controller_matrix
```

or:

```python
from tools.experiments.controller_matrix import SCENARIOS, run_scenario
```

Apply them to all files named in this task. Remove the four obsolete path insertions from:

- `tests/e2e/test_mpc_learns_a_grill.py` — remove both `os` and `sys` if now unused.
- `test_matrix_harness_auger_toggle.py` — remove `os`, retain `sys` for `sys.modules`.
- `test_matrix_harness_lid_sequence.py` — remove `os`, retain `sys` for `sys.modules`.
- `test_matrix_harness_lid_excursion.py` — remove both `os` and `sys` if now unused.

In `structure_compare.py`, hash the moved implementation rather than the launcher:

```python
from pathlib import Path
import tools.experiments.controller_matrix as controller_matrix
from tools.experiments.controller_matrix import SCENARIOS, run_scenario

harness = Path(controller_matrix.__file__).resolve()
harness_sha = hashlib.sha256(harness.read_bytes()).hexdigest()
```

Do not keep the old `os.path.join(..., "controller_matrix.py")` hash; that would attest only to the thin launcher.

- [ ] **Step 5: Run the matrix contracts and CLI smoke**

Run:

```bash
uv run pytest -q \
  tests/unit/controller/test_matrix_harness_sim_clock.py \
  tests/unit/controller/test_matrix_harness_lid_sequence.py \
  tests/unit/controller/test_matrix_harness_auger_toggle.py \
  tests/unit/controller/test_matrix_harness_configuration.py \
  tests/unit/controller/test_matrix_harness_lid_excursion.py
uv run pytest -q --collect-only tests/e2e/test_mpc_learns_a_grill.py
uv run python -m tools.experiments.controller_matrix --help
uv run python docs/superpowers/experiments/controller_matrix.py --help
```

Expected: all focused tests pass; the slow e2e module collects without an import error; both commands show the same argument parser and exit 0.

- [ ] **Step 6: Format, check, and commit**

Run:

```bash
uv run ruff format tools/experiments/controller_matrix.py docs/superpowers/experiments/controller_matrix.py \
  tests/e2e/test_mpc_learns_a_grill.py tests/unit/controller/test_matrix_harness_*.py \
  docs/superpowers/experiments/{control_rethink,cook_chain,mpc_default_mass,mpc_midcook_adopt,mpc_online_window,residual_mpc_compare,structure_compare}.py
uv run ruff check tools docs/superpowers/experiments tests/e2e/test_mpc_learns_a_grill.py tests/unit/controller
jj commit -m "refactor(experiments): move controller matrix out of docs" \
  tools/__init__.py tools/experiments/__init__.py tools/experiments/controller_matrix.py \
  docs/superpowers/experiments/controller_matrix.py \
  docs/superpowers/experiments/control_rethink.py \
  docs/superpowers/experiments/cook_chain.py \
  docs/superpowers/experiments/mpc_default_mass.py \
  docs/superpowers/experiments/mpc_midcook_adopt.py \
  docs/superpowers/experiments/mpc_online_window.py \
  docs/superpowers/experiments/residual_mpc_compare.py \
  docs/superpowers/experiments/structure_compare.py \
  tests/e2e/test_mpc_learns_a_grill.py \
  tests/unit/controller/test_matrix_harness_auger_toggle.py \
  tests/unit/controller/test_matrix_harness_configuration.py \
  tests/unit/controller/test_matrix_harness_lid_excursion.py \
  tests/unit/controller/test_matrix_harness_lid_sequence.py \
  tests/unit/controller/test_matrix_harness_sim_clock.py
```

---

### Task 2: Move Braking-Horizon Evidence Logic

**Files:**
- Move implementation: `docs/superpowers/experiments/braking_horizon.py` → `tools/experiments/braking_horizon.py`
- Recreate launcher: `docs/superpowers/experiments/braking_horizon.py`
- Modify: `tests/unit/mpc/test_braking_horizon.py`
- Preserve unchanged: `docs/superpowers/experiments/_braking_horizon.json`

**Interfaces:**
- Produces unchanged `OUTPUT`, `REGENERATION_COMMAND`, `COAST_SECONDS`, `T_FLOOR_C`, `T_HAZARD_C`, `braking_distance`, `_validate(payload)`, `measure()`, and `main()`.
- Preserves: `python -m docs.superpowers.experiments.braking_horizon` because that exact value is validated inside committed evidence.

- [ ] **Step 1: Move the implementation and update repository-relative paths**

Use LSP `rename_file`, then make the moved module use:

```python
REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "docs" / "superpowers" / "experiments" / "_braking_horizon.json"
REGENERATION_COMMAND = "python -m docs.superpowers.experiments.braking_horizon"
```

Delete its `sys.path` mutation and the now-unused `sys` import. Do not change `_validate`, `measure`, solver/scheduler inputs, or evidence fields.

- [ ] **Step 2: Recreate the stable module launcher**

Create `docs/superpowers/experiments/braking_horizon.py`:

```python
#!/usr/bin/env python3
"""Stable regeneration entry point for braking-horizon evidence."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.experiments.braking_horizon import main  # noqa: E402

if __name__ == "__main__":
    main()
```

This preserves the exact regeneration command already stored in `_braking_horizon.json`.

- [ ] **Step 3: Update and run the validator tests**

Change the test import to:

```python
from tools.experiments import braking_horizon
```

Run:

```bash
uv run pytest -q tests/unit/mpc/test_braking_horizon.py
uv run python -c "from tools.experiments import braking_horizon; braking_horizon._validate(__import__('json').loads(braking_horizon.OUTPUT.read_text()))"
```

Expected: the complete validator suite and committed artifact validation pass without rewriting the artifact.

- [ ] **Step 4: Format, check, and commit**

```bash
uv run ruff format tools/experiments/braking_horizon.py docs/superpowers/experiments/braking_horizon.py tests/unit/mpc/test_braking_horizon.py
uv run ruff check tools/experiments/braking_horizon.py docs/superpowers/experiments/braking_horizon.py tests/unit/mpc/test_braking_horizon.py
jj commit -m "refactor(experiments): move braking evidence logic out of docs" \
  tools/experiments/braking_horizon.py docs/superpowers/experiments/braking_horizon.py tests/unit/mpc/test_braking_horizon.py
```

---

### Task 3: Move Promotion-Signal Data Generation

**Files:**
- Move implementation: `docs/superpowers/experiments/promotion_signal.py` → `tools/experiments/promotion_signal.py`
- Recreate launcher: `docs/superpowers/experiments/promotion_signal.py`
- Modify: `tests/unit/mpc/test_mpc_refit.py`
- Modify: `docs/superpowers/experiments/offset_nuisance.py`
- Modify comment only: `controller/model_promotion.py:89-91`
- Preserve unchanged: `docs/superpowers/experiments/_promotion_signal.txt`

**Interfaces:**
- Produces unchanged `PROBE_Q`, `plant_record(...)`, `validation_runs(...)`, `flat_synthetic(...)`, `real_cook()`, `profiles()`, `truncations(...)`, fitting/scoring helpers, and `main()`.
- Consumes the real-cook fixture at `tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv`.

- [ ] **Step 1: Move the implementation and correct its root calculation**

Use LSP `rename_file`. Replace the four-level docs root bootstrap with a three-level repository path and remove `sys.path` mutation:

```python
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

Keep the real-cook path as `os.path.join(REPO, "tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv")`. Do not alter profiles, random seeds, cache identity, sample cadence, refit thresholds, metrics, or printed report structure.

- [ ] **Step 2: Recreate the docs launcher and migrate consumers**

Create `docs/superpowers/experiments/promotion_signal.py`:

```python
#!/usr/bin/env python3
"""Stable regeneration entry point for promotion-signal evidence."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.experiments.promotion_signal import main  # noqa: E402

if __name__ == "__main__":
    main()
```

Update imports:

```python
from tools.experiments import promotion_signal
```

in `test_mpc_refit.py`, and:

```python
from tools.experiments import promotion_signal as ps
```

in `offset_nuisance.py`.

Update `controller/model_promotion.py`'s measurement citation to name `tools/experiments/promotion_signal.py`, while retaining that `_promotion_signal.txt` is committed under `docs/superpowers/experiments/`.

- [ ] **Step 3: Run focused refit and import contracts**

```bash
uv run pytest -q tests/unit/mpc/test_mpc_refit.py
uv run python -c "from tools.experiments import promotion_signal as p; assert p.plant_record('mak', 'ramp_coast')['Q'].min() >= 0; assert p.real_cook()['Q'].max() <= 1"
```

Expected: refit tests pass and both representative data sources retain normalized firing-rate inputs.

- [ ] **Step 4: Format, check, and commit**

```bash
uv run ruff format tools/experiments/promotion_signal.py docs/superpowers/experiments/promotion_signal.py \
  docs/superpowers/experiments/offset_nuisance.py tests/unit/mpc/test_mpc_refit.py controller/model_promotion.py
uv run ruff check tools/experiments/promotion_signal.py docs/superpowers/experiments/promotion_signal.py \
  docs/superpowers/experiments/offset_nuisance.py tests/unit/mpc/test_mpc_refit.py controller/model_promotion.py
jj commit -m "refactor(experiments): move promotion signal out of docs" \
  tools/experiments/promotion_signal.py docs/superpowers/experiments/promotion_signal.py \
  docs/superpowers/experiments/offset_nuisance.py tests/unit/mpc/test_mpc_refit.py controller/model_promotion.py
```

---

### Task 4: Move Residual Comparison and Preserve Its Artifact Command

**Files:**
- Move implementation: `docs/superpowers/experiments/residual_mpc_compare.py` → `tools/experiments/residual_mpc_compare.py`
- Recreate launcher: `docs/superpowers/experiments/residual_mpc_compare.py`
- Modify: `tests/unit/controller/test_residual_mpc_compare.py`
- Preserve unchanged: `docs/superpowers/experiments/_residual_mpc_compare.json`

**Interfaces:**
- Produces unchanged `ARMS`, `PLANTS`, `SEEDS`, `COOKS`, `METRICS`, `_summary(rows)`, and `main(argv=None)`.
- Consumes `tools.experiments.controller_matrix.SCENARIOS` and `run_scenario` from Task 1.

- [ ] **Step 1: Move the implementation and remove path bootstrapping**

Use LSP `rename_file`. In the moved implementation:

```python
from tools.experiments.controller_matrix import SCENARIOS, run_scenario
```

Remove the old repository `sys.path.insert(...)` and unused path imports. Keep the default output under `docs/superpowers/experiments/_residual_mpc_compare.json`, and keep the artifact header's command pointing to the docs launcher.

- [ ] **Step 2: Recreate the stable launcher and update the test import**

Create `docs/superpowers/experiments/residual_mpc_compare.py`:

```python
#!/usr/bin/env python3
"""Stable regeneration entry point for residual-MPC comparison evidence."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.experiments.residual_mpc_compare import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
```

The test imports:

```python
from tools.experiments import residual_mpc_compare as experiment
```

- [ ] **Step 3: Run summary and CLI contracts**

```bash
uv run pytest -q tests/unit/controller/test_residual_mpc_compare.py
uv run python -m tools.experiments.residual_mpc_compare --help
uv run python docs/superpowers/experiments/residual_mpc_compare.py --help
```

Expected: summary gate tests pass and both CLIs expose the same `--out` and `--workers` arguments without starting the cook matrix.

- [ ] **Step 4: Format, check, and commit**

```bash
uv run ruff format tools/experiments/residual_mpc_compare.py docs/superpowers/experiments/residual_mpc_compare.py tests/unit/controller/test_residual_mpc_compare.py
uv run ruff check tools/experiments/residual_mpc_compare.py docs/superpowers/experiments/residual_mpc_compare.py tests/unit/controller/test_residual_mpc_compare.py
jj commit -m "refactor(experiments): move residual comparison out of docs" \
  tools/experiments/residual_mpc_compare.py docs/superpowers/experiments/residual_mpc_compare.py tests/unit/controller/test_residual_mpc_compare.py
```

---

### Task 5: Move the Mutation-Score Driver

**Files:**
- Move implementation: `docs/superpowers/experiments/mutation_score.py` → `tools/experiments/mutation_score.py`
- Recreate launcher: `docs/superpowers/experiments/mutation_score.py`
- Modify: `tests/unit/mpc/test_mutation_score.py`

**Interfaces:**
- Produces unchanged `NODES`, `MUTATIONS`, `_score()`, and `main()`.
- Preserves `python -m docs.superpowers.experiments.mutation_score` as a stable operator command.

- [ ] **Step 1: Move the implementation and repair repository paths**

Use LSP `rename_file`. The implementation's new root is:

```python
ROOT = pathlib.Path(__file__).resolve().parents[2]
```

Do not change mutation anchors, replacement text, test nodes, restoration ordering, subprocess environment, or compile checks.

- [ ] **Step 2: Recreate the launcher and update the test**

Create `docs/superpowers/experiments/mutation_score.py`:

```python
#!/usr/bin/env python3
"""Stable entry point for the MPC mutation-score driver."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.experiments.mutation_score import main  # noqa: E402

if __name__ == "__main__":
    main()
```

Update the test import to:

```python
from tools.experiments import mutation_score
```

- [ ] **Step 3: Run the anchor contract without executing mutations**

```bash
uv run pytest -q tests/unit/mpc/test_mutation_score.py
uv run python -c "from tools.experiments import mutation_score as m; assert m.MUTATIONS; assert all(path.is_absolute() for _, path, _, _ in m.MUTATIONS)"
```

Expected: every mutation anchor occurs exactly once and all resolved source paths remain rooted in this checkout. Do not invoke `main()` in this migration task.

- [ ] **Step 4: Format, check, and commit**

```bash
uv run ruff format tools/experiments/mutation_score.py docs/superpowers/experiments/mutation_score.py tests/unit/mpc/test_mutation_score.py
uv run ruff check tools/experiments/mutation_score.py docs/superpowers/experiments/mutation_score.py tests/unit/mpc/test_mutation_score.py
jj commit -m "refactor(experiments): move mutation driver out of docs" \
  tools/experiments/mutation_score.py docs/superpowers/experiments/mutation_score.py tests/unit/mpc/test_mutation_score.py
```

---

### Task 6: Enforce the Boundary and Close Backlog Item 7

**Files:**
- Create: `tests/unit/test_docs_import_boundary.py`
- Modify: `docs/superpowers/backlogs/backend-backlog.md:51-54,308-359`

**Interfaces:**
- Produces: a repository architecture contract that reports exact `path:line:module` violations.
- Enforces: no Python source outside `docs` imports a module rooted at `docs`; no module under `tools/experiments` mutates `sys.path`.

- [ ] **Step 1: Add the AST boundary test**

Create `tests/unit/test_docs_import_boundary.py`:

```python
"""Import boundaries for executable experiment code."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORED_PARTS = frozenset(
    {
        ".claude",
        ".git",
        ".jj",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".worktrees",
        "__pycache__",
        "build",
        "dist",
        "docs",
        "htmlcov",
        "node_modules",
        "web-react",
    }
)


def _sources() -> list[Path]:
    return sorted(
        path for path in ROOT.rglob("*.py") if not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
    )


def _docs_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.split(".", 1)[0] == "docs":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] == "docs":
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}")
    return violations


def _mutates_sys_path(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        target = node.func.value
        if (
            node.func.attr in {"append", "extend", "insert"}
            and isinstance(target, ast.Attribute)
            and target.attr == "path"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
        ):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:sys.path.{node.func.attr}")
    return violations


def test_non_docs_python_never_imports_docs() -> None:
    violations = [violation for path in _sources() for violation in _docs_imports(path)]
    assert violations == []


def test_importable_experiment_package_never_mutates_sys_path() -> None:
    violations = [
        violation
        for path in sorted((ROOT / "tools" / "experiments").rglob("*.py"))
        for violation in _mutates_sys_path(path)
    ]
    assert violations == []
```

- [ ] **Step 2: Prove the guard can fail, then restore the clean tree**

Create a temporary `tests/_docs_import_boundary_probe.py` containing:

```python
from docs.superpowers.experiments import controller_matrix
```

Run:

```bash
uv run pytest -q tests/unit/test_docs_import_boundary.py::test_non_docs_python_never_imports_docs
```

Expected: FAIL naming `tests/_docs_import_boundary_probe.py:1:docs.superpowers.experiments`. Delete the temporary probe, rerun the same node, and expect PASS.

Create `tools/experiments/_sys_path_boundary_probe.py` containing:

```python
import sys

sys.path.insert(0, "probe")
```

Run `uv run pytest -q tests/unit/test_docs_import_boundary.py::test_importable_experiment_package_never_mutates_sys_path`. Expected: FAIL naming `tools/experiments/_sys_path_boundary_probe.py:3:sys.path.insert`. Delete the temporary probe, rerun the same node, and expect PASS.

- [ ] **Step 3: Close the backlog entry without erasing its history**

Update item 7 to `DONE` with the actual landing date. Preserve the original 34-file/11-module finding as historical text, then append the reconciled landing facts:

- the acados retirement reduced the live boundary to 10 test import sites and five modules before this work began;
- implementations now live in `tools/experiments`;
- docs paths are launchers only, retained to keep committed regeneration commands executable;
- the AST guard is the permanent invariant;
- production packages still do not import experiment tooling.

Add a dated reconciliation-log line naming the implementation commits. Do not add a completion date until the code has actually landed.

- [ ] **Step 4: Run the complete focused import-boundary suite**

```bash
uv run pytest -q \
  tests/unit/test_docs_import_boundary.py \
  tests/unit/controller/test_matrix_harness_sim_clock.py \
  tests/unit/controller/test_matrix_harness_lid_sequence.py \
  tests/unit/controller/test_matrix_harness_auger_toggle.py \
  tests/unit/controller/test_matrix_harness_configuration.py \
  tests/unit/controller/test_matrix_harness_lid_excursion.py \
  tests/unit/controller/test_residual_mpc_compare.py \
  tests/unit/mpc/test_braking_horizon.py \
  tests/unit/mpc/test_mpc_refit.py \
  tests/unit/mpc/test_mutation_score.py
uv run pytest -q --collect-only tests/e2e/test_mpc_learns_a_grill.py
uv run ruff check .
```

Expected: all focused contracts pass, the slow e2e contract collects, and Ruff reports no diagnostics.

- [ ] **Step 5: Commit the permanent boundary**

```bash
jj commit -m "test(experiments): forbid imports from docs" \
  tests/unit/test_docs_import_boundary.py docs/superpowers/backlogs/backend-backlog.md
```

---

### Task 7: Aggregate Verification and Review

**Files:**
- Modify only if a focused failure or review finding identifies a source defect in this migration.

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: repository-wide proof that import relocation did not change runtime or experiment contracts.

- [ ] **Step 1: Verify both public regeneration paths resolve**

```bash
uv run python -m tools.experiments.controller_matrix --help
uv run python docs/superpowers/experiments/controller_matrix.py --help
uv run python -m tools.experiments.residual_mpc_compare --help
uv run python docs/superpowers/experiments/residual_mpc_compare.py --help
uv run python -c "from tools.experiments import braking_horizon, mutation_score, promotion_signal; assert braking_horizon.OUTPUT.is_file(); assert mutation_score.MUTATIONS; assert promotion_signal.PROBE_Q.size"
```

Expected: all commands exit 0 without launching an expensive cook or mutation run.

- [ ] **Step 2: Run repository-wide backend gates**

```bash
uv run ruff format --check .
uv run ruff check .
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy uv run pytest -q
```

Expected: formatter and Ruff are clean; the complete default Python suite has zero failures.

- [ ] **Step 3: Inspect the final import inventory**

Run the AST boundary tests. Then use the Grep tool with pattern `^(from|import)\s+docs(\.|\s|$)` over `*.py;blueprints;common;controller;display;distance;file_mgmt;grillplat;notify;probes;scripts;tests;tools;updater.py;wizard.py` and expect no matches.

Expected:

- zero imports outside `docs/`;
- docs-local references exist only where an experiment script intentionally calls another experiment or where a thin launcher imports its `tools.experiments` implementation;
- no `sys.path` mutation under `tools/experiments`;
- no committed evidence artifact changed.

- [ ] **Step 4: Request code review and resolve findings**

Use `skill://requesting-code-review`. Give the reviewer the range from the parent before Task 1 through the final boundary commit, the invariant, and the immutable-evidence constraint. Resolve every Critical or Important finding, rerun its focused contract, then rerun Step 2.

- [ ] **Step 5: Confirm the Jujutsu working copy is clean**

```bash
jj --no-pager status
jj --no-pager diff --stat
```

Expected: the working copy has no changes. Do not move bookmarks, push, or publish unless explicitly requested.
