# React Metrics Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Flask's `/metrics` page — the per-mode metrics record list and its
CSV export — to the React app at `/metrics`, behind a new `/api/metrics` JSON
blueprint.

**Architecture:** A new `blueprints/api_metrics/` serves two GETs: the processed
metrics list wrapped in the standard `api_response` envelope, and the CSV
export as an attachment. The React page is read-only: one mount fetch, a card
per metrics record with a per-mode field table and a raw-data disclosure, an
anchor to the CSV, and an empty state. `process_metrics()` stays the single
source of truth for the derived/"converted" columns — the client does no
timestamp or pellet-usage arithmetic of its own.

**Tech Stack:** Flask blueprint + `common.app.api_response`; React 19 +
react-router; rstest (unit), Playwright (e2e + layout baselines); Tailwind v4
`@apply` over `@theme static` tokens; bun; jj.

---

## Global Constraints

- **Toolchain is `bun`, never `npm`.** Commit `bun.lock` if it moves.
- **Commit with `jj`, never `git commit`.** `jj new` BEFORE the first Write of a
  task; `jj describe --stdin` with a quoted heredoc (there is no `-F` flag).
  Never `jj squash` after editing — edits are already in `@`.
- **`.venv/bin/ruff format` every changed Python file before committing.** Never
  `uvx ruff` (the repo pins ruff <0.16).
