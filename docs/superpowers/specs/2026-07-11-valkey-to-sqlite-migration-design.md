# Valkey → SQLite Datastore Migration — Design

**Date:** 2026-07-11
**Status:** Approved (design); ready for implementation planning
**Branch / worktree:** `sqlite-rewrite`

## Goal

Replace Valkey with an embedded SQLite datastore as PiFire's shared state,
queue, and history layer — removing the `valkey-server` daemon entirely, gaining
real durability for history/metrics, and keeping the change contained to the
datastore internals so the controller, webapp, and display are unaffected.

This is a **hard cut**: Valkey is removed, not kept selectable. There is no dual
backend at runtime.

### Why (from the investigation that preceded this design)

PiFire's workload is read-heavy (state polling) with a low-rate command bus.
Benchmarks on this workload showed embedded in-process reads (SQLite `SELECT`,
LMDB `GET`) are 4–5× faster than a localhost-TCP Valkey `GET`, and SQLite/WAL is
more than adequate for the queue at PiFire's real rate (~1/s). SQLite was chosen
over LMDB because LMDB would require hand-building queue/ring-buffer/transaction
machinery that SQL gives for free, and over the status quo because a hard cut
drops a daemon and gains durability with no meaningful performance cost.

## Key decisions

1. **Hard cut.** Remove Valkey (`valkey` Python package, `valkey-server` from
   installers/supervisor). No runtime fallback.
2. **Real durability where it is new.** SQLite is the durable source of truth
   for **history** and **metrics** (RAM-only in Valkey today, lost on reboot).
3. **Native rewrite, preserve accessor signatures (Approach B).** Rewrite the
   bodies of `common/common.py`'s accessors + the queue class + the log handler
   to a purpose-built SQLite schema, keeping every public function/method
   signature identical. Blueprints and the `Store` seam do not change.
4. **Config is SQLite-authoritative; files are explicit import/export only.**
   `settings` and `pelletdb` live in SQLite. There is **no** automatic file
   read/write, write-through, or auto-export at runtime. A one-time startup
   migration imports existing `settings.json`/`pelletdb.json` on first boot
   (empty DB) and never touches them again. Hand-editing is a deliberate act via
   scripts (below), whose import path is shared with the first-boot migration.
5. **Ephemeral runtime state is still rebuilt on boot.** `control:general`,
   `control:current`, `control:status`, and the queues are flushed and
   re-seeded on startup exactly as today — they are not migrated or persisted
   across reboots as authoritative state.

## Current-state anchors (from the datastore inventory)

- Single connection created once: `common/common.py:68`
  (`cmdsts = valkey.StrictValkey('localhost', 6379, ..., decode_responses=True)`);
  `common/valkey_queue.py:13` opens a **second** connection of its own.
- **13 Valkey verbs** used anywhere: `get, set, delete, rpush, lpush, lpop,
  rpop, lrange, llen, lindex, exists, lrem, config_set` (+ `ping` in test gates).
- **Two access paths:** the `Store` ABC seam (`controller/runtime/store.py`) is
  used **only** by the controller (`control.py`). The **entire webapp** and
  `common/app.py` call `common.common` free functions directly and build
  `ValkeyQueue` directly (`common/app.py:28`) — they bypass the seam.
- **Semantics that must be preserved** (the breakage risks):
  - OVERWRITE vs MERGE + deep-merge-on-drain — `common/common.py:900-937`.
  - History cap (`rpush` then `if llen > maxsizelines: lpop`, default 28800) —
    `common/common.py:1796-1801`.
  - Metrics replace-last (`rpop` then `rpush`) — `common/common.py:1060-1066`;
    read-last via `lindex(-1)` at `:1035`.
  - Boot flush (delete the 5 control keys, **not** history/current; seed
    `default_control`) — `common/common.py:879-891`.
  - `config_set('appendonly'/'save')` toggles — `:887-888`, `:1053-1054`.
  - `decode_responses=True`: reads already return `str`. Raw-string (not JSON)
    keys: `warnings` (`:1007`), `users:connected`, log lines, `logs:events`.
- **Existing oracle:** `tests/test_valkey_store_parity.py` and
  `tests/e2e/test_work_cycle_e2e.py` assert `ValkeyStore` matches
  `InMemoryStore` golden outcomes — but both **skip unless a `valkey-server` is
  already running** (a ping-and-skip gate; they do not spin one up).
  `InMemoryStore.write_history` currently **omits the cap** — a fake-vs-real
  divergence to fix.

## Architecture

### Module layout

