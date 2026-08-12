# Test Suite Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicated, redundant, and assertion-free tests from the PiFire suite, and add a detector that stops the same duplication from coming back.

**Architecture:** Every task is a behavior-preserving test refactor — production code is not touched anywhere in this plan. Each task follows the same safety pattern: capture the *current* per-test outcome, apply the refactor, then prove the same set of behaviors is still asserted (usually via `--collect-only` counts plus a targeted mutation check). The final task adds `tests/tools/duplicate_tests.py` plus a meta-test so the analysis that produced this plan becomes a permanent, runnable gate.

**Tech Stack:** Python 3.14, pytest 8 (`pytest-xdist`, `pytest-random-order`), Flask test client, PySide6/QtQuick, ruff; TypeScript with rstest (unit) and Playwright (e2e) under `web-react/`; `uv` for Python deps, `bun` for JS; `jj` (Jujutsu) for version control.

## Global Constraints

- **No production code changes.** This plan only edits files under `tests/` and `web-react/tests/`, plus the new `tests/tools/duplicate_tests.py`. If a refactor appears to require a production change, stop and report it — that is a finding, not a task.
- **Behavior preservation is the acceptance bar.** A refactor is correct only if the same assertions still run against the same inputs. Fewer test *functions* is fine; fewer asserted *behaviors* is a defect.
- **This repo is Jujutsu-backed (`.jj/` exists).** Use `jj` for all version control. Do NOT use raw `git commit`/`git rebase` — they can corrupt a colocated jj repo. Read the `superpowers:jujutsu` skill before the first commit.
- **Python floor: 3.14** (`requires-python = ">=3.14"` in `pyproject.toml`).
- **Line length: 120** (`ruff.toml`). `uv run ruff check .` is a merge gate and must pass.
- **Ruff is pinned `>=0.8.0,<0.16`** — do not upgrade it as a side effect of any task.
- **`F401` (unused imports) is ignored by ruff config.** Deleting a fixture often orphans a module-level import; ruff will NOT catch it. Remove orphaned imports by hand and verify with the grep command given in each task.
- **The default pytest run excludes slow tests**: `addopts = ["--random-order", "-n", "auto", "-m", "not slow"]`. Tests are order-randomized — never write a test that depends on another test having run.
- **All verification runs happen on the Linux test VM**, not on the macOS host. macOS produces host-specific failures (fonts, GL, platform probes) that are not real regressions. See Task 0.
- **Do not commit VM hostnames, usernames, or credentials** into any file in the repo. Connection details live only in the operator's shell environment.

---

## Environment

The Linux test VM's connection details are supplied by the operator as environment variables in the *local shell only*:

```bash
export PIFIRE_TEST_HOST=<host>     # supplied by operator; never commit
export PIFIRE_TEST_USER=<user>     # supplied by operator; never commit
```

Every remote command in this plan is written as:

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" '<command>'
```

Confirm both are set before starting any task:

```bash
test -n "$PIFIRE_TEST_HOST" && test -n "$PIFIRE_TEST_USER" && echo "env OK" || echo "SET THE ENV VARS FIRST"
```

---

### Task 0: Provision the Linux test VM and capture the green baseline

The VM is bare Fedora 44 (aarch64, 15 cores, 15 GB RAM): no Python, no git, no compiler, no fonts. Nothing downstream can be trusted until a full suite run is green here, because the *whole point* of this VM is that macOS fails tests for non-code reasons.

**Files:**
- Create: `docs/superpowers/plans/2026-08-12-test-suite-cleanup-baseline.txt` (untracked scratch — do NOT commit)

**Interfaces:**
- Produces: a synced checkout at `~/PiFire` on the VM, a `uv` venv, and `BASELINE_COUNT` — the number of tests collected by the default run. Every later task compares against `BASELINE_COUNT`.

- [ ] **Step 1: Install system packages**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'sudo dnf install -y \
  python3 python3-devel git gcc gcc-c++ cmake make pkgconf \
  dejavu-sans-fonts dejavu-sans-mono-fonts dejavu-serif-fonts liberation-fonts fontconfig \
  mesa-libGL mesa-libEGL libxkbcommon libxkbcommon-x11 xorg-x11-server-Xvfb \
  libX11 libXext libXrender libXi libXtst libXrandr libXcursor libXcomposite \
  alsa-lib nss atk at-spi2-atk cups-libs libdrm libgbm pango cairo \
  sqlite sqlite-devel bluez-libs bluez-libs-devel glib2-devel'
```

Expected: `Complete!`. `python3 --version` must report 3.14.x — the repo floor.

- [ ] **Step 2: Verify the Python floor and rebuild the font cache**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'python3 --version && fc-cache -fv >/dev/null && fc-list | wc -l'
```

Expected: `Python 3.14.6` (or later 3.14.x) and a font count greater than 0. A zero font count will fail the PIL-based display tests (`tests/ui/test_fonts_present.py`) for environmental reasons.

- [ ] **Step 3: Install `uv` and `bun`**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'curl -LsSf https://astral.sh/uv/install.sh | sh'
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'curl -fsSL https://bun.sh/install | bash'
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"; uv --version; bun --version'
```

Expected: both print versions. `web-react`'s `package.json` scripts invoke `bun`, so bun is required for the JS tasks (Task 16).

- [ ] **Step 4: Sync the working tree to the VM**

Use `rsync` from the macOS host, excluding build/venv/VCS noise. Re-run this exact command at the start of every subsequent verification step — it is the sync primitive this whole plan relies on.

```bash
rsync -az --delete \
  --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' \
  --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' \
  /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
```

Expected: completes with no error. `--delete` matters: without it, files you delete locally linger on the VM and keep passing.

- [ ] **Step 5: Create the venv and install dependencies**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv sync --group dev'
```

Expected: resolves and installs. If a package fails to build, it is almost always a missing `-devel` header — add it via `dnf` and re-run rather than skipping the dependency.

- [ ] **Step 6: Record the baseline collection count**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest --collect-only -q 2>&1 | tail -3'
```

Write the reported number down as `BASELINE_COUNT`. Expected: a count in the low thousands (the suite has 4,407 test functions before parametrize expansion).

- [ ] **Step 7: Run the full suite and confirm green**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest 2>&1 | tail -25' \
  | tee docs/superpowers/plans/2026-08-12-test-suite-cleanup-baseline.txt
```

Expected: `N passed` with 0 failures. `xvfb-run` supplies the X display the QtQuick and pygame tests need.

**If anything fails here, STOP and report it.** A pre-existing failure is a finding about the suite, not something to fix inside a cleanup task — and it destroys your ability to attribute later failures to your own edits.

- [ ] **Step 8: Confirm the JS suites are green too**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.bun/bin:$PATH"; cd ~/PiFire/web-react && bun install && bun run test 2>&1 | tail -15'
```

Expected: all rstest unit tests pass. (Playwright e2e is exercised only in Task 14; it needs `bunx playwright install --with-deps chromium` first.)

- [ ] **Step 9: Do NOT commit**

This task produces no repo changes. Confirm the tree is clean:

```bash
jj status
```

Expected: no modified tracked files. The baseline `.txt` is scratch — delete it or leave it untracked, but never `jj` it in.

---

### Task 1: Give the redundant MPC trace test its own premise

`tests/unit/mpc/test_update_mpc.py:417` and `:575` have **byte-identical bodies**. Both call `append_control_trace(_lifecycle_records())` and assert the same two arrays.

The names claim different things: `..._accepts_an_active_framed_session_without_a_terminal_partial` versus `..._accepts_skipped_numeric_revisions`. Reading `_lifecycle_records()` at `tests/unit/mpc/test_update_mpc.py:241` shows why both pass — the fixture emits revisions `0, 2, 7, 12`, which *are* non-contiguous. So the second test's premise is real but **entirely implicit in a shared fixture**. If someone renumbers `_lifecycle_records()` to `0, 1, 2, 3`, the test keeps passing under a name that is then a lie, and the skipped-revision behavior silently loses coverage.

The fix is not deletion — it is making the premise explicit so the test fails when its premise evaporates.

**Files:**
- Modify: `tests/unit/mpc/test_update_mpc.py:575-582`
- Test: `tests/unit/mpc/test_update_mpc.py` (this file *is* the test)

**Interfaces:**
- Consumes: `_lifecycle_records()` (`:241`), `load_trace_samples`, `append_control_trace`, `SESSION_ID`, `MpcUpdatePayload`, `AppliedOutputPayload` — all already imported in this module.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Confirm the two bodies really are identical**

```bash
cd /Users/dannyb/sources/PiFire && diff <(sed -n '417,424p' tests/unit/mpc/test_update_mpc.py | tail -6) <(sed -n '575,582p' tests/unit/mpc/test_update_mpc.py | tail -6)
```

Expected: no output (identical).

- [ ] **Step 2: Confirm the fixture's revisions are non-contiguous**

```bash
cd /Users/dannyb/sources/PiFire && sed -n '241,250p' tests/unit/mpc/test_update_mpc.py
```

Expected: you can read revisions `0`, `2`, `7`, `12` — a seed at 0, `_revision_records(2, ...)`, `_revision_records(7, ...)`, `_update(12, ...)`. Gaps at 1, 3-6, 8-11.

- [ ] **Step 3: Replace the redundant test with one that asserts its own premise**

Replace the whole function at `tests/unit/mpc/test_update_mpc.py:575-582` with:

```python
def test_load_trace_samples_accepts_skipped_numeric_revisions(ds):
    # The premise of this test lives in _lifecycle_records(), which emits
    # revisions 0, 2, 7, 12 -- deliberately non-contiguous. Assert that here,
    # so renumbering the fixture contiguously fails this test loudly instead
    # of quietly voiding it (which is what made this a duplicate of
    # test_load_trace_samples_accepts_an_active_framed_session_without_a_terminal_partial).
    records = _lifecycle_records()
    revisions = sorted(
        {
            record.payload.result_revision
            for record in records
            if isinstance(record.payload, (MpcUpdatePayload, AppliedOutputPayload))
        }
    )
    assert revisions == [0, 2, 7, 12], "fixture no longer exercises skipped revisions"
    assert revisions != list(range(revisions[0], revisions[-1] + 1))

    append_control_trace(records)

    _, temperature_c, combustion_load = load_trace_samples(session_id=SESSION_ID)

    np.testing.assert_allclose(temperature_c, (100.0, 110.0))
    np.testing.assert_allclose(combustion_load, (0.2, 0.25))
```

- [ ] **Step 4: Verify the imports it needs are present**

```bash
cd /Users/dannyb/sources/PiFire && sed -n '8,26p' tests/unit/mpc/test_update_mpc.py | grep -n "MpcUpdatePayload\|AppliedOutputPayload"
```

Expected: both names appear in the `from common.control_trace import (...)` block. If either is missing, add it to that existing import block — do not add a new import statement.

- [ ] **Step 5: Run the test and confirm it passes**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/mpc/test_update_mpc.py -p no:randomly -q 2>&1 | tail -5'
```

Expected: all pass.

- [ ] **Step 6: Prove the new assertion has teeth (mutation check)**

Temporarily edit `_lifecycle_records()` at `:246-248` to use contiguous revisions — change `_revision_records(2, ...)` to `_revision_records(1, ...)` — sync, and re-run.

Expected: `test_load_trace_samples_accepts_skipped_numeric_revisions` FAILS with `fixture no longer exercises skipped revisions`. **Revert the mutation immediately** and re-run to confirm green again. Without this step you have not shown the test does anything.

- [ ] **Step 7: Commit**

```bash
jj commit -m "test(mpc): make the skipped-revision trace test assert its own premise

test_load_trace_samples_accepts_skipped_numeric_revisions had a body
byte-identical to its neighbour; its premise lived implicitly in
_lifecycle_records()'s 0/2/7/12 numbering. Assert the non-contiguity
directly so renumbering the fixture fails loudly."
```

---

### Task 2: Hoist the `client` fixture into `tests/web/conftest.py`

29 files define their own `client` fixture. They fall into 8 textual variants, and two of those variants are **behaviorally different**:

- 16 files yield inside `with flask_app.test_client() as c:` — the request context is entered and exited properly.
- 4 files `return flask_app.test_client()` without ever entering the context manager.

That is a real difference in teardown, not a formatting inconsistency. `tests/web/conftest.py` already exists with 369 lines of shared fixtures and has no `client` — this is where it belongs.

**Scope note:** the 8 files whose fixture is `def client(api_files_client): return api_files_client` are **intentional per-file overrides** onto a genuinely different client. Pytest's override rules make a module-level `client` shadow the conftest one, so they keep working untouched. Leave them alone. This task handles the 16 identical yield-variants and the 3 in-directory `return` variants.

**Files:**
- Modify: `tests/web/conftest.py` (add fixture)
- Modify (delete local fixture + orphaned import): `tests/web/test_api_tuner.py`, `test_spa.py`, `test_api_update.py`, `test_api_metrics.py`, `test_spa_manifest.py`, `test_spa_caching.py`, `test_i2c_bus_discovery_parity.py`, `test_api_history.py`, `test_api_tuner_auto.py`, `test_api_wizard.py`, `test_api_probe_map.py`, `test_api_dismiss_warnings.py`, `test_api_model_evidence.py`, `test_api_pid_sp_learning.py`, `test_api_admin_maintenance.py`, `test_api_cmd_requires_post.py`, `test_api_settings_error_detail.py`, `test_api_settings_update.py`, `test_api_settings_controller_gate.py`

**Interfaces:**
- Produces: `client` fixture in `tests/web/conftest.py` — signature `client(ds)`, yields a `flask.testing.FlaskClient`. Task 3 depends on this existing.

- [ ] **Step 1: Record the exact pre-change test count for tests/web/**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/web --collect-only -q 2>&1 | tail -2'
```

Write this down as `WEB_COUNT`. It must be identical after the refactor — this task removes fixtures, never tests.

- [ ] **Step 2: Add the shared fixture to `tests/web/conftest.py`**

Append to `tests/web/conftest.py`:

```python
@pytest.fixture
def client(ds):
    """Flask test client over the isolated temp SQLite datastore from `ds`.

    Enters the app's test-request context and exits it on teardown. Nineteen
    modules each carried their own copy of this; four of them used a bare
    `return flask_app.test_client()`, which never enters the context manager
    and so never runs its teardown. This is the single definition.

    Modules needing a differently-seeded client (see test_api_mpc_calibration.py)
    or the files-API client (`api_files_client`) override `client` locally --
    a module-level fixture shadows this one, which is intended.
    """
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client
```

