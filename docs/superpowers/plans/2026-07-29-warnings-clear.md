# Warnings-Clear Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give React an explicit "dismiss" control that clears warning banners without ever deleting a warning the user has not seen.

**Architecture:** `list_warnings` rows carry a monotonic `id`. One query returns both the warning strings and their `max_id`; the Socket.IO payload carries that id as `warningsMaxId`; dismissing POSTs the id back and the server deletes `WHERE id <= through_id`. A warning written *after* the client's snapshot has a higher id and survives. The read-and-burn primitives (`read_warnings`, `drain_warnings`) and the frozen Valkey-era oracle scenario that pinned them are deleted in the same change.

**Tech Stack:** Python 3.14 / Flask / SQLite (`SqliteQueue`), Flask-SocketIO, React + TypeScript (`web-react`, bun + Biome + `@rstest/core`/RTL).

> **Correction applied during execution:** the two TypeScript test snippets below were originally written against **Vitest** (`import … from "vitest"`, `vi.*`). This repo has no vitest package — the runner is **`@rstest/core`** and its mock API is **`rs`** (`rs.fn`, `rs.mock`, `rs.stubGlobal`, `rs.spyOn`). The snippets are corrected here. Two things to know: `bun test` invokes bun's OWN runner and fails with `Rstest API 'describe' is not registered yet` — use **`bun run test`**; and a `Cannot find module 'vitest'` error superficially resembles a TDD-red, so it can fool you into thinking a test failed for the right reason. Prefer a neighbouring real test file over any snippet when the two disagree on framework mechanics.

**Spec:** `docs/superpowers/specs/2026-07-29-warnings-clear-design.md`

## Global Constraints

- Python tests run as `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <paths>`. A bare `python`/`pytest` gives false failures.
- Format Python with `.venv/bin/ruff format <changed files>` before every commit. NEVER `uvx ruff` (the repo pins ruff <0.16).
- `web-react` uses **bun**, never npm. Gates: `bun run lint` (Biome format — required, not just typecheck), `bun run typecheck`, `bun run test`, `bun run build`.
- Commit with **jj**, not git: `jj new` BEFORE the first Write, then `jj describe -m "..."`. Never `jj squash` after editing (the edits are already in `@`).
- The Socket.IO read path MUST stay non-destructive. `list_warnings` has independent consumers; a repeating poll that consumed warnings would land them in one payload and lose them for every client that reconnects after.
- **Deletions are bounded to exactly these five:** `read_warnings()`, `drain_warnings()`, `scenario_warnings`, `tests/oracle/fixtures/warnings.json`, `test_warnings_drain_and_clear_matches_oracle`. No other test may be deleted to make this slice fit — a test that fails is rewritten onto the new accessors, or the design is wrong.
- The three surviving oracle scenarios (`control_merge`, `history_cap`, `metrics_replace_last`) and their fixtures are untouched.
- New warning ids are positive integers (SQLite INTEGER PRIMARY KEY starts at 1), so `0` is a safe "nothing dismissed yet" sentinel.

## Verified Facts (confirmed against live code — do not re-derive)

- `common/sqlite_queue.py::SqliteQueue` is constructed as `SqliteQueue("list_warnings", raw=True)`. `raw=True` stores strings verbatim, so `_decode` is the identity for warnings.
- Existing `SqliteQueue` methods: `push`, `pop`, `length`, `list(start=0, end=-1)`, `flush`. `list()` does `SELECT value FROM {table} ORDER BY id` — it **discards the id**, which is why a new method is needed.
- `datastore.execute_write(sql, params)` performs writes; `datastore.connection().execute(...)` performs reads. Both are used throughout `SqliteQueue`.
- `blueprints/api/routes.py:445` defines `_API_POST_ACTIONS = {...}`; handlers are called as `handler(settings, request_json)` and each returns `(jsonify(...), status)`.
- The POST branch already does `abort(400)` when `request.json` is falsy — a missing/empty body is handled before any handler runs.
- `blueprints/api/routes.py:15` already imports `api_response` from `common.app`. `api_response(result, message=None, data=None)` returns the bare dict `{"data", "result", "message"}` (no jsonify, no status).
- `blueprints/mobile/socket_io.py:45` imports `read_warnings`; line 271 does `warnings = read_warnings()`; the `dash_data` dict sets `"warnings": warnings` and already uses camelCase keys (`criticalError`, `grillName`).
- `web-react/src/components/shell/AppShell.tsx:40-44` renders `<Banners errors={live.errors ?? []} warnings={live.warnings ?? []} criticalError={live.criticalError} />`.
- `web-react/src/helpers/types.ts:45-47` declares `errors: string[]; warnings: string[]; criticalError: boolean;`.
- `web-react/src/helpers/update/updateApi.ts` is the client pattern to mirror: `const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";` and an `unpack<T>(res)` that resolves `{ok, status, message, data}` rather than throwing.
- Test consumers that must be **rewritten, not deleted**: `tests/unit/common/test_common_blobs.py::test_read_warnings_does_not_consume` and `tests/unit/deps/test_extra_installer.py::test_successful_install_records_done_and_tells_the_user` (asserts `any("finished installing" in w for w in banners)`).

