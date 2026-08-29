# Selective sqlite-utils Migration Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt `sqlite-utils` only as PiFire's ordered schema-migration registry while making fresh and legacy schema bootstrap safe under simultaneous WAL-mode process startup.

**Architecture:** PiFire continues to open and configure every SQLite connection, acquire its retried `BEGIN IMMEDIATE`, own commit/rollback, and manage database and sidecar files. Inside that already-acquired transaction, a `sqlite-utils.Database` wraps the existing `sqlite3.Connection` with recursive triggers and plugin hooks disabled; legacy v0-v10 bootstrap runs first, then a named `Migrations("pifire-schema")` registry owns v11 and later migrations. `PRAGMA user_version` remains the compatibility bridge, but every registered migration updates it inside the same nested savepoint as its `_sqlite_migrations` audit row.

**Tech Stack:** Python 3.14, SQLite/WAL, `sqlite-utils>=4.2.1,<5`, multiprocessing with the `spawn` context, pytest, uv, Jujutsu (`jj`).

**Spec:** Approved selective-adoption requirements and evidence from `agent://SqliteUtilsMigrationReview`; they are restated below because no separate repository spec file exists.

## Start Gate

- Implementation MUST NOT begin until the current cumulative MPC work is complete, committed, and pushed by its integration owner.
- Begin from a new, empty Jujutsu working-copy change based on that pushed cumulative MPC result. Do not overlap this work with unfinished MPC schema v9/v10 edits.
- Before editing, use `jj st` to confirm the working copy is empty and use the repository's existing tracked bookmark/remote state to confirm the cumulative MPC result has been pushed. Never use raw Git commands.
- Re-read `common/datastore.py`, the v9/v10 migration tests, and `DB_SCHEMA_VERSION` references at execution time because the cumulative MPC work is explicitly allowed to finish before this plan starts.

## Global Constraints

- Selective adoption only. Reject a full persistence replacement: do not replace `datastore.connection()`, `datastore.transaction`, thread-local connection reuse, raw persistence repositories, settings migrations, pellet migrations, PRAGMA policy, installer ownership, or file lifecycle.
- Reject plain `Migrations.apply(database)` from an un-serialized connection. PiFire's retried `BEGIN IMMEDIATE` MUST be acquired before `Migrations.applied()`, `pending()`, `ensure_migrations_table()`, or `apply()` can inspect or mutate migration state.
- Choose one outer transaction for the complete startup batch: legacy bootstrap, trigger reconciliation, and all pending registered migrations either commit together or roll back together. This deliberately replaces today's per-version partial-commit recovery boundary.
- `common/datastore.py::connection()` remains the sole owner of normal connection creation and continues to set `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`, and `isolation_level=None` before schema work.
- Wrap the already-configured connection as `Database(conn, recursive_triggers=False, execute_plugins=False)`. Never pass a filename, never use `Database` as a context manager, and never call the `sqlite-utils` CLI from an installer, updater, sudo path, or test.
- Keep exact PiFire DDL. Do not introduce `Table.transform()`, `Table.add_column()`, broad automatic table rebuilding, or `transactional=False` migrations in this adoption.
- Freeze legacy schema history at v0-v10. Do not register fabricated v0-v10 migrations and never call private `_record_applied()`.
- Introduce one public version source in `common/schema_migrations.py`: legacy baseline 10 and current schema 11. `common/datastore.py::DB_SCHEMA_VERSION` remains a compatibility alias to the current value so callers do not acquire a second number.
- Registered migration v11 is the registry-adoption bridge. Its only PiFire schema action is advancing `PRAGMA user_version` from 10 to 11; creation of `_sqlite_migrations` and its unique `(migration_set, name)` index is owned by the public sqlite-utils API.
- A registered migration record at target version N and `user_version < N` is an authority conflict and startup MUST fail without mutating either authority. A database already at `user_version >= N` with that known record missing is an audited historical no-op: public `Migrations.apply()` records it without decrementing the newer version.
- A database newer than this binary remains newer; do not stamp it down. Known migrations missing from its audit table may be recorded as public, audited no-ops after their bodies verify `user_version >= target`.
- Settings `_SHAPE_MIGRATIONS` and pellet `_PELLET_MIGRATIONS` remain blob/data-shape registries and MUST NOT be converted or recorded in `_sqlite_migrations`.
- No reverse/down migration is promised. Failure rollback means the whole forward batch returns to the exact pre-attempt schema and can be retried. Operational release rollback requires restoring a pre-upgrade database copy; never decrement `user_version` or delete `_sqlite_migrations` rows.
- Preserve database uid, gid, and mode. With supervisor/install `umask=002`, live `-wal` and `-shm` sidecars must remain owned by the same uid/gid and group-writable. `sqlite-utils` must never open a second connection that could create sidecars under another owner.
- Dependency decision: declare `sqlite-utils>=4.2.1,<5`. Version 4.2 is forbidden because of its shipped `typing_extensions` import failure; the `<5` ceiling prevents an unreviewed major migration/API change.
- Dependency-cost gate: the lock change must be reviewed before production edits proceed. The accepted envelope is sqlite-utils plus its seven declared direct runtime dependencies: `click`, `click-default-group`, `pluggy`, `python-dateutil`, `sqlite-fts4`, `tabulate`, and `pip`. If the resolved 4.x release adds direct dependencies, plugin behavior, or a Python incompatibility beyond that reviewed envelope, stop and re-evaluate adoption rather than silently accepting the new cost.
- TDD is mandatory. Add and observe the fresh/v8 multiprocess concurrency RED and whole-batch failure RED before editing production code.
- On macOS use `uv sync --no-install-package bluepy`; never attempt to build/install `bluepy` on Darwin. Repeat focused migration, permission, and concurrency verification on Linux with the production dependency path.
- Every exported-symbol change requires an LSP reference search before editing and complete caller migration. Do not add compatibility shims, deprecated aliases, or duplicate migration runners.
- Use Jujutsu only. Describe each implementation change before editing, leave an independently reviewable GREEN change, and run `jj new` only after that change's focused verification passes.

