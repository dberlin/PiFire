# React Tuner — Auto Flow Implementation Plan (Slice 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Flask `/tuner`'s **auto** flow to the React `/tuner` page: accumulate
temperature/resistance samples against a reference probe until the spread is wide
enough, then solve for a profile through the same coefficients/save path the
manual flow already uses.

**Architecture:** One new backend endpoint (`POST /api/tuner/auto-status`) that
records a sample and returns the derived high/medium/low selection, plus a flush
of the autotune store when a session opens. The React page gains a Manual/Auto
mode toggle; auto mode reuses the slice-1 session hook, coefficients endpoint,
chart and save form, and adds a reference-probe selector and a live accumulation
readout. The Steinhart-Hart maths, the session lifetime, and the profile save are
untouched — this slice is the second data-gathering path into the same solve.

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
  `bun run format` fixes Biome formatting; `bunx biome check --write <file>` also
  sorts imports (plain `format` does not — this bit slice 1 twice).
- **Every `pf-*` class used in a `.tsx` must have a rule in a `.css`, and every
  rule must have a consumer.** `src/cssCoverage.test.ts` and
  `src/styleCoverage.test.ts` enforce both directions.
- **Do NOT run `bun run test:e2e`** (the whole `app` project). Use
  `bun run test:e2e:fidelity`, or one named spec with `--project=app`.
- **Never hand-edit `web-react/tests/e2e/baselines/*.json`** — captured by
  `bun run baseline:capture`.

### Constraints specific to THIS slice (carried from slice 1, still binding)

- **This page moves the operator's live grill.** `control.py` runs on this
  machine, so opening a session actually puts the grill into Monitor. Every task
  that can reach `/api/tuner/session` must leave the grill in **Stop** when it
  finishes; the e2e's `afterEach` force-closes and polls control to prove it.
- **The e2e backend MUST run with a threaded worker:**
  `gunicorn -k gthread --threads 25 -w 1 -b 127.0.0.1:5000 --reload app:app`.
  A sync `-w 1` worker is pinned by the app's Socket.IO connection the moment a
  page loads, and every later request queues until it times out — this cost
  slice 1 an hour. Do NOT start `control.py` (it is already running).
- **In the e2e, hit Flask directly at `127.0.0.1:5000` (`ports.pifireUrl` with
  `localhost`→`127.0.0.1`), not the dev proxy**, and navigate away with a
  client-side navbar click (not `page.goto`, which cancels the unmount's
  closeSession fetch). Both learnings are baked into `tests/e2e/tuner.spec.ts`.
- **Neutralize `os.system`/`subprocess`/`sudo`/`reboot`/`shutdown` before any
  test that can reach admin/installer/updater/wizard paths.** `blueprints/tuner`
  and `blueprints/api_tuner` contain none (verified 2026-07-28); this slice adds
  none. Re-grep if that changes.
- **The auto-status endpoint writes to the autotune QUEUE, never to control.**
  The only control writes on this whole surface remain the two session calls.
  Keep it that way: sample accumulation is tuning data, not grill state.
- **Exact strings.** The mode toggle options are `Manual` and `Auto`. The three
  derived rows stay `High`, `Medium`, `Low`. Profile save actions remain
  `Save & Apply` and `Save Only`.

---

## Verified Facts

Read from live code and a running backend on 2026-07-28. Do not re-derive; flag
anything that no longer matches.

### What Flask's auto flow does

`blueprints/tuner/routes.py`'s `read_auto_status` branch, on each poll:

1. On the FIRST poll only (`not control["tuning_mode"]`): enables tuning mode,
   `flush_autotune()`, sets `first_run`.
2. If Stopped: moves to Monitor.
3. Reads `current_tr = read_tr()[probe_selected]` (or -1 if absent).
4. Reads `current_temp` by looking `probe_reference` up in
   `read_current()["P" | "F" | "AUX"]` (or -1 if absent).
5. Records a sample `{"ref_T": current_temp, "probe_Tr": current_tr}` **only
   when** `(autotune_length() > 4 or current_temp > 0) and current_tr >= 0 and
   current_temp >= 0 and not first_run` — the guard that skips a DS18B20's early
   zero readings.
6. If `len(read_autotune()) > 10`: `calc_auto_tune_status(data, units,
   status_data)` fills in high/medium/low temp+Tr and `ready`.
7. Returns `status_data` (the eight numbers plus `ready`).

The client polls this ~1 s; when `ready` is true the user finishes, and the
high/medium/low temps and Trs become the three points fed to the SAME
coefficient solve the manual flow uses.

### The split this slice preserves

Slice 1 separated the SESSION (writes control, opens/closes) from the READING
(inert). Auto-status does not fit "inert" — each poll RECORDS a sample — but it
records to the **autotune queue**, not control. So the rule holds in the form
that matters: **the only control writes are the two session calls.** Steps 1–2
of Flask's list (enable tuning, go to Monitor) are already done by
`POST /api/tuner/session {open:true}` and are NOT repeated here.

Because the session is what "starts fresh", **the autotune flush moves to
session-open** (Task 1). Flask flushed on the first auto-status poll because that
was its "enable tuning" moment; ours is session-open. Manual tuning never reads
the autotune queue, so flushing it on every open is a harmless no-op there.

