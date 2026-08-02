# Persisted Schema Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the settings tree and the pellet database each an explicit, enforced schema version, so a shape migration is gated on the shape's own version instead of on the release build number — and close the unvalidated pellet-restore path while doing it.

**Architecture:** Two durable blobs get the same three pieces: a module-level `*_SCHEMA_VERSION` integer stored as a top-level `schema_version` key on the blob, an ordered `[(target_version, migration)]` registry whose runner stamps last inside the store's existing `BEGIN IMMEDIATE`, and a committed shape-digest test that fails when the modeled shape changes without a version bump. The pellet database additionally gets its first pydantic model, wired into `write_pellet_db()` exactly as `validate_settings_tree()` is wired into `write_settings()`.

**Tech Stack:** Python 3.14, pydantic v2 (strict mode), SQLite (`common/datastore.py`), pytest, React 19 + TypeScript (`web-react/`), rstest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-02-persisted-schema-versioning-design.md` — read it before Task 1. Section references below (§1, §5.3, …) point into it.

---

## Global Constraints

- **This machine is not the grill.** A `gunicorn --reload` on :5000 and a guarded `control.py` are running against the live `pifire.db`. Never start, stop, or drive either. Never write to `pifire.db`.
- **Before running any script, grep its path** for `os.system`, `subprocess`, `sudo`, `reboot`, `shutdown` and neutralize them first. A `real_hw=False` flag is not enough.
- **Version control is `jj`, not `git`.** The repo is colocated, so `git commit` silently works and is wrong. Per task: `jj new` **before the first Write**, edit, then `jj describe --stdin` (there is no `-F` flag). Never `jj squash` after editing — the edits are already in `@`.
- **Backticks inside double-quoted shell arguments get eaten by zsh.** Use a quoted heredoc or a file for any commit message or `python3 -c` containing them.
- **Format before every commit:** `.venv/bin/ruff format <changed files>` then `.venv/bin/ruff check .`. Never `uvx ruff` — the repo pins ruff <0.16.
- **Python test command (the only one that is green):**
  `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/`
  Bare `python` gives false failures. Baseline at the time of writing: **4635 passed, 4 skipped**.
- **`[chromium]`-marked tests SKIP in agent worktrees.** Any task touching `tests/web/*.py` must have those files re-run in the main checkout before merge.
- **web-react uses `bun`, never `npm`.** Gates: `bun run typecheck`, `bun run lint`, `bun run test` (rstest — **never** `bun test`), `bun run build`. Baseline lint: 0 errors, 2 pre-existing warnings.
- **Source comments state what the code achieves.** Never narrate the change, the measurement, or the reasoning that produced it. No "previously…", no "this fixes…".
- **`ruff` canonicalises `except (A, B)` to `except A, B`.** That is correct for this repo's Python version. Do not "fix" it back.
- **SQLite is authoritative.** `pifire.db` is the live store; `settings.json` is only ever an export/backup/first-boot import.
- **Do not touch `versions.server` / `versions.build` semantics.** This plan adds a dimension; it removes nothing (spec, *Backward compatibility*).

---

## File Structure

| File | Responsibility | Slice |
|---|---|---|
| `common/schema_digest.py` | **NEW.** `shape_entries(model)` / `shape_digest(model)` — the shape hash both blobs' gates use. Pure function of a pydantic model; imports nothing from PiFire. | A |
| `tests/unit/common/test_schema_digest.py` | **NEW.** The digest's own negative controls: it fires on a retype, a removed path and an added constraint; it does NOT fire on a changed default. | A |
| `tests/unit/common/test_settings_shape_digest.py` | **NEW.** The settings gate — one committed constant, one assertion, one failure message naming the two things to do. | A |
| `common/settings_schema.py` | `SETTINGS_SCHEMA_VERSION` + the `schema_version` field on `SettingsSchema`. | A |
| `common/defaults.py` | Stamps a fresh tree CURRENT; later, seeds `est_usage` as a float and drops the redundant profile `id`. | A, C |
| `common/settings_migration.py` | `_SHAPE_MIGRATIONS` — the ordered registry. `_migrate_i2c_buses` is re-gated, not rewritten. | A |
| `common/datastore.py` | The runner inside `_upgrade_settings_in_store`; the new `_upgrade_pellets_in_store`, called from `init()`. | A, B |
| `common/pellets_schema.py` | **NEW.** `PelletDbSchema`, `PelletDbValidationError`, `validate_pellet_db()`, `PELLETDB_SCHEMA_VERSION`, and the v1→v2 migrations. Mirrors `settings_schema.py`'s structure so the codebase has one pattern, not two. | B, C |
| `common/datastore_accessors.py` | `write_pellet_db()` calls the gate. | B |
| `common/backups.py` | `read_pellet_db_file()`'s top-level overlay must survive a scalar key. | B |
| `blueprints/api_admin/routes.py` | The restore route answers 400 on a malformed pellet backup, matching its settings branch. | B |
| `common/pellets_actions.py` | The writers: bounded `rating`, vocabulary append, no redundant `id`, tombstone objects, epoch-ms log keys. | C |
| `tests/fixtures/pelletdb_live.json` | **NEW.** The pellet database as a real grill holds it. Captured, never authored. | B |
| `web-react/src/helpers/pellets/pelletTypes.ts` | The v2 shape, hand-written and pinned by `tests/web/test_api_pellets.py`. | C |
| `web-react/src/components/pellets/PelletLog.tsx` | Renders epoch-ms keys as dates and sorts them numerically. | C |

---

## Slices

Each slice is independently shippable and leaves the tree green.

| Slice | Tasks | Ships |
|---|---|---|
| **A — the settings tree's shape version** | 1–4 | The gate, the version, the registry. The release number stops being load-bearing for the i2c shape. |
| **B — the pellet database, version 1** | 5–7 | The pellet DB's first model, and with it the 400 that the restore route has never had (spec §5.1). No data is reshaped. |
| **C — the pellet database, version 2** | 8–11 | The sane shape, its four migrations, the writers that produce it, and the UI that reads it. |

**Ship A before B, and B before C.** B's whole argument is that validation lands on a release where nothing is reshaped, so a rejected restore is unambiguous (spec, *Backward compatibility*).

---

## Risks and judgement calls

Read these before starting. Each is a decision this plan makes on the spec's behalf; raise it rather than diverging quietly.

1. **`write_pellet_db()` raises, including on the control process's path.**
   `controller/runtime/store.py:452` calls it every 60s and at each mode end. A blob that fails validation there takes down the control loop. This plan mirrors `write_settings()` exactly (spec §5.5 says "exactly as") and relies on `_upgrade_pellets_in_store()` at `init()` having brought the blob to the current shape before any process writes it. Task 6 adds a test that the control-process call path succeeds against the live fixture. The alternative — swallowing the error in `write_pellet_db` — persists nothing anyway and hides the failure, so it was not taken.

2. **Handler-level rejections are HTTP 200 with an `Error` envelope, not 400.**
   Spec §5.7 asks for "a 400 naming the field". `common/pellets_actions.py` is shared by the REST route and the Socket.IO namespace, and `blueprints/api/routes.py:_api_post_pellets` returns `200` for every handler result — the existing contract for "Cannot delete current profile" too. Task 10 uses `api_response(result="Error", message=...)`, which `pelletsApi.ts` already surfaces to the operator. The **restore route** (Task 6) does return a real 400, because that route builds its own response and its settings branch already does.

3. **`current.pelletid` missing from `archive` is a hard rejection** (spec §5.8), even though `pelletTypes.ts:22` claims it can happen after a clear. `clear_pellet_db()` reseeds both consistently and `pellets_delete_profile` refuses to delete the loaded profile, so the live invariant holds — but if an install in the field violates it, its next write fails. Task 5 asserts the live fixture satisfies it; if an operator ever reports otherwise, this validator is the thing to soften into a repair.

4. **Naming an unlisted brand or wood now adds it to the vocabulary** (Task 10). This is a behaviour change to the pellet UI's autocomplete, reasoned from the writers rather than from a product decision — the spec records it as an open judgement call (§*Open questions* 1), accepted at approval. `brand`/`wood` stay free text either way: "Custom" is a first-class brand and an operator naming a bag the list has not heard of is the normal case. If the vocabularies are ever meant to be curated, this is the line to revisit.

5. **The digest does not cover `union_mode`, nor required-vs-defaulted.** Pydantic folds `Field(union_mode=...)` into the `FieldInfo` rather than into `metadata`, and giving a required field a default cannot invalidate stored data (every existing blob already carries a value). Both are documented in `common/schema_digest.py`'s docstring rather than worked around.

---

# Slice A — the settings tree's shape version

### Task 1: The shape digest, and its own negative controls

Nothing changes at runtime in this task. It builds the gate and proves the gate can fail, so that Task 2 — which deliberately trips it — means something.

**Files:**
- Create: `common/schema_digest.py`
- Create: `tests/unit/common/test_schema_digest.py`
- Create: `tests/unit/common/test_settings_shape_digest.py`

**Interfaces:**
- Produces: `shape_entries(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[str]` and `shape_digest(model: type[BaseModel]) -> str` (64-char lowercase hex). Task 7 calls `shape_digest(PelletDbSchema)`.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test — the digest's negative controls**

Create `tests/unit/common/test_schema_digest.py`:

```python
"""What the shape digest must and must not notice.

A gate that cannot fail is the failure it exists to prevent, so every case
here is a control: three changes that MUST move the digest, and two that must
not. The models are local throwaways -- asserting these properties against
SettingsSchema would only prove that today's schema happens to have a field
of the right kind.
"""

from pydantic import BaseModel, Field

from common.schema_digest import shape_digest


class _Base(BaseModel):
    rating: int
    label: str


def test_a_retype_moves_the_digest():
    class Retyped(BaseModel):
        rating: str
        label: str

    assert shape_digest(_Base) != shape_digest(Retyped)


def test_a_removed_path_moves_the_digest():
    class Shorter(BaseModel):
        rating: int

    assert shape_digest(_Base) != shape_digest(Shorter)


def test_an_added_constraint_moves_the_digest():
    """The case a paths-only digest would miss, and the one the pellet DB's v2
    migration is (spec 5.6)."""

    class Bounded(BaseModel):
        rating: int = Field(ge=1, le=5)
        label: str

    assert shape_digest(_Base) != shape_digest(Bounded)


def test_a_changed_default_does_not_move_the_digest():
    """A default cannot invalidate stored data -- every existing blob already
    carries a value -- and a gate that fires on one trains its readers to
    update the constant without reading it."""

    class Defaulted(BaseModel):
        rating: int = 4
        label: str = "x"

    assert shape_digest(_Base) == shape_digest(Defaulted)


def test_a_changed_description_does_not_move_the_digest():
    class Described(BaseModel):
        rating: int = Field(description="stars, one to five")
        label: str

    assert shape_digest(_Base) == shape_digest(Described)


def test_the_alias_is_what_the_path_uses():
    """The digest describes the shape ON DISK, not the Python attribute names."""

    class Aliased(BaseModel):
        one_wire: int = Field(alias="1WIRE")

    assert any(entry.startswith("1WIRE:") for entry in _entries(Aliased))


def _entries(model):
    from common.schema_digest import shape_entries

    return shape_entries(model)
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_schema_digest.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common.schema_digest'`.

- [ ] **Step 4: Write the implementation**

Create `common/schema_digest.py`:

```python
"""A stable digest of the SHAPE a pydantic model persists.

One entry per leaf, each `path: annotation [constraints]`:

  * **Path** uses the field's ALIAS where it has one, because the alias is what
    is on disk -- `platform.system.1WIRE`, not `platform.system.one_wire`.
  * **Annotation** is included, so a retype cannot keep a path and pass
    silently. Union members are sorted, since a scalar union's order carries
    no meaning; a union OF MODELS is walked per member and keyed by INDEX,
    because `union_mode="left_to_right"` makes that order decide which member
    a value matches.
  * **Constraints** are the field's `metadata` -- `Ge`, `Le`,
    `StringConstraints` and the rest. A tightened bound can reject data an
    install already holds, which is the definition of needing a migration.

Defaults and descriptions are EXCLUDED. Neither can invalidate stored data,
and a digest that moved on one would be updated without being read.

Two things it does not distinguish, both by the same reasoning: a field
gaining a default (every stored blob already carries a value), and a change
to `union_mode`, which pydantic folds into the FieldInfo rather than into
`metadata`.

Run against a model to see the entries behind a digest:

    uv run python -m common.schema_digest common.settings_schema:SettingsSchema
"""

import hashlib
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _annotation_repr(annotation: Any) -> str:
    if _is_model(annotation):
        return "<model>"
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        return " | ".join(sorted(_annotation_repr(arg) for arg in get_args(annotation)))
    args = get_args(annotation)
    if args:
        name = getattr(origin, "__name__", str(origin))
        return f"{name}[{', '.join(_annotation_repr(arg) for arg in args)}]"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _constraints_repr(metadata) -> str:
    return ", ".join(sorted(repr(item) for item in metadata))


def shape_entries(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[str]:
    """One `path: annotation [constraints]` line per leaf of `model`."""
    entries: list[str] = []
    for name, info in model.model_fields.items():
        path = prefix + (info.alias or name,)
        annotation = info.annotation

        if _is_model(annotation):
            entries.extend(shape_entries(annotation, path))
            continue

        origin = get_origin(annotation)
        members = get_args(annotation) if origin is Union or origin is UnionType else ()
        if members and all(_is_model(member) for member in members):
            for index, member in enumerate(members):
                entries.extend(shape_entries(member, path + (f"<{index}>",)))
            continue

        entries.append(f"{'.'.join(path)}: {_annotation_repr(annotation)} [{_constraints_repr(info.metadata)}]")
        # A model reached through a container -- dict[str, ProbeChartConfig],
        # list[PwmProfile] -- is still part of the persisted shape.
        for index, arg in enumerate(get_args(annotation)):
            if _is_model(arg):
                entries.extend(shape_entries(arg, path + (f"[{index}]",)))
    return entries


def shape_digest(model: type[BaseModel]) -> str:
    """A hex SHA-256 over `shape_entries(model)`, sorted."""
    return hashlib.sha256("\n".join(sorted(shape_entries(model))).encode()).hexdigest()


if __name__ == "__main__":
    import importlib
    import sys

    module_name, _, attr = sys.argv[1].partition(":")
    target = getattr(importlib.import_module(module_name), attr)
    for entry in sorted(shape_entries(target)):
        print(entry)
    print()
    print(shape_digest(target))
```

- [ ] **Step 5: Run the negative controls to verify they pass**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_schema_digest.py -v
```

Expected: PASS, 6 tests.

- [ ] **Step 6: Write the settings gate**

Create `tests/unit/common/test_settings_shape_digest.py`:

```python
"""The gate that makes SETTINGS_SCHEMA_VERSION a fact rather than a promise.

A shape change without a version bump is invisible until an operator's install
fails to migrate -- the i2c migration was skipped by an unchecked path four
separate times before anyone noticed. This test is what makes a human look.
"""

from common.schema_digest import shape_digest
from common.settings_schema import SettingsSchema

#: Regenerate with:
#:   uv run python -m common.schema_digest common.settings_schema:SettingsSchema
SETTINGS_SHAPE_DIGEST = "14a5e8db901360716a33d0c8cc1505a91fe8a725c15d9f129be18a52dbed8973"

_MESSAGE = """The modeled settings shape changed. Two things to do, in order:

  1. Bump SETTINGS_SCHEMA_VERSION in common/settings_schema.py, and add a step
     to _SHAPE_MIGRATIONS in common/settings_migration.py. If the change moves
     no data (a pure widening), register a no-op step anyway -- the step list
     is the record that the question was asked.
  2. Update SETTINGS_SHAPE_DIGEST below, from:
     uv run python -m common.schema_digest common.settings_schema:SettingsSchema
"""


def test_shape_change_requires_a_schema_version_bump():
    assert shape_digest(SettingsSchema) == SETTINGS_SHAPE_DIGEST, _MESSAGE
```

- [ ] **Step 7: Verify the committed digest is the real one**

```bash
uv run python -m common.schema_digest common.settings_schema:SettingsSchema | tail -1
```

Expected: `14a5e8db901360716a33d0c8cc1505a91fe8a725c15d9f129be18a52dbed8973`.

If it differs, the schema moved since this plan was written — paste the printed value into `SETTINGS_SHAPE_DIGEST` and note it in the commit message.

- [ ] **Step 8: Run the gate and the full suite**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_shape_digest.py -v
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: the gate passes; the suite is at baseline + 7.

- [ ] **Step 9: Format and commit**

```bash
.venv/bin/ruff format common/schema_digest.py tests/unit/common/test_schema_digest.py tests/unit/common/test_settings_shape_digest.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(schema): hash the shape a model persists, and gate the settings tree on it

The digest covers paths, annotations and constraints, and excludes defaults
and descriptions -- what can invalidate stored data, and what cannot.
EOF
```

---

### Task 2: `SETTINGS_SCHEMA_VERSION` and the stamp

This task changes the modeled shape, so Task 1's gate fires. That is the demonstration: fix it by doing the two things the failure message names.

**Files:**
- Modify: `common/settings_schema.py` (above `class SettingsSchema`, and the class body)
- Modify: `common/defaults.py:34-38` (`default_settings`)
- Modify: `tests/unit/common/test_settings_shape_digest.py` (the constant)
- Modify: `web-react/schema/settings.schema.json` (regenerated)
- Modify: `web-react/src/helpers/settings/settingsTypes.gen.ts` (regenerated)
- Modify: `web-react/tests/e2e/fixtures/settings.json`

**Interfaces:**
- Consumes: `shape_digest` from Task 1.
- Produces: `common.settings_schema.SETTINGS_SCHEMA_VERSION: int` (value `1`) and the top-level key `settings["schema_version"]`. Task 3's runner reads both.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/common/test_settings_schema.py` (below `test_all_sections_are_modeled`):

```python
def test_a_fresh_tree_is_stamped_with_the_current_shape_version():
    """A tree the defaults just built needs no migration, so it starts at
    CURRENT -- the same reasoning as _ensure_schema's `0 <` guards."""
    from common.settings_schema import SETTINGS_SCHEMA_VERSION

    assert default_settings()["schema_version"] == SETTINGS_SCHEMA_VERSION


def test_the_shape_version_is_not_derived_from_the_release_version():
    """The coupling being fixed. schema_version must never move because a
    release number moved."""
    from common.settings_schema import SETTINGS_SCHEMA_VERSION

    settings = default_settings()
    settings["versions"]["build"] = settings["versions"]["build"] + 1000
    settings["versions"]["server"] = "9.9.9"

    assert settings["schema_version"] == SETTINGS_SCHEMA_VERSION
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_schema.py -k shape_version -v
```

Expected: FAIL with `ImportError: cannot import name 'SETTINGS_SCHEMA_VERSION'`.

- [ ] **Step 4: Add the constant and the field**

In `common/settings_schema.py`, immediately above `class SettingsSchema(_Section):`:

```python
#: The shape of the settings tree, independent of the release version. Bumped
#: by any change to the persisted shape that an existing install cannot simply
#: be validated into: a rename, a retype, a restructure. NOT bumped for an
#: added or deleted field -- the write-time repair handles both losslessly
#: (added takes its default, deleted is stripped).
#:
#: It lives at the top level of the tree rather than beside versions.server /
#: versions.build, so that nothing suggests the three move together. They do
#: not: versions is the RELEASE, read from updater/updater_manifest.json.
SETTINGS_SCHEMA_VERSION = 1
```

and as the first field of `SettingsSchema`:

```python
class SettingsSchema(_Section):
    schema_version: int = SETTINGS_SCHEMA_VERSION
    versions: Versions
    ...
```

- [ ] **Step 5: Stamp the defaults**

In `common/defaults.py`, add to the import block at line 20:

```python
from common.settings_schema import SETTINGS_SCHEMA_VERSION
```

and in `default_settings()`, immediately after `settings = {}`:

```python
    settings["schema_version"] = SETTINGS_SCHEMA_VERSION
```

- [ ] **Step 6: Prove there is no import cycle**

`common/defaults.py` now imports `common/settings_schema.py`, which imports `common/common.py`, which imports `common/datastore.py`. `datastore` defers its `common.defaults` import into function bodies, so the graph stays acyclic — confirm rather than assume:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_import_smoke.py -v
uv run python -c "import common.defaults; import common.settings_schema; print('ok')"
```

Expected: PASS, then `ok`.

- [ ] **Step 7: Watch the gate fire, then update it**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_shape_digest.py -v
```

Expected: FAIL, printing the two-step message from Task 1.

Step 1 of that message is already satisfied and needs no further action here: adding a field is the one shape change the version rule explicitly does not bump for (the repair pass gives an existing tree the default), and the `1` this task introduces is the tree's FIRST stamp — its step, `_migrate_i2c_buses`, arrives in Task 3. Do Step 2:

```bash
uv run python -m common.schema_digest common.settings_schema:SettingsSchema | tail -1
```

Paste the printed digest into `SETTINGS_SHAPE_DIGEST` in `tests/unit/common/test_settings_shape_digest.py`.

- [ ] **Step 8: Regenerate the web-react artifacts**

```bash
uv run python -m common.settings_schema > web-react/schema/settings.schema.json
cd web-react && bun run gen:types && cd ..
```

Then add `"schema_version": 1,` as the first key of `web-react/tests/e2e/fixtures/settings.json`.

- [ ] **Step 9: Run every gate**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
cd web-react && bun run typecheck && bun run lint && bun run test && cd ..
```

Expected: Python at baseline + 9. `test_committed_schema_is_current` and `test_default_settings_round_trips` both pass — if either fails, Step 8 or Step 5 is incomplete.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format common/settings_schema.py common/defaults.py tests/unit/common/test_settings_schema.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(settings): record the shape the tree was written against

schema_version sits at the top level rather than beside versions.server, so
nothing suggests the release number and the shape move together.
EOF
```

---

### Task 3: The ordered migration registry, replacing the ungated repair

`_migrate_i2c_buses` runs unconditionally on every connect today, because a version-gated migration was skipped four times. The version it needed is the one Task 2 added, so the workaround goes with the fix.

**Files:**
- Modify: `common/settings_migration.py` (below `_migrate_i2c_buses`)
- Modify: `common/datastore.py:385-451` (`_upgrade_settings_in_store`)
- Modify: `tests/unit/datastore/test_settings_store_migration.py:16-30` (`_legacy_stored_settings`)
- Modify: `tests/unit/common/test_settings_migration_matrix.py` (`_legacy_settings`)

**Interfaces:**
- Consumes: `SETTINGS_SCHEMA_VERSION`, `settings["schema_version"]` (Task 2).
- Produces: `common.settings_migration._SHAPE_MIGRATIONS: list[tuple[int, Callable[[dict], bool]]]`. Task 7 builds the pellet equivalent against this shape.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/datastore/test_settings_shape_migration.py`:

```python
"""The shape version decides which migrations run, and the release version
does not get a vote.

A real grill sat at 1.11.0 build 71 -- the code's own current release, so the
version gate was closed -- while still holding the pre-71 i2c settings shape.
Every case here is about what the STAMP says, with the release version held
current throughout so it cannot be the thing doing the work.
"""

import copy

import pytest

from common import datastore, settings_migration
from common.datastore_accessors import read_settings_store, write_settings_store
from common.defaults import default_settings
from common.settings_schema import SETTINGS_SCHEMA_VERSION


def _unstamped_legacy_tree():
    """A settings tree as an install predating the stamp holds it: current
    release, legacy i2c shape, no schema_version at all."""
    settings = copy.deepcopy(default_settings())
    settings.pop("schema_version", None)
    distance = settings["platform"]["devices"]["distance"]
    distance.pop("i2c_bus", None)
    distance["i2c_bus_kind"] = "extended"
    distance["i2c_bus_num"] = "CP2112"
    write_settings_store(settings)
    return settings


def test_an_unstamped_tree_runs_every_step_and_ends_stamped_current(ds):
    _unstamped_legacy_tree()

    datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    assert stored["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert stored["platform"]["devices"]["distance"]["i2c_bus"] == {"kind": "kernel", "adapter": "CP2112"}


def test_a_current_tree_runs_no_step(ds, monkeypatch):
    """Spy on the callables rather than compare trees: every step here is
    idempotent, so an output comparison would pass while the step still ran."""
    ran = []
    monkeypatch.setattr(
        settings_migration,
        "_SHAPE_MIGRATIONS",
        [(1, lambda tree: ran.append(1) or False)],
    )
    write_settings_store(copy.deepcopy(default_settings()))

    datastore._upgrade_settings_in_store()

    assert ran == []


def test_only_the_steps_above_the_stamp_run(ds, monkeypatch):
    ran = []
    monkeypatch.setattr(
        settings_migration,
        "_SHAPE_MIGRATIONS",
        [
            (1, lambda tree: ran.append(1) or False),
            (2, lambda tree: ran.append(2) or False),
            (3, lambda tree: ran.append(3) or False),
        ],
    )
    # Patch the SOURCE module: _upgrade_settings_in_store imports the constant
    # inside the function body, so it reads common.settings_schema at call time.
    monkeypatch.setattr("common.settings_schema.SETTINGS_SCHEMA_VERSION", 3)
    settings = copy.deepcopy(default_settings())
    settings["schema_version"] = 1
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    assert ran == [2, 3]


def test_a_stamp_from_the_future_runs_nothing_and_is_not_rewound(ds, monkeypatch):
    """An operator downgraded PiFire. This code cannot know what the newer
    keys meant, so it must not migrate backwards, and must not crash the boot
    path either."""
    ran = []
    monkeypatch.setattr(settings_migration, "_SHAPE_MIGRATIONS", [(1, lambda tree: ran.append(1) or True)])
    settings = copy.deepcopy(default_settings())
    settings["schema_version"] = SETTINGS_SCHEMA_VERSION + 5
    write_settings_store(settings)

    datastore._upgrade_settings_in_store()

    assert ran == []
    assert read_settings_store()["schema_version"] == SETTINGS_SCHEMA_VERSION + 5


def test_the_stamp_is_not_written_when_a_step_raises(ds, monkeypatch):
    """A version stamped ahead of the data is the one failure that cannot
    self-heal on the next boot."""

    def _explode(tree):
        raise RuntimeError("migration blew up")

    monkeypatch.setattr(settings_migration, "_SHAPE_MIGRATIONS", [(1, _explode)])
    _unstamped_legacy_tree()

    with pytest.raises(RuntimeError):
        datastore._upgrade_settings_in_store()

    stored = read_settings_store()
    assert "schema_version" not in stored
    assert stored["platform"]["devices"]["distance"]["i2c_bus_kind"] == "extended"


def test_running_the_chain_twice_is_identical_to_running_it_once(ds):
    _unstamped_legacy_tree()

    datastore._upgrade_settings_in_store()
    once = copy.deepcopy(read_settings_store())
    datastore._upgrade_settings_in_store()

    assert read_settings_store() == once
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_settings_shape_migration.py -v
```

Expected: FAIL — `AttributeError: module 'common.settings_migration' has no attribute '_SHAPE_MIGRATIONS'`.

- [ ] **Step 4: Add the registry**

In `common/settings_migration.py`, immediately below `_migrate_i2c_buses`:

```python
#: The shape migrations, in ascending order, as (target_version, migration).
#: A step's number is the version the tree is AT once that step has run, and
#: each callable mutates the tree in place and returns True if it changed
#: anything. Gated on settings["schema_version"] alone -- the release-gated
#: cascade in upgrade_settings() below is closed to new entries.
_SHAPE_MIGRATIONS = [
    (1, _migrate_i2c_buses),
]
```

- [ ] **Step 5: Run the registry from the store upgrade**

In `common/datastore.py`, add to `_upgrade_settings_in_store`'s deferred imports:

```python
    from common.settings_schema import SETTINGS_SCHEMA_VERSION, validate_settings_tree
```

and replace:

```python
        if settings_migration._migrate_i2c_buses(settings):
            changed = True
```

with:

```python
        # The stamp decides which steps run; the release version does not get
        # a vote. An unstamped tree is version 0, so every step runs once.
        # A tree from the future -- an operator downgraded PiFire -- runs
        # nothing and keeps its own stamp: this code cannot know what its
        # newer keys meant, and the strict-schema repair strips what it does
        # not model.
        stamp = settings.get("schema_version", 0)
        if stamp > SETTINGS_SCHEMA_VERSION:
            write_log(
                f"Settings shape version {stamp} is newer than this build's "
                f"{SETTINGS_SCHEMA_VERSION}; no shape migration was run."
            )
        else:
            for target, migrate in settings_migration._SHAPE_MIGRATIONS:
                if stamp < target and migrate(settings):
                    changed = True
            if stamp != SETTINGS_SCHEMA_VERSION:
                # Written last, inside the same BEGIN IMMEDIATE the read took,
                # so a crash mid-chain leaves the old stamp and the whole chain
                # retries from scratch.
                settings["schema_version"] = SETTINGS_SCHEMA_VERSION
                changed = True
```

Then update that function's docstring: the paragraph beginning "The i2c bus bus shape repair runs independently of the version cascade above and unconditionally" is no longer true. Replace it with:

```
    Shape migrations are gated on settings["schema_version"], which the release
    version cannot close: a store already stamped at the code's own current
    release still runs every shape step it has not been stamped for.
```

- [ ] **Step 6: Repair the fixtures that a stamped `default_settings()` invalidates**

Both legacy-tree builders start from `default_settings()`, which now stamps CURRENT — so their trees would be treated as already migrated and the i2c step would never run. An install holding the legacy i2c shape by definition predates the stamp, so the fixture must drop it.

In `tests/unit/datastore/test_settings_store_migration.py`, inside `_legacy_stored_settings`, immediately after the `settings = copy.deepcopy(default_settings())` line:

```python
    # An install still holding the legacy i2c shape predates the shape stamp.
    settings.pop("schema_version", None)
```

Make the identical edit in `tests/unit/common/test_settings_migration_matrix.py`'s `_legacy_settings`.

- [ ] **Step 7: Run the migration tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/datastore/test_settings_shape_migration.py \
  tests/unit/datastore/test_settings_store_migration.py \
  tests/unit/common/test_settings_migration_matrix.py \
  tests/unit/common/test_settings_migration_i2c.py -v
```

Expected: all PASS. `test_the_repair_is_not_version_gated` still passes and still means what its name says — the tree it builds carries the current RELEASE and no shape stamp, and is repaired anyway.

**`tests/unit/common/test_settings_migration_i2c.py` must not need a single edit.** That file is `_migrate_i2c_buses`'s own test, and the function is being re-gated, not rewritten — if it needs changing, something is wrong. The two fixtures Step 6 edits belong to the STORE-level upgrade tests, which are about what the datastore decides to run, not about what the migration does.

- [ ] **Step 8: Full suite**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

- [ ] **Step 9: Format and commit**

```bash
.venv/bin/ruff format common/settings_migration.py common/datastore.py tests/unit/datastore/test_settings_shape_migration.py tests/unit/datastore/test_settings_store_migration.py tests/unit/common/test_settings_migration_matrix.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(settings): gate shape migrations on the shape version

The i2c repair ran unconditionally on every connect because no version could
be trusted to gate it. There is one now, so it becomes step 1 of an ordered
registry and stops scanning every boot forever.
EOF
```

---

### Task 4: The boot path still initialises, end to end

Slice A's last task. Nothing new is built; this proves the entry points that reach `init()` survive the new stamp, and that a real upgrade from the pre-stamp shape lands where Slice B expects it.

**Files:**
- Modify: `tests/unit/datastore/test_settings_shape_migration.py` (append)

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/datastore/test_settings_shape_migration.py`:

```python
def test_a_migrated_tree_survives_a_validating_write(ds):
    """The stamp is a modeled field, so write_settings must preserve it rather
    than strip it as an unmodeled key -- which is what happens to anything the
    schema does not know about."""
    from common.datastore_accessors import write_settings

    _unstamped_legacy_tree()
    datastore._upgrade_settings_in_store()

    write_settings(read_settings_store())

    assert read_settings_store()["schema_version"] == SETTINGS_SCHEMA_VERSION


def test_init_stamps_a_pre_stamp_store(ds):
    """init() is what every entry point calls; the stamp has to arrive there
    and not only in the function under test."""
    _unstamped_legacy_tree()

    datastore.init()

    assert read_settings_store()["schema_version"] == SETTINGS_SCHEMA_VERSION
```

- [ ] **Step 3: Run it**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_settings_shape_migration.py -v
```

Expected: PASS. If `test_a_migrated_tree_survives_a_validating_write` fails, Task 2's Step 4 did not add `schema_version` to `SettingsSchema` — the repair pass is stripping it.

- [ ] **Step 4: Full suite and the entry-point scan**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_entry_points_initialise_the_datastore.py -v
```

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format tests/unit/datastore/test_settings_shape_migration.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
test(settings): the shape stamp reaches the store through init(), and survives a write
EOF
```

---

# Slice B — the pellet database, version 1

Version 1 is today's shape modeled exactly. It adds **no constraint**. Its whole job is to make an unvalidated blob a validated one, so the restore hole (spec §5.1) closes before anything is reshaped.

### Task 5: `PelletDbSchema` v1, and a fixture taken off a real grill

**Files:**
- Create: `common/pellets_schema.py`
- Create: `tests/fixtures/pelletdb_live.json`
- Create: `tests/unit/common/test_pellets_schema.py`

**Interfaces:**
- Produces: `PelletDbSchema`, `PelletDbValidationError(ValueError)` with an `.errors: list[str]`, and `validate_pellet_db(pelletdb: dict) -> dict` (returns the normalized dump). Task 6 calls `validate_pellet_db`; Task 7 adds the version constant to this module; Task 8 changes the model to v2.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Capture the fixture from the live store**

The grill's database is the source; `default_pellets()` is not, and the two disagree — `est_usage` is seeded as `int 0` there and is a float in the field (spec §5.3). Read-only, never write:

```bash
uv run python - <<'PY'
import json, sqlite3
con = sqlite3.connect("file:pifire.db?mode=ro", uri=True)
blob = con.execute("select value from kv where key='pellets:general'").fetchone()[0]
with open("tests/fixtures/pelletdb_live.json", "w") as fh:
    json.dump(json.loads(blob), fh, indent=2)
    fh.write("\n")
PY
```

Confirm it looks like the shape below — one archive entry, one log entry, a float `est_usage`:

```bash
uv run python -c "
import json
db = json.load(open('tests/fixtures/pelletdb_live.json'))
print(sorted(db))
print(type(db['current']['est_usage']).__name__, db['current']['est_usage'])
print(list(db['log'].items()))
"
```

Expected: `['archive', 'brands', 'current', 'lastupdated', 'log', 'woods']`, then `float 171.198…`, then one `("YYYY-MM-DD HH:MM:SS", "<profile id>")` pair.

- [ ] **Step 3: Write the failing test**

Create `tests/unit/common/test_pellets_schema.py`:

```python
"""Version 1 of the pellet database: today's shape, modeled exactly.

Every case here is a shape a live grill can hold. A v1 that is stricter than
the data in the field turns a restore into a 400 with no migration to blame it
on, so the traps below (spec 5.3) are ACCEPTANCE tests, not rejections -- each
one is behaviour verified in common/pellets_actions.py, not a guess.
"""

import copy
import json
from pathlib import Path

import pytest

from common.pellets_schema import PelletDbSchema, PelletDbValidationError, validate_pellet_db

LIVE_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pelletdb_live.json"


@pytest.fixture
def live():
    return json.loads(LIVE_FIXTURE.read_text())


def test_the_live_database_validates_and_round_trips(live):
    """Captured from a running grill, not authored from the model -- a fixture
    written from the model only proves the model agrees with itself."""
    assert validate_pellet_db(copy.deepcopy(live)) == live


def test_est_usage_is_a_float(live):
    """defaults.py seeds int 0 and the field holds 171.198...; an int
    annotation would reject every grill that has ever cooked."""
    live["current"]["est_usage"] = 171.19809679985045
    assert validate_pellet_db(live)["current"]["est_usage"] == 171.19809679985045


def test_an_int_est_usage_is_accepted(live):
    """pellets_load_profile writes the literal 0."""
    live["current"]["est_usage"] = 0
    assert validate_pellet_db(live)["current"]["est_usage"] == 0


def test_a_deleted_log_value_is_accepted(live):
    """pellets_delete_profile rewrites each of the profile's log entries to the
    literal string rather than removing them, so log values are not keys of
    archive and must not be modeled as such."""
    live["log"]["2020-01-01 00:00:00"] = "deleted"
    assert validate_pellet_db(live)["log"]["2020-01-01 00:00:00"] == "deleted"


def test_a_brand_outside_the_vocabulary_is_accepted(live):
    """pellets_add_profile copies brand_name verbatim; brands is autocomplete,
    not an enumeration."""
    profile = next(iter(live["archive"].values()))
    profile["brand"] = "A Brand Nobody Listed"
    assert "A Brand Nobody Listed" not in live["brands"]
    assert validate_pellet_db(live)


def test_an_unbounded_rating_is_accepted_at_v1(live):
    """rating is action_data["rating"] with no bounds check at either writer.
    v1 records that; v2 is where it becomes 1..5."""
    next(iter(live["archive"].values()))["rating"] = 99
    assert validate_pellet_db(live)


def test_the_redundant_id_is_accepted_at_v1(live):
    profile_id, profile = next(iter(live["archive"].items()))
    assert profile["id"] == profile_id
    assert validate_pellet_db(live)


def test_a_loaded_profile_missing_from_the_archive_is_rejected(live):
    """The one invariant the code already enforces: pellets_delete_profile
    refuses to delete the loaded profile."""
    live["current"]["pelletid"] = "not-in-archive"
    with pytest.raises(PelletDbValidationError) as exc:
        validate_pellet_db(live)
    assert "archive" in str(exc.value)


def test_an_unmodeled_key_is_stripped_and_the_write_proceeds(live):
    """Self-healing repair, on the same terms as validate_settings_tree: an
    unmodeled key must never permanently block a save."""
    live["current"]["future_knob"] = 42
    live["totally_new_section"] = {"a": 1}

    repaired = validate_pellet_db(live)

    assert "future_knob" not in repaired["current"]
    assert "totally_new_section" not in repaired


def test_a_real_error_still_raises_even_beside_an_unmodeled_key(live):
    """Repair must never mask a genuine failure."""
    live["future_knob"] = 42
    live["current"]["hopper_level"] = "not a number"
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(live)


def test_raw_model_validate_rejects_an_unmodeled_key(live):
    """Self-healing lives ONLY in validate_pellet_db, exactly as it lives only
    in validate_settings_tree."""
    from pydantic import ValidationError

    live["current"]["future_knob"] = 42
    with pytest.raises(ValidationError):
        PelletDbSchema.model_validate(live, strict=True)
```

- [ ] **Step 4: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_schema.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common.pellets_schema'`.

- [ ] **Step 5: Write the model**

Create `common/pellets_schema.py`:

```python
"""Pydantic model for the pellet database blob (`pellets:general`).

Mirrors common/settings_schema.py deliberately -- one pattern in this codebase
rather than two. Unknown keys are REJECTED (`extra="forbid"` on
`_PelletSection`), and `validate_pellet_db()` wraps that strictness in the same
self-healing repair pass: when every failure is an unmodeled key, those keys
are stripped from a copy, each stripped path is logged, and validation is
retried once. Any other error still raises, so repair never masks a real bug.

The shape is taken from a LIVE install (tests/fixtures/pelletdb_live.json), not
from `default_pellets()`, because the two disagree: the defaults seed
`est_usage` as an int and the field holds a float accumulated from the auger
rate. Nothing here constrains a value beyond its type -- the writers in
common/pellets_actions.py do not, and a model stricter than the data in the
field turns a restore into a rejection.
"""

import copy

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from pydantic_core import ErrorDetails

from common.common import write_log


class _PelletSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PelletProfile(_PelletSection):
    # `id` repeats its own archive key. Nothing enforces the agreement.
    id: str
    brand: str
    wood: str
    rating: int
    comments: str


class PelletCurrent(_PelletSection):
    pelletid: str
    # An int percentage: every distance driver's get_level() returns one
    # (distance/_sampled_base.py takes int() of the computed level).
    hopper_level: int
    date_loaded: str
    # Grams since the last load, accumulated from the auger rate.
    est_usage: float


class PelletLastUpdated(_PelletSection):
    time: int


class PelletDbSchema(_PelletSection):
    current: PelletCurrent
    archive: dict[str, PelletProfile]
    # Keyed by load time, valued by profile id or the literal "deleted" --
    # pellets_delete_profile rewrites rather than removes, so a log value is
    # not a key of `archive`.
    log: dict[str, str]
    # Autocomplete vocabularies, not enumerations: a profile may name a brand
    # or wood that is absent from these.
    brands: list[str]
    woods: list[str]
    lastupdated: PelletLastUpdated

    @model_validator(mode="after")
    def _loaded_profile_is_archived(self) -> "PelletDbSchema":
        # pellets_delete_profile refuses to delete the loaded profile, so this
        # holds in the store; stating it here makes the guard and the schema
        # agree rather than the schema merely hoping.
        if self.current.pelletid not in self.archive:
            raise ValueError("current.pelletid must be a key of archive")
        return self


class PelletDbValidationError(ValueError):
    """A pellet database failed strict schema validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _format_errors(errs: list[ErrorDetails]) -> list[str]:
    return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in errs]


def _strip_error_locs(db: dict, errors: list[ErrorDetails]) -> None:
    """Delete each error's `loc` path from `db` in place. Caller passes a COPY."""
    for err in errors:
        loc = err["loc"]
        cur = db
        for part in loc[:-1]:
            cur = cur.get(part) if isinstance(cur, dict) else None
            if not isinstance(cur, dict):
                break
        if isinstance(cur, dict):
            cur.pop(loc[-1], None)


def validate_pellet_db(pelletdb: dict) -> dict:
    """Strict-validate a pellet database; return the normalized dump.

    The single enforcement entry -- write_pellet_db() calls it before
    persisting. Raises PelletDbValidationError with dotted-path messages.
    """
    try:
        model = PelletDbSchema.model_validate(pelletdb, strict=True)
    except ValidationError as exc:
        errors = exc.errors()
        if not errors or any(err["type"] != "extra_forbidden" for err in errors):
            raise PelletDbValidationError(_format_errors(errors)) from exc

        repaired = copy.deepcopy(pelletdb)
        _strip_error_locs(repaired, errors)
        try:
            model = PelletDbSchema.model_validate(repaired, strict=True)
        except ValidationError as retry_exc:
            raise PelletDbValidationError(_format_errors(retry_exc.errors())) from retry_exc

        for err in errors:
            dotted = ".".join(str(part) for part in err["loc"])
            write_log(f"pelletdb: stripped unmodeled key '{dotted}' during write-time repair (was: {err['msg']})")
    return model.model_dump(mode="json")
```

- [ ] **Step 6: Run the tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_schema.py -v
```

Expected: PASS, 11 tests. If `test_the_live_database_validates_and_round_trips` fails, the live install carries a shape this model does not describe — read the error, widen the MODEL, and record what was found. Do not edit the fixture.

- [ ] **Step 7: Full suite, format, commit**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
.venv/bin/ruff format common/pellets_schema.py tests/unit/common/test_pellets_schema.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(pellets): model the pellet database, exactly as a live grill holds it

Taken off a running install rather than off default_pellets(), which seeds
est_usage as an int where the field holds a float.
EOF
```

---

### Task 6: The gate, and the 400 the restore route never had

The same admin route validates a settings backup and 400s a bad one, then in its next branch writes whatever JSON a pellet file contained straight into the live store.

**Files:**
- Modify: `common/datastore_accessors.py:537-543` (`write_pellet_db`)
- Modify: `common/backups.py:63-105` (`read_pellet_db_file`)
- Modify: `blueprints/api_admin/routes.py:336`
- Modify: `tests/web/test_api_admin_backups.py` (append, below `test_restoring_the_pellet_database_does_not_restart`)
- Modify: `tests/unit/common/test_pellets_schema.py` (append)

**Interfaces:**
- Consumes: `validate_pellet_db`, `PelletDbValidationError` (Task 5).

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

The restore tests belong beside the ones already covering that route, and reuse its `env` fixture — which redirects **both** `BACKUP_PATH` references (the blueprint's `current_app.config` one and `common/backups.py`'s module constant) and neutralizes `restart_scripts`. Building a second fixture here would get one of those wrong. Append to `tests/web/test_api_admin_backups.py`:

```python
def test_a_malformed_pellet_backup_is_refused_and_the_store_is_untouched(env):
    """The settings branch of this route validates and refuses a bad backup
    with a 400. The pellet branch wrote whatever JSON the file held straight
    into the live store, and the same UI is what let the operator upload it."""
    from common.datastore_accessors import read_pellets_store

    before = read_pellets_store()
    with open(os.path.join(env["dir"], "PelletDB_01-01-26_130000.json"), "w", encoding="utf-8") as h:
        json.dump({"current": {"hopper_level": "not a number"}}, h)

    resp = env["client"].post(
        "/api/admin/backups/restore",
        json={"kind": "pelletdb", "file": "PelletDB_01-01-26_130000.json"},
    )

    assert resp.status_code == 400
    assert resp.get_json()["message"] == "invalid_backup"
    assert read_pellets_store() == before
    assert env["calls"] == []


def test_a_well_formed_pellet_backup_still_restores(env):
    from common.datastore_accessors import read_pellets_store
    from common.defaults import default_pellets

    payload = default_pellets()
    payload["brands"] = ["Generic", "Custom", "Restored Brand"]
    with open(os.path.join(env["dir"], "PelletDB_01-01-26_140000.json"), "w", encoding="utf-8") as h:
        json.dump(payload, h)

    resp = env["client"].post(
        "/api/admin/backups/restore",
        json={"kind": "pelletdb", "file": "PelletDB_01-01-26_140000.json"},
    )

    assert resp.status_code == 200
    assert "Restored Brand" in read_pellets_store()["brands"]
```

Append to `tests/unit/common/test_pellets_schema.py`:

```python
def test_the_control_process_write_path_accepts_a_live_database(ds, live):
    """controller/runtime/store.py calls write_pellet_db every 60s and at each
    mode end, updating exactly these two fields. The gate raises, so a shape it
    rejected would take down the control loop rather than a request."""
    from common.datastore_accessors import read_pellets_store, write_pellet_db

    write_pellet_db(live)

    stored = read_pellets_store()
    stored["current"]["est_usage"] = stored["current"]["est_usage"] + 12.5
    stored["current"]["hopper_level"] = 87
    write_pellet_db(stored)

    assert read_pellets_store()["current"]["hopper_level"] == 87
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_admin_backups.py tests/unit/common/test_pellets_schema.py -v
```

Expected: `test_a_malformed_pellet_backup_is_refused_and_the_store_is_untouched` FAILS with `assert 200 == 400`. The other two pass — that is the point: the hole is specifically the malformed case. (`read_pellet_db_file` overlays the file's top-level keys onto `default_pellets()`, so the payload above lands as a `current` section missing `pelletid`, `date_loaded` and `est_usage` — a genuine validation failure rather than a parse error.)

- [ ] **Step 4: Wire the gate into the writer**

In `common/datastore_accessors.py`, add to the imports from `common.pellets_schema`:

```python
from common.pellets_schema import validate_pellet_db
```

and replace `write_pellet_db`'s body:

```python
def write_pellet_db(pelletdb):
    """
    Write Pellet DataBase to SQLite DB (source of truth at runtime).

    Strict-validates first, exactly as write_settings() does: a rejected write
    leaves the store untouched, and the normalized dump the gate returns -- not
    the caller's raw dict -- is what gets persisted. No bypass parameter.

    :param pelletdb: Pellet Database
    """
    write_pellets_store(validate_pellet_db(pelletdb))
```

- [ ] **Step 5: Answer 400 at the route**

In `blueprints/api_admin/routes.py`, add `PelletDbValidationError` to the imports and replace line 336:

```python
    else:
        try:
            write_pellet_db(read_pellet_db_file(filename=path))
        except PelletDbValidationError as exc:
            return error("invalid_backup", 400, detail="; ".join(exc.errors))
```

Then extend the route docstring's third paragraph, which currently speaks only of settings, so it covers both branches:

```
    A backup that fails strict validation -- hand-edited, or from a build old
    enough that migration cannot fully repair it -- is rejected with the same
    error envelope every other write endpoint uses. Both writers validate
    before persisting, so a rejection leaves the store untouched and, for
    settings, never restarts.
```

- [ ] **Step 6: Let the file reader carry a scalar**

`read_pellet_db_file()` overlays the file's top-level keys onto `default_pellets()` with `pelletdb_struct[key].copy()`. Task 7 adds an integer `schema_version` to the defaults, and `int` has no `.copy()`. Replace the overlay loop:

```python
    # Overlay the read values over the top of the default values
    #  This ensures that any NEW fields are captured.
    for key in pelletdb.keys():
        if key in pelletdb_struct.keys():
            value = pelletdb_struct[key]
            pelletdb[key] = value.copy() if hasattr(value, "copy") else value
```

- [ ] **Step 7: Run the tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/web/test_api_admin_backups.py tests/unit/common/test_pellets_schema.py \
  tests/unit/bootstrap/test_startup_migration.py -v
```

Expected: all PASS.

- [ ] **Step 8: The negative control (required)**

Comment out `validate_pellet_db(...)` in `write_pellet_db` (make it `write_pellets_store(pelletdb)`), then:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  "tests/web/test_api_admin_backups.py::test_a_malformed_pellet_backup_is_refused_and_the_store_is_untouched" -v
```

Expected: **FAIL**. Without this, the test could pass on the model alone and prove nothing about the route. Restore the call and re-run to confirm green.

- [ ] **Step 9: Full suite**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Every existing pellet writer now goes through the gate. Pay attention to `tests/web/test_socketio_app_data.py`, `tests/characterization/test_all_writers_strict.py` and `tests/e2e/test_work_cycle_e2e.py` — a failure there is a writer producing a shape the model does not describe, and the model is what should move (back to Task 5's fixture question), not the gate.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format common/datastore_accessors.py common/backups.py blueprints/api_admin/routes.py tests/web/test_api_admin_backups.py tests/unit/common/test_pellets_schema.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(pellets): validate every pellet-database write, and 400 a malformed restore

The restore route validated a settings backup and refused a bad one, then in
its next branch wrote whatever JSON a pellet file held into the live store.
EOF
```

> **Chromium note:** `tests/web/test_api_admin_backups.py` uses the Flask test client, not Playwright, so these two cases run everywhere. The `[chromium]`-marked files in `tests/web/` that this task's Step 9 exercises must still be re-run in the main checkout before merge.

---

### Task 7: `PELLETDB_SCHEMA_VERSION`, the stamp, and the pellet digest gate

The machinery lands before it is needed, so Slice C is migrations and nothing else.

**Files:**
- Modify: `common/pellets_schema.py`
- Modify: `common/defaults.py` (`default_pellets`)
- Modify: `common/datastore.py` (`init`, plus the new `_upgrade_pellets_in_store`)
- Create: `tests/unit/common/test_pellets_shape_digest.py`
- Create: `tests/unit/datastore/test_pellets_shape_migration.py`

**Interfaces:**
- Consumes: `shape_digest` (Task 1), `PelletDbSchema` (Task 5).
- Produces: `PELLETDB_SCHEMA_VERSION: int` (value `1`), `_PELLET_MIGRATIONS: list[tuple[int, Callable[[dict], bool]]]` (empty at v1), `datastore._upgrade_pellets_in_store()`. Task 9 appends to `_PELLET_MIGRATIONS`.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/datastore/test_pellets_shape_migration.py`:

```python
"""The pellet database's own stamp, on the same terms as the settings tree's.

Two versions and two digests, one per blob, because the whole point is that
each shape moves on its own schedule -- coupling them would mean bumping one
to migrate the other.
"""

import copy
import json
from pathlib import Path

import pytest

from common import datastore, pellets_schema
from common.datastore_accessors import read_pellets_store, write_pellets_store
from common.defaults import default_pellets
from common.pellets_schema import PELLETDB_SCHEMA_VERSION

LIVE_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pelletdb_live.json"


def _unstamped_live_db():
    db = json.loads(LIVE_FIXTURE.read_text())
    db.pop("schema_version", None)
    write_pellets_store(db)
    return db


def test_a_fresh_database_is_stamped_current():
    assert default_pellets()["schema_version"] == PELLETDB_SCHEMA_VERSION


def test_an_unstamped_database_ends_stamped_current(ds):
    _unstamped_live_db()

    datastore._upgrade_pellets_in_store()

    assert read_pellets_store()["schema_version"] == PELLETDB_SCHEMA_VERSION


def test_a_current_database_runs_no_step(ds, monkeypatch):
    ran = []
    monkeypatch.setattr(pellets_schema, "_PELLET_MIGRATIONS", [(1, lambda db: ran.append(1) or False)])
    write_pellets_store(copy.deepcopy(default_pellets()))

    datastore._upgrade_pellets_in_store()

    assert ran == []


def test_a_stamp_from_the_future_runs_nothing_and_is_not_rewound(ds, monkeypatch):
    ran = []
    monkeypatch.setattr(pellets_schema, "_PELLET_MIGRATIONS", [(1, lambda db: ran.append(1) or True)])
    db = copy.deepcopy(default_pellets())
    db["schema_version"] = PELLETDB_SCHEMA_VERSION + 5
    write_pellets_store(db)

    datastore._upgrade_pellets_in_store()

    assert ran == []
    assert read_pellets_store()["schema_version"] == PELLETDB_SCHEMA_VERSION + 5


def test_the_stamp_is_not_written_when_a_step_raises(ds, monkeypatch):
    def _explode(db):
        raise RuntimeError("migration blew up")

    monkeypatch.setattr(pellets_schema, "_PELLET_MIGRATIONS", [(1, _explode)])
    _unstamped_live_db()

    with pytest.raises(RuntimeError):
        datastore._upgrade_pellets_in_store()

    assert "schema_version" not in read_pellets_store()


def test_running_the_chain_twice_is_identical_to_running_it_once(ds):
    _unstamped_live_db()

    datastore._upgrade_pellets_in_store()
    once = copy.deepcopy(read_pellets_store())
    datastore._upgrade_pellets_in_store()

    assert read_pellets_store() == once


def test_init_stamps_a_pre_stamp_store(ds):
    _unstamped_live_db()

    datastore.init()

    assert read_pellets_store()["schema_version"] == PELLETDB_SCHEMA_VERSION
```

Create `tests/unit/common/test_pellets_shape_digest.py`:

```python
"""The pellet database's shape gate -- see test_settings_shape_digest.py for
why this exists at all."""

from common.pellets_schema import PelletDbSchema
from common.schema_digest import shape_digest

#: Regenerate with:
#:   uv run python -m common.schema_digest common.pellets_schema:PelletDbSchema
#: Step 5 of this task replaces the value below with what that command prints.
PELLETDB_SHAPE_DIGEST = "cde812b19cb56958b428b8044895db86bd5660d1eaacc036096dca2838046972"

_MESSAGE = """The modeled pellet-database shape changed. Two things to do:

  1. Bump PELLETDB_SCHEMA_VERSION in common/pellets_schema.py, and add a step
     to _PELLET_MIGRATIONS in the same module. A change that moves no data
     still gets a no-op step -- the step list is the record.
  2. Update PELLETDB_SHAPE_DIGEST below, from:
     uv run python -m common.schema_digest common.pellets_schema:PelletDbSchema
"""


def test_shape_change_requires_a_schema_version_bump():
    assert shape_digest(PelletDbSchema) == PELLETDB_SHAPE_DIGEST, _MESSAGE
```

- [ ] **Step 3: Run them to verify they fail**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_pellets_shape_migration.py -v
```

Expected: FAIL — `cannot import name 'PELLETDB_SCHEMA_VERSION'`.

- [ ] **Step 4: Add the version, the field and the registry**

In `common/pellets_schema.py`, above `class PelletDbSchema`:

```python
#: The shape of the pellet database, independent of both the release version
#: and the settings tree's shape version. Different shapes, different migration
#: histories: coupling them would mean bumping one to migrate the other.
PELLETDB_SCHEMA_VERSION = 1
```

as the first field of `PelletDbSchema`:

```python
    schema_version: int = PELLETDB_SCHEMA_VERSION
```

and below the class:

```python
#: The shape migrations, in ascending order, as (target_version, migration).
#: A step's number is the version the database is AT once it has run; each
#: callable mutates in place and returns True if it changed anything. Empty at
#: version 1, which is today's shape modeled exactly.
_PELLET_MIGRATIONS: list = []
```

In `common/defaults.py`, add to `default_pellets()` immediately after `pelletdb = {}`:

```python
    pelletdb["schema_version"] = PELLETDB_SCHEMA_VERSION
```

and extend the module's import of `common.settings_schema` with a second line:

```python
from common.pellets_schema import PELLETDB_SCHEMA_VERSION
```

- [ ] **Step 5: Compute and paste the pellet digest**

```bash
uv run python -m common.schema_digest common.pellets_schema:PelletDbSchema | tail -1
```

Expected: `855dedd289228e40935ddc8bb3b39260e506851779081c240a8fdec542f26696`. Paste it into `PELLETDB_SHAPE_DIGEST`, replacing the pre-stamp value that is committed there. If the command prints the pre-stamp value unchanged, the field was not added to the model.

- [ ] **Step 6: Add the store upgrade**

In `common/datastore.py`, immediately below `_upgrade_settings_in_store`:

```python
def _upgrade_pellets_in_store():
    """Bring the stored pellet database up to the current shape, and stamp it.

    Mirrors _upgrade_settings_in_store: read and write inside one
    BEGIN IMMEDIATE, steps gated on the blob's own stamp, and the stamp written
    last so a crash mid-chain retries the whole chain rather than leaving a
    version ahead of its data.
    """
    import json

    from common.common import write_log
    from common.pellets_schema import PELLETDB_SCHEMA_VERSION, _PELLET_MIGRATIONS

    with transaction() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key='pellets:general'").fetchone()
        if row is None:
            return
        pelletdb = json.loads(row[0])
        changed = False

        stamp = pelletdb.get("schema_version", 0)
        if stamp > PELLETDB_SCHEMA_VERSION:
            write_log(
                f"Pellet database shape version {stamp} is newer than this build's "
                f"{PELLETDB_SCHEMA_VERSION}; no shape migration was run."
            )
            return

        for target, migrate in _PELLET_MIGRATIONS:
            if stamp < target and migrate(pelletdb):
                changed = True
        if stamp != PELLETDB_SCHEMA_VERSION:
            pelletdb["schema_version"] = PELLETDB_SCHEMA_VERSION
            changed = True

        if not changed:
            return

        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("pellets:general", json.dumps(pelletdb)),
        )
```

and in `init()`, after `_upgrade_settings_in_store()`:

```python
    _upgrade_pellets_in_store()
```

- [ ] **Step 7: Run the tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/datastore/test_pellets_shape_migration.py \
  tests/unit/common/test_pellets_shape_digest.py \
  tests/unit/common/test_pellets_schema.py -v
```

Expected: all PASS. `test_the_live_database_validates_and_round_trips` still passes because `schema_version` has a default — the fixture has no stamp and the model supplies one, and the dump then differs from the fixture. **If that test now fails on the added key, change it to compare against `{**live, "schema_version": PELLETDB_SCHEMA_VERSION}` and say so in the commit message.**

- [ ] **Step 8: Full suite, format, commit**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
.venv/bin/ruff format common/pellets_schema.py common/defaults.py common/datastore.py tests/unit/datastore/test_pellets_shape_migration.py tests/unit/common/test_pellets_shape_digest.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(pellets): stamp the pellet database with the shape it was written against

Its own integer and its own digest, independent of the settings tree's: two
shapes with two migration histories.
EOF
```

---

# Slice C — the pellet database, version 2

The sane shape. Each change is a migration, and each is only possible because the writers change with it.

### Task 8: The v2 model

**Files:**
- Modify: `common/pellets_schema.py`
- Modify: `tests/unit/common/test_pellets_shape_digest.py` (the constant)
- Modify: `tests/unit/common/test_pellets_schema.py` (the traps become migration inputs)

**Interfaces:**
- Produces: `PelletLogEntry` (`pelletid: str | None`, `deleted: bool`), `PelletProfile` without `id`, `rating: int = Field(ge=1, le=5)`, `log: dict[_EpochMsKey, PelletLogEntry]`, `PELLETDB_SCHEMA_VERSION = 2`.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

**Rewrite `tests/unit/common/test_pellets_schema.py` in full**, replacing every use of the `live` fixture with a v2-shaped builder. The v1 acceptance tests were true statements about version 1 and are false about version 2 — that is what a version is for — and the live fixture's only remaining consumer becomes `test_pellets_migration_v2.py` (Task 9), where it is the migration's INPUT. Expect this task's diff to delete roughly as much as it adds; that is not churn, it is the version boundary.

The whole new file:

```python
"""Version 2 of the pellet database: the sane shape.

The writers are in scope, so the shape is allowed to be sane. The split is by
TIME, not by strictness -- new writes are validated strictly here, and data an
install already holds is migrated rather than rejected (see
test_pellets_migration_v2.py, which is where the live v1 fixture went).
"""

import copy

import pytest

from common.pellets_schema import PelletDbSchema, PelletDbValidationError, validate_pellet_db


def _v2_db():
    """A v2-shaped database. The live fixture is v1; Task 9's migration is what
    carries one to the other, and these tests are about the SHAPE only."""
    return {
        "schema_version": 2,
        "current": {
            "pelletid": "p1",
            "hopper_level": 100,
            "date_loaded": "2026-07-11 09:03:26",
            "est_usage": 171.19809679985045,
        },
        "archive": {"p1": {"brand": "Generic", "wood": "Alder", "rating": 4, "comments": "c"}},
        "log": {"1783775006000": {"pelletid": "p1", "deleted": False}},
        "brands": ["Generic", "Custom"],
        "woods": ["Alder"],
        "lastupdated": {"time": 1783775006},
    }


def test_the_v2_shape_validates_and_round_trips():
    db = _v2_db()
    assert validate_pellet_db(copy.deepcopy(db)) == db


def test_a_rating_out_of_range_is_rejected():
    db = _v2_db()
    db["archive"]["p1"]["rating"] = 99
    with pytest.raises(PelletDbValidationError) as exc:
        validate_pellet_db(db)
    assert "rating" in str(exc.value)


def test_a_redundant_id_is_not_a_modeled_field():
    """It repeated its own dict key and nothing enforced the agreement. An
    unmodeled key is stripped by the repair pass, not rejected."""
    db = _v2_db()
    db["archive"]["p1"]["id"] = "p1"
    assert "id" not in validate_pellet_db(db)["archive"]["p1"]


def test_a_tombstone_log_entry_validates():
    db = _v2_db()
    db["log"]["1783775007000"] = {"pelletid": None, "deleted": True}
    assert validate_pellet_db(db)["log"]["1783775007000"]["deleted"] is True


def test_a_non_numeric_log_key_is_rejected():
    """The key format is enforced rather than hoped: a second-resolution
    timestamp string is exactly what v2 exists to stop storing."""
    db = _v2_db()
    db["log"]["2026-07-11 09:03:26"] = {"pelletid": "p1", "deleted": False}
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(db)


def test_the_in_band_deleted_sentinel_is_rejected():
    db = _v2_db()
    db["log"]["1783775008000"] = "deleted"
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(db)


def test_a_brand_outside_the_vocabulary_is_still_accepted():
    """The one item in 5.3 that is a feature: an operator naming a bag the
    vocabulary has not heard of is the normal case, not an error."""
    db = _v2_db()
    db["archive"]["p1"]["brand"] = "A Brand Nobody Listed"
    assert validate_pellet_db(db)


def test_est_usage_is_still_a_float():
    db = _v2_db()
    db["current"]["est_usage"] = 0
    assert validate_pellet_db(db)["current"]["est_usage"] == 0


def test_a_loaded_profile_missing_from_the_archive_is_rejected():
    """Rejected at BOTH versions. pellets_delete_profile refuses to delete the
    loaded profile, so this is the one invariant the code already enforces."""
    db = _v2_db()
    db["current"]["pelletid"] = "not-in-archive"
    with pytest.raises(PelletDbValidationError) as exc:
        validate_pellet_db(db)
    assert "archive" in str(exc.value)


def test_an_unmodeled_key_is_stripped_and_the_write_proceeds():
    """Self-healing repair, on the same terms as validate_settings_tree: an
    unmodeled key must never permanently block a save."""
    db = _v2_db()
    db["current"]["future_knob"] = 42
    db["totally_new_section"] = {"a": 1}

    repaired = validate_pellet_db(db)

    assert "future_knob" not in repaired["current"]
    assert "totally_new_section" not in repaired


def test_a_real_error_still_raises_even_beside_an_unmodeled_key():
    """Repair must never mask a genuine failure."""
    db = _v2_db()
    db["future_knob"] = 42
    db["current"]["hopper_level"] = "not a number"
    with pytest.raises(PelletDbValidationError):
        validate_pellet_db(db)


def test_raw_model_validate_rejects_an_unmodeled_key():
    """Self-healing lives ONLY in validate_pellet_db, exactly as it lives only
    in validate_settings_tree."""
    from pydantic import ValidationError

    db = _v2_db()
    db["current"]["future_knob"] = 42
    with pytest.raises(ValidationError):
        PelletDbSchema.model_validate(db, strict=True)


def test_the_control_process_write_path_accepts_a_stored_database(ds):
    """controller/runtime/store.py calls write_pellet_db every 60s and at each
    mode end, updating exactly these two fields. The gate raises, so a shape it
    rejected would take down the control loop rather than a request."""
    from common.datastore_accessors import read_pellets_store, write_pellet_db

    write_pellet_db(_v2_db())

    stored = read_pellets_store()
    stored["current"]["est_usage"] = stored["current"]["est_usage"] + 12.5
    stored["current"]["hopper_level"] = 87
    write_pellet_db(stored)

    assert read_pellets_store()["current"]["hopper_level"] == 87
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_schema.py -v
```

Expected: FAILS against the v1 model — `test_the_v2_shape_validates_and_round_trips` and `test_a_tombstone_log_entry_validates` because a dict is not a `str` log value, `test_a_rating_out_of_range_is_rejected` / `test_a_non_numeric_log_key_is_rejected` / `test_the_in_band_deleted_sentinel_is_rejected` because v1 has no such constraint, and `test_a_redundant_id_is_not_a_modeled_field` because v1 models `id`. If any of those five PASSES here, the test is not asserting what it claims.

- [ ] **Step 4: Move the model to v2**

In `common/pellets_schema.py`:

```python
from typing import Annotated

from pydantic import Field, StringConstraints
```

```python
#: A log key is the load time in epoch milliseconds, as a decimal string --
#: JSON object keys are strings, and the pattern is what keeps a
#: second-resolution timestamp from being stored under a key that says
#: milliseconds.
_EpochMsKey = Annotated[str, StringConstraints(pattern=r"^\d+$")]


class PelletLogEntry(_PelletSection):
    # A removed profile leaves a tombstone rather than an in-band "deleted"
    # string, so a log value has one type whatever happened to it.
    pelletid: str | None
    deleted: bool


class PelletProfile(_PelletSection):
    brand: str
    wood: str
    rating: int = Field(ge=1, le=5)
    comments: str
```

and on `PelletDbSchema`:

```python
    log: dict[_EpochMsKey, PelletLogEntry]
```

Update the comment above `log` to describe the tombstone rather than the sentinel, and bump:

```python
PELLETDB_SCHEMA_VERSION = 2
```

`PelletCurrent`, `PelletLastUpdated`, `brands`, `woods` and the `_loaded_profile_is_archived` validator are unchanged.

- [ ] **Step 5: Watch the digest gate fire, then update it**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_shape_digest.py -v
uv run python -m common.schema_digest common.pellets_schema:PelletDbSchema | tail -1
```

Expected: FAIL, then `59fcceaa5042afd8bd36d22b9849526a2aca5de802d5f94550656a8e5e1a21c9`. Paste it into `PELLETDB_SHAPE_DIGEST`. A different value means the model diverged from Step 4 — run the command without `| tail -1` and read the entry list, which names every path it disagrees about.

This is the worked example the digest was built for. Confirm it by eye: of the four v2 changes, only "`id` dropped" removes a path. The bounded `rating`, the rekeyed `log` and the object-valued `log` are all invisible to a paths-only digest, and all three rewrite data.

- [ ] **Step 6: Run the model tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_schema.py tests/unit/common/test_pellets_shape_digest.py -v
```

Expected: PASS. The rest of the suite is expected to be RED after this task — every writer still produces v1 — and Tasks 9 and 10 are what make it green. Do not chase those failures here.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format common/pellets_schema.py tests/unit/common/test_pellets_schema.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(pellets): the version 2 shape -- bounded rating, no redundant id, epoch-ms log

Log values become objects, so a removed profile leaves a tombstone instead of
an in-band string.
EOF
```

> The tree is intentionally red between Tasks 8 and 10. If a slice boundary is needed here, squash 8, 9 and 10 into one commit rather than pushing 8 alone.

### Task 9: The v1 → v2 migrations

**Files:**
- Modify: `common/pellets_schema.py`
- Create: `tests/unit/common/test_pellets_migration_v2.py`

**Interfaces:**
- Consumes: `_PELLET_MIGRATIONS` (Task 7), the v2 model (Task 8).
- Produces: `_migrate_pellets_to_v2(pelletdb: dict) -> bool`.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/common/test_pellets_migration_v2.py`:

```python
"""One test per v2 migration, each asserting the v1 input and the v2 output.

Existing data is never rejected for a legacy value -- that is the whole split
this version exists to express: strict for new writes, migrated for what is
already stored.
"""

import copy
import json
from pathlib import Path

from common.pellets_schema import PELLETDB_SCHEMA_VERSION, _migrate_pellets_to_v2, validate_pellet_db

LIVE_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pelletdb_live.json"


def _v1_db():
    """The live v1 database, with the traps of spec 5.3 present."""
    db = json.loads(LIVE_FIXTURE.read_text())
    db.pop("schema_version", None)
    profile_id = next(iter(db["archive"]))
    db["archive"]["gone"] = {"id": "gone", "brand": "Custom", "wood": "Oak", "rating": 4, "comments": ""}
    db["log"]["2026-07-12 10:00:00"] = "deleted"
    db["log"]["2026-07-13 11:30:45"] = profile_id
    return db


def test_a_migrated_database_validates_at_v2():
    db = _v1_db()
    assert _migrate_pellets_to_v2(db) is True
    assert validate_pellet_db(db)


def test_rating_is_coerced_and_clamped():
    db = _v1_db()
    db["archive"]["gone"]["rating"] = "4"
    profile_id = next(iter(db["archive"]))
    db["archive"][profile_id]["rating"] = 99

    _migrate_pellets_to_v2(db)

    assert db["archive"]["gone"]["rating"] == 4
    assert db["archive"][profile_id]["rating"] == 5


def test_a_rating_below_range_is_clamped_up():
    db = _v1_db()
    db["archive"]["gone"]["rating"] = 0

    _migrate_pellets_to_v2(db)

    assert db["archive"]["gone"]["rating"] == 1


def test_the_redundant_id_is_dropped():
    db = _v1_db()

    _migrate_pellets_to_v2(db)

    assert all("id" not in profile for profile in db["archive"].values())


def test_a_mismatched_id_is_logged_rather_than_silently_resolved(monkeypatch):
    logged = []
    monkeypatch.setattr("common.pellets_schema.write_log", logged.append)
    db = _v1_db()
    db["archive"]["gone"]["id"] = "something-else"

    _migrate_pellets_to_v2(db)

    assert any("something-else" in line for line in logged)


def test_log_keys_become_epoch_milliseconds():
    db = _v1_db()
    original = sorted(db["log"])

    _migrate_pellets_to_v2(db)

    assert len(db["log"]) == len(original)
    assert all(key.isdigit() for key in db["log"])


def test_log_keys_keep_their_order():
    db = _v1_db()
    expected = [db["log"][key] for key in sorted(db["log"])]

    _migrate_pellets_to_v2(db)

    migrated = [db["log"][key] for key in sorted(db["log"], key=int)]
    assert [entry["pelletid"] or "deleted" for entry in migrated] == expected


def test_the_deleted_sentinel_becomes_a_tombstone():
    db = _v1_db()

    _migrate_pellets_to_v2(db)

    entries = list(db["log"].values())
    assert {"pelletid": None, "deleted": True} in entries
    assert any(entry["deleted"] is False and entry["pelletid"] for entry in entries)


def test_running_the_migration_twice_is_identical_to_running_it_once():
    db = _v1_db()

    _migrate_pellets_to_v2(db)
    once = copy.deepcopy(db)
    _migrate_pellets_to_v2(db)

    assert db == once


def test_the_store_upgrade_carries_a_v1_database_to_v2(ds):
    from common import datastore
    from common.datastore_accessors import read_pellets_store, write_pellets_store

    write_pellets_store(_v1_db())

    datastore._upgrade_pellets_in_store()

    stored = read_pellets_store()
    assert stored["schema_version"] == PELLETDB_SCHEMA_VERSION
    assert all(key.isdigit() for key in stored["log"])
    assert validate_pellet_db(stored)
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_migration_v2.py -v
```

Expected: FAIL — `cannot import name '_migrate_pellets_to_v2'`.

- [ ] **Step 4: Write the migration**

In `common/pellets_schema.py`, above `_PELLET_MIGRATIONS`:

```python
def _migrate_pellets_to_v2(pelletdb: dict) -> bool:
    """Carry a version 1 database to version 2. Idempotent.

    Four changes: `rating` is coerced and clamped into 1..5, the redundant
    per-profile `id` is dropped, log keys become epoch milliseconds, and log
    values become objects rather than an id-or-"deleted" string.
    """
    changed = False

    for key, profile in (pelletdb.get("archive") or {}).items():
        if not isinstance(profile, dict):
            continue
        if "id" in profile:
            stored_id = profile.pop("id")
            if stored_id != key:
                write_log(f"pelletdb: archive['{key}'] carried id '{stored_id}'; the key wins")
            changed = True
        raw = profile.get("rating")
        try:
            rating = int(raw)
        except (TypeError, ValueError):
            rating = 1
        rating = max(1, min(5, rating))
        if rating != raw:
            write_log(f"pelletdb: archive['{key}'].rating {raw!r} -> {rating}")
            profile["rating"] = rating
            changed = True

    log = pelletdb.get("log")
    if isinstance(log, dict) and any(not str(key).isdigit() for key in log):
        migrated: dict[str, dict] = {}
        # Sorted, because "%Y-%m-%d %H:%M:%S" sorts chronologically as text and
        # the collision rule below has to advance the LATER of two entries.
        for key in sorted(log):
            try:
                stamp = int(datetime.strptime(key, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
            except (TypeError, ValueError):
                write_log(f"pelletdb: dropped log entry with unreadable timestamp '{key}'")
                continue
            while str(stamp) in migrated:
                stamp += 1
            migrated[str(stamp)] = log[key]
        log = migrated
        pelletdb["log"] = log
        changed = True

    for key, value in list((pelletdb.get("log") or {}).items()):
        if isinstance(value, str):
            pelletdb["log"][key] = (
                {"pelletid": None, "deleted": True} if value == "deleted" else {"pelletid": value, "deleted": False}
            )
            changed = True

    return changed
```

Add `from datetime import datetime` to the module imports, and register the step:

```python
_PELLET_MIGRATIONS = [
    (2, _migrate_pellets_to_v2),
]
```

> **Local zone, deliberately.** `datetime.strptime` produces a naive datetime and `.timestamp()` reads it in the system's local zone — which is the zone `str(datetime.now())` wrote it in. Anything else would shift every operator's log by their UTC offset.

- [ ] **Step 5: Run the migration tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_migration_v2.py -v
```

Expected: PASS, 10 tests.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format common/pellets_schema.py tests/unit/common/test_pellets_migration_v2.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(pellets): carry a version 1 database to version 2

Log timestamps are reparsed in the local zone they were written in, which is
the only reading that does not shift an operator's log by their UTC offset.
EOF
```

---

### Task 10: The writers, and the collision bug they were hiding

The log dict is keyed by `str(datetime.now())[0:19]` — local time, second resolution. Two loads within the same second collide on the dict key and one entry is silently lost. Both writers do it.

**Files:**
- Modify: `common/pellets_actions.py`
- Modify: `common/defaults.py` (`default_pellets`)
- Create: `tests/unit/common/test_pellets_writers_v2.py`
- Modify: `tests/web/test_socketio_app_data.py` (the log-shape assertions)
- Modify: `tests/web/test_api_admin_maintenance.py:138`, `tests/web/test_api_admin_system.py:172`

**Interfaces:**
- Consumes: the v2 model (Task 8).
- Produces: `_log_key(log: dict) -> str` in `common/pellets_actions.py`.

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/common/test_pellets_writers_v2.py`:

```python
"""What the writers must produce now that the shape is enforced at the door.

The same-second test is the regression test for a bug the modeling surfaced:
the log dict was keyed at second resolution, so two loads inside one second
collided on the key and one entry was silently lost.
"""

import pytest

from common import pellets_actions
from common.datastore_accessors import read_pellets_store, write_pellets_store
from common.defaults import default_pellets


@pytest.fixture
def db(ds, monkeypatch):
    monkeypatch.setattr(pellets_actions, "write_control", lambda *a, **k: None)
    monkeypatch.setattr(pellets_actions, "backup_pellet_db", lambda *a, **k: None)
    pelletdb = default_pellets()
    write_pellets_store(pelletdb)
    return pelletdb


def _frozen_clock(monkeypatch, millis):
    """Pin the clock so both writes land in the same millisecond -- the
    strongest form of the collision, and one that does not depend on how fast
    this machine happens to be."""
    monkeypatch.setattr(pellets_actions.time, "time", lambda: millis / 1000)


def test_two_loads_in_the_same_millisecond_are_both_recorded(db, monkeypatch):
    _frozen_clock(monkeypatch, 1783775006000)
    profile_id = next(iter(db["archive"]))
    before = len(db["log"])

    pellets_actions.pellets_load_profile(read_pellets_store(), {"profile": profile_id})
    pellets_actions.pellets_load_profile(read_pellets_store(), {"profile": profile_id})

    log = read_pellets_store()["log"]
    # COUNT first: a key-format assertion would fail for the wrong reason if
    # the entry were dropped.
    assert len(log) == before + 2
    assert sorted(log)[-2:] == ["1783775006000", "1783775006001"]


def test_a_load_writes_a_tombstone_shaped_entry(db, monkeypatch):
    _frozen_clock(monkeypatch, 1783775006000)
    profile_id = next(iter(db["archive"]))

    pellets_actions.pellets_load_profile(read_pellets_store(), {"profile": profile_id})

    assert read_pellets_store()["log"]["1783775006000"] == {"pelletid": profile_id, "deleted": False}


def test_deleting_a_profile_writes_a_tombstone(db):
    profile_id = next(iter(db["archive"]))
    pelletdb = read_pellets_store()
    pelletdb["archive"]["other"] = {"brand": "Custom", "wood": "Oak", "rating": 3, "comments": ""}
    pelletdb["log"]["1783775009000"] = {"pelletid": "other", "deleted": False}
    write_pellets_store(pelletdb)

    pellets_actions.pellets_delete_profile(read_pellets_store(), {"profile": "other"})

    assert read_pellets_store()["log"]["1783775009000"] == {"pelletid": None, "deleted": True}
    assert profile_id in read_pellets_store()["archive"]


def test_adding_a_profile_stores_no_redundant_id(db):
    pellets_actions.pellets_add_profile(
        read_pellets_store(),
        {"brand_name": "Acme", "wood_type": "Oak", "rating": 4, "comments": "", "add_and_load": False},
    )

    assert all("id" not in profile for profile in read_pellets_store()["archive"].values())


def test_an_unseen_brand_and_wood_join_the_vocabularies(db):
    """The vocabularies are what the lists are visibly for; a bag the list has
    not heard of is the normal case."""
    pellets_actions.pellets_add_profile(
        read_pellets_store(),
        {"brand_name": "Acme", "wood_type": "Ironbark", "rating": 4, "comments": "", "add_and_load": False},
    )

    stored = read_pellets_store()
    assert "Acme" in stored["brands"]
    assert "Ironbark" in stored["woods"]


@pytest.mark.parametrize("bad", ["nope", 0, 99, None])
def test_a_rating_outside_one_to_five_is_refused_at_the_door(db, bad):
    """New writes are validated strictly; the store must be untouched."""
    before = read_pellets_store()

    resp = pellets_actions.pellets_add_profile(
        read_pellets_store(),
        {"brand_name": "Acme", "wood_type": "Oak", "rating": bad, "comments": "", "add_and_load": False},
    )

    assert resp["result"] == "Error"
    assert "rating" in resp["message"]
    assert read_pellets_store() == before


def test_editing_a_profile_refuses_a_bad_rating(db):
    profile_id = next(iter(db["archive"]))

    resp = pellets_actions.pellets_edit_profile(
        read_pellets_store(),
        {"profile": profile_id, "brand_name": "Acme", "wood_type": "Oak", "rating": 12, "comments": ""},
    )

    assert resp["result"] == "Error"
    assert read_pellets_store()["archive"][profile_id]["rating"] != 12
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_writers_v2.py -v
```

Expected: every test FAILS — the writers still produce v1.

- [ ] **Step 4: Rewrite the writers**

In `common/pellets_actions.py`, add `import time` beside the existing `from datetime import datetime`, and add above `clear_pellet_db`:

```python
def _log_key(log):
    """A millisecond key for `log` that no existing entry already holds.

    Second resolution let two loads inside one second land on the same dict key
    and lose an entry; a millisecond that is already taken is advanced rather
    than overwritten, so the count is the number of loads whatever the clock
    does.
    """
    stamp = int(time.time() * 1000)
    while str(stamp) in log:
        stamp += 1
    return str(stamp)


def _validated_rating(action_data):
    """The rating as an int in 1..5, or None if the request did not carry one."""
    try:
        rating = int(action_data["rating"])
    except (KeyError, TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 5 else None
```

`pellets_load_profile`'s body becomes:

```python
def pellets_load_profile(pelletdb, action_data):
    if "profile" in action_data:
        pelletdb["current"]["pelletid"] = action_data["profile"]
        pelletdb["current"]["date_loaded"] = str(datetime.now())[0:19]
        pelletdb["current"]["est_usage"] = 0.0
        pelletdb["log"][_log_key(pelletdb["log"])] = {"pelletid": action_data["profile"], "deleted": False}
        # This handler changes one boolean, so one boolean is what it states.
        # Queuing the whole control dict would carry a stale snapshot of every
        # other member through the queue, and a delta says only what it means.
        write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin="app")
        write_pellet_db(pelletdb)
        # Snapshot the new load, exactly as the pellets page does -- the React
        # "Load New Pellets" path reaches this handler.
        backup_pellet_db(action="backup")
        return api_response(result="OK")
    else:
        return api_response(result="Error", message="Error: Profile not included in request")
```

`pellets_add_profile`:

```python
def pellets_add_profile(pelletdb, action_data):
    rating = _validated_rating(action_data)
    if rating is None:
        return api_response(result="Error", message="Error: rating must be a whole number from 1 to 5")

    profile_id = "".join(filter(str.isalnum, str(datetime.now())))
    brand = action_data["brand_name"]
    wood = action_data["wood_type"]
    pelletdb["archive"][profile_id] = {
        "brand": brand,
        "wood": wood,
        "rating": rating,
        "comments": action_data["comments"],
    }
    # The vocabularies are autocomplete suggestions, and a bag they have not
    # heard of is the normal case -- so naming one adds it.
    if brand not in pelletdb["brands"]:
        pelletdb["brands"].append(brand)
    if wood not in pelletdb["woods"]:
        pelletdb["woods"].append(wood)

    if action_data["add_and_load"]:
        pelletdb["current"]["pelletid"] = profile_id
        # MINIMAL patch -- see pellets_load_profile for the full rationale.
        write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin="app")
        pelletdb["current"]["date_loaded"] = str(datetime.now())[0:19]
        pelletdb["current"]["est_usage"] = 0.0
        pelletdb["log"][_log_key(pelletdb["log"])] = {"pelletid": profile_id, "deleted": False}

    write_pellet_db(pelletdb)
    return api_response(result="OK")
```

`pellets_edit_profile`:

```python
def pellets_edit_profile(pelletdb, action_data):
    if "profile" not in action_data:
        return api_response(result="Error", message="Error: Profile not included in request")
    rating = _validated_rating(action_data)
    if rating is None:
        return api_response(result="Error", message="Error: rating must be a whole number from 1 to 5")

    profile_id = action_data["profile"]
    brand = action_data["brand_name"]
    wood = action_data["wood_type"]
    pelletdb["archive"][profile_id]["brand"] = brand
    pelletdb["archive"][profile_id]["wood"] = wood
    pelletdb["archive"][profile_id]["rating"] = rating
    pelletdb["archive"][profile_id]["comments"] = action_data["comments"]
    if brand not in pelletdb["brands"]:
        pelletdb["brands"].append(brand)
    if wood not in pelletdb["woods"]:
        pelletdb["woods"].append(wood)
    write_pellet_db(pelletdb)
    return api_response(result="OK")
```

`pellets_delete_profile`'s inner loop:

```python
            pelletdb["archive"].pop(profile_id)
            for index in pelletdb["log"]:
                if pelletdb["log"][index]["pelletid"] == profile_id:
                    pelletdb["log"][index] = {"pelletid": None, "deleted": True}
```

- [ ] **Step 5: Correct the defaults**

In `common/defaults.py`'s `default_pellets()`: `est_usage` becomes `0.0`, the archive entry loses its `id` key, and the log becomes one epoch-ms entry with a tombstone-shaped value:

```python
    pelletdb["current"] = {
        "pelletid": ID,  # Pellet ID for the profile currently loaded
        "hopper_level": 100,  # Percentage of pellets remaining
        "date_loaded": now,  # Date that current pellets loaded
        "est_usage": 0.0,  # Estimated usage since loading (use auger load rate, and auger on time)
    }
```

```python
    pelletdb["archive"] = {
        ID: {
            "brand": "Generic",
            "wood": "Alder",
            "rating": 4,
            "comments": "This is a placeholder profile.  Alder is generic and used in almost all pellets, "
            "regardless of the wood type indicated on the packaging.  It tends to burn "
            "consistently and produces a mild smoke.",
        }
    }

    pelletdb["log"] = {str(math.trunc(time.time() * 1000)): {"pelletid": ID, "deleted": False}}
```

- [ ] **Step 6: Run the writer tests**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_pellets_writers_v2.py -v
```

Expected: PASS, 10 tests (the rating parametrize counts four).

- [ ] **Step 7: The negative control (required)**

Revert `_log_key(pelletdb["log"])` in `pellets_load_profile` to `str(datetime.now())[0:19]` and re-run:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/common/test_pellets_writers_v2.py::test_two_loads_in_the_same_millisecond_are_both_recorded -v
```

Expected: **FAIL on the COUNT assertion** (`len(log) == before + 2`), not on the key format. If it fails on the key format instead, the assertions are in the wrong order and the test would pass for the wrong reason once the format changed. Restore and re-run green.

- [ ] **Step 8: Update the tests that encode the v1 log shape**

Three files assert log keys or values directly. Each must move to v2:

- `tests/web/test_socketio_app_data.py:372, 386` — `pelletdb["log"]["2020-01-01 00:00:00"] = "x"` becomes `pelletdb["log"]["1577836800000"] = {"pelletid": "x", "deleted": False}`.
- `tests/web/test_socketio_app_data.py:1036, 1041` — the seeded entry becomes `pelletdb["log"]["1577836800000"] = {"pelletid": "deadbeef", "deleted": False}` and the assertion becomes `== {"pelletid": None, "deleted": True}`.
- `tests/web/test_socketio_app_data.py:637` — no change needed; it takes whatever key exists.
- `tests/web/test_api_admin_maintenance.py:138` and `tests/web/test_api_admin_system.py:172` — `pelletdb["log"]["1767225600"] = "sentinel-profile-id"` becomes `pelletdb["log"]["1767225600000"] = {"pelletid": "sentinel-profile-id", "deleted": False}`.

- [ ] **Step 9: Full suite**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: green. Any remaining failure is a writer this task missed — find it by the validation error's dotted path, not by relaxing the model.

- [ ] **Step 10: Format and commit**

```bash
.venv/bin/ruff format common/pellets_actions.py common/defaults.py tests/unit/common/test_pellets_writers_v2.py tests/web/test_socketio_app_data.py tests/web/test_api_admin_maintenance.py tests/web/test_api_admin_system.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(pellets): write the version 2 shape, and stop losing same-second log entries

The log key carries milliseconds and advances past one already taken, so the
entry count is the number of loads whatever the clock does. Rating is bounded
at the door, and naming an unlisted brand or wood adds it to the vocabulary.
EOF
```

> **Chromium note:** this task edits three files under `tests/web/`. Re-run them in the main checkout before merge — `[chromium]` tests skip in agent worktrees.

---

### Task 11: The React side of the v2 shape

**Files:**
- Modify: `web-react/src/helpers/pellets/pelletTypes.ts`
- Modify: `web-react/src/components/pellets/PelletLog.tsx`
- Modify: `web-react/src/components/pellets/ProfileEditor.tsx` (one comment)
- Modify: `web-react/tests/unit/components/pellets/PelletLog.test.tsx`
- Modify: `tests/web/test_api_pellets.py:45-48` (the shape pin)

**Interfaces:**
- Consumes: the v2 payload (Tasks 8–10).

- [ ] **Step 1: `jj new`**

```bash
jj new
```

- [ ] **Step 2: Write the failing test**

Update `web-react/tests/unit/components/pellets/PelletLog.test.tsx`'s fixtures to the v2 shape and add the ordering case that the old string sort got wrong:

```tsx
const ARCHIVE: Record<string, PelletProfile> = {
  p1: { brand: "Generic", wood: "Alder", rating: 5, comments: "c" },
};

// Epoch milliseconds, as decimal strings. "9..." sorts after "10..." as text
// and before it as a number, which is why the component sorts numerically.
const LOG: Record<string, PelletLogEntry> = {
  "1785024000000": { pelletid: "p1", deleted: false },
  "999999999999": { pelletid: "p1", deleted: false },
  "1784851200000": { pelletid: null, deleted: true },
  "1784937600000": { pelletid: "vanished", deleted: false },
};
```

and replace `timestampCells()` plus the sorting assertion:

```tsx
function timestampCells() {
  return screen.getAllByRole("row").map((r) => r.firstElementChild?.textContent ?? "");
}

it("sorts rows oldest first, numerically rather than as text", () => {
  renderLog();
  expect(timestampCells()).toEqual([
    new Date(999999999999).toLocaleString(),
    new Date(1784851200000).toLocaleString(),
    new Date(1784937600000).toLocaleString(),
    new Date(1785024000000).toLocaleString(),
  ]);
});
```

Update the remaining cases in that file so a deleted row is identified by `entry.deleted` rather than by the string `"deleted"`, and so `onDelete` is asserted with the millisecond key.

- [ ] **Step 3: Run it to verify it fails**

```bash
cd web-react && bun run test tests/unit/components/pellets/PelletLog.test.tsx
```

Expected: FAIL — the type does not exist and the component still string-sorts. (`bun run test`, never `bun test` — the latter picks the wrong runner and its import error looks like a TDD red.)

- [ ] **Step 4: Update the types**

In `web-react/src/helpers/pellets/pelletTypes.ts`:

```ts
/** One archive entry. The archive key is the profile id; the entry does not
    repeat it. */