The import is function-local on purpose: `app` pulls in the whole Flask application at import time, and `conftest.py` is loaded for every test in the directory.

- [ ] **Step 3: Delete the 16 identical yield-variant fixtures**

In each of these files, delete the `@pytest.fixture` decorator and the `def client(...)` function body:

`tests/web/test_api_tuner.py:16`, `test_spa.py:9`, `test_api_update.py:12`, `test_api_metrics.py:16`, `test_spa_manifest.py:10`, `test_spa_caching.py:20`, `test_i2c_bus_discovery_parity.py:35`, `test_api_history.py:8`, `test_api_tuner_auto.py:14`, `test_api_wizard.py:23`, `test_api_probe_map.py:26`, `test_api_dismiss_warnings.py:8`, `test_api_model_evidence.py:49`, `test_api_pid_sp_learning.py:30`, `test_api_admin_maintenance.py:18`, `test_api_cmd_requires_post.py:71`

The bodies are one of these three shapes — all semantically identical to the new shared fixture:

```python
@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
```

```python
@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client
```

```python
@pytest.fixture
def client(ds):
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client
```

- [ ] **Step 4: Convert the 3 `return`-variant fixtures**

`tests/web/test_api_settings_error_detail.py:14`, `test_api_settings_update.py:34`, `test_api_settings_controller_gate.py:15` each hold:

```python
@pytest.fixture
def client(ds):
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()
```

Delete all three. They now inherit the conftest fixture, which additionally enters the request context. This is a strict improvement, but it *is* a behavior change — Step 7 verifies these three files specifically.

- [ ] **Step 5: Remove orphaned module-level imports**

Ruff ignores `F401`, so it will not flag these. For each of the 16 files from Step 3, check whether `flask_app` is still referenced:

```bash
cd /Users/dannyb/sources/PiFire && for f in tests/web/test_api_tuner.py tests/web/test_spa.py tests/web/test_api_update.py tests/web/test_api_metrics.py tests/web/test_spa_manifest.py tests/web/test_spa_caching.py tests/web/test_i2c_bus_discovery_parity.py tests/web/test_api_history.py tests/web/test_api_tuner_auto.py tests/web/test_api_wizard.py tests/web/test_api_probe_map.py tests/web/test_api_dismiss_warnings.py tests/web/test_api_model_evidence.py tests/web/test_api_pid_sp_learning.py tests/web/test_api_admin_maintenance.py tests/web/test_api_cmd_requires_post.py; do
  n=$(grep -c 'flask_app' "$f"); echo "$n $f"
done
```

Any file reporting `1` has only the now-orphaned `from app import app as flask_app` line — delete that import. Files reporting `2` or more still use it elsewhere (e.g. `flask_app.test_request_context`); leave those imports in place.

- [ ] **Step 6: Confirm no `client` fixtures remain outside the intended overrides**

```bash
cd /Users/dannyb/sources/PiFire && grep -rn "def client(" tests/web/
```

Expected: exactly 9 hits — the 8 `def client(api_files_client)` aliases, plus `test_api_mpc_calibration.py`'s heavily-seeded override. Nothing else.

- [ ] **Step 7: Verify the three converted files first, in isolation**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/web/test_api_settings_error_detail.py tests/web/test_api_settings_update.py tests/web/test_api_settings_controller_gate.py -q 2>&1 | tail -5'
```

Expected: all pass. These are the only files whose fixture semantics actually changed; if the added context-manager entry breaks anything, it surfaces here.

- [ ] **Step 8: Run the whole web suite and compare the count**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest tests/web -q 2>&1 | tail -5'
```

Expected: `WEB_COUNT` tests, all passing, exactly matching Step 1.

- [ ] **Step 9: Commit**

```bash
jj commit -m "test(web): hoist the duplicated client fixture into conftest

Nineteen modules carried a private copy. Four used a bare
return flask_app.test_client(), never entering the request context --
a real teardown difference, not just style. One definition now lives in
tests/web/conftest.py; the api_files_client aliases and the seeded
mpc_calibration client stay as deliberate overrides."
```

---

### Task 3: Fix the two remaining out-of-directory `client` fixtures

Two `client` fixtures sit outside `tests/web/` and cannot inherit the Task 2 conftest fixture: one takes a different upstream fixture, the other seeds heavily before constructing the client. Both are worth correcting; neither can simply be deleted.

**Files:**
- Modify: `tests/characterization/test_control_delta_seam.py:176`
- Modify: `tests/web/test_api_mpc_calibration.py:29`

**Interfaces:**
- Consumes: the `client` fixture added to `tests/web/conftest.py` in Task 2.

- [ ] **Step 1: Fix the characterization fixture's missing context entry**

`tests/characterization/test_control_delta_seam.py:176` currently reads:

```python
@pytest.fixture
def client(seeded):
    from app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()
```

It depends on `seeded`, not `ds`, so it stays local. Replace it with the context-managed form:

```python
@pytest.fixture
def client(seeded):
    # Local to this module because it hangs off `seeded`, not `ds`, so it
    # cannot share tests/web/conftest.py's client. Kept context-managed to
    # match it.
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as test_client:
        yield test_client
```

- [ ] **Step 2: Rebuild the mpc_calibration override on top of the shared fixture**

`tests/web/test_api_mpc_calibration.py:29` duplicates the client construction after doing its own seeding. Replace the fixture with one that seeds and then delegates:

```python
@pytest.fixture
def client(ds, client):  # noqa: F811 -- intentionally wraps the conftest fixture
    settings = read_settings()
    settings["globals"]["units"] = "F"
    settings["safety"]["maxtemp"] = 500
    settings["controller"] = {
        "selected": "mpc",
        "config": {"mpc": {"enable_grey_box": True, "enable_online_adaptation": True}},
    }
    write_settings(settings)
    control = read_control()
    control["mode"] = Mode.HOLD
    control.pop("mpc_calibration", None)
    write_control(control, WriteKind.OVERWRITE, origin="test")
    return client
```

**Important:** pytest does not allow a fixture to request a same-named fixture from a parent conftest by that name. If this raises a recursive-dependency error at collection, use the explicit rename instead — give the local fixture a distinct name and have the seeding depend on it:

```python
@pytest.fixture
def mpc_seeded(ds):
    settings = read_settings()
    settings["globals"]["units"] = "F"
    settings["safety"]["maxtemp"] = 500
    settings["controller"] = {
        "selected": "mpc",
        "config": {"mpc": {"enable_grey_box": True, "enable_online_adaptation": True}},
    }
    write_settings(settings)
    control = read_control()
    control["mode"] = Mode.HOLD
    control.pop("mpc_calibration", None)
    write_control(control, WriteKind.OVERWRITE, origin="test")


@pytest.fixture
def client(mpc_seeded, client):  # noqa: F811
    return client
```

If *that* also errors, keep the original fixture verbatim and note in a comment that it is a deliberate standalone; a working duplicate beats a broken abstraction.

- [ ] **Step 3: Verify both files**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/characterization/test_control_delta_seam.py tests/web/test_api_mpc_calibration.py -q 2>&1 | tail -6'
```

Expected: all pass, same count as before the change.

- [ ] **Step 4: Commit**

```bash
jj commit -m "test: context-manage the two out-of-directory client fixtures"
```

---

### Task 4: Deduplicate the cookfile/recipes asset-test helpers

`tests/web/test_api_files_cookfile_assets.py` and `tests/web/test_api_files_recipes_assets.py` are near-clones: four helpers are copy-pasted between them (`static_img_tmp_cleanup` 12 lines, `_png` 4 lines, `_upload` 6 lines, `_read_member` 6 lines). `_read_member` has a *third* copy in `test_api_files_cookfile_write.py:226`.

**Files:**
- Create: `tests/web/_asset_helpers.py`
- Modify: `tests/web/test_api_files_cookfile_assets.py`, `tests/web/test_api_files_recipes_assets.py`, `tests/web/test_api_files_cookfile_write.py`

**Interfaces:**
- Produces: module `tests/web/_asset_helpers.py` exporting `png_bytes()`, `upload(...)`, `read_member(...)`, and the `static_img_tmp_cleanup` fixture body as a reusable function.

- [ ] **Step 1: Read all copies before touching anything**

```bash
cd /Users/dannyb/sources/PiFire && sed -n '34,80p' tests/web/test_api_files_cookfile_assets.py && echo "=====" && sed -n '26,72p' tests/web/test_api_files_recipes_assets.py && echo "=====" && sed -n '224,234p' tests/web/test_api_files_cookfile_write.py
```

Diff them mentally. **If any two copies differ in substance** (different MIME type, different member path, different cleanup root), they are not duplicates — stop, and dedupe only the ones that genuinely match. A detector flagging "duplicate" is a hypothesis, not a fact.

- [ ] **Step 2: Create the shared module**

Create `tests/web/_asset_helpers.py`. Fill each function body with the *verbatim* body you read in Step 1 — do not retype from memory:

```python
"""Helpers shared by the cookfile- and recipes-asset test modules.

Both suites upload the same one-pixel PNG through the same multipart shape
and read members back out of the same archive format; the two modules each
carried a private copy of all four helpers. Named without a `test_` prefix so
pytest does not collect this module.
"""
```

Then add `png_bytes()`, `upload(...)`, `read_member(...)`, and `make_static_img_tmp_cleanup()` (a factory returning the fixture function), each with the body copied from Step 1.

- [ ] **Step 3: Rewrite the three modules to import from it**

In each file, delete the local helper definitions and add near the existing imports:

```python
from tests.web._asset_helpers import png_bytes, read_member, upload
```

Update call sites: `_png()` → `png_bytes()`, `_upload(...)` → `upload(...)`, `_read_member(...)` → `read_member(...)`.

For the `static_img_tmp_cleanup` fixture, keep a one-line fixture in each module that delegates to the factory, since fixtures must be declared where pytest can see them.

- [ ] **Step 4: Confirm no helper copies remain**

```bash
cd /Users/dannyb/sources/PiFire && grep -rn "def _png\|def _upload\|def _read_member" tests/web/
```

Expected: no output.

- [ ] **Step 5: Verify**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/web/test_api_files_cookfile_assets.py tests/web/test_api_files_recipes_assets.py tests/web/test_api_files_cookfile_write.py -q 2>&1 | tail -5'
```

Expected: same test count as before, all passing.

- [ ] **Step 6: Commit**

```bash
jj commit -m "test(web): share the asset-upload helpers between cookfile and recipes"
```

---

### Task 5: Collapse the ADS1115/ADS1015 Adafruit clone classes

In `tests/unit/probes/test_ads1115_probes.py`, `TestADS1115Adafruit` (`:280`) and `TestADS1015Adafruit` (`:375`) differ **only** in their `_load()` method — which chip module they import and which fake they install. Five test methods are byte-identical between them; four were confirmed identical including all literals.

**Files:**
- Modify: `tests/unit/probes/test_ads1115_probes.py:275-475`

**Interfaces:**
- Consumes: `_install_fake_adafruit_ads1x15`, `_probe_info`, `_device_info`, `EXPECTED_TEMP_F`, `EXPECTED_TR` — module-level, already defined above line 275.

- [ ] **Step 1: Record the current test IDs**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/probes/test_ads1115_probes.py --collect-only -q 2>&1 | head -30'
```

Save this list. After the refactor, every *behavior* must still be covered — the test IDs will change (they gain a parametrize suffix), but the count of Adafruit-class tests must not drop.

- [ ] **Step 2: Replace the two classes with a parametrized base**

Replace everything from line 275 (the `# ====` banner above `TestADS1115Adafruit`) through the end of `TestADS1015Adafruit` with:

```python
# ===========================================================================
# probes/ads1115_adafruit.py and probes/ads1015_adafruit.py
#
# The two modules are the same driver over a different Adafruit chip class,
# and their tests were byte-identical apart from _load(). Parametrizing the
# class keeps both chips covered while there is one copy of each assertion --
# and a new chip variant now costs one tuple, not a cloned class.
# ===========================================================================


@pytest.mark.parametrize(
    ("module_name", "chip_module", "chip_class"),
    [
        ("probes.ads1115_adafruit", "ads1115", "ADS1115"),
        ("probes.ads1015_adafruit", "ads1015", "ADS1015"),
    ],
    ids=["ads1115", "ads1015"],
)
class TestAdafruitADS:
    def _load(self, monkeypatch, module_name, chip_module, chip_class, voltages, fail_channels=()):
        _install_fake_adafruit_ads1x15(monkeypatch, chip_module, chip_class, voltages, fail_channels)
        probe = importlib.import_module(module_name)

        importlib.reload(probe)  # bind the fake adafruit_ads1x15
        monkeypatch.setattr(probe, "open_i2c_bus", lambda bus: "FAKE_I2C_BUS")
        return probe
```

Then move each test method in, adding the three parametrize arguments to its signature and threading them through `_load`. For example, the first one becomes:

```python
    def test_read_all_ports_maps_known_adc_voltage_to_temperature(
        self, monkeypatch, module_name, chip_module, chip_class
    ):
        # 1.5V -> AnalogIn.voltage -> math.floor(1.5*1000) = 1500mV, matching
        # the reference case computed in test_base.py.
        probe = self._load(monkeypatch, module_name, chip_module, chip_class, {0: 1.5, 1: 1.5, 2: 1.5})
        probe_info = _probe_info([("ADC0", "Probe1", "Primary"), ("ADC1", "Probe2", "Food"), ("ADC2", "Probe3", "Aux")])
        obj = probe.ReadProbes(probe_info, _device_info(), "F")

        result = obj.read_all_ports(obj.output_data)

        assert result["primary"]["Probe1"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["tr"]["Probe1"] == EXPECTED_TR
        assert result["food"]["Probe2"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
        assert result["aux"]["Probe3"] == pytest.approx(EXPECTED_TEMP_F, abs=1e-6)
```

Carry over the remaining methods the same way: `test_read_voltage_error_returns_zero`, `test_adsdevice_opens_bus_via_factory`, `test_init_device_defaults`, `test_init_device_failure_logs_and_reraises`. Copy each body verbatim from the ADS1115 class.

- [ ] **Step 3: Check `test_adsdevice_opens_bus_via_factory` for a real difference**

```bash
cd /Users/dannyb/sources/PiFire && diff <(sed -n '313,330p' tests/unit/probes/test_ads1115_probes.py) <(sed -n '406,423p' tests/unit/probes/test_ads1115_probes.py)
```

