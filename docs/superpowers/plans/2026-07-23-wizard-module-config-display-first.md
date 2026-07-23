# Wizard Module-Config Surface (React), Display-First — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a React wizard shell (welcome → grillplatform → probes → display → distance → finish) with the **display step fully real** and the other three navigable placeholders, backed by new `/api/wizard/*` JSON endpoints that reuse the existing staged-installer, plus fixing the display double-default manifest bug.

**Architecture:** A new sibling Flask blueprint (`/api/wizard/*`, per-route decorators) exposes JSON over the existing wizard-install-info blob + discovery + finish/status functions. The React app holds working state client-side (faithful to Flask), flushes drafts on step transitions (a deliberate improvement over Flask), and submits everything in one Finish POST that fires the existing detached installer, then polls install status. A shared `<ModuleCard>` + field-widget registry + `DiscoveryPanel` + `ConfigOptionField` + `InstallProgress` form the reusable spine that grillplatform/probes/distance will later extend.

**Tech Stack:** Python 3.14 / Flask (blueprints), pydantic settings schema; React 19 + TypeScript 7 (tsgo), react-router (data router), rsbuild, rstest (istanbul coverage), Biome, Playwright. jj (jujutsu) for VCS.

**Spec:** `docs/superpowers/specs/2026-07-23-wizard-module-config-display-first.md`
**Inventories:** `.superpowers/sdd/wizard-family-inventory.md`, `.superpowers/sdd/probeconfig-inventory.md`
**Conventions reference:** `.superpowers/sdd/wizard-conventions.md`

## Global Constraints

- **VCS is jj, NOT git.** No staging area; jj auto-snapshots. Never run `git` write commands. jj does NOT run the prek hook, so run formatters MANUALLY before every commit: Python → `uvx ruff format <files>`; web-react → `bun run format` (biome). Commit protocol per task: edit → tests → format → `jj --no-pager diff --git` (verify only intended files) → `jj commit -m "msg"` → capture `jj --no-pager show @- --stat`. Commit messages imperative, sentence case, no trailing period. Use `--no-pager` on all jj commands.
- **Python tests run via uv with offscreen Qt:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <path> -q`. Bare `python` gives false failures.
- **web-react uses bun, not npm.** `bun install`, `bun run typecheck` (tsgo), `bun run lint` (biome+eslint), `bun run test` (rstest), `bun run test:coverage`, `bun run build`. Commit `bun.lock` if it changes.
- **Coverage: 75% per-file lines is ENFORCED** (`rstest.config.ts` thresholds `src/**/*.{ts,tsx}` lines 75 perFile). New components must meet it; primitives aim high.
- **React API idiom:** `const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";` + bare `fetch()`. New API module `wizardApi.ts` mirrors `helpers/settings/settingsApi.ts` (thin `buildUrl(baseUrl, path)` + `fetch` returning a typed `{ok, ...}` result).
- **React test idiom:** `rs.mock("<module>", () => ({...}))` + `afterEach(cleanup)`; outlet-context components use `renderRoute(ui, context)` from `src/test-utils.tsx`. `.test.ts` = node env, `.test.tsx` = jsdom env.
- **Flask JSON-endpoint idiom (new blueprint):** per-route `@api_wizard_bp.route("/state", methods=["GET"])` returning `jsonify({...}), status`. Response convention: success → HTTP 200 with the payload; error → `jsonify({"result": "error", "message": ...}), <4xx>`. Reads: `read_settings`, `read_control` from `common.datastore_accessors`; `read_wizard` from `common.common`.
- **os.system neutralization in tests (footgun):** the finish endpoint fires `os.system(... &)`. Endpoint tests MUST patch `os.system` on the **endpoint module's own attribute** (`blueprints.api_wizard.routes.os.system` or the exact reference used) and MUST NOT relocate the installer-invoking call out of the patched module. A `real_hw=False` flag is NOT sufficient. Grep for `os.system`/`subprocess`/`reboot` before running any endpoint test unguarded.
- **Latent-bug policy:** the manifest fix (Task 1) is a real behavior fix; if a `tests/web/` characterization test pins the buggy behavior, update that pin in the same commit and note it.

## Shared API contract (both backend and React tasks depend on this — exact shapes)

`GET /api/wizard/state` → 200:
```json
{
  "modules_metadata": { "grillplatform": {"<mod>": {ModuleData}}, "display": {...}, "distance": {...}, "probes": {...} },
  "selections":        { "grillplatform": "<mod>", "display": "<mod>", "distance": "<mod>", "probes": "<mod-or-empty>" },
  "settings_dep_values": { "grillplatform": {"<dep_key>": "<str|null>"}, "display": {...}, "distance": {...} },
  "display_config":    { "<display_module>": { "<option_name>": <value> } },
  "control_mode":      "Stop",
  "first_time_setup":  true,
  "has_draft":         false
}
```
- `ModuleData` = `{ friendly_name, description?, notes?, image?, settings_dependencies: {<key>: {friendly_name, description?, type?, options?, hidden?, settings: string[]}}, config?: [{option_name, option_friendly_name, option_description?, option_type: "list"|"string", list_values?, list_labels?, default?, hidden?}] }`.
- `state` reads the **draft blob if present** (resume) else computes from settings/defaults (see Task 2). `has_draft` tells the client whether it resumed.

`POST /api/wizard/draft` body `{selections, settings_dep_values, display_config}` → 200 `{"result":"success"}` — persists the client working-state into the wizard blob (draft).

`POST /api/wizard/scan` body `{kind, ...extra}` → 200 `{groups:[{title, items:[{value,label}]}], error: string|null}`.

`POST /api/wizard/finish` body `{selections, settings_dep_values, display_config}` → 200 `{"result":"success"}` (installer fired) · 409 `{"result":"error","message":"system_active"}` if `control.mode != "Stop"` · 422 `{"result":"error","message":"bus_conflict", "detail": ...}` on bus-kind validation failure.

`GET /api/wizard/installstatus` → 200 `{percent, status, output}`.

