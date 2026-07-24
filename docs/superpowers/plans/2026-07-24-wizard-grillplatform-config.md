# React Wizard GrillPlatform Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder `grillplatform` step of the React setup wizard with a working module-config surface over the existing `ModuleCard` spine, plus a server round-trip on platform-switch and a guarded board→probe_map reseed.

**Architecture:** grillplatform becomes a `DisplayStep`-shaped slice (`configSource="none"`, no config bag). Two new seams: (D1) a general `POST /api/wizard/module-values` endpoint the client fetches on module-switch to replace that section's dep-values from live settings (byte-exact legacy `_wizard_modulecard` parity); (D2) a pure `reseedProbeMapForBoard` helper that reseeds `working.probe_map` from `manifest.boards[board_id].probe_map` only on a fresh install and only when the current map hasn't diverged from the previous board's default. The backend already persists/validates grillplatform generically — no new persist/validate code.

**Tech Stack:** Flask (Python 3.14), React + TypeScript (TS7/tsgo), rsbuild, `@rstest/core`, Playwright, Biome. Package manager: **bun**.

## Global Constraints

- **bun, not npm** for all web-react install/run.
- **Testing API is `@rstest/core`** (`rs.fn`/`rs.mock`) — NOT vitest/`vi`. `.test.ts` runs in node, `.test.tsx` in jsdom.
- **TS7 typecheck** via `bun run typecheck` (`noUnusedLocals` on) must stay clean — a new **required** `WizardState.board_probe_maps` field breaks every full `WizardState` test literal; all must be updated in the same task that adds the field.
- **Coverage ≥75% lines per changed file** (rstest gate).
- **Python tests:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`. Run `uvx ruff format` on changed Python before every commit. PEP 758 bare-tuple `except A, B` is canonical — do NOT rewrite to `except (A, B)`.
- **Security:** no test may fire the real installer — any test reaching `/finish`'s `os.system` must monkeypatch it. (No task here touches `/finish`.)
- **Legacy parity (D1):** the module-values endpoint reads the LIVE settings tree via `get_settings_dependencies_values`; do NOT substitute manifest defaults. This intentionally reproduces the legacy quirk that a fresh-install switch shows generic default pins.
- **Guarded reseed (D2):** reseed `probe_map` only when `first_time_setup === true` AND the current map deep-equals the previous board's default. Never clobber user probe edits.
- **jj boundary protocol:** the controller runs `jj new -m "wip: ..."` before each dispatch; the implementer finalizes with a single `jj desc -m`. `git add`/`git commit` in steps below are the logical commit; in this repo they map to the implementer's single `jj desc`.

---

### Task 1: Backend `POST /api/wizard/module-values` endpoint

**Files:**
- Modify: `blueprints/api_wizard/routes.py` (add a new route; all needed imports — `jsonify`, `request`, `read_settings`, `read_wizard`, `get_settings_dependencies_values` — are already imported at the top of the file)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: `read_wizard()` → dict with `["modules"][section][module]` module-data; `read_settings()` → live settings dict; `get_settings_dependencies_values(settings, module_data)` → `{dep_key: value|None}` (already imported from `blueprints.wizard.wizard`).
- Produces: `POST /api/wizard/module-values` with JSON body `{"section": str, "module": str}` → `200 {"settings": {dep_key: value|null}, "config": {option_name: value}}`, or `400 {"result": "error", "message": "unknown_module"}` for an unknown section/module.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_api_wizard.py` (the `client`/`ds` fixtures already exist at the top of the file):

```python
def test_module_values_grillplatform_returns_live_settings(ds, client):
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "grillplatform", "module": "pcb_4.x.x"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # settings is the dep-key -> value map for the module's settings_dependencies
    assert isinstance(body["settings"], dict)
    assert "system_type" in body["settings"]
    # grillplatform never has a config bag
    assert body["config"] == {}


def test_module_values_display_config_is_guarded(ds, client):
    # A display module that has never been persisted must not KeyError -- the
    # config bag falls back to {} (mirrors legacy _wizard_modulecard callout #2).
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "display", "module": "ili9341b"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body["settings"], dict)
    assert isinstance(body["config"], dict)  # dict, never a crash


def test_module_values_unknown_module_is_400(ds, client):
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "grillplatform", "module": "does_not_exist"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "unknown_module"


def test_module_values_unknown_section_is_400(ds, client):
    # "probes" has no module-card round-trip; anything outside the 3 non-probe
    # sections is rejected.
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "probes", "module": "default"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "unknown_module"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k module_values -v`
Expected: FAIL — 404 (route not registered) on every test.

- [ ] **Step 3: Add the endpoint**

Add this route to `blueprints/api_wizard/routes.py` (place it after `wizard_scan` / near the other routes):

