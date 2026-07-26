# React Probe Configuration Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## READ THIS FIRST — the backlog entry is factually wrong

`react-migration-backlog.md:289-290` says:

> **probeconfig** — standalone probe-config page; the wizard's probes step is done, so this
> can likely reuse the shipped reducer and cards

Two claims. **One is right, one is wrong.**

**WRONG — "standalone probe-config page."** `blueprints/probeconfig/` renders **no page**. It has
no `<!doctype>`, no navbar, no `render_template` — only `render_template_string` over two Jinja
macros (`routes.py:393-399`). It is a **fragment API for the Flask wizard**, and the Flask wizard
is its only consumer: `wizard.html:3` imports `render_probe_devices` / `render_probe_ports`
inline, `wizard.html:349` loads `probeconfig.js`, and that script re-fetches the two fragments
into `#probeDevicesCard` / `#probePortsCard` with `$(...).load("/probeconfig", {...})`
(`probeconfig.js:162, 172, 194, 203, 225, 247, 262, 297`). No other template, blueprint or nav
entry references it. `tests/web/test_page_probeconfig.py:6-23` says the same thing in its own
docstring: *"probeconfig_page is never navigated to directly by a user: it has no full HTML page
of its own."*

**RIGHT — "can reuse the shipped reducer and cards."** It can. Completely. Every one of the ten
`(section, action)` behaviours this endpoint implements is already shipped in React by the wizard
probes step. The mapping is 1:1 and is tabulated below. The *editing surface* needs zero new
logic.

**So what is actually missing?** Not the editor — the **page**. The shipped editor is wired to
the wizard's `wizard:install` draft blob and only reaches live hardware after the detached
installer runs (`wizard.py:227`). What no UI in either stack offers is **editing the LIVE probe
map without re-running the whole wizard**. That is the gap this plan fills, and it is
deliberately delivered where Flask already puts probe configuration in its own information
architecture: **inside Settings**, as `/settings/probes`. Flask's settings page owns the live
per-probe editor today (`_settings_probe_select` / `_settings_probe_config` /
`_settings_probe_config_save`, `blueprints/settings/routes.py:59-108`), and React's settings has
**no Probes tab at all** — the 2026-07-25 audit records it as *"Probe Settings / Probe Profiles
settings tabs deferred to a later sub-project."* This plan is that sub-project's first half.

**Honest cost split.** The React editing components and reducer are reused **verbatim, in place,
with no move and no rename** (~1,100 lines of shipped TSX/TS, zero new lines). What is genuinely
new is the plumbing around them: two REST endpoints, one control-loop flag, one CSS extraction
(the shipped styles are locked inside `wizard.css`, which only `WizardShell` imports), one tab
component, and an e2e spec. Roughly **70% reuse of behaviour, 0% reuse of the delivery path.**

---

**Goal:** Ship a React **Probes** settings tab at `/settings/probes` that edits the LIVE probe
map (`settings["probe_settings"]["probe_map"]`) — devices, ports, probes, all five hardware
discovery flows — by reusing the shipped wizard probes components unchanged, and applies the
result to a running PiFire without a wizard re-run or a server restart.

**Architecture:** The tab is a thin shell around the **already-shipped** `DevicesCard` +
`PortsCard` + `probeReducer`. It seeds its working `ProbeMap` from the settings loader's
`settings.probe_settings.probe_map` (no new read endpoint for the map), fetches only the probes
**module manifest** from one new GET, and posts the whole edited map to one new POST. The POST is
the only place that knows about hardware safety: mode gate, full cross-subsystem bus-kind
validation, a dependency-installability guard, `history_page.probe_config` regeneration, and a
new `probe_map_update` control flag that makes the running controller rebuild its probe devices
in place via `ProbesMain.update_probe_map()` — an existing, currently **dead** method
(`probes/main.py:84-88`, zero callers) that this plan revives instead of shelling out a restart.

**Tech Stack:** React 19 + react-router 8, TypeScript 7 (`typescript7` bin, `typescript@5.9`
retained for the eslint parser), rsbuild 2, Biome 2.5.5 + ESLint 10, `@rstest/core` 0.11.4,
Playwright, **bun**; Flask + SQLite datastore (`common/datastore.py`) on the Python side.

---

## Global Constraints

Copied verbatim from the brief and from live config. Do not paraphrase these into something
weaker.

### Safety (non-negotiable)

- **`pifire.db` in the source directory is NOT the real live DB, but the e2e suite IS globally
  destructive to whatever backend it reaches, and runs `workers: 1` for that reason.**
  (`web-react/playwright.config.ts:23`; the comment there enumerates the specific destructive
  actions.) Never run two e2e suites against one backend.
- **SQLite is authoritative. `settings.json` is ONLY ever an export produced by
  `scripts/export-settings-json.py` when a human runs it. Never write a plan step that reads or
  writes `settings.json` as live state.** Every read in this plan goes through
  `common.datastore_accessors.read_settings()`; every write through `write_settings()` /
  `save_settings_and_flag_update()`.