export interface PelletProfile {
  brand: string;
  wood: string;
  rating: number; // 1-5, enforced by common/pellets_schema.py
  comments: string;
}

/** One load. `pelletid` is null exactly when the profile it named was deleted
    (common/pellets_actions.py pellets_delete_profile writes the tombstone). */
export interface PelletLogEntry {
  pelletid: string | null;
  deleted: boolean;
}
```

and on `PelletDb`:

```ts
  /** Load time in epoch MILLISECONDS, as a decimal string -- JSON object keys
      are strings. Sort numerically; text order is wrong across digit counts. */
  log: Record<string, PelletLogEntry>;
  schema_version: number;
```

Update the `est_usage` doc line to say the control process writes a float, and update the file's header pin note to name `tests/web/test_api_pellets.py::test_get_pellets_returns_full_database` unchanged.

- [ ] **Step 5: Update the component**

Replace the body of `web-react/src/components/pellets/PelletLog.tsx` below its imports:

```tsx
import { useState } from "react";
import type { PelletLogEntry, PelletProfile } from "../../helpers/pellets/pelletTypes";
import { ConfirmAction } from "../dashboard/ConfirmAction";
import { Rating } from "./Rating";

interface Props {
  log: Record<string, PelletLogEntry>;
  archive: Record<string, PelletProfile>;
  busy: boolean;
  onDelete(key: string): void;
}

