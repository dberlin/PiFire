# React Events + Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Flask's `/events` and `/logs` pages with one React route at
`/events` carrying two tabs, built on `@melloware/react-logviewer`, served by a
Range-capable plain-text endpoint that stitches each rotated log family in
chronological order.

**Architecture:** The backend gains one read endpoint,
`GET /api/admin/logs/view?log=<family>`, which concatenates a log family
oldest-first into a `BytesIO` and serves it via
`send_file(conditional=True)` so HTTP Range works. The client fetches the whole
family once, then tails it by polling `Range: bytes=<offset>-` and pushing the
delta into `LazyLog.appendLines()`. No pagination exists on either side —
`LazyLog` virtualizes and searches client-side.

**Tech Stack:** Flask, SQLite (`common/datastore.py`), React 19.2.8,
`@melloware/react-logviewer` 6.5.5, react-router v8, rstest + Testing Library,
Playwright, bun.

**Spec:** `docs/superpowers/specs/2026-07-28-react-events-logs.md`

## Global Constraints

- **Never run `bun run test:e2e`** (the whole `app` project). `roundtrip.spec.ts`
  puts the grill into Startup mode and `settings.spec.ts` flushes the history
  store. Use `bun run test:e2e:fidelity`, or one named spec with `--project=app`.
- **Neutralize `os.system` / `subprocess` / `sudo` / `reboot` / `shutdown`
  before running any test that can reach admin paths.** An `is_real_hardware()`
  flag is NOT enough — it defaults to True. This repo has really rebooted the
  developer's machine three times.
- Mocks must patch the **importing module's** globals, not the origin module.
- Toolchain is **bun**, never npm. Commit `bun.lock`.
- Commit with `jj describe --stdin` + a quoted heredoc (no `-F` flag), never
  `git commit`. Run `jj new` **before** the first Write of each task.
- Run `.venv/bin/ruff format` on changed Python before committing. Never `uvx ruff`.
- Python tests: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
- web-react gates: `bun run typecheck`, `bun run typecheck:e2e`, `bun run lint`,
  `bun run test`.
- Retention defaults, confirmed 2026-07-28: `LOG_RETENTION_ROWS = 20_000`,
  `PRUNE_INTERVAL = 1_000`.
- Log family member order is **oldest first**: `x.log.3`, `x.log.2`, `x.log.1`,
  `x.log`. `RotatingFileHandler` shifts suffixes upward, so the highest number
  is the oldest.
- Offsets are **bytes**, not characters. Any JS length used against a Range
  offset must go through `new TextEncoder().encode(s).length`.
- Never hand-edit `web-react/tests/e2e/baselines/*.json`.
- Do not edit `blueprints/events/` or `blueprints/logs/`. They stay live until
  the general Flask retirement pass.

## File Structure

**Backend**

| File | Responsibility |
|---|---|
| `blueprints/api_admin/admin_api.py` | `list_log_families`, `stitch_family`; family-aware `delete_logs`, `build_log_archive`, `clear_events_log` |
| `blueprints/api_admin/routes.py` | the `/logs/view` route |
| `common/datastore.py` | `prune_log(name, keep)` |
| `common/sqlite_log_handler.py` | prune counter |
| `common/common.py` | `LOG_DIR`, `log_path()`, `reset_loggers()` |
| `tests/conftest.py` | autouse log isolation |

**Frontend**

| File | Responsibility |
|---|---|
| `web-react/src/helpers/logs/logTypes.ts` | `LogFamily`, `LogDelta` |
| `web-react/src/helpers/logs/logsApi.ts` | typed client, URL builders, `fetchLogDelta` |
| `web-react/src/helpers/logs/useLogTail.ts` | poll loop, offset + total tracking |
| `web-react/src/components/logs/LogViewer.tsx` | `LazyLog` wrapper, `formatPart` |
| `web-react/src/components/logs/EventsPage.tsx` | route shell, tabs |
| `web-react/src/components/logs/LogFilesTab.tsx` | family picker + downloads |
| `web-react/src/components/logs/logs.css` | styles |

## Parallelization

Tasks 1–7 (backend) and Tasks 8–10 (frontend data layer) touch disjoint trees
and may run concurrently **only in isolated jj workspaces** — disjoint file sets
alone are not enough, because a shared working copy means one agent's snapshot
captures another's edits.

- **Task 1 must land before Tasks 2–5** (they all call `list_log_families`).
- **Task 8 must land before Tasks 9–12.**
- Tasks 6 and 7 are independent of everything else and of each other.
- Tasks 11–14 are strictly serial.

Recommended: run Slice A serially, then Slice B serially. The dependency chain
is deep enough that parallelism buys little.

---

# SLICE A — Backend

### Task 1: Log families

**Files:**
- Modify: `blueprints/api_admin/admin_api.py`
- Test: `tests/unit/api_admin/test_log_families.py` (create)

**Interfaces:**
- Produces: `list_log_families(folder=None) -> dict[str, list[str]]` mapping a
  family stem to its member filenames **oldest first**. Excludes any name that
  is not `<stem>.log` or `<stem>.log.<n>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/api_admin/test_log_families.py
import pytest
from blueprints.api_admin import admin_api


@pytest.fixture
def logdir(tmp_path):
    for name in (
        "events.log", "events.log.1", "events.log.2",
        "mqtt.log", "logfiles.txt", "notes.md",
    ):
        (tmp_path / name).write_text(f"{name}\n")
    return str(tmp_path)


def test_groups_members_by_stem(logdir):
    families = admin_api.list_log_families(logdir)
    assert set(families) == {"events", "mqtt"}


def test_orders_members_oldest_first(logdir):
    #  RotatingFileHandler shifts suffixes UPWARD on rollover, so the highest
    #  number is the oldest and must be stitched first.
    assert admin_api.list_log_families(logdir)["events"] == [
        "events.log.2", "events.log.1", "events.log",
    ]


def test_excludes_non_log_names(logdir):
    #  logs/logfiles.txt is a supervisord placeholder, not a log.
    flat = [n for members in admin_api.list_log_families(logdir).values() for n in members]
    assert "logfiles.txt" not in flat
    assert "notes.md" not in flat


def test_missing_folder_is_empty_not_an_error(tmp_path):
    assert admin_api.list_log_families(str(tmp_path / "nope")) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/test_log_families.py -q`
Expected: FAIL — `AttributeError: module 'blueprints.api_admin.admin_api' has no attribute 'list_log_families'`

- [ ] **Step 3: Implement**

Add `import re` to the imports in `blueprints/api_admin/admin_api.py`, then add
below `list_logs`:

```python
#: A log family member is `<stem>.log`, or `<stem>.log.<n>` after rotation.
#: Anything else in the folder -- logfiles.txt, stray notes -- is not a log and
#: is not offered.
_LOG_MEMBER = re.compile(r"^(?P<stem>.+)\.log(?:\.(?P<index>\d+))?$")


def list_log_families(folder=None):
    """{stem: [member filenames, OLDEST FIRST]}.

    RotatingFileHandler shifts suffixes upward on rollover -- `x.log` becomes
    `x.log.1`, `x.log.1` becomes `x.log.2` -- so the highest-numbered member is
    the oldest and sorts first. `x.log` itself is index 0 and sorts last.

    `folder` resolves at call time; see list_logs.
    """
    folder = folder or LOG_FOLDER
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    families = {}
    for name in names:
        match = _LOG_MEMBER.match(name)
        if match:
            index = int(match["index"] or 0)
            families.setdefault(match["stem"], []).append((index, name))
    return {
        stem: [name for _, name in sorted(members, key=lambda pair: -pair[0])]
        for stem, members in sorted(families.items())
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/test_log_families.py -q`
Expected: PASS, 4 passed

- [ ] **Step 5: Negative control**

Change `key=lambda pair: -pair[0]` to `key=lambda pair: pair[0]` and re-run.
Expected: `test_orders_members_oldest_first` FAILS. Restore the minus sign.
A green ordering test that cannot go red is proving nothing.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_admin/admin_api.py tests/unit/api_admin/test_log_families.py
jj new -m "feat(api_admin): group log files into rotation families"
```

---

### Task 2: Stitch a family into one byte stream

**Files:**
- Modify: `blueprints/api_admin/admin_api.py`
- Test: `tests/unit/api_admin/test_log_stitch.py` (create)

**Interfaces:**
- Consumes: `list_log_families(folder=None) -> dict[str, list[str]]`
- Produces: `stitch_family(stem, folder=None) -> bytes | None`. `None` means the
  stem is not a known family — the caller turns that into a 404.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/api_admin/test_log_stitch.py
import pytest
from blueprints.api_admin import admin_api


@pytest.fixture
def logdir(tmp_path):
    (tmp_path / "events.log.2").write_text("oldest\n")
    (tmp_path / "events.log.1").write_text("middle\n")
    (tmp_path / "events.log").write_text("newest\n")
    return str(tmp_path)


def test_concatenates_oldest_first(logdir):
    assert admin_api.stitch_family("events", logdir) == b"oldest\nmiddle\nnewest\n"


def test_unknown_stem_is_none(logdir):
    assert admin_api.stitch_family("nosuch", logdir) is None


def test_a_member_missing_its_trailing_newline_does_not_join_two_lines(logdir, tmp_path):
    #  Without this guard the last line of one member and the first of the next
    #  render as a single corrupt line in the viewer.
    (tmp_path / "events.log.1").write_text("middle")
    assert admin_api.stitch_family("events", logdir) == b"oldest\nmiddle\nnewest\n"


def test_a_path_shaped_stem_reads_nothing(tmp_path):
    #  A REAL decoy where `../` resolves: if containment were done by an
    #  os.path.isfile check, a nonexistent target would fake the pass.
    (tmp_path / "secret.log").write_text("SECRET\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.log").write_text("ok\n")
    assert admin_api.stitch_family("../secret", str(logs)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/test_log_stitch.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'stitch_family'`

