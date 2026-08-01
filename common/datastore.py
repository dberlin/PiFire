"""SQLite datastore: thread-local connection, schema, transactions, first-boot
import. The only module that opens the database; common.py talks to it."""

import json
import os
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PIFIRE_DB_PATH", os.path.join(_HERE, "..", "pifire.db"))
_ORIGINAL_DB_PATH = DB_PATH

_local = threading.local()

#: Guards ensure_settings_upgraded() -- the migration cascade runs at most
#: once per process. Module-level rather than per-connection: updater.py and
#: wizard.py run as their own standalone processes and open their own
#: connection, so this has to survive independently of _local.conn.
_settings_upgraded = False

# history table DDL (schema v4). `{name}` is templated so the pre-v4
# migration below can rebuild it under a temporary name (history_new) with an
# identical schema before swapping it in, preserving existing rows.
#
# psp (primary setpoint) uses NUMERIC affinity rather than REAL: SQLite's
# NUMERIC affinity stores an integer literal as INTEGER and a real literal as
# REAL, so ints round-trip as ints instead of being coerced to floats.
# primary_setpoint is always written as an int (e.g. 225); REAL affinity
# would silently coerce it to a float (225.0) on round-trip.
_HISTORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {name} (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    psp            NUMERIC,
    primary_temps  TEXT NOT NULL CHECK(json_valid(primary_temps)),
    food_temps     TEXT NOT NULL CHECK(json_valid(food_temps)),
    aux_temps      TEXT NOT NULL CHECK(json_valid(aux_temps)),
    notify_targets TEXT NOT NULL CHECK(json_valid(notify_targets)),
    ext_data       TEXT CHECK(ext_data IS NULL OR json_valid(ext_data))
);
"""

_HISTORY_INDEX_DDL = "CREATE INDEX IF NOT EXISTS ix_history_ts ON history(ts);\n"

_HISTORY_DDL = _HISTORY_TABLE_DDL.format(name="history") + _HISTORY_INDEX_DDL

# Columnar metrics schema (schema v3). Columns mirror common.metrics_items in
# order; `seq` is a surrogate PK so it doesn't clash with the metrics 'id'
# field (a uuid string). Defined separately so the v1->v3 migration below can
# reuse the exact same DDL when recreating the table.
#
# Numeric columns that conventionally hold integer values use NUMERIC affinity
# rather than REAL: SQLite's NUMERIC affinity stores an integer literal as
# INTEGER and a real literal as REAL, so ints round-trip as ints instead of
# being coerced to floats (REAL affinity would turn e.g. pellet_level_start=87
# into 87.0). smart_start_profile/p_mode/smokeplus are always-integer flags
# and stay INTEGER; the *_c display columns and other strings stay TEXT.
_METRICS_DDL = """
CREATE TABLE IF NOT EXISTS metrics (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    id                  TEXT,
    starttime           NUMERIC,
    starttime_c         TEXT,
    endtime             NUMERIC,
    endtime_c           TEXT,
    timeinmode          NUMERIC,
    mode                TEXT,
    augerontime         NUMERIC,
    augerontime_c       TEXT,
    estusage_m          TEXT,
    estusage_i          TEXT,
    fanontime           NUMERIC,
    fanontime_c         TEXT,
    smokeplus           INTEGER,
    primary_setpoint    NUMERIC,
    smart_start_profile INTEGER,
    startup_temp        NUMERIC,
    p_mode              INTEGER,
    auger_cycle_time    NUMERIC,
    pellet_level_start  NUMERIC,
    pellet_level_end    NUMERIC,
    pellet_brand_type   TEXT
);
"""

#: The file sink is capped by RotatingFileHandler at 1 MiB x 3 backups, roughly
#: 4 MiB per logger. Nothing capped the table, so it grew onto the SD card
#: forever. ~20k rows is the same order of magnitude as those files.
LOG_RETENTION_ROWS = 20_000

#: Pruning on every insert would roughly double the write cost of logging, so
#: the trigger only does real work every Nth row.
PRUNE_INTERVAL = 1_000


def _logs_retention_ddl():
    """A trigger, not a counter in the logging handler.

    Retention has to hold for every writer, and a Python-side counter does not:
    PiFire runs control.py, gunicorn and board-config.py as separate processes,
    each with its own SqliteLogHandler instances and its own count, and
    common.common.reset_loggers() detaches handlers -- which resets any such
    counter to zero. A process that restarts often would prune late or never,
    and nothing would report it. Enforcing this in the schema means anything
    that inserts into `logs` is covered, including future writers that never go
    near the handler.

    `NEW.id % PRUNE_INTERVAL` gates the delete so the amortised cost matches a
    counter's: the trigger fires on every insert but only does work every Nth.
    `id` is a global AUTOINCREMENT, so the gate trips every N rows across all
    loggers, and the delete then trims whichever logger wrote that row -- which
    self-balances toward the noisiest one.

    Recreated rather than CREATE TRIGGER IF NOT EXISTS, so changing the
    constants above takes effect on the next connection instead of leaving a
    stale definition on every existing database. _ensure_logs_retention only
    applies it when the definition actually differs -- see there.
    """
    return f"""CREATE TRIGGER logs_prune AFTER INSERT ON logs