---

## File and Interface Map

### New files

- `common/schema_migrations.py`: sqlite-utils adapter, version constants, named v11+ registry, public application guard, and authority-consistency validation. It MUST NOT open or close connections.
- `tests/unit/datastore/test_schema_migrations.py`: v11 bridge, serialization order, failure/retry, authority-conflict, wrapper-default, idempotency, and file/sidecar ownership contracts.

### Modified files

- `common/datastore.py`: import the central schema version, acquire one outer `transaction(conn)` before all schema discovery, execute scripts transaction-safely through the existing wrapped connection, remove nested migration transactions, and invoke the registered migration runner under the same lock.
- `pyproject.toml`: add `sqlite-utils>=4.2.1,<5` to production dependencies.
- `uv.lock`: lock the selected sqlite-utils 4.x release and reviewed dependency graph.
- `tests/unit/datastore/test_datastore_concurrency.py`: add simultaneous fresh and v8 schema-startup workers while retaining the existing queue producer and visibility tests.
- `tests/unit/datastore/test_datastore.py`: retain legacy v1-v8 migration/idempotency coverage, update current-version assertions to the central v11 value, and update comments that currently describe nested transactions.
- `tests/unit/common/test_learning_trajectory_store.py`: preserve v8-to-v9 DDL/rollback assertions while expecting the complete successful startup to end at the current schema version and contain the v11 audit record.
- `tests/unit/common/test_model_challenger_store.py`: preserve v9-to-v10 DDL/rollback assertions while expecting successful retry/startup to continue through v11.
- `tests/unit/datastore/test_log_retention.py`: add stale-trigger reconciliation coverage under the serialized schema path if the current tests do not already assert definition replacement and reconnect idempotency after the cumulative work lands.

### Explicitly unchanged production files

- `common/settings_migration.py` and `common/pellets_schema.py`: data-shape registries remain separate.
- `common/persistence/learning_trajectory.py` and `common/persistence/model_evidence.py`: their explicit-path connections continue to configure/open themselves and call `datastore._ensure_schema(connection)`; the unchanged call signature lets them inherit serialization.
- `common/persistence/control_trace.py`: its read-only explicit-path behavior remains read-only.
- `app.py`, `control.py`, `updater.py`, `wizard.py`, and `board-config.py`: startup reachability and the deliberate sudo exclusion remain unchanged.
- `auto-install/pifire-install-common.sh`: production continues to use `uv sync --no-dev --inexact`; do not add a CLI migration step.

### Interfaces

```python
# common/schema_migrations.py
LEGACY_SCHEMA_VERSION: Final[int] = 10
CURRENT_SCHEMA_VERSION: Final[int] = 11
MIGRATION_SET_NAME: Final[str] = "pifire-schema"
V11_REGISTRY_ADOPTION: Final[str] = "v0011_adopt_sqlite_utils_registry"


def database_for_connection(connection: sqlite3.Connection) -> Database:
    """Wrap, but never own or close, PiFire's configured connection."""


def apply_registered_migrations(database: Database) -> None:
    """Apply known migrations only inside PiFire's active BEGIN IMMEDIATE."""
```

```python
# common/datastore.py
DB_SCHEMA_VERSION = schema_migrations.CURRENT_SCHEMA_VERSION


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Serialize legacy bootstrap and registered migrations as one batch."""


def _ensure_legacy_schema(database: Database) -> None:
    """Bring v0-v9 inputs to frozen legacy baseline v10 inside caller transaction."""
```

No registry object is exported as a supported caller API. Production callers use `datastore.connection()` or the existing explicit-path `_ensure_schema(connection)` seam; only `datastore._ensure_schema()` invokes `apply_registered_migrations()`.

---

### Task 1: Serialize legacy bootstrap before every schema decision

**Jujutsu change:** `Serialize SQLite schema bootstrap`

**Files:**
- Create: `common/schema_migrations.py`
- Modify: `common/datastore.py:13`, `_ensure_logs_retention`, `_migrate_history_to_numeric_psp`, `_ensure_schema`, and `transaction` documentation
- Modify: `pyproject.toml:[project].dependencies`
- Modify: `uv.lock`
- Modify: `tests/unit/datastore/test_datastore_concurrency.py`
- Modify: `tests/unit/datastore/test_datastore.py`
- Test: `tests/unit/datastore/test_datastore_concurrency.py`
- Test: `tests/unit/datastore/test_datastore.py`
- Test: `tests/unit/datastore/test_log_retention.py`

**Interfaces:**
- Produces: `database_for_connection(connection: sqlite3.Connection) -> sqlite_utils.Database`.
- Produces: `_ensure_legacy_schema(database: Database) -> None`, which requires an active caller-owned transaction and ends at `LEGACY_SCHEMA_VERSION == 10` for databases at or below v10.
- Preserves: `_ensure_schema(conn: sqlite3.Connection) -> None` for the normal and explicit-path callers.
- Consumes: existing `transaction(conn)` retried `BEGIN IMMEDIATE`, exact `SCHEMA`, `_queue_ddl()`, `_LEARNING_TRAJECTORY_V9_DDL`, and `_MODEL_CHALLENGER_V10_DDL`.

- [ ] **Step 1: Confirm the start gate and describe the change**