## File Structure

| File | Responsibility |
|---|---|
| `common/sqlite_queue.py` | MODIFY — generic queue primitives `list_with_ids()`, `clear_through(max_id)`. No warnings-specific logic. |
| `common/datastore_accessors.py` | MODIFY — add `read_warnings_snapshot()`, `clear_warnings_through()`; delete `read_warnings()`, `drain_warnings()`. |
| `blueprints/mobile/socket_io.py` | MODIFY — consume the snapshot; add `warningsMaxId` to the payload. |
| `blueprints/api/routes.py` | MODIFY — `_api_post_dismiss_warnings` handler + registration. |
| `tests/unit/common/test_sqlite_queue_ids.py` | CREATE — queue primitive tests incl. the lossless-race property. |
| `tests/unit/common/test_common_blobs.py` | MODIFY — rewrite the non-consume test; delete the oracle test. |
| `tests/unit/deps/test_extra_installer.py` | MODIFY — observe via the snapshot. |
| `tests/oracle/capture_oracle.py` | MODIFY — delete `scenario_warnings` + its `_dump` call. |
| `tests/oracle/fixtures/warnings.json` | DELETE |
| `tests/web/test_api_dismiss_warnings.py` | CREATE — endpoint tests. |
| `tests/web/test_socket_warnings_payload.py` | CREATE — cross-process seam shape test. |
| `web-react/src/helpers/types.ts` | MODIFY — `warningsMaxId: number \| null`. |
| `web-react/src/helpers/shell/warningsApi.ts` | CREATE — `dismissWarnings(throughId)`. |
| `web-react/src/components/shell/Banners.tsx` | MODIFY — dismiss control + visibility rule. |
| `web-react/src/components/shell/AppShell.tsx` | MODIFY — pass `warningsMaxId`. |
| `web-react/src/components/shell/shell.css` | MODIFY — dismiss button style. |
| `web-react/src/components/shell/Banners.test.tsx` | MODIFY — dismiss behavior tests. |

---

### Task 1: Queue id primitives

**Files:**
- Modify: `common/sqlite_queue.py`
- Test: `tests/unit/common/test_sqlite_queue_ids.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `SqliteQueue.list_with_ids() -> list[tuple[int, str]]` and `SqliteQueue.clear_through(max_id: int) -> None`. Task 2 calls both.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/common/test_sqlite_queue_ids.py`:

```python
from common.sqlite_queue import SqliteQueue


def _q():
    return SqliteQueue("list_warnings", raw=True)


def test_list_with_ids_returns_ids_in_insertion_order(ds):
    q = _q()
    q.push("first")
    q.push("second")
    rows = q.list_with_ids()
    assert [v for _, v in rows] == ["first", "second"]
    ids = [i for i, _ in rows]
    assert ids == sorted(ids)
    assert all(isinstance(i, int) for i in ids)


def test_list_with_ids_is_empty_for_empty_queue(ds):
    assert _q().list_with_ids() == []


def test_clear_through_deletes_only_up_to_the_id(ds):
    q = _q()
    q.push("first")
    q.push("second")
    first_id = q.list_with_ids()[0][0]
    q.clear_through(first_id)
    assert [v for _, v in q.list_with_ids()] == ["second"]


def test_clear_through_preserves_a_warning_written_after_the_snapshot(ds):
    # THE lossless property: a warning pushed after the client's snapshot has a
    # higher id, so dismissing the snapshot must not delete it unseen.
    q = _q()
    q.push("seen")
    snapshot_max_id = q.list_with_ids()[-1][0]
    q.push("written after the snapshot")
    q.clear_through(snapshot_max_id)
    assert [v for _, v in q.list_with_ids()] == ["written after the snapshot"]


def test_clear_through_is_idempotent(ds):
    q = _q()
    q.push("first")
    max_id = q.list_with_ids()[-1][0]
    q.clear_through(max_id)
    q.clear_through(max_id)
    assert q.list_with_ids() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_sqlite_queue_ids.py -v`