### The stores and their accessors (all verified)

- `read_tr()` → `control:tuning` blob, `{label: ohms}`. (Slice 1.)
- `read_current()` → `control:current` blob. Live shape confirmed:
  `{"P": {"Grill": <temp>}, "F": {"Probe1": <temp>, "Probe2": …, "Probe3": …},
  "AUX": {}, "NT": {…}, "PSP": <number>}`. The reference lookup checks `P`, then
  `F`, then `AUX`, by label — exactly Flask's order.
- `write_autotune(data)` → `SqliteQueue("queue_autotune").push(data)` — a DIRECT
  write, no control delta, no drain needed in tests.
- `read_autotune()` → `SqliteQueue("queue_autotune").list()`.
- `autotune_length()` → count without materializing.
- `flush_autotune()` → clears the queue.
- `calc_auto_tune_status(data, units, status_data)` (`blueprints/tuner/tuner.py`)
  MUTATES `status_data` in place, setting `high_temp`/`high_tr`/`medium_temp`/
  `medium_tr`/`low_temp`/`low_tr`/`ready`. Caller guards `if len(data) > 10`.
  Ready is set when the high−low temp spread ≥ 50 °F (25 °C). UNCHANGED here.
  The legacy Flask `read_auto_status` route (which this slice does NOT touch) is
  pinned by `tests/web/test_page_tuner.py:146`
  (`test_command_read_auto_status_first_run`); the new `/api/tuner/auto-status`
  is a separate endpoint, so that test stays green regardless.

### Reference probes on the live map

`probe_info` labels/types confirmed: `Grill` (Primary,
profile `PT-1000-Ideal`), `Probe1`/`Probe2`/`Probe3` (Food,
`Thermoworks-Pro-Series-HeaterMeter`). Any probe may be a reference; the natural
default is a probe OTHER than the one being tuned, with a known-good profile.
This slice offers all probes as reference and defaults it to the first probe
that is not the tune target.

### What slice 1 already built and this slice reuses UNCHANGED

- `POST /api/tuner/session {open}` — opened/closed by `useTunerSession`.
- `POST /api/tuner/coefficients {points}` — auto's Finish sends the derived
  high/medium/low here, exactly as manual sends its three recorded points.
- `POST /api/tuner/profile` and `ProfileForm`.
- `TunerChart` (SVG polyline).
- `helpers/tuner/tunerTypes.ts`, `tunerApi.ts` (extended, not rewritten),
  `useTunerSession.ts`.
- `TunerPage.tsx` (extended with a mode toggle; the manual path is untouched).

### React conventions (unchanged from slice 1)

- `BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || ""` — same-origin.
- Typed client resolves to a result, never throws; `tunerErrorText` translates
  the machine token.
- Unit tests stub `fetch` with a module-level `rs.fn()` + `rs.stubGlobal`; page
  tests mock the API module through a lazy wrapper (`AdminPage.test.tsx` idiom).
- Fake timers installed BEFORE `render`, bound as `installFakeClock` if Biome's
  `useHookAtTopLevel` objects.
- `aria-labelledby` ids must NOT start with `pf-` (cssCoverage scans for `pf-*`).

---

## File Structure

**Create**

| Path | Responsibility |
|---|---|
| `tests/web/test_api_tuner_auto.py` | The auto-status endpoint and the session flush |
| `web-react/src/components/tuner/AutoTuneCard.tsx` | Reference selector + live readout + progress |
| `web-react/src/components/tuner/AutoTuneCard.test.tsx` | |

**Modify**

| Path | Change |
|---|---|
| `blueprints/api_tuner/routes.py` | Session-open flush (Task 1); auto-status route (Task 2) |
| `web-react/src/helpers/tuner/tunerTypes.ts` | `AutoStatus` (Task 3) |
| `web-react/src/helpers/tuner/tunerApi.ts` | `fetchAutoStatus` (Task 3) |
| `web-react/src/helpers/tuner/tunerApi.test.ts` | `fetchAutoStatus` tests (Task 3) |
| `web-react/src/components/tuner/TunerPage.tsx` | Manual/Auto toggle + auto wiring (Task 5) |
| `web-react/src/components/tuner/TunerPage.test.tsx` | Auto-mode tests (Task 5) |
| `web-react/src/components/tuner/tuner.css` | Toggle + auto-card rules (Tasks 4, 5) |
| `web-react/tests/e2e/tuner.spec.ts` | Auto-flow e2e (Task 6) |
| `web-react/tests/e2e/apiFixtures.ts` | `stubTuner` covers auto-status (Task 6) |
| `web-react/tests/e2e/pageSpecs.ts` | tuner spec: capture the auto screen too, or note why not (Task 6) |
| `docs/superpowers/react-migration-backlog.md` | Closeout (Task 6) |

**Deliberately not created:** no new session, coefficients, profile, chart, or
save module. If a task finds itself rewriting one of those, stop — the slice is
additive over slice 1's surface.

## Parallelization

