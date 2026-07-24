# Wizard Probes Config (React) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port PiFire's probe-configuration surface (devices + ports) into the React wizard as a client-held `probe_map` reducer plus a two-card Probes step, with all five discovery flows and profile value-copy.

**Architecture:** A pure TypeScript reducer owns the `probe_map` working object (`{probe_devices[], probe_info[]}`); every CRUD mutation, the virtual-port reposition algorithm, cascade-delete, and the exactly-one-Primary invariant run in TS. The backend `/api/wizard/*` surface (built for the display slice) is extended so `/state` and `/draft` carry `probe_map` + `probe_profiles` + `probes_units`, `/finish` reads `probe_map` from the payload, and Bluetooth/ThermoWorks/bus-kind endpoints are added. No live per-op server writes — edits stage into the draft blob and commit once at Finish.

**Tech Stack:** Flask blueprint (`blueprints/api_wizard`), Python 3.14 + pytest; React 19 + TypeScript (tsgo), rsbuild, rstest (jsdom for `.test.tsx`, node for `.test.ts`), Biome, Playwright. Package manager: **bun**.

**Spec:** `docs/superpowers/specs/2026-07-23-wizard-probes-config.md`. Legacy ground truth: `.superpowers/sdd/probeconfig-inventory.md` (cited §N). The display-slice companion this extends: `blueprints/api_wizard/routes.py`, `web-react/src/helpers/wizard/*`, `web-react/src/components/wizard/*`.

## Global Constraints

- **Scope:** one combined spec — devices + ports + reposition + profile selection + all five discovery flows.
- **Logic home:** a pure client-held TS reducer owns `probe_map`. No live per-op server writes. The reposition algorithm (§3) is reproduced **exactly** — it encodes a runtime invariant the value-averaging pass depends on. Legacy Python `blueprints/probeconfig/*` is left in place (deleted only after React parity is proven live).
- **Data flow (as display slice):** client holds working state; it joins the draft blob (datastore key `wizard:install`) and the single `/api/wizard/finish` payload. `/api/wizard/state` resumes from the draft if `has_draft`, else `_build_state` seeds from live settings. No selection is `null`, never `""`. List-typed fields are always lists.
- **Four bug fixes vs legacy** (§9): (1) device-rename cascades to `probe_info[].device`; (2) zero-Primary guard on delete/type-change — invalid only when `probe_info` is non-empty AND has zero Primary; zero probes → zero Primary is allowed; add stays exempt (its guard is the *second*-Primary check); (3) reject an empty alnum-stripped device name; (4) device delete scrubs the deleted probes' labels from every virtual device's `probes_list`.
- **Coverage / gate:** web-react gate stays green — **≥75% lines per file** (rstest, `thread` concurrency), `bun run typecheck` clean, Biome lint clean, `bun run build` succeeds. bun, not npm; commit `bun.lock`.
- **Testing API (rstest, NOT vitest):** import `{ describe, it, expect, rs }` from `@rstest/core`; use `rs.fn()` / `rs.mock(path, factory)` / `rs.spyOn` (there is NO `vi`). Component tests import `{ cleanup, fireEvent, render, screen }` from `@testing-library/react` and register `afterEach(cleanup)`. **Mirror the existing sibling test `src/components/wizard/fields/I2cBusPicker.test.tsx` exactly for the module-mock + render pattern.** The `rs.fn()`/`rs.mock(...)` calls shown in this plan's test snippets are the correct API; do not translate them to `vi`.
- **Python:** full suite green under `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`. Run `uvx ruff format` on changed Python before every commit. PEP 758 bare-tuple `except A, B` is ruff-canonical here — do not "fix" it.
- **Installer safety:** `os.system` in `/finish` must be monkeypatch-neutralized in every test reaching it; no test may fire the real installer.
- **jj:** per-task commit, manual format, verify `jj --no-pager diff --git` before commit. Never run jj-write mutations while an implementer is committing in the same workspace.
- **Chromium/e2e:** `[chromium]`/Playwright tests skip in agent worktrees — re-run in the main checkout before merge.

## Data shapes (authoritative — copied from live manifest/settings)

`probe_map` (datastore blob `wizard:install` → key `probe_map`; board manifest `boards.<b>.probe_map`):

```jsonc
{
  "probe_devices": [
    { "device": "ADS1115", "module": "ads1115_adafruit", "module_filename": "ads1115_adafruit",
      "ports": ["ADC0","ADC1","ADC2","ADC3"],
      "config": { "i2c_bus_addr": "0x48", "voltage_ref": "3.28", "ADC0_rd": "10000", "...": "..." } }
  ],
  "probe_info": [
    { "name": "Grill", "label": "Grill", "type": "Primary", "enabled": true,
      "device": "ADS1115", "port": "ADC0",
      "profile": { "A": 0.0413, "B": -0.0067, "C": 2.76e-05, "id": "99b8...", "name": "PT-1000-Ideal" } }
  ]
}
```

A probe module manifest (`modules.probes.<key>`) carries `friendly_name`, `filename`, `description`, `image`, optional `notes`, `settings_dependencies`, and `device_specific: { ports: string[], type: string, config: ProbeConfigField[] }`. Each `ProbeConfigField` has `label`, `friendly_name`, `description`, `type` (`"list" | "int" | "float" | "string" | "i2c_bus_num" | "probes_list" | "bt_address" | "usb_serial_device"`), `default`, `hidden`, and per-type extras (`list_values`/`list_labels` for list; `min`/`max`/`step` for int/float). **`device_serial` is `type: "string"` + `hidden: true`, special-cased by LABEL** (§6).

`probe_config_options` (fixed 5-field port form, in this order): `name`, `device_port`, `type` (`Food`/`Primary`/`Aux`), `profile_id`, `enabled` (`"true"`/`"false"`). `type.description` and others contain literal HTML (`<br>`, `<strong>`) that must render as HTML (§6).

A profile: `{ A: number, B: number, C: number, id: string, name: string }`.

---

## Task 1: Backend — `/state` + `/draft` carry probe_map, probe_profiles, probes_units

**Files:**
- Modify: `blueprints/api_wizard/routes.py` (`_build_state`, `wizard_draft`)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: existing `_build_state(settings, control)`, `wizard_draft()`, `_load_draft()`, `store_wizard_install_info`, `read_settings` in `blueprints/api_wizard/routes.py`.
- Produces: `/api/wizard/state` JSON gains `probe_map` (dict `{probe_devices, probe_info}`), `probe_profiles` (list of profile objects), `probes_units` (str `"F"`/`"C"`). `/api/wizard/draft` persists `probe_map` + `probes_units` alongside the existing draft keys, and clears them on `{clear:true}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_api_wizard.py`:

```python
def test_state_includes_probe_map_profiles_units(ds, client):
    resp = client.get("/api/wizard/state")
    body = resp.get_json()
    assert isinstance(body["probe_map"], dict)
    assert "probe_devices" in body["probe_map"] and "probe_info" in body["probe_map"]
    # probe_profiles is a LIST of profile objects (not the settings dict keyed by id)
    assert isinstance(body["probe_profiles"], list)
    assert all("id" in p and "name" in p for p in body["probe_profiles"])
    assert body["probes_units"] in ("F", "C")


def test_draft_persists_and_resumes_probe_map_and_units(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {},
        "display_config": {},
        "probe_map": {"probe_devices": [{"device": "D1", "module": "ads1115_adafruit",
                       "module_filename": "ads1115_adafruit", "ports": ["ADC0"], "config": {}}],
                      "probe_info": []},
        "probes_units": "C",
    }
    r1 = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r1.status_code == 200
    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is True
    assert body["probe_map"]["probe_devices"][0]["device"] == "D1"
    assert body["probes_units"] == "C"


def test_draft_clear_drops_probe_map_and_units(ds, client):
    draft = {"selections": {}, "settings_dep_values": {}, "display_config": {},
             "probe_map": {"probe_devices": [{"device": "D1"}], "probe_info": []}, "probes_units": "C"}
    client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    client.post("/api/wizard/draft", data=json.dumps({"clear": True}), content_type="application/json")
    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is False
    # After clear, probe_map falls back to the computed value (from live settings), not the drafted one
    assert body["probe_map"]["probe_devices"][0]["device"] != "D1" or body["probe_map"]["probe_devices"] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k "probe_map or profiles_units or probe_map_and_units" -v`
Expected: FAIL — `KeyError: 'probe_map'` (state has no such key yet).

- [ ] **Step 3: Extend `_build_state`**

In `blueprints/api_wizard/routes.py`, inside `_build_state`, after the `if has_draft:` / `else:` block that sets `selections`/`settings_dep_values`/`display_config`, add probe fields. In the `has_draft` branch read them from the draft; in the `else` branch compute from live settings:

```python
    if has_draft:
        selections = draft.get("selections", {})
        settings_dep_values = draft.get("settings_dep_values", {})
        display_config = draft.get("display_config", {})
        probe_map = draft.get("probe_map") or {"probe_devices": [], "probe_info": []}
        probes_units = draft.get("probes_units") or settings["globals"].get("units", "F")
    else:
        # ... existing selections/settings_dep_values/display_config computation ...
        probe_map = settings.get("probe_settings", {}).get("probe_map", {"probe_devices": [], "probe_info": []})
        probes_units = settings["globals"].get("units", "F")

    # probe_profiles is shipped as a LIST for the port form's picker. Live
    # settings store it as a dict keyed by id; flatten to the value objects.
    profiles_dict = settings.get("probe_settings", {}).get("probe_profiles", {})
    probe_profiles = list(profiles_dict.values())
```

Then add the three keys to the returned dict:

```python
    return {
        "modules_metadata": {s: modules.get(s, {}) for s in _SECTIONS if s in modules},
        "selections": selections,
        "settings_dep_values": settings_dep_values,
        "display_config": display_config,
        "probe_map": probe_map,
        "probe_profiles": probe_profiles,
        "probes_units": probes_units,
        "control_mode": control.get("mode", "Stop"),
        "first_time_setup": bool(settings["globals"]["first_time_setup"]),
        "has_draft": has_draft,
    }
```

- [ ] **Step 4: Extend `wizard_draft`**

In `wizard_draft`, add `probe_map`/`probes_units` to both the clear branch and the persist branch:

```python
    if payload.get("clear"):
        info.pop(_DRAFT_KEY, None)
        info.pop("selections", None)
        info.pop("settings_dep_values", None)
        info.pop("display_config", None)
        info.pop("probe_map", None)
        info.pop("probes_units", None)
        store_wizard_install_info(info)
        return jsonify({"result": "success"}), 200

    info[_DRAFT_KEY] = True
    info["selections"] = payload.get("selections", {})
    info["settings_dep_values"] = payload.get("settings_dep_values", {})
    info["display_config"] = payload.get("display_config", {})
    info["probe_map"] = payload.get("probe_map", {"probe_devices": [], "probe_info": []})
    info["probes_units"] = payload.get("probes_units", "F")
    store_wizard_install_info(info)
    return jsonify({"result": "success"}), 200
```