- **Python tests:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run
  pytest tests/ -q`. Bare `python` gives false failures.
- **web-react gates, all three, every task that touches `web-react/`:**
  `bun run typecheck`, `bun run lint` (Biome + eslint), `bun run test`.
  `bun run typecheck:e2e` additionally for anything under `tests/e2e/`.
- **Do NOT run `bun run test:e2e`** (the whole `app` project): `roundtrip.spec.ts`
  puts the grill into Startup mode and `settings.spec.ts` flushes the history
  store. Use `bun run test:e2e:fidelity`, or one named spec with `--project=app`.
- **Every `pf-*` class used in a `.tsx` must have a rule in a `.css`.**
  `src/cssCoverage.test.ts` and `src/styleCoverage.test.ts` enforce this in both
  directions — an unused rule fails too.
- **Never hand-edit `web-react/tests/e2e/baselines/*.json`.** They are captured
  by `bun run baseline:capture`.
- **Types come from a LIVE payload, never from a producer's default literals.**
  `common/defaults.py`'s `metrics_items` declares `("starttime_c", 0)` and
  `("timeinmode", 0)`, but `process_metrics()` overwrites both with strings. A
  type written from the defaults would be wrong on every field it names. Task 4
  pins the shape against a real response.
- **The metrics surface is read-only.** No task in this plan adds a POST, and no
  test in this plan calls one. `POST /api/admin/logs/delete`,
  `/api/admin/maintenance`, `/api/admin/system` and `/api/admin/factory-reset`
  are not touched, not stubbed, and not navigated to.
- **Neutralize `os.system`/`subprocess`/`sudo`/`reboot`/`shutdown` before running
  any test that can reach admin/installer/updater/wizard paths.** Nothing in this
  plan should reach one; if a task finds itself running such a test, grep the
  module under test for those calls first. `is_real_hardware()` is NOT a guard —
  it defaults to True, and this repo has really rebooted the developer's machine
  three times.
- **`control.py` drives relays. Do not start it.** The e2e task needs `gunicorn`
  only.
- **Exact strings.** Page heading `Metrics`. Empty-state heading `No Data` with
  the body `Start the grill to begin populating metrics.` (verbatim from
  `blueprints/metrics/templates/metrics/index.html`). CSV link label
  `Download CSV Data` (verbatim). Raw-data disclosure label `Raw Data`
  (verbatim).

---

## Verified Facts

Everything below was read from live code on 2026-07-28. Do not re-derive it;
do flag it if it no longer matches.

### The Flask surface being ported

`blueprints/metrics/routes.py` is 28 lines:

```python
@metrics_bp.route("/<action>", methods=["POST", "GET"])
@metrics_bp.route("/", methods=["POST", "GET"])
def metrics_page(action=None):
    settings = read_settings()
    control = read_control()

    metrics_data = process_metrics(read_all_metrics())

    if (request.method == "GET") and (action == "export"):
        filename = datetime.datetime.now().strftime("%Y%m%d-%H%M") + "-PiFire-Metrics-Export"
        csvfilename = prepare_metrics_csv(metrics_data, filename)
        return send_file(csvfilename, as_attachment=True, max_age=0)

    return render_template(
        "metrics/index.html",
        settings=settings,
        control=control,
        metrics_data=metrics_data,
    )
```

`index.html` renders `No Data` when the list is empty, otherwise a
`Download CSV Data` anchor to `/metrics/export` followed by one
`_macro_metrics.html` card per record, dispatched on `item['mode']`. Every card
is a three-column table (`Metric` / `Value` / `Converted`) plus a
`Raw Data` collapse containing `{{ metric }}`.

### The wire shape, after `process_metrics`

The `metrics` table's columns are `common.defaults.METRIC_COLUMNS`, which is
`[k for k, _ in metrics_items]`. `_metrics_row_to_dict` (`common/datastore_accessors.py:261`)
coerces `smokeplus` to `bool` and leaves everything else as SQLite returned it.
`process_metrics` (`common/common.py:569`) then **overwrites** six fields:

| field | DDL type | after `process_metrics` |
|---|---|---|
| `starttime_c` | TEXT | `"%H:%M:%S"` string |
| `endtime_c` | TEXT | `"%H:%M:%S"` string, **or the integer `0`** when `endtime == 0` |
| `timeinmode` | NUMERIC | `"NA"` (Stop), `"Active"` (endtime 0), `"3 m 20 s"`, or `"45 s"` |
| `augerontime_c` | TEXT | `"<int> s"` |
| `estusage_m` | TEXT | `"<grams> grams"` |
| `estusage_i` | TEXT | `"<lb> pounds (<oz> ounces)"` |

`endtime_c` is the trap: it is a `string` for a finished mode and the **number
`0`** for a running one. Type it `string | number` and never render it directly
— branch on `endtime === 0`.

`fanontime_c` is declared TEXT and is **never** written by `process_metrics`;
it carries whatever `control.py` last wrote, which may be `null`.

**`timeinmode`'s minute boundary is `> 60`, not `>= 60`.** A mode lasting exactly
60 000 ms reports `"60 s"`, not `"1 m 0 s"` — the branch is
`if seconds > 60`. Verified against a live `process_metrics` call on 2026-07-28;
every fixture below uses a 60 000 ms span and therefore expects `"60 s"`.

**`append_metric` overwrites `starttime` and `id`.** It stamps
`metrics["starttime"] = time.time() * 1000` and a fresh uuid before inserting
(`common/datastore_accessors.py:305`), so seeding those two keys in the dict
handed to it silently does nothing. Every fixture below seeds a deterministic
span by calling `append_metric(row)` and then `update_metrics({...})`, and no
test asserts on `id`.

### Three live defects this port fixes

1. **`_macro_metrics.html`'s Hold card reads `metric['grill_settemp']`, which is
   not a column.** `metrics_items` has `primary_setpoint`; there is no
   `grill_settemp` anywhere in the metrics schema. Jinja renders an undefined
   key as empty, so Flask's Hold card has always shown a blank "Grill Set Temp".
   The React card reads `primary_setpoint`. **The Jinja template is not edited**
   — it is the legacy UI and `tests/web/test_page_smallpages.py` characterizes
   it; the fix lands only on the new surface.
2. **`metrics_page` never passes the configured auger rate.**
   `process_metrics(metrics_data, augerrate=0.3)` defaults to 0.3, and the route
   calls it with one argument, so the estimated pellet usage on the Flask
   metrics page is wrong for every grill whose
   `settings["globals"]["augerrate"]` is not 0.3. The setting is real and
   already wired: `blueprints/settings/routes.py:679-680` writes it, and
   `common/app.py:175` reads it for the dashboard's own estimate. Task 1 fixes
   the call site in **both** routes.
3. **The Flask export's filename says `-PiFire-Metrics-Export` twice.**
   `metrics_page` composes `strftime("%Y%m%d-%H%M") + "-PiFire-Metrics-Export"`
   and hands it to `prepare_metrics_csv`, which appends
   `"-PiFire-Metrics-Export.csv"` of its own (`common/app.py:209`) — so the
   download arrives as `20260728-1631-PiFire-Metrics-Export-PiFire-Metrics-Export.csv`.
   Task 3's route passes the bare stamp. The Flask route is left alone.

### The catch-all that `/api/metrics` has to beat

`blueprints/api/routes.py:457-462` registers `/api/`, `/api/<action>` and four
deeper variadic forms, all `["POST", "GET"]`. `/api/<action>` matches the
literal path `/api/metrics`. Werkzeug sorts static rules ahead of converter
rules, so the new blueprint's static rule should win — but this is exactly the
kind of routing accident `blueprints/api_admin/routes.py`'s module docstring
warns about, so Task 2 pins it with a test rather than assuming.

### React conventions this page follows

- `BASE_URL` is `import.meta.env.PUBLIC_PIFIRE_URL || ""` — same-origin,
  **never** the shell context's `targetUrl` (absolute, and Flask sends no CORS
  headers).
- The typed client mirrors `src/helpers/admin/adminApi.ts`: an `unpack()` over
  the `{data, result, message}` envelope, resolving to a result object rather
  than throwing.
- Download links are **anchors**, never fetches — see `backupDownloadUrl`'s
  comment in `adminApi.ts`: it keeps the file out of JS memory and gives the
  user the browser's own save dialog.
- A heading `id` used by `aria-labelledby` must **not** start with `pf-`:
  `cssCoverage`'s `classesUsedIn()` scans source strings for `pf-*` and would
  take it for a class with no rule (`AdminPage.tsx` has this comment).
- `src/structure.test.ts` fails on two importable modules in one directory whose
  stems differ only by case. `metrics.css` beside `MetricsPage.tsx` is fine
  (`.css` is not importable-ambiguous); a `metrics.ts` beside a `Metrics.tsx`
  would not be.

### Navigation ruling

The navbar gets **no** Metrics entry. Flask's `templates/base.html` has none
either — the only link into `/metrics` in the entire Flask tree is
`blueprints/history/templates/history/index.html:47`:

```html
<a href="/metrics" class="btn btn-primary" role="button"><i class="fas fa-chart-line"></i>&nbsp; Metrics</a>
```

The React `HistoryPage` dropped it, which the backlog records as an open parity
gap ("History→Metrics link dropped", `backlogs/react-migration-backlog.md:1018`). Task 8
restores it there and Task 9 strikes the backlog line.

---

## File Structure

**Create**

| Path | Responsibility |
|---|---|
| `blueprints/api_metrics/__init__.py` | Blueprint object, `url_prefix="/api/metrics"` |
| `blueprints/api_metrics/routes.py` | The two GETs |
| `tests/web/test_api_metrics.py` | Both endpoints, the envelope, the routing pin |
| `web-react/src/helpers/metrics/metricsTypes.ts` | `MetricRecord`, `MetricsPayload`, `MetricsResult` |
| `web-react/src/helpers/metrics/metricsApi.ts` | `fetchMetrics`, `metricsExportUrl` |
| `web-react/src/helpers/metrics/metricsApi.test.ts` | Client unit tests |
| `web-react/src/components/metrics/metricFields.ts` | mode → row descriptors (pure data) |
| `web-react/src/components/metrics/metricFields.test.ts` | Row-table unit tests |
| `web-react/src/components/metrics/MetricCard.tsx` | One record: header, table, raw-data disclosure |
| `web-react/src/components/metrics/MetricCard.test.tsx` | Card unit tests |
| `web-react/src/components/metrics/MetricsPage.tsx` | Fetch, states, list, CSV link |
| `web-react/src/components/metrics/MetricsPage.test.tsx` | Page unit tests |
| `web-react/src/components/metrics/metrics.css` | Every `pf-metrics-*` rule |
| `web-react/tests/e2e/metrics.spec.ts` | Live-backend read-only e2e |

**Modify**

| Path | Change |
|---|---|
| `blueprints/metrics/routes.py` | Pass the configured auger rate (Task 1) |
| `app.py` | Import + register `api_metrics_bp` (Task 2) |
| `tests/unit/common/test_common_metrics.py` | Auger-rate test (Task 1) |
| `web-react/src/components/App.tsx` | `/metrics` route (Task 8) |
| `web-react/src/components/history/HistoryPage.tsx` | Metrics link (Task 8) |
| `web-react/src/components/history/HistoryPage.test.tsx` | Link test (Task 8) |
| `web-react/tests/e2e/apiFixtures.ts` | `stubMetrics` (Task 9) |
| `web-react/tests/e2e/pageSpecs.ts` | `metrics` page spec (Task 9) |
| `docs/superpowers/backlogs/react-migration-backlog.md` | Closeout (Task 9) |

**Deliberately not created:** no `metricsRoutes.ts` loader. `/metrics` reads on
mount like `/admin` and `/events` do, so a failed read is retryable inside the
page instead of throwing the shell to an error element.

## Parallelization

- **Tasks 1–3 (backend)** are strictly serial: 2 depends on 1's call-site shape,
  3 registers a route in the blueprint 2 creates.
- **Tasks 4–7 (frontend)** are serial: each imports the previous one's exports.
- **Slice A and Slice B can run concurrently in separate jj workspaces**, because
  Task 4 pins the payload against the *contract stated in Task 2*, not against a
  running server. If they are run concurrently, Task 9's e2e is the join point
  and must run after both land. Disjoint files are not sufficient isolation —
  use `jj workspace add` (and copy `.lsp.json` + run `bun install`, both
  gitignored, so `workspace add` skips them).
- **Task 8 and Task 9** are serial after 7, and after Slice A if it was run in
  parallel.

---

## Slice A — the JSON surface

### Task 1: Feed the configured auger rate into `process_metrics`

**Files:**
- Modify: `blueprints/metrics/routes.py`
- Test: `tests/unit/common/test_common_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the ruling that every `process_metrics()` call site passes
  `augerrate=settings["globals"]["augerrate"]`. Task 2's new route follows it.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/common/test_common_metrics.py`:

```python
def test_process_metrics_uses_the_supplied_auger_rate():
    """The pellet-usage estimate is a function of the grill's configured auger
    rate, not of the 0.3 g/s default.

    settings["globals"]["augerrate"] has been settable since
    blueprints/settings/routes.py:679 and is what common/app.py:175 already
    uses for the dashboard's own estimate. The metrics page ignored it, so a
    grill tuned to any other rate read its pellet usage off a stranger's auger.
    """
    from common.common import process_metrics
    from common.defaults import default_metrics

    row = default_metrics()
    row["mode"] = "Smoke"
    row["starttime"] = 1_700_000_000_000
    row["endtime"] = 1_700_000_060_000
    row["augerontime"] = 100

    default_rate = process_metrics([dict(row)])[0]["estusage_m"]
    doubled = process_metrics([dict(row)], augerrate=0.6)[0]["estusage_m"]

    assert default_rate == "30 grams"
    assert doubled == "60 grams"
```

- [ ] **Step 2: Run it to make sure it passes for the right reason**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/common/test_common_metrics.py::test_process_metrics_uses_the_supplied_auger_rate -q
```

Expected: PASS. `process_metrics` already accepts the parameter — this test
exists to pin the arithmetic before the call site changes, so a later refactor
cannot quietly restore the hardcoded rate.

- [ ] **Step 3: Write the failing route test**

Route behaviour is tested through a Flask test client in `tests/web/`, not in
`tests/unit/`. Create `tests/web/test_metrics_auger_rate.py`:

```python
"""The legacy /metrics page must estimate pellet usage from the grill's own
auger rate. See docs/superpowers/plans/2026-07-28-react-metrics-page.md."""

from unittest.mock import patch

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_metrics_page_passes_the_configured_auger_rate(ds, client):
    from common.datastore_accessors import read_settings, write_settings

    import blueprints.metrics.routes as metrics_routes

    settings = read_settings()
    settings["globals"]["augerrate"] = 0.9
    write_settings(settings)

    #  Patched on the IMPORTING module's own globals: the route bound the name
    #  at import time, so patching common.common would leave it pointing at the
    #  real function and the assertion would never fire.
    with patch.object(metrics_routes, "process_metrics", return_value=[]) as spy:
        assert client.get("/metrics/").status_code == 200

    assert spy.call_args.kwargs["augerrate"] == 0.9
```

- [ ] **Step 4: Run it to verify it fails**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/web/test_metrics_auger_rate.py -q
```

Expected: FAIL — `KeyError: 'augerrate'`, because the route calls
`process_metrics(read_all_metrics())` with no keyword.

- [ ] **Step 5: Fix the call site**

In `blueprints/metrics/routes.py`, replace

```python
    metrics_data = process_metrics(read_all_metrics())
```

with

```python
    #  The grill's own auger rate, not process_metrics' 0.3 g/s default. The
    #  setting has been writable since blueprints/settings/routes.py:679 and is
    #  what the dashboard's estimate already uses (common/app.py:175); this page
    #  was the one consumer still reading a stranger's auger.
    metrics_data = process_metrics(read_all_metrics(), augerrate=settings["globals"]["augerrate"])
```

- [ ] **Step 6: Run both new tests plus the metrics regression net**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/web/test_metrics_auger_rate.py \
  tests/unit/common/test_common_metrics.py \
  tests/web/test_page_smallpages.py -q
```

Expected: all PASS.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format blueprints/metrics/routes.py tests/web/test_metrics_auger_rate.py tests/unit/common/test_common_metrics.py
jj new
jj describe --stdin <<'EOF'
fix(metrics): estimate pellet usage from the configured auger rate

process_metrics defaults to 0.3 g/s and the route called it with one
argument, so the metrics page reported pellet usage for a grill that was
not this one whenever settings.globals.augerrate had been tuned.
EOF
```

---

### Task 2: `GET /api/metrics`

**Files:**
- Create: `blueprints/api_metrics/__init__.py`
- Create: `blueprints/api_metrics/routes.py`
- Create: `tests/web/test_api_metrics.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: Task 1's ruling on `augerrate`.
- Produces: `GET /api/metrics` →
  `200 {"data": {"metrics": [...], "units": "F"|"C", "augerrate": float}, "result": "OK", "message": null}`.
  Task 4 types this payload; Task 9 stubs it.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_metrics.py`:

```python
"""The JSON metrics surface the React /metrics page reads.

Read-only by construction: this blueprint has no POST, so nothing here can
change the grill. See docs/superpowers/plans/2026-07-28-react-metrics-page.md.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


START = 1_700_000_000_000


def seed(mode="Smoke", augerontime=100, duration_ms=60_000):
    """Insert one metrics row with a deterministic span.

    append_metric stamps its OWN starttime (time.time() * 1000) and id, so
    those two cannot be seeded through it -- they are written back afterwards
    with update_metrics, which amends the last row in place. Seeding them in
    the dict handed to append_metric looks like it works and does nothing.
    """
    from common.datastore_accessors import append_metric, update_metrics
    from common.defaults import default_metrics

    row = default_metrics()
    row["mode"] = mode
    row["augerontime"] = augerontime
    row["primary_setpoint"] = 225
    append_metric(row)
    update_metrics({"starttime": START, "endtime": 0 if duration_ms == 0 else START + duration_ms})


def test_listing_is_empty_when_nothing_has_been_recorded(ds, client):
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    body = client.get("/api/metrics").get_json()
    assert body["result"] == "OK"
    assert body["data"]["metrics"] == []


def test_listing_returns_the_processed_record(ds, client):
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    seed()

    body = client.get("/api/metrics").get_json()
    (record,) = body["data"]["metrics"]

    assert record["mode"] == "Smoke"
    assert record["primary_setpoint"] == 225
    #  The derived columns process_metrics writes, which is the whole reason
    #  this endpoint does not simply return read_all_metrics().
    #
    #  "60 s", not "1 m 0 s": the minute branch is `if seconds > 60`, so a span
    #  of exactly 60 000 ms falls to the seconds form. Verified live.
    assert record["timeinmode"] == "60 s"
    assert record["augerontime_c"] == "100 s"
    assert record["estusage_m"] == "30 grams"
    assert record["estusage_i"].endswith("ounces)")
    #  bool, not 0/1: _metrics_row_to_dict coerces it, and the TypeScript type
    #  in web-react/src/helpers/metrics/metricsTypes.ts is written from this.
    assert record["smokeplus"] is True


def test_a_running_mode_reports_endtime_c_as_zero(ds, client):
    """The one non-obvious member of the payload.

    process_metrics writes a "%H:%M:%S" STRING into endtime_c for a finished
    mode and the integer 0 for a running one. The React client types it
    `string | number` because of this; pinned here so the union cannot be
    "simplified" away on either side without a red test.
    """
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    seed(duration_ms=0)

    (record,) = client.get("/api/metrics").get_json()["data"]["metrics"]
    assert record["endtime_c"] == 0
    assert record["timeinmode"] == "Active"


def test_listing_carries_units_and_auger_rate(ds, client):
    from common.datastore_accessors import read_settings, write_settings

    settings = read_settings()
    settings["globals"]["units"] = "C"
    settings["globals"]["augerrate"] = 0.9
    write_settings(settings)

    data = client.get("/api/metrics").get_json()["data"]
    assert data["units"] == "C"
    assert data["augerrate"] == 0.9


def test_estimates_use_the_configured_auger_rate(ds, client):
    from common.datastore_accessors import flush_metrics, read_settings, write_settings

    flush_metrics()
    settings = read_settings()
    settings["globals"]["augerrate"] = 0.6
    write_settings(settings)
    seed()

    (record,) = client.get("/api/metrics").get_json()["data"]["metrics"]
    assert record["estusage_m"] == "60 grams"


def test_the_generic_api_catchall_does_not_swallow_this_path(ds, client):
    """blueprints/api registers /api/<action> for GET and POST, which matches
    the literal path /api/metrics.

    Werkzeug sorts static rules ahead of converter rules, so the specific rule
    should win -- but blueprints/api_admin/routes.py's module docstring records
    a case where a method mismatch made a request fall through to that
    catch-all and 404 from somewhere else entirely. Assert on the endpoint
    name, not just the status: a 200 from the catch-all would pass a status
    check and return a completely different body.
    """
    with flask_app.test_request_context("/api/metrics"):
        from flask import request

        assert request.endpoint == "api_metrics_bp.metrics_listing"


def test_the_surface_registers_no_write():
    """No POST reaches this blueprint. If one ever does, it must be a
    deliberate decision with its own test, not an inherited `methods` list.

    Asserted against the url_map rather than by POSTing: blueprints/api's
    /api/<action> rule accepts POST and would answer a POST to /api/metrics
    with something of its own, so a status check here would be measuring that
    blueprint's behaviour, not this one's.
    """
    writes = [
        rule
        for rule in flask_app.url_map.iter_rules()
        if rule.endpoint.startswith("api_metrics_bp.") and "POST" in rule.methods
    ]
    assert writes == []
```

- [ ] **Step 2: Run them to verify they fail**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_metrics.py -q
```

Expected: FAIL — the blueprint does not exist, so `/api/metrics` reaches
`blueprints/api`'s catch-all and the payload has no `data.metrics`.

- [ ] **Step 3: Create the blueprint package**

`blueprints/api_metrics/__init__.py`:

```python
from flask import Blueprint

api_metrics_bp = Blueprint("api_metrics_bp", __name__, url_prefix="/api/metrics")

from . import routes  # noqa: E402,F401
```

- [ ] **Step 4: Write the route**

`blueprints/api_metrics/routes.py`:

```python
"""JSON endpoints for PiFire's metrics surface.

Read-only, and deliberately so: there is nothing on a metrics page to write.
The blueprint registers no POST at all rather than registering one that
refuses, so there is no door to leave unlocked later.

Why a new blueprint rather than adding JSON to blueprints/metrics: that one
answers `/metrics/<action>` for both POST and GET and dispatches on a path
segment, so `export` and any future action share a rule with the page itself.
New code gets one rule per thing it does.
"""

import datetime

from flask import jsonify, send_file

from common.app import api_response, prepare_metrics_csv
from common.common import process_metrics
from common.datastore_accessors import read_all_metrics, read_settings

from . import api_metrics_bp


def processed_metrics(settings):
    """Every metrics record, with process_metrics' derived columns applied.

    The derivation stays server-side rather than being re-implemented in the
    client: process_metrics is the only definition of what "60 s" or
    "30 grams" means, and a second one in TypeScript would be a second answer.
    """
    return process_metrics(read_all_metrics(), augerrate=settings["globals"]["augerrate"])


@api_metrics_bp.route("", methods=["GET"])
@api_metrics_bp.route("/", methods=["GET"])
def metrics_listing():
    """The whole metrics table, in insertion order.

    Not paginated. The table is capped server-side and a metrics record is a
    handful of scalars, so the whole thing is smaller than one history window.

    `units` and `augerrate` ride along because the client renders both: the
    temperature suffix on setpoint rows, and the auger rate as the stated
    assumption behind every pellet-usage estimate on the page.
    """
    settings = read_settings()
    return jsonify(
        api_response(
            "OK",
            None,
            {
                "metrics": processed_metrics(settings),
                "units": settings["globals"]["units"],
                "augerrate": settings["globals"]["augerrate"],
            },
        )
    ), 200
```

- [ ] **Step 5: Register it**

In `app.py`, beside the other API blueprint imports (near line 87):

```python
from blueprints.api_metrics import api_metrics_bp
```

and beside the other registrations (near line 110):

```python
app.register_blueprint(api_metrics_bp, url_prefix="/api/metrics")
```

- [ ] **Step 6: Run the tests**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_metrics.py -q
```

Expected: all PASS.

- [ ] **Step 7: Negative control on the routing pin**

Temporarily comment out the `app.register_blueprint(api_metrics_bp, ...)` line
and re-run. `test_the_generic_api_catchall_does_not_swallow_this_path` must
fail. Restore the line. A routing assertion that passes with the blueprint
unregistered is asserting nothing.

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_metrics/ tests/web/test_api_metrics.py app.py
jj new
jj describe --stdin <<'EOF'
feat(api_metrics): read-only JSON listing of the metrics table

GET /api/metrics returns process_metrics' output in the standard
api_response envelope, with the grill's units and auger rate alongside
so the client can state the assumption behind every usage estimate.
EOF
```

---

### Task 3: `GET /api/metrics/export`

**Files:**
- Modify: `blueprints/api_metrics/routes.py`
- Modify: `tests/web/test_api_metrics.py`

**Interfaces:**
- Consumes: Task 2's `processed_metrics(settings)`.
- Produces: `GET /api/metrics/export` → `text/csv` attachment named
  `<YYYYmmdd-HHMM>-PiFire-Metrics-Export.csv`. Task 4 builds the href for it;
  no client code ever fetches it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_api_metrics.py`:

```python
def test_export_streams_a_csv_attachment(ds, client):
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    seed()

    resp = client.get("/api/metrics/export")
    assert resp.status_code == 200
    disposition = resp.headers.get("Content-Disposition", "")
    assert "attachment" in disposition
    #  Werkzeug leaves the filename UNQUOTED when it needs no quoting, so this
    #  is a containment check: asserting a trailing quote fails on a header
    #  that is perfectly correct.
    assert "-PiFire-Metrics-Export.csv" in disposition
    #  Once, not twice. The Flask route hands prepare_metrics_csv a name that
    #  already ends in -PiFire-Metrics-Export, and the helper appends its own.
    assert disposition.count("PiFire-Metrics-Export") == 1

    body = resp.get_data(as_text=True)
    #  The header row is metrics_items' keys, in order.
    assert body.splitlines()[0].startswith("id, starttime, starttime_c,")
    assert "Smoke" in body


def test_export_of_an_empty_table_says_so(ds, client):
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    resp = client.get("/api/metrics/export")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True).strip() == "No Data"


def test_export_carries_the_derived_columns(ds, client):
    """prepare_metrics_csv writes every metrics_items key, so the export
    inherits process_metrics' derived values -- which is why the route exports
    processed_metrics() and not read_all_metrics()."""
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    seed()

    body = client.get("/api/metrics/export").get_data(as_text=True)
    assert "30 grams" in body
    assert "60 s" in body


def test_export_takes_no_client_supplied_name(ds, client):
    """The filename is composed from the clock, never from the request.

    prepare_metrics_csv joins its argument under /tmp; common/app.py's
    _export_temp_path basenames it, but the right answer is to never let a
    client string reach it at all. A query string must not change the name.
    """
    from common.datastore_accessors import flush_metrics

    flush_metrics()
    resp = client.get("/api/metrics/export?filename=../../etc/passwd")
    assert resp.status_code == 200
    disposition = resp.headers["Content-Disposition"]
    assert "passwd" not in disposition
    assert "-PiFire-Metrics-Export.csv" in disposition
```

- [ ] **Step 2: Run them to verify they fail**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_metrics.py -q -k export
```

Expected: FAIL — `/api/metrics/export` does not exist yet, so it falls through
to `blueprints/api`'s catch-all.

- [ ] **Step 3: Write the route**

Append to `blueprints/api_metrics/routes.py`:

```python
@api_metrics_bp.route("/export", methods=["GET"])
def metrics_export():
    """The whole table as a CSV attachment.

    The filename is composed here from the clock and NOTHING else: no request
    value reaches prepare_metrics_csv, which joins its argument under /tmp.
    common/app.py's _export_temp_path basenames what it is given, but the
    request is not the place to find out whether that held.

    Exports processed_metrics() rather than read_all_metrics() so the CSV
    carries the same derived columns the page shows -- an export that
    disagreed with the screen would be worse than no export.
    """
    settings = read_settings()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    path = prepare_metrics_csv(processed_metrics(settings), stamp)
    return send_file(path, mimetype="text/csv", as_attachment=True, max_age=0)
```

- [ ] **Step 4: Run the whole module**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_metrics.py -q
```

Expected: all PASS.

- [ ] **Step 5: Negative control**

Change `stamp` to `request.args.get("filename", stamp)` and add
`from flask import request`. `test_export_takes_no_client_supplied_name` must
fail. Revert both edits.

- [ ] **Step 6: Full backend gate**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: green, with the new tests added to the count. Report the number.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_metrics/routes.py tests/web/test_api_metrics.py
jj new
jj describe --stdin <<'EOF'
feat(api_metrics): CSV export of the processed metrics table

The filename is composed from the clock and nothing else -- no request
value reaches prepare_metrics_csv, which joins its argument under /tmp.
EOF
```

---

## Slice B — the React page

### Task 4: The typed client

**Files:**
- Create: `web-react/src/helpers/metrics/metricsTypes.ts`
- Create: `web-react/src/helpers/metrics/metricsApi.ts`
- Create: `web-react/src/helpers/metrics/metricsApi.test.ts`

**Interfaces:**
- Consumes: Task 2's and Task 3's endpoints.
- Produces:
  - `interface MetricRecord` — every field named below.
  - `interface MetricsPayload { metrics: MetricRecord[]; units: string; augerrate: number }`
  - `interface MetricsResult { ok: boolean; status: number; message: string; data: MetricsPayload | null }`
  - `fetchMetrics(baseUrl?: string): Promise<MetricsResult>`
  - `metricsExportUrl(baseUrl?: string): string`

  Tasks 5–7 import all five.

- [ ] **Step 1: Confirm the shape against a live payload**

Do **not** skip this and do **not** type from `common/defaults.py`. With a
backend already running (`gunicorn` only — never `control.py`):

```
curl -s localhost:5000/api/metrics | head -c 2000
```

If no backend is running, start one:

```
cd /home/dannyb/sources/PiFire && \
  .venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 app:app > "$SCRATCH/gunicorn.log" 2>&1 &
```

Confirm every field in the table under **Verified Facts › The wire shape** —
especially that `endtime_c` is `0` on a running record and a string otherwise.
If the live payload disagrees with that table, **stop and report it**; the type
follows the payload, not this plan.

- [ ] **Step 2: Write the failing tests**

Create `web-react/src/helpers/metrics/metricsApi.test.ts`:

The fetch-stubbing idiom is `src/helpers/admin/adminApi.test.ts`'s, verbatim:
one module-level `rs.fn()` installed with `rs.stubGlobal("fetch", …)` and reset
in `beforeEach`, returning a hand-built `{ok, status, json}` object rather than
a real `Response`. Do not invent a different one.

```ts
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fetchMetrics, metricsExportUrl } from "./metricsApi";

const fetchMock = rs.fn();
rs.stubGlobal("fetch", fetchMock);

function envelope(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

//  "60 s", not "1 m 0 s": process_metrics' minute branch is `if seconds > 60`,
//  so a span of exactly 60 000 ms reports the seconds form. Taken from a live
//  /api/metrics response, not from common/defaults.py's metrics_items.
const RECORD = {
  id: "9f2c1b",
  starttime: 1_700_000_000_000,
  starttime_c: "12:00:00",
  endtime: 1_700_000_060_000,
  endtime_c: "12:01:00",
  timeinmode: "60 s",
  mode: "Smoke",
  augerontime: 100,
  augerontime_c: "100 s",
  estusage_m: "30 grams",
  estusage_i: "0.07 pounds (1.06 ounces)",
  fanontime: 60,
  fanontime_c: "60 s",
  smokeplus: true,
  primary_setpoint: 225,
  smart_start_profile: 2,
  startup_temp: 160,
  p_mode: 2,
  auger_cycle_time: 20,
  pellet_level_start: 90,
  pellet_level_end: 85,
  pellet_brand_type: "Lumber Jack Hickory",
};

const PAYLOAD = { metrics: [RECORD], units: "F", augerrate: 0.3 };

beforeEach(() => {
  fetchMock.mockReset();
});

describe("fetchMetrics", () => {
  it("unwraps the envelope", async () => {
    fetchMock.mockResolvedValue(envelope(200, { result: "OK", message: null, data: PAYLOAD }));
    const result = await fetchMetrics("");
    expect(result.ok).toBe(true);
    expect(result.data?.units).toBe("F");
    expect(result.data?.metrics[0].mode).toBe("Smoke");
  });

  it("reads the endpoint relative to the base url", async () => {
    fetchMock.mockResolvedValue(envelope(200, { result: "OK", message: null, data: PAYLOAD }));
    await fetchMetrics("http://grill.local");
    expect(fetchMock.mock.calls[0][0]).toBe("http://grill.local/api/metrics");
    //  A bare GET: no second argument, so no method, headers or body.
    expect(fetchMock.mock.calls[0][1]).toBeUndefined();
  });

  it("reports a server error instead of throwing", async () => {
    fetchMock.mockResolvedValue(envelope(500, { result: "Error", message: "boom", data: null }));
    const result = await fetchMetrics("");
    expect(result.ok).toBe(false);
    expect(result.status).toBe(500);
    expect(result.message).toBe("boom");
  });

  it("survives a body that is not JSON", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    });
    const result = await fetchMetrics("");
    expect(result.ok).toBe(false);
    expect(result.message).toBe("HTTP 502");
  });

  it("reports a dropped connection as status 0", async () => {
    fetchMock.mockRejectedValue(new Error("Failed to fetch"));
    const result = await fetchMetrics("");
    expect(result).toEqual({ ok: false, status: 0, message: "Failed to fetch", data: null });
  });

  it("refuses an OK status carrying an Error envelope", async () => {
    //  common/app.py's api_response puts the verdict in the BODY, so a 200 is
    //  not on its own a success. Pinned because the whole client branches on
    //  `ok` and a 200/Error would otherwise render as data.
    fetchMock.mockResolvedValue(envelope(200, { result: "Error", message: "nope", data: null }));
    expect((await fetchMetrics("")).ok).toBe(false);
  });
});

describe("metricsExportUrl", () => {
  it("points at the export endpoint", () => {
    expect(metricsExportUrl("")).toBe("/api/metrics/export");
    expect(metricsExportUrl("http://grill.local")).toBe("http://grill.local/api/metrics/export");
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

```
cd web-react && bun run test src/helpers/metrics/metricsApi.test.ts
```

Expected: FAIL — cannot resolve `./metricsApi`.

- [ ] **Step 4: Write the types**

Create `web-react/src/helpers/metrics/metricsTypes.ts`:

```ts
// The shape of GET /api/metrics.
//
// Written from a LIVE response, not from common/defaults.py's metrics_items:
// that list declares ("starttime_c", 0) and ("timeinmode", 0), and
// common/common.py's process_metrics overwrites BOTH with strings before the
// record ever leaves the server. A type taken from the defaults would be wrong
// about every field it named.

/**
 * One metrics record: a single mode the grill passed through.
 *
 * The `_c` suffix is PiFire's own: the "converted", human-readable form of the
 * raw column beside it, computed server-side by process_metrics(). Nothing here
 * is recomputed in the client -- there is one definition of what "60 s"
 * means and it is in Python.
 */
