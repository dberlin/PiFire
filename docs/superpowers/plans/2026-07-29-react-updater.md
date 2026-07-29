# React Updater Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Flask software-update page to React as a `/update` route, reached from an Admin "System Update" card, backed by a new `blueprints/api_update` JSON surface.

**Architecture:** A new JSON REST blueprint (`blueprints/api_update`, mirroring `blueprints/api_tuner`) wraps the existing `updater.py` functions and install-status accessors — reads passthrough, mutations fire the exact detached `updater.py` command Flask fires, behind the same `is_real_hardware()` / STOP-mode / branch-allowlist guards. A typed React client (`updateApi.ts`), an `UpdatePage`, and a `SystemUpdateCard` on the Admin page consume it; progress polling reuses the wizard's `101`/`142` install-status sentinel contract.

**Tech Stack:** Flask blueprint + `common.app.api_response`; React 19 + react-router, TS, rsbuild, Biome, @rstest/core, Playwright, bun. Python 3.14, pytest via `uv`.

**Design doc:** `docs/superpowers/specs/2026-07-29-react-updater-design.md`

## Global Constraints

- **bun, never npm.** React gate: `cd web-react && bun run typecheck && bun run lint && bun run test && bun run build`.
- **Python:** run tests with `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <paths>`. Format changed Python with `.venv/bin/ruff format <files>` before committing (ruff pinned <0.16; never `uvx ruff`).
- **No suppressions:** no `biome-ignore`, `@ts-expect-error`, `eslint-disable`, or bare `except:` you add.
- **Every response is a `common.app.api_response(result, message, data)` envelope** — `result` is `"OK"` or `"Error"`, returned as `jsonify(...), <status>`. This is what the React `unpack` helper branches on (`body.result === "OK"`).
- **Safety — the load-bearing rule:** every mutating endpoint fires its shell-out **only under `is_real_hardware(settings)`**, through a single `_fire()` seam. Tests monkeypatch `blueprints.api_update.routes.os.system` and assert the exact command — **no test runs git/pip/reboot**, and the e2e never triggers a real update.
- **Server-side guards:** `pull`/`upgrade` refuse with **409** unless `read_control()["mode"] == Mode.STOP`; `branch` refuses with **400** unless `target` is in the fetched branch list; `log` refuses with **400** on a non-positive-integer `commits`.
- **Untouched:** `blueprints/update/` and `tests/web/test_page_update.py` stay exactly as they are (the Flask net). `updater.py` is called, never modified.
- **Out of scope:** the post-update "what's new" modal (shell chrome; recorded in `react-migration-backlog.md`).
- **New `pf-*` CSS classes** need a rule in a `.css` file AND a consumer (the `cssCoverage`/`styleCoverage` gates); do not add them to the `UNSTYLED` allowlist. Reuse `pf-admin-*` classes where they fit.

---

## File Structure

**Backend (new blueprint):**
- Create `blueprints/api_update/__init__.py` — the Blueprint object.
- Create `blueprints/api_update/routes.py` — 4 reads + 4 mutations.
- Modify `app.py` — import + `register_blueprint` (two lines, next to the other `api_*` registrations).
- Create `tests/web/test_api_update.py` — endpoint tests (updater fns + `os.system` stubbed).

**Frontend:**
- Create `web-react/src/helpers/update/updateTypes.ts` — shared types.
- Create `web-react/src/helpers/update/updateApi.ts` (+ `.test.ts`) — typed client.
- Create `web-react/src/components/update/UpdatePage.tsx` (+ `.test.tsx`) — the page.
- Create `web-react/src/components/update/update.css` — only classes not already in `admin.css`.
- Create `web-react/src/components/admin/SystemUpdateCard.tsx` (+ `.test.tsx`).
- Modify `web-react/src/components/admin/AdminPage.tsx` — render `<SystemUpdateCard/>`.
- Modify `web-react/src/components/App.tsx` — add the `/update` route.
- Create `web-react/tests/e2e/update.spec.ts` — mocked-backend flow.

## Task dependency / parallelization

- **Wave 1:** Task 1 (reads) → Task 2 (mutations) are **serial** (same `routes.py`). Task 3 (TS client) can run **in parallel** with Tasks 1–2 — it is written against the endpoint contracts in this plan, not against the running server.
- **Wave 2:** Task 4 (UpdatePage) needs Task 3. Task 6 (SystemUpdateCard/AdminPage) needs Task 3 and touches different files, so it may run parallel to Task 4/5.
- **Serial within the page:** Task 4 → Task 5 (both edit `UpdatePage.tsx`).
- **Wave 3:** Task 7 (e2e + gate) needs everything.

Concurrency needs isolated jj workspaces with disjoint files; when in doubt, run serially.

---

### Task 1: `blueprints/api_update` read endpoints + registration

**Files:**
- Create: `blueprints/api_update/__init__.py`
- Create: `blueprints/api_update/routes.py`
- Modify: `app.py` (imports near line 89; `register_blueprint` near line 114)
- Test: `tests/web/test_api_update.py`

**Interfaces:**
- Produces: `GET /api/update/state` → `{version, branch, branches[], remote_url, remote_version}`; `GET /api/update/check` → `{current, behind}`; `GET /api/update/log?commits=N` → `{output}`; `GET /api/update/status` → `{percent, status, output}`. All wrapped in `api_response`. Also produces the module-level helpers `_ok`, `_error`, `_python_exec`, `_fire` that Task 2 consumes.

- [ ] **Step 1: Write the failing tests.** Create `tests/web/test_api_update.py`. Stub the `updater` functions where the routes module imported them, so no git/network runs.