- **Any test that can reach an installer/shell-out path must neutralize `os.system` /
  `subprocess` FIRST. An `is_real_hardware()` flag is not enough — it defaults to True and this
  repo has really rebooted the developer's machine twice.** Concretely, before running anything
  in Tasks 2 or 3: `rg -n "os\.system|subprocess|sudo|reboot|shutdown|restart_scripts"` over
  every module the test imports, and `monkeypatch.setattr` each one at its *import site*.
  **Moving code out of a `patch.object`'d module silently disarms the mock** — this repo has
  been bitten by that three times. This plan deliberately does **not** call `restart_scripts()`
  anywhere (see Task 3's rationale); if a reviewer proposes adding it, that neutralization
  requirement comes with it.

### Toolchain

- **bun, NOT npm.** `bun install`, `bun run <script>`, `bun install -g`. Commit `bun.lock`.
- **Gates every task must pass** (run from `web-react/` for the JS ones):
  `bun run typecheck && bun run lint && bun run test && bun run gen:types:check`
- Test runner is **`@rstest/core`** — `rs.fn`, `rs.mock`, `rs.stubGlobal`. **`vi` does not
  exist.** Run it as **`bun run test`**, never bare `bun test` (that is bun's own runner and will
  not resolve the rstest config).
- **rstest project globs** (`rstest.config.ts:57-64`, three projects): `src/**/*.test.ts` → node,
  `src/**/*.test.tsx` → jsdom, and root-level `*.test.ts` → node (the `unit-config` project, for
  `ports.ts`-style config modules). **Anything outside `src/` or the package root is never
  globbed** — a test file placed in `web-react/tests/` silently never runs. Every test this plan
  adds lives under `src/`, beside its module.
- **Coverage floor is per-file**: `thresholds: { "src/**/*.{ts,tsx}": { lines: 75, perFile: true } }`
  (`rstest.config.ts:53-55`). Every new `src/` module needs its own test file. `bun run test`
  does not enforce it; `bun run test:coverage` does — run it once at Task 8.
- Python: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/`. A bare `python`
  gives false failures (the venv holds PySide6).
- **Format Python with `.venv/bin/ruff format` — NOT `uvx ruff`.** The repo pins `ruff>=0.8.0,<0.16`
  on purpose (`pyproject.toml:91`); `uvx` resolves 0.16+ and reformats 1,422 files. Installed
  version is 0.15.22. **Run it on every changed Python file before every commit** — standing repo
  rule.
- Do not "fix" `except TypeError, ValueError` (`blueprints/api_wizard/routes.py:58`). The repo is
  Python 3.14+ and the paren-free form is ruff-canonical.

### House React style

- **No `setState` in `useEffect` for derived state** — the React Compiler is active. Use
  render-phase adjustment: the `prev`-compare idiom in `settings/tabs/SafetyTab.tsx:36-40` and
  `PelletsTab.tsx:38-43`. The tab in Task 6 mirrors loader data into working state and uses it
  verbatim.
- **No suppressions**: no `biome-ignore`, no `eslint-disable`, no `@ts-ignore`, no
  `@ts-expect-error`, no `any`.
- `react-refresh/only-export-components`: non-components live under `src/helpers/`, never beside
  a component. **`bun run lint` must exit 0**; exactly **2 pre-existing `react-refresh` warnings**
  are the baseline — a third is yours.
- **`helpers/` must never import from `components/`** — enforced by `src/structure.test.ts:117-131`.
- **`useLiveState()` may only be imported by `AppShell.tsx`** — enforced by
  `src/structure.test.ts:100-115`. Pages read the bundle from Outlet context via `useShellState()`.
- Same-origin fetch base everywhere: `import.meta.env.PUBLIC_PIFIRE_URL || ""`. Never
  `targetUrl` from the shell context — it is absolute, Flask sends no CORS headers, and the
  notify slice already shipped that bug once.
- **Locators must not rely on loose text matching.** Use `exact: true`, or a role+name that
  cannot collide, or scope with `within` / `getByRole("region", { name })`. This project has lost
  time to `name: "Set"` matching an `aria-label="settings"` gear.

### e2e / harness

- Playwright needs the **main checkout or an explicit `PIFIRE_DB_PATH`**: `common/datastore.py`
  resolves `DB_PATH` relative to its own checkout, so a jj workspace seeds a different
  `pifire.db` than the backend serves.
- Point the dev proxy with **`PIFIRE_BACKEND_URL`, never `PUBLIC_PIFIRE_URL`** — rsbuild inlines
  every `PUBLIC_*` name into the browser bundle, making every request cross-origin.
- **Restart gunicorn before trusting an e2e result.** A worker started before a backend change
  serves the old code and new endpoints 404. This has cost three separate tasks.
- Chromium-dependent Python web tests **skip** in agent worktrees (`requires_chromium`,
  `tests/web/conftest.py:85-88`). Re-run any touched `tests/web/*.py` in the main checkout before
  merging.

---

## Verified facts (checked against live code on 2026-07-26 — do not re-derive, do not guess)

### F1. The Flask endpoint: every `(section, action)` pair and what it persists

`blueprints/probeconfig/routes.py` is one route, `probeconfig_page()` (`:384-425`), dispatching a
9-entry map keyed on `(r["section"], r["action"])` (`:371-381`) with `_probeconfig_*` handlers.
A tenth pair is used in production but has **no handler** and falls through to the re-render tail.

| # | `(section, action)` | Handler (line) | Persists? | React equivalent (SHIPPED) |
|---|---|---|---|---|
| 0 | `GET /probeconfig/` | `:392-399` | no — renders both cards | `DevicesCard` + `PortsCard` |
| 1 | `("devices","delete_device")` | `_probeconfig_devices_delete_device` `:9-23` | **yes** — pops the device, drops every `probe_info` row on it | `deleteDevice` (`probeReducer.ts:113-129`) + `ConfirmAction` |
| 2 | `("devices","add_config")` | `:26-51` | **no** — renders the add form with manifest defaults + `available_probes` | `DevicesCard.openAdd` + `defaultsFor` (`DevicesCard.tsx:30-55`), client-side, no round trip |
| 3 | `("devices","add_device")` | `:54-107` | **yes** — appends a device; alnum-strips the name; duplicate/blank-name alerts; per-device bus-kind validation with `settings=None` | `addDevice` (`probeReducer.ts:40-70`) + `validateBusKinds` (`wizardApi.ts:110-120`) |
| 4 | `("devices","edit_config")` | `:110-141` | **no** — renders the edit form; **backfills manifest defaults for options the saved device lacks** (`:122-124`) | `DevicesCard.openEdit` (`DevicesCard.tsx:57-70`), same backfill |
| 5 | `("devices","edit_device")` | `:144-190` | **yes** — renames; carries `module`/`module_filename`/`ports` over from the OLD entry; re-validates bus kinds | `editDevice` (`probeReducer.ts:72-111`) |
| 6 | `("ports","delete_probe")` | `:193-206` | **yes** — pops the probe and scrubs its label from every virtual device's `probes_list` | `deleteProbe` (`probeReducer.ts:310-328`) + `ConfirmAction` |
| 7 | `("ports","config")` | `:209-240` | **no** — renders the port form; builds `device_port` options from every device×port and `profile_id` options from `settings["probe_settings"]["probe_profiles"]` | `PortForm` + `devicePortOptions` (`probeReducer.ts:30-38`) |
| 8 | `("ports","add_probe")` | `_probeconfig_ports_add_edit_probe` `:243-368` | **yes** — appends; alnum label; splits `device_port`; snapshots the profile by value; one-Primary check | `addProbe` (`probeReducer.ts:171-193`) |
| 9 | `("ports","edit_probe")` | same handler | **yes** — plus the **virtual-port reposition algorithm** (`:321-358`) | `editProbe` (`probeReducer.ts:195-308`), branches 3a/3b/3c transcribed with the legacy line refs in comments |
| 10 | `("ports","refresh_probes")` | **none** — `probeconfig.js:164` posts it; `_PROBECONFIG_DISPATCH.get()` returns `None`, the `elif r["section"] == "ports"` tail re-renders | n/a | no-op: the React map is client-held, so there is nothing to re-fetch |

The five discovery flows the forms embed are served by the **wizard** blueprint, not this one
(`probeconfig.js:58, 87, 120, 146` → `blueprints/wizard/routes.py:148, 170, 220, 256`), and all
five already have React counterparts against `/api/wizard/*`:

| Flow | Flask | React (SHIPPED) | Endpoint (EXISTS) |
|---|---|---|---|
| Bluetooth scan | `/wizard/bt_scan` | `BluetoothPicker.tsx` | `POST /api/wizard/scan/bluetooth` (`api_wizard/routes.py:450`) |
| ThermoWorks Cloud | `/wizard/thermoworks_discover` | `ThermoworksPicker.tsx` | `POST /api/wizard/scan/thermoworks` (`:490`) |
| Extended I2C / MCP2221 / FT232H | `/wizard/i2c_bus_scan` | `I2cBusPicker.tsx` | `POST /api/wizard/scan` `{kind}` (`:219`) |
| USB serial | `/wizard/usb_serial_scan` | `UsbSerialPicker.tsx` | `POST /api/wizard/scan` `{kind:"usb_serial"}` (`:219`) |
| Bus-kind coexistence | inline in `add_device`/`edit_device` | `DevicesCard.submit` | `POST /api/wizard/probes/validate-bus-kinds` (`:475`) |

**These five endpoints are generic hardware discovery, not wizard state.** Calling them from a
settings tab needs no backend change and no rename. Do not "clean this up" by duplicating them
under `/api/probes/*`.

### F2. The store the Flask endpoint writes is NOT live settings

Every persisting handler above calls `load_wizard_install_info()` / `store_wizard_install_info()`
— SQLite key `wizard:install` (`common/datastore_accessors.py`). Live settings are read **once**,
for `probe_profiles` lookups only (`routes.py:234-235, 263-266`). The map reaches live settings
only when the detached installer runs: `wizard.py:227`,
`settings["probe_settings"]["probe_map"] = WizardInstallInfo["probe_map"]`.

`tests/web/test_page_probeconfig.py:24-42` documents the consequence: `get_blob()` returns `None`
for a missing key and `load_wizard_install_info()` does an unguarded `json.loads`, so
**`wizard:install` has no seeded default** — `conftest.py`'s `_seed_fresh_db()` does not create it.
Every test touching it must seed it first.

### F3. `run_wizard` does three things this plan must account for

`wizard.py:189-450`:

1. `settings["probe_settings"]["probe_map"] = WizardInstallInfo["probe_map"]` (`:227`).
2. `settings["history_page"]["probe_config"] = default_probe_config(settings)` (`:230`) —
   **regenerated from the new map.** `default_probe_config` (`common/defaults.py:319-348`)
   preserves existing per-label entries and colour-assigns new ones. A probe map written without
   this leaves the history chart configured for probes that no longer exist.
3. Installs each selected module's `apt_dependencies`, `py_dependencies` and `command_list`
   (`:319-430`), then signals a restart (`percent = 101`) or a reboot (`percent = 142`).

Point 3 is the whole reason a live probe-map editor needs a guard. **Verified from
`wizard/wizard_manifest.json` programmatically:**

- **6 modules need NO install** (all three dep lists empty): `max31865`, `prototype`,
  `virtual_average`, `virtual_highest`, `virtual_lowest`, `virtual_median`.
- **12 modules DO**: `ads1115_adafruit`, `ads1015_adafruit`, `max31865_adafruit`,
  `max31856_adafruit`, `mcp9600_adafruit`, `bt_ibbq`, `bt_ibt6xs`, `bt_meater`, `bt_meater_exp`,
  `thermoworks_cloud`, `ads1115`, `ds18b20`.

A module **already present in the live map** has necessarily been installed, so it is always
allowed. That pair of rules is the entire dependency guard.

### F4. A probe-map change is NOT picked up by `probe_profile_update`

`controller/runtime/controller.py:361-367` handles `control["probe_profile_update"]` by calling
`self.probe_complex.update_probe_profiles(...)`, which is `probes/main.py:90-92` →
`device.set_profiles(probe_info)` for each already-constructed device. `set_profiles`
(`probes/base.py:393-401`) only refills `self.probe_profiles` per port. It does **not** rebuild
`port_map`, does not construct devices, and does not notice an added/removed/renamed probe.

`ProbesMain.update_probe_map(probe_map)` (`probes/main.py:84-88`) **does** — it reassigns
`probe_devices`/`probe_info` and re-runs `_setup_probe_devices`, which re-imports each module and
reconstructs every `ReadProbes` instance. It has **zero callers anywhere in the repo** (verified
with `grep -rn update_probe_map`). Its `error = self._setup_probe_devices(...)` is also dead:
`_setup_probe_devices` returns `None` unconditionally (`:33-63`). Task 3 wires it up and fixes
the return.

Devices are built at startup by `controller/runtime/devices.py:190-219` and reached as
`ctx.devices.probe_complex` (`context.py:10`, `controller.py:76`, `modes/base.py:68`).

### F5. What already exists in React — reused verbatim, not moved

Under `web-react/src/`:

| Module | Lines | Role |
|---|---|---|
| `helpers/wizard/probeReducer.ts` | 328 | Pure `ProbeMap` reducer: `addDevice`/`editDevice`/`deleteDevice`/`addProbe`/`editProbe`/`deleteProbe`, `alnum`, `isVirtualDevice`, `availableProbes`, `devicePortOptions`, plus the virtual-port reposition. Carries 4 deliberate divergences from legacy, labelled `FIX 1`–`FIX 4`. |
| `helpers/wizard/probeTypes.ts` | 73 | `ProbeMap`, `ProbeDevice`, `Probe`, `ProbeProfile`, `ProbeModuleData`, `ProbeConfigField`, `BtScanRow`, `ThermoworksRow`, `RowsResult<T>` |
| `helpers/wizard/wizardApi.ts` | 120 | includes `scan`, `scanBluetooth`, `scanThermoworks`, `validateBusKinds` — the four calls the cards make |
| `helpers/wizard/wizardAssets.ts` | 22 | `moduleImageUrl(baseUrl, image)` → `${baseUrl}/static/img/wizard/<file>` |
| `components/wizard/probes/DevicesCard.tsx` | 200 | devices table, add/edit form host, bus-kind check, cascade-delete confirm |
| `components/wizard/probes/PortsCard.tsx` | 156 | ports table, add/edit form host, delete confirm, Primary-probe guard errors |
| `components/wizard/probes/DeviceForm.tsx` | 65 | module photo + description + notes + per-field config + unique-name input |
| `components/wizard/probes/PortForm.tsx` | 145 | name / device+port / type / profile / enabled, with verbatim manifest hint copy |
| `components/wizard/probes/DeviceConfigField.tsx` | 151 | dispatches all 8 `ProbeFieldType`s incl. the 5 discovery pickers |
| `components/wizard/probes/BluetoothPicker.tsx` / `ThermoworksPicker.tsx` | 53 / 50 | two of the discovery flows |
| `components/wizard/fields/I2cBusPicker.tsx` / `UsbSerialPicker.tsx` | 82 / — | free-text + Discover (audit finding C5 already fixed) |
| `components/wizard/DiscoveryPanel.tsx` | — | grouped scan-result pills |
| `components/dashboard/ConfirmAction.tsx` | 29 | `{open,title,message,onConfirm,onCancel}` — already wired into both cards (audit finding C7 fixed) |

Existing tests that pin all of it: `probeReducer.devices.test.ts` (261), `probeReducer.probes.test.ts`
(264), `probeReducer.reposition.test.ts` (98), `DevicesCard.test.tsx` (277), `PortsCard.test.tsx`
(227), `DeviceConfigField.test.tsx` (222), `DeviceForm.test.tsx` (98), `PortForm.test.tsx` (98).
**None of these change.** If a step in this plan makes one of them red, the step is wrong.

### F6. `ProbesStep`'s props are the exact seam to reuse

`components/wizard/steps/ProbesStep.tsx:12-42` is 42 lines and does three things: a units
`<select>`, `<DevicesCard probeMap modules baseUrl onChange>`, `<PortsCard probeMap profiles
onChange>`. The tab in Task 6 is the same wiring against different data sources. **`ProbesStep`
itself is not reused** — it is bound to `WizardWorking` and to the wizard's units field, which is
a `/settings/units` concern in the React app. The two cards are reused directly.

### F7. The API surface — two endpoints must be newly created

| Need | Endpoint | Status |
|---|---|---|
| Read live settings (incl. `probe_settings.probe_map` + `probe_profiles`) | `GET /api/settings` | **EXISTS** (`api/routes.py:47-48`, `_API_GET_ACTIONS["settings"]`). Already fetched by `settingsLoader`. **No new read endpoint for the map.** |
| Read the probes **module manifest** (18 modules, `device_specific.config`, images, deps) | — | **MUST BE CREATED** → `GET /api/probe_modules` (Task 1). `GET /api/wizard/state` ships it (`api_wizard/routes.py:133`) but also computes draft resumption, board reseed maps and `first_time_setup`, and its `probe_map` is the **draft** map — wrong source for a live editor. |
| Apply a live probe map with hardware guards | — | **MUST BE CREATED** → `POST /api/probe_map` (Task 2). `POST /api/settings_update` (`api/routes.py:168`) *could* carry `{probe_settings:{probe_map}}` — `deep_update` replaces lists wholesale and `ProbeMap`'s schema is `list[dict]` (`common/settings_schema.py:229-234`) — but it performs **none** of: mode gate, cross-subsystem bus-kind validation, dependency guard, `history_page.probe_config` regeneration, or a probe-map control flag. `_SETTINGS_UPDATE_ALLOWED_FLAGS` (`:159-165`) has no probe-map entry either. Using it would silently ship a half-applied config. |
| Hardware discovery ×4 + bus-kind validate | `POST /api/wizard/scan`, `/scan/bluetooth`, `/scan/thermoworks`, `/probes/validate-bus-kinds` | **EXIST** — reused unchanged (F1). |

**Exactly two new REST endpoints.** Same as the pellets slice needed two, and for the same
reason: no existing path read or wrote this blob over REST with the semantics the page needs.

### F8. The CSS is locked inside the wizard

`web-react/src/main.tsx:4-6` globally imports `theme.css`, `dashboard.css` and `settings.css`
only. `components/wizard/wizard.css` is imported **solely** by `WizardShell.tsx`, and
`wizardStyles.test.ts:100-104` pins that.

`settings.css` declares the *generic* vocabulary the cards lean on — `.pf-field` (`:76`),
`.pf-field-column` (`:82`), `.pf-field-label` (`:86`), `.pf-input` (`:95`), `.pf-field-hint`
(`:192`), and `.pf-probes-card { position: relative }` (`:342`, the containing block
`ConfirmAction`'s absolute scrim needs).

But every class that makes the probe editor *look like anything* lives in `wizard.css`:
`.pf-probes-table*`, `.pf-port-form`, `.pf-device-form`, `.pf-form-actions`, `.pf-module-image`
/`-name`/`-description`/`-notes`, `.pf-discovery-*`, and the `.pf-wizard`-scoped `.pf-btn` /
`.pf-btn-primary` / `.pf-probes-card` appearance rules (`wizard.css:153-192, 253-299, 312-500`).
Rendered outside `.pf-wizard`, the cards would be unstyled and mis-scoped. Task 5 extracts that
vocabulary. `wizardStyles.test.ts:64-65, 94-96` asserts wizard-owned classes are declared **in
`wizard.css` itself**, so that guard changes with the extraction — a principled edit, not a
suppression.

### F9. Settings-tab idioms this tab must follow

- Route tree: `App.tsx:65-88`, `/settings` → `SettingsShell` + `settingsLoader` +
  `errorElement: <SettingsError/>` + `HydrateFallback`, with 11 child tabs. Tab pills come from
  `SETTINGS_TABS` in `SettingsShell.tsx:4-17`; `<Outlet context={{settings, mode, controllerMeta}}>`
  at `:45`.
- `settingsLoader` (`helpers/settings/settingsRoutes.ts:13-24`) `Promise.all`s
  `getSettings`/`getMode`/`getControllerMetadata`.
- `useSaveSettings()` (`helpers/settings/useSaveSettings.ts`) owns save state and calls
  `revalidator.revalidate()` on success — which re-runs **every active loader**, including a
  child route's. That is how the tab gets a fresh map back after applying.
- `SaveBar` (`components/settings/SaveBar.tsx`) is presentational: `{onSave, saving, status}`.
- Render-phase mirror idiom: `SafetyTab.tsx:36-40`.
- **Type-name collision hazard:** `helpers/settings/settingsTypes.gen.ts:510` exports an
  interface named **`ProbeMap`** with all-optional members, and `helpers/wizard/probeTypes.ts:25`
  exports a **different** `ProbeMap` with required members. Any module importing both must
  alias. Task 4 owns the single narrowing function so nowhere else has to.

### F10. Existing test coverage that pins current behaviour

- `tests/web/test_page_probeconfig.py` — 11 Playwright tests, `requires_chromium`, driving
  `/probeconfig/` with `page.request.post`. Covers the base GET, all 9 dispatched actions, and
  **four dedicated tests for the virtual-port ordering invariant** (`:375-546`). **This file must
  stay green and must not be edited.** It is the characterization net proving the Flask fragment
  API still behaves after this work.
- `tests/web/test_webapp_sqlite.py:186-200` —
  `test_probeconfig_add_usb_hid_probe_not_blocked_by_stale_platform_bus`, the pin on
  `add_device`'s `settings=None` bus-kind check.
- `tests/web/test_api_wizard.py` — 591 lines, `ds` + `client` fixtures (`:10-14`); the model for
  Tasks 1–2's tests. `ds` is `tests/conftest.py:138-143` (a `tmp_path` SQLite datastore).
- `tests/characterization/test_controller_loop_golden.py` — `make_controller(...)` harness
  (`:107`), `_neutralize_externals(monkeypatch)`, and `test_tick_probe_profile_update_clears_flag`
  (`:604-614`), the exact model for Task 3. Fake at `tests/fakes/probes.py`.

---

## Two hazards, answered

### Hazard 1 — a whole-map POST is a clobber, and there IS a second writer

The pellets slice established the house rule: *the client posts an intent, not a database.* This
plan **breaks that rule deliberately**, and here is why that is correct rather than lazy.

A probe map is not an accumulating log with independent rows. It is one interdependent graph:
`probe_info[].device` references `probe_devices[].device`, virtual devices' `config.probes_list`
references `probe_info[].label`, and the reposition algorithm's correctness depends on the
**order** of `probe_info`. There is no intent vocabulary that expresses "move this entry to just
after that one" without re-transmitting the ordering anyway — which is exactly why the shipped
reducer is a whole-map reducer.

The second writer is real: `update_probe_config` (`common/app.py:346-390`), reached from
`_settings_probe_config_save` (`blueprints/settings/routes.py:93-108`), edits individual
`probe_info` entries in place from the **Flask** settings page. Mitigations, in order of strength:

1. **The mode gate.** `POST /api/probe_map` refuses unless `control["mode"] == Mode.STOP`
   (409), mirroring `/api/wizard/finish` (`api_wizard/routes.py:406-407`). Probe hardware is
   reconfigured between cooks, not during one.
2. **`revalidate()` on success** re-reads live settings, so the tab never keeps editing a map the
   server has moved on from.
3. **The tab is loader-seeded, not socket-seeded** — every mount re-reads the live map.

What is **not** mitigated: two humans editing probes simultaneously in the old and new UIs, in
Stop mode, within the same page-lifetime. Last write wins. **Record it in the backlog when this
ships.** Do not paper over it with a `lastupdated.time` compare-and-swap; that race is
datastore-wide and pre-existing.

### Hazard 2 — reviving `update_probe_map()` runs code that has never run

`ProbesMain.update_probe_map` has zero callers today (F4). Turning it on means, for the first
time, tearing down and rebuilding live probe device objects inside a running controller. Two
specific risks:

- **`_setup_probe_devices` does not release the old devices.** It rebinds `self.probe_device_list`
  to a fresh list (`probes/main.py:35`) and lets the old instances fall out of scope. For a
  Bluetooth or USB-HID device holding an OS handle, that is a leak until GC — and possibly a
  failed re-open of the same hardware. **This is a disclosed limitation, not a fixed one.**
  Mitigation: the mode gate means this only ever runs in Stop, where no cook depends on the next
  read; and `_setup_probe_devices` already falls back to `probes.disabled` on any import/construct
  failure (`:44-53`), so a failed rebuild degrades to "no data from this device" rather than
  crashing the control loop.
- **It re-imports the module.** For a module whose Python dependency is not installed, the import
  raises and the device becomes `disabled` with an error appended. That is precisely what the
  dependency guard in Task 2 exists to prevent reaching.

The alternative — writing settings and calling `restart_scripts()` (`common/system.py:52`) —
was considered and **rejected**: it shells out to `sudo systemctl restart`, which drags the full
`os.system`/`subprocess` neutralization burden into every test that can reach this page, on a
repo that has really rebooted the developer's machine twice. An in-process rebuild has no such
blast radius. If the rebuild proves unreliable on real hardware, the fallback is a *manual*
"Restart required" affordance on the admin page — not an automatic shell-out from a settings tab.

---

## Design decisions (answered, with rationale)

1. **`/settings/probes`, not a top-level `/probes`.** Flask puts live probe configuration inside
   Settings (`_macro_probes.html`, reached from `settings/index.html:90`). A new top-level nav
   entry would advertise a surface Flask's navbar does not have, and `NavBar.tsx:16-24` is a
   deliberate 1:1 port of `base.html:63-82` plus exactly one documented addition (Pellets). One
   more `SETTINGS_TABS` pill is the smaller, more honest change.
2. **Reuse `DevicesCard`/`PortsCard` in place — no move, no barrel, no rename.**
   `components/ → components/` imports are legal (only `helpers/ → components/` is banned,
   `structure.test.ts:117-131`). Moving them would touch 8 shipped test files and collide with any
   in-flight wizard work for zero behavioural gain.
3. **No units control on this tab.** `ProbesStep` carries one because the wizard writes
   `globals.units` at install time. In the running app that is `/settings/units`, which already
   ships. Duplicating it here would give two controls for one setting.
4. **The dependency guard warns in the client and enforces on the server.** Client-side: the tab
   renders a warning and disables Save the moment the working map contains a device whose module
   `requires_install` and which was not in the loaded live map (computed at render, no effect).
   Server-side: `POST /api/probe_map` returns 422 with the offending module names. Filtering the
   Add dropdown was rejected — `DevicesCard.tsx:130` resolves `friendly_name` from the same
   `modules` map it builds the dropdown from, so a filtered map would degrade the *table* to raw
   module keys.
5. **`probes_units` is not posted.** The map is the payload; units are a separate setting.
6. **`control["notify_data"]` and `settings["recipe"]["probe_map"]` are not regenerated.**
   `run_wizard` does not regenerate them either (`wizard.py:227-231` regenerates only
   `history_page.probe_config`). Matching the installer exactly is the conservative choice;
   diverging is a separate, deliberate decision that belongs in its own change. **Disclosed in
   Out of scope.**

---

## File Structure

### Created

| Path | Single responsibility |
|---|---|
| `blueprints/api/probe_map_actions.py` | Pure, Flask-free probe-map application logic: `module_requires_install()`, `unsupported_new_modules()`, `apply_probe_map()`. Importable by tests without a request context. |
| `tests/web/test_api_probe_map.py` | Endpoint tests for `GET /api/probe_modules` and `POST /api/probe_map`, incl. all four rejection paths. |
| `tests/unit/probes/test_update_probe_map.py` | Unit test for `ProbesMain.update_probe_map()` returning its error list and rebuilding the device list. |
| `web-react/src/helpers/probes/probeMapTypes.ts` | `ProbeModuleCatalog`, `ApplyProbeMapResult` — the two shapes crossing the new HTTP seam. |
| `web-react/src/helpers/probes/probeMapTypes.test.ts` | Shape-pinning test for the seam (both ends). |
| `web-react/src/helpers/probes/probeMapApi.ts` | `getProbeModules()`, `applyProbeMap()`, `readLiveProbeMap(settings)`, `readLiveProfiles(settings)` — the only place the generated `Settings` type is narrowed to `probeTypes.ProbeMap`. |
| `web-react/src/helpers/probes/probeMapApi.test.ts` | Client + narrowing tests. |
| `web-react/src/helpers/probes/probeMapRoutes.ts` | `probeModulesLoader` for the `/settings/probes` child route. |
| `web-react/src/helpers/probes/probeMapRoutes.test.ts` | Loader test. |
| `web-react/src/components/wizard/probes/probes.css` | The probe-editing visual vocabulary, extracted from `wizard.css` so it travels with the components. |
| `web-react/src/components/settings/tabs/ProbesTab.tsx` | The tab: seeds working state from live settings, hosts the two shipped cards, dirty/Save/Discard, mode gate, dependency warning. |
| `web-react/src/components/settings/tabs/ProbesTab.test.tsx` | Tab tests. |
| `web-react/tests/e2e/probes.spec.ts` | Round-trip: edit a probe in the browser → assert it in `GET /api/settings`. |

### Modified

| Path | Change |
|---|---|
| `blueprints/api/routes.py` | `_api_get_probe_modules` + `_api_post_probe_map`; two dispatch-map entries. |
| `common/defaults.py` | `control["probe_map_update"] = False` in `default_control()`. |
| `controller/runtime/controller.py` | Handle `control["probe_map_update"]` before the `probe_profile_update` block. |
| `probes/main.py` | `_setup_probe_devices` returns its error list; `update_probe_map` returns it. |
| `tests/fakes/probes.py` | `FakeProbes.update_probe_map()` recording calls. |
| `tests/characterization/test_controller_loop_golden.py` | One test for the new flag. |
| `web-react/src/components/wizard/wizard.css` | Extraction: the moved block is deleted; `.pf-wizard`-only chrome stays. |
| `web-react/src/components/wizard/steps/ProbesStep.tsx` | Root gains `pf-probes-surface`. |
| `web-react/src/components/wizard/probes/DevicesCard.tsx` | `import "./probes.css";` only. |
| `web-react/src/components/wizard/probes/PortsCard.tsx` | `import "./probes.css";` only. |
| `web-react/src/components/wizard/wizardStyles.test.ts` | Guard reads `wizard.css` ∪ `probes/probes.css`. |
| `web-react/src/components/settings/SettingsShell.tsx` | One `SETTINGS_TABS` entry. |
| `web-react/src/components/App.tsx` | One child route with `probeModulesLoader`. |
| `docs/superpowers/react-migration-backlog.md` | Correct the entry; record what shipped. |

### Untouched (and must stay green)

`blueprints/probeconfig/**`, `tests/web/test_page_probeconfig.py`,
`web-react/src/helpers/wizard/probeReducer.ts` and its 3 test files,
`web-react/src/components/wizard/probes/{DeviceForm,PortForm,DeviceConfigField,BluetoothPicker,ThermoworksPicker}.tsx`
and their tests.

---

## Tasks

### Task 1: `GET /api/probe_modules` — the probes module catalog

**Files:** Create `blueprints/api/probe_map_actions.py`; Create `tests/web/test_api_probe_map.py`;
Modify `blueprints/api/routes.py`

**Interfaces:**

- Produces `module_requires_install(module_data: dict) -> bool` — True when any of
  `py_dependencies`, `apt_dependencies`, `command_list` is non-empty.
- Produces `HTTP GET /api/probe_modules -> 200`
  `{"data": {"modules": {<module_key>: <manifest entry>}, "requires_install": {<module_key>: bool}}, "result": "OK", "message": None}`
  (the `common.app.api_response` envelope, same as `GET /api/pellets`).
- Consumes `common.common.read_wizard()` → `wizard/wizard_manifest.json`.

**Steps:**

- [x] **Step 1: Neutralization sweep before writing a line of test.** This endpoint reads a JSON
      manifest and nothing else, but the rule is the rule:
      ```sh
      rg -n "os\.system|subprocess|sudo|reboot|shutdown|restart_scripts" \
        blueprints/api/routes.py common/common.py common/app.py
      ```
      Expected: **no hits in `read_wizard`'s or `api_response`'s call graph.** `blueprints/api/routes.py`
      itself has none. If this prints a hit inside a function Task 1 touches, stop and neutralize
      it at its import site before continuing.

- [x] **Step 2: Write the failing test.** Create `tests/web/test_api_probe_map.py` using the
      `ds` + `client` fixtures copied from `tests/web/test_api_wizard.py:10-14`:
      ```python
      import pytest

      from app import app as flask_app


      @pytest.fixture
      def client(ds):
          flask_app.config["TESTING"] = True
          with flask_app.test_client() as c:
              yield c


      def test_probe_modules_lists_every_manifest_module(ds, client):
          resp = client.get("/api/probe_modules")
          assert resp.status_code == 200
          body = resp.get_json()
          assert body["result"] == "OK"
          modules = body["data"]["modules"]
          # 18 probe modules ship in wizard/wizard_manifest.json (verified 2026-07-26).
          assert len(modules) == 18
          assert "ds18b20" in modules and "virtual_average" in modules
          # The card components read exactly these keys.
          ds18b20 = modules["ds18b20"]
          assert ds18b20["friendly_name"]
          assert ds18b20["filename"] == "ds18b20"
          assert ds18b20["device_specific"]["ports"] == ["DS0"]
          assert isinstance(ds18b20["device_specific"]["config"], list)


      def test_probe_modules_flags_which_modules_need_the_wizard(ds, client):
          body = client.get("/api/probe_modules").get_json()
          req = body["data"]["requires_install"]
          # The six dep-free modules, verified against the manifest 2026-07-26.
          for free in (
              "max31865",
              "prototype",
              "virtual_average",
              "virtual_highest",
              "virtual_lowest",
              "virtual_median",
          ):
              assert req[free] is False, free
          # ds18b20 has a py_dependency AND a command_list entry; bt_ibbq has all three.
          assert req["ds18b20"] is True
          assert req["bt_ibbq"] is True
          assert req["thermoworks_cloud"] is True
          assert set(req) == set(body["data"]["modules"])
      ```

- [x] **Step 3: Run, confirm they fail** with 404 and the body
      `{"Error": "Received GET request, without valid action"}` (`api/routes.py:394`):
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
        tests/web/test_api_probe_map.py -q
      ```

- [x] **Step 4: Create `blueprints/api/probe_map_actions.py`** with the module-level docstring
      and the first helper. Flask-free on purpose so Task 2's guards are unit-testable without a
      request context:
      ```python
      """Live probe-map application logic, kept Flask-free.

      The Flask wizard's probeconfig fragment API (blueprints/probeconfig/routes.py)
      edits the wizard STAGING blob (`wizard:install`), and its edits reach live
      settings only when the detached installer runs (wizard.py:227). This module
      is the other path: applying a probe map straight to
      settings["probe_settings"]["probe_map"] on a running PiFire, with the
      guards the installer would otherwise have provided.

      The one guard the installer provides that this module CANNOT is dependency
      INSTALLATION (wizard.py:319-430 runs apt/pip/command_list per selected
      module). So this module refuses instead: a probe module may be added here
      only when it is already present in the live map (hence already installed),
      or when its manifest declares no dependencies at all.
      """


      def module_requires_install(module_data):
          """True when adding this probe module would need the wizard's installer.

          Reads the same three manifest lists wizard.py:319-334 collects. Six of
          the 18 probe modules declare none of them (max31865, prototype and the
          four virtual_* reducers) and are therefore safe to add on a running
          system; the other twelve are not.
          """
          if not isinstance(module_data, dict):
              return True
          return bool(
              module_data.get("py_dependencies")
              or module_data.get("apt_dependencies")
              or module_data.get("command_list")
          )
      ```

- [x] **Step 5: Add the route.** In `blueprints/api/routes.py`, add the import beside the
      existing `from common.pellets_actions import PELLETS_DISPATCH` line (`:16`):
      ```python
      from blueprints.api.probe_map_actions import module_requires_install
      ```
      and `read_wizard` to the `common.common` import block at `:2`. Then add the handler
      immediately after `_api_get_controller_metadata` (`:122-123`):
      ```python
      def _api_get_probe_modules(settings, server_status):
          """The probes section of wizard/wizard_manifest.json, plus a per-module
          "would this need the wizard's installer?" flag.

          GET /api/wizard/state ships the same manifest slice (api_wizard/routes.py:133)
          but also computes draft resumption, board reseed maps and first_time_setup,
          and its probe_map is the DRAFT map -- the wrong source for an editor that
          edits LIVE settings. This route is the manifest and nothing else.
          """
          modules = read_wizard().get("modules", {}).get("probes", {})
          return jsonify(
              api_response(
                  result="OK",
                  data={
                      "modules": modules,
                      "requires_install": {key: module_requires_install(mod) for key, mod in modules.items()},
                  },
              )
          ), 200
      ```
      and one entry in `_API_GET_ACTIONS` (`:127-136`), after `"controller_metadata"`:
      ```python
          "probe_modules": _api_get_probe_modules,
      ```

- [x] **Step 6: Run, confirm pass**, then the whole web suite to prove the new dispatch entry
      disturbed nothing:
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web -q
      ```
      Expected: the 2 new tests pass; `tests/web/test_page_probeconfig.py` and
      `tests/web/test_api_wizard.py` unchanged (chromium-marked ones may SKIP in a worktree —
      re-run in the main checkout before merge).

- [x] **Step 7: Format and commit.**
      ```sh
      .venv/bin/ruff format blueprints/api/probe_map_actions.py blueprints/api/routes.py \
        tests/web/test_api_probe_map.py
      .venv/bin/ruff check blueprints/api/probe_map_actions.py blueprints/api/routes.py \
        tests/web/test_api_probe_map.py
      ```
      **Deliverable:** `curl -s localhost:5000/api/probe_modules | jq '.data.requires_install.ds18b20'`
      prints `true`. **Commit.**

---

### Task 2: `POST /api/probe_map` — apply a live probe map, with all four guards

**Files:** Modify `blueprints/api/probe_map_actions.py`, `blueprints/api/routes.py`,
`tests/web/test_api_probe_map.py`

**Interfaces:**

- Produces `unsupported_new_modules(new_map: dict, live_map: dict, manifest_modules: dict) -> list[str]`
  — sorted module keys present in `new_map` that are neither in `live_map` nor dependency-free.
- Produces `apply_probe_map(settings, probe_map) -> dict` — returns the mutated settings with
  `probe_settings.probe_map` replaced and `history_page.probe_config` regenerated. Pure; does not
  write.
- Produces `HTTP POST /api/probe_map`, body `{"probe_map": {"probe_devices": [...], "probe_info": [...]}}`:
  - `200 {"result": "success", "message": "Probe map applied.", "data": {"probe_map": {...}}}`
  - `409 {"result": "error", "message": "system_active"}` — control mode is not `Stop`
  - `422 {"result": "error", "message": "bus_conflict", "detail": "<I2CBusConfigError text>"}`
  - `422 {"result": "error", "message": "modules_require_install", "modules": ["ds18b20"]}`
  - `400 {"result": "error", "message": "bad_probe_map"}` — shape violation
- Consumes `read_control`, `read_settings`, `read_wizard`, `common.modes.Mode`,
  `common.i2c_bus.{configured_bus_kinds, validate_bus_kinds, I2CBusConfigError}`,
  `common.defaults.default_probe_config`, `common.app.save_settings_and_flag_update`.

**Steps:**

- [x] **Step 1: Neutralization sweep.** This route sets a control flag and writes settings. It
      must **not** shell out. Prove it:
      ```sh
      rg -n "os\.system|subprocess|sudo|reboot|shutdown|restart_scripts" \
        blueprints/api/probe_map_actions.py blueprints/api/routes.py common/app.py \
        common/defaults.py common/i2c_bus.py
      ```
      Expected: **no hits in any function this task calls.** If `common/i2c_bus.py`'s discovery
      helpers show hits, note that this route calls only `configured_bus_kinds` and
      `validate_bus_kinds`, neither of which touches hardware — `configured_bus_kinds`
      (`common/i2c_bus.py:223-239`) only reads dict keys. Confirm that by reading it before
      proceeding, do not assume.

- [x] **Step 2: Write the failing tests.** Append to `tests/web/test_api_probe_map.py`:
      ```python
      # CORRECTED 2026-07-26 against live code: there is NO `write_control_store`
      # in common/datastore_accessors.py -- this plan's first draft invented it.
      # Control is written with write_control(control, WriteKind.OVERWRITE).
      from common.common import WriteKind
      from common.datastore_accessors import (
          execute_control_writes,
          read_control,
          read_settings,
          write_control,
          write_settings_store,
      )
      from common.modes import Mode

      PROFILE_ID = "TWPS00"


      def _profile():
          return read_settings()["probe_settings"]["probe_profiles"][PROFILE_ID].copy()


      def _map(devices=None, probes=None):
          return {"probe_devices": devices or [], "probe_info": probes or []}


      def _virtual_device(name="VirtDev", probes_list=()):
          # virtual_average declares no py/apt/command dependencies, so it is one of
          # the six modules addable without the wizard.
          return {
              "config": {"probes_list": list(probes_list)},
              "device": name,
              "module": "virtual_average",
              "module_filename": "virtual_average",
              "ports": ["VIRT0"],
          }


      def _probe(name, device, port, probe_type="Primary"):
          return {
              "name": name,
              "label": name,
              "device": device,
              "port": port,
              "type": probe_type,
              "enabled": True,
              "profile": _profile(),
          }


      def _set_mode(mode):
          control = read_control()
          control["mode"] = mode
          write_control(control, WriteKind.OVERWRITE, origin="test")


      def _stop_mode():
          _set_mode(Mode.STOP)


      def test_apply_writes_live_settings_and_flags_the_controller(ds, client):
          _stop_mode()
          new_map = _map([_virtual_device()], [_probe("Grill", "VirtDev", "VIRT0")])

          resp = client.post("/api/probe_map", json={"probe_map": new_map})

          assert resp.status_code == 200
          assert resp.get_json()["result"] == "success"
          stored = read_settings()["probe_settings"]["probe_map"]
          assert [d["device"] for d in stored["probe_devices"]] == ["VirtDev"]
          assert [p["label"] for p in stored["probe_info"]] == ["Grill"]
          # The flag the controller acts on (Task 3). CORRECTED 2026-07-26:
          # save_settings_and_flag_update QUEUES a named-flag DELTA
          # (common/app.py:419) instead of overwriting control:general, so the
          # queue must be drained before read_control() can see it. In production
          # that drain is the control loop's own execute_control_writes().
          execute_control_writes()
          assert read_control()["probe_map_update"] is True


      def test_apply_regenerates_the_history_probe_config(ds, client):
          _stop_mode()
          client.post(
              "/api/probe_map",
              json={"probe_map": _map([_virtual_device()], [_probe("Grill", "VirtDev", "VIRT0")])},
          )
          # wizard.py:230 does exactly this after writing the map; a probe map written
          # without it leaves the history chart configured for probes that are gone.
          assert set(read_settings()["history_page"]["probe_config"]) == {"Grill"}


      def test_apply_refuses_while_the_grill_is_running(ds, client):
          _set_mode(Mode.SMOKE)
          before = read_settings()["probe_settings"]["probe_map"]

          resp = client.post("/api/probe_map", json={"probe_map": _map()})

          assert resp.status_code == 409
          assert resp.get_json()["message"] == "system_active"
          assert read_settings()["probe_settings"]["probe_map"] == before


      def test_apply_refuses_a_module_that_needs_the_installer(ds, client):
          _stop_mode()
          before = read_settings()["probe_settings"]["probe_map"]
          bt = {
              "config": {},
              "device": "MeaterProbe",
              "module": "bt_meater",
              "module_filename": "bt_meater",
              "ports": ["BT_Tip", "BT_Ambient"],
          }

          resp = client.post("/api/probe_map", json={"probe_map": _map([bt])})

          assert resp.status_code == 422
          body = resp.get_json()
          assert body["message"] == "modules_require_install"
          assert body["modules"] == ["bt_meater"]
          assert read_settings()["probe_settings"]["probe_map"] == before


      def test_apply_allows_a_module_that_is_already_installed(ds, client):
          """A module already in the LIVE map has necessarily been installed, so it
          may be re-sent even though its manifest declares dependencies."""
          _stop_mode()
          settings = read_settings()
          ds18b20 = {
              "config": {"transient": "False"},
              "device": "TempSensor",
              "module": "ds18b20",
              "module_filename": "ds18b20",
              "ports": ["DS0"],
          }
          settings["probe_settings"]["probe_map"] = _map([ds18b20])
          write_settings_store(settings)

          resp = client.post(
              "/api/probe_map",
              json={"probe_map": _map([ds18b20], [_probe("Grill", "TempSensor", "DS0")])},
          )

          assert resp.status_code == 200
          assert [p["label"] for p in read_settings()["probe_settings"]["probe_map"]["probe_info"]] == ["Grill"]


      def test_apply_rejects_a_bus_kind_conflict(ds, client):
          """FULL cross-subsystem check here, unlike the wizard's in-progress
          settings=None check (api_wizard/routes.py:475-487): this writes LIVE
          config, so the live fan/distance kinds are exactly what it must consider."""
          _stop_mode()
          settings = read_settings()
          settings["platform"].setdefault("devices", {}).setdefault("distance", {})["i2c_bus_kind"] = "basic"
          write_settings_store(settings)
          adc = {
              "config": {"i2c_bus_kind": "ft232h", "i2c_bus_num": "FT232H"},
              "device": "Adc",
              "module": "prototype",
              "module_filename": "prototype",
              "ports": ["ADC0", "ADC1", "ADC2", "ADC3"],
          }

          resp = client.post("/api/probe_map", json={"probe_map": _map([adc])})

          assert resp.status_code == 422
          assert resp.get_json()["message"] == "bus_conflict"
          assert "basic" in resp.get_json()["detail"]


      def test_apply_rejects_a_malformed_map(ds, client):
          _stop_mode()
          # CORRECTED 2026-07-26: `{}` is NOT in this loop. api_page's POST branch
          # does `if not request.json: abort(400)` before any handler runs, so an
          # empty JSON object gets a bare Werkzeug 400 with an HTML body -- there
          # is no "message" key to assert. Pinned separately below.
          for bad in ({"probe_map": None}, {"probe_map": {"probe_devices": {}}},
                      {"probe_map": {"probe_devices": [], "probe_info": "nope"}}):
              resp = client.post("/api/probe_map", json=bad)
              assert resp.status_code == 400, bad
              assert resp.get_json()["message"] == "bad_probe_map"


      def test_apply_rejects_an_empty_body_before_any_handler(ds, client):
          resp = client.post("/api/probe_map", json={})
          assert resp.status_code == 400
          assert resp.get_json() is None
      ```
      **Before running:** confirm the two write helpers exist and are named as used —
      `rg -n "def write_control_store|def write_settings_store" common/datastore_accessors.py`.
      If either differs, use the real name; do not invent one.

- [x] **Step 3: Run, confirm they fail** with 404
      (`{"Error": "Received POST request no valid action."}`, `api/routes.py:404`):
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
        tests/web/test_api_probe_map.py -q
      ```

- [x] **Step 4: Implement the two pure helpers** in `blueprints/api/probe_map_actions.py`:
      ```python
      from common.defaults import default_probe_config


      def valid_probe_map(probe_map):
          """The outer shape only, matching common/settings_schema.py:229-234's
          ProbeMap (probe_devices/probe_info are list[dict]; their contents are
          driver-specific and stay loose)."""
          if not isinstance(probe_map, dict):
              return False
          for key in ("probe_devices", "probe_info"):
              value = probe_map.get(key)
              if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                  return False
          return True


      def unsupported_new_modules(new_map, live_map, manifest_modules):
          """Modules the caller is ADDING that this path cannot install.

          A module already present in `live_map` was installed by a previous wizard
          run, so it is always allowed. Anything else must declare no dependencies
          (module_requires_install() == False). An unknown module -- not in the
          manifest at all -- is refused too: module_requires_install() returns True
          for a non-dict, so a stale/renamed module cannot slip through.
          """
          installed = {d.get("module") for d in live_map.get("probe_devices", []) if d.get("module")}
          offenders = set()
          for device in new_map.get("probe_devices", []):
              module = device.get("module")
              if not module or module in installed:
                  continue
              if module_requires_install(manifest_modules.get(module)):
                  offenders.add(module)
          return sorted(offenders)


      def apply_probe_map(settings, probe_map):
          """Replace the live probe map and regenerate everything derived from it.

          Mirrors wizard.py:227-231, which is the ONLY other writer of this key.
          default_probe_config() preserves an existing per-label entry and
          colour-assigns new ones (common/defaults.py:319-348), so an edit that
          leaves a probe alone leaves its chart colour alone.

          Deliberately does NOT regenerate control["notify_data"] or
          settings["recipe"]["probe_map"] -- the installer does not either, and
          diverging from it is a separate decision.
          """
          settings["probe_settings"]["probe_map"] = probe_map
          settings["history_page"]["probe_config"] = default_probe_config(settings)
          return settings
      ```

- [x] **Step 5: Add the route.** In `blueprints/api/routes.py`, extend the Task 1 import to
      `from blueprints.api.probe_map_actions import apply_probe_map, module_requires_install, unsupported_new_modules, valid_probe_map`,
      add `from common.modes import Mode` and
      `from common.i2c_bus import I2CBusConfigError, configured_bus_kinds, validate_bus_kinds`.
      Add the handler after `_api_post_pellets` (`:337-352`):
      ```python
      def _api_post_probe_map(settings, request_json):
          """Apply a whole probe map to LIVE settings.

          Whole-map, not intent-based, and that is deliberate (unlike
          _api_post_pellets above): a probe map is one interdependent graph --
          probe_info[].device references probe_devices[].device, virtual devices'
          config.probes_list references probe_info[].label, and the reposition
          invariant depends on probe_info ORDER. No per-item intent vocabulary
          expresses "this entry sorts after that one" without re-sending the order.

          Four guards, in the order a rejection is cheapest:
            1. shape          -> 400, nothing read
            2. control mode   -> 409, mirrors /api/wizard/finish (api_wizard:406)
            3. new modules    -> 422, the installer is the only thing that can
                                 install dependencies and it does not run here
            4. bus kinds      -> 422, FULL cross-subsystem (settings passed, not
                                 None) because this is live config, not a draft
          Only after all four does anything get written.
          """
          probe_map = request_json.get("probe_map")
          if not valid_probe_map(probe_map):
              return jsonify({"result": "error", "message": "bad_probe_map"}), 400

          control = read_control()
          if control.get("mode") != Mode.STOP:
              return jsonify({"result": "error", "message": "system_active"}), 409

          manifest_modules = read_wizard().get("modules", {}).get("probes", {})
          live_map = settings["probe_settings"]["probe_map"]
          offenders = unsupported_new_modules(probe_map, live_map, manifest_modules)
          if offenders:
              return jsonify(
                  {"result": "error", "message": "modules_require_install", "modules": offenders}
              ), 422

          try:
              validate_bus_kinds(configured_bus_kinds(settings, probe_map))
          except I2CBusConfigError as exc:
              return jsonify({"result": "error", "message": "bus_conflict", "detail": str(exc)}), 422

          settings = apply_probe_map(settings, probe_map)
          # settings_update makes the loop re-read settings; probe_map_update is what
          # makes it REBUILD its probe devices (controller.py, Task 3).
          # probe_profile_update is NOT enough on its own -- it only refills
          # per-port profiles on already-constructed devices (probes/base.py:393).
          save_settings_and_flag_update(settings, control, "settings_update", "probe_map_update", origin="api")
          return jsonify(
              {"result": "success", "message": "Probe map applied.", "data": {"probe_map": probe_map}}
          ), 200
      ```
      and one entry in `_API_POST_ACTIONS` (`:355-362`), after `"pellets"`:
      ```python
          "probe_map": _api_post_probe_map,
      ```

- [x] **Step 6: Run, confirm pass.** All 7 new tests plus Task 1's 2:
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
        tests/web/test_api_probe_map.py -q
      ```
      Then the full Python suite — `save_settings_and_flag_update` writes a **named-flag delta**
      (`common/app.py:416-419`), so nothing else's flags can be clobbered, but prove it:
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
      ```

- [x] **Step 7: Format and commit.**
      ```sh
      .venv/bin/ruff format blueprints/api/probe_map_actions.py blueprints/api/routes.py \
        tests/web/test_api_probe_map.py
      .venv/bin/ruff check blueprints/api/probe_map_actions.py blueprints/api/routes.py \
        tests/web/test_api_probe_map.py
      ```
      **Deliverable:** a `POST /api/probe_map` in Stop mode changes
      `read_settings()["probe_settings"]["probe_map"]` and sets `control["probe_map_update"]`;
      all four rejection paths leave the store byte-identical. **Commit.**

---

### Task 3: Make the running controller rebuild its probe devices on `probe_map_update`

**Files:** Modify `probes/main.py`, `controller/runtime/controller.py`, `common/defaults.py`,
`tests/fakes/probes.py`, `tests/characterization/test_controller_loop_golden.py`; Create
`tests/unit/probes/test_update_probe_map.py`

**Interfaces:**

- Consumes `control["probe_map_update"]: bool` (written by Task 2).
- Produces `ProbesMain._setup_probe_devices(probe_devices) -> list[str]` (was `-> None`) and
  `ProbesMain.update_probe_map(probe_map) -> list[str]` — the errors raised while rebuilding.
- Produces `FakeProbes.update_probe_map(probe_map) -> list[str]`, recording into
  `FakeProbes.update_probe_map_calls: list[dict]`.

**Steps:**

- [x] **Step 1: Neutralization sweep — this one matters.** The controller loop is the process
      that drives real relays, and `tests/characterization/test_controller_loop_golden.py` already
      carries `_neutralize_externals(monkeypatch)` for that reason. Before touching anything:
      ```sh
      rg -n "os\.system|subprocess|sudo|reboot|shutdown|restart_scripts" \
        controller/runtime/controller.py controller/runtime/devices.py probes/main.py probes/base.py
      rg -n "def _neutralize_externals" -A 25 tests/characterization/test_controller_loop_golden.py
      ```
      Read what `_neutralize_externals` already covers and **call it in the new test**. Do not
      write a controller test without it. If a hit appears in `probes/base.py`'s import chain,
      neutralize it *at the import site inside the module under test* — moving the call
      elsewhere silently disarms a `patch.object`, which this repo has hit three times.

- [x] **Step 2: Write the failing unit test** for the revived method. Create
      `tests/unit/probes/test_update_probe_map.py`:
      ```python
      """ProbesMain.update_probe_map() had ZERO callers before 2026-07-26 (verified
      with `grep -rn update_probe_map`), and its `error = self._setup_probe_devices(...)`
      was dead: _setup_probe_devices returned None unconditionally. Both are fixed
      here, because POST /api/probe_map is now its caller."""

      from probes.main import ProbesMain

      VIRT = {
          "config": {"probes_list": []},
          "device": "VirtDev",
          "module": "virtual_average",
          "module_filename": "virtual_average",
          "ports": ["VIRT0"],
      }
      BOGUS = {
          "config": {},
          "device": "Ghost",
          "module": "no_such_module",
          "module_filename": "no_such_module",
          "ports": ["X0"],
      }


      def _map(devices, probes=()):
          return {"probe_devices": list(devices), "probe_info": list(probes)}


      def test_update_probe_map_rebuilds_the_device_list():
          pm = ProbesMain(_map([]), "F")
          assert pm.probe_device_list == []

          errors = pm.update_probe_map(_map([VIRT]))

          assert errors == []
          assert len(pm.probe_device_list) == 1
          assert pm.probe_devices == [VIRT]


      def test_update_probe_map_shrinks_as_well_as_grows():
          pm = ProbesMain(_map([VIRT]), "F")
          assert len(pm.probe_device_list) == 1

          pm.update_probe_map(_map([]))

          assert pm.probe_device_list == []


      def test_update_probe_map_reports_a_module_that_will_not_import():
          """An unimportable module degrades to probes.disabled rather than raising
          -- _setup_probe_devices:44-53 -- but the caller must be able to SEE it."""
          pm = ProbesMain(_map([]), "F")

          errors = pm.update_probe_map(_map([BOGUS]))

          assert len(errors) == 1
          assert "no_such_module" in errors[0]
          assert len(pm.probe_device_list) == 1  # the disabled stand-in
      ```

- [x] **Step 3: Run, confirm failure** (`errors` is `None`, not a list):
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
        tests/unit/probes/test_update_probe_map.py -q
      ```
      If `tests/unit/probes/` does not exist, create it; check whether sibling unit dirs carry an
      `__init__.py` (`ls tests/unit/*/__init__.py`) and match them.

- [x] **Step 4: Fix the two return values** in `probes/main.py`. In `_setup_probe_devices`
      (`:33-63`), collect per-device errors and return them. The existing code already appends
      each `error_event` to `self.errors` and sets `error_event` in the `except`; add a local
      list, append the same string, and `return` it at the end:
      ```python
          def _setup_probe_devices(self, probe_devices):
              """Construct one ReadProbes instance per configured device.

              Returns the errors raised THIS call (also appended to self.errors,
              which accumulates across calls). The return value matters now that
              update_probe_map() is live: a rebuild triggered from the web tier has
              no other way to report that a module failed to import.
              """
              errors = []
              self.probe_device_list = []
              ...
                  except:
                      ...
                      self.errors.append(error_event)
                      errors.append(error_event)
                      self.logger.error(error_event)
                  ...
              return errors
      ```
      and in `update_probe_map` (`:84-88`) keep the body, returning what it already assigns:
      ```python
          def update_probe_map(self, probe_map):
              """Rebuild every probe device from a new map, in place.

              Called by the control loop when control["probe_map_update"] is set
              (controller.py) -- i.e. after POST /api/probe_map wrote a new
              settings["probe_settings"]["probe_map"].

              NOT equivalent to update_probe_profiles(): that only refills per-port
              profiles on already-constructed devices (probes/base.py:393-401) and
              cannot see an added, removed or renamed probe.

              KNOWN LIMITATION: the previous device objects are dropped, not closed.
              A Bluetooth/USB-HID device holding an OS handle releases it at GC, not
              here. Callers gate this on control mode == Stop for that reason.
              """
              self.probe_devices = probe_map["probe_devices"]
              self.probe_info = probe_map["probe_info"]
              return self._setup_probe_devices(self.probe_devices)
      ```
      **Check the other caller before changing the signature:** `__init__` (`:31`) calls
      `_setup_probe_devices` and ignores the result — returning a list is backward-compatible
      there. Confirm with `rg -n "_setup_probe_devices" .` that those are the only two call sites.

- [x] **Step 5: Run, confirm the unit tests pass.**

- [x] **Step 6: Seed the new control key.** In `common/defaults.py`'s `default_control()`, beside
      `control["probe_profile_update"] = False` (`:474`):
      ```python
          control["probe_map_update"] = False  # Request a full probe-device rebuild (POST /api/probe_map)
      ```

- [x] **Step 7: Teach the fake.** In `tests/fakes/probes.py`, beside `update_probe_profiles`
      (`:32-33`):
      ```python
          def __init__(self):
              ...
              self.update_probe_map_calls = []

          def update_probe_map(self, probe_map):
              self.update_probe_map_calls.append(probe_map)
              return []
      ```

- [x] **Step 8: Write the failing controller test.** In
      `tests/characterization/test_controller_loop_golden.py`, next to
      `test_tick_probe_profile_update_clears_flag` (`:604-614`). Note `make_controller` builds its
      own `FakeProbes` at `:107`, so reach it through `ctx.devices.probe_complex`:
      ```python
      def test_tick_probe_map_update_rebuilds_devices_and_clears_flag(monkeypatch):
          """POST /api/probe_map sets this flag. probe_profile_update is NOT enough:
          it only refills per-port profiles on already-constructed devices
          (probes/base.py:393-401) and cannot see an added/removed/renamed probe."""
          _neutralize_externals(monkeypatch)
          settings = base_settings()
          control_data = base_control(mode="Stop")
          control_data["updated"] = False
          control_data["probe_map_update"] = True
          c, ctx, store, grill, dist, notifier = make_controller(settings, control_data, base_pellet_db())
          _spy_dispatch(c)
          c.setup()
          c.tick()
          assert ctx.devices.probe_complex.update_probe_map_calls == [
              settings["probe_settings"]["probe_map"]
          ]
          assert store.read_control()["probe_map_update"] is False


      def test_tick_tolerates_a_control_blob_without_the_new_flag(monkeypatch):
          """An install upgraded in place has a control blob written before this key
          existed. probe_profile_update indexes control[...] directly and would
          KeyError on such a blob; the new handler must use .get()."""
          _neutralize_externals(monkeypatch)
          control_data = base_control(mode="Stop")
          control_data["updated"] = False
          control_data.pop("probe_map_update", None)
          c, ctx, store, grill, dist, notifier = make_controller(
              base_settings(), control_data, base_pellet_db()
          )
          _spy_dispatch(c)
          c.setup()
          c.tick()  # must not raise
          assert ctx.devices.probe_complex.update_probe_map_calls == []
      ```

- [x] **Step 9: Run, confirm both fail** (`AttributeError` / no rebuild):
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
        tests/characterization/test_controller_loop_golden.py -q -k probe_map
      ```

- [x] **Step 10: Implement in `controller/runtime/controller.py`.** Insert **immediately before**
      the `probe_profile_update` block at `:360`, so a full rebuild happens first and a
      profiles-only refresh in the same tick lands on the new devices:
      ```python
              # Rebuild every probe device if the probe MAP changed (POST /api/probe_map).
              # Distinct from probe_profile_update below: that only refills per-port
              # profiles on already-constructed devices (probes/base.py:393-401) and
              # cannot see an added, removed or renamed probe.
              #
              # .get(), not [...]: an install upgraded in place has a control blob
              # written before this key existed in default_control(). The
              # probe_profile_update line below indexes directly and would KeyError
              # on such a blob; do not copy that here.
              if self.control.get("probe_map_update"):
                  self.settings = settings = store.read_settings()
                  self.control["probe_map_update"] = False
                  store.write_control(self.control, WriteKind.OVERWRITE, origin="control")
                  errors = self.probe_complex.update_probe_map(settings["probe_settings"]["probe_map"])
                  store.write_generic_key("probe_device_info", self.probe_complex.get_device_info())
                  for error in errors or []:
                      self.eventLogger.error(error)
                  self.eventLogger.info("Probe map reloaded in control script.")
      ```
      **Verify `store.write_generic_key` is reachable on this object** before writing that line —
      `rg -n "write_generic_key" controller/runtime/controller.py` shows it at `:283`; match that
      call style exactly.

- [x] **Step 11: Run, confirm pass**, then the whole suite:
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
      ```
      Expected: green, including every pre-existing golden test. A red golden test here means the
      new block changed tick ordering — move it, do not re-baseline the golden.

- [x] **Step 12: Format and commit.**
      ```sh
      .venv/bin/ruff format probes/main.py controller/runtime/controller.py common/defaults.py \
        tests/fakes/probes.py tests/characterization/test_controller_loop_golden.py \
        tests/unit/probes/test_update_probe_map.py
      .venv/bin/ruff check probes/main.py controller/runtime/controller.py common/defaults.py
      ```
      **Deliverable:** with `control.py` running, a `POST /api/probe_map` is followed within one
      tick by `probe_map_update == False` and a `probe_device_info` generic key matching the new
      device set. **Commit.**

---

### Task 4: TypeScript client for the two new endpoints, and the one narrowing function

**Files:** Create `web-react/src/helpers/probes/probeMapTypes.ts`, `probeMapTypes.test.ts`,
`probeMapApi.ts`, `probeMapApi.test.ts`, `probeMapRoutes.ts`, `probeMapRoutes.test.ts`

**Interfaces:**

- Produces
  ```ts
  export interface ProbeModuleCatalog {
    modules: Record<string, ProbeModuleData>;
    requires_install: Record<string, boolean>;
  }
  export type ApplyProbeMapResult =
    | { ok: true }
    | { ok: false; message: string };
  export function getProbeModules(baseUrl: string): Promise<ProbeModuleCatalog>;
  export function applyProbeMap(baseUrl: string, probeMap: ProbeMap): Promise<ApplyProbeMapResult>;
  export function readLiveProbeMap(settings: Settings): ProbeMap;
  export function readLiveProfiles(settings: Settings): ProbeProfile[];
  export function probeModulesLoader(): Promise<ProbeModuleCatalog>;
  ```
- Consumes `helpers/wizard/probeTypes` (`ProbeMap`, `ProbeModuleData`, `ProbeProfile`) and
  `helpers/settings/settingsApi` (`Settings`).
- **`helpers/` must not import `components/`** (`structure.test.ts:117-131`) — none of these do.

**Steps:**

- [x] **Step 1: Write the failing seam test** `src/helpers/probes/probeMapTypes.test.ts` (`.ts` →
      node project). This is the shape pin for a cross-process handoff, so it asserts **both
      ends**: the literal key names Python emits, and the fields the React cards consume.
      ```ts
      import { describe, expect, it } from "@rstest/core";
      import type { ProbeModuleCatalog } from "./probeMapTypes";

      // Pinned against blueprints/api/routes.py::_api_get_probe_modules, which
      // returns api_response(data={"modules": ..., "requires_install": ...}) --
      // the {data,result,message} envelope from common/app.py:422-431.
      const WIRE = {
        data: {
          modules: {
            ds18b20: {
              friendly_name: "DS18B20",
              filename: "ds18b20",
              image: "ds18b20.png",
              device_specific: { ports: ["DS0"], type: "1wire", config: [] },
            },
          },
          requires_install: { ds18b20: true },
        },
        result: "OK",
        message: null,
      };

      describe("probe module catalog seam", () => {
        it("carries the two maps the tab needs, keyed alike", () => {
          const catalog: ProbeModuleCatalog = WIRE.data as ProbeModuleCatalog;
          expect(Object.keys(catalog.modules)).toEqual(Object.keys(catalog.requires_install));
          expect(catalog.requires_install.ds18b20).toBe(true);
        });

        it("exposes exactly what DevicesCard reads off a module", () => {
          const mod = (WIRE.data as ProbeModuleCatalog).modules.ds18b20;
          // DevicesCard.tsx:120-130 and DeviceForm.tsx:23-34.
          expect(mod.friendly_name).toBe("DS18B20");
          expect(mod.image).toBe("ds18b20.png");
          expect(mod.device_specific.ports).toEqual(["DS0"]);
          expect(Array.isArray(mod.device_specific.config)).toBe(true);
        });
      });
      ```

- [x] **Step 2: Run, confirm fail** (module does not exist):
      `cd web-react && bun run test src/helpers/probes/probeMapTypes.test.ts`

- [x] **Step 3: Write `probeMapTypes.ts`.**
      ```ts
      import type { ProbeModuleData } from "../wizard/probeTypes";

      /** GET /api/probe_modules -> body.data (blueprints/api/routes.py). Both maps
       *  are keyed by module name; `requires_install` is true when the module
       *  declares py/apt/command dependencies, i.e. adding it needs the wizard's
       *  installer (wizard.py:319-430) and POST /api/probe_map will refuse it. */
      export interface ProbeModuleCatalog {
        modules: Record<string, ProbeModuleData>;
        requires_install: Record<string, boolean>;
      }

      /** POST /api/probe_map. `message` is already user-facing: the route's four
       *  rejection codes are translated in probeMapApi, not in the component. */
      export type ApplyProbeMapResult = { ok: true } | { ok: false; message: string };
      ```

- [x] **Step 4: Write the failing client tests** `probeMapApi.test.ts` (`.ts` → node). Use
      `rs.fn`/`rs.stubGlobal` — **`vi` does not exist**:
      ```ts
      import { beforeEach, describe, expect, it, rs } from "@rstest/core";
      import { applyProbeMap, getProbeModules, readLiveProbeMap, readLiveProfiles } from "./probeMapApi";

      let fetchMock: ReturnType<typeof rs.fn>;
      function reply(status: number, body: unknown) {
        return { ok: status < 400, status, json: async () => body };
      }
      beforeEach(() => {
        fetchMock = rs.fn();
        rs.stubGlobal("fetch", fetchMock);
      });

      describe("getProbeModules", () => {
        it("GETs /api/probe_modules and unwraps the envelope", async () => {
          fetchMock.mockResolvedValue(
            reply(200, { data: { modules: { prototype: {} }, requires_install: { prototype: false } }, result: "OK" }),
          );
          const catalog = await getProbeModules("");
          expect(fetchMock.mock.calls[0][0]).toBe("/api/probe_modules");
          expect(catalog.requires_install.prototype).toBe(false);
        });

        it("throws on a non-ok response so the route's errorElement renders", async () => {
          fetchMock.mockResolvedValue(reply(500, {}));
          await expect(getProbeModules("")).rejects.toThrow("GET /api/probe_modules failed: HTTP 500");
        });
      });

      describe("applyProbeMap", () => {
        const MAP = { probe_devices: [], probe_info: [] };

        it("POSTs the map under a probe_map key", async () => {
          fetchMock.mockResolvedValue(reply(200, { result: "success" }));
          expect(await applyProbeMap("", MAP)).toEqual({ ok: true });
          const [url, init] = fetchMock.mock.calls[0];
          expect(url).toBe("/api/probe_map");
          expect(init.method).toBe("POST");
          expect(JSON.parse(init.body as string)).toEqual({ probe_map: MAP });
        });

        it("translates system_active into a sentence about the grill", async () => {
          fetchMock.mockResolvedValue(reply(409, { result: "error", message: "system_active" }));
          const r = await applyProbeMap("", MAP);
          expect(r).toEqual({
            ok: false,
            message: "Stop the grill before changing probe configuration.",
          });
        });

        it("names the offending modules on modules_require_install", async () => {
          fetchMock.mockResolvedValue(
            reply(422, { result: "error", message: "modules_require_install", modules: ["ds18b20", "bt_ibbq"] }),
          );
          const r = await applyProbeMap("", MAP);
          expect(r.ok).toBe(false);
          if (!r.ok) {
            expect(r.message).toContain("ds18b20");
            expect(r.message).toContain("bt_ibbq");
            expect(r.message).toContain("wizard");
          }
        });

        it("surfaces the bus-conflict detail verbatim", async () => {
          fetchMock.mockResolvedValue(
            reply(422, { result: "error", message: "bus_conflict", detail: "'basic' I2C can't share a process" }),
          );
          const r = await applyProbeMap("", MAP);
          expect(r).toEqual({ ok: false, message: "'basic' I2C can't share a process" });
        });

        it("does not throw on a network failure", async () => {
          fetchMock.mockRejectedValue(new Error("boom"));
          const r = await applyProbeMap("", MAP);
          expect(r.ok).toBe(false);
        });
      });

      describe("readLiveProbeMap / readLiveProfiles", () => {
        it("narrows the generated settings type and defaults both arrays", () => {
          expect(readLiveProbeMap({} as never)).toEqual({ probe_devices: [], probe_info: [] });
        });

        it("flattens probe_profiles from an id-keyed object to a list", () => {
          const settings = {
            probe_settings: {
              probe_profiles: { TWPS00: { id: "TWPS00", name: "TW", A: 1, B: 2, C: 3 } },
            },
          };
          expect(readLiveProfiles(settings as never)).toEqual([
            { id: "TWPS00", name: "TW", A: 1, B: 2, C: 3 },
          ]);
        });
      });
      ```

- [x] **Step 5: Run, confirm fail.** `bun run test src/helpers/probes/probeMapApi.test.ts`

- [x] **Step 6: Write `probeMapApi.ts`.** This is the **only** module allowed to import both
      `ProbeMap` names — alias the generated one out of the way (`settingsTypes.gen.ts:510` also
      exports `ProbeMap`, with every member optional):
      ```ts
      import type { Settings } from "../settings/settingsApi";
      import type { ProbeMap, ProbeProfile } from "../wizard/probeTypes";
      import type { ApplyProbeMapResult, ProbeModuleCatalog } from "./probeMapTypes";

      const EMPTY_MAP: ProbeMap = { probe_devices: [], probe_info: [] };

      export async function getProbeModules(baseUrl: string): Promise<ProbeModuleCatalog> {
        const res = await fetch(`${baseUrl}/api/probe_modules`);
        if (!res.ok) throw new Error(`GET /api/probe_modules failed: HTTP ${res.status}`);
        const body = (await res.json()) as { data?: ProbeModuleCatalog };
        return body.data ?? { modules: {}, requires_install: {} };
      }

      // The route's four rejection codes, turned into sentences here rather than in
      // the component: the codes are a backend contract and belong beside the client
      // that speaks it. `bus_conflict` carries its own already-readable detail
      // (common/i2c_bus.py raises full sentences), so it is passed through.
      function explain(status: number, body: { message?: string; detail?: string; modules?: string[] }): string {
        switch (body.message) {
          case "system_active":
            return "Stop the grill before changing probe configuration.";
          case "modules_require_install":
            return `These probe modules need the setup wizard to install their dependencies first: ${(
              body.modules ?? []
            ).join(", ")}.`;
          case "bus_conflict":
            return body.detail ?? "This probe configuration conflicts on the I2C bus.";
          case "bad_probe_map":
            return "The probe configuration is malformed and was not saved.";
          default:
            return `Probe configuration was not saved (HTTP ${status}).`;
        }
      }

      export async function applyProbeMap(
        baseUrl: string,
        probeMap: ProbeMap,
      ): Promise<ApplyProbeMapResult> {
        try {
          const res = await fetch(`${baseUrl}/api/probe_map`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ probe_map: probeMap }),
          });
          const body = (await res.json().catch(() => ({}))) as {
            message?: string;
            detail?: string;
            modules?: string[];
          };
          if (!res.ok) return { ok: false, message: explain(res.status, body) };
          return { ok: true };
        } catch {
          // The grill going unreachable mid-save must not throw past the tab.
          return { ok: false, message: "Could not reach PiFire. The probe configuration was not saved." };
        }
      }

      /** The generated Settings type models probe_map as all-optional
       *  (settingsTypes.gen.ts:510) because common/settings_schema.py:229-234 keeps
       *  the device/probe dicts loose. The reducer and both cards need the required
       *  shape from helpers/wizard/probeTypes. This is the ONE place that crossing
       *  happens, so no component ever has to hold both ProbeMap types at once. */
      export function readLiveProbeMap(settings: Settings): ProbeMap {
        const raw = settings?.probe_settings?.probe_map;
        if (!raw) return EMPTY_MAP;
        // CORRECTED 2026-07-26: a single `as` does NOT compile. The generated
        // members are bare index signatures ({[k: string]: unknown}[]), which TS
        // rejects as insufficiently overlapping (TS2352). `as unknown as` is
        // required, and is the same idiom NotificationsTab.tsx:22 already uses
        // for this exact generated-to-strict crossing. Same for readLiveProfiles.
        return {
          probe_devices: (raw.probe_devices ?? []) as unknown as ProbeMap["probe_devices"],
          probe_info: (raw.probe_info ?? []) as unknown as ProbeMap["probe_info"],
        };
      }

      /** Live settings store probe_profiles keyed by id; PortForm's picker takes a
       *  list. Same flattening /api/wizard/state does (api_wizard/routes.py:129-130). */
      export function readLiveProfiles(settings: Settings): ProbeProfile[] {
        return Object.values(settings?.probe_settings?.probe_profiles ?? {}) as unknown as ProbeProfile[];
      }
      ```

- [x] **Step 7: Write `probeMapRoutes.ts` + its test.** Mirrors
      `helpers/wizard/wizardRoutes.ts:1-10` exactly:
      ```ts
      import { getProbeModules } from "./probeMapApi";
      import type { ProbeModuleCatalog } from "./probeMapTypes";

      export const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

      // React Router route loader for /settings/probes. Throws on failure so the
      // parent's errorElement (SettingsError) renders. Runs alongside -- not
      // inside -- settingsLoader, so useSaveSettings' revalidate() refreshes both.
      export async function probeModulesLoader(): Promise<ProbeModuleCatalog> {
        return getProbeModules(BASE_URL);
      }
      ```
      Test: stub `fetch`, assert the loader resolves the catalog, and assert it **rejects** when
      the endpoint 500s (that is what makes the errorElement fire).

- [x] **Step 8: Full gate.**
      ```sh
      cd web-react && bun run typecheck && bun run lint && bun run test && bun run gen:types:check
      ```
      Expected: green; `bun run lint` exits 0 with exactly the 2 baseline `react-refresh`
      warnings. `gen:types:check` must pass unchanged — **no Python schema changed in Tasks 1–3**,
      so a diff here means something unrelated drifted; investigate rather than regenerating.

- [x] **Step 9: Commit.** **Deliverable:** `bun run test src/helpers/probes` is green and no
      component imports both `ProbeMap` types.

---

### Task 5: Free the probe-editing styles from `wizard.css`

**Files:** Create `web-react/src/components/wizard/probes/probes.css`; Modify
`web-react/src/components/wizard/wizard.css`,
`web-react/src/components/wizard/steps/ProbesStep.tsx`,
`web-react/src/components/wizard/probes/DevicesCard.tsx`,
`web-react/src/components/wizard/probes/PortsCard.tsx`,
`web-react/src/components/wizard/wizardStyles.test.ts`

**Why this task exists:** `wizard.css` is imported **only** by `WizardShell.tsx`
(`wizardStyles.test.ts:100-104` pins it), and every class that gives the probe editor its
appearance lives there — many of them scoped `.pf-wizard X`. Rendered inside a settings tab, both
cards would be unstyled and the `.pf-wizard`-scoped overrides would not apply at all. This is the
plan's one unavoidable refactor of shipped code. **It changes no markup semantics and no test
assertion about behaviour** — only where rules live and what they are scoped to.

**Interfaces:**

- Produces `src/components/wizard/probes/probes.css`, imported as a side effect by `DevicesCard`
  and `PortsCard` so the stylesheet travels with the components to any surface.
- Produces a new scoping hook class `pf-probes-surface`, applied by `ProbesStep`'s root and (in
  Task 6) by `ProbesTab`'s root, replacing `.pf-wizard` in every moved selector.
- Consumes nothing new.

**Steps:**

- [x] **Step 1: Update the guard first, and watch it go red.** In `wizardStyles.test.ts`, the
      wizard-owned check reads `wizard.css` alone (`:74`, `:94-96`). Make it read the union, and
      add an assertion that the new file is actually reachable:
      ```ts
      const PROBES_CSS = join(WIZARD_DIR, "probes", "probes.css");
      ...
      // The probe-editing vocabulary lives in probes/probes.css so it can travel to
      // /settings/probes, which does not render inside .pf-wizard and does not
      // import wizard.css. Both files are wizard-owned; neither may push a rule out
      // to a stylesheet some other surface might refactor away.
      const inWizardCss = new Set([
        ...declaredClasses(readFileSync(WIZARD_CSS, "utf8")),
        ...declaredClasses(readFileSync(PROBES_CSS, "utf8")),
      ]);
      ```
      and replace the `.pf-wizard .pf-probes-card` scoping assertion (`:115-117`) with the one
      that is now true:
      ```ts
      it("scopes its .pf-probes-card override under .pf-probes-surface, not .pf-wizard", () => {
        const css = readFileSync(PROBES_CSS, "utf8");
        expect(css).toContain(".pf-probes-surface .pf-probes-card");
        expect(readFileSync(WIZARD_CSS, "utf8")).not.toContain(".pf-wizard .pf-probes-card");
      });

      it("is imported by the two cards, so it reaches every surface that renders them", () => {
        for (const file of ["DevicesCard.tsx", "PortsCard.tsx"]) {
          expect(readFileSync(join(WIZARD_DIR, "probes", file), "utf8")).toContain(
            'import "./probes.css";',
          );
        }
      });
      ```
      Run `bun run test src/components/wizard/wizardStyles.test.ts` — expect failures (no such
      file). That is the point: the guard leads.

- [x] **Step 2: Create `probes.css` and move the rules.** Cut these blocks out of `wizard.css`
      **verbatim, comments included**, and paste them into the new file, rewriting `.pf-wizard `
      → `.pf-probes-surface ` in each:
      - the button treatment, `wizard.css:153-192` — `.pf-wizard .pf-btn`,
        `.pf-wizard-step > .pf-btn`, `:hover`, `:disabled`, `.pf-wizard .pf-btn-primary`,
        `.pf-btn-primary:hover`. **Keep `.pf-wizard-step > .pf-btn` in `wizard.css`** — it is
        about the wizard's step column, not about probes.
        **CORRECTED 2026-07-26: do NOT re-scope this family to `.pf-probes-surface`.** These
        five rules dress *every* button in the wizard — Back / Next / Finish / Exit Setup as
        well as the probe cards' — and `.pf-probes-surface` sits on the probes step only.
        Swapping the scope leaves the whole wizard chrome as `dashboard.css`'s bare 25px
        shell. They move to `probes.css` with **both** scopes in one selector list
        (`.pf-wizard .pf-btn, .pf-probes-surface .pf-btn { … }`), which is one rule, no
        duplicated declarations, and no change to what matches inside the wizard.
        `wizardStyles.test.ts` pins it.
      - `.pf-module-image` / `-name` / `-description` / `-notes` (`:253-299`) — `DeviceForm.tsx:23-32`
        uses all four. **`.pf-module-details > .pf-module-image` (`:263`) stays in `wizard.css`**:
        `.pf-module-details` is `ModuleCard`'s grid, which no settings surface renders.
      - `.pf-form-actions` (`:312-318`)
      - the whole `/* ---- probes step: DevicesCard / PortsCard ---- */` section (`:320-406`)
      - the whole `/* ---- add/edit dialogs ---- */` section (`:408-443`)
      - the whole `/* ---- discovery results ---- */` section (`:445-497`), including
        `.pf-wizard .pf-field-column > .pf-discovery-group-items`, which BluetoothPicker needs
        and which the plan's prose did not call out by name.

      **Stays behind, verified 2026-07-26:** `.pf-wizard .pf-modal-scrim { position: fixed }`
      (`wizard.css`, near the modals section). That override exists because the wizard's
      nearest positioned ancestor is its scrolling content area, so ConfirmAction's absolute
      scrim would leave the sticky header and footer at full brightness. `/settings/probes`
      has no such chrome, and `settings.css:342`'s `.pf-probes-card { position: relative }`
      makes `dashboard.css`'s `position: absolute` anchor to the card — which is the wanted
      behaviour there. Re-scoping it would be a regression on both surfaces.

      Head the new file with:
      ```css
      /* The probe-editing visual vocabulary, extracted from wizard.css on 2026-07-26.
         DevicesCard/PortsCard are rendered by TWO surfaces now -- the wizard's probes
         step and the /settings/probes tab -- and wizard.css is imported only by
         WizardShell.tsx. Rules that only load on one of the two surfaces are rules
         that do not exist on the other, so this file is imported by the CARDS, which
         both surfaces render, rather than by either surface's shell.

         Selectors that were scoped `.pf-wizard X` are scoped `.pf-probes-surface X`
         here. That class is applied by ProbesStep's root and by ProbesTab's root --
         the two containers that host these cards -- and preserves the original
         (0,2,0) specificity, which several of these rules need in order to beat the
         bare-class versions in settings.css (e.g. .pf-probes-card's
         `position: relative` at settings.css:342, which ConfirmAction's absolute
         scrim depends on and which must NOT be overridden here). */
      ```

- [x] **Step 3: Apply the hook class.** `ProbesStep.tsx:16`:
      ```tsx
      <div className="pf-wizard-step pf-probes-surface" data-step="probes">
      ```
      **Check `wizard-layout.spec.ts` and `ProbesStep.test.tsx` for a selector on that root** —
      `rg -n 'data-step="probes"|pf-wizard-step' web-react/src web-react/tests` — before changing
      it. `data-step="probes"` is untouched, so a spec keyed on that attribute keeps working; one
      keyed on an exact `class` string does not.

- [x] **Step 4: Import the stylesheet from the two cards.** Add to `DevicesCard.tsx` and
      `PortsCard.tsx`, after the existing imports and matching `PelletsPage.tsx:17`'s placement
      idiom:
      ```ts
      import "./probes.css";
      ```

- [x] **Step 5: Run the guard, confirm green.**
      `bun run test src/components/wizard/wizardStyles.test.ts`
      All six assertions must pass, including `"has a non-empty CSS rule for every pf-* class the
      wizard uses"` — that one catches a class dropped during the cut/paste, which is the single
      most likely mistake in this task.

