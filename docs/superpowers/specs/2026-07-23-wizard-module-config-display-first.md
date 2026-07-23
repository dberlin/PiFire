# Wizard Module-Config Surface (React), Display-First — Design Spec

**Date:** 2026-07-23
**Status:** Approved (design), pending user spec review
**Scope:** `web-react/` — a new React wizard shell replacing the Flask/Jinja
hardware-setup wizard, built **display-first** as the clean exemplar of a
**shared, reusable module-config surface** that grillplatform / probes /
distance will later extend. Plus one Flask-side manifest bug fix (separate
commit). NO new install/`wizard.py` logic — the existing detached installer is
reused via thin JSON endpoints.

## Context

PiFire's hardware setup lives in the Flask wizard (`blueprints/wizard/`). Four
module sections — **grillplatform, display, distance, probes** — all funnel
through ONE handler (`_wizard_modulecard`, `routes.py:105-119`) and ONE Jinja
macro (`render_wizard_card`), differing only by a single ternary computing
`moduleSettings["config"]`. Applying changes is a **staged installer**, not a
settings save: Finish fires a detached background process
(`os.system("{python} wizard.py &")`) that pip-installs driver deps, shells out
to `board-config.py`/`raspi5.sh`/`ds18b20.sh`, and may require a reboot;
progress is polled via `/wizard/installstatus`.

Two full inventories back this spec: `.superpowers/sdd/wizard-family-inventory.md`
(grillplatform/display/distance + shared-vs-bespoke analysis) and
`.superpowers/sdd/probeconfig-inventory.md` (probes + discovery flows).