```python
import json

import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def _stub_reads(monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(
        ur,
        "get_update_data",
        lambda settings: {
            "version": "v1.8.0 (v1.8.0)",
            "branch_target": "main",
            "branches": ["main", "dev", "prototype"],
            "remote_url": "https://github.com/nebhead/PiFire",
            "remote_version": "v1.8.1",
        },
    )
    return ur


def test_state_returns_the_update_data_shape(ds, client, monkeypatch):
    _stub_reads(monkeypatch)
    body = client.get("/api/update/state").get_json()
    assert body["result"] == "OK"
    assert body["data"] == {
        "version": "v1.8.0 (v1.8.0)",
        "branch": "main",
        "branches": ["main", "dev", "prototype"],
        "remote_url": "https://github.com/nebhead/PiFire",
        "remote_version": "v1.8.1",
    }


def test_check_reports_commits_behind(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_available_updates", lambda: {"success": True, "commits_behind": 3})
    body = client.get("/api/update/check").get_json()
    assert body["result"] == "OK"
    assert body["data"]["behind"] == 3
    assert isinstance(body["data"]["current"], str)


def test_check_maps_a_failed_fetch_to_an_error_envelope(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(
        ur, "get_available_updates", lambda: {"success": False, "message": "ERROR Getting Remote"}
    )
    resp = client.get("/api/update/check")
    assert resp.status_code == 502
    assert resp.get_json()["result"] == "Error"
    assert "ERROR" in resp.get_json()["message"]


def test_log_defaults_to_ten_and_returns_output(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    seen = {}
    monkeypatch.setattr(ur, "get_log", lambda num_commits: (seen.setdefault("n", num_commits), ("abc123 msg", ""))[1])
    body = client.get("/api/update/log").get_json()
    assert seen["n"] == 10
    assert body["data"]["output"] == "abc123 msg"


def test_log_rejects_a_non_numeric_commit_count(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_log", lambda num_commits: ("", ""))
    resp = client.get("/api/update/log?commits=abc")
    assert resp.status_code == 400


def test_status_passes_through_the_install_status_triplet(ds, client, monkeypatch):
    import blueprints.api_update.routes as ur

    monkeypatch.setattr(ur, "get_updater_install_status", lambda: (42, "Working...", "line"))
    body = client.get("/api/update/status").get_json()
    assert body["data"] == {"percent": 42, "status": "Working...", "output": "line"}
```

- [ ] **Step 2: Run, confirm they fail.** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_update.py -q` — expect ImportError / 404 (blueprint absent).

- [ ] **Step 3: Create the blueprint object.** `blueprints/api_update/__init__.py`:

```python
from flask import Blueprint

api_update_bp = Blueprint("api_update_bp", __name__, url_prefix="/api/update")

from . import routes  # noqa: E402,F401
```

- [ ] **Step 4: Implement the read routes + helpers.** `blueprints/api_update/routes.py`:

```python
"""JSON endpoints for PiFire's software updater.

A thin JSON surface over updater.py, mirroring blueprints/api_tuner. Reads are
pure passthroughs; mutations fire the SAME detached `updater.py <flags> &`
command the Flask page fires (blueprints/update/routes.py), behind the same
is_real_hardware() gate plus a STOP-mode and branch-allowlist guard, and seed
the install-status row the client then polls. No rendered HTML -- so none of
Flask's render_template_string paths (the post-message / branch-change alerts)
come along, and their template-injection surface stays behind.
"""

import os

from flask import jsonify, request

from common.app import api_response
from common.datastore_accessors import (
    get_updater_install_status,
    read_control,
    read_settings,
    set_updater_install_status,
)
from common.modes import Mode
from common.system import is_real_hardware
from updater import get_available_updates, get_branch, get_log, get_update_data

from . import api_update_bp


def _ok(data=None):
    return jsonify(api_response("OK", None, data)), 200


def _error(message, status, **data):
    return jsonify(api_response("Error", message, data or None)), status


def _python_exec(settings):
    return settings["globals"].get("python_exec", "python")


def _fire(settings, command):
    """Fire a detached updater.py process, ONLY on real hardware. Returns
    whether it fired. `os.system` is the single seam tests neutralize; nothing
    else in this module shells out."""
    if is_real_hardware(settings):
        os.system(command)
        return True
    return False


@api_update_bp.route("/state", methods=["GET"])
def update_state():
    d = get_update_data(read_settings())
    return _ok(
        {
            "version": d["version"],
            "branch": d["branch_target"],
            "branches": d["branches"],
            "remote_url": d["remote_url"],
            "remote_version": d["remote_version"],
        }
    )


@api_update_bp.route("/check", methods=["GET"])
def update_check():
    settings = read_settings()
    avail = get_available_updates()
    if not avail.get("success"):
        return _error(avail.get("message", "update check failed"), 502)
    return _ok({"current": settings["versions"]["server"], "behind": avail["commits_behind"]})


@api_update_bp.route("/log", methods=["GET"])
def update_log():
    commits = request.args.get("commits", "10")
    if not commits.isdigit() or int(commits) <= 0:
        return _error("commits must be a positive integer", 400)
    result, error_msg = get_log(num_commits=int(commits))
    if error_msg:
        return _error(error_msg, 502)
    return _ok({"output": result})


@api_update_bp.route("/status", methods=["GET"])
def update_status():
    percent, status, output = get_updater_install_status()
    return _ok({"percent": percent, "status": status, "output": output})
```

- [ ] **Step 5: Register the blueprint in `app.py`.** Add the import beside the other `api_*` imports (near line 89):

```python
from blueprints.api_update import api_update_bp
```

and the registration beside the other `api_*` registrations (near line 114):

```python
app.register_blueprint(api_update_bp, url_prefix="/api/update")
```

- [ ] **Step 6: Run, confirm the read tests pass.** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_update.py -q`