Expected: FAIL with `AttributeError: 'SqliteQueue' object has no attribute 'list_with_ids'`

- [ ] **Step 3: Implement the two methods**

In `common/sqlite_queue.py`, add to `class SqliteQueue` immediately after `list()`:

```python
    def list_with_ids(self, start=0, end=-1):
        """Return [(id, value)] in insertion order.

        The id is the row's monotonic INTEGER PRIMARY KEY. Callers that clear
        what they have shown a user need it as a high-water mark, which plain
        list() cannot provide because it drops the id.
        """
        rows = datastore.connection().execute(f"SELECT id, value FROM {self.table} ORDER BY id").fetchall()
        values = [(r[0], self._decode(r[1])) for r in rows]
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    def clear_through(self, max_id):
        """Delete every row with id <= max_id.

        Bounded counterpart to flush(): a row inserted after the caller read its
        high-water mark has a larger id and survives, so a concurrent writer's
        entry is never discarded unread.
        """
        datastore.execute_write(f"DELETE FROM {self.table} WHERE id <= ?", (max_id,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_sqlite_queue_ids.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format common/sqlite_queue.py tests/unit/common/test_sqlite_queue_ids.py
jj describe -m "feat(queue): id-aware list and bounded clear for SqliteQueue

list_with_ids exposes each row's monotonic id so a consumer can record a
high-water mark; clear_through deletes only through that mark, leaving rows
inserted afterwards intact."
```

---

### Task 2: Warnings accessors — snapshot in, read-and-burn out

**Files:**
- Modify: `common/datastore_accessors.py`
- Modify: `tests/unit/common/test_common_blobs.py`
- Modify: `tests/unit/deps/test_extra_installer.py`
- Modify: `tests/oracle/capture_oracle.py`
- Delete: `tests/oracle/fixtures/warnings.json`

**Interfaces:**
- Consumes: `SqliteQueue.list_with_ids()`, `SqliteQueue.clear_through(max_id)` (Task 1).
- Produces: `read_warnings_snapshot() -> {"warnings": list[str], "max_id": int | None}` and `clear_warnings_through(max_id) -> None`. Task 3 uses the snapshot; Task 4 uses the clear.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/common/test_common_blobs.py`, **replace** `test_warnings_drain_and_clear_matches_oracle` and `test_read_warnings_does_not_consume` (both, lines ~128-147) with:

```python
def test_read_warnings_snapshot_does_not_consume(ds):
    # The non-destructive property. Its absence was the original cross-consumer
    # bug: the Socket.IO poll ate the warnings before another consumer saw them.
    c.write_warning("first")
    c.write_warning("second")
    snap = c.read_warnings_snapshot()
    assert snap["warnings"] == ["first", "second"]
    assert c.read_warnings_snapshot()["warnings"] == ["first", "second"]
    c.clear_warnings_through(snap["max_id"])
    assert c.read_warnings_snapshot()["warnings"] == []


def test_read_warnings_snapshot_max_id_matches_the_returned_strings(ds):
    c.write_warning("first")
    c.write_warning("second")
    snap = c.read_warnings_snapshot()
    # max_id belongs to the LAST string returned, so clearing through it clears
    # exactly what was returned and nothing more.
    c.clear_warnings_through(snap["max_id"])
    assert c.read_warnings_snapshot()["warnings"] == []


def test_read_warnings_snapshot_is_empty_with_null_max_id(ds):
    assert c.read_warnings_snapshot() == {"warnings": [], "max_id": None}
```

In `tests/unit/deps/test_extra_installer.py::test_successful_install_records_done_and_tells_the_user`, change the import and the read (around lines 95-98) from `read_warnings` to the snapshot:

```python
    from common.datastore_accessors import read_warnings_snapshot

    banners = read_warnings_snapshot()["warnings"]
    assert any("finished installing" in w for w in banners)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_common_blobs.py tests/unit/deps/test_extra_installer.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'read_warnings_snapshot'`

- [ ] **Step 3: Add the new accessors and delete the obsolete ones**

In `common/datastore_accessors.py`, **delete** `read_warnings()` (lines ~213-223) and `drain_warnings()` (lines ~226-243) entirely, and put in their place:

```python
def read_warnings_snapshot():
    """
    Read the outstanding warnings together with their high-water mark id.

    One query, so ``max_id`` always belongs to the last string in ``warnings``
    -- a caller that clears through it clears exactly what it was handed. Two
    separate reads could not promise that.

    Non-destructive, matching :func:`read_errors`. Consumed by the Socket.IO
    feed (``blueprints/mobile/socket_io.py``), which packs it into the
    ``socket_dash_data`` payload for the React warning banners.

    :return: {"warnings": [str], "max_id": int | None} -- max_id is None when
        there are no outstanding warnings.
    """
    rows = SqliteQueue("list_warnings", raw=True).list_with_ids()
    return {"warnings": [v for _, v in rows], "max_id": rows[-1][0] if rows else None}


