# React Wizard Display Retrofit + Distance Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retrofit the display wizard step onto the `/api/wizard/module-values` round-trip (fixing a reachable install-killing crash), build the distance/hopper step (the last placeholder), and harden the detached installer against unknown dependency keys.

**Architecture:** Extract the async module-switch mechanics shared by three steps into a `useModuleSwitch` hook (fetch → loading → error → apply, never half-applying). `GrillPlatformStep` refactors onto it behavior-preservingly; `DisplayStep` adopts it applying **settings only** (config bag stays client-held); a new `DistanceStep` mirrors `DisplayStep` with `configSource="none"`. Separately, `wizard.py`'s settings-write loop skips + logs unknown dependency keys instead of raising.

**Tech Stack:** Flask (Python 3.14), React 19 + TypeScript (TS7/tsgo), rsbuild, `@rstest/core`, `@testing-library/react` 16 (`renderHook` available), Playwright, Biome. Package manager: **bun**.

## Global Constraints

- **bun, not npm** for all web-react install/run.
- **Testing API is `@rstest/core`** (`rs.fn`/`rs.mock`) — NOT vitest/`vi`. `.test.ts` runs in node, `.test.tsx` in jsdom (a hook test needs a React renderer → `.test.tsx`).
- **`bun run lint` must be run and exit 0** in every task that touches web-react (Biome enforces format). Two pre-existing `react-refresh` **warnings** on `App.tsx`/`WizardShell.tsx` are acceptable; **errors** are not. If Biome complains about your file's formatting, run `bunx biome check --write <file>`.
- **`bun run typecheck`** (TS7, `noUnusedLocals`) must stay clean.
- **Coverage ≥75% lines per changed file** (rstest gate).
- **Python tests:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`. Run `uvx ruff format` on changed Python before committing. PEP 758 bare-tuple `except A, B` is canonical — do NOT rewrite to `except (A, B)`.
- **Security:** no test may fire a real install. Before running any test that reaches `wizard.run_wizard`, grep the exercised path for `os.system` / `subprocess` / `sudo` / `reboot` / `shutdown` and confirm every side effect is neutralized by the `no_install` fixture.
- **D1 (display config):** on a display module switch, apply ONLY `values.settings`. Never touch `working.display_config` — preserving unsaved config edits is the point.
- **Previous-module semantics:** `apply` closes over the render's `working`, so `working.selections[section]` inside `apply` IS the pre-switch module. Do not thread `prevModule` through the hook.
- **jj boundary protocol:** the controller runs `jj new -m "wip: ..."` before each dispatch; the implementer finalizes with a single `jj desc -m`. The `git add`/`git commit` shown in steps is the logical commit; in this repo it maps to that one `jj desc`.

---

### Task 1: Installer guard — skip unknown dependency keys

**Files:**
- Modify: `wizard.py` (the settings-write loop inside `run_wizard`, currently ~lines 223-247)
- Test: `tests/unit/wizard/test_wizard_run_no_probes.py`

**Interfaces:**
- Consumes: `WizardData["modules"][module][selected]["settings_dependencies"]` (dict of dep-name → `{"settings": [...], ...}`); `set_wizard_install_status(percent, status, output)`; `set_nested_key_value(settings, path, value)`.
- Produces: no new API. Behavior change only: a setting name absent from the selected module's `settings_dependencies` is skipped + logged instead of raising `KeyError`.

- [ ] **Step 1: Read the existing test harness**

Run: `sed -n '1,60p' tests/unit/wizard/test_wizard_run_no_probes.py`

Note the `no_install` fixture (neutralizes `wizard.subprocess.run`, `wizard.is_real_hardware`, `wizard.time.sleep`) and the exact call convention used by existing tests: `install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)` then `wizard.run_wizard(settings, wizard_data, install_info)`. **Match that convention exactly** — do not guess argument order.

- [ ] **Step 2: Confirm no un-neutralized side effects**

Run: `grep -nE "os\.system|subprocess|sudo|reboot|shutdown" wizard.py | head -20`

Confirm every side-effecting call reachable from `run_wizard` under the `no_install` fixture is neutralized (the fixture stubs `subprocess.run` and forces `is_real_hardware` False). If you find a reachable `os.system` that the fixture does NOT neutralize, monkeypatch it in your test too. Do not proceed until this is confirmed.

- [ ] **Step 3: Write the failing test**

Append to `tests/unit/wizard/test_wizard_run_no_probes.py`:

```python
def test_run_wizard_skips_unknown_setting_key(ds, no_install):
    """A dependency key that isn't in the selected module's manifest entry must
    not crash the detached installer.

    Reachable from the React wizard: 12 of 30 display modules carry a
    `buttonslevel` dependency. Switching to one of the other 18 used to leave
    that stale key in the display section's settings, which reached this loop
    and raised KeyError inside the detached process -- silently freezing the
    install at its last status line while the browser polled forever.
    """
    settings = defaults.default_settings()
    settings["probe_settings"]["probe_map"]["probe_devices"] = []
    datastore_accessors.write_settings_store(settings)

    wizard_data = read_wizard()
    install_info = wizard.wizardInstallInfoExisting(settings, wizard_data)
    # A setting name that exists in no module's settings_dependencies.
    install_info["modules"]["display"]["settings"]["totally_bogus_setting"] = "x"

    # Must not raise.
    wizard.run_wizard(settings, wizard_data, install_info)

    percent, status, output = datastore_accessors.get_wizard_install_status()
    assert percent == 101  # ran to completion (restart-only), not frozen mid-install
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/wizard/test_wizard_run_no_probes.py::test_run_wizard_skips_unknown_setting_key -v`
Expected: FAIL with `KeyError: 'totally_bogus_setting'`.

- [ ] **Step 5: Implement the guard**

In `wizard.py`, inside `run_wizard`'s settings-write loop, the `else:` branch currently reads:

```python
            else:
                # 'selected'/'settingsLocation' only apply to settings that map to a
                # manifest settings_dependencies path. The probes module's only setting
                # is 'units' (handled above) and its profile_selected list is empty when
                # no probe devices are configured -- computing these before the units
                # check would raise IndexError and silently kill the detached installer.
                selected = WizardInstallInfo["modules"][module]["profile_selected"][0]
                settingsLocation = WizardData["modules"][module][selected]["settings_dependencies"][setting]["settings"]
                settings = set_nested_key_value(settings, settingsLocation, selected_setting)