After the cumulative MPC result is finished and pushed, confirm an empty working copy, then describe the new change:

```bash
jj st
jj desc -m "Serialize SQLite schema bootstrap"
```

Do not proceed if cumulative MPC files are still modified in the working copy.

- [ ] **Step 2: Locate all schema-version and bootstrap consumers**

Use LSP references on `DB_SCHEMA_VERSION`, `_ensure_schema`, and `transaction`. Confirm that `common/persistence/learning_trajectory.py` and `common/persistence/model_evidence.py` pass existing connections to `_ensure_schema`; do not change those call sites.

- [ ] **Step 3: Add the simultaneous-start worker and RED tests**

Extend `tests/unit/datastore/test_datastore_concurrency.py`. Use raw configured connections so every child proves WAL before it enters the barrier, then call the real `_ensure_schema()`:

```python
def _schema_start_worker(db: str, barrier, reports) -> None:
    try:
        connection = sqlite3.connect(db, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.isolation_level = None
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        barrier.wait(timeout=30)
        datastore._ensure_schema(connection)
        reports.put(("ok", connection.execute("PRAGMA user_version").fetchone()[0]))
        connection.close()
    except BaseException as exc:
        reports.put(("error", type(exc).__name__, str(exc)))
        raise


def _run_simultaneous_schema_start(db: Path, workers: int = 6) -> list[tuple[object, ...]]:
    context = mp.get_context("spawn")
    barrier = context.Barrier(workers)
    reports = context.Queue()
    processes = [
        context.Process(target=_schema_start_worker, args=(str(db), barrier, reports))
        for _ in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert not process.is_alive(), "schema worker timed out"
    observed = [reports.get(timeout=10) for _ in processes]
    assert all(process.exitcode == 0 for process in processes), observed
    return observed
```

Add a helper that initializes WAL on an otherwise empty database. Add a second helper that creates only `SCHEMA + _queue_ddl()`, the current retention trigger, and `PRAGMA user_version=8`; it must not create v9/v10 objects. Run each contract for five attempts to cover more than one process schedule:

```python
@pytest.mark.parametrize("fixture", ["fresh", "v8"])
def test_simultaneous_schema_start_is_serialized_and_complete(tmp_path: Path, fixture: str) -> None:
    for attempt in range(5):
        path = tmp_path / f"{fixture}-{attempt}.db"
        _seed_wal_database(path, version=fixture)
        reports = _run_simultaneous_schema_start(path)
        assert reports == [("ok", 10)] * 6
        _assert_legacy_v10_schema_complete(path)
```

`_assert_legacy_v10_schema_complete()` must assert:

- `PRAGMA user_version == 10` for Task 1;
- the v9 tables `learning_trajectory_corpus`, `learning_trajectory_segment`, `learning_trajectory_frame`, `learning_trajectory_operation_receipt`, and `learning_fit_run` exist;
- the v9 indexes and singleton corpus row exist exactly once;
- `model_challenger_state` and `ix_model_challenger_identity` exist;
- the normalized `logs_prune` SQL equals `_logs_retention_ddl()`;
- no `history_new` or other partial migration artifact remains.

- [ ] **Step 4: Add the deterministic whole-batch rollback RED**

Start from the v8 fixture and use `sqlite3.Connection.set_authorizer()` to deny only the `PRAGMA user_version=10` write. This fails after v9 and v10 DDL have run but before the v10 stamp:

```python
def test_legacy_bootstrap_failure_rolls_back_the_entire_batch_and_retries(tmp_path: Path) -> None:
    path = tmp_path / "legacy-failure.db"
    _seed_wal_database(path, version="v8")
    connection = _open_configured_connection(path)

    def deny_v10(action, arg1, arg2, database_name, trigger_name):
        if action == sqlite3.SQLITE_PRAGMA and arg1.lower() == "user_version" and arg2 == "10":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_v10)
    with pytest.raises(sqlite3.DatabaseError):
        datastore._ensure_schema(connection)
    connection.set_authorizer(None)

    assert connection.execute("PRAGMA user_version").fetchone() == (8,)
    assert not _v9_or_v10_objects(connection)
    assert connection.execute(
        "SELECT payload FROM legacy_v8_data WHERE identity='legacy-row'"
    ).fetchone() == ("untouched",)

    datastore._ensure_schema(connection)
    assert connection.execute("PRAGMA user_version").fetchone() == (10,)
    _assert_legacy_v10_schema_complete(path)
```

Make the v8 fixture include `legacy_v8_data` and its sentinel row, matching the existing trajectory migration fixture. This is the deterministic RED: current code commits v9 before the denied v10 stamp and therefore leaves version 9 instead of version 8.

- [ ] **Step 5: Run the new contracts and verify RED before production edits**

Run:

```bash
.venv/bin/pytest -q \
  tests/unit/datastore/test_datastore_concurrency.py::test_simultaneous_schema_start_is_serialized_and_complete \
  tests/unit/datastore/test_datastore_concurrency.py::test_legacy_bootstrap_failure_rolls_back_the_entire_batch_and_retries
```

Expected before the fix:

- the whole-batch rollback test deterministically reports version 9 after the injected v10 failure; and
- at least one concurrency parametrization reports a worker error such as `database is locked`, `trigger logs_prune already exists`, or `table model_challenger_state already exists`.

Do not weaken worker count, remove the WAL assertion, catch worker errors as success, or special-case a failure message.

- [ ] **Step 6: Add the dependency and perform the dependency-cost checkpoint**

Add this production dependency to `pyproject.toml` in alphabetical position:

```toml
"sqlite-utils>=4.2.1,<5",
```

Regenerate the lock and inspect the focused dependency tree:

```bash
uv lock
uv lock --check
uv tree --package sqlite-utils --depth 1
jj diff -- pyproject.toml uv.lock
```

Acceptance for this checkpoint:

- the selected sqlite-utils version satisfies `>=4.2.1,<5` and is not 4.2;
- the direct runtime dependency list matches the seven-package reviewed envelope in Global Constraints;
- `pyproject.toml` and `uv.lock` are the only dependency files changed;
- no sqlite-utils plugin package or installer command is added.

If any item fails, stop this adoption change and return the lock diff for design review. Do not continue production editing with an unreviewed dependency graph.

- [ ] **Step 7: Add the non-owning sqlite-utils connection adapter**

Create `common/schema_migrations.py` with the version-10 constants and adapter needed by this task. Do not add the registry until Task 2:

```python
"""PiFire-owned schema migration integration.

This module wraps configured connections; it never opens or closes them.
"""

import sqlite3
from typing import Final

from sqlite_utils import Database

LEGACY_SCHEMA_VERSION: Final[int] = 10
CURRENT_SCHEMA_VERSION: Final[int] = LEGACY_SCHEMA_VERSION


def database_for_connection(connection: sqlite3.Connection) -> Database:
    return Database(
        connection,
        recursive_triggers=False,
        execute_plugins=False,
    )
```

Add a focused unit assertion using `unittest.mock.patch.object(..., wraps=Database)` that the exact existing `sqlite3.Connection` is the first constructor argument, both sqlite-utils behavior-changing defaults are disabled, and the connection still executes `SELECT 1` after `_ensure_schema()` returns. Never enter `with Database(...)`.

- [ ] **Step 8: Put all legacy schema discovery and writes under one PiFire lock**

In `common/datastore.py`:

1. Import `common.schema_migrations` and define `DB_SCHEMA_VERSION = schema_migrations.CURRENT_SCHEMA_VERSION`.
2. Extract the existing bootstrap body into `_ensure_legacy_schema(database)`.
3. Change script execution to `database.executescript(...)` so an active outer transaction is not implicitly committed.
4. Change `_ensure_logs_retention(database)` to perform its comparison and, when needed, two individual `database.execute()` calls: `DROP TRIGGER IF EXISTS logs_prune`, then the exact desired trigger DDL.
5. Change `_migrate_history_to_numeric_psp(database)` and every legacy migration statement to use `database.execute()`.
6. Remove every nested `with transaction(conn)` from the v3-v10 branches. The one outer transaction owns their rollback.
7. Keep the original starting `version` branch semantics, but read it only after the outer lock is acquired.

The outer entry point must be visibly ordered as follows:

```python
def _ensure_schema(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        database = schema_migrations.database_for_connection(conn)
        _ensure_legacy_schema(database)
```

The legacy helper retains the exact existing DDL and ordering:

```python
def _ensure_legacy_schema(database: "Database") -> None:
    database.executescript(SCHEMA + _queue_ddl())
    _ensure_logs_retention(database)
    version = database.execute("PRAGMA user_version").fetchone()[0]
    # Existing v1-v10 branches, now all using database.execute/executescript
```

Update comments that currently claim each v3/v7/v8/v9/v10 branch owns a separate transaction. State that `_ensure_schema()` now owns one serialized, atomic batch and sqlite-utils script execution preserves that transaction.

- [ ] **Step 9: Verify GREEN for the legacy concurrency and rollback contracts**

Run:

```bash
.venv/bin/pytest -q \
  tests/unit/datastore/test_datastore_concurrency.py \
  tests/unit/datastore/test_datastore.py \
  tests/unit/datastore/test_log_retention.py
```

Expected: all workers exit zero on all fresh/v8 attempts; injected v10 failure leaves the exact v8 database and retry reaches v10; existing v1-v8 data-preservation, crash, idempotency, transaction, PRAGMA, and trigger contracts remain green.

- [ ] **Step 10: Finalize the independently reviewable legacy-serialization change**

Inspect only the current Jujutsu change, confirm it is GREEN, then advance:

```bash
jj st
jj diff
jj new
```

Do not fold v11 registry behavior into this change. Task 1 ends with dependency use limited to transaction-safe wrapping/script execution and a concurrency-safe legacy v10 bootstrap.

---

### Task 2: Add the v11 registry bridge without a second version authority

**Jujutsu change:** `Adopt sqlite-utils migration registry`

**Files:**
- Modify: `common/schema_migrations.py`
- Modify: `common/datastore.py:DB_SCHEMA_VERSION`, `_ensure_schema`
- Create: `tests/unit/datastore/test_schema_migrations.py`
- Modify: `tests/unit/datastore/test_datastore.py`
- Modify: `tests/unit/common/test_learning_trajectory_store.py`
- Modify: `tests/unit/common/test_model_challenger_store.py`
- Test: all files above

**Interfaces:**
- Produces: `CURRENT_SCHEMA_VERSION == 11`, `MIGRATION_SET_NAME == "pifire-schema"`, and `V11_REGISTRY_ADOPTION == "v0011_adopt_sqlite_utils_registry"`.
- Produces: `apply_registered_migrations(database: Database) -> None`; it requires `database.conn.in_transaction` and rejects un-serialized use.
- Consumes: Task 1's `database_for_connection()` and serialized `_ensure_schema()` outer transaction.
- Preserves: exact v9 corpus and v10 challenger DDL and all persisted rows.

- [ ] **Step 1: Describe the registry change**

```bash
jj desc -m "Adopt sqlite-utils migration registry"
```

- [ ] **Step 2: Write RED tests for serialization order, v11 tracking, and idempotency**