export interface MetricRecord {
  id: string | number;
  /** Epoch MILLISECONDS. process_metrics divides by 1000 before formatting. */
  starttime: number;
  /** "%H:%M:%S", server-local. */
  starttime_c: string;
  /** Epoch milliseconds, or 0 while the mode is still running. */
  endtime: number;
  /**
   * "%H:%M:%S" for a finished mode, and the NUMBER 0 for a running one --
   * process_metrics assigns `endtime_c = 0` rather than a placeholder string.
   * Branch on `endtime === 0`; never render this field bare.
   */
  endtime_c: string | number;
  /** "NA" in Stop mode, "Active" while running, else "3 m 20 s" / "45 s". */
  timeinmode: string;
  /** "Startup" | "Smoke" | "Hold" | "Shutdown" | "Stop" | "Monitor" | "Manual"
   * | "Reignite" | "Error", but typed as a string: the mode vocabulary lives in
   * common/modes.py and a record written by an older build can carry anything. */
  mode: string;
  /** Seconds. */
  augerontime: number;
  /** "<seconds> s". */
  augerontime_c: string;
  /** "<grams> grams". */
  estusage_m: string;
  /** "<pounds> pounds (<ounces> ounces)". */
  estusage_i: string;
  fanontime: number;
  /**
   * Declared TEXT in the metrics DDL and never written by process_metrics, so
   * it carries whatever control.py last put there -- including nothing.
   */
  fanontime_c: string | number | null;
  /** Coerced to a real boolean by _metrics_row_to_dict, not left as 0/1. */
  smokeplus: boolean;
  /** The Hold setpoint, in the grill's configured units. NOT `grill_settemp`:
   * that name appears only in the Jinja macro and matches no column. */
  primary_setpoint: number;
  smart_start_profile: number;
  startup_temp: number;
  p_mode: number;
  /** Seconds. */
  auger_cycle_time: number;
  pellet_level_start: number;
  pellet_level_end: number;
  pellet_brand_type: string;
}