This method was NOT in the byte-identical set, so it may legitimately differ per chip. If the diff shows a substantive difference, keep it as two separate non-parametrized tests outside the shared class rather than forcing it into the parametrization.

- [ ] **Step 4: Confirm `importlib` is imported**

```bash
cd /Users/dannyb/sources/PiFire && grep -n "^import importlib" tests/unit/probes/test_ads1115_probes.py
```

Expected: one hit. The original `_load` used `import probes.X as probe` statements; the new one uses `importlib.import_module`, so `importlib` must be at module scope (it already is — the old code called `importlib.reload`).

- [ ] **Step 5: Verify both chips are still exercised**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/probes/test_ads1115_probes.py -v 2>&1 | grep -c "ads1015"'
```

Expected: a non-zero count — proof the ads1015 parametrization actually runs rather than being silently skipped.

- [ ] **Step 6: Run the file and the probes package**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/probes -q 2>&1 | tail -5'
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
jj commit -m "test(probes): parametrize the Adafruit ADS clone classes over both chips"
```

---

### Task 6: Collapse the duplicated platform-contract tests

`tests/unit/platform/test_prototype_system.py:14` and `tests/unit/platform/test_raspberry_pi_system.py:23` hold byte-identical `test_supported_commands_lists_all_nine`, and `:37`/`:39` hold byte-identical `test_check_alive_ok`. These assert a *shared platform contract*, so one parametrized test covers both — and catches a third platform drifting, which the current copies would not.

**Files:**
- Create: `tests/unit/platform/test_platform_contract.py`
- Modify: `tests/unit/platform/test_prototype_system.py`, `tests/unit/platform/test_raspberry_pi_system.py`

**Interfaces:**
- Produces: `tests/unit/platform/test_platform_contract.py` covering the cross-platform contract. The per-platform files keep everything platform-specific.

- [ ] **Step 1: Create the shared contract test**

Create `tests/unit/platform/test_platform_contract.py`:

```python
"""The parts of the GrillPlatform system API that every platform must satisfy
identically. These assertions were copy-pasted per platform; a shared contract
test also catches a NEW platform that quietly omits a command."""

import logging
import sys
import types

import pytest

# raspberry_pi_all imports `from rpi_hardware_pwm import HardwarePWM` at module
# load; that package is Pi-only and absent in the test venv. Stub it so the
# module imports on a generic host. (gpiozero IS installed.)
if "rpi_hardware_pwm" not in sys.modules:
    _stub = types.ModuleType("rpi_hardware_pwm")
    _stub.HardwarePWM = type("HardwarePWM", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["rpi_hardware_pwm"] = _stub

import grillplat.prototype as proto  # noqa: E402
import grillplat.raspberry_pi_all as rpi  # noqa: E402

REQUIRED_COMMANDS = (
    "check_throttled",
    "check_wifi_quality",
    "check_cpu_temp",
    "supported_commands",
    "check_alive",
    "scan_bluetooth",
    "os_info",
    "network_info",
    "hardware_info",
)


def _bare(module, logger_name):
    # System methods only need self.logger; skip __init__ (no GPIO on host).
    obj = object.__new__(module.GrillPlatform)
    obj.logger = logging.getLogger(logger_name)
    return obj


@pytest.fixture(params=[(proto, "test.prototype"), (rpi, "test.rpi")], ids=["prototype", "raspberry_pi"])
def platform(request):
    module, logger_name = request.param
    return _bare(module, logger_name)


def test_supported_commands_lists_all_nine(platform):
    cmds = platform.supported_commands([])["data"]["supported_cmds"]
    for name in REQUIRED_COMMANDS:
        assert name in cmds


def test_check_alive_ok(platform):
    assert platform.check_alive([]) == {
        "result": "OK",
        "message": "The control script is running.",
        "data": {},
    }
```

- [ ] **Step 2: Delete the four duplicated tests**

Delete `test_supported_commands_lists_all_nine` and `test_check_alive_ok` from **both** `tests/unit/platform/test_prototype_system.py` and `tests/unit/platform/test_raspberry_pi_system.py`. Leave every other test in those files alone — `test_check_throttled_stub_all_false` and `test_os_info_ok_shape` are genuinely platform-specific.

- [ ] **Step 3: Verify the deletion was surgical**

```bash
cd /Users/dannyb/sources/PiFire && grep -rn "def test_supported_commands_lists_all_nine\|def test_check_alive_ok" tests/unit/platform/
```

Expected: exactly 2 hits, both in the new `test_platform_contract.py`.

- [ ] **Step 4: Prove the contract test actually runs both platforms**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/platform/test_platform_contract.py -v 2>&1 | grep -E "prototype|raspberry_pi"'
```

Expected: 4 lines — 2 tests × 2 platforms — all PASSED.

- [ ] **Step 5: Run the platform package**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/platform -q 2>&1 | tail -5'
```

Expected: all pass; total count unchanged (2 tests removed from each of 2 files = 4 removed; 2 tests × 2 params = 4 added).

- [ ] **Step 6: Commit**

```bash
jj commit -m "test(platform): extract the shared GrillPlatform contract test

test_supported_commands_lists_all_nine and test_check_alive_ok were
byte-identical across prototype and raspberry_pi. One parametrized
contract test covers both and will catch a third platform that drifts."
```

---

### Task 7: Test the distance base class once, not three times through transports

`_level_from_distance_cm` lives in `distance/_sampled_base.py:321` on `SampledHopperLevel`. Every transport inherits it: `ToFHopperLevel`, `SerialToFHopperLevel`, and `hcsr04.HopperLevel`. The same percentage-interpolation triplet (at-full → 100, at-empty → 0, between → interpolated) is asserted separately in three files, each paying full fixture and threading cost to reach identical inherited code. The `_await_sample` helper is copy-pasted across all three too.

**Files:**
- Create: `tests/unit/distance/test_sampled_base_levels.py`
- Modify: `tests/unit/distance/test_tof_base.py`, `tests/unit/distance/test_hcsr04.py`, `tests/unit/distance/test_serial_tof_base.py`

**Interfaces:**
- Produces: direct unit coverage of `SampledHopperLevel._level_from_distance_cm`.

- [ ] **Step 1: Read the method under test**

```bash
cd /Users/dannyb/sources/PiFire && sed -n '321,360p' distance/_sampled_base.py
```

Note its exact signature and how it reads `full`/`empty`. Write the new test against **that** signature — do not assume it takes centimeters if it takes millimetres.

- [ ] **Step 2: Read all three existing triplets**

```bash
cd /Users/dannyb/sources/PiFire && sed -n '137,165p' tests/unit/distance/test_tof_base.py && echo "=====" && sed -n '164,190p' tests/unit/distance/test_hcsr04.py && echo "=====" && grep -n "def test_reading_at_or_below_full_is_100_percent" -A 24 tests/unit/distance/test_serial_tof_base.py
```

Confirm the boundary values used (full=4 cm, empty=22 cm) and the expected percentages.

- [ ] **Step 3: Create the direct unit test**

Create `tests/unit/distance/test_sampled_base_levels.py`:

```python
"""Direct coverage of SampledHopperLevel._level_from_distance_cm.

Every transport (ToF, serial ToF, HC-SR04) inherits this method unchanged, so
the percentage mapping was being asserted three times through three transport
fixtures. It is arithmetic on the base class -- test it there once, and let
each transport module keep only the tests about ITS transport.
"""

import pytest

from distance._sampled_base import SampledHopperLevel


def _levels(full_cm, empty_cm):
    obj = object.__new__(SampledHopperLevel)
    obj.full = full_cm
    obj.empty = empty_cm
    return obj


@pytest.mark.parametrize(
    ("distance_cm", "expected"),
    [
        (4.0, 100),  # exactly at full
        (2.0, 100),  # closer than full clamps to 100
        (22.0, 0),  # exactly at empty
        (30.0, 0),  # beyond empty clamps to 0
        (13.0, 50),  # midpoint interpolates
    ],
    ids=["at-full", "above-full", "at-empty", "below-empty", "midpoint"],
)
def test_level_from_distance_cm(distance_cm, expected):
    assert _levels(full_cm=4, empty_cm=22)._level_from_distance_cm(distance_cm) == expected
```

**If `object.__new__` plus manual attribute assignment does not work** (e.g. the method reads an attribute set only in `__init__`), read `__init__` and set the attributes it actually needs. Do not fall back to instantiating a transport — that would recreate the coupling this task removes.

- [ ] **Step 4: Verify the new test passes and the midpoint value is right**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/distance/test_sampled_base_levels.py -v 2>&1 | tail -12'
```

Expected: 5 passed. If `midpoint` fails, the real formula is not linear on that range — fix the *expected value in the test* to match the production behavior, and note it in a comment. Do not change production code.

- [ ] **Step 5: Reduce each transport file to ONE end-to-end level assertion**

In each of `test_tof_base.py`, `test_hcsr04.py`, `test_serial_tof_base.py`, delete `test_reading_at_empty_is_0_percent` and `test_reading_between_full_and_empty_is_interpolated`, and **keep** `test_reading_at_or_below_full_is_100_percent`. Add a comment above the survivor in each file:

```python
# One end-to-end case per transport is deliberate: it proves this transport's
# reading actually reaches the shared level maths. The maths itself is covered
# directly in test_sampled_base_levels.py.
```

This keeps proof that each transport is wired to the base class, without re-testing the arithmetic three times.

- [ ] **Step 6: Verify the distance package**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/distance -q 2>&1 | tail -5'
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
jj commit -m "test(distance): cover the shared level maths once on the base class

The 100%/0%/interpolated triplet was asserted through all three transports
against identical inherited code. Tested directly on SampledHopperLevel now;
each transport keeps one end-to-end case proving it reaches that code."
```

---

### Task 8: Give the assertion-free display tests real assertions

Three tests in `tests/ui/test_fixed_drivers_methods.py` call production rendering code and assert **nothing** — they pass as long as no exception is raised, while their names promise verified behavior. They were written to reach coverage branches.

**Files:**
- Modify: `tests/ui/test_fixed_drivers_methods.py:280`, `:852`, `:947`

**Interfaces:**
- Consumes: `_make_ssd1306()`, `_make_ili9341f()`, `_make_prototype()`, `_drive_flex_input_and_menu_coverage()`, `SAMPLE_STATUS_DATA`, `SAMPLE_IN_DATA` — all already in the module.

- [ ] **Step 1: Find out what these drivers expose that is observable**

```bash
cd /Users/dannyb/sources/PiFire && grep -n "def _make_ssd1306\|def _make_prototype\|def _make_ili9341f" -A 20 tests/ui/test_fixed_drivers_methods.py | head -70
```

Look for a fake display surface, a recorded draw-call list, or an in-memory PIL image. **What you find determines what you can assert** — the rest of this task depends on it.

- [ ] **Step 2: If the harness records draw calls or exposes an image, assert on it**

For `test_ssd1306_display_current_all_outpins_off_and_no_notify` at `:280`, the point of the test is that nothing lights up. Assert that:

```python
def test_ssd1306_display_current_all_outpins_off_and_no_notify():
    # Cover the "nothing lit up" branches (fan/igniter/auger False, no
    # notify_data requiring a bell icon) -- and assert the outcome, rather
    # than only that rendering did not raise.
    mod, d = _make_ssd1306()
    status = dict(SAMPLE_STATUS_DATA)
    status["outpins"] = {"fan": False, "igniter": False, "auger": False}
    status["notify_data"] = []

    d._display_current(SAMPLE_IN_DATA, status)

    lit = _rendered_icons(d)
    assert lit == set(), f"nothing should be lit, got {lit}"
```

You must write `_rendered_icons(d)` against whatever Step 1 revealed. If the harness exposes a PIL image, a defensible alternative is to render both states and assert they differ:

```python
def test_ssd1306_display_current_all_outpins_off_and_no_notify():
    mod, d = _make_ssd1306()
    status = dict(SAMPLE_STATUS_DATA)
    status["outpins"] = {"fan": False, "igniter": False, "auger": False}
    status["notify_data"] = []
    d._display_current(SAMPLE_IN_DATA, status)
    dark = _snapshot(d)

    status["outpins"] = {"fan": True, "igniter": True, "auger": True}
    d._display_current(SAMPLE_IN_DATA, status)
    lit = _snapshot(d)

    assert dark != lit, "the all-off render must differ from the all-on render"
```

- [ ] **Step 3: Apply the same treatment to the other two**

`test_ili9341f_event_detect_and_menu_touch_branches` at `:852` and `test_prototype_display_status_low_and_mid_hopper_levels` at `:947`. For the latter, the two hopper branches (<25 and 25-70) must produce *different* output — that is exactly the behavior the name claims:

```python
def test_prototype_display_status_low_and_mid_hopper_levels(monkeypatch):
    mod, d = _make_prototype()
    status_data = {
        "mode": "Stop",
        "notify_data": [],
        "outpins": {"fan": False, "igniter": False, "auger": False},
        "hopper_level": 10,
    }
    in_data = {"probe_history": {"primary": {}}, "primary_setpoint": 0, "notify_targets": {}}

    d.display_status(in_data, status_data)  # hopper_level < 25 branch
    low = _snapshot(d)

    status_data["hopper_level"] = 50
    d.display_status(in_data, status_data)  # 25 <= hopper_level < 70 branch
    mid = _snapshot(d)

    assert low != mid, "the low and mid hopper branches must render differently"
```

- [ ] **Step 4: If a driver genuinely exposes nothing observable, rename instead**

If Step 1 shows there is no way to observe the output, do NOT invent a fake. Rename the test to state what it actually verifies and say so:

```python
def test_prototype_display_status_does_not_raise_for_low_and_mid_hopper_levels(monkeypatch):
    """The prototype driver renders to nothing observable, so this is a
    smoke test over the two hopper branches -- not a rendering assertion.
    Named to say so."""
```

An honest name is worth more than a fake assertion. Record in the task report which tests got real assertions and which got honest renames.

- [ ] **Step 5: Prove the new assertions can fail (mutation check)**

For each test you gave a real assertion, temporarily break the branch it covers in the production driver (e.g. make the hopper-level threshold always take one branch), sync, and re-run.

Expected: the corresponding test FAILS. **Revert the production mutation immediately.** A test you cannot make fail on demand has not been fixed.