- [ ] **Step 7: Format + commit.** `.venv/bin/ruff format blueprints/api_update/ tests/web/test_api_update.py app.py`, then commit `blueprints/api_update/`, `app.py`, `tests/web/test_api_update.py`.

---

### Task 2: `blueprints/api_update` mutation endpoints + guards

**Files:**
- Modify: `blueprints/api_update/routes.py` (append the four mutation routes)
- Test: `tests/web/test_api_update.py` (append)

**Interfaces:**
- Consumes: `_ok`, `_error`, `_python_exec`, `_fire` (Task 1).
- Produces: `POST /api/update/branches/refresh`, `POST /api/update/branch {target}`, `POST /api/update/pull`, `POST /api/update/upgrade` — each returns `{started: true}` on success.

- [ ] **Step 1: Write the failing tests.** Append to `tests/web/test_api_update.py`. The `finish` tests in `tests/web/test_api_wizard.py:262-300` are the exact model: neutralize `routes.os.system`, drive control mode, assert status + the `fired` list.

```python
from common.common import WriteKind


def _set_mode(mode):
    from common.datastore_accessors import read_control, write_control

    ctrl = read_control()
    ctrl["mode"] = mode
    write_control(ctrl, WriteKind.OVERWRITE, origin="test")


def _set_real_hw(value):
    from common.datastore_accessors import read_settings, write_settings_store

    s = read_settings()
    s["platform"]["real_hw"] = value
    write_settings_store(s)


def _neutralize(monkeypatch):
    import blueprints.api_update.routes as ur

    fired = []
    monkeypatch.setattr(ur.os, "system", lambda cmd: fired.append(cmd))
    return ur, fired


def test_refresh_fires_the_r_flag_on_real_hardware(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(True)
    resp = client.post("/api/update/branches/refresh")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"started": True}
    assert fired == ["python updater.py -r &"]


def test_mutations_do_not_fire_off_real_hardware(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(False)
    resp = client.post("/api/update/branches/refresh")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"started": True}
    assert fired == []  # gated: no process on a non-Pi


def test_change_branch_validates_against_the_branch_list(ds, client, monkeypatch):
    ur, fired = _neutralize(monkeypatch)
    monkeypatch.setattr(ur, "get_update_data", lambda settings: {"branches": ["main", "dev"]})
    _set_real_hw(True)
    ok = client.post("/api/update/branch", data=json.dumps({"target": "dev"}), content_type="application/json")
    assert ok.status_code == 200
    assert fired == ["python updater.py -b dev &"]
    bad = client.post("/api/update/branch", data=json.dumps({"target": "evil; rm -rf"}), content_type="application/json")
    assert bad.status_code == 400
    assert fired == ["python updater.py -b dev &"]  # rejected target never fired


def test_pull_is_blocked_unless_stopped(ds, client, monkeypatch):
    ur, fired = _neutralize(monkeypatch)
    monkeypatch.setattr(ur, "get_branch", lambda: ("main", ""))
    _set_real_hw(True)
    _set_mode("Hold")
    blocked = client.post("/api/update/pull")
    assert blocked.status_code == 409
    assert fired == []
    _set_mode(Mode.STOP)
    ok = client.post("/api/update/pull")
    assert ok.status_code == 200
    assert fired == ["python updater.py -u main -p &"]


def test_upgrade_is_blocked_unless_stopped(ds, client, monkeypatch):
    _, fired = _neutralize(monkeypatch)
    _set_real_hw(True)
    _set_mode("Hold")
    assert client.post("/api/update/upgrade").status_code == 409
    assert fired == []
    _set_mode(Mode.STOP)
    assert client.post("/api/update/upgrade").status_code == 200
    assert fired == ["python updater.py -i &"]
```

Add `from common.modes import Mode` at the top of the test file.

- [ ] **Step 2: Run, confirm they fail.** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_update.py -q -k "refresh or branch or pull or upgrade or real_hardware"` — expect 404/405.

- [ ] **Step 3: Append the mutation routes** to `blueprints/api_update/routes.py`:

```python
@api_update_bp.route("/branches/refresh", methods=["POST"])
def update_branches_refresh():
    settings = read_settings()
    set_updater_install_status(0, "Refreshing remote branches...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -r &")
    return _ok({"started": True})


@api_update_bp.route("/branch", methods=["POST"])
def update_branch():
    settings = read_settings()
    body = request.get_json(silent=True) or {}
    target = body.get("target")
    branches = get_update_data(settings)["branches"]
    if target not in branches:
        return _error("invalid_branch", 400, branches=branches)
    set_updater_install_status(0, "Starting Branch Change...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -b {target} &")
    return _ok({"started": True})


@api_update_bp.route("/pull", methods=["POST"])
def update_pull():
    settings = read_settings()
    if read_control().get("mode") != Mode.STOP:
        return _error("system_active", 409)
    branch, error_msg = get_branch()
    if error_msg:
        return _error(error_msg, 502)
    set_updater_install_status(0, "Starting Update...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -u {branch} -p &")
    return _ok({"started": True})


@api_update_bp.route("/upgrade", methods=["POST"])
def update_upgrade():
    settings = read_settings()
    if read_control().get("mode") != Mode.STOP:
        return _error("system_active", 409)
    set_updater_install_status(0, "Starting Upgrade...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -i &")
    return _ok({"started": True})
```

Add `get_branch` to the existing `from updater import ...` line (it is already listed in Task 1's import).

- [ ] **Step 4: Run, confirm all tests pass.** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_update.py -q`

