# React History Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate PiFire's cook-history chart to React on uPlot, and replace the history downsampler's naive decimation with a fidelity-driven LTTB selection so the chart stops showing curves that never happened.

**Architecture:** Two independent halves. Backend: `prepare_chartdata` selects points by LTTB against a **2 °F shape target**, and only downsamples when a window has more than 10,000 raw samples — shared by both UIs, so it fixes the legacy Flask chart too. Frontend: a `/history` route rendering a uPlot canvas chart with a cursor-following tooltip plugin, fed by a new read-only JSON endpoint and appended live from the existing socket feed.

**Tech Stack:** Flask (Python 3.14), React 19 + TypeScript (TS7/tsgo), **uPlot** (new runtime dep), rsbuild, `@rstest/core`, Playwright, Biome. Package manager: **bun**.

## Design decisions (user-approved 2026-07-24)

- **D1 — uPlot, with a custom tooltip plugin.** Chosen after a live four-way comparison (uPlot / Chart.js / Recharts / Highcharts) on a synthetic 8-hour cook, reviewed by the user on the target hardware. Canvas (no per-point DOM, unlike the SVG libraries which degrade past a few thousand points on Pi-class hardware); ~22 KB gz vs ~69 KB (Chart.js) / ~97 KB (Highcharts core); MIT, so no licensing question. uPlot's "you build it yourself" objection is largely answered: `legend.live` is a built-in hover readout, `cursor.drag.x` gives drag-to-zoom, annotations are a `draw` hook, and a cursor-following tooltip is ~30 lines (reference implementation: `/home/dannyb/sources/pifire-chart-demo/src/charts.tsx::tooltipPlugin`).
- **D2 — Fidelity-driven downsampling, not a point count.** Replace `step = int(num_items / data_points)` (every-Nth) with LTTB, and choose the budget from a **shape target: the drawn curve stays within 2 °F of the true curve**. Only downsample when the window has **more than 10,000 raw samples**; at or below that, send every point. Rationale: at the old default (`datapoints=60`) over a 480-minute window the step was ≈160, so a two-minute lid-open dip or an overshoot spike could be sampled straight past.
- **D3 — Fix both UIs.** `prepare_chartdata` is shared, so the legacy Flask chart gets the same correctness fix. The ≤10k passthrough means legacy's payload only grows for genuinely long windows; see the **legacy performance note** below.
- **D4 — Chart only.** The cook-file list / upload / delete stays with the cookfile+recipes backlog item (they share a data model and a JSON listing endpoint that does not exist yet). No `/history` cook-file management in this slice.

### Legacy performance note (raised with the user, accepted)

Above the 10k threshold the legacy Flask page now receives more points than before, and it renders with Chart.js — whose own tooltip warns that large datasets are *"very slow… especially on the RaspberryPi Zero."* uPlot is unaffected. If the legacy page becomes unusable on long windows before Flask is retired, the mitigation is to cap its budget specifically (the selector takes an explicit `max_points`), not to revert LTTB. Log it if observed; do not pre-optimise.

## Global Constraints

