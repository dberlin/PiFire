// Mirrors common/defaults.py default_pellets() (:616-672) and the payload
// blueprints/mobile/socket_io.py:174 emits as `socket_pellet_data`.
//
// SHAPE PIN: tests/web/test_api_pellets.py::test_get_pellets_returns_full_database
// asserts these exact key sets against a live GET /api/pellets. If you add a
// field here, add it there in the same commit -- a hand-written type for a
// cross-process payload is a guess until something checks it.
//
// Hand-written because `gen:types` compiles schema/settings.schema.json only
// (scripts/gen-types.ts:15-16) and the pellet DB has no JSON schema.

/** One archive entry. `id` repeats the archive key (defaults.py:657-665). */
export interface PelletProfile {
  id: string;
  brand: string;
  wood: string;
  rating: number; // 1-5, rendered as stars
  comments: string;
}

export interface PelletCurrent {
  /** Key into `archive`. May be absent from `archive` if the DB was cleared. */
  pelletid: string;
  /** Percent remaining, 0-100. Also arrives on socket_dash_data as `hopperLevel`. */
  hopper_level: number;
  /** "YYYY-MM-DD HH:MM:SS" -- str(datetime.now())[0:19], defaults.py:619-620. */
  date_loaded: string;
  /** GRAMS since the last load. The control process increments this
        (controller/runtime/modes/base.py:761-763); nothing in the UI writes it
        except indirectly, by loading a profile (which zeroes it). */
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
  /** timestamp key -> profile id, or the literal "deleted" when the profile
        it pointed at was removed (common/pellets_actions.py, delete_profile). */
  log: Record<string, string>;
  lastupdated: { time: number };
}