```python
@api_wizard_bp.route("/module-values", methods=["POST"])
def wizard_module_values():
    """Return a module's settings-dependency values (+ display config bag) for
    the wizard's client-side module-switch, mirroring the legacy
    _wizard_modulecard round-trip (blueprints/wizard/routes.py:105-119).

    `settings` come from the LIVE settings tree via
    get_settings_dependencies_values -- NOT manifest defaults -- so a switch
    reproduces legacy behavior exactly (D1). `config` is display-only and
    guarded with .get(module, {}) because a display module may never have been
    configured (callout #2 -- legacy indexes it unguarded and KeyErrors)."""
    payload = request.get_json(silent=True) or {}
    section = payload.get("section")
    module = payload.get("module")
    if section not in ("grillplatform", "display", "distance"):
        return jsonify({"result": "error", "message": "unknown_module"}), 400
    wizard_data = read_wizard()
    module_data = wizard_data.get("modules", {}).get(section, {}).get(module)
    if not isinstance(module_data, dict):
        return jsonify({"result": "error", "message": "unknown_module"}), 400
    settings = read_settings()
    dep_values = get_settings_dependencies_values(settings, module_data)
    if section == "display":
        config = settings.get("display", {}).get("config", {}).get(module, {})
    else:
        config = {}
    return jsonify({"settings": dep_values, "config": config}), 200
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k module_values -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Format and commit**

```bash
uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py
git add blueprints/api_wizard/routes.py tests/web/test_api_wizard.py
git commit -m "feat(api_wizard): add /module-values endpoint for wizard module-switch"
```

---

### Task 2: Backend `_build_state` — ship `board_probe_maps` + fresh-install probe_map seeding

**Files:**
- Modify: `blueprints/api_wizard/routes.py` — `_build_state` (currently lines 61-126)
- Test: `tests/web/test_api_wizard.py`

**Interfaces:**
- Consumes: `read_wizard()["boards"]` → `{board_id: {"probe_map": {"probe_devices": [...], "probe_info": [...]}, ...}}` (4 PCB ids); `info["probe_map"]` from `wizardInstallInfoDefaults` (the default board's map, already computed).
- Produces: `_build_state` return dict gains `"board_probe_maps": {board_id: probe_map}`; on `first_time_setup`, the returned `"probe_map"` is the default board's map (`info["probe_map"]`) instead of the live-settings probe_map.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_api_wizard.py`:

```python
def test_state_ships_board_probe_maps(ds, client):
    body = client.get("/api/wizard/state").get_json()
    assert isinstance(body["board_probe_maps"], dict)
    # the 4 PCB boards each carry a probe_map with the two probe arrays
    assert "pcb_4.x.x" in body["board_probe_maps"]
    board = body["board_probe_maps"]["pcb_4.x.x"]
    assert "probe_devices" in board and "probe_info" in board
    assert len(board["probe_devices"]) >= 1


def test_state_fresh_install_seeds_default_board_probe_map(ds, client):
    settings = read_settings()
    settings["globals"]["first_time_setup"] = True
    write_settings_store(settings)

    body = client.get("/api/wizard/state").get_json()
    # On a fresh install the returned probe_map is the DEFAULT board's map,
    # not the live-settings one -- it matches board_probe_maps[default_board].
    assert body["probe_map"] == body["board_probe_maps"]["pcb_4.x.x"]
    assert len(body["probe_map"]["probe_devices"]) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k "board_probe_maps or fresh_install_seeds" -v`
Expected: FAIL — `KeyError: 'board_probe_maps'` (field not in the state dict); the fresh-install test fails because probe_map comes from settings.

- [ ] **Step 3: Implement the two changes in `_build_state`**

In `blueprints/api_wizard/routes.py`, inside `_build_state`, just after `modules = wizard_data.get("modules", {})` (near the top of the function), add the board map extraction:

```python
    # Board default probe maps (the 4 PCB ids). The client reseeds probe_map
    # from these on a fresh-install platform switch (guarded). Boards without a
    # probe_map are skipped.
    board_probe_maps = {
        board_id: board["probe_map"]
        for board_id, board in wizard_data.get("boards", {}).items()
        if isinstance(board, dict) and isinstance(board.get("probe_map"), dict)
    }
```

Then, in the non-draft `else` branch, replace the single `probe_map = settings.get(...)` line with a first-time-setup split. The current code is:

```python
        display_config = settings.get("display", {}).get("config", {})
        probe_map = settings.get("probe_settings", {}).get("probe_map", {"probe_devices": [], "probe_info": []})
        probes_units = settings["globals"].get("units", "F")
```

Change the `probe_map` assignment to:

```python
        display_config = settings.get("display", {}).get("config", {})
        if settings["globals"]["first_time_setup"]:
            # Fresh install: seed from the default board's probe_map (which
            # wizardInstallInfoDefaults already computed into info["probe_map"])
            # instead of the live-settings map, so the default board's probes
            # show up-front and establish the reseed baseline.
            probe_map = info.get("probe_map") or {"probe_devices": [], "probe_info": []}
        else:
            probe_map = settings.get("probe_settings", {}).get("probe_map", {"probe_devices": [], "probe_info": []})
        probes_units = settings["globals"].get("units", "F")
```