- **bun, not npm** for all web-react install/run.
- **Testing API is `@rstest/core`** (`rs.fn`/`rs.mock`) — NOT vitest/`vi`. `.test.tsx` runs in jsdom.
- **`bun run lint` must be run and exit 0** in every web-react task (Biome enforces format). Two pre-existing `react-refresh` **warnings** (`App.tsx`, `WizardShell.tsx`) are acceptable; **errors** are not.
- **`bun run typecheck`** (TS7, `noUnusedLocals`) must stay clean.
- **Coverage ≥75% lines per changed file.**
- **Python:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`; run `uvx ruff format` on changed Python before committing. PEP 758 bare-tuple `except A, B` is canonical — do NOT rewrite.
- **Test isolation:** the suite must leave no artifacts in the repo root (`pifire.db`, `os_info.json`, `settings.json`, `pelletdb.json`). Check after running.
- **Do not modify the Flask `history` blueprint's templates or JS** — the legacy page keeps working until it is retired. Backend *data* changes are shared and intended (D3).
- **jj boundary protocol:** the controller runs `jj new` before each dispatch; the implementer finalizes with a single `jj desc -m`.

## Parallelization

```
T1 (LTTB selector + prepare_chartdata + settings)  ─┐
T2 (GET /api/history/chart endpoint)               ─┼─→ T4 (HistoryPage + toolbar + route) ─→ T5 (e2e + gate)
T3 (uPlot dep + HistoryChart component)            ─┘
```

- **Wave 1 — dispatch T1, T2 and T3 CONCURRENTLY.** They touch disjoint files: T1 is `file_mgmt/cookfile.py` + settings + its tests; T2 is a new `blueprints/api_history/` + its tests; T3 is `web-react` chart component + `package.json`. T2 calls `prepare_chartdata` but only through its existing signature, so it does not need T1's internals.
- **Wave 2 — T4** consumes T2's endpoint shape and T3's component.
- **Wave 3 — T5** e2e + full gate.
- **Reviews parallelize** (read-only): review T1, T2, T3 concurrently.

Isolated jj workspaces are **mandatory** for wave 1 — concurrent agents sharing one working copy cross-pollute commits regardless of which files they touch:

```bash
jj workspace add --name hc1 -r <plan-commit> ../PiFire-hc1   # T1
jj workspace add --name hc2 -r <plan-commit> ../PiFire-hc2   # T2
jj workspace add --name hc3 -r <plan-commit> ../PiFire-hc3   # T3
# then in each web-react workspace: bun install (node_modules is gitignored, ~200MB)
```

**Behavioural-reach caution (bitten three times this session):** file-disjointness is necessary but not sufficient. T3 adds a **runtime dependency** (`uplot`) to `package.json`; T4 must `bun install` after linearization or its build fails. And T1 changes the *shape* of what `prepare_chartdata` returns for large windows — anything asserting exact point counts will move. Verify the **merged state** at integration; no single task tests the combination.

**Integration (controller):** `jj workspace forget hc1 hc2 hc3` → linearize with **change ids** → `bun install` → verify merged state (`typecheck && lint && test && build`) → e2e in the main checkout (HUP-reload gunicorn first) → `rm -rf ../PiFire-hc{1,2,3}` → update ledger + backlog.

---

### Task 1: fidelity-driven downsampling (LTTB)

**Files:**
- Create: `file_mgmt/downsample.py`
- Modify: `file_mgmt/cookfile.py` (`prepare_chartdata`, currently ~line 406)
- Modify: `common/settings_schema.py` (~line 553), `common/defaults.py` (~line 231)
- Test: `tests/unit/file_mgmt/test_downsample.py` (create)

**Interfaces:**
- Produces:
  ```python
  def lttb_indices(values, times, budget) -> list[int]
  def max_interpolation_error(values, indices) -> float
  def select_indices(series, times, *, tolerance=2.0, min_points=10000, max_points=None) -> list[int]
  ```
  `series` is a list of value-lists (one per probe) sharing `times`. `select_indices` returns ALL indices when `len(times) <= min_points`.
- Settings: `history_page.datapoints` 60 → **10000** (reinterpreted as the "downsample above this many samples" threshold), plus new `history_page.fidelity_degrees: float = 2.0`.

- [ ] **Step 1: Write the failing selector tests**

Create `tests/unit/file_mgmt/test_downsample.py`:

```python
"""Fidelity-driven history downsampling.

prepare_chartdata used to keep every Nth sample (step = num_items // data_points).
At the shipped default (60 points over a 480-minute window) that is step ~= 160,
so a two-minute lid-open dip -- the exact event a cook chart exists to show --
could be sampled straight past and never appear. These tests pin the replacement:
LTTB selection driven by a shape target, not a point count.
"""

import math

from file_mgmt.downsample import lttb_indices, max_interpolation_error, select_indices


def _cook_with_dip(n=30000, dip_at=15000, dip_len=120, dip_depth=60.0):
    """Flat 225F hold with one sharp, narrow dip (a lid-open event)."""
    times = [float(i) for i in range(n)]
    values = [225.0 for _ in range(n)]
    for i in range(dip_at, dip_at + dip_len):
        phase = (i - dip_at) / dip_len
        values[i] = 225.0 - dip_depth * math.sin(math.pi * phase)
    return values, times


def test_passthrough_below_the_threshold():
    values, times = _cook_with_dip(n=5000)
    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    assert idx == list(range(5000))  # nothing dropped -- fidelity is exact


def test_downsamples_above_the_threshold():
    values, times = _cook_with_dip(n=30000)
    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    assert len(idx) < len(times)
    assert idx[0] == 0 and idx[-1] == len(times) - 1  # endpoints always kept


def test_meets_the_shape_target():
    values, times = _cook_with_dip(n=30000)
    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    assert max_interpolation_error(values, idx) <= 2.0