- [ ] **Step 3: Implement**

Add below `list_log_families`:

```python
def stitch_family(stem, folder=None):
    """One family as a single byte stream, oldest first, or None if unknown.

    The stem is looked up in list_log_families rather than joined onto a path,
    so there is no client-supplied path component anywhere in this function.
    Flask's logs page concatenated a request field onto the logs folder in two
    places; that is the hole this shape closes by construction.
    """
    folder = folder or LOG_FOLDER
    members = list_log_families(folder).get(stem)
    if members is None:
        return None
    chunks = []
    for name in members:
        try:
            with open(os.path.join(folder, name), "rb") as handle:
                chunk = handle.read()
        except OSError:
            continue
        #  A member truncated without its final newline would otherwise weld its
        #  last line onto the next member's first.
        if chunk and not chunk.endswith(b"\n"):
            chunk += b"\n"
        chunks.append(chunk)
    return b"".join(chunks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/test_log_stitch.py -q`
Expected: PASS, 4 passed

- [ ] **Step 5: Prove the traversal test is a real control**

Temporarily replace the `members is None` guard with
`members = members or [stem + ".log"]` and re-run.
Expected: `test_a_path_shaped_stem_reads_nothing` FAILS. Restore the guard.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_admin/admin_api.py tests/unit/api_admin/test_log_stitch.py
jj new -m "feat(api_admin): stitch a log family into one ordered byte stream"
```

---

### Task 3: The view endpoint, with Range

**Files:**
- Modify: `blueprints/api_admin/routes.py`
- Test: `tests/web/test_api_admin_logs_view.py` (create)

**Interfaces:**
- Consumes: `stitch_family(stem, folder=None) -> bytes | None`
- Produces: `GET /api/admin/logs/view?log=<stem>[&download=1]` →
  `200 text/plain` with `Accept-Ranges: bytes`; `206` + `Content-Range` on a
  `Range:` request; `416` + `Content-Range: bytes */<size>` past the end;
  `404` with message `not_found` for an unknown stem.

- [ ] **Step 1: Write the failing test**

Use the app fixture already used by `tests/web/`; read `tests/web/conftest.py`
for its name before writing, and follow the pattern of the existing
`tests/web/` admin tests.

```python
# tests/web/test_api_admin_logs_view.py
import pytest
from blueprints.api_admin import admin_api


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    (tmp_path / "events.log.1").write_text("old\n")
    (tmp_path / "events.log").write_text("new\n")
    monkeypatch.setattr(admin_api, "LOG_FOLDER", str(tmp_path))
    return tmp_path


def test_serves_the_whole_family_as_plain_text(client, logdir):
    r = client.get("/api/admin/logs/view?log=events")
    assert r.status_code == 200
    assert r.data == b"old\nnew\n"
    assert r.mimetype == "text/plain"
    assert r.headers["Accept-Ranges"] == "bytes"


def test_a_range_returns_only_the_tail(client, logdir):
    r = client.get("/api/admin/logs/view?log=events", headers={"Range": "bytes=4-"})
    assert r.status_code == 206
    assert r.data == b"new\n"
    assert r.headers["Content-Range"] == "bytes 4-7/8"


def test_past_the_end_reports_the_total_size(client, logdir):
    #  The client uses this total to detect that the family rotated: if it is
    #  below the cursor the client holds, the log rolled and it refetches.
    r = client.get("/api/admin/logs/view?log=events", headers={"Range": "bytes=9999-"})
    assert r.status_code == 416
    assert r.headers["Content-Range"] == "bytes */8"


def test_unknown_family_is_404(client, logdir):
    r = client.get("/api/admin/logs/view?log=nosuch")
    assert r.status_code == 404
    assert r.get_json()["message"] == "not_found"