**Why display-first:** display is the cleanest exemplar — pure "pick a module →
edit its `device_specific` config" — and the ONLY section exercising the
`settings-by-module` config source, so building the shared spine against it
validates both config modes before the harder consumers (probes' port graph,
grillplatform's pin/PWM widgets). Decision recorded 2026-07-23.

## Goals

1. A React wizard **shell** (stepper: welcome → grillplatform → probes → display
   → distance → finish) with the **display step fully real** and the other three
   as navigable placeholders.
2. The shared **`<ModuleCard>`** component + field-widget registry + discovery
   panels + `ConfigOptionField`, all designed for reuse by the later sections.
3. The faithful **staged-install** apply flow: Finish triggers the existing
   detached installer, `InstallProgress` polls to completion (reboot/restart
   branches).
4. Fix the display double-default manifest bug (separate commit).
5. Coverage ≥75% per-file (enforced).

## Apply model & dataflow (the corrected, authoritative version)

The Flask reality (inventory §6, callouts #1/#10), which supersedes any earlier
framing: `wizardInstallInfo` is **recomputed from settings on every GET** and
overwritten; module-card AJAX does **not** persist to it; **all edits live in the
browser form** until Finish submits every field in ONE POST; reloading discards
edits. So the *faithful* model is **client-held-until-Finish**, and the
per-module-change server round-trip is **unnecessary** in React: the only
server-computed values are the settings-dependency current values and (display
only) `settings["display"]["config"][module]` — and `DisplaySettings.config` is
a **dict keyed by every module** (`settings_schema.py:261`), so the client holds
the whole dict and looks up `[module]` locally, defaulting to `{}` (which fixes
the KeyError footgun for free).

**One deliberate improvement over Flask:** add **draft persistence** — flush the
client working-state to the wizard blob on step transitions so a reload resumes
mid-setup (Flask loses it). This is an intentional divergence, called out here.

**Required consequence for correctness:** Flask's GET **recomputes**
`wizardInstallInfo` from settings/defaults and **overwrites** the blob every
time (`routes.py:294-300`). If `GET /api/wizard/state` did the same, a reload
would clobber the draft. So `/api/wizard/state` MUST instead **read the existing
draft blob when one is present** (resume), and compute-from-settings/defaults
only when no draft exists (fresh entry). A completed/cancelled Finish clears the
draft so the next entry recomputes. This divergence from Flask's always-recompute
is what makes draft persistence actually work — it is not optional.

```
GET  /api/wizard/state          → { modules_metadata (all sections/modules from
                                     wizardData.modules), current selections +
                                     settings-dependency values + display.config
                                     dict, control.mode, first_time_setup }
   (module select, field edits, step nav = pure client state, NO round-trips)
POST /api/wizard/draft          → flush client working-state to wizardInstallInfo
                                     blob on step transition (draft persistence)
POST /api/wizard/scan  {kind,…} → discovery { groups:[{title,items:[{value,label}]}],
                                     error }   (reuses discover_extended_i2c_buses /
                                     discover_mcp2221_devices / discover_ft232h_devices /
                                     discover_usb_serial_devices)
POST /api/wizard/finish {payload} → server validates bus kinds across ALL sections
                                     (validate_bus_kinds(wizard_bus_kinds(...))),
                                     stores wizardInstallInfo, fires the existing
                                     detached installer. 409 if control.mode != STOP.
GET  /api/wizard/installstatus  → { percent, status, output }; poll @250ms:
                                     142 → reboot modal · >100 → restart-redirect ·
                                     else → progress bar
```

**Backend contract:** thin JSON endpoints that delegate to the *existing*
store/discovery/finish/status functions (the probe-config precedent). The
existing `/wizard/*` handlers return HTML macros; these add JSON siblings that
call the same underlying logic and return data. `finish`/`installstatus` are not
pure delegators (they trigger `os.system`), but they reuse the existing
finish/status code rather than reimplementing it. The endpoints live under the
`api` blueprint (`/api/wizard/*`), consistent with the rest of the React app's
backend surface; the plan places the handler module.

## Components

- **`WizardShell`** — stepper + routing, the client working-state object, the
  draft-persistence flush, and the Finish→install-poll flow. Forced when
  `settings.globals.first_time_setup`. Guards Finish on `control.mode === STOP`
  (else a "can't run while active" modal, matching Flask's `runningModal`).
- **`ModuleCard`** (shared spine) — props:
  `{ section, moduleData, moduleSettings, configSource: "none" | "settings-by-module",
     onFieldChange, onModuleChange }`. Renders module `<select>` + image /
  friendly_name / description / notes badge + the settings-dependency table +
  (display) the config table.
- **Field-widget registry** — keyed on `settings_dependencies[key].type`:
  `undefined → <select>`, `i2c_bus_num → I2cBusPicker + Discover`,
  `usb_serial_device → UsbSerialPicker + Discover`. Honors per-field `hidden`
  (data-driven, callout #8). I2C kind/num pairing is an **explicit
  `kindFieldName` prop**, not Flask's `_num`→`_kind` string-replace (callout #10).
- **`DiscoveryPanel`** — `POST /api/wizard/scan` → renders `{groups, error}`; "no
  results" → friendly error string, never an empty table.
- **`ConfigOptionField`** — renders display's `config` bag (`option_type`
  `"list"`/`"string"`, `list_values`/`list_labels`). **Same shape as probe
  device config** (inventory §7) — build it generic so probes reuse it, not a
  display-only widget. Values arrive/leave as strings; server `_convert_value()`
  coerces on save (unchanged).
- **`InstallProgress`** — polls `installstatus` @250ms; `142 → reboot modal`
  (choice of `/admin/reboot` vs `/admin/restart`), `>100 → restart-redirect`,
  else progress bar.
- **Display step:** real (`ModuleCard section="display" configSource="settings-by-module"`).
  **grillplatform / probes / distance steps:** navigable placeholders (a clear
  "configured in a later release" panel) that don't break stepper nav or Finish.

## Manifest bug fix (separate commit, Flask-side)

`wizard/wizard_manifest.json` marks **two** display modules `"default": true`
(`ili9341b`, `ili9488b`) — the only section with this bug (audited: all others
have exactly one default). Effect (callout #3): the dropdown/identity resolves
to `ili9341b` (`profile_selected[0]`) while the pre-populated config comes from
`ili9488b` (last dict-iterated) — they disagree.

**Fix:** remove `"default": true` from `ili9488b`, leaving `ili9341b` the sole
default. Rationale: `ili9341b` is what the dropdown already shows selected, so
this makes the pre-populated config consistent with the shown selection with the
least surprise. **Separate commit**, ahead of the React display step.

**Risk:** a wizard characterization test in `tests/web/` may pin the current
(buggy) config-population behavior; if so, the fix flips that pin — update the
pinned expectation in the same commit and note it (this is a real behavior fix,
per repo convention on characterization-pinned bug fixes).

## Display-specific handling (the config round-trip)

Per inventory §4: on the display step, `moduleSettings.config` for module `M` is
`state.display_config[M] ?? {}` (client-side lookup into the settings-derived
dict from `/api/wizard/state`, **default `{}`** — this is the KeyError fix). On
Finish, the assembled payload carries `display: { module, config: {…} }`;
`selected` and `modules.display` (filename) are set server-side by the existing
finish logic (`wizard.py:201-203`) — the React port does NOT reimplement that.

## Shared-vs-bespoke contract (for later sections)

Reusable now, extended later (inventory §7): `ModuleCard`, the field-widget
registry, `DiscoveryPanel`, `ConfigOptionField`, `InstallProgress`, the Finish
flow, and the `/api/wizard/*` contract are **section-agnostic**. Later sections
add only their bespoke delta: probes → port/virtual-port graph + multi-device
list (not single `<select>`); grillplatform → pin/PWM `<select>` sets (still the
generic field renderer, `hidden`/options are data-driven) + it owns the
`device_distance_*`/`device_display_*` wiring fields; distance → borrows most
wiring from grillplatform. The `configSource` prop is the one real per-section
branch already accounted for.

## Testing

- **RTL:** `ModuleCard` (module-change re-render from client state, `hidden`
  honored, KeyError-safe `{}` default); each widget (select / I2cBusPicker /
  UsbSerialPicker + Discover); `DiscoveryPanel` (groups render, no-results
  error); `ConfigOptionField` (list/string); `WizardShell` (step nav, draft
  flush on transition, Finish gated on `control.mode !== STOP`);
  `InstallProgress` (142 / >100 / progress branches).
- **Coverage:** ≥75% per-file enforced; shared primitives aim high.
- **E2e:** walk to the display step, pick a module, edit a config field, assert
  the assembled Finish payload (mock the installer trigger); leave-as-found.
- **Backend:** the new JSON endpoints get characterization tests against the
  existing web-test harness (state shape, scan delegation, finish gating on
  non-STOP → 409, installstatus passthrough).

## Non-goals

- grillplatform / probes / distance **step content** (placeholders only this
  slice); grillplatform pin/PWM widgets; probe port/virtual-port UI.
- Any change to the real installer (`wizard.py`), `select_grillplat_module`, or
  `_convert_value`.
- Modeling `probe_map` / probe_settings dynamic content.
- The `settings["platform"]` **scalar** tab (tracked separately as PlatformTab in
  `.superpowers/sdd/react-migration-backlog.md`).

## Sequencing

This slice ships the shell + shared spine + display. Then, reusing the same
components: grillplatform, probes (folds in the probeconfig inventory), distance.
The extras-ratchet (schema `extra="forbid"`) is an orthogonal parallel workstream
(`.superpowers/sdd/extras-ratchet-progress.md`).