(`info` is already in scope — it was assigned just above from `wizardInstallInfoDefaults`/`wizardInstallInfoExisting`.)

Finally, add `board_probe_maps` to the returned dict (the `return {...}` at the end of `_build_state`):

```python
        "probes_units": probes_units,
        "board_probe_maps": board_probe_maps,
        "control_mode": control.get("mode", "Stop"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k "board_probe_maps or fresh_install_seeds" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the whole api_wizard suite to catch regressions**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -v`
Expected: PASS (all, including the Task 1 tests).

- [ ] **Step 6: Format and commit**

```bash
uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py
git add blueprints/api_wizard/routes.py tests/web/test_api_wizard.py
git commit -m "feat(api_wizard): ship board_probe_maps and seed fresh-install probe_map from default board"
```

---

### Task 3: Frontend types + `fetchModuleValues` + fixture updates

**Files:**
- Modify: `web-react/src/helpers/wizard/wizardTypes.ts` (add `board_probe_maps` to `WizardState`, add `ModuleValues`)
- Modify: `web-react/src/helpers/wizard/wizardApi.ts` (add `fetchModuleValues`)
- Modify (fixtures — add `board_probe_maps: {}` to every full `WizardState` literal): `web-react/src/components/App.test.tsx`, `web-react/src/helpers/wizard/wizardState.test.ts`, `web-react/src/components/wizard/steps/DisplayStep.test.tsx`, `web-react/src/components/wizard/WizardShell.test.tsx`, `web-react/src/components/wizard/steps/ProbesStep.test.tsx`
- Test: `web-react/src/helpers/wizard/wizardApi.test.ts`

**Interfaces:**
- Consumes: `WizardSection` (existing, from `wizardTypes.ts`); `ProbeMap` (existing, from `probeTypes.ts`, already imported into `wizardTypes.ts`).
- Produces: `WizardState.board_probe_maps: Record<string, ProbeMap>`; `interface ModuleValues { settings: Record<string, string | null>; config: Record<string, unknown> }`; `fetchModuleValues(baseUrl: string, section: WizardSection, module: string): Promise<ModuleValues>` (throws on non-ok response).

- [ ] **Step 1: Write the failing api test**

Add to `web-react/src/helpers/wizard/wizardApi.test.ts` (inside the existing `describe("wizardApi", ...)` block):

```typescript
  test("fetchModuleValues posts section/module to /module-values and returns parsed JSON", async () => {
    const fake = { settings: { system_type: "raspberry_pi_all" }, config: {} };
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: true, json: async () => fake }) as never;
    const { fetchModuleValues } = await import("./wizardApi");
    const r = await fetchModuleValues("", "grillplatform", "pcb_4.x.x");
    const call = (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0];
    expect(call[0]).toContain("/api/wizard/module-values");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      section: "grillplatform",
      module: "pcb_4.x.x",
    });
    expect(r.settings.system_type).toBe("raspberry_pi_all");
  });

  test("fetchModuleValues throws on a non-ok response", async () => {
    globalThis.fetch = rs.fn().mockResolvedValue({ ok: false, status: 400 }) as never;
    const { fetchModuleValues } = await import("./wizardApi");
    await expect(fetchModuleValues("", "grillplatform", "nope")).rejects.toThrow();
  });
```

- [ ] **Step 2: Run the api test to verify it fails**

Run: `cd web-react && bun run test src/helpers/wizard/wizardApi.test.ts`
Expected: FAIL — `fetchModuleValues` is not exported.

- [ ] **Step 3: Add the type + api function**

In `web-react/src/helpers/wizard/wizardTypes.ts`, add `board_probe_maps` to the `WizardState` interface (after `probes_units: string;`):

```typescript
  probes_units: string;
  board_probe_maps: Record<string, ProbeMap>;
  control_mode: string;
```

and add the `ModuleValues` interface (after the `WizardState` interface):

```typescript
export interface ModuleValues {
  settings: Record<string, string | null>;
  config: Record<string, unknown>;
}
```

In `web-react/src/helpers/wizard/wizardApi.ts`, extend the top import to include `ModuleValues` and `WizardSection`:

```typescript
import type {
  InstallStatus,
  ModuleValues,
  ScanResult,
  WizardSection,
  WizardState,
  WizardWorking,
} from "./wizardTypes";
```

and add the function (after `scan`):

```typescript
export async function fetchModuleValues(
  baseUrl: string,
  section: WizardSection,
  module: string,
): Promise<ModuleValues> {
  const r = await fetch(url(baseUrl, "module-values"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, module }),
  });
  if (!r.ok) throw new Error(`module-values failed: ${r.status}`);
  return (await r.json()) as ModuleValues;
}
```

- [ ] **Step 4: Add `board_probe_maps: {}` to every full `WizardState` literal**