def test_preserves_a_narrow_dip_that_every_nth_would_miss():
    """The regression this whole change exists for."""
    values, times = _cook_with_dip(n=30000, dip_at=15000, dip_len=120, dip_depth=60.0)

    # Naive decimation at the OLD default budget: step = 30000 // 60 = 500, so
    # the 120-sample dip falls entirely between two kept samples.
    every_nth = list(range(0, len(times), len(times) // 60))
    assert max_interpolation_error(values, every_nth) > 20.0  # dip essentially erased

    idx = select_indices([values], times, tolerance=2.0, min_points=10000)
    kept_in_dip = [i for i in idx if 15000 <= i < 15120]
    assert kept_in_dip, "LTTB dropped the entire dip"
    assert max_interpolation_error(values, idx) <= 2.0


def test_budget_grows_until_every_series_is_within_tolerance():
    """One shared x-axis serves all probes, so the WORST series sets the budget."""
    calm, times = _cook_with_dip(n=30000, dip_len=120, dip_depth=1.0)  # nearly flat
    spiky, _ = _cook_with_dip(n=30000, dip_len=120, dip_depth=80.0)

    calm_only = select_indices([calm], times, tolerance=2.0, min_points=10000)
    both = select_indices([calm, spiky], times, tolerance=2.0, min_points=10000)

    assert len(both) >= len(calm_only)
    assert max_interpolation_error(spiky, both) <= 2.0
    assert max_interpolation_error(calm, both) <= 2.0


def test_max_points_caps_the_budget():
    values, times = _cook_with_dip(n=30000)
    idx = select_indices([values], times, tolerance=0.001, min_points=10000, max_points=2000)
    assert len(idx) <= 2000  # tolerance unreachable, but the cap holds


def test_lttb_keeps_endpoints_and_respects_budget():
    values, times = _cook_with_dip(n=20000)
    idx = lttb_indices(values, times, 500)
    assert len(idx) == 500
    assert idx[0] == 0 and idx[-1] == len(values) - 1
    assert idx == sorted(idx)
```

- [ ] **Step 2: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/file_mgmt/test_downsample.py -v`
Expected: FAIL — `file_mgmt.downsample` does not exist.

- [ ] **Step 3: Implement the selector**

Create `file_mgmt/downsample.py`:

```python
"""Fidelity-driven downsampling for the history chart.

The chart used to keep every Nth sample, which silently erases short events
(a lid-open dip, an overshoot spike) whenever the step exceeded the event's
width. This selects points by Largest-Triangle-Three-Buckets instead, and
grows the budget until the drawn curve is within a stated tolerance of the
true curve -- a shape target rather than a point count.
"""


def lttb_indices(values, times, budget):
    """Largest-Triangle-Three-Buckets (Steinarsson, 2013).

    Returns the INDICES to keep, so several series can share one x-axis.
    Picks, per bucket, the sample forming the largest triangle with the
    previously-kept point and the next bucket's average -- a good proxy for
    "the point that most defines the curve's shape", which is why peaks and
    valleys survive where every-Nth loses them.
    """
    n = len(values)
    if budget >= n or budget <= 2:
        return list(range(n))

    kept = [0]  # always keep the first point
    bucket_size = (n - 2) / (budget - 2)
    a = 0  # previously kept index

    for i in range(budget - 2):
        # Average of the NEXT bucket = the triangle's third vertex.
        next_start = int((i + 1) * bucket_size) + 1
        next_end = min(int((i + 2) * bucket_size) + 1, n)
        count = max(1, next_end - next_start)
        avg_x = sum(times[next_start:next_end]) / count if next_end > next_start else times[-1]
        avg_y = sum(values[next_start:next_end]) / count if next_end > next_start else values[-1]

        range_start = int(i * bucket_size) + 1
        range_end = min(int((i + 1) * bucket_size) + 1, n)
        best, best_area = range_start, -1.0
        for j in range(range_start, range_end):
            area = abs((times[a] - avg_x) * (values[j] - values[a]) - (times[a] - times[j]) * (avg_y - values[a]))
            if area > best_area:
                best_area, best = area, j
        kept.append(best)
        a = best

    kept.append(n - 1)  # always keep the last point
    return kept


def max_interpolation_error(values, indices):
    """Worst absolute gap between the drawn curve and the true one.

    The chart draws straight lines between kept samples, so the error at a
    dropped sample is the distance from it to that line. This is the number
    the tolerance is expressed in (degrees, same units as the probe).
    """
    if len(indices) < 2:
        return float("inf")
    worst = 0.0
    for k in range(len(indices) - 1):
        i0, i1 = indices[k], indices[k + 1]
        y0, y1 = values[i0], values[i1]
        span = i1 - i0
        if span <= 1:
            continue
        for i in range(i0 + 1, i1):
            approx = y0 + (y1 - y0) * ((i - i0) / span)
            err = abs(approx - values[i])
            if err > worst:
                worst = err
    return worst


def select_indices(series, times, *, tolerance=2.0, min_points=10000, max_points=None):
    """Choose which samples to send for a set of co-timed series.

    Returns every index when there are `min_points` or fewer samples -- below
    that the payload is small enough that downsampling only loses information.
    Above it, grow the LTTB budget until EVERY series is within `tolerance`
    (they share one x-axis, so the worst series sets the budget), optionally
    capped at `max_points`.
    """
    n = len(times)
    all_indices = list(range(n))
    if n <= min_points or not series:
        return all_indices

    ceiling = min(max_points, n) if max_points else n
    # Drive selection off the most dynamic series; verification below covers
    # the rest, and the budget keeps growing until they all fit.
    driver = max(series, key=lambda s: (max(s) - min(s)) if s else 0)

    budget = 1000
    best = all_indices
    while budget < ceiling:
        idx = lttb_indices(driver, times, budget)
        if all(max_interpolation_error(s, idx) <= tolerance for s in series):
            return idx
        best = idx
        budget *= 2

    capped = lttb_indices(driver, times, ceiling)
    return capped if max_points else best if len(best) <= n else all_indices
```

- [ ] **Step 4: Run to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/file_mgmt/test_downsample.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Wire it into `prepare_chartdata`**

In `file_mgmt/cookfile.py`, the selection currently reads:

```python
    if reduce and (num_items > data_points):
        step = int(num_items / data_points)
    else:
        step = 1
```
and the build loop iterates `range(list_length - num_items, list_length, step)`.

Replace the step-based iteration with an explicit index list. Add at the top of the file:

```python
from file_mgmt.downsample import select_indices
```

Compute the window's indices once, before the build loop, and iterate over them instead of `range(..., step)`:

```python
window_start = max(0, list_length - num_items)
window = list(range(window_start, list_length))
if reduce and window:
    # Fidelity-driven: keep the shape within `data_points`-gated tolerance
    # rather than keeping every Nth sample (which erased short events).
    series = [list(v[window_start:list_length]) for v in history["P"].values()]
    series += [list(v[window_start:list_length]) for v in history["F"].values()]
    times = [float(t) for t in history["T"][window_start:list_length]]
    chosen = select_indices(series, times, tolerance=tolerance, min_points=data_points, max_points=max_points)
    window = [window_start + i for i in chosen]
```

then change the build loop from `for index in range(list_length - num_items, list_length, step):` to `for index in window:`. Keep every append inside the loop exactly as-is.

Give `prepare_chartdata` the two new keyword args (defaulted so existing callers are unaffected):

```python
def prepare_chartdata(
    probe_config, chart_info={}, num_items=10, reduce=True, data_points=10000,
    history=None, tolerance=2.0, max_points=None,
):
```

- [ ] **Step 6: Update the settings defaults**

`common/settings_schema.py` (~line 553): `datapoints: int = 60` → `datapoints: int = 10000`, and add below it `fidelity_degrees: float = 2.0`.
`common/defaults.py` (~line 231): `"datapoints": 60,` → `"datapoints": 10000,  # Downsample only above this many samples in the window`, and add `"fidelity_degrees": 2.0,  # Drawn curve stays within this many degrees of the true curve`.

- [ ] **Step 7: Run the affected suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/file_mgmt/ tests/unit/common/test_settings_schema.py tests/web/test_page_history.py -v`
Expected: PASS. Some tests may assert the old `datapoints` default or exact chart point counts — update those expectations to the new behaviour (do NOT weaken an assertion to make it pass; if a test pinned every-Nth semantics, rewrite it to pin the fidelity target instead, and say so in your report).

- [ ] **Step 8: Full suite + artifact check**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest -q`
then: `ls pifire.db os_info.json settings.json pelletdb.json 2>/dev/null || echo "clean"`
Expected: all PASS, no artifacts.

- [ ] **Step 9: Format and commit**

```bash
uvx ruff format file_mgmt/downsample.py file_mgmt/cookfile.py common/settings_schema.py common/defaults.py tests/unit/file_mgmt/test_downsample.py
git add file_mgmt/downsample.py file_mgmt/cookfile.py common/settings_schema.py common/defaults.py tests/unit/file_mgmt/test_downsample.py
git commit -m "fix(history): select chart points by LTTB against a fidelity target"
```

---

### Task 2: `GET /api/history/chart` JSON endpoint

**Files:**
- Create: `blueprints/api_history/__init__.py`, `blueprints/api_history/routes.py`
- Modify: `app.py` (register the blueprint — follow exactly how `api_wizard_bp` is registered)
- Test: `tests/web/test_api_history.py` (create)

**Interfaces:**
- Consumes: `prepare_chartdata(probe_config, num_items=..., reduce=True, data_points=...)` from `file_mgmt.cookfile`; `read_settings()`, `read_control()` from `common.datastore_accessors`.
- Produces: `GET /api/history/chart?minutes=N` → `200 {"time_labels": [...], "chart_data": [...], "probe_mapper": {...}, "annotations": [...], "minutes": N}`.

**Why a new endpoint rather than reusing `/history/refresh`:** the legacy route is a POST that also **persists** `settings["history_page"]["minutes"]` as a side effect of reading. A React client asking for a different window must not rewrite the user's saved setting. This endpoint is read-only.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_history.py` (mirror `tests/web/test_api_wizard.py`'s `ds`/`client` fixture style — read it first):

```python
import json

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_chart_returns_the_series_payload(ds, client):
    resp = client.get("/api/history/chart?minutes=10")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {"time_labels", "chart_data", "probe_mapper", "annotations", "minutes"}
    assert body["minutes"] == 10
    assert isinstance(body["chart_data"], list)
    assert isinstance(body["time_labels"], list)


def test_chart_defaults_to_the_saved_window(ds, client):
    body = client.get("/api/history/chart").get_json()
    assert body["minutes"] >= 1


def test_chart_is_read_only(ds, client):
    """Unlike the legacy POST /history/refresh, asking for a window must NOT
    rewrite the user's saved history_page.minutes setting."""
    from common.datastore_accessors import read_settings

    before = read_settings()["history_page"]["minutes"]
    client.get("/api/history/chart?minutes=999")
    assert read_settings()["history_page"]["minutes"] == before


def test_chart_rejects_a_bad_window(ds, client):
    resp = client.get("/api/history/chart?minutes=notanumber")
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "invalid_minutes"
```

- [ ] **Step 2: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_history.py -v`
Expected: FAIL — 404 (blueprint not registered).

- [ ] **Step 3: Implement the blueprint**

Create `blueprints/api_history/__init__.py`:

```python
from flask import Blueprint

api_history_bp = Blueprint("api_history_bp", __name__, url_prefix="/api/history")

from . import routes  # noqa: E402,F401
```

Create `blueprints/api_history/routes.py`:

```python
import time

from flask import jsonify, request

from common.app import prepare_annotations
from common.datastore_accessors import read_settings
from file_mgmt.cookfile import prepare_chartdata

from . import api_history_bp

# The history store records ~20 samples per minute (see the legacy
# blueprints/history/routes.py, which computes num_items as minutes * 20).
SAMPLES_PER_MINUTE = 20


@api_history_bp.route("/chart", methods=["GET"])
def history_chart():
    """Read-only chart data for the React history page.

    Deliberately NOT the legacy POST /history/refresh: that route persists
    settings["history_page"]["minutes"] as a side effect of being asked for a
    window, which would let a client's transient zoom overwrite the user's
    saved preference.
    """
    settings = read_settings()
    history_page = settings["history_page"]

    raw = request.args.get("minutes")
    if raw is None:
        minutes = int(history_page["minutes"])
    else:
        try:
            minutes = int(raw)
        except TypeError, ValueError:
            return jsonify({"result": "error", "message": "invalid_minutes"}), 400
        if minutes < 1:
            return jsonify({"result": "error", "message": "invalid_minutes"}), 400

    payload = prepare_chartdata(
        history_page["probe_config"],
        num_items=minutes * SAMPLES_PER_MINUTE,
        reduce=True,
        data_points=history_page.get("datapoints", 10000),
        tolerance=history_page.get("fidelity_degrees", 2.0),
    )
    payload["annotations"] = prepare_annotations(time.time() - minutes * 60)
    payload["minutes"] = minutes
    return jsonify(payload), 200
```

**Verify before writing:** confirm `prepare_annotations` really lives in `common.app` (the legacy history route imports it — check that import) and that `prepare_chartdata` accepts `tolerance` (Task 1 adds it; if Task 1 has not landed in your workspace, omit the `tolerance` kwarg and note it in your report rather than inventing a signature).

- [ ] **Step 4: Register the blueprint**

In `app.py`, find where `api_wizard_bp` is registered and add the same treatment for `api_history_bp`. Match the existing style exactly.

- [ ] **Step 5: Run to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_history.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Regression + artifact check**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/ -q`
then: `ls pifire.db os_info.json 2>/dev/null || echo "clean"`
Expected: PASS, no artifacts.

- [ ] **Step 7: Format and commit**

```bash
uvx ruff format blueprints/api_history/ app.py tests/web/test_api_history.py
git add blueprints/api_history/ app.py tests/web/test_api_history.py
git commit -m "feat(api): add read-only GET /api/history/chart"
```

---

### Task 3: uPlot dependency + `HistoryChart` component

**Files:**
- Modify: `web-react/package.json` (add `uplot`)
- Create: `web-react/src/components/history/HistoryChart.tsx`
- Create: `web-react/src/components/history/HistoryChart.test.tsx`
- Create: `web-react/src/components/history/historyChart.css`

**Interfaces:**
- Produces:
  ```typescript
  export interface ChartSeries { label: string; color: string; values: (number | null)[] }
  export interface HistoryChartProps {
    times: number[];            // epoch SECONDS (uPlot's x convention)
    series: ChartSeries[];
    height?: number;            // default 360
  }
  export function HistoryChart(props: HistoryChartProps): JSX.Element
  ```

- [ ] **Step 1: Add the dependency**

Run: `cd <workspace>/web-react && bun add uplot`
This is the app's FIRST charting dependency; it is MIT and ~22 KB gzipped. Commit the resulting `package.json` + `bun.lock`.

- [ ] **Step 2: Write the failing tests**

Create `web-react/src/components/history/HistoryChart.test.tsx`. jsdom has no canvas, so assert the component's contract (mounts, renders a container, tears down cleanly, reacts to prop changes) rather than pixels:

```tsx
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import { HistoryChart } from "./HistoryChart";

afterEach(cleanup);

const times = [1, 2, 3, 4, 5];
const series = [
  { label: "Grill", color: "#ff7a1a", values: [200, 210, 220, 225, 224] },
  { label: "Probe 1", color: "#4dc9ff", values: [80, 90, 100, 110, 120] },
];

describe("HistoryChart", () => {
  it("mounts a chart container", () => {
    const { container } = render(<HistoryChart times={times} series={series} />);
    expect(container.querySelector(".pf-history-chart")).toBeInTheDocument();
  });

  it("renders without throwing when there is no data yet", () => {
    expect(() => render(<HistoryChart times={[]} series={[]} />)).not.toThrow();
  });

  it("survives a data update (re-render with new points)", () => {
    const { rerender, container } = render(<HistoryChart times={times} series={series} />);
    expect(() =>
      rerender(
        <HistoryChart
          times={[...times, 6]}
          series={series.map((s) => ({ ...s, values: [...s.values, 230] }))}
        />,
      ),
    ).not.toThrow();
    expect(container.querySelector(".pf-history-chart")).toBeInTheDocument();
  });

  it("cleans up on unmount", () => {
    const { unmount, container } = render(<HistoryChart times={times} series={series} />);
    unmount();
    expect(container.querySelector(".pf-history-chart")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd <workspace>/web-react && bun run test src/components/history/HistoryChart.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the component**

Create `web-react/src/components/history/HistoryChart.tsx`. Model the tooltip plugin on the working reference at `/home/dannyb/sources/pifire-chart-demo/src/charts.tsx::tooltipPlugin` (read it):

```tsx
import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./historyChart.css";

export interface ChartSeries {
  label: string;
  color: string;
  values: (number | null)[];
}

export interface HistoryChartProps {
  /** Epoch SECONDS -- uPlot's x-axis convention. */
  times: number[];
  series: ChartSeries[];
  height?: number;
}

/**
 * Cursor-following tooltip, as a uPlot plugin (~30 lines).
 *
 * uPlot already ships a live hover readout (`legend.live` defaults to true),
 * but it renders as a legend table; this puts the values at the cursor, which
 * is what the legacy Chart.js page did.
 */
function tooltipPlugin(): uPlot.Plugin {
  let el: HTMLDivElement | null = null;
  return {
    hooks: {
      init: (u: uPlot) => {
        el = document.createElement("div");
        el.className = "pf-history-tip";
        el.style.display = "none";
        u.over.appendChild(el);
      },
      setCursor: (u: uPlot) => {
        if (!el) return;
        const { idx, left, top } = u.cursor;
        if (idx == null || left == null || left < 0) {
          el.style.display = "none";
          return;
        }
        const when = new Date((u.data[0][idx] as number) * 1000);
        const rows = u.series
          .slice(1)
          .map((s, i) => {
            const v = u.data[i + 1][idx] as number | null;
            const val = v == null ? "—" : `${v.toFixed(1)}°`;
            return `<div class="r"><i style="background:${String(s.stroke)}"></i>${s.label}<b>${val}</b></div>`;
          })
          .join("");
        el.innerHTML = `<div class="t">${when.toLocaleTimeString()}</div>${rows}`;
        el.style.display = "block";
        // Flip left near the right edge so the tooltip stays on screen.
        const flip = left > u.over.clientWidth - 150;
        el.style.left = `${flip ? left - 158 : left + 12}px`;
        el.style.top = `${Math.max(4, (top ?? 0) - 10)}px`;
      },
      destroy: () => {
        el?.remove();
        el = null;
      },
    },
  };
}

export function HistoryChart({ times, series, height = 360 }: HistoryChartProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);

  // uPlot is an imperative canvas library: create once per series *shape*, then
  // feed new data via setData. Rebuilding on every tick would drop the user's
  // zoom and thrash the canvas.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const opts: uPlot.Options = {
      width: host.clientWidth || 800,
      height,
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: s.color,
          width: 1.5,
          points: { show: false },
        })),
      ],
      axes: [
        { stroke: "#9aa3ad", grid: { stroke: "rgba(255,255,255,0.07)" } },
        { stroke: "#9aa3ad", grid: { stroke: "rgba(255,255,255,0.07)" } },
      ],
      cursor: { drag: { x: true, y: false } }, // drag-to-zoom
      legend: { live: true },
      plugins: [tooltipPlugin()],
    };

    const data: uPlot.AlignedData = [times, ...series.map((s) => s.values)] as uPlot.AlignedData;
    const plot = new uPlot(opts, data, host);
    plotRef.current = plot;

    const onResize = () => plot.setSize({ width: host.clientWidth || 800, height });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plot.destroy();
      plotRef.current = null;
    };
    // Rebuild only when the series SHAPE changes (count/labels), not per tick.
  }, [height, series.length, series.map((s) => s.label).join("|")]);

  // Feed data updates without rebuilding.
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    plot.setData([times, ...series.map((s) => s.values)] as uPlot.AlignedData);
  }, [times, series]);

  return <div ref={hostRef} className="pf-history-chart" style={{ height }} />;
}
```

If the React Compiler lint objects to the dependency arrays, do NOT add a suppression — restructure (e.g. hoist the shape key into a `useMemo`) and report what you changed.

- [ ] **Step 5: Add the stylesheet**

Create `web-react/src/components/history/historyChart.css` with the tooltip styling (mirror the demo's `.uplot-tip` rules, renamed to `.pf-history-tip`, and use the app's existing CSS custom properties for colours rather than hardcoding new ones — read `src/components/dashboard/dashboard.css` for the variable names in use).

- [ ] **Step 6: Run to verify they pass, then gate**

Run: `cd <workspace>/web-react && bun run test src/components/history/HistoryChart.test.tsx`
then: `cd <workspace>/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add web-react/package.json web-react/bun.lock web-react/src/components/history/
git commit -m "feat(web-react): add uPlot HistoryChart with a cursor tooltip"
```

---

### Task 4: `HistoryPage` + toolbar + route

**Files:**
- Create: `web-react/src/components/history/HistoryPage.tsx`
- Create: `web-react/src/components/history/HistoryPage.test.tsx`
- Create: `web-react/src/helpers/history/historyApi.ts`
- Create: `web-react/src/helpers/history/historyApi.test.ts`
- Modify: `web-react/src/components/App.tsx` (add the `/history` route)

**Interfaces:**
- Consumes: `HistoryChart` (T3); `GET /api/history/chart?minutes=N` (T2).
- Produces: `fetchHistoryChart(baseUrl, minutes?): Promise<HistoryChartData>`; `HistoryPage()`.

- [ ] **Step 1: Write the failing api-client tests**

Create `web-react/src/helpers/history/historyApi.test.ts`, mirroring `src/helpers/wizard/wizardApi.test.ts`'s fetch-mocking style (read it first):

```typescript
import { afterEach, describe, expect, rs, test } from "@rstest/core";

