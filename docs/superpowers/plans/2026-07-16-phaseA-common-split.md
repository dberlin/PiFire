# Phase A — `common/common.py` Split + Simplifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify `common/common.py` internally (dispatch table, generic accessors, file-read dedup, install-status dedup, `is_not_blank` fix) and then split it into a cohesive `common/` package with all direct import sites rewritten — with no runtime behavior change except the deliberate, test-gated `is_not_blank` empty-value fix.

**Architecture:** Two-commit-group branch `refactor/common-split`. Group A1 (Tasks 1–7) does low-risk in-file simplifications guarded by the existing suite plus new characterization tests. Group A2 (Tasks 8–9) physically moves symbols into new modules and rewrites the 55 direct `from common.common import …` lines. `common/app.py` remains the web facade (blueprints import `process_command`, `is_not_blank`, etc. from `common.app`, which re-imports from the new modules), so blueprint import lines do **not** change.

**Tech Stack:** Python 3.14, pytest, SQLite blob datastore (`common/datastore.py`), Serena for all symbolic edits.

## Global Constraints

- **Behavior-preserving** except the `is_not_blank` empty-value fix (Task 1), which is characterized before and after.
- **Serena for all code edits** (`replace_symbol_body`, `insert_*_symbol`, `replace_content`); never hand-edit code files blind.
- **Public names stay stable.** Every currently-importable name in `common.common` remains importable from *somewhere*; `common/app.py` re-exports keep the blueprint-facing surface identical.
- **No new dependencies.**
- **Full suite green before each commit:** `python3 -m pytest -q`.
- **Frequent commits** — one per task.
- `except (IOError, OSError):` — parenthesized tuple form everywhere (no Python-2 comma syntax).

---

### Task 1: Characterize and fix `is_not_blank`

`common/app.py:271-272` currently returns `setting in response and setting != ""` — it tests the *key name*, so it means "key present." All 38 callers (in `blueprints/settings/routes.py`) coerce numerically (`int`/`float`/`min`), so an empty submission today raises `int("")` → 500. Fix it to test the *value*, converting that latent crash into "keep prior value."

**Files:**
- Test: `tests/unit/common/test_is_not_blank.py` (create)
- Modify: `common/app.py:271-272`

**Interfaces:**
- Produces: `is_not_blank(response, setting) -> bool` — True iff `setting` is a key in `response` **and** `response[setting]` is a non-empty string. Signature unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/common/test_is_not_blank.py
from common.app import is_not_blank


def test_key_absent_returns_false():
    assert is_not_blank({}, "pmode") is False


def test_key_present_with_value_returns_true():
    assert is_not_blank({"pmode": "2"}, "pmode") is True


def test_key_present_but_empty_returns_false():
    # Regression: today this is True (helper checks the key name, not the value),
    # which lets int("") reach the settings route and raise a 500. After the fix,
    # an empty submission must be treated as blank so the branch is skipped.
    assert is_not_blank({"pmode": ""}, "pmode") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/common/test_is_not_blank.py -v`
Expected: `test_key_present_but_empty_returns_false` FAILS (returns True); the other two PASS.

- [ ] **Step 3: Apply the fix with Serena**

Use `replace_symbol_body` on `is_not_blank` in `common/app.py`:

```python
def is_not_blank(response, setting):
    return setting in response and response[setting] != ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/common/test_is_not_blank.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run the web suite to confirm no settings route regressed**

Run: `python3 -m pytest tests/web -q`
Expected: PASS (all settings-route branches still store populated values; empty inputs now skip instead of crashing).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/common/test_is_not_blank.py common/app.py
git commit -m "fix(common): is_not_blank checks the submitted value, not the key name

Empty numeric form fields previously reached int()/float() and raised a
500; now they are treated as blank and skip the assignment. Gated by new
characterization tests. Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Generic JSON-blob accessors