def clear_warnings_through(max_id):
    """
    Clear the warnings up to and including ``max_id``.

    The dismiss primitive: a user clears the banner they were shown, identified
    by the high-water mark that came with it. A warning written after that
    snapshot has a larger id and survives, so it is never discarded unread --
    which is why this is bounded rather than a flush.

    :param max_id: High-water mark from :func:`read_warnings_snapshot`.
    """
    SqliteQueue("list_warnings", raw=True).clear_through(max_id)
```

- [ ] **Step 4: Delete the frozen oracle scenario**

In `tests/oracle/capture_oracle.py`, delete the whole `scenario_warnings` function (lines ~60-67) and its `_dump("warnings", scenario_warnings())` line in `main()`. Then delete the fixture:

```bash
rm tests/oracle/fixtures/warnings.json
```

The capture script's header states it records **Valkey-backed** behavior and is run once against the pre-migration codebase; the warnings scenario pinned read-and-burn semantics that no production path has once clearing is `DELETE WHERE id <= n`. The other three scenarios still pin live behavior and stay.

- [ ] **Step 5: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_common_blobs.py tests/unit/deps/test_extra_installer.py tests/oracle -v`
Expected: PASS, no reference to `read_warnings`/`drain_warnings` remains.

- [ ] **Step 6: Verify nothing still references the deleted accessors**

Run: `rg -n "read_warnings\b|drain_warnings" -g '!.venv' .`
Expected: only `read_warnings_snapshot` hits. Any bare `read_warnings(`/`drain_warnings(` left is a miss — fix it before committing.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format common/datastore_accessors.py tests/unit/common/test_common_blobs.py tests/unit/deps/test_extra_installer.py tests/oracle/capture_oracle.py
jj describe -m "feat(warnings): snapshot read with a high-water mark; retire read-and-burn

read_warnings_snapshot returns the warnings and their max id from one query, so
a dismiss can clear exactly what was shown. clear_warnings_through replaces
drain_warnings, which flushed everything including warnings written after the
reader's snapshot.

read_warnings and drain_warnings had no production caller left, and the oracle
scenario pinning them recorded pre-migration Valkey read-and-burn behavior that
no code path has any more, so scenario and fixture go with them."
```

---

### Task 3: Socket payload carries `warningsMaxId`

**Files:**
- Modify: `blueprints/mobile/socket_io.py`
- Test: `tests/web/test_socket_warnings_payload.py`

**Interfaces:**
- Consumes: `read_warnings_snapshot()` (Task 2).
- Produces: the `socket_dash_data` payload key `warningsMaxId` (`int | None`), read by Task 5's TypeScript type.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_socket_warnings_payload.py`:

```python
from common.datastore_accessors import read_pellet_db, read_settings, write_warning


def _dash_data():
    # Build the real payload the React shell subscribes to, rather than
    # asserting against a hand-written literal: a fixture written from the
    # producer's own fallbacks only proves it agrees with itself.
    from blueprints.mobile import socket_io

    return socket_io._get_dash_data(read_settings(), read_pellet_db())


def test_payload_carries_warnings_and_their_high_water_mark(ds):
    write_warning("hopper low")
    data = _dash_data()
    assert data["warnings"] == ["hopper low"]
    assert isinstance(data["warningsMaxId"], int)


def test_payload_max_id_is_none_when_there_are_no_warnings(ds):
    data = _dash_data()
    assert data["warnings"] == []
    assert data["warningsMaxId"] is None


def test_payload_read_is_non_destructive(ds):
    # The poll repeats; a consuming read would hand a warning to exactly one
    # payload and lose it for every client that reconnects afterwards.
    write_warning("hopper low")
    _dash_data()
    assert _dash_data()["warnings"] == ["hopper low"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socket_warnings_payload.py -v`
Expected: FAIL with `KeyError: 'warningsMaxId'`

`_get_dash_data(settings, pelletdb)` is verified — that is exactly how `socket_io` calls it internally (lines 213, 260, 350). Do not change the production signature to suit the test.

- [ ] **Step 3: Wire the snapshot into the payload**

In `blueprints/mobile/socket_io.py`: change the import on line ~45 from `read_warnings` to `read_warnings_snapshot`; replace line ~271 `warnings = read_warnings()` with:

```python
    warnings_snapshot = read_warnings_snapshot()
```

In the `dash_data` dict, replace `"warnings": warnings,` with:

```python
        "warnings": warnings_snapshot["warnings"],
        # High-water mark for the dismiss control: the client posts it back to
        # clear exactly the warnings it displayed (blueprints/api dismiss_warnings).
        "warningsMaxId": warnings_snapshot["max_id"],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socket_warnings_payload.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format blueprints/mobile/socket_io.py tests/web/test_socket_warnings_payload.py
jj describe -m "feat(socket): publish warningsMaxId beside the warnings

The dismiss control needs to name what it is clearing. Shipping the snapshot's
high-water mark with the strings lets the client post back exactly the range it
displayed."
```

---

### Task 4: `POST /api/dismiss_warnings`

**Files:**
- Modify: `blueprints/api/routes.py`
- Test: `tests/web/test_api_dismiss_warnings.py`

**Interfaces:**
- Consumes: `clear_warnings_through(max_id)` (Task 2).
- Produces: `POST /api/dismiss_warnings` with body `{"through_id": int}`, called by Task 6's `dismissWarnings`.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_dismiss_warnings.py`:

```python
import pytest

from app import app as flask_app
from common.datastore_accessors import read_warnings_snapshot, write_warning


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_dismiss_clears_the_warnings_through_the_given_id(client):
    write_warning("hopper low")
    snap = read_warnings_snapshot()
    r = client.post("/api/dismiss_warnings", json={"through_id": snap["max_id"]})
    assert r.status_code == 200
    assert read_warnings_snapshot()["warnings"] == []


def test_dismiss_keeps_a_warning_written_after_the_snapshot(client):
    write_warning("seen")
    snap = read_warnings_snapshot()
    write_warning("written after the snapshot")
    client.post("/api/dismiss_warnings", json={"through_id": snap["max_id"]})
    assert read_warnings_snapshot()["warnings"] == ["written after the snapshot"]


def test_dismiss_rejects_a_non_integer_through_id(client):
    write_warning("hopper low")
    r = client.post("/api/dismiss_warnings", json={"through_id": "not-an-int"})
    assert r.status_code == 400
    # The warning must survive a rejected request.
    assert read_warnings_snapshot()["warnings"] == ["hopper low"]


def test_dismiss_rejects_a_boolean_through_id(client):
    # bool is an int subclass in Python; True must not be accepted as id 1.
    r = client.post("/api/dismiss_warnings", json={"through_id": True})
    assert r.status_code == 400


def test_dismiss_rejects_a_missing_through_id(client):
    r = client.post("/api/dismiss_warnings", json={"nope": 1})
    assert r.status_code == 400


def test_dismiss_is_idempotent(client):
    write_warning("hopper low")
    max_id = read_warnings_snapshot()["max_id"]
    assert client.post("/api/dismiss_warnings", json={"through_id": max_id}).status_code == 200
    assert client.post("/api/dismiss_warnings", json={"through_id": max_id}).status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_dismiss_warnings.py -v`
Expected: FAIL — 404 (`"Received POST request no valid action."`) instead of 200.

- [ ] **Step 3: Add the handler**

In `blueprints/api/routes.py`, add `clear_warnings_through` to the existing `common.datastore_accessors` import block, then define the handler immediately above the `_API_POST_ACTIONS` dict (line ~445):

```python
def _api_post_dismiss_warnings(settings, request_json):
    """Clear the warnings the client was showing, and only those.

    The client posts back the high-water mark it received with the banner
    (socket_dash_data's warningsMaxId), so a warning written between that
    payload and the click keeps a larger id and survives the clear.
    """
    through_id = request_json.get("through_id")
    # bool is an int subclass, so it has to be excluded explicitly or True
    # would silently clear id 1.
    if isinstance(through_id, bool) or not isinstance(through_id, int):
        return jsonify(api_response("ERROR", "through_id must be an integer.")), 400
    clear_warnings_through(through_id)
    return jsonify(api_response("OK", "Warnings dismissed.")), 200
```

And register it in `_API_POST_ACTIONS`:

```python
    "dismiss_warnings": _api_post_dismiss_warnings,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_dismiss_warnings.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format blueprints/api/routes.py tests/web/test_api_dismiss_warnings.py
jj describe -m "feat(api): POST /api/dismiss_warnings clears through a high-water mark

Takes the id the client received with its banner and clears only through it, so
a warning raised between the payload and the click is not swept away unread. A
non-integer through_id is refused rather than coerced."
```

---

### Task 5: React type + API client