- [ ] **Step 6: Run the UI suite**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest tests/ui -q 2>&1 | tail -5'
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
jj commit -m "test(ui): assert on render output instead of only not-raising"
```

---

### Task 9: Parametrize the Bluetooth probe close-loop tests

`tests/unit/probes/test_bt_probe_close.py:119`, `:158`, `:214` are three assertion-free tests with the same structure across three device classes. They prove the loops return, but assert nothing — and they would also pass if the loops returned for the wrong reason.

**Files:**
- Modify: `tests/unit/probes/test_bt_probe_close.py`

**Interfaces:**
- Consumes: the `ibbq`, `meater`, `meater_exp` fixtures and `_bare()` — already in the module.

- [ ] **Step 1: Read the three tests and their fixtures**

```bash
cd /Users/dannyb/sources/PiFire && sed -n '100,225p' tests/unit/probes/test_bt_probe_close.py
```

Note that each sets a different extra attribute before the call (`hardware_id = None` for meater, `address = None` for meater_exp, nothing for ibbq).

- [ ] **Step 2: Replace the three with one parametrized test that asserts**

```python
@pytest.mark.parametrize(
    ("fixture_name", "device_attr", "extra_attrs"),
    [
        ("ibbq", "iBBQ_Device", {}),
        ("meater", "Meater_Device", {"hardware_id": None}),
        ("meater_exp", "Meater_Device", {"address": None}),
    ],
    ids=["ibbq", "meater", "meater_exp"],
)
def test_loops_exit_promptly_when_closing(request, fixture_name, device_attr, extra_attrs):
    """Both loops must return promptly once the flag is set -- with it already
    set they must not touch Bluetooth at all, which is what lets these run in
    a test at all. Asserting the flag is still set on return is what
    distinguishes 'exited because closing' from 'exited for some other reason'."""
    module = request.getfixturevalue(fixture_name)
    device = _bare(getattr(module, device_attr))
    device._closing.set()
    for name, value in extra_attrs.items():
        setattr(device, name, value)

    device._setup_device()
    device._sensing_loop()

    assert device._closing.is_set(), "the loops must not clear the closing flag on the way out"
```

- [ ] **Step 3: Prove it discriminates (mutation check)**

Temporarily add `self._closing.clear()` at the top of `_sensing_loop` in `probes/bt_ibbq.py`, sync, and re-run.

Expected: the `ibbq` parametrization FAILS. **Revert immediately.** If it still passes, the assertion is not reaching the code — investigate before continuing.

- [ ] **Step 4: Verify**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/probes/test_bt_probe_close.py -v 2>&1 | tail -10'
```

Expected: 3 parametrizations, all pass.

- [ ] **Step 5: Commit**

```bash
jj commit -m "test(probes): parametrize the BT close-loop tests and give them an assertion"
```

---

### Task 10: Parametrize the runtime logic families

Two families in `tests/unit/runtime/`: 8 tests in `test_logic_pwm.py` and 5 in `test_logic_smartstart.py`, each differing only in input literals.

**Judgment call that matters here:** every `test_logic_pwm.py` test carries a distinct comment explaining *which branch* it covers. That prose is the most valuable thing in the file. A naive parametrize deletes it. Preserve each explanation as a comment on its `pytest.param` row.

**Files:**
- Modify: `tests/unit/runtime/test_logic_pwm.py`, `tests/unit/runtime/test_logic_smartstart.py`

- [ ] **Step 1: Replace the 8 `test_logic_pwm.py` tests with one parametrized test**

```python
PWM_SETTINGS = {
    "min_duty_cycle": 20,
    "max_duty_cycle": 100,
    "temp_range_list": [5, 15, 30],
    "profiles": [{"duty_cycle": 50}, {"duty_cycle": 70}, {"duty_cycle": 90}],
}


@pytest.mark.parametrize(
    ("ptemp", "profiles", "expected"),
    [
        # ptemp > setpoint (strict >) short-circuits to min_duty_cycle
        # regardless of temp_range_list/profiles.
        pytest.param(230, None, 20, id="over-setpoint-returns-min"),
        # ptemp == setpoint means (setpoint - ptemp) == 0, which is <= the
        # first range entry, so profile 0 is used (not the over-setpoint
        # branch, since that requires strict >).
        pytest.param(225, None, 50, id="at-setpoint-uses-profile-zero"),
        # setpoint - ptemp = 10, which is > temp_range_list[0]=5 but <= [1]=15,
        # so profile index 1 (duty_cycle=70) is used.
        pytest.param(215, None, 70, id="matches-early-profile"),
        # setpoint - ptemp == temp_range_list[i] exactly must match index i
        # (uses <=, not <).
        pytest.param(210, None, 70, id="boundary-uses-le-match"),
        # Matched profile's duty_cycle (10) is below min_duty_cycle (20), so
        # the clamp raises it to min_duty_cycle. Clamp order is max-then-min,
        # so the min clamp must win here.
        pytest.param(
            225,
            [{"duty_cycle": 10}, {"duty_cycle": 70}, {"duty_cycle": 90}],
            20,
            id="clamps-below-min",
        ),
        # Matched profile's duty_cycle (150) is above max_duty_cycle (100), so
        # the clamp lowers it to max_duty_cycle.
        pytest.param(
            225,
            [{"duty_cycle": 150}, {"duty_cycle": 70}, {"duty_cycle": 90}],
            100,
            id="clamps-above-max",
        ),
        # setpoint - ptemp = 50, larger than every entry in temp_range_list, so
        # the loop falls through all comparisons and the last-index fallthrough
        # branch returns max_duty_cycle directly (bypassing profiles/clamps).
        pytest.param(175, None, 100, id="fallthrough-beyond-all-ranges-returns-max"),
        # setpoint - ptemp == temp_range_list[-1] exactly still matches via <=
        # on the last iteration, using that profile's clamped duty_cycle rather
        # than the fallthrough max_duty_cycle.
        pytest.param(195, None, 90, id="last-boundary-uses-profile-not-fallthrough"),
    ],
)
def test_hold_duty_cycle(ptemp, profiles, expected):
    pwm_settings = dict(PWM_SETTINGS)
    if profiles is not None:
        pwm_settings["profiles"] = profiles

    assert hold_duty_cycle(setpoint=225, ptemp=ptemp, pwm_settings=pwm_settings) == expected
```

- [ ] **Step 2: Replace the 5 `test_logic_smartstart.py` tests**

```python
@pytest.mark.parametrize(
    ("startup_temp", "expected"),
    [
        pytest.param(40, 0, id="below-first-range"),
        pytest.param(60, 1, id="between-ranges"),
        pytest.param(50, 1, id="equal-to-first-boundary"),
        # startup_temp == temp_range_list[i] must NOT match (strict <), so it
        # falls through to the next index (or len() if it's the last one).
        pytest.param(70, 2, id="equal-to-middle-boundary-falls-through"),
        pytest.param(90, 3, id="equal-to-last-boundary"),
    ],
)
def test_select_profile(startup_temp, expected):
    temp_range_list = [50, 70, 90]
    assert select_profile(startup_temp, temp_range_list) == expected
```

- [ ] **Step 3: Confirm `pytest` is imported in both files**

```bash
cd /Users/dannyb/sources/PiFire && grep -c "^import pytest" tests/unit/runtime/test_logic_pwm.py tests/unit/runtime/test_logic_smartstart.py
```

Expected: `1` for each. Add the import if a file reports `0` — these files may not have needed pytest before.

- [ ] **Step 4: Verify the case count is preserved**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/runtime/test_logic_pwm.py tests/unit/runtime/test_logic_smartstart.py -v 2>&1 | tail -20'
```

Expected: 8 + 5 = 13 test cases, all passing — same as before, with readable ids.

- [ ] **Step 5: Commit**

```bash
jj commit -m "test(runtime): parametrize the PWM and smartstart profile tables

Branch-explaining comments are preserved per pytest.param row -- they are
the most valuable content in these files."
```

---

### Task 11: Parametrize the notify families

Three files, five families: `test_wled_handler.py` (7 + 3), `test_notifications_events.py` (4 + 4), `test_mqtt_handler.py` (3 + 3).

**Watch out:** in `test_wled_handler.py`, six of the seven preset tests assert `color` and `effect`, but `test_probe_alarm_is_red_blink_slower` asserts `color` and **`speed`**. A parametrization that assumes `effect` everywhere silently drops that assertion. Parametrize over an expected-kwargs *dict* instead.

Likewise in `test_notifications_events.py`, one family asserts `body.startswith(...)` and the other asserts `body ==`. Merging them needs an explicit exact-vs-prefix flag.

**Files:**
- Modify: `tests/unit/notify/test_wled_handler.py`, `tests/unit/notify/test_notifications_events.py`, `tests/unit/notify/test_mqtt_handler.py`

- [ ] **Step 1: Replace the 7 WLED preset tests**

```python
@pytest.mark.parametrize(
    ("preset", "expected_kwargs"),
    [
        ("booting", {"color": "white", "effect": "breathe"}),
        ("preheat", {"color": "orange", "effect": "breathe"}),
        ("cooldown", {"color": "orange", "effect": "fade"}),
        ("target_reached", {"color": "green", "effect": "solid"}),
        # This one pins speed, not effect -- the odd row out.
        ("probe_alarm", {"color": "red", "speed": 100}),
        ("low_pellets", {"color": "yellow", "effect": "breathe"}),
        ("error", {"color": "red", "effect": "solid"}),
    ],
)
def test_send_suggested_preset(preset, expected_kwargs):
    handler = _make_handler()
    handler.send_direct_command = MagicMock()

    handler.send_suggested_preset(preset, {})

    kwargs = handler.send_direct_command.call_args.kwargs
    for key, value in expected_kwargs.items():
        assert kwargs[key] == value, f"{preset}: {key}"
```

- [ ] **Step 2: Replace the 3 WLED control-mode tests**

```python
@pytest.mark.parametrize(
    ("use_profiles", "use_suggested_presets", "expected"),
    [
        (True, True, "profiles"),  # profiles take priority when both are on
        (False, True, "suggested"),
        (False, False, "traditional"),
    ],
)
def test_get_control_mode(use_profiles, use_suggested_presets, expected):
    handler = _make_handler()
    handler.config = {"use_profiles": use_profiles, "use_suggested_presets": use_suggested_presets}
    assert handler.get_control_mode() == expected
```

- [ ] **Step 3: Merge the two notification-event families into one table**

```python
@pytest.mark.parametrize(
    ("event", "title", "body", "exact", "channel", "query_args"),
    [
        pytest.param(
            "Grill_Error_01",
            "Grill Error!",
            # Behavior change: "exceded" -> "exceeded" typo fix.
            "Grill exceeded maximum temperature limit of 550F! Shutting down. ",
            False,
            "pifire_error_alerts",
            {"value1": "550"},
            id="grill-error-01",
        ),
        pytest.param(
            "Grill_Error_02",
            "Grill Error!",
            "Grill temperature dropped below minimum startup temperature of 100F!"
            " Shutting down to prevent firepot overload. ",
            False,
            "pifire_error_alerts",
            {"value1": "100"},
            id="grill-error-02",
        ),
        pytest.param(
            "Grill_Error_03",
            "Grill Error!",
            # No trailing <now> suffix -- exact match.
            "Grill temperature dropped below minimum startup temperature of 100F!"
            " Starting a re-ignite attempt, per user settings.",
            True,
            "pifire_error_alerts",
            {"value1": "100"},
            id="grill-error-03",
        ),
        pytest.param(
            "Recipe_Step_Message",
            "Recipe Message",
            "Flip the brisket. ",
            False,
            "pifire_recipe_message",
            {"value1": "Flip the brisket. "},
            id="recipe-step",
        ),
        pytest.param(
            "Timer_Expired",
            "Timer Complete",
            "Your timer has expired, time to check your cook!",
            True,
            "pifire_timer_alerts",
            {"value1": "Your timer has expired."},
            id="timer-expired",
        ),
        pytest.param(
            "Test_Notify",
            "Test Notification",
            "This is a test notification from PiFire.",
            True,
            "pifire_test_message",
            {"value1": "This is a test notification from PiFire."},
            id="test-notify",
        ),
        pytest.param(
            "Control_Process_Stopped",
            "Control Process Stopped!",
            "The control process has encountered an issue and has been stopped. "
            "Check on your grill as soon as possible to prevent damage!",
            True,
            "pifire_error_alerts",
            {"value1": "Control Process Stopped"},
            id="control-process-stopped",
        ),
        pytest.param(
            "Zzz",
            "PiFire: Unknown Notification issue",
            "Whoops! PiFire had the following unhandled notify event: Zzz at ",
            False,
            "default",
            {"value1": "Unknown Notification issue"},
            id="unmatched-falls-back",
        ),
    ],
)
def test_notification_event(monkeypatch, event, title, body, exact, channel, query_args):
    rec = _capture(monkeypatch, event)

    assert rec["title"] == title
    if exact:
        assert rec["body"] == body
    else:
        assert rec["body"].startswith(body)
    assert rec["channel"] == channel
    assert rec["query_args"] == query_args
```

- [ ] **Step 4: Replace the mqtt publish family**

```python
@pytest.mark.parametrize(
    ("topic_suffix", "payload"),
    [
        ("pellet", {"hopper_level": 42}),
        ("control_notify_data_ADC1", {"target": 225}),
        ("probe_data_primary", {"Grill": 225}),
    ],
)
def test_publish_topic_and_payload(patched_client, topic_suffix, payload):
    handler = _make_handler(patched_client)
    handler.last_conn_time = 0
    handler._create_autodiscover = mock.Mock()

    handler._publish(topic_suffix, payload)

    calls = [c for c in handler.client.publish_calls if c["topic"] == f"PiFireTest/{topic_suffix}"]
    assert calls and json.loads(calls[-1]["payload"]) == payload
```

- [ ] **Step 5: Replace the mqtt autodiscover family**

```python
@pytest.mark.parametrize(
    ("group", "reading", "key", "expected"),
    [
        ("pid", {"u_max": 0.5}, "u_max", {"unit_of_measurement": "%", "value_template": "{{ value_json.u_max | round(2)}}"}),
        ("system", {"cpu_temp": 45.2}, "cpu_temp", {"device_class": "temperature", "unit_of_measurement": "°C"}),
        # Uses the global units setting, which the handler fixture pins to F.
        ("control", {"primary_setpoint": 225}, "primary_setpoint", {"device_class": "temperature", "unit_of_measurement": "°F"}),
    ],
)
def test_autodiscover_fields(patched_client, group, reading, key, expected):
    handler = _make_handler(patched_client)

    result = _discover(handler, group, reading)

    _, discovery = result[key]
    for field, value in expected.items():
        assert discovery[field] == value, field