```

Replace that `else:` body with:

```python
            else:
                # 'selected'/'settingsLocation' only apply to settings that map to a
                # manifest settings_dependencies path. The probes module's only setting
                # is 'units' (handled above) and its profile_selected list is empty when
                # no probe devices are configured -- computing these before the units
                # check would raise IndexError and silently kill the detached installer.
                selected = WizardInstallInfo["modules"][module]["profile_selected"][0]
                dependencies = WizardData["modules"][module][selected]["settings_dependencies"]
                dependency = dependencies.get(setting)
                if dependency is None:
                    # A setting name that isn't in the selected module's manifest entry
                    # (e.g. a stale key left over from a module switch in the wizard UI).
                    # Skip it rather than raising: this loop runs in the DETACHED
                    # installer process, where an uncaught exception freezes the install
                    # at its last status line forever.
                    set_wizard_install_status(percent, status, f"   - Skipped unknown setting {setting}")
                    continue
                settings = set_nested_key_value(settings, dependency["settings"], selected_setting)
```

Leave the `units` branch above it and the `output = ...` / `set_wizard_install_status(...)` lines below it untouched (the `continue` deliberately skips that trailing "Set ..." status line, since nothing was set).

- [ ] **Step 6: Run the test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/wizard/test_wizard_run_no_probes.py -v`
Expected: PASS (all tests in the file, including the two pre-existing ones).

