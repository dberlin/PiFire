// Mirrors common/pellets_schema.py PelletDbSchema and the payload
// blueprints/mobile/socket_io.py emits as `socket_pellet_data`.
//
// SHAPE PIN: tests/web/test_api_pellets.py::test_get_pellets_returns_full_database
// asserts these exact key sets against a live GET /api/pellets. If you add a
// field here, add it there in the same commit -- a hand-written type for a
// cross-process payload is a guess until something checks it.
//
// Hand-written because `gen:types` compiles schema/settings.schema.json only
// (scripts/gen-types.ts:15-16) and the pellet DB has no JSON schema.

/** One archive entry. The archive key is the profile id; the entry does not
    repeat it. */
export interface PelletProfile {
  brand: string;
  wood: string;
  rating: number; // 1-5, enforced by common/pellets_schema.py, rendered as stars
  comments: string;
}

/** One load. `pelletid` is null exactly when the profile it named was deleted
    -- common/pellets_actions.py pellets_delete_profile writes the tombstone
    rather than removing the entry. */
export interface PelletLogEntry {
  pelletid: string | null;
  deleted: boolean;
}

export interface PelletCurrent {
  /** Key into `archive`. May be absent from `archive` if the DB was cleared. */
  pelletid: string;
  /** Percent remaining, 0-100. Also arrives on socket_dash_data as `hopperLevel`. */
  hopper_level: number;
  /** "YYYY-MM-DD HH:MM:SS" -- str(datetime.now())[0:19]. */
  date_loaded: string;
  /** GRAMS since the last load, a float. The control process increments this
        (controller/runtime/modes/base.py); nothing in the UI writes it except
        indirectly, by loading a profile (which zeroes it). */
  est_usage: number;
}

export interface PelletDb {
  /** The SHAPE this database was written against, independent of the release
        version (common/pellets_schema.py PELLETDB_SCHEMA_VERSION). */
  schema_version: number;
  current: PelletCurrent;
  brands: string[];
  woods: string[];
  archive: Record<string, PelletProfile>;
  /** Load time in epoch MILLISECONDS, as a decimal string -- JSON object keys
        are strings. Sort numerically; text order is wrong across digit counts. */
  log: Record<string, PelletLogEntry>;
  lastupdated: { time: number };
}