### Shared TypeScript types (defined in Task 5 `wizardTypes.ts`, used verbatim by all React tasks)
```ts
export type WizardSection = "grillplatform" | "display" | "distance" | "probes";
export interface SettingsDependency {
  friendly_name: string; description?: string;
  type?: "i2c_bus_num" | "usb_serial_device";
  options?: Record<string, string>; hidden?: boolean; settings: string[];
}
export interface ConfigOption {
  option_name: string; option_friendly_name: string; option_description?: string;
  option_type: "list" | "string";
  list_values?: unknown[]; list_labels?: string[]; default?: unknown; hidden?: boolean;
}
export interface WizardModuleData {
  friendly_name: string; description?: string; notes?: string; image?: string;
  settings_dependencies: Record<string, SettingsDependency>;
  config?: ConfigOption[];
}
export interface WizardState {
  modules_metadata: Record<WizardSection, Record<string, WizardModuleData>>;
  selections: Record<WizardSection, string>;
  settings_dep_values: Record<WizardSection, Record<string, string | null>>;
  display_config: Record<string, Record<string, unknown>>;
  control_mode: string; first_time_setup: boolean; has_draft: boolean;
}
export interface ScanGroup { title: string; items: { value: string; label: string }[]; }
export interface ScanResult { groups: ScanGroup[]; error: string | null; }
export interface InstallStatus { percent: number; status: string; output: string; }
// Client working state (mutable subset submitted at draft/finish):
export interface WizardWorking {
  selections: Record<WizardSection, string>;
  settings_dep_values: Record<WizardSection, Record<string, string | null>>;
  display_config: Record<string, Record<string, unknown>>;
}
```

---

## Task 1: Fix display double-default manifest bug (Flask, standalone)

**Files:**
- Modify: `wizard/wizard_manifest.json` (the `modules.display.ili9488b` entry — remove its `"default": true`)
- Test: `tests/web/test_wizard_install_info_defaults.py` (may need a pin update)

**Interfaces:**
- Produces: a manifest where `modules.display` has exactly one `default:true` (`ili9341b`). No code signature change.

- [ ] **Step 1: Write a failing test asserting a single display default**

Add to `tests/web/test_wizard_install_info_defaults.py`:
```python
def test_display_has_single_default_module():
    """Regression: the manifest must mark exactly one display module default
    (was two — ili9341b and ili9488b — causing dropdown/config disagreement)."""
    from common.common import read_wizard
    display = read_wizard()["modules"]["display"]
    defaults = [name for name, entry in display.items()
                if isinstance(entry, dict) and entry.get("default") is True]
    assert defaults == ["ili9341b"], f"expected single default ili9341b, got {defaults}"
```

- [ ] **Step 2: Run it, verify it FAILS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_wizard_install_info_defaults.py::test_display_has_single_default_module -q`
Expected: FAIL — `got ['ili9341b', 'ili9488b']`.

- [ ] **Step 3: Fix the manifest**

In `wizard/wizard_manifest.json`, locate `modules.display.ili9488b` and remove its `"default": true` key (delete the whole key/line; do not set it to `false` — match how non-default modules omit the key). Leave `ili9341b`'s `"default": true` intact.

- [ ] **Step 4: Run the new test + the existing defaults tests**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_wizard_install_info_defaults.py -q`
Expected: PASS. If a pre-existing test in that file now fails because it pinned `ili9488b`'s config being populated as the default, that is the manifest bug's characterization — update that test's expectation to `ili9341b`'s config and add a one-line comment noting the manifest fix. Do NOT loosen the new assertion.

- [ ] **Step 5: Broader wizard test sweep (guard against other pins)**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/ -k wizard -q`
Expected: all pass (update any that pinned the double-default, per Step 4 policy).

- [ ] **Step 6: Commit**

```bash
uvx ruff format tests/web/test_wizard_install_info_defaults.py
jj --no-pager diff --git   # verify only manifest + that test changed
jj commit -m "fix(wizard-manifest): mark only ili9341b as default display (drop duplicate ili9488b default)"
jj --no-pager show @- --stat
```

---

## Task 2: Backend — `GET /api/wizard/state` + `POST /api/wizard/draft`

**Files:**
- Create: `blueprints/api_wizard/__init__.py`, `blueprints/api_wizard/routes.py`
- Modify: `app.py` (register the blueprint)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: `read_settings`, `read_control` (`common.datastore_accessors`); `read_wizard` (`common.common`); `load_wizard_install_info`, `store_wizard_install_info` (`common.datastore_accessors`); the existing `wizardInstallInfoDefaults`/`wizardInstallInfoExisting` (`blueprints/wizard/wizard.py`) and `get_settings_dependencies_values` for computing dep values.
- Produces: `api_wizard_bp` (Blueprint, `url_prefix="/api/wizard"`); `GET /state`, `POST /draft` per the Shared API contract. `_build_state(settings, control)` and `_draft_key` helpers used by later tasks' tests.

- [ ] **Step 1: Register an empty blueprint (make it importable)**

Create `blueprints/api_wizard/__init__.py`:
```python
from flask import Blueprint

api_wizard_bp = Blueprint("api_wizard_bp", __name__, url_prefix="/api/wizard")

from . import routes  # noqa: E402,F401
```
Create `blueprints/api_wizard/routes.py`:
```python
from flask import jsonify, request

from blueprints.wizard.wizard import (
    get_settings_dependencies_values,
    wizardInstallInfoDefaults,
    wizardInstallInfoExisting,
)
from common.common import read_wizard
from common.datastore_accessors import (
    load_wizard_install_info,
    read_control,
    read_settings,
    store_wizard_install_info,
)

from . import api_wizard_bp