export interface MetricsPayload {
  /** Insertion order -- oldest first, as the server read them. */
  metrics: MetricRecord[];
  /** "F" or "C". The suffix on every temperature row. */
  units: string;
  /** Grams per second. The stated assumption behind every usage estimate. */
  augerrate: number;
}

/**
 * How a read finished. Resolves rather than throws, matching
 * helpers/admin/adminApi.ts: the page renders the failure in place and offers a
 * retry, so an exception would only have to be caught and turned back into this.
 */
export interface MetricsResult {
  ok: boolean;
  /** HTTP status, or 0 when the request never reached a server. */
  status: number;
  message: string;
  data: MetricsPayload | null;
}
```

- [ ] **Step 5: Write the client**

Create `web-react/src/helpers/metrics/metricsApi.ts`:

```ts
// Typed client for the /api/metrics surface.
//
// Two GETs and nothing else -- blueprints/api_metrics registers no POST, so
// there is no write here to get wrong.

import type { MetricsPayload, MetricsResult } from "./metricsTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Every record, with process_metrics' derived columns already applied. */
export async function fetchMetrics(baseUrl = BASE_URL): Promise<MetricsResult> {
  try {
    const res = await fetch(`${baseUrl}/api/metrics`);
    //  A body that is not JSON (a proxy's HTML 502, a dropped connection
    //  mid-stream) must not mask the status the caller renders.
    const body = (await res.json().catch(() => ({}))) as {
      result?: string;
      message?: string;
      data?: MetricsPayload | null;
    };
    return {
      ok: res.ok && body.result === "OK",
      status: res.status,
      message: body.message ?? `HTTP ${res.status}`,
      data: body.data ?? null,
    };
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

/** Href for the CSV. Deliberately not fetched: letting the anchor carry it
 * keeps the file out of JS memory and gives the user the browser's own save
 * dialog -- the same reasoning as adminApi.ts's backupDownloadUrl. */
export const metricsExportUrl = (baseUrl = BASE_URL) => `${baseUrl}/api/metrics/export`;
```

- [ ] **Step 6: Run the tests**

```
cd web-react && bun run test src/helpers/metrics/metricsApi.test.ts
```

Expected: 8 passing.

- [ ] **Step 7: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): typed client for the /api/metrics surface

MetricRecord is written from a live payload: metrics_items declares
starttime_c and timeinmode as ints, and process_metrics replaces both
with strings before the record leaves the server. endtime_c is typed
`string | number` because a running mode reports the number 0.
EOF
```

---

### Task 5: The per-mode row table

**Files:**
- Create: `web-react/src/components/metrics/metricFields.ts`
- Create: `web-react/src/components/metrics/metricFields.test.ts`

**Interfaces:**
- Consumes: `MetricRecord` from Task 4.
- Produces:
  - `interface MetricRow { label: string; value: string; converted: string }`
  - `metricRows(record: MetricRecord, units: string): MetricRow[]`
  - `modeAccent(mode: string): "start" | "stop" | "warn" | "neutral"`

  Task 6 imports both functions and the row type.

**Why this is data and not JSX:** the per-mode field sets are the entire content
of `_macro_metrics.html`'s eight macros. Kept as a pure function they are
testable without rendering anything, and the card below stays one table.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/components/metrics/metricFields.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import type { MetricRecord } from "../../helpers/metrics/metricsTypes";
import { metricRows, modeAccent } from "./metricFields";

const BASE: MetricRecord = {
  id: "m1",
  starttime: 1_700_000_000_000,
  starttime_c: "12:00:00",
  endtime: 1_700_000_060_000,
  endtime_c: "12:01:00",
  timeinmode: "60 s",
  mode: "Smoke",
  augerontime: 100,
  augerontime_c: "100 s",
  estusage_m: "30 grams",
  estusage_i: "0.07 pounds (1.06 ounces)",
  fanontime: 60,
  fanontime_c: "60 s",
  smokeplus: true,
  primary_setpoint: 225,
  smart_start_profile: 2,
  startup_temp: 160,
  p_mode: 2,
  auger_cycle_time: 20,
  pellet_level_start: 90,
  pellet_level_end: 85,
  pellet_brand_type: "Lumber Jack Hickory",
};

const labels = (record: MetricRecord, units = "F") =>
  metricRows(record, units).map((row) => row.label);

describe("metricRows", () => {
  it("gives Stop mode only its stop time", () => {
    expect(labels({ ...BASE, mode: "Stop" })).toEqual(["Stop Time"]);
  });

  it("treats Error like Stop, as the Jinja dispatch does", () => {
    expect(labels({ ...BASE, mode: "Error" })).toEqual(["Stop Time"]);
  });

  it("gives Shutdown, Monitor and Manual the three timing rows", () => {
    for (const mode of ["Shutdown", "Monitor", "Manual"]) {
      expect(labels({ ...BASE, mode })).toEqual(["Start Time", "End Time", "Time in Mode"]);
    }
  });

  it("gives Smoke the timing, usage, smoke-plus and startup rows", () => {
    expect(labels({ ...BASE, mode: "Smoke" })).toEqual([
      "Start Time",
      "End Time",
      "Time in Mode",
      "Auger On Time",
      "Estimated Pellet Usage",
      "Smoke Plus",
      "Smart Start Profile",
      "Smart Startup Temp",
      "P Mode",
      "Auger Cycle Time",
    ]);
  });

  it("gives Hold the setpoint instead of the startup rows", () => {
    expect(labels({ ...BASE, mode: "Hold" })).toEqual([
      "Start Time",
      "End Time",
      "Time in Mode",
      "Auger On Time",
      "Estimated Pellet Usage",
      "Smoke Plus",
      "Grill Set Temp",
    ]);
  });

  it("reads the Hold setpoint from primary_setpoint", () => {
    //  _macro_metrics.html reads metric['grill_settemp'], which is not a
    //  column in the metrics table -- Jinja renders the missing key as empty,
    //  so Flask's Hold card has always shown a blank setpoint.
    const rows = metricRows({ ...BASE, mode: "Hold", primary_setpoint: 225 }, "F");
    const row = rows.find((r) => r.label === "Grill Set Temp");
    expect(row).toEqual({ label: "Grill Set Temp", value: "225", converted: "225 F" });
  });

  it("falls back to the Startup row set for an unknown mode", () => {
    //  The Jinja dispatch's final `{% else %}` renders render_reignite, whose
    //  row set is identical to render_startup's.
    expect(labels({ ...BASE, mode: "Prime" })).toEqual(labels({ ...BASE, mode: "Startup" }));
  });

  it("shows an em dash for a mode that has not ended", () => {
    //  What the server really sends for a running mode: endtime 0, endtime_c
    //  the NUMBER 0, and timeinmode the string "Active".
    const running = { ...BASE, mode: "Smoke", endtime: 0, endtime_c: 0, timeinmode: "Active" };
    const rows = metricRows(running, "F");
    expect(rows.find((r) => r.label === "End Time")).toEqual({
      label: "End Time",
      value: "0",
      converted: "—",
    });
    expect(rows.find((r) => r.label === "Time in Mode")).toEqual({
      label: "Time in Mode",
      value: "—",
      converted: "Active",
    });
  });

  it("reports the elapsed milliseconds as the raw Time in Mode", () => {
    const rows = metricRows(BASE, "F");
    expect(rows.find((r) => r.label === "Time in Mode")?.value).toBe("60000");
  });

  it("renders Smoke Plus as Active or Disabled", () => {
    const on = metricRows({ ...BASE, mode: "Smoke", smokeplus: true }, "F");
    const off = metricRows({ ...BASE, mode: "Smoke", smokeplus: false }, "F");
    expect(on.find((r) => r.label === "Smoke Plus")?.converted).toBe("Active");
    expect(off.find((r) => r.label === "Smoke Plus")?.converted).toBe("Disabled");
  });

  it("suffixes the startup temperature with the grill's units", () => {
    const rows = metricRows({ ...BASE, mode: "Startup", startup_temp: 160 }, "C");
    expect(rows.find((r) => r.label === "Smart Startup Temp")?.converted).toBe("160 C");
  });
});

describe("modeAccent", () => {
  it("greens the modes that start a cook", () => {
    expect(modeAccent("Startup")).toBe("start");
  });

  it("reds the modes that end one", () => {
    expect(modeAccent("Stop")).toBe("stop");
    expect(modeAccent("Error")).toBe("stop");
  });

  it("warns on Reignite", () => {
    expect(modeAccent("Reignite")).toBe("warn");
  });

  it("leaves everything else neutral", () => {
    expect(modeAccent("Smoke")).toBe("neutral");
    expect(modeAccent("Prime")).toBe("neutral");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```
cd web-react && bun run test src/components/metrics/metricFields.test.ts
```

Expected: FAIL — cannot resolve `./metricFields`.

- [ ] **Step 3: Write the module**

Create `web-react/src/components/metrics/metricFields.ts`:

```ts
// Which rows a metrics card shows, per mode.
//
// This is blueprints/metrics/templates/metrics/_macro_metrics.html's eight
// macros as data. They are eight near-copies of one table differing only in
// which rows they include, so they collapse to one row builder and a per-mode
// row list -- and as data they are testable without rendering anything.

import type { MetricRecord } from "../../helpers/metrics/metricsTypes";

/** One row of a card: the label, the raw column, and its readable form. */
export interface MetricRow {
  label: string;
  value: string;
  converted: string;
}

/** Shown wherever a value does not exist yet, matching the Jinja `--`. */
const NONE = "—";

/** The colour band across the top of a card. */
export type ModeAccent = "start" | "stop" | "warn" | "neutral";

/** Ported from index.html's dispatch, which picks a macro whose card-header
 * class is bg-success (Startup), bg-danger (Stop, Error), bg-warning
 * (Reignite and the else branch) or bg-secondary (everything else). */
export function modeAccent(mode: string): ModeAccent {
  if (mode === "Startup") return "start";
  if (mode === "Stop" || mode === "Error") return "stop";
  if (mode === "Reignite") return "warn";
  return "neutral";
}

/** Which labels each mode shows, in order. `startup` is also the fallback:
 * index.html's dispatch ends in `{% else %}{{ render_reignite(...) }}`, whose
 * row set is identical to render_startup's. */
const ROW_SETS: Record<string, readonly string[]> = {
  stop: ["Stop Time"],
  timing: ["Start Time", "End Time", "Time in Mode"],
  hold: [
    "Start Time",
    "End Time",
    "Time in Mode",
    "Auger On Time",
    "Estimated Pellet Usage",
    "Smoke Plus",
    "Grill Set Temp",
  ],
  smoke: [
    "Start Time",
    "End Time",
    "Time in Mode",
    "Auger On Time",
    "Estimated Pellet Usage",
    "Smoke Plus",
    "Smart Start Profile",
    "Smart Startup Temp",
    "P Mode",
    "Auger Cycle Time",
  ],
  startup: [
    "Start Time",
    "End Time",
    "Time in Mode",
    "Auger On Time",
    "Estimated Pellet Usage",
    "Smart Start Profile",
    "Smart Startup Temp",
    "P Mode",
    "Auger Cycle Time",
  ],
};

const MODE_ROWS: Record<string, keyof typeof ROW_SETS> = {
  Stop: "stop",
  Error: "stop",
  Shutdown: "timing",
  Monitor: "timing",
  Manual: "timing",
  Hold: "hold",
  Smoke: "smoke",
  Startup: "startup",
  Reignite: "startup",
};

/** The rows for one record, already stringified for display.
 *
 * `units` is the grill's configured unit letter, appended to the two
 * temperature rows exactly as the Jinja macros append it. */
export function metricRows(record: MetricRecord, units: string): MetricRow[] {
  const running = record.endtime === 0;

  const build: Record<string, () => MetricRow> = {
    "Stop Time": () => ({
      label: "Stop Time",
      value: String(record.starttime),
      converted: String(record.starttime_c),
    }),
    "Start Time": () => ({
      label: "Start Time",
      value: String(record.starttime),
      converted: String(record.starttime_c),
    }),
    "End Time": () => ({
      label: "End Time",
      value: String(record.endtime),
      //  endtime_c is the NUMBER 0 while a mode runs, not a placeholder
      //  string, so this branches on endtime rather than rendering it bare.
      converted: running ? NONE : String(record.endtime_c),
    }),
    "Time in Mode": () => ({
      label: "Time in Mode",
      value: running ? NONE : String(record.endtime - record.starttime),
      converted: record.timeinmode,
    }),
    "Auger On Time": () => ({
      label: "Auger On Time",
      value: String(record.augerontime),
      converted: record.augerontime_c,
    }),
    "Estimated Pellet Usage": () => ({
      label: "Estimated Pellet Usage",
      value: record.estusage_m,
      converted: record.estusage_i,
    }),
    "Smoke Plus": () => ({
      label: "Smoke Plus",
      value: String(record.smokeplus),
      converted: record.smokeplus ? "Active" : "Disabled",
    }),
    "Smart Start Profile": () => ({
      label: "Smart Start Profile",
      value: String(record.smart_start_profile),
      converted: String(record.smart_start_profile),
    }),
    "Smart Startup Temp": () => ({
      label: "Smart Startup Temp",
      value: String(record.startup_temp),
      converted: `${record.startup_temp} ${units}`,
    }),
    "P Mode": () => ({
      label: "P Mode",
      value: String(record.p_mode),
      converted: String(record.p_mode),
    }),
    "Auger Cycle Time": () => ({
      label: "Auger Cycle Time",
      value: String(record.auger_cycle_time),
      converted: `${record.auger_cycle_time}s`,
    }),
    //  From primary_setpoint. _macro_metrics.html reads metric['grill_settemp'],
    //  which matches no column in the metrics table -- Jinja renders a missing
    //  key as empty, so Flask's Hold card has always shown a blank setpoint.
    "Grill Set Temp": () => ({
      label: "Grill Set Temp",
      value: String(record.primary_setpoint),
      converted: `${record.primary_setpoint} ${units}`,
    }),
  };

  const set = ROW_SETS[MODE_ROWS[record.mode] ?? "startup"];
  return set.map((label) => build[label]());
}
```

- [ ] **Step 4: Run the tests**

```
cd web-react && bun run test src/components/metrics/metricFields.test.ts
```

Expected: 15 passing.

- [ ] **Step 5: Negative control on the setpoint fix**

Temporarily change the `Grill Set Temp` builder to read a non-existent
`grill_settemp` (cast through `unknown` to satisfy the compiler). Exactly one
test — `reads the Hold setpoint from primary_setpoint` — must fail. Revert.

- [ ] **Step 6: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): per-mode row table for metrics records

_macro_metrics.html's eight macros as data: they are near-copies of one
table differing only in which rows they carry. The Hold card's setpoint
now reads primary_setpoint -- the Jinja macro reads grill_settemp, which
matches no column, so that row has always rendered blank.
EOF
```

---

### Task 6: `MetricCard`

**Files:**
- Create: `web-react/src/components/metrics/MetricCard.tsx`
- Create: `web-react/src/components/metrics/MetricCard.test.tsx`
- Create: `web-react/src/components/metrics/metrics.css`

**Interfaces:**
- Consumes: `MetricRecord` (Task 4), `metricRows` / `modeAccent` (Task 5).
- Produces: `MetricCard({ record, units }: { record: MetricRecord; units: string })`.
  Task 7 renders one per record.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/components/metrics/MetricCard.test.tsx`:

```tsx
import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MetricRecord } from "../../helpers/metrics/metricsTypes";
import { MetricCard } from "./MetricCard";

const RECORD: MetricRecord = {
  id: "m1",
  starttime: 1_700_000_000_000,
  starttime_c: "12:00:00",
  endtime: 1_700_000_060_000,
  endtime_c: "12:01:00",
  timeinmode: "60 s",
  mode: "Smoke",
  augerontime: 100,
  augerontime_c: "100 s",
  estusage_m: "30 grams",
  estusage_i: "0.07 pounds (1.06 ounces)",
  fanontime: 60,
  fanontime_c: "60 s",
  smokeplus: true,
  primary_setpoint: 225,
  smart_start_profile: 2,
  startup_temp: 160,
  p_mode: 2,
  auger_cycle_time: 20,
  pellet_level_start: 90,
  pellet_level_end: 85,
  pellet_brand_type: "Lumber Jack Hickory",
};

describe("MetricCard", () => {
  it("names the mode in its heading", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("heading", { name: "Smoke Mode" })).toBeInTheDocument();
  });

  it("labels the table for assistive technology", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("table", { name: "Smoke Mode metrics" })).toBeInTheDocument();
  });

  it("renders the three Flask columns", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("columnheader", { name: "Metric" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Value" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Converted" })).toBeInTheDocument();
  });

  it("renders every row the mode calls for", () => {
    render(<MetricCard record={RECORD} units="F" />);
    expect(screen.getByRole("rowheader", { name: "Estimated Pellet Usage" })).toBeInTheDocument();
    expect(screen.getByText("0.07 pounds (1.06 ounces)")).toBeInTheDocument();
  });

  it("carries the mode accent as a class", () => {
    const { container } = render(<MetricCard record={{ ...RECORD, mode: "Stop" }} units="F" />);
    expect(container.querySelector(".pf-metrics-card")).toHaveClass("stop");
  });

  it("keeps the raw record collapsed until asked", async () => {
    //  Asserted on the `open` ATTRIBUTE, not on visibility. jsdom has no UA
    //  stylesheet rule hiding a closed <details>' children, so
    //  `.not.toBeVisible()` would pass on a card that renders the JSON
    //  expanded -- it would be measuring nothing.
    const { container } = render(<MetricCard record={RECORD} units="F" />);
    const details = container.querySelector("details");
    expect(details).not.toHaveAttribute("open");

    await userEvent.click(screen.getByText("Raw Data"));
    expect(details).toHaveAttribute("open");
  });

  it("shows every field in the raw record, including the ones no table row names", () => {
    render(<MetricCard record={RECORD} units="F" />);
    //  fanontime, pellet_level_start/_end and pellet_brand_type are collected
    //  by control.py and named by no macro in _macro_metrics.html. The
    //  disclosure is where they are reachable, exactly as in Flask.
    const raw = screen.getByTestId("metric-raw-m1").textContent ?? "";
    expect(raw).toContain('"fanontime": 60');
    expect(raw).toContain('"pellet_level_end": 85');
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```
cd web-react && bun run test src/components/metrics/MetricCard.test.tsx
```

Expected: FAIL — cannot resolve `./MetricCard`.

- [ ] **Step 3: Write the component**

Create `web-react/src/components/metrics/MetricCard.tsx`:

```tsx
import type { MetricRecord } from "../../helpers/metrics/metricsTypes";
import { metricRows, modeAccent } from "./metricFields";
import "./metrics.css";

/**
 * One metrics record, as Flask's _macro_metrics.html renders one: a mode-tinted
 * header, the Metric/Value/Converted table, and the raw record behind a
 * disclosure.
 *
 * The raw record is a <details>, not a button over conditional JSX. Flask uses
 * a Bootstrap collapse, which keeps the content in the DOM -- and so does
 * <details>, which means the browser's own find-in-page reaches a field the
 * table does not name (fanontime, the pellet levels, the brand) without the
 * user having to open every card first.
 */
export function MetricCard({ record, units }: { record: MetricRecord; units: string }) {
  const rows = metricRows(record, units);
  const heading = `${record.mode} Mode`;
  const headingId = `metrics-card-${record.id}`;

  return (
    <section className={`pf-metrics-card ${modeAccent(record.mode)}`} aria-labelledby={headingId}>
      <h2 className="pf-metrics-card-title" id={headingId}>
        {heading}
      </h2>

      <table className="pf-metrics-table" aria-label={`${heading} metrics`}>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Value</th>
            <th scope="col">Converted</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td className="pf-metrics-raw-value">{row.value}</td>
              <td>{row.converted}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <details className="pf-metrics-details">
        <summary className="pf-metrics-summary">Raw Data</summary>
        <pre className="pf-metrics-json" data-testid={`metric-raw-${record.id}`}>
          {JSON.stringify(record, null, 2)}
        </pre>
      </details>
    </section>
  );
}
```

- [ ] **Step 4: Write the stylesheet**

Create `web-react/src/components/metrics/metrics.css`.

**Write only the second half in this task** — everything from `.pf-metrics-card`
onwards. The page-level rules (`.pf-metrics`, `.pf-metrics-header`,
`.pf-metrics-title`, `.pf-metrics-rate`, `.pf-metrics-btn`,
`.pf-metrics-empty*`, `.pf-metrics-list`) have no markup until Task 7, and
`src/styleCoverage.test.ts` fails on a rule with no consumer. They are written
out here in full so Task 7 Step 4 can copy them verbatim.

```css
@reference "../../theme.css";

/* The metrics page: one card per mode the grill passed through.

   .pf-shell-main is flex:1/min-height:0/overflow:auto (shell.css), so the page
   below scrolls inside that pane rather than growing the document. Target
   viewport 1280x720. */
.pf-metrics {
  @apply flex h-full flex-col gap-[16px] overflow-auto p-[16px] text-text;
  box-sizing: border-box;
}
.pf-metrics-header {
  @apply flex flex-wrap items-baseline gap-[16px];
}
.pf-metrics-title {
  @apply m-0 text-text;
  font: 600 22px "Barlow";
}
/* The stated assumption behind every pellet-usage estimate on the page. An
   estimate whose constant is invisible is a number nobody can check. */
.pf-metrics-rate {
  @apply text-label uppercase;
  font: 600 12px "Barlow";
  letter-spacing: 2px;
}
/* Modelled on .pf-admin-btn (admin.css), which is itself modelled on
   .pf-modal-btn. An anchor rather than a button: the CSV is a download. */
.pf-metrics-btn {
  @apply cursor-pointer rounded-[12px] bg-inset px-[14px] py-[8px] text-text no-underline;
  border: 1px solid var(--color-card-border);
  font: 700 14px "Barlow";
  text-align: center;
}
.pf-metrics-empty {
  @apply rounded-card bg-card p-[24px] text-center text-text;
  border: 1px solid var(--color-card-border);
}
.pf-metrics-empty-title {
  @apply m-0 mb-[8px] text-text;
  font: 600 18px "Barlow";
}
.pf-metrics-empty-note {
  @apply m-0 text-label;
  font: 400 14px "Barlow";
}
.pf-metrics-list {
  @apply flex flex-col gap-[16px];
}

/* One record. The accent band across the top replaces Bootstrap's tinted
   card-header (bg-success / bg-danger / bg-warning / bg-secondary): the same
   four-way signal, without a block of saturated colour behind white text. */
.pf-metrics-card {
  @apply rounded-card bg-card p-[16px] text-text;
  border: 1px solid var(--color-card-border);
  border-top: 3px solid var(--color-label);
}
.pf-metrics-card.start {
  border-top-color: var(--color-accent);
}
.pf-metrics-card.stop {
  border-top-color: var(--color-danger);
}
.pf-metrics-card.warn {
  border-top-color: var(--color-warn);
}
.pf-metrics-card-title {
  @apply m-0 mb-[12px] text-text;
  font: 600 16px "Barlow";
}
.pf-metrics-table {
  @apply w-full text-text;
  border-collapse: collapse;
  font: 400 13px "Barlow";
}
.pf-metrics-table th,
.pf-metrics-table td {
  @apply px-[8px] py-[6px];
  text-align: left;
  border-bottom: 1px solid var(--color-card-border);
}
.pf-metrics-table thead th {
  @apply text-label uppercase;
  font: 600 11px "Barlow";
  letter-spacing: 1px;
}
/* The raw column: epoch milliseconds and second counts, which line up only in
   a tabular face. */
.pf-metrics-raw-value {
  font-variant-numeric: tabular-nums;
}
.pf-metrics-details {
  @apply mt-[12px];
}
.pf-metrics-summary {
  @apply cursor-pointer text-label;
  font: 600 12px "Barlow";
}
.pf-metrics-json {
  @apply mt-[8px] overflow-x-auto rounded-card bg-inset p-[12px] text-label;
  font: 400 12px monospace;
}
```

- [ ] **Step 5: Run the tests**

```
cd web-react && bun run test src/components/metrics/MetricCard.test.tsx
```

Expected: 7 passing. If jsdom's `<details>` activation behaviour turns out not
to flip `open` on a summary click, drop that half of the assertion (keep the
"starts closed" half) and say so — Task 9's e2e proves the toggle in a real
browser either way.

- [ ] **Step 6: Gates and commit**

`bun run test` runs `src/cssCoverage.test.ts` and `src/styleCoverage.test.ts`,
which catch a `pf-metrics-*` class with no rule and a rule with no markup. If
`styleCoverage` fails here, a page-level rule was written early — see Step 4.

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): metrics record card

The Metric/Value/Converted table _macro_metrics.html renders, with the
raw record behind a <details> rather than a Bootstrap collapse -- both
keep the content in the DOM, so find-in-page reaches the fields no row
names.
EOF
```

---

### Task 7: `MetricsPage`

**Files:**
- Create: `web-react/src/components/metrics/MetricsPage.tsx`
- Create: `web-react/src/components/metrics/MetricsPage.test.tsx`
- Modify: `web-react/src/components/metrics/metrics.css` (the page-level rules
  deferred from Task 6)

**Interfaces:**
- Consumes: `fetchMetrics`, `metricsExportUrl` (Task 4); `MetricCard` (Task 6).
- Produces: `MetricsPage()`. Task 8 routes to it.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/components/metrics/MetricsPage.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { render, screen, waitFor } from "@testing-library/react";
import * as actualMetricsApi from "../../helpers/metrics/metricsApi" with { rstest: "importActual" };
import type { MetricsPayload, MetricsResult } from "../../helpers/metrics/metricsTypes";

// The API module is mocked, not `fetch` -- the idiom AdminPage.test.tsx
// established. Stubbed through a lazy wrapper so the hoisted mock factory never
// captures an uninitialised binding, and `metricsExportUrl` stays REAL: the
// href it builds is one of the things under test here, and a stub would let a
// wrong one pass unnoticed.
const fetchMetricsMock = rs.fn();
rs.mock("../../helpers/metrics/metricsApi", () => ({
  ...actualMetricsApi,
  fetchMetrics: (...a: unknown[]) => fetchMetricsMock(...a),
}));

const { MetricsPage } = await import("./MetricsPage");

const RECORD = {
  id: "m1",
  starttime: 1_700_000_000_000,
  starttime_c: "12:00:00",
  endtime: 1_700_000_060_000,
  endtime_c: "12:01:00",
  timeinmode: "60 s",
  mode: "Smoke",
  augerontime: 100,
  augerontime_c: "100 s",
  estusage_m: "30 grams",
  estusage_i: "0.07 pounds (1.06 ounces)",
  fanontime: 60,
  fanontime_c: "60 s",
  smokeplus: true,
  primary_setpoint: 225,
  smart_start_profile: 2,
  startup_temp: 160,
  p_mode: 2,
  auger_cycle_time: 20,
  pellet_level_start: 90,
  pellet_level_end: 85,
  pellet_brand_type: "Lumber Jack Hickory",
};

const ok = (data: MetricsPayload): MetricsResult => ({
  ok: true,
  status: 200,
  message: "",
  data,
});

const payload = (metrics: unknown[], augerrate = 0.3) =>
  ({ metrics, units: "F", augerrate }) as unknown as MetricsPayload;

beforeEach(() => {
  fetchMetricsMock.mockReset();
});

describe("MetricsPage", () => {
  it("renders a card per record once the read lands", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([RECORD, { ...RECORD, id: "m2", mode: "Hold" }])));
    render(<MetricsPage />);

    await waitFor(() => expect(screen.getByRole("heading", { name: "Smoke Mode" })).toBeVisible());
    expect(screen.getByRole("heading", { name: "Hold Mode" })).toBeVisible();
  });

  it("offers the CSV as a download link, not a button", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([RECORD])));
    render(<MetricsPage />);

    const link = await screen.findByRole("link", { name: "Download CSV Data" });
    expect(link).toHaveAttribute("href", "/api/metrics/export");
  });

  it("states the auger rate the estimates assume", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([RECORD], 0.45)));
    render(<MetricsPage />);
    expect(await screen.findByText("Auger rate: 0.45 g/s")).toBeVisible();
  });

  it("shows Flask's empty state and no CSV link when nothing is recorded", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([])));
    render(<MetricsPage />);

    expect(await screen.findByRole("heading", { name: "No Data" })).toBeVisible();
    expect(screen.getByText("Start the grill to begin populating metrics.")).toBeVisible();
    //  Flask hides the export behind the same `{% if %}`: a CSV of nothing is
    //  a file that says "No Data".
    expect(screen.queryByRole("link", { name: "Download CSV Data" })).toBeNull();
  });

  it("reports a failed read in place", async () => {
    fetchMetricsMock.mockResolvedValue({ ok: false, status: 500, message: "boom", data: null });
    render(<MetricsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("shows a loading note before the first response", () => {
    //  A promise that never settles, so this asserts the loading branch rather
    //  than racing a resolved one to the first paint.
    fetchMetricsMock.mockReturnValue(new Promise(() => {}));
    render(<MetricsPage />);
    expect(screen.getByText("Loading metrics…")).toBeVisible();
  });

  it("reads once on mount and not on a timer", async () => {
    fetchMetricsMock.mockResolvedValue(ok(payload([RECORD])));
    render(<MetricsPage />);
    await screen.findByRole("heading", { name: "Smoke Mode" });
    expect(fetchMetricsMock).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```
cd web-react && bun run test src/components/metrics/MetricsPage.test.tsx
```

Expected: FAIL — cannot resolve `./MetricsPage`.

- [ ] **Step 3: Write the page**

Create `web-react/src/components/metrics/MetricsPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { fetchMetrics, metricsExportUrl } from "../../helpers/metrics/metricsApi";
import type { MetricsPayload } from "../../helpers/metrics/metricsTypes";
import { MetricCard } from "./MetricCard";
import "./metrics.css";

// Same-origin, matching every other module. Deliberately NOT `targetUrl` from
// the shell context: that value is absolute so ConnectionStatus has something
// readable to show, and fetching with it sends every request cross-origin,
// which Flask answers without CORS headers.
const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/**
 * The metrics page: one card per mode the grill has passed through.
 *
 * Read once on mount, never on a timer. A metrics row is written when a mode
 * ENDS, so between transitions there is nothing new to see -- and a mode lasts
 * minutes to hours. The dashboard is where a live reading belongs.
 *
 * Records are shown in the order the server read them (oldest first), matching
 * the CSV the export button hands over. A page and its own export disagreeing
 * about order would make the two impossible to line up.
 */
export function MetricsPage() {
  const [payload, setPayload] = useState<MetricsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchMetrics(BASE_URL).then((result) => {
      if (cancelled) return;
      if (result.ok && result.data) {
        setPayload(result.data);
        setError(null);
      } else {
        setError(result.message);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="pf-metrics">
        <p>Loading metrics…</p>
      </div>
    );
  }

  if (!payload) {
    return (
      <div className="pf-metrics">
        <p className="pf-settings-error-text" role="alert">
          {error ?? "The server did not answer."}
        </p>
      </div>
    );
  }

  const { metrics, units, augerrate } = payload;

  return (
    <div className="pf-metrics">
      <header className="pf-metrics-header">
        <h1 className="pf-metrics-title">Metrics</h1>
        {/* Every "Estimated Pellet Usage" on this page is augerontime x this
            number. Stating it is what makes the estimates checkable, and it
            is the setting the Flask page silently ignored. */}
        <span className="pf-metrics-rate">{`Auger rate: ${augerrate} g/s`}</span>
        {metrics.length > 0 && (
          // An anchor, not a fetch: the file stays out of JS memory and the
          // user gets the browser's own save dialog.
          <a className="pf-metrics-btn" href={metricsExportUrl(BASE_URL)} download>
            Download CSV Data
          </a>
        )}
      </header>

      {metrics.length === 0 ? (
        <div className="pf-metrics-empty">
          <h2 className="pf-metrics-empty-title">No Data</h2>
          <p className="pf-metrics-empty-note">Start the grill to begin populating metrics.</p>
        </div>
      ) : (
        <div className="pf-metrics-list">
          {metrics.map((record) => (
            <MetricCard key={String(record.id)} record={record} units={units} />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add the page-level CSS rules**

Append to `web-react/src/components/metrics/metrics.css` the `.pf-metrics`,
`.pf-metrics-header`, `.pf-metrics-title`, `.pf-metrics-rate`,
`.pf-metrics-btn`, `.pf-metrics-empty`, `.pf-metrics-empty-title`,
`.pf-metrics-empty-note` and `.pf-metrics-list` rules exactly as written in Task
6 Step 4 (they were deferred there so `styleCoverage` had markup for each).

- [ ] **Step 5: Run the tests**

```
cd web-react && bun run test src/components/metrics/MetricsPage.test.tsx
```

Expected: 7 passing.

- [ ] **Step 6: Negative control on the mount-once claim**

Add a `setInterval(reload, 1000)` to the effect and run with fake timers
advanced past a tick. `reads once on mount and not on a timer` must fail.
Revert.

- [ ] **Step 7: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): the /metrics page

One read on mount, never a timer: a metrics row is written when a mode
ENDS, and a mode lasts minutes to hours. The auger rate the estimates
assume is stated in the header -- it is the setting the Flask page
silently ignored.
EOF
```

---

### Task 8: Route it and link to it

**Files:**
- Modify: `web-react/src/components/App.tsx`
- Modify: `web-react/src/components/history/HistoryPage.tsx`
- Modify: `web-react/src/components/history/HistoryPage.test.tsx`

**Interfaces:**
- Consumes: `MetricsPage` (Task 7).
- Produces: the `/metrics` route. Task 9's e2e navigates to it.

- [ ] **Step 1: Write the failing test**

Append to `web-react/src/components/history/HistoryPage.test.tsx`, following the
render helper that file already uses:

```tsx
it("links to the metrics page", async () => {
  //  Ported from blueprints/history/templates/history/index.html:47, the only
  //  link into /metrics anywhere in the Flask tree -- the navbar has never
  //  carried one. Dropping it in the first history port left the React
  //  /metrics unreachable by clicking.
  renderHistoryPage();
  const link = await screen.findByRole("link", { name: "Metrics" });
  expect(link).toHaveAttribute("href", "/metrics");
});
```

If `HistoryPage.test.tsx` has no `renderHistoryPage` helper, use whatever render
call the neighbouring tests in that file use, unchanged.

- [ ] **Step 2: Run it to verify it fails**

```
cd web-react && bun run test src/components/history/HistoryPage.test.tsx
```

Expected: FAIL — no link named Metrics.

- [ ] **Step 3: Add the link**

In `HistoryPage.tsx`, inside the chart section's existing control row (the `div`
that holds the Reset zoom button), add:

```tsx
{/* blueprints/history/templates/history/index.html:47. The ONLY link into
    /metrics in the Flask tree -- templates/base.html's navbar has never had
    one, and React's does not either. */}
<Link className="pf-metrics-btn" to="/metrics">
  Metrics
</Link>
```

and add `import { Link } from "react-router";` to the file's imports plus
`import "../metrics/metrics.css";` so `.pf-metrics-btn` is in the bundle for
this page too.

If importing the metrics stylesheet into the history page reads wrong to the
implementer, the alternative is a `.pf-history-btn` rule in a history
stylesheet — take that instead and adjust the class name here and in the test.
Do not leave a class with no rule: `cssCoverage` fails on it.

- [ ] **Step 4: Add the route**

In `App.tsx`, add the import beside the other page imports:

```tsx
import { MetricsPage } from "./metrics/MetricsPage";
```

and the route inside the `AppShell` children, after `/history`:

```tsx
// Per-mode metrics for the cooks in the history store. Reached from
// /history, matching Flask, where templates/base.html's navbar has no
// Metrics entry and history/index.html:47 is the only link in. No loader:
// the page reads on mount so a failed read is retryable in place.
{ path: "/metrics", element: <MetricsPage /> },
```

- [ ] **Step 5: Run the tests**

```
cd web-react && bun run test src/components/history src/components/App.test.tsx src/components/metrics
```

Expected: all PASS.

- [ ] **Step 6: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): /metrics route and the History link back into it

No navbar entry: templates/base.html has never had one, and
history/index.html:47 is the only link into /metrics in the Flask tree.
That link was dropped by the first history port; it is back.
EOF
```

---

### Task 9: End to end, baselines and closeout

**Files:**
- Create: `web-react/tests/e2e/metrics.spec.ts`
- Modify: `web-react/tests/e2e/apiFixtures.ts`
- Modify: `web-react/tests/e2e/pageSpecs.ts`
- Modify: `web-react/tests/e2e/baselines/*.json` (captured, never hand-edited)
- Modify: `docs/superpowers/backlogs/react-migration-backlog.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing downstream.

- [ ] **Step 1: Start a backend**

`gunicorn` ONLY. Do **not** start `control.py` — it drives the relays.

```bash
cd /home/dannyb/sources/PiFire && \
  .venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 app:app > "$SCRATCH/gunicorn.log" 2>&1 &
curl -s -o /dev/null -w '%{http_code}\n' localhost:5000/api/metrics
```

Expected: `200`.

- [ ] **Step 2: Write the live spec**

Create `web-react/tests/e2e/metrics.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

// The metrics page against the real backend, reading only.
//
// SAFETY: /api/metrics has no write half — blueprints/api_metrics registers no
// POST at all. The guard below is not about that endpoint; it is about the
// destructive doors that live one path segment away on the same server. They
// are aborted at the network boundary and every attempt is RECORDED, because an
// aborted request is otherwise silently swallowed and this spec would keep
// passing while quietly trying to power the grill off.

const WRITE_ROUTES = [
  "**/api/admin/system",
  "**/api/admin/factory-reset",
  "**/api/admin/maintenance",
  "**/api/admin/logs/delete",
];

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
  expect(attempted, "a destructive call escaped this spec").toEqual([]);
});