- [x] **Step 6: Full gate + the whole wizard test surface.**
      ```sh
      cd web-react && bun run typecheck && bun run lint && bun run test
      ```
      `DevicesCard.test.tsx`, `PortsCard.test.tsx`, `DeviceForm.test.tsx`, `PortForm.test.tsx`,
      `DeviceConfigField.test.tsx`, `WizardShell.test.tsx` must all stay green **unchanged**. If
      one goes red, the CSS import broke module resolution in jsdom — check that rstest's
      `pluginReact` handles the `.css` side-effect import the same way it does for
      `pellets.css`/`historyChart.css` (it does; those ship today).

- [x] **Step 7: Look at it.** With the backend up and `bun run dev` running, open `/wizard`,
      advance to the Probes step, and confirm the devices/ports cards, the add/edit dialogs and a
      Discover panel are **visually unchanged**. This task is a pure refactor; a visible
      difference means a rule was dropped or mis-scoped. **If the backend is not reachable, say
      so rather than marking this step done** — backlog lesson 2 is that the wizard shipped with
      zero CSS and a fully green suite.

- [x] **Step 8: Commit.** **Deliverable:** `wizard.css` no longer contains `.pf-wizard .pf-probes-card`;
      `probes.css` is imported by both cards; every wizard test is green and the Probes step looks
      identical.