```

- [ ] **Step 6: Verify the notify package**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/notify -q 2>&1 | tail -5'
```

Expected: all pass. The count should be unchanged (21 functions replaced by 21 parametrized cases).

- [ ] **Step 7: Confirm the odd-row-out assertion survived**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/notify/test_wled_handler.py -v 2>&1 | grep probe_alarm'
```

Expected: a passing `probe_alarm` case. Then temporarily change the expected `speed` from `100` to `999` and confirm it FAILS — proving `speed` is still asserted and was not lost in the merge. Revert.

- [ ] **Step 8: Commit**

```bash
jj commit -m "test(notify): parametrize the WLED, event and MQTT tables

The probe_alarm row pins speed rather than effect, and three event rows
match body exactly rather than by prefix -- both preserved explicitly."
```

---

### Task 12: Parametrize the Socket.IO app-data families

`tests/web/test_socketio_app_data.py` holds three families totalling 19 tests: 8 missing-argument errors, 6 invalid-type errors, 5 admin actions.

Note the file's module docstring: it is a deliberate **characterization net** written to pin behavior before a planned refactor of the Socket.IO god-functions. Parametrizing is safe — the assertions are unchanged — but do not delete or weaken any case.

**Files:**
- Modify: `tests/web/test_socketio_app_data.py`

- [ ] **Step 1: Replace the 6 invalid-type tests**

```python
@pytest.mark.parametrize(
    "action",
    ["update_action", "pellets_action", "timer_action", "recipes_action", "probes_action", "notify_action"],
)
def test_post_invalid_type(sio, action):
    payload_key = "globals" if action == "update_action" else action
    resp = sio.mod._post_app_data(action, "bogus", json.dumps({payload_key: {}}))
    assert resp["result"] == "Error"
    assert resp["message"] == "Error: Received request without valid type"
```

Verify against the originals: `test_post_update_invalid_type` uses `{"globals": {}}` while every other one uses `{"<action>": {}}`. That asymmetry is real and the `payload_key` line preserves it — do not "tidy" it away.

- [ ] **Step 2: Replace the 5 admin-action tests**

```python
@pytest.mark.parametrize(
    ("action_type", "expected_call"),
    [
        ("reboot", "reboot_system"),
        ("shutdown", "shutdown_system"),
        ("restart_control", "restart_control"),
        ("restart_webapp", "restart_webapp"),
        # The supervisor action resolves to restart_scripts, not
        # restart_supervisor -- pinned deliberately.
        ("restart_supervisor", "restart_scripts"),
    ],
)
def test_post_admin_action(sio, action_type, expected_call):
    resp = sio.mod._post_app_data("admin_action", action_type)
    assert resp["result"] == "OK"
    assert any(c[0] == expected_call for c in sio.calls)
```

- [ ] **Step 3: Replace the 8 missing-argument tests**

```python
@pytest.mark.parametrize(
    ("action", "action_type", "message"),
    [
        ("pellets_action", "load_profile", "Error: Profile not included in request"),
        ("pellets_action", "edit_brands", "Error: Function not specified"),
        ("pellets_action", "edit_woods", "Error: Function not specified"),
        ("pellets_action", "edit_profile", "Error: Profile not included in request"),
        ("pellets_action", "delete_profile", "Error: Profile not included in request"),
        ("pellets_action", "delete_log", "Error: Function not specified"),
        # paused == 0 but no ranges -> Error (partial in-memory mutation not persisted).
        ("timer_action", "start_timer", "Error: Start time not specified"),
        ("notify_action", "notify_update", "Error: Request missing probe label"),
    ],
)
def test_post_missing_required_argument(sio, action, action_type, message):
    payload = json.dumps({action: {}})
    resp = sio.mod._post_app_data(action, action_type, payload)
    assert resp["result"] == "Error"
    assert resp["message"] == message
```

- [ ] **Step 4: Verify the count and that hazards are still neutralized**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/web/test_socketio_app_data.py -q 2>&1 | tail -5'
```

Expected: same total count as before, all passing. The `sio` fixture patches `reboot_system`/`shutdown_system`/`os.system` — if the VM reboots, the fixture was not applied and you must stop immediately.

- [ ] **Step 5: Commit**

```bash
jj commit -m "test(web): parametrize the Socket.IO app-data characterization tables"
```

---

### Task 13: Parametrize the remaining Python families, and leave the ones that should stay

Twenty-plus smaller families remain across `tests/`. Some should be parametrized; some **must not be**.

**Do NOT parametrize these** — each member carries a distinct docstring encoding real domain knowledge that a shared body would delete:

- `tests/unit/common/test_settings_schema.py` — `test_current_schema_omits_retired_fan_pid_setting`, `..._u_min_setting`, `..._hold_cycle_time_setting`. Each docstring explains *why* that specific setting was retired (where the real value comes from instead). Leave all three as-is and add a comment noting they are deliberately not merged.

Add that note above the trio:

```python
# These three are deliberately NOT parametrized: each docstring records why
# that specific setting was retired and what replaced it. A shared body would
# delete the only place that reasoning is written down.
```

**Do parametrize these.** For each, apply the same procedure used in Tasks 10-12: read all members, build a `pytest.param` table with descriptive `ids`, preserve every per-test comment as a row comment, and preserve any assertion that differs between members.

| File | Family size | Varying dimension |
|---|---|---|
| `tests/characterization/test_status_dimension.py` | 3 | `mode` → expected `status` |
| `tests/unit/common/test_settings_schema.py` | 4 | rejected `i2c_bus` dict → raises |
| `tests/unit/common/test_settings_schema.py` | 3 | out-of-range field → error substring |
| `tests/characterization/test_all_writers_strict.py` | 4 | admin flag/value |
| `tests/web/test_api_admin_maintenance.py` (+1 in `test_api_tuner.py`) | 4 | refused input → 400 |
| `tests/web/test_api_files_recipes_write.py` | 3 | bad index/action → 400 |
| `tests/unit/usb_serial/test_usb_serial_discovery.py` | 3 | id encoding accepted |
| `tests/unit/updater/test_web_ui_build.py` | 3 | version/build → recorded or not |
| `tests/unit/distance/test_tof_base.py` | 4 | (remaining family after Task 7) |
| `tests/ui/test_display_launch.py` | 3 | display module → bare launch |
| `tests/ui/test_base_flex_dash_update.py` | 3 | mode → button row |
| `tests/ui/test_flexobject_coverage.py` | 4 | hopper level → colour |
| `tests/unit/test_docs_import_boundary.py` | 3 parametrized | merge the three identical-bodied parametrized tests into one table |

- [ ] **Step 1: Regenerate the exact current family list**

Task 16 creates the detector this table came from. If Task 16 is already done, run it. Otherwise, list each family's members before editing:

```bash
cd /Users/dannyb/sources/PiFire && grep -n "def test_" tests/characterization/test_status_dimension.py
```

Repeat per file. **Always read all members before merging** — the detector reports structural similarity, which is a hypothesis about intent, not proof.

- [ ] **Step 2: Do `test_status_dimension.py` first, as the reference shape**

```python
@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        # Mirrors test_tick_stop_mode_cleanup, focused on the status axis.
        ("Stop", "inactive"),
        ("Error", "inactive"),
        ("Monitor", "monitor"),
    ],
)
def test_status_after_first_tick(monkeypatch, mode, expected_status):
    _neutralize_externals(monkeypatch)
    settings = base_settings()
    control_data = base_control(mode=mode)
    control_data["updated"] = True
    c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
    _spy_dispatch(c)
    c.setup()
    c.tick()

    assert store.read_control()["status"] == expected_status
```

- [ ] **Step 3: Merge the three `test_docs_import_boundary.py` tests**

`:176`, `:196`, and `:244` have byte-identical bodies and identically-shaped parametrize decorators — only their param tables differ. Merge into one test whose table is the concatenation of all three, keeping the original grouping as comments:

```python
@pytest.mark.parametrize(
    ("statement", "action"),
    [
        # --- direct assignment ---
        ("sys.path <<= 2", "sys.path <<="),
        ("sys.path >>= 2", "sys.path >>="),
        ("sys.path |= []", "sys.path |="),
        ("sys.path ^= []", "sys.path ^="),
        ("sys.path &= []", "sys.path &="),
        ("sys.path //= 2", "sys.path //="),
        ('sys.path[(index := 0)] += ["probe"]', "sys.path[(index := 0)] +="),
        ('sys.path[0] = "probe"', "sys.path[0] ="),
        ('sys.path[:] = ["probe"]', "sys.path[:] ="),
        # --- direct deletion ---
        ("del sys.path", "del sys.path"),
        ("del sys.path[0]", "del sys.path[0]"),
        ("del sys.path[:]", "del sys.path[:]"),
        # --- unpacking deletion ---
        ("del (sys.path, marker)", "del sys.path"),
        ("del [marker, sys.path[0]]", "del sys.path[0]"),
        ("del (marker, [sys.path[:]])", "del sys.path[:]"),
    ],
)
def test_single_sys_path_mutation_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    action: str,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    path = _write_probe(tmp_path, "probe.py", f"import sys\n{statement}\n")

    assert _mutates_sys_path(path) == [f"probe.py:2:{action}"]
```

Leave `test_unpacking_sys_path_assignment_is_reported` alone — it asserts a *list* of actions (`actions`, plural), which is a genuinely different assertion shape.

- [ ] **Step 4: Work through the remaining table rows one file at a time**

After each file: run that file, confirm the case count matches the original test count, and confirm all pass.

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest <file> -v 2>&1 | tail -20'
```

**If any family turns out to have members with materially different assertions, leave that family alone** and record why in the task report. Not every structural twin is a semantic twin.

- [ ] **Step 5: Run the full Python suite**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest 2>&1 | tail -10'
```

Expected: 0 failures. The total should be at or near `BASELINE_COUNT` — parametrizing preserves case counts.

- [ ] **Step 6: Commit**

```bash
jj commit -m "test: parametrize the remaining structural test families

The three retired-setting schema tests are deliberately left alone --
each docstring records why that setting was retired."
```

---

### Task 14: Clean up the JS/TS duplicates and the raw-throw assertion

Four small items in `web-react/tests/`. The JS side is in far better shape than the Python side — only 2 duplicate bodies across 2,053 tests.

**Files:**
- Modify: `web-react/tests/unit/helpers/generatedContracts.test.ts:260`
- Modify: `web-react/tests/unit/helpers/notify/notifyState.test.ts:144,274`
- Modify: `web-react/tests/unit/helpers/tuner/tunerApi.test.ts:60,141`
- Modify: `web-react/tests/unit/components/cookfiles/CookFileList.test.tsx:149`, `web-react/tests/unit/components/recipes/RecipeList.test.tsx:140`
- Modify: `web-react/tests/unit/components/wizard/wizardStyles.test.ts:40`, `web-react/tests/unit/helpers/cssCoverage.test.ts:36`

- [ ] **Step 1: Convert the raw `throw` to an `expect`**

`generatedContracts.test.ts:260` throws an `Error` while the test three lines above it uses `expect(duplicates).toEqual([])`. Make them consistent:

```typescript
  it("keeps migrated helpers free of Python-owned interface and type declarations", () => {
    const residuals = residualMirrors(HELPERS_ROOT, pythonOwnedNames());
    expect(residuals).toEqual([]);
  });
```

`toEqual([])` prints the offending names on failure, which the `throw` was manually reconstructing.

- [ ] **Step 2: Disambiguate the same-named tests**

`notifyState.test.ts` has `"lets shutdown win when the payload somehow carries both"` at both `:144` (inside `describe("readTargetEdit")`) and `:274` (inside `describe("readLimitEdit")`). These are legal — the full test IDs differ — but failure output does not say which. Rename to carry their subject:

- `:144` → `"readTargetEdit lets shutdown win when the payload somehow carries both"`
- `:274` → `"readLimitEdit lets shutdown win when the payload somehow carries both"`

Same treatment for `tunerApi.test.ts`: `:60` is inside `describe("fetchTr")` and `:141` inside `describe("fetchAutoStatus")`:

- `:60` → `"fetchTr keeps a null reading null"`
- `:141` → `"fetchAutoStatus keeps a null reading null"`

Leave `keys.test.ts` alone — its repeated names sit in describes named after clearly distinct key families, and the names read correctly in context.

- [ ] **Step 3: Assess the two duplicate bodies before changing them**

```bash
cd /Users/dannyb/sources/PiFire && sed -n '149,160p' web-react/tests/unit/components/cookfiles/CookFileList.test.tsx && echo "=====" && sed -n '140,151p' web-react/tests/unit/components/recipes/RecipeList.test.tsx
```

These test *different components* that share a pagination control. Duplicated bodies across two components are usually correct — each component must independently work. **Only extract a shared helper if both render through the same list primitive.** If they do, add the helper to an existing test-utils module; if not, leave them and note why in the task report.

Do the same assessment for `wizardStyles.test.ts:40` vs `cssCoverage.test.ts:36` — if both call the same `cssCoverage` helper, the wizard copy is redundant and can be deleted; if `wizardStyles` wraps it differently, keep both.

- [ ] **Step 4: Run the JS unit suite**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.bun/bin:$PATH"; cd ~/PiFire/web-react && bun run test 2>&1 | tail -12'
```

Expected: all pass.

- [ ] **Step 5: Typecheck and lint**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.bun/bin:$PATH"; cd ~/PiFire/web-react && bun run typecheck && bun run lint 2>&1 | tail -12'
```

Expected: typecheck clean; lint reports 0 errors.

- [ ] **Step 6: Commit**

```bash
jj commit -m "test(web-react): use expect over raw throw, disambiguate duplicate test names"
```

---

### Task 15: Dedupe the remaining cross-file helpers and the twin upload test

The audit found 26 helper functions duplicated across test files beyond the asset helpers handled in Task 4 (~267 duplicated lines in the top 20 alone), plus one duplicated *test* not covered by any earlier task. Task 16's detector will fail on that test, so it must be resolved here.