test.describe("metrics page", () => {
  test("renders the page for whatever the grill has recorded", async ({ page }) => {
    await page.goto("/metrics");
    await expect(page.getByRole("heading", { name: "Metrics", level: 1 })).toBeVisible();
    await expect(page.getByText(/^Auger rate: /)).toBeVisible();

    //  A real machine may legitimately have no metrics at all, so this asserts
    //  the page reached ONE of its two settled states -- never that it is
    //  still loading, which is what a broken read would leave on screen.
    const cards = page.locator(".pf-metrics-card");
    const empty = page.getByRole("heading", { name: "No Data" });
    await expect
      .poll(async () => (await cards.count()) > 0 || (await empty.count()) > 0)
      .toBe(true);
    await expect(page.getByText("Loading metrics…")).toHaveCount(0);
  });

  test("opens the raw record on demand", async ({ page }) => {
    await page.goto("/metrics");
    const card = page.locator(".pf-metrics-card").first();
    //  Skipped rather than failed when the grill has never recorded a mode:
    //  there is no card to open, and inventing one would mean writing to the
    //  metrics table from an e2e.
    test.skip((await page.locator(".pf-metrics-card").count()) === 0, "no metrics recorded");

    const details = card.locator("details");
    await expect(details).not.toHaveAttribute("open", /.*/);
    await card.getByText("Raw Data").click();
    await expect(details).toHaveAttribute("open", /.*/);
  });

  test("serves the listing envelope", async ({ request }) => {
    const resp = await request.get("/api/metrics");
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.result).toBe("OK");
    expect(Array.isArray(body.data.metrics)).toBe(true);
    expect(typeof body.data.units).toBe("string");
    expect(typeof body.data.augerrate).toBe("number");
  });

  test("serves the export as a CSV attachment", async ({ request }) => {
    //  Fetched through `request`, not by clicking the link: following it would
    //  write a file into the runner's download directory on every run.
    const resp = await request.get("/api/metrics/export");
    expect(resp.status()).toBe(200);
    expect(resp.headers()["content-disposition"]).toContain("attachment");
    expect(resp.headers()["content-disposition"]).toContain("PiFire-Metrics-Export.csv");
  });

  test("reaches the page from the history page", async ({ page }) => {
    await page.goto("/history");
    await page.getByRole("link", { name: "Metrics" }).click();
    await expect(page).toHaveURL(/\/metrics$/);
  });
});
```

- [ ] **Step 3: Run it**

```
cd web-react && bun run typecheck:e2e && bunx playwright test metrics.spec.ts --project=app
```

Expected: 5 passing (or 4 passing + 1 skipped on a grill with no metrics
recorded). Do **not** run `bun run test:e2e`.

- [ ] **Step 4: Add the fidelity stub**

Append to `web-react/tests/e2e/apiFixtures.ts`, following `stubEvents`' shape:

```ts
export async function stubMetrics(page: Page): Promise<void> {
  //  Pinned content, not the live table: a real record carries wall-clock
  //  timestamps and a pellet estimate that both move between captures, and a
  //  baseline photographs geometry, not the grill's afternoon.
  await page.route("**/api/metrics", (r) =>
    json(
      r,
      JSON.stringify({
        result: "OK",
        message: null,
        data: {
          units: "F",
          augerrate: 0.3,
          metrics: [
            {
              id: "m1",
              starttime: 1700000000000,
              starttime_c: "12:00:00",
              endtime: 1700000060000,
              endtime_c: "12:01:00",
              timeinmode: "60 s",
              mode: "Smoke",
              augerontime: 100,
              augerontime_c: "100 s",
              estusage_m: "30 grams",
              estusage_i: "0.07 pounds (1.06 ounces)",
              fanontime: 60,
              fanontime_c: "60 s",
              smokeplus: true,
              primary_setpoint: 225,
              smart_start_profile: 2,
              startup_temp: 160,
              p_mode: 2,
              auger_cycle_time: 20,
              pellet_level_start: 90,
              pellet_level_end: 85,
              pellet_brand_type: "Lumber Jack Hickory",
            },
          ],
        },
      }),
    ),
  );
}
```

Add `stubMetrics` to the import list at the top of `pageSpecs.ts`.

- [ ] **Step 5: Add the page spec**

Append to `PAGE_SPECS` in `web-react/tests/e2e/pageSpecs.ts`:

```ts
  // The metrics page. Stubbed for the same reason as admin and events: the
  // demo server has no /api/metrics backend, and an unstubbed read here would
  // photograph the failure branch instead of the page. The stub also pins the
  // CONTENT -- a real record's timestamps and pellet estimate move between
  // captures.
  //
  // One Smoke record is enough: it is the widest row set of the five (ten
  // rows), and a second card would only re-measure the same card geometry.
  {
    name: "metrics",
    path: "/metrics",
    //  The card, not the heading: the heading paints before the read lands,
    //  and a baseline taken then would measure the loading note.
    ready: ".pf-metrics-card",
    root: ".pf-shell",
    stubs: stubMetrics,
    landmarks: [
      ...SHELL,
      ".pf-metrics",
      ".pf-metrics-header",
      ".pf-metrics-title",
      ".pf-metrics-rate",
      ".pf-metrics-btn",
      ".pf-metrics-list",
      ".pf-metrics-card",
      ".pf-metrics-card-title",
      ".pf-metrics-table",
      ".pf-metrics-details",
      ".pf-metrics-summary",
    ],
  },