- **Tasks 1–2 (backend)** are serial (2 builds on the endpoint 1 touches).
- **Tasks 3–5 (frontend)** are serial (each imports the previous).
- **Backend (1–2) and frontend (3–5) can run concurrently in separate jj
  workspaces**: Task 3 types against the contract stated in Task 2. Task 6 is the
  join point. Concurrency needs `jj workspace add` + `.lsp.json` copy + `bun
  install` (both gitignored, so `workspace add` skips them).

---

## Task 1: Session-open flushes the autotune store

**Files:**
- Modify: `blueprints/api_tuner/routes.py`
- Test: `tests/web/test_api_tuner_auto.py` (new)

**Interfaces:**
- Consumes: slice 1's `tuner_session`.
- Produces: the guarantee that `read_autotune()` is empty after an open. Task 2
  relies on it (every auto session starts from zero samples).

- [ ] **Step 1: Confirm no shell-out in the blueprint**

```
grep -nE "os\.system|subprocess|sudo|reboot|shutdown|popen" blueprints/api_tuner/ blueprints/tuner/
```

Expected: no matches. If any appear, STOP — the mock-first rule applies first.

- [ ] **Step 2: Write the failing test**

Create `tests/web/test_api_tuner_auto.py`. Reuse the slice-1 harness pattern
(the autouse `grill_left_stopped` fixture and `control_now()` draining), copied
here so this file stands alone:

```python
"""The auto-tuning half of the /api/tuner surface.

SAFETY: opening a session moves the live grill to Monitor. Every test that
opens one closes it, and the autouse fixture asserts the grill is back in Stop.
See docs/superpowers/plans/2026-07-28-react-tuner-auto.md.
"""

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def control_now():
    from common.datastore_accessors import execute_control_writes, read_control

    execute_control_writes()
    return read_control()


@pytest.fixture(autouse=True)
def grill_left_stopped(ds):
    yield
    control = control_now()
    assert control["mode"] == "Stop", "a test left the grill out of Stop"
    assert not control.get("tuning_mode"), "a test left tuning_mode set"


def set_mode(mode):
    from common.common import WriteKind
    from common.control_delta import control_delta
    from common.datastore_accessors import execute_control_writes, write_control

    write_control(control_delta(set_values={"mode": mode}), WriteKind.DELTA, origin="test")
    execute_control_writes()


def test_opening_a_session_flushes_the_autotune_store(ds, client):
    """A fresh session must not inherit samples from a previous one. Flask
    flushed on the first auto-status poll; the session is where "start fresh"
    lives now."""
    from common.datastore_accessors import read_autotune, write_autotune

    write_autotune({"ref_T": 100, "probe_Tr": 40000})
    assert len(read_autotune()) == 1

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    try:
        assert read_autotune() == []
    finally:
        client.post("/api/tuner/session", json={"open": False})
        control_now()


def test_closing_a_session_does_not_touch_the_autotune_store(ds, client):
    """Close restores grill state; it must not also discard the samples a just-
    finished auto tune may still want to read back."""
    from common.datastore_accessors import read_autotune, write_autotune

    set_mode("Stop")
    client.post("/api/tuner/session", json={"open": True})
    control_now()
    write_autotune({"ref_T": 100, "probe_Tr": 40000})

    client.post("/api/tuner/session", json={"open": False})
    control_now()
    assert len(read_autotune()) == 1
    #  Clean up so the next test's flush-on-open assertion starts empty.
    from common.datastore_accessors import flush_autotune

    flush_autotune()
```

- [ ] **Step 3: Run to verify failure**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner_auto.py -q
```

Expected: `test_opening_a_session_flushes_the_autotune_store` FAILS (open does
not flush yet); the close test passes.

- [ ] **Step 4: Flush on open**

In `blueprints/api_tuner/routes.py`, add `flush_autotune` to the
`datastore_accessors` import, and in `tuner_session`'s open branch, after the
control write:

```python
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
        #  Start every tuning session from an empty autotune store. Flask
        #  flushed on the first auto-status poll -- the moment it enabled tuning
        #  mode -- which is this call now. Manual tuning never reads this queue,
        #  so the flush is a no-op there.
        flush_autotune()
        return jsonify(
            api_response("OK", None, {"open": True, "mode": Mode.MONITOR, "restored": moved})
        ), 200
```

- [ ] **Step 5: Run the tests**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner_auto.py tests/web/test_api_tuner.py -q
```

Expected: all PASS. Running the slice-1 module too confirms the open-path change
did not break the session tests.

- [ ] **Step 6: Negative control**

Remove the `flush_autotune()` line.
`test_opening_a_session_flushes_the_autotune_store` must fail. Restore it.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_tuner/routes.py tests/web/test_api_tuner_auto.py
jj new
jj describe --stdin <<'EOF'
feat(api_tuner): flush the autotune store when a session opens

Every auto tune must start from zero samples. Flask flushed on the first
read_auto_status poll -- the moment it enabled tuning mode -- which the
explicit session open now owns. Manual tuning never reads this queue.
EOF
```

---

## Task 2: `POST /api/tuner/auto-status`

**Files:**
- Modify: `blueprints/api_tuner/routes.py`, `tests/web/test_api_tuner_auto.py`

**Interfaces:**
- Consumes: Task 1's `error`/`json_body`, slice 1's session.
- Produces: `POST /api/tuner/auto-status {"probe": str, "reference": str}` →
  `200 {"data": {"current_tr": number|null, "current_temp": number|null,
  "high_tr","high_temp","medium_tr","medium_temp","low_tr","low_temp": number,
  "samples": number, "ready": bool}}`. Task 3 types it; Task 5 polls it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_api_tuner_auto.py`:

```python
def seed_tr(values):
    from common.datastore_accessors import write_tr

    write_tr(values)


def seed_current(current):
    """Write the control:current blob read_current() reads.

    Shape confirmed live: {"P": {label: temp}, "F": {...}, "AUX": {...}}.
    write_current is the public writer (common/datastore_accessors.py)."""
    from common.datastore_accessors import write_current

    write_current(current)


def test_auto_status_requires_both_probes(ds, client):
    for body in ({"reference": "Grill"}, {"probe": "Grill"}, {}):
        resp = client.post("/api/tuner/auto-status", json=body)
        assert resp.status_code == 400
        assert resp.get_json()["data"]["field"] in ("probe", "reference")


def test_auto_status_records_a_sample_and_reports_it(ds, client):
    from common.datastore_accessors import flush_autotune, read_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current({"P": {"Ref": 225}, "F": {}, "AUX": {}})

    body = client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"}).get_json()
    assert body["result"] == "OK"
    data = body["data"]
    assert data["current_tr"] == 41000
    assert data["current_temp"] == 225
    #  The sample landed in the queue with Flask's key names, so
    #  calc_auto_tune_status (unchanged) can consume it.
    (sample,) = read_autotune()
    assert sample == {"ref_T": 225, "probe_Tr": 41000}
    assert data["samples"] == 1
    assert data["ready"] is False


def test_auto_status_reports_null_for_a_probe_that_is_not_reporting(ds, client):
    """A probe absent from the tuning blob (Tr) or the current blob (temp) is
    null, not Flask's -1 sentinel, and no sample is recorded from it."""
    from common.datastore_accessors import flush_autotune, read_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current({"P": {}, "F": {}, "AUX": {}})

    data = client.post(
        "/api/tuner/auto-status", json={"probe": "Grill", "reference": "Missing"}
    ).get_json()["data"]
    assert data["current_tr"] == 41000
    assert data["current_temp"] is None
    assert read_autotune() == [], "a sample was recorded from a missing reference"


def test_auto_status_finds_the_reference_in_F_and_AUX_too(ds, client):
    from common.datastore_accessors import flush_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current({"P": {}, "F": {"Food1": 160}, "AUX": {}})
    data = client.post(
        "/api/tuner/auto-status", json={"probe": "Grill", "reference": "Food1"}
    ).get_json()["data"]
    assert data["current_temp"] == 160


def test_auto_status_becomes_ready_once_the_spread_is_wide_enough(ds, client):
    """More than ten samples spanning >= 50 F flips ready and fills the three
    derived points. Seeded directly rather than driven a poll at a time."""
    from common.datastore_accessors import flush_autotune, write_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current({"P": {"Ref": 240}, "F": {}, "AUX": {}})
    #  Twelve samples from 100 F to 240 F: a 140 F spread, well over the 50 F
    #  minimum. Distinct temps so calc_auto_tune_status picks real high/low.
    for i in range(12):
        write_autotune({"ref_T": 100 + i * 13, "probe_Tr": 40000 - i * 3000})

    data = client.post(
        "/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"}
    ).get_json()["data"]
    assert data["ready"] is True
    assert data["high_temp"] > data["low_temp"]
    assert data["high_temp"] - data["low_temp"] >= 50


def test_auto_status_writes_no_control(ds, client):
    """Sample accumulation is tuning DATA, not grill state. The only control
    writes on this surface are the two session calls."""
    from common.datastore_accessors import flush_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current({"P": {"Ref": 225}, "F": {}, "AUX": {}})
    before = control_now()
    client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"})
    after = control_now()
    assert after["mode"] == before["mode"]
    assert after.get("tuning_mode") == before.get("tuning_mode")


def test_auto_status_skips_an_early_zero_reading(ds, client):
    """The DS18B20 slow-start guard: with few samples and a zero temp, the poll
    reports but records nothing, so a cold probe's 0 does not poison the solve."""
    from common.datastore_accessors import flush_autotune, read_autotune

    flush_autotune()
    seed_tr({"Grill": 41000})
    seed_current({"P": {"Ref": 0}, "F": {}, "AUX": {}})
    client.post("/api/tuner/auto-status", json={"probe": "Grill", "reference": "Ref"})
    assert read_autotune() == [], "an early zero reading was recorded"
```

If `write_current` is not the public writer name, find the one paired with
`read_current()` (`control:current`) and use it; do not reach for
`_write_json_blob`.

- [ ] **Step 2: Run to verify failure**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner_auto.py -q -k auto_status
```

Expected: FAIL — no such route.

- [ ] **Step 3: Write the route**

Append to `blueprints/api_tuner/routes.py`, adding `read_current`,
`write_autotune`, `read_autotune`, `autotune_length` to the
`datastore_accessors` import and `calc_auto_tune_status` to the `tuner.tuner`
import:

```python
def _reference_temp(current, reference):
    """The reference probe's temperature, or None if it is not reporting.

    read_current() groups probes as P / F / AUX by label, and Flask checks them
    in that order. None (not -1): a probe absent from every group is not
    reporting, which the client renders as "waiting" -- distinct from a real
    reading that happens to be zero.
    """
    for group in ("P", "F", "AUX"):
        values = current.get(group, {})
        if reference in values:
            return values[reference]
    return None