Create `tests/unit/datastore/test_schema_migrations.py`. Reuse focused raw-connection/v10 fixture helpers locally rather than importing helpers from unrelated test modules.

Add these contracts:

```python
def test_migration_discovery_occurs_after_pifire_begin_immediate(v10_connection) -> None:
    traced: list[str] = []
    v10_connection.set_trace_callback(traced.append)
    datastore._ensure_schema(v10_connection)
    normalized = ["".join(statement.lower().split()) for statement in traced]
    begin = normalized.index("beginimmediate")
    discovery = next(
        index for index, statement in enumerate(normalized)
        if "_sqlite_migrations" in statement
    )
    assert begin < discovery


def test_v10_upgrades_to_one_named_v11_audit_record(v10_connection) -> None:
    datastore._ensure_schema(v10_connection)
    assert v10_connection.execute("PRAGMA user_version").fetchone() == (11,)
    rows = v10_connection.execute(
        "SELECT migration_set, name, applied_at FROM _sqlite_migrations ORDER BY id"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0:2] == ("pifire-schema", "v0011_adopt_sqlite_utils_registry")
    assert rows[0][2]

    applied_at = rows[0][2]
    datastore._ensure_schema(v10_connection)
    assert v10_connection.execute(
        "SELECT migration_set, name, applied_at FROM _sqlite_migrations ORDER BY id"
    ).fetchall() == [("pifire-schema", "v0011_adopt_sqlite_utils_registry", applied_at)]
```

Also assert `DB_SCHEMA_VERSION == schema_migrations.CURRENT_SCHEMA_VERSION == 11` and that `_sqlite_migrations` has primary key `id` plus a unique index over `(migration_set, name)`.

Expected RED: the module has no registry/apply API, version remains 10, and `_sqlite_migrations` does not exist.

- [ ] **Step 3: Write the v11 failure/rollback/retry RED**

Start from a committed v10 fixture. Deny only `PRAGMA user_version=11` using an authorizer. sqlite-utils will have created `_sqlite_migrations` inside the outer transaction before the v11 body reaches this denied stamp; both infrastructure DDL and stamps must roll back:

```python
def test_v11_failure_rolls_back_tracking_ddl_and_retries(v10_connection) -> None:
    def deny_v11(action, arg1, arg2, database_name, trigger_name):
        if action == sqlite3.SQLITE_PRAGMA and arg1.lower() == "user_version" and arg2 == "11":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    v10_connection.set_authorizer(deny_v11)
    with pytest.raises(sqlite3.DatabaseError):
        datastore._ensure_schema(v10_connection)
    v10_connection.set_authorizer(None)

    assert v10_connection.execute("PRAGMA user_version").fetchone() == (10,)
    assert v10_connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_sqlite_migrations'"
    ).fetchone() is None

    datastore._ensure_schema(v10_connection)
    assert v10_connection.execute("PRAGMA user_version").fetchone() == (11,)
    assert v10_connection.execute(
        "SELECT migration_set, name FROM _sqlite_migrations"
    ).fetchall() == [("pifire-schema", "v0011_adopt_sqlite_utils_registry")]
```

Expected RED: the denied v11 write never occurs and the expected tracking table is absent even after retry.

- [ ] **Step 4: Write RED tests for version-authority conflicts and historical no-ops**

Add four explicit cases:

```python
def test_applied_v11_record_with_user_version_10_fails_closed(v11_connection) -> None:
    v11_connection.execute("PRAGMA user_version=10")
    with pytest.raises(RuntimeError, match="migration authority conflict"):
        datastore._ensure_schema(v11_connection)
    assert v11_connection.execute("PRAGMA user_version").fetchone() == (10,)


@pytest.mark.parametrize("newer_version", [11, 12])
def test_existing_version_without_v11_record_is_a_public_audited_noop(
    v10_connection, newer_version: int
) -> None:
    v10_connection.execute(f"PRAGMA user_version={newer_version}")
    datastore._ensure_schema(v10_connection)
    assert v10_connection.execute("PRAGMA user_version").fetchone() == (newer_version,)
    assert v10_connection.execute(
        "SELECT migration_set, name FROM _sqlite_migrations"
    ).fetchall() == [("pifire-schema", "v0011_adopt_sqlite_utils_registry")]


def test_unknown_record_in_pifire_migration_set_fails_closed(v11_connection) -> None:
    v11_connection.execute(
        "INSERT INTO _sqlite_migrations(migration_set, name, applied_at) VALUES(?, ?, ?)",
        ("pifire-schema", "unknown_future_name", "2026-08-29 00:00:00+00:00"),
    )
    with pytest.raises(RuntimeError, match="unknown migration record"):
        datastore._ensure_schema(v11_connection)
```

The no-op must be recorded only by public `Migrations.apply()`. No test or production code may call `_record_applied()`.

Add an un-serialized runner guard:

```python
def test_registered_runner_rejects_plain_apply_without_pifire_transaction(v10_connection) -> None:
    database = schema_migrations.database_for_connection(v10_connection)
    assert not v10_connection.in_transaction
    with pytest.raises(RuntimeError, match="PiFire BEGIN IMMEDIATE"):
        schema_migrations.apply_registered_migrations(database)
    assert v10_connection.execute("PRAGMA user_version").fetchone() == (10,)
```

- [ ] **Step 5: Run the exact v11 tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/unit/datastore/test_schema_migrations.py
```

Expected: failures are missing v11 registry/version behavior, not fixture errors. Fix the fixture before production edits if it cannot independently produce a committed v10 database.

- [ ] **Step 6: Implement the private named registry and version bridge**

Extend `common/schema_migrations.py` with the public constants, private registry, and one transactional migration:

```python
from sqlite_utils.migrations import Migrations