def test_download_flag_sets_an_attachment_name(client, logdir):
    r = client.get("/api/admin/logs/view?log=events&download=1")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    assert "events.log" in r.headers["Content-Disposition"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_admin_logs_view.py -q`
Expected: FAIL — 404 from Flask's routing (no such rule) on every test

- [ ] **Step 3: Implement**

Add `import io` to `blueprints/api_admin/routes.py` if absent, then add beside
the other logs routes:

```python
@api_admin_bp.route("/logs/view", methods=["GET"])
def admin_logs_view():
    """One log family as plain text, with Range support.

    conditional=True is what makes send_file advertise Accept-Ranges and answer
    a Range: header with 206 -- verified to work for a synthesized BytesIO, not
    only for a real path, and to emit `Content-Range: bytes */<size>` on 416.
    That 416 header is load-bearing: it is how the client learns the family
    rotated out from under its cursor.
    """
    stem = request.args.get("log", "")
    payload = admin_api.stitch_family(stem)
    if payload is None:
        return jsonify(api_response("ERROR", "not_found", {"log": stem})), 404
    download = request.args.get("download") == "1"
    return send_file(
        io.BytesIO(payload),
        mimetype="text/plain",
        conditional=True,
        as_attachment=download,
        download_name=f"{stem}.log",
        max_age=0,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_admin_logs_view.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Negative control**

Change `conditional=True` to `conditional=False` and re-run.
Expected: `test_a_range_returns_only_the_tail` and
`test_past_the_end_reports_the_total_size` both FAIL. Restore it.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_admin/routes.py tests/web/test_api_admin_logs_view.py
jj new -m "feat(api_admin): GET /api/admin/logs/view with byte-range support"
```

---

### Task 4: Family-aware delete and archive

**Files:**
- Modify: `blueprints/api_admin/admin_api.py` (`delete_logs`, `build_log_archive`)
- Test: `tests/unit/api_admin/test_log_families.py` (extend)

**Interfaces:**
- Consumes: `list_log_families(folder=None)`
- Produces: `delete_logs(folder=None) -> list[str]` — every member of every
  family, sorted. `build_log_archive(folder=None) -> str` — a zip containing
  every member.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/api_admin/test_log_families.py`:

```python
import zipfile


def test_delete_removes_rotated_members_too(logdir, tmp_path):
    #  Before this, delete_logs filtered on endswith(".log") and left every
    #  rotated file behind -- so "Delete All" did not delete what the viewer shows.
    removed = admin_api.delete_logs(logdir)
    assert "events.log.2" in removed
    assert "events.log.1" in removed
    assert sorted(p.name for p in tmp_path.iterdir()) == ["logfiles.txt", "notes.md"]


def test_archive_contains_rotated_members_too(logdir):
    archive = admin_api.build_log_archive(logdir)
    with zipfile.ZipFile(archive) as zf:
        assert "events.log.2" in zf.namelist()
        assert "logfiles.txt" not in zf.namelist()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/test_log_families.py -q`
Expected: FAIL — both new tests; `events.log.2` is absent from `removed` and
from the archive

- [ ] **Step 3: Implement**

Replace the body of `delete_logs`:

```python
def delete_logs(folder=None):
    """Delete every member of every log family, reporting what went.

    Flask runs `os.system("rm logs/*.log")` inside a bare `except:`, so a
    failure is indistinguishable from success. This enumerates server-side and
    names what it removed. Rotated members are included: leaving them behind
    meant the viewer still showed content after a "Delete All".
    """
    folder = folder or LOG_FOLDER
    removed = []
    for members in list_log_families(folder).values():
        for name in members:
            try:
                os.remove(os.path.join(folder, name))
                removed.append(name)
            except OSError:
                continue
    return sorted(removed)
```

In `build_log_archive`, replace the loop body:

```python
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for members in list_log_families(folder).values():
            for name in members:
                zf.write(os.path.join(folder, name), arcname=name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/ -q`
Expected: PASS, all tests

- [ ] **Step 5: Confirm the shipped admin page still passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/ tests/unit/api_admin/ -q`
Expected: PASS. `list_logs()` is deliberately untouched, so the LogsCard shipped
on 2026-07-27 keeps its exact contract.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_admin/admin_api.py tests/unit/api_admin/test_log_families.py
jj new -m "fix(api_admin): delete and archive every rotated log member"
```

---

### Task 5: Clearing events clears both stores

**Files:**
- Modify: `blueprints/api_admin/admin_api.py` (`clear_events_log`)
- Test: `tests/unit/api_admin/test_clear_events.py` (create)

**Interfaces:**
- Consumes: `list_log_families(folder=None)`, `common.datastore.clear_log(name)`
- Produces: `clear_events_log(folder=None) -> True` — **return type unchanged**,
  because `blueprints/api_admin/routes.py` dispatches it as
  `"clear_events": lambda: admin_api.clear_events_log()` and the shipped
  MaintenanceCard is built against that response shape.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/api_admin/test_clear_events.py
import pytest
from blueprints.api_admin import admin_api


@pytest.fixture
def logdir(tmp_path):
    (tmp_path / "events.log").write_text("a\n")
    (tmp_path / "events.log.1").write_text("b\n")
    (tmp_path / "mqtt.log").write_text("c\n")
    return tmp_path


def test_clears_the_whole_events_family(logdir):
    admin_api.clear_events_log(str(logdir))
    assert sorted(p.name for p in logdir.iterdir()) == ["mqtt.log"]


def test_also_clears_the_database_rows(logdir, monkeypatch):
    #  read_events_records() reads the FILE while flush_events_records() cleared
    #  the DATABASE, so clearing events wiped rows nothing reads and left the
    #  file everything reads. One action must empty both stores.
    cleared = []
    monkeypatch.setattr(admin_api.datastore, "clear_log", lambda name: cleared.append(name))
    admin_api.clear_events_log(str(logdir))
    assert cleared == ["events"]


def test_still_returns_true(logdir):
    #  The maintenance dispatch and the shipped MaintenanceCard depend on this.
    assert admin_api.clear_events_log(str(logdir)) is True


def test_missing_files_are_success(tmp_path):
    assert admin_api.clear_events_log(str(tmp_path)) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/test_clear_events.py -q`
Expected: FAIL — `test_clears_the_whole_events_family` (leaves `events.log.1`)
and `test_also_clears_the_database_rows` (`AttributeError: ... has no attribute
'datastore'`)

- [ ] **Step 3: Implement**

Add `from common import datastore` to the imports in
`blueprints/api_admin/admin_api.py`, then replace `clear_events_log`:

```python
def clear_events_log(folder=None):
    """Empty the events log in BOTH stores.

    Flask runs `os.system("rm ./logs/events.log")` for this, which misses every
    rotated member. Worse, common.common.flush_events_records() cleared only the
    DATABASE while read_events_records() reads the FILE, so clearing events
    deleted rows nothing reads and left the file everything reads. Every logger
    built by create_logger writes to both sinks, so clearing must too.

    `folder` resolves at call time; see list_logs.
    """
    folder = folder or LOG_FOLDER
    for name in list_log_families(folder).get("events", []):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            continue
    datastore.clear_log("events")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/api_admin/test_clear_events.py -q`
Expected: PASS, 4 passed

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_admin/admin_api.py tests/unit/api_admin/test_clear_events.py
jj new -m "fix(api_admin): clearing events empties the family and the database"
```

---

### Task 6: Database log retention

**Files:**
- Modify: `common/datastore.py`, `common/sqlite_log_handler.py`
- Test: `tests/unit/common/test_log_retention.py` (create)

**Interfaces:**
- Produces: `datastore.prune_log(name, keep)` — deletes all but the newest
  `keep` rows for that logger. `datastore.LOG_RETENTION_ROWS = 20_000`,
  `datastore.PRUNE_INTERVAL = 1_000`, `datastore._logs_retention_ddl()`,
  `datastore._ensure_logs_retention(conn)`.

> **REVISED DURING EXECUTION, 2026-07-28.** As written, this task put an emit
> counter on `SqliteLogHandler`. It shipped as a SQLite **trigger** instead, at
> the user's suggestion. The counter is per-handler-instance and per-process —
> PiFire runs `control.py`, gunicorn and `board-config.py` separately, each with
> its own count — and Task 7's `reset_loggers()` detaches handlers, which zeroes
> every counter. A process that restarts regularly would prune late or never,
> silently. A trigger cannot be bypassed and covers any writer that inserts into
> `logs`, handler or not. The constants moved from `sqlite_log_handler` to
> `datastore`, since the trigger DDL owns them, and the counter was deleted
> rather than left alongside its replacement.
>
> Two things this turned up that the original design would not have:
>
> 1. **The `OFFSET` boundary was off by one.** `OFFSET keep` names the
>    `keep + 1`-th newest row, so `id < cutoff` keeps it and leaves `keep + 1`
>    rows. The comparison must be `id <=`. Caught only because the test asserts
>    exact row contents rather than a count.
> 2. **Refreshing the trigger is a schema WRITE.** Running `DROP TRIGGER` +
>    `CREATE TRIGGER` unconditionally in `_ensure_schema` writes to
>    `sqlite_master` on *every* connection and takes a write lock, which made
>    `test_datastore_concurrency.py::test_concurrent_producers_no_loss` flaky
>    (1 failure in 2 combined runs). `_ensure_logs_retention` compares the
>    stored definition first and only rewrites when it differs; the combined
>    suite then passed 5 runs in a row.

- [ ] **Step 1: Write the failing test**

Use the existing test-isolated datastore fixture — read `tests/conftest.py`'s
`ds(tmp_path)` fixture (line 112) and use it rather than touching `pifire.db`.

```python
# tests/unit/common/test_log_retention.py
from common import datastore


def _write(name, count):
    for i in range(count):
        datastore.execute_write(
            "INSERT INTO logs(name, ts, message) VALUES(?,?,?)", (name, i, f"{name}-{i}")
        )


def test_keeps_only_the_newest_rows(ds):
    _write("events", 10)
    datastore.prune_log("events", 4)
    assert datastore.read_log("events") == [f"events-{i}" for i in (9, 8, 7, 6)]


def test_leaves_other_loggers_untouched(ds):
    #  Interleaved, because ids are ONE global AUTOINCREMENT sequence shared by
    #  every logger -- any MAX(id) - keep arithmetic would delete the wrong rows.
    for i in range(10):
        _write("events", 1)
        _write("mqtt", 1)
    datastore.prune_log("events", 2)
    assert len(datastore.read_log("events")) == 2
    assert len(datastore.read_log("mqtt")) == 10


def test_fewer_rows_than_keep_is_a_no_op(ds):
    _write("events", 3)
    datastore.prune_log("events", 100)
    assert len(datastore.read_log("events")) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_log_retention.py -q`
Expected: FAIL — `AttributeError: module 'common.datastore' has no attribute 'prune_log'`

- [ ] **Step 3: Implement**

Add to `common/datastore.py`, below `clear_log`:

```python
def prune_log(name, keep):
    """Drop all but the newest `keep` rows for one logger.

    The cutoff is found by OFFSET into that logger's own rows, walking
    ix_logs_name_id directly. It cannot be MAX(id) - keep arithmetic: `id` is a
    single global AUTOINCREMENT sequence shared by every logger, so wherever two
    loggers interleave that subtraction lands on the wrong row.

    With fewer than `keep` rows the subquery is NULL, `id < NULL` is NULL, and
    nothing is deleted.
    """
    execute_write(
        "DELETE FROM logs WHERE name=? AND id <= "
        "(SELECT id FROM logs WHERE name=? ORDER BY id DESC LIMIT 1 OFFSET ?)",
        (name, name, keep),
    )
```

Add the constants and the trigger DDL to `common/datastore.py` above `SCHEMA`,
and call `_ensure_logs_retention(conn)` from `_ensure_schema` *after*
`conn.executescript(SCHEMA + _queue_ddl())`:

```python
LOG_RETENTION_ROWS = 20_000
PRUNE_INTERVAL = 1_000


def _logs_retention_ddl():
    return f"""CREATE TRIGGER logs_prune AFTER INSERT ON logs
WHEN NEW.id % {PRUNE_INTERVAL} = 0
BEGIN
    DELETE FROM logs
     WHERE name = NEW.name
       AND id <= (SELECT id FROM logs WHERE name = NEW.name
                   ORDER BY id DESC LIMIT 1 OFFSET {LOG_RETENTION_ROWS});
END"""


def _ensure_logs_retention(conn):
    """Install the trigger only when it is missing or stale -- an
    unconditional DROP + CREATE writes to sqlite_master on every connection and
    takes a write lock."""
    desired = _logs_retention_ddl()
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='logs_prune'"
    ).fetchone()
    if row is not None and " ".join(row[0].split()) == " ".join(desired.split()):
        return
    conn.executescript(f"DROP TRIGGER IF EXISTS logs_prune;\n{desired};")
```

`_logs_retention_ddl()` must be called from `_ensure_schema` at runtime, NOT
concatenated into the module-level `SCHEMA` constant — `SCHEMA` is evaluated
once at import, which would freeze the constants and make them unpatchable in
tests.

Leave `common/sqlite_log_handler.py` as it is apart from a docstring line
recording that retention is the trigger's job, not the handler's.

- [ ] **Step 4: Run test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_log_retention.py -q`
Expected: PASS, 3 passed

- [ ] **Step 5: Negative control**

Change the SQL to `... AND id < (SELECT MAX(id) FROM logs) - ?` with params
`(name, keep)` and re-run.
Expected: `test_leaves_other_loggers_untouched` FAILS. Restore the OFFSET form.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format common/datastore.py common/sqlite_log_handler.py tests/unit/common/test_log_retention.py
jj new -m "feat(datastore): bound the logs table with a per-logger retention policy"
```

---

### Task 7: Stop the test suite writing to the real ./logs/

**Files:**
- Modify: `common/common.py` (lines 67–95 `create_logger`, 266/271 `read_events`,
  329 `write_log`), `common/process_mon.py:56,59`, `app.py:171,176`,
  `board-config.py:488`
- Modify: `tests/conftest.py`
- Test: `tests/unit/common/test_log_isolation.py` (create)

**Interfaces:**
- Produces: `common.common.LOG_DIR` (module constant, default `"./logs"`),
  `common.common.log_path(name) -> str`, `common.common.reset_loggers()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/common/test_log_isolation.py
import logging
from common import common as common_mod


def test_log_path_derives_from_the_module_constant(monkeypatch, tmp_path):
    monkeypatch.setattr(common_mod, "LOG_DIR", str(tmp_path))
    assert common_mod.log_path("events.log") == str(tmp_path / "events.log")


def test_reset_loggers_detaches_handlers_so_they_rebuild(monkeypatch, tmp_path):
    #  app.py builds its loggers at IMPORT time, before any fixture can run, so
    #  redirecting LOG_DIR alone would leave those handlers bound to ./logs/.
    monkeypatch.setattr(common_mod, "LOG_DIR", str(tmp_path))
    common_mod.create_logger("isolation-probe", filename=common_mod.log_path("probe.log"))
    assert logging.getLogger("isolation-probe").handlers
    common_mod.reset_loggers()
    assert not logging.getLogger("isolation-probe").handlers


def test_the_suite_writes_no_files_into_the_repo_logs_dir(tmp_path):
    #  The autouse fixture in tests/conftest.py must already have redirected
    #  LOG_DIR by the time any test body runs.
    assert not common_mod.LOG_DIR.rstrip("/").endswith("./logs")
    common_mod.write_log("isolation canary")
    import os
    assert "canary" not in "".join(
        open(os.path.join("./logs", n)).read()
        for n in os.listdir("./logs")
        if n.startswith("events.log")
    ) if os.path.isdir("./logs") else True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_log_isolation.py -q`
Expected: FAIL — `AttributeError: module 'common.common' has no attribute 'LOG_DIR'`

- [ ] **Step 3: Implement the seam in `common/common.py`**

Add near the top of `common/common.py`, after the imports:

```python
#: Every log file path in the tree derives from this. It exists so the test
#: suite can redirect logging wholesale: before this, tests appended to the
#: operator's real ./logs/events.log, which is why that file carried lines like
#: "Admin: Shutdown failed: boom" and diverged from the database.
LOG_DIR = "./logs"

#: Names create_logger has actually built a handler for, so reset_loggers can
#: find them again. logging.getLogger caches by name, so a logger built before
#: LOG_DIR moved keeps its old file handler until it is detached.
_CREATED_LOGGERS = set()


def log_path(name):
    """Absolute-or-relative path to a log file inside LOG_DIR."""
    return os.path.join(LOG_DIR, name)


def reset_loggers():
    """Detach and close every handler create_logger built.

    The next create_logger call for the same name sees `not logger.handlers`
    and rebuilds against the current LOG_DIR.
    """
    for name in sorted(_CREATED_LOGGERS):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    _CREATED_LOGGERS.clear()
```

In `create_logger`, change the default and record the name. The signature's
default becomes `None` so it resolves at call time:

```python
def create_logger(
    name,
    filename=None,
    messageformat="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    maxBytes=1 * 1024 * 1024,  # 1 MB
    backupCount=3,
):
    """Create or Get Existing Logger"""
    filename = filename or log_path("pifire.log")
    logger = logging.getLogger(name)
```

and immediately after `logger.addHandler(sqlite_handler)` inside the
`if not logger.handlers:` block, add:

```python
        _CREATED_LOGGERS.add(name)
```

- [ ] **Step 4: Route the remaining call sites through `log_path`**

Replace every hardcoded log path with `log_path(...)`:

- `common/common.py:266` — `with open(log_path("events.log")) as event_file:`
- `common/common.py:271` — `event_file = open(log_path("events.log"), "w")`
- `common/common.py:329` — `filename=log_path("events.log"),`
- `common/process_mon.py:56` — `filename=log_path(f"{self.process}.log")`
- `common/process_mon.py:59` — `filename=log_path("events.log")`
- `app.py:171` — `filename=log_path("webapp.log")`
- `app.py:176` — `filename=log_path("events.log")`
- `board-config.py:488` — `filename=log_path("pifire.log")`

Add `log_path` to each file's existing `from common.common import ...`.
Leave `blueprints/admin/routes.py` and `blueprints/mobile/socket_io.py` alone —
those are the Flask blueprints scheduled for retirement.

- [ ] **Step 5: Add the autouse fixture to `tests/conftest.py`**

```python
@pytest.fixture(autouse=True, scope="session")
def _isolate_log_files(tmp_path_factory):
    """Point all logging at a temp directory for the whole session.

    Without this the suite appends to the operator's real ./logs/, which is how
    fixture strings like [nonexistent_probe_module_xyz] ended up in the file the
    log viewer shows. reset_loggers() is required as well as the redirect,
    because app.py builds its loggers at import time -- before any fixture runs.
    """
    from common import common as common_mod
    from blueprints.api_admin import admin_api

    log_dir = tmp_path_factory.mktemp("pifire-logs")
    common_mod.LOG_DIR = str(log_dir)
    admin_api.LOG_FOLDER = str(log_dir) + "/"
    common_mod.reset_loggers()
    yield
```

- [ ] **Step 6: Run the new test and the full suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_log_isolation.py -q`
Expected: PASS, 3 passed

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: PASS, at or above the 3352-test baseline. Investigate any test that
asserted against the real `./logs/`; the correct fix is to point it at the
fixture's directory, not to weaken the isolation.

- [ ] **Step 7: Prove the isolation empirically**

```bash
ls -la logs/ > /tmp/logs-before.txt
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
ls -la logs/ > /tmp/logs-after.txt
diff /tmp/logs-before.txt /tmp/logs-after.txt && echo "ISOLATED"
```
Expected: `ISOLATED`. A green suite alone does not prove the suite stopped
writing; only the before/after comparison does.

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format common/common.py common/process_mon.py app.py board-config.py tests/conftest.py tests/unit/common/test_log_isolation.py
jj new -m "fix(tests): stop the suite appending to the real logs directory"
```

---

# SLICE B — Frontend

### Task 8: Dependency and typed client

**Files:**
- Create: `web-react/src/helpers/logs/logTypes.ts`, `web-react/src/helpers/logs/logsApi.ts`
- Modify: `web-react/package.json`, `web-react/bun.lock`
- Test: `web-react/src/helpers/logs/logsApi.test.ts` (create)

**Interfaces:**
- Consumes: `GET /api/admin/logs/view?log=<stem>[&download=1]`
- Produces:
  - `interface LogFamily { stem: string; members: string[]; bytes: number }`
  - `type LogDelta = { kind: "appended" | "rotated"; text: string; nextOffset: number; total: number } | { kind: "unchanged"; nextOffset: number; total: number }`
  - `logViewUrl(stem, baseUrl?) => string`, `logDownloadUrl(stem, baseUrl?) => string`
  - `fetchLogWhole(stem, baseUrl?) => Promise<{ text: string; total: number }>`
  - `fetchLogDelta(stem, offset, lastTotal, baseUrl?) => Promise<LogDelta>`
  - `byteLength(s: string) => number`

- [ ] **Step 1: Install the dependency**

```bash
cd web-react && bun add @melloware/react-logviewer@6.5.5
```
Expected: `package.json` gains the dependency and `bun.lock` updates. The
package is MPL-2.0 with peer `react >=17`; this repo is on React 19.2.8.

- [ ] **Step 2: Write the failing test**

```ts
// web-react/src/helpers/logs/logsApi.test.ts
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { byteLength, fetchLogDelta, logDownloadUrl, logViewUrl } from "./logsApi";

const fetchMock = rs.fn();
beforeEach(() => {
  fetchMock.mockReset();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

const res = (status: number, body: string, headers: Record<string, string> = {}) =>
  new Response(status === 416 ? null : body, { status, headers });

describe("logsApi", () => {
  it("encodes the stem into the view url", () => {
    expect(logViewUrl("events", "")).toBe("/api/admin/logs/view?log=events");
    expect(logViewUrl("a b", "")).toBe("/api/admin/logs/view?log=a%20b");
  });

  it("adds the download flag without a second question mark", () => {
    expect(logDownloadUrl("events", "")).toBe("/api/admin/logs/view?log=events&download=1");
  });

  it("counts BYTES, not characters", () => {
    //  Range offsets are byte offsets. A multi-byte line would desync the
    //  cursor forever if this used String.length.
    expect(byteLength("é")).toBe(2);
    expect(byteLength("abc")).toBe(3);
  });

  it("advances the offset by the delta's byte length", async () => {
    fetchMock.mockResolvedValue(res(206, "new\n", { "Content-Range": "bytes 4-7/8" }));
    const d = await fetchLogDelta("events", 4, 8, "");
    expect(d).toEqual({ kind: "appended", text: "new\n", nextOffset: 8, total: 8 });
  });

  it("reports nothing new on a 416 whose total still covers the cursor", async () => {
    fetchMock.mockResolvedValue(res(416, "", { "Content-Range": "bytes */8" }));
    const d = await fetchLogDelta("events", 8, 8, "");
    expect(d.kind).toBe("unchanged");
  });

  it("refetches from zero when a 416 shows the family shrank", async () => {
    //  Rotation drops the oldest member, so the stitched total falls BELOW the
    //  cursor. Without this the tail silently stops and reads as a dead grill.
    fetchMock
      .mockResolvedValueOnce(res(416, "", { "Content-Range": "bytes */3" }))
      .mockResolvedValueOnce(res(200, "abc"));
    const d = await fetchLogDelta("events", 99, 200, "");
    expect(d).toEqual({ kind: "rotated", text: "abc", nextOffset: 3, total: 3 });
  });

  it("refetches from zero when a 206 total drops below the last known total", async () => {
    //  Rotation can leave the new total still ABOVE the cursor, so the 416
    //  branch never fires and the 206 body would be misaligned content.
    fetchMock
      .mockResolvedValueOnce(res(206, "xx", { "Content-Range": "bytes 10-11/40" }))
      .mockResolvedValueOnce(res(200, "whole"));
    const d = await fetchLogDelta("events", 10, 900, "");
    expect(d.kind).toBe("rotated");
    expect(d.text).toBe("whole");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web-react && bun run test src/helpers/logs/logsApi.test.ts`
Expected: FAIL — cannot resolve `./logsApi`

- [ ] **Step 4: Implement `logTypes.ts`**

```ts
// web-react/src/helpers/logs/logTypes.ts

/** One rotation family: `events.log` plus its `.1`/`.2`/`.3` backups. */
export interface LogFamily {
  stem: string;
  /** Member filenames, OLDEST first — the order the server stitches them. */
  members: string[];
  /** Total stitched size, which is also the end offset of the byte stream. */
  bytes: number;
}

/** The outcome of one tail poll. `total` is always the server's current
 * stitched size, which the caller must carry into the next poll so rotation
 * can be detected by a shrinking total. */
export type LogDelta =
  | { kind: "appended"; text: string; nextOffset: number; total: number }
  | { kind: "rotated"; text: string; nextOffset: number; total: number }
  | { kind: "unchanged"; nextOffset: number; total: number };
```

- [ ] **Step 5: Implement `logsApi.ts`**

```ts
// web-react/src/helpers/logs/logsApi.ts
import type { LogDelta } from "./logTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export function logViewUrl(stem: string, baseUrl = BASE_URL): string {
  return `${baseUrl}/api/admin/logs/view?log=${encodeURIComponent(stem)}`;
}

export function logDownloadUrl(stem: string, baseUrl = BASE_URL): string {
  return `${logViewUrl(stem, baseUrl)}&download=1`;
}

/** Range offsets are BYTE offsets. Using String.length here would desync the
 * cursor permanently on the first non-ASCII log line. */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** The `<total>` from either `bytes a-b/<total>` or `bytes * /<total>`. */
function totalFromContentRange(header: string | null): number | null {
  const match = /\/(\d+)\s*$/.exec(header ?? "");
  return match ? Number(match[1]) : null;
}

export async function fetchLogWhole(
  stem: string,
  baseUrl = BASE_URL,
): Promise<{ text: string; total: number }> {
  const response = await fetch(logViewUrl(stem, baseUrl));
  const text = await response.text();
  return { text, total: byteLength(text) };
}

/**
 * One tail poll.
 *
 * Rotation is the hazard this function exists to survive. When the family
 * rolls, the stitched stream shrinks and everything after the cursor is
 * different bytes. Two independent signals catch it, because one is not
 * enough: a 416 whose total is below the cursor, and a 206 whose total is
 * below the total we last saw. The second matters because a rotation can leave
 * the new total still above the cursor, in which case the server answers 206
 * quite happily with content from the wrong place.
 */
export async function fetchLogDelta(
  stem: string,
  offset: number,
  lastTotal: number,
  baseUrl = BASE_URL,
): Promise<LogDelta> {
  const response = await fetch(logViewUrl(stem, baseUrl), {
    headers: { Range: `bytes=${offset}-` },
  });
  const total = totalFromContentRange(response.headers.get("Content-Range"));

  if (response.status === 416) {
    if (total !== null && total < offset) {
      const whole = await fetchLogWhole(stem, baseUrl);
      return { kind: "rotated", text: whole.text, nextOffset: whole.total, total: whole.total };
    }
    return { kind: "unchanged", nextOffset: offset, total: total ?? lastTotal };
  }

  if (response.status === 206) {
    if (total !== null && total < lastTotal) {
      const whole = await fetchLogWhole(stem, baseUrl);
      return { kind: "rotated", text: whole.text, nextOffset: whole.total, total: whole.total };
    }
    const text = await response.text();
    return { kind: "appended", text, nextOffset: offset + byteLength(text), total: total ?? lastTotal };
  }

  //  200 means the server ignored the Range entirely; treat it as a whole read
  //  rather than appending a duplicate of everything already displayed.
  const text = await response.text();
  return { kind: "rotated", text, nextOffset: byteLength(text), total: byteLength(text) };
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd web-react && bun run test src/helpers/logs/logsApi.test.ts`
Expected: PASS, 7 passed

- [ ] **Step 7: Negative control**

Delete the `total < lastTotal` branch from the 206 path and re-run.
Expected: `refetches from zero when a 206 total drops below the last known
total` FAILS. Restore it.

- [ ] **Step 8: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
jj new -m "feat(web-react): typed client for the log view endpoint"
```

---

### Task 9: The viewer component

**Files:**
- Create: `web-react/src/components/logs/LogViewer.tsx`, `web-react/src/components/logs/logs.css`
- Test: `web-react/src/components/logs/LogViewer.test.tsx` (create)

**Interfaces:**
- Consumes: `fetchLogWhole`, `LogDelta`
- Produces: `<LogViewer stem={string} follow={boolean} />`

- [ ] **Step 1: Write the failing test**

```tsx
// web-react/src/components/logs/LogViewer.test.tsx
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { render, screen, waitFor } from "@testing-library/react";
import * as actualLogsApi from "../../helpers/logs/logsApi" with { rstest: "importActual" };

const fetchLogWholeMock = rs.fn();
const fetchLogDeltaMock = rs.fn();
rs.mock("../../helpers/logs/logsApi", () => ({
  ...actualLogsApi,
  fetchLogWhole: (...a: unknown[]) => fetchLogWholeMock(...a),
  fetchLogDelta: (...a: unknown[]) => fetchLogDeltaMock(...a),
}));

const { LogViewer } = await import("./LogViewer");

beforeEach(() => {
  fetchLogWholeMock.mockReset();
  fetchLogDeltaMock.mockReset();
  fetchLogWholeMock.mockResolvedValue({ text: "alpha\nbravo\n", total: 12 });
});

describe("LogViewer", () => {
  it("reads the whole family once on mount", async () => {
    render(<LogViewer stem="events" follow={false} />);
    await waitFor(() => expect(fetchLogWholeMock).toHaveBeenCalledTimes(1));
    expect(fetchLogWholeMock).toHaveBeenCalledWith("events");
  });

  it("renders the lines it read", async () => {
    render(<LogViewer stem="events" follow={false} />);
    expect(await screen.findByText(/alpha/)).toBeTruthy();
  });

  it("reports a failed read instead of rendering an empty frame", async () => {
    fetchLogWholeMock.mockRejectedValue(new Error("Failed to fetch"));
    render(<LogViewer stem="events" follow={false} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Failed to fetch"));
  });

  it("does not poll when follow is off", async () => {
    render(<LogViewer stem="events" follow={false} />);
    await waitFor(() => expect(fetchLogWholeMock).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchLogDeltaMock).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web-react && bun run test src/components/logs/LogViewer.test.tsx`
Expected: FAIL — cannot resolve `./LogViewer`

- [ ] **Step 3: Implement `LogViewer.tsx`**

```tsx
// web-react/src/components/logs/LogViewer.tsx
import { LazyLog } from "@melloware/react-logviewer";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchLogDelta, fetchLogWhole } from "../../helpers/logs/logsApi";
import "./logs.css";

const POLL_MS = 3000;

/** Colourize the level tag. Applied per line part by LazyLog. */
function formatPart(text: string) {
  if (text.includes("[ERROR]")) return <span className="pf-log-error">{text}</span>;
  if (text.includes("[WARNING]")) return <span className="pf-log-warning">{text}</span>;
  return text;
}

/**
 * One log family, virtualized and searchable.
 *
 * LazyLog runs in `external` mode: this component owns the fetching so the tail
 * can go through the Range endpoint, and pushes new lines in through
 * appendLines() on a ref. Letting LazyLog fetch its own `url` would re-download
 * the whole family on every poll.
 */
export function LogViewer({ stem, follow }: { stem: string; follow: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<LazyLog | null>(null);
  const offset = useRef(0);
  const total = useRef(0);

  const load = useCallback(async () => {
    const whole = await fetchLogWhole(stem);
    offset.current = whole.total;
    total.current = whole.total;
    return whole.text;
  }, [stem]);

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setError(null);
    load()
      .then((loaded) => {
        if (!cancelled) setText(loaded);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  useEffect(() => {
    if (!follow || text === null) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const delta = await fetchLogDelta(stem, offset.current, total.current);
        if (cancelled) return;
        offset.current = delta.nextOffset;
        total.current = delta.total;
        if (delta.kind === "appended" && delta.text) {
          logRef.current?.appendLines(delta.text.replace(/\n$/, "").split("\n"));
        } else if (delta.kind === "rotated") {
          //  The stream shrank underneath us, so every cached line may be
          //  stale. Replace wholesale rather than appending onto wrong content.
          setText(delta.text);
        }
      } catch {
        //  A failed poll is not fatal; the next tick retries.
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [follow, stem, text]);

  if (error) {
    return (
      <p className="pf-settings-error-text" role="alert">
        {error}
      </p>
    );
  }
  if (text === null) return <p className="pf-admin-note">Loading log…</p>;

  return (
    <div className="pf-log-frame">
      <LazyLog
        ref={logRef}
        text={text}
        external
        enableSearch
        enableSearchNavigation
        caseInsensitive
        enableLineNumbers
        selectableLines
        wrapLines
        follow={follow}
        formatPart={formatPart}
        height="60vh"
        extraLines={1}
      />
    </div>
  );
}
```

- [ ] **Step 4: Write `logs.css`**

```css
.pf-log-frame {
  border: 1px solid var(--pf-border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--pf-surface);
}

.pf-log-error {
  color: var(--pf-danger);
  font-weight: 600;
}

.pf-log-warning {
  color: var(--pf-warning);
}

.pf-log-tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.pf-log-tab {
  padding: 0.4rem 0.9rem;
  border: 1px solid var(--pf-border);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.pf-log-tab[aria-selected="true"] {
  background: var(--pf-accent);
  color: var(--pf-on-accent);
}

.pf-log-controls {
  display: flex;
  gap: 1rem;
  align-items: center;
  margin-bottom: 0.75rem;
  flex-wrap: wrap;
}
```

Check the real token names in `web-react/src/styles/` before committing; use
the ones that exist rather than inventing `--pf-*` names. `styleCoverage.test.ts`
will fail on any `pf-` class with no rule.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web-react && bun run test src/components/logs/LogViewer.test.tsx`
Expected: PASS, 4 passed

- [ ] **Step 6: Register the surface for style coverage**

In `web-react/src/styleCoverage.test.ts`, add `"src/components/logs"` to
SURFACES and `["src/components/logs/LogViewer.tsx", "./logs.css"]` to the import
assertions, matching the existing admin entries.

- [ ] **Step 7: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
jj new -m "feat(web-react): virtualized log viewer with Range-delta tailing"
```

---

### Task 10: Family listing on the state endpoint

**Files:**
- Modify: `blueprints/api_admin/routes.py` (the `/logs` route)
- Modify: `web-react/src/helpers/logs/logsApi.ts`
- Test: `tests/web/test_api_admin_logs_view.py` (extend),
  `web-react/src/helpers/logs/logsApi.test.ts` (extend)

**Interfaces:**
- Produces: `GET /api/admin/logs` gains a `families` member alongside the
  existing `logs` member, which is left untouched:
  `{"logs": [...], "families": [{"stem": "events", "members": [...], "bytes": 8}]}`
- Produces: `fetchLogFamilies(baseUrl?) => Promise<LogFamily[]>`

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_api_admin_logs_view.py`:

```python
def test_listing_reports_families_alongside_the_flat_list(client, logdir):
    body = client.get("/api/admin/logs").get_json()["data"]
    #  `logs` keeps its exact shipped contract: the LogsCard depends on it.
    assert body["logs"] == ["events.log"]
    assert body["families"] == [
        {"stem": "events", "members": ["events.log.1", "events.log"], "bytes": 8}
    ]
```

Append to `web-react/src/helpers/logs/logsApi.test.ts`:

```ts
it("unpacks the families member", async () => {
  fetchMock.mockResolvedValue(
    new Response(
      JSON.stringify({
        result: "OK",
        message: "",
        data: { logs: [], families: [{ stem: "events", members: ["events.log"], bytes: 4 }] },
      }),
      { status: 200 },
    ),
  );
  const { fetchLogFamilies } = await import("./logsApi");
  expect(await fetchLogFamilies("")).toEqual([
    { stem: "events", members: ["events.log"], bytes: 4 },
  ]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_admin_logs_view.py -q`
Expected: FAIL — `KeyError: 'families'`

Run: `cd web-react && bun run test src/helpers/logs/logsApi.test.ts`
Expected: FAIL — `fetchLogFamilies` is not exported

- [ ] **Step 3: Implement the backend**

Add to `blueprints/api_admin/admin_api.py`:

```python
def log_family_listing(folder=None):
    """[{stem, members, bytes}] for every family, ordered by stem.

    `bytes` is the stitched total, which is also the end offset of the byte
    stream /logs/view serves -- the client seeds its tail cursor from it.
    """
    folder = folder or LOG_FOLDER
    listing = []
    for stem, members in list_log_families(folder).items():
        total = 0
        for name in members:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                continue
        listing.append({"stem": stem, "members": members, "bytes": total})
    return listing
```

Change the `/logs` route in `blueprints/api_admin/routes.py`:

```python
@api_admin_bp.route("/logs", methods=["GET"])
def admin_logs():
    return jsonify(
        api_response(
            "OK",
            None,
            {"logs": admin_api.list_logs(), "families": admin_api.log_family_listing()},
        )
    ), 200
```

- [ ] **Step 4: Implement the client**

Add to `web-react/src/helpers/logs/logsApi.ts`:

```ts
import type { LogFamily } from "./logTypes";

export async function fetchLogFamilies(baseUrl = BASE_URL): Promise<LogFamily[]> {
  const response = await fetch(`${baseUrl}/api/admin/logs`);
  const body = (await response.json().catch(() => ({}))) as {
    result?: string;
    data?: { families?: LogFamily[] };
  };
  if (!response.ok || body.result !== "OK") return [];
  return body.data?.families ?? [];
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/ -q`
Expected: PASS — including the existing admin state tests, which must not move

Run: `cd web-react && bun run test src/helpers/logs/`
Expected: PASS, 8 passed

- [ ] **Step 6: Format, gate and commit**

```bash
.venv/bin/ruff format blueprints/api_admin/admin_api.py blueprints/api_admin/routes.py
cd web-react && bun run typecheck && bun run lint
jj new -m "feat(api_admin): publish log families alongside the flat listing"
```

---

### Task 11: The /events route, tabs and navbar

**Files:**
- Create: `web-react/src/components/logs/EventsPage.tsx`
- Modify: `web-react/src/components/App.tsx`, `web-react/src/components/shell/NavBar.tsx`
- Test: `web-react/src/components/logs/EventsPage.test.tsx` (create),
  `web-react/src/components/shell/NavBar.test.tsx` (modify)

**Interfaces:**
- Consumes: `<LogViewer stem follow />`, `fetchLogFamilies`
- Produces: route `/events`, `<EventsPage />`

- [ ] **Step 1: Write the failing test**

```tsx
// web-react/src/components/logs/EventsPage.test.tsx
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderRoute } from "../../test-utils";
import * as actualLogsApi from "../../helpers/logs/logsApi" with { rstest: "importActual" };

const fetchLogFamiliesMock = rs.fn();
rs.mock("../../helpers/logs/logsApi", () => ({
  ...actualLogsApi,
  fetchLogFamilies: (...a: unknown[]) => fetchLogFamiliesMock(...a),
  fetchLogWhole: () => Promise.resolve({ text: "alpha\n", total: 6 }),
  fetchLogDelta: () => Promise.resolve({ kind: "unchanged", nextOffset: 6, total: 6 }),
}));

const { EventsPage } = await import("./EventsPage");

beforeEach(() => {
  fetchLogFamiliesMock.mockReset();
  fetchLogFamiliesMock.mockResolvedValue([
    { stem: "events", members: ["events.log"], bytes: 6 },
    { stem: "mqtt", members: ["mqtt.log"], bytes: 9 },
  ]);
});

describe("EventsPage", () => {
  it("opens on the Events tab", async () => {
    renderRoute(<EventsPage />);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Events" }).getAttribute("aria-selected")).toBe("true"),
    );
  });

  it("switches to Log Files", async () => {
    renderRoute(<EventsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "Log Files" }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Log Files" }).getAttribute("aria-selected")).toBe("true"),
    );
  });

  it("offers a follow toggle on the Events tab", async () => {
    renderRoute(<EventsPage />);
    expect(await screen.findByRole("checkbox", { name: /follow/i })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web-react && bun run test src/components/logs/EventsPage.test.tsx`
Expected: FAIL — cannot resolve `./EventsPage`

- [ ] **Step 3: Implement `EventsPage.tsx`**

```tsx
// web-react/src/components/logs/EventsPage.tsx
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { fetchLogFamilies } from "../../helpers/logs/logsApi";
import type { LogFamily } from "../../helpers/logs/logTypes";
import { LogFilesTab } from "./LogFilesTab";
import { LogViewer } from "./LogViewer";
import "./logs.css";

const EVENTS_STEM = "events";
const TABS = [
  { id: "events", label: "Events" },
  { id: "files", label: "Log Files" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * The event feed and the log-file browser, one page.
 *
 * Both tabs are the same viewer over a different family, which is why they live
 * together rather than as two routes that would quietly diverge.
 */
export function EventsPage() {
  const [params, setParams] = useSearchParams();
  const active: TabId = params.get("tab") === "files" ? "files" : "events";
  const [families, setFamilies] = useState<LogFamily[]>([]);
  const [follow, setFollow] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchLogFamilies().then((found) => {
      if (!cancelled) setFamilies(found);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="pf-admin">
      <h1>Events</h1>

      <div className="pf-log-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            className="pf-log-tab"
            aria-selected={active === tab.id}
            onClick={() => setParams(tab.id === "events" ? {} : { tab: tab.id })}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {active === "events" ? (
        <>
          <div className="pf-log-controls">
            <label className="pf-field">
              <input
                type="checkbox"
                checked={follow}
                onChange={(e) => setFollow(e.target.checked)}
              />
              <span>Follow new events</span>
            </label>
          </div>
          <LogViewer stem={EVENTS_STEM} follow={follow} />
        </>
      ) : (
        <LogFilesTab families={families} />
      )}
    </section>
  );
}
```

- [ ] **Step 4: Wire the route and navbar**

In `web-react/src/components/App.tsx`, add under `AppShell` beside the `/admin`
entry:

```tsx
    //  No loader: the page reads its own families and log text, and a loader
    //  would block the shell on a file read that can be slow on a Pi.
    { path: "/events", element: <EventsPage /> },
```

In `web-react/src/components/shell/NavBar.tsx`, change the Events entry from its
disabled form to a real link:

```tsx
  { label: "Events", to: "/events", end: false },
```

and update the header comment: Events was the last unported destination, so the
comment must now say every Flask page in the navbar has a React route.

Update **both** cases in `web-react/src/components/shell/NavBar.test.tsx` — the
enabled-links case and the disabled-entries case — in the same edit.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd web-react && bun run test src/components/logs/ src/components/shell/NavBar.test.tsx`
Expected: PASS

- [ ] **Step 6: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
jj new -m "feat(web-react): /events route, tabs and the last navbar entry"
```

---

### Task 12: The Log Files tab

**Files:**
- Create: `web-react/src/components/logs/LogFilesTab.tsx`
- Test: `web-react/src/components/logs/LogFilesTab.test.tsx` (create)

**Interfaces:**
- Consumes: `LogFamily`, `logDownloadUrl(stem)`, `<LogViewer stem follow />`
- Produces: `<LogFilesTab families={LogFamily[]} />`

- [ ] **Step 1: Write the failing test**

```tsx
// web-react/src/components/logs/LogFilesTab.test.tsx
import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import * as actualLogsApi from "../../helpers/logs/logsApi" with { rstest: "importActual" };

rs.mock("../../helpers/logs/logsApi", () => ({
  ...actualLogsApi,
  fetchLogWhole: () => Promise.resolve({ text: "body\n", total: 5 }),
  fetchLogDelta: () => Promise.resolve({ kind: "unchanged", nextOffset: 5, total: 5 }),
}));

const { LogFilesTab } = await import("./LogFilesTab");

const FAMILIES = [
  { stem: "events", members: ["events.log.1", "events.log"], bytes: 2048 },
  { stem: "mqtt", members: ["mqtt.log"], bytes: 1024 },
];

describe("LogFilesTab", () => {
  it("lists every family with its member count", () => {
    render(<LogFilesTab families={FAMILIES} />);
    expect(screen.getByRole("option", { name: /events/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /mqtt/ })).toBeTruthy();
  });

  it("downloads the whole family, not one member", () => {
    //  The bytes offered must be the bytes displayed, so the link points at the
    //  same view endpoint with download=1 rather than at a single file.
    render(<LogFilesTab families={FAMILIES} />);
    expect(screen.getByRole("link", { name: /download/i }).getAttribute("href")).toBe(
      "/api/admin/logs/view?log=events&download=1",
    );
  });

  it("switches the viewer when another family is picked", () => {
    render(<LogFilesTab families={FAMILIES} />);
    fireEvent.change(screen.getByRole("combobox", { name: /log file/i }), {
      target: { value: "mqtt" },
    });
    expect(screen.getByRole("link", { name: /download/i }).getAttribute("href")).toBe(
      "/api/admin/logs/view?log=mqtt&download=1",
    );
  });

  it("says so when there are no logs at all", () => {
    render(<LogFilesTab families={[]} />);
    expect(screen.getByText(/no log files/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web-react && bun run test src/components/logs/LogFilesTab.test.tsx`
Expected: FAIL — cannot resolve `./LogFilesTab`

- [ ] **Step 3: Implement**

```tsx
// web-react/src/components/logs/LogFilesTab.tsx
import { useState } from "react";
import { logDownloadUrl } from "../../helpers/logs/logsApi";
import type { LogFamily } from "../../helpers/logs/logTypes";
import { LogViewer } from "./LogViewer";

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}

/** Family picker plus the viewer. Tailing is off here: these are historical
 * files a user is reading, not the live feed the Events tab shows. */
export function LogFilesTab({ families }: { families: LogFamily[] }) {
  const [stem, setStem] = useState(families[0]?.stem ?? "");
  const selected = families.find((family) => family.stem === stem) ?? families[0];

  if (families.length === 0) {
    return <p className="pf-admin-note">No log files yet.</p>;
  }

  return (
    <>
      <div className="pf-log-controls">
        <label className="pf-field">
          <span className="pf-field-label">Log file</span>
          {/* aria-label as well as the wrapping label: a label that wraps a
              select contributes its whole text content, options included, to
              the accessible name. */}
          <select
            aria-label="Log file"
            className="pf-input"
            value={selected?.stem ?? ""}
            onChange={(e) => setStem(e.target.value)}
          >
            {families.map((family) => (
              <option key={family.stem} value={family.stem}>
                {`${family.stem} (${family.members.length} file${
                  family.members.length === 1 ? "" : "s"
                }, ${formatBytes(family.bytes)})`}
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <a className="pf-admin-btn" href={logDownloadUrl(selected.stem)} download>
            Download
          </a>
        )}
      </div>
      {selected && <LogViewer stem={selected.stem} follow={false} />}
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web-react && bun run test src/components/logs/LogFilesTab.test.tsx`
Expected: PASS, 4 passed

- [ ] **Step 5: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
jj new -m "feat(web-react): log file browser with family download"
```

---

### Task 13: End-to-end against the live backend

**Files:**
- Create: `web-react/tests/e2e/events.spec.ts`
- Modify: `web-react/tests/e2e/apiFixtures.ts`

**Interfaces:**
- Consumes: the live `/api/admin/logs` and `/api/admin/logs/view`
- Produces: `stubEvents(page)` for the baseline capture in Task 14

- [ ] **Step 1: Write the spec**

```ts
// web-react/tests/e2e/events.spec.ts
import { expect, test } from "@playwright/test";

// The events page against the real backend, reading only.
//
// SAFETY: this page is read-only, but the log DELETE and CLEAR doors live on
// the same API surface and a stray navigation could reach them. They are
// aborted at the network boundary and every attempt is recorded, because an
// aborted request would otherwise be silently swallowed and this spec would
// keep passing while quietly trying to delete the operator's logs.

const WRITE_ROUTES = ["**/api/admin/logs/delete", "**/api/admin/maintenance"];

let attempted: string[] = [];

test.beforeEach(async ({ page }) => {
  attempted = [];
  for (const pattern of WRITE_ROUTES) {
    await page.route(pattern, async (route) => {
      attempted.push(route.request().url());
      await route.abort();
    });
  }
});

test.afterEach(() => {
  expect(attempted, "a log write escaped this spec").toEqual([]);
});

test.describe("events page", () => {
  test("renders the live event feed", async ({ page }) => {
    await page.goto("/events");
    await expect(page.getByRole("heading", { name: "Events" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Events" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    //  A real PiFire always has an events log; an empty frame means the
    //  endpoint and the page disagree about the payload.
    await expect(page.locator(".pf-log-frame")).toBeVisible();
  });

  test("serves the family as plain text with range support", async ({ request }) => {
    const whole = await request.get("/api/admin/logs/view?log=events");
    expect(whole.status()).toBe(200);
    expect(whole.headers()["accept-ranges"]).toBe("bytes");
    const total = (await whole.body()).length;

    const tail = await request.get("/api/admin/logs/view?log=events", {
      headers: { Range: `bytes=${Math.max(0, total - 10)}-` },
    });
    expect(tail.status()).toBe(206);
    expect(tail.headers()["content-range"]).toContain(`/${total}`);

    const past = await request.get("/api/admin/logs/view?log=events", {
      headers: { Range: `bytes=${total + 1000}-` },
    });
    expect(past.status()).toBe(416);
    //  The client's rotation detection depends on this header existing.
    expect(past.headers()["content-range"]).toBe(`bytes */${total}`);
  });

  test("refuses a path-shaped family name", async ({ request }) => {
    const escaped = await request.get("/api/admin/logs/view?log=../pifire");
    expect(escaped.status()).toBe(404);
  });

  test("browses log files and offers a family download", async ({ page }) => {
    await page.goto("/events");
    await page.getByRole("tab", { name: "Log Files" }).click();
    const picker = page.getByRole("combobox", { name: "Log file" });
    await expect(picker).toBeVisible();

    //  Not clicked: following it would download. The point is the href.
    const link = page.getByRole("link", { name: "Download", exact: true });
    const href = (await link.getAttribute("href")) ?? "";
    expect(href).toMatch(/^\/api\/admin\/logs\/view\?log=[^&/]+&download=1$/);
  });

  test("searches within the loaded log", async ({ page }) => {
    await page.goto("/events");
    await page.getByRole("searchbox").fill("PiFire");
    await expect(page.locator(".pf-log-frame")).toBeVisible();
  });

  test("reaches the page from the navbar", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Events" }).click();
    await expect(page).toHaveURL(/\/events$/);
  });
});
```

- [ ] **Step 2: Add the baseline stub**

In `web-react/tests/e2e/apiFixtures.ts`, beside `stubAdmin`:

```ts
export async function stubEvents(page: Page) {
  await page.route("**/api/admin/logs", async (route) => {
    await route.fulfill({
      json: {
        result: "OK",
        message: "",
        data: {
          logs: ["events.log"],
          families: [{ stem: "events", members: ["events.log"], bytes: 42 }],
        },
      },
    });
  });
  await page.route("**/api/admin/logs/view*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/plain",
      headers: { "Accept-Ranges": "bytes" },
      body: "2026-07-28 09:00:00 -0400 [INFO] PiFire Web UI started.\n",
    });
  });
}
```

- [ ] **Step 3: Confirm the backend is current, then run**

```bash
curl -s localhost:5000/api/get/revision
```
Expected: `stale` is false. The dev backend auto-reloads (`gunicorn --reload`
plus `control.py` under `watchfiles`), but confirm before blaming a test.

Run: `cd web-react && bunx playwright test tests/e2e/events.spec.ts --project=app`
Expected: PASS, 6 passed

Do **not** run `bun run test:e2e`.

- [ ] **Step 4: Gate and commit**

```bash
cd web-react && bun run typecheck:e2e && bun run lint
jj new -m "test(web-react): live events and log view end to end"
```

---

### Task 14: Baselines and backlog closeout

**Files:**
- Modify: `web-react/tests/e2e/pageSpecs.ts`
- Create: `web-react/tests/e2e/baselines/events-1280x720.json`, `events-390x844.json`
- Modify: `docs/superpowers/backlogs/react-migration-backlog.md`

- [ ] **Step 1: Register the page spec**

In `web-react/tests/e2e/pageSpecs.ts`, add an `events` PageSpec matching the
`admin` entry's shape: `path: "/events"`, `ready: '.pf-log-frame'`,
`stubs: stubEvents`, and the landmark list for the heading, both tabs, and the
follow toggle.

- [ ] **Step 2: Capture**

```bash
cd web-react && bun run baseline:capture -- -g "events"
```
Expected: two new baseline files. Never hand-edit them.

- [ ] **Step 3: Verify the diff is pure additions**

```bash
jj --no-pager diff --git web-react/tests/e2e/baselines/ | grep -c "^-[^-]"
```
Expected: `0`. Any deletion means an existing page's baseline moved, which this
task must not cause.

- [ ] **Step 4: Run the full gate**

```bash
cd web-react && bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run test:e2e:fidelity
cd .. && QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```
Expected: all green; pytest at or above 3352.

- [ ] **Step 5: Close out the backlog**

In `docs/superpowers/backlogs/react-migration-backlog.md`:

1. §8, mark `- [x] **events** + **logs** — SHIPPED 2026-07-28` with the plan path.
2. Add an **Events + Logs** entry to the SHIPPED section covering: the one
   Range endpoint, the family-stem contract that closes the two traversal
   doors, the rotated-file blind spot fixed in three places
   (`delete_logs`, `build_log_archive`, and the listing), the clear-path bug
   where `flush_events_records()` cleared the database while
   `read_events_records()` read the file, the new `logs`-table retention
   policy, and the test-pollution fix.
3. §10 "Whole surfaces never built": strike the Events line. **Every navbar
   entry is now a real link** — update the App-shell line that still says
   Events is rendered disabled.
4. Record the deferrals this slice creates, per the standing rule: Flask's
   `blueprints/events/` and `blueprints/logs/` are still live and still carry
   their two unfixed traversal doors; `datastore.read_log()` remains without a
   caller; the wizard's `/admin/restart` and `/admin/reboot` links are still
   open.

- [ ] **Step 6: Commit**

```bash
jj new -m "docs(events): close out the events and logs slice"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: view endpoint → Task 3;
family stem containment → Tasks 2 and 3; chronological stitching → Tasks 1 and
2; `list_log_families` with `list_logs` untouched → Tasks 1 and 10; family-aware
delete/archive → Task 4; clear-path fix → Task 5; retention → Task 6; test
pollution → Task 7; dependency → Task 8; tabs and route → Task 11; picker and
download → Task 12; live tail with rotation detection → Tasks 8 and 9;
testing and baselines → Tasks 13 and 14.

**Deviation from the spec, deliberate:** the spec described rotation detection
as the 416 branch alone. Task 8 implements **two** signals — a 416 whose total
is below the cursor, and a 206 whose total is below the last known total —
because rotation can leave the new total above the cursor, in which case the
server answers 206 with content from the wrong offset and the single-signal
design would display corrupt output rather than recovering. The spec's final
section should be read as superseded on this point.

**Type consistency.** `LogFamily` is `{stem, members, bytes}` in
`logTypes.ts`, in `log_family_listing()`, in the `stubEvents` fixture and in
every test. `LogDelta` carries `total` on all three variants, and every
`fetchLogDelta` call site passes `(stem, offset, lastTotal)`.
`clear_events_log` returns `True` in Task 5 exactly as it did before, because
the shipped MaintenanceCard depends on it.

**One risk worth naming.** Task 7 changes logging paths across `app.py`,
`board-config.py` and `common/process_mon.py`. If `reset_loggers()` fails to
detach a handler bound at import time, the suite keeps writing to the real
`./logs/` and the isolation test could still pass by coincidence. Step 7 of that
task is a before/after directory comparison for exactly that reason — it is the
only step that proves the outcome rather than the mechanism.
