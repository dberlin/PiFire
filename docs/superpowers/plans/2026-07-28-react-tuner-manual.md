# React Tuner — Manual Flow Implementation Plan (Slice 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Flask's `/tuner` **manual** three-point flow to React at `/tuner`,
behind a new `blueprints/api_tuner`, and close the template-injection door in the
legacy blueprint on the way.

**Architecture:** The tuning SESSION is separated from the READING. Flask's
`read_tr` command both enables tuning mode and returns a value, so merely polling
mutates grill state; here `POST /api/tuner/session` opens and closes the session
(the only two calls that write `control`) and `GET /api/tuner/tr` is a pure read.
A React hook owns the session lifetime and guarantees teardown on unmount. The
Steinhart-Hart maths stays in `blueprints/tuner/tuner.py` — one definition — but
gains an honest failure signal instead of returning `(0, 0, 0)`.

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
- **web-react gates, every task that touches `web-react/`:** `bun run typecheck`,
  `bun run lint`, `bun run test`. Plus `bun run typecheck:e2e` for `tests/e2e/`.
  `bun run format` fixes Biome formatting failures.
- **Every `pf-*` class used in a `.tsx` must have a rule in a `.css`, and every
  rule must have a consumer.** `src/cssCoverage.test.ts` and
  `src/styleCoverage.test.ts` enforce both directions, so a page-level rule
  written before its markup fails the suite.
- **Do NOT run `bun run test:e2e`** (the whole `app` project): `roundtrip.spec.ts`
  puts the grill into Startup mode and `settings.spec.ts` flushes the history
  store. Use `bun run test:e2e:fidelity`, or one named spec with `--project=app`.
- **Never hand-edit `web-react/tests/e2e/baselines/*.json`** — `bun run
  baseline:capture` writes them.
- **`control.py` drives the relays. Do not start it.** The e2e needs `gunicorn`
  only.

### Constraints specific to THIS slice

- **This is the first ported page whose own operation changes grill mode.**
  Opening a tuning session sets `tuning_mode: true` and, from Stop, moves the
  grill to **Monitor**. Monitor lights nothing — it reads probes — but it is a
  real mode change on the operator's grill. Every task that can reach
  `/api/tuner/session` must leave the grill in **Stop** when it finishes.
- **No test in this plan may open a session against the live backend without
  closing it.** The e2e task uses an `afterEach` that asserts the grill is back
  in Stop, and fails the spec if it is not — an abandoned Monitor session is a
  silent state leak onto the developer's machine.
- **Never call `/api/tuner/session` with `{"open": true}` from a fidelity
  capture.** Baselines stub the API; they must not touch the live grill.
- **Neutralize `os.system`/`subprocess`/`sudo`/`reboot`/`shutdown` before running
  any test that can reach admin/installer/updater/wizard paths.** Nothing here
  should, and `blueprints/tuner` contains none — verified 2026-07-28 — but
  Task 1 edits that blueprint, so re-grep before running its tests.
- **Exact strings.** Route heading `Tuner`. The three segments are `High`,
  `Medium`, `Low` (capitalised, in that order — `_macro_tuner.html:68-76`).
  Profile save actions are `Save & Apply` and `Save Only`
  (`index.html:135-136`).

---

## Verified Facts

Read from live code on 2026-07-28. Do not re-derive; do flag any that no longer
match.

### The template-injection door Task 1 closes

`blueprints/tuner/routes.py` builds a Jinja template out of a **client-supplied
string** and renders it:

```python
render_string = (
    "{% from 'tuner/_macro_tuner.html' import render_"
    + requestform["value"]
    + " %}{{ render_"
    + requestform["value"]
    + "(settings, control) }}"
)
return render_template_string(render_string, settings=settings, control=control)
```

`requestform["value"]` is concatenated into template SOURCE, so anything it
contains is parsed as Jinja, not escaped as data. This is server-side template
injection, the same family as the traversal doors the admin and events slices
found.

The client only ever sends six literal values
(`static/tuner/js/tuner.js:91,120,127,254,296,303`):

```
manual_instruction_card   manual_tool   manual_finish_btn
auto_instruction_card     auto_tool     auto_finish_btn
```

`_macro_tuner.html` defines **seven** macros; the seventh,
`render_manual_tool_card`, is only called from inside `render_manual_tool` and
is never requested directly. The allowlist is therefore exactly the six above.

`tests/web/test_page_tuner.py:81` pins this endpoint with
`{"command": "render", "value": "manual_instruction_card"}` and must keep
passing.

**Not the same bug:** `blueprints/settings/routes.py:353`
(`_settings_controller_card`) also calls `render_template_string`, but its
template is a **constant** and the request value is passed as a template
VARIABLE. That one is fine. Do not "fix" it.

### What a tuning session does to `control`

From `tuner_page`'s `read_tr` / `read_auto_status` branches:

- Sets `tuning_mode: True` if not already set.
- If `control["mode"] == Mode.STOP`, sets `mode: Mode.MONITOR` and
  `updated: True`.

and from `stop_tuning` / `manual_finish` / `auto_finish`:

- Sets `tuning_mode: False` if set.
- If `control["mode"] == Mode.MONITOR`, sets `mode: Mode.STOP` and
  `updated: True`.

`Mode.STOP == "Stop"` and `Mode.MONITOR == "Monitor"` (`common/modes.py:15,20`).
Writes go through `write_control(control_delta(set_values={...}),
WriteKind.DELTA, origin="app")`.

**The asymmetry that matters:** closing only restores Stop when the mode is
*currently* Monitor. If the operator started a cook while the session was open,
closing leaves the cook alone. Preserve that — it is correct.

### The readings

`read_tr()` (`common/datastore_accessors.py:662`) returns the
`control:tuning` JSON blob: a dict keyed by probe LABEL, values are resistance
in ohms. A label that is not present means the probe is not reporting; Flask
answers `{"trohms": 0}` for a missing key, which is indistinguishable from a
real zero reading. This slice reports `null` instead and says so on screen.

### The maths, and how it fails

`blueprints/tuner/tuner.py`:

- `calc_shh_coefficients(t1, t2, t3, r1, r2, r3, units="F")` → `(a, b, c)`.
  Converts F→K (or C→K), then the standard Steinhart-Hart solve. Wrapped in a
  **bare `except:`** that logs and returns `(0, 0, 0)`.
- `calc_shh_chart(a, b, c, units, temp_range=220, tr_points=[])` → `(labels,
  chart_data)`. `labels` is `range(0, 220, 11)` — 20 points. `chart_data` is
  `[{"x": temp, "y": tr}]`, and is returned **empty** the moment any point
  fails.
- `temp_to_tr`'s own docstring: *"Not recommended for use, as it commonly
  produces a complex number"* — `math.sqrt` of a negative throws, caught, returns
  0, which `calc_shh_chart` treats as the signal to abandon the whole chart.

So two distinct failure modes exist and Flask reports neither: coefficients of
`(0, 0, 0)` flow straight into the save form, and an empty chart is drawn as an
empty chart. **Task 4 gives each an explicit signal.** `tuner.py`'s functions
are NOT changed — `tests/web/test_page_tuner.py:118` pins their current return
shape, and other callers exist. The new endpoint interprets their output.

### Where a finished profile goes

`_settings_addprofile` (`blueprints/settings/routes.py:316`) reads a **form**:
`Name`, `A`, `B`, `C`, and optional `apply_profile` (a probe LABEL). It writes
`settings["probe_settings"]["probe_profiles"][<new uuid>] = {A, B, C, name, id}`
and, when `apply_profile` is present, copies that profile onto the matching
entry of `probe_settings.probe_map.probe_info[]` by `label`. Bare `except:` on
the float conversions.

Task 5 does the same two writes through JSON with validated numbers, and does
NOT route through `_settings_addprofile` — that handler takes `request.form`
directly off the global, so it is not callable without faking a request context.

### React conventions this page follows

- `BASE_URL` is `import.meta.env.PUBLIC_PIFIRE_URL || ""` — same-origin, never
  the shell context's `targetUrl` (absolute; Flask sends no CORS headers).
- Typed client mirrors `src/helpers/admin/adminApi.ts` and
  `src/helpers/metrics/metricsApi.ts`: an `unpack()` over the
  `{data, result, message}` envelope resolving to a result object, never
  throwing. A refusal is an expected outcome here (the grill is lit), so the
  page must render the reason.
- Unit tests stub `fetch` with a module-level `rs.fn()` + `rs.stubGlobal`
  (`adminApi.test.ts`); PAGE tests mock the API module instead
  (`AdminPage.test.tsx`), through a lazy wrapper so the hoisted factory never
  captures an uninitialised binding.
- A heading `id` used by `aria-labelledby` must NOT start with `pf-` —
  `cssCoverage`'s `classesUsedIn()` scans for `pf-*` and would take it for a
  class with no rule.

### Charting ruling

**Do not reuse `HistoryChart`.** Its x-axis is time-in-seconds on a uPlot canvas;
the tuner curve is Temp (x) vs Tr (y), 20 points, no zoom or pan. Reusing it
would mean fighting a time scale, and canvas is unassertable in jsdom without a
stub. Task 9 draws an inline **SVG polyline** instead: 20 points is nothing, it
needs no library, and every coordinate is readable from the DOM in a unit test.

---

## File Structure

**Create**

| Path | Responsibility |
|---|---|
| `blueprints/api_tuner/__init__.py` | Blueprint, `url_prefix="/api/tuner"` |
| `blueprints/api_tuner/routes.py` | session, tr, coefficients, profile |
| `tests/web/test_api_tuner.py` | All four endpoints and their guards |
| `tests/web/test_tuner_template_allowlist.py` | The SSTI fix (Task 1) |
| `web-react/src/helpers/tuner/tunerTypes.ts` | Payload and result types |
| `web-react/src/helpers/tuner/tunerApi.ts` | Typed client |
| `web-react/src/helpers/tuner/tunerApi.test.ts` | Client unit tests |
| `web-react/src/helpers/tuner/useTunerSession.ts` | Session lifetime + teardown |
| `web-react/src/helpers/tuner/useTunerSession.test.tsx` | Teardown proof |
| `web-react/src/components/tuner/SegmentCard.tsx` | One of High/Medium/Low |
| `web-react/src/components/tuner/SegmentCard.test.tsx` | |
| `web-react/src/components/tuner/TunerChart.tsx` | SVG Temp-vs-Tr curve |
| `web-react/src/components/tuner/TunerChart.test.tsx` | |
| `web-react/src/components/tuner/ProfileForm.tsx` | Name + a/b/c + save actions |
| `web-react/src/components/tuner/ProfileForm.test.tsx` | |
| `web-react/src/components/tuner/TunerPage.tsx` | The page |
| `web-react/src/components/tuner/TunerPage.test.tsx` | |
| `web-react/src/components/tuner/tuner.css` | Every `pf-tuner-*` rule |
| `web-react/tests/e2e/tuner.spec.ts` | Live e2e, session-safe |