WHEN NEW.id % {PRUNE_INTERVAL} = 0
BEGIN
    DELETE FROM logs
     WHERE name = NEW.name
       AND id <= (SELECT id FROM logs WHERE name = NEW.name
                   ORDER BY id DESC LIMIT 1 OFFSET {LOG_RETENTION_ROWS});
END"""


def _ensure_logs_retention(conn):
    """Install the retention trigger, but only when it is missing or stale.

    Unconditionally running DROP + CREATE would write to sqlite_master on EVERY
    connection, and a schema write takes a write lock. connection() runs per
    thread and PiFire opens many, so that turns a read-only fast path into
    lock contention for every other writer. Comparing first keeps the steady
    state a single indexed read of sqlite_master.
    """
    desired = _logs_retention_ddl()
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='logs_prune'").fetchone()
    if row is not None and " ".join(row[0].split()) == " ".join(desired.split()):
        return
    conn.executescript(f"DROP TRIGGER IF EXISTS logs_prune;\n{desired};")


SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL CHECK(json_valid(value))
);
"""
    + _HISTORY_DDL
    + _METRICS_DDL
    + """
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_logs_name_id ON logs(name, id);
"""
)

# one table per queue; JSON queues carry a json_valid CHECK, raw lists do not
_JSON_QUEUE_TABLES = ["queue_control_write", "queue_systemq", "queue_systemo", "queue_displayq", "queue_autotune"]
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
        ddl.append(f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL);")
    return "\n".join(ddl)


def _migrate_history_to_numeric_psp(conn):
    """Rebuild `history` in place with NUMERIC-affinity psp, preserving rows.
    Unlike metrics (transient, per-cook), history is durable, so this cannot
    drop-and-recreate: it builds a shadow table with the corrected schema,
    copies every row across (which normalizes any REAL-coerced values like
    225.0 back to 225 on re-insert through the NUMERIC column), then swaps it
    in for the original.

    Callers must run this inside a `transaction(conn)` block so the whole
    rebuild commits or rolls back as one unit. Each DDL statement is issued
    via `execute()` (not `executescript()`, which implicitly commits any
    pending transaction before running) so it stays inside that transaction."""
    conn.execute(_HISTORY_TABLE_DDL.format(name="history_new"))
    conn.execute(
        "INSERT INTO history_new (id, ts, psp, primary_temps, food_temps, aux_temps, notify_targets, ext_data) "
        "SELECT id, ts, psp, primary_temps, food_temps, aux_temps, notify_targets, ext_data FROM history"
    )
    conn.execute("DROP TABLE history")
    conn.execute("ALTER TABLE history_new RENAME TO history")
    conn.execute(_HISTORY_INDEX_DDL)