In each of these files, every object literal typed as `WizardState` (identifiable by its `first_time_setup:` line) needs `board_probe_maps: {},` added. Add it next to the `probes_units:` line:
- `web-react/src/components/App.test.tsx`
- `web-react/src/helpers/wizard/wizardState.test.ts` (TWO literals: `base` and `baseState()`)
- `web-react/src/components/wizard/steps/DisplayStep.test.tsx` (the `state` literal)
- `web-react/src/components/wizard/WizardShell.test.tsx`
- `web-react/src/components/wizard/steps/ProbesStep.test.tsx`

Example (DisplayStep.test.tsx `state`):

```typescript
  probes_units: "F",
  board_probe_maps: {},
  control_mode: "Stop",
```

- [ ] **Step 5: Run typecheck to confirm all fixtures satisfy the new required field**

Run: `cd web-react && bun run typecheck`
Expected: PASS — no `Property 'board_probe_maps' is missing` errors. If any remain, add the field to that literal.

- [ ] **Step 6: Run the api test + full web suite**

Run: `cd web-react && bun run test src/helpers/wizard/wizardApi.test.ts && bun run test`
Expected: PASS (api tests green; whole suite green after fixture updates).

- [ ] **Step 7: Commit**

```bash
git add web-react/src/helpers/wizard/wizardTypes.ts web-react/src/helpers/wizard/wizardApi.ts web-react/src/helpers/wizard/wizardApi.test.ts web-react/src/components/App.test.tsx web-react/src/helpers/wizard/wizardState.test.ts web-react/src/components/wizard/steps/DisplayStep.test.tsx web-react/src/components/wizard/WizardShell.test.tsx web-react/src/components/wizard/steps/ProbesStep.test.tsx
git commit -m "feat(web-react): add board_probe_maps to WizardState + fetchModuleValues api"
```

---

### Task 4: Frontend state helpers — `setSectionDepValues`, `replaceProbeMap`, `reseedProbeMapForBoard`

**Files:**
- Modify: `web-react/src/helpers/wizard/wizardState.ts`
- Test: `web-react/src/helpers/wizard/wizardState.test.ts`

**Interfaces:**
- Consumes: `WizardWorking` (existing); `WizardSection` (existing); `ProbeMap` (from `probeTypes.ts`).
- Produces:
  - `EMPTY_PROBE_MAP: ProbeMap` — `{ probe_devices: [], probe_info: [] }`.
  - `setSectionDepValues(w: WizardWorking, section: WizardSection, values: Record<string, string | null>): WizardWorking`.
  - `replaceProbeMap(w: WizardWorking, probe_map: ProbeMap): WizardWorking`.
  - `reseedProbeMapForBoard(currentMap: ProbeMap, prevBoardMap: ProbeMap, newBoardMap: ProbeMap, firstTimeSetup: boolean): ProbeMap` — returns a deep clone of `newBoardMap` when `firstTimeSetup && deepEqual(currentMap, prevBoardMap)`, else returns `currentMap` unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `web-react/src/helpers/wizard/wizardState.test.ts`. First extend the top import:

```typescript
import {
  displayConfigFor,
  EMPTY_PROBE_MAP,
  initialWorking,
  replaceProbeMap,
  reseedProbeMapForBoard,
  selectModule,
  setDepValue,
  setDisplayConfig,
  setSectionDepValues,
} from "./wizardState";
```

Then add a new describe block:

```typescript
describe("wizardState grillplatform helpers", () => {
  const boardA: import("./probeTypes").ProbeMap = {
    probe_devices: [{ device: "DA", module: "ads1115", module_filename: "ads1115", ports: ["ADC0"], config: {} }],
    probe_info: [],
  };
  const boardB: import("./probeTypes").ProbeMap = {
    probe_devices: [{ device: "DB", module: "ads1115", module_filename: "ads1115", ports: ["ADC1"], config: {} }],
    probe_info: [],
  };

  test("setSectionDepValues replaces a section's dep map immutably", () => {
    const w = initialWorking(base);
    const w2 = setSectionDepValues(w, "grillplatform", { output_auger: "14", system_type: "raspberry_pi_all" });
    expect(w2.settings_dep_values.grillplatform).toEqual({ output_auger: "14", system_type: "raspberry_pi_all" });
    expect(w.settings_dep_values.grillplatform).toEqual({}); // original untouched
  });

  test("replaceProbeMap swaps the probe_map immutably", () => {
    const w = initialWorking(base);
    const w2 = replaceProbeMap(w, boardB);
    expect(w2.probe_map).toEqual(boardB);
    expect(w.probe_map).toEqual({ probe_devices: [], probe_info: [] });
  });

  test("reseedProbeMapForBoard reseeds when fresh + current equals previous board default", () => {
    // user hasn't edited: current == prev board default -> adopt new board
    const result = reseedProbeMapForBoard(boardA, boardA, boardB, true);
    expect(result).toEqual(boardB);
    expect(result).not.toBe(boardB); // deep-cloned, not aliased
  });

  test("reseedProbeMapForBoard preserves current when the user has edited it", () => {
    const edited: import("./probeTypes").ProbeMap = {
      probe_devices: [{ device: "EDITED", module: "m", module_filename: "m", ports: [], config: {} }],
      probe_info: [],
    };
    const result = reseedProbeMapForBoard(edited, boardA, boardB, true);
    expect(result).toBe(edited); // unchanged
  });

  test("reseedProbeMapForBoard is a no-op on an existing install", () => {
    const result = reseedProbeMapForBoard(boardA, boardA, boardB, false);
    expect(result).toBe(boardA);
  });

  test("reseedProbeMapForBoard reseeds to EMPTY when the new board has no entry (unedited)", () => {
    // caller passes EMPTY_PROBE_MAP for a board with no manifest entry
    const result = reseedProbeMapForBoard(boardA, boardA, EMPTY_PROBE_MAP, true);
    expect(result).toEqual({ probe_devices: [], probe_info: [] });
  });

  test("reseedProbeMapForBoard treats an initial EMPTY previous board correctly", () => {
    // prev selection was null -> prevBoardMap is EMPTY; current also EMPTY -> reseed
    const result = reseedProbeMapForBoard(EMPTY_PROBE_MAP, EMPTY_PROBE_MAP, boardB, true);
    expect(result).toEqual(boardB);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web-react && bun run test src/helpers/wizard/wizardState.test.ts`