/** A log key is epoch milliseconds as a decimal string. */
function formatKey(key: string): string {
  return new Date(Number(key)).toLocaleString();
}

/**
 * The pellet load log: one row per load, oldest first.
 *
 * A row is either a load that still has its profile, a tombstone left by
 * delete_profile, or an id that is simply absent from the archive. The last two
 * get the same "User Deleted Profile" treatment, because neither has anything
 * to show and both are ordinary states rather than errors.
 */
export function PelletLog({ log, archive, busy, onDelete }: Props) {
  // One ConfirmAction for the whole table, keyed by a pending log key, rather
  // than one per row -- the arrangement StringListField documents.
  const [pending, setPending] = useState<string | null>(null);

  // Numeric, not lexicographic: a twelve-digit stamp sorts after a
  // thirteen-digit one as text.
  const rows = Object.entries(log).sort(([a], [b]) => Number(a) - Number(b));

  return (
    <section className="pf-pellets-card pf-pellets-wide" aria-label="Pellet Log">
      <div className="pf-pellets-card-title">Pellet Log</div>

      <div className="pf-pellets-scroll">
        <table className="pf-devices-table">
          <tbody>
            {rows.map(([key, entry]) => {
              const profile = entry.pelletid === null ? undefined : archive[entry.pelletid];
              const when = formatKey(key);
              return (
                <tr key={key}>
                  <td>{when}</td>
                  <td>{profile ? `${profile.brand} ${profile.wood}` : "User Deleted Profile"}</td>
                  <td>{profile ? <Rating value={profile.rating} /> : "-"}</td>
                  <td>
                    {profile ? (
                      <button
                        className="pf-devices-table-btn"
                        aria-label={`Delete log entry ${when}`}
                        disabled={busy}
                        onClick={() => setPending(key)}
                      >
                        ✕
                      </button>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ConfirmAction
        open={pending !== null}
        title="Delete log entry?"
        message={`The load recorded at ${pending === null ? "" : formatKey(pending)} will be removed from the pellet log.`}
        onConfirm={() => {
          const key = pending;
          setPending(null);
          if (key !== null) onDelete(key);
        }}
        onCancel={() => setPending(null)}
      />
    </section>
  );
}
```

In `ProfileEditor.tsx:239`, the comment says delete rewrites log entries to `the literal "deleted"`. It writes a tombstone now — say that.

- [ ] **Step 6: Update the shape pin**

In `tests/web/test_api_pellets.py`:

```python
    assert set(pellets) == {"schema_version", "current", "woods", "brands", "archive", "log", "lastupdated"}
    assert set(pellets["current"]) == {"pelletid", "hopper_level", "date_loaded", "est_usage"}
    any_profile = next(iter(pellets["archive"].values()))
    assert set(any_profile) == {"brand", "wood", "rating", "comments"}
    assert isinstance(pellets["brands"], list)
    assert isinstance(pellets["woods"], list)
    assert isinstance(pellets["log"], dict)
    any_entry = next(iter(pellets["log"].values()))
    assert set(any_entry) == {"pelletid", "deleted"}
    assert all(key.isdigit() for key in pellets["log"])
```

- [ ] **Step 7: Run every gate**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test && bun run build && cd ..
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: typecheck clean, lint 0 errors / 2 pre-existing warnings, rstest green, Python green. Typecheck is what finds any remaining `profile.id` reader — `PelletsPage.tsx:122` is the only `log` consumer, but let the compiler confirm.

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format tests/web/test_api_pellets.py
.venv/bin/ruff check .
jj describe --stdin <<'EOF'
feat(web): read the version 2 pellet shape

Log keys are milliseconds, so the table sorts numerically -- text order puts a
twelve-digit stamp before a thirteen-digit one.
EOF
```

> **Chromium note:** `tests/web/test_api_pellets.py` is `[chromium]`-marked and SKIPS in an agent worktree. It must be run in the main checkout before merge, or the shape pin proves nothing.

---

## After the last task

- [ ] Re-run every `tests/web/` file this plan touched **in the main checkout**, where chromium exists:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/web/test_api_pellets.py tests/web/test_socketio_app_data.py \
  tests/web/test_api_admin_maintenance.py tests/web/test_api_admin_system.py \
  tests/web/test_api_admin_backups.py -v
```

- [ ] Strike the *Schema and toolchain follow-ups* line in `docs/superpowers/react-migration-backlog.md` — "nothing PiFire persists records the schema it was written against" — following that file's own convention: **DONE** carries the date it landed, and the wizard-manifest half stays open with no date, because the spec deliberately left it out of scope (the fingerprint is the better answer for staleness).
- [ ] Record in the same entry that the pellet log's second-resolution collision bug (spec §5.4) was fixed as part of the v2 migration, since the backlog does not mention it anywhere.
- [ ] The grill is running `v1.11.0-dev20`. Nothing here reaches it until a release is tagged.

---

## Parallelization

Concurrency needs isolated `jj` workspaces — disjoint file lists are not enough, because `@` is shared. Copy `.lsp.json` and run `bun install` in each new workspace (both are gitignored, so `workspace add` skips them).

| Wave | Tasks | Why they can run together |
|---|---|---|
| 1 | **Task 1** alone | Everything downstream imports `common/schema_digest.py`. |
| 2 | **Task 2** alone | It changes `SettingsSchema`, which Task 1's constant pins. |
| 3 | **Task 3**, **Task 5** | Disjoint: Task 3 is `settings_migration`/`datastore`; Task 5 creates `pellets_schema.py` and a fixture and touches nothing else. Both need Task 2 (Task 5 only for the green baseline). |
| 4 | **Task 4**, **Task 6** | Task 4 appends to a file Task 3 created; Task 6 needs Task 5. They share no file. |
| 5 | **Task 7** alone | Touches `common/datastore.py`, which Task 3 also edits, and `common/defaults.py`, which Task 2 also edits. |
| 6 | **Tasks 8 → 9 → 10** strictly serial | All three edit `common/pellets_schema.py` or the writers it validates, and the tree is red until Task 10 lands. |
| 7 | **Task 11** alone | Needs the v2 payload that Task 10 produces. |

Slices A, B and C are themselves serial: B's model must be written against the shape A's release ships, and C reshapes what B validates.