**Files:**
- Modify: `tests/web/test_api_files_recipes_write.py:145`, `tests/web/test_api_files_cookfile_write.py:189`
- Modify (helper consolidation): `tests/ui/test_driver_input_behavior.py`, `tests/ui/test_fixed_base_drivers_load.py`, `tests/ui/test_pygame_qt_drivers.py`, `tests/unit/distance/test_hcsr04.py`, `tests/unit/distance/test_tof_base.py`, `tests/unit/distance/test_serial_tof_base.py`, `tests/unit/runtime/test_threaded_runner.py`, `tests/unit/runtime/test_sync_runner.py`, `tests/ui/test_qtquick_1024x600_manifest.py`, `tests/ui/test_qtquick_manifest.py`
- Create: `tests/ui/_driver_helpers.py`, `tests/unit/distance/_sampling_helpers.py`

**Interfaces:**
- Consumes: `manifest_config_default` from `tests/conftest.py:94`.
- Produces: `tests/ui/_driver_helpers.py` exporting `load_driver(...)` and `instantiate(...)`; `tests/unit/distance/_sampling_helpers.py` exporting `await_sample(hopper)`.

- [ ] **Step 1: Resolve the duplicated upload test**

`test_upload_with_an_empty_filename_is_400` is byte-identical at `tests/web/test_api_files_recipes_write.py:145` and `tests/web/test_api_files_cookfile_write.py:189`. Read both first:

```bash
cd /Users/dannyb/sources/PiFire && sed -n '145,150p' tests/web/test_api_files_recipes_write.py && echo "=====" && sed -n '189,194p' tests/web/test_api_files_cookfile_write.py
```

They are identical because both files' `client` fixture resolves to the *same* `api_files_client`, so both tests hit the same endpoint with the same input. That makes one of them genuinely redundant rather than "the same check against two endpoints".

**Verify that claim before deleting anything:**

```bash
cd /Users/dannyb/sources/PiFire && grep -n "def client" -A 3 tests/web/test_api_files_recipes_write.py tests/web/test_api_files_cookfile_write.py
```

If both are `def client(api_files_client): return api_files_client` **and** the test body names no recipes- or cookfile-specific URL, delete the copy in `test_api_files_cookfile_write.py:189`. If the bodies post to different URLs, they are not redundant — keep both and add them to Task 16's `ALLOWLIST` with that reason.

- [ ] **Step 2: Share the UI driver-loading helpers**

`_load_driver` (4 lines) is duplicated in `tests/ui/test_driver_input_behavior.py:102` and `tests/ui/test_fixed_base_drivers_load.py:117`; `_instantiate` (17 lines) is duplicated across those two plus `tests/ui/test_pygame_qt_drivers.py:165` (as `_instantiate_fixed`).

Read all copies before merging — the `_instantiate_fixed` name suggests it may differ:

```bash
cd /Users/dannyb/sources/PiFire && sed -n '102,125p' tests/ui/test_driver_input_behavior.py && echo "=====" && sed -n '117,145p' tests/ui/test_fixed_base_drivers_load.py && echo "=====" && sed -n '165,185p' tests/ui/test_pygame_qt_drivers.py
```

Create `tests/ui/_driver_helpers.py` with a module docstring explaining the consolidation, and move the verbatim bodies of the copies that genuinely match. Import from it in each module. **If `_instantiate_fixed` differs in substance, leave it where it is** and consolidate only the true pair.

- [ ] **Step 3: Share the distance `_await_sample` helper**

`_await_sample` (13 lines) is identical in `tests/unit/distance/test_hcsr04.py:90`, `test_tof_base.py:113`, and `test_serial_tof_base.py:84`. Task 7 removed tests from these files but left the helper triplicated.

Create `tests/unit/distance/_sampling_helpers.py`:

```python
"""Sampling helpers shared by the transport test modules.

_await_sample was copy-pasted identically into all three transport test
files. Named without a `test_` prefix so pytest does not collect it.
"""
```

Move the verbatim body in as `await_sample`, import it in all three modules, and update call sites.

- [ ] **Step 4: Point the manifest helpers at the existing conftest function**

`_config_default` in `tests/ui/test_qtquick_1024x600_manifest.py:12` and `tests/ui/test_qtquick_manifest.py:12` duplicates `manifest_config_default`, which already exists at `tests/conftest.py:94`. Delete both local copies and import the conftest one:

```python
from tests.conftest import manifest_config_default
```

Update call sites from `_config_default(...)` to `manifest_config_default(...)`.

- [ ] **Step 5: Assess the remaining helper pairs without changing them yet**

These pairs were flagged but sit in unrelated packages, where a shared module may couple things that should stay independent: `_frame` (`test_threaded_runner.py:51` / `test_sync_runner.py:28`), `_eval_js` (`test_qtquick_parity.py:118` / `test_qtquick_smoke_plus.py:17`), `_pair_descriptor`/`_descriptor`, `_manifest`, `store`, `_entry`, `grill_left_stopped`, `_ctx`, `_flex_status_data`/`_dsi_status_data`.

For each, decide by one rule: **consolidate only when both copies would have to change together for the same reason.** A helper that happens to look alike but belongs to two independent subsystems should stay duplicated. Record each decision — consolidated or deliberately kept — in the task report.

- [ ] **Step 6: Confirm the consolidations landed**

```bash
cd /Users/dannyb/sources/PiFire && grep -rn "def _await_sample\|def _config_default\|def _load_driver" tests/
```

Expected: no output for `_await_sample` and `_config_default`; `_load_driver` gone if Step 2 consolidated it.

- [ ] **Step 7: Verify the affected suites**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest tests/ui tests/unit/distance tests/web/test_api_files_recipes_write.py tests/web/test_api_files_cookfile_write.py -q 2>&1 | tail -6'
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
jj commit -m "test: consolidate the remaining cross-file test helpers

Helpers that must change together now live in one place; helpers that
merely look alike across independent subsystems are left duplicated on
purpose, noted per site."
```

---

### Task 16: Add the duplicate-test detector as a permanent gate

Everything in this plan was found by an AST analysis run once, by hand. Make it a repo tool and a test, so the duplication cannot silently return — this repo already has meta-tests of exactly this genre (`tests/unit/test_docs_import_boundary.py`, `web-react/tests/unit/structure.test.ts`).

**Files:**
- Create: `tests/tools/__init__.py`, `tests/tools/duplicate_tests.py`
- Create: `tests/unit/test_no_duplicate_test_bodies.py`

**Interfaces:**
- Produces: `find_duplicate_test_bodies(root: Path) -> list[DuplicateGroup]` where `DuplicateGroup` is a dataclass with `.digest: str`, `.members: list[tuple[str, int, str]]` (file, line, test name), and `.line_count: int`.

- [ ] **Step 1: Write the failing meta-test first**

Create `tests/unit/test_no_duplicate_test_bodies.py`:

```python
"""Guard against the copy-pasted-test problem coming back.

A test whose body is byte-identical to another test's -- decorators included --
is either a redundant copy or a mislabelled one. Both are worth catching at
merge time rather than in a once-a-year audit.

ALLOWLIST is for genuinely-justified twins. Add to it only with a reason.
"""

from pathlib import Path

from tests.tools.duplicate_tests import find_duplicate_test_bodies

ROOT = Path(__file__).resolve().parents[1]

# (file, test name) pairs that are duplicates on purpose.
#
# The two upload tests below have byte-identical bodies but are NOT redundant:
# each module's `client` fixture resolves to a different endpoint, so they post
# to different URLs. Task 15 verified this against the actual fixtures before
# deciding to keep both -- the detector sees identical source and cannot see the
# fixture indirection that makes them different tests.
ALLOWLIST: set[tuple[str, str]] = {
    ("tests/web/test_api_files_recipes_write.py", "test_upload_with_an_empty_filename_is_400"),
    ("tests/web/test_api_files_cookfile_write.py", "test_upload_with_an_empty_filename_is_400"),
}


def test_no_duplicate_test_bodies():
    groups = find_duplicate_test_bodies(ROOT)
    offenders = [
        group
        for group in groups
        if not all((path, name) in ALLOWLIST for path, _line, name in group.members)
    ]

    assert offenders == [], "\n".join(
        f"{group.line_count} identical lines:\n"
        + "\n".join(f"    {path}:{line}  {name}" for path, line, name in group.members)
        for group in offenders
    )
```

- [ ] **Step 2: Run it and watch it fail on import**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/test_no_duplicate_test_bodies.py -q 2>&1 | tail -5'
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tests.tools'`.

- [ ] **Step 3: Implement the detector**

Create `tests/tools/__init__.py` (empty) and `tests/tools/duplicate_tests.py`:

```python
"""AST-based duplicate-test detection.

Two tests are duplicates when their decorators and their bodies produce the
same AST dump. Docstrings are stripped first (a differing docstring on an
otherwise identical body is still a duplicate), and bodies below
MIN_AST_NODES are ignored -- one-line tests collide harmlessly.
"""

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

MIN_AST_NODES = 6


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    members: tuple[tuple[str, int, str], ...]
    line_count: int


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _digest(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = [statement for statement in node.body if not _is_docstring(statement)]
    decorators = "".join(sorted(ast.unparse(d) for d in node.decorator_list))
    module = ast.Module(body=body or [ast.Pass()], type_ignores=[])
    return hashlib.md5((decorators + ast.unparse(module)).encode()).hexdigest()[:16]


def find_duplicate_test_bodies(root: Path) -> list[DuplicateGroup]:
    seen: dict[str, list[tuple[str, int, str]]] = {}
    sizes: dict[str, int] = {}

    for path in sorted(root.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            if sum(1 for _ in ast.walk(node)) < MIN_AST_NODES:
                continue
            digest = _digest(node)
            rel = str(path.relative_to(root.parent))
            seen.setdefault(digest, []).append((rel, node.lineno, node.name))
            sizes[digest] = (node.end_lineno or node.lineno) - node.lineno + 1

    return [
        DuplicateGroup(digest=digest, members=tuple(members), line_count=sizes[digest])
        for digest, members in sorted(seen.items())
        if len(members) > 1
    ]
```

- [ ] **Step 4: Run the meta-test — it should now pass**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/test_no_duplicate_test_bodies.py -q 2>&1 | tail -20'
```

Expected: PASS, because Tasks 1-15 removed every duplicate. **If it fails, the failure output names the duplicates that are still there** — go fix them rather than adding them to `ALLOWLIST`. The allowlist is for justified twins, not for unfinished work.

- [ ] **Step 5: Prove the guard actually catches a regression**

Temporarily paste an exact copy of any 10-line test into the same file under a new name, sync, and re-run.

Expected: the meta-test FAILS and names both copies. **Revert the pasted copy.**

- [ ] **Step 6: Confirm `tests/tools` is not collected as tests**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/tools --collect-only -q 2>&1 | tail -3'
```

Expected: no tests collected. The module is named `duplicate_tests.py`, not `test_*.py`, so pytest ignores it.

- [ ] **Step 7: Commit**

```bash
jj commit -m "test: add a duplicate-test-body detector and meta-test

The audit that motivated this cleanup was a one-off AST script. This makes
it a gate, so copy-pasted tests fail at merge instead of accumulating."
```

---

### Task 17: Declare the undeclared test dependencies

`pyproject.toml` does not declare `scipy`, `casadi`, or `pandas`, but the suite
imports all three. A clean `uv sync --group dev` on a fresh machine produces
**35 collection errors**. They are currently installed by hand, which is why
nobody noticed.

`codegen` already pins `casadi==3.7.2`, `scipy==1.18.0` and `numpy==2.5.1`, but
that group is for code generation, not for running tests — `--group dev` alone
must be sufficient to run the suite.

**The exact set was determined empirically, not from reading comments.** On a
clean `uv sync --group dev`, collection reported: `scipy` (28 errors), `pandas`
(1), `casadi` (1). Installing exactly those three took collection to
**6846/6849 collected, 3 deselected, 0 errors**.

**Do NOT add `scikit-learn` or `do-mpc`.** Neither is imported anywhere in the
tree — verified by AST/regex scan across all `.py` files outside `.jj/` and
`build/`, and confirmed by the clean collection above without them. The only
things that mention them are a stale comment in `pyproject.toml` (which this
task fixes) and a frozen JSON fixture in
`tests/unit/acados/test_grey_box_pifire_parity.py`, which merely records the
*provenance string* `{"do_mpc": "5.1.1", ...}` of a corpus captured long ago —
it reads a JSON file and never imports the package.

**Files:**
- Modify: `pyproject.toml` (the `[dependency-groups] dev` list)
- Modify: `uv.lock` (regenerated, not hand-edited)

**Interfaces:**
- Produces: a `dev` group that alone satisfies every import in `tests/`.

- [ ] **Step 1: Confirm the gap from a clean sync**

Do this in a scratch directory, not the shared checkout:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire-verify && rm -rf .venv && uv sync --group dev >/dev/null 2>&1; .venv/bin/python -m pytest --collect-only -q 2>&1 | tail -2; .venv/bin/python -m pytest --collect-only -q 2>&1 | grep -oE "No module named .[a-z_]+." | sort | uniq -c'
```

Expected: `35 errors`, and the module tally `28 scipy`, `1 pandas`, `1 casadi`.
Nothing naming `sklearn` or `do_mpc`.

- [ ] **Step 2: Add the three packages to the `dev` group**

In `pyproject.toml`, inside `[dependency-groups]`'s `dev = [...]` list, add:

```toml
    # Imported by the controller/MPC surface but previously undeclared, so a
    # clean `uv sync --group dev` produced 35 collection errors: scipy (28),
    # pandas (1), casadi (1). Pinned to match the `codegen` group so the two
    # resolve together. scipy is imported by controller/{grey_box,mpc_model,
    # update_mpc}.py; casadi by tests/unit/acados/test_grey_box_definition.py;
    # pandas by tests/unit/mpc/test_mpc_{calibration,refit}.py.
    "casadi==3.7.2",
    "scipy==1.18.0",
    "pandas>=3.0",
    # A hung test under `-n auto` stalls the whole run with no failing test to
    # point at. Several probe/display loops retry forever when their exit
    # condition is broken, so a wall-clock ceiling is the only thing that turns
    # "CI is stuck" into a named failure.
    "pytest-timeout>=2.3.0",