Expected: FAIL — `EMPTY_PROBE_MAP`, `setSectionDepValues`, `replaceProbeMap`, `reseedProbeMapForBoard` are not exported.

- [ ] **Step 3: Implement the helpers**

In `web-react/src/helpers/wizard/wizardState.ts`, extend the top import and add the helpers at the end of the file:

```typescript
import type { ProbeMap } from "./probeTypes";
import type { WizardSection, WizardState, WizardWorking } from "./wizardTypes";

export const EMPTY_PROBE_MAP: ProbeMap = { probe_devices: [], probe_info: [] };

// Order-insensitive structural equality. probe_maps are plain JSON (arrays of
// objects with fixed string/number keys); a manifest-sourced map and a
// reducer-built map can differ only in object key order, which this ignores.
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) {
    return false;
  }
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ak = Object.keys(a as object);
  const bk = Object.keys(b as object);
  if (ak.length !== bk.length) return false;
  return ak.every((k) =>
    deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
  );
}

export function setSectionDepValues(
  w: WizardWorking,
  section: WizardSection,
  values: Record<string, string | null>,
): WizardWorking {
  return {
    ...w,
    settings_dep_values: { ...w.settings_dep_values, [section]: { ...values } },
  };
}

export function replaceProbeMap(w: WizardWorking, probe_map: ProbeMap): WizardWorking {
  return { ...w, probe_map };
}

// D2 guard: reseed the probe_map from the newly-selected board's default only
// on a fresh install AND only when the current map has NOT diverged from the
// previous board's default -- so manual probe edits are never clobbered.
// Callers resolve prev/new board maps as `board_probe_maps[module] ?? EMPTY_PROBE_MAP`.
export function reseedProbeMapForBoard(
  currentMap: ProbeMap,
  prevBoardMap: ProbeMap,
  newBoardMap: ProbeMap,
  firstTimeSetup: boolean,
): ProbeMap {
  if (firstTimeSetup && deepEqual(currentMap, prevBoardMap)) {
    return structuredClone(newBoardMap);
  }
  return currentMap;
}
```

(The existing `import type { WizardSection, WizardState, WizardWorking } from "./wizardTypes";` line at the top is already present — merge the `ProbeMap` import as a new line; do not duplicate the wizardTypes import.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web-react && bun run test src/helpers/wizard/wizardState.test.ts`
Expected: PASS (all, including the pre-existing wizardState tests).

- [ ] **Step 5: Typecheck**

Run: `cd web-react && bun run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web-react/src/helpers/wizard/wizardState.ts web-react/src/helpers/wizard/wizardState.test.ts
git commit -m "feat(web-react): add setSectionDepValues/replaceProbeMap/reseedProbeMapForBoard helpers"
```

---

### Task 5: `GrillPlatformStep` component (+ `ModuleCard` `disabled` prop)

**Files:**
- Create: `web-react/src/components/wizard/steps/GrillPlatformStep.tsx`
- Create: `web-react/src/components/wizard/steps/GrillPlatformStep.test.tsx`
- Modify: `web-react/src/components/wizard/ModuleCard.tsx` (add optional `disabled?: boolean` prop on the module `<select>`)

**Interfaces:**
- Consumes: `ModuleCard` (existing; now with `disabled?: boolean`); `fetchModuleValues` (Task 3); `selectModule`, `setDepValue`, `setSectionDepValues`, `replaceProbeMap`, `reseedProbeMapForBoard`, `EMPTY_PROBE_MAP` (Tasks 3/4); `WizardState`, `WizardWorking`.
- Produces: `GrillPlatformStep({ state, working, onChange, baseUrl }: { state: WizardState; working: WizardWorking; onChange: (next: WizardWorking) => void; baseUrl: string })`.

- [ ] **Step 1: Add the `disabled` prop to `ModuleCard`**

In `web-react/src/components/wizard/ModuleCard.tsx`, add `disabled?: boolean;` to `ModuleCardProps`, destructure it (defaulting to `false`), and apply it to the module `<select>`:

```typescript
export interface ModuleCardProps {
  section: WizardSection;
  modules: Record<string, WizardModuleData>;
  selectedModule: string | null;
  depValues: Record<string, string | null>;
  configValues: Record<string, unknown>;
  configSource: WizardConfigSource;
  onSelectModule: (moduleName: string) => void;
  onDepChange: (key: string, value: string) => void;
  onConfigChange: (optionName: string, value: string) => void;
  baseUrl: string;
  disabled?: boolean;
}
```

In the destructure, add `disabled = false,`. On the module select element, add the attribute:

```typescript
        <select
          className="pf-input"
          value={selectedModule ?? ""}
          disabled={disabled}
          onChange={(e) => onSelectModule(e.target.value)}
        >
```

(Existing `ModuleCard` callers omit `disabled` → `undefined` → not disabled → their tests are unaffected.)

- [ ] **Step 2: Write the failing component tests**

Create `web-react/src/components/wizard/steps/GrillPlatformStep.test.tsx`:

```typescript
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ProbeMap } from "../../../helpers/wizard/probeTypes";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { GrillPlatformStep } from "./GrillPlatformStep";