- [ ] **Step 7: Run the wizard unit + web wizard suites for regressions**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/wizard/ tests/web/test_api_wizard.py -v`
Expected: PASS.

- [ ] **Step 8: Format and commit**

```bash
uvx ruff format wizard.py tests/unit/wizard/test_wizard_run_no_probes.py
git add wizard.py tests/unit/wizard/test_wizard_run_no_probes.py
git commit -m "fix(wizard): skip unknown dependency keys instead of killing the detached installer"
```

---

### Task 2: `useModuleSwitch` hook

**Files:**
- Create: `web-react/src/helpers/wizard/useModuleSwitch.ts`
- Test: `web-react/src/helpers/wizard/useModuleSwitch.test.tsx` (`.tsx` — a hook test needs the jsdom environment)

**Interfaces:**
- Consumes: `fetchModuleValues(baseUrl, section, module): Promise<ModuleValues>` from `./wizardApi` (throws on non-ok); `ModuleValues`, `WizardSection` from `./wizardTypes`.
- Produces:
  ```typescript
  export interface UseModuleSwitchParams {
    baseUrl: string;
    section: WizardSection;
    errorMessage: string;
    apply: (values: ModuleValues, newModule: string) => void;
  }
  export interface ModuleSwitch {
    loading: boolean;
    error: string | null;
    switchModule: (newModule: string) => void;
  }
  export function useModuleSwitch(params: UseModuleSwitchParams): ModuleSwitch
  ```

- [ ] **Step 1: Write the failing test**

Create `web-react/src/helpers/wizard/useModuleSwitch.test.tsx`:

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { useModuleSwitch } from "./useModuleSwitch";

const fetchModuleValues = rs.fn();
rs.mock("./wizardApi", () => ({
  fetchModuleValues: (...args: unknown[]) => fetchModuleValues(...args),
}));

afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});

describe("useModuleSwitch", () => {
  it("fetches module values and invokes apply once with them", async () => {
    fetchModuleValues.mockResolvedValue({ settings: { a: "1" }, config: {} });
    const apply = rs.fn();
    const { result } = renderHook(() =>
      useModuleSwitch({ baseUrl: "", section: "display", errorMessage: "nope", apply }),
    );

    result.current.switchModule("mod2");

    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply.mock.calls[0][0]).toEqual({ settings: { a: "1" }, config: {} });
    expect(apply.mock.calls[0][1]).toBe("mod2");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "display", "mod2");
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();
  });

  it("sets the error message and never invokes apply when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const apply = rs.fn();
    const { result } = renderHook(() =>
      useModuleSwitch({ baseUrl: "", section: "distance", errorMessage: "could not load", apply }),
    );

    result.current.switchModule("mod2");

    await waitFor(() => expect(result.current.error).toBe("could not load"));
    expect(apply).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it("clears a previous error when a new switch starts", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const apply = rs.fn();
    const { result } = renderHook(() =>
      useModuleSwitch({ baseUrl: "", section: "display", errorMessage: "could not load", apply }),
    );
    result.current.switchModule("bad");
    await waitFor(() => expect(result.current.error).toBe("could not load"));

    fetchModuleValues.mockResolvedValue({ settings: {}, config: {} });
    result.current.switchModule("good");
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(apply).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/helpers/wizard/useModuleSwitch.test.tsx`
Expected: FAIL — module `./useModuleSwitch` not found.

- [ ] **Step 3: Implement the hook**

Create `web-react/src/helpers/wizard/useModuleSwitch.ts`:

```typescript
import { useState } from "react";
import { fetchModuleValues } from "./wizardApi";
import type { ModuleValues, WizardSection } from "./wizardTypes";

export interface UseModuleSwitchParams {
  baseUrl: string;
  section: WizardSection;
  errorMessage: string;
  apply: (values: ModuleValues, newModule: string) => void;
}

export interface ModuleSwitch {
  loading: boolean;
  error: string | null;
  switchModule: (newModule: string) => void;
}

// Shared async module-switch mechanics for the wizard's module-card steps
// (grillplatform / display / distance): fetch the target module's values from
// the server, expose loading + error, and hand the values to a per-step `apply`.
// `apply` is defined in the component body and closes over that render's
// `working`, so it can read the PRE-switch selection directly -- callers must
// not thread a prevModule through this hook.
export function useModuleSwitch({
  baseUrl,
  section,
  errorMessage,
  apply,
}: UseModuleSwitchParams): ModuleSwitch {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(newModule: string) {
    setLoading(true);
    setError(null);
    try {
      const values = await fetchModuleValues(baseUrl, section, newModule);
      apply(values, newModule);
    } catch {
      // Advisory failure: leave the prior selection/deps intact so the user can
      // retry -- never half-apply a switch.
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  }

  return { loading, error, switchModule: (newModule: string) => void run(newModule) };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/helpers/wizard/useModuleSwitch.test.tsx`
Expected: PASS (3 passed).