- [ ] **Step 5: Run tests + format**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -v` — Expected: all pass.
Then: `uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py`

- [ ] **Step 6: Commit**

```bash
jj desc -m "feat(api-wizard): carry probe_map, probe_profiles, probes_units in /state and /draft"
```

---

## Task 2: Backend — `/finish` reads probe_map + probes_units from the payload

**Files:**
- Modify: `blueprints/api_wizard/routes.py` (`_wizard_install_info_from_payload`)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: `_wizard_install_info_from_payload(payload, existing)`, `wizard_finish()` from Task 1's file.
- Produces: `_wizard_install_info_from_payload` now takes `probe_map` from `payload["probe_map"]` (falling back to `existing["probe_map"]` only when the payload omits it) and writes `payload["probes_units"]` into `modules["probes"]["settings"]["units"]`.

- [ ] **Step 1: Write the failing test**

```python
def test_finish_uses_probe_map_from_payload(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    monkeypatch.setattr(wr, "wizard_bus_kinds", lambda *a, **k: {})
    monkeypatch.setattr(wr, "validate_bus_kinds", lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(wr, "store_wizard_install_info", lambda info: captured.update(info))
    payload = {
        "selections": {"grillplatform": "custom", "display": "ili9341b", "distance": "hcsr04"},
        "settings_dep_values": {}, "display_config": {},
        "probe_map": {"probe_devices": [{"device": "PAYLOAD_DEV", "module": "ads1115_adafruit",
                       "module_filename": "ads1115_adafruit", "ports": ["ADC0"], "config": {}}],
                      "probe_info": []},
        "probes_units": "C",
    }
    resp = client.post("/api/wizard/finish", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 200
    assert captured["probe_map"]["probe_devices"][0]["device"] == "PAYLOAD_DEV"
    assert captured["modules"]["probes"]["settings"]["units"] == "C"
    assert captured["modules"]["probes"]["profile_selected"] == ["ads1115_adafruit"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py::test_finish_uses_probe_map_from_payload -v`
Expected: FAIL — `captured["probe_map"]` is `{}` (payload probe_map ignored; current code reads it from `existing` only).

- [ ] **Step 3: Modify `_wizard_install_info_from_payload`**

Change the `probe_map` source and the probes `settings`. Replace the current `probe_map`/`probe_devices` derivation lines:

```python
    # probe_map is client-held (the React probe reducer): prefer the payload,
    # fall back to whatever is persisted only when the payload omits it.
    probe_map = payload.get("probe_map")
    if not isinstance(probe_map, dict):
        probe_map = existing.get("probe_map") if isinstance(existing, dict) else None
    if not isinstance(probe_map, dict):
        probe_map = {"probe_devices": [], "probe_info": []}
    probe_devices = probe_map.get("probe_devices") or []
    probes_units = payload.get("probes_units") or ""
```

Then in the `modules["probes"]` block, add the units into settings:

```python
    probes_settings = dict(settings_dep_values.get("probes", {}) or {})
    if probes_units:
        probes_settings["units"] = probes_units
    modules["probes"] = {
        "profile_selected": [d.get("module") for d in probe_devices if d.get("module")],
        "settings": probes_settings,
        "config": {},
    }
```

- [ ] **Step 4: Run tests + format**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -v` — Expected: all pass (existing finish tests still green — they omit `probe_map`, so the `existing` fallback keeps them working).
Then: `uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py`

- [ ] **Step 5: Commit**

```bash
jj desc -m "feat(api-wizard): /finish reads client-held probe_map + probes_units from payload"
```

---

## Task 3: Backend — Bluetooth + ThermoWorks scan endpoints

**Files:**
- Modify: `blueprints/api_wizard/routes.py` (add two routes + imports)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: legacy helpers `get_supported_cmds`, `process_command`, `get_system_command_output` (`common/app.py`), `parse_bt_device_info` (`blueprints/wizard/wizard.py`), `discover` (`probes.thermoworks_cloud`), `AuthenticationError` (`thermoworks_cloud`).
- Produces: `POST /api/wizard/scan/bluetooth` → `{"rows": [{"name","hw_id","info"}], "error": str|None}`; `POST /api/wizard/scan/thermoworks` (body `{email,password}`) → `{"rows": [{"label","type","serial","num_channels"}], "error": str|None}`. Never 500 (blanket-except → friendly error).

- [ ] **Step 1: Write the failing tests**

```python
def test_scan_bluetooth_returns_rows(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    monkeypatch.setattr(wr, "get_supported_cmds", lambda: ["scan_bluetooth"])
    monkeypatch.setattr(wr, "process_command", lambda **k: None)
    monkeypatch.setattr(wr, "get_system_command_output",
        lambda **k: {"result": "OK", "data": {"bt_devices": [{"name": "iBBQ", "hw_id": "AA:BB", "info": ""}]}})
    monkeypatch.setattr(wr, "parse_bt_device_info", lambda devs: devs)
    resp = client.post("/api/wizard/scan/bluetooth", data=json.dumps({}), content_type="application/json")
    body = resp.get_json()
    assert body["error"] is None
    assert body["rows"][0]["hw_id"] == "AA:BB"


def test_scan_bluetooth_unsupported_is_friendly_error(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    monkeypatch.setattr(wr, "get_supported_cmds", lambda: [])
    resp = client.post("/api/wizard/scan/bluetooth", data=json.dumps({}), content_type="application/json")
    body = resp.get_json()
    assert body["rows"] == []
    assert body["error"] == "No support for bluetooth scan command."


def test_scan_thermoworks_auth_error(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    from thermoworks_cloud import AuthenticationError

    def _boom(*a, **k):
        raise AuthenticationError("bad creds")
    monkeypatch.setattr(wr, "_thermoworks_discover", _boom)
    resp = client.post("/api/wizard/scan/thermoworks",
        data=json.dumps({"email": "x@y.z", "password": "nope"}), content_type="application/json")
    body = resp.get_json()
    assert body["rows"] == []
    assert "Could not log in" in body["error"]


def test_scan_thermoworks_returns_rows(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    monkeypatch.setattr(wr, "_thermoworks_discover",
        lambda email, password: [{"label": "Signals", "type": "signals", "serial": "S1", "num_channels": 4}])
    resp = client.post("/api/wizard/scan/thermoworks",
        data=json.dumps({"email": "x@y.z", "password": "ok"}), content_type="application/json")
    body = resp.get_json()
    assert body["error"] is None
    assert body["rows"][0]["serial"] == "S1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k "bluetooth or thermoworks" -v`
Expected: FAIL — 404 (routes don't exist).

- [ ] **Step 3: Add imports**

At the top of `blueprints/api_wizard/routes.py` add:

```python
import asyncio

from blueprints.wizard.wizard import parse_bt_device_info
from common.app import get_supported_cmds, get_system_command_output, process_command
from probes.thermoworks_cloud import discover as _thermoworks_discover_impl
from thermoworks_cloud import AuthenticationError
```

Add a thin wrapper (so tests can monkeypatch a sync seam instead of the async impl):

```python
def _thermoworks_discover(email, password):
    """Sync seam over the async ThermoWorks discovery, matching legacy
    _wizard_thermoworks_discover (blueprints/wizard/routes.py:162)."""
    return asyncio.run(_thermoworks_discover_impl(email, password))
```

- [ ] **Step 4: Add the two routes**

```python
@api_wizard_bp.route("/scan/bluetooth", methods=["POST"])
def wizard_scan_bluetooth():
    """Bluetooth peripheral discovery for probe device forms. Hardware-mediated:
    routes scan_bluetooth through the control process (6s timeout). Mirrors
    blueprints/wizard/routes.py::_wizard_bt_scan but returns JSON rows."""
    rows = []
    error = None
    try:
        if "scan_bluetooth" in get_supported_cmds():
            process_command(action="sys", arglist=["scan_bluetooth"], origin="admin")
            data = get_system_command_output(requested="scan_bluetooth", timeout=6)
            if data["result"] != "OK":
                error = data["message"]
            else:
                rows = parse_bt_device_info(data["data"]["bt_devices"])
                if rows == []:
                    error = "No bluetooth devices found."
        else:
            error = "No support for bluetooth scan command."
    except Exception as e:  # never 500 -- surface as a friendly banner
        error = f"Something bad happened: {e}"
        rows = []
    return jsonify({"rows": rows, "error": error}), 200


@api_wizard_bp.route("/scan/thermoworks", methods=["POST"])
def wizard_scan_thermoworks():
    """ThermoWorks Cloud account discovery for the thermoworks_cloud device.
    Blocking network auth; distinguishes bad-creds from generic failure.
    Mirrors blueprints/wizard/routes.py::_wizard_thermoworks_discover."""
    payload = request.get_json(silent=True) or {}
    email = payload.get("email", "")
    password = payload.get("password", "")
    rows = []
    error = None
    try:
        rows = _thermoworks_discover(email, password)
        if rows == []:
            error = "No ThermoWorks Cloud devices found for this account."
    except AuthenticationError as e:
        error = f"Could not log in to ThermoWorks Cloud: {e}"
        rows = []
    except Exception as e:
        error = f"Something bad happened: {e}"
        rows = []
    return jsonify({"rows": rows, "error": error}), 200
```

- [ ] **Step 5: Run tests + format**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k "bluetooth or thermoworks" -v` — Expected: pass.
Then: `uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py`

- [ ] **Step 6: Commit**

```bash
jj desc -m "feat(api-wizard): add /scan/bluetooth and /scan/thermoworks endpoints"
```

---

## Task 4: Backend — `/probes/validate-bus-kinds`

**Files:**
- Modify: `blueprints/api_wizard/routes.py` (add route)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: `configured_bus_kinds`, `validate_bus_kinds`, `I2CBusConfigError` (`common/i2c_bus.py`).
- Produces: `POST /api/wizard/probes/validate-bus-kinds` (body `{probe_devices: [...]}`) → `{"ok": true}` (200) or `{"ok": false, "detail": str}` (200). Validates ONLY the in-progress probe device set against itself (`settings=None`, §7) — deliberately excludes stale fan/distance kinds to avoid false positives.

- [ ] **Step 1: Write the failing tests**

```python
def test_validate_bus_kinds_clean(ds, client):
    devs = [{"device": "D1", "module": "ads1115_adafruit",
             "config": {"i2c_bus_kind": "basic"}, "ports": ["ADC0"]}]
    resp = client.post("/api/wizard/probes/validate-bus-kinds",
        data=json.dumps({"probe_devices": devs}), content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_validate_bus_kinds_conflict(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    from common.i2c_bus import I2CBusConfigError

    def _boom(*a, **k):
        raise I2CBusConfigError("'basic' I2C can't share a process with a USB-HID bus")
    monkeypatch.setattr(wr, "validate_bus_kinds", _boom)
    resp = client.post("/api/wizard/probes/validate-bus-kinds",
        data=json.dumps({"probe_devices": [{"device": "D1", "config": {"i2c_bus_kind": "basic"}}]}),
        content_type="application/json")
    body = resp.get_json()
    assert body["ok"] is False
    assert "USB-HID" in body["detail"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k validate_bus_kinds -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add import + route**

Add `configured_bus_kinds` to the existing `common.i2c_bus` import group. Then:

```python
@api_wizard_bp.route("/probes/validate-bus-kinds", methods=["POST"])
def wizard_probes_validate_bus_kinds():
    """Per-device bus-kind coexistence check for the in-progress probe device
    set only (settings=None, §7) -- deliberately excludes the live fan/distance
    kinds so a mid-wizard edit doesn't false-positive against stale settings.
    The FULL cross-subsystem check still runs at /finish."""
    payload = request.get_json(silent=True) or {}
    probe_devices = payload.get("probe_devices") or []
    try:
        validate_bus_kinds(configured_bus_kinds(None, probe_devices))
    except I2CBusConfigError as exc:
        return jsonify({"ok": False, "detail": str(exc)}), 200
    return jsonify({"ok": True}), 200
```

- [ ] **Step 4: Run tests + format**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k validate_bus_kinds -v` — Expected: pass.
Then: `uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py`

- [ ] **Step 5: Commit**

```bash
jj desc -m "feat(api-wizard): add /probes/validate-bus-kinds (in-progress device set only)"
```

---

## Task 5: React — probe types + wizard state/api extensions

**Files:**
- Create: `web-react/src/helpers/wizard/probeTypes.ts`
- Modify: `web-react/src/helpers/wizard/wizardTypes.ts`, `web-react/src/helpers/wizard/wizardState.ts`, `web-react/src/helpers/wizard/wizardApi.ts`
- Test: `web-react/src/helpers/wizard/wizardState.test.ts` (extend), `web-react/src/helpers/wizard/wizardApi.test.ts` (extend)

**Interfaces:**
- Produces (`probeTypes.ts`):

```ts
export interface ProbeProfile { A: number; B: number; C: number; id: string; name: string; }
export interface ProbeDevice {
  device: string;
  module: string;
  module_filename: string;
  ports: string[];
  config: Record<string, unknown>; // may hold probes_list?: string[]
}
export type ProbeType = "Primary" | "Food" | "Aux";
export interface Probe {
  name: string;
  label: string;
  type: ProbeType;
  enabled: boolean;
  device: string;
  port: string;
  profile: ProbeProfile | Record<string, never>;
}
export interface ProbeMap { probe_devices: ProbeDevice[]; probe_info: Probe[]; }
export type ProbeFieldType =
  | "list" | "int" | "float" | "string" | "i2c_bus_num"
  | "probes_list" | "bt_address" | "usb_serial_device";
export interface ProbeConfigField {
  label: string;
  friendly_name: string;
  description?: string;
  type: ProbeFieldType;
  default?: unknown;
  hidden?: boolean;
  list_values?: unknown[];
  list_labels?: string[];
  min?: number;
  max?: number | "";
  step?: number;
}
export interface ProbeModuleData {
  friendly_name: string;
  filename: string;
  description?: string;
  notes?: string;
  image?: string;
  device_specific: { ports: string[]; type: string; config: ProbeConfigField[] };
}
export interface BtScanRow { name: string; hw_id: string; info: string; }
export interface ThermoworksRow { label: string; type: string; serial: string; num_channels: number; }
export interface RowsResult<T> { rows: T[]; error: string | null; }
```

- Modifies `WizardState` and `WizardWorking` to carry `probe_map`, `probe_profiles`, `probes_units`.

- [ ] **Step 1: Write the failing tests**

Extend `web-react/src/helpers/wizard/wizardState.test.ts`:

```ts
import { initialWorking } from "./wizardState";
import type { WizardState } from "./wizardTypes";

function baseState(): WizardState {
  return {
    modules_metadata: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    selections: { grillplatform: null, probes: null, display: null, distance: null },
    settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    display_config: {},
    probe_map: { probe_devices: [{ device: "D1", module: "m", module_filename: "m", ports: ["ADC0"], config: {} }], probe_info: [] },
    probe_profiles: [{ A: 1, B: 2, C: 3, id: "p1", name: "P1" }],
    probes_units: "C",
    control_mode: "Stop",
    first_time_setup: false,
    has_draft: false,
  };
}

it("initialWorking deep-clones probe_map and copies units", () => {
  const s = baseState();
  const w = initialWorking(s);
  expect(w.probe_map.probe_devices[0].device).toBe("D1");
  expect(w.probes_units).toBe("C");
  w.probe_map.probe_devices[0].device = "MUT";
  expect(s.probe_map.probe_devices[0].device).toBe("D1"); // no aliasing
});
```

Extend `web-react/src/helpers/wizard/wizardApi.test.ts` (uses the existing fetch-mock pattern already in that file):

```ts
import { scanBluetooth, scanThermoworks, validateBusKinds } from "./wizardApi";

it("scanBluetooth posts to /scan/bluetooth and returns rows", async () => {
  const spy = mockFetchJson({ rows: [{ name: "iBBQ", hw_id: "AA", info: "" }], error: null });
  const r = await scanBluetooth("");
  expect(spy).toHaveBeenCalledWith("/api/wizard/scan/bluetooth", expect.objectContaining({ method: "POST" }));
  expect(r.rows[0].hw_id).toBe("AA");
});

it("validateBusKinds posts probe_devices and returns ok", async () => {
  mockFetchJson({ ok: true });
  const r = await validateBusKinds("", [{ device: "D1", module: "m", module_filename: "m", ports: [], config: {} }]);
  expect(r.ok).toBe(true);
});
```

(Reuse whatever fetch-mock helper `wizardApi.test.ts` already defines; name it to match — read the file first.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/helpers/wizard/wizardState.test.ts src/helpers/wizard/wizardApi.test.ts`
Expected: FAIL — missing exports / properties.

- [ ] **Step 3: Create `probeTypes.ts`** with the block from Interfaces above.

- [ ] **Step 4: Extend `wizardTypes.ts`**

Add the import and extend the two interfaces. **Replace** the existing
`modules_metadata: Record<WizardSection, Record<string, WizardModuleData>>` line
with the explicit per-section object below, so the `probes` key carries the
distinct `ProbeModuleData` shape (device_specific) while the other three keep
`WizardModuleData` — existing display/grillplatform/distance consumers are
unaffected:

```ts
import type { ProbeMap, ProbeModuleData, ProbeProfile } from "./probeTypes";

export interface WizardState {
  modules_metadata: {
    grillplatform: Record<string, WizardModuleData>;
    display: Record<string, WizardModuleData>;
    distance: Record<string, WizardModuleData>;
    probes: Record<string, ProbeModuleData>;
  };
  // ...existing selections/settings_dep_values/display_config/control_mode/
  //    first_time_setup/has_draft fields unchanged...
  probe_map: ProbeMap;
  probe_profiles: ProbeProfile[];
  probes_units: string;
}

export interface WizardWorking {
  // ...existing fields...
  probe_map: ProbeMap;
  probes_units: string;
}
```

- [ ] **Step 5: Extend `initialWorking`** in `wizardState.ts`

```ts
export function initialWorking(state: WizardState): WizardWorking {
  return {
    selections: { ...state.selections },
    settings_dep_values: structuredClone(state.settings_dep_values),
    display_config: structuredClone(state.display_config),
    probe_map: structuredClone(state.probe_map),
    probes_units: state.probes_units,
  };
}
```

- [ ] **Step 6: Extend `wizardApi.ts`**

Add three functions (mirror the existing `scan`/`saveDraft` fetch style):

```ts
import type { BtScanRow, ProbeDevice, RowsResult, ThermoworksRow } from "./probeTypes";

export async function scanBluetooth(baseUrl: string): Promise<RowsResult<BtScanRow>> {
  const r = await fetch(url(baseUrl, "scan/bluetooth"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  return (await r.json()) as RowsResult<BtScanRow>;
}

export async function scanThermoworks(
  baseUrl: string, email: string, password: string,
): Promise<RowsResult<ThermoworksRow>> {
  const r = await fetch(url(baseUrl, "scan/thermoworks"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return (await r.json()) as RowsResult<ThermoworksRow>;
}

export async function validateBusKinds(
  baseUrl: string, probeDevices: ProbeDevice[],
): Promise<{ ok: boolean; detail?: string }> {
  const r = await fetch(url(baseUrl, "probes/validate-bus-kinds"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ probe_devices: probeDevices }),
  });
  return (await r.json()) as { ok: boolean; detail?: string };
}
```

- [ ] **Step 7: Run tests + typecheck + lint**

Run: `cd web-react && bun run test src/helpers/wizard/ && bun run typecheck && bunx biome check src/helpers/wizard`
Expected: pass. (Fix any other files that break because `WizardState`/`WizardWorking` gained required fields — e.g. test fixtures in `WizardShell.test.tsx`, `DisplayStep.test.tsx` may need the three new fields added to their mock state. Add them.)

- [ ] **Step 8: Commit**

```bash
jj desc -m "feat(web-react): probe types + probe_map/profiles/units in wizard state and api"
```

---

## Task 6: React reducer — device actions + fixes + selectors

**Files:**
- Create: `web-react/src/helpers/wizard/probeReducer.ts`
- Test: `web-react/src/helpers/wizard/probeReducer.devices.test.ts`

**Interfaces:**
- Consumes: `probeTypes.ts`.
- Produces:

```ts
export type ReducerResult =
  | { ok: true; probeMap: ProbeMap }
  | { ok: false; error: string };

export function alnum(s: string): string;                 // Python str.isalnum() analog (ASCII)
export function isVirtualDevice(d: ProbeDevice): boolean;  // d.module.includes("virtual")
export function availableProbes(pm: ProbeMap): string[];   // probe_info labels
export function devicePortOptions(pm: ProbeMap): { value: string; label: string }[];

export function addDevice(pm: ProbeMap, input: {
  name: string; module: string; moduleData: ProbeModuleData; config: Record<string, unknown>;
}): ReducerResult;
export function editDevice(pm: ProbeMap, input: {
  originalName: string; newName: string; config: Record<string, unknown>;
}): ReducerResult;
export function deleteDevice(pm: ProbeMap, name: string): ProbeMap;  // never errors
```

- [ ] **Step 1: Write the failing tests**

```ts
import { addDevice, alnum, availableProbes, deleteDevice, devicePortOptions, editDevice, isVirtualDevice } from "./probeReducer";
import type { ProbeMap, ProbeModuleData } from "./probeTypes";

const ADS_MODULE: ProbeModuleData = {
  friendly_name: "ADS1115 Adafruit", filename: "ads1115_adafruit",
  device_specific: { ports: ["ADC0", "ADC1"], type: "adc", config: [] },
};
const empty = (): ProbeMap => ({ probe_devices: [], probe_info: [] });

it("alnum strips punctuation and spaces", () => {
  expect(alnum("ADS 1115!")).toBe("ADS1115");
  expect(alnum("---")).toBe("");
});

it("isVirtualDevice matches on module-key substring", () => {
  expect(isVirtualDevice({ device: "V", module: "virtual_average", module_filename: "", ports: [], config: {} })).toBe(true);
  expect(isVirtualDevice({ device: "A", module: "ads1115_adafruit", module_filename: "", ports: [], config: {} })).toBe(false);
});

it("addDevice copies ports from the manifest and stores sanitized name", () => {
  const r = addDevice(empty(), { name: "ADS 1115", module: "ads1115_adafruit", moduleData: ADS_MODULE, config: { i2c_bus_addr: "0x48" } });
  expect(r.ok).toBe(true);
  if (r.ok) {
    expect(r.probeMap.probe_devices[0].device).toBe("ADS1115");
    expect(r.probeMap.probe_devices[0].ports).toEqual(["ADC0", "ADC1"]);
    expect(r.probeMap.probe_devices[0].config.i2c_bus_addr).toBe("0x48");
  }
});

it("addDevice rejects a duplicate sanitized name", () => {
  const pm = (addDevice(empty(), { name: "ADS1115", module: "ads1115_adafruit", moduleData: ADS_MODULE, config: {} }) as { probeMap: ProbeMap }).probeMap;
  const r = addDevice(pm, { name: "ADS-1115", module: "ads1115_adafruit", moduleData: ADS_MODULE, config: {} });
  expect(r.ok).toBe(false);
});

it("addDevice rejects an all-punctuation name (empty after sanitize) [FIX 3]", () => {
  const r = addDevice(empty(), { name: "---", module: "ads1115_adafruit", moduleData: ADS_MODULE, config: {} });
  expect(r.ok).toBe(false);
});

it("editDevice keeps module/ports immutable and cascades the rename to probes [FIX 1]", () => {
  let pm = (addDevice(empty(), { name: "ADS1115", module: "ads1115_adafruit", moduleData: ADS_MODULE, config: {} }) as { probeMap: ProbeMap }).probeMap;
  pm = { ...pm, probe_info: [{ name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "ADS1115", port: "ADC0", profile: {} }] };
  const r = editDevice(pm, { originalName: "ADS1115", newName: "Main ADC", config: {} });
  expect(r.ok).toBe(true);
  if (r.ok) {
    expect(r.probeMap.probe_devices[0].device).toBe("MainADC");
    expect(r.probeMap.probe_devices[0].ports).toEqual(["ADC0", "ADC1"]); // immutable
    expect(r.probeMap.probe_info[0].device).toBe("MainADC");             // cascaded
  }
});

it("editDevice cascades a rename into virtual devices' own device key and deps", () => {
  const pm: ProbeMap = {
    probe_devices: [
      { device: "ADS1115", module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports: ["ADC0"], config: {} },
      { device: "Avg", module: "virtual_average", module_filename: "virtual_average", ports: ["VIRT0"], config: { probes_list: ["Grill"] } },
    ],
    probe_info: [{ name: "Grill", label: "Grill", type: "Aux", enabled: true, device: "ADS1115", port: "ADC0", profile: {} }],
  };
  const r = editDevice(pm, { originalName: "ADS1115", newName: "ADC One", config: {} });
  expect(r.ok).toBe(true);
  if (r.ok) expect(r.probeMap.probe_info[0].device).toBe("ADCOne");
});

it("deleteDevice cascades probe deletion AND scrubs virtual probes_list [FIX 4]", () => {
  const pm: ProbeMap = {
    probe_devices: [
      { device: "ADS1115", module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports: ["ADC0"], config: {} },
      { device: "Avg", module: "virtual_average", module_filename: "virtual_average", ports: ["VIRT0"], config: { probes_list: ["Grill", "Probe2"] } },
    ],
    probe_info: [
      { name: "Grill", label: "Grill", type: "Aux", enabled: true, device: "ADS1115", port: "ADC0", profile: {} },
      { name: "Probe-2", label: "Probe2", type: "Aux", enabled: true, device: "Avg", port: "VIRT0", profile: {} },
    ],
  };
  const out = deleteDevice(pm, "ADS1115");
  expect(out.probe_devices.map((d) => d.device)).toEqual(["Avg"]);
  expect(out.probe_info.map((p) => p.label)).toEqual(["Probe2"]);
  expect((out.probe_devices[0].config.probes_list as string[])).toEqual(["Probe2"]); // "Grill" scrubbed
});

it("devicePortOptions builds device:port pairs", () => {
  const pm: ProbeMap = { probe_devices: [{ device: "ADS1115", module: "m", module_filename: "m", ports: ["ADC0", "ADC1"], config: {} }], probe_info: [] };
  expect(devicePortOptions(pm)).toEqual([
    { value: "ADS1115:ADC0", label: "ADS1115 -> ADC0" },
    { value: "ADS1115:ADC1", label: "ADS1115 -> ADC1" },
  ]);
});

it("availableProbes returns probe labels", () => {
  const pm: ProbeMap = { probe_devices: [], probe_info: [{ name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "D", port: "ADC0", profile: {} }] };
  expect(availableProbes(pm)).toEqual(["Grill"]);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/helpers/wizard/probeReducer.devices.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the device half of `probeReducer.ts`**

```ts
import type { ProbeDevice, ProbeMap, ProbeModuleData } from "./probeTypes";

export type ReducerResult =
  | { ok: true; probeMap: ProbeMap }
  | { ok: false; error: string };

// Python str.isalnum() analog over ASCII device/probe names (matches
// "".join(c for c in name if c.isalnum()), blueprints/probeconfig/routes.py:57).
export function alnum(s: string): string {
  return Array.from(s).filter((c) => /[0-9A-Za-z]/.test(c)).join("");
}

// "virtual" in device["module"] -- a substring match on the module KEY (§3),
// NOT the manifest device_specific.type field.
export function isVirtualDevice(d: ProbeDevice): boolean {
  return d.module.includes("virtual");
}

export function availableProbes(pm: ProbeMap): string[] {
  return pm.probe_info.map((p) => p.label);
}

export function devicePortOptions(pm: ProbeMap): { value: string; label: string }[] {
  const opts: { value: string; label: string }[] = [];
  for (const d of pm.probe_devices) {
    for (const port of d.ports) {
      opts.push({ value: `${d.device}:${port}`, label: `${d.device} -> ${port}` });
    }
  }
  return opts;
}

export function addDevice(
  pm: ProbeMap,
  input: { name: string; module: string; moduleData: ProbeModuleData; config: Record<string, unknown> },
): ReducerResult {
  const deviceName = alnum(input.name);
  if (input.name === "") return { ok: false, error: "Device name is blank. Please enter a device name." };
  // FIX 3: an all-punctuation name sanitizes to "" -- reject (legacy checked
  // only the raw name and let an empty sanitized key through, §9).
  if (deviceName === "") return { ok: false, error: "Device name has no letters or numbers. Please enter a valid name." };
  if (pm.probe_devices.some((d) => d.device === deviceName)) {
    return { ok: false, error: "Device name already exists. Please choose a unique name." };
  }
  const device: ProbeDevice = {
    device: deviceName,
    module: input.module,
    module_filename: input.moduleData.filename ?? input.module,
    ports: [...input.moduleData.device_specific.ports],
    config: { ...input.config },
  };
  return { ok: true, probeMap: { ...pm, probe_devices: [...pm.probe_devices, device] } };
}

export function editDevice(
  pm: ProbeMap,
  input: { originalName: string; newName: string; config: Record<string, unknown> },
): ReducerResult {
  const newName = alnum(input.newName);
  if (input.newName === "") return { ok: false, error: "Device name is blank. Please enter a device name." };
  if (newName === "") return { ok: false, error: "Device name has no letters or numbers. Please enter a valid name." };
  const idx = pm.probe_devices.findIndex((d) => d.device === input.originalName);
  if (idx === -1) return { ok: false, error: "Device not found." };
  if (newName !== input.originalName && pm.probe_devices.some((d) => d.device === newName)) {
    return { ok: false, error: "Device name already exists. Please choose a unique name." };
  }
  const original = pm.probe_devices[idx];
  // module/module_filename/ports are immutable on edit (§2 edit_device).
  const updated: ProbeDevice = {
    device: newName,
    module: original.module,
    module_filename: original.module_filename,
    ports: [...original.ports],
    config: { ...input.config },
  };
  const probe_devices = pm.probe_devices.map((d, i) => {
    if (i === idx) return updated;
    // FIX 1 (virtual own device key): a virtual device may reference this
    // device -- but device references live only in probe_info and probes_list,
    // not on another device's own key, so nothing to rewrite on siblings here.
    return d;
  });
  // FIX 1: cascade the rename to every probe pointing at the old device name.
  const probe_info =
    newName === input.originalName
      ? pm.probe_info
      : pm.probe_info.map((p) => (p.device === input.originalName ? { ...p, device: newName } : p));
  return { ok: true, probeMap: { probe_devices, probe_info } };
}

export function deleteDevice(pm: ProbeMap, name: string): ProbeMap {
  const doomed = new Set(pm.probe_info.filter((p) => p.device === name).map((p) => p.label));
  const probe_devices = pm.probe_devices
    .filter((d) => d.device !== name)
    .map((d) => {
      // FIX 4: scrub the cascade-deleted probe labels out of any virtual
      // device's probes_list (legacy leaves them dangling, §2 delete_device).
      if (!isVirtualDevice(d)) return d;
      const list = (d.config.probes_list as string[] | undefined) ?? [];
      const scrubbed = list.filter((label) => !doomed.has(label));
      return scrubbed.length === list.length ? d : { ...d, config: { ...d.config, probes_list: scrubbed } };
    });
  const probe_info = pm.probe_info.filter((p) => p.device !== name);
  return { probe_devices, probe_info };
}
```

- [ ] **Step 4: Run tests**

Run: `cd web-react && bun run test src/helpers/wizard/probeReducer.devices.test.ts`
Expected: all pass.

- [ ] **Step 5: Typecheck + lint + coverage**

Run: `cd web-react && bun run typecheck && bunx biome check src/helpers/wizard/probeReducer.ts && bun run test --coverage src/helpers/wizard/probeReducer.devices.test.ts`
Expected: clean; `probeReducer.ts` ≥75% lines (device paths only for now; probe paths land in T7/T8).

- [ ] **Step 6: Commit**

```bash
cd web-react && bunx biome format --write src/helpers/wizard/probeReducer.ts src/helpers/wizard/probeReducer.devices.test.ts
```
```bash
jj desc -m "feat(web-react): probe reducer device actions (add/edit/delete) + fixes 1,3,4 + selectors"
```

---

## Task 7: React reducer — probe add/edit/delete + primary invariants + profile copy (non-virtual ordering)

**Files:**
- Modify: `web-react/src/helpers/wizard/probeReducer.ts`
- Test: `web-react/src/helpers/wizard/probeReducer.probes.test.ts`

**Interfaces:**
- Consumes: `probeReducer.ts` (T6), `ProbeProfile` from `probeTypes.ts`.
- Produces:

```ts
export interface ProbeInput {
  name: string; devicePort: string; type: ProbeType;
  profileId: string; enabled: boolean;
}
export function addProbe(pm: ProbeMap, profiles: ProbeProfile[], input: ProbeInput): ReducerResult;
export function editProbe(pm: ProbeMap, profiles: ProbeProfile[], originalLabel: string, input: ProbeInput): ReducerResult;
export function deleteProbe(pm: ProbeMap, label: string): ReducerResult;
```

**This task handles everything EXCEPT the virtual-port reposition branches (§3 branch 3a/3b), which are deferred to Task 8.** For an edit that resolves to an existing probe, do a straight in-place replace here; T8 layers in the virtual reposition on top. New adds append at the end (legacy branch, routes.py:360-363).

- [ ] **Step 1: Write the failing tests**

```ts
import { addProbe, deleteProbe, editProbe } from "./probeReducer";
import type { ProbeMap, ProbeProfile } from "./probeTypes";

const PROFILES: ProbeProfile[] = [{ A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" }];
const dev = (device: string, ports: string[]): ProbeMap["probe_devices"][number] => ({
  device, module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports, config: {},
});

it("addProbe builds label, splits device:port, value-copies the profile object", () => {
  const pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0"])], probe_info: [] };
  const r = addProbe(pm, PROFILES, { name: "Grill 1", devicePort: "ADS1115:ADC0", type: "Primary", profileId: "PT-1000", enabled: true });
  expect(r.ok).toBe(true);
  if (r.ok) {
    const p = r.probeMap.probe_info[0];
    expect(p.label).toBe("Grill1");
    expect(p.device).toBe("ADS1115");
    expect(p.port).toBe("ADC0");
    expect(p.profile).toEqual({ A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" });
  }
});

it("addProbe rejects an empty name", () => {
  const pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0"])], probe_info: [] };
  expect(addProbe(pm, PROFILES, { name: "", devicePort: "ADS1115:ADC0", type: "Food", profileId: "PT-1000", enabled: true }).ok).toBe(false);
});

it("addProbe blocks a second Primary", () => {
  let pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0", "ADC1"])], probe_info: [] };
  pm = (addProbe(pm, PROFILES, { name: "Grill", devicePort: "ADS1115:ADC0", type: "Primary", profileId: "PT-1000", enabled: true }) as { probeMap: ProbeMap }).probeMap;
  const r = addProbe(pm, PROFILES, { name: "Grill2", devicePort: "ADS1115:ADC1", type: "Primary", profileId: "PT-1000", enabled: true });
  expect(r.ok).toBe(false);
});

it("addProbe permits zero primaries transiently (Food first) — add is not primary-guarded", () => {
  const pm: ProbeMap = { probe_devices: [dev("ADS1115", ["ADC0"])], probe_info: [] };
  expect(addProbe(pm, PROFILES, { name: "Food1", devicePort: "ADS1115:ADC0", type: "Food", profileId: "PT-1000", enabled: true }).ok).toBe(true);
});

it("deleteProbe blocks removing the last Primary while other probes remain [FIX 2]", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0", "ADC1"])],
    probe_info: [
      { name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "ADS1115", port: "ADC0", profile: {} },
      { name: "Food", label: "Food", type: "Food", enabled: true, device: "ADS1115", port: "ADC1", profile: {} },
    ],
  };
  expect(deleteProbe(pm, "Grill").ok).toBe(false);       // would leave 1 probe, 0 primaries
  expect(deleteProbe(pm, "Food").ok).toBe(true);          // still 1 primary left
});

it("deleteProbe allows removing the only probe even if Primary (zero probes → zero primaries OK) [FIX 2]", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0"])],
    probe_info: [{ name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "ADS1115", port: "ADC0", profile: {} }],
  };
  expect(deleteProbe(pm, "Grill").ok).toBe(true);
});

it("deleteProbe scrubs the label from virtual probes_list", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0"]), { device: "Avg", module: "virtual_average", module_filename: "virtual_average", ports: ["VIRT0"], config: { probes_list: ["Grill", "Food"] } }],
    probe_info: [
      { name: "Grill", label: "Grill", type: "Aux", enabled: true, device: "ADS1115", port: "ADC0", profile: {} },
      { name: "Food", label: "Food", type: "Aux", enabled: true, device: "ADS1115", port: "ADC0", profile: {} },
    ],
  };
  const r = deleteProbe(pm, "Grill");
  expect(r.ok).toBe(true);
  if (r.ok) expect((r.probeMap.probe_devices[1].config.probes_list as string[])).toEqual(["Food"]);
});

it("editProbe type-change away from the only Primary is blocked while probes remain [FIX 2]", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0", "ADC1"])],
    probe_info: [
      { name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "ADS1115", port: "ADC0", profile: {} },
      { name: "Food", label: "Food", type: "Food", enabled: true, device: "ADS1115", port: "ADC1", profile: {} },
    ],
  };
  const r = editProbe(pm, PROFILES, "Grill", { name: "Grill", devicePort: "ADS1115:ADC0", type: "Food", profileId: "PT-1000", enabled: true });
  expect(r.ok).toBe(false);
});

it("editProbe in place replaces an ordinary (non-virtual) probe", () => {
  const pm: ProbeMap = {
    probe_devices: [dev("ADS1115", ["ADC0"])],
    probe_info: [{ name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "ADS1115", port: "ADC0", profile: {} }],
  };
  const r = editProbe(pm, PROFILES, "Grill", { name: "Grill Renamed", devicePort: "ADS1115:ADC0", type: "Primary", profileId: "PT-1000", enabled: false });
  expect(r.ok).toBe(true);
  if (r.ok) {
    expect(r.probeMap.probe_info).toHaveLength(1);
    expect(r.probeMap.probe_info[0].label).toBe("GrillRenamed");
    expect(r.probeMap.probe_info[0].enabled).toBe(false);
  }
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/helpers/wizard/probeReducer.probes.test.ts`
Expected: FAIL — exports missing.

- [ ] **Step 3: Implement**

Append to `probeReducer.ts`:

```ts
import type { ProbeProfile, ProbeType } from "./probeTypes";

export interface ProbeInput {
  name: string; devicePort: string; type: ProbeType; profileId: string; enabled: boolean;
}

function buildProbe(profiles: ProbeProfile[], input: ProbeInput): Probe {
  const [device, port] = input.devicePort.split(":");
  const matched = profiles.find((p) => p.id === input.profileId);
  return {
    name: input.name,
    label: alnum(input.name),
    type: input.type,
    enabled: input.enabled,
    device,
    port,
    // Value copy, not a FK -- probes carry a profile snapshot (§5).
    profile: matched ? { ...matched } : {},
  };
}

function primaryCount(list: Probe[]): number {
  return list.filter((p) => p.type === "Primary").length;
}

// FIX 2: zero primaries are valid ONLY when there are zero probes. Any result
// with >=1 probe and 0 primaries is rejected on a delete/type-change path.
function violatesPrimaryRule(list: Probe[]): boolean {
  return list.length > 0 && primaryCount(list) === 0;
}

export function addProbe(pm: ProbeMap, profiles: ProbeProfile[], input: ProbeInput): ReducerResult {
  if (input.name === "") return { ok: false, error: "Probe name is empty. Please enter a probe name." };
  const probe = buildProbe(profiles, input);
  // exactly-one-Primary: a NEW Primary conflicts with any existing Primary.
  if (probe.type === "Primary" && primaryCount(pm.probe_info) > 0) {
    return { ok: false, error: "There must be only one Primary probe. Change the existing Primary first." };
  }
  if (pm.probe_info.some((p) => p.label === probe.label)) {
    return { ok: false, error: "Probe name is already used or too similar to another. Choose a different name." };
  }
  // New adds append at the end (legacy branch, routes.py:360-363).
  return { ok: true, probeMap: { ...pm, probe_info: [...pm.probe_info, probe] } };
}

export function editProbe(
  pm: ProbeMap, profiles: ProbeProfile[], originalLabel: string, input: ProbeInput,
): ReducerResult {
  if (input.name === "") return { ok: false, error: "Probe name is empty. Please enter a probe name." };
  const found = pm.probe_info.findIndex((p) => p.label === originalLabel);
  if (found === -1) return { ok: false, error: "Error editing probe. Please try again." };
  const probe = buildProbe(profiles, input);
  // exactly-one-Primary, skipping the probe being edited.
  if (probe.type === "Primary") {
    const otherPrimary = pm.probe_info.some((p, i) => i !== found && p.type === "Primary");
    if (otherPrimary) return { ok: false, error: "There must be only one Primary probe. Change the existing Primary first." };
  }
  // A rename must not collide with a DIFFERENT existing probe.
  if (probe.label !== originalLabel && pm.probe_info.some((p, i) => i !== found && p.label === probe.label)) {
    return { ok: false, error: "Probe name is already used or too similar to another. Choose a different name." };
  }
  // Rename cascade into virtual probes_list (§3 pre-step): rewrite the old
  // label to the new one everywhere it is consumed.
  const probe_devices =
    probe.label === originalLabel
      ? pm.probe_devices
      : pm.probe_devices.map((d) => {
          if (!isVirtualDevice(d)) return d;
          const list = (d.config.probes_list as string[] | undefined) ?? [];
          if (!list.includes(originalLabel)) return d;
          return { ...d, config: { ...d.config, probes_list: list.map((l) => (l === originalLabel ? probe.label : l)) } };
        });
  // Non-virtual ordering: straight in-place replace (§3 branch 3c). Virtual
  // reposition (branches 3a/3b) is layered on in Task 8.
  const probe_info = pm.probe_info.map((p, i) => (i === found ? probe : p));
  if (violatesPrimaryRule(probe_info)) {
    return { ok: false, error: "At least one probe must be Primary while probes are configured." };
  }
  return { ok: true, probeMap: { probe_devices, probe_info } };
}

export function deleteProbe(pm: ProbeMap, label: string): ReducerResult {
  const probe_info = pm.probe_info.filter((p) => p.label !== label);
  if (violatesPrimaryRule(probe_info)) {
    return { ok: false, error: "At least one probe must be Primary while probes are configured. Reassign Primary before deleting." };
  }
  const probe_devices = pm.probe_devices.map((d) => {
    if (!isVirtualDevice(d)) return d;
    const list = (d.config.probes_list as string[] | undefined) ?? [];
    if (!list.includes(label)) return d;
    return { ...d, config: { ...d.config, probes_list: list.filter((l) => l !== label) } };
  });
  return { ok: true, probeMap: { probe_devices, probe_info } };
}
```

- [ ] **Step 4: Run tests + typecheck + lint + coverage**

Run: `cd web-react && bun run test src/helpers/wizard/probeReducer.probes.test.ts && bun run typecheck && bunx biome check src/helpers/wizard/probeReducer.ts`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
cd web-react && bunx biome format --write src/helpers/wizard/probeReducer.ts src/helpers/wizard/probeReducer.probes.test.ts
```
```bash
jj desc -m "feat(web-react): probe reducer add/edit/delete + one-Primary + zero-Primary guard + profile copy"
```

---

## Task 8: React reducer — virtual-port reposition (§3 branches 3a/3b), exact

**Files:**
- Modify: `web-react/src/helpers/wizard/probeReducer.ts` (extend `editProbe`)
- Test: `web-react/src/helpers/wizard/probeReducer.reposition.test.ts`

**Interfaces:**
- Consumes: `editProbe` from T7.
- Produces: `editProbe` now reproduces the two virtual-reposition branches from `blueprints/probeconfig/routes.py:321-355`:
  - **3a** (`"VIRT" in port`): the edited probe IS a virtual device's output entry. Walk `probe_info` backward; if an input probe is found at a higher index than the entry's own slot, relocate the entry to immediately after that input probe (so it sorts after every input).
  - **3b** (probe feeds a virtual device): forward-scan; if the consuming virtual device's own entry is found before the probe's slot, insert the edited probe immediately before it.
  - **3c** (neither): in-place replace (already implemented in T7).

**Fidelity note:** the invariant is *virtual entry sorts AFTER its inputs; input entries sort BEFORE the virtual entry they feed*. Reproduce the backward/forward scan and index arithmetic exactly (legacy `insert(probe+1)` then `pop(found)` for 3a; `insert(index)` then `pop(found+1)` for 3b). New adds still append at the end (T7) — do NOT eagerly reorder on add (§9).

- [ ] **Step 1: Write the failing tests**

```ts
import { editProbe } from "./probeReducer";
import type { ProbeMap, ProbeProfile } from "./probeTypes";

const P: ProbeProfile[] = [];
const aux = (label: string, device: string, port: string): ProbeMap["probe_info"][number] => ({
  name: label, label, type: "Aux", enabled: true, device, port, profile: {},
});
const adc = (device: string, ports: string[]): ProbeMap["probe_devices"][number] => ({
  device, module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports, config: {},
});
const virt = (device: string, inputs: string[]): ProbeMap["probe_devices"][number] => ({
  device, module: "virtual_average", module_filename: "virtual_average", ports: ["VIRT0"], config: { probes_list: inputs },
});

// 3a: the virtual entry currently sorts BEFORE one of its inputs; editing it
// relocates it to immediately after the last input found in the backward scan.
it("editing a virtual (VIRT) probe relocates it after its input probes [3a]", () => {
  const pm: ProbeMap = {
    probe_devices: [adc("ADS", ["ADC0", "ADC1"]), virt("Avg", ["Grill", "Food"])],
    probe_info: [
      aux("Avg", "Avg", "VIRT0"),     // index 0 -- BEFORE its inputs (needs fixing)
      aux("Grill", "ADS", "ADC0"),    // index 1
      aux("Food", "ADS", "ADC1"),     // index 2
    ],
  };
  const r = editProbe(pm, P, "Avg", { name: "Avg", devicePort: "Avg:VIRT0", type: "Aux", profileId: "", enabled: true });
  expect(r.ok).toBe(true);
  if (r.ok) {
    const labels = r.probeMap.probe_info.map((p) => p.label);
    // Avg must now sort after Food (its last-scanned input).
    expect(labels.indexOf("Avg")).toBeGreaterThan(labels.indexOf("Food"));
    expect(r.probeMap.probe_info).toHaveLength(3);
  }
});

it("editing a virtual probe already correctly placed leaves order unchanged [3a in-place]", () => {
  const pm: ProbeMap = {
    probe_devices: [adc("ADS", ["ADC0"]), virt("Avg", ["Grill"])],
    probe_info: [aux("Grill", "ADS", "ADC0"), aux("Avg", "Avg", "VIRT0")],
  };
  const r = editProbe(pm, P, "Avg", { name: "Avg", devicePort: "Avg:VIRT0", type: "Aux", profileId: "", enabled: true });
  expect(r.ok).toBe(true);
  if (r.ok) expect(r.probeMap.probe_info.map((p) => p.label)).toEqual(["Grill", "Avg"]);
});

// 3b: an input probe currently sorts AFTER the virtual device that consumes it;
// editing the input moves it to immediately before the virtual entry.
it("editing an input probe moves it before the virtual entry that consumes it [3b]", () => {
  const pm: ProbeMap = {
    probe_devices: [adc("ADS", ["ADC0"]), virt("Avg", ["Grill"])],
    probe_info: [
      aux("Avg", "Avg", "VIRT0"),   // index 0 -- virtual entry
      aux("Grill", "ADS", "ADC0"),  // index 1 -- its input, AFTER it (needs fixing)
    ],
  };
  const r = editProbe(pm, P, "Grill", { name: "Grill", devicePort: "ADS:ADC0", type: "Aux", profileId: "", enabled: true });
  expect(r.ok).toBe(true);
  if (r.ok) {
    const labels = r.probeMap.probe_info.map((p) => p.label);
    expect(labels.indexOf("Grill")).toBeLessThan(labels.indexOf("Avg"));
    expect(r.probeMap.probe_info).toHaveLength(2);
  }
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/helpers/wizard/probeReducer.reposition.test.ts`
Expected: FAIL — current `editProbe` does a plain in-place replace, so the virtual entry is not relocated (3a/3b order assertions fail).

- [ ] **Step 3: Implement the reposition branches in `editProbe`**

Replace the "Non-virtual ordering: straight in-place replace" section of `editProbe` (from T7) with the full three-way reposition. Reproduces `blueprints/probeconfig/routes.py:321-358` exactly, operating on a mutable copy of `probe_info`:

```ts
  // Build the working probe_info as a mutable copy; the reposition branches
  // splice it in place, mirroring legacy list.insert/pop index arithmetic.
  const info = [...pm.probe_info];

  if (probe.port.includes("VIRT")) {
    // 3a: this probe IS a virtual device's output entry. Ensure its config
    // entry sorts after every one of that device's input probes.
    const owning = probe_devices.find((d) => isVirtualDevice(d) && d.device === probe.device);
    const inputProbes = (owning?.config.probes_list as string[] | undefined) ?? [];
    for (let i = info.length - 1; i >= 0; i--) {
      if (i === found) {
        // Own entry reached first (by index, not label) -- already correct.
        info[found] = probe;
        break;
      }
      if (inputProbes.includes(info[i].label)) {
        // Hit an input probe at a higher index -- relocate right after it.
        info.splice(i + 1, 0, probe);
        info.splice(found, 1);
        break;
      }
    }
  } else {
    // Does this probe feed any virtual device? (§3 in_virtual_device)
    const consuming = probe_devices
      .filter((d) => isVirtualDevice(d) && ((d.config.probes_list as string[] | undefined) ?? []).includes(probe.label))
      .map((d) => d.device);
    if (consuming.length > 0) {
      // 3b: ensure this input's entry sorts before the consuming virtual entry.
      for (let i = 0; i < info.length; i++) {
        if (info[i].label === originalLabel) {
          info[i] = probe; // own slot reached first -- already correct.
          break;
        }
        if (consuming.includes(info[i].device)) {
          info.splice(i, 0, probe);   // insert before the virtual entry
          info.splice(found + 1, 1);  // +1: the insert shifted the stale copy up
          break;
        }
      }
    } else {
      // 3c: ordinary probe -- in-place replace.
      info[found] = probe;
    }
  }

  const probe_info = info;
  if (violatesPrimaryRule(probe_info)) {
    return { ok: false, error: "At least one probe must be Primary while probes are configured." };
  }
  return { ok: true, probeMap: { probe_devices, probe_info } };
```

Delete the old `const probe_info = pm.probe_info.map(...)` line this replaces.

- [ ] **Step 4: Run tests (reposition + regression)**

Run: `cd web-react && bun run test src/helpers/wizard/probeReducer.reposition.test.ts src/helpers/wizard/probeReducer.probes.test.ts`
Expected: all pass (the T7 non-virtual `editProbe` case still passes via branch 3c).

- [ ] **Step 5: Typecheck + lint + coverage**

Run: `cd web-react && bun run typecheck && bunx biome check src/helpers/wizard/probeReducer.ts && bun run test --coverage src/helpers/wizard/probeReducer.probes.test.ts src/helpers/wizard/probeReducer.devices.test.ts src/helpers/wizard/probeReducer.reposition.test.ts`
Expected: `probeReducer.ts` ≥75% lines (aim near 100%).

- [ ] **Step 6: Commit**

```bash
cd web-react && bunx biome format --write src/helpers/wizard/probeReducer.ts src/helpers/wizard/probeReducer.reposition.test.ts
```
```bash
jj desc -m "feat(web-react): probe reducer virtual-port reposition (branches 3a/3b, exact)"
```

---

## Task 9: React — device config field widget + BT/ThermoWorks pickers

**Files:**
- Create: `web-react/src/components/wizard/probes/DeviceConfigField.tsx`, `web-react/src/components/wizard/probes/BluetoothPicker.tsx`, `web-react/src/components/wizard/probes/ThermoworksPicker.tsx`
- Test: `web-react/src/components/wizard/probes/DeviceConfigField.test.tsx`, `BluetoothPicker.test.tsx`, `ThermoworksPicker.test.tsx`

**Interfaces:**
- Consumes: `ProbeConfigField`, `BtScanRow`, `ThermoworksRow` (`probeTypes.ts`); `scanBluetooth`, `scanThermoworks`, `scan` (`wizardApi.ts`); existing `I2cBusPicker`, `UsbSerialPicker`, `SelectField` (`components/wizard/fields/*`), `DiscoveryPanel`.
- Produces:

```ts
export interface DeviceConfigFieldProps {
  field: ProbeConfigField;
  value: unknown;
  allValues: Record<string, unknown>;   // for i2c_bus_num's paired i2c_bus_kind
  availableProbes: string[];            // for probes_list multi-select
  baseUrl: string;
  onChange: (label: string, value: unknown) => void;
}
export function DeviceConfigField(props: DeviceConfigFieldProps): JSX.Element | null;
```

**Field dispatch (§6):** `hidden` fields render `null` — EXCEPT `label === "device_serial"` (rendered via `ThermoworksPicker`, stays visible despite `hidden`). Then by `type`: `int`/`float` → number input (`min`/`max`/`step`); `list` → `SelectField` from `list_values`/`list_labels`; `string` → text; `probes_list` → native `<select multiple>` of `availableProbes`; `i2c_bus_num` → `I2cBusPicker` (kind = `allValues["i2c_bus_kind"]`); `bt_address` → `BluetoothPicker`; `usb_serial_device` → `UsbSerialPicker`.

- [ ] **Step 1: Write the failing tests** (representative — cover each branch)

`DeviceConfigField.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { DeviceConfigField } from "./DeviceConfigField";
import type { ProbeConfigField } from "../../../helpers/wizard/probeTypes";

const base = { friendly_name: "F", description: "" };

it("renders an int field and emits numeric-string changes", () => {
  const f: ProbeConfigField = { ...base, label: "ADC0_rd", type: "int", min: 1, step: 1 };
  const onChange = rs.fn();
  render(<DeviceConfigField field={f} value={10000} allValues={{}} availableProbes={[]} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "5000" } });
  expect(onChange).toHaveBeenCalledWith("ADC0_rd", "5000");
});

it("hidden field renders null", () => {
  const f: ProbeConfigField = { ...base, label: "transient", type: "list", hidden: true, list_values: ["False"], list_labels: ["Fixed"] };
  const { container } = render(<DeviceConfigField field={f} value="False" allValues={{}} availableProbes={[]} baseUrl="" onChange={rs.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

it("device_serial stays visible despite hidden (Test Connection widget)", () => {
  const f: ProbeConfigField = { ...base, label: "device_serial", type: "string", hidden: true };
  render(<DeviceConfigField field={f} value="" allValues={{ email: "a@b.c", password: "x" }} availableProbes={[]} baseUrl="" onChange={rs.fn()} />);
  expect(screen.getByRole("button", { name: /test connection/i })).toBeInTheDocument();
});

it("probes_list renders a multi-select of availableProbes", () => {
  const f: ProbeConfigField = { ...base, label: "probes_list", type: "probes_list" };
  render(<DeviceConfigField field={f} value={["Grill"]} allValues={{}} availableProbes={["Grill", "Food"]} baseUrl="" onChange={rs.fn()} />);
  expect(screen.getByRole("listbox")).toBeInTheDocument();
  expect(screen.getAllByRole("option")).toHaveLength(2);
});
```

`BluetoothPicker.test.tsx` and `ThermoworksPicker.test.tsx`: mock `scanBluetooth`/`scanThermoworks` (`rs.mock("../../../helpers/wizard/wizardApi")`), click Scan/Test-Connection, assert the returned rows render as pick buttons and picking calls `onChange`. (Mirror `web-react/src/components/wizard/fields/I2cBusPicker.test.tsx`.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/components/wizard/probes/`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `BluetoothPicker.tsx`** (mirror `fields/I2cBusPicker.tsx` — text input + Scan button + result rows; rows come from `scanBluetooth`; each row shows `name`/`hw_id`/`info`, picking sets `hw_id`):

```tsx
import { useState } from "react";
import { scanBluetooth } from "../../../helpers/wizard/wizardApi";
import type { BtScanRow } from "../../../helpers/wizard/probeTypes";

export interface BluetoothPickerProps {
  label: string; value: string; baseUrl: string; onChange: (value: string) => void;
}

export function BluetoothPicker({ label, value, baseUrl, onChange }: BluetoothPickerProps) {
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<BtScanRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleScan() {
    setLoading(true);
    try {
      const r = await scanBluetooth(baseUrl);
      setRows(r.rows);
      setError(r.error);
    } finally {
      setLoading(false);
    }
  }

  return (
    <label className="pf-field pf-field-column">
      <span className="pf-field-label">{label}</span>
      <input className="pf-input" type="text" value={value} onChange={(e) => onChange(e.target.value)} />
      <span className="pf-field-hint">Turn on your bluetooth device then click Scan.</span>
      <button type="button" onClick={() => void handleScan()} disabled={loading}>
        {loading ? "Scanning…" : "Scan"}
      </button>
      {error && <p role="alert">{error}</p>}
      {rows && rows.length > 0 && (
        <div className="pf-discovery-group-items">
          {rows.map((row) => (
            <button type="button" key={row.hw_id} onClick={() => onChange(row.hw_id)}>
              {row.name} [{row.hw_id}] {row.info}
            </button>
          ))}
        </div>
      )}
    </label>
  );
}
```

- [ ] **Step 4: Implement `ThermoworksPicker.tsx`** (Test-Connection button; reads `email`/`password` from `allValues`; on success calls `onChange("device_serial", serial)` and, per row, exposes `num_channels`). Signature:

```tsx
import { useState } from "react";
import { scanThermoworks } from "../../../helpers/wizard/wizardApi";
import type { ThermoworksRow } from "../../../helpers/wizard/probeTypes";

export interface ThermoworksPickerProps {
  value: string; email: string; password: string; baseUrl: string;
  onPick: (row: ThermoworksRow) => void;
}

export function ThermoworksPicker({ value, email, password, baseUrl, onPick }: ThermoworksPickerProps) {
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<ThermoworksRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleTest() {
    setLoading(true);
    try {
      const r = await scanThermoworks(baseUrl, email, password);
      setRows(r.rows);
      setError(r.error);
    } finally {
      setLoading(false);
    }
  }

  return (
    <label className="pf-field pf-field-column">
      <span className="pf-field-label">Device Serial</span>
      <input className="pf-input" type="text" value={value} readOnly />
      <button type="button" onClick={() => void handleTest()} disabled={loading}>
        {loading ? "Connecting…" : "Test Connection"}
      </button>
      {error && <p role="alert">{error}</p>}
      {rows?.map((row) => (
        <button type="button" key={row.serial} onClick={() => onPick(row)}>
          {row.label} ({row.serial}) — {row.num_channels} probes
        </button>
      ))}
    </label>
  );
}
```

- [ ] **Step 5: Implement `DeviceConfigField.tsx`** (the dispatch):

```tsx
import type { ProbeConfigField } from "../../../helpers/wizard/probeTypes";
import { scan } from "../../../helpers/wizard/wizardApi";
import { I2cBusPicker } from "../fields/I2cBusPicker";
import { SelectField } from "../fields/SelectField";
import { UsbSerialPicker } from "../fields/UsbSerialPicker";
import { BluetoothPicker } from "./BluetoothPicker";
import { ThermoworksPicker } from "./ThermoworksPicker";

export interface DeviceConfigFieldProps {
  field: ProbeConfigField;
  value: unknown;
  allValues: Record<string, unknown>;
  availableProbes: string[];
  baseUrl: string;
  onChange: (label: string, value: unknown) => void;
}

export function DeviceConfigField({ field, value, allValues, availableProbes, baseUrl, onChange }: DeviceConfigFieldProps) {
  const set = (v: unknown) => onChange(field.label, v);

  // device_serial: hidden in the manifest but always shown because it hosts
  // the Test Connection button (§6 special case).
  if (field.label === "device_serial") {
    return (
      <ThermoworksPicker
        value={String(value ?? "")}
        email={String(allValues.email ?? "")}
        password={String(allValues.password ?? "")}
        baseUrl={baseUrl}
        onPick={(row) => {
          onChange("device_serial", row.serial);
          onChange("num_probes", row.num_channels);
        }}
      />
    );
  }
  if (field.hidden) return null;

  const dep = { friendly_name: field.friendly_name, description: field.description, settings: [] as string[] };

  switch (field.type) {
    case "int":
    case "float":
      return (
        <label className="pf-field">
          <span className="pf-field-label">{field.friendly_name}</span>
          <input
            className="pf-input" type="number"
            value={String(value ?? field.default ?? "")}
            min={field.min} max={field.max === "" ? undefined : field.max} step={field.step}
            onChange={(e) => set(e.target.value)}
          />
        </label>
      );
    case "list": {
      const options = (field.list_values ?? []).map((v, i) => ({ value: String(v), label: field.list_labels?.[i] ?? String(v) }));
      return <SelectField label={field.friendly_name} value={String(value ?? "")} options={options} onChange={set} />;
    }
    case "probes_list": {
      const selected = (value as string[] | undefined) ?? [];
      return (
        <label className="pf-field">
          <span className="pf-field-label">{field.friendly_name}</span>
          <select
            className="pf-input" multiple value={selected}
            onChange={(e) => set(Array.from(e.target.selectedOptions, (o) => o.value))}
          >
            {availableProbes.map((label) => (<option key={label} value={label}>{label}</option>))}
          </select>
        </label>
      );
    }
    case "i2c_bus_num":
      return (
        <I2cBusPicker
          dep={dep} value={String(value ?? "")}
          kindValue={String(allValues.i2c_bus_kind ?? "")}
          onChange={set}
          onScan={() => scan(baseUrl, { kind: String(allValues.i2c_bus_kind ?? "") })}
        />
      );
    case "bt_address":
      return <BluetoothPicker label={field.friendly_name} value={String(value ?? "")} baseUrl={baseUrl} onChange={set} />;
    case "usb_serial_device":
      return <UsbSerialPicker dep={dep} value={String(value ?? "")} onChange={set} onScan={() => scan(baseUrl, { kind: "usb_serial" })} />;
    default:
      return (
        <label className="pf-field">
          <span className="pf-field-label">{field.friendly_name}</span>
          <input className="pf-input" type="text" value={String(value ?? "")} onChange={(e) => set(e.target.value)} />
        </label>
      );
  }
}
```

- [ ] **Step 6: Run tests + typecheck + lint + coverage**

Run: `cd web-react && bun run test src/components/wizard/probes/ && bun run typecheck && bunx biome check src/components/wizard/probes`
Expected: pass; each file ≥75% lines (add a pick-a-row test per picker and an i2c/usb branch test to clear the gate).

- [ ] **Step 7: Commit**

```bash
cd web-react && bunx biome format --write src/components/wizard/probes
```
```bash
jj desc -m "feat(web-react): DeviceConfigField dispatch + Bluetooth/ThermoWorks pickers"
```

---

## Task 10: React — DeviceForm + DevicesCard

**Files:**
- Create: `web-react/src/components/wizard/probes/DeviceForm.tsx`, `web-react/src/components/wizard/probes/DevicesCard.tsx`
- Test: `web-react/src/components/wizard/probes/DeviceForm.test.tsx`, `DevicesCard.test.tsx`

**Interfaces:**
- Consumes: `DeviceConfigField` (T9); reducer `addDevice`/`editDevice`/`deleteDevice`/`availableProbes` (T6); types from `probeTypes.ts`; `ProbeModuleData` map from `WizardState.modules_metadata.probes`.
- Produces:

```ts
export interface DeviceFormProps {
  mode: "add" | "edit";
  moduleData: ProbeModuleData;
  values: Record<string, unknown>;   // device config being edited
  nameValue: string;
  availableProbes: string[];
  baseUrl: string;
  onNameChange: (name: string) => void;
  onFieldChange: (label: string, value: unknown) => void;
  onSubmit: () => void;
  onCancel: () => void;
  error: string | null;
}
export function DeviceForm(props: DeviceFormProps): JSX.Element;

export interface DevicesCardProps {
  probeMap: ProbeMap;
  modules: Record<string, ProbeModuleData>;
  baseUrl: string;
  onChange: (next: ProbeMap) => void;
}
export function DevicesCard(props: DevicesCardProps): JSX.Element;
```

**DeviceForm** renders the module header (image, friendly_name, description, optional notes) then one `DeviceConfigField` per `moduleData.device_specific.config` entry, plus a required "Unique Device Name" input. **Edit-mode backfill (§2):** before rendering in edit mode, fill any manifest field absent from `values` with its manifest `default` (do this in `DevicesCard` when opening the edit form, not inside the reducer). Submit calls `onSubmit`; the parent runs the reducer and surfaces `error`.

**DevicesCard** owns the local add/edit UI state (which form is open, the in-progress name + config values, the reducer error) and the device table (thumbnail / name / module friendly_name / Edit · Delete + an Add affordance whose module `<select>` seeds `nameValue` from `alnum(friendly_name)` and `values` from each field's `default`, with `probes_list` defaulting to `[]`, per §2 add_config). On submit it calls the reducer and, on `ok`, `onChange(result.probeMap)` and closes the form; on failure it sets the error.

- [ ] **Step 1: Write the failing tests** (behavior-level)

`DevicesCard.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { DevicesCard } from "./DevicesCard";
import type { ProbeMap, ProbeModuleData } from "../../../helpers/wizard/probeTypes";

const modules: Record<string, ProbeModuleData> = {
  ads1115_adafruit: {
    friendly_name: "ADS1115 Adafruit", filename: "ads1115_adafruit", image: "ads1115.png",
    device_specific: { ports: ["ADC0", "ADC1"], type: "adc", config: [
      { label: "i2c_bus_addr", friendly_name: "I2C Bus Address", type: "list", default: "0x48", list_values: ["0x48"], list_labels: ["0x48"] },
    ] },
  },
};
const emptyMap: ProbeMap = { probe_devices: [], probe_info: [] };

it("lists existing devices with module name", () => {
  const pm: ProbeMap = { probe_devices: [{ device: "ADS1115", module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports: ["ADC0"], config: {} }], probe_info: [] };
  render(<DevicesCard probeMap={pm} modules={modules} baseUrl="" onChange={rs.fn()} />);
  expect(screen.getByText("ADS1115")).toBeInTheDocument();
  expect(screen.getByText("ADS1115 Adafruit")).toBeInTheDocument();
});

it("adding a device runs the reducer and emits the new probe_map", () => {
  const onChange = rs.fn();
  render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /add device/i }));
  fireEvent.change(screen.getByLabelText(/module/i), { target: { value: "ads1115_adafruit" } });
  // default name pre-filled from friendly_name -> "ADS1115Adafruit"
  fireEvent.click(screen.getByRole("button", { name: /^add$|save/i }));
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
    probe_devices: expect.arrayContaining([expect.objectContaining({ device: "ADS1115Adafruit" })]),
  }));
});

it("surfaces a duplicate-name error without emitting", () => {
  const pm: ProbeMap = { probe_devices: [{ device: "ADS1115Adafruit", module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports: ["ADC0"], config: {} }], probe_info: [] };
  const onChange = rs.fn();
  render(<DevicesCard probeMap={pm} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /add device/i }));
  fireEvent.change(screen.getByLabelText(/module/i), { target: { value: "ads1115_adafruit" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$|save/i }));
  expect(screen.getByRole("alert")).toHaveTextContent(/already exists/i);
  expect(onChange).not.toHaveBeenCalled();
});