@api_tuner_bp.route("/auto-status", methods=["POST"])
def tuner_auto_status():
    """Record one auto-tuning sample and report the derived selection.

    Unlike /tr this is a POST: each poll captures a datapoint. But it writes
    only the autotune QUEUE -- never control -- so the mode-change safety stays
    entirely in the session endpoint. The session's flush-on-open is what makes
    each run start from zero.

    A sample is recorded only when both readings are present and past the
    DS18B20 warm-up guard (Flask's `autotune_length() > 4 or current_temp > 0`),
    so a cold probe's leading zeros do not poison the solve. Once more than ten
    samples span a wide enough temperature range, calc_auto_tune_status
    (unchanged) fills in the high/medium/low points and flips `ready`.
    """
    body = json_body()
    probe = body.get("probe")
    reference = body.get("reference")
    if not isinstance(probe, str) or not probe:
        return error("bad_request", 400, field="probe")
    if not isinstance(reference, str) or not reference:
        return error("bad_request", 400, field="reference")

    current_tr = read_tr().get(probe)
    current_temp = _reference_temp(read_current(), reference)

    #  Record only a complete, warmed-up reading. `current_temp > 0` lets an
    #  early sample through once the probe is live; `autotune_length() > 4`
    #  lets later samples through even at exactly zero, matching Flask.
    if (
        current_tr is not None
        and current_temp is not None
        and current_tr >= 0
        and current_temp >= 0
        and (autotune_length() > 4 or current_temp > 0)
    ):
        write_autotune({"ref_T": current_temp, "probe_Tr": current_tr})

    status = {
        "current_tr": current_tr,
        "current_temp": current_temp,
        "high_tr": 0,
        "high_temp": 0,
        "medium_tr": 0,
        "medium_temp": 0,
        "low_tr": 0,
        "low_temp": 0,
        "ready": False,
    }
    samples = read_autotune()
    if len(samples) > 10:
        settings = read_settings()
        calc_auto_tune_status(samples, settings["globals"]["units"], status)

    status["samples"] = len(samples)
    return jsonify(api_response("OK", None, status)), 200
```

- [ ] **Step 4: Run the module**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_tuner_auto.py -q
```

Expected: all PASS. If `test_auto_status_becomes_ready…` fails because the
seeded triple is degenerate for `calc_auto_tune_status`, widen the spread or
vary the Tr values — do NOT relax the `ready` assertion.

- [ ] **Step 5: Negative control on the warm-up guard**

Delete the `(autotune_length() > 4 or current_temp > 0)` clause.
`test_auto_status_skips_an_early_zero_reading` must fail. Restore it.

- [ ] **Step 6: Full backend suite**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
```

Expected: green. Report the total against the pre-slice baseline of **3438**.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format blueprints/api_tuner/routes.py tests/web/test_api_tuner_auto.py
jj new
jj describe --stdin <<'EOF'
feat(api_tuner): auto-tune status that accumulates samples

POST /api/tuner/auto-status records one temperature/resistance sample
against a reference probe and reports the derived high/medium/low
selection. It writes only the autotune queue, never control -- the
session endpoint remains the sole writer of grill state. A missing probe
reads null (not Flask's -1), and the DS18B20 warm-up guard is preserved.
EOF
```

---

## Task 3: Typed client for auto-status

**Files:**
- Modify: `web-react/src/helpers/tuner/tunerTypes.ts`, `tunerApi.ts`,
  `tunerApi.test.ts`

**Interfaces:**
- Consumes: Task 2's endpoint.
- Produces: `interface AutoStatus`; `fetchAutoStatus(probe, reference, baseUrl?)
  : Promise<TunerResult<AutoStatus>>`. Tasks 4–5 import both.

- [ ] **Step 1: Confirm the shape against the live endpoint**

With the gthread backend running (never `control.py`), and restoring Stop after:

```
curl -s -XPOST 127.0.0.1:5000/api/tuner/session -H 'Content-Type: application/json' -d '{"open":true}'
curl -s -XPOST 127.0.0.1:5000/api/tuner/auto-status -H 'Content-Type: application/json' -d '{"probe":"Grill","reference":"Grill"}'
curl -s -XPOST 127.0.0.1:5000/api/tuner/session -H 'Content-Type: application/json' -d '{"open":false}'
```

Confirm every field of the `data` object, then confirm `GET /api/control`
reports `Stop` and no `tuning_mode`. If it does not, STOP and fix that first.

- [ ] **Step 2: Write the failing tests**

Append to `web-react/src/helpers/tuner/tunerApi.test.ts` (the file already sets
up `fetchMock`/`rs.stubGlobal`/`envelope`/`OK`):