**Files:**
- Modify: `web-react/src/helpers/types.ts`
- Create: `web-react/src/helpers/shell/warningsApi.ts`

**Interfaces:**
- Consumes: the `warningsMaxId` payload key (Task 3) and `POST /api/dismiss_warnings` (Task 4).
- Produces: `warningsMaxId: number | null` on the live-state type, and `dismissWarnings(throughId: number): Promise<boolean>` — used by Task 6's `Banners`.

- [ ] **Step 1: Add the payload field to the type**

In `web-react/src/helpers/types.ts`, beside `warnings: string[];` (line ~46) add:

```ts
  /** High-water mark of the warnings above; null when there are none. Posted
   *  back to dismiss exactly the warnings this payload carried. */
  warningsMaxId: number | null;
```

- [ ] **Step 2: Write the failing client test**

Create `web-react/src/helpers/shell/warningsApi.test.ts`:

```ts
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { dismissWarnings } from "./warningsApi";

afterEach(() => rs.unstubAllGlobals());

describe("dismissWarnings", () => {
  // Stub fetch with rs.fn() + rs.stubGlobal (the idiom in
  // helpers/notify/wledApi.test.ts), NOT a namespace spy.
  //
  // Assert THREE things, because the first two were originally missed and a
  // reviewer's mutation testing caught it: the URL is exactly
  // "/api/dismiss_warnings", the method is exactly "POST", and the body is
  // {through_id: 7}. Without the first two, a typo'd path or a GET still
  // passes -- and its failure is silent (404/405 -> res.ok false -> resolves
  // false -> the banner simply never dismisses, with no error surfaced).
  it("posts the high-water mark to the right endpoint and resolves true on success");
  it("resolves false on a refusal rather than throwing");
  it("resolves false when the request fails outright");
});
```

**Shipped version:** `web-react/src/helpers/shell/warningsApi.test.ts` — read it rather than reconstructing from this sketch; it is the mutation-verified form.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web-react && bun run test warningsApi`
Expected: FAIL — cannot resolve `./warningsApi`. (Use `bun run test`, never `bun test`.)

- [ ] **Step 4: Write the client**

Create `web-react/src/helpers/shell/warningsApi.ts`:

```ts
// Client for the warnings dismiss endpoint.
//
// Modeled on helpers/update/updateApi.ts: a refusal resolves to false rather
// than throwing, because the caller keeps the banner up and lets the user retry
// instead of catching an escape.

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Clear the warnings up to and including `throughId` -- the high-water mark
 *  that arrived with the banner being dismissed. Resolves true when the server
 *  confirms; false on any refusal or transport failure. */