- [ ] **Step 5: Typecheck + lint**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint`
Expected: typecheck clean; lint exit 0 (only the 2 pre-existing react-refresh warnings).

- [ ] **Step 6: Commit**

```bash
git add web-react/src/helpers/wizard/useModuleSwitch.ts web-react/src/helpers/wizard/useModuleSwitch.test.tsx
git commit -m "feat(web-react): add useModuleSwitch hook for wizard module-card steps"
```

---

### Task 3: Refactor `GrillPlatformStep` onto the hook (behavior-preserving)

**Files:**
- Modify: `web-react/src/components/wizard/steps/GrillPlatformStep.tsx`
- Test: `web-react/src/components/wizard/steps/GrillPlatformStep.test.tsx` — **must NOT be modified.** Its 3 existing tests are the behavior-preservation pin.

**Interfaces:**
- Consumes: `useModuleSwitch` (Task 2); existing `EMPTY_PROBE_MAP`, `replaceProbeMap`, `reseedProbeMapForBoard`, `selectModule`, `setDepValue`, `setSectionDepValues`.
- Produces: no signature change — `GrillPlatformStep({ state, working, onChange, baseUrl })` is unchanged.

- [ ] **Step 1: Confirm the pin is green before touching anything**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/steps/GrillPlatformStep.test.tsx`
Expected: PASS (3 passed). This is the baseline you must still meet after the refactor.

- [ ] **Step 2: Replace the component body**

Rewrite `web-react/src/components/wizard/steps/GrillPlatformStep.tsx` as:

```tsx
import { useModuleSwitch } from "../../../helpers/wizard/useModuleSwitch";
import {
  EMPTY_PROBE_MAP,
  replaceProbeMap,
  reseedProbeMapForBoard,
  selectModule,
  setDepValue,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type {
  ModuleValues,
  WizardState,
  WizardWorking,
} from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface GrillPlatformStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function GrillPlatformStep({ state, working, onChange, baseUrl }: GrillPlatformStepProps) {
  const { loading, error, switchModule } = useModuleSwitch({
    baseUrl,
    section: "grillplatform",
    errorMessage: "Couldn't load the platform configuration. Please try again.",
    apply: (values: ModuleValues, newModule: string) => {
      // `working` is this render's (pre-switch) value, so this reads the
      // PREVIOUS selection -- same semantics as capturing prevModule before the
      // fetch.
      const prevModule = working.selections.grillplatform;
      let next = selectModule(working, "grillplatform", newModule);
      next = setSectionDepValues(next, "grillplatform", values.settings);
      const prevBoardMap = state.board_probe_maps[prevModule ?? ""] ?? EMPTY_PROBE_MAP;
      const newBoardMap = state.board_probe_maps[newModule] ?? EMPTY_PROBE_MAP;
      next = replaceProbeMap(
        next,
        reseedProbeMapForBoard(
          working.probe_map,
          prevBoardMap,
          newBoardMap,
          state.first_time_setup,
        ),
      );
      onChange(next);
    },
  });

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
        onSelectModule={(m) => switchModule(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "grillplatform", k, v))}
        onConfigChange={() => {}}
      />
    </div>
  );
}
```

- [ ] **Step 3: Run the pin (unmodified) to verify behavior is preserved**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/steps/GrillPlatformStep.test.tsx`
Expected: PASS (3 passed), with `GrillPlatformStep.test.tsx` **unchanged** — confirm with `jj st` that the test file is not in your diff.

Note: the test file mocks `../../../helpers/wizard/wizardApi`, and the hook imports `fetchModuleValues` from that same module, so the existing mock still intercepts the call.

- [ ] **Step 4: Typecheck + lint + full web suite**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add web-react/src/components/wizard/steps/GrillPlatformStep.tsx
git commit -m "refactor(web-react): move GrillPlatformStep onto useModuleSwitch"
```

---

### Task 4: `DisplayStep` retrofit (settings-only round-trip)

**Files:**
- Modify: `web-react/src/components/wizard/steps/DisplayStep.tsx`
- Test: `web-react/src/components/wizard/steps/DisplayStep.test.tsx`

**Interfaces:**
- Consumes: `useModuleSwitch` (Task 2); existing `displayConfigFor`, `selectModule`, `setDepValue`, `setDisplayConfig`, `setSectionDepValues`.
- Produces: no signature change — `DisplayStep({ state, working, onChange, baseUrl })` is unchanged.

- [ ] **Step 1: Write the failing tests**

The existing `DisplayStep.test.tsx` mocks `wizardApi` with only `scan`. Extend that mock to also provide `fetchModuleValues`, and add the three new tests. Change the mock block at the top of the file to:

```tsx
const fetchModuleValues = rs.fn();
rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
  fetchModuleValues: (...args: unknown[]) => fetchModuleValues(...args),
}));
```