- [ ] **Step 5: Confirm the Flask net still passes** (proves `blueprints/update` untouched): `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_page_update.py -q`

- [ ] **Step 6: Format + commit.** `.venv/bin/ruff format blueprints/api_update/routes.py tests/web/test_api_update.py`, then commit.

---

### Task 3: `updateApi.ts` typed client

**Files:**
- Create: `web-react/src/helpers/update/updateTypes.ts`
- Create: `web-react/src/helpers/update/updateApi.ts`
- Test: `web-react/src/helpers/update/updateApi.test.ts`

**Interfaces:**
- Produces: `UpdateState`, `UpdateCheck`, `UpdateStatus`, `UpdateResult<T>` types; and `fetchUpdateState`, `fetchUpdateCheck`, `fetchUpdateLog(commits)`, `fetchUpdateStatus`, `refreshBranches`, `changeBranch(target)`, `pullUpdate`, `upgradeDeps` — each `(…, baseUrl?) => Promise<UpdateResult<T>>`. Modeled on `web-react/src/helpers/admin/adminApi.ts`.

- [ ] **Step 1: Write the types.** `web-react/src/helpers/update/updateTypes.ts`:

```ts
export interface UpdateState {
  version: string;
  branch: string;
  branches: string[];
  remote_url: string;
  remote_version: string;
}

export interface UpdateCheck {
  current: string;
  behind: number;
}

export interface UpdateStatus {
  percent: number;
  status: string;
  output: string;
}

/** Started-flag returned by every mutation. */
export interface UpdateStarted {
  started: boolean;
}

/** Same envelope shape helpers/admin/adminApi.ts returns. */
export interface UpdateResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
}
```

- [ ] **Step 2: Write the failing tests.** `web-react/src/helpers/update/updateApi.test.ts`, following `helpers/command.test.ts`/`adminApi` idiom (`rs.stubGlobal("fetch", …)` in each test, `rs.unstubAllGlobals()` in `afterEach`).

```ts
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import {
  changeBranch,
  fetchUpdateCheck,
  fetchUpdateLog,
  fetchUpdateState,
  fetchUpdateStatus,
  pullUpdate,
  refreshBranches,
  upgradeDeps,
} from "./updateApi";

afterEach(() => {
  rs.unstubAllGlobals();
});

function stub(status: number, body: unknown) {
  const fetchMock = rs.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }));
  rs.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("updateApi reads", () => {
  it("fetchUpdateState GETs /api/update/state and unwraps data", async () => {
    const fetchMock = stub(200, {
      result: "OK",
      data: { version: "v1", branch: "main", branches: ["main"], remote_url: "u", remote_version: "v2" },
    });
    const r = await fetchUpdateState("");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/update/state");
    expect(r.ok).toBe(true);
    expect(r.data?.branch).toBe("main");
  });

  it("fetchUpdateCheck maps a 502 Error envelope to ok:false", async () => {
    stub(502, { result: "Error", message: "ERROR Getting Remote" });
    const r = await fetchUpdateCheck("");
    expect(r.ok).toBe(false);
    expect(r.status).toBe(502);
    expect(r.message).toContain("ERROR");
  });

  it("fetchUpdateLog passes commits as a query param", async () => {
    const fetchMock = stub(200, { result: "OK", data: { output: "log" } });
    await fetchUpdateLog(25, "");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/update/log?commits=25");
  });

  it("fetchUpdateStatus returns the triplet", async () => {
    stub(200, { result: "OK", data: { percent: 142, status: "done", output: "x" } });
    const r = await fetchUpdateStatus("");
    expect(r.data?.percent).toBe(142);
  });
});

describe("updateApi mutations", () => {
  it("changeBranch POSTs the target", async () => {
    const fetchMock = stub(200, { result: "OK", data: { started: true } });
    const r = await changeBranch("dev", "");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/update/branch");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ target: "dev" });
    expect(r.data?.started).toBe(true);
  });

  it("pullUpdate surfaces a 409 as ok:false", async () => {
    stub(409, { result: "Error", message: "system_active" });
    const r = await pullUpdate("");
    expect(r.ok).toBe(false);
    expect(r.status).toBe(409);
    expect(r.message).toBe("system_active");
  });

  it("refreshBranches and upgradeDeps POST their paths", async () => {
    const fetchMock = stub(200, { result: "OK", data: { started: true } });
    await refreshBranches("");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/update/branches/refresh");
    await upgradeDeps("");
    expect((fetchMock.mock.calls[1] as [string])[0]).toBe("/api/update/upgrade");
  });
});
```

- [ ] **Step 3: Run, confirm they fail.** `cd web-react && bun run test src/helpers/update/updateApi.test.ts`

- [ ] **Step 4: Implement `updateApi.ts`.** Copy the `unpack`/`get`/`post` shape from `helpers/admin/adminApi.ts:31-76` verbatim (retyped for these routes):