**Modify**

| Path | Change |
|---|---|
| `blueprints/tuner/routes.py` | Macro allowlist (Task 1) |
| `app.py` | Register `api_tuner_bp` (Task 2) |
| `web-react/src/components/App.tsx` | `/tuner` route (Task 10) |
| `web-react/src/components/settings/tabs/ProbesTab.tsx` | Link to `/tuner` (Task 10) |
| `web-react/tests/e2e/apiFixtures.ts` | `stubTuner` (Task 11) |
| `web-react/tests/e2e/pageSpecs.ts` | `tuner` page spec (Task 11) |
| `docs/superpowers/backlogs/react-migration-backlog.md` | Closeout (Task 11) |

**Deliberately not created:** no auto-flow endpoint or component. `GET
/api/tuner/auto-status` and the autotune store are slice 2; this slice's session
and client are built so slice 2 adds one endpoint and one component.

## Parallelization

- **Tasks 1–5 (backend)** are serial after Task 2 creates the blueprint; Task 1
  is independent of all of them and can go first or in parallel in its own
  workspace.
- **Tasks 6–9 (frontend)** are serial — each imports the previous one's exports.
- **Slice A (1–5) and Slice B (6–9) can run concurrently in separate jj
  workspaces**: Task 6 types against the contract stated in Tasks 2–5, not
  against a running server. Tasks 10–11 are the join point.
- Concurrency needs `jj workspace add` — disjoint files are not sufficient
  isolation. Copy `.lsp.json` and run `bun install` in the new workspace (both
  gitignored, so `workspace add` skips them).

---

## Slice A — the JSON surface

### Task 1: Close the template-injection door

**Files:**
- Modify: `blueprints/tuner/routes.py`
- Test: `tests/web/test_tuner_template_allowlist.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing the later tasks import. Independent; sequence it first
  because it is the security fix and should not wait behind a port.

- [ ] **Step 1: Confirm the blueprint has no shell-out reachable from a test**

```
grep -nE "os\.system|subprocess|sudo|reboot|shutdown|popen" blueprints/tuner/
```

Expected: no matches. If any appear, STOP and report — the mock-first rule
applies before any test in this task runs.

- [ ] **Step 2: Write the failing test**

Create `tests/web/test_tuner_template_allowlist.py`:

```python
"""`/tuner`'s fragment endpoint renders a macro NAMED by the client.

Before 2026-07-28 that name was concatenated straight into Jinja source and
handed to render_template_string, so the value was parsed as template code
rather than escaped as data -- server-side template injection. The six names
the client actually sends are now an allowlist.

See docs/superpowers/plans/2026-07-28-react-tuner-manual.md.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


ALLOWED = [
    "manual_instruction_card",
    "manual_tool",
    "manual_finish_btn",
    "auto_instruction_card",
    "auto_tool",
    "auto_finish_btn",
]


@pytest.mark.parametrize("name", ALLOWED)
def test_every_name_the_client_sends_still_renders(ds, client, name):
    """The six literals in static/tuner/js/tuner.js. If one of these 404s, the
    legacy page has a blank panel and no error anywhere."""
    resp = client.post("/tuner/", data={"command": "render", "value": name})
    assert resp.status_code == 200
    assert resp.get_data(as_text=True).strip() != ""


def test_a_macro_the_client_never_requests_is_refused(ds, client):
    """render_manual_tool_card is defined in _macro_tuner.html but is only
    called from inside render_manual_tool. Reachable != offered."""
    resp = client.post("/tuner/", data={"command": "render", "value": "manual_tool_card"})
    assert resp.status_code == 400


def test_template_syntax_in_the_name_is_not_executed(ds, client):
    """The injection itself.

    `value` used to be concatenated into template SOURCE, so this payload
    closed the import statement and opened an expression the renderer would
    evaluate. Asserting on the STATUS is not enough -- a 200 whose body
    contains the evaluated result would pass that check.
    """
    payload = "x %}{{ 7*6 }}{% from 'tuner/_macro_tuner.html' import render_manual_tool"
    resp = client.post("/tuner/", data={"command": "render", "value": payload})
    assert resp.status_code == 400
    assert b"42" not in resp.get_data()


def test_a_name_naming_another_template_is_refused(ds, client):
    resp = client.post("/tuner/", data={"command": "render", "value": "../../settings/_macro_settings.html"})
    assert resp.status_code == 400
```

- [ ] **Step 3: Run it to verify it fails**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_tuner_template_allowlist.py -q
```

Expected: the three refusal tests FAIL. `test_template_syntax_in_the_name_is_not_executed`
should fail on the status assertion **and** `b"42"` should be present — record
whether it is, because that is the proof the door was real.

- [ ] **Step 4: Add the allowlist**

In `blueprints/tuner/routes.py`, above `tuner_page`:

```python
#: The macro fragments the tuner page may ask the server to render, mapped from
#: the name the client sends to the macro that answers it.
#:
#: This is an ALLOWLIST, not a validation step, and the difference is the point:
#: the name used to be concatenated into Jinja SOURCE and handed to
#: render_template_string, so `value` was parsed as template code rather than
#: escaped as data. Every template string below is a constant chosen by key --
#: no request value reaches the renderer as source.
#:
#: render_manual_tool_card is deliberately absent: it is defined in
#: _macro_tuner.html but only ever called from inside render_manual_tool, and
#: the client never asks for it. Reachable is not the same as offered.
_RENDERABLE_FRAGMENTS = {
    name: (
        "{% from 'tuner/_macro_tuner.html' import render_" + name + " %}{{ render_" + name + "(settings, control) }}"
    )
    for name in (
        "manual_instruction_card",
        "manual_tool",
        "manual_finish_btn",
        "auto_instruction_card",
        "auto_tool",
        "auto_finish_btn",
    )
}
```

and replace the render branch:

```python
        if "command" in requestform.keys():
            if "render" in requestform["command"]:
                render_string = _RENDERABLE_FRAGMENTS.get(requestform.get("value", ""))
                if render_string is None:
                    return jsonify({"error": "unknown_fragment"}), 400
                return render_template_string(render_string, settings=settings, control=control)
```

`jsonify` is already imported.

- [ ] **Step 5: Run the new tests and the characterization net**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/web/test_tuner_template_allowlist.py tests/web/test_page_tuner.py -q
```

Expected: all PASS. `test_page_tuner.py:81` uses `manual_instruction_card`,
which is on the allowlist.

- [ ] **Step 6: Negative control**

Temporarily restore the concatenation. `test_template_syntax_in_the_name_is_not_executed`
must fail **and** report `42` in the body. Restore the allowlist. A refusal
test that passes against the vulnerable code is asserting nothing.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format blueprints/tuner/routes.py tests/web/test_tuner_template_allowlist.py
jj new
jj describe --stdin <<'EOF'
fix(tuner): allowlist the renderable fragments instead of building Jinja from a request value

The fragment name was concatenated into template SOURCE and handed to
render_template_string, so `value` was parsed as Jinja rather than
escaped as data. The client only ever sends six literals; those six are
now keys into constant template strings.
EOF
```

---

### Task 2: The session endpoint

**Files:**
- Create: `blueprints/api_tuner/__init__.py`, `blueprints/api_tuner/routes.py`
- Create: `tests/web/test_api_tuner.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `POST /api/tuner/session {"open": bool}` →
  `200 {"data": {"open": bool, "mode": str, "restored": bool}, "result": "OK"}`,
  or `409 "not_tunable"` with `data.mode`. Tasks 3–5 reuse `require_tunable()`;
  Task 6 types the payload.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_tuner.py`:

```python
"""The JSON tuner surface the React /tuner page drives.

SAFETY: opening a session moves the grill from Stop to MONITOR. Monitor lights
nothing -- it reads probes -- but it is a real mode change, so every test below
that opens one closes it, and the module-level fixture asserts the grill is
back in Stop afterwards. See
docs/superpowers/plans/2026-07-28-react-tuner-manual.md.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def grill_left_stopped(ds):
    """Every test in this module must hand the grill back in Stop.

    autouse and post-yield: a test that opens a session and then fails its
    assertion would otherwise leave tuning_mode set for every test after it,
    and the failure would be attributed to the wrong test.
    """
    yield
    from common.datastore_accessors import read_control

    control = read_control()
    assert control["mode"] == "Stop", "a test left the grill out of Stop"
    assert not control.get("tuning_mode"), "a test left tuning_mode set"


def set_mode(mode):
    from common.common import WriteKind
    from common.control_delta import control_delta
    from common.datastore_accessors import write_control

    write_control(control_delta(set_values={"mode": mode}), WriteKind.DELTA, origin="test")


def test_opening_a_session_enables_tuning_and_monitors(ds, client):
    from common.datastore_accessors import read_control

    set_mode("Stop")
    body = client.post("/api/tuner/session", json={"open": True}).get_json()
    assert body["result"] == "OK"
    assert body["data"]["open"] is True
    assert body["data"]["mode"] == "Monitor"

    control = read_control()
    assert control["tuning_mode"] is True
    assert control["mode"] == "Monitor"

    client.post("/api/tuner/session", json={"open": False})


def test_closing_a_session_restores_stop(ds, client):
    from common.datastore_accessors import read_control

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})

    body = client.post("/api/tuner/session", json={"open": False}).get_json()
    assert body["data"]["open"] is False
    assert body["data"]["restored"] is True

    control = read_control()
    assert control["tuning_mode"] is False
    assert control["mode"] == "Stop"


def test_closing_is_idempotent(ds, client):
    """The React hook closes on unmount, and an unmount can follow an explicit
    Finish. Closing twice must not be an error and must not touch the mode the
    second time."""
    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    client.post("/api/tuner/session", json={"open": False})

    body = client.post("/api/tuner/session", json={"open": False}).get_json()
    assert body["result"] == "OK"
    assert body["data"]["restored"] is False


def test_a_cooking_grill_refuses_to_open_a_session(ds, client):
    """Tuning from Hold would fight the controller for the probes and lie about
    what the grill is doing. Flask offers no such guard; this one matches the
    409 shape /api/admin/system already uses."""
    set_mode("Hold")
    try:
        resp = client.post("/api/tuner/session", json={"open": True})
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["message"] == "not_tunable"
        assert body["data"]["mode"] == "Hold"

        from common.datastore_accessors import read_control

        assert not read_control().get("tuning_mode"), "a refused open still wrote tuning_mode"
    finally:
        set_mode("Stop")