const fetchModuleValues = rs.fn();
rs.mock("../../../helpers/wizard/wizardApi", () => ({
  fetchModuleValues: (...args: unknown[]) => fetchModuleValues(...args),
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
}));

afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});

const boardDefault: ProbeMap = {
  probe_devices: [{ device: "GRILL", module: "ads1115", module_filename: "ads1115", ports: ["ADC0"], config: {} }],
  probe_info: [],
};
const boardOther: ProbeMap = {
  probe_devices: [{ device: "OTHER", module: "ads1115", module_filename: "ads1115", ports: ["ADC1"], config: {} }],
  probe_info: [],
};

function makeState(firstTime: boolean): WizardState {
  return {
    modules_metadata: {
      grillplatform: {
        pcb_4: { friendly_name: "PCB 4.x.x", settings_dependencies: { system_type: { friendly_name: "System", options: { raspberry_pi_all: "Pi" }, settings: [] } } },
        pcb_2: { friendly_name: "PCB 2.00a", settings_dependencies: { system_type: { friendly_name: "System", options: { raspberry_pi_all: "Pi" }, settings: [] } } },
      },
      probes: {},
      distance: {},
      display: {},
    },
    selections: { grillplatform: "pcb_4", probes: null, distance: null, display: null },
    settings_dep_values: { grillplatform: { system_type: "raspberry_pi_all" }, probes: {}, distance: {}, display: {} },
    display_config: {},
    probe_map: boardDefault,
    probe_profiles: [],
    probes_units: "F",
    board_probe_maps: { pcb_4: boardDefault, pcb_2: boardOther },
    control_mode: "Stop",
    first_time_setup: firstTime,
    has_draft: false,
  };
}

function makeWorking(state: WizardState): WizardWorking {
  return {
    selections: { ...state.selections },
    settings_dep_values: structuredClone(state.settings_dep_values),
    display_config: {},
    probe_map: structuredClone(state.probe_map),
    probes_units: "F",
  };
}