export async function dismissWarnings(throughId: number): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/api/dismiss_warnings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ through_id: throughId }),
    });
    const body = (await res.json().catch(() => ({}))) as { result?: string };
    return res.ok && body.result === "OK";
  } catch {
    return false;
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web-react && bun run test warningsApi`
Expected: PASS (3 tests)

- [ ] **Step 6: Fix the fixture the new required field breaks**

A required field on the live-state type breaks every full-literal fixture. Run `bun run typecheck` and add `warningsMaxId: null` to each literal it flags (`src/helpers/fixture.ts` and any test that builds a whole payload). Do not widen the type to avoid the work.

- [ ] **Step 7: Gate and commit**

```bash
cd web-react && bun run lint && bun run typecheck && bun run test
jj describe -m "feat(web-react): warnings dismiss client and payload field

Types the high-water mark the socket now publishes and posts it back to clear
the banner. A refusal resolves false so the caller can leave the banner up."
```

---

### Task 6: Dismiss control in `Banners`

**Files:**
- Modify: `web-react/src/components/shell/Banners.tsx`
- Modify: `web-react/src/components/shell/AppShell.tsx`
- Modify: `web-react/src/components/shell/shell.css`
- Test: `web-react/src/components/shell/Banners.test.tsx`

**Interfaces:**
- Consumes: `dismissWarnings` and `warningsMaxId` (Task 5).
- Produces: the finished feature. Nothing depends on it.

- [ ] **Step 1: Write the failing tests**

Add to `web-react/src/components/shell/Banners.test.tsx` (keep every existing test):

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, rs } from "@rstest/core";
import { Banners } from "./Banners";
import * as warningsApi from "../../helpers/shell/warningsApi";

// Mock the module with rs.mock(path, factory) -- the idiom used by
// components/admin/SystemUpdateCard.test.tsx. Namespace-import spying has no
// precedent here.
afterEach(() => rs.resetAllMocks());

it("shows no dismiss control when there are no warnings", () => {
  render(<Banners errors={["boom"]} warnings={[]} warningsMaxId={null} criticalError={false} />);
  expect(screen.queryByRole("button", { name: /dismiss warnings/i })).toBeNull();
});

it("posts the high-water mark and hides the warnings on dismiss");
it("keeps the warnings up when the dismiss is refused");
it("shows a newer warning that arrives after a dismiss");
it("still renders errors after warnings are dismissed");
it("offers no dismiss control when a max id arrives with no warnings");
```

Behaviors each must pin, and the traps found while pinning them:

- **Dismiss** posts exactly `warningsMaxId` (not a recomputed or off-by-one value) and hides the group.
- **Refusal keeps the banner up.** This one was VACUOUS on the first attempt:
  `await waitFor(() => expect(screen.getByText(...)).toBeTruthy())` is satisfied by
  `waitFor`'s first synchronous check, so it asserts the PRE-click DOM and passes
  even against `await dismissWarnings(id); setDismissedThroughId(id);` — i.e. code
  that ignores the server's answer. Wait for the mock to have been called, THEN
  flush the continuation inside `act`, THEN assert.
- **A newer warning reappears** after a dismiss (higher mark). This is the test that
  proves the design: it fails against a boolean `dismissed` flag, which is exactly
  the bug the high-water mark exists to prevent.
- **Errors always render**, dismissed or not.
- **No dismiss button when a mark arrives with an empty warnings list** — you cannot
  offer to clear rows nobody saw.

**Shipped version:** `web-react/src/components/shell/Banners.test.tsx` — read it rather than reconstructing from this sketch; every one of these was mutation-verified (each mutant kills exactly one test).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web-react && bun run test Banners`
Expected: FAIL — no dismiss button; `warningsMaxId` is not a prop.

- [ ] **Step 3: Implement the control**

Rewrite `web-react/src/components/shell/Banners.tsx`:

```tsx
import { useState } from "react";
import { dismissWarnings } from "../../helpers/shell/warningsApi";
import "./shell.css";

// Errors/warnings/critical strip. Source: dash.errors / dash.warnings /
// dash.criticalError (socket_dash_data).
//
// Part of the shell rather than the dashboard because Flask renders these
// alerts on every page (templates/base.html), not only on the controller view.
//
// Warnings are dismissable and errors are not: Flask cleared warnings when a
// human rendered the dashboard, while errors clear only when the devices are
// rebuilt. Dismissal is keyed on warningsMaxId, so warnings raised after the
// dismissed payload reappear instead of being hidden by a stale click.
export function Banners({
  errors,
  warnings,
  warningsMaxId,
  criticalError,
}: {
  errors: string[];
  warnings: string[];
  warningsMaxId: number | null;
  criticalError: boolean;
}) {
  // Ids start at 1, so 0 means "nothing dismissed yet".
  const [dismissedThroughId, setDismissedThroughId] = useState(0);
  const errorLevel: "critical" | "error" = criticalError ? "critical" : "error";
  const showWarnings = warningsMaxId !== null && warningsMaxId > dismissedThroughId;
  const items: { t: string; level: "critical" | "error" | "warning" }[] = [
    ...errors.map((t) => ({ t, level: errorLevel })),
    ...(showWarnings ? warnings.map((t) => ({ t, level: "warning" as const })) : []),
  ];
  if (items.length === 0) return null;

  const onDismiss = async () => {
    if (warningsMaxId === null) return;
    // Only record the dismissal once the server confirms; otherwise the banner
    // stays up and the user can try again.
    if (await dismissWarnings(warningsMaxId)) setDismissedThroughId(warningsMaxId);
  };

  return (
    <div className="pf-banners">
      {items.map((it, i) => (
        <div key={i} className={`pf-banner pf-banner--${it.level}`}>
          {it.t}
        </div>
      ))}
      {showWarnings ? (
        <button type="button" className="pf-banner-dismiss" onClick={onDismiss} aria-label="Dismiss warnings">
          Dismiss warnings
        </button>
      ) : null}
    </div>
  );
}
```

In `AppShell.tsx`, pass the new prop (line ~40):

```tsx
      <Banners
        errors={live.errors ?? []}
        warnings={live.warnings ?? []}
        warningsMaxId={live.warningsMaxId ?? null}
        criticalError={live.criticalError}
      />
```

- [ ] **Step 4: Style the control**

In `web-react/src/components/shell/shell.css`, add beside the existing `.pf-banner` rules — match the surrounding file's token usage and spacing conventions rather than inventing new values:

```css
.pf-banner-dismiss {
  align-self: flex-end;
  background: none;
  border: 1px solid currentColor;
  border-radius: 4px;
  color: inherit;
  cursor: pointer;
  font: inherit;
  padding: 0.15rem 0.6rem;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd web-react && bun run test shell`
Expected: PASS — the 5 new tests plus every pre-existing `Banners`/`AppShell` test.

- [ ] **Step 6: Full gate**

```bash
cd web-react && bun run lint && bun run typecheck && bun run test && bun run build
```
Expected: all green. Fix any `AppShell.test.tsx` fixture that the new prop breaks by supplying `warningsMaxId`, not by loosening the type.

- [ ] **Step 7: Commit**

```bash
jj describe -m "feat(web-react): dismiss control for warning banners

Clears the warnings the user was shown by posting back their high-water mark.
Keyed on that mark rather than a boolean, so a warning raised after the dismissed
payload still reaches the user. Errors stay undismissable, matching Flask."
```

---

### Task 7: Full-suite gate and backlog

**Files:**
- Modify: `docs/superpowers/backlogs/react-migration-backlog.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Run the whole Python suite**

Run each directory in its own session — `tests/web` and `tests/unit` together leak an asyncio event loop across directories (a known pre-existing interaction, not caused by this slice):

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web -q
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit -q
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/characterization -q
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ui -q
```
Expected: all green. `tests/ui` must stay at its full pass count — it renders through `static/font`, which a recent regression had deleted.

- [ ] **Step 2: Run the web-react gate**

```bash
cd web-react && bun run lint && bun run typecheck && bun run test && bun run build
```
Expected: all green.

- [ ] **Step 3: Update the backlog**

In `docs/superpowers/backlogs/react-migration-backlog.md`, find the "Deferred by the Flask-retirement pass" block and replace the "Warnings never auto-clear in React (behavioral gap)" bullet with a SHIPPED note naming the slice, the endpoint (`POST /api/dismiss_warnings`), the mechanism (high-water-mark clear, so a warning raised after the displayed payload is never lost), and the fact that `read_warnings`/`drain_warnings` and the Valkey-era warnings oracle scenario were retired with it. Leave the separate pre-existing e2e baseline-drift bullet alone.

- [ ] **Step 4: Commit**

```bash
jj describe -m "docs(backlog): warnings dismiss shipped

Records the high-water-mark clear and the retirement of the read-and-burn
accessors and their frozen Valkey oracle scenario."
```

---

## Parallelization

Tasks 1 → 2 → 3 and 1 → 2 → 4 are a hard chain: each consumes the previous task's new function. Task 3 and Task 4 are independent of each other once Task 2 lands and **may run concurrently** — they touch disjoint files (`socket_io.py` vs `blueprints/api/routes.py`) and disjoint tests. Task 5 needs Task 3's payload key and Task 4's endpoint to exist; Task 6 needs Task 5. Task 7 is last.

Concurrency requires **isolated jj workspaces** — disjoint file lists alone are not enough, because a shared working copy snapshots every agent's edits into one commit. Given only Tasks 3 and 4 can overlap, and each is small, running the chain serially in one workspace is the recommended default; the parallel option is not worth the workspace setup here.

```
1 ──▶ 2 ──┬──▶ 3 ──┐
          └──▶ 4 ──┴──▶ 5 ──▶ 6 ──▶ 7
```

## Self-Review

**Spec coverage:** §1 queue primitives → Task 1. §2 accessors + both deletions → Task 2. §3 socket payload → Task 3. §4 endpoint → Task 4. §5 React (type, client, Banners, AppShell, css) → Tasks 5-6. §6 test consumers that move rather than die → Task 2 Step 1. Oracle scenario/fixture deletion → Task 2 Step 4. Testing section → distributed across every task, with the lossless-race property asserted in Tasks 1, 4, and 6.

**Placeholder scan:** no TBD/TODO; every code step carries the actual code; every test step names the exact command and expected result.

**Type consistency:** `read_warnings_snapshot()` returns `{"warnings", "max_id"}` in Tasks 2, 3 and the tests; the payload key is `warningsMaxId` in Tasks 3, 5, 6; `dismissWarnings(throughId: number) => Promise<boolean>` in Tasks 5 and 6; the body key is `through_id` in Tasks 4 and 5.

**Signatures verified against live code, not recalled:** `_get_dash_data(settings, pelletdb)` (matching `socket_io`'s own three call sites), `read_pellet_db()`, `read_settings()`, `api_response(result, message=None, data=None)`, and the `handler(settings, request_json)` contract every `_API_POST_ACTIONS` entry obeys.
