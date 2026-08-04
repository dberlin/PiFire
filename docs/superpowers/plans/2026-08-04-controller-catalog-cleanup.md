# Controller Catalog Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Use test-driven development and verification-before-completion. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PID, PID-SP, and MPC the only supported/selectable temperature controllers, migrate retired selections safely to PID, and remove the retired implementations and dependencies completely.

**Architecture:** `controller/controllers.json` remains the controller catalog authority. A schema-versioned settings migration rewrites the six known retired IDs before runtime construction. The backend manifest, generated React types, settings UI, and tests all expose the same three-controller set. Retired code and package dependencies are deleted rather than shimmed.

**Tech Stack:** Python 3.14, Pydantic, pytest, React 19, TypeScript, Rstest, Bun, uv, Ruff, Jujutsu.

**Approved design:** `docs/superpowers/specs/2026-08-04-controller-catalog-cleanup-design.md`

**Retained IDs:** `pid`, `pid_sp`, `mpc`

**Retired IDs:** `pid_clamping`, `pid_clamping_percent_pb`, `pid_ac`, `pid_parallel`, `fuzzy`, `ml`

---

## Task 1: Migrate every retired selection to standard PID

**Files:**

- Create: `tests/unit/common/test_settings_migration_retired_controllers.py`
- Modify: `common/settings_migration.py`
- Modify: `common/settings_schema.py`
- Test: `tests/unit/common/test_settings_schema.py`

### Steps

- [ ] **Step 1: Start a focused revision**

```bash
jj new -m "fix(settings): migrate retired controllers to pid"
```

- [ ] **Step 2: Write the failing migration unit tests**

Create `tests/unit/common/test_settings_migration_retired_controllers.py` with these contracts:

```python
import copy

import pytest

from common.defaults import default_settings
from common.settings_migration import _migrate_retired_controllers

RETIRED = (
    "pid_clamping",
    "pid_clamping_percent_pb",
    "pid_ac",
    "pid_parallel",
    "fuzzy",
    "ml",
)
RETAINED = ("pid", "pid_sp", "mpc")


def _settings(selected="pid"):
    settings = copy.deepcopy(default_settings())
    settings["controller"]["selected"] = selected
    settings["controller"]["config"].update({name: {"stale": 1} for name in RETIRED})
    return settings


@pytest.mark.parametrize("selected", RETIRED)
def test_retired_selection_moves_to_pid_and_preserves_pid_config(selected):
    settings = _settings(selected)
    expected_pid = copy.deepcopy(settings["controller"]["config"]["pid"])

    assert _migrate_retired_controllers(settings) is True

    assert settings["controller"]["selected"] == "pid"
    assert settings["controller"]["config"]["pid"] == expected_pid
    assert not (set(settings["controller"]["config"]) & set(RETIRED))


@pytest.mark.parametrize("selected", RETAINED)
def test_retained_selection_is_unchanged_while_stale_blocks_are_removed(selected):
    settings = _settings(selected)

    assert _migrate_retired_controllers(settings) is True

    assert settings["controller"]["selected"] == selected
    assert not (set(settings["controller"]["config"]) & set(RETIRED))


def test_migration_is_idempotent():
    settings = _settings("ml")
    assert _migrate_retired_controllers(settings) is True
    assert _migrate_retired_controllers(settings) is False


@pytest.mark.parametrize(
    "settings",
    [{}, {"controller": None}, {"controller": {}}, {"controller": {"config": None}}],
)
def test_malformed_or_missing_controller_tree_is_left_for_normal_repair(settings):
    before = copy.deepcopy(settings)
    assert _migrate_retired_controllers(settings) is False
    assert settings == before
```

Use the real default PID mapping; do not restate its values in this test.

- [ ] **Step 3: Run the new test and confirm RED**

```bash
uv run pytest tests/unit/common/test_settings_migration_retired_controllers.py -v
```

Expected: collection fails because `_migrate_retired_controllers` does not exist.

- [ ] **Step 4: Implement the migration helper and register schema version 3**

In `common/settings_migration.py`, add one private tuple containing the six retired IDs and an idempotent `_migrate_retired_controllers(settings) -> bool` immediately before `_SHAPE_MIGRATIONS`.