```

- [ ] **Step 6: Capture the baselines**

```
cd web-react && bun run baseline:capture
```

This writes `metrics-1280x720.json` and `metrics-390x844.json`. It also
**re-captures every other page**. Diff each changed baseline file individually
and account for every change: adding a link to `/history` is expected to move
that page's geometry, and nothing else should move at all. If an unrelated page
changed, stop and find out why before committing — a re-capture that quietly
absorbs a regression is worse than no baseline.

- [ ] **Step 7: Run the fidelity gate**

```
cd web-react && bun run test:e2e:fidelity
```

Expected: green, two more tests than before.

- [ ] **Step 8: Close out the backlog**

In `docs/superpowers/backlogs/react-migration-backlog.md`:

- Change the §8 line `- [ ] **metrics** — metrics/stats page` to `- [x]` with a
  SHIPPED note naming this plan and the two live defects it fixed.
- Strike `History→Metrics link dropped;` from the "UI parity, minor-graded"
  paragraph (line ~1018) — it is closed by Task 8.
- Add a SHIPPED entry for the slice, and a
  `#### Deferred by the metrics slice — 2026-07-28` section recording:
  - **`fanontime` / `fanontime_c` are collected and shown nowhere but the raw
    disclosure.** `_macro_metrics.html` names neither, and the port kept parity.
    They are the only reading of fan duty the system has.
  - **`pellet_level_start` / `pellet_level_end` / `pellet_brand_type` likewise.**
    The level delta over a mode is the most direct pellet-consumption figure in
    the database and the page shows an *estimate* beside it.
  - **`_macro_metrics.html`'s Hold card still reads the non-existent
    `grill_settemp`.** Fixed on the React surface only; the Jinja template is
    the legacy UI and its characterization test pins current behaviour.
  - **Metrics are shown flat, not grouped by cook.** Records chain
    Startup → Smoke/Hold → Shutdown → Stop; a session grouping was considered
    and cut as scope.
  - **No live refresh.** The page reads once on mount.