def _ensure_schema(conn):
    conn.executescript(SCHEMA + _queue_ddl())
    #  Applied separately, and conditionally: unlike CREATE TABLE IF NOT EXISTS
    #  above, refreshing a trigger is a schema WRITE on every connection.
    _ensure_logs_retention(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if 0 < version < 3:
        # Pre-v3 DB: either the old (id, data) JSON blob metrics table (v1,
        # untouched by CREATE TABLE IF NOT EXISTS above), or a v2 columnar
        # metrics table whose numeric columns used REAL affinity (which
        # coerces integer values like pellet_level_start=87 to 87.0 on
        # round-trip). Recreate with the current (NUMERIC-affinity) DDL.
        # Metrics are per-cook/transient, so dropping in-progress metrics on
        # this one-time upgrade is acceptable.
        conn.executescript("DROP TABLE IF EXISTS metrics;" + _METRICS_DDL)
    if 0 < version < 4:
        # Pre-v4 DB: history.psp used REAL affinity, coercing integer
        # primary_setpoint values (e.g. 225) to floats (225.0) on round-trip.
        # history is durable, so rebuild-and-swap instead of drop+recreate.
        # Wrapped in a single explicit transaction (SQLite DDL is
        # transactional) so a crash mid-rebuild rolls back cleanly, leaving
        # user_version unbumped -- the whole migration retries from scratch
        # on the next connect instead of leaving a half-built history_new
        # table or a dropped-but-not-renamed history table around.
        #
        # Pass `conn` explicitly (transaction(conn), not transaction()):
        # we're still inside connection()'s call to _ensure_schema() here,
        # before _local.conn is assigned, so a bare transaction() would call
        # connection() again and recurse into _ensure_schema() on a second,
        # separate sqlite3 connection.
        with transaction(conn):
            _migrate_history_to_numeric_psp(conn)
    if version < 4:
        conn.execute("PRAGMA user_version=4")


def connection():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = None  # autocommit; we manage txns explicitly
        try:
            _ensure_schema(conn)
        except Exception:
            # Don't leak the freshly-opened connection (and its WAL lock) if
            # schema setup/migration fails -- _local.conn is never assigned in
            # that case, so nothing else would close it for us.
            conn.close()
            raise
        _local.conn = conn
    return conn


_RETRY_DEADLINE_S = 10.0  # wall-clock cap: a fire-control loop can't afford ~4min


def _retry(fn, attempts=50, deadline_s=_RETRY_DEADLINE_S):
    """Retry `fn` on SQLITE_BUSY/LOCKED, bounded by both an attempt count and a
    wall-clock deadline. Each individual attempt can itself block up to
    busy_timeout (5s, set in connection()) inside SQLite before raising
    OperationalError to us, so the attempt-count bound alone is not enough to
    keep worst-case latency bounded (50 attempts * 5s = ~4min); the deadline
    check below stops us from starting another attempt once we're out of
    budget, regardless of how many attempts remain."""
    start = time.monotonic()
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                if time.monotonic() - start >= deadline_s:
                    raise sqlite3.OperationalError(
                        f"SQLITE_BUSY: retry deadline ({deadline_s}s) exceeded after {i + 1} attempt(s)"
                    ) from e
                time.sleep(0.005 * (i + 1))
                continue
            raise
    raise sqlite3.OperationalError("SQLITE_BUSY: retries exhausted")


def execute_write(sql, params=()):
    return _retry(lambda: connection().execute(sql, params))


class transaction:
    """`with transaction() as conn:` — BEGIN IMMEDIATE / COMMIT / ROLLBACK,
    retrying only the BEGIN on BUSY.

    `transaction(conn)` reuses an already-open connection instead of calling
    `connection()`. Needed by `_ensure_schema()`, which runs during
    `connection()` itself (before `_local.conn` is assigned) -- calling the
    no-arg form there would recurse into `connection()` -> `_ensure_schema()`
    on a brand new sqlite3 connection instead of joining the one being set up."""

    def __init__(self, conn=None):
        self._conn = conn

    def __enter__(self):
        self.conn = self._conn if self._conn is not None else connection()
        _retry(lambda: self.conn.execute("BEGIN IMMEDIATE"))
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


def init():
    connection()
    _first_boot_import()
    ensure_settings_upgraded()


def _first_boot_import():
    import json

    from common import backups, settings_migration  # deferred to avoid import cycle

    # INSERT ... ON CONFLICT DO UPDATE (not a plain INSERT): read_settings_file
    # (via its init=True overlay) can itself detect a corrupted settings.json
    # and call restore_settings(), which persists the recovered settings to
    # SQLite immediately (write_settings_store). That nested write lands on
    # this same thread-local connection/transaction, so by the time we get
    # here the row may already exist -- upsert keeps this idempotent instead
    # of raising a PRIMARY KEY IntegrityError.
    upsert = "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM kv WHERE key='settings:general'").fetchone() is None:
            # init=True here is settings_migration.read_settings_file's OWN
            # parameter (a real one -- it applies the version-overlay /
            # upgrade_settings() path), so imported settings gain new default
            # fields and get upgraded in place instead of being stored as a
            # stale, un-migrated snapshot. Not to be confused with the dead
            # init flag the SQLite-side read_settings() used to carry; that one
            # never did anything and has been removed.
            settings = settings_migration.read_settings_file(init=True)  # the FILE reader, not SQLite
            conn.execute(upsert, ("settings:general", json.dumps(settings)))
        if conn.execute("SELECT 1 FROM kv WHERE key='pellets:general'").fetchone() is None:
            pelletdb = backups.read_pellet_db_file()  # the FILE reader, not SQLite
            conn.execute(upsert, ("pellets:general", json.dumps(pelletdb)))


def ensure_settings_upgraded():
    """Migrate the stored settings tree once per process, before it is served.

    Every entry point that reads settings gets a migrated tree, whether or not
    it called init() -- the upgrade script runs updater.py and wizard.py as
    standalone processes, and a migration only some callers reach is the
    defect this exists to close.
    """
    global _settings_upgraded
    if _settings_upgraded:
        return
    # Set before doing the work: upgrade_settings() itself reads settings (it
    # calls into read/write helpers for warnings, profiles, etc.), and that
    # re-entrant call must see the guard as already tripped, not recurse.
    _settings_upgraded = True
    _upgrade_settings_in_store()