| File | Change |
|---|---|
| `common/datastore.py` | **New.** Owns the per-process SQLite connection, schema DDL, PRAGMAs, the startup migration/import, a `SQLITE_BUSY` retry wrapper, and low-level helpers (blob get/set, queue ops, history/metrics/log ops). Everything the `cmdsts` global owned. |
| `common/common.py` | Accessor bodies rewritten to call `datastore`; **signatures unchanged**. The `cmdsts` global and `config_set` calls are deleted. |
| `common/valkey_queue.py` → `common/sqlite_queue.py` | `ValkeyQueue` → `SqliteQueue`, same `push/pop/length/list/flush` API, **one table per queue** (constructed with its table name). Also **`SqliteMembershipList`** for `users:connected` — `add`/`remove(value)`/`list`/`flush` — since it removes by value (`lrem`), which the FIFO queue API does not express. |
| `common/valkey_handler.py` → `common/sqlite_log_handler.py` | `ValkeyHandler` → `SqliteLogHandler`, backed by the `logs` table. |
| `controller/runtime/store.py` | `ValkeyStore` → `SqliteStore` (still a thin pass-through to `common.py`). `InMemoryStore` unchanged except fixing the history-cap divergence. |
| `pyproject.toml` / `uv.lock` | Drop `valkey`; add nothing (SQLite is stdlib). |
| `auto-install/` + `auto-install/supervisor/` | Stop installing/running `valkey-server`. |
| `scripts/export-settings-json`, `scripts/import-settings-json`, `scripts/export-pelletdb-json`, `scripts/import-pelletdb-json` | **New.** Explicit config hand-edit round-trip. Import shares the migration code path. |

Callers that reference the old names (`from common.valkey_queue import
ValkeyQueue` at `common/app.py:7`, `ValkeyStore` at `control.py:30`, test file
names) are updated.

### Connection & concurrency model

- **One SQLite file** (default `<pifire>/pifire.db`; path configurable), opened
  **once per process** as a module global — same lifecycle as today's `cmdsts`.
  Each supervised process (control, each webapp worker, display) opens its own
  connection. WAL sidecar files (`-wal`, `-shm`) live beside it.
- **PRAGMAs:** `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`,
  `foreign_keys=ON`. WAL gives lock-free concurrent readers + one writer; a
  committed write is visible to every other process's next read transaction
  immediately (the cross-process semantics Valkey provided).
- **`config_set` becomes a no-op** (removed; persistence is a PRAGMA/DDL concern
  now, not a runtime toggle).
- **Atomicity:** multi-statement accessors run inside an explicit
  `BEGIN IMMEDIATE … COMMIT`: `write_history` (append+cap) and `write_metrics`
  replace-last. **`execute_control_writes` keeps per-item transactions** (read
  control → pop one partial → merge → write back, per iteration) rather than one
  transaction over the whole drain — this preserves today's behavior where a
  MERGE arriving mid-drain is picked up on a later iteration. A
  **retry-on-`SQLITE_BUSY`** wrapper (bounded backoff) guards writes so lock
  contention never escapes to callers.

### Schema

- **`kv(key TEXT PRIMARY KEY, value TEXT CHECK(json_valid(value)))`** —
  last-value-wins JSON blobs: `control:general`, `control:current`,
  `control:status`, `settings:general`, `pellets:general`, `errors`,
  `control:tuning`. All values are `json.dumps`'d, so the `json_valid` CHECK is
  always satisfiable and turns a stray non-JSON write into an immediate
  `IntegrityError`. `get`→`SELECT`,
  `set`→`INSERT OR REPLACE`, `delete`→`DELETE`, `exists`→`SELECT 1`. Missing key
  → `None` (matches Valkey).
- **Queues — one table per queue.** Each list-backed queue key gets its own
  table `(id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL [CHECK…])`.
  `rpush`=INSERT; `lpop`=delete lowest id; `rpop`=delete highest id;
  `lrange`=ordered SELECT; `llen`=COUNT; `lindex(-1)`=highest id. A single file
  still means one write lock (SQLite locks per-database, not per-table, so this
  does **not** reduce write contention today) — the split is for clarity, easier
  debugging/inspection, and the option to index or evolve each queue
  independently later.

  | table | backs | value | CHECK | interface |
  |---|---|---|---|---|
  | `queue_control_write` | `control:write` | JSON | `json_valid(value)` | `SqliteQueue` |
  | `queue_systemq` | `control:systemq` | JSON | `json_valid(value)` | `SqliteQueue` |
  | `queue_systemo` | `control:systemo` | JSON | `json_valid(value)` | `SqliteQueue` |
  | `queue_displayq` | `control:displayq` | JSON | `json_valid(value)` | `SqliteQueue` |
  | `queue_autotune` | `control:autotune` | JSON | `json_valid(value)` | `SqliteQueue` (refactored from bare `common.py` list calls at `:1883-1898`) |
  | `list_warnings` | `warnings` | raw text | none | `SqliteQueue` (push / list / flush; read-and-clear) |
  | `list_users_connected` | `users:connected` | raw text | none | `SqliteMembershipList` (`add` / `remove(value)` / `list` / `flush`; the `lrem`-by-value case) |

  JSON queues carry the `json_valid` CHECK; the two raw-string lists
  (`warnings`, `users:connected`) deliberately omit it. `control:autotune` is
  moved onto the `SqliteQueue` interface (its `rpush`/`lrange`/`llen`/`delete`
  usage maps to `push`/`list`/`length`/`flush` — it never pops individual
  items), so no queue key is accessed with bare SQL.