---

### Task 6: `ProbesTab` — the page

**Files:** Create `web-react/src/components/settings/tabs/ProbesTab.tsx` +
`ProbesTab.test.tsx`

**Interfaces:**

- Consumes `useOutletContext<{ settings: Settings; mode: string }>()` (from `SettingsShell.tsx:45`),
  `useLoaderData() as ProbeModuleCatalog` (from `probeModulesLoader`), `useRevalidator`,
  `readLiveProbeMap`, `readLiveProfiles`, `applyProbeMap`,
  `components/wizard/probes/{DevicesCard,PortsCard}`, `components/settings/fields/Section`.
- Produces `export function ProbesTab(): JSX.Element`.
- Produces no new backend calls beyond `applyProbeMap`; `DevicesCard` keeps making its own
  `/api/wizard/probes/validate-bus-kinds` and discovery calls.

**Behaviour contract:**

1. Working `ProbeMap` state seeded from `readLiveProbeMap(settings)`, re-seeded by the
   **render-phase `prev`-compare idiom** whenever the loader hands back a new `settings` object.
   **No `useEffect`.**
2. `dirty` = working map differs from the live map (JSON compare — the reducer returns fresh
   objects, so identity is not a reliable signal after a no-op edit).
3. Save is disabled when: not dirty, or `mode !== "Stop"`, or the working map contains a device
   whose module `requires_install` and which is absent from the live map.