```ts
describe("fetchAutoStatus", () => {
  it("posts both probe labels", async () => {
    fetchMock.mockResolvedValue(
      OK({
        current_tr: 41000,
        current_temp: 225,
        high_tr: 0,
        high_temp: 0,
        medium_tr: 0,
        medium_temp: 0,
        low_tr: 0,
        low_temp: 0,
        samples: 1,
        ready: false,
      }),
    );
    await fetchAutoStatus("Grill", "Probe1", "");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/tuner/auto-status");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      probe: "Grill",
      reference: "Probe1",
    });
  });

  it("keeps a null reading null", async () => {
    fetchMock.mockResolvedValue(
      OK({
        current_tr: 41000,
        current_temp: null,
        high_tr: 0,
        high_temp: 0,
        medium_tr: 0,
        medium_temp: 0,
        low_tr: 0,
        low_temp: 0,
        samples: 0,
        ready: false,
      }),
    );
    const result = await fetchAutoStatus("Grill", "Missing", "");
    expect(result.data?.current_temp).toBeNull();
  });

  it("surfaces the ready selection", async () => {
    fetchMock.mockResolvedValue(
      OK({
        current_tr: 41000,
        current_temp: 240,
        high_tr: 30000,
        high_temp: 240,
        medium_tr: 40000,
        medium_temp: 170,
        low_tr: 50000,
        low_temp: 100,
        samples: 12,
        ready: true,
      }),
    );
    const result = await fetchAutoStatus("Grill", "Probe1", "");
    expect(result.data?.ready).toBe(true);
    expect(result.data?.high_temp).toBe(240);
  });
});
```

Add `fetchAutoStatus` to the file's top import from `./tunerApi`.

- [ ] **Step 3: Run to verify failure**

```
cd web-react && bun run test src/helpers/tuner/tunerApi.test.ts
```

Expected: FAIL — `fetchAutoStatus` is not exported.

- [ ] **Step 4: Add the type**

Append to `web-react/src/helpers/tuner/tunerTypes.ts`:

```ts
/** GET-shaped but POSTed: one auto-tuning poll's result. Each poll records a
 * sample server-side and returns the running selection. `current_*` are the
 * live readings (null when a probe is not reporting); the high/medium/low
 * points are 0 until `ready`, at which point they are the three the solve
 * will use. */
export interface AutoStatus {
  current_tr: number | null;
  current_temp: number | null;
  high_tr: number;
  high_temp: number;
  medium_tr: number;
  medium_temp: number;
  low_tr: number;
  low_temp: number;
  /** How many samples have accumulated so far. */
  samples: number;
  /** True once the high−low temperature spread is wide enough to solve. */
  ready: boolean;
}
```

- [ ] **Step 5: Add the client function**

Append to `web-react/src/helpers/tuner/tunerApi.ts` (import `AutoStatus`):

```ts
/** Record one auto-tune sample and read the running selection.
 *
 * A POST, not a GET: each poll captures a datapoint. It writes only the
 * autotune queue server-side, never control -- the session calls remain the
 * sole writers of grill state. */
export const fetchAutoStatus = (probe: string, reference: string, baseUrl = BASE_URL) =>
  post<AutoStatus>(baseUrl, "auto-status", { probe, reference });
```

- [ ] **Step 6: Run tests and gates**

```
cd web-react && bun run test src/helpers/tuner/tunerApi.test.ts
bun run typecheck && bun run lint && bun run test
```

Expected: the three new cases pass; suite green. `bun run format` (or `bunx
biome check --write`) first if lint reports formatting.

- [ ] **Step 7: Commit**

```bash
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): typed client for auto-tune status

fetchAutoStatus POSTs both probe labels and returns the running
selection. current_* stay `number | null` -- a probe not reporting is
null, never a coerced 0 -- matching the Tr reading from slice 1.
EOF
```

---

## Task 4: `AutoTuneCard`

**Files:**
- Create: `web-react/src/components/tuner/AutoTuneCard.tsx`,
  `AutoTuneCard.test.tsx`
- Modify: `web-react/src/components/tuner/tuner.css`

**Interfaces:**
- Consumes: `AutoStatus` (Task 3).
- Produces:
  `AutoTuneCard({ probes, reference, onReferenceChange, tuneProbe, status, active }: { probes: string[]; reference: string; onReferenceChange: (label: string) => void; tuneProbe: string; status: AutoStatus | null; active: boolean })`.
  Task 5 renders one.

The card shows: a reference-probe `<select>` (labelled `Reference probe`,
disabled while `active`), the live reference temperature and the tuned probe's
resistance, a progress line (`samples` collected and whether the spread is wide
enough), and a `ready` state. It does NOT own the session or the Finish button —
Task 5's page does.

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/components/tuner/AutoTuneCard.test.tsx` covering, each as a
real assertion with inline fixtures:

```
- renders a reference-probe select excluding nothing (any probe may be the reference)
- shows the live reference temperature and the tuned probe's resistance
- says "waiting" for a null reading rather than 0
- shows how many samples have accumulated
- announces when the spread is not yet wide enough (not ready)
- announces ready once status.ready is true
- disables the reference select while active (a session is open)
- calls onReferenceChange when a new reference is picked
```

Use `getByRole("combobox", { name: /reference/i })`, `getByRole("status")` for
the progress/ready line, and text assertions for the readouts. Follow
`SegmentCard.test.tsx`'s structure.

- [ ] **Step 2: Run to verify failure**, then **Step 3: write the component and
  its CSS** (add `pf-tuner-auto*` rules to `tuner.css`; reuse `.pf-tuner-input`,
  `.pf-tuner-field-label`, `.pf-tuner-reading`, `.pf-tuner-stale` from slice 1
  where they fit). Only add rules a test's markup uses — `styleCoverage` fails
  on an unused rule.

- [ ] **Step 4: Run the tests** — expect all green.

- [ ] **Step 5: Negative control** — break the null-vs-0 branch (render
  `${status.current_temp} °` unconditionally) and confirm the "waiting" test
  fails; restore.

- [ ] **Step 6: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): the auto-tune accumulation card

A reference-probe selector, the live reference temperature and tuned
probe resistance, a running sample count, and a ready indicator. A null
reading reads "waiting", never 0. The card observes; the page owns the
session and Finish.
EOF
```