def _upgrade_settings_in_store():
    """Bring the SQLite-stored settings tree up to the running code's version.

    The settings migration cascade reaches settings imported from a JSON file,
    which happens on first boot and on an explicit restore. A tree that has
    lived in SQLite ever since would otherwise never be migrated again -- so a
    shape change leaves an existing install holding keys the schema no longer
    models, and the write-time repair strips them on the next save.
    """
    import copy
    import json

    from common import settings_migration  # deferred to avoid import cycle
    from common.common import semantic_ver_is_lower, semantic_ver_to_list, write_log
    from common.defaults import default_settings
    from common.settings_schema import validate_settings_tree

    settings_default = default_settings()
    current = settings_default["versions"]

    # Read and write inside one BEGIN IMMEDIATE, like _first_boot_import's
    # check-then-write: a reader between the two would otherwise observe an
    # unmigrated tree stamped with nothing to say it is about to change.
    with transaction() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key='settings:general'").fetchone()
        if row is None:
            return
        settings = json.loads(row[0])
        stored = settings.get("versions") or {}
        if not stored.get("server"):
            return
        if not semantic_ver_is_lower(stored["server"], current["server"]) and stored.get("build", 0) >= current.get(
            "build", 0
        ):
            return

        prev_ver = semantic_ver_to_list(stored["server"])
        settings = settings_migration.upgrade_settings(prev_ver, settings, settings_default)
        settings["versions"] = current
        try:
            # Validated for the side effect of logging only -- write_settings()
            # is not used here (see module docstring): its repair is exactly
            # what strips a legacy key instead of letting the migration
            # convert it. A deepcopy so the repair's normalized output can
            # never replace what gets persisted.
            validate_settings_tree(copy.deepcopy(settings))
        except Exception as exc:
            write_log(f"Settings migration produced a tree the schema rejects: {exc}")
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("settings:general", json.dumps(settings)),
        )


def _reset_for_tests(path):
    """Test hook: repoint DB_PATH, drop the cached thread-local connection, and
    clear the once-per-process settings-upgrade guard so the next init()/read
    in a test runs it again against the fresh (test) database."""
    global DB_PATH, _settings_upgraded
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    DB_PATH = path if path is not None else _ORIGINAL_DB_PATH
    _settings_upgraded = False


def get_blob(key):
    row = connection().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def set_blob(key, value_str):
    execute_write(
        "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value_str)
    )


def delete_blob(key):
    execute_write("DELETE FROM kv WHERE key=?", (key,))


def exists_blob(key):
    return connection().execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone() is not None


def read_log(name, num=0):
    sql = "SELECT message FROM logs WHERE name=? ORDER BY id DESC"
    params = (name,)
    if num > 0:
        sql += " LIMIT ?"
        params = (name, num)
    return [r[0] for r in connection().execute(sql, params).fetchall()]


def clear_log(name):
    execute_write("DELETE FROM logs WHERE name=?", (name,))


def prune_log(name, keep):
    """Drop all but the newest `keep` rows for one logger.

    The cutoff is found by OFFSET into that logger's own rows, walking
    ix_logs_name_id directly. It cannot be `MAX(id) - keep` arithmetic: `id` is
    a single global AUTOINCREMENT sequence shared by every logger, so wherever
    two loggers interleave -- which on a running grill is always -- that
    subtraction lands on the wrong row and deletes another logger's history.

    `OFFSET keep` names the (keep + 1)-th newest row, so the comparison is
    inclusive: deleting that row and everything older leaves exactly `keep`.

    With fewer than `keep` rows the subquery yields NULL, `id <= NULL` is NULL,
    and nothing is deleted.
    """
    execute_write(
        "DELETE FROM logs WHERE name=? AND id <= (SELECT id FROM logs WHERE name=? ORDER BY id DESC LIMIT 1 OFFSET ?)",
        (name, name, keep),
    )


def export_config(key, path):
    """Write the kv blob at `key` to `path` as pretty-printed JSON."""
    raw = get_blob(key)
    if raw is None:
        raise KeyError(f"{key} not present in datastore")
    with open(path, "w") as fh:
        fh.write(json.dumps(json.loads(raw), indent=2, sort_keys=True))


def import_config(key, path):
    """Read a JSON file at `path`, validate it, and store it at the kv blob `key`."""
    with open(path) as fh:
        text = fh.read()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    set_blob(key, json.dumps(obj))