Collapse the repeated reader shape `raw = datastore.get_blob(KEY); return json.loads(raw) if raw is not None else DEFAULT` (and its writer) into two helpers, then define the *simple* accessors from them. `write_control`/`read_control` have extra MERGE/flush logic — only their plain-blob paths use the helpers.

**Files:**
- Modify: `common/common.py` (add helpers near the other private helpers, e.g. after `_read_json_key_or_none`; migrate `read_control` non-flush path and the simple pairs: `read_settings_store`/`write_settings_store`, `read_pellets_store`/`write_pellets_store`, `read_current`/`write_current`, `read_tr`/`write_tr`, `read_status`/`write_status`, `read_errors`/`write_errors`)
- Test: `tests/unit/common/test_json_blob_helpers.py` (create)

**Interfaces:**
- Produces:
  - `_read_json_blob(key, default_factory)` → `json.loads(get_blob(key))` if present else `default_factory()`.
  - `_write_json_blob(key, value)` → `datastore.set_blob(key, json.dumps(value))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/common/test_json_blob_helpers.py
import common.common as c


def test_read_json_blob_returns_default_when_absent(monkeypatch):
    monkeypatch.setattr(c.datastore, "get_blob", lambda key: None)
    assert c._read_json_blob("nope", lambda: {"d": 1}) == {"d": 1}


def test_read_json_blob_parses_present(monkeypatch):
    monkeypatch.setattr(c.datastore, "get_blob", lambda key: '{"x": 5}')
    assert c._read_json_blob("k", dict) == {"x": 5}


def test_write_json_blob_roundtrip(monkeypatch):
    seen = {}
    monkeypatch.setattr(c.datastore, "set_blob", lambda key, raw: seen.update({key: raw}))
    c._write_json_blob("k", {"x": 5})
    assert seen == {"k": '{"x": 5}'}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/common/test_json_blob_helpers.py -v`
Expected: FAIL (`_read_json_blob` / `_write_json_blob` not defined).

- [ ] **Step 3: Add the helpers with Serena**

Use `insert_after_symbol` on `_read_json_key_or_none` in `common/common.py`:

```python
def _read_json_blob(key, default_factory):
    raw = datastore.get_blob(key)
    return json.loads(raw) if raw is not None else default_factory()


def _write_json_blob(key, value):
    datastore.set_blob(key, json.dumps(value))
```

- [ ] **Step 4: Migrate the simple accessors with Serena**

For each simple reader, `replace_symbol_body` to delegate. Example — `read_control` non-flush path:

```python
def read_control(flush=False):
    """
    Read Control from SQLite DB

    :param flush: True to clean control. False otherwise
    :return: control
    """
    if flush:
        return _flush_control()
    return _read_json_blob("control:general", default_control)
```

Apply the same pattern to `read_settings_store`, `read_pellets_store`, `read_current`, `read_tr`, `read_status`, `read_errors` (each `_read_json_blob(KEY, default_factory)`) and their writers to `_write_json_blob(KEY, value)`. For `write_control`, only the `WriteKind.OVERWRITE` branch changes to `_write_json_blob("control:general", control)`; leave the MERGE branch untouched.

- [ ] **Step 5: Run the blob + datastore suites**

Run: `python3 -m pytest tests/unit/common/test_common_blobs.py tests/unit/datastore -q`
Expected: PASS (existing behavior preserved).

- [ ] **Step 6: Commit**