---

## Task 5: Manual/Auto toggle on `TunerPage`

**Files:**
- Modify: `web-react/src/components/tuner/TunerPage.tsx`, `TunerPage.test.tsx`,
  `tuner.css`

**Interfaces:**
- Consumes: `AutoTuneCard` (Task 4), `fetchAutoStatus` (Task 3).
- Produces: the finished two-mode page.

**Behaviour the page gains (the manual path stays exactly as slice 1 built it):**

1. A `Manual` / `Auto` segmented toggle, defaulting to Manual, disabled while a
   session is open (you cannot switch flow mid-tune).
2. In Auto mode: a `reference` selection defaulting to the first probe that is
   not the tune target. While the session is open, poll `fetchAutoStatus(tuneProbe,
   reference)` every **1000 ms** into an `AutoStatus` state — the same
   arm-only-when-idle interval discipline the manual Tr poll uses. Stop polling
   the instant the session leaves `open`.
3. `Finish` in Auto mode is enabled only when `status?.ready`. It builds the
   three points from the status's high/medium/low temps and Trs and sends them
   to the SAME `computeCoefficients` path, then closes the session and shows the
   chart and `ProfileForm` — identical to manual from that point on.
4. A `refused` session and a `finishError` render in the existing `role="alert"`
   slots. The reference and tune-probe selects are disabled while open.

- [ ] **Step 1: Write the failing tests**

Extend `TunerPage.test.tsx` (it already mocks the tuner API module and
`getSettings`; add `fetchAutoStatus` to the mock). Cover, at minimum:

```
- the toggle defaults to Manual and the three segment cards are shown
- switching to Auto shows the reference selector and hides the segment cards
- the toggle is disabled while a session is open
- Auto Start opens the session and begins polling auto-status with (tuneProbe, reference)
- Auto Finish is disabled until status.ready, then enabled
- Auto Finish sends the high/medium/low points to computeCoefficients, closes the session, and shows the chart + form
- leaving the page in Auto mode with an open session closes it
```

Fake timers installed BEFORE `render` (bind as `installFakeClock` if Biome
objects). Reuse the polling-cadence assertion pattern from the manual test.

- [ ] **Step 2: Run to verify failure, then wire the page.** Extract nothing
  the manual path needs to keep — add the toggle and an auto branch beside the
  existing manual branch. Add the toggle + any `pf-tuner-mode*` rules to
  `tuner.css` in the same edit as their markup.

- [ ] **Step 3: Negative control** — make Auto Finish read `recorded` (the
  manual state) instead of the status's high/medium/low, and confirm the
  "sends the high/medium/low points" test fails; restore.

- [ ] **Step 4: Gates and commit**

```bash
cd web-react && bun run typecheck && bun run lint && bun run test
cd .. && jj new
jj describe --stdin <<'EOF'
feat(web-react): Manual/Auto toggle on the tuner page

Auto mode adds a reference-probe selector and polls auto-status once a
second while the session is open, then finishes through the same
coefficients/close/chart/save path manual uses. The manual flow is
untouched; the toggle is disabled once a session is open so the flow
cannot change mid-tune.
EOF
```

---

## Task 6: End to end, baselines and closeout

**Files:**
- Modify: `web-react/tests/e2e/tuner.spec.ts`, `apiFixtures.ts`, `pageSpecs.ts`,
  baselines
- Modify: `docs/superpowers/react-migration-backlog.md`

- [ ] **Step 1: Backend up with the RIGHT worker**

```bash
cd /home/dannyb/sources/PiFire && \
  .venv/bin/gunicorn -k gthread --threads 25 -w 1 -b 127.0.0.1:5000 --reload app:app \
  > "$SCRATCH/gunicorn.log" 2>&1 &
```

Never `control.py` (already running). Confirm `GET 127.0.0.1:5000/api/control`
answers before running the spec.

- [ ] **Step 2: Extend the live spec**

Add auto-flow tests to `tests/e2e/tuner.spec.ts`, reusing its `API`
(`127.0.0.1`), the abort+record admin guard, and the `afterEach` force-close +
Stop poll. Cover:

- switching to Auto and pressing Start opens a session (grill → Monitor) and the
  auto readout appears;
- `POST /api/tuner/auto-status {probe, reference}` returns the envelope with
  `samples` and `ready` fields (a direct `request` call, like the Tr test);
- leaving the page (client-side navbar click) closes the session → Stop.