```ts
import type {
  UpdateCheck,
  UpdateResult,
  UpdateStarted,
  UpdateState,
  UpdateStatus,
} from "./updateTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const url = (baseUrl: string, path: string) => `${baseUrl}/api/update/${path}`;

async function unpack<T>(res: Response): Promise<UpdateResult<T>> {
  const body = (await res.json().catch(() => ({}))) as {
    result?: string;
    message?: string;
    data?: T | null;
  };
  return {
    ok: res.ok && body.result === "OK",
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    data: (body.data ?? null) as T | null,
  };
}

async function get<T>(baseUrl: string, path: string): Promise<UpdateResult<T>> {
  try {
    return await unpack<T>(await fetch(url(baseUrl, path)));
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

async function post<T>(baseUrl: string, path: string, body: unknown = {}): Promise<UpdateResult<T>> {
  try {
    const res = await fetch(url(baseUrl, path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await unpack<T>(res);
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

export const fetchUpdateState = (baseUrl = BASE_URL) => get<UpdateState>(baseUrl, "state");
export const fetchUpdateCheck = (baseUrl = BASE_URL) => get<UpdateCheck>(baseUrl, "check");
export const fetchUpdateLog = (commits: number, baseUrl = BASE_URL) =>
  get<{ output: string }>(baseUrl, `log?commits=${commits}`);
export const fetchUpdateStatus = (baseUrl = BASE_URL) => get<UpdateStatus>(baseUrl, "status");

export const refreshBranches = (baseUrl = BASE_URL) =>
  post<UpdateStarted>(baseUrl, "branches/refresh");
export const changeBranch = (target: string, baseUrl = BASE_URL) =>
  post<UpdateStarted>(baseUrl, "branch", { target });
export const pullUpdate = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "pull");
export const upgradeDeps = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "upgrade");
```

- [ ] **Step 5: Run, confirm pass.** `cd web-react && bun run test src/helpers/update/updateApi.test.ts && bun run typecheck`

- [ ] **Step 6: Format + commit.** `cd web-react && bun run format`, then commit the three files.

---

### Task 4: `UpdatePage` — status, branch, actions, log + `/update` route

**Files:**
- Create: `web-react/src/components/update/UpdatePage.tsx`
- Create: `web-react/src/components/update/update.css`
- Test: `web-react/src/components/update/UpdatePage.test.tsx`
- Modify: `web-react/src/components/App.tsx` (add the route)

**Interfaces:**
- Consumes: all of `updateApi.ts` (Task 3).
- Produces: `UpdatePage` default export; the `/update` route. Task 5 adds the progress panel to this same component.

This task builds the page WITHOUT the progress panel: it renders state, runs the four actions, and shows the log. A mutation's `{started:true}` sets a local `busy` message; Task 5 turns that into live polling.

- [ ] **Step 1: Write the failing tests.** `web-react/src/components/update/UpdatePage.test.tsx`. Mock `updateApi` with `rs.mock`.

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import * as api from "../../helpers/update/updateApi";
import { UpdatePage } from "./UpdatePage";

rs.mock("../../helpers/update/updateApi", () => ({
  fetchUpdateState: rs.fn(),
  fetchUpdateCheck: rs.fn(),
  fetchUpdateLog: rs.fn(),
  fetchUpdateStatus: rs.fn(),
  refreshBranches: rs.fn(),
  changeBranch: rs.fn(),
  pullUpdate: rs.fn(),
  upgradeDeps: rs.fn(),
}));

const state = {
  ok: true,
  status: 200,
  message: "",
  data: { version: "v1.8.0", branch: "main", branches: ["main", "dev"], remote_url: "u", remote_version: "v1.8.1" },
};

function seed(overrides: Partial<Record<keyof typeof api, unknown>> = {}) {
  (api.fetchUpdateState as ReturnType<typeof rs.fn>).mockResolvedValue(state);
  (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
    ok: true, status: 200, message: "", data: { current: "v1.8.0", behind: 3 },
  });
  for (const [k, v] of Object.entries(overrides)) {
    (api[k as keyof typeof api] as ReturnType<typeof rs.fn>).mockResolvedValue(v);
  }
}

const renderPage = () => render(<MemoryRouter><UpdatePage /></MemoryRouter>);

afterEach(cleanup);