Implementation rules:

1. Require `settings["controller"]` and `controller["config"]` to be mappings; malformed trees return `False` for normal repair to handle.
2. If `controller["selected"]` is in the retired tuple, assign `"pid"`.
3. Remove every retired key from `controller["config"]` with `pop(name, sentinel)`.
4. Return whether either the selection or config changed.
5. Never create, merge, or normalize `config["pid"]`.

Append `(3, _migrate_retired_controllers)` to `_SHAPE_MIGRATIONS`. Change `SETTINGS_SCHEMA_VERSION` from `2` to `3` in `common/settings_schema.py`. Do not tie this step to the release/build version.

- [ ] **Step 5: Add a full migration-registry integration test**

Extend the new test file with a stored settings tree stamped at schema version 2. Exercise the same schema upgrade entry point used by `common.datastore._upgrade_settings_in_store()`, then assert:

- schema version is 3;
- a retired selection is now `pid`;
- the pre-existing PID config is unchanged;
- all retired config blocks are absent.

Follow the datastore fixture and write/read pattern in `tests/unit/common/test_settings_migration_matrix.py`; do not mock SQLite or call the helper in this integration test.

- [ ] **Step 6: Run focused migration and schema tests**

```bash
uv run pytest \
  tests/unit/common/test_settings_migration_retired_controllers.py \
  tests/unit/common/test_settings_schema.py \
  tests/unit/common/test_settings_shape_digest.py -v
```

If the shape digest remains unchanged because `ControllerSettings.config` is intentionally dynamic, retain the existing digest and document in the test why a data migration still advances the schema version. If it changes, update the recorded digest to the value emitted by the failing test.

- [ ] **Step 7: Format and inspect the revision**

```bash
uv run ruff format common/settings_migration.py common/settings_schema.py \
  tests/unit/common/test_settings_migration_retired_controllers.py
uv run ruff check common/settings_migration.py common/settings_schema.py \
  tests/unit/common/test_settings_migration_retired_controllers.py
jj st
```

Expected revision description: `fix(settings): migrate retired controllers to pid`.

---

## Task 2: Reduce the backend and frontend catalog to three controllers

**Files:**

- Modify: `controller/controllers.json`
- Create: `tests/unit/controller/test_controller_catalog.py`
- Modify: `web-react/src/helpers/settings/controllerTypes.gen.ts` (generated)
- Modify: `web-react/tests/e2e/fixtures/controller-metadata.json`
- Modify: `web-react/tests/e2e/fixtures/settings.json`
- Modify: `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx`
- Modify: `web-react/tests/unit/scripts/emitControllerTypes.test.ts`
- Test: `web-react/scripts/emitControllerTypes.ts`

### Steps

- [ ] **Step 1: Start a catalog revision**

```bash
jj new -m "refactor(controller): keep pid pid-sp and mpc"
```

- [ ] **Step 2: Write the failing backend catalog contract**

Create `tests/unit/controller/test_controller_catalog.py`:

```python
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[3] / "controller" / "controllers.json"
RETAINED = {"pid", "pid_sp", "mpc"}


def test_controller_catalog_contains_exactly_the_supported_controllers():
    metadata = json.loads(CATALOG.read_text())["metadata"]
    assert set(metadata) == RETAINED
    assert {entry["module_name"] for entry in metadata.values()} == RETAINED
```

Run it and confirm it fails with the six retired IDs present:

```bash
uv run pytest tests/unit/controller/test_controller_catalog.py -v
```

- [ ] **Step 3: Delete the six retired manifest entries**

Remove the complete JSON objects for:

- `pid_clamping`;
- `pid_clamping_percent_pb`;
- `pid_ac`;
- `pid_parallel`;
- `fuzzy`;
- `ml`.

Do not change option defaults, recommendations, descriptions, dependencies, or ordering for `pid`, `pid_sp`, or `mpc`.

Validate and rerun the contract:

```bash
uv run python -m json.tool controller/controllers.json >/dev/null
uv run pytest tests/unit/controller/test_controller_catalog.py -v
```

- [ ] **Step 4: Regenerate the frontend controller types**