4. Discard resets working state to the live map.
5. On a successful save, `revalidate()` re-runs both loaders; the `prev`-compare then re-seeds
   from the fresh settings, which clears `dirty` without a second code path.

**Steps:**

- [x] **Step 1: Write the failing tests** `ProbesTab.test.tsx` (`.tsx` → jsdom). Render inside a
      memory router that supplies both the outlet context and the loader data — copy the harness
      shape from an existing tab test (`rg -n "createMemoryRouter|MemoryRouter" src/components/settings/tabs/*.test.tsx`)
      and add `loader` to the route. Assert:
      - **seeds from live settings**: with `settings.probe_settings.probe_map` holding one device
        and one probe, `getByRole("region", { name: "Probe devices" })` shows the device name and
        `getByRole("region", { name: "Probe ports" })` shows the probe name. (Those accessible
        names come from `DevicesCard.tsx:113` / `PortsCard.tsx:80` — reuse them, do not add new
        ones.)
      - **Save disabled until dirty**: `getByRole("button", { name: "Save probe configuration", exact: true })`
        is disabled on first render.
      - **Save disabled while running**: with `mode: "Smoke"`, Save stays disabled and an alert
        reads that the grill must be stopped.
      - **dependency warning**: given `requires_install: { ds18b20: true }` and a working map that
        adds a `ds18b20` device not in the live map, a `role="alert"` names `ds18b20` and Save is
        disabled. Drive this through the real `DevicesCard` add flow (select the module, fill the
        name, submit) rather than reaching into state — the point is that the two shipped
        components and this guard compose.
      - **posts the working map**: `rs.fn` on `applyProbeMap` via `rs.mock` of
        `../../../helpers/probes/probeMapApi`; after an edit + Save, the first argument's
        `probe_info` has the edited label.
      - **surfaces a rejection**: `applyProbeMap` resolving `{ ok: false, message: "Stop the grill…" }`
        renders that exact text in a `role="alert"` and leaves the working map intact (the user's
        edits must not be thrown away on a refusal — same rule as `SaveBar.tsx:8-10`).
      - **Discard restores**: after an edit, Discard puts the live probe name back.