describe("GrillPlatformStep", () => {
  it("switching platform fetches module values and applies them + reseeds probe_map", async () => {
    fetchModuleValues.mockResolvedValue({ settings: { system_type: "prototype" }, config: {} });
    const state = makeState(true);
    const onChange = rs.fn();
    render(<GrillPlatformStep state={state} working={makeWorking(state)} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), { target: { value: "pcb_2" } });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.selections.grillplatform).toBe("pcb_2");
    expect(next.settings_dep_values.grillplatform.system_type).toBe("prototype");
    // fresh install + unedited current == prev board default -> reseed to pcb_2's board map
    expect(next.probe_map.probe_devices[0].device).toBe("OTHER");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "grillplatform", "pcb_2");
  });

  it("does not reseed probe_map on an existing install", async () => {
    fetchModuleValues.mockResolvedValue({ settings: { system_type: "prototype" }, config: {} });
    const state = makeState(false);
    const onChange = rs.fn();
    render(<GrillPlatformStep state={state} working={makeWorking(state)} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), { target: { value: "pcb_2" } });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.probe_map.probe_devices[0].device).toBe("GRILL"); // untouched
  });

  it("shows an error banner and does not call onChange when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const state = makeState(true);
    const onChange = rs.fn();
    render(<GrillPlatformStep state={state} working={makeWorking(state)} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), { target: { value: "pcb_2" } });

    await waitFor(() => expect(screen.getByText(/couldn't load the platform/i)).toBeInTheDocument());
    expect(onChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd web-react && bun run test src/components/wizard/steps/GrillPlatformStep.test.tsx`
Expected: FAIL — module `./GrillPlatformStep` not found.

- [ ] **Step 4: Implement the component**

Create `web-react/src/components/wizard/steps/GrillPlatformStep.tsx`:

```typescript
import { useState } from "react";
import { fetchModuleValues } from "../../../helpers/wizard/wizardApi";
import {
  EMPTY_PROBE_MAP,
  replaceProbeMap,
  reseedProbeMapForBoard,
  selectModule,
  setDepValue,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface GrillPlatformStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function GrillPlatformStep({ state, working, onChange, baseUrl }: GrillPlatformStepProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSelect(newModule: string) {
    const prevModule = working.selections.grillplatform;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchModuleValues(baseUrl, "grillplatform", newModule);
      let next = selectModule(working, "grillplatform", newModule);
      next = setSectionDepValues(next, "grillplatform", res.settings);
      const prevBoardMap = state.board_probe_maps[prevModule ?? ""] ?? EMPTY_PROBE_MAP;
      const newBoardMap = state.board_probe_maps[newModule] ?? EMPTY_PROBE_MAP;
      const reseeded = reseedProbeMapForBoard(
        working.probe_map,
        prevBoardMap,
        newBoardMap,
        state.first_time_setup,
      );
      next = replaceProbeMap(next, reseeded);
      onChange(next);
    } catch {
      // Advisory failure: leave the prior selection/deps/probe_map intact so
      // the user can retry -- never half-apply a switch.
      setError("Couldn't load the platform configuration. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="pf-wizard-step" data-step="grillplatform">
      <h2 className="pf-wizard-step-title">Grill Platform</h2>
      {error && <p className="pf-wizard-finish-error">{error}</p>}
      <ModuleCard
        section="grillplatform"
        configSource="none"
        modules={state.modules_metadata.grillplatform}
        selectedModule={working.selections.grillplatform}
        depValues={working.settings_dep_values.grillplatform ?? {}}
        configValues={{}}
        baseUrl={baseUrl}
        disabled={loading}
        onSelectModule={(m) => void handleSelect(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "grillplatform", k, v))}
        onConfigChange={() => {}}
      />
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd web-react && bun run test src/components/wizard/steps/GrillPlatformStep.test.tsx`
Expected: PASS (3 passed).

- [ ] **Step 6: Confirm `ModuleCard`'s own tests still pass + typecheck**

Run: `cd web-react && bun run test src/components/wizard/ModuleCard.test.tsx && bun run typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web-react/src/components/wizard/steps/GrillPlatformStep.tsx web-react/src/components/wizard/steps/GrillPlatformStep.test.tsx web-react/src/components/wizard/ModuleCard.tsx
git commit -m "feat(web-react): add GrillPlatformStep with module-switch round-trip + guarded reseed"
```

---

### Task 6: Wire `GrillPlatformStep` into `WizardShell`

**Files:**
- Modify: `web-react/src/components/wizard/WizardShell.tsx`
- Test: `web-react/src/components/wizard/WizardShell.test.tsx`

**Interfaces:**
- Consumes: `GrillPlatformStep` (Task 5).
- Produces: the `grillplatform` step renders `GrillPlatformStep` (no longer `PlaceholderStep`); `distance` stays a `PlaceholderStep`.

- [ ] **Step 1: Write the failing test**

Add to `web-react/src/components/wizard/WizardShell.test.tsx` a test that navigating to the grill-platform step renders the platform module select (not the placeholder message). First check how the existing tests drive step navigation (there is a "Next" button; grillplatform is step index 1, one Next-click from welcome). Add:

```typescript
  it("renders GrillPlatformStep (a module select) on the grill platform step, not the placeholder", async () => {
    renderWizard(); // existing helper in this file that renders WizardShell with a loader stub
    // advance from welcome (step 0) to grill platform (step 1)
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Grill Platform" })).toBeInTheDocument(),
    );
    // the module <select> is present; the placeholder "coming in a later release" text is not
    expect(screen.getByRole("combobox", { name: "Module" })).toBeInTheDocument();
    expect(screen.queryByText(/coming in a later release/i)).not.toBeInTheDocument();
  });
```

If the existing test file has no `renderWizard`/navigation helper, mirror whatever setup the existing WizardShell tests use (loader stub via `react-router` — reuse the file's existing harness rather than inventing a new one). Mock `fetchModuleValues` alongside the file's existing `wizardApi` mock so a stray switch never hits the network:

```typescript
rs.mock("../../helpers/wizard/wizardApi", () => ({
  // keep whatever the file already stubs (saveDraft/finishWizard/getInstallStatus/scan),
  // and add:
  fetchModuleValues: rs.fn().mockResolvedValue({ settings: {}, config: {} }),
}));
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web-react && bun run test src/components/wizard/WizardShell.test.tsx`
Expected: FAIL — the grill platform step still shows the placeholder; no "Module" combobox.

- [ ] **Step 3: Wire in the component**

In `web-react/src/components/wizard/WizardShell.tsx`:

Add the import (next to the other step imports):

```typescript
import { GrillPlatformStep } from "./steps/GrillPlatformStep";
```

Split the combined `grillplatform`/`distance` placeholder case so only `distance` stays a placeholder. Replace:

```typescript
      case "grillplatform":
      case "distance":
        return <PlaceholderStep section={currentStep} />;
```

with:

```typescript
      case "grillplatform":
        return (
          <GrillPlatformStep state={state} working={working} onChange={setWorking} baseUrl={BASE_URL} />
        );
      case "distance":
        return <PlaceholderStep section={currentStep} />;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web-react && bun run test src/components/wizard/WizardShell.test.tsx`
Expected: PASS (all, including the pre-existing WizardShell tests).

- [ ] **Step 5: Typecheck + full web suite**

Run: `cd web-react && bun run typecheck && bun run test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web-react/src/components/wizard/WizardShell.tsx web-react/src/components/wizard/WizardShell.test.tsx
git commit -m "feat(web-react): render GrillPlatformStep in the wizard grill-platform step"
```

---

### Task 7: e2e coverage + full gate

**Files:**
- Modify: `web-react/tests/e2e/wizard.spec.ts`

**Interfaces:**
- Consumes: the wired grill-platform step (Task 6); the running dev backend + rsbuild dev server (Playwright `baseURL` :5173, dev proxy `/api`→:5000).

- [ ] **Step 1: Read the existing e2e spec to match its harness**

Run: `sed -n '1,60p' web-react/tests/e2e/wizard.spec.ts`
Note how it navigates to `/wizard`, steps forward, and asserts (the probes/display steps already have e2e coverage — reuse the same navigation pattern and selectors).

- [ ] **Step 2: Add a grill-platform e2e test**

Append to `web-react/tests/e2e/wizard.spec.ts` a test that opens the wizard, advances to the grill platform step, and asserts the platform module select + a settings field render, then switches the module and confirms the card re-renders. Use the file's existing `test`/`expect` imports and navigation helpers. Concrete shape (adapt selectors to the file's conventions):

```typescript
test("grill platform step renders the platform module card and switches modules", async ({ page }) => {
  await page.goto("/wizard");
  // advance welcome -> grill platform
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();

  const moduleSelect = page.getByRole("combobox", { name: "Module" });
  await expect(moduleSelect).toBeVisible();

  // switching the platform re-fetches its config (module-values round-trip);
  // the card stays rendered with the new selection.
  await moduleSelect.selectOption({ index: 1 });
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();
});
```

- [ ] **Step 3: Run the e2e test (main checkout only — chromium is skipped in agent worktrees)**

Ensure the dev backend + dev server are up, then:
Run: `cd web-react && bunx playwright test wizard.spec.ts`
Expected: PASS (grill platform test + the pre-existing wizard e2e tests). If running in an agent worktree where `[chromium]` is skipped, note that this must be re-run in the main checkout.

- [ ] **Step 4: Full web-react gate**

Run: `cd web-react && bun run typecheck && bun run lint && bun run test && bun run build`
Expected: all PASS; coverage gate (≥75% lines per file) satisfied for the new files.

- [ ] **Step 5: Full Python suite (touched-area first, then whole)**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -v`
then: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add web-react/tests/e2e/wizard.spec.ts
git commit -m "test(web-react): e2e for the wizard grill-platform step"
```

---

## Self-Review

**1. Spec coverage:**
- Backend `/module-values` endpoint (D1) → Task 1. ✅
- Backend `board_probe_maps` + fresh-install probe_map seeding → Task 2. ✅
- `fetchModuleValues` + `WizardState.board_probe_maps` + `ModuleValues` → Task 3. ✅
- `setSectionDepValues` / `replaceProbeMap` / `reseedProbeMapForBoard` (D2 guard) → Task 4. ✅
- `GrillPlatformStep` (`configSource="none"`, async switch, error banner, loading) → Task 5. ✅
- WizardShell wiring → Task 6. ✅
- Tests: helper unit (Task 4), component (Task 5), backend endpoint (Tasks 1-2), e2e (Task 7). ✅
- Out-of-scope items (display/distance retrofit, distance step, PlatformTab, dc_fan) are correctly untouched. ✅

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — every code step shows complete code. ✅

**3. Type consistency:** `board_probe_maps: Record<string, ProbeMap>` (Task 3) matches the backend key (Task 2) and the reseed call sites (Tasks 4-5). `ModuleValues { settings: Record<string, string | null>; config: Record<string, unknown> }` (Task 3) matches `fetchModuleValues`'s return and `setSectionDepValues`'s `values` param (Task 4) and the endpoint's response (Task 1). `reseedProbeMapForBoard(currentMap, prevBoardMap, newBoardMap, firstTimeSetup)` signature is identical in Task 4's definition and Task 5's call. `disabled?: boolean` added to `ModuleCardProps` (Task 5) is consumed only by `GrillPlatformStep`. ✅