```bash
cd web-react
bun run gen:types
```

Inspect `src/helpers/settings/controllerTypes.gen.ts`: `ControllerConfigs` and generated config interfaces must name only `pid`, `pid_sp`, and `mpc`. Do not hand-edit the generated file.

- [ ] **Step 5: Reduce frontend fixtures to the retained catalog**

Update `web-react/tests/e2e/fixtures/controller-metadata.json` to contain the exact three production manifest entries. Update `web-react/tests/e2e/fixtures/settings.json` so `controller.config` contains only `pid`, `pid_sp`, and `mpc`.

Keep the retained values byte-for-byte equal to the production manifest/default fixture values; this is deletion, not retuning.

- [ ] **Step 6: Replace retired-controller UI cases with retained behavior**

In `ControllerTab.test.tsx`:

- reduce local metadata/config fixtures to the three retained IDs;
- add an assertion that the selector options have values exactly `pid`, `pid_sp`, and `mpc`;
- replace the fuzzy no-config/save tests with an MPC selection test that renders an MPC field and saves into `controller.config.mpc`;
- replace `pid_parallel` field tests with a PID-SP-only field such as `tau` or `theta`;
- remove type assertions that exist only to contrast PID with `pid_parallel`.

Preserve existing tests for unsaved changes, validation, save payloads, and controller-specific field isolation.

- [ ] **Step 7: Keep the generic type emitter generic**

Update `emitControllerTypes.test.ts` fixture and expected generated names to `pid`, `pid_sp`, and `mpc`. Retain one synthetic no-options manifest entry inside the emitter unit test only if needed to preserve the generic `Record<string, never>` contract; name it something neutral such as `no_options`, not a retired production controller.

Do not specialize `emitControllerTypes.ts` for the three current IDs.

- [ ] **Step 8: Run focused backend and frontend tests**

```bash
uv run pytest tests/unit/controller/test_controller_catalog.py -v
cd web-react
bun run test -- src/unit/components/settings/tabs/ControllerTab.test.tsx \
  src/unit/scripts/emitControllerTypes.test.ts
bun run typecheck
```

- [ ] **Step 9: Format and inspect the revision**

```bash
uv run ruff format tests/unit/controller/test_controller_catalog.py
uv run ruff check tests/unit/controller/test_controller_catalog.py
cd web-react && bun run lint
jj st
```

Expected revision description: `refactor(controller): keep pid pid-sp and mpc`.

---

## Task 3: Delete retired implementations and narrow retained behavior tests

**Files:**

- Delete: `controller/pid_clamping.py`
- Delete: `controller/pid_clamping_percent_pb.py`
- Delete: `controller/pid_ac.py`
- Delete: `controller/pid_parallel.py`
- Delete: `controller/fuzzy.py`
- Delete: `controller/ml.py`
- Delete: `controller/update_fuzzy.py`
- Delete: `controller/update_ml.py`
- Delete: `tests/unit/controller/test_fuzzy.py`
- Delete: `tests/unit/controller/test_ml.py`
- Delete: `tests/unit/controller/test_update_fuzzy.py`
- Delete: `tests/unit/controller/test_update_ml.py`
- Modify: `tests/unit/controller/test_controller_construct_smoke.py`
- Rename: `tests/characterization/test_pid_variants_golden.py` to `tests/characterization/test_pid_controllers_golden.py`

### Steps

- [ ] **Step 1: Start a code-removal revision**

```bash
jj new -m "refactor(controller): remove retired implementations"
```

- [ ] **Step 2: Narrow the construction smoke test before deleting modules**

Update `test_controller_construct_smoke.py` so its explicit controller configurations contain only `pid` and `pid_sp`, and its non-PID import case contains only `mpc`.

Add one test that reads `controller/controllers.json`, imports every manifest `module_name`, and asserts each module exposes `Controller`. This makes catalog-to-module consistency the durable contract instead of maintaining another hand-written list.

Run the file; expected PASS before deletions:

```bash
uv run pytest tests/unit/controller/test_controller_construct_smoke.py -v
```

- [ ] **Step 3: Preserve only PID and PID-SP golden traces**

