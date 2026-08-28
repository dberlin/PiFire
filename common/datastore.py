"""SQLite datastore: thread-local connection, schema, transactions, first-boot
import. The only module that opens the database; common.py talks to it."""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH: str = os.environ.get("PIFIRE_DB_PATH", os.path.join(_HERE, "..", "pifire.db"))
_ORIGINAL_DB_PATH: str = DB_PATH
DB_SCHEMA_VERSION = 10

_local = threading.local()

_database_creation_allowed = True


class DatabaseNotFoundError(sqlite3.OperationalError):
    """Raised when existing-only initialization cannot find the datastore."""

# history table DDL (schema v8). `{name}` is templated so the pre-v4
# migration below can rebuild it under a temporary name (history_new) with an
# identical schema before swapping it in, preserving existing rows.
#
# psp (primary setpoint) uses NUMERIC affinity rather than REAL: SQLite's
# NUMERIC affinity stores an integer literal as INTEGER and a real literal as
# REAL, so ints round-trip as ints instead of being coerced to floats.
# primary_setpoint is always written as an int (e.g. 225); REAL affinity
# would silently coerce it to a float (225.0) on round-trip.
#
# The three duty columns are nullable with no default: a row written before
# schema v8, or by a control loop that reported no duty, reads back as None
# and renders as a gap in the chart rather than as a fabricated zero. Zero is
# a meaningful duty, so it cannot double as "unknown".
#
# cycle_ratio is what the controller COMMANDED; realized_cycle_ratio is what
# actually reached the auger, measured from delivered on-time by the framed
# pulse machinery. They separate exactly where a clamp acts -- the duty floor
# lifting a request too small to pulse, u_max capping one too large, a lid-open
# pause pinning the auger off -- which is the whole reason to record both.
# Only the framed-pulse Hold path measures the second, so it is NULL elsewhere.
#
# Both are REAL (a 0.0-1.0 fraction, genuinely fractional). fan_duty takes
# NUMERIC for the same reason psp does: it is a whole percent, and REAL
# affinity would round-trip 65 as 65.0.
_HISTORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {name} (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                    INTEGER NOT NULL,
    psp                   NUMERIC,
    primary_temps         TEXT NOT NULL CHECK(json_valid(primary_temps)),
    food_temps            TEXT NOT NULL CHECK(json_valid(food_temps)),
    aux_temps             TEXT NOT NULL CHECK(json_valid(aux_temps)),
    notify_targets        TEXT NOT NULL CHECK(json_valid(notify_targets)),
    ext_data              TEXT CHECK(ext_data IS NULL OR json_valid(ext_data)),
    cycle_ratio           REAL,
    realized_cycle_ratio  REAL,
    fan_duty              NUMERIC
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

# Controller control-quality evidence (schema v5). Event-specific data stays in
# a Pydantic-validated JSON payload; these envelope columns support bounded,
# indexed retention and replay reads without exposing arbitrary JSON to callers.
_CONTROL_TRACE_DDL = """
CREATE TABLE IF NOT EXISTS control_trace (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms          INTEGER NOT NULL,
    session_id     TEXT NOT NULL,
    cook_id        TEXT,
    controller     TEXT NOT NULL,
    event_kind     TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload        TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX IF NOT EXISTS ix_control_trace_session_id ON control_trace(session_id, id);
CREATE INDEX IF NOT EXISTS ix_control_trace_cook_id ON control_trace(cook_id, id);
CREATE INDEX IF NOT EXISTS ix_control_trace_ts_ms ON control_trace(ts_ms);
"""

# Durable compact model evidence (schema v6) is deliberately separate from
# raw control traces: raw retention can never erase replay/activation evidence.
_MODEL_EVIDENCE_DDL = """
CREATE TABLE IF NOT EXISTS model_evidence (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id       TEXT NOT NULL UNIQUE,
    session_id        TEXT NOT NULL,
    cook_id           TEXT,
    timestamp_ms      INTEGER NOT NULL,
    kind              TEXT NOT NULL,
    role_generation   INTEGER NOT NULL,
    model_digest      TEXT,
    provenance_digest TEXT,
    schema_version    INTEGER NOT NULL,
    payload           TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX IF NOT EXISTS ix_model_evidence_session ON model_evidence(session_id, id);
CREATE INDEX IF NOT EXISTS ix_model_evidence_cook ON model_evidence(cook_id, id);
CREATE INDEX IF NOT EXISTS ix_model_evidence_kind ON model_evidence(kind, id);
CREATE INDEX IF NOT EXISTS ix_model_evidence_generation ON model_evidence(role_generation, id);
CREATE INDEX IF NOT EXISTS ix_model_evidence_digest ON model_evidence(model_digest, id);

CREATE TABLE IF NOT EXISTS model_activation_state (
    singleton                        INTEGER PRIMARY KEY CHECK(singleton = 1),
    active_snapshot_json             TEXT NOT NULL CHECK(json_valid(active_snapshot_json)),
    rollback_snapshot_json           TEXT NOT NULL CHECK(json_valid(rollback_snapshot_json)),
    evidence_decision_id             TEXT NOT NULL,
    controller_configuration_digest  TEXT NOT NULL,
    role_generation                  INTEGER NOT NULL,
    phase                           TEXT NOT NULL DEFAULT 'active'
                                    CHECK(phase IN ('prepared', 'active', 'aborted')),
    transaction_id                  TEXT,
    incumbent_pair_json             TEXT CHECK(incumbent_pair_json IS NULL OR json_valid(incumbent_pair_json)),
    candidate_pair_json             TEXT CHECK(candidate_pair_json IS NULL OR json_valid(candidate_pair_json)),
    rollback_pair_json              TEXT CHECK(rollback_pair_json IS NULL OR json_valid(rollback_pair_json)),
    origin                          TEXT,
    policy                          TEXT,
    candidate_generation            INTEGER,
    candidate_digest                TEXT,
    reason                          TEXT
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
    + _CONTROL_TRACE_DDL
    + _MODEL_EVIDENCE_DDL
    + """
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_logs_name_id ON logs(name, id);
"""
    # `kind` carries no CHECK constraint: CREATE TABLE IF NOT EXISTS leaves an
    # existing table's constraints alone, so a CHECK would admit a newly added
    # kind on fresh databases while silently rejecting it on every install that
    # already has the table. common.common.ErrorKind is the one enforcement
    # point, applied in Python where every database sees the same rule.
    #
    # No timestamp column: read_errors() groups by kind and orders by `id`
    # within each group, which needs no clock -- and a clock here would have to
    # answer whose it is, across three independently supervised processes.
    + """
CREATE TABLE IF NOT EXISTS errors (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_errors_kind_id ON errors(kind, id);
"""
)


# Durable bounded cumulative-learning corpus (schema v9). Kept out of SCHEMA so
# every table, index, singleton row, and the user_version bump are one
# transactional migration. executescript() would commit before executing and
# would make a crash leave v9 objects behind while the database still reports
# v8.
_LEARNING_TRAJECTORY_V9_DDL = (
    """
CREATE TABLE IF NOT EXISTS learning_trajectory_corpus (
    singleton                  INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version             INTEGER NOT NULL,
    corpus_revision            INTEGER NOT NULL,
    segment_count              INTEGER NOT NULL,
    pre_roll_count             INTEGER NOT NULL,
    scored_count               INTEGER NOT NULL,
    evicted_segment_count      INTEGER NOT NULL,
    evicted_pre_roll_count     INTEGER NOT NULL,
    evicted_scored_count       INTEGER NOT NULL,
    quarantined_segment_count  INTEGER NOT NULL
)
""",
    """
CREATE TABLE IF NOT EXISTS learning_trajectory_segment (
    segment_id                 TEXT PRIMARY KEY,
    state                      TEXT NOT NULL CHECK(state IN ('open','finalized','quarantined')),
    fit_partition_digest       TEXT NOT NULL,
    header_json                TEXT NOT NULL CHECK(json_valid(header_json)),
    start_monotonic_ms         INTEGER NOT NULL,
    end_monotonic_ms           INTEGER NOT NULL,
    start_wall_ms              INTEGER NOT NULL,
    end_wall_ms                INTEGER NOT NULL,
    start_sequence             INTEGER NOT NULL,
    end_sequence               INTEGER NOT NULL,
    hold_entry_json            TEXT CHECK(hold_entry_json IS NULL OR json_valid(hold_entry_json)),
    hold_entry_revision        INTEGER,
    pre_roll_count             INTEGER NOT NULL,
    scored_count               INTEGER NOT NULL,
    next_ordinal               INTEGER NOT NULL,
    rolling_digest             TEXT NOT NULL,
    final_digest               TEXT,
    content_digest             TEXT NOT NULL,
    begin_content_digest        TEXT NOT NULL,
    roll_successor_segment_id   TEXT,
    created_corpus_revision    INTEGER NOT NULL,
    updated_corpus_revision    INTEGER NOT NULL,
    finalized_corpus_revision  INTEGER,
    pre_roll_end_reason        TEXT,
    terminal_break_reason      TEXT,
    source_trace_digest        TEXT,
    source_schema_version      INTEGER NOT NULL,
    source_row_digest          TEXT
)
""",
    """
CREATE TABLE IF NOT EXISTS learning_trajectory_frame (
    segment_id                 TEXT NOT NULL,
    ordinal                    INTEGER NOT NULL,
    kind                       TEXT NOT NULL CHECK(kind IN ('pre-roll','scored')),
    payload_schema_version     INTEGER NOT NULL CHECK(payload_schema_version = 2),
    interval_identity          TEXT NOT NULL,
    canonical_json             TEXT NOT NULL CHECK(json_valid(canonical_json)),
    frame_digest               TEXT NOT NULL,
    created_corpus_revision    INTEGER NOT NULL,
    PRIMARY KEY(segment_id, ordinal),
    FOREIGN KEY(segment_id) REFERENCES learning_trajectory_segment(segment_id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE IF NOT EXISTS learning_trajectory_operation_receipt (
    operation_key             TEXT PRIMARY KEY,
    operation_kind            TEXT NOT NULL CHECK(operation_kind IN ('append','break-and-begin')),
    source_segment_id         TEXT NOT NULL,
    request_digest            TEXT NOT NULL,
    result_segment_id         TEXT NOT NULL,
    inserted_pre_roll_count   INTEGER NOT NULL,
    inserted_scored_count     INTEGER NOT NULL,
    created_corpus_revision   INTEGER NOT NULL,
    FOREIGN KEY(source_segment_id) REFERENCES learning_trajectory_segment(segment_id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE IF NOT EXISTS learning_fit_run (
    request_id                    TEXT PRIMARY KEY,
    status                        TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','interrupted','stale')),
    fit_partition_digest          TEXT NOT NULL,
    corpus_revision               INTEGER NOT NULL,
    corpus_digest                 TEXT NOT NULL,
    manifest_json                 TEXT CHECK(manifest_json IS NULL OR json_valid(manifest_json)),
    parent_incumbent_digest       TEXT NOT NULL,
    parent_incumbent_generation   INTEGER NOT NULL,
    candidate_generation          INTEGER NOT NULL,
    trigger_origin                TEXT NOT NULL,
    candidate_digest              TEXT,
    result_error                  TEXT,
    created_ms                    INTEGER NOT NULL,
    started_ms                    INTEGER,
    completed_ms                  INTEGER
)
""",
    (
        "CREATE INDEX IF NOT EXISTS ix_learning_segment_retention "
        "ON learning_trajectory_segment(state, end_wall_ms, segment_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_learning_segment_partition "
        "ON learning_trajectory_segment(fit_partition_digest, start_wall_ms, segment_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_learning_frame_revision "
        "ON learning_trajectory_frame(segment_id, created_corpus_revision, ordinal)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_learning_operation_source "
        "ON learning_trajectory_operation_receipt(source_segment_id, created_corpus_revision, operation_key)"
    ),
    ("CREATE INDEX IF NOT EXISTS ix_learning_fit_terminal ON learning_fit_run(status, completed_ms, request_id)"),
    """
INSERT OR IGNORE INTO learning_trajectory_corpus(
    singleton, schema_version, corpus_revision, segment_count,
    pre_roll_count, scored_count, evicted_segment_count,
    evicted_pre_roll_count, evicted_scored_count,
    quarantined_segment_count
) VALUES(1, 1, 0, 0, 0, 0, 0, 0, 0, 0)
""",
)

# Singleton durable challenger authority (schema v10).  The canonical JSON
# owns the complete immutable state; the repeated identity columns make
# revision CAS and corruption detection possible without trusting that JSON.
_MODEL_CHALLENGER_V10_DDL = (
    """
CREATE TABLE model_challenger_state (
    singleton      INTEGER PRIMARY KEY CHECK(singleton = 1),
    challenger_id  TEXT NOT NULL,
    revision       INTEGER NOT NULL CHECK(revision >= 0),
    phase          TEXT NOT NULL
                   CHECK(phase IN ('built', 'evaluating', 'qualified',
                                   'activating', 'retired')),
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    state_json     TEXT NOT NULL CHECK(json_valid(state_json)),
    updated_ms     INTEGER NOT NULL CHECK(updated_ms >= 0)
)
""",
    ("CREATE UNIQUE INDEX ix_model_challenger_identity ON model_challenger_state(challenger_id)"),
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
    if version < 5:
        # Schema v5 introduces an additive control_trace table. `SCHEMA` has
        # already created it with IF NOT EXISTS, so an existing database keeps
        # every current row and starts with an empty trace table.
        conn.execute("PRAGMA user_version=5")
    if version < 6:
        # Schema v6 adds an independent durable evidence ledger and singleton
        # activation state. Both are additive and begin empty on upgrade.
        conn.execute("PRAGMA user_version=6")
    if version < 7:
        # Schema v7 turns activation authority into a crash-convergent pair
        # transaction. Existing active-only rows retain their old projection.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(model_activation_state)").fetchall()}
        additions = (
            ("phase", "TEXT NOT NULL DEFAULT 'active'"),
            ("transaction_id", "TEXT"),
            ("incumbent_pair_json", "TEXT"),
            ("candidate_pair_json", "TEXT"),
            ("rollback_pair_json", "TEXT"),
            ("origin", "TEXT"),
            ("policy", "TEXT"),
            ("candidate_generation", "INTEGER"),
            ("candidate_digest", "TEXT"),
            ("reason", "TEXT"),
        )
        with transaction(conn):
            for name, declaration in additions:
                if name not in columns:
                    conn.execute(f"ALTER TABLE model_activation_state ADD COLUMN {name} {declaration}")
            conn.execute("PRAGMA user_version=7")
    if version < 8:
        # Schema v8 records the duty that drove each history sample: the auger
        # cycle ratio the controller commanded, the ratio actually delivered to
        # the auger, and fan duty.
        #
        # Added in place rather than by the rebuild-and-swap `history` uses
        # above: these are nullable additive columns, so ALTER TABLE ADD COLUMN
        # is a metadata-only change that leaves durable rows untouched, and
        # every pre-v8 row keeps NULL duty (a gap in the chart, not a zero).
        # Guarded by table_info so a fresh database -- where SCHEMA already
        # created the table with all three -- walks this branch to reach the
        # version bump without trying to add a column twice.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(history)").fetchall()}
        additions = (
            ("cycle_ratio", "REAL"),
            ("realized_cycle_ratio", "REAL"),
            ("fan_duty", "NUMERIC"),
        )
        with transaction(conn):
            for name, declaration in additions:
                if name not in columns:
                    conn.execute(f"ALTER TABLE history ADD COLUMN {name} {declaration}")
            conn.execute("PRAGMA user_version=8")
    if version < 9:
        # Schema v9 is an additive cumulative-learning corpus. All DDL and the
        # version bump stay inside the same explicit transaction so a failed
        # migration leaves both the v8 data and user_version untouched.
        with transaction(conn):
            for statement in _LEARNING_TRAJECTORY_V9_DDL:
                conn.execute(statement)
            conn.execute("PRAGMA user_version=9")
    if version < 10:
        # Schema v10 adds the independent challenger singleton. Keep its DDL
        # and the version bump in one transaction so v9 authority is unchanged
        # if either operation fails.
        with transaction(conn):
            for statement in _MODEL_CHALLENGER_V10_DDL:
                conn.execute(statement)
            conn.execute("PRAGMA user_version=10")


def connection():
    conn = getattr(_local, "conn", None)
    if conn is None:
        if _database_creation_allowed:
            conn = sqlite3.connect(DB_PATH, timeout=30)
        else:
            uri = Path(DB_PATH).resolve().as_uri() + "?mode=rw"
            try:
                conn = sqlite3.connect(uri, timeout=30, uri=True)
            except sqlite3.OperationalError as exc:
                try:
                    os.stat(DB_PATH)
                except FileNotFoundError:
                    raise DatabaseNotFoundError(f"Datastore does not exist: {DB_PATH}") from exc
                raise
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
    _drop_legacy_error_blobs()
    _first_boot_import()
    _upgrade_settings_in_store()
    _upgrade_pellets_in_store()
    _validate_settings_in_store()


def init_existing():
    """Initialize an existing datastore and prohibit creation in this process."""
    global _database_creation_allowed
    _database_creation_allowed = False
    init()


def _drop_legacy_error_blobs():
    """Remove the `kv` rows that the `errors` table replaced.

    Nothing reads `errors`/`display_errors` out of `kv` any more; leaving them
    behind would leave a stale copy of banners that outlives every process that
    could clear them.

    The SELECT guards the DELETE for the reason _ensure_logs_retention
    documents: the steady state is a row that no longer exists, and an
    unconditional DELETE would take a write lock on every process start to
    delete nothing.
    """
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM kv WHERE key IN ('errors','display_errors')").fetchone() is None:
            return
        conn.execute("DELETE FROM kv WHERE key IN ('errors','display_errors')")


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


def _upgrade_settings_in_store():
    """Bring the SQLite-stored settings tree up to the running code's version.

    The settings migration cascade reaches settings imported from a JSON file,
    which happens on first boot and on an explicit restore. A tree that has
    lived in SQLite ever since would otherwise never be migrated again -- so a
    shape change leaves an existing install holding keys the schema no longer
    models, and the write-time repair strips them on the next save.

    Shape migrations are gated on settings["schema_version"], which the release
    version cannot close: a store already stamped at the code's own current
    release still runs every shape step it has not been stamped for.
    """
    import copy
    import json

    from common import settings_migration  # deferred to avoid import cycle
    from common.common import semantic_ver_is_lower, semantic_ver_to_list, write_log
    from common.defaults import default_settings
    from common.settings_schema import SETTINGS_SCHEMA_VERSION, validate_settings_tree

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
        changed = False

        stored = settings.get("versions") or {}
        if stored.get("server") and (
            semantic_ver_is_lower(stored["server"], current["server"])
            or stored.get("build", 0) < current.get("build", 0)
        ):
            prev_ver = semantic_ver_to_list(stored["server"])
            settings = settings_migration.upgrade_settings(prev_ver, settings, settings_default)
            settings["versions"] = current
            changed = True

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
            if settings_migration._apply_shape_migrations(settings, SETTINGS_SCHEMA_VERSION):
                changed = True

        if not changed:
            return

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


def _upgrade_pellets_in_store():
    """Bring the stored pellet database up to the current shape, and stamp it.

    Mirrors _upgrade_settings_in_store: read and write inside one
    BEGIN IMMEDIATE, steps gated on the blob's own stamp, and the stamp written
    last so a crash mid-chain retries the whole chain rather than leaving a
    version ahead of its data.
    """
    import json

    from common.common import write_log
    from common.pellets_schema import _PELLET_MIGRATIONS
    from common.web_contracts.control import PELLETDB_SCHEMA_VERSION

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


def _validate_settings_in_store():
    """Report a settings tree that does not match the models.

    Observes only: the tree is returned to every reader exactly as stored,
    whatever this finds. Runs after the migration steps, so a report here names
    something migrations could not repair -- a hand-edited database, a
    downgrade, or a gap in the registry -- rather than a tree merely waiting to
    be brought forward.
    """
    from common import settings_schema  # deferred to avoid import cycle
    from common.common import write_log
    from common.persistence.runtime import read_settings

    try:
        settings_schema.validate_settings_tree(read_settings(), persisted=False)
    except settings_schema.SettingsValidationError as exc:
        write_log("Stored settings do not match this build's schema: " + "; ".join(exc.errors))


def _reset_for_tests(path):
    """Reset the test path, cached connection, and creation policy."""
    global DB_PATH, _database_creation_allowed
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    DB_PATH = path if path is not None else _ORIGINAL_DB_PATH
    _database_creation_allowed = True


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