it("deleting a device emits the cascade-updated map", () => {
  const pm: ProbeMap = { probe_devices: [{ device: "ADS1115", module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports: ["ADC0"], config: {} }], probe_info: [] };
  const onChange = rs.fn();
  render(<DevicesCard probeMap={pm} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ probe_devices: [] }));
});
```

`DeviceForm.test.tsx`: render in add mode with the ADS module, assert the name input + one config field render and that changing the name calls `onNameChange`; render in edit mode and assert a value from `values` shows.

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/components/wizard/probes/DevicesCard.test.tsx src/components/wizard/probes/DeviceForm.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `DeviceForm.tsx`** — module header + `DeviceConfigField` per config entry + required name input. Skeleton:

```tsx
import type { ProbeModuleData } from "../../../helpers/wizard/probeTypes";
import { DeviceConfigField } from "./DeviceConfigField";

export interface DeviceFormProps {
  mode: "add" | "edit";
  moduleData: ProbeModuleData;
  values: Record<string, unknown>;
  nameValue: string;
  availableProbes: string[];
  baseUrl: string;
  onNameChange: (name: string) => void;
  onFieldChange: (label: string, value: unknown) => void;
  onSubmit: () => void;
  onCancel: () => void;
  error: string | null;
}

export function DeviceForm(props: DeviceFormProps) {
  const { moduleData, values, nameValue, availableProbes, baseUrl } = props;
  return (
    <div className="pf-device-form" role="dialog" aria-label={`${props.mode} device`}>
      {moduleData.image && <img className="pf-module-image" src={moduleData.image} alt={moduleData.friendly_name} />}
      <h3 className="pf-module-name">{moduleData.friendly_name}</h3>
      {moduleData.description && <p className="pf-module-description">{moduleData.description}</p>}
      {props.error && <p role="alert">{props.error}</p>}
      {moduleData.device_specific.config.map((field) => (
        <DeviceConfigField
          key={field.label} field={field} value={values[field.label]}
          allValues={values} availableProbes={availableProbes}
          baseUrl={baseUrl} onChange={props.onFieldChange}
        />
      ))}
      <label className="pf-field">
        <span className="pf-field-label">Unique Device Name</span>
        <input className="pf-input" type="text" required value={nameValue} onChange={(e) => props.onNameChange(e.target.value)} />
      </label>
      <div className="pf-form-actions">
        <button type="button" className="pf-btn" onClick={props.onCancel}>Cancel</button>
        <button type="button" className="pf-btn pf-btn-primary" onClick={props.onSubmit}>
          {props.mode === "add" ? "Add" : "Save"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement `DevicesCard.tsx`** — table + Add `<select>` + open/submit/cancel wiring. Key logic (defaults from manifest on add; backfill on edit; reducer on submit):

```tsx
import { useState } from "react";
import { addDevice, alnum, availableProbes, deleteDevice, editDevice } from "../../../helpers/wizard/probeReducer";
import type { ProbeMap, ProbeModuleData } from "../../../helpers/wizard/probeTypes";
import { DeviceForm } from "./DeviceForm";

export interface DevicesCardProps {
  probeMap: ProbeMap;
  modules: Record<string, ProbeModuleData>;
  baseUrl: string;
  onChange: (next: ProbeMap) => void;
}

interface FormState {
  mode: "add" | "edit";
  module: string;
  originalName: string;   // edit only
  name: string;
  values: Record<string, unknown>;
}

function defaultsFor(mod: ProbeModuleData): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of mod.device_specific.config) {
    out[f.label] = f.type === "probes_list" ? [] : (f.default ?? "");
  }
  return out;
}

export function DevicesCard({ probeMap, modules, baseUrl, onChange }: DevicesCardProps) {
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const probes = availableProbes(probeMap);

  function openAdd(module: string) {
    const mod = modules[module];
    if (!mod) return;
    setError(null);
    setForm({ mode: "add", module, originalName: "", name: alnum(mod.friendly_name), values: defaultsFor(mod) });
  }

  function openEdit(device: ProbeMap["probe_devices"][number]) {
    const mod = modules[device.module];
    if (!mod) return;
    // §2 backfill: manifest fields absent from saved config get their default.
    const values = { ...defaultsFor(mod), ...device.config };
    setError(null);
    setForm({ mode: "edit", module: device.module, originalName: device.device, name: device.device, values });
  }

  function submit() {
    if (!form) return;
    const mod = modules[form.module];
    const result =
      form.mode === "add"
        ? addDevice(probeMap, { name: form.name, module: form.module, moduleData: mod, config: form.values })
        : editDevice(probeMap, { originalName: form.originalName, newName: form.name, config: form.values });
    if (result.ok) {
      onChange(result.probeMap);
      setForm(null);
      setError(null);
    } else {
      setError(result.error);
    }
  }

  return (
    <section className="pf-probes-card" aria-label="Probe devices">
      <h3>Devices</h3>
      <table className="pf-probes-table">
        <tbody>
          {probeMap.probe_devices.map((d) => (
            <tr key={d.device}>
              <td>{modules[d.module]?.image && <img src={modules[d.module].image} alt="" width={48} height={48} />}</td>
              <td>{d.device}</td>
              <td>{modules[d.module]?.friendly_name ?? d.module}</td>
              <td>
                <button type="button" onClick={() => openEdit(d)}>Edit</button>
                <button type="button" onClick={() => onChange(deleteDevice(probeMap, d.device))}>Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {!form && (
        <label className="pf-field">
          <span className="pf-field-label">Add Device — Module</span>
          <select className="pf-input" defaultValue="" onChange={(e) => e.target.value && openAdd(e.target.value)} aria-label="Add device module">
            <option value="">— add device —</option>
            {Object.entries(modules).map(([key, mod]) => (<option key={key} value={key}>{mod.friendly_name}</option>))}
          </select>
        </label>
      )}

      {form && (
        <DeviceForm
          mode={form.mode} moduleData={modules[form.module]} values={form.values} nameValue={form.name}
          availableProbes={probes} baseUrl={baseUrl} error={error}
          onNameChange={(name) => setForm({ ...form, name })}
          onFieldChange={(label, value) => setForm({ ...form, values: { ...form.values, [label]: value } })}
          onSubmit={submit} onCancel={() => { setForm(null); setError(null); }}
        />
      )}
    </section>
  );
}
```

(The test's "Add device" button-name expectation: the Add affordance is the labeled `<select>`; adjust the test to select via `getByLabelText(/add device module/i)` if needed — keep test and component in sync.)

- [ ] **Step 5: Run tests + typecheck + lint + coverage**

Run: `cd web-react && bun run test src/components/wizard/probes/DevicesCard.test.tsx src/components/wizard/probes/DeviceForm.test.tsx && bun run typecheck && bunx biome check src/components/wizard/probes`
Expected: pass; both files ≥75% lines.

- [ ] **Step 6: Commit**

```bash
cd web-react && bunx biome format --write src/components/wizard/probes
```
```bash
jj desc -m "feat(web-react): DevicesCard + DeviceForm (manifest-driven, edit backfill, reducer-wired)"
```

---

## Task 11: React — PortForm + PortsCard

**Files:**
- Create: `web-react/src/components/wizard/probes/PortForm.tsx`, `web-react/src/components/wizard/probes/PortsCard.tsx`
- Test: `web-react/src/components/wizard/probes/PortForm.test.tsx`, `PortsCard.test.tsx`

**Interfaces:**
- Consumes: reducer `addProbe`/`editProbe`/`deleteProbe`/`devicePortOptions` (T7/T8); `ProbeProfile`, `ProbeMap` (`probeTypes.ts`).
- Produces:

```ts
export interface PortFormProps {
  mode: "add" | "edit";
  devicePortOptions: { value: string; label: string }[];
  profiles: ProbeProfile[];
  values: { name: string; device_port: string; type: string; profile_id: string; enabled: string };
  onFieldChange: (field: string, value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  error: string | null;
}
export function PortForm(props: PortFormProps): JSX.Element;

export interface PortsCardProps {
  probeMap: ProbeMap;
  profiles: ProbeProfile[];
  onChange: (next: ProbeMap) => void;
}
export function PortsCard(props: PortsCardProps): JSX.Element;
```

**PortForm** renders the fixed 5 fields in order (`name` text; `device_port`, `type`, `profile_id`, `enabled` selects). **Conditional visibility (§6), computed on the current values (equiv. of the on-load + on-change calls):** show the Profile row iff the selected `device_port` value contains `"ADC"`; hide the Enabled row iff the selected `type` contains `"Aux"`. `type`'s description contains literal HTML — render via `dangerouslySetInnerHTML` from the manifest `probe_config_options.type.description`, OR hardcode the plain-text meaning (choose `dangerouslySetInnerHTML` to preserve §6 fidelity; the string is a static manifest constant, not user input).

**PortsCard** owns the add/edit form state + the ports table (name / enabled icon / type / device / port / profile-name-if-ADC-else-"NA" / Edit · Delete + Add). On submit calls the reducer (`addProbe` for add, `editProbe` with the original label for edit); on `ok` emits and closes; deletes via `deleteProbe` (surfacing its error, e.g. the zero-Primary guard).

- [ ] **Step 1: Write the failing tests**

`PortsCard.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { PortsCard } from "./PortsCard";
import type { ProbeMap, ProbeProfile } from "../../../helpers/wizard/probeTypes";

const profiles: ProbeProfile[] = [{ A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" }];
const pmWith = (info: ProbeMap["probe_info"]): ProbeMap => ({
  probe_devices: [{ device: "ADS1115", module: "ads1115_adafruit", module_filename: "ads1115_adafruit", ports: ["ADC0", "ADC1"], config: {} }],
  probe_info: info,
});

it("lists probes and shows profile name only for ADC ports", () => {
  const pm = pmWith([
    { name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "ADS1115", port: "ADC0", profile: { A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" } },
    { name: "Avg", label: "Avg", type: "Aux", enabled: true, device: "Avg", port: "VIRT0", profile: {} },
  ]);
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={rs.fn()} />);
  expect(screen.getByText("PT-1000")).toBeInTheDocument();
  expect(screen.getByText("NA")).toBeInTheDocument();
});

it("deleting the only Primary while a probe remains surfaces the guard error", () => {
  const pm = pmWith([
    { name: "Grill", label: "Grill", type: "Primary", enabled: true, device: "ADS1115", port: "ADC0", profile: {} },
    { name: "Food", label: "Food", type: "Food", enabled: true, device: "ADS1115", port: "ADC1", profile: {} },
  ]);
  const onChange = rs.fn();
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={onChange} />);
  fireEvent.click(screen.getAllByRole("button", { name: /delete/i })[0]); // delete Grill (Primary)
  expect(screen.getByRole("alert")).toHaveTextContent(/Primary/i);
  expect(onChange).not.toHaveBeenCalled();
});

it("adding a probe emits the new map", () => {
  const onChange = rs.fn();
  render(<PortsCard probeMap={pmWith([])} profiles={profiles} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /add probe/i }));
  fireEvent.change(screen.getByLabelText(/probe name/i), { target: { value: "Grill" } });
  fireEvent.change(screen.getByLabelText(/device & port/i), { target: { value: "ADS1115:ADC0" } });
  fireEvent.change(screen.getByLabelText(/probe type/i), { target: { value: "Primary" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$|save/i }));
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
    probe_info: expect.arrayContaining([expect.objectContaining({ label: "Grill", device: "ADS1115", port: "ADC0" })]),
  }));
});
```

`PortForm.test.tsx`: assert the Profile row is hidden when `device_port` is a `VIRT` value and shown for an `ADC` value; assert the Enabled row is hidden when `type` is `Aux`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/components/wizard/probes/PortsCard.test.tsx src/components/wizard/probes/PortForm.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `PortForm.tsx`** with the conditional-visibility rules:

```tsx
import type { ProbeProfile } from "../../../helpers/wizard/probeTypes";

const TYPE_OPTIONS = [
  { value: "Food", label: "Food Probe" },
  { value: "Primary", label: "Primary Probe" },
  { value: "Aux", label: "Auxillary Probe" },
];

export interface PortFormProps {
  mode: "add" | "edit";
  devicePortOptions: { value: string; label: string }[];
  profiles: ProbeProfile[];
  values: { name: string; device_port: string; type: string; profile_id: string; enabled: string };
  onFieldChange: (field: string, value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  error: string | null;
}

export function PortForm({ mode, devicePortOptions, profiles, values, onFieldChange, onSubmit, onCancel, error }: PortFormProps) {
  const showProfile = values.device_port.includes("ADC");   // §6
  const showEnabled = !values.type.includes("Aux");         // §6
  return (
    <div className="pf-port-form" role="dialog" aria-label={`${mode} probe`}>
      {error && <p role="alert">{error}</p>}
      <label className="pf-field">
        <span className="pf-field-label">Probe Name</span>
        <input className="pf-input" type="text" value={values.name} onChange={(e) => onFieldChange("name", e.target.value)} />
      </label>
      <label className="pf-field">
        <span className="pf-field-label">Device &amp; Port</span>
        <select className="pf-input" value={values.device_port} onChange={(e) => onFieldChange("device_port", e.target.value)}>
          <option value="">— select —</option>
          {devicePortOptions.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
        </select>
      </label>
      <label className="pf-field">
        <span className="pf-field-label">Probe Type</span>
        <select className="pf-input" value={values.type} onChange={(e) => onFieldChange("type", e.target.value)}>
          {TYPE_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
        </select>
      </label>
      {showProfile && (
        <label className="pf-field">
          <span className="pf-field-label">Probe Profile</span>
          <select className="pf-input" value={values.profile_id} onChange={(e) => onFieldChange("profile_id", e.target.value)}>
            <option value="">— select —</option>
            {profiles.map((p) => (<option key={p.id} value={p.id}>{p.name}</option>))}
          </select>
        </label>
      )}
      {showEnabled && (
        <label className="pf-field">
          <span className="pf-field-label">Enabled</span>
          <select className="pf-input" value={values.enabled} onChange={(e) => onFieldChange("enabled", e.target.value)}>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </label>
      )}
      <div className="pf-form-actions">
        <button type="button" className="pf-btn" onClick={onCancel}>Cancel</button>
        <button type="button" className="pf-btn pf-btn-primary" onClick={onSubmit}>{mode === "add" ? "Add" : "Save"}</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement `PortsCard.tsx`** — table + form wiring:

```tsx
import { useState } from "react";
import { addProbe, deleteProbe, devicePortOptions, editProbe } from "../../../helpers/wizard/probeReducer";
import type { ProbeMap, ProbeProfile } from "../../../helpers/wizard/probeTypes";
import { PortForm } from "./PortForm";

export interface PortsCardProps {
  probeMap: ProbeMap;
  profiles: ProbeProfile[];
  onChange: (next: ProbeMap) => void;
}

interface FormState {
  mode: "add" | "edit";
  originalLabel: string;
  values: { name: string; device_port: string; type: string; profile_id: string; enabled: string };
}

const EMPTY = { name: "", device_port: "", type: "Food", profile_id: "", enabled: "true" };

export function PortsCard({ probeMap, profiles, onChange }: PortsCardProps) {
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const options = devicePortOptions(probeMap);

  function openEdit(p: ProbeMap["probe_info"][number]) {
    setError(null);
    setForm({
      mode: "edit", originalLabel: p.label,
      values: {
        name: p.name, device_port: `${p.device}:${p.port}`, type: p.type,
        profile_id: (p.profile as { id?: string }).id ?? "", enabled: p.enabled ? "true" : "false",
      },
    });
  }

  function del(label: string) {
    const r = deleteProbe(probeMap, label);
    if (r.ok) { onChange(r.probeMap); setError(null); } else { setError(r.error); }
  }

  function submit() {
    if (!form) return;
    const input = {
      name: form.values.name, devicePort: form.values.device_port,
      type: form.values.type as ProbeMap["probe_info"][number]["type"],
      profileId: form.values.profile_id, enabled: form.values.enabled === "true",
    };
    const r = form.mode === "add" ? addProbe(probeMap, profiles, input) : editProbe(probeMap, profiles, form.originalLabel, input);
    if (r.ok) { onChange(r.probeMap); setForm(null); setError(null); } else { setError(r.error); }
  }

  return (
    <section className="pf-probes-card" aria-label="Probe ports">
      <h3>Ports</h3>
      <table className="pf-probes-table">
        <tbody>
          {probeMap.probe_info.map((p) => (
            <tr key={p.label}>
              <td>{p.name}</td>
              <td>{p.enabled ? "✓" : "✗"}</td>
              <td>{p.type}</td>
              <td>{p.device}</td>
              <td>{p.port}</td>
              <td>{p.port.includes("ADC") ? ((p.profile as { name?: string }).name ?? "") : "NA"}</td>
              <td>
                <button type="button" onClick={() => openEdit(p)}>Edit</button>
                <button type="button" onClick={() => del(p.label)}>Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {!form && (
        <button type="button" className="pf-btn" onClick={() => { setError(null); setForm({ mode: "add", originalLabel: "", values: { ...EMPTY } }); }}>
          Add Probe
        </button>
      )}
      {form && (
        <PortForm
          mode={form.mode} devicePortOptions={options} profiles={profiles} values={form.values} error={error}
          onFieldChange={(field, value) => setForm({ ...form, values: { ...form.values, [field]: value } })}
          onSubmit={submit} onCancel={() => { setForm(null); setError(null); }}
        />
      )}
    </section>
  );
}
```

- [ ] **Step 5: Run tests + typecheck + lint + coverage**

Run: `cd web-react && bun run test src/components/wizard/probes/PortsCard.test.tsx src/components/wizard/probes/PortForm.test.tsx && bun run typecheck && bunx biome check src/components/wizard/probes`
Expected: pass; both files ≥75% lines.

- [ ] **Step 6: Commit**

```bash
cd web-react && bunx biome format --write src/components/wizard/probes
```
```bash
jj desc -m "feat(web-react): PortsCard + PortForm (5-field, conditional visibility, reducer-wired)"
```

---

## Task 12: React — ProbesStep + wire into WizardShell

**Files:**
- Create: `web-react/src/components/wizard/steps/ProbesStep.tsx`
- Modify: `web-react/src/components/wizard/WizardShell.tsx`
- Test: `web-react/src/components/wizard/steps/ProbesStep.test.tsx`

**Interfaces:**
- Consumes: `DevicesCard` (T10), `PortsCard` (T11); `WizardState`/`WizardWorking`; the probe reducer selectors.
- Produces:

```ts
export interface ProbesStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}
export function ProbesStep(props: ProbesStepProps): JSX.Element;
```

ProbesStep renders the units `<select>` (writing `working.probes_units`), then `DevicesCard` and `PortsCard`, both reading `working.probe_map` and writing it back via `onChange({ ...working, probe_map })`. `WizardShell`'s `case "probes"` renders `ProbesStep` instead of `PlaceholderStep`.

- [ ] **Step 1: Write the failing test**

`ProbesStep.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { ProbesStep } from "./ProbesStep";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";

function fixtures(): { state: WizardState; working: WizardWorking } {
  const modules = { ads1115_adafruit: { friendly_name: "ADS1115 Adafruit", filename: "ads1115_adafruit", device_specific: { ports: ["ADC0"], type: "adc", config: [] } } };
  const state = {
    modules_metadata: { grillplatform: {}, probes: modules, display: {}, distance: {} },
    selections: { grillplatform: null, probes: null, display: null, distance: null },
    settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    display_config: {},
    probe_map: { probe_devices: [], probe_info: [] },
    probe_profiles: [], probes_units: "F", control_mode: "Stop", first_time_setup: false, has_draft: false,
  } as unknown as WizardState;
  const working: WizardWorking = { selections: state.selections, settings_dep_values: state.settings_dep_values, display_config: {}, probe_map: state.probe_map, probes_units: "F" };
  return { state, working };
}

it("renders both cards and a units selector", () => {
  const { state, working } = fixtures();
  render(<ProbesStep state={state} working={working} onChange={rs.fn()} baseUrl="" />);
  expect(screen.getByLabelText(/temp units/i)).toBeInTheDocument();
  expect(screen.getByRole("region", { name: /probe devices/i })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: /probe ports/i })).toBeInTheDocument();
});

it("changing units emits updated working state", () => {
  const { state, working } = fixtures();
  const onChange = rs.fn();
  render(<ProbesStep state={state} working={working} onChange={onChange} baseUrl="" />);
  fireEvent.change(screen.getByLabelText(/temp units/i), { target: { value: "C" } });
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ probes_units: "C" }));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web-react && bun run test src/components/wizard/steps/ProbesStep.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ProbesStep.tsx`**

```tsx
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { DevicesCard } from "../probes/DevicesCard";
import { PortsCard } from "../probes/PortsCard";

export interface ProbesStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function ProbesStep({ state, working, onChange, baseUrl }: ProbesStepProps) {
  const setProbeMap = (probe_map: WizardWorking["probe_map"]) => onChange({ ...working, probe_map });
  return (
    <div className="pf-wizard-step" data-step="probes">
      <h2 className="pf-wizard-step-title">Probes</h2>
      <label className="pf-field">
        <span className="pf-field-label">Temp Units</span>
        <select
          className="pf-input" value={working.probes_units}
          onChange={(e) => onChange({ ...working, probes_units: e.target.value })}
        >
          <option value="F">Fahrenheit</option>
          <option value="C">Celsius</option>
        </select>
      </label>
      <DevicesCard probeMap={working.probe_map} modules={state.modules_metadata.probes} baseUrl={baseUrl} onChange={setProbeMap} />
      <PortsCard probeMap={working.probe_map} profiles={state.probe_profiles} onChange={setProbeMap} />
    </div>
  );
}
```

- [ ] **Step 4: Wire into `WizardShell.tsx`**

Add the import:

```tsx
import { ProbesStep } from "./steps/ProbesStep";
```

Change the `renderStepBody` switch — remove `"probes"` from the `PlaceholderStep` group and add its own case:

```tsx
      case "grillplatform":
      case "distance":
        return <PlaceholderStep section={currentStep} />;
      case "probes":
        return (
          <ProbesStep state={state} working={working} onChange={setWorking} baseUrl={BASE_URL} />
        );
      case "display":
        return (
          <DisplayStep state={state} working={working} onChange={setWorking} baseUrl={BASE_URL} />
        );
```

- [ ] **Step 5: Run tests + typecheck + lint + coverage + build**

Run: `cd web-react && bun run test src/components/wizard/ && bun run typecheck && bunx biome check src/components/wizard && bun run build`
Expected: pass; new files ≥75% lines; build succeeds. (Update `WizardShell.test.tsx` fixtures with the three new state fields if not already done in T5.)

- [ ] **Step 6: Commit**

```bash
cd web-react && bunx biome format --write src/components/wizard/steps/ProbesStep.tsx src/components/wizard/WizardShell.tsx
```
```bash
jj desc -m "feat(web-react): ProbesStep (units + Devices + Ports) wired into WizardShell"
```

---

## Task 12.5: React — inline bus-kind validation on device add/edit

**Files:**
- Modify: `web-react/src/components/wizard/probes/DevicesCard.tsx`
- Test: `web-react/src/components/wizard/probes/DevicesCard.test.tsx`

**Interfaces:**
- Consumes: `validateBusKinds` (`wizardApi.ts`, T5); `DevicesCard` (T10).
- Produces: `DevicesCard`'s `submit` becomes async. After the reducer returns `ok`, it calls `validateBusKinds(baseUrl, result.probeMap.probe_devices)`; a **conflict** (`{ok:false}`) keeps the form open, surfaces `detail` as the inline error, and does NOT emit; a clean result emits `onChange(result.probeMap)` and closes the form. Reproduces legacy per-device bus-kind gating (§7) for the in-progress device set only — the full cross-subsystem check still runs at `/finish`.

**Note on existing DevicesCard tests:** `submit` becoming async means the T10 tests that assert `onChange` fired right after a click must now `await` it. Add a module mock so `validateBusKinds` defaults to `{ok:true}` for those existing tests, and switch their `expect(onChange)...` assertions to `await waitFor(() => expect(onChange)...)` (import `waitFor` from `@testing-library/react`). The delete path stays synchronous (no validation) — its test is unchanged.

- [ ] **Step 1: Write the failing tests**

Add the module mock at the top of `DevicesCard.test.tsx` (mirror `I2cBusPicker.test.tsx`'s `rs.mock` shape), defaulting `validateBusKinds` to resolve `{ok:true}`:

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

rs.mock("../../../helpers/wizard/wizardApi", () => ({
  validateBusKinds: rs.fn(async () => ({ ok: true })),
}));
import { validateBusKinds } from "../../../helpers/wizard/wizardApi";

afterEach(cleanup);
```

Then the two new behaviors:

```tsx
it("blocks an add whose bus kind conflicts and shows the detail [inline validate]", async () => {
  (validateBusKinds as ReturnType<typeof rs.fn>).mockResolvedValueOnce({
    ok: false, detail: "'basic' I2C can't share a process with a USB-HID bus",
  });
  const onChange = rs.fn();
  render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/add device module/i), { target: { value: "ads1115_adafruit" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/USB-HID/i);
  expect(onChange).not.toHaveBeenCalled();
});

it("emits when the bus kind validates clean", async () => {
  const onChange = rs.fn();
  render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/add device module/i), { target: { value: "ads1115_adafruit" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(onChange).toHaveBeenCalled());
});
```

Update the existing T10 add/edit success + duplicate-name tests: the success ones become `async` and use `await waitFor(() => expect(onChange)...)`; the duplicate-name test (reducer fails BEFORE validate) still shows the error synchronously — but guard it with `await waitFor` too for safety, and assert `validateBusKinds` was NOT called (`expect(validateBusKinds).not.toHaveBeenCalled()`).

- [ ] **Step 2: Run to verify they fail**

Run: `cd web-react && bun run test src/components/wizard/probes/DevicesCard.test.tsx`
Expected: FAIL — the new conflict test fails (submit is sync, doesn't call `validateBusKinds`, so the device is emitted despite the conflict).

- [ ] **Step 3: Make `submit` async + validate**

In `DevicesCard.tsx`, add the import and rewrite `submit`:

```tsx
import { validateBusKinds } from "../../../helpers/wizard/wizardApi";
```

```tsx
  async function submit() {
    if (!form) return;
    const mod = modules[form.module];
    const result =
      form.mode === "add"
        ? addDevice(probeMap, { name: form.name, module: form.module, moduleData: mod, config: form.values })
        : editDevice(probeMap, { originalName: form.originalName, newName: form.name, config: form.values });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    // In-progress bus-kind coexistence check (§7). The full cross-subsystem
    // check still runs at /finish; this is inline pre-Finish feedback.
    const verdict = await validateBusKinds(baseUrl, result.probeMap.probe_devices);
    if (!verdict.ok) {
      setError(verdict.detail ?? "This device's bus configuration conflicts with another device.");
      return;
    }
    onChange(result.probeMap);
    setForm(null);
    setError(null);
  }
```

Update the submit invocation site to fire-and-forget the promise: change `onSubmit={submit}` to `onSubmit={() => void submit()}`.

- [ ] **Step 4: Run tests**

Run: `cd web-react && bun run test src/components/wizard/probes/DevicesCard.test.tsx`
Expected: all pass (new + updated).

- [ ] **Step 5: Typecheck + lint + coverage**

Run: `cd web-react && bun run typecheck && bunx biome check src/components/wizard/probes/DevicesCard.tsx && bun run test --coverage src/components/wizard/probes/DevicesCard.test.tsx`
Expected: clean; `DevicesCard.tsx` ≥75% lines.

- [ ] **Step 6: Commit**

```bash
cd web-react && bunx biome format --write src/components/wizard/probes/DevicesCard.tsx src/components/wizard/probes/DevicesCard.test.tsx
```
```bash
jj desc -m "feat(web-react): inline bus-kind validation on device add/edit"
```

---

## Task 13: e2e + full gate (FINAL)

**Files:**
- Modify: `web-react/tests/e2e/wizard.spec.ts`
- Test: whole web-react gate + full Python suite

**Interfaces:**
- Consumes: everything. Runs against the live backend (rsbuild dev proxying `/api` → :5000; do NOT set `PUBLIC_PIFIRE_URL`).

- [ ] **Step 1: Restart the dev backend so the new endpoints are live**

The Flask app must be restarted to pick up the new `blueprints/api_wizard/routes.py` routes:

```bash
pkill -f "control.py" ; pkill -f "gunicorn" ; sleep 2
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python control.py &
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app &
sleep 5
```

- [ ] **Step 2: Add the e2e flow to `wizard.spec.ts`**

Add a test that navigates to the Probes step, adds a device (select the ADS1115 module, accept the default name, submit), adds a probe (name, device:port, type Primary, a profile), and asserts the device + probe rows render. Then drive to Finish with the installer neutralized (the existing spec already stubs `/api/wizard/finish`/`installstatus` responses — follow that pattern; do not fire a real install). Assert the staged `probe_map` is what was built (e.g. by intercepting the `/api/wizard/draft` POST body on the step transition and asserting `probe_devices`/`probe_info`).

```ts
test("probes step: add device + probe, stage into draft", async ({ page }) => {
  await page.goto("/wizard");
  // STEPS order is [welcome, grillplatform, probes, ...]; from step 0 (welcome)
  // click Next twice to reach the Probes step (step 2).
  await page.getByRole("button", { name: /next/i }).click(); // -> grillplatform
  await page.getByRole("button", { name: /next/i }).click(); // -> probes
  await expect(page.locator('[data-step="probes"]')).toBeVisible();
  await page.getByLabel(/add device module/i).selectOption("ads1115_adafruit");
  await page.getByRole("button", { name: /^add$/i }).click();
  await expect(page.getByText("ADS1115Adafruit")).toBeVisible();

  await page.getByRole("button", { name: /add probe/i }).click();
  await page.getByLabel(/probe name/i).fill("Grill");
  await page.getByLabel(/device & port/i).selectOption("ADS1115Adafruit:ADC0");
  await page.getByLabel(/probe type/i).selectOption("Primary");
  await page.getByRole("button", { name: /^add$/i }).click();
  await expect(page.getByText("Grill")).toBeVisible();

  const draftReq = page.waitForRequest((r) => r.url().includes("/api/wizard/draft") && r.method() === "POST");
  await page.getByRole("button", { name: /next/i }).click();
  const body = JSON.parse((await draftReq).postData() ?? "{}");
  expect(body.probe_map.probe_devices[0].device).toBe("ADS1115Adafruit");
  expect(body.probe_map.probe_info[0].label).toBe("Grill");
});
```

- [ ] **Step 3: Run the e2e**

Run: `cd web-react && bunx playwright test wizard`
Expected: pass. (If it hangs on the backend, confirm control.py + gunicorn are up per Step 1.)

- [ ] **Step 4: Run the full web-react gate**

Run: `cd web-react && bun run typecheck && bunx biome check . && bun run test --coverage && bun run build`
Expected: all green; global gate ≥75% lines per file.

- [ ] **Step 5: Run the full Python suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`
Expected: all pass (no regressions in `tests/web/test_api_wizard.py` or elsewhere).

- [ ] **Step 6: Commit**

```bash
cd web-react && bunx biome format --write tests/e2e/wizard.spec.ts
```
```bash
jj desc -m "test(web-react): probes-step e2e (add device + probe, staged into draft) + full gate"
```

---

## Self-review notes (coverage of spec)

- Spec §① data flow & seeding → T1 (state/draft), T2 (finish payload). First-time board-default seeding is spec-scoped-out.
- Spec §② UI (Devices/Ports cards, units, retired quirks) → T10, T11, T12. `refresh_probes`/500ms/nested-modal quirks are dropped by construction (client-held state, React dialogs).
- Spec §③ reducer (CRUD, reposition exact, 4 fixes, derived selectors, bus-kind endpoint) → T6 (device + fixes 1/3/4 + selectors), T7 (probe CRUD + one-Primary + fix 2 + profile copy), T8 (reposition 3a/3b), T4 (validate-bus-kinds endpoint), T12.5 (inline client wiring of the per-device validate call on device add/edit).
- Spec §④ discovery (5 flows) → T3 (BT/ThermoWorks endpoints), T9 (BT/ThermoWorks/i2c/usb pickers); I2C + usb_serial `/scan` kinds already exist from the display slice.
- Spec §⑤ profiles (value-copy, list in /state, no CRUD) → T1 (`probe_profiles` in state), T7 (`buildProbe` value-copy).
- Spec §⑥ testing → per-task TDD + T13 e2e/full gate.

**Deferred (tracked, not gaps):** first-time board-default `probe_map` seeding depends on the grillplatform step (still a placeholder). Deleting legacy `blueprints/probeconfig/*` is post-parity cleanup after React parity is proven live.