def test_closing_a_session_does_not_stop_a_cook(ds, client):
    """The asymmetry in Flask that is CORRECT and must survive the port: close
    only restores Stop when the mode is currently Monitor. If a cook started
    while the session was open, closing leaves it alone."""
    from common.datastore_accessors import read_control

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    set_mode("Hold")
    try:
        body = client.post("/api/tuner/session", json={"open": False}).get_json()
        assert body["data"]["restored"] is False
        assert read_control()["mode"] == "Hold"
        assert read_control()["tuning_mode"] is False
    finally:
        set_mode("Stop")


def test_the_open_flag_must_be_a_bool(ds, client):
    resp = client.post("/api/tuner/session", json={"open": "yes"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "open"


def test_the_generic_api_catchall_does_not_swallow_this_path(ds, client):
    """blueprints/api registers /api/<action>/<arg0> for GET and POST, which
    matches /api/tuner/session. See blueprints/api_admin/routes.py's docstring
    for the case where a request fell through to it and 404'd from elsewhere."""
    with flask_app.test_request_context("/api/tuner/session", method="POST"):
        from flask import request

        assert request.endpoint == "api_tuner_bp.tuner_session"
```

- [ ] **Step 2: Run to verify failure**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q
```

Expected: FAIL — the blueprint does not exist, so the requests reach
`blueprints/api`'s catch-all.

- [ ] **Step 3: Create the package**

`blueprints/api_tuner/__init__.py`:

```python
from flask import Blueprint

api_tuner_bp = Blueprint("api_tuner_bp", __name__, url_prefix="/api/tuner")

from . import routes  # noqa: E402,F401
```

- [ ] **Step 4: Write the session route**

`blueprints/api_tuner/routes.py`:

```python
"""JSON endpoints for PiFire's probe tuner.

The SESSION is separated from the READING, which is the one structural change
from Flask. `/tuner`'s read_tr command both enables tuning mode and returns a
value, so a page that merely polls mutates grill state on every tick and there
is no request that means "stop". Here exactly two calls write control -- open
and close -- and everything else is a pure read.

Opening moves a stopped grill to Monitor. Monitor lights nothing, but it is a
real mode change, so opening is refused from any mode that is neither Stop nor
Monitor: tuning during a cook would fight the controller for the probes.
"""

from flask import jsonify, request
from werkzeug.exceptions import BadRequest

from common.app import api_response
from common.common import WriteKind
from common.control_delta import control_delta
from common.datastore_accessors import read_control, write_control
from common.modes import Mode

from . import api_tuner_bp

#: The only two modes a tuning session may be opened from. Stop is the normal
#: case; Monitor is allowed because the session itself puts the grill there,
#: which makes re-opening after a reload a no-op rather than a refusal.
TUNABLE_MODES = (Mode.STOP, Mode.MONITOR)


def error(message, status, **data):
    return jsonify(api_response("Error", message, data or None)), status


def json_body():
    try:
        return request.get_json(silent=True) or {}
    except BadRequest:
        return {}


def set_control(**values):
    write_control(control_delta(set_values=values), WriteKind.DELTA, origin="api-tuner")


def require_tunable():
    """None if a session may be opened, else a 409 refusing it.

    Re-reads control rather than taking it as an argument: the caller's copy
    may predate the request, and this is the guard between a web request and a
    mode change on a live grill.
    """
    control = read_control()
    if control.get("mode") not in TUNABLE_MODES:
        return error("not_tunable", 409, mode=control.get("mode"))
    return None


@api_tuner_bp.route("/session", methods=["POST"])
def tuner_session():
    """Open or close a tuning session.

    Closing is IDEMPOTENT and closing never stops a cook: it restores Stop only
    when the mode is currently Monitor, so a cook started while the session was
    open is left alone. `restored` reports whether the mode was actually moved,
    which is what makes "close twice" observable rather than silent.
    """
    body = json_body()
    if not isinstance(body.get("open"), bool):
        return error("bad_request", 400, field="open")

    if body["open"]:
        refusal = require_tunable()
        if refusal:
            return refusal
        control = read_control()
        moved = control.get("mode") == Mode.STOP
        values = {"tuning_mode": True}
        if moved:
            values.update({"mode": Mode.MONITOR, "updated": True})
        set_control(**values)
        return jsonify(api_response("OK", None, {"open": True, "mode": Mode.MONITOR, "restored": moved})), 200

    control = read_control()
    restored = control.get("mode") == Mode.MONITOR
    values = {"tuning_mode": False}
    if restored:
        values.update({"mode": Mode.STOP, "updated": True})
    set_control(**values)
    return jsonify(
        api_response(
            "OK",
            None,
            {"open": False, "mode": control.get("mode") if not restored else Mode.STOP, "restored": restored},
        )
    ), 200
```

- [ ] **Step 5: Register it**

In `app.py`, beside the other API blueprints:

```python
from blueprints.api_tuner import api_tuner_bp
```

```python
app.register_blueprint(api_tuner_bp, url_prefix="/api/tuner")
```

- [ ] **Step 6: Run the tests**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q
```

Expected: 8 PASS.

- [ ] **Step 7: Negative control on the 409 guard**

Delete the `refusal = require_tunable()` lines.
`test_a_cooking_grill_refuses_to_open_a_session` must fail on BOTH the status
and the `tuning_mode` assertion. Restore them.

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_tuner/ tests/web/test_api_tuner.py app.py
jj new
jj describe --stdin <<'EOF'
feat(api_tuner): a tuning session that is opened and closed explicitly

Flask's read_tr both enables tuning mode and returns a reading, so a
polling page mutated grill state on every tick and no request meant
"stop". Exactly two calls write control now. Opening is refused from any
mode that is neither Stop nor Monitor; closing is idempotent and never
stops a cook.
EOF
```

---

### Task 3: The Tr reading

**Files:**
- Modify: `blueprints/api_tuner/routes.py`, `tests/web/test_api_tuner.py`

**Interfaces:**
- Consumes: Task 2's helpers.
- Produces: `GET /api/tuner/tr?probe=<label>` →
  `200 {"data": {"probe": str, "trohms": number | null, "tuning": bool}}`.
  Task 6 types it; Task 8 polls it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_api_tuner.py`:

```python
def seed_tr(values):
    """Write the control:tuning blob read_tr() reads.

    write_tr is the public writer for exactly this blob
    (common/datastore_accessors.py:654) -- do not reach for _write_json_blob.
    """
    from common.datastore_accessors import write_tr

    write_tr(values)


def test_tr_reports_a_reading_for_a_known_probe(ds, client):
    seed_tr({"Grill": 51234})
    body = client.get("/api/tuner/tr?probe=Grill").get_json()
    assert body["result"] == "OK"
    assert body["data"]["probe"] == "Grill"
    assert body["data"]["trohms"] == 51234


def test_tr_reports_null_for_a_probe_that_is_not_reporting(ds, client):
    """Flask answers {"trohms": 0} for a missing key, which a client cannot
    tell apart from a real zero-ohm reading. null is the honest answer and the
    page renders it as "waiting"."""
    seed_tr({"Grill": 51234})
    body = client.get("/api/tuner/tr?probe=Probe1").get_json()
    assert body["data"]["trohms"] is None


def test_tr_requires_a_probe(ds, client):
    resp = client.get("/api/tuner/tr")
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "probe"


def test_tr_does_not_write_control(ds, client):
    """The whole reason session and reading are separate endpoints. This is a
    GET and it must be inert: the page polls it once a second."""
    from common.datastore_accessors import read_control

    seed_tr({"Grill": 51234})
    before = read_control()
    client.get("/api/tuner/tr?probe=Grill")
    after = read_control()
    assert after["mode"] == before["mode"]
    assert after.get("tuning_mode") == before.get("tuning_mode")


def test_tr_reports_whether_a_session_is_open(ds, client):
    """A reading taken with no session is stale by definition -- control.py
    only refreshes the tuning blob in tuning mode -- so the flag rides along
    and the page can say so instead of showing a frozen number."""
    seed_tr({"Grill": 51234})
    assert client.get("/api/tuner/tr?probe=Grill").get_json()["data"]["tuning"] is False
```

`write_tr(tr_data)` and `read_tr()` are the matched pair over the
`control:tuning` blob (`common/datastore_accessors.py:654` and `:662`), both
public. Verified 2026-07-28.

- [ ] **Step 2: Run to verify failure**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q -k tr
```

Expected: FAIL — no such route.

- [ ] **Step 3: Write the route**

Append to `blueprints/api_tuner/routes.py` (add `read_tr` to the
`datastore_accessors` import):

```python
@api_tuner_bp.route("/tr", methods=["GET"])
def tuner_tr():
    """The current resistance reading for one probe, in ohms.

    Inert by design: the page polls this once a second, and a poll that moved
    the grill between modes is exactly the shape this blueprint exists to
    avoid.

    A probe that is not in the blob reads `null`, not 0. Flask returns 0, which
    a client cannot tell apart from a real zero-ohm reading -- and 0 ohms is
    what a shorted probe reports, so the two cases genuinely differ.
    """
    probe = request.args.get("probe", "")
    if not probe:
        return error("bad_request", 400, field="probe")

    readings = read_tr()
    control = read_control()
    return jsonify(
        api_response(
            "OK",
            None,
            {
                "probe": probe,
                "trohms": readings.get(probe),
                #  A reading taken outside a session is stale: control.py only
                #  refreshes this blob in tuning mode.
                "tuning": bool(control.get("tuning_mode")),
            },
        )
    ), 200
```

- [ ] **Step 4: Run the module**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q
```

Expected: 13 PASS.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_tuner/routes.py tests/web/test_api_tuner.py
jj new
jj describe --stdin <<'EOF'
feat(api_tuner): an inert Tr reading

A GET that writes nothing, so a once-a-second poll cannot move the grill
between modes. A probe absent from the tuning blob reads null rather
than Flask's 0, which is indistinguishable from a shorted probe.
EOF
```

---

### Task 4: Coefficients and the chart

**Files:**
- Modify: `blueprints/api_tuner/routes.py`, `tests/web/test_api_tuner.py`

**Interfaces:**
- Consumes: Task 2's `error`/`json_body`.
- Produces: `POST /api/tuner/coefficients` with
  `{"points": [{"segment": "High"|"Medium"|"Low", "temp": number, "trohms": number}, ...]}`
  → `200 {"data": {"a","b","c", "chart": [{"x","y"}], "chart_ok": bool}}`, or
  `422 "uncomputable"`. Task 6 types it; Task 9 charts it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_api_tuner.py`:

```python
def points(high=(400, 1200), medium=(250, 6000), low=(100, 40000)):
    return [
        {"segment": "High", "temp": high[0], "trohms": high[1]},
        {"segment": "Medium", "temp": medium[0], "trohms": medium[1]},
        {"segment": "Low", "temp": low[0], "trohms": low[1]},
    ]


def test_coefficients_are_computed_from_three_points(ds, client):
    body = client.post("/api/tuner/coefficients", json={"points": points()}).get_json()
    assert body["result"] == "OK"
    data = body["data"]
    for key in ("a", "b", "c"):
        assert isinstance(data[key], float)
    #  Not all three zero: that tuple is exactly what calc_shh_coefficients
    #  returns from its bare `except:`, and Flask fed it straight to the save
    #  form. A 200 carrying (0, 0, 0) is the bug this endpoint refuses to have.
    assert (data["a"], data["b"], data["c"]) != (0, 0, 0)


def test_an_uncomputable_set_is_refused_rather_than_saved_as_zeros(ds, client):
    """calc_shh_coefficients swallows every exception and returns (0, 0, 0).
    Two identical resistances divide by zero in step 3."""
    resp = client.post(
        "/api/tuner/coefficients",
        json={"points": points(high=(400, 5000), medium=(250, 5000))},
    )
    assert resp.status_code == 422
    assert resp.get_json()["message"] == "uncomputable"


def test_the_chart_is_reported_as_missing_rather_than_empty(ds, client):
    """calc_shh_chart abandons the whole series the moment temp_to_tr throws --
    which its own docstring says is common. An empty list and a list that
    genuinely has no points look identical, so the flag carries the difference.
    """
    body = client.post("/api/tuner/coefficients", json={"points": points()}).get_json()
    data = body["data"]
    assert isinstance(data["chart"], list)
    assert data["chart_ok"] == (len(data["chart"]) > 0)


def test_all_three_segments_are_required(ds, client):
    resp = client.post("/api/tuner/coefficients", json={"points": points()[:2]})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "points"


def test_a_non_numeric_reading_is_refused(ds, client):
    bad = points()
    bad[0]["trohms"] = "lots"
    resp = client.post("/api/tuner/coefficients", json={"points": bad})
    assert resp.status_code == 400


def test_coefficients_does_not_write_control(ds, client):
    from common.datastore_accessors import read_control

    before = read_control()
    client.post("/api/tuner/coefficients", json={"points": points()})
    assert read_control()["mode"] == before["mode"]
```

- [ ] **Step 2: Run to verify failure**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q -k coefficients
```

Expected: FAIL — no such route.

- [ ] **Step 3: Write the route**

Append to `blueprints/api_tuner/routes.py`, importing the maths and settings:

```python
from blueprints.tuner.tuner import calc_shh_chart, calc_shh_coefficients
from common.datastore_accessors import read_settings

SEGMENTS = ("High", "Medium", "Low")


@api_tuner_bp.route("/coefficients", methods=["POST"])
def tuner_coefficients():
    """Solve Steinhart-Hart for three temperature/resistance pairs.

    The maths itself is blueprints/tuner/tuner.py's, unchanged -- one
    definition, and tests/web/test_page_tuner.py pins its return shape. What is
    new is that both of its silent failures get a signal:

      * calc_shh_coefficients wraps everything in a bare `except:` and returns
        (0, 0, 0). Flask handed that tuple to the save form, so a failed tune
        produced a saveable profile of zeros. Here it is a 422.
      * calc_shh_chart abandons the whole series the moment temp_to_tr throws,
        which its own docstring calls common. An empty chart is reported as
        chart_ok: false rather than drawn as an empty chart.
    """
    body = json_body()
    raw = body.get("points")
    if not isinstance(raw, list) or len(raw) != 3:
        return error("bad_request", 400, field="points")

    by_segment = {}
    for entry in raw:
        if not isinstance(entry, dict):
            return error("bad_request", 400, field="points")
        segment = entry.get("segment")
        if segment not in SEGMENTS:
            return error("bad_request", 400, field="segment")
        try:
            #  bool is a subclass of int, so `True` would otherwise sail
            #  through float() and become a 1-ohm reading.
            for key in ("temp", "trohms"):
                if isinstance(entry.get(key), bool):
                    raise TypeError(key)
            by_segment[segment] = (float(entry["temp"]), float(entry["trohms"]))
        except TypeError, ValueError, KeyError:
            return error("bad_request", 400, field="points")

    if set(by_segment) != set(SEGMENTS):
        return error("bad_request", 400, field="points")

    units = read_settings()["globals"]["units"]
    (high_t, high_r) = by_segment["High"]
    (medium_t, medium_r) = by_segment["Medium"]
    (low_t, low_r) = by_segment["Low"]

    a, b, c = calc_shh_coefficients(low_t, medium_t, high_t, low_r, medium_r, high_r, units=units)
    if (a, b, c) == (0, 0, 0):
        return error("uncomputable", 422)

    _labels, chart = calc_shh_chart(
        a, b, c, units=units, temp_range=220, tr_points=[int(high_r), int(medium_r), int(low_r)]
    )
    return jsonify(api_response("OK", None, {"a": a, "b": b, "c": c, "chart": chart, "chart_ok": bool(chart)})), 200
```

- [ ] **Step 4: Run the module**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q
```

Expected: 19 PASS. If `test_coefficients_are_computed_from_three_points` fails
because the sample triple is itself uncomputable, pick a real thermistor triple
and record the values used — do NOT weaken the assertion to allow `(0, 0, 0)`.

- [ ] **Step 5: Negative control**

Remove the `(a, b, c) == (0, 0, 0)` check.
`test_an_uncomputable_set_is_refused_rather_than_saved_as_zeros` must fail.
Restore it.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_tuner/routes.py tests/web/test_api_tuner.py
jj new
jj describe --stdin <<'EOF'
feat(api_tuner): coefficients with an honest failure signal

The maths is blueprints/tuner/tuner.py's, unchanged. What is new is that
its two silent failures get reported: a (0, 0, 0) solve is a 422 rather
than a saveable profile of zeros, and an abandoned chart is chart_ok
false rather than an empty series drawn as an empty series.
EOF
```

---

### Task 5: Saving a profile

**Files:**
- Modify: `blueprints/api_tuner/routes.py`, `tests/web/test_api_tuner.py`

**Interfaces:**
- Consumes: Task 2's helpers.
- Produces: `POST /api/tuner/profile` with
  `{"name": str, "a": number, "b": number, "c": number, "apply_to": str | null}`
  → `200 {"data": {"id": str, "applied": str | null}}`. Task 6 types it;
  Task 9 submits it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_api_tuner.py`:

```python
PROFILE = {"name": "Test Probe", "a": 0.0007343140544, "b": 0.0002157437229, "c": 0.0000000951568577}


def test_saving_a_profile_stores_it_under_a_new_id(ds, client):
    from common.datastore_accessors import read_settings

    body = client.post("/api/tuner/profile", json=PROFILE).get_json()
    assert body["result"] == "OK"
    new_id = body["data"]["id"]
    assert body["data"]["applied"] is None

    profiles = read_settings()["probe_settings"]["probe_profiles"]
    assert profiles[new_id]["name"] == "Test Probe"
    assert profiles[new_id]["A"] == PROFILE["a"]
    assert profiles[new_id]["id"] == new_id


def test_applying_a_profile_attaches_it_to_the_probe(ds, client):
    from common.datastore_accessors import read_settings

    label = read_settings()["probe_settings"]["probe_map"]["probe_info"][0]["label"]
    body = client.post("/api/tuner/profile", json={**PROFILE, "apply_to": label}).get_json()
    assert body["data"]["applied"] == label

    probe_info = read_settings()["probe_settings"]["probe_map"]["probe_info"]
    attached = next(p for p in probe_info if p["label"] == label)
    assert attached["profile"]["id"] == body["data"]["id"]


def test_applying_to_an_unknown_probe_is_refused_and_saves_nothing(ds, client):
    """Flask's _settings_addprofile loops looking for the label and silently
    does nothing when it does not match -- reporting success for a profile that
    was saved but never applied."""
    from common.datastore_accessors import read_settings

    before = set(read_settings()["probe_settings"]["probe_profiles"])
    resp = client.post("/api/tuner/profile", json={**PROFILE, "apply_to": "Nonexistent"})
    assert resp.status_code == 404
    assert set(read_settings()["probe_settings"]["probe_profiles"]) == before


@pytest.mark.parametrize("field", ["name", "a", "b", "c"])
def test_every_field_is_required(ds, client, field):
    payload = dict(PROFILE)
    del payload[field]
    resp = client.post("/api/tuner/profile", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == field


def test_a_blank_name_is_refused(ds, client):
    resp = client.post("/api/tuner/profile", json={**PROFILE, "name": "   "})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "name"


def test_a_non_numeric_coefficient_is_refused(ds, client):
    resp = client.post("/api/tuner/profile", json={**PROFILE, "a": "nope"})
    assert resp.status_code == 400
    assert resp.get_json()["data"]["field"] == "a"


def test_saving_does_not_write_control(ds, client):
    from common.datastore_accessors import read_control

    before = read_control()
    client.post("/api/tuner/profile", json=PROFILE)
    assert read_control()["mode"] == before["mode"]
```

- [ ] **Step 2: Run to verify failure**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q -k profile
```

Expected: FAIL — no such route.

- [ ] **Step 3: Write the route**

Append to `blueprints/api_tuner/routes.py`, adding
`from common.common import generate_uuid` (that is where
`_settings_addprofile` gets it — `blueprints/settings/routes.py:2`) and
`write_settings` to the `datastore_accessors` import:

```python
@api_tuner_bp.route("/profile", methods=["POST"])
def tuner_profile():
    """Save a probe profile, optionally attaching it to a probe.

    The same two writes _settings_addprofile makes, with three differences that
    are all deliberate:

      * numbers are validated instead of being float()-ed inside a bare
        `except:` that reports "something bad happened";
      * an apply_to that matches no probe is a 404, not a silent success --
        Flask loops looking for the label and simply does not find it, so the
        operator is told the profile was applied when it was not;
      * nothing is written at all when apply_to does not match, so a failed
        apply does not leave an orphan profile behind.

    Not routed through _settings_addprofile: that handler reads request.form
    off the global, so it is not callable without faking a request context.
    """
    body = json_body()

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return error("bad_request", 400, field="name")

    coefficients = {}
    for key in ("a", "b", "c"):
        value = body.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return error("bad_request", 400, field=key)
        coefficients[key] = float(value)

    apply_to = body.get("apply_to")
    if apply_to is not None and not isinstance(apply_to, str):
        return error("bad_request", 400, field="apply_to")

    settings = read_settings()
    probe_info = settings["probe_settings"]["probe_map"]["probe_info"]
    target = None
    if apply_to:
        target = next((i for i, p in enumerate(probe_info) if p["label"] == apply_to), None)
        if target is None:
            #  Refused BEFORE the profile is stored, so a bad label cannot
            #  leave an orphan behind.
            return error("not_found", 404, field="apply_to")

    profile_id = generate_uuid()
    profile = {
        "A": coefficients["a"],
        "B": coefficients["b"],
        "C": coefficients["c"],
        "name": name.strip(),
        "id": profile_id,
    }
    settings["probe_settings"]["probe_profiles"][profile_id] = profile
    if target is not None:
        probe_info[target]["profile"] = profile
    write_settings(settings)

    return jsonify(
        api_response("OK", None, {"id": profile_id, "applied": apply_to if target is not None else None})
    ), 200
```

- [ ] **Step 4: Run the module, then the whole backend suite**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner.py -q
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: 29 PASS in the module; the whole suite green. Report the total
against the pre-slice baseline of **3400**.

- [ ] **Step 5: Negative control**

Move the `apply_to` lookup to AFTER `write_settings`.
`test_applying_to_an_unknown_probe_is_refused_and_saves_nothing` must fail on
its second assertion (the orphan). Restore the order.

- [ ] **Step 6: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_tuner/routes.py tests/web/test_api_tuner.py
jj new
jj describe --stdin <<'EOF'
feat(api_tuner): save and optionally apply a probe profile

The two writes _settings_addprofile makes, with validated numbers, and
with an apply_to that matches no probe refused as a 404 before anything
is stored. Flask loops for the label, does not find it, and reports
success for a profile it never applied.
EOF
```

---

## Slice B — the React page

### Task 6: Typed client

**Files:**
- Create: `web-react/src/helpers/tuner/tunerTypes.ts`, `tunerApi.ts`,
  `tunerApi.test.ts`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: `TunerSession`, `TrReading`, `TunerPoint`, `Coefficients`,
  `SavedProfile`, `TunerResult<T>`; `openSession`, `closeSession`, `fetchTr`,
  `computeCoefficients`, `saveProfile`, `tunerErrorText`. Tasks 7–9 import them.

- [ ] **Step 1: Confirm the shapes against the live endpoints**

With `gunicorn` running (never `control.py`), and **restoring Stop afterwards**:

```
curl -s -XPOST localhost:5000/api/tuner/session -H 'Content-Type: application/json' -d '{"open":true}'
curl -s 'localhost:5000/api/tuner/tr?probe=Grill'
curl -s -XPOST localhost:5000/api/tuner/session -H 'Content-Type: application/json' -d '{"open":false}'
```

Then confirm `GET /api/get/control` reports mode `Stop` and no `tuning_mode`.
**If it does not, stop and fix that before writing any type** — a leaked session
is the failure this slice is most likely to cause.

Type from what came back, not from this plan.

- [ ] **Step 2: Write the failing tests**

Create `web-react/src/helpers/tuner/tunerApi.test.ts`, following
`src/helpers/admin/adminApi.test.ts`'s idiom exactly (module-level `rs.fn()`
installed with `rs.stubGlobal`, hand-built `{ok, status, json}` responses):

```ts
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  closeSession,
  computeCoefficients,
  fetchTr,
  openSession,
  saveProfile,
  tunerErrorText,
} from "./tunerApi";

const fetchMock = rs.fn();
rs.stubGlobal("fetch", fetchMock);

const envelope = (status: number, body: unknown) => ({
  ok: status < 400,
  status,
  json: async () => body,
});

const OK = (data: unknown = null) => envelope(200, { result: "OK", message: null, data });

beforeEach(() => {
  fetchMock.mockReset();
});

describe("session", () => {
  it("opens with an explicit boolean", async () => {
    fetchMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    const result = await openSession("");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/tuner/session");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ open: true });
    expect(result.data?.mode).toBe("Monitor");
  });

  it("closes with the same endpoint and the opposite flag", async () => {
    fetchMock.mockResolvedValue(OK({ open: false, mode: "Stop", restored: true }));
    await closeSession("");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ open: false });
  });

  it("surfaces a 409 refusal rather than throwing", async () => {
    fetchMock.mockResolvedValue(
      envelope(409, { result: "Error", message: "not_tunable", data: { mode: "Hold" } }),
    );
    const result = await openSession("");
    expect(result.ok).toBe(false);
    expect(result.message).toBe("not_tunable");
    expect(result.mode).toBe("Hold");
  });
});