Also import `waitFor` from `@testing-library/react` alongside the existing imports, and reset the mock in the existing `afterEach` (change `afterEach(cleanup)` to):

```tsx
afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});
```

The existing test *"selecting a display module calls onChange with an updated display selection"* now goes through the async round-trip, so it must await. Replace that one test with:

```tsx
  it("selecting a display module fetches its values and calls onChange with the new selection", async () => {
    fetchModuleValues.mockResolvedValue({ settings: {}, config: {} });
    const onChange = rs.fn();
    render(<DisplayStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "generic" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.selections.display).toBe("generic");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "display", "generic");
  });
```

Then append these three new tests inside the same `describe("DisplayStep", ...)` block:

```tsx
  it("replaces the display dep map wholesale so a stale key from the previous module is gone", async () => {
    // 12 of 30 display modules carry `buttonslevel`; the rest carry none.
    // Switching must not leave the old module's key behind -- a stale key
    // reaches /finish and used to KeyError inside the detached installer.
    fetchModuleValues.mockResolvedValue({ settings: {}, config: {} });
    const onChange = rs.fn();
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, display: "generic" },
      settings_dep_values: {
        ...baseWorking().settings_dep_values,
        display: { buttonslevel: "HIGH" },
      },
    };
    render(<DisplayStep state={state} working={working} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "other" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.settings_dep_values.display).toEqual({});
    expect(next.settings_dep_values.display.buttonslevel).toBeUndefined();
  });

  it("preserves an unsaved display_config edit across a module switch", async () => {
    // D1: the switch applies only `settings`; display_config stays client-held,
    // so the user's unsaved edit survives switching away (and back).
    fetchModuleValues.mockResolvedValue({
      settings: {},
      config: { units: "F" }, // server copy -- must be IGNORED
    });
    const onChange = rs.fn();
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, display: "generic" },
      display_config: { generic: { units: "C" } }, // the user's unsaved edit
    };
    render(<DisplayStep state={state} working={working} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "other" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.display_config.generic.units).toBe("C");
  });

  it("shows an error banner and does not call onChange when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const onChange = rs.fn();
    render(<DisplayStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "generic" },
    });

    await waitFor(() => expect(screen.getByText(/couldn't load the display/i)).toBeInTheDocument());
    expect(onChange).not.toHaveBeenCalled();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/steps/DisplayStep.test.tsx`
Expected: FAIL — the stale-key test fails (dep map still contains `buttonslevel`), the error-banner test fails (no banner rendered), and `fetchModuleValues` is never called.

- [ ] **Step 3: Rewrite the component**

Rewrite `web-react/src/components/wizard/steps/DisplayStep.tsx` as:

```tsx
import { useModuleSwitch } from "../../../helpers/wizard/useModuleSwitch";
import {
  displayConfigFor,
  selectModule,
  setDepValue,
  setDisplayConfig,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type {
  ModuleValues,
  WizardState,
  WizardWorking,
} from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface DisplayStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function DisplayStep({ state, working, onChange, baseUrl }: DisplayStepProps) {
  const selectedDisplay = working.selections.display ?? "";
  const { loading, error, switchModule } = useModuleSwitch({
    baseUrl,
    section: "display",
    errorMessage: "Couldn't load the display configuration. Please try again.",
    // Apply ONLY the dep-values. `display_config` stays client-held so an
    // unsaved config edit survives switching modules (the returned `config` is
    // deliberately ignored).
    apply: (values: ModuleValues, newModule: string) => {
      let next = selectModule(working, "display", newModule);
      next = setSectionDepValues(next, "display", values.settings);
      onChange(next);
    },
  });

  return (
    <div className="pf-wizard-step" data-step="display">
      <h2 className="pf-wizard-step-title">Display</h2>
      {error && <p className="pf-wizard-finish-error">{error}</p>}
      <ModuleCard
        section="display"
        configSource="settings-by-module"
        modules={state.modules_metadata.display}
        selectedModule={working.selections.display}
        depValues={working.settings_dep_values.display ?? {}}
        configValues={displayConfigFor(working, selectedDisplay)}
        baseUrl={baseUrl}
        disabled={loading}
        onSelectModule={(m) => switchModule(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "display", k, v))}
        onConfigChange={(name, v) => onChange(setDisplayConfig(working, selectedDisplay, name, v))}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/steps/DisplayStep.test.tsx`
