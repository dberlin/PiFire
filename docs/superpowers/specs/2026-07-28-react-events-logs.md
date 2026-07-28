# React Events + Logs — Design

**Date:** 2026-07-28
**Status:** design approved, plan pending
**Backlog item:** §8 "Un-migrated Flask pages" — `events` + `logs`, the last
navbar entry still rendered disabled.

## Goal

Replace Flask's two remaining reader pages (`/events`, `/logs`) with one React
route at `/events` carrying two tabs, built on
[`@melloware/react-logviewer`](https://github.com/melloware/react-logviewer).
Close the two path-traversal doors in `blueprints/logs/routes.py`, surface the
rotated log files that both Flask and the shipped admin API currently hide, and
fix the events-clear path, which today clears a store nothing reads.

## Current state

### The two Flask blueprints

Both use the same trick: a `GET` renders the page, and a `POST` carrying an
`eventslist` field returns a *rendered HTML fragment* of one page of rows that
the page swaps in by AJAX.

| | `blueprints/events/routes.py` (31 lines) | `blueprints/logs/routes.py` (48 lines) |
|---|---|---|
| Source | `read_events(legacy=False)` over `./logs/events.log` | `read_log_file(LOGS_FOLDER + <name>)` |
| Rows | `line.split(" ", 2)` → 3 columns | raw lines + `add_line_numbers()` |
| Paging | `paginate_list(page, reverse, itemsperpage)` | same |
| Extra | — | file picker + download |

`read_events`'s 3-way split means Flask's message column literally begins with
the timezone offset and level: a line reading
`2026-07-27 20:44:04 -0400 [INFO] Clearing History Log.` renders as
`date=2026-07-27`, `time=20:44:04`, `message=-0400 [INFO] Clearing History Log.`

### Defects this replaces

1. **Two path-traversal doors.** `blueprints/logs/routes.py:29` calls
   `send_file(LOGS_FOLDER + requestform["selectLog"])` and `:33` calls
   `read_log_file(LOGS_FOLDER + requestform["logfile"])`. Both concatenate a
   client-supplied string onto the logs folder with no containment check —
   the same class as the backup-restore hole closed in the 2026-07-27 admin
   slice, but two doors, and both are *reads* of arbitrary files.

2. **Rotated files are invisible, two different ways.** Flask filters the
   directory listing through `allowed_file()`, which tests the extension
   against `ALLOWED_EXTENSIONS`; rotation produces `events.log.1`, whose
   extension is `1`. The shipped `admin_api.list_logs()` filters on
   `n.endswith(".log")` and reaches the same conclusion by a different route.
   On the development machine this hides 5 of 15 files, **including the three
   largest**.

3. **Flask's filter is also written incorrectly.** `blueprints/logs/routes.py:21-23`
   mutates the list it is iterating (`for file in log_file_list: ...
   log_file_list.remove(file)`), which skips the element after every removal.

4. **The events clear path targets the wrong store.**
   `common/common.py::read_events_records()` reads the **file** (via
   `read_events()`), but `common/common.py::flush_events_records()` calls
   `datastore.clear_log("events")`, which deletes **database** rows. Clearing
   events therefore wipes rows nothing reads and leaves the file everything
   reads. This is directly observable: the development database holds 1
   `events` row against 1,062 lines in `logs/events.log`.

### Logging topology

`common/common.py::create_logger()` attaches **two** handlers to every logger:

- `RotatingFileHandler(filename, maxBytes=1 MiB, backupCount=3)` — so each
  logger is hard-capped at ~4 MiB across `x.log`, `x.log.1`, `x.log.2`,
  `x.log.3`. Rotation shifts suffixes **upward**, so the highest-numbered file
  is the **oldest**.
- `common/sqlite_log_handler.py::SqliteLogHandler` — `INSERT INTO logs(name,
  ts, message)`.

The `logs` table is `id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ts
INTEGER, message TEXT` with `INDEX ix_logs_name_id ON logs(name, id)`.

Everything routed through `create_logger` — the web app included — reaches both
sinks. Only **supervisord** writes files that never reach the database
(`logs/logfiles.txt` records that the directory is also supervisord's).

### Why files, not the database

The database is the *cleaner* record: the development table is missing recent
activity only because the test suite writes to the real `./logs/` while using
temporary databases, so the files carry test noise (`[nonexistent_probe_module_xyz]`,
`WLED ... port=1`, `Admin: Shutdown failed: boom`) that the database does not.

Files win anyway, for three reasons:

- **Completeness.** Supervisord's files have no database rows at all. A
  database-backed viewer structurally cannot show them.
- **Boundedness.** Files are capped at 1 MiB × 4 per logger by
  `RotatingFileHandler`. The `logs` table has no retention policy anywhere in
  the tree — it grows forever onto a Pi's SD card. (This spec adds one, but
  that is a separate concern from what the viewer reads.)
- **Agreement with Download.** The shipped `/api/admin/logs/download` serves
  files. A viewer reading a different store would show bytes the download
  button does not produce.

The test-noise problem is a development-box artifact — no tests run on a real
PiFire — and this spec fixes it at the source anyway.

## Design

### Backend

#### One new read endpoint

```
GET /api/admin/logs/view?log=<family>   →  200 text/plain
                                           Accept-Ranges: bytes
                                        →  206 Partial Content  (on Range:)
                                        →  416                  (offset past end)
                                        →  404                  (unknown family)
```

`log` is a **family stem** (`events`, `mqtt`, `control`), never a filename and
never a path. This is the containment story: with no client-supplied path
component to concatenate, `../` has nowhere to go, and the two Flask doors are
not reproduced. The stem is validated against the set `list_log_families()`
returns rather than pattern-matched.

The body is the family stitched in **chronological order** —
`events.log.3` + `.log.2` + `.log.1` + `events.log`, oldest first — because
rotation shifts suffixes upward. This is why the payload cannot be a file path:
it does not exist as a single file on disk.

The stitched bytes are assembled into a `BytesIO` and returned via
`send_file(buf, mimetype="text/plain", conditional=True)`. `conditional=True`
makes Flask advertise `Accept-Ranges: bytes` and answer `Range:` with `206` and
a `Content-Range` header — the same machinery works for a synthesized buffer as
for a real file, given a known length.

#### Listing

Add `admin_api.list_log_families()` returning, per family, its stem, its
ordered member filenames, and the total stitched size in bytes. **`list_logs()`
is left exactly as it is** so the LogsCard shipped on 2026-07-27 does not move.

#### Family-aware write paths

- `delete_logs()` — deletes every member of every family, not only `*.log`.
  Today it leaves every rotated file behind, so "Delete All" does not delete
  what the viewer shows.
- `build_log_archive()` — zips every member. Today it zips `list_logs()` and
  inherits the same blind spot.
- `clear_events_log()` — clears the whole `events` family **and** the `events`
  rows in the database, so one action empties both stores. This is the fix for
  defect 4.

`GET /api/admin/logs/download` takes **no** parameters: it calls
`build_log_archive()` and returns the whole ZIP, and the shipped LogsCard offers
only a "Download All" link. There is no per-file log download today. Rather than
add one, the Log Files tab links each family at the new `view` endpoint with
`&download=1`, which flips the response to `as_attachment=True` — so the bytes
offered are by construction the bytes displayed.

#### Database retention

Add `datastore.prune_log(name, keep)`:

```sql
DELETE FROM logs
 WHERE name = ?
   AND id < (SELECT id FROM logs WHERE name = ?
              ORDER BY id DESC LIMIT 1 OFFSET ?)
```

`OFFSET keep` finds the id of the `keep`-th newest row for that logger and
deletes everything below it. This uses `ix_logs_name_id` directly. It must be
per-`name`: ids are a single global `AUTOINCREMENT` sequence shared across
loggers, so any `MAX(id) - keep` arithmetic would be wrong wherever loggers
interleave.

`SqliteLogHandler` calls it every `PRUNE_INTERVAL` emits (counter on the
handler instance), not on every record.

**Proposed defaults — confirm before implementation:** `keep = 20_000` rows per
logger, `PRUNE_INTERVAL = 1_000` emits. At roughly 150 bytes a row that is
~3 MB per logger, chosen to sit near the ~4 MiB the file handler already allows.

`datastore.read_log()` is **kept** as the database read API even though it
currently has no callers.

#### Test pollution

The suite writes into the real `./logs/`. Route every log file path through one
resolvable location and add an autouse `conftest.py` fixture that points it at
`tmp_path` for the whole suite, so a test run cannot append to the operator's
logs. Live end-to-end runs against the real backend still write real logs; that
is correct and unchanged.

### Frontend

Route `/events`, tabs `Events` and `Log Files`, tab reflected in the URL.
The navbar entry becomes a real link — the last one still rendered disabled.

Both tabs render one shared viewer component wrapping `LazyLog`:

- Events pins it to the `events` family with tailing on.
- Log Files adds a family picker (from `list_log_families()`) and per-member
  download links.

Component configuration: `enableSearch`, `enableSearchNavigation`,
`caseInsensitive`, `enableLineNumbers`, `selectableLines`, `wrapLines`, and a
`formatPart` that colourizes `[ERROR]` / `[WARNING]` / `[INFO]`. `LazyLog`
virtualizes through `virtua`, so a 1 MiB family is the case it is built for and
no pagination is needed on either side.

#### Live tail

The client holds a byte offset and polls `Range: bytes=<offset>-`. A `206`
yields only the new bytes, appended through a `ref` to `LazyLog.appendLines()`
with `external: true`. Nothing new yields `416` and no body.

Deliberately **not** SSE or WebSocket: gunicorn runs `-k gthread --threads 25
-w 1` here, and a held streaming connection occupies a thread for as long as the
tab is open. Range polling holds one for milliseconds.

**Rotation during tail must be handled.** When a family rotates, the stitched
total *shrinks* and the stored offset lands past the end. A `416` carries
`Content-Range: bytes */<size>`; when that size is below the stored offset the
family rolled and the client re-fetches from zero. Without this check the tail
silently stops updating, which on a grill reads as a dead appliance.

## Dependency

`@melloware/react-logviewer@6.5.5` — MPL-2.0, peer `react >=17` (repo is on
19.2.8), runtime deps `virtua`, `hotkeys-js`, `immutable`, `mitt`,
`react-string-replace`. Installed with **bun**; `bun.lock` is committed.

## Testing

- **Python unit** — family grouping and chronological order; stitched bytes
  equal the concatenation of members oldest-first; `Range` returns `206` with
  the right slice and `416` past the end; an unknown or path-shaped `log`
  parameter returns `404` with nothing read. Traversal tests place a **real
  decoy file** where `../` resolves, so a missing-file check cannot fake the
  pass; each is proved by negative control.
- **Python unit** — `prune_log` keeps exactly `keep` rows for the named logger
  and leaves other loggers untouched, asserted with interleaved ids.
- **Python unit** — clearing events empties both the family and the database
  rows.
- **rstest/RTL** — offset arithmetic, the `416`-with-shrunk-size rotation
  branch, tab routing, family picker.
- **Playwright** — `/events` against the live backend: both tabs render, search
  works, the tail appends after a new line is logged. The spec must not delete
  or clear anything; write routes are aborted at the network boundary and
  recorded, with an `afterEach` that fails if any attempt escaped — the pattern
  established by `tests/e2e/admin.spec.ts`.
- **Baselines** — add `events` to `pageSpecs.ts` with a `stubEvents` fixture,
  capture with `-g "events"`, verify the diff is pure additions.

Gate: `typecheck`, `typecheck:e2e`, `lint`, `bun run test`,
`bun run test:e2e:fidelity`, the named Playwright spec, and
`QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`.

## Out of scope

- Retiring `blueprints/events/` and `blueprints/logs/`. They stay live until the
  general Flask retirement pass, along with their characterization tests.
- Server-side search or filtering. `LazyLog` searches client-side over material
  it already holds.
- Making `datastore.read_log()` the viewer's source, or building a database-backed
  view. Kept as an API; not wired to a surface.
- Reworking `read_events()`'s 3-way split. The viewer renders raw lines, so the
  split's quirk stops mattering; the function stays for its Flask callers.

## Decisions to confirm at review

1. Retention defaults: `keep = 20_000` rows per logger, `PRUNE_INTERVAL = 1_000`
   emits. Pruning is destructive and irreversible, so these numbers deserve an
   explicit yes.
2. Whether `logs/logfiles.txt` (not a log; a supervisord placeholder) should be
   excluded from the family listing. Proposed: yes, exclude non-`.log*` names.