afterEach(() => rs.resetAllMocks());

describe("historyApi", () => {
  test("fetchHistoryChart requests the window and returns parsed JSON", async () => {
    const fake = { time_labels: [], chart_data: [], probe_mapper: {}, annotations: [], minutes: 30 };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { fetchHistoryChart } = await import("./historyApi");
    const data = await fetchHistoryChart("", 30);
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/history/chart?minutes=30",
    );
    expect(data.minutes).toBe(30);
  });

  test("fetchHistoryChart omits the query when no window is given", async () => {
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ minutes: 60 }) }) as never;
    const { fetchHistoryChart } = await import("./historyApi");
    await fetchHistoryChart("");
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).not.toContain("minutes=");
  });

  test("fetchHistoryChart throws on a non-ok response", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: false, status: 400 }) as never;
    const { fetchHistoryChart } = await import("./historyApi");
    await expect(fetchHistoryChart("", 5)).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Implement the client**

Create `web-react/src/helpers/history/historyApi.ts`:

```typescript
const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export interface HistoryChartData {
  time_labels: string[];
  chart_data: { label: string; data: (number | null)[]; borderColor?: string }[];
  probe_mapper: Record<string, unknown>;
  annotations: unknown[];
  minutes: number;
}

export async function fetchHistoryChart(
  baseUrl: string = BASE_URL,
  minutes?: number,
): Promise<HistoryChartData> {
  const qs = minutes === undefined ? "" : `?minutes=${Math.max(1, Math.round(minutes))}`;
  const r = await fetch(`${baseUrl}/api/history/chart${qs}`);
  if (!r.ok) throw new Error(`history chart failed: ${r.status}`);
  return (await r.json()) as HistoryChartData;
}
```