- [ ] **Step 9: Full gate**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
cd web-react && bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run test:e2e:fidelity
```

Report every count against the pre-slice baseline (pytest 3387, rstest 1484
across 154 files, fidelity 125).

- [ ] **Step 10: Commit**

```bash
cd /home/dannyb/sources/PiFire && jj new
jj describe --stdin <<'EOF'
test(web-react): live metrics e2e, fidelity baselines and closeout

The e2e aborts and RECORDS the destructive admin routes rather than
merely aborting them: an aborted request is silently swallowed, and a
spec that keeps passing while trying to power the grill off is worse
than no spec.
EOF
```

---

## Self-Review

**Spec coverage.** There is no separate spec document for this slice — the
design was settled in this plan's *Verified Facts* and *File Structure*
sections, which is where a reviewer should look for the rulings. Every element
of the Flask page has a task: the record list (5–7), the per-mode field sets
(5), the raw-data collapse (6), the CSV export (3, 7), the empty state (7), the
`Download CSV Data` label (7), and the only inbound link (8).

**Placeholders.** None. Every code step carries the code. Task 1 Step 3
deliberately shows a wrong first draft and then replaces it — that is a warning
about a real trap (a route test that never calls the route), not a placeholder.

**Type consistency.** `MetricRecord` is defined once in Task 4 and imported by
Tasks 5, 6 and 7. `metricRows(record, units)` and `modeAccent(mode)` keep the
same signatures in Task 5's implementation, Task 5's tests and Task 6's use.
`fetchMetrics` / `metricsExportUrl` are defined in Task 4 and used in Task 7.
`processed_metrics(settings)` is defined in Task 2 and used in Task 3. The
`stubMetrics` fixture's payload matches `MetricsPayload` field for field.

**Known rough edge, flagged rather than hidden.** The stylesheet is split across
Tasks 6 and 7 so `styleCoverage` never sees a rule without markup. That is a
real constraint of this repo's CSS gates, not an oversight; if the implementer
finds `styleCoverage` tolerates unused rules, they should say so and write the
whole stylesheet in Task 6.

**Verified against live code on 2026-07-28**, after a first draft got four
things wrong: `timeinmode` for a 60-second span is `"60 s"` and not
`"1 m 0 s"` (the branch is `> 60`); `append_metric` overwrites the `starttime`
and `id` a fixture tries to seed; Werkzeug's `Content-Disposition` filename is
unquoted; and this repo stubs `fetch` with `rs.stubGlobal` at module level and
mocks the API module (not `fetch`) in page tests. Every fixture and assertion
above reflects the corrected facts. Treat any remaining disagreement between
this plan and live code as the plan being wrong.

**Task 8 Step 3 offers a choice** (import `metrics.css` into the history page,
or add a `.pf-history-btn`). Both are acceptable; the plan states the constraint
that decides it (no class without a rule) rather than pretending there is one
right answer.