Expected: PASS (all, including the pre-existing config-edit and dep-change tests).

- [ ] **Step 5: Typecheck + lint + full web suite**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add web-react/src/components/wizard/steps/DisplayStep.tsx web-react/src/components/wizard/steps/DisplayStep.test.tsx
git commit -m "fix(web-react): refresh display dep-values on module switch (drops stale keys)"
```

---

### Task 5: `DistanceStep` + wire into `WizardShell`

**Files:**
- Create: `web-react/src/components/wizard/steps/DistanceStep.tsx`
- Create: `web-react/src/components/wizard/steps/DistanceStep.test.tsx`
- Modify: `web-react/src/components/wizard/WizardShell.tsx`
- Test: `web-react/src/components/wizard/WizardShell.test.tsx`

**Interfaces:**
- Consumes: `useModuleSwitch` (Task 2); `selectModule`, `setDepValue`, `setSectionDepValues`; `ModuleCard`.
- Produces: `DistanceStep({ state, working, onChange, baseUrl }: { state: WizardState; working: WizardWorking; onChange: (next: WizardWorking) => void; baseUrl: string })`.

- [ ] **Step 1: Write the failing DistanceStep tests**

Create `web-react/src/components/wizard/steps/DistanceStep.test.tsx`:

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { WizardState, WizardWorking } from "../../../helpers/wizard/wizardTypes";
import { DistanceStep } from "./DistanceStep";

const fetchModuleValues = rs.fn();
rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
  fetchModuleValues: (...args: unknown[]) => fetchModuleValues(...args),
}));

afterEach(() => {
  cleanup();
  rs.resetAllMocks();
});

const state: WizardState = {
  modules_metadata: {
    grillplatform: {},
    probes: {},
    display: {},
    distance: {
      // 6 of 7 real distance modules have no deps and no config -- bare card.
      none: { friendly_name: "None", settings_dependencies: {} },
      // sen0628 is the lone exception: one usb_serial_device field.
      sen0628: {
        friendly_name: "SEN0628 USB ToF",
        settings_dependencies: {
          sen0628_device: {
            friendly_name: "USB Serial Device",
            type: "usb_serial_device",
            settings: ["platform", "devices", "distance", "device"],
          },
        },
      },
    },
  },
  selections: { grillplatform: null, probes: null, display: null, distance: "none" },
  settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
  display_config: {},
  probe_map: { probe_devices: [], probe_info: [] },
  probe_profiles: [],
  probes_units: "F",
  board_probe_maps: {},
  control_mode: "Stop",
  first_time_setup: false,
  has_draft: false,
};

function baseWorking(): WizardWorking {
  return {
    selections: { grillplatform: null, probes: null, display: null, distance: "none" },
    settings_dep_values: { grillplatform: {}, probes: {}, display: {}, distance: {} },
    display_config: {},
    probe_map: { probe_devices: [], probe_info: [] },
    probes_units: "F",
  };
}

describe("DistanceStep", () => {
  it("renders a bare card for a module with no settings dependencies", () => {
    render(<DistanceStep state={state} working={baseWorking()} onChange={rs.fn()} baseUrl="" />);
    expect(screen.getByRole("heading", { name: "Distance / Hopper" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Module" })).toBeInTheDocument();
    // no dep fields for `none`
    expect(screen.queryByLabelText("USB Serial Device")).not.toBeInTheDocument();
  });

  it("renders the sen0628 USB serial field when that module is selected", () => {
    const working = {
      ...baseWorking(),
      selections: { ...baseWorking().selections, distance: "sen0628" },
    };
    render(<DistanceStep state={state} working={working} onChange={rs.fn()} baseUrl="" />);
    expect(screen.getByLabelText("USB Serial Device")).toBeInTheDocument();
  });

  it("switching modules fetches values and applies them to the distance dep map", async () => {
    fetchModuleValues.mockResolvedValue({
      settings: { sen0628_device: "/dev/ttyACM0" },
      config: {},
    });
    const onChange = rs.fn();
    render(<DistanceStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "sen0628" },
    });

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const next = onChange.mock.calls[0][0] as WizardWorking;
    expect(next.selections.distance).toBe("sen0628");
    expect(next.settings_dep_values.distance.sen0628_device).toBe("/dev/ttyACM0");
    expect(fetchModuleValues).toHaveBeenCalledWith("", "distance", "sen0628");
  });

  it("shows an error banner and does not call onChange when the fetch fails", async () => {
    fetchModuleValues.mockRejectedValue(new Error("boom"));
    const onChange = rs.fn();
    render(<DistanceStep state={state} working={baseWorking()} onChange={onChange} baseUrl="" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Module" }), {
      target: { value: "sen0628" },
    });

    await waitFor(() => expect(screen.getByText(/couldn't load the sensor/i)).toBeInTheDocument());
    expect(onChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/steps/DistanceStep.test.tsx`