**Verify the real payload shape first** — run `curl -s 'http://localhost:5000/api/history/chart?minutes=5' | head -c 400` against the dev backend (Task 2 must have landed) and adjust `chart_data`'s element type to match what `prepare_chartdata` actually emits. Do NOT guess: the legacy `history.js` consumes this same structure, so read it if the curl is unavailable.

- [ ] **Step 3: Write the failing page tests**

Create `web-react/src/components/history/HistoryPage.test.tsx`, mocking `historyApi` and `HistoryChart` (jsdom has no canvas, so stub the chart to a marker element):

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HistoryPage } from "./HistoryPage";

const fetchHistoryChart = rs.fn();
rs.mock("../../helpers/history/historyApi", () => ({
  fetchHistoryChart: (...a: unknown[]) => fetchHistoryChart(...a),
}));
rs.mock("./HistoryChart", () => ({
  HistoryChart: () => <div data-testid="chart" />,
}));

afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});

const payload = {
  time_labels: ["12:00:00", "12:00:03"],
  chart_data: [{ label: "Grill", data: [220, 225], borderColor: "#ff7a1a" }],
  probe_mapper: {},
  annotations: [],
  minutes: 60,
};

describe("HistoryPage", () => {
  it("loads and renders the chart", async () => {
    fetchHistoryChart.mockResolvedValue(payload);
    render(<HistoryPage />);
    await waitFor(() => expect(screen.getByTestId("chart")).toBeInTheDocument());
  });

  it("refetches when the window changes", async () => {
    fetchHistoryChart.mockResolvedValue(payload);
    render(<HistoryPage />);
    await waitFor(() => expect(fetchHistoryChart).toHaveBeenCalled());
    fetchHistoryChart.mockClear();

    fireEvent.change(screen.getByLabelText(/minutes/i), { target: { value: "120" } });
    await waitFor(() => expect(fetchHistoryChart).toHaveBeenCalledWith(expect.anything(), 120));
  });

  it("shows an error banner when the fetch fails", async () => {
    fetchHistoryChart.mockRejectedValue(new Error("boom"));
    render(<HistoryPage />);
    await waitFor(() => expect(screen.getByText(/couldn't load/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 4: Implement the page**

Create `web-react/src/components/history/HistoryPage.tsx`. It must provide:
- a **minutes** window control (labelled "Minutes", so `getByLabelText(/minutes/i)` resolves), refetching on change;
- an **Export CSV** link to `/history/export` (the legacy route already serves it — link, do not reimplement);
- a **Reset zoom** button (call `setScale` on the plot, or remount the chart — simplest correct approach wins);
- an error banner on fetch failure, and a loading state;
- the `HistoryChart` fed from the fetched payload.

Follow the house patterns: no `setState` in `useEffect` for derived state (render-phase adjustment, as in `SafetyTab.tsx`); fetch-in-effect is fine (as in `DashboardRoute.tsx`). Reuse existing CSS classes rather than inventing new ones.

- [ ] **Step 5: Register the route**

In `web-react/src/components/App.tsx`, add `{ path: "/history", element: <HistoryPage /> }` alongside the other top-level routes, with the matching import.

- [ ] **Step 6: Run tests + gate**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/history/ src/helpers/history/`
then: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add web-react/src/components/history/ web-react/src/helpers/history/ web-react/src/components/App.tsx
git commit -m "feat(web-react): add the history page with window, export and reset-zoom controls"
```

---

### Task 5: e2e + full gate

**Files:**
- Modify: `web-react/tests/e2e/` (add a history spec — read the directory and follow its conventions, including `ensureStopped` from `helpers.ts` where a known mode is needed)

- [ ] **Step 1: Add the e2e**

Add a spec that loads `/history`, waits for the chart container (`.pf-history-chart`) to appear, changes the minutes window and asserts the page refetches without error, and confirms the Export CSV link points at `/history/export`. Leave no trace: the endpoint is read-only, so no restore is needed — state that in a comment.

- [ ] **Step 2: Run the e2e in the MAIN checkout**

The Flask backend must serve current code — HUP-reload gunicorn first, then confirm `curl -s 'http://localhost:5000/api/history/chart?minutes=5'` returns JSON.
Run: `cd /home/dannyb/sources/PiFire/web-react && bunx playwright test --reporter=line`
Expected: all PASS.

- [ ] **Step 3: Full gate**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test && bun run build`
then: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest -q`
then: `ls pifire.db os_info.json settings.json pelletdb.json 2>/dev/null || echo "clean"`
Expected: all PASS, no artifacts.

- [ ] **Step 4: Commit**

```bash
git add web-react/tests/e2e/
git commit -m "test(web-react): e2e for the history chart page"
```

---

## Self-Review

**1. Spec coverage:** D1 (uPlot + tooltip plugin) → T3 ✅. D2 (LTTB, 2 °F target, >10k threshold) → T1 ✅. D3 (shared `prepare_chartdata`, both UIs) → T1 ✅. D4 (chart only, no cook-file list) → scope of T4 ✅. Read-only endpoint → T2 ✅. e2e → T5 ✅.

**2. Placeholder scan:** every code step carries complete code, except three deliberate "read the real thing first" points, each naming the exact file and why guessing is worse: T2 Step 3 (`prepare_annotations`' module and `prepare_chartdata`'s signature), T3 Step 5 (existing CSS variable names), T4 Step 2 (the real `chart_data` element shape, verified by curl against the dev backend). T4 Step 4 and T5 Step 1 specify required behaviours rather than literal code because they must match local conventions — both list the exact controls/assertions needed.

**3. Type consistency:** `ChartSeries`/`HistoryChartProps` are defined in T3 and consumed by T4. `HistoryChartData` is defined in T4's `historyApi.ts` and matches T2's JSON keys (`time_labels`, `chart_data`, `probe_mapper`, `annotations`, `minutes`). `select_indices(series, times, *, tolerance, min_points, max_points)` is identical in T1's definition and its `prepare_chartdata` call site; `prepare_chartdata` gains `tolerance`/`max_points` in T1 and T2 passes `tolerance`.