describe("UpdatePage", () => {
  it("shows the current version, branch and commits-behind", async () => {
    seed();
    renderPage();
    expect(await screen.findByText(/v1\.8\.0/)).toBeInTheDocument();
    expect(await screen.findByText(/3 commits behind/i)).toBeInTheDocument();
  });

  it("Change Branch posts the selected branch", async () => {
    seed({ changeBranch: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.change(screen.getByLabelText(/branch/i), { target: { value: "dev" } });
    fireEvent.click(screen.getByRole("button", { name: /change branch/i }));
    await waitFor(() => expect(api.changeBranch).toHaveBeenCalledWith("dev"));
  });

  it("Update to latest calls pullUpdate", async () => {
    seed({ pullUpdate: { ok: true, status: 200, message: "", data: { started: true } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /update to latest/i }));
    await waitFor(() => expect(api.pullUpdate).toHaveBeenCalled());
  });

  it("surfaces a 409 refusal from pullUpdate as an inline message", async () => {
    seed({ pullUpdate: { ok: false, status: 409, message: "system_active", data: null } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /update to latest/i }));
    expect(await screen.findByText(/stop the grill/i)).toBeInTheDocument();
  });

  it("Show log fetches and renders the git log", async () => {
    seed({ fetchUpdateLog: { ok: true, status: 200, message: "", data: { output: "abc123 fix" } } });
    renderPage();
    await screen.findByText(/v1\.8\.0/);
    fireEvent.click(screen.getByRole("button", { name: /show log/i }));
    expect(await screen.findByText(/abc123 fix/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, confirm they fail.** `cd web-react && bun run test src/components/update/UpdatePage.test.tsx`

- [ ] **Step 3: Implement `UpdatePage.tsx`.** Load `state` + `check` on mount (render-phase-safe: fetch in an effect that sets state, standard async-load pattern used across the app). Map `system_active` → "Stop the grill before updating." Reuse `pf-admin-card`/`pf-admin-btn` classes; add only genuinely new classes to `update.css`.

```tsx
import { useEffect, useState } from "react";
import {
  changeBranch,
  fetchUpdateCheck,
  fetchUpdateLog,
  fetchUpdateState,
  pullUpdate,
  refreshBranches,
  upgradeDeps,
} from "../../helpers/update/updateApi";
import type { UpdateResult, UpdateStarted, UpdateState } from "../../helpers/update/updateTypes";
import "./update.css";

const refusalText = (r: UpdateResult<unknown>): string =>
  r.status === 409 ? "Stop the grill before updating." : r.message;

export function UpdatePage() {
  const [state, setState] = useState<UpdateState | null>(null);
  const [behind, setBehind] = useState<number | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [log, setLog] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const s = await fetchUpdateState();
    if (s.ok && s.data) {
      setState(s.data);
      setSelected(s.data.branch);
    }
    const c = await fetchUpdateCheck();
    setBehind(c.ok && c.data ? c.data.behind : null);
  };

  useEffect(() => {
    void load();
  }, []);

  // After a mutation returns started:true, Task 5 begins polling here.
  const run = async (fn: () => Promise<UpdateResult<UpdateStarted>>) => {
    setNote(null);
    setBusy(true);
    const r = await fn();
    setBusy(false);
    if (!r.ok) {
      setNote(refusalText(r));
      return;
    }
    setNote("Started.");
  };

  const showLog = async () => {
    const r = await fetchUpdateLog(10);
    setLog(r.ok && r.data ? r.data.output : `Log failed: ${r.message}`);
  };

  if (!state) return <div className="pf-admin">Loading updater…</div>;

  return (
    <div className="pf-admin">
      <section className="pf-admin-card pf-admin-wide">
        <h2>System Update</h2>
        <p>
          Current: <strong>{state.version}</strong> on branch <strong>{state.branch}</strong>
        </p>
        <p>Remote: {state.remote_version}</p>
        <p>{behind === null ? "Update status unavailable" : behind > 0 ? `${behind} commits behind` : "Up to date"}</p>
      </section>

      <section className="pf-admin-card">
        <h3>Branch</h3>
        <label>
          Branch
          <select value={selected} onChange={(e) => setSelected(e.target.value)}>
            {state.branches.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="pf-admin-btn" disabled={busy} onClick={() => void run(() => changeBranch(selected))}>
          Change Branch
        </button>
        <button type="button" className="pf-admin-btn" disabled={busy} onClick={() => void run(() => refreshBranches())}>
          Refresh remote branches
        </button>
      </section>

      <section className="pf-admin-card">
        <h3>Actions</h3>
        <button type="button" className="pf-admin-btn" disabled={busy} onClick={() => void run(() => pullUpdate())}>
          Update to latest
        </button>
        <button type="button" className="pf-admin-btn" disabled={busy} onClick={() => void run(() => upgradeDeps())}>
          Upgrade dependencies
        </button>
        {note && <p className="pf-update-note">{note}</p>}
      </section>

      <section className="pf-admin-card pf-admin-wide">
        <h3>Update log</h3>
        <button type="button" className="pf-admin-btn" onClick={() => void showLog()}>
          Show log
        </button>
        {log !== null && <pre className="pf-update-log">{log}</pre>}
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Add `update.css`** with only the new classes:

```css
.pf-update-note {
  color: var(--color-danger, #d66);
  margin-top: 0.5rem;
}

.pf-update-log {
  max-height: 20rem;
  overflow: auto;
  white-space: pre-wrap;
  font-family: var(--font-mono, monospace);
  font-size: 0.85rem;
}
```

(Confirmed against `web-react/src/components/admin/admin.css`: the root grid container is `.pf-admin` (line 7), and `.pf-admin-card`, `.pf-admin-card-title`, `.pf-admin-wide`, `.pf-admin-btn` all exist — reuse them. `pf-admin-grid` does **not** exist; do not use it.)

- [ ] **Step 5: Add the `/update` route** to `web-react/src/components/App.tsx`. It must go **inside the `AppShell` layout route's `children` array** (the same array that holds `/admin`, ~line 98) so the page gets the navbar, timer strip, and the shell's single `useLiveState()` socket subscription. Add beside `/admin`:

```tsx
{ path: "/update", element: <UpdatePage /> },
```

Import at top (named export — matches `AdminPage`/`HistoryPage`/`PelletsPage`, all `import { X }`): `import { UpdatePage } from "./update/UpdatePage";`

- [ ] **Step 6: Run tests + typecheck + the CSS gates.** `cd web-react && bun run test src/components/update/ && bun run typecheck && bun run test src/helpers/cssCoverage.test.ts src/helpers/styleCoverage.test.ts`

- [ ] **Step 7: Format + commit.** `cd web-react && bun run format`, then commit.

---

### Task 5: `UpdatePage` progress panel (the `101`/`142` sentinel)

**Files:**
- Modify: `web-react/src/components/update/UpdatePage.tsx`
- Test: `web-react/src/components/update/UpdatePage.test.tsx` (append)

**Interfaces:**
- Consumes: `fetchUpdateStatus` (Task 3), the `run()` handler (Task 4).
- Produces: a progress panel that, once a mutation returns `started:true`, polls `GET /api/update/status` and renders `percent`/`status`/`output`, treating `percent > 100` as done and `142` as done-with-reboot, then reloads `state`/`check`.

The sentinel contract is verbatim the wizard's — `web-react/src/components/wizard/InstallProgress.tsx:11-14` (`REBOOT_REQUIRED_PERCENT = 142`, `percent > 100` finished).

- [ ] **Step 1: Write the failing tests.** Append to `UpdatePage.test.tsx`. Drive polling with a fake timer or a sequence of resolved statuses.

```tsx
it("polls status after a mutation starts and reports completion", async () => {
  seed({ upgradeDeps: { ok: true, status: 200, message: "", data: { started: true } } });
  let calls = 0;
  (api.fetchUpdateStatus as ReturnType<typeof rs.fn>).mockImplementation(async () => {
    calls += 1;
    return { ok: true, status: 200, message: "", data: { percent: calls < 2 ? 40 : 101, status: "Working", output: "line" } };
  });
  renderPage();
  await screen.findByText(/v1\.8\.0/);
  fireEvent.click(screen.getByRole("button", { name: /upgrade dependencies/i }));
  expect(await screen.findByText(/complete/i)).toBeInTheDocument();
});

it("shows a reboot notice when the run ends at 142", async () => {
  seed({ upgradeDeps: { ok: true, status: 200, message: "", data: { started: true } } });
  (api.fetchUpdateStatus as ReturnType<typeof rs.fn>).mockResolvedValue({
    ok: true, status: 200, message: "", data: { percent: 142, status: "Done", output: "" },
  });
  renderPage();
  await screen.findByText(/v1\.8\.0/);
  fireEvent.click(screen.getByRole("button", { name: /upgrade dependencies/i }));
  expect(await screen.findByText(/reboot/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run, confirm they fail.** `cd web-react && bun run test src/components/update/UpdatePage.test.tsx`

- [ ] **Step 3: Add the progress state + polling.** In `UpdatePage.tsx`, add a `progress` state and a polling effect. On a successful `run()`, set `progress = { percent: 0, ... }` and start polling `fetchUpdateStatus` on an interval; stop when `percent > 100`, record whether it was `142` (reboot), and call `load()` to refresh the header.

```tsx
const REBOOT_REQUIRED_PERCENT = 142; // matches wizard/InstallProgress.tsx and updater.py:548

// add to state:
const [progress, setProgress] = useState<{ percent: number; status: string; output: string } | null>(null);
const [done, setDone] = useState<null | "ok" | "reboot">(null);

// in run(), after a successful mutation instead of setNote("Started."):
setProgress({ percent: 0, status: "Starting…", output: "" });
setDone(null);

// polling effect:
useEffect(() => {
  if (progress === null || done !== null) return;
  const id = setInterval(async () => {
    const r = await fetchUpdateStatus();
    if (!r.ok || !r.data) return;
    setProgress(r.data);
    if (r.data.percent > 100) {
      setDone(r.data.percent === REBOOT_REQUIRED_PERCENT ? "reboot" : "ok");
      void load();
    }
  }, 1000);
  return () => clearInterval(id);
}, [progress, done]);
```

Render, below the actions:

```tsx
{progress && (
  <section className="pf-admin-card pf-admin-wide" aria-label="update progress">
    <div className="pf-update-progress" style={{ width: `${Math.min(progress.percent, 100)}%` }} />
    <p>{progress.status}</p>
    <pre className="pf-update-log">{progress.output}</pre>
    {done === "ok" && <p>Update complete.</p>}
    {done === "reboot" && <p>Update complete — reboot required.</p>}
  </section>
)}
```

Add `.pf-update-progress` to `update.css` (a filled bar; height + background). Because the polling effect calls `setProgress` from inside `setInterval` (an event, not render), it does not trip the React-Compiler no-setState-in-effect rule — the effect only *starts* the interval.

- [ ] **Step 4: Run, confirm pass.** `cd web-react && bun run test src/components/update/UpdatePage.test.tsx && bun run typecheck && bun run lint`

- [ ] **Step 5: Format + commit.**

---

### Task 6: `SystemUpdateCard` on the Admin page

**Files:**
- Create: `web-react/src/components/admin/SystemUpdateCard.tsx`
- Test: `web-react/src/components/admin/SystemUpdateCard.test.tsx`
- Modify: `web-react/src/components/admin/AdminPage.tsx` (render the card)

**Interfaces:**
- Consumes: `fetchUpdateCheck` (Task 3); `react-router`'s `Link`.
- Produces: a card showing the current version + behind-count with a `<Link to="/update">`. It takes **no props** — `AdminState` carries no server version (`AdminSettings` is `{debug_mode, boot_to_monitor}`; `SystemInfo` has no version either, both verified in `adminTypes.ts`), so the card reads the version from `GET /api/update/check`'s `current`, which it fetches anyway for the behind-count.

- [ ] **Step 1: Write the failing test.** `SystemUpdateCard.test.tsx`:

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import * as api from "../../helpers/update/updateApi";
import { SystemUpdateCard } from "./SystemUpdateCard";

rs.mock("../../helpers/update/updateApi", () => ({ fetchUpdateCheck: rs.fn() }));
afterEach(cleanup);

describe("SystemUpdateCard", () => {
  it("shows the version and behind-count with a link to /update", async () => {
    (api.fetchUpdateCheck as ReturnType<typeof rs.fn>).mockResolvedValue({
      ok: true, status: 200, message: "", data: { current: "v1.8.0", behind: 2 },
    });
    render(
      <MemoryRouter>
        <SystemUpdateCard />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/v1\.8\.0/)).toBeInTheDocument();
    expect(await screen.findByText(/2 commits behind/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /updater/i })).toHaveAttribute("href", "/update");
  });
});
```

- [ ] **Step 2: Run, confirm it fails.** `cd web-react && bun run test src/components/admin/SystemUpdateCard.test.tsx`

- [ ] **Step 3: Implement `SystemUpdateCard.tsx`:**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { fetchUpdateCheck } from "../../helpers/update/updateApi";
import type { UpdateCheck } from "../../helpers/update/updateTypes";

export function SystemUpdateCard() {
  const [check, setCheck] = useState<UpdateCheck | null>(null);

  useEffect(() => {
    void fetchUpdateCheck().then((r) => setCheck(r.ok ? r.data : null));
  }, []);

  const behindText =
    check === null
      ? "Update status unavailable"
      : check.behind > 0
        ? `${check.behind} commits behind`
        : "Up to date";

  return (
    <section className="pf-admin-card" aria-labelledby="admin-system-update">
      <h3 id="admin-system-update">System Update</h3>
      <p>Current version: {check?.current ?? "unknown"}</p>
      <p>{behindText}</p>
      <Link to="/update" className="pf-admin-btn">
        Open Updater
      </Link>
    </section>
  );
}
```

- [ ] **Step 4: Wire it into `AdminPage.tsx`.** Import it (`import { SystemUpdateCard } from "./SystemUpdateCard";`) and render it beside the other cards, after `<SystemCard .../>` in the render tail (~line 188). No props:

```tsx
<SystemUpdateCard />
```

- [ ] **Step 5: Run tests + typecheck.** `cd web-react && bun run test src/components/admin/ && bun run typecheck`. Update `AdminPage.test.tsx` only if the new card breaks an existing assertion (e.g. a card-count) — adjust the harness, never weaken an assertion.

- [ ] **Step 6: Format + commit.**

---

### Task 7: e2e + full gate

**Files:**
- Create: `web-react/tests/e2e/update.spec.ts`

- [ ] **Step 1: Write the e2e** with all `/api/update/*` routes mocked (like `wled-editor.spec.ts` route-mocks its endpoints). It must NEVER hit a real update — every mutation route returns a canned `{started:true}`, and status returns a short sequence ending at `101`.

```ts
import { expect, test } from "@playwright/test";

test.describe("updater", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/update/state", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          result: "OK",
          data: { version: "v1.8.0", branch: "main", branches: ["main", "dev"], remote_url: "u", remote_version: "v1.8.1" },
        }),
      }),
    );
    await page.route("**/api/update/check", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ result: "OK", data: { current: "v1.8.0", behind: 3 } }) }),
    );
    await page.route("**/api/update/log*", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ result: "OK", data: { output: "abc123 fix" } }) }),
    );
    await page.route("**/api/update/upgrade", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ result: "OK", data: { started: true } }) }),
    );
    await page.route("**/api/update/status", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ result: "OK", data: { percent: 101, status: "Done", output: "ok" } }) }),
    );
  });

  test("shows state, runs upgrade, polls to completion", async ({ page }) => {
    await page.goto("/update");
    await expect(page.getByText(/3 commits behind/i)).toBeVisible();
    await page.getByRole("button", { name: /upgrade dependencies/i }).click();
    await expect(page.getByText(/complete/i)).toBeVisible();
  });
});
```

Note in a comment: unlike other specs this one is fully route-mocked and needs no live backend, because a real updater run mutates the machine.

- [ ] **Step 2: Run the e2e.** `cd web-react && bun run test:e2e update.spec.ts` (in the main checkout — chromium specs skip in agent worktrees).

- [ ] **Step 3: Full React gate.** `cd web-react && bun run typecheck && bun run lint && bun run test && bun run build`

- [ ] **Step 4: Full Python check.** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_update.py tests/web/test_page_update.py -q` and confirm the repo-root artifacts (`os_info.json`/`settings.json`/`pelletdb.json`) are absent.

- [ ] **Step 5: Update the backlog** — mark the updater page SHIPPED in `docs/superpowers/react-migration-backlog.md` (the `[ ] update` checklist line and the Pages-and-chrome section), and commit.

---

## Self-Review

**Spec coverage:** REST surface (4 reads + 4 mutations) → T1/T2; `is_real_hardware` gate + `_fire` seam → T2; STOP-mode 409 → T2; branch allowlist 400 → T2; `commits` 400 → T1; typed client → T3; `/update` page (status/branch/actions/log) → T4; progress polling with `101`/`142` sentinel → T5; Admin card → T6; e2e + gate → T7; `blueprints/update` + `test_page_update.py` untouched → T2 Step 5; "what's new" modal out of scope → not built (backlog). Every spec section maps to a task.

**Placeholder scan:** none — every code step carries real code; the two "confirm the exact class name / version path against the file" notes (T4 Step 4, T6 Step 4) are verification instructions, not deferred work.

**Type consistency:** `UpdateState`/`UpdateCheck`/`UpdateStatus`/`UpdateStarted`/`UpdateResult<T>` defined in T3, consumed unchanged in T4/T5/T6. Endpoint paths (`state`/`check`/`log`/`status`/`branches/refresh`/`branch`/`pull`/`upgrade`) identical between the Flask routes (T1/T2) and the client (T3). Command strings (`updater.py -r/-b/-u -p/-i`) match Flask's `blueprints/update/routes.py` verbatim. `_fire`/`_ok`/`_error` defined in T1, used in T2.

**Resolved during planning (via serena/symbol lookup, not grep):**
- Pages use **named** exports (`import { AdminPage }`), so `UpdatePage` and `SystemUpdateCard` are named exports, not `export default`.
- The AdminPage root grid class is **`.pf-admin`** (`admin.css:7`); `pf-admin-grid` does not exist. `pf-admin-card`/`pf-admin-card-title`/`pf-admin-wide`/`pf-admin-btn` do.
- **`AdminState` carries no server version** — `AdminSettings` is `{debug_mode, boot_to_monitor}` and `SystemInfo` has none — so `SystemUpdateCard` takes no `version` prop and reads `current` from `GET /api/update/check` instead. No admin-state path is assumed.