```bash
git add common/common.py tests/unit/common/test_json_blob_helpers.py
git commit -m "refactor(common): generic _read/_write_json_blob helpers for simple accessors

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Generic file-read-with-retry + fix Python-2 except syntax

Five readers (`read_settings_file`, `read_pellet_db_file`, `read_wizard`, `read_updater_manifest`, `read_generic_json`) reimplement `os.fdopen(os.open(...))` → `json.loads` → `except (IOError, OSError)` (default) → `except ValueError` (recursive retry). Route them through one helper and fix the 6 `except IOError, OSError:` occurrences.

**Files:**
- Modify: `common/common.py`
- Test: `tests/unit/common/test_load_json_file.py` (create)

**Interfaces:**
- Produces: `_load_json_file(filename, default, retry_count=0)` → parsed JSON, or `default` on `(IOError, OSError)`, or one recursive retry on `ValueError` (matching the existing `read_generic_json` retry semantics; cap retries as the current code does).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/common/test_load_json_file.py
import json
import common.common as c


def test_missing_file_returns_default(tmp_path):
    assert c._load_json_file(str(tmp_path / "absent.json"), {"d": 1}) == {"d": 1}


def test_valid_file_parses(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"x": 9}))
    assert c._load_json_file(str(p), {}) == {"x": 9}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/common/test_load_json_file.py -v`
Expected: FAIL (`_load_json_file` not defined).

- [ ] **Step 3: Add `_load_json_file` and migrate the five readers with Serena**

Read each of the five existing readers' bodies first (`find_symbol` with `include_body=True`) to preserve their per-reader default/upgrade overlay, then have each delegate the raw load to `_load_json_file`, keeping its own post-load logic. Replace all `except IOError, OSError:` with `except (IOError, OSError):` in the same pass.

- [ ] **Step 4: Run the relevant suites**

Run: `python3 -m pytest tests/unit/common tests/unit/wizard tests/unit/bootstrap -q`
Expected: PASS.

- [ ] **Step 5: Confirm no Python-2 except syntax remains**

Run: `grep -rn "except IOError, OSError" common/ || echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 6: Commit**

```bash
git add common/common.py tests/unit/common/test_load_json_file.py
git commit -m "refactor(common): single _load_json_file helper; fix py2 except syntax

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: De-duplicate wizard/updater install-status accessors

`get_/set_wizard_install_status` and `get_/set_updater_install_status` are identical except the `wizard:` vs `updater:` key prefix.

**Files:**
- Modify: `common/common.py`
- Test: `tests/unit/common/test_install_status.py` (create)

**Interfaces:**
- Produces: `_get_install_status(prefix)` → `(percent, status, output)` tuple via `_read_json_key_or_none(f"{prefix}:percent"|:status|:output)`; `_set_install_status(prefix, percent, status, output)` → three `set_blob` writes. The four public functions become one-line wrappers.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/common/test_install_status.py
import common.common as c