describe("fetchTr", () => {
  it("encodes the probe label", async () => {
    fetchMock.mockResolvedValue(OK({ probe: "Probe 1", trohms: 51234, tuning: true }));
    await fetchTr("Probe 1", "");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/tuner/tr?probe=Probe%201");
  });

  it("keeps a null reading null", async () => {
    //  null means "not reporting". Coercing it to 0 would render as a real
    //  zero-ohm reading, which is what a shorted probe looks like.
    fetchMock.mockResolvedValue(OK({ probe: "Grill", trohms: null, tuning: true }));
    expect((await fetchTr("Grill", "")).data?.trohms).toBeNull();
  });
});

describe("computeCoefficients", () => {
  it("posts the three points", async () => {
    fetchMock.mockResolvedValue(OK({ a: 1, b: 2, c: 3, chart: [{ x: 0, y: 9 }], chart_ok: true }));
    const points = [
      { segment: "High" as const, temp: 400, trohms: 1200 },
      { segment: "Medium" as const, temp: 250, trohms: 6000 },
      { segment: "Low" as const, temp: 100, trohms: 40000 },
    ];
    const result = await computeCoefficients(points, "");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ points });
    expect(result.data?.chart_ok).toBe(true);
  });

  it("surfaces a 422 as an uncomputable result", async () => {
    fetchMock.mockResolvedValue(envelope(422, { result: "Error", message: "uncomputable", data: null }));
    const result = await computeCoefficients([], "");
    expect(result.ok).toBe(false);
    expect(result.message).toBe("uncomputable");
  });
});