```

- [ ] **Step 2a: Correct the stale `numpy` comment**

The comment above `"numpy>=1.26"` in `[project] dependencies` makes three
claims; two are false and one is now misleading:

- ✅ "Imported eagerly by `controller/fopdt_identifier.py`" — true.
- ❌ "unlike the MPC stack's lazy imports" — false. scipy is imported at
  **module scope** in `controller/grey_box.py:12`, `controller/mpc_model.py:114`
  and `controller/update_mpc.py:32`. The MPC stack's scipy imports are not lazy.
- ❌ "Already present transitively via scikit-learn" — false. scikit-learn is
  not a dependency of this project at all, direct or transitive. Nothing
  imports it.

Replace the whole comment block with one that is true:

```toml
    # Imported eagerly by controller/fopdt_identifier.py, which controller/pid_sp.py
    # loads at module scope -- the shipped PID Smith Predictor controller depends on
    # it unconditionally, so it cannot live in an optional extra. scipy (declared in
    # the dev group) also pulls numpy, but this declaration is what stops a future
    # drop of that package from silently taking numpy with it.
    "numpy>=1.26",
```

While you are here, check the rest of `pyproject.toml`'s comments against
reality and fix any other claim you can disprove — but **only** claims you have
actually verified. Do not rewrite prose you have not checked.

- [ ] **Step 2a-bis: Make the declared line-length limit actually enforced**

`ruff.toml` sets `line-length = 120`, but `ruff check` does **not** enforce it:
`E501` is not in Ruff's default rule selection, and `ruff.toml` has no `select`
list that adds it. Verified directly — `ruff check` printed `All checks passed!`
on a 130-character line. Every "ruff clean" claim in this project has therefore
never been evidence about line length.

Add `E501` to the enforced set in `ruff.toml`:

```toml
[lint]
# `line-length` above is only advisory to the formatter unless E501 is selected
# explicitly -- it is NOT in Ruff's default rule set. Without this, `ruff check`
# passes on a 130-column line while ruff.toml claims a 120-column limit.
extend-select = ["E501"]
```

**This has already been measured: 128 lines over 120 columns, across 54 files**
(733 Python files scanned, excluding `.jj/`, `build/`, `.venv/`, `node_modules/`).
Concentrated in `probes/` (41), `tools/` (15), `common/` (15), `controller/` (14),
with only 10 in `tests/`.

That is a repo-wide reformat, not a side effect of this task — the same
reasoning `pyproject.toml` already applies to the ruff 0.16 upgrade.

**But only 10 of those 128 lines are inside `tests/`**, in three files:
`tests/unit/mpc/test_grey_online_learning.py` (7),
`tests/unit/updater/test_acados_prerequisites.py` (2),
`tests/unit/acados/test_rebuild_acados.py` (1).

This is not hypothetical: during this plan's execution, **two separate tasks
shipped over-length lines that `ruff check` reported as clean**, and both were
caught only because a reviewer ran `awk` by hand. So enable the gate where it
is cheap and where this plan actually works — `tests/` — and defer the
production backlog:

```toml
[lint]
extend-select = ["E501"]

[lint.per-file-ignores]
# `line-length = 120` was declared but never enforced: E501 is not in Ruff's
# default rule set, so `ruff check` passed on 130-column lines. Enabling it
# repo-wide would surface a 118-line pre-existing backlog outside tests/, which
# is a reformat commit of its own (same reasoning as the ruff 0.16 note in
# pyproject.toml). Until that lands, the gate is enforced on tests/ only.
"blueprints/*" = ["E501"]
"common/*" = ["E501"]
"controller/*" = ["E501"]
"display/*" = ["E501"]
"docs/*" = ["E501"]
"file_mgmt/*" = ["E501"]
"grillplat/*" = ["E501"]
"notify/*" = ["E501"]
"probes/*" = ["E501"]
"tools/*" = ["E501"]
"*.py" = ["E501"]          # top-level scripts (board-config.py, control.py, app.py, wizard.py)
```

Then fix the 10 test-side violations by wrapping them, and confirm the gate is
live:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire-verify && .venv/bin/python -m ruff check tests/ 2>&1 | tail -5'
```

Expected: `All checks passed!` — and it must now be a claim that means something.
Sanity-check that the gate really bites by temporarily appending a 130-column
line to any test file and confirming `ruff check tests/` fails, then removing it.

**If the `per-file-ignores` pattern above does not behave as expected** (Ruff's
glob semantics for bare `*.py` can be surprising), do not fight it: fall back to
leaving `ruff.toml` unchanged and record the finding plus the measured counts in
your report. A wrong lint config is worse than an unenforced one.

Confirm the count yourself before recording it, so the number in the report is
one you verified rather than one you copied:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire-verify && find . -name "*.py" -not -path "./.venv/*" -not -path "./build/*" -not -path "./node_modules/*" | xargs awk "length>120" | wc -l'
```

- [ ] **Step 2b: Give the suite a per-test timeout**

In `[tool.pytest.ini_options]`, extend `addopts` and add the timeout method:

```toml
addopts = ["--random-order", "-n", "auto", "-m", "not slow", "--timeout=120", "--timeout-method=thread"]
```

120s is far above any healthy unit test here (the whole default suite runs in
~30s) so it will not fire on a loaded machine, but it converts an infinite hang
into a named failure with a traceback. `thread` method is required because
`signal` does not work under xdist workers.

Verify it actually engages:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire && .venv/bin/python -m pytest tests/unit/datastore -q 2>&1 | tail -3'
```

Expected: passes as before. Then confirm the plugin is loaded:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire && .venv/bin/python -m pytest --version 2>&1 | grep -i timeout'
```

Expected: `pytest-timeout` appears in the plugin list.

- [ ] **Step 3: Regenerate the lockfile**

```bash
uv lock
```

Never hand-edit `uv.lock`. If `uv lock` reports a resolution conflict between
`dev` and `codegen`, relax the `dev` pin to `>=` rather than changing `codegen`.

- [ ] **Step 4: Prove a clean `--group dev` sync now collects with zero errors**

```bash
ssh "$PIFIRE_SSH" 'cd PiFire && uv sync --group dev --reinstall 2>&1 | tail -3 && .venv/bin/python -m pytest --collect-only -q 2>&1 | tail -3'
```

Expected: `6846 tests collected` (or the current baseline), **0 errors**.

- [ ] **Step 5: Confirm `uv run` no longer prunes the suite's dependencies**

The reason the venv Python is invoked directly everywhere in this plan is that
`uv run` re-syncs and removed the hand-installed packages. Once they are
declared, that should stop:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire && uv run pytest --collect-only -q 2>&1 | tail -3'
```

Expected: same collection count, 0 errors. If this passes, note in the report
that `uv run pytest` is usable again.

- [ ] **Step 6: Commit**

```bash
jj commit -m "build: declare the test suite's undeclared dependencies

scipy, casadi, pandas, scikit-learn and do-mpc are imported by the
controller/MPC test surface but were never declared. A clean
`uv sync --group dev` produced 28 collection errors; they were only
ever installed by hand."
```

---

### Task 18: Fix the three tests that fail under parallel execution

The suite runs `-n auto` by default and three tests fail intermittently — one
rotates in on each run, never the same one twice. They are pre-existing and
independent of this plan's refactors, but they make the parallel suite
untrustworthy, which is the mode the project actually runs in.

**Files:**
- Modify: `tests/unit/datastore/test_datastore.py:83`
- Modify: `tests/web/test_api_metrics.py:181`
- Modify: `tests/unit/runtime/test_hold_model_persistence.py`
- Modify: `tests/web/test_api_files_recipes_assets.py` and
  `tests/web/test_api_files_cookfile_assets.py` (`test_uploaded_asset_is_served_from_static_img_tmp`)
- Modify: `tests/web/test_api_model_evidence.py`
  (`test_operator_evaluation_persists_restart_checkpoint_consumed_by_unmocked_activation_route`)

**Interfaces:**
- Produces: nothing consumed by other tasks.

**Two more were found during execution and are confirmed pre-existing** (both
reproduce on unmodified code, so neither is caused by this plan's refactors):

- **`test_uploaded_asset_is_served_from_static_img_tmp`** — flakes under
  parallel execution. Reproduced on pristine files: 1 failure in 4 runs, and 2
  in 3 runs on another pass. It exists in *both* the cookfile and recipes asset
  modules, which share the `static_img_tmp` directory — a strong hint that the
  two modules race each other over a shared on-disk path rather than each using
  an isolated temp dir. Confirm that hypothesis before fixing.

- **`test_operator_evaluation_persists_restart_checkpoint_consumed_by_unmocked_activation_route`**
  — the inverse of a flake: it **passes in the full suite but fails 3 out of 3
  runs in isolation**. That means it depends on state some other test leaves
  behind, so running its own file alone is broken. This is more dangerous than a
  normal flake, because the usual debugging move — run just this file — makes it
  fail for a reason unrelated to the change under test. Find the state it
  depends on and make the test establish it itself.

- [ ] **Step 1: Reproduce all three deliberately**

Order-dependence hides under a single run. Force it:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire && for i in 1 2 3 4 5; do xvfb-run -a .venv/bin/python -m pytest -q 2>&1 | grep -E "^FAILED"; echo "--- run $i ---"; done'
```

Expected: each of the three names appears at least once across five runs.
**Record which run produced which failure** — you need a reproducer before a fix.

- [ ] **Step 2: Fix `test_reset_for_tests_restores_db_path_on_none`**

The test captures `datastore.DB_PATH` as "the original" and then asserts the
restored value `.endswith("pifire.db")`. `DB_PATH` is module-level global state.
When another test in the same xdist worker has already called
`_reset_for_tests(<temp path>)` and not restored it, the captured "original" is
a temp path and the final assertion fails.

Make the test establish its own precondition instead of inheriting whatever the
worker left behind:

```python
def test_reset_for_tests_restores_db_path_on_none(tmp_path):
    """Regression test: _reset_for_tests(None) restores original DB_PATH.

    Establishes its own baseline first: DB_PATH is module-level global state,
    and a sibling test that left a temp path installed used to make the
    `endswith("pifire.db")` assertion fail depending on execution order.
    """
    datastore._reset_for_tests(None)  # known-good starting point
    original_db_path = datastore.DB_PATH
    assert original_db_path.endswith("pifire.db")

    temp_db_path = str(tmp_path / "temp.db")
    try:
        datastore._reset_for_tests(temp_db_path)
        assert datastore.DB_PATH == temp_db_path

        datastore._reset_for_tests(None)
        assert datastore.DB_PATH == original_db_path
        assert datastore.DB_PATH.endswith("pifire.db")
    finally:
        datastore._reset_for_tests(None)
```

The `finally` matters as much as the fix: without it this test is itself the
polluter for whichever test runs next.

- [ ] **Step 3: Diagnose and fix `test_export_of_an_empty_table_says_so`**

It calls `flush_metrics()` and expects the export to be exactly `"No Data"`, so
it fails when metrics rows exist. Find out where they come from before fixing:

```bash
ssh "$PIFIRE_SSH" 'cd PiFire && .venv/bin/python -m pytest tests/web/test_api_metrics.py -p no:randomly -q 2>&1 | tail -5'
ssh "$PIFIRE_SSH" 'cd PiFire && grep -n "def ds" -A 20 tests/conftest.py'
```

Determine whether the `ds` fixture is function-scoped and whether
`flush_metrics()` targets the same store the route reads. **Write the fix that
matches what you find** — if `ds` is genuinely per-test, the leak is elsewhere
and the honest fix may be to flush the exact store the route reads rather than
the module-level default. Do not paper over it with a retry.

- [ ] **Step 4: Fix the wall-clock assertion in `test_hold_model_persistence.py`**

`test_checkpoint_writer_does_not_block_hold_or_teardown_and_finishes_latest_snapshot`
asserts elapsed wall-clock time. `pyproject.toml`'s own comment already states
the rule: *"never alongside the suite's wall-clock budget assertions -- a loaded
machine fails those on timing alone."* With `-n auto` on a busy machine, this
test asserts something about the machine, not the code.

Read the test, then choose ONE:

- **Preferred** — replace the timing assertion with a deterministic one that
  proves the same property: that the writer does not hold the lock / that hold
  and teardown complete without waiting on the checkpoint. Use an event or a
  recorded call order rather than a stopwatch.
- **Acceptable if the property is genuinely temporal** — mark it `@pytest.mark.slow`
  so the default `-m "not slow"` run excludes it, and add a comment saying it is
  excluded because it is a timing assertion, not because it is long.

Do NOT simply raise the timeout — that makes the flake rarer, not gone.

- [ ] **Step 5: Prove the fixes with repeated parallel runs**

```bash
ssh "$PIFIRE_SSH" 'cd PiFire && for i in 1 2 3 4 5 6 7 8; do xvfb-run -a .venv/bin/python -m pytest -q 2>&1 | tail -1; done'
```

Expected: eight consecutive runs, 0 failures, no rotating names. Five runs was
enough to surface all three before; eight clean runs is the bar for calling them
fixed.

- [ ] **Step 6: Commit**

```bash
jj commit -m "test: fix the three tests that fail under -n auto

Two depended on module-global datastore state surviving whatever order
the worker chose; the third asserted wall-clock time on a loaded machine."
```

---

### Task 20: Stop test modules importing each other

Tests must import from shared helper modules, never from other test modules. The
suite violates this in **7 places across 4 target modules**:

| Importer | Imports from | Names taken |
|---|---|---|
| `tests/characterization/test_outer_transitions.py:24` | `test_controller_loop_golden` | `make_controller`, `_neutralize_externals`, `_spy_dispatch` |
| `tests/characterization/test_status_dimension.py:17` | `test_controller_loop_golden` | same three |
| `tests/unit/controller/test_heartbeat.py:93` | `test_controller_loop_golden` | `_neutralize_externals`, `make_controller` |
| `tests/unit/runtime/test_critical_error_stop.py:36` | `test_controller_loop_golden` | `_neutralize_externals`, `make_controller` |
| `tests/unit/mpc/test_confidence_bootstrap.py:9` | `test_model_confidence` | `_qualifying`, `_rebuild`, `_state` |
| `tests/unit/mpc/test_model_activation.py:37` | `test_mpc_solver_options` | `CYCLE`, `_config`, `_Estimator`, `_Solver` |
| `tests/unit/runtime/test_hold_refit_trigger.py:740` | `test_hold_model_persistence` | `_pair_phase_state` |

**This is not stylistic.** Importing a test module executes its module-level
code and pulls its fixtures and collection side effects into the importer. It
also couples unrelated tests: during this plan, a change to
`test_hold_model_persistence.py` could not be cleared of causing a flake in
`test_hold_refit_trigger.py` precisely because the latter imports the former.
`test_controller_loop_golden` is imported by four modules — it is already a
shared harness, it just also runs as a test.

**Files:**
- Create: `tests/characterization/_controller_harness.py`
- Create: `tests/unit/mpc/_confidence_helpers.py`
- Create: `tests/unit/mpc/_solver_fixtures.py`
- Create: `tests/unit/runtime/_persistence_helpers.py`
- Create: `tests/unit/test_no_cross_test_imports.py`
- Modify: the 4 source modules (to import from the new helper modules) and the 7 importers

**Interfaces:**
- Produces: helper modules whose names are `_`-prefixed so pytest does not
  collect them, mirroring `tests/web/_asset_helpers.py` and
  `tests/ui/_driver_helpers.py` from earlier tasks.

- [ ] **Step 1: Write the guard test first, and watch it fail**

Create `tests/unit/test_no_cross_test_imports.py`:

```python
"""Tests import from shared helper modules, never from other test modules.

Importing a test module runs its module-level code and drags its fixtures and
collection side effects into the importer, and it couples the two files: a
change to the imported test can break the importer for reasons that have
nothing to do with the behaviour under test. Shared setup belongs in a
`_`-prefixed helper module, which pytest does not collect.
"""

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]