- [x] **Step 2: Run, confirm all fail.** `bun run test src/components/settings/tabs/ProbesTab.test.tsx`

- [x] **Step 3: Implement.** The whole component, with the load-bearing parts spelled out:
      ```tsx
      import { useState } from "react";
      import { useLoaderData, useOutletContext, useRevalidator } from "react-router";
      import { applyProbeMap, readLiveProbeMap, readLiveProfiles } from "../../../helpers/probes/probeMapApi";
      import type { ProbeModuleCatalog } from "../../../helpers/probes/probeMapTypes";
      import type { Settings } from "../../../helpers/settings/settingsApi";
      import type { ProbeMap } from "../../../helpers/wizard/probeTypes";
      import { DevicesCard } from "../../wizard/probes/DevicesCard";
      import { PortsCard } from "../../wizard/probes/PortsCard";
      import { Section } from "../fields/Section";

      const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

      /** Modules being ADDED that the running system cannot install. Mirrors
       *  blueprints/api/probe_map_actions.py::unsupported_new_modules exactly -- the
       *  server is authoritative and will 422; this is the same rule, evaluated
       *  early, so the user learns before losing an edit. Computed during render;
       *  there is no state here and no effect. */
      function blockedModules(
        working: ProbeMap,
        live: ProbeMap,
        requiresInstall: Record<string, boolean>,
      ): string[] {
        const installed = new Set(live.probe_devices.map((d) => d.module));
        const blocked = new Set<string>();
        for (const d of working.probe_devices) {
          if (!installed.has(d.module) && requiresInstall[d.module] !== false) blocked.add(d.module);
        }
        return [...blocked].sort();
      }

      export function ProbesTab() {
        const { settings, mode } = useOutletContext<{ settings: Settings; mode: string }>();
        const catalog = useLoaderData() as ProbeModuleCatalog;
        const revalidator = useRevalidator();

        const live = readLiveProbeMap(settings);
        const [working, setWorking] = useState<ProbeMap>(live);
        const [prev, setPrev] = useState(settings);
        const [saving, setSaving] = useState(false);
        const [error, setError] = useState<string | null>(null);
        const [saved, setSaved] = useState(false);

        // Render-phase adjustment, NOT an effect: the React Compiler is active and
        // setState-in-useEffect for derived state is banned. Same idiom as
        // SafetyTab.tsx:36-40. A successful save calls revalidate(), which hands
        // back a NEW settings object -- so this is also what clears `dirty`.
        if (settings !== prev) {
          setPrev(settings);
          setWorking(readLiveProbeMap(settings));
          setError(null);
        }

        const dirty = JSON.stringify(working) !== JSON.stringify(live);
        const blocked = blockedModules(working, live, catalog.requires_install);
        const running = mode !== "Stop";
        const canSave = dirty && !running && blocked.length === 0 && !saving;

        const onSave = async () => {
          setSaving(true);
          setSaved(false);
          setError(null);
          const r = await applyProbeMap(BASE_URL, working);
          setSaving(false);
          if (r.ok) {
            setSaved(true);
            revalidator.revalidate(); // re-runs settingsLoader AND probeModulesLoader
          } else {
            // Deliberately keeps `working` -- the store is untouched on every
            // rejection path, so there is no drift to correct, and discarding the
            // user's edits exactly when they need to fix them is the worse bug.
            setError(r.message);
          }
        };

        return (
          <div className="pf-probes-surface">
            <Section title="Probes">
              {running && (
                <p role="alert">
                  The grill is running ({mode}). Stop it before changing probe configuration.
                </p>
              )}
              {blocked.length > 0 && (
                <p role="alert">
                  These probe modules need the setup wizard to install their dependencies first:{" "}
                  {blocked.join(", ")}. Remove them here, or run the wizard to add them.
                </p>
              )}
              {error && <p role="alert">{error}</p>}

              <DevicesCard
                probeMap={working}
                modules={catalog.modules}
                baseUrl={BASE_URL}
                onChange={setWorking}
              />
              <PortsCard
                probeMap={working}
                profiles={readLiveProfiles(settings)}
                onChange={setWorking}
              />

              <div className="pf-settings-actions">
                <button
                  className="pf-modal-btn accent"
                  disabled={!canSave}
                  onClick={() => void onSave()}
                >
                  {saving ? "Applying…" : "Save probe configuration"}
                </button>
                <button className="pf-modal-btn" disabled={!dirty || saving} onClick={() => setWorking(live)}>
                  Discard changes
                </button>
                {saved && !dirty && <span className="pf-settings-saved">Applied ✓</span>}
              </div>
            </Section>
          </div>
        );
      }
      ```
      Notes the implementer must not "simplify" away:
      - **`SaveBar` is deliberately not used.** It renders a single "Save" button with no
        disabled-reason, no Discard, and its own `SaveStatus` union — this tab has three distinct
        block reasons and a second action. Reusing it would mean widening a component eight other
        tabs share.
      - **`requiresInstall[d.module] !== false`** (not `=== true`): a module missing from the
        catalog entirely — a stale/renamed module in a saved map — must count as blocked, matching
        the server's `module_requires_install(None) → True`.
      - **`readLiveProfiles(settings)` is called in the render body, not memoized.** It is
        `Object.values` over a handful of profiles; a `useMemo` here would be noise.