_SECTIONS = ["grillplatform", "display", "distance", "probes"]
```
In `app.py`, register alongside the other blueprints (mirror the `api_bp`/`wizard_bp` registration lines):
```python
from blueprints.api_wizard import api_wizard_bp
app.register_blueprint(api_wizard_bp)
```

- [ ] **Step 2: Write the failing test for `/state` (fresh, no draft)**

Create `tests/web/test_api_wizard.py` (mirror `tests/web/test_api_settings_update.py`'s `client(ds)` fixture):
```python
import json
import pytest
from app import app as flask_app
from common.datastore_accessors import read_settings


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_state_fresh_returns_metadata_and_selections(ds, client):
    resp = client.get("/api/wizard/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["modules_metadata"].keys()) >= {"grillplatform", "display", "distance"}
    assert "display" in body["selections"]
    assert body["has_draft"] is False
    # display_config is a dict keyed by module name (may be empty on fresh)
    assert isinstance(body["display_config"], dict)
    assert "control_mode" in body and "first_time_setup" in body
```

- [ ] **Step 3: Run it, verify it FAILS (404 — route not defined)**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_state_fresh_returns_metadata_and_selections -q`
Expected: FAIL (404 / KeyError).

- [ ] **Step 4: Implement `_build_state` + the `/state` route**

Append to `blueprints/api_wizard/routes.py`:
```python
_DRAFT_KEY = "react_draft"  # marker key inside the wizard blob


def _build_state(settings, control):
    wizard_data = read_wizard()
    modules = wizard_data.get("modules", {})

    draft = load_wizard_install_info()
    has_draft = isinstance(draft, dict) and draft.get(_DRAFT_KEY) is True

    if has_draft:
        selections = draft.get("selections", {})
        settings_dep_values = draft.get("settings_dep_values", {})
        display_config = draft.get("display_config", {})
    else:
        # Compute from current settings/defaults (do NOT overwrite the blob here).
        if settings["globals"]["first_time_setup"]:
            info = wizardInstallInfoDefaults(wizard_data, settings)
        else:
            info = wizardInstallInfoExisting(wizard_data, settings)
        selections = {s: info["modules"].get(s, {}).get("profile_selected", [""])[0]
                      if isinstance(info["modules"].get(s, {}).get("profile_selected"), list)
                      else info["modules"].get(s, {}).get("profile_selected", "")
                      for s in _SECTIONS if s in modules}
        settings_dep_values = {}
        for section in _SECTIONS:
            if section not in modules:
                continue
            sel = selections.get(section)
            mod_data = modules.get(section, {}).get(sel)
            settings_dep_values[section] = (
                get_settings_dependencies_values(settings, mod_data) if mod_data else {}
            )
        display_config = settings.get("display", {}).get("config", {})

    return {
        "modules_metadata": {s: modules.get(s, {}) for s in _SECTIONS if s in modules},
        "selections": selections,
        "settings_dep_values": settings_dep_values,
        "display_config": display_config,
        "control_mode": control.get("mode", "Stop"),
        "first_time_setup": bool(settings["globals"]["first_time_setup"]),
        "has_draft": has_draft,
    }


@api_wizard_bp.route("/state", methods=["GET"])
def wizard_state():
    settings = read_settings()
    control = read_control()
    return jsonify(_build_state(settings, control)), 200
```
NOTE for the implementer: the exact `profile_selected` shape (list vs scalar) must be verified against `blueprints/wizard/wizard.py` `wizardInstallInfoDefaults`/`wizardInstallInfoExisting` — read those functions and adapt the `selections` extraction to their real output. Keep the "take `[0]` for both identity and config" behavior (matches Task 1's single-default manifest). If the real shape differs from the guarded expression above, simplify it to match — do not keep defensive branches that don't correspond to real shapes.

- [ ] **Step 5: Run `/state` test → PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_state_fresh_returns_metadata_and_selections -q`
Expected: PASS.

- [ ] **Step 6: Write the failing test for `/draft` round-trip (draft resumes)**

Add to `tests/web/test_api_wizard.py`:
```python
def test_draft_persists_and_state_resumes(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {"display": {}},
        "display_config": {"ili9341b": {"rotation": 90}},
    }
    r1 = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r1.status_code == 200 and r1.get_json()["result"] == "success"

    r2 = client.get("/api/wizard/state")
    body = r2.get_json()
    assert body["has_draft"] is True
    assert body["selections"]["display"] == "ili9341b"
    assert body["display_config"]["ili9341b"]["rotation"] == 90
```

- [ ] **Step 7: Run it, verify it FAILS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_draft_persists_and_state_resumes -q`
Expected: FAIL (404 on `/draft`).

- [ ] **Step 8: Implement `/draft`**

Append to `routes.py`:
```python
@api_wizard_bp.route("/draft", methods=["POST"])
def wizard_draft():
    payload = request.get_json(silent=True) or {}
    info = load_wizard_install_info()
    if not isinstance(info, dict):
        info = {}
    info[_DRAFT_KEY] = True
    info["selections"] = payload.get("selections", {})
    info["settings_dep_values"] = payload.get("settings_dep_values", {})
    info["display_config"] = payload.get("display_config", {})
    store_wizard_install_info(info)
    return jsonify({"result": "success"}), 200
```

- [ ] **Step 9: Run both `/state` and `/draft` tests → PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
uvx ruff format blueprints/api_wizard/routes.py blueprints/api_wizard/__init__.py tests/web/test_api_wizard.py
jj --no-pager diff --git
jj commit -m "feat(api-wizard): add /api/wizard/state and /draft (resume-from-draft)"
jj --no-pager show @- --stat
```

---

## Task 3: Backend — `POST /api/wizard/scan` (discovery delegation)

**Files:**
- Modify: `blueprints/api_wizard/routes.py`
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: the discovery functions the legacy wizard uses — read `blueprints/wizard/routes.py` `_wizard_i2c_bus_scan`/`_wizard_usb_serial_scan` to find the exact functions (`discover_extended_i2c_buses`, `discover_mcp2221_devices`, `discover_ft232h_devices` from `common.i2c_bus`; `discover_usb_serial_devices` from `common.usb_serial`) and their return shapes.
- Produces: `POST /scan` returning `{groups, error}` per contract.

- [ ] **Step 1: Write the failing test (i2c extended scan shape, functions mocked)**

Add to `tests/web/test_api_wizard.py`:
```python
def test_scan_extended_i2c_returns_groups(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    monkeypatch.setattr(
        wr, "discover_extended_i2c_buses",
        lambda *a, **k: [{"bus_num": 1, "name": "i2c-1", "serial": "ABC"}],
    )
    resp = client.post("/api/wizard/scan",
                       data=json.dumps({"kind": "extended"}),
                       content_type="application/json")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is None
    assert isinstance(body["groups"], list) and body["groups"]
    assert body["groups"][0]["items"][0]["value"]
```

- [ ] **Step 2: Run, verify FAIL (404)**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_scan_extended_i2c_returns_groups -q`
Expected: FAIL.

- [ ] **Step 3: Implement `/scan`**

First READ `blueprints/wizard/routes.py:176-262` to copy the exact grouping logic (`groups=[{title, items}]`) for each kind (`extended` → "By Bus Number"/"By Serial"; `mcp2221` → serial; `ft232h` → url/description; `usb_serial` → `discover_usb_serial_devices(vid, pid)`). Then append to `routes.py`, importing the same discovery functions the legacy handler imports:
```python
from common.i2c_bus import (
    discover_extended_i2c_buses,
    discover_ft232h_devices,
    discover_mcp2221_devices,
)
from common.usb_serial import discover_usb_serial_devices


@api_wizard_bp.route("/scan", methods=["POST"])
def wizard_scan():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    groups = []
    error = None
    try:
        if kind == "extended":
            adapters = discover_extended_i2c_buses()
            groups = [
                {"title": "By Bus Number",
                 "items": [{"value": str(a["bus_num"]), "label": f'{a["name"]} (bus {a["bus_num"]})'} for a in adapters]},
                {"title": "By Serial",
                 "items": [{"value": a["serial"], "label": f'{a["name"]} [{a["serial"]}]'} for a in adapters if a.get("serial")]},
            ]
        elif kind == "mcp2221":
            devs = discover_mcp2221_devices()
            groups = [{"title": "MCP2221 Devices",
                       "items": [{"value": d["serial"], "label": d["serial"]} for d in devs]}]
        elif kind == "ft232h":
            devs = discover_ft232h_devices()
            groups = [{"title": "FT232H Devices",
                       "items": [{"value": d["url"], "label": d.get("description", d["url"])} for d in devs]}]
        elif kind == "usb_serial":
            devs = discover_usb_serial_devices(payload.get("vid"), payload.get("pid"))
            groups = [{"title": "USB Serial Devices",
                       "items": [{"value": d["device"], "label": d.get("description", d["device"])} for d in devs]}]
        else:
            error = f"Unknown scan kind: {kind}"
        if not error and not any(g["items"] for g in groups):
            error = "No devices found."
    except Exception as e:  # discovery hits hardware libs; surface failures as a friendly error
        error = f"Scan failed: {e}"
        groups = []
    return jsonify({"groups": groups, "error": error}), 200
```
IMPLEMENTER: reconcile the exact adapter/device dict keys (`bus_num`/`name`/`serial`/`url`/`description`/`device`) against the real `discover_*` return values in `common/i2c_bus.py` and `common/usb_serial.py` — adjust the field reads to match; keep the `{groups, error}` output shape fixed.

- [ ] **Step 4: Run scan test → PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_scan_extended_i2c_returns_groups -q`
Expected: PASS.

- [ ] **Step 5: Add a no-results test**

```python
def test_scan_no_results_returns_friendly_error(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    monkeypatch.setattr(wr, "discover_extended_i2c_buses", lambda *a, **k: [])
    resp = client.post("/api/wizard/scan", data=json.dumps({"kind": "extended"}),
                       content_type="application/json")
    body = resp.get_json()
    assert body["error"] == "No devices found."
```
Run the whole file: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py
jj --no-pager diff --git
jj commit -m "feat(api-wizard): add /api/wizard/scan discovery delegation"
jj --no-pager show @- --stat
```

---

## Task 4: Backend — `POST /api/wizard/finish` + `GET /api/wizard/installstatus`

**Files:**
- Modify: `blueprints/api_wizard/routes.py`
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: `store_wizard_install_info`, `set_wizard_install_status`, `get_wizard_install_status` (`common.datastore_accessors`); `validate_bus_kinds`, `wizard_bus_kinds` (read `blueprints/wizard/routes.py` `_wizard_finish` + `blueprints/wizard/wizard.py` for the exact validation call); `os` (for `os.system`), `read_control`, `read_settings`, `read_wizard`.
- Produces: `POST /finish` (409 non-STOP, 422 bus-conflict, 200 fired) + `GET /installstatus`.

- [ ] **Step 1: Write failing test — finish blocked when system active (409)**

Add to `tests/web/test_api_wizard.py`:
```python
def test_finish_blocked_when_not_stopped(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))  # neutralize installer
    from common.datastore_accessors import read_control, write_control
    ctrl = read_control(); ctrl["mode"] = "Hold"; write_control(ctrl)
    resp = client.post("/api/wizard/finish",
                       data=json.dumps({"selections": {}, "settings_dep_values": {}, "display_config": {}}),
                       content_type="application/json")
    assert resp.status_code == 409
    assert resp.get_json()["message"] == "system_active"
    assert fired == []  # installer must NOT fire
```

- [ ] **Step 2: Run, verify FAIL (404)**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_finish_blocked_when_not_stopped -q`
Expected: FAIL.

- [ ] **Step 3: Implement `/finish` + `/installstatus`**

READ `blueprints/wizard/routes.py:77-102` (`_wizard_finish`) for the exact STOP check (`control["mode"] == Mode.STOP` — import the same `Mode`), the bus-kind validation call, and the `os.system(f"{python_exec} wizard.py &")` invocation, then append to `routes.py`:
```python
import os
from common.datastore_accessors import get_wizard_install_status, set_wizard_install_status
# import Mode + validate_bus_kinds/wizard_bus_kinds from the same places _wizard_finish does


@api_wizard_bp.route("/finish", methods=["POST"])
def wizard_finish():
    settings = read_settings()
    control = read_control()
    if control.get("mode") != "Stop":   # use the same Mode.STOP constant/string _wizard_finish uses
        return jsonify({"result": "error", "message": "system_active"}), 409

    payload = request.get_json(silent=True) or {}
    info = load_wizard_install_info()
    if not isinstance(info, dict):
        info = {}
    info[_DRAFT_KEY] = True
    info["selections"] = payload.get("selections", {})
    info["settings_dep_values"] = payload.get("settings_dep_values", {})
    info["display_config"] = payload.get("display_config", {})

    # Bus-kind validation across all sections (mirror _wizard_finish's validate_bus_kinds(wizard_bus_kinds(...)))
    try:
        bus_kinds = wizard_bus_kinds(settings, info)   # adapt to the real signature read from wizard.py
        validate_bus_kinds(bus_kinds)
    except Exception as e:
        return jsonify({"result": "error", "message": "bus_conflict", "detail": str(e)}), 422

    store_wizard_install_info(info)
    set_wizard_install_status(0, "Starting Install...", "")
    python_exec = settings["globals"].get("python_exec", "python")
    os.system(f"{python_exec} wizard.py &")
    return jsonify({"result": "success"}), 200


@api_wizard_bp.route("/installstatus", methods=["GET"])
def wizard_installstatus():
    percent, status, output = get_wizard_install_status()
    return jsonify({"percent": percent, "status": status, "output": output}), 200
```
IMPLEMENTER: the `wizard_bus_kinds(settings, info)` call must match the real signature/inputs used by `_wizard_finish` (it assembles bus kinds from the whole in-progress selection — probe devices + grillplatform/distance `i2c_bus_kind` deps). Read `blueprints/wizard/wizard.py` `wizard_bus_kinds` and adapt the `info` shape you pass so it sees the staged selections/deps. If the legacy helper needs the full `wizardInstallInfo` module shape rather than the React draft shape, translate the draft into that shape before calling — do not skip the validation.

- [ ] **Step 4: Run 409 test → PASS**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_finish_blocked_when_not_stopped -q`
Expected: PASS.

- [ ] **Step 5: Test the happy path fires the installer (neutralized) + installstatus**

```python
def test_finish_fires_installer_when_stopped(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    monkeypatch.setattr(wr, "wizard_bus_kinds", lambda *a, **k: {})
    monkeypatch.setattr(wr, "validate_bus_kinds", lambda *a, **k: None)
    # control defaults to Stop in a fresh ds
    resp = client.post("/api/wizard/finish",
                       data=json.dumps({"selections": {"display": "ili9341b"},
                                        "settings_dep_values": {}, "display_config": {}}),
                       content_type="application/json")
    assert resp.status_code == 200 and resp.get_json()["result"] == "success"
    assert fired and "wizard.py" in fired[0]

    st = client.get("/api/wizard/installstatus").get_json()
    assert st["percent"] == 0 and "Starting Install" in st["status"]
```
Confirm the fresh `ds` control mode is `"Stop"`; if not, set it explicitly like the 409 test does (inverted).

- [ ] **Step 6: Full-file run + os.system safety grep**

Run: `grep -rn "os.system\|subprocess\|reboot\|shutdown" blueprints/api_wizard/` — confirm the ONLY `os.system` is the neutralized-in-test finish call.
Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -q`
Expected: all PASS, and no real installer ran (every test patches `wr.os.system`).

- [ ] **Step 7: Commit**

```bash
uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py
jj --no-pager diff --git
jj commit -m "feat(api-wizard): add /finish (staged installer, STOP+bus-kind gated) and /installstatus"
jj --no-pager show @- --stat
```

---

## Task 5: React — wizard types + API client

**Files:**
- Create: `web-react/src/helpers/wizard/wizardTypes.ts`
- Create: `web-react/src/helpers/wizard/wizardApi.ts`
- Test: `web-react/src/helpers/wizard/wizardApi.test.ts`

**Interfaces:**
- Consumes: the Shared API contract + Shared TypeScript types above (put them verbatim in `wizardTypes.ts`).
- Produces: `getWizardState(baseUrl): Promise<WizardState>`, `saveDraft(baseUrl, working: WizardWorking): Promise<boolean>`, `scan(baseUrl, body): Promise<ScanResult>`, `finishWizard(baseUrl, working): Promise<{ok: boolean; status: number; message?: string}>`, `getInstallStatus(baseUrl): Promise<InstallStatus>`.

- [ ] **Step 1: Write `wizardTypes.ts`** — paste the "Shared TypeScript types" block from the Global section verbatim as the file contents (add `export {}` guards as needed). No test needed for pure types.

- [ ] **Step 2: Write the failing API-client test**

Create `web-react/src/helpers/wizard/wizardApi.test.ts` (`.ts` → node env):
```ts
import { afterEach, describe, expect, test, rs } from "@rstest/core";
import type { WizardState } from "./wizardTypes";

afterEach(() => { rs.resetAllMocks(); });

describe("wizardApi", () => {
  test("getWizardState fetches /api/wizard/state and returns parsed JSON", async () => {
    const fake: Partial<WizardState> = { has_draft: false, control_mode: "Stop" };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { getWizardState } = await import("./wizardApi");
    const state = await getWizardState("");
    expect(state.control_mode).toBe("Stop");
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain("/api/wizard/state");
  });

  test("finishWizard surfaces 409 as ok:false with status", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({
      ok: false, status: 409, json: async () => ({ result: "error", message: "system_active" }),
    }) as never;
    const { finishWizard } = await import("./wizardApi");
    const r = await finishWizard("", { selections: {}, settings_dep_values: {}, display_config: {} } as never);
    expect(r.ok).toBe(false);
    expect(r.status).toBe(409);
    expect(r.message).toBe("system_active");
  });
});
```

- [ ] **Step 3: Run, verify FAIL** — Run: `cd web-react && bun run test src/helpers/wizard/wizardApi.test.ts` → FAIL (module missing).

- [ ] **Step 4: Implement `wizardApi.ts`** (mirror `helpers/settings/settingsApi.ts`):
```ts
import type { InstallStatus, ScanResult, WizardState, WizardWorking } from "./wizardTypes";

function url(baseUrl: string, path: string): string {
  return `${baseUrl}/api/wizard/${path}`;
}

export async function getWizardState(baseUrl: string): Promise<WizardState> {
  const r = await fetch(url(baseUrl, "state"));
  return (await r.json()) as WizardState;
}

export async function saveDraft(baseUrl: string, working: WizardWorking): Promise<boolean> {
  const r = await fetch(url(baseUrl, "draft"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(working),
  });
  return r.ok;
}

export async function scan(baseUrl: string, body: { kind: string; vid?: number; pid?: number }): Promise<ScanResult> {
  const r = await fetch(url(baseUrl, "scan"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return (await r.json()) as ScanResult;
}

export async function finishWizard(
  baseUrl: string, working: WizardWorking,
): Promise<{ ok: boolean; status: number; message?: string }> {
  const r = await fetch(url(baseUrl, "finish"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(working),
  });
  const body = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, message: body?.message };
}

export async function getInstallStatus(baseUrl: string): Promise<InstallStatus> {
  const r = await fetch(url(baseUrl, "installstatus"));
  return (await r.json()) as InstallStatus;
}
```

- [ ] **Step 5: Run tests → PASS**, then typecheck: `bun run test src/helpers/wizard/wizardApi.test.ts && bun run typecheck`.

- [ ] **Step 6: Commit**

```bash
cd web-react && bun run format
cd /home/dannyb/sources/PiFire && jj --no-pager diff --git
jj commit -m "feat(web-react): wizard API types and client"
jj --no-pager show @- --stat
```

---

## Task 6: React — pure working-state model

**Files:**
- Create: `web-react/src/helpers/wizard/wizardState.ts`
- Test: `web-react/src/helpers/wizard/wizardState.test.ts`

**Interfaces:**
- Consumes: `WizardState`, `WizardWorking`, `WizardSection` from `wizardTypes`.
- Produces (pure functions, no React): `initialWorking(state: WizardState): WizardWorking`; `selectModule(w, section, module): WizardWorking`; `setDepValue(w, section, key, value): WizardWorking`; `setDisplayConfig(w, module, optionName, value): WizardWorking`; `displayConfigFor(w, module): Record<string, unknown>` (returns `{}` when absent — the KeyError fix).

- [ ] **Step 1: Write failing tests**

Create `web-react/src/helpers/wizard/wizardState.test.ts`:
```ts
import { describe, expect, test } from "@rstest/core";
import { displayConfigFor, initialWorking, selectModule, setDisplayConfig } from "./wizardState";
import type { WizardState } from "./wizardTypes";

const base: WizardState = {
  modules_metadata: { grillplatform: {}, display: {}, distance: {}, probes: {} },
  selections: { grillplatform: "pcb_4.x.x", display: "ili9341b", distance: "none", probes: "" },
  settings_dep_values: { grillplatform: {}, display: {}, distance: {} } as never,
  display_config: { ili9341b: { rotation: 90 } },
  control_mode: "Stop", first_time_setup: true, has_draft: false,
};

describe("wizardState", () => {
  test("initialWorking copies selections/deps/display_config", () => {
    const w = initialWorking(base);
    expect(w.selections.display).toBe("ili9341b");
    expect(w.display_config.ili9341b.rotation).toBe(90);
  });
  test("displayConfigFor returns {} for a never-configured module (KeyError fix)", () => {
    const w = initialWorking(base);
    expect(displayConfigFor(w, "ili9488b")).toEqual({});
    expect(displayConfigFor(w, "ili9341b")).toEqual({ rotation: 90 });
  });
  test("selectModule is immutable and updates the section", () => {
    const w = initialWorking(base);
    const w2 = selectModule(w, "display", "st7789");
    expect(w2.selections.display).toBe("st7789");
    expect(w.selections.display).toBe("ili9341b"); // original untouched
  });
  test("setDisplayConfig writes nested option value immutably", () => {
    const w = initialWorking(base);
    const w2 = setDisplayConfig(w, "ili9341b", "rotation", 180);
    expect(w2.display_config.ili9341b.rotation).toBe(180);
    expect(w.display_config.ili9341b.rotation).toBe(90);
  });
});
```

- [ ] **Step 2: Run, verify FAIL** — `cd web-react && bun run test src/helpers/wizard/wizardState.test.ts`.

- [ ] **Step 3: Implement `wizardState.ts`**
```ts
import type { WizardSection, WizardState, WizardWorking } from "./wizardTypes";

export function initialWorking(state: WizardState): WizardWorking {
  return {
    selections: { ...state.selections },
    settings_dep_values: structuredClone(state.settings_dep_values),
    display_config: structuredClone(state.display_config),
  };
}

export function selectModule(w: WizardWorking, section: WizardSection, module: string): WizardWorking {
  return { ...w, selections: { ...w.selections, [section]: module } };
}

export function setDepValue(
  w: WizardWorking, section: WizardSection, key: string, value: string | null,
): WizardWorking {
  return {
    ...w,
    settings_dep_values: {
      ...w.settings_dep_values,
      [section]: { ...w.settings_dep_values[section], [key]: value },
    },
  };
}

export function displayConfigFor(w: WizardWorking, module: string): Record<string, unknown> {
  return w.display_config[module] ?? {};
}

export function setDisplayConfig(
  w: WizardWorking, module: string, optionName: string, value: unknown,
): WizardWorking {
  return {
    ...w,
    display_config: {
      ...w.display_config,
      [module]: { ...displayConfigFor(w, module), [optionName]: value },
    },
  };
}
```

- [ ] **Step 4: Run → PASS**; coverage: `bun run test:coverage src/helpers/wizard/wizardState.test.ts` (expect ~100% for this file).

- [ ] **Step 5: Commit**
```bash
cd web-react && bun run format
cd /home/dannyb/sources/PiFire && jj --no-pager diff --git
jj commit -m "feat(web-react): pure wizard working-state model with KeyError-safe display config"
jj --no-pager show @- --stat
```

---

## Task 7: React — field widgets + DiscoveryPanel

**Files:**
- Create: `web-react/src/components/wizard/fields/SelectField.tsx`, `I2cBusPicker.tsx`, `UsbSerialPicker.tsx`
- Create: `web-react/src/components/wizard/DiscoveryPanel.tsx`
- Tests: sibling `.test.tsx` for each

**Interfaces:**
- Consumes: `scan` from `wizardApi`; `SettingsDependency`, `ScanResult` from `wizardTypes`.
- Produces:
  - `SelectField({label, value, options, hidden?, onChange})` — a `<label>`+`<select>`; renders nothing when `hidden`.
  - `I2cBusPicker({dep, value, kindValue, onChange, onScan})` and `UsbSerialPicker(...)` — a `SelectField`/text input plus a "Discover" button that calls `onScan` and shows the returned groups via `DiscoveryPanel`.
  - `DiscoveryPanel({result, onPick})` — renders `result.groups` (title + item buttons); shows `result.error` when present; a "no results" error is a message, never an empty table.

- [ ] **Step 1: Write failing tests** (one `.test.tsx` per component). Cover: `SelectField` renders options + fires `onChange`, and renders nothing when `hidden`; `DiscoveryPanel` renders groups, renders the error string when `result.error` is set, and calls `onPick(value)` on item click; `I2cBusPicker` "Discover" button calls `onScan` and then renders the panel (mock `scan` via `rs.mock("../../../helpers/wizard/wizardApi", ...)`). Use `renderRoute`/plain `render` from `src/test-utils.tsx` + `afterEach(cleanup)`.

Example (`DiscoveryPanel.test.tsx`):
```tsx
import { afterEach, describe, expect, test, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DiscoveryPanel } from "./DiscoveryPanel";

afterEach(cleanup);

describe("DiscoveryPanel", () => {
  test("renders groups and picks an item", () => {
    const onPick = rs.fn();
    render(<DiscoveryPanel result={{ groups: [{ title: "By Bus", items: [{ value: "1", label: "i2c-1" }] }], error: null }} onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "i2c-1" }));
    expect(onPick).toHaveBeenCalledWith("1");
  });
  test("shows error instead of table", () => {
    render(<DiscoveryPanel result={{ groups: [], error: "No devices found." }} onPick={rs.fn()} />);
    expect(screen.getByText("No devices found.")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run, verify FAIL.** `cd web-react && bun run test src/components/wizard/`

- [ ] **Step 3: Implement the components.** `SelectField` (return `null` when `hidden`); `DiscoveryPanel` (map groups → `<div>`+item `<button>`s; when `error`, render a `<p role="alert">{error}</p>` and no table); `I2cBusPicker`/`UsbSerialPicker` compose `SelectField`/text input + a Discover `<button>` that sets local `loading`, calls `onScan()` (which returns a `ScanResult`), stores it, and renders `<DiscoveryPanel result={...} onPick={v => onChange(v)} />`. Honor `dep.hidden`. The i2c kind pairing is passed as an explicit `kindValue` prop (NOT string-replace). Keep each component small and presentational.

- [ ] **Step 4: Run → PASS**; `bun run test:coverage src/components/wizard/fields src/components/wizard/DiscoveryPanel.test.tsx` ≥75% per file.

- [ ] **Step 5: Commit**
```bash
cd web-react && bun run format
cd /home/dannyb/sources/PiFire && jj --no-pager diff --git
jj commit -m "feat(web-react): wizard field widgets and DiscoveryPanel"
jj --no-pager show @- --stat
```

---

## Task 8: React — ConfigOptionField (list/string)

**Files:**
- Create: `web-react/src/components/wizard/ConfigOptionField.tsx`
- Test: `web-react/src/components/wizard/ConfigOptionField.test.tsx`

**Interfaces:**
- Consumes: `ConfigOption` from `wizardTypes`.
- Produces: `ConfigOptionField({option, value, onChange})` — `option.option_type === "list"` → `<select>` over `list_values`/`list_labels`; `"string"` → `<input type="text">`; renders nothing when `option.hidden`. Values compared/emitted as strings (server coerces). Reusable by the future probes device-config table.

- [ ] **Step 1: Failing tests** — list renders labeled options + emits chosen `list_values` entry (as string); string renders text input + emits typed value; `hidden` renders nothing.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — map `list_values[i]`↔`list_labels[i]`; compare with `String(value) === String(item)`; `onChange(String(next))`.
- [ ] **Step 4: Run → PASS**; coverage ≥75%.
- [ ] **Step 5: Commit** `feat(web-react): ConfigOptionField for list/string module config options`.

---

## Task 9: React — ModuleCard

**Files:**
- Create: `web-react/src/components/wizard/ModuleCard.tsx`
- Test: `web-react/src/components/wizard/ModuleCard.test.tsx`

**Interfaces:**
- Consumes: `WizardModuleData`, `SettingsDependency`, `ConfigOption`, `WizardSection` from `wizardTypes`; `SelectField`, `I2cBusPicker`, `UsbSerialPicker`, `ConfigOptionField`, `scan`.
- Produces: `ModuleCard({section, modules, selectedModule, depValues, configValues, configSource, onSelectModule, onDepChange, onConfigChange, baseUrl})` where `modules: Record<string, WizardModuleData>`, `configSource: "none" | "settings-by-module"`. Renders: module `<select>` (module identity), the selected module's image/friendly_name/description/notes, a settings-dependency table (dispatch each dep by `type` → SelectField / I2cBusPicker / UsbSerialPicker; skip `hidden`), and (when the module has `config` AND `configSource === "settings-by-module"`) a config table of `ConfigOptionField`s. **Module change is pure client state** (calls `onSelectModule`), no server round-trip.

- [ ] **Step 1: Failing tests**
  - Renders module options from `modules`, changing the `<select>` calls `onSelectModule(newModule)` (no fetch).
  - Renders a `SelectField` per non-hidden settings-dependency; a `hidden` dep renders nothing.
  - For `configSource="settings-by-module"` with a module carrying `config`, renders `ConfigOptionField`s from `configValues`; when `configValues` is `{}` (never-configured module) it still renders using option defaults and does NOT throw (KeyError-safe).
  - For `configSource="none"`, renders no config table even if `config` present.
  - A dep with `type: "i2c_bus_num"` renders the `I2cBusPicker` (assert its Discover button present).

Use `rs.mock("../../helpers/wizard/wizardApi", () => ({ scan: rs.fn().mockResolvedValue({groups:[],error:null}) }))`.

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `ModuleCard`.** Field dispatch:
```tsx
function renderDep(key: string, dep: SettingsDependency) {
  if (dep.hidden) return null;
  const common = { key, label: dep.friendly_name, value: depValues[key] ?? "", onChange: (v: string) => onDepChange(key, v) };
  if (dep.type === "i2c_bus_num") return <I2cBusPicker {...common} dep={dep} kindValue={depValues[key.replace("_num", "_kind")] ?? ""} onScan={(kind) => scan(baseUrl, { kind })} />;
  if (dep.type === "usb_serial_device") return <UsbSerialPicker {...common} dep={dep} onScan={() => scan(baseUrl, { kind: "usb_serial" })} />;
  return <SelectField {...common} options={dep.options ?? {}} />;
}
```
(Compute the paired kind key here in the parent and pass it as `kindValue` — the widget itself takes an explicit prop, not the string-replace.) Config table only when `configSource === "settings-by-module" && selected.config?.length`.

- [ ] **Step 4: Run → PASS**; coverage ≥75%.
- [ ] **Step 5: Commit** `feat(web-react): shared ModuleCard (module select + settings-dep + config tables)`.

---

## Task 10: React — InstallProgress

**Files:**
- Create: `web-react/src/components/wizard/InstallProgress.tsx`
- Test: `web-react/src/components/wizard/InstallProgress.test.tsx`

**Interfaces:**
- Consumes: `getInstallStatus` from `wizardApi`; `InstallStatus` type.
- Produces: `InstallProgress({baseUrl, onDone})` — polls `getInstallStatus` every 250ms; renders `{status}` + a progress bar at `min(percent,100)%`; on `percent === 142` stops polling and renders a reboot modal (buttons linking `/admin/reboot` and `/admin/restart`); on other `percent > 100` calls `onDone("restart")` (caller redirects to `/admin/restart`); clears the interval on unmount.

- [ ] **Step 1: Failing tests** (fake timers). Mock `getInstallStatus` to return a sequence (`{percent:10}` → `{percent:142}`); advance timers; assert the reboot modal appears and polling stopped. Second test: `{percent:101}` → `onDone("restart")` called. Third: unmount clears interval (no post-unmount calls).

Idiom:
```tsx
import { rs } from "@rstest/core";
rs.useFakeTimers();
rs.mock("../../helpers/wizard/wizardApi", () => ({ getInstallStatus: getStatusMock }));
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** with `useEffect` + `setInterval(async () => { const s = await getInstallStatus(baseUrl); setStatus(s); if (s.percent > 100) { clearInterval(id); if (s.percent === 142) setReboot(true); else onDone("restart"); } }, 250)`; clear on unmount. Respect `prefers-reduced-motion` for the bar transition.
- [ ] **Step 4: Run → PASS**; coverage ≥75%.
- [ ] **Step 5: Commit** `feat(web-react): InstallProgress poller with reboot/restart branches`.

---

## Task 11: React — WizardShell (stepper, state, draft flush, finish gating)

**Files:**
- Create: `web-react/src/components/wizard/WizardShell.tsx`
- Create: `web-react/src/helpers/wizard/wizardRoutes.ts` (the `wizardLoader`)
- Test: `web-react/src/components/wizard/WizardShell.test.tsx`

**Interfaces:**
- Consumes: `getWizardState`, `saveDraft`, `finishWizard` from `wizardApi`; `initialWorking` and reducers from `wizardState`; `InstallProgress`; the step components (Task 12 provides `DisplayStep`/`PlaceholderStep`, imported here).
- Produces: `wizardLoader(): Promise<WizardState>` (calls `getWizardState(BASE_URL)`); `WizardShell` reading it via `useLoaderData()`; the `STEPS` order `["welcome","grillplatform","probes","display","distance","finish"]`; step nav (Back/Next); a `saveDraft` flush on each step transition; Finish → `finishWizard`; on 409 shows a "system active" modal; on success renders `<InstallProgress>`. Exports `HydrateFallback` and `WizardError`.

- [ ] **Step 1: Failing tests** (mock `wizardApi`). Cover: renders the welcome step first; Next advances the step and calls `saveDraft` with current working state; Finish on the finish step calls `finishWizard`; a `finishWizard` result `{ok:false,status:409,message:"system_active"}` shows the system-active modal and does NOT render InstallProgress; a `{ok:true}` renders `<InstallProgress>`. Provide the loader data via `renderRoute`/a memory router with the loader stubbed, or by mocking `useLoaderData`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `const [working, setWorking] = useState(() => initialWorking(state));` `const [step, setStep] = useState(0);` Back/Next mutate `step`; on any transition call `saveDraft(BASE_URL, working)` (fire-and-forget, but await in a wrapper so tests can assert). Render the current step's component, passing `working` + change handlers wired to the `wizardState` reducers. Finish button (finish step) calls `finishWizard`; branch on the result. Guard: if `state.control_mode !== "Stop"`, the Finish button is disabled with a note (belt-and-suspenders with the server 409).
- [ ] **Step 4: Run → PASS**; coverage ≥75%.
- [ ] **Step 5: Commit** `feat(web-react): WizardShell stepper with draft flush and finish gating`.

---

## Task 12: React — Display step, placeholder steps, routing

**Files:**
- Create: `web-react/src/components/wizard/steps/DisplayStep.tsx`, `steps/PlaceholderStep.tsx`
- Modify: `web-react/src/components/App.tsx` (add `/wizard` route + first_time_setup gate)
- Tests: `steps/DisplayStep.test.tsx`, and an `App` routing test if the repo has one (else a focused `DisplayStep` + gate test)

**Interfaces:**
- Consumes: `ModuleCard`; `WizardWorking` + reducers; `WizardState`.
- Produces: `DisplayStep({state, working, onChange})` rendering `<ModuleCard section="display" configSource="settings-by-module" ...>` wired to `working` via the `wizardState` reducers (module select → `selectModule`; config change → `setDisplayConfig`; the `configValues` = `displayConfigFor(working, working.selections.display)`). `PlaceholderStep({section})` renders a "configured in a later release" panel. `App.tsx` gains a top-level `/wizard` route (`element: <WizardShell/>`, `loader: wizardLoader`, `errorElement: <WizardError/>`, `HydrateFallback`).

- [ ] **Step 1: Failing tests.** `DisplayStep`: selecting a display module updates working (assert via a captured `onChange`); editing a config option calls `onChange` with the new `display_config`. `PlaceholderStep`: renders the section name + a clear "not yet configurable here" message. Routing: navigating to `/wizard` renders the shell (if an App-level router test exists; otherwise assert the route object is present in the exported routes array).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `DisplayStep` composes `ModuleCard` with display wiring (KeyError-safe via `displayConfigFor`). `PlaceholderStep` is a small presentational panel. Add the `/wizard` route to `App.tsx`'s routes array (mirror the `/settings` entry's structure). First-time-setup gate: if the app should force the wizard when `first_time_setup`, add a redirect in the loader or a top-level guard — keep it minimal (a redirect to `/wizard` from the index loader when `first_time_setup` is true, matching Flask's forced-wizard behavior); if that risks a redirect loop with existing routes, gate only the index route and document it.
- [ ] **Step 4: Run → PASS**; coverage ≥75%.
- [ ] **Step 5: Commit** `feat(web-react): display step, placeholder steps, and /wizard route`.

---

## Task 13: E2e final gate

**Files:**
- Create: `web-react/tests/e2e/wizard.spec.ts`

**Interfaces:**
- Consumes: the running prototype backend (control.py + gunicorn on :5000) + `bun run dev` on :5173, per `playwright.config.ts`.
- Produces: one e2e that walks to the display step, selects a module, edits a config field, and asserts the assembled state persists as a draft (via `page.request.get("/api/wizard/state")`), then restores/clears the draft — leave-as-found.

- [ ] **Step 1: Write the e2e** (mirror `tests/e2e/settings.spec.ts` structure + the leave-as-found convention). Navigate `/wizard`, step to Display, pick a non-default module (e.g. `st7789`), set a `rotation`, click Next (triggers draft flush), then `const s = await page.request.get("/api/wizard/state")` and assert `body.has_draft === true` and the display selection/config match. Do NOT click Finish (it would fire the real installer). Restore: POST a cleared draft or call an endpoint that resets `has_draft` so the backend is left as found. If clearing the draft needs a helper, add a minimal `POST /api/wizard/draft` with an empty/`clear` flag in Task 2's endpoint (note here rather than expanding scope silently).
- [ ] **Step 2: Run** (requires backend up): `cd web-react && bun run test:e2e tests/e2e/wizard.spec.ts` (skips cleanly in no-chromium agent envs; re-run in the main checkout before merge per the chromium-skip convention).
- [ ] **Step 3: Full gate.** `cd web-react && bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build`. All green; coverage thresholds met.
- [ ] **Step 4: Commit** `test(web-react): wizard display-step e2e round-trip (leave-as-found)`.

---

## Self-review notes (for the executor)

- **Draft clearing:** Tasks 11/13 assume a way to clear the draft (so a completed/cancelled wizard recomputes fresh, per the spec's correctness rule). If the executor finds no clear path, extend Task 2's `/draft` to accept `{clear: true}` (drop `_DRAFT_KEY` + the working keys, `store_wizard_install_info`) and update the finish handler to clear the draft after firing the installer. This is in-scope (it's the spec's "completed/cancelled Finish clears the draft" line) — implement it in whichever task first needs it and note it in that task's report.
- **`profile_selected` shape (Task 2) and `wizard_bus_kinds` signature (Task 4)** are the two backend spots where the plan's code is provisional pending a read of the real `blueprints/wizard/wizard.py` helpers — the tasks call this out explicitly; adapt to the real shapes, keep the contract fixed.
- **Type consistency:** all React tasks import the Task-5 types; the working-state reducers (Task 6) are the only mutation path; `ModuleCard`'s `configSource` prop is the single per-section branch.