Rename the characterization file with the LSP file-rename operation when available so references follow automatically:

```text
LSP rename_file tests/characterization/test_pid_variants_golden.py
  -> tests/characterization/test_pid_controllers_golden.py
```

Change its module docstring from six variants to the two supported PID-family controllers. Delete retired configs and golden rows; leave the existing `pid` and `pid_sp` arrays unchanged.

Run:

```bash
uv run pytest tests/characterization/test_pid_controllers_golden.py -v
```

- [ ] **Step 4: Delete retired modules, utilities, and dedicated tests**

Delete every file listed as `Delete` in this task. Do not leave import shims, deprecation wrappers, skipped tests, or placeholder modules.

- [ ] **Step 5: Prove retained imports and behavior after deletion**

```bash
uv run pytest \
  tests/unit/controller/test_controller_catalog.py \
  tests/unit/controller/test_controller_construct_smoke.py \
  tests/characterization/test_pid_controllers_golden.py \
  tests/unit/controller/test_pid.py \
  tests/unit/controller/test_pid_sp.py -v
```

If the exact retained PID test filenames differ, use the existing PID/PID-SP unit files found in `tests/unit/controller/`; do not substitute a broad suite for this focused check.

- [ ] **Step 6: Search live code and tests for retired IDs**

Use the repository search tool over `controller`, `common`, `tests`, `web-react`, `blueprints`, `tools`, and `updater`. Every hit must be one of:

- the migration's explicit retired-ID tuple;
- the migration tests;
- a release note naming what was removed.

Remove all executable, selectable, generated, fixture, and test-matrix references. Historical documents under `docs/superpowers/specs` and `docs/superpowers/plans` are intentionally exempt.

- [ ] **Step 7: Format and inspect the revision**

```bash
uv run ruff format tests/unit/controller/test_controller_construct_smoke.py \
  tests/characterization/test_pid_controllers_golden.py
uv run ruff check tests/unit/controller/test_controller_construct_smoke.py \
  tests/characterization/test_pid_controllers_golden.py
jj st
```

Expected revision description: `refactor(controller): remove retired implementations`.

---

## Task 4: Remove retired artifacts, dependencies, and current documentation

**Files:**

- Delete: `controller/fuzzy.pickle`
- Delete: `controller/ml_model.joblib`
- Delete: `controller/ml_dataset.csv`
- Delete: `controller/readme_fuzzy.md`
- Delete: `static/img/controller/fuzzy.png`
- Modify: `controller/readme.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock` (generated)
- Modify: `updater/updater_manifest.json`
- Modify: `auto-install/install-debian.sh`
- Modify: `auto-install/install-fedora.sh`
- Modify: `auto-install/install.sh`
- Modify: `README.md`

### Steps

- [ ] **Step 1: Start a dependency/documentation revision**

```bash
jj new -m "chore(controller): drop fuzzy and ml dependencies"
```

- [ ] **Step 2: Delete persisted/generated retired artifacts**

Delete the pickle, joblib model, ML dataset, fuzzy README, and fuzzy image listed above. These are not migration inputs and must not remain as orphaned source-tree data.

- [ ] **Step 3: Remove direct Python dependencies and regenerate the lock**

Delete only these top-level dependency declarations from `pyproject.toml`:

```toml
"scikit-fuzzy>=0.5.0",
"scikit-learn>=1.9.0",
```

Keep direct `numpy` and `scipy` declarations. Regenerate rather than hand-edit the lock:

```bash
uv lock
```

Use `uv tree --package pifire` or the context-mode dependency-tree route to verify neither package remains reachable. If either survives transitively, identify the retained parent dependency and amend the plan/design before deleting lock expectations.

- [ ] **Step 4: Remove updater and installer claims**

Delete fuzzy/ML package entries from `updater/updater_manifest.json`. Update installer comments that currently describe the scientific stack as `scipy/scikit-learn` or list scikit-learn among lockfile-installed packages. Do not alter package installation commands unrelated to these retired controllers.

Validate the updater JSON:

```bash
uv run python -m json.tool updater/updater_manifest.json >/dev/null
```

- [ ] **Step 5: Rewrite current controller documentation**

In `controller/readme.md`:

- state that the supported controllers are standard PID, PID Smith Predictor, and MPC;
- make examples use retained IDs/config blocks only;
- remove fuzzy/ML generation and installation instructions;
- retain the generic instructions for adding a future controller.

Do not rewrite historical design/plan documents.

Add a concise entry to the current `README.md` release/What's New section: six named controllers were removed; upgraded installations that selected one migrate to standard PID while preserving their existing standard-PID configuration.

- [ ] **Step 6: Verify dependency and documentation cleanup**

```bash
uv sync --all-extras --dev
uv run python -c "import numpy, scipy; print(numpy.__version__, scipy.__version__)"
```

Search non-historical live paths for `skfuzzy`, `sklearn`, `scikit-fuzzy`, `scikit-learn`, `fuzzy.pickle`, `ml_model.joblib`, and `ml_dataset.csv`. Expected: no live-code or current-documentation hits.

- [ ] **Step 7: Run installer/updater focused tests**

Run the existing tests that validate `pyproject.toml`, `uv.lock`, updater manifests, and installer dependency parity. Locate them by symbol/path before execution; expected PASS without weakening assertions.

- [ ] **Step 8: Inspect the revision**

```bash
jj st
```

Expected revision description: `chore(controller): drop fuzzy and ml dependencies`.

---

## Task 5: Integrated verification and evidence audit

**Files:**

- Modify only if a failing retained contract exposes a real stale reference.

### Steps

- [ ] **Step 1: Run focused Python regression suites**

```bash
uv run pytest \
  tests/unit/common/test_settings_migration_retired_controllers.py \
  tests/unit/common/test_settings_schema.py \
  tests/unit/controller/test_controller_catalog.py \
  tests/unit/controller/test_controller_construct_smoke.py \
  tests/characterization/test_pid_controllers_golden.py \
  tests/unit/controller \
  tests/unit/runtime -v
```

Expected: all retained controller, migration, and runtime tests pass.

- [ ] **Step 2: Run focused frontend verification**

```bash
cd web-react
bun run gen:types
bun run typecheck
bun run lint
bun run test -- src/unit/components/settings/tabs/ControllerTab.test.tsx \
  src/unit/scripts/emitControllerTypes.test.ts
```

Then exercise the settings controller selector in the running UI or its existing Playwright settings flow and observe exactly three options: PID Standard, PID Smith Predictor, and MPC.

- [ ] **Step 3: Run full project verification**

Use context-mode for the large outputs:

```bash
uv run pytest
cd web-react && bun run test
```

Run the project's full Python static checks and frontend production build. Record exact pass/fail totals; do not summarize unobserved commands as passing.

- [ ] **Step 4: Audit clean-cutover invariants**

Confirm all of the following:

- production manifest keys equal `{pid, pid_sp, mpc}`;
- default settings contain config blocks for exactly those IDs;
- generated TypeScript names exactly those IDs;
- each retained manifest module imports and constructs;
- all six retired selections migrate to PID;
- retired config blocks are removed;
- no retired Python module, generator, model artifact, UI fixture, image, or direct dependency remains;
- no compatibility alias or deprecated path was introduced;
- PID/PID-SP golden outputs are unchanged;
- historical superpowers documents are the only exempt retired-name references.

- [ ] **Step 5: Review the Jujutsu series**

```bash
jj --no-pager log -r 'trunk()..@' --no-graph -T 'change_id.short() ++ " " ++ description.first_line() ++ "\n"'
jj --no-pager diff --git --from 'trunk()' --to '@'
```

Expected: one logical revision per task, no unrelated files, no empty or undescribed revision.

---

## Requirement coverage

| Design requirement | Tasks |
|---|---|
| Exactly PID, PID-SP, MPC in catalog/UI/types | 2, 5 |
| Retired selections migrate deterministically to PID | 1, 5 |
| Existing PID config preserved; stale blocks deleted | 1, 5 |
| No shims or retired implementations remain | 3, 5 |
| Fuzzy/ML dependencies and artifacts removed | 4, 5 |
| Retained PID/PID-SP/MPC behavior remains covered | 3, 5 |
| Release documentation describes the cutover | 4 |