def _is_test_module(dotted: str) -> bool:
    last = dotted.split(".")[-1]
    return last.startswith("test_") or last.endswith("_test")


def _cross_test_imports() -> list[str]:
    offenders: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        rel = path.relative_to(TESTS_ROOT.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and _is_test_module(node.module):
                names = ", ".join(a.name for a in node.names)
                offenders.append(f"{rel}:{node.lineno}: from {node.module} import {names}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_test_module(alias.name):
                        offenders.append(f"{rel}:{node.lineno}: import {alias.name}")
    return offenders


def test_no_test_module_imports_another_test_module():
    offenders = _cross_test_imports()
    assert offenders == [], "tests must import shared helpers, not other tests:\n" + "\n".join(offenders)
```

Run it:

```bash
ssh "$PIFIRE_SSH" 'cd REMOTE_DIR && ~/PiFire/.venv/bin/python -m pytest tests/unit/test_no_cross_test_imports.py -q 2>&1 | tail -20'
```

Expected: FAIL, listing exactly the 7 imports in the table above. If it lists a
different set, the table is stale — trust the tool and report the difference.

- [ ] **Step 2: Extract the controller harness (4 of the 7 importers)**

`make_controller`, `_neutralize_externals` and `_spy_dispatch` live in
`tests/characterization/test_controller_loop_golden.py`. Move them **verbatim**
into `tests/characterization/_controller_harness.py`, then have
`test_controller_loop_golden.py` import them from there like everyone else. Do
not reimplement them; cut and paste, so the diff shows a pure move.

Update all four importers to `from tests.characterization._controller_harness import ...`.

- [ ] **Step 3: Extract the remaining three**

Same treatment:
- `_qualifying`, `_rebuild`, `_state` from `tests/unit/mpc/test_model_confidence.py` → `tests/unit/mpc/_confidence_helpers.py`
- `CYCLE`, `_config`, `_Estimator`, `_Solver` from `tests/unit/mpc/test_mpc_solver_options.py` → `tests/unit/mpc/_solver_fixtures.py`
- `_pair_phase_state` from `tests/unit/runtime/test_hold_model_persistence.py` → `tests/unit/runtime/_persistence_helpers.py`

**If a moved helper depends on other module-level state in its original file**
(a constant, a fixture, another private function), move that too, or stop and
report it — a helper that silently reaches back into the test module has not
actually been decoupled.

- [ ] **Step 4: Confirm the guard now passes and the helpers are not collected**

```bash
ssh "$PIFIRE_SSH" 'cd REMOTE_DIR && ~/PiFire/.venv/bin/python -m pytest tests/unit/test_no_cross_test_imports.py -q 2>&1 | tail -5'
ssh "$PIFIRE_SSH" 'cd REMOTE_DIR && ~/PiFire/.venv/bin/python -m pytest tests/characterization/_controller_harness.py tests/unit/mpc/_confidence_helpers.py tests/unit/mpc/_solver_fixtures.py tests/unit/runtime/_persistence_helpers.py --collect-only -q 2>&1 | tail -3'
```

Expected: guard passes; `no tests collected` for the helper modules.

- [ ] **Step 5: Confirm no test was lost in the move**

```bash
ssh "$PIFIRE_SSH" 'cd REMOTE_DIR && xvfb-run -a ~/PiFire/.venv/bin/python -m pytest --collect-only -q 2>&1 | tail -2'
```

The collected count must be **unchanged**. A drop means a test function moved
into a helper module and is no longer collected — find it and move it back.

- [ ] **Step 6: Run the affected suites**

```bash
ssh "$PIFIRE_SSH" 'cd REMOTE_DIR && xvfb-run -a ~/PiFire/.venv/bin/python -m pytest tests/characterization tests/unit/mpc tests/unit/runtime tests/unit/controller -q 2>&1 | tail -6'
```

- [ ] **Step 7: Commit**

```bash
jj commit -m "test: import shared helpers, never other test modules

Seven imports reached into four test modules for setup. Importing a test runs
its module-level code and couples the two files -- a change to one could break
the other for reasons unrelated to the behaviour under test. The shared setup
now lives in _-prefixed helper modules, with a guard test to keep it that way."
```

---

### Task 21: Fix the two remaining pre-existing parallel flakes

Two tests fail intermittently under `-n auto`. They were found during Task 18's
verification but are **not** caused by it — that was measured, not assumed:

```
tests/unit/bootstrap/test_startup_migration.py::test_backup_restore_settings_round_trip
tests/unit/runtime/test_hold_refit_trigger.py::test_recorder_construction_failure_still_restores_the_final_disabled_checkpoint_once
```

**A cheap reproducer already exists — use it, do not start from whole-suite runs.**
Running the three implicated directories together reproduces at roughly 1–2 in 20:

```bash
ssh "$PIFIRE_SSH" 'cd REMOTE_DIR && hits=0; for i in $(seq 1 20); do
  out=$(xvfb-run -a ~/PiFire/.venv/bin/python -m pytest tests/unit/datastore tests/unit/bootstrap tests/unit/runtime -q 2>&1 | grep -E "^FAILED");
  if echo "$out" | grep -qE "test_recorder_construction_failure|test_backup_restore_settings_round_trip"; then
    hits=$((hits+1)); echo "HIT on iter $i:"; echo "$out"; fi;
done; echo "TOTAL: $hits / 20"'
```

Measured rates with that command: **1/20** and **2/20** across two arms of a
controlled experiment. Treat ~20 iterations as one measurement, and remember
that at this rate **8 samples cannot distinguish presence from absence** — the
controller made exactly that error and had to retract a conclusion. Any claim
that a fix worked needs enough iterations to be meaningful (40+ clean).

**The observed symptom** in the failing run was this log line:

```
Something failed when reading the settings file.  Resetting settings to defaults, since no backup settings files were found.
```

That points at shared settings/datastore state rather than at either test's own
logic. Both tests touch settings persistence; `datastore.DB_PATH` is a
process-global and xdist workers are processes, so the suspect is state leaking
between tests **within a worker** — very likely a background thread outliving
the test that started it, since a purely sequential leak would fail
deterministically rather than 1-in-20.

**Files:**
- Modify: `tests/unit/bootstrap/test_startup_migration.py`
- Modify: wherever the root cause actually lives (a fixture or conftest is more
  likely than either test body)

**FILE OWNERSHIP — a concurrent task owns these, do not edit them:**
`tests/unit/runtime/test_hold_refit_trigger.py`,
`tests/unit/runtime/test_hold_model_persistence.py`,
`tests/characterization/test_controller_loop_golden.py`,
`tests/unit/mpc/test_model_confidence.py`,
`tests/unit/mpc/test_mpc_solver_options.py`.
If the root cause requires changing one of them, **stop and report** rather than
editing — the controller will sequence it.

**Interfaces:**
- Produces: nothing other tasks consume.

- [ ] **Step 1: Reproduce, and measure the baseline rate**

Run the reproducer above. Record the hit count. If you get 0/20, run it again —
do not conclude the flake is absent from a single measurement.

- [ ] **Step 2: Find the root cause before changing anything**

Do NOT add retries, sleeps, reruns, or `@pytest.mark.flaky`. The rule for this
whole plan is root cause before fix; a change that makes a flake rarer rather
than impossible is a failure.

Two concrete leads worth pursuing in order:

1. **A leaked background thread.** Grep the implicated modules for `Thread(`,
   and check whether every one is joined before its test returns. Task 18 found
   exactly this shape in a neighbouring test — a missing `Thread.join()` before
   an `is_alive()` check — so the pattern is present in this codebase.
2. **Settings-file path state.** Find what writes the settings file and what
   `read_settings` falls back to when it cannot parse it. Determine whether a
   test can leave `datastore.DB_PATH`, or the settings path, pointing somewhere
   the next test does not expect.

State which lead the evidence supports, with the evidence, before you fix.

- [ ] **Step 3: Fix at the root**

If the cause is a leaked thread, join it. If it is shared path state, make the
owning fixture restore it in a `finally`. Fix it where it originates, not in the
test that happens to observe it.

- [ ] **Step 4: Prove it, with enough samples to mean something**

Run the reproducer **twice** (40 iterations total) and require 0 hits. Then run
the full suite 8 times and confirm neither name appears.

```bash
ssh "$PIFIRE_SSH" 'cd REMOTE_DIR && for i in 1 2 3 4 5 6 7 8; do xvfb-run -a ~/PiFire/.venv/bin/python -m pytest -q 2>&1 | tail -1; done'
```

Known unrelated failures to ignore: `test_resolve_spi_bus_basic_unknown_cs_raises`
(aarch64 board detect) and `test_typescript_output_is_biome_clean` (needs biome).

**If you cannot establish a root cause, say so and change nothing.** Reporting
an unreproduced flake honestly is a better outcome than a speculative fix — a
prior task in this plan did exactly that and was right to.

- [ ] **Step 5: Commit**

```bash
jj commit -m "test: fix the two remaining tests that fail under -n auto"
```

---

### Task 19: Final full-suite verification

**Files:** none modified.

- [ ] **Step 1: Sync and run the entire Python suite**

```bash
rsync -az --delete --exclude '.jj' --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' --exclude 'htmlcov' --exclude 'build' --exclude '*.pyc' --exclude 'pifire.db' --exclude 'logs' /Users/dannyb/sources/PiFire/ "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST:~/PiFire/"
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest 2>&1 | tail -15'
```

Expected: 0 failures, 0 errors.

- [ ] **Step 2: Run the suite a second time to shake out order dependence**

The suite runs with `--random-order`, so a second run uses a different order. A refactor that accidentally introduced shared state between parametrized cases shows up here and nowhere else.

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest 2>&1 | tail -8'
```

Expected: 0 failures again. If this run fails where the first passed, you have an order dependence — find it before proceeding.

- [ ] **Step 3: Compare against the baseline count**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest --collect-only -q 2>&1 | tail -2'
```

Compare to `BASELINE_COUNT` from Task 0. The count should be within a handful of the baseline: Task 6 is net zero, Task 7 removes 6 and adds 5, Task 15 removes at most 1, Task 16 adds 1, and every parametrize task is net zero. **A drop of more than ~10 means a family was collapsed too far** — find which and restore the lost cases.

- [ ] **Step 4: Run the slow tests, which the default run excludes**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && xvfb-run -a uv run pytest -m slow 2>&1 | tail -8'
```

Expected: pass. These simulate whole cooks and take minutes — they are excluded from the default run, so nothing else in this plan has exercised them.

- [ ] **Step 5: Lint**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run ruff check . 2>&1 | tail -10'
```

Expected: `All checks passed!`. Remember `F401` is ignored — orphaned imports will NOT appear here, which is why each task removed them by hand.

- [ ] **Step 6: Run the JS unit suite, typecheck, lint, and e2e**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.bun/bin:$PATH"; cd ~/PiFire/web-react && bun run test && bun run typecheck && bun run lint 2>&1 | tail -12'
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.bun/bin:$PATH"; cd ~/PiFire/web-react && bunx playwright install --with-deps chromium && bun run test:e2e 2>&1 | tail -15'
```

Expected: unit tests pass, typecheck clean, lint 0 errors, e2e passes.

- [ ] **Step 7: Confirm the duplicate detector reports a clean tree**

```bash
ssh "$PIFIRE_TEST_USER@$PIFIRE_TEST_HOST" 'export PATH="$HOME/.local/bin:$PATH"; cd ~/PiFire && uv run pytest tests/unit/test_no_duplicate_test_bodies.py -q 2>&1 | tail -5'
```

Expected: PASS with an empty `ALLOWLIST` — the cleanup is complete and nothing was swept under the allowlist.

- [ ] **Step 8: Confirm no VM details leaked into the repo**

Search for the *values* of the environment variables rather than hardcoding them — writing the hostname into this command would itself leak it into the repo:

```bash
cd /Users/dannyb/sources/PiFire && grep -rnF -e "$PIFIRE_TEST_HOST" -e "$PIFIRE_TEST_USER" \
  --include='*.py' --include='*.ts' --include='*.tsx' --include='*.md' --include='*.toml' --include='*.json' . | head
```

Expected: no output — including from this plan file, which refers to the VM only through the two environment variables.

- [ ] **Step 9: Report**

Write a short completion report covering: baseline vs final test counts, which families were parametrized, which were deliberately left alone and why, which assertion-free tests got real assertions versus honest renames, and every mutation check performed with its result.

---

## Notes for the executor

**The detector's output is a hypothesis, not a verdict.** Structural similarity means "these look alike", not "these mean the same thing". Two tests can share an AST and assert genuinely different things about different components (see Task 14, Step 3). Every merge in this plan requires reading all members first. When in doubt, leave the duplication and write down why — a justified duplicate costs a few lines; a wrong merge silently deletes coverage.

**Parametrizing is not automatically an improvement.** It is right when the varying thing is data. It is wrong when each case carries distinct reasoning that only survives as prose (Task 13, Step 1). Collapsing eight well-commented branch tests into an uncommented table is a net loss even though it deletes lines.

**Mutation checks are the only proof an assertion has teeth.** Several tasks ask you to break production code temporarily and confirm a test fails. Do not skip these and do not leave a mutation behind — re-run and confirm green after every revert.