Do NOT wait for `ready`: a real 50 °F spread will not occur on a
Stopped/Monitor grill during a test. Assert the accumulation shape and the
safety contract, not convergence. NEVER click Save & Apply against the live
backend.

- [ ] **Step 3: Run it**

```
cd web-react && bun run typecheck:e2e && bunx playwright test tuner.spec.ts --project=app
```

Afterwards confirm by hand the grill is in Stop.

- [ ] **Step 4: Stub and page spec**

Extend `stubTuner` to fulfil `**/api/tuner/auto-status` with a pinned,
not-ready status so a fidelity capture never touches the live grill. Decide
whether the fidelity page spec captures the Auto screen: the tuner baseline is
captured before Start; if the Auto screen's pre-Start geometry (the reference
selector) is worth a landmark, add a second capture or extend the existing one
and say which. If it is only a `<select>` already measured elsewhere, note that
and leave the manual capture as the tuner baseline.

- [ ] **Step 5: Capture and audit baselines**

Snapshot `tests/e2e/baselines` first, then `bun run baseline:capture`, then diff
every changed file. The Auto toggle adds markup to `/tuner`, so
`tuner-*.json` will move — expected. `history-390x844.json` may re-drift on the
un-stubbed saved-cooks list (a known, documented non-tuner flake — revert it if
it moves and nothing else on /history did). Nothing else should change.

- [ ] **Step 6: Fidelity gate**

```
cd web-react && bun run test:e2e:fidelity
```

Expected green. `pellets 390x844` has a known intermittent failure unrelated to
this work; re-run once and confirm the pellets baselines are byte-unchanged
before treating it as real.

- [ ] **Step 7: Close out the backlog**

In `docs/superpowers/react-migration-backlog.md`:

- Mark the **tuner** §8 line `[x]` SHIPPED — both flows now exist.
- Extend the Tuner SHIPPED entry with the auto flow: the one new endpoint, the
  autotune-flush-on-open, the reference-probe temp lookup, and that the auto
  Finish reuses the manual coefficients/save path.
- Remove "The AUTO flow is not ported" from the tuner deferrals; move whatever
  genuinely remains (e.g. the still-live `blueprints/tuner`, the untouched
  `tuner.py` bare-excepts) under a single tuner deferral note, and add any new
  deferral this slice creates (e.g. the auto e2e cannot reach `ready`, so
  convergence is covered only by the backend seeded test).

- [ ] **Step 8: Full gate**

```
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
cd web-react && bun run typecheck && bun run typecheck:e2e && bun run lint && bun run test && bun run test:e2e:fidelity
```

Report each count against the pre-slice baselines: pytest **3438**, rstest
**1568** across 166 files, fidelity **133**.

- [ ] **Step 9: Commit**

```bash
cd /home/dannyb/sources/PiFire && jj new
jj describe --stdin <<'EOF'
test(web-react): live auto-tune e2e, fidelity baselines and closeout

The auto flow against the real grill, with the same Stop-restoration
safety net as the manual spec. Convergence (ready) is not driven live --
a 50 F spread will not happen on a monitored grill during a test -- so
the ready path is covered by the backend's seeded test, and the e2e
proves accumulation and the session lifecycle.
EOF
```

---

## Self-Review

**Spec coverage.** Every part of Flask's auto flow maps to a task: the session
flush that resets the store (1), the sample accumulation and the ready
selection (2), the client (3), the reference selector and live readout (4), the
mode toggle and the auto Finish that reuses the manual solve (5), and the live
proof plus closeout (6). The manual flow, the coefficients solve, the chart and
the profile save are explicitly reused, not rebuilt.

**Placeholders.** Tasks 1–3 carry their code. Tasks 4 and 5 specify behaviour
and the required assertions rather than full listings — `AutoTuneCard`'s markup
and `TunerPage`'s toggle depend on choices made in the tasks before them, and
writing them blind here would produce code the implementer rewrites. This is the
same stated exception slice 1 made for its page task; if the implementer wants
literal code, write it once Task 4 lands.

**Type consistency.** `AutoStatus` is defined once (Task 3) and imported by 4
and 5. `fetchAutoStatus(probe, reference, baseUrl?)` keeps its signature across
its definition, its tests, and Task 5's poll. The recorded sample shape
`{ref_T, probe_Tr}` matches what `calc_auto_tune_status` (unchanged) consumes —
verified against `blueprints/tuner/tuner.py`.

**Facts verified against live code and a running backend on 2026-07-28:** the
`control:current` P/F/AUX shape and lookup order; that `write_autotune` is a
direct SqliteQueue push needing no drain; that `calc_auto_tune_status` mutates
its `status_data` argument and needs >10 samples; and that the session,
coefficients, chart and save from slice 1 are reusable without change. Treat any
disagreement between this plan and live code as the plan being wrong.

**The hazard is unchanged from slice 1 and so are its nets.** Opening an auto
session moves the live grill to Monitor. The autouse pytest fixture, the session
hook's unmount tests (already shipped), and the e2e's `afterEach` control read
all still apply; Task 5's page tests add the auto-mode unmount case. The one new
write path — sample accumulation — deliberately touches the autotune queue and
nothing else, so it cannot leave grill state changed.