describe("saveProfile", () => {
  it("sends null apply_to when saving only", async () => {
    fetchMock.mockResolvedValue(OK({ id: "abc", applied: null }));
    await saveProfile({ name: "P", a: 1, b: 2, c: 3, apply_to: null }, "");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).apply_to).toBeNull();
  });
});

describe("tunerErrorText", () => {
  it("explains a refusal in the operator's terms", () => {
    expect(tunerErrorText({ ok: false, status: 409, message: "not_tunable", data: null, mode: "Hold" }))
      .toContain("Hold");
    expect(tunerErrorText({ ok: false, status: 422, message: "uncomputable", data: null }))
      .toMatch(/could not be calculated/i);
  });

  it("passes an unrecognised message through unchanged", () => {
    expect(tunerErrorText({ ok: false, status: 500, message: "kaboom", data: null })).toBe("kaboom");
  });
});
```

- [ ] **Step 3: Run to verify failure**

```
cd web-react && bun run test src/helpers/tuner/tunerApi.test.ts
```

Expected: FAIL — cannot resolve `./tunerApi`.

- [ ] **Step 4: Write the types**

`web-react/src/helpers/tuner/tunerTypes.ts`:

```ts
// The shapes of the /api/tuner/* surface.
//
// Written from live responses, not from the Python literals. The one member
// worth naming here: `trohms` is `number | null`, and the null is load-bearing
// -- it means "this probe is not reporting". Flask answered 0 for that case,
// which is indistinguishable from a shorted probe reading a real zero.

/** The three points a manual tune records, in the order the page shows them. */
export type Segment = "High" | "Medium" | "Low";

export interface TunerPoint {
  segment: Segment;
  /** In the grill's configured units, as the operator read it off a thermometer. */
  temp: number;
  /** Resistance in ohms, captured from the live reading. */
  trohms: number;
}

export interface TunerSession {
  open: boolean;
  /** The mode the grill is in after the call. */
  mode: string;
  /** Whether this call actually MOVED the mode. False on a no-op close, and
   * false when a cook was running and was deliberately left alone. */
  restored: boolean;
}

export interface TrReading {
  probe: string;
  /** Ohms, or null when the probe is not reporting. Never coerce to 0. */
  trohms: number | null;
  /** False when no session is open, in which case the reading is stale:
   * control.py only refreshes the tuning blob in tuning mode. */
  tuning: boolean;
}

export interface Coefficients {
  a: number;
  b: number;
  c: number;
  /** Temp (x) vs Tr (y). Empty when the curve could not be evaluated. */
  chart: { x: number; y: number }[];
  /** Whether `chart` is empty because it failed, rather than because there was
   * nothing to draw. calc_shh_chart abandons the whole series on one bad
   * point, which its own docstring says is common. */
  chart_ok: boolean;
}

export interface ProfileInput {
  name: string;
  a: number;
  b: number;
  c: number;
  /** A probe LABEL to attach this profile to, or null for save-only. */
  apply_to: string | null;
}

export interface SavedProfile {
  id: string;
  applied: string | null;
}

/** Resolves rather than throws, matching helpers/admin/adminApi.ts: a refusal
 * is an expected outcome on this page (the grill is lit; the maths did not
 * converge), so every caller renders the reason instead of escaping past it. */
export interface TunerResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
  /** From a 409's data.mode: the mode that blocked the session. */
  mode?: string;
  /** From a 400's data.field. */
  field?: string;
}
```

- [ ] **Step 5: Write the client**

`web-react/src/helpers/tuner/tunerApi.ts`:

```ts
// Typed client for the /api/tuner/* surface.
//
// Only openSession and closeSession write anything. fetchTr is polled once a
// second and is a pure GET by design -- see blueprints/api_tuner's docstring
// for why the session and the reading are separate endpoints.

import type {
  Coefficients,
  ProfileInput,
  SavedProfile,
  TrReading,
  TunerPoint,
  TunerResult,
  TunerSession,
} from "./tunerTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const url = (baseUrl: string, path: string) => `${baseUrl}/api/tuner/${path}`;

async function unpack<T>(res: Response): Promise<TunerResult<T>> {
  const body = (await res.json().catch(() => ({}))) as {
    result?: string;
    message?: string;
    data?: (T & { mode?: string; field?: string }) | null;
  };
  const detail = body.data ?? null;
  return {
    ok: res.ok && body.result === "OK",
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    data: detail as T | null,
    mode: detail?.mode,
    field: detail?.field,
  };
}