- [x] **Step 4: Run, confirm pass.** Then confirm nothing else moved:
      `bun run test src/components/wizard src/components/settings`

- [x] **Step 5: Full gate.**
      `bun run typecheck && bun run lint && bun run test && bun run gen:types:check`

- [x] **Step 6: Commit.** **Deliverable:** `ProbesTab.test.tsx` green, including the
      dependency-guard case driven through the real `DevicesCard` add flow.

---

### Task 7: Register the route and the tab pill

**Files:** Modify `web-react/src/components/App.tsx`,
`web-react/src/components/settings/SettingsShell.tsx`

**Interfaces:**

- Produces the route `/settings/probes` → `<ProbesTab/>` with `loader: probeModulesLoader`,
  nested under the existing `/settings` route so it inherits `settingsLoader`,
  `errorElement: <SettingsError/>` and `HydrateFallback`.
- Produces one `SETTINGS_TABS` entry, `{ path: "probes", label: "Probes" }`.

**Steps:**

- [x] **Step 1: Write the failing route test.** `App.test.tsx` already drives the exported
      `routes` array through `createMemoryRouter` (`App.tsx:34-36` explains why `routes` is
      exported). Add a case there — check the existing mocks first
      (`rg -n "rs.mock|getSettings" src/components/App.test.tsx`), because the settings loader is
      already stubbed and the new child loader needs the same treatment:
      ```tsx
      it("renders the Probes settings tab at /settings/probes", async () => {
        const router = createMemoryRouter(routes, { initialEntries: ["/settings/probes"] });
        render(<RouterProvider router={router} />);
        expect(await screen.findByRole("region", { name: "Probe devices" })).toBeInTheDocument();
      });
      ```
      Also add to `SettingsShell.test.tsx`: the pill list contains a link named `"Probes"`
      pointing at `probes`. **Use `exact: true`** — `"Probes"` would otherwise match
      `"Probe Profiles"` if that tab is ever added.

- [x] **Step 2: Run, confirm fail.**
      `bun run test src/components/App.test.tsx src/components/settings/SettingsShell.test.tsx`

- [x] **Step 3: Add the route.** In `App.tsx`, import
      `import { probeModulesLoader } from "../helpers/probes/probeMapRoutes";` and
      `import { ProbesTab } from "./settings/tabs/ProbesTab";` (keep the import block's existing
      alphabetical grouping), then add the child **after `platform`** in the `/settings` children
      array (`:75-87`):
      ```tsx
                { path: "probes", element: <ProbesTab />, loader: probeModulesLoader },
      ```
      This is the only child route in the settings tree with its own loader. That is intentional
      and worth a comment:
      ```tsx
                // The only settings child with its own loader: the probes MODULE
                // MANIFEST (18 entries with per-field config metadata and vendor
                // photos) is ~40 KB of data every other tab has no use for, so it is
                // fetched on navigation into this tab rather than added to
                // settingsLoader's Promise.all. useSaveSettings' revalidate() re-runs
                // active child loaders too, so a save still refreshes both halves.
      ```

- [x] **Step 4: Add the pill.** In `SettingsShell.tsx`'s `SETTINGS_TABS` (`:4-17`), after
      `{ path: "platform", label: "Platform" }`:
      ```ts
        { path: "probes", label: "Probes" },
      ```
      Place it last deliberately: it is the most destructive tab in the group, and the `tabs`
      filter at `:28` is a `.filter()` over this array, so order here is display order.

- [x] **Step 5: Run, confirm pass**, then the whole suite: `bun run test`.

- [x] **Step 6: Drive it in a browser.** Backend up, `bun run dev` up:
      navigate to `/settings/probes`, confirm the two cards render **styled** (this is where a
      missed rule in Task 5 shows), the module photos load (not 404 — `moduleImageUrl` +
      the `/static/img` dev proxy), a Discover button returns something or a friendly error, and
      the page fits **1280×720 without page scroll**. If the backend is not reachable, say so
      rather than marking this done.

- [x] **Step 7: Full gate + commit.**
      `bun run typecheck && bun run lint && bun run test && bun run gen:types:check && bun run build`
      **Deliverable:** `/settings/probes` is reachable by clicking, from a cold load.

---

### Task 8: End-to-end round trip

**Files:** Create `web-react/tests/e2e/probes.spec.ts`

**Interfaces:** Consumes the running stack. Produces no app code.

**Preconditions, restated because this suite is destructive:**

- A real backend must be running — `control.py` **and** gunicorn — and **gunicorn must have been
  restarted since Task 2 landed**, or `POST /api/probe_map` 404s and the spec fails as if the
  frontend were broken. This has cost three separate tasks.
- From a jj workspace, `PIFIRE_DB_PATH` must point at the **same** database the backend serves.
- The suite runs `workers: 1` and is globally destructive. **Do not run it concurrently with
  another agent's e2e work.**

**Steps:**

- [ ] **Step 1: Neutralization check for the e2e path.** This spec drives a UI that can write a
      probe map, which sets `probe_map_update`, which makes `control.py` rebuild probe devices.
      It must not be able to reach an installer. Confirm:
      ```sh
      rg -n "os\.system|subprocess|restart_scripts" blueprints/api/routes.py blueprints/api/probe_map_actions.py
      ```
      Expected: **zero hits.** If this is ever non-zero, this spec does not run until it is zero
      again.

- [ ] **Step 2: Write the spec.**
      ```ts
      // End-to-end coverage for the Probes settings tab at /settings/probes.
      //
      // This spec REWRITES THE LIVE PROBE MAP of whatever backend it reaches. It
      // snapshots the map first and restores it in afterAll, but a crash mid-run
      // leaves the grill with the test's map. Never point it at a real cooker.
      //
      // Preconditions: control.py + gunicorn running, gunicorn restarted since
      // POST /api/probe_map landed, and PIFIRE_DB_PATH pointing at the DB the
      // backend serves if you are in a jj workspace.
      //
      // LOCATOR DISCIPLINE: every getByRole below is scoped by getByRole("region")
      // or carries exact: true. "Save probe configuration" and "Save" are different
      // buttons on this page's ancestors; do not shorten the name.
      import { expect, test } from "@playwright/test";

      let original: unknown;

      test.beforeAll(async ({ request }) => {
        const res = await request.get("/api/settings");
        original = (await res.json()).settings.probe_settings.probe_map;
        expect(original).toBeTruthy();
      });

      test.afterAll(async ({ request }) => {
        await request.post("/api/probe_map", { data: { probe_map: original } });
      });

      test("renaming a probe round-trips to live settings", async ({ page, request }) => {
        // The tab refuses to save while the grill runs; make sure it is stopped.
        await request.post("/api/set/mode/stop");

        await page.goto("/settings/probes");
        const ports = page.getByRole("region", { name: "Probe ports" });
        await expect(ports).toBeVisible({ timeout: 15000 });

        const renamed = `E2E${Date.now().toString().slice(-6)}`;
        await ports.getByRole("button", { name: "Edit", exact: true }).first().click();
        const form = page.getByRole("dialog", { name: "edit probe" });
        await form.getByLabel("Probe Name").fill(renamed);
        await form.getByRole("button", { name: "Save", exact: true }).click();

        const save = page.getByRole("button", { name: "Save probe configuration", exact: true });
        await expect(save).toBeEnabled();
        await save.click();
        await expect(page.getByText("Applied ✓", { exact: true })).toBeVisible({ timeout: 10000 });

        const after = (await (await request.get("/api/settings")).json()).settings;
        const labels = after.probe_settings.probe_map.probe_info.map((p: { name: string }) => p.name);
        expect(labels).toContain(renamed);
        // wizard.py:230's regeneration, ported into apply_probe_map: the history
        // chart config must have followed the map.
        expect(Object.keys(after.history_page.probe_config).length).toBeGreaterThan(0);
      });

      test("the tab refuses to save while the grill is running", async ({ page, request }) => {
        await request.post("/api/set/mode/monitor");
        try {
          await page.goto("/settings/probes");
          await expect(page.getByRole("alert").filter({ hasText: "Stop it before" })).toBeVisible({
            timeout: 15000,
          });
          await expect(
            page.getByRole("button", { name: "Save probe configuration", exact: true }),
          ).toBeDisabled();
        } finally {
          await request.post("/api/set/mode/stop");
        }
      });

      test("the page fits 1280x720 without page scroll", async ({ page }) => {
        await page.goto("/settings/probes");
        await expect(page.getByRole("region", { name: "Probe devices" })).toBeVisible({ timeout: 15000 });
        // Both halves: an overflow:hidden container CLIPS an oversized grid rather
        // than scrolling, so scrollHeight alone passes vacuously (pellets.spec.ts
        // learned this the hard way with a row sitting at y=704).
        const metrics = await page.evaluate(() => ({
          scroll: document.documentElement.scrollHeight,
          inner: window.innerHeight,
          lastBottom: [...document.querySelectorAll(".pf-settings-actions button")]
            .map((el) => el.getBoundingClientRect().bottom)
            .reduce((a, b) => Math.max(a, b), 0),
        }));
        expect(metrics.scroll).toBeLessThanOrEqual(metrics.inner);
        expect(metrics.lastBottom).toBeLessThanOrEqual(metrics.inner);
      });
      ```
      **Verify the mode-setting path before relying on it:** `rg -n '"set"' blueprints/api/routes.py`
      shows `api_page` routes `action in ["get","set","cmd","sys"]` through `process_command`
      (`:379-388`). Confirm `/api/set/mode/stop` is the real spelling with
      `curl -X POST localhost:5000/api/set/mode/stop`; if it differs, use
      `tests/e2e/helpers.ts`'s `ensureStopped` instead — check whether it already exists
      (`rg -n "ensureStopped" web-react/tests/e2e/helpers.ts`) and prefer it if so.

