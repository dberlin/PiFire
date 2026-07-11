# Valkey → SQLite Datastore Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Valkey with an embedded SQLite datastore for PiFire's shared
state, queues, and history — removing the `valkey-server` daemon, gaining
durable history/metrics, keeping every accessor signature identical.

**Architecture:** A new `common/datastore.py` owns a per-process, **thread-local**
SQLite connection (WAL, `synchronous=NORMAL`), the schema, a first-boot
JSON→SQLite migration, and a `SQLITE_BUSY` retry wrapper. `common/common.py`'s
accessors are rewritten to call it, signatures unchanged. `ValkeyQueue` →
`SqliteQueue` (one table per queue), plus `SqliteMembershipList` for
`users:connected`. Hard cut: Valkey is removed entirely.

**Tech Stack:** Python 3, `sqlite3` (stdlib), pytest. No new third-party deps.

## Global Constraints

- **Preserve accessor signatures.** Every public function/method in
  `common/common.py`, `SqliteQueue`, and the `Store` seam keeps the exact name,
  parameters, and return type it has today. Only bodies change.
- **Hard cut.** No runtime Valkey fallback. `import valkey` must not appear in
  production code after Task 19.
- **Thread-local connection, per process.** Never share a `sqlite3.Connection`
  across threads or processes. Each process/thread opens its own.
- **PRAGMAs on every connection:** `journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout=5000`, `foreign_keys=ON`.
- **JSON validity CHECKs** on all-JSON surfaces only (`kv`, `history` JSON
  columns, `metrics`, the JSON queues). **Not** on `list_warnings`,
  `list_users_connected`, or `logs` (raw strings).
- **DB path** comes from one place: `datastore.DB_PATH` (env `PIFIRE_DB_PATH`,
  default `<repo-root>/pifire.db`).
- **Ruff format** changed Python files before every commit (`uvx ruff format <files>`).
- Spec: `docs/superpowers/specs/2026-07-11-valkey-to-sqlite-migration-design.md`.

---

## File Structure

| File | Responsibility |
|---|---|
| `common/datastore.py` | **New.** Thread-local connection, PRAGMAs, schema DDL, `init()`, first-boot import, blob helpers, transaction/retry helpers. |
| `common/sqlite_queue.py` | **New.** `SqliteQueue` (one table per queue) + `SqliteMembershipList`. Replaces `common/valkey_queue.py`. |
| `common/sqlite_log_handler.py` | **New.** `SqliteLogHandler(logging.Handler)`. Replaces `common/valkey_handler.py`. |
| `common/common.py` | Accessor bodies rewritten to call `datastore`/`SqliteQueue`; `cmdsts` + `config_set` deleted. |
| `controller/runtime/store.py` | `ValkeyStore` → `SqliteStore`; `InMemoryStore` history-cap fix. |
| `scripts/{export,import}-{settings,pelletdb}-json` | **New.** Explicit config round-trip; import shares migration code. |
| `tests/oracle/` | **New.** T0 recorded-golden fixtures + capture script. |
| `tests/test_datastore.py`, `tests/test_sqlite_queue.py`, `tests/test_datastore_concurrency.py`, `tests/test_datastore_crash.py`, `tests/test_startup_migration.py`, `tests/test_webapp_sqlite.py` | **New** test tiers. |
| `common/valkey_queue.py`, `common/valkey_handler.py` | **Deleted** (Task 19). |
| `pyproject.toml`, `uv.lock`, `auto-install/**` | Drop `valkey` / `valkey-server` (Task 19). |

Build order keeps the suite green at every step: capture the oracle first (Task
1, while Valkey still runs), build the SQLite layer bottom-up (Tasks 2–6),
rewrite accessors against the oracle (Tasks 7–12), add migration + scripts
(13–14), repoint the Store seam and existing suites (15), add new tiers (16–18),
then delete Valkey (19).

---

### Task 1: Capture the Valkey behavioral oracle (T0)

Runs against the **current, unmodified** code with a live `valkey-server`. Records
every tricky accessor's output as committed JSON fixtures so the SQLite rewrite
can be proven byte-for-byte identical. Do this before any code changes.

**Files:**
- Create: `tests/oracle/capture_oracle.py`
- Create: `tests/oracle/fixtures/` (committed JSON output)
- Create: `tests/oracle/__init__.py` (empty)

**Interfaces:**
- Produces: `tests/oracle/fixtures/*.json` — each file a recorded
  `{"ops": [...], "result": <value>}` snapshot keyed by scenario name.

- [ ] **Step 1: Write the capture script**

```python
# tests/oracle/capture_oracle.py
"""Record current Valkey-backed accessor behavior as golden fixtures.

Run ONCE against the unmodified codebase with a live valkey-server:
    python -m tests.oracle.capture_oracle
Commit the resulting tests/oracle/fixtures/*.json. The SQLite rewrite is
asserted byte-for-byte against these (see tests/test_datastore.py::test_oracle_*).
"""
import json
import os

from common import common as c

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _dump(name, value):
    os.makedirs(FIX, exist_ok=True)
    with open(os.path.join(FIX, f"{name}.json"), "w") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)


def scenario_control_merge():
    c.cmdsts.delete("control:general")
    c.cmdsts.delete("control:write")
    c.write_control({"mode": "Stop", "nested": {"a": 1, "b": 2}}, c.WriteKind.OVERWRITE, origin="test")
    c.write_control({"nested": {"b": 9, "c": 3}}, c.WriteKind.MERGE, origin="webapp")
    before = c.read_control()
    c.execute_control_writes()
    after = c.read_control()
    return {"before_execute": before, "after_execute": after}


def scenario_history_cap():
    c.cmdsts.delete("control:history")
    sample = {
        "probe_history": {"primary": {"Grill": 225}, "food": {"P1": 145}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {"Grill": 0},
    }
    for _ in range(5):
        c.write_history(sample, maxsizelines=3)
    return {"len": c.cmdsts.llen("control:history"), "items": c.read_history()}


def scenario_metrics_replace_last():
    c.cmdsts.delete("metrics:general")
    m = c.default_metrics()
    m["mode"] = "Startup"
    c.write_metrics(m, new_metric=True)
    m2 = c.default_metrics()
    m2["mode"] = "Hold"
    c.write_metrics(m2, new_metric=False)
    return {"last": c.read_metrics(), "all_len": len(c.read_metrics(all=True))}


def scenario_warnings():
    c.cmdsts.delete("warnings")
    c.write_warning("first")
    c.write_warning("second")
    return {"read1": c.read_warnings(), "read2_after_clear": c.read_warnings()}


def main():
    _dump("control_merge", scenario_control_merge())
    _dump("history_cap", scenario_history_cap())
    _dump("metrics_replace_last", scenario_metrics_replace_last())
    _dump("warnings", scenario_warnings())
    print("wrote fixtures to", FIX)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against live Valkey to produce fixtures**

Run: `valkey-server --daemonize yes 2>/dev/null; python -m tests.oracle.capture_oracle`
Expected: `wrote fixtures to .../tests/oracle/fixtures` and four `.json` files exist.

- [ ] **Step 3: Sanity-check one fixture**

Run: `python -c "import json;d=json.load(open('tests/oracle/fixtures/control_merge.json'));print(d['after_execute']['nested'])"`
Expected: `{'a': 1, 'b': 9, 'c': 3}` (deep-merge applied, `origin` absent).

- [ ] **Step 4: Commit**

```bash
uvx ruff format tests/oracle/capture_oracle.py
git add tests/oracle
git commit -m "test(oracle): capture Valkey accessor behavior as golden fixtures"
```

---

### Task 2: `datastore.py` — connection, PRAGMAs, schema, init

**Files:**
- Create: `common/datastore.py`
- Create: `tests/test_datastore.py`

**Interfaces:**
- Produces:
  - `DB_PATH: str`
  - `connection() -> sqlite3.Connection` (thread-local, PRAGMAs applied)
  - `transaction()` — context manager, `BEGIN IMMEDIATE`/COMMIT/ROLLBACK with BUSY retry
  - `execute_write(sql: str, params: tuple = ()) -> sqlite3.Cursor` (autocommit + BUSY retry)
  - `init() -> None` — create schema (idempotent) and run first-boot import (Task 13 fills the import)
  - `_reset_for_tests(path: str) -> None` — point DB_PATH at a temp file and drop the thread-local cache

- [ ] **Step 1: Write failing tests**

```python
# tests/test_datastore.py
import json
import os
import sqlite3