async function get<T>(baseUrl: string, path: string): Promise<TunerResult<T>> {
  try {
    return await unpack<T>(await fetch(url(baseUrl, path)));
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

async function post<T>(baseUrl: string, path: string, body: unknown): Promise<TunerResult<T>> {
  try {
    return await unpack<T>(
      await fetch(url(baseUrl, path), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

/** Turn a refusal into copy for a human. The server's `message` is a machine
 * token that tests/web assert on, so the translation lives here and nowhere
 * else -- no component matches on the token itself. */
export function tunerErrorText(result: TunerResult<unknown>): string {
  switch (result.message) {
    case "not_tunable":
      return `The grill must be stopped before tuning — it is currently in ${result.mode || "another"} mode.`;
    case "uncomputable":
      return "Those three readings could not be calculated into a profile. Check that each temperature and resistance pair is different from the others, then try again.";
    case "not_found":
      return "That probe is no longer configured.";
    case "bad_request":
      return result.field
        ? `The server refused that request: ${result.field}.`
        : "The server refused that request.";
    default:
      return result.message;
  }
}

/** Enter tuning mode. Moves a STOPPED grill to Monitor; refused with 409
 * `not_tunable` from any mode that is neither Stop nor Monitor. */
export const openSession = (baseUrl = BASE_URL) =>
  post<TunerSession>(baseUrl, "session", { open: true });

/** Leave tuning mode, restoring Stop only if the grill is still in Monitor.
 * Idempotent: the page closes on unmount, which can follow an explicit Finish. */
export const closeSession = (baseUrl = BASE_URL) =>
  post<TunerSession>(baseUrl, "session", { open: false });

/** One probe's live resistance. Inert — safe to poll. */
export const fetchTr = (probe: string, baseUrl = BASE_URL) =>
  get<TrReading>(baseUrl, `tr?probe=${encodeURIComponent(probe)}`);

/** Solve Steinhart-Hart. Refused with 422 `uncomputable` rather than
 * answering the (0, 0, 0) the underlying function returns on failure. */
export const computeCoefficients = (points: TunerPoint[], baseUrl = BASE_URL) =>
  post<Coefficients>(baseUrl, "coefficients", { points });

export const saveProfile = (profile: ProfileInput, baseUrl = BASE_URL) =>
  post<SavedProfile>(baseUrl, "profile", profile);
```

- [ ] **Step 6: Run the tests, then the gates**

```
cd web-react && bun run test src/helpers/tuner/tunerApi.test.ts
bun run typecheck && bun run lint && bun run test
```

Expected: 10 passing in the file; the suite green. `bun run format` first if
Biome reports a formatting failure.

- [ ] **Step 7: Commit**

```bash
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): typed client for the /api/tuner surface

Only the two session calls write. trohms is `number | null` and the null
is load-bearing: it means "not reporting", which Flask's 0 could not be
told apart from a shorted probe.
EOF
```

---

### Task 7: The session hook

**Files:**
- Create: `web-react/src/helpers/tuner/useTunerSession.ts`,
  `useTunerSession.test.tsx`

**Interfaces:**
- Consumes: `openSession`, `closeSession`, `TunerResult` (Task 6).
- Produces: `useTunerSession(baseUrl?)` →
  `{ status: "idle" | "opening" | "open" | "refused" | "failed", error: string | null, start: () => void, stop: () => void }`.
  Task 10's page owns one.

**Why a hook and not page code:** the teardown guarantee is the single most
important behaviour in this slice, and it is the one thing a page test would
mock away. Isolated, it can be proved.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/helpers/tuner/useTunerSession.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import * as actualTunerApi from "./tunerApi" with { rstest: "importActual" };

const openMock = rs.fn();
const closeMock = rs.fn();
rs.mock("./tunerApi", () => ({
  ...actualTunerApi,
  openSession: (...a: unknown[]) => openMock(...a),
  closeSession: (...a: unknown[]) => closeMock(...a),
}));

const { useTunerSession } = await import("./useTunerSession");

const OK = (data: unknown) => ({ ok: true, status: 200, message: "", data });

function Host() {
  const session = useTunerSession("");
  return (
    <div>
      <span data-testid="status">{session.status}</span>
      <span data-testid="error">{session.error ?? ""}</span>
      <button type="button" onClick={session.start}>
        start
      </button>
      <button type="button" onClick={session.stop}>
        stop
      </button>
    </div>
  );
}

beforeEach(() => {
  openMock.mockReset();
  closeMock.mockReset();
  closeMock.mockResolvedValue(OK({ open: false, mode: "Stop", restored: true }));
});

describe("useTunerSession", () => {
  it("starts idle and opens nothing on mount", () => {
    render(<Host />);
    expect(screen.getByTestId("status")).toHaveTextContent("idle");
    //  Mounting must NOT move the grill. Navigating to /tuner and reading the
    //  instructions is not consent to switch the grill into Monitor.
    expect(openMock).not.toHaveBeenCalled();
  });

  it("opens on start", async () => {
    openMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("open"));
  });

  it("reports a refusal without claiming the session opened", async () => {
    openMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "not_tunable",
      data: null,
      mode: "Hold",
    });
    render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("refused"));
    expect(screen.getByTestId("error")).toHaveTextContent("Hold");
  });

  it("CLOSES THE SESSION ON UNMOUNT", async () => {
    //  The single most important assertion in this slice. A page that unmounts
    //  without closing leaves the operator's grill in Monitor with tuning_mode
    //  set, and nothing on screen to say so.
    openMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    const view = render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("open"));

    view.unmount();
    await waitFor(() => expect(closeMock).toHaveBeenCalledTimes(1));
  });

  it("does not close on unmount when it never opened", () => {
    render(<Host />).unmount();
    expect(closeMock).not.toHaveBeenCalled();
  });

  it("closes on unmount even when the open is still in flight", async () => {
    //  Navigating away mid-open is the race that leaves a session orphaned:
    //  the open lands on a page that no longer exists, so nothing ever closes
    //  it. The hook must close once the open resolves.
    let resolveOpen!: (v: unknown) => void;
    openMock.mockReturnValue(new Promise((r) => { resolveOpen = r; }));
    const view = render(<Host />);
    act(() => screen.getByText("start").click());
    view.unmount();

    await act(async () => {
      resolveOpen(OK({ open: true, mode: "Monitor", restored: true }));
    });
    await waitFor(() => expect(closeMock).toHaveBeenCalledTimes(1));
  });

  it("stop closes once and returns to idle", async () => {
    openMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    render(<Host />);
    act(() => screen.getByText("start").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("open"));

    act(() => screen.getByText("stop").click());
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("idle"));
    expect(closeMock).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run to verify failure**

```
cd web-react && bun run test src/helpers/tuner/useTunerSession.test.tsx
```

Expected: FAIL — cannot resolve `./useTunerSession`.

- [ ] **Step 3: Write the hook**

`web-react/src/helpers/tuner/useTunerSession.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { closeSession, openSession, tunerErrorText } from "./tunerApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export type SessionStatus = "idle" | "opening" | "open" | "refused" | "failed";

/**
 * Owns the lifetime of a tuning session.
 *
 * A session moves the operator's grill into Monitor. The contract this hook
 * exists to keep is that one is NEVER left open: it closes on unmount, and it
 * closes even when the unmount races an open that is still in flight -- that
 * case would otherwise land the open on a page that no longer exists, leaving
 * the grill in Monitor with nothing on screen to say so.
 *
 * Mounting deliberately does NOT open. Navigating to /tuner to read the
 * instructions is not consent to switch the grill's mode; `start` is.
 */
export function useTunerSession(baseUrl = BASE_URL) {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  //  Refs, not state: the unmount cleanup below reads these AFTER the last
  //  render, so a state value would be the one captured when the effect was
  //  created rather than the current one.
  const opened = useRef(false);
  const mounted = useRef(true);

  const close = useCallback(() => {
    if (!opened.current) return;
    opened.current = false;
    void closeSession(baseUrl);
  }, [baseUrl]);

  const start = useCallback(() => {
    setStatus("opening");
    setError(null);
    openSession(baseUrl).then((result) => {
      if (result.ok) {
        opened.current = true;
        //  The unmount may already have happened while this was in flight. The
        //  session is open on the server regardless, so close it rather than
        //  returning early and orphaning it.
        if (!mounted.current) {
          close();
          return;
        }
        setStatus("open");
        return;
      }
      if (!mounted.current) return;
      setStatus(result.status === 409 ? "refused" : "failed");
      setError(tunerErrorText(result));
    });
  }, [baseUrl, close]);

  const stop = useCallback(() => {
    close();
    setStatus("idle");
    setError(null);
  }, [close]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      close();
    };
  }, [close]);

  return { status, error, start, stop };
}
```

- [ ] **Step 4: Run the tests**

```
cd web-react && bun run test src/helpers/tuner/useTunerSession.test.tsx
```

Expected: 7 passing.

- [ ] **Step 5: Negative control on the teardown**

Delete `close()` from the effect's cleanup. **Two** tests must fail:
`CLOSES THE SESSION ON UNMOUNT` and `closes on unmount even when the open is
still in flight`. Restore it. If only one fails, the in-flight case is not
actually covered — say so.

- [ ] **Step 6: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): a tuning session that cannot be left open

Closes on unmount, including when the unmount races an open still in
flight -- that case lands the open on a page that no longer exists and
would otherwise leave the grill in Monitor with nothing on screen to say
so. Mounting does not open: reading the instructions is not consent to
change the grill's mode.
EOF
```

---

### Task 8: `SegmentCard`

**Files:**
- Create: `web-react/src/components/tuner/SegmentCard.tsx`,
  `SegmentCard.test.tsx`
- Create: `web-react/src/components/tuner/tuner.css` (card rules only — the
  page-level rules land in Task 10, because `styleCoverage` fails on a rule
  with no consumer)

**Interfaces:**
- Consumes: `Segment`, `TrReading` (Task 6).
- Produces:
  `SegmentCard({ segment, reading, recorded, onRecord, onClear }: { segment: Segment; reading: TrReading | null; recorded: { temp: number; trohms: number } | null; onRecord: (temp: number, trohms: number) => void; onClear: () => void })`.
  Task 10 renders three.

Ported from `_macro_tuner.html:80-123` (`render_manual_tool_card`): a live
resistance readout, a temperature input, and a Pause button that freezes the
reading so the operator can type the thermometer value against it.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/components/tuner/SegmentCard.test.tsx`:

```tsx
import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SegmentCard } from "./SegmentCard";

const READING = { probe: "Grill", trohms: 51234, tuning: true };

describe("SegmentCard", () => {
  it("names its segment", () => {
    render(<SegmentCard segment="High" reading={READING} recorded={null} onRecord={rs.fn()} onClear={rs.fn()} />);
    expect(screen.getByRole("heading", { name: "High" })).toBeInTheDocument();
  });

  it("shows the live resistance", () => {
    render(<SegmentCard segment="High" reading={READING} recorded={null} onRecord={rs.fn()} onClear={rs.fn()} />);
    expect(screen.getByText("51234 Ω")).toBeVisible();
  });

  it("says the probe is not reporting rather than showing zero", () => {
    //  null is not 0. A shorted probe reads a real 0 ohms, and the operator
    //  needs to tell those apart before recording a point.
    render(
      <SegmentCard
        segment="High"
        reading={{ probe: "Grill", trohms: null, tuning: true }}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.getByText("Waiting for a reading…")).toBeVisible();
    expect(screen.queryByText("0 Ω")).toBeNull();
  });

  it("warns when the reading is stale because no session is open", () => {
    render(
      <SegmentCard
        segment="High"
        reading={{ probe: "Grill", trohms: 51234, tuning: false }}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/not updating/i);
  });

  it("records the typed temperature against the frozen resistance", async () => {
    const onRecord = rs.fn();
    render(<SegmentCard segment="High" reading={READING} recorded={null} onRecord={onRecord} onClear={rs.fn()} />);

    await userEvent.type(screen.getByRole("spinbutton", { name: /temperature/i }), "400");
    await userEvent.click(screen.getByRole("button", { name: "Record" }));

    expect(onRecord).toHaveBeenCalledWith(400, 51234);
  });

  it("cannot record without a temperature", async () => {
    render(<SegmentCard segment="High" reading={READING} recorded={null} onRecord={rs.fn()} onClear={rs.fn()} />);
    expect(screen.getByRole("button", { name: "Record" })).toBeDisabled();
  });

  it("cannot record while the probe is not reporting", async () => {
    render(
      <SegmentCard
        segment="High"
        reading={{ probe: "Grill", trohms: null, tuning: true }}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    await userEvent.type(screen.getByRole("spinbutton", { name: /temperature/i }), "400");
    expect(screen.getByRole("button", { name: "Record" })).toBeDisabled();
  });

  it("shows what was recorded and offers to clear it", async () => {
    const onClear = rs.fn();
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={{ temp: 400, trohms: 51234 }}
        onRecord={rs.fn()}
        onClear={onClear}
      />,
    );
    expect(screen.getByText(/400/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onClear).toHaveBeenCalled();
  });

  it("stops offering Record once a point is recorded", () => {
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={{ temp: 400, trohms: 51234 }}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Record" })).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

```
cd web-react && bun run test src/components/tuner/SegmentCard.test.tsx
```

Expected: FAIL — cannot resolve `./SegmentCard`.

- [ ] **Step 3: Write the component and its CSS**

Write `SegmentCard.tsx` to satisfy the tests above, and the card-level rules in
`tuner.css`. Required elements, each of which a test asserts:

- `<section className="pf-tuner-segment">` with an `<h3 className="pf-tuner-segment-title">` naming the segment
- a readout: `{trohms} Ω`, or the literal `Waiting for a reading…` when
  `reading?.trohms == null`
- a `role="status"` note reading `The grill is not updating this reading — start tuning first.` when `reading && !reading.tuning`
- a number input labelled `Temperature`, `<label>`-associated (so
  `getByRole("spinbutton", {name: /temperature/i})` finds it)
- a `Record` button, disabled unless the typed temperature parses AND
  `reading?.trohms != null`; on click calls `onRecord(temp, reading.trohms)`
- when `recorded` is set: the recorded pair is shown, `Record` is gone, and a
  `Clear` button calls `onClear`

`tuner.css` starts with `@reference "../../theme.css";` and uses the repo's
tokens (`--color-card`, `--color-card-border`, `--color-inset`, `--color-text`,
`--color-label`, `--color-accent`, `--color-warn`, `--radius-card`). Model the
buttons on `.pf-admin-btn` (`admin.css`) and reuse `.pf-field` / `.pf-input`
from `settings.css` for the temperature input, as `admin.css` and `logs.css`
already do.

**Only card-level rules in this task.** Page-level ones go in Task 10.

- [ ] **Step 4: Run the tests**

```
cd web-react && bun run test src/components/tuner/SegmentCard.test.tsx
```

Expected: 9 passing.

- [ ] **Step 5: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): one manual-tuning segment card

A null reading says "waiting", never 0 -- a shorted probe reads a real
zero ohms, and the operator has to tell those apart before recording a
point. A reading taken with no session open is marked as not updating
rather than shown as a live number.
EOF
```

---

### Task 9: `TunerChart` and `ProfileForm`

**Files:**
- Create: `web-react/src/components/tuner/TunerChart.tsx`, `TunerChart.test.tsx`
- Create: `web-react/src/components/tuner/ProfileForm.tsx`, `ProfileForm.test.tsx`
- Modify: `web-react/src/components/tuner/tuner.css`

**Interfaces:**
- Consumes: `Coefficients`, `ProfileInput`, `SavedProfile`, `saveProfile`,
  `tunerErrorText` (Task 6).
- Produces:
  `TunerChart({ chart, chartOk }: { chart: {x: number; y: number}[]; chartOk: boolean })`
  and
  `ProfileForm({ coefficients, probeLabel, onSaved }: { coefficients: {a: number; b: number; c: number}; probeLabel: string; onSaved: (saved: SavedProfile) => void })`.
  Task 10 renders both.

- [ ] **Step 1: Write the failing chart tests**

Create `web-react/src/components/tuner/TunerChart.test.tsx`:

```tsx
import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { TunerChart } from "./TunerChart";

const CHART = [
  { x: 0, y: 100000 },
  { x: 110, y: 20000 },
  { x: 220, y: 1000 },
];

describe("TunerChart", () => {
  it("draws one polyline point per sample", () => {
    //  SVG rather than uPlot: 20 points need no library, and every coordinate
    //  is readable from the DOM here -- a canvas chart is unassertable in
    //  jsdom without a stub that would make this test meaningless.
    const { container } = render(<TunerChart chart={CHART} chartOk />);
    const points = container.querySelector("polyline")?.getAttribute("points") ?? "";
    expect(points.trim().split(/\s+/)).toHaveLength(3);
  });

  it("puts the lowest temperature on the left and the highest on the right", () => {
    const { container } = render(<TunerChart chart={CHART} chartOk />);
    const xs = (container.querySelector("polyline")?.getAttribute("points") ?? "")
      .trim()
      .split(/\s+/)
      .map((p) => Number(p.split(",")[0]));
    expect(xs[0]).toBeLessThan(xs[xs.length - 1]);
  });

  it("puts the highest resistance at the top", () => {
    //  SVG y grows downward, so the largest ohms value must have the SMALLEST
    //  y. Getting this backwards renders a plausible-looking inverted curve.
    const { container } = render(<TunerChart chart={CHART} chartOk />);
    const ys = (container.querySelector("polyline")?.getAttribute("points") ?? "")
      .trim()
      .split(/\s+/)
      .map((p) => Number(p.split(",")[1]));
    expect(ys[0]).toBeLessThan(ys[ys.length - 1]);
  });

  it("says the curve could not be drawn rather than drawing nothing", () => {
    render(<TunerChart chart={[]} chartOk={false} />);
    expect(screen.getByRole("status")).toHaveTextContent(/could not be plotted/i);
  });

  it("labels itself for assistive technology", () => {
    render(<TunerChart chart={CHART} chartOk />);
    expect(screen.getByRole("img", { name: /resistance/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Write the failing form tests**

Create `web-react/src/components/tuner/ProfileForm.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as actualTunerApi from "../../helpers/tuner/tunerApi" with { rstest: "importActual" };

const saveProfileMock = rs.fn();
rs.mock("../../helpers/tuner/tunerApi", () => ({
  ...actualTunerApi,
  saveProfile: (...a: unknown[]) => saveProfileMock(...a),
}));

const { ProfileForm } = await import("./ProfileForm");

const COEFFICIENTS = { a: 0.0007343140544, b: 0.0002157437229, c: 0.0000000951568577 };

beforeEach(() => {
  saveProfileMock.mockReset();
  saveProfileMock.mockResolvedValue({
    ok: true,
    status: 200,
    message: "",
    data: { id: "abc", applied: null },
  });
});

describe("ProfileForm", () => {
  it("shows the computed coefficients read-only", () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    for (const key of ["A", "B", "C"]) {
      expect(screen.getByRole("textbox", { name: key })).toHaveAttribute("readonly");
    }
  });

  it("requires a name before either save", async () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    expect(screen.getByRole("button", { name: "Save & Apply" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save Only" })).toBeDisabled();
  });

  it("Save Only sends no probe label", async () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save Only" }));
    expect(saveProfileMock.mock.calls[0][0]).toEqual({ ...COEFFICIENTS, name: "My Probe", apply_to: null });
  });

  it("Save & Apply attaches it to the probe being tuned", async () => {
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={rs.fn()} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save & Apply" }));
    expect(saveProfileMock.mock.calls[0][0].apply_to).toBe("Grill");
  });

  it("reports the saved profile to its parent", async () => {
    const onSaved = rs.fn();
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={onSaved} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save Only" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ id: "abc", applied: null }));
  });

  it("renders a refusal in place and does not claim success", async () => {
    saveProfileMock.mockResolvedValue({
      ok: false,
      status: 404,
      message: "not_found",
      data: null,
    });
    const onSaved = rs.fn();
    render(<ProfileForm coefficients={COEFFICIENTS} probeLabel="Grill" onSaved={onSaved} />);
    await userEvent.type(screen.getByRole("textbox", { name: /name/i }), "My Probe");
    await userEvent.click(screen.getByRole("button", { name: "Save & Apply" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/no longer configured/i);
    expect(onSaved).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run both to verify failure**

```
cd web-react && bun run test src/components/tuner/TunerChart.test.tsx src/components/tuner/ProfileForm.test.tsx
```

Expected: FAIL — neither module resolves.

- [ ] **Step 4: Write both components and their CSS**

`TunerChart.tsx` — an inline `<svg role="img" aria-label="Resistance against
temperature">` with a `viewBox`, one `<polyline>` whose `points` are the samples
scaled into the box (x by temperature ascending, y INVERTED so the largest ohms
sits at the smallest y), plus axis end-labels. When `!chartOk`, render a
`role="status"` note reading `The curve could not be plotted from these
coefficients.` instead of the svg.

`ProfileForm.tsx` — a `Name` textbox, three readonly textboxes labelled `A`,
`B`, `C` carrying the coefficients, and the two submit buttons. Both are
disabled until the name is non-blank. `Save Only` sends `apply_to: null`;
`Save & Apply` sends `probeLabel`. On failure, render
`tunerErrorText(result)` in a `role="alert"` and do NOT call `onSaved`.

Add the rules these need to `tuner.css`.

- [ ] **Step 5: Run the tests**

```
cd web-react && bun run test src/components/tuner/
```

Expected: 20 passing across the three component files.

- [ ] **Step 6: Negative control on the chart inversion**

Remove the y inversion (plot `y` directly).
`puts the highest resistance at the top` must fail and nothing else. Restore it.

- [ ] **Step 7: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): the tuner curve and the profile save form

An inline SVG polyline rather than uPlot: twenty points need no library,
and every coordinate stays readable from the DOM, so the y inversion is
provable in a unit test instead of hidden inside a canvas.
EOF
```

---

### Task 10: `TunerPage`, the route and the link

**Files:**
- Create: `web-react/src/components/tuner/TunerPage.tsx`, `TunerPage.test.tsx`
- Modify: `web-react/src/components/tuner/tuner.css` (page-level rules)
- Modify: `web-react/src/components/App.tsx`
- Modify: `web-react/src/components/settings/tabs/ProbesTab.tsx` and its test

**Interfaces:**
- Consumes: everything above.
- Produces: the `/tuner` route. Task 11's e2e navigates to it.

**Behaviour the page owns:**

1. Probe selection from `probe_settings.probe_map.probe_info[]` — read through
   the existing settings loader/API the ProbesTab already uses; do NOT add a
   second fetch for the probe list if one is already reachable.
2. `useTunerSession`. Start is an explicit button; the page never opens on mount.
3. While `status === "open"`, poll `fetchTr(selectedProbe)` every **1000 ms**
   (Flask's `tunerUpdateTr` interval). Stop polling the moment status leaves
   `open`. Arm the next poll only when nothing is in flight, so a slow response
   cannot queue polls behind itself — the pattern `HistoryPage.tsx` uses.
4. Three `SegmentCard`s. When all three are recorded, enable `Finish`.
5. `Finish` → `computeCoefficients` → close the session → show `TunerChart` and
   `ProfileForm`.
6. A `refused` status renders `tunerErrorText` in a `role="alert"` and offers no
   Start until the mode changes.

- [ ] **Step 1: Write the failing tests**

`TunerPage.test.tsx` mocks `../../helpers/tuner/tunerApi` wholesale (the
`AdminPage.test.tsx` idiom) and covers, at minimum:

```
- renders the three segments in High, Medium, Low order
- does not open a session on mount
- Start opens the session and begins polling
- polls exactly once per second while open, and stops when the session closes
- a 409 refusal shows the mode and offers no Start
- Finish is disabled until all three segments are recorded
- Finish computes, closes the session, and shows the chart and the form
- an uncomputable result shows the 422 copy and does NOT show the save form
- leaving the page closes the session
```

Write each as a real assertion with the fixtures inline — no placeholders. Use
`rs.useFakeTimers()` installed **before** `render` (installing it afterwards
leaves the interval on the real clock; this cost the events slice four failing
tests), bound as `const installFakeClock = rs.useFakeTimers.bind(rs)` if Biome's
`useHookAtTopLevel` objects to the bare call.

- [ ] **Step 2: Run to verify failure, then write the page**

Add the page-level rules to `tuner.css` in the same edit as the markup that
wears them.

- [ ] **Step 3: Add the route**

In `App.tsx`, beside the other pages:

```tsx
import { TunerPage } from "./tuner/TunerPage";
```

```tsx
// The probe tuner. Reached from Settings > Probes, matching Flask, whose
// navbar has no Tuner entry either. No loader: the page must not read or
// write anything before the operator has asked for a session.
{ path: "/tuner", element: <TunerPage /> },
```

- [ ] **Step 4: Link it from the Probes tab**

Add a `<Link to="/tuner">Tune a probe</Link>` to `ProbesTab.tsx`, styled with a
class that has a rule (follow whatever the tab already uses for its actions;
`cssCoverage` fails on a class with none). Add a test asserting the link's
`href`. **No navbar entry** — Flask's `base.html` has no Tuner link, and the
backlog's App-shell entry records the three routes deliberately kept out of the
navbar; add `/tuner` to that list in Task 11.

- [ ] **Step 5: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): the /tuner page, manual flow

Reached from Settings > Probes, matching Flask, whose navbar has no
Tuner entry. No loader and nothing on mount: the page reads and writes
only once the operator has asked for a session.
EOF
```

---

### Task 11: End to end, baselines and closeout

**Files:**
- Create: `web-react/tests/e2e/tuner.spec.ts`
- Modify: `web-react/tests/e2e/apiFixtures.ts`, `pageSpecs.ts`, baselines
- Modify: `docs/superpowers/backlogs/react-migration-backlog.md`

- [ ] **Step 1: Start a backend**

`gunicorn` ONLY — never `control.py`.

```bash
cd /home/dannyb/sources/PiFire && \
  .venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 --reload app:app > "$SCRATCH/gunicorn.log" 2>&1 &
```

- [ ] **Step 2: Write the live spec**

`tuner.spec.ts` must:

- abort AND RECORD the destructive admin routes, with an `afterEach` asserting
  nothing was attempted — the same guard `metrics.spec.ts` and `events.spec.ts`
  carry;
- **additionally**, in `afterEach`, read `GET /api/get/control` through
  `request` and assert `mode === "Stop"` and no `tuning_mode`. This is the
  slice's own hazard and no other spec covers it. A test that opens a session
  and fails mid-way must be caught here, not by the next developer.
- cover: the page renders; Start opens a session and the readout appears;
  navigating away closes it (assert via the control read); `GET /api/tuner/tr`
  answers the envelope; and the coefficients endpoint 422s on a degenerate
  triple.
- **Never** click Save & Apply against the live backend — it writes a probe
  profile into the operator's settings. Assert the button exists and is
  enabled; do not press it.

- [ ] **Step 3: Run it**

```
cd web-react && bun run typecheck:e2e && bunx playwright test tuner.spec.ts --project=app
```

Do **not** run `bun run test:e2e`. Afterwards, confirm by hand that the grill is
in Stop.

- [ ] **Step 4: Add `stubTuner` and the page spec**

`stubTuner` fulfils `**/api/tuner/tr*` and `**/api/tuner/session` with pinned
content so a baseline never touches the live grill, and so the page renders on
a machine with no probes reporting. Follow `stubMetrics`' shape.

The `pageSpecs.ts` entry uses `ready: ".pf-tuner-segment"` and lists every
`pf-tuner-*` landmark. Capture at the point where the three cards are on
screen — before Start, so the baseline never depends on a session.

- [ ] **Step 5: Capture baselines and audit them**

```
cd web-react && bun run baseline:capture
```

Snapshot the baselines directory BEFORE capturing, then diff every changed file
property-by-property and account for each change. Adding a link to
Settings > Probes is expected to move that page; **nothing else should move at
all**. If an unrelated page changed, stop and find out why — a re-capture that
quietly absorbs a regression is worse than no baseline.

- [ ] **Step 6: Run the fidelity gate**

```
cd web-react && bun run test:e2e:fidelity
```

Expected: green, four more tests than before (two baselines + two overflow
checks). Note: `pellets 390x844` has a known intermittent failure unrelated to
this work — if it fails, re-run and confirm the pellets baselines are unchanged
before treating it as real.

- [ ] **Step 7: Close out the backlog**

In `docs/superpowers/backlogs/react-migration-backlog.md`:

- Change `- [ ] **tuner** — probe tuning tool` to reflect that the **manual**
  flow shipped and the auto flow is slice 2. Do NOT mark it `[x]`.
- Add `/tuner` to the App-shell entry's list of routes deliberately kept out of
  the navbar.
- Add a SHIPPED entry covering: the session/reading split and why; the 409
  guard; the closed template-injection door; the two silent failures that now
  have signals; and the `null`-vs-`0` reading distinction.
- Add `#### Deferred by the tuner manual slice — 2026-07-28` recording:
  - **The auto flow is not ported** — `read_auto_status`, the autotune store,
    reference-probe selection and the readiness threshold are slice 2.
  - **`blueprints/tuner/` is still live**, still renders its Jinja page, and
    still owns `tuner.py`'s maths. Retirement waits for the general pass.
  - **`calc_shh_coefficients` and `temp_to_tr` still swallow every exception
    into a bare `except:`.** The new endpoint interprets their output rather
    than changing them, because `test_page_tuner.py` pins the current return
    shape and other callers exist.
  - **`_settings_addprofile` still reports success for a profile it never
    applied** when `apply_profile` matches no probe. The new endpoint 404s;
    the legacy handler is untouched.
  - **`temp_to_tr` remains the documented-unreliable inverse.** `chart_ok`
    reports when it fails; nothing yet makes it fail less often.

- [ ] **Step 8: Full gate**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
cd web-react && bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run test:e2e:fidelity
```

Report every count against the pre-slice baseline: pytest **3400**, rstest
**1521** across 160 files, fidelity **129**.

- [ ] **Step 9: Commit**

```bash
cd /home/dannyb/sources/PiFire && jj new
jj describe --stdin <<'EOF'
test(web-react): live tuner e2e, fidelity baselines and closeout

The spec asserts in afterEach that the grill is back in Stop with no
tuning_mode. That is this slice's own hazard and no other spec covers
it: a test that opens a session and fails mid-way leaves the operator's
grill in Monitor, and the next person to notice would be the operator.
EOF
```

---

## Self-Review

**Spec coverage.** The design decisions live in *Verified Facts* and the
*Constraints specific to THIS slice*; that is where a reviewer should look for
rulings. Every element of Flask's manual flow has a task: probe selection and
the instruction card (10), the three segment cards with pause-and-record (8),
Finish → coefficients (4, 10), the chart (9), the profile save with both actions
(5, 9), and the session mode transitions (2, 7). The SSTI fix the user asked for
is Task 1.

**Placeholders.** Tasks 1–9 carry their code. **Tasks 10 and 11 deliberately do
not** — they specify behaviour and required assertions rather than full
listings, because `TunerPage` composes six modules whose exact JSX depends on
choices made in Tasks 6–9, and writing it blind here would produce code the
implementer has to rewrite. This is a stated exception, not an oversight: if the
implementer wants literal code for those two, say so and it will be written
after Task 9 lands.

**Type consistency.** `Segment`, `TunerPoint`, `TrReading`, `Coefficients`,
`ProfileInput`, `SavedProfile` and `TunerResult<T>` are defined once in Task 6
and imported by 7, 8, 9 and 10. `openSession`/`closeSession`/`fetchTr`/
`computeCoefficients`/`saveProfile`/`tunerErrorText` keep the same signatures in
Task 6's implementation, its tests, and every consumer. Server-side,
`error`/`json_body`/`set_control`/`require_tunable` are defined in Task 2 and
used by 3, 4 and 5; `SEGMENTS` is defined in Task 4 and matches the TS
`Segment` union exactly.

**Facts verified against live code on 2026-07-28**, not assumed: the six macro
names the client sends and the seventh it never asks for; that
`_settings_controller_card`'s superficially-similar `render_template_string` is
NOT the same bug; `Mode.STOP`/`Mode.MONITOR` string values; that `read_tr()`
returns the `control:tuning` blob keyed by probe label; that
`calc_shh_chart` produces 20 labels and abandons the series on one failure; and
that `_settings_addprofile` reads `request.form` off the global, which is why
Task 5 does not call it. Treat any disagreement between this plan and live code
as the plan being wrong.

**The riskiest thing in this slice is not the code.** It is that a test, an e2e
run, or an abandoned browser tab leaves the grill in Monitor with `tuning_mode`
set. Three independent nets cover it: the `autouse` fixture in
`test_api_tuner.py`, the unmount tests in Task 7 (including the in-flight race),
and the `afterEach` control read in Task 11. Each has a negative control.