- [ ] **Step 3: Run it.**
      ```sh
      cd web-react && bun run test:e2e tests/e2e/probes.spec.ts
      ```
      All three green. If the first test 404s on `/api/probe_map`, **restart gunicorn** before
      debugging anything else.

- [ ] **Step 4: Confirm the controller actually reacted.** The e2e asserts settings; it does not
      assert the control loop rebuilt anything, and per backlog lesson 3, asserting one end is not
      asserting the seam. With `control.py` running, immediately after the first test:
      ```sh
      QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run python -c "
      from common.datastore_accessors import read_control
      from common.datastore import get_blob
      print('flag cleared:', read_control().get('probe_map_update') is False)
      print('device info:', get_blob('probe_device_info') is not None)
      "
      ```
      Both `True`. **Verify `get_blob`'s real import path first** — `rg -n "def get_blob|def read_generic_key" common/datastore.py common/datastore_accessors.py` — and use whatever the repo actually exposes.
      If the flag is still `True`, `control.py` is not running or is an older process; that is a
      finding, not a reason to weaken the assertion.

- [ ] **Step 5: Run the coverage gate once.** `bun run test:coverage` — the per-file 75 % line
      floor applies to every new `src/` module (`rstest.config.ts:53-55`).

- [ ] **Step 6: Commit.** **Deliverable:** three green e2e tests and a manual confirmation that
      the control process cleared the flag.

---

### Task 9: Correct the backlog and record what shipped

**Files:** Modify `docs/superpowers/react-migration-backlog.md`

**Interfaces:** Documentation only. No code.

**Why it is a task and not a footnote:** the entry this plan started from was wrong about live
code, and this repo has a documented history of plans inheriting such errors (one recent plan was
factually wrong seven times). Leaving the wrong sentence in place guarantees the next agent
repeats the mistake.

**Steps:**

- [ ] **Step 1: Replace the `probeconfig` bullet** (`:289-290`) with the corrected facts:
      ```markdown
      - [x] **probeconfig** — SHIPPED 2026-07-26 as the `/settings/probes` tab
            (`plans/2026-07-26-react-probeconfig-page.md`, 9 tasks). **The old entry here was
            wrong on one point:** `blueprints/probeconfig/` is not a standalone page. It renders
            no full document (no `<!doctype>`, no navbar) — it is a fragment API whose only
            consumer is the Flask wizard (`wizard.html:3, 349` + `probeconfig.js`), and
            `tests/web/test_page_probeconfig.py:6-23` says so in its own docstring. Its ten
            `(section, action)` behaviours were **already** 100 % covered in React by the
            wizard's probes step, so the reuse half of the old entry was right: the shipped
            reducer, both cards, all four discovery pickers and `ConfirmAction` were reused
            verbatim, unmoved and unrenamed. What was missing was never the editor — it was a way
            to edit the **live** probe map without re-running the wizard. That is what shipped.
      ```

- [ ] **Step 2: Add the SHIPPED-section entry**, matching the style of the pellets entry, naming
      the two new endpoints (`GET /api/probe_modules`, `POST /api/probe_map`), the new control
      flag (`probe_map_update`), and the revived `ProbesMain.update_probe_map()`.

- [ ] **Step 3: Record the three disclosed gaps** in the backlog's OPEN section — copied from
      "Out of scope" below, not summarized away:
      1. `control["notify_data"]` and `settings["recipe"]["probe_map"]` are not regenerated when
         the probe map changes (matching `run_wizard`, which does not either). A probe renamed
         here leaves a stale notify entry.
      2. `ProbesMain._setup_probe_devices` drops the previous device objects without closing
         them; a BT/USB-HID handle is released at GC, not at rebuild.
      3. Two humans editing probes simultaneously in the Flask settings page and the React tab,
         both in Stop mode, is last-write-wins.

- [ ] **Step 4: Do NOT delete `blueprints/probeconfig/`.** It is load-bearing for the Flask
      wizard, which is still the only installer UI. `tests/web/test_page_probeconfig.py` stays as
      the characterization net.

- [ ] **Step 5: Commit.** **Deliverable:** the backlog no longer claims probeconfig is a
      standalone page.

---

## Parallelization

**Concurrency requires isolated jj workspaces. Disjoint file sets are NOT sufficient.** Two agents
in one working copy share `bun.lock`, `node_modules`, the rsbuild/rstest caches, `pifire.db`, and
the dev-server and gunicorn ports — every one of which is a shared mutable resource that no
file-level analysis sees. A workspace also needs two things a fresh `jj workspace add` does not
give it:

1. **Copy `.lsp.json` in.** It is gitignored, so a new workspace has none, and its absence is the
   real cause of "LSP unavailable" — not a broken language server.
2. **Run `bun install`** in the workspace's `web-react/`.

And per the harness notes, a second workspace running e2e needs its **own** dev servers *and* its
own PiFire:

```sh
export PORT=5273 DEMO_PORT=5274                   # this workspace's dev servers
export PIFIRE_BACKEND_URL=http://localhost:5100   # this workspace's backend
export PIFIRE_DB_PATH="$PWD/pifire.db"            # and its own datastore
uv run python control.py &
uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5100 -w 1 app:app &
```

`PIFIRE_BACKEND_URL`, **never** `PUBLIC_PIFIRE_URL` — rsbuild inlines every `PUBLIC_*` name into
the browser bundle, turning same-origin requests into cross-origin ones that Flask answers without
CORS headers.

### Waves

| Wave | Tasks | Workspaces | Notes |
|---|---|---|---|
| **1** | **1**, **3**, **5** | up to 3 | Three genuinely independent starting points. |
| **2** | **2**, **4** | up to 2 | Task 2 needs Task 1 (shares `probe_map_actions.py` + `routes.py`). Task 4 needs nothing but is written against Tasks 1–2's contracts, so it is cheapest to start once Task 1's response shape is committed. |
| **3** | **6** | 1 | Needs Tasks 4 (client) and 5 (styles). |
| **4** | **7** | 1 | Needs Task 6. |
| **5** | **8** | 1, main checkout preferred | e2e. |
| **6** | **9** | 1 | Docs; can also be folded into whoever lands Task 8. |

**Wave 1 detail — why these three are safe together:**

- **Task 1** touches `blueprints/api/{routes.py, probe_map_actions.py}` and
  `tests/web/test_api_probe_map.py`.
- **Task 3** touches `probes/main.py`, `controller/runtime/controller.py`, `common/defaults.py`,
  `tests/fakes/probes.py`, `tests/characterization/`, `tests/unit/probes/`.
- **Task 5** touches only `web-react/src/components/wizard/**`.

No overlap. But both Python tasks run `uv run pytest tests/` as their gate, and Task 5 runs
`bun run test` — so each still needs its own workspace to avoid fighting over the rstest cache and
the datastore.

**Task 2 must NOT run concurrently with Task 1.** They edit the same two files, and Task 2's route
imports Task 1's helper module. Serialize.

**Task 5 must NOT run concurrently with any wizard-styling or dashboard-reflow work.** It rewrites
`wizard.css` wholesale. Check for in-flight work touching `web-react/src/components/wizard/wizard.css`
or `settings.css` before starting; if any exists, **serialize behind it** rather than merging CSS
by hand.

**Task 8 must NOT run concurrently with any other e2e work, in any workspace, against the same
backend.** `workers: 1` protects specs from each other *within* one run; it protects nothing from a
second run.

**Critical path:** Task 1 → Task 2 → (already-parallel Task 4) → Task 6 → Task 7 → Task 8. Real
concurrency is Wave 1 only, worth roughly two tasks of wall clock.

---

## Out of scope, deliberately

- **Deleting `blueprints/probeconfig/`.** It is the Flask wizard's probe editor and the Flask
  wizard is still the only installer UI. No page has been deleted on this migration yet and this
  is not the plan that starts.
- **Editing `tests/web/test_page_probeconfig.py`.** It is the characterization net. It must stay
  green and unmodified; if this work makes it red, this work is wrong.
- **A `/settings/probe-profiles` tab.** The A/B/C thermistor-coefficient editor
  (`_settings_editprofile` / `_settings_addprofile`, `blueprints/settings/routes.py:252-350`) is
  the other half of the deferred sub-project the 2026-07-25 audit names. `PortForm`'s profile
  picker consumes profiles read-only; creating them stays in Flask for now.
- **Regenerating `control["notify_data"]` and `settings["recipe"]["probe_map"]`** after a probe-map
  write. `run_wizard` regenerates only `history_page.probe_config` (`wizard.py:227-231`); matching
  the installer exactly is the conservative choice. Consequence: a probe renamed here leaves a
  stale `notify_data` entry keyed on the old label. **Record it in the backlog** (Task 9 Step 3).
- **Closing probe device handles on rebuild.** `_setup_probe_devices` rebinds the list and lets
  old instances fall out of scope. Fixing that means giving every probe driver a `close()`, which
  is a `probes/` refactor with its own blast radius. The Stop-mode gate is the mitigation.
- **Optimistic concurrency on the settings blob** (`lastupdated.time` compare-and-swap). That race
  is datastore-wide and pre-existing; solving it for one tab would be a false comfort.
- **Reusing `ProbesStep` itself.** It is 42 lines bound to `WizardWorking` and to the wizard's
  units field; the two cards are the reusable unit (see F6).
- **New `/api/probes/*` aliases for the four discovery endpoints.** `/api/wizard/scan`,
  `/scan/bluetooth`, `/scan/thermoworks` and `/probes/validate-bus-kinds` are generic hardware
  discovery. Duplicating them under a second prefix would create two contracts to keep in sync.
- **A `GET /api/probe_map`.** `GET /api/settings` already ships the live map and is already
  fetched by `settingsLoader`. A second read path would be a second thing to keep consistent.

---

## Could NOT verify

Stated plainly, because a plan that hides its gaps is worse than one that has them.

- **Nothing in this plan was opened in a browser.** The investigation was read-only by
  instruction. Task 5 Step 7 and Task 7 Step 6 are the checks that make the visual claims real. The
  1280×720 fit in Task 8 is derived from `.pf-shell-main`'s box model (`shell.css:17-22`) and the
  settings grid, **not observed**. If the page scrolls, fix the layout — do not relax the
  assertion.
- **`ProbesMain.update_probe_map()` has never been executed against real probe hardware**, by
  anyone, ever — it had zero callers before this plan. Task 3's unit tests exercise it with
  `virtual_average` (pure computation) and a deliberately unimportable module. Whether rebuilding a
  live `bt_ibbq` or `ds18b20` mid-process succeeds is **unverified** and is Hazard 2's substance.
  The first real-hardware run is the check.
- **`configured_bus_kinds(settings, probe_map)` was read, not exercised with a real conflicting
  live config.** Task 2's bus-conflict test synthesizes the conflict by writing
  `platform.devices.distance.i2c_bus_kind`; that path was traced through
  `common/i2c_bus.py:223-239` but not run.
- **`/api/set/mode/stop` was not called.** Task 8 Step 2 says to confirm the spelling with `curl`
  before relying on it, and to prefer `tests/e2e/helpers.ts`'s `ensureStopped` if it exists.
- **`write_control_store` / `write_settings_store` were seen only in `tests/web/test_api_wizard.py`'s
  import list.** Task 2 Step 2 says to confirm both names against
  `common/datastore_accessors.py` before running.
- **The exact pre-existing Biome/ESLint warning count was not measured** — `bun run lint` was not
  run (read-only investigation). The brief states 2 `react-refresh` warnings; treat that as the
  baseline and report immediately if the untouched tree shows a different number.
- **The 18-module manifest count and the 6/12 dependency split WERE verified programmatically**
  against `wizard/wizard_manifest.json` on 2026-07-26 — those two numbers are not guesses. If
  Task 1's `len(modules) == 18` assertion fails, the manifest changed; update the number and note
  it, do not delete the assertion.
- **Whether any other in-flight branch is editing `wizard.css` or `settings.css`** was not checked
  at plan time. Task 5's serialization warning is a standing instruction, not an observation.

---

## Self-Review

**Spec coverage.** Every row of F1's `(section, action)` table maps to shipped React code
(columns 5), and every row of F1's discovery table maps to a shipped endpoint. The delta this plan
implements is the delivery path, and each piece has a task: catalog read → Task 1; live write with
guards → Task 2; the controller noticing → Task 3; the TS client and the one type narrowing →
Task 4; the styles reaching a non-wizard surface → Task 5; the tab → Task 6; reachability →
Task 7; proof end-to-end → Task 8; the record → Task 9.

**Placeholder scan.** No "TBD", no "add appropriate error handling", no "similar to Task N". Every
endpoint path, JSON key, error string, CSS class, line reference and command above is cited to a
file and line in live code, or is introduced in full in the step that creates it. Five places
deliberately say *verify this name before relying on it* (`write_control_store` /
`write_settings_store`, `/api/set/mode/stop`, `get_blob`, `_setup_probe_devices`'s call sites,
`tests/unit/probes/__init__.py`) — those are instructions to check, not gaps in the design.

**Type consistency.** `ProbeMap`/`ProbeDevice`/`Probe`/`ProbeProfile`/`ProbeModuleData` come from
the shipped `helpers/wizard/probeTypes.ts` and are unchanged. `ProbeModuleCatalog` and
`ApplyProbeMapResult` (Task 4) are consumed by Tasks 6 and 7 only. The generated
`settingsTypes.gen.ts` `ProbeMap` — a **different, all-optional** interface with the same name — is
crossed in exactly one function, `readLiveProbeMap`, so no component ever holds both.

**Cross-process seams are pinned at both ends.** `GET /api/probe_modules` is asserted in Python
(Task 1 Steps 2) and in TypeScript against a literal wire fixture (Task 4 Step 1).
`POST /api/probe_map`'s four rejection codes are asserted in Python (Task 2 Step 2) and translated
+ asserted in TypeScript (Task 4 Step 4). The `probe_map_update` control flag is written in Task 2's
test and read in Task 3's controller test. No decision is traced to only one end of its data path.

**Safety constraints are honoured, not just quoted.** Three tasks open with an explicit
`rg` neutralization sweep (1, 2, 3) and Task 8 repeats it before touching the live stack; the
design deliberately avoids `restart_scripts()` so no test in this plan can reach a shell-out;
`settings.json` appears nowhere except in the constraint forbidding it; and the e2e spec snapshots
and restores the probe map because the suite is globally destructive to whatever backend it
reaches.

**Hazards answered.** Whole-map clobber → argued from the data model rather than waved away, with
three named mitigations and one honestly unmitigated case routed to the backlog. Reviving dead code
→ both failure modes named, the rejected alternative named with its reason, and the untested-ness
recorded in "Could NOT verify" rather than buried.

**Task count: 9.** Three Python, five JavaScript/TypeScript (one of which is CSS-only), one docs.