MIGRATION_SET_NAME: Final[str] = "pifire-schema"
V11_REGISTRY_ADOPTION: Final[str] = "v0011_adopt_sqlite_utils_registry"
CURRENT_SCHEMA_VERSION: Final[int] = 11

_SCHEMA_MIGRATIONS = Migrations(MIGRATION_SET_NAME)
_MIGRATION_TARGETS: Final[dict[str, int]] = {
    V11_REGISTRY_ADOPTION: CURRENT_SCHEMA_VERSION,
}


def _user_version(database: Database) -> int:
    return int(database.execute("PRAGMA user_version").fetchone()[0])


@_SCHEMA_MIGRATIONS(name=V11_REGISTRY_ADOPTION)
def _adopt_sqlite_utils_registry(database: Database) -> None:
    version = _user_version(database)
    if version < LEGACY_SCHEMA_VERSION:
        raise RuntimeError(
            f"legacy schema must reach {LEGACY_SCHEMA_VERSION} before registered migrations; got {version}"
        )
    if version < CURRENT_SCHEMA_VERSION:
        if version != LEGACY_SCHEMA_VERSION:
            raise RuntimeError(f"cannot bridge schema version {version} to {CURRENT_SCHEMA_VERSION}")
        database.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
```

Do not set `transactional=False`. sqlite-utils' `Database.atomic()` must create a savepoint inside PiFire's already-active outer transaction, coupling the body and tracking insert.

- [ ] **Step 7: Implement the guarded runner and authority validation**

Implement `apply_registered_migrations()` with this order:

```python
def apply_registered_migrations(database: Database) -> None:
    if not database.conn.in_transaction:
        raise RuntimeError("registered migrations require PiFire BEGIN IMMEDIATE")

    _SCHEMA_MIGRATIONS.apply(database)

    version = _user_version(database)
    applied = [migration.name for migration in _SCHEMA_MIGRATIONS.applied(database)]
    applied_set = set(applied)
    known_set = set(_MIGRATION_TARGETS)

    unknown = applied_set - known_set
    if unknown:
        raise RuntimeError(f"unknown migration record in {MIGRATION_SET_NAME}: {sorted(unknown)}")

    ahead = [
        name for name, target in _MIGRATION_TARGETS.items()
        if name in applied_set and version < target
    ]
    missing = [
        name for name, target in _MIGRATION_TARGETS.items()
        if version >= target and name not in applied_set
    ]
    if ahead or missing or version < CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            "migration authority conflict: "
            f"user_version={version}, applied={applied}, ahead={ahead}, missing={missing}"
        )