- **`history`** — schematized "wide-hybrid" (H2): scalar fields promoted to
  typed columns, the variable/user-defined probe dicts kept as JSON. This is
  chosen over an all-JSON blob (queryable time axis) and over full normalization
  (H3's per-reading table has brutal write amplification on a Pi — ~720k reading
  rows for an 8 h/1 Hz/5-probe cook — for analytics that aren't a goal yet).
  ```sql
  CREATE TABLE history (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      ts             INTEGER NOT NULL,   -- was datastruct 'T' (epoch ms)
      psp            REAL,               -- 'PSP' primary setpoint
      primary_temps  TEXT NOT NULL CHECK(json_valid(primary_temps)),  -- 'P'
      food_temps     TEXT NOT NULL CHECK(json_valid(food_temps)),     -- 'F'
      aux_temps      TEXT NOT NULL CHECK(json_valid(aux_temps)),      -- 'AUX'
      notify_targets TEXT NOT NULL CHECK(json_valid(notify_targets)), -- 'NT'
      ext_data       TEXT CHECK(ext_data IS NULL OR json_valid(ext_data)) -- 'EXD', nullable
  );
  CREATE INDEX ix_history_ts ON history(ts);
  ```
  The `json_valid` CHECKs cost only a JSON parse per column; benchmarked at
  ~2–12% of an already-cheap write under WAL/`NORMAL` (fsync-dominated), i.e.
  single-digit microseconds a couple times a second at PiFire's ~1–2 Hz history
  rate — free insurance against a bad write silently corrupting the chart data.
  `write_history` maps the `datastruct` fields onto these columns (probe dicts
  `json.dumps`'d); the cap is `DELETE FROM history WHERE id IN (SELECT id … ORDER
  BY id LIMIT n)` when `COUNT > maxsizelines`. `read_history`/`unpack_history`
  reassemble the `{'T','P','F','PSP','NT','AUX','EXD'}` dict shape from the
  columns so callers are unchanged. The column↔dict round-trip is a specific
  breakage path covered by T0/T1.
- **`metrics(id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT
  CHECK(json_valid(data)))`** — append-new vs replace-last (UPDATE highest-id
  row); read-last = highest id; read-all = ordered. (Columnized in the
  fast-follow; JSON blob + CHECK for now.)
- **`logs(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ts INTEGER, message
  TEXT)`** — the log sink + `logs:events`. Redis `lpush` + read-newest-first
  becomes INSERT + `ORDER BY id DESC` (no head-insert emulation). **No
  `json_valid` CHECK** — `message` holds raw log strings, not JSON.
- **Versioning:** `PRAGMA user_version` gates schema creation and migration.

### Startup migration & config scripts

- On boot, `datastore.init()`: create schema if `user_version == 0`; bump
  `user_version`. Then the **idempotent first-boot import**: if `settings:general`
  is absent from `kv` and `settings.json` exists, load and insert it; same for
  `pelletdb.json`. Wrapped in one transaction (all-or-nothing). A second boot,
  seeing the rows already present, is a no-op. Missing files → seed defaults.
  Corrupt/partial JSON → fail loudly, roll back, do not half-migrate.
- **`read_settings`/`read_settings_valkey` unify onto SQLite**;
  `write_settings` writes SQLite only. Same for `read_pellet_db`/
  `write_pellet_db`. No file I/O at runtime.
- **Config scripts** (`scripts/{export,import}-{settings,pelletdb}-json`) are the
  only runtime file path: export writes a JSON file from the DB; import loads a
  JSON file into the DB (same code as first-boot import). This is the supported
  hand-edit workflow: export → edit → import. (Confirm during implementation
  that `settings` and `pelletdb` are the only hand-editable config files; add a
  script pair for any other.)

## Test strategy (breakage-path coverage)

Seven tiers. T1–T6 are hermetic (no external daemon) and therefore run **by
default in CI** — an improvement over today's parity/e2e suites, which silently
skip unless a `valkey-server` happens to be running. (On-real-hardware
validation is left to the maintainer running the branch on a Pi, not a test
tier.)

- **T0 — Recorded-golden capture from Valkey (before deletion).** Run a
  comprehensive operation script against the *current* `valkey-server` once and
  record every accessor's output as committed JSON golden fixtures. Assert the
  SQLite implementation reproduces them byte-for-byte. A differential oracle
  against real Valkey without carrying two live backends.
- **T1 — Accessor semantic unit tests (hermetic).** Per-accessor, targeting each
  catalogued risk: history cap boundary (`=`, `+1`, sustained, subsets, flush);
  `execute_control_writes` (FIFO, nested deep-merge, origin inject/strip,
  OVERWRITE vs deferred MERGE, interleaving); `write_metrics` (new/stamp,
  replace-last, read last/all, the no-existing-metrics regression);
  raw-string-vs-JSON keys (`warnings` read-and-clear, `users:connected`
  rpush/lrem, logs newest-first); boot flush (exactly the 5 control keys, not
  history/current; seed default); blob round-trips, missing→`None`, `str` (not
  bytes) returns, `read_status(init=True)`, `read_current(zero_out)`.
- **T2 — Behavioral oracle reuse.** Repoint the golden/characterization
  scenarios and the parity + e2e suites onto `SqliteStore`; assert it reproduces
  `InMemoryStore` golden outcomes across all mode scenarios. Fix
  `InMemoryStore`'s history-cap divergence so the fake matches real semantics.
- **T3 — Concurrency / multi-process stress.** Spawn processes mirroring
  PiFire's topology (control writer + webapp readers/writers + display):
  concurrent MERGE during a drain (no lost partials, FIFO, per-item-txn holds);
  history appends racing the cap (length ≤ max, no deadlock); metrics
  replace-last under a concurrent reader (no torn/absent tail); cross-process
  visibility within a bounded delay; sustained load with no `SQLITE_BUSY`/
  "database is locked" escaping to callers. Reuses the `queue_bench.py` harness
  shape.
- **T4 — Crash / durability.** `kill -9` mid-write → reopen,
  `PRAGMA integrity_check`, WAL recovery, last committed value intact, no
  half-written row. Simulated reboot (close+reopen) → settings/pellets/history
  survive; ephemeral keys rebuilt by boot flush. Document the `synchronous=
  NORMAL` guarantee (no corruption; may lose only the last txn on power loss).
- **T5 — Startup migration + export/import scripts.** First-boot import
  correctness; idempotent second boot (no clobber); missing files → defaults;
  corrupt JSON → fail loudly, rolled back, DB not half-migrated; export produces
  valid JSON matching the DB; round-trip export→import is identity;
  export→edit→import reflects the edit; malformed import rejected safely.
- **T6 — Webapp integration (the bypass path).** Boot the Flask app against a
  SQLite file **with no `valkey-server` present** and exercise representative
  routes: dashboard (current/status/history), settings save/load, pellet ops,
  events/logs, and socket.io connected-users add/remove (the
  `SqliteMembershipList` add / remove-by-value path). This covers what the
  Store-seam tests miss.

## Error handling

- `SQLITE_BUSY`/locked → two layers absorb realistic contention: `busy_timeout`
  (SQLite itself blocks the caller up to 5s waiting for the lock) plus a bounded
  retry-with-backoff wrapper on writes. PiFire is a single low-rate writer, so
  the write lock is essentially uncontended and neither layer normally fires. If
  the bounded retries are still exhausted — a pathological case that is not
  expected to occur — the write **raises a clear error and fails loudly** rather
  than corrupting state or silently dropping data. Infinite retry is
  deliberately rejected: it could stall the control loop indefinitely on a lock.
- Corruption / open failure → a clear operator-facing error (replacing today's
  "Unable to reach Valkey database…" messages at `common/common.py:959` etc.).
- Migration/import failure → fail loudly with a rolled-back transaction; never a
  half-migrated DB.

## Out of scope / follow-ups

- **Schematize `metrics` into typed columns — planned fast-follow immediately
  after this migration.** `metrics_items` (`common/common.py:694`) is ~22 flat
  scalars (`id, starttime, endtime, timeinmode, mode, augerontime, fanontime,
  smokeplus, primary_setpoint, …`), so it maps cleanly to columns with
  replace-last → `UPDATE` of the current row. It is intentionally deferred: the
  replace-last blob semantics are simplest to port faithfully first, and adding
  columns carries `user_version` migration churn. Ship metrics as a
  `metrics(id, data TEXT)` JSON blob now; columnize once parity is proven.
  (History was *not* deferred — it ships schematized as H2 above.)
- Any change to controller logic, mode handlers, or display rendering.
- Renaming persisted settings keys or the on-disk settings/pellet JSON shapes
  (the DB stores the same JSON structures).
</content>