Expected: FAIL — module `./DistanceStep` not found.

- [ ] **Step 3: Implement the component**

Create `web-react/src/components/wizard/steps/DistanceStep.tsx`:

```tsx
import { useModuleSwitch } from "../../../helpers/wizard/useModuleSwitch";
import {
  selectModule,
  setDepValue,
  setSectionDepValues,
} from "../../../helpers/wizard/wizardState";
import type {
  ModuleValues,
  WizardState,
  WizardWorking,
} from "../../../helpers/wizard/wizardTypes";
import { ModuleCard } from "../ModuleCard";

export interface DistanceStepProps {
  state: WizardState;
  working: WizardWorking;
  onChange: (next: WizardWorking) => void;
  baseUrl: string;
}

export function DistanceStep({ state, working, onChange, baseUrl }: DistanceStepProps) {
  const { loading, error, switchModule } = useModuleSwitch({
    baseUrl,
    section: "distance",
    errorMessage: "Couldn't load the sensor configuration. Please try again.",
    apply: (values: ModuleValues, newModule: string) => {
      let next = selectModule(working, "distance", newModule);
      next = setSectionDepValues(next, "distance", values.settings);
      onChange(next);
    },
  });

  return (
    <div className="pf-wizard-step" data-step="distance">
      <h2 className="pf-wizard-step-title">Distance / Hopper</h2>
      {error && <p className="pf-wizard-finish-error">{error}</p>}
      <ModuleCard
        section="distance"
        configSource="none"
        modules={state.modules_metadata.distance}
        selectedModule={working.selections.distance}
        depValues={working.settings_dep_values.distance ?? {}}
        configValues={{}}
        baseUrl={baseUrl}
        disabled={loading}
        onSelectModule={(m) => switchModule(m)}
        onDepChange={(k, v) => onChange(setDepValue(working, "distance", k, v))}
        onConfigChange={() => {}}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/steps/DistanceStep.test.tsx`
Expected: PASS (4 passed).

- [ ] **Step 5: Write the failing WizardShell test**

In `web-react/src/components/wizard/WizardShell.test.tsx`, mirror the file's existing helpers (it has a `renderShell` / `fixtureState` / `clickNext` harness and an `rs.mock` of `../../helpers/wizard/wizardApi` that already stubs `fetchModuleValues`). Add:

```tsx
  it("renders DistanceStep on the distance step, not the placeholder", async () => {
    renderShell();
    clickNext("Grill Platform");
    clickNext("Probes");
    clickNext("Display");
    clickNext("Distance / Hopper");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Distance / Hopper" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("combobox", { name: "Module" })).toBeInTheDocument();
    expect(screen.queryByText(/coming in a later release/i)).not.toBeInTheDocument();
  });
```

Adapt the navigation calls to the file's real helper signature — read the existing grill-platform test in that file and mirror it exactly.

- [ ] **Step 6: Run to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/WizardShell.test.tsx`
Expected: FAIL — the distance step still renders the placeholder.

- [ ] **Step 7: Wire DistanceStep into the shell**

In `web-react/src/components/wizard/WizardShell.tsx`, add the import next to the other step imports:

```typescript
import { DistanceStep } from "./steps/DistanceStep";
```

and replace the distance case:

```typescript
      case "distance":
        return <PlaceholderStep section={currentStep} />;
```

with:

```typescript
      case "distance":
        return (
          <DistanceStep state={state} working={working} onChange={setWorking} baseUrl={BASE_URL} />
        );