def test_set_then_get_wizard(monkeypatch):
    store = {}
    monkeypatch.setattr(c.datastore, "set_blob", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(c.datastore, "get_blob", lambda k: store.get(k))
    c.set_wizard_install_status(42, "Working", "line")
    assert c.get_wizard_install_status() == (42, "Working", "line")


def test_wizard_and_updater_use_separate_namespaces(monkeypatch):
    store = {}
    monkeypatch.setattr(c.datastore, "set_blob", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(c.datastore, "get_blob", lambda k: store.get(k))
    c.set_wizard_install_status(1, "w", "wo")
    c.set_updater_install_status(2, "u", "uo")
    assert c.get_wizard_install_status() == (1, "w", "wo")
    assert c.get_updater_install_status() == (2, "u", "uo")
```

- [ ] **Step 2: Run test to verify it passes on current code (behavior baseline)**

Run: `python3 -m pytest tests/unit/common/test_install_status.py -v`
Expected: PASS (this is a characterization baseline — the refactor must keep it green).

- [ ] **Step 3: Introduce the private helpers and rewrite the four wrappers with Serena**

Add `_get_install_status(prefix)` / `_set_install_status(prefix, percent, status, output)` (bodies mirroring the current wizard versions with the prefix parameterized), then `replace_symbol_body` on each of the four public functions to delegate, e.g.:

```python
def get_wizard_install_status():
    return _get_install_status("wizard")


def set_wizard_install_status(percent, status, output):
    _set_install_status("wizard", percent, status, output)
```

- [ ] **Step 4: Run tests to verify they still pass**

Run: `python3 -m pytest tests/unit/common/test_install_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add common/common.py tests/unit/common/test_install_status.py
git commit -m "refactor(common): parameterize install-status accessors by namespace

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Characterization safety net for `process_command`

Before decomposing the 666-line `process_command` (`common/common.py:2523`), lock its observable behavior with a golden test driven against the in-memory datastore, so Tasks 6–7 can only pass if behavior is byte-identical.

**Files:**
- Test: `tests/characterization/test_process_command_golden.py` (create)
- Reference (do not modify): `tests/characterization/harness.py`, `tests/characterization/fixtures.py` (existing in-memory datastore setup)

**Interfaces:**
- Consumes: `common.common.process_command(action, arglist, origin, kind)` and the existing characterization datastore fixtures.

- [ ] **Step 1: Enumerate the representative command set**

Read `process_command`'s body (`find_symbol` `process_command` `include_body=True`) and list every `(action, arglist[0])` pair it branches on: the GET subcommands, SET subcommands, the 4 manual outputs (power/igniter/fan/auger), CMD, SYS. Record them in the test as a table.

- [ ] **Step 2: Write the golden test**

```python
# tests/characterization/test_process_command_golden.py
import json
import pytest
from tests.characterization.fixtures import in_memory_datastore  # adjust to actual fixture name

import common.common as c

CASES = [
    # (action, arglist) pairs — fill from Step 1 enumeration
    ("get", ["temp"]),
    ("set", ["mode", "Startup"]),
    ("set", ["manual", "power", "on"]),
    ("set", ["manual", "fan", "off"]),
    # ... one per branch identified in Step 1
]


@pytest.mark.parametrize("action,arglist", CASES)
def test_process_command_golden(in_memory_datastore, snapshot, action, arglist):
    result = c.process_command(action=action, arglist=arglist, origin="test")
    # Capture both the return value and the resulting control/status blob state.
    state = {
        "result": result,
        "control": c.read_control(),
        "status": c.read_status(),
    }
    assert json.dumps(state, sort_keys=True, default=str) == snapshot
```

If the repo's characterization harness uses a captured-file oracle rather than a `snapshot` fixture, follow the existing pattern in `tests/characterization/test_modes_golden.py` (read that file first and mirror its capture/compare mechanism) instead of `snapshot`.

- [ ] **Step 3: Run to capture the baseline**

Run: `python3 -m pytest tests/characterization/test_process_command_golden.py -q`
Expected: PASS (baseline captured). Commit the captured golden artifact.

- [ ] **Step 4: Commit**

```bash
git add tests/characterization/test_process_command_golden.py tests/characterization/**/*process_command*
git commit -m "test(common): golden characterization for process_command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Extract `_manual_toggle` from the four manual-output branches

The `set`/`manual` branch repeats a ~15-line read-status → toggle → set-output block for `power`, `igniter`, `fan`, `auger`.

**Files:**
- Modify: `common/common.py` (the `set`/`manual` section of `process_command`)

**Interfaces:**
- Produces: `_manual_toggle(control, pin_name, arglist)` → mutates/returns `control` applying the on/off/pulse action for `pin_name`, matching the current per-pin blocks exactly.

- [ ] **Step 1: Read the four branches**

`find_symbol` `process_command` `include_body=True`; copy the power/igniter/fan/auger blocks verbatim to diff their only differences (the pin key).

- [ ] **Step 2: Add `_manual_toggle` with Serena**

`insert_before_symbol` on `process_command`, body = the common block parameterized by `pin_name`.

- [ ] **Step 3: Replace the four inline blocks with calls**

`replace_content` within `process_command` so each of the four becomes `control = _manual_toggle(control, "<pin>", arglist)`.

- [ ] **Step 4: Run the golden test**

Run: `python3 -m pytest tests/characterization/test_process_command_golden.py -q`
Expected: PASS (byte-identical).

- [ ] **Step 5: Commit**

```bash
git add common/common.py
git commit -m "refactor(common): extract _manual_toggle for the four manual outputs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Convert `process_command` to a dispatch table

Replace the nested `if action==… / elif arglist[0]==…` ladders with `{(action, subcommand): handler}`. Extract each branch to a small module-level `_cmd_*` function first, then dispatch.

**Files:**
- Modify: `common/common.py`

**Interfaces:**
- Produces: private `_cmd_<action>_<subcommand>(control, arglist, origin, kind)` handlers and a `_COMMAND_DISPATCH` dict; `process_command` looks up `(action, arglist[0])`, calls the handler, and preserves the current "unknown command" fallback behavior.

- [ ] **Step 1: Extract one branch to a handler, keep dispatch inline, run golden**

Pick the smallest GET branch; `insert_before_symbol` the handler; replace its inline block with a call. Run `python3 -m pytest tests/characterization/test_process_command_golden.py -q` → PASS. This proves the extraction shape before repeating.

- [ ] **Step 2: Extract the remaining branches the same way, running the golden test after each**

One handler per `(action, subcommand)`. After each extraction: `python3 -m pytest tests/characterization/test_process_command_golden.py -q` → PASS.

- [ ] **Step 3: Replace the if/elif ladder with the dispatch dict**

`replace_symbol_body` on `process_command`:

```python
def process_command(action=None, arglist=[], origin="unknown", kind=WriteKind.MERGE):
    control = read_control()
    handler = _COMMAND_DISPATCH.get((action, arglist[0] if arglist else None))
    if handler is None:
        return _process_command_unknown(action, arglist, origin)  # preserve current fallback
    return handler(control, arglist, origin, kind)
```

Preserve the exact current fallback/error path (read it first and reproduce it in `_process_command_unknown`).

- [ ] **Step 4: Run the full characterization + web suites**

Run: `python3 -m pytest tests/characterization tests/web -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add common/common.py
git commit -m "refactor(common): dispatch table for process_command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Create the `common/` package modules and move symbols

Physically split `common/common.py`. Move symbols with Serena, adding intra-`common` imports so `common/common.py` (temporarily) still imports what it needs. **No import-site rewrite yet** — that is Task 9.

**Files:**
- Create: `common/defaults.py`, `common/system.py`, `common/datastore_accessors.py`, `common/api_commands.py`, `common/settings_migration.py`
- Modify: `common/common.py` (becomes thin; re-imports moved names for now)

**Interfaces:**
- Produces the module homes:
  - `common/defaults.py` — the `default_*` builders (`default_settings`, `default_control`, `default_notify_services`, `default_notify`, `default_probe_map`, …).
  - `common/system.py` — reboot/shutdown/restart, `_wifi_quality_*`, os/hardware/network info.
  - `common/datastore_accessors.py` — the blob read/write accessors + `_read_json_blob`/`_write_json_blob`.
  - `common/api_commands.py` — `process_command`, `_manual_toggle`, `_cmd_*`, `_COMMAND_DISPATCH`.
  - `common/settings_migration.py` — `upgrade_settings`, `downgrade_settings`, `read_settings_file`, `_load_json_file`.

- [ ] **Step 1: Move `default_*` builders to `common/defaults.py`**

Use `create_text_file`/`insert_*` to place the builders in `common/defaults.py` with their imports; delete from `common/common.py` via `safe_delete_symbol`; add `from common.defaults import *` (or explicit names) at the top of `common/common.py`.

- [ ] **Step 2: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS (nothing should notice — names still resolve via `common.common`).

- [ ] **Step 3: Repeat Step 1–2 for `system.py`, `datastore_accessors.py`, `settings_migration.py`, `api_commands.py`**

Move one module's worth of symbols, wire the temporary re-import in `common/common.py`, run `python3 -m pytest -q` → PASS, then proceed to the next. Commit after each module moves cleanly.

- [ ] **Step 4: Commit (per module)**

```bash
git add common/
git commit -m "refactor(common): extract <module> from common.common (re-exported for now)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Rewrite the 55 direct import sites; remove the temporary re-exports

Point every direct `from common.common import X` at the new module, then delete the temporary `common/common.py` re-imports. `common/app.py` updates its line `from common.common import process_command, …` to the new homes; blueprint files importing from `common.app` are unaffected.

**Files:**
- Modify: all files listed by the grep in Step 1 (55 lines across `blueprints/`, `controller/`, `display/`, `board-config.py`, `common/app.py`, `common/datastore.py`, `tools/`, and the `tests/` that import directly)
- Test: `tests/unit/common/test_import_smoke.py` (create)

**Interfaces:**
- Consumes: the module homes from Task 8.

- [ ] **Step 1: List every direct import site**

Run: `grep -rn "from common.common import\|import common.common" --include="*.py" . | grep -v '\.venv'`
Expected: the 55 lines to rewrite.

- [ ] **Step 2: Write an import-smoke test**

```python
# tests/unit/common/test_import_smoke.py
import importlib


def test_new_modules_import():
    for mod in (
        "common.defaults",
        "common.system",
        "common.datastore_accessors",
        "common.api_commands",
        "common.settings_migration",
    ):
        importlib.import_module(mod)


def test_public_names_resolve_from_new_homes():
    from common.api_commands import process_command  # noqa: F401
    from common.datastore_accessors import read_control, write_control  # noqa: F401
    from common.defaults import default_settings, default_control  # noqa: F401
```

- [ ] **Step 3: Run it — expect PASS (Task 8 already created the modules)**

Run: `python3 -m pytest tests/unit/common/test_import_smoke.py -v`
Expected: PASS.

- [ ] **Step 4: Rewrite each import site with Serena `replace_content`**

For each of the 55 lines, repoint the imported name to its new module (`read_control`/`write_control` → `common.datastore_accessors`; `process_command` → `common.api_commands`; `default_*` → `common.defaults`; reboot/wifi/os info → `common.system`; `upgrade_settings`/`read_settings_file` → `common.settings_migration`). Work file-by-file; run that file's nearest test after each.

- [ ] **Step 5: Delete the temporary re-imports from `common/common.py`**

Remove the `from common.<module> import *` lines added in Task 8. `common/common.py` now contains only whatever genuinely remains (or becomes a small compatibility shim if any straggler names are easier left in place — note them explicitly in the commit).

- [ ] **Step 6: Full suite + import-smoke + grep guard**

Run: `python3 -m pytest -q`
Expected: PASS.
Run: `grep -rn "from common.common import" --include="*.py" . | grep -v '\.venv'`
Expected: only intentional lines remain (ideally none outside a documented shim).

- [ ] **Step 7: `/verify` the runtime surface**

Boot the web app and a controller cycle (per the repo's run/verify skill) to confirm imports resolve at runtime, not just under pytest.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(common): rewrite import sites to new common package modules

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage (Phase A section):** `process_command` dispatch table → Tasks 5–7; blob accessors → Task 2; file-read dedup + except syntax → Task 3; install-status dedup → Task 4; `is_not_blank` fix (test-gated) → Task 1; package split (`datastore_accessors`, `system`, `defaults`, `api_commands`, `settings_migration`) → Task 8; hard import rewrite → Task 9. All covered.
- **Placeholder scan:** the only intentionally-deferred content is the `process_command` branch *enumeration* (Task 5 Step 1) and the per-reader overlay logic (Task 3 Step 3), both of which require reading the live body at implementation time and are explicitly instructed as such, not hand-waved requirements.
- **Type consistency:** `_read_json_blob`/`_write_json_blob`, `_load_json_file`, `_get/_set_install_status`, `_manual_toggle`, `_COMMAND_DISPATCH`, `_cmd_*` names are used consistently across tasks.
- **Ordering:** in-file simplifications (1–7) precede the physical split (8) and import rewrite (9); `process_command` characterization (5) precedes its refactors (6–7).