import pytest

from common import datastore


@pytest.fixture
def ds(tmp_path):
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


def test_pragmas_applied(ds):
    conn = ds.connection()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_schema_tables_exist(ds):
    names = {r[0] for r in ds.connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ["kv", "history", "metrics", "logs",
              "queue_control_write", "queue_systemq", "queue_systemo",
              "queue_displayq", "queue_autotune",
              "list_warnings", "list_users_connected"]:
        assert t in names, t


def test_init_idempotent(ds):
    ds.init()  # second call must not raise
    assert ds.connection().execute("PRAGMA user_version").fetchone()[0] >= 1


def test_kv_check_rejects_non_json(ds):
    with pytest.raises(sqlite3.IntegrityError):
        ds.execute_write("INSERT INTO kv(key,value) VALUES('x','{not json')")


def test_transaction_rolls_back_on_error(ds):
    with pytest.raises(RuntimeError):
        with ds.transaction() as conn:
            conn.execute("INSERT INTO kv(key,value) VALUES('a','1')")
            raise RuntimeError("boom")
    assert ds.connection().execute("SELECT COUNT(*) FROM kv WHERE key='a'").fetchone()[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_datastore.py -x`
Expected: FAIL with `ModuleNotFoundError: No module named 'common.datastore'`.

- [ ] **Step 3: Implement `common/datastore.py`**

```python
# common/datastore.py
"""SQLite datastore: thread-local connection, schema, transactions, first-boot
import. The only module that opens the database; common.py talks to it."""
import os
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PIFIRE_DB_PATH", os.path.join(_HERE, "..", "pifire.db"))

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL CHECK(json_valid(value))
);
CREATE TABLE IF NOT EXISTS history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    psp            REAL,
    primary_temps  TEXT NOT NULL CHECK(json_valid(primary_temps)),
    food_temps     TEXT NOT NULL CHECK(json_valid(food_temps)),
    aux_temps      TEXT NOT NULL CHECK(json_valid(aux_temps)),
    notify_targets TEXT NOT NULL CHECK(json_valid(notify_targets)),
    ext_data       TEXT CHECK(ext_data IS NULL OR json_valid(ext_data))
);
CREATE INDEX IF NOT EXISTS ix_history_ts ON history(ts);
CREATE TABLE IF NOT EXISTS metrics (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL CHECK(json_valid(data))
);
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_logs_name_id ON logs(name, id);
"""

# one table per queue; JSON queues carry a json_valid CHECK, raw lists do not
_JSON_QUEUE_TABLES = [
    "queue_control_write", "queue_systemq", "queue_systemo",
    "queue_displayq", "queue_autotune",
]
_RAW_LIST_TABLES = ["list_warnings", "list_users_connected"]


def _queue_ddl():
    ddl = []
    for t in _JSON_QUEUE_TABLES:
        ddl.append(
            f"CREATE TABLE IF NOT EXISTS {t} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "value TEXT NOT NULL CHECK(json_valid(value)));"
        )
    for t in _RAW_LIST_TABLES:
        ddl.append(
            f"CREATE TABLE IF NOT EXISTS {t} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL);"
        )
    return "\n".join(ddl)


def connection():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = None  # autocommit; we manage txns explicitly
        _local.conn = conn
    return conn


def _retry(fn, attempts=50):
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                time.sleep(0.005 * (i + 1))
                continue
            raise
    raise sqlite3.OperationalError("SQLITE_BUSY: retries exhausted")


def execute_write(sql, params=()):
    return _retry(lambda: connection().execute(sql, params))


class transaction:
    """`with transaction() as conn:` — BEGIN IMMEDIATE / COMMIT / ROLLBACK,
    retrying only the BEGIN on BUSY."""

    def __enter__(self):
        self.conn = connection()
        _retry(lambda: self.conn.execute("BEGIN IMMEDIATE"))
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


def init():
    conn = connection()
    conn.executescript(SCHEMA + _queue_ddl())
    if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
        conn.execute("PRAGMA user_version=1")
    _first_boot_import()  # filled in Task 13


def _first_boot_import():
    pass  # Task 13


def _reset_for_tests(path):
    """Test hook: repoint DB_PATH and drop the cached thread-local connection."""
    global DB_PATH
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    if path is not None:
        DB_PATH = path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_datastore.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/datastore.py tests/test_datastore.py
git add common/datastore.py tests/test_datastore.py
git commit -m "feat(datastore): connection, PRAGMAs, schema, transactions"
```

---

### Task 3: `datastore` blob helpers (kv)

**Files:**
- Modify: `common/datastore.py`
- Modify: `tests/test_datastore.py`

**Interfaces:**
- Produces: `get_blob(key) -> str | None`, `set_blob(key, value_str)`,
  `delete_blob(key)`, `exists_blob(key) -> bool`. `get_blob` returns the raw
  stored string (caller `json.loads`), `None` if absent (matches Valkey `get`).

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_datastore.py
def test_blob_roundtrip_and_missing(ds):
    assert ds.get_blob("k") is None            # missing -> None (matches Valkey)
    ds.set_blob("k", '{"a": 1}')
    assert ds.get_blob("k") == '{"a": 1}'
    assert ds.exists_blob("k") is True
    ds.set_blob("k", '{"a": 2}')               # overwrite
    assert ds.get_blob("k") == '{"a": 2}'
    ds.delete_blob("k")
    assert ds.get_blob("k") is None
    assert ds.exists_blob("k") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_datastore.py::test_blob_roundtrip_and_missing -x`
Expected: FAIL with `AttributeError: module 'common.datastore' has no attribute 'get_blob'`.

- [ ] **Step 3: Implement**

```python
# append to common/datastore.py
def get_blob(key):
    row = connection().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def set_blob(key, value_str):
    execute_write("INSERT INTO kv(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, value_str))


def delete_blob(key):
    execute_write("DELETE FROM kv WHERE key=?", (key,))


def exists_blob(key):
    return connection().execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone() is not None
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_datastore.py::test_blob_roundtrip_and_missing -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/datastore.py tests/test_datastore.py
git add common/datastore.py tests/test_datastore.py
git commit -m "feat(datastore): kv blob helpers"
```

---

### Task 4: `SqliteQueue` (one table per queue)

**Files:**
- Create: `common/sqlite_queue.py`
- Create: `tests/test_sqlite_queue.py`

**Interfaces:**
- Consumes: `datastore.connection`, `datastore.execute_write`, `datastore.transaction`.
- Produces: `SqliteQueue(table)` with `push(data)`, `pop() -> obj | None`,
  `length() -> int`, `list(start=0, end=-1) -> list`, `flush()`. Values are
  JSON-serialized (`json.dumps` on push, `json.loads` on read) — identical to
  `ValkeyQueue`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sqlite_queue.py
import sqlite3

import pytest

from common import datastore
from common.sqlite_queue import SqliteQueue


@pytest.fixture
def ds(tmp_path):
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


def test_fifo_roundtrip(ds):
    q = SqliteQueue("queue_systemq")
    assert q.length() == 0
    assert q.pop() is None
    q.push(["a", 1])
    q.push({"b": 2})
    assert q.length() == 2
    assert q.list() == [["a", 1], {"b": 2}]   # non-destructive peek, FIFO
    assert q.pop() == ["a", 1]                 # head first
    assert q.pop() == {"b": 2}
    assert q.length() == 0


def test_flush(ds):
    q = SqliteQueue("queue_displayq")
    q.push(["text", "ERROR"])
    q.flush()
    assert q.length() == 0


def test_json_queue_rejects_via_check(ds):
    # raw (non-JSON) insert into a JSON queue table must be rejected by the CHECK
    with pytest.raises(sqlite3.IntegrityError):
        datastore.execute_write("INSERT INTO queue_control_write(value) VALUES('raw')")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_sqlite_queue.py -x`
Expected: FAIL with `ModuleNotFoundError: No module named 'common.sqlite_queue'`.

- [ ] **Step 3: Implement**

```python
# common/sqlite_queue.py
"""List-backed queues on SQLite, one table per queue. API-compatible with the
old ValkeyQueue (push/pop/length/list/flush). Plus SqliteMembershipList for the
users:connected remove-by-value case."""
import json

from common import datastore

_ALLOWED_TABLES = {
    "queue_control_write", "queue_systemq", "queue_systemo",
    "queue_displayq", "queue_autotune",
    "list_warnings", "list_users_connected",
}


def _check_table(table):
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"unknown queue table: {table!r}")


class SqliteQueue:
    def __init__(self, table, raw=False):
        _check_table(table)
        self.table = table
        self.raw = raw  # raw=True stores strings verbatim (list_warnings)

    def _encode(self, data):
        return data if self.raw else json.dumps(data)

    def _decode(self, value):
        return value if self.raw else json.loads(value)

    def push(self, data):
        datastore.execute_write(
            f"INSERT INTO {self.table}(value) VALUES(?)", (self._encode(data),))

    def pop(self):
        with datastore.transaction() as conn:
            row = conn.execute(
                f"SELECT id, value FROM {self.table} ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute(f"DELETE FROM {self.table} WHERE id=?", (row[0],))
            return self._decode(row[1])

    def length(self):
        return datastore.connection().execute(
            f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]

    def list(self, start=0, end=-1):
        rows = datastore.connection().execute(
            f"SELECT value FROM {self.table} ORDER BY id").fetchall()
        values = [self._decode(r[0]) for r in rows]
        if end == -1:
            return values[start:]
        return values[start:end + 1]

    def flush(self):
        datastore.execute_write(f"DELETE FROM {self.table}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_sqlite_queue.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/sqlite_queue.py tests/test_sqlite_queue.py
git add common/sqlite_queue.py tests/test_sqlite_queue.py
git commit -m "feat(sqlite_queue): table-per-queue SqliteQueue"
```

---

### Task 5: `SqliteMembershipList` (users:connected)

**Files:**
- Modify: `common/sqlite_queue.py`
- Modify: `tests/test_sqlite_queue.py`

**Interfaces:**
- Produces: `SqliteMembershipList(table)` with `add(value)`, `remove(value)`
  (delete ALL matching rows, like `lrem(key, 0, value)`), `list() -> list[str]`,
  `flush()`. Values are raw strings.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_sqlite_queue.py
from common.sqlite_queue import SqliteMembershipList


def test_membership_add_remove(ds):
    m = SqliteMembershipList("list_users_connected")
    m.add("sidA")
    m.add("sidB")
    m.add("sidA")                       # duplicate allowed (matches rpush)
    assert sorted(m.list()) == ["sidA", "sidA", "sidB"]
    m.remove("sidA")                    # removes ALL "sidA" (lrem count=0)
    assert m.list() == ["sidB"]
    m.flush()
    assert m.list() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_sqlite_queue.py::test_membership_add_remove -x`
Expected: FAIL with `ImportError: cannot import name 'SqliteMembershipList'`.

- [ ] **Step 3: Implement**

```python
# append to common/sqlite_queue.py
class SqliteMembershipList:
    """Raw-string membership list with remove-by-value (Valkey lrem count=0)."""

    def __init__(self, table):
        _check_table(table)
        self.table = table

    def add(self, value):
        datastore.execute_write(f"INSERT INTO {self.table}(value) VALUES(?)", (value,))

    def remove(self, value):
        datastore.execute_write(f"DELETE FROM {self.table} WHERE value=?", (value,))

    def list(self):
        rows = datastore.connection().execute(
            f"SELECT value FROM {self.table} ORDER BY id").fetchall()
        return [r[0] for r in rows]

    def flush(self):
        datastore.execute_write(f"DELETE FROM {self.table}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_sqlite_queue.py::test_membership_add_remove -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/sqlite_queue.py tests/test_sqlite_queue.py
git add common/sqlite_queue.py tests/test_sqlite_queue.py
git commit -m "feat(sqlite_queue): SqliteMembershipList for users:connected"
```

---

### Task 6: `SqliteLogHandler` + logs helpers

**Files:**
- Create: `common/sqlite_log_handler.py`
- Modify: `common/datastore.py` (logs read helper)
- Modify: `tests/test_datastore.py`

**Interfaces:**
- Produces:
  - `SqliteLogHandler(name)` — `logging.Handler`; `emit` INSERTs `(name, ts, message)`.
  - `datastore.read_log(name, num=0) -> list[str]` — newest-first (`ORDER BY id DESC`),
    optional limit; `datastore.clear_log(name)`.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_datastore.py
import logging


def test_log_handler_and_read(ds):
    from common.sqlite_log_handler import SqliteLogHandler
    logger = logging.getLogger("t_events")
    logger.setLevel(logging.INFO)
    logger.addHandler(SqliteLogHandler("events"))
    logger.info("first")
    logger.info("second")
    assert ds.read_log("events", num=1) == ["second"]      # newest-first, limited
    assert ds.read_log("events") == ["second", "first"]
    ds.clear_log("events")
    assert ds.read_log("events") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_datastore.py::test_log_handler_and_read -x`
Expected: FAIL with `ModuleNotFoundError: No module named 'common.sqlite_log_handler'`.

- [ ] **Step 3: Implement**

```python
# common/sqlite_log_handler.py
import logging
import time

from common import datastore


class SqliteLogHandler(logging.Handler):
    """Log sink writing formatted records into the logs table under `name`."""

    def __init__(self, name):
        super().__init__()
        self.name = name

    def emit(self, record):
        try:
            datastore.execute_write(
                "INSERT INTO logs(name, ts, message) VALUES(?,?,?)",
                (self.name, int(time.time() * 1000), self.format(record)))
        except Exception:  # never let logging crash the caller
            self.handleError(record)
```

```python
# append to common/datastore.py
def read_log(name, num=0):
    sql = "SELECT message FROM logs WHERE name=? ORDER BY id DESC"
    params = (name,)
    if num > 0:
        sql += " LIMIT ?"
        params = (name, num)
    return [r[0] for r in connection().execute(sql, params).fetchall()]


def clear_log(name):
    execute_write("DELETE FROM logs WHERE name=?", (name,))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_datastore.py::test_log_handler_and_read -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/sqlite_log_handler.py common/datastore.py tests/test_datastore.py
git add common/sqlite_log_handler.py common/datastore.py tests/test_datastore.py
git commit -m "feat(datastore): SqliteLogHandler + logs read/clear"
```

---

### Task 7: Rewrite kv-blob accessors in `common.py`

Rewrite the blob accessors to call `datastore`, preserving signatures. Keys:
`control:general`, `control:current`, `control:status`, `errors`,
`control:tuning`, `settings:general`, `pellets:general`. Remove the
`cmdsts`/`config_set` usage from each. `read_control(flush=...)` flush branch is
Task 12; here do the non-flush read + both write kinds + `execute_control_writes`.

**Files:**
- Modify: `common/common.py:57-68` (delete `cmdsts` global + `import valkey`)
- Modify: `common/common.py` accessors listed below
- Create: `tests/test_common_blobs.py`

**Interfaces:**
- Consumes: `datastore.get_blob/set_blob/delete_blob/exists_blob`,
  `datastore.transaction`; `SqliteQueue("queue_control_write")`.
- Produces (signatures unchanged): `read_control`, `write_control(control, kind, origin)`,
  `execute_control_writes`, `read_current`, `write_current`, `read_status`,
  `write_status`, `read_errors`, `write_errors`, `write_generic_key`,
  `read_settings_valkey`, `write_settings_valkey`, `read_pellets_valkey`,
  `write_pellets_valkey`, `read_tr`/`write_tr` (control:tuning).

- [ ] **Step 1: Write failing tests (oracle-backed for control merge)**

```python
# tests/test_common_blobs.py
import json
import os

import pytest

from common import common as c
from common import datastore


@pytest.fixture
def ds(tmp_path):
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


def _oracle(name):
    p = os.path.join(os.path.dirname(__file__), "oracle", "fixtures", f"{name}.json")
    return json.load(open(p))


def test_control_overwrite_and_read(ds):
    c.write_control({"mode": "Stop", "n": {"a": 1}}, c.WriteKind.OVERWRITE, origin="t")
    assert c.read_control() == {"mode": "Stop", "n": {"a": 1}}


def test_control_merge_matches_oracle(ds):
    exp = _oracle("control_merge")
    c.write_control({"mode": "Stop", "nested": {"a": 1, "b": 2}}, c.WriteKind.OVERWRITE, origin="test")
    c.write_control({"nested": {"b": 9, "c": 3}}, c.WriteKind.MERGE, origin="webapp")
    assert c.read_control() == exp["before_execute"]      # MERGE deferred
    c.execute_control_writes()
    assert c.read_control() == exp["after_execute"]       # deep-merge, origin stripped


def test_errors_and_current_status_roundtrip(ds):
    c.write_errors(["e1"])
    assert c.read_errors() == ["e1"]
    c.write_status({"mode": "Hold"})
    assert c.read_status() == {"mode": "Hold"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_common_blobs.py -x`
Expected: FAIL (accessors still reference `cmdsts`; `AttributeError`/`NameError`).

- [ ] **Step 3: Implement the rewrites**

Delete `import valkey` and the `cmdsts = valkey.StrictValkey(...)` line
(`common/common.py:24, 68`). Add near the top: `from common import datastore`
and `from common.sqlite_queue import SqliteQueue, SqliteMembershipList`.
Rewrite each accessor body:

```python
def read_control(flush=False):
    if flush:
        return _flush_control()          # Task 12
    raw = datastore.get_blob("control:general")
    return json.loads(raw) if raw is not None else default_control()


def write_control(control, kind, origin="unknown"):
    if kind is WriteKind.OVERWRITE:
        datastore.set_blob("control:general", json.dumps(control))
    elif kind is WriteKind.MERGE:
        control["origin"] = origin
        SqliteQueue("queue_control_write").push(control)
    else:
        raise TypeError(f"write_control: kind must be WriteKind, got {kind!r}")


def execute_control_writes():
    q = SqliteQueue("queue_control_write")
    while q.length() > 0:
        control = read_control()
        command = q.pop()
        if command is None:
            break
        command.pop("origin", None)
        control = deep_update(control, command)
        write_control(control, WriteKind.OVERWRITE, origin="writer")
    return "OK"


def read_errors(flush=False):
    if flush:
        write_errors([])
        return []
    raw = datastore.get_blob("errors")
    return json.loads(raw) if raw is not None else []


def write_errors(errors):
    datastore.set_blob("errors", json.dumps(errors))


def write_current(in_data):
    current = {
        "P": in_data["probe_history"]["primary"],
        "F": in_data["probe_history"]["food"],
        "AUX": in_data["probe_history"]["aux"],
        "PSP": in_data["primary_setpoint"],
        "NT": in_data["notify_targets"],
        "TS": int(time.time() * 1000),
    }
    datastore.set_blob("control:current", json.dumps(current))


def write_status(status):
    datastore.set_blob("control:status", json.dumps(status))


def write_generic_key(key, value):
    datastore.set_blob(key, json.dumps(value))


def write_settings_valkey(settings):
    datastore.set_blob("settings:general", json.dumps(settings))


def write_pellets_valkey(pelletdb):
    datastore.set_blob("pellets:general", json.dumps(pelletdb))
```

For `read_current(zero_out=...)`, `read_status(init=...)`,
`read_settings_valkey(init=...)`, `read_pellets_valkey(init=...)`: keep the
**exact existing default-building / zero-out logic**, replacing only the storage
calls — `cmdsts.exists('K')` → `datastore.exists_blob('K')`,
`cmdsts.get('K')` → `datastore.get_blob('K')`,
`cmdsts.set('K', json.dumps(x))` → `datastore.set_blob('K', json.dumps(x))`.
`control:tuning` read/write (`read_tr`/`write_tr`, `common.py:1855-1875`) become
`datastore.get_blob('control:tuning')` / `set_blob`.

**Config source-of-truth split (critical).** Today `read_settings`
(`common.py:1069`) and `read_pellet_db` (`:1466`) read the JSON *files*, and many
blueprints call them directly — but at runtime they must now read SQLite. So:

- Rename the current file-reading bodies to `read_settings_file()` and
  `read_pellet_db_file()` (unchanged logic; used ONLY by the first-boot import in
  Task 13 and the import scripts in Task 14).
- Repoint `read_settings()` and `read_pellet_db()` to read SQLite — make them
  thin wrappers over the `_valkey` readers:

```python
def read_settings(filename="settings.json", init=False, retry_count=0):
    return read_settings_valkey()

def read_pellet_db(filename="pelletdb.json"):
    return read_pellets_valkey()
```

  (Keep the parameters for signature compatibility; they are ignored now that
  SQLite is authoritative.) `write_settings`/`write_pellet_db` likewise delegate
  to `write_settings_valkey`/`write_pellets_valkey` (SQLite only — no file
  write). Runtime callers are unchanged; the file path survives only through the
  `*_file` readers and the export/import scripts.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_common_blobs.py -v`
Expected: all PASS (including `test_control_merge_matches_oracle`).

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/common.py tests/test_common_blobs.py
git add common/common.py tests/test_common_blobs.py
git commit -m "feat(common): kv-blob accessors on SQLite; drop cmdsts global"
```

---

### Task 8: Queue-backed accessors (system/display/autotune)

**Files:**
- Modify: `common/common.py` (autotune accessors `:1883-1898`, plus any
  `ValkeyQueue` construction sites moved to `SqliteQueue`)
- Modify: `common/app.py:7,28` (`ValkeyQueue` → `SqliteQueue`)
- Modify: `tests/test_common_blobs.py` (add autotune cases)

**Interfaces:**
- Consumes: `SqliteQueue`.
- Produces: `write_autotune(data)` (push to `queue_autotune`),
  `read_autotune()` (list all), `clear_autotune()` (flush) — same names/behavior
  as today's bare-SQL functions; the three system/display queues via
  `SqliteQueue("queue_systemq"|"queue_systemo"|"queue_displayq")`.

- [ ] **Step 1: Write failing test**

```python
# append to tests/test_common_blobs.py
def test_autotune_uses_queue(ds):
    c.clear_autotune()
    c.write_autotune({"tr": 1})
    c.write_autotune({"tr": 2})
    assert c.read_autotune() == [{"tr": 1}, {"tr": 2}]
    c.clear_autotune()
    assert c.read_autotune() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_common_blobs.py::test_autotune_uses_queue -x`
Expected: FAIL (autotune still uses `cmdsts`).

- [ ] **Step 3: Implement**

Replace the autotune bodies (`common.py:1883-1898`) — preserving the existing
public function names — with:

```python
def write_autotune(data):
    SqliteQueue("queue_autotune").push(data)


def read_autotune():
    return SqliteQueue("queue_autotune").list()


def clear_autotune():
    SqliteQueue("queue_autotune").flush()
```

In `common/app.py` and any `common.py` site, replace
`from common.valkey_queue import ValkeyQueue` with
`from common.sqlite_queue import SqliteQueue`, and map the Valkey key names to
tables: `ValkeyQueue('control:systemo')` → `SqliteQueue('queue_systemo')`,
`'control:systemq'` → `'queue_systemq'`, `'control:displayq'` → `'queue_displayq'`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_common_blobs.py::test_autotune_uses_queue -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/common.py common/app.py tests/test_common_blobs.py
git add common/common.py common/app.py tests/test_common_blobs.py
git commit -m "feat(common): autotune + system/display queues on SqliteQueue"
```

---

### Task 9: warnings + users:connected accessors

**Files:**
- Modify: `common/common.py` (`read_warnings`/`write_warning`;
  `read_connected_users`/`write_connected_user`/`remove_connected_user`)
- Modify: `tests/test_common_blobs.py`

**Interfaces:**
- Consumes: `SqliteQueue("list_warnings", raw=True)`,
  `SqliteMembershipList("list_users_connected")`.
- Produces (unchanged names): `read_warnings()` (list-then-clear, raw strings),
  `write_warning(w)`, `read_connected_users(flush=False)`,
  `write_connected_user(id)`, `remove_connected_user(id)`.

- [ ] **Step 1: Write failing tests (warnings oracle-backed)**

```python
# append to tests/test_common_blobs.py
def test_warnings_read_and_clear_matches_oracle(ds):
    exp = _oracle("warnings")
    c.write_warning("first")
    c.write_warning("second")
    assert c.read_warnings() == exp["read1"]
    assert c.read_warnings() == exp["read2_after_clear"]


def test_connected_users_add_remove(ds):
    assert c.read_connected_users() == []
    c.write_connected_user("sidA")
    c.write_connected_user("sidB")
    assert sorted(c.read_connected_users()) == ["sidA", "sidB"]
    c.remove_connected_user("sidA")
    assert c.read_connected_users() == ["sidB"]
    c.read_connected_users(flush=True)
    assert c.read_connected_users() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_common_blobs.py -k "warnings or connected" -x`
Expected: FAIL (still `cmdsts`).

- [ ] **Step 3: Implement**

```python
def read_warnings():
    q = SqliteQueue("list_warnings", raw=True)
    warnings = q.list()
    q.flush()
    return warnings


def write_warning(warning):
    SqliteQueue("list_warnings", raw=True).push(warning)


def read_connected_users(flush=False):
    m = SqliteMembershipList("list_users_connected")
    if flush:
        m.flush()
    return m.list()


def write_connected_user(client_id):
    SqliteMembershipList("list_users_connected").add(client_id)


def remove_connected_user(client_id):
    SqliteMembershipList("list_users_connected").remove(client_id)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_common_blobs.py -k "warnings or connected" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/common.py tests/test_common_blobs.py
git add common/common.py tests/test_common_blobs.py
git commit -m "feat(common): warnings + connected-users on SQLite"
```

---

### Task 10: History accessors (H2 schema)

**Files:**
- Modify: `common/common.py` (`write_history`, `read_history`; keep `unpack_history` as-is)
- Create: `tests/test_common_history.py`

**Interfaces:**
- Consumes: `datastore.transaction`, `datastore.connection`, `datastore.execute_write`.
- Produces (unchanged): `write_history(in_data, maxsizelines=28800, ext_data=False)`,
  `read_history(num_items=0, flushhistory=False) -> list[dict]` where each dict
  is `{'T','P','F','PSP','NT','AUX'[,'EXD']}` — the same shape callers/`unpack_history` expect.

- [ ] **Step 1: Write failing tests (cap oracle-backed + round-trip)**

```python
# tests/test_common_history.py
import json
import os

import pytest

from common import common as c
from common import datastore


@pytest.fixture
def ds(tmp_path):
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


def _oracle(name):
    return json.load(open(os.path.join(os.path.dirname(__file__), "oracle", "fixtures", f"{name}.json")))


SAMPLE = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"P1": 145}, "aux": {}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0},
}


def test_history_cap_matches_oracle(ds):
    exp = _oracle("history_cap")
    for _ in range(5):
        c.write_history(SAMPLE, maxsizelines=3)
    items = c.read_history()
    assert len(items) == exp["len"] == 3        # capped
    # each reconstructed row carries the expected dict keys
    assert set(items[0]) == {"T", "P", "F", "PSP", "NT", "AUX"}
    assert items[0]["P"] == {"Grill": 225}
    assert items[0]["PSP"] == 225


def test_history_ext_data_roundtrip(ds):
    d = dict(SAMPLE, ext_data={"k": 1})
    c.write_history(d, ext_data=True)
    row = c.read_history()[0]
    assert row["EXD"] == {"k": 1}
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_common_history.py -x`
Expected: FAIL (still `cmdsts`/list-based).

- [ ] **Step 3: Implement**

```python
def write_history(in_data, maxsizelines=28800, ext_data=False):
    ts = int(time.time() * 1000)
    exd = json.dumps(in_data["ext_data"]) if ext_data else None
    with datastore.transaction() as conn:
        conn.execute(
            "INSERT INTO history(ts,psp,primary_temps,food_temps,aux_temps,"
            "notify_targets,ext_data) VALUES(?,?,?,?,?,?,?)",
            (ts, in_data["primary_setpoint"],
             json.dumps(in_data["probe_history"]["primary"]),
             json.dumps(in_data["probe_history"]["food"]),
             json.dumps(in_data["probe_history"]["aux"]),
             json.dumps(in_data["notify_targets"]), exd))
        count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        if count > maxsizelines:
            conn.execute(
                "DELETE FROM history WHERE id IN "
                "(SELECT id FROM history ORDER BY id LIMIT ?)", (count - maxsizelines,))


def _history_row_to_dict(row):
    ts, psp, p, f, aux, nt, exd = row
    d = {"T": ts, "P": json.loads(p), "F": json.loads(f),
         "PSP": psp, "NT": json.loads(nt), "AUX": json.loads(aux)}
    if exd is not None:
        d["EXD"] = json.loads(exd)
    return d


def read_history(num_items=0, flushhistory=False):
    if flushhistory:
        datastore.execute_write("DELETE FROM history")
        read_current(zero_out=True)
        write_metrics(flush=True)
        return []
    sql = ("SELECT ts,psp,primary_temps,food_temps,aux_temps,notify_targets,"
           "ext_data FROM history ORDER BY id")
    rows = datastore.connection().execute(sql).fetchall()
    if num_items > 0:
        rows = rows[-num_items:]
    return [_history_row_to_dict(r) for r in rows]
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_common_history.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/common.py tests/test_common_history.py
git add common/common.py tests/test_common_history.py
git commit -m "feat(common): history on H2 schema (columns + JSON probe dicts)"
```

---

### Task 11: Metrics accessors (blob + replace-last)

**Files:**
- Modify: `common/common.py` (`read_metrics`, `write_metrics`)
- Create: `tests/test_common_metrics.py`

**Interfaces:**
- Consumes: `datastore.transaction`, `datastore.connection`, `datastore.execute_write`.
- Produces (unchanged): `read_metrics(all=False)`,
  `write_metrics(metrics=None, flush=False, new_metric=False)`. `read_metrics()`
  returns the last record; `all=True` returns the full list.

- [ ] **Step 1: Write failing tests (replace-last oracle-backed)**

```python
# tests/test_common_metrics.py
import json
import os

import pytest

from common import common as c
from common import datastore


@pytest.fixture
def ds(tmp_path):
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    yield datastore
    datastore._reset_for_tests(None)


def _oracle(name):
    return json.load(open(os.path.join(os.path.dirname(__file__), "oracle", "fixtures", f"{name}.json")))


def test_replace_last_matches_oracle(ds):
    exp = _oracle("metrics_replace_last")
    m = c.default_metrics(); m["mode"] = "Startup"
    c.write_metrics(m, new_metric=True)
    m2 = c.default_metrics(); m2["mode"] = "Hold"
    c.write_metrics(m2, new_metric=False)
    assert c.read_metrics()["mode"] == exp["last"]["mode"] == "Hold"
    assert len(c.read_metrics(all=True)) == exp["all_len"] == 1


def test_new_metric_without_existing_does_not_crash(ds):
    c.write_metrics(new_metric=True)          # regression: no metrics yet
    assert "starttime" in c.read_metrics()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_common_metrics.py -x`
Expected: FAIL (still `cmdsts`).

- [ ] **Step 3: Implement**

```python
def read_metrics(all=False):
    conn = datastore.connection()
    if all:
        rows = conn.execute("SELECT data FROM metrics ORDER BY id").fetchall()
        return [json.loads(r[0]) for r in rows]
    row = conn.execute("SELECT data FROM metrics ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else default_metrics()


def write_metrics(metrics=None, flush=False, new_metric=False):
    if metrics is None:
        metrics = default_metrics()
    if flush:
        datastore.execute_write("DELETE FROM metrics")
        return
    if new_metric:
        metrics["starttime"] = time.time() * 1000
        metrics["id"] = generate_uuid()
        datastore.execute_write("INSERT INTO metrics(data) VALUES(?)", (json.dumps(metrics),))
        return
    with datastore.transaction() as conn:
        row = conn.execute("SELECT id FROM metrics ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO metrics(data) VALUES(?)", (json.dumps(metrics),))
        else:
            conn.execute("UPDATE metrics SET data=? WHERE id=?", (json.dumps(metrics), row[0]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_common_metrics.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/common.py tests/test_common_metrics.py
git add common/common.py tests/test_common_metrics.py
git commit -m "feat(common): metrics blob with replace-last on SQLite"
```

---

### Task 12: Boot flush + wire the log handler

**Files:**
- Modify: `common/common.py` (`_flush_control` helper used by `read_control(flush=True)`; logging setup `:106-110`)
- Modify: `tests/test_common_blobs.py`

**Interfaces:**
- Produces: `_flush_control()` — deletes the control queues + control blob keys
  (NOT history/current), then seeds `default_control()`. Logging setup uses
  `SqliteLogHandler` instead of `ValkeyHandler`.

- [ ] **Step 1: Write failing test**

```python
# append to tests/test_common_blobs.py
def test_flush_control_clears_only_control_not_history(ds):
    # seed history + a control blob + a queued write
    c.write_history({"probe_history": {"primary": {"G": 1}, "food": {}, "aux": {}},
                    "primary_setpoint": 1, "notify_targets": {}})
    c.write_control({"mode": "Hold"}, c.WriteKind.OVERWRITE, origin="t")
    c.write_control({"x": 1}, c.WriteKind.MERGE, origin="t")
    control = c.read_control(flush=True)
    assert control == c.default_control()                    # reseeded default
    from common.sqlite_queue import SqliteQueue
    assert SqliteQueue("queue_control_write").length() == 0  # queue cleared
    assert len(c.read_history()) == 1                        # history untouched
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_common_blobs.py::test_flush_control_clears_only_control_not_history -x`
Expected: FAIL (`_flush_control` not defined).

- [ ] **Step 3: Implement**

```python
def _flush_control():
    for table in ("queue_control_write", "queue_systemq", "queue_systemo"):
        datastore.execute_write(f"DELETE FROM {table}")
    for key in ("control:general", "control:command"):
        datastore.delete_blob(key)
    control = default_control()
    write_control(control, WriteKind.OVERWRITE, origin="common")
    return control
```

In `create_logger` (`common/common.py:106-110`), replace the `ValkeyHandler`
block with:

```python
from common.sqlite_log_handler import SqliteLogHandler
sqlite_handler = SqliteLogHandler(name)
sqlite_handler.setFormatter(formatter)
sqlite_handler.addFilter(ratelimit)
logger.addHandler(sqlite_handler)
```

Also rewrite `read_events_valkey(flush=False)` (`common.py:1680`), which read
the `logs:events` list, onto the logs table:

```python
def read_events_valkey(flush=False):
    if flush:
        datastore.clear_log("events")
        return []
    return datastore.read_log("events")
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_common_blobs.py::test_flush_control_clears_only_control_not_history -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/common.py tests/test_common_blobs.py
git add common/common.py tests/test_common_blobs.py
git commit -m "feat(common): boot flush + SqliteLogHandler wiring"
```

---

### Task 13: First-boot JSON→SQLite migration

**Files:**
- Modify: `common/datastore.py` (`_first_boot_import`)
- Create: `tests/test_startup_migration.py`

**Interfaces:**
- Consumes: `common.common.read_settings`, `read_pellet_db` (the **file**
  readers), `default_settings`, `default_pellets`.
- Produces: `_first_boot_import()` — if `settings:general` absent in `kv` and a
  settings file exists, import it (else seed defaults); same for
  `pellets:general`. All in one transaction. `import_config_file(kind, path)`
  and `export_config_file(kind, path)` shared with the scripts (Task 14).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_startup_migration.py
import json

import pytest

from common import datastore


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("PIFIRE_DB_PATH", str(tmp_path / "t.db"))
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    yield tmp_path
    datastore._reset_for_tests(None)


def test_first_boot_imports_settings(fresh, monkeypatch):
    from common import common as c
    monkeypatch.setattr(c, "read_settings_file", lambda *a, **k: {"globals": {"units": "F"}})
    monkeypatch.setattr(c, "read_pellet_db_file", lambda *a, **k: {"current": {"hopper_level": 100}})
    datastore.init()
    assert json.loads(datastore.get_blob("settings:general"))["globals"]["units"] == "F"
    assert json.loads(datastore.get_blob("pellets:general"))["current"]["hopper_level"] == 100


def test_first_boot_idempotent(fresh, monkeypatch):
    from common import common as c
    monkeypatch.setattr(c, "read_settings_file", lambda *a, **k: {"v": 1})
    monkeypatch.setattr(c, "read_pellet_db_file", lambda *a, **k: {"v": 1})
    datastore.init()
    datastore.set_blob("settings:general", json.dumps({"v": 999}))  # simulate runtime edit
    datastore.init()                                                # must NOT re-import
    assert json.loads(datastore.get_blob("settings:general"))["v"] == 999
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_startup_migration.py -x`
Expected: FAIL (`_first_boot_import` is a no-op; settings blob is `None`).

- [ ] **Step 3: Implement**

```python
# replace _first_boot_import in common/datastore.py
def _first_boot_import():
    import json

    from common import common as c  # deferred to avoid import cycle
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM kv WHERE key='settings:general'").fetchone() is None:
            settings = c.read_settings_file()      # the FILE reader, not SQLite
            conn.execute("INSERT INTO kv(key,value) VALUES('settings:general',?)",
                         (json.dumps(settings),))
        if conn.execute("SELECT 1 FROM kv WHERE key='pellets:general'").fetchone() is None:
            pelletdb = c.read_pellet_db_file()     # the FILE reader, not SQLite
            conn.execute("INSERT INTO kv(key,value) VALUES('pellets:general',?)",
                         (json.dumps(pelletdb),))
```

Note the `read_settings_file`/`read_pellet_db_file` names introduced in Task 7 —
`_first_boot_import` must use the FILE readers (the SQLite readers are empty on
first boot, which would defeat the import).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_startup_migration.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
uvx ruff format common/datastore.py tests/test_startup_migration.py
git add common/datastore.py tests/test_startup_migration.py
git commit -m "feat(datastore): idempotent first-boot JSON import"
```

---

### Task 14: Export/import config scripts

**Files:**
- Create: `scripts/export-settings-json`, `scripts/import-settings-json`,
  `scripts/export-pelletdb-json`, `scripts/import-pelletdb-json`
- Modify: `common/datastore.py` (`export_config`/`import_config` helpers)
- Modify: `tests/test_startup_migration.py`

**Interfaces:**
- Produces: `datastore.export_config(key, path)` (write kv blob → JSON file),
  `datastore.import_config(key, path)` (read JSON file → kv blob, validated).

- [ ] **Step 1: Write failing test**

```python
# append to tests/test_startup_migration.py
def test_export_import_roundtrip(fresh):
    datastore.init_schema_only() if hasattr(datastore, "init_schema_only") else datastore.connection().executescript(datastore.SCHEMA + datastore._queue_ddl())
    datastore.set_blob("settings:general", json.dumps({"globals": {"units": "C"}}))
    p = str(fresh / "out.json")
    datastore.export_config("settings:general", p)
    assert json.load(open(p))["globals"]["units"] == "C"
    # edit the file, re-import
    d = json.load(open(p)); d["globals"]["units"] = "F"; json.dump(d, open(p, "w"))
    datastore.import_config("settings:general", p)
    assert json.loads(datastore.get_blob("settings:general"))["globals"]["units"] == "F"


def test_import_rejects_malformed(fresh):
    datastore.connection().executescript(datastore.SCHEMA + datastore._queue_ddl())
    p = str(fresh / "bad.json")
    open(p, "w").write("{not json")
    with pytest.raises(ValueError):
        datastore.import_config("settings:general", p)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_startup_migration.py -k "export or malformed" -x`
Expected: FAIL (`export_config` missing).

- [ ] **Step 3: Implement helpers + scripts**

```python
# append to common/datastore.py
def export_config(key, path):
    raw = get_blob(key)
    if raw is None:
        raise KeyError(f"{key} not present in datastore")
    with open(path, "w") as fh:
        fh.write(json.dumps(json.loads(raw), indent=2, sort_keys=True))


def import_config(key, path):
    with open(path) as fh:
        text = fh.read()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    set_blob(key, json.dumps(obj))
```

```python
# scripts/export-settings-json  (chmod +x; same pattern for the other three)
#!/usr/bin/env python3
import sys
from common import datastore
datastore.export_config("settings:general", sys.argv[1] if len(sys.argv) > 1 else "settings.json")
print("exported settings:general")
```

The four scripts differ only in `key` (`settings:general` / `pellets:general`),
direction (`export_config` / `import_config`), and default filename
(`settings.json` / `pelletdb.json`).

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_startup_migration.py -k "export or malformed" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/export-settings-json scripts/import-settings-json scripts/export-pelletdb-json scripts/import-pelletdb-json
uvx ruff format common/datastore.py tests/test_startup_migration.py
git add scripts common/datastore.py tests/test_startup_migration.py
git commit -m "feat(scripts): settings/pelletdb export+import; shared with migration"
```

---

### Task 15: Rename Store seam, fix InMemory cap, repoint existing suites

**Files:**
- Modify: `controller/runtime/store.py` (`ValkeyStore` → `SqliteStore`;
  `_ValkeyQueueAdapter` → `SqliteQueue` tables; `InMemoryStore.write_history` cap)
- Modify: `control.py:30` (import `SqliteStore`)
- Rename: `tests/test_valkey_store_parity.py` → `tests/test_sqlite_store_parity.py`;
  `tests/e2e/test_work_cycle_e2e.py` (repoint to `SqliteStore`, drop the
  valkey-ping skip, add a temp-DB fixture)

**Interfaces:**
- Produces: `SqliteStore` (same `Store` ABC methods), used by `control.py` and
  the e2e/parity suites; hermetic (temp DB, no server).

- [ ] **Step 1: Update InMemoryStore cap test**

```python
# in tests/test_in_memory_store.py add:
def test_in_memory_history_cap():
    from controller.runtime.store import InMemoryStore
    s = InMemoryStore()
    sample = {"probe_history": {"primary": {"G": 1}, "food": {}, "aux": {}},
              "primary_setpoint": 1, "notify_targets": {}}
    for _ in range(5):
        s.write_history(sample, maxsizelines=3)
    assert len(s.read_history()) == 3       # was unbounded; must now cap
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_in_memory_store.py::test_in_memory_history_cap -x`
Expected: FAIL with `assert 5 == 3` (cap not yet implemented in the fake).

- [ ] **Step 3: Implement rename + cap fix + repoint**

- In `controller/runtime/store.py`: rename class `ValkeyStore` → `SqliteStore`
  (keep the thin pass-through to `common.common`); change the three queue
  adapters to `SqliteQueue("queue_systemq"|"queue_systemo"|"queue_displayq")`;
  add the cap to `InMemoryStore.write_history`:

```python
    def write_history(self, in_data, maxsizelines=28800, ext_data=False):
        self._history.append(...)  # existing append
        if len(self._history) > maxsizelines:
            self._history = self._history[-maxsizelines:]
```

- In `control.py:30`, change `from controller.runtime.store import ValkeyStore`
  → `SqliteStore` and the construction at `:99`.
- Rename the parity test file; replace its `_valkey_available()`/`skipif` gate
  and the live-server snapshot/restore with a temp-DB fixture:

```python
@pytest.fixture
def store(tmp_path):
    from common import datastore
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    from controller.runtime.store import SqliteStore
    yield SqliteStore()
    datastore._reset_for_tests(None)
```

  Keep every existing assertion body (they now run against `SqliteStore`).
- In `tests/e2e/test_work_cycle_e2e.py`: drop the ping/skip; build the context
  with `SqliteStore()` on a temp DB (same fixture); keep the scenario asserts.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_in_memory_store.py tests/test_sqlite_store_parity.py tests/e2e/ -v`
Expected: all PASS (now hermetic — no valkey-server needed).

- [ ] **Step 5: Commit**

```bash
uvx ruff format controller/runtime/store.py control.py tests/test_in_memory_store.py tests/test_sqlite_store_parity.py tests/e2e/test_work_cycle_e2e.py
git add -A
git commit -m "refactor(store): SqliteStore; hermetic parity/e2e; InMemory cap fix"
```

---

### Task 16: Concurrency / multi-process stress (T3)

**Files:**
- Create: `tests/test_datastore_concurrency.py`

**Interfaces:**
- Consumes: `datastore`, `common.common` accessors, `SqliteQueue`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_datastore_concurrency.py
import multiprocessing as mp
import os

import pytest

from common import datastore


def _producer(db, table, n):
    os.environ["PIFIRE_DB_PATH"] = db
    datastore._reset_for_tests(db)
    from common.sqlite_queue import SqliteQueue
    q = SqliteQueue(table)
    for i in range(n):
        q.push({"i": i})


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "t.db")
    os.environ["PIFIRE_DB_PATH"] = p
    datastore._reset_for_tests(p)
    datastore.init()
    yield p
    datastore._reset_for_tests(None)


def test_concurrent_producers_no_loss(db):
    from common.sqlite_queue import SqliteQueue
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_producer, args=(db, "queue_systemq", 200)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert SqliteQueue("queue_systemq").length() == 800   # no lost/dup under contention


def test_cross_process_visibility(db):
    datastore.set_blob("control:status", '{"mode":"Hold"}')
    ctx = mp.get_context("spawn")
    q = ctx.Queue()

    def reader(dbpath, out):
        os.environ["PIFIRE_DB_PATH"] = dbpath
        datastore._reset_for_tests(dbpath)
        out.put(datastore.get_blob("control:status"))

    p = ctx.Process(target=reader, args=(db, q))
    p.start(); p.join()
    assert q.get() == '{"mode":"Hold"}'   # committed write visible in another process
```

- [ ] **Step 2: Run to verify it fails, then passes**

Run: `pytest tests/test_datastore_concurrency.py -v`
Expected: PASS (this validates the implementation; if it fails with lost rows or
`database is locked`, the `busy_timeout`/retry/transaction handling needs fixing
before proceeding).

- [ ] **Step 3: Commit**

```bash
uvx ruff format tests/test_datastore_concurrency.py
git add tests/test_datastore_concurrency.py
git commit -m "test(datastore): multi-process contention + cross-process visibility"
```

---

### Task 17: Crash / durability (T4)

**Files:**
- Create: `tests/test_datastore_crash.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_datastore_crash.py
import multiprocessing as mp
import os

import pytest

from common import datastore


def _write_then_kill(db):
    os.environ["PIFIRE_DB_PATH"] = db
    datastore._reset_for_tests(db)
    datastore.init()
    datastore.set_blob("settings:general", '{"committed": true}')
    os._exit(9)   # hard kill AFTER commit, before clean close


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "t.db")
    os.environ["PIFIRE_DB_PATH"] = p
    yield p
    datastore._reset_for_tests(None)


def test_committed_survives_hard_kill(db):
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_write_then_kill, args=(db,))
    p.start(); p.join()
    assert p.exitcode == 9
    datastore._reset_for_tests(db)
    datastore.init()
    assert datastore.get_blob("settings:general") == '{"committed": true}'  # WAL recovered
    assert datastore.connection().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
```

- [ ] **Step 2: Run to verify it passes**

Run: `pytest tests/test_datastore_crash.py -v`
Expected: PASS (committed value survives; integrity ok).

- [ ] **Step 3: Commit**

```bash
uvx ruff format tests/test_datastore_crash.py
git add tests/test_datastore_crash.py
git commit -m "test(datastore): committed writes survive hard kill (WAL)"
```

---

### Task 18: Webapp integration (T6)

**Files:**
- Create: `tests/test_webapp_sqlite.py`

**Interfaces:**
- Consumes: the Flask app factory in `common/app.py`, `common.common` accessors.

- [ ] **Step 1: Write the test (adapt selectors to the real app factory)**

```python
# tests/test_webapp_sqlite.py
import os

import pytest

from common import datastore


@pytest.fixture
def client(tmp_path, monkeypatch):
    os.environ["PIFIRE_DB_PATH"] = str(tmp_path / "t.db")
    datastore._reset_for_tests(str(tmp_path / "t.db"))
    datastore.init()
    from common import common as c
    c.write_settings_valkey(c.default_settings())
    c.write_pellets_valkey(c.default_pellets())
    c.write_status(c.read_status(init=True))
    from common.app import create_app          # adapt to the real factory name
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as cl:
        yield cl
    datastore._reset_for_tests(None)


def test_dashboard_route_reads_sqlite(client):
    resp = client.get("/")                       # adapt to a real read route
    assert resp.status_code == 200


def test_no_valkey_import_at_runtime():
    import sys
    # exercised app must not have pulled in the valkey client
    assert "valkey" not in sys.modules
```

- [ ] **Step 2: Adjust to the real app factory/routes, run**

Run: `pytest tests/test_webapp_sqlite.py -v`
Expected: PASS. (If `create_app` differs, match the actual factory in
`common/app.py`; if `/` needs auth/config, pick a simple GET route that reads
`control:current`/`settings`.)

- [ ] **Step 3: Commit**

```bash
uvx ruff format tests/test_webapp_sqlite.py
git add tests/test_webapp_sqlite.py
git commit -m "test(webapp): Flask routes read/write SQLite, no valkey import"
```

---

### Task 19: Remove Valkey entirely

**Files:**
- Delete: `common/valkey_queue.py`, `common/valkey_handler.py`
- Modify: `pyproject.toml`, `uv.lock` (drop `valkey`)
- Modify: `auto-install/**`, `auto-install/supervisor/**` (drop `valkey-server` install/run)
- Grep-and-fix: any remaining `valkey`/`cmdsts`/`ValkeyQueue`/`ValkeyHandler` references

- [ ] **Step 1: Write the guard test**

```python
# append to tests/test_datastore.py
import subprocess


def test_no_valkey_references_in_source():
    hits = subprocess.run(
        ["grep", "-rIl", "-e", "import valkey", "-e", "cmdsts",
         "-e", "ValkeyQueue", "-e", "ValkeyHandler",
         "--include=*.py", "common", "controller", "blueprints", "control.py"],
        capture_output=True, text=True).stdout.strip()
    assert hits == "", f"stale Valkey references in: {hits}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_datastore.py::test_no_valkey_references_in_source -x`
Expected: FAIL, listing files still referencing Valkey.

- [ ] **Step 3: Remove Valkey**

```bash
git rm common/valkey_queue.py common/valkey_handler.py
```

Fix each file the grep lists (replace imports/usages with the SQLite
equivalents from Tasks 4–9). Remove `valkey` from `pyproject.toml`
`dependencies` and run `uv lock` to update `uv.lock`. In `auto-install/`, delete
the `valkey-server` package install and any `[program:valkey]`/service unit.

- [ ] **Step 4: Run the full suite + the guard**

Run: `pytest -q && pytest tests/test_datastore.py::test_no_valkey_references_in_source -v`
Expected: full suite PASS; guard PASS (no references).

- [ ] **Step 5: Commit**

```bash
uvx ruff format $(git diff --name-only --cached | grep '\.py$')
git add -A
git commit -m "chore: remove Valkey (client, server install, handlers) — SQLite hard cut"
```

---

## Self-Review

**Spec coverage:** Connection/PRAGMAs/atomicity → Task 2; kv+CHECK → Tasks 2–3,7;
table-per-queue + json_valid + `SqliteMembershipList` + autotune-on-interface →
Tasks 4,5,8,9; H2 history + CHECKs → Tasks 2,10; metrics blob (+ fast-follow
note) → Task 11; logs → Task 6; boot flush → Task 12; first-boot import +
scripts → Tasks 13,14; Store seam rename + InMemory cap → Task 15; T0 → Task 1;
T1 → Tasks 7–12; T2 → Task 15; T3 → Task 16; T4 → Task 17; T5 → Tasks 13,14; T6
→ Task 18; error handling (retry/fail-loud) → Task 2; Valkey removal → Task 19.

**Deferred to fast-follow (per spec):** metrics columnization (Task 11 keeps the
JSON blob).

**Follow-ups the implementer must resolve inline:** the real Flask factory
name/route in Task 18; confirming settings/pelletdb are the only hand-edit
config files (Task 14) — add a script pair if another exists.