```

The validator runs before the outer commit. Therefore a newly inserted record also rolls back if consistency fails. Keep `_SCHEMA_MIGRATIONS` private so production code has no supported route to plain `apply()`.

- [ ] **Step 8: Invoke the registry while the PiFire lock is still held**

Update only the inside of Task 1's outer transaction:

```python
def _ensure_schema(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        database = schema_migrations.database_for_connection(conn)
        _ensure_legacy_schema(database)
        schema_migrations.apply_registered_migrations(database)
```

The call order is mandatory. Do not call `pending()` or `applied()` in `connection()` before `_ensure_schema()`, do not add a preliminary read outside the transaction, and do not open a second connection for the registry.

- [ ] **Step 9: Update legacy migration tests without weakening their local contracts**

Update only successful-final-version expectations and transaction-boundary comments:

- In `tests/unit/datastore/test_datastore.py`, use `datastore.DB_SCHEMA_VERSION` for successful current-version assertions. Preserve v1/v2/v3/v7 affinity, row-preservation, idempotency, and injected-crash assertions.
- In `tests/unit/common/test_learning_trajectory_store.py`, v9 failure injection still asserts exact rollback to v8 with no v9 objects. Successful retry now asserts `PRAGMA user_version == datastore.DB_SCHEMA_VERSION == 11`, the v9 tables, and exactly one v11 registry record.
- In `tests/unit/common/test_model_challenger_store.py`, v10 failure injection still asserts exact rollback to v9 with no challenger table. Successful retry now asserts `PRAGMA user_version == datastore.DB_SCHEMA_VERSION == 11`, the v10 table/index, preserved v9 authority rows, and exactly one v11 registry record.
- Any current-schema idempotency test must capture the original `applied_at`, reconnect, and assert neither duplicate row nor timestamp rewrite.

Do not rename v9/v10 tests as v11 tests: those files still defend the observable schema steps they own.

- [ ] **Step 10: Verify GREEN across the bridge and legacy migrations**

Run:

```bash
.venv/bin/pytest -q \
  tests/unit/datastore/test_schema_migrations.py \
  tests/unit/datastore/test_datastore_concurrency.py \
  tests/unit/datastore/test_datastore.py \
  tests/unit/common/test_learning_trajectory_store.py::test_schema_v9_migration_is_additive_and_declares_corpus_tables \
  tests/unit/common/test_learning_trajectory_store.py::test_schema_v9_migration_rolls_back_before_version_bump \
  tests/unit/common/test_learning_trajectory_store.py::test_schema_v9_migration_is_idempotent_and_preserves_trajectory_rows \
  tests/unit/common/test_model_challenger_store.py::test_schema_v10_migration_is_additive_and_preserves_every_v9_authority_row \
  tests/unit/common/test_model_challenger_store.py::test_schema_v10_migration_rolls_back_ddl_and_version_bump_together
```

Expected:

- fresh, v8, v9, and v10 inputs converge to v11;
- simultaneous processes all succeed;
- injected failures leave the exact input version/data and no tracking row;
- retry applies exactly once;
- version/audit mismatches fail before commit;
- v9 corpus and v10 challenger data remain unchanged.

- [ ] **Step 11: Finalize the independently reviewable registry change**

```bash
jj st
jj diff
jj new
```

The completed change must contain no connection-by-filename use, sqlite-utils context manager, CLI call, settings/pellet migration conversion, private migration-record call, or down-migration API.

---

### Task 3: Guard PRAGMAs, file ownership, and live sidecars

**Jujutsu change:** `Guard SQLite migration file ownership`

**Files:**
- Modify: `tests/unit/datastore/test_schema_migrations.py`
- Test: `tests/unit/datastore/test_schema_migrations.py`
- Test: `tests/unit/datastore/test_entry_points_initialise_the_datastore.py`
- Test: existing explicit-path persistence tests selected below

**Interfaces:**
- Consumes: Task 2's complete `_ensure_schema()` path.
- Produces: platform-neutral POSIX ownership/mode and sidecar regression coverage.
- Preserves: PiFire connection PRAGMAs and board-config sudo exclusion.

- [ ] **Step 1: Describe the guard change**

```bash
jj desc -m "Guard SQLite migration file ownership"
```

- [ ] **Step 2: Add the configured-connection PRAGMA contract**

Add a test that obtains the real `datastore.connection()` on a fresh path and asserts after v11 migration:

```python
assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
assert connection.execute("PRAGMA synchronous").fetchone() == (1,)  # NORMAL
assert connection.execute("PRAGMA busy_timeout").fetchone() == (5000,)
assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
assert connection.execute("PRAGMA recursive_triggers").fetchone() == (0,)
assert connection.isolation_level is None
assert datastore.connection() is connection
assert connection.execute("SELECT 1").fetchone() == (1,)
```

This proves the wrapper neither closes/replaces the connection nor enables sqlite-utils' recursive-trigger default. The constructor spy from Task 1 separately proves `execute_plugins=False`.

- [ ] **Step 3: Add a live-sidecar ownership and mode test**

Use a spawned child so it can set `umask=002` without changing the pytest process. The child opens a pre-existing mode-`0o664` v10 database through the real datastore, commits one write to keep WAL active, signals `ready`, and waits on `release` before closing:

```python
def _hold_migrated_database_open(path: str, ready, release, reports) -> None:
    previous_umask = os.umask(0o002)
    try:
        datastore._reset_for_tests(path)
        connection = datastore.connection()
        connection.execute(
            "INSERT INTO kv(key, value) VALUES('permission-probe', '{\"ok\":true}')"
        )
        reports.put((
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute("PRAGMA journal_mode").fetchone()[0],
        ))
        ready.set()
        assert release.wait(timeout=30)
    finally:
        datastore._reset_for_tests(None)
        os.umask(previous_umask)
```

In the parent, capture database `st_uid`, `st_gid`, and `stat.S_IMODE(st_mode)` before spawn. While the child is waiting, assert:

```python
assert reports.get(timeout=30) == (11, "wal")
assert ready.wait(timeout=30)
assert (path.stat().st_uid, path.stat().st_gid, stat.S_IMODE(path.stat().st_mode)) == before
for suffix in ("-wal", "-shm"):
    sidecar = Path(f"{path}{suffix}")
    assert sidecar.exists()
    metadata = sidecar.stat()
    assert (metadata.st_uid, metadata.st_gid) == (before_uid, before_gid)
    assert stat.S_IMODE(metadata.st_mode) & 0o020  # group-writable under umask=002
```

Always set `release` and join the child in `finally`; assert exit code zero. Do not assert transient sidecars after every connection closes, because SQLite may legitimately remove them then.

- [ ] **Step 4: Verify startup boundaries and explicit-path callers remain unchanged**

Run focused tests that exercise the two explicit `_ensure_schema(existing_connection)` callers and the board-config ownership exclusion:

```bash
.venv/bin/pytest -q \
  tests/unit/datastore/test_schema_migrations.py \
  tests/unit/datastore/test_entry_points_initialise_the_datastore.py \
  tests/unit/common/test_learning_trajectory_store.py::test_schema_v9_migration_is_additive_and_declares_corpus_tables \
  tests/unit/common/test_model_challenger_store.py::test_schema_v10_migration_is_additive_and_preserves_every_v9_authority_row
```

Expected: explicit-path connections migrate without being closed or replaced, and `board-config.py` remains excluded from `datastore.init()`.

- [ ] **Step 5: Finalize the permission guard change**

```bash
jj st
jj diff
jj new
```

No production code should change in this task unless the new behavioral test exposes a real ownership/PRAGMA regression. If it does, fix the connection-owning source rather than loosening the test.

---

### Task 4: Cross-platform verification, independent review, and cleanup

**Files:**
- Modify only if review finds a concrete defect in files already listed above.
- Do not create a new design document, migration README, compatibility shim, or release-specific cleanup scaffold.

**Interfaces:**
- Verifies the complete selective-adoption contract.
- Produces an independently reviewed set of Jujutsu changes ready for the integration owner.

- [ ] **Step 1: Verify the macOS dependency and focused behavior surface**

Use the existing environment where possible; on Darwin sync without bluepy:

```bash
uv sync --no-install-package bluepy
uv lock --check
.venv/bin/pytest -q \
  tests/unit/datastore/test_schema_migrations.py \
  tests/unit/datastore/test_datastore_concurrency.py \
  tests/unit/datastore/test_datastore_crash.py \
  tests/unit/datastore/test_datastore.py \
  tests/unit/datastore/test_log_retention.py \
  tests/unit/datastore/test_entry_points_initialise_the_datastore.py \
  tests/unit/datastore/test_settings_shape_migration.py \
  tests/unit/datastore/test_pellets_shape_migration.py
```

This is the macOS acceptance set. Do not substitute a source-text assertion for the multiprocessing or live-sidecar tests.

- [ ] **Step 2: Verify the production install path and behavior on Linux**

On the user-provided Linux host, use the production dependency mode first, prove sqlite-utils is importable from that environment, then restore dev dependencies for the focused tests:

```bash
uv sync --no-dev --inexact
uv run --no-sync python -c "import importlib.metadata; print(importlib.metadata.version('sqlite-utils'))"
uv sync
uv lock --check
uv run pytest -q \
  tests/unit/datastore/test_schema_migrations.py \
  tests/unit/datastore/test_datastore_concurrency.py \
  tests/unit/datastore/test_datastore_crash.py \
  tests/unit/datastore/test_datastore.py \
  tests/unit/datastore/test_log_retention.py \
  tests/unit/datastore/test_entry_points_initialise_the_datastore.py \
  tests/unit/datastore/test_settings_shape_migration.py \
  tests/unit/datastore/test_pellets_shape_migration.py
```

Record the locked sqlite-utils version and the macOS/Linux results in the execution ledger. A successful macOS run does not replace Linux permission/sidecar and production-sync verification.

- [ ] **Step 3: Request independent review before integration**

Use the `requesting-code-review` skill and give a reviewer both implementation change IDs. Require the reviewer to check, with file/line evidence:

1. `BEGIN IMMEDIATE` is acquired before any schema or migration discovery.
2. No `sqlite3.Connection.executescript()` remains on the active migration path.
3. No nested `transaction(conn)` remains inside legacy bootstrap.
4. `Database` receives the existing connection plus `recursive_triggers=False` and `execute_plugins=False`, and is never used as a context manager.
5. `_SCHEMA_MIGRATIONS.apply()` is reachable only through the guarded runner under `_ensure_schema()`.
6. v11 `user_version` and `_sqlite_migrations` record share the same rollback boundary.
7. Known missing records at an already-newer version are public audited no-ops; records ahead of `user_version` and unknown names fail closed.
8. Fresh/v8 concurrent startup and failure/retry tests genuinely exercise WAL and separate spawned processes.
9. Database, WAL, and SHM ownership/mode assertions are live while the connection is open.
10. Settings/pellet registries, explicit-path ownership, startup entry points, and installer behavior are unchanged.
11. The dependency/lock diff stays within `sqlite-utils>=4.2.1,<5` and the reviewed dependency-cost envelope.
12. No full persistence replacement, CLI migration path, table transform, non-transactional migration, reverse migration, or private sqlite-utils API was introduced.

- [ ] **Step 4: Address review findings in a separate Jujutsu change**

If the independent reviewer finds a concrete issue, create a new change before editing:

```bash
jj desc -m "Address SQLite migration review"
```

Apply only evidence-backed corrections, rerun the smallest affected RED/GREEN test plus the complete focused migration set, then:

```bash
jj st
jj diff
jj new
```

If review is clean, do not create an empty or synthetic review-fix change.

- [ ] **Step 5: Perform final cleanup and handoff**

Confirm all of the following before handing changes to the integration owner:

- no temporary stress databases, WAL/SHM files, injected authorizers, or test-only environment hooks exist outside pytest temporary directories;
- no duplicate version constant exists outside `common/schema_migrations.py` except the intentional `datastore.DB_SCHEMA_VERSION` compatibility alias;
- no obsolete nested-transaction comments or stale successful-final-version assertions remain;
- `pyproject.toml` and `uv.lock` agree;
- the working copy `@` is empty after the last finalized change;
- the implementation changes are based on the already-pushed cumulative MPC result.

Use `jj st` and `jj log` for the final Jujutsu handoff. The integration owner, not an individual task worker, advances and pushes the repository's existing tracked bookmark after review.

---

## Final Acceptance Matrix

| Contract | Required evidence |
|---|---|
| Selective adoption | Only `common/schema_migrations.py` imports sqlite-utils; datastore/persistence APIs and data-shape registries remain PiFire-owned. |
| Serialized discovery | Trace test shows `BEGIN IMMEDIATE` before the first `_sqlite_migrations` access; un-serialized runner test fails closed. |
| Legacy concurrency repair | Five fresh and five v8 attempts with six spawned WAL workers complete with zero worker errors. |
| Atomic failure | Denied v10 returns the exact v8 fixture; denied v11 returns exact v10 with no migration table/record; both retries converge once. |
| One version authority | Central current version is 11; v11 audit and `user_version` agree; ahead/unknown conflicts fail; missing historical row at version 11+ is publicly audited without a downgrade. |
| Connection policy | WAL, NORMAL, 5000 ms busy timeout, foreign keys, autocommit ownership, and recursive-trigger state are unchanged. |
| File authority | Existing database uid/gid/mode do not change; live WAL/SHM sidecars share uid/gid and are group-writable under `umask=002`. |
| Dependency decision | Lock contains a reviewed sqlite-utils 4.x version satisfying `>=4.2.1,<5`, never 4.2, within the accepted dependency-cost envelope. |
| No unsafe APIs | No filename/CLI/context-manager database, `Table.transform`, `transactional=False`, private `_record_applied`, or down migration. |
| Platform coverage | Focused acceptance set passes on macOS without bluepy and on Linux through the production dependency path. |
| Rollback/retry | Failed forward batches leave no partial DDL/stamp/audit and retry once; schema downgrade is explicitly unsupported and uses pre-upgrade database restore. |
| Review/integration | Independent review is clean or corrected in a separate GREEN Jujutsu change; integration begins only after cumulative MPC was committed and pushed. |
