# Warnings-Clear Slice — Design

**Date:** 2026-07-29
**Status:** approved (brainstorming)

## Problem

The Flask-retirement pass deleted the Jinja `dash_page`, which was the sole
caller of `drain_warnings()` — the destructive read that cleared the
`list_warnings` SQLite queue every time a human rendered the dashboard
(`blueprints/dash/routes.py`, comment: "rendering the page is where a human
actually sees the banner, so this is the one consumer entitled to clear it").

React reads warnings **non-destructively** via
`common/datastore_accessors.py::read_warnings()` → the Socket.IO feed
(`blueprints/mobile/socket_io.py`) → `AppShell` → `Banners.tsx`. With the drain
caller gone, nothing flushes `list_warnings`, so **warning banners persist
forever and stack**. The read/drain split was deliberate — `list_warnings` has
independent consumers, so the socket read MUST stay non-destructive.

Errors are out of scope: `read_errors()` is likewise non-destructive, but Flask
never cleared errors on view (they clear only on device rebuild via
`flush_errors()`), so errors remain a pure display with no dismiss.

## Chosen behavior

An **explicit dismiss control** (not an auto-clear-on-view port). Flask's SPA
successor has no per-view render and the banner now lives in the shell (shown on
every page), so "clears when you view the dashboard" does not translate. A user
clicks a single **×** on the warning group to clear it.

### The data-loss trap and the fix

A naive dismiss that calls `drain_warnings()` (flush-all) would delete any
warning `write_warning()` pushed **after** the client rendered its banner but
**before** the user clicked ×. Those warnings were never shown — flushing them
loses data.

`list_warnings` rows carry a monotonic `id` (INTEGER PRIMARY KEY;
`SqliteQueue.push` inserts, `pop`/`list` order by `id`). That `id` is a natural
high-water mark:

1. The socket payload carries the **max outstanding warning id** (`warningsMaxId`)
   alongside the warning strings, read in a **single query** so the id always
   corresponds exactly to the strings returned.
2. The client renders the latest payload, so the max id it displays equals
   `warningsMaxId`. On dismiss it POSTs that id back.
3. The server deletes `WHERE id <= through_id`. A warning pushed after the
   client's snapshot has a higher id and **survives** — lossless under a
   write/dismiss race.

Granularity is a **single** dismiss (clear all shown through the high-water
mark), matching Flask's all-or-nothing clear. Per-warning dismiss was rejected:
it needs a `{id, text}[]` payload and per-row controls for UX Flask never had.

## Architecture / data flow

```
write_warning() ──push──▶ list_warnings (SQLite; rows have monotonic id)
                              │
socket tick: read_warnings_snapshot()  ── ONE query ──▶ (["Hopper low", …], max_id=5)
                              │  packed into socket_dash_data:
                              │    warnings: string[]        (unchanged)
                              │    warningsMaxId: number|null (NEW)
                              ▼
   Banners renders strings; remembers warningsMaxId = 5
   user clicks ×  ──▶  POST /api/dismiss_warnings { through_id: 5 }
                              ▼
   clear_warnings_through(5): DELETE FROM list_warnings WHERE id <= 5
   ── a warning with id 6 (pushed after the snapshot) is NOT deleted ──
```

## Components

### 1. `common/sqlite_queue.py`

- `list_with_ids()` → `list[tuple[int, str]]` from one `SELECT id, value … ORDER BY id`.
- `clear_through(max_id)` → `DELETE FROM {table} WHERE id <= ?`.

These are generic queue primitives (no warnings-specific logic).

### 2. `common/datastore_accessors.py`

- `read_warnings_snapshot()` → `{"warnings": list[str], "max_id": int | None}`,
  derived from a **single** `list_with_ids()` call. `max_id` is the id of the
  last row (or `None` when empty). Guarantees the id matches the strings — no
  read-vs-read race between "the strings" and "the max id".
- `clear_warnings_through(max_id)` → calls `SqliteQueue("list_warnings",
  raw=True).clear_through(max_id)`.
- `read_warnings()` (old, string-only) is **DELETED**. Once socket_io moves to
  the snapshot it has no production caller; its only remaining references would
  be tests. `read_warnings_snapshot()` is its replacement, and the two test
  consumers move to it (see §6).
- `drain_warnings()` (flush-all) is **DELETED**. `clear_warnings_through`
  supersedes it, leaving no production caller.

### Why deleting both is right, not a scope grab

A primitive whose only consumers are the tests that test it is circular — the
test proves nothing about the product, and the pair is dead code on
test-shaped life support. The repo's standing rule is to delete what a fix
obsoletes in the *same* change, keeping only controls with a surviving
independent reason.

`drain_warnings()`'s apparent independent reason does not survive inspection.
`tests/oracle/capture_oracle.py` states its own purpose in its header: *"Record
current **Valkey-backed** accessor behavior as golden fixtures. Run ONCE against
the unmodified codebase with a live valkey-server."* It is a frozen artifact of
the Valkey→SQLite migration and cannot be re-run (no valkey-server exists here
any more). `scenario_warnings` pins **read-and-burn** semantics — and after this
slice no production code path has read-and-burn semantics at all, because
clearing is `DELETE WHERE id <= n`. So the fixture would pin a behavior the
product no longer has.

Deleted with it: `scenario_warnings` from `capture_oracle.py`,
`tests/oracle/fixtures/warnings.json`, and
`test_warnings_drain_and_clear_matches_oracle`. The other three oracle scenarios
(`control_merge`, `history_cap`, `metrics_replace_last`) are untouched — they
still pin live behavior.

### 3. `blueprints/mobile/socket_io.py`

- Replace `warnings = read_warnings()` with the snapshot; set the payload's
  `"warnings"` from `snapshot["warnings"]` and add
  `"warningsMaxId": snapshot["max_id"]`. Payload keys are already camelCase
  (`criticalError`, `grillName`), so `warningsMaxId` matches convention.

### 4. `blueprints/api/routes.py` — new POST action

- `POST /api/dismiss_warnings` via a new `_API_POST_ACTIONS["dismiss_warnings"]`
  handler. Body `{"through_id": int}`.
- The existing POST path already `abort(400)`s when there is no JSON body. The
  handler additionally validates `through_id` is an integer (reject `bool`,
  `None`, non-numeric) → `400`. On success calls `clear_warnings_through` and
  returns `{"result": "ok"}`.
- No `is_real_hardware()` / STOP-mode guard — this is a datastore mutation, not
  a hardware command.

### 5. React — `web-react/`

- `src/helpers/types.ts`: the dash payload type gains
  `warningsMaxId: number | null`.
- `src/helpers/shell/warningsApi.ts` (NEW): `dismissWarnings(throughId: number)`
  POSTs `{ through_id: throughId }` to `/api/dismiss_warnings`, mirroring
  `updateApi.ts`'s `unpack`/`post` shape.
- `src/components/shell/AppShell.tsx`: pass `warningsMaxId` into `Banners`.
- `src/components/shell/Banners.tsx`:
  - Takes `warningsMaxId: number | null`.
  - Local state `dismissedThroughId: number` (init `-1` / `0`; ids are positive).
  - **Show warnings iff `warningsMaxId != null && warningsMaxId > dismissedThroughId`.**
    The single scalar drives everything: after dismiss, the still-stale payload
    is hidden immediately (optimistic), and a newer warning (higher id) makes
    the group reappear without re-showing dismissed ones (the server has already
    deleted them, so the next payload omits them).
  - One **×** control on the warning group. On click: call
    `dismissWarnings(warningsMaxId)`; on success set
    `dismissedThroughId = warningsMaxId`.
  - Errors always render (unchanged, not dismissable).

## Error handling

- Missing/empty JSON body → existing `abort(400)`.
- `through_id` not an integer → `400` with a JSON error.
- Deleting through an id with no matching rows is a no-op success (idempotent) —
  a double-click or a stale id simply deletes nothing new.
- Client POST failure: the dismiss is not recorded (`dismissedThroughId`
  unchanged), so the banner remains — the user can retry. No optimistic clear is
  committed until the POST resolves ok.

## Testing

**Backend (`tests/` Python):**
- `clear_warnings_through(n)` deletes rows with `id <= n` **and preserves a
  higher-id warning** pushed after the snapshot — the explicit lossless/race
  property.

**§6 — test consumers that move rather than die.** These pin real product
behavior and MUST survive, rewritten onto the new accessors:
- `test_read_warnings_does_not_consume` pins the non-destructive read — the
  property whose absence was the original cross-consumer bug ("stop one consumer
  eating the other's warnings"). Rewritten: two successive
  `read_warnings_snapshot()` calls return the same warnings, then
  `clear_warnings_through(max_id)` empties them. Same property, current
  accessors.
- `tests/unit/deps/test_extra_installer.py` asserts the extra-installer's
  "finished installing" banner reaches the user. Rewritten to observe via
  `read_warnings_snapshot()["warnings"]`. The behavior under test (the installer
  writes a banner) is unchanged — only the observation accessor moves.
- `list_with_ids()` returns `(id, value)` in insertion order; `read_warnings_snapshot()`'s
  `max_id` equals the id of the last returned string, and is `None` when empty.
- `POST /api/dismiss_warnings`: ok path calls `clear_warnings_through`; missing
  body → 400; non-integer `through_id` → 400.

**Cross-process seam (per repo convention):**
- A shape test pins `warningsMaxId` in the socket payload at BOTH ends — the
  producer (`socket_io` payload) and the React type — using a fixture built from
  a real payload, not hand-written literals.

**React (`web-react`, Vitest + RTL):**
- `×` renders only when warnings are present and undismissed.
- Clicking `×` POSTs `warningsMaxId`; on success the group hides (optimistic).
- A subsequent payload with a higher `warningsMaxId` re-shows the group; one
  with `warningsMaxId <= dismissedThroughId` (or `null`) keeps it hidden.
- Errors render regardless of warning dismissal.
- Gates: `bun run lint` (Biome format), typecheck, test, build.

## Out of scope

- Errors dismiss (Flask never cleared errors on view).
- Per-warning dismiss.
- Any change to `write_warning()` producers or the independent `list_warnings`
  consumers.
- The three surviving oracle scenarios (`control_merge`, `history_cap`,
  `metrics_replace_last`) and the rest of the Valkey-migration fixture set.
- Any behavior change to the extra-installer or other `write_warning()`
  producers — only the accessor the tests observe through moves.

**Deletions are bounded to exactly:** `read_warnings()`, `drain_warnings()`,
`scenario_warnings`, `fixtures/warnings.json`, and
`test_warnings_drain_and_clear_matches_oracle`. No other test may be deleted to
make this slice fit — a test that fails should be rewritten onto the new
accessors (§6) or the design is wrong.