```

`PlaceholderStep` is now unused by the shell — remove its now-dead import if `noUnusedLocals`/Biome flags it, but **keep** `PlaceholderStep.tsx` and `PlaceholderStep.test.tsx` (the component is still independently tested and harmless).

- [ ] **Step 8: Run to verify it passes**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/components/wizard/WizardShell.test.tsx`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 9: Typecheck + lint + full web suite**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add web-react/src/components/wizard/steps/DistanceStep.tsx web-react/src/components/wizard/steps/DistanceStep.test.tsx web-react/src/components/wizard/WizardShell.tsx web-react/src/components/wizard/WizardShell.test.tsx
git commit -m "feat(web-react): add DistanceStep and render it in the wizard distance step"
```

---

### Task 6: e2e + full gate

**Files:**
- Modify: `web-react/tests/e2e/wizard.spec.ts`

**Interfaces:**
- Consumes: the wired distance step (Task 5). Playwright `baseURL` :5173 with the dev proxy `/api`→:5000.

- [ ] **Step 1: Read the existing e2e spec conventions**

Run: `sed -n '1,40p' web-react/tests/e2e/wizard.spec.ts`
Note the imports (`import { expect, test } from "@playwright/test"`), `page.goto("/wizard")`, and the `getByRole("button", { name: "Next" })` step navigation used by the display/probes/grill-platform tests.

- [ ] **Step 2: Append the distance e2e test**

Append to `web-react/tests/e2e/wizard.spec.ts`:

```typescript
test("distance step renders the sensor module card and switches modules", async ({ page }) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  // Welcome -> Grill Platform -> Probes -> Display -> Distance / Hopper
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Probes" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Display" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Distance / Hopper" })).toBeVisible();

  const moduleSelect = page.getByRole("combobox", { name: "Module" });
  await expect(moduleSelect).toBeVisible();

  // Switching the sensor re-fetches its config (module-values round-trip); the
  // card stays rendered with the new selection.
  await moduleSelect.selectOption("sen0628");
  await expect(page.getByLabel("USB Serial Device")).toBeVisible();
});
```

- [ ] **Step 3: Run the e2e (main checkout only)**

The Flask backend must be serving CURRENT code (if it was started before this branch's backend changes, reload it — e.g. `kill -HUP <gunicorn master pid>` — and confirm `POST /api/wizard/module-values` returns 400 rather than 404 for an unknown module). Playwright starts the rsbuild dev server itself (`reuseExistingServer: true`).

Run: `cd /home/dannyb/sources/PiFire/web-react && bunx playwright test wizard.spec.ts --reporter=line`
Expected: PASS (4 tests — display, probes, grill platform, distance). In an agent worktree where `[chromium]` is skipped, report that and note it must be re-run in the main checkout.

- [ ] **Step 4: Full web-react gate**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test && bun run build`
Expected: all PASS; coverage gate (≥75% lines/file) satisfied for `useModuleSwitch.ts` and `DistanceStep.tsx`.

- [ ] **Step 5: Full Python suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/wizard/ tests/web/test_api_wizard.py -v`
then: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add web-react/tests/e2e/wizard.spec.ts
git commit -m "test(web-react): e2e for the wizard distance step"
```

---

## Self-Review

**1. Spec coverage:**
- Installer guard (D2) → Task 1. ✅
- `useModuleSwitch` shared hook → Task 2. ✅
- GrillPlatformStep refactor onto the hook (behavior-preserving) → Task 3. ✅
- DisplayStep retrofit, settings-only (D1) + stale-key crash fix → Task 4. ✅
- DistanceStep + WizardShell wiring (last placeholder) → Task 5. ✅
- Tests: hook unit (T2), display stale-key + config-preservation + error (T4), distance bare-card/sen0628/switch/error (T5), installer skip (T1), e2e (T6). ✅
- Out-of-scope items (ConfigOptionField manifest defaults, PlatformTab, first_time_setup redirect, dc_fan) correctly untouched. ✅

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — every code step carries complete code. The two places that say "mirror the file's existing helper" (T5 Step 5, T6 Step 1) name the exact file and the exact existing test to copy, and are accompanied by concrete code. ✅

**3. Type consistency:** `useModuleSwitch(params: UseModuleSwitchParams): ModuleSwitch` with `apply: (values: ModuleValues, newModule: string) => void` is identical in T2's definition and all three call sites (T3, T4, T5). `ModuleValues` and `WizardSection` come from `wizardTypes.ts` (added in the grillplatform slice). `switchModule(newModule: string): void` is used as `onSelectModule={(m) => switchModule(m)}` uniformly. `EMPTY_PROBE_MAP`/`reseedProbeMapForBoard` signatures in T3 match the shipped helpers. ✅
